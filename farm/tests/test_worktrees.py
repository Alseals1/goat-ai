"""Tests for farm.worktrees with real temporary git repos: a bare origin, a main checkout, and item worktrees."""

import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from farm import board as fb
from farm import cleanup, worktrees
from farm.tests.fakes import BOARD, FakeClient, raw_item

NOW = datetime(2026, 10, 28, 15, 0, 0, tzinfo=timezone.utc)


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
                   cwd=cwd, check=True, capture_output=True)


def git_out(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def write(folder, name, text):
    path = os.path.join(folder, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def read(folder, name):
    with open(os.path.join(folder, name)) as f:
        return f.read()


class RepoTestCase(unittest.TestCase):
    """A bare origin and a main checkout "proj" on main, with .env files ignored."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.origin = os.path.join(self.tmp, "origin.git")
        git(self.tmp, "init", "-q", "--bare", "-b", "main", self.origin)
        self.repo = os.path.join(self.tmp, "proj")
        git(self.tmp, "clone", "-q", self.origin, self.repo)
        git(self.repo, "checkout", "-q", "-b", "main")
        write(self.repo, "README", "hello")
        write(self.repo, ".gitignore", ".env\n.env.local\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "first")
        git(self.repo, "push", "-q", "-u", "origin", "main")

    def push_new_commit(self):
        """A new commit on origin/main, made in a second clone. Returns its hash."""
        other = os.path.join(self.tmp, "other")
        git(self.tmp, "clone", "-q", self.origin, other)
        write(other, "NEW", "new")
        git(other, "add", ".")
        git(other, "commit", "-q", "-m", "second")
        git(other, "push", "-q", "origin", "main")
        return git_out(other, "rev-parse", "HEAD")

    def head(self, path):
        return git_out(path, "rev-parse", "HEAD")


class PrepareTests(RepoTestCase):
    def test_makes_a_detached_worktree_at_origin_main(self):
        path = worktrees.prepare(self.repo, "14")
        self.assertEqual(path, os.path.join(self.tmp, "proj-farm", "14"))
        self.assertEqual(self.head(path), git_out(self.repo, "rev-parse", "origin/main"))
        self.assertEqual(git_out(path, "branch", "--show-current"), "")

    def test_leaves_a_worktree_on_a_branch(self):
        path = worktrees.prepare(self.repo, "14")
        git(path, "switch", "-q", "-c", "farm/14-thing")
        write(path, "work.txt", "in progress")
        before = self.head(path)
        self.push_new_commit()
        worktrees.fetch(self.repo)
        self.assertEqual(worktrees.prepare(self.repo, "14"), path)
        self.assertEqual(self.head(path), before)
        self.assertEqual(read(path, "work.txt"), "in progress")

    def test_moves_a_clean_detached_worktree_to_the_new_origin_commit(self):
        path = worktrees.prepare(self.repo, "14")
        new = self.push_new_commit()
        worktrees.fetch(self.repo)
        worktrees.prepare(self.repo, "14")
        self.assertEqual(self.head(path), new)

    def test_ignored_env_file_does_not_block_the_move(self):
        path = worktrees.prepare(self.repo, "14")
        write(path, ".env", "A=1")
        new = self.push_new_commit()
        worktrees.fetch(self.repo)
        worktrees.prepare(self.repo, "14")
        self.assertEqual(self.head(path), new)

    def test_detached_worktree_with_changes_is_not_touched(self):
        path = worktrees.prepare(self.repo, "14")
        write(path, "README", "changed")
        before = self.head(path)
        self.push_new_commit()
        worktrees.fetch(self.repo)
        with self.assertRaisesRegex(worktrees.WorktreeError, "local changes"):
            worktrees.prepare(self.repo, "14")
        self.assertEqual(self.head(path), before)
        self.assertEqual(read(path, "README"), "changed")

    def test_detached_worktree_with_commits_on_no_branch_is_not_touched(self):
        path = worktrees.prepare(self.repo, "14")
        write(path, "more.txt", "x")
        git(path, "add", ".")
        git(path, "commit", "-q", "-m", "detached work")
        with self.assertRaisesRegex(worktrees.WorktreeError, "commits on no branch"):
            worktrees.prepare(self.repo, "14")

    def test_a_folder_that_is_not_a_worktree(self):
        os.makedirs(os.path.join(self.tmp, "proj-farm", "14"))
        with self.assertRaisesRegex(worktrees.WorktreeError, "not a worktree"):
            worktrees.prepare(self.repo, "14")

    def test_remakes_a_worktree_folder_deleted_by_hand(self):
        path = worktrees.prepare(self.repo, "14")
        shutil.rmtree(path)
        self.assertEqual(worktrees.prepare(self.repo, "14"), path)
        self.assertTrue(os.path.isfile(os.path.join(path, "README")))

    def test_refuses_a_ref_that_is_not_an_item_id(self):
        for ref in ("../x", "14/../../x", "notes", "SFP-14", ""):
            with self.assertRaises(worktrees.WorktreeError, msg=ref):
                worktrees.prepare(self.repo, ref)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "proj-farm")))

    def test_failed_fetch_raises(self):
        git(self.repo, "remote", "set-url", "origin", os.path.join(self.tmp, "missing.git"))
        with self.assertRaisesRegex(worktrees.WorktreeError, "git fetch"):
            worktrees.fetch(self.repo)

    def test_default_branch(self):
        self.assertEqual(worktrees.default_branch(self.repo), "main")
        git(self.repo, "push", "-q", "origin", "main:trunk")
        git(self.repo, "fetch", "-q", "origin")
        git(self.repo, "remote", "set-head", "origin", "trunk")
        self.assertEqual(worktrees.default_branch(self.repo), "trunk")

    def test_item_ref(self):
        self.assertEqual(worktrees.item_ref(SimpleNamespace(id="14")), "14")


class EnvFileTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.worktree = worktrees.prepare(self.repo, "14")

    def test_copies_ignored_files_including_nested_ones(self):
        write(self.repo, ".env", "TOKEN=main")
        write(self.repo, "apps/web/.env.local", "PORT=3000")
        lines = worktrees.copy_env_files(self.repo, self.worktree, [".env", "apps/web/.env.local"])
        self.assertEqual(lines, ["Copied .env.", "Copied apps/web/.env.local."])
        self.assertEqual(read(self.worktree, ".env"), "TOKEN=main")
        self.assertEqual(read(self.worktree, "apps/web/.env.local"), "PORT=3000")

    def test_does_not_overwrite(self):
        write(self.repo, ".env", "TOKEN=main")
        write(self.worktree, ".env", "TOKEN=worktree")
        self.assertEqual(worktrees.copy_env_files(self.repo, self.worktree, [".env"]), [])
        self.assertEqual(read(self.worktree, ".env"), "TOKEN=worktree")

    def test_skips_a_file_that_git_does_not_ignore(self):
        write(self.repo, "config.env", "A=1")
        lines = worktrees.copy_env_files(self.repo, self.worktree, ["config.env"])
        self.assertEqual(lines, ["Not copied config.env: git does not ignore it."])
        self.assertFalse(os.path.exists(os.path.join(self.worktree, "config.env")))

    def test_skips_a_missing_file(self):
        self.assertEqual(worktrees.copy_env_files(self.repo, self.worktree, [".env"]), [])
        self.assertFalse(os.path.exists(os.path.join(self.worktree, ".env")))

    def test_cleanup_still_removes_the_worktree(self):
        write(self.repo, ".env", "TOKEN=main")
        worktrees.copy_env_files(self.repo, self.worktree, [".env"])
        client = FakeClient(raw_item("14", stage="staging", farm_status="", state="closed"))
        items = [fb.FarmItem.from_github(raw) for raw in client.board_items()]
        with mock.patch.object(cleanup, "pr_merged", return_value=False):
            lines = cleanup.clean_up(self.repo, client, BOARD, items, "me", NOW)
        self.assertFalse(os.path.exists(self.worktree), lines)


class EnvFilePathsTests(unittest.TestCase):
    def test_default_is_dot_env(self):
        self.assertEqual(worktrees.env_file_paths({}), [".env"])

    def test_list_with_spaces_and_empty_entries(self):
        env = {"FARM_WORKTREE_ENV_FILES": " .env , apps/web/.env.local,, "}
        self.assertEqual(worktrees.env_file_paths(env), [".env", "apps/web/.env.local"])

    def test_refuses_paths_outside_the_repo(self):
        for bad in ("/etc/passwd", "../x", "apps/../../x"):
            with self.assertRaises(worktrees.WorktreeError, msg=bad):
                worktrees.env_file_paths({"FARM_WORKTREE_ENV_FILES": bad})


if __name__ == "__main__":
    unittest.main()
