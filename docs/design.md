# Design: the factory on GitHub Issues

G.O.A.T. AI keeps its tasks in GitHub Issues. It started as a port of a factory that used a Monday board, and the workflow is the same: agents work a task through intake, plan, implement and review, and people hold every gate and every merge. This file says how issues carry the state, and what is different from the original.

## Workflow

1. A task is an issue. `stage:discovery` (backlog) issues are ignored by the goat. A person assigns it, sets `stage:intake` and `goat:ready`.
2. **Intake** (Sonnet) summarizes the issue and proposes a size. It stops at `goat:tier-review`.
3. **Plan** (Opus) writes the spec and plan. It stops at `goat:plan-review` for sizes M, L and XL, or when the plan touches an always-gate area (a database migration, auth or permissions, CI or deploy settings, a new dependency). XL is split instead of planned.
4. **Implement** (Sonnet for S, Opus for larger) works in its own git worktree on a `goat/<number>-<slug>` branch and opens a draft PR. Size L uses stacked PRs.
5. **Review** (Opus, a new session) reviews the PR and reads the CI checks. It sends changes back to Implement (at most 2 rounds, then it asks a question), or marks the PR ready and stops at `goat:merge-review`.
6. **A person merges.** The coordinator sees the merge and moves the issue to `stage:staging`.
7. **A person closes the issue.** The coordinator runs a retro, then cleans up the worktrees and branches and sets `goat:done`. An aggregate retro files improvement issues from many retro notes.

Agents never approve a gate, answer a question, or merge. The gate rules are in `goat-config.json` and are applied by the Intake and Plan agents.

## How an issue holds its state

| Monday (original) | GitHub |
| --- | --- |
| A board per repo | The project repo's Issues. One checkout, one repo |
| Status (stage) | One `stage:*` label. A closed issue is done |
| G.O.A.T. status | At most one `goat:*` label. No `goat:` label: G.O.A.T. ignores the issue |
| Size, Priority | `size:*` and `prio:*` labels |
| Owner | Assignee. G.O.A.T. only works on issues assigned to the `gh` user |
| Group or epic | Milestone. G.O.A.T. shows it and lets `add` set it |
| Task ID (`SFP-14`) | The issue number. Branches are `goat/<number>-<slug>` |
| Claim, Claim expires, GitHub link, Goat summary | One "goat state" comment: JSON in a hidden HTML comment, with a short readable view |
| Doc column (long text) | "Doc" comments, one per section, with a hidden title marker. A section with the same title is updated in place |
| Updates | The comment thread |
| Board automations | None needed: the coordinator moves a merged issue to `stage:staging` |

The label names are fixed (`goat/board.py`). `goat.py setup-repo` creates them, and `claim` refuses to start if one is missing.

### Why labels and a hidden comment

Labels are visible, filterable and editable by hand on every GitHub screen, which is how people hold the gates. A label change is one call, and it removes the old label of the same kind in the same call. The claim does not fit a label (it holds a session id and a time), so it lives in a comment that only `goat.py` writes.

## Claims

GitHub has no compare-and-set, so a claim is written and then read back, as in the original:

1. `claim` reads the issue, checks that the issue is a G.O.A.T. issue, is assigned to this user, and is ready for the role, and checks that the repo has every label.
2. It writes the state comment (claim `role|session`, expiry), then the labels (`goat:working`, and the new stage after an approved gate).
3. It reads the issue again. If the claim is not this session's, it stops with exit code 5 ("claim lost").

Every later write (`renew`, `post`, `doc`, `summary`, `release`) reads the issue first and stops with exit code 5 if the claim is not this session's or a person changed G.O.A.T. label.

A crashed agent leaves `goat:working` with a claim that expires (10 minutes for intake, 30 plan, 60 implement, 15 review). After that, the stage's agent can take over and reads the last handoff first. Two sessions that claim within the same second can still race: the read-back catches most of it, as before.

**Release** posts the handoff, then writes the labels, then clears the claim. If it fails between the last two writes, the same session can run `release` again: it sees the labels already moved and its own claim, finishes the state write and does not post the handoff twice.

## Human gates

| Gate | A person sees | Approve | Request changes |
| --- | --- | --- | --- |
| Tier review | `goat:tier-review`, the size label, the intake summary | Swap to `goat:approved` (change `size:*` first to override) | Comment, swap to `goat:ready` |
| Plan review | `goat:plan-review`, the "Spec and plan" doc | Swap to `goat:approved` | Comment, swap to `goat:ready` |
| Merge review | `goat:merge-review`, the review notes, a ready PR | Merge the PR. The coordinator moves the issue to `stage:staging` | Comment, swap `stage:review` to `stage:implement` and `goat:merge-review` to `goat:ready` |
| Question | `goat:answer-question`, the agent's comment | Answer in a comment, swap to `goat:ready` | |
| Chat | `goat:chat`, a comment with the resume command | `goat.py chat <number>` | |

When an agent hands an issue to a person, the comment ends with an @mention of the assignee. While agents use your own `gh` login, GitHub does not notify you of your own comments, so the mention is a visible marker only. To get real notifications, run the agents under a separate bot login (see "Open decisions").

## Stalled issues and the health check

Two things that go wrong quietly, and what the factory does about them.

