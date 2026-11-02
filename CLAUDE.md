# Fitness App Factory

A software factory on GitHub Issues: Python CLI (`farm.py`, `farm/`) plus Claude Code skills (`skills/`). Read `docs/design.md` before changing behavior, and `README.md` for use.

## Rules for working in this repo

- Python standard library only. No third-party dependencies.
- All GitHub access goes through `farm/github.py` (`gh` CLI). Everything else takes a client, so tests use `farm/tests/fakes.py` (`FakeClient`) and never touch the network.
- The farm's label names and the state-comment format are in `farm/board.py`. Change them there only, and update `farm.py setup-repo` output, `farm/guide.md`, the protocol and the tests together.
- Agents never merge, approve a gate, or call `gh api`, `gh issue` or `gh label`. Keep `agent-settings.json`, `skills/farm-board/protocol.md` and `farm/guide.md` in step with the CLI. `farm/tests/test_cli_guide.py` checks the guide against the parser.
- Do not create or push tags, and do not change `VERSION` except in a release PR.

## Test

```sh
python3 -m unittest discover -s farm/tests -t . -v
```
