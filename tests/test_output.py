"""Output-related tests for neorev."""

import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import (
    ADDED_LINE_NUMBER,
    FENCED_BODY_DIFF,
    GLOBAL_PARSE_NOTE_TEXT,
    GLOBAL_PATH_DIFF,
    HEADING_BODY_DIFF,
    HUNK_IDENTITY_DIFFS,
    INDENTED_FENCE_BODY_DIFF,
    LINE_TARGET_APPLY_TEXT,
    LINE_TARGET_NOTE_LINE,
    LINE_TARGET_NOTE_TEXT,
    LONG_BODY_LINE_COUNT,
    REMOVED_LINE_NUMBER,
    REOPEN_CYCLE_COUNT,
    ROUND_TRIP_COMMENT_TEXT,
    ROUND_TRIP_NOTE_TEXTS,
    SIMPLE_DIFF,
    TWO_FILE_DIFF,
    WIDE_FENCE_BODY_DIFF,
    make_hunk,
    neorev,
    reopen_review,
)

FENCE_TEST_TEXT = "why this change?"
FENCE_TEST_RANGE_LINE = "@@ -1,1 +1,2 @@"
BACKTICK_RUN_BODIES = {
    "no backticks": ("+plain line", 3),
    "single backtick": ("+use `foo`", 3),
    "double backtick": ("+use ``foo``", 3),
    "triple backtick": ("+```", 4),
    "quadruple backtick": ("+````", 5),
}
ROUND_TRIP_DIFFS = {
    "plain body": SIMPLE_DIFF,
    "fenced body": FENCED_BODY_DIFF,
    "indented fenced body": INDENTED_FENCE_BODY_DIFF,
    "wide fenced body": WIDE_FENCE_BODY_DIFF,
    "heading body": HEADING_BODY_DIFF,
}
SECTION_PROLOGUE = (
    "<!-- neorev: note-anchor=abc -->\n\n```diff\n@@ -1,2 +1,3 @@\n+added\n```\n\n"
)
SECTION_PROLOGUE_ANCHOR = "abc"
FOOTER_ENDED_NOTE_TEXT = "before\n<!-- neorev: approved-hashes=AAAAAAAAAAA= -->"
FOLLOWING_NOTE_TEXT = "the note that follows"
FOOTER_TEST_NOTE_TEXT = "fix the bug"
FOOTER_TEST_ENCODING = "AQ=="


