# G.O.A.T. AI: design

G.O.A.T. AI keeps every task in a GitHub Issue. Agents carry a task through four stages (intake, plan, implement, review) and hand a pull request to a person; people hold every gate and make every merge. This document explains how an issue carries its state, how agents avoid stepping on each other, and what happens when something goes wrong.

## The flow of one task

1. A task is an issue. Issues in `stage:discovery` (the backlog) are ignored. A person assigns the issue and labels it `stage:intake` and `goat:ready`.
2. **Intake** (Sonnet) restates the task, proposes a size, and stops at `goat:tier-review`.
3. **Plan** (Opus) writes a spec and a plan. For sizes M, L and XL, or when the plan touches an always-gate area (a database migration, auth or permissions, CI or deploy settings, a new dependency), it stops at `goat:plan-review`. An XL task is split instead of planned.
4. **Implement** (Sonnet for S, Opus for larger) works in its own git worktree on a `goat/<number>-<slug>` branch and opens a draft PR. Size L is built as stacked PRs.
5. **Review** (Opus, in a fresh session) reads the diff and the CI checks. It either sends fixes back to Implement (at most two rounds, then it asks a question) or marks the PR ready and stops at `goat:merge-review`.
6. **A person merges.** The coordinator notices and moves the issue to `stage:staging`.
7. **A person closes the issue.** The coordinator then runs a retro, removes the local worktrees and branches, and sets `goat:done`. Now and then an aggregate retro turns many retro notes into improvement issues.

Agents never approve a gate, answer a question or merge. The gate rules live in `goat-config.json`; the Intake and Plan agents apply them.

## How an issue holds its state

| Need | Where it lives |
| --- | --- |
| Stage | One `stage:*` label. A closed issue is done |
| Status (what it waits on) | At most one `goat:*` label. No `goat:` label means G.O.A.T. ignores the issue |
| Size and priority | `size:*` and `prio:*` labels |
| Owner | The assignee. G.O.A.T. only works on issues assigned to the `gh` user |
| Grouping | The milestone. G.O.A.T. shows it, and `add` can set it |
| Identity | The issue number. Branches are `goat/<number>-<slug>` |
| Claim, claim expiry, PR link, short summary | One "G.O.A.T. state" comment: JSON inside a hidden HTML comment, followed by a short readable view |
| Long stage outputs | "Doc" comments, one per section, each with a hidden title marker. Writing a section whose title exists updates it in place |
| Conversation | The ordinary comment thread |

Label names are defined once, in `goat/board.py`. `goat.py setup-repo` creates them, and `claim` refuses to start when one is missing.

**Why labels plus one hidden comment.** Labels are visible, filterable and editable by hand on any GitHub screen, which is exactly how people operate the gates. One call changes a label and removes the old label of the same kind. A claim does not fit in a label because it holds a session id and a time, so it sits in a comment that only `goat.py` writes.

## Claims

GitHub offers no compare-and-set, so a claim is written and then read back:

1. `claim` reads the issue and checks that it is a G.O.A.T. issue, assigned to this user and ready for the role, and that the repo has every label.
2. It writes the state comment (claim as `role|session`, plus the expiry), then the labels (`goat:working`, and the new stage after an approved gate).
3. It reads the issue again. If the claim is not this session's, it stops with exit code 5 ("claim lost").

Every later write (`renew`, `post`, `doc`, `summary`, `release`) first re-reads the issue and stops with exit code 5 if the claim is no longer this session's, or if a person changed the `goat:` label.

A crashed agent leaves `goat:working` with a claim that eventually expires (10 minutes for intake, 30 for plan, 60 for implement, 15 for review). The stage's agent can then take over, after reading the last handoff. Two sessions claiming in the same second can still race; the read-back catches most such cases.

**Release** posts the handoff, writes the labels, then clears the claim. If it fails between the last two writes, the same session can run `release` again: it recognises the moved labels and its own claim, finishes the state write, and does not post the handoff twice.

## Human gates

| Gate | A person sees | To approve | To request changes |
| --- | --- | --- | --- |
| Size | `goat:tier-review`, the size label, the intake summary | Swap to `goat:approved` (change the `size:*` label first to override) | Comment, then swap to `goat:ready` |
| Plan | `goat:plan-review`, the "Spec and plan" Doc section | Swap to `goat:approved` | Comment, then swap to `goat:ready` |
| Merge | `goat:merge-review`, the review notes, a ready PR | Merge the PR; the coordinator moves the issue to `stage:staging` | Comment, then swap `stage:review` for `stage:implement` and `goat:merge-review` for `goat:ready` |
| Question | `goat:answer-question` and the agent's comment | Answer in a comment, then swap to `goat:ready` | |
| Chat | `goat:chat` and a comment with the resume command | Run `goat.py chat <number>` | |

When an agent hands an issue to a person, its comment ends with an @mention of the assignee. While agents use your own `gh` login GitHub does not notify you about your own comments, so the mention is only a visible marker; a separate bot login for the agents makes it a real notification (see "Open decisions").

## Stalled issues and the health check

Two failures that would otherwise be silent.

**A stalled issue.** When an agent runs on an issue and nothing about the issue changes, the coordinator retries, but at most twice. On the turn that reaches that cap, `escalate_stalled` (in `goat/coordinator.py`):

