# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Selecting volumes (R16), workload risk and its clearance (R17), and the per-volume view (R18)."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openmove import demo, handling, risk, selection
from openmove import lifecycle as L
from openmove import server as S
from openmove.demo.simulate import Simulation
from openmove.journal import Journal
from openmove.server import create_server


class Base(unittest.TestCase):
    scenario = "sf-to-pure"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.server, self.state = create_server("127.0.0.1", 0, Path(self.dir.name))
        demo.start(self.state, scenario=self.scenario)
        self.info = S._demo_info(self.state)
        S.api_discover(self.state, {}, {})
        S.api_inventory_run(self.state, {"backend": self.info["source"]}, {})
        S.api_demo_dbexport(self.state, {}, {})
        self.run_checks()

    def run_checks(self):
        return S.api_checks_run(self.state, {"source_backend": self.info["source"], "target_backend": self.info["target"]}, {})

    def tearDown(self):
        if self.state.sim:
            self.state.sim.stop()
        self.server.server_close()
        self.state.disconnect()
        demo.stop(self.state)
        self.state.journal.close()
        self.dir.cleanup()

    def vols(self, cls=None):
        return [v for v in self.state.inventory["volumes"] if cls is None or v["class"] == cls]

    def run_sim(self, **opts):
        S.api_demo_approve(self.state, {}, {})
        self.state.options = dict({"hold_before_cutover": False, "stop_after_failures": 0}, **opts)
        sim = Simulation(self.state, lambda: S._gate(self.state), tick=0)
        self.state.sim = sim
        sim.start()
        sim._thread.join(timeout=60)
        return sim


