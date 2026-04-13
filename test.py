#!/usr/bin/env python3
"""Tests for neorev — interactive diff review tool."""

import base64
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
from unittest.mock import MagicMock, patch

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
MANY_APPROVED_COUNT = 20
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
MOCK_OUTPUT_PATH = "/mock/output/review.md"

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
    range_line: str = "",
) -> neorev.Hunk:
    """Create a Hunk with sensible defaults for testing."""
    if not range_line:
        range_line = f"@@ -1,3 +{start_line},4 @@"
    hunk = neorev.Hunk(
        file_header=f"diff --git a/{file_path} b/{file_path}",
        range_line=range_line,
        body=body,
        raw=f"diff --git a/{file_path} b/{file_path}\n{range_line}\n{body}",
        file_path=file_path,
        start_line=start_line,
        display_lines=neorev.parse_display_lines(range_line, body),
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
    extra_args: list[str] | None = None,
) -> str:
    """Run neorev.main() with a fake terminal script and return captured stderr."""
    stderr = io.StringIO()
    argv = ["neorev", *(extra_args or []), "-o", output_path]
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
        """short_location falls back to range_line when file_path is absent."""
        hunk = make_hunk()
        hunk.file_path = None
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


class TestApprovedHashes(unittest.TestCase):
    """Tests for encode_approved_hashes / decode_approved_hashes round-trip."""

    def test_round_trip_mixed(self) -> None:
        """Encoding then decoding recovers exactly the approved hunks."""
        hunks = [
            make_hunk(file_path="a.py", body="+line a", status=neorev.Status.APPROVED),
            make_hunk(file_path="b.py", body="+line b"),
            make_hunk(file_path="c.py", body="+line c", status=neorev.Status.APPROVED),
        ]
        encoded = neorev.encode_approved_hashes(hunks)
        result = neorev.decode_approved_hashes(encoded)
        approved_ids = {neorev.hunk_identity_hash(h) for h in hunks if h.approved}
        self.assertEqual(result, approved_ids)

    def test_all_approved(self) -> None:
        """All-approved set round-trips correctly."""
        hunks = [
            make_hunk(
                file_path=f"f{i}.py",
                body=f"+line {i}",
                status=neorev.Status.APPROVED,
            )
            for i in range(MANY_APPROVED_COUNT)
        ]
        encoded = neorev.encode_approved_hashes(hunks)
        result = neorev.decode_approved_hashes(encoded)
        self.assertEqual(len(result), MANY_APPROVED_COUNT)

    def test_none_approved(self) -> None:
        """No approved hunks produce an empty encoded string."""
        hunks = [make_hunk(file_path="a.py", body="+x")]
        encoded = neorev.encode_approved_hashes(hunks)
        result = neorev.decode_approved_hashes(encoded)
        self.assertEqual(result, set())

    def test_empty_hunks(self) -> None:
        """Empty hunk list encodes and decodes to empty set."""
        encoded = neorev.encode_approved_hashes([])
        self.assertEqual(neorev.decode_approved_hashes(encoded), set())

    def test_single_approved(self) -> None:
        """Single approved hunk round-trips."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        encoded = neorev.encode_approved_hashes(hunks)
        result = neorev.decode_approved_hashes(encoded)
        self.assertEqual(len(result), 1)
        self.assertIn(neorev.hunk_identity_hash(hunks[0]), result)

    def test_changed_body_invalidates_approval(self) -> None:
        """A hunk whose body changed does not match the saved hash."""
        original = make_hunk(
            file_path="f.py",
            body="+old line",
            status=neorev.Status.APPROVED,
        )
        encoded = neorev.encode_approved_hashes([original])
        result = neorev.decode_approved_hashes(encoded)
        modified = make_hunk(file_path="f.py", body="+new line")
        self.assertNotIn(neorev.hunk_identity_hash(modified), result)

    def test_changed_file_path_invalidates_approval(self) -> None:
        """A hunk whose file path changed does not match the saved hash."""
        original = make_hunk(
            file_path="old.py",
            body="+same",
            status=neorev.Status.APPROVED,
        )
        encoded = neorev.encode_approved_hashes([original])
        result = neorev.decode_approved_hashes(encoded)
        moved = make_hunk(file_path="new.py", body="+same")
        self.assertNotIn(neorev.hunk_identity_hash(moved), result)

    def test_changed_range_line_invalidates_approval(self) -> None:
        """A hunk whose range line changed does not match the saved hash."""
        original = make_hunk(
            file_path="f.py",
            body="+same",
            range_line="@@ -1,3 +1,4 @@",
            status=neorev.Status.APPROVED,
        )
        encoded = neorev.encode_approved_hashes([original])
        result = neorev.decode_approved_hashes(encoded)
        shifted = make_hunk(
            file_path="f.py",
            body="+same",
            range_line="@@ -5,3 +5,4 @@",
        )
        self.assertNotIn(neorev.hunk_identity_hash(shifted), result)

    def test_invalid_base64(self) -> None:
        """Invalid base64 returns empty set."""
        self.assertEqual(neorev.decode_approved_hashes("!!!bad"), set())

    def test_truncated_hash_bytes(self) -> None:
        """Non-multiple-of-digest-size base64 returns empty set."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        encoded = neorev.encode_approved_hashes(hunks)
        raw = base64.b64decode(encoded)
        truncated = base64.b64encode(raw[:-1]).decode("ascii")
        self.assertEqual(neorev.decode_approved_hashes(truncated), set())

    def test_shifted_hash_bytes(self) -> None:
        """Prepending a byte shifts all hashes, invalidating every entry."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        encoded = neorev.encode_approved_hashes(hunks)
        raw = base64.b64decode(encoded)
        shifted = base64.b64encode(b"\x00" + raw).decode("ascii")
        result = neorev.decode_approved_hashes(shifted)
        self.assertNotIn(neorev.hunk_identity_hash(hunks[0]), result)

    def test_extra_hash_bytes_appended(self) -> None:
        """Extra digest-sized bytes add a spurious hash; originals survive."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        encoded = neorev.encode_approved_hashes(hunks)
        raw = base64.b64decode(encoded)
        extended = base64.b64encode(raw + b"\xff" * neorev.APPROVAL_HASH_BYTES).decode(
            "ascii"
        )
        result = neorev.decode_approved_hashes(extended)
        self.assertIn(neorev.hunk_identity_hash(hunks[0]), result)

    def test_empty_string_returns_empty_set(self) -> None:
        """An empty encoded string decodes to an empty set."""
        self.assertEqual(neorev.decode_approved_hashes(""), set())

    def test_identity_hash_deterministic(self) -> None:
        """The same hunk always produces the same identity hash."""
        hunk = make_hunk(file_path="f.py", body="+x")
        self.assertEqual(
            neorev.hunk_identity_hash(hunk),
            neorev.hunk_identity_hash(hunk),
        )

    def test_identity_hash_differs_for_different_hunks(self) -> None:
        """Two hunks with different content produce different hashes."""
        h1 = make_hunk(file_path="f.py", body="+a")
        h2 = make_hunk(file_path="f.py", body="+b")
        self.assertNotEqual(
            neorev.hunk_identity_hash(h1),
            neorev.hunk_identity_hash(h2),
        )


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
        self.assertIn("`global`", output)
        self.assertIn("add tests", output)

    def test_long_hunk_body_trimmed(self) -> None:
        """Hunk bodies exceeding HUNK_BODY_MAX_LINES are trimmed."""
        long_body = "\n".join(f"+line {i}" for i in range(LONG_BODY_LINE_COUNT))
        hunks = [
            make_hunk(body=long_body, status=neorev.Status.FLAG, comment="too long")
        ]
        output = neorev.format_output(hunks, [])
        self.assertIn("# ...", output)

    def test_footer_present_in_output(self) -> None:
        """Output always contains a neorev approved-hashes footer comment."""
        hunks = [make_hunk(status=neorev.Status.FLAG, comment="x")]
        output = neorev.format_output(hunks, [])
        self.assertIn("<!-- neorev: approved-hashes=", output)

    def test_no_status_hunks(self) -> None:
        """Hunks with no status and no actionable items get 'all clear'."""
        hunks = [make_hunk(), make_hunk()]
        output = neorev.format_output(hunks, [])
        self.assertIn("all clear", output)
        self.assertIn("0/2 hunks approved", output)
        self.assertIn("<!-- neorev: approved-hashes=", output)

    def test_global_note_question_label(self) -> None:
        """A global question note section header uses QUESTION label."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        notes = [
            neorev.GlobalNote(kind=neorev.NoteKind.QUESTION, text="why this approach?")
        ]
        output = neorev.format_output(hunks, notes)
        self.assertIn("[QUESTION] `global`", output)
        self.assertNotIn("[CHANGE REQUESTED] `global`", output)

    def test_multiline_comment_preserved(self) -> None:
        """Each line of a multi-line comment appears as plain text."""
        hunks = [
            make_hunk(
                status=neorev.Status.FLAG, comment="line one\nline two\nline three"
            )
        ]
        output = neorev.format_output(hunks, [])
        self.assertIn("line one\nline two\nline three", output)

    def test_body_exactly_max_lines_not_trimmed(self) -> None:
        """A body with exactly HUNK_BODY_MAX_LINES lines is not trimmed."""
        body = "\n".join(f"+line {i}" for i in range(neorev.HUNK_BODY_MAX_LINES))
        hunks = [make_hunk(body=body, status=neorev.Status.FLAG, comment="ok")]
        output = neorev.format_output(hunks, [])
        match = re.search(r"```diff\n(.*?)```", output, re.DOTALL)
        self.assertIsNotNone(match)
        if match:
            self.assertNotIn("# ...", match.group(1))

    def test_body_one_over_max_lines_trimmed(self) -> None:
        """A body with HUNK_BODY_MAX_LINES + 1 lines is trimmed."""
        body = "\n".join(f"+line {i}" for i in range(neorev.HUNK_BODY_MAX_LINES + 1))
        hunks = [make_hunk(body=body, status=neorev.Status.FLAG, comment="too long")]
        output = neorev.format_output(hunks, [])
        match = re.search(r"```diff\n(.*?)```", output, re.DOTALL)
        self.assertIsNotNone(match)
        if match:
            self.assertIn("# ...", match.group(1))

    def test_diff_source_in_preamble(self) -> None:
        """The diff source appears in the header when provided."""
        hunks = [make_hunk(status=neorev.Status.FLAG, comment="fix")]
        output = neorev.format_output(hunks, [], diff_source="jj show abc")
        self.assertIn("- Reviewed diff: `jj show abc`\n", output)

    def test_diff_source_in_all_clear(self) -> None:
        """The diff source appears in the all-clear output."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        output = neorev.format_output(hunks, [], diff_source="git show HEAD~1")
        self.assertIn("# Reviewed diff: `git show HEAD~1`\n", output)

    def test_no_diff_source_by_default(self) -> None:
        """No diff source line when the parameter is empty."""
        hunks = [make_hunk(status=neorev.Status.FLAG, comment="fix")]
        output = neorev.format_output(hunks, [])
        self.assertNotIn("# Reviewed diff:", output)

    def test_no_diff_source_in_all_clear_by_default(self) -> None:
        """No diff source line in all-clear output when not provided."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        output = neorev.format_output(hunks, [])
        self.assertNotIn("# Reviewed diff:", output)


class TestLoadPreviousReview(unittest.TestCase):
    """Tests for load_previous_review, extract_comment_lines, apply_previous_review."""

    def setUp(self) -> None:
        """Create a temporary directory for review files."""
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        """Remove the temporary directory."""
        self.tmpdir.cleanup()

    def tmp_path(self, name: str = "review.md") -> str:
        """Return a path inside the temporary directory."""
        return str(Path(self.tmpdir.name) / name)

    def test_nonexistent_file(self) -> None:
        """Loading a missing file returns empty results."""
        annotations, notes, hashes_encoded = neorev.load_previous_review(
            "/no/such/file",
        )
        self.assertEqual(annotations, {})
        self.assertEqual(notes, [])
        self.assertIsNone(hashes_encoded)

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

        path = self.tmp_path()
        with open(path, "w") as f:
            f.write(output)

        annotations, _, _ = neorev.load_previous_review(path)
        key = ("a.py", hunks[0].range_line, neorev.HunkTarget())
        self.assertIn(key, annotations)
        saved = annotations[key]
        self.assertEqual(saved.kind, neorev.NoteKind.FLAG)
        self.assertEqual(saved.text, ROUND_TRIP_COMMENT_TEXT)
        self.assertIsNotNone(saved.anchor)

    def test_extract_comment_lines(self) -> None:
        """extract_comment_lines pulls plain text after the heading."""
        section = "[CHANGE REQUESTED] `foo.py @ hunk`\n\nline one\nline two\n"
        self.assertEqual(neorev.extract_comment_lines(section), "line one\nline two")

    def test_extract_comment_with_diff_block(self) -> None:
        """Text after a diff block is extracted, diff block is excluded."""
        section = (
            "[CHANGE REQUESTED] `foo.py @ hunk`\n\n"
            "```diff\n@@ -1,2 +1,3 @@\n+added\n```\n\n"
            "fix this\n"
        )
        self.assertEqual(neorev.extract_comment_lines(section), "fix this")

    def test_extract_comment_with_markdown_headings(self) -> None:
        """Review text containing ### headings is preserved."""
        section = (
            "[QUESTION] `foo.py @ hunk`\n\n"
            "```diff\n@@ -1,2 +1,3 @@\n+added\n```\n\n"
            "### Why this?\n\nBecause reasons.\n"
        )
        result = neorev.extract_comment_lines(section)
        self.assertIn("### Why this?", result)
        self.assertIn("Because reasons.", result)

    def test_extract_comment_with_special_characters(self) -> None:
        """Review text with backticks, brackets, and unicode is preserved."""
        section = (
            "[CHANGE REQUESTED] `bar.py @ hunk`\n\n"
            "Use `foo()` instead of `bar()` — see [docs](url).\n"
            "Also: émojis 🎉 and <angle> brackets.\n"
        )
        result = neorev.extract_comment_lines(section)
        self.assertIn("`foo()`", result)
        self.assertIn("— see [docs](url)", result)
        self.assertIn("🎉", result)
        self.assertIn("<angle>", result)

    def test_extract_comment_with_html_comment(self) -> None:
        """The neorev footer HTML comment is stripped from the body."""
        section = (
            "[CHANGE REQUESTED] `baz.py @ hunk`\n\n"
            "fix the bug\n\n"
            "<!-- neorev: approved-hashes=AQ== -->\n"
        )
        result = neorev.extract_comment_lines(section)
        self.assertEqual(result, "fix the bug")

    def test_apply_previous_review(self) -> None:
        """apply_previous_review sets notes on matching hunks."""
        hunks = [make_hunk(file_path="x.py")]
        target = neorev.HunkTarget()
        annotations = {
            ("x.py", hunks[0].range_line, target): neorev.SavedAnnotation(
                kind=neorev.NoteKind.QUESTION,
                text="why?",
                anchor=neorev.compute_note_anchor(hunks[0].body, target),
            ),
        }
        result = neorev.apply_previous_review(hunks, annotations)
        self.assertEqual(result.matched, 1)
        self.assertEqual(len(hunks[0].notes), 1)
        self.assertEqual(hunks[0].notes[0].kind, neorev.NoteKind.QUESTION)
        self.assertEqual(hunks[0].notes[0].text, "why?")

    def test_apply_no_match(self) -> None:
        """Unmatched annotations don't alter hunks."""
        hunks = [make_hunk(file_path="x.py")]
        target = neorev.HunkTarget()
        annotations = {
            ("other.py", "@@ -1 +1 @@", target): neorev.SavedAnnotation(
                kind=neorev.NoteKind.FLAG,
                text="n/a",
                anchor=neorev.compute_note_anchor("body", target),
            ),
        }
        result = neorev.apply_previous_review(hunks, annotations)
        self.assertEqual(result.matched, 0)
        self.assertEqual(result.stale_unmatched, 1)
        self.assertEqual(hunks[0].notes, [])

    def test_global_notes_round_trip(self) -> None:
        """Global notes survive format_output → load_previous_review."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        notes = [
            neorev.GlobalNote(kind=neorev.NoteKind.QUESTION, text="overall design?")
        ]
        output = neorev.format_output(hunks, notes)

        path = self.tmp_path()
        with open(path, "w") as f:
            f.write(output)

        _, loaded_notes, _ = neorev.load_previous_review(path)
        self.assertEqual(len(loaded_notes), 1)
        self.assertEqual(loaded_notes[0].kind, neorev.NoteKind.QUESTION)
        self.assertIn("overall design", loaded_notes[0].text)

    def test_load_empty_file(self) -> None:
        """An existing but empty file returns empty results."""
        path = self.tmp_path()
        with open(path, "w") as f:
            f.write("   \n\n")

        annotations, notes, hashes_encoded = neorev.load_previous_review(path)
        self.assertEqual(annotations, {})
        self.assertEqual(notes, [])
        self.assertIsNone(hashes_encoded)

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

        path = self.tmp_path()
        with open(path, "w") as f:
            f.write(output)

        annotations, _, _ = neorev.load_previous_review(path)
        key = ("m.py", hunks[0].range_line, neorev.HunkTarget())
        saved = annotations[key]
        self.assertIn("first line", saved.text)
        self.assertIn("second line", saved.text)
        self.assertIn("third line", saved.text)

    def test_multiple_global_notes_round_trip(self) -> None:
        """Multiple global notes of different kinds survive round-trip."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        notes = [
            neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="add tests"),
            neorev.GlobalNote(kind=neorev.NoteKind.QUESTION, text="why this design?"),
        ]
        output = neorev.format_output(hunks, notes)

        path = self.tmp_path()
        with open(path, "w") as f:
            f.write(output)

        _, loaded_notes, _ = neorev.load_previous_review(path)
        self.assertEqual(len(loaded_notes), 2)
        self.assertEqual(loaded_notes[0].kind, neorev.NoteKind.FLAG)
        self.assertEqual(loaded_notes[1].kind, neorev.NoteKind.QUESTION)

    def test_section_without_range_line_skipped(self) -> None:
        """A section header with no ```diff block is skipped gracefully."""
        content = (
            "### [CHANGE REQUESTED] `broken.py`\n\n"
            "some comment\n\n"
            "<!-- neorev: approved-hashes=AQ== -->\n"
        )
        path = self.tmp_path()
        with open(path, "w") as f:
            f.write(content)

        annotations, _, hashes_encoded = neorev.load_previous_review(path)
        self.assertEqual(annotations, {})
        self.assertIsNotNone(hashes_encoded)

    def test_apply_previous_review_multiple_matches(self) -> None:
        """Multiple hunks matching annotations all get annotated."""
        hunks = [
            make_hunk(file_path="a.py", start_line=1),
            make_hunk(file_path="b.py", start_line=5),
        ]
        target = neorev.HunkTarget()
        annotations = {
            ("a.py", hunks[0].range_line, target): neorev.SavedAnnotation(
                kind=neorev.NoteKind.FLAG,
                text="fix a",
                anchor=neorev.compute_note_anchor(hunks[0].body, target),
            ),
            ("b.py", hunks[1].range_line, target): neorev.SavedAnnotation(
                kind=neorev.NoteKind.QUESTION,
                text="why b",
                anchor=neorev.compute_note_anchor(hunks[1].body, target),
            ),
        }
        result = neorev.apply_previous_review(hunks, annotations)
        self.assertEqual(result.matched, 2)
        self.assertEqual(hunks[0].notes[0].kind, neorev.NoteKind.FLAG)
        self.assertEqual(hunks[1].notes[0].kind, neorev.NoteKind.QUESTION)


