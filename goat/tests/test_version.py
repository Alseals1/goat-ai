"""Tests for goat.version with a real temporary git repo."""

import os
import shutil
import tempfile
import unittest

from goat import version
from goat.tests.test_worktrees import git, git_out, write


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = os.path.join(self.tmp, "goat")
        os.makedirs(self.repo)
        write(self.repo, "VERSION", "0.5.0\n")

    def make_clone_at_tag(self):
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "first")
        git(self.repo, "tag", "v0.5.0")

    def test_label_is_the_version_at_its_release_tag(self):
        self.make_clone_at_tag()
        self.assertEqual(version.version_label(self.repo), "0.5.0")
        head = git_out(self.repo, "rev-parse", "--short", "HEAD")
        self.assertEqual(version.version_lines(self.repo), ["G.O.A.T. 0.5.0", "commit {} (tag v0.5.0)".format(head)])

    def test_label_has_the_commit_after_the_tag(self):
        self.make_clone_at_tag()
        write(self.repo, "README", "more")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "second")
        head = git_out(self.repo, "rev-parse", "--short", "HEAD")
        self.assertEqual(version.version_label(self.repo), "0.5.0+" + head)
        self.assertEqual(version.version_lines(self.repo), ["G.O.A.T. 0.5.0", "commit " + head])

    def test_a_tag_for_another_version_does_not_count(self):
        self.make_clone_at_tag()
        write(self.repo, "VERSION", "0.6.0\n")
        git(self.repo, "commit", "-q", "-am", "bump")
        git(self.repo, "tag", "v0.5.1")
        head = git_out(self.repo, "rev-parse", "--short", "HEAD")
        self.assertEqual(version.version_label(self.repo), "0.6.0+" + head)

    def test_no_git(self):
        self.assertIsNone(version.commit(self.repo))
        self.assertEqual(version.version_label(self.repo), "0.5.0")
        self.assertEqual(version.version_lines(self.repo), ["G.O.A.T. 0.5.0", "commit unknown"])


if __name__ == "__main__":
    unittest.main()