class SelectionTests(Base):
    def test_the_default_selects_every_admitted_volume(self):
        d = S.api_selection_get(self.state, {}, {})
        self.assertTrue(d["default"])
        self.assertEqual(d["selected"], d["eligible"])
        self.assertEqual(d["selected"], len(self.state.checks["admitted"]))
        self.assertEqual(d["options"], selection.defaults(True))

    def test_pick_volumes_one_by_one(self):
        ids = self.state.checks["admitted"][:3]
        d = S.api_selection_set(self.state, {"action": "set", "ids": ids}, {})
        self.assertEqual(d["selected"], 3)
        self.assertFalse(d["default"])
        d = S.api_selection_set(self.state, {"action": "remove", "ids": ids[:1]}, {})
        self.assertEqual(d["selected"], 2)
        d = S.api_selection_set(self.state, {"action": "add", "ids": ids[:1]}, {})
        self.assertEqual(d["selected"], 3)
        d = S.api_selection_set(self.state, {"action": "none"}, {})
        self.assertEqual(d["selected"], 0)
        d = S.api_selection_set(self.state, {"action": "reset"}, {})
        self.assertTrue(d["default"])

    def test_a_held_back_volume_cannot_be_selected(self):
        held = [v["id"] for v in self.vols() if v["id"] not in self.state.checks["admitted"]]
        self.assertTrue(held)
        d = S.api_selection_set(self.state, {"action": "set", "ids": held + self.state.checks["admitted"][:1]}, {})
        self.assertEqual(d["selected"], 1)
        self.assertEqual({r["id"] for r in d["refused"]}, set(held))
        self.assertTrue(all(not v["selected"] for v in d["volumes"] if v["id"] in held))

    def test_unknown_volume_is_refused(self):
        with self.assertRaises(ValueError):
            S.api_selection_set(self.state, {"action": "add", "ids": ["nope"]}, {})

    def test_select_by_group(self):
        d = S.api_selection_get(self.state, {}, {})
        classes = {g["key"]: g for g in d["groups"]["class"]}
        self.assertTrue({"A", "C"} <= set(classes))
        d = S.api_selection_set(self.state, {"action": "set", "group": {"kind": "class", "key": "C"}}, {})
        self.assertEqual(d["selected"], classes["C"]["eligible"])
        d = S.api_selection_set(self.state, {"action": "add", "group": {"kind": "class", "key": "A"}}, {})
        self.assertEqual(d["selected"], classes["C"]["eligible"] + classes["A"]["eligible"])
        d = S.api_selection_set(self.state, {"action": "remove", "group": {"kind": "class", "key": "C"}}, {})
        self.assertEqual(d["selected"], classes["A"]["eligible"])
        for kind, _ in selection.KINDS:                               # every kind of group can be resolved
            g = S.api_selection_get(self.state, {}, {})["groups"][kind]
            self.assertTrue(g, kind)
        with self.assertRaises(ValueError):
            S.api_selection_set(self.state, {"action": "add", "group": {"kind": "colour", "key": "red"}}, {})

    def test_every_change_is_journaled(self):
        S.api_selection_set(self.state, {"action": "none", "by": "Pat Example"}, {})
        e = self.state.journal.events("selection", None, 5)[0]["data"]
        self.assertEqual(e["by"], "Pat Example")
        self.assertEqual(e["count"], 0)

    def test_options_start_at_the_defaults_and_are_validated(self):
        o = S.api_selection_get(self.state, {}, {})["options"]
        self.assertTrue(o["hold_before_cutover"])
        self.assertEqual((o["rechecks"], o["recheck_gap_min"], o["soak_days"]), (2, 5, 7))
        for bad in ({"rechecks": 1}, {"recheck_gap_min": 4}, {"soak_days": 0}, {"concurrency": 0}, {"concurrency": 99}, {"rate_cap_mb_s": 1},
                    {"hold_before_cutover": 1}, {"concurrency": 1.5}, {"unknown": 3}, {"rechecks": True}):
            with self.assertRaises(ValueError, msg=bad):
                S.api_selection_set(self.state, {"options": bad}, {})
        self.assertIsNone(self.state.options)                          # nothing was half applied
        d = S.api_selection_set(self.state, {"options": {"rechecks": 3, "recheck_gap_min": 10, "concurrency": 2}}, {})
        self.assertEqual((d["options"]["rechecks"], d["options"]["recheck_gap_min"], d["options"]["concurrency"]), (3, 10, 2))
        d = S.api_selection_set(self.state, {"reset_options": True}, {})
        self.assertEqual(d["options"], selection.defaults(True))

    def test_the_selection_cannot_change_during_a_run(self):
        S.api_demo_approve(self.state, {}, {})
        self.state.options = {"hold_before_cutover": True}
        sim = Simulation(self.state, lambda: S._gate(self.state), tick=0.2)
        self.state.sim = sim
        sim.start()
        try:
            with self.assertRaises(S.ApiError):
                S.api_selection_set(self.state, {"action": "none"}, {})
        finally:
            sim.stop()

    def test_only_selected_volumes_are_migrated_and_the_rest_are_reported(self):
        ids = self.state.checks["admitted"][:5]
        S.api_selection_set(self.state, {"action": "set", "ids": ids}, {})
        self.run_sim()
        started = set(L.states(self.state.journal))
        self.assertEqual(started, set(ids))
        u = S.api_unmigrated(self.state, {}, {})
        rest = [r for r in u["rows"] if r["cause"] == "not_selected"]
        self.assertEqual(len(rest), len(self.state.checks["admitted"]) - 5)
        self.assertTrue(all(r["steps"] and r["outcome"] == "not selected" for r in rest))

    def test_nothing_selected_means_nothing_starts(self):
        S.api_demo_approve(self.state, {}, {})
        S.api_selection_set(self.state, {"action": "none"}, {})
        with self.assertRaises(S.ApiError):
            S.api_demo_simulate(self.state, {"action": "start"}, {})


