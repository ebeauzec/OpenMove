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
from .staged import StagedMixin

ROTATION_TIMEOUT = timedelta(hours=72)      # how long a class B volume may wait for its snapshots to rotate away before it is given up
MIGRATION_TIMEOUT = timedelta(hours=6)      # how long a retype may run before the engine halts and asks a person
# phases in which data is moving, and phases in which a volume only waits on the clock (snapshots to rotate away, the safety period)
COPYING = ("copy", "sync", "cutover", "rollback")
WATCHING = ("precheck",)
LOOP_PAUSE = 5                              # seconds between two ticks
GAP_SECONDS = 90                            # a longer silence between two ticks means the computer slept or the program was stalled
MAX_RETRIES = 40                            # errors in a row (with a growing pause, up to a minute) before the loop gives up and leaves the volumes to a person
RETYPE_GRACE = timedelta(seconds=30)        # after this long, a volume that is back to normal and not on the target means the retype was undone
# The fields a retype must never change (playbook step 5.10.5): who owns the volume, where it lives and what it was made from.
IDENTITY_FIELDS = (("id", "id"), ("creation time", "created_at"), ("project", "os-vol-tenant-attr:tenant_id"), ("user", "user_id"),
                   ("snapshot origin", "snapshot_id"), ("source volume", "source_volid"), ("availability zone", "availability_zone"),
                   ("multi-attach flag", "multiattach"))
MOVED = ("A", "B")


class EngineError(ValueError):
    """A request the engine refuses with a reason a person can act on."""


