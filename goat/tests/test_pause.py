"""Tests for pausing the coordinator: the switch file (goat/pause.py), what a paused turn and wait do, the Discord
commands pause, stop and resume, and `goat.py pause` and `goat.py resume`. No process is signalled for real."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest import mock

from goat import cli, coordinator, inbox, pause
from goat.early_ticks import EarlyTicks, TickResult

NOW = datetime(2026, 11, 23, 14, 3, 0, tzinfo=timezone.utc)
OWNER = {"name": "Alandis", "title": "CEO"}


class PauseFileCase(unittest.TestCase):
    """Points the switch at a fresh temporary file for each test."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, "coordinator", "paused.json")
        patcher = mock.patch.object(pause, "PAUSE_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)


class SwitchTests(PauseFileCase):
    def test_not_paused_when_there_is_no_file(self):
        self.assertIsNone(pause.read())
        self.assertEqual(pause.status_line(pause.read()), "")

    def test_pause_writes_who_and_when(self):
        info, already = pause.pause("Discord", NOW)
        self.assertFalse(already)
        self.assertEqual(info, {"since": "2026-11-23T14:03:00Z", "by": "Discord"})
        self.assertEqual(pause.read(), info)
        self.assertEqual(pause.status_line(info), "Paused (since 14:03 UTC on 2026-11-23, from Discord): no new agent "
                                                  "starts. Resume with `resume` in Discord or `goat.py resume`.")

    def test_a_second_pause_keeps_the_first_time(self):
        pause.pause("Discord", NOW)
        info, already = pause.pause("a terminal", datetime(2026, 11, 24, tzinfo=timezone.utc))
        self.assertTrue(already)
        self.assertEqual(info["since"], "2026-11-23T14:03:00Z")
        self.assertEqual(info["by"], "Discord")

    def test_resume_removes_the_file_once(self):
        pause.pause("Discord", NOW)
        self.assertTrue(pause.resume())
        self.assertIsNone(pause.read())
        self.assertFalse(pause.resume())

    def test_an_unreadable_file_counts_as_paused(self):
        os.makedirs(os.path.dirname(self.path))
        for content in ("{not json", "[1, 2]"):
            with open(self.path, "w") as f:
                f.write(content)
            info = pause.read()
            self.assertIsNotNone(info, msg=content)
            self.assertEqual(pause.status_line(info), "Paused: no new agent starts. Resume with `resume` in Discord or "
                                                      "`goat.py resume`.")
        self.assertTrue(pause.resume())
        self.assertIsNone(pause.read())

class SafetyNetTests(unittest.TestCase):
    def test_the_tests_never_use_the_developers_real_switch(self):
        # goat/tests/__init__.py points the default away from ~/.goat-ai for the whole run.
        self.assertNotIn(os.path.expanduser("~/.goat-ai"), pause.PAUSE_FILE)


class PausedTickTests(PauseFileCase):
    def setUp(self):
        super().setUp()
        self.logs = []
        patches = [
            mock.patch.object(coordinator, "prune_finished", return_value=[]),
            mock.patch.object(coordinator, "log", self.logs.append),
            mock.patch.object(coordinator, "save_state"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_paused_turn_reads_nothing_and_starts_nothing(self):
        pause.pause("Discord", NOW)
        state = {"runs": [{"item": "1", "pid": 5}], "attempts": {}}
        with mock.patch.object(coordinator, "finish_autopilot_runs", return_value=False) as finish, \
                mock.patch.object(coordinator, "read_boards", side_effect=AssertionError("read_boards")), \
                mock.patch.object(coordinator, "run_autopilot", side_effect=AssertionError("run_autopilot")), \
                mock.patch.object(coordinator, "start_launches", side_effect=AssertionError("start_launches")):
            result = coordinator.tick({"coordinator": {}}, state, False)
        self.assertEqual(result, TickResult(slot_skipped=False, github_failed=False))
        # Runs that ended are still noted, so QA and PM results are not lost.
        finish.assert_called_once()
        self.assertTrue(any(line.startswith("Paused: nothing starts") and "1 agent(s) running" in line
                            for line in self.logs))

    def test_paused_follow_ups_start_nothing_and_save_ended_runs(self):
        pause.pause("Discord", NOW)
        ended = [{"item": "14", "role": "plan"}]
        early = EarlyTicks()
        early.follow_up_skipped_for_slot()
        with mock.patch.object(coordinator, "prune_finished", return_value=ended), \
                mock.patch.object(coordinator, "finish_autopilot_runs", return_value=True), \
                mock.patch.object(coordinator, "follow_up", side_effect=AssertionError("follow_up")), \
                mock.patch.object(coordinator, "run_one_tick", side_effect=AssertionError("run_one_tick")), \
                mock.patch.object(coordinator, "save_state") as save:
            coordinator.start_follow_ups({}, {"runs": [], "attempts": {}}, False, early)
        save.assert_called_once()

    def test_follow_ups_work_as_before_when_not_paused(self):
        ended = [{"item": "14", "role": "plan"}]
        with mock.patch.object(coordinator, "prune_finished", return_value=ended), \
                mock.patch.object(coordinator, "finish_autopilot_runs", return_value=False), \
                mock.patch.object(coordinator, "follow_up", return_value=False) as follow_up:
            coordinator.start_follow_ups({}, {"runs": [], "attempts": {}}, False, EarlyTicks())
        follow_up.assert_called_once()


class WatchTests(PauseFileCase):
    def test_reports_each_change_once(self):
        watch = coordinator.PauseWatch()
        self.assertIsNone(watch.check())
        pause.pause("a terminal", NOW)
        self.assertEqual(watch.check(), "paused")
        self.assertIsNone(watch.check())
        pause.resume()
        self.assertEqual(watch.check(), "resumed")
        self.assertIsNone(watch.check())

    def test_a_turn_syncs_the_watch_so_a_discord_resume_runs_one_turn_not_two(self):
        pause.pause("Discord", NOW)
        watch = coordinator.PauseWatch()
        pause.resume()
        watch.sync()
        self.assertIsNone(watch.check())

    def test_the_wait_runs_a_turn_when_a_terminal_resumes(self):
        pause.pause("a terminal", NOW)
        watch = coordinator.PauseWatch()
        pause.resume()

        class Keys:
            def wait_for_key(self, seconds):
                return None

        turns, logs = [], []
        times = iter([0.0, 0.0, 1000.0])
        with mock.patch.object(coordinator, "current_watch", watch), \
                mock.patch.object(coordinator, "run_one_tick", lambda *args: turns.append(1) or TickResult(False, False)), \
                mock.patch.object(coordinator, "start_follow_ups"), mock.patch.object(coordinator, "log", logs.append), \
                mock.patch.object(coordinator.time, "monotonic", lambda: next(times)):
            coordinator.wait_until(10.0, {}, {"runs": [], "attempts": {}}, False, Keys(), EarlyTicks())
        self.assertEqual(turns, [1])
        self.assertIn("Resumed: running a turn now.", logs)


class PauseReplyTests(PauseFileCase):
    def setUp(self):
        super().setUp()
        self.logs = []
        patches = [
            mock.patch.object(coordinator, "log", self.logs.append),
            mock.patch.object(coordinator, "save_state"),
            mock.patch.object(coordinator, "prune_finished"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_pause_from_discord(self):
        state = {"runs": [{"item": "1", "role": "implement", "pid": 41}], "attempts": {}}
        with mock.patch.object(coordinator.os, "killpg") as killpg:
            text, run_turn = coordinator.pause_reply("pause", state, "Discord")
        killpg.assert_not_called()
        self.assertFalse(run_turn)
        self.assertIsNotNone(pause.read())
        self.assertEqual(pause.read()["by"], "Discord")
        self.assertEqual(text, "paused. No new agent starts, and nothing is approved or merged. 1 agent(s) still running "
                               "will finish their step: type `stop` to interrupt them. Type `resume` to start again.")

    def test_stop_pauses_and_interrupts_every_running_agent(self):
        state = {"runs": [{"item": "1", "role": "implement", "pid": 41}, {"item": "2", "role": "review", "pid": 42}],
                 "attempts": {}}
        with mock.patch.object(coordinator.os, "killpg") as killpg:
            text, run_turn = coordinator.pause_reply("stop", state, "Discord")
        self.assertEqual([call.args for call in killpg.call_args_list],
                         [(41, coordinator.signal.SIGINT), (42, coordinator.signal.SIGINT)])
        self.assertIsNotNone(pause.read())
        self.assertFalse(run_turn)
        self.assertIn("I interrupted 2 running agent(s)", text)
        coordinator.save_state.assert_called_once_with(state)

    def test_a_second_pause_says_it_was_already_paused(self):
        pause.pause("a terminal", NOW)
        text, _ = coordinator.pause_reply("pause", {"runs": [], "attempts": {}}, "Discord")
        self.assertTrue(text.startswith("I was already paused (since 14:03 UTC on 2026-11-23, from a terminal)."))

    def test_resume_runs_a_turn_at_once(self):
        pause.pause("Discord", NOW)
        text, run_turn = coordinator.pause_reply("resume", {"runs": [], "attempts": {}}, "Discord")
        self.assertTrue(run_turn)
        self.assertIsNone(pause.read())
        self.assertEqual(text, "resumed. I start work again now.")

    def test_resume_when_not_paused_changes_nothing(self):
        text, run_turn = coordinator.pause_reply("resume", {"runs": [], "attempts": {}}, "Discord")
        self.assertFalse(run_turn)
        self.assertIn("not paused", text)

    def test_terminal_wording(self):
        state = {"runs": [{"item": "1", "role": "plan", "pid": 41}], "attempts": {}}
        text, _ = coordinator.pause_reply("pause", state, "a terminal")
        self.assertIn("run `goat.py pause --stop` to interrupt them", text)
        self.assertTrue(text.endswith("Run `goat.py resume` to start again."))


class DiscordCommandTests(unittest.TestCase):
    def test_parse(self):
        cases = {"pause": "pause", "Pause.": "pause", "!pause all": "pause", "stop": "stop", "STOP!": "stop",
                 "stop all": "stop", "stop the agents": "stop", "resume": "resume", "Unpause": "resume",
                 "carry on": "resume"}
        for text, kind in cases.items():
            self.assertEqual(inbox.parse(text), inbox.Command(kind, None, ""), msg=text)

    def test_stop_with_an_issue_number_is_not_a_command(self):
        for text in ("stop 9", "pause 9", "resume 9", "please pause"):
            self.assertEqual(inbox.parse(text).kind, "unclear", msg=text)

    def test_help_lists_pause_and_stop(self):
        self.assertIn("`pause`", inbox.HELP_TEXT)
        self.assertIn("`stop`", inbox.HELP_TEXT)

    def build(self, control=None, pause_line=None):
        return inbox.Inbox(None, "1", lambda: "2", lambda number: ([], []), lambda: ([], []),
                           lambda text, message_id: None, lambda state: None, {}, OWNER, lambda line: None,
                           control=control, pause_line=pause_line)

    def test_the_control_callback_gets_the_coordinators_state(self):
        calls = []

        def control(kind, state):
            calls.append((kind, state))
            return inbox.Outcome("resumed. I start work again now.", True)

        state = {"runs": [], "attempts": {}}
        outcome = self.build(control).run(inbox.Command("resume", None, ""), state)
        self.assertEqual(calls, [("resume", state)])
        self.assertEqual(outcome, inbox.Outcome("Alandis, resumed. I start work again now.", True))

    def test_without_a_control_callback_nothing_happens(self):
        outcome = self.build().run(inbox.Command("pause", None, ""), {})
        self.assertFalse(outcome.run_turn)
        self.assertIn("not available", outcome.text)

    def test_status_says_when_paused(self):
        box = self.build(pause_line=lambda: "Paused: no new agent starts.")
        outcome = box.run(inbox.Command("status", None, ""))
        self.assertEqual(outcome.text, "Alandis, nothing waits on you right now.\nPaused: no new agent starts.")
        self.assertEqual(self.build().run(inbox.Command("status", None, "")).text,
                         "Alandis, nothing waits on you right now.")


class CliTests(PauseFileCase):
    def setUp(self):
        super().setUp()
        patches = [
            mock.patch.object(coordinator, "log"),
            mock.patch.object(coordinator, "load_state", return_value={"runs": [], "attempts": {}}),
            mock.patch.object(coordinator, "save_state"),
            mock.patch.object(coordinator, "prune_finished"),
            mock.patch.object(coordinator, "coordinator_running", return_value=True),
            mock.patch.dict(os.environ, {"GOAT_HEADLESS": "0"}),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def main(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(argv), 0)
        return out.getvalue()

    def test_pause_and_resume(self):
        self.assertTrue(self.main(["pause"]).startswith("Paused. No new agent starts"))
        self.assertEqual(pause.read()["by"], "a terminal")
        self.assertIn("within 15 seconds", self.main(["resume"]))
        self.assertIsNone(pause.read())
        self.assertIn("was not paused", self.main(["resume"]))

    def test_pause_stop_interrupts_the_agents(self):
        with mock.patch.object(coordinator, "interrupt_runs", return_value=2) as interrupt:
            text = self.main(["pause", "--stop"])
        interrupt.assert_called_once()
        self.assertIn("I interrupted 2 running agent(s)", text)
        self.assertIsNotNone(pause.read())

    def test_says_when_the_coordinator_is_not_running(self):
        with mock.patch.object(coordinator, "coordinator_running", return_value=False):
            self.assertIn("stays paused when you start it", self.main(["pause"]))
            self.assertIn("start it with `goat.py run`", self.main(["resume"]))

    def test_headless_agents_may_not_pause_or_resume(self):
        with mock.patch.dict(os.environ, {"GOAT_HEADLESS": "1"}):
            for argv in (["pause"], ["pause", "--stop"], ["resume"]):
                with self.assertRaises(SystemExit, msg=argv):
                    cli.main(argv)
        self.assertIsNone(pause.read())

    def test_status_says_when_paused(self):
        pause.pause("Discord", NOW)
        with mock.patch.object(coordinator, "CONFIG_FILE", self.path + ".missing"), \
                mock.patch.object(coordinator.health, "read", return_value=None):
            text = self.main(["status"])
        self.assertIn("Paused (since 14:03 UTC on 2026-11-23, from Discord)", text)

    def test_agents_are_denied_both_commands(self):
        with open(coordinator.AGENT_SETTINGS) as f:
            deny = json.load(f)["permissions"]["deny"]
        for rule in ("Bash(python3 */goat.py pause*)", "Bash(python3 */goat.py resume*)",
                     "Bash(python3 goat.py pause*)", "Bash(python3 goat.py resume*)"):
            self.assertIn(rule, deny)


if __name__ == "__main__":
    unittest.main()
