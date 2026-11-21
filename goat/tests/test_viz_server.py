"""The floor view's server (viz/live_server.py): how it turns G.O.A.T.'s shapes into the ones the page expects."""

import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

PATH = os.path.join(os.path.dirname(__file__), "..", "..", "viz", "live_server.py")
spec = importlib.util.spec_from_file_location("live_server", PATH)
live_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live_server)


def listed(number, status, goat_status, repo="Alseals1/RocketPal"):
    return {"id": str(number), "name": "Issue {}".format(number), "status": status, "stage": status,
            "goatStatus": goat_status, "url": "https://github.com/{}/issues/{}".format(repo, number),
            "nextRole": None, "updatedAt": "2026-11-21T10:00:00Z"}


class FarmStatusTest(unittest.TestCase):
    def test_goat_statuses_are_spelled_as_the_page_expects(self):
        self.assertEqual(live_server.farm_status("plan-review", "plan"), "plan review")
        self.assertEqual(live_server.farm_status("answer-question", "plan"), "answer question")
        self.assertEqual(live_server.farm_status("chat", "plan"), "chat with agent")
        self.assertEqual(live_server.farm_status("ready", "intake"), "ready")

    def test_a_closed_issue_with_no_goat_label_is_done(self):
        self.assertEqual(live_server.farm_status(None, "done"), "done")
        self.assertEqual(live_server.farm_status("", "discovery"), "")


class NormalizeItemTest(unittest.TestCase):
    def test_the_card_id_carries_the_repo_and_the_label_the_number(self):
        item = live_server.normalize_item(listed(34, "implement", "working"), "Alseals1/RocketPal", False)
        self.assertEqual(item["id"], "Alseals1/RocketPal#34")
        self.assertEqual(item["number"], "34")
        self.assertEqual(item["taskId"], "#34")
        self.assertEqual(item["repo"], "Alseals1/RocketPal")
        self.assertEqual(item["farmStatus"], "working")

    def test_with_more_than_one_repo_the_label_names_the_repo(self):
        item = live_server.normalize_item(listed(34, "plan", "ready"), "Alseals1/RocketPal", True)
        self.assertEqual(item["taskId"], "RocketPal#34")

    def test_the_repo_comes_from_the_url_when_the_folder_name_differs(self):
        item = live_server.normalize_item(listed(7, "plan", "ready"), "Rocket Pals", False)
        self.assertEqual(item["id"], "Alseals1/RocketPal#7")
        self.assertEqual(item["repo"], "Rocket Pals")

    def test_staging_is_staging_review(self):
        item = live_server.normalize_item(listed(5, "staging", ""), "Alseals1/RocketPal", False)
        self.assertEqual(item["status"], "staging review")

    def test_an_issue_with_no_goat_label_before_staging_is_not_shown(self):
        self.assertIsNone(live_server.normalize_item(listed(9, "discovery", ""), "Alseals1/RocketPal", False))

    def test_the_input_is_not_changed(self):
        raw = listed(34, "plan", "plan-review")
        live_server.normalize_item(raw, "Alseals1/RocketPal", False)
        self.assertEqual(raw["id"], "34")
        self.assertEqual(raw["goatStatus"], "plan-review")


class RunKeyTest(unittest.TestCase):
    def test_an_issue_run_gets_the_card_id(self):
        self.assertEqual(live_server.run_key("34", "Alseals1/RocketPal"), "Alseals1/RocketPal#34")

    def test_a_project_manager_run_keeps_its_item(self):
        self.assertEqual(live_server.run_key("pm-Alseals1__RocketPal", "Alseals1/RocketPal"),
                         "pm-Alseals1__RocketPal")

    def test_a_record_with_no_repo_keeps_its_item(self):
        self.assertEqual(live_server.run_key("34", None), "34")


class AttentionTest(unittest.TestCase):
    def test_open_issues_at_a_gate_question_chat_or_stall_wait_for_a_person(self):
        items = [live_server.normalize_item(listed(n, status, goat, repo="o/r"), "o/r", False) for n, status, goat in
                 ((1, "plan", "plan-review"), (2, "plan", "chat"), (3, "plan", "stalled"),
                  (4, "implement", "working"), (5, "done", "merge-review"))]
        self.assertEqual(live_server.attention(items), ["o/r#1", "o/r#2", "o/r#3"])


class NewestCommentTest(unittest.TestCase):
    def test_the_newest_comment_of_show_json(self):
        data = {"comments": [{"text": "older"}, {"text": "Which colour?"}]}
        self.assertEqual(live_server.newest_comment(data), "Which colour?")
        self.assertIsNone(live_server.newest_comment({"comments": []}))


class SnapshotTest(unittest.TestCase):
    def test_runs_and_records_point_at_the_same_card_as_the_issue(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, "coordinator"))
            state = os.path.join(home, "coordinator", "state.json")
            runs = os.path.join(home, "runs.jsonl")
            with open(state, "w") as f:
                json.dump({"runs": [{"item": "34", "repo": "Alseals1/RocketPal", "role": "implement", "pid": 1,
                                     "startedAt": "2026-11-21T10:00:00Z"}]}, f)
            with open(runs, "w") as f:
                f.write(json.dumps({"item": "34", "board": "Alseals1/RocketPal", "role": "plan", "outcome": "gate",
                                    "tokens": {"output": 900}}) + "\n")
                f.write("not json\n")
            item = live_server.normalize_item(listed(34, "implement", "working"), "Alseals1/RocketPal", False)
            with mock.patch.multiple(live_server, STATE_FILE=state, RUNS_FILE=runs,
                                     CONFIG_FILE=os.path.join(home, "config.json")), \
                    mock.patch.dict(live_server.board, {"items": [item], "readAt": 1.0, "errors": {}}):
                snap = live_server.snapshot()
        self.assertEqual(snap["runs"][0]["item"], item["id"])
        self.assertEqual(snap["finished"], [dict(snap["finished"][0], item=item["id"], outputTokens=900)])
        self.assertEqual(snap["finished"][0]["outcome"], "gate")
        self.assertEqual(snap["attention"], [])
        self.assertTrue(snap["coordinatorStateFound"])


if __name__ == "__main__":
    unittest.main()
