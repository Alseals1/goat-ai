"""Tests for the loop flags in farm/coordinator.py: run_ticks and command_run."""

import argparse
import os
import tempfile
import unittest
from unittest import mock

from farm import coordinator
from farm.early_ticks import EarlyTicks, TickResult

CONFIG = {"repos": {"org/repo": "/tmp/repo"},
          "coordinator": {"maxAgents": 3, "intervalSeconds": 300, "maxAgentsPerRepo": None, "maxAgentsByRole": {},
                          "retro": {"aggregateMinRetros": 15, "aggregateMinDays": 3}}}


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


class FakeKeys:
    """A KeyListener stand-in. `script` is a list of (seconds, key) steps.

    Each wait_for_key call takes the next step: it moves the clock by `seconds` (at most the timeout)
    and returns `key`. With no steps left, it moves the clock by the full timeout and returns None.
    A step whose key is an exception class raises it.
    """

    def __init__(self, clock, script=()):
        self.clock = clock
        self.script = list(script)
        self.timeouts = []
        self.discards = 0

    def wait_for_key(self, timeout):
        self.timeouts.append(timeout)
        if not self.script:
            self.clock.now += timeout
            return None
        seconds, key = self.script.pop(0)
        self.clock.now += min(seconds, timeout)
        if isinstance(key, type) and issubclass(key, BaseException):
            raise key
        return key

    def discard_typed(self):
        self.discards += 1


GOOD = TickResult(slot_skipped=False, github_failed=False)


def run_ticks_with_fakes(dry_run, count, interval, script=(), tick_seconds=()):
    """Run run_ticks with a fake clock and fake keys.

    `tick_seconds` lists how long each tick takes; ticks past the end of the list take 0 seconds.
    The follow-up checks are turned off here (one step per wait); test_follow_up.py covers them.
    Returns the tick mock, the FakeKeys, the log mock, and True when run_ticks raised KeyboardInterrupt.
    """
    clock = FakeClock()
    fake_keys = FakeKeys(clock, script)
    durations = list(tick_seconds)

    def fake_tick(config, state, dry_run):
        if durations:
            clock.now += durations.pop(0)
        return GOOD

    interrupted = False
    with mock.patch.object(coordinator, "tick", side_effect=fake_tick) as tick, \
            mock.patch.object(coordinator, "load_state", lambda: {"runs": [], "attempts": {}}), \
            mock.patch.object(coordinator, "save_state"), \
            mock.patch.object(coordinator, "log") as log, \
            mock.patch.object(coordinator, "FOLLOW_UP_POLL_SECONDS", 10 ** 6), \
            mock.patch.object(coordinator.time, "monotonic", clock.monotonic):
        try:
            coordinator.run_ticks(CONFIG, dry_run, count, interval, fake_keys)
        except KeyboardInterrupt:
            interrupted = True
    return tick, fake_keys, log, interrupted


