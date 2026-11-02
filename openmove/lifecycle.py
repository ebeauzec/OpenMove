# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The only order in which a volume may be migrated (requirement R14, docs/SAFETY.md).

    precheck -> validate -> copy -> sync -> recheck (twice, apart) -> cutover -> postcheck -> soak -> done

  precheck   the pre-flight gate is approved (checks.gate)
  validate   this volume's plan is validated against the target (room, mapping, host reach)
  copy       a full copy is made to the target. The source is only read.
  sync       the target is kept in step with the source until the moment of cutover. The source is only read.
  recheck    everything is checked again, and then checked AGAIN later. One pass is not enough.
  cutover    the one step that touches the production path. Allowed only when cutover_ready() says yes.
  postcheck  the volume is checked from the new path
  soak       the source is kept, untouched, for the soak period. Rollback stays possible.
  done       only now may the source be released

Every volume carries its class (A to E, docs/CLASSES.md) from the precheck on. The class decides what is allowed, and a wrong class is
refused, never guessed: class D and E volumes never enter the order at all; class A and B volumes must have no snapshot left when they are
validated; a class C volume cannot leave the copy until every snapshot image has been compared identical and the snapshot ids match, and
each of its rechecks must show that no tenant snapshot appeared or disappeared (reconciled).

Three checkpoints of the playbook are signed per volume by a second person, one who is not the operator of the run (G2, G3, G4):

  G2  the copy is verified (same size and hash; for class C every snapshot image and id compared). Needed before the cutover.
  G3  the volume is ready to cut over (rechecks passed, source unchanged, nothing left to copy). Needed before the cutover.
  G4  the cutover is verified from the new path. Needed before the soak starts.

A sign-off is void when the volume is copied again (G2), copied again or falls back to sync (G3), or is cut over again (G4).

