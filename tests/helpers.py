"""Shared test helpers for neorev."""

import asyncio
import contextlib
import dataclasses
import importlib.machinery
import importlib.util
import io
import os
import re
import sys
import unittest
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from unittest.mock import patch

from rich.segment import Segment
from rich.text import Text
from textual import _wait as textual_wait
from textual.geometry import Region
from textual.pilot import Pilot
from textual.widget import Widget
from textual.widgets import OptionList, Static

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

# Fixtures spell out range lines loosely; only their start offsets are read back.
FIXTURE_RANGE_RE = re.compile(r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")
FIXTURE_DIFF_HEADER = "diff --git a/f b/f\n--- a/f\n+++ b/f\n"
ADDED_LINE_PREFIX = "+"
REMOVED_LINE_PREFIX = "-"
META_LINE_PREFIX = "\\"
TERM_WIDTH = 80
TERM_HEIGHT = 24
FAKE_EDITOR = "true"
# Backgrounds delta paints removed lines, changed words and added lines with.
DELTA_REMOVED_BACKGROUND = "\x1b[48;2;63;0;1m"
DELTA_WORD_BACKGROUND = "\x1b[48;2;144;16;17m"
DELTA_ADDED_BACKGROUND = "\x1b[48;2;0;40;0m"
NARROW_PROGRESS_WIDTH = 40
LONG_BODY_LINE_COUNT = 20
MANY_HUNKS_COUNT = 100
OVERFLOW_HUNK_INDEX = 50
MANY_APPROVED_COUNT = 20
MULTI_FILE_HUNK_COUNT = 6
HUNKS_PER_FILE = 2
TOP_BAR_INDEX_TOKEN = "Hunk 1/5"
REVIEW_SCREEN_INDEX_TOKEN = "Hunk 1/1"
REVIEW_SCREEN_LOCATION_TOKEN = "hello.py:1"
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
NOTE_EDIT_KEY = "e"
NOTE_DELETE_KEY = "d"
GLOBAL_NOTE_ADD_PREFIX = "g"
GLOBAL_NOTE_ADD_QUESTION_KEY = "c"
GLOBAL_NOTE_ADD_FLAG_KEY = "f"
DISPATCH_COMMENT_TEXT = "needs reviewer context"
ADDED_LINE_NUMBER = 2
REMOVED_LINE_NUMBER = 1
LINE_TARGET_NOTE_LINE = 42
LINE_TARGET_NOTE_TEXT = "fix the off-by-one"
GLOBAL_PARSE_NOTE_TEXT = "overall design concern"
LINE_TARGET_APPLY_TEXT = "adjust this import"
UPSERT_NOTE_TEXT = "initial note"
UPSERT_NOTE_UPDATED_TEXT = "updated note"
LINE_PICKER_MANY_LINES = 30
MOCK_OUTPUT_PATH = "/mock/output/review.md"
# Slice textual's pilot sleeps for while polling the process clock for idleness.
IDLE_POLL_GRANULARITY_S = 0.005

textual_wait.SLEEP_GRANULARITY = IDLE_POLL_GRANULARITY_S


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


NOTE_KIND_OF_STATUS = {
    neorev.Status.FLAG: neorev.NoteKind.FLAG,
    neorev.Status.QUESTION: neorev.NoteKind.QUESTION,
}


def make_hunk(  # noqa: PLR0913
    file_path: str = "test.py",
    start_line: int = 1,
    body: str = "+added line",
    status: neorev.Status | None = None,
    comment: str = "",
    *,
    notes: list[neorev.HunkNote] | None = None,
    range_line: str = "",
) -> neorev.Hunk:
    """Create a Hunk with sensible defaults for testing."""
    if not range_line:
        range_line = f"@@ -1,3 +{start_line},4 @@"
    kind = None if status is None else NOTE_KIND_OF_STATUS.get(status)
    if notes is None and kind is not None:
        notes = [neorev.HunkNote(kind=kind, target=neorev.HunkTarget(), text=comment)]
    return neorev.Hunk(
        range_line=range_line,
        body=body,
        raw=f"diff --git a/{file_path} b/{file_path}\n{range_line}\n{body}",
        file_path=file_path,
        start_line=start_line,
        approved=status is neorev.Status.APPROVED,
        notes=[] if notes is None else notes,
        display_lines=parse_display_lines(range_line, body),
    )


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


class FakeEditor:
    """Stand-in for the ``$EDITOR`` round trip, recording what it was asked to edit."""

    def __init__(self, *texts: str) -> None:
        """Store *texts* as the replies handed back in turn, the last one repeating."""
        self.texts = list(texts) or [""]
        self.calls: list[tuple[str, str, list[str] | None]] = []

    def __call__(
        self,
        existing: str,
        location: str,
        context_lines: list[str] | None = None,
    ) -> str:
        """Record the request and return the next scripted text."""
        self.calls.append((existing, location, context_lines))
        return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]


