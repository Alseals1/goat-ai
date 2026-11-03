"""What the team says in Discord, and when. Outbound only (see goat/discord.py for the sending).

When an issue starts waiting for a person, the teammate who owns that moment posts one short message: Maya for a size
check, Theo for a plan, Priya for a merge, and whoever was working for a question or a chat. Dex posts about merges
and stalled issues. A message carries the issue's title as a link, a short excerpt of the summary, and what to do.

Each waiting state is announced once. `state["notified"]` remembers what was announced for each issue; when the issue
leaves that state the memory is cleared, so a plan that comes back for a second review is announced again.

The activity feed (agent started, agent finished, stage moved) is at the end of this file.
"""

from goat import board as fb
from goat import team
from goat.discord import DiscordError, clean, MAX_DESCRIPTION, MAX_TITLE
from goat.views import waiting_hint

WAITING = (fb.TIER_REVIEW, fb.PLAN_REVIEW, fb.MERGE_REVIEW, fb.ANSWER_QUESTION, fb.CHAT_WITH_AGENT)
SPEAKER = {fb.TIER_REVIEW: "intake", fb.PLAN_REVIEW: "plan", fb.MERGE_REVIEW: "review"}
OPENERS = {
    fb.TIER_REVIEW: "I sized this one up and need your call on the size.",
    fb.PLAN_REVIEW: "The plan is ready for you.",
    fb.MERGE_REVIEW: "It's reviewed and ready for you to merge.",
    fb.ANSWER_QUESTION: "I'm stuck on one thing and need an answer from you.",
    fb.CHAT_WITH_AGENT: "I'd like to talk this one through with you.",
}
COLORS = {fb.TIER_REVIEW: 0xE99695, fb.PLAN_REVIEW: 0xE99695, fb.MERGE_REVIEW: 0x0E8A16,
          fb.ANSWER_QUESTION: 0xD93F0B, fb.CHAT_WITH_AGENT: 0xD93F0B, "dispatcher": 0x1D76DB}


def speaker(item):
    """The role that speaks for this waiting state."""
    return SPEAKER.get(item.goat_status) or item.stage or "dispatcher"


def what_to_do(goat_status):
    hint = waiting_hint(goat_status)
    hint = hint[len("please "):] if hint.startswith("please ") else hint
    return hint[:1].upper() + hint[1:]


def sentence(owner_name, text):
    """The text as a sentence, addressed to the owner when we know their name: "Pat, the plan is ready."

    Without a name the first letter is a capital. With one, it is lowercase, except for the word "I"."""
    if not owner_name:
        return text[:1].upper() + text[1:]
    if not (text.startswith("I ") or text.startswith("I'")):
        text = text[:1].lower() + text[1:]
    return "{}, {}".format(owner_name, text)


def waiting_message(item, summary, shared, owner_name):
    """(username, content, embed) for an issue that waits for a person."""
    role = speaker(item)
    opener = OPENERS[item.goat_status]
    description = clean(summary, MAX_DESCRIPTION) if summary else ""
    description = (description + "\n\n" if description else "") + "**What to do:** " + what_to_do(item.goat_status)
    footer = " · ".join(part for part in (item.status, ("size " + item.size) if item.size else "", item.board_id) if part)
    embed = {"title": clean("#{} {}".format(item.id, item.name), MAX_TITLE), "url": item.url, "description": description,
             "color": COLORS.get(item.goat_status, COLORS["dispatcher"]), "footer": {"text": footer}}
    return team.signature(shared, role), sentence(owner_name, opener), embed


def dispatcher_message(text, shared, owner_name, title=None, url=None):
    """(username, content, embed) for a message from Dex."""
    embed = None
    if title:
        embed = {"title": clean(title, MAX_TITLE), "color": COLORS["dispatcher"]}
        if url:
            embed["url"] = url
    return team.dispatcher_signature(shared), sentence(owner_name, text), embed


def tell_waiting(webhook, candidates, user_id, state, fetch_summary, shared, owner_name, now, dry_run, log):
    """Announces each issue that newly waits for a person. Returns how many messages were sent.

    candidates: [(repo path, GoatItem)]. fetch_summary(repo path, item) returns the summary text ("" if unknown).
    Never raises for a Discord problem: it logs and tries again on the next turn.
    """
    notified = state.setdefault("notified", {})
    waiting_now = set()
    sent = 0
    for repo_path, item in candidates:
        if not item.is_owned_by(user_id) or item.goat_status not in WAITING or item.claim_is_live(now):
            continue
        key = "{}#{}".format(item.board_id, item.id)
        value = "{}:{}".format(item.status, item.goat_status)
        waiting_now.add(key)
        if notified.get(key) == value:
            continue
        if dry_run:
            log("Would tell Discord: #{} is waiting ({}).".format(item.id, item.goat_status))
            continue
        try:
            username, content, embed = waiting_message(item, fetch_summary(repo_path, item), shared, owner_name)
            webhook.send(username, content, embed)
        except DiscordError as error:
            log("Discord: could not announce #{}: {}".format(item.id, error))
            continue
        notified[key] = value
        sent += 1
        log("Discord: told {} that #{} is waiting ({}).".format(owner_name or "you", item.id, item.goat_status))
    repos_read = {item.board_id for _, item in candidates}
    for key in list(notified):
        # An issue that is no longer waiting is forgotten, so a second round is announced again.
        if key not in waiting_now and key.split("#")[0] in repos_read:
            del notified[key]
    return sent


