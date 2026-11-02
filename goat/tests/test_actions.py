"""Tests for goat.actions: claim, renew, post, doc, summary, release and add, against the in-memory FakeClient."""

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from goat import board as fb
from goat.actions import ClaimConflict, Goat, GoatError, ItemNotAvailable, parse_item_ref, refuse_ready_when_headless
from goat.config import ConfigError
from goat.github import ItemNotFound
from goat.tests.fakes import BOARD, ME, SHARED, FakeClient, raw_item

NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)
NO_USAGE = {"tokens": None, "models": None, "usageNote": "test", "cwd": None, "model": None}


class ActionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def goat(self, client, session="s1", now=NOW):
        return Goat(client, SHARED, BOARD, ME, session, now=lambda: now, claims_dir=os.path.join(self.tmp, "claims"),
                    runs_file=os.path.join(self.tmp, "runs.jsonl"), usage_reader=lambda *_: NO_USAGE, version="9.9.9")

    def load(self, client, number="5"):
        return fb.GoatItem.from_github(client.get_item(number))


class ParseItemRefTests(unittest.TestCase):
    def test_accepts_numbers_hashes_and_links(self):
        self.assertEqual(parse_item_ref("14"), (None, "14"))
        self.assertEqual(parse_item_ref("#14"), (None, "14"))
        self.assertEqual(parse_item_ref("https://github.com/me/app/issues/14"), ("me/app", "14"))

    def test_rejects_everything_else(self):
        for ref in ("SFP-14", "abc", "0", "", "1.5"):
            self.assertIsNone(parse_item_ref(ref), ref)

    def test_refuses_ready_only_for_headless_agents(self):
        refuse_ready_when_headless(True, {})
        refuse_ready_when_headless(False, {"GOAT_HEADLESS": "1"})
        with self.assertRaises(GoatError):
            refuse_ready_when_headless(True, {"GOAT_HEADLESS": "1"})


