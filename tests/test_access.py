# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The access level: with the OpenStack APIs only, classes A and B are admitted and class C is held back with a cause and steps."""
import tempfile
import unittest
from pathlib import Path

from openmove import demo, handling
from openmove import server as S
from openmove.server import create_server


class AccessLevelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.server, cls.state = create_server("127.0.0.1", 0, Path(cls.dir.name))
        demo.start(cls.state, scenario="enterprise")
        cls.info = S._demo_info(cls.state)
        S.api_discover(cls.state, {}, {})
        S.api_inventory_run(cls.state, {"backend": cls.info["source"]}, {})
        S.api_demo_dbexport(cls.state, {}, {})

    @classmethod
    def tearDownClass(cls):
        cls.server.server_close()
        cls.state.disconnect()
        demo.stop(cls.state)
        cls.state.journal.close()
        cls.dir.cleanup()

    def run_checks(self, access):
        return S.api_checks_run(self.state, {"source_backend": self.info["source"], "target_backend": self.info["target"], "access": access}, {})

    def classes(self):
        return {v["id"]: v.get("class") for v in self.state.inventory["volumes"]}

    def test_api_only_does_not_admit_class_c_and_says_why(self):
        r = self.run_checks("api")
        self.assertEqual(r["access"], "api")
        cls = self.classes()
        self.assertTrue(r["no_mover"])
        self.assertTrue(all(cls[v] == "C" for v in r["no_mover"]))
        self.assertFalse(set(r["no_mover"]) & set(r["admitted"]))
        self.assertTrue(all(cls[v] in ("A", "B") for v in r["admitted"]))
        self.assertTrue(r["admitted"])
        data = S.api_unmigrated(self.state, {}, {})
        self.assertEqual(data["by_cause"].get("no_data_mover"), len(r["no_mover"]))
        self.assertTrue(all(row["class"] == "C" for row in data["rows"] if row["cause"] == "no_data_mover"))
        c = handling.CAUSES["no_data_mover"]
        self.assertTrue(c[4])                                    # steps to resolve it

    def test_array_checks_are_not_needed_with_api_only(self):
        r = self.run_checks("api")
        by = {c["id"]: c for c in r["platform"]}
        for cid in ("P12", "P14", "P15"):
            self.assertEqual(by[cid]["status"], "info", cid)

    def test_full_access_admits_class_c(self):
        r = self.run_checks("full")
        self.assertEqual(r["no_mover"], [])
        cls = self.classes()
        self.assertTrue(any(cls[v] == "C" for v in r["admitted"]))
        self.state.access = "api"

    def test_unknown_access_is_refused(self):
        with self.assertRaises(ValueError):
            self.run_checks("root")


if __name__ == "__main__":
    unittest.main()
