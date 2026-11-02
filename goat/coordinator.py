"""G.O.A.T. AI coordinator: starts role agents for this developer's ready GitHub issues.

Every turn it:
  1. Reads each project's GitHub issues with G.O.A.T. client (goat/github.py, through `gh`). Each repo in
     the config `repos` is a local checkout. Its issues' repo is GOAT_GITHUB_REPO in its .env, else its origin
     remote. No model is used.
  2. Chooses what to start (goat/choose.py): ready issues assigned to the `gh` user, up to maxAgents in all,
     up to maxAgentsPerRepo in one repo, and up to maxAgentsByRole[role] in one role.
  3. Prepares the item's worktree (goat/worktrees.py): fetches the repo once, makes or refreshes
     <repo folder>-goat/<ref> next to the main checkout, and copies the project's env files into it.
     <ref> is the issue number.
  4. Starts each agent as a headless `claude -p "/goat-<role> <ref>"` session in that worktree,
     in auto mode, with the deny rules in agent-settings.json (repo root).
  5. Starts a retro agent (`/goat-retro <ref>`, skills/goat-retro) for each closed issue with no retro note, in
     the repo's main checkout, with the slots the stage agents left. Items already cleaned up get one too.
  6. Cleans up closed issues (goat/cleanup.py): removes their local worktrees and goat/ branches,
     and sets the label goat:done. It waits for the item's retro note, or for MAX_ATTEMPTS failed retros.

Between turns it checks its running agents every FOLLOW_UP_POLL_SECONDS. When one ends, it reads only
that item and starts the item's next agent at once (a follow-up), with the same limits as a turn. So an
item moves stage after stage until it stops at a human gate, a question or a chat.

While items wait for a slot (the last turn or a follow-up skipped one for a slot limit), a run that ends
gets no follow-up. The coordinator runs a full early turn instead, so the normal order picks what fills
the slot. goat/early_ticks.py limits how often, and backs off after a GitHub error.

The coordinator writes to GitHub in two places: the label goat:done in the cleanup, and, when a person merged
a PR, the move to stage:staging (goat/merge.py, "autoAdvanceMerged" in the config). Agents claim, post and
release with goat.py.

Usage (from any folder; goat.py commands are in goat/cli.py):
  python3 ~/.goat-ai/goat.py run --once --dry-run   # show what it would start
  python3 ~/.goat-ai/goat.py run                    # loop until Ctrl-C
  python3 ~/.goat-ai/goat.py run --loop-count 5 --loop-interval 300   # 5 turns, 5 minutes apart
  python3 ~/.goat-ai/goat.py run --delay 60         # wait 60 seconds, then loop
  python3 ~/.goat-ai/goat.py status                 # agents it started
  python3 ~/.goat-ai/goat.py stop                   # interrupt those agents

While it waits between turns, press r to run a turn now.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

from goat import discord as discord_client
from goat import health, messenger, team, worktrees
from goat.board import DONE, STAGES, STALLED, GoatItem, label_changes, same_label
from goat.choose import (DEFAULT_RETRO_CAP, RETRO_ROLE, STAGE_PRIORITY, choose_launches, choose_retros,
                         is_slot_limit, retro_candidates, MAX_ATTEMPTS, stalled_items)
from goat.cleanup import clean_up
from goat.early_ticks import EarlyTicks, TickResult
from goat.config import (COORDINATOR_STATE_FILE, REPO_ROOT, ConfigError, board_env_names, load_board, load_shared,
                         main_checkout, project_env)
from goat.github import GitHubClient, GitHubError
from goat.keys import KeyListener
from goat.merge import advance_merged
from goat.notify import notify
from goat.retro_launch import (AGGREGATE_LABEL, AGGREGATE_ROLE, aggregate_text, choose_aggregate,
                               fill_retro_defaults, has_retro_note, retro_cleanup_ids)
from goat.views import all_items, only_goat_items

CONFIG_FILE = os.path.expanduser("~/.goat-ai/config.json")
STATE_FILE = COORDINATOR_STATE_FILE
STATE_DIR = os.path.dirname(STATE_FILE)
LOG_DIR = os.path.join(STATE_DIR, "logs")
AGENT_SETTINGS = os.path.join(REPO_ROOT, "agent-settings.json")

DEFAULT_MAX_AGENTS = 3
DEFAULT_INTERVAL_SECONDS = 900
# A person's merged PR moves its issue to stage:staging. Set "autoAdvanceMerged": false to turn it off.
DEFAULT_AUTO_ADVANCE_MERGED = True
# Desktop notifications (macOS or Linux) when an issue stalls. Set "notifications": false to turn them off.
DEFAULT_NOTIFICATIONS = True
# How often the wait between turns checks for finished agents, to start follow-ups.
FOLLOW_UP_POLL_SECONDS = 15
# Keys that run a turn now while the coordinator waits between turns.
TRIGGER_KEYS = ("r", "R")

# Child processes started by this coordinator process, by pid. poll() on them also reaps them.
children = {}

# The heartbeat of this coordinator run (goat/health.py). None except inside command_run, and not in a dry run.
current_beat = None


def log(message):
    stamp = datetime.now().strftime("%H:%M:%S")
    print("[{}] {}".format(stamp, message), flush=True)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- Config and state ----------

def load_config():
    """The developer config, with coordinator defaults filled in. Exits with a message when it is not usable."""
    if not os.path.exists(CONFIG_FILE):
        sys.exit("No {}. Run scripts/install.sh <handle> first.".format(CONFIG_FILE))
    with open(CONFIG_FILE) as f:
        config = json.load(f)
    if not config.get("repos"):
        sys.exit("{} has no repos. Add the local checkout of each project repo.".format(CONFIG_FILE))
    fill_coordinator_defaults(config)
    return config


def fill_coordinator_defaults(config):
    """Fills in the coordinator defaults and checks the role caps. Exits with a message when they are not usable."""
    coordinator = config.setdefault("coordinator", {})
    coordinator.setdefault("maxAgents", DEFAULT_MAX_AGENTS)
    coordinator.setdefault("intervalSeconds", DEFAULT_INTERVAL_SECONDS)
    # None means no per-repo limit. Set it to 1 for a repo whose agents would share local services.
    coordinator.setdefault("maxAgentsPerRepo", None)
    # A role that is not listed has no limit of its own. scripts/install.sh writes default caps.
    coordinator.setdefault("maxAgentsByRole", {})
    coordinator.setdefault("autoAdvanceMerged", DEFAULT_AUTO_ADVANCE_MERGED)
    coordinator.setdefault("notifications", DEFAULT_NOTIFICATIONS)
    check_role_caps(coordinator["maxAgentsByRole"])
    # The aggregate retro thresholds and repo (goat/retro_launch.py).
    fill_retro_defaults(coordinator, config.get("repos") or {}, CONFIG_FILE)


def check_role_caps(caps):
    """Exits with a message when maxAgentsByRole is not a map of known roles to whole numbers of 1 or more."""
    where = "{}: coordinator.maxAgentsByRole".format(CONFIG_FILE)
    if not isinstance(caps, dict):
        sys.exit("{} must be an object, for example {{\"implement\": 2}}.".format(where))
    for role, cap in caps.items():
        if role not in STAGES + (RETRO_ROLE,):
            sys.exit("{} has an unknown role \"{}\". Use intake, plan, implement, review or retro.".format(where, role))
        # bool is a kind of int in Python, so check for it first.
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            sys.exit("{}.{} must be a whole number of 1 or more, not {}.".format(where, role, json.dumps(cap)))


def limits_text(coordinator_config):
    """The agent limits in one line, for example "up to 5 agents (1 per repo; per role: intake 3, plan 2)"."""
    parts = []
    per_repo = coordinator_config.get("maxAgentsPerRepo")
    if per_repo is not None:
        parts.append("{} per repo".format(per_repo))
    caps = coordinator_config.get("maxAgentsByRole") or {}
    # Roles in board order: intake first.
    role_caps = ["{} {}".format(role, caps[role]) for role in reversed(STAGE_PRIORITY) if role in caps]
    # The retro cap has a default, so it is always shown.
    role_caps.append("{} {}".format(RETRO_ROLE, caps.get(RETRO_ROLE, DEFAULT_RETRO_CAP)))
    if role_caps:
        parts.append("per role: " + ", ".join(role_caps))
    text = "up to {} agents".format(coordinator_config["maxAgents"])
    if parts:
        text += " ({})".format("; ".join(parts))
    return text


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"runs": [], "attempts": {}}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    # Keep the attempts list short. Old keys belong to item states that have moved on.
    recent = list(state["attempts"].items())[-200:]
    state["attempts"] = dict(recent)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def is_alive(pid):
    if pid in children:
        return children[pid].poll() is None
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def prune_finished(state):
    """Removes finished runs from the state, logs each one, and returns them."""
    still_running = []
    finished = []
    for run in state["runs"]:
        if is_alive(run["pid"]):
            still_running.append(run)
            continue
        exit_code = children.pop(run["pid"]).returncode if run["pid"] in children else "unknown"
        log("Finished: {} {} (exit {}). Log: {}".format(run["role"], run.get("label", run["item"]), exit_code, run["log"]))
        finished.append(run)
    state["runs"] = still_running
    return finished


# ---------- GitHub ----------

def read_boards(repos):
    """Reads G.O.A.T. issues of each repo.

    Returns (candidates, user id, boards, github failed): candidates are (repo path, GoatItem) for G.O.A.T.
    items that are not Done. boards are (repo path, env, client, board, all items) for the worktrees, the merge
    check and the cleanup. A repo whose .env or issue read fails is logged and left out, so one broken project
    does not stop the others. github failed is True when a GitHub read raised GitHubError (network, rate limit
    or gh error) in any repo; the early-turn back-off uses it. Returns (None, None, None, github failed) when
    no repo could be read.
    """
    candidates = []
    boards = []
    user_id = None
    read_any = False
    github_failed = False
    for repo, path in repos.items():
        try:
            env = project_env(path)
            board = load_board(env, path)
            client = GitHubClient(board["repo"])
            user = client.me()
            if user_id is not None and user["id"] != user_id:
                log("Skipping {}: the gh login is {}, not the other repos' user.".format(repo, user["name"]))
                continue
            user_id = user["id"]
            items = all_items(client, board)
            # Done items have no next step. Leaving them out keeps them out of the "Not starting" log lines.
            candidates += [(path, item) for item in only_goat_items(items) if not same_label(item.goat_status, DONE)]
            boards.append((path, env, client, board, items))
            read_any = True
        except (ConfigError, GitHubError) as error:
            log("Skipping {}: {}".format(repo, error))
            if isinstance(error, GitHubError):
                github_failed = True
    if not read_any:
        return None, None, None, github_failed
    return candidates, user_id, boards, github_failed


def read_item(repo_path, item_id):
    """Reads one issue with the repo's .env. Returns (GoatItem, user id, env).

    The follow-up uses it so that a finished run reads only its own issue, not all of them.
    Raises ConfigError or GitHubError.
    """
    env = project_env(repo_path)
    board = load_board(env, repo_path)
    client = GitHubClient(board["repo"])
    user_id = client.me()["id"]
    item = GoatItem.from_github(client.get_item(item_id))
    return item, user_id, env


def discord_webhook(config):
    """The Discord webhook from ~/.goat-ai/discord.env, or None when it is not set up or turned off.

    "discord": false in the coordinator config turns the messages off. A bad URL is logged and ignored."""
    if config["coordinator"].get("discord", True) is False:
        return None
    try:
        return discord_client.from_settings(discord_client.settings())
    except discord_client.DiscordError as error:
        log("Discord: {}".format(error))
        return None


def say(webhook, message, what):
    """Sends (username, content, embed) to Discord. A failure is logged and never stops the coordinator."""
    if webhook is None:
        return
    try:
        webhook.send(*message)
    except discord_client.DiscordError as error:
        log("Discord: could not send {}: {}".format(what, error))


def tell_discord(webhook, config, state, candidates, user_id, boards, now, dry_run):
    """Announces issues that newly wait for a person. See goat/messenger.py."""
    if webhook is None and not dry_run:
        return
    clients = {board[0]: board[2] for board in boards}

    def fetch_summary(repo_path, item):
        try:
            return GoatItem.from_github(clients[repo_path].get_item(item.id)).summary
        except (GitHubError, KeyError):
            return ""

    messenger.tell_waiting(webhook, candidates, user_id, state, fetch_summary, load_shared(),
                           team.owner(config)["name"], now, dry_run, log)


def advance_merged_boards(boards, user_id, dry_run, webhook=None, owner_name=""):
    """Moves the issues whose PR a person merged to stage:staging, in each repo. Logs what it did.

    With a Discord webhook, Dex also says so."""
    shared = load_shared()

    def on_moved(item, urls):
        say(webhook, messenger.dispatcher_message(
            "the PR for #{} is merged. Close the issue when it's finished: that starts the retro and the cleanup.".format(
                item.id), shared, owner_name, "#{} {}".format(item.id, item.name), item.url), "the merge message")

    for path, _, client, _, items in boards:
        try:
            lines = advance_merged(client, items, user_id, datetime.now(timezone.utc), dry_run=dry_run, on_moved=on_moved)
        except GitHubError as error:
            lines = ["Merge check failed in {}: {}".format(path, error)]
        for line in lines:
            log("Merge: " + line)


def clean_up_boards(boards, user_id, running_items, dry_run, retro_wait_ids=(), retro_failed_ids=()):
    """Runs the closed-issue cleanup in each repo and logs what it did. Items with a running agent are skipped.
    Items in retro_wait_ids wait for their retro note; items in retro_failed_ids are cleaned with a note.

    Like goat.py cleanup, it uses the repo's main checkout, so a config path that points at a worktree
    still finds the <repo name>-goat folder.
    """
    for path, _, client, board, items in boards:
        try:
            lines = clean_up(main_checkout(path), client, board, items, user_id, datetime.now(timezone.utc),
                             dry_run=dry_run, skip_ids=running_items, wait_ids=retro_wait_ids,
                             retro_failed_ids=retro_failed_ids)
        except OSError as error:
            lines = ["Cleanup failed in {}: {}".format(path, error)]
        for line in lines:
            log("Cleanup: " + line)


# ---------- Agents ----------

def agent_env():
    """The environment for headless sessions, with the headless flag the protocol reads."""
    env = dict(os.environ)
    env["GOAT_HEADLESS"] = "1"
    # Each session gets its own id. Do not pass on the id of a session that started the coordinator.
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return env


def prepare_worktree(launch, env, fetch_errors):
    """Makes or refreshes the launch's worktree and copies the env files. Returns the worktree path.

    Fetches each repo at most once per turn. fetch_errors (repo -> error, or None after a good fetch) is
    shared across one turn's launches, so a failed fetch skips the repo's other launches too.
    Raises WorktreeError, or OSError from a failed git or file step.
    """
    repo = main_checkout(launch["repoPath"])
    if repo not in fetch_errors:
        try:
            worktrees.fetch(repo)
            fetch_errors[repo] = None
        except worktrees.WorktreeError as error:
            fetch_errors[repo] = error
    if fetch_errors[repo] is not None:
        raise fetch_errors[repo]
    paths = worktrees.env_file_paths(env)
    path = worktrees.prepare(repo, launch["ref"])
    for line in worktrees.copy_env_files(repo, path, paths):
        log("{}: {}".format(launch["ref"], line))
    return path


def start_agent(launch, worktree):
    """Starts one agent in the background, in `worktree` (the item's worktree, or the main checkout for a
    retro), and returns its run record."""
    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = os.path.join(LOG_DIR, "{}-{}-{}.json".format(stamp, launch["item"], launch["role"]))
    command = [
        # The aggregate retro has no item, so its ref is empty and the command has no argument.
        "claude", "-p", "/goat-{} {}".format(launch["role"], launch["ref"]).strip(),
        "--model", launch["model"],
        "--permission-mode", "auto",
        "--permission-prompts", "none",
        "--settings", AGENT_SETTINGS,
        "--output-format", "json",
    ]
    with open(log_path, "w") as log_file:
        # A new session (process group) keeps agents running when the coordinator gets Ctrl-C.
        process = subprocess.Popen(
            command,
            cwd=worktree,
            env=agent_env(),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    children[process.pid] = process
    return {
        "item": launch["item"],
        "label": launch["label"],
        "role": launch["role"],
        "model": launch["model"],
        "pid": process.pid,
        "startedAt": now_iso(),
        "log": log_path,
        "repoPath": launch["repoPath"],
        "worktree": worktree,
    }


def check_repos(config):
    """Logs repos in the config whose local path is missing. Returns the usable ones."""
    usable = {}
    for repo, path in config["repos"].items():
        expanded = os.path.expanduser(path)
        if os.path.isdir(expanded):
            usable[repo] = expanded
        else:
            log("Config repos: {} points to {}, which does not exist.".format(repo, path))
    return usable


# ---------- Commands ----------

def slot_counts(config, state):
    """Returns (free slots, repo path -> agents running there, role -> agents running in it) for the runs
    in the state."""
    free_slots = max(0, config["coordinator"]["maxAgents"] - len(state["runs"]))
    running_per_repo = {}
    running_per_role = {}
    for run in state["runs"]:
        # Runs saved before worktrees have no repoPath. They count toward maxAgents only.
        repo_path = run.get("repoPath")
        if repo_path:
            running_per_repo[repo_path] = running_per_repo.get(repo_path, 0) + 1
        role = run.get("role")
        if role:
            running_per_role[role] = running_per_role.get(role, 0) + 1
    return free_slots, running_per_repo, running_per_role


def start_launches(launches, envs, state, dry_run):
    """Prepares each launch's worktree and starts its agent. Adds the runs and attempts to the state.

    Retros need no worktree: they read and save through goat.py, so they start in the repo's main checkout.
    envs: repo path -> that repo's .env, for the env files to copy into the worktree.
    """
    fetch_errors = {}
    for launch in launches:
        needs_worktree = launch["role"] not in (RETRO_ROLE, AGGREGATE_ROLE)
        if dry_run:
            path = main_checkout(launch["repoPath"])
            if needs_worktree:
                path = worktrees.worktree_path(path, launch["ref"])
            log("Would start: /goat-{} {} on {} in {}".format(launch["role"], launch["label"], launch["model"], path))
            continue
        try:
            if needs_worktree:
                path = prepare_worktree(launch, envs[launch["repoPath"]], fetch_errors)
            else:
                path = main_checkout(launch["repoPath"])
        except (worktrees.WorktreeError, OSError) as error:
            # Not counted as an attempt: the agent did not run. OSError covers a failed git worktree list,
            # makedirs or copy; catching it keeps this turn's earlier runs for save_state.
            log("Not starting {}: {}".format(launch["label"], error))
            continue
        description = "/goat-{} {} on {} in {}".format(launch["role"], launch["label"], launch["model"], path)
        run = start_agent(launch, path)
        state["runs"].append(run)
        state["attempts"][launch["attemptKey"]] = state["attempts"].get(launch["attemptKey"], 0) + 1
        log("Started: {} (pid {})".format(description, run["pid"]))


def retro_items_of(boards, user_id, now):
    """(repo path, GoatItem) for each closed issue that may need a retro, from every repo read this turn."""
    found = []
    for path, _, _, board, items in boards:
        for item in retro_candidates(items, board["doneLabel"], user_id, now):
            found.append((path, item))
    return found


def latest_log(item_id, role):
    """The newest coordinator log file of this item and role, or None."""
    try:
        names = sorted(name for name in os.listdir(LOG_DIR) if name.endswith("-{}-{}.json".format(item_id, role)))
    except OSError:
        return None
    return os.path.join(LOG_DIR, names[-1]) if names else None


def stall_comment(item, role, log_path, shared=None):
    """The comment that tells the owner an issue stopped, and how to restart it."""
    mention = "@{} ".format(item.owner_ids[0]) if item.owner_ids else ""
    lines = [
        "**🤖 {} · stalled**".format(team.dispatcher_signature(shared)),
        "",
        "{}The {} agent ran {} times on this issue and nothing changed each time, so I stopped retrying instead of "
        "spending more.".format(mention, role, MAX_ATTEMPTS),
        "",
        "- Stage: {}".format(item.status or "none"),
        "- Goat status before: {}".format(item.goat_status or "none"),
        "- Latest agent log: `{}`".format(log_path) if log_path else "- No agent log was found on this machine.",
        "",
        "To retry, replace the label `goat:stalled` with `goat:ready`. If you can see what went wrong, add a comment "
        "that says what to change first: the agent reads the comments.",
    ]
    return "\n".join(lines)


def escalate_stalled(candidates, user_id, boards, running_items, attempts, now, config, dry_run, webhook=None):
    """Stops each stalled issue visibly: sets goat:stalled, comments for the owner, and shows a desktop notification.

    The label goes first. If it cannot be set (for example the repo has no goat:stalled label yet), nothing else is
    posted, and the next turn tries again without spamming comments.
    """
    shared = load_shared()
    clients = {board[0]: board[2] for board in boards}
    for repo_path, item, role in stalled_items(candidates, user_id, running_items, attempts, now):
        name = "#{} {}".format(item.id, item.name[:60])
        if dry_run:
            log("Would mark {} stalled: the {} agent ran {} times and changed nothing.".format(name, role, MAX_ATTEMPTS))
            continue
        client = clients.get(repo_path)
        if client is None:
            continue
        try:
            add, remove = label_changes(item, goat_status=STALLED)
            client.set_labels(item.id, add=add, remove=remove)
        except GitHubError as error:
            log("Could not mark {} stalled: {}. If the label is missing, run `goat.py setup-repo` in {}.".format(
                name, error, repo_path))
            continue
        log("Stalled: {} ({} agent ran {} times and changed nothing). Set goat:ready to retry.".format(
            name, role, MAX_ATTEMPTS))
        try:
            client.create_comment(item.id, stall_comment(item, role, latest_log(item.id, role), shared))
        except GitHubError as error:
            log("Marked {} stalled, but could not comment: {}".format(name, error))
        if config["coordinator"].get("notifications", DEFAULT_NOTIFICATIONS):
            boss = team.owner(config)["name"]
            notify("{}: stalled".format(team.dispatcher_name(shared)),
                   "{}{} needs you. Agents ran {} times and changed nothing.".format(
                       boss + ", " if boss else "", name, MAX_ATTEMPTS))
        say(webhook, messenger.dispatcher_message(
            "{} stalled: the {} agent ran {} times and changed nothing, so I stopped retrying. Check the agent log, "
            "then set the label goat:ready to retry.".format(name, role, MAX_ATTEMPTS), shared,
            team.owner(config)["name"], "#{} {}".format(item.id, item.name), item.url), "the stalled message")


def tick(config, state, dry_run):
    """Runs one full turn. Returns a TickResult for the early-turn logic."""
    prune_finished(state)
    repos = check_repos(config)
    candidates, user_id, boards, github_failed = read_boards(repos)
    if candidates is None:
        log("No repo could be read. Nothing started.")
        return TickResult(slot_skipped=False, github_failed=github_failed)
    running_items = {run["item"] for run in state["runs"]}
    now = datetime.now(timezone.utc)
    webhook = None if dry_run else discord_webhook(config)
    if config["coordinator"].get("autoAdvanceMerged", DEFAULT_AUTO_ADVANCE_MERGED):
        advance_merged_boards(boards, user_id, dry_run, webhook, team.owner(config)["name"])
    retro_items = retro_items_of(boards, user_id, now)
    retro_wait_ids, retro_failed_ids = retro_cleanup_ids(retro_items, state["attempts"])
    clean_up_boards(boards, user_id, running_items, dry_run, retro_wait_ids, retro_failed_ids)
    escalate_stalled(candidates, user_id, boards, running_items, state["attempts"], now, config, dry_run, webhook)
    tell_discord(webhook, config, state, candidates, user_id, boards, now, dry_run)
    free_slots, running_per_repo, running_per_role = slot_counts(config, state)
    max_per_repo = config["coordinator"]["maxAgentsPerRepo"]
    max_per_role = config["coordinator"]["maxAgentsByRole"]

    launches, skipped = choose_launches(
        candidates, user_id, running_items, state["attempts"], free_slots, now,
        running_per_repo=running_per_repo, max_per_repo=max_per_repo,
        running_per_role=running_per_role, max_per_role=max_per_role,
    )
    # Retros get the slots that the stage agents left. Count this turn's stage launches as running.
    for launch in launches:
        running_per_repo[launch["repoPath"]] = running_per_repo.get(launch["repoPath"], 0) + 1
    retro_launches, retro_skipped, retros_waiting = choose_retros(
        retro_items, has_retro_note, running_items, state["attempts"], free_slots - len(launches),
        running_per_repo=running_per_repo, max_per_repo=max_per_repo,
        running_per_role=running_per_role, max_per_role=max_per_role,
    )
    launches += retro_launches
    skipped += retro_skipped
    for launch in retro_launches:
        running_per_repo[launch["repoPath"]] = running_per_repo.get(launch["repoPath"], 0) + 1
    aggregate, reason = choose_aggregate(config["coordinator"], repos, state, free_slots - len(launches), now,
                                         running_per_repo)
    if aggregate:
        launches.append(aggregate)
    if reason:
        skipped.append((AGGREGATE_LABEL, reason))
    for item_label, reason in skipped:
        log("Not starting {}: {}".format(item_label, reason))
    if retros_waiting:
        log("{} retro(s) wait for a free slot.".format(retros_waiting))
    if not launches:
        log("Nothing to start. {} agent(s) running.".format(len(state["runs"])))
    envs = {board[0]: board[1] for board in boards}
    start_launches(launches, envs, state, dry_run)
    slot_skipped = any(is_slot_limit(reason) for _, reason in skipped)
    return TickResult(slot_skipped=slot_skipped, github_failed=github_failed)


def follow_up(config, state, run, dry_run):
    """After a run ends, reads only its item and starts the item's next agent, if it has one.

    Uses choose_launches with the item as the only candidate, so the rules and limits are the same as a
    turn. An item at a gate, a question or a chat gets no agent. When the read fails or no slot is free,
    the item waits for the next turn. Returns True when the item was skipped for a slot limit.
    """
    if run.get("role") not in STAGES:
        # Retros end at no gate and have no next agent.
        return False
    repo_path = run.get("repoPath")
    if not repo_path:
        # Runs saved before worktrees do not say which repo's .env to read.
        return False
    label = run.get("label", run["item"])
    try:
        item, user_id, env = read_item(repo_path, run["item"])
    except (ConfigError, GitHubError) as error:
        log("Follow-up for {}: {}".format(label, error))
        return False
    if same_label(item.goat_status, DONE):
        return False
    running_items = {other["item"] for other in state["runs"]}
    free_slots, running_per_repo, running_per_role = slot_counts(config, state)
    launches, skipped = choose_launches(
        [(repo_path, item)], user_id, running_items, state["attempts"], free_slots, datetime.now(timezone.utc),
        running_per_repo=running_per_repo, max_per_repo=config["coordinator"]["maxAgentsPerRepo"],
        running_per_role=running_per_role, max_per_role=config["coordinator"]["maxAgentsByRole"],
    )
    for item_label, reason in skipped:
        log("Not starting {}: {}".format(item_label, reason))
    start_launches(launches, {repo_path: env}, state, dry_run)
    return any(is_slot_limit(reason) for _, reason in skipped)


def run_one_tick(config, state, dry_run):
    """Runs a full turn and saves the state. Returns the turn's TickResult."""
    if current_beat:
        current_beat.turn_started()
    result = tick(config, state, dry_run)
    save_state(state)
    if current_beat:
        current_beat.turn_done(result.github_failed)
    return result


