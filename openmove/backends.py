# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Back-end families and the support matrix (requirement R8).

The engine never tests for a vendor name. It asks this module what a back-end is and whether a pair has been verified. The data is in
resources/backends.json. A back-end the data does not know is unsupported for every pair, and the reason is given. A known pair that nobody
has verified is allowed to be checked and planned, but is reported as not verified, and a named person must attest it.
"""
import json
from importlib import resources

_DATA = None


def data():
    global _DATA
    if _DATA is None:
        _DATA = json.loads(resources.files("openmove.resources").joinpath("backends.json").read_text(encoding="utf-8"))
    return _DATA


def family_of(vendor, protocol=None):
    """The family key of a back-end from the vendor and protocol its driver reports, or None."""
    v, p = (vendor or "").strip().lower(), (protocol or "").strip().lower()
    best = None
    for key, f in data()["families"].items():
        if v in f["vendors"] and (not f["protocols"] or p in f["protocols"]):
            if best is None or f["protocols"]:                      # a family that names the protocol is more specific than one that does not
                best = key
    return best


def title(key):
    return data()["families"].get(key, {}).get("title") or "unknown"


def verification(src, dst):
    """The verification entry that covers this pair, or None."""
    for v in data()["verified"]:
        if v["from"] == src and v["to"] == dst:
            return v
    return None


def assess(platform, source_backend, target_backend):
    """{'status': 'pass'|'warn'|'fail', 'evidence': str, 'source': family, 'target': family, 'verified': entry|None} for a pair of back-ends."""
    fams, problems = {}, []
    for role, be in (("source", source_backend), ("target", target_backend)):
        pools = [p for p in (platform.get("backends") or []) if p.get("backend") == be]
        if not pools:
            problems.append("%s back-end %s: no pool reported, so the driver cannot be identified" % (role, be))
            fams[role] = None
            continue
        p = pools[0]
        fam = family_of(p.get("vendor"), p.get("protocol"))
        fams[role] = fam
        if fam is None:
            problems.append("%s back-end %s is unsupported: its driver reports vendor %r and protocol %r, which match no family OpenMove knows (%s)" % (
                role, be, p.get("vendor"), p.get("protocol"), ", ".join(f["title"] for f in data()["families"].values())))
    if problems:
        return {"status": "fail", "evidence": "; ".join(problems), "source": fams.get("source"), "target": fams.get("target"), "verified": None}
    ver = verification(fams["source"], fams["target"])
    names = "%s to %s" % (title(fams["source"]), title(fams["target"]))
    if ver:
        ev = "%s: method known, verified in %s (%s)." % (names, ver["where"], ver["covers"])
        return {"status": "warn", "evidence": ev + " That proves the OpenStack side only, not this cloud's release or arrays: record Phase 0 for them.",
                "source": fams["source"], "target": fams["target"], "verified": ver}
    return {"status": "warn", "evidence": "%s: the method is known (%s) but this pair has not been verified anywhere. Phase 0 must verify it before a real move." % (
        names, data()["method"].split(",")[0]), "source": fams["source"], "target": fams["target"], "verified": None}


def matrix():
    """Rows of the support matrix: every ordered pair of known families, with the method and the verification status."""
    d = data()
    rows = []
    for a, fa in d["families"].items():
        for b, fb in d["families"].items():
            ver = verification(a, b)
            rows.append({"from": fa["title"], "to": fb["title"], "class_a_b": "retype, on-demand migration (APIs only)", "class_c": "staged method, not built",
                         "verified": ("lab only: " + ver["where"]) if ver else "not verified"})
    return rows