1. Swaps the issue's `goat:` label for `goat:stalled`. This goes first: if the repo lacks that label, nothing else is posted, and the next turn tries again without flooding the issue with comments.
2. Comments on the issue, @mentioning the owner, with the agent, the number of runs, the stage, the previous status, the path of the latest agent log on this machine, and how to retry.
3. Shows a desktop notification (`goat/notify.py`: `osascript` on macOS, `notify-send` on Linux; the text is passed as arguments, so an issue title cannot run as code). `"notifications": false` turns it off.

A person retries by swapping `goat:stalled` for `goat:ready`; that also changes the attempt key, so counting restarts. Agents cannot set `stalled`, because it is not a release status.

**A dead coordinator.** The coordinator writes a heartbeat file (`goat/health.py`) at start, about every 15 seconds while idle, around each turn, and when it stops on purpose. `goat.py health` combines that file with a liveness check of the process and answers running, stale, died, stopped or never (see the README). The write is atomic, a failed write never stops the coordinator, dry runs write nothing, and a stop signal (Ctrl-C or SIGTERM) is recorded as a deliberate stop, so only a real death looks like one. G.O.A.T. does not restart the coordinator by itself: starting agents is a decision for whoever owns the laptop, and a crash loop would burn money. `health --notify` is meant for a scheduled job.

## Merge detection

A merged PR is a native GitHub event, so the coordinator looks for it each turn (`goat/merge.py`). For each of your issues at `goat:merge-review` it looks for merged PRs on `goat/<number>-*` branches. It moves the issue only when the PR stored on the issue (the bottom PR of a stack) is among them, no `goat/<number>-*` branch still has an open PR, and **every merged part has actually reached the default branch**. It then posts a comment and sets `stage:staging` with no `goat:` label.

That last check matters for stacks. If you merge part 1 into `main` and then merge part 2 while its base is still part 1's branch, part 2 lands on that old branch and never reaches `main`, even though GitHub shows "Merged". The coordinator checks each merged PR's commit against the default branch (`commit_on_branch`). A stack merged top-down into the parent branches and finished by merging the bottom into `main` is fine, because the bottom part then contains the rest. When parts are stranded, the issue stays at merge review, a comment (marked `<!-- goat:stranded -->`, posted once) explains how to finish, and each turn logs "Not moving #N". Turning on "Automatically delete head branches" in the repo settings avoids the problem, because GitHub then re-targets the next part to the default branch after each merge. PR bodies say `Refs #<number>`, never `Closes`, so merging does not close the issue: closing is a person's step and starts the retro. `"autoAdvanceMerged": false` in the config turns this off.

## Safety

Safety comes in layers: deny rules in `agent-settings.json` (headless agents only), checks in code, rules in the protocol and skills, and the automatic permission classifier.

- Agents reach issues only through `goat.py`. `gh api`, `gh issue`, `gh label`, `gh project`, `curl` against `api.github.com`, and GitHub MCP write tools are denied. `goat.py` itself runs `gh` as a subprocess, which the agent's deny rules never see.
- Agents may run the read-only `gh pr view`, `diff`, `list` and `checks`, plus the PR commands the protocol lists. They may not merge, close, approve or request changes.
- Building and releasing the app is denied (`eas build`, `eas submit`, `eas update`, `fastlane`, store uploads), as are remote-database and hosting CLIs.
- In code: an agent can only set the statuses `release` allows (never `approved`, `working` or `done`), `add --ready` is refused for headless agents, ownership and claim are checked on every write, and the cleanup runs its "nothing can be lost" checks before it removes anything.
- Deny rules match command text, so they are a second line of defense rather than a boundary. Interactive sessions do not load `agent-settings.json`.

## Other design points

- **Issue numbers** make lookups direct: one call, no scanning of a board.
- **Review reads `gh pr checks`.** A failing check that the change caused is a `must fix`, and the verdict line shows `CI: pass | fail | pending | none`.
- **Retries.** `goat/github.py` retries rate limits and network errors twice before failing.
- **The summary is capped at 2,000 characters** to stay short; the full text always lives in the Doc sections and is never cut.
- **Gate rules are applied by agents**, not enforced inside `release`, and CI status is not required for `merge-review`.

## Known limits

- The claim is not atomic (see Claims).
- `board_items` reads at most 500 issues that carry a `goat:` or `stage:` label, newest first. A repo with more needs paging.
- Run records, attempt counts, retro notes and claim records live on one machine. A second machine cannot `chat`, run a retro, or count attempts.
- The coordinator trusts process ids with no start-time check, so a reused pid looks alive. `goat.py health` shares this weakness, but it reports "running" only while the heartbeat is also fresh, which makes a false positive unlikely, not impossible.
- The heartbeat proves the coordinator is alive, not that it makes progress: a coordinator stuck inside one turn goes stale after 5 minutes, but one that loops without progress does not.
- A person's label edit between two agent writes is caught by the next write's check, not before.
- GitHub limits a comment to 65,536 characters, so a longer spec must be split into two Doc sections.

## Open decisions

- **Agent identity.** Agents currently act as your own `gh` login. A separate bot account would give real notifications and clear attribution, and would let you approve a PR yourself. It needs a second account, a token (`GH_TOKEN` for the coordinator) and collaborator access. The code needs no change, because `gh` takes the login from `GH_TOKEN`.
- **The Supabase schema and the app's first screens** are still backlog issues in the app repo. G.O.A.T. does not depend on the app's stack: the Plan agent learns the test, lint and run commands from the project's own `CLAUDE.md`.
