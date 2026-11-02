# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Discovery, inventory and the web server, against the mock cloud."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from openmove import discovery, inventory, profiles
from openmove.connectors.httpclient import ConnectorError
from openmove.connectors.openstack import OpenStackSession
from openmove.journal import Journal
from openmove.server import create_server
from tests.mock_openstack import PASSWORD, sample_cloud


def session_for(cloud, password=PASSWORD):
    return OpenStackSession(cloud.auth_url, "admin", password, "admin")


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.cloud = sample_cloud().start()
        self.dir = tempfile.TemporaryDirectory()
        self.j = Journal(Path(self.dir.name) / "j.db")

    def tearDown(self):
        self.cloud.stop()
        self.j.close()
        self.dir.cleanup()

    def test_detects_queens_and_is_read_only(self):
        s = session_for(self.cloud)
        s.authenticate()
        p = discovery.discover(s, self.j)
        self.assertEqual(p["release"]["release"], "Queens")
        self.assertEqual(p["release"]["confidence"], "exact")
        self.assertEqual(p["block_storage"]["max"], "3.50")
        self.assertEqual(p["compute"]["max"], "2.60")
        self.assertEqual(p["identity"]["version_id"], "v3.14")
        self.assertTrue(p["read_only"])
        self.assertEqual(p["profile"]["status"], "untested")
        self.assertEqual(p["backends"][0]["backend"], "sf-1")
        self.assertEqual(p["backends"][0]["vendor"], "Example Vendor")
        self.assertEqual(p["compute_hosts"][0]["version"], "2.11.0")
        self.assertEqual(p["cinder_services"][0]["binary"], "cinder-volume")

    def test_validated_release_allows_changes(self):
        profiles.record_validation(self.j, "Queens", True)
        s = session_for(self.cloud)
        s.authenticate()
        p = discovery.discover(s, self.j)
        self.assertFalse(p["read_only"])
        profiles.require_mutation_allowed(p["profile"])

    def test_other_releases_follow_their_versions(self):
        self.cloud.cinder_max, self.cloud.nova_max = "3.55", "2.65"
        s = session_for(self.cloud)
        s.authenticate()
        p = discovery.discover(s, self.j)
        self.assertEqual(p["release"]["release"], "Rocky")
        self.assertTrue(p["read_only"])  # no profile for Rocky
        self.assertIn("no profile", p["profile"]["reason"])

    def test_non_admin_degrades_with_warnings(self):
        self.cloud.admin = False
        s = session_for(self.cloud)
        s.authenticate()
        p = discovery.discover(s, self.j)
        self.assertEqual(p["backends"], [])
        self.assertEqual(p["compute_hosts"], [])
        self.assertTrue(any("not available" in w for w in p["warnings"]))
        self.assertEqual(p["release"]["release"], "Queens")  # versions need no admin

    def test_no_credentials_in_the_result(self):
        s = session_for(self.cloud)
        s.authenticate()
        text = json.dumps(discovery.discover(s, self.j))
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn("tok-test-1", text)

    def test_wrong_password(self):
        s = session_for(self.cloud, "wrong")
        with self.assertRaises(ConnectorError) as cm:
            s.authenticate()
        self.assertNotIn("wrong", str(cm.exception))
        self.assertEqual(cm.exception.status, 401)

    def test_unreachable_cloud_is_a_clean_error(self):
        s = OpenStackSession("http://127.0.0.1:9", "u", "p", "pr", timeout=2)
        with self.assertRaises(ConnectorError):
            s.authenticate()


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.cloud = sample_cloud().start()
        self.s = session_for(self.cloud)
        self.s.authenticate()

    def tearDown(self):
        self.cloud.stop()

    def test_collect_and_classify(self):
        raw = inventory.collect(self.s)
        inv = inventory.build(raw["volumes"], raw["servers"], raw["root_volumes"], None, "sf-1")
        self.assertEqual(inv["total"], 6)  # the volume on the other back-end is filtered out
        got = {r["id"][-1]: r["class"] for r in inv["volumes"]}
        self.assertEqual(got["1"], "A")
        self.assertEqual(got["2"], "A")   # attached to an ACTIVE instance
        self.assertEqual(got["3"], "C")   # long-lived snapshots, rotation unknown
        self.assertEqual(got["4"], "D")   # instance SHUTOFF
        self.assertEqual(got["5"], "E")   # encrypted
        self.assertEqual(got["7"], "A")
        self.assertEqual(inv["counts"], {"A": 3, "C": 1, "D": 1, "E": 1})
        self.assertEqual(inv["total_gib"], 20 + 50 + 100 + 30 + 5 + 40)

    def test_snapshot_summary(self):
        raw = inventory.collect(self.s)
        v = [x for x in raw["volumes"] if x["id"].endswith("3")][0]
        self.assertEqual(v["snapshots"], 2)
        self.assertGreater(v["oldest_snapshot_age_h"], 1900)
        self.assertEqual(v["min_snapshot_size_gib"], 100)

    def test_probable_root_volume(self):
        raw = inventory.collect(self.s)
        self.assertEqual(raw["root_volumes"], ["aaaaaaaa-0000-0000-0000-000000000007"])

    def test_assumptions_are_reported(self):
        raw = inventory.collect(self.s)
        inv = inventory.build(raw["volumes"], raw["servers"], raw["root_volumes"])
        self.assertIn("provider_id not checked (needs database access)", inv["assumptions"])

    def test_paging_follows_the_marker(self):
        items = self.s.paginate(("volumev3",), "/volumes/detail", "volumes", {"all_tenants": 1}, page=2)
        self.assertEqual(len(items), 7)
        self.assertEqual(len({i["id"] for i in items}), 7)

    def test_nothing_is_written(self):
        inventory.collect(self.s)
        methods = {m for m, p in self.cloud.calls if p != "/v3/auth/tokens"}
        self.assertEqual(methods, {"GET"})


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.cloud = sample_cloud().start()
        self.dir = tempfile.TemporaryDirectory()
        self.server, self.state = create_server("127.0.0.1", 0, Path(self.dir.name))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.state.disconnect()
        self.state.journal.close()
        self.cloud.stop()
        self.dir.cleanup()

    def call(self, method, path, body=None, token=True, headers=None):
        h = dict(headers or {})
        if token:
            h["X-OpenMove-Token"] = self.state.token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def connect_body(self, **kw):
        b = {"auth_url": self.cloud.auth_url, "username": "admin", "password": PASSWORD,
             "project_name": "admin", "verify": True}
        b.update(kw)
        return b

    def test_page_needs_the_token(self):
        self.assertEqual(self.call("GET", "/", token=False)[0], 403)
        self.assertEqual(self.call("GET", "/?token=nope", token=False)[0], 403)
        status, body = self.call("GET", "/?token=" + self.state.token, token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.state.token.encode(), body)
        self.assertNotIn(b"__TOKEN__", body)

    def test_api_needs_the_token(self):
        self.assertEqual(self.call("GET", "/api/status", token=False)[0], 401)
        self.assertEqual(self.call("GET", "/api/status")[0], 200)

    def test_foreign_host_header_is_refused(self):
        status, _ = self.call("GET", "/api/status", headers={"Host": "evil.example:%d" % self.port})
        self.assertEqual(status, 403)

    def test_static_files_and_security_headers(self):
        req = urllib.request.Request(self.base + "/app.js")
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])

    def test_full_flow(self):
        status, body = self.call("POST", "/api/connect", self.connect_body())
        self.assertEqual(status, 200, body)
        self.assertIn("admin", json.loads(body)["roles"])
        status, body = self.call("POST", "/api/discover", {})
        self.assertEqual(status, 200, body)
        plat = json.loads(body)
        self.assertEqual(plat["release"]["release"], "Queens")
        self.assertTrue(plat["read_only"])
        status, body = self.call("POST", "/api/inventory/run", {"backend": "sf-1"})
        self.assertEqual(status, 200, body)
        inv = json.loads(body)
        self.assertEqual(inv["total"], 6)
        status, body = self.call("GET", "/api/status")
        st = json.loads(body)
        self.assertTrue(st["connected"] and st["platform"] and st["inventory"] and st["read_only"])
        # the journal holds the events and never the password
        status, body = self.call("GET", "/api/journal")
        kinds = {e["kind"] for e in json.loads(body)["events"]}
        self.assertEqual(kinds, {"connect", "platform", "inventory"})
        self.assertNotIn(PASSWORD.encode(), body)
        self.assertNotIn(PASSWORD.encode(), (Path(self.dir.name) / "journal.db").read_bytes())
        self.call("POST", "/api/disconnect", {})
        self.assertFalse(json.loads(self.call("GET", "/api/status")[1])["connected"])

    def test_wrong_password_is_reported_without_it(self):
        status, body = self.call("POST", "/api/connect", self.connect_body(password="not-it"))
        self.assertEqual(status, 502)
        self.assertNotIn(b"not-it", body)

    def test_calls_need_a_connection(self):
        self.assertEqual(self.call("POST", "/api/discover", {})[0], 409)
        self.assertEqual(self.call("POST", "/api/inventory/run", {})[0], 409)

    def test_import_path_works_without_the_cloud(self):
        a1 = ("id\tstatus\tsize\thost\tmultiattach\tencryption_key_id\tgroup_id\tconsistencygroup_id\t"
              "replication_status\tmigration_status\tproject_id\tprovider_id\tsnapshots\toldest_snapshot_age_h\t"
              "min_snapshot_size_gib\nv1\tavailable\t10\tc@sf#p\t0\tNULL\tNULL\tNULL\tdisabled\tNULL\tp\t1 2 u\t0\t0\tNULL\n")
        status, body = self.call("POST", "/api/inventory/import", {"a1": a1})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["counts"], {"A": 1})
        self.assertEqual(self.call("POST", "/api/inventory/import", {"a1": "garbage\n1\n"})[0], 400)

    def test_bad_json_body(self):
        req = urllib.request.Request(self.base + "/api/connect", data=b"not json", method="POST",
                                     headers={"X-OpenMove-Token": self.state.token, "Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
