"""Tests for run-usage.py. They build a fake ~/.claude/projects folder; no real transcript is used.

Run from the repo root:
  python3 -m unittest discover -s skills/farm-board/tests -v
"""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "run-usage.py")

# The file name has a hyphen, so load it by path.
spec = importlib.util.spec_from_file_location("run_usage", SCRIPT)
run_usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_usage)

SESSION = "11111111-2222-3333-4444-555555555555"


def assistant_line(message_id, timestamp, model, input_tokens, output_tokens, cache_read=0, cache_write=0):
    """One transcript line in the shape Claude Code writes (only the fields the script reads)."""
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "message": {
            "id": message_id,
            "model": model,
            "content": [{"type": "text", "text": "secret message text"}],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
            },
        },
    }


class RunUsageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.projects = self.temp.name
        self.project_dir = os.path.join(self.projects, "-Users-someone-repo")
        os.makedirs(self.project_dir)

    def tearDown(self):
        self.temp.cleanup()

    def write_lines(self, path, entries):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as out:
            for entry in entries:
                if isinstance(entry, str):
                    out.write(entry + "\n")
                else:
                    out.write(json.dumps(entry) + "\n")

    def write_main(self, entries):
        self.write_lines(os.path.join(self.project_dir, SESSION + ".jsonl"), entries)

    def write_subagent(self, name, entries):
        path = os.path.join(self.project_dir, SESSION, "subagents", "agent-" + name + ".jsonl")
        self.write_lines(path, entries)

    def run_script(self, since="2026-10-27T15:00:00Z", until="2026-10-27T16:00:00Z", session=SESSION):
        output = io.StringIO()
        with redirect_stdout(output):
            code = run_usage.main(
                ["--session", session, "--since", since, "--until", until, "--projects-dir", self.projects]
            )
        self.assertEqual(code, 0)
        return json.loads(output.getvalue())

    def test_counts_only_messages_inside_the_window(self):
        self.write_main([
            assistant_line("before", "2026-10-27T14:59:59.000Z", "claude-opus-5-5", 1000, 1000),
            assistant_line("inside", "2026-10-27T15:30:00.000Z", "claude-opus-5-5", 10, 20, 30, 40),
            assistant_line("after", "2026-10-27T16:00:01.000Z", "claude-opus-5-5", 1000, 1000),
        ])
        result = self.run_script()
        self.assertEqual(result["tokens"], {"input": 10, "output": 20, "cacheRead": 30, "cacheWrite": 40})
        self.assertIsNone(result["usageNote"])

    def test_counts_a_repeated_message_id_once(self):
        line = assistant_line("same", "2026-10-27T15:10:00.000Z", "claude-opus-5-5", 10, 20)
        self.write_main([line, line, line])
        result = self.run_script()
        self.assertEqual(result["tokens"]["input"], 10)
        self.assertEqual(result["tokens"]["output"], 20)

    def test_counts_subagents_and_lists_each_model(self):
        self.write_main([assistant_line("main", "2026-10-27T15:10:00.000Z", "claude-opus-5-5", 10, 20)])
        self.write_subagent("a1", [assistant_line("sub", "2026-10-27T15:11:00.000Z", "claude-haiku-5-5", 1, 2)])
        result = self.run_script()
        self.assertEqual(result["tokens"]["input"], 11)
        self.assertEqual(result["tokens"]["output"], 22)
        self.assertEqual([m["model"] for m in result["models"]], ["claude-haiku-5-5", "claude-opus-5-5"])
        self.assertEqual(result["models"][0]["input"], 1)

    def test_skips_synthetic_messages(self):
        self.write_main([
            assistant_line("real", "2026-10-27T15:10:00.000Z", "claude-opus-5-5", 10, 20),
            assistant_line("local", "2026-10-27T15:11:00.000Z", "<synthetic>", 500, 500),
        ])
        result = self.run_script()
        self.assertEqual(result["tokens"]["input"], 10)
        self.assertEqual([m["model"] for m in result["models"]], ["claude-opus-5-5"])

    def test_missing_session_gives_null_usage_and_exit_0(self):
        completed = subprocess.run(
            [sys.executable, SCRIPT, "--session", "no-such-session", "--since", "2026-10-27T15:00:00Z",
             "--projects-dir", self.projects],
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0)
        result = json.loads(completed.stdout)
        self.assertEqual(result, {"tokens": None, "models": None, "usageNote": "transcript not found",
                                  "cwd": None, "model": None})

    def test_skips_a_bad_line(self):
        self.write_main([
            "{not json",
            assistant_line("good", "2026-10-27T15:10:00.000Z", "claude-opus-5-5", 10, 20),
            {"type": "assistant"},
        ])
        result = self.run_script()
        self.assertEqual(result["tokens"]["input"], 10)

    def test_no_messages_in_window_gives_zero_not_null(self):
        self.write_main([assistant_line("old", "2026-10-27T14:00:00.000Z", "claude-opus-5-5", 10, 20)])
        result = self.run_script()
        self.assertEqual(result["tokens"], {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0})
        self.assertEqual(result["models"], [])

    def test_never_prints_message_text(self):
        self.write_main([assistant_line("m", "2026-10-27T15:10:00.000Z", "claude-opus-5-5", 10, 20)])
        output = io.StringIO()
        with redirect_stdout(output):
            run_usage.main(["--session", SESSION, "--since", "2026-10-27T15:00:00Z", "--projects-dir", self.projects])
        self.assertNotIn("secret message text", output.getvalue())

    def test_cwd_is_from_the_first_line_that_has_one(self):
        self.write_main([
            {"type": "queue-operation"},
            {"type": "user", "cwd": "/code/start", "timestamp": "2026-10-27T15:00:00.000Z"},
            {"type": "user", "cwd": "/code/start-farm/1", "timestamp": "2026-10-27T15:05:00.000Z"},
            assistant_line("m", "2026-10-27T15:10:00.000Z", "claude-opus-5-5", 10, 20),
        ])
        self.assertEqual(self.run_script()["cwd"], "/code/start")

    def test_model_is_the_last_main_transcript_model_in_the_window(self):
        self.write_main([
            assistant_line("first", "2026-10-27T15:10:00.000Z", "claude-sonnet-5-5", 10, 20),
            assistant_line("last", "2026-10-27T15:20:00.000Z", "claude-opus-5-5", 10, 20),
            assistant_line("local", "2026-10-27T15:25:00.000Z", "<synthetic>", 0, 0),
            assistant_line("after", "2026-10-27T16:30:00.000Z", "claude-haiku-5-5", 10, 20),
        ])
        self.write_subagent("a1", [assistant_line("sub", "2026-10-27T15:30:00.000Z", "claude-haiku-5-5", 1, 2)])
        self.assertEqual(self.run_script()["model"], "claude-opus-5-5")

    def test_no_main_messages_in_window_gives_null_model(self):
        self.write_main([{"type": "user", "cwd": "/code/start"}])
        result = self.run_script()
        self.assertEqual(result["cwd"], "/code/start")
        self.assertIsNone(result["model"])

    def test_bad_since_gives_null_usage(self):
        self.write_main([assistant_line("m", "2026-10-27T15:10:00.000Z", "claude-opus-5-5", 10, 20)])
        result = self.run_script(since="not a time")
        self.assertIsNone(result["tokens"])
        self.assertEqual(result["usageNote"], "could not read transcript: ValueError")
        self.assertIsNone(result["cwd"])
        self.assertIsNone(result["model"])


if __name__ == "__main__":
    unittest.main()