class ReviewTestCase(unittest.IsolatedAsyncioTestCase):
    """Async test case whose event loop skips asyncio's debug bookkeeping."""

    async def asyncSetUp(self) -> None:
        """Turn the debug mode of the runner's event loop off."""
        asyncio.get_running_loop().set_debug(False)


@dataclasses.dataclass
class ReviewDriver:
    """A running review app, the pilot driving it and the editor it opens."""

    app: neorev.ReviewApp
    pilot: Pilot[None]
    editor: FakeEditor

    @property
    def state(self) -> neorev.ReviewState:
        """Return the review state the app is showing."""
        return self.app.state

    async def settle(self) -> None:
        """Wait until no worker runs and no widget has a message left queued."""
        while True:
            while self.app.workers:
                await self.app.workers.wait_for_complete()
            await self.pilot.pause()
            if not self.app.workers and not self.pending_messages():
                return

    def pending_messages(self) -> int:
        """Count the messages the app and the widgets it shows still have queued."""
        pumps = [self.app, *self.app.screen.walk_children(with_self=True)]
        return sum(pump.message_queue_size for pump in pumps)

    async def press(self, *keys: str) -> None:
        """Send *keys* to the app and let it settle."""
        await self.pilot.press(*keys)
        await self.settle()

    async def resize(self, width: int, height: int) -> None:
        """Resize the terminal and let the app redraw."""
        await self.pilot.resize_terminal(width, height)
        await self.settle()

    def chrome_text(self, widget_type: type[Static]) -> str:
        """Return the text the chrome widget of *widget_type* draws."""
        return rendered_text(self.app.query_one(widget_type))

    def diff_rows(self) -> list[str]:
        """Return the text of every row the diff view holds."""
        return [strip.text for strip in self.app.query_one(neorev.Diff).lines]

    def option_texts(self) -> list[str]:
        """Return the prompt text of every option of the visible list."""
        return option_texts(self.app.screen.query_one(OptionList))


def rendered_text(widget: Static) -> str:
    """Return the plain text *widget* draws."""
    rendered = widget.render()
    return rendered.plain if isinstance(rendered, Text) else str(rendered)


def rendered_segments(widget: Widget) -> list[Segment]:
    """Return the styled segments *widget* paints at its current size."""
    region = Region(0, 0, widget.size.width, widget.size.height)
    return [segment for strip in widget.render_lines(region) for segment in strip]


def option_texts(options: OptionList) -> list[str]:
    """Return the prompt text of every option of *options*."""
    return [str(option.prompt) for option in options.options]


