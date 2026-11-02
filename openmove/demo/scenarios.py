# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Demonstration scenarios: made-up clouds that look like the ones OpenMove is used on.

Three scenarios ship with the tool. Each builds a MockCloud with deterministic data, so a
demonstration looks the same every time:

  sf-to-pure   a small cloud: SolidFire source, one Pure Storage FlashArray target
  sf-to-ontap  the same cloud with a NetApp ONTAP target
  enterprise   a large, messy cloud: several SolidFire clusters, Pure, NetApp ONTAP and Ceph,
               14 projects, ~100 instances, ~340 volumes with a case for every volume class and
               every pre-flight check, and the problems an environment audit finds

The cloud distribution named in a scenario (Mirantis Cloud Platform) is a label for the
demonstration only. OpenMove identifies the OpenStack release from the API versions; it does
not detect the distribution.

Everything here is invented. No name, address or figure describes a real system.
"""
import random

from .mock_cloud import MockCloud, iso, snapshot, volume

DEFAULT = "sf-to-pure"
MIRANTIS = "Mirantis Cloud Platform 2019.2 (OpenStack Queens)"

# vendor_name, driver_version, storage_protocol as the Cinder drivers report them (the figures are made up)
ARRAYS = {
    "solidfire": ("SolidFire Inc", "2.0.2", "iSCSI"),
    "pure": ("Pure Storage", "16.0.0", "iSCSI"),
    "ontap": ("NetApp", "1.0.0", "iSCSI"),
    "ceph": ("Open Source", "1.3.0", "ceph"),
}

SCENARIOS = {
    "sf-to-pure": {
        "title": "SolidFire to Pure FlashArray (small)",
        "summary": "A small cloud with one SolidFire back-end and one Pure Storage FlashArray. The simplest end-to-end demonstration.",
        "distribution": MIRANTIS, "source": "sf-prod", "target": "pure-prod", "size": "small",
        "backends": [("sf-prod", "solidfire"), ("pure-prod", "pure")]},
    "sf-to-ontap": {
        "title": "SolidFire to NetApp ONTAP (small)",
        "summary": "The same small cloud with a NetApp ONTAP back-end as the destination.",
        "distribution": MIRANTIS, "source": "sf-prod", "target": "ontap-prod", "size": "small",
        "backends": [("sf-prod", "solidfire"), ("ontap-prod", "ontap")]},
    "enterprise": {
        "title": "Typical enterprise cloud (large)",
        "summary": ("A large, busy cloud: two SolidFire clusters as sources; Pure Storage (production and DR), NetApp ONTAP and Ceph as "
                    "other back-ends; 14 projects; about 100 instances; over 300 volumes covering every volume class, rotating and "
                    "long-lived snapshots, boot volumes, encrypted, replicated, grouped and shared volumes, stuck and inconsistent "
                    "volumes, mixed compute versions, a nearly full DR pool and a standby service that is down. Use 'Load the "
                    "demonstration database export' on the Inventory tab to add the facts only the Cinder database holds."),
        "distribution": MIRANTIS, "source": "sf-prod", "target": "pure-prod", "size": "large",
        "backends": [("sf-prod", "solidfire"), ("sf-bulk", "solidfire"), ("pure-prod", "pure"), ("pure-dr", "pure"),
                     ("ontap-prod", "ontap"), ("ceph-rbd", "ceph")]},
}


def get(scenario_id):
    if scenario_id not in SCENARIOS:
        raise ValueError("unknown scenario %r; choose one of: %s" % (scenario_id, ", ".join(SCENARIOS)))
    return SCENARIOS[scenario_id]


def describe(scenario_id):
    s = get(scenario_id)
    return {"id": scenario_id, "title": s["title"], "summary": s["summary"], "distribution": s["distribution"],
            "source": s["source"], "target": s["target"], "size": s["size"],
            "backends": [{"name": n, "kind": k, "vendor": ARRAYS[k][0]} for n, k in s["backends"]]}


def listing():
    return [describe(k) for k in SCENARIOS]


def build(scenario_id=DEFAULT, **kw):
    """Build the mock cloud of one scenario (not started)."""
    s = get(scenario_id)
    cloud = _enterprise(s, **kw) if s["size"] == "large" else _small(s, **kw)
    cloud.scenario = scenario_id
    return cloud


def _uid(prefix, n):
    return "%s-%04x-4000-8000-%012x" % (prefix, n, n * 7919)


def _pool(host, name, kind, total, free, provisioned, ratio, reserved=0):
    vendor, drv, proto = ARRAYS[kind]
    return {"name": "%s@%s#pool" % (host, name), "capabilities": {
        "volume_backend_name": name, "vendor_name": vendor, "driver_version": drv, "storage_protocol": proto,
        "total_capacity_gb": total, "free_capacity_gb": free, "provisioned_capacity_gb": provisioned,
        "max_over_subscription_ratio": ratio, "reserved_percentage": reserved,
        "replication_enabled": False, "multiattach": True, "thin_provisioning_support": True}}


def _svc(binary, host, status="enabled", state="up", hours=0, reason=None):
    d = {"binary": binary, "host": host, "zone": "az1", "status": status, "state": state, "updated_at": iso(hours)}
    if reason:
        d["disabled_reason"] = reason
    return d


# ---- the small scenarios ---------------------------------------------------------------------------

# image properties of boot volumes, so the workload-risk check has something to find (openmove/risk.py)
IMG_WIN_LEGACY = {"os_type": "windows", "os_distro": "windows", "os_version": "2008r2", "image_name": "windows-server-2008-r2", "hw_disk_bus": "ide"}
IMG_WIN_NO_AGENT = {"os_type": "windows", "os_distro": "windows", "os_version": "2016", "image_name": "windows-server-2016", "hw_disk_bus": "virtio"}
IMG_WIN_OK = {"os_type": "windows", "os_distro": "windows", "os_version": "2019", "image_name": "windows-server-2019", "hw_disk_bus": "virtio", "hw_qemu_guest_agent": "yes"}
IMG_LINUX = {"os_type": "linux", "os_distro": "ubuntu", "os_version": "18.04", "image_name": "ubuntu-18.04", "hw_qemu_guest_agent": "yes"}


def _small(spec, **kw):
    """Two back-ends, five projects, and the kinds of volume and problem an audit finds."""
    rnd = random.Random(2026)
    c = MockCloud(**kw)
    (src, src_kind), (tgt, tgt_kind) = spec["backends"]
    names = ["project-a", "project-b", "project-c", "project-d", "project-e"]
    pids = ["%032x" % (0xa0000000 + i) for i in range(5)]
    c.projects = [{"id": p, "name": n, "enabled": True} for p, n in zip(pids, names)]
    c.projects[4]["enabled"] = False
    gone_project = "%032x" % 0xdead0001  # a project that no longer exists

    c.types = [
        {"id": "t-fast", "name": "fast", "is_public": True, "description": "Primary tier",
         "extra_specs": {"volume_backend_name": src}, "qos_specs_id": "q-1"},
        {"id": "t-flash", "name": "flash", "is_public": True, "description": "Destination tier",
         "extra_specs": {"volume_backend_name": tgt}, "qos_specs_id": None},
        {"id": "t-enc", "name": "encrypted", "is_public": False, "description": "Encrypted volumes",
         "extra_specs": {"volume_backend_name": src}, "qos_specs_id": None},
    ]
    c.qos = [{"id": "q-1", "name": "gold", "consumer": "front-end", "specs": {"total_iops_sec": "5000"}}]

    hosts = ["cmp01", "cmp02", "cmp03", "cmp04"]
    states = ["ACTIVE"] * 14 + ["SHUTOFF"] * 3 + ["PAUSED"] + ["ERROR"]
    c.servers = [{"id": _uid("5e5e5e5e", n), "name": "instance-%02d" % n, "status": st,
                  "OS-EXT-STS:task_state": "rebooting" if n == 5 else None, "locked": False,
                  "OS-EXT-SRV-ATTR:host": hosts[n % 4], "tenant_id": pids[n % 4],
                  "image": "" if n in (0, 1, 2) else {"id": "img-1"}} for n, st in enumerate(states)]

    vols, snaps = [], []

    def add(n, size, status="available", backend=src, project=None, vtype="fast", snaps_n=0, attach=None,
            device="/dev/vdb", age_h=900, updated_h=None, **extra):
        vid = _uid("a1a1a1a1", n)
        att = [{"server_id": attach, "device": device, "host_name": hosts[n % 4]}] if attach is not None else []
        v = volume(vid, status=status, size=size, backend=backend, attachments=att, volume_type=vtype,
                   **{"os-vol-tenant-attr:tenant_id": project or pids[n % 4]}, **extra)
        v["name"] = "data-%03d" % n
        v["created_at"] = iso(age_h)
        v["updated_at"] = iso(updated_h if updated_h is not None else min(age_h, 300))
        if device == "/dev/vda" and attach is not None:
            v["bootable"] = "true"
        vols.append(v)
        for k in range(snaps_n):
            snaps.append(snapshot(_uid("b2b2b2b2", len(snaps) + 1), vid, age_h - 20 - k * (age_h // max(snaps_n, 1)), size=size))
        return vid

    n = 1
    sizes = [20, 50, 100, 200, 500, 75, 150, 300, 40, 10]
    for k in range(14):  # no snapshots: some detached, some attached to running instances
        server = None if k % 3 else _uid("5e5e5e5e", 3 + k % 10)
        add(n, sizes[k % 10], status="in-use" if server else "available", attach=server, age_h=1500 - k * 40)
        n += 1
    for k in range(3):  # root volumes of boot-from-volume instances: a legacy Windows server, a Windows server without the guest agent, a Linux server
        add(n, 40, status="in-use", attach=_uid("5e5e5e5e", k), device="/dev/vda", age_h=2200,
            volume_image_metadata=[IMG_WIN_LEGACY, IMG_WIN_NO_AGENT, IMG_LINUX][k])
        n += 1
    for k in range(6):  # a few snapshots
        add(n, sizes[(k + 3) % 10], snaps_n=2 + k % 4, age_h=3000 - k * 100,
            attach=_uid("5e5e5e5e", 6 + k) if k % 2 else None, status="in-use" if k % 2 else "available")
        n += 1
    add(n, 400, snaps_n=32, age_h=4000)  # snapshot-heavy, at the array's limit
    n += 1
    for k in range(3):  # attached to instances that are not running
        add(n, 60 + k * 20, status="in-use", attach=_uid("5e5e5e5e", 14 + k), age_h=1800)
        n += 1
    add(n, 80, status="in-use", attach=_uid("5e5e5e5e", 17), age_h=1700)  # paused instance
    n += 1
    add(n, 100, vtype="encrypted", encrypted=True)  # not handled by the method
    n += 1
    add(n, 100, vtype="encrypted", encrypted=True, project=pids[1])
    n += 1
    add(n, 120, multiattach=True)
    n += 1
    add(n, 90, replication_status="enabled")
    n += 1
    add(n, 30, status="error", updated_h=2000)
    n += 1
    add(n, 55, status="deleting", updated_h=720)  # problems an audit should find
    n += 1
    add(n, 65, status="detaching", updated_h=96)
    n += 1
    add(n, 25, vtype=None)
    vols[-1]["volume_type"] = None
    n += 1
    add(n, 35, status="in-use")  # in use, no attachment record
    n += 1
    add(n, 45, status="available", attach=_uid("5e5e5e5e", 3))  # available but has an attachment record
    n += 1
    add(n, 70, status="in-use", attach=_uid("5e5e5e5e", 900))  # attached to an instance that does not exist
    n += 1
    add(n, 15, project=gone_project)  # project no longer exists
    n += 1
    for k in range(4):  # already on the target back-end
        add(n, 100 + k * 50, backend=tgt, vtype="flash", age_h=300)
        n += 1
    rotating = set()
    for k in range(4):  # daily snapshots that rotate (class B once the database export shows the history)
        rotating.add(add(n, 60 + 20 * k, snaps_n=3, age_h=200))
        n += 1
    c.volumes = vols
    c.snapshots = snaps + [
        snapshot(_uid("b2b2b2b2", 900), _uid("a1a1a1a1", 9999), 5000, size=50),  # volume is gone
        snapshot(_uid("b2b2b2b2", 901), _uid("a1a1a1a1", 9999), 4000, size=50),
        snapshot(_uid("b2b2b2b2", 902), _uid("a1a1a1a1", 3), 900, size=10, status="error"),
        snapshot(_uid("b2b2b2b2", 903), _uid("a1a1a1a1", 4), 800, size=10, status="deleting")]
    c.backups = [{"id": _uid("c3c3c3c3", k), "volume_id": _uid("a1a1a1a1", k + 1), "status": "available" if k < 4 else "error",
                  "size": 50, "created_at": iso(100 + k * 50), "updated_at": iso(100 + k * 50)} for k in range(5)]
    c.pools = [_pool("ctl01", src, src_kind, 20000, 6400, 31000, "20.0", 5),
               _pool("ctl01", tgt, tgt_kind, 50000, 41000, 9000, "5.0")]
    c.services = [_svc("cinder-scheduler", "ctl01"), _svc("cinder-volume", "ctl01@%s" % src), _svc("cinder-volume", "ctl01@%s" % tgt),
                  _svc("cinder-volume", "ctl02@%s" % tgt, state="down", hours=300),
                  _svc("cinder-backup", "ctl01", status="disabled", state="down", hours=2000, reason="maintenance")]
    c.hypervisors = [{"hypervisor_hostname": h, "hypervisor_type": "QEMU", "hypervisor_version": 2011000 + (0 if i < 3 else 1000),
                      "state": "up", "status": "enabled" if i != 3 else "disabled", "running_vms": 5 - i} for i, h in enumerate(hosts)]
    c.meta = {"solidfire": {src} if src_kind == "solidfire" else set(), "bad_provider": {_uid("a1a1a1a1", 30)}, "rotating": rotating,
              "roots": {v["id"] for v in vols if v["attachments"] and v["attachments"][0]["device"] == "/dev/vda"}}
    return c


# ---- the enterprise scenario -------------------------------------------------------------------------

PROJECTS = ["finance-erp", "hr-portal", "ecommerce-prod", "ecommerce-test", "data-platform", "analytics", "ci-cd",
            "monitoring", "db-oracle", "db-postgres", "vdi", "backup-infra", "shared-services", "decommissioned-lab"]


def _enterprise(spec, **kw):
    rnd = random.Random(7)
    c = MockCloud(**kw)
    pids = ["%032x" % (0xb0000000 + i) for i in range(len(PROJECTS))]
    pname = dict(zip(pids, PROJECTS))
    c.projects = [{"id": p, "name": n, "enabled": n != "decommissioned-lab"} for p, n in zip(pids, PROJECTS)]
    gone_project = "%032x" % 0xdead0002
    live = pids[:-1]
    heavy = [pids[0], pids[2], pids[4], pids[8], pids[9], pids[10]]  # the big consumers

    c.types = [
        {"id": "t-gold", "name": "gold", "is_public": True, "description": "SolidFire, guaranteed IOPS",
         "extra_specs": {"volume_backend_name": "sf-prod"}, "qos_specs_id": "q-gold"},
        {"id": "t-silver", "name": "silver", "is_public": True, "description": "SolidFire bulk",
         "extra_specs": {"volume_backend_name": "sf-bulk"}, "qos_specs_id": "q-silver"},
        {"id": "t-flash", "name": "flash", "is_public": True, "description": "Pure Storage production",
         "extra_specs": {"volume_backend_name": "pure-prod"}, "qos_specs_id": "q-plat"},
        {"id": "t-dr", "name": "flash-dr", "is_public": False, "description": "Pure Storage DR site",
         "extra_specs": {"volume_backend_name": "pure-dr"}, "qos_specs_id": None},
        {"id": "t-ontap", "name": "ontap-std", "is_public": True, "description": "NetApp ONTAP",
         "extra_specs": {"volume_backend_name": "ontap-prod"}, "qos_specs_id": None},
        {"id": "t-ceph", "name": "ceph", "is_public": True, "description": "Ceph RBD",
         "extra_specs": {"volume_backend_name": "ceph-rbd"}, "qos_specs_id": None},
        {"id": "t-enc", "name": "encrypted", "is_public": False, "description": "Encrypted volumes",
         "extra_specs": {"volume_backend_name": "sf-prod", "encryption": "luks"}, "qos_specs_id": None},
        {"id": "t-shared", "name": "shared-cluster", "is_public": False, "description": "Multi-attach for clusters",
         "extra_specs": {"volume_backend_name": "sf-prod", "multiattach": "<is> True"}, "qos_specs_id": None},
        {"id": "t-repl", "name": "replicated", "is_public": True, "description": "Replicated to the second site",
         "extra_specs": {"volume_backend_name": "sf-prod", "replication_enabled": "<is> True"}, "qos_specs_id": "q-gold"},
        {"id": "t-old", "name": "legacy-lvm", "is_public": True, "description": "Points at a back-end that no longer exists",
         "extra_specs": {"volume_backend_name": "lvm-old"}, "qos_specs_id": None},
    ]
    c.qos = [{"id": "q-gold", "name": "gold", "consumer": "front-end", "specs": {"total_iops_sec": "8000"}},
             {"id": "q-silver", "name": "silver", "consumer": "front-end", "specs": {"total_iops_sec": "2000"}},
             {"id": "q-plat", "name": "platinum", "consumer": "front-end", "specs": {"total_iops_sec": "20000"}}]

    # ---- instances: 100, spread over ten compute hosts ----
    hosts = ["cmp%02d" % i for i in range(1, 11)]
    plan = ["ACTIVE"] * 82 + ["SHUTOFF"] * 8 + ["PAUSED"] * 3 + ["SUSPENDED"] * 2 + ["ERROR"] * 3 + ["REBUILD"] * 2
    servers = []
    for n, st in enumerate(plan):
        servers.append({"id": _uid("5e5e5e5e", n), "name": "%s-%02d" % (rnd.choice(["erp-app", "web", "api", "db", "etl", "ci-runner", "vdi"]), n),
                        "status": st, "OS-EXT-STS:task_state": "rebuilding" if st == "REBUILD" else ("rebooting" if n in (11, 40) else None),
                        "locked": n in (7, 63), "OS-EXT-SRV-ATTR:host": hosts[n % 10], "tenant_id": live[n % len(live)],
                        "image": "" if n < 24 else {"id": "img-1"}})
    c.servers = servers
    by_state = lambda st: [s["id"] for s in servers if s["status"] == st]
    active, bad_state = by_state("ACTIVE"), by_state("SHUTOFF") + by_state("SUSPENDED") + by_state("ERROR")
    paused = by_state("PAUSED")

    vols, snaps = [], []
    meta = {"solidfire": {"sf-prod", "sf-bulk"}, "bad_provider": set(), "rotating": set(), "roots": set()}
    counter = {"n": 0, "s": 0}

    def add(backend="sf-prod", vtype="gold", size=None, status="available", project=None, attach=(), device="/dev/vdb",
            age_h=None, updated_h=None, snap_ages=(), snap_size=None, root=False, tag="data", **extra):
        counter["n"] += 1
        n = counter["n"]
        size = size or rnd.choice([10, 20, 40, 50, 75, 100, 150, 200, 300, 500, 800])
        pid = project or rnd.choice(heavy if rnd.random() < 0.6 else live)
        age = age_h if age_h is not None else rnd.randint(100, 5200)
        vid = _uid("a1a1a1a1", n)
        att = [{"server_id": s, "device": device, "host_name": hosts[int(s.split("-")[1], 16) % 10] if s.startswith("5e5e5e5e") else None}
               for s in attach]
        v = volume(vid, status=status, size=size, backend=backend, attachments=att, volume_type=vtype,
                   **{"os-vol-tenant-attr:tenant_id": pid}, **extra)
        v["os-vol-host-attr:host"] = "%s@%s#pool" % ("ctl01" if backend in ("sf-prod", "pure-prod", "ceph-rbd") else
                                                    "ctl02" if backend in ("sf-bulk",) else "ctl03", backend)
        v["name"] = "%s-%s-%03d" % (pname.get(pid, "orphan"), tag, n)
        v["created_at"] = iso(age)
        v["updated_at"] = iso(updated_h if updated_h is not None else min(age, 250))
        if root:
            v["bootable"] = "true"
            meta["roots"].add(vid)
        vols.append(v)
        for h in snap_ages:
            counter["s"] += 1
            snaps.append(snapshot(_uid("b2b2b2b2", counter["s"]), vid, h, size=snap_size or size))
        return vid

    pick = lambda pool: rnd.choice(pool)

    # class A: no snapshots, detached
    for _ in range(60):
        add(vtype=rnd.choice(["gold", "gold", "silver"]), backend="sf-prod", tag="data")
    # class A: attached to running instances (14 are boot volumes)
    imgs = [IMG_WIN_LEGACY, IMG_WIN_NO_AGENT, IMG_WIN_OK, IMG_LINUX, IMG_LINUX]
    for k in range(48):
        root = k < 14
        extra = {"volume_image_metadata": imgs[k % 5]} if root else {}
        add(status="in-use", attach=[active[(k * 3) % len(active)] if not root else active[k]], device="/dev/vda" if root else "/dev/vdb",
            root=root, tag="boot" if root else "data", size=40 if root else None, age_h=rnd.randint(300, 4000), **extra)
    for k in range(3):  # databases (guessed from the name)
        add(vtype="gold", backend="sf-prod", tag="oracle-db", size=500)
    # class B: snapshots that rotate (daily snapshots, 3 to 7 kept) - needs the database export to be recognised
    for k in range(24):
        n_snaps = 3 + k % 5
        vid = add(status="in-use" if k % 3 == 0 else "available", attach=[pick(active)] if k % 3 == 0 else (),
                  snap_ages=[12 + 24 * j for j in range(n_snaps)], tag="rot")
        meta["rotating"].add(vid)
    # class C: long-lived snapshots
    for k in range(18):
        add(snap_ages=[rnd.randint(400, 6500) for _ in range(2 + k % 7)], tag="keep",
            status="in-use" if k % 4 == 0 else "available", attach=[pick(active)] if k % 4 == 0 else ())
    for k in range(2):  # at the array's snapshot limit
        add(size=400, snap_ages=[300 + 90 * j for j in range(32)], tag="snapheavy")
    for k in range(3):  # extended after the snapshots were taken
        add(size=200, snap_ages=[800, 600], snap_size=100, tag="extended")
    # class D: attached to instances that are not running
    for k in range(8):
        add(status="in-use", attach=[bad_state[k % len(bad_state)]], tag="stopped")
    for k in range(3):
        add(status="in-use", attach=[by_state("SHUTOFF")[k]], device="/dev/vda", root=True, size=40, tag="boot-stopped")
    add(status="in-use", attach=[paused[0]], tag="paused")  # paused is allowed
    add(status="in-use", attach=[by_state("REBUILD")[0]], tag="rebuilding")  # an instance with a task in progress
    # class E: not handled by the method
    for k in range(6):
        add(vtype="encrypted", encrypted=True, tag="enc", status="in-use" if k % 2 else "available",
            attach=[pick(active)] if k % 2 else ())
    for k in range(5):
        add(vtype="shared-cluster", multiattach=True, status="in-use", attach=[active[10 + k], active[20 + k]], tag="shared")
    for k in range(4):
        add(group_id="grp-%02d" % (k // 2 + 1), tag="grp")
    for k in range(5):
        add(vtype="replicated", replication_status="enabled", tag="repl")
    for k in range(4):
        add(status="error", updated_h=3000 + 100 * k, tag="err")
    add(status="error_deleting", updated_h=1500, tag="err")
    add(status="extending", updated_h=200, tag="stuck")
    for k in range(2):
        add(status="in-use", attach=[pick(active)], **{"os-vol-mig-status-attr:migstat": "migrating"}, tag="mig")
    add(**{"os-vol-mig-status-attr:migstat": "error"}, tag="mig-failed")  # failed migration: allowed, but a finding
    for k in range(3):  # provider_id is malformed in the database (only the database export shows it)
        meta["bad_provider"].add(add(tag="badprov"))
    # problems the audit finds
    for k in range(2):
        add(status="deleting", updated_h=720 + 200 * k, tag="stuck")
    for k in range(2):
        add(status="detaching", updated_h=96 + 40 * k, tag="stuck")
    for k in range(3):
        add(vtype=None, tag="notype")
        vols[-1]["volume_type"] = None
    for k in range(3):
        add(status="in-use", tag="noattach")  # in use but no attachment record
    for k in range(2):
        add(status="available", attach=[pick(active)], tag="ghostattach")
    for k in range(3):
        add(status="in-use", attach=[_uid("5e5e5e5e", 900 + k)], tag="lostserver")  # attached to an instance that does not exist
    for k in range(4):
        add(project=gone_project, tag="orphanproj")
    add(project=pids[-1], tag="lab")  # the decommissioned project
    add(project=pids[-1], size=500, snap_ages=[3000, 2500], tag="lab")
    for k in range(3):
        add(tag="bootable-unused", **{"bootable": "true"})  # bootable volumes nobody uses
    # volumes already on the other back-ends
    for k in range(40):
        add(backend="sf-bulk", vtype="silver", status="in-use" if k % 4 == 0 else "available",
            attach=[pick(active)] if k % 4 == 0 else (), snap_ages=[rnd.randint(50, 3000) for _ in range(k % 4)], tag="bulk")
    for k in range(25):
        add(backend="pure-prod", vtype="flash", age_h=rnd.randint(50, 600), tag="flash")
    for k in range(6):
        add(backend="pure-dr", vtype="flash-dr", size=150, age_h=rnd.randint(50, 400), tag="dr")
    for k in range(15):
        add(backend="ontap-prod", vtype="ontap-std", age_h=rnd.randint(50, 900), tag="ontap")
    for k in range(30):
        add(backend="ceph-rbd", vtype="ceph", age_h=rnd.randint(50, 2000), tag="ceph")
    c.volumes = vols

    orphan_snaps = [snapshot(_uid("b2b2b2b2", 5000 + k), _uid("a1a1a1a1", 9000 + k), 2000 + k * 300, size=50) for k in range(8)]
    odd_snaps = [snapshot(_uid("b2b2b2b2", 5100), vols[70]["id"], 900, size=vols[70]["size"], status="error"),
                 snapshot(_uid("b2b2b2b2", 5101), vols[75]["id"], 700, size=vols[75]["size"], status="error"),
                 snapshot(_uid("b2b2b2b2", 5102), vols[80]["id"], 800, size=vols[80]["size"], status="error"),
                 snapshot(_uid("b2b2b2b2", 5103), vols[85]["id"], 600, size=vols[85]["size"], status="deleting"),
                 snapshot(_uid("b2b2b2b2", 5104), vols[90]["id"], 1200, size=vols[90]["size"], status="deleting")]
    c.snapshots = snaps + orphan_snaps + odd_snaps
    c.meta = meta
    c.backups = [{"id": _uid("c3c3c3c3", k), "volume_id": vols[k * 3]["id"], "status": "error" if k in (3, 11) else "available",
                  "size": vols[k * 3]["size"], "created_at": iso(100 + k * 160), "updated_at": iso(100 + k * 160)} for k in range(20)]

    total = lambda backend: sum(v["size"] for v in vols if v["os-vol-host-attr:host"].split("@")[1].startswith(backend))
    c.pools = [
        _pool("ctl01", "sf-prod", "solidfire", 120000, 31000, max(total("sf-prod") * 22, 410000), "20.0", 5),
        _pool("ctl02", "sf-bulk", "solidfire", 80000, 4000, 2100000, "20.0", 5),   # nearly full and heavily over-subscribed
        _pool("ctl01", "pure-prod", "pure", 200000, 150000, max(total("pure-prod"), 60000), "5.0"),
        _pool("ctl03", "pure-dr", "pure", 20000, 1500, 18000, "2.0"),               # too small to take a large migration
        _pool("ctl03", "ontap-prod", "ontap", 150000, 120000, max(total("ontap-prod"), 30000), "3.0"),
        _pool("ctl01", "ceph-rbd", "ceph", 300000, 180000, 140000, "3.0")]
    c.services = [
        _svc("cinder-scheduler", "ctl01"), _svc("cinder-scheduler", "ctl02"), _svc("cinder-scheduler", "ctl03"),
        _svc("cinder-volume", "ctl01@sf-prod"), _svc("cinder-volume", "ctl02@sf-prod"),
        _svc("cinder-volume", "ctl02@sf-bulk"),
        _svc("cinder-volume", "ctl01@pure-prod"), _svc("cinder-volume", "ctl02@pure-prod"),   # same hosts as sf-prod: single-host copy
        _svc("cinder-volume", "ctl03@pure-dr"),                                               # a different host: relay copy
        _svc("cinder-volume", "ctl03@ontap-prod"), _svc("cinder-volume", "ctl02@ontap-prod", state="down", hours=340),
        _svc("cinder-volume", "ctl01@ceph-rbd"), _svc("cinder-volume", "ctl02@ceph-rbd"), _svc("cinder-volume", "ctl03@ceph-rbd"),
        _svc("cinder-backup", "ctl01"), _svc("cinder-backup", "ctl02", status="disabled", state="down", hours=1500, reason="maintenance")]
    running = {h: sum(1 for s in servers if s["OS-EXT-SRV-ATTR:host"] == h and s["status"] in ("ACTIVE", "PAUSED")) for h in hosts}
    c.hypervisors = [
        {"hypervisor_hostname": h, "hypervisor_type": "QEMU", "hypervisor_version": 2011000 if i < 8 else 2012000,
         "state": "down" if h == "cmp10" else "up", "status": "disabled" if h == "cmp09" else "enabled",
         "running_vms": running[h]} for i, h in enumerate(hosts)]
    return c
