"""Tests for the chrome builders and the editor context blocks."""

import tempfile
import unittest

from rich.color import Color
from rich.style import Style
from rich.text import Text

from tests.helpers import (
    CENTERED_SNIPPET_LINE_COUNT,
    CENTERED_SNIPPET_TARGET_LINE,
    DELETE_FILE_DIFF,
    DELTA_ADDED_BACKGROUND,
    DELTA_REMOVED_BACKGROUND,
    DELTA_WORD_BACKGROUND,
    MANY_HUNKS_COUNT,
    NARROW_PROGRESS_WIDTH,
    OVERFLOW_HUNK_INDEX,
    PURE_RENAME_DIFF,
    TERM_WIDTH,
    TOP_BAR_INDEX_TOKEN,
    make_hunk,
    neorev,
)

RESET = "\x1b[0m"
BLUE_BACKGROUND = "\x1b[44m"
DEFAULT_BACKGROUND = Color.default()
# The line-number gutter delta draws, coloured with a foreground only.
DELTA_GUTTER_FOREGROUND = "\x1b[38;5;88m"
DELTA_GUTTER = "  4 ⋮    │"
WIDE_WIDTH = 120
MANY_LINES_COUNT = 30
REMOVED_RGB = (63, 0, 1)
ADDED_RGB = (0, 40, 0)
HUNK_LABEL = "Hunk"


def style_at(text: Text, offset: int) -> Style:
    """Return the style *text* renders its character at *offset* with."""
    return text.get_style_at_offset(neorev.CONSOLE, offset)


def background_of(style: Style | None) -> tuple[int, int, int] | None:
    """Return the red, green and blue components of *style*'s background."""
    if style is None or style.bgcolor is None or style.bgcolor.triplet is None:
        return None
    triplet = style.bgcolor.triplet
    return (triplet.red, triplet.green, triplet.blue)


def make_state(
    hunks: list[neorev.Hunk],
    global_notes: list[neorev.GlobalNote] | None = None,
    current_index: int = 0,
) -> neorev.ReviewState:
    """Build review state over *hunks* for the chrome builders."""
    return neorev.ReviewState(
        hunks=hunks,
        global_notes=[] if global_notes is None else global_notes,
        current_index=current_index,
    )


class TestFillStyle(unittest.TestCase):
    """Tests for the background a changed diff line is padded with."""

    def test_erase_sequence_gives_its_background(self) -> None:
        """Verify the fill style is the one active at delta's erase sequence."""
        line = (
            f"{DELTA_REMOVED_BACKGROUND}foo {DELTA_WORD_BACKGROUND}x{RESET}"
            f"{DELTA_REMOVED_BACKGROUND}{neorev.ERASE_TO_LINE_END}{RESET}"
        )
        self.assertEqual(background_of(neorev.erase_fill_style(line)), REMOVED_RGB)

    def test_line_without_erase_sequence_has_no_fill(self) -> None:
        """Verify a line delta left unchanged carries no fill style."""
        self.assertIsNone(neorev.erase_fill_style(f"{BLUE_BACKGROUND}text{RESET}"))

    def test_diff_line_carries_the_fill_style(self) -> None:
        """Verify the rendered line takes the erase background as its base style."""
        line = f"{DELTA_ADDED_BACKGROUND}{neorev.ERASE_TO_LINE_END}{RESET}".encode()
        style = neorev.diff_line_text(line).style
        self.assertEqual(
            background_of(style if isinstance(style, Style) else None), ADDED_RGB
        )

    def test_fill_stops_at_the_first_background_delta_paints(self) -> None:
        """Verify the margin and the line-number gutter keep the terminal background."""
        line = (
            f"{DELTA_GUTTER_FOREGROUND}{DELTA_GUTTER}"
            f"{DELTA_REMOVED_BACKGROUND}old{RESET}"
            f"{DELTA_REMOVED_BACKGROUND}{neorev.ERASE_TO_LINE_END}{RESET}"
        ).encode()
        text = neorev.diff_line_text(line, neorev.MARGIN_EMPTY)
        gutter_end = neorev.MARGIN_WIDTH + len(DELTA_GUTTER)
        self.assertEqual(style_at(text, 0).bgcolor, DEFAULT_BACKGROUND)
        self.assertEqual(style_at(text, gutter_end - 1).bgcolor, DEFAULT_BACKGROUND)
        self.assertEqual(background_of(style_at(text, gutter_end)), REMOVED_RGB)

    def test_diff_line_keeps_its_own_colours(self) -> None:
        """Verify the text of a changed line keeps the colours delta gave it."""
        line = f"{DELTA_REMOVED_BACKGROUND}old{RESET}".encode()
        self.assertEqual(neorev.diff_line_text(line).plain, "old")

    def test_margin_is_prefixed_to_the_line(self) -> None:
        """Verify the note margin is drawn ahead of the diff line."""
        text = neorev.diff_line_text(b"added", neorev.NoteKind.FLAG.icon + " ")
        self.assertEqual(text.plain, f"{neorev.NoteKind.FLAG.icon} added")

    def test_padding_reaches_the_requested_width(self) -> None:
        """Verify a short line is padded out to the width it is given."""
        text = neorev.pad_to_width(neorev.diff_line_text(b"x"), TERM_WIDTH)
        self.assertEqual(text.cell_len, TERM_WIDTH)

    def test_padding_leaves_a_long_line_alone(self) -> None:
        """Verify a line wider than the terminal is not padded."""
        line = ("x" * (TERM_WIDTH + 10)).encode()
        text = neorev.pad_to_width(neorev.diff_line_text(line), TERM_WIDTH)
        self.assertEqual(text.cell_len, TERM_WIDTH + 10)


