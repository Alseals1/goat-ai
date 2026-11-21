# G.O.A.T. floor (local only)

A live picture of your G.O.A.T. AI factory as an animated office, in 2D or 3D. Adapted from the Software Farm floor.
It is a viewer only: it reads, and it never changes an issue, a label or a file.

```sh
python3 viz/live_server.py            # repos from ~/.goat-ai/config.json
python3 viz/live_server.py --repo ~/path/to/project --mine
```

Open http://127.0.0.1:8765. Run it next to `goat.py run`.

It only reads: `goat.py list --json` in each repo (every 30 s, `--board-every` to change), the coordinator's
`~/.goat-ai/coordinator/state.json`, `~/.goat-ai/runs.jsonl` (the last 300 lines), and `goat.py show <number> --json`
for an issue that waits on a question or a chat. From the local config it reads only `repos` and `coordinator`.
It listens on 127.0.0.1 only and writes nothing. `GOAT_VIZ_HOME` points it at another folder than `~/.goat-ai`.

## Files

- `live_server.py`: the server (Python standard library). `normalize_item()` turns `goat.py list --json` into the
  shape the page was written for: `plan-review` becomes `plan review`, `chat` becomes `chat with agent`, `staging`
  becomes `staging review`, and a card's id is `owner/name#number`, because issue numbers repeat across repos.
  Tests: `goat/tests/test_viz_server.py`.
- `floor.html`: the page. It polls `/api/snapshot` every 2 s and moves cards between rooms.
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
- Scoreboard: agents running, cards waiting for you, runs and output tokens today, average run time per stage.
- Sound is off until you press the Sound button.
