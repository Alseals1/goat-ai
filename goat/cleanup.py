"""Cleanup of Done items: removes the local worktrees and goat/ branches of this developer's closed issues,
then sets the label goat:done so the cleanup does not run again for them.

The Implement agent makes a worktree at <parent of main checkout>/<repo folder>-goat/<issue number> on a branch
goat/<issue number>-<slug>. Nothing else is touched.

Nothing is removed for an item unless no work can be lost: no worktree has local changes, no branch is
checked out in another worktree, and every branch commit is on a remote or in a merged PR. The cleanup
never uses force. The coordinator runs it every turn, and `goat.py cleanup` runs it by hand.
"""

import os
import re
import subprocess
from dataclasses import dataclass, field

from goat import board as fb
from goat.github import GitHubError

# An issue number ("14") at the start of a folder or branch name, then the end of the name or "-".
ITEM_KEY = re.compile(r"^(\d+)(?:-|$)")
BRANCH_PREFIX = "goat/"
# Short, because the coordinator runs pr_merged on each turn and must not stall when there is no network.
GH_TIMEOUT_SECONDS = 15


@dataclass
class Worktree:
    path: str
    branch: str  # Short branch name, or "" for a detached HEAD.


@dataclass
class LocalWork:
    """An item's worktrees and branches, and the branches checked out in other worktrees."""
    worktrees: list = field(default_factory=list)
    branches: list = field(default_factory=list)
    other_checkouts: dict = field(default_factory=dict)  # branch -> path of a worktree this cleanup keeps

    @property
    def is_empty(self):
        return not self.worktrees and not self.branches


def run_git(repo, *args):
    """Runs git in a folder. Returns (exit code, stdout, stderr)."""
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr.strip()


def item_key(name):
    """The item id at the start of a worktree folder name or a branch name (after goat/), or None."""
    match = ITEM_KEY.match(name)
    return match.group(1) if match else None


def key_matches(key, item):
    return key is not None and key == item.id


def goat_folder(repo):
    """The folder with G.O.A.T. worktrees, next to the main checkout."""
    repo = os.path.realpath(repo)
    return os.path.join(os.path.dirname(repo), os.path.basename(repo) + "-goat")


def list_worktrees(repo):
    """Every worktree of the repo as a Worktree, from `git worktree list --porcelain`. The first is the main checkout."""
    code, out, err = run_git(repo, "worktree", "list", "--porcelain")
    if code != 0:
        raise OSError("git worktree list failed in {}: {}".format(repo, err))
    worktrees = []
    for line in out.splitlines():
        if line.startswith("worktree "):
            worktrees.append(Worktree(path=line[len("worktree "):], branch=""))
        elif line.startswith("branch refs/heads/") and worktrees:
            worktrees[-1].branch = line[len("branch refs/heads/"):]
    return worktrees


def list_goat_branches(repo):
    code, out, err = run_git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/" + BRANCH_PREFIX)
    if code != 0:
        raise OSError("git for-each-ref failed in {}: {}".format(repo, err))
    return [line for line in out.splitlines() if line]


def local_work(repo, item):
    """The item's worktrees in G.O.A.T. folder and its goat/ branches."""
    folder = goat_folder(repo)
    work = LocalWork()
    for worktree in list_worktrees(repo):
        path = os.path.realpath(worktree.path)
        if os.path.dirname(path) == folder and key_matches(item_key(os.path.basename(path)), item):
            work.worktrees.append(worktree)
        elif worktree.branch:
            work.other_checkouts[worktree.branch] = worktree.path
    for branch in list_goat_branches(repo):
        if key_matches(item_key(branch[len(BRANCH_PREFIX):]), item):
            work.branches.append(branch)
    return work


def pr_merged(repo, branch):
    """True when GitHub has a merged PR whose head is the branch's current commit.

    This makes a branch safe to delete after a squash merge, when its commits are on no remote branch.
    Any problem with gh (not installed, no network, no PR) counts as not merged.
    """
    code, tip, _ = run_git(repo, "rev-parse", branch)
    if code != 0:
        return False
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--head", branch, "--state", "merged", "--json", "headRefOid", "--jq", ".[].headRefOid"],
            cwd=repo, capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and tip.strip() in result.stdout.split()