class TestFormatOutput(unittest.TestCase):
    """Tests for format_output and friends."""

    def test_all_approved(self) -> None:
        """Verify all approved hunks produce a short 'all clear' output."""
        hunks = [
            make_hunk(status=neorev.Status.APPROVED),
            make_hunk(status=neorev.Status.APPROVED),
        ]
        output = neorev.format_output(hunks, [])
        self.assertIn("all clear", output)
        self.assertIn("neorev:", output)

    def test_flag_output(self) -> None:
        """Verify a flagged hunk appears as CHANGE REQUESTED in the output."""
        hunks = [make_hunk(status=neorev.Status.FLAG, comment="fix this")]
        output = neorev.format_output(hunks, [])
        self.assertIn("CHANGE REQUESTED", output)
        self.assertIn("fix this", output)

    def test_question_output(self) -> None:
        """Verify a questioned hunk appears as QUESTION in the output."""
        hunks = [make_hunk(status=neorev.Status.QUESTION, comment="why?")]
        output = neorev.format_output(hunks, [])
        self.assertIn("QUESTION", output)
        self.assertIn("why?", output)

    def test_global_notes_in_output(self) -> None:
        """Verify global notes appear in the output."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        notes = [neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text="add tests")]
        output = neorev.format_output(hunks, notes)
        self.assertIn("`global`", output)
        self.assertIn("add tests", output)

    def test_long_hunk_body_trimmed(self) -> None:
        """Verify hunk bodies exceeding HUNK_BODY_MAX_LINES are trimmed."""
        long_body = "\n".join(f"+line {i}" for i in range(LONG_BODY_LINE_COUNT))
        hunks = [
            make_hunk(body=long_body, status=neorev.Status.FLAG, comment="too long")
        ]
        output = neorev.format_output(hunks, [])
        self.assertIn("# ...", output)

    def test_footer_present_in_output(self) -> None:
        """Verify output always contains a neorev approved-hashes footer comment."""
        hunks = [make_hunk(status=neorev.Status.FLAG, comment="x")]
        output = neorev.format_output(hunks, [])
        self.assertIn("<!-- neorev: approved-hashes=", output)

    def test_no_status_hunks(self) -> None:
        """Verify hunks with no status and no actionable items get 'all clear'."""
        hunks = [make_hunk(), make_hunk()]
        output = neorev.format_output(hunks, [])
        self.assertIn("all clear", output)
        self.assertIn("0/2 hunks approved", output)
        self.assertIn("<!-- neorev: approved-hashes=", output)

    def test_global_note_question_label(self) -> None:
        """Verify a global question note section header uses QUESTION label."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        notes = [
            neorev.GlobalNote(kind=neorev.NoteKind.QUESTION, text="why this approach?")
        ]
        output = neorev.format_output(hunks, notes)
        self.assertIn("[REVIEW QUESTION] `global`", output)
        self.assertNotIn("[REVIEW CHANGE REQUESTED] `global`", output)

    def test_multiline_comment_preserved(self) -> None:
        """Verify each line of a multi-line comment appears as plain text."""
        hunks = [
            make_hunk(
                status=neorev.Status.FLAG, comment="line one\nline two\nline three"
            )
        ]
        output = neorev.format_output(hunks, [])
        self.assertIn("line one\nline two\nline three", output)

    def test_body_exactly_max_lines_not_trimmed(self) -> None:
        """Verify a body with exactly HUNK_BODY_MAX_LINES lines is not trimmed."""
        body = "\n".join(f"+line {i}" for i in range(neorev.HUNK_BODY_MAX_LINES))
        hunks = [make_hunk(body=body, status=neorev.Status.FLAG, comment="ok")]
        output = neorev.format_output(hunks, [])
        match = re.search(r"```diff\n(.*?)```", output, re.DOTALL)
        self.assertIsNotNone(match)
        if match:
            self.assertNotIn("# ...", match.group(1))

    def test_body_one_over_max_lines_trimmed(self) -> None:
        """Verify a body with HUNK_BODY_MAX_LINES + 1 lines is trimmed."""
        body = "\n".join(f"+line {i}" for i in range(neorev.HUNK_BODY_MAX_LINES + 1))
        hunks = [make_hunk(body=body, status=neorev.Status.FLAG, comment="too long")]
        output = neorev.format_output(hunks, [])
        match = re.search(r"```diff\n(.*?)```", output, re.DOTALL)
        self.assertIsNotNone(match)
        if match:
            self.assertIn("# ...", match.group(1))

    def test_diff_source_in_preamble(self) -> None:
        """Verify the diff source appears in the header when provided."""
        hunks = [make_hunk(status=neorev.Status.FLAG, comment="fix")]
        output = neorev.format_output(hunks, [], diff_source="jj show abc")
        self.assertIn("- Reviewed diff: `jj show abc`\n", output)

    def test_diff_source_in_all_clear(self) -> None:
        """Verify the diff source appears in the all-clear output."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        output = neorev.format_output(hunks, [], diff_source="jj show xyz")
        self.assertIn("# Reviewed diff: `jj show xyz`\n", output)

    def test_no_diff_source_by_default(self) -> None:
        """Verify no diff source line when the parameter is empty."""
        hunks = [make_hunk(status=neorev.Status.FLAG, comment="fix")]
        output = neorev.format_output(hunks, [])
        self.assertNotIn("# Reviewed diff:", output)

    def test_no_diff_source_in_all_clear_by_default(self) -> None:
        """Verify no diff source line in all-clear output when not provided."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        output = neorev.format_output(hunks, [])
        self.assertNotIn("# Reviewed diff:", output)


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


