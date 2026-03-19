#!/usr/bin/env python3
"""Tests for neorev — interactive diff review tool."""

import contextlib
import fcntl
import importlib.machinery
import io
import os
import re
import select
import struct
import sys
import tempfile
import termios
import tty
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Self
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
READ_DRAIN_TIMEOUT = 0.01
READ_DRAIN_EMPTY_POLLS = 3
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
HUNKS_PER_FILE = 2
TOP_BAR_INDEX_TOKEN = "Hunk 1/5"
REVIEW_SCREEN_INDEX_TOKEN = "Hunk 1/1"
REVIEW_SCREEN_LOCATION_TOKEN = "hello.py:1"
REVIEW_SCREEN_FOOTER_TOKEN = "j/k"
ROUND_TRIP_COMMENT_TEXT = "fix this"
WORKFLOW_FLAG_COMMENT = "Please split this import change."
WORKFLOW_GLOBAL_NOTE = "Can we add tests for this behavior?"
WORKFLOW_RESUME_FLAG = "Carry this change request forward"
WORKFLOW_RESUME_GLOBAL = "Overall: check module boundaries"
WORKFLOW_PRECEDENCE_QUESTION = "Why is this import needed?"
WORKFLOW_STALE_MESSAGE = "no longer match any hunk"
WORKFLOW_ALL_CLEAR_SUMMARY = "# 1/2 hunks approved."
GLOBAL_NOTE_CREATED_TEXT = "needs follow-up"
GLOBAL_NOTE_EDITED_TEXT = "edited follow-up"
GLOBAL_NOTE_EDIT_KEY = "e"
GLOBAL_NOTE_DELETE_KEY = "d"
GLOBAL_NOTE_EXIT_KEY = "q"
GLOBAL_NOTE_INDEX_KEY = "1"
GLOBAL_NOTE_ADD_PREFIX = "g"
GLOBAL_NOTE_ADD_QUESTION_KEY = "c"
GLOBAL_NOTE_ADD_FLAG_KEY = "f"
COMMENT_KEY_QUESTION = "c"
DISPATCH_COMMENT_TEXT = "needs reviewer context"
DISPATCH_REDRAW_FALSE = False
ADDED_LINE_NUMBER = 2
REMOVED_LINE_NUMBER = 1
LINE_TARGET_NOTE_LINE = 42
LINE_TARGET_NOTE_TEXT = "fix the off-by-one"
GLOBAL_PARSE_NOTE_TEXT = "overall design concern"
LINE_TARGET_APPLY_TEXT = "adjust this import"
UPSERT_NOTE_TEXT = "initial note"
UPSERT_NOTE_UPDATED_TEXT = "updated note"
SCROLL_HALF_PAGE = max(
    1,
    (TERM_HEIGHT - neorev.CHROME_ROWS - neorev.SCROLL_INDICATOR_ROWS) // 2,
)
LINE_PICKER_MANY_LINES = 30

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


def make_hunk(  # noqa: PLR0913
    file_path: str = "test.py",
    start_line: int = 1,
    body: str = "+added line",
    status: neorev.Status | None = None,
    comment: str = "",
    *,
    approved: bool = False,
    notes: list[neorev.HunkNote] | None = None,
) -> neorev.Hunk:
    """Create a Hunk with sensible defaults for testing."""
    range_line = f"@@ -1,3 +{start_line},4 @@"
    hunk = neorev.Hunk(
        file_header=f"diff --git a/{file_path} b/{file_path}",
        range_line=range_line,
        body=body,
        raw=f"diff --git a/{file_path} b/{file_path}\n{range_line}\n{body}",
        file_path=file_path,
        start_line=start_line,
    )
    if status == neorev.Status.APPROVED:
        hunk.approved = True
    elif status == neorev.Status.FLAG:
        hunk.notes = [
            neorev.HunkNote(
                kind=neorev.NoteKind.FLAG,
                target=neorev.HunkTarget(),
                text=comment,
            )
        ]
    elif status == neorev.Status.QUESTION:
        hunk.notes = [
            neorev.HunkNote(
                kind=neorev.NoteKind.QUESTION,
                target=neorev.HunkTarget(),
                text=comment,
            )
        ]
    if approved:
        hunk.approved = True
    if notes is not None:
        hunk.notes = notes
    return hunk


def remove_ansi_escape_sequences(text: str) -> str:
    """Return *text* with ANSI escape sequences removed."""
    return neorev.ANSI_ESCAPE_TEXT_RE.sub("", text)


def decode_visible_terminal_output(output: bytes) -> str:
    """Decode terminal bytes and strip ANSI escape sequences."""
    text = output.decode("utf-8", errors="replace")
    return remove_ansi_escape_sequences(text)


class MainWorkflowTerminal:
    """Minimal Terminal stub for driving main() workflow tests."""

    ALT_SCREEN_ON = ""
    ALT_SCREEN_OFF = ""
    CURSOR_HIDE = ""
    CURSOR_SHOW = ""

    def __init__(self, script: Callable[[neorev.ReviewState], None]) -> None:
        """Store the script callback to mutate review state in run_review_loop."""
        self.script = script

    def __enter__(self) -> Self:
        """Return self for context-managed use."""
        return self

    def __exit__(self, *_: object) -> None:
        """Exit context manager without extra cleanup."""

    def write(self, _data: bytes | str) -> None:
        """Accept writes performed by main() without rendering anything."""

    def run_review_loop(
        self,
        state: neorev.ReviewState,
        _delta_cache: dict[int, bytes],
    ) -> None:
        """Apply the scripted state mutations and return immediately."""
        self.script(state)


def run_main_with_scripted_terminal(
    diff_text: str,
    output_path: str,
    script: Callable[[neorev.ReviewState], None],
) -> str:
    """Run neorev.main() with a fake terminal script and return captured stderr."""
    stderr = io.StringIO()
    argv = ["neorev", output_path]
    with (
        patch.object(
            neorev,
            "Terminal",
            side_effect=lambda: MainWorkflowTerminal(script),
        ),
        patch.object(sys, "argv", argv),
        patch.object(sys, "stdin", io.StringIO(diff_text)),
        contextlib.redirect_stderr(stderr),
    ):
        neorev.main()
    return stderr.getvalue()


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
        """Read and drain currently available output from the pseudo-terminal."""
        ready, _, _ = select.select([self.master_fd], [], [], SELECT_TIMEOUT)
        if not ready:
            return b""

        chunks: list[bytes] = [os.read(self.master_fd, size)]
        empty_polls = 0
        while empty_polls < READ_DRAIN_EMPTY_POLLS:
            ready, _, _ = select.select(
                [self.master_fd],
                [],
                [],
                READ_DRAIN_TIMEOUT,
            )
            if not ready:
                empty_polls += 1
                continue

            chunk = os.read(self.master_fd, size)
            if not chunk:
                break
            chunks.append(chunk)
            empty_polls = 0
        return b"".join(chunks)

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
            make_hunk(status=neorev.Status.APPROVED),
            make_hunk(),
            make_hunk(status=neorev.Status.APPROVED),
        ]
        encoded = neorev.encode_approved_bitmap(hunks)
        decoded = neorev.decode_approved_bitmap(encoded, len(hunks))
        self.assertEqual(decoded, [True, False, True])

    def test_all_approved(self) -> None:
        """All-approved bitmap round-trips correctly."""
        hunks = [make_hunk(status=neorev.Status.APPROVED) for _ in range(10)]
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
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        encoded = neorev.encode_approved_bitmap(hunks)
        self.assertEqual(neorev.decode_approved_bitmap(encoded, 99), [])

    def test_single_hunk_approved(self) -> None:
        """Edge case: single approved hunk."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        encoded = neorev.encode_approved_bitmap(hunks)
        self.assertEqual(neorev.decode_approved_bitmap(encoded, 1), [True])

    def test_exactly_8_hunks(self) -> None:
        """8 hunks (exactly 1 byte boundary) round-trip correctly."""
        statuses = [
            neorev.Status.APPROVED,
            None,
            neorev.Status.APPROVED,
            None,
            None,
            neorev.Status.APPROVED,
            neorev.Status.APPROVED,
            None,
        ]
        hunks = [make_hunk(status=s) for s in statuses]
        encoded = neorev.encode_approved_bitmap(hunks)
        decoded = neorev.decode_approved_bitmap(encoded, BYTE_BOUNDARY_HUNK_COUNT)
        expected = [s == neorev.Status.APPROVED for s in statuses]
        self.assertEqual(decoded, expected)

    def test_9_hunks(self) -> None:
        """9 hunks (2 bytes) with mixed approvals round-trip correctly."""
        statuses = [
            neorev.Status.APPROVED,
            None,
            neorev.Status.APPROVED,
            None,
            None,
            neorev.Status.APPROVED,
            neorev.Status.APPROVED,
            None,
            neorev.Status.APPROVED,
        ]
        hunks = [make_hunk(status=s) for s in statuses]
        encoded = neorev.encode_approved_bitmap(hunks)
        decoded = neorev.decode_approved_bitmap(encoded, OVER_BYTE_BOUNDARY_HUNK_COUNT)
        expected = [s == neorev.Status.APPROVED for s in statuses]
        self.assertEqual(decoded, expected)

    def test_empty_hunks(self) -> None:
        """0 hunks encodes and decodes to empty list."""
        encoded = neorev.encode_approved_bitmap([])
        self.assertEqual(neorev.decode_approved_bitmap(encoded, 0), [])

    def test_decode_truncated_data(self) -> None:
        """Valid base64 with too few bytes for num_hunks returns empty list."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        encoded = neorev.encode_approved_bitmap(hunks)
        self.assertEqual(neorev.decode_approved_bitmap(encoded, 16), [])


