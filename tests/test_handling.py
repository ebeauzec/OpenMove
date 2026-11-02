# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The class rules (docs/CLASSES.md) and the not-migrated report (requirement R15)."""
import io
import re
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openmove import classification, demo, handling
from openmove import lifecycle as L
from openmove import server as S
from openmove.demo.simulate import Simulation
from openmove.journal import Journal
from openmove.server import create_server

T0 = datetime(2026, 11, 2, 9, 0, 0, tzinfo=timezone.utc)
GATE = {"status": "approved", "message": "ok", "run_id": "r1"}


def at(m):
    return T0 + timedelta(minutes=m)


class ClassRuleTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.j = Journal(Path(self.dir.name) / "j.db")

    def tearDown(self):
        self.j.close()
        self.dir.cleanup()

    def start(self, cls, v="v1"):
        L.enter(self.j, v, "precheck", {"class": cls}, gate=GATE, now=at(0))

    def test_class_d_and_e_never_enter_the_order(self):
        for cls in ("D", "E"):
            with self.assertRaises(L.LifecycleError, msg=cls):
                self.start(cls, "v-" + cls)
            self.assertIsNone(L.phase_of(self.j, "v-" + cls))

    def test_the_class_must_be_given_and_known(self):
        for bad in (None, "", "Z", "a"):
            with self.assertRaises(L.LifecycleError, msg=repr(bad)):
                L.enter(self.j, "v", "precheck", {"class": bad}, gate=GATE, now=at(0))

    def test_class_a_and_b_move_only_with_no_snapshot_left(self):
        for cls in ("A", "B"):
            v = "v-" + cls
            self.start(cls, v)
            for bad in (None, 1, 5):
                with self.assertRaises(L.LifecycleError, msg=(cls, bad)):
                    L.enter(self.j, v, "validate", {"snapshots_now": bad}, now=at(1))
            L.enter(self.j, v, "validate", {"snapshots_now": 0}, now=at(2))

    def test_no_room_on_the_target_is_refused_at_validation(self):
        self.start("A")
        with self.assertRaises(L.LifecycleError):
            L.enter(self.j, "v1", "validate", {"snapshots_now": 0, "room_ok": False}, now=at(1))

    def test_class_c_copy_records_its_snapshot_count_and_verifies_the_chain(self):
        self.start("C")
        L.enter(self.j, "v1", "validate", {"snapshots_now": 4}, now=at(1))
        with self.assertRaises(L.LifecycleError):
            L.enter(self.j, "v1", "copy", {"validated": True}, now=at(2))             # no snapshot count
        L.enter(self.j, "v1", "copy", {"validated": True, "snapshots": 4}, now=at(2))
        ok = {"size_equal": True, "hash_match": True}
        for bad in (dict(ok), dict(ok, chain_match=True, snapshots_matched=3), dict(ok, chain_match=False, snapshots_matched=4)):
            with self.assertRaises(L.LifecycleError, msg=bad):
                L.enter(self.j, "v1", "sync", bad, now=at(60))
        L.enter(self.j, "v1", "sync", dict(ok, chain_match=True, snapshots_matched=4), now=at(60))

    def test_a_class_c_recheck_must_reconcile_the_snapshots(self):
        self.start("C")
        L.enter(self.j, "v1", "validate", {"snapshots_now": 2}, now=at(1))
        L.enter(self.j, "v1", "copy", {"validated": True, "snapshots": 2}, now=at(2))
        L.enter(self.j, "v1", "sync", {"size_equal": True, "hash_match": True, "chain_match": True, "snapshots_matched": 2}, now=at(60))
        rc = {"ok": True, "lag_s": 0, "pending_blocks": 0, "source_fingerprint": "f"}
        L.enter(self.j, "v1", "recheck", dict(rc, reconciled=False, fail_reason="a tenant snapshot was created"), now=at(70))
        h = L.history(self.j, "v1")
        self.assertEqual(h[-1]["phase"], "sync")                       # back to sync, not forward
        self.assertIn("tenant snapshot", h[-1]["reason"])
        L.enter(self.j, "v1", "recheck", dict(rc, reconciled=True), now=at(80))
        L.enter(self.j, "v1", "recheck", dict(rc, reconciled=True), now=at(90))
        ok, why = L.cutover_ready(self.j, "v1", GATE, at(91))
        self.assertTrue(ok, why)

    def test_a_missing_reconcile_is_not_a_pass_for_class_c(self):
        self.start("C")
        L.enter(self.j, "v1", "validate", {"snapshots_now": 1}, now=at(1))
        L.enter(self.j, "v1", "copy", {"validated": True, "snapshots": 1}, now=at(2))
        L.enter(self.j, "v1", "sync", {"size_equal": True, "hash_match": True, "chain_match": True, "snapshots_matched": 1}, now=at(60))
        L.enter(self.j, "v1", "recheck", {"ok": True, "lag_s": 0, "pending_blocks": 0, "source_fingerprint": "f"}, now=at(70))
        self.assertEqual(L.phase_of(self.j, "v1"), "sync")


