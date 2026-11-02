#!/usr/bin/env python3
"""Token and model use of one agent run, read from the Claude Code session transcript.

Agents run this at release (protocol section 12) and copy its output into the run record.
It reads the main transcript and its subagent transcripts, keeps the assistant messages
inside the time window, and sums their usage per model. From the main transcript only, it
also reads the session's start folder (`cwd`) and its model, so `goat.py chat` can resume it.

The transcript format is a Claude Code internal format and is not documented, so this
script fails soft: on any problem it prints null usage with a note and exits 0.
It prints only numbers and model names, never message text.

Usage:
  python3 run-usage.py --session "$CLAUDE_CODE_SESSION_ID" --since 2026-10-27T15:00:00Z
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

# Transcript usage field -> run record field.
USAGE_FIELDS = {
    "input_tokens": "input",
    "output_tokens": "output",
    "cache_read_input_tokens": "cacheRead",
    "cache_creation_input_tokens": "cacheWrite",
}


def parse_time(text):
    """ISO time ("...Z" or with an offset) to an aware datetime. A time with no offset is UTC."""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def null_result(note):
    return {"tokens": None, "models": None, "usageNote": note, "cwd": None, "model": None}


def empty_usage():
    return {field: 0 for field in USAGE_FIELDS.values()}


def find_transcripts(projects_dir, session_id):
    """The main transcript and its subagent transcripts, or None when the session is not found."""
    matches = glob.glob(os.path.join(projects_dir, "*", session_id + ".jsonl"))
    if len(matches) != 1:
        return None
    main_file = matches[0]
    subagent_dir = os.path.join(os.path.dirname(main_file), session_id, "subagents")
    subagent_files = sorted(glob.glob(os.path.join(subagent_dir, "agent-*.jsonl")))
    return [main_file] + subagent_files


def read_messages(path):
    """Yields (timestamp, message) for each assistant line. Lines that do not parse are skipped."""
    with open(path, encoding="utf-8") as lines:
        for line in lines:
            try:
                entry = json.loads(line)
                if entry.get("type") != "assistant":
                    continue
                yield parse_time(entry["timestamp"]), entry["message"]
            except (ValueError, KeyError, TypeError, AttributeError):
                continue


def in_window(timestamp, since, until):
    return since <= timestamp <= until


def add_usage(totals, usage):
    for source, field in USAGE_FIELDS.items():
        value = usage.get(source) or 0
        if isinstance(value, int):
            totals[field] += value


def add_usage_totals(totals, model_usage):
    for field in USAGE_FIELDS.values():
        totals[field] += model_usage[field]


def start_folder(main_file):
    """The `cwd` of the first transcript line that has one: the folder where the session started."""
    with open(main_file, encoding="utf-8") as lines:
        for line in lines:
            try:
                cwd = json.loads(line).get("cwd")
            except (ValueError, AttributeError):
                continue
            if cwd:
                return cwd
    return None


def main_model(main_file, since, until):
    """The model of the last assistant message in the window. Local messages ("<synthetic>") do not count."""
    model = None
    for timestamp, message in read_messages(main_file):
        name = message.get("model") or ""
        if in_window(timestamp, since, until) and name and not name.startswith("<"):
            model = name
    return model


def collect(files, since, until):
    """Sums usage per model. One message is written as several lines with the same id: count it once."""
    seen_ids = set()
    per_model = {}
    for path in files:
        for timestamp, message in read_messages(path):
            if not in_window(timestamp, since, until):
                continue
            message_id = message.get("id")
            model = message.get("model") or "unknown"
            usage = message.get("usage")
            if not message_id or message_id in seen_ids or not isinstance(usage, dict):
                continue
            # Local messages such as "<synthetic>" have no API use.
            if model.startswith("<"):
                continue
            seen_ids.add(message_id)
            if model not in per_model:
                per_model[model] = empty_usage()
            add_usage(per_model[model], usage)

    tokens = empty_usage()
    models = []
    for model in sorted(per_model):
        add_usage_totals(tokens, per_model[model])
        models.append(dict({"model": model}, **per_model[model]))
    return {"tokens": tokens, "models": models, "usageNote": None}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Token and model use of one agent run.")
    parser.add_argument("--session", required=True, help="Claude Code session id")
    parser.add_argument("--since", required=True, help="run start, ISO time (claim.startedAt)")
    parser.add_argument("--until", help="run end, ISO time (default: now)")
    parser.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"))
    args = parser.parse_args(argv)

    try:
        since = parse_time(args.since)
        until = parse_time(args.until) if args.until else datetime.now(timezone.utc)
        files = find_transcripts(args.projects_dir, args.session)
        if files is None:
            result = null_result("transcript not found")
        else:
            result = collect(files, since, until)
            result["cwd"] = start_folder(files[0])
            result["model"] = main_model(files[0], since, until)
    except Exception as error:  # Fail soft: the release must still happen.
        result = null_result("could not read transcript: " + type(error).__name__)

    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
