# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Back-end families and the support matrix (R8): an unknown back-end is unsupported, a known unverified pair is a warning, the document is in step."""
import sys
import tempfile
import unittest
from pathlib import Path

from openmove import backends, demo
from openmove import server as S

ROOT = Path(__file__).resolve().parents[1]


def platform(*pools):
    return {"backends": [{"pool": "h@%s#p" % n, "backend": n, "vendor": v, "protocol": p} for n, v, p in pools], "cinder_services": [], "warnings": []}


class FamilyTests(unittest.TestCase):
    def test_families_are_recognised_by_what_the_driver_reports(self):
        self.assertEqual(backends.family_of("SolidFire Inc", "iSCSI"), "solidfire")
        self.assertEqual(backends.family_of("Pure Storage", "iSCSI"), "pure")
        self.assertEqual(backends.family_of("NetApp", "iSCSI"), "ontap")
        self.assertEqual(backends.family_of("Open Source", "ceph"), "ceph")
        self.assertEqual(backends.family_of("Open Source", "iSCSI"), "lvm")
        self.assertIsNone(backends.family_of("Example Vendor", "iSCSI"))
        self.assertIsNone(backends.family_of(None, None))

    def test_an_unknown_back_end_is_unsupported_and_the_reason_names_it(self):
        a = backends.assess(platform(("src", "Example Vendor", "iSCSI"), ("dst", "Pure Storage", "iSCSI")), "src", "dst")
        self.assertEqual(a["status"], "fail")
        self.assertIn("unsupported", a["evidence"])
        self.assertIn("Example Vendor", a["evidence"])

    def test_a_back_end_without_a_pool_cannot_be_identified(self):
        a = backends.assess(platform(("dst", "Pure Storage", "iSCSI")), "src", "dst")
        self.assertEqual(a["status"], "fail")
        self.assertIn("no pool reported", a["evidence"])

    def test_a_known_pair_that_nobody_verified_is_a_warning_not_a_pass(self):
        a = backends.assess(platform(("sf", "SolidFire Inc", "iSCSI"), ("pure", "Pure Storage", "iSCSI")), "sf", "pure")
        self.assertEqual(a["status"], "warn")
        self.assertIsNone(a["verified"])
        self.assertIn("not been verified", a["evidence"])

    def test_the_lab_pair_is_marked_verified_in_the_lab_only(self):
        a = backends.assess(platform(("a", "Open Source", "iSCSI"), ("b", "Open Source", "iSCSI")), "a", "b")
        self.assertEqual(a["status"], "warn")
        self.assertIn("OpenMove lab", a["evidence"])
        self.assertIn("OpenStack side only", a["evidence"])


class CheckP3Tests(unittest.TestCase):
    def test_the_pre_flight_reports_the_pair_through_the_matrix(self):
        d = tempfile.TemporaryDirectory()
        server, state = S.create_server("127.0.0.1", 0, Path(d.name))
        try:
            demo.start(state, scenario="sf-to-pure")
            info = S._demo_info(state)
            S.api_discover(state, {}, {})
            S.api_inventory_run(state, {"backend": info["source"]}, {})
            c = S.api_checks_run(state, {"source_backend": info["source"], "target_backend": info["target"]}, {})
            p3 = [x for x in c["platform"] if x["id"] == "P3"][0]
            self.assertEqual(p3["status"], "warn")
            self.assertIn("NetApp SolidFire to Pure Storage FlashArray", p3["evidence"])
            self.assertIn("not been verified", p3["evidence"])
        finally:
            server.server_close()
            state.disconnect()
            demo.stop(state)
            state.journal.close()
            d.cleanup()


class MatrixDocumentTests(unittest.TestCase):
    def test_the_document_is_in_step_with_the_data(self):
        sys.path.insert(0, str(ROOT / "tools" / "dev"))
        import support_matrix
        text = (ROOT / "docs" / "SUPPORT_MATRIX.md").read_text(encoding="utf-8")
        self.assertEqual(text.replace("\r\n", "\n"), support_matrix.render().replace("\r\n", "\n"),
                         "docs/SUPPORT_MATRIX.md is out of date: run python tools/dev/support_matrix.py")

    def test_every_pair_of_families_has_a_row(self):
        n = len(backends.data()["families"])
        self.assertEqual(len(backends.matrix()), n * n)


if __name__ == "__main__":
    unittest.main()
