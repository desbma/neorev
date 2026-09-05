"""Diff-parsing tests for neorev."""

import unittest

from tests.helpers import (
    BINARY_DIFF,
    CONTEXT_LABEL_DIFF,
    DELETE_FILE_DIFF,
    HUNKS_PER_FILE,
    MULTI_FILE_HUNK_COUNT,
    MULTI_FILE_MULTI_HUNK_DIFF,
    NEW_FILE_DIFF,
    NO_NEWLINE_DIFF,
    PURE_RENAME_DIFF,
    REMOVED_LINE_NUMBER,
    RENAME_FILE_DIFF,
    SIMPLE_DIFF,
    TWO_DELETED_FILES_DIFF,
    TWO_FILE_DIFF,
    TWO_HUNK_DIFF,
    make_hunk,
    neorev,
)


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
        """Verify parsing an empty string produces no hunks."""
        self.assertEqual(neorev.parse_diff(""), [])

    def test_no_hunks(self) -> None:
        """Verify a diff header with no @@ lines yields no hunks."""
        diff = "diff --git a/f b/f\n--- a/f\n+++ b/f\n"
        self.assertEqual(neorev.parse_diff(diff), [])

    def test_short_location(self) -> None:
        """Verify Hunk.short_location returns 'file:line'."""
        hunk = neorev.parse_diff(SIMPLE_DIFF)[0]
        self.assertEqual(hunk.short_location, "hello.py:1")

    def test_short_location_no_file(self) -> None:
        """Verify short_location falls back to range_line when file_path is absent."""
        hunk = make_hunk()
        hunk.file_path = None
        self.assertEqual(hunk.short_location, hunk.range_line.strip())

    def test_hunk_raw_includes_header(self) -> None:
        """Verify the raw field should include the file header and range line."""
        hunk = neorev.parse_diff(SIMPLE_DIFF)[0]
        self.assertIn("diff --git", hunk.raw)
        self.assertIn("@@", hunk.raw)

    def test_file_path_strips_b_prefix(self) -> None:
        """Verify the b/ prefix is stripped from the +++ line."""
        diff = (
            "diff --git a/src/x.py b/src/x.py\n"
            "--- a/src/x.py\n+++ b/src/x.py\n@@ -1 +1,2 @@\n+line\n"
        )
        hunks = neorev.parse_diff(diff)
        self.assertEqual(hunks[0].file_path, "src/x.py")

    def test_binary_file_diff(self) -> None:
        """Verify a binary diff with no @@ lines produces no hunks."""
        self.assertEqual(neorev.parse_diff(BINARY_DIFF), [])

    def test_new_file_mode_diff(self) -> None:
        """Verify a new-file diff parses the file_path, hunk, and ADDED status."""
        hunks = neorev.parse_diff(NEW_FILE_DIFF)
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0].file_path, "new.py")
        self.assertIn("+def hello():", hunks[0].body)
        self.assertIs(hunks[0].file_status, neorev.FileStatus.ADDED)

    def test_delete_file_diff(self) -> None:
        """Verify a deleted-file diff resolves to its source path and DELETED."""
        hunks = neorev.parse_diff(DELETE_FILE_DIFF)
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0].file_path, "old.py")
        self.assertIs(hunks[0].file_status, neorev.FileStatus.DELETED)

    def test_rename_file_diff(self) -> None:
        """Verify a renamed-file diff exposes the new path, old path, and RENAMED."""
        hunks = neorev.parse_diff(RENAME_FILE_DIFF)
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0].file_path, "new.txt")
        self.assertEqual(hunks[0].old_path, "old.txt")
        self.assertIs(hunks[0].file_status, neorev.FileStatus.RENAMED)

    def test_pure_rename_diff(self) -> None:
        """Verify a content-free move still yields one RENAMED hunk with both paths."""
        hunks = neorev.parse_diff(PURE_RENAME_DIFF)
        self.assertEqual(len(hunks), 1)
        hunk = hunks[0]
        self.assertIs(hunk.file_status, neorev.FileStatus.RENAMED)
        self.assertEqual(hunk.file_path, "new.txt")
        self.assertEqual(hunk.old_path, "old.txt")
        self.assertEqual(hunk.body, "")

    def test_rename_location_label(self) -> None:
        """Verify a rename's location label spells out the old and new paths."""
        hunk = neorev.parse_diff(PURE_RENAME_DIFF)[0]
        self.assertEqual(hunk.location_label, f"old.txt {neorev.RENAME_ARROW} new.txt")

    def test_modified_file_status(self) -> None:
        """Verify an ordinary edit is reported as MODIFIED."""
        hunks = neorev.parse_diff(SIMPLE_DIFF)
        self.assertIs(hunks[0].file_status, neorev.FileStatus.MODIFIED)

    def test_deleted_files_have_distinct_paths(self) -> None:
        """Verify two deleted files keep their paths instead of sharing /dev/null."""
        hunks = neorev.parse_diff(TWO_DELETED_FILES_DIFF)
        self.assertEqual([h.file_path for h in hunks], ["first.py", "second.py"])
        for hunk in hunks:
            self.assertIs(hunk.file_status, neorev.FileStatus.DELETED)

    def test_multiple_hunks_across_multiple_files(self) -> None:
        """Parse 3 files with 2 hunks each into 6 hunks."""
        hunks = neorev.parse_diff(MULTI_FILE_MULTI_HUNK_DIFF)
        self.assertEqual(len(hunks), MULTI_FILE_HUNK_COUNT)
        files = [h.file_path for h in hunks]
        for name in ("a.py", "b.py", "c.py"):
            self.assertEqual(files.count(name), HUNKS_PER_FILE)

    def test_hunk_no_plus_in_range(self) -> None:
        """Verify a deletion-only range @@ -1,2 +0,0 @@ yields start_line 0."""
        hunks = neorev.parse_diff(DELETE_FILE_DIFF)
        self.assertEqual(hunks[0].start_line, 0)

    def test_no_newline_marker_in_body(self) -> None:
        """Verify the 'No newline at end of file' marker is kept in the hunk body."""
        hunks = neorev.parse_diff(NO_NEWLINE_DIFF)
        self.assertEqual(len(hunks), 1)
        self.assertIn("No newline at end of file", hunks[0].body)

    def test_range_line_with_context_label(self) -> None:
        """Verify a range line with a context label still parses start_line."""
        hunks = neorev.parse_diff(CONTEXT_LABEL_DIFF)
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0].start_line, 10)

    def test_short_location_deleted_omits_line(self) -> None:
        """Verify a deleted single-hunk file's location is just the path, without :0."""
        hunk = neorev.parse_diff(DELETE_FILE_DIFF)[0]
        self.assertEqual(hunk.start_line, 0)
        self.assertEqual(hunk.short_location, "old.py")


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
