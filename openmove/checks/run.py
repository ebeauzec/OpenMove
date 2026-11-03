# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Runs every check that can run with the access OpenMove has, and lists the rest.

Statuses:
  pass         verified, nothing to do
  info         verified, for the record
  warn         verified and worth a decision: needs an acknowledgement before the gate opens
  fail         verified and wrong: blocks, cannot be waived, must be fixed
  not_checked  could not be verified with the access available: needs an attestation or the access

Nothing here changes the cloud. A check never reports 'pass' for something it did not look at.
"""
import hashlib
import secrets
from datetime import datetime, timezone

from .. import backends
from .. import risk as risk_mod
from . import catalogue
from ..classification import FINAL_MIGRATION_STATES, OK_INSTANCE_STATES, PROVIDER_RE

STATUSES = ("pass", "info", "warn", "fail", "not_checked")
ORDER = {s: i for i, s in enumerate(("fail", "not_checked", "warn", "info", "pass"))}


def rec(cid, status, looked_at, evidence, meaning, source="api"):
    meta = catalogue.BY_ID[cid]
    return {"id": cid, "group": meta["group"], "title": meta["title"], "status": status, "looked_at": looked_at,
            "evidence": evidence, "meaning": meaning, "source": source, "needs": meta["needs"]}


def waiting(cid, why=None):
    meta = catalogue.BY_ID[cid]
    return rec(cid, "not_checked", "Nothing: no access to the component",
               why or ("Waiting for access to: " + ", ".join(meta["needs"])),
               "Not verified. Blocks the gate until it is checked or attested by an operator.", "none")


def worst(statuses):
    return min(statuses, key=lambda s: ORDER[s]) if statuses else "pass"


# ---- platform ------------------------------------------------------------------------------------

def _backend_of_host(host):
    return host.split("@", 1)[1].split("#", 1)[0] if host and "@" in host else None


def _services_for(platform, backend):
    return [s for s in platform.get("cinder_services", [])
            if s.get("binary") == "cinder-volume" and _backend_of_host(s.get("host")) == backend]


def copy_hosts(platform, source_backend, target_backend):
    """Which cinder-volume hosts serve the source and the target back-end (names without the back-end part)."""
    def hosts(be):
        return sorted({s["host"].split("@", 1)[0] for s in _services_for(platform, be) if s.get("state") == "up"})
    src, tgt = hosts(source_backend), hosts(target_backend)
    return {"source_hosts": src, "target_hosts": tgt, "single": sorted(set(src) & set(tgt)),
            "relay": [] if set(src) & set(tgt) else [(a, b) for a in src for b in tgt]}


def copy_plan_text(platform, source_backend, target_backend):
    if not source_backend or not target_backend:
        return "Choose the source and the target back-end to see which Cinder hosts can run the copy."
    c = copy_hosts(platform, source_backend, target_backend)
    if not c["source_hosts"] or not c["target_hosts"]:
        return "No cinder-volume service that is up was found for %s." % (
            source_backend if not c["source_hosts"] else target_backend)
    if c["single"]:
        return "Single-host copy possible on: %s (serves both back-ends). Relay is the alternative." % ", ".join(c["single"])
    return "No Cinder host serves both back-ends. Relay copy: read on %s, write on %s." % (
        ", ".join(c["source_hosts"]), ", ".join(c["target_hosts"]))


def platform_checks(platform, source_backend=None, target_backend=None):
    out = {}
    rel = platform.get("release") or {}
    prof = platform.get("profile") or {}

    # P1
    if rel.get("release") and rel.get("confidence") == "exact":
        status, meaning = "pass", "The release is identified from both API versions."
    elif rel.get("release"):
        status, meaning = "warn", "The release is the best reading of the API versions (%s); confirm it." % rel.get("confidence")
    else:
        status, meaning = "fail", "The release could not be identified; nothing can be planned for an unknown release."
    out["P1"] = rec("P1", status, "Block Storage and Compute API version documents",
                    "Release %s (%s). Block Storage API max %s, Compute API max %s. Profile %s. %s" % (
                        rel.get("release") or "unknown", rel.get("confidence"), platform["block_storage"].get("max"),
                        platform["compute"].get("max"), prof.get("status"), "; ".join(rel.get("notes") or [])), meaning)

    # P2
    if not source_backend or not target_backend:
        out["P2"] = waiting("P2", "Choose the source and the target back-end first.")
    elif not platform.get("cinder_services") or not platform.get("backends"):
        out["P2"] = waiting("P2", "The Cinder services or pools could not be read (administrator access needed).")
    else:
        problems, notes = [], []
        for role, be in (("source", source_backend), ("target", target_backend)):
            pools = [p for p in platform["backends"] if p.get("backend") == be]
            svcs = _services_for(platform, be)
            if not pools:
                problems.append("%s back-end %s has no pool reported" % (role, be))
            up = [s for s in svcs if s.get("status") == "enabled" and s.get("state") == "up"]
            if not up:
                problems.append("%s back-end %s has no enabled cinder-volume service that is up" % (role, be))
            notes.append("%s %s: %d pool(s), %d of %d service(s) enabled and up" % (role, be, len(pools), len(up), len(svcs)))
        down = [s["host"] for be in (source_backend, target_backend) for s in _services_for(platform, be)
                if s.get("state") != "up" or s.get("status") != "enabled"]
        status = "fail" if problems else "warn" if down else "pass"
        out["P2"] = rec("P2", status, "Cinder services and scheduler pools",
                        "; ".join(notes + problems + (["not enabled or not up: " + ", ".join(down)] if down else [])),
                        "Both back-ends can serve requests." if status == "pass" else
                        "A back-end cannot serve requests, or a service on it is down." if status == "fail" else
                        "A service of a back-end is down or disabled; the back-end still has one that is up.")

    # P4 .. P8, P10, P12, P14, P15 need access OpenMove does not have yet
    for cid in ("P4", "P5", "P6", "P7", "P10", "P11", "P12", "P14", "P15"):
        out[cid] = waiting(cid)

    # P3: is each back-end a known family, and has the pair been verified (the support matrix, R8)? The driver variant and the array API
    # version still need the Cinder host and the array, so a known pair stays a warning that a person attests.
    if not source_backend or not target_backend:
        out["P3"] = waiting("P3", "Choose the source and the target back-end first.")
    elif not platform.get("backends"):
        out["P3"] = waiting("P3", "The back-end pools could not be read (administrator access needed).")
    else:
        a = backends.assess(platform, source_backend, target_backend)
        out["P3"] = rec("P3", a["status"], "Target driver variant and array API version supported", a["evidence"],
                        "A back-end OpenMove does not know is unsupported and nothing is attempted on it." if a["status"] == "fail" else
                        "The pair is known. The driver variant and the array API version are not read without the Cinder host and the array; "
                        "a named person attests them. The support matrix is docs/SUPPORT_MATRIX.md.")

    # P8: propose how the existing Cinder hosts could run the copy (R12), but do not call the check passed
    out["P8"] = waiting("P8", copy_plan_text(platform, source_backend, target_backend) +
                        " Reach to the arrays, host-to-host reach for a relay and copy throughput are measured on the hosts that "
                        "will run the copy; waiting for SSH access.")

    # P9: API administrator access only
    reads = {"Cinder services": "block storage services not available", "back-end pools": "back-end pools not available",
             "compute hosts": "compute hosts not available"}
    failed = [name for name, key in reads.items() if any(w.startswith(key) for w in platform.get("warnings", []))]
    if failed:
        out["P9"] = rec("P9", "fail", "Administrator-only reads through the APIs", "Not allowed: " + ", ".join(failed),
                        "The account is not an administrator for these reads; the audit and the checks are incomplete.")
    else:
        out["P9"] = rec("P9", "warn", "Administrator-only reads through the APIs",
                        "Proven for the OpenStack APIs (Cinder services, pools, compute hosts). Not yet proven: Cinder database, "
                        "Cinder and compute hosts, source and target arrays.",
                        "Partly verified. Acknowledge once the remaining components are attested or checked.")

    # P13
    if not source_backend or not target_backend:
        out["P13"] = waiting("P13", "Choose the source and the target back-end first.")
    elif not platform.get("cinder_services"):
        out["P13"] = waiting("P13", "The Cinder services could not be read (administrator access needed).")
    else:
        zs = {be: sorted({s.get("zone") for s in _services_for(platform, be)}) for be in (source_backend, target_backend)}
        same = zs[source_backend] == zs[target_backend] and zs[source_backend]
        out["P13"] = rec("P13", "pass" if same else "warn", "Availability zones of the cinder-volume services",
                         "%s: %s; %s: %s" % (source_backend, ", ".join(map(str, zs[source_backend])) or "none",
                                            target_backend, ", ".join(map(str, zs[target_backend])) or "none"),
                         "The zones match." if same else "The zones differ: volumes may land in a different zone; confirm that is intended.")

    # P16
    if prof.get("status") == "validated":
        out["P16"] = rec("P16", "pass", "Lab validation records in the journal", "A passed validation for %s is recorded." % rel.get("release"),
                         "The release has been validated; steps that change the cloud are allowed.", "records")
    else:
        out["P16"] = rec("P16", "fail", "Lab validation records in the journal", prof.get("reason") or "No passed validation is recorded.",
                         "Gate G0 is not met. Every step that changes the cloud is refused until a lab validation passes.", "records")
    return [out[c["id"]] for c in catalogue.of_group("platform")]


# ---- volumes -------------------------------------------------------------------------------------

def _volume_type(v):
    return v.get("volume_type") or v.get("volume_type_id")


def _v1(v, ctx):
    ok = v.get("status") in ("available", "in-use") and v.get("migration_status") in FINAL_MIGRATION_STATES
    return ("pass" if ok else "fail", "status %s, migration_status %s" % (v.get("status"), v.get("migration_status") or "none"))


def _v2(v, ctx):
    why = []
    if v.get("encrypted"):
        why.append("encrypted")
    if str(v.get("multiattach")).lower() in ("1", "true", "yes"):
        why.append("multiattach")
    if v.get("group_id") or v.get("consistencygroup_id"):
        why.append("in a group")
    if v.get("replication_status") not in (None, "", "disabled", "not-capable"):
        why.append("replication %s" % v.get("replication_status"))
    return ("fail", ", ".join(why)) if why else ("pass", "none of encrypted, multiattach, group, replication")


def _v3(v, ctx):
    if "provider_id" not in v:
        return ("not_checked", "provider_id is in the Cinder database; no database access or export")
    pid = v.get("provider_id")
    return ("pass", pid) if pid and PROVIDER_RE.match(pid) else ("fail", "provider_id missing or malformed")


def _v4(v, ctx):
    t = _volume_type(v)
    return ("pass", "type %s" % t) if t else ("fail", "no volume type")


def _v5(v, ctx):
    snaps = int(v.get("snapshots") or 0)
    base = "class %s: %s" % (v.get("class"), v.get("reason"))
    if snaps and any("rotation not assessed" in a for a in v.get("assumptions", [])):
        return ("warn", base + "; snapshot churn not assessed (needs snapshot history from the database)")
    return ("pass", base)


def _v6(v, ctx):
    if "snapshot_statuses" not in v:
        return ("not_checked", "snapshot states not in the data")
    bad = [s for s in v["snapshot_statuses"] if s != "available"]
    return ("fail", "snapshot in state %s" % ", ".join(bad)) if bad else ("pass", "%d snapshot(s), all available" % int(v.get("snapshots") or 0))


def _v7(v, ctx):
    notes = [n for n in v.get("notes", []) if n in ("extended after snapshot", "at snapshot limit")]
    if notes:
        return ("warn", "; ".join(notes) + " (the mapping to array snapshots is not checked: needs the source array)")
    if not int(v.get("snapshots") or 0):
        return ("pass", "no snapshots to map")
    return ("not_checked", "mapping of Cinder snapshots to array snapshots needs the source array")


def _v8(v, ctx):
    att = [a.get("server_id") for a in v.get("attachments", []) if a.get("server_id")]
    if not att:
        return ("fail", "status in-use but no attachment record") if v.get("status") == "in-use" else ("pass", "not attached")
    if v.get("status") != "in-use":
        return ("fail", "attachment record on a volume that is %s" % v.get("status"))
    found = [ctx["servers"].get(s) for s in att]
    if any(f is None for f in found):
        return ("fail", "attached to an instance that is not found") if ctx["servers"] else \
            ("not_checked", "instance states not in the data")
    problems = []
    for f in found:
        if f.get("status") not in OK_INSTANCE_STATES:
            problems.append("instance %s" % f.get("status"))
        if f.get("task_state"):
            problems.append("task %s in progress" % f["task_state"])
    extra = ", boot volume" if v.get("id") in ctx["roots"] else ""
    lock = ", locked" if any(f.get("locked") for f in found) else ""
    return ("fail", "; ".join(problems)) if problems else ("pass", "instance state ACTIVE or PAUSED, no task%s%s" % (extra, lock))


def _v11(v, ctx):
    a = ctx["risk"].get(v["id"])
    if not a:
        return ("pass", "no known risk factor (the check reads image and instance hints only; it cannot see inside the guest)")
    why = "; ".join(r.split(": ", 1)[-1] for r in a["reasons"])
    if a["level"] != "high":
        return ("info", "medium risk: " + why)
    if ctx["cleared"].get(v["id"]) == risk_mod.signature(a):
        return ("warn", "HIGH RISK, cleared by two people: " + why)
    return ("fail", "NOT MOVED, high risk: " + why + ". It moves only after two different people clear it.")


V_FUNCS = (("V1", _v1), ("V2", _v2), ("V3", _v3), ("V4", _v4), ("V5", _v5), ("V6", _v6), ("V7", _v7), ("V8", _v8), ("V11", _v11))


def volume_checks(inventory, servers=None, root_volumes=(), target_backend=None, target_free_gib=None, previous_ids=None, risk_map=None, cleared=None):
    """Run the per-volume checks on a classified inventory. Returns (check records, per-volume status map)."""
    ctx = {"servers": servers or {}, "roots": set(root_volumes), "risk": risk_map or {}, "cleared": cleared or {}}
    vols = inventory["volumes"]
    agg = {cid: {"pass": 0, "info": 0, "warn": 0, "fail": 0, "not_checked": 0, "findings": []} for cid, _ in V_FUNCS}
    per_volume = {}
    for v in vols:
        status_of = {}
        for cid, fn in V_FUNCS:
            st, ev = fn(v, ctx)
            agg[cid][st] += 1
            status_of[cid] = st
            if st != "pass":
                agg[cid]["findings"].append({"volume": v["id"], "name": v.get("name"), "status": st, "evidence": ev})
        per_volume[v["id"]] = status_of

    records = []
    for cid, _ in V_FUNCS:
        a = agg[cid]
        st = worst([s for s in ("fail", "not_checked", "warn", "info", "pass") if a[s]])
        r = rec(cid, st, "%d volume(s) of the inventory" % len(vols),
                "%d pass, %d warn, %d fail, %d not checked" % (a["pass"], a["warn"], a["fail"], a["not_checked"]),
                "Every volume passes." if st == "pass" else
                "Volumes that fail are excluded from the migration; the others may go ahead." if st == "fail" else
                "Volumes with a warning need an acknowledgement; volumes not checked need the missing access or an attestation.")
        r.update({"counts": {k: a[k] for k in STATUSES}, "findings": a["findings"]})
        records.append(r)

    # V9: capacity of the target pool against the volumes that could move
    movable = sum(v["size"] for v in vols if v.get("class") in ("A", "B", "C") and not any(
        per_volume[v["id"]][c] == "fail" for c in per_volume[v["id"]]))
    if not target_backend:
        r9 = waiting("V9", "Choose the target back-end first.")
    elif target_free_gib is None:
        r9 = waiting("V9", "No pool capacity reported for %s (administrator access needed)." % target_backend)
    else:
        if target_free_gib < movable:
            st, m = "fail", "The target pool does not have room for the volumes that could move."
        elif target_free_gib < movable * 1.2:
            st, m = "warn", "Room for the volumes, with less than 20 percent to spare."
        else:
            st, m = "pass", "The target pool has room, with at least 20 percent to spare."
        r9 = rec("V9", st, "Pool capacity of %s against the volumes in classes A to C" % target_backend,
                 "%d GiB to move, %d GiB free in the target pool. Quota effect on projects: not checked." % (movable, target_free_gib), m)
    records.append(r9)

    # V10: drift since the previous inventory
    ids = {v["id"] for v in vols}
    if previous_ids is None:
        records.append(waiting("V10", "This is the first inventory. Run the checks again before the batch starts to detect drift."))
    else:
        new = sorted(ids - set(previous_ids))
        gone = sorted(set(previous_ids) - ids)
        r10 = rec("V10", "warn" if new else "pass", "Volume ids against the previous inventory",
                  "%d new, %d no longer present" % (len(new), len(gone)),
                  "New volumes were created since the last inventory; they need classifying." if new else "No volumes appeared since the last inventory.")
        r10["findings"] = [{"volume": i, "name": "", "status": "warn", "evidence": "created since the previous inventory"} for i in new]
        records.append(r10)
    return records, per_volume


def later_phase_checks():
    """S, C and X: they run during and after the migration. Listed so the report shows the whole catalogue."""
    out = []
    for g in ("staging", "cutover", "post"):
        for c in catalogue.of_group(g):
            out.append({"id": c["id"], "group": g, "title": c["title"], "phase": catalogue.PHASE[g], "needs": c["needs"]})
    return out


# ---- the whole run -------------------------------------------------------------------------------

def fingerprint(source_backend, target_backend, volume_ids):
    h = hashlib.sha256()
    h.update(("%s|%s|" % (source_backend, target_backend)).encode())
    h.update("\n".join(sorted(volume_ids)).encode())
    return h.hexdigest()[:16]


def apply_host_checks(plat, host_checks):
    """What the registered hosts showed (hosts.readiness) answers P5 and P8. The rest of the array checks still need an attestation."""
    byid = {}
    for c in host_checks:
        byid.setdefault(c["id"], []).append(c)
    out = list(plat)
    for i, c in enumerate(out):
        if c["id"] == "P8":
            h = [x for x in host_checks if x["id"].startswith("H")]
            bad = [x for x in h if x["status"] == "fail"]
            warn = [x for x in h if x["status"] == "warn"]
            if h:
                out[i] = rec("P8", "fail" if bad else "warn" if warn else "pass", "The migration host, through SSH",
                             "; ".join("%s: %s" % (x["title"], x["detail"]) for x in (bad or warn or h)[:8]),
                             "The data mover host is ready." if not bad and not warn else "Fix what failed on the host first." if bad else "Read the warnings.", "migration_host")
        elif c["id"] == "P5" and byid.get("C2"):
            x = byid["C2"][0]
            out[i] = rec("P5", "pass" if x["status"] == "pass" else "warn" if x["status"] == "warn" else "fail", "cinder.conf, through the Cinder helper", x["detail"],
                         "The name templates are the defaults the method assumes." if x["status"] == "pass" else x["hint"], "cinder_host")
    return out


ACCESS_LEVELS = ("api", "full")
NEEDS_ARRAYS = ("P12", "P14", "P15")      # checks that only the staged method (class C) needs: the source array account, the target namespace, the clone call


def run_all(platform, inventory, servers=None, root_volumes=(), source_backend=None, target_backend=None, previous_ids=None, clearances=None, marks=None,
            access="api", host_checks=None):
    """All the pre-flight checks. Returns the result stored for the gate and the reports.

    access is what OpenMove can reach. "api": the OpenStack APIs only. Classes A and B are moved by Cinder itself (a retype), so they need nothing else;
    class C needs a data mover host with both arrays and is NOT admitted. "full": also the hosts and the arrays, so class C is admitted."""
    if access not in ACCESS_LEVELS:
        raise ValueError("access must be one of %s" % (ACCESS_LEVELS,))
    free = None
    if target_backend:
        pools = [p for p in platform.get("backends", []) if p.get("backend") == target_backend and p.get("free_gb") is not None]
        free = int(sum(float(p["free_gb"]) for p in pools)) if pools else None
    plat = platform_checks(platform, source_backend, target_backend)
    if host_checks:
        plat = apply_host_checks(plat, host_checks)
    if access != "full":
        for i, c in enumerate(plat):
            if c["id"] in NEEDS_ARRAYS:
                plat[i] = rec(c["id"], "info", "Not needed for this level of access",
                              "OpenMove has the OpenStack APIs only. This check is needed only by the staged method for class C volumes, which are not migrated at this level.",
                              "Not applicable. Classes A and B are moved by Cinder itself (a retype).", c["source"])
    risk_map = risk_mod.assess_all(inventory["volumes"], servers, root_volumes, marks)
    cleared = {v: s for v, s in (clearances or {}).items() if v in risk_map}
    vol_checks, per_volume = volume_checks(inventory, servers, root_volumes, target_backend, free, previous_ids, risk_map, cleared)
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)
    allc = plat + vol_checks
    counts = {s: sum(1 for c in allc if c["status"] == s) for s in STATUSES}
    clean = [v for v in inventory["volumes"] if v.get("class") in ("A", "B", "C") and not any(s == "fail" for s in per_volume[v["id"]].values())]
    no_mover = [v["id"] for v in clean if v.get("class") == "C" and access != "full"]
    on_target = [v["id"] for v in clean if target_backend and v.get("backend") == target_backend]      # nothing to move
    admitted = [v["id"] for v in clean if v["id"] not in set(no_mover) and v["id"] not in set(on_target)]
    return {"run_id": run_id, "ran_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "source_backend": source_backend,
            "target_backend": target_backend, "fingerprint": fingerprint(source_backend, target_backend, [v["id"] for v in inventory["volumes"]]),
            "platform": plat, "volume": vol_checks, "later": later_phase_checks(), "counts": counts,
            "volumes_total": len(inventory["volumes"]), "admitted": admitted, "access": access, "no_mover": no_mover, "on_target": on_target,
            "volume_status": {vid: worst(list(s.values())) for vid, s in per_volume.items()},
            "volume_ids": [v["id"] for v in inventory["volumes"]],
            "risk": {vid: dict(a, signature=risk_mod.signature(a), cleared=cleared.get(vid) == risk_mod.signature(a)) for vid, a in risk_map.items()}}
