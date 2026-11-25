# G.O.A.T. floor (local only)

A live picture of your G.O.A.T. AI factory as an animated office, in 2D or 3D. Adapted from the Software Farm floor.
The viewer only reads: it never changes an issue, a label or a file. The optional plan usage status line (below) is the
only part that writes, to `~/.goat-ai/viz/usage.json`.

```sh
python3 viz/live_server.py            # repos from ~/.goat-ai/config.json
python3 viz/live_server.py --repo ~/path/to/project --mine
```

Open http://127.0.0.1:8765. Run it next to `goat.py run`.

It only reads: `goat.py list --json` in each repo (every 30 s, `--board-every` to change), the coordinator's
`~/.goat-ai/coordinator/state.json`, `~/.goat-ai/runs.jsonl` (the last 300 lines), and `goat.py show <number> --json`
for an issue that waits on a question or a chat, and `~/.goat-ai/viz/usage.json` (see Plan usage). From the local
config it reads only `repos` and `coordinator`. It listens on 127.0.0.1 only and writes nothing. `GOAT_VIZ_HOME` points it at another folder than `~/.goat-ai`.

## Shift report

The Shift report button opens an end-of-day summary for any day, built from every line of `runs.jsonl` that ended that
day: runs, issues, gates, questions, send-backs, agent time, a timeline per stage, time per stage, how long issues
waited on people, tokens, and your latest saved plan usage. "Copy as text" gives a short version to paste into Slack
or Discord. It can't count merges, QA, project manager or Retro runs, or runs that ended without releasing their
issue, because none of these write a run record.

## Plan usage (optional)

The page can show your Claude plan's 5-hour and 7-day limits (percent used and when each resets), or a gateway spend
limit. Claude Code only gives these numbers to a status line script, and only for claude.ai Pro and Max plans or behind
a gateway that sets a spend limit. `usage_statusline.py` is that script: it saves the numbers to
`~/.goat-ai/viz/usage.json` and prints a short usage line in Claude Code. It runs on your computer and uses no API
tokens. The numbers update when you use an interactive Claude Code session; the coordinator's headless agents don't
run a status line.

```sh
python3 viz/install_statusline.py            # adds it; keeps a status line you already have
python3 viz/install_statusline.py --remove   # takes it out and puts your old one back
```

It backs up `~/.claude/settings.json` first and changes only the `statusLine` key. If the Software Farm's usage status
line is already there, this one wraps it, and Claude Code shows both usage lines. Then send one message in Claude Code.
The page shows the limits, or says that your account gets none. The "G.O.A.T. agent tokens today" card comes from
`runs.jsonl` and needs no setup.

## Files

- `live_server.py`: the server (Python standard library). `normalize_item()` turns `goat.py list --json` into the
  shape the page was written for: `plan-review` becomes `plan review`, `chat` becomes `chat with agent`, `staging`
  becomes `staging review`, and a card's id is `owner/name#number`, because issue numbers repeat across repos.
  Tests: `goat/tests/test_viz_server.py`.
- `floor.html`: the page. It polls `/api/snapshot` every 2 s and moves cards between rooms. The Shift report reads
  `/api/day?date=YYYY-MM-DD`.
- `usage_statusline.py`, `install_statusline.py`: the optional plan usage status line, and its installer.
- `floor.js`: the drawing (sprites, effects, day and night, weather, sound).
- `floor3d.js` + `vendor/three.min.js`: the 3D view (Three.js r158, MIT license in `vendor/three-LICENSE.txt`). The View
  button switches 2D and 3D, and the page remembers your choice. In 3D: drag to turn, right-drag or Shift-drag to move,
  scroll to zoom, double-click to reset the camera. Click an agent or a card.

## The rooms

- **GitHub issues** (the wall): issues at `goat:ready` in intake or in the backlog (`stage:discovery`).
- **Intake, Plan, Implement, Review**: each stage's desks, and a tray of the issues waiting for that stage's agent.
- **QA**: where the autopilot's QA agent sits. No card waits here.
- **Shipping dock**: issues at `stage:staging`, or closed while at merge review.
- **Gate desk**: issues waiting for you: tier, plan or merge review, a question, a chat, or `goat:stalled`.
- **Lounge**: a paused agent, asleep, with the question it asked in a speech bubble.
- **Project manager**: where the autopilot's PM agent sits.
- **Retro library**: Retro agents, and the shelf of `goat:done` issues.
- **Parking**: kept from the Farm, where it holds items waiting on others. G.O.A.T. has no dependency waits, so it stays
  empty.

## What the effects mean

- Weather in the windows: sunny when nothing waits for you, cloudy at 1 or 2, rain at 3 or more (cards at the Gate desk).
- Day and night follow your computer's clock. The Time button forces day, dusk or night.
- APPROVED / CHANGES / ANSWERED stamp: a person moved a card off a gate.
- Confetti and the truck: an issue reached staging, or was closed, at merge review.
- Smoke and a bug card: an Intake, Plan, Implement or Review run ended with no record in `runs.jsonl` (an error, or it
  was stopped).
- Papers on a desk: one sheet per 2 minutes the agent has worked.
- Scoreboard: agents running, cards waiting for you, runs and output tokens today, average run time per stage. Every few
  seconds it switches to plan usage and today's input and output tokens.
- Sound is off until you press the Sound button.
