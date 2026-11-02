"""Retro notes and aggregate reports, as local files.

- Notes: one Markdown file per Done item, written by the farm-retro agent, in
  ~/.software-farm/retros/tasks/<owner>_<repo>-<issue number>.md. The coordinator (farm/coordinator.py) knows a retro
  is done when its note exists, so the Done cleanup waits for it.
- Aggregate reports: written by the farm-retro-aggregate agent, in ~/.software-farm/retros/aggregates/.
- state.json: the note ids that an aggregate has read (aggregatedNotes), and the time of the last aggregate
  (lastAggregateAt).

They stay on this machine: they can quote session text, so they are never committed or posted to GitHub.
Every function takes the retros folder as an argument, so tests can use a temp folder.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

from farm import board as fb
from farm.actions import FarmError, ItemNotAvailable, iso
from farm.config import RETROS_DIR

# The note header: "- Key: value" lines after the title. pending_notes() reads them back.
HEADER_LINE = re.compile(r"^- ([A-Za-z ]+): (.*)$")


def tasks_dir(retros_dir=RETROS_DIR):
    return os.path.join(retros_dir, "tasks")


def note_id(board_id, item_id):
    """The note's id and file name stem, for example "me_app-14" for issue 14 of me/app."""
    return "{}-{}".format(board_id.replace("/", "_"), item_id)


def note_path(board_id, item_id, retros_dir=RETROS_DIR):
    return os.path.join(tasks_dir(retros_dir), note_id(board_id, item_id) + ".md")


def has_note(board_id, item_id, retros_dir=RETROS_DIR):
    return os.path.isfile(note_path(board_id, item_id, retros_dir))


def note_header(item, now):
    """The lines before the agent's Markdown: a title and the item's ids, for people and for pending_notes()."""
    return "\n".join([
        "# Retro: #{} {}".format(item.id, item.name),
        "",
        "- Item: {}".format(item.id),
        "- Repo: {}".format(item.board_id),
        "- Saved: {}".format(iso(now)),
        "",
        "",
    ])


def save_note(item, markdown, now, retros_dir=RETROS_DIR):
    """Writes the item's note (header, then the Markdown) and returns its path. A second save replaces it."""
    path = note_path(item.board_id, item.id, retros_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(note_header(item, now) + markdown.strip() + "\n")
    return path


def read_header(path):
    """The header fields of a note as a dict, for example {"Item": "1", "Repo": "me/app", ...}."""
    fields = {}
    with open(path) as f:
        for line in f:
            if line.startswith("## "):
                break
            match = HEADER_LINE.match(line.rstrip("\n"))
            if match:
                fields[match.group(1)] = match.group(2)
    return fields


def check_retro_item(item, user_id, done_label):
    """Raises ItemNotAvailable unless the user owns the issue and it is closed (stage done).

    retro-save runs it, so a note is only ever saved for a finished item of this developer.
    """
    if not item.is_owned_by(user_id):
        raise ItemNotAvailable("#{} is not assigned to you. A retro note is only saved for your own issues.".format(
            item.id))
    if not fb.same_label(item.status, done_label):
        raise ItemNotAvailable("#{} has stage {!r}, not {!r}. A retro is only for closed issues.".format(
            item.id, item.status, done_label))


# ---------- Aggregate ----------

def aggregates_dir(retros_dir=RETROS_DIR):
    return os.path.join(retros_dir, "aggregates")


def state_path(retros_dir=RETROS_DIR):
    return os.path.join(retros_dir, "state.json")


def read_state(retros_dir=RETROS_DIR):
    """{"aggregatedNotes": [note ids], "lastAggregateAt": ISO time or None}. A missing file is the empty state.

    Raises ValueError when the file is not a JSON object (json.JSONDecodeError is a ValueError too).
    """
    state = {"aggregatedNotes": [], "lastAggregateAt": None}
    try:
        with open(state_path(retros_dir)) as f:
            saved = json.load(f)
    except FileNotFoundError:
        return state
    if not isinstance(saved, dict):
        raise ValueError("{} is not a JSON object".format(state_path(retros_dir)))
    state.update(saved)
    return state


def last_aggregate_at(retros_dir=RETROS_DIR):
    """The time of the last aggregate as an aware datetime, or None before the first one."""
    text = read_state(retros_dir)["lastAggregateAt"]
    if not text:
        return None
    if not isinstance(text, str):
        raise ValueError("lastAggregateAt is not a time: {}".format(json.dumps(text)))
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def pending_notes(retros_dir=RETROS_DIR):
    """The notes that no aggregate has read, oldest saved first: a list of {"id", "path", "item", "issue",
    "savedAt"}."""
    folder = tasks_dir(retros_dir)
    if not os.path.isdir(folder):
        return []
    read_ids = set(read_state(retros_dir)["aggregatedNotes"])
    notes = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".md") or name[:-len(".md")] in read_ids:
            continue
        path = os.path.join(folder, name)
        header = read_header(path)
        notes.append({
            "id": name[:-len(".md")],
            "path": path,
            "item": header.get("Item"),
            "issue": header.get("Item"),
            "savedAt": header.get("Saved"),
        })
    notes.sort(key=lambda note: (note["savedAt"] or "", note["id"]))
    return notes


def aggregate_due(pending_count, last_at, now, min_retros, min_days):
    """True when an aggregate retro should run: at least min_retros notes wait, and at least min_days days
    passed since the last aggregate. Before the first aggregate (last_at is None), only the note count counts."""
    if pending_count < min_retros:
        return False
    if last_at is None:
        return True
    return now - last_at >= timedelta(days=min_days)


def aggregate_reports(retros_dir=RETROS_DIR):
    """The aggregate report paths, newest first. The file names start with the time, so they sort by it."""
    folder = aggregates_dir(retros_dir)
    if not os.path.isdir(folder):
        return []
    names = sorted((name for name in os.listdir(folder) if name.endswith(".md")), reverse=True)
    return [os.path.join(folder, name) for name in names]


def save_aggregate(markdown, note_ids, now, retros_dir=RETROS_DIR):
    """Writes the aggregate report, marks note_ids as read, and sets the last aggregate time. Returns the path.

    Raises FarmError for a note id with no note file, so a typo cannot mark the wrong notes as read.
    """
    missing = []
    for note in note_ids:
        if not os.path.isfile(os.path.join(tasks_dir(retros_dir), note + ".md")):
            missing.append(note)
    if missing:
        raise FarmError("No retro note with these ids: {}. Use the ids from `farm.py retro-pending`.".format(
            ", ".join(missing)))
    os.makedirs(aggregates_dir(retros_dir), exist_ok=True)
    path = os.path.join(aggregates_dir(retros_dir), now.strftime("%Y%m%dT%H%M%SZ") + ".md")
    with open(path, "w") as f:
        f.write(markdown.strip() + "\n")
    state = read_state(retros_dir)
    for note in note_ids:
        if note not in state["aggregatedNotes"]:
            state["aggregatedNotes"].append(note)
    state["lastAggregateAt"] = iso(now)
    # Write a temp file, then replace: the coordinator reads state.json each turn, and must never see half a file.
    temp_path = state_path(retros_dir) + ".tmp"
    with open(temp_path, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(temp_path, state_path(retros_dir))
    return path
