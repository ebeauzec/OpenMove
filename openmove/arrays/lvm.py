# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The LVM adapter: two Cinder LVM back-ends stand in for the two arrays (the OpenStack lab, docs/LAB.md).

How Cinder's LVM driver names things, and so how the staged method has to name them:
  a volume                  logical volume  volume-<name id>                   in the volume group of its back-end
  a snapshot of a volume    logical volume  _snapshot-<snapshot id>             a copy-on-write snapshot of the volume's logical volume
The staging volume is created as om-stage-<volume id>, gets the snapshots _snapshot-<id> in chronological order, and is renamed to
volume-<destination record id> when the destination record exists. Cinder then finds the volume and every snapshot by name, which is
exactly the situation after a standard migration.

This adapter proves the method's mechanics (the order, the copy, the compare, the record, the swap, the rollbacks). It cannot prove anything
about SolidFire or FlashArray: their snapshot, clone and naming rules differ and need their own adapters.
"""
from .base import GIB, Adapter, AdapterError

TAG = "openmove"


class LvmAdapter(Adapter):
    family = "lvm"
    built = True
    note = "built; proven in the OpenStack lab only (two LVM back-ends)"

    def __init__(self, mover, cfg=None):
        super().__init__(mover, cfg)
        self._vgs = None

    # ---- naming ----------------------------------------------------------------------------------------------------------
    @staticmethod
    def volume_lv(name_id):
        return "volume-%s" % name_id

    @staticmethod
    def snapshot_lv(snapshot_id):
        return "_snapshot-%s" % snapshot_id

    @staticmethod
    def stage_lv(vid):
        return "om-stage-%s" % vid

    def snapshot_name(self, snapshot_id):
        return self.snapshot_lv(snapshot_id)

    def vg_of(self, backend):
        fixed = (self.cfg.get("vgs") or {}).get(backend)
        if fixed:
            return fixed
        if self._vgs is None:
            self._vgs = self.mover.mover("probe").get("volume_groups") or []
        found = [v for v in self._vgs if v == backend or v.endswith("-" + backend)]
        if len(found) != 1:
            raise AdapterError("cannot tell which volume group belongs to back-end %r (candidates: %s); set it in the host's arrays settings" % (
                backend, ", ".join(found) or "none visible on the data mover"))
        return found[0]

    def lvs(self, backend):
        return self.mover.mover("lv.list", {"vg": self.vg_of(backend)})["lvs"]

    def find(self, backend, name):
        return next((r for r in self.lvs(backend) if r["name"] == name), None)

    # ---- reading ---------------------------------------------------------------------------------------------------------
    def describe(self):
        return {"family": self.family, "built": True, "note": self.note, "volume_groups": self._vgs}

    def locate_volume(self, backend, name_id):
        r = self.find(backend, self.volume_lv(name_id))
        if not r:
            raise AdapterError("the volume's logical volume volume-%s is not in %s" % (name_id, self.vg_of(backend)))
        return {"path": r["path"], "bytes": r["bytes"], "name": r["name"], "vg": r["vg"]}

    def locate_snapshot(self, backend, snapshot_id, volume_name_id):
        r = self.find(backend, self.snapshot_lv(snapshot_id))
        if not r:
            raise AdapterError("the snapshot %s has no logical volume in %s: Cinder and the array disagree" % (snapshot_id, self.vg_of(backend)))
        if r["origin"] != self.volume_lv(volume_name_id):
            raise AdapterError("the snapshot %s is a snapshot of %s, not of the volume %s" % (snapshot_id, r["origin"], volume_name_id))
        return {"path": r["path"], "bytes": r["bytes"], "name": r["name"], "vg": r["vg"]}

    def target_snapshots(self, backend, volume_name_id):
        origin = self.volume_lv(volume_name_id)
        return sorted(r["name"][len("_snapshot-"):] for r in self.lvs(backend) if r["origin"] == origin and r["name"].startswith("_snapshot-"))

    def exists(self, backend, name):
        return self.find(backend, name) is not None

    def free_bytes(self, backend):
        return self.mover.mover("vg.free", {"vg": self.vg_of(backend)})["free_bytes"]

    # ---- staging on the target -----------------------------------------------------------------------------------------------
    def stage_create(self, backend, vid, size_bytes):
        res = self.mover.mover("lv.create", {"vg": self.vg_of(backend), "name": self.stage_lv(vid), "bytes": int(size_bytes)})
        lv = res["lv"]
        return {"path": lv["path"], "bytes": lv["bytes"], "name": lv["name"], "vg": lv["vg"], "existed": res["existed"]}

    def stage_snapshot(self, backend, stage_name, snapshot_id, size_bytes):
        res = self.mover.mover("lv.snapshot", {"vg": self.vg_of(backend), "origin": stage_name, "name": self.snapshot_lv(snapshot_id), "bytes": int(size_bytes)})
        lv = res["lv"]
        return {"path": lv["path"], "bytes": lv["bytes"], "name": lv["name"], "vg": lv["vg"], "existed": res["existed"]}

    def stage_extend(self, backend, stage_name, size_bytes):
        res = self.mover.mover("lv.extend", {"vg": self.vg_of(backend), "name": stage_name, "bytes": int(size_bytes)})
        lv = res["lv"]
        return {"path": lv["path"], "bytes": lv["bytes"], "name": lv["name"], "vg": lv["vg"], "existed": res["existed"]}

    def stage_rename(self, backend, stage_name, dest_id):
        res = self.mover.mover("lv.rename", {"vg": self.vg_of(backend), "old": stage_name, "new": self.volume_lv(dest_id)})
        lv = res["lv"]
        return {"path": lv["path"], "bytes": lv["bytes"], "name": lv["name"], "vg": lv["vg"], "existed": res["existed"]}

    def clone_head(self, backend, volume_name, label, vid, size_bytes):
        name = "om-%s-%s" % (label, vid)
        res = self.mover.mover("lv.create", {"vg": self.vg_of(backend), "name": name, "bytes": int(size_bytes)})
        lv = res["lv"]
        return {"path": lv["path"], "bytes": lv["bytes"], "name": lv["name"], "vg": lv["vg"], "existed": res["existed"]}

    # ---- cleaning up ---------------------------------------------------------------------------------------------------------
    def remove(self, backend, name):
        return self.mover.mover("lv.remove", {"vg": self.vg_of(backend), "name": name})

    def release_source(self, backend, volume_name, snapshot_ids):
        names = [self.snapshot_lv(s) for s in snapshot_ids]
        return self.mover.mover("lv.remove_source_snapshots", {"vg": self.vg_of(backend), "origin": volume_name, "names": names})
