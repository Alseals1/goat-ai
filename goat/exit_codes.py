"""goat.py exit codes. Each error class has an `exit_code` attribute with one of these; cli.main() returns it.

2 is argparse's code for bad usage, so G.O.A.T. codes start at 3.
"""

GENERAL = 1
CONFIG = 3
ITEM_NOT_AVAILABLE = 4
CLAIM_CONFLICT = 5
GITHUB_API = 6

HELP_TEXT = """exit codes:
  0  success
  1  other failure (setup-repo --check also exits 1 when it finds problems)
  2  bad usage (wrong command or arguments)
  3  configuration missing: the repo, the `gh` login, or the session id
  4  item not available: not found, not a G.O.A.T. issue, not assigned to you, or not ready for this role
  5  claim conflict: another session claimed the issue, or a person changed it. Stop work.
  6  GitHub API error (gh failed, rate limit, no network)

list, show and setup-repo take --json: one JSON document on stdout, with "version": "0.1"."""
