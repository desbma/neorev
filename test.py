#!/usr/bin/env python3
"""Tests for neorev — interactive diff review tool."""

import contextlib
import fcntl
import importlib.machinery
import io
import os
import select
import struct
import tempfile
import termios
import tty
import unittest
from pathlib import Path
from unittest.mock import patch

# neorev is a script without .py extension; import it as a module.
NEOREV_PATH = str(Path(__file__).resolve().parent / "neorev")
neorev = importlib.machinery.SourceFileLoader("neorev", NEOREV_PATH).load_module()

TERM_WIDTH = 80
TERM_HEIGHT = 24
TERM_PIXEL_SIZE = 0  # pixel dimensions unused by tests
WINSIZE_FORMAT = "HHHH"
READ_BUFFER_SIZE = 8192
SELECT_TIMEOUT = 0.1
TINY_WIDTH = 5
NARROW_FOOTER_WIDTH = 15
WIDE_FOOTER_WIDTH = 120
NARROW_PROGRESS_WIDTH = 40
LONG_BODY_LINE_COUNT = 20
MANY_HUNKS_COUNT = 100
OVERFLOW_HUNK_INDEX = 50
OVERFLOWING_LINE_COUNT = 100
OUT_OF_BOUNDS_OFFSET = 9999
MIDDLE_SCROLL_OFFSET = 5
BYTE_BOUNDARY_HUNK_COUNT = 8
OVER_BYTE_BOUNDARY_HUNK_COUNT = 9
MULTI_FILE_HUNK_COUNT = 6
MULTI_FILE_COUNT = 3
HUNKS_PER_FILE = 2
LONG_COMMENT_LENGTH = 80
SCROLL_HALF_PAGE = max(
    1,
    (TERM_HEIGHT - neorev.CHROME_ROWS - neorev.SCROLL_INDICATOR_ROWS) // 2,
)

ESC_ARROW_UP = b"\x1b[A"
ESC_ARROW_DOWN = b"\x1b[B"
KEY_CTRL_C = b"\x03"
SIGWINCH_BYTE = b"\x1c"  # signal number written by set_wakeup_fd
RESIZE_WIDTH_DELTA = 10


BINARY_DIFF = """\
diff --git a/image.png b/image.png
Binary files a/image.png and b/image.png differ
"""

NEW_FILE_DIFF = """\
diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,3 @@
+def hello():
+    pass
"""

DELETE_FILE_DIFF = """\
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def old():
-    pass
"""

NO_NEWLINE_DIFF = """\
diff --git a/f.py b/f.py
--- a/f.py
+++ b/f.py
@@ -1 +1 @@
-old
+new
\\ No newline at end of file
"""

CONTEXT_LABEL_DIFF = """\
diff --git a/f.py b/f.py
--- a/f.py
+++ b/f.py
@@ -10,3 +10,4 @@ def foo():
     pass
+    return 0
"""

MULTI_FILE_MULTI_HUNK_DIFF = """\
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,2 +1,3 @@
 x = 1
+y = 2
@@ -10,2 +11,3 @@
 z = 3
+w = 4
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -1,2 +1,3 @@
 a = 1
+b = 2
@@ -20,2 +21,3 @@
 c = 3
+d = 4
diff --git a/c.py b/c.py
--- a/c.py
+++ b/c.py
@@ -1,2 +1,3 @@
 e = 1
+f = 2
@@ -30,2 +31,3 @@
 g = 3
+h = 4
"""

SIMPLE_DIFF = """\
diff --git a/hello.py b/hello.py
--- a/hello.py
+++ b/hello.py
@@ -1,3 +1,4 @@
 import sys
+import os

 def main():
"""

TWO_HUNK_DIFF = """\
diff --git a/hello.py b/hello.py
--- a/hello.py
+++ b/hello.py
@@ -1,3 +1,4 @@
 import sys
+import os

 def main():
@@ -10,3 +11,4 @@
     pass
+    return 0

"""

TWO_FILE_DIFF = """\
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,2 +1,3 @@
 x = 1
+y = 2
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -1,2 +1,3 @@
 a = 1
+b = 2
"""


def make_hunk(
    file_path: str = "test.py",
    start_line: int = 1,
    body: str = "+added line",
    status: neorev.ReviewStatus | None = None,
    comment: str = "",
) -> neorev.Hunk:
    """Create a Hunk with sensible defaults for testing."""
    range_line = f"@@ -1,3 +{start_line},4 @@"
    return neorev.Hunk(
        file_header=f"diff --git a/{file_path} b/{file_path}",
        range_line=range_line,
        body=body,
        raw=f"diff --git a/{file_path} b/{file_path}\n{range_line}\n{body}",
        file_path=file_path,
        start_line=start_line,
        comment=comment,
        status=status,
    )


class FakeTTY:
    """A pseudo-terminal pair for testing Terminal at the fd level."""

    def __init__(self) -> None:
        """Open a pty pair and configure the slave side."""
        self.master_fd, self.slave_fd = os.openpty()
        # Set a known terminal size.
        winsize = struct.pack(
            WINSIZE_FORMAT, TERM_HEIGHT, TERM_WIDTH, TERM_PIXEL_SIZE, TERM_PIXEL_SIZE
        )
        fcntl.ioctl(self.slave_fd, termios.TIOCSWINSZ, winsize)

    def close(self) -> None:
        """Close both ends of the pty (tolerates already-closed fds)."""
        for fd in (self.master_fd, self.slave_fd):
            with contextlib.suppress(OSError):
                os.close(fd)

    def inject_keys(self, data: bytes) -> None:
        """Write bytes into the master side so the slave reads them as input."""
        os.write(self.master_fd, data)

    def read_output(self, size: int = READ_BUFFER_SIZE) -> bytes:
        """Read what the slave side wrote (non-blocking, best-effort)."""
        ready, _, _ = select.select([self.master_fd], [], [], SELECT_TIMEOUT)
        if ready:
            return os.read(self.master_fd, size)
        return b""

    def make_terminal(self) -> neorev.Terminal:
        """Build a Terminal instance backed by this pty's slave fd."""
        with patch("os.open", return_value=self.slave_fd):
            term = neorev.Terminal()
        # Override width/height to known values in case the ioctl didn't stick.
        term.width = TERM_WIDTH
        term.height = TERM_HEIGHT
        return term