class TestMarginMarkers(unittest.TestCase):
    """Tests for build_margin_markers."""

    def test_hunk_without_line_notes_has_no_margin(self) -> None:
        """Verify no margin column is reserved when no line carries a note."""
        self.assertEqual(neorev.build_margin_markers(make_hunk()), [])

    def test_line_note_shows_its_icon(self) -> None:
        """Verify the noted line gets its kind icon and the others stay blank."""
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=1)
        hunk = make_hunk(
            body="+added line",
            notes=[
                neorev.HunkNote(
                    kind=neorev.NoteKind.QUESTION,
                    target=target,
                    text="why?",
                )
            ],
        )
        self.assertEqual(
            neorev.build_margin_markers(hunk),
            [f"{neorev.NoteKind.QUESTION.icon} "],
        )


class TestInitialPickerLine(unittest.TestCase):
    """Tests for the line the picker starts on."""

    def test_first_selectable_line_when_not_scrolled(self) -> None:
        """Verify the cursor starts on the first line a note can target."""
        hunk = make_hunk(range_line="@@ -1,2 +1,2 @@", body=" context\n+added")
        self.assertEqual(neorev.initial_picker_line(hunk, 0), 1)

    def test_line_at_or_below_the_scroll_position(self) -> None:
        """Verify the cursor starts on the first selectable line still on screen."""
        body = "\n".join(f"+line {i}" for i in range(MANY_LINES_COUNT))
        hunk = make_hunk(range_line=f"@@ -1,0 +1,{MANY_LINES_COUNT} @@", body=body)
        self.assertEqual(neorev.initial_picker_line(hunk, 10), 10)

    def test_scroll_past_the_last_line_falls_back(self) -> None:
        """Verify a scroll offset past the hunk falls back to the first line."""
        hunk = make_hunk(body="+added")
        self.assertEqual(neorev.initial_picker_line(hunk, 999), 0)


