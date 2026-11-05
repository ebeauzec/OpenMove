# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Found on the real lab: Cinder answers its version document with HTTP 300 (Multiple Choices). That is a normal answer, not a failure,
and the release detection needs the version in it. Other redirects and errors stay failures."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from openmove import discovery
from openmove.connectors.httpclient import ConnectorError, Http

DOC = {"versions": [{"id": "v3.0", "status": "CURRENT", "version": "3.71", "min_version": "3.0"}]}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        code = {"/volume/": 300, "/moved": 302, "/gone": 404}.get(self.path, 200)
        body = json.dumps(DOC).encode()
        self.send_response(code)
        if code == 302:
            self.send_header("Location", "/volume/")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class VersionAnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Handler)
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_a_300_with_the_version_list_is_read_as_the_answer(self):
        status, _, doc = Http().request("GET", self.base + "/volume/")
        self.assertEqual(status, 300)
        self.assertEqual(discovery.pick_current(doc)["version"], "3.71")

    def test_other_non_success_answers_stay_failures(self):
        for path, code in (("/gone", 404),):
            with self.assertRaises(ConnectorError) as cm:
                Http().request("GET", self.base + path)
            self.assertEqual(cm.exception.status, code)


if __name__ == "__main__":
    unittest.main()
