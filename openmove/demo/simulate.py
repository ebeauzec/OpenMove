# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""A simulated migration run for demonstrations (demo mode only).

The migration engine is not built, so there is nothing real to watch. This module drives the REAL lifecycle rules
(openmove.lifecycle) and the REAL halt switch and gate with made-up evidence and a virtual clock, so that the live view
can show the order being enforced: copy, sync, recheck, recheck again, cutover. Nothing is sent to any cloud, array or
host. Every event it writes is marked "simulated" and the screens say so.

Every class that the gate admits takes its own path (docs/CLASSES.md): class A the standard path; class B waits for its snapshots to
rotate away; class C rebuilds and compares the snapshot history. Classes D and E are held back and never start.

It injects the failures the process exists for, so a customer can see them handled and read them in the not-migrated report:
  * a premature cutover attempt is refused (only one recheck has passed)
  * a source that changes between the rechecks sends the volume back to sync; one that never settles is stopped
  * a copy that does not verify is aborted and the staging volume is discarded, the source was only read
  * class B: snapshots that never rotate away; class C: a snapshot image that does not match, a tenant snapshot during the sync
  * a swap that is refused at the cutover is rolled back (R2) and the source is restored
  * a target with no room; a halt pauses everything; a gate that is no longer approved stops everything
