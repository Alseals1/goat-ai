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


STRANDED_MARKER = "<!-- goat:stranded -->"


def classify(candidates, merged_prs, open_prs, on_default):
    """Sorts the candidates into (done, stranded).

    done:     [(item, [merged PR urls])] for issues whose merged work is all on the default branch.
    stranded: [(item, [PRs])] for issues with a merged PR whose commits are NOT on the default branch.

    An issue is only judged when it has merged PRs, none of its goat/<number>-* branches has an open PR, and the PR
    stored on the issue (the bottom PR of a stack) is among the merged ones. `on_default(pr)` says whether a merged
    PR's commits reached the default branch: a PR merged straight into it did, and so did a stack part that was
    merged into the part below it, once that part itself was merged into the default branch.
    """
    done, stranded = [], []
    for item in candidates:
        own = [pr for pr in merged_prs if branch_item_id(pr["branch"]) == item.id]
        if not own:
            continue
        if [pr for pr in open_prs if branch_item_id(pr["branch"]) == item.id]:
            continue
        if item.pr_url and item.pr_url not in [pr["url"] for pr in own]:
            continue
        lost = [pr for pr in own if not on_default(pr)]
        if lost:
            stranded.append((item, lost))
        else:
            done.append((item, [pr["url"] for pr in own]))
    return done, stranded


def stranded_text(item, lost, default_branch):
    parts = ", ".join("#{} (merged into `{}`)".format(pr["number"], pr["base"]) for pr in lost)
    return (STRANDED_MARKER + "\n**🤖 G.O.A.T.** · Part of this work is **not on `{default}` yet**: {parts}.\n\n"
            "In a stack, a part merged into the branch below it only reaches `{default}` when that branch is merged "
            "too. The issue stays at merge review until it is. To finish: open a pull request from the top branch of "
            "the stack into `{default}` and merge it, then this issue moves on by itself. (Turning on "
            "\"Automatically delete head branches\" in the repo settings avoids this: GitHub then re-targets the "
            "next part to `{default}` after each merge.)").format(default=default_branch, parts=parts)


def advance_merged(client, items, user_id, now, dry_run=False):
    """Moves each finished issue to stage:staging, and flags stranded stacks. Returns one log line per issue."""
    candidates = mergeable_items(items, user_id, now)
    if not candidates:
        return []
    default = client.default_branch()

    def on_default(pr):
        if pr.get("base") == default:
            return True
        return bool(pr.get("merge_commit")) and client.commit_on_branch(pr["merge_commit"], default)

    done, stranded = classify(candidates, client.pull_requests("merged"), client.pull_requests("open"), on_default)
    lines = []
    for item, urls in done:
        if dry_run:
            lines.append("Would move #{} to stage:staging: its PR is merged.".format(item.id))
            continue
        client.create_comment(item.id, "**🤖 G.O.A.T.** · The PR is merged: {}\n\nMoved to stage:staging. "
                                       "Close this issue when it is done: that starts the retro and the cleanup.".format(
                                           ", ".join(urls)))
        add, remove = fb.label_changes(item, stage=fb.STAGING, clear_goat_status=True)
        client.set_labels(item.id, add=add, remove=remove)
        lines.append("Moved #{} to stage:staging: its PR is merged.".format(item.id))
    for item, lost in stranded:
        already_told = any(STRANDED_MARKER in (comment.get("body") or "") for comment in item.comments)
        lines.append("Not moving #{}: PR {} merged into another branch, so its work is not on {} yet.".format(
            item.id, ", ".join("#{}".format(pr["number"]) for pr in lost), default))
        if not dry_run and not already_told:
            client.create_comment(item.id, stranded_text(item, lost, default))
    return lines
