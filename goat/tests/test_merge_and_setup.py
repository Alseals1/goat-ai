"""Tests for goat.merge (a merged PR moves its issue to staging) and goat.setup (setup-repo)."""

import unittest
from datetime import datetime, timedelta, timezone

from goat import board as fb
from goat import merge, setup
from goat.tests.fakes import FakeClient, raw_item

NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)
PR_URL = "https://github.com/me/app/pull/9"


def items_of(client):
    return [fb.GoatItem.from_github(raw) for raw in client.board_items()]


def at_merge_review(number="5", **kwargs):
    return raw_item(number, stage="review", goat_status="merge-review", pr_url=PR_URL, **kwargs)


class BranchTests(unittest.TestCase):
    def test_branch_item_id(self):
        self.assertEqual(merge.branch_item_id("goat/5-login-fix"), "5")
        self.assertEqual(merge.branch_item_id("goat/12-2-api"), "12")
        self.assertEqual(merge.branch_item_id("goat/7"), "7")
        for name in ("main", "feature/5-x", "goat/x-5", "goat/", None, "xgoat/5-a"):
            self.assertIsNone(merge.branch_item_id(name), name)


class AdvanceMergedTests(unittest.TestCase):
    def merged(self, number=9, branch="goat/5-login-fix", url=PR_URL, base="main", commit="c9"):
        return {"number": number, "url": url, "branch": branch, "base": base, "merge_commit": commit, "mergedAt": "t"}

    def run_merge(self, client, dry_run=False):
        return merge.advance_merged(client, items_of(client), "me", NOW, dry_run=dry_run)

    def test_merged_pr_moves_the_issue_to_staging(self):
        client = FakeClient(at_merge_review())
        client.merged_prs = [self.merged()]
        self.assertEqual(self.run_merge(client), ["Moved #5 to stage:staging: its PR is merged."])
        self.assertEqual(client.labels_of(5), ["stage:staging"])
        self.assertIn("PR is merged", client.comment_bodies(5)[-1])

    def test_nothing_happens_without_a_merged_pr(self):
        client = FakeClient(at_merge_review())
        self.assertEqual(self.run_merge(client), [])
        self.assertEqual(client.writes, [])

    def test_an_open_stack_part_blocks_the_move(self):
        client = FakeClient(at_merge_review())
        client.merged_prs = [self.merged()]
        client.open_prs = [{"number": 10, "url": "u", "branch": "goat/5-2-api", "base": "goat/5-1-x", "mergedAt": None}]
        self.assertEqual(self.run_merge(client), [])
        self.assertEqual(client.writes, [])

    def test_the_bottom_pr_must_be_the_merged_one(self):
        client = FakeClient(at_merge_review())
        client.merged_prs = [self.merged(url="https://github.com/me/app/pull/12")]
        self.assertEqual(self.run_merge(client), [])

    def test_other_issues_prs_and_other_states_are_ignored(self):
        client = FakeClient(at_merge_review("5"), raw_item("6", stage="review", goat_status="ready"),
                            at_merge_review("7", assignees=("you",)))
        client.merged_prs = [self.merged(branch="goat/6-x"), self.merged(branch="goat/7-x")]
        self.assertEqual(self.run_merge(client), [])

    def test_a_live_claim_blocks_it(self):
        future = fb.format_date(NOW + timedelta(minutes=5))
        client = FakeClient(at_merge_review(claim="review|x", expires=future))
        client.merged_prs = [self.merged()]
        self.assertEqual(self.run_merge(client), [])

    def test_dry_run_writes_nothing(self):
        client = FakeClient(at_merge_review())
        client.merged_prs = [self.merged()]
        self.assertEqual(self.run_merge(client, dry_run=True), ["Would move #5 to stage:staging: its PR is merged."])
        self.assertEqual(client.writes, [])

    def test_does_not_list_prs_when_nothing_waits_at_merge_review(self):
        client = FakeClient(raw_item("5", stage="plan", goat_status="ready"))
        client.pull_requests = None  # a call would raise TypeError
        self.assertEqual(self.run_merge(client), [])

    # ----- stacks: the case that went wrong in the first real run -----

    def stack_where_parts_2_and_3_merged_into_their_parents(self):
        """Part 1 merged into main. Parts 2 and 3 merged into the branches below them, so they never reached main."""
        client = FakeClient(at_merge_review())
        client.merged_prs = [
            self.merged(number=17, branch="goat/5-3-seed", base="goat/5-2-access", commit="c17"),
            self.merged(number=16, branch="goat/5-2-access", base="goat/5-1-table", commit="c16"),
            self.merged(number=15, branch="goat/5-1-table", base="main", commit="c15", url=PR_URL),
        ]
        return client

    def test_a_stack_with_parts_merged_into_other_branches_is_not_finished(self):
        client = self.stack_where_parts_2_and_3_merged_into_their_parents()
        lines = self.run_merge(client)
        self.assertEqual(lines, ["Not moving #5: PR #17, #16 merged into another branch, so its work is not on main yet."])
        self.assertEqual(client.labels_of(5), ["goat:merge-review", "stage:review"])  # nothing moved

    def test_the_owner_is_told_once_how_to_finish(self):
        client = self.stack_where_parts_2_and_3_merged_into_their_parents()
        self.run_merge(client)
        comment = client.comment_bodies(5)[-1]
        self.assertIn(merge.STRANDED_MARKER, comment)
        self.assertIn("not on `main` yet", comment)
        self.assertIn("#17 (merged into `goat/5-2-access`)", comment)
        self.assertIn("Automatically delete head branches", comment)
        before = len(client.comment_bodies(5))
        self.run_merge(client)  # the next turn
        self.assertEqual(len(client.comment_bodies(5)), before)

    def test_a_dry_run_posts_no_warning(self):
        client = self.stack_where_parts_2_and_3_merged_into_their_parents()
        self.run_merge(client, dry_run=True)
        self.assertEqual(client.writes, [])

    def test_a_stack_merged_top_down_is_finished_once_the_bottom_reaches_main(self):
        # Parts 3 and 2 were merged into the branch below, then part 1 (now containing them) into main.
        client = self.stack_where_parts_2_and_3_merged_into_their_parents()
        client.commits_on_default = {"c15", "c16", "c17"}
        self.assertEqual(self.run_merge(client), ["Moved #5 to stage:staging: its PR is merged."])
        self.assertEqual(client.labels_of(5), ["stage:staging"])

    def test_a_pr_merged_into_the_wrong_branch_with_no_commit_is_not_trusted(self):
        client = FakeClient(at_merge_review())
        client.merged_prs = [self.merged(base="goat/5-1-x", commit=None)]
        self.assertTrue(self.run_merge(client)[0].startswith("Not moving #5"))


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
        del client.labels["goat:working"]
        lines, problems = setup.setup_repo(client, "me/app", check_only=True)
        self.assertEqual(problems, ["No label 'goat:working'."])
        self.assertFalse(any(kind == "ensure_label" for kind, _, _ in client.writes))

    def test_milestone_and_env_hint(self):
        client = FakeClient()
        lines, _ = setup.setup_repo(client, "me/app", milestone="v0.1")
        self.assertIn("Milestone 'v0.1': created.", lines)
        self.assertIn("GOAT_GITHUB_REPO=me/app", lines)
        self.assertIn("v0.1", client.milestones)


if __name__ == "__main__":
    unittest.main()
