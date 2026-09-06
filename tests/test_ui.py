"""Tests driving the review app through its keyboard interface."""

import os
import unittest
from unittest.mock import patch

from rich.color import ColorType
from rich.segment import Segment
from rich.style import Style
from textual.widgets import Footer, HelpPanel, OptionList, Static
from textual.widgets._footer import FooterKey
from textual.widgets._key_panel import BindingsTable

from tests.helpers import (
    DISPATCH_COMMENT_TEXT,
    GLOBAL_NOTE_ADD_FLAG_KEY,
    GLOBAL_NOTE_ADD_PREFIX,
    GLOBAL_NOTE_ADD_QUESTION_KEY,
    GLOBAL_NOTE_CREATED_TEXT,
    GLOBAL_NOTE_EDITED_TEXT,
    LINE_PICKER_MANY_LINES,
    NOTE_DELETE_KEY,
    NOTE_EDIT_KEY,
    PURE_RENAME_DIFF,
    REVIEW_SCREEN_INDEX_TOKEN,
    REVIEW_SCREEN_LOCATION_TOKEN,
    SECOND_LARGE_FILE_NAME,
    SIMPLE_DIFF,
    TERM_HEIGHT,
    TERM_WIDTH,
    TWO_FILE_DIFF,
    TWO_HUNK_DIFF,
    FakeEditor,
    ReviewDriver,
    ReviewTestCase,
    make_large_diff,
    neorev,
    rendered_segments,
    rendered_text,
    review_session,
)

APPROVE_KEY = "a"
APPROVE_FILE_KEY = "A"
QUESTION_KEY = "c"
FLAG_KEY = "f"
NOTES_KEY = "m"
QUIT_KEY = "q"
HELP_KEY = "question_mark"
NEXT_KEY = "j"
PREVIOUS_KEY = "k"
SELECT_KEY = "enter"
WHOLE_HUNK_KEY = "h"
CANCEL_KEY = "escape"
SCROLL_DOWN_KEY = "ctrl+d"
SCROLL_UP_KEY = "ctrl+u"
QUIT_INTERRUPT_KEY = "ctrl+c"
INTERRUPT_KEY_DISPLAY = "^c"
PALETTE_KEY = "ctrl+p"
# Keys the framework binds on every screen of its own accord.
FRAMEWORK_KEYS = ("tab", "shift+tab", "super+c", "ctrl+q")

NEXT_KEY_DISPLAY = "j/↓"
PREVIOUS_KEY_DISPLAY = "k/↑"
REVIEW_FOOTER_HINTS = [
    (NEXT_KEY_DISPLAY, "next hunk"),
    (PREVIOUS_KEY_DISPLAY, "previous hunk"),
    ("a", "approve"),
    ("A", "approve file"),
    ("c", "question"),
    ("f", "flag"),
    ("g", "global"),
    ("m", "notes"),
    ("q", "save & quit"),
    ("?", "help"),
]
NOTES_FOOTER_HINTS = [
    (NEXT_KEY_DISPLAY, "next"),
    (PREVIOUS_KEY_DISPLAY, "previous"),
    ("e/⏎", "edit"),
    ("d", "delete"),
    ("esc/q", "close"),
]
PICKER_FOOTER_HINTS = [
    (NEXT_KEY_DISPLAY, "next"),
    (PREVIOUS_KEY_DISPLAY, "previous"),
    ("⏎", "pick line"),
    ("h", "whole hunk"),
    ("esc/q", "cancel"),
]
GLOBAL_NOTE_FOOTER_HINTS = [
    ("c", "question"),
    ("f", "change request"),
    ("esc/q", "cancel"),
]

PICKER_NOTE_TEXT = "look at this line"
HUNK_NOTE_EDITED_TEXT = "edited hunk note"
PAINT_BACKLOG_LINES = 4000
SHORT_HEIGHT = 10
NARROW_WIDTH = 40

BRAND_TOKEN = "neorev"
HUNK_TOKEN = "Hunk"
CURSOR_LINE_TOKEN = "import os"
CHROME_INDEX = 0
YELLOW_INDEX = 3
BLUE_INDEX = 4
# Colour the terminal resolves itself, as opposed to a palette entry.
DEFAULT_TYPE = ColorType.DEFAULT
# Colours the terminal resolves itself: a palette entry, or its own default.
PALETTE_COLOUR_TYPES = (ColorType.STANDARD, ColorType.DEFAULT)


class TestNavigation(ReviewTestCase):
    """Tests for moving between hunks."""

    async def test_first_hunk_is_shown(self) -> None:
        """Verify the app opens on the first hunk with its chrome filled in."""
        async with review_session(SIMPLE_DIFF) as review:
            top_bar = review.chrome_text(neorev.TopBar)
            self.assertIn(REVIEW_SCREEN_INDEX_TOKEN, top_bar)
            self.assertIn(REVIEW_SCREEN_LOCATION_TOKEN, top_bar)

    async def test_next_and_previous_hunk(self) -> None:
        """Verify j and k walk the hunks and stop at both ends."""
        async with review_session(TWO_HUNK_DIFF) as review:
            await review.press(NEXT_KEY)
            self.assertEqual(review.state.current_index, 1)
            await review.press(NEXT_KEY)
            self.assertEqual(review.state.current_index, 1)
            await review.press(PREVIOUS_KEY)
            self.assertEqual(review.state.current_index, 0)
            await review.press(PREVIOUS_KEY)
            self.assertEqual(review.state.current_index, 0)

    async def test_arrow_keys_move_between_hunks(self) -> None:
        """Verify the arrow keys navigate like j and k."""
        async with review_session(TWO_HUNK_DIFF) as review:
            await review.press("down")
            self.assertEqual(review.state.current_index, 1)
            await review.press("up")
            self.assertEqual(review.state.current_index, 0)

    async def test_moving_shows_the_new_hunk(self) -> None:
        """Verify the diff view and chrome follow the hunk the cursor is on."""
        async with review_session(TWO_HUNK_DIFF) as review:
            await review.press(NEXT_KEY)
            self.assertIn("Hunk 2/2", review.chrome_text(neorev.TopBar))
            self.assertIn("return 0", "\n".join(review.diff_rows()))

    async def test_unknown_key_changes_nothing(self) -> None:
        """Verify a key with no binding leaves the review untouched."""
        async with review_session(TWO_HUNK_DIFF) as review:
            await review.press("z")
            self.assertEqual(review.state.current_index, 0)
            self.assertFalse(review.state.hunks[0].is_handled)


