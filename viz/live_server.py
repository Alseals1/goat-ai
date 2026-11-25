#!/usr/bin/env python3
"""Local live server for the G.O.A.T. AI floor view. Adapted from the Software Farm floor.

Serves floor.html, /api/snapshot and /api/day (the shift report) on 127.0.0.1 only. It only reads:
  - `goat.py list --json` in each project repo (a read of the GitHub issues, every --board-every seconds)
  - ~/.goat-ai/coordinator/state.json (the coordinator's running agents)
  - ~/.goat-ai/runs.jsonl (one record per finished run, with its outcome)
  - the "repos" and "coordinator" keys of ~/.goat-ai/config.json
  - ~/.goat-ai/viz/usage.json (your plan usage, saved by usage_statusline.py when you use Claude Code)
  - `goat.py show <number> --json`, only for an issue that waits on a question or a chat, once per change,
    for the text of its newest comment (the question bubble)
It writes nothing and sends nothing anywhere but your own browser.

The page was written for the Software Farm. normalize_item() and the run_key() helpers turn G.O.A.T.'s shapes into
the Farm's, so the page and the drawing code stay close to the Farm's copy:
  - goatStatus "plan-review" -> farmStatus "plan review", "chat" -> "chat with agent"
  - status "staging" -> "staging review"; a closed issue with no goat: label -> farmStatus "done"
  - issue numbers repeat across repos, so a card's id is "owner/name#number"

Run:  python3 viz/live_server.py                 (repos from ~/.goat-ai/config.json)
      python3 viz/live_server.py --repo ~/code/my-project
Then open http://127.0.0.1:8765
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
GOAT_HOME = os.path.expanduser(os.environ.get("GOAT_VIZ_HOME", "~/.goat-ai"))
STATE_FILE = os.path.join(GOAT_HOME, "coordinator", "state.json")
RUNS_FILE = os.path.join(GOAT_HOME, "runs.jsonl")
CONFIG_FILE = os.path.join(GOAT_HOME, "config.json")
# Written by usage_statusline.py (a Claude Code status line): the plan's usage windows and their reset times.
USAGE_FILE = os.path.join(GOAT_HOME, "viz", "usage.json")
# This folder sits in the G.O.A.T. AI repo, so it runs the CLI next to it.
GOAT_CLI = os.path.join(os.path.dirname(HERE), "goat.py")
PAGE = os.path.join(HERE, "floor.html")
# The only other files the server sends: a fixed list, so no other path on disk can be read through it.
STATIC = {"/floor.js": "floor.js", "/floor3d.js": "floor3d.js", "/vendor/three.min.js": "vendor/three.min.js"}
RUN_TAIL = 300

lock = threading.Lock()
board = {"items": [], "readAt": None, "errors": {}}


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def safe_config():
    """Only the repos and the coordinator limits."""
    config = read_json(CONFIG_FILE, {})
    coordinator = config.get("coordinator") or {}
    return {
        "repos": config.get("repos") or {},
        "limits": {
            "maxAgents": coordinator.get("maxAgents", 3),
            "maxAgentsByRole": coordinator.get("maxAgentsByRole") or {},
        },
    }


# ---------- G.O.A.T. to Farm shapes ----------

ISSUE_URL = re.compile(r"github\.com/([^/]+/[^/]+)/issues/(\d+)")


def item_key(slug, number):
    """A card's id. Issue numbers repeat across repos, so the repo is part of it."""
    return "{}#{}".format(slug, number) if slug else str(number)


def farm_status(goat_status, status):
    """The Farm status the page expects for a goat: label (without the prefix) and a G.O.A.T. status."""
    goat_status = (goat_status or "").strip().lower()
    if goat_status == "chat":
        return "chat with agent"
    if goat_status:
        return goat_status.replace("-", " ")
    return "done" if status == "done" else ""


def normalize_item(item, repo_name, multi_repo):
    """One item of `goat.py list --json` in the Farm's shape, or None when the floor does not show it.

    repo_name is the repo's key in the config (or the folder name with --repo); `show` runs in that repo.
    multi_repo: card labels carry the repo name, so #34 of two repos can be told apart."""
    found = ISSUE_URL.search(item.get("url") or "")
    slug = found.group(1) if found else repo_name
    number = str(item.get("id"))
    status = (item.get("status") or "").lower()
    out = dict(item)
    out["number"] = number
    out["slug"] = slug
    out["repo"] = repo_name
    out["id"] = item_key(slug, number)
    out["status"] = "staging review" if status == "staging" else status
    out["farmStatus"] = farm_status(item.get("goatStatus"), status)
    out["taskId"] = ("{}#{}".format(slug.split("/")[-1], number) if multi_repo else "#" + number)
    if not out["farmStatus"] and out["status"] != "staging review":
        return None
    return out


