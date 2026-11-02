"""Reads single keys from the terminal while the coordinator waits between turns.

KeyListener is a context manager. On enter, when stdin is a terminal, it puts the terminal in cbreak
mode (keys arrive one at a time, Ctrl-C still works). On exit it restores the saved settings, also after
Ctrl-C or an error. When stdin is not a terminal (pipe, cron), the coordinator runs as a background job
(started with &), or termios is missing (it is Unix only), the listener stays inactive: it changes nothing
and its wait is a plain sleep.

A background process that changes or flushes the terminal is stopped by the kernel (SIGTTOU). So the
listener also acts as inactive while it is not in the foreground, for example after Ctrl-Z and bg.
"""

import os
import select
import sys
import time

try:
    import termios
    import tty
except ImportError:
    termios = None
    tty = None


class KeyListener:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdin
        self.active = False
        self.fd = None
        self.saved = None

    def __enter__(self):
        if termios is None or not self.stream.isatty():
            return self
        self.fd = self.stream.fileno()
        if not self.in_foreground():
            return self
        self.saved = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        self.active = True
        return self

    def __exit__(self, *exc_info):
        # In the background, the shell restores its own terminal settings, so there is nothing to undo.
        if self.active and self.in_foreground():
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
        self.active = False
        return False

    def in_foreground(self):
        """True when this process is in the terminal's foreground process group."""
        try:
            return os.tcgetpgrp(self.fd) == os.getpgrp()
        except OSError:
            return False

    def wait_for_key(self, timeout):
        """Waits up to `timeout` seconds for a key. Returns the key, or None when the time ran out."""
        if not (self.active and self.in_foreground()):
            time.sleep(timeout)
            return None
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return None
        return os.read(self.fd, 1).decode(errors="replace")

    def discard_typed(self):
        """Drops keys typed so far, so a key pressed during a turn does not start another turn."""
        if self.active and self.in_foreground():
            termios.tcflush(self.fd, termios.TCIFLUSH)
