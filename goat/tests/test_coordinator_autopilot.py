"""Tests for the autopilot in goat/coordinator.py: QA and project manager launches, finished runs, quiet gates, and the
QA queue after a merge. GitHub is the FakeClient; no process is started."""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from goat import autopilot, coordinator
from goat import board as fb
from goat.tests.fakes import REPO, FakeClient, raw_item

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
PATH = "/work/app"


def config(**pilot):
    found = {"repos": {REPO: PATH, "me/other": "/work/other"},
             "coordinator": {"maxAgents": 4, "maxAgentsPerRepo": 3, "maxAgentsByRole": {}},
             "owner": {"name": "Alex"}}
    found["autopilot"] = autopilot.settings(dict(found, autopilot=dict({"repos": [REPO]}, **pilot)))
    return found


def items_of(client):
    return [fb.GoatItem.from_github(raw) for raw in client.board_items()]


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient(
            raw_item(9, stage="staging", goat_status=""),
            raw_item(41, stage="discovery", goat_status="", assignees=(), priority="next"),
            raw_item(31, stage="implement", goat_status="ready"),
        )
        self.items = items_of(self.client)
        self.boards = [(PATH, {}, self.client, {"repo": REPO}, self.items)]
        self.state = {"runs": [], "attempts": {}}

    def launches(self, cfg=None, free_slots=4, running_items=(), per_repo=None, per_role=None):
        return coordinator.autopilot_launches(cfg or config(), self.state, self.boards, "me", set(running_items),
                                              free_slots, per_repo or {}, per_role or {}, NOW)

    def test_starts_qa_for_a_merged_issue_and_a_pm_when_there_is_room(self):
        autopilot.queue_qa(self.state, REPO, PATH, self.items[0])
        # QA counts as in flight: with #31 and QA for #9 the pipeline of 2 is full, so no PM yet.
        launches, skipped = self.launches()
        self.assertEqual([launch["role"] for launch in launches], ["qa"])
        self.assertEqual(launches[0]["item"], "9")
        self.assertEqual(launches[0]["env"], {"GOAT_AUTOPILOT": "1"})
        launches, _ = self.launches(cfg=config(inFlight=3))
        self.assertEqual([launch["role"] for launch in launches], ["qa", "pm"])
        pm = launches[1]
        self.assertEqual((pm["item"], pm["ref"], pm["model"], pm["repo"]), (coordinator.pm_run_item(REPO), "", "opus", REPO))

    def test_no_pm_on_the_same_board_twice(self):
        launches, _ = self.launches()
        self.assertEqual([launch["role"] for launch in launches], ["pm"])
        self.state["attempts"][launches[0]["attemptKey"]] = 1
        self.assertEqual(self.launches()[0], [])

    def test_no_pm_while_one_runs_when_the_day_is_used_up_or_nothing_is_ready(self):
        self.assertEqual(self.launches(running_items={coordinator.pm_run_item(REPO)})[0], [])
        self.state["autopilot"] = {"starts": {"2026-11-12": 10}}
        self.assertEqual(self.launches()[0], [])
        self.state["autopilot"] = {}
        self.client.items[41]["labels"] = ["stage:discovery"]
        self.client.items[41]["assignees"] = ["you"]
        self.assertEqual(self.launches()[0], [])

    def test_slot_limits(self):
        autopilot.queue_qa(self.state, REPO, PATH, self.items[0])
        launches, skipped = self.launches(cfg=config(inFlight=3), free_slots=1)
        self.assertEqual([launch["role"] for launch in launches], ["qa"])
        self.assertEqual(skipped, [("project manager for " + REPO, "all agent slots are in use")])
        launches, skipped = self.launches(per_role={"qa": 1})
        self.assertEqual(skipped[0], ("QA #9 Issue 9", "all qa slots are in use"))

    def test_qa_queued_this_turn_waits_for_the_next_read(self):
        # The merge moved #9 to staging during this turn, but this turn's read still shows it in review.
        autopilot.queue_qa(self.state, REPO, PATH, self.items[0])
        self.items[0].status = "review"
        launches, _ = self.launches()
        self.assertNotIn("qa", [launch["role"] for launch in launches])
        self.assertEqual(list(autopilot.qa_queue(self.state)), [autopilot.qa_key(REPO, "9")])

    def test_qa_is_dropped_when_a_person_closed_the_issue(self):
        autopilot.queue_qa(self.state, REPO, PATH, self.items[0])
        self.items[0].status = fb.DONE_STATUS
        launches, _ = self.launches()
        self.assertNotIn("qa", [launch["role"] for launch in launches])
        self.assertEqual(autopilot.qa_queue(self.state), {})

    def test_other_repos_get_nothing(self):
        cfg = config()
        cfg["autopilot"]["repos"] = []
        self.assertEqual(self.launches(cfg=cfg), ([], []))


