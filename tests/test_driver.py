"""End-to-end test of the terminal driver, with the diff piped on standard input."""

import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import NEOREV_PATH, REVIEW_SCREEN_INDEX_TOKEN, SIMPLE_DIFF, neorev

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
ALT_SCREEN_ON = "\x1b[?1049h"
MOUSE_TRACKING_ON = "\x1b[?1000h"
ALTERNATE_SCROLL_OFF = "\x1b[?1007l"
ALTERNATE_SCROLL_ON = "\x1b[?1007h"
STARTUP_TIMEOUT = 10.0
KEY_TIMEOUT = 5.0
POLL_INTERVAL = 0.1
READ_SIZE = 65536
TERM_TYPE = "xterm-256color"
TERM_COLUMNS = 100
TERM_ROWS = 30
EDITOR_SCRIPT = '#!/bin/sh\nprintf \'%s\\n\' "$NOTE_TEXT" >> "${1%%:*}"\n'
NOTE_TEXT = "written from the editor"
DIFF_BODY_TOKEN = "import os"
FLAG_SECTION_TOKEN = "[REVIEW CHANGE REQUESTED] `hello.py"
REVIEW_FOOTER_TOKEN = "approve file"
PICKER_FOOTER_TOKEN = "whole hunk"
HUNK_FLAGGED_TOKEN = f"hunk {neorev.FLAG_ICON} 1"


class Session:
    """A neorev process reading its diff from a pipe and its keys from a pty."""

    def __init__(self, output_path: str, editor: str) -> None:
        """Start neorev on the sample diff, writing its review to *output_path*."""
        self.master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(
            slave_fd,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", TERM_ROWS, TERM_COLUMNS, 0, 0),
        )
        read_fd, write_fd = os.pipe()
        os.write(write_fd, SIMPLE_DIFF.encode())
        os.close(write_fd)
        self.process = subprocess.Popen(
            [sys.executable, NEOREV_PATH, "-o", output_path],
            stdin=read_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env={**os.environ, "TERM": TERM_TYPE, "EDITOR": editor},
            preexec_fn=take_controlling_tty,  # noqa: PLW1509
        )
        os.close(slave_fd)
        os.close(read_fd)
        self.output = ""

    def read_once(self) -> bool:
        """Read what the terminal has ready, returning False once it closes."""
        ready, _, _ = select.select([self.master_fd], [], [], POLL_INTERVAL)
        if not ready:
            return True
        try:
            chunk = os.read(self.master_fd, READ_SIZE)
        except OSError:
            return False
        self.output += chunk.decode(errors="replace")
        return bool(chunk)

    def wait_for(self, token: str, seconds: float) -> bool:
        """Read until *token* shows on the terminal, or *seconds* run out."""
        deadline = time.time() + seconds
        while token not in self.visible():
            if time.time() >= deadline or not self.read_once():
                return token in self.visible()
        return True

    def press(self, keys: str) -> None:
        """Send *keys* to the terminal."""
        os.write(self.master_fd, keys.encode())

    def visible(self) -> str:
        """Return everything drawn so far, with the escape sequences removed."""
        return ANSI_ESCAPE_RE.sub("", self.output)

    def close(self) -> int:
        """Read to the end of the output, then wait for neorev to exit."""
        deadline = time.time() + STARTUP_TIMEOUT
        while time.time() < deadline and self.read_once():
            pass
        code = self.process.wait(timeout=STARTUP_TIMEOUT)
        os.close(self.master_fd)
        return code


def take_controlling_tty() -> None:
    """Give the child its own session with the pty as its controlling terminal."""
    os.setsid()
    fcntl.ioctl(1, termios.TIOCSCTTY, 0)


class TestTtyDriver(unittest.TestCase):
    """Tests for the keys the driver reads when standard input is a pipe."""

    def setUp(self) -> None:
        """Create a temporary review file path and a scripted editor."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.output_path = str(Path(self.tmpdir.name) / "review.md")
        self.editor_path = str(Path(self.tmpdir.name) / "editor.sh")
        Path(self.editor_path).write_text(EDITOR_SCRIPT)
        Path(self.editor_path).chmod(0o755)

    def tearDown(self) -> None:
        """Remove the temporary directory."""
        self.tmpdir.cleanup()

    def test_terminal_review_round_trip(self) -> None:
        """Verify the diff is drawn, the keys come from the tty and $EDITOR runs."""
        with patch.dict(os.environ, {"NOTE_TEXT": NOTE_TEXT}):
            session = Session(self.output_path, self.editor_path)
            self.assertTrue(session.wait_for(REVIEW_FOOTER_TOKEN, STARTUP_TIMEOUT))
            self.assertIn(ALT_SCREEN_ON, session.output)
            self.assertTrue(session.wait_for(DIFF_BODY_TOKEN, STARTUP_TIMEOUT))
            self.assertIn(REVIEW_SCREEN_INDEX_TOKEN, session.visible())
            self.assertNotIn(MOUSE_TRACKING_ON, session.output)
            self.assertIn(ALTERNATE_SCROLL_OFF, session.output)
            session.press("f")
            self.assertTrue(session.wait_for(PICKER_FOOTER_TOKEN, KEY_TIMEOUT))
            session.press("h")
            self.assertTrue(session.wait_for(HUNK_FLAGGED_TOKEN, KEY_TIMEOUT))
            session.press("q")
            self.assertEqual(session.close(), 0)
            self.assertGreater(
                session.output.rfind(ALTERNATE_SCROLL_ON),
                session.output.rfind(ALTERNATE_SCROLL_OFF),
            )
        review = Path(self.output_path).read_text()
        self.assertIn(NOTE_TEXT, review)
        self.assertIn(FLAG_SECTION_TOKEN, review)


if __name__ == "__main__":
    unittest.main()
