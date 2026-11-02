"""Tests for farm.cli: --json output on stdout, the exit code of each error type, and a full run through the CLI."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from farm import cli, exit_codes, views
from farm.actions import Farm, utc_now
from farm.github import GitHubClient, GitHubError
from farm.tests.fakes import BOARD, ME, SHARED, FakeClient, raw_item


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_cli(self, argv, client=None, env=None, stdin=""):
        """Runs cli.main with a fake client (or a real env, to test config errors). Returns (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            if client is not None:
                stack.enter_context(mock.patch.object(cli, "make_farm", lambda args: self.farm(client, args)))
                stack.enter_context(mock.patch.object(cli, "GitHubClient", lambda repo: client))
                stack.enter_context(mock.patch.object(cli, "load_board", lambda env: BOARD))
            stack.enter_context(mock.patch.object(cli, "project_env", lambda: dict(env or {})))
            stack.enter_context(mock.patch.object(cli.sys, "stdin", io.StringIO(stdin)))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def farm(self, client, args):
        return Farm(client, SHARED, BOARD, ME, getattr(args, "session", None),
                    claims_dir=os.path.join(self.tmp.name, "claims"),
                    runs_file=os.path.join(self.tmp.name, "runs.jsonl"),
                    usage_reader=lambda *_: {"tokens": None, "models": None})


class JsonOutputTests(CliTestCase):
    def test_list_json(self):
        client = FakeClient(raw_item("1"), raw_item("2"))
        code, out, _ = self.run_cli(["list", "--json"], client)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["version"], "0.1")
        self.assertEqual(len(data["items"]), 2)

    def test_list_json_with_no_items(self):
        code, out, _ = self.run_cli(["list", "--json"], FakeClient())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"version": "0.1", "items": []})

    def test_show_json(self):
        code, out, _ = self.run_cli(["show", "#1", "--json"], FakeClient(raw_item("1", body="Why")))
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual((data["item"]["id"], data["description"]), ("1", "Why"))

    def test_show_text(self):
        code, out, _ = self.run_cli(["show", "1"], FakeClient(raw_item("1", title="Fix it")))
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# #1 Fix it\n"))

    def test_setup_repo_json(self):
        client = FakeClient()
        code, out, _ = self.run_cli(["setup-repo", "--check", "--json"], client)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual((data["ok"], data["repo"]), (True, "me/app"))

    def test_text_list_is_unchanged(self):
        client = FakeClient(raw_item("1"))
        code, out, _ = self.run_cli(["list"], client)
        self.assertEqual(code, 0)
        self.assertEqual(out, "\n".join(views.list_lines(views.farm_items(client, BOARD), utc_now())) + "\n")

    def test_list_mine_hides_other_assignees(self):
        client = FakeClient(raw_item("1"), raw_item("2", assignees=("you",)))
        _, out, _ = self.run_cli(["list", "--mine", "--json"], client)
        self.assertEqual([item["id"] for item in json.loads(out)["items"]], ["1"])


