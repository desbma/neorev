"""CLI-related tests for neorev."""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
            patch.object(neorev, "detect_vcs", return_value=neorev.Vcs.GIT),
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

    def test_git_with_revision(self) -> None:
        """The -g flag records (Vcs.GIT, rev) in vcs_rev."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-g", "HEAD~1", "-o", "out.md"])
        self.assertEqual(args.vcs_rev, (neorev.Vcs.GIT, "HEAD~1"))
        self.assertEqual(args.output, "out.md")

    def test_jj_with_revision(self) -> None:
        """The -j flag records (Vcs.JUJUTSU, rev) in vcs_rev."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-j", "abc123", "-o", "out.md"])
        self.assertEqual(args.vcs_rev, (neorev.Vcs.JUJUTSU, "abc123"))
        self.assertEqual(args.output, "out.md")

    def test_git_without_revision(self) -> None:
        """The -g flag without a revision records (Vcs.GIT, None)."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-g"])
        self.assertEqual(args.vcs_rev, (neorev.Vcs.GIT, None))

    def test_jj_without_revision(self) -> None:
        """The -j flag without a revision records (Vcs.JUJUTSU, None)."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-j"])
        self.assertEqual(args.vcs_rev, (neorev.Vcs.JUJUTSU, None))

    def test_vcs_rev_none_when_no_flag_passed(self) -> None:
        """Without -g/-j, vcs_rev defaults to None."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.vcs_rev)

    def test_git_and_jj_mutually_exclusive(self) -> None:
        """Using both -g and -j is rejected."""
        parser = neorev.build_arg_parser()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["-g", "HEAD", "-j", "abc"])

    def test_long_form_git_with_revision(self) -> None:
        """The --git long form works with a revision."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--git", "v1.0"])
        self.assertEqual(args.vcs_rev, (neorev.Vcs.GIT, "v1.0"))

    def test_long_form_jj_without_revision(self) -> None:
        """The --jj long form works without a revision."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--jj"])
        self.assertEqual(args.vcs_rev, (neorev.Vcs.JUJUTSU, None))


class TestDefaultOutputPath(unittest.TestCase):
    """Tests for default_output_path and query_vcs_metadata."""

    def make_meta(
        self,
        dirname: str = "proj",
        workspace: str | None = None,
        rev: str | None = None,
    ) -> neorev.VcsMetadata:
        """Build a VcsMetadata with test defaults."""
        return neorev.VcsMetadata(dirname=dirname, workspace=workspace, rev=rev)

    def test_uses_xdg_state_home_env(self) -> None:
        """Respect $XDG_STATE_HOME when set."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"XDG_STATE_HOME": tmpdir}),
            patch.object(neorev, "query_vcs_metadata", return_value=self.make_meta()),
        ):
            result = neorev.default_output_path(neorev.Vcs.GIT)
        self.assertTrue(result.startswith(tmpdir))
        self.assertTrue(result.endswith(".md"))

    def test_falls_back_to_xdg_default(self) -> None:
        """Use ~/.local/state when $XDG_STATE_HOME is unset."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(neorev, "query_vcs_metadata", return_value=self.make_meta()),
            patch.object(Path, "mkdir"),
        ):
            result = neorev.default_output_path(neorev.Vcs.GIT)
        self.assertIn(".local/state/neorev/proj", result)

    def test_filename_parts_basic(self) -> None:
        """Filename is just review.md; dirname becomes a subdirectory."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"XDG_STATE_HOME": tmpdir}),
            patch.object(
                neorev,
                "query_vcs_metadata",
                return_value=self.make_meta(dirname="myproj"),
            ),
        ):
            result = neorev.default_output_path(neorev.Vcs.GIT)
        self.assertEqual(Path(result).name, "review.md")
        self.assertEqual(Path(result).parent.name, "myproj")

    def test_filename_parts_with_workspace_and_rev(self) -> None:
        """Filename includes workspace and rev when available."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"XDG_STATE_HOME": tmpdir}),
            patch.object(
                neorev,
                "query_vcs_metadata",
                return_value=self.make_meta(workspace="feat", rev="abc1234"),
            ),
        ):
            result = neorev.default_output_path(neorev.Vcs.JUJUTSU)
        self.assertEqual(Path(result).name, "review-feat-abc1234.md")
        self.assertEqual(Path(result).parent.name, "proj")

    def test_filename_parts_with_rev_only(self) -> None:
        """Filename includes rev but skips empty workspace."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"XDG_STATE_HOME": tmpdir}),
            patch.object(
                neorev,
                "query_vcs_metadata",
                return_value=self.make_meta(rev="def456"),
            ),
        ):
            result = neorev.default_output_path(neorev.Vcs.GIT)
        self.assertEqual(Path(result).name, "review-def456.md")
        self.assertEqual(Path(result).parent.name, "proj")

    def test_does_not_create_state_directory(self) -> None:
        """default_output_path must not create the directory itself."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "sub" / "neorev" / "proj"
            with (
                patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(tmpdir) / "sub")}),
                patch.object(
                    neorev, "query_vcs_metadata", return_value=self.make_meta()
                ),
            ):
                neorev.default_output_path(neorev.Vcs.GIT)
            self.assertFalse(state_dir.is_dir())

    def test_resolve_args_detects_jj_from_flag(self) -> None:
        """resolve_args passes Vcs.JUJUTSU and None rev to default_output_path."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-j"])
        with patch.object(
            neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH
        ) as mock:
            neorev.resolve_args(args)
        mock.assert_called_once_with(neorev.Vcs.JUJUTSU, None)

    def test_resolve_args_passes_jj_rev(self) -> None:
        """resolve_args forwards the jj revision to default_output_path."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-j", "abc123"])
        with patch.object(
            neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH
        ) as mock:
            neorev.resolve_args(args)
        mock.assert_called_once_with(neorev.Vcs.JUJUTSU, "abc123")

    def test_resolve_args_detects_git_from_flag(self) -> None:
        """resolve_args passes Vcs.GIT and None rev to default_output_path."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-g"])
        with patch.object(
            neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH
        ) as mock:
            neorev.resolve_args(args)
        mock.assert_called_once_with(neorev.Vcs.GIT, None)

    def test_resolve_args_passes_git_rev(self) -> None:
        """resolve_args forwards the git revision to default_output_path."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-g", "HEAD~2"])
        with patch.object(
            neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH
        ) as mock:
            neorev.resolve_args(args)
        mock.assert_called_once_with(neorev.Vcs.GIT, "HEAD~2")

    def test_resolve_args_detects_jj_from_directory(self) -> None:
        """resolve_args detects jj when no flag is given."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args([])
        with (
            patch.object(neorev, "detect_vcs", return_value=neorev.Vcs.JUJUTSU),
            patch.object(
                neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH
            ) as mock,
        ):
            neorev.resolve_args(args)
        mock.assert_called_once_with(neorev.Vcs.JUJUTSU, None)

    def test_jj_uses_shortest_unambiguous_rev(self) -> None:
        """query_jj_metadata uses shortest() without a fixed length."""
        calls: list[list[str]] = []

        def fake_run_vcs(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            calls.append(cmd)
            if "log" in cmd:
                return "l"
            return "default: /some/path"

        with patch.object(neorev, "run_vcs", side_effect=fake_run_vcs):
            meta = neorev.query_jj_metadata()
        self.assertEqual(meta.rev, "l")
        log_cmd = calls[0]
        template_arg = log_cmd[-1]
        self.assertIn("shortest()", template_arg)
        self.assertNotIn("shortest(8)", template_arg)

    def test_jj_passes_custom_rev_to_log(self) -> None:
        """query_jj_metadata passes a custom rev to jj log -r."""
        calls: list[list[str]] = []

        def fake_run_vcs(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            calls.append(cmd)
            if "log" in cmd:
                return "x"
            return "default: /some/path"

        with patch.object(neorev, "run_vcs", side_effect=fake_run_vcs):
            meta = neorev.query_jj_metadata(rev="myrev")
        self.assertEqual(meta.rev, "x")
        log_cmd = calls[0]
        r_idx = log_cmd.index("-r")
        self.assertEqual(log_cmd[r_idx + 1], "myrev")

    def test_jj_picks_workspace_matching_cwd(self) -> None:
        """query_jj_metadata selects the workspace whose path matches cwd."""

        def fake_run_vcs(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            if "log" in cmd:
                return "abc"
            return "default: /home/user/proj\nfeature: /home/user/proj-feature"

        with (
            patch.object(neorev, "run_vcs", side_effect=fake_run_vcs),
            patch.object(Path, "cwd", return_value=Path("/home/user/proj-feature")),
        ):
            meta = neorev.query_jj_metadata()
        self.assertEqual(meta.workspace, "feature")

    def test_git_default_rev_appends_dirty_suffix(self) -> None:
        """query_git_metadata with rev=None returns '<short>-dirty' for working tree."""

        def fake_run_vcs(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            if "rev-parse" in cmd and "--short" in cmd:
                return "abc1234"
            if "--show-toplevel" in cmd:
                return "/home/user/proj"
            return "worktree /home/user/proj\n"

        with patch.object(neorev, "run_vcs", side_effect=fake_run_vcs):
            meta = neorev.query_git_metadata()
        self.assertEqual(meta.rev, "abc1234-dirty")

    def test_git_explicit_rev_has_no_dirty_suffix(self) -> None:
        """query_git_metadata with an explicit rev returns the bare short hash."""

        def fake_run_vcs(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            if "rev-parse" in cmd and "--short" in cmd:
                return "abc1234"
            if "--show-toplevel" in cmd:
                return "/home/user/proj"
            return "worktree /home/user/proj\n"

        with patch.object(neorev, "run_vcs", side_effect=fake_run_vcs):
            meta = neorev.query_git_metadata(rev="v1.0")
        self.assertEqual(meta.rev, "abc1234")

    def test_git_passes_custom_rev_to_rev_parse(self) -> None:
        """query_git_metadata passes a custom rev to git rev-parse."""
        calls: list[list[str]] = []

        def fake_run_vcs(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            calls.append(cmd)
            if "rev-parse" in cmd and "--short" in cmd:
                return "abc1234"
            if "--show-toplevel" in cmd:
                return "/home/user/proj"
            return "worktree /home/user/proj\n"

        with patch.object(neorev, "run_vcs", side_effect=fake_run_vcs):
            meta = neorev.query_git_metadata(rev="v1.0")
        self.assertEqual(meta.rev, "abc1234")
        rev_parse_cmd = calls[0]
        self.assertIn("v1.0", rev_parse_cmd)


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


class TestDetectVcs(unittest.TestCase):
    """Tests for detect_vcs."""

    def test_returns_none_outside_any_repo(self) -> None:
        """Return None when no VCS probe command succeeds."""
        with patch("neorev.try_vcs", return_value=False):
            result = neorev.detect_vcs()
        self.assertIsNone(result)

    def test_returns_jujutsu_in_jj_repo(self) -> None:
        """Return Vcs.JUJUTSU when jj root succeeds."""

        def fake_try_vcs(command: list[str]) -> bool:
            """Succeed only for jj."""
            return command[0] == "jj"

        with patch("neorev.try_vcs", side_effect=fake_try_vcs):
            result = neorev.detect_vcs()
        self.assertEqual(result, neorev.Vcs.JUJUTSU)

    def test_prefers_jujutsu_over_git_and_short_circuits(self) -> None:
        """Return Vcs.JUJUTSU and only probe jj when both backends are present."""
        mock = MagicMock(return_value=True)
        with patch("neorev.try_vcs", mock):
            result = neorev.detect_vcs()
        self.assertEqual(result, neorev.Vcs.JUJUTSU)
        mock.assert_called_once()
        self.assertEqual(mock.call_args[0][0][0], "jj")

    def test_returns_git_in_git_repo(self) -> None:
        """Return Vcs.GIT when git rev-parse succeeds but jj root does not."""

        def fake_try_vcs(command: list[str]) -> bool:
            """Succeed only for git."""
            return command[0] == "git"

        with patch("neorev.try_vcs", side_effect=fake_try_vcs):
            result = neorev.detect_vcs()
        self.assertEqual(result, neorev.Vcs.GIT)
