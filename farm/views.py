"""Read-only farm views: list issues, show one issue as Markdown, and check a repo's farm labels."""

from farm import board as fb


# ---------- list ----------

def all_items(client, board):
    """Every farm issue of the project's repo, open or closed. The cleanup needs the closed ones."""
    return [fb.FarmItem.from_github(raw) for raw in client.board_items()]


def farm_items(client, board, user_id=None):
    """The farm items of the project's repo. With user_id, only issues assigned to that user."""
    return only_farm_items(all_items(client, board), user_id)


def only_farm_items(items, user_id=None):
    """The farm items in a list of items. With user_id, only issues assigned to that user."""
    return [item for item in items if item.is_farm_item and (user_id is None or item.is_owned_by(user_id))]


def waiting_hint(farm_status):
    """What a person does to move an issue that waits for them."""
    if fb.same_label(farm_status, fb.ANSWER_QUESTION):
        return "please answer the question above in a comment, then set the label farm:ready."
    if fb.same_label(farm_status, fb.CHAT_WITH_AGENT):
        return "please run `farm.py chat <issue number>` to talk with the agent."
    if fb.same_label(farm_status, fb.TIER_REVIEW):
        return "please confirm or change the size (labels size:S to size:XL), then set the label farm:approved."
    if fb.same_label(farm_status, fb.PLAN_REVIEW):
        return "please review the plan, then set the label farm:approved, or comment and set farm:ready to send it back."
    if fb.same_label(farm_status, fb.MERGE_REVIEW):
        return "please review and merge the PR, or comment and swap the labels to stage:implement and farm:ready."
    if fb.same_label(farm_status, fb.STALLED):
        return "agents stopped after repeated runs that changed nothing: check the agent log, then set the label farm:ready to retry."
    return ""


def next_step(item, now):
    """What happens next for an item, in a few words."""
    role = fb.next_role(item, now)
    if role:
        return "run /farm-{} {}".format(role, item.id)
    if fb.same_label(item.farm_status, fb.WORKING):
        expires = item.claim_expires.strftime("%H:%M UTC") if item.claim_expires else "?"
        return "{} agent working, claim until {}".format(item.claim_role, expires)
    if fb.same_label(item.farm_status, fb.ANSWER_QUESTION):
        return "waits for a person: answer the question in a comment, then set farm:ready"
    if fb.same_label(item.farm_status, fb.CHAT_WITH_AGENT):
        return "waits for a person: run farm.py chat {}".format(item.id)
    if fb.same_label(item.farm_status, fb.STALLED):
        return "waits for a person: stalled, check the agent log, then set farm:ready to retry"
    if item.farm_status in fb.GATES:
        return "waits for a person: {}".format(fb.display(item.farm_status))
    if fb.same_label(item.farm_status, fb.DONE):
        return "done"
    return "no farm step (stage {!r})".format(item.status)


def list_order(items, now):
    """Items that wait for a person first, then the ones an agent can start. Text and JSON use this order."""
    def order(item):
        waits = next_step(item, now).startswith("waits")
        return (0 if waits else 1, fb.priority_rank(item.priority), item.updated_at)

    return sorted(items, key=order)


def list_lines(items, now):
    lines = []
    for item in list_order(items, now):
        lines.append("#{:<5} {:<40} {:<10} {:<16} {:<3} {}".format(
            item.id, item.name[:40], item.status, item.farm_status, item.size, next_step(item, now)))
    return lines


# ---------- show ----------

def author_of(comment):
    return comment.get("author") or "?"


def updates_of(item):
    """The comments that people and agents wrote, oldest first: not the farm state and not the doc sections."""
    return [comment for comment in item.comments
            if not fb.is_state_comment(comment.get("body")) and fb.doc_title(comment.get("body")) is None]


def doc_text(item):
    """The doc sections as one Markdown text, or None when the issue has none."""
    sections = item.doc_sections
    if not sections:
        return None
    return "\n\n".join(fb.doc_markdown(section["body"]) for section in sections)


def show_markdown(item, now):
    """The issue, its summary, description, doc and comments, as one Markdown document."""
    claim = "none"
    if item.claim:
        state = "live" if item.claim_is_live(now) else "expired"
        until = item.claim_expires.strftime("%Y-%m-%d %H:%M UTC") if item.claim_expires else "?"
        claim = "{} ({}, until {})".format(item.claim, state, until)
    out = [
        "# #{} {}".format(item.id, item.name),
        "",
        "- Link: {}".format(item.url),
        "- Stage: {} (agent stage: {})".format(item.status or "none", item.stage or "none"),
        "- Farm status: {}".format(item.farm_status or "none"),
        "- Size: {}".format(item.size or "none"),
        "- Priority: {}".format(item.priority or "none"),
        "- Assignee: {}".format(", ".join(item.owner_ids) or "none"),
        "- Milestone: {}".format(item.milestone or "none"),
        "- Claim: {}".format(claim),
        "- PR: {}".format(item.pr_url or "none"),
        "",
        "## Farm summary",
        "",
        item.summary or "None",
        "",
        "## Description",
        "",
        item.body.strip() or "None",
        "",
        "## Doc",
        "",
        doc_text(item) or "None",
        "",
        "## Comments (oldest first)",
        "",
    ]
    comments = updates_of(item)
    if not comments:
        out.append("None")
    for comment in comments:
        out.append("### {} · {}".format(comment["created_at"], author_of(comment)))
        out += ["", comment["body"].strip(), ""]
    return "\n".join(out).rstrip() + "\n"


# ---------- setup-repo ----------

def board_config_problems(existing_labels):
    """The farm labels that the repo lacks, as problems. existing_labels: label name -> label."""
    return ["No label {!r}.".format(name) for name, _, _ in fb.all_labels() if name not in existing_labels]
