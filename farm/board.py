"""The farm's view of a GitHub issue: which stage it is in, who holds it, and what runs next.

State lives on the issue, in labels and one hidden comment (see docs/design.md):

- stage:<name>   the stage. discovery (backlog) -> intake -> plan -> implement -> review -> staging. A closed issue is done.
- farm:<name>    what the farm is doing or waiting for. No farm: label means the farm never touches the issue.
- size:S|M|L|XL, prio:<name>   size and priority.
- assignee       the owner. The farm only works on issues assigned to the person whose `gh` login it uses.
- milestone      a release or a phase. The farm only shows it.
- the "farm state" comment   claim, claim expiry, PR link and the short summary, as JSON in an HTML comment.
- "farm doc" comments        the long stage output (intake summary, spec and plan, review notes), one comment per section.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# The stages an agent works in.
STAGES = ("intake", "plan", "implement", "review")
# Stage labels that are not an agent stage: the backlog, and after the merge.
DISCOVERY = "discovery"
STAGING = "staging"
STAGE_NAMES = (DISCOVERY,) + STAGES + (STAGING,)
# Status of a closed issue.
DONE_STATUS = "done"

# Farm statuses (the farm:* labels, without the prefix).
READY = "ready"
WORKING = "working"
APPROVED = "approved"
ANSWER_QUESTION = "answer-question"
TIER_REVIEW = "tier-review"
PLAN_REVIEW = "plan-review"
MERGE_REVIEW = "merge-review"
# A headless agent asks for a live discussion. The developer resumes its session with `farm.py chat`.
CHAT_WITH_AGENT = "chat"
# Set by the cleanup (farm/cleanup.py) after it removes a Done item's local worktrees and branches.
DONE = "done"
FARM_STATUSES = (READY, WORKING, ANSWER_QUESTION, CHAT_WITH_AGENT, TIER_REVIEW, PLAN_REVIEW, MERGE_REVIEW, APPROVED,
                 DONE)

# The statuses an agent may set when it releases an item.
RELEASE_STATUSES = (READY, ANSWER_QUESTION, CHAT_WITH_AGENT, TIER_REVIEW, PLAN_REVIEW, MERGE_REVIEW)
GATES = (TIER_REVIEW, PLAN_REVIEW, MERGE_REVIEW)
# Statuses where a person must act. The coordinator @mentions the owner when an item reaches one.
WAITS_FOR_PERSON = (ANSWER_QUESTION, CHAT_WITH_AGENT) + GATES

SIZES = ("S", "M", "L", "XL")

# When a person approves a gate, the item goes to the next stage's agent.
NEXT_STAGE = {"intake": "plan", "plan": "implement"}

# Coordinator order. Unknown or empty priority sorts last.
PRIORITY_ORDER = ("blocker", "first", "next", "later", "last", "best-effort")

FARM_PREFIX = "farm:"
STAGE_PREFIX = "stage:"
SIZE_PREFIX = "size:"
PRIORITY_PREFIX = "prio:"

# Hidden first lines of the farm's comments.
STATE_MARKER = "<!-- farm:state "
DOC_MARKER = "<!-- farm:doc "


def same_label(a, b):
    """Labels compare without case or surrounding space."""
    return (a or "").strip().lower() == (b or "").strip().lower()


def display(status):
    """A status for people: "plan-review" is shown as "plan review"."""
    return (status or "").replace("-", " ")


def farm_label(status):
    return FARM_PREFIX + status


def stage_label(stage):
    return STAGE_PREFIX + stage


def size_label(size):
    return SIZE_PREFIX + size


def priority_label(priority):
    return PRIORITY_PREFIX + priority


# ---------- Farm state comment and doc comments ----------

def parse_date(text):
    """An ISO timestamp (UTC, "2026-11-01T10:00:00Z") as a datetime, or None."""
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def format_date(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_claim(text):
    """Claim text is "role|session id". Returns (role, session id), or (None, None) when empty."""
    if not text or "|" not in text:
        return None, None
    role, session = text.split("|", 1)
    return role, session


def claim_text(role, session_id):
    return "{}|{}".format(role, session_id)


def is_state_comment(body):
    return (body or "").startswith(STATE_MARKER)


def parse_state(body):
    """The farm state dict in a farm state comment, or {} when it is not one or is damaged."""
    if not is_state_comment(body):
        return {}
    end = body.find(" -->")
    if end < 0:
        return {}
    try:
        parsed = json.loads(body[len(STATE_MARKER):end])
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def hidden_json(data):
    """JSON that is safe inside an HTML comment: no "<" or ">" can end the comment early."""
    return json.dumps(data, sort_keys=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_state(state):
    """The farm state comment: the machine-readable JSON first, then a short readable view of it."""
    lines = [STATE_MARKER + hidden_json(state) + " -->", "**🤖 Farm state** · kept up to date by `farm.py`, please do not edit", ""]
    claim = state.get("claim")
    if claim:
        role, session = parse_claim(claim)
        lines.append("- Claim: {} agent, session `{}`, until {}".format(role, session, state.get("claimExpires") or "?"))
    else:
        lines.append("- Claim: none")
    lines.append("- PR: {}".format(state.get("prUrl") or "none"))
    if state.get("summary"):
        lines += ["", "**Summary**", "", state["summary"]]
    return "\n".join(lines)


def doc_title(body):
    """The section title of a farm doc comment, or None when it is not one."""
    if not (body or "").startswith(DOC_MARKER):
        return None
    end = body.find(" -->")
    try:
        return json.loads(body[len(DOC_MARKER):end]).get("title")
    except (ValueError, AttributeError):
        return None


def render_doc(title, markdown):
    return "{}{} -->\n# {}\n\n{}\n".format(DOC_MARKER, hidden_json({"title": title}), title, markdown.strip())


def doc_markdown(body):
    """The text of a farm doc comment without its hidden marker line."""
    return body.split("\n", 1)[1].strip() if "\n" in body else ""


@dataclass
class FarmItem:
    """One GitHub issue as the farm sees it. `id` is the issue number (a string); the repo is `board_id`."""
    id: str
    board_id: str
    name: str
    url: str
    updated_at: str
    status: str = ""
    farm_status: str = ""
    size: str = ""
    priority: str = ""
    owner_ids: list = field(default_factory=list)
    milestone: str = ""
    claim: str = ""
    claim_expires: datetime = None
    summary: str = ""
    pr_url: str = ""
    state_comment_id: int = None
    labels: list = field(default_factory=list)
    body: str = ""
    # Every comment of the issue, oldest first, when this read included them (see comments_loaded).
    comments: list = field(default_factory=list)
    stage: str = None
    # True when this read included the comments. A list read has them only for farm:working issues.
    comments_loaded: bool = False

    @classmethod
    def from_github(cls, raw):
        """Builds a FarmItem from a raw item (see farm/github.py)."""
        labels = list(raw.get("labels") or [])

        def with_prefix(prefix):
            return next((name[len(prefix):] for name in labels if name.startswith(prefix)), "")

        stage_name = with_prefix(STAGE_PREFIX)
        status = DONE_STATUS if raw.get("state") == "closed" else stage_name
        state_comment_id = None
        state = {}
        comments = raw.get("comments")
        for comment in comments or []:
            if is_state_comment(comment.get("body")):
                state = parse_state(comment["body"])
                state_comment_id = comment["id"]
        return cls(
            id=str(raw["number"]),
            board_id=raw.get("repo") or "",
            name=raw["title"],
            url=raw["url"],
            updated_at=raw["updated_at"],
            status=status,
            farm_status=with_prefix(FARM_PREFIX),
            size=with_prefix(SIZE_PREFIX),
            priority=with_prefix(PRIORITY_PREFIX),
            owner_ids=list(raw.get("assignees") or []),
            milestone=raw.get("milestone") or "",
            claim=state.get("claim") or "",
            claim_expires=parse_date(state.get("claimExpires")),
            summary=state.get("summary") or "",
            pr_url=state.get("prUrl") or "",
            state_comment_id=state_comment_id,
            labels=labels,
            body=raw.get("body") or "",
            comments=list(comments or []),
            stage=stage_name if stage_name in STAGES and status != DONE_STATUS else None,
            comments_loaded=comments is not None,
        )

    @property
    def doc_sections(self):
        """The item's doc sections, oldest first: [{"id", "title", "body"}]. Each is one farm doc comment."""
        found = []
        for comment in self.comments:
            title = doc_title(comment.get("body"))
            if title is not None:
                found.append({"id": comment["id"], "title": title, "body": comment["body"]})
        return found

    @property
    def is_farm_item(self):
        """Only issues with a farm: label are farm items that the coordinator handles. The farm never touches the others."""
        return bool(self.farm_status.strip())

    @property
    def farm_touched(self):
        """True for an issue the farm has worked on: it has a farm: label, or a stage past the backlog.
        After the merge the farm: label is gone, but the issue is still the farm's, for the retro and the cleanup."""
        past_backlog = [name[len(STAGE_PREFIX):] for name in self.labels if name.startswith(STAGE_PREFIX)]
        return self.is_farm_item or any(stage != DISCOVERY for stage in past_backlog)

    @property
    def claim_role(self):
        return parse_claim(self.claim)[0]

    @property
    def claim_session(self):
        return parse_claim(self.claim)[1]

    def claim_is_live(self, now):
        return bool(self.claim) and self.claim_expires is not None and self.claim_expires > now

    def is_owned_by(self, user_id):
        return str(user_id).lower() in [owner.lower() for owner in self.owner_ids]