class HoldAndStopTests(Base):
    def test_volumes_wait_in_sync_until_released_then_the_cutover_rules_still_apply(self):
        S.api_demo_approve(self.state, {}, {})
        ids = [v for v in self.state.checks["admitted"] if self.state.inventory["volumes"][0] and True][:6]
        S.api_selection_set(self.state, {"action": "set", "ids": ids}, {})
        self.state.options = {"stop_after_failures": 0}                # hold_before_cutover stays at its default: on
        sim = Simulation(self.state, lambda: S._gate(self.state), tick=0)
        self.state.sim = sim
        sim.start()
        sim.stop()                                                     # the ticks are driven by hand below
        for _ in range(400):
            sim.tick_once()
        live = S.api_live(self.state, {}, {})
        self.assertGreater(live["ready"], 0)
        self.assertIn("waiting for you", sim.status)
        for vid in ids:
            self.assertNotIn(L.phase_of(self.state.journal, vid), ("cutover", "postcheck", "soak"), vid)
        with self.assertRaises(ValueError):
            S.api_release(self.state, {"all": True}, {})                # a name is needed
        ready = [r["id"] for r in live["volumes"] if r["ready"]]
        S.api_release(self.state, {"ids": ready[:1], "by": "Pat Example"}, {})
        for _ in range(60):
            sim.tick_once()
        self.assertIn(L.phase_of(self.state.journal, ready[0]), ("cutover", "postcheck", "soak", "rollback"))
        others = [r for r in ready[1:]]
        for vid in others:
            self.assertNotIn(L.phase_of(self.state.journal, vid), ("cutover", "postcheck", "soak"), vid)    # still waiting
        sim.stop()

    def test_release_ignores_volumes_that_are_not_selected(self):
        ids = self.state.checks["admitted"]
        S.api_selection_set(self.state, {"action": "set", "ids": ids[:2]}, {})
        with self.assertRaises(ValueError):
            S.api_release(self.state, {"ids": ids[5:6], "by": "Pat Example"}, {})

    def test_the_run_halts_itself_after_too_many_failures(self):
        from openmove import safety
        S.api_demo_approve(self.state, {}, {})
        self.state.options = {"hold_before_cutover": False, "stop_after_failures": 2}
        sim = Simulation(self.state, lambda: S._gate(self.state), tick=0)
        self.state.sim = sim
        sim.start()
        sim.stop()                                                     # the ticks are driven by hand below
        for _ in range(300):
            sim.tick_once()
            if safety.halted(self.state.journal):
                break
        h = safety.halted(self.state.journal)
        self.assertTrue(h)
        self.assertIn("OpenMove (automatic)", h["by"])
        self.assertGreaterEqual(sim.failures, 2)
        before = len(L.states(self.state.journal))
        sim.tick_once()
        self.assertEqual(sim.status, "paused by halt")
        self.assertEqual(len(L.states(self.state.journal)), before)
        sim.stop()

    def test_more_rechecks_means_more_passes_before_the_cutover(self):
        S.api_selection_set(self.state, {"options": {"rechecks": 3}}, {})
        opts = self.state.options
        self.run_sim(**opts)
        for vid, e in L.states(self.state.journal).items():
            if e["phase"] in ("soak", "postcheck", "cutover"):
                h = L.history(self.state.journal, vid)
                passes = 0
                for x in h:
                    if x["phase"] == "cutover":
                        break
                    if x["phase"] == "recheck":
                        passes = passes + 1 if x.get("ok") else 0
                    elif x["phase"] == "sync" and x.get("regression"):
                        passes = 0
                self.assertGreaterEqual(passes, 3, vid)


