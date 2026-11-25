#!/usr/bin/env python3
"""Claude Code status line that saves your plan usage for the G.O.A.T. floor view. Adapted from the Software Farm.

Claude Code runs this script on your computer and sends it the session's data as JSON on stdin. It uses no API
tokens. The script:
  - saves the `rate_limits` part (5-hour and 7-day limits, or a gateway spend limit: percent used and reset time)
    to ~/.goat-ai/viz/usage.json, which live_server.py reads,
  - prints a short usage line at the bottom of Claude Code.
Claude Code only sends rate_limits for claude.ai Pro and Max plans, or behind a gateway that sets a spend limit,
and only after the session's first API response. When they are missing, the file says so.

Settings (~/.claude/settings.json):
  "statusLine": {"type": "command", "command": "python3 /path/to/viz/usage_statusline.py"}
To keep a status line you already have, pass its command after --then; its line is printed first:
  "command": "python3 /path/to/viz/usage_statusline.py --then 'your old command'"
"""

import json
import os
import subprocess
import sys
import time

OUT_DIR = os.path.expanduser(os.environ.get("GOAT_VIZ_HOME", "~/.goat-ai"))
OUT_FILE = os.path.join(OUT_DIR, "viz", "usage.json")
WINDOWS = ("five_hour", "seven_day", "spend_limit")
FIELDS = ("used_percentage", "resets_at", "used_usd", "limit_usd", "period")


def read_saved():
    try:
        with open(OUT_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(data):
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    tmp = OUT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, OUT_FILE)


def windows_from(payload):
    limits = payload.get("rate_limits")
    if not isinstance(limits, dict):
        return {}
    out = {}
    for name in WINDOWS:
        window = limits.get(name)
        if isinstance(window, dict):
            out[name] = {k: window.get(k) for k in FIELDS if window.get(k) is not None}
    return out


def update(payload):
    """Saves what this session sent, and returns the saved data."""
    now = time.time()
    saved = read_saved()
    windows = windows_from(payload)
    had_response = bool((payload.get("cost") or {}).get("total_api_duration_ms"))
    kept = {}
    for name, window in (saved.get("windows") or {}).items():
        # A window from an earlier session stays until its reset time passes.
        if name not in windows and (window.get("resets_at") or 0) > now:
            kept[name] = window
    kept.update(windows)
    data = {
        "checkedAt": now,
        "windows": kept,
        "windowsSavedAt": now if windows else saved.get("windowsSavedAt"),
        # True when this session had an API response but Claude Code sent no rate_limits: the account gets none.
        "noLimitsAfterResponse": bool(had_response and not windows),
        "claudeCodeVersion": payload.get("version"),
    }
    save(data)
    return data


def until(ts):
    seconds = max(0, int(ts - time.time()))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return "{}d {}h".format(days, hours)
    if hours:
        return "{}h {}m".format(hours, minutes)
    return "{}m".format(minutes)


def line(data):
    parts = []
    labels = {"five_hour": "5h", "seven_day": "7d", "spend_limit": "spend"}
    for name in WINDOWS:
        window = (data.get("windows") or {}).get(name)
        if not window or window.get("used_percentage") is None:
            continue
        text = "{} {}%".format(labels[name], round(window["used_percentage"]))
        if window.get("resets_at"):
            text += " (resets in {})".format(until(window["resets_at"]))
        parts.append(text)
    if parts:
        return "Usage: " + " · ".join(parts)
    if data.get("noLimitsAfterResponse"):
        return "Usage: no plan limits sent for this account"
    return ""


def main():
    raw = sys.stdin.read()
    args = sys.argv[1:]
    then = args[args.index("--then") + 1] if "--then" in args and args.index("--then") + 1 < len(args) else None
    printed = []
    if then:
        try:
            result = subprocess.run(then, shell=True, input=raw, capture_output=True, text=True, timeout=10)
            if result.stdout.strip():
                printed.append(result.stdout.rstrip("\n"))
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        text = line(update(json.loads(raw or "{}")))
        if text:
            printed.append(text)
    except Exception:  # A status line must never break Claude Code.
        pass
    if printed:
        print("\n".join(printed))


if __name__ == "__main__":
    main()
