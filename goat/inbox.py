"""Replies from Discord: the owner types a command, and the coordinator acts as the owner.

The owner types in the private approvals channel:

    start 9                        release a backlog issue to the factory, assigned to you
    approve 9                      approve a size or plan checkpoint
    send back 9: what to change    comment your note and send the issue back to the agent
    answer 9: your answer          answer an agent's question and let it continue
    status                         what is waiting on you

Agents never see any of this and never change a label because of it. The coordinator reads the channel and makes the
same label change the owner would make by hand (goat/board.py label_changes), with the owner's own `gh` login. It
only does what was typed, and only when all of these hold:

- the message is from the owner's Discord account (DISCORD_OWNER_USER_ID), in the approvals channel, and not from a bot;
- the message was sent after this coordinator started listening and is recent (MAX_AGE_SECONDS), so a command typed
  while the coordinator was off is never run late. Such a message is not dropped silently: Dex says it was skipped;
- the issue is an open G.O.A.T. issue assigned to the owner, with no agent holding it, and it waits at the gate the
  command is for (approve: size or plan checkpoint; answer: a question; send back: a size, plan or merge checkpoint).
  `start` is the one command for an issue that is not in the factory yet: a backlog issue that nobody else owns;
- each message is looked at once: the cursor moves before anything is done, so a crash can lose a command (the owner
  sees no reply and types it again) but can never run one twice.

A merge is never done from Discord: merging a PR stays on GitHub. Every action leaves a note on the issue.
"""

import re
import time
from collections import namedtuple
from datetime import datetime, timezone

from goat import board as fb
from goat import team
from goat.config import ConfigError
from goat.discord import DiscordError, clean
from goat.github import GitHubError
from goat.messenger import sentence

MAX_AGE_SECONDS = 30 * 60
POLL_SECONDS = 15
RESOLVE_RETRY_SECONDS = 300
MAX_PAGES = 4
PAGE_SIZE = 50
MAX_TEXT = 1800
MAX_NUMBER_DIGITS = 7
MARKER = "<!-- goat:discord -->"
DISCORD_EPOCH_MS = 1420070400000
# A message is a plain one (0) or a reply to another message (19). Everything else (joins, pins, ...) is ignored.
ACCEPTED_TYPES = (0, 19)

Command = namedtuple("Command", ["kind", "number", "text"])
# What a command did: the reply to send, and whether an agent can now start (so the coordinator runs a turn at once).
Outcome = namedtuple("Outcome", ["text", "run_turn"])

HELP_TEXT = ("here is what I understand:\n"
             "`start 9` releases a backlog issue to the factory and assigns it to you.\n"
             "`approve 9` approves a size or plan checkpoint.\n"
             "`send back 9: what to change` sends it back to the agent with your note.\n"
             "`answer 9: your answer` answers an agent's question.\n"
             "`status` lists what waits on you.\n"
             "Merging a PR always happens on GitHub.")
USAGE = {
    "start": "`start` takes only the issue number, like `start 9` (or `start 9: assign me`).",
    "approve": "`approve` takes only the issue number, like `approve 9`. To add a note, use `send back 9: what to change`.",
    "send_back": "`send back` needs the issue number and your note, like `send back 9: add a skip button`.",
    "answer": "`answer` needs the issue number and your answer, like `answer 9: yes, use Birmingham`.",
}

STATUS_WORDS = re.compile(r"^(?:status|what'?s waiting|waiting)\s*[?.!]*$", re.IGNORECASE)
HELP_WORDS = re.compile(r"^(?:help|commands|\?)\s*[?.!]*$", re.IGNORECASE)
ASSIGN_ME = re.compile(r"^assign(?:ed)?(?:\s+it)?(?:\s+to)?\s+me[.!]*$", re.IGNORECASE)
VERBS = (("start", re.compile(r"^start\b(.*)$", re.IGNORECASE | re.DOTALL)),
         ("approve", re.compile(r"^approved?\b(.*)$", re.IGNORECASE | re.DOTALL)),
         ("send_back", re.compile(r"^send[\s_-]*back\b(.*)$", re.IGNORECASE | re.DOTALL)),
         ("answer", re.compile(r"^answer\b(.*)$", re.IGNORECASE | re.DOTALL)))
