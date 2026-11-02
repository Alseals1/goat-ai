"""Tests for farm/choose.py: which agents the coordinator starts.

Run from the repo root:
  python3 -m unittest discover -s farm/tests -t . -v
"""

import unittest
from datetime import datetime, timezone

from farm.choose import (REPO_SLOTS_FULL, SLOTS_FULL, attempt_key, choose_launches, choose_retros, is_slot_limit,
                         model_for, retro_attempt_key, retro_candidates, role_slots_full)
from farm.board import FarmItem

ME = "me"
NOW = datetime(2026, 10, 27, 15, 0, 0, tzinfo=timezone.utc)
STAGE_STATUS = {"intake": "intake", "plan": "plan", "implement": "implement", "review": "review"}


def item(item_id="1", stage="intake", farm_status="ready", owner=ME, size="", priority="",
         updated="2026-10-27T10:00:00Z", claim="", expires=None):
    return FarmItem(
        id=item_id, board_id="me/app", name="Item " + item_id, url="", updated_at=updated,
        status=STAGE_STATUS.get(stage, stage), farm_status=farm_status, size=size, priority=priority,
        owner_ids=[owner], claim=claim, claim_expires=expires, stage=stage if stage in STAGE_STATUS else None,
        labels=["stage:" + stage] if stage else [],
    )


def choose(items, running=(), attempts=None, slots=3, running_per_repo=None, max_per_repo=None,
           running_per_role=None, max_per_role=None):
    """items: FarmItems, all in /code/project, or (repo path, FarmItem) pairs."""
    candidates = [entry if isinstance(entry, tuple) else ("/code/project", entry) for entry in items]
    return choose_launches(candidates, ME, set(running), attempts or {}, slots, NOW,
                           running_per_repo=running_per_repo, max_per_repo=max_per_repo,
                           running_per_role=running_per_role, max_per_role=max_per_role)


class ModelTests(unittest.TestCase):
    def test_role_models(self):
        self.assertEqual(model_for("intake", ""), "sonnet")
        self.assertEqual(model_for("plan", "S"), "opus")
        self.assertEqual(model_for("review", "S"), "opus")
        self.assertEqual(model_for("retro", "L"), "sonnet")

    def test_implement_model_follows_size(self):
        self.assertEqual(model_for("implement", "S"), "sonnet")
        self.assertEqual(model_for("implement", "M"), "opus")
        self.assertEqual(model_for("implement", "XL"), "opus")
        self.assertEqual(model_for("implement", ""), "opus")


class ChooseTests(unittest.TestCase):
    def test_starts_a_ready_item(self):
        launches, skipped = choose([item()])
        self.assertEqual(len(launches), 1)
        self.assertEqual(launches[0]["role"], "intake")
        self.assertEqual(launches[0]["repoPath"], "/code/project")
        self.assertEqual(skipped, [])

    def test_approved_starts_the_next_stage(self):
        launches, _ = choose([item(stage="plan", farm_status="approved", size="S")])
        self.assertEqual((launches[0]["role"], launches[0]["model"]), ("implement", "sonnet"))

    def test_skips_items_that_wait_for_a_person(self):
        items = [
            item("gate", stage="plan", farm_status="plan-review"),
            item("question", farm_status="answer-question"),
            item("chat", stage="implement", farm_status="chat"),
            item("other", owner="99"),
        ]
        launches, skipped = choose(items)
        self.assertEqual(launches, [])
        # Items of other people are not reported.
        self.assertEqual([entry[0] for entry in skipped], ["#gate Item gate", "#question Item question", "#chat Item chat"])

    def test_live_claims_wait_and_expired_claims_restart(self):
        live = item("live", farm_status="working", claim="intake|x", expires=datetime(2026, 10, 27, 15, 5, tzinfo=timezone.utc))
        stale = item("stale", farm_status="working", claim="intake|x", expires=datetime(2026, 10, 27, 14, 5, tzinfo=timezone.utc))
        launches, _ = choose([live, stale])
        self.assertEqual([launch["item"] for launch in launches], ["stale"])

    def test_skips_an_item_with_a_running_agent(self):
        launches, skipped = choose([item("a")], running=["a"])
        self.assertEqual(launches, [])
        self.assertIn("already running", skipped[0][1])

    def test_stops_retrying_after_max_attempts(self):
        ready = item("a")
        launches, skipped = choose([ready], attempts={attempt_key(ready): 2})
        self.assertEqual(launches, [])
        self.assertIn("changed nothing", skipped[0][1])

    def test_a_changed_item_is_a_new_attempt(self):
        old = item("a", updated="2026-10-27T09:00:00Z")
        new = item("a", updated="2026-10-27T10:00:00Z")
        launches, _ = choose([new], attempts={attempt_key(old): 2})
        self.assertEqual(len(launches), 1)

    def test_stale_takeovers_count_even_when_updated_at_changes(self):
        expires = datetime(2026, 10, 27, 14, 0, tzinfo=timezone.utc)
        first = item("a", farm_status="working", claim="intake|x", expires=expires, updated="2026-10-27T09:00:00Z")
        second = item("a", farm_status="working", claim="intake|y", expires=expires, updated="2026-10-27T10:00:00Z")
        self.assertEqual(attempt_key(first), attempt_key(second))

    def test_priority_then_later_stage_then_oldest(self):
        items = [
            item("review-later", stage="review", priority="Later"),
            item("intake-blocker", stage="intake", priority="Blocker"),
            item("plan-none-new", stage="plan", updated="2026-10-27T12:00:00Z"),
            item("plan-none-old", stage="plan", updated="2026-10-27T08:00:00Z"),
            item("implement-later", stage="implement", priority="Later", size="S"),
        ]
        launches, skipped = choose(items, slots=4)
        self.assertEqual([launch["item"] for launch in launches],
                         ["intake-blocker", "review-later", "implement-later", "plan-none-old"])
        self.assertEqual(skipped, [("#plan-none-new Item plan-none-new", "all agent slots are in use")])