class TestFormatOutput(unittest.TestCase):
    """Tests for format_output and friends."""

    def test_all_approved(self) -> None:
        """All approved hunks produce a short 'all clear' output."""
        hunks = [
            make_hunk(status=neorev.Status.APPROVED),
            make_hunk(status=neorev.Status.APPROVED),
        ]
        output = neorev.format_output(hunks, [])
        self.assertIn("all clear", output)
        self.assertIn("neorev:", output)

    def test_flag_output(self) -> None:
        """A flagged hunk appears as CHANGE REQUESTED in the output."""
        hunks = [make_hunk(status=neorev.Status.FLAG, comment="fix this")]
        output = neorev.format_output(hunks, [])
        self.assertIn("CHANGE REQUESTED", output)
        self.assertIn("fix this", output)

    def test_question_output(self) -> None:
        """A questioned hunk appears as QUESTION in the output."""
        hunks = [make_hunk(status=neorev.Status.QUESTION, comment="why?")]
        output = neorev.format_output(hunks, [])
        self.assertIn("QUESTION", output)
        self.assertIn("why?", output)

    def test_global_notes_in_output(self) -> None:
        """Global notes appear in the output."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        notes = [neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="add tests")]
        output = neorev.format_output(hunks, notes)
        self.assertIn("(global)", output)
        self.assertIn("add tests", output)

    def test_long_hunk_body_trimmed(self) -> None:
        """Hunk bodies exceeding HUNK_BODY_MAX_LINES are trimmed."""
        long_body = "\n".join(f"+line {i}" for i in range(LONG_BODY_LINE_COUNT))
        hunks = [
            make_hunk(body=long_body, status=neorev.Status.FLAG, comment="too long")
        ]
        output = neorev.format_output(hunks, [])
        self.assertIn("# ...", output)

    def test_bitmap_present_in_output(self) -> None:
        """Output always contains a neorev: bitmap line."""
        hunks = [make_hunk(status=neorev.Status.FLAG, comment="x")]
        output = neorev.format_output(hunks, [])
        self.assertIn("# neorev:", output)

    def test_no_status_hunks(self) -> None:
        """Hunks with no status and no actionable items get 'all clear'."""
        hunks = [make_hunk(), make_hunk()]
        output = neorev.format_output(hunks, [])
        self.assertIn("all clear", output)
        self.assertIn("0/2 hunks approved", output)
        self.assertIn("# neorev:", output)

    def test_global_note_question_label(self) -> None:
        """A global question note section header uses QUESTION label."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        notes = [
            neorev.GlobalNote(kind=neorev.NoteKind.QUESTION, text="why this approach?")
        ]
        output = neorev.format_output(hunks, notes)
        self.assertIn("[QUESTION] (global)", output)
        self.assertNotIn("[CHANGE REQUESTED] (global)", output)

    def test_multiline_comment_quoted(self) -> None:
        """Each line of a multi-line comment gets a > prefix."""
        hunks = [
            make_hunk(
                status=neorev.Status.FLAG, comment="line one\nline two\nline three"
            )
        ]
        output = neorev.format_output(hunks, [])
        self.assertIn("> line one\n", output)
        self.assertIn("> line two\n", output)
        self.assertIn("> line three\n", output)

    def test_body_exactly_max_lines_not_trimmed(self) -> None:
        """A body with exactly HUNK_BODY_MAX_LINES lines is not trimmed."""
        body = "\n".join(f"+line {i}" for i in range(neorev.HUNK_BODY_MAX_LINES))
        hunks = [make_hunk(body=body, status=neorev.Status.FLAG, comment="ok")]
        output = neorev.format_output(hunks, [])
        self.assertNotIn("# ...", output)

    def test_body_one_over_max_lines_trimmed(self) -> None:
        """A body with HUNK_BODY_MAX_LINES + 1 lines is trimmed."""
        body = "\n".join(f"+line {i}" for i in range(neorev.HUNK_BODY_MAX_LINES + 1))
        hunks = [make_hunk(body=body, status=neorev.Status.FLAG, comment="too long")]
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
            make_hunk(
                file_path="a.py",
                status=neorev.Status.FLAG,
                comment=ROUND_TRIP_COMMENT_TEXT,
            ),
            make_hunk(file_path="b.py", status=neorev.Status.APPROVED),
        ]
        output = neorev.format_output(hunks, [])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(output)
            path = f.name

        try:
            annotations, _, _ = neorev.load_previous_review(path)
            key = ("a.py", hunks[0].range_line, neorev.HunkTarget())
            self.assertIn(key, annotations)
            kind, comment = annotations[key]
            self.assertEqual(kind, neorev.NoteKind.FLAG)
            self.assertEqual(comment, ROUND_TRIP_COMMENT_TEXT)
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
        """apply_previous_review sets notes on matching hunks."""
        hunks = [make_hunk(file_path="x.py")]
        target = neorev.HunkTarget()
        annotations = {
            ("x.py", hunks[0].range_line, target): (neorev.NoteKind.QUESTION, "why?"),
        }
        matched = neorev.apply_previous_review(hunks, annotations)
        self.assertEqual(matched, 1)
        self.assertEqual(len(hunks[0].notes), 1)
        self.assertEqual(hunks[0].notes[0].kind, neorev.NoteKind.QUESTION)
        self.assertEqual(hunks[0].notes[0].text, "why?")

    def test_apply_no_match(self) -> None:
        """Unmatched annotations don't alter hunks."""
        hunks = [make_hunk(file_path="x.py")]
        target = neorev.HunkTarget()
        annotations = {
            ("other.py", "@@ -1 +1 @@", target): (neorev.NoteKind.FLAG, "n/a"),
        }
        matched = neorev.apply_previous_review(hunks, annotations)
        self.assertEqual(matched, 0)
        self.assertEqual(hunks[0].notes, [])

    def test_global_notes_round_trip(self) -> None:
        """Global notes survive format_output → load_previous_review."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        notes = [
            neorev.GlobalNote(kind=neorev.NoteKind.QUESTION, text="overall design?")
        ]
        output = neorev.format_output(hunks, notes)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(output)
            path = f.name

        try:
            _, loaded_notes, _ = neorev.load_previous_review(path)
            self.assertEqual(len(loaded_notes), 1)
            self.assertEqual(loaded_notes[0].kind, neorev.NoteKind.QUESTION)
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
                status=neorev.Status.FLAG,
                comment="first line\nsecond line\nthird line",
            )
        ]
        output = neorev.format_output(hunks, [])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(output)
            path = f.name

        try:
            annotations, _, _ = neorev.load_previous_review(path)
            key = ("m.py", hunks[0].range_line, neorev.HunkTarget())
            _, comment = annotations[key]
            self.assertIn("first line", comment)
            self.assertIn("second line", comment)
            self.assertIn("third line", comment)
        finally:
            os.unlink(path)

    def test_multiple_global_notes_round_trip(self) -> None:
        """Multiple global notes of different kinds survive round-trip."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        notes = [
            neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="add tests"),
            neorev.GlobalNote(kind=neorev.NoteKind.QUESTION, text="why this design?"),
        ]
        output = neorev.format_output(hunks, notes)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(output)
            path = f.name

        try:
            _, loaded_notes, _ = neorev.load_previous_review(path)
            self.assertEqual(len(loaded_notes), 2)
            self.assertEqual(loaded_notes[0].kind, neorev.NoteKind.FLAG)
            self.assertEqual(loaded_notes[1].kind, neorev.NoteKind.QUESTION)
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
        target = neorev.HunkTarget()
        annotations = {
            ("a.py", hunks[0].range_line, target): (neorev.NoteKind.FLAG, "fix a"),
            ("b.py", hunks[1].range_line, target): (
                neorev.NoteKind.QUESTION,
                "why b",
            ),
        }
        matched = neorev.apply_previous_review(hunks, annotations)
        self.assertEqual(matched, 2)
        self.assertEqual(hunks[0].notes[0].kind, neorev.NoteKind.FLAG)
        self.assertEqual(hunks[1].notes[0].kind, neorev.NoteKind.QUESTION)


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
        """Approving then re-approving toggles the approved flag."""
        neorev.handle_approve(self.state)
        self.assertTrue(self.hunks[0].approved)
        self.state.current_index = 0
        neorev.handle_approve(self.state)
        self.assertFalse(self.hunks[0].approved)

    def test_approve_ignores_hunk_with_hunk_note(self) -> None:
        """Approving a hunk that has a hunk-level note has no effect."""
        self.hunks[0].notes = [
            neorev.HunkNote(
                kind=neorev.NoteKind.FLAG,
                target=neorev.HunkTarget(),
                text="old comment",
            )
        ]
        neorev.handle_approve(self.state)
        self.assertFalse(self.hunks[0].approved)
        self.assertEqual(len(self.hunks[0].notes), 1)

    def test_approve_ignores_hunk_with_line_note(self) -> None:
        """Approving a hunk that has a line-level note has no effect."""
        self.hunks[0].notes = [
            neorev.HunkNote(
                kind=neorev.NoteKind.QUESTION,
                target=neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=1),
                text="why this?",
            )
        ]
        neorev.handle_approve(self.state)
        self.assertFalse(self.hunks[0].approved)
        self.assertEqual(len(self.hunks[0].notes), 1)

    def test_approve_advances_to_next_unhandled(self) -> None:
        """After approval, cursor moves to the next unhandled hunk."""
        self.hunks[1].approved = True
        neorev.handle_approve(self.state)
        self.assertEqual(self.state.current_index, 2)

    def test_approve_file(self) -> None:
        """Approve-file approves all hunks with the same file_path."""
        for h in self.hunks:
            h.file_path = "same.py"
        neorev.handle_approve_file(self.state)
        for h in self.hunks:
            self.assertTrue(h.approved)

    def test_approve_file_skips_other_files(self) -> None:
        """Approve-file only touches hunks matching the current file."""
        self.hunks[0].file_path = "a.py"
        self.hunks[1].file_path = "b.py"
        self.hunks[2].file_path = "a.py"
        neorev.handle_approve_file(self.state)
        self.assertTrue(self.hunks[0].approved)
        self.assertFalse(self.hunks[1].approved)
        self.assertTrue(self.hunks[2].approved)

    def test_approve_file_skips_hunks_with_notes(self) -> None:
        """Approve-file only approves hunks that have no notes."""
        for h in self.hunks:
            h.file_path = "same.py"
        self.hunks[1].notes = [
            neorev.HunkNote(
                kind=neorev.NoteKind.FLAG,
                target=neorev.HunkTarget(),
                text="needs work",
            )
        ]
        self.hunks[2].notes = [
            neorev.HunkNote(
                kind=neorev.NoteKind.QUESTION,
                target=neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=1),
                text="why?",
            )
        ]
        neorev.handle_approve_file(self.state)
        self.assertTrue(self.hunks[0].approved)
        self.assertFalse(self.hunks[1].approved)
        self.assertFalse(self.hunks[2].approved)
        self.assertEqual(len(self.hunks[1].notes), 1)
        self.assertEqual(len(self.hunks[2].notes), 1)

    def test_find_next_unhandled_wraps(self) -> None:
        """find_next_unhandled_hunk wraps around the list."""
        self.hunks[1].approved = True
        self.hunks[2].approved = True
        result = neorev.find_next_unhandled_hunk(self.hunks, 2)
        self.assertEqual(result, 0)

    def test_find_next_unhandled_all_handled(self) -> None:
        """When all hunks are handled, returns current index."""
        for h in self.hunks:
            h.approved = True
        result = neorev.find_next_unhandled_hunk(self.hunks, 1)
        self.assertEqual(result, 1)

    def test_find_initial_hunk_index(self) -> None:
        """find_initial_hunk_index returns the first unhandled hunk."""
        self.hunks[0].approved = True
        self.assertEqual(neorev.find_initial_hunk_index(self.hunks), 1)

    def test_find_initial_all_handled(self) -> None:
        """When all hunks are handled, returns 0."""
        for h in self.hunks:
            h.approved = True
        self.assertEqual(neorev.find_initial_hunk_index(self.hunks), 0)

    def test_navigate_single_hunk(self) -> None:
        """With a single hunk, both j and k return False."""
        state = neorev.ReviewState(hunks=[make_hunk()], global_notes=[])
        self.assertFalse(neorev.handle_navigation("j", state))
        self.assertFalse(neorev.handle_navigation("k", state))
        self.assertEqual(state.current_index, 0)

    def test_approve_already_flagged_hunk_has_no_effect(self) -> None:
        """Approving a flagged hunk has no effect — notes protect the hunk."""
        self.hunks[0].notes = [
            neorev.HunkNote(
                kind=neorev.NoteKind.FLAG, target=neorev.HunkTarget(), text="fix this"
            )
        ]
        neorev.handle_approve(self.state)
        self.assertFalse(self.hunks[0].approved)
        self.assertEqual(len(self.hunks[0].notes), 1)

    def test_approve_file_idempotent_on_approved(self) -> None:
        """Approve-file on already-approved hunks keeps them approved."""
        for h in self.hunks:
            h.file_path = "same.py"
            h.approved = True
        neorev.handle_approve_file(self.state)
        for h in self.hunks:
            self.assertTrue(h.approved)

    def test_approve_file_advances_to_other_file(self) -> None:
        """After approve-file, cursor moves to next unhandled hunk in another file."""
        self.hunks[0].file_path = "a.py"
        self.hunks[1].file_path = "a.py"
        self.hunks[2].file_path = "b.py"
        neorev.handle_approve_file(self.state)
        self.assertEqual(self.state.current_index, 2)

    def test_find_next_unhandled_single_unhandled(self) -> None:
        """With one unhandled hunk, it is always found regardless of position."""
        self.hunks[0].approved = True
        self.hunks[1].approved = True
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
        self.assertGreaterEqual(vp.scroll_offset, 0)
        self.assertLess(vp.scroll_offset, len(line_rows))

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

    def test_scroll_to_end_fills_screen(self) -> None:
        """Scrolling to the end still fills the available screen with content."""
        total = OVERFLOWING_LINE_COUNT
        vp = neorev.compute_diff_viewport(total, TERM_HEIGHT, OUT_OF_BOUNDS_OFFSET)
        avail = TERM_HEIGHT - neorev.CHROME_ROWS - neorev.SCROLL_INDICATOR_ROWS
        self.assertGreaterEqual(vp.visible_line_count, min(avail, total))