NUMBER_AND_TEXT = re.compile(r"^#?(\d{1,%d})(?:\s*[:\-–—]\s*|\s+|$)(.*)$" % MAX_NUMBER_DIGITS, re.DOTALL)


def parse(content):
    """What an owner's message means: a Command, or None for an empty message.

    kind is approve, send_back, answer, status, help, or unclear (text then holds what to tell the owner)."""
    text = (content or "").strip().lstrip("!").strip()
    if not text:
        return None
    if STATUS_WORDS.match(text):
        return Command("status", None, "")
    if HELP_WORDS.match(text):
        return Command("help", None, "")
    for kind, pattern in VERBS:
        match = pattern.match(text)
        if not match:
            continue
        found = NUMBER_AND_TEXT.match(match.group(1).strip())
        note = found.group(2).strip() if found else ""
        if kind in ("approve", "start"):
            # No note. `start 9: assign me` is allowed: starting always assigns the issue to the owner.
            wrong = not found or (note and not (kind == "start" and ASSIGN_ME.match(note)))
        else:
            wrong = not found or not note
        if wrong:
            return Command("unclear", None, USAGE[kind])
        return Command(kind, int(found.group(1)), note if kind in ("send_back", "answer") else "")
    return Command("unclear", None, "I did not recognise that. " + HELP_TEXT[0].upper() + HELP_TEXT[1:])


# ---------- doing it ----------

def owner_label(owner):
    name = (owner or {}).get("name") or ""
    title = (owner or {}).get("title") or ""
    if not name:
        return "The owner"
    return "{} ({})".format(name, title) if title else name


def next_agent(shared, role):
    """"Theo (Plan agent)" for a role, or a plain phrase when it has no name or no role."""
    if not role:
        return "the next agent"
    who = team.persona(shared, role)
    return "{} ({})".format(who["name"], who["title"]) if who["name"] else who["title"]


def link(item):
    return "#{} {}\n<{}>".format(item.id, item.name, item.url)


def refuse(item, command, status):
    """Why the command does not apply to an issue in this status."""
    here = "#{} is at {}.".format(item.id, fb.display(status) or "no goat status")
    if fb.same_label(status, fb.MERGE_REVIEW):
        if command.kind == "approve":
            return here + " Merging happens on GitHub: review and merge the PR there. To send it back, use `send back {}: what to change`.".format(item.id)
    elif fb.same_label(status, fb.ANSWER_QUESTION):
        return here + " It is asking you a question: reply with `answer {}: your answer`.".format(item.id)
    elif fb.same_label(status, fb.CHAT_WITH_AGENT):
        return here + " The agent wants a live talk: run `goat.py chat {}` in a terminal.".format(item.id)
    elif fb.same_label(status, fb.STALLED):
        return here + " Agents stopped after repeated runs that changed nothing. Read the agent log, then set the label goat:ready on GitHub."
    elif fb.same_label(status, fb.WORKING):
        return here + " An agent is working on it right now."
    elif fb.same_label(status, fb.READY) or fb.same_label(status, fb.APPROVED):
        return here + " It is waiting for an agent, not for you."
    only = {"approve": "`approve` is for size and plan checkpoints.",
            "answer": "`answer` is for questions an agent asked.",
            "send_back": "`send back` is for size, plan and merge checkpoints."}[command.kind]
    return "{} {}".format(here, only)


def feedback_comment(text, owner):
    """The owner's words as a comment. It has no robot header, so agents read it as what a person wrote."""
    return "{}\n{}\n\n<sub>Sent from Discord by {}.</sub>".format(MARKER, clean(text, MAX_TEXT), owner_label(owner))


def audit_comment(shared, owner, sentence_text):
    return "{}\n**🤖 {}** · {}".format(MARKER, team.dispatcher_signature(shared), sentence_text)


