# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The adapter contract and the registry."""
GIB = 1024 ** 3


class AdapterError(Exception):
    """The array (or the mover in front of it) refused or failed. The message says what to do."""

    def __init__(self, message, outcome_unknown=False):
        super().__init__(message)
        self.outcome_unknown = outcome_unknown


class UnsupportedFamily(AdapterError):
    """No adapter exists for this back-end family."""


class Adapter:
    """One source back-end and one target back-end of one family, reached through the data mover on a migration host.

    Every method that changes something is idempotent: asked twice it returns what the first call made (existed=True), so a call whose
    answer was lost can be repeated safely. Nothing here removes anything it did not create, except release_source after sign-off."""

    family = ""
    built = False

    def __init__(self, mover, cfg=None):
        self.mover, self.cfg = mover, dict(cfg or {})

    # ---- reading ---------------------------------------------------------------------------------------------------------
    def describe(self):
        raise NotImplementedError

    def locate_volume(self, backend, name_id):
        """{'path', 'bytes', 'name'} of the device that holds a Cinder volume, or raises AdapterError."""
        raise NotImplementedError

    def locate_snapshot(self, backend, snapshot_id, volume_name_id):
        raise NotImplementedError

    def target_snapshots(self, backend, volume_name_id):
        """The ids of the snapshots that exist on the array for this volume (for check X3)."""
        raise NotImplementedError

    def snapshot_name(self, snapshot_id):
        """The name the array gives the snapshot of a Cinder snapshot."""
        raise NotImplementedError

    def exists(self, backend, name):
        """True when a device of this name is on the array."""
        raise NotImplementedError

    def free_bytes(self, backend):
        """Free space on the back-end, or None when the array cannot say."""
        raise NotImplementedError

    def required_bytes(self, size_bytes, snapshots):
        """Space the staged copy needs on the target: the volume plus room for every snapshot image written as it is taken."""
        return int(size_bytes) * (1 + max(0, int(snapshots)))

    # ---- staging on the target -----------------------------------------------------------------------------------------------
    def stage_create(self, backend, vid, size_bytes):
        raise NotImplementedError

    def stage_snapshot(self, backend, stage_name, snapshot_id, size_bytes):
        raise NotImplementedError

    def stage_rename(self, backend, stage_name, dest_id):
        """Give the staging volume the name the target driver derives for the destination record. Returns the new name."""
        raise NotImplementedError

    def clone_head(self, backend, volume_name, label, vid, size_bytes):
        """A full copy of a volume's current state, for rollback (R2) or as a safety copy (R3). Returns the device; the copy runs as a job."""
        raise NotImplementedError

    # ---- cleaning up ---------------------------------------------------------------------------------------------------------
    def remove(self, backend, name):
        raise NotImplementedError

    def release_source(self, backend, volume_name, snapshot_ids):
        """After the soak: remove the source volume's snapshots and (if Cinder could not) the volume. Only named things."""
        raise NotImplementedError

    # ---- jobs (shared) ---------------------------------------------------------------------------------------------------------
    def job(self, job_id, verb, args):
        return self.mover.mover("job.start", {"job": job_id, "verb": verb, "args": args})["job"]

    def job_status(self, job_id):
        return self.mover.mover("job.status", {"job": job_id})["job"]


def _registry():
    from .lvm import LvmAdapter
    return {"lvm": LvmAdapter}


def families():
    """[{'family', 'built', 'note'}] for the screen and the checks."""
    out = [{"family": f, "built": cls.built, "note": cls.note if hasattr(cls, "note") else ""} for f, cls in _registry().items()]
    for f, why in (("solidfire", "needs a SolidFire cluster (Element API) to write and to prove against"),
                   ("pure", "needs a FlashArray (REST API) to write and to prove against"),
                   ("ontap", "not built"), ("ceph", "not built")):
        out.append({"family": f, "built": False, "note": why})
    return out


def adapter_for(family, mover, cfg=None):
    cls = _registry().get(family)
    if cls is None:
        raise UnsupportedFamily("there is no array adapter for the %r back-end family yet (built: %s)" % (family, ", ".join(sorted(_registry())) or "none"))
    return cls(mover, cfg)
