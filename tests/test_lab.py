# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Lab mode: the connection to a real OpenStack on this machine. The tests use the mock cloud as a stand-in for the lab endpoint, so they
prove the wiring and the refusals. That the engine works against real Cinder and Nova is shown by the live lab run (docs/LAB.md)."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from openmove import lab
from openmove import server as S
from openmove.demo.mock_cloud import PASSWORD, MockCloud, sample_cloud
from openmove.server import create_server


class LabConfigTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["OPENMOVE_LAB_DIR"] = self.dir.name

    def tearDown(self):
        os.environ.pop("OPENMOVE_LAB_DIR", None)
        self.dir.cleanup()

    def test_a_missing_lab_says_what_to_do(self):
        with self.assertRaises(lab.LabError) as cm:
            lab.load_config()
        self.assertIn("not set up", str(cm.exception))

    def test_the_config_and_the_secret_are_read_from_the_lab_folder(self):
        (Path(self.dir.name) / "lab.json").write_text(json.dumps({"auth_url": "http://127.0.0.1:1"}), encoding="utf-8")
        (Path(self.dir.name) / "lab-secret.json").write_text(json.dumps({"password": "x"}), encoding="utf-8")
        cfg = lab.load_config()
        self.assertEqual(cfg["auth_url"], "http://127.0.0.1:1")
        self.assertEqual(cfg["password"], "x")

    def test_the_lab_home_is_inside_the_lab_folder(self):
        self.assertEqual(lab.home_dir(), Path(self.dir.name) / "openmove-home")


class LabConnectTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["OPENMOVE_LAB_DIR"] = self.dir.name
        self.cloud = sample_cloud().start()
        (Path(self.dir.name) / "lab.json").write_text(json.dumps({"auth_url": self.cloud.auth_url, "user": "admin", "project": "demo"}), encoding="utf-8")
        (Path(self.dir.name) / "lab-secret.json").write_text(json.dumps({"password": PASSWORD}), encoding="utf-8")
        (Path(self.dir.name) / "home").mkdir()
        self.server, self.state = create_server("127.0.0.1", 0, Path(self.dir.name) / "home")

    def tearDown(self):
        self.server.server_close()
        self.state.disconnect()
        self.state.journal.close()
        self.cloud.stop()
        os.environ.pop("OPENMOVE_LAB_DIR", None)
        self.dir.cleanup()

    def test_connect_logs_in_and_marks_the_state_as_lab(self):
        shown = lab.connect(self.state)
        self.assertNotIn("password", shown)
        self.assertTrue(self.state.lab)
        self.assertFalse(self.state.demo)
        self.assertTrue(S.api_status(self.state, {}, {})["lab"])
        ev = self.state.journal.latest("lab")
        self.assertIn("not a customer cloud", ev["data"]["note"])
        self.assertNotIn(PASSWORD, json.dumps(ev["data"]))

    def test_a_lab_that_is_not_running_is_reported_with_the_command_to_start_it(self):
        self.cloud.stop()
        with self.assertRaises(lab.LabError) as cm:
            lab.connect(self.state)
        self.assertIn("OpenMove-Lab.bat up", str(cm.exception))

    def test_the_lab_shortcuts_are_refused_outside_lab_mode(self):
        with self.assertRaises(S.ApiError):
            S.api_lab_approve(self.state, {}, {})
        with self.assertRaises(S.ApiError):
            S.api_demo_approve(self.state, {}, {})

    def test_the_lab_does_not_validate_a_release_that_has_no_lab_profile(self):
        lab.connect(self.state)
        S.api_discover(self.state, {}, {})
        with self.assertRaises(S.ApiError) as cm:
            S._lab_validate(self.state, {}, {})
        self.assertIn("lab profile", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
