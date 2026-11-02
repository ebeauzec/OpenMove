# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Infer the OpenStack release from the API microversions the services report (R9).

The table is data (resources/releases.json). Each release lists the highest microversion
of the block storage and compute APIs in that release. A cloud reports the maximum of the
code it runs, so the maximum identifies the release, with two limits that this module
reports instead of hiding:

* several releases can share a maximum (for example Cinder 3.59 is both Stein and Train);
* a vendor build can report a value that matches no release exactly.

When the signals disagree, the oldest reading is used, because the safe behaviour for an
older release is also safe on a newer one.
"""
import json

from . import paths


def parse_version(text):
    """'3.50' -> (3, 50). Returns None for anything that is not major.minor."""
    try:
        major, minor = str(text).strip().split(".")
        return int(major), int(minor)
    except (ValueError, AttributeError):
        return None


def load_table():
    with open(paths.resource_dir() / "releases.json", encoding="utf-8") as fh:
        return json.load(fh)["releases"]


def _signal(table, key, observed):
    """Return (candidate release names, exact?, note) for one service."""
    v = parse_version(observed)
    if v is None:
        return [], False, "no usable %s version" % key
    known = [(parse_version(r[key]), r["name"]) for r in table if r.get(key)]
    exact = [name for ver, name in known if ver == v]
    if exact:
        return exact, True, None
    highest = max(ver for ver, _ in known)
    lowest = min(ver for ver, _ in known)
    if v > highest:
        return [], False, "%s %s is newer than the newest release in the table" % (key.replace("_max", ""), observed)
    if v < lowest:
        return [], False, "%s %s is older than the oldest release in the table" % (key.replace("_max", ""), observed)
    upper = min((ver, name) for ver, name in known if ver > v)
    names = [name for ver, name in known if ver == upper[0]]
    return names, False, "%s %s matches no release exactly; reading it as %s" % (
        key.replace("_max", ""), observed, "/".join(names))


def infer(cinder_max, nova_max, table=None):
    """Return {'candidates': [...], 'release': str|None, 'confidence': str, 'notes': [...]}.

    confidence is 'exact', 'approximate', 'disagree', 'newer_than_known' or 'unknown'.
    'release' is the oldest candidate, which is the one the profile is looked up for.
    """
    table = table or load_table()
    order = [r["name"] for r in table]
    notes = []
    sets = []
    exact_all = True
    newer = False
    for key, observed in (("cinder_max", cinder_max), ("nova_max", nova_max)):
        if observed is None:
            notes.append("no %s version reported" % key.replace("_max", ""))
            continue
        names, exact, note = _signal(table, key, observed)
        if note:
            notes.append(note)
            if "newer than" in note:
                newer = True
        if names:
            sets.append(set(names))
            exact_all = exact_all and exact
    if not sets:
        conf = "newer_than_known" if newer else "unknown"
        return {"candidates": [], "release": None, "confidence": conf, "notes": notes}
    inter = set.intersection(*sets)
    if inter:
        cands = sorted(inter, key=order.index)
        conf = "exact" if exact_all else "approximate"
        if newer:
            notes.append("one service is newer than the table; the reading uses the other")
            conf = "approximate"
    else:
        union = set.union(*sets)
        cands = [min(union, key=order.index)]
        conf = "disagree"
        notes.append("block storage and compute point to different releases (%s); using the oldest" % (
            " / ".join("/".join(sorted(s, key=order.index)) for s in sets)))
    return {"candidates": cands, "release": cands[0], "confidence": conf, "notes": notes}