class TestWriteReviewOutput(unittest.TestCase):
    """Tests for write_review_output."""

    def test_creates_parent_directory(self) -> None:
        """Verify write_review_output creates missing parent directories."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(neorev.sys, "stderr", new=io.StringIO()),
        ):
            output_path = str(Path(tmpdir) / "nested" / "dir" / "review.md")
            hunk = make_hunk(file_path="a.py")
            neorev.write_review_output(output_path, [hunk], [])
            self.assertTrue(Path(output_path).exists())


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
        """Verify the template contains the location and existing comment."""
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
        """Verify read_comment_file strips lines starting with #."""
        path = self.tmp_path()
        with open(path, "w") as f:
            f.write("# header\nactual comment\n# footer\n")

        comment = neorev.read_comment_file(path)
        self.assertEqual(comment, "actual comment")

    def test_write_comment_template_no_existing(self) -> None:
        """Verify a template with no existing comment has location and blank line."""
        path = self.tmp_path()
        with open(path, "w") as f:
            jump = neorev.write_comment_template(f, "foo.py:5", "")

        with open(path) as f:
            content = f.read()
        self.assertIn("foo.py:5", content)
        self.assertGreater(jump, 0)

    def test_read_comment_file_all_comments(self) -> None:
        """Verify a file with only # lines returns empty string."""
        path = self.tmp_path()
        with open(path, "w") as f:
            f.write("# line one\n# line two\n")

        comment = neorev.read_comment_file(path)
        self.assertEqual(comment, "")

    def test_read_comment_file_preserves_inner_hashes(self) -> None:
        """Verify lines not starting with # are preserved even if they contain #."""
        path = self.tmp_path()
        with open(path, "w") as f:
            f.write("# header\n  # indented hash\nplain\n")

        comment = neorev.read_comment_file(path)
        self.assertIn("# indented hash", comment)
        self.assertIn("plain", comment)


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


class TestLoadPreviousReview(unittest.TestCase):
    """Tests for load_previous_review and apply_previous_review."""

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
        """Verify loading a missing file returns empty results."""
        annotations, notes, hashes_encoded = neorev.load_previous_review(
            "/no/such/file",
        )
        self.assertEqual(annotations, {})
        self.assertEqual(notes, [])
        self.assertIsNone(hashes_encoded)

    def test_round_trip_through_file(self) -> None:
        """Verify format_output → load_previous_review recovers annotations."""
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

    def test_apply_previous_review(self) -> None:
        """Verify apply_previous_review sets notes on matching hunks."""
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
        """Verify unmatched annotations don't alter hunks."""
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
        """Verify global notes survive format_output → load_previous_review."""
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
        """Verify an existing but empty file returns empty results."""
        path = self.tmp_path()
        with open(path, "w") as f:
            f.write("   \n\n")

        annotations, notes, hashes_encoded = neorev.load_previous_review(path)
        self.assertEqual(annotations, {})
        self.assertEqual(notes, [])
        self.assertIsNone(hashes_encoded)

    def test_multiline_comment_round_trip(self) -> None:
        """Verify a multi-line comment survives format_output → load_previous_review."""
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
        """Verify multiple global notes of different kinds survive round-trip."""
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
        """Verify a section header with no ```diff block is skipped gracefully."""
        content = (
            "### [REVIEW CHANGE REQUESTED] `broken.py @ hunk`\n\n"
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
        """Verify multiple hunks matching annotations all get annotated."""
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


class TestSectionLabels(unittest.TestCase):
    """Tests for the REVIEW-prefixed section labels."""

    def test_hunk_note_label_is_prefixed(self) -> None:
        """Verify a hunk note section header carries the REVIEW prefix."""
        hunks = [make_hunk(status=neorev.Status.QUESTION, comment=FENCE_TEST_TEXT)]
        output = neorev.format_output(hunks, [])
        self.assertIn("### [REVIEW QUESTION] `test.py @ hunk`", output)

    def test_global_note_label_is_prefixed(self) -> None:
        """Verify a global note section header carries the REVIEW prefix."""
        hunks = [make_hunk(status=neorev.Status.APPROVED)]
        notes = [neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text=FENCE_TEST_TEXT)]
        output = neorev.format_output(hunks, notes)
        self.assertIn("### [REVIEW CHANGE REQUESTED] `global`", output)

    def test_preamble_documents_prefixed_labels(self) -> None:
        """Verify the instructions name the labels the sections actually use."""
        hunks = [make_hunk(status=neorev.Status.QUESTION, comment=FENCE_TEST_TEXT)]
        output = neorev.format_output(hunks, [])
        self.assertIn("`REVIEW CHANGE REQUESTED`", output)
        self.assertIn("`REVIEW QUESTION`", output)


