"""Tests for goat/retro_launch.py: the aggregate retro config, its repo, and when it starts."""

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from goat import retro_launch, retros
from goat.board import GoatItem
from goat.choose import is_slot_limit

NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)
REPOS = {"org/goat": "/code/goat", "org/app": "/code/app"}


def coordinator_config(**retro):
    config = {"retro": dict(retro)}
    retro_launch.fill_retro_defaults(config, REPOS, "config.json")
    return config


def done_item(item_id):
    return GoatItem(
        id=item_id, board_id="me/app", name="Item " + item_id, url="", updated_at="2026-10-27T10:00:00Z",
        status="done", goat_status="done", size="", priority="", owner_ids=["me"], claim="",
        claim_expires=None, stage=None,
    )


class FillRetroDefaultsTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(coordinator_config()["retro"], {"aggregateMinRetros": 15, "aggregateMinDays": 3})

    def test_keeps_good_values(self):
        retro = coordinator_config(aggregateMinRetros=4, aggregateMinDays=1, repo="org/goat")["retro"]
        self.assertEqual(retro, {"aggregateMinRetros": 4, "aggregateMinDays": 1, "repo": "org/goat"})

    def test_bad_values_exit_with_a_message(self):
        for retro, words in [({"aggregateMinRetros": 0}, "1 or more"), ({"aggregateMinDays": 1.5}, "1 or more"),
                             ({"aggregateMinDays": True}, "1 or more"), ({"repo": "org/nope"}, "not in repos")]:
            with self.assertRaises(SystemExit, msg=str(retro)) as raised:
                coordinator_config(**retro)
            self.assertIn(words, str(raised.exception))
        with self.assertRaises(SystemExit) as raised:
            retro_launch.fill_retro_defaults({"retro": []}, REPOS, "config.json")
        self.assertIn("must be an object", str(raised.exception))


class AggregateRepoTests(unittest.TestCase):
    def test_the_repo_setting_wins(self):
        self.assertEqual(retro_launch.aggregate_repo_path(coordinator_config(repo="org/app"), REPOS), "/code/app")

    def test_finds_the_goat_install_repo(self):
        with mock.patch.object(retro_launch, "REPO_ROOT", "/code/goat"), \
                mock.patch.object(retro_launch, "main_checkout", lambda path: path):
            self.assertEqual(retro_launch.aggregate_repo_path(coordinator_config(), REPOS), "/code/goat")

    def test_none_when_no_repo_matches(self):
        with mock.patch.object(retro_launch, "REPO_ROOT", "/elsewhere"), \
                mock.patch.object(retro_launch, "main_checkout", lambda path: path):
            self.assertIsNone(retro_launch.aggregate_repo_path(coordinator_config(), REPOS))


class ChooseAggregateTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        for target, name, value in [(retros, "RETROS_DIR", self.dir), (retro_launch, "REPO_ROOT", "/code/goat"),
                                    (retro_launch, "main_checkout", lambda path: path)]:
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def add_notes(self, count):
        for number in range(count):
            retros.save_note(done_item(str(number)), "x", NOW - timedelta(days=5), self.dir)

    def choose(self, runs=(), attempts=None, free_slots=1, repos=REPOS, running_per_repo=None, max_per_repo=None,
               **retro):
        state = {"runs": list(runs), "attempts": dict(attempts or {})}
        config = coordinator_config(**retro)
        config["maxAgentsPerRepo"] = max_per_repo
        return retro_launch.choose_aggregate(config, repos, state, free_slots, NOW, running_per_repo)

    def test_not_due_with_too_few_notes(self):
        self.add_notes(14)
        self.assertEqual(self.choose(), (None, None))

    def test_first_aggregate_starts_with_enough_notes(self):
        self.add_notes(15)
        launch, reason = self.choose()
        self.assertIsNone(reason)
        self.assertEqual(launch, {"item": "retro-aggregate", "ref": "", "label": "aggregate retro",
                                  "role": "retro-aggregate", "model": "opus", "repoPath": "/code/goat",
                                  "attemptKey": "retro-aggregate:never:15"})

    def test_not_due_two_days_after_the_last_aggregate(self):
        self.add_notes(16)
        retros.save_aggregate("r", ["me_app-0"], NOW - timedelta(days=2), self.dir)
        self.assertEqual(self.choose(), (None, None))
        retros.save_aggregate("r", [], NOW - timedelta(days=3), self.dir)
        launch, _ = self.choose()
        self.assertIsNotNone(launch)

    def test_only_one_runs_at_a_time(self):
        self.add_notes(15)
        self.assertEqual(self.choose(runs=[{"role": "retro-aggregate", "item": "retro-aggregate"}]), (None, None))

    def test_stops_after_max_attempts(self):
        self.add_notes(15)
        launch, reason = self.choose(attempts={"retro-aggregate:never:15": 2})
        self.assertIsNone(launch)
        self.assertIn("saved no report", reason)

    def test_no_goat_repo(self):
        self.add_notes(15)
        launch, reason = self.choose(repos={"org/app": "/code/app"})
        self.assertIsNone(launch)
        self.assertIn("set coordinator.retro.repo", reason)

    def test_no_free_slot(self):
        self.add_notes(15)
        self.assertEqual(self.choose(free_slots=0), (None, retro_launch.AGGREGATE_WAITS))

    def test_repo_limit(self):
        self.add_notes(15)
        result = self.choose(running_per_repo={"/code/goat": 1}, max_per_repo=1)
        self.assertEqual(result, (None, retro_launch.AGGREGATE_WAITS_FOR_REPO))
        launch, _ = self.choose(running_per_repo={"/code/app": 1}, max_per_repo=1)
        self.assertIsNotNone(launch)

    def test_wait_reasons_are_not_slot_limits(self):
        for reason in [retro_launch.AGGREGATE_WAITS, retro_launch.AGGREGATE_WAITS_FOR_REPO]:
            self.assertFalse(is_slot_limit(reason), reason)

    def test_a_bad_state_file_gives_a_reason_and_does_not_raise(self):
        self.add_notes(15)
        for text in ["{not json", "[]", '{"lastAggregateAt": "yesterday"}', '{"lastAggregateAt": 5}']:
            with open(retros.state_path(self.dir), "w") as f:
                f.write(text)
            launch, reason = self.choose()
            self.assertIsNone(launch, text)
            self.assertIn("cannot read", reason)


if __name__ == "__main__":
    unittest.main()
