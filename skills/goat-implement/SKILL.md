---
name: goat-implement
description: G.O.A.T. AI Implement agent. Builds an approved plan on its own branch and worktree, runs the tests, and opens a draft PR (or a PR stack, when the plan has one). Use when the developer runs /goat-implement <issue number> in the project repo.
argument-hint: "<issue number>"
---

# G.O.A.T. AI: Implement agent

Issue number: $ARGUMENTS

Your stage is `implement`. You turn the newest "Spec and plan" in the issue's Doc into working code on a branch dedicated to this issue, then open a draft PR. You never merge.

**Choosing the model.** This skill sets no model, because the right choice follows the size. The developer picks it before starting: `/model sonnet` for S, `/model opus` for M and L. Once you have read the issue, check the size. If it is M or L and this session is not on Opus, say so and ask whether to carry on. In a headless run (protocol section 13) the coordinator already picked the model, so do not ask.

Read [the board protocol](../goat-board/protocol.md) first and follow it for all board actions, which go through `python3 ~/.goat-ai/goat.py`. Its safety rules (section 11) apply here with full force.

## Procedure

### 1. Start up

Follow protocol sections 1, 3 and 4 with the role `implement`. Read the newest "Spec and plan" in the Doc carefully.

### 2. Is this rework?

`goat.py show` prints a PR link when one exists. A PR link means the issue came back after review or merge review with changes requested. Read the newest "Review notes" in the Doc and the latest change requests in the comments, then fix exactly what they ask and nothing else.

### 3. Get onto the right branch and worktree

Each issue has one worktree, at `../<repo name>-goat/<issue number>`, beside the main checkout.

Branch names:
- One PR: `goat/<issue number>-<short slug of the title>`, for example `goat/14-login-timeout`.
- A PR stack: one branch per part, `goat/<issue number>-<n>-<part slug>`, as listed in the plan's "PR plan" (protocol section 15).

Where you start decides what to do:

| You start in | New work | Rework |
| --- | --- | --- |
| The issue's worktree (the coordinator launched you, and the current folder is `../<repo name>-goat/<issue number>`) | HEAD is detached at the latest `origin/<default branch>`. Create the branch here with `git switch -c <branch>`. | You are already on the item's branch. Keep using it. |
| The main checkout (a manual run) | Run `python3 ~/.goat-ai/goat.py worktree <issue number>`. It fetches, creates or refreshes the worktree, copies the project's env files, and prints the path on its last line. Then run `git -C <path> switch -c <branch>`. | Run the same `worktree` command. If the worktree is not on the branch yet, run `git -C <path> switch <branch>`. The branch is named on the `Where:` line of the last handoff. |

Then:
- Work only inside that worktree. It holds the project's env files (`GOAT_WORKTREE_ENV_FILES`, default `.env`), so tests can run. `goat.py` also works there, reading the main checkout's `.env`.
- Post a `checkpoint` naming the branch and the worktree path.

For a PR stack, create every part's branch before writing any code, instead of the single `git switch -c`:
- With `gh-stack`: run `git switch -c <branch 1>` from the detached HEAD, then `gh stack init <branch 1> <branch 2> ...` listing the branches bottom to top. (`init` adopts branch 1, so the stack begins at the latest default branch.)
- Without it: run `git switch -c` for each part, each one created from the part below it.
- For rework, move to the part the change request concerns, using `gh stack checkout <branch>` or `git switch <branch>`.

### 4. Build

Work through the plan steps in order, honoring the conventions in `CLAUDE.md`. Make every code change yourself in this session, never in a subagent. A Sonnet `Explore` subagent is fine for finding code the plan does not mention, such as other callers of a function you are changing.

Departing from the plan:
- Small deviation: allowed. Record what and why in a `checkpoint`.
- Big deviation (another approach, wider scope, a new always-gate area): needs a human. Ask a `question` (protocol section 7).

Other rules:
- Use only local services, with seed or sample data.
- Stack only: commit each change to the part that owns it. Never put a lower part's change on a higher part. With `gh-stack`, check out the part, commit, run `gh stack rebase --upstack`, then `gh stack top`. Without it, commit on that part and then merge it into every part above, in order.

