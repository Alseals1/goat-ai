"""The Label Doctor: finds issues whose labels G.O.A.T. cannot act on, and says the exact fix. It only reads.

A person moves an issue by editing its labels, and a small slip (a missing goat:ready, the wrong label removed at a
gate) used to make G.O.A.T. ignore the issue without a word. `goat.py doctor` lists each such issue with the problem
in plain words and the label change that fixes it. It never changes a label itself, and when two labels disagree it
names both choices instead of guessing which one the person meant.

It checks the issues assigned to the `gh` user, and unassigned ones. Issues assigned to someone else are theirs.
"""

from dataclasses import dataclass
from datetime import timedelta

from goat import board as fb

# goat:working with a claim that ended this long ago means nothing took the issue over.
STALE_CLAIM = timedelta(hours=1)
# Each gate is set at one stage. Approving it starts the next stage's agent (board.NEXT_STAGE).
GATE_STAGE = {fb.TIER_REVIEW: "intake", fb.PLAN_REVIEW: "plan", fb.MERGE_REVIEW: "review"}


@dataclass(frozen=True)
class Problem:
    """code: a stable name for scripts. short: a few words for `list --problems`. text: what is wrong and what it
    causes. fix: the label change that fixes it."""
    code: str
    short: str
    text: str
    fix: str


def code(name):
    return "`{}`".format(name)


def codes(names):
    """`a`, `b` and `c`."""
    quoted = [code(name) for name in names]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " and " + quoted[-1]


def with_prefix(labels, prefix):
    """The labels that start with the prefix, as (label, name without the prefix). Matched as goat/board.py does."""
    return [(label, label[len(prefix):]) for label in labels if label.startswith(prefix)]


def in_scope(item, user_id):
    """The doctor checks the user's own issues and unassigned ones."""
    return not item.owner_ids or user_id is None or item.is_owned_by(user_id)