class TestChrome(unittest.TestCase):
    """Tests for top bar, hunk markers, progress markers, and footer."""

    def test_top_bar_contains_index(self) -> None:
        """Top bar shows 'Hunk N/total'."""
        hunk = make_hunk()
        bar = neorev.build_top_bar(hunk, 0, [hunk] * 5, [])
        visible_bar = remove_ansi_escape_sequences(bar)
        self.assertIn(TOP_BAR_INDEX_TOKEN, visible_bar)

    def test_top_bar_global_count(self) -> None:
        """Top bar shows global note count when present."""
        hunk = make_hunk()
        global_notes = [
            neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="g1"),
            neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="g2"),
            neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="g3"),
        ]
        bar = neorev.build_top_bar(hunk, 0, [hunk], global_notes)
        self.assertIn("global", bar)
        self.assertIn("3", bar)

    def test_hunk_marker_styles(self) -> None:
        """Each status produces a distinct marker icon."""
        cases = [
            (neorev.Status.APPROVED, "✓"),
            (neorev.Status.FLAG, "✗"),
            (neorev.Status.QUESTION, "?"),
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
        visible_output = decode_visible_terminal_output(output)
        self.assertIsInstance(scroll, int)
        self.assertGreater(len(output), 0)
        self.assertEqual(scroll, 0)
        self.assertIn(REVIEW_SCREEN_INDEX_TOKEN, visible_output)
        self.assertIn(REVIEW_SCREEN_LOCATION_TOKEN, visible_output)
        self.assertIn(REVIEW_SCREEN_FOOTER_TOKEN, visible_output)

    def test_render_help_screen(self) -> None:
        """render_help_screen writes the help box."""
        self.term.render_help_screen()
        output = self.fake.read_output()
        self.assertIn(b"neorev", output)

    def test_help_screen_fits_80_columns(self) -> None:
        """Every help screen line fits within an 80-column terminal."""
        self.term.render_help_screen()
        output = self.fake.read_output()
        visible = decode_visible_terminal_output(output)
        for line in visible.splitlines():
            stripped = line.rstrip()
            if stripped:
                self.assertLessEqual(len(stripped), TERM_WIDTH, repr(stripped))

    def test_render_manage_notes_screen_empty(self) -> None:
        """Notes screen with no notes shows 'No notes yet'."""
        self.term.render_manage_notes_screen([])
        output = self.fake.read_output()
        self.assertIn(b"No notes", output)

    def test_render_manage_notes_screen_with_notes(self) -> None:
        """Notes screen lists existing notes."""
        refs: list[tuple[str, str, neorev.NoteKind]] = [
            ("(global)", "fix this", neorev.NoteKind.FLAG),
        ]
        self.term.render_manage_notes_screen(refs)
        output = self.fake.read_output()
        self.assertIn(b"fix this", output)


class TestBuildManagedNoteRefs(unittest.TestCase):
    """Tests for Terminal.build_managed_note_refs."""

    def setUp(self) -> None:
        """Create a fake TTY and Terminal."""
        self.fake = FakeTTY()
        self.term = self.fake.make_terminal()

    def tearDown(self) -> None:
        """Restore terminal state and close the pty."""
        with contextlib.suppress(OSError):
            self.term.close()

    def test_line_notes_appear_in_refs(self) -> None:
        """Line notes on the current hunk appear in managed note refs."""
        line_target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=42)
        line_note = neorev.HunkNote(
            kind=neorev.NoteKind.FLAG,
            target=line_target,
            text="fix this line",
        )
        hunk = make_hunk(notes=[line_note])
        state = neorev.ReviewState(hunks=[hunk], global_notes=[])
        refs = self.term.build_managed_note_refs(state)
        self.assertEqual(len(refs), 1)
        scope_label, text, kind = refs[0]
        self.assertEqual(text, "fix this line")
        self.assertEqual(kind, neorev.NoteKind.FLAG)
        self.assertIn("+42", scope_label)

    def test_line_notes_from_all_hunks_appear(self) -> None:
        """Line notes from non-current hunks also appear in managed note refs."""
        line_target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=10)
        note_other = neorev.HunkNote(
            kind=neorev.NoteKind.QUESTION,
            target=line_target,
            text="why this?",
        )
        hunk_current = make_hunk(file_path="a.py")
        hunk_other = make_hunk(file_path="b.py", notes=[note_other])
        state = neorev.ReviewState(hunks=[hunk_current, hunk_other], global_notes=[])
        refs = self.term.build_managed_note_refs(state)
        texts = [text for _, text, _ in refs]
        self.assertIn("why this?", texts)


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
        self.assertTrue(self.hunks[0].approved)

    def test_dispatch_approve_file(self) -> None:
        """dispatch_key('A') approves all hunks in the current file."""
        self.hunks[1].file_path = "a.py"
        result = self.term.dispatch_key("A", self.state, self.redraw)
        self.assertTrue(result)
        self.assertTrue(all(h.approved for h in self.hunks))

    def test_dispatch_comment_with_hunk_target(self) -> None:
        """Verify c with hunk target from line picker creates a hunk note."""
        parsed_hunk = neorev.parse_diff(SIMPLE_DIFF)[0]
        state = neorev.ReviewState(hunks=[parsed_hunk], global_notes=[])

        with (
            patch.object(
                self.term,
                "pick_line_target",
                return_value=neorev.HunkTarget(),
            ),
            patch.object(
                self.term,
                "edit_text_outside_tui",
                return_value=DISPATCH_COMMENT_TEXT,
            ),
        ):
            handled = self.term.dispatch_key(COMMENT_KEY_QUESTION, state, self.redraw)

        self.assertTrue(handled)
        self.assertEqual(len(parsed_hunk.notes), 1)
        note = parsed_hunk.notes[0]
        self.assertEqual(note.kind, neorev.NoteKind.QUESTION)
        self.assertEqual(note.text, DISPATCH_COMMENT_TEXT)
        self.assertEqual(note.target, neorev.HunkTarget())

    def test_hunk_note_advances_to_next_hunk(self) -> None:
        """Adding a hunk-level note jumps to the next unhandled hunk."""
        hunk_a = neorev.parse_diff(SIMPLE_DIFF)[0]
        hunk_b = make_hunk(file_path="b.py")
        state = neorev.ReviewState(hunks=[hunk_a, hunk_b], global_notes=[])

        with (
            patch.object(
                self.term, "pick_line_target", return_value=neorev.HunkTarget()
            ),
            patch.object(
                self.term,
                "edit_text_outside_tui",
                return_value=DISPATCH_COMMENT_TEXT,
            ),
        ):
            self.term.dispatch_key("f", state, self.redraw)

        self.assertEqual(state.current_index, 1)

    def test_line_note_stays_on_current_hunk(self) -> None:
        """Adding a line-level note does not jump to the next hunk."""
        hunk_a = neorev.parse_diff(SIMPLE_DIFF)[0]
        hunk_b = make_hunk(file_path="b.py")
        state = neorev.ReviewState(hunks=[hunk_a, hunk_b], global_notes=[])
        line_target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=1)

        with (
            patch.object(self.term, "pick_line_target", return_value=line_target),
            patch.object(
                self.term,
                "edit_text_outside_tui",
                return_value=DISPATCH_COMMENT_TEXT,
            ),
        ):
            self.term.dispatch_key("f", state, self.redraw)

        self.assertEqual(state.current_index, 0)

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

    def test_dispatch_m_opens_manage_notes(self) -> None:
        """Pressing m dispatches to handle_manage_notes and requests redraw."""
        with patch.object(self.term, "handle_manage_notes"):
            result = self.term.dispatch_key("m", self.state, self.redraw)
        self.assertTrue(result)

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