class TestParseDiff(unittest.TestCase):
    """Tests for parse_diff and related parsing functions."""

    def test_single_hunk(self) -> None:
        """Parse a simple one-hunk diff."""
        hunks = neorev.parse_diff(SIMPLE_DIFF)
        self.assertEqual(len(hunks), 1)
        hunk = hunks[0]
        self.assertEqual(hunk.file_path, "hello.py")
        self.assertEqual(hunk.start_line, 1)
        self.assertIn("+import os", hunk.body)

    def test_two_hunks_same_file(self) -> None:
        """Parse a diff with two hunks in the same file."""
        hunks = neorev.parse_diff(TWO_HUNK_DIFF)
        self.assertEqual(len(hunks), 2)
        for hunk in hunks:
            self.assertEqual(hunk.file_path, "hello.py")
        self.assertEqual(hunks[0].start_line, 1)
        self.assertEqual(hunks[1].start_line, 11)

    def test_two_files(self) -> None:
        """Parse a diff spanning two files."""
        hunks = neorev.parse_diff(TWO_FILE_DIFF)
        self.assertEqual(len(hunks), 2)
        self.assertEqual(hunks[0].file_path, "a.py")
        self.assertEqual(hunks[1].file_path, "b.py")

    def test_empty_diff(self) -> None:
        """Parsing an empty string produces no hunks."""
        self.assertEqual(neorev.parse_diff(""), [])

    def test_no_hunks(self) -> None:
        """A diff header with no @@ lines yields no hunks."""
        diff = "diff --git a/f b/f\n--- a/f\n+++ b/f\n"
        self.assertEqual(neorev.parse_diff(diff), [])

    def test_short_location(self) -> None:
        """Hunk.short_location returns 'file:line'."""
        hunk = neorev.parse_diff(SIMPLE_DIFF)[0]
        self.assertEqual(hunk.short_location, "hello.py:1")

    def test_short_location_no_file(self) -> None:
        """short_location falls back to range_line when file_path is empty."""
        hunk = make_hunk(file_path="")
        hunk.file_path = ""
        self.assertEqual(hunk.short_location, hunk.range_line.strip())

    def test_hunk_raw_includes_header(self) -> None:
        """The raw field should include the file header and range line."""
        hunk = neorev.parse_diff(SIMPLE_DIFF)[0]
        self.assertIn("diff --git", hunk.raw)
        self.assertIn("@@", hunk.raw)

    def test_file_path_strips_b_prefix(self) -> None:
        """The b/ prefix is stripped from the +++ line."""
        diff = (
            "diff --git a/src/x.py b/src/x.py\n"
            "--- a/src/x.py\n+++ b/src/x.py\n@@ -1 +1,2 @@\n+line\n"
        )
        hunks = neorev.parse_diff(diff)
        self.assertEqual(hunks[0].file_path, "src/x.py")

    def test_binary_file_diff(self) -> None:
        """A binary diff with no @@ lines produces no hunks."""
        self.assertEqual(neorev.parse_diff(BINARY_DIFF), [])

    def test_new_file_mode_diff(self) -> None:
        """A new-file diff parses the file_path and hunk correctly."""
        hunks = neorev.parse_diff(NEW_FILE_DIFF)
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0].file_path, "new.py")
        self.assertIn("+def hello():", hunks[0].body)

    def test_delete_file_diff(self) -> None:
        """A deleted-file diff with +++ /dev/null uses /dev/null as file_path."""
        hunks = neorev.parse_diff(DELETE_FILE_DIFF)
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0].file_path, "/dev/null")

    def test_multiple_hunks_across_multiple_files(self) -> None:
        """Parse 3 files with 2 hunks each into 6 hunks."""
        hunks = neorev.parse_diff(MULTI_FILE_MULTI_HUNK_DIFF)
        self.assertEqual(len(hunks), MULTI_FILE_HUNK_COUNT)
        files = [h.file_path for h in hunks]
        for name in ("a.py", "b.py", "c.py"):
            self.assertEqual(files.count(name), HUNKS_PER_FILE)

    def test_hunk_no_plus_in_range(self) -> None:
        """A deletion-only range @@ -1,2 +0,0 @@ yields start_line 0."""
        hunks = neorev.parse_diff(DELETE_FILE_DIFF)
        self.assertEqual(hunks[0].start_line, 0)

    def test_no_newline_marker_in_body(self) -> None:
        """The 'No newline at end of file' marker is kept in the hunk body."""
        hunks = neorev.parse_diff(NO_NEWLINE_DIFF)
        self.assertEqual(len(hunks), 1)
        self.assertIn("No newline at end of file", hunks[0].body)

    def test_range_line_with_context_label(self) -> None:
        """A range line with a context label still parses start_line correctly."""
        hunks = neorev.parse_diff(CONTEXT_LABEL_DIFF)
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0].start_line, 10)

    def test_file_path_dev_null_not_stripped(self) -> None:
        """+++ /dev/null keeps the leading slash (not stripped as b/ prefix)."""
        hunks = neorev.parse_diff(DELETE_FILE_DIFF)
        self.assertTrue(hunks[0].file_path.startswith("/"))


class TestBitmap(unittest.TestCase):
    """Tests for encode_approved_bitmap / decode_approved_bitmap round-trip."""

    def test_round_trip(self) -> None:
        """Encoding then decoding recovers the original approval states."""
        hunks = [
            make_hunk(status="approved"),
            make_hunk(),
            make_hunk(status="approved"),
        ]
        encoded = neorev.encode_approved_bitmap(hunks)
        decoded = neorev.decode_approved_bitmap(encoded, len(hunks))
        self.assertEqual(decoded, [True, False, True])

    def test_all_approved(self) -> None:
        """All-approved bitmap round-trips correctly."""
        hunks = [make_hunk(status="approved") for _ in range(10)]
        encoded = neorev.encode_approved_bitmap(hunks)
        decoded = neorev.decode_approved_bitmap(encoded, 10)
        self.assertTrue(all(decoded))

    def test_none_approved(self) -> None:
        """No-approval bitmap round-trips correctly."""
        hunks = [make_hunk() for _ in range(5)]
        encoded = neorev.encode_approved_bitmap(hunks)
        decoded = neorev.decode_approved_bitmap(encoded, 5)
        self.assertFalse(any(decoded))

    def test_invalid_base64(self) -> None:
        """Invalid base64 returns empty list."""
        self.assertEqual(neorev.decode_approved_bitmap("!!!bad", 3), [])

    def test_length_mismatch(self) -> None:
        """Mismatched hunk count returns empty list."""
        hunks = [make_hunk(status="approved")]
        encoded = neorev.encode_approved_bitmap(hunks)
        self.assertEqual(neorev.decode_approved_bitmap(encoded, 99), [])

    def test_single_hunk_approved(self) -> None:
        """Edge case: single approved hunk."""
        hunks = [make_hunk(status="approved")]
        encoded = neorev.encode_approved_bitmap(hunks)
        self.assertEqual(neorev.decode_approved_bitmap(encoded, 1), [True])

    def test_exactly_8_hunks(self) -> None:
        """8 hunks (exactly 1 byte boundary) round-trip correctly."""
        statuses = [
            "approved",
            None,
            "approved",
            None,
            None,
            "approved",
            "approved",
            None,
        ]
        hunks = [make_hunk(status=s) for s in statuses]
        encoded = neorev.encode_approved_bitmap(hunks)
        decoded = neorev.decode_approved_bitmap(encoded, BYTE_BOUNDARY_HUNK_COUNT)
        expected = [s == "approved" for s in statuses]
        self.assertEqual(decoded, expected)

    def test_9_hunks(self) -> None:
        """9 hunks (2 bytes) with mixed approvals round-trip correctly."""
        statuses = [
            "approved",
            None,
            "approved",
            None,
            None,
            "approved",
            "approved",
            None,
            "approved",
        ]
        hunks = [make_hunk(status=s) for s in statuses]
        encoded = neorev.encode_approved_bitmap(hunks)
        decoded = neorev.decode_approved_bitmap(encoded, OVER_BYTE_BOUNDARY_HUNK_COUNT)
        expected = [s == "approved" for s in statuses]
        self.assertEqual(decoded, expected)

    def test_empty_hunks(self) -> None:
        """0 hunks encodes and decodes to empty list."""
        encoded = neorev.encode_approved_bitmap([])
        self.assertEqual(neorev.decode_approved_bitmap(encoded, 0), [])

    def test_decode_truncated_data(self) -> None:
        """Valid base64 with too few bytes for num_hunks returns empty list."""
        hunks = [make_hunk(status="approved")]
        encoded = neorev.encode_approved_bitmap(hunks)
        self.assertEqual(neorev.decode_approved_bitmap(encoded, 16), [])


