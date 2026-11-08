"""Tests for goat.team (the named team), the `team` command, and where the names show up."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from goat import board as fb
from goat import cli, config, coordinator, merge, team
from goat.actions import post_header
from goat.tests.fakes import FakeClient, raw_item

SHARED = {"agents": {
    "plan": {"name": "Theo", "voice": "Calm."},
    "review": {"name": "Priya", "voice": "Sharp and fair."},
    "retro": {"name": "Sam", "voice": "Reflective."},
    "dispatcher": {"name": "Dex", "voice": "Keeps things moving."},
}}


class PersonaTests(unittest.TestCase):
    def test_signature_puts_the_name_before_the_role(self):
        self.assertEqual(team.signature(SHARED, "review"), "Priya · Review agent")
        self.assertEqual(team.dispatcher_signature(SHARED), "Dex · Dispatcher")

    def test_a_role_with_no_name_keeps_its_plain_title(self):
        self.assertEqual(team.signature(SHARED, "intake"), "Intake agent")
        self.assertEqual(team.signature({}, "plan"), "Plan agent")
        self.assertEqual(team.signature(None, "plan"), "Plan agent")
        self.assertEqual(team.dispatcher_signature({}), "G.O.A.T.")
        self.assertEqual(team.dispatcher_name({}), "G.O.A.T.")

    def test_the_aggregate_retro_uses_the_retro_agents_name(self):
        self.assertEqual(team.signature(SHARED, "retro-aggregate"), "Sam · Retro agent")

    def test_the_qa_agent_title_keeps_its_capitals(self):
        self.assertEqual(team.title("qa"), "QA agent")
        self.assertEqual(team.signature({"agents": {"qa": {"name": "Quinn"}}}, "qa"), "Quinn · QA agent")

    def test_damaged_config_never_raises(self):
        for bad in ({"agents": "nope"}, {"agents": {"plan": "Theo"}}, {"agents": {"plan": {"name": None}}}):
            self.assertEqual(team.signature(bad, "plan"), "Plan agent")

    def test_owner(self):
        self.assertEqual(team.owner({"owner": {"name": " Pat ", "title": "CEO"}}), {"name": "Pat", "title": "CEO"})
        self.assertEqual(team.owner({}), {"name": "", "title": ""})
        self.assertEqual(team.owner({"owner": "Pat"}), {"name": "", "title": ""})

    def test_read_owner_ignores_a_missing_or_broken_file(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(team.read_owner(os.path.join(directory, "none.json"))["name"], "")
            bad = os.path.join(directory, "bad.json")
            with open(bad, "w") as f:
                f.write("{")
            self.assertEqual(team.read_owner(bad)["name"], "")

    def test_lines(self):
        text = team.lines(SHARED, {"owner": {"name": "Pat", "title": "CEO"}})
        self.assertEqual(text[0], "Owner: Pat, CEO")
        self.assertIn("Plan agent: Theo. Voice: Calm.", text)
        self.assertIn("Intake agent: (no name set)", text)
        self.assertEqual(team.lines(SHARED, None, "plan"), ["Plan agent: Theo. Voice: Calm."])


class ShippedCastTests(unittest.TestCase):
    """The cast that ships in goat-config.json."""

    def setUp(self):
        self.shared = config.load_shared()

    def test_every_role_has_a_name_and_a_voice(self):
        for role in ("intake", "plan", "implement", "review", "retro", "qa", "dispatcher"):
            who = team.persona(self.shared, role)
            self.assertTrue(who["name"], role)
            self.assertTrue(who["voice"], role)

    def test_names_are_unique_single_first_names(self):
        names = [team.persona(self.shared, role)["name"] for role in ("intake", "plan", "implement", "review", "retro", "qa", "dispatcher")]
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            self.assertRegex(name, r"^[A-Z][a-z]+$")

    def test_voices_stay_short_and_do_not_pose_as_people(self):
        for role, entry in team.cast(self.shared).items():
            self.assertLessEqual(len(entry["voice"]), 160, role)
            for phrase in ("years old", "i am a human", "i'm a human", "human being", "my family", "my weekend"):
                self.assertNotIn(phrase, entry["voice"].lower(), role)

    def test_the_owner_is_not_stored_in_the_shared_config(self):
        self.assertNotIn("owner", self.shared)


class WhereTheNamesShowUpTests(unittest.TestCase):
    def test_post_header_has_name_role_kind_and_version(self):
        self.assertEqual(post_header("review", "handoff", "1.2.3", SHARED), "**🤖 Priya · Review agent · handoff · G.O.A.T. 1.2.3**")

    def test_review_can_still_count_its_rounds(self):
        # The Review skill counts comments that contain "Review agent · handoff".
        self.assertIn("Review agent · handoff", post_header("review", "handoff", "1.2.3", SHARED))
        self.assertIn("Review agent · handoff", post_header("review", "handoff", "1.2.3", None))

    def test_post_header_without_a_cast_is_the_plain_form(self):
        self.assertEqual(post_header("plan", "note", "1.0"), "**🤖 Plan agent · note · G.O.A.T. 1.0**")

    def test_the_dispatcher_signs_merge_and_stranded_comments(self):
        client = FakeClient(raw_item("5", stage="review", goat_status="merge-review", pr_url="https://github.com/me/app/pull/9"))
        client.merged_prs = [{"number": 9, "url": "https://github.com/me/app/pull/9", "branch": "goat/5-x", "base": "main",
                              "merge_commit": "c", "mergedAt": "t"}]
        items = [fb.GoatItem.from_github(raw) for raw in client.board_items()]
        merge.advance_merged(client, items, "me", datetime(2026, 11, 2, tzinfo=timezone.utc))
        self.assertTrue(client.comment_bodies(5)[-1].startswith("**🤖 Dex · Dispatcher** · The PR is merged"))

    def test_the_stranded_comment_is_signed_too(self):
        text = merge.stranded_text(None, [{"number": 3, "base": "goat/5-1-x"}], "main", SHARED)
        self.assertIn("**🤖 Dex · Dispatcher**", text)

    def test_stall_comment_and_notification_use_the_dispatcher_and_the_owner_name(self):
        item = fb.GoatItem.from_github(raw_item("5", stage="plan", goat_status="ready"))
        self.assertIn("**🤖 Dex · Dispatcher · stalled**", coordinator.stall_comment(item, "plan", None, SHARED))
        self.assertIn("**🤖 G.O.A.T. · stalled**", coordinator.stall_comment(item, "plan", None, None))


class TeamCommandTests(unittest.TestCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(out):
            path = os.path.join(directory, "config.json")
            with open(path, "w") as f:
                json.dump({"owner": {"name": "Pat", "title": "CEO"}}, f)
            with mock.patch.object(coordinator, "CONFIG_FILE", path):
                code = cli.main(["team", *argv])
        return code, out.getvalue()

    def test_prints_the_whole_team_and_the_owner(self):
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("Owner: Pat, CEO", out)
        for name in ("Maya", "Theo", "Jordan", "Priya", "Sam", "Dex"):
            self.assertIn(name, out)

    def test_one_role(self):
        _, out = self.run_cli("--role", "review")
        self.assertEqual(out.count("\n"), 1)
        self.assertIn("Priya", out)
        self.assertNotIn("Owner", out)

    def test_json(self):
        _, out = self.run_cli("--json", "--role", "plan")
        data = json.loads(out)
        self.assertEqual(data["version"], "0.1")
        self.assertEqual(data["team"]["plan"]["name"], "Theo")
        self.assertEqual(data["owner"], {"name": "Pat", "title": "CEO"})


if __name__ == "__main__":
    unittest.main()
