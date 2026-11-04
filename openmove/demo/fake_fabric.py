# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""A stand-in for the migration host and the Cinder host, in memory, for the automated tests and for demo mode.

FakeMover answers the data mover's verbs (resources/mover/mover.py) over in-memory logical volumes, so the real LvmAdapter and the real
engine run on top of it. FakeHelper does what the Cinder helper does to the records of a MockCloud. Together they are a FakeFabric.

The "content" of a device is one short token instead of bytes. A copy sets the destination's token to the source's, a snapshot freezes
the token of its origin, a hash is the hash of the token, a compare is an equality test. That is enough to prove the order of the method,
the checks and the rollbacks. It proves nothing about real devices, real Cinder or real arrays: that is the lab.
"""
import copy
import hashlib

from ..arrays import adapter_for
from ..connectors.ssh import SshError

SRC_VG, DST_VG = "stack-vg-sf-1", "stack-vg-pure-1"
GIB = 1024 ** 3


def sha(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


class FakeMover:
    """The verbs of the data mover over in-memory logical volumes. Failure injection: fail_jobs, corrupt, lose_answer, refuse."""

    def __init__(self, vgs=(SRC_VG, DST_VG)):
        self.lv = {vg: {} for vg in vgs}
        self.content = {}
        self.jobs = {}
        self.job_polls = 2
        self.fail_jobs = set()          # a job whose id contains one of these fails
        self.corrupt = set()            # a copy job whose id contains one of these writes a different image than it read
        self.lose_answer = set()        # verbs whose next answer is lost after the work is done (outcome unknown)
        self.refuse = {}                # verb -> message: the verb fails for certain
        self.calls = []
        self.spec = {"name": "fake-mover", "roles": ["mover", "cinder"]}
        self.name, self.local = "fake-mover", True

    # ---- helpers for the tests -----------------------------------------------------------------------------------------------
    def add(self, vg, name, nbytes, token, origin="", tags=("openmove",)):
        self.lv[vg][name] = {"name": name, "vg": vg, "bytes": int(nbytes), "attr": "-wi-a-----", "origin": origin, "tags": list(tags), "path": "/dev/%s/%s" % (vg, name)}
        self.content[(vg, name)] = token
        return self.lv[vg][name]

    def where(self, path):
        _, _, vg, name = path.split("/")
        return vg, name

    def token(self, path):
        return self.content[self.where(path)]

    def names(self, vg):
        return sorted(self.lv[vg])

    # ---- the verbs -------------------------------------------------------------------------------------------------------------
    def mover(self, verb, args=None, timeout=None):
        args = dict(args or {})
        self.calls.append((verb, args))
        if verb in self.refuse:
            raise SshError("%s refused: %s" % (verb, self.refuse[verb]))
        res = getattr(self, "v_" + verb.replace(".", "_"))(args)
        if verb in self.lose_answer:
            self.lose_answer.discard(verb)
            raise SshError("the connection dropped while %s was running" % verb, outcome_unknown=True)
        return res

    def v_probe(self, a):
        return {"version": "1", "python": "3.12.3", "platform": "linux", "user": "root", "euid": 0, "home": "/root/.openmove-mover", "time": "now",
                "tools": {t: True for t in ("lvm", "lvs", "lvcreate", "lvremove", "lvrename", "blockdev", "iscsiadm", "multipath", "sha256sum")},
                "free_bytes": 10 ** 12, "volume_groups": sorted(self.lv)}

    def v_lv_list(self, a):
        return {"lvs": [copy.deepcopy(r) for r in self.lv[a["vg"]].values()]}

    def v_lv_create(self, a):
        vg, name, size = a["vg"], a["name"], int(a["bytes"])
        have = self.lv[vg].get(name)
        if have:
            if "openmove" in have["tags"] and have["bytes"] >= size:
                return {"lv": copy.deepcopy(have), "existed": True}
            raise SshError("%s/%s already exists" % (vg, name))
        return {"lv": copy.deepcopy(self.add(vg, name, size, "zero")), "existed": False}

    def v_lv_snapshot(self, a):
        vg, origin, name, size = a["vg"], a["origin"], a["name"], int(a["bytes"])
        have = self.lv[vg].get(name)
        if have:
            if "openmove" in have["tags"] and have["origin"] == origin:
                return {"lv": copy.deepcopy(have), "existed": True}
            raise SshError("%s/%s already exists" % (vg, name))
        o = self.lv[vg].get(origin)
        if o is None or "openmove" not in o["tags"]:
            raise SshError("%s/%s was not created by OpenMove (no 'openmove' tag): it is not touched" % (vg, origin))
        return {"lv": copy.deepcopy(self.add(vg, name, size, self.content[(vg, origin)], origin=origin)), "existed": False}

    def v_array_list(self, a):
        return {"arrays": []}

    def v_lv_extend(self, a):
        vg, name, size = a["vg"], a["name"], int(a["bytes"])
        r = self.lv[vg].get(name)
        if r is None or "openmove" not in r["tags"]:
            raise SshError("%s/%s was not created by OpenMove (no 'openmove' tag): it is not touched" % (vg, name))
        if size < r["bytes"]:
            raise SshError("%s/%s has %d bytes; it is never made smaller (asked for %d)" % (vg, name, r["bytes"], size))
        existed = size == r["bytes"]
        r["bytes"] = size
        return {"lv": copy.deepcopy(r), "existed": existed}

    def v_lv_rename(self, a):
        vg, old, new = a["vg"], a["old"], a["new"]
        if new in self.lv[vg]:
            if old not in self.lv[vg] and "openmove" in self.lv[vg][new]["tags"]:
                return {"lv": copy.deepcopy(self.lv[vg][new]), "existed": True}
            raise SshError("%s/%s already exists" % (vg, new))
        r = self.lv[vg].get(old)
        if r is None or "openmove" not in r["tags"]:
            raise SshError("%s/%s was not created by OpenMove (no 'openmove' tag): it is not touched" % (vg, old))
        del self.lv[vg][old]
        r.update(name=new, path="/dev/%s/%s" % (vg, new))
        self.lv[vg][new] = r
        self.content[(vg, new)] = self.content.pop((vg, old))
        for s in self.lv[vg].values():
            if s["origin"] == old:
                s["origin"] = new
        return {"lv": copy.deepcopy(r), "existed": False}

    def v_lv_remove(self, a):
        vg, name = a["vg"], a["name"]
        r = self.lv[vg].get(name)
        if r is None:
            return {"removed": False, "absent": True}
        if "openmove" not in r["tags"]:
            raise SshError("%s/%s was not created by OpenMove (no 'openmove' tag): it is not touched" % (vg, name))
        for s in list(self.lv[vg].values()):
            if s["origin"] == name:
                if "openmove" not in s["tags"]:
                    raise SshError("%s/%s has a snapshot %s that OpenMove did not create; it is not removed" % (vg, name, s["name"]))
                del self.lv[vg][s["name"]]
        del self.lv[vg][name]
        self.content.pop((vg, name), None)
        return {"removed": True}

    def v_lv_remove_source_snapshots(self, a):
        vg, origin, removed = a["vg"], a["origin"], []
        for n in a["names"]:
            s = self.lv[vg].get(n)
            if s:
                if s["origin"] != origin:
                    raise SshError("%s is not a snapshot of %s; it is not removed" % (n, origin))
                del self.lv[vg][n]
                removed.append(n)
        return {"removed": removed}

    def v_vg_free(self, a):
        return {"vg": a["vg"], "bytes": 10 ** 13, "free_bytes": getattr(self, "free", 10 ** 13)}

    def v_diag(self, a):
        return {"fake": True, "lvs": {vg: sorted(d) for vg, d in self.lv.items()}, "jobs": {k: v["state"] for k, v in self.jobs.items()}}

    def v_cleanup(self, a):
        return {"removed_jobs": 0}

    def v_bench(self, a):
        return {"bytes": a.get("bytes") or 0, "seconds": 0.1, "mib_per_s": 640.0}

    # ---- jobs ------------------------------------------------------------------------------------------------------------------
    def v_job_start(self, a):
        jid, verb, args = a["job"], a["verb"], a["args"]
        if jid in self.jobs and self.jobs[jid]["state"] in ("running", "done"):
            return {"job": self.status(jid), "existed": True}
        for p in (args.get("src"), args.get("dev"), args.get("a"), args.get("b"), args.get("dst")):
            if p and self.where(p) not in self.content:
                raise SshError("%s does not exist" % p)
        if verb == "copy" and "openmove" not in self.lv[self.where(args["dst"])[0]][self.where(args["dst"])[1]]["tags"]:
            raise SshError("%s is not a device OpenMove created: it is never written" % args["dst"])
        self.jobs[jid] = {"job": jid, "verb": verb, "args": args, "state": "running", "left": self.job_polls, "done": 0, "total": 1, "result": None, "error": "", "pid": 4242}
        return {"job": self.status(jid, tick=False), "existed": False}

    def status(self, jid, tick=True):
        j = self.jobs.get(jid)
        if j is None:
            raise SshError("no job %s" % jid)
        if j["state"] == "running" and tick:
            j["left"] -= 1
            j["done"] = j["total"] // 2
            if j["left"] <= 0:
                self.finish(j)
        return {k: j[k] for k in ("job", "verb", "state", "done", "total", "result", "error", "pid")}

    def finish(self, j):
        if any(x in j["job"] for x in self.fail_jobs):
            j.update(state="failed", error="Input/output error while reading the device (injected by the test)")
            return
        a = j["args"]
        j["done"] = j["total"]
        if j["verb"] == "copy":
            tok = self.token(a["src"])
            if any(x in j["job"] for x in self.corrupt):
                self.content[self.where(a["dst"])] = tok + "~corrupt"
            else:
                self.content[self.where(a["dst"])] = tok
            j.update(state="done", result={"bytes": a["bytes"], "written": a["bytes"], "source_sha256": sha(tok), "seconds": 0.1})
        elif j["verb"] == "hash":
            j.update(state="done", result={"bytes": a["bytes"], "sha256": sha(self.token(a["dev"])), "seconds": 0.1})
        else:
            same = self.token(a["a"]) == self.token(a["b"])
            j.update(state="done", result={"bytes": a["bytes"], "identical": same, "first_difference": None if same else 0, "different_chunks": 0 if same else 1, "seconds": 0.1})

    def v_job_status(self, a):
        return {"job": self.status(a["job"])}

    def v_job_cancel(self, a):
        j = self.jobs.get(a["job"])
        if j and j["state"] == "running":
            j["state"] = "cancelled"
        return {"job": self.status(a["job"], tick=False)}

    def kill_jobs(self):
        """The mover host lost its job processes (a reboot, a kill): every running job is dead."""
        for j in self.jobs.values():
            if j["state"] == "running":
                j.update(state="dead", error="the job process is gone before it finished; start the job again to continue")


class FakeHelper:
    """What the Cinder helper does to a MockCloud's volume list."""

    def __init__(self, cloud):
        self.cloud = cloud
        self.refuse = {}
        self.lose_answer = set()
        self.calls = []
        self.n = 0

    def vol(self, vid):
        v = next((x for x in self.cloud.volumes if x["id"] == vid), None)
        if v is None:
            raise SshError("no volume %s" % vid)
        return v

    @staticmethod
    def fields(v):
        return {"id": v["id"], "project_id": v.get("os-vol-tenant-attr:tenant_id"), "user_id": v.get("user_id"), "display_name": v.get("name"),
                "display_description": v.get("description"), "size": v["size"], "bootable": v.get("bootable"), "created_at": v.get("created_at"),
                "snapshot_id": v.get("snapshot_id"), "source_volid": v.get("source_volid"), "availability_zone": v.get("availability_zone"),
                "encryption_key_id": None, "multiattach": v.get("multiattach"), "status": v["status"], "migration_status": v.get("os-vol-mig-status-attr:migstat"),
                "previous_status": v.get("previous_status"), "host": v.get("os-vol-host-attr:host"), "volume_type": v.get("volume_type")}

    def __call__(self, verb, args=None, timeout=None):
        args = dict(args or {})
        self.calls.append((verb, args))
        if verb in self.refuse:
            raise SshError("%s refused: %s" % (verb, self.refuse[verb]))
        res = getattr(self, "v_" + verb.replace("-", "_"))(args)
        if verb in self.lose_answer:
            self.lose_answer.discard(verb)
            raise SshError("the connection dropped while %s was running" % verb, outcome_unknown=True)
        return res

    def v_show(self, a):
        return {"volume": self.fields(self.vol(a["volume"]))}

    def v_record_create(self, a):
        src = self.vol(a["src"])
        for v in self.cloud.volumes:
            if v.get("os-vol-mig-status-attr:migstat") == "target:%s" % src["id"]:
                return {"new": v["id"], "existed": True}
        if src["status"] not in ("retyping", "maintenance"):
            raise SshError("the source volume is %s; it must be frozen first" % src["status"])
        self.n += 1
        dtype = next((t["name"] for t in self.cloud.types if t["id"] == a["dest_type"]), None)
        rec = copy.deepcopy(src)
        rec.update(id="dddddddd-0000-0000-0000-%012d" % self.n, status="available", attachments=[], volume_type=dtype, name=src["name"])
        rec["os-vol-host-attr:host"] = a["dest_host"]
        rec["os-vol-mig-status-attr:migstat"] = "target:%s" % src["id"]
        rec["os-vol-mig-status-attr:name_id"] = None
        self.cloud.volumes.append(rec)
        return {"new": rec["id"], "existed": False}

    def v_record_delete(self, a):
        v = self.vol(a["new"])
        if not str(v.get("os-vol-mig-status-attr:migstat") or "").startswith("target:"):
            raise SshError("%s is not a destination record; it is not deleted" % a["new"])
        self.cloud.volumes.remove(v)
        return {"deleted": a["new"]}

    def v_freeze(self, a):
        v = self.vol(a["volume"])
        before = {"status": v["status"], "previous_status": v.get("previous_status"), "migration_status": v.get("os-vol-mig-status-attr:migstat")}
        if v["status"] == a["status"] and v.get("os-vol-mig-status-attr:migstat") == "migrating":
            return {"before": before, "already": True}
        if v["status"] != a["previous_status"] or v.get("os-vol-mig-status-attr:migstat") not in (None, "success", "error"):
            raise SshError("the volume is %s / %s; it was not frozen" % (v["status"], v.get("os-vol-mig-status-attr:migstat")))
        v.update(status=a["status"], previous_status=a["previous_status"])
        v["os-vol-mig-status-attr:migstat"] = "migrating"
        return {"before": before}

    def v_unfreeze(self, a):
        v = self.vol(a["volume"])
        v["status"], v["previous_status"] = a["status"], a.get("previous_status")
        v["os-vol-mig-status-attr:migstat"] = a.get("migration_status")
        return {"restored": True}

    def v_sync_fields(self, a):
        return {"synced": True}

    def v_find_host(self, a):
        return {"hosts": ["ctl@%s#pool" % a["backend"]]}

    def v_conf(self, a):
        return {"options": {"DEFAULT.volume_name_template": "volume-%s", "DEFAULT.snapshot_name_template": "snapshot-%s"}}

    def v_diag(self, a):
        return {"fake": True}


