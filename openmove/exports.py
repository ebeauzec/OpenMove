# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Import the results of the playbook's database queries (Appendix A) when database access is not given.

The operator runs the read-only queries A1 (inventory), A2 (snapshot churn) and optionally
A4 (root volumes of boot-from-volume instances) and gives the tab-separated output to
OpenMove. The columns are those of the playbook. They add provider_id and the snapshot
history, which the API does not show.
"""
import csv
import io

NULLS = ("", "NULL", "None", "none", "-")


def _rows(text):
    return list(csv.DictReader(io.StringIO(text.replace("\r\n", "\n")), delimiter="\t"))


def _null(v):
    return None if v is None or v in NULLS else v


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def parse_inventory(a1_text):
    """Return normalised volume records from the A1 output."""
    rows = _rows(a1_text)
    if rows and "id" not in rows[0]:
        raise ValueError("the inventory export has no 'id' column; run query A1 and keep its header row")
    out = []
    for r in rows:
        host = _null(r.get("host")) or ""
        out.append({
            "id": r["id"], "name": None, "status": _null(r.get("status")),
            "size": _int(r.get("size")), "project_id": _null(r.get("project_id")),
            "volume_type_id": _null(r.get("volume_type_id")), "host": host,
            "backend": host.split("@", 1)[1].split("#", 1)[0] if "@" in host else None,
            "migration_status": _null(r.get("migration_status")),
            "multiattach": r.get("multiattach") in ("1", "True", "true"),
            "encrypted": _null(r.get("encryption_key_id")) is not None,
            "group_id": _null(r.get("group_id")), "consistencygroup_id": _null(r.get("consistencygroup_id")),
            "replication_status": _null(r.get("replication_status")),
            "provider_id": _null(r.get("provider_id")),
            "attach_status": _null(r.get("attach_status")),
            "attachments": [],
            "snapshots": _int(r.get("snapshots")),
            "oldest_snapshot_age_h": float(r.get("oldest_snapshot_age_h") or 0),
            "min_snapshot_size_gib": _null(r.get("min_snapshot_size_gib")),
            "source": "database export"})
    return out


def parse_churn(a2_text):
    return {r["volume_id"]: {"created_last_60d": _int(r.get("created_last_60d")),
                             "deleted_last_60d": _int(r.get("deleted_last_60d")),
                             "max_lifetime_h": _int(r.get("max_lifetime_h"))} for r in _rows(a2_text)}


def parse_root_volumes(a4_text):
    return {r["volume_id"] for r in _rows(a4_text)}


def parse_instances(text):
    """volume_id, instance_uuid, instance_status (attached volumes only)."""
    return _rows(text)


def apply_instances(volumes, rows):
    """Attach the instance rows to the volume records. Returns the instance-state map for the classifier."""
    by_volume = {}
    states = {}
    for r in rows:
        by_volume.setdefault(r["volume_id"], []).append(r["instance_uuid"])
        states[r["instance_uuid"]] = {"status": r.get("instance_status"), "task_state": None}
    for v in volumes:
        v["attachments"] = [{"server_id": sid, "device": None} for sid in by_volume.get(v["id"], [])]
    return states
