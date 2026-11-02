# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""A made-up 'database export' for the demonstration cloud, in the format of the playbook's read-only queries.

On a real cloud the operator runs queries A1 (inventory), A2 (snapshot churn) and A4 (root volumes) and gives the
output to OpenMove, or OpenMove reads the Cinder database itself. They hold what the API cannot show: the array
volume id (provider_id) and the snapshot history, which separate class B (snapshots that rotate) from class C.
In demonstration mode this module writes the same tables from the mock cloud, so the whole classification can be shown.
"""
from datetime import datetime, timezone

A1_COLUMNS = ("id", "status", "size", "project_id", "volume_type_id", "host", "migration_status", "multiattach",
              "encryption_key_id", "group_id", "consistencygroup_id", "replication_status", "provider_id", "attach_status",
              "snapshots", "oldest_snapshot_age_h", "min_snapshot_size_gib")


def _t(rows, columns):
    return "\n".join(["\t".join(columns)] + ["\t".join("" if r.get(c) is None else str(r[c]) for c in columns) for r in rows]) + "\n"


def _age_h(ts, now):
    t = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
    return (now - t).total_seconds() / 3600.0


def build(cloud):
    """Return {'a1', 'a2', 'a4', 'inst', 'backend'} as text, for POST /api/inventory/import."""
    now = datetime.now(timezone.utc)
    meta = cloud.meta
    sf = meta.get("solidfire", set())
    types = {t["name"]: t["id"] for t in cloud.types}
    snaps = {}
    for s in cloud.snapshots:
        snaps.setdefault(s["volume_id"], []).append(s)
    servers = {s["id"]: s["status"] for s in cloud.servers}
    a1, a2, inst = [], [], []
    for n, v in enumerate(cloud.volumes, 1):
        backend = v["os-vol-host-attr:host"].split("@", 1)[1].split("#", 1)[0]
        if backend not in sf:
            continue
        vs = snaps.get(v["id"], [])
        if v["id"] in meta.get("bad_provider", ()):
            provider = "unknown-volume"            # malformed: class E (playbook Step 1.9)
        else:
            provider = "%d %d cluster-%s" % (4000 + n, 12 + n % 9, backend)
        a1.append({"id": v["id"], "status": v["status"], "size": v["size"], "project_id": v["os-vol-tenant-attr:tenant_id"],
                   "volume_type_id": types.get(v.get("volume_type")), "host": v["os-vol-host-attr:host"],
                   "migration_status": v.get("os-vol-mig-status-attr:migstat"), "multiattach": 1 if v.get("multiattach") else 0,
                   "encryption_key_id": "key-%s" % v["id"][-4:] if v.get("encrypted") else None,
                   "group_id": v.get("group_id"), "consistencygroup_id": v.get("consistencygroup_id"),
                   "replication_status": v.get("replication_status"), "provider_id": provider,
                   "attach_status": "attached" if v["attachments"] else "detached", "snapshots": len(vs),
                   "oldest_snapshot_age_h": round(max([_age_h(s["created_at"], now) for s in vs] or [0]), 1),
                   "min_snapshot_size_gib": min([s["size"] for s in vs]) if vs else None})
        if vs:
            rotating = v["id"] in meta.get("rotating", ())
            a2.append({"volume_id": v["id"], "created_last_60d": 55 if rotating else 0, "deleted_last_60d": 52 if rotating else 0,
                       "max_lifetime_h": 168 if rotating else int(max(_age_h(s["created_at"], now) for s in vs))})
        for a in v["attachments"]:
            inst.append({"volume_id": v["id"], "instance_uuid": a["server_id"], "instance_status": servers.get(a["server_id"], "DELETED")})
    roots = [{"volume_id": i} for i in sorted(meta.get("roots", ()))]
    return {"a1": _t(a1, A1_COLUMNS),
            "a2": _t(a2, ("volume_id", "created_last_60d", "deleted_last_60d", "max_lifetime_h")),
            "a4": _t(roots, ("volume_id",)), "inst": _t(inst, ("volume_id", "instance_uuid", "instance_status"))}
