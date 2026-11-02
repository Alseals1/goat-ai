"""Sends messages to a Discord channel through a webhook. Outbound only.

The webhook URL is in ~/.goat-ai/discord.env (outside the repository), as DISCORD_WEBHOOK_DISPATCH. It is a secret:
anyone who has it can post to the channel. This module never prints it, never puts it in an error message, and the
file is never committed. A webhook lets each message carry its own name, so Maya, Theo and Priya each post as
themselves.

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
        for attempt in range(RETRIES + 1):
            try:
                with self.opener(request, TIMEOUT_SECONDS):
                    return
            except urllib.error.HTTPError as error:
                retry_after = self.retry_delay(error)
                if error.code == 429 and attempt < RETRIES and retry_after is not None:
                    self.sleep(retry_after)
                    continue
                if error.code >= 500 and attempt < RETRIES:
                    self.sleep(2)
                    continue
                raise DiscordError("Discord returned HTTP {} ({}).".format(error.code, self.explain(error.code)))
            except (urllib.error.URLError, OSError) as error:
                if attempt < RETRIES:
                    self.sleep(2)
                    continue
                raise DiscordError("Could not reach Discord: {}".format(self.hide(getattr(error, "reason", error))))

    @staticmethod
    def retry_delay(error):
        try:
            wait = float(json.loads(error.read().decode() or "{}").get("retry_after"))
        except (ValueError, TypeError, OSError, AttributeError):
            return None
        return min(max(wait, 0.0), MAX_WAIT_SECONDS)

    @staticmethod
    def explain(code):
        return {400: "Discord rejected the message", 401: "the webhook is not valid", 403: "not allowed to post there",
                404: "the webhook was deleted or the URL is wrong", 429: "rate limited"}.get(code, "see Discord status")


def from_settings(values):
    """A Webhook from the env-file values, or None when no webhook is set."""
    url = (values or {}).get(WEBHOOK_KEY, "").strip()
    return Webhook(url) if url else None
