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

    def test_moved_volumes_need_nothing_and_say_whether_they_are_ready_and_completed(self):
        s = self.one(vol("soak", ALL))
        self.assertEqual((s["group"], s["label"], s["outcome"], s["ready"], s["completed"], s["action"]), ("moved", "Moved and ready", "moved_ready", True, False, None))
        self.assertIn("ready to use", s["next"])
        self.assertIn("old copy is kept", s["next"])
        s = self.one(vol("done", ALL))
        self.assertEqual((s["group"], s["label"], s["outcome"], s["ready"], s["completed"], s["action"]), ("moved", "Completed", "completed", True, True, None))
        self.assertIn("old copy is released", s["next"])
        for ph in ("soak", "done"):
            self.assertIn("Nothing to do", self.one(vol(ph, ALL))["next"])

    def test_a_volume_that_was_not_moved_is_a_decision_with_the_reason_and_its_state(self):
        s = self.one(vol("aborted", note="the target pool has no room"))
        self.assertEqual((s["group"], s["label"], s["action"]["type"]), ("stopped", "Not moved", "decision"))
        self.assertEqual(s["failure"]["title"], "Not enough room on the target")
        self.assertEqual(s["failure"]["detail"], "the target pool has no room")
        self.assertIn("Not enough room on the target", s["next"])
        self.assertIn("To fix:", s["next"])
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


class FailureTests(unittest.TestCase):
    """A volume that was not moved says WHY in plain words, what state it is in, and what to do first."""

    def one(self, r):
        return S._volume_state(st(), r, True, "Olive")

    def test_the_reasons_the_engines_give_have_a_plain_cause(self):
        from openmove import handling as H
        cases = {"several volume types belong to the target back-end (a, b); choose one for type x": "target_type_unclear",
                 "no volume type is bound to the target back-end pure": "target_type_unclear",
                 "the volume is on sf-sim, not on pure": "not_on_source",
                 "2 snapshot(s) remain; Cinder moves a volume with snapshots only by copying": "snapshot_appeared",
                 "the snapshots did not rotate away within 72 hours": "rotation_timeout",
                 "the volume is encrypted": "encrypted", "the volume is multi-attach": "multiattach", "the volume belongs to a group": "grouped",
                 "the scheduler reports no pool for the target back-end pure": "no_room",
                 "the image of snapshot abc on the target is not identical to the one read (S3)": "copy_not_verified",
                 "a step on the migration host failed 3 times in a row: boom": "host_step_failed",
                 "something nobody foresaw": "aborted_other", "": "aborted_other"}
        for text, code in cases.items():
            self.assertEqual(H.classify_reason(text), code, text)
            d = H.describe_cause(code)
            self.assertTrue(d["title"] and d["why"] and d["steps"] and d["who"])

    def test_the_screenshot_case_names_the_fix(self):
        s = self.one(vol("aborted", failed=True, error="given up: several volume types belong to the target back-end (sf-gold, sf-sim); choose one for type pure-sim"))
        self.assertEqual(s["group"], "stopped")
        self.assertEqual(s["failure"]["title"], "OpenMove could not tell which volume type to move to")
        self.assertIn("Move", s["failure"]["steps"][0])
        self.assertIn("several volume types belong", s["failure"]["detail"])
        self.assertNotIn("given up:", s["failure"]["detail"])
        self.assertIn("data intact", s["next"])

    def test_an_unknown_reason_is_still_shown(self):
        s = self.one(vol("aborted", note="given up: the moon is in the wrong phase"))
        self.assertEqual(s["failure"]["title"], "Stopped for another reason")
        self.assertEqual(s["failure"]["detail"], "the moon is in the wrong phase")

    def test_a_put_back_volume_says_it_is_on_the_old_storage(self):
        s = self.one(vol("rollback"))
        self.assertEqual((s["label"], s["outcome"]), ("Put back", "not_moved"))
        self.assertIn("back on the old storage", s["next"])


class SummaryTests(unittest.TestCase):
    def test_the_summary_answers_what_has_moved_what_is_ready_and_what_is_not(self):
        phases = ["queued", "sync", "validate", "soak", "soak", "done", "aborted"]
        states = [S._volume_state(st(), vol(p, ALL if p in ("soak", "done") else None, vid=str(i)), True, "") for i, p in enumerate(phases)]
        sm = S._summary(states)
        self.assertEqual(sm, {"moved": 3, "ready": 3, "completed": 1, "in_safety_period": 2, "not_moved": 1, "needs_you": 1, "being_moved": 1, "waiting": 1, "total": 7})


class TargetTypeTests(unittest.TestCase):
    """The Move step shows the volume types bound to the target; it must never claim to know what it has not read."""

    class Session:
        def __init__(self, types=None, boom=False):
            self.types, self.boom = types, boom

        def get(self, service, path):
            if self.boom:
                raise RuntimeError("no answer")
            return {"volume_types": self.types}

    def state(self, session, demo=False, target="pure-prod"):
        return SimpleNamespace(checks={"target_backend": target} if target else {}, demo=demo, session=session)

    def types(self, *pairs):
        return [{"name": n, "extra_specs": {"volume_backend_name": b}} for n, b in pairs]

    def test_exactly_one_bound_type_is_chosen_by_itself(self):
        out = S._target_types(self.state(self.Session(self.types(("fa", "pure-prod"), ("sf", "sf-prod")))))
        self.assertEqual((out["backend"], out["types"], out["auto"]), ("pure-prod", ["fa"], "fa"))

    def test_several_bound_types_are_listed_and_none_is_guessed(self):
        out = S._target_types(self.state(self.Session(self.types(("fa-a", "pure-prod"), ("fa-b", "pure-prod")))))
        self.assertEqual((out["types"], out["auto"]), (["fa-a", "fa-b"], None))

    def test_no_bound_type_is_reported_as_empty_not_as_unknown(self):
        out = S._target_types(self.state(self.Session(self.types(("sf", "sf-prod")))))
        self.assertEqual((out["backend"], out["types"], out["auto"]), ("pure-prod", [], None))

    def test_unread_types_leave_the_backend_empty_so_the_page_does_not_block(self):
        for s in (self.state(self.Session(boom=True)), self.state(None), self.state(self.Session(self.types()), demo=True), self.state(self.Session(self.types()), target=None)):
            out = S._target_types(s)
            self.assertEqual((out["backend"], out["types"], out["auto"]), (None, [], None))


if __name__ == "__main__":
    unittest.main()
