# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Read-only inventory of Cinder volumes through the OpenStack APIs, then classification.

Needs an administrator account (all projects). Volumes, snapshots and servers are read; nothing
is changed. Fields the API does not show (provider_id, snapshot history) are left out and
the classifier reports that it did not check them.
"""
from datetime import datetime, timezone

from . import classification
from .connectors.openstack import BLOCK_STORAGE_TYPES, COMPUTE_TYPES

HOST_ATTR = "os-vol-host-attr:host"
MIG_ATTR = "os-vol-mig-status-attr:migstat"
NAME_ID_ATTR = "os-vol-mig-status-attr:name_id"
TENANT_ATTR = "os-vol-tenant-attr:tenant_id"
ROOT_DEVICES = ("/dev/vda", "/dev/sda", "/dev/xvda")


def _parse_time(text):
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# image properties that hint at the guest (openmove/risk.py reads them; nothing else from the image is kept)
IMAGE_KEYS = ("os_type", "os_distro", "os_version", "image_name", "hw_disk_bus", "hw_scsi_model", "hw_qemu_guest_agent",
              "hw_firmware_type", "hw_machine_type", "architecture")


def _backend(host):
    return host.split("@", 1)[1].split("#", 1)[0] if host and "@" in host else None


def normalise_volume(v, snapshots):
    """One Cinder volume (API form) -> the record OpenMove works with."""
    host = v.get(HOST_ATTR) or ""
    now = datetime.now(timezone.utc)
    ages, sizes = [], []
    for s in snapshots:
        t = _parse_time(s.get("created_at"))
        if t:
            ages.append((now - t).total_seconds() / 3600.0)
        if s.get("size") is not None:
            sizes.append(int(s["size"]))
    return {
        "id": v["id"], "name": v.get("name"), "status": v.get("status"), "size": int(v.get("size") or 0),
        "project_id": v.get(TENANT_ATTR), "volume_type": v.get("volume_type"),
        "host": host, "backend": _backend(host),
        "migration_status": v.get(MIG_ATTR), "name_id": v.get(NAME_ID_ATTR),
        "multiattach": bool(v.get("multiattach")), "encrypted": bool(v.get("encrypted")),
        "group_id": v.get("group_id"), "consistencygroup_id": v.get("consistencygroup_id"),
        "replication_status": v.get("replication_status"), "bootable": v.get("bootable"),
        "snapshot_id": v.get("snapshot_id"), "source_volid": v.get("source_volid"),
        "image_meta": {k: str(x) for k, x in (v.get("volume_image_metadata") or {}).items() if k in IMAGE_KEYS},
        "attachments": [{"server_id": a.get("server_id"), "device": a.get("device"),
                         "host_name": a.get("host_name")} for a in v.get("attachments", [])],
        "snapshots": len(snapshots),
        "snapshot_ids": [s["id"] for s in snapshots],
        "snapshot_statuses": sorted({s.get("status") for s in snapshots if s.get("status")}),
        "oldest_snapshot_age_h": max(ages) if ages else 0,
        "newest_snapshot_age_h": min(ages) if ages else 0,
        "min_snapshot_size_gib": min(sizes) if sizes else None,
        "created_at": v.get("created_at"), "updated_at": v.get("updated_at"), "source": "api"}


def collect(session, nova_microversion=None):
    """Read volumes, snapshots and servers. Returns {'volumes', 'servers', 'warnings'}."""
    warnings = []
    vols = session.paginate(BLOCK_STORAGE_TYPES, "/volumes/detail", "volumes", {"all_tenants": 1})
    snaps = session.paginate(BLOCK_STORAGE_TYPES, "/snapshots/detail", "snapshots", {"all_tenants": 1})
    by_volume = {}
    for s in snaps:
        by_volume.setdefault(s.get("volume_id"), []).append(s)
    records = [normalise_volume(v, by_volume.get(v["id"], [])) for v in vols]

    servers = {}
    try:
        for s in session.paginate(COMPUTE_TYPES, "/servers/detail", "servers", {"all_tenants": 1},
                                  microversion=nova_microversion):
            servers[s["id"]] = {
                "name": s.get("name"), "project_id": s.get("tenant_id"),
                "status": s.get("status"), "task_state": s.get("OS-EXT-STS:task_state"),
                "locked": s.get("locked"), "host": s.get("OS-EXT-SRV-ATTR:host"),
                "image_empty": s.get("image") in ("", None, {}),
                "metadata": {k: str(x) for k, x in (s.get("metadata") or {}).items() if k in IMAGE_KEYS},
                "flavor": (s.get("flavor") or {}).get("original_name")}
    except Exception as exc:  # compute data is an extra: classification then notes what it lacks
        warnings.append("servers not read: %s" % exc)

    # a probable root volume: boot-from-volume instance (no image) with the volume on the first device
    roots = set()
    for rec in records:
        for a in rec["attachments"]:
            sv = servers.get(a.get("server_id"))
            if sv and sv["image_empty"] and a.get("device") in ROOT_DEVICES:
                roots.add(rec["id"])
    snapshots = [{"id": s.get("id"), "volume_id": s.get("volume_id"), "status": s.get("status"), "size": s.get("size"),
                  "created_at": s.get("created_at"), "updated_at": s.get("updated_at")} for s in snaps]
    return {"volumes": records, "servers": servers, "snapshots": snapshots, "root_volumes": sorted(roots),
            "servers_read": not any(w.startswith("servers not read") for w in warnings), "warnings": warnings}


def build(volumes, servers=None, root_volumes=(), churn=None, backend=None, window_days=14, snapshot_limit=None):
    """Filter to a back-end if asked, classify, and return the inventory result."""
    if backend:
        volumes = [v for v in volumes if v.get("backend") == backend]
    rows, counts, assumed = classification.classify_all(
        volumes, churn=churn, instance_states=servers or {}, root_volumes=set(root_volumes),
        window_days=window_days, snapshot_limit=snapshot_limit)
    return {"collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "backend": backend, "window_days": window_days, "total": len(rows),
            "total_gib": sum(r["size"] for r in rows), "counts": counts, "assumptions": assumed, "volumes": rows}
