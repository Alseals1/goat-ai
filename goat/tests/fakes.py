"""Shared test fakes: an in-memory GitHub (FakeClient) and a builder for raw issues.

FakeClient has the same methods as goat.github.GitHubClient, so Goat, the views and the cleanup run against it with no
network. Test hooks:
- after_write(kind, client): called after each write (kind is "labels", "comment" or "update_comment"), to simulate a
  person changing the issue between a claim's write and its read-back.
- writes: a list of (kind, number, detail) for each write, to assert what was written.
"""

import copy

from goat import board as fb
from goat.github import GitHubError, ItemNotFound, is_goat_issue

REPO = "me/app"
ME = {"id": "me", "name": "me"}
BOARD = {"id": REPO, "repo": REPO, "doneLabel": "done"}
SHARED = {
    "leaseMinutes": 30,
    "leaseMinutesByStage": {"intake": 10, "plan": 30, "implement": 60, "review": 15},
}
UPDATED = "2026-10-27T10:00:00Z"


def state_comment(claim="", expires="", summary="", pr_url="", comment_id=900):
    body = fb.render_state({"claim": claim, "claimExpires": expires, "summary": summary, "prUrl": pr_url})
    return {"id": comment_id, "created_at": UPDATED, "author": "me", "body": body}


def raw_item(number, stage="intake", goat_status="ready", state="open", assignees=("me",), size="", priority="",
             title=None, labels=None, claim="", expires="", summary="", pr_url="", comments=None, milestone=None,
             body="", updated_at=UPDATED):
    """A raw issue as goat.github.GitHubClient returns it. stage "" means no stage label, goat_status "" no goat label."""
    names = list(labels or [])
    if stage:
        names.append(fb.stage_label(stage))
    if goat_status:
        names.append(fb.goat_label(goat_status))
    if size:
        names.append(fb.size_label(size))
    if priority:
        names.append(fb.priority_label(priority))
    found = list(comments or [])
    if claim or summary or pr_url:
        found.insert(0, state_comment(claim, expires, summary, pr_url))
    return {
        "repo": REPO,
        "number": int(number),
        "title": title or "Issue {}".format(number),
        "url": "https://github.com/{}/issues/{}".format(REPO, number),
        "updated_at": updated_at,
        "state": state,
        "labels": names,
        "assignees": list(assignees),
        "milestone": milestone,
        "body": body,
        "comments": found,
    }


class FakeClient:
    def __init__(self, *raw_items, user=ME):
        self.items = {item["number"]: copy.deepcopy(item) for item in raw_items}
        self.user = user
        self.writes = []
        self.after_write = None
        self.next_comment_id = 1000
        self.labels = {name: {"name": name, "color": color, "description": description}
                       for name, color, description in fb.all_labels()}
        self.milestones = {}
        self.open_prs = []
        self.merged_prs = []
        self.default = "main"
        # Merge commits that are part of the default branch (for PRs that were merged into another branch).
        self.commits_on_default = set()
        self.created = []

    # ---------- reads ----------

    def me(self):
        return self.user

    def get_item(self, number):
        try:
            return copy.deepcopy(self.items[int(number)])
        except KeyError:
            raise ItemNotFound("GitHub: not found (#{})".format(number))

    def comments(self, number):
        return copy.deepcopy(self.items[int(number)]["comments"])

    def board_items(self):
        found = []
        for item in self.items.values():
            if not is_goat_issue(item["labels"]):
                continue
            entry = copy.deepcopy(item)
            if "goat:working" not in entry["labels"] and "goat:merge-review" not in entry["labels"]:
                entry.pop("comments", None)
            found.append(entry)
        return found

    def pull_requests(self, state, limit=100):
        return copy.deepcopy(self.merged_prs if state == "merged" else self.open_prs)

    def default_branch(self):
        return self.default

    def commit_on_branch(self, commit, branch):
        return commit in self.commits_on_default

    def existing_labels(self):
        return dict(self.labels)

    # ---------- writes ----------

    def _wrote(self, kind, number, detail):
        self.writes.append((kind, number, detail))
        if self.after_write:
            self.after_write(kind, self)

    def set_labels(self, number, add=(), remove=()):
        item = self.items[int(number)]
        item["labels"] = [name for name in item["labels"] if name not in remove]
        for name in add:
            if name not in self.labels:
                # What `gh issue edit --add-label` does for a label the repo does not have.
                raise GitHubError("gh issue edit failed: could not add label: '{}' not found".format(name))
            if name not in item["labels"]:
                item["labels"].append(name)
        self._wrote("labels", int(number), {"add": list(add), "remove": list(remove)})

    def create_comment(self, number, body):
        self.next_comment_id += 1
        comment = {"id": self.next_comment_id, "created_at": UPDATED, "author": self.user["id"], "body": body}
        self.items[int(number)]["comments"].append(comment)
        self._wrote("comment", int(number), body)
        return comment["id"]

    def update_comment(self, comment_id, body):
        for number, item in self.items.items():
            for comment in item["comments"]:
                if comment["id"] == comment_id:
                    comment["body"] = body
                    self._wrote("update_comment", number, body)
                    return
        raise AssertionError("no comment {}".format(comment_id))

    def create_issue(self, title, body="", labels=(), assignees=(), milestone=None):
        number = max(self.items, default=0) + 1
        self.items[number] = raw_item(number, stage="", goat_status="", title=title, labels=labels, assignees=assignees,
                                      body=body, milestone=milestone)
        self.created.append(number)
        return number

    def ensure_label(self, name, color, description, existing):
        self.writes.append(("ensure_label", None, name))
        if name not in self.labels:
            self.labels[name] = {"name": name, "color": color, "description": description}
            return "created"
        return "ok"

    def ensure_milestone(self, title, description=""):
        if title in self.milestones:
            return "ok"
        self.milestones[title] = description
        return "created"

    # ---------- test helpers ----------

    def labels_of(self, number):
        return sorted(self.items[int(number)]["labels"])

    def comment_bodies(self, number):
        return [comment["body"] for comment in self.items[int(number)]["comments"]]
