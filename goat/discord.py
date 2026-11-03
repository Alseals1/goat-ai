"""Talks to Discord: sends messages through a webhook, and reads the owner's replies with the bot.

The webhook URL is in ~/.goat-ai/discord.env (outside the repository), as DISCORD_WEBHOOK_DISPATCH. It is a secret:
anyone who has it can post to the channel. This module never prints it, never puts it in an error message, and the
file is never committed. A webhook lets each message carry its own name, so Maya, Theo and Priya each post as
themselves.

The bot token (DISCORD_BOT_TOKEN, same file, same rules) is only used to read the approvals channel and to answer
in it (goat/inbox.py decides what a message means). It is never printed or put in an error message either.

Safety, by design:
- Every message sets `allowed_mentions` to none, so text from an issue title can never ping @everyone or a role.
- Text is cut to Discord's limits and stripped of control characters.
- Certificate checking stays on. The python.org build of Python on macOS ships without root certificates, so when
  Python has none, the macOS system bundle is used. Verification is never switched off.
"""

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request

from goat import exit_codes
from goat.config import LOCAL_DIR, parse_env_file

ENV_FILE = os.path.join(LOCAL_DIR, "discord.env")
WEBHOOK_KEY = "DISCORD_WEBHOOK_DISPATCH"
BOT_TOKEN_KEY = "DISCORD_BOT_TOKEN"
GUILD_KEY = "DISCORD_GUILD_ID"
OWNER_KEY = "DISCORD_OWNER_USER_ID"
APPROVALS_KEY = "DISCORD_CHANNEL_APPROVALS"
APPROVALS_CHANNEL_NAME = "approvals"
API = "https://discord.com/api/v10"
# Application flag bits: Message Content Intent is on for the app (limited = a bot that is not verified).
MESSAGE_CONTENT_FLAGS = (1 << 18) | (1 << 19)
SNOWFLAKE = re.compile(r"^\d{5,25}$")
WEBHOOK_URL = re.compile(r"^https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/(?:v\d+/)?webhooks/\d+/[\w-]+$")
SYSTEM_CERTIFICATES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt")
TIMEOUT_SECONDS = 15
MAX_USERNAME = 80
MAX_CONTENT = 1900
MAX_TITLE = 250
MAX_DESCRIPTION = 1500
RETRIES = 2
MAX_WAIT_SECONDS = 10


class DiscordError(Exception):
    exit_code = exit_codes.GENERAL


def clean(text, limit):
    """One string without control characters, cut at `limit` (with an ellipsis)."""
    text = "".join(ch if ch.isprintable() or ch in "\n\t" else " " for ch in str(text or "")).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def settings(path=None):
    """The settings in the env file. The path is looked up when called, so tests can point it somewhere harmless."""
    return parse_env_file(path or ENV_FILE)


def ssl_context():
    """A verifying SSL context. Uses the system certificates when Python has none of its own."""
    paths = ssl.get_default_verify_paths()
    if paths.cafile or (paths.capath and os.path.isdir(paths.capath) and os.listdir(paths.capath)):
        return ssl.create_default_context()
    for candidate in SYSTEM_CERTIFICATES:
        if os.path.isfile(candidate):
            return ssl.create_default_context(cafile=candidate)
    return ssl.create_default_context()


class Webhook:
    def __init__(self, url, opener=None, sleep=time.sleep):
        if not WEBHOOK_URL.match(url or ""):
            raise DiscordError("DISCORD_WEBHOOK_DISPATCH does not look like a Discord webhook URL.")
        self.url = url
        self.opener = opener or self.open
        self.sleep = sleep

    @staticmethod
    def open(request, timeout):
        return urllib.request.urlopen(request, timeout=timeout, context=ssl_context())

    def hide(self, text):
        """Removes the webhook URL (and its secret part) from any text before it can be shown."""
        text = str(text).replace(self.url, "[webhook]")
        return re.sub(r"webhooks/\d+/[\w-]+", "webhooks/[hidden]", text)

    def payload(self, username, content, embed=None):
        data = {
            "username": clean(username, MAX_USERNAME) or "G.O.A.T.",
            "content": clean(content, MAX_CONTENT),
            "allowed_mentions": {"parse": []},
        }
        if embed:
            data["embeds"] = [embed]
        return data

    def send(self, username, content, embed=None):
        """Posts a message. Raises DiscordError (with no secret in it) when Discord does not accept it."""
        body = json.dumps(self.payload(username, content, embed)).encode()
        request = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json", "User-Agent": "goat-ai (private, 0.1)"})
        call(request, self.opener, self.sleep, self.hide, "the webhook")

    retry_delay = staticmethod(lambda error: retry_delay(error))
    explain = staticmethod(lambda code: explain(code, "the webhook"))