def start_follow_ups(config, state, dry_run, early):
    """Handles the runs that ended. Saves the state when a run ended, so `status` in another shell stays current.

    While items wait for a slot, it runs a full early turn when `early` (EarlyTicks) allows, and no
    single-item follow-ups, so a waiting item with a higher priority gets the slot first. Otherwise it
    starts a follow-up for each ended run.
    """
    finished = prune_finished(state)
    if finished:
        early.run_ended()
    if early.waiting_for_slot:
        now = time.monotonic()
        if early.is_due(now):
            log("Early turn: an agent ended and items wait for a slot.")
            result = run_one_tick(config, state, dry_run)
            line = early.after_early_tick(result, now)
            if line:
                log(line)
        elif finished:
            save_state(state)
        return
    for run in finished:
        if follow_up(config, state, run, dry_run):
            early.follow_up_skipped_for_slot()
    if finished:
        save_state(state)


def wait_until(deadline, config, state, dry_run, keys, early):
    """Waits until `deadline` (a time.monotonic() value).

    Every FOLLOW_UP_POLL_SECONDS it handles runs that ended: follow-ups, or an early turn (start_follow_ups).
    A trigger key runs a manual turn at once. Neither one moves the deadline: when a manual turn ends after
    it, the wait ends right away.
    """
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        key = keys.wait_for_key(min(FOLLOW_UP_POLL_SECONDS, remaining))
        if current_beat:
            current_beat.alive()
        if key in TRIGGER_KEYS:
            log("Turn triggered by user (r).")
            early.after_tick(run_one_tick(config, state, dry_run))
            keys.discard_typed()
        else:
            start_follow_ups(config, state, dry_run, early)


