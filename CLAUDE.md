# G.O.A.T. AI

A software factory on GitHub Issues: Python CLI (`goat.py`, `goat/`) plus Claude Code skills (`skills/`). Read `docs/design.md` before changing behavior, and `README.md` for use.

## Rules for working in this repo

- Python standard library only. No third-party dependencies.
- All GitHub access goes through `goat/github.py` (`gh` CLI). Everything else takes a client, so tests use `goat/tests/fakes.py` (`FakeClient`) and never touch the network.
- G.O.A.T.'s label names and the state-comment format are in `goat/board.py`. Change them there only, and update `goat.py setup-repo` output, `goat/guide.md`, the protocol and the tests together.
- Agents never merge, approve a gate, or call `gh api`, `gh issue` or `gh label`. In an autopilot repo the coordinator (`goat/autopilot.py`), not an agent, approves gates and merges, and never into `main` or `master`. Keep `agent-settings.json`, `skills/goat-board/protocol.md` and `goat/guide.md` in step with the CLI. `goat/tests/test_cli_guide.py` checks the guide against the parser.
- Do not create or push tags, and do not change `VERSION` except in a release PR.

## Test

```sh
python3 -m unittest discover -s goat/tests -t . -v
```