def do_approve(client, item, shared, owner):
    add, remove = fb.label_changes(item, goat_status=fb.APPROVED)
    client.set_labels(item.id, add=add, remove=remove)
    role = fb.NEXT_STAGE.get(item.stage)
    gate = "size" if fb.same_label(item.goat_status, fb.TIER_REVIEW) else "plan"
    agent = next_agent(shared, role)
    saved = True
    try:
        client.create_comment(item.id, audit_comment(
            shared, owner, "{} approved the {} checkpoint in Discord. {} takes it from here.".format(
                owner_label(owner), gate, agent)))
    except GitHubError:
        saved = False
    text = "approved the {} checkpoint on {}\n{} takes it from here.".format(gate, link(item), agent)
    if not saved:
        text += "\n(I could not leave the note on the issue, but the approval is in place.)"
    return Outcome(text, True)


def do_note_and_ready(client, item, command, shared, owner, stage=None):
    """Comments the owner's note, then sets goat:ready (and a stage). The note goes first: an agent that restarts with
    no note would not know what to change."""
    client.create_comment(item.id, feedback_comment(command.text, owner))
    add, remove = fb.label_changes(item, goat_status=fb.READY, stage=stage)
    try:
        client.set_labels(item.id, add=add, remove=remove)
    except GitHubError as error:
        return Outcome("I posted your note on #{}, but could not change its labels: {}\nNothing started. "
                       "Set the label goat:ready on GitHub to send it on.".format(item.id, error), False)
    agent = next_agent(shared, stage or item.stage)
    verb = "answered" if command.kind == "answer" else "sent back"
    where = {"answer": "your answer is on the issue", "send_back": "your note is on the issue"}[command.kind]
    return Outcome("{} {}, and {}.\n{} picks it up.".format(verb, link(item), where, agent), True)


def apply_start(command, found, problems, shared, owner):
    """Releases a backlog issue to the factory: assigns it to the owner, then sets stage:intake and goat:ready.

    Only for an open issue that is not in the factory yet (no goat: label, in the backlog or with no stage) and that
    nobody else owns. The assignment goes first: the coordinator only works on the owner's issues."""
    number = command.number
    open_ones = [entry for entry in found if entry[1].status != fb.DONE_STATUS]
    if not open_ones:
        if problems and not found:
            return Outcome("I could not read GitHub just now ({}). Nothing changed.".format("; ".join(problems)), False)
        if found:
            return Outcome("#{} is closed, so I did not start it. Nothing changed.".format(number), False)
        return Outcome("I can't find an issue #{}. Nothing changed.".format(number), False)
    if len(open_ones) > 1:
        return Outcome("Issue #{} exists in more than one project, and I can't tell which you mean yet. "
                       "Nothing changed. Use GitHub for this one.".format(number), False)
    client, item, user_id = open_ones[0]
    if item.goat_status.strip():
        return Outcome("#{} is already in the factory, at {}. Nothing changed.".format(item.id, fb.display(item.goat_status)), False)
    if item.status not in ("", fb.DISCOVERY):
        return Outcome("#{} is at stage:{}, not in the backlog, so I did not start it. Nothing changed.".format(
            item.id, item.status), False)
    others = [login for login in item.owner_ids if login.lower() != str(user_id).lower()]
    if others:
        return Outcome("#{} is assigned to {}. I will not take it over: assign it to yourself on GitHub first if you want it. "
                       "Nothing changed.".format(item.id, ", ".join(others)), False)
    if not item.is_owned_by(user_id):
        client.add_assignees(item.id, [user_id])
    add, remove = fb.label_changes(item, goat_status=fb.READY, stage="intake")
    try:
        client.set_labels(item.id, add=add, remove=remove)
    except GitHubError as error:
        return Outcome("I assigned #{} to you, but could not set its labels: {}\nNothing started. Add stage:intake and "
                       "goat:ready on GitHub to start it.".format(item.id, error), False)
    agent = next_agent(shared, "intake")
    saved = True
    try:
        client.create_comment(item.id, audit_comment(
            shared, owner, "{} started this in Discord and assigned it to themselves. {} sizes it first.".format(
                owner_label(owner), agent)))
    except GitHubError:
        saved = False
    text = "started {}\nAssigned to you. {} sizes it first, and I will message you when it needs you.".format(link(item), agent)
    if not saved:
        text += "\n(I could not leave the note on the issue, but it is started.)"
    return Outcome(text, True)


