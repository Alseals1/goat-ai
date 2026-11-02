"""Goat config.

- Shared rules (gates, leases) are in goat-config.json in this repo.
- The project's GitHub repo comes from GOAT_GITHUB_REPO in the .env file of its checkout, or, when that is not
  set, from the checkout's `origin` remote. Real environment variables win over .env.
- GitHub access is the developer's own `gh` login. There is no token setting.
- Local files (claims, run records) are in ~/.goat-ai.
"""

import json
import os
import re
import subprocess

from goat import exit_codes

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED_CONFIG = os.path.join(REPO_ROOT, "goat-config.json")

LOCAL_DIR = os.path.expanduser("~/.goat-ai")
CLAIMS_DIR = os.path.join(LOCAL_DIR, "claims")
RUNS_FILE = os.path.join(LOCAL_DIR, "runs.jsonl")
# Retro notes and aggregate reports (goat/retros.py). Local only: they can quote session text.
RETROS_DIR = os.path.join(LOCAL_DIR, "retros")
# The coordinator's running agents. `goat.py chat` reads it too.
COORDINATOR_STATE_FILE = os.path.join(LOCAL_DIR, "coordinator", "state.json")

DEFAULT_LEASE_MINUTES = 30
STAGES = ("intake", "plan", "implement", "review")

REPO_ENV = "GOAT_GITHUB_REPO"
# Every project setting starts with this. In the real environment, such a variable wins over each project's .env.
BOARD_ENV_PREFIX = "GOAT_GITHUB_"

# Env files (comma-separated, relative to the repo root) that goat/worktrees.py copies into item worktrees.
WORKTREE_ENV_FILES_ENV = "GOAT_WORKTREE_ENV_FILES"

REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# git@github.com:owner/name.git, https://github.com/owner/name(.git), ssh://git@github.com/owner/name
REMOTE_URL = re.compile(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")


class ConfigError(Exception):
    exit_code = exit_codes.CONFIG


def load_shared(path=SHARED_CONFIG):
    with open(path) as f:
        return json.load(f)


def lease_minutes(shared, role):
    """The claim lease for a role: leaseMinutesByStage, then leaseMinutes, then 30."""
    by_stage = shared.get("leaseMinutesByStage", {}).get(role)
    if isinstance(by_stage, (int, float)) and by_stage > 0:
        return by_stage
    default = shared.get("leaseMinutes")
    if isinstance(default, (int, float)) and default > 0:
        return default
    return DEFAULT_LEASE_MINUTES


# ---------- .env ----------

def parse_env_file(path):
    """KEY=VALUE lines from a .env file. Skips blank lines and comments, allows `export` and quotes."""
    values = {}
    if not os.path.isfile(path):
        return values
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    return values


def main_checkout(cwd):
    """The project's main checkout folder. In a git worktree, this is the folder of the main checkout,
    because .env is usually not in git, so a worktree has no copy of it. Outside git, it is cwd."""
    result = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        return cwd
    common_dir = os.path.abspath(os.path.join(cwd, result.stdout.strip()))
    return os.path.dirname(common_dir)


def project_env(cwd=None):
    """The project's .env values, with real environment variables on top."""
    folder = main_checkout(cwd or os.getcwd())
    env = parse_env_file(os.path.join(folder, ".env"))
    env.update(os.environ)
    return env


# ---------- The project's repo ----------

def repo_from_remote(url):
    """"owner/name" from a GitHub remote URL, or None for any other URL."""
    match = REMOTE_URL.search((url or "").strip())
    return match.group(1) if match else None


def origin_repo(cwd):
    """"owner/name" of the checkout's origin remote, or None."""
    result = subprocess.run(["git", "remote", "get-url", "origin"], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return repo_from_remote(result.stdout)


def load_board(env, cwd=None):
    """The project's goat "board": {"id": "owner/name", "repo": "owner/name", "doneLabel": "done"}.
    doneLabel is the status of a closed issue (goat/board.py DONE_STATUS).

    GOAT_GITHUB_REPO wins. Without it, the checkout's origin remote is used. Raises ConfigError when neither names a
    GitHub repo. The app repo and the factory repo are different repos: set GOAT_GITHUB_REPO in the app checkout's .env
    when its origin is not the repo that holds the issues.
    """
    repo = env.get(REPO_ENV, "").strip()
    if not repo:
        repo = origin_repo(main_checkout(cwd or os.getcwd())) or ""
    if not repo:
        raise ConfigError("Set {} (owner/name) in the project's .env, or give the checkout a GitHub `origin` remote. "
                          "`goat.py setup-repo` prints the .env line.".format(REPO_ENV))
    if not REPO_NAME.match(repo):
        raise ConfigError("{} must look like owner/name, not {!r}.".format(REPO_ENV, repo))
    return {"id": repo, "repo": repo, "doneLabel": "done"}


def board_env_names(environ):
    """The project setting names (GOAT_GITHUB_*) that are set in environ, sorted."""
    return sorted(name for name in environ if name.startswith(BOARD_ENV_PREFIX))
