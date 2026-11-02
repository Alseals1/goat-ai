"""Tests for tick() in farm/coordinator.py: worktrees, the launch ref, and per-repo slots. Git and processes are mocked."""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from farm import coordinator, retros
from farm.board import FarmItem
from farm.early_ticks import TickResult
from farm.worktrees import WorktreeError

ME = "me"


def item(item_id):
    return FarmItem(
        id=item_id, board_id="me/app", name="Item " + item_id, url="", updated_at="2026-10-27T10:00:00Z",
        status="intake", farm_status="ready", size="", priority="", owner_ids=[ME], claim="",
        claim_expires=None, stage="intake", labels=["stage:intake", "farm:ready"],
    )


def done_item(item_id, farm_status="merge-review"):
    """A closed issue the farm worked on."""
    return FarmItem(
        id=item_id, board_id="me/app", name="Item " + item_id, url="", updated_at="2026-10-27T10:00:00Z",
        status="done", farm_status=farm_status, size="", priority="", owner_ids=[ME], claim="",
        claim_expires=None, stage=None, labels=["stage:staging"] + (["farm:" + farm_status] if farm_status else []),
    )


class TickTests(unittest.TestCase):
    def setUp(self):
        self.logs = []
        self.popen_calls = []
        self.fetch = mock.Mock()
        self.prepare = mock.Mock(side_effect=lambda repo, ref: "{}-farm/{}".format(repo, ref))
        self.copy_env_files = mock.Mock(return_value=["Copied .env."])
        log_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, log_dir)
        self.retros_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.retros_dir)

        def popen(command, **kwargs):
            self.popen_calls.append((command, kwargs))
            return mock.Mock(pid=1000 + len(self.popen_calls))

        patches = [
            mock.patch.object(coordinator, "prune_finished"),
            mock.patch.object(coordinator, "clean_up_boards"),
            mock.patch.object(coordinator, "advance_merged_boards"),
            mock.patch.object(coordinator, "main_checkout", lambda path: path),
            mock.patch.object(coordinator, "log", self.logs.append),
            mock.patch.object(coordinator, "LOG_DIR", log_dir),
            mock.patch.object(retros, "RETROS_DIR", self.retros_dir),
            mock.patch.object(coordinator.subprocess, "Popen", popen),
            mock.patch.object(coordinator.worktrees, "fetch", self.fetch),
            mock.patch.object(coordinator.worktrees, "prepare", self.prepare),
            mock.patch.object(coordinator.worktrees, "copy_env_files", self.copy_env_files),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(coordinator.children.clear)

    def run_tick(self, candidates, runs=(), dry_run=False, max_agents=3, max_per_repo=None, max_per_role=None,
                 done_items=(), attempts=None):
        """done_items: Done FarmItems on the board of /code/a, for retros."""
        repos = sorted({path for path, _ in candidates} | ({"/code/a"} if done_items else set()))
        boards = []
        for path in repos:
            board_items = list(done_items) if path == "/code/a" else []
            boards.append((path, {}, "client", {"doneLabel": "done"}, board_items))
        config = {"repos": {}, "coordinator": {"maxAgents": max_agents, "maxAgentsPerRepo": max_per_repo,
                                               "maxAgentsByRole": max_per_role or {}, "retro": {"aggregateMinRetros": 15, "aggregateMinDays": 3}}}
        state = {"runs": list(runs), "attempts": dict(attempts or {})}
        with mock.patch.object(coordinator, "check_repos", lambda config: {}), \
                mock.patch.object(coordinator, "read_boards", lambda repos: (candidates, ME, boards, False)):
            self.tick_result = coordinator.tick(config, state, dry_run)
        return state

    def test_starts_the_agent_in_the_worktree_with_the_ref(self):
        state = self.run_tick([("/code/a", item("14"))])
        command, kwargs = self.popen_calls[0]
        self.assertEqual(command[:3], ["claude", "-p", "/farm-intake 14"])
        self.assertEqual(kwargs["cwd"], "/code/a-farm/14")
        self.fetch.assert_called_once_with("/code/a")
        self.copy_env_files.assert_called_once_with("/code/a", "/code/a-farm/14", [".env"])
        run = state["runs"][0]
        self.assertEqual((run["repoPath"], run["worktree"]), ("/code/a", "/code/a-farm/14"))
        self.assertEqual(list(state["attempts"].values()), [1])
        self.assertIn("14: Copied .env.", self.logs)

    def test_starts_a_retro_in_the_main_checkout_without_a_worktree(self):
        state = self.run_tick([], done_items=[done_item("14")])
        command, kwargs = self.popen_calls[0]
        self.assertEqual(command[:3], ["claude", "-p", "/farm-retro 14"])
        self.assertEqual(command[command.index("--model") + 1], "sonnet")
        self.assertEqual(kwargs["cwd"], "/code/a")
        self.fetch.assert_not_called()
        self.prepare.assert_not_called()
        self.assertEqual(state["attempts"], {"retro:14": 1})
        self.assertEqual(state["runs"][0]["role"], "retro")

    def test_backfill_starts_a_retro_for_farm_status_done(self):
        self.run_tick([], done_items=[done_item("11", farm_status="done")])
        self.assertEqual(self.popen_calls[0][0][2], "/farm-retro 11")

    def test_item_with_a_note_gets_no_retro_and_is_not_held(self):
        retros.save_note(done_item("11"), "note", datetime(2026, 10, 29, tzinfo=timezone.utc), self.retros_dir)
        self.run_tick([], done_items=[done_item("11")])
        self.assertEqual(self.popen_calls, [])
        kwargs = coordinator.clean_up_boards.call_args
        self.assertEqual(kwargs[0][4:], (set(), set()))

    def test_cleanup_waits_for_the_retro_then_gives_up_after_two_tries(self):
        self.run_tick([], done_items=[done_item("11")], dry_run=True)
        self.assertEqual(coordinator.clean_up_boards.call_args[0][4:], ({"11"}, set()))
        self.run_tick([], done_items=[done_item("11")], attempts={"retro:11": 2})
        self.assertEqual(coordinator.clean_up_boards.call_args[0][4:], (set(), {"11"}))
        self.assertEqual(self.popen_calls, [])

    def test_stage_agents_get_the_slots_first(self):
        self.run_tick([("/code/a", item("1")), ("/code/a", item("2"))], max_agents=2,
                      done_items=[done_item("11")])
        commands = [command[2] for command, _ in self.popen_calls]
        self.assertEqual(commands, ["/farm-intake 1", "/farm-intake 2"])
        self.assertIn("1 retro(s) wait for a free slot.", self.logs)

    def test_waiting_retros_give_one_line_and_no_early_turn(self):
        self.run_tick([], done_items=[done_item("11"), done_item("12"), done_item("13")])
        self.assertEqual(len(self.popen_calls), 1)
        self.assertIn("2 retro(s) wait for a free slot.", self.logs)
        self.assertFalse([line for line in self.logs if line.startswith("Not starting")])
        self.assertFalse(self.tick_result.slot_skipped)

    def test_a_free_slot_after_stage_launches_starts_a_retro(self):
        self.run_tick([("/code/a", item("1"))], max_agents=2, done_items=[done_item("11")])
        commands = [command[2] for command, _ in self.popen_calls]
        self.assertEqual(commands, ["/farm-intake 1", "/farm-retro 11"])

    def test_retro_dry_run_names_the_main_checkout(self):
        self.run_tick([], done_items=[done_item("11")], dry_run=True)
        self.assertIn("Would start: /farm-retro #11 Item 11 on sonnet in /code/a", self.logs)

    def test_aggregate_starts_in_the_farm_checkout_with_no_argument(self):
        launch = {"item": "retro-aggregate", "ref": "", "label": "aggregate retro", "role": "retro-aggregate",
                  "model": "opus", "repoPath": "/code/farm", "attemptKey": "retro-aggregate:never:15"}
        with mock.patch.object(coordinator, "choose_aggregate", return_value=(launch, None)) as choose:
            state = self.run_tick([("/code/a", item("1"))], max_agents=3)
        self.assertEqual(choose.call_args[0][3], 2)  # The slots left after the intake launch.
        command, kwargs = self.popen_calls[1]
        self.assertEqual(command[:3], ["claude", "-p", "/farm-retro-aggregate"])
        self.assertEqual(command[command.index("--model") + 1], "opus")
        self.assertEqual(kwargs["cwd"], "/code/farm")
        self.prepare.assert_called_once()
        self.assertEqual(state["attempts"]["retro-aggregate:never:15"], 1)

    def test_aggregate_sees_this_turns_retro_launches_in_its_repo(self):
        with mock.patch.object(coordinator, "choose_aggregate", return_value=(None, None)) as choose:
            self.run_tick([], done_items=[done_item("11")])
        self.assertEqual(choose.call_args[0][5], {"/code/a": 1})

    def test_aggregate_skip_reason_is_logged(self):
        with mock.patch.object(coordinator, "choose_aggregate", return_value=(None, "all agent slots are in use")):
            self.run_tick([("/code/a", item("1"))])
        self.assertIn("Not starting aggregate retro: all agent slots are in use", self.logs)

    def test_fetches_each_repo_once(self):
        self.run_tick([("/code/a", item("1")), ("/code/a", item("2")), ("/code/b", item("3"))])
        self.assertEqual(self.fetch.call_args_list, [mock.call("/code/a"), mock.call("/code/b")])
        self.assertEqual(len(self.popen_calls), 3)

    def test_worktree_error_skips_the_launch_without_an_attempt(self):
        self.prepare.side_effect = [WorktreeError("worktree has local changes"), "/code/a-farm/2"]
        state = self.run_tick([("/code/a", item("1")), ("/code/a", item("2"))])
        self.assertEqual([call[1]["cwd"] for call in self.popen_calls], ["/code/a-farm/2"])
        self.assertEqual(len(state["attempts"]), 1)
        self.assertIn("Not starting #1 Item 1: worktree has local changes", self.logs)

    def test_os_error_skips_the_launch_without_an_attempt(self):
        self.prepare.side_effect = [PermissionError("Permission denied: '/code/a-farm'"), "/code/a-farm/2"]
        state = self.run_tick([("/code/a", item("1")), ("/code/a", item("2"))])
        self.assertEqual([call[1]["cwd"] for call in self.popen_calls], ["/code/a-farm/2"])
        self.assertEqual(len(state["runs"]), 1)
        self.assertEqual(len(state["attempts"]), 1)
        self.assertIn("Not starting #1 Item 1: Permission denied: '/code/a-farm'", self.logs)

    def test_failed_fetch_skips_the_repo_only(self):
        self.fetch.side_effect = lambda repo: self.raise_for(repo, "/code/a")
        state = self.run_tick([("/code/a", item("1")), ("/code/a", item("2")), ("/code/b", item("3"))])
        self.assertEqual([call[1]["cwd"] for call in self.popen_calls], ["/code/b-farm/3"])
        self.assertEqual(self.fetch.call_count, 2)
        self.assertEqual(len(state["attempts"]), 1)
        skipped = [line for line in self.logs if line.startswith("Not starting") and "no network" in line]
        self.assertEqual(len(skipped), 2)

    def test_dry_run_calls_no_git(self):
        state = self.run_tick([("/code/a", item("14"))], dry_run=True)
        self.fetch.assert_not_called()
        self.prepare.assert_not_called()
        self.copy_env_files.assert_not_called()
        self.assertEqual(self.popen_calls, [])
        self.assertEqual(state["runs"], [])
        self.assertTrue(any(line.startswith("Would start: /farm-intake #14") and line.endswith("in /code/a-farm/14")
                            for line in self.logs), self.logs)

    def test_per_repo_limit_counts_running_agents(self):
        runs = [{"item": "9", "pid": 1, "repoPath": "/code/a"}, {"item": "8", "pid": 2}]
        self.run_tick([("/code/a", item("1")), ("/code/b", item("2"))], runs=runs, max_per_repo=1)
        self.assertEqual([call[1]["cwd"] for call in self.popen_calls], ["/code/b-farm/2"])

    def test_role_cap_counts_running_agents(self):
        runs = [{"item": "9", "pid": 1, "role": "intake", "repoPath": "/code/b"}]
        self.run_tick([("/code/a", item("1")), ("/code/a", item("2"))], runs=runs, max_agents=5,
                      max_per_role={"intake": 2})
        self.assertEqual(len(self.popen_calls), 1)
        self.assertIn("Not starting #2 Item 2: all intake slots are in use", self.logs)

    def test_tick_reports_a_slot_skip(self):
        state = {"runs": [], "attempts": {}}
        config = {"repos": {}, "coordinator": {"maxAgents": 5, "maxAgentsPerRepo": None,
                                               "maxAgentsByRole": {"intake": 1}, "retro": {"aggregateMinRetros": 15, "aggregateMinDays": 3}}}
        candidates = [("/code/a", item("1")), ("/code/a", item("2"))]
        boards = [("/code/a", {}, "client", {"doneLabel": "done"}, [])]
        with mock.patch.object(coordinator, "check_repos", lambda config: {}), \
                mock.patch.object(coordinator, "read_boards", lambda repos: (candidates, ME, boards, True)):
            result = coordinator.tick(config, state, True)
        self.assertEqual(result, TickResult(slot_skipped=True, github_failed=True))

    def test_tick_with_no_skip(self):
        config = {"repos": {}, "coordinator": {"maxAgents": 3, "maxAgentsPerRepo": None, "maxAgentsByRole": {},
                                               "retro": {"aggregateMinRetros": 15, "aggregateMinDays": 3}}}
        boards = [("/code/a", {}, "client", {"doneLabel": "done"}, [])]
        with mock.patch.object(coordinator, "check_repos", lambda config: {}), \
                mock.patch.object(coordinator, "read_boards", lambda repos: ([("/code/a", item("1"))], ME, boards, False)):
            result = coordinator.tick(config, {"runs": [], "attempts": {}}, True)
        self.assertEqual(result, TickResult(slot_skipped=False, github_failed=False))

    def test_tick_with_no_board(self):
        with mock.patch.object(coordinator, "check_repos", lambda config: {}), \
                mock.patch.object(coordinator, "read_boards", lambda repos: (None, None, None, True)):
            result = coordinator.tick({"repos": {}, "coordinator": {}}, {"runs": [], "attempts": {}}, False)
        self.assertEqual(result, TickResult(slot_skipped=False, github_failed=True))

    def raise_for(self, repo, bad_repo):
        if repo == bad_repo:
            raise WorktreeError("git fetch in {} failed: no network".format(repo))


class StatusTests(unittest.TestCase):
    def status_lines(self, runs, config=None):
        """Runs command_status and returns the printed lines. config None means there is no config file."""
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            if config is not None:
                with open(path, "w") as f:
                    json.dump(config, f)
            with mock.patch.object(coordinator, "CONFIG_FILE", path), \
                    mock.patch.object(coordinator, "load_state", lambda: {"runs": runs, "attempts": {}}), \
                    mock.patch.object(coordinator, "prune_finished"), \
                    mock.patch.object(coordinator, "save_state"), \
                    mock.patch.object(coordinator.health, "read", lambda path: None), \
                    mock.patch("builtins.print") as printed:
                coordinator.command_status(None)
        lines = [call.args[0] for call in printed.call_args_list]
        # The first line is the coordinator's health. StatusTests.test_status_starts_with_the_health covers it.
        assert lines[0].startswith("Coordinator: "), lines
        return lines[1:]

    def test_status_starts_with_the_health(self):
        with mock.patch.object(coordinator, "load_state", lambda: {"runs": [], "attempts": {}}), \
                mock.patch.object(coordinator, "prune_finished"), mock.patch.object(coordinator, "save_state"), \
                mock.patch.object(coordinator.health, "read", lambda path: None), \
                mock.patch("builtins.print") as printed:
            coordinator.command_status(None)
        self.assertTrue(printed.call_args_list[0].args[0].startswith("Coordinator: The coordinator was never started"))

    def test_status_shows_the_repo_and_reads_old_records(self):
        runs = [
            {"item": "1", "role": "plan", "model": "opus", "pid": 1, "startedAt": "t", "log": "l", "repoPath": "/code/proj"},
            {"item": "2", "role": "intake", "model": "sonnet", "pid": 2, "startedAt": "t", "log": "l"},
        ]
        lines = self.status_lines(runs)
        self.assertTrue(lines[0].startswith("proj "))
        self.assertTrue(lines[1].startswith("- "))

    def test_status_shows_the_limits_first(self):
        config = {"repos": {}, "coordinator": {"maxAgents": 3, "maxAgentsByRole": {"implement": 2, "plan": 1}}}
        lines = self.status_lines([], config)
        self.assertEqual(lines, ["Limits: up to 3 agents (per role: plan 1, implement 2, retro 1)", "No agents running."])

    def test_status_without_a_config_file_has_no_limits_line(self):
        self.assertEqual(self.status_lines([]), ["No agents running."])


if __name__ == "__main__":
    unittest.main()