class LogPathTests(unittest.TestCase):
    def test_the_pm_log_lands_in_the_log_folder(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        launch = {"item": coordinator.pm_run_item("me/app"), "label": "PM", "role": "pm", "model": "opus",
                  "ref": "", "repoPath": PATH}
        with mock.patch.object(coordinator, "LOG_DIR", folder), \
                mock.patch.object(coordinator.subprocess, "Popen", return_value=mock.Mock(pid=4242)):
            run = coordinator.start_agent(launch, folder)
        self.assertEqual(os.path.dirname(run["log"]), folder)
        self.assertTrue(run["log"].endswith("-pm-me__app-pm.json"))
        self.assertTrue(os.path.exists(run["log"]))

    def test_a_slash_in_any_item_is_replaced(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder)
        launch = {"item": "pm:me/app", "label": "PM", "role": "pm", "model": "opus", "ref": "", "repoPath": PATH}
        with mock.patch.object(coordinator, "LOG_DIR", folder), \
                mock.patch.object(coordinator.subprocess, "Popen", return_value=mock.Mock(pid=4243)):
            run = coordinator.start_agent(launch, folder)
        self.assertEqual(os.path.dirname(run["log"]), folder)


class MarkAndQuietTests(unittest.TestCase):
    def test_stage_launches_in_autopilot_repos_are_marked(self):
        launches = [{"repoPath": PATH, "role": "plan"}, {"repoPath": "/work/other", "role": "plan"}]
        coordinator.mark_autopilot(config(), launches)
        self.assertEqual(launches[0]["env"], {"GOAT_AUTOPILOT": "1"})
        self.assertEqual(launches[0]["repo"], REPO)
        self.assertNotIn("env", launches[1])

    def test_gates_the_autopilot_handles_are_not_announced(self):
        def item(number, status, comments=None):
            return fb.GoatItem.from_github(raw_item(number, stage="review", goat_status=status, comments=comments))

        held = item(4, "merge-review", [{"id": 1, "created_at": "x", "author": "me", "body": autopilot.HOLD_MARKER}])
        candidates = [(PATH, item(1, "tier-review")), (PATH, item(2, "plan-review")), (PATH, item(3, "merge-review")),
                      (PATH, held), (PATH, item(5, "answer-question")), ("/work/other", item(6, "tier-review"))]
        kept = coordinator.autopilot_quiet(config(), candidates)
        self.assertEqual([entry[1].id for entry in kept], ["4", "5", "6"])


class FinishTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder)
        self.client = FakeClient(raw_item(9, stage="staging", goat_status=""),
                                 raw_item(41, stage="discovery", goat_status="", assignees=()))
        self.state = {"runs": [], "attempts": {}}
        patches = [mock.patch.object(coordinator, "GitHubClient", lambda repo: self.client),
                   mock.patch.object(coordinator, "load_shared", lambda: {}),
                   mock.patch.object(coordinator, "log", lambda message: None),
                   mock.patch.object(autopilot, "PICKS_DIR", self.folder)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_red_ci_is_announced_once_per_run(self):
        sent = []
        client = FakeClient(raw_item(9, stage="review", goat_status="merge-review", pr_url="u", comments=[]))
        client.default = "dev"
        client.ci = ("fail", "https://ci/run/7")
        client.open_prs = [{"number": 109, "url": "u", "branch": "goat/9-a", "base": "dev"}]
        boards = [(PATH, {}, client, {"repo": REPO}, items_of(client))]
        with mock.patch.object(coordinator, "say", lambda webhook, message, what: sent.append(message[1])):
            for _ in range(2):
                coordinator.run_autopilot(config(), self.state, boards, "me", NOW, False, webhook=object())
        self.assertEqual(len(sent), 1)
        self.assertIn("stopped merging", sent[0])
        self.assertEqual(client.merged, [])

    def test_qa_run_closes_the_issue(self):
        autopilot.queue_qa(self.state, REPO, PATH, items_of(self.client)[0])
        log_path = os.path.join(self.folder, "qa.json")
        with open(log_path, "w") as f:
            json.dump({"subtype": "success", "is_error": False, "result": "QA result: pass"}, f)
        run = {"role": "qa", "item": "9", "repo": REPO, "log": log_path, "label": "#9 x", "url": ""}
        self.assertFalse(coordinator.finish_autopilot_runs(config(), self.state, [run], dry_run=False))
        self.assertEqual(self.client.closed, [9])
        self.assertEqual(autopilot.qa_queue(self.state), {})

    def test_pm_run_starts_its_picks(self):
        autopilot.save_pick(REPO, 41, "next in M2", NOW, self.folder)
        run = {"role": "pm", "item": coordinator.pm_run_item(REPO), "repo": REPO, "log": "", "label": "PM", "url": ""}
        self.assertTrue(coordinator.finish_autopilot_runs(config(), self.state, [run], dry_run=False))
        self.assertEqual(self.client.labels_of(41), ["goat:ready", "stage:intake"])

    def test_stage_runs_and_dry_runs_are_ignored(self):
        run = {"role": "plan", "item": "9", "repo": REPO, "log": ""}
        self.assertFalse(coordinator.finish_autopilot_runs(config(), self.state, [run], dry_run=False))
        self.assertFalse(coordinator.finish_autopilot_runs(config(), self.state, [dict(run, role="pm")], dry_run=True))
        self.assertEqual(self.client.writes, [])


class MergedTests(unittest.TestCase):
    def test_a_merge_in_an_autopilot_repo_queues_qa(self):
        client = FakeClient(raw_item(9, stage="review", goat_status="merge-review", pr_url="https://x/pull/109"))
        client.merged_prs = [{"number": 109, "url": "https://x/pull/109", "branch": "goat/9-a", "base": "main",
                              "merge_commit": "abc", "mergedAt": "x"}]
        state = {"runs": [], "attempts": {}}
        boards = [(PATH, {}, client, {"repo": REPO}, items_of(client))]
        with mock.patch.object(coordinator, "log", lambda message: None):
            coordinator.advance_merged_boards(boards, "me", False, None, "", config(), state)
        self.assertIn("stage:staging", client.labels_of(9))
        self.assertIn("QA agent tests the merged branch next", client.comment_bodies(9)[-1])
        self.assertEqual(list(autopilot.qa_queue(state)), [autopilot.qa_key(REPO, "9")])

    def test_with_qa_off_the_issue_is_closed_at_once(self):
        client = FakeClient(raw_item(9, stage="review", goat_status="merge-review", pr_url="https://x/pull/109"))
        client.merged_prs = [{"number": 109, "url": "https://x/pull/109", "branch": "goat/9-a", "base": "main",
                              "merge_commit": "abc", "mergedAt": "x"}]
        state = {"runs": [], "attempts": {}}
        boards = [(PATH, {}, client, {"repo": REPO}, items_of(client))]
        with mock.patch.object(coordinator, "log", lambda message: None):
            coordinator.advance_merged_boards(boards, "me", False, None, "", config(qa=False), state)
        self.assertEqual(client.closed, [9])


class ConfigTests(unittest.TestCase):
    def test_role_caps_accept_qa_and_pm(self):
        coordinator.check_role_caps({"qa": 1, "pm": 1})

    def test_a_bad_autopilot_section_stops_the_coordinator(self):
        bad = {"repos": {REPO: PATH}, "autopilot": {"repos": ["me/unknown"]}}
        with self.assertRaises(SystemExit) as stop:
            coordinator.fill_coordinator_defaults(bad)
        self.assertIn("not in \"repos\"", str(stop.exception))


if __name__ == "__main__":
    unittest.main()
