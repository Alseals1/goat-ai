"""Which agents the coordinator starts on this turn. Pure logic: no GitHub reads, no processes.

The coordinator (goat/coordinator.py) reads each project's GitHub issues and its own run state,
then calls choose_launches() for the stage agents and choose_retros() for the retro agents.
Keeping the choice here makes it easy to read and to test.
"""

from goat.board import WORKING, next_role, priority_rank, same_label

# Later stages first, so work in progress finishes before new work starts. Priority comes before this.
STAGE_PRIORITY = ["review", "implement", "plan", "intake"]

# A launch whose agent changed nothing on the item is tried again at most this often.
MAX_ATTEMPTS = 2

# The retro agent (skills/goat-retro) is a coordinator role, not a board stage. It reviews one Done item.
RETRO_ROLE = "retro"
# The retro cap when maxAgentsByRole has no "retro" key. Retros are not urgent, so stage agents keep the slots.
DEFAULT_RETRO_CAP = 1

# Skip reasons for slot limits. An early turn (goat/early_ticks.py) runs only while items wait for one of these.
# Retros are left out: they are not urgent, and choose_retros counts them instead of giving skip reasons.
SLOTS_FULL = "all agent slots are in use"
REPO_SLOTS_FULL = "all agent slots for this repo are in use"


def role_slots_full(role):
    return "all {} slots are in use".format(role)


def is_slot_limit(reason):
    """True when a skip reason is one of the slot limits: overall, repo or role."""
    role_reasons = [role_slots_full(role) for role in STAGE_PRIORITY]
    return reason in [SLOTS_FULL, REPO_SLOTS_FULL] + role_reasons


def model_for(role, size):
    """The model for a role. Intake and retro use Sonnet. Implement depends on the size: Sonnet for S, Opus for M, L, XL, or no size yet."""
    if role in ("intake", RETRO_ROLE):
        return "sonnet"
    if role == "implement" and size == "S":
        return "sonnet"
    return "opus"


def attempt_key(item):
    """Identifies one state of an item. An agent that does any work changes it, so the key changes.

    For a stale claim (Working, expired), updated_at is left out: each takeover writes a new claim, so
    updated_at always changes, and an agent that keeps failing would otherwise be started again forever.
    """
    key = "{}:{}:{}".format(item.id, item.status, item.goat_status)
    if same_label(item.goat_status, WORKING):
        return key
    return "{}:{}".format(key, item.updated_at)


def skip_reason(item, role, running_items, attempts):
    """Why the coordinator does not start an agent for this item now, or None when it can."""
    if role is None:
        return "goat status is {}".format(item.goat_status or "not set")
    if item.id in running_items:
        return "an agent is already running"
    if attempts.get(attempt_key(item), 0) >= MAX_ATTEMPTS:
        return "{} agent runs changed nothing; check the logs".format(MAX_ATTEMPTS)
    return None


def stalled_items(candidates, user_id, running_items, attempts, now):
    """The user's issues that the coordinator gave up on: an agent is due, none is running, and agents already ran
    MAX_ATTEMPTS times without changing the issue. Returns [(repo path, GoatItem, role)].

    The coordinator tells the owner and sets goat:stalled, so the issue stops here instead of waiting unseen.
    """
    found = []
    for repo_path, item in candidates:
        if not item.is_owned_by(user_id) or item.id in running_items:
            continue
        role = next_role(item, now)
        if role is not None and attempts.get(attempt_key(item), 0) >= MAX_ATTEMPTS:
            found.append((repo_path, item, role))
    return found


