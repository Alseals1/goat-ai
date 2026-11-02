"""The local facts of one item for its retro: run records, coordinator logs, and a digest of each session.

`goat.py retro-facts <item>` prints them. The retro agent reads this, not the raw transcripts: they are large,
and they are outside the agent's folder.

Only the item's own sessions are read. Their ids come from the run records (runs.jsonl) and the coordinator
logs (a run that did not release has a log but no run record).

The transcript format is a Claude Code internal format and is not documented, so the digest fails soft, like
skills/goat-board/run-usage.py: lines that do not parse are skipped, and a missing file gives a note.
"""

import glob
import json
import os

from goat.config import COORDINATOR_STATE_FILE, RUNS_FILE

# The coordinator writes one log per agent run here (goat/coordinator.py LOG_DIR).
LOG_DIR = os.path.join(os.path.dirname(COORDINATOR_STATE_FILE), "logs")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

TOOL_ERROR_CHARS = 200
PERSON_MESSAGE_CHARS = 500
# Keep the digest short: the retro needs the shape of a session, not all of it.
MAX_TOOL_ERRORS = 20
MAX_PERSON_MESSAGES = 20

# Coordinator log fields that the retro uses. The log is the `claude -p --output-format json` result.
LOG_FIELDS = ("session_id", "is_error", "subtype", "num_turns", "duration_ms", "total_cost_usd")


def cut(text, limit):
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[:limit - 3] + "..."


def run_records(item_id, runs_file=RUNS_FILE):
    """The item's run records, oldest first. Lines that do not parse are skipped."""
    records = []
    if not os.path.isfile(runs_file):
        return records
    with open(runs_file) as f:
        for line in f:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and str(record.get("item")) == str(item_id):
                records.append(record)
    return records


def coordinator_logs(item_id, log_dir=LOG_DIR):
    """The item's coordinator logs, oldest first. The file names are <stamp>-<item id>-<role>.json.

    Each entry has the file name, the role, the LOG_FIELDS, and the permission denials (count and tool names).
    A log that is not one JSON object (the agent is still running, or it crashed) gets a note instead.
    """
    logs = []
    for path in sorted(glob.glob(os.path.join(log_dir, "*-{}-*.json".format(item_id)))):
        name = os.path.basename(path)
        role = name[:-len(".json")].split("-{}-".format(item_id), 1)[-1]
        entry = {"file": name, "role": role}
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            entry["note"] = "log is not a finished JSON result"
            logs.append(entry)
            continue
        if not isinstance(data, dict):
            entry["note"] = "log is not a JSON object"
            logs.append(entry)
            continue
        for field in LOG_FIELDS:
            entry[field] = data.get(field)
        denials = data.get("permission_denials") or []
        entry["permissionDenials"] = len(denials)
        entry["deniedTools"] = sorted({denial.get("tool_name", "?") for denial in denials if isinstance(denial, dict)})
        logs.append(entry)
    return logs


def find_transcripts(session_id, projects_dir=PROJECTS_DIR):
    """The main transcript and its subagent transcripts, or [] when the session is not found."""
    matches = glob.glob(os.path.join(projects_dir, "*", session_id + ".jsonl"))
    if len(matches) != 1:
        return []
    subagent_dir = os.path.join(os.path.dirname(matches[0]), session_id, "subagents")
    return matches + sorted(glob.glob(os.path.join(subagent_dir, "agent-*.jsonl")))


