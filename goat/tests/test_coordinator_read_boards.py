"""Tests for read_boards() and read_item() in goat/coordinator.py, and the merge check in tick(), with a fake GitHub."""

import unittest
from unittest import mock

from goat import coordinator
from goat.config import ConfigError
from goat.github import GitHubError
from goat.tests.fakes import FakeClient, raw_item


def board_for(repo):
    return {"id": repo, "repo": repo, "doneLabel": "done"}


class ReadBoardsTests(unittest.TestCase):
    def setUp(self):
        self.logs = []
        self.clients = {}
        patches = [
            mock.patch.object(coordinator, "log", self.logs.append),
            mock.patch.object(coordinator, "project_env", lambda path: {"PATH": path}),
            mock.patch.object(coordinator, "load_board", self.load_board),
            mock.patch.object(coordinator, "GitHubClient", lambda repo: self.clients[repo]),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def load_board(self, env, path=None):
        if env["PATH"] == "/code/broken":
            raise ConfigError("Set GOAT_GITHUB_REPO")
        return board_for("me/" + env["PATH"].rsplit("/", 1)[1])

    def test_candidates_are_the_goat_issues_that_are_not_done(self):
        self.clients["me/a"] = FakeClient(
            raw_item("1", stage="plan", goat_status="ready"),
            raw_item("2", stage="staging", goat_status="done", state="closed"),
            raw_item("3", stage="discovery", goat_status=""),
        )
        candidates, user_id, boards, failed = coordinator.read_boards({"me/a": "/code/a"})
        self.assertEqual([(path, item.id) for path, item in candidates], [("/code/a", "1")])
        self.assertEqual((user_id, failed), ("me", False))
        # The cleanup and the merge check get every issue with a stage: or goat: label, closed ones too.
        self.assertEqual(sorted(item.id for item in boards[0][4]), ["1", "2", "3"])

    def test_a_broken_repo_is_skipped_and_the_others_still_run(self):
        self.clients["me/a"] = FakeClient(raw_item("1"))
        candidates, _, boards, failed = coordinator.read_boards({"x/broken": "/code/broken", "me/a": "/code/a"})
        self.assertEqual(len(boards), 1)
        self.assertFalse(failed)
        self.assertTrue(any(line.startswith("Skipping x/broken: Set GOAT_GITHUB_REPO") for line in self.logs), self.logs)

    def test_a_github_error_is_reported_for_the_back_off(self):
        client = FakeClient()
        client.board_items = mock.Mock(side_effect=GitHubError("rate limit"))
        self.clients["me/a"] = client
        candidates, user_id, boards, failed = coordinator.read_boards({"me/a": "/code/a"})
        self.assertEqual((candidates, user_id, boards, failed), (None, None, None, True))

    def test_a_different_gh_login_is_skipped(self):
        self.clients["me/a"] = FakeClient(raw_item("1"))
        self.clients["me/b"] = FakeClient(raw_item("2"), user={"id": "other", "name": "other"})
        _, _, boards, _ = coordinator.read_boards({"me/a": "/code/a", "me/b": "/code/b"})
        self.assertEqual(len(boards), 1)
        self.assertTrue(any("the gh login is other" in line for line in self.logs), self.logs)

    def test_read_item_reads_one_issue(self):
        client = FakeClient(raw_item("7", stage="review", goat_status="ready"))
        client.board_items = mock.Mock(side_effect=AssertionError("the whole list was read"))
        self.clients["me/a"] = client
        item, user_id, env = coordinator.read_item("/code/a", "7")
        self.assertEqual((item.id, item.stage, user_id), ("7", "review", "me"))


class MergeInTickTests(unittest.TestCase):
    def test_the_merge_check_runs_each_turn_unless_turned_off(self):
        for setting, expected_calls in ((None, 1), (True, 1), (False, 0)):
            coordinator_config = {"maxAgents": 3, "maxAgentsPerRepo": None, "maxAgentsByRole": {},
                                  "retro": {"aggregateMinRetros": 15, "aggregateMinDays": 3}}
            if setting is not None:
                coordinator_config["autoAdvanceMerged"] = setting
            config = {"repos": {}, "coordinator": coordinator_config}
            with mock.patch.object(coordinator, "check_repos", lambda config: {}), \
                    mock.patch.object(coordinator, "read_boards", lambda repos: ([], "me", [], False)), \
                    mock.patch.object(coordinator, "prune_finished"), \
                    mock.patch.object(coordinator, "clean_up_boards"), \
                    mock.patch.object(coordinator, "log"), \
                    mock.patch.object(coordinator, "advance_merged_boards") as advance:
                coordinator.tick(config, {"runs": [], "attempts": {}}, False)
            self.assertEqual(advance.call_count, expected_calls, setting)

    def test_advance_merged_boards_logs_and_survives_a_github_error(self):
        logs = []
        client = FakeClient(raw_item("5", stage="review", goat_status="merge-review"))
        client.pull_requests = mock.Mock(side_effect=GitHubError("down"))
        items = [coordinator.GoatItem.from_github(raw) for raw in client.board_items()]
        with mock.patch.object(coordinator, "log", logs.append):
            coordinator.advance_merged_boards([("/code/a", {}, client, {}, items)], "me", False)
        self.assertEqual(logs, ["Merge: Merge check failed in /code/a: down"])

    def test_advance_merged_boards_moves_a_merged_issue(self):
        logs = []
        client = FakeClient(raw_item("5", stage="review", goat_status="merge-review"))
        client.merged_prs = [{"number": 9, "url": "u", "branch": "goat/5-x", "base": "main", "merge_commit": "c", "mergedAt": "t"}]
        items = [coordinator.GoatItem.from_github(raw) for raw in client.board_items()]
        with mock.patch.object(coordinator, "log", logs.append):
            coordinator.advance_merged_boards([("/code/a", {}, client, {}, items)], "me", False)
        self.assertEqual(logs, ["Merge: Moved #5 to stage:staging: its PR is merged."])
        self.assertEqual(client.labels_of(5), ["stage:staging"])


class GhOnPathTests(unittest.TestCase):
    def test_command_run_needs_gh(self):
        import argparse
        args = argparse.Namespace(once=True, dry_run=True, loop_count=None, loop_interval=None, delay=0)
        config = {"repos": {"me/a": "/x"}, "coordinator": {"maxAgents": 1, "intervalSeconds": 1, "maxAgentsPerRepo": None,
                                                            "maxAgentsByRole": {}, "retro": {"aggregateMinRetros": 15,
                                                                                             "aggregateMinDays": 3}}}
        with mock.patch.dict(coordinator.os.environ, {}, clear=True), \
                mock.patch.object(coordinator, "load_config", lambda: config), \
                mock.patch.object(coordinator.shutil, "which", lambda name: "/bin/claude" if name == "claude" else None):
            with self.assertRaises(SystemExit) as raised:
                coordinator.command_run(args)
        self.assertIn("gh command is not on PATH", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
