"""Farm write actions: claim, renew, post, doc, summary, release, and add (a new issue). Each one checks the farm
rules before it writes.

GitHub has no compare-and-set, so a claim is written and then read back (docs/design.md#claims).
Every later write reads the issue first and stops if the claim is no longer this session's.

An issue's claim, expiry, PR link and summary live in one "farm state" comment (farm/board.py). Labels hold the
stage, the farm status and the size. A claim writes the state comment first and the labels second; a release writes
the labels first and clears the claim second. A crash between the two writes leaves a state that the checks below
accept (`release` can be run again) or that expires (a stale claim).
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from farm import board as fb
from farm import exit_codes
from farm import views
from farm.config import CLAIMS_DIR, REPO_ROOT, RUNS_FILE, ConfigError, lease_minutes
from farm.github import GitHubError
from farm.views import next_step

# Long enough for a short version of the stage output. The full text goes in a doc section.
SUMMARY_LIMIT = 2000
POST_KINDS = ("note", "question", "checkpoint", "chat")
RUN_USAGE = os.path.join(REPO_ROOT, "skills", "farm-board", "run-usage.py")
# Set to 1 in agent sessions that the coordinator starts.
HEADLESS_ENV = "FARM_HEADLESS"


class FarmError(Exception):
    exit_code = exit_codes.GENERAL


class ItemNotAvailable(FarmError):
    """The item is not one this session can work on: not found, another repo, not a farm item, not yours, not ready."""
    exit_code = exit_codes.ITEM_NOT_AVAILABLE


class ClaimConflict(FarmError):
    """Another session claimed the item, or a person changed it since this session claimed it."""
    exit_code = exit_codes.CLAIM_CONFLICT


def check_summary(summary):
    if len(summary) > SUMMARY_LIMIT:
        raise FarmError("Summary is {} characters. The limit is {}. Shorten it; the full text goes in the doc.".format(
            len(summary), SUMMARY_LIMIT))


ISSUE_URL = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/issues/(\d+)")


def parse_item_ref(ref):
    """An issue number ("14" or "#14"), or an issue link. Returns (repo or None, number as a string), or None."""
    ref = str(ref).strip()
    match = ISSUE_URL.search(ref)
    if match:
        return match.group(1), match.group(2)
    ref = ref.lstrip("#")
    if ref.isdigit() and int(ref) > 0:
        return None, str(int(ref))
    return None


def refuse_ready_when_headless(ready, environ):
    """Headless agents may add items, but not with --ready: a person decides when new work starts."""
    if ready and environ.get(HEADLESS_ENV) == "1":
        raise FarmError("Agents may not add items with --ready. Add it without --ready, and a person moves it into the farm.")


def utc_now():
    return datetime.now(timezone.utc)


def iso(moment):
    return fb.format_date(moment)


def read_usage(session_id, since):
    """Token and model use of this run, from run-usage.py. It fails soft: on any problem, null usage and a note."""
    try:
        result = subprocess.run([sys.executable, RUN_USAGE, "--session", session_id, "--since", since],
                                capture_output=True, text=True, timeout=120)
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {"tokens": None, "models": None, "usageNote": "run-usage.py failed: {}".format(error)}


def post_header(role, kind, version):
    """The first line of an agent post, with the farm version that made it."""
    return "**🤖 {} agent · {} · farm {}**".format(role.capitalize(), kind, version)


def resume_text(item):
    """The end of a chat post: the command that resumes the agent session."""
    command = "python3 ~/.software-farm/farm.py chat {}".format(item.id)
    return "\n\nResume this session:\n\n```\n{}\n```".format(command)


def mention_text(item, farm_status):
    """The last line of a handoff that reaches a person: who is needed, and what to do.

    It @mentions the first assignee. GitHub does not notify you of your own comments, so while agents use your own
    login this line is a visible marker only. With a separate bot login for the agents, it is a real notification.
    """
    if farm_status not in fb.WAITS_FOR_PERSON or not item.owner_ids:
        return ""
    return "\n\n@{} {}".format(item.owner_ids[0], views.waiting_hint(farm_status))


class Farm:
    def __init__(self, client, shared, board, user, session_id, now=utc_now, claims_dir=CLAIMS_DIR, runs_file=RUNS_FILE,
                 usage_reader=read_usage, version="unknown"):
        self.client = client
        self.version = version
        self.usage_reader = usage_reader
        self.shared = shared
        self.board = board
        self.user = user
        self.session_id = session_id
        self.now = now
        self.claims_dir = claims_dir
        self.runs_file = runs_file

    # ---------- Reads and checks ----------

    def resolve_item_id(self, item_ref):
        """The issue number for a number, "#number" or an issue link of this project's repo."""
        parsed = parse_item_ref(item_ref)
        if parsed is None:
            raise ItemNotAvailable("Not an issue number or issue link: {}".format(item_ref))
        repo, number = parsed
        if repo is not None and repo.lower() != self.board["id"].lower():
            raise ItemNotAvailable("{} is in {}, not in this project's repo {} (FARM_GITHUB_REPO). "
                                   "Run farm.py in the checkout of that repo.".format(item_ref, repo, self.board["id"]))
        return number

    def load(self, item_ref):
        """Reads the issue with its comments. Returns (FarmItem, board)."""
        raw = self.client.get_item(self.resolve_item_id(item_ref))
        return fb.FarmItem.from_github(raw), self.board

    def require_owner(self, item):
        if not item.is_owned_by(self.user["id"]):
            raise ItemNotAvailable("#{} is not assigned to {}. Only the assignee's agents work on it. Ask a person to assign it.".format(
                item.id, self.user["name"]))

    def require_session(self):
        if not self.session_id:
            raise ConfigError("No session id. Set CLAUDE_CODE_SESSION_ID or pass --session.")

    def require_own_claim(self, item, resuming=None):
        """Stops when a person or another session changed the issue since this session claimed it.

        resuming: (stage, farm status) of a release that already wrote its labels but not its state. That session
        still holds the claim, so it may run the release again.
        """
        self.require_session()
        if item.claim_session != self.session_id:
            raise ClaimConflict("#{} is not claimed by this session (claim: {!r}). Stop work and read the issue with "
                                "farm.py show {}.".format(item.id, item.claim, item.id))
        if fb.same_label(item.farm_status, fb.WORKING):
            return
        if resuming and item.status == resuming[0] and fb.same_label(item.farm_status, resuming[1]):
            return
        raise ClaimConflict("#{} farm status is {!r}, not working. A person changed it. Stop work and read the issue with "
                            "farm.py show {}.".format(item.id, item.farm_status, item.id))

    def check_board(self):
        """Stops when the repo lacks a label the farm needs. `farm.py setup-repo` creates them.

        Claim runs it before its first write, so a run never starts on a repo the CLI cannot write to.
        """
        problems = views.board_config_problems(self.client.existing_labels())
        if problems:
            lines = ["Repo {} is not set up for farm {}:".format(self.board["id"], self.version)]
            lines += ["- " + problem for problem in problems]
            lines.append("Run `farm.py setup-repo` to create the labels.")
            raise FarmError("\n".join(lines))

    # ---------- Writes ----------

    def write_labels(self, item, farm_status=None, stage=None, size=None, clear_farm_status=False):
        add, remove = fb.label_changes(item, farm_status=farm_status, stage=stage, size=size,
                                       clear_farm_status=clear_farm_status)
        self.client.set_labels(item.id, add=add, remove=remove)

    def write_state(self, item, **changes):
        """Rewrites the farm state comment with the changes (keys: claim, claimExpires, summary, prUrl)."""
        state = {
            "claim": item.claim,
            "claimExpires": iso(item.claim_expires) if item.claim_expires else "",
            "summary": item.summary,
            "prUrl": item.pr_url,
        }
        state.update(changes)
        body = fb.render_state(state)
        if item.state_comment_id:
            self.client.update_comment(item.state_comment_id, body)
        else:
            self.client.create_comment(item.id, body)

    # ---------- Claim and renew ----------

    def claim(self, item_ref, role):
        self.require_session()
        item, board = self.load(item_ref)
        if not item.is_farm_item:
            raise ItemNotAvailable("#{} has no farm: label, so it is not a farm issue. "
                                   "Add the label farm:ready to make it one.".format(item.id))
        self.require_owner(item)
        now = self.now()
        if fb.same_label(item.farm_status, fb.CHAT_WITH_AGENT):
            # The developer resumed the agent session, which claims again for its own stage.
            expected = item.stage
        else:
            expected = fb.next_role(item, now)
        if expected != role:
            raise ItemNotAvailable("#{} is not ready for {} (stage {!r}, farm status {!r}, next role: {}). Next step: {}.".format(
                item.id, role, item.status, item.farm_status, expected or "none", next_step(item, now)))
        self.check_board()

        expires = now + timedelta(minutes=lease_minutes(self.shared, role))
        self.write_state(item, claim=fb.claim_text(role, self.session_id), claimExpires=iso(expires))
        # Farm status approved: the gate passed, so the issue moves to this role's stage.
        self.write_labels(item, farm_status=fb.WORKING, stage=role if item.stage != role else None)

        after, _ = self.load(item.id)
        if after.claim_session != self.session_id or not fb.same_label(after.farm_status, fb.WORKING):
            raise ClaimConflict("Claim lost: #{} now has claim {!r}, farm status {!r}. Another session claimed it. "
                                "Do not work on the issue.".format(item.id, after.claim, after.farm_status))

        record = {
            "item": item.id,
            "board": item.board_id,
            "role": role,
            "sessionId": self.session_id,
            "startedAt": iso(now),
            "fromStage": role,
            "tookOverStale": fb.same_label(item.farm_status, fb.WORKING),
        }
        self.save_claim_record(record)
        return dict(record, expiresAt=iso(expires), previousClaim=item.claim or None)

    def renew(self, item_ref):
        item, _ = self.load(item_ref)
        self.require_own_claim(item)
        expires = self.now() + timedelta(minutes=lease_minutes(self.shared, item.claim_role))
        self.write_state(item, claimExpires=iso(expires))
        return iso(expires)

    # ---------- Posts and the doc ----------

    def post(self, item_ref, kind, markdown):
        if kind not in POST_KINDS:
            raise FarmError("Post kind must be one of: {}.".format(", ".join(POST_KINDS)))
        item, _ = self.load(item_ref)
        self.require_own_claim(item)
        text = post_header(item.claim_role, kind, self.version) + "\n\n" + markdown.rstrip()
        if kind == "chat":
            text += resume_text(item)
        if kind in ("question", "chat"):
            text += mention_text(item, fb.ANSWER_QUESTION if kind == "question" else fb.CHAT_WITH_AGENT)
        return self.client.create_comment(item.id, text)

    def add_doc_section(self, item_ref, title, markdown):
        """Adds a doc section: one comment. A section with the same title is updated in place, so a repeated
        write does not double it. Returns the comment id."""
        item, _ = self.load(item_ref)
        self.require_own_claim(item)
        body = fb.render_doc(title, markdown)
        for section in item.doc_sections:
            if section["title"] == title:
                self.client.update_comment(section["id"], body)
                return section["id"]
        return self.client.create_comment(item.id, body)

    def set_summary(self, item_ref, summary):
        """Sets the farm summary. Refuses text over the limit, to keep it short."""
        check_summary(summary)
        item, _ = self.load(item_ref)
        self.require_own_claim(item)
        self.write_state(item, summary=summary)

    # ---------- Add ----------

    def add(self, title, description=None, ready=False, milestone=None, priority=None):
        """Creates an issue. Returns (FarmItem, notes), where notes are lines to print.

        Default: label stage:discovery, no farm: label and no assignee, so the farm ignores it.
        ready: stage:intake, farm:ready and assigned to this user, so the coordinator starts it.
        The description is the issue body. Needs no session and no claim.
        """
        title = (title or "").strip()
        if not title:
            raise FarmError("The title is empty.")
        if priority is not None and priority not in fb.PRIORITY_ORDER:
            raise FarmError("Priority must be one of: {}.".format(", ".join(fb.PRIORITY_ORDER)))
        labels = [fb.stage_label(fb.DISCOVERY)]
        assignees = []
        if ready:
            labels = [fb.stage_label("intake"), fb.farm_label(fb.READY)]
            assignees = [self.user["id"]]
        if priority:
            labels.append(fb.priority_label(priority))
        number = self.client.create_issue(title, (description or "").strip(), labels, assignees, milestone)
        item, _ = self.load(number)
        return item, []

    # ---------- Release ----------

    def release(self, item_ref, stage, farm_status, handoff, summary=None, size=None, pr_url=None, usage=None):
        """Posts the handoff, sets the new stage, farm status and size, then clears the claim.

        If the second write fails, the claim stays and the handoff is already posted: run release again.
        """
        if stage not in fb.STAGES:
            raise FarmError("Stage must be one of: {}.".format(", ".join(fb.STAGES)))
        if farm_status not in fb.RELEASE_STATUSES:
            raise FarmError("Farm status must be one of: {}.".format(", ".join(fb.RELEASE_STATUSES)))
        if summary is not None:
            check_summary(summary)
        if size is not None and size not in fb.SIZES:
            raise FarmError("Size must be one of: {}.".format(", ".join(fb.SIZES)))

        item, _ = self.load(item_ref)
        self.require_own_claim(item, resuming=(stage, farm_status))
        resuming = not fb.same_label(item.farm_status, fb.WORKING)
        role = item.claim_role
        if not resuming:
            text = post_header(role, "handoff", self.version) + "\n\n" + handoff.rstrip() + mention_text(item, farm_status)
            self.client.create_comment(item.id, text)
            self.write_labels(item, farm_status=farm_status, stage=stage, size=size)
        changes = {"claim": "", "claimExpires": ""}
        if summary is not None:
            changes["summary"] = summary
        if pr_url:
            changes["prUrl"] = pr_url
        self.write_state(item, **changes)

        after, _ = self.load(item.id)
        if after.claim or not fb.same_label(after.farm_status, farm_status):
            raise FarmError("Release did not apply: claim {!r}, farm status {!r}. Run release again.".format(
                after.claim, after.farm_status))

        run = self.run_record(item, role, stage, farm_status, usage)
        self.append_run(run)
        self.remove_claim_record(item.id)
        return run

    def run_record(self, item, role, to_stage, farm_status, usage):
        claim = self.read_claim_record(item.id) or {}
        if usage is None:
            if claim.get("startedAt"):
                usage = self.usage_reader(self.session_id, claim["startedAt"])
            else:
                usage = {"tokens": None, "models": None, "usageNote": "no claim record on this machine",
                         "cwd": None, "model": None}
        if fb.same_label(farm_status, fb.CHAT_WITH_AGENT):
            outcome = "chat"
        elif fb.same_label(farm_status, fb.ANSWER_QUESTION):
            outcome = "question"
        elif farm_status in fb.GATES:
            outcome = "gate"
        elif role == "review" and to_stage == "implement":
            outcome = "sent-back"
        else:
            outcome = "next-stage"
        record = {
            "item": item.id,
            "board": item.board_id,
            "role": role,
            "sessionId": self.session_id,
            "startedAt": claim.get("startedAt"),
            "endedAt": iso(self.now()),
            "fromStage": item.stage,
            "toStage": to_stage,
            "outcome": outcome,
            "farmVersion": self.version,
        }
        record.update(usage or {})
        return record

    # ---------- Local files ----------

    def claim_record_path(self, item_id):
        # The repo is in the name: issue numbers repeat across repos.
        return os.path.join(self.claims_dir, "{}-{}.json".format(self.board["id"].replace("/", "_"), item_id))

    def save_claim_record(self, record):
        os.makedirs(self.claims_dir, exist_ok=True)
        with open(self.claim_record_path(record["item"]), "w") as f:
            json.dump(record, f, indent=2)

    def read_claim_record(self, item_id):
        try:
            with open(self.claim_record_path(item_id)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def remove_claim_record(self, item_id):
        try:
            os.remove(self.claim_record_path(item_id))
        except OSError:
            pass

    def append_run(self, record):
        os.makedirs(os.path.dirname(self.runs_file), exist_ok=True)
        with open(self.runs_file, "a") as f:
            f.write(json.dumps(record) + "\n")
