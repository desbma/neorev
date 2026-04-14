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
    GLOBAL_PARSE_NOTE_TEXT,
    LINE_TARGET_APPLY_TEXT,
    LINE_TARGET_NOTE_LINE,
    LINE_TARGET_NOTE_TEXT,
    LONG_BODY_LINE_COUNT,
    REMOVED_LINE_NUMBER,
    ROUND_TRIP_COMMENT_TEXT,
    make_hunk,
    neorev,
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
        """write_review_output creates missing parent directories before writing."""
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
