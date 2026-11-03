# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Checks on fresh reads, and the list of calls a dry run shows. Nothing here talks to a cloud: it works on what the caller just read.

A check is {"name", "ok", "detail", "wait"}. wait is true when the check is not met YET and may be met later without anyone doing
anything (a class B volume whose snapshots are still rotating away): the volume waits instead of being given up.
"""

MIGRATING_OK = (None, "", "none", "success")        # a finished earlier migration leaves "success" behind; any other value is a move in flight
REPLICATION_OK = (None, "", "disabled", "not-capable")


def backend_of(host):
    """'ctl@sf-1#pool' -> 'sf-1'."""
    h = str(host or "")
    h = h.split("@", 1)[1] if "@" in h else h
    return h.split("#", 1)[0]


def attached_servers(vol):
    return sorted({a.get("server_id") for a in (vol.get("attachments") or []) if a.get("server_id")})


def target_type_for(volume_type, type_map, backend_types, target_backend):
    """The volume type to retype to. type_map {source type: target type} is the operator's choice; without one, a target type is used
    only if exactly one type belongs to the target back-end. Returns (type, why not)."""
    if type_map and volume_type in type_map:
        return type_map[volume_type], None
    if type_map and "*" in type_map:
        return type_map["*"], None          # one target type for every source type
    names = sorted(t["name"] for t in backend_types if (t.get("extra_specs") or {}).get("volume_backend_name") == target_backend)
    if len(names) == 1:
        return names[0], None
    if not names:
        return None, "no volume type is bound to the target back-end %s" % target_backend
    return None, "several volume types belong to the target back-end (%s); choose one for type %s" % (", ".join(names), volume_type)


def validate(vol, snaps, servers, spec):
    """spec: {class, source_backend, target_backend, target_type, types: [volume types]}. servers: {id: server or None}."""
    out = []

    def add(name, ok, good, bad=None, wait=False):
        out.append({"name": name, "ok": bool(ok), "detail": good if ok or bad is None else bad, "wait": bool(wait and not ok)})

    host = backend_of(vol.get("os-vol-host-attr:host"))
    add("The volume is on the source back-end", host == spec["source_backend"],
        "on %s" % host, "the volume is on %s, not on %s" % (host or "an unknown back-end", spec["source_backend"]))
    add("The volume is in a state that can be retyped", vol.get("status") in ("available", "in-use"),
        "status %s" % vol.get("status"), "status is %s; only available and in-use volumes are moved" % vol.get("status"))
    mig = vol.get("os-vol-mig-status-attr:migstat")
    add("No move is in flight", mig in MIGRATING_OK, "migration status %s" % (mig or "none"), "migration status is %s: another move is in flight or failed" % mig)
    n = len(snaps or [])
    if not spec.get("staged"):           # the staged method exists for the volumes that have snapshots
        add("No snapshot left", n == 0, "no snapshots",
            "%d snapshot(s) remain; Cinder moves a volume with snapshots only by copying, so it is held until they are gone" % n, wait=spec.get("class") == "B")
    add("Not encrypted", not vol.get("encrypted"), "not encrypted", "the volume is encrypted")
    add("Not multi-attach", not vol.get("multiattach"), "single attach", "the volume is multi-attach")
    add("Not replicated", vol.get("replication_status") in REPLICATION_OK, "replication %s" % (vol.get("replication_status") or "off"),
        "replication status is %s" % vol.get("replication_status"))
    add("Not in a group", not (vol.get("group_id") or vol.get("consistencygroup_id")), "no group", "the volume belongs to a group")
    tt = spec.get("target_type")
    names = [t["name"] for t in spec.get("types") or []]
    add("The target volume type exists", bool(tt) and tt in names, "type %s" % tt,
        spec.get("why_no_type") or "the target type %r does not exist" % tt)
    if tt and tt in names:
        tb = next((t.get("extra_specs") or {}).get("volume_backend_name") for t in spec["types"] if t["name"] == tt)
        add("The target type is bound to the target back-end", tb == spec["target_backend"], "type %s -> %s" % (tt, tb),
            "type %s is bound to %s, not to %s" % (tt, tb, spec["target_backend"]))
        add("The volume does not already have the target type", vol.get("volume_type") != tt, "now %s" % vol.get("volume_type"),
            "the volume already has type %s" % tt)
    for sid in attached_servers(vol):
        s = servers.get(sid)
        if s is None:
            add("Instance %s can be read" % sid, False, "", "the instance %s could not be read" % sid)
            continue
        add("Instance %s lock state is known" % sid, isinstance(s.get("locked"), bool), "locked" if s.get("locked") else "not locked",
            "the answer does not show whether the instance is locked (Nova microversion 2.9 or later is needed); an instance is never locked blind")
        add("Instance %s is running" % sid, s.get("status") == "ACTIVE", "ACTIVE", "the instance is %s; only running instances are handled" % s.get("status"))
        ps = s.get("OS-EXT-STS:power_state")
        add("Instance %s is really running" % sid, ps in (None, 1), "power state %s" % ("not shown" if ps is None else "running"),
            "Nova says %s but the guest is not running (power state %s); a volume swap needs a running guest" % (s.get("status"), ps))
        add("Instance %s is idle" % sid, not s.get("OS-EXT-STS:task_state"), "no task", "the instance is busy: %s" % s.get("OS-EXT-STS:task_state"))
    return out


def verdict(checks):
    """'ok', 'wait' (only waits are open) or 'blocked'."""
    bad = [c for c in checks if not c["ok"]]
    if not bad:
        return "ok"
    return "wait" if all(c["wait"] for c in bad) else "blocked"


def planned_calls(vol, spec, servers):
    """The changes a move would make, in order, each with its undo. Used by the dry run and shown before anything is sent."""
    calls, locked = [], False
    for sid in attached_servers(vol):
        if (servers.get(sid) or {}).get("locked"):
            calls.append({"step": "none", "what": "instance %s is already locked by someone else; it is left locked and not unlocked" % sid, "undo": "nothing"})
        else:
            locked = True
            calls.append({"step": "lock", "what": "POST /servers/%s/action {\"lock\": null}" % sid, "undo": "unlock the instance"})
    calls.append({"step": "retype", "what": "POST /volumes/%s/action {\"os-retype\": {\"new_type\": \"%s\", \"migration_policy\": \"on-demand\"}}" % (vol["id"], spec.get("target_type")),
                  "undo": "retype back to %s (R3)" % vol.get("volume_type")})
    for sid in attached_servers(vol):
        if locked and not (servers.get(sid) or {}).get("locked"):
            calls.append({"step": "unlock", "what": "POST /servers/%s/action {\"unlock\": null} after the post-check passes" % sid, "undo": "lock the instance again"})
    return calls