class TestDiffFence(unittest.TestCase):
    """Tests for diff_fence: the fence must outrun every backtick run it wraps."""

    def test_fence_length_follows_longest_backtick_run(self) -> None:
        """Verify the fence is one backtick longer than the longest run it wraps."""
        for name, (line, expected) in BACKTICK_RUN_BODIES.items():
            with self.subTest(body=name):
                fence = neorev.diff_fence([FENCE_TEST_RANGE_LINE, line])
                self.assertEqual(fence, neorev.DIFF_FENCE_CHAR * expected)

    def test_fence_never_shorter_than_minimum(self) -> None:
        """Verify an empty block still gets the minimum fence length."""
        self.assertEqual(
            neorev.diff_fence([]),
            neorev.DIFF_FENCE_CHAR * neorev.DIFF_FENCE_MIN_LENGTH,
        )

    def test_longest_run_across_lines_wins(self) -> None:
        """Verify the longest run anywhere in the block sets the fence length."""
        fence = neorev.diff_fence(["+`", "+```", "+``"])
        self.assertEqual(fence, neorev.DIFF_FENCE_CHAR * 4)

    def test_no_block_line_can_close_the_fence(self) -> None:
        """Verify no wrapped line equals the fence, whatever its backtick runs."""
        lines = [FENCE_TEST_RANGE_LINE, "+```", "+````", " `````"]
        fence = neorev.diff_fence(lines)
        for line in lines:
            self.assertNotEqual(line.strip(), fence)


class TestDiffBlockPattern(unittest.TestCase):
    """Tests for DIFF_BLOCK_RE: which line is allowed to close a diff block."""

    def match_body(self, block: str) -> str | None:
        """Return the body DIFF_BLOCK_RE captures from *block*, or None."""
        match = neorev.DIFF_BLOCK_RE.match(block)
        return match.group("body") if match else None

    def test_plain_block(self) -> None:
        """Verify a fence-free block is captured up to its closing fence."""
        block = "```diff\n@@ -1,1 +1,2 @@\n+added\n```\n\ntext\n"
        self.assertEqual(self.match_body(block), "@@ -1,1 +1,2 @@\n+added\n")

    def test_prefixed_fence_does_not_close(self) -> None:
        """Verify a fence carrying a diff prefix does not close the block."""
        block = "```diff\n@@ -1,1 +1,2 @@\n+```\n-```\n ```\n```\n"
        self.assertEqual(self.match_body(block), "@@ -1,1 +1,2 @@\n+```\n-```\n ```\n")

    def test_indented_fence_does_not_close(self) -> None:
        """Verify an indented fence does not close the block."""
        block = "```diff\n@@ -1,1 +1,2 @@\n    ```\n```\n"
        self.assertEqual(self.match_body(block), "@@ -1,1 +1,2 @@\n    ```\n")

    def test_shorter_fence_does_not_close_wide_block(self) -> None:
        """Verify a three-backtick line does not close a four-backtick block."""
        block = "````diff\n@@ -1,1 +1,2 @@\n```\n````\n"
        self.assertEqual(self.match_body(block), "@@ -1,1 +1,2 @@\n```\n")

    def test_longer_fence_does_not_close_narrow_block(self) -> None:
        """Verify a four-backtick line does not close a three-backtick block."""
        block = "```diff\n@@ -1,1 +1,2 @@\n````\n```\n"
        self.assertEqual(self.match_body(block), "@@ -1,1 +1,2 @@\n````\n")

    def test_unterminated_block_does_not_match(self) -> None:
        """Verify a block with no closing fence is not matched at all."""
        self.assertIsNone(self.match_body("```diff\n@@ -1,1 +1,2 @@\n+added\n"))

    def test_other_info_string_does_not_match(self) -> None:
        """Verify a fenced block that is not a diff block is not matched."""
        self.assertIsNone(self.match_body("```python\nx = 1\n```\n"))

    def test_written_block_is_matched(self) -> None:
        """Verify every block format_diff_block writes is matched by the pattern."""
        for name, (line, _) in BACKTICK_RUN_BODIES.items():
            with self.subTest(body=name):
                hunk = make_hunk(body=line, range_line=FENCE_TEST_RANGE_LINE)
                note = neorev.HunkNote(
                    kind=neorev.NoteKind.QUESTION,
                    target=neorev.HunkTarget(),
                    text=FENCE_TEST_TEXT,
                )
                block = neorev.format_diff_block(hunk, note)
                self.assertEqual(
                    self.match_body(block),
                    f"{FENCE_TEST_RANGE_LINE}\n{line}\n",
                )


