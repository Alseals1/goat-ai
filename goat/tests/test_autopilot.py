"""Tests for goat/autopilot.py: settings, dependencies, gate approvals, merges, the backlog, picks and QA."""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

from goat import autopilot
from goat import board as fb
from goat.tests.fakes import REPO, FakeClient, raw_item

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
SHARED = {"agents": {"dispatcher": {"name": "Dex"}, "pm": {"name": "Morgan"}, "qa": {"name": "Quinn"}}}
SETTINGS = autopilot.settings({"repos": {REPO: "~/app"}, "autopilot": {"repos": [REPO]}})


def items_of(client):
    return [fb.GoatItem.from_github(raw) for raw in client.board_items()]


def review_handoff():
    return {"id": 1, "created_at": "2026-11-12T10:00:00Z", "author": "me",
            "body": "**🤖 Priya · Review agent · handoff · G.O.A.T. 0.1.0**\n\nReady."}


def merge_review_item(number, pr_url=None, comments=None):
    return raw_item(number, stage="review", goat_status="merge-review", size="M",
                    pr_url=pr_url or "https://github.com/me/app/pull/{}".format(100 + number),
                    comments=[review_handoff()] + list(comments or []))


def pr(number, branch, base="dev"):
    return {"number": number, "url": "https://github.com/me/app/pull/{}".format(number), "branch": branch,
            "base": base, "merge_commit": None, "mergedAt": None}


