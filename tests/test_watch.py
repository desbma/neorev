"""Tests for watch mode and jujutsu working-copy snapshot behavior."""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from collections.abc import Callable
from pathlib import Path
from typing import Self
from unittest.mock import MagicMock, patch

from tests.helpers import (
    SIMPLE_DIFF,
    FakeTTY,
    neorev,
)

WATCH_OUTPUT_NAME = "review.md"
UNUSED_OUTPUT_PATH = "unused-review.md"
APPROVED_HASHES_TOKEN = "approved-hashes="
ALL_CLEAR_TOKEN = "all clear"
INOTIFYWAIT_FAKE_PATH = "/usr/bin/inotifywait"
JJ_WORKING_COPY_SOURCE = "jj show"

LoopAction = Callable[[neorev.ReviewState | None], None]
LoopScript = list[tuple[LoopAction, neorev.LoopResult]]


def approve_all(state: neorev.ReviewState | None) -> None:
    """Approve every hunk in *state* (used as a scripted loop action)."""
    if state is not None:
        for hunk in state.hunks:
            hunk.approved = True


def noop(_state: neorev.ReviewState | None) -> None:
    """Leave the review state untouched."""


class FakeWatcher:
    """Stand-in for RepoWatcher exposing a real pipe read end."""

    def __init__(self) -> None:
        """Open a pipe so callers have a valid read fd to select on."""
        self.read_fd, self.write_fd = os.pipe()
        self.closed = False

    def close(self) -> None:
        """Close both pipe ends exactly once."""
        if not self.closed:
            os.close(self.read_fd)
            os.close(self.write_fd)
            self.closed = True


def fake_restart(old: FakeWatcher | None) -> FakeWatcher:
    """Replacement for restart_watcher that closes *old* and returns a fake."""
    if old is not None:
        old.close()
    return FakeWatcher()


class WatchScriptTerminal:
    """Terminal stub driving watch_loop with scripted loop results."""

    ALT_SCREEN_ON = ""
    ALT_SCREEN_OFF = ""
    CURSOR_HIDE = ""
    CURSOR_SHOW = ""

    def __init__(self, scripts: LoopScript) -> None:
        """Store the scripted (action, result) steps to replay."""
        self.scripts = list(scripts)
        self.review_calls = 0
        self.wait_calls = 0

    def __enter__(self) -> Self:
        """Return self for context-managed use."""
        return self

    def __exit__(self, *_: object) -> None:
        """Exit context manager without extra cleanup."""

    def write(self, _data: bytes | str) -> None:
        """Accept writes performed by the session without rendering."""

    def run_review_loop(
        self,
        state: neorev.ReviewState,
        _delta_cache: dict[int, neorev.DeltaStream],
        _watch_read_fd: int | None = None,
    ) -> neorev.LoopResult:
        """Apply the next scripted action and return its loop result."""
        self.review_calls += 1
        action, result = self.scripts.pop(0)
        action(state)
        return result

    def run_wait_screen(self, _watch_read_fd: int) -> neorev.LoopResult:
        """Apply the next scripted action for the empty-diff wait screen."""
        self.wait_calls += 1
        action, result = self.scripts.pop(0)
        action(None)
        return result


class TestJjWorkingCopyFlag(unittest.TestCase):
    """The diff command snapshots the working copy; metadata queries skip it."""

    def test_jj_diff_command_without_rev(self) -> None:
        """jj_diff_command omits both the rev and --ignore-working-copy."""
        self.assertEqual(
            neorev.jj_diff_command(None),
            ["jj", "show"],
        )

    def test_jj_diff_command_with_rev(self) -> None:
        """jj_diff_command appends the rev and keeps snapshotting enabled."""
        self.assertEqual(
            neorev.jj_diff_command("abc123"),
            ["jj", "show", "abc123"],
        )

    def test_jj_root_probes_with_ignore_working_copy(self) -> None:
        """jj_root probes jj root with --ignore-working-copy."""
        with patch.object(neorev, "run_jj", return_value="/repo") as mock:
            neorev.jj_root()
        self.assertEqual(mock.call_args[0][0], ["jj", "root", "--ignore-working-copy"])


class TestWatchArgParsing(unittest.TestCase):
    """The -w/--watch flag toggles watch mode."""

    def test_watch_defaults_false(self) -> None:
        """Without -w, watch is disabled."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-o", "out.md"])
        self.assertFalse(args.watch)

    def test_watch_short_flag(self) -> None:
        """The -w short flag enables watch mode."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["-w", "-o", "out.md"])
        self.assertTrue(args.watch)

    def test_watch_long_flag(self) -> None:
        """The --watch long flag enables watch mode."""
        parser = neorev.build_arg_parser()
        args = parser.parse_args(["--watch"])
        self.assertTrue(args.watch)


