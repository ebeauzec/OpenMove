# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The option "one approval for every volume": the two approvers of the safety checks sign once, and the system signs G2, G3 and G4 of every volume in the
second approver's name. Each signature is still refused where a person's would be (wrong phase, copy not ready), and the history says it was standing."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from openmove import lifecycle as L
from openmove import selection
from openmove import server as S
from openmove.journal import Journal

T0 = datetime(2026, 11, 2, 9, 0, 0, tzinfo=timezone.utc)
GATE = {"status": "approved", "message": "ok", "run_id": "r1", "signoffs": {"operator": {"by": "Olive Operator"}, "reviewer": {"by": "Rex Reviewer"}}}
ON = {"one_approval": True}
V = "vol-1"


def at(m):
    return T0 + timedelta(minutes=m)


class SignerTests(unittest.TestCase):
    def test_it_needs_the_option_an_approved_gate_and_two_different_people(self):
        self.assertEqual(L.standing_signers(ON, GATE), ("Olive Operator", "Rex Reviewer"))
        self.assertIsNone(L.standing_signers({"one_approval": False}, GATE))
        self.assertIsNone(L.standing_signers({}, GATE))
        self.assertIsNone(L.standing_signers(ON, None))
        self.assertIsNone(L.standing_signers(ON, dict(GATE, status="stale")))
        same = dict(GATE, signoffs={"operator": {"by": "Olive Operator"}, "reviewer": {"by": "olive operator"}})
        self.assertIsNone(L.standing_signers(ON, same))
        self.assertIsNone(L.standing_signers(ON, dict(GATE, signoffs={"operator": {"by": "Olive Operator"}})))

    def test_the_option_is_off_by_default_and_is_a_yes_or_no(self):
        self.assertFalse(selection.DEFAULTS["one_approval"])
        self.assertIn("one_approval", selection.BOOLEANS)
        self.assertTrue(selection.check_options({"one_approval": True}, False)["one_approval"])


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.j = Journal(Path(self.dir.name) / "j.db")

    def tearDown(self):
        self.j.close()
        self.dir.cleanup()

    def to_recheck(self):
        L.enter(self.j, V, "precheck", {"class": "A"}, gate=GATE, now=at(0))
        L.enter(self.j, V, "validate", {"snapshots_now": 0}, now=at(1))
        L.enter(self.j, V, "copy", {"validated": True}, now=at(2))
        L.enter(self.j, V, "sync", {"size_equal": True, "hash_match": True}, now=at(60))
        for m in (70, 80):
            L.enter(self.j, V, "recheck", {"ok": True, "lag_s": 0, "pending_blocks": 0, "source_fingerprint": "fp"}, now=at(m))

    def test_nothing_is_signed_while_the_option_is_off(self):
        self.to_recheck()
        self.assertEqual(L.apply_standing(self.j, V, GATE, {}, now=at(81)), [])
        self.assertIsNone(L.gate_signed(self.j, V, "G2", GATE))

    def test_g2_and_g3_are_signed_for_the_volume_in_the_second_approvers_name(self):
        self.to_recheck()
        self.assertEqual(L.apply_standing(self.j, V, GATE, ON, now=at(81)), ["G2", "G3"])
        for g in ("G2", "G3"):
            e = L.gate_signed(self.j, V, g, GATE)
            self.assertEqual(e["by"], "Rex Reviewer")
            self.assertTrue(e["standing"])
            self.assertIn("not looked at on its own", e["evidence"])
        self.assertIsNone(L.gate_signed(self.j, V, "G4", GATE))                      # the result is not there yet
        L.enter(self.j, V, "cutover", gate=GATE, now=at(82))                         # the volume may now be switched
        L.enter(self.j, V, "postcheck", now=at(85))
        self.assertEqual(L.apply_standing(self.j, V, GATE, ON, now=at(86)), ["G4"])
        L.enter(self.j, V, "soak", gate=GATE, now=at(90))

    def test_the_conditions_of_a_gate_still_hold(self):
        L.enter(self.j, V, "precheck", {"class": "A"}, gate=GATE, now=at(0))
        L.enter(self.j, V, "validate", {"snapshots_now": 0}, now=at(1))
        L.enter(self.j, V, "copy", {"validated": True}, now=at(2))
        L.enter(self.j, V, "sync", {"size_equal": True, "hash_match": True}, now=at(60))
        self.assertEqual(L.apply_standing(self.j, V, GATE, ON, now=at(61)), ["G2"])          # the copy is verified, so G2 is signed; no rechecks yet, so G3 is refused
        self.assertIsNone(L.gate_signed(self.j, V, "G3", GATE))

    def test_a_gate_that_is_not_approved_signs_nothing(self):
        self.to_recheck()
        self.assertEqual(L.apply_standing(self.j, V, dict(GATE, status="stale"), ON, now=at(81)), [])


class ServerTests(unittest.TestCase):
    def state(self, on=True, gate=True):
        return SimpleNamespace(options={"one_approval": on} if on else None, checks={"x": 1} if gate else None, demo=True, released=set())

    def test_the_progress_page_does_not_ask_a_person_while_the_standing_approval_is_in_force(self):
        r = {"id": "v1", "phase": "recheck", "gates": {"G2": None, "G3": None, "G4": None}, "note": "", "failed": False}
        real = S._gate
        try:
            S._gate = lambda state: GATE
            self.assertEqual(S._wait_kind(self.state(False), r, True), "g23")
            self.assertEqual(S._wait_kind(self.state(True), r, True), "release")      # only the release by the operator remains
            self.assertEqual(S._standing(self.state(True)), ("Olive Operator", "Rex Reviewer"))
            self.assertIsNone(S._standing(self.state(False)))
            self.assertIsNone(S._standing(self.state(True, gate=False)))
        finally:
            S._gate = real


if __name__ == "__main__":
    unittest.main()