Rules that cannot be bent: no phase can be skipped; a failed recheck drops the volume back to sync, never forward, and
the count of passes starts again; the cutover needs two passed rechecks that are apart in time, the newest one recent, the source
unchanged between them, nothing left to copy, an approved gate right now and no halt. Anything unexpected is a refusal.
State lives in the journal, so a restart picks up exactly where it was and cannot jump ahead.
"""
from datetime import datetime, timedelta, timezone

from . import safety

KIND = "phase"
ORDER = ("precheck", "validate", "copy", "sync", "recheck", "cutover", "postcheck", "soak", "done")
# Class A and B volumes can be moved by Cinder itself (a retype with on-demand migration). Cinder and Nova copy the data and switch the guest
# in one operation that OpenMove cannot pause, so there is no copy, sync or recheck phase: the retype IS the cutover. G2 and G3 are signed
# in the validate phase, before it. Rollback is a retype back (R3).
RETYPE_ORDER = ("precheck", "validate", "cutover", "postcheck", "soak", "done")
RETYPE = "retype"
STAGED = "staged"
ABORTED, ROLLBACK = "aborted", "rollback"
MOVED_CLASSES = ("A", "B", "C")
HELD_CLASSES = {
    "D": "class D waits: its instance is not ACTIVE or PAUSED. It enters the order only after the instance runs (it is then class A or C) "
         "or an agreed alternative (admin lock and detach) is recorded",
    "E": "class E is not moved by this method (encrypted, multiattach, grouped, replicated, wrong state or a bad provider id)"}
TERMINAL = ("done", ABORTED)

GATE_KIND = "volume_gate"
VOLUME_GATES = {
    "G2": "Staging verified: the copy is the same size and hash as the source and, for class C, every snapshot image and id was compared",
    "G3": "Ready to cut over: rechecks passed, the source did not change, nothing is left to copy",
    "G4": "Cutover verified: the volume is healthy from its new path"}
GATE_CONDITIONS = {"G2": "G2 signed by a second person", "G3": "G3 signed by a second person"}

MIN_RECHECKS = 2          # passed rechecks needed before a cutover
RECHECK_GAP = 300         # seconds that must lie between the two newest rechecks
MAX_RECHECK_AGE = 900     # seconds: the newest recheck must be this recent at the moment of cutover
MAX_LAG = 0               # seconds of lag and blocks pending allowed at a recheck: none

NEXT = {None: ("precheck",), "precheck": ("validate",), "validate": ("copy",), "copy": ("sync",),
        "sync": ("sync", "recheck"), "recheck": ("sync", "recheck", "cutover"),
        "cutover": ("postcheck", ROLLBACK), "postcheck": ("soak", ROLLBACK), "soak": ("done", ROLLBACK),
        ROLLBACK: ("done",)}
# Giving up is always allowed before the cutover. After it the only way out is rollback.
ABORTABLE = ("precheck", "validate", "copy", "sync", "recheck")
NEXT_RETYPE = {None: ("precheck",), "precheck": ("validate",), "validate": ("cutover",),
               "cutover": ("postcheck", ROLLBACK), "postcheck": ("soak", ROLLBACK), "soak": ("done", ROLLBACK), ROLLBACK: ("done",)}


class LifecycleError(Exception):
    """A step was attempted out of order or without its evidence. Nothing was recorded."""


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts):
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def history(journal, volume):
    """Every phase event of one volume, oldest first."""
    return [dict(e["data"], id=e["id"]) for e in reversed(journal.events(KIND, volume, 10000))]


def current(journal, volume):
    h = history(journal, volume)
    return h[-1] if h else None


def phase_of(journal, volume):
    c = current(journal, volume)
    return c["phase"] if c else None


def klass(h):
    """The class the volume entered the order with."""
    for e in h:
        if e["phase"] == "precheck":
            return e.get("class")
    return None


def method_of(h):
    """How the volume is moved: "retype" (Cinder moves it) or "staged" (OpenMove copies it). Set by the precheck; staged when not said."""
    for e in h:
        if e["phase"] == "precheck":
            return RETYPE if e.get("method") == RETYPE else STAGED
    return STAGED


def _gate_anchor(h, gate):
    """The id of the event after which a sign-off of this gate counts. An earlier sign-off is void."""
    a = 0
    for e in h:
        if gate == "G2" and e["phase"] in ("copy", "validate"):
            a = e["id"]
        elif gate == "G3" and (e["phase"] in ("copy", "validate") or (e["phase"] == "sync" and e.get("regression"))):
            a = e["id"]
        elif gate == "G4" and e["phase"] == "cutover":
            a = e["id"]
    return a


def _operator(gate):
    return str((((gate or {}).get("signoffs") or {}).get("operator") or {}).get("by") or "").strip()


def gate_signed(journal, volume, gate_name, gate, h=None):
    """The valid sign-off of one volume gate, or None. Valid: given after the anchor, not withdrawn, by a person other than the run's operator."""
    h = history(journal, volume) if h is None else h
    anchor = _gate_anchor(h, gate_name)
    op = _operator(gate).lower()
    cur = None
    for e in reversed(journal.events(GATE_KIND, volume, 10000)):
        d = e["data"]
        if d.get("gate") != gate_name:
            continue
        if d.get("withdrawn"):
            cur = None
        else:
            cur = dict(d, id=e["id"], ts=e["ts"]) if e["id"] > anchor else None
    if cur and (not op or str(cur.get("by") or "").strip().lower() == op):
        return None                        # no operator known, or the operator signed their own work: it does not count
    return cur


def sign_gate(journal, volume, gate_name, by, evidence, gate, withdraw=False, now=None, rules=None):
    """One person's sign-off (or withdrawal) of G2, G3 or G4 for one volume. Refused unless the volume is at the right point."""
    if gate_name not in VOLUME_GATES:
        raise LifecycleError("the volume gates are G2, G3 and G4")
    by, evidence = str(by or "").strip(), str(evidence or "").strip()
    if len(by) < 3:
        raise LifecycleError("a sign-off needs the name of the person who signs")
    op = _operator(gate)
    if not op:
        raise LifecycleError("the pre-flight gate has no operator sign-off, so a second person cannot be told apart from the operator")
    if by.lower() == op.lower():
        raise LifecycleError("%s must be signed by a second person, not by the operator (%s)" % (gate_name, op))
    h = history(journal, volume)
    cur = h[-1]["phase"] if h else None
    if not withdraw:
        if len(evidence) < 8:
            raise LifecycleError("a sign-off needs a note of what was checked (at least 8 characters)")
        retype = method_of(h) == RETYPE
        where = ("validate",) if retype else ("sync", "recheck")
        if gate_name == "G2" and cur not in where:
            raise LifecycleError("G2 is signed %s, while the volume is in %s; it is in %s" % (
                "after validation, before the retype" if retype else "after the copy is verified", " or ".join(where), cur or "no phase"))
        if gate_name == "G3":
            if cur not in where:
                raise LifecycleError("G3 is signed while the volume is in %s; it is in %s" % (" or ".join(where), cur or "no phase"))
            bad = [c["detail"] for c in readiness(journal, volume, gate, now, rules)
                   if not c["ok"] and c["name"] not in GATE_CONDITIONS.values() and c["name"] != "Newest recheck is recent"]
            if bad:
                raise LifecycleError("G3 cannot be signed yet: " + "; ".join(bad))
        if gate_name == "G4" and cur != "postcheck":
            raise LifecycleError("G4 is signed after the cutover, while the volume is in postcheck; it is in %s" % (cur or "no phase"))
    journal.record("volume_gate", volume, {"gate": gate_name, "by": by, "evidence": evidence, "withdrawn": bool(withdraw)})


