"""Tests for the coordinator defaults that load_config fills in."""

import json
import os
import tempfile
import unittest
from unittest import mock

from goat import coordinator


def load_config_from(config):
    """Write config to a temporary file and run load_config on it."""
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "config.json")
        with open(path, "w") as f:
            json.dump(config, f)
        with mock.patch.object(coordinator, "CONFIG_FILE", path):
            return coordinator.load_config()


class LoadConfigDefaultsTest(unittest.TestCase):
    def test_fills_in_defaults_when_there_is_no_coordinator_key(self):
        config = load_config_from({"repos": {"org/repo": "/tmp/repo"}})
        self.assertEqual(config["coordinator"]["intervalSeconds"], 900)
        self.assertEqual(config["coordinator"]["maxAgents"], 3)
        self.assertTrue(config["coordinator"]["autoAdvanceMerged"])

    def test_auto_advance_merged_can_be_turned_off(self):
        config = load_config_from({"repos": {"org/repo": "/tmp/repo"}, "coordinator": {"autoAdvanceMerged": False}})
        self.assertFalse(config["coordinator"]["autoAdvanceMerged"])

    def test_keeps_an_interval_set_in_the_config(self):
        config = load_config_from({
            "repos": {"org/repo": "/tmp/repo"},
            "coordinator": {"intervalSeconds": 300},
        })
        self.assertEqual(config["coordinator"]["intervalSeconds"], 300)



class RoleCapsTest(unittest.TestCase):
    def test_fills_in_the_retro_defaults(self):
        config = load_config_from({"repos": {"org/repo": "/tmp/repo"}})
        self.assertEqual(config["coordinator"]["retro"], {"aggregateMinRetros": 15, "aggregateMinDays": 3})

    def test_bad_retro_config_exits_with_a_message(self):
        with self.assertRaises(SystemExit) as raised:
            load_config_from({"repos": {"org/repo": "/tmp/repo"}, "coordinator": {"retro": {"repo": "org/other"}}})
        self.assertIn("not in repos", str(raised.exception))

    def test_no_role_caps_by_default(self):
        config = load_config_from({"repos": {"org/repo": "/tmp/repo"}})
        self.assertEqual(config["coordinator"]["maxAgentsByRole"], {})

    def test_keeps_good_role_caps(self):
        caps = {"intake": 3, "plan": 2, "implement": 2, "review": 9, "retro": 1}
        config = load_config_from({"repos": {"org/repo": "/tmp/repo"}, "coordinator": {"maxAgentsByRole": caps}})
        self.assertEqual(config["coordinator"]["maxAgentsByRole"], caps)

    def test_bad_role_caps_exit_with_a_message(self):
        for caps, words in [({"deploy": 1}, "unknown role"), ({"plan": 0}, "1 or more"), ({"plan": -1}, "1 or more"),
                            ({"plan": True}, "1 or more"), ({"plan": "2"}, "1 or more"), ({"plan": 1.5}, "1 or more"),
                            ({"retro": 0}, "1 or more"),
                            (["plan"], "must be an object")]:
            with self.assertRaises(SystemExit, msg=str(caps)) as raised:
                load_config_from({"repos": {"org/repo": "/tmp/repo"}, "coordinator": {"maxAgentsByRole": caps}})
            self.assertIn("maxAgentsByRole", str(raised.exception))
            self.assertIn(words, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
