"""Tests for the leaner agent context: `show --brief` and `protocol --sections`."""

import contextlib
import io
import json
import unittest

from goat import board as fb
from goat import cli, views, views_json
from goat.actions import utc_now
from goat.tests.fakes import FakeClient, raw_item
from goat.tests.test_cli_output import CliTestCase


def doc(title, text, comment_id):
    return {"id": comment_id, "created_at": "2026-11-12T10:00:00Z", "author": "me", "body": fb.render_doc(title, text)}


def note(text, comment_id):
    return {"id": comment_id, "created_at": "2026-11-12T10:00:00Z", "author": "me", "body": text}


def item_with_history(comments=10):
    found = [doc("Intake summary", "INTAKE", 1), doc("Spec and plan", "PLAN ONE", 2),
             doc("Review notes (round 1)", "REVIEW ONE", 3), doc("Spec and plan (revision 2)", "PLAN TWO", 4),
             doc("Review notes (round 2)", "REVIEW TWO", 5)]
    found += [note("comment {}".format(n), 100 + n) for n in range(comments)]
    return fb.GoatItem.from_github(raw_item(7, comments=found))


class BriefShowTests(unittest.TestCase):
    def test_newest_section_of_each_kind_in_first_seen_order(self):
        sections = item_with_history().doc_sections
        self.assertEqual([section["title"] for section in views.newest_sections(sections)],
                         ["Intake summary", "Spec and plan (revision 2)", "Review notes (round 2)"])

    def test_brief_markdown_keeps_the_newest_and_says_what_it_left_out(self):
        text = views.show_markdown(item_with_history(), utc_now(), brief=True)
        for kept in ("INTAKE", "PLAN TWO", "REVIEW TWO", "comment 9", "comment 4"):
            self.assertIn(kept, text)
        for dropped in ("PLAN ONE", "REVIEW ONE", "comment 3\n"):
            self.assertNotIn(dropped, text)
        self.assertIn("left out 2 older doc section(s) and 4 older comment(s). Run `goat.py show 7`", text)

    def test_full_markdown_is_unchanged(self):
        text = views.show_markdown(item_with_history(), utc_now())
        self.assertIn("PLAN ONE", text)
        self.assertIn("comment 0", text)
        self.assertNotIn("Brief view", text)

    def test_nothing_left_out_means_no_note(self):
        item = fb.GoatItem.from_github(raw_item(7, comments=[note("hi", 1)]))
        self.assertNotIn("Brief view", views.show_markdown(item, utc_now(), brief=True))

    def test_brief_json(self):
        data = views_json.show_data(item_with_history(), utc_now(), brief=True)
        self.assertEqual(data["leftOut"], {"docSections": 2, "comments": 4})
        self.assertEqual(len(data["comments"]), views.BRIEF_COMMENTS)
        self.assertNotIn("leftOut", views_json.show_data(item_with_history(), utc_now()))


class BriefShowCliTests(CliTestCase):
    def test_show_brief_flag(self):
        client = FakeClient(raw_item(7, comments=[doc("Spec and plan", "PLAN ONE", 2),
                                                  doc("Spec and plan (revision 2)", "PLAN TWO", 4)]))
        code, out, _ = self.run_cli(["show", "7", "--brief"], client)
        self.assertEqual(code, 0)
        self.assertIn("PLAN TWO", out)
        self.assertNotIn("PLAN ONE", out)


class ProtocolCommandTests(unittest.TestCase):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_whole_protocol(self):
        code, out, _ = self.run_cli(["protocol"])
        with open(cli.PROTOCOL_PATH) as f:
            self.assertEqual(out, f.read().rstrip("\n") + "\n")

    def test_sections_keep_the_opening_and_the_order(self):
        code, out, _ = self.run_cli(["protocol", "--sections", "13,1,3"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# G.O.A.T. AI protocol for agents"))
        headings = [line for line in out.splitlines() if line.startswith("## ")]
        self.assertEqual([heading.split(".")[0] for heading in headings], ["## 1", "## 3", "## 13"])

    def test_the_stage_agents_list_is_much_shorter_than_the_whole(self):
        whole = self.run_cli(["protocol"])[1]
        stage = self.run_cli(["protocol", "--sections", "1,2,3,4,5,6,8,9,10,11,12,13"])[1]
        self.assertLess(len(stage), 0.75 * len(whole))

    def test_bad_sections(self):
        code, _, err = self.run_cli(["protocol", "--sections", "1,99"])
        self.assertNotEqual(code, 0)
        self.assertIn("no section 99", err)
        code, _, err = self.run_cli(["protocol", "--sections", "one"])
        self.assertIn("section numbers", err)

    def test_every_skill_names_sections_that_exist(self):
        import glob, re
        with open(cli.PROTOCOL_PATH) as f:
            text = f.read()
        for path in glob.glob(cli.os.path.join(cli.REPO_ROOT, "skills", "*", "SKILL.md")):
            with open(path) as f:
                for numbers in re.findall(r"protocol --sections ([\d,]+)", f.read()):
                    cli.protocol_sections(text, [int(n) for n in numbers.split(",")])


if __name__ == "__main__":
    unittest.main()