class TestDiffView(unittest.TestCase):
    """Tests for the delta output shown for the current hunk."""

    def test_delta_output_drops_its_framing_blank_lines(self) -> None:
        """Verify the blank lines delta frames its output with are dropped."""
        self.assertEqual(neorev.split_delta_output(b"\none\ntwo\n"), [b"one", b"two"])

    def test_output_without_framing_is_kept_whole(self) -> None:
        """Verify output that carries no blank framing keeps every line."""
        self.assertEqual(neorev.split_delta_output(b"one"), [b"one"])

    def test_a_carriage_return_does_not_start_a_row(self) -> None:
        """Verify only a newline breaks a row, so rows stay aligned with the diff."""
        self.assertEqual(
            neorev.split_delta_output(b"\nkeep\rtogether\napart\n"),
            [b"keep\rtogether", b"apart"],
        )


class TestDiffRendering(ReviewTestCase):
    """Tests for what the diff view holds."""

    async def test_hunk_body_reaches_the_view(self) -> None:
        """Verify the lines of the current hunk are rendered."""
        async with review_session(SIMPLE_DIFF) as review:
            self.assertIn("import os", "\n".join(review.diff_rows()))

    async def test_rows_fill_the_width(self) -> None:
        """Verify every rendered row spans the whole diff view."""
        async with review_session(SIMPLE_DIFF) as review:
            width = review.app.query_one(neorev.Diff).scrollable_content_region.width
            for row in review.diff_rows():
                self.assertEqual(len(row), width)

    async def test_resize_rewraps_the_diff(self) -> None:
        """Verify a narrower terminal re-lays the cached delta output out."""
        async with review_session(make_large_diff()) as review:
            before = len(review.diff_rows())
            await review.resize(NARROW_WIDTH, TERM_HEIGHT)
            width = review.app.query_one(neorev.Diff).scrollable_content_region.width
            self.assertLess(width, TERM_WIDTH)
            for row in review.diff_rows():
                self.assertEqual(len(row), width)
            self.assertEqual(len(review.diff_rows()), before)

    async def test_delta_output_is_cached_per_hunk(self) -> None:
        """Verify returning to a hunk reuses the delta output already read."""
        async with review_session(TWO_HUNK_DIFF) as review:
            await review.press(NEXT_KEY)
            await review.press(PREVIOUS_KEY)
            self.assertEqual(review.app.delta_complete, {0, 1})

    async def test_line_note_adds_a_margin_marker(self) -> None:
        """Verify a line note puts its icon in the margin of that line."""
        editor = FakeEditor(PICKER_NOTE_TEXT)
        async with review_session(SIMPLE_DIFF, editor) as review:
            await review.press(QUESTION_KEY)
            await review.press(SELECT_KEY)
            rows = review.diff_rows()
            marked = [row for row in rows if row.startswith(neorev.QUESTION_ICON)]
            self.assertEqual(len(marked), 1)


