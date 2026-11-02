"""Tests for farm.merge (a merged PR moves its issue to staging) and farm.setup (setup-repo)."""

import unittest
from datetime import datetime, timedelta, timezone

from farm import board as fb
from farm import merge, setup
from farm.tests.fakes import FakeClient, raw_item

NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)
PR_URL = "https://github.com/me/app/pull/9"


def items_of(client):
    return [fb.FarmItem.from_github(raw) for raw in client.board_items()]


def at_merge_review(number="5", **kwargs):
    return raw_item(number, stage="review", farm_status="merge-review", pr_url=PR_URL, **kwargs)


class BranchTests(unittest.TestCase):
    def test_branch_item_id(self):
        self.assertEqual(merge.branch_item_id("farm/5-login-fix"), "5")
        self.assertEqual(merge.branch_item_id("farm/12-2-api"), "12")
        self.assertEqual(merge.branch_item_id("farm/7"), "7")
        for name in ("main", "feature/5-x", "farm/x-5", "farm/", None, "xfarm/5-a"):
            self.assertIsNone(merge.branch_item_id(name), name)


class AdvanceMergedTests(unittest.TestCase):
    def merged(self, number=9, branch="farm/5-login-fix", url=PR_URL):
        return {"number": number, "url": url, "branch": branch, "mergedAt": "t"}

    def test_merged_pr_moves_the_issue_to_staging(self):
        client = FakeClient(at_merge_review())
        client.merged_prs = [self.merged()]
        lines = merge.advance_merged(client, items_of(client), "me", NOW)
        self.assertEqual(lines, ["Moved #5 to stage:staging: its PR is merged."])
        self.assertEqual(client.labels_of(5), ["stage:staging"])
        self.assertIn("PR is merged", client.comment_bodies(5)[-1])

    def test_nothing_happens_without_a_merged_pr(self):
        client = FakeClient(at_merge_review())
        self.assertEqual(merge.advance_merged(client, items_of(client), "me", NOW), [])
        self.assertEqual(client.writes, [])

    def test_an_open_stack_part_blocks_the_move(self):
        client = FakeClient(at_merge_review())
        client.merged_prs = [self.merged()]
        client.open_prs = [{"number": 10, "url": "u", "branch": "farm/5-2-api", "mergedAt": None}]
        self.assertEqual(merge.advance_merged(client, items_of(client), "me", NOW), [])

    def test_the_bottom_pr_must_be_the_merged_one(self):
        client = FakeClient(at_merge_review())
        client.merged_prs = [self.merged(url="https://github.com/me/app/pull/12")]
        self.assertEqual(merge.advance_merged(client, items_of(client), "me", NOW), [])

    def test_other_issues_prs_and_other_states_are_ignored(self):
        client = FakeClient(at_merge_review("5"), raw_item("6", stage="review", farm_status="ready"),
                            at_merge_review("7", assignees=("you",)))
        client.merged_prs = [self.merged(branch="farm/6-x"), self.merged(branch="farm/7-x")]
        self.assertEqual(merge.advance_merged(client, items_of(client), "me", NOW), [])

    def test_a_live_claim_blocks_it(self):
        future = fb.format_date(NOW + timedelta(minutes=5))
        client = FakeClient(at_merge_review(claim="review|x", expires=future))
        client.merged_prs = [self.merged()]
        self.assertEqual(merge.advance_merged(client, items_of(client), "me", NOW), [])

    def test_dry_run_writes_nothing(self):
        client = FakeClient(at_merge_review())
        client.merged_prs = [self.merged()]
        lines = merge.advance_merged(client, items_of(client), "me", NOW, dry_run=True)
        self.assertEqual(lines, ["Would move #5 to stage:staging: its PR is merged."])
        self.assertEqual(client.writes, [])

    def test_does_not_list_prs_when_nothing_waits_at_merge_review(self):
        client = FakeClient(raw_item("5", stage="plan", farm_status="ready"))
        client.pull_requests = None  # a call would raise TypeError
        self.assertEqual(merge.advance_merged(client, items_of(client), "me", NOW), [])


class SetupRepoTests(unittest.TestCase):
    def test_creates_every_missing_label_and_is_safe_to_repeat(self):
        client = FakeClient()
        client.labels = {}
        lines, problems = setup.setup_repo(client, "me/app")
        self.assertEqual(problems, [])
        self.assertEqual(len(client.labels), len(fb.all_labels()))
        self.assertIn("{} created".format(len(fb.all_labels())), lines[0])
        lines, _ = setup.setup_repo(client, "me/app")
        self.assertIn("0 created", lines[0])

    def test_check_only_reports_and_changes_nothing(self):
        client = FakeClient()
        del client.labels["farm:working"]
        lines, problems = setup.setup_repo(client, "me/app", check_only=True)
        self.assertEqual(problems, ["No label 'farm:working'."])
        self.assertFalse(any(kind == "ensure_label" for kind, _, _ in client.writes))

    def test_milestone_and_env_hint(self):
        client = FakeClient()
        lines, _ = setup.setup_repo(client, "me/app", milestone="v0.1")
        self.assertIn("Milestone 'v0.1': created.", lines)
        self.assertIn("FARM_GITHUB_REPO=me/app", lines)
        self.assertIn("v0.1", client.milestones)


if __name__ == "__main__":
    unittest.main()
