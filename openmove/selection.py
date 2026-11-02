# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Which volumes are migrated, and with which settings (requirement R16).

The operator chooses, volume by volume or by group, what goes into the batch. Anything not chosen is left alone and is listed in the
not-migrated report as "not selected". Rules that cannot be bent:

  * Only a volume the pre-flight admitted can be selected (class A, B or C with no failed check). A held-back volume cannot be
    forced in by selection; the reason it is held back is shown instead.
  * By default every admitted volume is selected, and every option has a safe default.
  * Options can make the process stricter (more rechecks, a longer gap, a longer soak, a lower rate, a pause before cutover) and
    can be loosened only down to the floors fixed in the process (two rechecks, five minutes apart, one day of soak). A value
    outside the range is refused, not clamped, so nobody believes a setting is in force that is not.
  * Every change to the selection or the options is written to the journal with who made it.

Nothing here talks to a cloud.
"""
import math

DEFAULTS = {
    "concurrency": 8,              # volumes in flight at once
    "rate_cap_mb_s": 200,          # copy rate cap per host
    "rechecks": 2,                 # passed rechecks needed before a cutover
    "recheck_gap_min": 5,          # minutes between the last two rechecks
    "soak_days": 7,                # days the source is kept after the cutover
    "hold_before_cutover": True,   # volumes wait, in sync, until a person releases them
    "stop_after_failures": 10,     # halt automatically when this many volumes failed in one run; 0 = never
}
DEMO_DEFAULTS = dict(DEFAULTS, concurrency=16, rate_cap_mb_s=600)

# (low, high, meaning). The low end of rechecks, recheck_gap_min and soak_days is the floor of the process.
LIMITS = {
    "concurrency": (1, 32, "Volumes copied at the same time. Lower is gentler on production."),
    "rate_cap_mb_s": (10, 2000, "Copy rate cap per host, MB/s. Lower is gentler on production."),
    "rechecks": (2, 5, "Passed rechecks in a row before a cutover. Two is the minimum of the process."),
    "recheck_gap_min": (5, 120, "Minutes between the last two rechecks. Five is the minimum of the process."),
    "soak_days": (1, 90, "Days the source is kept untouched after the cutover. A rollback needs it."),
    "stop_after_failures": (0, 500, "Halt automatically when this many volumes failed in one run. 0 turns it off."),
}
BOOLEANS = ("hold_before_cutover",)

LABELS = {
    "concurrency": "Volumes at once", "rate_cap_mb_s": "Copy rate cap (MB/s per host)", "rechecks": "Rechecks before cutover",
    "recheck_gap_min": "Minutes between rechecks", "soak_days": "Soak period (days)", "hold_before_cutover": "Hold each volume until I release it for cutover",
    "stop_after_failures": "Halt automatically after this many failed volumes (0 = never)"}


class SelectionError(ValueError):
    pass


def defaults(demo=False):
    return dict(DEMO_DEFAULTS if demo else DEFAULTS)


def check_options(opts, demo=False):
    """Validate a (partial) options dict against the ranges. Returns the full options with defaults filled in. Refuses, never clamps."""
    out = defaults(demo)
    for k, v in (opts or {}).items():
        if k in BOOLEANS:
            if not isinstance(v, bool):
                raise SelectionError("%s must be true or false" % k)
            out[k] = v
        elif k in LIMITS:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
                raise SelectionError("%s must be a whole number" % k)
            lo, hi, _ = LIMITS[k]
            if not lo <= v <= hi:
                raise SelectionError("%s must be between %d and %d; got %d. %s" % (k, lo, hi, v, LIMITS[k][2]))
            out[k] = int(v)
        else:
            raise SelectionError("unknown option %r" % k)
    return out


def rules(opts):
    """The part of the options the lifecycle enforces."""
    return {"min_rechecks": int(opts["rechecks"]), "recheck_gap": int(opts["recheck_gap_min"]) * 60, "soak_days": int(opts["soak_days"])}


def gap_ticks(opts, step_minutes):
    return max(2, math.ceil(int(opts["recheck_gap_min"]) / float(step_minutes)))


# ---- groups ----------------------------------------------------------------------------------------------------------

def _band(size):
    return "up to 50 GiB" if size <= 50 else "51 to 200 GiB" if size <= 200 else "201 to 1000 GiB" if size <= 1000 else "over 1000 GiB"


BAND_ORDER = ["up to 50 GiB", "51 to 200 GiB", "201 to 1000 GiB", "over 1000 GiB"]
KINDS = (("class", "Class"), ("project", "Project"), ("attachment", "Attachment"), ("size", "Size"), ("snapshots", "Snapshots"),
         ("host", "Cinder host"), ("instance", "Instance"), ("risk", "Workload risk"))


def _risk_key(v, servers):
    r = (servers.get("__risk__") or {}).get(v["id"])
    return "high risk, cleared" if r and r["level"] == "high" else "medium risk" if r else "no known risk"


def _keys(v, names, servers):
    snaps = int(v.get("snapshots") or 0)
    atts = [a.get("server_id") for a in v.get("attachments", []) if a.get("server_id")]
    return {"class": [v.get("class") or "?"],
            "project": [names.get(v.get("project_id")) or str(v.get("project_id") or "none")[:8]],
            "attachment": ["attached" if v.get("attachments") else "detached"] + (["boot volume"] if v.get("id") in servers.get("__roots__", ()) else []),
            "size": [_band(int(v.get("size") or 0))],
            "snapshots": ["none" if snaps == 0 else "1 to 5" if snaps <= 5 else "6 to 20" if snaps <= 20 else "more than 20"],
            "host": [v.get("host") or v.get("backend") or "unknown"],
            "instance": [((servers.get(s) or {}).get("name") or s[:8]) for s in atts] or ["none"], "risk": [_risk_key(v, servers)]}


def groups(inventory, names, servers, roots, eligible, risk=None):
    """{kind: [{'key', 'count', 'eligible', 'gib'}]} over the inventory. eligible is the set of admitted volume ids."""
    sv = dict(servers or {})
    sv["__roots__"] = set(roots or ())
    sv["__risk__"] = risk or {}
    out = {k: {} for k, _ in KINDS}
    for v in inventory["volumes"]:
        for kind, keys in _keys(v, names or {}, sv).items():
            for key in keys:
                g = out[kind].setdefault(key, {"key": key, "count": 0, "eligible": 0, "gib": 0})
                g["count"] += 1
                if v["id"] in eligible:
                    g["eligible"] += 1
                    g["gib"] += int(v.get("size") or 0)
    res = {}
    for kind, _ in KINDS:
        items = list(out[kind].values())
        if kind == "size":
            items.sort(key=lambda g: BAND_ORDER.index(g["key"]))
        else:
            items.sort(key=lambda g: (-g["eligible"], g["key"]))
        res[kind] = items
    return res


def members(inventory, names, servers, roots, kind, key, eligible, risk=None):
    """Ids of eligible volumes in one group."""
    if kind not in dict(KINDS):
        raise SelectionError("unknown group kind %r" % kind)
    sv = dict(servers or {})
    sv["__roots__"] = set(roots or ())
    sv["__risk__"] = risk or {}
    return [v["id"] for v in inventory["volumes"] if v["id"] in eligible and key in _keys(v, names or {}, sv)[kind]]


def apply(current, eligible, action, ids):
    """New selection (a set). current is the set now. ids are the volumes the action is about (already limited to what exists)."""
    ids = set(ids)
    if action == "set":
        return ids & eligible
    if action == "add":
        return (set(current) | ids) & eligible
    if action == "remove":
        return set(current) - ids
    if action == "reset":
        return set(eligible)
    raise SelectionError("action must be set, add, remove or reset")


# ---- the state of the running tool (duck typed: server.AppState) -------------------------------------------------------

def eligible_ids(state):
    return set((state.checks or {}).get("admitted") or [])


def selected_ids(state):
    """The volumes that will be migrated, in inventory order. By default every admitted volume; never a volume the pre-flight did not admit."""
    el = eligible_ids(state)
    # the default leaves out every volume that needed a risk clearance: it is selected only on purpose
    high = {v for v, r in ((state.checks or {}).get("risk") or {}).items() if r.get("level") == "high"}
    chosen = (el - high) if state.selection is None else (set(state.selection) & el)
    return [v["id"] for v in (state.inventory or {}).get("volumes", []) if v["id"] in chosen]


def options_of(state):
    return check_options(state.options or {}, state.demo)