class TestMainWorkflow(unittest.TestCase):
    """High-level tests for new review and resume workflows through main()."""

    def test_new_review_flow_writes_expected_output(self) -> None:
        """A scripted review session writes hunk/global annotations and bitmap."""

        def script(state: neorev.ReviewState) -> None:
            """Apply one flag, one approval, and one global note."""
            state.hunks[0].notes = [
                neorev.HunkNote(
                    kind=neorev.NoteKind.FLAG,
                    target=neorev.HunkTarget(),
                    text=WORKFLOW_FLAG_COMMENT,
                )
            ]
            state.hunks[1].approved = True
            state.global_notes.append(
                neorev.GlobalNote(
                    kind=neorev.NoteKind.QUESTION,
                    text=WORKFLOW_GLOBAL_NOTE,
                )
            )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            output_path = f.name

        try:
            run_main_with_scripted_terminal(TWO_HUNK_DIFF, output_path, script)
            output = Path(output_path).read_text()
            self.assertIn("[CHANGE REQUESTED] hello.py", output)
            self.assertIn(WORKFLOW_FLAG_COMMENT, output)
            self.assertIn("[QUESTION] (global)", output)
            self.assertIn(WORKFLOW_GLOBAL_NOTE, output)
            self.assertIn("# neorev:", output)
        finally:
            os.unlink(output_path)

    def test_resume_workflow_applies_annotations_bitmap_and_global_notes(self) -> None:
        """Resuming from an existing output restores notes and bitmap approvals."""
        previous_hunks = neorev.parse_diff(TWO_HUNK_DIFF)
        previous_hunks[0].notes = [
            neorev.HunkNote(
                kind=neorev.NoteKind.FLAG,
                target=neorev.HunkTarget(),
                text=WORKFLOW_RESUME_FLAG,
            )
        ]
        previous_hunks[1].approved = True
        previous_notes = [
            neorev.GlobalNote(
                kind=neorev.NoteKind.QUESTION, text=WORKFLOW_RESUME_GLOBAL
            )
        ]
        previous_output = neorev.format_output(previous_hunks, previous_notes)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(previous_output)
            output_path = f.name

        try:
            stderr = run_main_with_scripted_terminal(
                TWO_HUNK_DIFF,
                output_path,
                lambda _state: None,
            )
            output = Path(output_path).read_text()
            self.assertIn("Loaded 1 hunk annotation(s), 1 approved hunk(s)", stderr)
            self.assertIn(WORKFLOW_RESUME_FLAG, output)
            self.assertIn(WORKFLOW_RESUME_GLOBAL, output)
        finally:
            os.unlink(output_path)

    def test_resume_annotation_precedence_over_bitmap(self) -> None:
        """Explicit annotation status wins when bitmap marks the same hunk approved."""
        previous_hunks = neorev.parse_diff(TWO_HUNK_DIFF)
        previous_hunks[0].notes = [
            neorev.HunkNote(
                kind=neorev.NoteKind.QUESTION,
                target=neorev.HunkTarget(),
                text=WORKFLOW_PRECEDENCE_QUESTION,
            )
        ]
        previous_output = neorev.format_output(previous_hunks, [])

        bitmap_hunks = neorev.parse_diff(TWO_HUNK_DIFF)
        bitmap_hunks[0].approved = True
        conflicting_bitmap = neorev.encode_approved_bitmap(bitmap_hunks)
        previous_output = re.sub(
            r"# neorev:\S+",
            f"# neorev:{conflicting_bitmap}",
            previous_output,
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(previous_output)
            output_path = f.name

        try:
            run_main_with_scripted_terminal(
                TWO_HUNK_DIFF,
                output_path,
                lambda _state: None,
            )
            output = Path(output_path).read_text()
            self.assertIn("[QUESTION] hello.py", output)
            self.assertIn(WORKFLOW_PRECEDENCE_QUESTION, output)
        finally:
            os.unlink(output_path)

    def test_resume_with_stale_annotation_reports_and_keeps_bitmap(self) -> None:
        """Stale annotations are reported while bitmap approvals still resume."""
        bitmap_hunks = neorev.parse_diff(TWO_HUNK_DIFF)
        bitmap_hunks[1].approved = True
        bitmap = neorev.encode_approved_bitmap(bitmap_hunks)
        stale_output = (
            "## [CHANGE REQUESTED] stale.py @ hunk\n"
            "```diff\n"
            "@@ -99,1 +99,1 @@\n"
            "+stale\n"
            "```\n"
            "> stale note\n\n"
            f"# neorev:{bitmap}\n"
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(stale_output)
            output_path = f.name

        try:
            stderr = run_main_with_scripted_terminal(
                TWO_HUNK_DIFF,
                output_path,
                lambda _state: None,
            )
            output = Path(output_path).read_text()
            self.assertIn(WORKFLOW_STALE_MESSAGE, stderr)
            self.assertIn(WORKFLOW_ALL_CLEAR_SUMMARY, output)
            self.assertNotIn("CHANGE REQUESTED", output)
        finally:
            os.unlink(output_path)


class TestGlobalNoteLifecycle(unittest.TestCase):
    """Tests global note creation and management through key-dispatch paths."""

    def setUp(self) -> None:
        """Create a fake TTY, terminal, and baseline review state."""
        self.fake = FakeTTY()
        self.term = self.fake.make_terminal()
        self.state = neorev.ReviewState(hunks=[make_hunk()], global_notes=[])

    def tearDown(self) -> None:
        """Close the terminal and pseudo-terminal fds."""
        with contextlib.suppress(OSError):
            self.term.close()
        self.fake.close()

    def test_dispatch_gc_adds_global_question(self) -> None:
        """Pressing g then c appends a global question note."""
        with (
            patch.object(
                self.term,
                "read_key",
                return_value=GLOBAL_NOTE_ADD_QUESTION_KEY,
            ),
            patch.object(
                self.term,
                "edit_text_outside_tui",
                return_value=GLOBAL_NOTE_CREATED_TEXT,
            ),
        ):
            handled = self.term.dispatch_key(
                GLOBAL_NOTE_ADD_PREFIX,
                self.state,
                lambda: DISPATCH_REDRAW_FALSE,
            )
        self.assertTrue(handled)
        self.assertEqual(len(self.state.global_notes), 1)
        self.assertEqual(self.state.global_notes[0].kind, neorev.NoteKind.QUESTION)
        self.assertEqual(self.state.global_notes[0].text, GLOBAL_NOTE_CREATED_TEXT)

    def test_dispatch_gf_adds_global_flag(self) -> None:
        """Pressing g then f appends a global change-request note."""
        with (
            patch.object(self.term, "read_key", return_value=GLOBAL_NOTE_ADD_FLAG_KEY),
            patch.object(
                self.term,
                "edit_text_outside_tui",
                return_value=GLOBAL_NOTE_CREATED_TEXT,
            ),
        ):
            handled = self.term.dispatch_key(
                GLOBAL_NOTE_ADD_PREFIX,
                self.state,
                lambda: DISPATCH_REDRAW_FALSE,
            )
        self.assertTrue(handled)
        self.assertEqual(len(self.state.global_notes), 1)
        self.assertEqual(self.state.global_notes[0].kind, neorev.NoteKind.FLAG)
        self.assertEqual(self.state.global_notes[0].text, GLOBAL_NOTE_CREATED_TEXT)

    def test_manage_global_notes_edit_then_delete(self) -> None:
        """Global notes manager supports edit and delete in one session."""
        self.state.global_notes.append(
            neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text=GLOBAL_NOTE_CREATED_TEXT)
        )
        key_sequence = [
            GLOBAL_NOTE_EDIT_KEY,
            GLOBAL_NOTE_INDEX_KEY,
            GLOBAL_NOTE_DELETE_KEY,
            GLOBAL_NOTE_INDEX_KEY,
            GLOBAL_NOTE_EXIT_KEY,
        ]
        with (
            patch.object(self.term, "read_key", side_effect=key_sequence),
            patch.object(self.term, "render_manage_notes_screen"),
            patch.object(
                self.term,
                "edit_text_outside_tui",
                return_value=GLOBAL_NOTE_EDITED_TEXT,
            ),
            patch("tty.setraw"),
        ):
            self.term.handle_manage_notes(self.state)
        self.assertEqual(self.state.global_notes, [])


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
        hunk = make_hunk()
        bar = neorev.build_top_bar(
            hunk, 0, [hunk], [], term_width=NARROW_PROGRESS_WIDTH
        )
        visible = neorev.visible_len(bar)
        self.assertLessEqual(visible, NARROW_PROGRESS_WIDTH)

    def test_no_truncation_without_width(self) -> None:
        """Top bar is not truncated when term_width is 0 (default)."""
        hunk = make_hunk()
        bar = neorev.build_top_bar(hunk, 0, [hunk], [], term_width=0)
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


class TestLineTargetMapping(unittest.TestCase):
    """Tests for parse_display_lines line-target mapping."""

    def test_added_line_target(self) -> None:
        """Verify parse_display_lines creates a LineTarget('+', N) for added lines."""
        hunks = neorev.parse_diff(SIMPLE_DIFF)
        hunk = hunks[0]
        added = [
            dl for dl in hunk.display_lines if dl.kind is neorev.DisplayLineKind.ADDED
        ]
        self.assertTrue(len(added) > 0)
        for dl in added:
            self.assertIsNotNone(dl.target)
            self.assertIsInstance(dl.target, neorev.LineTarget)
            target = dl.target
            self.assertEqual(target.side, neorev.LineSide.ADDED)

    def test_removed_line_target(self) -> None:
        """Verify parse_display_lines creates a LineTarget('-', N) for removed lines."""
        diff = (
            "diff --git a/f.py b/f.py\n"
            "--- a/f.py\n+++ b/f.py\n"
            "@@ -1,2 +1,1 @@\n"
            "-old line\n"
            " kept\n"
        )
        hunks = neorev.parse_diff(diff)
        hunk = hunks[0]
        removed = [
            dl for dl in hunk.display_lines if dl.kind is neorev.DisplayLineKind.REMOVED
        ]
        self.assertTrue(len(removed) > 0)
        for dl in removed:
            self.assertIsNotNone(dl.target)
            self.assertIsInstance(dl.target, neorev.LineTarget)
            target = dl.target
            self.assertEqual(target.side, neorev.LineSide.REMOVED)
            self.assertEqual(target.line_number, REMOVED_LINE_NUMBER)

    def test_context_line_policy(self) -> None:
        """Verify context lines have target=None (not selectable)."""
        hunks = neorev.parse_diff(SIMPLE_DIFF)
        hunk = hunks[0]
        context = [
            dl for dl in hunk.display_lines if dl.kind is neorev.DisplayLineKind.CONTEXT
        ]
        self.assertTrue(len(context) > 0)
        for dl in context:
            self.assertIsNone(dl.target)


class TestFormatOutputTargetHeaders(unittest.TestCase):
    """Tests for format_output note target headers."""

    def test_hunk_target_header(self) -> None:
        """Verify output header contains '@ hunk' for hunk-scoped notes."""
        hunk = make_hunk(
            notes=[
                neorev.HunkNote(
                    kind=neorev.NoteKind.FLAG,
                    target=neorev.HunkTarget(),
                    text="fix it",
                )
            ],
        )
        output = neorev.format_output([hunk], [])
        self.assertIn("@ hunk", output)

    def test_line_target_header_plus(self) -> None:
        """Verify output header contains '@ +N' for added-line notes."""
        target = neorev.LineTarget(
            side=neorev.LineSide.ADDED, line_number=ADDED_LINE_NUMBER
        )
        hunk = make_hunk(
            notes=[
                neorev.HunkNote(
                    kind=neorev.NoteKind.FLAG,
                    target=target,
                    text="fix it",
                )
            ],
        )
        output = neorev.format_output([hunk], [])
        self.assertIn(f"@ +{ADDED_LINE_NUMBER}", output)

    def test_line_target_header_minus(self) -> None:
        """Verify output header contains '@ -N' for removed-line notes."""
        target = neorev.LineTarget(
            side=neorev.LineSide.REMOVED, line_number=REMOVED_LINE_NUMBER
        )
        hunk = make_hunk(
            notes=[
                neorev.HunkNote(
                    kind=neorev.NoteKind.QUESTION,
                    target=target,
                    text="why remove?",
                )
            ],
        )
        output = neorev.format_output([hunk], [])
        self.assertIn(f"@ -{REMOVED_LINE_NUMBER}", output)


class TestParsePreviousReview(unittest.TestCase):
    """Tests for loading previous review output with line-target and global notes."""

    def test_parse_line_target_note(self) -> None:
        """Load output with line target note '@ +42' and verify it parses correctly."""
        target = neorev.LineTarget(
            side=neorev.LineSide.ADDED,
            line_number=LINE_TARGET_NOTE_LINE,
        )
        hunk = make_hunk(
            file_path="target.py",
            notes=[
                neorev.HunkNote(
                    kind=neorev.NoteKind.FLAG,
                    target=target,
                    text=LINE_TARGET_NOTE_TEXT,
                )
            ],
        )
        output = neorev.format_output([hunk], [])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(output)
            path = f.name

        try:
            annotations, _, _ = neorev.load_previous_review(path)
            key = ("target.py", hunk.range_line, target)
            self.assertIn(key, annotations)
            kind, comment = annotations[key]
            self.assertEqual(kind, neorev.NoteKind.FLAG)
            self.assertEqual(comment, LINE_TARGET_NOTE_TEXT)
        finally:
            os.unlink(path)

    def test_parse_global_note(self) -> None:
        """Load output with global note and verify it parses correctly."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        notes = [
            neorev.GlobalNote(
                kind=neorev.NoteKind.QUESTION, text=GLOBAL_PARSE_NOTE_TEXT
            )
        ]
        output = neorev.format_output(hunks, notes)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(output)
            path = f.name

        try:
            _, loaded_notes, _ = neorev.load_previous_review(path)
            self.assertEqual(len(loaded_notes), 1)
            self.assertEqual(loaded_notes[0].kind, neorev.NoteKind.QUESTION)
            self.assertEqual(loaded_notes[0].text, GLOBAL_PARSE_NOTE_TEXT)
        finally:
            os.unlink(path)


class TestApplyPreviousReview(unittest.TestCase):
    """Tests for applying previous review annotations with line targets."""

    def test_match_by_file_range_and_target(self) -> None:
        """Apply a line-target annotation and verify it creates the right note."""
        hunks = [make_hunk(file_path="x.py")]
        target = neorev.LineTarget(
            side=neorev.LineSide.ADDED, line_number=ADDED_LINE_NUMBER
        )
        annotations: dict[
            tuple[str, str, neorev.NoteTarget], tuple[neorev.NoteKind, str]
        ] = {
            ("x.py", hunks[0].range_line, target): (
                neorev.NoteKind.FLAG,
                LINE_TARGET_APPLY_TEXT,
            ),
        }
        matched = neorev.apply_previous_review(hunks, annotations)
        self.assertEqual(matched, 1)
        self.assertEqual(len(hunks[0].notes), 1)
        note = hunks[0].notes[0]
        self.assertEqual(note.kind, neorev.NoteKind.FLAG)
        self.assertEqual(note.text, LINE_TARGET_APPLY_TEXT)
        self.assertEqual(note.target, target)


class TestDispatchKeys(unittest.TestCase):
    """Tests for specific dispatch_key behaviors."""

    def setUp(self) -> None:
        """Create a fake TTY, Terminal, and a two-hunk state."""
        self.fake = FakeTTY()
        self.term = self.fake.make_terminal()
        self.hunks = [make_hunk(file_path="a.py"), make_hunk(file_path="b.py")]
        self.state = neorev.ReviewState(hunks=self.hunks, global_notes=[])

    def redraw(self) -> None:
        """Dummy redraw callback."""

    def tearDown(self) -> None:
        """Restore terminal state and close the pty."""
        with contextlib.suppress(OSError):
            self.term.close()
        self.fake.close()

    def test_m_opens_note_manager(self) -> None:
        """Pressing 'm' dispatches to handle_manage_notes and requests redraw."""
        with patch.object(self.term, "handle_manage_notes"):
            result = self.term.dispatch_key("m", self.state, self.redraw)
        self.assertTrue(result)

    def test_g_no_longer_manages_notes(self) -> None:
        """Pressing 'G' should return False (not handled)."""
        result = self.term.dispatch_key("G", self.state, self.redraw)
        self.assertFalse(result)


class TestNoteMutation(unittest.TestCase):
    """Tests for note mutation helpers."""

    def test_empty_edit_deletes_note(self) -> None:
        """Upsert then remove on empty text verifies note is gone."""
        target = neorev.HunkTarget()
        notes: list[neorev.HunkNote] = []
        neorev.upsert_note(notes, neorev.NoteKind.FLAG, target, UPSERT_NOTE_TEXT)
        self.assertEqual(len(notes), 1)
        neorev.remove_note_for_target(notes, target)
        self.assertEqual(len(notes), 0)


class TestNoteTargetRoundTrip(unittest.TestCase):
    """Tests for format_note_target and parse_note_target round-trip."""

    def test_hunk_target_round_trip(self) -> None:
        """Serialize and parse a HunkTarget back to an equal value."""
        target = neorev.HunkTarget()
        serialized = neorev.format_note_target(target)
        parsed = neorev.parse_note_target(serialized)
        self.assertEqual(parsed, target)

    def test_line_target_added_round_trip(self) -> None:
        """Serialize and parse a LineTarget('+', N) back to an equal value."""
        target = neorev.LineTarget(
            side=neorev.LineSide.ADDED, line_number=LINE_TARGET_NOTE_LINE
        )
        serialized = neorev.format_note_target(target)
        parsed = neorev.parse_note_target(serialized)
        self.assertEqual(parsed, target)

    def test_line_target_removed_round_trip(self) -> None:
        """Serialize and parse a LineTarget('-', N) back to an equal value."""
        target = neorev.LineTarget(
            side=neorev.LineSide.REMOVED, line_number=REMOVED_LINE_NUMBER
        )
        serialized = neorev.format_note_target(target)
        parsed = neorev.parse_note_target(serialized)
        self.assertEqual(parsed, target)

    def test_parse_invalid_returns_none(self) -> None:
        """Parse an invalid target string and verify it returns None."""
        self.assertIsNone(neorev.parse_note_target("bogus"))

    def test_parse_malformed_line_number_returns_none(self) -> None:
        """Parse '+abc' and verify it returns None."""
        self.assertIsNone(neorev.parse_note_target("+abc"))


class TestNoteAccessHelpers(unittest.TestCase):
    """Tests for get_note_for_target, upsert_note, and remove_note_for_target."""

    def test_get_note_for_target_found(self) -> None:
        """Find an existing note by its target."""
        target = neorev.HunkTarget()
        note = neorev.HunkNote(kind=neorev.NoteKind.FLAG, target=target, text="hello")
        result = neorev.get_note_for_target([note], target)
        self.assertIs(result, note)

    def test_get_note_for_target_not_found(self) -> None:
        """Return None when no note matches the target."""
        target = neorev.HunkTarget()
        other = neorev.LineTarget(
            side=neorev.LineSide.ADDED, line_number=ADDED_LINE_NUMBER
        )
        note = neorev.HunkNote(kind=neorev.NoteKind.FLAG, target=target, text="hello")
        result = neorev.get_note_for_target([note], other)
        self.assertIsNone(result)

    def test_upsert_note_insert(self) -> None:
        """Upsert into an empty list appends a new note."""
        notes: list[neorev.HunkNote] = []
        target = neorev.HunkTarget()
        neorev.upsert_note(notes, neorev.NoteKind.FLAG, target, UPSERT_NOTE_TEXT)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].text, UPSERT_NOTE_TEXT)

    def test_upsert_note_update(self) -> None:
        """Upsert on an existing target replaces the note."""
        notes: list[neorev.HunkNote] = []
        target = neorev.HunkTarget()
        neorev.upsert_note(notes, neorev.NoteKind.FLAG, target, UPSERT_NOTE_TEXT)
        neorev.upsert_note(
            notes, neorev.NoteKind.QUESTION, target, UPSERT_NOTE_UPDATED_TEXT
        )
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].text, UPSERT_NOTE_UPDATED_TEXT)
        self.assertEqual(notes[0].kind, neorev.NoteKind.QUESTION)

    def test_remove_note_for_target_present(self) -> None:
        """Remove a note matching the target."""
        target = neorev.HunkTarget()
        notes = [
            neorev.HunkNote(
                kind=neorev.NoteKind.FLAG, target=target, text=UPSERT_NOTE_TEXT
            )
        ]
        neorev.remove_note_for_target(notes, target)
        self.assertEqual(len(notes), 0)

    def test_remove_note_for_target_absent(self) -> None:
        """Remove on a missing target leaves the list unchanged."""
        target = neorev.HunkTarget()
        other = neorev.LineTarget(
            side=neorev.LineSide.ADDED, line_number=ADDED_LINE_NUMBER
        )
        notes = [
            neorev.HunkNote(
                kind=neorev.NoteKind.FLAG, target=target, text=UPSERT_NOTE_TEXT
            )
        ]
        neorev.remove_note_for_target(notes, other)
        self.assertEqual(len(notes), 1)


class TestHunkStatusHelpers(unittest.TestCase):
    """Tests for hunk_summary_status and hunk_is_handled."""

    def test_hunk_summary_status_approved(self) -> None:
        """Return 'approved' for an approved hunk."""
        hunk = make_hunk(approved=True)
        self.assertEqual(neorev.hunk_summary_status(hunk), neorev.Status.APPROVED)

    def test_hunk_summary_status_flag(self) -> None:
        """Return 'flag' when a flag note is present."""
        hunk = make_hunk(
            notes=[
                neorev.HunkNote(
                    kind=neorev.NoteKind.FLAG,
                    target=neorev.HunkTarget(),
                    text="fix",
                )
            ],
        )
        self.assertEqual(neorev.hunk_summary_status(hunk), neorev.Status.FLAG)

    def test_hunk_summary_status_question(self) -> None:
        """Return 'question' when a question note is present."""
        hunk = make_hunk(
            notes=[
                neorev.HunkNote(
                    kind=neorev.NoteKind.QUESTION,
                    target=neorev.HunkTarget(),
                    text="why?",
                )
            ],
        )
        self.assertEqual(neorev.hunk_summary_status(hunk), neorev.Status.QUESTION)

    def test_hunk_summary_status_none(self) -> None:
        """Return None for a hunk with no status, notes, or approval."""
        hunk = make_hunk()
        self.assertIsNone(neorev.hunk_summary_status(hunk))

    def test_hunk_is_handled_approved(self) -> None:
        """An approved hunk is handled."""
        hunk = make_hunk(approved=True)
        self.assertTrue(neorev.hunk_is_handled(hunk))

    def test_hunk_is_handled_with_notes(self) -> None:
        """A hunk with notes is handled."""
        hunk = make_hunk(
            notes=[
                neorev.HunkNote(
                    kind=neorev.NoteKind.FLAG,
                    target=neorev.HunkTarget(),
                    text="fix",
                )
            ],
        )
        self.assertTrue(neorev.hunk_is_handled(hunk))

    def test_hunk_is_handled_with_status(self) -> None:
        """A hunk with a legacy status is handled."""
        hunk = make_hunk(status=neorev.Status.FLAG)
        self.assertTrue(neorev.hunk_is_handled(hunk))

    def test_hunk_is_not_handled(self) -> None:
        """A bare hunk with no status, notes, or approval is not handled."""
        hunk = make_hunk()
        self.assertFalse(neorev.hunk_is_handled(hunk))


class TestLinePickerResize(unittest.TestCase):
    """Tests for terminal resize handling in pick_line_target."""

    def setUp(self) -> None:
        """Create a fake TTY, Terminal, and wakeup pipe."""
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

    def test_resize_refreshes_geometry_during_line_pick(self) -> None:
        """A resize signal during line selection refreshes terminal geometry."""
        hunk = neorev.parse_diff(SIMPLE_DIFF)[0]
        state = neorev.ReviewState(hunks=[hunk], global_notes=[])
        self.term.wakeup_read_fd = self.wakeup_r

        new_width = TERM_WIDTH + RESIZE_WIDTH_DELTA
        winsize = struct.pack(
            WINSIZE_FORMAT,
            TERM_HEIGHT,
            new_width,
            TERM_PIXEL_SIZE,
            TERM_PIXEL_SIZE,
        )

        tty.setraw(self.fake.slave_fd)
        # Send resize signal then Enter to select the line.
        os.write(self.wakeup_w, SIGWINCH_BYTE)
        fcntl.ioctl(self.fake.slave_fd, termios.TIOCSWINSZ, winsize)
        self.fake.inject_keys(b"\r")

        with patch.object(self.term, "write"):
            self.term.pick_line_target(state)

        self.assertEqual(self.term.width, new_width)

    def test_resize_rerenders_delta_at_new_width(self) -> None:
        """A resize during line selection re-renders delta output at the new width."""
        hunk = neorev.parse_diff(SIMPLE_DIFF)[0]
        state = neorev.ReviewState(hunks=[hunk], global_notes=[])
        self.term.wakeup_read_fd = self.wakeup_r

        new_width = TERM_WIDTH + RESIZE_WIDTH_DELTA
        winsize = struct.pack(
            WINSIZE_FORMAT,
            TERM_HEIGHT,
            new_width,
            TERM_PIXEL_SIZE,
            TERM_PIXEL_SIZE,
        )

        tty.setraw(self.fake.slave_fd)
        os.write(self.wakeup_w, SIGWINCH_BYTE)
        fcntl.ioctl(self.fake.slave_fd, termios.TIOCSWINSZ, winsize)
        self.fake.inject_keys(b"\r")

        render_widths: list[int] = []
        original_render = neorev.render_through_delta

        def tracking_render(raw: str, width: int = 0) -> bytes:
            """Track the width argument passed to render_through_delta."""
            render_widths.append(width)
            return original_render(raw, width=width)

        with (
            patch.object(self.term, "write"),
            patch("neorev.render_through_delta", side_effect=tracking_render),
        ):
            self.term.pick_line_target(state)

        self.assertIn(new_width, render_widths)


class TestLinePickerScrollFollowsCursor(unittest.TestCase):
    """Ensure the line picker scrolls to keep the selected line visible."""

    def setUp(self) -> None:
        """Create a fake TTY and Terminal."""
        self.fake = FakeTTY()
        self.term = self.fake.make_terminal()

    def tearDown(self) -> None:
        """Close all fds."""
        with contextlib.suppress(OSError):
            self.term.close()
        self.fake.close()

    def test_cursor_at_bottom_stays_visible(self) -> None:
        """Moving the cursor down keeps it within the visible viewport."""
        body = "\n".join(f"+line {i}" for i in range(LINE_PICKER_MANY_LINES))
        range_line = f"@@ -0,0 +1,{LINE_PICKER_MANY_LINES} @@"
        raw = f"diff --git a/test.py b/test.py\n{range_line}\n{body}"
        hunk = neorev.Hunk(
            file_header="diff --git a/test.py b/test.py",
            range_line=range_line,
            body=body,
            raw=raw,
            file_path="test.py",
            start_line=1,
            display_lines=neorev.parse_display_lines(range_line, body),
        )
        state = neorev.ReviewState(hunks=[hunk], global_notes=[])
        selectable = [dl for dl in hunk.display_lines if dl.target is not None]
        delta_output = neorev.render_through_delta(hunk.raw, width=self.term.width)

        # Place cursor at the last selectable line (requires scrolling).
        cursor = len(selectable) - 1

        with patch.object(self.term, "write"):
            scroll = self.term.render_line_picker(
                state, selectable, cursor, delta_output, 0
            )

        viewport = neorev.compute_diff_viewport(
            len(neorev.build_display_lines(delta_output, self.term.width)),
            self.term.height,
            scroll,
        )
        cursor_idx = neorev.find_display_line_index(
            hunk.display_lines, selectable[cursor]
        )
        self.assertIsNotNone(cursor_idx)
        self.assertGreaterEqual(cursor_idx, viewport.scroll_offset)
        self.assertLess(
            cursor_idx,
            viewport.scroll_offset + viewport.visible_line_count,
        )


CENTERED_SNIPPET_LINE_COUNT = 20
CENTERED_SNIPPET_TARGET_LINE = 10


class TestSnippetCenteredOnTargetLine(unittest.TestCase):
    """Tests for diff snippet centering on the targeted line in review output."""

    def build_long_hunk_with_line_note(
        self,
        target_index: int,
    ) -> neorev.Hunk:
        """Build a hunk with many added lines and a note on *target_index*."""
        body = "\n".join(f"+line {i}" for i in range(CENTERED_SNIPPET_LINE_COUNT))
        target = neorev.LineTarget(
            side=neorev.LineSide.ADDED,
            line_number=target_index + 1,
        )
        return make_hunk(
            body=body,
            start_line=1,
            notes=[
                neorev.HunkNote(
                    kind=neorev.NoteKind.FLAG,
                    target=target,
                    text="fix this line",
                )
            ],
        )

    def test_snippet_centers_on_target_line(self) -> None:
        """When a note targets a specific line, the snippet is centered on it."""
        hunk = self.build_long_hunk_with_line_note(
            CENTERED_SNIPPET_TARGET_LINE,
        )
        output = neorev.format_output([hunk], [])
        # The targeted line should appear in the snippet
        self.assertIn(f"+line {CENTERED_SNIPPET_TARGET_LINE}", output)

    def test_snippet_does_not_center_for_hunk_note(self) -> None:
        """Hunk-scoped notes use the default first/last trimming."""
        body = "\n".join(f"+line {i}" for i in range(CENTERED_SNIPPET_LINE_COUNT))
        hunk = make_hunk(
            body=body,
            start_line=1,
            notes=[
                neorev.HunkNote(
                    kind=neorev.NoteKind.FLAG,
                    target=neorev.HunkTarget(),
                    text="fix it",
                )
            ],
        )
        output = neorev.format_output([hunk], [])
        # Default trimming: first 5 and last 5 lines present, middle absent
        self.assertIn("+line 0", output)
        self.assertIn(f"+line {CENTERED_SNIPPET_LINE_COUNT - 1}", output)
        self.assertIn("# ...", output)

    def test_snippet_target_near_start_clamps(self) -> None:
        """A target near the start doesn't go out of bounds."""
        hunk = self.build_long_hunk_with_line_note(1)
        output = neorev.format_output([hunk], [])
        self.assertIn("+line 1", output)
        self.assertIn("+line 0", output)

    def test_snippet_target_near_end_clamps(self) -> None:
        """A target near the end doesn't go out of bounds."""
        last = CENTERED_SNIPPET_LINE_COUNT - 1
        hunk = self.build_long_hunk_with_line_note(last)
        output = neorev.format_output([hunk], [])
        self.assertIn(f"+line {last}", output)
        self.assertIn(f"+line {CENTERED_SNIPPET_LINE_COUNT - 2}", output)


if __name__ == "__main__":
    unittest.main()