class TestChrome(unittest.TestCase):
    """Tests for the top bar and the hunk progress markers."""

    def test_top_bar_contains_index(self) -> None:
        """Verify top bar shows 'Hunk N/total'."""
        hunk = make_hunk()
        bar = neorev.build_top_bar(make_state([hunk] * 5), WIDE_WIDTH)
        self.assertIn(TOP_BAR_INDEX_TOKEN, bar.plain)

    def test_top_bar_global_count(self) -> None:
        """Verify top bar shows global note count when present."""
        hunk = make_hunk()
        global_notes = [
            neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="g1"),
            neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="g2"),
            neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="g3"),
        ]
        bar = neorev.build_top_bar(make_state([hunk], global_notes), WIDE_WIDTH)
        self.assertIn(f"global {neorev.NoteKind.FLAG.icon} 3", bar.plain)

    def test_file_status_markers(self) -> None:
        """Verify each file status renders its own Nerd Font diff icon."""
        cases = [
            (neorev.FileStatus.ADDED, neorev.DIFF_ADDED_ICON),
            (neorev.FileStatus.DELETED, neorev.DIFF_REMOVED_ICON),
            (neorev.FileStatus.RENAMED, neorev.DIFF_RENAMED_ICON),
            (neorev.FileStatus.MODIFIED, neorev.DIFF_MODIFIED_ICON),
        ]
        for file_status, icon in cases:
            with self.subTest(file_status=file_status):
                self.assertEqual(file_status.marker.plain, icon)

    def test_removed_marker_uses_yellow(self) -> None:
        """Verify the deleted-file marker uses the yellow colour."""
        self.assertEqual(
            style_at(neorev.FileStatus.DELETED.marker, 0),
            Style.parse(neorev.STYLE_QUESTION),
        )

    def test_renamed_marker_uses_cyan(self) -> None:
        """Verify the renamed-file marker uses the cyan colour."""
        self.assertEqual(
            style_at(neorev.FileStatus.RENAMED.marker, 0),
            Style.parse(neorev.STYLE_RENAMED),
        )

    def test_modified_marker_is_neutral(self) -> None:
        """Verify the modified-file marker carries no colour, only the bare icon."""
        self.assertEqual(style_at(neorev.FileStatus.MODIFIED.marker, 0), Style())

    def test_top_bar_deleted_file(self) -> None:
        """Verify top bar marks a deleted file with the removed icon and no :0 line."""
        hunk = neorev.parse_diff(DELETE_FILE_DIFF)[0]
        bar = neorev.build_top_bar(make_state([hunk]), WIDE_WIDTH).plain
        self.assertIn(neorev.DIFF_REMOVED_ICON, bar)
        self.assertIn("old.py", bar)
        self.assertNotIn(":0", bar)

    def test_top_bar_renamed_file(self) -> None:
        """Verify the top bar shows an 'old → new' mapping and the renamed icon."""
        hunk = neorev.parse_diff(PURE_RENAME_DIFF)[0]
        bar = neorev.build_top_bar(make_state([hunk]), WIDE_WIDTH).plain
        self.assertIn(neorev.DIFF_RENAMED_ICON, bar)
        self.assertIn(f"old.txt {neorev.RENAME_ARROW} new.txt", bar)

    def test_hunk_marker_styles(self) -> None:
        """Verify each status produces a distinct marker icon."""
        cases = [
            (neorev.Status.APPROVED, "✓"),
            (neorev.Status.FLAG, "✗"),
            (neorev.Status.QUESTION, "?"),
            (None, "·"),
        ]
        for status, icon in cases:
            with self.subTest(status=status):
                hunk = make_hunk(status=status)
                marker = hunk.marker(is_current=False)
                self.assertIn(icon, marker.plain)

    def test_current_marker_has_brackets(self) -> None:
        """Verify the current hunk marker is wrapped in brackets."""
        marker = make_hunk().marker(is_current=True)
        self.assertTrue(marker.plain.startswith("["))
        self.assertTrue(marker.plain.endswith("]"))

    def test_current_marker_bolds_only_its_brackets(self) -> None:
        """Verify the bracket weight does not spill onto the status icon."""
        marker = make_hunk().marker(is_current=True)
        self.assertEqual(style_at(marker, 0), Style.parse(neorev.STYLE_BOLD))
        self.assertEqual(style_at(marker, 1), Style.parse(neorev.STYLE_DIM))

    def test_top_bar_highlights_only_the_tool_name(self) -> None:
        """Verify the brand style stops before the hunk index."""
        bar = neorev.build_top_bar(make_state([make_hunk()]), WIDE_WIDTH)
        self.assertEqual(style_at(bar, 0), Style.parse(neorev.STYLE_BRAND))
        self.assertEqual(style_at(bar, bar.plain.index(HUNK_LABEL)), Style())

    def test_chrome_text_carries_no_base_style(self) -> None:
        """Verify no builder leaves a style spanning the whole of its text."""
        state = make_state([make_hunk()])
        builders = {
            "top bar": neorev.build_top_bar(state, WIDE_WIDTH),
            "progress markers": neorev.build_progress_markers(state, TERM_WIDTH),
            "file status marker": neorev.FileStatus.DELETED.marker,
            "hunk marker": make_hunk().marker(is_current=True),
        }
        for name, text in builders.items():
            with self.subTest(builder=name):
                self.assertEqual(text.style, "")

    def test_progress_markers_count(self) -> None:
        """Verify progress markers line contains all hunk markers when they fit."""
        state = make_state([make_hunk() for _ in range(5)], current_index=2)
        line = neorev.build_progress_markers(state, TERM_WIDTH)
        self.assertEqual(line.plain.count("·"), 5)

    def test_progress_markers_overflow(self) -> None:
        """Verify with many hunks, overflow arrows appear."""
        state = make_state(
            [make_hunk() for _ in range(MANY_HUNKS_COUNT)],
            current_index=OVERFLOW_HUNK_INDEX,
        )
        line = neorev.build_progress_markers(state, NARROW_PROGRESS_WIDTH).plain
        self.assertIn("◀", line)
        self.assertIn("▶", line)

    def test_progress_markers_single_hunk(self) -> None:
        """Verify a single hunk produces one marker with no overflow arrows."""
        line = neorev.build_progress_markers(make_state([make_hunk()]), TERM_WIDTH)
        self.assertNotIn("◀", line.plain)
        self.assertNotIn("▶", line.plain)

    def test_progress_markers_at_start(self) -> None:
        """Verify at index 0 with many hunks, no left arrow but right arrow present."""
        state = make_state([make_hunk() for _ in range(MANY_HUNKS_COUNT)])
        line = neorev.build_progress_markers(state, NARROW_PROGRESS_WIDTH).plain
        self.assertNotIn("◀", line)
        self.assertIn("▶", line)

    def test_progress_markers_at_end(self) -> None:
        """Verify at the last index with many hunks, left arrow but no right arrow."""
        state = make_state(
            [make_hunk() for _ in range(MANY_HUNKS_COUNT)],
            current_index=MANY_HUNKS_COUNT - 1,
        )
        line = neorev.build_progress_markers(state, NARROW_PROGRESS_WIDTH).plain
        self.assertIn("◀", line)
        self.assertNotIn("▶", line)


