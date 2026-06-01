"""CLI-related tests for neorev."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import (
    MOCK_OUTPUT_PATH,
    neorev,
)


class TestArgParser(unittest.TestCase):
    """Tests for build_arg_parser and resolve_args."""

    def test_output_flag(self) -> None:
        """The -o/--output flag sets the output path."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-o", "out.md"])
        self.assertEqual(args.output, "out.md")
        self.assertFalse(args.clip)
        self.assertFalse(args.clear)

    def test_output_long_form(self) -> None:
        """The --output long form sets the output path."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--output", "out.md"])
        self.assertEqual(args.output, "out.md")

    def test_clip_flag(self) -> None:
        """The -x/--clip flag is recognised."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--clip", "-o", "out.md"])
        self.assertTrue(args.clip)
        args_short = parser.parse_args(["-x", "-o", "out.md"])
        self.assertTrue(args_short.clip)

    def test_clear_flag(self) -> None:
        """The -c/--clear flag is recognised."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--clear", "-o", "out.md"])
        self.assertTrue(args.clear)
        args_short = parser.parse_args(["-c", "-o", "out.md"])
        self.assertTrue(args_short.clear)

    def test_output_defaults_to_none(self) -> None:
        """Omitting -o leaves output as None for resolve_args to fill."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.output)

    def test_resolve_args_fills_default_output(self) -> None:
        """resolve_args fills in a default output path when not provided."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args([])
        with (
            patch.object(neorev, "jj_root", return_value="/repo"),
            patch.object(neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH),
        ):
            neorev.resolve_args(args)
        self.assertEqual(args.output, MOCK_OUTPUT_PATH)

    def test_resolve_args_keeps_explicit_output(self) -> None:
        """resolve_args preserves an explicitly provided -o value."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-o", "mine.md"])
        neorev.resolve_args(args)
        self.assertEqual(args.output, "mine.md")

    def test_jj_with_revision(self) -> None:
        """The -j flag records a JjSource with the given rev."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-j", "abc123", "-o", "out.md"])
        self.assertEqual(args.source, neorev.JjSource("abc123"))
        self.assertEqual(args.output, "out.md")

    def test_jj_without_revision(self) -> None:
        """The -j flag without a revision records JjSource(None)."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-j"])
        self.assertEqual(args.source, neorev.JjSource(None))

    def test_source_none_when_no_flag_passed(self) -> None:
        """Without -j, source defaults to None."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.source)

    def test_long_form_jj_with_revision(self) -> None:
        """The --jj long form works with a revision."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--jj", "v1.0"])
        self.assertEqual(args.source, neorev.JjSource("v1.0"))

    def test_long_form_jj_without_revision(self) -> None:
        """The --jj long form works without a revision."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--jj"])
        self.assertEqual(args.source, neorev.JjSource(None))


class TestDefaultOutputPath(unittest.TestCase):
    """Tests for default_output_path and jj_metadata."""

    def make_meta(
        self,
        dirname: str = "proj",
        workspace: str | None = None,
        rev: str | None = None,
    ) -> neorev.JjMetadata:
        """Build a JjMetadata with test defaults."""
        return neorev.JjMetadata(dirname=dirname, workspace=workspace, rev=rev)

    def test_uses_xdg_state_home_env(self) -> None:
        """Respect $XDG_STATE_HOME when set."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"XDG_STATE_HOME": tmpdir}),
            patch.object(neorev, "jj_metadata", return_value=self.make_meta()),
        ):
            result = neorev.default_output_path()
        self.assertTrue(result.startswith(tmpdir))
        self.assertTrue(result.endswith(".md"))

    def test_falls_back_to_xdg_default(self) -> None:
        """Use ~/.local/state when $XDG_STATE_HOME is unset."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(neorev, "jj_metadata", return_value=self.make_meta()),
            patch.object(Path, "mkdir"),
        ):
            result = neorev.default_output_path()
        self.assertIn(".local/state/neorev/proj", result)

    def test_filename_parts_basic(self) -> None:
        """Filename is just review.md; dirname becomes a subdirectory."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"XDG_STATE_HOME": tmpdir}),
            patch.object(
                neorev,
                "jj_metadata",
                return_value=self.make_meta(dirname="myproj"),
            ),
        ):
            result = neorev.default_output_path()
        self.assertEqual(Path(result).name, "review.md")
        self.assertEqual(Path(result).parent.name, "myproj")

    def test_filename_parts_with_workspace_and_rev(self) -> None:
        """Filename includes workspace and rev when available."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"XDG_STATE_HOME": tmpdir}),
            patch.object(
                neorev,
                "jj_metadata",
                return_value=self.make_meta(workspace="feat", rev="abc1234"),
            ),
        ):
            result = neorev.default_output_path()
        self.assertEqual(Path(result).name, "review-feat-abc1234.md")
        self.assertEqual(Path(result).parent.name, "proj")

    def test_filename_parts_with_rev_only(self) -> None:
        """Filename includes rev but skips empty workspace."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"XDG_STATE_HOME": tmpdir}),
            patch.object(
                neorev,
                "jj_metadata",
                return_value=self.make_meta(rev="def456"),
            ),
        ):
            result = neorev.default_output_path()
        self.assertEqual(Path(result).name, "review-def456.md")
        self.assertEqual(Path(result).parent.name, "proj")

    def test_does_not_create_state_directory(self) -> None:
        """default_output_path must not create the directory itself."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "sub" / "neorev" / "proj"
            with (
                patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(tmpdir) / "sub")}),
                patch.object(neorev, "jj_metadata", return_value=self.make_meta()),
            ):
                neorev.default_output_path()
            self.assertFalse(state_dir.is_dir())

    def test_resolve_args_detects_jj_from_flag(self) -> None:
        """resolve_args passes the working-copy rev (None) to default_output_path."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-j"])
        with patch.object(
            neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH
        ) as mock:
            neorev.resolve_args(args)
        mock.assert_called_once_with(None)

    def test_resolve_args_passes_jj_rev(self) -> None:
        """resolve_args forwards the jj revision to default_output_path."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-j", "abc123"])
        with patch.object(
            neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH
        ) as mock:
            neorev.resolve_args(args)
        mock.assert_called_once_with("abc123")

    def test_resolve_args_detects_jj_from_directory(self) -> None:
        """resolve_args detects jj when no flag is given."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args([])
        with (
            patch.object(neorev, "jj_root", return_value="/repo"),
            patch.object(
                neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH
            ) as mock,
        ):
            neorev.resolve_args(args)
        mock.assert_called_once_with(None)

    def test_resolve_args_skips_output_outside_jj(self) -> None:
        """resolve_args leaves output unset when not in a jj repo and no flag given."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args([])
        with patch.object(neorev, "jj_root", return_value=None):
            neorev.resolve_args(args)
        self.assertIsNone(args.output)

    def test_jj_uses_shortest_unambiguous_rev(self) -> None:
        """jj_metadata uses shortest() without a fixed length."""
        calls: list[list[str]] = []

        def fake_run_jj(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            calls.append(cmd)
            if "log" in cmd:
                return "l"
            return "default: /some/path"

        with patch.object(neorev, "run_jj", side_effect=fake_run_jj):
            meta = neorev.jj_metadata()
        self.assertEqual(meta.rev, "l")
        log_cmd = calls[0]
        template_arg = log_cmd[-1]
        self.assertIn("shortest()", template_arg)
        self.assertNotIn("shortest(8)", template_arg)

    def test_jj_passes_custom_rev_to_log(self) -> None:
        """jj_metadata passes a custom rev to jj log -r."""
        calls: list[list[str]] = []

        def fake_run_jj(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            calls.append(cmd)
            if "log" in cmd:
                return "x"
            return "default: /some/path"

        with patch.object(neorev, "run_jj", side_effect=fake_run_jj):
            meta = neorev.jj_metadata(rev="myrev")
        self.assertEqual(meta.rev, "x")
        log_cmd = calls[0]
        r_idx = log_cmd.index("-r")
        self.assertEqual(log_cmd[r_idx + 1], "myrev")

    def test_jj_picks_workspace_matching_cwd(self) -> None:
        """jj_metadata selects the workspace whose path matches cwd."""

        def fake_run_jj(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            if "log" in cmd:
                return "abc"
            return "default: /home/user/proj\nfeature: /home/user/proj-feature"

        with (
            patch.object(neorev, "run_jj", side_effect=fake_run_jj),
            patch.object(Path, "cwd", return_value=Path("/home/user/proj-feature")),
        ):
            meta = neorev.jj_metadata()
        self.assertEqual(meta.workspace, "feature")

    def test_jj_metadata_falls_back_on_failure(self) -> None:
        """jj_metadata returns a name-only fallback when jj is unavailable."""
        with (
            patch.object(neorev, "run_jj", side_effect=FileNotFoundError),
            patch.object(neorev, "project_name", return_value="proj"),
        ):
            meta = neorev.jj_metadata()
        self.assertEqual(meta, neorev.JjMetadata(dirname="proj"))


class TestProjectName(unittest.TestCase):
    """Tests for project_name."""

    def test_uses_last_two_components_lowercased(self) -> None:
        """Return the last 2 path components of cwd, lowercased, joined with '-'."""
        cwd = Path("/home/user/Projets/NeoRev")
        with patch.object(Path, "cwd", return_value=cwd):
            result = neorev.project_name()
        self.assertEqual(result, "projets-neorev")

    def test_single_component_path(self) -> None:
        """Return just the single component lowercased when path has depth 1."""
        with patch.object(Path, "cwd", return_value=Path("/root")):
            result = neorev.project_name()
        self.assertEqual(result, "root")


class TestJjRoot(unittest.TestCase):
    """Tests for jj_root."""

    def test_returns_none_outside_jj_repo(self) -> None:
        """Return None when the jj root probe fails."""
        with patch.object(
            neorev, "run_jj", side_effect=subprocess.CalledProcessError(1, ["jj"])
        ):
            self.assertIsNone(neorev.jj_root())

    def test_returns_root_in_jj_repo(self) -> None:
        """Return the repository root reported by jj."""
        with patch.object(neorev, "run_jj", return_value="/home/user/proj") as mock:
            result = neorev.jj_root()
        self.assertEqual(result, "/home/user/proj")
        self.assertEqual(mock.call_args[0][0], ["jj", "root", "--ignore-working-copy"])

    def test_returns_none_when_jj_missing(self) -> None:
        """Return None when the jj binary is not installed."""
        with patch.object(neorev, "run_jj", side_effect=FileNotFoundError):
            self.assertIsNone(neorev.jj_root())
