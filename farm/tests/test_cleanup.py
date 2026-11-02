"""Tests for farm.cleanup with real temporary git repos: a bare origin, a main checkout, and farm worktrees."""

import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from farm import board as fb
from farm import cleanup
from farm.github import GitHubError
from farm.tests.fakes import BOARD, FakeClient, raw_item

NOW = datetime(2026, 10, 28, 15, 0, 0, tzinfo=timezone.utc)


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
                   cwd=cwd, check=True, capture_output=True)


def done_item(item_id="11", farm_status="merge-review", **kwargs):
    """A closed issue the farm worked on. farm_status "" means the merge check already cleared the label."""
    return raw_item(item_id, stage="staging", farm_status=farm_status, state="closed", **kwargs)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        origin = os.path.join(self.tmp, "origin.git")
        git(self.tmp, "init", "-q", "--bare", "-b", "main", origin)
        self.repo = os.path.join(self.tmp, "proj")
        git(self.tmp, "clone", "-q", origin, self.repo)
        git(self.repo, "checkout", "-q", "-b", "main")
        self.write(self.repo, "README", "hello")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "first")
        git(self.repo, "push", "-q", "-u", "origin", "main")
        # No gh calls in tests. Tests that need a merged PR patch this to True.
        patcher = mock.patch.object(cleanup, "pr_merged", return_value=False)
        self.pr_merged = patcher.start()
        self.addCleanup(patcher.stop)

    # ---------- Helpers ----------

    def write(self, folder, name, text):
        with open(os.path.join(folder, name), "w") as f:
            f.write(text)

    def worktree(self, folder_name, branch, push=True):
        """A farm worktree with one commit on its branch. Returns its path."""
        path = os.path.join(self.tmp, "proj-farm", folder_name)
        git(self.repo, "worktree", "add", "-q", "-b", branch, path, "main")
        self.write(path, "work.txt", folder_name)
        git(path, "add", ".")
        git(path, "commit", "-q", "-m", "work")
        if push:
            git(path, "push", "-q", "-u", "origin", branch)
        return path

    def branches(self):
        out = subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=self.repo,
                             capture_output=True, text=True, check=True).stdout
        return out.split()

    def run_cleanup(self, *raw_items, dry_run=False, skip_ids=(), wait_ids=(), retro_failed_ids=()):
        client = FakeClient(*raw_items)
        items = [fb.FarmItem.from_github(raw) for raw in client.board_items()]
        lines = cleanup.clean_up(self.repo, client, BOARD, items, "me", NOW, dry_run=dry_run, skip_ids=skip_ids,
                                 wait_ids=wait_ids, retro_failed_ids=retro_failed_ids)
        return lines, client

    def farm_status_writes(self, client):
        """The issues that got the label farm:done, as [(number, labels added)]."""
        return [(str(number), detail["add"]) for kind, number, detail in client.writes
                if kind == "labels" and "farm:done" in detail["add"]]

    # ---------- Tests ----------

    def test_removes_a_clean_worktree_and_a_pushed_branch(self):
        path = self.worktree("11", "farm/11-thing")
        lines, client = self.run_cleanup(done_item())
        self.assertFalse(os.path.exists(path))
        self.assertNotIn("farm/11-thing", self.branches())
        self.assertEqual(self.farm_status_writes(client), [("11", ["farm:done"])])
        self.assertIn("removed worktree", lines[0])

    def test_keeps_a_worktree_with_uncommitted_changes(self):
        path = self.worktree("11", "farm/11-thing")
        self.write(path, "new.txt", "not saved")
        lines, client = self.run_cleanup(done_item())
        self.assertTrue(os.path.exists(path))
        self.assertIn("farm/11-thing", self.branches())
        self.assertEqual(client.writes, [])
        self.assertIn("uncommitted changes", lines[0])

    def test_keeps_a_branch_with_unpushed_commits(self):
        path = self.worktree("11", "farm/11-thing", push=False)
        lines, client = self.run_cleanup(done_item())
        self.assertTrue(os.path.exists(path))
        self.assertEqual(client.writes, [])
        self.assertIn("not on a remote or in a merged PR", lines[0])

    def test_unpushed_commits_in_a_merged_pr_are_safe(self):
        # A squash merge with the remote branch deleted and pruned.
        path = self.worktree("11", "farm/11-thing", push=False)
        self.pr_merged.return_value = True
        lines, client = self.run_cleanup(done_item())
        self.assertFalse(os.path.exists(path))
        self.assertNotIn("farm/11-thing", self.branches())
        self.pr_merged.assert_called_with(self.repo, "farm/11-thing")

    def test_branch_checked_out_in_the_main_checkout_is_kept(self):
        git(self.repo, "checkout", "-q", "-b", "farm/11-thing")
        git(self.repo, "push", "-q", "-u", "origin", "farm/11-thing")
        lines, client = self.run_cleanup(done_item())
        self.assertIn("farm/11-thing", self.branches())
        self.assertEqual(client.writes, [])
        self.assertIn("is checked out in", lines[0])

    def test_nothing_local_with_a_farm_status_writes_done(self):
        lines, client = self.run_cleanup(done_item(farm_status="merge-review"))
        self.assertEqual(self.farm_status_writes(client), [("11", ["farm:done"])])
        self.assertIn("Nothing local to remove", lines[0])

    def test_item_waiting_for_its_retro_is_not_touched(self):
        path = self.worktree("11", "farm/11-thing")
        lines, client = self.run_cleanup(done_item(), wait_ids={"11"})
        self.assertTrue(os.path.exists(path))
        self.assertIn("farm/11-thing", self.branches())
        self.assertEqual(client.writes, [])
        self.assertEqual(lines, ["Waiting for #11's retro."])

    def test_item_whose_retro_failed_is_cleaned_with_a_note(self):
        path = self.worktree("11", "farm/11-thing")
        lines, client = self.run_cleanup(done_item(), retro_failed_ids={"11"})
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.farm_status_writes(client), [("11", ["farm:done"])])
        self.assertTrue(lines[0].startswith("Set #11 to farm:done and removed"))
        self.assertTrue(lines[0].endswith(" The retro did not finish after 2 tries."))

    def test_running_agent_wins_over_the_retro_wait(self):
        lines, _ = self.run_cleanup(done_item(), skip_ids={"11"}, wait_ids={"11"})
        self.assertEqual(lines, ["Skipped #11: an agent is working on it."])

    def test_nothing_local_and_never_touched_by_the_farm_writes_nothing(self):
        lines, client = self.run_cleanup(raw_item("11", stage="discovery", farm_status="", state="closed"))
        self.assertEqual(client.writes, [])
        self.assertEqual(lines, [])

    def test_empty_farm_status_with_a_worktree_is_cleaned_up(self):
        # The Staging Review automation clears Farm status before an item reaches Done.
        path = self.worktree("11", "farm/11-thing")
        _, client = self.run_cleanup(done_item(farm_status=""))
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.farm_status_writes(client), [("11", ["farm:done"])])

    def test_second_run_does_nothing(self):
        self.worktree("11", "farm/11-thing")
        client = FakeClient(done_item())
        for _ in range(2):
            items = [fb.FarmItem.from_github(raw) for raw in client.board_items()]
            lines = cleanup.clean_up(self.repo, client, BOARD, items, "me", NOW)
        self.assertEqual(lines, [])
        self.assertEqual(len(client.writes), 1)

    def test_a_task_id_style_name_is_not_an_issue_number(self):
        path = self.worktree("SFP-15", "farm/SFP-15-thing")
        self.run_cleanup(done_item("15"))
        self.assertTrue(os.path.exists(path))
        self.assertIn("farm/SFP-15-thing", self.branches())

    def test_other_worktrees_and_branches_are_not_touched(self):
        mine = self.worktree("11", "farm/11-thing")
        phase0 = self.worktree("sf-0b6a4", "farm/sf-0b6a4")
        other_item = self.worktree("12", "farm/12-other")
        longer_id = self.worktree("110", "farm/110-other")
        self.run_cleanup(done_item())
        self.assertFalse(os.path.exists(mine))
        for path in (phase0, other_item, longer_id):
            self.assertTrue(os.path.exists(path))
        self.assertEqual(sorted(self.branches()), ["farm/110-other", "farm/12-other", "farm/sf-0b6a4", "main"])

    def test_items_owned_by_others_or_not_done_are_not_touched(self):
        path = self.worktree("11", "farm/11-thing")
        lines, client = self.run_cleanup(done_item(assignees=("you",)))
        self.assertTrue(os.path.exists(path))
        lines, client = self.run_cleanup(raw_item("11", stage="review", farm_status="merge-review"))
        self.assertTrue(os.path.exists(path))
        lines, client = self.run_cleanup(done_item(farm_status="done"))
        self.assertTrue(os.path.exists(path))
        self.assertEqual(client.writes, [])

    def test_dry_run_changes_nothing(self):
        path = self.worktree("11", "farm/11-thing")
        lines, client = self.run_cleanup(done_item(), dry_run=True)
        self.assertTrue(os.path.exists(path))
        self.assertIn("farm/11-thing", self.branches())
        self.assertEqual(client.writes, [])
        self.assertTrue(lines[0].startswith("Would set #11 to farm:done and remove worktree"))

    def test_skips_items_with_a_running_agent_or_a_live_claim(self):
        path = self.worktree("11", "farm/11-thing")
        lines, client = self.run_cleanup(done_item(), skip_ids={"11"})
        self.assertIn("an agent is working on it", lines[0])
        expires = fb.format_date(NOW + timedelta(minutes=10))
        lines, client = self.run_cleanup(done_item(claim="implement|abc", expires=expires))
        self.assertIn("an agent is working on it", lines[0])
        self.assertTrue(os.path.exists(path))
        self.assertEqual(client.writes, [])

    def test_prunes_a_worktree_folder_deleted_by_hand(self):
        path = self.worktree("11", "farm/11-thing")
        shutil.rmtree(path)
        self.run_cleanup(done_item())
        out = subprocess.run(["git", "worktree", "list"], cwd=self.repo, capture_output=True, text=True).stdout
        self.assertNotIn("proj-farm", out)
        self.assertNotIn("farm/11-thing", self.branches())

    def test_keeps_a_detached_worktree_with_commits_on_no_branch(self):
        path = self.worktree("11", "farm/11-thing")
        git(path, "checkout", "-q", "--detach")
        self.write(path, "more.txt", "x")
        git(path, "add", ".")
        git(path, "commit", "-q", "-m", "detached work")
        lines, client = self.run_cleanup(done_item())
        self.assertTrue(os.path.exists(path))
        self.assertIn("detached HEAD", lines[0])

    def test_a_failed_done_write_removes_nothing(self):
        path = self.worktree("11", "farm/11-thing")
        client = FakeClient(done_item())

        def fail(number, add=(), remove=()):
            raise GitHubError("Bad label")

        client.set_labels = fail
        items = [fb.FarmItem.from_github(raw) for raw in client.board_items()]
        lines = cleanup.clean_up(self.repo, client, BOARD, items, "me", NOW)
        self.assertTrue(os.path.exists(path))
        self.assertIn("farm/11-thing", self.branches())
        self.assertIn("could not set farm:done: Bad label", lines[0])


