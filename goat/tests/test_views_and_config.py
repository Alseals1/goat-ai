"""Tests for goat.views, goat.views_json and goat.config (repo selection)."""

import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from goat import board as fb
from goat import config, views, views_json
from goat.tests.fakes import BOARD, FakeClient, raw_item

NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)


def item(**kwargs):
    return fb.GoatItem.from_github(raw_item("5", **kwargs))


class NextStepTests(unittest.TestCase):
    def step(self, **kwargs):
        return views.next_step(item(**kwargs), NOW)

    def test_ready_names_the_command(self):
        self.assertEqual(self.step(stage="plan", goat_status="ready"), "run /goat-plan 5")

    def test_waiting_states(self):
        self.assertEqual(self.step(stage="plan", goat_status="plan-review"), "waits for a person: plan review")
        self.assertEqual(self.step(stage="review", goat_status="merge-review"), "waits for a person: merge review")
        self.assertTrue(self.step(goat_status="answer-question").startswith("waits for a person: answer the question"))
        self.assertEqual(self.step(goat_status="chat"), "waits for a person: run goat.py chat 5")

    def test_working_shows_the_claim_expiry(self):
        future = fb.format_date(NOW + timedelta(minutes=5))
        self.assertEqual(self.step(stage="plan", goat_status="working", claim="plan|s", expires=future),
                         "plan agent working, claim until 12:05 UTC")

    def test_done_and_unknown(self):
        self.assertEqual(self.step(goat_status="done", stage="staging"), "done")
        self.assertEqual(self.step(goat_status="approved", stage="review"), "no goat step (stage 'review')")


class ListTests(unittest.TestCase):
    def test_only_goat_issues_assigned_to_me_and_waiting_ones_first(self):
        client = FakeClient(
            raw_item("1", stage="plan", goat_status="ready", priority="first"),
            raw_item("2", stage="plan", goat_status="plan-review", priority="last"),
            raw_item("3", stage="discovery", goat_status=""),
            raw_item("4", stage="plan", goat_status="ready", assignees=("you",)),
        )
        found = views.goat_items(client, BOARD, "me")
        self.assertEqual([i.id for i in views.list_order(found, NOW)], ["2", "1"])
        self.assertEqual([i.id for i in views.goat_items(client, BOARD)], ["1", "2", "4"])

    def test_lines_have_the_issue_number(self):
        line = views.list_lines([item(stage="plan", goat_status="ready", size="M")], NOW)[0]
        self.assertTrue(line.startswith("#5 "), line)
        self.assertTrue(line.endswith("run /goat-plan 5"), line)


class ShowTests(unittest.TestCase):
    def full_item(self):
        future = fb.format_date(NOW + timedelta(minutes=5))
        comments = [
            {"id": 1, "created_at": "2026-10-27T10:00:00Z", "author": "alice", "body": "A person comment"},
            {"id": 2, "created_at": "2026-10-27T11:00:00Z", "author": "me", "body": fb.render_doc("Spec and plan", "The plan")},
            {"id": 3, "created_at": "2026-10-27T12:00:00Z", "author": "me", "body": "**🤖 Plan agent · note**\n\nHello"},
        ]
        return item(stage="plan", goat_status="working", size="M", claim="plan|abc", expires=future, summary="Sum",
                    pr_url="https://github.com/me/app/pull/9", comments=comments, body="The description", milestone="v0.1")

    def test_markdown_has_every_section_in_order(self):
        text = views.show_markdown(self.full_item(), NOW)
        order = [text.index(h) for h in ("# #5 Issue 5", "## Goat summary", "## Description", "## Doc", "## Comments")]
        self.assertEqual(order, sorted(order))
        self.assertIn("- Milestone: v0.1", text)
        self.assertIn("- Claim: plan|abc (live, until 2026-10-28 12:05 UTC)", text)
        self.assertIn("The description", text)
        self.assertIn("The plan", text)

    def test_the_state_and_doc_comments_are_not_listed_as_comments(self):
        text = views.show_markdown(self.full_item(), NOW)
        comments = text[text.index("## Comments"):]
        self.assertIn("A person comment", comments)
        self.assertIn("Hello", comments)
        self.assertNotIn("The plan", comments)
        self.assertNotIn("goat:state", comments)
        self.assertLess(comments.index("A person comment"), comments.index("Hello"))

    def test_empty_issue(self):
        text = views.show_markdown(item(), NOW)
        self.assertIn("## Doc\n\nNone", text)
        self.assertIn("## Comments (oldest first)\n\nNone", text)

    def test_json_shape(self):
        data = views_json.show_data(self.full_item(), NOW)
        self.assertEqual(data["version"], "0.1")
        self.assertEqual(data["item"]["summary"], "Sum")
        self.assertEqual(data["item"]["claim"]["role"], "plan")
        self.assertTrue(data["item"]["claim"]["live"])
        self.assertEqual(data["doc"].count("The plan"), 1)
        self.assertEqual([c["author"] for c in data["comments"]], ["alice", "me"])
        self.assertEqual(data["item"]["nextStep"], "plan agent working, claim until 12:05 UTC")

    def test_list_json_orders_like_the_text(self):
        data = views_json.list_data([item(stage="plan", goat_status="ready")], NOW)
        self.assertEqual(data["items"][0]["nextRole"], "plan")
        self.assertIsNone(data["items"][0]["claim"])


class RepoConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def checkout(self, origin=None):
        subprocess.run(["git", "init", "-q", self.tmp], check=True, capture_output=True)
        if origin:
            subprocess.run(["git", "-C", self.tmp, "remote", "add", "origin", origin], check=True, capture_output=True)
        return self.tmp

    def test_repo_from_remote_urls(self):
        for url in ("git@github.com:me/app.git", "https://github.com/me/app.git", "https://github.com/me/app",
                    "ssh://git@github.com/me/app.git", "https://github.com/me/app/"):
            self.assertEqual(config.repo_from_remote(url), "me/app", url)
        self.assertIsNone(config.repo_from_remote("https://gitlab.com/me/app.git"))
        self.assertIsNone(config.repo_from_remote(""))

    def test_env_wins_over_the_origin_remote(self):
        path = self.checkout("git@github.com:me/factory.git")
        self.assertEqual(config.load_board({"GOAT_GITHUB_REPO": "me/app"}, path)["repo"], "me/app")

    def test_origin_is_the_fallback(self):
        path = self.checkout("git@github.com:me/app.git")
        self.assertEqual(config.load_board({}, path), {"id": "me/app", "repo": "me/app", "doneLabel": "done"})

    def test_no_repo_is_a_config_error(self):
        path = self.checkout()
        with self.assertRaises(config.ConfigError) as raised:
            config.load_board({}, path)
        self.assertEqual(raised.exception.exit_code, 3)
        self.assertIn("GOAT_GITHUB_REPO", str(raised.exception))

    def test_a_bad_repo_name_is_refused(self):
        with self.assertRaises(config.ConfigError):
            config.load_board({"GOAT_GITHUB_REPO": "not a repo"}, self.checkout())

    def test_the_env_file_is_read_from_the_main_checkout(self):
        path = self.checkout()
        with open(os.path.join(path, ".env"), "w") as f:
            f.write("# c\nexport GOAT_GITHUB_REPO='me/app'\n")
        self.assertEqual(config.project_env(path)["GOAT_GITHUB_REPO"], "me/app")

    def test_lease_minutes_and_board_env_names(self):
        shared = {"leaseMinutes": 20, "leaseMinutesByStage": {"plan": 45}}
        self.assertEqual(config.lease_minutes(shared, "plan"), 45)
        self.assertEqual(config.lease_minutes(shared, "intake"), 20)
        self.assertEqual(config.lease_minutes({}, "plan"), 30)
        self.assertEqual(config.board_env_names({"GOAT_GITHUB_REPO": "x", "PATH": "y"}), ["GOAT_GITHUB_REPO"])


if __name__ == "__main__":
    unittest.main()