class TestFormatOutput(unittest.TestCase):
    """Tests for format_output and friends."""

    def test_all_approved(self) -> None:
        """All approved hunks produce a short 'all clear' output."""
        hunks = [make_hunk(status="approved"), make_hunk(status="approved")]
        output = neorev.format_output(hunks, [])
        self.assertIn("all clear", output)
        self.assertIn("neorev:", output)

    def test_flag_output(self) -> None:
        """A flagged hunk appears as CHANGE REQUESTED in the output."""
        hunks = [make_hunk(status="flag", comment="fix this")]
        output = neorev.format_output(hunks, [])
        self.assertIn("CHANGE REQUESTED", output)
        self.assertIn("fix this", output)

    def test_question_output(self) -> None:
        """A questioned hunk appears as QUESTION in the output."""
        hunks = [make_hunk(status="question", comment="why?")]
        output = neorev.format_output(hunks, [])
        self.assertIn("QUESTION", output)
        self.assertIn("why?", output)

    def test_global_notes_in_output(self) -> None:
        """Global notes appear in the output."""
        hunks = [make_hunk(status="approved")]
        notes = [neorev.GlobalNote(kind="flag", text="add tests")]
        output = neorev.format_output(hunks, notes)
        self.assertIn("(global)", output)
        self.assertIn("add tests", output)

    def test_long_hunk_body_trimmed(self) -> None:
        """Hunk bodies exceeding HUNK_BODY_MAX_LINES are trimmed."""
        long_body = "\n".join(f"+line {i}" for i in range(LONG_BODY_LINE_COUNT))
        hunks = [make_hunk(body=long_body, status="flag", comment="too long")]
        output = neorev.format_output(hunks, [])
        self.assertIn("# ...", output)

    def test_bitmap_present_in_output(self) -> None:
        """Output always contains a neorev: bitmap line."""
        hunks = [make_hunk(status="flag", comment="x")]
        output = neorev.format_output(hunks, [])
        self.assertIn("# neorev:", output)

    def test_no_status_hunks(self) -> None:
        """Hunks with no status and no actionable items get 'all clear'."""
        hunks = [make_hunk(), make_hunk()]
        output = neorev.format_output(hunks, [])
        self.assertIn("all clear", output)
        self.assertIn("0/2 hunks approved", output)
        self.assertIn("# neorev:", output)

    def test_mixed_statuses_summary(self) -> None:
        """Mixed statuses produce correct summary counts in header."""
        hunks = [
            make_hunk(status="approved"),
            make_hunk(status="flag", comment="fix"),
            make_hunk(status="question", comment="why"),
            make_hunk(),
        ]
        output = neorev.format_output(hunks, [])
        self.assertIn("1 approved", output)
        self.assertIn("1 questions", output)
        self.assertIn("1 changes requested", output)

    def test_global_note_question_label(self) -> None:
        """A global question note section header uses QUESTION label."""
        hunks = [make_hunk(status="approved")]
        notes = [neorev.GlobalNote(kind="question", text="why this approach?")]
        output = neorev.format_output(hunks, notes)
        self.assertIn("[QUESTION] (global)", output)
        self.assertNotIn("[CHANGE REQUESTED] (global)", output)

    def test_multiline_comment_quoted(self) -> None:
        """Each line of a multi-line comment gets a > prefix."""
        hunks = [make_hunk(status="flag", comment="line one\nline two\nline three")]
        output = neorev.format_output(hunks, [])
        self.assertIn("> line one\n", output)
        self.assertIn("> line two\n", output)
        self.assertIn("> line three\n", output)

    def test_body_exactly_max_lines_not_trimmed(self) -> None:
        """A body with exactly HUNK_BODY_MAX_LINES lines is not trimmed."""
        body = "\n".join(f"+line {i}" for i in range(neorev.HUNK_BODY_MAX_LINES))
        hunks = [make_hunk(body=body, status="flag", comment="ok")]
        output = neorev.format_output(hunks, [])
        self.assertNotIn("# ...", output)

    def test_body_one_over_max_lines_trimmed(self) -> None:
        """A body with HUNK_BODY_MAX_LINES + 1 lines is trimmed."""
        body = "\n".join(f"+line {i}" for i in range(neorev.HUNK_BODY_MAX_LINES + 1))
        hunks = [make_hunk(body=body, status="flag", comment="too long")]
        output = neorev.format_output(hunks, [])
        self.assertIn("# ...", output)