@contextlib.asynccontextmanager
async def review_session(
    diff_text: str,
    editor: FakeEditor | None = None,
    size: tuple[int, int] = (TERM_WIDTH, TERM_HEIGHT),
    output_path: str = MOCK_OUTPUT_PATH,
    watch: neorev.JjSource | None = None,
) -> AsyncIterator[ReviewDriver]:
    """Run a review app over *diff_text* and yield a driver for it."""
    hunks = neorev.parse_diff(diff_text)
    state = neorev.ReviewState(
        hunks=hunks,
        global_notes=[],
        current_index=neorev.ReviewState.initial_index(hunks),
    )
    session = neorev.Session(
        output_path=output_path,
        diff_source=None,
        diff_text=diff_text,
        watch=watch,
    )
    app = neorev.ReviewApp(state, session)
    fake = FakeEditor() if editor is None else editor
    with (
        patch.object(neorev.ReviewApp, "edit_note_text", fake),
        # Keep the repository watcher out of the tests: no jj probe, no inotifywait.
        patch.object(neorev, "jj_root", return_value=None),
    ):
        async with app.run_test(size=size) as pilot:
            driver = ReviewDriver(app=app, pilot=pilot, editor=fake)
            await driver.settle()
            yield driver


def run_main_with_scripted_app(
    diff_text: str,
    output_path: str,
    script: Callable[[neorev.ReviewState], None],
    extra_args: list[str] | None = None,
) -> str:
    """Run neorev.main() with *script* standing in for the UI, returning stderr."""
    stderr = io.StringIO()
    argv = ["neorev", *(extra_args or []), "-o", output_path]

    def run(app: neorev.ReviewApp, *, mouse: bool) -> None:  # noqa: ARG001
        """Apply the scripted state mutations instead of starting the UI."""
        script(app.state)

    with (
        patch.object(neorev.ReviewApp, "run", run),
        patch.dict(os.environ, {"EDITOR": FAKE_EDITOR}),
        patch.object(sys, "argv", argv),
        patch.object(sys, "stdin", io.StringIO(diff_text)),
        contextlib.redirect_stderr(stderr),
    ):
        neorev.main()
    return stderr.getvalue()


ANCHOR_BODY_ORIGINAL = "+first\n+second"
ANCHOR_BODY_CHANGED = "+first\n+second-modified"
ANCHOR_RANGE_LINE = "@@ -1,1 +1,3 @@"
ANCHOR_NOTE_TEXT = "look here"
ANCHOR_LEGACY_COMMENT = "legacy note"

LARGE_BODY_LINE_COUNT = 100
LARGE_FILE_NAME = "big.txt"
SECOND_LARGE_FILE_NAME = "other.txt"


def make_large_diff(
    line_count: int = LARGE_BODY_LINE_COUNT,
    name: str = LARGE_FILE_NAME,
) -> str:
    """Build a synthetic diff adding *line_count* lines to *name*."""
    header = (
        f"diff --git a/{name} b/{name}\n"
        f"--- a/{name}\n"
        f"+++ b/{name}\n"
        f"@@ -0,0 +1,{line_count} @@\n"
    )
    body = "".join(f"+line {i}\n" for i in range(line_count))
    return header + body


WIDE_BODY_LINE_COUNT = 30
WIDE_LINE_WORD_COUNT = 20
WIDE_FILE_NAME = "wide.txt"
# The number the text of every line of a wide diff opens with.
WIDE_LINE_NUMBER_FORMAT = "line {index:02d}"


def make_wide_diff(
    line_count: int = WIDE_BODY_LINE_COUNT,
    words: int = WIDE_LINE_WORD_COUNT,
) -> str:
    """Build a synthetic diff whose added lines are wider than the terminal."""
    header = (
        f"diff --git a/{WIDE_FILE_NAME} b/{WIDE_FILE_NAME}\n"
        f"--- a/{WIDE_FILE_NAME}\n"
        f"+++ b/{WIDE_FILE_NAME}\n"
        f"@@ -0,0 +1,{line_count} @@\n"
    )
    filler = " ".join(f"word{i}" for i in range(words))
    body = "".join(
        f"+{WIDE_LINE_NUMBER_FORMAT.format(index=i)} {filler}\n"
        for i in range(line_count)
    )
    return header + body


CENTERED_SNIPPET_LINE_COUNT = 20
CENTERED_SNIPPET_TARGET_LINE = 10
