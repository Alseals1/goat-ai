"""Tests for `goat.py backlog` and `goat.py pick`, the project manager's commands (goat/autopilot.py)."""

import json
import os
import unittest
from unittest import mock

from goat import autopilot, cli, coordinator, exit_codes
from goat.tests.fakes import REPO, FakeClient, raw_item
from goat.tests.test_cli_output import CliTestCase


class BacklogAndPickTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.picks = os.path.join(self.tmp.name, "picks")
        self.config = os.path.join(self.tmp.name, "config.json")
        self.write_config(on=True)
        for patch in (mock.patch.object(autopilot, "PICKS_DIR", self.picks),
                      mock.patch.object(coordinator, "CONFIG_FILE", self.config),
                      mock.patch.object(coordinator, "STATE_FILE", os.path.join(self.tmp.name, "state.json"))):
            patch.start()
            self.addCleanup(patch.stop)
        self.client = FakeClient(
            raw_item(41, stage="discovery", goat_status="", assignees=(), priority="next", milestone="M2"),
            raw_item(42, stage="discovery", goat_status="", assignees=(), body="Depends on: #31"),
            raw_item(31, stage="implement", goat_status="ready", title="Renderer host"),
        )

    def write_config(self, on):
        with open(self.config, "w") as f:
            json.dump({"repos": {REPO: "/work/app"}, "autopilot": {"repos": [REPO] if on else []}}, f)

    def test_backlog_json(self):
        code, out, _ = self.run_cli(["backlog", "--json"], self.client)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual((data["repo"], data["autopilot"], data["slots"]), (REPO, True, 1))
        self.assertEqual([entry["number"] for entry in data["inFlight"]], [31])
        self.assertEqual([entry["number"] for entry in data["ready"]], [41])
        self.assertEqual(data["blocked"], [{"number": 42, "title": "Issue 42", "priority": "",
                                            "reason": "it depends on #31, still open"}])

    def test_backlog_text(self):
        code, out, _ = self.run_cli(["backlog"], self.client)
        self.assertEqual(code, 0)
        self.assertIn("autopilot on. Free slots: 1.", out)
        self.assertIn("- #41 Issue 41 (prio:next, milestone: M2)", out)
        self.assertIn("- #42 Issue 42: it depends on #31, still open", out)

    def test_pick_records_a_ready_issue_once_and_then_the_slots_run_out(self):
        code, out, _ = self.run_cli(["pick", "41", "--why", "next in M2"], self.client)
        self.assertEqual(code, 0, out)
        self.assertEqual(autopilot.read_picks(REPO, self.picks)[0]["why"], "next in M2")
        # Picking it again replaces the reason; the slot is not counted twice.
        code, _, _ = self.run_cli(["pick", "#41", "--why", "first in M2"], self.client)
        self.assertEqual(code, 0)
        self.assertEqual(len(autopilot.read_picks(REPO, self.picks)), 1)
        self.client.items[43] = raw_item(43, stage="discovery", goat_status="", assignees=())
        code, _, err = self.run_cli(["pick", "43", "--why", "x"], self.client)
        self.assertEqual(code, exit_codes.GENERAL)
        self.assertIn("No free slot", err)
        # Nothing reached GitHub.
        self.assertEqual(self.client.writes, [])

    def test_pick_refuses_blocked_issues_and_repos_without_autopilot(self):
        code, _, err = self.run_cli(["pick", "42", "--why", "x"], self.client)
        self.assertEqual(code, exit_codes.GENERAL)
        self.assertIn("#42 cannot be started: it depends on #31, still open.", err)
        code, _, err = self.run_cli(["pick", "31", "--why", "x"], self.client)
        self.assertIn("not an open backlog issue", err)
        self.write_config(on=False)
        code, _, err = self.run_cli(["pick", "41", "--why", "x"], self.client)
        self.assertIn("does not run on autopilot", err)
        self.assertEqual(autopilot.read_picks(REPO, self.picks), [])


if __name__ == "__main__":
    unittest.main()
