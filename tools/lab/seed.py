# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Fill the OpenMove lab cloud with test objects, or remove them.   python tools/lab/seed.py [seed|clean|list]

This is lab tooling, not part of the application: it creates things in the LAB cloud (a real OpenStack on this machine, see
tools/lab/README.md) with its own small client. It refuses to run against any address that is not a local one, so it cannot be pointed at a
customer cloud by mistake. Everything it creates has the name prefix "lab-". It uses only the standard library.

Objects, all on the source back-end type (sf-sim):
  lab-a-detached-1..4   1 GiB each, detached                      class A, simplest case
  lab-a-attached-1..2   1 GiB each, attached to a running server  class A, the instance is locked around the move
  lab-boot              1 GiB bootable volume of a running server class A, boot-from-volume
  lab-b-snap            1 GiB with one snapshot                   class B, waits for the snapshot to rotate away
  lab-d-stopped         1 GiB attached to a stopped server        class D, held back
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from openmove import lab  # noqa: E402

PREFIX = "lab-"
SOURCE_TYPE = lab.SOURCE_BACKEND


class Cloud:
    def __init__(self, cfg):
        host = urlsplit(cfg["auth_url"]).hostname or ""
        if not (host in ("127.0.0.1", "localhost", "::1") or host.startswith("192.168.") or host.startswith("172.") or host.startswith("10.")):
            raise SystemExit("refusing to seed %s: the lab seeder works only on a local address" % host)
        body = {"auth": {"identity": {"methods": ["password"], "password": {"user": {
            "name": cfg.get("user", "admin"), "domain": {"id": "default"}, "password": cfg["password"]}}},
            "scope": {"project": {"name": cfg.get("project", "admin"), "domain": {"id": "default"}}}}}
        req = urllib.request.Request(cfg["auth_url"].rstrip("/") + "/v3/auth/tokens", json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            self.token = r.headers["X-Subject-Token"]
            cat = json.loads(r.read())["token"]["catalog"]
        self.ep = {}
        for svc in cat:
            for e in svc["endpoints"]:
                if e["interface"] == "public":
                    self.ep.setdefault(svc["type"], e["url"].rstrip("/"))

    def call(self, method, service, path, body=None, ok=(200, 201, 202, 204), version=None):
        base = self.ep.get(service) or self.ep.get({"volumev3": "block-storage"}.get(service, service))
        headers = {"X-Auth-Token": self.token, "Content-Type": "application/json", "Accept": "application/json"}
        if version:
            headers["OpenStack-API-Version"] = version
        req = urllib.request.Request(base + path, None if body is None else json.dumps(body).encode(), headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            raise SystemExit("%s %s -> %s %s" % (method, path, e.code, e.read(300).decode("utf-8", "replace")))

    def wait(self, what, fn, good, bad=("error",), timeout=300):
        end = time.time() + timeout
        while time.time() < end:
            status = fn()
            if status in good:
                return status
            if status in bad:
                raise SystemExit("%s ended in status %s" % (what, status))
            time.sleep(3)
        raise SystemExit("%s did not reach %s in %d s" % (what, good, timeout))


def existing(c):
    vols = c.call("GET", "volumev3", "/volumes/detail?all_tenants=1")["volumes"]
    srvs = c.call("GET", "compute", "/servers/detail?all_tenants=1")["servers"]
    return [v for v in vols if v["name"].startswith(PREFIX)], [s for s in srvs if s["name"].startswith(PREFIX)]


def make_volume(c, name, size=1, vtype=SOURCE_TYPE, image=None):
    body = {"volume": {"name": name, "size": size, "volume_type": vtype}}
    if image:
        body["volume"]["imageRef"] = image
    v = c.call("POST", "volumev3", "/volumes", body)["volume"]
    c.wait(name, lambda: c.call("GET", "volumev3", "/volumes/" + v["id"])["volume"]["status"], ("available",))
    return v["id"]


def make_server(c, name, net, flavor, image=None, boot_volume=None):
    s = {"name": name, "flavorRef": flavor, "networks": [{"uuid": net}]}
    if boot_volume:
        s["block_device_mapping_v2"] = [{"boot_index": 0, "uuid": boot_volume, "source_type": "volume", "destination_type": "volume"}]
    else:
        s["imageRef"] = image
    sid = c.call("POST", "compute", "/servers", {"server": s})["server"]["id"]
    c.wait(name, lambda: c.call("GET", "compute", "/servers/" + sid)["server"]["status"], ("ACTIVE",), ("ERROR",), 600)
    return sid


def attach(c, sid, vid):
    c.call("POST", "compute", "/servers/%s/os-volume_attachments" % sid, {"volumeAttachment": {"volumeId": vid}})
    c.wait("attach " + vid, lambda: c.call("GET", "volumev3", "/volumes/" + vid)["volume"]["status"], ("in-use",))


def seed(c):
    vols, srvs = existing(c)
    if vols or srvs:
        raise SystemExit("the lab already has %d lab-* volumes and %d lab-* servers; run  seed.py clean  first" % (len(vols), len(srvs)))
    types = {t["name"] for t in c.call("GET", "volumev3", "/types")["volume_types"]}
    if SOURCE_TYPE not in types or lab.TARGET_BACKEND not in types:
        raise SystemExit("the lab needs the volume types %s and %s; found %s" % (SOURCE_TYPE, lab.TARGET_BACKEND, sorted(types)))
    images = c.call("GET", "image", "/v2/images?status=active")["images"]
    image = next((i["id"] for i in images if "cirros" in i["name"].lower()), images[0]["id"] if images else None)
    flavor = next(f["id"] for f in c.call("GET", "compute", "/flavors")["flavors"] if f["name"] == "m1.tiny")
    nets = c.call("GET", "network", "/v2.0/networks")["networks"]
    net = next((n["id"] for n in nets if n["name"] == "private"), nets[0]["id"])
    print("creating detached volumes ...")
    for i in range(1, 5):
        make_volume(c, "lab-a-detached-%d" % i)
    print("creating attached volumes ...")
    for i in (1, 2):
        sid = make_server(c, "lab-srv-%d" % i, net, flavor, image)
        attach(c, sid, make_volume(c, "lab-a-attached-%d" % i))
    print("creating a boot-from-volume server ...")
    boot = make_volume(c, "lab-boot", image=image)
    make_server(c, "lab-srv-boot", net, flavor, boot_volume=boot)
    print("creating a volume with a snapshot ...")
    b = make_volume(c, "lab-b-snap")
    sn = c.call("POST", "volumev3", "/snapshots", {"snapshot": {"volume_id": b, "name": "lab-b-snap-1", "force": True}})["snapshot"]
    c.wait("snapshot", lambda: c.call("GET", "volumev3", "/snapshots/" + sn["id"])["snapshot"]["status"], ("available",))
    print("creating a volume on a stopped server ...")
    sid = make_server(c, "lab-srv-off", net, flavor, image)
    attach(c, sid, make_volume(c, "lab-d-stopped"))
    c.call("POST", "compute", "/servers/%s/action" % sid, {"os-stop": None})
    c.wait("stop", lambda: c.call("GET", "compute", "/servers/" + sid)["server"]["status"], ("SHUTOFF",))
    print("done")
    show(c)


def clean(c):
    vols, srvs = existing(c)
    for s in srvs:
        c.call("DELETE", "compute", "/servers/" + s["id"])
    end = time.time() + 300
    while srvs and time.time() < end and existing(c)[1]:
        time.sleep(3)
    for v in existing(c)[0]:
        for sn in c.call("GET", "volumev3", "/snapshots/detail?volume_id=" + v["id"] + "&all_tenants=1")["snapshots"]:
            c.call("DELETE", "volumev3", "/snapshots/" + sn["id"])
    time.sleep(3)
    for v in existing(c)[0]:
        try:
            c.call("POST", "volumev3", "/volumes/%s/action" % v["id"], {"os-reset_status": {"status": "available", "migration_status": "none"}})
        except SystemExit:
            pass
        c.call("DELETE", "volumev3", "/volumes/" + v["id"])
    print("removed %d volume(s) and %d server(s)" % (len(vols), len(srvs)))


def show(c):
    vols, srvs = existing(c)
    for v in sorted(vols, key=lambda x: x["name"]):
        print("%-22s %-8s %-9s %s" % (v["name"], v["status"], v.get("volume_type"), v.get("os-vol-host-attr:host")))
    print("%d volumes, %d servers" % (len(vols), len(srvs)))


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "list"
    c = Cloud(lab.load_config())
    {"seed": seed, "clean": clean, "list": show}[cmd](c)


if __name__ == "__main__":
    main(sys.argv)