class TestDeltaStreaming(ReviewTestCase):
    """Tests for the delta output arriving one batch at a time."""

    async def test_partial_output_is_shown_as_it_arrives(self) -> None:
        """Verify a batch of delta lines reaches the view before the rest."""
        async with review_session(TWO_HUNK_DIFF) as review:
            review.app.delta_complete.clear()
            review.app.delta_cache.clear()
            review.app.refresh_diff()
            review.app.absorb_delta_lines(
                0, review.app.delta_generation, [b"first line"]
            )
            await review.pilot.pause()
            self.assertIn("first line", "\n".join(review.diff_rows()))

    async def test_leading_blank_line_is_dropped(self) -> None:
        """Verify the blank line delta opens its output with is not cached."""
        async with review_session(TWO_HUNK_DIFF) as review:
            review.app.delta_complete.clear()
            review.app.delta_cache.clear()
            review.app.absorb_delta_lines(
                0, review.app.delta_generation, [b"", b"one", b""]
            )
            self.assertEqual(review.app.delta_cache[0], [b"one", b""])

    async def test_cached_hunk_is_painted_beyond_the_view_in_the_background(
        self,
    ) -> None:
        """Verify a redraw lays the visible lines out before the rest of the hunk."""
        async with review_session(make_large_diff()) as review:
            log = review.app.query_one(neorev.Diff)
            total = len(review.app.delta_cache[0])
            review.app.refresh_diff()
            self.assertGreaterEqual(
                len(log.lines), log.scrollable_content_region.height
            )
            self.assertLess(len(log.lines), total)
            await review.settle()
            self.assertEqual(len(log.lines), total)

    async def test_a_paint_in_flight_does_not_outlive_the_review(self) -> None:
        """Verify leaving with lines left to lay out raises nothing."""
        async with review_session(make_large_diff(PAINT_BACKLOG_LINES)) as review:
            review.app.refresh_diff()
            self.assertLess(review.app.painted, PAINT_BACKLOG_LINES)

    async def test_margin_markers_are_built_once_per_redraw(self) -> None:
        """Verify the note margin is not rebuilt for every batch of a redraw."""
        async with review_session(make_large_diff()) as review:
            with patch.object(
                neorev,
                "build_margin_markers",
                wraps=neorev.build_margin_markers,
            ) as markers:
                review.app.refresh_diff()
                await review.settle()
            self.assertEqual(markers.call_count, 1)

    async def test_output_that_does_not_line_up_ends_the_review(self) -> None:
        """Verify delta output with a row count of its own stops the review."""
        async with review_session(SIMPLE_DIFF) as review:
            review.app.delta_cache[0] = [b"one row for four lines"]
            review.app.mark_delta_complete(0, review.app.delta_generation)
            self.assertIsNotNone(review.app.delta_mismatch)
            await review.settle()
            self.assertFalse(review.app.is_running)
            self.assertEqual(review.app.return_code, os.EX_SOFTWARE)

    async def test_a_body_less_hunk_is_left_alone(self) -> None:
        """Verify the rename header delta draws for a body-less hunk is accepted."""
        async with review_session(PURE_RENAME_DIFF) as review:
            self.assertIsNone(review.app.delta_mismatch)
            self.assertFalse(review.state.current_hunk.display_lines)

    async def test_incomplete_output_is_read_again(self) -> None:
        """Verify a hunk left half-rendered is streamed from scratch on return."""
        async with review_session(TWO_HUNK_DIFF) as review:
            review.app.delta_complete.discard(0)
            review.app.delta_cache[0] = [b"stale"]
            review.app.show_hunk()
            await review.settle()
            self.assertNotIn("stale", "\n".join(review.diff_rows()))
            self.assertIn(0, review.app.delta_complete)


class TestScrolling(ReviewTestCase):
    """Tests for scrolling inside a hunk larger than the view."""

    async def test_half_page_scroll(self) -> None:
        """Verify Ctrl-D scrolls down and Ctrl-U comes back to the top."""
        async with review_session(
            make_large_diff(), size=(TERM_WIDTH, SHORT_HEIGHT)
        ) as review:
            log = review.app.query_one(neorev.Diff)
            self.assertEqual(log.scroll_offset.y, 0)
            await review.press(SCROLL_DOWN_KEY)
            self.assertGreater(log.scroll_offset.y, 0)
            await review.press(SCROLL_UP_KEY)
            self.assertEqual(log.scroll_offset.y, 0)

    async def test_scrolling_up_at_the_top_stays_there(self) -> None:
        """Verify Ctrl-U at the top of a hunk does not scroll past it."""
        async with review_session(
            make_large_diff(), size=(TERM_WIDTH, SHORT_HEIGHT)
        ) as review:
            await review.press(SCROLL_UP_KEY)
            self.assertEqual(review.app.query_one(neorev.Diff).scroll_offset.y, 0)

    async def test_moving_hunk_returns_to_the_top(self) -> None:
        """Verify the diff view starts at the top of every hunk."""
        async with review_session(
            TWO_HUNK_DIFF, size=(TERM_WIDTH, SHORT_HEIGHT)
        ) as review:
            await review.press(SCROLL_DOWN_KEY)
            await review.press(NEXT_KEY)
            self.assertEqual(review.app.query_one(neorev.Diff).scroll_offset.y, 0)

    async def test_moving_to_an_already_shown_hunk_returns_to_the_top(self) -> None:
        """Verify the offset of the hunk left behind does not follow to the next."""
        diff = make_large_diff() + make_large_diff(name=SECOND_LARGE_FILE_NAME)
        size = (TERM_WIDTH, SHORT_HEIGHT)
        async with review_session(diff, size=size) as review:
            await review.press(NEXT_KEY)
            await review.press(PREVIOUS_KEY)
            log = review.app.query_one(neorev.Diff)
            await review.press(SCROLL_DOWN_KEY)
            self.assertGreater(log.scroll_offset.y, 0)
            await review.press(NEXT_KEY)
            self.assertEqual(log.scroll_offset.y, 0)

    async def test_resize_keeps_the_place_in_the_hunk(self) -> None:
        """Verify laying a hunk out for a new width does not jump back to its top."""
        size = (TERM_WIDTH, SHORT_HEIGHT)
        async with review_session(make_large_diff(), size=size) as review:
            log = review.app.query_one(neorev.Diff)
            await review.press(SCROLL_DOWN_KEY)
            before = log.scroll_offset.y
            self.assertGreater(before, 0)
            await review.resize(NARROW_WIDTH, SHORT_HEIGHT)
            self.assertEqual(log.scroll_offset.y, before)


class TestApproval(ReviewTestCase):
    """Tests for approving hunks and files."""

    async def test_approve_marks_and_advances(self) -> None:
        """Verify a approves the hunk and moves to the next unreviewed one."""
        async with review_session(TWO_HUNK_DIFF) as review:
            await review.press(APPROVE_KEY)
            self.assertTrue(review.state.hunks[0].approved)
            self.assertEqual(review.state.current_index, 1)

    async def test_approve_again_takes_it_back(self) -> None:
        """Verify pressing a on an approved hunk clears its approval."""
        async with review_session(SIMPLE_DIFF) as review:
            await review.press(APPROVE_KEY)
            await review.press(APPROVE_KEY)
            self.assertFalse(review.state.hunks[0].approved)

    async def test_approve_file_covers_every_hunk_of_the_file(self) -> None:
        """Verify A approves the note-free hunks of the current file only."""
        async with review_session(TWO_FILE_DIFF) as review:
            await review.press(APPROVE_FILE_KEY)
            self.assertTrue(review.state.hunks[0].approved)
            self.assertFalse(review.state.hunks[1].approved)

    async def test_approval_shows_in_the_markers(self) -> None:
        """Verify an approved hunk gets its tick in the marker line."""
        async with review_session(TWO_HUNK_DIFF) as review:
            await review.press(APPROVE_KEY)
            self.assertIn("✓", review.chrome_text(neorev.Markers))