class TestSectionComment(unittest.TestCase):
    """Tests for parse_review_section: which part of a section is note text."""

    def hunk_annotation(
        self, prologue: str, text: str
    ) -> neorev.SavedAnnotation | None:
        """Return the annotation parsed from a hunk section, or None if dropped."""
        section = f"[REVIEW QUESTION] `foo.py @ hunk`\n\n{prologue}{text}\n"
        annotations: neorev.SavedAnnotationMap = {}
        neorev.parse_review_section(section, annotations, [])
        return next(iter(annotations.values()), None)

    def hunk_comment(self, prologue: str, text: str) -> str | None:
        """Return the note text parsed from a hunk section, or None if dropped."""
        annotation = self.hunk_annotation(prologue, text)
        return annotation.text if annotation is not None else None

    def hunk_anchor(self, prologue: str, text: str) -> str | None:
        """Return the anchor parsed from a hunk section, or None if there is none."""
        annotation = self.hunk_annotation(prologue, text)
        return annotation.anchor if annotation is not None else None

    def global_comment(self, text: str) -> str | None:
        """Return the note text parsed from a global section, or None if dropped."""
        section = f"[REVIEW QUESTION] `global`\n\n{text}\n"
        notes: list[neorev.GlobalNote] = []
        neorev.parse_review_section(section, {}, notes)
        return notes[0].text if notes else None

    def test_prologue_stripped(self) -> None:
        """Verify the anchor and the diff block are not part of the note text."""
        self.assertEqual(self.hunk_comment(SECTION_PROLOGUE, "fix this"), "fix this")

    def test_block_holding_a_fence_stripped_whole(self) -> None:
        """Verify a diff block whose content holds a fence is stripped in full."""
        prologue = (
            "<!-- neorev: note-anchor=abc -->\n\n"
            "```diff\n@@ -1,4 +1,4 @@\n ```bash\n-old\n+new\n ```\n```\n\n"
        )
        self.assertEqual(self.hunk_comment(prologue, "fix this"), "fix this")

    def test_section_without_a_diff_block_is_dropped(self) -> None:
        """Verify a hunk section neorev wrote no diff block for yields no annotation."""
        self.assertIsNone(self.hunk_comment("", "```diff\n-a\n+b\n```"))

    def test_second_diff_block_is_note_text(self) -> None:
        """Verify only the block belonging to the prologue is stripped."""
        self.assertEqual(
            self.hunk_comment(SECTION_PROLOGUE, "Try:\n\n```diff\n-a\n+b\n```"),
            "Try:\n\n```diff\n-a\n+b\n```",
        )

    def test_anchor_comment_in_note_text_kept(self) -> None:
        """Verify an anchor comment inside the note text is not stripped."""
        self.assertEqual(
            self.hunk_comment(SECTION_PROLOGUE, "see <!-- neorev: note-anchor=def -->"),
            "see <!-- neorev: note-anchor=def -->",
        )

    def test_anchor_read_from_the_prologue(self) -> None:
        """Verify the anchor is the prologue's, not one quoted in the note text."""
        self.assertEqual(
            self.hunk_anchor(SECTION_PROLOGUE, "see <!-- neorev: note-anchor=def -->"),
            SECTION_PROLOGUE_ANCHOR,
        )

    def test_section_header_in_note_text_kept(self) -> None:
        """Verify a section header line inside the note text is not stripped."""
        self.assertEqual(
            self.hunk_comment(SECTION_PROLOGUE, "[QUESTION] `bar.py @ hunk`"),
            "[QUESTION] `bar.py @ hunk`",
        )

    def test_markdown_heading_in_note_text_kept(self) -> None:
        """Verify a markdown heading inside the note text is not stripped."""
        text = "### Why this?\n\nBecause reasons."
        self.assertEqual(self.hunk_comment(SECTION_PROLOGUE, text), text)

    def test_special_characters_kept(self) -> None:
        """Verify backticks, brackets and non-ASCII characters survive extraction."""
        text = "Use `foo()` — see [docs](url), émojis 🎉 and <angle>."
        self.assertEqual(self.hunk_comment(SECTION_PROLOGUE, text), text)

    def test_footer_line_in_note_text_kept(self) -> None:
        """Verify a footer-shaped line the note does not end with is note text."""
        text = "<!-- neorev: approved-hashes=AQ== -->\nis the footer"
        self.assertEqual(self.hunk_comment(SECTION_PROLOGUE, text), text)

    def test_global_note_keeps_a_leading_anchor_comment(self) -> None:
        """Verify a global note has no prologue, so an anchor comment is its text."""
        text = "<!-- neorev: note-anchor=abc -->\n\nreal text"
        self.assertEqual(self.global_comment(text), text)

    def test_global_note_may_be_only_an_anchor_comment(self) -> None:
        """Verify a global note made of nothing but an anchor comment is kept."""
        text = "<!-- neorev: note-anchor=abc -->"
        self.assertEqual(self.global_comment(text), text)