def apply(command, found, problems, shared, owner, now):
    """Does a start, approve, send_back or answer command. Returns an Outcome.

    found: [(client, GoatItem, user id)] for each repo that has an issue with this number (read just now)."""
    number = command.number
    if command.kind == "start":
        return apply_start(command, found, problems, shared, owner)
    usable = [(client, item) for client, item, user_id in found
              if item.is_goat_item and item.is_owned_by(user_id)]
    if not usable:
        if problems and not found:
            return Outcome("I could not read GitHub just now ({}). Nothing changed.".format("; ".join(problems)), False)
        return Outcome("I can't find an open G.O.A.T. issue #{} assigned to you. Nothing changed.".format(number), False)
    if len(usable) > 1:
        return Outcome("Issue #{} exists in more than one project, and I can't tell which you mean yet. "
                       "Nothing changed. Use GitHub for this one.".format(number), False)
    client, item = usable[0]
    status = item.goat_status
    if item.claim_is_live(now):
        return Outcome(refuse(item, command, fb.WORKING) + " Nothing changed.", False)
    if command.kind == "approve" and (fb.same_label(status, fb.TIER_REVIEW) or fb.same_label(status, fb.PLAN_REVIEW)):
        return do_approve(client, item, shared, owner)
    if command.kind == "send_back" and (fb.same_label(status, fb.TIER_REVIEW) or fb.same_label(status, fb.PLAN_REVIEW)):
        return do_note_and_ready(client, item, command, shared, owner)
    if command.kind == "send_back" and fb.same_label(status, fb.MERGE_REVIEW):
        return do_note_and_ready(client, item, command, shared, owner, stage="implement")
    if command.kind == "answer" and fb.same_label(status, fb.ANSWER_QUESTION):
        return do_note_and_ready(client, item, command, shared, owner)
    return Outcome(refuse(item, command, status) + " Nothing changed.", False)


def status_text(items):
    if not items:
        return "nothing waits on you right now."
    lines = ["waiting on you:"]
    for item in sorted(items, key=lambda entry: int(entry.id)):
        lines.append("• #{} {}: {}".format(item.id, fb.display(item.goat_status), item.name))
    return "\n".join(lines)


# ---------- reading the channel ----------

def message_time(message):
    """When a Discord message was sent, in seconds since 1970 (taken from its id)."""
    return ((int(message["id"]) >> 22) + DISCORD_EPOCH_MS) / 1000.0


def snowflake_at(seconds):
    """The smallest Discord id of a message sent at this time (seconds since 1970). Ids sort by time."""
    return str((int(seconds * 1000) - DISCORD_EPOCH_MS) << 22)


