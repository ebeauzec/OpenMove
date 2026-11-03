# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Lab mode: the connection to a real OpenStack on this machine. The tests use the mock cloud as a stand-in for the lab endpoint, so they
prove the wiring and the refusals. That the engine works against real Cinder and Nova is shown by the live lab run (docs/LAB.md)."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

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

    def test_the_dashboard_is_on_the_same_host_as_keystone(self):
        self.assertEqual(lab.horizon_url({"auth_url": "http://127.0.0.1/identity"}), "http://127.0.0.1/dashboard")
        self.assertEqual(lab.horizon_url({"auth_url": "http://localhost:5000/v3"}), "http://localhost:5000/dashboard")
        self.assertFalse(lab.horizon_answers({"auth_url": "http://127.0.0.1:1/identity"}, timeout=1))

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


class FakeHorizon:
    """Answers like Horizon's login: a form with a CSRF token and a region select, a session cookie after a correct post."""

    def __init__(self, password):
        self.password, self.posted = password, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = (b'<form method="post"><input type="hidden" name="csrfmiddlewaretoken" value="tok">'
                        b'<select name="region"><option value="http://k/v3">Default</option></select>'
                        b'<input name="domain"><input name="username"><input name="password" type="password"></form>')
                self.send_response(200)
                self.send_header("Set-Cookie", "csrftoken=abc; Path=/")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
                outer.posted.append({k: v for k, v in form.items() if k != "password"})
                good = form.get("password") == outer.password and form.get("csrfmiddlewaretoken") == "tok" and "csrftoken=abc" in (self.headers.get("Cookie") or "")
                self.send_response(302 if good else 200)
                if good:
                    self.send_header("Set-Cookie", "sessionid=sess1; Path=/")
                    self.send_header("Location", "/dashboard/")
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class LabDashboardTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["OPENMOVE_LAB_DIR"] = self.dir.name
        self.horizon = FakeHorizon("lab-pass")
        (Path(self.dir.name) / "lab.json").write_text(json.dumps({"auth_url": self.horizon.url + "/identity", "user": "admin"}), encoding="utf-8")
        (Path(self.dir.name) / "lab-secret.json").write_text(json.dumps({"password": "lab-pass"}), encoding="utf-8")
        (Path(self.dir.name) / "home").mkdir()
        self.server, self.state = create_server("127.0.0.1", 0, Path(self.dir.name) / "home")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.state.journal.close()
        self.horizon.stop()
        os.environ.pop("OPENMOVE_LAB_DIR", None)
        self.dir.cleanup()

    def get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        c.request("GET", path)
        r = c.getresponse()
        r.read()
        c.close()
        return r

    def test_the_login_posts_the_form_fields_and_returns_the_session(self):
        cookies = lab.horizon_session(lab.load_config())
        self.assertEqual(cookies["sessionid"], "sess1")
        self.assertEqual(self.horizon.posted[0]["region"], "http://k/v3")
        self.assertEqual(self.horizon.posted[0]["domain"], "Default")

    def test_a_wrong_password_is_reported_without_it(self):
        (Path(self.dir.name) / "lab-secret.json").write_text(json.dumps({"password": "wrong-one"}), encoding="utf-8")
        with self.assertRaises(lab.LabError) as cm:
            lab.horizon_session(lab.load_config())
        self.assertNotIn("wrong-one", str(cm.exception))

    def test_the_route_signs_the_browser_in_and_redirects_to_the_dashboard(self):
        self.state.lab = True
        r = self.get("/lab/dashboard?token=" + self.state.token)
        self.assertEqual(r.status, 302)
        self.assertIn("sessionid=sess1", " ".join(v for k, v in r.getheaders() if k == "Set-Cookie"))
        self.assertEqual(r.getheader("Location"), "http://127.0.0.1:%d/dashboard/" % self.horizon.server.server_address[1])
        self.assertNotIn("lab-pass", str(r.getheaders()))

    def test_the_route_needs_the_token_and_lab_mode(self):
        self.assertEqual(self.get("/lab/dashboard?token=wrong").status, 403)
        self.assertEqual(self.get("/lab/dashboard?token=" + self.state.token).status, 404)       # not in lab mode


if __name__ == "__main__":
    unittest.main()