def ago(delta):
    minutes = int(delta.total_seconds() // 60)
    if minutes < 120:
        return "{} minutes".format(minutes)
    hours = minutes // 60
    return "{} hours".format(hours) if hours < 48 else "{} days".format(hours // 24)


def diagnose(item, user_id, now):
    """The problems of one issue, most basic first. An empty list means G.O.A.T. can read the issue."""
    stages = with_prefix(item.labels, fb.STAGE_PREFIX)
    statuses = with_prefix(item.labels, fb.GOAT_PREFIX)
    problems = []

    unknown = [label for label, name in stages if name not in fb.STAGE_NAMES]
    unknown += [label for label, name in statuses if name not in fb.GOAT_STATUSES]
    if unknown:
        problems.append(Problem(
            "unknown-label", "unknown label " + ", ".join(unknown),
            "{} {} not a G.O.A.T. label, so nothing reads it.".format(codes(unknown), "is" if len(unknown) == 1 else "are"),
            "Remove {}. If you meant a G.O.A.T. label, add that one instead (`goat.py guide` lists them).".format(
                codes(unknown))))
    stages = [(label, name) for label, name in stages if name in fb.STAGE_NAMES]
    statuses = [(label, name) for label, name in statuses if name in fb.GOAT_STATUSES]

    if item.status == fb.DONE_STATUS:
        return problems + closed_problems(statuses)

    gate = next((name for _, name in statuses if name in GATE_STAGE), None)
    if len(stages) > 1:
        names = [label for label, _ in stages]
        fix = "Keep the stage the issue is really at and remove the others."
        if gate and GATE_STAGE[gate] in [name for _, name in stages]:
            keep = fb.stage_label(GATE_STAGE[gate])
            fix += " `goat:{}` is set at {}, so probably keep {} and remove {}.".format(
                gate, code(keep), code(keep), codes([name for name in names if name != keep]))
        problems.append(Problem("many-stages", "{} stage labels".format(len(stages)),
                                "It has {} stage labels ({}), so G.O.A.T. cannot tell where it is.".format(
                                    len(stages), codes(names)), fix))
    elif not stages and statuses:
        if gate:
            fix = "Add {}: `goat:{}` is set at that stage.".format(code(fb.stage_label(GATE_STAGE[gate])), gate)
        else:
            fix = "Add the label of the stage it is at: {}.".format(
                codes([fb.stage_label(stage) for stage in fb.STAGES]).replace(" and ", " or "))
        problems.append(Problem("no-stage", "no stage label",
                                "It has a goat: label but no stage label, so no agent can pick it up.", fix))

    if len(statuses) > 1:
        names = [label for label, _ in statuses]
        others = [label for label, name in statuses if name != fb.APPROVED]
        if len(others) < len(names) and all(name in GATE_STAGE for _, name in statuses if name != fb.APPROVED):
            fix = "If you approved the gate, remove {}. If you did not, remove `goat:approved`.".format(codes(others))
        else:
            fix = "Keep the one you mean and remove the others."
        problems.append(Problem("many-statuses", "{} goat: labels".format(len(statuses)),
                                "It has {} goat: labels ({}), so G.O.A.T. cannot tell what it waits for.".format(
                                    len(statuses), codes(names)), fix))

    if not item.owner_ids and (statuses or any(name in fb.STAGES for _, name in stages)):
        problems.append(Problem("unassigned", "not assigned",
                                "Nobody is assigned, and G.O.A.T. only works on issues assigned to the person it "
                                "runs as.", "Assign it to yourself{}.".format(
                                    " ({})".format(code(user_id)) if user_id else "")))

    if len(stages) == 1 and len(statuses) <= 1:
        stage = stages[0][1]
        status = statuses[0][1] if statuses else None
        problems += stage_problems(item, stage, status, now)
    return problems


def closed_problems(statuses):
    """A closed issue is finished. Only goat:done belongs on it, and the cleanup sets that."""
    leftover = [label for label, name in statuses if name != fb.DONE]
    if not leftover:
        return []
    return [Problem("closed-leftover", "closed with " + ", ".join(leftover),
                    "The issue is closed but still has {}, so lists show a status it no longer has.".format(
                        codes(leftover)),
                    "Run `goat.py cleanup`: it removes the issue's local worktrees and branches, then sets `goat:done` "
                    "in place of {}. Or remove {} by hand.".format(codes(leftover), "it" if len(leftover) == 1 else
                                                                  "them"))]


def stage_problems(item, stage, status, now):
    """Problems of an issue with one stage label and at most one goat: label."""
    stage_tag = code(fb.stage_label(stage))
    status_tag = code(fb.goat_label(status)) if status else None
    if status is None:
        if stage not in fb.STAGES:
            return []
        return [Problem("no-status", "no goat: label",
                        "It is at {} but has no goat: label, so G.O.A.T. ignores it.".format(stage_tag),
                        "Add `goat:ready` to start the {} agent.".format(stage))]
    if status == fb.DONE:
        return [Problem("done-open", "goat:done on an open issue",
                        "`goat:done` is set by the cleanup after an issue is closed. On an open issue it makes the "
                        "cleanup skip the issue later, so its worktree and branches stay on disk.",
                        "Remove `goat:done`. Close the issue when the work is finished, and the cleanup sets it.")]
    if stage == fb.DISCOVERY:
        return [Problem("backlog-status", "{} in the backlog".format(fb.goat_label(status)),
                        "G.O.A.T. ignores the backlog ({}), so {} does nothing.".format(stage_tag, status_tag),
                        "To start it, swap {} for `stage:intake` and set `goat:ready`. To keep it in the backlog, "
                        "remove {}.".format(stage_tag, status_tag))]
    if stage == fb.STAGING:
        return [Problem("staging-status", "{} after the merge".format(fb.goat_label(status)),
                        "At {} the PR is merged and no agent runs, so {} does nothing.".format(stage_tag, status_tag),
                        "Remove {}. Close the issue when the work is finished.".format(status_tag))]
    if status == fb.APPROVED and stage not in fb.NEXT_STAGE:
        if stage == "review":
            fix = ("To finish, merge the PR. To run Review again, set `goat:ready`. To send it back, swap "
                   "`stage:review` for `stage:implement` and set `goat:ready`.")
        else:
            fix = "Swap `goat:approved` for `goat:ready` to run the {} agent again.".format(stage)
        return [Problem("approved-dead-end", "goat:approved at " + fb.stage_label(stage),
                        "`goat:approved` at {} starts nothing: only the size gate (`stage:intake`) and the plan gate "
                        "(`stage:plan`) are approved with a label.".format(stage_tag), fix)]
    if status in GATE_STAGE and GATE_STAGE[status] != stage:
        right = code(fb.stage_label(GATE_STAGE[status]))
        return [Problem("gate-wrong-stage", "{} at {}".format(fb.goat_label(status), fb.stage_label(stage)),
                        "{} belongs to {}, but the issue is at {}. Acting on the gate would start the wrong "
                        "agent.".format(status_tag, right, stage_tag),
                        "If the issue really waits at that gate, swap {} for {}. If not, swap {} for `goat:ready` to "
                        "run the {} agent.".format(stage_tag, right, status_tag, stage))]
    if status == fb.WORKING and item.comments_loaded:
        return working_problems(item, stage, now)
    return []


def working_problems(item, stage, now):
    """goat:working with no agent behind it: no claim, or one that ended long ago and nothing took it over."""
    retry = ("Check that the coordinator is running (`goat.py health`): it starts the {} agent again on its next "
             "turn. Or run `/goat-{} {}` yourself.".format(stage, stage, item.id))
    if not item.claim:
        return [Problem("working-no-claim", "goat:working, no claim",
                        "It has `goat:working` but no agent holds a claim on it.", retry)]
    if item.claim_expires is not None and now - item.claim_expires > STALE_CLAIM:
        return [Problem("working-stale", "goat:working, claim ended {} ago".format(ago(now - item.claim_expires)),
                        "It has `goat:working`, but the {} agent's claim ended {} ago and nothing took it "
                        "over.".format(item.claim_role or "?", ago(now - item.claim_expires)), retry)]
    return []


def check(items, user_id, now):
    """[(item, problems)] for the issues in scope that have problems: open issues first, then by number. Also returns
    how many issues were checked."""
    checked = [item for item in items if in_scope(item, user_id)]
    found = [(item, diagnose(item, user_id, now)) for item in checked]
    found = [(item, problems) for item, problems in found if problems]
    found.sort(key=lambda pair: (pair[0].status == fb.DONE_STATUS, int(pair[0].id)))
    return found, len(checked)


def label_summary(item):
    names = [label for label in item.labels if label.startswith((fb.STAGE_PREFIX, fb.GOAT_PREFIX))]
    return ", ".join(names) or "no stage or goat: label"


def report_lines(found, checked):
    """The doctor's text report."""
    lines = []
    for item, problems in found:
        state = " (closed)" if item.status == fb.DONE_STATUS else ""
        lines.append("#{} {}{}  [{}]".format(item.id, item.name, state, label_summary(item)))
        for problem in problems:
            lines += ["  Problem: " + problem.text, "  Fix: " + problem.fix]
        lines.append("")
    count = sum(len(problems) for _, problems in found)
    if found:
        lines.append("Checked {} issue(s), yours and unassigned: {} problem(s) on {} issue(s).".format(
            checked, count, len(found)))
    else:
        lines.append("Checked {} issue(s), yours and unassigned: no problems found.".format(checked))
    return lines


def problem_lines(found):
    """`list --problems`: one line per issue, in the `list` layout, with the problems in place of the next step."""
    return ["#{:<5} {:<40} {:<10} {:<16} {:<3} ! {}".format(
        item.id, item.name[:40], item.status, item.goat_status, item.size,
        "; ".join(problem.short for problem in problems)) for item, problems in found]