def result_text(content):
    """The text of a tool result: a string, or a list of text blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(block.get("text", "") for block in content if isinstance(block, dict))
    return ""


def person_text(entry):
    """The person's words in a user line, or None for tool results and lines the CLI added (isMeta)."""
    if entry.get("isMeta"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
        if texts and len(texts) == len(content):
            return " ".join(texts)
    return None


def read_entries(path):
    """Yields each transcript line that parses as a JSON object. Counts the lines that do not."""
    skipped = 0
    with open(path, encoding="utf-8") as lines:
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if isinstance(entry, dict):
                yield entry
    if skipped:
        yield {"type": "goat-skipped", "count": skipped}


def digest_file(path, digest):
    """Adds one transcript file's tool calls, tool errors and person messages to the digest."""
    is_main = digest["files"] == 0
    digest["files"] += 1
    for entry in read_entries(path):
        kind = entry.get("type")
        if kind == "goat-skipped":
            digest["skippedLines"] += entry["count"]
            continue
        content = (entry.get("message") or {}).get("content")
        if kind == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    digest["toolCalls"][name] = digest["toolCalls"].get(name, 0) + 1
        elif kind == "user":
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                        digest["toolErrorCount"] += 1
                        if len(digest["toolErrors"]) < MAX_TOOL_ERRORS:
                            digest["toolErrors"].append(cut(result_text(block.get("content")), TOOL_ERROR_CHARS))
            # Subagent prompts are written by the main agent, not a person.
            text = person_text(entry) if is_main else None
            if text and len(digest["personMessages"]) < MAX_PERSON_MESSAGES:
                digest["personMessages"].append(cut(text, PERSON_MESSAGE_CHARS))


def transcript_digest(session_id, projects_dir=PROJECTS_DIR):
    """A short digest of one session (main and subagent transcripts). Never raises: problems become a note."""
    digest = {
        "sessionId": session_id,
        "files": 0,
        "toolCalls": {},
        "toolErrorCount": 0,
        "toolErrors": [],
        "personMessages": [],
        "skippedLines": 0,
        "note": None,
    }
    try:
        paths = find_transcripts(session_id, projects_dir)
        if not paths:
            digest["note"] = "transcript not found on this machine"
            return digest
        for path in paths:
            digest_file(path, digest)
    except Exception as error:  # Fail soft: the retro still works from GitHub and the run records.
        digest["note"] = "could not read transcript: " + type(error).__name__
    return digest


def session_ids(runs, logs):
    """The item's session ids, in first-seen order: run records first, then logs."""
    ids = []
    for source in list(runs) + list(logs):
        session = source.get("sessionId") or source.get("session_id")
        if session and session not in ids:
            ids.append(session)
    return ids


def collect(item_id, runs_file=RUNS_FILE, log_dir=LOG_DIR, projects_dir=PROJECTS_DIR):
    """All local facts of one item: {"item", "runs", "logs", "sessions"}."""
    runs = run_records(item_id, runs_file)
    logs = coordinator_logs(item_id, log_dir)
    sessions = [transcript_digest(session, projects_dir) for session in session_ids(runs, logs)]
    return {"item": str(item_id), "runs": runs, "logs": logs, "sessions": sessions}


# ---------- Markdown ----------

def tokens_text(tokens):
    if not isinstance(tokens, dict):
        return "unknown"
    return "{} in, {} out, {} cache read, {} cache write".format(
        tokens.get("input"), tokens.get("output"), tokens.get("cacheRead"), tokens.get("cacheWrite"))


def run_lines(run):
    lines = ["- **{}** {} → {} ({}), {} to {}".format(
        run.get("role"), run.get("fromStage"), run.get("toStage"), run.get("outcome"),
        run.get("startedAt"), run.get("endedAt"))]
    lines.append("  - Session: {}; model: {}; tokens: {}".format(
        run.get("sessionId"), run.get("model"), tokens_text(run.get("tokens"))))
    if run.get("usageNote"):
        lines.append("  - Usage note: {}".format(run["usageNote"]))
    return lines


def log_lines(entry):
    if entry.get("note"):
        return ["- **{}** {}: {}".format(entry["role"], entry["file"], entry["note"])]
    lines = ["- **{}** {}: {}, error {}, {} turns, {} s, ${}".format(
        entry["role"], entry["file"], entry.get("subtype"), entry.get("is_error"), entry.get("num_turns"),
        round((entry.get("duration_ms") or 0) / 1000), round(entry.get("total_cost_usd") or 0, 2))]
    lines.append("  - Session: {}".format(entry.get("session_id")))
    if entry.get("permissionDenials"):
        lines.append("  - Permission denials: {} ({})".format(
            entry["permissionDenials"], ", ".join(entry["deniedTools"])))
    return lines


def session_lines(digest):
    lines = ["### Session {}".format(digest["sessionId"]), ""]
    if digest["note"]:
        lines += ["Note: {}".format(digest["note"]), ""]
        if not digest["files"]:
            return lines
    calls = sorted(digest["toolCalls"].items(), key=lambda pair: (-pair[1], pair[0]))
    lines.append("- Transcript files: {} (main and subagents)".format(digest["files"]))
    lines.append("- Tool calls: {}".format(", ".join("{} {}".format(name, count) for name, count in calls) or "None"))
    lines.append("- Tool errors: {}".format(digest["toolErrorCount"]))
    for error in digest["toolErrors"]:
        lines.append("  - {}".format(error))
    if digest["skippedLines"]:
        lines.append("- Lines that did not parse: {}".format(digest["skippedLines"]))
    lines.append("- Person messages: {}".format(len(digest["personMessages"])))
    for message in digest["personMessages"]:
        lines.append("  - {}".format(message))
    lines.append("")
    return lines


def facts_markdown(facts, label):
    """The facts as Markdown, for `goat.py retro-facts`. label names the item, for example "#14 Fix login"."""
    lines = ["# Retro facts: {}".format(label), "", "## Run records (runs.jsonl)", ""]
    if not facts["runs"]:
        lines.append("None on this machine.")
    for run in facts["runs"]:
        lines += run_lines(run)
    lines += ["", "## Coordinator logs", ""]
    if not facts["logs"]:
        lines.append("None on this machine (the item may have been run by hand).")
    for entry in facts["logs"]:
        lines += log_lines(entry)
    lines += ["", "## Sessions", ""]
    if not facts["sessions"]:
        lines += ["None found.", ""]
    for digest in facts["sessions"]:
        lines += session_lines(digest)
    return "\n".join(lines).rstrip() + "\n"