class Inbox:
    """Reads the approvals channel and acts on the owner's commands. Everything outside is passed in, so tests need no network.

    resolve_channel() -> channel id (str) or None.   locate(number) -> (found, problems) as for apply().
    waiting() -> (items waiting on the owner, problems).   reply(text, message id) sends the answer.
    save(state) stores the state (the cursor lives in state["inbox"]["after"])."""

    def __init__(self, bot, owner_id, resolve_channel, locate, waiting, reply, save, shared, owner, log,
                 wall=time.time, clock=time.monotonic, started=None):
        self.bot = bot
        self.owner_id = str(owner_id)
        self.resolve_channel = resolve_channel
        self.locate = locate
        self.waiting = waiting
        self.reply = reply
        self.save = save
        self.shared = shared
        self.owner = owner
        self.log = log
        self.wall = wall
        self.clock = clock
        # Only messages sent after this moment are commands. Earlier ones were typed before anyone was listening.
        self.started = wall() if started is None else started
        self.channel_id = None
        self.next_poll = 0.0
        self.next_resolve = 0.0
        self.last_problem = None
        self.told_about_content = False

    def is_due(self):
        return self.clock() >= self.next_poll

    def problem(self, text):
        """Logs a problem once, until it changes or goes away."""
        if text != self.last_problem:
            self.log("Discord inbox: " + text)
            self.last_problem = text

    def ready(self):
        if self.channel_id:
            return True
        if self.clock() < self.next_resolve:
            return False
        self.next_resolve = self.clock() + RESOLVE_RETRY_SECONDS
        try:
            self.channel_id = self.resolve_channel()
        except DiscordError as error:
            self.problem(str(error))
            return False
        if not self.channel_id:
            self.problem("no private #approvals channel found, so replies from Discord are off. See the README, section Discord.")
            return False
        return True

    def poll(self, state):
        """Looks for new messages and acts on them. Returns True when an agent can now start (run a turn)."""
        self.next_poll = self.clock() + POLL_SECONDS
        if not self.ready():
            return False
        run_turn = False
        try:
            cursor = state.setdefault("inbox", {}).get("after")
            if cursor is None:
                # The first look reads the last few minutes, so a message typed just before we started listening gets
                # an answer ("I was not listening yet") instead of silence. Those messages are never run.
                cursor = state["inbox"]["after"] = snowflake_at(self.started - MAX_AGE_SECONDS)
                self.save(state)
            for _ in range(MAX_PAGES):
                messages = self.bot.messages_after(self.channel_id, cursor, PAGE_SIZE)
                self.problem_cleared()
                for message in messages:
                    cursor = state["inbox"]["after"] = str(message["id"])
                    self.save(state)
                    run_turn = self.handle_message(message) or run_turn
                if len(messages) < PAGE_SIZE:
                    break
        except DiscordError as error:
            self.problem(str(error))
        return run_turn

    def problem_cleared(self):
        self.last_problem = None

    def handle_message(self, message):
        """Acts on one message if it is the owner's. Returns True when an agent can now start."""
        author = message.get("author") or {}
        if author.get("bot") or message.get("webhook_id"):
            return False
        if str(author.get("id")) != self.owner_id:
            self.log("Discord inbox: ignored a message from another account.")
            return False
        if str(message.get("channel_id")) != str(self.channel_id) or message.get("type", 0) not in ACCEPTED_TYPES:
            return False
        message_id = str(message["id"])
        content = message.get("content") or ""
        if not content.strip():
            if not self.told_about_content:
                self.told_about_content = True
                self.say(message_id, "I can see you wrote something, but not what it says. In the Discord Developer "
                                     "Portal, open the Dex application, then Bot, and switch on Message Content Intent.")
                self.log("Discord inbox: an empty message arrived: Message Content Intent may be off.")
            return False
        if message_time(message) < self.started:
            self.say(message_id, "I had not started listening when you sent that, so I did not run it. "
                                 "Send it again if you still want it.")
            self.log("Discord inbox: skipped a message sent before the coordinator started.")
            return False
        if self.wall() - message_time(message) > MAX_AGE_SECONDS:
            self.say(message_id, "That message is more than {} minutes old and I was not listening, so I skipped it. "
                                 "Send it again if you still want it.".format(MAX_AGE_SECONDS // 60))
            self.log("Discord inbox: skipped an old message.")
            return False
        command = parse(content)
        if command is None:
            return False
        self.log("Discord inbox: {}{}.".format(command.kind.replace("_", " "),
                                                " #{}".format(command.number) if command.number else ""))
        try:
            outcome = self.run(command)
        except (GitHubError, ConfigError) as error:
            outcome = Outcome("I hit a problem and nothing was changed: {}".format(error), False)
        self.say(message_id, outcome.text)
        return outcome.run_turn

    def run(self, command):
        if command.kind == "help":
            return Outcome(sentence(self.owner.get("name"), HELP_TEXT), False)
        if command.kind == "unclear":
            return Outcome(sentence(self.owner.get("name"), command.text), False)
        if command.kind == "status":
            items, problems = self.waiting()
            if problems and not items:
                return Outcome("I could not read GitHub just now ({}).".format("; ".join(problems)), False)
            return Outcome(sentence(self.owner.get("name"), status_text(items)), False)
        found, problems = self.locate(command.number)
        outcome = apply(command, found, problems, self.shared, self.owner, datetime.now(timezone.utc))
        return Outcome(sentence(self.owner.get("name"), outcome.text), outcome.run_turn)

    def say(self, message_id, text):
        try:
            self.reply(text, message_id)
        except DiscordError as error:
            self.log("Discord inbox: could not reply: {}".format(error))
