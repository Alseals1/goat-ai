"""Desktop notifications, best effort. A missing tool or a failure returns False and never raises.

macOS uses `osascript`, Linux uses `notify-send`. The title and message go in as arguments, never into a script string,
so text from an issue title cannot run as code.
"""

import subprocess
import sys

TIMEOUT_SECONDS = 10
MAX_LENGTH = 200


def clean(text):
    """One line, no control characters, not too long."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(text)).strip()
    return text if len(text) <= MAX_LENGTH else text[:MAX_LENGTH - 1] + "…"


def command(title, message, platform=sys.platform):
    """The command that shows the notification on this platform, or None."""
    title, message = clean(title), clean(message)
    if platform == "darwin":
        return ["osascript",
                "-e", "on run argv",
                "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e", "end run",
                title, message]
    if platform.startswith("linux"):
        return ["notify-send", title, message]
    return None


def notify(title, message, run=subprocess.run, platform=sys.platform):
    """Shows a desktop notification. Returns True when the command ran without an error."""
    argv = command(title, message, platform)
    if argv is None:
        return False
    try:
        result = run(argv, capture_output=True, timeout=TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