def gates_of(journal, volume, gate, h=None):
    """{'G2': event or None, ...} for the screen."""
    h = history(journal, volume) if h is None else h
    return {g: gate_signed(journal, volume, g, gate, h) for g in VOLUME_GATES}


def _passed_rechecks(h):
    """Passed rechecks in a row, newest last. A failed recheck, a drop back to sync or a new copy clears the count."""
    out = []
    for e in h:
        if e["phase"] == "recheck":
            out = out + [e] if e.get("ok") else []
        elif e["phase"] == "copy" or (e["phase"] == "sync" and e.get("regression")):
            out = []
    return out


def readiness(journal, volume, gate, now=None, rules=None):
    """Every condition for a cutover, one by one: [{'name', 'ok', 'detail', 'pending'}]. The screen shows this list.

    rules can only make the check stricter: more passed rechecks or a longer gap than the process minimum, never fewer or shorter.
    A condition that cannot be judged yet (for example the gap, before two rechecks have passed) is not ok and is marked pending."""
    now = now or datetime.now(timezone.utc)
    rules = rules or {}
    need = max(MIN_RECHECKS, int(rules.get("min_rechecks") or 0))
    gap_min = max(RECHECK_GAP, int(rules.get("recheck_gap") or 0))
    out = []

    def add(name, ok, good, bad, pending=False):
        out.append({"name": name, "ok": bool(ok), "detail": good if ok else bad, "pending": bool(pending and not ok)})

    try:
        h = history(journal, volume)
        if method_of(h) == RETYPE:
            return _readiness_retype(journal, volume, gate, h, out, add)
        in_phase = bool(h) and h[-1]["phase"] in ("recheck", "sync")
        add("In the recheck stage", in_phase, "the volume is in %s" % h[-1]["phase"] if h else "",
            "the volume is in phase %s, not in recheck" % (h[-1]["phase"] if h else "none"))
        halt = safety.halted(journal)
        add("No halt in force", not halt, "no halt", "all changes are halted by %s: %s" % ((halt or {}).get("by"), (halt or {}).get("reason")))
        gate_ok = bool(gate) and gate.get("status") == "approved"
        add("Gate approved right now", gate_ok, "the pre-flight gate is approved",
            "the pre-flight gate is not approved: %s" % ((gate or {}).get("message") or "no pre-flight has run"))
        passes = _passed_rechecks(h)
        enough = len(passes) >= need
        add("Rechecks passed in a row", enough, "%d passed, %d needed" % (len(passes), need),
            "%d passed recheck(s); %d are needed, one after the other" % (len(passes), need))
        last = passes[-need:] if enough else []
        pend = not enough
        gaps_ok, gap_txt = True, "no pair to compare yet"
        for x, y in zip(last, last[1:]):
            g = (_parse(y["at"]) - _parse(x["at"])).total_seconds()
            gap_txt = "two rechecks are only %d s apart; they must be at least %d s apart" % (g, gap_min)
            if g < gap_min:
                gaps_ok = False
                break
        add("Rechecks far enough apart", gaps_ok and enough, "every pair is at least %d s apart" % gap_min, gap_txt if enough else "not judged yet: needs %d passed rechecks" % need, pend)
        age = (now - _parse(last[-1]["at"])).total_seconds() if last else None
        add("Newest recheck is recent", age is not None and age <= MAX_RECHECK_AGE, "the newest is %d s old (limit %d s)" % (age or 0, MAX_RECHECK_AGE),
            "the newest recheck is %d s old; it must be at most %d s old. Check again." % (age, MAX_RECHECK_AGE) if age is not None else "not judged yet", pend)
        fps = {e.get("source_fingerprint") for e in last}
        add("Source unchanged between them", enough and len(fps) == 1 and bool(next(iter(fps))), "the source fingerprint is identical at every recheck",
            "the source changed between the rechecks or was not fingerprinted" if enough else "not judged yet", pend)
        waiting = any((e.get("lag_s") or 0) > MAX_LAG or (e.get("pending_blocks") or 0) > 0 for e in last)
        add("Nothing left to copy", enough and not waiting, "no lag and no blocks pending at any recheck",
            "data was still waiting to be copied at a recheck" if enough else "not judged yet", pend)
        for g, name in GATE_CONDITIONS.items():
            s = gate_signed(journal, volume, g, gate, h)
            add(name, s, "signed by %s" % (s or {}).get("by"), "%s is not signed by a second person (%s)" % (g, VOLUME_GATES[g].split(":")[0].lower()))
    except Exception as exc:  # fail closed
        out.append({"name": "Decision", "ok": False, "detail": "cutover_ready could not decide (%s: %s)" % (type(exc).__name__, exc), "pending": False})
    return out


