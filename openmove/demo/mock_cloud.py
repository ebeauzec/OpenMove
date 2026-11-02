# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""A mock OpenStack cloud: Keystone, Cinder and Nova on one local port, path-based.

It serves what OpenMove reads. It is used by the automated tests and by demo mode
(python -m openmove --demo). Every identifier, name and figure in it is made up; nothing in it
describes a real system.
"""
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PROJECT = "11111111111111111111111111111111"
PASSWORD = "correct-horse"
TOKEN = "tok-test-1"


def iso(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.000000")


def volume(vid, status="available", size=10, backend="sf-1", snapshots=(), attachments=(), **extra):
    v = {"id": vid, "name": "vol-" + vid[-4:], "status": status, "size": size, "volume_type": "fast",
         "os-vol-host-attr:host": "ctl@%s#pool" % backend, "os-vol-tenant-attr:tenant_id": "p-" + vid[-2:],
         "os-vol-mig-status-attr:migstat": None, "multiattach": False, "encrypted": False,
         "replication_status": "disabled", "bootable": "false", "created_at": iso(500), "updated_at": iso(400),
         "attachments": list(attachments)}
    v.update(extra)
    return v


def snapshot(sid, vid, hours_ago, size=10, status="available"):
    return {"id": sid, "volume_id": vid, "status": status, "size": size, "created_at": iso(hours_ago),
            "updated_at": iso(hours_ago)}


class MockCloud:
    def __init__(self, cinder_max="3.50", nova_max="2.60", admin=True):
        self.cinder_max, self.nova_max, self.admin = cinder_max, nova_max, admin
        self.volumes, self.snapshots, self.servers = [], [], []
        self.pools, self.services, self.hypervisors = [], [], []
        self.types, self.qos, self.backups, self.projects = [], [], [], []
        self.calls = []
        self.meta = {}         # facts only the Cinder database holds, used to make the demonstration database export
        self.scenario = None
        self.server = None
        self.port = None

    @property
    def auth_url(self):
        return "http://127.0.0.1:%d" % self.port

    def start(self, port=0):
        cloud = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, status, body, headers=None):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                cloud.calls.append(("POST", self.path))
                if urlsplit(self.path).path != "/v3/auth/tokens":
                    return self._send(404, {})
                pw = body["auth"]["identity"]["password"]["user"]["password"]
                if pw != PASSWORD:
                    return self._send(401, {"error": {"message": "The request you have made requires authentication."}})
                base = "http://127.0.0.1:%d" % cloud.port
                cat = [
                    {"type": "identity", "endpoints": [{"interface": "public", "url": base + "/v3"}]},
                    {"type": "volumev3", "endpoints": [{"interface": "public", "url": base + "/volume/v3/" + PROJECT}]},
                    {"type": "compute", "endpoints": [{"interface": "public", "url": base + "/compute/v2.1"}]}]
                roles = [{"name": "admin"}] if cloud.admin else [{"name": "member"}]
                self._send(201, {"token": {"catalog": cat, "project": {"id": PROJECT}, "user": {"id": "u1"},
                                           "roles": roles, "expires_at": "2099-01-01T00:00:00Z"}},
                           {"X-Subject-Token": TOKEN})

            def do_GET(self):
                url = urlsplit(self.path)
                path, q = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
                cloud.calls.append(("GET", path))
                if path == "/v3":
                    return self._send(200, {"version": {"id": "v3.14", "status": "stable"}})
                if path == "/volume/":
                    return self._send(200, {"versions": [
                        {"id": "v2.0", "status": "DEPRECATED", "version": "", "min_version": ""},
                        {"id": "v3.0", "status": "CURRENT", "version": cloud.cinder_max, "min_version": "3.0"}]})
                if path == "/compute/":
                    return self._send(200, {"versions": [
                        {"id": "v2.1", "status": "CURRENT", "version": cloud.nova_max, "min_version": "2.1"}]})
                if self.headers.get("X-Auth-Token") != TOKEN:
                    return self._send(401, {"error": {"message": "unauthorized"}})
                cin, nov = "/volume/v3/" + PROJECT, "/compute/v2.1"
                admin_only = {cin + "/os-services": ("services", cloud.services),
                              cin + "/scheduler-stats/get_pools": ("pools", cloud.pools),
                              cin + "/qos-specs": ("qos_specs", cloud.qos),
                              nov + "/os-hypervisors/detail": ("hypervisors", cloud.hypervisors)}
                if path in admin_only:
                    if not cloud.admin:
                        return self._send(403, {"error": {"message": "policy does not allow this"}})
                    key, data = admin_only[path]
                    return self._send(200, {key: data})
                if path == "/v3/projects":
                    if not cloud.admin:
                        return self._send(403, {"error": {"message": "policy does not allow this"}})
                    return self._send(200, {"projects": cloud.projects})
                if path == cin + "/types":
                    return self._send(200, {"volume_types": cloud.types})
                lists = {cin + "/volumes/detail": ("volumes", cloud.volumes),
                         cin + "/snapshots/detail": ("snapshots", cloud.snapshots),
                         cin + "/backups/detail": ("backups", cloud.backups),
                         nov + "/servers/detail": ("servers", cloud.servers)}
                if path in lists:
                    key, data = lists[path]
                    start = 0
                    if q.get("marker"):
                        ids = [x["id"] for x in data]
                        start = ids.index(q["marker"]) + 1
                    limit = int(q.get("limit", 1000))
                    return self._send(200, {key: data[start:start + limit]})
                self._send(404, {"error": {"message": "not found: " + path}})

        self.server = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()


def sample_cloud(**kw):
    """A small cloud: one back-end with volumes of several classes."""
    c = MockCloud(**kw)
    att = lambda sid, dev="/dev/vdb": {"server_id": sid, "device": dev, "host_name": "cmp1"}
    c.volumes = [
        volume("aaaaaaaa-0000-0000-0000-000000000001", size=20),
        volume("aaaaaaaa-0000-0000-0000-000000000002", status="in-use", size=50, attachments=[att("srv-active")]),
        volume("aaaaaaaa-0000-0000-0000-000000000003", size=100),
        volume("aaaaaaaa-0000-0000-0000-000000000004", status="in-use", size=30, attachments=[att("srv-off")]),
        volume("aaaaaaaa-0000-0000-0000-000000000005", size=5, encrypted=True),
        volume("aaaaaaaa-0000-0000-0000-000000000006", size=8, backend="other"),
        volume("aaaaaaaa-0000-0000-0000-000000000007", status="in-use", size=40,
               attachments=[att("srv-bfv", "/dev/vda")]),
    ]
    c.snapshots = [
        snapshot("bbbbbbbb-0000-0000-0000-000000000001", "aaaaaaaa-0000-0000-0000-000000000003", 2000, size=100),
        snapshot("bbbbbbbb-0000-0000-0000-000000000002", "aaaaaaaa-0000-0000-0000-000000000003", 100, size=100),
    ]
    c.servers = [
        {"id": "srv-active", "status": "ACTIVE", "OS-EXT-STS:task_state": None, "image": {"id": "img"}},
        {"id": "srv-off", "status": "SHUTOFF", "OS-EXT-STS:task_state": None, "image": {"id": "img"}},
        {"id": "srv-bfv", "status": "ACTIVE", "OS-EXT-STS:task_state": None, "image": ""},
    ]
    c.pools = [{"name": "ctl@sf-1#pool", "capabilities": {
        "volume_backend_name": "sf-1", "vendor_name": "Example Vendor", "driver_version": "1.2",
        "storage_protocol": "iSCSI", "total_capacity_gb": 1000, "free_capacity_gb": 600}}]
    c.services = [{"binary": "cinder-volume", "host": "ctl@sf-1", "zone": "nova", "status": "enabled",
                   "state": "up", "updated_at": iso(0)}]
    c.hypervisors = [{"hypervisor_hostname": "cmp1", "hypervisor_type": "QEMU", "hypervisor_version": 2011000,
                      "state": "up", "status": "enabled", "running_vms": 3}]
    return c


# ---- demo cloud ------------------------------------------------------------------------------------

def demo_cloud(scenario="sf-to-pure", **kw):
    """A made-up cloud for demonstrations (see scenarios.py for the list). Deterministic."""
    from . import scenarios
    return scenarios.build(scenario, **kw)