def problems(repo, work):
    """The reasons not to remove the item's local work. Empty when nothing can be lost."""
    reasons = []
    for worktree in work.worktrees:
        if not os.path.isdir(worktree.path):
            continue
        code, out, err = run_git(worktree.path, "status", "--porcelain")
        if code != 0:
            reasons.append("cannot read worktree {}: {}".format(worktree.path, err))
        elif out.strip():
            reasons.append("worktree {} has uncommitted changes".format(worktree.path))
        elif not worktree.branch:
            # A detached HEAD can hold commits that are on no branch. Removing the worktree would lose them.
            code, out, err = run_git(worktree.path, "log", "--oneline", "HEAD", "--not", "--branches", "--remotes")
            if code != 0 or out.strip():
                reasons.append("worktree {} has a detached HEAD with commits on no branch".format(worktree.path))
    for branch in work.branches:
        if branch in work.other_checkouts:
            reasons.append("branch {} is checked out in {}".format(branch, work.other_checkouts[branch]))
            continue
        code, out, err = run_git(repo, "log", "--oneline", branch, "--not", "--remotes")
        if code != 0:
            reasons.append("cannot read branch {}: {}".format(branch, err))
        elif out.strip() and not pr_merged(repo, branch):
            reasons.append("branch {} has commits that are not on a remote or in a merged PR".format(branch))
    return reasons


def done_items(items, board, user_id):
    """Goat issues the user owns that are closed (stage done) and do not have the label goat:done yet."""
    found = []
    for item in items:
        if not item.is_owned_by(user_id) or not item.goat_touched:
            continue
        if fb.same_label(item.status, board["doneLabel"]) and not fb.same_label(item.goat_status, fb.DONE):
            found.append(item)
    return found


def label(item):
    return "#" + item.id


def describe(work):
    parts = ["worktree " + worktree.path for worktree in work.worktrees]
    parts += ["branch " + branch for branch in work.branches]
    return ", ".join(parts)


def set_done(client, board, item):
    add, remove = fb.label_changes(item, goat_status=fb.DONE)
    client.set_labels(item.id, add=add, remove=remove)


def remove(repo, work):
    """Removes the worktrees, then the branches. Returns the errors; an empty list means all were removed."""
    errors = []
    pruned = False
    for worktree in work.worktrees:
        if os.path.isdir(worktree.path):
            code, _, err = run_git(repo, "worktree", "remove", worktree.path)
            if code != 0:
                errors.append("worktree {}: {}".format(worktree.path, err))
        elif not pruned:
            # The folder was deleted by hand. Prune clears git's record of it (and of any other missing worktree).
            code, _, err = run_git(repo, "worktree", "prune")
            if code != 0:
                errors.append("worktree prune: {}".format(err))
            pruned = True
    for branch in work.branches:
        # -D, because squash merges leave the commits off the default branch. problems() checked they are safe.
        code, _, err = run_git(repo, "branch", "-D", branch)
        if code != 0:
            errors.append("branch {}: {}".format(branch, err))
    return errors


RETRO_FAILED_NOTE = " The retro did not finish after 2 tries."


def clean_up(repo, client, board, items, user_id, now, dry_run=False, skip_ids=(), wait_ids=(), retro_failed_ids=()):
    """Cleans up every Done item in `items`. Returns one line per item that had something to do.

    For each item: check that nothing can be lost, set the label goat:done, then remove. The label is written
    first so that a failed write leaves the local work in place for the next run. An issue G.O.A.T. never
    touched, with nothing local, is not written to.

    The coordinator passes wait_ids (items whose retro note is not saved yet: they are not touched) and
    retro_failed_ids (items whose retro failed: they are cleaned, and the line says so). `goat.py cleanup`
    passes neither.
    """
    lines = []
    for item in done_items(items, board, user_id):
        name = label(item)
        if item.id in skip_ids or item.claim_is_live(now):
            lines.append("Skipped {}: an agent is working on it.".format(name))
            continue
        if item.id in wait_ids:
            lines.append("Waiting for {}'s retro.".format(name))
            continue
        line = clean_up_item(repo, client, board, item, dry_run)
        if line and item.id in retro_failed_ids:
            line += RETRO_FAILED_NOTE
        if line:
            lines.append(line)
    return lines


def clean_up_item(repo, client, board, item, dry_run):
    """Cleans up one Done item. Returns its line, or None when there was nothing to do."""
    name = label(item)
    try:
        work = local_work(repo, item)
        reasons = problems(repo, work)
    except OSError as error:
        return "Kept {}: {}".format(name, error)
    if work.is_empty and not item.goat_touched:
        return None
    if reasons:
        return "Kept {}: {}.".format(name, "; ".join(reasons))

    if dry_run:
        if work.is_empty:
            return "Would set {} to goat:done. Nothing local to remove.".format(name)
        return "Would set {} to goat:done and remove {}.".format(name, describe(work))
    try:
        set_done(client, board, item)
    except GitHubError as error:
        return "Kept {}: could not set goat:done: {}".format(name, error)
    if work.is_empty:
        return "Set {} to goat:done. Nothing local to remove.".format(name)
    errors = remove(repo, work)
    if errors:
        return "Set {} to goat:done, but could not remove (remove these by hand): {}".format(
            name, "; ".join(errors))
    return "Set {} to goat:done and removed {}.".format(name, describe(work))
