"""Autopilot: G.O.A.T. carries a repo's issues from backlog to closed with no person at the gates.

It is on only for the repos listed in the local config (~/.goat-ai/config.json):

    "autopilot": {"repos": ["owner/name"], "inFlight": 2, "maxStartsPerDay": 10, "mergeMethod": "squash", "qa": true}

For those repos, on every turn, the coordinator itself (code, with the owner's gh login, never an agent):

1. **Approves gates.** The size gate at once. The plan gate once every issue in the issue's "Depends on" line is closed.
2. **Merges.** An issue at goat:merge-review has passed the Review agent. When its PR is not a draft, GitHub says it
   merges cleanly and no check is failing or pending, the coordinator merges it into the default branch. A stack is
   merged one part per turn, bottom first, each part re-targeted to the default branch once the part below is in.
   A failing check or a conflict sends the issue back to Implement with a note, at most MAX_SEND_BACKS times; after
   that, and for anything it cannot judge, it stops and leaves the PR to a person (a "hold").
   It never merges into a branch named in NEVER_MERGE_INTO, so promoting to production stays a person's step.
3. **Tests the merge.** goat/merge.py moves the merged issue to stage:staging. The coordinator then starts the QA
   agent (skills/goat-qa) on the merged default branch, and closes the issue when QA has run. Closing starts the retro
   and the cleanup, as when a person closes it. QA files what it finds as backlog issues.
4. **Starts the next work.** While fewer than `inFlight` issues are in the pipeline, it starts the project manager
   agent (skills/goat-pm). The PM reads `goat.py backlog` and records its choices with `goat.py pick`, which only
   writes a local file. When the PM ends, the coordinator checks each pick again and starts it, like `start <n>` in
   Discord: assigned to the owner, stage:intake, goat:ready. At most `maxStartsPerDay` issues are started in a day.

People are still needed for questions, chats, stalled issues, holds and anything the agents ask. Agents still never
merge, approve or change a gate label; the deny rules in agent-settings.json are unchanged.
"""

import json
import os
import re
from datetime import datetime, timezone

from goat import board as fb
from goat import team
from goat.github import GitHubError, ItemNotFound
from goat.merge import branch_item_id

DEFAULTS = {"repos": [], "inFlight": 2, "maxStartsPerDay": 10, "mergeMethod": "squash", "qa": True}
MERGE_METHODS = ("squash", "merge", "rebase")
# The autopilot never merges into these. Promoting the default branch to production is a person's step.
NEVER_MERGE_INTO = ("main", "master")
# How often the autopilot sends one issue back to Implement for a failing check or a conflict before it holds.
MAX_SEND_BACKS = 2
# How long a PR in a repo with CI may sit with no check at all before the autopilot holds it for a person.
NO_CHECKS_MINUTES = 30
# How often QA is tried on one merged issue before the autopilot gives up and tells the owner.
MAX_QA_TRIES = 2

QA_ROLE = "qa"
PM_ROLE = "pm"
ROLES = (QA_ROLE, PM_ROLE)
# The role caps when maxAgentsByRole has none: one QA (it starts the app on a port) and one PM per turn.
DEFAULT_ROLE_CAPS = {QA_ROLE: 1, PM_ROLE: 1}
MODELS = {QA_ROLE: "sonnet", PM_ROLE: "opus"}

# Issues waiting on a person do not count as in flight: the pipeline keeps moving around them.
WAITING_ON_PERSON = (fb.ANSWER_QUESTION, fb.CHAT_WITH_AGENT, fb.STALLED)

# Hidden first lines of the autopilot's comments.
APPROVED_MARKER = "<!-- goat:autopilot-approved -->"
MERGED_MARKER = "<!-- goat:autopilot-merged -->"
SEND_BACK_MARKER = "<!-- goat:autopilot-sendback -->"
HOLD_MARKER = "<!-- goat:autopilot-hold -->"
QA_MARKER = "<!-- goat:autopilot-qa -->"
STARTED_MARKER = "<!-- goat:autopilot-started -->"

