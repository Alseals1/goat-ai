"""Tests for approving from Discord: parsing what the owner types, the safety checks, the label changes, and the
channel reader. No test touches the network or the developer's real settings."""

import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest import mock

from goat import board as fb
from goat import cli, coordinator, inbox
from goat import discord as dc
from goat.github import GitHubError
from goat.tests.fakes import FakeClient, ME, raw_item

TOKEN = "MTIzNDU2.Tok3n-secret_value.abcdefghijklmnopqrstuvwxyz"
OWNER_ID = "111111111111111111"
CHANNEL = "222222222222222222"
AFTER = "100000000000000005"
GUILD = "333333333333333333"
SHARED = {"agents": {"intake": {"name": "Maya"}, "plan": {"name": "Theo"}, "implement": {"name": "Jordan"},
                     "review": {"name": "Priya"}, "dispatcher": {"name": "Dex"}}}
OWNER = {"name": "Alandis", "title": "CEO"}
NOW = datetime(2026, 11, 2, 12, 0, 0, tzinfo=timezone.utc)


def snowflake(when, sequence=0):
    """A Discord id for a message sent at `when` (a datetime)."""
    return str((int(when.timestamp() * 1000) - inbox.DISCORD_EPOCH_MS) << 22 | sequence)


def message(content, when=NOW, sequence=0, author=OWNER_ID, bot=False, channel=CHANNEL, kind=0, webhook_id=None):
    found = {"id": snowflake(when, sequence), "channel_id": channel, "type": kind, "content": content,
             "author": {"id": author, "bot": bot}}
    if webhook_id:
        found["webhook_id"] = webhook_id
    return found


def goat_item(number, **kwargs):
    return fb.GoatItem.from_github(raw_item(number, **kwargs))


def found_for(client, number, user=ME):
    return [(client, fb.GoatItem.from_github(client.get_item(number)), user["id"])]


class ParseTests(unittest.TestCase):
    def test_the_commands(self):
        cases = {
            "approve 9": ("approve", 9, ""),
            "start 9": ("start", 9, ""),
            "Start #9": ("start", 9, ""),
            "Start 9: assign me": ("start", 9, ""),
            "start 9 assign it to me": ("start", 9, ""),
            "start 9 - assigned to me.": ("start", 9, ""),
            "Approve #9": ("approve", 9, ""),
            "approved 12": ("approve", 12, ""),
            "!approve 9": ("approve", 9, ""),
            "send back 9: add a skip button": ("send_back", 9, "add a skip button"),
            "Send Back #9 - add a skip button": ("send_back", 9, "add a skip button"),
            "sendback 9 add a skip button": ("send_back", 9, "add a skip button"),
            "send-back 9: two\nlines": ("send_back", 9, "two\nlines"),
            "answer 7: yes, use Birmingham": ("answer", 7, "yes, use Birmingham"),
            "status": ("status", None, ""),
            "Status?": ("status", None, ""),
            "help": ("help", None, ""),
        }
        for text, expected in cases.items():
            self.assertEqual(tuple(inbox.parse(text)), expected, text)

    def test_nothing_to_act_on(self):
        for text in ("", "   ", None):
            self.assertIsNone(inbox.parse(text))

    def test_an_incomplete_command_explains_itself_and_does_nothing(self):
        for text in ("approve", "approve nine", "approve 9 looks great", "send back 9", "send back", "send back: x",
                     "answer 9", "answer yes", "approve 99999999", "approve 9x", "start", "start nine",
                     "start 9: assign Bob", "start 9 and add a skip button", "start the day"):
            command = inbox.parse(text)
            self.assertEqual(command.kind, "unclear", text)
            self.assertIn("`", command.text)

    def test_chatter_is_not_a_command(self):
        command = inbox.parse("good morning team")
        self.assertEqual(command.kind, "unclear")
        self.assertIn("approve 9", command.text)


