"""Tests for farm.health (the heartbeat and the running/stale/died/stopped verdict) and farm.notify."""

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from farm import health, notify

NOW = datetime(2026, 11, 2, 12, 0, 0, tzinfo=timezone.utc)


def beat_file(**changes):
    data = {"pid": 4242, "status": "running", "startedAt": "2026-11-02T10:00:00Z", "lastSeenAt": "2026-11-02T11:59:50Z",
            "intervalSeconds": 900, "lastTurnAt": "2026-11-02T11:50:00Z", "nextTurnAt": "2026-11-02T12:05:00Z",
            "githubFailures": 0}
    data.update(changes)
    return data


ALIVE = lambda pid: True
DEAD = lambda pid: False


class AssessTests(unittest.TestCase):
    def test_never_started(self):
        state, text = health.assess(None, NOW)
        self.assertEqual(state, health.NEVER)
        self.assertIn("never started", text)

    def test_running_when_alive_and_recent(self):
        state, text = health.assess(beat_file(), NOW, ALIVE)
        self.assertEqual(state, health.RUNNING)
        self.assertIn("pid 4242", text)
        self.assertIn("Last check 10 minutes ago", text)
        self.assertIn("next in 5 minutes", text)

    def test_stale_when_alive_but_silent(self):
        old = beat_file(lastSeenAt="2026-11-02T11:00:00Z")
        state, text = health.assess(old, NOW, ALIVE)
        self.assertEqual(state, health.STALE)
        self.assertIn("asleep", text)
        self.assertIn("60 minutes", text)

    def test_died_when_the_process_is_gone(self):
        state, text = health.assess(beat_file(), NOW, DEAD)
        self.assertEqual(state, health.DIED)
        self.assertIn("NOT running", text)
        self.assertIn("did not stop on purpose", text)

    def test_stopped_on_purpose_is_not_a_death(self):
        state, text = health.assess(beat_file(status="stopped"), NOW, DEAD)
        self.assertEqual(state, health.STOPPED)
        self.assertIn("on purpose", text)

    def test_just_at_the_limit_is_still_running(self):
        edge = beat_file(lastSeenAt=health.stamp(NOW - timedelta(seconds=health.STALE_SECONDS)))
        self.assertEqual(health.assess(edge, NOW, ALIVE)[0], health.RUNNING)
        late = beat_file(lastSeenAt=health.stamp(NOW - timedelta(seconds=health.STALE_SECONDS + 1)))
        self.assertEqual(health.assess(late, NOW, ALIVE)[0], health.STALE)

    def test_a_damaged_timestamp_is_not_running(self):
        state, _ = health.assess(beat_file(lastSeenAt="garbage"), NOW, ALIVE)
        self.assertEqual(state, health.STALE)

    def test_github_failures_are_mentioned(self):
        _, text = health.assess(beat_file(githubFailures=3), NOW, ALIVE)
        self.assertIn("GitHub could not be read in the last 3 check(s)", text)

    def test_no_check_finished_yet(self):
        _, text = health.assess(beat_file(lastTurnAt=None, nextTurnAt=None), NOW, ALIVE)
        self.assertIn("No check has finished yet", text)

    def test_only_stale_and_died_raise_an_alert(self):
        self.assertEqual(set(health.ALERT_STATES), {health.STALE, health.DIED})

    def test_process_alive(self):
        self.assertTrue(health.process_alive(os.getpid()))
        self.assertFalse(health.process_alive(0))
        self.assertFalse(health.process_alive(None))
        self.assertFalse(health.process_alive("12"))

    def test_age_text(self):
        self.assertEqual(health.age_text(30), "30 seconds")
        self.assertEqual(health.age_text(600), "10 minutes")
        self.assertEqual(health.age_text(3 * 3600), "3 hours")
        self.assertEqual(health.age_text(3 * 86400), "3 days")


class BeatTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, "nested", "heartbeat.json")
        self.clock = [NOW]
        self.beat = health.Beat(99, 900, path=self.path, now=lambda: self.clock[0])

    def test_start_writes_the_file(self):
        data = health.read(self.path)
        self.assertEqual((data["pid"], data["status"], data["intervalSeconds"]), (99, "running", 900))
        self.assertEqual(data["startedAt"], "2026-11-02T12:00:00Z")

    def test_alive_moves_the_last_seen_time(self):
        self.clock[0] = NOW + timedelta(seconds=15)
        self.beat.alive()
        self.assertEqual(health.read(self.path)["lastSeenAt"], "2026-11-02T12:00:15Z")

    def test_turns_and_the_failure_streak(self):
        self.beat.turn_done(github_failed=True)
        self.beat.turn_done(github_failed=True)
        self.assertEqual(health.read(self.path)["githubFailures"], 2)
        self.beat.turn_done(github_failed=False)
        data = health.read(self.path)
        self.assertEqual(data["githubFailures"], 0)
        self.assertEqual(data["lastTurnAt"], "2026-11-02T12:00:00Z")

    def test_scheduled_sets_the_next_turn(self):
        self.beat.scheduled(300)
        self.assertEqual(health.read(self.path)["nextTurnAt"], "2026-11-02T12:05:00Z")

    def test_stopped_is_recorded(self):
        self.beat.stopped()
        data = health.read(self.path)
        self.assertEqual(data["status"], "stopped")
        self.assertEqual(data["stoppedAt"], "2026-11-02T12:00:00Z")
        self.assertEqual(health.assess(data, NOW, DEAD)[0], health.STOPPED)

    def test_a_write_failure_never_stops_the_coordinator(self):
        blocked = os.path.join(os.path.dirname(self.path), "heartbeat.json")
        os.makedirs(blocked + ".tmp")  # a directory where the temporary file should go
        self.beat.alive()  # must not raise

    def test_read_ignores_junk(self):
        with open(self.path, "w") as f:
            f.write("not json")
        self.assertIsNone(health.read(self.path))
        with open(self.path, "w") as f:
            json.dump([1, 2], f)
        self.assertIsNone(health.read(self.path))
        self.assertIsNone(health.read(self.path + ".missing"))


class NotifyTests(unittest.TestCase):
    def test_macos_passes_text_as_arguments_not_script(self):
        argv = notify.command('Title "x"', 'Issue #5 "Fix"; do shell script "rm -rf ~"', platform="darwin")
        self.assertEqual(argv[0], "osascript")
        script = " ".join(argv[1:8])
        self.assertNotIn("rm -rf", script)
        self.assertEqual(argv[-2:], ['Title "x"', 'Issue #5 "Fix"; do shell script "rm -rf ~"'])

    def test_linux_uses_notify_send(self):
        self.assertEqual(notify.command("T", "M", platform="linux"), ["notify-send", "T", "M"])

    def test_other_platforms_do_nothing(self):
        self.assertIsNone(notify.command("T", "M", platform="win32"))
        self.assertFalse(notify.notify("T", "M", platform="win32"))

    def test_clean_makes_one_short_printable_line(self):
        self.assertEqual(notify.clean("a\nb\tc\x07d"), "a b c d")
        self.assertEqual(len(notify.clean("x" * 500)), notify.MAX_LENGTH)

    def test_notify_reports_success_and_swallows_failures(self):
        ok = lambda argv, **kw: subprocess.CompletedProcess(argv, 0)
        bad = lambda argv, **kw: subprocess.CompletedProcess(argv, 1)

        def missing(argv, **kw):
            raise FileNotFoundError("osascript")

        def hung(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 1)

        self.assertTrue(notify.notify("T", "M", run=ok, platform="darwin"))
        self.assertFalse(notify.notify("T", "M", run=bad, platform="darwin"))
        self.assertFalse(notify.notify("T", "M", run=missing, platform="darwin"))
        self.assertFalse(notify.notify("T", "M", run=hung, platform="darwin"))


if __name__ == "__main__":
    unittest.main()
