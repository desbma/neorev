"""End-to-end tests for neorev."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import (
    FENCED_BODY_DIFF,
    REOPEN_CYCLE_COUNT,
    TWO_HUNK_DIFF,
    WORKFLOW_ALL_CLEAR_SUMMARY,
    WORKFLOW_FENCED_QUESTION,
    WORKFLOW_FLAG_COMMENT,
    WORKFLOW_GLOBAL_NOTE,
    WORKFLOW_PRECEDENCE_QUESTION,
    WORKFLOW_RESUME_FLAG,
    WORKFLOW_RESUME_GLOBAL,
    WORKFLOW_STALE_MESSAGE,
    neorev,
    run_main_with_scripted_terminal,
)


class TestMainWorkflow(unittest.TestCase):
    """High-level tests for new review and resume workflows through main()."""

    def test_new_review_flow_writes_expected_output(self) -> None:
        """Verify a scripted review writes annotations and approval hashes."""

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
            self.assertIn("[REVIEW CHANGE REQUESTED] `hello.py", output)
            self.assertIn(WORKFLOW_FLAG_COMMENT, output)
            self.assertIn("[REVIEW QUESTION] `global`", output)
            self.assertIn(WORKFLOW_GLOBAL_NOTE, output)
            self.assertIn("<!-- neorev: approved-hashes=", output)
        finally:
            os.unlink(output_path)

    def test_resume_workflow_applies_annotations_and_global_notes(self) -> None:
        """Verify resuming an existing output restores notes and hash approvals."""
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

    def test_repeated_resume_over_fenced_diff_keeps_note_intact(self) -> None:
        """Verify reopening a review of a fenced diff rewrites the same file."""

        def script(state: neorev.ReviewState) -> None:
            """Ask one question about the whole hunk."""
            state.hunks[0].upsert_note(
                neorev.NoteKind.QUESTION,
                neorev.HunkTarget(),
                WORKFLOW_FENCED_QUESTION,
            )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        try:
            run_main_with_scripted_terminal(FENCED_BODY_DIFF, output_path, script)
            first = Path(output_path).read_text()
            for _ in range(REOPEN_CYCLE_COUNT):
                stderr = run_main_with_scripted_terminal(
                    FENCED_BODY_DIFF,
                    output_path,
                    lambda _state: None,
                )
                self.assertIn("Loaded 1 hunk annotation(s)", stderr)
                self.assertEqual(Path(output_path).read_text(), first)
            self.assertEqual(first.count(WORKFLOW_FENCED_QUESTION), 1)
            self.assertEqual(first.count("old command"), 1)
        finally:
            os.unlink(output_path)

    def test_resume_annotation_precedence_over_hashes(self) -> None:
        """Verify annotation status wins when hashes mark the same hunk approved."""
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
            self.assertIn("[REVIEW QUESTION] `hello.py", output)
            self.assertIn(WORKFLOW_PRECEDENCE_QUESTION, output)
        finally:
            os.unlink(output_path)

    def test_resume_with_stale_annotation_reports_and_keeps_hashes(self) -> None:
        """Verify stale annotations are reported while hash approvals still resume."""
        hash_hunks = neorev.parse_diff(TWO_HUNK_DIFF)
        hash_hunks[1].approved = True
        hashes = neorev.encode_approved_hashes(hash_hunks)
        stale_output = (
            "### [REVIEW CHANGE REQUESTED] `stale.py @ hunk`\n\n"
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

    def test_auto_detect_jj_uses_jj_show(self) -> None:
        """Verify a detected jj repo with no piped diff defaults to 'jj show'."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        try:
            with (
                patch.object(
                    neorev,
                    "read_diff_from_stdin",
                    side_effect=neorev.NoDiffOnStdinError,
                ),
                patch.object(neorev, "jj_root", return_value="/repo"),
                patch.object(
                    neorev,
                    "fetch_diff_from_jj",
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

    def test_jj_with_revision_uses_jj_show_rev(self) -> None:
        """Verify using -j REV runs 'jj show REV' and records it as the source."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        try:
            with patch.object(
                neorev,
                "fetch_diff_from_jj",
                return_value=TWO_HUNK_DIFF,
            ) as fetch_mock:
                run_main_with_scripted_terminal(
                    TWO_HUNK_DIFF,
                    output_path,
                    lambda state: state.hunks[0].__setattr__("approved", True),
                    extra_args=["-j", "abc123"],
                )
            fetch_mock.assert_called_once_with(["jj", "show", "abc123"])
            output = Path(output_path).read_text()
            self.assertIn("`jj show abc123`", output)
        finally:
            os.unlink(output_path)

    def test_jj_without_revision_includes_diff_source(self) -> None:
        """Verify using -j without a revision includes 'jj show' as diff source."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        try:
            with patch.object(
                neorev,
                "fetch_diff_from_jj",
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
        """Verify the --clear flag discards a previous review and starts fresh."""
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


class TestAllClearSkipsFile(unittest.TestCase):
    """Workflow tests: all-clear review skips output file creation."""

    def test_all_approved_skips_file_and_prints_message(self) -> None:
        """Verify approving all hunks with no global notes writes no file."""

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
        """Verify approving all hunks with a global note writes a file."""

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
        """Verify a file is written when not all hunks are approved."""

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
        """Verify exiting without approving or annotating anything writes no output."""

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