class RiskTests(Base):
    def high(self):
        return {vid: a for vid, a in self.state.checks["risk"].items() if a["level"] == "high"}

    def test_assessments_from_hints(self):
        srv = {"s1": {"name": "web-1", "metadata": {}}}
        base = {"id": "v", "size": 40, "attachments": [{"server_id": "s1"}]}
        legacy = dict(base, image_meta={"os_type": "windows", "os_version": "2008r2", "hw_disk_bus": "ide"})
        self.assertEqual(risk.assess(legacy, srv, {"v"})["level"], "high")
        noagent = dict(base, image_meta={"os_type": "windows", "os_version": "2019", "hw_disk_bus": "virtio"})
        self.assertEqual(risk.assess(noagent, srv, set())["level"], "high")
        ok = dict(base, image_meta={"os_type": "windows", "os_version": "2019", "hw_disk_bus": "virtio", "hw_qemu_guest_agent": "yes"})
        self.assertEqual(risk.assess(ok, srv, set())["level"], "medium")
        linux = dict(base, image_meta={"os_type": "linux", "hw_qemu_guest_agent": "yes"})
        self.assertEqual(risk.assess(linux, srv, {"v"})["level"], "medium")          # a boot volume
        self.assertEqual(risk.assess(linux, srv, set())["level"], "none")
        self.assertEqual(risk.assess({"id": "x", "size": 10, "attachments": []}, {}, set())["level"], "none")   # no hint is not "safe", it is "nothing known"
        self.assertEqual(risk.assess(dict(base, size=2000), srv, set())["level"], "medium")
        marked = risk.assess({"id": "x", "size": 10, "attachments": []}, {}, set(), {"by": "Pat Example", "note": "legacy billing application"})
        self.assertEqual(marked["level"], "high")

    def test_a_high_risk_volume_is_not_moved(self):
        high = self.high()
        self.assertTrue(high)
        for vid in high:
            self.assertNotIn(vid, self.state.checks["admitted"])
        v11 = next(c for c in self.state.checks["volume"] if c["id"] == "V11")
        self.assertEqual(v11["counts"]["fail"], len(high))
        u = S.api_unmigrated(self.state, {}, {})
        causes = {r["volume"]: r["cause"] for r in u["rows"]}
        for vid in high:
            self.assertEqual(causes[vid], "risky_workload")
        d = S.api_selection_set(self.state, {"action": "add", "ids": list(high)}, {})
        self.assertEqual({r["id"] for r in d["refused"]}, set(high))

    def test_clearing_needs_evidence_and_two_different_people(self):
        vid = next(iter(self.high()))
        with self.assertRaises(ValueError):
            S.api_risk_clear(self.state, {"ids": [vid], "role": "operator", "by": "Pat Example", "evidence": "short"}, {})
        with self.assertRaises(ValueError):
            S.api_risk_clear(self.state, {"ids": [vid], "role": "operator", "by": "", "evidence": "tested on a clone in the lab, owner agreed"}, {})
        ev = "tested on a clone in the lab; owner agreed a window; rollback R3 rehearsed"
        S.api_risk_clear(self.state, {"ids": [vid], "role": "operator", "by": "Pat Example", "evidence": ev}, {})
        self.assertNotIn(vid, self.state.checks["admitted"])           # one person is not enough
        S.api_risk_clear(self.state, {"ids": [vid], "role": "reviewer", "by": "pat example", "evidence": ev}, {})
        self.assertNotIn(vid, self.state.checks["admitted"])           # the same person twice is not enough
        S.api_risk_clear(self.state, {"ids": [vid], "role": "reviewer", "by": "Sam Sample", "evidence": ev}, {})
        self.assertIn(vid, self.state.checks["admitted"])
        self.assertTrue(self.state.checks["risk"][vid]["cleared"])
        self.assertNotIn(vid, selection.selected_ids(self.state))      # cleared volumes are never in the default selection
        S.api_selection_set(self.state, {"action": "add", "ids": [vid]}, {})
        self.assertIn(vid, selection.selected_ids(self.state))         # but can be chosen on purpose

    def test_clearing_restarts_the_gate(self):
        S.api_demo_approve(self.state, {}, {})
        self.assertEqual(S._gate(self.state)["status"], "approved")
        vid = next(iter(self.high()))
        ev = "tested on a clone in the lab; owner agreed a window; rollback R3 rehearsed"
        S.api_risk_clear(self.state, {"ids": [vid], "role": "operator", "by": "Pat Example", "evidence": ev}, {})
        self.assertNotEqual(S._gate(self.state)["status"], "approved")  # a new pre-flight run needs both sign-offs again

    def test_a_clearance_does_not_survive_a_change_in_the_risk(self):
        vid = next(iter(self.high()))
        ev = "tested on a clone in the lab; owner agreed a window; rollback R3 rehearsed"
        S.api_risk_clear(self.state, {"ids": [vid], "role": "operator", "by": "Pat Example", "evidence": ev}, {})
        S.api_risk_clear(self.state, {"ids": [vid], "role": "reviewer", "by": "Sam Sample", "evidence": ev}, {})
        self.assertIn(vid, self.state.checks["admitted"])
        S.api_risk_mark(self.state, {"ids": [vid], "by": "Lee Other", "note": "the owner reports a new licence dongle problem"}, {})
        self.assertNotIn(vid, self.state.checks["admitted"])           # the reasons changed: cleared again by two people, or not at all

    def test_withdrawing_a_clearance_blocks_the_volume_again(self):
        vid = next(iter(self.high()))
        ev = "tested on a clone in the lab; owner agreed a window; rollback R3 rehearsed"
        S.api_risk_clear(self.state, {"ids": [vid], "role": "operator", "by": "Pat Example", "evidence": ev}, {})
        S.api_risk_clear(self.state, {"ids": [vid], "role": "reviewer", "by": "Sam Sample", "evidence": ev}, {})
        S.api_risk_clear(self.state, {"ids": [vid], "role": "reviewer", "by": "Sam Sample", "withdraw": True}, {})
        self.assertNotIn(vid, self.state.checks["admitted"])

    def test_a_volume_can_be_marked_by_a_person_and_the_mark_removed(self):
        vid = next(v["id"] for v in self.vols("A") if v["id"] not in self.state.checks["risk"] and v["id"] in self.state.checks["admitted"])
        S.api_risk_mark(self.state, {"ids": [vid], "by": "Pat Example", "note": "legacy billing application, owner Sam"}, {})
        self.assertNotIn(vid, self.state.checks["admitted"])
        self.assertEqual(self.state.checks["risk"][vid]["level"], "high")
        S.api_risk_mark(self.state, {"ids": [vid], "by": "Pat Example", "withdraw": True}, {})
        self.assertIn(vid, self.state.checks["admitted"])
        with self.assertRaises(ValueError):
            S.api_risk_mark(self.state, {"ids": [vid], "by": "Pat Example", "note": "x"}, {})

    def test_a_cleared_risky_volume_is_always_held_for_release_and_can_fail_safely(self):
        ev = "tested on a clone in the lab; owner agreed a window; rollback R3 rehearsed"
        for vid in list(self.high()):
            S.api_risk_clear(self.state, {"ids": [vid], "role": "operator", "by": "Pat Example", "evidence": ev}, {})
            S.api_risk_clear(self.state, {"ids": [vid], "role": "reviewer", "by": "Sam Sample", "evidence": ev}, {})
        cleared = [v for v, a in self.state.checks["risk"].items() if a["cleared"]]
        self.assertTrue(cleared)
        S.api_demo_approve(self.state, {}, {})
        S.api_selection_set(self.state, {"action": "set", "ids": cleared}, {})
        self.state.options = {"hold_before_cutover": False, "stop_after_failures": 0}   # the option is off, risky volumes are held anyway
        sim = Simulation(self.state, lambda: S._gate(self.state), tick=0)
        self.state.sim = sim
        sim.start()
        sim.stop()                                                     # the ticks are driven by hand below
        for _ in range(300):
            sim.tick_once()
        self.assertEqual(S.api_live(self.state, {}, {})["ready"], len(cleared))
        S.api_release(self.state, {"all": True, "by": "Pat Example"}, {})
        for _ in range(200):
            sim.tick_once()
        u = {r["volume"]: r for r in S.api_unmigrated(self.state, {}, {})["rows"]}
        self.assertTrue(any(r["cause"] == "postcheck_failed" and r["outcome"] == "rolled back" for r in u.values()))
        sim.stop()

    def test_the_risk_list_says_what_it_cannot_see(self):
        d = S.api_risk_get(self.state, {}, {})
        self.assertIn("cannot see inside a guest", d["note"])
        self.assertTrue(all(r["reasons"] and r["advice"] for r in d["rows"]))

    def test_clearances_are_read_back_from_the_journal(self):
        j = Journal(Path(self.dir.name) / "other.db")
        risk.record(j, "v1", "sig", "operator", "Pat Example", "x" * 25)
        risk.record(j, "v1", "sig", "reviewer", "Sam Sample", "y" * 25)
        a = {"v1": {"level": "high", "reasons": ["r"]}}
        self.assertEqual(risk.active(j, a), {})                          # different signature: not valid
        sig = risk.signature(a["v1"])
        risk.record(j, "v1", sig, "operator", "Pat Example", "x" * 25)
        risk.record(j, "v1", sig, "reviewer", "Sam Sample", "y" * 25)
        self.assertEqual(risk.active(j, a), {"v1": sig})
        j.close()