### 5. Test

Run every test, type check and linter that the plan or the repo calls for, and fix what fails. If a failure is not from your change, say so in a checkpoint and include the output.

When the output is long, hand the commands to a Haiku subagent that returns only the failures, each with file, line and message (protocol section 10). You still fix them yourself.

### 6. Commit

Keep commits small and clear. Put unrelated changes in separate commits.

### 7. Catch up with the default branch

Do this before every push, for new work and for rework alike. The default branch may have moved, and Review needs a branch that is current. For a single PR, use merge only: never rebase, never force-push (protocol section 11). Stacks are covered at the end of this step.

1. Run `git fetch origin`. The default branch is the output of `git symbolic-ref --short refs/remotes/origin/HEAD` with the `origin/` prefix removed, or `main` if that command fails.
2. Run `git merge --no-edit origin/<default branch>`. If it says "Already up to date", skip to section 8.
3. If there are conflicts, settle one yourself only when the right answer is obvious:
   - Both sides changed different things and both belong (for instance, each added lines nearby): keep both.
   - One side merely moved, renamed or reformatted code: carry the other side's change into the new form.
   - A generated file or lock file: regenerate it with the repo's own tool. Never hand-edit it.
4. When every conflict is settled, run `git add` on the files, then `git commit --no-edit` to complete the merge. The `--no-edit` matters in a headless run, where no editor can open.
5. **Stop and ask** if the answer is not obvious. Examples: both sides changed the same logic in different ways, the default branch deleted or reworked code the plan relies on, or the outcome hinges on a product decision. In that case:
   - run `git merge --abort`;
   - post a `question` (protocol section 7) listing the conflicting files, what each side did, and the options with your recommendation;
   - release with `--stage implement --goat-status answer-question`;
   - do not push.
6. If the merge pulled in commits, run section 5 (Test) again and fix any failures the merge caused. If the default branch fails the tests by itself, say so in a `checkpoint` with the output.
7. Post a `checkpoint`: either "already up to date" or the number of commits merged in, plus how each conflict was resolved.

For a PR stack, follow "Catch up with the default branch" in protocol section 15.
- With `gh-stack`, `gh stack sync` takes the place of the fetch and merge. Settle its conflicts by the rules above using `gh stack rebase` and `gh stack rebase --continue`. If the answer is unclear, run `gh stack rebase --abort`, then stop and ask.
- Without `gh-stack`, merge the default branch into the first branch, then merge each branch into the one above it, in stack order.

### 8. Push and open the PR

New work, single PR:
1. `git push -u origin <branch>`
2. `gh pr create --draft`, titled from the issue. The body must contain:
   - the line `Refs #<issue number>` (never `Closes`, `Fixes` or `Resolves`, since a person closes the issue once it is finished);
   - a summary;
   - how it was tested;
   - the always-gate areas it touches, taken from the plan's gate hits;
   - any conflicts you resolved during the catch-up.

New work, PR stack: one draft PR per part, bottom to top, each based on the part below it (protocol section 15).
- With `gh-stack`: run `gh stack submit --auto` instead of the push and `gh pr create`. Then, for each PR, run `gh pr edit <number> --title <title> --body-file -` with the body described above plus "Part <n> of <m>".
- Without it: for each part, `git push -u origin <branch>` and `gh pr create --draft --base <branch below>`.

Rework: push to the same branch, then run `gh pr comment` describing what changed and any conflicts resolved in the catch-up. For a stack, use `gh stack push` (or `git push` on each changed branch when `gh-stack` is not in use), and comment on every PR that changed.

### 9. Release (protocol section 8)

- No Doc section.
- Short summary: what was built, and the PR link.
- Flags: `--stage review --goat-status ready --pr <first PR url>`. For a stack, that is the bottom PR.
- The `Where:` line of the handoff must give the branch (every branch of a stack, bottom to top), the worktree path, every PR, and the test commands.

### 10. Report

Give the developer the PR link and the next command, `/goat-review <issue number>`, to be run in a NEW session so the review stays independent. In a headless run the coordinator starts the Review agent in a fresh session once this one ends, so say that instead of giving the command.
