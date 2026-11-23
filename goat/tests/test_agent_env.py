"""Tests for coordinator.agent_env: the environment every headless agent starts with."""

import unittest
from unittest import mock

from goat import coordinator


class AgentEnvTest(unittest.TestCase):
    def env_from(self, outer):
        with mock.patch.dict("os.environ", outer, clear=True):
            return coordinator.agent_env()

    def test_sets_the_headless_flag(self):
        self.assertEqual(self.env_from({"PATH": "/usr/bin"})["GOAT_HEADLESS"], "1")

    def test_drops_the_starting_session_id(self):
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", self.env_from({"CLAUDE_CODE_SESSION_ID": "abc"}))

    def test_drops_every_herdr_variable(self):
        outer = {
            "PATH": "/usr/bin",
            "HERDR_ENV": "1",
            "HERDR_SOCKET_PATH": "/tmp/herdr.sock",
            "HERDR_WORKSPACE_ID": "w1",
            "HERDR_TAB_ID": "t1",
            "HERDR_PANE_ID": "w1:p2",
        }
        env = self.env_from(outer)
        self.assertEqual([name for name in env if name.startswith("HERDR_")], [])
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_keeps_other_variables(self):
        env = self.env_from({"HOME": "/home/dev", "MY_HERDR_NOTE": "kept"})
        self.assertEqual(env["HOME"], "/home/dev")
        self.assertEqual(env["MY_HERDR_NOTE"], "kept")

    def test_does_not_change_the_coordinator_environment(self):
        with mock.patch.dict("os.environ", {"HERDR_ENV": "1"}, clear=True):
            coordinator.agent_env()
            import os
            self.assertEqual(os.environ["HERDR_ENV"], "1")


if __name__ == "__main__":
    unittest.main()
