"""goat.py command line. Agents and people use it for every GitHub read and write of the goat.

Run it in the project repo (the app checkout). The GitHub repo that holds the issues is GOAT_GITHUB_REPO in the
checkout's .env, or its origin remote (see goat/config.py). GitHub access is the `gh` login: run `gh auth login`.
Write commands also need the session id (CLAUDE_CODE_SESSION_ID, or --session). `add` does not: it makes a new
issue and claims nothing.
`chat` is different: its --session picks an older run of the item to resume.
Text inputs (posts, doc sections, handoffs, summaries) are Markdown files, so no shell quoting is needed.

`run`, `status` and `stop` are the coordinator (goat/coordinator.py). They work from any folder and do not
read the current folder's .env: `run` reads ~/.goat-ai/config.json and each configured repo's .env.

`guide` prints goat/guide.md, the guide for any agent. It needs no .env. `retro-pending` and `retro-aggregate-save`
read and write only local files in ~/.goat-ai/retros, and need no .env either.
`version` and `update` also work from any folder and do not read .env. They use G.O.A.T.'s own clone.
"""

import argparse
import json
import os
import sys

from goat import board as fb
from goat import autopilot, chat, coordinator, exit_codes, health, retro_facts, retros, setup, team, views, views_json
from goat.actions import POST_KINDS, Goat, GoatError, refuse_ready_when_headless, utc_now
from goat.cleanup import clean_up
from goat.config import REPO_ROOT, ConfigError, load_board, load_shared, main_checkout, project_env
from goat.github import GitHubClient, GitHubError
from goat.discord import DiscordError
from goat import discord as discord_client
from goat.notify import notify
from goat.update import UpdateError, update
from goat.version import version_label, version_lines
from goat.worktrees import WorktreeError, copy_env_files, env_file_paths, fetch, item_ref, prepare

GUIDE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guide.md")
GOAT = "python3 ~/.goat-ai/goat.py"


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


def make_goat(args):
    env = project_env()
    board = load_board(env)
    client = GitHubClient(board["repo"])
    session = getattr(args, "session", None) or env.get("CLAUDE_CODE_SESSION_ID")
    return Goat(client, load_shared(), board, client.me(), session, version=version_label())


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
    goat = make_goat(args)
    user_id = goat.user["id"] if args.mine else None
    items = views.goat_items(goat.client, goat.board, user_id)
    if args.json:
        print_json(views_json.list_data(items, utc_now()))
        return
    if not items:
        print("No G.O.A.T. issues.")
    for line in views.list_lines(items, utc_now()):
        print(line)


def command_show(args):
    goat = make_goat(args)
    item, _ = goat.load(args.item)
    if args.json:
        print_json(views_json.show_data(item, utc_now()))
    else:
        print(views.show_markdown(item, utc_now()))


def command_setup_repo(args):
    """Creates G.O.A.T.'s labels (and a milestone) in the project's repo. With --check it only reports."""
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
    item, notes = make_goat(args).add(args.title, read_description(args), args.ready, args.milestone, args.priority)
    for note in notes:
        print(note)
    print("Added #{}: {}".format(item.id, item.url))


def command_claim(args):
    result = make_goat(args).claim(args.item, args.role)
    print(json.dumps(result, indent=2))


def command_renew(args):
    print("Claim renewed until {}.".format(make_goat(args).renew(args.item)))


def command_post(args):
    make_goat(args).post(args.item, args.kind, read_file(args.file))
    print("Posted {}.".format(args.kind))


def command_doc(args):
    make_goat(args).add_doc_section(args.item, args.title, read_file(args.file))
    print("Doc section {!r} saved.".format(args.title))


def command_summary(args):
    make_goat(args).set_summary(args.item, read_file(args.file).strip())
    print("Summary set.")


def command_chat(args):
    chat.chat(make_goat(args).resolve_item_id(args.item), session=args.session)