def label_changes(item, farm_status=None, stage=None, size=None, priority=None, clear_farm_status=False):
    """The labels to add and to remove, so each label group (farm:, stage:, size:, prio:) keeps one value.

    Returns (add, remove). A label the issue already has is neither added nor removed.
    """
    wanted = []
    groups = []
    if farm_status is not None:
        wanted.append(farm_label(farm_status))
        groups.append(FARM_PREFIX)
    elif clear_farm_status:
        groups.append(FARM_PREFIX)
    if stage is not None:
        wanted.append(stage_label(stage))
        groups.append(STAGE_PREFIX)
    if size is not None:
        wanted.append(size_label(size))
        groups.append(SIZE_PREFIX)
    if priority is not None:
        wanted.append(priority_label(priority))
        groups.append(PRIORITY_PREFIX)
    remove = [name for name in item.labels if name.startswith(tuple(groups)) and name not in wanted]
    add = [name for name in wanted if name not in item.labels]
    return add, remove


def next_role(item, now):
    """The role that should work on this item now, or None.

    Ready: the stage's own agent. Approved: the next stage's agent (a gate was approved).
    Working with an expired claim: the agent of the issue's stage may take over.
    The coordinator uses this, so chat must stay None here: only the resumed session claims it.
    """
    if item.stage is None:
        return None
    if same_label(item.farm_status, READY):
        return item.stage
    if same_label(item.farm_status, APPROVED):
        return NEXT_STAGE.get(item.stage)
    if same_label(item.farm_status, WORKING) and not item.claim_is_live(now):
        return item.stage
    return None


