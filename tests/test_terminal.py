"""Terminal-focused tests for neorev."""

import contextlib
import fcntl
import os
import select
import struct
import tempfile
import termios
import tty
import unittest
from unittest.mock import MagicMock, patch

from tests.helpers import (
    CENTERED_SNIPPET_LINE_COUNT,
    CENTERED_SNIPPET_TARGET_LINE,
    COMMENT_KEY_QUESTION,
    DELETE_FILE_DIFF,
    DELTA_ADDED_BACKGROUND,
    DELTA_REMOVED_BACKGROUND,
    DELTA_WORD_BACKGROUND,
    DISPATCH_COMMENT_TEXT,
    DISPATCH_REDRAW_FALSE,
    ESC_ARROW_DOWN,
    ESC_ARROW_UP,
    GLOBAL_NOTE_ADD_FLAG_KEY,
    GLOBAL_NOTE_ADD_PREFIX,
    GLOBAL_NOTE_ADD_QUESTION_KEY,
    GLOBAL_NOTE_CREATED_TEXT,
    GLOBAL_NOTE_DELETE_KEY,
    GLOBAL_NOTE_EDIT_KEY,
    GLOBAL_NOTE_EDITED_TEXT,
    GRAPHEME_CLUSTER_SAMPLE,
    KEY_CTRL_C,
    LARGE_BODY_LINE_COUNT,
    LINE_PICKER_MANY_LINES,
    MANY_HUNKS_COUNT,
    MIDDLE_SCROLL_OFFSET,
    NARROW_FOOTER_WIDTH,
    NARROW_PROGRESS_WIDTH,
    OUT_OF_BOUNDS_OFFSET,
    OVERFLOW_HUNK_INDEX,
    OVERFLOWING_LINE_COUNT,
    PURE_RENAME_DIFF,
    RESIZE_WIDTH_DELTA,
    REVIEW_SCREEN_FOOTER_TOKEN,
    REVIEW_SCREEN_INDEX_TOKEN,
    REVIEW_SCREEN_LOCATION_TOKEN,
    SCROLL_HALF_PAGE,
    SIGWINCH_BYTE,
    SIMPLE_DIFF,
    TERM_HEIGHT,
    TERM_PIXEL_SIZE,
    TERM_WIDTH,
    TINY_WIDTH,
    TOP_BAR_INDEX_TOKEN,
    WIDE_CHARACTER_CELL_WIDTH,
    WIDE_CHARACTER_SAMPLE,
    WIDE_FOOTER_WIDTH,
    WIDE_GLYPH_SAMPLES,
    WINSIZE_FORMAT,
    FakeTTY,
    decode_visible_terminal_output,
    make_hunk,
    make_large_diff,
    neorev,
    remove_ansi_escape_sequences,
    terminal_cell_width,
)


