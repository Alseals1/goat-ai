"""Pausing the coordinator: a switch the owner flips from Discord or a terminal, so no new agent starts.

The switch is a file, ~/.goat-ai/coordinator/paused.json. `goat.py pause`, `goat.py resume` and the Discord commands
`pause`, `stop` and `resume` write or remove it; the coordinator reads it before it starts anything. Being a file, it
works across processes (a terminal pause reaches a running coordinator) and outlasts a restart: a coordinator started
while the file is there stays paused until someone resumes it.

While paused the coordinator starts no agent (stage, retro, QA, project manager or aggregate), approves no gate and
merges nothing. It still notes agents that end and records what autopilot QA and project manager runs did. Agents
already running finish their step, unless `stop` (or `pause --stop`) interrupts them.

A file that cannot be read counts as paused: the switch is there to stop spending, so when in doubt it stays off.
"""

import json
import os
from datetime import datetime, timezone

from goat.config import LOCAL_DIR

PAUSE_FILE = os.path.join(LOCAL_DIR, "coordinator", "paused.json")
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
UNKNOWN = "unknown"


def read(path=None):
    """{"since": ISO time, "by": who paused it} while paused, else None. An unreadable file counts as paused.

    path defaults to PAUSE_FILE, read at call time so the tests can point it elsewhere."""
    path = path or PAUSE_FILE
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"since": "", "by": UNKNOWN}
    if not isinstance(data, dict):
        return {"since": "", "by": UNKNOWN}
    return {"since": str(data.get("since") or ""), "by": str(data.get("by") or UNKNOWN)}


def pause(by, now=None, path=None):
    """Turns the pause on. Returns (pause info, True when it was already on). An existing pause keeps its time."""
    path = path or PAUSE_FILE
    found = read(path)
    if found is not None:
        return found, True
    moment = now or datetime.now(timezone.utc)
    data = {"since": moment.astimezone(timezone.utc).strftime(TIME_FORMAT), "by": by}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(temporary, path)
    return data, False


def resume(path=None):
    """Turns the pause off. Returns True when it was on."""
    path = path or PAUSE_FILE
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    return True


def since_text(info):
    """ "since 14:03 UTC on 2026-11-23, from Discord", or less when the file did not say."""
    parts = []
    try:
        moment = datetime.strptime(info.get("since", ""), TIME_FORMAT)
        parts.append("since {} UTC on {}".format(moment.strftime("%H:%M"), moment.strftime("%Y-%m-%d")))
    except ValueError:
        pass
    if info.get("by") and info["by"] != UNKNOWN:
        parts.append("from " + info["by"])
    return ", ".join(parts)


def status_line(info):
    """One line for `goat.py status` and the Discord status reply, or "" when not paused."""
    if info is None:
        return ""
    when = since_text(info)
    return "Paused{}: no new agent starts. Resume with `resume` in Discord or `goat.py resume`.".format(
        " (" + when + ")" if when else "")
