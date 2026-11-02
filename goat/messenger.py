"""What the team says in Discord, and when. Outbound only (see goat/discord.py for the sending).

When an issue starts waiting for a person, the teammate who owns that moment posts one short message: Maya for a size
check, Theo for a plan, Priya for a merge, and whoever was working for a question or a chat. Dex posts about merges
and stalled issues. A message carries the issue's title as a link, a short excerpt of the summary, and what to do.

Each waiting state is announced once. `state["notified"]` remembers what was announced for each issue; when the issue
leaves that state the memory is cleared, so a plan that comes back for a second review is announced again.
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
