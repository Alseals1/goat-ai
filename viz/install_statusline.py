#!/usr/bin/env python3
"""Adds usage_statusline.py as your Claude Code status line, in ~/.claude/settings.json.

  python3 viz/install_statusline.py           add it (keeps a status line you already have, with --then)
  python3 viz/install_statusline.py --remove  take it out again (puts your old status line back)

It backs up settings.json first (settings.json.bak-<time>) and changes only the "statusLine" key.
If settings.json is not valid JSON, it changes nothing.
"""

import json
import os
import shlex
import sys
import time

SETTINGS = os.path.expanduser(os.environ.get("CLAUDE_SETTINGS_FILE", "~/.claude/settings.json"))
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage_statusline.py")
OURS = "python3 " + shlex.quote(SCRIPT)
# The full path, so a usage status line of another tool (the Software Farm has one) is not taken for this one.
MARK = SCRIPT


def load():
    if not os.path.exists(SETTINGS):
        return {}
    with open(SETTINGS) as f:
        text = f.read()
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as error:
        sys.exit("{} is not valid JSON ({}). Nothing was changed.".format(SETTINGS, error))
    if not isinstance(data, dict):
        sys.exit("{} is not a JSON object. Nothing was changed.".format(SETTINGS))
    return data


def save(data):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    if os.path.exists(SETTINGS):
        backup = "{}.bak-{}".format(SETTINGS, time.strftime("%Y%m%d-%H%M%S"))
        with open(SETTINGS) as f, open(backup, "w") as b:
            b.write(f.read())
        print("Backup: " + backup)
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, SETTINGS)


def old_command(command):
    """The command that ours wraps with --then, or None."""
    parts = shlex.split(command)
    if "--then" in parts and parts.index("--then") + 1 < len(parts):
        return parts[parts.index("--then") + 1]
    return None


def add():
    data = load()
    line = data.get("statusLine")
    if isinstance(line, dict) and MARK in str(line.get("command", "")):
        print("The usage status line is already in {}. Nothing to do.".format(SETTINGS))
        return
    if isinstance(line, dict) and line.get("type") == "command" and line.get("command"):
        new = dict(line, command=OURS + " --then " + shlex.quote(line["command"]))
        print("Kept your status line: " + line["command"])
    elif line:
        sys.exit("Your statusLine is not a command ({}). Nothing was changed. Add it by hand, see viz/README.md."
                 .format(json.dumps(line)))
    else:
        new = {"type": "command", "command": OURS}
    data["statusLine"] = new
    save(data)
    print("Added to {}:\n  {}".format(SETTINGS, new["command"]))
    print("Next: send one message in Claude Code, then look at the live view.")


def remove():
    data = load()
    line = data.get("statusLine")
    if not (isinstance(line, dict) and MARK in str(line.get("command", ""))):
        print("The usage status line is not in {}. Nothing to do.".format(SETTINGS))
        return
    old = old_command(line["command"])
    if old:
        data["statusLine"] = dict(line, command=old)
        print("Put back your status line: " + old)
    else:
        del data["statusLine"]
        print("Removed the status line.")
    save(data)


if __name__ == "__main__":
    remove() if "--remove" in sys.argv[1:] else add()
