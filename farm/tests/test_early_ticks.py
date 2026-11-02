"""Tests for farm/early_ticks.py: when the coordinator runs an early full turn."""

import unittest

from farm.early_ticks import EARLY_TICK_WAIT_SECONDS, EarlyTicks, TickResult

GOOD = TickResult(slot_skipped=False, github_failed=False)
SLOT_SKIPPED = TickResult(slot_skipped=True, github_failed=False)
GITHUB_FAILED = TickResult(slot_skipped=False, github_failed=True)


def waiting():
    """EarlyTicks after a scheduled tick that skipped an item for a slot."""
    early = EarlyTicks()
    early.after_tick(SLOT_SKIPPED)
    return early


class EarlyTicksTests(unittest.TestCase):
    def test_not_due_before_a_run_ends(self):
        self.assertFalse(waiting().is_due(1000))

    def test_not_due_when_no_item_waits(self):
        early = EarlyTicks()
        early.after_tick(GOOD)
        early.run_ended()
        self.assertFalse(early.is_due(1000))

    def test_due_at_once_after_a_scheduled_tick(self):
        early = waiting()
        early.run_ended()
        self.assertTrue(early.is_due(1000))

    def test_follow_up_skip_sets_the_flag(self):
        early = EarlyTicks()
        early.follow_up_skipped_for_slot()
        self.assertTrue(early.waiting_for_slot)
        early.run_ended()
        self.assertTrue(early.is_due(1000))

    def test_throttle_after_an_early_tick(self):
        early = waiting()
        early.run_ended()
        self.assertIsNone(early.after_early_tick(SLOT_SKIPPED, 1000))
        early.run_ended()
        self.assertFalse(early.is_due(1000 + EARLY_TICK_WAIT_SECONDS - 1))
        self.assertTrue(early.is_due(1000 + EARLY_TICK_WAIT_SECONDS))

    def test_good_early_tick_with_no_skip_clears_the_flag(self):
        early = waiting()
        early.run_ended()
        early.after_early_tick(GOOD, 1000)
        self.assertFalse(early.waiting_for_slot)
        self.assertFalse(early.slot_freed)

    def test_back_off_then_pause(self):
        early = waiting()
        early.run_ended()
        now = 1000
        for retry, wait in [(1, 120), (2, 240), (3, 480)]:
            line = early.after_early_tick(GITHUB_FAILED, now)
            self.assertEqual(line, "Early turn had a GitHub error; next try in {} seconds (retry {} of 3).".format(
                wait, retry))
            self.assertFalse(early.is_due(now + wait - 1))
            now += wait
            self.assertTrue(early.is_due(now))
        line = early.after_early_tick(GITHUB_FAILED, now)
        self.assertEqual(line, "Early turns paused until the next turn: 3 retries failed.")
        self.assertFalse(early.is_due(now + 10 ** 6))

    def test_good_early_tick_resets_failures(self):
        early = waiting()
        early.run_ended()
        early.after_early_tick(GITHUB_FAILED, 1000)
        early.after_early_tick(SLOT_SKIPPED, 1120)
        self.assertEqual(early.failures, 0)
        early.run_ended()
        # The next error starts the back-off again at 120 seconds.
        line = early.after_early_tick(GITHUB_FAILED, 1180)
        self.assertIn("next try in 120 seconds (retry 1 of 3)", line)

    def test_scheduled_tick_ends_a_pause(self):
        early = waiting()
        early.run_ended()
        for now in [1000, 1120, 1360, 1840]:
            early.after_early_tick(GITHUB_FAILED, now)
        early.after_tick(SLOT_SKIPPED)
        self.assertEqual(early.failures, 0)
        early.run_ended()
        # The scheduled tick does not start the throttle; the last back-off wait (to 2320) has passed.
        self.assertTrue(early.is_due(2400))


if __name__ == "__main__":
    unittest.main()
