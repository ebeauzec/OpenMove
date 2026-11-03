# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Phase 0 as guided procedures: results are recorded per release, and gate G0 can be recorded from them only when every required test passed."""
import tempfile
import unittest
from pathlib import Path

from openmove import demo, phase0, profiles
from openmove import server as S
from openmove.journal import Journal

EVIDENCE = "Ran the procedure on the test instance of the lab and read the volume afterwards; it matched."


class PhaseZeroTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.j = Journal(Path(self.dir.name) / "j.db")

    def tearDown(self):
        self.j.close()
        self.dir.cleanup()

    def required(self):
        return [t["id"] for t in phase0.tests() if t["scope"] == "retype"]

    def test_the_catalogue_has_the_playbook_tests_and_ours(self):
        ids = [t["id"] for t in phase0.tests()]
        self.assertEqual(ids[:22], ["T%d" % i for i in range(1, 23)])
        self.assertEqual(ids[22:], ["M1", "M2", "M3", "M4"])
        for t in phase0.tests():
            self.assertTrue(t["scope"] in ("retype", "staged") and t["title"] and t["proves"] and t["pass"] and t["how"], t["id"])
        self.assertIn("T22", self.required())                          # the lock test of playbook v1.3 is needed for the retype path

    def test_nothing_is_ready_until_every_required_test_has_passed(self):
        st = phase0.status(self.j, "2025.1")
        self.assertFalse(st["summary"]["ready"])
        with self.assertRaises(ValueError):
            phase0.evidence_text(self.j, "2025.1")
        for tid in self.required()[:-1]:
            phase0.record(self.j, "2025.1", tid, "pass", "Pat Tester", EVIDENCE)
        self.assertFalse(phase0.status(self.j, "2025.1")["summary"]["ready"])
        phase0.record(self.j, "2025.1", self.required()[-1], "pass", "Pat Tester", EVIDENCE)
        self.assertTrue(phase0.status(self.j, "2025.1")["summary"]["ready"])
        self.assertIn("Phase 0 tests passed for 2025.1", phase0.evidence_text(self.j, "2025.1"))

    def test_the_newest_result_counts_and_a_failure_blocks(self):
        for tid in self.required():
            phase0.record(self.j, "r", tid, "pass", "Pat Tester", EVIDENCE)
        phase0.record(self.j, "r", "T1", "fail", "Pat Tester", EVIDENCE)
        st = phase0.status(self.j, "r")
        self.assertEqual((st["summary"]["failed"], st["summary"]["ready"]), (1, False))
        phase0.record(self.j, "r", "T1", "pass", "Pat Tester", EVIDENCE + " Second attempt.")
        self.assertTrue(phase0.status(self.j, "r")["summary"]["ready"])

    def test_results_belong_to_a_release(self):
        for tid in self.required():
            phase0.record(self.j, "queens", tid, "pass", "Pat Tester", EVIDENCE)
        self.assertTrue(phase0.status(self.j, "queens")["summary"]["ready"])
        self.assertFalse(phase0.status(self.j, "train")["summary"]["ready"])

    def test_bad_input_is_refused(self):
        for args in (("T99", "pass", "Pat Tester", EVIDENCE), ("T1", "maybe", "Pat Tester", EVIDENCE), ("T1", "pass", "x", EVIDENCE), ("T1", "pass", "Pat Tester", "short")):
            with self.assertRaises(ValueError):
                phase0.record(self.j, "r", *args)


class G0FromTestsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.server, self.state = S.create_server("127.0.0.1", 0, Path(self.dir.name))
        demo.start(self.state, scenario="sf-to-pure")
        S.api_discover(self.state, {}, {})

    def tearDown(self):
        self.server.server_close()
        self.state.disconnect()
        demo.stop(self.state)
        self.state.journal.close()
        self.dir.cleanup()

    def test_g0_from_the_tests_is_refused_until_they_pass_and_then_lifts_the_read_only_state(self):
        before = S.api_phase0_get(self.state, {}, {})
        self.assertFalse(before["summary"]["ready"])
        with self.assertRaises(ValueError):
            S.api_validation(self.state, {"by": "Pat Tester", "second": "Sam Second", "from_tests": True}, {})
        for t in before["tests"]:
            if t["required"]:
                S.api_phase0_record(self.state, {"test": t["id"], "result": "pass", "by": "Pat Tester", "evidence": EVIDENCE}, {})
        with self.assertRaises(ValueError):                                    # still two different people
            S.api_validation(self.state, {"by": "Pat Tester", "second": "pat tester", "from_tests": True}, {})
        r = S.api_validation(self.state, {"by": "Pat Tester", "second": "Sam Second", "from_tests": True}, {})
        self.assertEqual(r["result"], "passed")
        ev = self.state.journal.latest(profiles.VALIDATION_KIND, r["release"])["data"]["evidence"]["evidence"]
        self.assertIn("Phase 0 tests passed", ev)


if __name__ == "__main__":
    unittest.main()