class TestHunkNotes(ReviewTestCase):
    """Tests for writing notes on a hunk or one of its lines."""

    async def test_question_on_a_line(self) -> None:
        """Verify c picks a line and stores a question on it."""
        editor = FakeEditor(DISPATCH_COMMENT_TEXT)
        async with review_session(SIMPLE_DIFF, editor) as review:
            await review.press(QUESTION_KEY)
            await review.press(SELECT_KEY)
            (note,) = review.state.hunks[0].notes
            self.assertEqual(note.kind, neorev.NoteKind.QUESTION)
            self.assertIsInstance(note.target, neorev.LineTarget)
            self.assertEqual(note.text, DISPATCH_COMMENT_TEXT)

    async def test_flag_on_the_whole_hunk(self) -> None:
        """Verify h in the picker attaches the note to the whole hunk."""
        editor = FakeEditor(DISPATCH_COMMENT_TEXT)
        async with review_session(TWO_HUNK_DIFF, editor) as review:
            await review.press(FLAG_KEY)
            await review.press(WHOLE_HUNK_KEY)
            (note,) = review.state.hunks[0].notes
            self.assertEqual(note.kind, neorev.NoteKind.FLAG)
            self.assertEqual(note.target, neorev.HunkTarget())

    async def test_hunk_note_advances_to_the_next_hunk(self) -> None:
        """Verify a hunk-scoped note moves the review on."""
        editor = FakeEditor(DISPATCH_COMMENT_TEXT)
        async with review_session(TWO_HUNK_DIFF, editor) as review:
            await review.press(FLAG_KEY)
            await review.press(WHOLE_HUNK_KEY)
            self.assertEqual(review.state.current_index, 1)

    async def test_line_note_stays_on_the_current_hunk(self) -> None:
        """Verify a line-scoped note leaves the review where it is."""
        editor = FakeEditor(DISPATCH_COMMENT_TEXT)
        async with review_session(TWO_HUNK_DIFF, editor) as review:
            await review.press(QUESTION_KEY)
            await review.press(SELECT_KEY)
            self.assertEqual(review.state.current_index, 0)

    async def test_cancelling_the_picker_writes_nothing(self) -> None:
        """Verify Esc in the picker leaves the hunk unannotated."""
        editor = FakeEditor(DISPATCH_COMMENT_TEXT)
        async with review_session(SIMPLE_DIFF, editor) as review:
            await review.press(QUESTION_KEY)
            await review.press(CANCEL_KEY)
            self.assertEqual(review.state.hunks[0].notes, [])
            self.assertEqual(editor.calls, [])

    async def test_empty_comment_drops_an_existing_note(self) -> None:
        """Verify saving an empty comment removes the note that was there."""
        editor = FakeEditor(DISPATCH_COMMENT_TEXT, "")
        async with review_session(SIMPLE_DIFF, editor) as review:
            await review.press(QUESTION_KEY)
            await review.press(SELECT_KEY)
            await review.press(QUESTION_KEY)
            await review.press(SELECT_KEY)
            self.assertEqual(review.state.hunks[0].notes, [])

    async def test_note_clears_a_previous_approval(self) -> None:
        """Verify annotating an approved hunk takes its approval back."""
        editor = FakeEditor(DISPATCH_COMMENT_TEXT)
        async with review_session(SIMPLE_DIFF, editor) as review:
            await review.press(APPROVE_KEY)
            await review.press(FLAG_KEY)
            await review.press(WHOLE_HUNK_KEY)
            self.assertFalse(review.state.hunks[0].approved)

    async def test_editor_gets_the_line_context(self) -> None:
        """Verify the editor is opened with the diff lines around the target."""
        editor = FakeEditor(DISPATCH_COMMENT_TEXT)
        async with review_session(SIMPLE_DIFF, editor) as review:
            await review.press(QUESTION_KEY)
            await review.press(SELECT_KEY)
            (existing, location, context) = editor.calls[0]
            self.assertEqual(existing, "")
            self.assertIn(REVIEW_SCREEN_LOCATION_TOKEN, location)
            self.assertIsNotNone(context)
            self.assertTrue(
                any(neorev.EDITOR_TARGET_MARKER in c for c in context or [])
            )

    async def test_hunk_without_selectable_line_skips_the_picker(self) -> None:
        """Verify a hunk with no added or removed line goes straight to the editor."""
        editor = FakeEditor(DISPATCH_COMMENT_TEXT)
        diff = (
            "diff --git a/old.txt b/new.txt\nrename from old.txt\nrename to new.txt\n"
        )
        async with review_session(diff, editor) as review:
            await review.press(FLAG_KEY)
            (note,) = review.state.hunks[0].notes
            self.assertEqual(note.target, neorev.HunkTarget())


