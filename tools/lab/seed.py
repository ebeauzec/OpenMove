# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Fill the OpenMove lab cloud with test objects, or remove them.   python tools/lab/seed.py [seed|more|clean|list]

This is lab tooling, not part of the application: it creates things in the LAB cloud (a real OpenStack on this machine, see
docs/LAB.md) with its own small client. It refuses to run against an address that is not local, so it cannot be pointed at a customer
cloud by mistake. Everything it creates has the name prefix "lab-". Standard library only.

Volume types (all bound to the source back-end sf-sim, so a retype to the target has to leave them all):
  sf-sim (exists)   sf-gold   sf-bulk   sf-multiattach (multi-attach)   sf-encrypted (LUKS)

Volumes, grouped by what the engine should do with them:
  lab-a-detached-*   plain detached volumes of several sizes and types              class A: moved
  lab-a-image-*      detached bootable volumes made from the cirros image (data)     class A: moved, and they hold real data
  lab-a-attached-*   attached to running servers                                    class A: the instance is locked around the move
  lab-boot-*         boot volumes of running servers (boot-from-volume)              class A: moved with the instance running
  lab-b-snap-*       volumes with one or two snapshots                               class C by API alone (rotation unknown): held back
  lab-d-stopped-*    attached to stopped servers                                     class D: held back
  lab-e-multiattach  a multi-attach volume                                           class E: held back
  lab-e-encrypted    an encrypted volume                                             class E: held back
  lab-t-on-target-*  volumes already on the target back-end (pure-sim)               not admitted: already on the target

`more` adds a second, larger set on top of the first (it grows the two volume groups first, see grow_volume_groups):
  lab-a2-detached-*  12 plain volumes, 1 to 4 GiB, all types                             class A
  lab-a2-image-*     3 bootable volumes with data                                         class A
  lab-a2-attached-*  4 volumes on two running servers (two volumes per server)           class A: one instance, two volumes
  lab-a2-boot-*      2 boot volumes of running servers                                    class A
  lab-a2-paused-1    attached to a paused server                                          class A (a paused guest is allowed)
  lab-c-snap-*       6 detached volumes with 1 to 4 snapshots, one of 2 GiB               class C
  lab-c-wide-1       one volume with 8 snapshots                                          class C (many snapshots)
  lab-c-att-*        3 attached volumes with 1 snapshot each on one running server        class C attached (forced snapshots)
  lab-d-stopped-*    3 more on a stopped server; lab-d-suspended-1 on a suspended one    class D
  lab-e-*            a second multi-attach and encrypted volume, one in status error, one with migration_status migrating   class E
  lab-t-on-target-*  two more on the target                                               not admitted
Class B has no volume of its own: with the APIs only, any volume with snapshots is class C; import a churn export to see class B (docs/LAB.md).
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
SRC, TGT = lab.SOURCE_BACKEND, lab.TARGET_BACKEND
EXTRA_TYPES = {                                  # name -> extra specs (all on the source back-end)
    "sf-gold": {},
    "sf-bulk": {},
    "sf-multiattach": {"multiattach": "<is> True"},
    "sf-encrypted": {},
}


