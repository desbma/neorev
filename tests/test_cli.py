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
        with patch.object(
            neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH
        ) as mock:
            neorev.resolve_args(args)
        self.assertEqual(args.output, MOCK_OUTPUT_PATH)
        mock.assert_called_once_with(None)

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
        rev: str | None = None,
    ) -> neorev.JjMetadata:
        """Build a JjMetadata with test defaults."""
        return neorev.JjMetadata(dirname=dirname, rev=rev)

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
        self.assertIn(".local/state/agents/reviews/proj", result)

    def test_filename_parts_basic(self) -> None:
        """Filename is just neorev.md; dirname becomes a subdirectory."""
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
        self.assertEqual(Path(result).name, "neorev.md")
        self.assertEqual(Path(result).parent.name, "myproj")

    def test_filename_parts_with_rev(self) -> None:
        """Filename leads with the rev, and never carries the workspace name."""
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
        self.assertEqual(Path(result).name, "def456-neorev.md")
        self.assertEqual(Path(result).parent.name, "proj")

    def test_does_not_create_state_directory(self) -> None:
        """default_output_path must not create the directory itself."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "sub" / "agents" / "reviews" / "proj"
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

    def test_resolve_args_fills_output_outside_jj(self) -> None:
        """resolve_args fills a default output for a piped diff outside a jj repo."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args([])
        with (
            patch.object(neorev, "jj_root", return_value=None),
            patch.object(neorev, "default_output_path", return_value=MOCK_OUTPUT_PATH),
        ):
            neorev.resolve_args(args)
        self.assertEqual(args.output, MOCK_OUTPUT_PATH)

    def test_jj_uses_fixed_length_rev(self) -> None:
        """jj_metadata asks for a change id of a fixed length, not the shortest one."""
        calls: list[list[str]] = []

        def fake_run_jj(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            calls.append(cmd)
            if "log" in cmd:
                return "l" * neorev.JJ_REV_LENGTH
            return "/some/path"

        with patch.object(neorev, "run_jj", side_effect=fake_run_jj):
            meta = neorev.jj_metadata()
        self.assertEqual(meta.rev, "l" * neorev.JJ_REV_LENGTH)
        log_cmd = calls[0]
        template_arg = log_cmd[-1]
        self.assertIn(f"short({neorev.JJ_REV_LENGTH})", template_arg)
        self.assertNotIn("shortest", template_arg)

    def test_jj_passes_custom_rev_to_log(self) -> None:
        """jj_metadata passes a custom rev to jj log -r."""
        calls: list[list[str]] = []

        def fake_run_jj(cmd: list[str]) -> str:
            """Capture calls and return stub output."""
            calls.append(cmd)
            if "log" in cmd:
                return "x"
            return "/some/path"

        with patch.object(neorev, "run_jj", side_effect=fake_run_jj):
            meta = neorev.jj_metadata(rev="myrev")
        self.assertEqual(meta.rev, "x")
        log_cmd = calls[0]
        r_idx = log_cmd.index("-r")
        self.assertEqual(log_cmd[r_idx + 1], "myrev")

    def test_jj_metadata_shares_dirname_across_workspaces(self) -> None:
        """Every workspace of a repo resolves to the same project directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "Projets"
            default_ws = base / "proj"
            feature_ws = base / "proj-feature"
            for workspace in (default_ws, feature_ws):
                workspace.mkdir(parents=True)

            def fake_run_jj(cmd: list[str]) -> str:
                """Return a stub change id and the repo's workspace roots."""
                if "log" in cmd:
                    return "abc"
                return f"{default_ws}\n{feature_ws}"

            dirnames = []
            for cwd in (default_ws, feature_ws, feature_ws / "src"):
                with (
                    patch.object(neorev, "run_jj", side_effect=fake_run_jj),
                    patch.object(Path, "cwd", return_value=cwd),
                ):
                    dirnames.append(neorev.jj_metadata().dirname)
        self.assertEqual(dirnames, ["projets-proj"] * len(dirnames))

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
        """Return the last 2 path components, lowercased, joined with '-'."""
        result = neorev.project_name(Path("/home/user/Projets/NeoRev"))
        self.assertEqual(result, "projets-neorev")

    def test_single_component_path(self) -> None:
        """Return just the single component lowercased when path has depth 1."""
        result = neorev.project_name(Path("/root"))
        self.assertEqual(result, "root")


class TestSharedWorkspaceRoot(unittest.TestCase):
    """Tests for shared_workspace_root."""

    def test_sibling_workspaces_share_the_prefix_root(self) -> None:
        """Sibling workspace roots resolve to the directory they all sit under."""
        with tempfile.TemporaryDirectory() as tmpdir:
            main = Path(tmpdir) / "proj"
            feature = Path(tmpdir) / "proj-feature"
            for workspace in (main, feature):
                workspace.mkdir()
            result = neorev.shared_workspace_root(feature, [main, feature])
        self.assertEqual(result, main)

    def test_prefix_cutting_mid_component_backs_up_to_parent(self) -> None:
        """A common prefix that is not a directory falls back to its parent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            first = base / "proj-a"
            second = base / "proj-b"
            for workspace in (first, second):
                workspace.mkdir()
            result = neorev.shared_workspace_root(first, [first, second])
        self.assertEqual(result, base)

    def test_single_workspace_returns_its_root(self) -> None:
        """A lone workspace root is its own shared root."""
        with tempfile.TemporaryDirectory() as tmpdir:
            main = Path(tmpdir) / "proj"
            main.mkdir()
            result = neorev.shared_workspace_root(main, [main])
        self.assertEqual(result, main)

    def test_deeply_nested_workspace_keeps_its_own_root(self) -> None:
        """A workspace too far below the shared prefix does not share it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            main = Path(tmpdir) / "proj"
            nested = main / ".worktrees" / "exp"
            nested.mkdir(parents=True)
            result = neorev.shared_workspace_root(nested, [main, nested])
        self.assertEqual(result, nested)

    def test_subdirectory_resolves_to_its_workspace_root(self) -> None:
        """A cwd below a workspace root is treated as that workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            main = Path(tmpdir) / "proj"
            nested = main / ".worktrees" / "exp"
            nested.mkdir(parents=True)
            result = neorev.shared_workspace_root(nested / "src", [main, nested])
        self.assertEqual(result, nested)


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