def run_ticks(config, dry_run, count, interval, keys):
    """Run `count` turns (forever when None), `interval` seconds apart. No wait after the last turn.

    While it waits, `keys` (a KeyListener) can trigger manual turns. They do not count toward `count`
    and do not move the next scheduled turn. The wait keeps the turn's state, so its follow-ups see the
    runs that the turn just started. One EarlyTicks lives for the whole loop; every turn updates it.

    With a `count`, it logs how many scheduled turns remain after each scheduled turn.
    """
    early = EarlyTicks()
    ticks_done = 0
    while True:
        state = load_state()
        early.after_tick(run_one_tick(config, state, dry_run))
        keys.discard_typed()
        ticks_done += 1
        if count is not None:
            log("Turn {} of {} done. {} turn(s) remaining.".format(ticks_done, count, count - ticks_done))
            if ticks_done >= count:
                return
        if current_beat:
            current_beat.scheduled(interval)
        wait_until(time.monotonic() + interval, config, state, dry_run, keys, early)


def command_run(args):
    config = load_config()
    shell_board_settings = board_env_names(os.environ)
    if shell_board_settings:
        # A real environment variable wins over every project's .env, so all repos would use one GitHub repo.
        sys.exit("Unset these in the shell before you start the coordinator. Each project sets them in its own "
                 ".env: {}".format(", ".join(shell_board_settings)))
    if shutil.which("claude") is None:
        sys.exit("The claude command is not on PATH.")
    if shutil.which("gh") is None:
        sys.exit("The gh command is not on PATH. Install the GitHub CLI and run `gh auth login`.")
    os.makedirs(STATE_DIR, exist_ok=True)
    # --once wins over --loop-count: when both are given, we do the more limited run.
    count = 1 if args.once else args.loop_count
    interval = config["coordinator"]["intervalSeconds"]
    if args.loop_interval is not None:
        interval = args.loop_interval
    count_text = ", for {} turn(s)".format(count) if count is not None else ""
    log("Coordinator for {} repo(s): {}, every {} seconds{}{}.".format(
        len(config["repos"]), limits_text(config["coordinator"]), interval, count_text,
        " (dry run)" if args.dry_run else ""))
    log(aggregate_text(config["coordinator"]))
    global current_beat
    previous_handler = None
    if not args.dry_run:
        current_beat = health.Beat(os.getpid(), interval, path=health.HEARTBEAT_FILE)
        # A stop signal ends the run like Ctrl-C, so the heartbeat says "stopped on purpose".
        previous_handler = signal.signal(signal.SIGTERM, raise_keyboard_interrupt)
    try:
        if args.delay > 0:
            log("Waiting {} seconds before the first turn.".format(args.delay))
            time.sleep(args.delay)
        # The with block restores the terminal before the KeyboardInterrupt handler runs.
        with KeyListener() as keys:
            if keys.active:
                log("Press r to run a turn now.")
            run_ticks(config, args.dry_run, count, interval, keys)
    except KeyboardInterrupt:
        log("Coordinator stopped. Agents it started keep running. Use `goat.py stop` to interrupt them.")
    finally:
        if current_beat:
            current_beat.stopped()
            current_beat = None
            signal.signal(signal.SIGTERM, previous_handler)


def raise_keyboard_interrupt(signum, frame):
    raise KeyboardInterrupt


def command_status(args):
    state_name, text = health.assess(health.read(health.HEARTBEAT_FILE), health.utc_now())
    print("Coordinator: " + text)
    if os.path.exists(CONFIG_FILE):
        # Not load_config: status also works before any repo is in the config.
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        fill_coordinator_defaults(config)
        print("Limits: " + limits_text(config["coordinator"]))
    state = load_state()
    prune_finished(state)
    save_state(state)
    if not state["runs"]:
        print("No agents running.")
        return
    for run in state["runs"]:
        repo = os.path.basename(run.get("repoPath", "")) or "-"
        print("{:<20} {role:<10} {item:<14} {model:<7} pid {pid:<7} since {startedAt}  {log}".format(repo, **run))


def command_stop(args):
    state = load_state()
    prune_finished(state)
    for run in state["runs"]:
        # SIGINT lets the agent end its turn. Its claim then expires and the issue shows as stale.
        try:
            os.killpg(run["pid"], signal.SIGINT)
            log("Interrupted {} {} (pid {}).".format(run["role"], run["item"], run["pid"]))
        except OSError as error:
            log("Could not interrupt pid {}: {}".format(run["pid"], error))
    save_state(state)