def _readiness_retype(journal, volume, gate, h, out, add):
    """The conditions for a retype: validated, no halt, gate approved, G2 and G3 signed by a second person. There are no rechecks:
    Cinder and Nova copy and switch in one operation."""
    add("In the validate stage", bool(h) and h[-1]["phase"] == "validate", "the volume is validated",
        "the volume is in phase %s, not in validate" % (h[-1]["phase"] if h else "none"))
    halt = safety.halted(journal)
    add("No halt in force", not halt, "no halt", "all changes are halted by %s: %s" % ((halt or {}).get("by"), (halt or {}).get("reason")))
    gate_ok = bool(gate) and gate.get("status") == "approved"
    add("Gate approved right now", gate_ok, "the pre-flight gate is approved",
        "the pre-flight gate is not approved: %s" % ((gate or {}).get("message") or "no pre-flight has run"))
    for g, name in GATE_CONDITIONS.items():
        s = gate_signed(journal, volume, g, gate, h)
        add(name, s, "signed by %s" % (s or {}).get("by"), "%s is not signed by a second person (%s)" % (g, VOLUME_GATES[g].split(":")[0].lower()))
    return out


def cutover_ready(journal, volume, gate, now=None, rules=None):
    """(ok, reasons). The check that stands between a synchronised volume and the production path."""
    conds = readiness(journal, volume, gate, now, rules)
    return all(c["ok"] for c in conds), [c["detail"] for c in conds if not c["ok"] and not c["pending"]]


