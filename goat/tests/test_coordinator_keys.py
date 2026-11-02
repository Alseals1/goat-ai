"""Tests for goat/keys.py: the KeyListener that reads single keys while the coordinator waits."""

import unittest
from unittest import mock

from goat import keys

FD = 5
OUR_GROUP = 100
OTHER_GROUP = 200


def fake_stream(is_terminal):
    stream = mock.Mock()
    stream.isatty.return_value = is_terminal
    stream.fileno.return_value = FD
    return stream


class NotATerminalTest(unittest.TestCase):
    def test_inactive_listener_sleeps_and_changes_nothing(self):
        with mock.patch.object(keys, "termios") as termios, \
                mock.patch.object(keys, "tty") as tty, \
                mock.patch.object(keys.time, "sleep") as sleep:
            with keys.KeyListener(fake_stream(False)) as listener:
                self.assertFalse(listener.active)
                self.assertIsNone(listener.wait_for_key(2))
                listener.discard_typed()
        sleep.assert_called_once_with(2)
        termios.tcgetattr.assert_not_called()
        termios.tcsetattr.assert_not_called()
        termios.tcflush.assert_not_called()
        tty.setcbreak.assert_not_called()

    def test_inactive_when_termios_is_missing(self):
        with mock.patch.object(keys, "termios", None):
            with keys.KeyListener(fake_stream(True)) as listener:
                self.assertFalse(listener.active)


class TerminalMocksTest(unittest.TestCase):
    """Mocks the terminal calls. The process starts in the terminal's foreground group."""

    def setUp(self):
        patches = {
            "termios": mock.patch.object(keys, "termios"),
            "tty": mock.patch.object(keys, "tty"),
            "select": mock.patch.object(keys.select, "select"),
            "read": mock.patch.object(keys.os, "read"),
            "tcgetpgrp": mock.patch.object(keys.os, "tcgetpgrp", return_value=OUR_GROUP),
            "getpgrp": mock.patch.object(keys.os, "getpgrp", return_value=OUR_GROUP),
        }
        self.mocks = {name: patch.start() for name, patch in patches.items()}
        for patch in patches.values():
            self.addCleanup(patch.stop)
        self.termios = self.mocks["termios"]
        self.termios.tcgetattr.return_value = "saved settings"


class TerminalTest(TerminalMocksTest):

    def test_enter_sets_cbreak_and_exit_restores(self):
        with keys.KeyListener(fake_stream(True)) as listener:
            self.assertTrue(listener.active)
            self.mocks["tty"].setcbreak.assert_called_once_with(FD)
            self.termios.tcsetattr.assert_not_called()
        self.termios.tcsetattr.assert_called_once_with(FD, self.termios.TCSADRAIN, "saved settings")
        self.assertFalse(listener.active)

    def test_wait_for_key_returns_the_key(self):
        self.mocks["select"].return_value = ([FD], [], [])
        self.mocks["read"].return_value = b"r"
        with keys.KeyListener(fake_stream(True)) as listener:
            self.assertEqual(listener.wait_for_key(3), "r")
        self.mocks["select"].assert_called_once_with([FD], [], [], 3)
        self.mocks["read"].assert_called_once_with(FD, 1)

    def test_wait_for_key_returns_none_on_timeout(self):
        self.mocks["select"].return_value = ([], [], [])
        with keys.KeyListener(fake_stream(True)) as listener:
            self.assertIsNone(listener.wait_for_key(3))
        self.mocks["read"].assert_not_called()

    def test_discard_typed_flushes_input(self):
        with keys.KeyListener(fake_stream(True)) as listener:
            listener.discard_typed()
        self.termios.tcflush.assert_called_once_with(FD, self.termios.TCIFLUSH)

    def test_exit_restores_after_ctrl_c(self):
        with self.assertRaises(KeyboardInterrupt):
            with keys.KeyListener(fake_stream(True)):
                raise KeyboardInterrupt
        self.termios.tcsetattr.assert_called_once_with(FD, self.termios.TCSADRAIN, "saved settings")


class BackgroundTest(TerminalMocksTest):
    """The process is not (or no longer) in the terminal's foreground group."""

    def move_to_background(self):
        self.mocks["tcgetpgrp"].return_value = OTHER_GROUP

    def test_inactive_when_started_as_a_background_job(self):
        self.move_to_background()
        with mock.patch.object(keys.time, "sleep") as sleep:
            with keys.KeyListener(fake_stream(True)) as listener:
                self.assertFalse(listener.active)
                self.assertIsNone(listener.wait_for_key(2))
                listener.discard_typed()
        sleep.assert_called_once_with(2)
        self.termios.tcgetattr.assert_not_called()
        self.mocks["tty"].setcbreak.assert_not_called()
        self.termios.tcflush.assert_not_called()
        self.termios.tcsetattr.assert_not_called()

    def test_inactive_when_the_process_group_check_fails(self):
        self.mocks["tcgetpgrp"].side_effect = OSError
        with keys.KeyListener(fake_stream(True)) as listener:
            self.assertFalse(listener.active)
        self.mocks["tty"].setcbreak.assert_not_called()

    def test_moved_to_background_leaves_the_terminal_alone(self):
        with mock.patch.object(keys.time, "sleep") as sleep:
            with keys.KeyListener(fake_stream(True)) as listener:
                self.assertTrue(listener.active)
                self.move_to_background()
                self.assertIsNone(listener.wait_for_key(2))
                listener.discard_typed()
        sleep.assert_called_once_with(2)
        self.mocks["select"].assert_not_called()
        self.termios.tcflush.assert_not_called()
        self.termios.tcsetattr.assert_not_called()
        self.assertFalse(listener.active)


if __name__ == "__main__":
    unittest.main()