class TestNoteTextRoundTrip(unittest.TestCase):
    """Round-trip tests for reviewer-typed note text."""

    def setUp(self) -> None:
        """Create a temporary directory for review files."""
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        """Remove the temporary directory."""
        self.tmpdir.cleanup()

    def tmp_path(self) -> str:
        """Return the review file path inside the temporary directory."""
        return str(Path(self.tmpdir.name) / "review.md")

    def added_line_target(self, hunk: neorev.Hunk) -> neorev.LineTarget:
        """Return the target of the first added line of *hunk*."""
        for display_line in hunk.display_lines:
            if display_line.target is not None:
                return display_line.target
        raise AssertionError(hunk.range_line)

    def test_hunk_note_text(self) -> None:
        """Verify a hunk-scoped note keeps its text through a reopen."""
        for diff_name, diff_text in ROUND_TRIP_DIFFS.items():
            for text_name, text in ROUND_TRIP_NOTE_TEXTS.items():
                with self.subTest(diff=diff_name, text=text_name):
                    hunks = neorev.parse_diff(diff_text)
                    hunks[0].upsert_note(
                        neorev.NoteKind.QUESTION, neorev.HunkTarget(), text
                    )
                    reloaded, _, _ = reopen_review(
                        diff_text, hunks, [], self.tmp_path()
                    )
                    self.assertEqual([n.text for n in reloaded[0].notes], [text])

    def test_line_note_text(self) -> None:
        """Verify a line-scoped note keeps its text through a reopen."""
        for diff_name, diff_text in ROUND_TRIP_DIFFS.items():
            for text_name, text in ROUND_TRIP_NOTE_TEXTS.items():
                with self.subTest(diff=diff_name, text=text_name):
                    hunks = neorev.parse_diff(diff_text)
                    target = self.added_line_target(hunks[0])
                    hunks[0].upsert_note(neorev.NoteKind.FLAG, target, text)
                    reloaded, _, _ = reopen_review(
                        diff_text, hunks, [], self.tmp_path()
                    )
                    self.assertEqual([n.text for n in reloaded[0].notes], [text])

    def test_global_note_text(self) -> None:
        """Verify a global note keeps its text through a reopen."""
        for text_name, text in ROUND_TRIP_NOTE_TEXTS.items():
            with self.subTest(text=text_name):
                hunks = neorev.parse_diff(FENCED_BODY_DIFF)
                notes = [neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text=text)]
                _, reloaded_notes, _ = reopen_review(
                    FENCED_BODY_DIFF, hunks, notes, self.tmp_path()
                )
                self.assertEqual([n.text for n in reloaded_notes], [text])

    def test_note_kind_and_target_preserved(self) -> None:
        """Verify kind and target survive alongside the text for a fenced hunk body."""
        hunks = neorev.parse_diff(FENCED_BODY_DIFF)
        target = self.added_line_target(hunks[0])
        hunks[0].upsert_note(neorev.NoteKind.QUESTION, target, FENCE_TEST_TEXT)
        reloaded, _, _ = reopen_review(FENCED_BODY_DIFF, hunks, [], self.tmp_path())
        note = reloaded[0].notes[0]
        self.assertEqual(note.kind, neorev.NoteKind.QUESTION)
        self.assertEqual(note.target, target)
        self.assertEqual(note.text, FENCE_TEST_TEXT)

    def test_approved_hashes_readable_beside_fenced_note(self) -> None:
        """Verify the approved-hashes footer stays readable next to a fenced note."""
        hunks = neorev.parse_diff(FENCED_BODY_DIFF)
        hunks.extend(neorev.parse_diff(SIMPLE_DIFF))
        hunks[0].upsert_note(
            neorev.NoteKind.QUESTION,
            neorev.HunkTarget(),
            ROUND_TRIP_NOTE_TEXTS["only a diff block"],
        )
        hunks[1].approved = True
        path = self.tmp_path()
        Path(path).write_text(neorev.format_output(hunks, []))
        _, _, hashes_encoded = neorev.load_previous_review(path)
        self.assertEqual(
            neorev.decode_approved_hashes(hashes_encoded or ""),
            {neorev.hunk_identity_hash(hunks[1])},
        )

    def test_footer_in_note_text_does_not_hijack_approvals(self) -> None:
        """Verify a footer-shaped note line leaves the real footer authoritative."""
        hunks = neorev.parse_diff(SIMPLE_DIFF)
        hunks.extend(neorev.parse_diff(FENCED_BODY_DIFF))
        hunks[0].upsert_note(
            neorev.NoteKind.QUESTION,
            neorev.HunkTarget(),
            ROUND_TRIP_NOTE_TEXTS["approved-hashes footer line"],
        )
        hunks[1].approved = True
        path = self.tmp_path()
        Path(path).write_text(neorev.format_output(hunks, []))
        _, _, hashes_encoded = neorev.load_previous_review(path)
        self.assertEqual(
            neorev.decode_approved_hashes(hashes_encoded or ""),
            {neorev.hunk_identity_hash(hunks[1])},
        )

    def test_closing_footer_is_not_note_text(self) -> None:
        """Verify the footer closing the file does not become the last note's text."""
        path = self.tmp_path()
        Path(path).write_text(
            f"### [REVIEW QUESTION] `foo.py @ hunk`\n\n{SECTION_PROLOGUE}"
            f"{FOOTER_TEST_NOTE_TEXT}\n\n"
            f"<!-- neorev: approved-hashes={FOOTER_TEST_ENCODING} -->\n"
        )
        annotations, _, approved_encoded = neorev.load_previous_review(path)
        self.assertEqual(
            [annotation.text for annotation in annotations.values()],
            [FOOTER_TEST_NOTE_TEXT],
        )
        self.assertEqual(approved_encoded, FOOTER_TEST_ENCODING)

    def test_footer_ending_a_nonfinal_note_is_kept(self) -> None:
        """Verify a note ending on a footer-shaped line keeps it when one follows."""
        hunks = neorev.parse_diff(TWO_FILE_DIFF)
        hunks[0].upsert_note(
            neorev.NoteKind.QUESTION, neorev.HunkTarget(), FOOTER_ENDED_NOTE_TEXT
        )
        hunks[1].upsert_note(
            neorev.NoteKind.FLAG, neorev.HunkTarget(), FOLLOWING_NOTE_TEXT
        )
        reloaded, _, _ = reopen_review(TWO_FILE_DIFF, hunks, [], self.tmp_path())
        self.assertEqual(
            [[note.text for note in hunk.notes] for hunk in reloaded],
            [[FOOTER_ENDED_NOTE_TEXT], [FOLLOWING_NOTE_TEXT]],
        )

    def test_footer_ending_a_nonfinal_global_note_is_kept(self) -> None:
        """Verify a global note keeps a footer-shaped last line when one follows."""
        hunks = neorev.parse_diff(SIMPLE_DIFF)
        notes = [
            neorev.GlobalNote(
                kind=neorev.NoteKind.QUESTION, text=FOOTER_ENDED_NOTE_TEXT
            ),
            neorev.GlobalNote(kind=neorev.NoteKind.FLAG, text=FOLLOWING_NOTE_TEXT),
        ]
        _, reloaded_notes, _ = reopen_review(SIMPLE_DIFF, hunks, notes, self.tmp_path())
        self.assertEqual(
            [note.text for note in reloaded_notes],
            [FOOTER_ENDED_NOTE_TEXT, FOLLOWING_NOTE_TEXT],
        )