class TestRenderingHelpers(unittest.TestCase):
    """Tests for ANSI text measurement, wrapping, and display-line building."""

    def test_visible_width_plain(self) -> None:
        """Verify plain ASCII text occupies one column per character."""
        self.assertEqual(neorev.visible_width("hello"), 5)

    def test_visible_width_ansi(self) -> None:
        """Verify ANSI escape sequences occupy no columns."""
        line = f"{neorev.GREEN}hello{neorev.RESET}"
        self.assertEqual(neorev.visible_width(line), 5)

    def test_wrap_ansi_line_empty(self) -> None:
        """Verify an empty line still occupies one display row."""
        self.assertEqual(len(neorev.wrap_ansi_line_to_rows(b"", TERM_WIDTH)), 1)

    def test_count_fitting_lines(self) -> None:
        """Verify compute_diff_viewport reserves rows for scroll indicators."""
        chrome = neorev.CHROME_ROWS
        vp = neorev.compute_diff_viewport(100, 10 + chrome, 0)
        self.assertEqual(vp.visible_line_count, 9)
        self.assertFalse(vp.can_scroll_up)
        self.assertTrue(vp.can_scroll_down)

    def test_count_fitting_lines_from_offset(self) -> None:
        """Verify compute_diff_viewport shows both indicators when mid-scroll."""
        chrome = neorev.CHROME_ROWS
        vp = neorev.compute_diff_viewport(100, 10 + chrome, 5)
        self.assertEqual(vp.visible_line_count, 8)
        self.assertTrue(vp.can_scroll_up)
        self.assertTrue(vp.can_scroll_down)

    def test_build_display_lines_strips_blanks(self) -> None:
        """Verify leading/trailing blank lines from delta output are stripped."""
        raw = b"\nline1\nline2\n"
        lines = neorev.build_display_lines(raw, TERM_WIDTH)
        self.assertEqual(lines[0].rstrip(), b"line1")
        self.assertEqual(lines[-1].rstrip(), b"line2")

    def test_build_display_lines_empty(self) -> None:
        """Verify empty input produces a single empty-bytes entry."""
        lines = neorev.build_display_lines(b"", TERM_WIDTH)
        self.assertEqual(lines, [b""])

    def test_wrap_ansi_line_short(self) -> None:
        """Verify a line shorter than term_width becomes one row padded to width."""
        line = b"hello"
        result = neorev.wrap_ansi_line_to_rows(line, TERM_WIDTH)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], b"hello" + b" " * (TERM_WIDTH - len(line)))

    def test_wrap_ansi_line_pads_with_trailing_style(self) -> None:
        """Verify row padding falls back to the trailing style with no erase."""
        line = f"{neorev.BG_BLUE}text{neorev.RESET}".encode()
        (row,) = neorev.wrap_ansi_line_to_rows(line, TERM_WIDTH)
        self.assertIn(neorev.BG_BLUE, row.decode())
        self.assertEqual(
            terminal_cell_width(remove_ansi_escape_sequences(row.decode())),
            TERM_WIDTH,
        )

    def test_wrap_ansi_line_exact(self) -> None:
        """Verify a line exactly term_width long produces one row."""
        line = b"x" * TERM_WIDTH
        result = neorev.wrap_ansi_line_to_rows(line, TERM_WIDTH)
        self.assertEqual(len(result), 1)

    def test_wrap_ansi_line_overflow(self) -> None:
        """Verify a line longer than term_width wraps into multiple rows."""
        line = b"x" * (TERM_WIDTH + TERM_WIDTH // 4)
        result = neorev.wrap_ansi_line_to_rows(line, TERM_WIDTH)
        self.assertEqual(len(result), 2)

    def test_visible_width_unicode(self) -> None:
        """Verify narrow multi-byte UTF-8 characters occupy one column each."""
        self.assertEqual(neorev.visible_width("héllo"), 5)

    def test_wrap_ansi_line_one_over(self) -> None:
        """Verify a line of term_width + 1 columns occupies 2 rows."""
        line = b"x" * (TERM_WIDTH + 1)
        self.assertEqual(len(neorev.wrap_ansi_line_to_rows(line, TERM_WIDTH)), 2)

    def test_count_fitting_lines_zero_budget(self) -> None:
        """Verify compute_diff_viewport enforces MIN_VISIBLE_ROWS."""
        chrome = neorev.CHROME_ROWS
        vp = neorev.compute_diff_viewport(100, 0 + chrome, 0)
        self.assertEqual(vp.visible_line_count, neorev.MIN_VISIBLE_ROWS)

    def test_count_fitting_lines_all_fit(self) -> None:
        """Verify when at end, compute_diff_viewport can disable down indicator."""
        vp = neorev.compute_diff_viewport(20, 10, 15)
        self.assertEqual(vp.visible_line_count, 5)
        self.assertTrue(vp.can_scroll_up)
        self.assertFalse(vp.can_scroll_down)

    def test_wrap_ansi_preserves_color_across_rows(self) -> None:
        """Verify a colored line that wraps carries color into the second row."""
        colored_line = f"{neorev.GREEN}{'x' * (TERM_WIDTH + 10)}{neorev.RESET}".encode()
        result = neorev.wrap_ansi_line_to_rows(colored_line, TERM_WIDTH)
        self.assertGreater(len(result), 1)
        second_row = result[1].decode("utf-8", errors="replace")
        self.assertIn(neorev.GREEN, second_row)

    def test_build_display_lines_multiple_wraps(self) -> None:
        """Verify lines exceeding width produce more display lines than raw lines."""
        long_line = b"x" * (TERM_WIDTH * 2)
        raw = long_line + b"\n" + b"short"
        lines = neorev.build_display_lines(raw, TERM_WIDTH)
        self.assertGreater(len(lines), 2)

    def test_visible_width_wide_characters(self) -> None:
        """Verify wide characters count as two terminal columns."""
        self.assertEqual(
            neorev.visible_width(WIDE_CHARACTER_SAMPLE), WIDE_CHARACTER_CELL_WIDTH
        )

    def test_wrap_ansi_line_wide_glyphs(self) -> None:
        """Verify rows holding wide glyphs stay within the terminal width."""
        for sample in WIDE_GLYPH_SAMPLES:
            with self.subTest(sample=sample):
                line = (sample * TERM_WIDTH).encode()
                for row in neorev.wrap_ansi_line_to_rows(line, TERM_WIDTH):
                    visible = remove_ansi_escape_sequences(row.decode())
                    self.assertLessEqual(terminal_cell_width(visible), TERM_WIDTH)

    def test_wrap_ansi_line_keeps_every_character(self) -> None:
        """Verify wrapping a grapheme cluster across the fold loses no text."""
        text = "x" * (TERM_WIDTH - 2) + GRAPHEME_CLUSTER_SAMPLE + "PAYLOAD"
        rows = neorev.wrap_ansi_line_to_rows(text.encode(), TERM_WIDTH)
        joined = "".join(
            remove_ansi_escape_sequences(row.decode()).rstrip() for row in rows
        )
        self.assertEqual(joined, text)

    def test_wrap_ansi_line_pads_with_erase_background(self) -> None:
        """Verify padding uses the background active at delta's erase sequence."""
        text = "foo x"
        line = (
            f"{DELTA_REMOVED_BACKGROUND}foo {DELTA_WORD_BACKGROUND}x{neorev.RESET}"
            f"{DELTA_REMOVED_BACKGROUND}{neorev.ERASE_TO_LINE_END}{neorev.RESET}"
        ).encode()
        (row,) = neorev.wrap_ansi_line_to_rows(line, TERM_WIDTH)
        padding = " " * (TERM_WIDTH - len(text))
        self.assertIn(DELTA_REMOVED_BACKGROUND + padding, row.decode())

    def test_wrap_ansi_line_keeps_empty_change_background(self) -> None:
        """Verify a changed line with no text keeps its background across the row."""
        line = f"{DELTA_ADDED_BACKGROUND}{neorev.ERASE_TO_LINE_END}{neorev.RESET}"
        (row,) = neorev.wrap_ansi_line_to_rows(line.encode(), TERM_WIDTH)
        self.assertIn(DELTA_ADDED_BACKGROUND + " " * TERM_WIDTH, row.decode())


class TestViewport(unittest.TestCase):
    """Tests for compute_diff_viewport."""

    def test_no_scrolling_needed(self) -> None:
        """Verify when content fits, no scroll indicators are shown."""
        line_rows = [1] * 5
        vp = neorev.compute_diff_viewport(len(line_rows), TERM_HEIGHT, 0)
        self.assertFalse(vp.can_scroll_up)
        self.assertFalse(vp.can_scroll_down)
        self.assertEqual(vp.scroll_offset, 0)

    def test_scrolling_needed(self) -> None:
        """Verify when content exceeds terminal height, scrolling is enabled."""
        line_rows = [1] * OVERFLOWING_LINE_COUNT
        vp = neorev.compute_diff_viewport(len(line_rows), TERM_HEIGHT, 0)
        self.assertFalse(vp.can_scroll_up)
        self.assertTrue(vp.can_scroll_down)

    def test_scroll_offset_clamped(self) -> None:
        """Verify scroll offset is clamped to valid range."""
        line_rows = [1] * OVERFLOWING_LINE_COUNT
        vp = neorev.compute_diff_viewport(
            len(line_rows),
            TERM_HEIGHT,
            OUT_OF_BOUNDS_OFFSET,
        )
        self.assertGreaterEqual(vp.scroll_offset, 0)
        self.assertLess(vp.scroll_offset, len(line_rows))

    def test_scrolled_to_middle(self) -> None:
        """Verify scrolling to the middle enables both scroll indicators."""
        line_rows = [1] * OVERFLOWING_LINE_COUNT
        vp = neorev.compute_diff_viewport(
            len(line_rows),
            TERM_HEIGHT,
            MIDDLE_SCROLL_OFFSET,
        )
        self.assertTrue(vp.can_scroll_up)
        self.assertTrue(vp.can_scroll_down)

    def test_single_line(self) -> None:
        """Verify a single line with a large terminal needs no scrolling."""
        line_rows = [1]
        vp = neorev.compute_diff_viewport(len(line_rows), TERM_HEIGHT, 0)
        self.assertFalse(vp.can_scroll_up)
        self.assertFalse(vp.can_scroll_down)
        self.assertEqual(vp.visible_line_count, 1)

    def test_exact_fit(self) -> None:
        """Verify content rows exactly filling available space needs no scrolling."""
        avail = TERM_HEIGHT - neorev.CHROME_ROWS
        line_rows = [1] * avail
        vp = neorev.compute_diff_viewport(len(line_rows), TERM_HEIGHT, 0)
        self.assertFalse(vp.can_scroll_up)
        self.assertFalse(vp.can_scroll_down)
        self.assertEqual(vp.visible_line_count, avail)

    def test_scroll_to_end(self) -> None:
        """Verify scrolling to a large offset clamps and disables scroll-down."""
        line_rows = [1] * OVERFLOWING_LINE_COUNT
        vp = neorev.compute_diff_viewport(
            len(line_rows),
            TERM_HEIGHT,
            OUT_OF_BOUNDS_OFFSET,
        )
        self.assertFalse(vp.can_scroll_down)
        self.assertTrue(vp.can_scroll_up)

    def test_scroll_to_end_fills_screen(self) -> None:
        """Verify scrolling to the end still fills the available screen with content."""
        total = OVERFLOWING_LINE_COUNT
        vp = neorev.compute_diff_viewport(total, TERM_HEIGHT, OUT_OF_BOUNDS_OFFSET)
        avail = TERM_HEIGHT - neorev.CHROME_ROWS - neorev.SCROLL_INDICATOR_ROWS
        self.assertGreaterEqual(vp.visible_line_count, min(avail, total))


class TestChrome(unittest.TestCase):
    """Tests for top bar, hunk markers, progress markers, and footer."""

    def test_top_bar_contains_index(self) -> None:
        """Verify top bar shows 'Hunk N/total'."""
        hunk = make_hunk()
        bar = neorev.build_top_bar(hunk, 0, [hunk] * 5, [])
        visible_bar = remove_ansi_escape_sequences(bar)
        self.assertIn(TOP_BAR_INDEX_TOKEN, visible_bar)

    def test_top_bar_global_count(self) -> None:
        """Verify top bar shows global note count when present."""
        hunk = make_hunk()
        global_notes = [
            neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="g1"),
            neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="g2"),
            neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="g3"),
        ]
        bar = neorev.build_top_bar(hunk, 0, [hunk], global_notes)
        self.assertIn("global", bar)
        self.assertIn("3", bar)

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
                self.assertIn(icon, file_status.marker)

    def test_removed_marker_uses_yellow(self) -> None:
        """Verify the deleted-file marker uses the yellow indexed color."""
        self.assertIn(neorev.YELLOW, neorev.FileStatus.DELETED.marker)

    def test_renamed_marker_uses_cyan(self) -> None:
        """Verify the renamed-file marker uses the cyan indexed color."""
        self.assertIn(neorev.CYAN, neorev.FileStatus.RENAMED.marker)

    def test_modified_marker_is_neutral(self) -> None:
        """Verify the modified-file marker carries no color, only the bare icon."""
        self.assertEqual(neorev.FileStatus.MODIFIED.marker, neorev.DIFF_MODIFIED_ICON)

    def test_top_bar_deleted_file(self) -> None:
        """Verify top bar marks a deleted file with the removed icon and no :0 line."""
        hunk = neorev.parse_diff(DELETE_FILE_DIFF)[0]
        bar = neorev.build_top_bar(hunk, 0, [hunk], [])
        self.assertIn(neorev.DIFF_REMOVED_ICON, bar)
        self.assertIn("old.py", bar)
        self.assertNotIn(":0", bar)

    def test_top_bar_renamed_file(self) -> None:
        """Verify the top bar shows an 'old → new' mapping and the renamed icon."""
        hunk = neorev.parse_diff(PURE_RENAME_DIFF)[0]
        bar = neorev.build_top_bar(hunk, 0, [hunk], [])
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
                marker = neorev.hunk_marker(hunk, is_current=False)
                self.assertIn(icon, marker)

    def test_current_marker_has_brackets(self) -> None:
        """Verify the current hunk marker is wrapped in brackets."""
        hunk = make_hunk()
        marker = neorev.hunk_marker(hunk, is_current=True)
        self.assertIn("[", marker)
        self.assertIn("]", marker)

    def test_progress_markers_count(self) -> None:
        """Verify progress markers line contains all hunk markers when they fit."""
        hunks = [make_hunk() for _ in range(5)]
        line = neorev.build_progress_markers(hunks, 2, TERM_WIDTH)
        self.assertEqual(line.count("·"), 5)

    def test_progress_markers_overflow(self) -> None:
        """Verify with many hunks, overflow arrows appear."""
        hunks = [make_hunk() for _ in range(MANY_HUNKS_COUNT)]
        line = neorev.build_progress_markers(
            hunks, OVERFLOW_HUNK_INDEX, NARROW_PROGRESS_WIDTH
        )
        self.assertIn("◀", line)
        self.assertIn("▶", line)

    def test_footer_contains_key_hints(self) -> None:
        """Verify footer line includes key hints."""
        footer = neorev.build_keyhint_footer(
            neorev.MAIN_FOOTER_SEGMENTS, WIDE_FOOTER_WIDTH, ellipsis=True
        )
        self.assertIn("j/k", footer)
        self.assertIn("quit", footer)

    def test_footer_truncates_narrow(self) -> None:
        """Verify a very narrow terminal truncates the footer."""
        footer = neorev.build_keyhint_footer(
            neorev.MAIN_FOOTER_SEGMENTS, NARROW_FOOTER_WIDTH, ellipsis=True
        )
        # Should not contain all segments.
        self.assertNotIn("help", footer)

    def test_progress_markers_single_hunk(self) -> None:
        """Verify a single hunk produces one marker with no overflow arrows."""
        hunks = [make_hunk()]
        line = neorev.build_progress_markers(hunks, 0, TERM_WIDTH)
        self.assertNotIn("◀", line)
        self.assertNotIn("▶", line)

    def test_progress_markers_at_start(self) -> None:
        """Verify at index 0 with many hunks, no left arrow but right arrow present."""
        hunks = [make_hunk() for _ in range(MANY_HUNKS_COUNT)]
        line = neorev.build_progress_markers(hunks, 0, NARROW_PROGRESS_WIDTH)
        self.assertNotIn("◀", line)
        self.assertIn("▶", line)

    def test_progress_markers_at_end(self) -> None:
        """Verify at the last index with many hunks, left arrow but no right arrow."""
        hunks = [make_hunk() for _ in range(MANY_HUNKS_COUNT)]
        line = neorev.build_progress_markers(
            hunks,
            MANY_HUNKS_COUNT - 1,
            NARROW_PROGRESS_WIDTH,
        )
        self.assertIn("◀", line)
        self.assertNotIn("▶", line)

    def test_footer_exact_width(self) -> None:
        """Verify a width that exactly fits all segments does not append ellipsis."""
        full_footer = neorev.build_keyhint_footer(
            neorev.MAIN_FOOTER_SEGMENTS, WIDE_FOOTER_WIDTH, ellipsis=True
        )
        visible = neorev.visible_width(full_footer)
        exact_footer = neorev.build_keyhint_footer(
            neorev.MAIN_FOOTER_SEGMENTS, visible, ellipsis=True
        )
        self.assertNotIn("…", exact_footer)


