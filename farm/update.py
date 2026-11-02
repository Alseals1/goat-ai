"""farm.py update: move a farm install to the newest release tag.

An install is a clone at a release tag (vX.Y.Z), with a detached HEAD. A clone on a branch is a farm development
checkout: update does not move it, and says to use git pull. The skills and ~/.software-farm/farm.py are links into
this clone, so the update changes both.
"""

import os
import subprocess

from farm.version import RELEASE_TAG, release_tag_at_head


class UpdateError(Exception):
    pass


def git(repo, *args):
    """The output of a git command. Raises UpdateError with git's message when it fails."""
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        raise UpdateError("git {} failed: {}".format(" ".join(args), result.stderr.strip() or result.stdout.strip()))
    return result.stdout.strip()


def tag_number(tag):
    """(major, minor, patch) of a release tag, so v0.10.0 sorts after v0.9.0."""
    match = RELEASE_TAG.match(tag)
    return tuple(int(part) for part in match.groups())


def newest_release_tag(tags):
    """The newest release tag in a list of tags, or None. Other tags are skipped."""
    release_tags = [tag.strip() for tag in tags if RELEASE_TAG.match(tag.strip())]
    if not release_tags:
        return None
    return max(release_tags, key=tag_number)


def current_branch(repo):
    """The branch at HEAD, or None for a detached HEAD."""
    result = subprocess.run(["git", "symbolic-ref", "-q", "--short", "HEAD"], cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def new_skills(repo, old_tag, new_tag):
    """Names of the skills that the new tag adds (skills/<name>/SKILL.md)."""
    added = git(repo, "diff", "--name-only", "--diff-filter=A", old_tag, new_tag, "--", "skills")
    names = []
    for path in added.splitlines():
        if os.path.basename(path) == "SKILL.md":
            names.append(os.path.basename(os.path.dirname(path)))
    return names


def update(repo, running_agents):
    """Moves the clone at repo to the newest release tag. Returns the lines to print.

    Refuses (UpdateError) on a branch, at a commit that is not a release tag, with local changes, or while the
    coordinator has agents running. HEAD does not move when it refuses.
    """
    branch = current_branch(repo)
    if branch:
        raise UpdateError("This clone is on branch {}, so it is a farm development checkout. farm.py update only "
                          "moves installs at a release tag. Use git pull here.".format(branch))
    old_tag = release_tag_at_head(repo)
    if not old_tag:
        raise UpdateError("HEAD is at {}, which is not a release tag. farm.py update only moves installs at a release "
                          "tag. To go to one: git checkout <tag> (git tag --list 'v*' lists them).".format(
                              git(repo, "rev-parse", "--short", "HEAD")))
    if git(repo, "status", "--porcelain"):
        raise UpdateError("This clone has local changes. Commit, move or remove them, then try again.")
    if running_agents > 0:
        raise UpdateError("The coordinator has {} agent(s) running. Wait for them, or run farm.py stop, then try "
                          "again.".format(running_agents))

    git(repo, "fetch", "--tags", "--quiet", "origin")
    new_tag = newest_release_tag(git(repo, "tag", "--list", "v*").splitlines())
    if tag_number(new_tag) <= tag_number(old_tag):
        return ["Already at the newest release, {}.".format(old_tag)]

    git(repo, "checkout", "--quiet", new_tag)
    lines = ["Moved from {} to {}.".format(old_tag, new_tag), "Changes:"]
    lines += git(repo, "log", "--oneline", "{}..{}".format(old_tag, new_tag)).splitlines()
    skills = new_skills(repo, old_tag, new_tag)
    if skills:
        lines.append("New skills: {}. Run scripts/install.sh to link them.".format(", ".join(skills)))
    return lines
