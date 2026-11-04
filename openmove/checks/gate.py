# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The pre-flight gate (G1): nothing that changes the cloud starts until every prerequisite is met.

A migration may start only when all of these hold for the latest pre-flight run:

  1. no platform check has failed (a failed check is fixed, never waived);
  2. every check that warned or could not be checked has an attestation: an operator's recorded
     statement, with their name and the evidence, that the prerequisite is met or accepted;
  3. at least one volume is admitted (classes A to C with no failed volume check);
  4. two different people have signed off the run, one as operator and one as reviewer (four eyes);
  5. the run is not stale: it is newer than the time limit, and a later run replaced no earlier sign-off.

Attestations and sign-offs are journal events, so they appear in the reports and cannot be edited.
A new run needs new sign-offs; attestations carry over while the source and target back-end are the same.
"""
from datetime import datetime, timezone

MAX_AGE_HOURS = 24
ROLES = ("operator", "reviewer")


class GateClosed(Exception):
    """Raised by require_open() when the gate does not allow a migration to start."""


def _pair(source, target):
    return "%s>%s" % (source or "", target or "")


def _parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def attest(journal, source, target, check_id, by, note):
    by, note = (by or "").strip(), (note or "").strip()
    if not by:
        raise ValueError("an attestation needs the name of the person making it")
    if len(note) < 8:
        raise ValueError("an attestation needs the evidence or reason, in at least a few words")
    return journal.record("attestation", check_id, {"pair": _pair(source, target), "by": by, "note": note, "withdrawn": False})


def withdraw(journal, source, target, check_id, by):
    return journal.record("attestation", check_id, {"pair": _pair(source, target), "by": (by or "").strip(), "note": "", "withdrawn": True})


def attestations(journal, source, target):
    """Current attestations for the back-end pair: {check_id: {by, note, at}}."""
    pair, seen, out = _pair(source, target), set(), {}
    for e in journal.events("attestation", None, 5000):  # newest first: the first one seen for a check wins
        if e["data"].get("pair") != pair or e["subject"] in seen:
            continue
        seen.add(e["subject"])
        if not e["data"].get("withdrawn"):
            out[e["subject"]] = {"by": e["data"]["by"], "note": e["data"]["note"], "at": e["ts"]}
    return out


def sign(journal, run_id, role, by):
    by = (by or "").strip()
    if role not in ROLES:
        raise ValueError("role must be one of: " + ", ".join(ROLES))
    if not by:
        raise ValueError("a sign-off needs the name of the person signing")
    return journal.record("signoff", run_id, {"role": role, "by": by})


def signoffs(journal, run_id):
    """Sign-offs of one run: {role: {by, at}} (the latest of each role)."""
    out = {}
    for e in journal.events("signoff", run_id, 100):
        out.setdefault(e["data"]["role"], {"by": e["data"]["by"], "at": e["ts"]})
    return out


def _item(c):
    return {"id": c["id"], "title": c["title"], "status": c["status"], "evidence": c["evidence"]}


def evaluate(result, att, sign, now=None, max_age_hours=MAX_AGE_HOURS):
    """Decide the gate for one pre-flight run. Returns the status and every reason behind it."""
    now = now or datetime.now(timezone.utc)
    blockers, open_items, attested = [], [], []
    plan_level = list(result["platform"]) + [c for c in result["volume"] if c["id"] in ("V9", "V10")]
    for c in plan_level:
        if c["status"] == "fail":
            blockers.append(_item(c))
    for c in list(result["platform"]) + list(result["volume"]):
        if c["status"] in ("warn", "not_checked"):
            a = att.get(c["id"])
            if a:
                attested.append(dict(_item(c), by=a["by"], note=a["note"], at=a["at"]))
            else:
                open_items.append(_item(c))
    if not result.get("admitted"):
        blockers.append({"id": "-", "title": "At least one volume must be admitted", "status": "fail",
                         "evidence": "No volume is in class A, B or C with every volume check passed."})

    age_h = (now - _parse(result["ran_at"])).total_seconds() / 3600.0
    stale = age_h > max_age_hours
    missing = [r for r in ROLES if r not in sign]
    same_person = len(sign) == 2 and len({s["by"].strip().lower() for s in sign.values()}) < 2

    if blockers:
        status, message = "blocked", "%d check(s) failed. Fix the cause, then run the safety checks again." % len(blockers)
    elif open_items:
        status, message = "open", "%d check(s) need a person to look at them and confirm." % len(open_items)
    elif stale:
        status, message = "stale", "The safety checks ran %.0f hours ago (the limit is %d). Run them again." % (age_h, max_age_hours)
    elif missing:
        status, message = "awaiting_signoff", "Waiting for approval from: %s." % ", ".join({"operator": "the first approver", "reviewer": "the second approver"}[r] for r in missing)
    elif same_person:
        status, message = "awaiting_signoff", "The first and the second approver must be two different people."
    else:
        status, message = "approved", "Everything is met and two different people have approved."
    return {"status": status, "message": message, "blockers": blockers, "open_items": open_items, "attested": attested,
            "signoffs": sign, "admitted": len(result.get("admitted") or []), "age_hours": round(age_h, 1),
            "max_age_hours": max_age_hours, "run_id": result["run_id"]}


def require_open(gate):
    """The migration engine calls this before it starts a batch. Fails closed."""
    if not gate or gate.get("status") != "approved":
        raise GateClosed((gate or {}).get("message") or "no pre-flight has been run")
    return True