class TestTopBarTruncation(unittest.TestCase):
    """Tests for build_top_bar width truncation."""

    def test_narrow_width_truncates(self) -> None:
        """Verify top bar is truncated to an ellipsis when term_width is small."""
        hunk = make_hunk()
        bar = neorev.build_top_bar(
            hunk, 0, [hunk], [], term_width=NARROW_PROGRESS_WIDTH
        )
        self.assertLessEqual(neorev.visible_width(bar), NARROW_PROGRESS_WIDTH)
        visible = remove_ansi_escape_sequences(bar)
        self.assertTrue(visible.endswith(neorev.TRUNCATION_ELLIPSIS))

    def test_fitting_width_passes_bar_through(self) -> None:
        """Verify a bar that fits reaches the terminal exactly as it was built."""
        hunk = make_hunk()
        untruncated = neorev.build_top_bar(hunk, 0, [hunk], [], term_width=None)
        bar = neorev.build_top_bar(hunk, 0, [hunk], [], term_width=WIDE_FOOTER_WIDTH)
        self.assertEqual(bar, untruncated)

    def test_no_truncation_without_width(self) -> None:
        """Verify top bar is not truncated when term_width is None (default)."""
        hunk = make_hunk()
        bar = neorev.build_top_bar(hunk, 0, [hunk], [], term_width=None)
        visible = neorev.visible_width(bar)
        self.assertGreater(visible, NARROW_PROGRESS_WIDTH)


