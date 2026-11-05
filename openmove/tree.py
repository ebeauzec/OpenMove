# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The volume tree: every project, its instances, and the volumes attached to each, in plain words.

This is only a different way to read the inventory. The classes A to E of the method are kept (they are the basis of every rule), but each one is
shown with a short name and a sentence a person who has not read the method can follow. Nothing here talks to a cloud or changes anything.
"""

PLAIN = {
    "A": ("Standard", "ok", "No snapshots. The storage system can move it in one step."),
    "B": ("Waits for snapshots", "warn", "Its snapshots are about to expire. OpenMove waits for them to go, then moves it like an easy one."),
    "C": ("Staged copy", "warn", "It has snapshots that must be kept, so it is copied in stages. That needs a helper server."),
    "D": ("Instance is off", "stop", "The instance it is attached to is not running. Start the instance, then read the volumes again."),
    "E": ("Not moved by this tool", "stop", "This kind of volume (for example encrypted, shared between instances or grouped) is not moved here. It is listed so a person can plan it."),
}


def plain(cls):
    t = PLAIN.get(cls) or ("Unknown", "stop", "OpenMove could not decide how to handle this volume.")
    return {"label": t[0], "level": t[1], "meaning": t[2]}


def _volume(v, roots, state):
    p = plain(v.get("class"))
    out = {"id": v["id"], "name": v.get("name") or "", "size": v.get("size"), "snapshots": v.get("snapshots") or 0, "status": v.get("status") or "",
           "backend": v.get("backend") or v.get("host") or "", "class": v.get("class"), "label": p["label"], "level": p["level"], "meaning": p["meaning"],
           "reason": v.get("reason") or "", "boot": v["id"] in roots, "device": "", "verdict": "", "verdict_level": "", "why": ""}
    out.update(state)
    return out


def build(inv, servers, names, roots=(), eligible=None, selected=None, failed=None):
    """The tree for the inventory `inv`. `eligible` and `selected` are sets of volume ids once the pre-flight has run, else None."""
    roots, failed = set(roots or ()), failed or {}
    projects = {}

    def project(pid):
        pid = pid or ""
        if pid not in projects:
            projects[pid] = {"id": pid, "name": names.get(pid) or ("Project " + str(pid)[:8] if pid else "(no project)"), "instances": {}, "loose": [], "volumes": 0, "gib": 0, "moving": 0}
        return projects[pid]

    for v in inv.get("volumes", []):
        st = {}
        if eligible is not None:
            if v["id"] not in eligible:
                st = {"verdict": "Held back", "verdict_level": "stop", "why": v.get("reason") or "A check did not pass."}
                if failed.get(v["id"]):
                    st["why"] += " Failed check: %s." % ", ".join(failed[v["id"]])
            elif v["id"] in (selected or ()):
                st = {"verdict": "Will be moved", "verdict_level": "ok"}
            else:
                st = {"verdict": "Not chosen", "verdict_level": "warn", "why": "It can be moved but is not in your selection."}
        rec = _volume(v, roots, st)
        pr = project(v.get("project_id"))
        pr["volumes"] += 1
        pr["gib"] += int(v.get("size") or 0)
        if rec["verdict"] == "Will be moved":
            pr["moving"] += 1
        atts = [a for a in v.get("attachments", []) if a.get("server_id")]
        if not atts:
            pr["loose"].append(rec)
            continue
        for a in atts:                                    # a volume shared by two instances is shown under both
            sid = a["server_id"]
            sv = servers.get(sid) or {}
            ins = pr["instances"].setdefault(sid, {"id": sid, "name": sv.get("name") or "(unknown instance %s)" % sid[:8], "status": sv.get("status") or "unknown",
                                                    "host": sv.get("host") or "", "flavor": sv.get("flavor") or "", "locked": bool(sv.get("locked")), "volumes": []})
            ins["volumes"].append(dict(rec, device=a.get("device") or ""))
    out = []
    for pr in projects.values():
        insts = sorted(pr["instances"].values(), key=lambda i: i["name"].lower())
        for i in insts:
            i["volumes"].sort(key=lambda r: (not r["boot"], r["device"], r["name"]))
        pr["instances"] = insts
        pr["loose"].sort(key=lambda r: (r["name"], r["id"]))
        out.append(pr)
    out.sort(key=lambda p: p["name"].lower())
    total = sum(p["volumes"] for p in out)
    return {"projects": out, "total": total, "gib": sum(p["gib"] for p in out), "checked": eligible is not None,
            "moving": sum(p["moving"] for p in out), "legend": [dict(plain(c), code=c) for c in sorted(PLAIN)]}