class FakeFabric:
    """The fabric the engine uses (hosts.Fabric), over a FakeMover and a FakeHelper."""

    def __init__(self, cloud, source="sf-1", target="pure-1", mover=None):
        self.mover_host = self.cinder_host = mover or FakeMover()
        self.helper = FakeHelper(cloud)
        self.cloud, self.family, self.source, self.target, self.home, self.settings = cloud, "lvm", source, target, None, {}
        self.adapter = adapter_for("lvm", self.mover_host, {"vgs": {source: SRC_VG, target: DST_VG}})
        self.is_ready, self.why = True, ""
        cloud.on_swap_complete = self.swap_copy
        cloud.on_force_delete = self.driver_delete

    @property
    def mover(self):
        return self.mover_host

    def rate(self):
        return None

    def check(self, home=None):
        return [{"id": "H1", "title": "Reached", "status": "pass" if self.is_ready else "fail", "detail": "fake", "hint": ""}]

    def ready(self):
        return self.is_ready, self.why

    def hosts(self):
        return [self.mover_host]

    @staticmethod
    def vg_of_host(host):
        return SRC_VG if "sf-1" in str(host) else DST_VG

    def swap_copy(self, vid, dest_id):
        """Nova's block copy during a volume swap: the destination gets the content the source has now (in either direction)."""
        src, dest = self.cloud.find(self.cloud.volumes, vid), self.cloud.find(self.cloud.volumes, dest_id)
        nid = src.get("os-vol-mig-status-attr:name_id") or vid
        c = self.mover_host.content
        c[(self.vg_of_host(dest["os-vol-host-attr:host"]), "volume-%s" % dest_id)] = c[(self.vg_of_host(src["os-vol-host-attr:host"]), "volume-%s" % nid)]

    def driver_delete(self, rec):
        """Cinder's LVM driver deleting the backing volume of a record: refused while the volume has snapshots (seen in the lab)."""
        vg = SRC_VG if "sf-1" in rec["os-vol-host-attr:host"] else DST_VG
        name = "volume-%s" % (rec.get("os-vol-mig-status-attr:name_id") or rec["id"])
        m = self.mover_host
        if name in m.lv[vg] and not any(r["origin"] == name for r in m.lv[vg].values()):
            del m.lv[vg][name]
            m.content.pop((vg, name), None)