def priority_rank(priority):
    label = (priority or "").strip().lower()
    if label in PRIORITY_ORDER:
        return PRIORITY_ORDER.index(label)
    return len(PRIORITY_ORDER)


# The farm's labels, with a color and a description, for `farm.py setup-repo`.
def all_labels():
    """[(name, color, description)] for every label the farm uses."""
    labels = []
    descriptions = {
        DISCOVERY: "Backlog. The farm ignores it until a person moves it to stage:intake",
        "intake": "Intake agent: summarize the task and propose a size",
        "plan": "Plan agent: write the spec and plan",
        "implement": "Implement agent: write the code and open a draft PR",
        "review": "Review agent: review the PR",
        STAGING: "Merged. Close the issue when it is done",
    }
    stage_colors = {DISCOVERY: "d4c5f9", "intake": "c5def5", "plan": "bfd4f2", "implement": "9ecbf5", "review": "7fb8f0",
                    STAGING: "0e8a16"}
    for name in STAGE_NAMES:
        labels.append((stage_label(name), stage_colors[name], descriptions[name]))
    farm_descriptions = {
        READY: "The stage's agent can start",
        WORKING: "An agent has claimed this issue (see the farm state comment)",
        APPROVED: "A person approved the gate: the next stage's agent can start",
        ANSWER_QUESTION: "Waiting for a person: answer the agent's question, then set farm:ready",
        CHAT_WITH_AGENT: "Waiting for a person: run `farm.py chat <number>`",
        TIER_REVIEW: "Waiting for a person: confirm the size, then set farm:approved",
        PLAN_REVIEW: "Waiting for a person: review the plan, then set farm:approved",
        MERGE_REVIEW: "Waiting for a person: review and merge the PR",
        DONE: "The farm cleaned up this issue",
    }
    farm_colors = {READY: "fbca04", WORKING: "1d76db", APPROVED: "0e8a16", ANSWER_QUESTION: "d93f0b", CHAT_WITH_AGENT: "d93f0b",
                   TIER_REVIEW: "e99695", PLAN_REVIEW: "e99695", MERGE_REVIEW: "e99695", DONE: "ededed"}
    for name in FARM_STATUSES:
        labels.append((farm_label(name), farm_colors[name], farm_descriptions[name]))
    for size in SIZES:
        labels.append((size_label(size), "5319e7", "Size {}: sets the gates and the model".format(size)))
    for name in PRIORITY_ORDER:
        labels.append((priority_label(name), "b60205" if name == "blocker" else "f9d0c4", "Priority: {}".format(name)))
    return labels