class CauseTests(unittest.TestCase):
    def test_every_class_has_a_plan(self):
        self.assertEqual(sorted(handling.PLANS), list("ABCDE"))
        for k, p in handling.PLANS.items():
            self.assertTrue(p["title"] and p["method"] and p["steps"], k)

    def test_every_cause_explains_itself_and_proposes_steps(self):
        for code, c in handling.CAUSES.items():
            self.assertEqual(len(c), 6, code)
            title, why, src, tgt, steps, who = c
            for part in (title, why, src, tgt, who):
                self.assertTrue(part and len(part) > 5, code)
            self.assertTrue(steps and all(len(x) > 10 for x in steps), code)

    def test_every_reason_the_classifier_gives_for_class_e_and_d_has_a_cause(self):
        base = {"id": "v", "status": "available", "size": 10, "snapshots": 0, "attachments": []}
        cases = [dict(base, status="error"), dict(base, migration_status="migrating"), dict(base, encrypted=True), dict(base, multiattach=True),
                 dict(base, group_id="g"), dict(base, replication_status="enabled"), dict(base, provider_id="bad"),
                 dict(base, status="in-use", attachments=[{"server_id": "s1"}])]
        seen = set()
        for v in cases:
            r = classification.classify(v, instance_states={"s1": {"status": "SHUTOFF"}})
            self.assertIn(r["class"], ("D", "E"), v)
            code = handling.held_back_cause(dict(v, **{"class": r["class"], "reason": r["reason"]}), [])
            self.assertIn(code, handling.CAUSES, r["reason"])
            seen.add(code)
        self.assertEqual(seen, {"bad_status", "migration_in_flight", "encrypted", "multiattach", "grouped", "replicated", "bad_provider_id", "instance_not_running"})

    def test_every_cause_the_simulation_can_give_exists(self):
        src = (Path(__file__).resolve().parents[1] / "openmove" / "demo" / "simulate.py").read_text(encoding="utf-8")
        used = set(re.findall(r'_abort\(vid, "(\w+)"', src)) | set(re.findall(r'"cause": "(\w+)"', src))
        self.assertTrue(used)
        for code in used:
            self.assertIn(code, handling.CAUSES)