def staged_cloud(snapshots=2, attached=True, size_gib=1):
    """A MockCloud with one class C volume (and, for an attached one, its instance), with matching logical volumes in a FakeFabric.
    Returns (cloud, fabric, ids) where ids = {'volume', 'snapshots': [...], 'server'}."""
    from .mock_cloud import MockCloud, snapshot, volume
    c = MockCloud()
    c.types = [{"id": "t1", "name": "fast", "extra_specs": {"volume_backend_name": "sf-1"}},
               {"id": "t2", "name": "pure", "extra_specs": {"volume_backend_name": "pure-1"}}]
    c.pools = [{"name": "ctl@sf-1#pool", "capabilities": {"volume_backend_name": "sf-1"}}, {"name": "ctl@pure-1#pool", "capabilities": {"volume_backend_name": "pure-1"}}]
    vid = "cccccccc-0000-0000-0000-000000000001"
    srv = "srv-c"
    att = [{"server_id": srv, "device": "/dev/vdb", "host_name": "cmp1"}] if attached else []
    c.volumes = [volume(vid, status="in-use" if attached else "available", size=size_gib, attachments=att)]
    c.volumes[0]["os-vol-mig-status-attr:name_id"] = None
    sids = ["eeeeeeee-0000-0000-0000-00000000000%d" % (i + 1) for i in range(snapshots)]
    c.snapshots = [snapshot(s, vid, 100 - 10 * i, size=size_gib) for i, s in enumerate(sids)]
    c.servers = [{"id": srv, "status": "ACTIVE", "OS-EXT-STS:task_state": None, "locked": False, "image": {"id": "img"}}] if attached else []
    c.start()
    fab = FakeFabric(c)
    m = fab.mover_host
    m.add(SRC_VG, "volume-%s" % vid, size_gib * GIB, "head-v0", tags=())
    for i, s in enumerate(sids):
        m.add(SRC_VG, "_snapshot-%s" % s, size_gib * GIB, "image-%d" % (i + 1), origin="volume-%s" % vid, tags=())
    return c, fab, {"volume": vid, "snapshots": sids, "server": srv if attached else None}