def run_key(item, repo):
    """The card id of a coordinator run or a run record. The project manager's item is not an issue number."""
    item = str(item or "")
    return item_key(repo, item) if repo and item.isdigit() else item


def tail_runs():
    try:
        with open(RUNS_FILE) as f:
            lines = f.readlines()[-RUN_TAIL:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        entry = {k: record.get(k) for k in
                 ("item", "board", "role", "startedAt", "endedAt", "fromStage", "toStage", "outcome")}
        entry["item"] = run_key(record.get("item"), record.get("board"))
        tokens = record.get("tokens") if isinstance(record.get("tokens"), dict) else {}
        entry["outputTokens"] = tokens.get("output")
        entry["inputTokens"] = tokens.get("input")
        entry["cacheReadTokens"] = tokens.get("cacheRead")
        entry["cacheWriteTokens"] = tokens.get("cacheWrite")
        out.append(entry)
    return out


def local_date(stamp):
    """The local calendar date of an ISO time such as 2026-11-24T21:05:00Z, or None."""
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().date()
    except (AttributeError, ValueError):
        return None


def day_report(day_text):
    """Every run record that ended on one local day (all of runs.jsonl, not only the tail), with issue names."""
    try:
        day = datetime.strptime(day_text or "", "%Y-%m-%d").date()
    except ValueError:
        day = datetime.now().date()
    runs = []
    try:
        with open(RUNS_FILE) as f:
            for line in f:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                if local_date(record.get("endedAt") or record.get("startedAt")) != day:
                    continue
                tokens = record.get("tokens") if isinstance(record.get("tokens"), dict) else {}
                runs.append({
                    "item": run_key(record.get("item"), record.get("board")), "role": record.get("role"),
                    "startedAt": record.get("startedAt"), "endedAt": record.get("endedAt"),
                    "fromStage": record.get("fromStage"), "toStage": record.get("toStage"),
                    "outcome": record.get("outcome"), "model": record.get("model"), "effort": record.get("effort"),
                    "tokens": {k: tokens.get(k) for k in ("input", "output", "cacheRead", "cacheWrite")},
                })
    except OSError:
        pass
    with lock:
        items = {str(i["id"]): {"label": i.get("taskId") or str(i["id"]), "name": i.get("name"),
                                "farmStatus": i.get("farmStatus"), "status": i.get("status")} for i in board["items"]}
    return {"date": day.isoformat(), "runs": runs, "items": items, "planUsage": read_json(USAGE_FILE, None),
            "runsFileFound": os.path.exists(RUNS_FILE)}


QUESTION_STATES = ("answer question", "chat with agent")
# The statuses where a person must act (WAITS_FOR_PERSON in goat/board.py), as farm_status() spells them.
WAITS_FOR_PERSON = QUESTION_STATES + ("tier review", "plan review", "merge review", "stalled")
question_cache = {}


def clean_text(text):
    text = re.sub(r"[`*_>#]+", "", text or "")
    return " ".join(text.split())[:200]


def newest_comment(data):
    """The text of the newest comment in `goat.py show --json` output, or None."""
    comments = data.get("comments") or []
    return comments[-1].get("text") if comments else None


def read_questions(repos, items, fake_show):
    """The newest comment of each issue that waits on a question or a chat. One `goat.py show` per change."""
    out = {}
    for item in items:
        if (item.get("farmStatus") or "").lower() not in QUESTION_STATES:
            continue
        key = (item["id"], item.get("farmStatus"), item.get("updatedAt"))
        if key not in question_cache:
            text = None
            try:
                if fake_show:
                    text = read_json(fake_show, {}).get(item["id"])
                else:
                    path = repos.get(item.get("repo"))
                    result = subprocess.run([sys.executable, GOAT_CLI, "show", item["number"], "--json"],
                                            cwd=os.path.expanduser(path), capture_output=True, text=True, timeout=120)
                    if result.returncode == 0:
                        text = newest_comment(json.loads(result.stdout))
            except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
                text = None
            question_cache[key] = clean_text(text) if text else None
        if question_cache[key]:
            out[item["id"]] = question_cache[key]
    return out


def read_board_once(repos, fake_list, mine):
    items, errors = [], {}
    for name, path in repos.items():
        try:
            if fake_list:
                data = read_json(fake_list, {"items": []})
            else:
                cmd = [sys.executable, GOAT_CLI, "list", "--json"] + (["--mine"] if mine else [])
                result = subprocess.run(cmd, cwd=os.path.expanduser(path), capture_output=True, text=True,
                                        timeout=120)
                if result.returncode != 0:
                    errors[name] = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["exit {}".format(
                        result.returncode)]
                    continue
                data = json.loads(result.stdout)
            for item in data.get("items", []):
                shown = normalize_item(item, name, len(repos) > 1)
                if shown:
                    items.append(shown)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            errors[name] = [str(error)]
    return items, errors


def board_loop(repos, every, fake_list, fake_show, mine):
    while True:
        started = time.time()
        items, errors = read_board_once(repos, fake_list, mine)
        questions = read_questions(repos, items, fake_show)
        with lock:
            board["questions"] = questions
            if items or not errors:
                board["items"] = items
                # The time the read started: the board holds every change made before it.
                board["readAt"] = started
            board["errors"] = errors
        time.sleep(every)


def attention(items):
    """The ids of the open issues that wait for a person."""
    return [item["id"] for item in items
            if item.get("farmStatus") in WAITS_FOR_PERSON and item.get("status") != "done"]


def snapshot():
    state = read_json(STATE_FILE, {})
    with lock:
        items = list(board["items"])
        read_at = board["readAt"]
        errors = dict(board["errors"])
        questions = dict(board.get("questions") or {})
    runs = []
    for run in state.get("runs") or []:
        entry = {k: run.get(k) for k in ("item", "label", "role", "model", "effort", "pid", "startedAt", "worktree",
                                          "resumedSession", "log")}
        entry["item"] = run_key(run.get("item"), run.get("repo"))
        runs.append(entry)
    return {
        "now": time.time(),
        "boardReadAt": read_at,
        "boardErrors": errors,
        "items": items,
        "runs": runs,
        # The cards drawn as waiting for you. The Farm's coordinator keeps this list; G.O.A.T.'s does not.
        "attention": attention(items),
        # The Farm coordinator's dependency waits. G.O.A.T. has none, so the page's parking room stays empty.
        "dependencyWaits": state.get("dependencyWaits") or {},
        "finished": tail_runs(),
        "questions": questions,
        "limits": safe_config()["limits"],
        "planUsage": read_json(USAGE_FILE, None),
        "coordinatorStateFound": os.path.exists(STATE_FILE),
    }


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, kind):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/snapshot"):
            self.send(200, json.dumps(snapshot()).encode(), "application/json")
        elif self.path.startswith("/api/day"):
            query = parse_qs(urlparse(self.path).query)
            self.send(200, json.dumps(day_report((query.get("date") or [""])[0])).encode(), "application/json")
        elif self.path in ("/", "/index.html"):
            with open(PAGE, "rb") as f:
                self.send(200, f.read(), "text/html; charset=utf-8")
        elif self.path in STATIC:
            with open(os.path.join(HERE, STATIC[self.path]), "rb") as f:
                self.send(200, f.read(), "text/javascript; charset=utf-8")
        else:
            self.send(404, b"not found", "text/plain")

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="Live G.O.A.T. AI floor view (read only, local only)")
    parser.add_argument("--repo", action="append", help="a project repo checkout; repeat for more. "
                                                         "Default: the repos in ~/.goat-ai/config.json")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--board-every", type=int, default=30, help="seconds between GitHub reads (default 30)")
    parser.add_argument("--mine", action="store_true", help="only issues assigned to you")
    parser.add_argument("--fake-list", help=argparse.SUPPRESS)
    parser.add_argument("--fake-show", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.repo:
        repos = {os.path.basename(os.path.normpath(os.path.expanduser(p))): p for p in args.repo}
    else:
        repos = safe_config()["repos"]
    if not repos:
        sys.exit("No repos. Pass --repo <project checkout>, or add repos to ~/.goat-ai/config.json.")

    threading.Thread(target=board_loop, args=(repos, args.board_every, args.fake_list, args.fake_show, args.mine),
                     daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("G.O.A.T. floor (read only): http://127.0.0.1:{}".format(args.port))
    print("Repos: " + ", ".join(repos))
    print("GitHub read every {}s. Ctrl-C to stop.".format(args.board_every))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