class TestProgressMarkersTinyWidth(unittest.TestCase):
    """Tests for build_progress_markers with tiny terminal widths."""

    def test_very_narrow_returns_empty(self) -> None:
        """Verify extremely narrow terminals produce an empty marker line."""
        hunks = [neorev.Hunk(range_line="", body="", raw="", file_path="f.py")]
        # prefix_width=2, so available < MARKER_WIDTH
        too_narrow = neorev.MARKER_WIDTH + 1
        result = neorev.build_progress_markers(hunks, 0, too_narrow)
        self.assertEqual(result, "")

    def test_marker_width_boundary(self) -> None:
        """Verify widths exactly fitting one marker still produce output."""
        hunks = [neorev.Hunk(range_line="", body="", raw="", file_path="f.py")]
        min_working_width = neorev.MARKER_WIDTH + 2  # prefix_width = 2
        result = neorev.build_progress_markers(hunks, 0, min_working_width)
        self.assertNotEqual(result, "")


class TestFooterTinyWidth(unittest.TestCase):
    """Tests for build_keyhint_footer with very small widths."""

    def test_zero_width(self) -> None:
        """Verify zero width produces empty footer."""
        result = neorev.build_keyhint_footer(neorev.MAIN_FOOTER_SEGMENTS, 0)
        self.assertEqual(result, "")

    def test_tiny_width_no_crash(self) -> None:
        """Verify tiny widths produce a footer without crashing."""
        for w in range(1, TINY_WIDTH + 1):
            result = neorev.build_keyhint_footer(neorev.MAIN_FOOTER_SEGMENTS, w)
            visible = neorev.visible_width(result)
            self.assertLessEqual(visible, w)


