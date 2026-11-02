#!/usr/bin/env bash
# Sets up Software Farm for one developer:
#   1. Links the skills (farm and farm-*) into ~/.claude/skills, so every Claude Code session can use them.
#   2. Links ~/.software-farm/farm.py to this repo's farm.py. The skills call the farm CLI by that path.
#   3. Creates ~/.software-farm/config.json for the coordinator (local checkouts of project repos), with
#      default limits. An existing config with no coordinator.maxAgentsByRole gets the default role caps added.
#
# Usage: scripts/install.sh
# Each project repo needs a GitHub repo for its issues (its `origin` remote, or FARM_GITHUB_REPO in its .env),
# and the farm's labels: run `farm.py setup-repo` there once. The `gh` CLI must be installed and logged in.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SKILLS_DIR="$HOME/.claude/skills"
CONFIG_DIR="$HOME/.software-farm"
# Default per-role agent caps for the coordinator. With maxAgents 3, no single role can take every slot.
# retro: the Done item retros (skills/farm-retro), one at a time.
DEFAULT_ROLE_CAPS='{ "intake": 2, "plan": 2, "implement": 2, "review": 2, "retro": 1 }'

mkdir -p "$SKILLS_DIR"
for skill in "$REPO_DIR"/skills/*/; do
  name="$(basename "$skill")"
  target="$SKILLS_DIR/$name"
  if [ -L "$target" ]; then
    ln -sfn "${skill%/}" "$target"
    echo "Updated link: $target"
  elif [ -e "$target" ]; then
    echo "Skipped $name: $target exists and is not a link. Remove it and run again." >&2
  else
    ln -s "${skill%/}" "$target"
    echo "Linked: $target"
  fi
done

mkdir -p "$CONFIG_DIR"
ln -sfn "$REPO_DIR/farm.py" "$CONFIG_DIR/farm.py"
echo "Linked: $CONFIG_DIR/farm.py"

CONFIG_FILE="$CONFIG_DIR/config.json"
if [ -e "$CONFIG_FILE" ]; then
  # Add the default role caps to a config made before they existed. Bash cannot edit JSON safely.
  # The step always exits 0, so a broken config does not stop the rest of the setup.
  python3 - "$CONFIG_FILE" "$DEFAULT_ROLE_CAPS" <<'PY'
import json
import sys

path, default_caps = sys.argv[1], json.loads(sys.argv[2])
try:
    with open(path) as f:
        config = json.load(f)
except ValueError as error:
    print("Left {} as it is: it is not valid JSON ({}).".format(path, error), file=sys.stderr)
    sys.exit(0)
coordinator = config.setdefault("coordinator", {}) if isinstance(config, dict) else None
if not isinstance(coordinator, dict):
    print("Left {} as it is: its coordinator settings are not an object.".format(path), file=sys.stderr)
elif "maxAgentsByRole" in coordinator:
    print("Kept existing config: {}".format(path))
else:
    coordinator["maxAgentsByRole"] = default_caps
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    print("Added default maxAgentsByRole to {}".format(path))
PY
else
  printf '{\n  "repos": {},\n  "coordinator": {\n    "maxAgents": 3,\n    "intervalSeconds": 900,\n    "maxAgentsByRole": %s\n  }\n}\n' \
    "$DEFAULT_ROLE_CAPS" > "$CONFIG_FILE"
  echo "Created config: $CONFIG_FILE"
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "Note: the gh command is not installed. Install the GitHub CLI and run: gh auth login" >&2
fi
echo "Done. In each project repo, create the farm's labels on GitHub (safe to repeat):"
echo "  python3 ~/.software-farm/farm.py setup-repo"
echo "Then start a new Claude Code session there and run /farm-board."
echo "For the coordinator, add each project's local checkout to \"repos\" in $CONFIG_FILE."
