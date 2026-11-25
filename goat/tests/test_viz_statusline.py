"""The floor view's optional plan usage status line (viz/usage_statusline.py) and its installer."""

import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

VIZ = os.path.join(os.path.dirname(__file__), "..", "..", "viz")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(VIZ, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


usage = load("usage_statusline")
install = load("install_statusline")


class UsageStatusLineTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.object(usage, "OUT_FILE", os.path.join(self.home.name, "viz", "usage.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_saves_the_windows_claude_code_sends(self):
        later = time.time() + 3600
        data = usage.update({"rate_limits": {"five_hour": {"used_percentage": 42.4, "resets_at": later},
                                             "other": {"used_percentage": 1}},
                             "cost": {"total_api_duration_ms": 10}})
        self.assertEqual(data["windows"], {"five_hour": {"used_percentage": 42.4, "resets_at": later}})
        self.assertFalse(data["noLimitsAfterResponse"])
        self.assertEqual(usage.read_saved()["windows"], data["windows"])
        self.assertTrue(usage.line(data).startswith("Usage: 5h 42% (resets in "))

    def test_an_earlier_window_stays_until_its_reset_time(self):
        usage.update({"rate_limits": {"seven_day": {"used_percentage": 10, "resets_at": time.time() + 3600},
                                      "five_hour": {"used_percentage": 90, "resets_at": time.time() - 1}}})
        data = usage.update({})
        self.assertEqual(list(data["windows"]), ["seven_day"])

    def test_an_answer_with_no_limits_means_the_account_gets_none(self):
        data = usage.update({"cost": {"total_api_duration_ms": 10}})
        self.assertTrue(data["noLimitsAfterResponse"])
        self.assertEqual(usage.line(data), "Usage: no plan limits sent for this account")
        self.assertEqual(usage.line(usage.update({})), "")


class InstallStatusLineTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.settings = os.path.join(self.home.name, "settings.json")
        patcher = mock.patch.object(install, "SETTINGS", self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, data):
        with open(self.settings, "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def read(self):
        with open(self.settings) as f:
            return json.load(f)

    def run_quiet(self, fn):
        with redirect_stdout(io.StringIO()) as out:
            fn()
        return out.getvalue()

    def test_adds_it_when_there_is_no_settings_file(self):
        self.run_quiet(install.add)
        self.assertEqual(self.read()["statusLine"], {"type": "command", "command": install.OURS})

    def test_keeps_a_status_line_already_there_and_remove_puts_it_back(self):
        self.write({"model": "opus", "statusLine": {"type": "command", "command": "echo hi"}})
        self.run_quiet(install.add)
        self.assertEqual(self.read()["statusLine"]["command"], install.OURS + " --then 'echo hi'")
        self.assertEqual(self.read()["model"], "opus")
        self.assertIn("already", self.run_quiet(install.add))
        self.run_quiet(install.remove)
        self.assertEqual(self.read()["statusLine"], {"type": "command", "command": "echo hi"})

    def test_the_software_farm_usage_line_is_wrapped_not_taken_for_this_one(self):
        farm = "python3 /elsewhere/software-farm/viz/usage_statusline.py"
        self.write({"statusLine": {"type": "command", "command": farm}})
        self.run_quiet(install.add)
        self.assertTrue(self.read()["statusLine"]["command"].startswith(install.OURS + " --then "))
        self.run_quiet(install.remove)
        self.assertEqual(self.read()["statusLine"]["command"], farm)

    def test_remove_with_nothing_before_deletes_the_key(self):
        self.run_quiet(install.add)
        self.run_quiet(install.remove)
        self.assertNotIn("statusLine", self.read())

    def test_invalid_json_changes_nothing(self):
        self.write("{not json")
        with self.assertRaises(SystemExit):
            install.add()
        with open(self.settings) as f:
            self.assertEqual(f.read(), "{not json")


if __name__ == "__main__":
    unittest.main()