class ClaimTests(ActionTestCase):
    def test_claim_writes_state_then_labels_and_reads_back(self):
        client = FakeClient(raw_item("5", stage="plan", goat_status="ready"))
        result = self.goat(client).claim("5", "plan")
        self.assertEqual(result["role"], "plan")
        self.assertEqual(result["expiresAt"], "2026-10-28T12:30:00Z")
        item = self.load(client)
        self.assertEqual((item.claim, item.goat_status, item.status), ("plan|s1", "working", "plan"))
        self.assertEqual(item.claim_expires, NOW + timedelta(minutes=30))
        # State comment first, labels second: a crash between them leaves a claim on a ready issue, which just expires.
        self.assertEqual([kind for kind, _, _ in client.writes], ["comment", "labels"])

    def test_claim_reuses_the_state_comment(self):
        client = FakeClient(raw_item("5", stage="plan", goat_status="ready", summary="old"))
        self.goat(client).claim("5", "plan")
        self.assertEqual([kind for kind, _, _ in client.writes], ["update_comment", "labels"])
        self.assertEqual(self.load(client).summary, "old")

    def test_approved_moves_the_stage_to_the_claiming_role(self):
        client = FakeClient(raw_item("5", stage="intake", goat_status="approved"))
        self.goat(client).claim("5", "plan")
        item = self.load(client)
        self.assertEqual((item.status, item.goat_status), ("plan", "working"))
        self.assertNotIn("stage:intake", item.labels)

    def test_claim_with_a_link_or_hash(self):
        client = FakeClient(raw_item("5", stage="plan", goat_status="ready"))
        self.goat(client).claim("https://github.com/me/app/issues/5", "plan")

    def test_wrong_role_is_not_available(self):
        client = FakeClient(raw_item("5", stage="plan", goat_status="ready"))
        with self.assertRaises(ItemNotAvailable) as raised:
            self.goat(client).claim("5", "implement")
        self.assertIn("not ready for implement", str(raised.exception))
        self.assertEqual(client.writes, [])

    def test_not_a_goat_issue(self):
        client = FakeClient(raw_item("5", stage="discovery", goat_status=""))
        with self.assertRaises(ItemNotAvailable):
            self.goat(client).claim("5", "intake")

    def test_someone_elses_issue(self):
        client = FakeClient(raw_item("5", stage="plan", goat_status="ready", assignees=("you",)))
        with self.assertRaises(ItemNotAvailable) as raised:
            self.goat(client).claim("5", "plan")
        self.assertIn("not assigned to me", str(raised.exception))

    def test_unassigned_issue_is_refused(self):
        client = FakeClient(raw_item("5", stage="plan", goat_status="ready", assignees=()))
        with self.assertRaises(ItemNotAvailable):
            self.goat(client).claim("5", "plan")

    def test_missing_issue_exits_with_item_not_available(self):
        with self.assertRaises(ItemNotFound) as raised:
            self.goat(FakeClient()).claim("77", "plan")
        self.assertEqual(raised.exception.exit_code, 4)

    def test_other_repo_link_is_refused(self):
        with self.assertRaises(ItemNotAvailable):
            self.goat(FakeClient(raw_item("5"))).claim("https://github.com/other/repo/issues/5", "intake")

    def test_needs_a_session_id(self):
        with self.assertRaises(ConfigError):
            self.goat(FakeClient(raw_item("5")), session=None).claim("5", "intake")

    def test_missing_labels_stop_the_claim_before_any_write(self):
        client = FakeClient(raw_item("5", stage="plan", goat_status="ready"))
        del client.labels["goat:working"]
        with self.assertRaises(GoatError) as raised:
            self.goat(client).claim("5", "plan")
        self.assertIn("setup-repo", str(raised.exception))
        self.assertEqual(client.writes, [])

    def test_claim_lost_when_another_session_writes_in_between(self):
        client = FakeClient(raw_item("5", stage="plan", goat_status="ready"))

        def steal(kind, fake):
            if kind == "labels":
                comment = next(c for c in fake.items[5]["comments"] if fb.is_state_comment(c["body"]))
                comment["body"] = fb.render_state({"claim": "plan|other", "claimExpires": "2026-10-28T12:30:00Z"})

        client.after_write = steal
        with self.assertRaises(ClaimConflict) as raised:
            self.goat(client).claim("5", "plan")
        self.assertIn("Claim lost", str(raised.exception))

    def test_takeover_of_a_stale_claim_is_recorded(self):
        past = fb.format_date(NOW - timedelta(minutes=5))
        client = FakeClient(raw_item("5", stage="implement", goat_status="working", claim="implement|dead", expires=past))
        result = self.goat(client).claim("5", "implement")
        self.assertTrue(result["tookOverStale"])
        self.assertEqual(result["previousClaim"], "implement|dead")

    def test_live_claim_of_another_session_is_not_available(self):
        future = fb.format_date(NOW + timedelta(minutes=5))
        client = FakeClient(raw_item("5", stage="implement", goat_status="working", claim="implement|other", expires=future))
        with self.assertRaises(ItemNotAvailable):
            self.goat(client).claim("5", "implement")

    def test_chat_lets_the_stage_agent_claim_again(self):
        client = FakeClient(raw_item("5", stage="plan", goat_status="chat"))
        self.goat(client).claim("5", "plan")
        self.assertEqual(self.load(client).goat_status, "working")