ANCHOR_BODY_ORIGINAL = "+first\n+second"
ANCHOR_BODY_CHANGED = "+first\n+second-modified"
ANCHOR_RANGE_LINE = "@@ -1,1 +1,3 @@"
ANCHOR_NOTE_TEXT = "look here"
ANCHOR_LEGACY_COMMENT = "legacy note"


class TestNoteAnchor(unittest.TestCase):
    """Tests for compute_note_anchor and anchor-based resume validation."""

    def setUp(self) -> None:
        """Create a temporary directory for review files."""
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        """Remove the temporary directory."""
        self.tmpdir.cleanup()

    def tmp_path(self, name: str = "review.md") -> str:
        """Return a path inside the temporary directory."""
        return str(Path(self.tmpdir.name) / name)

    def test_compute_note_anchor_format(self) -> None:
        """Anchor strings are an 11-character urlsafe base64 digest."""
        anchor = neorev.compute_note_anchor("body", neorev.HunkTarget())
        self.assertRegex(anchor, r"^[A-Za-z0-9_-]{11}$")

    def test_compute_note_anchor_changes_with_body(self) -> None:
        """Different hunk bodies produce different anchors for the same target."""
        target = neorev.HunkTarget()
        self.assertNotEqual(
            neorev.compute_note_anchor("body one", target),
            neorev.compute_note_anchor("body two", target),
        )

    def test_compute_note_anchor_changes_with_target(self) -> None:
        """Different targets produce different anchors for the same body."""
        body = ANCHOR_BODY_ORIGINAL
        line_anchor = neorev.compute_note_anchor(
            body,
            neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=1),
        )
        hunk_anchor = neorev.compute_note_anchor(body, neorev.HunkTarget())
        self.assertNotEqual(line_anchor, hunk_anchor)

    def test_format_output_contains_anchor_comment(self) -> None:
        """Freshly written review output contains a note-anchor comment."""
        hunk = make_hunk(
            file_path="a.py",
            body=ANCHOR_BODY_ORIGINAL,
            range_line=ANCHOR_RANGE_LINE,
            status=neorev.Status.FLAG,
            comment=ANCHOR_NOTE_TEXT,
        )
        output = neorev.format_output([hunk], [])
        self.assertRegex(output, r"<!-- neorev: note-anchor=[A-Za-z0-9_-]{11} -->")

    def test_resume_identical_diff_restores_note(self) -> None:
        """Resume on identical diff: anchored note is restored."""
        hunk = make_hunk(
            file_path="a.py",
            body=ANCHOR_BODY_ORIGINAL,
            range_line=ANCHOR_RANGE_LINE,
            status=neorev.Status.FLAG,
            comment=ANCHOR_NOTE_TEXT,
        )
        path = self.tmp_path()
        Path(path).write_text(neorev.format_output([hunk], []))

        annotations, _, _ = neorev.load_previous_review(path)
        fresh = make_hunk(
            file_path="a.py",
            body=ANCHOR_BODY_ORIGINAL,
            range_line=ANCHOR_RANGE_LINE,
        )
        result = neorev.apply_previous_review([fresh], annotations)
        self.assertEqual(result.matched, 1)
        self.assertEqual(len(fresh.notes), 1)
        self.assertEqual(fresh.notes[0].text, ANCHOR_NOTE_TEXT)

    def test_resume_changed_body_invalidates_note(self) -> None:
        """Same file/range/target but a changed hunk body drops the note."""
        original = make_hunk(
            file_path="a.py",
            body=ANCHOR_BODY_ORIGINAL,
            range_line=ANCHOR_RANGE_LINE,
            status=neorev.Status.FLAG,
            comment=ANCHOR_NOTE_TEXT,
        )
        path = self.tmp_path()
        Path(path).write_text(neorev.format_output([original], []))

        annotations, _, _ = neorev.load_previous_review(path)
        changed = make_hunk(
            file_path="a.py",
            body=ANCHOR_BODY_CHANGED,
            range_line=ANCHOR_RANGE_LINE,
        )
        result = neorev.apply_previous_review([changed], annotations)
        self.assertEqual(result.matched, 0)
        self.assertEqual(result.stale_anchor, 1)
        self.assertEqual(changed.notes, [])

    def test_resume_missing_line_target_invalidates_note(self) -> None:
        """A line-target note whose line no longer exists is dropped."""
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=2)
        original = make_hunk(
            file_path="a.py",
            body=ANCHOR_BODY_ORIGINAL,
            range_line=ANCHOR_RANGE_LINE,
            notes=[
                neorev.HunkNote(
                    kind=neorev.NoteKind.FLAG,
                    target=target,
                    text=ANCHOR_NOTE_TEXT,
                )
            ],
        )
        path = self.tmp_path()
        Path(path).write_text(neorev.format_output([original], []))

        annotations, _, _ = neorev.load_previous_review(path)
        shrunk = make_hunk(
            file_path="a.py",
            body="+first",
            range_line=ANCHOR_RANGE_LINE,
        )
        result = neorev.apply_previous_review([shrunk], annotations)
        self.assertEqual(result.matched, 0)
        self.assertEqual(result.stale_missing_target, 1)
        self.assertEqual(shrunk.notes, [])

    def test_unanchored_review_is_treated_as_anchor_mismatch(self) -> None:
        """A review file without an anchor is dropped like a hash mismatch."""
        unanchored_content = (
            "### [CHANGE REQUESTED] `a.py @ hunk`\n\n"
            "```diff\n"
            f"{ANCHOR_RANGE_LINE}\n"
            "+first\n"
            "+second\n"
            "```\n\n"
            f"{ANCHOR_LEGACY_COMMENT}\n\n"
            "<!-- neorev: approved-hashes= -->\n"
        )
        path = self.tmp_path()
        Path(path).write_text(unanchored_content)

        annotations, _, _ = neorev.load_previous_review(path)
        key = ("a.py", ANCHOR_RANGE_LINE, neorev.HunkTarget())
        self.assertIn(key, annotations)
        self.assertIsNone(annotations[key].anchor)

        hunk = make_hunk(
            file_path="a.py",
            body=ANCHOR_BODY_ORIGINAL,
            range_line=ANCHOR_RANGE_LINE,
        )
        result = neorev.apply_previous_review([hunk], annotations)
        self.assertEqual(result.matched, 0)
        self.assertEqual(result.stale_anchor, 1)
        self.assertEqual(hunk.notes, [])


