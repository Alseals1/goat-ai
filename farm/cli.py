"""farm.py command line. Agents and people use it for every GitHub read and write of the farm.

Run it in the project repo (the app checkout). The GitHub repo that holds the issues is FARM_GITHUB_REPO in the
checkout's .env, or its origin remote (see farm/config.py). GitHub access is the `gh` login: run `gh auth login`.
Write commands also need the session id (CLAUDE_CODE_SESSION_ID, or --session). `add` does not: it makes a new
issue and claims nothing.
`chat` is different: its --session picks an older run of the item to resume.
Text inputs (posts, doc sections, handoffs, summaries) are Markdown files, so no shell quoting is needed.

`run`, `status` and `stop` are the coordinator (farm/coordinator.py). They work from any folder and do not
read the current folder's .env: `run` reads ~/.software-farm/config.json and each configured repo's .env.

`guide` prints farm/guide.md, the guide for any agent. It needs no .env. `retro-pending` and `retro-aggregate-save`
read and write only local files in ~/.software-farm/retros, and need no .env either.
`version` and `update` also work from any folder and do not read .env. They use the farm's own clone.
"""

import argparse
import json
import os
import sys

from farm import board as fb
from farm import chat, coordinator, exit_codes, retro_facts, retros, setup, views, views_json
from farm.actions import POST_KINDS, Farm, FarmError, refuse_ready_when_headless, utc_now
from farm.cleanup import clean_up
from farm.config import REPO_ROOT, ConfigError, load_board, load_shared, main_checkout, project_env
from farm.github import GitHubClient, GitHubError
from farm.update import UpdateError, update
from farm.version import version_label, version_lines
from farm.worktrees import WorktreeError, copy_env_files, env_file_paths, fetch, item_ref, prepare

GUIDE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guide.md")
FARM = "python3 ~/.software-farm/farm.py"


def read_file(path):
    if path == "-":
        return sys.stdin.read()
    with open(path) as f:
        return f.read()


def read_description(args):
    """The add command's description: --file, else stdin when it is piped, else None."""
    if args.file:
        return read_file(args.file)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return None


def make_farm(args):
    env = project_env()
    board = load_board(env)
    client = GitHubClient(board["repo"])
    session = getattr(args, "session", None) or env.get("CLAUDE_CODE_SESSION_ID")
    return Farm(client, load_shared(), board, client.me(), session, version=version_label())


def print_json(data):
    print(json.dumps(data, indent=2))


