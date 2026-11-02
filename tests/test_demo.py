# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Demonstration mode: scenarios, the demonstration database export, switching, reports and the HTTP surface."""
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from xml.dom import minidom

from openmove import demo
from openmove import server as S
from openmove.demo import scenarios
from openmove.server import create_server


class DemoBase(unittest.TestCase):
    scenario = scenarios.DEFAULT

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.server, self.state = create_server("127.0.0.1", 0, Path(self.dir.name))
        demo.start(self.state, scenario=self.scenario)

    def tearDown(self):
        self.server.server_close()
        self.state.disconnect()
        demo.stop(self.state)
        self.state.journal.close()
        self.dir.cleanup()


class ScenarioListTests(unittest.TestCase):
    def test_scenarios_cover_the_requested_arrays(self):
        kinds = {b["vendor"] for k in scenarios.SCENARIOS for b in scenarios.describe(k)["backends"]}
        for vendor in ("SolidFire Inc", "Pure Storage", "NetApp"):
            self.assertIn(vendor, kinds)
        for k in scenarios.SCENARIOS:
            self.assertIn("Mirantis", scenarios.describe(k)["distribution"])

    def test_unknown_scenario_is_refused(self):
        with self.assertRaises(ValueError):
            scenarios.get("nope")

    def test_every_scenario_builds_and_is_deterministic(self):
        for k in scenarios.SCENARIOS:
            a, b = scenarios.build(k), scenarios.build(k)
            self.assertEqual([v["id"] for v in a.volumes], [v["id"] for v in b.volumes])
            ids = [v["id"] for v in a.volumes]
            self.assertEqual(len(ids), len(set(ids)), k)
            names = [p["capabilities"]["volume_backend_name"] for p in a.pools]
            self.assertIn(scenarios.get(k)["source"], names)
            self.assertIn(scenarios.get(k)["target"], names)


class SmallOntapTests(DemoBase):
    scenario = "sf-to-ontap"

    def test_ontap_target_is_discovered(self):
        p = S.api_discover(self.state, {}, {})
        vendors = {b["backend"]: b["vendor"] for b in p["backends"]}
        self.assertEqual(vendors, {"sf-prod": "SolidFire Inc", "ontap-prod": "NetApp"})
        S.api_inventory_run(self.state, {}, {})
        c = S.api_checks_run(self.state, {"source_backend": "sf-prod", "target_backend": "ontap-prod"}, {})
        self.assertEqual(c["target_backend"], "ontap-prod")


