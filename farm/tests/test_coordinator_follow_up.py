"""Tests for follow-ups in farm/coordinator.py: follow_up() and the follow-up checks in wait_until(). GitHub, git and processes are mocked."""

import shutil
import tempfile
import unittest
from unittest import mock

from farm import coordinator
from farm.choose import MAX_ATTEMPTS, attempt_key
from farm.board import FarmItem
from farm.config import ConfigError
from farm.early_ticks import EarlyTicks, TickResult
from farm.github import GitHubError

ME = "me"
REPO = "/code/a"


def item(status, farm_status, stage, size="M"):
    return FarmItem(
        id="11", board_id="me/app", name="Item 11", url="", updated_at="2026-10-29T10:00:00Z",
        status=status, farm_status=farm_status, size=size, priority="", owner_ids=[ME], claim="",
        claim_expires=None, stage=stage,
    )


def finished_run(role="implement", repo_path=REPO):
    run = {"item": "11", "label": "#11 Item 11", "role": role, "pid": 500, "log": "l"}
    if repo_path:
        run["repoPath"] = repo_path
    return run


class FollowUpTests(unittest.TestCase):
    def setUp(self):
        self.logs = []
        self.popen_calls = []
        self.read_item = mock.Mock()
        log_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, log_dir)

        def popen(command, **kwargs):
            self.popen_calls.append((command, kwargs))
            return mock.Mock(pid=1000 + len(self.popen_calls))

        patches = [
            mock.patch.object(coordinator, "read_item", self.read_item),
            mock.patch.object(coordinator, "read_boards", side_effect=AssertionError("read_boards called")),
            mock.patch.object(coordinator.GitHubClient, "board_items",
                              side_effect=AssertionError("board_items called")),
            mock.patch.object(coordinator, "main_checkout", lambda path: path),
            mock.patch.object(coordinator, "log", self.logs.append),
            mock.patch.object(coordinator, "LOG_DIR", log_dir),
            mock.patch.object(coordinator.subprocess, "Popen", popen),
            mock.patch.object(coordinator.worktrees, "fetch"),
            mock.patch.object(coordinator.worktrees, "prepare",
                              lambda repo, ref: "{}-farm/{}".format(repo, ref)),
            mock.patch.object(coordinator.worktrees, "copy_env_files", return_value=[]),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(coordinator.children.clear)

    def follow_up(self, next_item, runs=(), attempts=None, max_agents=3, max_per_repo=None, max_per_role=None,
                  run=None):
        self.read_item.return_value = (next_item, ME, {})
        config = {"repos": {}, "coordinator": {"maxAgents": max_agents, "maxAgentsPerRepo": max_per_repo,
                                               "maxAgentsByRole": max_per_role or {}}}
        state = {"runs": list(runs), "attempts": dict(attempts or {})}
        self.result = coordinator.follow_up(config, state, run or finished_run(), False)
        return state

    def test_implement_done_starts_review_in_the_worktree(self):
        state = self.follow_up(item("review", "ready", "review"))
        self.read_item.assert_called_once_with(REPO, "11")
        command, kwargs = self.popen_calls[0]
        self.assertEqual(command[:3], ["claude", "-p", "/farm-review 11"])
        self.assertEqual(kwargs["cwd"], "/code/a-farm/11")
        self.assertEqual(state["runs"][0]["role"], "review")
        self.assertEqual(list(state["attempts"].values()), [1])
        self.assertTrue(any(line.startswith("Started: /farm-review #11") for line in self.logs), self.logs)
        self.assertFalse(self.result)

    def test_approved_plan_starts_implement(self):
        self.follow_up(item("plan", "approved", "plan"))
        self.assertEqual(self.popen_calls[0][0][:3], ["claude", "-p", "/farm-implement 11"])

    def test_gates_and_questions_start_nothing(self):
        for status, farm_status, stage in [("review", "merge-review", "review"), ("plan", "plan-review", "plan"),
                                           ("implement", "answer-question", "implement"),
                                           ("implement", "chat", "implement")]:
            self.logs.clear()
            state = self.follow_up(item(status, farm_status, stage))
            self.assertEqual(self.popen_calls, [], farm_status)
            self.assertEqual(state["runs"], [])
            self.assertIn("Not starting #11 Item 11: farm status is {}".format(farm_status), self.logs)
            self.assertFalse(self.result)

    def test_no_free_slot_starts_nothing(self):
        runs = [{"item": str(n), "pid": n, "repoPath": "/code/b"} for n in range(3)]
        self.follow_up(item("review", "ready", "review"), runs=runs, max_agents=3)
        self.assertEqual(self.popen_calls, [])
        self.assertIn("Not starting #11 Item 11: all agent slots are in use", self.logs)
        self.assertTrue(self.result)

    def test_no_free_repo_slot_starts_nothing(self):
        runs = [{"item": "9", "pid": 9, "repoPath": REPO}]
        self.follow_up(item("review", "ready", "review"), runs=runs, max_per_repo=1)
        self.assertEqual(self.popen_calls, [])
        self.assertIn("Not starting #11 Item 11: all agent slots for this repo are in use", self.logs)
        self.assertTrue(self.result)

    def test_no_free_role_slot_starts_nothing(self):
        runs = [{"item": "9", "pid": 9, "role": "review", "repoPath": "/code/b"}]
        self.follow_up(item("review", "ready", "review"), runs=runs, max_per_role={"review": 1})
        self.assertEqual(self.popen_calls, [])
        self.assertIn("Not starting #11 Item 11: all review slots are in use", self.logs)
        self.assertTrue(self.result)

    def test_attempt_limit_starts_nothing(self):
        next_item = item("review", "ready", "review")
        self.follow_up(next_item, attempts={attempt_key(next_item): MAX_ATTEMPTS})
        self.assertEqual(self.popen_calls, [])

    def test_done_item_starts_nothing_and_logs_nothing(self):
        self.follow_up(item("review", "done", "review"))
        self.assertEqual(self.popen_calls, [])
        self.assertEqual(self.logs, [])

    def test_read_errors_are_logged(self):
        for error in [GitHubError("rate limited"), ConfigError("FARM_GITHUB_REPO is missing")]:
            self.logs.clear()
            self.read_item.side_effect = error
            state = self.follow_up(item("review", "ready", "review"))
            self.assertEqual(self.popen_calls, [])
            self.assertEqual(state["runs"], [])
            self.assertEqual(self.logs, ["Follow-up for #11 Item 11: {}".format(error)])
            self.assertFalse(self.result)

    def test_retro_run_gets_no_follow_up_and_reads_nothing(self):
        state = self.follow_up(item("done", "done", None), run=finished_run(role="retro"))
        self.read_item.assert_not_called()
        self.assertEqual(state["runs"], [])
        self.assertFalse(self.result)

    def test_run_without_repo_path_reads_nothing(self):
        self.follow_up(item("review", "ready", "review"), run=finished_run(repo_path=None))
        self.read_item.assert_not_called()
        self.assertEqual(self.popen_calls, [])


class NoKeys:
    """A KeyListener stand-in with no keys typed: each wait takes its full timeout on the fake clock."""

    def __init__(self, clock):
        self.clock = clock
        self.timeouts = []

    def wait_for_key(self, timeout):
        self.timeouts.append(timeout)
        self.clock[0] += timeout
        return None

    def discard_typed(self):
        pass


class WaitUntilFollowUpTests(unittest.TestCase):
    def setUp(self):
        self.clock = [1000.0]
        self.keys = NoKeys(self.clock)
        self.prune = mock.Mock(return_value=[])
        self.follow_up = mock.Mock(return_value=False)
        self.save = mock.Mock()
        self.logs = []
        # Each early tick records the fake clock time; its result comes from self.tick_results.
        self.tick_times = []
        self.tick_results = []
        self.early = EarlyTicks()

        def tick(config, state, dry_run):
            self.tick_times.append(self.clock[0])
            if self.tick_results:
                return self.tick_results.pop(0)
            return TickResult(slot_skipped=True, github_failed=False)

        patches = [
            mock.patch.object(coordinator.time, "monotonic", lambda: self.clock[0]),
            mock.patch.object(coordinator, "prune_finished", self.prune),
            mock.patch.object(coordinator, "follow_up", self.follow_up),
            mock.patch.object(coordinator, "save_state", self.save),
            mock.patch.object(coordinator, "tick", tick),
            mock.patch.object(coordinator, "log", self.logs.append),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def wait(self, config, state, dry_run, interval):
        coordinator.wait_until(self.clock[0] + interval, config, state, dry_run, self.keys, self.early)

    def ends_at_polls(self, polls, total):
        """prune_finished returns one ended run at each poll number in `polls` (1 = the first poll)."""
        self.prune.side_effect = [[{"item": str(n)}] if n in polls else [] for n in range(1, total + 1)]

    def test_waits_in_steps_until_the_interval_ends(self):
        self.wait({}, {}, False, 40)
        step = coordinator.FOLLOW_UP_POLL_SECONDS
        self.assertEqual(step, 15)
        self.assertEqual(self.keys.timeouts, [15, 15, 10])
        self.assertEqual(self.prune.call_count, 3)
        self.follow_up.assert_not_called()
        self.save.assert_not_called()

    def test_zero_interval_does_not_wait(self):
        self.wait({}, {}, False, 0)
        self.assertEqual(self.keys.timeouts, [])
        self.prune.assert_not_called()

    def test_follows_up_each_finished_run_and_saves(self):
        first, second = {"item": "1"}, {"item": "2"}
        self.prune.side_effect = [[], [first, second], []]
        config, state = {"coordinator": {}}, {"runs": []}
        self.wait(config, state, True, 45)
        self.assertEqual(self.follow_up.call_args_list,
                         [mock.call(config, state, first, True), mock.call(config, state, second, True)])
        self.save.assert_called_once_with(state)
        self.assertEqual(self.tick_times, [])

    def test_waiting_item_gets_an_early_tick_and_no_follow_up(self):
        self.early.after_tick(TickResult(slot_skipped=True, github_failed=False))
        self.ends_at_polls({2}, 3)
        start = self.clock[0]
        self.wait({}, {"runs": []}, False, 45)
        self.follow_up.assert_not_called()
        self.assertEqual(self.tick_times, [start + 30])
        self.assertIn("Early turn: an agent ended and items wait for a slot.", self.logs)

    def test_second_early_tick_waits_for_the_throttle(self):
        # Runs end at 15 s and 45 s. The second early tick waits until 60 s after the first one.
        self.early.after_tick(TickResult(slot_skipped=True, github_failed=False))
        self.ends_at_polls({1, 3}, 8)
        start = self.clock[0]
        self.wait({}, {"runs": []}, False, 120)
        self.follow_up.assert_not_called()
        self.assertEqual(self.tick_times, [start + 15, start + 75])

    def test_follow_up_slot_skip_makes_the_next_end_an_early_tick(self):
        self.follow_up.return_value = True
        self.ends_at_polls({1, 2}, 3)
        start = self.clock[0]
        self.wait({}, {"runs": []}, False, 45)
        self.assertEqual(self.follow_up.call_count, 1)
        self.assertEqual(self.tick_times, [start + 30])

    def test_github_error_retries_after_120_seconds(self):
        self.early.after_tick(TickResult(slot_skipped=True, github_failed=False))
        self.tick_results = [TickResult(slot_skipped=False, github_failed=True)]
        self.ends_at_polls({1}, 12)
        start = self.clock[0]
        self.wait({}, {"runs": []}, False, 180)
        self.assertEqual(self.tick_times, [start + 15, start + 135])
        self.assertIn("Early turn had a GitHub error; next try in 120 seconds (retry 1 of 3).", self.logs)


if __name__ == "__main__":
    unittest.main()