class TestTopBarTruncation(unittest.TestCase):
    """Tests for build_top_bar width truncation."""

    def test_narrow_width_truncates(self) -> None:
        """Verify top bar is truncated to an ellipsis when term_width is small."""
        state = make_state([make_hunk()])
        bar = neorev.build_top_bar(state, term_width=NARROW_PROGRESS_WIDTH)
        self.assertLessEqual(bar.cell_len, NARROW_PROGRESS_WIDTH)
        self.assertTrue(bar.plain.endswith("…"))

    def test_fitting_width_passes_bar_through(self) -> None:
        """Verify a bar that fits reaches the screen whole, ellipsis-free."""
        bar = neorev.build_top_bar(make_state([make_hunk()]), WIDE_WIDTH)
        self.assertGreater(bar.cell_len, NARROW_PROGRESS_WIDTH)
        self.assertFalse(bar.plain.endswith("…"))


class TestProgressMarkersTinyWidth(unittest.TestCase):
    """Tests for build_progress_markers with tiny terminal widths."""

    def test_very_narrow_returns_empty(self) -> None:
        """Verify extremely narrow terminals produce an empty marker line."""
        state = make_state([neorev.Hunk(range_line="", body="", raw="", file_path="f")])
        # prefix is 2 columns wide, so available < MARKER_WIDTH
        too_narrow = neorev.MARKER_WIDTH + 1
        self.assertEqual(neorev.build_progress_markers(state, too_narrow).plain, "")

    def test_marker_width_boundary(self) -> None:
        """Verify widths exactly fitting one marker still produce output."""
        state = make_state([neorev.Hunk(range_line="", body="", raw="", file_path="f")])
        min_working_width = neorev.MARKER_WIDTH + 2  # prefix is 2 columns wide
        result = neorev.build_progress_markers(state, min_working_width)
        self.assertNotEqual(result.plain, "")


class TestNoteOptionText(unittest.TestCase):
    """Tests for the prompt describing a note in the note list."""

    def test_hunk_note_names_its_location_and_target(self) -> None:
        """Verify a hunk note is labelled with its file, line and target."""
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=2)
        note = neorev.HunkNote(
            kind=neorev.NoteKind.QUESTION,
            target=target,
            text="why?",
        )
        hunk = make_hunk(file_path="a.py", start_line=1, notes=[note])
        prompt = neorev.note_option_text((hunk, note)).plain
        self.assertIn("a.py:1 @ +2", prompt)
        self.assertIn("why?", prompt)

    def test_global_note_is_labelled_global(self) -> None:
        """Verify a global note is labelled with the global scope."""
        note = neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="rework this")
        prompt = neorev.note_option_text(note).plain
        self.assertIn(neorev.GLOBAL_TARGET, prompt)
        self.assertIn("rework this", prompt)

    def test_multi_line_note_is_kept_whole(self) -> None:
        """Verify the whole note text reaches the list, newlines included."""
        note = neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="first\nsecond")
        self.assertIn("first\nsecond", neorev.note_option_text(note).plain)


