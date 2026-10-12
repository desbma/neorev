"""CLI-related tests for neorev."""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from tests.helpers import (
    FAKE_CHANGE_ID,
    neorev,
)

PIPED_CWD = Path("/home/user/Projets/Piped")
PIPED_DIRNAME = "projets-piped"
REPO_DIRNAME = "projets-proj"
RESOLVED_REV = "trunk()..@"
OTHER_CHANGE_ID = "zzzzzzzzkkkkkkkkzzzzzzzzkkkkkkkk"


class TestArgParser(unittest.TestCase):
    """Tests for build_arg_parser."""

    def test_output_flag(self) -> None:
        """Verify the -o/--output flag sets the output path, other flags unset."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-o", "out.md"])
        self.assertEqual(args.output, "out.md")
        self.assertTrue(args.clip)
        self.assertFalse(args.clear)

    def test_output_long_form(self) -> None:
        """Verify the --output long form sets the output path."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--output", "out.md"])
        self.assertEqual(args.output, "out.md")

    def test_no_clip_flag(self) -> None:
        """Verify the -X/--no-clip flag turns the clipboard copy off."""
        parser = neorev.build_arg_parser()
        for flag in ("-X", "--no-clip"):
            with self.subTest(flag=flag):
                self.assertFalse(parser.parse_args([flag, "-o", "out.md"]).clip)

    def test_clear_flag(self) -> None:
        """Verify the -c/--clear flag is recognised."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--clear", "-o", "out.md"])
        self.assertTrue(args.clear)
        args_short = parser.parse_args(["-c", "-o", "out.md"])
        self.assertTrue(args_short.clear)

    def test_output_defaults_to_none(self) -> None:
        """Verify omitting -o leaves output as None."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.output)

    def test_jj_revision_argument(self) -> None:
        """Verify the optional revision accepted by -j and --jj."""
        parser = neorev.build_arg_parser()
        cases = (
            ([], None),
            (["-j"], neorev.JJ_DEFAULT_REV),
            (["-j", "abc123"], "abc123"),
            (["--jj"], neorev.JJ_DEFAULT_REV),
            (["--jj", "v1.0"], "v1.0"),
        )
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(parser.parse_args(argv).rev, expected)


class TestResolveJjSource(unittest.TestCase):
    """Tests for resolve_jj_source."""

    def resolve(self, stdout: str) -> tuple[neorev.JjSource, list[str]]:
        """Resolve RESOLVED_REV against a jj printing *stdout*, with its command."""
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout)
        with patch("neorev.subprocess.run", return_value=completed) as run:
            source = neorev.resolve_jj_source(RESOLVED_REV)
        return source, run.call_args[0][0]

    def test_returns_the_full_change_id(self) -> None:
        """Verify the single change the revision matches is returned in full."""
        source, command = self.resolve(f"{FAKE_CHANGE_ID}\n")
        self.assertEqual(source, neorev.JjSource(FAKE_CHANGE_ID))
        self.assertEqual(command[command.index("-r") + 1], RESOLVED_REV)

    def test_snapshots_the_working_copy(self) -> None:
        """Verify the lookup lets jj record pending edits before evaluating."""
        _, command = self.resolve(f"{FAKE_CHANGE_ID}\n")
        self.assertNotIn("--ignore-working-copy", command)

    def test_never_colors_the_change_id(self) -> None:
        """Verify the lookup output stays free of escape codes whatever the config."""
        _, command = self.resolve(f"{FAKE_CHANGE_ID}\n")
        self.assertIn("--color=never", command)

    def test_exits_when_the_change_id_selects_another_commit(self) -> None:
        """Verify a divergent or hidden commit exits with a hint to pipe its diff."""
        for state in ("divergent", "hidden"):
            with (
                self.subTest(state=state),
                contextlib.redirect_stderr(io.StringIO()) as stderr,
                self.assertRaises(SystemExit) as caught,
            ):
                self.resolve(f"{FAKE_CHANGE_ID} {state}\n")
            self.assertEqual(caught.exception.code, os.EX_DATAERR)
            self.assertIn(f"jj show '{RESOLVED_REV}' | neorev", stderr.getvalue())

    def test_exits_unless_exactly_one_change(self) -> None:
        """Verify a revision matching no change or several exits with an error."""
        for stdout in ("", f"{FAKE_CHANGE_ID}\n{OTHER_CHANGE_ID}\n"):
            with (
                self.subTest(stdout=stdout),
                contextlib.redirect_stderr(io.StringIO()) as stderr,
                self.assertRaises(SystemExit) as caught,
            ):
                self.resolve(stdout)
            self.assertEqual(caught.exception.code, os.EX_DATAERR)
            self.assertIn(RESOLVED_REV, stderr.getvalue())