def choose_launches(candidates, user_id, running_items, attempts, free_slots, now,
                    running_per_repo=None, max_per_repo=None, running_per_role=None, max_per_role=None):
    """Returns (launches, skipped).

    candidates: (repo path, GoatItem) for G.O.A.T. items on each project's board.
    running_per_repo: repo path -> number of agents running there now.
    max_per_repo: the most agents at once in one repo, or None for no limit.
    running_per_role: role -> number of agents running in that role now.
    max_per_role: role -> the most agents at once in that role. A role that is not in it has no limit.
    The limits are checked in this order: overall (free_slots), repo, role. The skip reason names the first one hit.
    launches: up to free_slots dicts with item, ref, role, model, repo path and attempt key, in start order.
      ref is the item's worktree folder name and the skill argument: the issue number.
    skipped: (item label, reason) for this user's items that are not started, so the log can say why.
    """
    ready = []
    skipped = []
    for repo_path, item in candidates:
        if not item.is_owned_by(user_id):
            continue
        role = next_role(item, now)
        reason = skip_reason(item, role, running_items, attempts)
        if reason is None:
            ready.append((repo_path, item, role))
        else:
            skipped.append((label(item), reason))

    # Priority first, then later stage, then oldest.
    ready.sort(key=lambda entry: (
        priority_rank(entry[1].priority), STAGE_PRIORITY.index(entry[2]), entry[1].updated_at, entry[1].id))
    launches = []
    # Launches chosen on this turn count toward their repo and role too.
    per_repo = dict(running_per_repo or {})
    per_role = dict(running_per_role or {})
    role_caps = max_per_role or {}
    for repo_path, item, role in ready:
        if len(launches) >= free_slots:
            skipped.append((label(item), SLOTS_FULL))
            continue
        if max_per_repo is not None and per_repo.get(repo_path, 0) >= max_per_repo:
            skipped.append((label(item), REPO_SLOTS_FULL))
            continue
        if role in role_caps and per_role.get(role, 0) >= role_caps[role]:
            skipped.append((label(item), role_slots_full(role)))
            continue
        per_repo[repo_path] = per_repo.get(repo_path, 0) + 1
        per_role[role] = per_role.get(role, 0) + 1
        launches.append({
            "item": item.id,
            "ref": item.id,
            "label": label(item),
            "role": role,
            "model": model_for(role, item.size),
            "repoPath": repo_path,
            "attemptKey": attempt_key(item),
        })
    return launches, skipped


def retro_candidates(items, done_label, user_id, now):
    """This user's goat issues that are closed (stage done) and have no live claim. Items with goat:done count too:
    they were cleaned up before retros existed, and get a retro now (the backfill)."""
    return [item for item in items
            if item.is_owned_by(user_id) and item.goat_touched and same_label(item.status, done_label)
            and not item.claim_is_live(now)]


def retro_attempt_key(item):
    """One key per item: a retro that ends with no note counts toward MAX_ATTEMPTS, whatever the item's state."""
    return "{}:{}".format(RETRO_ROLE, item.id)


def retro_attempts_used(item, attempts):
    return attempts.get(retro_attempt_key(item), 0) >= MAX_ATTEMPTS


def choose_retros(candidates, has_note, running_items, attempts, free_slots,
                  running_per_repo=None, max_per_repo=None, running_per_role=None, max_per_role=None):
    """Returns (launches, skipped, waiting) for retro agents. launches and skipped have the same shape as in
    choose_launches.

    candidates: (repo path, GoatItem) for this user's Done goat items with no live claim.
    has_note: a function of a GoatItem, True when its retro note exists. Those items need no retro.
    free_slots and the counts are what is left after this turn's stage launches, so stage agents go first.
    The retro cap is max_per_role["retro"], else DEFAULT_RETRO_CAP.
    Items whose retro failed MAX_ATTEMPTS times are left out with no skip line: the cleanup logs them once.
    waiting: the number of retros not started for a slot limit (overall, repo or retro cap). They get no
      skip line each, so a backfill of many Done items gives one log line, and no early turns.
    """
    ready = []
    skipped = []
    for repo_path, item in candidates:
        if has_note(item) or retro_attempts_used(item, attempts):
            continue
        if item.id in running_items:
            skipped.append((label(item), "an agent is already running"))
            continue
        ready.append((repo_path, item))

    # Oldest first, so the backfill of old Done items goes in board order.
    ready.sort(key=lambda entry: (entry[1].updated_at, entry[1].id))
    launches = []
    per_repo = dict(running_per_repo or {})
    retro_cap = (max_per_role or {}).get(RETRO_ROLE, DEFAULT_RETRO_CAP)
    retros_running = (running_per_role or {}).get(RETRO_ROLE, 0)
    waiting = 0
    for repo_path, item in ready:
        slots_full = len(launches) >= free_slots
        repo_full = max_per_repo is not None and per_repo.get(repo_path, 0) >= max_per_repo
        retro_cap_full = retros_running + len(launches) >= retro_cap
        if slots_full or repo_full or retro_cap_full:
            waiting += 1
            continue
        per_repo[repo_path] = per_repo.get(repo_path, 0) + 1
        launches.append({
            "item": item.id,
            "ref": item.id,
            "label": label(item),
            "role": RETRO_ROLE,
            "model": model_for(RETRO_ROLE, item.size),
            "repoPath": repo_path,
            "attemptKey": retro_attempt_key(item),
        })
    return launches, skipped, waiting


def label(item):
    """How logs name an item: its issue number and name."""
    return "#{} {}".format(item.id, item.name[:40])
