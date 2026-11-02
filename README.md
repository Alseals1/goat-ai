# Fitness App Factory

A software factory where AI agents and people work together on a project's GitHub Issues, taking tasks to pull requests that are ready for human review.

It is the "Software Farm" design: four Claude Code agent skills (intake, plan, implement, review), a CLI (`farm.py`) that does every issue read and write, and a coordinator that starts the agents. Tasks live in GitHub Issues: labels hold the stage and the state, assignees are owners, milestones group releases, and comments are the thread. The design is in [docs/design.md](docs/design.md).

This repo is the **factory**. The app it builds lives in a **separate repo** (see [Set up a project repo](#set-up-a-project-repo)). The factory is not tied to one app: any repo can be a project.

## Folder layout

| Path | What it is |
| --- | --- |
| `farm.py`, `farm/` | The CLI and the coordinator: add, claim, post, doc, summary, release, list, show, guide, setup-repo, cleanup, worktree, retro-facts, retro-save, retro-pending, retro-aggregate-save, version, update, and run, status, stop, chat. `farm/guide.md` is the guide that `farm.py guide` prints. `farm/github.py` is the only code that talks to GitHub |
| `VERSION` | The factory version (SemVer). See [Versions and updates](#versions-and-updates) |
| `farm-config.json` | Shared gate rules and claim leases |
| `agent-settings.json` | Allow and deny rules for the headless agents the coordinator starts. They may use `farm.py`, `gh stack` and read-only `gh pr` commands. They may not merge, call `gh api`, `gh issue` or `gh label`, force-push, or build and release the app |
| `skills/farm/` | General skill: runs `farm.py guide`, then uses the CLI |
| `skills/farm-board/` | Board status skill, and `protocol.md`: the rules every role skill follows |
| `skills/farm-intake/`, `farm-plan/`, `farm-implement/`, `farm-review/` | The four role skills |
| `skills/farm-retro/`, `farm-retro-aggregate/` | The retro skills: one reviews a closed issue and saves a local note; the aggregate finds patterns across notes and adds improvement issues. See [Retros](#retros) |
| `scripts/install.sh` | Links the skills and the CLI, and creates the local config with default agent limits |
| `docs/` | The design |

## Requirements

- Python 3 (standard library only; developed on 3.10), `git`, the GitHub CLI `gh` (logged in: `gh auth login`), and the `claude` CLI.
- `gh stack` (GitHub's stacked-PR extension) is optional. Without it, agents use plain git and gh for PR stacks.
- Your `gh` login needs write access to the project repo: issues, labels, and pull requests.

## Set up (each developer)

1. Clone this repo, check out the newest release tag (or stay on `main` while the factory is in development), and run `scripts/install.sh`. It links the skills into `~/.claude/skills` and the CLI to `~/.software-farm/farm.py`. It also creates `~/.software-farm/config.json` with default agent limits.

   ```sh
   scripts/install.sh
   ```

2. Log in to GitHub with `gh auth login`, and make sure Claude Code is logged in (`/login`).

Any agent can run `python3 ~/.software-farm/farm.py guide` to learn the CLI: the commands, the labels, and which commands agents may run.

## Set up a project repo

A project is a GitHub repo whose issues the factory works on, with a local checkout.

1. Clone the project repo. The farm finds its GitHub repo from the checkout's `origin` remote. If the issues live in a different repo than `origin`, put this in the checkout's `.env` (gitignored):

   ```
   FARM_GITHUB_REPO=owner/name
   ```

2. Create the factory's labels in the repo, from the checkout. It is safe to repeat, and `--check` only reports:

   ```sh
   python3 ~/.software-farm/farm.py setup-repo --milestone "v0.1"
   ```

3. Give agents the project's own instructions in the project repo's `CLAUDE.md`: how to run the app, the tests and the linter, and the conventions. The Plan agent reads it, and the Implement agent uses the test commands the plan names.

## Run an issue

1. Create an issue (`farm.py add`, or on GitHub). Assign it to yourself, and add the labels `stage:intake` and `farm:ready`. Issues without a `farm:` label are never touched by the farm. `stage:discovery` is the backlog.
2. In the project repo, start a Claude Code session and run the agent for the issue's stage, or let the [coordinator](#run-the-coordinator) do it. The issue is named by its number.

   | Labels | Command |
   | --- | --- |
   | `stage:intake` + `farm:ready` | `/farm-intake <number>` |
   | `stage:intake` + `farm:approved` | `/farm-plan <number>` |
   | `stage:plan` + `farm:ready` | `/farm-plan <number>` |
   | `stage:plan` + `farm:approved` | `/model sonnet` (size S) or `/model opus` (M, L), then `/farm-implement <number>` |
   | `stage:implement` + `farm:ready` | The same as the row above |
   | `stage:review` + `farm:ready` | `/farm-review <number>` (in a new session) |

3. Answer questions and approve gates on GitHub, by changing labels and commenting:
   - **`farm:answer-question`:** answer in a comment, then replace the label with `farm:ready`.
   - **`farm:chat`:** run `python3 ~/.software-farm/farm.py chat <number>` in a terminal. It resumes the agent's session in its start folder, so you can talk it through.
   - **`farm:tier-review`, `farm:plan-review`:** replace the label with `farm:approved`, or comment with the changes you want and set `farm:ready`.
   - **`farm:merge-review`:** merge the PR on GitHub. The coordinator then moves the issue to `stage:staging`. To ask for changes instead, comment, then replace `stage:review` with `stage:implement` and `farm:merge-review` with `farm:ready`.
4. When the issue is finished, **close it**. The coordinator starts a [retro](#retros) for it. After the retro, the coordinator (or `python3 ~/.software-farm/farm.py cleanup` in the project repo, which does not wait for the retro) removes its local worktrees and `farm/` branches and sets the label `farm:done`. It keeps anything with unsaved work and says why. Add `--dry-run` to see what it would do.

`/farm-board` shows your issues and the next command for each.

## The labels

`farm.py setup-repo` creates all of these:

| Label | Meaning |
| --- | --- |
| `stage:discovery`, `stage:intake`, `stage:plan`, `stage:implement`, `stage:review`, `stage:staging` | The stage. A closed issue is done |
| `farm:ready`, `farm:working`, `farm:approved`, `farm:answer-question`, `farm:chat`, `farm:tier-review`, `farm:plan-review`, `farm:merge-review`, `farm:done` | What the farm is doing or waiting for |
| `size:S`, `size:M`, `size:L`, `size:XL` | The size: sets the gates and the model |
| `prio:blocker`, `first`, `next`, `later`, `last`, `best-effort` | Priority, in this order |

An issue keeps one label of each kind. The farm keeps the claim, the PR link and a short summary in one comment that starts with a hidden `farm:state` marker: do not edit or delete it. The long stage outputs (intake summary, spec and plan, review notes) are "Doc" comments.

## Retros

A retro looks back at one closed issue and saves a short note: what went well, what went badly, and what to change in the factory. Run it in the project repo:

```sh
/farm-retro <number>
```

The skill reads the issue with `farm.py show`, and its local facts with `farm.py retro-facts <number>`: the run records, the coordinator logs, and a digest of each session. It saves the note with `farm.py retro-save`. It does not claim the issue or post to GitHub.

Notes are in `~/.software-farm/retros/tasks/<owner>_<repo>-<number>.md`, on your machine only. They can quote session text, so they are not committed or posted.

The coordinator starts the retro by itself when a farm issue is closed, before the cleanup.

### Aggregate retro

The aggregate retro (`/farm-retro-aggregate`, on Opus) reads the notes that no aggregate has read, and finds two kinds of suggestions: patterns (seen in 2 or more issues) and high-value changes (seen once, but they prevent a serious failure or save much effort). It adds the top 5 at most to the factory's own repo with `farm.py add`, in `stage:discovery`, for a person to review. The rest go in its report's "Carried over" list. A carried suggestion with no new support for 3 aggregates in a row is dropped.

Reports are in `~/.software-farm/retros/aggregates/`. `farm.py retro-pending` lists the waiting notes and the reports.

The coordinator starts it when at least 15 notes wait **and** at least 3 days passed since the last aggregate. It runs in the factory repo's checkout, so its issues go to the factory's own repo: that checkout needs a GitHub `origin` remote (or `FARM_GITHUB_REPO` in its `.env`), and a `repos` entry in the config. To pick another entry, or to change the thresholds, set `coordinator.retro` in `~/.software-farm/config.json`:

```json
"retro": { "aggregateMinRetros": 15, "aggregateMinDays": 3, "repo": "me/fitness-app-factory" }
```

`repo` is a key of `repos`. If no entry matches, the coordinator logs that and does not start the aggregate.

## Add an issue

```sh
python3 ~/.software-farm/farm.py add "Fix the login redirect" --priority next --milestone "v0.1"
```

- The description comes from `--file <path>` (`-` for stdin), or from stdin when it is piped. It is the issue body, in Markdown.
- By default the issue gets `stage:discovery`, no `farm:` label and no assignee. The farm does not touch it until a person moves it into the pipeline.
- `--ready` skips that gate: `stage:intake`, `farm:ready`, and assigned to you. The coordinator starts Intake on its next turn.
- The command prints the issue number and link, for example `Added #27: https://github.com/...`.
- `--milestone` needs an existing milestone (`setup-repo --milestone <title>` creates one).
- Agents may add issues, but never with `--ready`. In a headless agent session (`FARM_HEADLESS=1`), `add --ready` stops with an error, and `agent-settings.json` denies it. This does not cover a person's own interactive Claude session.

## Run the coordinator

The coordinator starts the agents for you: every 15 minutes it reads each project's issues and starts the right agent for each of your ready issues, up to 3 at a time. When one of its agents ends, it reads only that issue and starts the next agent at once (for example Review right after Implement opens the PR), until the issue stops at a gate or a question. It also moves an issue to `stage:staging` when you merge its PR. It runs from any folder: it reads the config and each repo's own `.env`, not the current folder's. You still answer questions, approve gates, and merge.

1. Add your local checkouts to `~/.software-farm/config.json`, one per project repo. One coordinator runs all of them:

   ```json
   {
     "repos": {
       "me/fitness-app": "~/Developer/fitness-app",
       "me/fitness-app-factory": "~/Desktop/Fitness-App-Factory"
     },
     "coordinator": {
       "maxAgents": 3,
       "intervalSeconds": 300,
       "maxAgentsPerRepo": 1,
       "autoAdvanceMerged": true,
       "maxAgentsByRole": { "intake": 2, "plan": 2, "implement": 2, "review": 2, "retro": 1 }
     }
   }
   ```

   - The `repos` keys are labels for you. The GitHub repo of each checkout comes from its `.env` or its `origin` remote. All repos use your one `gh` login.
   - Do not set `FARM_GITHUB_*` variables in the shell that runs the coordinator. They would win over every project's `.env`, so the coordinator stops with a message.
   - `maxAgents` limits agents across all repos. `maxAgentsPerRepo` is optional (no limit when it is not set). Set it to 1 when a repo's agents would share local services, such as a database or a dev server port.
   - `maxAgentsByRole` is optional: the most agents at once in one role, so long Implement runs cannot take every slot. The caps are ceilings in the shared `maxAgents` pool, not reserved slots. `retro` defaults to 1 and only gets the slots that stage agents leave free.
   - `autoAdvanceMerged` (default `true`): when a person merges a PR, the coordinator moves its issue from `farm:merge-review` to `stage:staging`. Set it to `false` to do that by hand.
   - When issues wait for a slot and an agent ends, the coordinator runs a full early turn, so the waiting issues start in priority order. Early turns run at most once a minute. After a GitHub error, an early turn tries again after 120, 240, then 480 seconds.
2. Each agent runs in its issue's own worktree, `<repo folder>-farm/<number>` next to the checkout. The coordinator fetches, makes or refreshes the worktree at the latest default branch, and copies the project's env files into it, so tests can run there. Your main checkout can be on any branch, with local changes.
   - Env files: `FARM_WORKTREE_ENV_FILES` in the project's `.env`, comma-separated paths from the repo root. Default: `.env`. Only files that git ignores are copied, and a file that is already in the worktree is never overwritten.
   - For a manual run, `python3 ~/.software-farm/farm.py worktree <number>` does the same setup and prints the path.
3. See what it would start without starting anything:

   ```sh
   python3 ~/.software-farm/farm.py run --once --dry-run
   ```

4. Run it in its own terminal, and stop it with Ctrl-C:

   ```sh
   python3 ~/.software-farm/farm.py run
   ```

   `--loop-count N` stops after N turns, `--loop-interval S` sets the seconds between turns, `--once` is one turn, `--delay S` waits before the first turn. While it waits, press `r` to run a turn now.
5. Between turns it checks its agents every 15 seconds. When one ends, it reads only that issue and starts its next agent at once, within the same limits. The log shows `Finished: ...` and then `Started: ...` or `Not starting ...: <reason>`.
6. `python3 ~/.software-farm/farm.py status` lists the agents it started. `python3 ~/.software-farm/farm.py stop` interrupts them. Logs are in `~/.software-farm/coordinator/logs/`.

## farm.py for scripts and agents

`list`, `show` and `setup-repo` take `--json`. They then print one JSON document on stdout and nothing else. Each document has `"version": "0.1"`, so a reader can see when a shape changes.

`claim` and `release` print JSON already, without a version field.

Errors print one line on stderr, and the exit code tells the failure type:

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Other failure. `setup-repo --check` also exits 1 when it finds missing labels |
| 2 | Bad usage: wrong command or arguments |
| 3 | Configuration missing: the repo, the `gh` login, or the session id |
| 4 | Not available: issue not found, no `farm:` label, not assigned to you, or not ready for this role |
| 5 | Claim conflict: another session claimed the issue, or a person changed it. Stop work |
| 6 | GitHub API error (gh failed, rate limit, no network) |

`farm.py --help` shows the same table.

## Versions and updates

The factory version is in `VERSION` (SemVer). Each release has a git tag, `vX.Y.Z`. Use a release tag for real work. `main` is for factory development: the skills are symlinks into this checkout, so a branch checked out here changes how agents behave.

- `python3 ~/.software-farm/farm.py version` prints the version and the commit.
- Every agent comment shows the version in its header, and every run record has `farmVersion`.
- `claim` checks that the repo has every farm label before it writes. If not, it stops and names them: run `farm.py setup-repo`.
- `python3 ~/.software-farm/farm.py update` fetches the tags and moves your clone to the newest release tag. If the release adds a skill, run `scripts/install.sh` again, and restart the coordinator.

`update` refuses, and says why, when the clone is on a branch, HEAD is not at a release tag, the clone has local changes, or the coordinator has agents running.

**Make a release** (a person, not an agent): in a PR, change `VERSION`, merge it, then tag the merge commit and push the tag:

```sh
git tag vX.Y.Z <merge commit>
git push origin vX.Y.Z
```

## Tests

```sh
python3 -m unittest discover -s farm/tests -t . -v
python3 skills/farm-board/tests/test_run_usage.py
```

The tests use an in-memory fake of GitHub (`farm/tests/fakes.py`) and temporary git repos. They never call GitHub.