class EnterpriseRunTests(unittest.TestCase):
    """One full simulated run of the enterprise scenario, then everything is checked against it."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.server, cls.state = create_server("127.0.0.1", 0, Path(cls.dir.name))
        demo.start(cls.state, scenario="enterprise")
        info = S._demo_info(cls.state)
        S.api_discover(cls.state, {}, {})
        S.api_inventory_run(cls.state, {"backend": info["source"]}, {})
        S.api_demo_dbexport(cls.state, {}, {})
        S.api_checks_run(cls.state, {"source_backend": info["source"], "target_backend": info["target"]}, {})
        cls.before = S.api_unmigrated(cls.state, {}, {})
        S.api_demo_approve(cls.state, {}, {})
        cls.state.options = {"hold_before_cutover": False, "stop_after_failures": 0}
        sim = Simulation(cls.state, lambda: S._gate(cls.state), tick=0)
        cls.state.sim = sim
        sim.start()
        sim._thread.join(timeout=120)
        cls.sim = sim
        cls.data = S.api_unmigrated(cls.state, {}, {})

    @classmethod
    def tearDownClass(cls):
        cls.server.server_close()
        cls.state.disconnect()
        demo.stop(cls.state)
        cls.state.journal.close()
        cls.dir.cleanup()

    def test_the_run_finished_and_every_class_was_present(self):
        self.assertEqual(self.sim.status, "finished")
        classes = {v["class"] for v in self.state.inventory["volumes"]}
        self.assertEqual(classes, set("ABCDE"))

    def test_held_back_volumes_are_reported_before_anything_runs(self):
        self.assertEqual(self.before["migrated"], 0)
        self.assertEqual(self.before["total"], len(self.state.inventory["volumes"]))
        causes = set(self.before["by_cause"])
        for want in ("encrypted", "multiattach", "grouped", "replicated", "bad_status", "bad_provider_id", "instance_not_running"):
            self.assertIn(want, causes)
        self.assertNotIn("copy_not_verified", causes)

    def test_class_d_and_e_never_started(self):
        for v in self.state.inventory["volumes"]:
            if v["class"] in ("D", "E"):
                self.assertIsNone(L.phase_of(self.state.journal, v["id"]), v["id"])

    def test_every_failure_kind_of_every_class_shows_up(self):
        got = self.data["by_cause"]
        for code in ("copy_not_verified", "never_stable", "no_room", "rotation_timeout", "chain_mismatch", "swap_refused"):
            self.assertIn(code, got)
        by_class = {}
        for r in self.data["rows"]:
            if r["outcome"] in ("stopped", "rolled back"):
                by_class.setdefault(r["class"], set()).add(r["cause"])
        self.assertTrue({"rotation_timeout"} <= by_class["B"])
        self.assertTrue({"chain_mismatch", "swap_refused"} <= by_class["C"])
        self.assertTrue({"copy_not_verified", "no_room", "never_stable"} <= by_class["A"])

    def test_migrated_plus_not_migrated_is_the_whole_inventory(self):
        self.assertEqual(self.data["migrated"] + len(self.data["rows"]) + self.data["rows_more"], self.data["total"])
        ids = [r["volume"] for r in self.data["rows"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_not_migrated_volume_says_why_how_and_what_to_do(self):
        for r in self.data["rows"]:
            self.assertTrue(r["title"] and r["why"] and r["steps"] and r["who"] and r["source"] and r["target"], r["volume"])
            self.assertTrue(r["how"], r["volume"])
            if r["outcome"] in ("stopped", "rolled back"):
                self.assertTrue(r["timeline"], r["volume"])
                self.assertTrue(r["source"].startswith(("Untouched", "Restored")), r["source"])

    def test_nothing_that_failed_passed_through_a_cutover_without_the_rules(self):
        for r in self.data["rows"]:
            if r["outcome"] != "stopped":
                continue
            phases = [h["phase"] for h in L.history(self.state.journal, r["volume"])]
            self.assertNotIn("cutover", phases, r["volume"])               # a stopped volume never reached the production path

    def test_a_rolled_back_volume_did_reach_the_cutover_and_was_restored(self):
        rolled = [r for r in self.data["rows"] if r["outcome"] == "rolled back"]
        self.assertTrue(rolled)
        for r in rolled:
            phases = [h["phase"] for h in L.history(self.state.journal, r["volume"])]
            self.assertIn("cutover", phases)
            self.assertEqual(phases[-1], "rollback")
            self.assertTrue(r["source"].startswith("Restored"))

    def test_class_b_waits_and_its_refusal_is_recorded(self):
        refusals = [e["data"]["reason"] for e in self.state.journal.events("refusal", None, 5000)]
        self.assertTrue(any("moved only with no snapshot left" in x for x in refusals))

    def test_the_report_is_built_and_carries_causes_and_steps(self):
        r = S.api_report(self.state, {"kind": "unmigrated", "prepared_by": "Test"}, {})
        self.assertTrue(r.data.startswith(b"PK"))
        xml = zipfile.ZipFile(io.BytesIO(r.data)).read("word/document.xml").decode("utf-8")
        for text in ("Proposed steps to resolve", "Why this happens", "Snapshot history does not match", "Timeline", "SIMULATED RUN"):
            self.assertIn(text, xml)

    def test_the_pre_migration_report_proposes_steps_for_held_back_volumes(self):
        r = S.api_report(self.state, {"kind": "before"}, {})
        xml = zipfile.ZipFile(io.BytesIO(r.data)).read("word/document.xml").decode("utf-8")
        self.assertIn("Proposed steps to resolve", xml)
        self.assertIn("Encrypted volume", xml)

    def test_the_live_view_counts_rollbacks_and_classes(self):
        live = S.api_live(self.state, {}, {})
        self.assertEqual(live["active"], 0)
        self.assertTrue(live["counts"].get("rollback"))
        self.assertEqual(set(live["by_class"]), set("ABC"))
        self.assertTrue(all("class" in r for r in live["volumes"]))


if __name__ == "__main__":
    unittest.main()
