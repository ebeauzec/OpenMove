# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Audit of the current environment: a read-only, full picture of the cloud as it is today.

It covers the platform (release, services, back-ends and their capacity, compute hosts), the
volume types, every volume and snapshot (by project, type, back-end, state, size and age), the
backups and the instances that use the volumes, and it lists findings: things that are wrong,
stuck, orphaned or close to a limit, each with the affected objects and what to do. Nothing
here changes the cloud, and what could not be read is said so rather than skipped.
"""
from datetime import datetime, timezone

from .connectors.httpclient import ConnectorError
from .connectors.openstack import BLOCK_STORAGE_TYPES
from .inventory import _parse_time

TRANSIENT = ("creating", "deleting", "attaching", "detaching", "extending", "retyping", "uploading", "downloading",
             "backing-up", "restoring-backup", "maintenance", "reserved", "awaiting-transfer", "migrating")
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
STUCK_HOURS = 24
SNAPSHOT_HEAVY = 25


def collect_extra(session):
    """Volume types, QoS, backups and project names. Each is optional: a failure becomes a warning."""
    out, warnings = {"types": [], "qos": [], "backups": [], "projects": []}, []
    probes = (
        ("types", "volume types", lambda: [
            {"id": t.get("id"), "name": t.get("name"), "is_public": t.get("is_public"), "description": t.get("description"),
             "extra_specs": t.get("extra_specs") or {}, "qos_specs_id": t.get("qos_specs_id")}
            for t in (session.get(BLOCK_STORAGE_TYPES, "/types") or {}).get("volume_types", [])]),
        ("qos", "QoS specifications", lambda: [
            {"id": q.get("id"), "name": q.get("name"), "consumer": q.get("consumer"), "specs": q.get("specs") or {}}
            for q in (session.get(BLOCK_STORAGE_TYPES, "/qos-specs") or {}).get("qos_specs", [])]),
        ("backups", "backups", lambda: [
            {"id": b.get("id"), "volume_id": b.get("volume_id"), "status": b.get("status"), "size": b.get("size"),
             "created_at": b.get("created_at")}
            for b in session.paginate(BLOCK_STORAGE_TYPES, "/backups/detail", "backups", {"all_tenants": 1})]),
        ("projects", "project names", lambda: [
            {"id": p.get("id"), "name": p.get("name"), "enabled": p.get("enabled", True)}
            for p in (session.get_url(session.auth_url + "/projects") or {}).get("projects", [])]),
    )
    for key, label, fn in probes:
        try:
            out[key] = fn()
        except ConnectorError as exc:
            warnings.append("%s not read: %s" % (label, exc))
    out["warnings"] = warnings
    return out


def _age_h(ts, now):
    t = _parse_time(ts)
    return (now - t).total_seconds() / 3600.0 if t else None


def _count(items, key):
    out = {}
    for i in items:
        k = key(i)
        out[k] = out.get(k, 0) + 1
    return out


def _bucket(hours):
    if hours is None:
        return "unknown"
    return "under 7 days" if hours < 168 else "7 to 30 days" if hours < 720 else "30 to 90 days" if hours < 2160 else "over 90 days"


def _finding(fid, severity, title, affected, detail, action):
    return {"id": fid, "severity": severity, "title": title, "count": len(affected), "affected": affected[:500],
            "detail": detail, "action": action}


def build(platform, raw, extra, backend=None, now=None, tls_verification=True):
    """The audit of one environment. 'raw' is inventory.collect() output, 'extra' is collect_extra() output."""
    now = now or datetime.now(timezone.utc)
    vols, snaps, servers = raw["volumes"], raw.get("snapshots", []), raw.get("servers", {})
    projects = {p["id"]: p for p in extra.get("projects", [])}
    pname = lambda pid: (projects.get(pid) or {}).get("name") or ("(unknown project %s)" % str(pid)[:8] if pid else "(none)")
    vol_ids = {v["id"] for v in vols}
    snaps_by_vol = {}
    for s in snaps:
        snaps_by_vol.setdefault(s["volume_id"], []).append(s)

    # ---- platform and capacity
    pools = []
    for p in platform.get("backends", []):
        total, free = p.get("total_gb"), p.get("free_gb")
        try:
            util = round(100.0 * (float(total) - float(free)) / float(total), 1) if total and free is not None else None
        except (TypeError, ValueError, ZeroDivisionError):
            util = None
        pools.append(dict(p, utilisation_pct=util))
    services = []
    for s in platform.get("cinder_services", []):
        services.append(dict(s, age_h=round(_age_h(s.get("updated_at"), now), 1) if _age_h(s.get("updated_at"), now) is not None else None))

    # ---- volumes
    def vgroup(key):
        groups = {}
        for v in vols:
            g = groups.setdefault(key(v), {"volumes": 0, "gib": 0, "snapshots": 0})
            g["volumes"] += 1
            g["gib"] += v["size"]
            g["snapshots"] += int(v.get("snapshots") or 0)
        return [dict(name=k, **g) for k, g in sorted(groups.items(), key=lambda kv: -kv[1]["gib"])]

    sizes = [("up to 10 GiB", 10), ("11 to 50 GiB", 50), ("51 to 100 GiB", 100), ("101 to 500 GiB", 500), ("over 500 GiB", None)]
    size_dist = {label: 0 for label, _ in sizes}
    for v in vols:
        for label, hi in sizes:
            if hi is None or v["size"] <= hi:
                size_dist[label] += 1
                break
    type_use = _count(vols, lambda v: v.get("volume_type") or "(none)")

    # ---- snapshots
    snap_ages = {}
    for s in snaps:
        b = _bucket(_age_h(s.get("created_at"), now))
        snap_ages[b] = snap_ages.get(b, 0) + 1
    heavy = sorted(((len(v), k) for k, v in snaps_by_vol.items() if k in vol_ids), reverse=True)[:10]

    # ---- instances
    attached_to = {a["server_id"] for v in vols for a in v["attachments"] if a.get("server_id")}
    inst = {"total": len(servers), "by_status": _count(servers.values(), lambda s: s.get("status") or "unknown"),
            "by_host": _count(servers.values(), lambda s: s.get("host") or "unknown"),
            "boot_from_volume": sum(1 for s in servers.values() if s.get("image_empty")),
            "with_volumes": sum(1 for sid in servers if sid in attached_to)}

    # ---- findings
    F = []
    down = [s for s in services if s.get("status") == "enabled" and s.get("state") != "up"]
    if down:
        F.append(_finding("A1", "high", "Services that are enabled but down", ["%s on %s" % (s["binary"], s["host"]) for s in down],
                          "These services are enabled but have not reported in; work sent to them waits or fails.",
                          "Restart the service or disable it with a reason, and find why it stopped."))
    off = [s for s in services if s.get("status") != "enabled"]
    if off:
        F.append(_finding("A2", "low", "Services that are disabled", ["%s on %s (%s)" % (s["binary"], s["host"], s.get("disabled_reason") or "no reason given") for s in off],
                          "A disabled service takes no new work.", "Confirm each is meant to be disabled and that a reason is recorded."))
    err = [v for v in vols if str(v.get("status", "")).startswith("error")]
    if err:
        F.append(_finding("A3", "high", "Volumes in an error state", ["%s (%s, %s)" % (v["id"], v["name"], v["status"]) for v in err],
                          "A volume in error cannot be used or migrated.", "Find the cause, reset the state if the data is intact, or restore from backup."))
    stuck = []
    for v in vols:
        if v.get("status") in TRANSIENT:
            age = _age_h(v.get("updated_at"), now)
            if age is not None and age > STUCK_HOURS:
                stuck.append((v, age))
    if stuck:
        sev = "high" if any(a > 168 for _, a in stuck) else "medium"
        F.append(_finding("A4", sev, "Volumes stuck in a transient state", ["%s (%s for %.0f hours)" % (v["id"], v["status"], a) for v, a in stuck],
                          "These volumes have been in a state that should last minutes for more than %d hours." % STUCK_HOURS,
                          "Check the back-end and the service logs, then reset the state or finish the operation."))
    no_att = [v for v in vols if v["status"] == "in-use" and not v["attachments"]]
    if no_att:
        F.append(_finding("A5", "medium", "In-use volumes with no attachment record", ["%s (%s)" % (v["id"], v["name"]) for v in no_att],
                          "Cinder says the volume is in use, but it records no instance.", "Reset the attach state after confirming no instance uses it."))
    stale_att = [v for v in vols if v["status"] != "in-use" and v["attachments"]]
    if stale_att:
        F.append(_finding("A6", "medium", "Attachment records on volumes that are not in use", ["%s (%s, %s)" % (v["id"], v["name"], v["status"]) for v in stale_att],
                          "The attachment record and the volume state disagree.", "Check which is right, then correct the record."))
    if raw.get("servers_read", True):
        ghost = [(v, a) for v in vols for a in v["attachments"] if a.get("server_id") and a["server_id"] not in servers]
        if ghost:
            F.append(_finding("A7", "high", "Volumes attached to an instance that does not exist", ["%s -> %s" % (v["id"], a["server_id"]) for v, a in ghost],
                              "The instance is gone but the volume still points to it.", "Detach the record after confirming the instance was deleted."))
    untyped = [v for v in vols if not v.get("volume_type")]
    if untyped:
        F.append(_finding("A8", "low", "Volumes with no volume type", ["%s (%s)" % (v["id"], v["name"]) for v in untyped],
                          "Without a type the volume cannot be scheduled or retyped by policy.", "Set a type where it is safe to do so."))
    if projects:
        orphans = [v for v in vols if v.get("project_id") not in projects]
        if orphans:
            F.append(_finding("A9", "medium", "Volumes owned by a project that no longer exists", ["%s (%s)" % (v["id"], v["name"]) for v in orphans],
                              "No one can manage these volumes through their project.", "Find the owner, move the volume or delete it after review."))
        dis = [v for v in vols if (projects.get(v.get("project_id")) or {}).get("enabled") is False]
        if dis:
            F.append(_finding("A10", "low", "Volumes in disabled projects", ["%s (%s, %s)" % (v["id"], v["name"], pname(v["project_id"])) for v in dis],
                              "The project is disabled, so its owners cannot act on the volumes.", "Confirm the project is meant to stay disabled."))
    gone = [s for s in snaps if s.get("volume_id") not in vol_ids]
    if gone:
        F.append(_finding("A11", "medium", "Snapshots whose volume no longer exists", ["%s (volume %s)" % (s["id"], s["volume_id"]) for s in gone],
                          "These snapshots use array space and cannot be reached through a volume.", "Delete them after review, or restore the volume record."))
    bad_snaps = [s for s in snaps if s.get("status") == "error" or (s.get("status") in TRANSIENT and (_age_h(s.get("updated_at"), now) or 0) > STUCK_HOURS)]
    if bad_snaps:
        F.append(_finding("A12", "medium", "Snapshots in error or stuck", ["%s (%s)" % (s["id"], s["status"]) for s in bad_snaps],
                          "These snapshots are not usable and a volume with one cannot be migrated.", "Reset or delete them."))
    if heavy and heavy[0][0] >= SNAPSHOT_HEAVY:
        F.append(_finding("A13", "info", "Volumes with many snapshots", ["%s (%d snapshots)" % (vid, n) for n, vid in heavy if n >= SNAPSHOT_HEAVY],
                          "Back-ends limit the snapshots per volume, and a long chain is slow to migrate.", "Review the retention policy."))
    for p in pools:
        u = p.get("utilisation_pct")
        if u is not None and u >= 80:
            F.append(_finding("A14", "high" if u >= 90 else "medium", "Pool nearly full: %s" % p["pool"], ["%s at %.1f percent" % (p["pool"], u)],
                              "A pool that is full stops creating volumes and snapshots.", "Add capacity or move volumes away."))
    over = []
    for p in pools:
        try:
            ratio, prov, tot = float(p.get("max_over_subscription_ratio") or 0), float(p.get("provisioned_gb") or 0), float(p.get("total_gb") or 0)
        except (TypeError, ValueError):
            continue
        if ratio and tot and prov / tot > ratio:
            over.append("%s: provisioned %.0f GiB is %.1f times its capacity (limit %.1f)" % (p["pool"], prov, prov / tot, ratio))
    if over:
        F.append(_finding("A22", "medium", "Pools provisioned beyond their over-subscription limit", over,
                          "New volumes will be refused by the scheduler, and a burst of writes could fill the pool.",
                          "Add capacity, raise the limit knowingly, or move volumes away."))
    hosts_bad = [h for h in platform.get("compute_hosts", []) if h.get("state") != "up" or h.get("status") != "enabled"]
    if hosts_bad:
        F.append(_finding("A15", "medium", "Compute hosts that are down or disabled", ["%s (%s, %s)" % (h["host"], h["state"], h["status"]) for h in hosts_bad],
                          "Instances on these hosts, and the volumes attached to them, are affected.", "Confirm each is meant to be out of service."))
    err_inst = [sid for sid, s in servers.items() if s.get("status") == "ERROR" and sid in attached_to]
    if err_inst:
        F.append(_finding("A16", "medium", "Instances in ERROR with volumes attached", ["%s (%s)" % (sid, servers[sid].get("name")) for sid in err_inst],
                          "The volumes of these instances cannot be migrated safely.", "Recover or delete the instances."))
    off_inst = [sid for sid, s in servers.items() if s.get("status") == "SHUTOFF" and sid in attached_to]
    if off_inst:
        F.append(_finding("A17", "info", "Shut-off instances with volumes attached", ["%s (%s)" % (sid, servers[sid].get("name")) for sid in off_inst],
                          "These volumes wait (class D) until the instance runs or the owner agrees to a different method.", "Plan the volumes' migration with their owners."))
    bad_bk = [b for b in extra.get("backups", []) if b.get("status") == "error"]
    if bad_bk:
        F.append(_finding("A18", "low", "Backups in error", ["%s (volume %s)" % (b["id"], b["volume_id"]) for b in bad_bk],
                          "A failed backup is not a recovery point.", "Retry or remove them and check the backup service."))
    if extra.get("types") and pools:
        known = {p.get("backend") for p in pools}
        dangling = [t for t in extra["types"] if t["extra_specs"].get("volume_backend_name") and t["extra_specs"]["volume_backend_name"] not in known]
        if dangling:
            F.append(_finding("A19", "medium", "Volume types that name a back-end that is not reported", ["%s -> %s" % (t["name"], t["extra_specs"]["volume_backend_name"]) for t in dangling],
                              "Volumes of this type cannot be scheduled.", "Correct the type or restore the back-end."))
    if not tls_verification:
        F.append(_finding("A20", "medium", "TLS verification is off for this session", ["session"], "The identity and API servers were not verified.",
                          "Provide the CA certificate and turn verification on."))
    incomplete = list(platform.get("warnings", [])) + list(extra.get("warnings", [])) + list(raw.get("warnings", []))
    incomplete = [w for w in incomplete if not w.startswith("read only")]
    if incomplete:
        F.append(_finding("A21", "medium", "Parts of the audit could not be read", incomplete,
                          "Those parts are missing from this audit; their findings cannot be known.", "Use an administrator account or grant the missing read access, then run the audit again."))
    F.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], f["id"]))

    return {
        "collected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "backend_filter": backend,
        "environment": {"release": platform.get("release"), "profile": platform.get("profile"), "read_only": platform.get("read_only"),
                        "identity": platform.get("identity"), "block_storage": platform.get("block_storage"),
                        "compute": platform.get("compute"), "tls_verification": tls_verification},
        "services": services, "backends": pools, "compute_hosts": platform.get("compute_hosts", []),
        "volume_types": [dict(t, volumes=type_use.get(t["name"], 0)) for t in extra.get("types", [])], "qos": extra.get("qos", []),
        "volumes": {"total": len(vols), "gib": sum(v["size"] for v in vols), "by_status": _count(vols, lambda v: v["status"]),
                    "by_backend": vgroup(lambda v: v.get("backend") or "(unknown)"), "by_type": vgroup(lambda v: v.get("volume_type") or "(none)"),
                    "by_project": [dict(g, name=pname(g["name"]), project_id=g["name"]) for g in vgroup(lambda v: v.get("project_id"))],
                    "attached": sum(1 for v in vols if v["attachments"]), "bootable": sum(1 for v in vols if str(v.get("bootable")).lower() == "true"),
                    "size_distribution": size_dist},
        "snapshots": {"total": len(snaps), "gib": sum(int(s.get("size") or 0) for s in snaps), "by_status": _count(snaps, lambda s: s.get("status")),
                      "by_age": snap_ages, "heaviest": [{"volume": vid, "snapshots": n} for n, vid in heavy], "orphaned": len(gone)},
        "backups": {"total": len(extra.get("backups", [])), "by_status": _count(extra.get("backups", []), lambda b: b.get("status")),
                    "gib": sum(int(b.get("size") or 0) for b in extra.get("backups", []))},
        "instances": inst, "findings": F,
        "finding_counts": _count(F, lambda f: f["severity"]),
        "not_read": incomplete,
    }