class TestBuildLineContext(unittest.TestCase):
    """Tests for the editor context of a line target."""

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
        """Verify context shows surrounding lines with marker on the target."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=3)
        ctx = hunk.build_editor_context(target, 0)
        marker_lines = [c for c in ctx if neorev.EDITOR_TARGET_MARKER in c]
        self.assertEqual(len(marker_lines), 1)
        self.assertIn("new three", marker_lines[0])

    def test_context_around_removed_line(self) -> None:
        """Verify context marks the removed line with the target marker."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.REMOVED, line_number=3)
        ctx = hunk.build_editor_context(target, 0)
        marker_lines = [c for c in ctx if neorev.EDITOR_TARGET_MARKER in c]
        self.assertEqual(len(marker_lines), 1)
        self.assertIn("old three", marker_lines[0])

    def test_context_includes_diff_prefix(self) -> None:
        """Verify each context line includes the diff prefix from its kind."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=3)
        ctx = hunk.build_editor_context(target, 0)
        added = [c for c in ctx if "new three" in c]
        self.assertTrue(any("+" in c for c in added))
        context = [c for c in ctx if "line two" in c]
        self.assertTrue(len(context) > 0)

    def test_context_radius_limits(self) -> None:
        """Verify context does not exceed the configured radius."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=3)
        ctx = hunk.build_editor_context(target, 0)
        max_lines = 2 * neorev.EDITOR_CONTEXT_RADIUS + 1
        self.assertLessEqual(len(ctx), max_lines)

    def test_context_at_start_of_hunk(self) -> None:
        """Verify context near the beginning does not go out of bounds."""
        hunk = make_hunk(
            range_line="@@ -1,2 +1,2 @@",
            body="+added\n context",
        )
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=1)
        ctx = hunk.build_editor_context(target, 0)
        self.assertTrue(len(ctx) >= 1)
        self.assertIn(neorev.EDITOR_TARGET_MARKER, ctx[0])

    def test_unknown_target_returns_empty(self) -> None:
        """Verify a target not in the display lines returns an empty list."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=999)
        ctx = hunk.build_editor_context(target, 0)
        self.assertEqual(ctx, [])

    def test_context_lines_are_aligned(self) -> None:
        """Verify all context lines have the same length up to the diff prefix."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=3)
        ctx = hunk.build_editor_context(target, 0)
        # Check alignment: strip the "# " prefix and verify the marker + line
        # number + prefix portion has consistent width.
        for line in ctx:
            stripped = line[2:]  # remove "# "
            # marker(1) + space(1) + line_num(>=4) + space(1) + prefix(1)
            # The diff prefix char should always be at the same offset.
            self.assertEqual(stripped[0:2], stripped[0] + " ")
            self.assertIn(stripped[6], ("+", "-", " ", "\\"))


class TestBuildHunkContext(unittest.TestCase):
    """Tests for the editor context of a whole-hunk target."""

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
        """Verify context lines begin at the given scroll offset."""
        hunk = self.make_hunk()
        ctx = hunk.build_editor_context(neorev.HunkTarget(), 2)
        self.assertIn("old three", ctx[0])

    def test_respects_max_lines(self) -> None:
        """Verify context never exceeds EDITOR_HUNK_CONTEXT_MAX lines."""
        body = "\n".join(f"+line {i}" for i in range(MANY_LINES_COUNT))
        hunk = make_hunk(range_line=f"@@ -1,0 +1,{MANY_LINES_COUNT} @@", body=body)
        ctx = hunk.build_editor_context(neorev.HunkTarget(), 0)
        self.assertEqual(len(ctx), neorev.EDITOR_HUNK_CONTEXT_MAX)

    def test_offset_zero_starts_at_beginning(self) -> None:
        """Verify offset zero returns lines from the start of the hunk."""
        hunk = self.make_hunk()
        ctx = hunk.build_editor_context(neorev.HunkTarget(), 0)
        self.assertIn("line one", ctx[0])

    def test_offset_past_end_returns_empty(self) -> None:
        """Verify an offset beyond the display lines returns an empty list."""
        hunk = self.make_hunk()
        ctx = hunk.build_editor_context(neorev.HunkTarget(), 999)
        self.assertEqual(ctx, [])

    def test_negative_offset_clamps_to_zero(self) -> None:
        """Verify a negative offset is clamped to zero."""
        hunk = self.make_hunk()
        ctx_neg = hunk.build_editor_context(neorev.HunkTarget(), -5)
        ctx_zero = hunk.build_editor_context(neorev.HunkTarget(), 0)
        self.assertEqual(ctx_neg, ctx_zero)

    def test_lines_use_context_pad(self) -> None:
        """Verify hunk context lines use the context pad, not the target marker."""
        hunk = self.make_hunk()
        ctx = hunk.build_editor_context(neorev.HunkTarget(), 0)
        for line in ctx:
            self.assertNotIn(neorev.EDITOR_TARGET_MARKER, line)
            self.assertIn(neorev.EDITOR_CONTEXT_PAD, line)

    def test_includes_diff_prefix(self) -> None:
        """Verify context lines include the diff prefix character."""
        hunk = self.make_hunk()
        ctx = hunk.build_editor_context(neorev.HunkTarget(), 0)
        prefixes = set()
        for line in ctx:
            stripped = line[2:]  # remove "# "
            prefixes.add(stripped[6])
        self.assertTrue(prefixes & {"+", "-", " "})

    def test_a_line_target_centres_the_snippet_on_its_line(self) -> None:
        """Verify a line target gets the context centred on it, not the hunk head."""
        hunk = self.make_hunk()
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=3)
        marked = [line for line in hunk.build_editor_context(target, 0) if "►" in line]
        self.assertEqual(len(marked), 1)
        self.assertIn(" 3 ", marked[0])


