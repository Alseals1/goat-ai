"""Tests for the Label Doctor (goat/doctor.py) and the `doctor` and `list --problems` commands."""

import json
import unittest
from datetime import datetime, timedelta, timezone

from goat import board as fb
from goat import doctor
from goat.tests.fakes import FakeClient, raw_item
from goat.tests.test_cli_output import CliTestCase

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def item(number=7, **fields):
    return fb.GoatItem.from_github(raw_item(number, **fields))


def codes_of(target):
    return [problem.code for problem in doctor.diagnose(target, "me", NOW)]


def at(delta):
    return fb.format_date(NOW + delta)


class HealthyIssuesTests(unittest.TestCase):
    def test_every_normal_state_has_no_problem(self):
        normal = [
            item(stage="intake", goat_status="ready"),
            item(stage="intake", goat_status="tier-review", size="M"),
            item(stage="intake", goat_status="approved"),
            item(stage="plan", goat_status="plan-review"),
            item(stage="plan", goat_status="approved"),
            item(stage="implement", goat_status="answer-question"),
            item(stage="implement", goat_status="chat"),
            item(stage="implement", goat_status="stalled"),
            item(stage="review", goat_status="merge-review"),
            item(stage="review", goat_status="working", claim="review|s1", expires=at(timedelta(minutes=5))),
            item(stage="discovery", goat_status=""),
            item(stage="staging", goat_status=""),
            item(stage="review", goat_status="done", state="closed"),
            item(stage="staging", goat_status="", state="closed"),
        ]
        for target in normal:
            self.assertEqual(doctor.diagnose(target, "me", NOW), [], msg=target.labels)

    def test_a_claim_that_ended_recently_is_left_to_the_coordinator(self):
        target = item(stage="plan", goat_status="working", claim="plan|s1", expires=at(-timedelta(minutes=20)))
        self.assertEqual(codes_of(target), [])


class FirstReleaseMistakesTests(unittest.TestCase):
    """The two slips from the first hands-on release (docs/ideas.md, idea 1)."""

    def test_stage_and_assignee_but_no_goat_label(self):
        problems = doctor.diagnose(item(stage="intake", goat_status=""), "me", NOW)
        self.assertEqual([problem.code for problem in problems], ["no-status"])
        self.assertIn("`stage:intake`", problems[0].text)
        self.assertIn("ignores it", problems[0].text)
        self.assertEqual(problems[0].fix, "Add `goat:ready` to start the intake agent.")

    def test_approved_added_but_the_stage_label_removed_instead_of_the_gate(self):
        problems = doctor.diagnose(item(stage="", goat_status="approved", labels=["goat:tier-review"]), "me", NOW)
        self.assertEqual([problem.code for problem in problems], ["no-stage", "many-statuses"])
        self.assertIn("Add `stage:intake`", problems[0].fix)
        # Never picks the approval for the person: it names both choices.
        self.assertEqual(problems[1].fix, "If you approved the gate, remove `goat:tier-review`. "
                                          "If you did not, remove `goat:approved`.")


class ProblemTests(unittest.TestCase):
    def test_two_stage_labels_suggest_the_gate_stage(self):
        target = item(stage="plan", goat_status="plan-review", labels=["stage:intake"])
        problems = doctor.diagnose(target, "me", NOW)
        self.assertEqual([problem.code for problem in problems], ["many-stages"])
        self.assertIn("probably keep `stage:plan` and remove `stage:intake`", problems[0].fix)

    def test_two_stage_labels_without_a_gate(self):
        problems = doctor.diagnose(item(stage="plan", goat_status="ready", labels=["stage:implement"]), "me", NOW)
        self.assertEqual(problems[0].fix, "Keep the stage the issue is really at and remove the others.")

    def test_no_stage_without_a_gate_lists_the_agent_stages(self):
        problems = doctor.diagnose(item(stage="", goat_status="ready"), "me", NOW)
        self.assertEqual([problem.code for problem in problems], ["no-stage"])
        self.assertIn("`stage:intake`, `stage:plan`, `stage:implement` or `stage:review`", problems[0].fix)

    def test_two_statuses_that_are_not_an_approval(self):
        problems = doctor.diagnose(item(stage="plan", goat_status="ready", labels=["goat:working"]), "me", NOW)
        self.assertEqual([problem.code for problem in problems], ["many-statuses"])
        self.assertEqual(problems[0].fix, "Keep the one you mean and remove the others.")

    def test_approved_where_no_agent_follows(self):
        review = doctor.diagnose(item(stage="review", goat_status="approved"), "me", NOW)
        self.assertEqual([problem.code for problem in review], ["approved-dead-end"])
        self.assertIn("merge the PR", review[0].fix)
        implement = doctor.diagnose(item(stage="implement", goat_status="approved"), "me", NOW)
        self.assertIn("Swap `goat:approved` for `goat:ready`", implement[0].fix)

    def test_a_goat_label_in_the_backlog_or_after_the_merge(self):
        backlog = doctor.diagnose(item(stage="discovery", goat_status="ready"), "me", NOW)
        self.assertEqual([problem.code for problem in backlog], ["backlog-status"])
        self.assertIn("swap `stage:discovery` for `stage:intake`", backlog[0].fix)
        self.assertEqual(codes_of(item(stage="staging", goat_status="ready")), ["staging-status"])

    def test_a_gate_label_at_the_wrong_stage(self):
        problems = doctor.diagnose(item(stage="intake", goat_status="plan-review"), "me", NOW)
        self.assertEqual([problem.code for problem in problems], ["gate-wrong-stage"])
        self.assertIn("swap `stage:intake` for `stage:plan`", problems[0].fix)

    def test_working_with_no_claim(self):
        self.assertEqual(codes_of(item(stage="plan", goat_status="working", comments=[])), ["working-no-claim"])

    def test_working_with_a_claim_that_ended_long_ago(self):
        target = item(stage="implement", goat_status="working", claim="implement|s1", expires=at(-timedelta(hours=3)))
        problems = doctor.diagnose(target, "me", NOW)
        self.assertEqual([problem.code for problem in problems], ["working-stale"])
        self.assertIn("ended 3 hours ago", problems[0].text)
        self.assertIn("`/goat-implement 7`", problems[0].fix)

    def test_working_is_not_judged_without_its_comments(self):
        raw = raw_item(7, stage="plan", goat_status="working")
        del raw["comments"]
        self.assertEqual(codes_of(fb.GoatItem.from_github(raw)), [])

    def test_unassigned(self):
        problems = doctor.diagnose(item(stage="intake", goat_status="ready", assignees=()), "me", NOW)
        self.assertEqual([problem.code for problem in problems], ["unassigned"])
        self.assertEqual(problems[0].fix, "Assign it to yourself (`me`).")

    def test_goat_done_on_an_open_issue(self):
        self.assertEqual(codes_of(item(stage="staging", goat_status="done")), ["done-open"])
        self.assertEqual(codes_of(item(stage="review", goat_status="done")), ["done-open"])

    def test_closed_issue_with_a_leftover_status(self):
        problems = doctor.diagnose(item(stage="review", goat_status="working", state="closed"), "me", NOW)
        self.assertEqual([problem.code for problem in problems], ["closed-leftover"])
        self.assertIn("`goat.py cleanup`", problems[0].fix)

    def test_unknown_labels(self):
        problems = doctor.diagnose(item(stage="intake", goat_status="ready", labels=["goat:aproved"]), "me", NOW)
        self.assertEqual([problem.code for problem in problems], ["unknown-label"])
        self.assertIn("`goat:aproved` is not a G.O.A.T. label", problems[0].text)