class ReposTests(unittest.TestCase):
    def test_ref_is_the_issue_number(self):
        launches, _ = choose([item("14"), item("2")])
        self.assertEqual([launch["ref"] for launch in launches], ["14", "2"])

    def test_candidates_keep_their_repo(self):
        launches, _ = choose([("/code/a", item("a")), ("/code/b", item("b"))])
        self.assertEqual([(launch["item"], launch["repoPath"]) for launch in launches], [("a", "/code/a"), ("b", "/code/b")])

    def test_max_agents_is_shared_across_repos(self):
        launches, skipped = choose([("/code/a", item("a")), ("/code/b", item("b")), ("/code/c", item("c"))], slots=2)
        self.assertEqual([launch["item"] for launch in launches], ["a", "b"])
        self.assertEqual(skipped, [("#c Item c", "all agent slots are in use")])

    def test_a_full_repo_starts_only_other_repos(self):
        items = [("/code/a", item("a1")), ("/code/b", item("b1"))]
        launches, skipped = choose(items, running_per_repo={"/code/a": 1}, max_per_repo=1)
        self.assertEqual([launch["item"] for launch in launches], ["b1"])
        self.assertEqual(skipped, [("#a1 Item a1", "all agent slots for this repo are in use")])

    def test_launches_this_tick_count_toward_the_repo(self):
        launches, skipped = choose([item("a1"), item("a2")], max_per_repo=1)
        self.assertEqual([launch["item"] for launch in launches], ["a1"])
        self.assertEqual(skipped, [("#a2 Item a2", "all agent slots for this repo are in use")])

    def test_no_per_repo_limit_by_default(self):
        launches, _ = choose([item("a1"), item("a2")], running_per_repo={"/code/project": 5})
        self.assertEqual(len(launches), 2)



class RoleCapTests(unittest.TestCase):
    def test_a_full_role_starts_only_other_roles(self):
        items = [item("impl", stage="implement", size="M"), item("new", stage="intake")]
        launches, skipped = choose(items, slots=5, running_per_role={"implement": 2}, max_per_role={"implement": 2})
        self.assertEqual([launch["item"] for launch in launches], ["new"])
        self.assertEqual(skipped, [("#impl Item impl", "all implement slots are in use")])

    def test_launches_this_tick_count_toward_the_role(self):
        items = [item(str(n), stage="implement", size="M", updated="2026-10-27T0{}:00:00Z".format(n)) for n in range(4)]
        launches, skipped = choose(items, slots=5, max_per_role={"implement": 2})
        self.assertEqual([launch["item"] for launch in launches], ["0", "1"])
        self.assertEqual([reason for _, reason in skipped], [role_slots_full("implement")] * 2)

    def test_a_role_not_listed_has_no_cap(self):
        items = [item(str(n)) for n in range(3)]
        launches, _ = choose(items, slots=5, running_per_role={"intake": 4}, max_per_role={"implement": 1})
        self.assertEqual(len(launches), 3)

    def test_overall_slots_are_checked_first(self):
        items = [item("impl", stage="implement", size="M")]
        _, skipped = choose(items, slots=0, running_per_role={"implement": 2}, max_per_role={"implement": 2})
        self.assertEqual(skipped, [("#impl Item impl", SLOTS_FULL)])

    def test_repo_is_checked_before_role(self):
        items = [item("impl", stage="implement", size="M")]
        _, skipped = choose(items, running_per_repo={"/code/project": 1}, max_per_repo=1,
                            running_per_role={"implement": 2}, max_per_role={"implement": 2})
        self.assertEqual(skipped, [("#impl Item impl", REPO_SLOTS_FULL)])

    def test_is_slot_limit(self):
        for reason in [SLOTS_FULL, REPO_SLOTS_FULL, role_slots_full("intake"), role_slots_full("review")]:
            self.assertTrue(is_slot_limit(reason), reason)
        for reason in ["farm status is plan-review", "an agent is already running", "all bogus slots are in use"]:
            self.assertFalse(is_slot_limit(reason), reason)


