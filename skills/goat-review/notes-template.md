# Review notes template

This is the layout for everything the Review agent writes up: the Doc section, the `note` comment on the issue, and the review comment on each PR. Fill in the blanks. Do not invent extra sections.

## Layout

The order is fixed. In round 1, drop the "Since round" section.

````markdown
**<Ready for merge review | Changes needed>** · round <n> · <a> must fix, <b> suggestions · tests: <result> · CI: <result>

## Since round <n - 1>

- F2 fixed
- F3 not fixed: <reason, one line>

## Acceptance criteria

<X> of <Y> pass

- pass: <short label> (<proof: a test name or a file>)
- fail: <short label> (see F4)

## Findings

- **F4 must fix** `path/file.py:42`. With <input or state>, the result is <wrong outcome>. Do: <the change to make>.
- **F5 suggestion** `path/other.py:10`. Missed implication: <what the spec implies>, but <what the code leaves out>. Do: <the change to make>.

## How to verify

1. <step>
2. <step>

```sh
<commands>
```

## Also checked

- Plan: <matches the plan, or the checkpoint that explains the difference>
- Scope: <nothing extra, or what was added and why it is safe>
- Safety: <no secrets, no remote changes, no missing gate hits>
````

## Field rules

- **Headline.** Write `Ready for merge review` if no `must fix` is open. Otherwise write `Changes needed`. Tests read `N pass`, `N pass, M fail`, or `not run (<why>)`. CI reads `pass`, `fail (<check names>)`, `pending` or `none`.
- **Since round N.** List every finding that was open last round, one line each: `fixed`, or `not fixed` plus the reason. A fixed finding appears only here and leaves the Findings list.
- **Acceptance criteria.** One line per criterion. The label is 3 to 8 words that name the criterion without repeating the spec. The proof is a test name or a file. A failing criterion points at its finding ID.
- **Findings.** A single list with every `must fix` before any suggestion. Each entry carries the ID, the severity, `file:line`, the failure scenario, and "Do:". Missed implications and scope creep follow the same pattern, with their kind named first. If there are no findings, write "None."
- **How to verify.** The fewest steps a person at merge review needs to watch the change work.
- **Also checked.** One short line per item. A problem found here is also a finding. If the plan or a handoff leaves the Review agent an open question, answer it with one more line here.
- **Plain Markdown only.** Use headings, `-` and numbered lists, bold, links and fenced code. No tables and no `[x]` checkboxes, so the text looks identical in the Doc, on the issue and on the PR.
- **Leave out praise and diff recaps.** The PR already has its own description.

## Stack form, one comment per PR

When the work is a stack, each PR gets its own comment holding:

- The issue-wide headline.
- `## Acceptance criteria`, limited to the criteria that PR covers (per the plan's PR plan), with the count for that PR.
- `## Findings`, limited to findings for that PR. A finding belongs to the PR whose diff contains its `file:line`. If it crosses PRs, put it on the earliest PR containing the cause.
- `## How to verify`, on the top PR only.
- A closing line: `Full notes for all PRs: <issue link>`.

A lone PR gets the full notes.

## Worked example

Round 2 of an invented change that adds a `--json` flag to a `list` command.

````markdown
**Changes needed** · round 2 · 1 must fix, 1 suggestion · tests: 112 pass · CI: pass

## Since round 1

- F1 fixed
- F2 not fixed: an empty list still prints a header line

## Acceptance criteria

3 of 4 pass

- pass: `--json` prints a JSON array (`test_list_json`)
- pass: each entry has id, title, status (`test_list_json_fields`)
- fail: an empty list prints `[]` (see F2)
- pass: README documents the flag (`README.md`)

## Findings

- **F2 must fix** `goat/cli.py:88`. With a repo that has no goat issues, `list --json` prints `Items:` and then `[]`, which is not valid JSON. Do: return before the header whenever `--json` is set.
- **F3 suggestion** `goat/cli.py:95`. Missed implication: the spec says "every list command", yet `list --mine --json` bypasses the new path. Do: test `--json` after the `--mine` filter runs.

## How to verify

1. Run the tests.
2. Run `list --json` in a repo with no goat issues and confirm it prints `[]`.

```sh
python3 -m unittest discover -s goat/tests -t . -v
python3 goat.py list --json | python3 -m json.tool
```

## Also checked

- Plan: matches the plan
- Scope: nothing extra
- Safety: no secrets, remote changes or gate hits
````
