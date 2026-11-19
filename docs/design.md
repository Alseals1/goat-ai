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
8. **Whenever a person wants the app exercised,** they run `/goat-qa`. The QA agent starts the app on local services, walks it in a browser, and files bugs and UX or UI recommendations as `stage:discovery` issues. It is a side role like retro: no claim, no stage, never a gate.

Agents never approve a gate, answer a question or merge. The gate rules live in `goat-config.json`; the Intake and Plan agents apply them. A repo can also run on **autopilot** (below): then the coordinator, in code, approves the gates, merges, runs QA, closes the issue and starts the next one.

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

## The named team

Each role has a first name and a one-line voice (`agents` in `goat-config.json`; `goat/team.py` reads it). Comment headers become `🤖 Priya · Review agent · handoff · G.O.A.T. 0.1.0`, the coordinator signs as `Dex · Dispatcher`, and desktop notifications are titled with Dex's name and address the owner by the name in the local config (`owner`). It is cosmetic by design:

- Nothing branches on a name. The role stays in every header, and the name goes before it, so the Review skill's round counting (it looks for "Review agent · handoff") keeps working.
- A role with no entry in the cast keeps its plain title, so removing the section restores the old wording.
- The voice is limited to the first and last sentence of a post. Findings, plans, code, commit messages and PR titles stay plain, bad news is never softened, and an agent never claims to be a person (protocol section 5).
- The owner's name and title live in the local config, not in the repository.

## Discord messages

The coordinator can post into a Discord channel through a webhook (`goat/discord.py`, `goat/messenger.py`). Each message is signed by the teammate who owns the moment, using the webhook's per-message name.

- **When.** `tell_waiting` runs each turn. It announces an issue the first time it is seen at a gate, a question or a chat, and remembers what it announced in the coordinator's state file (`notified`). The memory is cleared when the issue leaves the waiting state, so a second round is announced again. A failed send is logged and retried on the next turn; it never stops the coordinator. Dex also announces merges and stalled issues.
- **Activity.** `tell_stage_moves` also runs each turn and remembers each issue's stage in the state file (`stages`). An issue seen for the first time, or one that moved into staging or done, is recorded without a message. `start_launches` and `prune_finished` take an optional `announce` callable (`activity_announcers` in `goat/coordinator.py`) for "agent started" and "agent finished". After a stage run ends, the issue is read once so the message can name who it is on (`messenger.on_whom`: the owner at a gate, a question, a chat or a stall; the agent with a live claim; else the agent due next). The aggregate retro has no issue, so it is not announced. A failed send is logged and, for moves, retried on the next turn. `"discordActivity": false` turns the three message kinds off. A run that ends while `goat.py status`, `stop` or `update` prunes the state first is not announced.
- **Secrets.** The webhook URL lives in `~/.goat-ai/discord.env`, outside the repo. It never appears in logs, errors or messages, and the agents' deny rules block reading that file and calling Discord (`agent-settings.json`).
- **Hardening.** `allowed_mentions` is empty on every message, so issue text cannot ping anyone. Text is stripped of control characters and cut to Discord's limits. Rate limits are waited out (capped) and retried, as are server errors.
- **Certificates.** Verification is never turned off. When Python has no root certificates of its own (the python.org build on macOS), the system bundle is used.
- **Tests.** The test package points the env file at an empty temporary folder, so no test can read the real settings or send a real message.

## Approving from Discord

The owner types `start 9`, `approve 9`, `send back 9: note` or `answer 9: text` in a private channel. `goat/inbox.py` reads it (REST polling with the bot token, every 15 seconds while the coordinator waits between turns: no gateway connection and no libraries) and the coordinator makes the label change.