def done(item_id="1", farm_status="merge-review", owner=ME, updated="2026-10-27T10:00:00Z", claim="", expires=None):
    # A closed issue: stage done. Its labels still say the stage it was closed in.
    found = item(item_id, stage="staging", farm_status=farm_status, owner=owner, updated=updated, claim=claim,
                 expires=expires)
    found.status = "done"
    return found


def choose_retro(items, has_note=lambda item: False, running=(), attempts=None, slots=3, running_per_repo=None,
                 max_per_repo=None, running_per_role=None, max_per_role=None):
    candidates = [("/code/project", entry) for entry in items]
    return choose_retros(candidates, has_note, set(running), attempts or {}, slots,
                         running_per_repo=running_per_repo, max_per_repo=max_per_repo,
                         running_per_role=running_per_role, max_per_role=max_per_role)


class RetroCandidateTests(unittest.TestCase):
    def test_owned_done_farm_items_with_no_live_claim(self):
        later = datetime(2026, 10, 27, 16, 0, 0, tzinfo=timezone.utc)
        plain = done("plain", farm_status="")
        plain.labels = ["stage:discovery"]  # closed, but the farm never touched it
        items = [
            done("mine"),
            done("backfill", farm_status="done"),
            done("other", owner="99"),
            plain,
            item("open", stage="review"),
            done("claimed", claim="review|s1", expires=later),
            done("stale", claim="review|s1", expires=NOW),
        ]
        found = retro_candidates(items, "done", ME, NOW)
        self.assertEqual([found_item.id for found_item in found], ["mine", "backfill", "stale"])


class ChooseRetrosTests(unittest.TestCase):
    def test_starts_a_retro_with_sonnet(self):
        launches, skipped, waiting = choose_retro([done("1")])
        self.assertEqual(launches, [{
            "item": "1", "ref": "1", "label": "#1 Item 1", "role": "retro", "model": "sonnet",
            "repoPath": "/code/project", "attemptKey": "retro:1"}])
        self.assertEqual((skipped, waiting), ([], 0))

    def test_skips_items_with_a_note_and_used_up_attempts_without_a_line(self):
        result = choose_retro(
            [done("noted"), done("failed")], has_note=lambda found: found.id == "noted",
            attempts={retro_attempt_key(done("failed")): 2})
        self.assertEqual(result, ([], [], 0))

    def test_one_failed_try_is_tried_again(self):
        launches, _, _ = choose_retro([done("1")], attempts={"retro:1": 1})
        self.assertEqual(len(launches), 1)

    def test_skips_a_running_item(self):
        launches, skipped, _ = choose_retro([done("1")], running={"1"})
        self.assertEqual(launches, [])
        self.assertEqual(skipped, [("#1 Item 1", "an agent is already running")])

    def test_default_cap_is_one_and_oldest_goes_first(self):
        launches, skipped, waiting = choose_retro([done("new", updated="2026-10-27T12:00:00Z"),
                                                   done("old", updated="2026-10-27T08:00:00Z")])
        self.assertEqual([launch["item"] for launch in launches], ["old"])
        self.assertEqual((skipped, waiting), ([], 1))

    def test_cap_counts_running_retros_and_can_be_raised(self):
        launches, _, waiting = choose_retro([done("1")], running_per_role={"retro": 1})
        self.assertEqual((launches, waiting), ([], 1))
        launches, _, waiting = choose_retro([done("1"), done("2"), done("3")], max_per_role={"retro": 2})
        self.assertEqual((len(launches), waiting), (2, 1))

    def test_uses_only_the_free_slots(self):
        self.assertEqual(choose_retro([done("1")], slots=0), ([], [], 1))

    def test_repo_limit(self):
        result = choose_retro([done("1")], running_per_repo={"/code/project": 1}, max_per_repo=1)
        self.assertEqual(result, ([], [], 1))


if __name__ == "__main__":
    unittest.main()
