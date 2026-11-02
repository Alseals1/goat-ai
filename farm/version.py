"""The farm version: the VERSION file, and the git commit and release tag of this clone.

The version label is "0.5.0" when HEAD is at tag v0.5.0. Otherwise it is "0.5.0+<short commit>" (SemVer build
metadata), so a run from main or a branch shows its exact commit.
"""

import os
import re
import subprocess

from farm.config import REPO_ROOT

VERSION_FILE = os.path.join(REPO_ROOT, "VERSION")
RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def read_version(repo=REPO_ROOT):
    with open(os.path.join(repo, "VERSION")) as f:
        return f.read().strip()


def git_output(repo, *args):
    """The output of a git command, or None on any error (no git, not a clone)."""
    try:
        result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def commit(repo=REPO_ROOT):
    """The short hash of HEAD, or None."""
    return git_output(repo, "rev-parse", "--short", "HEAD") or None


def release_tag_at_head(repo=REPO_ROOT):
    """The release tag (vX.Y.Z) at HEAD, or None."""
    tags = git_output(repo, "tag", "--points-at", "HEAD") or ""
    for tag in tags.splitlines():
        if RELEASE_TAG.match(tag.strip()):
            return tag.strip()
    return None


def version_label(repo=REPO_ROOT):
    version = read_version(repo)
    if release_tag_at_head(repo) == "v" + version:
        return version
    head = commit(repo)
    if head:
        return "{}+{}".format(version, head)
    return version


def version_lines(repo=REPO_ROOT):
    """The lines that `farm.py version` prints."""
    head = commit(repo)
    tag = release_tag_at_head(repo)
    if not head:
        commit_text = "unknown"
    elif tag:
        commit_text = "{} (tag {})".format(head, tag)
    else:
        commit_text = head
    return ["farm {}".format(read_version(repo)), "commit {}".format(commit_text)]