class ClaimedActionTests(ActionTestCase):
    def claimed(self, stage="plan", **kwargs):
        client = FakeClient(raw_item("5", stage=stage, goat_status="ready", **kwargs))
        goat = self.goat(client)
        goat.claim("5", stage)
        client.writes.clear()
        return client, goat

    def test_renew_moves_the_expiry(self):
        client, _ = self.claimed()
        later = self.goat(client, now=NOW + timedelta(minutes=20))
        self.assertEqual(later.renew("5"), "2026-10-28T12:50:00Z")
        self.assertEqual(self.load(client).claim_expires, NOW + timedelta(minutes=50))

    def test_every_write_needs_the_own_claim(self):
        client, _ = self.claimed()
        other = self.goat(client, session="other")
        for call in (lambda: other.renew("5"), lambda: other.post("5", "note", "x"),
                     lambda: other.add_doc_section("5", "T", "x"), lambda: other.set_summary("5", "x"),
                     lambda: other.release("5", "plan", "ready", "h")):
            with self.assertRaises(ClaimConflict):
                call()
        self.assertEqual(client.writes, [])

    def test_a_person_changing_the_label_stops_the_agent(self):
        client, goat = self.claimed()
        client.set_labels(5, add=["goat:plan-review"], remove=["goat:working"])
        client.writes.clear()
        with self.assertRaises(ClaimConflict) as raised:
            goat.post("5", "note", "x")
        self.assertIn("A person changed it", str(raised.exception))

    def test_post_has_header_and_kinds(self):
        client, goat = self.claimed()
        goat.post("5", "checkpoint", "Done a thing")
        body = client.comment_bodies(5)[-1]
        self.assertTrue(body.startswith("**🤖 Plan agent · checkpoint · G.O.A.T. 9.9.9**"))
        self.assertTrue(body.endswith("Done a thing"))
        with self.assertRaises(GoatError):
            goat.post("5", "handoff", "x")

    def test_chat_post_has_the_resume_command_and_a_mention(self):
        client, goat = self.claimed()
        goat.post("5", "chat", "Let's talk")
        body = client.comment_bodies(5)[-1]
        self.assertIn("python3 ~/.goat-ai/goat.py chat 5", body)
        self.assertIn("@me", body)

    def test_question_post_mentions_the_owner(self):
        client, goat = self.claimed()
        goat.post("5", "question", "Which one?")
        self.assertIn("@me please answer the question", client.comment_bodies(5)[-1])

    def test_inline_code_is_kept_as_markdown(self):
        client, goat = self.claimed()
        goat.post("5", "note", "Run `make test`")
        self.assertIn("`make test`", client.comment_bodies(5)[-1])

    def test_doc_section_is_a_comment_and_repeat_updates_it(self):
        client, goat = self.claimed()
        goat.add_doc_section("5", "Spec and plan", "First")
        goat.add_doc_section("5", "Spec and plan", "Second")
        goat.add_doc_section("5", "Spec and plan (revision 2)", "Third")
        item = self.load(client)
        self.assertEqual([s["title"] for s in item.doc_sections], ["Spec and plan", "Spec and plan (revision 2)"])
        self.assertIn("Second", item.doc_sections[0]["body"])
        self.assertNotIn("First", item.doc_sections[0]["body"])

    def test_summary_is_saved_and_the_claim_is_kept(self):
        client, goat = self.claimed()
        goat.set_summary("5", "Short summary")
        item = self.load(client)
        self.assertEqual((item.summary, item.claim), ("Short summary", "plan|s1"))

    def test_summary_over_the_limit_is_refused_before_any_read(self):
        client, goat = self.claimed()
        with self.assertRaises(GoatError):
            goat.set_summary("5", "x" * 2001)
        self.assertEqual(client.writes, [])