def command_health(args):
    """Is the coordinator running? Reads its heartbeat file. Exit code 0 when it is running, 1 when it is not.

    With --notify, a desktop notification is shown when it is stale or died without being stopped on purpose, so a
    scheduled `goat.py health --notify` can watch the coordinator.
    """
    data = health.read(health.HEARTBEAT_FILE)
    state, text = health.assess(data, health.utc_now())
    if args.json:
        print_json({"version": views_json.VERSION, "state": state, "running": state == health.RUNNING,
                    "message": text, "heartbeat": data})
    else:
        print(text)
    if args.notify and state in health.ALERT_STATES:
        who = team.dispatcher_name(load_shared())
        boss = team.read_owner(coordinator.CONFIG_FILE)["name"]
        notify("{}: the coordinator is not running".format(who), (boss + ", " if boss else "") + text[0].lower() + text[1:])
    return 0 if state == health.RUNNING else 1


def command_team(args):
    """Prints the team: each role's first name and voice, and the owner. Agents run it in setup to learn their voice."""
    shared = load_shared()
    local = {}
    try:
        with open(coordinator.CONFIG_FILE) as f:
            local = json.load(f)
    except (OSError, ValueError):
        pass
    if args.json:
        roles = [args.role] if args.role else ["intake", "plan", "implement", "review", "retro", "qa", team.DISPATCHER]
        print_json({"version": views_json.VERSION, "owner": team.owner(local),
                    "team": {role: team.persona(shared, role) for role in roles}})
        return
    for line in team.lines(shared, local, args.role):
        print(line)


def command_discord_test(args):
    """Sends one test message to the Discord channel, signed by a teammate. Needs ~/.goat-ai/discord.env."""
    webhook = discord_client.from_settings(discord_client.settings())
    if webhook is None:
        raise ConfigError("No {} in {}. See the README, section Discord.".format(
            discord_client.WEBHOOK_KEY, discord_client.ENV_FILE))
    shared = load_shared()
    boss = team.read_owner(coordinator.CONFIG_FILE)["name"]
    name = team.persona(shared, args.role)["name"] or "G.O.A.T."
    username = team.dispatcher_signature(shared) if args.role == team.DISPATCHER else team.signature(shared, args.role)
    text = "{}this is {}. If you can read this, G.O.A.T. can reach your Discord. Nothing is running because of this message.".format(
        boss + ", " if boss else "", name)
    webhook.send(username, text[:1].upper() + text[1:])
    print("Sent a test message to your Discord channel as {}.".format(username))


def command_discord_check(args):
    """Checks the Discord reply setup. Reads only: it sends nothing and changes nothing."""
    values = discord_client.settings()
    problems = []

    def line(ok, text):
        print("  {} {}".format("ok  " if ok else "FIX ", text))
        if not ok:
            problems.append(text)

    print("Discord replies check (nothing is sent):")
    line(bool(values.get(discord_client.WEBHOOK_KEY)), "the webhook for Dex's posts is set ({})".format(discord_client.WEBHOOK_KEY))
    owner_ok = bool(discord_client.SNOWFLAKE.match(values.get(discord_client.OWNER_KEY, "").strip()))
    line(owner_ok, "your Discord user id is set ({})".format(discord_client.OWNER_KEY))
    try:
        bot = discord_client.bot_from_settings(values)
    except DiscordError as error:
        bot = None
        line(False, str(error))
    else:
        line(bot is not None, "the bot token is set ({})".format(discord_client.BOT_TOKEN_KEY))
    if bot is not None:
        try:
            line(True, "the bot token works (bot name: {})".format(bot.me().get("username", "?")))
            flags_on = bool(bot.application_flags() & discord_client.MESSAGE_CONTENT_FLAGS)
            line(flags_on, "Message Content Intent is on" if flags_on else
                 "switch on Message Content Intent: Developer Portal, your application, Bot tab, then Save")
            channel_id = coordinator.approvals_channel(bot, values)
            line(bool(channel_id), "found the private #approvals channel" if channel_id else
                 "no channel named approvals found: create a private text channel #approvals, and add the bot to it")
            if channel_id:
                bot.newest_message_id(channel_id)
                line(True, "the bot can read #approvals")
        except DiscordError as error:
            line(False, str(error))
    if problems:
        raise ConfigError("Not ready yet: {} thing(s) to fix. See the README, section Discord.".format(len(problems)))
    print("All set. Start the coordinator and type `help` in #approvals.")