class TestViewportClampOnResize(unittest.TestCase):
    """Tests for viewport clamping after height changes."""

    def test_scroll_clamped_after_height_increase(self) -> None:
        """Verify increasing height clamps scroll offset to valid range."""
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
        """Verify decreasing height still produces a valid viewport."""
        line_rows = [1] * OVERFLOWING_LINE_COUNT
        vp = neorev.compute_diff_viewport(
            len(line_rows),
            neorev.MIN_TERMINAL_HEIGHT,
            MIDDLE_SCROLL_OFFSET,
        )
        self.assertGreaterEqual(vp.visible_line_count, 1)


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
        """Verify context shows surrounding lines with marker on the target."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=3)
        ctx = hunk.build_line_context(target)
        marker_lines = [c for c in ctx if neorev.EDITOR_TARGET_MARKER in c]
        self.assertEqual(len(marker_lines), 1)
        self.assertIn("new three", marker_lines[0])

    def test_context_around_removed_line(self) -> None:
        """Verify context marks the removed line with the target marker."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.REMOVED, line_number=3)
        ctx = hunk.build_line_context(target)
        marker_lines = [c for c in ctx if neorev.EDITOR_TARGET_MARKER in c]
        self.assertEqual(len(marker_lines), 1)
        self.assertIn("old three", marker_lines[0])

    def test_context_includes_diff_prefix(self) -> None:
        """Verify each context line includes the diff prefix from its kind."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=3)
        ctx = hunk.build_line_context(target)
        added = [c for c in ctx if "new three" in c]
        self.assertTrue(any("+" in c for c in added))
        context = [c for c in ctx if "line two" in c]
        self.assertTrue(len(context) > 0)

    def test_context_radius_limits(self) -> None:
        """Verify context does not exceed the configured radius."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=3)
        ctx = hunk.build_line_context(target)
        max_lines = 2 * neorev.EDITOR_CONTEXT_RADIUS + 1
        self.assertLessEqual(len(ctx), max_lines)

    def test_context_at_start_of_hunk(self) -> None:
        """Verify context near the beginning does not go out of bounds."""
        hunk = make_hunk(
            range_line="@@ -1,2 +1,2 @@",
            body="+added\n context",
        )
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=1)
        ctx = hunk.build_line_context(target)
        self.assertTrue(len(ctx) >= 1)
        self.assertIn(neorev.EDITOR_TARGET_MARKER, ctx[0])

    def test_unknown_target_returns_empty(self) -> None:
        """Verify a target not in the display lines returns an empty list."""
        hunk = self.make_hunk_with_context()
        target = neorev.LineTarget(side=neorev.LineSide.ADDED, line_number=999)
        ctx = hunk.build_line_context(target)
        self.assertEqual(ctx, [])

    def test_context_lines_are_aligned(self) -> None:
        """Verify all context lines have the same length up to the diff prefix."""
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
        """Verify context lines begin at the given scroll offset."""
        hunk = self.make_hunk()
        ctx = hunk.build_hunk_context(scroll_offset=2)
        self.assertIn("old three", ctx[0])

    def test_respects_max_lines(self) -> None:
        """Verify context never exceeds EDITOR_HUNK_CONTEXT_MAX lines."""
        body = "\n".join(f"+line {i}" for i in range(30))
        hunk = make_hunk(range_line="@@ -1,0 +1,30 @@", body=body)
        ctx = hunk.build_hunk_context(scroll_offset=0)
        self.assertEqual(len(ctx), neorev.EDITOR_HUNK_CONTEXT_MAX)

    def test_offset_zero_starts_at_beginning(self) -> None:
        """Verify offset zero returns lines from the start of the hunk."""
        hunk = self.make_hunk()
        ctx = hunk.build_hunk_context(scroll_offset=0)
        self.assertIn("line one", ctx[0])

    def test_offset_past_end_returns_empty(self) -> None:
        """Verify an offset beyond the display lines returns an empty list."""
        hunk = self.make_hunk()
        ctx = hunk.build_hunk_context(scroll_offset=999)
        self.assertEqual(ctx, [])

    def test_negative_offset_clamps_to_zero(self) -> None:
        """Verify a negative offset is clamped to zero."""
        hunk = self.make_hunk()
        ctx_neg = hunk.build_hunk_context(scroll_offset=-5)
        ctx_zero = hunk.build_hunk_context(scroll_offset=0)
        self.assertEqual(ctx_neg, ctx_zero)

    def test_lines_use_context_pad(self) -> None:
        """Verify hunk context lines use the context pad, not the target marker."""
        hunk = self.make_hunk()
        ctx = hunk.build_hunk_context(scroll_offset=0)
        for line in ctx:
            self.assertNotIn(neorev.EDITOR_TARGET_MARKER, line)
            self.assertIn(neorev.EDITOR_CONTEXT_PAD, line)

    def test_includes_diff_prefix(self) -> None:
        """Verify context lines include the diff prefix character."""
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
        """Verify context lines appear as # comments in the template."""
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
        """Verify context lines (starting with #) are stripped when reading back."""
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
        """Verify jump line is offset by the number of context lines."""
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
        """Verify when a note targets a specific line, the snippet is centered on it."""
        hunk = self.build_long_hunk_with_line_note(
            CENTERED_SNIPPET_TARGET_LINE,
        )
        output = neorev.format_output([hunk], [])
        # The targeted line should appear in the snippet
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
        # Default trimming: first 5 and last 5 lines present, middle absent
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
        """Verify a single ASCII byte is returned as a string."""
        tty.setraw(self.fake.slave_fd)
        self.fake.inject_keys(b"j")
        key = self.term.read_key()
        self.assertEqual(key, "j")

    def test_read_arrow_up(self) -> None:
        """Verify ESC [ A is normalised to 'up'."""
        tty.setraw(self.fake.slave_fd)
        self.fake.inject_keys(ESC_ARROW_UP)
        key = self.term.read_key()
        self.assertEqual(key, "up")

    def test_read_arrow_down(self) -> None:
        """Verify ESC [ B is normalised to 'down'."""
        tty.setraw(self.fake.slave_fd)
        self.fake.inject_keys(ESC_ARROW_DOWN)
        key = self.term.read_key()
        self.assertEqual(key, "down")

    def test_read_ctrl_c(self) -> None:
        """Verify Ctrl-C is returned as the raw byte."""
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
        """Verify Terminal.write accepts strings."""
        self.term.write("hello")
        output = self.fake.read_output()
        self.assertIn(b"hello", output)

    def test_write_bytes(self) -> None:
        """Verify Terminal.write accepts bytes."""
        self.term.write(b"world")
        output = self.fake.read_output()
        self.assertIn(b"world", output)

    def test_render_review_screen(self) -> None:
        """Verify render_review_screen writes output containing hunk info."""
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
        """Verify render_help_screen writes the help box."""
        self.term.render_help_screen()
        output = self.fake.read_output()
        self.assertIn(b"neorev", output)

    def test_help_screen_fits_80_columns(self) -> None:
        """Verify every help screen line fits within an 80-column terminal."""
        self.term.render_help_screen()
        output = self.fake.read_output()
        visible = decode_visible_terminal_output(output)
        for line in visible.splitlines():
            stripped = line.rstrip()
            if stripped:
                self.assertLessEqual(len(stripped), TERM_WIDTH, repr(stripped))

    def test_render_note_panel_empty(self) -> None:
        """Verify note panel with no notes shows 'No notes yet'."""
        state = neorev.ReviewState(hunks=[make_hunk()], global_notes=[])
        panel = neorev.NotePanelState()
        self.term.render_note_panel(state, [], panel, b"")
        output = self.fake.read_output()
        self.assertIn(b"No notes", output)

    def test_render_note_panel_with_notes(self) -> None:
        """Verify note panel lists existing notes."""
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
        """Verify note panel diff fills all rows above the panel without blank gaps."""
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
        """Stand in as a redraw callback and count invocations."""
        self.redraw_count += 1

    def tearDown(self) -> None:
        """Restore terminal state and close the pty."""
        with contextlib.suppress(OSError):
            self.term.close()
        self.fake.close()

    def test_dispatch_navigate(self) -> None:
        """Verify dispatch_key('j') navigates and requests redraw."""
        result = self.term.dispatch_key("j", self.state, self.redraw)
        self.assertTrue(result)
        self.assertEqual(self.state.current_index, 1)

    def test_dispatch_approve(self) -> None:
        """Verify dispatch_key('a') approves the current hunk."""
        result = self.term.dispatch_key("a", self.state, self.redraw)
        self.assertTrue(result)
        self.assertTrue(self.hunks[0].approved)

    def test_dispatch_approve_file(self) -> None:
        """Verify dispatch_key('A') approves all hunks in the current file."""
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
        """Verify adding a hunk-level note jumps to the next unhandled hunk."""
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
        """Verify adding a line-level note does not jump to the next hunk."""
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
        """Verify an unrecognised key returns False (no redraw)."""
        result = self.term.dispatch_key("z", self.state, self.redraw)
        self.assertFalse(result)

    def test_dispatch_scroll_ctrl_d(self) -> None:
        """Verify Ctrl-D scrolls down and triggers redraw callback."""
        self.term.dispatch_key(neorev.Terminal.KEY_CTRL_D, self.state, self.redraw)
        self.assertGreater(self.state.scroll_offset, 0)
        self.assertEqual(self.redraw_count, 1)

    def test_dispatch_scroll_ctrl_u(self) -> None:
        """Verify Ctrl-U from offset 0 stays at 0."""
        self.term.dispatch_key(neorev.Terminal.KEY_CTRL_U, self.state, self.redraw)
        self.assertEqual(self.state.scroll_offset, 0)

    def test_dispatch_help(self) -> None:
        """Verify dispatch_key('?') renders the help screen (needs a key to dismiss)."""
        tty.setraw(self.fake.slave_fd)
        self.fake.inject_keys(b"q")  # Key to dismiss help.
        result = self.term.dispatch_key("?", self.state, self.redraw)
        self.assertTrue(result)

    def test_dispatch_scroll_ctrl_d_increments(self) -> None:
        """Verify Ctrl-D increments scroll_offset by half-page amount."""
        self.state.scroll_offset = 0
        self.term.dispatch_key(neorev.Terminal.KEY_CTRL_D, self.state, self.redraw)
        self.assertEqual(self.state.scroll_offset, SCROLL_HALF_PAGE)

    def test_dispatch_scroll_ctrl_u_clamps_to_zero(self) -> None:
        """Verify Ctrl-U from a small offset clamps to 0."""
        self.state.scroll_offset = 1
        self.term.dispatch_key(neorev.Terminal.KEY_CTRL_U, self.state, self.redraw)
        self.assertEqual(self.state.scroll_offset, 0)

    def test_dispatch_g_followed_by_invalid(self) -> None:
        """Verify pressing g then an invalid key returns False."""
        tty.setraw(self.fake.slave_fd)
        self.fake.inject_keys(b"z")
        result = self.term.dispatch_key("g", self.state, self.redraw)
        self.assertFalse(result)

    def test_dispatch_m_opens_manage_notes(self) -> None:
        """Verify pressing m dispatches to handle_manage_notes and requests redraw."""
        with patch.object(self.term, "handle_manage_notes"):
            result = self.term.dispatch_key("m", self.state, self.redraw)
        self.assertTrue(result)

    def test_dispatch_navigate_resets_scroll(self) -> None:
        """Verify navigating after scrolling resets scroll_offset to 0."""
        self.state.scroll_offset = 10
        self.term.dispatch_key("j", self.state, self.redraw)
        self.assertEqual(self.state.scroll_offset, 0)


