# Review notes template

The shape of the Review agent's notes. Use it for the Doc section, the issue `note` comment, and the PR review on GitHub. Fill it in; do not add sections.

## Skeleton

Sections in this order. Leave out "Since round N" in round 1.

````markdown
**<Ready for merge review | Changes needed>** · round <n> · <a> must fix, <b> suggestions · tests: <result> · CI: <result>

## Since round <n - 1>

- F2 fixed
- F3 not fixed: <why, one line>

## Acceptance criteria

<X> of <Y> pass

- pass: <short label> (<evidence: test name or file>)
- fail: <short label> (see F4)

## Findings

- **F4 must fix** `path/file.py:42`. Given <input or state>, you get <wrong result>. Do: <what to change>.
- **F5 suggestion** `path/other.py:10`. Missed implication: <what the spec implies>, but <what the code skips>. Do: <what to change>.

## How to verify

1. <step>
2. <step>

```sh
<commands>
```

## Also checked

- Plan: <follows the plan, or which checkpoint explains the change>
- Scope: <no extra changes, or which ones and why they are safe>
- Safety: <no secrets, remote changes or missing gate hits>
````

## Rules

- **Verdict line.** `Ready for merge review` when there is no open `must fix`, otherwise `Changes needed`. The test result is `N pass`, `N pass, M fail`, or `not run (<why>)`. The CI result is `pass`, `fail (<check names>)`, `pending` or `none`.
- **Since round N.** One line for each finding that was open in the last round: `fixed`, or `not fixed` with why. Fixed findings leave the Findings list and show only here.
- **Acceptance criteria.** One line each. The label has 3 to 8 words and names the criterion; it does not restate the spec. The evidence is a test name or a file. A failed criterion points to its finding ID.
- **Findings.** One list, `must fix` first. Each has its ID, severity, `file:line`, "Given X, you get Y", and "Do:". Missed implications and scope creep use the same shape, with the kind at the start of the text. Write "None." when there are no findings.
- **How to verify.** The shortest steps a person at Merge review can follow to see the change work.
- **Also checked.** One short line each. A problem here is also a finding. If the plan or a handoff asks the Review agent an open question, answer it here in one more line.
- **Plain Markdown.** Headings, `-` and numbered lists, bold, links and fenced code only. No tables, no `[x]` checkboxes, so the notes read the same in the Doc, in the issue and on the PR.
- **No praise, no summary of the diff.** The PR has its own summary.

## Per-PR form for a stack

Each PR in a stack gets its own review comment:

- The issue's verdict line.
- `## Acceptance criteria` with only the criteria that PR covers (from the plan's PR plan). The count is for that PR.
- `## Findings` with only that PR's findings. A finding goes on the PR whose diff has its `file:line`. A finding that spans PRs goes on the earliest PR that has the cause.
- `## How to verify` on the top PR only.
- A last line: `Full notes for all PRs: <issue link>`.

A single PR gets the full notes.

## Example

Round 2 of a made-up change that adds a `--json` flag to a `list` command.

````markdown
**Changes needed** · round 2 · 1 must fix, 1 suggestion · tests: 112 pass · CI: pass

## Since round 1

- F1 fixed
- F2 not fixed: the empty list still prints a header line

## Acceptance criteria

3 of 4 pass

- pass: `--json` prints a JSON array (`test_list_json`)
- pass: each entry has id, title, status (`test_list_json_fields`)
- fail: an empty list prints `[]` (see F2)
- pass: README documents the flag (`README.md`)

## Findings

- **F2 must fix** `farm/cli.py:88`. Given a repo with no farm issues, `list --json` prints `Items:` and then `[]`, so the output is not valid JSON. Do: return before the header when `--json` is set.
- **F3 suggestion** `farm/cli.py:95`. Missed implication: the spec says "every list command", but `list --mine --json` skips the new path. Do: check `--json` after the `--mine` filter.

## How to verify

1. Run the tests.
2. Run `list --json` with no farm issues and check that the output is `[]`.

```sh
python3 -m unittest discover -s farm/tests -t . -v
python3 farm.py list --json | python3 -m json.tool
```

## Also checked

- Plan: follows the plan
- Scope: no extra changes
- Safety: no secrets, remote changes or gate hits
````