class TestLinePicker(ReviewTestCase):
    """Tests for the screen choosing which line a note targets."""

    async def test_picker_lists_every_diff_line(self) -> None:
        """Verify the picker offers one entry per line of the hunk."""
        async with review_session(SIMPLE_DIFF) as review:
            await review.press(QUESTION_KEY)
            options = review.app.screen.query_one(OptionList)
            self.assertEqual(
                options.option_count,
                len(review.state.current_hunk.display_lines),
            )

    async def test_context_lines_are_not_selectable(self) -> None:
        """Verify only added and removed lines can take a note."""
        async with review_session(SIMPLE_DIFF) as review:
            await review.press(QUESTION_KEY)
            options = review.app.screen.query_one(OptionList)
            enabled = [
                index
                for index, option in enumerate(options.options)
                if not option.disabled
            ]
            self.assertEqual(enabled, [1])

    async def test_cursor_starts_on_the_first_changed_line(self) -> None:
        """Verify the cursor opens on the first line a note can target."""
        async with review_session(SIMPLE_DIFF) as review:
            await review.press(QUESTION_KEY)
            self.assertEqual(review.app.screen.query_one(OptionList).highlighted, 1)

    async def test_cursor_skips_over_context_lines(self) -> None:
        """Verify j moves from one changed line to the next, not to a context line."""
        diff = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -1,1 +1,3 @@\n+one\n unchanged\n+two\n"
        )
        async with review_session(diff) as review:
            await review.press(QUESTION_KEY)
            options = review.app.screen.query_one(OptionList)
            self.assertEqual(options.highlighted, 0)
            await review.press(NEXT_KEY)
            self.assertEqual(options.highlighted, 2)

    async def test_highlighted_line_is_repainted(self) -> None:
        """Verify the cursor line is redrawn plain and the one it left is restored."""
        async with review_session(make_large_diff(LINE_PICKER_MANY_LINES)) as review:
            await review.press(FLAG_KEY)
            picker = review.app.screen
            if not isinstance(picker, neorev.LinePicker):
                self.fail("the line picker is not up")
            options = picker.query_one(OptionList)
            self.assertNotEqual(options.options[0].prompt, picker.prompts[0])
            await review.press(NEXT_KEY)
            self.assertEqual(options.options[0].prompt, picker.prompts[0])
            self.assertNotEqual(options.options[1].prompt, picker.prompts[1])

    async def test_picker_keeps_the_chrome(self) -> None:
        """Verify the top bar and hunk markers stay visible while picking a line."""
        async with review_session(SIMPLE_DIFF) as review:
            await review.press(QUESTION_KEY)
            picker = review.app.screen
            self.assertEqual(len(picker.query(neorev.TopBar)), 1)
            self.assertEqual(len(picker.query(neorev.Markers)), 1)

    async def test_picker_survives_a_resize(self) -> None:
        """Verify resizing while picking rebuilds the lines at the new width."""
        async with review_session(make_large_diff(LINE_PICKER_MANY_LINES)) as review:
            await review.press(QUESTION_KEY)
            picker = review.app.screen
            if not isinstance(picker, neorev.LinePicker):
                self.fail("the line picker is not up")
            before = picker.prompts[0].cell_len
            await review.resize(NARROW_WIDTH, TERM_HEIGHT)
            width = picker.query_one(neorev.PickerList).scrollable_content_region.width
            self.assertNotEqual(before, width)
            self.assertEqual({prompt.cell_len for prompt in picker.prompts}, {width})
            self.assertEqual(
                picker.query_one(OptionList).option_count,
                LINE_PICKER_MANY_LINES,
            )


class TestGlobalNotes(ReviewTestCase):
    """Tests for notes that are tied to no particular hunk."""

    async def test_global_note_kinds(self) -> None:
        """Verify g then the kind key appends a global note of that kind."""
        cases = [
            (GLOBAL_NOTE_ADD_QUESTION_KEY, neorev.NoteKind.QUESTION),
            (GLOBAL_NOTE_ADD_FLAG_KEY, neorev.NoteKind.FLAG),
        ]
        for key, kind in cases:
            with self.subTest(key=key):
                editor = FakeEditor(GLOBAL_NOTE_CREATED_TEXT)
                async with review_session(SIMPLE_DIFF, editor) as review:
                    await review.press(GLOBAL_NOTE_ADD_PREFIX)
                    await review.press(key)
                    self.assertEqual(
                        review.state.global_notes,
                        [
                            neorev.GlobalNote(
                                kind=kind,
                                text=GLOBAL_NOTE_CREATED_TEXT,
                            )
                        ],
                    )

    async def test_cancelling_the_kind_writes_nothing(self) -> None:
        """Verify Esc on the kind prompt leaves the review without a global note."""
        editor = FakeEditor(GLOBAL_NOTE_CREATED_TEXT)
        async with review_session(SIMPLE_DIFF, editor) as review:
            await review.press(GLOBAL_NOTE_ADD_PREFIX)
            await review.press(CANCEL_KEY)
            self.assertEqual(review.state.global_notes, [])
            self.assertEqual(editor.calls, [])

    async def test_empty_text_writes_nothing(self) -> None:
        """Verify leaving the editor empty adds no global note."""
        async with review_session(SIMPLE_DIFF, FakeEditor("")) as review:
            await review.press(GLOBAL_NOTE_ADD_PREFIX)
            await review.press(GLOBAL_NOTE_ADD_QUESTION_KEY)
            self.assertEqual(review.state.global_notes, [])

    async def test_global_note_shows_in_the_top_bar(self) -> None:
        """Verify the global counter of the top bar follows the notes."""
        editor = FakeEditor(GLOBAL_NOTE_CREATED_TEXT)
        async with review_session(SIMPLE_DIFF, editor) as review:
            await review.press(GLOBAL_NOTE_ADD_PREFIX)
            await review.press(GLOBAL_NOTE_ADD_QUESTION_KEY)
            self.assertIn(
                f"global {neorev.NoteKind.FLAG.icon} 0"
                f" {neorev.NoteKind.QUESTION.icon} 1",
                review.chrome_text(neorev.TopBar),
            )