def retry_delay(error):
    try:
        wait = float(json.loads(error.read().decode() or "{}").get("retry_after"))
    except (ValueError, TypeError, OSError, AttributeError):
        return None
    return min(max(wait, 0.0), MAX_WAIT_SECONDS)


def explain(code, subject="the webhook"):
    webhook = subject == "the webhook"
    return {400: "Discord rejected the message", 401: "{} is not valid".format(subject),
            403: "not allowed to post there" if webhook else "the bot cannot see or write in that channel",
            404: "the webhook was deleted or the URL is wrong" if webhook else "that channel or message was not found",
            429: "rate limited"}.get(code, "see Discord status")


def call(request, opener, sleep, hide, subject, want_body=False):
    """Sends one request with a few retries for rate limits and Discord hiccups. Returns the body when asked.

    Raises DiscordError. `hide` removes the secret from any text before it goes into an error."""
    for attempt in range(RETRIES + 1):
        try:
            with opener(request, TIMEOUT_SECONDS) as response:
                return response.read() if want_body else None
        except urllib.error.HTTPError as error:
            wait = retry_delay(error)
            if error.code == 429 and attempt < RETRIES and wait is not None:
                sleep(wait)
                continue
            if error.code >= 500 and attempt < RETRIES:
                sleep(2)
                continue
            raise DiscordError("Discord returned HTTP {} ({}).".format(error.code, explain(error.code, subject)))
        except (urllib.error.URLError, OSError) as error:
            if attempt < RETRIES:
                sleep(2)
                continue
            raise DiscordError("Could not reach Discord: {}".format(hide(getattr(error, "reason", error))))


class Bot:
    """Reads and writes channel messages as the bot (REST only: no gateway connection, no extra libraries)."""

    def __init__(self, token, opener=None, sleep=time.sleep):
        token = (token or "").strip()
        if not token or re.search(r"\s", token):
            raise DiscordError("{} is missing or does not look like a bot token.".format(BOT_TOKEN_KEY))
        self.token = token
        self.opener = opener or Webhook.open
        self.sleep = sleep

    def hide(self, text):
        return str(text).replace(self.token, "[token]")

    def request(self, method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(API + path, data=data, method=method, headers={
            "Authorization": "Bot " + self.token, "Content-Type": "application/json",
            "User-Agent": "DiscordBot (goat-ai, 0.1)"})
        body = call(request, self.opener, self.sleep, self.hide, "the bot token", want_body=True)
        try:
            return json.loads((body or b"").decode() or "null")
        except ValueError:
            raise DiscordError("Discord returned something that is not JSON.")

    def me(self):
        """The bot's own account: {"id", "username", ...}."""
        return self.request("GET", "/users/@me") or {}

    def application_flags(self):
        """The application's flag bits (includes whether Message Content Intent is on)."""
        return int((self.request("GET", "/applications/@me") or {}).get("flags") or 0)

    def channels(self, guild_id):
        """The text channels of the server: [{"id", "name", ...}]."""
        self.need_id(guild_id, GUILD_KEY)
        return [c for c in self.request("GET", "/guilds/{}/channels".format(guild_id)) or [] if c.get("type") == 0]

    def newest_message_id(self, channel_id):
        """The id of the newest message in the channel, or "0" when it is empty."""
        self.need_id(channel_id, "channel id")
        found = self.request("GET", "/channels/{}/messages?limit=1".format(channel_id)) or []
        return str(found[0]["id"]) if found else "0"

    def messages_after(self, channel_id, after, limit=50):
        """Messages newer than the id `after`, oldest first."""
        self.need_id(channel_id, "channel id")
        self.need_id(after, "message id", allow_zero=True)
        found = self.request("GET", "/channels/{}/messages?after={}&limit={}".format(channel_id, after, limit)) or []
        return sorted(found, key=lambda message: int(message["id"]))

    def send(self, channel_id, content, reply_to=None):
        """Posts a plain message in the channel. Never pings anyone."""
        self.need_id(channel_id, "channel id")
        payload = {"content": clean(content, MAX_CONTENT), "allowed_mentions": {"parse": []}}
        if reply_to:
            payload["message_reference"] = {"message_id": str(reply_to), "fail_if_not_exists": False}
        self.request("POST", "/channels/{}/messages".format(channel_id), payload)

    @staticmethod
    def need_id(value, name, allow_zero=False):
        if not (SNOWFLAKE.match(str(value or "")) or (allow_zero and str(value) == "0")):
            raise DiscordError("The {} is missing or not a Discord id.".format(name))


def from_settings(values):
    """A Webhook from the env-file values, or None when no webhook is set."""
    url = (values or {}).get(WEBHOOK_KEY, "").strip()
    return Webhook(url) if url else None


def bot_from_settings(values):
    """A Bot from the env-file values, or None when there is no bot token."""
    token = (values or {}).get(BOT_TOKEN_KEY, "").strip()
    return Bot(token) if token else None
