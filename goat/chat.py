"""`goat.py chat`: resume an agent's Claude Code session for a live discussion.

It reads the item's run record from runs.jsonl on this machine, checks that the coordinator does not
run an agent on the item now, then goes to the session's start folder and replaces this process with
`claude --resume`. The resumed session claims the item again before it writes to GitHub.
"""

import json
import os

from goat.actions import GoatError
from goat.config import COORDINATOR_STATE_FILE, RUNS_FILE


def read_runs(runs_file):
    """The run records in runs.jsonl, oldest first. Lines that do not parse are skipped."""
    runs = []
    try:
        with open(runs_file) as f:
            lines = f.readlines()
    except OSError:
        return runs
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            runs.append(record)
    return runs


def find_run(runs, item_id, session=None):
    """The latest run record for the item, or the one with this session id."""
    found = None
    for record in runs:
        if str(record.get("item")) != item_id:
            continue
        if session is None or record.get("sessionId") == session:
            found = record
    if found:
        return found
    if session:
        raise GoatError("No run record for item {} with session {} on this machine.".format(item_id, session))
    raise GoatError("No run record for item {} on this machine. The agent may have run on another machine.".format(
        item_id))


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except (OSError, TypeError):
        return False
    return True


def running_agent(state_file, item_id, is_alive):
    """The coordinator run on the item whose process is alive, or None. A missing or bad state file gives None."""
    try:
        with open(state_file) as f:
            runs = json.load(f).get("runs") or []
    except (OSError, ValueError, AttributeError):
        return None
    for run in runs:
        if isinstance(run, dict) and str(run.get("item")) == item_id and is_alive(run.get("pid")):
            return run
    return None


def resume_command(record):
    """The argv that resumes the run's session. Without a known model, Claude Code uses its default."""
    argv = ["claude", "--resume", record["sessionId"]]
    if record.get("model"):
        argv += ["--model", record["model"]]
    return argv


def chat(item_id, session=None, runs_file=RUNS_FILE, state_file=COORDINATOR_STATE_FILE, is_alive=pid_alive,
         execvp=os.execvp, chdir=os.chdir):
    """Resumes the item's agent session in this terminal. Raises GoatError, and runs nothing, when it cannot."""
    record = find_run(read_runs(runs_file), item_id, session)

    agent = running_agent(state_file, item_id, is_alive)
    if agent:
        raise GoatError("The coordinator runs an {} agent on item {} now (pid {}). Wait until it ends.".format(
            agent.get("role", "?"), item_id, agent.get("pid")))

    folder = record.get("cwd")
    if not folder:
        raise GoatError("The run record of session {} has no start folder (cwd). It was saved before goat.py "
                        "recorded it.".format(record["sessionId"]))
    if not os.path.isdir(folder):
        raise GoatError("The start folder of session {} does not exist: {}".format(record["sessionId"], folder))

    argv = resume_command(record)
    print("Resuming {} session {} in {}".format(record.get("role", "agent"), record["sessionId"], folder), flush=True)
    chdir(folder)
    execvp(argv[0], argv)