"""
import random
import threading
import time
from datetime import datetime, timedelta, timezone

from .. import lifecycle as L
from .. import safety, selection

STEP_MINUTES = 3          # virtual minutes per tick (the rechecks must be 5 apart, so at least two ticks)
RATE_PER_COPY_MB_S = 42   # synthetic
WATCH_LIMIT = 6           # ticks a class B volume is watched before the window is over


class Simulation:
    def __init__(self, state, get_gate, tick=0.6, seed=11):
        self.state, self.get_gate, self.tick = state, get_gate, tick
        self.rnd = random.Random(seed)
        self.vnow = datetime.now(timezone.utc)
        self.status = "idle"
        self.progress = {}          # volume -> copy percent
        self.notes = {}             # volume -> what it is doing now, in words
        self.throughput = 0.0
        self.ticks = 0
        self.failures = 0           # volumes stopped or rolled back in this run
        self._failures_at_halt = 0
        self._stop = threading.Event()
        self._thread = None
        self._plan = {}
        self._active = []
        self._queue = []

    # ---- control ----
    def start(self):
        gate = self.get_gate()
        if not gate or gate.get("status") != "approved":
            raise RuntimeError("the gate is not approved: " + ((gate or {}).get("message") or "run the pre-flight first"))
        ids = selection.selected_ids(self.state)
        if not ids:
            raise RuntimeError("no volume is selected: choose volumes on the Select tab (the pre-flight must admit them)")
        inv = {v["id"]: v for v in (self.state.inventory or {}).get("volumes", [])}
        seen = {}
        risk = (self.state.checks or {}).get("risk") or {}
        nrisk = 0
        for i, vid in enumerate(ids):
            v = inv.get(vid, {})
            cls = v.get("class") or "A"
            j = seen[cls] = seen.get(cls, -1) + 1            # position within the class: the faults are spread by class
            snaps = int(v.get("snapshots") or 0)
            size = v.get("size") or 10
            attached = bool(v.get("attachments"))
            risky = (risk.get(vid) or {}).get("level") == "high"      # cleared by two people, but still held and still watched
            nrisk += 1 if risky else 0
            self._plan[vid] = {
                "risk": risky, "postcheck_fail": risky and nrisk % 2 == 1,
                "stage": "new", "ticks": 0, "i": i, "cls": cls, "snaps": snaps, "left": snaps, "attached": attached, "regs": 0,
                "copy_ticks": max(2, min(12, int(size / 40) + 2 + (3 if i % 29 == 3 else 0) + (min(snaps, 6) // 2 if cls == "C" else 0))),
                "probe": i % 6 == 0,
                # faults, by class, so every class shows what it can go wrong with
                "drift_left": 1 if j % 17 == 5 else (99 if cls == "A" and j % 29 == 11 else 0),
                "verify_fail": cls in ("A", "B") and j % 23 == 7,
                "no_room": cls == "A" and j % 31 == 12,
                "never_rotates": cls == "B" and j % 4 == 3,
                "rotate_ticks": 2 + j % 4,
                "chain_mismatch": cls == "C" and j % 5 == 2,
                "raced": cls == "C" and j % 7 == 3,
                "swap_refused": cls == "C" and attached and j % 3 == 1}
        self._queue = ids
        self.status = "running"
        self.state.journal.record("demo", "simulation", {"note": "simulated migration started: made-up evidence, nothing is sent to any system",
                                                         "volumes": len(ids)})
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        if self.status in ("running", "paused by halt"):
            self.status = "stopped"

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def _size(self, vid):
        for v in (self.state.inventory or {}).get("volumes", []):
            if v["id"] == vid:
                return v.get("size") or 10
        return 10

    # ---- the loop ----
    def _run(self):
        try:
            while not self._stop.is_set():
                if not self.tick_once():
                    break
                if self.tick:
                    time.sleep(self.tick)
        except Exception as exc:                      # anything unexpected stops the simulation; it never carries on
            self.status = "stopped: %s" % exc
        finally:
            if self.status == "running":
                self.status = "finished"

    def opts(self):
        return selection.options_of(self.state)

    def release(self, ids):
        """Volumes the operator released for cutover. The state holds the set so the screen and the run agree."""
        self.state.released |= set(ids)

    def tick_once(self):
        """One step of the simulation. Returns False when it is over."""
        if safety.halted(self.state.journal):
            self.status, self.throughput = "paused by halt", 0.0
            return True
        gate = self.get_gate()
        if not gate or gate.get("status") != "approved":
            self.status = "stopped: the gate is no longer approved (%s)" % ((gate or {}).get("message") or "no pre-flight")
            self.state.journal.record("refusal", "simulation", {"attempt": "continue", "reason": self.status})
            return False
        o = self.opts()
        working = [v for v in self._active if self._plan[v]["stage"] != "ready"]
        if self._active and not working and not self._queue:
            # everything left is in sync and waiting for a person: the (virtual) clock stands still
            self.status = "waiting for you to release %d volume(s) for cutover" % len(self._active)
            self.throughput = 0.0
            self._release_ready(gate, o)
            return True
        self.status = "running"
        self.ticks += 1
        self.vnow += timedelta(minutes=STEP_MINUTES)
        while self._queue and len(working) < o["concurrency"]:
            vid = self._queue.pop(0)
            self._active.append(vid)
            working.append(vid)
        copying = 0
        for vid in list(self._active):
            copying += 1 if self._plan[vid]["stage"] == "copy" else 0
            self._step(vid, gate, o)
        self._auto_halt(o)
        self._active = [v for v in self._active if self._plan[v]["stage"] not in ("end",)]
        self.throughput = min(o["rate_cap_mb_s"], copying * RATE_PER_COPY_MB_S)
        return bool(self._active or self._queue)

    def _release_ready(self, gate, o):
        for vid in list(self._active):
            if self._plan[vid]["stage"] == "ready":
                self._step(vid, gate, o)

    def _auto_halt(self, o):
        n = o["stop_after_failures"]
        if n and self.failures - self._failures_at_halt >= n and not safety.halted(self.state.journal):
            self._failures_at_halt = self.failures
            safety.halt(self.state.journal, "OpenMove (automatic)",
                        "%d volumes failed in this run: read the not-migrated report, then resume" % n)

    def _enter(self, vid, phase, evidence, gate):
        try:
            L.enter(self.state.journal, vid, phase, dict(evidence, simulated=True), gate=gate, now=self.vnow,
                    rules=selection.rules(self.opts()))
            return True
        except L.LifecycleError as exc:
            self.state.journal.record("refusal", vid, {"attempt": phase, "reason": str(exc), "simulated": True})
            return False

    def _abort(self, vid, cause, reason):
        self.failures += 1
        L.enter(self.state.journal, vid, "aborted", {"reason": reason, "cause": cause, "simulated": True}, now=self.vnow)
        self._plan[vid]["stage"] = "end"

    def _recheck(self, vid, gate, fp, p, **bad):
        ev = {"ok": True, "lag_s": 0, "pending_blocks": 0, "source_fingerprint": fp}
        if p["cls"] == "C":
            ev["reconciled"] = True
        ev.update(bad)
        return self._enter(vid, "recheck", ev, gate)

    def _step(self, vid, gate, o):
        p = self._plan[vid]
        p["ticks"] += 1
        st, cls = p["stage"], p["cls"]
        fp = "fp-%s" % vid[-6:]
        if st == "new":
            if not self._enter(vid, "precheck", {"class": cls}, gate):
                p["stage"] = "end"
            elif cls == "B":                                         # the watcher: wait until the snapshots have rotated away
                p["stage"], p["ticks"] = "watch", 0
                self._enter(vid, "validate", {"snapshots_now": p["left"]}, gate)    # refused while any snapshot remains
                self.notes[vid] = "waiting for %d snapshot(s) to rotate away" % p["left"]
            else:
                p["stage"], p["ticks"] = "validate", 0
        elif st == "watch":
            if p["never_rotates"]:
                if p["ticks"] >= WATCH_LIMIT:
                    self._abort(vid, "rotation_timeout", "the snapshots did not rotate away within the agreed window: %d still exist" % p["left"])
                    self.notes[vid] = "gave up waiting: %d snapshot(s) remain" % p["left"]
                else:
                    self.notes[vid] = "waiting: %d snapshot(s) still exist" % p["left"]
            else:
                p["left"] = max(0, p["snaps"] - (p["snaps"] * p["ticks"] + p["rotate_ticks"] - 1) // p["rotate_ticks"])
                self.notes[vid] = ("waiting for %d snapshot(s) to rotate away" % p["left"]) if p["left"] else "snapshots gone: starting"
                if p["left"] == 0:
                    p["stage"], p["ticks"] = "validate", 0
        elif st == "validate":
            if self._enter(vid, "validate", {"snapshots_now": p["left"] if cls != "C" else p["snaps"], "room_ok": not p["no_room"]}, gate):
                ev = {"validated": True}
                if cls == "C":
                    ev["snapshots"] = p["snaps"]
                self._enter(vid, "copy", ev, gate)
                p["stage"], p["ticks"] = "copy", 0
                self.progress[vid] = 0
                self.notes.pop(vid, None)
            elif p["no_room"]:
                self._abort(vid, "no_room", "the target pool has no room for this volume")
            else:
                p["stage"] = "end"
        elif st == "copy":
            self.progress[vid] = min(100.0, 100.0 * p["ticks"] / p["copy_ticks"])
            if cls == "C":
                self.notes[vid] = "rebuilding snapshot history: %d of %d" % (min(p["snaps"], p["snaps"] * p["ticks"] // p["copy_ticks"]), p["snaps"])
            if p["ticks"] >= p["copy_ticks"]:
                ev = {"size_equal": True, "hash_match": not p["verify_fail"]}
                if cls == "C":
                    ev.update(chain_match=not p["chain_mismatch"], snapshots_matched=p["snaps"] - (1 if p["chain_mismatch"] else 0))
                if self._enter(vid, "sync", ev, gate):
                    p["stage"], p["ticks"] = "sync", 0
                    self.notes.pop(vid, None)
                elif p["verify_fail"]:                              # the copy does not verify: refused, discarded, source untouched
                    self._abort(vid, "copy_not_verified", "copy did not verify: the target copy was discarded, the source was only read")
                elif p["chain_mismatch"]:
                    self._abort(vid, "chain_mismatch", "snapshot image %d of %d does not match the source: the staging volume was discarded" % ((p["snaps"] + 1) // 2, p["snaps"]))
                else:
                    p["stage"] = "end"
        elif st == "sync":
            if p["ticks"] >= 2:
                self._recheck(vid, gate, fp, p)
                p["stage"], p["ticks"], p["passes"] = "recheck1", 0, 1
        elif st == "recheck1":
            gap = selection.gap_ticks(o, STEP_MINUTES)
            if p["probe"] and p["ticks"] == 1:                    # a premature cutover is refused: only one recheck has passed
                self._enter(vid, "cutover", {}, gate)
            elif p["ticks"] >= gap:                               # the gap later: check again
                if p["drift_left"] > 0:                           # the source changed: pending blocks, back to sync
                    p["drift_left"] -= 1
                    p["regs"] += 1
                    self._recheck(vid, gate, fp, p, pending_blocks=64, fail_reason="the source changed: 64 blocks pending")
                    if p["regs"] >= 3:
                        self._abort(vid, "never_stable", "the source changed at %d rechecks in a row: there was never a quiet moment to cut over" % p["regs"])
                    else:
                        p["stage"], p["ticks"] = "sync", 0
                elif p["raced"]:                                  # class C: a tenant snapshot appeared, the history no longer matches
                    p["raced"] = False
                    self._recheck(vid, gate, fp, p, reconciled=False, fail_reason="a tenant snapshot was created during the sync")
                    p["stage"], p["ticks"] = "sync", 0
                else:
                    self._recheck(vid, gate, fp, p)
                    p["passes"] += 1
                    p["ticks"] = 0
                    if p["passes"] >= o["rechecks"]:
                        if (o["hold_before_cutover"] or p["risk"]) and vid not in self.state.released:
                            p["stage"] = "ready"                  # in sync and checked: waits for a person
                            self.notes[vid] = "ready for cutover: in sync and checked, waiting for your release"
                        else:
                            p["stage"] = "recheck2"
        elif st == "ready":
            if vid in self.state.released or not (o["hold_before_cutover"] or p["risk"]):
                self.notes.pop(vid, None)
                self._recheck(vid, gate, fp, p)                   # released: check once more, right now
                p["stage"], p["ticks"] = "recheck2", 0
        elif st == "recheck2":
            if self._enter(vid, "cutover", {}, gate):
                p["stage"], p["ticks"] = "cutover", 0
            elif p["ticks"] >= 3:                                  # refused (for example the gap): check again
                self._recheck(vid, gate, fp, p)
                p["ticks"] = 0
        elif st == "cutover":
            if p["swap_refused"]:                                  # the swap is refused: rollback R2, the source is restored
                self.failures += 1
                L.enter(self.state.journal, vid, "rollback", {
                    "reason": "the swap was refused: the instance changed state (task in progress)", "cause": "swap_refused", "simulated": True}, now=self.vnow)
                p["stage"] = "end"
            elif self._enter(vid, "postcheck", {"attachment_ok": True}, gate):
                p["stage"], p["ticks"] = "postcheck", 0
                self.notes[vid] = "checking the workload from its new path"
        elif st == "postcheck":
            self.notes.pop(vid, None)
            if p["postcheck_fail"]:                                # the workload did not come back healthy: rolled back to the source (R3)
                self.failures += 1
                L.enter(self.state.journal, vid, "rollback", {
                    "reason": "the guest did not come back healthy after the cutover: rolled back to the source", "cause": "postcheck_failed", "simulated": True}, now=self.vnow)
                p["stage"] = "end"
            elif self._enter(vid, "soak", {}, gate):
                p["stage"] = "end"                                 # the soak period is not simulated: the source stays untouched