class TestNavigation(unittest.TestCase):
    """Tests for navigation, approval, and hunk-finding functions."""

    def setUp(self) -> None:
        """Create a three-hunk review state."""
        self.hunks = [make_hunk() for _ in range(3)]
        self.state = neorev.ReviewState(hunks=self.hunks, global_notes=[])

    def test_navigate_down(self) -> None:
        """'j' moves to the next hunk."""
        self.assertTrue(self.state.navigate("j"))
        self.assertEqual(self.state.current_index, 1)

    def test_navigate_up(self) -> None:
        """'k' moves to the previous hunk."""
        self.state.current_index = 2
        self.assertTrue(self.state.navigate("k"))
        self.assertEqual(self.state.current_index, 1)

    def test_navigate_down_at_end(self) -> None:
        """'j' at the last hunk does nothing."""
        self.state.current_index = 2
        self.assertFalse(self.state.navigate("j"))
        self.assertEqual(self.state.current_index, 2)

    def test_navigate_up_at_start(self) -> None:
        """'k' at the first hunk does nothing."""
        self.assertFalse(self.state.navigate("k"))
        self.assertEqual(self.state.current_index, 0)

    def test_arrow_keys(self) -> None:
        """Arrow key names work like j/k."""
        with self.subTest(key="down"):
            self.state.current_index = 0
            self.state.navigate("down")
            self.assertEqual(self.state.current_index, 1)

        with self.subTest(key="up"):
            self.state.navigate("up")
            self.assertEqual(self.state.current_index, 0)

    def test_approve_toggle(self) -> None:
        """Approving then re-approving toggles the approved flag."""
        self.state.approve()
        self.assertTrue(self.hunks[0].approved)
        self.state.current_index = 0
        self.state.approve()
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
        self.state.approve()
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
        self.state.approve()
        self.assertFalse(self.hunks[0].approved)
        self.assertEqual(len(self.hunks[0].notes), 1)

    def test_approve_advances_to_next_unhandled(self) -> None:
        """After approval, cursor moves to the next unhandled hunk."""
        self.hunks[1].approved = True
        self.state.approve()
        self.assertEqual(self.state.current_index, 2)

    def test_approve_file(self) -> None:
        """Approve-file approves all hunks with the same file_path."""
        for h in self.hunks:
            h.file_path = "same.py"
        self.state.approve_file()
        for h in self.hunks:
            self.assertTrue(h.approved)

    def test_approve_file_skips_other_files(self) -> None:
        """Approve-file only touches hunks matching the current file."""
        self.hunks[0].file_path = "a.py"
        self.hunks[1].file_path = "b.py"
        self.hunks[2].file_path = "a.py"
        self.state.approve_file()
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
        self.state.approve_file()
        self.assertTrue(self.hunks[0].approved)
        self.assertFalse(self.hunks[1].approved)
        self.assertFalse(self.hunks[2].approved)
        self.assertEqual(len(self.hunks[1].notes), 1)
        self.assertEqual(len(self.hunks[2].notes), 1)

    def test_find_next_unhandled_wraps(self) -> None:
        """find_next_unhandled wraps around the list."""
        self.hunks[1].approved = True
        self.hunks[2].approved = True
        self.state.current_index = 2
        self.assertEqual(self.state.find_next_unhandled(), 0)

    def test_find_next_unhandled_all_handled(self) -> None:
        """When all hunks are handled, returns current index."""
        for h in self.hunks:
            h.approved = True
        self.state.current_index = 1
        self.assertEqual(self.state.find_next_unhandled(), 1)

    def test_find_initial_hunk_index(self) -> None:
        """initial_index returns the first unhandled hunk."""
        self.hunks[0].approved = True
        self.assertEqual(neorev.ReviewState.initial_index(self.hunks), 1)

    def test_find_initial_all_handled(self) -> None:
        """When all hunks are handled, returns 0."""
        for h in self.hunks:
            h.approved = True
        self.assertEqual(neorev.ReviewState.initial_index(self.hunks), 0)

    def test_navigate_single_hunk(self) -> None:
        """With a single hunk, both j and k return False."""
        state = neorev.ReviewState(hunks=[make_hunk()], global_notes=[])
        self.assertFalse(state.navigate("j"))
        self.assertFalse(state.navigate("k"))
        self.assertEqual(state.current_index, 0)

    def test_approve_already_flagged_hunk_has_no_effect(self) -> None:
        """Approving a flagged hunk has no effect — notes protect the hunk."""
        self.hunks[0].notes = [
            neorev.HunkNote(
                kind=neorev.NoteKind.FLAG, target=neorev.HunkTarget(), text="fix this"
            )
        ]
        self.state.approve()
        self.assertFalse(self.hunks[0].approved)
        self.assertEqual(len(self.hunks[0].notes), 1)

    def test_approve_file_idempotent_on_approved(self) -> None:
        """Approve-file on already-approved hunks keeps them approved."""
        for h in self.hunks:
            h.file_path = "same.py"
            h.approved = True
        self.state.approve_file()
        for h in self.hunks:
            self.assertTrue(h.approved)

    def test_approve_file_advances_to_other_file(self) -> None:
        """After approve-file, cursor moves to next unhandled hunk in another file."""
        self.hunks[0].file_path = "a.py"
        self.hunks[1].file_path = "a.py"
        self.hunks[2].file_path = "b.py"
        self.state.approve_file()
        self.assertEqual(self.state.current_index, 2)

    def test_find_next_unhandled_single_unhandled(self) -> None:
        """With one unhandled hunk, it is always found regardless of position."""
        self.hunks[0].approved = True
        self.hunks[1].approved = True
        for start in range(3):
            self.state.current_index = start
            self.assertEqual(self.state.find_next_unhandled(), 2)


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
        """compute_diff_viewport reserves rows for scroll indicators."""
        chrome = neorev.CHROME_ROWS
        vp = neorev.compute_diff_viewport(100, 10 + chrome, 0)
        self.assertEqual(vp.visible_line_count, 9)
        self.assertFalse(vp.can_scroll_up)
        self.assertTrue(vp.can_scroll_down)

    def test_count_fitting_lines_from_offset(self) -> None:
        """compute_diff_viewport shows both indicators when mid-scroll."""
        chrome = neorev.CHROME_ROWS
        vp = neorev.compute_diff_viewport(100, 10 + chrome, 5)
        self.assertEqual(vp.visible_line_count, 8)
        self.assertTrue(vp.can_scroll_up)
        self.assertTrue(vp.can_scroll_down)

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
        """compute_diff_viewport enforces MIN_VISIBLE_ROWS."""
        chrome = neorev.CHROME_ROWS
        vp = neorev.compute_diff_viewport(100, 0 + chrome, 0)
        self.assertEqual(vp.visible_line_count, neorev.MIN_VISIBLE_ROWS)

    def test_count_fitting_lines_all_fit(self) -> None:
        """When at end, compute_diff_viewport can disable down indicator."""
        vp = neorev.compute_diff_viewport(20, 10, 15)
        self.assertEqual(vp.visible_line_count, 5)
        self.assertTrue(vp.can_scroll_up)
        self.assertFalse(vp.can_scroll_down)

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
        footer = neorev.build_keyhint_footer(
            neorev.MAIN_FOOTER_SEGMENTS, WIDE_FOOTER_WIDTH, ellipsis=True
        )
        self.assertIn("j/k", footer)
        self.assertIn("quit", footer)

    def test_footer_truncates_narrow(self) -> None:
        """A very narrow terminal truncates the footer."""
        footer = neorev.build_keyhint_footer(
            neorev.MAIN_FOOTER_SEGMENTS, NARROW_FOOTER_WIDTH, ellipsis=True
        )
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
        full_footer = neorev.build_keyhint_footer(
            neorev.MAIN_FOOTER_SEGMENTS, WIDE_FOOTER_WIDTH, ellipsis=True
        )
        visible = neorev.visible_len(full_footer)
        exact_footer = neorev.build_keyhint_footer(
            neorev.MAIN_FOOTER_SEGMENTS, visible, ellipsis=True
        )
        self.assertNotIn("…", exact_footer)


