"""The farm's only door to GitHub: Issues, comments, labels, milestones and pull requests, through the `gh` CLI.

Nothing else in the farm talks to GitHub for task storage. `gh` uses the developer's own login (`gh auth login`,
or GH_TOKEN), so there is no token to configure. Agents never call this module or `gh api` themselves: they use
farm.py, which uses this client. The deny rules in agent-settings.json keep `gh api` away from agents.

A "raw item" is a plain dict, so tests can fake the client without GitHub:

    {"repo": "owner/name", "number": 14, "title": ..., "url": ..., "updated_at": "2026-11-01T10:00:00Z", "state": "open" | "closed",
     "labels": ["stage:plan", ...], "assignees": ["login", ...], "milestone": "v0.1" | None, "body": "...",
     "comments": [{"id": 1, "created_at": ..., "author": "login", "body": "..."}, ...]}

`comments` is present on get_item. board_items adds it only to items with the farm:working label (for the claim)
or the farm:merge-review label (for the PR link), and one extra call per item on every turn would be slow.
"""

import json
import re
import subprocess
import time

from farm import exit_codes

# Issues the farm reads with one `gh issue list`. A repo with more farm issues than this needs paging.
LIST_LIMIT = 500
GH_TIMEOUT_SECONDS = 60
# Failures that are worth another try. Everything else raises at once.
TRANSIENT = re.compile(r"rate limit|secondary rate|timeout|timed out|502|503|504|connection (reset|refused)|EOF",
                       re.IGNORECASE)
RETRY_DELAYS = (2, 8)
# The label prefixes that mark a farm issue.
FARM_LABEL_PREFIXES = ("farm:", "stage:")


class GitHubError(Exception):
    exit_code = exit_codes.GITHUB_API


class ItemNotFound(GitHubError):
    exit_code = exit_codes.ITEM_NOT_AVAILABLE


def run_gh(args, input_text=None):
    """Runs `gh` and returns its stdout. Raises GitHubError with gh's message on a failure."""
    try:
        result = subprocess.run(["gh", *args], input=input_text, capture_output=True, text=True,
                                timeout=GH_TIMEOUT_SECONDS)
    except FileNotFoundError:
        raise GitHubError("The gh command is not installed. Install the GitHub CLI and run `gh auth login`.")
    except subprocess.TimeoutExpired:
        raise GitHubError("gh timed out after {} seconds: gh {}".format(GH_TIMEOUT_SECONDS, " ".join(args[:3])))
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()[:300]
        if "auth login" in message or "not logged in" in message.lower():
            message += " Run `gh auth login`."
        if "Could not resolve to an Issue" in message or "HTTP 404" in message or "Not Found" in message:
            raise ItemNotFound("GitHub: not found ({})".format(message))
        raise GitHubError("gh {} failed: {}".format(" ".join(args[:3]), message))
    return result.stdout


def label_names(labels):
    return [label["name"] if isinstance(label, dict) else label for label in labels or []]


def is_farm_issue(label_list):
    return any(name.startswith(FARM_LABEL_PREFIXES) for name in label_list)


