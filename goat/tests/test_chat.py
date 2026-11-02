"""Tests for goat.chat: find an item's agent session and resume it.

Run from the repo root:
  python3 -m unittest discover -s goat/tests -t . -v
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from goat import chat
from goat.actions import GoatError


def run(item="1", session="s1", cwd=None, model="claude-opus-5-5", **extra):
    record = {"item": item, "sessionId": session, "cwd": cwd, "model": model, "outcome": "chat"}
    record.update(extra)
    return record


class ChatTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.folder)
        self.runs_file = os.path.join(self.tmp.name, "runs.jsonl")
        self.state_file = os.path.join(self.tmp.name, "state.json")
        self.exec_calls = []
        self.chdir_calls = []
        self.live_pids = set()

    def write_runs(self, *records):
        with open(self.runs_file, "w") as f:
            for record in records:
                f.write((record if isinstance(record, str) else json.dumps(record)) + "\n")

    def write_state(self, runs):
        with open(self.state_file, "w") as f:
            json.dump({"runs": runs, "attempts": {}}, f)

    def chat(self, item="1", session=None):
        output = io.StringIO()
        with redirect_stdout(output):
            chat.chat(item, session=session, runs_file=self.runs_file, state_file=self.state_file,
                      is_alive=lambda pid: pid in self.live_pids,
                      execvp=lambda file, argv: self.exec_calls.append((file, argv)),
                      chdir=self.chdir_calls.append)
        return output.getvalue()


class FindRunTests(ChatTestCase):
    def test_latest_run_for_the_item(self):
        self.write_runs(run(session="old", cwd=self.folder), run(item="2", session="other", cwd=self.folder),
                        run(session="new", cwd=self.folder))
        self.chat()
        self.assertEqual(self.exec_calls[0][1][:3], ["claude", "--resume", "new"])

    def test_session_picks_an_older_run(self):
        self.write_runs(run(session="old", cwd=self.folder), run(session="new", cwd=self.folder))
        self.chat(session="old")
        self.assertEqual(self.exec_calls[0][1][:3], ["claude", "--resume", "old"])

    def test_bad_lines_are_skipped(self):
        self.write_runs("{not json", run(cwd=self.folder))
        self.chat()
        self.assertEqual(len(self.exec_calls), 1)

    def test_no_run_for_the_item(self):
        self.write_runs(run(item="2", cwd=self.folder))
        with self.assertRaisesRegex(GoatError, "No run record for item 1 on this machine"):
            self.chat()
        self.assertEqual(self.exec_calls, [])

    def test_no_runs_file(self):
        with self.assertRaisesRegex(GoatError, "No run record for item 1"):
            self.chat()

    def test_session_of_another_item(self):
        self.write_runs(run(cwd=self.folder), run(item="2", session="s2", cwd=self.folder))
        with self.assertRaisesRegex(GoatError, "No run record for item 1 with session s2"):
            self.chat(session="s2")


class ChecksTests(ChatTestCase):
    def test_a_running_coordinator_agent_stops_it(self):
        self.write_runs(run(cwd=self.folder))
        self.write_state([{"item": "1", "pid": 4242, "role": "implement"}])
        self.live_pids.add(4242)
        with self.assertRaisesRegex(GoatError, "coordinator runs an implement agent on item 1 now"):
            self.chat()
        self.assertEqual(self.exec_calls, [])

    def test_a_dead_pid_does_not_stop_it(self):
        self.write_runs(run(cwd=self.folder))
        self.write_state([{"item": "1", "pid": 4242, "role": "implement"}])
        self.chat()
        self.assertEqual(len(self.exec_calls), 1)

    def test_an_agent_on_another_item_does_not_stop_it(self):
        self.write_runs(run(cwd=self.folder))
        self.write_state([{"item": "2", "pid": 4242, "role": "plan"}])
        self.live_pids.add(4242)
        self.chat()
        self.assertEqual(len(self.exec_calls), 1)

    def test_a_bad_state_file_does_not_stop_it(self):
        self.write_runs(run(cwd=self.folder))
        with open(self.state_file, "w") as f:
            f.write("{not json")
        self.chat()
        self.assertEqual(len(self.exec_calls), 1)

    def test_a_record_without_cwd(self):
        self.write_runs(run(cwd=None))
        with self.assertRaisesRegex(GoatError, "has no start folder"):
            self.chat()

    def test_a_missing_folder(self):
        self.write_runs(run(cwd=os.path.join(self.tmp.name, "gone")))
        with self.assertRaisesRegex(GoatError, "does not exist"):
            self.chat()


class ResumeTests(ChatTestCase):
    def test_goes_to_the_folder_and_resumes_with_the_model(self):
        self.write_runs(run(cwd=self.folder))
        output = self.chat()
        self.assertEqual(self.chdir_calls, [self.folder])
        self.assertEqual(self.exec_calls, [("claude", ["claude", "--resume", "s1", "--model", "claude-opus-5-5"])])
        self.assertIn("s1", output)

    def test_no_model_leaves_out_the_model_flag(self):
        self.write_runs(run(cwd=self.folder, model=None))
        self.chat()
        self.assertEqual(self.exec_calls[0][1], ["claude", "--resume", "s1"])


if __name__ == "__main__":
    unittest.main()
