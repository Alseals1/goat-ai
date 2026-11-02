---
name: farm-board
description: Show this developer's Software Farm issues in the project's GitHub repo, and what to run next. Use when asked what is on the farm board, what work is waiting, or which Software Farm agent to start. Also holds the board protocol that the farm-intake, farm-plan, farm-implement and farm-review skills follow.
model: haiku
---

# Software Farm board status

Run this in a project repo. The board is that project's GitHub Issues.

1. Follow section 1 of [protocol.md](protocol.md): check that `~/.software-farm/farm.py` exists.
2. Run `python3 ~/.software-farm/farm.py list --mine`. Each line has: issue number, title, stage, farm status, size, and the next step. Issues that wait for a person come first.
3. Show the result as a short table:

   | Issue | Title | Stage | Farm status | Next |
   | --- | --- | --- | --- | --- |

   For **Next**, use the next step from `list`:
   - "run /farm-<role> <id>": the command to run in this repo. For `implement`, put the model first: `/model sonnet` for size S, `/model opus` for M and L.
   - "waits for a person": what the issue waits for. A gate is approved by replacing its label with `farm:approved`. A question is answered in a comment on the issue, then the label is replaced with `farm:ready`. For `farm:chat`, the developer runs `python3 ~/.software-farm/farm.py chat <number>` in a terminal to resume the agent's session.
   - "agent working": the role that holds the claim, and when it expires.
   - At merge review: "Merge the PR on GitHub": the coordinator then moves the issue to `stage:staging`. To ask for changes instead: comment, then replace `stage:review` with `stage:implement` and `farm:merge-review` with `farm:ready`.

This skill only reads the board. It never claims or changes issues.
