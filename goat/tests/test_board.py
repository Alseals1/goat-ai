"""Tests for goat.board: reading an issue as a GoatItem, G.O.A.T. state comment, label changes, next_role."""

import unittest
from datetime import datetime, timedelta, timezone

from goat import board as fb
from goat.tests.fakes import raw_item, state_comment

NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)


def item_from(**kwargs):
    return fb.GoatItem.from_github(raw_item("5", **kwargs))


class FromGitHubTests(unittest.TestCase):
    def test_reads_labels_assignees_and_milestone(self):
        item = item_from(stage="plan", goat_status="plan-review", size="M", priority="next", milestone="v0.1",
                         assignees=("me", "you"))
        self.assertEqual((item.id, item.status, item.stage), ("5", "plan", "plan"))
        self.assertEqual((item.goat_status, item.size, item.priority), ("plan-review", "M", "next"))
        self.assertEqual((item.owner_ids, item.milestone), (["me", "you"], "v0.1"))
        self.assertEqual(item.board_id, "me/app")

    def test_closed_issue_has_status_done_and_no_agent_stage(self):
        item = item_from(stage="review", goat_status="", state="closed")
        self.assertEqual((item.status, item.stage), ("done", None))

    def test_staging_and_discovery_are_not_agent_stages(self):
        self.assertIsNone(item_from(stage="staging", goat_status="").stage)
        self.assertIsNone(item_from(stage="discovery", goat_status="").stage)

    def test_no_goat_label_is_not_a_goat_item(self):
        self.assertFalse(item_from(stage="discovery", goat_status="").is_goat_item)
        self.assertTrue(item_from(goat_status="ready").is_goat_item)

    def test_a_closed_issue_is_not_a_goat_item_even_with_a_gate_label(self):
        # A person closed it at plan review: it is finished, so the coordinator and `list` must ignore it.
        closed = item_from(stage="plan", goat_status="plan-review", state="closed")
        self.assertFalse(closed.is_goat_item)
        self.assertTrue(closed.goat_touched)  # but the retro and the cleanup still handle it

    def test_goat_touched(self):
        self.assertFalse(item_from(stage="discovery", goat_status="").goat_touched)
        self.assertFalse(item_from(stage="", goat_status="").goat_touched)
        self.assertTrue(item_from(stage="staging", goat_status="").goat_touched)
        self.assertTrue(item_from(stage="staging", goat_status="", state="closed").goat_touched)
        self.assertTrue(item_from(stage="intake", goat_status="ready").goat_touched)

    def test_owner_match_ignores_case(self):
        self.assertTrue(item_from(assignees=("Me",)).is_owned_by("me"))
        self.assertFalse(item_from(assignees=("you",)).is_owned_by("me"))

    def test_claim_and_summary_come_from_the_state_comment(self):
        expires = fb.format_date(NOW + timedelta(minutes=5))
        item = item_from(stage="plan", goat_status="working", claim="plan|abc", expires=expires, summary="Short",
                         pr_url="https://github.com/me/app/pull/9")
        self.assertEqual((item.claim_role, item.claim_session), ("plan", "abc"))
        self.assertTrue(item.claim_is_live(NOW))
        self.assertEqual((item.summary, item.pr_url), ("Short", "https://github.com/me/app/pull/9"))
        self.assertTrue(item.state_comment_id)

    def test_expired_or_missing_expiry_is_not_live(self):
        past = fb.format_date(NOW - timedelta(minutes=1))
        self.assertFalse(item_from(goat_status="working", claim="plan|a", expires=past).claim_is_live(NOW))
        self.assertFalse(item_from(goat_status="working", claim="plan|a", expires="").claim_is_live(NOW))

    def test_a_list_read_has_no_comments(self):
        raw = raw_item("5")
        del raw["comments"]
        item = fb.GoatItem.from_github(raw)
        self.assertFalse(item.comments_loaded)
        self.assertEqual(item.claim, "")

    def test_doc_sections_in_order(self):
        comments = [
            {"id": 1, "created_at": "t", "author": "me", "body": fb.render_doc("Intake summary", "A")},
            {"id": 2, "created_at": "t", "author": "me", "body": "just a comment"},
            {"id": 3, "created_at": "t", "author": "me", "body": fb.render_doc("Spec and plan", "B")},
        ]
        item = item_from(comments=comments)
        self.assertEqual([s["title"] for s in item.doc_sections], ["Intake summary", "Spec and plan"])