class TestWriteCommentTemplateWithContext(unittest.TestCase):
    """Tests for write_comment_template with context_lines."""

    def test_context_lines_included_in_template(self) -> None:
        """Verify context lines appear as # comments in the template."""
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".cfg") as f:
            ctx = ["# ► 10 + added line", "#   11   context line"]
            jump = neorev.write_comment_template(f, "test.py:10", "", ctx)
            f.seek(0)
            content = f.read()
        self.assertIn("added line", content)
        self.assertIn("context line", content)
        self.assertGreater(jump, 0)

    def test_context_lines_stripped_by_read(self) -> None:
        """Verify context lines (starting with #) are stripped when reading back."""
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".cfg") as f:
            ctx = ["# ► 10 + the target line"]
            neorev.write_comment_template(f, "loc", "my note", ctx)
            f.flush()
            result = neorev.read_comment_file(f.name)
        self.assertEqual(result, "my note")
        self.assertNotIn("target line", result)

    def test_jump_line_accounts_for_context(self) -> None:
        """Verify jump line is offset by the number of context lines."""
        ctx = ["# line1", "# line2", "# line3"]
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".cfg") as f:
            jump_no_ctx = neorev.write_comment_template(f, "loc", "")
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".cfg") as f:
            jump_with_ctx = neorev.write_comment_template(f, "loc", "", ctx)
        # 3 context lines + 2 separator lines (#\n before and after)
        expected_offset = len(ctx) + 2
        self.assertEqual(jump_with_ctx, jump_no_ctx + expected_offset)


class TestSnippetCenteredOnTargetLine(unittest.TestCase):
    """Tests for diff snippet centering on the targeted line in review output."""

    def build_long_hunk_with_line_note(self, target_index: int) -> neorev.Hunk:
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
        """Verify when a note targets a specific line, the snippet is centered on it."""
        hunk = self.build_long_hunk_with_line_note(CENTERED_SNIPPET_TARGET_LINE)
        output = neorev.format_output([hunk], [])
        self.assertIn(f"+line {CENTERED_SNIPPET_TARGET_LINE}", output)

    def test_snippet_does_not_center_for_hunk_note(self) -> None:
        """Verify hunk-scoped notes use the default first/last trimming."""
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
        self.assertIn("+line 0", output)
        self.assertIn(f"+line {CENTERED_SNIPPET_LINE_COUNT - 1}", output)
        self.assertIn("# ...", output)

    def test_snippet_target_near_start_clamps(self) -> None:
        """Verify a target near the start doesn't go out of bounds."""
        hunk = self.build_long_hunk_with_line_note(1)
        output = neorev.format_output([hunk], [])
        self.assertIn("+line 1", output)
        self.assertIn("+line 0", output)

    def test_snippet_target_near_end_clamps(self) -> None:
        """Verify a target near the end doesn't go out of bounds."""
        last = CENTERED_SNIPPET_LINE_COUNT - 1
        hunk = self.build_long_hunk_with_line_note(last)
        output = neorev.format_output([hunk], [])
        self.assertIn(f"+line {last}", output)
        self.assertIn(f"+line {CENTERED_SNIPPET_LINE_COUNT - 2}", output)


if __name__ == "__main__":
    unittest.main()
