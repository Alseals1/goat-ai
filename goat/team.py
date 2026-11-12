"""The G.O.A.T. team: every agent role has a first name and a short voice, so a thread reads like a small team.

The cast is the `agents` section of goat-config.json. It is cosmetic: nothing in the logic depends on a name, and a
role with no entry simply keeps its plain title ("Plan agent"). The owner's own name and title live in the local
config (~/.goat-ai/config.json, key `owner`), not in the repository.

Rules the names never change: the role is always shown next to the name, so nobody mistakes an agent for a person,
and the voice only colours the first and last sentence of a post.
"""

import json

# The coordinator's own voice: it posts when a PR is merged, an issue stalls, or work is stranded.
DISPATCHER = "dispatcher"
# The aggregate retro is the retro agent's job, so it uses the retro agent's name.
ALIASES = {"retro-aggregate": "retro"}
ROLE_TITLES = {"dispatcher": "Dispatcher", "qa": "QA agent", "pm": "Project manager agent"}


def cast(shared):
    """The agents section of the shared config, or {} when there is none."""
    agents = (shared or {}).get("agents")
    return agents if isinstance(agents, dict) else {}


def title(role):
    """The plain title of a role: "Plan agent", "Dispatcher"."""
    role = ALIASES.get(role, role)
    return ROLE_TITLES.get(role) or "{} agent".format(role.capitalize())


def persona(shared, role):
    """{"name", "title", "voice"} for a role. name and voice are "" when the config has none for it."""
    entry = cast(shared).get(ALIASES.get(role, role))
    entry = entry if isinstance(entry, dict) else {}
    return {"name": str(entry.get("name") or "").strip(), "title": title(role), "voice": str(entry.get("voice") or "").strip()}


def signature(shared, role):
    """How a role signs a post: "Maya · Intake agent", or just "Intake agent" with no name configured."""
    who = persona(shared, role)
    return "{} · {}".format(who["name"], who["title"]) if who["name"] else who["title"]


def dispatcher_signature(shared):
    """How the coordinator signs: "Dex · Dispatcher", or "G.O.A.T." with no name configured."""
    who = persona(shared, DISPATCHER)
    return "{} · {}".format(who["name"], who["title"]) if who["name"] else "G.O.A.T."


def dispatcher_name(shared):
    return persona(shared, DISPATCHER)["name"] or "G.O.A.T."


def owner(local_config):
    """{"name", "title"} of the person the team works for, from the local config. Empty strings when not set."""
    entry = (local_config or {}).get("owner")
    entry = entry if isinstance(entry, dict) else {}
    return {"name": str(entry.get("name") or "").strip(), "title": str(entry.get("title") or "").strip()}


def read_owner(path):
    """The owner from a config file path. Any problem reading it gives an empty owner."""
    try:
        with open(path) as f:
            return owner(json.load(f))
    except (OSError, ValueError):
        return owner({})


def lines(shared, local_config=None, only_role=None):
    """Text lines describing the team (or one role), for `goat.py team`."""
    out = []
    boss = owner(local_config)
    if boss["name"] and only_role is None:
        out.append("Owner: {}{}".format(boss["name"], ", " + boss["title"] if boss["title"] else ""))
    roles = [only_role] if only_role else ["intake", "plan", "implement", "review", "retro", "qa", "pm", DISPATCHER]
    for role in roles:
        who = persona(shared, role)
        name = who["name"] or "(no name set)"
        line = "{}: {}".format(who["title"], name)
        if who["voice"]:
            line += ". Voice: " + who["voice"]
        out.append(line)
    return out