class TestDispatchKeys(unittest.TestCase):
    """Tests for specific dispatch_key behaviors."""

    def setUp(self) -> None:
        """Create a fake TTY, Terminal, and a two-hunk state."""
        self.fake = FakeTTY()
        self.term = self.fake.make_terminal()
        self.hunks = [make_hunk(file_path="a.py"), make_hunk(file_path="b.py")]
        self.state = neorev.ReviewState(hunks=self.hunks, global_notes=[])

    def redraw(self) -> None:
        """Stand in as a no-op redraw callback."""

    def tearDown(self) -> None:
        """Restore terminal state and close the pty."""
        with contextlib.suppress(OSError):
            self.term.close()
        self.fake.close()

    def test_m_opens_note_manager(self) -> None:
        """Verify pressing 'm' dispatches to handle_manage_notes and requests redraw."""
        with patch.object(self.term, "handle_manage_notes"):
            result = self.term.dispatch_key("m", self.state, self.redraw)
        self.assertTrue(result)

    def test_g_no_longer_manages_notes(self) -> None:
        """Verify pressing 'G' should return False (not handled)."""
        result = self.term.dispatch_key("G", self.state, self.redraw)
        self.assertFalse(result)


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
        """Verify pressing g then c appends a global question note."""
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
        """Verify pressing g then f appends a global change-request note."""
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
        """Verify editing a note from the manage menu closes the menu."""
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
        """Verify deleting a note from the manage menu closes the menu."""
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
        """Verify all pending bytes are consumed from the fd."""
        os.write(self.write_fd, b"abc")
        neorev.drain_fd(self.read_fd)
        ready, _, _ = select.select([self.read_fd], [], [], neorev.SELECT_IMMEDIATE)
        self.assertFalse(ready)

    def test_no_data_does_not_block(self) -> None:
        """Verify calling drain_fd with no pending data returns immediately."""
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
        """Verify when no further signal arrives, debounce returns after timeout."""
        neorev.debounce_resize(self.read_fd)

    def test_coalesces_pending_bytes(self) -> None:
        """Verify pending bytes written before call are drained."""
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
        """Verify a byte on the wakeup pipe makes read_key return RESIZE_KEY."""
        tty.setraw(self.fake.slave_fd)
        os.write(self.wakeup_w, SIGWINCH_BYTE)
        key = self.term.read_key(wakeup_read_fd=self.wakeup_r)
        self.assertEqual(key, neorev.Terminal.KEY_RESIZE)

    def test_tty_input_still_works_with_wakeup(self) -> None:
        """Verify normal keypresses are returned even when wakeup fd is set."""
        tty.setraw(self.fake.slave_fd)
        self.fake.inject_keys(b"x")
        key = self.term.read_key(wakeup_read_fd=self.wakeup_r)
        self.assertEqual(key, "x")

    def test_no_wakeup_fd_reads_normally(self) -> None:
        """Verify negative wakeup_read_fd falls through to normal read."""
        tty.setraw(self.fake.slave_fd)
        self.fake.inject_keys(b"k")
        key = self.term.read_key(wakeup_read_fd=None)
        self.assertEqual(key, "k")

    def test_wakeup_drains_pipe(self) -> None:
        """Verify after returning RESIZE_KEY the pipe is fully drained."""
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
        """Verify delta cache is cleared when terminal width changes."""
        stream = MagicMock(spec=neorev.DeltaStream)
        cache: dict[int, neorev.DeltaStream] = {0: stream}
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
        stream.kill.assert_called_once()

    def test_cache_kept_on_same_width(self) -> None:
        """Verify delta cache is kept when width does not change."""
        stream = MagicMock(spec=neorev.DeltaStream)
        cache: dict[int, neorev.DeltaStream] = {0: stream}
        self.term.apply_resize(cache)
        self.assertEqual(len(cache), 1)
        stream.kill.assert_not_called()


