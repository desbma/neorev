"""Note-related tests for neorev."""

import base64
import tempfile
import unittest
from pathlib import Path

from tests.helpers import (
    ADDED_LINE_NUMBER,
    ANCHOR_BODY_CHANGED,
    ANCHOR_BODY_ORIGINAL,
    ANCHOR_LEGACY_COMMENT,
    ANCHOR_NOTE_TEXT,
    ANCHOR_RANGE_LINE,
    LINE_TARGET_NOTE_LINE,
    MANY_APPROVED_COUNT,
    REMOVED_LINE_NUMBER,
    UPSERT_NOTE_TEXT,
    UPSERT_NOTE_UPDATED_TEXT,
    make_hunk,
    neorev,
)


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

    def test_approve_resets_scroll_offset(self) -> None:
        """Approving a hunk and advancing resets the scroll position."""
        self.state.scroll_offset = 42
        self.state.approve()
        self.assertEqual(self.state.current_index, 1)
        self.assertEqual(self.state.scroll_offset, 0)

    def test_approve_without_advance_keeps_scroll_offset(self) -> None:
        """Un-approving (toggle off) does not move, so scroll is preserved."""
        self.hunks[0].approved = True
        self.state.scroll_offset = 17
        self.state.approve()
        self.assertFalse(self.hunks[0].approved)
        self.assertEqual(self.state.current_index, 0)
        self.assertEqual(self.state.scroll_offset, 17)

    def test_approve_file(self) -> None:
        """Approve-file approves all hunks with the same file_path."""
        for h in self.hunks:
            h.file_path = "same.py"
        self.state.approve_file()
        for h in self.hunks:
            self.assertTrue(h.approved)

    def test_approve_file_resets_scroll_offset(self) -> None:
        """Approve-file advances to the next unhandled hunk and resets scroll."""
        self.hunks[0].file_path = "a.py"
        self.hunks[1].file_path = "a.py"
        self.hunks[2].file_path = "b.py"
        self.state.scroll_offset = 25
        self.state.approve_file()
        self.assertEqual(self.state.current_index, 2)
        self.assertEqual(self.state.scroll_offset, 0)

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