- **Not an agent.** The coordinator acts as the owner, with the owner's `gh` login, through `label_changes` and `set_labels`, the same path as a hand edit, so a label slip (removing the wrong label) cannot happen. Agents never read Discord and cannot change gate labels.
- **Checks, in order.** The message is from `DISCORD_OWNER_USER_ID` and not a bot or webhook, in the approvals channel, a plain message or reply, not empty, and under 30 minutes old. The issue is read fresh, is an open goat issue assigned to the owner, has no live claim, and waits at the gate the command is for. Approve: size or plan checkpoint. Send back: size, plan or merge checkpoint. Answer: a question. Everything else changes nothing and gets a reason.
- **Never a merge.** `approve` on a merge checkpoint is refused. Send back from merge review sets `stage:implement` and `goat:ready`, the documented path.
- **At most once.** The cursor (`state["inbox"]["after"]`) moves and is saved before the command runs.
- **Listening late.** The inbox remembers when it started. The first look reads the previous 30 minutes, so a message typed just before it started listening gets a reply ("I had not started listening") instead of silence, and a message typed just after is acted on. A message sent before the start is never run, including after a restart.
- **Start.** `start N` is for an open issue with no goat label, in the backlog (or with no stage), assigned to nobody or to the owner. The owner is assigned first, then `stage:intake` and `goat:ready` are set, then a note is left. Issues already in the factory, past the backlog, closed, or owned by someone else are refused.
- **Order of writes.** A note (send back, answer) is posted before the labels change, so an agent never restarts without it. For an approval the labels change first, then the audit note; a failed note is reported but the approval stands.
- **Audit.** Every action leaves a comment starting with `<!-- goat:discord -->`. The owner's words have no robot header, so agents read them as a person's comment.
- **Turn at once.** A successful command triggers a turn, like the `r` key, so the next agent starts without waiting for the interval.
- **Replies.** Dex answers in the channel as the bot, as a reply to the message. If the bot cannot write there, the webhook posts into the dispatch channel instead.
- **Quiet failures.** A problem (bad token, channel not found, bot not allowed in) is logged once until it changes. The channel is looked up again every 5 minutes, so creating it while the coordinator runs works. An empty message from the owner means Message Content Intent is off, and Dex says so once.
- **Secrets.** The token is only in the `Authorization` header and is removed from every error. `discord-check` reads only. Agents are denied the file, Discord, and both commands.
- **Residual risk.** A taken-over Discord account could approve a size or plan, never a merge. Two-step verification is the defence. A PIN word is an option if needed.

## Merge detection

A merged PR is a native GitHub event, so the coordinator looks for it each turn (`goat/merge.py`). For each of your issues at `goat:merge-review` it looks for merged PRs on `goat/<number>-*` branches. It moves the issue only when the PR stored on the issue (the bottom PR of a stack) is among them, no `goat/<number>-*` branch still has an open PR, and **every merged part has actually reached the default branch**. It then posts a comment and sets `stage:staging` with no `goat:` label.

That last check matters for stacks. If you merge part 1 into `main` and then merge part 2 while its base is still part 1's branch, part 2 lands on that old branch and never reaches `main`, even though GitHub shows "Merged". The coordinator checks each merged PR's commit against the default branch (`commit_on_branch`). A stack merged top-down into the parent branches and finished by merging the bottom into `main` is fine, because the bottom part then contains the rest. When parts are stranded, the issue stays at merge review, a comment (marked `<!-- goat:stranded -->`, posted once) explains how to finish, and each turn logs "Not moving #N". Turning on "Automatically delete head branches" in the repo settings avoids the problem, because GitHub then re-targets the next part to the default branch after each merge. PR bodies say `Refs #<number>`, never `Closes`, so merging does not close the issue: closing is a person's step and starts the retro. `"autoAdvanceMerged": false` in the config turns this off.

## Autopilot

`goat/autopilot.py`, turned on per repo by the `autopilot` section of the local config. The coordinator does in code what a person does at each gate; agents gain no power. Each turn, for each autopilot repo, before the merge check and the launches:

- **Gates.** `tier-review` is approved at once. `plan-review` is approved once every issue on the body's first `Depends on` line is closed (closed issues come from this turn's read, others from one `gh issue view`, and an unreadable one counts as open). This replaces the habit of holding a plan approval until its dependencies merge. The label goes first, then a note marked `<!-- goat:autopilot-approved -->`. The items read this turn are updated in place, so the next agent starts in the same turn.
- **Merge.** For an issue at `merge-review` with no live claim and no hold since the last review handoff, it looks at its open `goat/<number>-*` PRs and acts on the lowest one (the one whose base is not another open part): re-target it to the default branch if the part below was merged, then on a later turn read `gh pr view` (draft, `mergeable`, `statusCheckRollup`) and merge it once it is not a draft, `MERGEABLE`, and no check failed or is pending. **CI guard:** before any merge it reads the default branch's own CI (`branch_ci`: the push runs on its head commit). Red stops every merge in that repo until it is green again, and Dex says so once per failing run; still running means wait. At most one PR merges per turn, so each merge waits for the CI run its own merge started, and a merge that breaks the branch stops the next one. In a repo with CI, a PR with no checks has not been tested yet (a re-targeted stack part waits for its new run): it waits, and is held after 30 minutes. Only a repo with no CI at all merges a PR that has no checks. One PR uses `mergeMethod`; a stack uses merge commits so each next part still applies cleanly. A failing check or `CONFLICTING` sends the issue back to Implement (note first, then `stage:implement` and `goat:ready`), at most `MAX_SEND_BACKS` (2) times, counted by `<!-- goat:autopilot-sendback -->` comments. Anything else is a **hold**: a `<!-- goat:autopilot-hold -->` comment that @mentions the owner; Discord then announces the merge review as usual, and the autopilot leaves the issue until a new review round. It never merges when the default branch is `main` or `master` (`NEVER_MERGE_INTO`): production stays a person's step.
- **QA.** When `goat/merge.py` moves an autopilot issue to staging, the issue is queued in the coordinator state (`autopilot.qa`). A `qa` run (`/goat-qa <number>`, Sonnet, cap 1) starts in `<repo>-goat/qa`, a worktree detached at `origin/<default>` that keeps untracked files (installed packages) between runs and refuses to move when a tracked file changed. An entry queued in this turn waits for the next turn's read (this turn's read still shows the stage before the merge); it is dropped only when the issue is closed. When the run ends, the coordinator reads the JSON log: on success it posts the final reply (cut at 6,000 characters, marked `<!-- goat:autopilot-qa -->`) and closes the issue, which starts the retro and the cleanup; on failure it tries once more, then comments and leaves the issue in staging. In autopilot QA files at most 3 issues, high or medium only, which keeps a polish loop from feeding itself.
- **Next work.** In flight means the owner's open issues with a goat label that do not wait on a person (a question, a chat or a stall), plus issues waiting for QA. While fewer than `inFlight` are in flight, the daily start count is under `maxStartsPerDay`, and the backlog has an issue whose dependencies are closed, a `pm` run (`/goat-pm`, Opus, cap 1) starts in the main checkout. Its attempt key is the ready list, the in-flight list and the room, so a PM that picked nothing is not run again until the board changes. The PM writes picks with `goat.py pick`, which checks the issue is ready and a slot is free and writes `~/.goat-ai/coordinator/picks/<owner>__<repo>.json`, nothing on GitHub. When the run ends the coordinator takes the file, checks each pick again (open, backlog, no goat label, not someone else's, dependencies closed, room left) and starts it like Discord's `start`: assign, then `stage:intake` and `goat:ready`, then a note. A start triggers a turn at once.
- **Discord.** Dex announces merges, QA closes and PM starts. Gates the autopilot handles are not announced as waiting; held merges are.
- **Every agent in an autopilot repo** gets `GOAT_AUTOPILOT=1`. The skills read it: Review knows its pass is the go-ahead to merge, QA knows its worktree and its filing limit, and every agent knows that a question is the only way left to stop for a person (protocol section 17).
- **Limits.** Merges and closes use the owner's `gh` login, like the Discord inbox. Branch protection that requires an approving review blocks the merge (the owner cannot approve their own PR); the autopilot then holds. A bot account (see "Open decisions") would remove that limit.

## Safety

Safety comes in layers: deny rules in `agent-settings.json` (headless agents only), checks in code, rules in the protocol and skills, and the automatic permission classifier.

- Agents reach issues only through `goat.py`. `gh api`, `gh issue`, `gh label`, `gh project`, `curl` against `api.github.com`, and GitHub MCP write tools are denied. `goat.py` itself runs `gh` as a subprocess, which the agent's deny rules never see.
- Agents may run the read-only `gh pr view`, `diff`, `list` and `checks`, plus the PR commands the protocol lists. They may not merge, close, approve or request changes.
- Building and releasing the app is denied (`eas build`, `eas submit`, `eas update`, `fastlane`, store uploads), as are remote-database and hosting CLIs.
- In code: an agent can only set the statuses `release` allows (never `approved`, `working` or `done`), `add --ready` is refused for headless agents, ownership and claim are checked on every write, and the cleanup runs its "nothing can be lost" checks before it removes anything.
- Autopilot approvals, merges, closes and starts are coordinator code, never an agent action. The PM's `pick` only writes a local file, which the coordinator re-checks before it acts.
- Deny rules match command text, so they are a second line of defense rather than a boundary. Interactive sessions do not load `agent-settings.json`.

## Other design points

- **Issue numbers** make lookups direct: one call, no scanning of a board.
- **Review reads `gh pr checks`.** A failing check that the change caused is a `must fix`, and the verdict line shows `CI: pass | fail | pending | none`.
- **Retries.** `goat/github.py` retries rate limits and network errors twice before failing.
- **The summary is capped at 2,000 characters** to stay short; the full text always lives in the Doc sections and is never cut.
- **Gate rules are applied by agents**, not enforced inside `release`, and CI status is not required for `merge-review`.

## Keeping agent context small

Every token an agent reads stays in its context and is read again, from the prompt cache, on every later step. A plan run of 30 steps that reads 100k tokens of docs and threads re-reads about 3M tokens. So the CLI and the skills keep what an agent reads small:

- `goat.py protocol --sections N,N` prints only the protocol sections a skill names. Stage agents read 12 of the 17 sections (about 60% of the text) and open section 7, 15 or 17 only when a step needs it. `goat.py guide` is read only when an agent is unsure about a command.
- `goat.py show --brief` keeps the newest Doc section of each kind ("Spec and plan (revision 2)" replaces "Spec and plan") and the newest 6 comments, and says what it left out. On RocketPal it halves #39's thread and cuts #31's from about 100k to 23k characters. Review uses the full view, because it counts rounds and finding IDs across the history.
- Protocol section 10, "Reading for context": explore with Sonnet `Explore` subagents, read long files and docs by part and by named section, and batch independent reads into one step.

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