class ApproveTests(unittest.TestCase):
    def apply(self, client, kind, number, text="", problems=()):
        return inbox.apply(inbox.Command(kind, number, text), found_for(client, number) if number in client.items else [],
                           list(problems), SHARED, OWNER, NOW)

    def test_a_size_checkpoint_is_approved_with_one_label_swap_and_a_note(self):
        client = FakeClient(raw_item(9, stage="intake", goat_status="tier-review", size="M"))
        outcome = self.apply(client, "approve", 9)
        self.assertEqual(client.labels_of(9), ["goat:approved", "size:M", "stage:intake"])
        self.assertTrue(outcome.run_turn)
        self.assertIn("Theo (Plan agent)", outcome.text)
        note = client.comment_bodies(9)[-1]
        self.assertIn(inbox.MARKER, note)
        self.assertIn("Alandis (CEO) approved the size checkpoint in Discord", note)
        self.assertIn("Dex · Dispatcher", note)

    def test_a_plan_checkpoint_goes_to_the_implement_agent(self):
        client = FakeClient(raw_item(9, stage="plan", goat_status="plan-review", size="M"))
        outcome = self.apply(client, "approve", 9)
        self.assertEqual(client.labels_of(9), ["goat:approved", "size:M", "stage:plan"])
        self.assertIn("Jordan (Implement agent)", outcome.text)

    def test_the_only_label_that_changes_is_the_gate_one(self):
        client = FakeClient(raw_item(9, stage="plan", goat_status="plan-review", size="L", priority="first",
                                     labels=["bug"]))
        self.apply(client, "approve", 9)
        self.assertEqual(client.writes[0][2], {"add": ["goat:approved"], "remove": ["goat:plan-review"]})
        self.assertEqual(client.labels_of(9), ["bug", "goat:approved", "prio:first", "size:L", "stage:plan"])

    def test_a_merge_can_never_be_approved_from_discord(self):
        client = FakeClient(raw_item(9, stage="review", goat_status="merge-review"))
        before = client.labels_of(9)
        outcome = self.apply(client, "approve", 9)
        self.assertFalse(outcome.run_turn)
        self.assertEqual(client.labels_of(9), before)
        self.assertEqual(client.writes, [])
        self.assertIn("Merging happens on GitHub", outcome.text)
        self.assertIn("send back 9", outcome.text)

    def test_approve_does_nothing_for_every_other_state(self):
        for status in ("ready", "working", "approved", "answer-question", "chat", "stalled", "done"):
            client = FakeClient(raw_item(9, stage="plan", goat_status=status))
            outcome = self.apply(client, "approve", 9)
            self.assertFalse(outcome.run_turn, status)
            self.assertEqual(client.writes, [], status)
            self.assertIn("Nothing changed", outcome.text)

    def test_the_hint_for_a_question_and_a_chat_and_a_stall(self):
        question = self.apply(FakeClient(raw_item(9, goat_status="answer-question")), "approve", 9)
        self.assertIn("answer 9: your answer", question.text)
        chat = self.apply(FakeClient(raw_item(9, goat_status="chat")), "approve", 9)
        self.assertIn("goat.py chat 9", chat.text)
        stalled = self.apply(FakeClient(raw_item(9, goat_status="stalled")), "approve", 9)
        self.assertIn("goat:ready", stalled.text)

    def test_an_issue_with_a_live_agent_claim_is_left_alone(self):
        later = (NOW + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        client = FakeClient(raw_item(9, stage="intake", goat_status="tier-review", claim="plan|abc", expires=later))
        outcome = self.apply(client, "approve", 9)
        self.assertEqual(client.writes, [])
        self.assertIn("working on it", outcome.text)

    def test_someone_elses_closed_or_missing_issue_is_not_touched(self):
        others = FakeClient(raw_item(9, stage="intake", goat_status="tier-review", assignees=("someone",)))
        self.assertIn("can't find", self.apply(others, "approve", 9).text)
        self.assertEqual(others.writes, [])
        closed = FakeClient(raw_item(9, stage="intake", goat_status="tier-review", state="closed"))
        self.assertIn("can't find", self.apply(closed, "approve", 9).text)
        self.assertEqual(closed.writes, [])
        plain = FakeClient(raw_item(9, stage="", goat_status=""))
        self.assertIn("can't find", self.apply(plain, "approve", 9).text)
        missing = FakeClient()
        self.assertIn("can't find", self.apply(missing, "approve", 9).text)

    def test_a_github_problem_says_so_and_changes_nothing(self):
        outcome = inbox.apply(inbox.Command("approve", 9, ""), [], ["me/app: gh timed out"], SHARED, OWNER, NOW)
        self.assertIn("could not read GitHub", outcome.text)
        self.assertFalse(outcome.run_turn)

    def test_the_same_number_in_two_projects_is_refused(self):
        a = FakeClient(raw_item(9, stage="intake", goat_status="tier-review"))
        b = FakeClient(raw_item(9, stage="intake", goat_status="tier-review"))
        found = found_for(a, 9) + found_for(b, 9)
        outcome = inbox.apply(inbox.Command("approve", 9, ""), found, [], SHARED, OWNER, NOW)
        self.assertIn("more than one project", outcome.text)
        self.assertEqual((a.writes, b.writes), ([], []))

    def test_a_failed_note_does_not_undo_the_approval(self):
        client = FakeClient(raw_item(9, stage="intake", goat_status="tier-review"))
        client.create_comment = mock.Mock(side_effect=GitHubError("no"))
        outcome = self.apply(client, "approve", 9)
        self.assertIn("goat:approved", client.labels_of(9))
        self.assertIn("could not leave the note", outcome.text)
        self.assertTrue(outcome.run_turn)


class SendBackAndAnswerTests(unittest.TestCase):
    def apply(self, client, kind, number, text):
        return inbox.apply(inbox.Command(kind, number, text), found_for(client, number), [], SHARED, OWNER, NOW)

    def test_send_back_at_a_plan_checkpoint_comments_then_sets_ready(self):
        client = FakeClient(raw_item(9, stage="plan", goat_status="plan-review", size="M"))
        outcome = self.apply(client, "send_back", 9, "add a skip button")
        self.assertEqual([write[0] for write in client.writes], ["comment", "labels"])
        self.assertEqual(client.labels_of(9), ["goat:ready", "size:M", "stage:plan"])
        self.assertTrue(outcome.run_turn)
        self.assertIn("Theo (Plan agent)", outcome.text)
        note = client.comment_bodies(9)[-1]
        self.assertIn("add a skip button", note)
        self.assertIn("Sent from Discord by Alandis (CEO)", note)
        self.assertNotIn("🤖", note)

    def test_send_back_at_a_size_checkpoint_returns_to_intake(self):
        client = FakeClient(raw_item(9, stage="intake", goat_status="tier-review"))
        outcome = self.apply(client, "send_back", 9, "this is bigger than M")
        self.assertEqual(client.labels_of(9), ["goat:ready", "stage:intake"])
        self.assertIn("Maya (Intake agent)", outcome.text)

    def test_send_back_at_merge_review_goes_back_to_implement(self):
        client = FakeClient(raw_item(9, stage="review", goat_status="merge-review"))
        outcome = self.apply(client, "send_back", 9, "the button is the wrong color")
        self.assertEqual(client.labels_of(9), ["goat:ready", "stage:implement"])
        self.assertIn("Jordan (Implement agent)", outcome.text)
        self.assertTrue(outcome.run_turn)

    def test_the_note_is_posted_before_the_labels_change(self):
        client = FakeClient(raw_item(9, stage="plan", goat_status="plan-review"))
        client.create_comment = mock.Mock(side_effect=GitHubError("down"))
        with self.assertRaises(GitHubError):
            self.apply(client, "send_back", 9, "change it")
        self.assertEqual(client.labels_of(9), ["goat:plan-review", "stage:plan"])

    def test_a_label_failure_after_the_note_is_reported_without_a_false_start(self):
        client = FakeClient(raw_item(9, stage="plan", goat_status="plan-review"))
        client.set_labels = mock.Mock(side_effect=GitHubError("label failed"))
        outcome = self.apply(client, "send_back", 9, "change it")
        self.assertFalse(outcome.run_turn)
        self.assertIn("posted your note", outcome.text)
        self.assertIn("goat:ready", outcome.text)

    def test_answer_replies_to_a_question_and_sets_ready(self):
        client = FakeClient(raw_item(9, stage="plan", goat_status="answer-question"))
        outcome = self.apply(client, "answer", 9, "yes, use Birmingham")
        self.assertEqual(client.labels_of(9), ["goat:ready", "stage:plan"])
        self.assertIn("yes, use Birmingham", client.comment_bodies(9)[-1])
        self.assertTrue(outcome.run_turn)
        self.assertIn("answered", outcome.text)

    def test_answer_only_works_on_a_question_and_send_back_never_on_one(self):
        gate = FakeClient(raw_item(9, stage="plan", goat_status="plan-review"))
        self.assertIn("is for questions", self.apply(gate, "answer", 9, "hi").text)
        self.assertEqual(gate.writes, [])
        question = FakeClient(raw_item(9, stage="plan", goat_status="answer-question"))
        self.assertIn("answer 9: your answer", self.apply(question, "send_back", 9, "hi").text)
        self.assertEqual(question.writes, [])

    def test_send_back_does_nothing_for_a_working_ready_or_stalled_issue(self):
        for status in ("ready", "working", "approved", "chat", "stalled"):
            client = FakeClient(raw_item(9, stage="plan", goat_status=status))
            self.assertFalse(self.apply(client, "send_back", 9, "x").run_turn, status)
            self.assertEqual(client.writes, [], status)

    def test_control_characters_and_length_are_cleaned_from_the_note(self):
        client = FakeClient(raw_item(9, stage="plan", goat_status="plan-review"))
        self.apply(client, "send_back", 9, "bad\x07text" + "x" * 5000)
        note = client.comment_bodies(9)[-1]
        self.assertNotIn("\x07", note)
        self.assertLess(len(note), inbox.MAX_TEXT + 200)


class StartTests(unittest.TestCase):
    def apply(self, client, number=9, found=None, problems=()):
        found = found_for(client, number) if found is None and number in client.items else (found or [])
        return inbox.apply(inbox.Command("start", number, ""), found, list(problems), SHARED, OWNER, NOW)

    def backlog(self, **kwargs):
        return FakeClient(raw_item(9, stage="discovery", goat_status="", assignees=(), **kwargs))

    def test_a_backlog_issue_is_assigned_to_the_owner_and_released(self):
        client = self.backlog()
        outcome = self.apply(client)
        self.assertEqual(client.items[9]["assignees"], ["me"])
        self.assertEqual(client.labels_of(9), ["goat:ready", "stage:intake"])
        self.assertTrue(outcome.run_turn)
        self.assertIn("Maya (Intake agent)", outcome.text)
        self.assertIn("Assigned to you", outcome.text)
        self.assertEqual([write[0] for write in client.writes], ["assign", "labels", "comment"])
        self.assertIn("started this in Discord", client.comment_bodies(9)[-1])
        self.assertIn(inbox.MARKER, client.comment_bodies(9)[-1])

    def test_the_label_swap_keeps_everything_else(self):
        client = FakeClient(raw_item(9, stage="discovery", goat_status="", assignees=(), priority="first",
                                     labels=["bug"], milestone="v0.1"))
        self.apply(client)
        self.assertEqual(client.writes[1][2], {"add": ["goat:ready", "stage:intake"], "remove": ["stage:discovery"]})
        self.assertEqual(client.labels_of(9), ["bug", "goat:ready", "prio:first", "stage:intake"])

    def test_an_issue_with_no_labels_at_all_can_be_started(self):
        client = FakeClient(raw_item(9, stage="", goat_status="", assignees=()))
        self.apply(client)
        self.assertEqual(client.labels_of(9), ["goat:ready", "stage:intake"])

    def test_an_issue_already_assigned_to_the_owner_is_not_assigned_again(self):
        client = FakeClient(raw_item(9, stage="discovery", goat_status="", assignees=("me",)))
        self.assertTrue(self.apply(client).run_turn)
        self.assertEqual([write[0] for write in client.writes], ["labels", "comment"])

    def test_someone_elses_issue_is_not_taken_over(self):
        client = FakeClient(raw_item(9, stage="discovery", goat_status="", assignees=("sam",)))
        outcome = self.apply(client)
        self.assertEqual(client.writes, [])
        self.assertIn("assigned to sam", outcome.text)
        both = FakeClient(raw_item(9, stage="discovery", goat_status="", assignees=("me", "sam")))
        self.apply(both)
        self.assertEqual(both.writes, [])

    def test_an_issue_already_in_the_factory_is_left_alone(self):
        for status in ("ready", "working", "tier-review", "approved", "stalled", "done"):
            client = FakeClient(raw_item(9, stage="intake", goat_status=status, assignees=()))
            outcome = self.apply(client)
            self.assertFalse(outcome.run_turn, status)
            self.assertEqual(client.writes, [], status)
            self.assertIn("already in the factory", outcome.text)

    def test_only_the_backlog_can_be_started(self):
        for stage in ("intake", "plan", "implement", "review", "staging"):
            client = FakeClient(raw_item(9, stage=stage, goat_status="", assignees=()))
            outcome = self.apply(client)
            self.assertEqual(client.writes, [], stage)
            self.assertIn("not in the backlog", outcome.text)

    def test_a_closed_or_missing_issue_is_not_started(self):
        closed = FakeClient(raw_item(9, stage="discovery", goat_status="", assignees=(), state="closed"))
        self.assertIn("is closed", self.apply(closed).text)
        self.assertEqual(closed.writes, [])
        self.assertIn("can't find", self.apply(FakeClient()).text)

    def test_the_same_number_in_two_projects_is_refused(self):
        a, b = self.backlog(), self.backlog()
        outcome = self.apply(a, found=found_for(a, 9) + found_for(b, 9))
        self.assertIn("more than one project", outcome.text)
        self.assertEqual((a.writes, b.writes), ([], []))

    def test_a_github_problem_says_so(self):
        self.assertIn("could not read GitHub", self.apply(FakeClient(), problems=["me/app: timed out"]).text)

    def test_if_the_labels_fail_after_assigning_nothing_is_reported_as_started(self):
        client = self.backlog()
        client.set_labels = mock.Mock(side_effect=GitHubError("label failed"))
        outcome = self.apply(client)
        self.assertFalse(outcome.run_turn)
        self.assertIn("assigned #9 to you", outcome.text)
        self.assertIn("goat:ready", outcome.text)

    def test_if_the_assignment_fails_nothing_changes(self):
        client = self.backlog()
        client.add_assignees = mock.Mock(side_effect=GitHubError("no"))
        with self.assertRaises(GitHubError):
            self.apply(client)
        self.assertEqual(client.labels_of(9), ["stage:discovery"])

    def test_a_failed_note_does_not_undo_the_start(self):
        client = self.backlog()
        client.create_comment = mock.Mock(side_effect=GitHubError("no"))
        outcome = self.apply(client)
        self.assertTrue(outcome.run_turn)
        self.assertIn("could not leave the note", outcome.text)
        self.assertIn("goat:ready", client.labels_of(9))


class FakeBot:
    def __init__(self, messages=(), newest="0", fail=None):
        self.messages = list(messages)
        self.newest = newest
        self.fail = fail
        self.calls = []

    def newest_message_id(self, channel_id):
        self.calls.append(("newest", channel_id))
        if self.fail:
            raise self.fail
        return self.newest

    def messages_after(self, channel_id, after, limit=50):
        self.calls.append(("after", channel_id, after))
        if self.fail:
            raise self.fail
        return [m for m in self.messages if int(m["id"]) > int(after)]


class InboxTests(unittest.TestCase):
    def build(self, *messages, newest="0", channel=CHANNEL, client=None, wall=None, fail=None, locate_error=None,
              started=None):
        self.client = client or FakeClient(raw_item(9, stage="intake", goat_status="tier-review", size="M"))
        self.bot = FakeBot(messages, newest, fail)
        self.replies, self.logs, self.saved = [], [], []

        def locate(number):
            if locate_error:
                raise locate_error
            return (found_for(self.client, number) if number in self.client.items else []), []

        self.resolved = []

        def resolve():
            self.resolved.append(1)
            return channel

        self.clock = [0.0]
        self.box = inbox.Inbox(
            self.bot, OWNER_ID, resolve, locate,
            lambda: ([goat_item(9, stage="intake", goat_status="tier-review", title="Add trainers")], []),
            lambda text, message_id: self.replies.append((text, message_id)),
            lambda state: self.saved.append(json.loads(json.dumps(state))),
            SHARED, OWNER, self.logs.append, wall=wall or (lambda: NOW.timestamp() + 5), clock=lambda: self.clock[0],
            started=NOW.timestamp() - 60 if started is None else started)
        return self.box

    def poll(self, state=None):
        return self.box.poll(state if state is not None else {"inbox": {"after": "1"}})

    def test_the_first_look_answers_a_message_sent_just_after_starting(self):
        # The owner typed a command 18 seconds after the coordinator started, before its first look at the channel.
        started = NOW.timestamp() - 18
        self.build(message("approve 9"), started=started)
        state = {}
        self.assertTrue(self.box.poll(state))
        self.assertIn("goat:approved", self.client.labels_of(9))
        self.assertEqual(state["inbox"]["after"], message("approve 9")["id"])
        self.assertEqual(self.saved[0]["inbox"]["after"], inbox.snowflake_at(started - inbox.MAX_AGE_SECONDS))

    def test_a_message_sent_before_the_coordinator_started_is_not_run_and_gets_a_reply(self):
        before = NOW - timedelta(minutes=8)
        self.build(message("approve 9", when=before), message("help", when=NOW - timedelta(minutes=2), sequence=1))
        self.assertFalse(self.poll())
        self.assertEqual(self.client.writes, [])
        self.assertEqual(len(self.replies), 2)
        for text, _ in self.replies:
            self.assertIn("had not started listening", text)
        self.assertTrue(any("before the coordinator started" in line for line in self.logs))

    def test_messages_from_before_the_look_window_are_not_even_read(self):
        started = NOW.timestamp() + 3600
        self.build(message("approve 9", when=NOW), started=started)
        self.assertFalse(self.box.poll({}))
        self.assertEqual(self.replies, [])
        self.assertEqual(self.client.writes, [])

    def test_the_owner_approves_from_the_channel(self):
        command = message("approve 9")
        self.build(command)
        self.assertTrue(self.poll())
        self.assertIn("goat:approved", self.client.labels_of(9))
        self.assertEqual(len(self.replies), 1)
        text, reply_to = self.replies[0]
        self.assertEqual(reply_to, command["id"])
        self.assertTrue(text.startswith("Alandis, approved the size checkpoint"))
        self.assertIn("Theo (Plan agent)", text)

    def test_a_message_is_acted_on_once(self):
        command = message("approve 9")
        self.build(command)
        state = {"inbox": {"after": "1"}}
        self.assertTrue(self.box.poll(state))
        self.assertEqual(state["inbox"]["after"], command["id"])
        self.assertFalse(self.box.poll(state))
        self.assertEqual(len([w for w in self.client.writes if w[0] == "labels"]), 1)

    def test_the_cursor_moves_and_is_saved_before_anything_is_done(self):
        command = message("approve 9")
        self.build(command, locate_error=GitHubError("boom"))
        original = self.box.locate

        def locate(number):
            # By now the message must already be marked as seen, and saved.
            self.assertEqual(self.saved[-1]["inbox"]["after"], command["id"])
            return original(number)

        self.box.locate = locate
        self.poll()
        self.assertEqual(self.client.writes, [])
        self.assertIn("nothing was changed", self.replies[0][0])

    def test_nobody_but_the_owner_can_command_it(self):
        self.build(message("approve 9", author="999999999999999999"))
        self.assertFalse(self.poll())
        self.assertEqual((self.client.writes, self.replies), ([], []))
        self.assertTrue(any("another account" in line for line in self.logs))
        self.assertNotIn("approve", " ".join(self.logs))

    def test_bots_and_webhooks_are_ignored_even_with_the_owners_id(self):
        self.build(message("approve 9", bot=True), message("approve 9", sequence=1, webhook_id="55555"))
        self.assertFalse(self.poll())
        self.assertEqual((self.client.writes, self.replies), ([], []))

    def test_only_plain_messages_and_replies_count(self):
        self.build(message("approve 9", kind=7))
        self.assertFalse(self.poll())
        self.assertEqual(self.client.writes, [])
        self.build(message("approve 9", kind=19))
        self.assertTrue(self.poll())

    def test_a_message_from_another_channel_is_ignored(self):
        self.build(message("approve 9", channel="444444444444444444"))
        self.assertFalse(self.poll())
        self.assertEqual(self.client.writes, [])

    def test_an_old_message_is_skipped_with_a_reply(self):
        old = NOW - timedelta(minutes=45)
        self.build(message("approve 9", when=old), started=old.timestamp() - 60)
        self.assertFalse(self.poll())
        self.assertEqual(self.client.writes, [])
        self.assertIn("skipped it", self.replies[0][0])

    def test_a_message_just_inside_the_limit_still_counts(self):
        self.build(message("approve 9", when=NOW - timedelta(minutes=25)), wall=lambda: NOW.timestamp(),
                   started=(NOW - timedelta(minutes=26)).timestamp())
        self.assertTrue(self.poll())

    def test_an_empty_message_means_the_content_switch_is_off_and_it_says_so_once(self):
        self.build(message(""), message("", sequence=1))
        self.assertFalse(self.poll())
        self.assertEqual(len(self.replies), 1)
        self.assertIn("Message Content Intent", self.replies[0][0])
        self.assertEqual(self.client.writes, [])

    def test_start_from_the_channel_releases_the_issue(self):
        client = FakeClient(raw_item(9, stage="discovery", goat_status="", assignees=()))
        self.build(message("Start 9: assign me"), client=client)
        self.assertTrue(self.poll())
        self.assertEqual(client.items[9]["assignees"], ["me"])
        self.assertEqual(client.labels_of(9), ["goat:ready", "stage:intake"])
        self.assertTrue(self.replies[0][0].startswith("Alandis, started #9"))

    def test_status_lists_what_waits(self):
        self.build(message("status"))
        self.assertFalse(self.poll())
        self.assertIn("waiting on you", self.replies[0][0])
        self.assertIn("#9 tier review: Add trainers", self.replies[0][0])

    def test_help_and_unknown_text_get_the_command_list(self):
        self.build(message("help"), message("what is going on", sequence=1))
        self.poll()
        self.assertIn("`approve 9`", self.replies[0][0])
        self.assertIn("I did not recognise that", self.replies[1][0])
        self.assertEqual(self.client.writes, [])

    def test_a_command_in_the_wrong_state_is_refused_and_reported(self):
        client = FakeClient(raw_item(9, stage="review", goat_status="merge-review"))
        self.build(message("approve 9"), client=client)
        self.assertFalse(self.poll())
        self.assertEqual(client.writes, [])
        self.assertIn("Merging happens on GitHub", self.replies[0][0])

    def test_two_commands_in_a_row_are_handled_in_order(self):
        client = FakeClient(raw_item(9, stage="intake", goat_status="tier-review"),
                            raw_item(10, stage="plan", goat_status="answer-question"))
        self.build(message("approve 9"), message("answer 10: yes", sequence=1), client=client)
        self.assertTrue(self.poll())
        self.assertIn("goat:approved", client.labels_of(9))
        self.assertIn("goat:ready", client.labels_of(10))
        self.assertEqual(len(self.replies), 2)

    def test_a_failed_reply_is_logged_and_never_raises(self):
        self.build(message("approve 9"))

        def broken(text, message_id):
            raise dc.DiscordError("Discord returned HTTP 403 (the bot cannot see or write in that channel).")

        self.box.reply = broken
        self.assertTrue(self.poll())
        self.assertTrue(any("could not reply" in line for line in self.logs))

    def test_a_discord_problem_is_logged_once_not_every_poll(self):
        self.build(fail=dc.DiscordError("Discord returned HTTP 403 (the bot cannot see or write in that channel)."))
        for _ in range(3):
            self.assertFalse(self.poll())
        self.assertEqual(len([line for line in self.logs if "403" in line]), 1)

    def test_no_approvals_channel_is_logged_and_retried_later_not_every_poll(self):
        self.build(channel=None)
        for _ in range(3):
            self.assertFalse(self.poll())
        self.assertEqual(len(self.resolved), 1)
        self.assertEqual(len([line for line in self.logs if "approvals" in line]), 1)
        self.clock[0] += inbox.RESOLVE_RETRY_SECONDS + 1
        self.poll()
        self.assertEqual(len(self.resolved), 2)

    def test_polling_is_throttled(self):
        self.build()
        self.assertTrue(self.box.is_due())
        self.poll()
        self.assertFalse(self.box.is_due())
        self.clock[0] += inbox.POLL_SECONDS
        self.assertTrue(self.box.is_due())

    def test_the_message_text_is_never_written_to_the_log(self):
        self.build(message("send back 9: my private reason"))
        self.client.items[9]["labels"] = ["stage:intake", "goat:tier-review"]
        self.poll()
        self.assertNotIn("private", " ".join(self.logs))


class Response:
    def __init__(self, body=b"{}"):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


class Opener:
    def __init__(self, *script):
        self.script = list(script)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        step = self.script.pop(0) if self.script else Response()
        if isinstance(step, Exception):
            raise step
        return step


class BotTests(unittest.TestCase):
    def bot(self, *script):
        opener = Opener(*script)
        return dc.Bot(TOKEN, opener=opener, sleep=lambda seconds: None), opener

    def test_requests_carry_the_token_only_in_the_header(self):
        bot, opener = self.bot(Response(b"[]"))
        bot.messages_after(CHANNEL, AFTER)
        request = opener.requests[0]
        self.assertEqual(request.get_header("Authorization"), "Bot " + TOKEN)
        self.assertNotIn(TOKEN, request.full_url)
        self.assertEqual(request.full_url, "https://discord.com/api/v10/channels/{}/messages?after={}&limit=50".format(CHANNEL, AFTER))

    def test_messages_come_back_oldest_first(self):
        bot, _ = self.bot(Response(json.dumps([{"id": "300000000000000000"}, {"id": "100000000000000010"}, {"id": "200000000000000000"}]).encode()))
        self.assertEqual([m["id"] for m in bot.messages_after(CHANNEL, AFTER)], ["100000000000000010", "200000000000000000", "300000000000000000"])

    def test_the_newest_id_and_an_empty_channel(self):
        bot, _ = self.bot(Response(b'[{"id": "77"}]'), Response(b"[]"))
        self.assertEqual(bot.newest_message_id(CHANNEL), "77")
        self.assertEqual(bot.newest_message_id(CHANNEL), "0")

    def test_a_reply_never_pings_anyone_and_is_a_reply(self):
        bot, opener = self.bot()
        bot.send(CHANNEL, "@everyone hi <@&123>", reply_to="42")
        body = json.loads(opener.requests[0].data)
        self.assertEqual(body["allowed_mentions"], {"parse": []})
        self.assertEqual(body["message_reference"]["message_id"], "42")
        self.assertEqual(opener.requests[0].get_method(), "POST")

    def test_ids_must_look_like_discord_ids(self):
        bot, opener = self.bot()
        for call in (lambda: bot.messages_after("../../etc", "5"), lambda: bot.messages_after(CHANNEL, "x y"),
                     lambda: bot.channels("abc"), lambda: bot.send("12", "hi")):
            with self.assertRaises(dc.DiscordError):
                call()
        self.assertEqual(opener.requests, [])

    def test_only_text_channels_are_listed(self):
        bot, _ = self.bot(Response(json.dumps([{"id": "1", "name": "approvals", "type": 0},
                                               {"id": "2", "name": "voice", "type": 2}]).encode()))
        self.assertEqual([c["name"] for c in bot.channels(GUILD)], ["approvals"])

    def test_an_error_never_contains_the_token(self):
        error = urllib.error.HTTPError("https://discord.com/x", 401, "no", {}, io.BytesIO(b"{}"))
        bot, _ = self.bot(error)
        with self.assertRaises(dc.DiscordError) as raised:
            bot.me()
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertIn("bot token is not valid", str(raised.exception))
        down = urllib.error.URLError("failed with {}".format(TOKEN))
        bot, _ = self.bot(down, down, down)
        with self.assertRaises(dc.DiscordError) as raised:
            bot.me()
        self.assertNotIn(TOKEN, str(raised.exception))

    def test_a_forbidden_channel_says_what_to_fix(self):
        error = urllib.error.HTTPError("https://discord.com/x", 403, "no", {}, io.BytesIO(b"{}"))
        bot, _ = self.bot(error)
        with self.assertRaises(dc.DiscordError) as raised:
            bot.newest_message_id(CHANNEL)
        self.assertIn("cannot see or write", str(raised.exception))

    def test_rate_limits_are_waited_out(self):
        limited = urllib.error.HTTPError("https://discord.com/x", 429, "slow", {}, io.BytesIO(b'{"retry_after": 1.5}'))
        slept = []
        opener = Opener(limited, Response(b"[]"))
        bot = dc.Bot(TOKEN, opener=opener, sleep=slept.append)
        self.assertEqual(bot.messages_after(CHANNEL, AFTER), [])
        self.assertEqual(slept, [1.5])

    def test_a_bad_token_is_refused_up_front(self):
        for bad in ("", None, "two words"):
            with self.assertRaises(dc.DiscordError):
                dc.Bot(bad)
        self.assertIsNone(dc.bot_from_settings({}))
        self.assertIsInstance(dc.bot_from_settings({"DISCORD_BOT_TOKEN": TOKEN}), dc.Bot)


class CoordinatorWiringTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.env_file = os.path.join(directory.name, "discord.env")
        patcher = mock.patch.object(dc, "ENV_FILE", self.env_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_env(self, **values):
        with open(self.env_file, "w") as f:
            for key, value in values.items():
                f.write("{}={}\n".format(key, value))

    def full_env(self, **extra):
        values = {"DISCORD_WEBHOOK_DISPATCH": "https://discord.com/api/webhooks/123456789012345678/abcDEF-_ghi123",
                  "DISCORD_BOT_TOKEN": TOKEN, "DISCORD_GUILD_ID": GUILD, "DISCORD_OWNER_USER_ID": OWNER_ID}
        values.update(extra)
        self.write_env(**values)

    def build(self, coordinator_config=None):
        config = {"coordinator": coordinator_config or {}, "repos": {}}
        with mock.patch.object(coordinator, "log"):
            return coordinator.build_inbox(config)

    def test_it_is_off_without_a_webhook_a_token_or_the_owner_id(self):
        self.assertIsNone(self.build())
        self.write_env(DISCORD_WEBHOOK_DISPATCH="https://discord.com/api/webhooks/123456789012345678/abcDEF-_ghi123")
        self.assertIsNone(self.build())
        self.full_env(DISCORD_OWNER_USER_ID="not-an-id")
        self.assertIsNone(self.build())

    def test_it_can_be_turned_off_in_the_config(self):
        self.full_env()
        self.assertIsNotNone(self.build())
        self.assertIsNone(self.build({"discordReplies": False}))
        self.assertIsNone(self.build({"discord": False}))

    def test_a_bad_token_is_logged_without_the_token(self):
        self.full_env(DISCORD_BOT_TOKEN="two words")
        logged = []
        with mock.patch.object(coordinator, "log", logged.append):
            self.assertIsNone(coordinator.build_inbox({"coordinator": {}, "repos": {}}))
        self.assertTrue(logged)
        self.assertNotIn("two words", " ".join(logged))

    def test_the_channel_is_found_by_its_name_or_by_the_env_file(self):
        bot = mock.Mock()
        bot.channels.return_value = [{"id": "5555555555", "name": "dispatch"}, {"id": "6666666666", "name": "Approvals"}]
        values = {"DISCORD_GUILD_ID": GUILD}
        self.assertEqual(coordinator.approvals_channel(bot, values), "6666666666")
        self.assertEqual(coordinator.approvals_channel(bot, {"DISCORD_CHANNEL_APPROVALS": "7777777777"}), "7777777777")
        bot.channels.return_value = [{"id": "5555555555", "name": "dispatch"}]
        self.assertIsNone(coordinator.approvals_channel(bot, values))

    def test_a_command_makes_the_waiting_loop_run_a_turn_at_once(self):
        class Box:
            def __init__(self):
                self.polls = 0

            def is_due(self):
                return self.polls == 0

            def poll(self, state):
                self.polls += 1
                return True

        class Keys:
            def wait_for_key(self, seconds):
                return None

            def discard_typed(self):
                pass

        class Early:
            def after_tick(self, result):
                pass

        turns = []
        times = iter([0.0, 0.0, 1000.0])
        with mock.patch.object(coordinator, "current_inbox", Box()), \
                mock.patch.object(coordinator, "run_one_tick", lambda *args: turns.append(1)), \
                mock.patch.object(coordinator, "start_follow_ups"), mock.patch.object(coordinator, "log"), \
                mock.patch.object(coordinator.time, "monotonic", lambda: next(times)):
            coordinator.wait_until(10.0, {"coordinator": {}}, {"runs": [], "attempts": {}}, False, Keys(), Early())
        self.assertEqual(turns, [1])

    def test_no_inbox_means_the_waiting_loop_behaves_as_before(self):
        self.assertIsNone(coordinator.current_inbox)


class DiscordCheckTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env_file = os.path.join(directory.name, "discord.env")
        with open(env_file, "w") as f:
            f.write("DISCORD_WEBHOOK_DISPATCH=https://discord.com/api/webhooks/123456789012345678/abcDEF-_ghi123\n"
                    "DISCORD_BOT_TOKEN={}\nDISCORD_GUILD_ID={}\nDISCORD_OWNER_USER_ID={}\n".format(TOKEN, GUILD, OWNER_ID))
        patcher = mock.patch.object(dc, "ENV_FILE", env_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_check(self, flags=1 << 19, channels=({"id": CHANNEL, "name": "approvals", "type": 0},), readable=True):
        out, err = io.StringIO(), io.StringIO()
        newest = mock.Mock() if readable else mock.Mock(side_effect=dc.DiscordError(
            "Discord returned HTTP 403 (the bot cannot see or write in that channel)."))
        with mock.patch.object(dc.Bot, "me", lambda bot: {"username": "Dex"}), \
                mock.patch.object(dc.Bot, "application_flags", lambda bot: flags), \
                mock.patch.object(dc.Bot, "channels", lambda bot, guild: list(channels)), \
                mock.patch.object(dc.Bot, "newest_message_id", newest), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["discord-check"])
        return code, out.getvalue(), err.getvalue()

    def test_everything_in_place(self):
        code, out, _ = self.run_check()
        self.assertEqual(code, 0)
        self.assertIn("All set", out)
        self.assertNotIn("FIX", out)
        self.assertNotIn(TOKEN, out)

    def test_it_tells_you_what_to_fix(self):
        code, out, err = self.run_check(flags=0, channels=())
        self.assertNotEqual(code, 0)
        self.assertIn("switch on Message Content Intent", out)
        self.assertIn("create a private text channel #approvals", out)
        self.assertIn("2 thing(s)", err)
        self.assertNotIn(TOKEN, out + err)

    def test_a_channel_the_bot_cannot_read_is_reported(self):
        code, out, _ = self.run_check(readable=False)
        self.assertNotEqual(code, 0)
        self.assertIn("cannot see or write", out)

    def test_it_sends_nothing(self):
        with mock.patch.object(dc.Bot, "send") as send, mock.patch.object(dc.Webhook, "send") as webhook:
            self.run_check()
        send.assert_not_called()
        webhook.assert_not_called()


class AgentsCannotUseTheCheckTests(unittest.TestCase):
    def test_agents_are_denied_the_check_command(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, "agent-settings.json")) as f:
            deny = json.load(f)["permissions"]["deny"]
        self.assertIn("Bash(python3 */goat.py discord-check*)", deny)


if __name__ == "__main__":
    unittest.main()