# The comment a Review agent's handoff starts with (see team.signature and the protocol's post headers).
REVIEW_HANDOFF = "Review agent · handoff"

PICKS_DIR = os.path.expanduser("~/.goat-ai/coordinator/picks")
# Longest QA text the coordinator copies onto the issue. The full report stays in ~/.goat-ai/qa/.
QA_TEXT_LIMIT = 6000


class SettingsError(ValueError):
    pass


# ---------- Settings ----------

def settings(config):
    """The "autopilot" section of the local config with defaults filled in. Raises SettingsError when it is not usable.

    Every repo listed must also be in the config's "repos", so a typo cannot quietly leave autopilot off."""
    raw = config.get("autopilot")
    if raw is None:
        return dict(DEFAULTS, repos=[])
    if not isinstance(raw, dict):
        raise SettingsError("autopilot must be an object, for example {\"repos\": [\"owner/name\"]}.")
    found = dict(DEFAULTS)
    found.update(raw)
    repos = found["repos"]
    if not isinstance(repos, list) or not all(isinstance(repo, str) and "/" in repo for repo in repos):
        raise SettingsError("autopilot.repos must be a list of \"owner/name\" repos.")
    known = [name.lower() for name in (config.get("repos") or {})]
    for repo in repos:
        if repo.lower() not in known:
            raise SettingsError("autopilot.repos lists {}, which is not in \"repos\".".format(repo))
    for key in ("inFlight", "maxStartsPerDay"):
        value = found[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SettingsError("autopilot.{} must be a whole number of 0 or more, not {}.".format(key, json.dumps(value)))
    if found["mergeMethod"] not in MERGE_METHODS:
        raise SettingsError("autopilot.mergeMethod must be one of: {}.".format(", ".join(MERGE_METHODS)))
    if not isinstance(found["qa"], bool):
        raise SettingsError("autopilot.qa must be true or false.")
    found["repos"] = list(repos)
    return found


def is_on(autopilot, repo):
    """True when the GitHub repo ("owner/name") runs on autopilot."""
    return bool(repo) and repo.lower() in [name.lower() for name in autopilot.get("repos") or []]


def text_line(autopilot):
    """One line for the coordinator's start-up log, or "" when autopilot is off."""
    if not autopilot.get("repos"):
        return ""
    return "Autopilot on for {}: {} issue(s) in flight, at most {} started a day, {} merges, QA {}.".format(
        ", ".join(autopilot["repos"]), autopilot["inFlight"], autopilot["maxStartsPerDay"], autopilot["mergeMethod"],
        "on" if autopilot["qa"] else "off")


# ---------- Dependencies ----------

DEPENDS_LINE = re.compile(r"depends on\b([^\n]*)", re.IGNORECASE)
ISSUE_REF = re.compile(r"#(\d+)")


def dependencies(body):
    """The issue numbers on the first "Depends on" line of an issue body, in order, as strings. [] for none."""
    match = DEPENDS_LINE.search(body or "")
    if not match:
        return []
    found = []
    for number in ISSUE_REF.findall(match.group(1)):
        if number not in found:
            found.append(number)
    return found


class IssueStates:
    """Says whether an issue is open or closed, from this turn's read when it can, else one gh call (cached)."""

    def __init__(self, client, items=()):
        self.client = client
        self.known = {item.id: ("closed" if item.status == fb.DONE_STATUS else "open") for item in items}

    def __call__(self, number):
        number = str(number)
        if number not in self.known:
            try:
                self.known[number] = self.client.issue_state(number)
            except GitHubError:
                # Unknown counts as open: the autopilot waits rather than starting work too early.
                self.known[number] = "unknown"
        return self.known[number]


def open_dependencies(body, state_of):
    """The "Depends on" issues that are not closed yet."""
    return [number for number in dependencies(body) if state_of(number) != "closed"]


# ---------- Gates ----------

def owned_and_free(item, user_id, now):
    return item.is_owned_by(user_id) and item.status != fb.DONE_STATUS and not item.claim_is_live(now)


def mark(item, goat_status=None, stage=None, clear=False):
    """Updates the item read this turn after a label write, so later steps of the same turn see the new labels."""
    add, remove = fb.label_changes(item, goat_status=goat_status, stage=stage, clear_goat_status=clear)
    item.labels = [name for name in item.labels if name not in remove] + add
    if goat_status is not None or clear:
        item.goat_status = goat_status or ""
    if stage is not None:
        item.status = stage
        item.stage = stage if stage in fb.STAGES else None


def approve_gates(client, items, user_id, now, state_of, shared, dry_run=False):
    """Approves the size gate, and the plan gate once the issue's dependencies are closed. Returns log lines.

    The label goes first, then a short note on the issue, as an approval from Discord does."""
    lines = []
    who = team.dispatcher_signature(shared)
    for item in items:
        if not owned_and_free(item, user_id, now):
            continue
        if fb.same_label(item.goat_status, fb.TIER_REVIEW):
            what = "the size ({})".format(item.size or "as proposed")
        elif fb.same_label(item.goat_status, fb.PLAN_REVIEW):
            body = client.get_item(item.id).get("body") or ""
            waiting = open_dependencies(body, state_of)
            if waiting:
                lines.append("#{}'s plan waits for {} to close.".format(
                    item.id, ", ".join("#" + number for number in waiting)))
                continue
            what = "the plan"
        else:
            continue
        if dry_run:
            lines.append("Would approve {} of #{}.".format(what, item.id))
            continue
        add, remove = fb.label_changes(item, goat_status=fb.APPROVED)
        client.set_labels(item.id, add=add, remove=remove)
        mark(item, goat_status=fb.APPROVED)
        lines.append("Approved {} of #{}.".format(what, item.id))
        try:
            client.create_comment(item.id, "{}\n**🤖 {}** · Autopilot approved {}.".format(APPROVED_MARKER, who, what))
        except GitHubError as error:
            lines.append("Approved #{}, but could not leave the note: {}".format(item.id, error))
    return lines


# ---------- Merging ----------

def checks_verdict(rollup):
    """("pass" | "pending" | "fail" | "none", [names of failing checks]) for a PR's statusCheckRollup.

    A skipped or neutral check does not block. A PR with no checks at all is "none", which the autopilot merges:
    the Review agent already ran the tests."""
    if not rollup:
        return "none", []
    failing, pending = [], False
    for check in rollup:
        name = check.get("name") or check.get("context") or "a check"
        if check.get("__typename") == "StatusContext" or "context" in check:
            state = (check.get("state") or "").upper()
            if state in ("FAILURE", "ERROR"):
                failing.append(name)
            elif state in ("PENDING", "EXPECTED", ""):
                pending = True
            continue
        if (check.get("status") or "").upper() != "COMPLETED":
            pending = True
            continue
        conclusion = (check.get("conclusion") or "").upper()
        if conclusion not in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            failing.append(name)
    if failing:
        return "fail", failing
    return ("pending", []) if pending else ("pass", [])


def own_prs(prs, item_id):
    return [pr for pr in prs if branch_item_id(pr.get("branch")) == item_id]


def lowest_part(parts):
    """The open PR to merge next: the one whose base is not another open part's branch. None when unclear."""
    branches = {pr["branch"] for pr in parts}
    bottom = [pr for pr in parts if pr.get("base") not in branches]
    return bottom[0] if len(bottom) == 1 else None


def comments_after_last_review(item):
    """The issue's comments after the newest Review agent handoff (all of them when there is none)."""
    comments = list(item.comments or [])
    last = max((index for index, comment in enumerate(comments) if REVIEW_HANDOFF in (comment.get("body") or "")),
               default=-1)
    return comments[last + 1:]


def is_held(item):
    """True when the autopilot handed this merge review to a person since the last review round."""
    return any(HOLD_MARKER in (comment.get("body") or "") for comment in comments_after_last_review(item))


def send_back_count(item):
    return sum(SEND_BACK_MARKER in (comment.get("body") or "") for comment in item.comments or [])


def hold(client, item, reason, shared):
    """Leaves the merge to a person: one comment that says why, @mentioning the owner. The labels stay."""
    mention = "@{} ".format(item.owner_ids[0]) if item.owner_ids else ""
    client.create_comment(item.id, "{}\n**🤖 {}** · {}Autopilot stopped before merging: {}\n\nPlease look at the PR "
                                   "and merge it yourself, or comment what to change and swap `stage:review` for "
                                   "`stage:implement` and `goat:merge-review` for `goat:ready`.".format(
                                       HOLD_MARKER, team.dispatcher_signature(shared), mention, reason))
    item.comments = list(item.comments or []) + [{"body": HOLD_MARKER}]


def send_back(client, item, reason, shared):
    """Sends the issue back to Implement with a note, or holds once it was sent back MAX_SEND_BACKS times.
    Returns a log line. The note goes first, so the Implement agent never starts without it."""
    if send_back_count(item) >= MAX_SEND_BACKS:
        hold(client, item, "{} (already sent back to Implement {} times).".format(reason, MAX_SEND_BACKS), shared)
        return "Held #{} for a person: {}".format(item.id, reason)
    client.create_comment(item.id, "{}\n**🤖 {}** · Autopilot could not merge: {}\n\nBack to the Implement agent: fix "
                                   "this on the same branch. To catch up with the default branch, merge it into the "
                                   "branch (never rebase or force-push), run the tests, and push. Review runs "
                                   "again after that.".format(SEND_BACK_MARKER, team.dispatcher_signature(shared), reason))
    add, remove = fb.label_changes(item, goat_status=fb.READY, stage="implement")
    client.set_labels(item.id, add=add, remove=remove)
    mark(item, goat_status=fb.READY, stage="implement")
    return "Sent #{} back to Implement: {}".format(item.id, reason)


def merge_ready(client, items, user_id, now, autopilot, shared, dry_run=False, on_merged=None, on_red=None):
    """Merges, part by part, the PRs of issues that passed review. Returns log lines.

    At most one PR is merged per turn, and only while the default branch's own CI is green: each merge waits for the
    CI run its own merge started, so a merge that breaks the branch stops the next one. In a repo with CI, a PR with
    no checks yet waits (a re-targeted stack part waits for its new run), and is held after NO_CHECKS_MINUTES.

    on_merged(item, pr) is called after each merge, and on_red(url) when the default branch's CI is failing, for
    Discord."""
    candidates = [item for item in items if owned_and_free(item, user_id, now)
                  and fb.same_label(item.goat_status, fb.MERGE_REVIEW) and not is_held(item)]
    if not candidates:
        return []
    lines = []
    default = client.default_branch()
    open_prs = client.pull_requests("open")
    merged_prs = None
    branch_ci = None
    merged_one = False
    for item in candidates:
        parts = own_prs(open_prs, item.id)
        if not parts:
            # Merged already (by a person or an earlier turn): goat/merge.py moves the issue to staging.
            continue
        if default.lower() in NEVER_MERGE_INTO:
            if not dry_run:
                hold(client, item, "the default branch is `{}`. The autopilot never merges into `{}`.".format(
                    default, default), shared)
            lines.append("Held #{}: autopilot never merges into {}.".format(item.id, default))
            continue
        part = lowest_part(parts)
        if part is None:
            if not dry_run:
                hold(client, item, "its open PRs do not form one stack, so the merge order is unclear.", shared)
            lines.append("Held #{}: unclear stack order.".format(item.id))
            continue
        if part.get("base") != default:
            if branch_item_id(part.get("base")) != item.id:
                if not dry_run:
                    hold(client, item, "PR #{} targets `{}`, not `{}`.".format(part["number"], part.get("base"), default),
                         shared)
                lines.append("Held #{}: PR #{} targets {}.".format(item.id, part["number"], part.get("base")))
                continue
            # The part below was merged: move this part onto the default branch, and merge it on a later turn,
            # once GitHub has worked out whether it merges cleanly there.
            if dry_run:
                lines.append("Would re-target PR #{} of #{} to {}.".format(part["number"], item.id, default))
            else:
                client.set_pr_base(part["number"], default)
                lines.append("Re-targeted PR #{} of #{} to {}.".format(part["number"], item.id, default))
            continue
        if branch_ci is None:
            branch_ci = client.branch_ci(default)
            if branch_ci[0] == "fail":
                lines.append("No merges: CI on {} is failing ({}). Waiting until it is green again.".format(
                    default, branch_ci[1] or "see GitHub Actions"))
                if on_red and not dry_run:
                    on_red(branch_ci[1])
                return lines
        if merged_one or branch_ci[0] == "pending":
            lines.append("#{} waits: CI on {} is still running for the last merge.".format(item.id, default))
            continue
        status = client.pr_status(part["number"])
        if status["draft"]:
            if not dry_run:
                hold(client, item, "PR #{} is still a draft, so it was not marked ready by Review.".format(part["number"]),
                     shared)
            lines.append("Held #{}: PR #{} is a draft.".format(item.id, part["number"]))
            continue
        if status["mergeable"] == "CONFLICTING":
            reason = "PR #{} has conflicts with `{}`.".format(part["number"], default)
            lines.append(reason if dry_run else send_back(client, item, reason, shared))
            continue
        verdict, failing = checks_verdict(status["checks"])
        if verdict == "fail":
            reason = "PR #{} has failing checks: {}.".format(part["number"], ", ".join(failing))
            lines.append(reason if dry_run else send_back(client, item, reason, shared))
            continue
        if verdict == "pending" or status["mergeable"] != "MERGEABLE":
            lines.append("#{} waits: PR #{} has checks running or GitHub is still checking it.".format(
                item.id, part["number"]))
            continue
        if verdict == "none" and branch_ci[0] != "none":
            # The repo has CI, so a PR with no checks has not been tested yet (for example a stack part that was
            # just re-targeted). Wait for its run; hold when none comes.
            updated = fb.parse_date(status.get("updated_at") or "")
            if updated is not None and (now - updated).total_seconds() > NO_CHECKS_MINUTES * 60:
                reason = ("no CI check has run on PR #{} in {} minutes, though `{}` has CI. Check that the workflow "
                          "runs for this PR.".format(part["number"], NO_CHECKS_MINUTES, default))
                if not dry_run:
                    hold(client, item, reason, shared)
                lines.append("Held #{}: {}".format(item.id, reason))
            else:
                lines.append("#{} waits: no CI check on PR #{} yet.".format(item.id, part["number"]))
            continue
        if merged_prs is None:
            merged_prs = client.pull_requests("merged")
        # A stack keeps its commits (a merge commit), so the next part still merges cleanly onto the default branch.
        stacked = len(parts) + len(own_prs(merged_prs, item.id)) > 1
        method = "merge" if stacked else autopilot["mergeMethod"]
        if dry_run:
            lines.append("Would merge PR #{} of #{} ({}).".format(part["number"], item.id, method))
            continue
        try:
            client.merge_pr(part["number"], method)
        except GitHubError as error:
            hold(client, item, "GitHub refused the merge of PR #{}: {}".format(part["number"], error), shared)
            lines.append("Held #{}: merge of PR #{} failed: {}".format(item.id, part["number"], error))
            continue
        merged_one = True
        left = len(parts) - 1
        client.create_comment(item.id, "{}\n**🤖 {}** · Autopilot merged {} into `{}` (Review passed, checks {}).{}".format(
            MERGED_MARKER, team.dispatcher_signature(shared), part["url"], default, verdict,
            " {} more part(s) of the stack to go.".format(left) if left else ""))
        lines.append("Merged PR #{} of #{} into {} ({}).".format(part["number"], item.id, default, method))
        if on_merged:
            on_merged(item, part)
    return lines


# ---------- The pipeline and the backlog ----------

def in_flight(items, user_id):
    """The owner's open issues that the pipeline is working on: they have a goat: label, are not done, and do not
    wait on a person."""
    return [item for item in items
            if item.is_owned_by(user_id) and item.status != fb.DONE_STATUS and item.goat_status.strip()
            and not fb.same_label(item.goat_status, fb.DONE)
            and not any(fb.same_label(item.goat_status, status) for status in WAITING_ON_PERSON)]


def candidate_problem(item, user_id, state_of):
    """Why a backlog issue cannot be started now, or None when it can."""
    if item.status == fb.DONE_STATUS:
        return "it is closed"
    if item.goat_status.strip():
        return "it is already in the pipeline"
    if item.status not in ("", fb.DISCOVERY):
        return "it is at stage:{}, not in the backlog".format(item.status)
    others = [login for login in item.owner_ids if login.lower() != str(user_id).lower()]
    if others:
        return "it is assigned to {}".format(", ".join(others))
    waiting = open_dependencies(item.body, state_of)
    if waiting:
        return "it depends on {}, still open".format(", ".join("#" + number for number in waiting))
    return None


def backlog(client, items, user_id):
    """(ready, blocked) backlog issues as GoatItems. blocked is [(item, reason)]. Sorted by priority, then number."""
    state_of = IssueStates(client, items)
    ready, blocked = [], []
    for raw in client.backlog_items():
        item = fb.GoatItem.from_github(raw)
        problem = candidate_problem(item, user_id, state_of)
        if problem is None:
            ready.append(item)
        else:
            blocked.append((item, problem))
    order = lambda item: (fb.priority_rank(item.priority), int(item.id))
    ready.sort(key=order)
    blocked.sort(key=lambda entry: order(entry[0]))
    return ready, blocked


def today(now):
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d")


def starts_today(state, now):
    return (state.get("autopilot") or {}).get("starts", {}).get(today(now), 0)


def count_start(state, now):
    starts = state.setdefault("autopilot", {}).setdefault("starts", {})
    day = today(now)
    # Only today's count matters.
    for old in [key for key in starts if key != day]:
        del starts[old]
    starts[day] = starts.get(day, 0) + 1


def room(autopilot, flying, started_today):
    """How many issues the PM may start now: free places in the pipeline, within the daily limit."""
    return max(0, min(autopilot["inFlight"] - flying, autopilot["maxStartsPerDay"] - started_today))


# ---------- Picks (goat.py pick, then the coordinator) ----------

def picks_path(repo, folder=None):
    return os.path.join(folder or PICKS_DIR, repo.replace("/", "__") + ".json")


def read_picks(repo, folder=None):
    try:
        with open(picks_path(repo, folder)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [pick for pick in data if isinstance(pick, dict) and str(pick.get("item", "")).isdigit()] \
        if isinstance(data, list) else []


def save_pick(repo, item_id, why, now, folder=None):
    """Adds one pick to the repo's picks file. Returns the number of picks in it now."""
    picks = [pick for pick in read_picks(repo, folder) if str(pick["item"]) != str(item_id)]
    picks.append({"item": str(item_id), "why": (why or "").strip()[:500], "at": fb.format_date(now)})
    path = picks_path(repo, folder)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(picks, f, indent=2)
    return len(picks)


def take_picks(repo, folder=None):
    """Reads and removes the repo's picks file, so each pick is acted on once."""
    picks = read_picks(repo, folder)
    try:
        os.remove(picks_path(repo, folder))
    except OSError:
        pass
    return picks


def start_item(client, item, user_id, why, shared):
    """Starts a backlog issue as `start <n>` in Discord does: assigned to the owner, stage:intake, goat:ready."""
    if not item.is_owned_by(user_id):
        client.add_assignees(item.id, [user_id])
    add, remove = fb.label_changes(item, goat_status=fb.READY, stage="intake")
    client.set_labels(item.id, add=add, remove=remove)
    note = "{}\n**🤖 {}** · Started by the autopilot.".format(STARTED_MARKER, team.signature(shared, PM_ROLE))
    if why:
        note += " Why now: {}".format(why)
    try:
        client.create_comment(item.id, note)
    except GitHubError:
        pass


def apply_picks(client, repo, items, user_id, autopilot, state, shared, now, dry_run=False, folder=None, on_started=None):
    """Starts the PM's picks after checking each one again. Returns (log lines, how many started)."""
    picks = take_picks(repo, folder) if not dry_run else read_picks(repo, folder)
    if not picks:
        return ["The project manager picked nothing."], 0
    state_of = IssueStates(client, items)
    free = room(autopilot, len(in_flight(items, user_id)), starts_today(state, now))
    lines, started = [], 0
    for pick in picks:
        number = str(pick["item"])
        if started >= free:
            lines.append("Not starting #{}: no room left in the pipeline or today's limit.".format(number))
            continue
        try:
            item = fb.GoatItem.from_github(client.get_item(number))
        except (ItemNotFound, GitHubError) as error:
            lines.append("Not starting #{}: {}".format(number, error))
            continue
        problem = candidate_problem(item, user_id, state_of)
        if problem:
            lines.append("Not starting #{}: {}.".format(number, problem))
            continue
        if dry_run:
            lines.append("Would start #{}.".format(number))
            started += 1
            continue
        start_item(client, item, user_id, pick.get("why", ""), shared)
        count_start(state, now)
        started += 1
        lines.append("Started #{} {}: {}".format(number, item.name[:50], pick.get("why") or "picked by the PM"))
        if on_started:
            on_started(item, pick.get("why", ""))
    return lines, started


# ---------- QA ----------

def qa_key(repo, item_id):
    return "{}#{}".format(repo.lower(), item_id)


def qa_queue(state):
    return state.setdefault("autopilot", {}).setdefault("qa", {})


def queue_qa(state, repo, repo_path, item):
    """Remembers that a merged issue needs its QA run."""
    qa_queue(state)[qa_key(repo, item.id)] = {"repo": repo, "repoPath": repo_path, "item": item.id,
                                              "label": "#{} {}".format(item.id, item.name[:40]), "url": item.url,
                                              "status": "due", "tries": 0}


def prune_qa(state):
    """Forgets QA entries that are finished: done, failed (the owner was told) or dropped."""
    queue = qa_queue(state)
    for key in [key for key, entry in queue.items() if entry.get("status") != "due"]:
        del queue[key]


def run_result(log_path):
    """(ok, text) from a headless agent's JSON log: ok is False when the run errored or the log is not readable."""
    try:
        with open(log_path) as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return False, ""
    if not isinstance(data, dict):
        return False, ""
    text = str(data.get("result") or "").strip()
    return (not data.get("is_error") and data.get("subtype", "success") == "success" and bool(text)), text


def qa_comment(text, log_path, shared):
    body = text if len(text) <= QA_TEXT_LIMIT else text[:QA_TEXT_LIMIT] + "\n\n… (cut; the full report is on this machine)"
    return "{}\n**🤖 {}** · QA ran on the merged default branch. Closing this issue; new findings are backlog issues.\n\n" \
           "**{}:**\n\n{}\n\nAgent log on this machine: `{}`".format(
               QA_MARKER, team.dispatcher_signature(shared), team.signature(shared, QA_ROLE), body, log_path)


def finish_qa(client, entry, run, shared):
    """Handles a QA run that ended. Returns (log line, closed): closes the issue when QA ran, retries once when it
    did not, then gives up and tells the owner on the issue."""
    ok, text = run_result(run.get("log"))
    if ok:
        client.create_comment(entry["item"], qa_comment(text, run.get("log"), shared))
        client.close_issue(entry["item"])
        entry["status"] = "done"
        return "QA done on #{}; closed it.".format(entry["item"]), True
    entry["tries"] = entry.get("tries", 0) + 1
    if entry["tries"] < MAX_QA_TRIES:
        entry["status"] = "due"
        return "QA on #{} did not finish; trying again.".format(entry["item"]), False
    entry["status"] = "failed"
    client.create_comment(entry["item"], "{}\n**🤖 {}** · The QA agent could not finish on this issue after {} tries, so "
                                         "it is still open in stage:staging. Agent log on this machine: `{}`. Close the "
                                         "issue yourself when it is done.".format(
                                             HOLD_MARKER, team.dispatcher_signature(shared), MAX_QA_TRIES, run.get("log")))
    return "QA failed twice on #{}; left it open for a person.".format(entry["item"]), False


def now_utc():
    return datetime.now(timezone.utc)
