"""Tests for goat.github.GitHubClient with a fake `gh` runner: the exact gh calls, parsing, and retries."""

import json
import unittest

from goat.github import GitHubClient, GitHubError, ItemNotFound, is_goat_issue


class Runner:
    """Records each gh call and answers from a script of (args prefix, stdout or exception)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, args, input_text=None):
        self.calls.append((list(args), input_text))
        matched = None
        for index, (prefix, answer) in enumerate(self.answers):
            if args[:len(prefix)] == prefix:
                matched = index
                break
        if matched is None:
            raise AssertionError("unexpected gh call: {}".format(args))
        prefix, answer = self.answers[matched]
        if isinstance(answer, list):
            # A list of answers: use them in order, keep the last.
            current = answer.pop(0) if len(answer) > 1 else answer[0]
        else:
            current = answer
        if isinstance(current, Exception):
            raise current
        return current


REST_ISSUE = {"number": 14, "title": "T", "html_url": "https://github.com/me/app/issues/14", "state": "open",
              "updated_at": "2026-10-27T10:00:00Z", "labels": [{"name": "stage:plan"}, {"name": "goat:ready"}],
              "assignees": [{"login": "me"}], "milestone": {"title": "v0.1"}, "body": "Body"}


def client(runner):
    return GitHubClient("me/app", runner=runner, sleep=lambda _: None)


class ReadTests(unittest.TestCase):
    def test_me(self):
        runner = Runner((["api", "-X", "GET", "user"], json.dumps({"login": "alice"})))
        self.assertEqual(client(runner).me(), {"id": "alice", "name": "alice"})

    def test_get_item_reads_the_issue_and_all_comments(self):
        comments = [[{"id": 1, "created_at": "t1", "user": {"login": "me"}, "body": "hi"}]]
        runner = Runner(
            (["api", "-X", "GET", "repos/me/app/issues/14"], json.dumps(REST_ISSUE)),
            (["api", "-X", "GET", "repos/me/app/issues/14/comments?per_page=100", "--paginate", "--slurp"], json.dumps(comments)),
        )
        item = client(runner).get_item("14")
        self.assertEqual(item["labels"], ["stage:plan", "goat:ready"])
        self.assertEqual((item["assignees"], item["milestone"], item["repo"]), (["me"], "v0.1", "me/app"))
        self.assertEqual(item["comments"], [{"id": 1, "created_at": "t1", "author": "me", "body": "hi"}])

    def test_a_pull_request_is_not_an_issue(self):
        pr = dict(REST_ISSUE, pull_request={"url": "x"})
        runner = Runner((["api", "-X", "GET", "repos/me/app/issues/14"], json.dumps(pr)))
        with self.assertRaises(ItemNotFound):
            client(runner).get_item(14)

    def test_board_items_keeps_only_goat_issues_and_loads_comments_for_working_ones(self):
        listing = [
            {"number": 1, "title": "plain", "url": "u1", "updatedAt": "t", "state": "OPEN", "labels": [{"name": "bug"}],
             "assignees": [], "milestone": None},
            {"number": 2, "title": "ready", "url": "u2", "updatedAt": "t", "state": "OPEN",
             "labels": [{"name": "goat:ready"}, {"name": "stage:plan"}], "assignees": [{"login": "me"}], "milestone": None},
            {"number": 3, "title": "working", "url": "u3", "updatedAt": "t", "state": "OPEN",
             "labels": [{"name": "goat:working"}, {"name": "stage:plan"}], "assignees": [{"login": "me"}],
             "milestone": {"title": "v1"}},
            {"number": 4, "title": "closed", "url": "u4", "updatedAt": "t", "state": "CLOSED",
             "labels": [{"name": "stage:staging"}], "assignees": [], "milestone": None},
        ]
        runner = Runner(
            (["issue", "list"], json.dumps(listing)),
            (["api", "-X", "GET", "repos/me/app/issues/3/comments?per_page=100", "--paginate", "--slurp"], json.dumps([[]])),
        )
        items = client(runner).board_items()
        self.assertEqual([item["number"] for item in items], [2, 3, 4])
        self.assertNotIn("comments", items[0])
        self.assertEqual(items[1]["comments"], [])
        self.assertEqual((items[1]["milestone"], items[2]["state"]), ("v1", "closed"))
        self.assertEqual(items[0]["repo"], "me/app")
        list_call = runner.calls[0][0]
        self.assertIn("--state", list_call)
        self.assertEqual(list_call[list_call.index("--state") + 1], "all")
        # Only the working issue cost an extra call.
        self.assertEqual(len(runner.calls), 2)

    def test_pull_requests(self):
        prs = [{"number": 9, "url": "https://github.com/me/app/pull/9", "headRefName": "goat/5-thing",
                "baseRefName": "main", "mergeCommit": {"oid": "abc123"}, "mergedAt": "t"}]
        runner = Runner((["pr", "list"], json.dumps(prs)))
        found = client(runner).pull_requests("merged")
        self.assertEqual(found, [{"number": 9, "url": "https://github.com/me/app/pull/9", "branch": "goat/5-thing",
                                  "base": "main", "merge_commit": "abc123", "mergedAt": "t"}])
        self.assertEqual(runner.calls[0][0][runner.calls[0][0].index("--state") + 1], "merged")


class BranchTests(unittest.TestCase):
    def test_default_branch(self):
        runner = Runner((["repo", "view"], "main\n"))
        self.assertEqual(client(runner).default_branch(), "main")
        self.assertEqual(runner.calls[0][0][:3], ["repo", "view", "me/app"])

    def test_an_empty_default_branch_is_an_error(self):
        with self.assertRaises(GitHubError):
            client(Runner((["repo", "view"], "\n"))).default_branch()

    def test_a_commit_is_on_the_branch_when_the_branch_is_ahead_or_identical(self):
        for status, expected in (("behind", True), ("identical", True), ("ahead", False), ("diverged", False)):
            runner = Runner((["api", "repos/me/app/compare/main...abc"], status + "\n"))
            self.assertEqual(client(runner).commit_on_branch("abc", "main"), expected, status)

    def test_a_pr_with_no_merge_commit_has_none(self):
        prs = [{"number": 9, "url": "u", "headRefName": "goat/5-x", "baseRefName": "main", "mergeCommit": None,
                "mergedAt": None}]
        self.assertIsNone(client(Runner((["pr", "list"], json.dumps(prs)))).pull_requests("open")[0]["merge_commit"])


class WriteTests(unittest.TestCase):
    def test_set_labels_is_one_call_with_both_lists(self):
        runner = Runner((["issue", "edit"], ""))
        client(runner).set_labels(5, add=["goat:working"], remove=["goat:ready", "stage:plan"])
        self.assertEqual(runner.calls[0][0], ["issue", "edit", "5", "--repo", "me/app", "--add-label", "goat:working",
                                              "--remove-label", "goat:ready,stage:plan"])

    def test_add_assignees_is_one_call(self):
        runner = Runner((["issue", "edit"], ""))
        client(runner).add_assignees(5, ["me"])
        self.assertEqual(runner.calls[0][0], ["issue", "edit", "5", "--repo", "me/app", "--add-assignee", "me"])
        client(runner).add_assignees(5, [])
        self.assertEqual(len(runner.calls), 1)

    def test_set_labels_with_nothing_to_do_makes_no_call(self):
        runner = Runner()
        client(runner).set_labels(5)
        self.assertEqual(runner.calls, [])

    def test_comment_body_goes_in_on_stdin_as_json(self):
        runner = Runner((["api", "-X", "POST"], json.dumps({"id": 77})))
        text = 'Long "quoted" text\nwith `code` and ünïcode'
        self.assertEqual(client(runner).create_comment(5, text), 77)
        args, stdin = runner.calls[0]
        self.assertEqual(args, ["api", "-X", "POST", "repos/me/app/issues/5/comments", "--input", "-"])
        self.assertEqual(json.loads(stdin), {"body": text})

    def test_update_comment_patches_by_id(self):
        runner = Runner((["api", "-X", "PATCH"], ""))
        client(runner).update_comment(77, "new")
        self.assertEqual(runner.calls[0][0][:4], ["api", "-X", "PATCH", "repos/me/app/issues/comments/77"])

    def test_create_issue_resolves_the_milestone_title(self):
        runner = Runner(
            (["api", "-X", "GET", "repos/me/app/milestones?state=all&per_page=100", "--paginate", "--slurp"],
             json.dumps([[{"title": "v0.1", "number": 3}]])),
            (["api", "-X", "POST", "repos/me/app/issues"], json.dumps({"number": 21})),
        )
        number = client(runner).create_issue("T", "B", ["stage:discovery"], ["me"], milestone="v0.1")
        self.assertEqual(number, 21)
        payload = json.loads(runner.calls[1][1])
        self.assertEqual(payload, {"title": "T", "body": "B", "labels": ["stage:discovery"], "assignees": ["me"], "milestone": 3})

    def test_unknown_milestone_is_an_error(self):
        runner = Runner((["api", "-X", "GET", "repos/me/app/milestones?state=all&per_page=100", "--paginate", "--slurp"], "[[]]"))
        with self.assertRaises(GitHubError) as raised:
            client(runner).create_issue("T", milestone="nope")
        self.assertIn("No milestone", str(raised.exception))

    def test_ensure_label_creates_updates_or_leaves(self):
        runner = Runner((["api", "-X", "POST"], "{}"), (["api", "-X", "PATCH"], "{}"))
        gh = client(runner)
        existing = {"old": {"name": "old", "color": "000000", "description": "d"},
                    "same": {"name": "same", "color": "ABCDEF", "description": "d"}}
        self.assertEqual(gh.ensure_label("new", "ffffff", "d", existing), "created")
        self.assertEqual(gh.ensure_label("old", "ffffff", "d", existing), "updated")
        self.assertEqual(gh.ensure_label("same", "abcdef", "d", existing), "ok")
        self.assertEqual(len(runner.calls), 2)

    def test_label_with_a_colon_is_url_encoded_on_update(self):
        runner = Runner((["api", "-X", "PATCH"], "{}"))
        client(runner).ensure_label("goat:ready", "ffffff", "d", {"goat:ready": {"color": "000000", "description": "d"}})
        self.assertEqual(runner.calls[0][0][3], "repos/me/app/labels/goat%3Aready")


class ErrorTests(unittest.TestCase):
    def test_transient_errors_are_retried(self):
        runner = Runner((["api", "-X", "GET", "user"], [GitHubError("gh api failed: HTTP 503"), json.dumps({"login": "me"})]))
        self.assertEqual(client(runner).me()["id"], "me")
        self.assertEqual(len(runner.calls), 2)

    def test_rate_limit_is_retried_then_raised(self):
        runner = Runner((["api", "-X", "GET", "user"], GitHubError("API rate limit exceeded")))
        with self.assertRaises(GitHubError):
            client(runner).me()
        self.assertEqual(len(runner.calls), 3)

    def test_other_errors_are_not_retried(self):
        runner = Runner((["api", "-X", "GET", "user"], GitHubError("gh api failed: HTTP 401 Bad credentials")))
        with self.assertRaises(GitHubError):
            client(runner).me()
        self.assertEqual(len(runner.calls), 1)

    def test_not_found_is_not_retried_and_has_exit_code_4(self):
        runner = Runner((["api", "-X", "GET", "repos/me/app/issues/9"], ItemNotFound("GitHub: not found")))
        with self.assertRaises(ItemNotFound) as raised:
            client(runner).get_item(9)
        self.assertEqual((len(runner.calls), raised.exception.exit_code), (1, 4))

    def test_non_json_reply(self):
        runner = Runner((["api", "-X", "GET", "user"], "<html>"))
        with self.assertRaises(GitHubError):
            client(runner).me()

    def test_is_goat_issue(self):
        self.assertTrue(is_goat_issue(["bug", "stage:plan"]))
        self.assertTrue(is_goat_issue(["goat:ready"]))
        self.assertFalse(is_goat_issue(["bug", "size:M"]))


if __name__ == "__main__":
    unittest.main()


class BranchCiTests(unittest.TestCase):
    def ci(self, runs, head="abc"):
        runner = Runner((["run", "list"], json.dumps(runs)), (["api", "repos/me/app/commits/dev"], head + "\n"))
        return client(runner).branch_ci("dev")

    def run_(self, sha="abc", status="completed", conclusion="success", workflow="CI", url="u"):
        return {"headSha": sha, "status": status, "conclusion": conclusion, "workflowName": workflow, "url": url}

    def test_no_runs_means_no_ci(self):
        self.assertEqual(self.ci([]), ("none", None))

    def test_states_for_the_head_commit(self):
        self.assertEqual(self.ci([self.run_(url="ok")]), ("pass", "ok"))
        self.assertEqual(self.ci([self.run_(conclusion="failure", url="bad"), self.run_(url="old")]), ("fail", "bad"))
        self.assertEqual(self.ci([self.run_(conclusion="cancelled", url="c")]), ("fail", "c"))
        self.assertEqual(self.ci([self.run_(status="in_progress", conclusion="", url="r")]), ("pending", "r"))
        # The only runs are for an older commit: the head commit's run has not started yet.
        self.assertEqual(self.ci([self.run_(sha="old")]), ("pending", None))

    def test_the_newest_run_of_each_workflow_counts(self):
        runs = [self.run_(url="rerun"), self.run_(conclusion="failure", url="first try"),
                self.run_(workflow="Lint", conclusion="skipped")]
        self.assertEqual(self.ci(runs), ("pass", "rerun"))