class TestCommentHelpers(unittest.TestCase):
    """Tests for write_comment_template and read_comment_file."""

    def setUp(self) -> None:
        """Create a temporary directory for comment files."""
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        """Remove the temporary directory."""
        self.tmpdir.cleanup()

    def tmp_path(self, name: str = "comment.cfg") -> str:
        """Return a path inside the temporary directory."""
        return str(Path(self.tmpdir.name) / name)

    def test_write_comment_template(self) -> None:
        """Template contains the location and existing comment."""
        path = self.tmp_path()
        with open(path, "w") as f:
            jump = neorev.write_comment_template(f, "test.py:10", "existing")

        with open(path) as f:
            content = f.read()
        self.assertIn("test.py:10", content)
        self.assertIn("existing", content)
        self.assertIsInstance(jump, int)
        self.assertGreater(jump, 0)

    def test_read_comment_file_strips_hashes(self) -> None:
        """read_comment_file strips lines starting with #."""
        path = self.tmp_path()
        with open(path, "w") as f:
            f.write("# header\nactual comment\n# footer\n")

        comment = neorev.read_comment_file(path)
        self.assertEqual(comment, "actual comment")

    def test_write_comment_template_no_existing(self) -> None:
        """Template with no existing comment still has location and blank line."""
        path = self.tmp_path()
        with open(path, "w") as f:
            jump = neorev.write_comment_template(f, "foo.py:5", "")

        with open(path) as f:
            content = f.read()
        self.assertIn("foo.py:5", content)
        self.assertGreater(jump, 0)

    def test_read_comment_file_all_comments(self) -> None:
        """A file with only # lines returns empty string."""
        path = self.tmp_path()
        with open(path, "w") as f:
            f.write("# line one\n# line two\n")

        comment = neorev.read_comment_file(path)
        self.assertEqual(comment, "")

    def test_read_comment_file_preserves_inner_hashes(self) -> None:
        """Lines not starting with # are preserved even if they contain #."""
        path = self.tmp_path()
        with open(path, "w") as f:
            f.write("# header\n  # indented hash\nplain\n")

        comment = neorev.read_comment_file(path)
        self.assertIn("# indented hash", comment)
        self.assertIn("plain", comment)


class TestBuildLineContext(unittest.TestCase):
    """Tests for Hunk.build_line_context."""

    SAMPLE_BODY = (
        " line one\n line two\n-old three\n+new three\n+added four\n line five\n"
    )
    SAMPLE_RANGE = "@@ -1,4 +1,5 @@"

    def make_hunk_with_context(self) -> neorev.Hunk:
        """Build a hunk from the sample body."""
        return make_hunk(
            range_line=self.SAMPLE_RANGE,
            body=self.SAMPLE_BODY.rstrip("\n"),
        )

    def test_context_around_added_line(self) -> None:
        """Context shows surrounding lines with marker on the target."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=3)
        ctx = hunk.build_line_context(target)
        marker_lines = [c for c in ctx if neorev.EDITOR_TARGET_MARKER in c]
        self.assertEqual(len(marker_lines), 1)
        self.assertIn("new three", marker_lines[0])

    def test_context_around_removed_line(self) -> None:
        """Context marks the removed line with the target marker."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.REMOVED, line_number=3)
        ctx = hunk.build_line_context(target)
        marker_lines = [c for c in ctx if neorev.EDITOR_TARGET_MARKER in c]
        self.assertEqual(len(marker_lines), 1)
        self.assertIn("old three", marker_lines[0])

    def test_context_includes_diff_prefix(self) -> None:
        """Each context line includes the diff prefix from its kind."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=3)
        ctx = hunk.build_line_context(target)
        added = [c for c in ctx if "new three" in c]
        self.assertTrue(any("+" in c for c in added))
        context = [c for c in ctx if "line two" in c]
        self.assertTrue(len(context) > 0)

    def test_context_radius_limits(self) -> None:
        """Context does not exceed the configured radius."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=3)
        ctx = hunk.build_line_context(target)
        max_lines = 2 * neorev.EDITOR_CONTEXT_RADIUS + 1
        self.assertLessEqual(len(ctx), max_lines)

    def test_context_at_start_of_hunk(self) -> None:
        """Context near the beginning does not go out of bounds."""
        hunk = make_hunk(
            range_line="@@ -1,2 +1,2 @@",
            body="+added\n context",
        )
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=1)
        ctx = hunk.build_line_context(target)
        self.assertTrue(len(ctx) >= 1)
        self.assertIn(neorev.EDITOR_TARGET_MARKER, ctx[0])

    def test_unknown_target_returns_empty(self) -> None:
        """A target not in the display lines returns an empty list."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=999)
        ctx = hunk.build_line_context(target)
        self.assertEqual(ctx, [])

    def test_context_lines_are_aligned(self) -> None:
        """All context lines have the same length up to the diff prefix."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=3)
        ctx = hunk.build_line_context(target)
        # Check alignment: strip the "# " prefix and verify the marker + line
        # number + prefix portion has consistent width.
        for line in ctx:
            stripped = line[2:]  # remove "# "
            # marker(1) + space(1) + line_num(>=4) + space(1) + prefix(1)
            # The diff prefix char should always be at the same offset.
            self.assertEqual(stripped[0:2], stripped[0] + " ")
            self.assertIn(stripped[6], ("+", "-", " ", "\\"))


class TestBuildHunkContext(unittest.TestCase):
    """Tests for Hunk.build_hunk_context."""

    SAMPLE_BODY = (
        " line one\n line two\n-old three\n+new three\n+added four\n line five\n"
    )
    SAMPLE_RANGE = "@@ -1,4 +1,5 @@"

    def make_hunk(self) -> neorev.Hunk:
        """Build a hunk from the sample body."""
        return make_hunk(
            range_line=self.SAMPLE_RANGE,
            body=self.SAMPLE_BODY.rstrip("\n"),
        )

    def test_starts_from_scroll_offset(self) -> None:
        """Context lines begin at the given scroll offset."""
        hunk = self.make_hunk()
        ctx = hunk.build_hunk_context(scroll_offset=2)
        self.assertIn("old three", ctx[0])

    def test_respects_max_lines(self) -> None:
        """Context never exceeds EDITOR_HUNK_CONTEXT_MAX lines."""
        body = "\n".join(f"+line {i}" for i in range(30))
        hunk = make_hunk(range_line="@@ -1,0 +1,30 @@", body=body)
        ctx = hunk.build_hunk_context(scroll_offset=0)
        self.assertEqual(len(ctx), neorev.EDITOR_HUNK_CONTEXT_MAX)

    def test_offset_zero_starts_at_beginning(self) -> None:
        """Offset zero returns lines from the start of the hunk."""
        hunk = self.make_hunk()
        ctx = hunk.build_hunk_context(scroll_offset=0)
        self.assertIn("line one", ctx[0])

    def test_offset_past_end_returns_empty(self) -> None:
        """An offset beyond the display lines returns an empty list."""
        hunk = self.make_hunk()
        ctx = hunk.build_hunk_context(scroll_offset=999)
        self.assertEqual(ctx, [])

    def test_negative_offset_clamps_to_zero(self) -> None:
        """A negative offset is clamped to zero."""
        hunk = self.make_hunk()
        ctx_neg = hunk.build_hunk_context(scroll_offset=-5)
        ctx_zero = hunk.build_hunk_context(scroll_offset=0)
        self.assertEqual(ctx_neg, ctx_zero)

    def test_lines_use_context_pad(self) -> None:
        """All hunk context lines use the context pad marker, not the target marker."""
        hunk = self.make_hunk()
        ctx = hunk.build_hunk_context(scroll_offset=0)
        for line in ctx:
            self.assertNotIn(neorev.EDITOR_TARGET_MARKER, line)
            self.assertIn(neorev.EDITOR_CONTEXT_PAD, line)

    def test_includes_diff_prefix(self) -> None:
        """Context lines include the diff prefix character."""
        hunk = self.make_hunk()
        ctx = hunk.build_hunk_context(scroll_offset=0)
        prefixes = set()
        for line in ctx:
            stripped = line[2:]  # remove "# "
            prefixes.add(stripped[6])
        self.assertTrue(prefixes & {"+", "-", " "})


