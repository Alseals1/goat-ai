"""Tests for farm/retros.py: note files, the retro item check, and the aggregate files and due rule."""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from farm import board as fb
from farm import retros
from farm.actions import FarmError, ItemNotAvailable
from farm.tests.fakes import BOARD, ME, raw_item

NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)


def item(item_id="11", state="closed", owner=ME["id"]):
    """A closed issue (stage done) by default. state="open" makes an unfinished one."""
    return fb.FarmItem.from_github(raw_item(item_id, stage="staging", farm_status="", state=state, assignees=(owner,)))


class NoteFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def test_save_writes_header_then_markdown(self):
        path = retros.save_note(item(), "## Facts\n\n- None\n", NOW, self.dir)
        self.assertEqual(path, os.path.join(self.dir, "tasks", "me_app-11.md"))
        with open(path) as f:
            text = f.read()
        self.assertTrue(text.startswith("# Retro: #11 Issue 11\n"))
        self.assertIn("- Saved: 2026-10-29T12:00:00Z\n", text)
        self.assertTrue(text.endswith("## Facts\n\n- None\n"))

    def test_has_note(self):
        self.assertFalse(retros.has_note("me/app", "11", self.dir))
        retros.save_note(item(), "x", NOW, self.dir)
        self.assertTrue(retros.has_note("me/app", "11", self.dir))
        self.assertFalse(retros.has_note("me/app", "12", self.dir))
        self.assertFalse(retros.has_note("me/other", "11", self.dir))

    def test_header_reads_back(self):
        path = retros.save_note(item(), "## Facts\n\n- Item: not a header line\n", NOW, self.dir)
        self.assertEqual(retros.read_header(path), {
            "Item": "11", "Repo": "me/app", "Saved": "2026-10-29T12:00:00Z"})

    def test_second_save_replaces_the_note(self):
        retros.save_note(item(), "first", NOW, self.dir)
        path = retros.save_note(item(), "second", NOW, self.dir)
        with open(path) as f:
            text = f.read()
        self.assertNotIn("first", text)
        self.assertIn("second", text)


class CheckRetroItemTests(unittest.TestCase):
    def test_done_item_of_the_user_passes(self):
        retros.check_retro_item(item(), ME["id"], "done")

    def test_done_label_ignores_case(self):
        retros.check_retro_item(item(state="closed"), ME["id"], "done")

    def test_refuses_item_that_is_not_done(self):
        with self.assertRaisesRegex(ItemNotAvailable, "not 'done'"):
            retros.check_retro_item(item(state="open"), ME["id"], "done")

    def test_refuses_another_owners_item(self):
        with self.assertRaisesRegex(ItemNotAvailable, "not assigned to you"):
            retros.check_retro_item(item(owner="99"), ME["id"], "done")


class AggregateDueTests(unittest.TestCase):
    def due(self, notes, days_since_last, min_retros=15, min_days=3):
        last_at = None if days_since_last is None else NOW - timedelta(days=days_since_last)
        return retros.aggregate_due(notes, last_at, NOW, min_retros, min_days)

    def test_due_rule(self):
        cases = [
            (0, None, False),
            (14, 10, False),
            (15, 2, False),
            (15, 3, True),
            (15, None, True),
            (14, None, False),
            (40, 2.9, False),
        ]
        for notes, days, expected in cases:
            self.assertEqual(self.due(notes, days), expected, msg=(notes, days))

    def test_custom_thresholds(self):
        self.assertTrue(self.due(2, 1, min_retros=2, min_days=1))
        self.assertFalse(self.due(1, 1, min_retros=2, min_days=1))
        self.assertFalse(self.due(2, 0.5, min_retros=2, min_days=1))


class AggregateFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def save(self, item_id, when):
        retros.save_note(item(item_id), "x", when, self.dir)

    def test_empty_folder(self):
        self.assertEqual(retros.pending_notes(self.dir), [])
        self.assertEqual(retros.aggregate_reports(self.dir), [])
        self.assertIsNone(retros.last_aggregate_at(self.dir))

    def test_pending_notes_oldest_first_with_header_fields(self):
        self.save("12", NOW)
        self.save("11", NOW - timedelta(hours=1))
        notes = retros.pending_notes(self.dir)
        self.assertEqual([note["id"] for note in notes], ["me_app-11", "me_app-12"])
        self.assertEqual(notes[0]["issue"], "11")
        self.assertEqual(notes[0]["item"], "11")
        self.assertEqual(notes[0]["savedAt"], "2026-10-29T11:00:00Z")
        self.assertTrue(notes[0]["path"].endswith("me_app-11.md"))

    def test_save_aggregate_marks_notes_and_sets_the_time(self):
        self.save("11", NOW)
        self.save("12", NOW)
        path = retros.save_aggregate("# Report\n", ["me_app-11"], NOW, self.dir)
        self.assertEqual(os.path.basename(path), "20261029T120000Z.md")
        self.assertEqual([note["id"] for note in retros.pending_notes(self.dir)], ["me_app-12"])
        self.assertEqual(retros.last_aggregate_at(self.dir), NOW)
        with open(retros.state_path(self.dir)) as f:
            self.assertEqual(json.load(f), {"aggregatedNotes": ["me_app-11"], "lastAggregateAt": "2026-10-29T12:00:00Z"})
        # The temp file was moved into place.
        self.assertFalse(os.path.exists(retros.state_path(self.dir) + ".tmp"))

    def test_read_state_refuses_a_file_that_is_not_an_object(self):
        os.makedirs(self.dir, exist_ok=True)
        with open(retros.state_path(self.dir), "w") as f:
            f.write("[]")
        with self.assertRaises(ValueError):
            retros.read_state(self.dir)

    def test_save_aggregate_refuses_an_unknown_note(self):
        self.save("11", NOW)
        with self.assertRaisesRegex(FarmError, "100-99"):
            retros.save_aggregate("x", ["me_app-11", "100-99"], NOW, self.dir)
        self.assertEqual(retros.aggregate_reports(self.dir), [])
        # Nothing was marked as read.
        self.assertEqual(len(retros.pending_notes(self.dir)), 1)

    def test_reports_newest_first(self):
        self.save("11", NOW)
        first = retros.save_aggregate("a", ["me_app-11"], NOW - timedelta(days=4), self.dir)
        second = retros.save_aggregate("b", [], NOW, self.dir)
        self.assertEqual(retros.aggregate_reports(self.dir), [second, first])


if __name__ == "__main__":
    unittest.main()