class TestNotesScreen(ReviewTestCase):
    """Tests for the modal listing every note of the review."""

    async def add_global_note(self, review, text: str) -> None:  # noqa: ANN001
        """Add one global change request carrying *text*."""
        review.editor.texts = [text]
        await review.press(GLOBAL_NOTE_ADD_PREFIX)
        await review.press(GLOBAL_NOTE_ADD_FLAG_KEY)

    async def test_notes_are_listed(self) -> None:
        """Verify every note taken so far shows in the list."""
        async with review_session(SIMPLE_DIFF, FakeEditor()) as review:
            await self.add_global_note(review, GLOBAL_NOTE_CREATED_TEXT)
            await review.press(NOTES_KEY)
            self.assertEqual(len(review.option_texts()), 1)
            self.assertIn(GLOBAL_NOTE_CREATED_TEXT, review.option_texts()[0])

    async def test_empty_list_opens_without_notes(self) -> None:
        """Verify the list opens even when nothing has been annotated."""
        async with review_session(SIMPLE_DIFF) as review:
            await review.press(NOTES_KEY)
            self.assertEqual(review.option_texts(), [])

    async def test_editing_a_note_keeps_the_list_open(self) -> None:
        """Verify an edited note is rewritten in place and the list stays up."""
        async with review_session(SIMPLE_DIFF, FakeEditor()) as review:
            await self.add_global_note(review, GLOBAL_NOTE_CREATED_TEXT)
            await review.press(NOTES_KEY)
            review.editor.texts = [GLOBAL_NOTE_EDITED_TEXT]
            await review.press(NOTE_EDIT_KEY)
            self.assertIsInstance(review.app.screen, neorev.NotesScreen)
            self.assertEqual(
                review.state.global_notes,
                [
                    neorev.GlobalNote(
                        kind=neorev.NoteKind.FLAG,
                        text=GLOBAL_NOTE_EDITED_TEXT,
                    )
                ],
            )
            self.assertIn(GLOBAL_NOTE_EDITED_TEXT, review.option_texts()[0])

    async def test_editing_to_empty_deletes_the_note(self) -> None:
        """Verify clearing a note in the editor removes it."""
        async with review_session(SIMPLE_DIFF, FakeEditor()) as review:
            await self.add_global_note(review, GLOBAL_NOTE_CREATED_TEXT)
            await review.press(NOTES_KEY)
            review.editor.texts = [""]
            await review.press(NOTE_EDIT_KEY)
            self.assertEqual(review.state.global_notes, [])

    async def test_deleting_a_note_keeps_the_list_open(self) -> None:
        """Verify d drops the note under the cursor and the list stays up."""
        async with review_session(SIMPLE_DIFF, FakeEditor()) as review:
            await self.add_global_note(review, GLOBAL_NOTE_CREATED_TEXT)
            await review.press(NOTES_KEY)
            await review.press(NOTE_DELETE_KEY)
            self.assertEqual(review.state.global_notes, [])
            self.assertIsInstance(review.app.screen, neorev.NotesScreen)
            self.assertEqual(review.option_texts(), [])

    async def test_enter_edits_the_note_under_the_cursor(self) -> None:
        """Verify Enter opens the editor on the highlighted note."""
        async with review_session(SIMPLE_DIFF, FakeEditor()) as review:
            await self.add_global_note(review, GLOBAL_NOTE_CREATED_TEXT)
            await review.press(NOTES_KEY)
            review.editor.texts = [GLOBAL_NOTE_EDITED_TEXT]
            await review.press(SELECT_KEY)
            self.assertEqual(
                review.state.global_notes,
                [
                    neorev.GlobalNote(
                        kind=neorev.NoteKind.FLAG,
                        text=GLOBAL_NOTE_EDITED_TEXT,
                    )
                ],
            )

    async def test_escape_closes_the_list(self) -> None:
        """Verify Esc returns to the review screen."""
        async with review_session(SIMPLE_DIFF) as review:
            await review.press(NOTES_KEY)
            await review.press(CANCEL_KEY)
            self.assertIsInstance(review.app.screen, neorev.ReviewScreen)

    async def test_hunk_notes_are_listed_with_their_location(self) -> None:
        """Verify a hunk note is listed under the file and target it belongs to."""
        editor = FakeEditor(DISPATCH_COMMENT_TEXT)
        async with review_session(SIMPLE_DIFF, editor) as review:
            await review.press(QUESTION_KEY)
            await review.press(SELECT_KEY)
            await review.press(NOTES_KEY)
            self.assertIn(REVIEW_SCREEN_LOCATION_TOKEN, review.option_texts()[0])

    async def test_hunk_notes_are_edited_and_deleted_in_place(self) -> None:
        """Verify the list rewrites a hunk note with its context, then drops it."""
        editor = FakeEditor(DISPATCH_COMMENT_TEXT, HUNK_NOTE_EDITED_TEXT)
        async with review_session(SIMPLE_DIFF, editor) as review:
            await review.press(QUESTION_KEY)
            await review.press(SELECT_KEY)
            await review.press(NOTES_KEY)
            await review.press(NOTE_EDIT_KEY)
            self.assertEqual(
                review.state.hunks[0].notes[0].text,
                HUNK_NOTE_EDITED_TEXT,
            )
            self.assertIsNotNone(editor.calls[-1][2])
            await review.press(NOTE_DELETE_KEY)
            self.assertEqual(review.state.hunks[0].notes, [])


