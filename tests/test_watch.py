"""Tests for watch mode and jujutsu working-copy snapshot behavior."""

import argparse
import asyncio
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.helpers import (
    FAKE_EDITOR,
    SIMPLE_DIFF,
    TWO_HUNK_DIFF,
    ReviewDriver,
    ReviewTestCase,
    make_large_diff,
    neorev,
    review_session,
)

WATCH_OUTPUT_NAME = "review.md"
UNUSED_OUTPUT_PATH = "unused-review.md"
APPROVED_HASHES_TOKEN = "approved-hashes="
ALL_CLEAR_TOKEN = "all clear"
INOTIFYWAIT_FAKE_PATH = "/usr/bin/inotifywait"
JJ_WORKING_COPY_SOURCE = "jj show"
APPROVE_KEY = "a"
QUIT_KEY = "q"
FLAG_KEY = "f"
CANCEL_KEY = "escape"
SCROLL_DOWN_KEY = "ctrl+d"
DEBOUNCE_TIMEOUT = 0.01
DEBOUNCE_SETTLE = 0.2
# Long enough for a watcher that ignores the shutdown to spawn inotifywait.
SPAWN_TIMEOUT = 0.5
PROBE_TIMEOUT = 5.0


@contextlib.asynccontextmanager
async def watch_review(diff_text: str) -> AsyncIterator[tuple[ReviewDriver, Path]]:
    """Run a watched review over *diff_text*, yielding its driver and output path."""
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / WATCH_OUTPUT_NAME
        async with review_session(
            diff_text,
            output_path=str(output),
            watch=neorev.JjSource(None),
        ) as review:
            yield review, output


async def reload_with(review: ReviewDriver, diff_text: str) -> None:
    """Reload *review* as if the repository now produced *diff_text*."""
    with patch.object(
        neorev,
        "fetch_watch_diff",
        return_value=(diff_text, JJ_WORKING_COPY_SOURCE),
    ):
        review.app.reload()
    await review.settle()


def counts_shown(review: ReviewDriver) -> list[bool]:
    """Return whether each count of *review* is drawn, above then below."""
    return [count.display for count in review.app.query(neorev.HiddenRows).nodes]


def watch_session_args(output: str) -> argparse.Namespace:
    """Parse the command line of a watch session writing its review to *output*."""
    return neorev.build_arg_parser().parse_args(["-w", "-x", "-o", output])


class TestJjWorkingCopyFlag(unittest.TestCase):
    """The diff command snapshots the working copy; metadata queries skip it."""

    def test_jj_diff_command_spellings(self) -> None:
        """Verify jj_diff_command appends the rev and never stops snapshotting."""
        cases = [(None, ["jj", "show"]), ("abc123", ["jj", "show", "abc123"])]
        for rev, expected in cases:
            with self.subTest(rev=rev):
                self.assertEqual(neorev.jj_diff_command(rev), expected)

    def test_jj_root_probes_with_ignore_working_copy(self) -> None:
        """Verify jj_root probes jj root with --ignore-working-copy."""
        with patch.object(neorev, "run_jj", return_value="/repo") as mock:
            neorev.jj_root()
        self.assertEqual(mock.call_args[0][0], ["jj", "root", "--ignore-working-copy"])


class TestWatchArgParsing(unittest.TestCase):
    """The -w/--watch flag toggles watch mode."""

    def test_watch_flags(self) -> None:
        """Verify both spellings enable watch mode and omitting them leaves it off."""
        parser = neorev.build_arg_parser()
        cases = [(["-o", "out.md"], False), (["-w"], True), (["--watch"], True)]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertIs(parser.parse_args(argv).watch, expected)


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
        """Verify a successful command yields its stdout and the source label."""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=SIMPLE_DIFF
        )
        with patch("neorev.subprocess.run", return_value=completed):
            text, source = neorev.fetch_watch_diff(neorev.JjSource(None))
        self.assertEqual(text, SIMPLE_DIFF)
        self.assertEqual(source, JJ_WORKING_COPY_SOURCE)

    def test_returns_empty_on_failure(self) -> None:
        """Verify a failed command yields empty text without raising."""
        error = subprocess.CalledProcessError(1, ["jj", "show"])
        with patch("neorev.subprocess.run", side_effect=error):
            text, source = neorev.fetch_watch_diff(neorev.JjSource("abc"))
        self.assertEqual(text, "")
        self.assertEqual(source, "jj show abc")


class TestBuildWatchState(unittest.TestCase):
    """Tests for build_watch_state."""

    def test_hunkless_inputs_wait_for_changes(self) -> None:
        """Verify diff text holding no parseable hunk produces an empty state."""
        for diff_text in ("", "  \n ", "not a diff"):
            with self.subTest(diff_text=diff_text):
                state = neorev.build_watch_state(diff_text, UNUSED_OUTPUT_PATH)
                self.assertTrue(state.is_empty)

    def test_diff_returns_state(self) -> None:
        """Verify a real diff yields a state positioned at the first hunk."""
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / WATCH_OUTPUT_NAME)
            state = neorev.build_watch_state(SIMPLE_DIFF, output)
        self.assertEqual(state.current_index, 0)
        self.assertEqual(len(state.hunks), 1)