class Cloud:
    def __init__(self, cfg):
        host = urlsplit(cfg["auth_url"]).hostname or ""
        if not (host in ("127.0.0.1", "localhost", "::1") or host.startswith(("192.168.", "172.", "10."))):
            raise SystemExit("refusing to seed %s: the lab seeder works only on a local address" % host)
        body = {"auth": {"identity": {"methods": ["password"], "password": {"user": {
            "name": cfg.get("user", "admin"), "domain": {"id": "default"}, "password": cfg["password"]}}},
            "scope": {"project": {"name": cfg.get("project", "admin"), "domain": {"id": "default"}}}}}
        req = urllib.request.Request(cfg["auth_url"].rstrip("/") + "/v3/auth/tokens", json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            self.token = r.headers["X-Subject-Token"]
            tok = json.loads(r.read())["token"]
            cat = tok["catalog"]
        self.project = tok["project"]["id"]
        self.ep = {}
        for svc in cat:
            for e in svc["endpoints"]:
                if e["interface"] == "public":
                    self.ep.setdefault(svc["type"], e["url"].rstrip("/"))

    def call(self, method, service, path, body=None, soft=False):
        base = self.ep.get(service) or self.ep.get({"volumev3": "block-storage"}.get(service, service))
        headers = {"X-Auth-Token": self.token, "Content-Type": "application/json", "Accept": "application/json"}
        req = urllib.request.Request(base + path, None if body is None else json.dumps(body).encode(), headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            msg = "%s %s -> %s %s" % (method, path, e.code, e.read(300).decode("utf-8", "replace"))
            if soft:
                print("  skipped: " + msg)
                return None
            raise SystemExit(msg)

    def vol(self, vid):
        return self.call("GET", "volumev3", "/volumes/" + vid)["volume"]

    def wait(self, what, fn, good, bad=("error",), timeout=600):
        end = time.time() + timeout
        while time.time() < end:
            status = fn()
            if status in good:
                return status
            if status in bad or str(status).startswith("error"):
                raise SystemExit("%s ended in status %s" % (what, status))
            time.sleep(3)
        raise SystemExit("%s did not reach %s in %d s" % (what, good, timeout))

    def wait_volumes(self, ids, status="available"):
        for vid in ids:
            self.wait("volume " + vid, lambda v=vid: self.vol(v)["status"], (status,))


def existing(c):
    vols = c.call("GET", "volumev3", "/volumes/detail?all_tenants=1")["volumes"]
    srvs = c.call("GET", "compute", "/servers/detail?all_tenants=1")["servers"]
    return [v for v in vols if v["name"].startswith(PREFIX)], [s for s in srvs if s["name"].startswith(PREFIX)]


def new_volume(c, name, size=1, vtype=SRC, image=None):
    body = {"volume": {"name": name, "size": size, "volume_type": vtype}}
    if image:
        body["volume"]["imageRef"] = image
    return c.call("POST", "volumev3", "/volumes", body)["volume"]["id"]


def new_server(c, name, net, flavor, image=None, boot_volume=None):
    s = {"name": name, "flavorRef": flavor, "networks": [{"uuid": net}]}
    if boot_volume:
        s["block_device_mapping_v2"] = [{"boot_index": 0, "uuid": boot_volume, "source_type": "volume", "destination_type": "volume"}]
    else:
        s["imageRef"] = image
    return c.call("POST", "compute", "/servers", {"server": s})["server"]["id"]


def wait_servers(c, ids, status="ACTIVE"):
    for sid in ids:
        c.wait("server " + sid, lambda s=sid: c.call("GET", "compute", "/servers/" + s)["server"]["status"], (status,), ("ERROR",), 900)


def attach(c, sid, vid):
    c.call("POST", "compute", "/servers/%s/os-volume_attachments" % sid, {"volumeAttachment": {"volumeId": vid}})
    c.wait("attach " + vid, lambda: c.vol(vid)["status"], ("in-use",))


def ensure_types(c):
    have = {t["name"]: t for t in c.call("GET", "volumev3", "/types")["volume_types"]}
    for need in (SRC, TGT):
        if need not in have:
            raise SystemExit("the lab needs the volume type %s; found %s" % (need, sorted(have)))
    for name, specs in EXTRA_TYPES.items():
        if name in have:
            continue
        t = c.call("POST", "volumev3", "/types", {"volume_type": {"name": name, "os-volume-type-access:is_public": True,
                                                                  "extra_specs": dict(specs, volume_backend_name=SRC)}})["volume_type"]
        if name == "sf-encrypted":
            c.call("POST", "volumev3", "/types/%s/encryption" % t["id"], {"encryption": {
                "provider": "luks", "cipher": "aes-xts-plain64", "key_size": 256, "control_location": "front-end"}}, soft=True)
        have[name] = t
    return have


def ensure_quota(c):
    """The default quotas (10 volumes, 10 instances) are too small for the catalogue. This is the lab's own admin project."""
    c.call("PUT", "volumev3", "/os-quota-sets/" + c.project, {"quota_set": {"volumes": 200, "snapshots": 200, "gigabytes": 2000}})
    c.call("PUT", "compute", "/os-quota-sets/" + c.project, {"quota_set": {"instances": 50, "cores": 100, "ram": 204800}})


def seed(c):
    vols, srvs = existing(c)
    if vols or srvs:
        raise SystemExit("the lab already has %d lab-* volumes and %d lab-* servers; run  seed.py clean  first" % (len(vols), len(srvs)))
    ensure_quota(c)
    types = ensure_types(c)
    images = c.call("GET", "image", "/v2/images?status=active")["images"]
    image = next((i["id"] for i in images if "cirros" in i["name"].lower()), images[0]["id"] if images else None)
    flavor = next(f["id"] for f in c.call("GET", "compute", "/flavors")["flavors"] if f["name"] == "m1.tiny")
    nets = c.call("GET", "network", "/v2.0/networks")["networks"]
    net = next((n["id"] for n in nets if n["name"] == "private"), nets[0]["id"])

    print("creating detached volumes of several sizes and types ...")
    plan = [("sf-sim", 1), ("sf-sim", 2), ("sf-gold", 1), ("sf-gold", 3), ("sf-bulk", 2), ("sf-bulk", 5), ("sf-sim", 4), ("sf-gold", 2)]
    ids = [new_volume(c, "lab-a-detached-%d" % (i + 1), size, vt) for i, (vt, size) in enumerate(plan)]
    print("creating volumes made from the cirros image (they hold data) ...")
    ids += [new_volume(c, "lab-a-image-%d" % (i + 1), 1, vt, image) for i, vt in enumerate(("sf-sim", "sf-gold", "sf-bulk"))]
    print("creating volumes for snapshots, the edge cases and the target back-end ...")
    snap_vols = [new_volume(c, "lab-b-snap-%d" % (i + 1), 1, vt) for i, vt in enumerate(("sf-sim", "sf-gold", "sf-bulk"))]
    ids += snap_vols
    ids.append(new_volume(c, "lab-e-multiattach", 1, "sf-multiattach"))
    enc = new_volume(c, "lab-e-encrypted", 1, "sf-encrypted") if "sf-encrypted" in types else None
    ids += [enc] if enc else []
    ids += [new_volume(c, "lab-t-on-target-%d" % (i + 1), 1, TGT) for i in range(2)]
    attached = [(("lab-a-attached-%d" % (i + 1)), new_volume(c, "lab-a-attached-%d" % (i + 1), 1 + i % 2, vt))
                for i, vt in enumerate(("sf-sim", "sf-gold", "sf-bulk", "sf-sim"))]
    stopped = [("lab-d-stopped-%d" % (i + 1), new_volume(c, "lab-d-stopped-%d" % (i + 1), 1, "sf-sim")) for i in range(2)]
    boots = [new_volume(c, "lab-boot-%d" % (i + 1), 1, vt, image) for i, vt in enumerate(("sf-sim", "sf-gold"))]
    c.wait_volumes(ids + [v for _, v in attached] + [v for _, v in stopped] + boots)

    print("snapshotting ...")
    snaps = []
    for i, vid in enumerate(snap_vols):
        for k in range(1 + i % 2):
            snaps.append(c.call("POST", "volumev3", "/snapshots", {"snapshot": {"volume_id": vid, "name": "lab-b-snap-%d-%d" % (i + 1, k + 1), "force": True}})["snapshot"]["id"])
    for sid in snaps:
        c.wait("snapshot " + sid, lambda s=sid: c.call("GET", "volumev3", "/snapshots/" + s)["snapshot"]["status"], ("available",))

    print("booting servers (this is the slow part) ...")
    run = [new_server(c, "lab-srv-%d" % (i + 1), net, flavor, image) for i in range(len(attached))]
    off = [new_server(c, "lab-srv-off-%d" % (i + 1), net, flavor, image) for i in range(len(stopped))]
    bfv = [new_server(c, "lab-srv-boot-%d" % (i + 1), net, flavor, boot_volume=b) for i, b in enumerate(boots)]
    wait_servers(c, run + off + bfv)
    print("attaching volumes ...")
    for sid, (_, vid) in zip(run, attached):
        attach(c, sid, vid)
    for sid, (_, vid) in zip(off, stopped):
        attach(c, sid, vid)
        c.call("POST", "compute", "/servers/%s/action" % sid, {"os-stop": None})
    wait_servers(c, off, "SHUTOFF")
    print("done")
    show(c)


def grow_volume_groups(src_gib=220, tgt_gib=260):
    """The two volume groups sit on sparse backing files on loop devices (60 GiB each at first). The larger catalogue needs more room, and a class C move needs
    room on the target for the volume and every snapshot. Growing is online and does not touch the data; the files stay sparse."""
    from openmove.lab import hosts as lab_hosts
    script = chr(10).join(["set -e",
                        "for spec in sf-sim:%d pure-sim:%d; do" % (src_gib, tgt_gib),
                        "  be=${spec%%:*}; gib=${spec##*:}; f=/opt/stack/data/stack-volumes-$be-backing-file",
                        "  cur=$(( $(stat -c %s $f) / 1073741824 ))",
                        "  if [ $cur -lt $gib ]; then truncate -s ${gib}G $f; dev=$(losetup -j $f | cut -d: -f1); losetup -c $dev; pvresize $dev >/dev/null; echo grown $be to ${gib}G; fi",
                        "done",
                        "vgs --noheadings -o vg_name,vg_size,vg_free | sed 's/^/volume group: /'", ""])
    rc, out, err = lab_hosts.run(lab.load_config().get("wsl_distro") or lab.DISTRO, script, timeout=300)
    print((out or err).strip())
    if rc != 0:
        raise SystemExit("could not grow the volume groups: %s" % (err or out).strip())


def wait_for_capacity(c, need_gib=150, timeout=240):
    """Cinder learns the size of a volume group from the back-end's periodic report (every 60 seconds). Volumes made before that are refused with 'no valid
    backend'; found the first time the larger set was made, right after the groups were grown."""
    end = time.time() + timeout
    while time.time() < end:
        pools = c.call("GET", "volumev3", "/scheduler-stats/get_pools?detail=true")["pools"]
        free = {p["name"].split("@")[-1].split("#")[0]: float(p["capabilities"].get("free_capacity_gb") or 0) for p in pools}
        if free.get(SRC, 0) >= need_gib and free.get(TGT, 0) >= need_gib:
            print("Cinder sees the new capacity: %s" % ", ".join("%s %.0f GiB free" % kv for kv in sorted(free.items())))
            return
        time.sleep(10)
    raise SystemExit("Cinder still reports the old capacity after %d seconds; try again in a minute" % timeout)


def seed_more(c):
    """A second, larger set (see the top of this file). Additive: it refuses when it has already been made."""
    vols, srvs = existing(c)
    if not vols:
        raise SystemExit("make the first set first:  seed.py seed")
    if any(v["name"].startswith("lab-c-snap-") for v in vols):
        raise SystemExit("the larger set exists already; run  seed.py clean  to remove everything")
    grow_volume_groups()
    wait_for_capacity(c)
    ensure_quota(c)
    types = ensure_types(c)
    images = c.call("GET", "image", "/v2/images?status=active")["images"]
    image = next((i["id"] for i in images if "cirros" in i["name"].lower()), images[0]["id"] if images else None)
    flavor = next(f["id"] for f in c.call("GET", "compute", "/flavors")["flavors"] if f["name"] == "m1.tiny")
    nets = c.call("GET", "network", "/v2.0/networks")["networks"]
    net = next((n["id"] for n in nets if n["name"] == "private"), nets[0]["id"])
    cycle = ("sf-sim", "sf-gold", "sf-bulk")

    print("creating the volumes ...")
    sizes = (1, 1, 2, 2, 3, 1, 2, 1, 3, 2, 1, 4)
    ids = [new_volume(c, "lab-a2-detached-%d" % (i + 1), s, cycle[i % 3]) for i, s in enumerate(sizes)]
    ids += [new_volume(c, "lab-a2-image-%d" % (i + 1), 1, cycle[i], image) for i in range(3)]
    att = [new_volume(c, "lab-a2-attached-%d" % (i + 1), 1 + i % 2, cycle[i % 3]) for i in range(4)]
    boots = [new_volume(c, "lab-a2-boot-%d" % (i + 1), 1, cycle[i], image) for i in range(2)]
    paused_vol = new_volume(c, "lab-a2-paused-1", 1, "sf-sim")
    csnap = [new_volume(c, "lab-c-snap-%d" % (i + 1), 2 if i == 5 else 1, cycle[i % 3]) for i in range(6)]
    wide = new_volume(c, "lab-c-wide-1", 1, "sf-sim")
    catt = [new_volume(c, "lab-c-att-%d" % (i + 1), 1, cycle[i]) for i in range(3)]
    stopped = [new_volume(c, "lab-d-stopped-%d" % (i + 3), 1, "sf-sim") for i in range(3)]
    susp = new_volume(c, "lab-d-suspended-1", 1, "sf-sim")
    e_vols = [new_volume(c, "lab-e-multiattach-2", 1, "sf-multiattach")]
    if "sf-encrypted" in types:
        e_vols.append(new_volume(c, "lab-e-encrypted-2", 1, "sf-encrypted"))
    e_err = new_volume(c, "lab-e-error-1", 1, "sf-sim")
    e_mig = new_volume(c, "lab-e-migrating-1", 1, "sf-sim")
    on_tgt = [new_volume(c, "lab-t-on-target-%d" % (i + 3), 1, TGT) for i in range(2)]
    c.wait_volumes(ids + att + boots + [paused_vol] + csnap + [wide] + catt + stopped + [susp] + e_vols + [e_err, e_mig] + on_tgt)

    print("snapshotting ...")
    snaps = []
    for i, vid in enumerate(csnap):
        for k in range((1, 2, 3, 4, 2, 1)[i]):
            snaps.append(c.call("POST", "volumev3", "/snapshots", {"snapshot": {"volume_id": vid, "name": "lab-c-snap-%d-%d" % (i + 1, k + 1), "force": True}})["snapshot"]["id"])
    for k in range(8):
        snaps.append(c.call("POST", "volumev3", "/snapshots", {"snapshot": {"volume_id": wide, "name": "lab-c-wide-1-%d" % (k + 1), "force": True}})["snapshot"]["id"])
    for sid in snaps:
        c.wait("snapshot " + sid, lambda s=sid: c.call("GET", "volumev3", "/snapshots/" + s)["snapshot"]["status"], ("available",))

    print("booting servers ...")
    more = [new_server(c, "lab-srv-more-%d" % (i + 1), net, flavor, image) for i in range(3)]
    boot_srv = [new_server(c, "lab-srv-boot-more-%d" % (i + 1), net, flavor, boot_volume=b) for i, b in enumerate(boots)]
    off = new_server(c, "lab-srv-off-3", net, flavor, image)
    pau = new_server(c, "lab-srv-paused-1", net, flavor, image)
    sus = new_server(c, "lab-srv-susp-1", net, flavor, image)
    wait_servers(c, more + boot_srv + [off, pau, sus])
    print("attaching ...")
    for sid, vid in zip((more[0], more[0], more[1], more[1]), att):
        attach(c, sid, vid)
    for vid in catt:
        attach(c, more[2], vid)
        snaps.append(c.call("POST", "volumev3", "/snapshots", {"snapshot": {"volume_id": vid, "name": "lab-c-att-snap", "force": True}})["snapshot"]["id"])
    for sid in snaps[-len(catt):]:
        c.wait("snapshot " + sid, lambda s=sid: c.call("GET", "volumev3", "/snapshots/" + s)["snapshot"]["status"], ("available",))
    for vid in stopped:
        attach(c, off, vid)
    attach(c, pau, paused_vol)
    attach(c, sus, susp)
    c.call("POST", "compute", "/servers/%s/action" % off, {"os-stop": None})
    c.call("POST", "compute", "/servers/%s/action" % pau, {"pause": None})
    c.call("POST", "compute", "/servers/%s/action" % sus, {"suspend": None})
    wait_servers(c, [off], "SHUTOFF")
    wait_servers(c, [pau], "PAUSED")
    wait_servers(c, [sus], "SUSPENDED")
    print("setting the edge cases ...")
    c.call("POST", "volumev3", "/volumes/%s/action" % e_err, {"os-reset_status": {"status": "error"}})
    c.call("POST", "volumev3", "/volumes/%s/action" % e_mig, {"os-reset_status": {"migration_status": "migrating"}})   # seen as a move in flight: class E
    print("done")
    show(c)


def clean(c):
    vols, srvs = existing(c)
    for s in srvs:
        c.call("DELETE", "compute", "/servers/" + s["id"])
    end = time.time() + 600
    while time.time() < end and existing(c)[1]:
        time.sleep(3)
    for v in existing(c)[0]:
        for sn in c.call("GET", "volumev3", "/snapshots/detail?volume_id=%s&all_tenants=1" % v["id"])["snapshots"]:
            c.call("DELETE", "volumev3", "/snapshots/" + sn["id"])
    time.sleep(5)
    for v in existing(c)[0]:
        c.call("POST", "volumev3", "/volumes/%s/action" % v["id"], {"os-reset_status": {"status": "available", "migration_status": "none"}}, soft=True)
        c.call("DELETE", "volumev3", "/volumes/" + v["id"], soft=True)
    end = time.time() + 300
    while time.time() < end and existing(c)[0]:
        time.sleep(3)
    print("removed %d volume(s) and %d server(s); %d volume(s) left" % (len(vols), len(srvs), len(existing(c)[0])))


def show(c):
    vols, srvs = existing(c)
    for v in sorted(vols, key=lambda x: x["name"]):
        print("%-22s %-9s %-15s %3s GiB  %s%s" % (v["name"], v["status"], v.get("volume_type"), v["size"],
                                                 (v.get("os-vol-host-attr:host") or "").split("@")[-1],
                                                 "  attached" if v.get("attachments") else ""))
    print("%d volumes, %d servers" % (len(vols), len(srvs)))


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd not in ("seed", "more", "clean", "list"):
        raise SystemExit(__doc__)
    c = Cloud(lab.load_config())
    {"seed": seed, "more": seed_more, "clean": clean, "list": show}[cmd](c)


if __name__ == "__main__":
    main(sys.argv)
