"""Tests for the Discord messages: the webhook client, what the team says, when it says it, and the safety rules."""

import contextlib
import io
import json
import os
import ssl
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest import mock

from goat import board as fb
from goat import cli, coordinator, messenger, team
from goat import discord as dc
from goat.tests.fakes import FakeClient, raw_item

URL = "https://discord.com/api/webhooks/123456789012345678/abcDEF-_ghi123"
NOW = datetime(2026, 11, 2, 12, 0, 0, tzinfo=timezone.utc)
SHARED = {"agents": {
    "intake": {"name": "Maya"}, "plan": {"name": "Theo"}, "implement": {"name": "Jordan"}, "review": {"name": "Priya"},
    "dispatcher": {"name": "Dex"}}}


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b"{}"):
    return urllib.error.HTTPError(URL, code, "x", {}, io.BytesIO(body))


class Opener:
    """A fake network: answers with the scripted responses (or raises them), and records each request."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        step = self.script.pop(0) if self.script else Response()
        if isinstance(step, Exception):
            raise step
        return step


class FakeWebhook:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, username, content, embed=None):
        if self.fail:
            raise dc.DiscordError("Discord returned HTTP 500 (see Discord status).")
        self.sent.append((username, content, embed))


def item_of(number="5", **kwargs):
    return fb.GoatItem.from_github(raw_item(number, **kwargs))


class NoRealDiscordInTests(unittest.TestCase):
    def test_the_test_run_cannot_see_the_developers_settings(self):
        self.assertEqual(dc.settings(), {})
        self.assertIsNone(dc.from_settings(dc.settings()))
        self.assertIn("goat-tests-", dc.ENV_FILE)


class WebhookTests(unittest.TestCase):
    def webhook(self, *script):
        opener = Opener(*script)
        slept = []
        return dc.Webhook(URL, opener=opener, sleep=slept.append), opener, slept

    def test_only_a_discord_webhook_url_is_accepted(self):
        for bad in ("", None, "http://discord.com/api/webhooks/1/x", "https://example.com/api/webhooks/1/x", "nonsense"):
            with self.assertRaises(dc.DiscordError):
                dc.Webhook(bad)

    def test_a_message_is_posted_as_json_with_a_name_and_mentions_turned_off(self):
        hook, opener, _ = self.webhook()
        hook.send("Theo · Plan agent", "Plan is ready", {"title": "#5 Thing"})
        request = opener.requests[0]
        self.assertEqual((request.get_method(), request.full_url), ("POST", URL))
        body = json.loads(request.data)
        self.assertEqual(body["username"], "Theo · Plan agent")
        self.assertEqual(body["content"], "Plan is ready")
        self.assertEqual(body["allowed_mentions"], {"parse": []})
        self.assertEqual(body["embeds"], [{"title": "#5 Thing"}])
        self.assertEqual(request.get_header("Content-type"), "application/json")

    def test_text_from_an_issue_can_never_ping_everyone(self):
        hook, opener, _ = self.webhook()
        hook.send("Maya", "@everyone @here <@&123> look at this")
        self.assertEqual(json.loads(opener.requests[0].data)["allowed_mentions"], {"parse": []})

    def test_text_is_cleaned_and_cut_to_discords_limits(self):
        self.assertEqual(dc.clean("a\x07b\x00c", 10), "a b c")
        self.assertEqual(len(dc.clean("x" * 5000, dc.MAX_CONTENT)), dc.MAX_CONTENT)
        self.assertTrue(dc.clean("x" * 5000, 100).endswith("…"))
        hook, opener, _ = self.webhook()
        hook.send("n" * 500, "c" * 5000)
        body = json.loads(opener.requests[0].data)
        self.assertLessEqual(len(body["username"]), dc.MAX_USERNAME)
        self.assertLessEqual(len(body["content"]), dc.MAX_CONTENT)

    def test_a_blank_name_falls_back(self):
        hook, opener, _ = self.webhook()
        hook.send("", "hi")
        self.assertEqual(json.loads(opener.requests[0].data)["username"], "G.O.A.T.")

    def test_rate_limits_are_waited_out_and_retried(self):
        hook, opener, slept = self.webhook(http_error(429, b'{"retry_after": 1.5}'))
        hook.send("Dex", "hi")
        self.assertEqual((len(opener.requests), slept), (2, [1.5]))

    def test_a_huge_retry_after_is_capped(self):
        hook, _, slept = self.webhook(http_error(429, b'{"retry_after": 9999}'))
        hook.send("Dex", "hi")
        self.assertEqual(slept, [dc.MAX_WAIT_SECONDS])

    def test_server_errors_are_retried_then_reported(self):
        hook, opener, _ = self.webhook(http_error(500), http_error(502), http_error(503))
        with self.assertRaises(dc.DiscordError) as raised:
            hook.send("Dex", "hi")
        self.assertEqual(len(opener.requests), dc.RETRIES + 1)
        self.assertIn("HTTP 503", str(raised.exception))

    def test_client_errors_are_not_retried_and_say_what_to_check(self):
        for code, words in ((401, "not valid"), (404, "deleted"), (403, "not allowed")):
            hook, opener, _ = self.webhook(http_error(code))
            with self.assertRaises(dc.DiscordError) as raised:
                hook.send("Dex", "hi")
            self.assertEqual(len(opener.requests), 1)
            self.assertIn(words, str(raised.exception))

    def test_a_network_failure_is_reported_without_the_url(self):
        hook, _, _ = self.webhook(*[urllib.error.URLError("failed talking to " + URL)] * 3)
        with self.assertRaises(dc.DiscordError) as raised:
            hook.send("Dex", "hi")
        self.assertNotIn("abcDEF", str(raised.exception))
        self.assertNotIn(URL, str(raised.exception))

    def test_no_error_message_ever_contains_the_secret_part_of_the_url(self):
        hook, _, _ = self.webhook(http_error(404))
        with self.assertRaises(dc.DiscordError) as raised:
            hook.send("Dex", "hi")
        self.assertNotIn("abcDEF", str(raised.exception))
        self.assertEqual(hook.hide("see " + URL + " and webhooks/1/zzz"), "see [webhook] and webhooks/[hidden]")

    def test_settings_and_from_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "discord.env")
            with open(path, "w") as f:
                f.write("DISCORD_WEBHOOK_DISPATCH={}\nOTHER=1\n".format(URL))
            self.assertIsInstance(dc.from_settings(dc.settings(path)), dc.Webhook)
        self.assertIsNone(dc.from_settings({}))
        self.assertIsNone(dc.from_settings({"DISCORD_WEBHOOK_DISPATCH": "  "}))

    def test_certificate_checking_is_never_switched_off(self):
        self.assertEqual(dc.ssl_context().verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(dc.ssl_context().check_hostname)

    def test_without_python_certificates_the_system_bundle_is_used(self):
        import types
        empty = types.SimpleNamespace(cafile=None, capath=None)
        used = []
        with mock.patch.object(ssl, "get_default_verify_paths", return_value=empty), \
                mock.patch.object(dc.os.path, "isfile", lambda path: path == "/etc/ssl/cert.pem"), \
                mock.patch.object(ssl, "create_default_context", lambda **kwargs: used.append(kwargs) or "context"):
            self.assertEqual(dc.ssl_context(), "context")
        self.assertEqual(used, [{"cafile": "/etc/ssl/cert.pem"}])


class WaitingMessageTests(unittest.TestCase):
    def message(self, status, stage="plan", summary="A short summary.", owner="Pat", size="M"):
        item = item_of(stage=stage, goat_status=status, size=size, title="Places schema")
        return messenger.waiting_message(item, summary, SHARED, owner)

    def test_each_state_is_spoken_by_the_right_teammate(self):
        self.assertEqual(self.message("tier-review", "intake")[0], "Maya · Intake agent")
        self.assertEqual(self.message("plan-review", "plan")[0], "Theo · Plan agent")
        self.assertEqual(self.message("merge-review", "review")[0], "Priya · Review agent")

    def test_a_question_or_chat_comes_from_whoever_was_working(self):
        self.assertEqual(self.message("answer-question", "implement")[0], "Jordan · Implement agent")
        self.assertEqual(self.message("chat", "plan")[0], "Theo · Plan agent")

    def test_the_message_names_the_owner_and_says_what_is_needed(self):
        _, content, _ = self.message("plan-review")
        self.assertEqual(content, "Pat, the plan is ready for you.")
        self.assertEqual(self.message("plan-review", owner="")[1], "The plan is ready for you.")

    def test_the_embed_links_the_issue_and_shows_summary_and_next_step(self):
        _, _, embed = self.message("plan-review")
        self.assertEqual(embed["title"], "#5 Places schema")
        self.assertEqual(embed["url"], "https://github.com/me/app/issues/5")
        self.assertIn("A short summary.", embed["description"])
        self.assertIn("**What to do:** Review the plan", embed["description"])
        self.assertIn("goat:approved", embed["description"])
        self.assertIn("size M", embed["footer"]["text"])

    def test_no_summary_still_gives_a_useful_message(self):
        _, _, embed = self.message("tier-review", "intake", summary="")
        self.assertTrue(embed["description"].startswith("**What to do:**"))

    def test_a_long_summary_is_cut(self):
        _, _, embed = self.message("plan-review", summary="word " * 2000)
        self.assertLessEqual(len(embed["description"]), dc.MAX_DESCRIPTION + 300)

    def test_dispatcher_message(self):
        username, content, embed = messenger.dispatcher_message("the PR is merged.", SHARED, "Pat", "#5 Thing", "https://x")
        self.assertEqual((username, content), ("Dex · Dispatcher", "Pat, the PR is merged."))
        self.assertEqual((embed["title"], embed["url"]), ("#5 Thing", "https://x"))
        self.assertIsNone(messenger.dispatcher_message("hi", SHARED, "", None)[2])


class TellWaitingTests(unittest.TestCase):
    def setUp(self):
        self.webhook = FakeWebhook()
        self.logs = []
        self.state = {}

    def tell(self, items, webhook=None, dry_run=False, now=NOW, user="me"):
        candidates = [("/code/a", item) for item in items]
        return messenger.tell_waiting(webhook or self.webhook, candidates, user, self.state, lambda path, item: "Summary of {}".format(item.id),
                                      SHARED, "Pat", now, dry_run, self.logs.append)

    def test_a_newly_waiting_issue_is_announced_once(self):
        waiting = item_of(stage="plan", goat_status="plan-review")
        self.assertEqual(self.tell([waiting]), 1)
        self.assertEqual(self.tell([waiting]), 0)
        self.assertEqual(self.tell([waiting]), 0)
        self.assertEqual(len(self.webhook.sent), 1)
        self.assertIn("Summary of 5", self.webhook.sent[0][2]["description"])

    def test_a_new_waiting_state_is_announced_again(self):
        self.tell([item_of(stage="intake", goat_status="tier-review")])
        self.tell([item_of(stage="plan", goat_status="plan-review")])
        self.assertEqual(len(self.webhook.sent), 2)

    def test_a_second_round_of_the_same_gate_is_announced_again(self):
        waiting = item_of(stage="plan", goat_status="plan-review")
        self.tell([waiting])
        self.tell([item_of(stage="plan", goat_status="working", claim="plan|x", expires=fb.format_date(NOW + timedelta(minutes=5)))])
        self.assertEqual(self.state["notified"], {})
        self.tell([waiting])
        self.assertEqual(len(self.webhook.sent), 2)

    def test_states_that_need_no_person_are_never_announced(self):
        for status in ("ready", "working", "approved", "done", "stalled"):
            self.tell([item_of(stage="plan", goat_status=status)])
        self.assertEqual(self.webhook.sent, [])

    def test_other_peoples_issues_and_live_claims_are_skipped(self):
        self.tell([item_of(stage="plan", goat_status="plan-review", assignees=("someone",))])
        live = fb.format_date(NOW + timedelta(minutes=5))
        self.tell([item_of("6", stage="plan", goat_status="plan-review", claim="plan|x", expires=live)])
        self.assertEqual(self.webhook.sent, [])

    def test_each_issue_is_tracked_separately(self):
        self.tell([item_of("5", stage="plan", goat_status="plan-review"), item_of("6", stage="plan", goat_status="plan-review")])
        self.assertEqual(len(self.webhook.sent), 2)
        self.assertEqual(len(self.state["notified"]), 2)

    def test_a_failure_is_logged_and_retried_next_turn(self):
        waiting = item_of(stage="plan", goat_status="plan-review")
        self.assertEqual(self.tell([waiting], webhook=FakeWebhook(fail=True)), 0)
        self.assertEqual(self.state["notified"], {})
        self.assertTrue(any("could not announce #5" in line for line in self.logs), self.logs)
        self.assertEqual(self.tell([waiting]), 1)

    def test_a_dry_run_sends_and_remembers_nothing(self):
        self.tell([item_of(stage="plan", goat_status="plan-review")], dry_run=True)
        self.assertEqual(self.webhook.sent, [])
        self.assertEqual(self.state["notified"], {})
        self.assertTrue(any("Would tell Discord" in line for line in self.logs), self.logs)


class CoordinatorDiscordTests(unittest.TestCase):
    def setUp(self):
        self.logs = []
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.env_file = os.path.join(directory.name, "discord.env")
        for patcher in (mock.patch.object(coordinator, "log", self.logs.append),
                        mock.patch.object(dc, "ENV_FILE", self.env_file)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_env(self, text):
        with open(self.env_file, "w") as f:
            f.write(text)

    def test_no_settings_means_no_messages(self):
        self.assertIsNone(coordinator.discord_webhook({"coordinator": {}}))

    def test_a_webhook_is_used_when_it_is_set(self):
        self.write_env("DISCORD_WEBHOOK_DISPATCH={}\n".format(URL))
        self.assertIsInstance(coordinator.discord_webhook({"coordinator": {}}), dc.Webhook)

    def test_it_can_be_turned_off_in_the_config(self):
        self.write_env("DISCORD_WEBHOOK_DISPATCH={}\n".format(URL))
        self.assertIsNone(coordinator.discord_webhook({"coordinator": {"discord": False}}))

    def test_a_bad_url_is_logged_and_ignored_without_showing_it(self):
        self.write_env("DISCORD_WEBHOOK_DISPATCH=https://example.com/not-a-webhook\n")
        self.assertIsNone(coordinator.discord_webhook({"coordinator": {}}))
        self.assertTrue(any("does not look like a Discord webhook" in line for line in self.logs), self.logs)
        self.assertFalse(any("example.com" in line for line in self.logs), self.logs)

    def test_say_never_raises_and_does_nothing_without_a_webhook(self):
        coordinator.say(None, ("a", "b", None), "x")
        coordinator.say(FakeWebhook(fail=True), ("a", "b", None), "the test message")
        self.assertTrue(any("could not send the test message" in line for line in self.logs), self.logs)

    def test_a_stalled_issue_is_announced_by_dex(self):
        client = FakeClient(raw_item("5", stage="plan", goat_status="ready", title="Add login"))
        item = item_of(stage="plan", goat_status="ready", title="Add login")
        webhook = FakeWebhook()
        attempts = {coordinator.attempt_key(item): coordinator.MAX_ATTEMPTS} if hasattr(coordinator, "attempt_key") else None
        from goat.choose import attempt_key
        attempts = {attempt_key(item): coordinator.MAX_ATTEMPTS}
        config = {"coordinator": {"notifications": False}, "owner": {"name": "Pat"}}
        with mock.patch.object(coordinator, "notify"):
            coordinator.escalate_stalled([("/code/a", item)], "me", [("/code/a", {}, client, {}, [item])], set(), attempts,
                                         NOW, config, False, webhook)
        username, content, embed = webhook.sent[0]
        self.assertEqual(username, "Dex · Dispatcher")
        self.assertIn("stalled", content)
        self.assertEqual(embed["url"], item.url)

    def test_a_merge_is_announced_by_dex(self):
        client = FakeClient(raw_item("5", stage="review", goat_status="merge-review"))
        client.merged_prs = [{"number": 9, "url": "u", "branch": "goat/5-x", "base": "main", "merge_commit": "c", "mergedAt": "t"}]
        items = [fb.GoatItem.from_github(raw) for raw in client.board_items()]
        webhook = FakeWebhook()
        coordinator.advance_merged_boards([("/code/a", {}, client, {}, items)], "me", False, webhook, "Pat")
        username, content, _ = webhook.sent[0]
        self.assertEqual(username, "Dex · Dispatcher")
        self.assertIn("Pat, the PR for #5 is merged", content)

    def test_tick_announces_a_waiting_issue_once_and_a_dry_run_nothing(self):
        item = item_of(stage="plan", goat_status="plan-review")
        client = FakeClient(raw_item("5", stage="plan", goat_status="plan-review"))
        boards = [("/code/a", {}, client, {"doneLabel": "done"}, [item])]
        config = {"repos": {}, "coordinator": {"maxAgents": 3, "maxAgentsPerRepo": None, "maxAgentsByRole": {}, "autoAdvanceMerged": False,
                                               "retro": {"aggregateMinRetros": 15, "aggregateMinDays": 3}}, "owner": {"name": "Pat"}}
        webhook = FakeWebhook()
        state = {"runs": [], "attempts": {}}
        with mock.patch.object(coordinator, "check_repos", lambda config: {}), \
                mock.patch.object(coordinator, "read_boards", lambda repos: ([("/code/a", item)], "me", boards, False)), \
                mock.patch.object(coordinator, "prune_finished"), mock.patch.object(coordinator, "clean_up_boards"), \
                mock.patch.object(coordinator, "start_launches"), mock.patch.object(coordinator, "discord_webhook", lambda config: webhook):
            coordinator.tick(config, state, False)
            coordinator.tick(config, state, False)
            coordinator.tick(config, state, True)
        self.assertEqual(len(webhook.sent), 1)
        self.assertEqual(webhook.sent[0][0], "Theo · Plan agent")
        self.assertTrue(any("Would tell Discord" in line for line in self.logs) is False)  # already announced: nothing to say


class DiscordTestCommandTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.env_file = os.path.join(directory.name, "discord.env")
        config_file = os.path.join(directory.name, "config.json")
        with open(config_file, "w") as f:
            json.dump({"owner": {"name": "Pat", "title": "CEO"}}, f)
        self.sent = []
        for patcher in (mock.patch.object(dc, "ENV_FILE", self.env_file),
                        mock.patch.object(coordinator, "CONFIG_FILE", config_file),
                        mock.patch.object(dc.Webhook, "send", lambda hook, username, content, embed=None: self.sent.append((username, content)))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["discord-test", *argv])
        return code, out.getvalue(), err.getvalue()

    def configure(self):
        with open(self.env_file, "w") as f:
            f.write("DISCORD_WEBHOOK_DISPATCH={}\n".format(URL))

    def test_sends_one_message_as_dex_by_default(self):
        self.configure()
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(self.sent[0][0], "Dex · Dispatcher")
        self.assertTrue(self.sent[0][1].startswith("Pat, this is Dex."))
        self.assertNotIn("abcDEF", out)

    def test_can_be_signed_by_another_teammate(self):
        self.configure()
        self.run_cli("--as", "plan")
        self.assertEqual(self.sent[0][0], "Theo · Plan agent")

    def test_without_a_webhook_it_says_how_to_set_one_up(self):
        code, _, err = self.run_cli()
        self.assertEqual(code, 3)
        self.assertIn("DISCORD_WEBHOOK_DISPATCH", err)
        self.assertEqual(self.sent, [])

    def test_a_discord_failure_is_one_line_and_has_no_secret(self):
        self.configure()
        with mock.patch.object(dc.Webhook, "send", side_effect=dc.DiscordError("Discord returned HTTP 404 (the webhook was deleted or the URL is wrong).")):
            code, _, err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("HTTP 404", err)
        self.assertNotIn("abcDEF", err)


class AgentsCannotReachDiscordTests(unittest.TestCase):
    def test_the_settings_file_and_discord_are_denied_to_agents(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "agent-settings.json")) as f:
            deny = json.load(f)["permissions"]["deny"]
        for rule in ("Read(~/.goat-ai/discord.env)", "Bash(cat *discord.env*)", "Bash(curl *discord.com*)",
                     "WebFetch(domain:discord.com)", "Bash(python3 */goat.py discord-test*)"):
            self.assertIn(rule, deny)


if __name__ == "__main__":
    unittest.main()
