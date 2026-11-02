"""`farm.py setup-repo`: creates the farm's labels (and an optional milestone) in a GitHub repo, safely to repeat.

With check_only it changes nothing and reports what is missing.
"""

from farm import board as fb


def setup_repo(client, repo, check_only=False, milestone=None):
    """Returns (lines to print, problems). problems is empty when the repo has every label."""
    existing = client.existing_labels()
    lines = []
    problems = []
    counts = {"created": 0, "updated": 0, "ok": 0}
    for name, color, description in fb.all_labels():
        if check_only:
            if name not in existing:
                problems.append("No label {!r}.".format(name))
            continue
        counts[client.ensure_label(name, color, description, existing)] += 1
    if check_only:
        lines.append("Checked {} labels in {}: {} missing.".format(len(fb.all_labels()), repo, len(problems)))
    else:
        lines.append("Labels in {}: {} created, {} updated, {} already right.".format(
            repo, counts["created"], counts["updated"], counts["ok"]))
        if milestone:
            lines.append("Milestone {!r}: {}.".format(milestone, client.ensure_milestone(milestone)))
    lines.append("")
    lines.append("# Optional: put this in the checkout's .env when its origin remote is not the repo that holds the issues")
    lines.append("FARM_GITHUB_REPO={}".format(repo))
    return lines, problems