class PrMergedTests(unittest.TestCase):
    def check(self, gh_stdout, gh_code=0):
        """pr_merged for a branch at commit abc, with gh's output faked."""
        def run(command, **kwargs):
            if command[0] == "gh":
                return subprocess.CompletedProcess(command, gh_code, gh_stdout, "")
            return subprocess.CompletedProcess(command, 0, "abc\n", "")

        with mock.patch.object(cleanup.subprocess, "run", run):
            return cleanup.pr_merged("/repo", "farm/11-thing")

    def test_merged_pr_with_the_branch_head(self):
        self.assertTrue(self.check("old\nabc\n"))

    def test_merged_pr_with_another_head_is_not_enough(self):
        # Commits made after the merge would be lost.
        self.assertFalse(self.check("old\n"))

    def test_gh_failure_counts_as_not_merged(self):
        self.assertFalse(self.check("", gh_code=1))

    def test_gh_timeout_counts_as_not_merged(self):
        def run(command, **kwargs):
            if command[0] == "gh":
                self.assertEqual(kwargs["timeout"], cleanup.GH_TIMEOUT_SECONDS)
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return subprocess.CompletedProcess(command, 0, "abc\n", "")

        with mock.patch.object(cleanup.subprocess, "run", run):
            self.assertFalse(cleanup.pr_merged("/repo", "farm/11-thing"))


class ItemKeyTests(unittest.TestCase):
    def test_item_key(self):
        self.assertEqual(cleanup.item_key("14"), "14")
        self.assertEqual(cleanup.item_key("14-login-fix"), "14")
        self.assertIsNone(cleanup.item_key("SFP-015-clean-up"))
        self.assertIsNone(cleanup.item_key("sf-0b6a4"))
        self.assertIsNone(cleanup.item_key("notes"))


if __name__ == "__main__":
    unittest.main()