class TestHunkIdentityRoundTrip(unittest.TestCase):
    """Round-trip tests for the hunk identity a note is keyed by."""

    def setUp(self) -> None:
        """Create a temporary directory for review files."""
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        """Remove the temporary directory."""
        self.tmpdir.cleanup()

    def tmp_path(self) -> str:
        """Return the review file path inside the temporary directory."""
        return str(Path(self.tmpdir.name) / "review.md")

    def test_note_reattaches_for_every_identity(self) -> None:
        """Verify a hunk note survives a reopen whatever shape the identity takes."""
        for diff_name, diff_text in HUNK_IDENTITY_DIFFS.items():
            with self.subTest(diff=diff_name):
                hunks = neorev.parse_diff(diff_text)
                hunks[0].upsert_note(
                    neorev.NoteKind.QUESTION,
                    neorev.HunkTarget(),
                    ROUND_TRIP_COMMENT_TEXT,
                )
                reloaded, _, _ = reopen_review(diff_text, hunks, [], self.tmp_path())
                self.assertEqual(
                    [n.text for n in reloaded[0].notes], [ROUND_TRIP_COMMENT_TEXT]
                )

    def test_file_named_global_stays_hunk_scoped(self) -> None:
        """Verify a note on a file named `global` does not reload as a global note."""
        hunks = neorev.parse_diff(GLOBAL_PATH_DIFF)
        hunks[0].upsert_note(
            neorev.NoteKind.QUESTION, neorev.HunkTarget(), ROUND_TRIP_COMMENT_TEXT
        )
        _, reloaded_notes, _ = reopen_review(
            GLOBAL_PATH_DIFF, hunks, [], self.tmp_path()
        )
        self.assertEqual(reloaded_notes, [])