def enter(journal, volume, phase, evidence=None, gate=None, now=None, rules=None):
    """Move one volume to the next phase if, and only if, the order and the evidence allow it. Returns the recorded event.

    rules (from the operator's options, openmove.selection.rules) can only make the checks stricter."""
    evidence = dict(evidence or {})
    now = now or datetime.now(timezone.utc)
    cur = phase_of(journal, volume)
    if cur in TERMINAL:
        raise LifecycleError("%s is %s; nothing more can be done with it" % (volume, cur))
    if phase == ABORTED:
        if cur not in ABORTABLE and cur is not None:
            raise LifecycleError("after the cutover the only way back is rollback, not abort")
        if len(str(evidence.get("reason") or "")) < 8:
            raise LifecycleError("an abort needs a reason")
        return _write(journal, volume, ABORTED, evidence, now)
    h0 = history(journal, volume)
    retype = method_of(h0) == RETYPE if h0 else evidence.get("method") == RETYPE
    if phase not in (NEXT_RETYPE if retype else NEXT).get(cur, ()):
        raise LifecycleError("%s cannot go from %s to %s; the order is %s" % (
            volume, cur or "nothing", phase, " > ".join(RETYPE_ORDER if retype else ORDER)))

    if phase == "precheck":
        if not gate or gate.get("status") != "approved":
            raise LifecycleError("the pre-flight gate must be approved")
        evidence["gate_run"] = gate.get("run_id")
        cls = evidence.get("class")
        if cls in HELD_CLASSES:
            raise LifecycleError(HELD_CLASSES[cls])
        if cls not in MOVED_CLASSES:
            raise LifecycleError("the volume class (A, B or C) must be given; got %r" % (cls,))
        if evidence.get("method") not in (None, RETYPE, STAGED):
            raise LifecycleError("the method must be retype or staged")
        if evidence.get("method") == RETYPE and cls not in ("A", "B"):
            raise LifecycleError("only a class A or B volume can be moved by retype; a class C volume needs the staged method")
    elif phase == "validate":
        cls = klass(history(journal, volume))
        if evidence.get("room_ok") is False:
            raise LifecycleError("the target has no room for this volume")
        if cls in ("A", "B"):
            left = evidence.get("snapshots_now")
            if left != 0:
                raise LifecycleError("a class %s volume is moved only with no snapshot left; %s remain%s. %s" % (
                    cls, "an unknown number of snapshots" if left is None else left, "" if left is None else "s" if left == 1 else "",
                    "Wait for the rotation." if cls == "B" else "It is class C now: reclassify it."))
    elif phase == "copy":
        if evidence.get("validated") is not True:
            raise LifecycleError("the copy starts only after validation passed")
        if klass(history(journal, volume)) == "C" and not isinstance(evidence.get("snapshots"), int):
            raise LifecycleError("a class C copy must record how many snapshot images it rebuilds")
    elif phase == "sync":
        h = history(journal, volume)
        if cur == "copy" and not (evidence.get("size_equal") is True and evidence.get("hash_match") is True):
            raise LifecycleError("sync starts only when the copy was verified: same size and same hash")
        if cur == "copy" and klass(h) == "C":
            want = h[-1].get("snapshots")
            if not (evidence.get("chain_match") is True and evidence.get("snapshots_matched") == want):
                raise LifecycleError("a class C copy is verified only when all %s snapshot images compare identical and the snapshot ids match" % want)
    elif phase == "recheck":
        needs_reconcile = klass(history(journal, volume)) == "C"
        evidence["ok"] = bool(evidence.get("ok") is True and evidence.get("source_fingerprint")
                              and (evidence.get("lag_s") or 0) <= MAX_LAG and (evidence.get("pending_blocks") or 0) == 0
                              and (not needs_reconcile or evidence.get("reconciled") is True))
        if not evidence["ok"]:
            # a failed recheck never moves forward: back to sync, and the passes start again
            _write(journal, volume, "recheck", evidence, now)
            return _write(journal, volume, "sync", dict(evidence, regression=True, reason="recheck failed" + (": " + str(evidence["fail_reason"]) if evidence.get("fail_reason") else "")), now)
    elif phase == "cutover":
        ok, why = cutover_ready(journal, volume, gate, now, rules)
        if not ok:
            raise LifecycleError("cutover refused: " + "; ".join(why))
        if retype and evidence.get("before_state") is not True:
            raise LifecycleError("cutover refused: the before-state of the volume and its instance was not captured")
    elif phase == "soak":
        s = gate_signed(journal, volume, "G4", gate)
        if not s:
            raise LifecycleError("the soak starts only after G4 (the cutover verified) is signed by a second person")
        h = history(journal, volume)
        if h and h[-1].get("ok") is False:
            raise LifecycleError("the soak starts only when the post-check passed; it failed. The volume must be reversed")
        evidence["g4_by"] = s["by"]
        evidence["soak_days"] = max(1, int((rules or {}).get("soak_days") or 1))
    elif phase == "done":
        if cur == "soak":
            h = history(journal, volume)
            days = int(h[-1].get("soak_days") or 1)
            if now < _parse(h[-1]["at"]) + timedelta(days=days):
                raise LifecycleError("the source is released only after the soak period of %d day(s)" % days)
    elif phase == ROLLBACK:
        if len(str(evidence.get("reason") or "")) < 8:
            raise LifecycleError("a rollback needs a reason")
    return _write(journal, volume, phase, evidence, now)


def _write(journal, volume, phase, evidence, now):
    data = dict(evidence, phase=phase, at=_iso(now))
    journal.record(KIND, volume, data)
    return data


def states(journal):
    """The newest phase event of every volume, in one query: {volume: event}."""
    out = {}
    for e in journal.events(KIND, None, 1000000):
        out.setdefault(e["subject"], dict(e["data"], id=e["id"]))
    return out


# How far along a volume is, for the progress meter. The copy phase spans 5 to 60 percent by how much is copied.
PROGRESS = {"precheck": 2, "validate": 5, "sync": 65, "recheck": 80, "cutover": 90, "postcheck": 95,
            "soak": 100, "done": 100, ROLLBACK: 0, ABORTED: 0}


def progress(phase, copy_pct=None):
    if phase == "copy":
        return 5 + 55 * max(0.0, min(100.0, float(copy_pct or 0))) / 100.0
    return PROGRESS.get(phase, 0)


def summary(journal, volumes):
    """Count volumes per phase, for the screen and the report."""
    out = {}
    for v in volumes:
        p = phase_of(journal, v) or "not started"
        out[p] = out.get(p, 0) + 1
    return out