class StateCommentTests(unittest.TestCase):
    def test_round_trip(self):
        state = {"claim": "plan|s1", "claimExpires": "2026-10-28T12:30:00Z", "summary": "Hello", "prUrl": ""}
        self.assertEqual(fb.parse_state(fb.render_state(state)), state)

    def test_text_that_would_end_the_html_comment_is_safe(self):
        state = {"claim": "", "claimExpires": "", "summary": "--> <script> & <!-- evil", "prUrl": ""}
        body = fb.render_state(state)
        self.assertEqual(fb.parse_state(body), state)
        first_line = body.split("\n", 1)[0]
        self.assertEqual(first_line.count("-->"), 1)
        self.assertTrue(first_line.endswith(" -->"))

    def test_other_comments_are_not_state(self):
        self.assertFalse(fb.is_state_comment("hello"))
        self.assertEqual(fb.parse_state("hello"), {})
        self.assertEqual(fb.parse_state(fb.STATE_MARKER + "not json -->"), {})

    def test_doc_comment_title_round_trip(self):
        body = fb.render_doc("Spec and plan (revision 2)", "## Text\n\nBody")
        self.assertEqual(fb.doc_title(body), "Spec and plan (revision 2)")
        self.assertIn("Body", fb.doc_markdown(body))
        self.assertIsNone(fb.doc_title("plain"))


class LabelChangeTests(unittest.TestCase):
    def test_swaps_the_goat_label_and_keeps_others(self):
        item = item_from(stage="plan", goat_status="ready", size="M")
        add, remove = fb.label_changes(item, goat_status="working")
        self.assertEqual((add, remove), (["goat:working"], ["goat:ready"]))

    def test_changes_stage_and_size_groups(self):
        item = item_from(stage="plan", goat_status="working", size="M")
        add, remove = fb.label_changes(item, goat_status="tier-review", stage="intake", size="L")
        self.assertEqual(sorted(add), ["goat:tier-review", "size:L", "stage:intake"])
        self.assertEqual(sorted(remove), ["goat:working", "size:M", "stage:plan"])

    def test_same_label_is_not_touched(self):
        item = item_from(stage="plan", goat_status="ready")
        self.assertEqual(fb.label_changes(item, goat_status="ready", stage="plan"), ([], []))

    def test_clear_goat_status(self):
        item = item_from(stage="review", goat_status="merge-review")
        add, remove = fb.label_changes(item, stage="staging", clear_goat_status=True)
        self.assertEqual(add, ["stage:staging"])
        self.assertEqual(sorted(remove), ["goat:merge-review", "stage:review"])

    def test_removes_stray_duplicates_in_a_group(self):
        item = item_from(stage="plan", goat_status="ready", labels=["goat:working"])
        add, remove = fb.label_changes(item, goat_status="approved")
        self.assertEqual(sorted(remove), ["goat:ready", "goat:working"])


class NextRoleTests(unittest.TestCase):
    def role(self, **kwargs):
        return fb.next_role(item_from(**kwargs), NOW)

    def test_ready_is_the_stage_agent(self):
        self.assertEqual(self.role(stage="plan", goat_status="ready"), "plan")

    def test_approved_goes_to_the_next_stage(self):
        self.assertEqual(self.role(stage="intake", goat_status="approved"), "plan")
        self.assertEqual(self.role(stage="plan", goat_status="approved"), "implement")
        self.assertIsNone(self.role(stage="review", goat_status="approved"))

    def test_gates_questions_and_chat_have_no_role(self):
        for status in ("tier-review", "plan-review", "merge-review", "answer-question", "chat", "done"):
            self.assertIsNone(self.role(stage="plan", goat_status=status), status)

    def test_stale_claim_can_be_taken_over(self):
        past = fb.format_date(NOW - timedelta(minutes=1))
        future = fb.format_date(NOW + timedelta(minutes=5))
        self.assertEqual(self.role(stage="implement", goat_status="working", claim="implement|x", expires=past), "implement")
        self.assertIsNone(self.role(stage="implement", goat_status="working", claim="implement|x", expires=future))

    def test_no_stage_no_role(self):
        self.assertIsNone(self.role(stage="discovery", goat_status="ready"))
        self.assertIsNone(self.role(stage="", goat_status="ready"))

    def test_priority_rank(self):
        self.assertLess(fb.priority_rank("Blocker"), fb.priority_rank("first"))
        self.assertLess(fb.priority_rank("best-effort"), fb.priority_rank(""))
        self.assertEqual(fb.priority_rank("nonsense"), fb.priority_rank(None))


class LabelCatalogTests(unittest.TestCase):
    def test_every_label_is_unique_and_described(self):
        names = [name for name, _, _ in fb.all_labels()]
        self.assertEqual(len(names), len(set(names)))
        for name, color, description in fb.all_labels():
            self.assertRegex(color, r"^[0-9a-f]{6}$", name)
            self.assertLessEqual(len(description), 100, name)  # GitHub's limit
            self.assertTrue(description, name)

    def test_covers_every_status_stage_size_and_priority(self):
        names = {name for name, _, _ in fb.all_labels()}
        for status in fb.GOAT_STATUSES:
            self.assertIn("goat:" + status, names)
        for stage in fb.STAGE_NAMES:
            self.assertIn("stage:" + stage, names)
        for size in fb.SIZES:
            self.assertIn("size:" + size, names)
        for priority in fb.PRIORITY_ORDER:
            self.assertIn("prio:" + priority, names)


if __name__ == "__main__":
    unittest.main()