class TestWatchModeGuards(unittest.TestCase):
    """main() rejects unusable watch-mode combinations."""

    def run_main(self, argv: list[str], stdin_text: str) -> int:
        """Run main() with *argv* and piped *stdin_text*; return the exit code."""
        with (
            patch.dict(os.environ, {"EDITOR": FAKE_EDITOR}),
            patch.object(sys, "argv", argv),
            patch.object(sys, "stdin", io.StringIO(stdin_text)),
            self.assertRaises(SystemExit) as ctx,
        ):
            neorev.main()
        code = ctx.exception.code
        return code if isinstance(code, int) else 1

    def test_stdin_with_watch_is_usage_error(self) -> None:
        """Verify piping a diff with --watch fails with a usage error."""
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
        """Verify --watch without inotifywait exits before fetching any diff."""
        with patch("neorev.shutil.which", return_value=None):
            argv = ["neorev", "-w", "-j", "abc", "-o", "out.md"]
            with patch.object(sys, "stderr", io.StringIO()):
                code = self.run_main(argv, "")
        self.assertEqual(code, os.EX_UNAVAILABLE)

    def test_missing_editor_is_fatal(self) -> None:
        """Verify an unset $EDITOR stops the review before it starts."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(sys, "argv", ["neorev", "-o", "out.md"]),
            patch.object(sys, "stdin", io.StringIO(SIMPLE_DIFF)),
            patch.object(sys, "stderr", io.StringIO()),
            self.assertRaises(SystemExit) as ctx,
        ):
            neorev.main()
        self.assertEqual(ctx.exception.code, os.EX_USAGE)


class TestWatchReload(ReviewTestCase):
    """Tests for rebuilding the review after a repository change."""

    async def test_reload_replaces_the_hunks(self) -> None:
        """Verify a new diff replaces the hunks under review."""
        async with watch_review(SIMPLE_DIFF) as (review, _):
            await reload_with(review, TWO_HUNK_DIFF)
            self.assertEqual(len(review.state.hunks), 2)

    async def test_reload_saves_the_review_so_far(self) -> None:
        """Verify the review is written out before the diff is fetched again."""
        async with watch_review(SIMPLE_DIFF) as (review, output):
            await review.press(APPROVE_KEY)
            await reload_with(review, TWO_HUNK_DIFF)
            written = output.read_text()
            self.assertIn(ALL_CLEAR_TOKEN, written)
            self.assertIn(APPROVED_HASHES_TOKEN, written)

    async def test_unchanged_diff_keeps_the_review(self) -> None:
        """Verify a repository change that leaves the diff alone keeps the state."""
        async with watch_review(SIMPLE_DIFF) as (review, _):
            await review.press(APPROVE_KEY)
            await reload_with(review, SIMPLE_DIFF)
            self.assertTrue(review.state.hunks[0].approved)

    async def test_empty_diff_waits_then_reloads(self) -> None:
        """Verify an empty diff waits, then takes the hunks a later change brings."""
        async with watch_review("") as (review, _):
            self.assertTrue(review.state.is_empty)
            await reload_with(review, SIMPLE_DIFF)
            self.assertEqual(len(review.state.hunks), 1)
            self.assertTrue(review.app.query_one(neorev.Diff).display)

    async def test_a_reload_to_an_empty_diff_takes_the_counts_away(self) -> None:
        """Verify an emptied review leaves no count in the chrome it scrolled."""
        async with watch_review(make_large_diff()) as (review, _):
            await review.press(SCROLL_DOWN_KEY)
            self.assertEqual(counts_shown(review), [True, True])
            await reload_with(review, "")
            self.assertTrue(review.state.is_empty)
            self.assertEqual(counts_shown(review), [False, False])

    async def test_reload_waits_for_an_open_modal(self) -> None:
        """Verify a change under an open picker is held back until it closes."""
        fetched = asyncio.Event()

        def fetch(source: neorev.JjSource) -> tuple[str, str]:  # noqa: ARG001
            """Record the diff being fetched again, and hand back a longer one."""
            fetched.set()
            return TWO_HUNK_DIFF, JJ_WORKING_COPY_SOURCE

        async with watch_review(SIMPLE_DIFF) as (review, _):
            with (
                patch.object(neorev, "WATCH_DEBOUNCE_TIMEOUT", DEBOUNCE_TIMEOUT),
                patch.object(neorev, "fetch_watch_diff", fetch),
            ):
                await review.press(FLAG_KEY)
                review.app.reload()
                self.assertEqual(len(review.state.hunks), 1)
                self.assertFalse(fetched.is_set())

                await review.press(CANCEL_KEY)
                await asyncio.wait_for(fetched.wait(), DEBOUNCE_SETTLE)
                await review.settle()
            self.assertEqual(len(review.state.hunks), 2)

    async def test_delta_output_from_before_a_reload_is_dropped(self) -> None:
        """Verify delta output still in flight when the diff empties is ignored."""
        async with watch_review(SIMPLE_DIFF) as (review, _):
            stale = review.app.delta_generation
            await reload_with(review, "")
            self.assertTrue(review.state.is_empty)
            review.app.absorb_delta_lines(0, stale, [b"stale line"])
            review.app.mark_delta_complete(0, stale)
            self.assertEqual(review.app.delta_cache, {})
            self.assertEqual(review.app.delta_complete, set())

    async def test_events_are_coalesced_into_one_reload(self) -> None:
        """Verify a burst of repository events triggers a single reload."""
        async with watch_review(SIMPLE_DIFF) as (review, _):
            reload_mock = MagicMock()
            with (
                patch.object(neorev, "WATCH_DEBOUNCE_TIMEOUT", DEBOUNCE_TIMEOUT),
                patch.object(review.app, "reload", reload_mock),
            ):
                review.app.schedule_reload()
                review.app.schedule_reload()
                review.app.schedule_reload()
                await review.pilot.pause()
                await asyncio.sleep(DEBOUNCE_SETTLE)
                await review.pilot.pause()
            self.assertEqual(reload_mock.call_count, 1)


class TestWatcherShutdown(ReviewTestCase):
    """Tests for stopping the watcher when the review is left."""

    async def test_quitting_during_the_probe_starts_no_watcher(self) -> None:
        """Verify a quit while jj_root is probing leaves no inotifywait behind."""
        release = threading.Event()
        spawned = threading.Event()
        probe_started = threading.Event()

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".jj" / "repo" / "op_heads" / "heads").mkdir(parents=True)

            def slow_root() -> str:
                """Report the probe starting, then hold it open until it is released."""
                probe_started.set()
                release.wait(PROBE_TIMEOUT)
                return tmp

            def popen(*args: object, **kwargs: object) -> MagicMock:  # noqa: ARG001
                """Record that the watcher reached the point of spawning."""
                spawned.set()
                return MagicMock()

            state = neorev.ReviewState(hunks=[], global_notes=[])
            session = neorev.Session(
                output_path=str(Path(tmp) / WATCH_OUTPUT_NAME),
                diff_source=None,
                diff_text="",
                watch=neorev.JjSource(None),
            )
            app = neorev.ReviewApp(state, session)
            with (
                patch.object(neorev, "jj_root", slow_root),
                patch.object(neorev.subprocess, "Popen", popen),
            ):
                try:
                    async with app.run_test():
                        started = await asyncio.to_thread(
                            probe_started.wait,
                            PROBE_TIMEOUT,
                        )
                        self.assertTrue(started)
                finally:
                    release.set()
                self.assertFalse(await asyncio.to_thread(spawned.wait, SPAWN_TIMEOUT))


class TestWatchSessionExit(unittest.TestCase):
    """Tests for watch-session output and clipboard handling on exit."""

    def test_review_written_and_copied_on_quit(self) -> None:
        """Verify quitting a watch session writes the file and fills the clipboard."""
        clipboard = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / WATCH_OUTPUT_NAME)
            args = watch_session_args(output)

            def run(app: neorev.ReviewApp, *, mouse: bool) -> None:  # noqa: ARG001
                """Approve every hunk instead of starting the UI."""
                for hunk in app.state.hunks:
                    hunk.approved = True

            with (
                patch.object(neorev.ReviewApp, "run", run),
                patch.object(
                    neorev,
                    "fetch_watch_diff",
                    return_value=(SIMPLE_DIFF, JJ_WORKING_COPY_SOURCE),
                ),
                patch.object(neorev, "copy_output_reference_to_clipboard", clipboard),
                patch.object(sys, "stderr", io.StringIO()),
            ):
                neorev.run_watch_session(args, neorev.JjSource(None))
            content = Path(output).read_text()
        self.assertEqual(clipboard.call_count, 1)
        self.assertIn(ALL_CLEAR_TOKEN, content)
        self.assertIn(APPROVED_HASHES_TOKEN, content)

    def test_untouched_review_writes_nothing(self) -> None:
        """Verify quitting without reviewing anything leaves no file behind."""
        clipboard = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / WATCH_OUTPUT_NAME)
            args = watch_session_args(output)
            with (
                patch.object(neorev.ReviewApp, "run", MagicMock()),
                patch.object(
                    neorev,
                    "fetch_watch_diff",
                    return_value=(SIMPLE_DIFF, JJ_WORKING_COPY_SOURCE),
                ),
                patch.object(neorev, "copy_output_reference_to_clipboard", clipboard),
                patch.object(sys, "stderr", io.StringIO()),
            ):
                neorev.run_watch_session(args, neorev.JjSource(None))
            self.assertFalse(Path(output).exists())
        self.assertEqual(clipboard.call_count, 0)


if __name__ == "__main__":
    unittest.main()