class Engine(StagedMixin):
    def __init__(self, state, permit, gate, clock=None):
        self.state, self._permit, self._gate = state, permit, gate
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.ev = Evidence(Path(state.home) / "evidence")
        self.notes, self.type_map = {}, {}
        self.queue, self.active, self.failed, self.finished = [], [], set(), set()
        self.window = (None, None)                    # (start_at, stop_at): when new volumes may be taken and a retype may be sent (R3); None = no limit
        self.status, self.ticks, self._specs = "idle", 0, {}
        self.trouble, self.gaps, self.revived = "", [], 0      # the loop's last error while it retries; the silences it noticed; how many times its thread was started again
        self.errors, self._bundled = {}, set()          # the full text of the last error per volume; the reasons a bundle was already written for
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
            if v is None:                          # a volume that is under way but not in the inventory read now (it already left the source back-end, or the inventory is a later one): how it was started is in the journal
                out[vid] = self.spec_from_journal(vid, types)
                continue
            tt, why = plan.target_type_for(v.get("volume_type"), type_map, types, c["target_backend"])
            out[vid] = {"class": v.get("class"), "source_backend": c["source_backend"], "target_backend": c["target_backend"],
                        "target_type": tt, "why_no_type": why, "types": types, "source_type": v.get("volume_type")}
        return out

    def spec_from_journal(self, vid, types):
        """The spec a volume was started with, read from its precheck event. Never guessed: without the event the volume gets no target type and the engine says why."""
        pre = next((e for e in L.history(self.state.journal, vid) if e["phase"] == "precheck"), None) or {}
        c = self.state.checks or {}
        return {"class": pre.get("class"), "source_backend": pre.get("source_backend") or c.get("source_backend"),
                "target_backend": pre.get("target_backend") or c.get("target_backend"), "target_type": pre.get("target_type"),
                "why_no_type": "" if pre.get("target_type") else "the journal has no record of how this volume was started",
                "types": types, "source_type": pre.get("source_type")}

    def movable(self):
        """The classes this engine moves now: A and B always (Cinder's retype); C when a migration host and a Cinder host are registered and ready."""
        return MOVED + ("C",) if self.can_stage() else MOVED

    def method(self, vid):
        """How this volume is moved: the method it entered with, or the one its class needs."""
        h = L.history(self.state.journal, vid)
        if h:
            return L.method_of(h)
        return L.STAGED if (self.inv(vid) or {}).get("class") == "C" else L.RETYPE

    def candidates(self):
        """The selected volumes this engine can move: class A and B, and class C when the migration host is ready."""
        return [vid for vid in selection.selected_ids(self.state) if (self.inv(vid) or {}).get("class") in self.movable()]

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

    def failure(self, what, exc, volume=None):
        """Something unexpected. The full text and the traceback go to the log, the volume keeps the whole message, and the first time a
        distinct fault happens a support bundle is written so that the site has everything needed to find it."""
        d = getattr(self.state, "diag", None)
        text = "%s: %s" % (type(exc).__name__, exc)
        if volume:
            self.errors[volume] = text
        if d:
            d.exception(what, exc)
        self.auto_bundle("%s (%s)" % (what, text[:200]))

    def auto_bundle(self, reason):
        d = getattr(self.state, "diag", None)
        if not d or reason in self._bundled or len(self._bundled) >= 8:
            return
        self._bundled.add(reason)
        try:
            d.bundle(self.state.journal, extras={"engine": self.view(), "checks": self.state.checks}, remote=False, reason=reason)
        except Exception as exc:                          # a bundle that fails must not stop the engine; the log has why
            d.exception("writing the automatic support bundle", exc)

    # ---- the dry run: reads only, no lifecycle event, no change -------------------------------------------------
    def dry_run(self, ids=None, type_map=None):
        """What a run would do, volume by volume, from fresh reads. Sends nothing that changes anything."""
        acts = self.acts()
        ids = [i for i in (ids or self.candidates()) if (self.inv(i) or {}).get("class") in self.movable()]
        if not ids:
            raise EngineError("no class A or B volume is selected" if not self.can_stage() else "no volume of a class this tool moves is selected")
        specs = self.specs(acts, ids, type_map or self.type_map)
        rows, counts = [], {"ok": 0, "wait": 0, "blocked": 0}
        for vid in ids:
            try:
                vol, snaps, servers = self.read_all(acts, vid)
            except Exception as exc:
                rows.append({"id": vid, "class": specs[vid]["class"], "verdict": "blocked", "checks": [], "calls": [], "error": "could not read the volume: %s" % exc})
                counts["blocked"] += 1
                continue
            staged = self.method(vid) == L.STAGED
            checks = plan.validate(vol, snaps, servers, dict(specs[vid], staged=staged))
            if staged:
                checks += self.array_checks(vid, vol, snaps, specs[vid])
            v = plan.verdict(checks)
            counts[v] += 1
            calls = (self.staged_calls(vol, snaps, specs[vid], servers) if staged else plan.planned_calls(vol, specs[vid], servers)) if v != "blocked" else []
            rows.append({"id": vid, "class": specs[vid]["class"], "verdict": v, "target_type": specs[vid]["target_type"], "method": "staged" if staged else "retype",
                         "checks": checks, "calls": calls})
        self.dry_ok = {"run_id": (self.state.checks or {}).get("run_id"), "ids": set(ids)}       # read: a real run may start for these volumes
        self.state.journal.record("engine_dry_run", "batch", {"volumes": len(rows), "ok": counts["ok"], "wait": counts["wait"], "blocked": counts["blocked"]})
        return {"volumes": rows[:500], "more": max(0, len(rows) - 500), "counts": counts, "access": getattr(self.state, "access", "api"),
                "types": [{"name": t["name"], "backend": (t.get("extra_specs") or {}).get("volume_backend_name")} for t in specs[ids[0]]["types"]],
                "note": "Nothing was changed. Every change above would go through a permit that asks again at the moment of the call."}

    # ---- running -----------------------------------------------------------------------------------------------
    def open_volumes(self):
        """Volumes this engine started earlier (a retype) that have not reached an end: what a restart picks up again."""
        return [v for v, e in L.states(self.state.journal).items() if e["phase"] not in L.TERMINAL and (L.method_of(L.history(self.state.journal, v)) == L.RETYPE or self.can_stage())]

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
            open_now = set(self.open_volumes())                  # a volume OpenMove froze itself reads as another class now; a restart must still pick it up
            if ids and not any((self.inv(i) or {}).get("class") in self.movable() or i in open_now for i in ids):
                raise EngineError("no class A or B volume is selected" if not self.can_stage() else "no volume of a class this tool moves is selected")       # as the dry run says; never end a run quietly with nothing to do
            acts = self.acts()
            self.type_map = dict(type_map or self.type_map)
            started = self.open_volumes()
            ids = [i for i in (ids or self.candidates()) if (self.inv(i) or {}).get("class") in self.movable()]
            self.queue = [i for i in ids if self.phase(i) is None]
            self.queue.sort(key=lambda i: (self.inv(i) or {}).get("class") == "B")      # a volume that waits for its snapshots to rotate away goes last
            self.active = started
            self.failed, self.finished = set(), set()
            self._specs = self.specs(acts, list(set(self.queue) | set(self.active)), self.type_map) if (self.queue or self.active) else {}
            self.status, self._stop = "running", threading.Event()
            self.trouble = ""
            self.state.journal.record("engine_run", "batch", {"queued": len(self.queue), "resumed": len(self.active), "type_map": self.type_map})
            if thread:
                self._thread = threading.Thread(target=self._loop, daemon=True)
                self._thread.start()

    def stop(self, reason="stopped"):
        """Stop taking new volumes and stop ticking. Nothing in flight is touched: a retype already sent carries on in the cloud.
        `reason` is "stopped" for a person's stop and "shutdown" when the program is closing; a shutdown is carried on at the next start."""
        was = self.status == "running"
        self._stop.set()
        t = self._thread
        if t and t is not threading.current_thread():
            t.join(timeout=30)
        with self._mutex:
            if self.status == "running":
                self.status = "stopped"
        if was:
            self.ended(reason)

    def ended(self, reason):
        """The run ended, by a person (stopped), by itself (finished), by an error or by the program closing (shutdown). The next start reads this."""
        try:
            self.state.journal.record("engine_end", "batch", {"reason": reason, "ticks": self.ticks})
        except Exception:                                  # a closed journal at shutdown must not raise
            pass

    def ensure_alive(self):
        """Called by the guardian: if the run is meant to go on but its thread is gone, start the thread again. The journal carries the state, so nothing is lost."""
        with self._mutex:
            t = self._thread
            if self.status != "running" or self._stop.is_set() or (t is not None and t.is_alive()):
                return False
            self.revived += 1
            self.state.journal.record("engine_revive", "batch", {"times": self.revived})
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            return True

    def _loop(self):
        """Tick until the run ends. An error is not the end: the loop waits (longer each time, up to a minute) and looks again, because a tick
        reads the cloud afresh and the journal has every step. Only a long unbroken run of errors hands the volumes back to a person."""
        fails, last = 0, self.clock()
        try:
            while not self._stop.is_set():
                begun = self.clock()
                gap = (begun - last).total_seconds()                  # from the start of the last look to the start of this one: a tick plus the pause, normally under a minute
                last = begun
                if gap > GAP_SECONDS + LOOP_PAUSE and self.ticks:
                    self.gaps.append(round(gap))
                    self.trouble = ""
                    self.state.journal.record("engine_gap", "batch", {"seconds": round(gap), "note": "the computer slept or the program was stalled; every volume is read afresh"})
                    for vid in self.active:
                        self.note(vid, "the program was paused for %d s (sleep?); looking at the volume again" % round(gap))
                try:
                    self.tick()
                    fails, self.trouble = 0, ""
                except Exception as exc:                      # not the end: wait and look again
                    fails += 1
                    self.trouble = "retrying after an error (%d in a row): %s: %s" % (fails, type(exc).__name__, exc)
                    self.failure("the engine loop had an error (%d in a row)" % fails, exc)
                    if fails >= MAX_RETRIES:
                        self.status = "stopped after an error: %s" % exc
                        self.ended("error")
                        return
                    self._stop.wait(min(60, LOOP_PAUSE * 2 ** min(fails, 4)))
                    continue
                if self.status != "running":
                    if self.status == "finished":
                        self.ended("finished")
                    return
                self._stop.wait(LOOP_PAUSE)
        except BaseException as exc:                          # the thread is going down for a reason we did not plan for: leave the marker, the guardian starts it again
            self.failure("the engine thread ended unexpectedly", exc)
            raise

    def in_window(self):
        lo, hi = self.window
        now = self.clock()
        return (lo is None or now >= lo) and (hi is None or now < hi)

    def tick(self):
        """One pass: top up the active set, advance every active volume by one step, drop the ones that ended."""
        with self._mutex:
            self.ticks += 1
            h = safety.halted(self.state.journal)
            if h:
                self.auto_bundle("halt by %s: %s" % (h.get("by"), h.get("reason")))
            o = selection.options_of(self.state)
            self.fill(o)
            todo = list(self.active)
            while todo:
                self.step(todo.pop(0), o)
                if self._stop.is_set():
                    break
                todo += self.fill(o)                           # a place that has just been freed goes to the next volume in the queue now, not at the next look
            limit = o["stop_after_failures"]
            if limit and len(self.failed) >= limit and not safety.halted(self.state.journal):
                safety.halt(self.state.journal, "OpenMove (automatic)", "%d volumes failed in this run (the limit you set)" % len(self.failed))
            if not self.queue and not self.active:
                self.status = "finished"

    def counts(self):
        """The volumes the engine holds, by what they use: copying (a copy place), in progress (copying, or ready and waiting), watched (waiting on the clock), in the safety period."""
        ph = [self.phase(v) for v in self.active]
        copying = sum(1 for p in ph if p in COPYING)
        watched = sum(1 for p in ph if p in WATCHING)
        soak = sum(1 for p in ph if p == "soak")
        return {"copying": copying, "in_progress": len(ph) - watched - soak, "watched": watched, "soak": soak, "queued": len(self.queue)}

    def room(self, o):
        """Is there room to take one more volume from the queue? Volumes waiting on the clock (snapshots, the safety period) do not count as in progress; at most as many are watched as there are copy places."""
        c = self.counts()
        return c["in_progress"] < o["max_in_flight"] and c["watched"] < o["concurrency"]

    def copy_place(self, vid):
        """May this volume start moving data now? One that is already copying keeps its place; a new one needs a free place among the copy places."""
        o = selection.options_of(self.state)
        return self.phase(vid) in COPYING or self.counts()["copying"] < o["concurrency"]

    def copy_wait_note(self):
        o = selection.options_of(self.state)
        return "ready: waiting for a free copy place (%d of %d in use)" % (self.counts()["copying"], o["concurrency"])

    def fill(self, o):
        """Take volumes from the queue while there is room. A volume only waits on the clock (snapshots to rotate away, the safety period) is not counted as in progress. Returns the volumes taken."""
        taken = []
        while self.queue and self.room(o) and not safety.halted(self.state.journal) and self.in_window():
            vid = self.queue.pop(0)
            self.active.append(vid)
            taken.append(vid)
        return taken

    def step(self, vid, o):
        """One step of one volume; a volume that has ended leaves the active set."""
        try:
            self.advance(vid)
        except safety.Refused as exc:
            self.note(vid, "waiting: " + str(exc))
        except OutcomeUnknown as exc:
            self.note(vid, str(exc))
        except Exception as exc:                       # a read that failed, a surprise: say so, change nothing, look again next tick
            self.note(vid, "could not continue (%s: %s); will look again" % (type(exc).__name__, exc))
            self.failure("could not continue with %s" % vid, exc, volume=vid)
        ph = self.phase(vid)
        if ph in L.TERMINAL:
            self.active.remove(vid)
            (self.finished if ph == "done" and vid not in self.failed else self.failed).add(vid)

    # ---- one volume, one step ---------------------------------------------------------------------------------
    def advance(self, vid):
        acts = self.acts()
        ph = self.phase(vid)
        h = safety.halted(self.state.journal)
        if h and ph != "soak":                      # a halt waits for a person, also before an unknown answer is read back
            return self.note(vid, "halted by %s: %s" % (h["by"], h["reason"]))
        if ph and not acts.open_steps(vid):         # the option "one approval for every volume": the second approver of the safety checks stands for this volume's gates
            L.apply_standing(self.state.journal, vid, self._gate(), selection.options_of(self.state), now=self.clock(), rules=self.rules())
        if acts.open_steps(vid):
            return self.settle(vid, acts)
        if self.method(vid) == L.STAGED:
            return self.staged_step(vid, acts)
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
        if not self.copy_place(vid):
            return self.note(vid, self.copy_wait_note())
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
        out = []
        for v, e in L.states(self.state.journal).items():
            if e["phase"] not in ("cutover", "postcheck", "soak"):
                continue
            if L.method_of(L.history(self.state.journal, v)) == L.RETYPE or (e["phase"] == "soak" or self.sctx(v).get("switched_at")):
                out.append(v)
        return out

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
                if self.method(vid) == L.STAGED:
                    rows.append(self.staged_reverse_preview(vid, acts, ph, pre))
                    continue
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
        if self.method(vid) == L.STAGED:
            if self.phase(vid) == "cutover" and not self.sctx(vid).get("switched_at"):
                raise EngineError("the switch has not been sent yet; the cutover is rolled back by itself if it fails, or stop the run")
            self.s_reverse_request(vid, reason, by)
        else:
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
        if self.method(vid) == L.STAGED:
            return self.staged_settle(vid, acts)
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
        started = [v for v, e in L.states(j).items() if L.method_of(L.history(j, v)) == L.RETYPE or self.sctx(v).get("leg")]
        ids = list(dict.fromkeys(self.active + started + self.queue))
        rows, rev = [], set(self.reversible())
        for vid in ids[:300]:
            rows.append({"id": vid, "class": (self.inv(vid) or {}).get("class"), "phase": self.phase(vid) or "queued", "note": self.notes.get(vid, ""),
                         "gates": {g: bool(L.gate_signed(j, vid, g, gate)) for g in L.VOLUME_GATES}, "failed": vid in self.failed,
                         "reversible": vid in rev, "method": "staged" if self.method(vid) == L.STAGED else "retype", "error": self.errors.get(vid, ""),
                         "stage": self.staged_progress(vid) if self.method(vid) == L.STAGED else None})
        lo, hi = self.window
        slots = dict(self.counts(), copy_limit=selection.options_of(self.state)["concurrency"], in_progress_limit=selection.options_of(self.state)["max_in_flight"])
        return {"slots": slots, "window": {"start_at": lo.strftime("%Y-%m-%dT%H:%M:%SZ") if lo else None, "stop_at": hi.strftime("%Y-%m-%dT%H:%M:%SZ") if hi else None,
                           "open": self.in_window()}, "reversible": len(self.reversible()),
                "status": self.status, "ticks": self.ticks, "queued": len(self.queue), "active": len(self.active), "finished": len(self.finished),
                "failed": len(self.failed), "volumes": rows, "more": max(0, len(ids) - 300), "halted": safety.halted(j),
                "trouble": self.trouble, "gaps": self.gaps[-5:], "revived": self.revived}
