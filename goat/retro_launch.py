"""Retro helpers for the coordinator (goat/coordinator.py).

- Which Done items wait for their retro note. choose.py picks the retro launches; this module reads the local
  note files, so choose.py stays pure.
- The aggregate retro (skills/goat-retro-aggregate): its config, the repo it runs in, and when it starts.
"""

import json
import os
import sys

from goat import retros
from goat.choose import MAX_ATTEMPTS, retro_attempts_used
from goat.config import REPO_ROOT, main_checkout

AGGREGATE_ROLE = "retro-aggregate"
AGGREGATE_LABEL = "aggregate retro"
# Skip reasons when no slot is free. A later turn starts the aggregate.
AGGREGATE_WAITS = "it waits for a free agent slot"
AGGREGATE_WAITS_FOR_REPO = "it waits for a free agent slot in its repo"
DEFAULT_AGGREGATE_MIN_RETROS = 15
DEFAULT_AGGREGATE_MIN_DAYS = 3


def has_retro_note(item):
    # retros.RETROS_DIR is read here, not bound as a default, so tests can patch it.
    return retros.has_note(item.board_id, item.id, retros.RETROS_DIR)


def retro_cleanup_ids(retro_items, attempts):
    """Returns (wait ids, failed ids) for the cleanup. Wait: no retro note yet and tries left. Failed: no
    note after MAX_ATTEMPTS retros, so the cleanup goes on without it."""
    wait_ids = set()
    failed_ids = set()
    for _, item in retro_items:
        if has_retro_note(item):
            continue
        if retro_attempts_used(item, attempts):
            failed_ids.add(item.id)
        else:
            wait_ids.add(item.id)
    return wait_ids, failed_ids


# ---------- Aggregate retro ----------

def fill_retro_defaults(coordinator_config, repos, where):
    """Fills in coordinator.retro and checks it. Exits with a message when it is not usable.

    aggregateMinRetros and aggregateMinDays: whole numbers of 1 or more. repo: optional, a key of `repos`.
    where: the config file, for the messages.
    """
    retro = coordinator_config.setdefault("retro", {})
    where = "{}: coordinator.retro".format(where)
    if not isinstance(retro, dict):
        sys.exit("{} must be an object, for example {{\"aggregateMinRetros\": 15}}.".format(where))
    retro.setdefault("aggregateMinRetros", DEFAULT_AGGREGATE_MIN_RETROS)
    retro.setdefault("aggregateMinDays", DEFAULT_AGGREGATE_MIN_DAYS)
    for key in ("aggregateMinRetros", "aggregateMinDays"):
        value = retro[key]
        # bool is a kind of int in Python, so check for it first.
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            sys.exit("{}.{} must be a whole number of 1 or more, not {}.".format(where, key, json.dumps(value)))
    repo = retro.get("repo")
    if repo is not None and repo not in repos:
        sys.exit("{}.repo is {}, which is not in repos. Use the repos key of the G.O.A.T. AI repo.".format(
            where, json.dumps(repo)))


def aggregate_text(coordinator_config):
    """The aggregate rule in one line, for the coordinator's start log."""
    retro = coordinator_config["retro"]
    return "Aggregate retro when {} notes wait and {} days passed since the last one.".format(
        retro["aggregateMinRetros"], retro["aggregateMinDays"])


def aggregate_repo_path(coordinator_config, repos):
    """The repo path where the aggregate retro runs: coordinator.retro.repo, else the repos entry whose main
    checkout is this G.O.A.T. install's repo. None when no repo matches.

    It runs in the G.O.A.T. AI repo, so its `goat.py add` puts tasks in G.O.A.T.'s own GitHub repo.
    """
    name = coordinator_config["retro"].get("repo")
    if name:
        return repos.get(name)
    goat_repo = os.path.realpath(main_checkout(REPO_ROOT))
    for path in repos.values():
        if os.path.realpath(main_checkout(path)) == goat_repo:
            return path
    return None


def aggregate_attempt_key(pending_count, last_at_text):
    """Changes when a new note waits or an aggregate saves a report, so a failed aggregate is tried again then."""
    return "{}:{}:{}".format(AGGREGATE_ROLE, last_at_text or "never", pending_count)


def choose_aggregate(coordinator_config, repos, state, free_slots, now, running_per_repo=None):
    """Returns (launch, skip reason) for the aggregate retro. Both are None when it is not due or is running.

    repos: repo key -> local path, from check_repos. free_slots: the slots left after this turn's launches.
    running_per_repo: repo path -> agents running there, with this turn's launches, for maxAgentsPerRepo.
    A bad retros/state.json gives a skip reason, so it cannot stop the coordinator.
    The slot reasons are not slot limits for early turns (choose.is_slot_limit): the aggregate is not urgent.
    """
    retro = coordinator_config["retro"]
    try:
        pending = retros.pending_notes(retros.RETROS_DIR)
        last_at = retros.last_aggregate_at(retros.RETROS_DIR)
        last_at_text = retros.read_state(retros.RETROS_DIR)["lastAggregateAt"]
    except (OSError, ValueError) as error:
        return None, "cannot read {}: {}".format(retros.state_path(retros.RETROS_DIR), error)
    if not retros.aggregate_due(len(pending), last_at, now, retro["aggregateMinRetros"], retro["aggregateMinDays"]):
        return None, None
    for run in state["runs"]:
        if run.get("role") == AGGREGATE_ROLE:
            return None, None
    key = aggregate_attempt_key(len(pending), last_at_text)
    if state["attempts"].get(key, 0) >= MAX_ATTEMPTS:
        return None, "{} aggregate retro runs saved no report; check the logs".format(MAX_ATTEMPTS)
    path = aggregate_repo_path(coordinator_config, repos)
    if path is None:
        return None, "no repo in the config is the G.O.A.T. AI repo; set coordinator.retro.repo"
    if free_slots < 1:
        return None, AGGREGATE_WAITS
    max_per_repo = coordinator_config.get("maxAgentsPerRepo")
    if max_per_repo is not None and (running_per_repo or {}).get(path, 0) >= max_per_repo:
        return None, AGGREGATE_WAITS_FOR_REPO
    launch = {
        "item": AGGREGATE_ROLE,
        "ref": "",
        "label": AGGREGATE_LABEL,
        "role": AGGREGATE_ROLE,
        "model": "opus",
        "repoPath": path,
        "attemptKey": key,
    }
    return launch, None
