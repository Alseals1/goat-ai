---
name: goat-board
description: Show this developer's G.O.A.T. AI issues in the project's GitHub repo, and what to run next. Use when asked what is on G.O.A.T. board, what work is waiting, or which G.O.A.T. AI agent to start. Also holds the board protocol that the goat-intake, goat-plan, goat-implement and goat-review skills follow.
model: haiku
---

# G.O.A.T. AI board status

Run this in a project repo. The board is that project's GitHub Issues.

1. Follow section 1 of [protocol.md](protocol.md): check that `~/.goat-ai/goat.py` exists.
2. Run `python3 ~/.goat-ai/goat.py list --mine`. Each line has: issue number, title, stage, goat status, size, and the next step. Issues that wait for a person come first.
3. Show the result as a short table:

   | Issue | Title | Stage | G.O.A.T. status | Next |
   | --- | --- | --- | --- | --- |

   For **Next**, use the next step from `list`:
   - "run /goat-<role> <id>": the command to run in this repo. For `implement`, put the model first: `/model sonnet` for size S, `/model opus` for M and L.
   - "waits for a person": what the issue waits for. A gate is approved by replacing its label with `goat:approved`. A question is answered in a comment on the issue, then the label is replaced with `goat:ready`. For `goat:chat`, the developer runs `python3 ~/.goat-ai/goat.py chat <number>` in a terminal to resume the agent's session.
   - "agent working": the role that holds the claim, and when it expires.
   - At merge review: "Merge the PR on GitHub": the coordinator then moves the issue to `stage:staging`. To ask for changes instead: comment, then replace `stage:review` with `stage:implement` and `goat:merge-review` with `goat:ready`.

This skill only reads the board. It never claims or changes issues.
