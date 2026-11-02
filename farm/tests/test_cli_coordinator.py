"""Tests for the coordinator commands in farm/cli.py: `run`, `status` and `stop`, and their flags."""

import argparse
import io
import json
import unittest
from contextlib import redirect_stderr
from unittest import mock

from farm import cli, coordinator


class ArgumentTypesTest(unittest.TestCase):
    def test_positive_int(self):
        self.assertEqual(cli.positive_int("5"), 5)
        for bad in ["0", "-1", "abc", "1.5"]:
            with self.assertRaises(argparse.ArgumentTypeError, msg=bad):
                cli.positive_int(bad)

    def test_non_negative_int(self):
        self.assertEqual(cli.non_negative_int("5"), 5)
        self.assertEqual(cli.non_negative_int("0"), 0)
        for bad in ["-1", "abc", "1.5"]:
            with self.assertRaises(argparse.ArgumentTypeError, msg=bad):
                cli.non_negative_int(bad)


class CoordinatorCommandsTest(unittest.TestCase):
    def run_main(self, argv, command="command_run"):
        """Run cli.main with the coordinator command mocked. Returns the args it got."""
        with mock.patch.object(cli.coordinator, command, return_value=None) as handler:
            self.assertEqual(cli.main(argv), 0)
        handler.assert_called_once()
        return handler.call_args.args[0]

    def test_run_with_all_flags(self):
        args = self.run_main(["run", "--once", "--loop-count", "5", "--loop-interval", "300",
                              "--delay", "60", "--dry-run"])
        self.assertTrue(args.once)
        self.assertEqual(args.loop_count, 5)
        self.assertEqual(args.loop_interval, 300)
        self.assertEqual(args.delay, 60)
        self.assertTrue(args.dry_run)

    def test_run_defaults(self):
        args = self.run_main(["run"])
        self.assertFalse(args.once)
        self.assertIsNone(args.loop_count)
        self.assertIsNone(args.loop_interval)
        self.assertEqual(args.delay, 0)
        self.assertFalse(args.dry_run)

    def test_run_rejects_bad_numbers(self):
        for argv in (["run", "--loop-count", "0"], ["run", "--delay", "-1"], ["run", "--loop-interval", "-1"]):
            with self.assertRaises(SystemExit) as raised, redirect_stderr(io.StringIO()):
                cli.main(argv)
            self.assertEqual(raised.exception.code, 2, msg=argv)

    def test_status_and_stop(self):
        self.run_main(["status"], command="command_status")
        self.run_main(["stop"], command="command_stop")

    def test_coordinator_commands_do_not_read_the_current_folder_env(self):
        # The coordinator reads each configured repo's .env itself, from any folder.
        with mock.patch.object(cli, "project_env", side_effect=AssertionError("project_env called")) as project_env:
            self.run_main(["run", "--once"])
            self.run_main(["status"], command="command_status")
            self.run_main(["stop"], command="command_stop")
        project_env.assert_not_called()


class AgentSettingsTest(unittest.TestCase):
    def test_headless_agents_cannot_start_or_stop_the_coordinator(self):
        # Read through the coordinator's own path, so a wrong path fails here too.
        with open(coordinator.AGENT_SETTINGS) as f:
            deny = json.load(f)["permissions"]["deny"]
        for rule in ("Bash(python3 */farm.py run*)", "Bash(python3 */farm.py stop*)",
                     "Bash(python3 farm.py run*)", "Bash(python3 farm.py stop*)"):
            self.assertIn(rule, deny)

    def test_headless_agents_cannot_update_the_farm_or_push_tags(self):
        with open(coordinator.AGENT_SETTINGS) as f:
            deny = json.load(f)["permissions"]["deny"]
        for rule in ("Bash(python3 */farm.py update*)", "Bash(python3 farm.py update*)",
                     "Bash(git push --tags*)", "Bash(git push * --tags*)"):
            self.assertIn(rule, deny)

    def test_headless_agents_cannot_merge_stacks_or_change_extensions(self):
        with open(coordinator.AGENT_SETTINGS) as f:
            deny = json.load(f)["permissions"]["deny"]
        for rule in ("Bash(gh stack merge*)", "Bash(gh stack modify*)", "Bash(gh stack unstack*)",
                     "Bash(gh extension *)", "Bash(gh extensions *)", "Bash(gh ext *)"):
            self.assertIn(rule, deny)


if __name__ == "__main__":
    unittest.main()