class TestFooter(ReviewTestCase):
    """Tests for the key hints drawn at the bottom of every screen."""

    async def test_a_hint_names_every_key_of_its_action(self) -> None:
        """Verify each screen hints at all the keys running each of its actions."""
        async with review_session(SIMPLE_DIFF, FakeEditor()) as review:
            self.assertEqual(footer_hints(review), REVIEW_FOOTER_HINTS)
            await review.press(NOTES_KEY)
            self.assertEqual(footer_hints(review), NOTES_FOOTER_HINTS)
            await review.press(CANCEL_KEY)
            await review.press(QUESTION_KEY)
            self.assertEqual(footer_hints(review), PICKER_FOOTER_HINTS)
            await review.press(CANCEL_KEY)
            await review.press(GLOBAL_NOTE_ADD_PREFIX)
            self.assertEqual(footer_hints(review), GLOBAL_NOTE_FOOTER_HINTS)


class TestHelpPanel(ReviewTestCase):
    """Tests for the key help."""

    async def test_help_opens_and_closes(self) -> None:
        """Verify ? toggles the panel listing the keys."""
        async with review_session(SIMPLE_DIFF) as review:
            await review.press(HELP_KEY)
            self.assertEqual(len(review.app.screen.query(HelpPanel)), 1)
            await review.press(HELP_KEY)
            self.assertEqual(len(review.app.screen.query(HelpPanel)), 0)

    async def test_help_offers_no_palette_key(self) -> None:
        """Verify the panel lists no key of the framework's own."""
        async with review_session(SIMPLE_DIFF) as review:
            self.assertNotIn(PALETTE_KEY, review.app.screen.active_bindings)

    async def test_help_leaves_the_interrupt_key_out(self) -> None:
        """Verify Ctrl-C is not advertised, being the convention it is."""
        async with review_session(SIMPLE_DIFF) as review:
            await review.press(HELP_KEY)
            table = review.app.query_one(BindingsTable).render_bindings_table()
            keys = [str(cell) for cell in table.columns[0].cells]
            self.assertIn(QUIT_KEY, keys)
            self.assertNotIn(INTERRUPT_KEY_DISPLAY, keys)

    async def test_no_screen_carries_a_framework_key(self) -> None:
        """Verify the focus, copy and quit keys of the framework are unbound."""
        async with review_session(SIMPLE_DIFF) as review:
            screens = [review.app.screen]
            await review.press(NOTES_KEY)
            screens.append(review.app.screen)
            for screen in screens:
                for key in FRAMEWORK_KEYS:
                    with self.subTest(screen=type(screen).__name__, key=key):
                        self.assertNotIn(key, screen.active_bindings)


class TestCommandPalette(ReviewTestCase):
    """Tests for the command palette of the framework being out of reach."""

    async def test_palette_key_leaves_the_review_in_place(self) -> None:
        """Verify Ctrl-P opens nothing over the review screen."""
        async with review_session(SIMPLE_DIFF) as review:
            await review.press(PALETTE_KEY)
            self.assertIsInstance(review.app.screen, neorev.ReviewScreen)


class TestQuitting(ReviewTestCase):
    """Tests for leaving the review."""

    async def test_quit_key_exits(self) -> None:
        """Verify q leaves the review."""
        async with review_session(SIMPLE_DIFF) as review:
            await review.press(QUIT_KEY)
            self.assertFalse(review.app.is_running)

    async def test_interrupt_exits(self) -> None:
        """Verify Ctrl-C leaves the review instead of only warning about it."""
        async with review_session(SIMPLE_DIFF) as review:
            await review.press(QUIT_INTERRUPT_KEY)
            self.assertFalse(review.app.is_running)

    async def test_interrupt_exits_from_a_modal(self) -> None:
        """Verify Ctrl-C leaves the review even with a screen pushed over it."""
        async with review_session(SIMPLE_DIFF) as review:
            await review.press(NOTES_KEY)
            await review.press(QUIT_INTERRUPT_KEY)
            self.assertFalse(review.app.is_running)


class TestEmptyDiff(ReviewTestCase):
    """Tests for the placeholder shown while the watched diff holds no hunk."""

    async def test_placeholder_replaces_the_diff(self) -> None:
        """Verify the wait message shows and the chrome is hidden."""
        async with review_session("") as review:
            self.assertTrue(review.state.is_empty)
            self.assertFalse(review.app.query_one(neorev.Diff).display)
            self.assertFalse(review.app.query_one(neorev.TopBar).display)
            placeholder = review.app.query_one(f"#{neorev.EMPTY_ID}", Static)
            self.assertTrue(placeholder.display)
            self.assertEqual(rendered_text(placeholder), neorev.EMPTY_DIFF_WAIT_MESSAGE)

    async def test_keys_needing_a_hunk_do_nothing(self) -> None:
        """Verify approving or annotating an empty diff is a no-op."""
        async with review_session("") as review:
            await review.press(APPROVE_KEY)
            await review.press(APPROVE_FILE_KEY)
            await review.press(QUESTION_KEY)
            await review.press(NEXT_KEY)
            self.assertTrue(review.state.is_empty)

    async def test_global_note_still_works(self) -> None:
        """Verify a global note can be taken while waiting for a diff."""
        editor = FakeEditor(GLOBAL_NOTE_CREATED_TEXT)
        async with review_session("", editor) as review:
            await review.press(GLOBAL_NOTE_ADD_PREFIX)
            await review.press(GLOBAL_NOTE_ADD_FLAG_KEY)
            self.assertEqual(len(review.state.global_notes), 1)


