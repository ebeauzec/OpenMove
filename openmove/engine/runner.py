# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The engine for class A and class B volumes, moved by Cinder's retype with on-demand migration (docs/ENGINE_PLAN.md, docs/PROCESS.md).

The order of one volume (lifecycle.RETYPE_ORDER):
  precheck  > validate  > [G2 and G3 signed by a second person, release if held]  > cutover (lock, retype, wait)  > postcheck
  > [G4 signed by a second person] > soak > done          Rollback is a retype back (R3).

The engine is driven by ticks. A tick looks at every active volume, reads the cloud afresh, and does at most one next thing for each.
It keeps nothing in memory that the journal cannot give back: after a restart the same ticks carry on from the journal.
Every rule that matters is enforced by lifecycle.enter, safety.Permit and actions.Actions, not here; this file only decides what comes next.
"""
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import lifecycle as L
from .. import safety, selection
from . import plan
from .actions import JOB, Actions, CallFailed, OutcomeUnknown
from .evidence import Evidence

ROTATION_TIMEOUT = timedelta(hours=72)      # how long a class B volume may wait for its snapshots to rotate away before it is given up
MIGRATION_TIMEOUT = timedelta(hours=6)      # how long a retype may run before the engine halts and asks a person
RETYPE_GRACE = timedelta(seconds=30)        # after this long, a volume that is back to normal and not on the target means the retype was undone
# The fields a retype must never change (playbook step 5.10.5): who owns the volume, where it lives and what it was made from.
IDENTITY_FIELDS = (("id", "id"), ("creation time", "created_at"), ("project", "os-vol-tenant-attr:tenant_id"), ("user", "user_id"),
                   ("snapshot origin", "snapshot_id"), ("source volume", "source_volid"), ("availability zone", "availability_zone"),
                   ("multi-attach flag", "multiattach"))
MOVED = ("A", "B")


class EngineError(ValueError):
    """A request the engine refuses with a reason a person can act on."""


class Engine:
    def __init__(self, state, permit, gate, clock=None):
        self.state, self._permit, self._gate = state, permit, gate
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.ev = Evidence(Path(state.home) / "evidence")
        self.notes, self.type_map = {}, {}
        self.queue, self.active, self.failed, self.finished = [], [], set(), set()
        self.window = (None, None)                    # (start_at, stop_at): when new volumes may be taken and a retype may be sent (R3); None = no limit
        self.status, self.ticks, self._specs = "idle", 0, {}
        self._thread, self._stop, self._mutex = None, threading.Event(), threading.RLock()

    # ---- small helpers ---------------------------------------------------------------------------------------
    def acts(self):
        return Actions(self.state.session, self.state.journal, self._permit, self.ev)

    def rules(self):
        return selection.rules(selection.options_of(self.state))

    def inv(self, vid):
        return next((v for v in (self.state.inventory or {}).get("volumes", []) if v["id"] == vid), None)

    def specs(self, acts, ids, type_map):
        c = self.state.checks
        if not c:
            raise EngineError("run the pre-flight checks first")
        types = acts.volume_types()
        out = {}
        for vid in ids:
            v = self.inv(vid)
            tt, why = plan.target_type_for(v.get("volume_type"), type_map, types, c["target_backend"])
            out[vid] = {"class": v.get("class"), "source_backend": c["source_backend"], "target_backend": c["target_backend"],
                        "target_type": tt, "why_no_type": why, "types": types, "source_type": v.get("volume_type")}
        return out

    def candidates(self):
        """The selected volumes this engine can move: class A and B. Class C needs the staged method, which is not built yet."""
        return [vid for vid in selection.selected_ids(self.state) if (self.inv(vid) or {}).get("class") in MOVED]

    def read_all(self, acts, vid):
        vol = acts.volume(vid)
        snaps = acts.snapshots(vid)
        servers = {}
        for sid in plan.attached_servers(vol):
            try:
                servers[sid] = acts.server(sid)
            except Exception:
                servers[sid] = None
        return vol, snaps, servers

    def phase(self, vid):
        return L.phase_of(self.state.journal, vid)

    def note(self, vid, text):
        self.notes[vid] = text

    # ---- the dry run: reads only, no lifecycle event, no change -------------------------------------------------
    def dry_run(self, ids=None, type_map=None):
        """What a run would do, volume by volume, from fresh reads. Sends nothing that changes anything."""
        acts = self.acts()
        ids = [i for i in (ids or self.candidates()) if (self.inv(i) or {}).get("class") in MOVED]
        if not ids:
            raise EngineError("no class A or B volume is selected")
        specs = self.specs(acts, ids, type_map or self.type_map)
        rows, counts = [], {"ok": 0, "wait": 0, "blocked": 0}
        for vid in ids:
            try:
                vol, snaps, servers = self.read_all(acts, vid)
            except Exception as exc:
                rows.append({"id": vid, "class": specs[vid]["class"], "verdict": "blocked", "checks": [], "calls": [], "error": "could not read the volume: %s" % exc})
                counts["blocked"] += 1
                continue
            checks = plan.validate(vol, snaps, servers, specs[vid])
            v = plan.verdict(checks)
            counts[v] += 1
            rows.append({"id": vid, "class": specs[vid]["class"], "verdict": v, "target_type": specs[vid]["target_type"],
                         "checks": checks, "calls": plan.planned_calls(vol, specs[vid], servers) if v != "blocked" else []})
        self.dry_ok = {"run_id": (self.state.checks or {}).get("run_id"), "ids": set(ids)}       # read: a real run may start for these volumes
        self.state.journal.record("engine_dry_run", "batch", {"volumes": len(rows), "ok": counts["ok"], "wait": counts["wait"], "blocked": counts["blocked"]})
        return {"volumes": rows[:500], "more": max(0, len(rows) - 500), "counts": counts, "access": getattr(self.state, "access", "api"),
                "types": [{"name": t["name"], "backend": (t.get("extra_specs") or {}).get("volume_backend_name")} for t in specs[ids[0]]["types"]],
                "note": "Nothing was changed. Every change above would go through a permit that asks again at the moment of the call."}

    # ---- running -----------------------------------------------------------------------------------------------
    def open_volumes(self):
        """Volumes this engine started earlier (a retype) that have not reached an end: what a restart picks up again."""
        return [v for v, e in L.states(self.state.journal).items() if L.method_of(L.history(self.state.journal, v)) == L.RETYPE and e["phase"] not in L.TERMINAL]

    def dry_covers(self, ids):
        """True when a dry run was read for the current pre-flight run and included every one of these volumes."""
        d = getattr(self, "dry_ok", None)
        return bool(d) and d["run_id"] == (self.state.checks or {}).get("run_id") and set(ids) <= d["ids"]

    def start(self, ids=None, type_map=None, thread=True, window=(None, None)):
        """Begin a real run. Needs a permit right now (release validated, gate approved, no halt). `window` is (start_at, stop_at) as datetimes or None:
        outside it no new volume is taken and no retype is sent; what is already in flight carries on and is watched."""
        with self._mutex:
            lo, hi = window
            if lo and hi and hi <= lo:
                raise EngineError("the window ends before it starts")
            self.window = (lo, hi)
            if self.status == "running":
                raise EngineError("a run is already going")
            self._permit()                                    # refuses here, before anything is queued
            if ids and not any((self.inv(i) or {}).get("class") in MOVED for i in ids):
                raise EngineError("no class A or B volume is selected")       # as the dry run says; never end a run quietly with nothing to do
            acts = self.acts()
            self.type_map = dict(type_map or self.type_map)
            started = self.open_volumes()
            ids = [i for i in (ids or self.candidates()) if (self.inv(i) or {}).get("class") in MOVED]
            self.queue = [i for i in ids if self.phase(i) is None]
            self.active = started
            self.failed, self.finished = set(), set()
            self._specs = self.specs(acts, list(set(self.queue) | set(self.active)), self.type_map) if (self.queue or self.active) else {}
            self.status, self._stop = "running", threading.Event()
            self.state.journal.record("engine_run", "batch", {"queued": len(self.queue), "resumed": len(self.active), "type_map": self.type_map})
            if thread:
                self._thread = threading.Thread(target=self._loop, daemon=True)
                self._thread.start()

    def stop(self):
        """Stop taking new volumes and stop ticking. Nothing in flight is touched: a retype already sent carries on in the cloud."""
        self._stop.set()
        t = self._thread
        if t and t is not threading.current_thread():
            t.join(timeout=30)
        with self._mutex:
            if self.status == "running":
                self.status = "stopped"

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:                          # fail closed: stop the loop, change nothing more
                self.status = "stopped after an error: %s" % exc
                return
            if self.status != "running":
                return
            self._stop.wait(5)

    def in_window(self):
        lo, hi = self.window
        now = self.clock()
        return (lo is None or now >= lo) and (hi is None or now < hi)

    def tick(self):
        """One pass: top up the active set, advance every active volume by one step, drop the ones that ended."""
        with self._mutex:
            self.ticks += 1
            o = selection.options_of(self.state)
            while self.queue and len(self.active) < o["concurrency"] and not safety.halted(self.state.journal) and self.in_window():
                self.active.append(self.queue.pop(0))
            for vid in list(self.active):
                if self._stop.is_set():
                    break
                try:
                    self.advance(vid)
                except safety.Refused as exc:
                    self.note(vid, "waiting: " + str(exc))
                except OutcomeUnknown as exc:
                    self.note(vid, str(exc))
                except Exception as exc:                       # a read that failed, a surprise: say so, change nothing, look again next tick
                    self.note(vid, "could not continue (%s: %s); will look again" % (type(exc).__name__, exc))
                ph = self.phase(vid)
                if ph in L.TERMINAL:
                    self.active.remove(vid)
                    (self.finished if ph == "done" and vid not in self.failed else self.failed).add(vid)
            limit = o["stop_after_failures"]
            if limit and len(self.failed) >= limit and not safety.halted(self.state.journal):
                safety.halt(self.state.journal, "OpenMove (automatic)", "%d volumes failed in this run (the limit you set)" % len(self.failed))
            if not self.queue and not self.active:
                self.status = "finished"

    # ---- one volume, one step ---------------------------------------------------------------------------------
    def advance(self, vid):
        acts = self.acts()
        ph = self.phase(vid)
        h = safety.halted(self.state.journal)
        if h and ph != "soak":                      # a halt waits for a person, also before an unknown answer is read back
            return self.note(vid, "halted by %s: %s" % (h["by"], h["reason"]))
        if acts.open_steps(vid):
            return self.settle(vid, acts)
        if ph is None:
            return self.precheck(vid, acts)
        if ph in ("precheck", "validate"):
            return self.validate(vid, acts) if ph == "precheck" else self.before_cutover(vid, acts)
        if ph == "cutover":
            return self.cutover(vid, acts)
        if ph == "postcheck":
            return self.after_postcheck(vid, acts)
        if ph == "soak":
            try:
                L.enter(self.state.journal, vid, "done", now=self.clock(), rules=self.rules())
                self.note(vid, "done: the soak period is over")
            except L.LifecycleError as exc:
                self.note(vid, "soak: " + str(exc))
        elif ph == L.ROLLBACK:
            self.reverse_step(vid, acts)

    def spec(self, vid, acts):
        sp = self._specs.get(vid)
        if sp is None:
            sp = self._specs[vid] = self.specs(acts, [vid], self.type_map)[vid]
        return sp

    def give_up(self, vid, why):
        self.failed.add(vid)
        L.enter(self.state.journal, vid, L.ABORTED, {"reason": why[:300]}, now=self.clock())
        self.note(vid, "given up: " + why)

    def precheck(self, vid, acts):
        sp = self.spec(vid, acts)
        try:
            L.enter(self.state.journal, vid, "precheck", {"class": sp["class"], "method": L.RETYPE, "source_backend": sp["source_backend"],
                    "target_backend": sp["target_backend"], "source_type": sp["source_type"], "target_type": sp["target_type"]},
                    gate=self._gate(), now=self.clock(), rules=self.rules())
        except L.LifecycleError as exc:
            return self.note(vid, "not started: " + str(exc))
        self.validate(vid, acts)

    def validate(self, vid, acts):
        sp = self.spec(vid, acts)
        vol, snaps, servers = self.read_all(acts, vid)
        checks = plan.validate(vol, snaps, servers, sp)
        v = plan.verdict(checks)
        self.ev.save(vid, "validated.json", {"volume": vol, "snapshots": snaps, "servers": servers, "checks": checks})
        if v == "blocked":
            bad = [c["detail"] for c in checks if not c["ok"] and not c["wait"]]
            return self.give_up(vid, "; ".join(bad))
        if v == "wait":
            started = L._parse(L.history(self.state.journal, vid)[0]["at"])
            if self.clock() - started > ROTATION_TIMEOUT:
                return self.give_up(vid, "the snapshots did not rotate away within %d hours" % (ROTATION_TIMEOUT.total_seconds() // 3600))
            return self.note(vid, "waiting for the %d snapshot(s) to rotate away" % len(snaps))
        L.enter(self.state.journal, vid, "validate", {"snapshots_now": 0, "validated_at": self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")},
                now=self.clock(), rules=self.rules())
        self.note(vid, "validated: waiting for a second person to sign G2 and G3")

    def before_cutover(self, vid, acts):
        j, gate = self.state.journal, self._gate()
        signed = [g for g in ("G2", "G3") if L.gate_signed(j, vid, g, gate)]
        if len(signed) < 2:
            return self.note(vid, "validated: waiting for a second person to sign G2 and G3")
        risky = ((self.state.checks or {}).get("risk") or {}).get(vid, {}).get("level") == "high"
        if (selection.options_of(self.state)["hold_before_cutover"] or risky) and vid not in self.state.released:
            return self.note(vid, "ready: G2 and G3 are signed; waiting for you to release it for the retype")
        if not self.in_window():
            return self.note(vid, "ready: waiting for the run window (%s)" % self.window_text())
        sp = self.spec(vid, acts)
        vol, snaps, servers = self.read_all(acts, vid)              # fresh reads, right now: the volume may have changed since the validation
        checks = plan.validate(vol, snaps, servers, sp)
        if plan.verdict(checks) != "ok":
            return self.give_up(vid, "at the moment of the retype: " + "; ".join(c["detail"] for c in checks if not c["ok"]))
        before = {"volume": vol, "snapshots": snaps, "servers": servers, "at": self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")}
        self.ev.save(vid, "before.json", before)
        try:
            L.enter(j, vid, "cutover", {"before_state": True, "target_type": sp["target_type"], "source_type": vol.get("volume_type"),
                                        "servers": sorted(servers), "locked_already": [s for s, x in servers.items() if (x or {}).get("locked")]},
                    gate=gate, now=self.clock(), rules=self.rules())
        except L.LifecycleError as exc:
            return self.note(vid, "not cut over yet: " + str(exc))
        self.issue(vid, acts, sp, vol, servers)

    def issue(self, vid, acts, sp, vol, servers):
        """Lock the instance, then send the retype. A call that failed for certain ends in rollback; an unknown outcome halts."""
        try:
            for sid in plan.attached_servers(vol):
                if not (servers.get(sid) or {}).get("locked"):
                    acts.lock(vid, sid)
            acts.retype(vid, sp["target_type"], vol.get("volume_type"))
        except CallFailed as exc:
            self.failed.add(vid)
            L.enter(self.state.journal, vid, L.ROLLBACK, {"reason": "a call was refused and nothing was moved: %s" % str(exc)[:200]}, now=self.clock())
            return self.note(vid, "the cloud refused a call; nothing was moved: %s" % exc)
        self.note(vid, "retype sent: the cloud is copying the volume")

    def cutover(self, vid, acts):
        sp = self.spec(vid, acts)
        retype = acts.last_step(vid, "retype")
        vol = acts.volume(vid)
        if not (retype and retype["status"] == "done"):
            # entered the cutover, but the retype was never sent (a refusal, a crash): read the cloud before deciding
            if vol.get("os-vol-mig-status-attr:migstat") == "migrating" or vol.get("volume_type") == sp["target_type"]:
                return self.note(vid, "the retype is already running in the cloud; following it")
            return self.issue(vid, acts, sp, vol, {s: acts.server(s) for s in plan.attached_servers(vol)})
        st = self.outcome(vol, sp)
        if st == "running" and self.undone(vid, vol, sp):
            st = "undone"
        if st == "success":
            return self.complete(vid, acts, sp, vol)
        if st in ("failed", "undone"):
            self.failed.add(vid)
            self.ev.save(vid, "failed.json", {"volume": vol})
            why = ("Cinder accepted the retype and then put the volume back unchanged (status %s, type %s, migration_status %s); "
                   "a real Cinder does this for a volume it cannot move, for example one with a snapshot" % (vol.get("status"), vol.get("volume_type"),
                                                                                                            vol.get("os-vol-mig-status-attr:migstat"))
                   if st == "undone" else "Cinder reported migration_status=%s; the source volume is expected to be intact" %
                   vol.get("os-vol-mig-status-attr:migstat"))
            L.enter(self.state.journal, vid, L.ROLLBACK, {"reason": why}, now=self.clock())
            return self.note(vid, "the move failed in the cloud: rolling back (checking the volume is where it was)")
        if st == "broken":
            safety.halt(self.state.journal, "OpenMove (automatic)", "volume %s is in status %s after the retype" % (vid, vol.get("status")))
            return self.note(vid, "the volume is in status %s: halted for a person to look" % vol.get("status"))
        if self.clock() - L._parse(L.current(self.state.journal, vid)["at"]) > MIGRATION_TIMEOUT:
            safety.halt(self.state.journal, "OpenMove (automatic)", "the retype of %s has run for more than %d hours" % (vid, MIGRATION_TIMEOUT.total_seconds() // 3600))
        self.note(vid, "retype running: migration_status=%s, status=%s" % (vol.get("os-vol-mig-status-attr:migstat"), vol.get("status")))

    def undone(self, vid, vol, sp):
        """True when the retype was accepted and then silently undone. Cinder sets 'retyping' before it answers, so a volume that is back to
        available or in-use, not migrating and not of the target type, some time after the call, is not going to move."""
        if str(vol.get("status")) not in ("available", "in-use") or vol.get("volume_type") == sp["target_type"]:
            return False
        if vol.get("os-vol-mig-status-attr:migstat") in ("migrating", "starting"):
            return False
        return self.clock() - L._parse(L.current(self.state.journal, vid)["at"]) > RETYPE_GRACE

    @staticmethod
    def outcome(vol, sp):
        """success, failed (the move failed, the volume is usable), broken (the volume is in an error status) or running."""
        status, mig = str(vol.get("status")), vol.get("os-vol-mig-status-attr:migstat")
        if status.startswith("error"):
            return "broken"
        done = (mig == "success" and plan.backend_of(vol.get("os-vol-host-attr:host")) == sp["target_backend"]
                and vol.get("volume_type") == sp["target_type"] and status in ("available", "in-use"))
        if done:
            return "success"
        if mig in ("error", "failed") and status in ("available", "in-use"):
            return "failed"
        return "running"

    def complete(self, vid, acts, sp, vol):
        """The move finished. Compare with the before-state, record the result, and enter the post-check."""
        before = self.ev.load(vid, "before.json") or {}
        bvol = before.get("volume") or {}
        servers = {s: acts.server(s) for s in plan.attached_servers(vol)}
        key = lambda x: sorted((a.get("server_id"), a.get("device")) for a in (x.get("attachments") or []))
        checks = []

        def add(name, ok, detail):
            checks.append({"name": name, "ok": bool(ok), "detail": detail})
        add("On the target back-end with the target type", True, "%s, type %s" % (plan.backend_of(vol.get("os-vol-host-attr:host")), vol.get("volume_type")))
        add("Same status as before", vol.get("status") == bvol.get("status"), "%s now, %s before" % (vol.get("status"), bvol.get("status")))
        add("Same size", vol.get("size") == bvol.get("size"), "%s GiB now, %s before" % (vol.get("size"), bvol.get("size")))
        add("Same attachments", key(vol) == key(bvol), "%d now, %d before" % (len(key(vol)), len(key(bvol))))
        add("Same bootable flag", vol.get("bootable") == bvol.get("bootable"), "%s now, %s before" % (vol.get("bootable"), bvol.get("bootable")))
        for label, field in IDENTITY_FIELDS:        # what the tenant sees must not change; a rename or a metadata edit is the tenant's and is not checked
            add("Same %s" % label, vol.get(field) == bvol.get(field), "%s now, %s before" % (vol.get(field), bvol.get(field)))
        for sid, s in servers.items():
            add("Instance %s is running" % sid, (s or {}).get("status") == "ACTIVE", (s or {}).get("status") or "unreadable")
        ok = all(c["ok"] for c in checks)
        self.ev.save(vid, "after.json", {"volume": vol, "servers": servers, "checks": checks})
        L.enter(self.state.journal, vid, "postcheck", {"ok": ok, "checks": checks}, now=self.clock(), rules=self.rules())
        if ok:
            self.note(vid, "moved; the post-check passed: waiting for a second person to sign G4")
            self.unlock_ours(vid, acts)
        else:
            safety.halt(self.state.journal, "OpenMove (automatic)", "the post-check of %s failed: %s" % (vid, "; ".join(c["name"] for c in checks if not c["ok"])))
            self.note(vid, "the post-check failed: halted. The instance stays locked. Reverse the volume (retype back) or look at it first.")

    def unlock_ours(self, vid, acts):
        if acts.locked_by_us(vid):
            for sid in plan.attached_servers(acts.volume(vid)):
                try:
                    acts.unlock(vid, sid)
                except CallFailed as exc:
                    self.note(vid, "could not unlock the instance %s: %s" % (sid, exc))

    def after_postcheck(self, vid, acts):
        h = L.current(self.state.journal, vid)
        if h.get("ok") is False:
            return self.note(vid, "the post-check failed: a person must reverse the volume")
        if acts.locked_by_us(vid):
            self.unlock_ours(vid, acts)
            if acts.locked_by_us(vid):
                return
        try:
            L.enter(self.state.journal, vid, "soak", {}, gate=self._gate(), now=self.clock(), rules=self.rules())
            self.note(vid, "G4 signed: the soak period has started")
        except L.LifecycleError:
            self.note(vid, "the post-check passed: waiting for a second person to sign G4")

    def window_text(self):
        lo, hi = self.window
        f = lambda d: d.strftime("%Y-%m-%d %H:%M UTC") if d else "no limit"
        return "from %s until %s" % (f(lo), f(hi))

    # ---- reverse (R3) -------------------------------------------------------------------------------------------
    def reversible(self):
        """Volumes that were cut over and may be put back: after the retype was sent and before the soak ended."""
        return [v for v, e in L.states(self.state.journal).items()
                if L.method_of(L.history(self.state.journal, v)) == L.RETYPE and e["phase"] in ("cutover", "postcheck", "soak")]

    def reverse_preview(self, ids=None):
        """What putting these volumes back would do, read fresh now and changing nothing: the calls and their undo, per volume (R6: see it before it runs)."""
        acts = self.acts()
        rows = []
        for vid in (ids or self.reversible()):
            ph = self.phase(vid)
            if ph not in ("cutover", "postcheck", "soak"):
                rows.append({"id": vid, "phase": ph, "can": False, "why": "only a volume that was cut over can be reversed", "calls": []})
                continue
            try:
                h0 = L.history(self.state.journal, vid)
                pre = next(e for e in h0 if e["phase"] == "precheck")
                vol = acts.volume(vid)
                servers = {s: acts.server(s) for s in plan.attached_servers(vol)}
                calls = plan.planned_calls(vol, {"target_type": pre["source_type"]}, servers)
                for c in calls:
                    if c["step"] == "retype":
                        c["undo"] = "retype forward again to %s" % vol.get("volume_type")
                rows.append({"id": vid, "phase": ph, "can": True, "from_type": vol.get("volume_type"), "to_type": pre["source_type"],
                             "source_backend": pre.get("source_backend"), "calls": calls})
            except Exception as exc:
                rows.append({"id": vid, "phase": ph, "can": False, "why": "could not read it: %s" % exc, "calls": []})
        return {"volumes": rows, "note": "Nothing was changed. A reverse is a retype back to the first type; the instance is locked around it as on the way there. "
                                         "The way back needs the soak: once the source is released it cannot be done."}

    def reverse_all(self, reason, by):
        """Put every reversible volume back (the whole run). Each volume is journaled and checked on its own."""
        ids = self.reversible()
        if not ids:
            raise EngineError("no volume has been cut over, so there is nothing to put back")
        for vid in ids:
            self.reverse(vid, reason, by)
        return ids

    def reverse(self, vid, reason, by):
        """A person asks to put a volume back. Only after the retype was sent; before it, the volume is simply given up."""
        reason, by = str(reason or "").strip(), str(by or "").strip()
        if len(by) < 3 or len(reason) < 8:
            raise EngineError("a reverse needs the name of the person and a reason of at least 8 characters")
        if self.phase(vid) not in ("cutover", "postcheck", "soak"):
            raise EngineError("only a volume that was cut over can be reversed; it is in %s" % (self.phase(vid) or "no phase"))
        L.enter(self.state.journal, vid, L.ROLLBACK, {"reason": reason, "by": by}, now=self.clock())
        self.failed.add(vid)
        if vid not in self.active:
            self.active.append(vid)
        self.note(vid, "reverse requested by %s" % by)

    def reverse_step(self, vid, acts):
        """In phase rollback: make sure the volume is back on its source type and back-end, then unlock and finish."""
        h0 = L.history(self.state.journal, vid)
        pre = next(e for e in h0 if e["phase"] == "precheck")
        sp = {"source_backend": pre["source_backend"], "source_type": pre["source_type"]}
        vol = acts.volume(vid)
        mig, status = vol.get("os-vol-mig-status-attr:migstat"), str(vol.get("status"))
        back = acts.last_step(vid, "retype_back")
        at_source = plan.backend_of(vol.get("os-vol-host-attr:host")) == sp["source_backend"] and vol.get("volume_type") == sp["source_type"]
        if status.startswith("error"):
            safety.halt(self.state.journal, "OpenMove (automatic)", "volume %s is in status %s during the reverse" % (vid, status))
            return self.note(vid, "the volume is in status %s: halted for a person" % status)
        if status == "retyping" or mig in ("migrating", "starting"):
            return self.note(vid, "a move is still running in the cloud; waiting for it to end before the reverse")
        if at_source and status in ("available", "in-use"):
            self.unlock_ours(vid, acts)
            if acts.locked_by_us(vid):
                return
            self.ev.save(vid, "after-reverse.json", {"volume": vol})
            L.enter(self.state.journal, vid, "done", {"result": "back on %s with type %s" % (sp["source_backend"], sp["source_type"])}, now=self.clock())
            return self.note(vid, "back on the source back-end; the instance is unlocked")
        if back and back["status"] == "done":
            if mig in ("error", "failed"):
                safety.halt(self.state.journal, "OpenMove (automatic)", "the reverse retype of %s failed in the cloud (migration_status=%s)" % (vid, mig))
                return self.note(vid, "the reverse failed in the cloud: halted for a person")
            return self.note(vid, "reverse running: migration_status=%s" % mig)
        try:
            for sid in plan.attached_servers(vol):                      # the way back is a move too: nobody changes the instance meanwhile
                if not (acts.server(sid) or {}).get("locked"):
                    acts.lock(vid, sid)
            acts.retype(vid, sp["source_type"], vol.get("volume_type"), name="retype_back")
        except CallFailed as exc:
            return self.note(vid, "the cloud refused the reverse: %s" % exc)
        self.note(vid, "retype back sent")

    # ---- an unknown outcome, read back ----------------------------------------------------------------------------
    def settle(self, vid, acts):
        """A step has an intent and no result (its answer was lost). Read the volume and close the step with what the cloud shows."""
        sp = self.spec(vid, acts) if self.phase(vid) not in (None, L.ROLLBACK) else None
        vol = acts.volume(vid)
        for row in acts.open_steps(vid):
            if row["name"] in ("retype", "retype_back"):
                moving = vol.get("os-vol-mig-status-attr:migstat") == "migrating" or str(vol.get("status")) == "retyping"
                want = (sp or {}).get("target_type") if row["name"] == "retype" else None
                if moving or (want and vol.get("volume_type") == want):
                    acts.journal.finish_step(row["id"], "done", {"settled": "read back: the move is under way or finished"})
                else:
                    acts.journal.finish_step(row["id"], "failed", {"settled": "read back: the volume was not changed"})
                    if self.phase(vid) == "cutover":
                        L.enter(self.state.journal, vid, L.ROLLBACK, {"reason": "the retype did not take effect (read back after an unknown answer)"}, now=self.clock())
            else:
                srv = [s for s in plan.attached_servers(vol)]
                locked = bool(srv) and bool(acts.server(srv[0]).get("locked"))
                acts.journal.finish_step(row["id"], "done" if locked == (row["name"] == "lock") else "failed", {"settled": "read back from the instance"})
        self.note(vid, "an unknown answer was read back and settled; carrying on")

    # ---- the numbers for the screen ------------------------------------------------------------------------------
    def view(self):
        """For the Move screen: the counters and one row per volume the engine has touched or has queued."""
        j, gate = self.state.journal, self._gate()
        started = [v for v, e in L.states(j).items() if L.method_of(L.history(j, v)) == L.RETYPE]
        ids = list(dict.fromkeys(self.active + started + self.queue))
        rows = []
        for vid in ids[:300]:
            rows.append({"id": vid, "class": (self.inv(vid) or {}).get("class"), "phase": self.phase(vid) or "queued", "note": self.notes.get(vid, ""),
                         "gates": {g: bool(L.gate_signed(j, vid, g, gate)) for g in L.VOLUME_GATES}, "failed": vid in self.failed,
                         "reversible": self.phase(vid) in ("cutover", "postcheck", "soak")})
        lo, hi = self.window
        return {"window": {"start_at": lo.strftime("%Y-%m-%dT%H:%M:%SZ") if lo else None, "stop_at": hi.strftime("%Y-%m-%dT%H:%M:%SZ") if hi else None,
                           "open": self.in_window()}, "reversible": len(self.reversible()),
                "status": self.status, "ticks": self.ticks, "queued": len(self.queue), "active": len(self.active), "finished": len(self.finished),
                "failed": len(self.failed), "volumes": rows, "more": max(0, len(ids) - 300), "halted": safety.halted(j)}