class GitHubClient:
    """Issues of one repository ("owner/name"). `runner(args, input_text)` is the test seam: it returns gh's stdout."""

    def __init__(self, repo, runner=run_gh, sleep=time.sleep):
        self.repo = repo
        self.runner = runner
        self.sleep = sleep

    # ---------- plumbing ----------

    def gh(self, args, input_text=None):
        """Runs gh with a few retries for rate limits and network blips."""
        for delay in (*RETRY_DELAYS, None):
            try:
                return self.runner(args, input_text)
            except ItemNotFound:
                raise
            except GitHubError as error:
                if delay is None or not TRANSIENT.search(str(error)):
                    raise
                self.sleep(delay)

    def api(self, method, path, payload=None, paginate=False):
        """A REST call. The JSON payload goes in on stdin, so long Markdown needs no shell quoting."""
        args = ["api", "-X", method, path.format(repo=self.repo)]
        if paginate:
            args += ["--paginate", "--slurp"]
        if payload is not None:
            args += ["--input", "-"]
        out = self.gh(args, json.dumps(payload) if payload is not None else None)
        if not out.strip():
            return None
        try:
            data = json.loads(out)
        except ValueError:
            raise GitHubError("GitHub returned something that is not JSON: {}".format(out[:200]))
        if paginate:
            # --slurp wraps the pages in one list.
            data = [entry for page in data for entry in (page if isinstance(page, list) else [page])]
        return data

    # ---------- reads ----------

    def me(self):
        login = self.api("GET", "user")["login"]
        return {"id": login, "name": login}

    def get_item(self, number):
        """One issue with all its comments. Raises ItemNotFound for a missing issue or for a pull request."""
        raw = self.api("GET", "repos/{repo}/issues/%s" % int(number))
        if raw.get("pull_request"):
            raise ItemNotFound("#{} is a pull request, not an issue.".format(number))
        item = raw_item(raw)
        item["repo"] = self.repo
        item["comments"] = self.comments(number)
        return item

    def comments(self, number):
        found = self.api("GET", "repos/{repo}/issues/%s/comments?per_page=100" % int(number), paginate=True) or []
        return [{"id": c["id"], "created_at": c["created_at"], "author": (c.get("user") or {}).get("login", "?"),
                 "body": c.get("body") or ""} for c in found]

    def board_items(self):
        """Every farm issue of the repo, open or closed: those with a farm:* or stage:* label.

        Only the farm:working and farm:merge-review ones carry their comments (the claim, the PR link).
        """
        out = self.gh(["issue", "list", "--repo", self.repo, "--state", "all", "--limit", str(LIST_LIMIT),
                       "--json", "number,title,url,updatedAt,state,labels,assignees,milestone"])
        items = []
        for entry in json.loads(out or "[]"):
            labels = label_names(entry["labels"])
            if not is_farm_issue(labels):
                continue
            item = {
                "repo": self.repo,
                "number": entry["number"],
                "title": entry["title"],
                "url": entry["url"],
                "updated_at": entry["updatedAt"],
                "state": entry["state"].lower(),
                "labels": labels,
                "assignees": [a["login"] for a in entry["assignees"]],
                "milestone": (entry.get("milestone") or {}).get("title"),
                "body": "",
            }
            if "farm:working" in labels or "farm:merge-review" in labels:
                item["comments"] = self.comments(entry["number"])
            items.append(item)
        return items

    def pull_requests(self, state, limit=100):
        """PRs in a state ("open" or "merged") as [{"number", "url", "branch", "mergedAt"}].

        The coordinator matches the farm/<number>-* branches to issues. Only recent PRs are listed (`limit`).
        """
        out = self.gh(["pr", "list", "--repo", self.repo, "--state", state, "--limit", str(limit),
                       "--json", "number,url,headRefName,mergedAt"])
        return [{"number": pr["number"], "url": pr["url"], "branch": pr["headRefName"], "mergedAt": pr.get("mergedAt")}
                for pr in json.loads(out or "[]")]

    # ---------- writes ----------

    def set_labels(self, number, add=(), remove=()):
        """Adds and removes labels in one call. Removing a label the issue does not have is not an error."""
        args = ["issue", "edit", str(int(number)), "--repo", self.repo]
        if add:
            args += ["--add-label", ",".join(add)]
        if remove:
            args += ["--remove-label", ",".join(remove)]
        if add or remove:
            self.gh(args)

    def create_comment(self, number, body):
        return self.api("POST", "repos/{repo}/issues/%s/comments" % int(number), {"body": body})["id"]

    def update_comment(self, comment_id, body):
        self.api("PATCH", "repos/{repo}/issues/comments/%s" % int(comment_id), {"body": body})

    def create_issue(self, title, body="", labels=(), assignees=(), milestone=None):
        """Creates an issue and returns its number. `milestone` is a title: it must exist (see ensure_milestone)."""
        payload = {"title": title, "body": body, "labels": list(labels), "assignees": list(assignees)}
        if milestone:
            payload["milestone"] = self.milestone_number(milestone)
        return self.api("POST", "repos/{repo}/issues", payload)["number"]

    # ---------- labels and milestones (farm.py setup-repo) ----------

    def existing_labels(self):
        return {label["name"]: label for label in self.api("GET", "repos/{repo}/labels?per_page=100", paginate=True) or []}

    def ensure_label(self, name, color, description, existing):
        """Creates the label, or fixes its color and description. Returns "created", "updated" or "ok"."""
        current = existing.get(name)
        if current is None:
            self.api("POST", "repos/{repo}/labels", {"name": name, "color": color, "description": description})
            return "created"
        if current.get("color", "").lower() != color.lower() or (current.get("description") or "") != description:
            self.api("PATCH", "repos/{repo}/labels/%s" % _quote(name),
                     {"new_name": name, "color": color, "description": description})
            return "updated"
        return "ok"

    def milestones(self):
        return self.api("GET", "repos/{repo}/milestones?state=all&per_page=100", paginate=True) or []

    def milestone_number(self, title):
        for milestone in self.milestones():
            if milestone["title"] == title:
                return milestone["number"]
        raise GitHubError("No milestone {!r}. Create it with `farm.py setup-repo --milestone {!r}` or on GitHub.".format(
            title, title))

    def ensure_milestone(self, title, description=""):
        for milestone in self.milestones():
            if milestone["title"] == title:
                return "ok"
        self.api("POST", "repos/{repo}/milestones", {"title": title, "description": description})
        return "created"


def raw_item(raw):
    """A REST issue (api.github.com shape) as a raw item."""
    return {
        "number": raw["number"],
        "title": raw["title"],
        "url": raw["html_url"],
        "updated_at": raw["updated_at"],
        "state": raw["state"],
        "labels": label_names(raw.get("labels")),
        "assignees": [a["login"] for a in raw.get("assignees") or []],
        "milestone": (raw.get("milestone") or {}).get("title"),
        "body": raw.get("body") or "",
    }


def _quote(text):
    from urllib.parse import quote
    return quote(text, safe="")
