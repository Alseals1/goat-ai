# Ideas and open questions

Captured 2026-11-02 from the owner. Nothing here is planned or built yet. Idea 1 is the owner's first priority; the others are not ranked.

## Ideas

### 1. Label Doctor (priority 1)

**Why.** Operating the factory means editing labels by hand, and it is easy to get them slightly wrong. The first hands-on release made this clear. Two mistakes happened in a row, and both times the factory did nothing and said nothing:
- A task was moved to `stage:intake` and assigned, but `goat:ready` was never added. The factory only looks at issues that have a `goat:` label, so it ignored the task.
- At an approval, `goat:approved` was added but the wrong label was removed: `stage:intake` disappeared and `goat:tier-review` stayed. The issue then had two `goat:` labels and no stage, so the factory could not tell where it was.

A silent no-op is the worst outcome for a beginner, because nothing tells them what to fix.

**The idea.** A checker that finds half-released or inconsistent issues, explains the problem in plain words, and says the exact fix.

**Problems it should catch.**

| Problem | What the person sees today |
| --- | --- |
| A stage past the backlog and an assignee, but no `goat:` label | Nothing happens |
| No stage label, or more than one stage label | Nothing happens |
| More than one `goat:` label (for example `approved` and `tier-review`) | Nothing happens, or the wrong one wins |
| `goat:approved` or `goat:ready` on a stage that has no agent after it (for example `stage:review` with `approved`) | Nothing happens |
| `goat:working` with no live claim for a long time | The issue looks busy but nothing runs |
| A `goat:` label on an issue assigned to someone else, or to nobody | The factory ignores it |
| A closed issue that still has `goat:working` or a gate label | Clutter, and a wrong status in lists |

**How it could work.**
- `goat.py doctor` reads the issues and prints each problem with the exact fix ("add the label `goat:ready`", "remove `goat:tier-review`").
- `goat.py list --problems` shows only the issues that need attention.
- The coordinator runs the same check each turn and, once per problem, comments on the issue and sends a desktop notification, so a mistake is caught within one turn instead of going unnoticed.
- The setup check (`setup-repo --check`) could include it.

**Safety rules for any automatic fixing.**
- Report first. Fix nothing unless `--fix` is given, and ask before each change.
- Fix only unambiguous structural slips, such as a duplicate stage label that is clearly stale.
- Never turn a missing approval into an approval, and never change a gate decision. When two `goat:` labels disagree, ask the person which one they meant.

**How to know it works.** Replaying the two mistakes above, the doctor names each one within one coordinator turn and gives the right fix.

**Related.** This is the "label drift" item from the self-healing list in the 2026-11-02 planning notes.

### 2. Cloud hosting and a dashboard

**Why.** The coordinator runs on one laptop. When the laptop sleeps or the terminal closes, work stops, and only the owner can see what is going on.

**The idea.** Run the coordinator on an always-on cloud host, and add a web dashboard that shows every issue by stage, what waits on a person, recent agent runs with their cost, and whether the coordinator is healthy.

**What it would take.**
- Today the coordinator keeps run records, attempt counts, retro notes and claim records in local files. A server needs those in shared storage or a small database.
- The server needs `gh` and Claude credentials, kept as secrets. Agents must run isolated from each other and from those secrets, with the same deny rules as today.
- The dashboard can start read-only: it only needs GitHub (labels and comments) plus the run records.
- A cheap first step: a command that writes a static status page from the same data, before any hosting.

### 3. Named agents, so it feels like a real software team

**Why.** "The Plan agent" is easy to lose track of. A name and a short personality make a long thread easier to follow.

**The idea.** Give each role a name (for example Intake, Plan, Implement and Review each get a first name) and sign their comments, notifications and dashboard rows with it.

**Notes.**
- Keep it cosmetic and configurable (for example an `agents` section in `goat-config.json`), so no logic depends on a name.
- Always show the role next to the name ("Mia, Plan agent") so nobody mistakes an AI for a person.

### 4. Messaging: Slack, GroupMe, text messages, or another platform

**Why.** A desktop notification only reaches the laptop it runs on. A person should hear about a gate, a question, a stalled issue or a dead coordinator wherever they are.

**Options to compare.**

| Channel | Good for | Watch out for |
| --- | --- | --- |
| Slack | Team use, buttons for approve and send back | Needs an app and workspace permissions |
| GroupMe | A simple group chat with a bot | Bot API is basic, mobile-first |
| Text messages (for example through Twilio) | Reaching one person anywhere | Costs money, needs a phone number and consent rules |
| Email or push (ntfy, Pushover) | Cheap and simple | Easy to ignore |

**Design notes.**
- Add a notification channel interface. The existing desktop notification (`goat/notify.py`) becomes the first implementation.
- Send only an issue number, title and link. Never code, logs or secrets.
- Start outbound only. Replying "approve" to change a label is a sensitive action: accept it only from an allow-listed sender, and only for gates, never for the merge (which stays on GitHub).
- Add throttling, quiet hours and an optional daily digest, so it does not become noise.

## Open questions

### 5. Should the agents create the tasks, or the factory?

**Current thinking.**
- People own the "what" and the "why". The factory runs the work. Tasks live in each project's own repo.
- Agents may **propose** tasks into the backlog (`stage:discovery`), never into the working pipeline. The aggregate retro already does this for the factory's own improvements, and an XL split already proposes sub-issues for a person to create.
- A possible addition: a per-project backlog agent that turns a PRD or roadmap into draft backlog issues for the owner to promote.
- Still to decide: whether approved XL splits may create their sub-issues automatically. The safe default is no.

### 6. A PRD for the fitness app, or Matt Pocock's Wayfinder skill?

**What Wayfinder is.** According to secondary articles (not checked against its repository), Wayfinder is a skill in `mattpocock/skills` that plans work too big or uncertain for one session. It asks questions to name the destination, then creates a "map" issue labeled `wayfinder:map` with child issues, each one question sized for one session (research, prototype, grilling or task). It works on GitHub Issues and does not write code. Source: https://pasqualepillitteri.it/en/news/12137/wayfinder-claude-code-skill-plan-big-projects

**Current thinking.** They solve different problems, so use both at different times:
- **A short PRD** (what, who, why, what success looks like, what we will not do) in the app repo, for example `docs/prd.md`, referenced from the app's `CLAUDE.md`. It gives every agent the product intent.
- **Wayfinder** (or something like it) before a large or unclear area, such as the listings data model, search or payments. It produces decisions and tasks; a person then promotes those tasks into G.O.A.T.
- **No collision expected:** G.O.A.T. only acts on issues that carry a `goat:` label, so Wayfinder's map and tickets are ignored as long as they never get that label.
- **Next step:** try Wayfinder on one real large feature before adopting it.