class RunTicksTest(unittest.TestCase):
    def test_runs_count_ticks_and_waits_between_them(self):
        tick, keys, _, _ = run_ticks_with_fakes(False, 5, 300)
        self.assertEqual(tick.call_count, 5)
        self.assertEqual(keys.timeouts, [300] * 4)

    def test_no_wait_after_a_single_tick(self):
        tick, keys, _, _ = run_ticks_with_fakes(False, 1, 300)
        self.assertEqual(tick.call_count, 1)
        self.assertEqual(keys.timeouts, [])

    def test_dry_run_and_zero_interval(self):
        tick, keys, _, _ = run_ticks_with_fakes(True, 2, 0)
        self.assertEqual(tick.call_count, 2)
        for call in tick.call_args_list:
            self.assertEqual(call.args[2], True)
        self.assertEqual(keys.timeouts, [])

    def test_no_count_runs_until_interrupted(self):
        tick, _, _, interrupted = run_ticks_with_fakes(
            False, None, 10, script=[(10, None), (10, None), (3, KeyboardInterrupt)])
        self.assertTrue(interrupted)
        self.assertEqual(tick.call_count, 3)

    def test_trigger_key_runs_a_manual_tick_that_does_not_count(self):
        tick, keys, log, _ = run_ticks_with_fakes(False, 2, 300, script=[(100, "r")])
        self.assertEqual(tick.call_count, 3)
        log.assert_any_call("Turn triggered by user (r).")
        # The wait goes on to the same deadline: 300 s after the first tick.
        self.assertEqual(keys.timeouts, [300, 200])

    def test_uppercase_r_also_triggers(self):
        tick, keys, _, _ = run_ticks_with_fakes(False, 2, 300, script=[(50, "R")])
        self.assertEqual(tick.call_count, 3)
        self.assertEqual(keys.timeouts, [300, 250])

    def test_other_keys_are_ignored(self):
        tick, keys, log, _ = run_ticks_with_fakes(False, 2, 300, script=[(100, "x"), (50, "\n")])
        self.assertEqual(tick.call_count, 2)
        self.assertEqual(keys.timeouts, [300, 200, 150])
        # Only the remaining-count lines: the other keys log nothing.
        self.assertEqual(log.call_args_list, [
            mock.call("Turn 1 of 2 done. 1 turn(s) remaining."),
            mock.call("Turn 2 of 2 done. 0 turn(s) remaining."),
        ])

    def test_logs_ticks_remaining_after_each_scheduled_tick(self):
        _, _, log, _ = run_ticks_with_fakes(False, 3, 0)
        self.assertEqual(log.call_args_list, [
            mock.call("Turn 1 of 3 done. 2 turn(s) remaining."),
            mock.call("Turn 2 of 3 done. 1 turn(s) remaining."),
            mock.call("Turn 3 of 3 done. 0 turn(s) remaining."),
        ])

    def test_once_logs_zero_remaining(self):
        _, _, log, _ = run_ticks_with_fakes(False, 1, 300)
        self.assertEqual(log.call_args_list, [mock.call("Turn 1 of 1 done. 0 turn(s) remaining.")])

    def test_no_remaining_line_without_a_count(self):
        _, _, log, _ = run_ticks_with_fakes(False, None, 10, script=[(10, None), (3, KeyboardInterrupt)])
        self.assertEqual(log.call_args_list, [])

    def test_manual_tick_does_not_change_the_remaining_count(self):
        _, _, log, _ = run_ticks_with_fakes(False, 2, 300, script=[(100, "r")])
        self.assertEqual(log.call_args_list, [
            mock.call("Turn 1 of 2 done. 1 turn(s) remaining."),
            mock.call("Turn triggered by user (r)."),
            mock.call("Turn 2 of 2 done. 0 turn(s) remaining."),
        ])

    def test_manual_tick_past_the_deadline_runs_the_next_tick_at_once(self):
        # Scheduled tick: 0 s. Manual tick at 100 s takes 400 s, past the 300 s deadline.
        tick, keys, _, _ = run_ticks_with_fakes(False, 2, 300, script=[(100, "r")], tick_seconds=[0, 400, 0])
        self.assertEqual(tick.call_count, 3)
        self.assertEqual(keys.timeouts, [300])

    def test_typed_keys_are_discarded_after_every_tick(self):
        tick, keys, _, _ = run_ticks_with_fakes(False, 2, 300, script=[(100, "r")])
        self.assertEqual(tick.call_count, 3)
        self.assertEqual(keys.discards, 3)

    def test_wait_uses_the_ticks_state(self):
        # The follow-ups during the wait must see the runs that the tick just started.
        states = []
        def tick(config, state, dry_run):
            state["runs"].append("run")
            return GOOD

        wait = mock.Mock(side_effect=lambda deadline, config, state, dry_run, keys, early: states.append(state))
        with mock.patch.object(coordinator, "tick", tick), \
                mock.patch.object(coordinator, "load_state", lambda: {"runs": [], "attempts": {}}), \
                mock.patch.object(coordinator, "save_state"), \
                mock.patch.object(coordinator, "log"), \
                mock.patch.object(coordinator, "wait_until", wait):
            coordinator.run_ticks(CONFIG, False, 2, 10, FakeKeys(FakeClock()))
        self.assertEqual(states, [{"runs": ["run"], "attempts": {}}])

    def test_manual_tick_uses_the_waits_state(self):
        states = []
        clock = FakeClock()
        def tick(config, state, dry_run):
            states.append(state)
            return GOOD

        with mock.patch.object(coordinator, "tick", tick), \
                mock.patch.object(coordinator, "save_state"), \
                mock.patch.object(coordinator, "log"), \
                mock.patch.object(coordinator.time, "monotonic", clock.monotonic):
            state = {"runs": [], "attempts": {}}
            coordinator.wait_until(clock.now + 30, CONFIG, state, False, FakeKeys(clock, [(5, "r")]), EarlyTicks())
        self.assertEqual(len(states), 1)
        self.assertIs(states[0], state)

    def test_scheduled_tick_with_a_slot_skip_sets_the_flag(self):
        early_seen = []
        wait = mock.Mock(side_effect=lambda deadline, config, state, dry_run, keys, early: early_seen.append(
            early.waiting_for_slot))
        with mock.patch.object(coordinator, "tick", return_value=TickResult(True, False)), \
                mock.patch.object(coordinator, "load_state", lambda: {"runs": [], "attempts": {}}), \
                mock.patch.object(coordinator, "save_state"), \
                mock.patch.object(coordinator, "wait_until", wait):
            coordinator.run_ticks(CONFIG, False, 2, 10, FakeKeys(FakeClock()))
        self.assertEqual(early_seen, [True])

    def test_manual_tick_resets_the_back_off(self):
        clock = FakeClock()
        early = EarlyTicks()
        early.waiting_for_slot = True
        early.failures = 4
        with mock.patch.object(coordinator, "tick", return_value=TickResult(True, False)), \
                mock.patch.object(coordinator, "save_state"), \
                mock.patch.object(coordinator, "log"), \
                mock.patch.object(coordinator, "FOLLOW_UP_POLL_SECONDS", 10 ** 6), \
                mock.patch.object(coordinator.time, "monotonic", clock.monotonic):
            coordinator.wait_until(clock.now + 30, CONFIG, {"runs": [], "attempts": {}}, False,
                                   FakeKeys(clock, [(5, "r")]), early)
        self.assertEqual(early.failures, 0)
        self.assertTrue(early.waiting_for_slot)


