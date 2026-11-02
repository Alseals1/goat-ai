"""Tests for the retro commands in farm/cli.py, with a fake GitHub client and temp folders."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from farm import cli, exit_codes, retro_facts, retros
from farm.actions import Farm
from farm.tests.fakes import BOARD, ME, SHARED, FakeClient, raw_item


class RetroCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.retros_dir = os.path.join(self.tmp, "retros")
        self.client = FakeClient(
            raw_item("11", stage="staging", farm_status="done", state="closed"),
            raw_item("12", stage="review", farm_status="merge-review"),
            raw_item("13", stage="staging", farm_status="done", state="closed", assignees=("you",)),
        )
        # The real ~/.software-farm and ~/.claude folders are never touched.
        self.patch(retros, "RETROS_DIR", self.retros_dir)
        self.patch(retro_facts, "RUNS_FILE", os.path.join(self.tmp, "runs.jsonl"))
        self.patch(retro_facts, "LOG_DIR", os.path.join(self.tmp, "logs"))
        self.patch(retro_facts, "PROJECTS_DIR", os.path.join(self.tmp, "projects"))

    def patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_farm(self, args):
        return Farm(self.client, SHARED, BOARD, ME, None)

    def run_cli(self, argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "make_farm", self.make_farm), \
                mock.patch.object(cli.sys, "stdin", io.StringIO(stdin)), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_retro_save_writes_the_note_for_a_done_item(self):
        code, out, _ = self.run_cli(["retro-save", "11", "--file", "-"], "## Facts\n\n- None\n")
        self.assertEqual(code, 0)
        path = os.path.join(self.retros_dir, "tasks", "me_app-11.md")
        self.assertIn(path, out)
        with open(path) as f:
            self.assertIn("## Facts", f.read())
        self.assertEqual(self.client.writes, [])
        self.assertEqual([c for c in self.client.comment_bodies(11)], [])

    def test_retro_save_refuses_an_item_that_is_not_done(self):
        code, _, err = self.run_cli(["retro-save", "12", "--file", "-"], "x")
        self.assertEqual(code, exit_codes.ITEM_NOT_AVAILABLE)
        self.assertIn("not 'done'", err)
        self.assertFalse(os.path.exists(os.path.join(self.retros_dir, "tasks", "me_app-12.md")))

    def test_retro_save_refuses_another_owners_item(self):
        code, _, err = self.run_cli(["retro-save", "13", "--file", "-"], "x")
        self.assertEqual(code, exit_codes.ITEM_NOT_AVAILABLE)
        self.assertIn("not assigned to you", err)

    def test_retro_facts_markdown_and_json(self):
        code, out, _ = self.run_cli(["retro-facts", "11"])
        self.assertEqual(code, 0)
        self.assertIn("# Retro facts: #11 Issue 11", out)
        code, out, _ = self.run_cli(["retro-facts", "11", "--json"])
        self.assertEqual(code, 0)
        self.assertIn('"item": "11"', out)

    def test_retro_pending_and_aggregate_save_round_trip(self):
        self.run_cli(["retro-save", "11", "--file", "-"], "note")
        code, out, _ = self.run_cli(["retro-pending", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual([note["id"] for note in data["notes"]], ["me_app-11"])
        self.assertEqual((data["lastAggregateAt"], data["reports"]), (None, []))

        code, out, _ = self.run_cli(["retro-aggregate-save", "--notes", "me_app-11", "--file", "-"], "# Report")
        self.assertEqual(code, 0)
        self.assertIn("Marked 1 note(s) as read.", out)
        data = json.loads(self.run_cli(["retro-pending", "--json"])[1])
        self.assertEqual(data["notes"], [])
        self.assertIsNotNone(data["lastAggregateAt"])
        self.assertEqual(len(data["reports"]), 1)

    def test_partial_report_with_no_notes_keeps_them_waiting(self):
        # The aggregate skill saves a partial report this way when `add` fails after some tasks were added.
        self.run_cli(["retro-save", "11", "--file", "-"], "note")
        code, out, _ = self.run_cli(["retro-aggregate-save", "--notes", "", "--file", "-"], "# Partial")
        self.assertEqual(code, 0)
        self.assertIn("Marked 0 note(s) as read.", out)
        data = json.loads(self.run_cli(["retro-pending", "--json"])[1])
        self.assertEqual([note["id"] for note in data["notes"]], ["me_app-11"])
        self.assertEqual(len(data["reports"]), 1)

    def test_retro_pending_text(self):
        code, out, _ = self.run_cli(["retro-pending"])
        self.assertEqual(code, 0)
        self.assertIn("Last aggregate: never", out)

    def test_aggregate_save_refuses_an_unknown_note(self):
        code, _, err = self.run_cli(["retro-aggregate-save", "--notes", "100-99", "--file", "-"], "x")
        self.assertEqual(code, exit_codes.GENERAL)
        self.assertIn("No retro note with these ids: 100-99", err)

    def test_pending_commands_read_no_github(self):
        def no_farm(args):
            self.fail("make_farm must not be called")
        with mock.patch.object(cli, "make_farm", no_farm), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["retro-pending"]), 0)


if __name__ == "__main__":
    unittest.main()