class ExitCodeTests(CliTestCase):
    def test_no_repo_is_a_config_error(self):
        with mock.patch("farm.config.origin_repo", return_value=None):
            code, _, err = self.run_cli(["list"], env={})
        self.assertEqual(code, exit_codes.CONFIG)
        self.assertIn("FARM_GITHUB_REPO", err)

    def test_claim_of_an_item_that_is_not_ready(self):
        client = FakeClient(raw_item("1", stage="plan", farm_status="plan-review"))
        code, _, err = self.run_cli(["claim", "1", "--role", "implement", "--session", "s"], client)
        self.assertEqual(code, exit_codes.ITEM_NOT_AVAILABLE)
        self.assertIn("waits for a person", err)

    def test_not_an_issue_number(self):
        code, _, _ = self.run_cli(["show", "TASK-99"], FakeClient(raw_item("1")))
        self.assertEqual(code, exit_codes.ITEM_NOT_AVAILABLE)

    def test_unknown_issue(self):
        code, _, _ = self.run_cli(["show", "42"], FakeClient(raw_item("1")))
        self.assertEqual(code, exit_codes.ITEM_NOT_AVAILABLE)

    def test_renew_of_an_item_claimed_by_another_session(self):
        client = FakeClient(raw_item("1", stage="plan", farm_status="working", claim="plan|other"))
        code, _, err = self.run_cli(["renew", "1", "--session", "s"], client)
        self.assertEqual(code, exit_codes.CLAIM_CONFLICT)
        self.assertIn("farm.py show 1", err)

    def test_github_api_error(self):
        client = GitHubClient("me/app", runner=lambda args, input_text=None: (_ for _ in ()).throw(GitHubError("HTTP 401")),
                              sleep=lambda _: None)
        code, _, err = self.run_cli(["list"], client)
        self.assertEqual(code, exit_codes.GITHUB_API)
        self.assertIn("HTTP 401", err)

    def test_setup_repo_with_problems_exits_1(self):
        client = FakeClient()
        del client.labels["stage:plan"]
        code, out, _ = self.run_cli(["setup-repo", "--check"], client)
        self.assertEqual(code, exit_codes.GENERAL)
        self.assertIn("No label 'stage:plan'.", out)

    def test_headless_agents_cannot_add_ready_issues(self):
        client = FakeClient()
        with mock.patch.dict(os.environ, {"FARM_HEADLESS": "1"}):
            code, _, err = self.run_cli(["add", "Thing", "--ready"], client)
        self.assertEqual(code, exit_codes.GENERAL)
        self.assertIn("may not add items with --ready", err)
        self.assertEqual(client.created, [])


class EndToEndTests(CliTestCase):
    """The CLI as an agent uses it: claim, post, doc, summary, release, and a person's gate in between."""

    def test_a_plan_stage_from_claim_to_gate_and_back_to_approved(self):
        client = FakeClient(raw_item("5", stage="plan", farm_status="ready", size="M", title="Add login"))
        session = ["--session", "s1"]
        code, out, _ = self.run_cli(["claim", "5", "--role", "plan", *session], client)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["role"], "plan")

        self.assertEqual(self.run_cli(["post", "5", "--kind", "note", "--file", "-", *session], client, stdin="Reading code")[0], 0)
        self.assertEqual(self.run_cli(["doc", "5", "--title", "Spec and plan", "--file", "-", *session], client, stdin="# Spec\nBody")[0], 0)
        self.assertEqual(self.run_cli(["summary", "5", "--file", "-", *session], client, stdin="Short")[0], 0)
        code, out, _ = self.run_cli(["release", "5", "--stage", "plan", "--farm-status", "plan-review", "--file", "-", *session],
                                    client, stdin="**State:** planned")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["outcome"], "gate")
        self.assertEqual([l for l in client.labels_of(5) if l.startswith(("farm:", "stage:"))], ["farm:plan-review", "stage:plan"])

        # An agent cannot approve its own gate.
        code, _, err = self.run_cli(["claim", "5", "--role", "implement", *session], client)
        self.assertEqual(code, exit_codes.ITEM_NOT_AVAILABLE)

        # A person approves by swapping the label on GitHub. The next stage's agent can then claim.
        client.set_labels(5, add=["farm:approved"], remove=["farm:plan-review"])
        code, _, _ = self.run_cli(["claim", "5", "--role", "implement", "--session", "s2"], client)
        self.assertEqual(code, 0)
        self.assertIn("stage:implement", client.labels_of(5))

        code, out, _ = self.run_cli(["show", "5"], client)
        self.assertIn("Short", out)
        self.assertIn("# Spec", out)
        self.assertIn("Reading code", out)

    def test_add_prints_the_new_issue(self):
        client = FakeClient()
        code, out, _ = self.run_cli(["add", "New thing", "--file", "-", "--priority", "next"], client, stdin="Details")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("Added #1: https://github.com/me/app/issues/1"), out)
        self.assertIn("prio:next", client.labels_of(1))


if __name__ == "__main__":
    unittest.main()
