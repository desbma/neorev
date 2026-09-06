"""Shared test helpers for neorev."""

import contextlib
import fcntl
import importlib.machinery
import importlib.util
import io
import os
import re
import select
import struct
import sys
import termios
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Self
from unittest.mock import patch

# neorev is a script without .py extension; import it as a module.
NEOREV_PATH = str(Path(__file__).resolve().parents[1] / "neorev")
NEOREV_LOADER = importlib.machinery.SourceFileLoader("neorev", NEOREV_PATH)
neorev = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec("neorev", NEOREV_LOADER, origin=NEOREV_PATH)
)
# Must precede exec_module: dataclass creation and mock.patch("neorev.<name>")
# targets both resolve the module through sys.modules.
sys.modules["neorev"] = neorev
NEOREV_LOADER.exec_module(neorev)

ANSI_ESCAPE_TEXT_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
# Fixtures spell out range lines loosely; only their start offsets are read back.
FIXTURE_RANGE_RE = re.compile(r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")
FIXTURE_DIFF_HEADER = "diff --git a/f b/f\n--- a/f\n+++ b/f\n"
ADDED_LINE_PREFIX = "+"
REMOVED_LINE_PREFIX = "-"
META_LINE_PREFIX = "\\"
TERM_WIDTH = 80
TERM_HEIGHT = 24
TERM_PIXEL_SIZE = 0  # pixel dimensions unused by tests
WINSIZE_FORMAT = "HHHH"
READ_BUFFER_SIZE = 8192
SELECT_TIMEOUT = 0.1
READ_DRAIN_TIMEOUT = 0.01
READ_DRAIN_EMPTY_POLLS = 3
TINY_WIDTH = 5
WIDE_EAST_ASIAN_WIDTHS = ("W", "F")
WIDE_CHARACTER_SAMPLE = "漢字"
WIDE_CHARACTER_CELL_WIDTH = 4
EMOJI_SAMPLE = "🚀"
WIDE_GLYPH_SAMPLES = (WIDE_CHARACTER_SAMPLE, EMOJI_SAMPLE)
# Heart plus variation selector: one grapheme, two cells, two code points.
GRAPHEME_CLUSTER_SAMPLE = "❤️"
# Backgrounds delta paints removed lines, changed words and added lines with.
DELTA_REMOVED_BACKGROUND = "\x1b[48;2;63;0;1m"
DELTA_WORD_BACKGROUND = "\x1b[48;2;144;16;17m"
DELTA_ADDED_BACKGROUND = "\x1b[48;2;0;40;0m"
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
WORKFLOW_FENCED_QUESTION = "Why replace the command here?"
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
@@ -0,0 +1,2 @@
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

RENAME_FILE_DIFF = """\
diff --git a/old.txt b/new.txt
rename from old.txt
rename to new.txt
--- a/old.txt
+++ b/new.txt
@@ -2,3 +2,3 @@
 b
-c
+C
 d
"""

PURE_RENAME_DIFF = """\
diff --git a/old.txt b/new.txt
rename from old.txt
rename to new.txt
"""

TWO_DELETED_FILES_DIFF = """\
diff --git a/first.py b/first.py
deleted file mode 100644
--- a/first.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def first():
-    pass
diff --git a/second.py b/second.py
deleted file mode 100644
--- a/second.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def second():
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
@@ -10 +10,2 @@ def foo():
     pass
+    return 0
"""

MULTI_FILE_MULTI_HUNK_DIFF = """\
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1,2 @@
 x = 1
+y = 2
@@ -10 +11,2 @@
 z = 3
+w = 4
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -1 +1,2 @@
 a = 1
+b = 2
@@ -20 +21,2 @@
 c = 3
+d = 4
diff --git a/c.py b/c.py
--- a/c.py
+++ b/c.py
@@ -1 +1,2 @@
 e = 1
+f = 2
@@ -30 +31,2 @@
 g = 3
+h = 4
"""

FENCED_BODY_DIFF = """\
diff --git a/SKILL.md b/SKILL.md
--- a/SKILL.md
+++ b/SKILL.md
@@ -1,5 +1,5 @@
 Run this:

 ```bash
-old command
+new command
 ```
"""

INDENTED_FENCE_BODY_DIFF = """\
diff --git a/indented.md b/indented.md
--- a/indented.md
+++ b/indented.md
@@ -1,5 +1,5 @@
 1. Run this:

       ```bash
-      old command
+      new command
       ```
"""

WIDE_FENCE_BODY_DIFF = """\
diff --git a/nested.md b/nested.md
--- a/nested.md
+++ b/nested.md
@@ -1,5 +1,5 @@
 ````markdown
 ```python
-x = 1
+x = 2
 ```
 ````
"""

HEADING_BODY_DIFF = """\
diff --git a/doc.md b/doc.md
--- a/doc.md
+++ b/doc.md
@@ -1,2 +1,2 @@
 ### Section
-old
+new
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
@@ -10 +11,2 @@
     pass
+    return 0
"""

TWO_FILE_DIFF = """\
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1,2 @@
 x = 1
+y = 2
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -1 +1,2 @@
 a = 1
+b = 2
"""

GLOBAL_PATH_DIFF = """\
diff --git a/global b/global
--- a/global
+++ b/global
@@ -1 +1,2 @@
 x = 1
+y = 2
"""

SEPARATOR_PATH_DIFF = """\
diff --git a/d @ r.py b/d @ r.py
--- a/d @ r.py
+++ b/d @ r.py
@@ -1 +1,2 @@
 x = 1
+y = 2
"""

CRLF_DIFF = (
    "diff --git a/crlf.py b/crlf.py\r\n"
    "--- a/crlf.py\r\n"
    "+++ b/crlf.py\r\n"
    "@@ -1 +1,2 @@\r\n"
    " x = 1\r\n"
    "+y = 2\r\n"
)

# GNU 'diff -u' output: file markers with no 'diff --git' line before them.
NO_GIT_HEADER_DIFF = """\
--- old/a.txt
+++ new/a.txt
@@ -1,3 +1,3 @@
 one
-two
+two modified
 three
"""

# GNU 'diff -ur' output: file markers carrying a tab-separated timestamp.
TIMESTAMPED_DIFF = (
    "diff -ur old/a.txt new/a.txt\n"
    "--- old/a.txt\t2026-01-01 00:00:00.000000000 +0000\n"
    "+++ new/a.txt\t2026-01-01 00:00:00.000000000 +0000\n"
    "@@ -1,3 +1,3 @@\n"
    " one\n"
    "-two\n"
    "+two modified\n"
    " three\n"
)

# Git quotes a path holding non-ASCII bytes, escaping them octally.
QUOTED_PATH_NAME = "caf\\303\\251 menu.txt"
QUOTED_PATH_DIFF = (
    f'diff --git "a/{QUOTED_PATH_NAME}" "b/{QUOTED_PATH_NAME}"\n'
    "new file mode 100644\n"
    "--- /dev/null\n"
    f'+++ "b/{QUOTED_PATH_NAME}"\n'
    "@@ -0,0 +1 @@\n"
    "+starter\n"
)

# A diff whose text carries a blank line past the end of its last hunk.
TRAILING_BLANK_DIFF = """\
diff --git a/f.py b/f.py
--- a/f.py
+++ b/f.py
@@ -1 +1 @@
-old
+new

"""

# Emptying a tracked file: both markers name it, and the target side is empty.
EMPTIED_FILE_DIFF = """\
diff --git a/f.py b/f.py
--- a/f.py
+++ b/f.py
@@ -1 +0,0 @@
-content
"""

# An edit to a file whose own name spells out git's rename extended header.
RENAME_PHRASE_PATH = "rename from x"
RENAME_PHRASE_DIFF = (
    f"diff --git a/{RENAME_PHRASE_PATH} b/{RENAME_PHRASE_PATH}\n"
    f"--- a/{RENAME_PHRASE_PATH}\n"
    f"+++ b/{RENAME_PHRASE_PATH}\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)

# 'difflib.unified_diff' called without filenames: markers present, names empty.
UNNAMED_PATH_DIFF = "--- \n+++ \n@@ -1 +1 @@\n-old\n+new\n"

# GNU diff's standalone binary notice, carrying no target marker.
STANDALONE_BINARY_DIFF = "Binary file image.bin has changed\n"


def hunk_line_counts(body: str) -> tuple[int, int]:
    """Count the source and target lines *body* holds."""
    source = target = 0
    for line in body.splitlines():
        if line.startswith(META_LINE_PREFIX):
            continue
        source += not line.startswith(ADDED_LINE_PREFIX)
        target += not line.startswith(REMOVED_LINE_PREFIX)
    return source, target


def parse_display_lines(range_line: str, body: str) -> list[neorev.DisplayLine]:
    """Build the display lines of a hunk fixture, fixing up its range counts."""
    match = FIXTURE_RANGE_RE.match(range_line)
    if match is None:
        return []
    source, target = hunk_line_counts(body)
    diff = (
        f"{FIXTURE_DIFF_HEADER}"
        f"@@ -{match['old']},{source} +{match['new']},{target} @@\n{body}\n"
    )
    return neorev.parse_diff(diff)[0].display_lines


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
        range_line=range_line,
        body=body,
        raw=f"diff --git a/{file_path} b/{file_path}\n{range_line}\n{body}",
        file_path=file_path,
        start_line=start_line,
        display_lines=parse_display_lines(range_line, body),
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


REOPEN_CYCLE_COUNT = 3

# Note texts a reviewer may plausibly type, each exercising a token the review
# file format also uses for its own structure.
ROUND_TRIP_NOTE_TEXTS = {
    "plain": "just fix it",
    "multiline": "first line\nsecond line\nthird line",
    "markdown heading": "### Why this?\n\nBecause reasons.",
    "leading markdown heading": "### heading first",
    "diff block": "Try instead:\n\n```diff\n-a\n+b\n```\n\nDoes that work?",
    "only a diff block": "```diff\n-a\n+b\n```",
    "code block": "Use:\n\n```python\nx = 1\n```",
    "wide fence": "````\n```\n````",
    "bare fence line": "```",
    "range line": "@@ -1,2 +1,3 @@ is the wrong hunk",
    "anchor comment": "see <!-- neorev: note-anchor=abc -->",
    "anchor comment on its own line": (
        "before\n<!-- neorev: note-anchor=abc -->\nafter"
    ),
    "section header line": "[QUESTION] `foo.py @ hunk`",
    "unprefixed section heading": "### [QUESTION] `foo.py @ hunk`",
    "special characters": "Use `foo()` — see [docs](url), émojis 🎉 and <angle>.",
    "leading anchor comment": "<!-- neorev: note-anchor=abc -->\n\nreal text",
    "only an anchor comment": "<!-- neorev: note-anchor=abc -->",
    "approved-hashes footer line": (
        "quoting the footer:\n<!-- neorev: approved-hashes=AAAAAAAAAAA= -->\ntail"
    ),
}

# Diffs whose hunks stress how a note is keyed back to its hunk on reload.
HUNK_IDENTITY_DIFFS = {
    "file named global": GLOBAL_PATH_DIFF,
    "separator in path": SEPARATOR_PATH_DIFF,
    "pure rename": PURE_RENAME_DIFF,
    "crlf line endings": CRLF_DIFF,
}


def reopen_review(
    diff_text: str,
    hunks: list[neorev.Hunk],
    global_notes: list[neorev.GlobalNote],
    path: str,
) -> tuple[list[neorev.Hunk], list[neorev.GlobalNote], str]:
    """Write the review to *path*, then reload it onto a fresh parse of *diff_text*."""
    output = neorev.format_output(hunks, global_notes)
    Path(path).write_text(output)
    annotations, loaded_notes, _ = neorev.load_previous_review(path)
    reloaded = neorev.parse_diff(diff_text)
    neorev.apply_previous_review(reloaded, annotations)
    return reloaded, loaded_notes, output


def remove_ansi_escape_sequences(text: str) -> str:
    """Return *text* with ANSI escape sequences removed."""
    return ANSI_ESCAPE_TEXT_RE.sub("", text)


def terminal_cell_width(text: str) -> int:
    """Return the terminal column count of *text*, counting wide characters as two."""
    return sum(
        2 if unicodedata.east_asian_width(char) in WIDE_EAST_ASIAN_WIDTHS else 1
        for char in text
    )


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


ANCHOR_BODY_ORIGINAL = "+first\n+second"
ANCHOR_BODY_CHANGED = "+first\n+second-modified"
ANCHOR_RANGE_LINE = "@@ -1,1 +1,3 @@"
ANCHOR_NOTE_TEXT = "look here"
ANCHOR_LEGACY_COMMENT = "legacy note"

LARGE_BODY_LINE_COUNT = 100


def make_large_diff(line_count: int = LARGE_BODY_LINE_COUNT) -> str:
    """Build a synthetic diff with *line_count* added lines."""
    header = (
        "diff --git a/big.txt b/big.txt\n"
        "--- a/big.txt\n"
        "+++ b/big.txt\n"
        f"@@ -0,0 +1,{line_count} @@\n"
    )
    body = "".join(f"+line {i}\n" for i in range(line_count))
    return header + body


CENTERED_SNIPPET_LINE_COUNT = 20
CENTERED_SNIPPET_TARGET_LINE = 10
