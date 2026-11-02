"""JSON versions of the read-only views (`list`, `show`, `setup-repo` with --json), for agents and scripts.

Each builder returns a plain dict with a top-level "version". Empty text is null.
"""

from farm import board as fb
from farm import views
from farm.actions import iso

# Change it when a JSON shape changes, so agents can detect it.
VERSION = "0.1"


def or_none(text):
    return text or None


def claim_data(item, now):
    if not item.claim:
        return None
    return {
        "role": item.claim_role,
        "session": item.claim_session,
        "expiresAt": iso(item.claim_expires) if item.claim_expires else None,
        "live": item.claim_is_live(now),
    }


def item_data(item, now):
    """One issue's labels, its next role and its next step."""
    return {
        "id": item.id,
        "name": item.name,
        "url": item.url,
        "status": or_none(item.status),
        "stage": item.stage,
        "farmStatus": or_none(item.farm_status),
        "size": or_none(item.size),
        "priority": or_none(item.priority),
        "assignees": item.owner_ids,
        "milestone": or_none(item.milestone),
        "claim": claim_data(item, now),
        "prUrl": or_none(item.pr_url),
        "updatedAt": item.updated_at,
        "nextRole": fb.next_role(item, now),
        "nextStep": views.next_step(item, now),
    }


def list_data(items, now):
    return {"version": VERSION, "items": [item_data(item, now) for item in views.list_order(items, now)]}


def comment_data(comment):
    """A comment: when, who, and the text."""
    return {"createdAt": comment["created_at"], "author": views.author_of(comment), "text": comment["body"].strip()}


def show_data(item, now):
    """The issue with its summary, description, doc and comments (oldest first)."""
    return {
        "version": VERSION,
        "item": dict(item_data(item, now), summary=or_none(item.summary)),
        "description": or_none(item.body.strip()),
        "doc": views.doc_text(item),
        "comments": [comment_data(comment) for comment in views.updates_of(item)],
    }


def setup_data(repo, lines, problems):
    """The setup lines and the problems found. ok is false when there are problems."""
    return {"version": VERSION, "repo": repo, "lines": lines, "problems": problems, "ok": not problems}