def status(number, draft=False, mergeable="MERGEABLE", checks=None, base="dev"):
    return {"number": number, "url": "https://github.com/me/app/pull/{}".format(number), "state": "OPEN",
            "draft": draft, "mergeable": mergeable, "base": base, "branch": "goat/x",
            "checks": [{"__typename": "CheckRun", "name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}]
            if checks is None else checks}


class SettingsTests(unittest.TestCase):
    def test_off_by_default(self):
        found = autopilot.settings({"repos": {REPO: "~/app"}})
        self.assertEqual(found["repos"], [])
        self.assertFalse(autopilot.is_on(found, REPO))
        self.assertEqual(autopilot.text_line(found), "")

    def test_defaults_filled_in(self):
        self.assertTrue(autopilot.is_on(SETTINGS, "ME/App"))
        self.assertEqual((SETTINGS["inFlight"], SETTINGS["maxStartsPerDay"], SETTINGS["mergeMethod"], SETTINGS["qa"]),
                         (2, 10, "squash", True))
        self.assertIn("Autopilot on for me/app", autopilot.text_line(SETTINGS))

    def test_bad_settings_are_refused(self):
        repos = {REPO: "~/app"}
        for bad in ({"repos": ["other/repo"]}, {"repos": "me/app"}, {"repos": [REPO], "mergeMethod": "octopus"},
                    {"repos": [REPO], "inFlight": -1}, {"repos": [REPO], "maxStartsPerDay": True},
                    {"repos": [REPO], "qa": "yes"}, ["me/app"]):
            with self.assertRaises(autopilot.SettingsError, msg=bad):
                autopilot.settings({"repos": repos, "autopilot": bad})


class DependencyTests(unittest.TestCase):
    def test_reads_the_depends_on_line(self):
        body = ("- **Milestone:** M1\n- **Depends on:** #29, #30 (all must be closed before work starts)\n"
                "- **Read first:** #16, docs\n\nAfter #31 lands, also check #32.")
        self.assertEqual(autopilot.dependencies(body), ["29", "30"])

    def test_nothing_and_no_line(self):
        self.assertEqual(autopilot.dependencies("- **Depends on:** nothing\nSee #4."), [])
        self.assertEqual(autopilot.dependencies("Fix the bug. Related to #4."), [])
        self.assertEqual(autopilot.dependencies(None), [])

    def test_open_dependencies_uses_the_turn_read_then_github(self):
        client = FakeClient(raw_item(29, stage="staging", goat_status="", state="closed"),
                            raw_item(30, stage="implement", goat_status="working"),
                            raw_item(5, stage="", goat_status="", state="closed"))
        states = autopilot.IssueStates(client, items_of(client))
        self.assertEqual(autopilot.open_dependencies("Depends on: #29, #30, #5, #77", states), ["30", "77"])


class GateTests(unittest.TestCase):
    def test_approves_the_size_gate(self):
        client = FakeClient(raw_item(7, stage="intake", goat_status="tier-review", size="M"))
        items = items_of(client)
        lines = autopilot.approve_gates(client, items, "me", NOW, autopilot.IssueStates(client, items), SHARED)
        self.assertEqual(client.labels_of(7), ["goat:approved", "size:M", "stage:intake"])
        self.assertEqual(items[0].goat_status, "approved")
        self.assertIn(autopilot.APPROVED_MARKER, client.comment_bodies(7)[-1])
        self.assertEqual(lines, ["Approved the size (M) of #7."])

    def test_plan_gate_waits_for_open_dependencies(self):
        client = FakeClient(raw_item(32, stage="plan", goat_status="plan-review", body="Depends on: #29, #30"),
                            raw_item(29, stage="staging", goat_status="", state="closed"),
                            raw_item(30, stage="review", goat_status="merge-review"))
        items = items_of(client)
        lines = autopilot.approve_gates(client, items, "me", NOW, autopilot.IssueStates(client, items), SHARED)
        self.assertIn("goat:plan-review", client.labels_of(32))
        self.assertEqual(lines, ["#32's plan waits for #30 to close."])

    def test_plan_gate_approved_once_dependencies_close(self):
        client = FakeClient(raw_item(32, stage="plan", goat_status="plan-review", body="Depends on: #29"),
                            raw_item(29, stage="staging", goat_status="", state="closed"))
        items = items_of(client)
        autopilot.approve_gates(client, items, "me", NOW, autopilot.IssueStates(client, items), SHARED)
        self.assertIn("goat:approved", client.labels_of(32))

    def test_leaves_other_peoples_issues_and_dry_runs_alone(self):
        client = FakeClient(raw_item(1, stage="intake", goat_status="tier-review", assignees=("you",)),
                            raw_item(3, stage="intake", goat_status="tier-review"))
        items = items_of(client)
        lines = autopilot.approve_gates(client, items, "me", NOW, autopilot.IssueStates(client, items), SHARED,
                                        dry_run=True)
        self.assertEqual(lines, ["Would approve the size (as proposed) of #3."])
        self.assertEqual([write for write in client.writes if write[0] == "labels"], [])


class ChecksTests(unittest.TestCase):
    def test_verdicts(self):
        run = lambda status, conclusion=None, name="test": {"__typename": "CheckRun", "name": name, "status": status,
                                                            "conclusion": conclusion}
        self.assertEqual(autopilot.checks_verdict([]), ("none", []))
        self.assertEqual(autopilot.checks_verdict([run("COMPLETED", "SUCCESS"), run("COMPLETED", "SKIPPED")]),
                         ("pass", []))
        self.assertEqual(autopilot.checks_verdict([run("IN_PROGRESS")]), ("pending", []))
        self.assertEqual(autopilot.checks_verdict([run("COMPLETED", "FAILURE", "build"), run("QUEUED")]),
                         ("fail", ["build"]))
        self.assertEqual(autopilot.checks_verdict([{"__typename": "StatusContext", "context": "ci", "state": "ERROR"}]),
                         ("fail", ["ci"]))
        self.assertEqual(autopilot.checks_verdict([{"__typename": "StatusContext", "context": "ci", "state": "PENDING"}]),
                         ("pending", []))


class MergeTests(unittest.TestCase):
    def client(self, *raws):
        client = FakeClient(*raws)
        client.default = "dev"
        return client

    def merge(self, client, settings=SETTINGS):
        items = items_of(client)
        return autopilot.merge_ready(client, items, "me", NOW, settings, SHARED), items

    def test_merges_a_reviewed_pr_with_green_checks(self):
        client = self.client(merge_review_item(9))
        client.open_prs = [pr(109, "goat/9-thing")]
        client.pr_statuses[109] = status(109)
        merged = []
        items = items_of(client)
        lines = autopilot.merge_ready(client, items, "me", NOW, SETTINGS, SHARED,
                                      on_merged=lambda item, part: merged.append((item.id, part["number"])))
        self.assertEqual(client.merged, [(109, "squash")])
        self.assertEqual(merged, [("9", 109)])
        self.assertIn(autopilot.MERGED_MARKER, client.comment_bodies(9)[-1])
        self.assertEqual(lines, ["Merged PR #109 of #9 into dev (squash)."])

    def test_waits_while_checks_run_or_github_is_still_checking(self):
        client = self.client(merge_review_item(9), merge_review_item(10))
        client.open_prs = [pr(109, "goat/9-a"), pr(110, "goat/10-b")]
        client.pr_statuses[109] = status(109, checks=[{"__typename": "CheckRun", "name": "t", "status": "QUEUED"}])
        client.pr_statuses[110] = status(110, mergeable="UNKNOWN")
        lines, _ = self.merge(client)
        self.assertEqual(client.merged, [])
        self.assertEqual(len(lines), 2)
        self.assertTrue(all("waits" in line for line in lines))

    def test_failing_check_sends_back_to_implement_with_the_note_first(self):
        client = self.client(merge_review_item(9))
        client.open_prs = [pr(109, "goat/9-a")]
        client.pr_statuses[109] = status(109, checks=[
            {"__typename": "CheckRun", "name": "Unit tests", "status": "COMPLETED", "conclusion": "FAILURE"}])
        lines, items = self.merge(client)
        self.assertEqual(client.merged, [])
        self.assertEqual(client.labels_of(9), ["goat:ready", "size:M", "stage:implement"])
        self.assertEqual([kind for kind, _, _ in client.writes], ["comment", "labels"])
        self.assertIn("Unit tests", client.comment_bodies(9)[-1])
        self.assertEqual(items[0].stage, "implement")
        self.assertTrue(lines[0].startswith("Sent #9 back to Implement"))

    def test_conflict_sends_back(self):
        client = self.client(merge_review_item(9))
        client.open_prs = [pr(109, "goat/9-a")]
        client.pr_statuses[109] = status(109, mergeable="CONFLICTING")
        self.merge(client)
        self.assertIn("conflicts with `dev`", client.comment_bodies(9)[-1])
        self.assertIn("stage:implement", client.labels_of(9))

    def test_holds_after_two_send_backs(self):
        earlier = [{"id": n, "created_at": "2026-11-12T09:00:00Z", "author": "me",
                    "body": autopilot.SEND_BACK_MARKER + "\nAutopilot could not merge"} for n in (2, 3)]
        client = self.client(raw_item(9, stage="review", goat_status="merge-review", pr_url="u",
                                      comments=earlier + [review_handoff()]))
        client.open_prs = [pr(109, "goat/9-a")]
        client.pr_statuses[109] = status(109, mergeable="CONFLICTING")
        lines, _ = self.merge(client)
        self.assertIn("goat:merge-review", client.labels_of(9))
        self.assertIn(autopilot.HOLD_MARKER, client.comment_bodies(9)[-1])
        self.assertTrue(lines[0].startswith("Held #9"))

    def test_a_held_issue_is_left_to_a_person_until_the_next_review(self):
        hold = {"id": 5, "created_at": "2026-11-12T11:00:00Z", "author": "me", "body": autopilot.HOLD_MARKER + "\nx"}
        client = self.client(merge_review_item(9, comments=[hold]))
        client.open_prs = [pr(109, "goat/9-a")]
        client.pr_statuses[109] = status(109)
        lines, items = self.merge(client)
        self.assertEqual((lines, client.merged), ([], []))
        self.assertTrue(autopilot.is_held(items[0]))
        # A new review round after the hold makes it the autopilot's again.
        self.assertFalse(autopilot.is_held(fb.GoatItem.from_github(
            raw_item(9, stage="review", goat_status="merge-review", comments=[hold, review_handoff()]))))

    def test_drafts_and_the_main_branch_are_held(self):
        client = self.client(merge_review_item(9))
        client.open_prs = [pr(109, "goat/9-a")]
        client.pr_statuses[109] = status(109, draft=True)
        self.merge(client)
        self.assertIn("still a draft", client.comment_bodies(9)[-1])

        client = self.client(merge_review_item(9))
        client.default = "main"
        client.open_prs = [pr(109, "goat/9-a", base="main")]
        client.pr_statuses[109] = status(109)
        self.merge(client)
        self.assertEqual(client.merged, [])
        self.assertIn("never merges into `main`", client.comment_bodies(9)[-1])

    def test_a_stack_is_merged_bottom_first_with_merge_commits(self):
        client = self.client(merge_review_item(9))
        client.open_prs = [pr(119, "goat/9-part-2", base="goat/9-part-1"), pr(109, "goat/9-part-1")]
        client.pr_statuses[109] = status(109)
        client.pr_statuses[119] = status(119)
        self.merge(client)
        self.assertEqual(client.merged, [(109, "merge")])
        self.assertIn("1 more part(s)", client.comment_bodies(9)[-1])
        # Next turn: the upper part moves onto dev first, and is merged on the turn after.
        client.merged_prs = [dict(pr(109, "goat/9-part-1"), merge_commit="abc")]
        self.merge(client)
        self.assertEqual(client.retargeted, [(119, "dev")])
        self.assertEqual(client.merged, [(109, "merge")])
        self.merge(client)
        self.assertEqual(client.merged, [(109, "merge"), (119, "merge")])

    def test_a_pr_aimed_at_a_strange_branch_is_held(self):
        client = self.client(merge_review_item(9))
        client.open_prs = [pr(109, "goat/9-a", base="release")]
        self.merge(client)
        self.assertIn("targets `release`", client.comment_bodies(9)[-1])

    def test_a_refused_merge_is_held(self):
        client = self.client(merge_review_item(9))
        client.open_prs = [pr(109, "goat/9-a")]
        client.pr_statuses[109] = status(109)
        client.merge_error = "gh pr merge failed: review required"
        self.merge(client)
        self.assertIn("review required", client.comment_bodies(9)[-1])
        self.assertIn(autopilot.HOLD_MARKER, client.comment_bodies(9)[-1])


class BacklogTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient(
            raw_item(40, stage="discovery", goat_status="", assignees=(), priority="later", body="Depends on: #31"),
            raw_item(41, stage="discovery", goat_status="", assignees=(), priority="next", body="Depends on: nothing"),
            raw_item(42, stage="discovery", goat_status="", assignees=("you",)),
            raw_item(43, stage="discovery", goat_status="", assignees=("me",), priority="first"),
            raw_item(31, stage="implement", goat_status="working", claim="implement|s", expires="2026-11-12T13:00:00Z"),
            raw_item(30, stage="plan", goat_status="answer-question"),
            raw_item(29, stage="intake", goat_status="tier-review", assignees=("you",)),
        )

    def test_ready_and_blocked(self):
        ready, blocked = autopilot.backlog(self.client, items_of(self.client), "me")
        self.assertEqual([item.id for item in ready], ["43", "41"])
        self.assertEqual([(item.id, reason) for item, reason in blocked],
                         [("40", "it depends on #31, still open"), ("42", "it is assigned to you")])

    def test_in_flight_leaves_out_issues_waiting_on_a_person(self):
        self.assertEqual([item.id for item in autopilot.in_flight(items_of(self.client), "me")], ["31"])

    def test_room(self):
        self.assertEqual(autopilot.room(SETTINGS, 0, 0), 2)
        self.assertEqual(autopilot.room(SETTINGS, 2, 0), 0)
        self.assertEqual(autopilot.room(dict(SETTINGS, maxStartsPerDay=3), 0, 2), 1)

    def test_daily_count_keeps_only_today(self):
        state = {"autopilot": {"starts": {"2026-11-11": 9}}}
        autopilot.count_start(state, NOW)
        autopilot.count_start(state, NOW)
        self.assertEqual(state["autopilot"]["starts"], {"2026-11-12": 2})
        self.assertEqual(autopilot.starts_today(state, NOW), 2)


class PickTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder)

    def test_save_read_take(self):
        autopilot.save_pick(REPO, 41, "first in M2", NOW, self.folder)
        self.assertEqual(autopilot.save_pick(REPO, "41", "again", NOW, self.folder), 1)
        autopilot.save_pick(REPO, 43, "", NOW, self.folder)
        self.assertEqual([pick["item"] for pick in autopilot.read_picks(REPO, self.folder)], ["41", "43"])
        self.assertEqual(len(autopilot.take_picks(REPO, self.folder)), 2)
        self.assertEqual(autopilot.read_picks(REPO, self.folder), [])

    def test_a_damaged_file_reads_as_no_picks(self):
        with open(autopilot.picks_path(REPO, self.folder), "w") as f:
            f.write("{not json")
        self.assertEqual(autopilot.read_picks(REPO, self.folder), [])

    def test_apply_starts_valid_picks_within_room(self):
        client = FakeClient(
            raw_item(41, stage="discovery", goat_status="", assignees=()),
            raw_item(42, stage="discovery", goat_status="", assignees=(), body="Depends on: #31"),
            raw_item(43, stage="discovery", goat_status="", assignees=()),
            raw_item(44, stage="discovery", goat_status="", assignees=()),
            raw_item(31, stage="implement", goat_status="ready"),
        )
        for number in (42, 41, 43, 44):
            autopilot.save_pick(REPO, number, "why {}".format(number), NOW, self.folder)
        state = {}
        started = []
        lines, count = autopilot.apply_picks(client, REPO, items_of(client), "me", SETTINGS, state, SHARED, NOW,
                                             folder=self.folder, on_started=lambda item, why: started.append(item.id))
        # #31 is in flight, so there is room for one: #42 is blocked, #41 starts, #43 and #44 wait.
        self.assertEqual((count, started), (1, ["41"]))
        self.assertEqual(client.labels_of(41), ["goat:ready", "stage:intake"])
        self.assertEqual(client.items[41]["assignees"], ["me"])
        self.assertIn("Morgan · Project manager agent", client.comment_bodies(41)[-1])
        self.assertIn("why 41", client.comment_bodies(41)[-1])
        self.assertEqual(client.labels_of(42), ["stage:discovery"])
        self.assertEqual(autopilot.starts_today(state, NOW), 1)
        self.assertTrue(any("#42: it depends on #31" in line for line in lines))
        self.assertTrue(any("#43: no room" in line for line in lines))
        self.assertEqual(autopilot.read_picks(REPO, self.folder), [])

    def test_no_picks(self):
        client = FakeClient()
        self.assertEqual(autopilot.apply_picks(client, REPO, [], "me", SETTINGS, {}, SHARED, NOW, folder=self.folder),
                         (["The project manager picked nothing."], 0))


class QaTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder)
        self.client = FakeClient(raw_item(9, stage="staging", goat_status=""))
        self.state = {}
        item = items_of(self.client)[0]
        autopilot.queue_qa(self.state, REPO, "/repo", item)
        self.entry = autopilot.qa_queue(self.state)[autopilot.qa_key(REPO, "9")]

    def log(self, data):
        path = os.path.join(self.folder, "run.json")
        with open(path, "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))
        return {"log": path}

    def test_a_finished_qa_run_comments_and_closes(self):
        run = self.log({"type": "result", "subtype": "success", "is_error": False,
                        "result": "QA result: issues filed: #61\nReport: ~/.goat-ai/qa/app/2026-11-12/report.md"})
        line, closed = autopilot.finish_qa(self.client, self.entry, run, SHARED)
        self.assertTrue(closed)
        self.assertEqual(self.client.closed, [9])
        self.assertIn("QA result: issues filed: #61", self.client.comment_bodies(9)[-1])
        self.assertIn("Quinn · QA agent", self.client.comment_bodies(9)[-1])
        autopilot.prune_qa(self.state)
        self.assertEqual(autopilot.qa_queue(self.state), {})

    def test_a_failed_run_is_tried_again_then_left_to_a_person(self):
        line, closed = autopilot.finish_qa(self.client, self.entry, self.log({"is_error": True, "result": "x"}), SHARED)
        self.assertFalse(closed)
        self.assertEqual(self.entry["status"], "due")
        line, closed = autopilot.finish_qa(self.client, self.entry, self.log("not json"), SHARED)
        self.assertEqual(self.entry["status"], "failed")
        self.assertEqual(self.client.closed, [])
        self.assertIn("could not finish", self.client.comment_bodies(9)[-1])

    def test_long_reports_are_cut(self):
        text = autopilot.qa_comment("x" * (autopilot.QA_TEXT_LIMIT + 50), "/log", SHARED)
        self.assertIn("(cut;", text)


if __name__ == "__main__":
    unittest.main()