class FakeListener:
    def __init__(self, active):
        self.active = active

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class CommandRunTest(unittest.TestCase):
    def setUp(self):
        # command_run writes a heartbeat. Never into the developer's real ~/.software-farm.
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.heartbeat_file = os.path.join(directory.name, "heartbeat.json")
        patcher = mock.patch.object(coordinator.health, "HEARTBEAT_FILE", self.heartbeat_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_command(self, active=False, environ=None, sleep_error=None, config=CONFIG, **options):
        """Run command_run with mocks. `self.calls` records the sleep and run_ticks calls in order."""
        defaults = {"once": False, "dry_run": False, "loop_count": None, "loop_interval": None, "delay": 0}
        args = argparse.Namespace(**dict(defaults, **options))
        self.calls = mock.Mock()
        self.listener = FakeListener(active)
        with mock.patch.dict(coordinator.os.environ, environ or {}, clear=True), \
                mock.patch.object(coordinator, "load_config", lambda: config), \
                mock.patch.object(coordinator.shutil, "which", lambda name: "/usr/bin/claude"), \
                mock.patch.object(coordinator.os, "makedirs"), \
                mock.patch.object(coordinator, "KeyListener", lambda: self.listener), \
                mock.patch.object(coordinator, "log") as log, \
                mock.patch.object(coordinator.time, "sleep", side_effect=sleep_error) as sleep, \
                mock.patch.object(coordinator, "run_ticks") as run_ticks:
            self.calls.attach_mock(sleep, "sleep")
            self.calls.attach_mock(run_ticks, "run_ticks")
            self.log = log
            self.sleep = sleep
            coordinator.command_run(args)
        return run_ticks

    def test_once_wins_over_loop_count(self):
        run_ticks = self.run_command(once=True, loop_count=5)
        run_ticks.assert_called_once_with(CONFIG, False, 1, 300, self.listener)

    def test_defaults_to_config_interval_and_no_count(self):
        run_ticks = self.run_command()
        run_ticks.assert_called_once_with(CONFIG, False, None, 300, self.listener)

    def test_loop_interval_overrides_config_even_when_zero(self):
        run_ticks = self.run_command(loop_count=2, loop_interval=0)
        run_ticks.assert_called_once_with(CONFIG, False, 2, 0, self.listener)

    def test_hint_line_only_when_the_listener_is_active(self):
        self.run_command(active=True)
        self.log.assert_any_call("Press r to run a turn now.")
        self.run_command(active=False)
        self.assertNotIn(mock.call("Press r to run a turn now."), self.log.call_args_list)

    def test_exits_when_repo_settings_are_in_the_shell(self):
        # They would override every project's .env, so every checkout would use one GitHub repo.
        with self.assertRaises(SystemExit) as raised:
            self.run_command(environ={"FARM_GITHUB_REPO": "me/app", "GH_TOKEN": "x"})
        self.assertIn("FARM_GITHUB_REPO", str(raised.exception))
        self.assertNotIn("GH_TOKEN", str(raised.exception))

    def test_gh_token_in_the_shell_is_fine(self):
        run_ticks = self.run_command(environ={"GH_TOKEN": "x"})
        run_ticks.assert_called_once()

    def test_no_delay_by_default(self):
        run_ticks = self.run_command()
        self.sleep.assert_not_called()
        run_ticks.assert_called_once_with(CONFIG, False, None, 300, self.listener)

    def test_delay_waits_before_the_first_tick(self):
        self.run_command(delay=30)
        self.assertEqual(self.calls.mock_calls, [
            mock.call.sleep(30),
            mock.call.run_ticks(CONFIG, False, None, 300, self.listener),
        ])
        self.log.assert_any_call("Waiting 30 seconds before the first turn.")

    def test_start_log_shows_the_limits(self):
        config = {"repos": {"org/repo": "/tmp/repo"},
                  "coordinator": {"maxAgents": 5, "intervalSeconds": 300, "maxAgentsPerRepo": 1,
                                  "maxAgentsByRole": {"review": 2, "intake": 3}, "retro": {"aggregateMinRetros": 15, "aggregateMinDays": 3}}}
        self.run_command(config=config)
        self.log.assert_any_call(
            "Coordinator for 1 repo(s): up to 5 agents (1 per repo; per role: intake 3, review 2, retro 1), every 300 seconds.")

    def test_start_log_shows_the_turn_count(self):
        self.run_command(loop_count=2)
        self.log.assert_any_call(
            "Coordinator for 1 repo(s): up to 3 agents (per role: retro 1), every 300 seconds, for 2 turn(s).")

    def test_ctrl_c_during_delay_stops_cleanly(self):
        run_ticks = self.run_command(delay=30, sleep_error=KeyboardInterrupt)
        run_ticks.assert_not_called()
        self.assertIn("Coordinator stopped", self.log.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