class ReleaseTests(ActionTestCase):
    def claimed(self, stage="plan", size="", **kwargs):
        client = FakeClient(raw_item("5", stage=stage, goat_status="ready", size=size, **kwargs))
        goat = self.goat(client)
        goat.claim("5", stage)
        client.writes.clear()
        return client, goat

    def test_release_to_a_gate(self):
        client, goat = self.claimed()
        run = goat.release("5", "plan", "plan-review", "**State:** done", summary="Sum")
        item = self.load(client)
        self.assertEqual((item.goat_status, item.status, item.claim, item.summary), ("plan-review", "plan", "", "Sum"))
        self.assertEqual(item.claim_expires, None)
        self.assertEqual(run["outcome"], "gate")
        handoff = client.comment_bodies(5)[-1]
        self.assertTrue(handoff.startswith("**🤖 Plan agent · handoff · G.O.A.T. 9.9.9**"))
        self.assertIn("@me please review the plan", handoff)
        # Handoff, then labels, then the state: a crash in between leaves a claim the same session can finish.
        self.assertEqual([kind for kind, _, _ in client.writes], ["comment", "labels", "update_comment"])

    def test_release_sets_stage_size_and_pr(self):
        client, goat = self.claimed(stage="implement", size="M")
        goat.release("5", "review", "ready", "handoff", size="L", pr_url="https://github.com/me/app/pull/9")
        item = self.load(client)
        self.assertEqual((item.status, item.goat_status, item.size), ("review", "ready", "L"))
        self.assertEqual(item.pr_url, "https://github.com/me/app/pull/9")
        self.assertEqual(sorted(l for l in item.labels if l.startswith(("size:", "stage:", "goat:"))),
                         ["goat:ready", "size:L", "stage:review"])

    def test_send_back_is_recorded(self):
        client, goat = self.claimed(stage="review")
        run = goat.release("5", "implement", "ready", "needs work")
        self.assertEqual(run["outcome"], "sent-back")
        self.assertEqual(self.load(client).stage, "implement")

    def test_question_and_chat_outcomes(self):
        client, goat = self.claimed()
        self.assertEqual(goat.release("5", "plan", "answer-question", "q")["outcome"], "question")

    def test_run_record_is_appended_and_claim_record_removed(self):
        client, goat = self.claimed()
        goat.release("5", "plan", "plan-review", "h")
        with open(os.path.join(self.tmp, "runs.jsonl")) as f:
            self.assertEqual(len(f.read().strip().splitlines()), 1)
        self.assertIsNone(goat.read_claim_record("5"))

    def test_agents_cannot_set_approved_working_or_done(self):
        client, goat = self.claimed()
        for status in ("approved", "working", "done", "nonsense"):
            with self.assertRaises(GoatError):
                goat.release("5", "plan", status, "h")
        with self.assertRaises(GoatError):
            goat.release("5", "nonsense", "ready", "h")
        with self.assertRaises(GoatError):
            goat.release("5", "plan", "ready", "h", size="XXL")
        self.assertEqual(client.writes, [])

    def test_release_can_be_run_again_after_the_state_write_failed(self):
        client, goat = self.claimed()
        calls = {"n": 0}
        real = client.update_comment

        def flaky(comment_id, body):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("network")
            return real(comment_id, body)

        client.update_comment = flaky
        with self.assertRaises(RuntimeError):
            goat.release("5", "plan", "plan-review", "h")
        # Labels already moved, claim still ours.
        item = self.load(client)
        self.assertEqual((item.goat_status, item.claim_session), ("plan-review", "s1"))
        goat.release("5", "plan", "plan-review", "h")
        item = self.load(client)
        self.assertEqual((item.goat_status, item.claim), ("plan-review", ""))
        # The handoff was posted once.
        self.assertEqual(sum("handoff" in body for body in client.comment_bodies(5)), 1)

    def test_resuming_release_still_refuses_a_different_outcome(self):
        client, goat = self.claimed()
        client.set_labels(5, add=["goat:plan-review"], remove=["goat:working"])
        with self.assertRaises(ClaimConflict):
            goat.release("5", "plan", "ready", "h")

    def test_release_that_did_not_apply_is_reported(self):
        client, goat = self.claimed()
        real = client.update_comment
        client.update_comment = lambda comment_id, body: None  # the write silently does nothing
        with self.assertRaises(GoatError) as raised:
            goat.release("5", "plan", "plan-review", "h")
        self.assertIn("Run release again", str(raised.exception))

    def test_no_mention_without_an_assignee_or_for_ready(self):
        client, goat = self.claimed()
        goat.release("5", "plan", "ready", "h")
        self.assertNotIn("@me", client.comment_bodies(5)[-1])


class AddTests(ActionTestCase):
    def test_default_is_a_discovery_issue_the_goat_ignores(self):
        client = FakeClient()
        item, _ = self.goat(client).add("Fix login", "Details here")
        self.assertEqual((item.name, item.body, item.status), ("Fix login", "Details here", "discovery"))
        self.assertEqual((item.goat_status, item.owner_ids), ("", []))
        self.assertFalse(item.is_goat_item)

    def test_ready_starts_intake_assigned_to_me(self):
        item, _ = self.goat(FakeClient()).add("Fix login", ready=True)
        self.assertEqual((item.status, item.goat_status, item.owner_ids), ("intake", "ready", ["me"]))
        self.assertTrue(item.is_goat_item)

    def test_priority_and_milestone(self):
        item, _ = self.goat(FakeClient()).add("Fix login", priority="first", milestone="v0.1")
        self.assertEqual((item.priority, item.milestone), ("first", "v0.1"))
        with self.assertRaises(GoatError):
            self.goat(FakeClient()).add("x", priority="urgent")

    def test_empty_title_is_refused(self):
        with self.assertRaises(GoatError):
            self.goat(FakeClient()).add("  ")


if __name__ == "__main__":
    unittest.main()
