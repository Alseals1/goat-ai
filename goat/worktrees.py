"""Item worktrees: one git worktree per item, next to the main checkout, with the project's env files.

The coordinator prepares the worktree before it starts an agent, and `goat.py worktree` does the same for
manual runs. The path is <parent of main checkout>/<repo folder>-goat/<ref>, where <ref> is the item's
issue number. This is the folder that goat/cleanup.py removes when the item is Done.

A new worktree is detached at origin/<default branch>. The Implement agent makes its branch there.
"""

import os
import shutil
import subprocess

from goat.cleanup import goat_folder, item_key, list_worktrees, run_git
from goat.config import WORKTREE_ENV_FILES_ENV

DEFAULT_ENV_FILES = ".env"
FETCH_TIMEOUT_SECONDS = 60


class WorktreeError(Exception):
    pass


def item_ref(item):
    """The name of the item's worktree folder: its issue number."""
    return item.id


def worktree_path(repo, ref):
    return os.path.join(goat_folder(repo), ref)


def default_branch(repo):
    """The remote's default branch, from origin/HEAD. "main" when origin/HEAD is not set."""
    code, out, _ = run_git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    name = out.strip()
    if code != 0 or not name.startswith("origin/"):
        return "main"
    return name[len("origin/"):]


def fetch(repo):
    """git fetch origin --prune. Raises WorktreeError when it fails or takes too long."""
    try:
        result = subprocess.run(["git", "-C", repo, "fetch", "origin", "--prune"],
                                capture_output=True, text=True, timeout=FETCH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise WorktreeError("git fetch in {} took more than {} seconds".format(repo, FETCH_TIMEOUT_SECONDS))
    if result.returncode != 0:
        raise WorktreeError("git fetch in {} failed: {}".format(repo, result.stderr.strip()))


def prepare(repo, ref):
    """Makes or refreshes the item's worktree and returns its path. Call fetch() first.

    - Missing: made detached at origin/<default branch>.
    - On a branch: used as it is (the Implement agent's work).
    - Detached with no local changes: moved to origin/<default branch>.
    - Detached with local changes or commits on no branch: not touched, raises WorktreeError.
    """
    if item_key(ref) != ref:
        # The cleanup finds worktrees by this name, and a name with "/" or ".." could point outside the folder.
        raise WorktreeError("{!r} is not an issue number".format(ref))
    path = worktree_path(repo, ref)
    target = "origin/" + default_branch(repo)

    if not os.path.exists(path):
        os.makedirs(goat_folder(repo), exist_ok=True)
        # Clears git's record of a worktree folder that was deleted by hand, so `add` can use the path again.
        run_git(repo, "worktree", "prune")
        code, _, err = run_git(repo, "worktree", "add", "--detach", path, target)
        if code != 0:
            raise WorktreeError("could not make worktree {}: {}".format(path, err))
        return path

    worktree = find_worktree(repo, path)
    if worktree is None:
        raise WorktreeError("{} exists but is not a worktree of {}".format(path, repo))
    if worktree.branch:
        return path

    code, out, err = run_git(path, "status", "--porcelain")
    if code != 0:
        raise WorktreeError("cannot read worktree {}: {}".format(path, err))
    if out.strip():
        raise WorktreeError("worktree {} has local changes on a detached HEAD".format(path))
    code, out, _ = run_git(path, "log", "--oneline", "HEAD", "--not", "--branches", "--remotes")
    if code != 0 or out.strip():
        raise WorktreeError("worktree {} has commits on no branch".format(path))
    code, _, err = run_git(path, "checkout", "-q", "--detach", target)
    if code != 0:
        raise WorktreeError("could not move worktree {} to {}: {}".format(path, target, err))
    return path


# The autopilot's QA worktree folder, next to the issue worktrees. Not an issue number, so the cleanup leaves it alone.
QA_FOLDER = "qa"


def prepare_qa(repo):
    """Makes or refreshes the autopilot's QA worktree, detached at origin/<default branch>, and returns its path.
    Call fetch() first.

    QA tests the default branch as merged. Untracked files (installed packages, test output) are kept between runs;
    a change to a tracked file means something went wrong, so it raises WorktreeError instead of throwing it away.
    """
    path = worktree_path(repo, QA_FOLDER)
    target = "origin/" + default_branch(repo)
    if not os.path.exists(path):
        os.makedirs(goat_folder(repo), exist_ok=True)
        run_git(repo, "worktree", "prune")
        code, _, err = run_git(repo, "worktree", "add", "--detach", path, target)
        if code != 0:
            raise WorktreeError("could not make the QA worktree {}: {}".format(path, err))
        return path
    worktree = find_worktree(repo, path)
    if worktree is None:
        raise WorktreeError("{} exists but is not a worktree of {}".format(path, repo))
    if worktree.branch:
        raise WorktreeError("the QA worktree {} is on branch {}; it must stay detached".format(path, worktree.branch))
    code, out, err = run_git(path, "status", "--porcelain", "--untracked-files=no")
    if code != 0:
        raise WorktreeError("cannot read the QA worktree {}: {}".format(path, err))
    if out.strip():
        raise WorktreeError("the QA worktree {} has changes to tracked files".format(path))
    code, _, err = run_git(path, "checkout", "-q", "--detach", target)
    if code != 0:
        raise WorktreeError("could not move the QA worktree {} to {}: {}".format(path, target, err))
    return path


def find_worktree(repo, path):
    """The Worktree of the repo at this path, or None."""
    path = os.path.realpath(path)
    for worktree in list_worktrees(repo):
        if os.path.realpath(worktree.path) == path:
            return worktree
    return None


# ---------- Env files ----------

def env_file_paths(env):
    """The env files to copy, from GOAT_WORKTREE_ENV_FILES (comma-separated, relative to the repo root).

    Default: .env. Raises WorktreeError for a path that is absolute or goes outside the repo.
    """
    text = env.get(WORKTREE_ENV_FILES_ENV, DEFAULT_ENV_FILES)
    paths = [part.strip() for part in text.split(",") if part.strip()]
    for path in paths:
        if os.path.isabs(path) or ".." in path.replace("\\", "/").split("/"):
            raise WorktreeError("{} must list paths inside the repo, not {!r}".format(WORKTREE_ENV_FILES_ENV, path))
    return paths


def copy_env_files(repo, worktree, paths):
    """Copies each env file from the main checkout into the worktree. Returns one log line per file.

    A file is copied only when it exists in the main checkout, is missing in the worktree, and git ignores
    it in the worktree, so it is never committed and never blocks the cleanup. Never prints file contents.
    """
    lines = []
    for path in paths:
        source = os.path.join(repo, path)
        destination = os.path.join(worktree, path)
        if not os.path.isfile(source):
            continue
        if os.path.exists(destination):
            continue
        code, _, _ = run_git(worktree, "check-ignore", "-q", path)
        if code != 0:
            lines.append("Not copied {}: git does not ignore it.".format(path))
            continue
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy2(source, destination)
        lines.append("Copied {}.".format(path))
    return lines
