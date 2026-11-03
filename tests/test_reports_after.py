# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The after report, the update report and the volume list (Excel and CSV): the run compared with the state recorded before its cutover (R7)."""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.dom import minidom

from openmove import demo
from openmove import server as S
from openmove.demo.simulate import Simulation
from openmove.engine.evidence import Evidence
from openmove.reports import after, build, sheet
from tests.test_engine import GATE, V1, V2, V3, EngineCase


class AfterReportTests(EngineCase):
    def collect(self):
        return after.collect(self.state, self.state.inventory, GATE, {}, Evidence(self.state.home / "evidence"))

    def moved(self, vid):
        self.start([vid])
        self.run_until(vid, "validate")
        self.sign(vid, "G2", "G3")
        self.run_until(vid, "postcheck")

    def test_a_moved_volume_differs_only_in_back_end_and_type_and_both_are_expected(self):
        self.moved(V1)
        row = self.collect()[0]
        changed = {d["field"]: d for d in row["diffs"] if not d["same"]}
        self.assertEqual(set(changed), {"Back-end", "Volume type"})
        self.assertTrue(all(d["expected"] for d in changed.values()))
        self.assertEqual((row["outcome"], row["verdict"], row["unexpected"]), ("migrated", "as planned", 0))
        self.assertEqual((changed["Back-end"]["before"], changed["Back-end"]["after"]), ("sf-1", "pure-1"))

    def test_a_change_the_move_should_not_make_is_flagged_unexpected(self):
        self.moved(V1)
        self.vol(V1)["size"] = 99                                    # something outside the retype changed the volume
        self.vol(V1)["bootable"] = "true"
        ev = Evidence(self.state.home / "evidence")
        saved = ev.load(V1, "after.json")
        saved["volume"]["size"] = 99
        ev.save(V1, "after.json", saved)
        row = self.collect()[0]
        self.assertEqual(row["verdict"], "unexpected differences")
        self.assertIn("Size (GiB)", [d["field"] for d in row["diffs"] if not d["same"] and not d["expected"]])

    def test_a_reversed_volume_must_match_its_baseline_in_every_field(self):
        self.moved(V2)
        self.engine.reverse(V2, "the application owner reports a problem", "Olive Operator")
        self.run_until(V2, "done", 15)
        row = [r for r in self.collect() if r["id"] == V2][0]
        self.assertEqual((row["outcome"], row["verdict"], row["unexpected"]), ("rolled back", "back at its baseline", 0))
        self.assertTrue(all(d["same"] for d in row["diffs"]))

    def test_the_documents_build_and_name_the_tool_and_the_comparison(self):
        self.moved(V1)
        rows = self.collect()
        for final, title in ((True, "After report"), (False, "Update report")):
            doc = build.after_document(rows, "A customer", "Olive Operator", False, "9.9.9", "sf-1", "pure-1", "gate ok", final=final)
            self.assertEqual(doc["title"], title)
            text = " ".join(str(b.get("text") or b.get("rows") or "") for b in doc["blocks"])
            self.assertIn("changed, expected", text)
            self.assertIn("OpenMove 9.9.9", " ".join(doc["meta"]))

    def test_a_run_with_no_volumes_says_so(self):
        doc = build.after_document([], "c", "", False, "1", "a", "b", "")
        self.assertIn("nothing to compare", " ".join(str(b.get("text") or "") for b in doc["blocks"]))


class SpreadsheetTests(unittest.TestCase):
    ROWS = [["id-1", "Zoë's volume", 10, True, None], ["id-2", "a & b <c>", 2.5, False, "x\x01y"]]
    HEAD = ["Volume id", "Name", "GiB", "Admitted", "Note"]

    def test_the_workbook_is_well_formed_and_holds_the_cells(self):
        data = sheet.xlsx_bytes(self.HEAD, self.ROWS, "Volumes", ["a note"])
        z = zipfile.ZipFile(io.BytesIO(data))
        for name in z.namelist():
            minidom.parseString(z.read(name))
        xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("a &amp; b &lt;c&gt;", xml)
        self.assertIn("Zoë", xml)
        self.assertNotIn("\x01", xml)
        self.assertIn('<pane ySplit="2"', xml)                       # the note row and the header row stay in view

    def test_the_csv_has_a_byte_order_mark_and_quotes(self):
        data = sheet.csv_bytes(self.HEAD, [["a", "x,y", 1, True, None]])
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertIn('"x,y"', data.decode("utf-8-sig"))


class DemoEndpointTests(unittest.TestCase):
    """The same reports through the real handler, in demonstration mode (made-up data, the simulated result)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.server, self.state = S.create_server("127.0.0.1", 0, Path(self.dir.name))
        demo.start(self.state, scenario="sf-to-pure")
        info = S._demo_info(self.state)
        S.api_discover(self.state, {}, {})
        S.api_inventory_run(self.state, {"backend": info["source"]}, {})
        S.api_audit_run(self.state, {}, {})
        S.api_checks_run(self.state, {"source_backend": info["source"], "target_backend": info["target"], "access": "full"}, {})
        S.api_demo_approve(self.state, {}, {})
        self.state.options = {"hold_before_cutover": False, "stop_after_failures": 0}
        self.state.auto_sign = "Demo reviewer (synthetic)"
        sim = Simulation(self.state, lambda: S._gate(self.state), tick=0)
        self.state.sim = sim
        sim.start()
        sim._thread.join(timeout=120)

    def tearDown(self):
        self.state.sim.stop()
        self.server.server_close()
        self.state.disconnect()
        demo.stop(self.state)
        self.state.journal.close()
        self.dir.cleanup()

    def test_after_and_update_reports_are_labelled_as_simulated(self):
        for kind in ("after", "update"):
            d = S.api_report(self.state, {"kind": kind}, {})
            self.assertTrue(d.filename.endswith(".docx"))
            body = zipfile.ZipFile(io.BytesIO(d.data)).read("word/document.xml").decode("utf-8")
            self.assertIn("SIMULATED RUN", body)
            self.assertIn("DEMONSTRATION DATA", body)

    def test_the_volume_list_comes_as_excel_and_csv_and_covers_the_inventory(self):
        n = len(self.state.inventory["volumes"])
        x = S.api_report(self.state, {"kind": "volumes_xlsx"}, {})
        self.assertTrue(x.filename.endswith(".xlsx"))
        self.assertIn("DEMONSTRATION DATA", zipfile.ZipFile(io.BytesIO(x.data)).read("xl/worksheets/sheet1.xml").decode("utf-8"))
        c = S.api_report(self.state, {"kind": "volumes_csv"}, {})
        self.assertEqual(len(c.data.decode("utf-8-sig").strip().splitlines()), n + 1)
        with self.assertRaises(ValueError):
            S.api_report(self.state, {"kind": "nope"}, {})


if __name__ == "__main__":
    unittest.main()