class TestLoadPreviousReview(unittest.TestCase):
    """Tests for load_previous_review, extract_comment_lines, apply_previous_review."""

    def test_nonexistent_file(self) -> None:
        """Loading a missing file returns empty results."""
        annotations, notes, bitmap = neorev.load_previous_review("/no/such/file")
        self.assertEqual(annotations, {})
        self.assertEqual(notes, [])
        self.assertEqual(bitmap, "")

    def test_round_trip_through_file(self) -> None:
        """format_output → load_previous_review recovers annotations."""
        hunks = [
            make_hunk(file_path="a.py", status="flag", comment="fix this"),
            make_hunk(file_path="b.py", status="approved"),
        ]
        output = neorev.format_output(hunks, [])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(output)
            path = f.name

        try:
            annotations, _, _ = neorev.load_previous_review(path)
            self.assertIn(("a.py", hunks[0].range_line), annotations)
            self.assertEqual(annotations[("a.py", hunks[0].range_line)][0], "flag")
        finally:
            os.unlink(path)

    def test_extract_comment_lines(self) -> None:
        """extract_comment_lines pulls > -prefixed lines."""
        section = "header\n> line one\n> line two\nother"
        self.assertEqual(neorev.extract_comment_lines(section), "line one\nline two")

    def test_extract_empty_quote_lines(self) -> None:
        """Bare > lines become empty lines in the comment."""
        section = "> first\n>\n> third"
        self.assertEqual(neorev.extract_comment_lines(section), "first\n\nthird")

    def test_apply_previous_review(self) -> None:
        """apply_previous_review sets status/comment on matching hunks."""
        hunks = [make_hunk(file_path="x.py")]
        annotations = {("x.py", hunks[0].range_line): ("question", "why?")}
        matched = neorev.apply_previous_review(hunks, annotations)
        self.assertEqual(matched, 1)
        self.assertEqual(hunks[0].status, "question")
        self.assertEqual(hunks[0].comment, "why?")

    def test_apply_no_match(self) -> None:
        """Unmatched annotations don't alter hunks."""
        hunks = [make_hunk(file_path="x.py")]
        annotations = {("other.py", "@@ -1 +1 @@"): ("flag", "n/a")}
        matched = neorev.apply_previous_review(hunks, annotations)
        self.assertEqual(matched, 0)
        self.assertIsNone(hunks[0].status)

    def test_global_notes_round_trip(self) -> None:
        """Global notes survive format_output → load_previous_review."""
        hunks = [make_hunk(status="approved")]
        notes = [neorev.GlobalNote(kind="question", text="overall design?")]
        output = neorev.format_output(hunks, notes)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(output)
            path = f.name

        try:
            _, loaded_notes, _ = neorev.load_previous_review(path)
            self.assertEqual(len(loaded_notes), 1)
            self.assertEqual(loaded_notes[0].kind, "question")
            self.assertIn("overall design", loaded_notes[0].text)
        finally:
            os.unlink(path)

    def test_load_empty_file(self) -> None:
        """An existing but empty file returns empty results."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("   \n\n")
            path = f.name

        try:
            annotations, notes, bitmap = neorev.load_previous_review(path)
            self.assertEqual(annotations, {})
            self.assertEqual(notes, [])
            self.assertEqual(bitmap, "")
        finally:
            os.unlink(path)

    def test_multiline_comment_round_trip(self) -> None:
        """A multi-line comment survives format_output → load_previous_review."""
        hunks = [
            make_hunk(
                file_path="m.py",
                status="flag",
                comment="first line\nsecond line\nthird line",
            )
        ]
        output = neorev.format_output(hunks, [])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(output)
            path = f.name

        try:
            annotations, _, _ = neorev.load_previous_review(path)
            _, comment = annotations[("m.py", hunks[0].range_line)]
            self.assertIn("first line", comment)
            self.assertIn("second line", comment)
            self.assertIn("third line", comment)
        finally:
            os.unlink(path)

    def test_multiple_global_notes_round_trip(self) -> None:
        """Multiple global notes of different kinds survive round-trip."""
        hunks = [make_hunk(status="approved")]
        notes = [
            neorev.GlobalNote(kind="flag", text="add tests"),
            neorev.GlobalNote(kind="question", text="why this design?"),
        ]
        output = neorev.format_output(hunks, notes)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(output)
            path = f.name

        try:
            _, loaded_notes, _ = neorev.load_previous_review(path)
            self.assertEqual(len(loaded_notes), 2)
            self.assertEqual(loaded_notes[0].kind, "flag")
            self.assertEqual(loaded_notes[1].kind, "question")
        finally:
            os.unlink(path)

    def test_section_without_range_line_skipped(self) -> None:
        """A section header with no ```diff block is skipped gracefully."""
        content = "## [CHANGE REQUESTED] broken.py\n> some comment\n# neorev:AQ==\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content)
            path = f.name

        try:
            annotations, _, bitmap = neorev.load_previous_review(path)
            self.assertEqual(annotations, {})
            self.assertNotEqual(bitmap, "")
        finally:
            os.unlink(path)

    def test_apply_previous_review_multiple_matches(self) -> None:
        """Multiple hunks matching annotations all get annotated."""
        hunks = [
            make_hunk(file_path="a.py", start_line=1),
            make_hunk(file_path="b.py", start_line=5),
        ]
        annotations = {
            ("a.py", hunks[0].range_line): ("flag", "fix a"),
            ("b.py", hunks[1].range_line): ("question", "why b"),
        }
        matched = neorev.apply_previous_review(hunks, annotations)
        self.assertEqual(matched, 2)
        self.assertEqual(hunks[0].status, "flag")
        self.assertEqual(hunks[1].status, "question")


class TestNavigation(unittest.TestCase):
    """Tests for navigation, approval, and hunk-finding functions."""

    def setUp(self) -> None:
        """Create a three-hunk review state."""
        self.hunks = [make_hunk() for _ in range(3)]
        self.state = neorev.ReviewState(hunks=self.hunks, global_notes=[])

    def test_navigate_down(self) -> None:
        """'j' moves to the next hunk."""
        self.assertTrue(neorev.handle_navigation("j", self.state))
        self.assertEqual(self.state.current_index, 1)

    def test_navigate_up(self) -> None:
        """'k' moves to the previous hunk."""
        self.state.current_index = 2
        self.assertTrue(neorev.handle_navigation("k", self.state))
        self.assertEqual(self.state.current_index, 1)

    def test_navigate_down_at_end(self) -> None:
        """'j' at the last hunk does nothing."""
        self.state.current_index = 2
        self.assertFalse(neorev.handle_navigation("j", self.state))
        self.assertEqual(self.state.current_index, 2)

    def test_navigate_up_at_start(self) -> None:
        """'k' at the first hunk does nothing."""
        self.assertFalse(neorev.handle_navigation("k", self.state))
        self.assertEqual(self.state.current_index, 0)

    def test_arrow_keys(self) -> None:
        """Arrow key names work like j/k."""
        with self.subTest(key="down"):
            self.state.current_index = 0
            neorev.handle_navigation("down", self.state)
            self.assertEqual(self.state.current_index, 1)

        with self.subTest(key="up"):
            neorev.handle_navigation("up", self.state)
            self.assertEqual(self.state.current_index, 0)

    def test_approve_toggle(self) -> None:
        """Approving then re-approving toggles the status."""
        neorev.handle_approve(self.state)
        self.assertEqual(self.hunks[0].status, "approved")
        self.state.current_index = 0
        neorev.handle_approve(self.state)
        self.assertIsNone(self.hunks[0].status)

    def test_approve_clears_comment(self) -> None:
        """Approving a hunk clears any existing comment."""
        self.hunks[0].comment = "old comment"
        self.hunks[0].status = "flag"
        neorev.handle_approve(self.state)
        self.assertEqual(self.hunks[0].comment, "")

    def test_approve_advances_to_next_unhandled(self) -> None:
        """After approval, cursor moves to the next unhandled hunk."""
        self.hunks[1].status = "approved"
        neorev.handle_approve(self.state)
        self.assertEqual(self.state.current_index, 2)

    def test_approve_file(self) -> None:
        """Approve-file approves all hunks with the same file_path."""
        for h in self.hunks:
            h.file_path = "same.py"
        neorev.handle_approve_file(self.state)
        for h in self.hunks:
            self.assertEqual(h.status, "approved")

    def test_approve_file_skips_other_files(self) -> None:
        """Approve-file only touches hunks matching the current file."""
        self.hunks[0].file_path = "a.py"
        self.hunks[1].file_path = "b.py"
        self.hunks[2].file_path = "a.py"
        neorev.handle_approve_file(self.state)
        self.assertEqual(self.hunks[0].status, "approved")
        self.assertIsNone(self.hunks[1].status)
        self.assertEqual(self.hunks[2].status, "approved")

    def test_find_next_unhandled_wraps(self) -> None:
        """find_next_unhandled_hunk wraps around the list."""
        self.hunks[1].status = "approved"
        self.hunks[2].status = "approved"
        result = neorev.find_next_unhandled_hunk(self.hunks, 2)
        self.assertEqual(result, 0)

    def test_find_next_unhandled_all_handled(self) -> None:
        """When all hunks are handled, returns current index."""
        for h in self.hunks:
            h.status = "approved"
        result = neorev.find_next_unhandled_hunk(self.hunks, 1)
        self.assertEqual(result, 1)

    def test_find_initial_hunk_index(self) -> None:
        """find_initial_hunk_index returns the first unhandled hunk."""
        self.hunks[0].status = "approved"
        self.assertEqual(neorev.find_initial_hunk_index(self.hunks), 1)

    def test_find_initial_all_handled(self) -> None:
        """When all hunks are handled, returns 0."""
        for h in self.hunks:
            h.status = "approved"
        self.assertEqual(neorev.find_initial_hunk_index(self.hunks), 0)

    def test_navigate_single_hunk(self) -> None:
        """With a single hunk, both j and k return False."""
        state = neorev.ReviewState(hunks=[make_hunk()], global_notes=[])
        self.assertFalse(neorev.handle_navigation("j", state))
        self.assertFalse(neorev.handle_navigation("k", state))
        self.assertEqual(state.current_index, 0)

    def test_approve_already_flagged_hunk(self) -> None:
        """Approving a flagged hunk sets status to approved and clears comment."""
        self.hunks[0].status = "flag"
        self.hunks[0].comment = "fix this"
        neorev.handle_approve(self.state)
        self.assertEqual(self.hunks[0].status, "approved")
        self.assertEqual(self.hunks[0].comment, "")

    def test_approve_file_idempotent_on_approved(self) -> None:
        """Approve-file on already-approved hunks keeps them approved."""
        for h in self.hunks:
            h.file_path = "same.py"
            h.status = "approved"
        neorev.handle_approve_file(self.state)
        for h in self.hunks:
            self.assertEqual(h.status, "approved")

    def test_approve_file_advances_to_other_file(self) -> None:
        """After approve-file, cursor moves to next unhandled hunk in another file."""
        self.hunks[0].file_path = "a.py"
        self.hunks[1].file_path = "a.py"
        self.hunks[2].file_path = "b.py"
        neorev.handle_approve_file(self.state)
        self.assertEqual(self.state.current_index, 2)

    def test_find_next_unhandled_single_unhandled(self) -> None:
        """With one unhandled hunk, it is always found regardless of position."""
        self.hunks[0].status = "approved"
        self.hunks[1].status = "approved"
        for start in range(3):
            result = neorev.find_next_unhandled_hunk(self.hunks, start)
            self.assertEqual(result, 2)


class TestRenderingHelpers(unittest.TestCase):
    """Tests for ANSI text measurement, wrapping, and display-line building."""

    def test_visible_text_length_plain(self) -> None:
        """Plain ASCII text has visible length equal to byte count."""
        self.assertEqual(neorev.visible_len("hello"), 5)

    def test_visible_text_length_ansi(self) -> None:
        """ANSI escape sequences are excluded from visible length."""
        line = f"{neorev.GREEN}hello{neorev.RESET}"
        self.assertEqual(neorev.visible_len(line), 5)

    def test_visible_len_str(self) -> None:
        """visible_len works on str with ANSI codes."""
        text = f"{neorev.BOLD}hi{neorev.RESET}"
        self.assertEqual(neorev.visible_len(text), 2)

    def test_estimate_wrapped_rows_short(self) -> None:
        """A short line occupies one wrapped row."""
        self.assertEqual(len(neorev.wrap_ansi_line_to_rows(b"short", TERM_WIDTH)), 1)

    def test_estimate_wrapped_rows_long(self) -> None:
        """A line longer than term_width wraps to multiple rows."""
        long_line = b"x" * (TERM_WIDTH * 2)
        self.assertEqual(len(neorev.wrap_ansi_line_to_rows(long_line, TERM_WIDTH)), 2)

    def test_estimate_wrapped_rows_empty(self) -> None:
        """An empty line still occupies one display row."""
        self.assertEqual(len(neorev.wrap_ansi_line_to_rows(b"", TERM_WIDTH)), 1)

    def test_count_fitting_lines(self) -> None:
        """compute_visible_count reserves rows for scroll indicators."""
        visible, can_up, can_down = neorev.compute_visible_count(100, 10, 0)
        self.assertEqual(visible, 9)
        self.assertFalse(can_up)
        self.assertTrue(can_down)

    def test_count_fitting_lines_from_offset(self) -> None:
        """compute_visible_count shows both indicators when mid-scroll."""
        visible, can_up, can_down = neorev.compute_visible_count(100, 10, 5)
        self.assertEqual(visible, 8)
        self.assertTrue(can_up)
        self.assertTrue(can_down)

    def test_build_display_lines_strips_blanks(self) -> None:
        """Leading/trailing blank lines from delta output are stripped."""
        raw = b"\nline1\nline2\n"
        lines = neorev.build_display_lines(raw, TERM_WIDTH)
        self.assertEqual(lines[0], b"line1")
        self.assertEqual(lines[-1], b"line2")

    def test_build_display_lines_empty(self) -> None:
        """Empty input produces a single empty-bytes entry."""
        lines = neorev.build_display_lines(b"", TERM_WIDTH)
        self.assertEqual(lines, [b""])

    def test_wrap_ansi_line_short(self) -> None:
        """A line shorter than term_width is returned as-is."""
        line = b"hello"
        result = neorev.wrap_ansi_line_to_rows(line, TERM_WIDTH)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], b"hello")

    def test_wrap_ansi_line_exact(self) -> None:
        """A line exactly term_width long produces one row."""
        line = b"x" * TERM_WIDTH
        result = neorev.wrap_ansi_line_to_rows(line, TERM_WIDTH)
        self.assertEqual(len(result), 1)

    def test_wrap_ansi_line_overflow(self) -> None:
        """A line longer than term_width wraps into multiple rows."""
        line = b"x" * (TERM_WIDTH + TERM_WIDTH // 4)
        result = neorev.wrap_ansi_line_to_rows(line, TERM_WIDTH)
        self.assertEqual(len(result), 2)

    def test_update_active_sgr_reset_clears(self) -> None:
        """A reset sequence clears the active SGR list."""
        active: list[str] = [neorev.BOLD]
        neorev.update_active_sgr(f"{neorev.CSI}0m", active)
        self.assertEqual(active, [])

    def test_update_active_sgr_accumulates(self) -> None:
        """Non-reset SGR sequences accumulate."""
        active: list[str] = []
        neorev.update_active_sgr(neorev.BOLD, active)
        neorev.update_active_sgr(neorev.GREEN, active)
        self.assertEqual(len(active), 2)

    def test_visible_text_length_unicode(self) -> None:
        """Multi-byte UTF-8 characters count as single visible characters."""
        self.assertEqual(neorev.visible_len("héllo"), 5)

    def test_visible_len_no_ansi(self) -> None:
        """Plain string with no escapes returns len()."""
        self.assertEqual(neorev.visible_len("hello"), 5)

    def test_estimate_wrapped_rows_exactly_width(self) -> None:
        """A line exactly term_width visible chars occupies 1 row."""
        line = b"x" * TERM_WIDTH
        self.assertEqual(len(neorev.wrap_ansi_line_to_rows(line, TERM_WIDTH)), 1)

    def test_estimate_wrapped_rows_one_over(self) -> None:
        """A line of term_width + 1 visible chars occupies 2 rows."""
        line = b"x" * (TERM_WIDTH + 1)
        self.assertEqual(len(neorev.wrap_ansi_line_to_rows(line, TERM_WIDTH)), 2)

    def test_count_fitting_lines_zero_budget(self) -> None:
        """compute_visible_count enforces MIN_VISIBLE_ROWS."""
        visible, _, _ = neorev.compute_visible_count(100, 0, 0)
        self.assertEqual(visible, neorev.MIN_VISIBLE_ROWS)

    def test_count_fitting_lines_all_fit(self) -> None:
        """When at end, compute_visible_count can disable down indicator."""
        visible, can_up, can_down = neorev.compute_visible_count(20, 10, 15)
        self.assertEqual(visible, 5)
        self.assertTrue(can_up)
        self.assertFalse(can_down)

    def test_wrap_ansi_preserves_color_across_rows(self) -> None:
        """A colored line that wraps carries color into the second row."""
        colored_line = f"{neorev.GREEN}{'x' * (TERM_WIDTH + 10)}{neorev.RESET}".encode()
        result = neorev.wrap_ansi_line_to_rows(colored_line, TERM_WIDTH)
        self.assertGreater(len(result), 1)
        second_row = result[1].decode("utf-8", errors="replace")
        self.assertIn(neorev.GREEN, second_row)

    def test_wrap_ansi_line_term_width_1(self) -> None:
        """term_width <= 1 returns the line as-is (guard clause)."""
        line = b"hello"
        result = neorev.wrap_ansi_line_to_rows(line, 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], line)

    def test_build_display_lines_multiple_wraps(self) -> None:
        """Lines exceeding width produce more display lines than raw lines."""
        long_line = b"x" * (TERM_WIDTH * 2)
        raw = long_line + b"\n" + b"short"
        lines = neorev.build_display_lines(raw, TERM_WIDTH)
        self.assertGreater(len(lines), 2)


class TestViewport(unittest.TestCase):
    """Tests for compute_diff_viewport."""

    def test_no_scrolling_needed(self) -> None:
        """When content fits, no scroll indicators are shown."""
        line_rows = [1] * 5
        vp = neorev.compute_diff_viewport(len(line_rows), TERM_HEIGHT, 0)
        self.assertFalse(vp.can_scroll_up)
        self.assertFalse(vp.can_scroll_down)
        self.assertEqual(vp.scroll_offset, 0)

    def test_scrolling_needed(self) -> None:
        """When content exceeds terminal height, scrolling is enabled."""
        line_rows = [1] * OVERFLOWING_LINE_COUNT
        vp = neorev.compute_diff_viewport(len(line_rows), TERM_HEIGHT, 0)
        self.assertFalse(vp.can_scroll_up)
        self.assertTrue(vp.can_scroll_down)

    def test_scroll_offset_clamped(self) -> None:
        """Scroll offset is clamped to valid range."""
        line_rows = [1] * OVERFLOWING_LINE_COUNT
        vp = neorev.compute_diff_viewport(
            len(line_rows),
            TERM_HEIGHT,
            OUT_OF_BOUNDS_OFFSET,
        )
        self.assertLessEqual(vp.scroll_offset, len(line_rows))

    def test_scrolled_to_middle(self) -> None:
        """Scrolling to the middle enables both scroll indicators."""
        line_rows = [1] * OVERFLOWING_LINE_COUNT
        vp = neorev.compute_diff_viewport(
            len(line_rows),
            TERM_HEIGHT,
            MIDDLE_SCROLL_OFFSET,
        )
        self.assertTrue(vp.can_scroll_up)
        self.assertTrue(vp.can_scroll_down)

    def test_single_line(self) -> None:
        """A single line with a large terminal needs no scrolling."""
        line_rows = [1]
        vp = neorev.compute_diff_viewport(len(line_rows), TERM_HEIGHT, 0)
        self.assertFalse(vp.can_scroll_up)
        self.assertFalse(vp.can_scroll_down)
        self.assertEqual(vp.visible_line_count, 1)

    def test_exact_fit(self) -> None:
        """Content rows exactly filling available space needs no scrolling."""
        avail = TERM_HEIGHT - neorev.CHROME_ROWS
        line_rows = [1] * avail
        vp = neorev.compute_diff_viewport(len(line_rows), TERM_HEIGHT, 0)
        self.assertFalse(vp.can_scroll_up)
        self.assertFalse(vp.can_scroll_down)
        self.assertEqual(vp.visible_line_count, avail)

    def test_scroll_to_end(self) -> None:
        """Scrolling to a large offset clamps and disables scroll-down."""
        line_rows = [1] * OVERFLOWING_LINE_COUNT
        vp = neorev.compute_diff_viewport(
            len(line_rows),
            TERM_HEIGHT,
            OUT_OF_BOUNDS_OFFSET,
        )
        self.assertFalse(vp.can_scroll_down)
        self.assertTrue(vp.can_scroll_up)


class TestChrome(unittest.TestCase):
    """Tests for top bar, hunk markers, progress markers, and footer."""

    def test_top_bar_contains_index(self) -> None:
        """Top bar shows 'Hunk N/total'."""
        hunk = make_hunk()
        bar = neorev.build_top_bar(hunk, 0, 5, 0)
        self.assertIn("1", bar)
        self.assertIn("5", bar)

    def test_top_bar_status_labels(self) -> None:
        """Top bar renders status text for each status type."""
        for status, expected in [
            ("approved", "approved"),
            ("flag", "change requested"),
            ("question", "question"),
        ]:
            with self.subTest(status=status):
                hunk = make_hunk(status=status)
                bar = neorev.build_top_bar(hunk, 0, 1, 0)
                self.assertIn(expected, bar)

    def test_top_bar_comment_preview(self) -> None:
        """Top bar shows a truncated comment preview."""
        hunk = make_hunk(comment="a very important comment here")
        bar = neorev.build_top_bar(hunk, 0, 1, 0)
        self.assertIn("a very important comment here", bar)

    def test_top_bar_global_count(self) -> None:
        """Top bar shows global note count when present."""
        hunk = make_hunk()
        bar = neorev.build_top_bar(hunk, 0, 1, 3)
        self.assertIn("3 global", bar)

    def test_hunk_marker_styles(self) -> None:
        """Each status produces a distinct marker icon."""
        cases = [
            ("approved", "✓"),
            ("flag", "✗"),
            ("question", "?"),
            (None, "·"),
        ]
        for status, icon in cases:
            with self.subTest(status=status):
                hunk = make_hunk(status=status)
                marker = neorev.hunk_marker(hunk, is_current=False)
                self.assertIn(icon, marker)

    def test_current_marker_has_brackets(self) -> None:
        """The current hunk marker is wrapped in brackets."""
        hunk = make_hunk()
        marker = neorev.hunk_marker(hunk, is_current=True)
        self.assertIn("[", marker)
        self.assertIn("]", marker)

    def test_progress_markers_count(self) -> None:
        """Progress markers line contains all hunk markers when they fit."""
        hunks = [make_hunk() for _ in range(5)]
        line = neorev.build_progress_markers(hunks, 2, TERM_WIDTH)
        self.assertEqual(line.count("·"), 5)

    def test_progress_markers_overflow(self) -> None:
        """With many hunks, overflow arrows appear."""
        hunks = [make_hunk() for _ in range(MANY_HUNKS_COUNT)]
        line = neorev.build_progress_markers(
            hunks, OVERFLOW_HUNK_INDEX, NARROW_PROGRESS_WIDTH
        )
        self.assertIn("◀", line)
        self.assertIn("▶", line)

    def test_footer_contains_key_hints(self) -> None:
        """Footer line includes key hints."""
        footer = neorev.build_footer_line(WIDE_FOOTER_WIDTH)
        self.assertIn("j/k", footer)
        self.assertIn("quit", footer)

    def test_footer_truncates_narrow(self) -> None:
        """A very narrow terminal truncates the footer."""
        footer = neorev.build_footer_line(NARROW_FOOTER_WIDTH)
        # Should not contain all segments.
        self.assertNotIn("help", footer)

    def test_top_bar_unknown_status(self) -> None:
        """A hunk with an unknown status falls back to the dim dash."""
        hunk = make_hunk()
        hunk.__dict__["status"] = "bogus"
        bar = neorev.build_top_bar(hunk, 0, 1, 0)
        self.assertIn("—", bar)

    def test_top_bar_long_comment_truncated(self) -> None:
        """A comment longer than COMMENT_PREVIEW_MAX is truncated with ellipsis."""
        long_comment = "x" * LONG_COMMENT_LENGTH
        hunk = make_hunk(comment=long_comment)
        bar = neorev.build_top_bar(hunk, 0, 1, 0)
        self.assertIn("…", bar)
        self.assertNotIn("x" * LONG_COMMENT_LENGTH, bar)

    def test_progress_markers_single_hunk(self) -> None:
        """A single hunk produces one marker with no overflow arrows."""
        hunks = [make_hunk()]
        line = neorev.build_progress_markers(hunks, 0, TERM_WIDTH)
        self.assertNotIn("◀", line)
        self.assertNotIn("▶", line)

    def test_progress_markers_at_start(self) -> None:
        """At index 0 with many hunks, no left arrow but right arrow present."""
        hunks = [make_hunk() for _ in range(MANY_HUNKS_COUNT)]
        line = neorev.build_progress_markers(hunks, 0, NARROW_PROGRESS_WIDTH)
        self.assertNotIn("◀", line)
        self.assertIn("▶", line)

    def test_progress_markers_at_end(self) -> None:
        """At the last index with many hunks, left arrow but no right arrow."""
        hunks = [make_hunk() for _ in range(MANY_HUNKS_COUNT)]
        line = neorev.build_progress_markers(
            hunks,
            MANY_HUNKS_COUNT - 1,
            NARROW_PROGRESS_WIDTH,
        )
        self.assertIn("◀", line)
        self.assertNotIn("▶", line)

    def test_footer_exact_width(self) -> None:
        """A width that exactly fits all segments does not append ellipsis."""
        full_footer = neorev.build_footer_line(WIDE_FOOTER_WIDTH)
        visible = neorev.visible_len(full_footer)
        exact_footer = neorev.build_footer_line(visible)
        self.assertNotIn("…", exact_footer)


class TestCommentHelpers(unittest.TestCase):
    """Tests for write_comment_template and read_comment_file."""

    def test_write_comment_template(self) -> None:
        """Template contains the location and existing comment."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            jump = neorev.write_comment_template(f, "test.py:10", "existing")
            path = f.name

        try:
            with open(path) as f:
                content = f.read()
            self.assertIn("test.py:10", content)
            self.assertIn("existing", content)
            self.assertIsInstance(jump, int)
            self.assertGreater(jump, 0)
        finally:
            os.unlink(path)

    def test_read_comment_file_strips_hashes(self) -> None:
        """read_comment_file strips lines starting with #."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# header\nactual comment\n# footer\n")
            path = f.name

        try:
            comment = neorev.read_comment_file(path)
            self.assertEqual(comment, "actual comment")
        finally:
            os.unlink(path)

    def test_write_comment_template_no_existing(self) -> None:
        """Template with no existing comment still has location and blank line."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            jump = neorev.write_comment_template(f, "foo.py:5", "")
            path = f.name

        try:
            with open(path) as f:
                content = f.read()
            self.assertIn("foo.py:5", content)
            self.assertGreater(jump, 0)
        finally:
            os.unlink(path)

    def test_read_comment_file_all_comments(self) -> None:
        """A file with only # lines returns empty string."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# line one\n# line two\n")
            path = f.name

        try:
            comment = neorev.read_comment_file(path)
            self.assertEqual(comment, "")
        finally:
            os.unlink(path)

    def test_read_comment_file_preserves_inner_hashes(self) -> None:
        """Lines not starting with # are preserved even if they contain #."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# header\n  # indented hash\nplain\n")
            path = f.name

        try:
            comment = neorev.read_comment_file(path)
            self.assertIn("# indented hash", comment)
            self.assertIn("plain", comment)
        finally:
            os.unlink(path)


class TestTerminalKeys(unittest.TestCase):
    """Tests for Terminal.read_key using a real pseudo-terminal."""

    def setUp(self) -> None:
        """Create a fake TTY and a Terminal backed by it."""
        self.fake = FakeTTY()
        self.term = self.fake.make_terminal()

    def tearDown(self) -> None:
        """Restore terminal state and close the pty."""
        with contextlib.suppress(OSError):
            self.term.close()
        self.fake.close()

    def test_read_plain_key(self) -> None:
        """A single ASCII byte is returned as a string."""
        tty.setraw(self.fake.slave_fd)
        self.fake.inject_keys(b"j")
        key = self.term.read_key()
        self.assertEqual(key, "j")

    def test_read_arrow_up(self) -> None:
        """ESC [ A is normalised to 'up'."""
        tty.setraw(self.fake.slave_fd)
        self.fake.inject_keys(ESC_ARROW_UP)
        key = self.term.read_key()
        self.assertEqual(key, "up")

    def test_read_arrow_down(self) -> None:
        """ESC [ B is normalised to 'down'."""
        tty.setraw(self.fake.slave_fd)
        self.fake.inject_keys(ESC_ARROW_DOWN)
        key = self.term.read_key()
        self.assertEqual(key, "down")

    def test_read_ctrl_c(self) -> None:
        """Ctrl-C is returned as the raw byte."""
        tty.setraw(self.fake.slave_fd)
        self.fake.inject_keys(KEY_CTRL_C)
        key = self.term.read_key()
        self.assertEqual(key, neorev.Terminal.KEY_CTRL_C)


class TestTerminalRender(unittest.TestCase):
    """Tests for Terminal rendering methods using a pseudo-terminal."""

    def setUp(self) -> None:
        """Create a fake TTY and a Terminal backed by it."""
        self.fake = FakeTTY()
        self.term = self.fake.make_terminal()

    def tearDown(self) -> None:
        """Restore terminal state and close the pty."""
        with contextlib.suppress(OSError):
            self.term.close()
        self.fake.close()

    def test_write_str(self) -> None:
        """Terminal.write accepts strings."""
        self.term.write("hello")
        output = self.fake.read_output()
        self.assertIn(b"hello", output)

    def test_write_bytes(self) -> None:
        """Terminal.write accepts bytes."""
        self.term.write(b"world")
        output = self.fake.read_output()
        self.assertIn(b"world", output)

    def test_render_review_screen(self) -> None:
        """render_review_screen writes output containing hunk info."""
        hunks = neorev.parse_diff(SIMPLE_DIFF)
        delta_output = hunks[0].raw.encode()
        scroll = self.term.render_review_screen(hunks, 0, delta_output, [])
        output = self.fake.read_output()
        self.assertIsInstance(scroll, int)
        self.assertGreater(len(output), 0)

    def test_render_help_screen(self) -> None:
        """render_help_screen writes the help box."""
        self.term.render_help_screen()
        output = self.fake.read_output()
        self.assertIn(b"neorev", output)

    def test_render_global_notes_screen_empty(self) -> None:
        """Global notes screen with no notes shows 'No global notes'."""
        self.term.render_global_notes_screen([])
        output = self.fake.read_output()
        self.assertIn(b"No global notes", output)

    def test_render_global_notes_screen_with_notes(self) -> None:
        """Global notes screen lists existing notes."""
        notes = [neorev.GlobalNote(kind="flag", text="fix this")]
        self.term.render_global_notes_screen(notes)
        output = self.fake.read_output()
        self.assertIn(b"fix this", output)


class TestDispatchKey(unittest.TestCase):
    """Tests for Terminal.dispatch_key using a pseudo-terminal."""

    def setUp(self) -> None:
        """Create a fake TTY, Terminal, and a two-hunk state."""
        self.fake = FakeTTY()
        self.term = self.fake.make_terminal()
        self.hunks = [make_hunk(file_path="a.py"), make_hunk(file_path="b.py")]
        self.state = neorev.ReviewState(hunks=self.hunks, global_notes=[])
        self.redraw_count = 0

    def redraw(self) -> None:
        """Dummy redraw callback that counts invocations."""
        self.redraw_count += 1

    def tearDown(self) -> None:
        """Restore terminal state and close the pty."""
        with contextlib.suppress(OSError):
            self.term.close()
        self.fake.close()

    def test_dispatch_navigate(self) -> None:
        """dispatch_key('j') navigates and requests redraw."""
        result = self.term.dispatch_key("j", self.state, self.redraw)
        self.assertTrue(result)
        self.assertEqual(self.state.current_index, 1)

    def test_dispatch_approve(self) -> None:
        """dispatch_key('a') approves the current hunk."""
        result = self.term.dispatch_key("a", self.state, self.redraw)
        self.assertTrue(result)
        self.assertEqual(self.hunks[0].status, "approved")

    def test_dispatch_approve_file(self) -> None:
        """dispatch_key('A') approves all hunks in the current file."""
        self.hunks[1].file_path = "a.py"
        result = self.term.dispatch_key("A", self.state, self.redraw)
        self.assertTrue(result)
        self.assertTrue(all(h.status == "approved" for h in self.hunks))

    def test_dispatch_unknown_key(self) -> None:
        """An unrecognised key returns False (no redraw)."""
        result = self.term.dispatch_key("z", self.state, self.redraw)
        self.assertFalse(result)

    def test_dispatch_scroll_ctrl_d(self) -> None:
        """Ctrl-D scrolls down and triggers redraw callback."""
        self.term.dispatch_key(neorev.Terminal.KEY_CTRL_D, self.state, self.redraw)
        self.assertGreater(self.state.scroll_offset, 0)
        self.assertEqual(self.redraw_count, 1)

    def test_dispatch_scroll_ctrl_u(self) -> None:
        """Ctrl-U from offset 0 stays at 0."""
        self.term.dispatch_key(neorev.Terminal.KEY_CTRL_U, self.state, self.redraw)
        self.assertEqual(self.state.scroll_offset, 0)

    def test_dispatch_help(self) -> None:
        """dispatch_key('?') renders the help screen (needs a key to dismiss)."""
        tty.setraw(self.fake.slave_fd)
        self.fake.inject_keys(b"q")  # Key to dismiss help.
        result = self.term.dispatch_key("?", self.state, self.redraw)
        self.assertTrue(result)

    def test_dispatch_scroll_ctrl_d_increments(self) -> None:
        """Ctrl-D increments scroll_offset by half-page amount."""
        self.state.scroll_offset = 0
        self.term.dispatch_key(neorev.Terminal.KEY_CTRL_D, self.state, self.redraw)
        self.assertEqual(self.state.scroll_offset, SCROLL_HALF_PAGE)

    def test_dispatch_scroll_ctrl_u_clamps_to_zero(self) -> None:
        """Ctrl-U from a small offset clamps to 0."""
        self.state.scroll_offset = 1
        self.term.dispatch_key(neorev.Terminal.KEY_CTRL_U, self.state, self.redraw)
        self.assertEqual(self.state.scroll_offset, 0)

    def test_dispatch_g_followed_by_invalid(self) -> None:
        """Pressing g then an invalid key returns False."""
        tty.setraw(self.fake.slave_fd)
        self.fake.inject_keys(b"z")
        result = self.term.dispatch_key("g", self.state, self.redraw)
        self.assertFalse(result)

    def test_dispatch_navigate_resets_scroll(self) -> None:
        """Navigating after scrolling resets scroll_offset to 0."""
        self.state.scroll_offset = 10
        self.term.dispatch_key("j", self.state, self.redraw)
        self.assertEqual(self.state.scroll_offset, 0)


class TestArgParser(unittest.TestCase):
    """Tests for build_arg_parser."""

    def test_output_required(self) -> None:
        """Parser requires an output positional argument."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["out.txt"])
        self.assertEqual(args.output, "out.txt")
        self.assertFalse(args.clip)

    def test_clip_flag(self) -> None:
        """The --clip flag is recognised."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--clip", "out.txt"])
        self.assertTrue(args.clip)

    def test_missing_output_fails(self) -> None:
        """Omitting the output file raises SystemExit."""
        parser = neorev.build_arg_parser()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args([])


class TestTruncateAnsiText(unittest.TestCase):
    """Tests for truncate_ansi_text."""

    def test_no_truncation_needed(self) -> None:
        """Return text unchanged when it fits within max_visible."""
        text = "hello"
        result = neorev.truncate_ansi_text(text, TERM_WIDTH)
        self.assertEqual(result, text)

    def test_plain_text_truncated(self) -> None:
        """Truncate plain text and append ellipsis."""
        text = "hello world"
        result = neorev.truncate_ansi_text(text, TINY_WIDTH)
        visible = neorev.ANSI_ESCAPE_TEXT_RE.sub("", result)
        self.assertEqual(len(visible), TINY_WIDTH)
        self.assertTrue(visible.endswith(neorev.TRUNCATION_ELLIPSIS))

    def test_ansi_sequences_preserved(self) -> None:
        """ANSI escape sequences pass through without consuming visible budget."""
        text = f"{neorev.BOLD}hello world{neorev.RESET}"
        result = neorev.truncate_ansi_text(text, TINY_WIDTH)
        visible = neorev.ANSI_ESCAPE_TEXT_RE.sub("", result)
        self.assertEqual(len(visible), TINY_WIDTH)
        self.assertIn(neorev.BOLD, result)

    def test_zero_width_returns_empty(self) -> None:
        """A max_visible of zero produces an empty string."""
        self.assertEqual(neorev.truncate_ansi_text("hello", 0), "")

    def test_width_one_returns_ellipsis(self) -> None:
        """A max_visible of one returns just the ellipsis character."""
        result = neorev.truncate_ansi_text("hello world", 1)
        self.assertEqual(result, neorev.TRUNCATION_ELLIPSIS)

    def test_ends_with_reset(self) -> None:
        """Truncated ANSI text ends with RESET before ellipsis."""
        text = f"{neorev.RED}a long red string{neorev.RESET}"
        result = neorev.truncate_ansi_text(text, TINY_WIDTH)
        self.assertIn(neorev.RESET, result)


class TestTopBarTruncation(unittest.TestCase):
    """Tests for build_top_bar width truncation."""

    def test_narrow_width_truncates(self) -> None:
        """Top bar is truncated when term_width is small."""
        hunk = make_hunk(comment="a" * LONG_COMMENT_LENGTH)
        bar = neorev.build_top_bar(hunk, 0, 1, 0, term_width=NARROW_PROGRESS_WIDTH)
        visible = neorev.visible_len(bar)
        self.assertLessEqual(visible, NARROW_PROGRESS_WIDTH)

    def test_no_truncation_without_width(self) -> None:
        """Top bar is not truncated when term_width is 0 (default)."""
        hunk = make_hunk(comment="a" * LONG_COMMENT_LENGTH)
        bar = neorev.build_top_bar(hunk, 0, 1, 0, term_width=0)
        visible = neorev.visible_len(bar)
        self.assertGreater(visible, NARROW_PROGRESS_WIDTH)


class TestProgressMarkersTinyWidth(unittest.TestCase):
    """Tests for build_progress_markers with tiny terminal widths."""

    def test_very_narrow_returns_empty(self) -> None:
        """Extremely narrow terminals produce an empty marker line."""
        hunks = [
            neorev.Hunk(
                file_header="", range_line="", body="", raw="", file_path="f.py"
            )
        ]
        # prefix_width=2, so available < MARKER_WIDTH
        too_narrow = neorev.MARKER_WIDTH + 1
        result = neorev.build_progress_markers(hunks, 0, too_narrow)
        self.assertEqual(result, "")

    def test_marker_width_boundary(self) -> None:
        """Widths exactly fitting one marker still produce output."""
        hunks = [
            neorev.Hunk(
                file_header="", range_line="", body="", raw="", file_path="f.py"
            )
        ]
        min_working_width = neorev.MARKER_WIDTH + 2  # prefix_width = 2
        result = neorev.build_progress_markers(hunks, 0, min_working_width)
        self.assertNotEqual(result, "")


class TestFooterTinyWidth(unittest.TestCase):
    """Tests for build_footer_line with very small widths."""

    def test_zero_width(self) -> None:
        """Zero width produces empty footer."""
        result = neorev.build_footer_line(0)
        self.assertEqual(result, "")

    def test_tiny_width_no_crash(self) -> None:
        """Tiny widths produce a footer without crashing."""
        for w in range(1, TINY_WIDTH + 1):
            result = neorev.build_footer_line(w)
            visible = neorev.visible_len(result)
            self.assertLessEqual(visible, w)


class TestViewportClampOnResize(unittest.TestCase):
    """Tests for viewport clamping after height changes."""

    def test_scroll_clamped_after_height_increase(self) -> None:
        """Increasing height clamps scroll offset to valid range."""
        line_rows = [1] * OVERFLOWING_LINE_COUNT
        small_height = TERM_HEIGHT
        vp_small = neorev.compute_diff_viewport(
            len(line_rows),
            small_height,
            OUT_OF_BOUNDS_OFFSET,
        )
        big_height = TERM_HEIGHT * 2
        vp_big = neorev.compute_diff_viewport(
            len(line_rows),
            big_height,
            vp_small.scroll_offset,
        )
        self.assertLessEqual(vp_big.scroll_offset, vp_small.scroll_offset)

    def test_scroll_clamped_after_height_decrease(self) -> None:
        """Decreasing height still produces a valid viewport."""
        line_rows = [1] * OVERFLOWING_LINE_COUNT
        vp = neorev.compute_diff_viewport(
            len(line_rows),
            neorev.MIN_TERMINAL_HEIGHT,
            MIDDLE_SCROLL_OFFSET,
        )
        self.assertGreaterEqual(vp.visible_line_count, 1)


class TestDrainFd(unittest.TestCase):
    """Tests for drain_fd."""

    def setUp(self) -> None:
        """Create a pipe for testing."""
        self.read_fd, self.write_fd = os.pipe()
        os.set_blocking(self.read_fd, False)
        os.set_blocking(self.write_fd, False)

    def tearDown(self) -> None:
        """Close both pipe ends."""
        for fd in (self.read_fd, self.write_fd):
            with contextlib.suppress(OSError):
                os.close(fd)

    def test_drains_all_bytes(self) -> None:
        """All pending bytes are consumed from the fd."""
        os.write(self.write_fd, b"abc")
        neorev.drain_fd(self.read_fd)
        ready, _, _ = select.select([self.read_fd], [], [], neorev.SELECT_IMMEDIATE)
        self.assertFalse(ready)

    def test_no_data_does_not_block(self) -> None:
        """Calling drain_fd with no pending data returns immediately."""
        neorev.drain_fd(self.read_fd)


class TestDebounceResize(unittest.TestCase):
    """Tests for debounce_resize."""

    def setUp(self) -> None:
        """Create a pipe for testing."""
        self.read_fd, self.write_fd = os.pipe()
        os.set_blocking(self.read_fd, False)
        os.set_blocking(self.write_fd, False)

    def tearDown(self) -> None:
        """Close both pipe ends."""
        for fd in (self.read_fd, self.write_fd):
            with contextlib.suppress(OSError):
                os.close(fd)

    def test_no_followup_returns_quickly(self) -> None:
        """When no further signal arrives, debounce returns after timeout."""
        neorev.debounce_resize(self.read_fd)

    def test_coalesces_pending_bytes(self) -> None:
        """Pending bytes written before call are drained."""
        os.write(self.write_fd, SIGWINCH_BYTE * 2)
        neorev.debounce_resize(self.read_fd)
        ready, _, _ = select.select([self.read_fd], [], [], neorev.SELECT_IMMEDIATE)
        self.assertFalse(ready)


class TestReadKeyWithWakeup(unittest.TestCase):
    """Tests for Terminal.read_key with a wakeup pipe fd."""

    def setUp(self) -> None:
        """Create a fake TTY, Terminal, and a wakeup pipe."""
        self.fake = FakeTTY()
        self.term = self.fake.make_terminal()
        self.wakeup_r, self.wakeup_w = os.pipe()
        os.set_blocking(self.wakeup_r, False)
        os.set_blocking(self.wakeup_w, False)

    def tearDown(self) -> None:
        """Close all fds."""
        with contextlib.suppress(OSError):
            self.term.close()
        self.fake.close()
        for fd in (self.wakeup_r, self.wakeup_w):
            with contextlib.suppress(OSError):
                os.close(fd)

    def test_wakeup_returns_resize_key(self) -> None:
        """A byte on the wakeup pipe makes read_key return RESIZE_KEY."""
        tty.setraw(self.fake.slave_fd)
        os.write(self.wakeup_w, SIGWINCH_BYTE)
        key = self.term.read_key(wakeup_read_fd=self.wakeup_r)
        self.assertEqual(key, neorev.Terminal.KEY_RESIZE)

    def test_tty_input_still_works_with_wakeup(self) -> None:
        """Normal keypresses are returned even when wakeup fd is set."""
        tty.setraw(self.fake.slave_fd)
        self.fake.inject_keys(b"x")
        key = self.term.read_key(wakeup_read_fd=self.wakeup_r)
        self.assertEqual(key, "x")

    def test_no_wakeup_fd_reads_normally(self) -> None:
        """Negative wakeup_read_fd falls through to normal read."""
        tty.setraw(self.fake.slave_fd)
        self.fake.inject_keys(b"k")
        key = self.term.read_key(wakeup_read_fd=None)
        self.assertEqual(key, "k")

    def test_wakeup_drains_pipe(self) -> None:
        """After returning RESIZE_KEY the pipe is fully drained."""
        tty.setraw(self.fake.slave_fd)
        os.write(self.wakeup_w, SIGWINCH_BYTE * 3)
        self.term.read_key(wakeup_read_fd=self.wakeup_r)
        ready, _, _ = select.select([self.wakeup_r], [], [], neorev.SELECT_IMMEDIATE)
        self.assertFalse(ready)


class TestApplyResize(unittest.TestCase):
    """Tests for Terminal.apply_resize cache invalidation."""

    def setUp(self) -> None:
        """Create a fake TTY and Terminal."""
        self.fake = FakeTTY()
        self.term = self.fake.make_terminal()

    def tearDown(self) -> None:
        """Close terminal and pty."""
        with contextlib.suppress(OSError):
            self.term.close()
        self.fake.close()

    def test_cache_cleared_on_width_change(self) -> None:
        """Delta cache is cleared when terminal width changes."""
        cache: dict[int, bytes] = {0: b"old"}
        self.term.width = TERM_WIDTH
        new_width = TERM_WIDTH + RESIZE_WIDTH_DELTA
        winsize = struct.pack(
            WINSIZE_FORMAT,
            TERM_HEIGHT,
            new_width,
            TERM_PIXEL_SIZE,
            TERM_PIXEL_SIZE,
        )
        fcntl.ioctl(self.fake.slave_fd, termios.TIOCSWINSZ, winsize)
        self.term.apply_resize(cache)
        self.assertEqual(cache, {})
        self.assertEqual(self.term.width, new_width)

    def test_cache_kept_on_same_width(self) -> None:
        """Delta cache is kept when width does not change."""
        cache: dict[int, bytes] = {0: b"old"}
        self.term.apply_resize(cache)
        self.assertEqual(cache, {0: b"old"})


if __name__ == "__main__":
    unittest.main()
