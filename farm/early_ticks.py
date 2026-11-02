"""When the coordinator runs an early full turn between scheduled turns. Pure logic: no GitHub reads, no clock.

When a turn skips items because a slot limit is full (overall, repo or role), a run that ends frees a slot.
The coordinator then runs a full turn at once instead of a single-item follow-up, so the normal order
(priority, then later stage, then oldest) picks what fills the slot.

Early turns read every repo's board, so they are limited: at most one per EARLY_TICK_WAIT_SECONDS. After a
GitHub read error, the next try waits longer each time (120, 240, 480 seconds). After MAX_EARLY_TICK_RETRIES
failed retries, early turns stop until the next scheduled or manual turn.

The state is in memory only. It matters only while the coordinator runs.
"""

from collections import namedtuple

EARLY_TICK_WAIT_SECONDS = 60
MAX_EARLY_TICK_RETRIES = 3

# What a turn tells the early-turn logic: did it skip an item for a slot limit, and did a GitHub read fail.
TickResult = namedtuple("TickResult", ["slot_skipped", "github_failed"])


class EarlyTicks:
    """Remembers whether items wait for a slot, and says when an early full turn may run.

    `now` arguments are time.monotonic() values, so tests can use plain numbers.
    """

    def __init__(self):
        # The last full turn (or a follow-up) skipped an item for a slot limit.
        self.waiting_for_slot = False
        # A run ended since the last full turn, so a slot may be free.
        self.slot_freed = False
        # Early turns in a row that had a GitHub read error.
        self.failures = 0
        # No early turn before this time.
        self.not_before = 0.0

    def after_tick(self, result):
        """After a scheduled or manual turn. It resets the back-off, but does not start the throttle."""
        self.waiting_for_slot = result.slot_skipped
        self.slot_freed = False
        self.failures = 0

    def follow_up_skipped_for_slot(self):
        self.waiting_for_slot = True

    def run_ended(self):
        self.slot_freed = True

    def is_due(self, now):
        if not (self.waiting_for_slot and self.slot_freed):
            return False
        if self.failures > MAX_EARLY_TICK_RETRIES:
            return False
        return now >= self.not_before

    def after_early_tick(self, result, now):
        """Records an early turn's result. Returns a line to log, or None."""
        if not result.github_failed:
            self.failures = 0
            self.not_before = now + EARLY_TICK_WAIT_SECONDS
            self.slot_freed = False
            self.waiting_for_slot = result.slot_skipped
            return None
        # Keep slot_freed, so the early turn runs again after the wait.
        self.failures += 1
        if self.failures > MAX_EARLY_TICK_RETRIES:
            return "Early turns paused until the next turn: {} retries failed.".format(MAX_EARLY_TICK_RETRIES)
        wait = EARLY_TICK_WAIT_SECONDS * 2 ** self.failures
        self.not_before = now + wait
        return "Early turn had a GitHub error; next try in {} seconds (retry {} of {}).".format(
            wait, self.failures, MAX_EARLY_TICK_RETRIES)
