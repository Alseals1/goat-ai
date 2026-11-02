"""Tests for farm.update with real temporary git repos: a bare origin with release tags, and an install clone."""

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from farm import cli, update
from farm.tests.test_worktrees import git, git_out, write
from farm.update import UpdateError


class NewestTagTests(unittest.TestCase):
    def test_sorts_by_number(self):
        self.assertEqual(update.newest_release_tag(["v0.9.0", "v0.10.0", "v0.2.0"]), "v0.10.0")

    def test_skips_tags_that_are_not_releases(self):
        self.assertEqual(update.newest_release_tag(["v0.5.0", "v1.0.0-rc1", "vnext"]), "v0.5.0")
        self.assertIsNone(update.newest_release_tag(["latest"]))


class UpdateTests(unittest.TestCase):
    """origin.git has v0.5.0 and v0.6.0. The install is a clone at v0.5.0."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.origin = os.path.join(self.tmp, "origin.git")
        git(self.tmp, "init", "-q", "--bare", "-b", "main", self.origin)
        self.dev = os.path.join(self.tmp, "dev")
        git(self.tmp, "clone", "-q", self.origin, self.dev)
        git(self.dev, "checkout", "-q", "-b", "main")
        self.release("0.5.0", "skills/farm-board/SKILL.md")
        self.release("0.6.0", "farm/cli.py")
        self.install = os.path.join(self.tmp, "install")
        git(self.tmp, "clone", "-q", self.origin, self.install)
        git(self.install, "checkout", "-q", "v0.5.0")

    def release(self, version, changed_file):
        """A commit on origin/main that sets VERSION and changes a file, tagged v<version> and pushed."""
        write(self.dev, "VERSION", version + "\n")
        write(self.dev, changed_file, version)
        git(self.dev, "add", ".")
        git(self.dev, "commit", "-q", "-m", "Release " + version)
        git(self.dev, "tag", "v" + version)
        git(self.dev, "push", "-q", "origin", "main", "v" + version)

    def head(self):
        return git_out(self.install, "rev-parse", "HEAD")

    def test_moves_to_the_newest_tag(self):
        lines = update.update(self.install, running_agents=0)
        self.assertEqual(self.head(), git_out(self.install, "rev-parse", "v0.6.0^{commit}"))
        self.assertEqual(lines[0], "Moved from v0.5.0 to v0.6.0.")
        self.assertEqual(lines[1], "Changes:")
        self.assertTrue(lines[2].endswith("Release 0.6.0"), lines[2])
        self.assertEqual(len(lines), 3)

    def test_picks_v0_10_over_v0_9(self):
        self.release("0.9.0", "farm/cli.py")
        self.release("0.10.0", "farm/cli.py")
        lines = update.update(self.install, running_agents=0)
        self.assertEqual(lines[0], "Moved from v0.5.0 to v0.10.0.")
        self.assertEqual(self.head(), git_out(self.install, "rev-parse", "v0.10.0^{commit}"))

    def test_at_the_newest_tag_changes_nothing(self):
        git(self.install, "checkout", "-q", "v0.6.0")
        before = self.head()
        self.assertEqual(update.update(self.install, running_agents=0), ["Already at the newest release, v0.6.0."])
        self.assertEqual(self.head(), before)

    def test_tells_to_run_install_for_a_new_skill(self):
        self.release("0.7.0", "skills/farm-new/SKILL.md")
        lines = update.update(self.install, running_agents=0)
        self.assertEqual(lines[-1], "New skills: farm-new. Run scripts/install.sh to link them.")

    def assert_refuses(self, pattern, running_agents=0):
        before = self.head()
        with self.assertRaisesRegex(UpdateError, pattern):
            update.update(self.install, running_agents)
        self.assertEqual(self.head(), before)

    def test_refuses_on_a_branch(self):
        git(self.install, "checkout", "-q", "main")
        self.assert_refuses("on branch main, so it is a farm development checkout.*Use git pull")

    def test_refuses_at_a_commit_that_is_not_a_release_tag(self):
        git(self.install, "checkout", "-q", "--detach", "main")
        write(self.install, "farm/extra.py", "x")
        git(self.install, "add", ".")
        git(self.install, "commit", "-q", "-m", "local work")
        self.assert_refuses("not a release tag")

    def test_refuses_with_local_changes(self):
        write(self.install, "VERSION", "changed\n")
        self.assert_refuses("local changes")

    def test_refuses_with_running_agents(self):
        self.assert_refuses("2 agent\\(s\\) running", running_agents=2)

    def test_a_fetch_error_is_shown(self):
        git(self.install, "remote", "set-url", "origin", os.path.join(self.tmp, "missing.git"))
        self.assert_refuses("git fetch --tags --quiet origin failed")


class UpdateCommandTests(unittest.TestCase):
    def test_counts_the_coordinator_agents_that_still_run(self):
        state = {"runs": [{"pid": os.getpid(), "item": "1", "role": "plan", "log": "x"}], "attempts": {}}
        with mock.patch.object(cli.coordinator, "load_state", return_value=state), \
                mock.patch.object(cli, "update", return_value=["Already at the newest release, v0.5.0."]) as run_update, \
                mock.patch.object(cli, "project_env", side_effect=AssertionError("project_env called")), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["update"]), 0)
        run_update.assert_called_once_with(cli.REPO_ROOT, 1)
        self.assertEqual(out.getvalue(), "Already at the newest release, v0.5.0.\n")

    def test_a_refusal_exits_1(self):
        with mock.patch.object(cli.coordinator, "load_state", return_value={"runs": [], "attempts": {}}), \
                mock.patch.object(cli, "update", side_effect=UpdateError("on branch main")), \
                redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["update"]), 1)
        self.assertEqual(err.getvalue(), "farm.py: on branch main\n")


if __name__ == "__main__":
    unittest.main()