class TestWatchPaths(unittest.TestCase):
    """watch_paths derives the existing jj operation-heads directory."""

    def test_watches_op_heads(self) -> None:
        """Return only the operation-heads directory."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            op_heads = root / ".jj" / "repo" / "op_heads" / "heads"
            op_heads.mkdir(parents=True)
            paths = neorev.watch_paths(str(root))
        self.assertEqual(paths, [str(op_heads)])

    def test_missing_op_heads_returns_empty(self) -> None:
        """Return no paths when the operation-heads directory does not exist."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(neorev.watch_paths(tmp), [])

    def test_secondary_workspace_resolves_repo_pointer(self) -> None:
        """Follow a workspace's .jj/repo pointer file to the shared op-heads dir."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            op_heads = root / "default" / ".jj" / "repo" / "op_heads" / "heads"
            op_heads.mkdir(parents=True)
            secondary_jj = root / "secondary" / ".jj"
            secondary_jj.mkdir(parents=True)
            (secondary_jj / "repo").write_text("../../default/.jj/repo")
            paths = neorev.watch_paths(str(root / "secondary"))
        self.assertEqual(len(paths), 1)
        self.assertEqual(Path(paths[0]).resolve(), op_heads.resolve())


class TestFetchWatchDiff(unittest.TestCase):
    """fetch_watch_diff tolerates empty diffs and command failures."""

    def test_returns_stdout_on_success(self) -> None:
        """A successful command yields its stdout and the source label."""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=SIMPLE_DIFF
        )
        with patch("neorev.subprocess.run", return_value=completed):
            text, source = neorev.fetch_watch_diff(neorev.JjSource(None))
        self.assertEqual(text, SIMPLE_DIFF)
        self.assertEqual(source, JJ_WORKING_COPY_SOURCE)

    def test_returns_empty_on_failure(self) -> None:
        """A failed command yields empty text without raising."""
        error = subprocess.CalledProcessError(1, ["jj", "show"])
        with patch("neorev.subprocess.run", side_effect=error):
            text, source = neorev.fetch_watch_diff(neorev.JjSource("abc"))
        self.assertEqual(text, "")
        self.assertEqual(source, "jj show abc")


class TestBuildWatchState(unittest.TestCase):
    """build_watch_state returns None for empty diffs, a state otherwise."""

    def test_empty_string_returns_none(self) -> None:
        """An empty diff produces no state."""
        self.assertIsNone(neorev.build_watch_state("", UNUSED_OUTPUT_PATH))

    def test_whitespace_returns_none(self) -> None:
        """A whitespace-only diff produces no state."""
        self.assertIsNone(neorev.build_watch_state("  \n ", UNUSED_OUTPUT_PATH))

    def test_text_without_hunks_returns_none(self) -> None:
        """Text with no parseable hunks produces no state."""
        self.assertIsNone(neorev.build_watch_state("not a diff", UNUSED_OUTPUT_PATH))

    def test_diff_returns_state(self) -> None:
        """A real diff yields a state positioned at the first hunk."""
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / WATCH_OUTPUT_NAME)
            state = neorev.build_watch_state(SIMPLE_DIFF, output)
        self.assertIsNotNone(state)
        if state is not None:
            self.assertEqual(state.current_index, 0)
            self.assertEqual(len(state.hunks), 1)


class TestWatchModeGuards(unittest.TestCase):
    """main() rejects unusable watch-mode combinations."""

    def run_main(self, argv: list[str], stdin_text: str) -> int:
        """Run main() with *argv* and piped *stdin_text*; return the exit code."""
        with (
            patch.object(sys, "argv", argv),
            patch.object(sys, "stdin", io.StringIO(stdin_text)),
            self.assertRaises(SystemExit) as ctx,
        ):
            neorev.main()
        code = ctx.exception.code
        return code if isinstance(code, int) else 1

    def test_stdin_with_watch_is_usage_error(self) -> None:
        """Piping a diff with --watch fails with a usage error."""
        with (
            patch("neorev.shutil.which", return_value=INOTIFYWAIT_FAKE_PATH),
            tempfile.TemporaryDirectory() as tmp,
        ):
            output = str(Path(tmp) / WATCH_OUTPUT_NAME)
            argv = ["neorev", "-w", "-o", output]
            with patch.object(sys, "stderr", io.StringIO()):
                code = self.run_main(argv, SIMPLE_DIFF)
        self.assertEqual(code, os.EX_USAGE)

    def test_missing_inotifywait_is_fatal(self) -> None:
        """--watch without inotifywait exits before fetching any diff."""
        with patch("neorev.shutil.which", return_value=None):
            argv = ["neorev", "-w", "-j", "abc", "-o", "out.md"]
            with patch.object(sys, "stderr", io.StringIO()):
                code = self.run_main(argv, "")
        self.assertEqual(code, os.EX_UNAVAILABLE)


class TestReadKeyReload(unittest.TestCase):
    """read_key surfaces a watch event as KEY_RELOAD."""

    def test_watch_event_returns_reload(self) -> None:
        """A byte on the watch pipe makes read_key return KEY_RELOAD."""
        tty_pair = FakeTTY()
        read_fd, write_fd = os.pipe()
        try:
            term = tty_pair.make_terminal()
            os.write(write_fd, b"\x01")
            with patch.object(neorev, "WATCH_DEBOUNCE_TIMEOUT", 0.01):
                key = term.read_key(watch_read_fd=read_fd)
            self.assertEqual(key, neorev.Terminal.KEY_RELOAD)
            term.close()
        finally:
            os.close(read_fd)
            os.close(write_fd)
            tty_pair.close()


class TestWatchLoop(unittest.TestCase):
    """watch_loop persistence, reload, and clipboard behavior."""

    def make_args(self, output: str) -> Namespace:
        """Build a watch-mode argument namespace targeting *output*."""
        return Namespace(
            output=output,
            clip=True,
            watch=True,
            clear=False,
            source=neorev.JjSource(None),
        )

    def run_session(
        self, scripts: LoopScript, fetch_results: list[tuple[str, str]]
    ) -> tuple[MagicMock, str]:
        """Run run_watch_session with stubs; return (clipboard mock, output path)."""
        clipboard = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / WATCH_OUTPUT_NAME)
            args = self.make_args(output)
            with (
                patch.object(
                    neorev,
                    "Terminal",
                    side_effect=lambda: WatchScriptTerminal(scripts),
                ),
                patch.object(neorev, "restart_watcher", side_effect=fake_restart),
                patch.object(neorev, "fetch_watch_diff", side_effect=fetch_results),
                patch.object(neorev, "copy_output_reference_to_clipboard", clipboard),
                patch.object(sys, "stderr", io.StringIO()),
            ):
                neorev.run_watch_session(args, neorev.JjSource(None))
            return clipboard, Path(output).read_text()

    def test_all_clear_persisted_and_clipboard_once_on_quit(self) -> None:
        """All-clear is written every cycle; clipboard copies only on quit."""
        scripts: LoopScript = [
            (approve_all, neorev.LoopResult.RELOAD),
            (noop, neorev.LoopResult.QUIT),
        ]
        fetch_results = [(SIMPLE_DIFF, JJ_WORKING_COPY_SOURCE)] * 3
        clipboard, content = self.run_session(scripts, fetch_results)
        self.assertEqual(clipboard.call_count, 1)
        self.assertIn(ALL_CLEAR_TOKEN, content)
        self.assertIn(APPROVED_HASHES_TOKEN, content)
        footer = content.split(APPROVED_HASHES_TOKEN, 1)[1]
        self.assertTrue(footer.split("-->", 1)[0].strip())

    def test_empty_diff_waits_then_reloads(self) -> None:
        """An initially empty diff shows the wait screen, then reloads."""
        scripts: LoopScript = [
            (noop, neorev.LoopResult.RELOAD),
            (approve_all, neorev.LoopResult.QUIT),
        ]
        fetch_results = [
            ("", JJ_WORKING_COPY_SOURCE),
            (SIMPLE_DIFF, JJ_WORKING_COPY_SOURCE),
        ]
        term_holder: list[WatchScriptTerminal] = []

        clipboard = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / WATCH_OUTPUT_NAME)
            args = self.make_args(output)

            def make_term() -> WatchScriptTerminal:
                """Build the scripted terminal and capture it for assertions."""
                term = WatchScriptTerminal(scripts)
                term_holder.append(term)
                return term

            with (
                patch.object(neorev, "Terminal", side_effect=make_term),
                patch.object(neorev, "restart_watcher", side_effect=fake_restart),
                patch.object(neorev, "fetch_watch_diff", side_effect=fetch_results),
                patch.object(neorev, "copy_output_reference_to_clipboard", clipboard),
                patch.object(sys, "stderr", io.StringIO()),
            ):
                neorev.run_watch_session(args, neorev.JjSource(None))
            content = Path(output).read_text()

        self.assertEqual(term_holder[0].wait_calls, 1)
        self.assertEqual(term_holder[0].review_calls, 1)
        self.assertIn(ALL_CLEAR_TOKEN, content)


if __name__ == "__main__":
    unittest.main()