**A stalled issue.** When an agent runs on an issue and the issue does not change, the coordinator tries again, but at most twice (the attempt cap). Before, it then only wrote a line in its log. Now, on the turn that hits the cap, the coordinator (`escalate_stalled` in `goat/coordinator.py`):

1. Replaces the issue's `goat:` label with `goat:stalled`. This goes first: if the repo has no such label, nothing else is posted, and the next turn tries again without spamming comments.
2. Comments on the issue, @mentioning the owner: which agent ran, how often, the stage, G.O.A.T. status before, and the path of the latest agent log on this machine, plus how to retry.
3. Shows a desktop notification (`goat/notify.py`, macOS `osascript` or Linux `notify-send`, text passed as arguments so an issue title cannot run as code). `"notifications": false` turns it off.

A person retries by replacing `goat:stalled` with `goat:ready`. The label change also changes the attempt key, so the count starts again. Agents can never set `stalled` (it is not a release status).

**A dead coordinator.** The coordinator writes a heartbeat file (`goat/health.py`) at start, every ~15 seconds while it waits, around each turn, and when it stops on purpose. `goat.py health` combines the file with a check that the process is alive and returns running, stale, died, stopped or never (see the README). The file is written atomically, write errors never stop the coordinator, dry runs write nothing, and a stop signal (Ctrl-C or SIGTERM) is recorded as a stop on purpose, so only a real death looks like one. The factory does not restart the coordinator by itself: starting agents is a decision for the person who owns the laptop, and a crash loop would spend money. `health --notify` is made for a scheduled job.

## Merge detection

A merged PR is a native GitHub event, so the coordinator checks for it each turn (`goat/merge.py`). For each of your issues at `goat:merge-review`, it finds merged PRs on `goat/<number>-*` branches. It moves the issue only when the issue's PR link (the bottom PR of a stack) is among them, and no `goat/<number>-*` branch still has an open PR. It posts a comment and sets `stage:staging` with no `goat:` label. PR bodies say `Refs #<number>`, never `Closes`, so a merge does not close the issue: closing is a person's step and starts the retro. `"autoAdvanceMerged": false` in the config turns this off.

## Safety

The layers are the same as in the original: deny rules in `agent-settings.json` (headless agents only), checks in code, rules in the protocol and skills, and the auto-mode classifier. What differs:

- Agents reach GitHub issues only through `goat.py`. `gh api`, `gh issue`, `gh label`, `gh project`, `curl` against `api.github.com`, and GitHub MCP write tools are denied. `goat.py` itself runs `gh` as a subprocess, which the agent's deny rules do not see.
- Agents may run the read-only `gh pr view`, `diff`, `list` and `checks`, and the PR commands the protocol lists. They may not merge, close, approve or request changes.
- Building and releasing the app is denied: `eas build`, `eas submit`, `eas update`, `fastlane`, store uploads. Remote database CLIs and hosting CLIs stay denied.
- In code: only `release` statuses an agent may set (never `approved`, `working` or `done`), `add --ready` refused for headless agents, ownership and claim checks, and the cleanup's "nothing can be lost" checks.
- Deny rules match command text. They are a second line of defense, not a boundary. Interactive sessions do not use `agent-settings.json`.

## What changed from the original, beyond the swap

These are the "free wins" that GitHub makes cheap. Each is small and isolated:

1. **Issue numbers.** A direct lookup. The original read the whole board to resolve a Task ID.
2. **An @mention** in a handoff that reaches a person.
3. **Review reads `gh pr checks`.** A failing check that the change caused is a `must fix`. The verdict line shows `CI: pass | fail | pending | none`.
4. **Merge detection** (above).
5. **Retries.** `goat/github.py` retries rate limits and network errors twice before it fails. The original Monday client did not retry.
6. **No 2,000-character split.** The summary is still limited to 2,000 characters, to stay short, but the full text is never cut.

Not changed on purpose: the gate rules are still applied by agents (not enforced in `release`), CI status is not required for `merge-review`, and the attempt cap and stale-claim design are as before.

## Known limits

- The claim is not atomic (see Claims).
- `board_items` reads at most 500 issues that carry a `goat:` or `stage:` label, newest first. A repo with more needs paging.
- Run records, attempt counts, retro notes and claim records are local to one machine, as in the original. A second machine cannot `chat`, retro or count attempts.
- The coordinator trusts process ids with no start-time check (a reused pid looks alive). `goat.py health` has the same weakness: it reports "running" only while the heartbeat is also fresh, which makes a false positive unlikely but not impossible.
- The heartbeat shows the coordinator is alive, not that it is doing useful work: a coordinator that is stuck inside one turn goes stale after 5 minutes, but one that loops without progress does not.
- A person's label edit between two agent writes is caught by the next write's check, not before.
- Comments are limited to 65,536 characters by GitHub. A spec longer than that must be split into two doc sections.

## Open decisions

- **Agent identity.** Agents use your own `gh` login for now. A separate bot account would give real notifications and clear attribution, and would let you approve a PR yourself. It needs a second account, a token (`GH_TOKEN` for the coordinator), and collaborator access. The code needs no change: `gh` takes the login from `GH_TOKEN`.
- **App repo and stack.** The app repo does not exist yet. Nothing in the factory depends on the stack. The Plan agent learns test, lint and run commands from the project repo's `CLAUDE.md`, so write them there when the repo exists. If the app uses Expo, the `eas` denies already apply.