class TestWriteCommentTemplateWithContext(unittest.TestCase):
    """Tests for write_comment_template with context_lines."""

    def test_context_lines_included_in_template(self) -> None:
        """Context lines appear as # comments in the template."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg", delete=False) as f:
            ctx = ["# ► 10 + added line", "#   11   context line"]
            jump = neorev.write_comment_template(f, "test.py:10", "", ctx)
            path = f.name

        try:
            with open(path) as rf:
                content = rf.read()
            self.assertIn("added line", content)
            self.assertIn("context line", content)
            self.assertGreater(jump, 0)
        finally:
            os.unlink(path)

    def test_context_lines_stripped_by_read(self) -> None:
        """Context lines (starting with #) are stripped when reading back."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg", delete=False) as f:
            ctx = ["# ► 10 + the target line"]
            neorev.write_comment_template(f, "loc", "my note", ctx)
            path = f.name

        try:
            result = neorev.read_comment_file(path)
            self.assertEqual(result, "my note")
            self.assertNotIn("target line", result)
        finally:
            os.unlink(path)

    def test_jump_line_accounts_for_context(self) -> None:
        """Jump line is offset by the number of context lines."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg", delete=False) as f:
            jump_no_ctx = neorev.write_comment_template(f, "loc", "")
            f.name  # noqa: B018

        with tempfile.NamedTemporaryFile(mode="w", suffix=".cfg", delete=False) as f:
            ctx = ["# line1", "# line2", "# line3"]
            jump_with_ctx = neorev.write_comment_template(f, "loc", "", ctx)
            path = f.name

        try:
            # 3 context lines + 2 separator lines (#\n before and after)
            expected_offset = len(ctx) + 2
            self.assertEqual(jump_with_ctx, jump_no_ctx + expected_offset)
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

    def test_render_note_panel_empty(self) -> None:
        """Note panel with no notes shows 'No notes yet'."""
        state = neorev.ReviewState(hunks=[make_hunk()], global_notes=[])
        panel = neorev.NotePanelState()
        self.term.render_note_panel(state, [], panel, b"")
        output = self.fake.read_output()
        self.assertIn(b"No notes", output)

    def test_render_note_panel_with_notes(self) -> None:
        """Note panel lists existing notes."""
        refs = [
            neorev.ManagedNoteRef(
                scope_label="global",
                text="fix this",
                kind=neorev.NoteKind.FLAG,
            ),
        ]
        state = neorev.ReviewState(hunks=[make_hunk()], global_notes=[])
        panel = neorev.NotePanelState()
        self.term.render_note_panel(state, refs, panel, b"")
        output = self.fake.read_output()
        self.assertIn(b"fix this", output)

    def test_note_panel_diff_uses_full_available_height(self) -> None:
        """Note panel diff fills all rows above the panel without blank gaps."""
        diff_lines = b"\n".join(b"line%d" % i for i in range(TERM_HEIGHT))
        refs = [
            neorev.ManagedNoteRef(
                scope_label="global",
                text="note",
                kind=neorev.NoteKind.FLAG,
            ),
        ]
        state = neorev.ReviewState(hunks=[make_hunk()], global_notes=[])
        panel = neorev.NotePanelState()
        self.term.render_note_panel(state, refs, panel, diff_lines)
        output = self.fake.read_output()
        lines = output.split(b"\r\n")
        non_empty = [ln for ln in lines if ln.strip()]
        panel_height = neorev.compute_note_panel_height(len(refs), TERM_HEIGHT)
        diff_height = TERM_HEIGHT - panel_height
        diff_chrome = neorev.NOTE_PANEL_DIFF_CHROME_ROWS
        expected_diff_content = diff_height - diff_chrome
        diff_content_lines = [ln for ln in non_empty if ln.startswith(b"line")]
        self.assertEqual(len(diff_content_lines), expected_diff_content)


class TestBuildManagedNoteRefs(unittest.TestCase):
    """Tests for build_managed_note_refs."""

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
        refs = state.managed_note_refs()
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].text, "fix this line")
        self.assertEqual(refs[0].kind, neorev.NoteKind.FLAG)
        self.assertIn("+42", refs[0].scope_label)

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
        refs = state.managed_note_refs()
        texts = [ref.text for ref in refs]
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
    """Tests for build_arg_parser and resolve_args."""

    def test_output_flag(self) -> None:
        """The -o/--output flag sets the output path."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-o", "out.md"])
        self.assertEqual(args.output, "out.md")
        self.assertFalse(args.clip)
        self.assertFalse(args.clear)

    def test_output_long_form(self) -> None:
        """The --output long form sets the output path."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--output", "out.md"])
        self.assertEqual(args.output, "out.md")

    def test_clip_flag(self) -> None:
        """The -x/--clip flag is recognised."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--clip", "-o", "out.md"])
        self.assertTrue(args.clip)
        args_short = parser.parse_args(["-x", "-o", "out.md"])
        self.assertTrue(args_short.clip)

    def test_clear_flag(self) -> None:
        """The -c/--clear flag is recognised."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--clear", "-o", "out.md"])
        self.assertTrue(args.clear)
        args_short = parser.parse_args(["-c", "-o", "out.md"])
        self.assertTrue(args_short.clear)

    def test_output_defaults_to_none(self) -> None:
        """Omitting -o leaves output as None for resolve_args to fill."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.output)

    def test_resolve_args_fills_default_output(self) -> None:
        """resolve_args fills in a default output path when not provided."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args([])
        with (
            patch.object(neorev, "detect_vcs", return_value=neorev.Vcs.GIT),
            patch.object(neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH),
        ):
            neorev.resolve_args(args)
        self.assertEqual(args.output, MOCK_OUTPUT_PATH)

    def test_resolve_args_keeps_explicit_output(self) -> None:
        """resolve_args preserves an explicitly provided -o value."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-o", "mine.md"])
        neorev.resolve_args(args)
        self.assertEqual(args.output, "mine.md")

    def test_git_with_revision(self) -> None:
        """The -g flag records (Vcs.GIT, rev) in vcs_rev."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-g", "HEAD~1", "-o", "out.md"])
        self.assertEqual(args.vcs_rev, (neorev.Vcs.GIT, "HEAD~1"))
        self.assertEqual(args.output, "out.md")

    def test_jj_with_revision(self) -> None:
        """The -j flag records (Vcs.JUJUTSU, rev) in vcs_rev."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-j", "abc123", "-o", "out.md"])
        self.assertEqual(args.vcs_rev, (neorev.Vcs.JUJUTSU, "abc123"))
        self.assertEqual(args.output, "out.md")

    def test_git_without_revision(self) -> None:
        """The -g flag without a revision records (Vcs.GIT, None)."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-g"])
        self.assertEqual(args.vcs_rev, (neorev.Vcs.GIT, None))

    def test_jj_without_revision(self) -> None:
        """The -j flag without a revision records (Vcs.JUJUTSU, None)."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-j"])
        self.assertEqual(args.vcs_rev, (neorev.Vcs.JUJUTSU, None))

    def test_vcs_rev_none_when_no_flag_passed(self) -> None:
        """Without -g/-j, vcs_rev defaults to None."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.vcs_rev)

    def test_git_and_jj_mutually_exclusive(self) -> None:
        """Using both -g and -j is rejected."""
        parser = neorev.build_arg_parser()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["-g", "HEAD", "-j", "abc"])

    def test_long_form_git_with_revision(self) -> None:
        """The --git long form works with a revision."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--git", "v1.0"])
        self.assertEqual(args.vcs_rev, (neorev.Vcs.GIT, "v1.0"))

    def test_long_form_jj_without_revision(self) -> None:
        """The --jj long form works without a revision."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--jj"])
        self.assertEqual(args.vcs_rev, (neorev.Vcs.JUJUTSU, None))


class TestDefaultOutputPath(unittest.TestCase):
    """Tests for default_output_path and query_vcs_metadata."""

    def make_meta(
        self,
        dirname: str = "proj",
        workspace: str | None = None,
        rev: str | None = None,
    ) -> neorev.VcsMetadata:
        """Build a VcsMetadata with test defaults."""
        return neorev.VcsMetadata(dirname=dirname, workspace=workspace, rev=rev)

    def test_uses_xdg_state_home_env(self) -> None:
        """Respect $XDG_STATE_HOME when set."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"XDG_STATE_HOME": tmpdir}),
            patch.object(neorev, "query_vcs_metadata", return_value=self.make_meta()),
        ):
            result = neorev.default_output_path(neorev.Vcs.GIT)
        self.assertTrue(result.startswith(tmpdir))
        self.assertTrue(result.endswith(".md"))

    def test_falls_back_to_xdg_default(self) -> None:
        """Use ~/.local/state when $XDG_STATE_HOME is unset."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(neorev, "query_vcs_metadata", return_value=self.make_meta()),
            patch.object(Path, "mkdir"),
        ):
            result = neorev.default_output_path(neorev.Vcs.GIT)
        self.assertIn(".local/state/neorev/proj", result)

    def test_filename_parts_basic(self) -> None:
        """Filename is just review.md; dirname becomes a subdirectory."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"XDG_STATE_HOME": tmpdir}),
            patch.object(
                neorev,
                "query_vcs_metadata",
                return_value=self.make_meta(dirname="myproj"),
            ),
        ):
            result = neorev.default_output_path(neorev.Vcs.GIT)
        self.assertEqual(Path(result).name, "review.md")
        self.assertEqual(Path(result).parent.name, "myproj")

    def test_filename_parts_with_workspace_and_rev(self) -> None:
        """Filename includes workspace and rev when available."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"XDG_STATE_HOME": tmpdir}),
            patch.object(
                neorev,
                "query_vcs_metadata",
                return_value=self.make_meta(workspace="feat", rev="abc1234"),
            ),
        ):
            result = neorev.default_output_path(neorev.Vcs.JUJUTSU)
        self.assertEqual(Path(result).name, "review-feat-abc1234.md")
        self.assertEqual(Path(result).parent.name, "proj")

    def test_filename_parts_with_rev_only(self) -> None:
        """Filename includes rev but skips empty workspace."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"XDG_STATE_HOME": tmpdir}),
            patch.object(
                neorev,
                "query_vcs_metadata",
                return_value=self.make_meta(rev="def456"),
            ),
        ):
            result = neorev.default_output_path(neorev.Vcs.GIT)
        self.assertEqual(Path(result).name, "review-def456.md")
        self.assertEqual(Path(result).parent.name, "proj")

    def test_does_not_create_state_directory(self) -> None:
        """default_output_path must not create the directory itself."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "sub" / "neorev" / "proj"
            with (
                patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(tmpdir) / "sub")}),
                patch.object(
                    neorev, "query_vcs_metadata", return_value=self.make_meta()
                ),
            ):
                neorev.default_output_path(neorev.Vcs.GIT)
            self.assertFalse(state_dir.is_dir())

    def test_resolve_args_detects_jj_from_flag(self) -> None:
        """resolve_args passes Vcs.JUJUTSU and None rev to default_output_path."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-j"])
        with patch.object(
            neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH
        ) as mock:
            neorev.resolve_args(args)
        mock.assert_called_once_with(neorev.Vcs.JUJUTSU, None)

    def test_resolve_args_passes_jj_rev(self) -> None:
        """resolve_args forwards the jj revision to default_output_path."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-j", "abc123"])
        with patch.object(
            neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH
        ) as mock:
            neorev.resolve_args(args)
        mock.assert_called_once_with(neorev.Vcs.JUJUTSU, "abc123")

    def test_resolve_args_detects_git_from_flag(self) -> None:
        """resolve_args passes Vcs.GIT and None rev to default_output_path."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-g"])
        with patch.object(
            neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH
        ) as mock:
            neorev.resolve_args(args)
        mock.assert_called_once_with(neorev.Vcs.GIT, None)

    def test_resolve_args_passes_git_rev(self) -> None:
        """resolve_args forwards the git revision to default_output_path."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-g", "HEAD~2"])
        with patch.object(
            neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH
        ) as mock:
            neorev.resolve_args(args)
        mock.assert_called_once_with(neorev.Vcs.GIT, "HEAD~2")

    def test_resolve_args_detects_jj_from_directory(self) -> None:
        """resolve_args detects jj when no flag is given."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args([])
        with (
            patch.object(neorev, "detect_vcs", return_value=neorev.Vcs.JUJUTSU),
            patch.object(
                neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH
            ) as mock,
        ):
            neorev.resolve_args(args)
        mock.assert_called_once_with(neorev.Vcs.JUJUTSU, None)

    def test_jj_uses_shortest_unambiguous_rev(self) -> None:
        """query_jj_metadata uses shortest() without a fixed length."""
        calls: list[list[str]] = []

        def fake_run_vcs(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            calls.append(cmd)
            if "log" in cmd:
                return "l"
            return "default: /some/path"

        with patch.object(neorev, "run_vcs", side_effect=fake_run_vcs):
            meta = neorev.query_jj_metadata()
        self.assertEqual(meta.rev, "l")
        log_cmd = calls[0]
        template_arg = log_cmd[-1]
        self.assertIn("shortest()", template_arg)
        self.assertNotIn("shortest(8)", template_arg)

    def test_jj_passes_custom_rev_to_log(self) -> None:
        """query_jj_metadata passes a custom rev to jj log -r."""
        calls: list[list[str]] = []

        def fake_run_vcs(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            calls.append(cmd)
            if "log" in cmd:
                return "x"
            return "default: /some/path"

        with patch.object(neorev, "run_vcs", side_effect=fake_run_vcs):
            meta = neorev.query_jj_metadata(rev="myrev")
        self.assertEqual(meta.rev, "x")
        log_cmd = calls[0]
        r_idx = log_cmd.index("-r")
        self.assertEqual(log_cmd[r_idx + 1], "myrev")

    def test_jj_picks_workspace_matching_cwd(self) -> None:
        """query_jj_metadata selects the workspace whose path matches cwd."""

        def fake_run_vcs(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            if "log" in cmd:
                return "abc"
            return "default: /home/user/proj\nfeature: /home/user/proj-feature"

        with (
            patch.object(neorev, "run_vcs", side_effect=fake_run_vcs),
            patch.object(Path, "cwd", return_value=Path("/home/user/proj-feature")),
        ):
            meta = neorev.query_jj_metadata()
        self.assertEqual(meta.workspace, "feature")

    def test_git_default_rev_appends_dirty_suffix(self) -> None:
        """query_git_metadata with rev=None returns '<short>-dirty' for working tree."""

        def fake_run_vcs(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            if "rev-parse" in cmd and "--short" in cmd:
                return "abc1234"
            if "--show-toplevel" in cmd:
                return "/home/user/proj"
            return "worktree /home/user/proj\n"

        with patch.object(neorev, "run_vcs", side_effect=fake_run_vcs):
            meta = neorev.query_git_metadata()
        self.assertEqual(meta.rev, "abc1234-dirty")

    def test_git_explicit_rev_has_no_dirty_suffix(self) -> None:
        """query_git_metadata with an explicit rev returns the bare short hash."""

        def fake_run_vcs(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            if "rev-parse" in cmd and "--short" in cmd:
                return "abc1234"
            if "--show-toplevel" in cmd:
                return "/home/user/proj"
            return "worktree /home/user/proj\n"

        with patch.object(neorev, "run_vcs", side_effect=fake_run_vcs):
            meta = neorev.query_git_metadata(rev="v1.0")
        self.assertEqual(meta.rev, "abc1234")

    def test_git_passes_custom_rev_to_rev_parse(self) -> None:
        """query_git_metadata passes a custom rev to git rev-parse."""
        calls: list[list[str]] = []

        def fake_run_vcs(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            calls.append(cmd)
            if "rev-parse" in cmd and "--short" in cmd:
                return "abc1234"
            if "--show-toplevel" in cmd:
                return "/home/user/proj"
            return "worktree /home/user/proj\n"

        with patch.object(neorev, "run_vcs", side_effect=fake_run_vcs):
            meta = neorev.query_git_metadata(rev="v1.0")
        self.assertEqual(meta.rev, "abc1234")
        rev_parse_cmd = calls[0]
        self.assertIn("v1.0", rev_parse_cmd)


class TestWriteReviewOutput(unittest.TestCase):
    """Tests for write_review_output."""

    def test_creates_parent_directory(self) -> None:
        """write_review_output creates missing parent directories before writing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = str(Path(tmpdir) / "nested" / "dir" / "review.md")
            hunk = make_hunk(file_path="a.py")
            neorev.write_review_output(output_path, [hunk], [])
            self.assertTrue(Path(output_path).exists())


class TestProjectName(unittest.TestCase):
    """Tests for project_name."""

    def test_uses_last_two_components_lowercased(self) -> None:
        """Return the last 2 path components of cwd, lowercased, joined with '-'."""
        cwd = Path("/home/user/Projets/NeoRev")
        with patch.object(Path, "cwd", return_value=cwd):
            result = neorev.project_name()
        self.assertEqual(result, "projets-neorev")

    def test_single_component_path(self) -> None:
        """Return just the single component lowercased when path has depth 1."""
        with patch.object(Path, "cwd", return_value=Path("/root")):
            result = neorev.project_name()
        self.assertEqual(result, "root")


class TestDetectVcs(unittest.TestCase):
    """Tests for detect_vcs."""

    def test_returns_none_outside_any_repo(self) -> None:
        """Return None when no VCS probe command succeeds."""
        with patch("neorev.try_vcs", return_value=False):
            result = neorev.detect_vcs()
        self.assertIsNone(result)

    def test_returns_jujutsu_in_jj_repo(self) -> None:
        """Return Vcs.JUJUTSU when jj root succeeds."""

        def fake_try_vcs(command: list[str]) -> bool:
            """Succeed only for jj."""
            return command[0] == "jj"

        with patch("neorev.try_vcs", side_effect=fake_try_vcs):
            result = neorev.detect_vcs()
        self.assertEqual(result, neorev.Vcs.JUJUTSU)

    def test_prefers_jujutsu_over_git_and_short_circuits(self) -> None:
        """Return Vcs.JUJUTSU and only probe jj when both backends are present."""
        mock = MagicMock(return_value=True)
        with patch("neorev.try_vcs", mock):
            result = neorev.detect_vcs()
        self.assertEqual(result, neorev.Vcs.JUJUTSU)
        mock.assert_called_once()
        self.assertEqual(mock.call_args[0][0][0], "jj")

    def test_returns_git_in_git_repo(self) -> None:
        """Return Vcs.GIT when git rev-parse succeeds but jj root does not."""

        def fake_try_vcs(command: list[str]) -> bool:
            """Succeed only for git."""
            return command[0] == "git"

        with patch("neorev.try_vcs", side_effect=fake_try_vcs):
            result = neorev.detect_vcs()
        self.assertEqual(result, neorev.Vcs.GIT)


class TestMainWorkflow(unittest.TestCase):
    """High-level tests for new review and resume workflows through main()."""

    def test_new_review_flow_writes_expected_output(self) -> None:
        """A scripted review writes annotations and approval hashes."""

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

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        try:
            run_main_with_scripted_terminal(TWO_HUNK_DIFF, output_path, script)
            output = Path(output_path).read_text()
            self.assertIn("[CHANGE REQUESTED] `hello.py", output)
            self.assertIn(WORKFLOW_FLAG_COMMENT, output)
            self.assertIn("[QUESTION] `global`", output)
            self.assertIn(WORKFLOW_GLOBAL_NOTE, output)
            self.assertIn("<!-- neorev: approved-hashes=", output)
        finally:
            os.unlink(output_path)

    def test_resume_workflow_applies_annotations_and_global_notes(self) -> None:
        """Resuming from an existing output restores notes and hash-keyed approvals."""
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

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
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

    def test_resume_annotation_precedence_over_hashes(self) -> None:
        """Explicit annotation status wins when hashes mark the same hunk approved."""
        previous_hunks = neorev.parse_diff(TWO_HUNK_DIFF)
        previous_hunks[0].notes = [
            neorev.HunkNote(
                kind=neorev.NoteKind.QUESTION,
                target=neorev.HunkTarget(),
                text=WORKFLOW_PRECEDENCE_QUESTION,
            )
        ]
        previous_hunks[0].approved = True
        previous_output = neorev.format_output(previous_hunks, [])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(previous_output)
            output_path = f.name

        try:
            run_main_with_scripted_terminal(
                TWO_HUNK_DIFF,
                output_path,
                lambda _state: None,
            )
            output = Path(output_path).read_text()
            self.assertIn("[QUESTION] `hello.py", output)
            self.assertIn(WORKFLOW_PRECEDENCE_QUESTION, output)
        finally:
            os.unlink(output_path)

    def test_resume_with_stale_annotation_reports_and_keeps_hashes(self) -> None:
        """Stale annotations are reported while hash approvals still resume."""
        hash_hunks = neorev.parse_diff(TWO_HUNK_DIFF)
        hash_hunks[1].approved = True
        hashes = neorev.encode_approved_hashes(hash_hunks)
        stale_output = (
            "### [CHANGE REQUESTED] `stale.py @ hunk`\n\n"
            "```diff\n"
            "@@ -99,1 +99,1 @@\n"
            "+stale\n"
            "```\n\n"
            "stale note\n\n"
            f"<!-- neorev: approved-hashes={hashes} -->\n"
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
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

    def test_git_without_revision_uses_git_diff(self) -> None:
        """Using -g without a revision runs 'git diff' and records it as the source."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        try:
            with patch.object(
                neorev,
                "fetch_diff_from_vcs",
                return_value=TWO_HUNK_DIFF,
            ) as fetch_mock:
                run_main_with_scripted_terminal(
                    TWO_HUNK_DIFF,
                    output_path,
                    lambda state: state.hunks[0].__setattr__("approved", True),
                    extra_args=["-g"],
                )
            fetch_mock.assert_called_once_with(["git", "diff"])
            output = Path(output_path).read_text()
            self.assertIn("Reviewed diff:", output)
            self.assertIn("`git diff`", output)
            self.assertNotIn("git show", output)
        finally:
            os.unlink(output_path)

    def test_auto_detect_git_uses_git_diff(self) -> None:
        """When no diff is piped and a git repo is detected, default to 'git diff'."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        try:
            with (
                patch.object(
                    neorev,
                    "read_diff_from_stdin",
                    side_effect=neorev.NoDiffOnStdinError,
                ),
                patch.object(neorev, "detect_vcs", return_value=neorev.Vcs.GIT),
                patch.object(
                    neorev,
                    "fetch_diff_from_vcs",
                    return_value=TWO_HUNK_DIFF,
                ) as fetch_mock,
            ):
                run_main_with_scripted_terminal(
                    TWO_HUNK_DIFF,
                    output_path,
                    lambda state: state.hunks[0].__setattr__("approved", True),
                )
            fetch_mock.assert_called_once_with(["git", "diff"])
            output = Path(output_path).read_text()
            self.assertIn("`git diff`", output)
            self.assertNotIn("git show", output)
        finally:
            os.unlink(output_path)

    def test_auto_detect_jj_uses_jj_show(self) -> None:
        """When no diff is piped and a jj repo is detected, default to 'jj show @'."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        try:
            with (
                patch.object(
                    neorev,
                    "read_diff_from_stdin",
                    side_effect=neorev.NoDiffOnStdinError,
                ),
                patch.object(neorev, "detect_vcs", return_value=neorev.Vcs.JUJUTSU),
                patch.object(
                    neorev,
                    "fetch_diff_from_vcs",
                    return_value=TWO_HUNK_DIFF,
                ) as fetch_mock,
            ):
                run_main_with_scripted_terminal(
                    TWO_HUNK_DIFF,
                    output_path,
                    lambda state: state.hunks[0].__setattr__("approved", True),
                )
            fetch_mock.assert_called_once_with(["jj", "show"])
            output = Path(output_path).read_text()
            self.assertIn("`jj show`", output)
        finally:
            os.unlink(output_path)

    def test_git_with_revision_uses_git_show(self) -> None:
        """Using -g with a revision runs 'git show REV' and records it as the source."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        try:
            with patch.object(
                neorev,
                "fetch_diff_from_vcs",
                return_value=TWO_HUNK_DIFF,
            ) as fetch_mock:
                run_main_with_scripted_terminal(
                    TWO_HUNK_DIFF,
                    output_path,
                    lambda state: state.hunks[0].__setattr__("approved", True),
                    extra_args=["-g", "HEAD~1"],
                )
            fetch_mock.assert_called_once_with(["git", "show", "HEAD~1"])
            output = Path(output_path).read_text()
            self.assertIn("`git show HEAD~1`", output)
        finally:
            os.unlink(output_path)

    def test_jj_without_revision_includes_diff_source(self) -> None:
        """Using -j without a revision still includes 'jj show' as diff source."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        try:
            with patch.object(
                neorev,
                "fetch_diff_from_vcs",
                return_value=TWO_HUNK_DIFF,
            ):
                run_main_with_scripted_terminal(
                    TWO_HUNK_DIFF,
                    output_path,
                    lambda state: state.hunks[0].__setattr__("approved", True),
                    extra_args=["-j"],
                )
            output = Path(output_path).read_text()
            self.assertIn("Reviewed diff:", output)
            self.assertIn("jj show", output)
        finally:
            os.unlink(output_path)

    def test_clear_flag_discards_previous_review(self) -> None:
        """The --clear flag discards a previous review and starts fresh."""
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

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(previous_output)
            output_path = f.name

        try:
            stderr = run_main_with_scripted_terminal(
                TWO_HUNK_DIFF,
                output_path,
                lambda _state: None,
                extra_args=["--clear"],
            )
            self.assertFalse(Path(output_path).exists())
            self.assertNotIn("Loaded", stderr)
        finally:
            if Path(output_path).exists():
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

    def test_manage_global_notes_edit_closes_menu(self) -> None:
        """Editing a note from the manage menu closes the menu."""
        self.state.global_notes.append(
            neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text=GLOBAL_NOTE_CREATED_TEXT)
        )
        with (
            patch.object(
                self.term, "read_key", side_effect=[GLOBAL_NOTE_EDIT_KEY]
            ) as read_key,
            patch.object(self.term, "render_note_panel"),
            patch.object(
                self.term,
                "edit_text_outside_tui",
                return_value=GLOBAL_NOTE_EDITED_TEXT,
            ),
            patch("tty.setraw"),
            patch("neorev.render_through_delta", return_value=b""),
        ):
            self.term.handle_manage_notes(self.state)
        self.assertEqual(read_key.call_count, 1)
        self.assertEqual(
            self.state.global_notes,
            [
                neorev.GlobalNote(
                    kind=neorev.NoteKind.FLAG, text=GLOBAL_NOTE_EDITED_TEXT
                )
            ],
        )

    def test_manage_global_notes_delete_closes_menu(self) -> None:
        """Deleting a note from the manage menu closes the menu."""
        self.state.global_notes.append(
            neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text=GLOBAL_NOTE_CREATED_TEXT)
        )
        with (
            patch.object(
                self.term, "read_key", side_effect=[GLOBAL_NOTE_DELETE_KEY]
            ) as read_key,
            patch.object(self.term, "render_note_panel"),
            patch("neorev.render_through_delta", return_value=b""),
        ):
            self.term.handle_manage_notes(self.state)
        self.assertEqual(read_key.call_count, 1)
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
        """Top bar is not truncated when term_width is None (default)."""
        hunk = make_hunk()
        bar = neorev.build_top_bar(hunk, 0, [hunk], [], term_width=None)
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
    """Tests for build_keyhint_footer with very small widths."""

    def test_zero_width(self) -> None:
        """Zero width produces empty footer."""
        result = neorev.build_keyhint_footer(neorev.MAIN_FOOTER_SEGMENTS, 0)
        self.assertEqual(result, "")

    def test_tiny_width_no_crash(self) -> None:
        """Tiny widths produce a footer without crashing."""
        for w in range(1, TINY_WIDTH + 1):
            result = neorev.build_keyhint_footer(neorev.MAIN_FOOTER_SEGMENTS, w)
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

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(output)
            path = f.name

        try:
            annotations, _, _ = neorev.load_previous_review(path)
            key = ("target.py", hunk.range_line, target)
            self.assertIn(key, annotations)
            saved = annotations[key]
            self.assertEqual(saved.kind, neorev.NoteKind.FLAG)
            self.assertEqual(saved.text, LINE_TARGET_NOTE_TEXT)
            self.assertIsNotNone(saved.anchor)
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

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
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
        hunks = [
            make_hunk(
                file_path="x.py",
                body="+a\n+b",
                range_line="@@ -1,1 +1,3 @@",
            )
        ]
        target = neorev.LineTarget(
            side=neorev.LineSide.ADDED, line_number=ADDED_LINE_NUMBER
        )
        annotations: neorev.SavedAnnotationMap = {
            ("x.py", hunks[0].range_line, target): neorev.SavedAnnotation(
                kind=neorev.NoteKind.FLAG,
                text=LINE_TARGET_APPLY_TEXT,
                anchor=neorev.compute_note_anchor(hunks[0].body, target),
            ),
        }
        result = neorev.apply_previous_review(hunks, annotations)
        self.assertEqual(result.matched, 1)
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
        hunk = make_hunk()
        hunk.upsert_note(neorev.NoteKind.FLAG, target, UPSERT_NOTE_TEXT)
        self.assertEqual(len(hunk.notes), 1)
        hunk.remove_note(target)
        self.assertEqual(len(hunk.notes), 0)


class TestNoteTargetRoundTrip(unittest.TestCase):
    """Tests for NoteTarget.__str__ and parse_note_target round-trip."""

    def test_hunk_target_round_trip(self) -> None:
        """Serialize and parse a HunkTarget back to an equal value."""
        target = neorev.HunkTarget()
        serialized = str(target)
        parsed = neorev.parse_note_target(serialized)
        self.assertEqual(parsed, target)

    def test_line_target_added_round_trip(self) -> None:
        """Serialize and parse a LineTarget('+', N) back to an equal value."""
        target = neorev.LineTarget(
            side=neorev.LineSide.ADDED, line_number=LINE_TARGET_NOTE_LINE
        )
        serialized = str(target)
        parsed = neorev.parse_note_target(serialized)
        self.assertEqual(parsed, target)

    def test_line_target_removed_round_trip(self) -> None:
        """Serialize and parse a LineTarget('-', N) back to an equal value."""
        target = neorev.LineTarget(
            side=neorev.LineSide.REMOVED, line_number=REMOVED_LINE_NUMBER
        )
        serialized = str(target)
        parsed = neorev.parse_note_target(serialized)
        self.assertEqual(parsed, target)

    def test_parse_invalid_returns_none(self) -> None:
        """Parse an invalid target string and verify it returns None."""
        self.assertIsNone(neorev.parse_note_target("bogus"))

    def test_parse_malformed_line_number_returns_none(self) -> None:
        """Parse '+abc' and verify it returns None."""
        self.assertIsNone(neorev.parse_note_target("+abc"))


class TestNoteAccessHelpers(unittest.TestCase):
    """Tests for Hunk.get_note, Hunk.upsert_note, and Hunk.remove_note."""

    def test_get_note_found(self) -> None:
        """Find an existing note by its target."""
        target = neorev.HunkTarget()
        note = neorev.HunkNote(kind=neorev.NoteKind.FLAG, target=target, text="hello")
        hunk = make_hunk(notes=[note])
        result = hunk.get_note(target)
        self.assertIs(result, note)

    def test_get_note_not_found(self) -> None:
        """Return None when no note matches the target."""
        target = neorev.HunkTarget()
        other = neorev.LineTarget(
            side=neorev.LineSide.ADDED, line_number=ADDED_LINE_NUMBER
        )
        note = neorev.HunkNote(kind=neorev.NoteKind.FLAG, target=target, text="hello")
        hunk = make_hunk(notes=[note])
        result = hunk.get_note(other)
        self.assertIsNone(result)

    def test_upsert_note_insert(self) -> None:
        """Upsert into an empty list appends a new note."""
        target = neorev.HunkTarget()
        hunk = make_hunk()
        hunk.upsert_note(neorev.NoteKind.FLAG, target, UPSERT_NOTE_TEXT)
        self.assertEqual(len(hunk.notes), 1)
        self.assertEqual(hunk.notes[0].text, UPSERT_NOTE_TEXT)

    def test_upsert_note_update(self) -> None:
        """Upsert on an existing target replaces the note."""
        target = neorev.HunkTarget()
        hunk = make_hunk()
        hunk.upsert_note(neorev.NoteKind.FLAG, target, UPSERT_NOTE_TEXT)
        hunk.upsert_note(neorev.NoteKind.QUESTION, target, UPSERT_NOTE_UPDATED_TEXT)
        self.assertEqual(len(hunk.notes), 1)
        self.assertEqual(hunk.notes[0].text, UPSERT_NOTE_UPDATED_TEXT)
        self.assertEqual(hunk.notes[0].kind, neorev.NoteKind.QUESTION)

    def test_remove_note_present(self) -> None:
        """Remove a note matching the target."""
        target = neorev.HunkTarget()
        hunk = make_hunk(
            notes=[
                neorev.HunkNote(
                    kind=neorev.NoteKind.FLAG, target=target, text=UPSERT_NOTE_TEXT
                )
            ]
        )
        hunk.remove_note(target)
        self.assertEqual(len(hunk.notes), 0)

    def test_remove_note_absent(self) -> None:
        """Remove on a missing target leaves the list unchanged."""
        target = neorev.HunkTarget()
        other = neorev.LineTarget(
            side=neorev.LineSide.ADDED, line_number=ADDED_LINE_NUMBER
        )
        hunk = make_hunk(
            notes=[
                neorev.HunkNote(
                    kind=neorev.NoteKind.FLAG, target=target, text=UPSERT_NOTE_TEXT
                )
            ]
        )
        hunk.remove_note(other)
        self.assertEqual(len(hunk.notes), 1)


class TestHunkStatusHelpers(unittest.TestCase):
    """Tests for Hunk.summary_status and Hunk.is_handled."""

    def test_hunk_summary_status_approved(self) -> None:
        """Return 'approved' for an approved hunk."""
        hunk = make_hunk(approved=True)
        self.assertEqual(hunk.summary_status, neorev.Status.APPROVED)

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
        self.assertEqual(hunk.summary_status, neorev.Status.FLAG)

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
        self.assertEqual(hunk.summary_status, neorev.Status.QUESTION)

    def test_hunk_summary_status_none(self) -> None:
        """Return None for a hunk with no status, notes, or approval."""
        hunk = make_hunk()
        self.assertIsNone(hunk.summary_status)

    def test_hunk_is_handled_approved(self) -> None:
        """An approved hunk is handled."""
        hunk = make_hunk(approved=True)
        self.assertTrue(hunk.is_handled)

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
        self.assertTrue(hunk.is_handled)

    def test_hunk_is_handled_with_status(self) -> None:
        """A hunk with a legacy status is handled."""
        hunk = make_hunk(status=neorev.Status.FLAG)
        self.assertTrue(hunk.is_handled)

    def test_hunk_is_not_handled(self) -> None:
        """A bare hunk with no status, notes, or approval is not handled."""
        hunk = make_hunk()
        self.assertFalse(hunk.is_handled)


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
            self.term.pick_line_target(state, neorev.NoteKind.QUESTION)

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
            self.term.pick_line_target(state, neorev.NoteKind.QUESTION)

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
        cursor_idx = next(
            (i for i, dl in enumerate(hunk.display_lines) if dl is selectable[cursor]),
            None,
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


class TestNotePreviewText(unittest.TestCase):
    """Tests for note_preview_text."""

    def test_single_line(self) -> None:
        """A single line is returned as-is."""
        self.assertEqual(neorev.note_preview_text("hello world"), "hello world")

    def test_empty_string(self) -> None:
        """An empty string returns empty."""
        self.assertEqual(neorev.note_preview_text(""), "")

    def test_only_whitespace(self) -> None:
        """Whitespace-only input returns empty."""
        self.assertEqual(neorev.note_preview_text("   \n\n  "), "")

    def test_newline_adds_interpunct(self) -> None:
        """Lines without trailing punctuation are joined with interpunct."""
        self.assertEqual(
            neorev.note_preview_text("fix the bug\nalso rename it"),
            "fix the bug · also rename it",
        )

    def test_newline_after_period(self) -> None:
        """A line ending with a period is joined with a plain space."""
        self.assertEqual(
            neorev.note_preview_text("fix the bug.\nalso rename it"),
            "fix the bug. also rename it",
        )

    def test_newline_after_exclamation(self) -> None:
        """A line ending with an exclamation mark is joined with a plain space."""
        self.assertEqual(
            neorev.note_preview_text("done!\nnext step"),
            "done! next step",
        )

    def test_newline_after_question_mark(self) -> None:
        """A line ending with a question mark is joined with a plain space."""
        self.assertEqual(
            neorev.note_preview_text("why?\nbecause"),
            "why? because",
        )

    def test_newline_after_comma(self) -> None:
        """A line ending with a comma is joined with a plain space."""
        self.assertEqual(
            neorev.note_preview_text("first,\nsecond"),
            "first, second",
        )

    def test_newline_after_semicolon(self) -> None:
        """A line ending with a semicolon is joined with a plain space."""
        self.assertEqual(
            neorev.note_preview_text("a;\nb"),
            "a; b",
        )

    def test_newline_after_colon(self) -> None:
        """A line ending with a colon is joined with a plain space."""
        self.assertEqual(
            neorev.note_preview_text("note:\ndetails"),
            "note: details",
        )

    def test_newline_after_ellipsis(self) -> None:
        """A line ending with an ellipsis is joined with a plain space."""
        self.assertEqual(
            neorev.note_preview_text("wait…\nmore"),
            "wait… more",
        )

    def test_newline_after_closing_paren(self) -> None:
        """A line ending with a closing parenthesis is joined with a plain space."""
        self.assertEqual(
            neorev.note_preview_text("(done)\nnext"),
            "(done) next",
        )

    def test_newline_after_closing_bracket(self) -> None:
        """A line ending with a closing bracket is joined with a plain space."""
        self.assertEqual(
            neorev.note_preview_text("[ref]\nsee"),
            "[ref] see",
        )

    def test_newline_after_quote(self) -> None:
        """A line ending with a quote mark is joined with a plain space."""
        self.assertEqual(
            neorev.note_preview_text('said "hi"\nthen left'),
            'said "hi" then left',
        )

    def test_newline_after_em_dash(self) -> None:
        """A line ending with an em dash is joined with a plain space."""
        self.assertEqual(
            neorev.note_preview_text("wait—\nmore"),
            "wait— more",
        )

    def test_consecutive_newlines(self) -> None:
        """Multiple consecutive newlines produce a single interpunct."""
        self.assertEqual(
            neorev.note_preview_text("a\n\n\nb"),
            "a · b",
        )

    def test_consecutive_newlines_after_punctuation(self) -> None:
        """Multiple consecutive newlines after punctuation produce a single space."""
        self.assertEqual(
            neorev.note_preview_text("end.\n\n\nstart"),
            "end. start",
        )

    def test_no_double_spaces(self) -> None:
        """Internal double spaces are collapsed to one."""
        self.assertEqual(
            neorev.note_preview_text("a  b"),
            "a b",
        )

    def test_tabs_collapsed(self) -> None:
        """Tab characters are collapsed to a single space."""
        self.assertEqual(
            neorev.note_preview_text("a\t\tb"),
            "a b",
        )

    def test_unicode_spaces_collapsed(self) -> None:
        """Various unicode whitespace chars are collapsed to a single space."""
        self.assertEqual(
            neorev.note_preview_text("a\u00a0\u2000\u3000b"),
            "a b",
        )

    def test_mixed_punctuated_and_unpunctuated_lines(self) -> None:
        """Mixed lines get interpunct only where needed."""
        self.assertEqual(
            neorev.note_preview_text("done.\nnext\nalso this"),
            "done. next · also this",
        )

    def test_leading_trailing_whitespace_stripped(self) -> None:
        """Leading and trailing whitespace is stripped from the result."""
        self.assertEqual(
            neorev.note_preview_text("  hello  \n  world  "),
            "hello · world",
        )

    def test_trailing_newline(self) -> None:
        """A trailing newline does not produce a trailing interpunct."""
        self.assertEqual(
            neorev.note_preview_text("hello\n"),
            "hello",
        )

    def test_leading_newline(self) -> None:
        """A leading newline does not produce a leading interpunct."""
        self.assertEqual(
            neorev.note_preview_text("\nhello"),
            "hello",
        )


class TestReviewIsAllClear(unittest.TestCase):
    """Tests for review_is_all_clear."""

    def test_all_approved_no_globals(self) -> None:
        """Return True when every hunk is approved and no global notes exist."""
        hunks = [
            make_hunk(approved=True),
            make_hunk(approved=True),
        ]
        self.assertTrue(neorev.review_is_all_clear(hunks, []))

    def test_unapproved_hunk(self) -> None:
        """Return False when at least one hunk is not approved."""
        hunks = [
            make_hunk(approved=True),
            make_hunk(),
        ]
        self.assertFalse(neorev.review_is_all_clear(hunks, []))

    def test_all_approved_with_global_notes(self) -> None:
        """Return False when there are global notes even if all hunks are approved."""
        hunks = [make_hunk(approved=True)]
        notes = [neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="concern")]
        self.assertFalse(neorev.review_is_all_clear(hunks, notes))

    def test_hunk_with_notes(self) -> None:
        """Return False when a hunk has notes instead of approval."""
        hunks = [make_hunk(status=neorev.Status.FLAG, comment="fix")]
        self.assertFalse(neorev.review_is_all_clear(hunks, []))


class TestAllClearSkipsFile(unittest.TestCase):
    """Workflow tests: all-clear review skips output file creation."""

    def test_all_approved_skips_file_and_prints_message(self) -> None:
        """When all hunks are approved with no global notes, no file is written."""

        def script(state: neorev.ReviewState) -> None:
            """Approve all hunks."""
            for hunk in state.hunks:
                hunk.approved = True

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        os.unlink(output_path)
        try:
            stderr = run_main_with_scripted_terminal(
                TWO_HUNK_DIFF,
                output_path,
                script,
            )
            self.assertFalse(
                Path(output_path).exists(),
                "Output file should not be created when review is all clear",
            )
            self.assertIn(neorev.ALL_CLEAR_MESSAGE.strip(), stderr)
        finally:
            if Path(output_path).exists():
                os.unlink(output_path)

    def test_all_approved_with_global_note_writes_file(self) -> None:
        """When all hunks are approved but a global note exists, file is written."""

        def script(state: neorev.ReviewState) -> None:
            """Approve all hunks and add a global note."""
            for hunk in state.hunks:
                hunk.approved = True
            state.global_notes.append(
                neorev.GlobalNote(kind=neorev.NoteKind.QUESTION, text="why?")
            )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        try:
            stderr = run_main_with_scripted_terminal(
                TWO_HUNK_DIFF,
                output_path,
                script,
            )
            self.assertTrue(
                Path(output_path).exists(),
                "Output file should be written when global notes exist",
            )
            self.assertIn("Review written to", stderr)
        finally:
            os.unlink(output_path)

    def test_partial_approval_writes_file(self) -> None:
        """When not all hunks are approved, file is written."""

        def script(state: neorev.ReviewState) -> None:
            """Approve only the first hunk."""
            state.hunks[0].approved = True

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        try:
            run_main_with_scripted_terminal(
                TWO_HUNK_DIFF,
                output_path,
                script,
            )
            self.assertTrue(Path(output_path).exists())
        finally:
            os.unlink(output_path)

    def test_immediate_exit_skips_file(self) -> None:
        """Exiting without approving or annotating anything writes no output."""

        def script(state: neorev.ReviewState) -> None:
            """Do nothing — simulate an immediate exit."""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        os.unlink(output_path)
        try:
            run_main_with_scripted_terminal(
                TWO_HUNK_DIFF,
                output_path,
                script,
            )
            self.assertFalse(
                Path(output_path).exists(),
                "Output file should not be created when no review actions were taken",
            )
        finally:
            if Path(output_path).exists():
                os.unlink(output_path)


class TestReviewHasContent(unittest.TestCase):
    """Tests for review_has_content."""

    def test_no_actions_taken(self) -> None:
        """Return False when no hunks are approved and no notes exist."""
        hunks = [make_hunk(), make_hunk()]
        self.assertFalse(neorev.review_has_content(hunks, []))

    def test_one_approved(self) -> None:
        """Return True when at least one hunk is approved."""
        hunks = [make_hunk(approved=True), make_hunk()]
        self.assertTrue(neorev.review_has_content(hunks, []))

    def test_all_approved(self) -> None:
        """Return True when all hunks are approved."""
        hunks = [make_hunk(approved=True), make_hunk(approved=True)]
        self.assertTrue(neorev.review_has_content(hunks, []))

    def test_hunk_with_notes(self) -> None:
        """Return True when a hunk has notes."""
        hunks = [make_hunk(status=neorev.Status.FLAG, comment="fix")]
        self.assertTrue(neorev.review_has_content(hunks, []))

    def test_global_notes_only(self) -> None:
        """Return True when only global notes exist."""
        hunks = [make_hunk()]
        notes = [neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="concern")]
        self.assertTrue(neorev.review_has_content(hunks, notes))


class TestCompactPath(unittest.TestCase):
    """Tests for compact_path: replaces home directory prefix with ~."""

    def test_path_under_home(self) -> None:
        """Replace home prefix with ~ for a path under the home directory."""
        home = str(Path.home())
        self.assertEqual(
            neorev.compact_path(f"{home}/foo/bar.md"),
            "~/foo/bar.md",
        )

    def test_home_itself(self) -> None:
        """Replace the home directory path itself with ~."""
        home = str(Path.home())
        self.assertEqual(neorev.compact_path(home), "~")

    def test_path_outside_home(self) -> None:
        """Leave paths outside the home directory unchanged."""
        self.assertEqual(neorev.compact_path("/etc/foo.md"), "/etc/foo.md")

    def test_partial_prefix_not_replaced(self) -> None:
        """Do not replace when home is only a partial prefix of a component."""
        home = str(Path.home())
        path = f"{home}extra/file.md"
        self.assertEqual(neorev.compact_path(path), path)


if __name__ == "__main__":
    unittest.main()
