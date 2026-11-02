"""Moves issues forward when a person has merged their PR.

A person merges the PR on GitHub (agents never do). The coordinator sees the merge and moves the issue to
stage:staging, with no goat: label, so nobody has to swap labels by hand. Closing the issue is still a person's
step: it is what starts the retro and the cleanup.

The PR body says "Refs #<number>", never "Closes #<number>", so a merge does not close the issue by itself.
It can be turned off with "autoAdvanceMerged": false in the coordinator config.
"""

import re

from goat import board as fb

BRANCH = re.compile(r"^goat/(\d+)(?:-|$)")


def branch_item_id(branch):
    """The issue number at the start of a goat/<number>-<slug> branch name, or None."""
    match = BRANCH.match(branch or "")
    return match.group(1) if match else None


def mergeable_items(items, user_id, now):
    """The user's issues that wait at merge review with no live claim."""
    return [item for item in items
            if item.is_owned_by(user_id) and fb.same_label(item.goat_status, fb.MERGE_REVIEW)
            and not item.claim_is_live(now)]


def merged_items(candidates, merged_prs, open_prs):
    """The candidates whose PR is merged. Returns [(item, [merged PR urls])].

    An issue counts as merged when one of its goat/<number>-* branches has a merged PR, the PR in its PR link (the
    bottom PR of a stack) is merged when it has one, and none of its branches still has an open PR.
    """
    found = []
    for item in candidates:
        merged = [pr for pr in merged_prs if branch_item_id(pr["branch"]) == item.id]
        still_open = [pr for pr in open_prs if branch_item_id(pr["branch"]) == item.id]
        if not merged or still_open:
            continue
        if item.pr_url and item.pr_url not in [pr["url"] for pr in merged]:
            continue
        found.append((item, [pr["url"] for pr in merged]))
    return found


def advance_merged(client, items, user_id, now, dry_run=False):
    """Moves each merged issue to stage:staging. Returns one log line per issue."""
    candidates = mergeable_items(items, user_id, now)
    if not candidates:
        return []
    found = merged_items(candidates, client.pull_requests("merged"), client.pull_requests("open"))
    lines = []
    for item, urls in found:
        if dry_run:
            lines.append("Would move #{} to stage:staging: its PR is merged.".format(item.id))
            continue
        client.create_comment(item.id, "**🤖 G.O.A.T.** · The PR is merged: {}\n\nMoved to stage:staging. "
                                       "Close this issue when it is done: that starts the retro and the cleanup.".format(
                                           ", ".join(urls)))
        add, remove = fb.label_changes(item, stage=fb.STAGING, clear_goat_status=True)
        client.set_labels(item.id, add=add, remove=remove)
        lines.append("Moved #{} to stage:staging: its PR is merged.".format(item.id))
    return lines
