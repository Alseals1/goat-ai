"""Is the coordinator running? The coordinator writes a heartbeat file while it runs; `goat.py health` reads it.

The coordinator updates ~/.goat-ai/coordinator/heartbeat.json every few seconds while it waits, at the start and
end of every turn, and once more when it stops on purpose. From that file and the process id, `assess` tells apart:

- running: the process is alive and reported recently.
- stale:   the process is alive but has not reported for a while (the laptop was asleep, or it is stuck).
- died:    it did not stop on purpose and the process is gone (terminal closed, crash, reboot).
- stopped: it was stopped on purpose (Ctrl-C or SIGTERM).
- never:   no heartbeat file: it was never started.
"""

import json
import os
from datetime import datetime, timezone

from goat.config import LOCAL_DIR

HEARTBEAT_FILE = os.path.join(LOCAL_DIR, "coordinator", "heartbeat.json")
# A turn can take a while (git fetches), but the wait between turns reports every 15 seconds.
STALE_SECONDS = 300
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

RUNNING, STALE, DIED, STOPPED, NEVER = "running", "stale", "died", "stopped", "never"
# States where a person should know: the coordinator is not doing its job and did not say it would stop.
ALERT_STATES = (STALE, DIED)


def utc_now():
    return datetime.now(timezone.utc)


def stamp(moment):
    return moment.astimezone(timezone.utc).strftime(TIME_FORMAT)


def parse_stamp(text):
    try:
        return datetime.strptime(text, TIME_FORMAT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def read(path=HEARTBEAT_FILE):
    """The heartbeat dict, or None when there is no readable file."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write(path, data):
    """Writes the file in one step (a temporary file, then a rename), so a reader never sees half of it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(temporary, path)


class Beat:
    """The coordinator's side: keeps the heartbeat file up to date. Every method swallows file errors, because a
    full disk must never stop the coordinator."""

    def __init__(self, pid, interval_seconds, path=HEARTBEAT_FILE, now=utc_now):
        self.path = path
        self.now = now
        self.data = {
            "pid": pid,
            "status": "running",
            "startedAt": stamp(now()),
            "lastSeenAt": stamp(now()),
            "intervalSeconds": interval_seconds,
            "lastTurnAt": None,
            "nextTurnAt": None,
            "githubFailures": 0,
        }
        self.save()

    def save(self):
        try:
            write(self.path, self.data)
        except OSError:
            pass

    def alive(self, **fields):
        """Marks "still here". Extra fields are saved too."""
        self.data.update(fields)
        self.data["lastSeenAt"] = stamp(self.now())
        self.save()

    def turn_started(self):
        self.alive(turnStartedAt=stamp(self.now()))

    def turn_done(self, github_failed):
        """Records a finished turn. githubFailures counts turns in a row that could not read GitHub."""
        failures = self.data["githubFailures"] + 1 if github_failed else 0
        self.alive(lastTurnAt=stamp(self.now()), githubFailures=failures)

    def scheduled(self, seconds):
        """Records when the next scheduled turn is due."""
        due = datetime.fromtimestamp(self.now().timestamp() + seconds, timezone.utc)
        self.alive(nextTurnAt=stamp(due))

    def stopped(self):
        self.data["status"] = "stopped"
        self.data["stoppedAt"] = stamp(self.now())
        self.alive()


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # it exists, but belongs to someone else
    except OSError:
        return False
    return True


def age_text(seconds):
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "{} seconds".format(seconds)
    if seconds < 90 * 60:
        return "{} minutes".format(round(seconds / 60))
    if seconds < 36 * 3600:
        return "{} hours".format(round(seconds / 3600))
    return "{} days".format(round(seconds / 86400))


def assess(data, now, alive=process_alive):
    """Returns (state, text) for a heartbeat dict (or None) at the time `now`."""
    if not data:
        return NEVER, "The coordinator was never started on this machine (no heartbeat). Start it with: goat.py run"
    seen = parse_stamp(data.get("lastSeenAt"))
    ago = (now - seen).total_seconds() if seen else None
    ago_text = age_text(ago) if ago is not None else "an unknown time"
    if data.get("status") == "stopped":
        return STOPPED, "The coordinator was stopped on purpose {} ago. Start it with: goat.py run".format(ago_text)
    pid = data.get("pid")
    process_exists = alive(pid)
    if process_exists and ago is not None and ago <= STALE_SECONDS:
        return RUNNING, "The coordinator is running (pid {}). {}".format(pid, turn_text(data, now))
    if process_exists:
        return STALE, ("The coordinator process (pid {}) exists but has not reported for {}. The laptop was probably "
                       "asleep, and it carries on when it wakes. If it stays like this, stop it and run goat.py run again."
                       ).format(pid, ago_text)
    return DIED, ("The coordinator is NOT running: it last reported {} ago and did not stop on purpose (terminal closed, "
                  "a crash or a restart). Tasks are paused. Start it again with: goat.py run").format(ago_text)


def turn_text(data, now):
    """One sentence about the last and next turn, for the running state."""
    parts = []
    last = parse_stamp(data.get("lastTurnAt"))
    if last:
        parts.append("Last check {} ago".format(age_text((now - last).total_seconds())))
    else:
        parts.append("No check has finished yet")
    following = parse_stamp(data.get("nextTurnAt"))
    if following and following > now:
        parts.append("next in {}".format(age_text((following - now).total_seconds())))
    failures = data.get("githubFailures") or 0
    if failures:
        parts.append("GitHub could not be read in the last {} check(s): see the coordinator terminal".format(failures))
    return ", ".join(parts) + "."
