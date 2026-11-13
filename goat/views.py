"""Read-only goat views: list issues, show one issue as Markdown, and check a repo's goat labels."""

import re

from goat import board as fb


# ---------- list ----------

def all_items(client, board):
    """Every goat issue of the project's repo, open or closed. The cleanup needs the closed ones."""
    return [fb.GoatItem.from_github(raw) for raw in client.board_items()]


def goat_items(client, board, user_id=None):
    """G.O.A.T. items of the project's repo. With user_id, only issues assigned to that user."""
    return only_goat_items(all_items(client, board), user_id)


def only_goat_items(items, user_id=None):
    """G.O.A.T. items in a list of items. With user_id, only issues assigned to that user."""
    return [item for item in items if item.is_goat_item and (user_id is None or item.is_owned_by(user_id))]


def waiting_hint(goat_status):
    """What a person does to move an issue that waits for them."""
    if fb.same_label(goat_status, fb.ANSWER_QUESTION):
        return "please answer the question above in a comment, then set the label goat:ready."
    if fb.same_label(goat_status, fb.CHAT_WITH_AGENT):
        return "please run `goat.py chat <issue number>` to talk with the agent."
    if fb.same_label(goat_status, fb.TIER_REVIEW):
        return "please confirm or change the size (labels size:S to size:XL), then set the label goat:approved."
    if fb.same_label(goat_status, fb.PLAN_REVIEW):
        return "please review the plan, then set the label goat:approved, or comment and set goat:ready to send it back."
    if fb.same_label(goat_status, fb.MERGE_REVIEW):
        return "please review and merge the PR, or comment and swap the labels to stage:implement and goat:ready."
    if fb.same_label(goat_status, fb.STALLED):
        return "agents stopped after repeated runs that changed nothing: check the agent log, then set the label goat:ready to retry."
    return ""


def next_step(item, now):
    """What happens next for an item, in a few words."""
    role = fb.next_role(item, now)
    if role:
        return "run /goat-{} {}".format(role, item.id)
    if fb.same_label(item.goat_status, fb.WORKING):
        expires = item.claim_expires.strftime("%H:%M UTC") if item.claim_expires else "?"
        return "{} agent working, claim until {}".format(item.claim_role, expires)
    if fb.same_label(item.goat_status, fb.ANSWER_QUESTION):
        return "waits for a person: answer the question in a comment, then set goat:ready"
    if fb.same_label(item.goat_status, fb.CHAT_WITH_AGENT):
        return "waits for a person: run goat.py chat {}".format(item.id)
    if fb.same_label(item.goat_status, fb.STALLED):
        return "waits for a person: stalled, check the agent log, then set goat:ready to retry"
    if item.goat_status in fb.GATES:
        return "waits for a person: {}".format(fb.display(item.goat_status))
    if fb.same_label(item.goat_status, fb.DONE):
        return "done"
    return "no goat step (stage {!r})".format(item.status)


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
            item.id, item.name[:40], item.status, item.goat_status, item.size, next_step(item, now)))
    return lines


# ---------- show ----------

def author_of(comment):
    return comment.get("author") or "?"


def updates_of(item):
    """The comments that people and agents wrote, oldest first: not G.O.A.T. state and not the doc sections."""
    return [comment for comment in item.comments
            if not fb.is_state_comment(comment.get("body")) and fb.doc_title(comment.get("body")) is None]


def doc_text(item, brief=False):
    """The doc sections as one Markdown text, or None when the issue has none. brief: only the newest section of
    each kind (see newest_sections)."""
    sections = newest_sections(item.doc_sections) if brief else item.doc_sections
    if not sections:
        return None
    return "\n\n".join(fb.doc_markdown(section["body"]) for section in sections)


# `show --brief` keeps this many of the newest comments.
BRIEF_COMMENTS = 6
REVISION = re.compile(r"\s*\((?:revision|round)\s+\d+\)\s*$", re.IGNORECASE)


def section_kind(title):
    """"Spec and plan (revision 2)" and "Spec and plan" are one kind; so are "Review notes (round 1)" and "(round 2)"."""
    return REVISION.sub("", title or "").strip().lower()


def newest_sections(sections):
    """The newest doc section of each kind, in the order the kinds first appeared."""
    newest = {}
    for section in sections:
        newest[section_kind(section["title"])] = section
    order = []
    for section in sections:
        kind = section_kind(section["title"])
        if kind not in order:
            order.append(kind)
    return [newest[kind] for kind in order]


def brief_comments(item):
    """(the newest BRIEF_COMMENTS comments, how many older ones were left out)."""
    comments = updates_of(item)
    return comments[-BRIEF_COMMENTS:], max(0, len(comments) - BRIEF_COMMENTS)


def show_markdown(item, now, brief=False):
    """The issue, its summary, description, doc and comments, as one Markdown document.

    brief: only the newest doc section of each kind and the newest comments, with a line that says what was left
    out. Agents that need the whole history (Review counting its rounds and finding IDs) use the full view."""
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
        "- Goat status: {}".format(item.goat_status or "none"),
        "- Size: {}".format(item.size or "none"),
        "- Priority: {}".format(item.priority or "none"),
        "- Assignee: {}".format(", ".join(item.owner_ids) or "none"),
        "- Milestone: {}".format(item.milestone or "none"),
        "- Claim: {}".format(claim),
        "- PR: {}".format(item.pr_url or "none"),
        "",
        "## Goat summary",
        "",
        item.summary or "None",
        "",
        "## Description",
        "",
        item.body.strip() or "None",
        "",
        "## Doc",
        "",
        doc_text(item, brief) or "None",
        "",
        "## Comments (oldest first)",
        "",
    ]
    comments, older = brief_comments(item) if brief else (updates_of(item), 0)
    if brief:
        dropped = len(item.doc_sections) - len(newest_sections(item.doc_sections))
        if dropped or older:
            out.insert(out.index("## Comments (oldest first)"),
                       "_Brief view: left out {} older doc section(s) and {} older comment(s). Run `goat.py show {}` "
                       "for everything._\n".format(dropped, older, item.id))
    if not comments:
        out.append("None")
    for comment in comments:
        out.append("### {} · {}".format(comment["created_at"], author_of(comment)))
        out += ["", comment["body"].strip(), ""]
    return "\n".join(out).rstrip() + "\n"


# ---------- setup-repo ----------

def board_config_problems(existing_labels):
    """G.O.A.T. labels that the repo lacks, as problems. existing_labels: label name -> label."""
    return ["No label {!r}.".format(name) for name, _, _ in fb.all_labels() if name not in existing_labels]