class TestScrollWithPartialStream(unittest.TestCase):
    """Tests for scrolling when only partial delta output is available."""

    def setUp(self) -> None:
        """Create a fake TTY and Terminal for rendering tests."""
        self.fake = FakeTTY()
        self.term = self.fake.make_terminal()

    def tearDown(self) -> None:
        """Restore terminal state and close the pty."""
        with contextlib.suppress(OSError):
            self.term.close()
        self.fake.close()

    def test_scroll_offset_clamped_to_actual_content(self) -> None:
        """Verify scroll offset must not exceed the actual rendered content."""
        hunks = neorev.parse_diff(make_large_diff())
        hunk = hunks[0]
        body_lines = hunk.body.split("\n")
        half = len(body_lines) // 2
        truncated_body = "\n".join(body_lines[:half])
        delta_output = truncated_body.encode()
        diff_lines = neorev.build_display_lines(delta_output, self.term.width)
        far_scroll = len(diff_lines) + TERM_HEIGHT
        returned_offset = self.term.render_review_screen(
            hunks,
            0,
            delta_output,
            [],
            scroll_offset=far_scroll,
        )
        self.assertLessEqual(
            returned_offset + 1,
            len(diff_lines),
            "scroll offset must be clamped so at least one line is visible",
        )

    def test_no_lines_below_count_while_streaming(self) -> None:
        """Verify while the stream is not exhausted, no 'lines below' is shown."""
        hunks = neorev.parse_diff(make_large_diff())
        hunk = hunks[0]
        # Use only half the body so the content is clearly scrollable.
        body_lines = hunk.body.split("\n")
        half_body = "\n".join(body_lines[: len(body_lines) // 2])
        delta_output = half_body.encode()
        self.term.render_review_screen(
            hunks,
            0,
            delta_output,
            [],
            scroll_offset=0,
            streaming=True,
        )
        output = self.fake.read_output()
        visible = decode_visible_terminal_output(output)
        self.assertNotIn("lines below", visible)


class TestDeltaStream(unittest.TestCase):
    """Tests for DeltaStream incremental delta output reading."""

    def test_kill_already_finished(self) -> None:
        """Verify killing a stream whose delta process already exited does not raise."""
        stream = neorev.DeltaStream(make_large_diff(), TERM_WIDTH)
        stream.read_all()
        stream.kill()

    def test_incremental_read(self) -> None:
        """Verify reading fewer lines than total allows draining the rest later."""
        stream = neorev.DeltaStream(make_large_diff(), TERM_WIDTH)
        try:
            stream.ensure_lines(3)
            self.assertGreaterEqual(len(stream.lines), 3)
            all_lines = stream.read_all()
            self.assertGreaterEqual(len(all_lines), LARGE_BODY_LINE_COUNT)
        finally:
            stream.kill()

    def test_exhausted_output_is_stable_across_counts(self) -> None:
        """Verify get_output returns all cached content for any count once exhausted."""
        stream = neorev.DeltaStream(make_large_diff(), TERM_WIDTH)
        try:
            stream.read_all()
            self.assertTrue(stream.exhausted)
            small = stream.get_output(1)
            large = stream.get_output(LARGE_BODY_LINE_COUNT * 10)
            self.assertEqual(small, large)
        finally:
            stream.kill()


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
        """Verify a resize signal during line selection refreshes terminal geometry."""
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
        """Verify a resize during line selection re-renders delta at the new width."""
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
        """Verify moving the cursor down keeps it within the visible viewport."""
        body = "\n".join(f"+line {i}" for i in range(LINE_PICKER_MANY_LINES))
        range_line = f"@@ -0,0 +1,{LINE_PICKER_MANY_LINES} @@"
        hunk = make_hunk(range_line=range_line, body=body)
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
