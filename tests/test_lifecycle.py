# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The order of a migration (requirement R14): nothing is skipped, nothing moves forward on a failed check."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openmove import lifecycle as L
from openmove import safety
from openmove.journal import Journal

T0 = datetime(2026, 11, 2, 9, 0, 0, tzinfo=timezone.utc)
GATE = {"status": "approved", "message": "ok", "run_id": "r1"}
V = "vol-1"


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def good_recheck(fp="fp-1"):
    return {"ok": True, "lag_s": 0, "pending_blocks": 0, "source_fingerprint": fp}


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.j = Journal(Path(self.dir.name) / "j.db")

    def tearDown(self):
        self.j.close()
        self.dir.cleanup()

    def to_sync(self):
        L.enter(self.j, V, "precheck", {"class": "A"}, gate=GATE, now=at(0))
        L.enter(self.j, V, "validate", {"snapshots_now": 0}, now=at(1))
        L.enter(self.j, V, "copy", {"validated": True}, now=at(2))
        L.enter(self.j, V, "sync", {"size_equal": True, "hash_match": True}, now=at(60))

    def two_rechecks(self, fp="fp-1"):
        L.enter(self.j, V, "recheck", good_recheck(fp), now=at(70))
        L.enter(self.j, V, "recheck", good_recheck(fp), now=at(80))

    def test_the_full_order_works(self):
        self.to_sync()
        self.two_rechecks()
        L.enter(self.j, V, "cutover", gate=GATE, now=at(82))
        L.enter(self.j, V, "postcheck", now=at(85))
        L.enter(self.j, V, "soak", now=at(90))
        L.enter(self.j, V, "done", now=at(90 + 60 * 24 * 7))
        self.assertEqual(L.phase_of(self.j, V), "done")

    def test_no_phase_can_be_skipped(self):
        for target in ("copy", "sync", "recheck", "cutover", "soak", "done"):
            with self.assertRaises(L.LifecycleError, msg=target):
                L.enter(self.j, V, target, {"validated": True, "size_equal": True, "hash_match": True}, gate=GATE, now=at(0))
        L.enter(self.j, V, "precheck", {"class": "A"}, gate=GATE, now=at(0))
        with self.assertRaises(L.LifecycleError):
            L.enter(self.j, V, "copy", {"validated": True}, now=at(1))   # validate was skipped

    def test_precheck_needs_an_approved_gate(self):
        for g in (None, {}, {"status": "open"}, {"status": "stale"}, {"status": "blocked"}):
            with self.assertRaises(L.LifecycleError):
                L.enter(self.j, V, "precheck", {"class": "A"}, gate=g, now=at(0))

    def test_copy_needs_validation_and_sync_needs_a_verified_copy(self):
        L.enter(self.j, V, "precheck", {"class": "A"}, gate=GATE, now=at(0))
        L.enter(self.j, V, "validate", {"snapshots_now": 0}, now=at(1))
        with self.assertRaises(L.LifecycleError):
            L.enter(self.j, V, "copy", {}, now=at(2))
        L.enter(self.j, V, "copy", {"validated": True}, now=at(2))
        for ev in ({}, {"size_equal": True}, {"hash_match": True}, {"size_equal": True, "hash_match": False}):
            with self.assertRaises(L.LifecycleError):
                L.enter(self.j, V, "sync", ev, now=at(30))

    def test_cutover_needs_two_rechecks(self):
        self.to_sync()
        with self.assertRaises(L.LifecycleError):
            L.enter(self.j, V, "cutover", gate=GATE, now=at(65))
        L.enter(self.j, V, "recheck", good_recheck(), now=at(70))
        with self.assertRaises(L.LifecycleError) as cm:
            L.enter(self.j, V, "cutover", gate=GATE, now=at(71))
        self.assertIn("1 passed recheck", str(cm.exception))

    def test_rechecks_must_be_apart_and_recent(self):
        self.to_sync()
        L.enter(self.j, V, "recheck", good_recheck(), now=at(70))
        L.enter(self.j, V, "recheck", good_recheck(), now=at(71))                  # one minute apart: too close
        with self.assertRaises(L.LifecycleError) as cm:
            L.enter(self.j, V, "cutover", gate=GATE, now=at(72))
        self.assertIn("apart", str(cm.exception))
        L.enter(self.j, V, "recheck", good_recheck(), now=at(80))                  # now the last two are apart
        L.enter(self.j, V, "cutover", gate=GATE, now=at(81))
        # and a stale one is refused
        self.setUp_new()
        self.to_sync()
        self.two_rechecks()
        with self.assertRaises(L.LifecycleError) as cm:
            L.enter(self.j, V, "cutover", gate=GATE, now=at(80 + 16))              # newest recheck is 16 minutes old
        self.assertIn("Check again", str(cm.exception))

    def setUp_new(self):
        self.j.close()
        self.dir.cleanup()
        self.setUp()

    def test_a_failed_recheck_drops_back_and_resets_the_count(self):
        self.to_sync()
        self.two_rechecks()
        bad = dict(good_recheck(), pending_blocks=12)                              # the source changed: data still to copy
        L.enter(self.j, V, "recheck", bad, now=at(90))
        self.assertEqual(L.phase_of(self.j, V), "sync")
        with self.assertRaises(L.LifecycleError):
            L.enter(self.j, V, "cutover", gate=GATE, now=at(91))
        L.enter(self.j, V, "recheck", good_recheck(), now=at(100))
        with self.assertRaises(L.LifecycleError):
            L.enter(self.j, V, "cutover", gate=GATE, now=at(101))                  # only one pass since the failure
        L.enter(self.j, V, "recheck", good_recheck(), now=at(110))
        L.enter(self.j, V, "cutover", gate=GATE, now=at(111))

    def test_a_recheck_without_a_fingerprint_or_with_lag_never_passes(self):
        self.to_sync()
        for ev in ({"ok": True, "lag_s": 0, "pending_blocks": 0}, dict(good_recheck(), lag_s=5), dict(good_recheck(), ok=False), {}):
            L.enter(self.j, V, "recheck", ev, now=at(70))
            self.assertEqual(L.phase_of(self.j, V), "sync")

    def test_source_change_between_rechecks_blocks_the_cutover(self):
        self.to_sync()
        L.enter(self.j, V, "recheck", good_recheck("fp-1"), now=at(70))
        L.enter(self.j, V, "recheck", good_recheck("fp-2"), now=at(80))
        with self.assertRaises(L.LifecycleError) as cm:
            L.enter(self.j, V, "cutover", gate=GATE, now=at(81))
        self.assertIn("source changed", str(cm.exception))

    def test_gate_and_halt_are_checked_at_the_moment_of_cutover(self):
        self.to_sync()
        self.two_rechecks()
        for g in (None, {"status": "stale", "message": "old"}, {"status": "awaiting_signoff", "message": "x"}):
            with self.assertRaises(L.LifecycleError):
                L.enter(self.j, V, "cutover", gate=g, now=at(81))
        safety.halt(self.j, "Pat Example", "the array is misbehaving")
        with self.assertRaises(L.LifecycleError) as cm:
            L.enter(self.j, V, "cutover", gate=GATE, now=at(81))
        self.assertIn("halted", str(cm.exception))
        safety.resume(self.j, "Sam Sample", "array is fine again")
        L.enter(self.j, V, "cutover", gate=GATE, now=at(82))

    def test_abort_before_cutover_and_rollback_after(self):
        self.to_sync()
        L.enter(self.j, V, "aborted", {"reason": "customer postponed the window"}, now=at(65))
        with self.assertRaises(L.LifecycleError):
            L.enter(self.j, V, "sync", now=at(66))                                  # nothing after an abort
        self.setUp_new()
        self.to_sync()
        self.two_rechecks()
        L.enter(self.j, V, "cutover", gate=GATE, now=at(82))
        with self.assertRaises(L.LifecycleError):
            L.enter(self.j, V, "aborted", {"reason": "want to stop now"}, now=at(83))  # after cutover only rollback
        with self.assertRaises(L.LifecycleError):
            L.enter(self.j, V, "rollback", {"reason": "no"}, now=at(83))                # a reason is required
        L.enter(self.j, V, "rollback", {"reason": "application errors after the switch"}, now=at(83))
        L.enter(self.j, V, "done", now=at(84))

    def test_abort_needs_a_reason(self):
        self.to_sync()
        with self.assertRaises(L.LifecycleError):
            L.enter(self.j, V, "aborted", {"reason": "x"}, now=at(65))

    def test_state_survives_a_restart(self):
        self.to_sync()
        self.two_rechecks()
        path = Path(self.dir.name) / "j.db"
        self.j.close()
        self.j = Journal(path)                                                      # a new process reads the same journal
        self.assertEqual(L.phase_of(self.j, V), "recheck")
        L.enter(self.j, V, "cutover", gate=GATE, now=at(82))

    def test_volumes_are_independent(self):
        self.to_sync()
        L.enter(self.j, "vol-2", "precheck", {"class": "A"}, gate=GATE, now=at(0))
        with self.assertRaises(L.LifecycleError):
            L.enter(self.j, "vol-2", "recheck", good_recheck(), now=at(70))
        self.assertEqual(L.summary(self.j, [V, "vol-2", "vol-3"]), {"sync": 1, "precheck": 1, "not started": 1})


if __name__ == "__main__":
    unittest.main()
