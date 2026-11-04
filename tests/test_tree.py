# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The volume tree: projects, instances, attached volumes, loose volumes, and the plain-words verdict."""
import tempfile
import unittest
from pathlib import Path

from openmove import demo, tree
from openmove import server as S
from openmove.server import create_server


class TreeBuild(unittest.TestCase):
    def inv(self):
        return {"volumes": [
            {"id": "v1", "name": "root", "size": 10, "snapshots": 0, "class": "A", "project_id": "p1", "attachments": [{"server_id": "s1", "device": "/dev/vda"}]},
            {"id": "v2", "name": "data", "size": 20, "snapshots": 3, "class": "C", "project_id": "p1", "attachments": [{"server_id": "s1", "device": "/dev/vdb"}]},
            {"id": "v3", "name": "spare", "size": 5, "snapshots": 0, "class": "A", "project_id": "p1", "attachments": []},
            {"id": "v4", "name": "shared", "size": 7, "snapshots": 0, "class": "E", "reason": "multiattach", "project_id": "p2",
             "attachments": [{"server_id": "s2", "device": "/dev/vdb"}, {"server_id": "gone", "device": "/dev/vdc"}]}]}

    def test_groups_by_project_and_instance(self):
        t = tree.build(self.inv(), {"s1": {"name": "web-1", "status": "ACTIVE"}, "s2": {"name": "db-1", "status": "SHUTOFF"}}, {"p1": "Alpha", "p2": "Beta"}, roots={"v1"})
        self.assertEqual([p["name"] for p in t["projects"]], ["Alpha", "Beta"])
        alpha = t["projects"][0]
        self.assertEqual(alpha["volumes"], 3)
        self.assertEqual(alpha["gib"], 35)
        self.assertEqual([i["name"] for i in alpha["instances"]], ["web-1"])
        self.assertEqual([v["name"] for v in alpha["instances"][0]["volumes"]], ["root", "data"])    # the boot volume first
        self.assertTrue(alpha["instances"][0]["volumes"][0]["boot"])
        self.assertEqual([v["name"] for v in alpha["loose"]], ["spare"])
        self.assertEqual(t["total"], 4)

    def test_a_shared_volume_is_shown_under_each_instance_and_an_unknown_instance_is_named(self):
        t = tree.build(self.inv(), {"s2": {"name": "db-1"}}, {"p2": "Beta"})
        beta = t["projects"][1] if t["projects"][0]["name"] != "Beta" else t["projects"][0]
        self.assertEqual(sorted(i["name"] for i in beta["instances"]), ["(unknown instance gone)", "db-1"])
        self.assertEqual(beta["volumes"], 1)                      # counted once

    def test_plain_words_for_every_class(self):
        for cls in "ABCDE":
            p = tree.plain(cls)
            self.assertTrue(p["label"] and p["meaning"] and p["level"] in ("ok", "warn", "stop"))
        self.assertEqual(tree.plain("Z")["level"], "stop")

    def test_verdict_after_the_pre_flight(self):
        t = tree.build(self.inv(), {}, {}, eligible={"v1", "v2"}, selected={"v1"}, failed={"v4": ["V3"]})
        by = {v["id"]: v for p in t["projects"] for i in p["instances"] for v in i["volumes"]}
        by.update({v["id"]: v for p in t["projects"] for v in p["loose"]})
        self.assertEqual(by["v1"]["verdict"], "Will be moved")
        self.assertEqual(by["v2"]["verdict"], "Not chosen")
        self.assertEqual(by["v3"]["verdict"], "Held back")
        self.assertIn("V3", by["v4"]["why"])
        self.assertTrue(t["checked"])
        self.assertEqual(t["moving"], 1)

    def test_no_verdict_before_the_pre_flight(self):
        t = tree.build(self.inv(), {}, {})
        self.assertFalse(t["checked"])
        self.assertTrue(all(v["verdict"] == "" for p in t["projects"] for v in p["loose"]))


class TreeApi(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.server, self.state = create_server("127.0.0.1", 0, Path(self.dir.name))
        demo.start(self.state, scenario="sf-to-pure")
        self.info = S._demo_info(self.state)
        S.api_discover(self.state, {}, {})
        S.api_inventory_run(self.state, {"backend": self.info["source"]}, {})

    def tearDown(self):
        self.server.server_close()
        self.state.disconnect()
        demo.stop(self.state)
        self.state.journal.close()
        self.dir.cleanup()

    def test_tree_from_the_demonstration_cloud(self):
        t = S.api_tree(self.state, {}, {})
        self.assertEqual(t["total"], self.state.inventory["total"])
        self.assertFalse(t["checked"])
        self.assertTrue(any(p["instances"] for p in t["projects"]))

    def test_tree_after_the_pre_flight_says_what_happens_to_each_volume(self):
        S.api_checks_run(self.state, {"source_backend": self.info["source"], "target_backend": self.info["target"]}, {})
        t = S.api_tree(self.state, {}, {})
        self.assertTrue(t["checked"])
        verdicts = {v["verdict"] for p in t["projects"] for i in p["instances"] for v in i["volumes"]} | {v["verdict"] for p in t["projects"] for v in p["loose"]}
        self.assertTrue(verdicts <= {"Will be moved", "Not chosen", "Held back"})
        self.assertTrue(verdicts)

    def test_tree_needs_the_inventory(self):
        self.state.inventory = None
        with self.assertRaises(S.ApiError):
            S.api_tree(self.state, {}, {})


if __name__ == "__main__":
    unittest.main()