class TestTerminalColours(ReviewTestCase):
    """Tests for the chrome drawing itself in the colours of the terminal."""

    async def test_chrome_colours_come_from_the_palette(self) -> None:
        """Verify the chrome names palette entries rather than absolute colours."""
        async with review_session(SIMPLE_DIFF) as review:
            for widget_type in (neorev.TopBar, neorev.Markers):
                with self.subTest(widget=widget_type.__name__):
                    for segment in rendered_segments(review.app.query_one(widget_type)):
                        self.assertTrue(is_palette_style(segment.style), segment.text)

    async def test_only_the_brand_carries_the_highlight(self) -> None:
        """Verify the brand background stops after the tool name."""
        async with review_session(SIMPLE_DIFF) as review:
            segments = rendered_segments(review.app.query_one(neorev.TopBar))
            brand = segment_holding(segments, BRAND_TOKEN)
            self.assertEqual(background_index(brand), BLUE_INDEX)
            hunk = segment_holding(segments, HUNK_TOKEN)
            self.assertEqual(background_index(hunk), CHROME_INDEX)

    async def test_picker_highlight_comes_from_the_palette(self) -> None:
        """Verify the line under the picker cursor is painted in a palette colour."""
        async with review_session(SIMPLE_DIFF) as review:
            await review.press(FLAG_KEY)
            segments = rendered_segments(review.app.screen.query_one(neorev.PickerList))
            backgrounds = {background_index(segment) for segment in segments}
            self.assertIn(YELLOW_INDEX, backgrounds)

    async def test_question_pick_inverts_the_terminal_colours(self) -> None:
        """Verify the question cursor line swaps the terminal's own pair."""
        async with review_session(SIMPLE_DIFF) as review:
            await review.press(QUESTION_KEY)
            segments = rendered_segments(review.app.screen.query_one(neorev.PickerList))
            cursor = segment_holding(segments, CURSOR_LINE_TOKEN)
            self.assertIsNotNone(cursor)
            self.assertTrue(segment_style(cursor).reverse)
            self.assertEqual(foreground_type(cursor), DEFAULT_TYPE)
            self.assertIsNone(background_index(cursor))

    async def test_chrome_sits_on_its_own_background(self) -> None:
        """Verify the bars framing the diff are set off from it."""
        async with review_session(SIMPLE_DIFF) as review:
            widgets = [
                review.app.query_one(neorev.TopBar),
                review.app.query_one(neorev.Markers),
                review.app.query_one(Footer),
                *review.app.query(FooterKey),
            ]
            for index, widget in enumerate(widgets):
                with self.subTest(widget=f"{index} {type(widget).__name__}"):
                    segments = rendered_segments(widget)
                    self.assertEqual(background_index(segments[-1]), CHROME_INDEX)

    async def test_footer_keys_are_dimmed_rather_than_coloured(self) -> None:
        """Verify a key hint is set apart from its label by weight, not by colour."""
        async with review_session(SIMPLE_DIFF) as review:
            segments = [
                segment
                for widget in review.app.query(FooterKey)
                for segment in rendered_segments(widget)
            ]
            key = segment_holding(segments, NEXT_KEY_DISPLAY)
            self.assertIsNotNone(key)
            self.assertTrue(segment_style(key).dim)
            self.assertFalse(segment_style(key).bold)
            self.assertEqual(foreground_type(key), DEFAULT_TYPE)

    async def test_scrollbar_runs_over_the_chrome_background(self) -> None:
        """Verify the diff scrollbar drops the accent colour of the theme."""
        size = (TERM_WIDTH, SHORT_HEIGHT)
        async with review_session(make_large_diff(), size=size) as review:
            scrollbar = review.app.query_one(neorev.Diff).vertical_scrollbar
            segments = rendered_segments(scrollbar)
            # The cursor is painted by reversing the track it slides along.
            cursor = [item for item in segments if segment_style(item).reverse]
            track = [item for item in segments if not segment_style(item).reverse]
            self.assertEqual({foreground_type(seg) for seg in cursor}, {DEFAULT_TYPE})
            self.assertEqual({background_index(seg) for seg in track}, {CHROME_INDEX})


def footer_hints(review: ReviewDriver) -> list[tuple[str, str]]:
    """Return the key and the label of every hint the visible screen draws."""
    return [
        (key.key_display, key.description) for key in review.app.screen.query(FooterKey)
    ]


def is_palette_style(style: Style | None) -> bool:
    """Tell whether *style* only uses palette entries and the terminal defaults."""
    if style is None:
        return True
    return all(
        colour is None or colour.type in PALETTE_COLOUR_TYPES
        for colour in (style.color, style.bgcolor)
    )


def segment_style(segment: Segment | None) -> Style:
    """Return the style *segment* is painted with."""
    return Style() if segment is None or segment.style is None else segment.style


def background_index(segment: Segment | None) -> int | None:
    """Return the palette index *segment* is painted on, if it names one."""
    background = segment_style(segment).bgcolor
    return None if background is None else background.number


def foreground_type(segment: Segment | None) -> ColorType | None:
    """Return the kind of colour *segment*'s text is painted in."""
    foreground = segment_style(segment).color
    return None if foreground is None else foreground.type


def segment_holding(segments: list[Segment], token: str) -> Segment | None:
    """Return the first segment of *segments* whose text holds *token*."""
    return next((segment for segment in segments if token in segment.text), None)


if __name__ == "__main__":
    unittest.main()
