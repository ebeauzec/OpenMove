# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The Progress page's answer for every volume: exactly ONE place (needs you, not moved, being moved, waiting to start, moved), one sentence on what happens
next, and, when a person has to act, the KIND of action (approval by a second person, release by you, decision by you)."""
import unittest
from types import SimpleNamespace

from openmove import server as S

SIGNED = {"G2": "A", "G3": "A", "G4": None}
ALL = {"G2": "A", "G3": "A", "G4": "A"}
NONE = {"G2": None, "G3": None, "G4": None}


def vol(phase, gates=None, vid="v1", **kw):
    return dict({"id": vid, "phase": phase, "gates": gates or NONE, "note": "", "failed": False}, **kw)


def st(released=()):
    return SimpleNamespace(released=set(released))


class PlaceTests(unittest.TestCase):
    def one(self, r, mine=True, released=(), who="Olive"):
        return S._volume_state(st(released), r, mine, who)

    def test_a_volume_that_waits_for_the_first_approvals_needs_a_second_person(self):
        for ph in ("validate", "recheck"):
            s = self.one(vol(ph))
            self.assertEqual(s["group"], "you")
            self.assertEqual((s["action"]["type"], s["action"]["do"], s["action"]["who"]), ("approval", "g23", "a second person"))
            self.assertIn("Olive started the move and cannot approve", s["next"])

    def test_an_approved_volume_waits_for_the_release_until_it_is_released(self):
        s = self.one(vol("recheck", SIGNED))
        self.assertEqual((s["group"], s["action"]["type"], s["action"]["do"], s["action"]["who"]), ("you", "release", "release", "you"))
        s = self.one(vol("recheck", SIGNED), released=["v1"])
        self.assertEqual((s["group"], s["action"]), ("moving", None))

    def test_a_switched_volume_needs_the_result_approved_by_a_second_person(self):
        s = self.one(vol("postcheck", SIGNED))
        self.assertEqual((s["group"], s["action"]["do"], s["action"]["type"]), ("you", "g4", "approval"))
        self.assertEqual(self.one(vol("postcheck", ALL))["group"], "moving")

    def test_moved_volumes_need_nothing(self):
        for ph in ("soak", "done"):
            s = self.one(vol(ph, ALL))
            self.assertEqual((s["group"], s["label"], s["action"]), ("moved", "Moved", None))
            self.assertIn("Nothing to do", s["next"])

    def test_a_volume_that_was_not_moved_is_a_decision_with_the_reason_and_its_state(self):
        s = self.one(vol("aborted", note="the target pool has no room"))
        self.assertEqual((s["group"], s["label"], s["action"]["type"]), ("stopped", "Not moved", "decision"))
        self.assertIn("the target pool has no room.", s["next"])
        self.assertIn("data intact", s["next"])
        s = self.one(vol("rollback"))
        self.assertEqual((s["group"], s["label"]), ("stopped", "Put back"))
        self.assertIn("back on the old storage", s["next"])
        self.assertEqual(self.one(vol("sync", failed=True))["group"], "stopped")

    def test_queued_and_working_volumes_need_nothing(self):
        s = self.one(vol("queued"))
        self.assertEqual((s["group"], s["action"]), ("queued", None))
        for ph in ("precheck", "sync", "cutover"):
            s = self.one(vol(ph))
            self.assertEqual((s["group"], s["action"]), ("moving", None))
            self.assertIn("Nothing to do", s["next"])

    def test_the_demonstration_marks_what_waits_in_its_own_way(self):
        self.assertEqual(self.one(dict(vol("recheck", ready=True), gates=None), mine=False)["action"]["do"], "release")
        self.assertEqual(self.one(vol("recheck", NONE, ready=True), mine=False)["action"]["do"], "g23")
        self.assertEqual(self.one(dict(vol("postcheck", g4wait=True), gates=None), mine=False)["action"]["do"], "g4")
        self.assertEqual(self.one(vol("sync"), mine=False)["group"], "moving")

    def test_every_volume_is_in_exactly_one_place_and_the_places_add_up(self):
        phases = ["queued", "precheck", "validate", "sync", "recheck", "cutover", "postcheck", "soak", "done", "aborted", "rollback"]
        states = [S._volume_state(st(), vol(p, vid=p), True, "") for p in phases]
        counts = S._group_counts(states)
        self.assertEqual(sum(counts.values()), len(phases))
        self.assertEqual(set(counts), {k for k, _ in S.GROUPS})
        self.assertTrue(all(s["group"] in counts and s["label"] and s["next"] for s in states))

    def test_a_reason_always_ends_with_a_full_stop(self):
        self.assertEqual(S.plain_note({"note": "no room"}), "no room.")
        self.assertEqual(S.plain_note({"note": "no room."}), "no room.")
        self.assertEqual(S.plain_note({"error": "broke", "note": "x"}), "broke.")
        self.assertEqual(S.plain_note({}), "")


if __name__ == "__main__":
    unittest.main()