class VolumeViewTests(Base):
    def test_every_class_has_its_steps_and_a_status_for_each(self):
        for cls in "ABCDE":
            steps = handling.steps_for(cls)
            self.assertTrue(steps and all(s["does"] and s["touches"] for s in steps), cls)
        self.assertEqual([s["id"] for s in handling.steps_for("B")][:3], ["precheck", "watch", "validate"])
        self.assertEqual([s["id"] for s in handling.steps_for("D")], ["hold"])

    def test_a_volume_that_has_not_started_and_one_in_the_middle(self):
        vid = self.state.checks["admitted"][0]
        v = S.api_volume(self.state, {}, {"id": vid})
        self.assertEqual(v["phase"], None)
        self.assertEqual(v["now"], "Not started.")
        self.assertTrue(all(s["status"] == "pending" for s in v["steps"]))
        self.run_sim(hold_before_cutover=False)
        done = [r for r in S.api_live(self.state, {}, {})["volumes"] if r["phase"] == "soak"]
        self.assertTrue(done)
        v = S.api_volume(self.state, {}, {"id": done[0]["id"]})
        self.assertEqual(v["phase"], "soak")
        self.assertEqual([s["status"] for s in v["steps"]][:7], ["done"] * 7)
        self.assertEqual(v["steps"][-2]["status"], "current")
        self.assertIn("soak", v["now"].lower())
        self.assertTrue(v["events"])

    def test_a_stopped_volume_shows_where_and_why(self):
        self.run_sim()
        stopped = [r for r in S.api_unmigrated(self.state, {}, {})["rows"] if r["outcome"] == "stopped"]
        self.assertTrue(stopped)
        v = S.api_volume(self.state, {}, {"id": stopped[0]["volume"]})
        self.assertTrue(v["now"].startswith("Stopped"))
        self.assertTrue(any(s["status"] == "failed" for s in v["steps"]))
        self.assertTrue(v["unmigrated"]["steps"])

    def test_the_cutover_conditions_are_listed_for_a_volume_in_sync(self):
        S.api_demo_approve(self.state, {}, {})
        self.state.options = {"hold_before_cutover": True, "stop_after_failures": 0}
        sim = Simulation(self.state, lambda: S._gate(self.state), tick=0)
        self.state.sim = sim
        sim.start()
        sim.stop()                                                     # the ticks are driven by hand below
        for _ in range(8):
            sim.tick_once()
        pending = [vid for vid, e in L.states(self.state.journal).items() if e["phase"] in ("sync", "recheck")]
        self.assertTrue(pending)
        v = S.api_volume(self.state, {}, {"id": pending[0]})
        names = [c["name"] for c in v["readiness"]]
        self.assertIn("Rechecks passed in a row", names)
        self.assertEqual(len(names), 8)
        sim.stop()

    def test_readiness_agrees_with_cutover_ready(self):
        S.api_demo_approve(self.state, {}, {})
        gate = S._gate(self.state)
        now = datetime.now(timezone.utc)
        j = self.state.journal
        L.enter(j, "vx", "precheck", {"class": "A"}, gate=gate, now=now)
        L.enter(j, "vx", "validate", {"snapshots_now": 0}, now=now + timedelta(minutes=1))
        L.enter(j, "vx", "copy", {"validated": True}, now=now + timedelta(minutes=2))
        L.enter(j, "vx", "sync", {"size_equal": True, "hash_match": True}, now=now + timedelta(minutes=3))
        rc = {"ok": True, "lag_s": 0, "pending_blocks": 0, "source_fingerprint": "f"}
        for m in (4, 10):
            L.enter(j, "vx", "recheck", rc, now=now + timedelta(minutes=m))
        t = now + timedelta(minutes=11)
        ok, why = L.cutover_ready(j, "vx", gate, t)
        conds = L.readiness(j, "vx", gate, t)
        self.assertEqual(ok, all(c["ok"] for c in conds))
        self.assertTrue(ok, why)
        ok2, _ = L.cutover_ready(j, "vx", gate, t, {"min_rechecks": 3})
        self.assertFalse(ok2)                                           # the operator asked for three passes
        ok3, _ = L.cutover_ready(j, "vx", gate, t, {"recheck_gap": 3600})
        self.assertFalse(ok3)
        ok4, _ = L.cutover_ready(j, "vx", gate, t, {"min_rechecks": 1, "recheck_gap": 1})
        self.assertTrue(ok4)                                            # rules cannot loosen the process minimum