def command_version(args):
    for line in version_lines():
        print(line)


def command_update(args):
    """Moves this G.O.A.T. clone to the newest release tag. Refuses while the coordinator has agents running."""
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
    goat = make_goat(args)
    items = views.all_items(goat.client, goat.board)
    lines = clean_up(main_checkout(os.getcwd()), goat.client, goat.board, items, goat.user["id"], utc_now(),
                     dry_run=args.dry_run)
    if not lines:
        print("Nothing to clean up.")
    for line in lines:
        print(line)


def command_retro_facts(args):
    """The item's local run records, coordinator logs and session digests. Reads GitHub only to find the issue."""
    goat = make_goat(args)
    item, _ = goat.load(args.item)
    # The folders are read here, not as default arguments, so tests can patch them.
    facts = retro_facts.collect(item.id, retro_facts.RUNS_FILE, retro_facts.LOG_DIR, retro_facts.PROJECTS_DIR)
    if args.json:
        print_json(facts)
    else:
        print(retro_facts.facts_markdown(facts, "#{} {}".format(item.id, item.name)), end="")


def command_retro_save(args):
    """Saves the retro note of a closed issue that this user owns. Writes a local file only, nothing to GitHub."""
    goat = make_goat(args)
    item, board = goat.load(args.item)
    retros.check_retro_item(item, goat.user["id"], board["doneLabel"])
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
    goat = make_goat(args)
    item, _ = goat.load(args.item)
    repo = main_checkout(os.getcwd())
    paths = env_file_paths(project_env())
    fetch(repo)
    path = prepare(repo, item_ref(item))
    for line in copy_env_files(repo, path, paths):
        print(line)
    # The path is last, so a script can read it with `tail -n 1`.
    print(path)


def command_release(args):
    run = make_goat(args).release(
        args.item,
        stage=args.stage,
        goat_status=args.goat_status,
        handoff=read_file(args.file),
        summary=read_file(args.summary) if args.summary else None,
        size=args.size,
        pr_url=args.pr,
    )
    print(json.dumps(run, indent=2))


# ---------- Autopilot: the project manager's backlog and picks ----------

def local_autopilot():
    """The autopilot settings from ~/.goat-ai/config.json. Off when the file is missing or the section is not usable."""
    try:
        with open(coordinator.CONFIG_FILE) as f:
            config = json.load(f)
        return autopilot.settings(config)
    except (OSError, ValueError):
        return autopilot.settings({})


def pipeline_view(goat):
    """What `backlog` shows and `pick` checks against: a dict with the repo, whether it runs on autopilot, the free
    slots, the picks so far, the work in flight, and the backlog issues that can start (ready) or not (blocked)."""
    repo = goat.board["repo"]
    user_id = goat.user["id"]
    settings = local_autopilot()
    items = views.all_items(goat.client, goat.board)
    ready, blocked = autopilot.backlog(goat.client, items, user_id)
    flying = autopilot.in_flight(items, user_id)
    state = coordinator.load_state()
    waiting_qa = coordinator.qa_pending(state, repo)
    picked = autopilot.read_picks(repo)
    on = autopilot.is_on(settings, repo)
    free = autopilot.room(settings, len(flying) + len(waiting_qa), autopilot.starts_today(state, utc_now())) if on else 0
    return {
        "repo": repo,
        "autopilot": on,
        "slots": max(0, free - len(picked)),
        "picked": picked,
        "inFlight": [{"number": int(item.id), "title": item.name, "stage": item.status, "status": item.goat_status,
                      "size": item.size, "url": item.url} for item in flying],
        "waitingForQa": [int(entry["item"]) for entry in waiting_qa],
        "ready": [{"number": int(item.id), "title": item.name, "priority": item.priority, "milestone": item.milestone,
                   "dependsOn": [int(n) for n in autopilot.dependencies(item.body)], "url": item.url} for item in ready],
        "blocked": [{"number": int(item.id), "title": item.name, "priority": item.priority, "reason": reason}
                    for item, reason in blocked],
    }


