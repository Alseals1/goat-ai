"""Tests for stalled issues (choose.stalled_items, coordinator.escalate_stalled), the heartbeat during a coordinator
run, and the `goat.py health` command."""

import argparse
import contextlib
import io
import json
import os
import signal
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from goat import board as fb
from goat import cli, coordinator, health, views
from goat.choose import MAX_ATTEMPTS, attempt_key, choose_launches, stalled_items
from goat.github import GitHubError
from goat.tests.fakes import FakeClient, raw_item

NOW = datetime(2026, 11, 2, 12, 0, 0, tzinfo=timezone.utc)


def item_of(client, number="5"):
    return fb.GoatItem.from_github(client.get_item(number))


def exhausted(item, count=MAX_ATTEMPTS):
    return {attempt_key(item): count}


class StalledItemsTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient(raw_item("5", stage="plan", goat_status="ready"))
        self.item = item_of(self.client)
        self.candidates = [("/code/a", self.item)]

    def found(self, attempts, running=(), user="me"):
        return stalled_items(self.candidates, user, set(running), attempts, NOW)

    def test_an_item_with_used_up_attempts_is_stalled(self):
        self.assertEqual([(path, i.id, role) for path, i, role in self.found(exhausted(self.item))],
                         [("/code/a", "5", "plan")])

    def test_below_the_limit_is_not_stalled(self):
        self.assertEqual(self.found(exhausted(self.item, MAX_ATTEMPTS - 1)), [])
        self.assertEqual(self.found({}), [])

    def test_a_running_agent_or_another_owner_is_skipped(self):
        self.assertEqual(self.found(exhausted(self.item), running={"5"}), [])
        self.assertEqual(self.found(exhausted(self.item), user="someone-else"), [])

    def test_items_that_wait_for_a_person_are_not_stalled(self):
        gate = item_of(FakeClient(raw_item("5", stage="plan", goat_status="plan-review")))
        self.assertEqual(stalled_items([("/code/a", gate)], "me", set(), exhausted(gate), NOW), [])

    def test_agrees_with_choose_launches_about_who_is_blocked(self):
        attempts = exhausted(self.item)
        launches, skipped = choose_launches(self.candidates, "me", set(), attempts, 3, NOW)
        self.assertEqual(launches, [])
        self.assertIn("changed nothing", skipped[0][1])
        self.assertEqual(len(self.found(attempts)), 1)


class EscalateTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient(raw_item("5", stage="plan", goat_status="ready", title="Add login"))
        self.item = item_of(self.client)
        self.logs = []
        self.notified = []
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.log_dir = directory.name
        for patcher in (mock.patch.object(coordinator, "log", self.logs.append),
                        mock.patch.object(coordinator, "notify", lambda title, message: self.notified.append((title, message))),
                        mock.patch.object(coordinator, "LOG_DIR", self.log_dir)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def escalate(self, attempts=None, dry_run=False, notifications=True, client=None):
        config = {"coordinator": {"notifications": notifications}}
        boards = [("/code/a", {}, client or self.client, {}, [self.item])]
        coordinator.escalate_stalled([("/code/a", self.item)], "me", boards, set(),
                                     exhausted(self.item) if attempts is None else attempts, NOW, config, dry_run)

    def test_marks_the_issue_stalled_comments_and_notifies(self):
        self.escalate()
        self.assertEqual([l for l in self.client.labels_of(5) if l.startswith("goat:")], ["goat:stalled"])
        comment = self.client.comment_bodies(5)[-1]
        self.assertIn("**🤖 Dex · Dispatcher · stalled**", comment)
        self.assertIn("@me", comment)
        self.assertIn("ran 2 times", comment)
        self.assertIn("replace the label `goat:stalled` with `goat:ready`", comment)
        self.assertEqual(self.notified[0][0], "Dex: stalled")
        self.assertIn("#5 Add login", self.notified[0][1])
        self.assertTrue(any(line.startswith("Stalled: #5 Add login") for line in self.logs), self.logs)

    def test_the_comment_names_the_latest_agent_log(self):
        for name in ("20261101T100000Z-5-plan.json", "20261102T110000Z-5-plan.json", "20261102T110000Z-6-plan.json"):
            open(os.path.join(self.log_dir, name), "w").close()
        self.escalate()
        self.assertIn(os.path.join(self.log_dir, "20261102T110000Z-5-plan.json"), self.client.comment_bodies(5)[-1])

    def test_no_log_is_said_plainly(self):
        self.escalate()
        self.assertIn("No agent log was found on this machine", self.client.comment_bodies(5)[-1])

    def test_dry_run_changes_nothing(self):
        self.escalate(dry_run=True)
        self.assertEqual(self.client.writes, [])
        self.assertEqual(self.notified, [])
        self.assertTrue(any(line.startswith("Would mark #5") for line in self.logs), self.logs)

    def test_notifications_can_be_turned_off(self):
        self.escalate(notifications=False)
        self.assertEqual(self.notified, [])
        self.assertIn("goat:stalled", self.client.labels_of(5))

    def test_nothing_happens_below_the_limit(self):
        self.escalate(attempts={})
        self.assertEqual(self.client.writes, [])

    def test_a_missing_label_posts_nothing_and_says_how_to_fix_it(self):
        del self.client.labels["goat:stalled"]
        self.escalate()
        self.assertEqual(self.client.comment_bodies(5), [])
        self.assertEqual(self.notified, [])
        self.assertTrue(any("setup-repo" in line for line in self.logs), self.logs)

    def test_a_comment_failure_still_leaves_the_issue_marked_and_notified(self):
        self.client.create_comment = mock.Mock(side_effect=GitHubError("down"))
        self.escalate()
        self.assertIn("goat:stalled", self.client.labels_of(5))
        self.assertTrue(any("could not comment" in line for line in self.logs), self.logs)
        self.assertEqual(len(self.notified), 1)

    def test_the_issue_is_not_a_candidate_next_turn(self):
        self.escalate()
        again = item_of(self.client)
        self.assertEqual(stalled_items([("/code/a", again)], "me", set(), exhausted(again), NOW), [])
        self.assertIsNone(fb.next_role(again, NOW))

    def test_the_views_tell_a_person_what_to_do(self):
        self.escalate()
        stalled = item_of(self.client)
        self.assertIn("stalled", views.next_step(stalled, NOW))
        self.assertTrue(views.next_step(stalled, NOW).startswith("waits for a person"))
        self.assertIn("goat:ready", views.waiting_hint(fb.STALLED))
        self.assertIn(fb.STALLED, fb.WAITS_FOR_PERSON)
        self.assertNotIn(fb.STALLED, fb.RELEASE_STATUSES)  # agents can never set it

    def test_tick_escalates_before_choosing_launches(self):
        config = {"repos": {}, "coordinator": {"maxAgents": 3, "maxAgentsPerRepo": None, "maxAgentsByRole": {},
                                               "retro": {"aggregateMinRetros": 15, "aggregateMinDays": 3}}}
        state = {"runs": [], "attempts": exhausted(self.item)}
        boards = [("/code/a", {}, self.client, {"doneLabel": "done"}, [self.item])]
        with mock.patch.object(coordinator, "check_repos", lambda config: {}), \
                mock.patch.object(coordinator, "read_boards", lambda repos: ([("/code/a", self.item)], "me", boards, False)), \
                mock.patch.object(coordinator, "prune_finished"), mock.patch.object(coordinator, "clean_up_boards"), \
                mock.patch.object(coordinator, "advance_merged_boards"), \
                mock.patch.object(coordinator, "start_launches") as start:
            coordinator.tick(config, state, False)
        self.assertIn("goat:stalled", self.client.labels_of(5))
        self.assertEqual(start.call_args[0][0], [])


class HeartbeatDuringRunTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, "heartbeat.json")
        patcher = mock.patch.object(health, "HEARTBEAT_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.seen = []

    def run_command(self, dry_run=False, error=None):
        args = argparse.Namespace(once=True, dry_run=dry_run, loop_count=None, loop_interval=None, delay=0)
        config = {"repos": {"me/a": "/x"}, "coordinator": {"maxAgents": 1, "intervalSeconds": 600, "maxAgentsPerRepo": None,
                                                            "maxAgentsByRole": {}, "retro": {"aggregateMinRetros": 15,
                                                                                             "aggregateMinDays": 3}}}

        class Listener:
            active = False
            def __enter__(inner): return inner
            def __exit__(inner, *exc): return False

        def fake_run_ticks(config, dry_run, count, interval, keys):
            self.seen.append(health.read(self.path))
            if error:
                raise error

        with mock.patch.dict(coordinator.os.environ, {}, clear=True), \
                mock.patch.object(coordinator, "load_config", lambda: config), \
                mock.patch.object(coordinator.shutil, "which", lambda name: "/bin/" + name), \
                mock.patch.object(coordinator.os, "makedirs"), \
                mock.patch.object(coordinator, "KeyListener", lambda: Listener()), \
                mock.patch.object(coordinator, "log"), \
                mock.patch.object(coordinator, "run_ticks", fake_run_ticks):
            coordinator.command_run(args)

    def test_a_run_writes_a_heartbeat_and_marks_a_clean_stop(self):
        self.run_command()
        during = self.seen[0]
        self.assertEqual((during["status"], during["pid"], during["intervalSeconds"]), ("running", os.getpid(), 600))
        after = health.read(self.path)
        self.assertEqual(after["status"], "stopped")
        self.assertIsNone(coordinator.current_beat)

    def test_ctrl_c_is_a_stop_on_purpose(self):
        self.run_command(error=KeyboardInterrupt())
        self.assertEqual(health.read(self.path)["status"], "stopped")

    def test_a_crash_is_not_marked_as_a_stop_on_purpose_but_still_cleans_up(self):
        # An unexpected exception ends the process. The finally block still marks the file, because the coordinator
        # is going away either way; a crash that skips it (kill -9, power loss) leaves "running" and reads as DIED.
        with self.assertRaises(RuntimeError):
            self.run_command(error=RuntimeError("boom"))
        self.assertIsNone(coordinator.current_beat)

    def test_a_dry_run_writes_no_heartbeat(self):
        self.run_command(dry_run=True)
        self.assertEqual(self.seen, [None])
        self.assertIsNone(health.read(self.path))

    def test_the_sigterm_handler_is_restored(self):
        before = signal.getsignal(signal.SIGTERM)
        self.run_command()
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)

    def test_sigterm_acts_like_ctrl_c(self):
        with self.assertRaises(KeyboardInterrupt):
            coordinator.raise_keyboard_interrupt(signal.SIGTERM, None)

    def test_turns_and_waits_update_the_heartbeat(self):
        beat = health.Beat(os.getpid(), 600, path=self.path)
        with mock.patch.object(coordinator, "current_beat", beat), \
                mock.patch.object(coordinator, "tick", lambda config, state, dry_run: coordinator.TickResult(False, True)), \
                mock.patch.object(coordinator, "save_state"):
            coordinator.run_one_tick({}, {}, False)
            coordinator.run_one_tick({}, {}, False)
        data = health.read(self.path)
        self.assertEqual(data["githubFailures"], 2)
        self.assertIsNotNone(data["lastTurnAt"])


class HealthCommandTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, "heartbeat.json")
        self.notified = []
        # The owner comes from a local config file. Never read the developer's real one in a test.
        config_file = os.path.join(directory.name, "config.json")
        with open(config_file, "w") as f:
            json.dump({"owner": {"name": "Pat", "title": "CEO"}}, f)
        for patcher in (mock.patch.object(health, "HEARTBEAT_FILE", self.path),
                        mock.patch.object(coordinator, "CONFIG_FILE", config_file),
                        mock.patch.object(cli, "notify", lambda title, message: self.notified.append((title, message)))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["health", *argv])
        return code, out.getvalue()

    def write(self, **changes):
        data = {"pid": os.getpid(), "status": "running", "startedAt": health.stamp(health.utc_now()),
                "lastSeenAt": health.stamp(health.utc_now()), "intervalSeconds": 900, "lastTurnAt": None,
                "nextTurnAt": None, "githubFailures": 0}
        data.update(changes)
        health.write(self.path, data)

    def test_never_started(self):
        code, out = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("never started", out)

    def test_running(self):
        self.write()
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("is running", out)

    def test_json(self):
        self.write()
        code, out = self.run_cli("--json")
        data = json.loads(out)
        self.assertEqual((code, data["state"], data["running"], data["version"]), (0, "running", True, "0.1"))
        self.assertEqual(data["heartbeat"]["pid"], os.getpid())

    def test_died_exits_1_and_notify_alerts(self):
        self.write(pid=2 ** 22 + 7, lastSeenAt="2026-01-01T00:00:00Z")
        code, out = self.run_cli("--notify")
        self.assertEqual(code, 1)
        self.assertIn("NOT running", out)
        self.assertEqual(self.notified[0][0], "Dex: the coordinator is not running")
        self.assertTrue(self.notified[0][1].startswith("Pat, the coordinator is NOT running"), self.notified[0])

    def test_a_stop_on_purpose_does_not_alert(self):
        self.write(status="stopped", pid=2 ** 22 + 7)
        code, out = self.run_cli("--notify")
        self.assertEqual(code, 1)
        self.assertEqual(self.notified, [])

    def test_running_does_not_alert(self):
        self.write()
        self.run_cli("--notify")
        self.assertEqual(self.notified, [])


if __name__ == "__main__":
    unittest.main()