# ---------- Activity: an agent started, an agent finished, an issue changed stage ----------
#
# These go to the same channel as the waiting messages, and they always say who the issue is on. They are for the
# owner's own issues. The first time an issue is seen its stage is only recorded, so starting the coordinator does not
# announce every issue on the board. Moves into staging and done are left to the merge message and the retro.

STAGE_MOVE_SILENT = (fb.STAGING, fb.DONE_STATUS)
ERROR_COLOR = 0xD93F0B


def agent_name(shared, role):
    """The teammate's first name for a role, or the plain title ("Plan agent") when none is set."""
    who = team.persona(shared, role)
    return who["name"] or who["title"]


def on_whom(item, shared, owner_name, now):
    """Who the issue is on right now, as text, or None when nobody is due.

    The owner when it waits for them (a gate, a question, a chat, a stall). An agent with a live claim has it. A ready
    or approved issue is on the agent that is due next (board.next_role)."""
    status = item.goat_status
    if status in WAITING or fb.same_label(status, fb.STALLED):
        return owner_name or "you"
    if fb.same_label(status, fb.WORKING) and item.claim_is_live(now):
        role = item.stage
    else:
        role = fb.next_role(item, now)
    return agent_name(shared, role) if role else None


def on_line(on):
    return "**On:** {}".format(on) if on else ""


def activity_embed(title, url, on, color, footer=""):
    embed = {"title": clean(title, MAX_TITLE), "color": color}
    if url:
        embed["url"] = url
    if on:
        embed["description"] = on_line(on)
    if footer:
        embed["footer"] = {"text": footer}
    return embed


def started_message(role, label, url, model, shared):
    """(username, content, embed) for an agent that just started on an issue. The issue is on that agent."""
    name = agent_name(shared, role)
    footer = " · ".join(part for part in (role, model) if part)
    return (team.signature(shared, role), "I'm starting on this one now.",
            activity_embed(label, url, name, COLORS["dispatcher"], footer))


def finished_message(role, label, url, exit_code, on, shared):
    """(username, content, embed) for an agent whose run ended. `on` is who the issue is on now, or None."""
    failed = exit_code not in (0, "0", "unknown")  # "unknown": the coordinator restarted and lost the exit code
    if failed:
        content = "I stopped with an error (exit {}). The log has the details.".format(exit_code)
    else:
        content = "I'm done for now."
    return (team.signature(shared, role), content,
            activity_embed(label, url, on, ERROR_COLOR if failed else COLORS["dispatcher"], role))


def stage_message(item, old_stage, shared, owner_name, now):
    """(username, content, embed) from Dex for an issue that moved from one stage to another."""
    on = on_whom(item, shared, owner_name, now)
    return (team.dispatcher_signature(shared), "Moved from {} to {}.".format(old_stage or "no stage", item.status),
            activity_embed("#{} {}".format(item.id, item.name), item.url, on, COLORS["dispatcher"]))


def tell_stage_moves(webhook, candidates, user_id, state, shared, owner_name, now, dry_run, log):
    """Announces each of the owner's issues whose stage changed since the last turn. Returns how many were sent.

    state["stages"] remembers each issue's stage. An issue seen for the first time, or one that moved into staging or
    done, is recorded without a message. A failed send is logged and tried again on the next turn. A dry run reads
    only: it sends nothing and records nothing."""
    stages = state.setdefault("stages", {})
    seen = set()
    sent = 0
    for _, item in candidates:
        if not item.is_owned_by(user_id):
            continue
        key = "{}#{}".format(item.board_id, item.id)
        stage = item.status or ""
        seen.add(key)
        old = stages.get(key)
        if old == stage:
            continue
        if old is not None and stage not in STAGE_MOVE_SILENT:
            if dry_run:
                log("Would tell Discord: #{} moved from {} to {}.".format(item.id, old or "no stage", stage))
                continue
            try:
                webhook.send(*stage_message(item, old, shared, owner_name, now))
            except DiscordError as error:
                log("Discord: could not announce the move of #{}: {}".format(item.id, error))
                continue
            sent += 1
            log("Discord: told {} that #{} moved from {} to {}.".format(owner_name or "you", item.id, old or "no stage", stage))
        if not dry_run:
            stages[key] = stage
    repos_read = {item.board_id for _, item in candidates}
    for key in list(stages):
        if key not in seen and key.split("#")[0] in repos_read:
            del stages[key]
    return sent