class TestDefaultOutputPath(unittest.TestCase):
    """Tests for default_output_path."""

    def test_falls_back_to_xdg_default(self) -> None:
        """Use ~/.local/state when $XDG_STATE_HOME is unset."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(Path, "cwd", return_value=PIPED_CWD),
        ):
            result = neorev.default_output_path(None)
        self.assertIn(f".local/state/agents/reviews/{PIPED_DIRNAME}", result)

    def test_piped_diff_uses_xdg_state_without_jj(self) -> None:
        """Verify a piped diff uses the XDG path without querying jj."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.dict(os.environ, {"XDG_STATE_HOME": tmpdir}),
            patch.object(Path, "cwd", return_value=PIPED_CWD),
            patch("neorev.subprocess.run") as run,
        ):
            result = Path(neorev.default_output_path(None))
        run.assert_not_called()
        self.assertEqual(
            result,
            Path(tmpdir) / neorev.REVIEWS_STATE_DIR / PIPED_DIRNAME / "neorev.md",
        )

    def test_jj_source_leads_with_its_change_id(self) -> None:
        """Verify a jj review file opens with a fixed-length change id prefix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "Projets" / "proj"
            workspace.mkdir(parents=True)
            with (
                patch.dict(os.environ, {"XDG_STATE_HOME": tmpdir}),
                patch.object(neorev, "jj_workspace_roots", return_value=[workspace]),
                patch.object(Path, "cwd", return_value=workspace),
            ):
                result = Path(
                    neorev.default_output_path(neorev.JjSource(FAKE_CHANGE_ID))
                )
        prefix = FAKE_CHANGE_ID[: neorev.JJ_REV_LENGTH]
        self.assertEqual(result.name, f"{prefix}-neorev.md")
        self.assertEqual(result.parent.name, REPO_DIRNAME)

    def test_jj_source_shares_dirname_across_workspaces(self) -> None:
        """Verify every workspace of a repo resolves to the same project directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "Projets"
            default_ws = base / "proj"
            feature_ws = base / "proj-feature"
            for workspace in (default_ws, feature_ws):
                workspace.mkdir(parents=True)
            dirnames = []
            for cwd in (default_ws, feature_ws, feature_ws / "src"):
                with (
                    patch.object(
                        neorev,
                        "jj_workspace_roots",
                        return_value=[default_ws, feature_ws],
                    ),
                    patch.object(Path, "cwd", return_value=cwd),
                ):
                    output = neorev.default_output_path(neorev.JjSource(FAKE_CHANGE_ID))
                dirnames.append(Path(output).parent.name)
        self.assertEqual(dirnames, [REPO_DIRNAME] * len(dirnames))

    def test_does_not_create_state_directory(self) -> None:
        """Verify default_output_path must not create the directory itself."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "sub" / "agents" / "reviews" / PIPED_DIRNAME
            with (
                patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(tmpdir) / "sub")}),
                patch.object(Path, "cwd", return_value=PIPED_CWD),
            ):
                neorev.default_output_path(None)
            self.assertFalse(state_dir.is_dir())


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
        """Verify sibling workspace roots resolve to the directory they sit under."""
        with tempfile.TemporaryDirectory() as tmpdir:
            main = Path(tmpdir) / "proj"
            feature = Path(tmpdir) / "proj-feature"
            for workspace in (main, feature):
                workspace.mkdir()
            result = neorev.shared_workspace_root(feature, [main, feature])
        self.assertEqual(result, main)

    def test_prefix_cutting_mid_component_backs_up_to_parent(self) -> None:
        """Verify a common prefix that is not a directory falls back to its parent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            first = base / "proj-a"
            second = base / "proj-b"
            for workspace in (first, second):
                workspace.mkdir()
            result = neorev.shared_workspace_root(first, [first, second])
        self.assertEqual(result, base)

    def test_single_workspace_returns_its_root(self) -> None:
        """Verify a lone workspace root is its own shared root."""
        with tempfile.TemporaryDirectory() as tmpdir:
            main = Path(tmpdir) / "proj"
            main.mkdir()
            result = neorev.shared_workspace_root(main, [main])
        self.assertEqual(result, main)

    def test_deeply_nested_workspace_keeps_its_own_root(self) -> None:
        """Verify a workspace too far below the shared prefix does not share it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            main = Path(tmpdir) / "proj"
            nested = main / ".worktrees" / "exp"
            nested.mkdir(parents=True)
            result = neorev.shared_workspace_root(nested, [main, nested])
        self.assertEqual(result, nested)

    def test_subdirectory_resolves_to_its_workspace_root(self) -> None:
        """Verify a cwd below a workspace root is treated as that workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            main = Path(tmpdir) / "proj"
            nested = main / ".worktrees" / "exp"
            nested.mkdir(parents=True)
            result = neorev.shared_workspace_root(nested / "src", [main, nested])
        self.assertEqual(result, nested)

    def test_emits_no_warning(self) -> None:
        """Verify resolving the shared root raises no warning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            main = Path(tmpdir) / "proj"
            main.mkdir()
            with warnings.catch_warnings(action="error"):
                result = neorev.shared_workspace_root(main, [main])
        self.assertEqual(result, main)


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
