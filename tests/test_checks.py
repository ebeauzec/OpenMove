# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Pre-flight checks, attestations, sign-off and the gate, against the demonstration cloud."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openmove import server as S
from openmove.checks import catalogue, gate
from openmove.checks.gate import GateClosed
from openmove.demo import start as start_demo
from openmove.server import create_server


class ChecksBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.server, self.state = create_server("127.0.0.1", 0, Path(self.dir.name))
        self.cloud = start_demo(self.state)
        S.api_discover(self.state, {}, {})
        S.api_inventory_run(self.state, {}, {})

    def tearDown(self):
        self.server.server_close()
        self.state.disconnect()
        self.state.journal.close()
        self.cloud.stop()
        self.dir.cleanup()

    def run_checks(self, src="sf-prod", dst="pure-prod"):
        return S.api_checks_run(self.state, {"source_backend": src, "target_backend": dst}, {})


class CatalogueTests(unittest.TestCase):
    def test_every_group_is_present(self):
        for group in ("platform", "volume", "staging", "cutover", "post"):
            self.assertTrue(catalogue.of_group(group), group)

    def test_ids_are_unique(self):
        ids = [c[0] for c in catalogue.CHECKS]
        self.assertEqual(len(ids), len(set(ids)))


class RunTests(ChecksBase):
    def test_statuses_are_honest(self):
        c = self.run_checks()
        valid = {"pass", "info", "warn", "fail", "not_checked"}
        for item in c["platform"] + c["volume"]:
            self.assertIn(item["status"], valid)
        by_id = {x["id"]: x for x in c["platform"]}
        # The playbook says a lab validation must be recorded; the demonstration cloud has none yet.
        self.assertEqual(by_id["P16"]["status"], "fail")
        # Anything that needs the Cinder database, an array or a host is never reported as pass.
        for cid in ("P4", "P5", "P6"):
            if cid in by_id:
                self.assertNotEqual(by_id[cid]["status"], "pass", cid)
        self.assertEqual(sum(c["counts"].values()), len(c["platform"]) + len(c["volume"]))

    def test_fail_blocks_the_gate(self):
        c = self.run_checks()
        self.assertEqual(c["gate"]["status"], "blocked")
        self.assertIn("P16", [b["id"] for b in c["gate"]["blockers"]])

    def test_volumes_are_admitted_only_in_classes_a_to_c(self):
        c = self.run_checks()
        self.assertGreater(len(c["admitted"]), 0)
        self.assertLess(len(c["admitted"]), c["volumes_total"])
        failing = {f["volume"] for v in c["volume"] for f in v.get("findings", []) if f["status"] == "fail"}
        self.assertFalse(failing & set(c["admitted"]))

    def test_unknown_backend_is_refused_or_reported(self):
        c = self.run_checks("no-such", "pure-prod")
        by_id = {x["id"]: x for x in c["platform"]}
        self.assertEqual(by_id["P2"]["status"], "fail")


class GateFlowTests(ChecksBase):
    def open_everything(self):
        S.api_demo_validate(self.state, {}, {})
        c = self.run_checks()
        for item in list(c["gate"]["open_items"]):
            S.api_attest(self.state, {"check_id": item["id"], "by": "Pat Example", "note": "checked on site with the customer"}, {})
        return S._checks_view(self.state)

    def test_attestation_needs_name_and_evidence(self):
        self.run_checks()
        with self.assertRaises(ValueError):
            S.api_attest(self.state, {"check_id": "P2", "by": "", "note": "a good reason"}, {})
        with self.assertRaises(ValueError):
            S.api_attest(self.state, {"check_id": "P2", "by": "Pat Example", "note": "ok"}, {})
        with self.assertRaises(ValueError):
            S.api_attest(self.state, {"check_id": "NOPE", "by": "Pat Example", "note": "a good reason"}, {})

    def test_full_flow_to_approved(self):
        c = self.open_everything()
        self.assertEqual(c["gate"]["status"], "awaiting_signoff", c["gate"])
        S.api_signoff(self.state, {"role": "operator", "by": "Pat Example"}, {})
        c = S.api_signoff(self.state, {"role": "reviewer", "by": "Sam Sample"}, {})
        self.assertEqual(c["gate"]["status"], "approved", c["gate"])
        gate.require_open(c["gate"])

    def test_same_person_cannot_sign_twice(self):
        self.open_everything()
        S.api_signoff(self.state, {"role": "operator", "by": "Pat Example"}, {})
        c = S.api_signoff(self.state, {"role": "reviewer", "by": "pat example"}, {})
        self.assertEqual(c["gate"]["status"], "awaiting_signoff")
        with self.assertRaises(GateClosed):
            gate.require_open(c["gate"])

    def test_new_run_needs_new_signoffs_but_keeps_attestations(self):
        self.open_everything()
        S.api_signoff(self.state, {"role": "operator", "by": "Pat Example"}, {})
        c = self.run_checks()
        self.assertEqual(c["gate"]["signoffs"], {})
        self.assertEqual(c["gate"]["open_items"], [])
        self.assertTrue(c["gate"]["attested"])

    def test_withdrawn_attestation_reopens_the_item(self):
        c = self.open_everything()
        first = c["gate"]["attested"][0]["id"]
        c = S.api_attest(self.state, {"check_id": first, "by": "Pat Example", "withdraw": True}, {})
        self.assertIn(first, [i["id"] for i in c["gate"]["open_items"]])
        self.assertEqual(c["gate"]["status"], "open")

    def test_old_run_goes_stale(self):
        c = self.open_everything()
        S.api_signoff(self.state, {"role": "operator", "by": "Pat Example"}, {})
        S.api_signoff(self.state, {"role": "reviewer", "by": "Sam Sample"}, {})
        c = S._checks_view(self.state)
        later = datetime.now(timezone.utc) + timedelta(hours=gate.MAX_AGE_HOURS + 1)
        g = gate.evaluate(self.state.checks, c["attestations"], gate.signoffs(self.state.journal, c["run_id"]), now=later)
        self.assertEqual(g["status"], "stale")

    def test_bad_role_is_refused(self):
        self.run_checks()
        with self.assertRaises(ValueError):
            S.api_signoff(self.state, {"role": "boss", "by": "Pat Example"}, {})

    def test_require_open_fails_closed(self):
        with self.assertRaises(GateClosed):
            gate.require_open(None)
        with self.assertRaises(GateClosed):
            gate.require_open({"status": "open", "message": "x"})

    def test_demo_validation_is_labelled(self):
        S.api_demo_validate(self.state, {}, {})
        events = [e for e in self.state.journal.events("validation", None, 20)]
        self.assertTrue(events)
        self.assertTrue(events[0]["data"]["evidence"].get("demo"))


if __name__ == "__main__":
    unittest.main()