class CheckTests(unittest.TestCase):
    def test_skips_issues_assigned_to_someone_else_and_orders_open_first(self):
        items = [item(9, stage="intake", goat_status="", state="closed", labels=["goat:ready"]),
                 item(8, stage="intake", goat_status=""),
                 item(3, stage="intake", goat_status="", assignees=("ana",)),
                 item(2, stage="review", goat_status="approved"),
                 item(1, stage="intake", goat_status="ready")]
        found, checked = doctor.check(items, "me", NOW)
        self.assertEqual(checked, 4)
        self.assertEqual([target.id for target, _ in found], ["2", "8", "9"])

    def test_report_text(self):
        found, checked = doctor.check([item(8, stage="intake", goat_status="")], "me", NOW)
        lines = doctor.report_lines(found, checked)
        self.assertEqual(lines[0], "#8 Issue 8  [stage:intake]")
        self.assertEqual(lines[2], "  Fix: Add `goat:ready` to start the intake agent.")
        self.assertEqual(lines[-1], "Checked 1 issue(s), yours and unassigned: 1 problem(s) on 1 issue(s).")
        self.assertEqual(doctor.report_lines([], 5), ["Checked 5 issue(s), yours and unassigned: no problems found."])


class DoctorCommandTests(CliTestCase):
    def broken_client(self):
        return FakeClient(raw_item("1"), raw_item("2", goat_status=""),
                          raw_item("3", stage="", goat_status="approved", labels=["goat:tier-review"]),
                          raw_item("4", goat_status="", assignees=("ana",)))

    def test_text_report_exits_1_on_a_problem(self):
        code, out, _ = self.run_cli(["doctor"], self.broken_client())
        self.assertEqual(code, 1)
        self.assertIn("#2 Issue 2  [stage:intake]", out)
        self.assertIn("#3 Issue 3  [goat:tier-review, goat:approved]", out)
        self.assertNotIn("#1 ", out)
        self.assertNotIn("#4 ", out)
        self.assertIn("3 problem(s) on 2 issue(s)", out)

    def test_exits_0_when_all_is_well(self):
        code, out, _ = self.run_cli(["doctor"], FakeClient(raw_item("1")))
        self.assertEqual(code, 0)
        self.assertIn("no problems found", out)

    def test_json(self):
        code, out, _ = self.run_cli(["doctor", "--json"], self.broken_client())
        data = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual((data["version"], data["checked"], data["ok"]), ("0.1", 3, False))
        self.assertEqual([entry["id"] for entry in data["items"]], ["2", "3"])
        self.assertEqual([p["code"] for p in data["items"][1]["problems"]], ["no-stage", "many-statuses"])
        self.assertEqual(data["items"][1]["labels"], ["goat:tier-review", "goat:approved"])

    def test_reads_only(self):
        client = self.broken_client()
        self.run_cli(["doctor"], client)
        self.assertEqual(client.writes, [])

    def test_list_problems(self):
        code, out, _ = self.run_cli(["list", "--problems"], self.broken_client())
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("#2 ") and lines[0].endswith("! no goat: label"), lines[0])
        self.assertTrue(lines[1].endswith("! no stage label; 2 goat: labels"), lines[1])
        self.assertIn("Run `goat.py doctor`", out)

    def test_list_problems_mine_and_json(self):
        client = self.broken_client()
        client.items[2]["assignees"] = []
        _, out, _ = self.run_cli(["list", "--problems", "--mine", "--json"], client)
        self.assertEqual([entry["id"] for entry in json.loads(out)["items"]], ["3"])

    def test_list_problems_when_none(self):
        _, out, _ = self.run_cli(["list", "--problems"], FakeClient(raw_item("1")))
        self.assertEqual(out, "No issues with label problems.\n")


if __name__ == "__main__":
    unittest.main()
