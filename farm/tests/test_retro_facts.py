"""Tests for farm/retro_facts.py with temp run records, coordinator logs and fake transcripts."""

import json
import os
import shutil
import tempfile
import unittest

from farm import retro_facts


def user_line(content, **extra):
    return dict({"type": "user", "message": {"role": "user", "content": content}}, **extra)


def tool_use(name):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "id": "t", "input": {}}]}}


def tool_result(text, is_error):
    return user_line([{"type": "tool_result", "tool_use_id": "t", "content": text, "is_error": is_error}])


class RetroFactsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.runs_file = os.path.join(self.tmp, "runs.jsonl")
        self.log_dir = os.path.join(self.tmp, "logs")
        self.projects = os.path.join(self.tmp, "projects")
        os.makedirs(self.log_dir)
        os.makedirs(os.path.join(self.projects, "-repo"))

    def write_runs(self, *records, extra_line=None):
        with open(self.runs_file, "w") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")
            if extra_line:
                f.write(extra_line + "\n")

    def write_log(self, name, data):
        with open(os.path.join(self.log_dir, name), "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def write_transcript(self, session, entries, subagent=False, bad_line=False):
        folder = os.path.join(self.projects, "-repo")
        if subagent:
            folder = os.path.join(folder, session, "subagents")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "agent-1.jsonl")
        else:
            path = os.path.join(folder, session + ".jsonl")
        with open(path, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
            if bad_line:
                f.write("{not json\n")

    def collect(self, item_id="11"):
        return retro_facts.collect(item_id, self.runs_file, self.log_dir, self.projects)

    # ---------- Run records and logs ----------

    def test_only_the_items_runs(self):
        self.write_runs({"item": "11", "role": "plan", "sessionId": "s1"},
                        {"item": "12", "role": "plan", "sessionId": "s2"},
                        extra_line="not json")
        facts = self.collect()
        self.assertEqual([run["sessionId"] for run in facts["runs"]], ["s1"])

    def test_no_runs_file(self):
        self.assertEqual(self.collect()["runs"], [])

    def test_only_the_items_logs_with_fields(self):
        self.write_log("20261029T100000Z-11-plan.json", {
            "session_id": "s1", "is_error": False, "subtype": "success", "num_turns": 9, "duration_ms": 5000,
            "total_cost_usd": 0.5, "result": "long text",
            "permission_denials": [{"tool_name": "Bash"}, {"tool_name": "Bash"}, {"tool_name": "Edit"}]})
        self.write_log("20261029T100000Z-111-plan.json", {"session_id": "other"})
        logs = self.collect()["logs"]
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["role"], "plan")
        self.assertEqual(logs[0]["num_turns"], 9)
        self.assertEqual(logs[0]["permissionDenials"], 3)
        self.assertEqual(logs[0]["deniedTools"], ["Bash", "Edit"])
        self.assertNotIn("result", logs[0])

    def test_unfinished_log_gets_a_note(self):
        self.write_log("20261029T100000Z-11-implement.json", "")
        logs = self.collect()["logs"]
        self.assertEqual(logs[0]["note"], "log is not a finished JSON result")

    def test_sessions_from_runs_and_logs_without_duplicates(self):
        self.write_runs({"item": "11", "sessionId": "s1"})
        self.write_log("20261029T100000Z-11-plan.json", {"session_id": "s1"})
        self.write_log("20261029T110000Z-11-implement.json", {"session_id": "s2"})
        sessions = self.collect()["sessions"]
        self.assertEqual([digest["sessionId"] for digest in sessions], ["s1", "s2"])

    # ---------- Transcript digest ----------

    def test_digest_counts_tools_errors_and_person_messages(self):
        self.write_transcript("s1", [
            user_line("/farm-plan 15"),
            user_line([{"type": "text", "text": "skill text"}], isMeta=True),
            tool_use("Bash"), tool_use("Bash"), tool_use("Read"),
            tool_result("ok", False),
            tool_result("Exit code 1\n" + "x" * 500, True),
            user_line([{"type": "text", "text": "Please   use the other API"}]),
        ], bad_line=True)
        self.write_transcript("s1", [
            user_line("Subagent prompt"), tool_use("Grep"), tool_result("no files", True),
        ], subagent=True)
        digest = retro_facts.transcript_digest("s1", self.projects)
        self.assertEqual(digest["files"], 2)
        self.assertEqual(digest["toolCalls"], {"Bash": 2, "Read": 1, "Grep": 1})
        self.assertEqual(digest["toolErrorCount"], 2)
        self.assertEqual(len(digest["toolErrors"][0]), retro_facts.TOOL_ERROR_CHARS)
        self.assertTrue(digest["toolErrors"][0].startswith("Exit code 1 xxx"))
        self.assertEqual(digest["personMessages"], ["/farm-plan 15", "Please use the other API"])
        self.assertEqual(digest["skippedLines"], 1)
        self.assertIsNone(digest["note"])

    def test_long_person_message_is_cut(self):
        self.write_transcript("s1", [user_line("y" * 2000)])
        message = retro_facts.transcript_digest("s1", self.projects)["personMessages"][0]
        self.assertEqual(len(message), retro_facts.PERSON_MESSAGE_CHARS)
        self.assertTrue(message.endswith("..."))

    def test_missing_transcript_gives_a_note(self):
        digest = retro_facts.transcript_digest("nope", self.projects)
        self.assertEqual(digest["note"], "transcript not found on this machine")
        self.assertEqual(digest["files"], 0)

    def test_unreadable_transcript_gives_a_note(self):
        path = os.path.join(self.projects, "-repo", "s1.jsonl")
        os.makedirs(path)  # A folder, so open() fails.
        digest = retro_facts.transcript_digest("s1", self.projects)
        self.assertTrue(digest["note"].startswith("could not read transcript"))

    # ---------- Markdown ----------

    def test_markdown_has_every_section(self):
        self.write_runs({"item": "11", "role": "plan", "sessionId": "s1", "outcome": "gate",
                         "tokens": {"input": 1, "output": 2, "cacheRead": 3, "cacheWrite": 4}})
        self.write_log("20261029T100000Z-11-plan.json", {"session_id": "s1", "num_turns": 3,
                                                         "permission_denials": [{"tool_name": "Bash"}]})
        self.write_transcript("s1", [tool_use("Bash")])
        text = retro_facts.facts_markdown(self.collect(), "#11 Fix login")
        self.assertIn("# Retro facts: #11 Fix login", text)
        self.assertIn("1 in, 2 out, 3 cache read, 4 cache write", text)
        self.assertIn("Permission denials: 1 (Bash)", text)
        self.assertIn("- Tool calls: Bash 1", text)

    def test_markdown_with_no_data(self):
        text = retro_facts.facts_markdown(self.collect(), "11")
        self.assertIn("None on this machine.", text)
        self.assertIn("None found.", text)


if __name__ == "__main__":
    unittest.main()