def positive_int(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number, 1 or more")
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def non_negative_int(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number, 0 or more")
    if value < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return value


# ---------- Commands ----------

def command_list(args):
    farm = make_farm(args)
    user_id = farm.user["id"] if args.mine else None
    items = views.farm_items(farm.client, farm.board, user_id)
    if args.json:
        print_json(views_json.list_data(items, utc_now()))
        return
    if not items:
        print("No farm items.")
    for line in views.list_lines(items, utc_now()):
        print(line)


def command_show(args):
    farm = make_farm(args)
    item, _ = farm.load(args.item)
    if args.json:
        print_json(views_json.show_data(item, utc_now()))
    else:
        print(views.show_markdown(item, utc_now()))


def command_setup_repo(args):
    """Creates the farm's labels (and a milestone) in the project's repo. With --check it only reports."""
    board = load_board(project_env())
    client = GitHubClient(board["repo"])
    lines, problems = setup.setup_repo(client, board["repo"], check_only=args.check, milestone=args.milestone)
    if args.json:
        print_json(views_json.setup_data(board["repo"], lines, problems))
    else:
        print("\n".join(lines))
        if problems:
            print("\nProblems:\n" + "\n".join("- " + problem for problem in problems))
    return 1 if problems else 0


def command_add(args):
    # Before any GitHub call, so a headless agent gets no further.
    refuse_ready_when_headless(args.ready, os.environ)
    item, notes = make_farm(args).add(args.title, read_description(args), args.ready, args.milestone, args.priority)
    for note in notes:
        print(note)
    print("Added #{}: {}".format(item.id, item.url))


def command_claim(args):
    result = make_farm(args).claim(args.item, args.role)
    print(json.dumps(result, indent=2))


def command_renew(args):
    print("Claim renewed until {}.".format(make_farm(args).renew(args.item)))


def command_post(args):
    make_farm(args).post(args.item, args.kind, read_file(args.file))
    print("Posted {}.".format(args.kind))


def command_doc(args):
    make_farm(args).add_doc_section(args.item, args.title, read_file(args.file))
    print("Doc section {!r} saved.".format(args.title))


def command_summary(args):
    make_farm(args).set_summary(args.item, read_file(args.file).strip())
    print("Farm summary set.")


def command_chat(args):
    chat.chat(make_farm(args).resolve_item_id(args.item), session=args.session)


def command_version(args):
    for line in version_lines():
        print(line)


def command_update(args):
    """Moves this farm clone to the newest release tag. Refuses while the coordinator has agents running."""
    state = coordinator.load_state()
    coordinator.prune_finished(state)
    for line in update(REPO_ROOT, len(state["runs"])):
        print(line)


def command_rules(args):
    print(json.dumps(load_shared(), indent=2))


def command_guide(args):
    with open(GUIDE_PATH) as f:
        print(f.read(), end="")


def command_cleanup(args):
    farm = make_farm(args)
    items = views.all_items(farm.client, farm.board)
    lines = clean_up(main_checkout(os.getcwd()), farm.client, farm.board, items, farm.user["id"], utc_now(),
                     dry_run=args.dry_run)
    if not lines:
        print("Nothing to clean up.")
    for line in lines:
        print(line)


def command_retro_facts(args):
    """The item's local run records, coordinator logs and session digests. Reads GitHub only to find the issue."""
    farm = make_farm(args)
    item, _ = farm.load(args.item)
    # The folders are read here, not as default arguments, so tests can patch them.
    facts = retro_facts.collect(item.id, retro_facts.RUNS_FILE, retro_facts.LOG_DIR, retro_facts.PROJECTS_DIR)
    if args.json:
        print_json(facts)
    else:
        print(retro_facts.facts_markdown(facts, "#{} {}".format(item.id, item.name)), end="")


def command_retro_save(args):
    """Saves the retro note of a closed issue that this user owns. Writes a local file only, nothing to GitHub."""
    farm = make_farm(args)
    item, board = farm.load(args.item)
    retros.check_retro_item(item, farm.user["id"], board["doneLabel"])
    path = retros.save_note(item, read_file(args.file), utc_now(), retros.RETROS_DIR)
    print("Saved retro note {}.".format(path))


def command_retro_pending(args):
    """The retro notes that no aggregate has read, the last aggregate time, and the reports. Local files only."""
    data = {
        "notes": retros.pending_notes(retros.RETROS_DIR),
        "lastAggregateAt": retros.read_state(retros.RETROS_DIR)["lastAggregateAt"],
        "reports": retros.aggregate_reports(retros.RETROS_DIR),
    }
    if args.json:
        print_json(data)
        return
    print("Last aggregate: {}".format(data["lastAggregateAt"] or "never"))
    print("Waiting notes: {}".format(len(data["notes"])))
    for note in data["notes"]:
        print("  {id}  #{issue}  saved {savedAt}  {path}".format(**note))
    print("Reports (newest first): {}".format(len(data["reports"])))
    for path in data["reports"]:
        print("  " + path)


def command_retro_aggregate_save(args):
    """Saves an aggregate report and marks its notes as read. Local files only."""
    note_ids = [note.strip() for note in args.notes.split(",") if note.strip()]
    path = retros.save_aggregate(read_file(args.file), note_ids, utc_now(), retros.RETROS_DIR)
    print("Saved aggregate report {}. Marked {} note(s) as read.".format(path, len(note_ids)))


def command_worktree(args):
    """Makes or refreshes the item's worktree, as the coordinator does. Local git only: writes nothing to GitHub."""
    farm = make_farm(args)
    item, _ = farm.load(args.item)
    repo = main_checkout(os.getcwd())
    paths = env_file_paths(project_env())
    fetch(repo)
    path = prepare(repo, item_ref(item))
    for line in copy_env_files(repo, path, paths):
        print(line)
    # The path is last, so a script can read it with `tail -n 1`.
    print(path)


def command_release(args):
    run = make_farm(args).release(
        args.item,
        stage=args.stage,
        farm_status=args.farm_status,
        handoff=read_file(args.file),
        summary=read_file(args.summary) if args.summary else None,
        size=args.size,
        pr_url=args.pr,
    )
    print(json.dumps(run, indent=2))


def heredoc(command):
    """An example that sends Markdown on stdin, as the role skills do."""
    return "{} {} --file - <<'EOF'\n  ...\n  EOF".format(FARM, command)


def build_parser():
    parser = argparse.ArgumentParser(prog="farm.py", description="Software Farm on GitHub Issues",
                                     epilog="Agents: run 'farm.py guide' first.\n\n" + exit_codes.HELP_TEXT,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name, handler, help_text, example, item=True, session=False):
        """Adds a command. `example` is required, so every command's --help shows one."""
        command = commands.add_parser(name, help=help_text, epilog="Example:\n  " + example,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
        if item:
            command.add_argument("item", help="issue number (for example 14 or #14), or an issue link")
        if session:
            command.add_argument("--session", help="session id (default: $CLAUDE_CODE_SESSION_ID)")
        command.set_defaults(handler=handler)
        return command

    json_help = "print one JSON document instead of text"
    list_command = add("list", command_list, "farm issues of the project's repo", FARM + " list --mine --json",
                       item=False)
    list_command.add_argument("--mine", action="store_true", help="only issues assigned to you")
    list_command.add_argument("--json", action="store_true", help=json_help)
    add("show", command_show, "one issue as Markdown: labels, description, doc, comments",
        FARM + " show 14").add_argument("--json", action="store_true", help=json_help)
    setup_repo = add("setup-repo", command_setup_repo, "create the farm's labels in the project's repo (safe to repeat)",
                     FARM + " setup-repo --milestone 'v0.1'", item=False)
    setup_repo.add_argument("--check", action="store_true", help="change nothing; report the missing labels")
    setup_repo.add_argument("--milestone", help="also create this milestone if it does not exist")
    setup_repo.add_argument("--json", action="store_true", help=json_help)

    add_item = add("add", command_add,
                   "create an issue in the project's repo (stage:discovery, or the farm's intake with --ready)",
                   heredoc('add "Fix the login timeout"'), item=False)
    add_item.add_argument("title", help="the issue's title")
    add_item.add_argument("--file", help="Markdown description file, or - for stdin (default: stdin when piped)")
    add_item.add_argument("--milestone", help="milestone title (it must exist: see setup-repo --milestone)")
    add_item.add_argument("--priority", choices=fb.PRIORITY_ORDER, help="priority label")
    add_item.add_argument("--ready", action="store_true",
                          help="stage:intake, farm:ready, assigned to you: the coordinator starts it. "
                               "Not for agents.")

    add("claim", command_claim, "claim an item for a role", FARM + " claim 14 --role plan",
        session=True).add_argument("--role", required=True, choices=("intake", "plan", "implement", "review"))
    add("renew", command_renew, "move this session's claim expiry forward", FARM + " renew 14", session=True)

    post = add("post", command_post, "post a Markdown file as an issue comment", heredoc("post 14 --kind note"),
               session=True)
    post.add_argument("--kind", required=True, choices=POST_KINDS)
    post.add_argument("--file", required=True, help="Markdown file, or - for stdin")

    doc = add("doc", command_doc, "add a Markdown section to the issue's doc",
              heredoc("doc 14 --title 'Spec and plan'"), session=True)
    doc.add_argument("--title", required=True, help="section heading, for example 'Spec and plan'")
    doc.add_argument("--file", required=True, help="Markdown file, or - for stdin")

    summary = add("summary", command_summary, "set the farm summary from a Markdown file (2,000 characters at most)",
                  heredoc("summary 14"), session=True)
    summary.add_argument("--file", required=True, help="Markdown file, or - for stdin")

    add("cleanup", command_cleanup, "remove local worktrees and branches of your closed issues, then set the label farm:done",
        FARM + " cleanup --dry-run", item=False).add_argument(
        "--dry-run", action="store_true", help="print what it would do; change nothing")

    run = add("run", coordinator.command_run,
              "run the coordinator: start agents for your ready items, on an interval",
              FARM + " run --loop-count 5 --loop-interval 300", item=False)
    run.add_argument("--once", action="store_true", help="run one turn, then exit (wins over --loop-count)")
    run.add_argument("--loop-count", type=positive_int, help="run this many turns, then exit (default: until Ctrl-C)")
    run.add_argument("--loop-interval", type=non_negative_int,
                     help="seconds between turns (default: intervalSeconds from the config)")
    run.add_argument("--delay", type=non_negative_int, default=0,
                     help="seconds to wait before the first turn (default: 0)")
    run.add_argument("--dry-run", action="store_true", help="show what would start; start nothing")
    add("status", coordinator.command_status, "list the agents the coordinator started", FARM + " status",
        item=False)
    add("stop", coordinator.command_stop, "interrupt the agents the coordinator started", FARM + " stop",
        item=False)

    add("chat", command_chat, "resume the issue's agent session in this terminal, for a live discussion",
        FARM + " chat 14").add_argument(
        "--session", help="session id of an older run of the item (default: the latest run)")
    add("worktree", command_worktree,
        "make or refresh the issue's worktree (detached at origin/<default branch>, with env files) and print its path",
        FARM + " worktree 14")

    add("retro-facts", command_retro_facts,
        "the issue's local run records, coordinator logs and session digests, for its retro",
        FARM + " retro-facts 14").add_argument("--json", action="store_true", help=json_help)
    add("retro-save", command_retro_save, "save the retro note of a closed issue (local file, not GitHub)",
        heredoc("retro-save 14")).add_argument("--file", required=True, help="Markdown file, or - for stdin")
    add("retro-pending", command_retro_pending,
        "retro notes that no aggregate has read, the last aggregate time, and the aggregate reports",
        FARM + " retro-pending --json", item=False).add_argument("--json", action="store_true", help=json_help)
    aggregate_save = add("retro-aggregate-save", command_retro_aggregate_save,
                         "save an aggregate retro report and mark its notes as read (local files, not GitHub)",
                         heredoc("retro-aggregate-save --notes me_app-11,me_app-12"), item=False)
    aggregate_save.add_argument("--notes", required=True, help="comma-separated note ids from retro-pending")
    aggregate_save.add_argument("--file", required=True, help="Markdown file, or - for stdin")

    add("version", command_version, "print the farm version and commit", FARM + " version", item=False)
    add("update", command_update, "move this farm install to the newest release tag", FARM + " update", item=False)
    add("rules", command_rules, "print the shared gates and leases (farm-config.json)", FARM + " rules", item=False)
    add("guide", command_guide, "print the agent guide: commands, stages, farm statuses, and what agents may run",
        FARM + " guide", item=False)

    release = add("release", command_release, "post the handoff and release the claim",
                  heredoc("release 14 --stage plan --farm-status plan-review"), session=True)
    release.add_argument("--stage", required=True, choices=("intake", "plan", "implement", "review"))
    release.add_argument("--farm-status", required=True, choices=fb.RELEASE_STATUSES)
    release.add_argument("--file", required=True, help="handoff note, Markdown file")
    release.add_argument("--summary", help="Farm summary, Markdown file (2,000 characters at most)")
    release.add_argument("--size", choices=("S", "M", "L", "XL"))
    release.add_argument("--pr", help="PR link, kept in the farm state comment")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args) or 0
    except (FarmError, GitHubError, ConfigError, WorktreeError, UpdateError, OSError) as error:
        print("farm.py: {}".format(error), file=sys.stderr)
        # OSError has no exit_code, so it is a general failure.
        return getattr(error, "exit_code", exit_codes.GENERAL)