class EnterpriseTests(DemoBase):
    scenario = "enterprise"

    def setUp(self):
        super().setUp()
        S.api_discover(self.state, {}, {})

    def test_audit_finds_the_whole_range_of_problems(self):
        a = S.api_audit_run(self.state, {}, {})
        ids = {f["id"] for f in a["findings"]}
        for want in ("A1", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A11", "A12", "A14", "A15", "A19"):
            self.assertIn(want, ids)
        self.assertGreater(a["volumes"]["total"], 300)
        self.assertGreaterEqual(len(a["volumes"]["by_project"]), 12)

    def test_database_export_adds_class_b_and_bad_provider_ids(self):
        api = S.api_inventory_run(self.state, {"backend": "sf-prod"}, {})
        self.assertNotIn("B", api["counts"])
        db = S.api_demo_dbexport(self.state, {}, {})
        self.assertEqual(set(db["counts"]), {"A", "B", "C", "D", "E"})
        e_reasons = " ".join(v["reason"] for v in db["volumes"] if v["class"] == "E")
        for text in ("encrypted", "multiattach", "group", "replication", "status error", "provider_id", "migration_status"):
            self.assertIn(text, e_reasons)
        self.assertTrue(any("root-wait" in v["reason"] for v in db["volumes"] if v["class"] == "D"))

    def test_both_copy_modes_are_shown(self):
        S.api_inventory_run(self.state, {"backend": "sf-prod"}, {})
        single = S.api_checks_run(self.state, {"source_backend": "sf-prod", "target_backend": "pure-prod"}, {})
        relay = S.api_checks_run(self.state, {"source_backend": "sf-prod", "target_backend": "ontap-prod"}, {})
        p8 = lambda c: [x for x in c["platform"] if x["id"] == "P8"][0]["evidence"]
        self.assertIn("Single-host copy possible", p8(single))
        self.assertIn("No Cinder host serves both", p8(relay))

    def test_small_target_pool_fails_the_capacity_check(self):
        S.api_inventory_run(self.state, {"backend": "sf-prod"}, {})
        c = S.api_checks_run(self.state, {"source_backend": "sf-prod", "target_backend": "pure-dr"}, {})
        v9 = [x for x in c["volume"] if x["id"] == "V9"][0]
        self.assertEqual(v9["status"], "fail")

    def test_switching_resets_everything(self):
        S.api_inventory_run(self.state, {}, {})
        info = S.api_demo_scenario(self.state, {"id": "sf-to-ontap"}, {})
        self.assertEqual(info["id"], "sf-to-ontap")
        self.assertIsNone(self.state.inventory)
        self.assertIsNone(self.state.platform)
        self.assertEqual(self.state.demo_scenario, "sf-to-ontap")
        self.assertEqual(S.api_status(self.state, {}, {})["demo_scenario"]["target"], "ontap-prod")
        with self.assertRaises(ValueError):
            S.api_demo_scenario(self.state, {"id": "nope"}, {})

    def test_reports_build_and_are_labelled(self):
        S.api_audit_run(self.state, {}, {})
        S.api_inventory_run(self.state, {"backend": "sf-prod"}, {})
        S.api_checks_run(self.state, {"source_backend": "sf-prod", "target_backend": "pure-prod"}, {})
        for kind in ("audit", "before"):
            d = S.api_report(self.state, {"kind": kind}, {})
            self.assertTrue(d.filename.endswith(".docx"))
            z = zipfile.ZipFile(io.BytesIO(d.data))
            for name in z.namelist():
                if name.endswith(".xml") or name.endswith(".rels"):
                    minidom.parseString(z.read(name))
            body = z.read("word/document.xml").decode("utf-8")
            self.assertIn("DEMONSTRATION DATA", body)
            self.assertIn("Typical enterprise cloud", body)


class NotDemoTests(unittest.TestCase):
    def test_demo_endpoints_are_refused_outside_demo_mode(self):
        with tempfile.TemporaryDirectory() as d:
            server, state = create_server("127.0.0.1", 0, Path(d))
            try:
                for fn in (S.api_demo_scenario, S.api_demo_dbexport, S.api_demo_validate):
                    with self.assertRaises(S.ApiError):
                        fn(state, {"id": "enterprise"}, {})
            finally:
                server.server_close()
                state.journal.close()


class HttpTests(DemoBase):
    def setUp(self):
        super().setUp()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        super().tearDown()

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-OpenMove-Token": self.state.token, "Content-Type": "application/json"})
        return urllib.request.urlopen(req)

    def test_status_lists_the_scenarios(self):
        st = json.load(self.call("GET", "/api/status"))
        self.assertTrue(st["demo"])
        self.assertEqual({s["id"] for s in st["demo_scenario"]["scenarios"]}, set(scenarios.SCENARIOS))

    def test_connect_is_refused_in_demo_mode(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.call("POST", "/api/connect", {"auth_url": "http://x", "username": "u", "password": "p", "project_name": "p"})
        self.assertEqual(cm.exception.code, 409)

    def test_report_is_a_download(self):
        self.call("POST", "/api/discover", {})
        self.call("POST", "/api/audit/run", {})
        r = self.call("POST", "/api/report", {"kind": "audit"})
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertTrue(r.read().startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()


class SimulationTests(DemoBase):
    def setUp(self):
        super().setUp()
        S.api_discover(self.state, {}, {})
        S.api_inventory_run(self.state, {"backend": "sf-prod"}, {})
        S.api_checks_run(self.state, {"source_backend": "sf-prod", "target_backend": "pure-prod"}, {})

    def run_sim(self):
        from openmove.demo.simulate import Simulation
        self.state.options = {"hold_before_cutover": False, "stop_after_failures": 0}
        self.state.auto_sign = "Demo reviewer (synthetic)"
        sim = Simulation(self.state, lambda: S._gate(self.state), tick=0)
        self.state.sim = sim
        sim.start()
        sim._thread.join(timeout=60)
        return sim

    def test_nothing_starts_until_the_gate_is_approved(self):
        with self.assertRaises(S.ApiError):
            S.api_demo_simulate(self.state, {"action": "start"}, {})
        self.assertEqual(S.api_live(self.state, {}, {})["started"], 0)

    def test_the_gate_helper_cannot_waive_a_failed_check(self):
        g = S.api_demo_approve(self.state, {}, {})["gate"]
        self.assertEqual(g["status"], "approved")
        self.assertEqual(g["signoffs"]["operator"]["by"], "Demo operator")
        self.assertNotEqual(g["signoffs"]["operator"]["by"], g["signoffs"]["reviewer"]["by"])

    def test_the_simulated_run_follows_the_rules_and_shows_the_failures(self):
        S.api_demo_approve(self.state, {}, {})
        sim = self.run_sim()
        live = S.api_live(self.state, {}, {})
        self.assertEqual(sim.status, "finished", sim.status)
        self.assertEqual(live["overall"], round(sum(r["progress"] for r in live["volumes"]) / live["total"], 1))
        self.assertTrue(live["counts"].get("soak"))
        self.assertTrue(live["counts"].get("aborted"))                 # the copy that did not verify
        refusals = [e for e in self.state.journal.events("refusal", None, 500)]
        self.assertTrue(any("passed recheck" in e["data"]["reason"] for e in refusals))   # the premature cutover
        self.assertTrue(any("did not verify" in e["data"]["reason"] or "verified" in e["data"]["reason"] for e in refusals))
        regress = [e for e in self.state.journal.events("phase", None, 5000) if e["data"].get("regression")]
        self.assertTrue(regress)                                       # a source that changed went back to sync
        # every volume that reached the cutover had two passed rechecks first
        from openmove import lifecycle as L
        for vid, e in L.states(self.state.journal).items():
            if e["phase"] in ("soak", "postcheck", "cutover"):
                phases = [h["phase"] for h in L.history(self.state.journal, vid)]
                self.assertEqual(phases.count("recheck") >= 2, True, vid)
                self.assertLess(phases.index("copy"), phases.index("sync"))
                self.assertLess(phases.index("sync"), phases.index("cutover"))

    def test_halt_pauses_the_run_and_resume_continues_it(self):
        from openmove import safety
        S.api_demo_approve(self.state, {}, {})
        safety.halt(self.state.journal, "Pat Example", "pause for a question")
        from openmove.demo.simulate import Simulation
        self.state.auto_sign = "Demo reviewer (synthetic)"
        s = Simulation(self.state, lambda: S._gate(self.state), tick=0)
        s.start()
        s.tick_once()
        self.assertEqual(s.status, "paused by halt")
        self.assertEqual(S.api_live(self.state, {}, {})["started"], 0)
        s.stop()
        safety.resume(self.state.journal, "Sam Sample", "question answered")

    def test_a_gate_that_stops_being_approved_stops_the_run(self):
        from openmove.demo.simulate import Simulation
        S.api_demo_approve(self.state, {}, {})
        self.state.auto_sign = "Demo reviewer (synthetic)"
        s = Simulation(self.state, lambda: S._gate(self.state), tick=0)
        s.start()
        s.stop()
        S.api_attest(self.state, {"check_id": "P2", "by": "Demo operator", "withdraw": True}, {})
        g = S._gate(self.state)
        self.assertNotEqual(g["status"], "approved")
        self.assertFalse(s.tick_once())
        self.assertIn("no longer approved", s.status)


class LiveViewTests(DemoBase):
    def test_live_view_is_available_before_anything_runs(self):
        live = S.api_live(self.state, {}, {})
        self.assertEqual(live["started"], 0)
        self.assertEqual(live["mode"], "idle")
        self.assertEqual(len(live["cutover_rules"]), 8)