def command_backlog(args):
    view = pipeline_view(make_goat(args))
    if args.json:
        print_json(view)
        return
    print("{}: autopilot {}. Free slots: {}.".format(view["repo"], "on" if view["autopilot"] else "off", view["slots"]))
    print("\nIn flight:")
    for entry in view["inFlight"]:
        print("- #{number} {title} (stage:{stage}, goat:{status})".format(**entry))
    for number in view["waitingForQa"]:
        print("- #{} merged, waiting for QA".format(number))
    if not view["inFlight"] and not view["waitingForQa"]:
        print("- none")
    print("\nPicked, waiting for the coordinator:")
    for pick in view["picked"]:
        print("- #{}: {}".format(pick["item"], pick.get("why") or ""))
    if not view["picked"]:
        print("- none")
    print("\nReady to start (dependencies closed):")
    for entry in view["ready"]:
        print("- #{number} {title} (prio:{priority}, milestone: {milestone})".format(
            **dict(entry, priority=entry["priority"] or "none", milestone=entry["milestone"] or "none")))
    if not view["ready"]:
        print("- none")
    print("\nNot ready:")
    for entry in view["blocked"]:
        print("- #{number} {title}: {reason}".format(**entry))
    if not view["blocked"]:
        print("- none")


def command_pick(args):
    """Records one project manager pick in a local file. The coordinator checks it again and starts it."""
    goat = make_goat(args)
    view = pipeline_view(goat)
    if not view["autopilot"]:
        raise GoatError("{} does not run on autopilot, so nothing is started from picks. A person starts work.".format(
            view["repo"]))
    number = int(goat.resolve_item_id(args.item))
    if number not in [entry["number"] for entry in view["ready"]]:
        reason = next((entry["reason"] for entry in view["blocked"] if entry["number"] == number),
                      "it is not an open backlog issue (stage:discovery)")
        raise GoatError("#{} cannot be started: {}.".format(number, reason))
    if number not in [int(pick["item"]) for pick in view["picked"]] and view["slots"] <= 0:
        raise GoatError("No free slot: the pipeline is full or today's limit is reached. Pick nothing more.")
    count = autopilot.save_pick(view["repo"], number, args.why, utc_now())
    print("Picked #{} ({} pick(s) so far). The coordinator starts it when this run ends.".format(number, count))


def heredoc(command):
    """An example that sends Markdown on stdin, as the role skills do."""
    return "{} {} --file - <<'EOF'\n  ...\n  EOF".format(GOAT, command)