class SoakTests(unittest.TestCase):
    def test_the_source_is_released_only_after_the_soak_period(self):
        d = tempfile.TemporaryDirectory()
        j = Journal(Path(d.name) / "j.db")
        gate = {"status": "approved", "run_id": "r"}
        t0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
        rc = {"ok": True, "lag_s": 0, "pending_blocks": 0, "source_fingerprint": "f"}
        L.enter(j, "v", "precheck", {"class": "A"}, gate=gate, now=t0)
        L.enter(j, "v", "validate", {"snapshots_now": 0}, now=t0)
        L.enter(j, "v", "copy", {"validated": True}, now=t0)
        L.enter(j, "v", "sync", {"size_equal": True, "hash_match": True}, now=t0)
        L.enter(j, "v", "recheck", rc, now=t0 + timedelta(minutes=1))
        L.enter(j, "v", "recheck", rc, now=t0 + timedelta(minutes=8))
        L.enter(j, "v", "cutover", gate=gate, now=t0 + timedelta(minutes=9))
        L.enter(j, "v", "postcheck", now=t0 + timedelta(minutes=10))
        L.enter(j, "v", "soak", {}, now=t0 + timedelta(minutes=11), rules={"soak_days": 14})
        with self.assertRaises(L.LifecycleError):
            L.enter(j, "v", "done", now=t0 + timedelta(days=13))
        L.enter(j, "v", "done", now=t0 + timedelta(days=15))
        j.close()
        d.cleanup()


if __name__ == "__main__":
    unittest.main()
