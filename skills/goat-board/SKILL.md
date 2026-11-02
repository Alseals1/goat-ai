---
name: goat-board
description: Show this developer's G.O.A.T. AI issues in the project's GitHub repo, and what to run next. Use when asked what is on the board, what work is waiting, or which G.O.A.T. agent to start. This folder also holds protocol.md, the rules every G.O.A.T. role skill follows.
model: haiku
---

# G.O.A.T. AI board status

Use this inside a project repo. The board is that repo's GitHub Issues. This skill only reads: it never claims, labels or changes an issue.

## Steps

1. Check that `~/.goat-ai/goat.py` exists (protocol section 1). If it does not, say so and stop.
2. Run `python3 ~/.goat-ai/goat.py list --mine`. Each line shows the issue number, title, stage, status, size and the next step. Lines that wait for a person come first.
3. Present the lines as a compact table:

   | Issue | Title | Stage | Status | Next |
   | --- | --- | --- | --- | --- |

## Filling in the "Next" column

Translate the next step from `list` like this:

| `list` says | Write |
| --- | --- |
| `run /goat-<role> <number>` | The command to run in this repo. For `implement`, put the model switch first: `/model sonnet` for size S, `/model opus` for M and L. |
| `waits for a person` | What the person must do: approve a gate by swapping its label for `goat:approved`; answer a question in a comment and then swap the label for `goat:ready`; for `goat:chat`, run `python3 ~/.goat-ai/goat.py chat <number>` in a terminal; for `goat:stalled`, read the agent log named in the issue's comment, then set `goat:ready` to retry |
| `agent working` | Which role holds the claim and when it expires |
| merge review | "Merge the PR on GitHub." The coordinator then moves the issue to `stage:staging`. To ask for changes instead: comment, swap `stage:review` for `stage:implement`, and swap `goat:merge-review` for `goat:ready` |