def build_parser():
    parser = argparse.ArgumentParser(prog="goat.py", description="G.O.A.T. AI on GitHub Issues",
                                     epilog="Agents: run 'goat.py guide' first.\n\n" + exit_codes.HELP_TEXT,
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
    list_command = add("list", command_list, "G.O.A.T. issues of the project's repo", GOAT + " list --mine --json",
                       item=False)
    list_command.add_argument("--mine", action="store_true", help="only issues assigned to you")
    list_command.add_argument("--json", action="store_true", help=json_help)
    add("show", command_show, "one issue as Markdown: labels, description, doc, comments",
        GOAT + " show 14").add_argument("--json", action="store_true", help=json_help)
    setup_repo = add("setup-repo", command_setup_repo, "create G.O.A.T.'s labels in the project's repo (safe to repeat)",
                     GOAT + " setup-repo --milestone 'v0.1'", item=False)
    setup_repo.add_argument("--check", action="store_true", help="change nothing; report the missing labels")
    setup_repo.add_argument("--milestone", help="also create this milestone if it does not exist")
    setup_repo.add_argument("--json", action="store_true", help=json_help)

    add_item = add("add", command_add,
                   "create an issue in the project's repo (stage:discovery, or G.O.A.T.'s intake with --ready)",
                   heredoc('add "Fix the login timeout"'), item=False)
    add_item.add_argument("title", help="the issue's title")
    add_item.add_argument("--file", help="Markdown description file, or - for stdin (default: stdin when piped)")
    add_item.add_argument("--milestone", help="milestone title (it must exist: see setup-repo --milestone)")
    add_item.add_argument("--priority", choices=fb.PRIORITY_ORDER, help="priority label")
    add_item.add_argument("--ready", action="store_true",
                          help="stage:intake, goat:ready, assigned to you: the coordinator starts it. "
                               "Not for agents.")

    backlog_command = add("backlog", command_backlog,
                          "the work in flight, free slots, and the backlog issues that can start (autopilot)",
                          GOAT + " backlog --json", item=False)
    backlog_command.add_argument("--json", action="store_true", help=json_help)
    add("pick", command_pick, "project manager only: choose a backlog issue for the autopilot to start",
        GOAT + " pick 38 --why 'Next in M2; no overlap with #39'").add_argument(
        "--why", required=True, help="one line: why this issue, and why now")

    add("claim", command_claim, "claim an item for a role", GOAT + " claim 14 --role plan",
        session=True).add_argument("--role", required=True, choices=("intake", "plan", "implement", "review"))
    add("renew", command_renew, "move this session's claim expiry forward", GOAT + " renew 14", session=True)

    post = add("post", command_post, "post a Markdown file as an issue comment", heredoc("post 14 --kind note"),
               session=True)
    post.add_argument("--kind", required=True, choices=POST_KINDS)
    post.add_argument("--file", required=True, help="Markdown file, or - for stdin")

    doc = add("doc", command_doc, "add a Markdown section to the issue's doc",
              heredoc("doc 14 --title 'Spec and plan'"), session=True)
    doc.add_argument("--title", required=True, help="section heading, for example 'Spec and plan'")
    doc.add_argument("--file", required=True, help="Markdown file, or - for stdin")

    summary = add("summary", command_summary, "set G.O.A.T. summary from a Markdown file (2,000 characters at most)",
                  heredoc("summary 14"), session=True)
    summary.add_argument("--file", required=True, help="Markdown file, or - for stdin")

    add("cleanup", command_cleanup, "remove local worktrees and branches of your closed issues, then set the label goat:done",
        GOAT + " cleanup --dry-run", item=False).add_argument(
        "--dry-run", action="store_true", help="print what it would do; change nothing")

    run = add("run", coordinator.command_run,
              "run the coordinator: start agents for your ready items, on an interval",
              GOAT + " run --loop-count 5 --loop-interval 300", item=False)
    run.add_argument("--once", action="store_true", help="run one turn, then exit (wins over --loop-count)")
    run.add_argument("--loop-count", type=positive_int, help="run this many turns, then exit (default: until Ctrl-C)")
    run.add_argument("--loop-interval", type=non_negative_int,
                     help="seconds between turns (default: intervalSeconds from the config)")
    run.add_argument("--delay", type=non_negative_int, default=0,
                     help="seconds to wait before the first turn (default: 0)")
    run.add_argument("--dry-run", action="store_true", help="show what would start; start nothing")
    add("status", coordinator.command_status, "is the coordinator running, and the agents it started", GOAT + " status",
        item=False)
    health_command = add("health", command_health, "is the coordinator running? (exit code 0: yes, 1: no)",
                         GOAT + " health --notify", item=False)
    health_command.add_argument("--json", action="store_true", help=json_help)
    health_command.add_argument("--notify", action="store_true",
                                help="show a desktop notification when it stopped without being told to")
    add("stop", coordinator.command_stop, "interrupt the agents the coordinator started", GOAT + " stop",
        item=False)

    add("chat", command_chat, "resume the issue's agent session in this terminal, for a live discussion",
        GOAT + " chat 14").add_argument(
        "--session", help="session id of an older run of the item (default: the latest run)")
    add("worktree", command_worktree,
        "make or refresh the issue's worktree (detached at origin/<default branch>, with env files) and print its path",
        GOAT + " worktree 14")

    add("retro-facts", command_retro_facts,
        "the issue's local run records, coordinator logs and session digests, for its retro",
        GOAT + " retro-facts 14").add_argument("--json", action="store_true", help=json_help)
    add("retro-save", command_retro_save, "save the retro note of a closed issue (local file, not GitHub)",
        heredoc("retro-save 14")).add_argument("--file", required=True, help="Markdown file, or - for stdin")
    add("retro-pending", command_retro_pending,
        "retro notes that no aggregate has read, the last aggregate time, and the aggregate reports",
        GOAT + " retro-pending --json", item=False).add_argument("--json", action="store_true", help=json_help)
    aggregate_save = add("retro-aggregate-save", command_retro_aggregate_save,
                         "save an aggregate retro report and mark its notes as read (local files, not GitHub)",
                         heredoc("retro-aggregate-save --notes me_app-11,me_app-12"), item=False)
    aggregate_save.add_argument("--notes", required=True, help="comma-separated note ids from retro-pending")
    aggregate_save.add_argument("--file", required=True, help="Markdown file, or - for stdin")

    team_command = add("team", command_team, "show the team: each agent's first name and voice, and the owner",
                       GOAT + " team --role plan", item=False)
    team_command.add_argument("--role", choices=("intake", "plan", "implement", "review", "retro", "qa", "pm", "dispatcher"),
                              help="show one role only")
    team_command.add_argument("--json", action="store_true", help=json_help)
    discord_test = add("discord-test", command_discord_test,
                       "send one test message to your Discord channel, signed by a teammate (default: Dex)",
                       GOAT + " discord-test --as plan", item=False)
    discord_test.add_argument("--as", dest="role", default=team.DISPATCHER,
                              choices=("intake", "plan", "implement", "review", "retro", "qa", "pm", "dispatcher"),
                              help="which teammate signs it (default: dispatcher)")
    add("discord-check", command_discord_check,
        "check the Discord reply setup (token, #approvals, Message Content Intent). Sends nothing",
        GOAT + " discord-check", item=False)
    add("version", command_version, "print G.O.A.T. version and commit", GOAT + " version", item=False)
    add("update", command_update, "move this G.O.A.T. install to the newest release tag", GOAT + " update", item=False)
    add("rules", command_rules, "print the shared gates and leases (goat-config.json)", GOAT + " rules", item=False)
    add("guide", command_guide, "print the agent guide: commands, stages, goat statuses, and what agents may run",
        GOAT + " guide", item=False)

    release = add("release", command_release, "post the handoff and release the claim",
                  heredoc("release 14 --stage plan --goat-status plan-review"), session=True)
    release.add_argument("--stage", required=True, choices=("intake", "plan", "implement", "review"))
    release.add_argument("--goat-status", required=True, choices=fb.RELEASE_STATUSES)
    release.add_argument("--file", required=True, help="handoff note, Markdown file")
    release.add_argument("--summary", help="short summary, Markdown file (2,000 characters at most)")
    release.add_argument("--size", choices=("S", "M", "L", "XL"))
    release.add_argument("--pr", help="PR link, kept in the state comment")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args) or 0
    except (GoatError, GitHubError, DiscordError, ConfigError, WorktreeError, UpdateError, OSError) as error:
        print("goat.py: {}".format(error), file=sys.stderr)
        # OSError has no exit_code, so it is a general failure.
        return getattr(error, "exit_code", exit_codes.GENERAL)