class TestReopenCycleStability(unittest.TestCase):
    """Stability tests for repeated reopening of an unchanged review."""

    def setUp(self) -> None:
        """Create a temporary directory for review files."""
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        """Remove the temporary directory."""
        self.tmpdir.cleanup()

    def tmp_path(self) -> str:
        """Return the review file path inside the temporary directory."""
        return str(Path(self.tmpdir.name) / "review.md")

    def cycle(self, diff_text: str, text: str) -> list[str]:
        """Run REOPEN_CYCLE_COUNT reopen cycles, returning the file after each."""
        hunks = neorev.parse_diff(diff_text)
        hunks[0].upsert_note(neorev.NoteKind.QUESTION, neorev.HunkTarget(), text)
        outputs: list[str] = []
        for _ in range(REOPEN_CYCLE_COUNT):
            hunks, _, output = reopen_review(diff_text, hunks, [], self.tmp_path())
            outputs.append(output)
            self.assertEqual([n.text for n in hunks[0].notes], [text])
        return outputs

    def test_file_is_byte_stable(self) -> None:
        """Verify repeated reopens of an unchanged diff rewrite the same file."""
        for diff_name, diff_text in ROUND_TRIP_DIFFS.items():
            with self.subTest(diff=diff_name):
                outputs = self.cycle(diff_text, FENCE_TEST_TEXT)
                self.assertEqual(outputs[1:], outputs[:-1])

    def test_fenced_body_note_does_not_absorb_the_diff(self) -> None:
        """Verify a note on a hunk holding a fence never absorbs the diff block."""
        outputs = self.cycle(FENCED_BODY_DIFF, FENCE_TEST_TEXT)
        for output in outputs:
            self.assertEqual(output.count("old command"), 1)
            self.assertEqual(output.count("new command"), 1)


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
