# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The staged method for class C volumes: volumes with snapshots that Cinder will not move (docs/MIGRATION_METHOD.md, docs/MOVER.md).

The order of one volume is lifecycle.ORDER:
  precheck > validate > copy > sync > recheck (twice, apart) > [G2 and G3 signed by a second person] > cutover > postcheck > [G4] > soak > done

  copy       the staging volume is created on the target and every snapshot image is copied to it in chronological order; after each copy a
             snapshot of the staging volume is taken under the name the target driver expects, and the image on the target is hashed and compared
             with the image that was read (S1 to S4).
  sync       a snapshot a tenant made since is copied the same way; one the tenant deleted is noted (it is destroyed after the cutover, X3).
  recheck    the volume is read again; nothing new, nothing missing, the same fingerprint (S5). Twice, five minutes apart, the newest recent.
  cutover    lock the instance (if it is attached and not locked already) > rollback clone > reconcile > freeze the record > destination record
             and the staging volume's name > record checks C1 to C4 > field sync > the switch (Nova's volume swap for an attached volume; a final
             copy, a compare and Cinder's completion for a detached one) > wait for Cinder to report success.
  postcheck  X1, X3 and X6 and the comparison with the state before the move.
  soak       the source is kept. Rollback R3 (a reverse migration) stays possible.
  done       after the soak: the source snapshots, the old backing and the rollback clone are released (X8).

Rollbacks: R1 before the destination record exists (remove what the run created); R2 until the swap is accepted (unfreeze, remove the
destination record, remove the staging volume and the clones, unlock); R3 after it (the same method, the other way round, with a safety copy).

Everything that matters is enforced by lifecycle.enter, safety.Permit and actions.Actions. This file decides what comes next. The state of
a volume that the phase does not hold (which image is copied, the destination record, what is frozen) is in the journal as events of kind
"stage", so a restart carries on from the journal. Every change is a journaled step with its undo; every step is safe to repeat.
"""
import hashlib
import json
from datetime import timedelta

from .. import lifecycle as L
from .. import safety, selection
from . import plan
from .actions import CallFailed, OutcomeUnknown

GIB = 1024 ** 3
CUT = ("lock", "rollback_clone", "reconcile", "freeze", "record", "verify_record", "sync_fields", "switch", "wait")
SWITCH_TIMEOUT = timedelta(hours=6)       # a swap or completion that has not reported success by then halts for a person
MAX_TRIES = 3                             # a mover job that died is started again this many times before the volume is given up
FROZEN_FIELDS = ("os-vol-tenant-attr:tenant_id", "user_id")      # fields a tenant may change until the freeze (a transfer); compared with the capture at the end of the freeze
FIELDS_EQUAL = ("project_id", "user_id", "display_name", "display_description", "size", "bootable", "created_at", "snapshot_id", "source_volid",
                "availability_zone", "encryption_key_id", "multiattach")
FINAL = ("available", "in-use")


def name_id_of(vol):
    return vol.get("os-vol-mig-status-attr:name_id") or vol["id"]


class StagedMixin:
    """Mixed into Engine. Uses its state, journal, evidence, notes, clock and gate."""

    # ---- what the journal keeps ---------------------------------------------------------------------------------------------------
    def sctx(self, vid):
        ctx = {}
        for e in reversed(self.state.journal.events("stage", vid, 1000000)):
            d = e["data"]
            if d.get("_leg_start"):
                ctx = {}
            ctx.update({k: v for k, v in d.items() if not k.startswith("_")})
        return ctx

    def sset(self, vid, **kw):
        self.state.journal.record("stage", vid, kw)

    def fabric(self):
        return getattr(self.state, "fabric", None)

    def can_stage(self):
        return self.fabric() is not None

    def log(self, level, message, **data):
        d = getattr(self.state, "diag", None)
        if d:
            d.log(level, message, **data)

    def snote(self, vid, text, level="info"):
        self.note(vid, text)
        self.log(level, text, volume=vid, phase=self.phase(vid))

    def staged_calls(self, vol, snaps, spec, servers):
        """What a staged move would do, in order, each with its undo (shown by the dry run before anything runs)."""
        n, sids = len(snaps), plan.attached_servers(vol)
        be = (spec["source_backend"], spec["target_backend"])
        calls = [{"step": "stage_create", "what": "create the staging volume (%d GiB) on %s through the data mover" % (vol["size"], be[1]), "undo": "remove it (R1/R2)"},
                 {"step": "copy", "what": "copy %d snapshot image(s) in order, take a snapshot of the staging volume after each, hash and compare each (S1-S4)" % n,
                  "undo": "remove the staging volume; the source is only read"},
                 {"step": "recheck", "what": "read the source again twice, at least %d s apart, until nothing changed (S5)" % L.RECHECK_GAP, "undo": "none: reads only"}]
        for sid in sids:
            if not (servers.get(sid) or {}).get("locked"):
                calls.append({"step": "lock", "what": "POST /servers/%s/action {\"lock\": null}" % sid, "undo": "unlock the instance"})
        calls += [{"step": "rollback_clone", "what": "make a full copy of the source on %s for rollback (R2). It is a point in time: writes made after it was taken are not in it" % be[0], "undo": "remove it"},
                  {"step": "freeze", "what": "Cinder helper: set the volume to %s and migration_status migrating" % ("retyping/in-use" if sids else "maintenance/available"),
                   "undo": "unfreeze: restore the recorded status, previous_status and migration_status"},
                  {"step": "record_create", "what": "Cinder helper: create the destination record on %s with the target type" % be[1], "undo": "delete the destination record"},
                  {"step": "stage_rename", "what": "rename the staging volume to volume-<destination record id>", "undo": "rename it back"},
                  {"step": "sync_fields", "what": "Cinder helper: copy the volume's fields to the destination record", "undo": "none: the record is deleted on rollback"}]
        if sids:
            calls.append({"step": "swap", "what": "PUT /servers/%s/os-volume_attachments/%s {\"volumeAttachment\": {\"volumeId\": <destination>}}" % (sids[0], vol["id"]),
                          "undo": "swap back by hand; the source is untouched until the completion"})
        else:
            calls += [{"step": "final_copy", "what": "copy the source one last time (changed blocks only) and compare it with the destination", "undo": "none: reads and writes the staging volume only"},
                      {"step": "complete", "what": "POST /volumes/%s/action {\"os-migrate_volume_completion\": ...}" % vol["id"], "undo": "reverse migration (R3)"}]
        for sid in sids:
            if not (servers.get(sid) or {}).get("locked"):
                calls.append({"step": "unlock", "what": "POST /servers/%s/action {\"unlock\": null} after the post-check passes" % sid, "undo": "lock the instance again"})
        calls.append({"step": "release", "what": "after the soak: remove the source snapshots, the old backing volume and the rollback clone (X8)", "undo": "none: this is the end"})
        return calls

    def staged_progress(self, vid):
        """For the screen: how far the images are, which cutover step is next, and what is running."""
        ctx = self.sctx(vid)
        chain = ctx.get("chain") or []
        return {"images": len(chain), "verified": sum(1 for c in chain if c.get("state") == "verified"), "cut": ctx.get("cut"), "leg": (ctx.get("leg") or {}).get("kind"),
                "frozen": bool(ctx.get("frozen")) and not ctx.get("unfrozen"), "dest": ctx.get("dest_id"), "aborting": ctx.get("aborting")}

    def staged_reverse_preview(self, vid, acts, ph, pre):
        ctx = self.sctx(vid)
        first = ctx.get("leg") or {}
        vol = acts.volume(vid)
        snaps = acts.snapshots(vid)
        servers = {s: acts.server(s) for s in plan.attached_servers(vol)}
        calls = self.staged_calls(vol, snaps, {"source_backend": first.get("dst"), "target_backend": first.get("src")}, servers)
        return {"id": vid, "phase": ph, "can": True, "from_type": vol.get("volume_type"), "to_type": pre["source_type"], "source_backend": pre.get("source_backend"),
                "calls": calls, "method": "staged"}

    def staged_settle(self, vid, acts):
        """A step has an intent and no result (its answer was lost, or the program stopped in the middle). Read the cloud and the hosts and close it.
        Steps on the hosts are written to be repeated, so they are closed as 'not seen' and done again; the changes to the cloud are read back."""
        ctx = self.sctx(vid)
        j = acts.journal
        vol = acts.volume(vid)
        for row in acts.open_steps(vid):
            name = row["name"]
            if name in ("lock", "unlock"):
                srv = plan.attached_servers(vol)
                locked = bool(srv) and bool(acts.server(srv[0]).get("locked"))
                j.finish_step(row["id"], "done" if locked == (name == "lock") else "failed", {"settled": "read back from the instance"})
            elif name in ("swap", "complete"):
                rec = None
                if ctx.get("dest_id"):
                    try:
                        rec = acts.volume(ctx["dest_id"])
                    except Exception:
                        rec = None
                mig = vol.get("os-vol-mig-status-attr:migstat")
                took = mig in ("completing", "success") or (name == "swap" and rec is not None and str(rec.get("status")) not in ("available", "creating"))
                j.finish_step(row["id"], "done" if took else "failed", {"settled": "read back: the %s %s" % (name, "was accepted" if took else "did not take effect")})
                if took:
                    self.sset(vid, **{"swap_sent" if name == "swap" else "completion_sent": True, "switched_at": self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")})
            elif name == "force_delete":
                gone = True
                try:
                    acts.volume(ctx.get("dest_id"))
                    gone = False
                except Exception:
                    pass
                j.finish_step(row["id"], "done" if gone else "failed", {"settled": "read back from Cinder"})
            else:
                j.finish_step(row["id"], "failed", {"settled": "result not seen; the step is repeated, which is safe: it returns what the first call made"})
        self.snote(vid, "an unknown answer was read back and settled; carrying on")

    # ---- dispatch -----------------------------------------------------------------------------------------------------------------
    def staged_step(self, vid, acts):
        ph = self.phase(vid)
        if ph is None:
            return self.s_precheck(vid, acts)
        ctx = self.sctx(vid)
        if ctx.get("aborting") and ph in L.ABORTABLE:
            return self.s_cleanup_abort(vid, acts, ctx)
        if ph == "precheck":
            return self.s_validate(vid, acts)
        if ph == "validate":
            if not self.copy_place(vid):
                return self.snote(vid, "validated: waiting for a free copy place (%d of %d in use)" % (self.counts()["copying"], selection.options_of(self.state)["concurrency"]))
            n = len(ctx.get("chain", []))
            L.enter(self.state.journal, vid, "copy", {"validated": True, "snapshots": n}, now=self.clock(), rules=self.rules())
            return self.snote(vid, "copying %d snapshot image(s) to the target" % n)
        if ph == "copy":
            return self.s_copy(vid, acts, ctx)
        if ph in ("sync", "recheck"):
            return self.s_sync(vid, acts, ctx)
        if ph == "cutover":
            return self.s_cutover(vid, acts, ctx)
        if ph == "postcheck":
            return self.s_postcheck(vid, acts, ctx)
        if ph == "soak":
            return self.s_soak(vid, acts, ctx)
        if ph == L.ROLLBACK:
            return self.s_rollback(vid, acts, ctx)

    def fail_hard(self, vid, reason):
        """A surprise in the staged flow: say why, halt, and leave everything as it is for a person."""
        self.log("error", "halting: " + reason, volume=vid)
        safety.halt(self.state.journal, "OpenMove (automatic)", reason)
        self.note(vid, "halted: " + reason)

    # ---- precheck and validate ----------------------------------------------------------------------------------------------------
    def s_precheck(self, vid, acts):
        sp = self.spec(vid, acts)
        fab = self.fabric()
        if fab is None:
            return self.snote(vid, "not started: no migration host is registered and ready")
        try:
            L.enter(self.state.journal, vid, "precheck", {"class": sp["class"], "method": L.STAGED, "source_backend": sp["source_backend"], "target_backend": sp["target_backend"],
                                                          "source_type": sp["source_type"], "target_type": sp["target_type"], "family": fab.family},
                    gate=self._gate(), now=self.clock(), rules=self.rules())
        except L.LifecycleError as exc:
            return self.snote(vid, "not started: " + str(exc))
        self.s_validate(vid, acts)

    def array_checks(self, vid, vol, snaps, sp):
        """Does the array show what Cinder says: the volume, each snapshot with the right origin, room on the target."""
        fab, out = self.fabric(), []

        def add(name, ok, detail):
            out.append({"name": name, "ok": bool(ok), "detail": detail, "wait": False})
        ad, nid = fab.adapter, name_id_of(vol)
        try:
            dev = ad.locate_volume(sp["source_backend"], nid)
            add("The source volume is on the array", dev["bytes"] == vol["size"] * GIB, "%s, %d bytes" % (dev["name"], dev["bytes"]) if dev["bytes"] == vol["size"] * GIB
                else "the array holds %d bytes, Cinder says %d GiB" % (dev["bytes"], vol["size"]))
        except Exception as exc:
            add("The source volume is on the array", False, str(exc))
            return out
        missing = []
        for s in snaps:
            try:
                ad.locate_snapshot(sp["source_backend"], s["id"], nid)
            except Exception as exc:
                missing.append("%s: %s" % (s["id"][:8], exc))
        add("Every Cinder snapshot is on the array", not missing, "%d snapshot(s) found" % len(snaps) if not missing else "; ".join(missing[:3]))
        try:
            free = ad.free_bytes(sp["target_backend"])
            need = ad.required_bytes(vol["size"] * GIB, len(snaps))
            add("The target has room", free is None or free >= need, "%d MiB free, %d MiB needed" % ((free or 0) // 1048576, need // 1048576))
        except Exception as exc:
            add("The target has room", False, str(exc))
        return out

    def s_validate(self, vid, acts):
        sp = dict(self.spec(vid, acts), staged=True)
        fab = self.fabric()
        vol, snaps, servers = self.read_all(acts, vid)
        checks = plan.validate(vol, snaps, servers, sp)
        odd = [s for s in snaps if s["status"] != "available"]
        checks.append({"name": "All snapshots are available", "ok": not odd, "wait": True,
                       "detail": "every snapshot is available" if not odd else "a snapshot is not available: %s" % ", ".join("%s %s" % (s["id"][:8], s["status"]) for s in odd)})
        checks += self.array_checks(vid, vol, snaps, sp)
        v = plan.verdict(checks)
        self.ev.save(vid, "validated.json", {"volume": vol, "snapshots": snaps, "servers": servers, "checks": checks})
        if v == "blocked":
            return self.s_abort(vid, "; ".join(c["detail"] for c in checks if not c["ok"] and not c["wait"]))
        if v == "wait":
            return self.snote(vid, "waiting: " + "; ".join(c["detail"] for c in checks if not c["ok"]))
        dest_host = next((p["name"] for p in acts.pools() if plan.backend_of(p["name"]) == sp["target_backend"]), None)
        if not dest_host:
            return self.s_abort(vid, "the scheduler reports no pool for the target back-end %s" % sp["target_backend"])
        dtype_id = next((t["id"] for t in sp["types"] if t["name"] == sp["target_type"]), None)
        leg = {"kind": "forward", "src": sp["source_backend"], "dst": sp["target_backend"], "dtype": sp["target_type"], "dtype_id": dtype_id, "dest_host": dest_host,
               "stype": sp["source_type"]}
        chain = [{"id": s["id"], "bytes": s["size"] * GIB, "state": "todo", "tries": 0} for s in sorted(snaps, key=lambda x: (x.get("created_at") or "", x["id"]))]
        self.sset(vid, leg=leg, size_bytes=vol["size"] * GIB, chain=chain, gen=0, src_name_id=name_id_of(vol), dropped=[], pair_type=vol.get("volume_type"))
        L.enter(self.state.journal, vid, "validate", {"room_ok": True, "snapshots_now": len(snaps)}, now=self.clock(), rules=self.rules())
        self.snote(vid, "validated: %d snapshot(s) to rebuild on %s" % (len(chain), sp["target_backend"]))

    # ---- abort (R1): remove what the run made, then give the volume up ---------------------------------------------------------------------
    def s_abort(self, vid, reason):
        self.log("warn", "giving up: " + reason, volume=vid)
        self.sset(vid, aborting=reason)
        self.failed.add(vid)
        self.note(vid, "giving up: " + reason)

    def s_cleanup_abort(self, vid, acts, ctx):
        left = self.remove_made(vid, acts, ctx)
        if left:
            return self.snote(vid, "giving up: removing %s" % left)
        L.enter(self.state.journal, vid, L.ABORTED, {"reason": str(ctx["aborting"])}, now=self.clock())
        self.snote(vid, "given up: " + str(ctx["aborting"]))

    def remove_made(self, vid, acts, ctx):
        """R1/R2: remove the staging volume (under either name), its snapshots, the clones and cancel running jobs. Returns what is still to do, or ''."""
        fab, leg = self.fabric(), ctx.get("leg") or {}
        if not leg:
            return ""
        jobs = [c["job"] for c in ctx.get("chain", []) if c.get("state") in ("copying", "hashing") and c.get("job")]
        jobs += [(ctx.get(k) or {}).get("id") for k in ("final", "final_cmp", "rb_job", "safety_job")]
        for jid in [j for j in jobs if j]:
            try:
                fab.mover_host.mover("job.cancel", {"job": jid})
            except Exception as exc:                       # a job that is already over cannot be cancelled; say so and go on
                self.log("warn", "could not cancel job %s: %s" % (jid, exc), volume=vid)
        on_dst = [n for n in ((ctx.get("stage") or {}).get("name"), "volume-%s" % ctx["dest_id"] if ctx.get("dest_id") else None) if n]
        on_src = [x["name"] for x in (ctx.get("rb"), ctx.get("safety")) if isinstance(x, dict) and x.get("name")]
        for backend, names in ((leg["dst"], on_dst), (leg["src"], on_src)):
            for n in dict.fromkeys(names):
                try:
                    if fab.adapter.exists(backend, n):
                        acts.op(vid, "remove", {"remove": n, "on": backend}, {"undo": "none: the source is untouched and the staging volume can be rebuilt"},
                                lambda n=n, backend=backend: fab.adapter.remove(backend, n))
                except CallFailed as exc:
                    return "%s (%s)" % (n, exc)
        return ""

    # ---- the copy -----------------------------------------------------------------------------------------------------------------
    def copy_rate(self):
        """MiB/s a copy may use: the lower of the host's own limit and the operator's cap (the setting 'Copy rate cap'). None means no limit."""
        host = self.fabric().rate()
        cap = selection.options_of(self.state).get("rate_cap_mb_s")
        known = [float(x) for x in (host, cap) if x]
        return min(known) if known else None

    def copy_step(self, vid, acts, limit=None):
        """Do what comes next for the staging volume and the images. 'done' when every image (the first `limit`, if given) is copied and verified."""
        fab = self.fabric()
        ad, rate = fab.adapter, self.copy_rate()
        for _ in range(40):                                          # several quick transitions per tick; stop at the first thing that must wait
            ctx = self.sctx(vid)
            leg, size = ctx["leg"], ctx["size_bytes"]
            stage = ctx.get("stage")
            if not stage:
                res = acts.op(vid, "stage_create", {"on": leg["dst"], "bytes": size}, {"undo": "remove the staging volume"},
                              lambda: ad.stage_create(leg["dst"], vid, size))
                if res["bytes"] != size:
                    return self.s_abort(vid, "the staging volume has %d bytes, the source %d (S1)" % (res["bytes"], size)) or "working"
                self.sset(vid, stage={k: res[k] for k in ("name", "path", "vg", "bytes")})
                continue
            chain = [dict(c) for c in ctx["chain"]]
            todo = [i for i, c in enumerate(chain) if c["state"] != "verified"]
            if limit is not None:
                todo = [i for i in todo if i < limit]
            if not todo:
                return "done"
            i = todo[0]
            c = chain[i]
            jid = lambda tag: "om-%s-%s-%s-g%d-t%d" % (vid[:8], tag, c["id"], ctx.get("gen", 0), c["tries"])
            if c["state"] == "todo":
                ref = ad.locate_snapshot(leg["src"], c["id"], ctx["src_name_id"])
                job = jid("c")
                acts.op(vid, "copy", {"snapshot": c["id"], "from": ref["path"], "to": stage["path"], "job": job},
                        {"undo": "none needed: the staging volume is rebuilt or removed; the source is only read"},
                        lambda: ad.job(job, "copy", {"src": ref["path"], "dst": stage["path"], "bytes": size, "rate_mbps": rate}))
                chain[i].update(state="copying", job=job)
                self.sset(vid, chain=chain)
                continue
            if c["state"] in ("copying", "hashing"):
                st = ad.job_status(c["job"])
                if st["state"] == "running":
                    pct = int(100.0 * st.get("done", 0) / max(1, st.get("total", 1)))
                    self.snote(vid, "%s image %d of %d (%s): %d%%" % ("copying" if c["state"] == "copying" else "checking", i + 1, len(chain), c["id"][:8], pct))
                    return "working"
                if st["state"] != "done":
                    chain[i]["tries"] += 1
                    self.log("warn", "job %s %s: %s" % (c["job"], st["state"], st.get("error")), volume=vid, job=st)
                    if chain[i]["tries"] >= MAX_TRIES:
                        return self.s_abort(vid, "the copy of snapshot %s failed %d times: %s" % (c["id"][:8], MAX_TRIES, st.get("error"))) or "working"
                    chain[i].update(state="todo" if c["state"] == "copying" else "snapped", job=None)
                    self.sset(vid, chain=chain)
                    return "working"
                if c["state"] == "copying":
                    chain[i].update(state="copied", src_sha=st["result"]["source_sha256"], job=None)
                else:
                    if st["result"]["sha256"] != c.get("src_sha"):
                        self.ev.save(vid, "mismatch-%s.json" % c["id"][:8], {"snapshot": c["id"], "read": c.get("src_sha"), "target": st["result"]})
                        return self.s_abort(vid, "the image of snapshot %s on the target is not identical to the one read (S3)" % c["id"][:8]) or "working"
                    chain[i].update(state="verified", tgt_sha=st["result"]["sha256"], job=None)
                self.sset(vid, chain=chain)
                continue
            if c["state"] == "copied":
                res = acts.op(vid, "stage_snapshot", {"snapshot": c["id"], "of": stage["name"]}, {"undo": "remove the snapshot of the staging volume"},
                              lambda: ad.stage_snapshot(leg["dst"], stage["name"], c["id"], size))
                chain[i].update(state="snapped", tpath=res["path"])
                self.sset(vid, chain=chain)
                continue
            if c["state"] == "snapped":
                job = jid("h")
                acts.op(vid, "hash", {"snapshot": c["id"], "on": c["tpath"], "job": job}, {"undo": "none: reading only"},
                        lambda: ad.job(job, "hash", {"dev": c["tpath"], "bytes": size, "rate_mbps": rate}))
                chain[i].update(state="hashing", job=job)
                self.sset(vid, chain=chain)
                continue
        return "working"

    def safe_copy(self, vid, acts, limit=None):
        """copy_step, with a step the host refused counted: after MAX_TRIES in a row the volume is given up, with the whole error."""
        try:
            r = self.copy_step(vid, acts, limit)
        except CallFailed as exc:
            n = self.sctx(vid).get("op_fail", 0) + 1
            self.sset(vid, op_fail=n)
            self.log("warn", "a copy step failed (%d of %d): %s" % (n, MAX_TRIES, exc), volume=vid)
            if n >= MAX_TRIES:
                self.s_abort(vid, "a step on the migration host failed %d times in a row: %s" % (MAX_TRIES, exc))
            else:
                self.snote(vid, "a step on the migration host failed (%d of %d); trying again: %s" % (n, MAX_TRIES, exc), "warn")
            return "working"
        if self.sctx(vid).get("op_fail"):
            self.sset(vid, op_fail=0)
        return r

    def s_copy(self, vid, acts, ctx):
        n = L.current(self.state.journal, vid).get("snapshots", 0)
        r = self.safe_copy(vid, acts, limit=n)
        if r != "done" or self.sctx(vid).get("aborting"):
            return
        ctx = self.sctx(vid)
        L.enter(self.state.journal, vid, "sync", {"size_equal": (ctx["stage"]["bytes"] == ctx["size_bytes"]), "hash_match": True, "chain_match": True,
                                                  "snapshots_matched": n}, now=self.clock(), rules=self.rules())
        self.snote(vid, "copied and verified: %d image(s) identical to the source; checking again before the cutover" % n)

    # ---- sync and recheck ---------------------------------------------------------------------------------------------------------
    @staticmethod
    def fingerprint(vol, snaps):
        d = {"size": vol.get("size"), "status": vol.get("status"), "host": vol.get("os-vol-host-attr:host"), "type": vol.get("volume_type"),
             "att": sorted(a.get("server_id") or "" for a in vol.get("attachments") or []), "snaps": sorted((s["id"], s["status"]) for s in snaps)}
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]

    def s_sync(self, vid, acts, ctx):
        ph, j = self.phase(vid), self.state.journal
        sp = dict(self.spec(vid, acts), staged=True)
        vol, snaps, servers = self.read_all(acts, vid)
        if not self.align_size(vid, acts, ctx, vol):
            return
        checks = plan.validate(vol, snaps, servers, sp)
        bad = [c["detail"] for c in checks if not c["ok"]]
        chain_ids = [c["id"] for c in ctx["chain"]]
        have = {s["id"] for s in snaps}
        new = [s for s in snaps if s["id"] not in chain_ids]
        pending = [c for c in ctx["chain"] if c["state"] != "verified"]
        if bad:
            if ph == "recheck" or ph == "sync":
                self.snote(vid, "cannot recheck: " + "; ".join(bad))
                if ph == "recheck":
                    L.enter(j, vid, "recheck", {"ok": False, "fail_reason": "; ".join(bad), "source_fingerprint": None}, now=self.clock(), rules=self.rules())
                return
        if new or pending:
            if any(s["status"] != "available" for s in snaps):
                return self.snote(vid, "waiting for a snapshot that is still being made or deleted")
            if new:
                chain = ctx["chain"] + [{"id": s["id"], "bytes": s["size"] * GIB, "state": "todo", "tries": 0}
                                        for s in sorted(new, key=lambda x: (x.get("created_at") or "", x["id"]))]
                self.sset(vid, chain=chain)
                self.snote(vid, "%d new snapshot(s) made by a tenant: copying them" % len(new))
                if ph == "recheck":
                    L.enter(j, vid, "recheck", {"ok": False, "fail_reason": "a tenant snapshot appeared", "source_fingerprint": self.fingerprint(vol, snaps), "pending_blocks": len(new)},
                            now=self.clock(), rules=self.rules())
                return
            self.safe_copy(vid, acts)
            return
        gone = [i for i in chain_ids if i not in have and i not in ctx.get("dropped", [])]
        if gone:
            self.sset(vid, dropped=ctx.get("dropped", []) + gone)
            self.snote(vid, "a tenant deleted %d snapshot(s); the copy on the target is kept until after the cutover" % len(gone))
        now = self.clock()
        cur = L.current(j, vid)
        age = (now - L._parse(cur["at"])).total_seconds()
        gap = max(L.RECHECK_GAP, int(self.rules().get("recheck_gap") or 0))
        ready, why = L.cutover_ready(j, vid, self._gate(), now, self.rules())
        if ph == "recheck" and ready:
            return self.s_before_cutover(vid, acts, sp, vol, snaps, servers)
        if ph == "recheck" and age < gap:
            reasons = [c["detail"] for c in L.readiness(j, vid, self._gate(), now, self.rules()) if not c["ok"] and not c.get("pending")]
            return self.snote(vid, "rechecked; next check in %d s. Waiting for: %s" % (gap - age, "; ".join(reasons) or "the second check"))
        ev = {"ok": True, "source_fingerprint": self.fingerprint(vol, snaps), "lag_s": 0, "pending_blocks": 0, "reconciled": not gone, "snapshots": len(snaps)}
        L.enter(j, vid, "recheck", ev, now=now, rules=self.rules())
        unmet = [c["detail"] for c in L.readiness(j, vid, self._gate(), now, self.rules()) if not c["ok"] and not c.get("pending")]
        self.snote(vid, "recheck %s: %d snapshot(s), fingerprint %s%s" % ("passed" if ev["reconciled"] else "restarted (a snapshot was deleted)", len(snaps),
                                                                        ev["source_fingerprint"], ". Waiting for: " + "; ".join(unmet) if unmet else ""))

    def align_size(self, vid, acts, ctx, vol):
        """Playbook 5.4.1 and 5.8.3a (v1.4): a tenant can extend the volume, or change its type, until the freeze. Nothing is frozen yet, so the
        run can follow. A larger source: grow the staging volume to match (the final copy and its compare cover the new region). A different
        type: give the volume up (R1), because the way back would restore the wrong type. A smaller source: nothing explains it. Returns True
        when the staging volume and the source agree."""
        want, have = int(vol.get("size") or 0) * GIB, int(ctx["size_bytes"])
        kind = ctx.get("pair_type")
        if kind is not None and vol.get("volume_type") != kind:
            self.s_abort(vid, "the volume type was changed from %s to %s after staging began: the way back would restore the wrong type. Start again" % (kind, vol.get("volume_type")))
            return False
        if want == have:
            return True
        if want < have:
            self.s_abort(vid, "the source is %d bytes now and was %d bytes when staging began: a volume cannot shrink and nothing in the method explains it" % (want, have))
            return False
        stage, leg, fab = ctx.get("stage"), ctx["leg"], self.fabric()
        if stage:
            res = acts.op(vid, "stage_extend", {"on": leg["dst"], "volume": stage["name"], "from": have, "to": want}, {"undo": "none: the staging volume is removed or rebuilt on rollback"},
                          lambda: fab.adapter.stage_extend(leg["dst"], stage["name"], want))
            if res["bytes"] != want:
                self.s_abort(vid, "the staging volume has %d bytes after the extend, the source %d (S1)" % (res["bytes"], want))
                return False
            self.sset(vid, stage=dict(stage, bytes=res["bytes"]))
        self.sset(vid, size_bytes=want)
        self.snote(vid, "the volume was extended during the copy (%d GiB to %d GiB): the staging volume was extended to match" % (have // GIB, want // GIB))
        return False                     # the next tick records the new size in the rechecks

    def s_before_cutover(self, vid, acts, sp, vol, snaps, servers):
        j, gate = self.state.journal, self._gate()
        risky = ((self.state.checks or {}).get("risk") or {}).get(vid, {}).get("level") == "high"
        if (selection.options_of(self.state)["hold_before_cutover"] or risky) and vid not in self.state.released:
            return self.snote(vid, "ready: G2 and G3 are signed; waiting for you to release it for the cutover")
        if not self.in_window():
            return self.snote(vid, "ready: waiting for the run window (%s)" % self.window_text())
        if not self.copy_place(vid):
            return self.snote(vid, self.copy_wait_note())
        before = {"volume": vol, "snapshots": snaps, "servers": servers, "at": self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")}
        self.ev.save(vid, "before.json", before)
        try:
            L.enter(j, vid, "cutover", {"before_state": True, "target_type": sp["target_type"], "source_type": vol.get("volume_type"), "servers": sorted(servers),
                                        "locked_already": [s for s, x in servers.items() if (x or {}).get("locked")]}, gate=gate, now=self.clock(), rules=self.rules())
        except L.LifecycleError as exc:
            return self.snote(vid, "not cut over yet: " + str(exc))
        self.sset(vid, cut="lock")
        self.snote(vid, "cutover started")

    # ---- the cutover --------------------------------------------------------------------------------------------------------------
    def s_cutover(self, vid, acts, ctx, rev=False):
        step = ctx.get("cut") or CUT[0]
        fn = getattr(self, "cut_" + step)
        try:
            done = fn(vid, acts, ctx, rev)
        except CallFailed as exc:
            self.log("error", "cutover step %s refused: %s" % (step, exc), volume=vid)
            return self.cut_failed(vid, "step %s: %s" % (step, exc), rev)
        if done is True:
            nxt = CUT[CUT.index(step) + 1] if step != CUT[-1] else None
            if nxt:
                self.sset(vid, cut=nxt)
                self.snote(vid, "%s done; next: %s" % (step, nxt))

    def cut_failed(self, vid, reason, rev=False):
        if rev:
            return self.fail_hard(vid, "the reverse of %s failed: %s" % (vid, reason))
        self.failed.add(vid)
        L.enter(self.state.journal, vid, L.ROLLBACK, {"reason": reason, "r": "R2"}, now=self.clock())
        self.snote(vid, "cutover failed, rolling back (R2): " + reason, "error")

    def read_cut(self, vid, acts):
        vol = acts.volume(vid)
        return vol, {s: acts.server(s) for s in plan.attached_servers(vol)}

    def cut_lock(self, vid, acts, ctx, rev):
        vol, servers = self.read_cut(vid, acts)
        for sid, s in servers.items():
            if s.get("status") not in ("ACTIVE",):
                return self.cut_failed(vid, "the instance %s is %s" % (sid, s.get("status")), rev) or False
            if s.get("OS-EXT-STS:power_state") not in (None, 1):
                return self.cut_failed(vid, "the instance %s is %s but its guest is not running (power state %s): a volume swap needs a running guest" % (
                    sid, s.get("status"), s.get("OS-EXT-STS:power_state")), rev) or False
            host = s.get("OS-EXT-SRV-ATTR:host")
            if host:                                                        # found in the lab: the swap was accepted by the API and then waited for ever, the compute service was down
                down = acts.compute_down(host)
                if down:
                    return self.cut_failed(vid, "the compute service on %s is %s: it would never do the volume swap" % (host, down), rev) or False
            if not s.get("locked") and not acts.locked_by_us(vid):
                acts.lock(vid, sid)
        return True

    def job_step(self, vid, acts, key, verb, args, label, why):
        """Start a mover job once (journaled), then follow it. Returns ('running'|'done'|'failed', result)."""
        fab, ad = self.fabric(), self.fabric().adapter
        ctx = self.sctx(vid)
        j = ctx.get(key) or {}
        if j.get("state") == "done":
            return "done", j.get("result")
        if not j.get("id"):
            jid = "om-%s-%s-g%d-t%d" % (vid[:8], key, ctx.get("gen", 0), j.get("tries", 0))
            acts.op(vid, label, dict(why, job=jid), {"undo": "cancel the job; the source is only read"}, lambda: ad.job(jid, verb, dict(args, rate_mbps=self.copy_rate())))
            self.sset(vid, **{key: {"id": jid, "state": "running", "tries": j.get("tries", 0)}})
            return "running", None
        st = ad.job_status(j["id"])
        if st["state"] == "running":
            self.snote(vid, "%s: %d%%" % (label, int(100.0 * st.get("done", 0) / max(1, st.get("total", 1)))))
            return "running", None
        if st["state"] == "done":
            self.sset(vid, **{key: dict(j, state="done", result=st["result"])})
            return "done", st["result"]
        tries = j.get("tries", 0) + 1
        self.log("warn", "job %s %s: %s" % (j["id"], st["state"], st.get("error")), volume=vid, job=st)
        if tries >= MAX_TRIES:
            return "failed", st
        self.sset(vid, **{key: {"id": None, "tries": tries}})
        return "running", None

    def cut_rollback_clone(self, vid, acts, ctx, rev):
        fab, leg = self.fabric(), ctx["leg"]
        key, label = ("safety", "safety copy") if rev else ("rb", "rollback clone")
        vol = acts.volume(vid)
        src = fab.adapter.locate_volume(leg["src"], name_id_of(vol))
        if not isinstance(ctx.get(key), dict) or not ctx[key].get("name"):
            res = acts.op(vid, "clone_head", {"of": src["name"], "as": key}, {"undo": "remove the copy"}, lambda: fab.adapter.clone_head(leg["src"], src["name"], key, vid, src["bytes"]))
            self.sset(vid, **{key: {"name": res["name"], "path": res["path"]}})
            return False
        st, res = self.job_step(vid, acts, key + "_job", "copy", {"src": src["path"], "dst": ctx[key]["path"], "bytes": src["bytes"]}, label, {"of": src["name"]})
        if st == "failed":
            return self.cut_failed(vid, "the %s could not be made: %s" % (label, res.get("error")), rev) or False
        return st == "done"

    def cut_reconcile(self, vid, acts, ctx, rev):
        snaps = acts.snapshots(vid)
        chain_ids = {c["id"] for c in ctx["chain"]}
        new = [s["id"][:8] for s in snaps if s["id"] not in chain_ids]
        if new:
            if rev:
                self.sset(vid, chain=ctx["chain"] + [{"id": s["id"], "bytes": s["size"] * GIB, "state": "todo", "tries": 0} for s in snaps if s["id"] not in chain_ids])
                self.sset(vid, cut="stage_more")
                return False
            return self.cut_failed(vid, "a tenant snapshot appeared before the freeze: %s" % ", ".join(new), rev) or False
        vol = acts.volume(vid)
        now_b, was_b = int(vol.get("size") or 0) * GIB, int(ctx["size_bytes"])
        if now_b != was_b:
            return self.cut_failed(vid, "the volume was %s after the last recheck (%d GiB, staged as %d GiB). Nothing is frozen yet. Start the volume again" % (
                "extended" if now_b > was_b else "changed", now_b // GIB, was_b // GIB), rev) or False
        if ctx.get("pair_type") is not None and not rev and vol.get("volume_type") != ctx["pair_type"]:
            return self.cut_failed(vid, "the volume type was changed from %s to %s after staging began. Nothing is frozen yet. Start the volume again" % (
                ctx["pair_type"], vol.get("volume_type")), rev) or False
        return True

    def cut_freeze(self, vid, acts, ctx, rev):
        fab = self.fabric()
        vol = acts.volume(vid)
        if not ctx.get("frozen"):
            attached = bool(plan.attached_servers(vol))
            ns, ps = ("retyping", "in-use") if attached else ("maintenance", "available")
            res = acts.op(vid, "freeze", {"status": ns, "previous_status": ps, "migration_status": "migrating"},
                          {"undo": "unfreeze: restore status, previous_status and migration_status exactly"},
                          lambda: fab.helper("freeze", {"volume": vid, "status": ns, "previous_status": ps}))
            self.sset(vid, frozen=res.get("before") or {}, frozen_to=ns, frozen_prev=ps, frozen_at=self.clock().strftime("%Y-%m-%dT%H:%M:%SZ"))
            return False
        snaps = acts.snapshots(vid)
        if any(s["status"] != "available" for s in snaps):
            t = L._parse(ctx["frozen_at"])
            if (self.clock() - t).total_seconds() < 120:
                self.snote(vid, "frozen; waiting for a snapshot that was being made to settle")
                return False
            return self.cut_failed(vid, "a snapshot stayed in a state other than available after the freeze", rev) or False
        chain_ids = {c["id"] for c in ctx["chain"]}
        missing = [s["id"][:8] for s in snaps if s["id"] not in chain_ids]
        if missing:
            return self.cut_failed(vid, "a tenant snapshot raced the freeze (C7): %s" % ", ".join(missing), rev) or False
        name = "freeze-reverse.json" if rev else "freeze.json"
        if not self.ev.load(vid, name):
            # Until the freeze a tenant may extend, transfer or snapshot the volume. The post-check compares those fields with this capture.
            self.ev.save(vid, name, {"volume": vol, "snapshots": snaps, "at": self.clock().strftime("%Y-%m-%dT%H:%M:%SZ")})
        return True

    def cut_record(self, vid, acts, ctx, rev):
        fab, leg = self.fabric(), ctx["leg"]
        if not ctx.get("dest_id"):
            res = acts.op(vid, "record_create", {"source": vid, "host": leg["dest_host"], "type": leg["dtype"]}, {"undo": "delete the destination record (R2)"},
                          lambda: fab.helper("record-create", {"src": vid, "dest_host": leg["dest_host"], "dest_type": leg["dtype_id"],
                                                               "reference_volume": (fab.settings or {}).get("reference_volume")}))
            self.sset(vid, dest_id=res["new"])
            return False
        if not ctx.get("renamed"):
            stage = ctx["stage"]
            res = acts.op(vid, "stage_rename", {"from": stage["name"], "record": ctx["dest_id"]}, {"undo": "rename it back"},
                          lambda: fab.adapter.stage_rename(leg["dst"], stage["name"], ctx["dest_id"]))
            self.sset(vid, renamed=res["name"], dest_path=res["path"])
            return False
        return True

    def cut_verify_record(self, vid, acts, ctx, rev):
        fab, leg = self.fabric(), ctx["leg"]
        src, dst = fab.helper("show", {"volume": vid}), fab.helper("show", {"volume": ctx["dest_id"]})
        bad = [f for f in FIELDS_EQUAL if src["volume"].get(f) != dst["volume"].get(f)]
        problems = ["record fields differ: " + ", ".join(bad)] if bad else []
        if dst["volume"].get("status") != "available" or dst["volume"].get("migration_status") != "target:%s" % vid:
            problems.append("the destination record is %s / %s" % (dst["volume"].get("status"), dst["volume"].get("migration_status")))
        try:
            dev = fab.adapter.locate_volume(leg["dst"], ctx["dest_id"])
            if dev["bytes"] != ctx["size_bytes"]:
                problems.append("the array volume has %d bytes, not %d" % (dev["bytes"], ctx["size_bytes"]))
        except Exception as exc:
            problems.append("C2: " + str(exc))
        rb = ctx.get("safety" if rev else "rb") or {}
        if not rb.get("name") or not fab.adapter.exists(leg["src"], rb["name"]):
            problems.append("C3: the %s does not exist" % ("safety copy" if rev else "rollback clone"))
        vol, servers = self.read_cut(vid, acts)
        for sid, s in servers.items():
            if not s.get("locked"):
                problems.append("C4: the instance %s is not locked" % sid)
        self.ev.save(vid, "record-checks-%s.json" % ("reverse" if rev else "forward"), {"source": src, "destination": dst, "problems": problems})
        if problems:
            return self.cut_failed(vid, "; ".join(problems), rev) or False
        return True

    def cut_sync_fields(self, vid, acts, ctx, rev):
        fab = self.fabric()
        acts.op(vid, "sync_fields", {"from": vid, "to": ctx["dest_id"]}, {"undo": "none: the destination record is deleted on rollback"},
                lambda: fab.helper("sync-fields", {"src": vid, "new": ctx["dest_id"]}))
        return True

    def cut_switch(self, vid, acts, ctx, rev):
        fab, leg = self.fabric(), ctx["leg"]
        vol, servers = self.read_cut(vid, acts)
        if not ctx.get("swap_sent") and not ctx.get("completion_sent") and int(vol.get("size") or 0) * GIB != int(ctx["size_bytes"]):
            return self.cut_failed(vid, "the volume is %s GiB now and %d GiB on the target: it changed after the freeze (the freeze blocks this, so someone changed it by hand)" % (
                vol.get("size"), int(ctx["size_bytes"]) // GIB), rev) or False
        sids = plan.attached_servers(vol)
        if sids:
            if ctx.get("swap_sent"):
                return True
            for sid, s in servers.items():
                if s.get("status") != "ACTIVE" or s.get("OS-EXT-STS:task_state"):
                    return self.cut_failed(vid, "the instance %s is %s / %s" % (sid, s.get("status"), s.get("OS-EXT-STS:task_state")), rev) or False
            acts.swap_volume(vid, sids[0], ctx["dest_id"])
            self.sset(vid, swap_sent=True, switched_at=self.clock().strftime("%Y-%m-%dT%H:%M:%SZ"))
            return True
        if ctx.get("completion_sent"):
            return True
        src = fab.adapter.locate_volume(leg["src"], name_id_of(vol))
        st, res = self.job_step(vid, acts, "final", "copy", {"src": src["path"], "dst": ctx["dest_path"], "bytes": ctx["size_bytes"]}, "final copy", {"of": src["name"]})
        if st == "failed":
            return self.cut_failed(vid, "the final copy failed: %s" % res.get("error"), rev) or False
        if st != "done":
            return False
        st, res = self.job_step(vid, acts, "final_cmp", "compare", {"a": src["path"], "b": ctx["dest_path"], "bytes": ctx["size_bytes"]}, "compare", {"a": src["name"]})
        if st == "failed":
            return self.cut_failed(vid, "the final compare failed: %s" % res.get("error"), rev) or False
        if st != "done":
            return False
        if not res["identical"]:
            return self.cut_failed(vid, "the final image differs from the source at byte %s" % res["first_difference"], rev) or False
        acts.complete_migration(vid, ctx["dest_id"])
        self.sset(vid, completion_sent=True, switched_at=self.clock().strftime("%Y-%m-%dT%H:%M:%SZ"))
        return True

    def cut_wait(self, vid, acts, ctx, rev):
        leg = ctx["leg"]
        vol = acts.volume(vid)
        mig, status = vol.get("os-vol-mig-status-attr:migstat"), str(vol.get("status"))
        on_target = plan.backend_of(vol.get("os-vol-host-attr:host")) == leg["dst"] and vol.get("volume_type") == leg["dtype"]
        if mig == "success" and on_target and status in FINAL:
            return self.s_complete(vid, acts, ctx, vol, rev)
        if mig in ("error", "failed") or status.startswith("error"):
            self.ev.save(vid, "failed-switch.json", {"volume": vol})
            if rev or status.startswith("error"):
                return self.fail_hard(vid, "the switch of %s ended with migration_status=%s, status=%s" % (vid, mig, status)) or False
            return self.cut_failed(vid, "the switch ended with migration_status=%s: Cinder put the source back" % mig, rev) or False
        t = L._parse(ctx["switched_at"])
        if self.clock() - t > SWITCH_TIMEOUT:
            return self.fail_hard(vid, "the switch of %s has run for more than %d hours" % (vid, SWITCH_TIMEOUT.total_seconds() // 3600)) or False
        self.snote(vid, "switching: migration_status=%s, status=%s" % (mig, status))
        return False

    # ---- after the switch ---------------------------------------------------------------------------------------------------------------
    def s_complete(self, vid, acts, ctx, vol, rev):
        leg, fab = ctx["leg"], self.fabric()
        before = self.ev.load(vid, "before.json") or {}
        bvol = before.get("volume") or {}
        # Two baselines (playbook 5.10.5, v1.4). The fixed fields are compared with the capture taken before the cutover. A tenant may extend,
        # transfer or snapshot the volume until the freeze, so size, project, user and the snapshot list are compared with the capture taken
        # at the end of the freeze.
        frozen = self.ev.load(vid, "freeze-reverse.json" if rev else "freeze.json") or {}
        fvol = frozen.get("volume") or bvol
        servers = {s: acts.server(s) for s in plan.attached_servers(vol)}
        snaps = acts.snapshots(vid)
        key = lambda x: sorted((a.get("server_id"), a.get("device")) for a in (x.get("attachments") or []))
        checks = []

        def add(name, ok, detail):
            checks.append({"name": name, "ok": bool(ok), "detail": detail})
        add("X1 On the %s back-end with the %s type" % ("original" if rev else "target", "original" if rev else "target"), True,
            "%s, type %s, migration_status %s" % (plan.backend_of(vol.get("os-vol-host-attr:host")), vol.get("volume_type"), vol.get("os-vol-mig-status-attr:migstat")))
        add("Same status as before", vol.get("status") == bvol.get("status"), "%s now, %s before" % (vol.get("status"), bvol.get("status")))
        add("Same size", vol.get("size") == fvol.get("size"), "%s GiB now, %s at the freeze" % (vol.get("size"), fvol.get("size")))
        add("Same attachments", key(vol) == key(bvol), "%d now, %d before" % (len(key(vol)), len(key(bvol))))
        add("Same bootable flag", vol.get("bootable") == bvol.get("bootable"), "%s now, %s before" % (vol.get("bootable"), bvol.get("bootable")))
        from .runner import IDENTITY_FIELDS
        for label, field in IDENTITY_FIELDS:
            base, when = (fvol, "at the freeze") if field in FROZEN_FIELDS else (bvol, "before")
            add("Same %s" % label, vol.get(field) == base.get(field), "%s now, %s %s" % (vol.get(field), base.get(field), when))
        for sid, s in servers.items():
            add("Instance %s is running" % sid, (s or {}).get("status") == "ACTIVE", (s or {}).get("status") or "unreadable")
        try:
            on_array = set(fab.adapter.target_snapshots(leg["dst"], name_id_of(vol)))
            cinder = {s["id"] for s in snaps}
            lacking = sorted(cinder - on_array)
            extra = sorted(on_array - cinder)
            add("X3 The snapshot chain on the array matches Cinder", not lacking, "%d snapshot(s) on the array, %d in Cinder%s" % (
                len(on_array), len(cinder), "; a tenant deleted %d after the freeze (kept until the soak ends)" % len(extra) if extra else "") if not lacking
                else "Cinder has snapshot(s) the array lacks: %s" % ", ".join(x[:8] for x in lacking))
        except Exception as exc:
            add("X3 The snapshot chain on the array matches Cinder", False, "could not read the array: %s" % exc)
        was = {s["id"] for s in (frozen.get("snapshots") or before.get("snapshots") or [])}
        if not rev:
            now_ids = {s["id"] for s in snaps}
            gone = sorted(was - now_ids)
            made = sorted(now_ids - was)
            deleted_by_tenant = set(ctx.get("dropped", []))
            # A snapshot made after the switch is the tenant's and is not a failure: it is reported, not counted.
            add("X6 The snapshot list is as at the freeze", not (set(gone) - deleted_by_tenant), "%d at the freeze, %d now%s%s" % (
                len(was), len(snaps), "; tenant deletions: %d" % len(gone) if gone else "", "; made since the freeze: %d" % len(made) if made else ""))
        ok = all(c["ok"] for c in checks)
        self.ev.save(vid, "after-reverse.json" if rev else "after.json", {"volume": vol, "servers": servers, "snapshots": snaps, "checks": checks})
        if rev:
            self.sset(vid, reversed_ok=ok, post=checks)
            return True
        L.enter(self.state.journal, vid, "postcheck", {"ok": ok, "checks": checks}, now=self.clock(), rules=self.rules())
        self.sset(vid, completed=True, completed_at=self.clock().strftime("%Y-%m-%dT%H:%M:%SZ"))
        if ok:
            self.snote(vid, "moved; the post-check passed: waiting for a second person to sign G4")
            self.unlock_ours(vid, acts)
        else:
            safety.halt(self.state.journal, "OpenMove (automatic)", "the post-check of %s failed: %s" % (vid, "; ".join(c["name"] for c in checks if not c["ok"])))
            self.snote(vid, "the post-check failed: halted. The instance stays locked. Reverse the volume or look at it first.", "error")
        return True

    def s_postcheck(self, vid, acts, ctx):
        """X3: a snapshot a tenant deleted after the copy is destroyed on the target now, so the array and Cinder agree. Then the usual G4 wait."""
        fab, leg = self.fabric(), ctx["leg"]
        if ctx.get("dropped") and not ctx.get("dropped_removed") and ctx.get("completed"):
            for sid in ctx["dropped"]:
                n = fab.adapter.snapshot_name(sid)
                if fab.adapter.exists(leg["dst"], n):
                    acts.op(vid, "remove", {"remove": n, "on": leg["dst"], "why": "a tenant deleted this snapshot before the cutover"}, {"undo": "none: the tenant deleted it"},
                            lambda n=n: fab.adapter.remove(leg["dst"], n))
            self.sset(vid, dropped_removed=True)
        self.after_postcheck(vid, acts)

    # ---- the soak and the release of the source (X8) -------------------------------------------------------------------------------------------
    def s_soak(self, vid, acts, ctx):
        j = self.state.journal
        cur = L.current(j, vid)
        days = int(cur.get("soak_days") or 1)
        if self.clock() < L._parse(cur["at"]) + timedelta(days=days):
            return self.snote(vid, "soak: the source is kept for %d day(s)" % days)
        left = self.release_first_leg(vid, acts, ctx)
        if left:
            return self.snote(vid, "soak over; releasing the source: " + left)
        L.enter(j, vid, "done", {"result": "released after the soak"}, now=self.clock(), rules=self.rules())
        self.snote(vid, "done: the source was released")

    def release_first_leg(self, vid, acts, ctx):
        """X8: the old volume's snapshots and its backing, the temporary record, the rollback clone. Returns what is left, or ''."""
        fab, leg = self.fabric(), ctx["leg"]
        chain_ids = [c["id"] for c in ctx.get("chain", [])]
        if not ctx.get("released_snaps"):
            acts.op(vid, "release_source", {"backend": leg["src"], "snapshots": len(chain_ids)}, {"undo": "none: the move is signed off and the soak is over"},
                    lambda: fab.adapter.release_source(leg["src"], "volume-%s" % ctx["src_name_id"], chain_ids))
            self.sset(vid, released_snaps=True)
        rec = ctx.get("dest_id")
        if rec:
            try:
                acts.volume(rec)
                exists = True
            except Exception:
                exists = False
            if exists:
                if not ctx.get("force_sent"):
                    acts.force_delete(vid, rec)
                    self.sset(vid, force_sent=True)
                return "waiting for Cinder to delete the old record %s" % rec[:8]
        rb = (ctx.get("rb") or {}).get("name")
        if rb and fab.adapter.exists(leg["src"], rb):
            acts.op(vid, "remove", {"remove": rb}, {"undo": "none"}, lambda: fab.adapter.remove(leg["src"], rb))
            return "removing the rollback clone"
        old = "volume-%s" % ctx["src_name_id"]
        if ctx["src_name_id"] != name_id_of(acts.volume(vid)) and fab.adapter.exists(leg["src"], old) and not ctx.get("leftover"):
            # Cinder's driver could not delete it (it is not ours to remove): say so once, in the log and the journal, and go on
            self.sset(vid, leftover=old)
            self.log("warn", "the old backing volume %s is still on %s; the storage administrator may remove it" % (old, leg["src"]), volume=vid)
        return ""

    # ---- rollback ---------------------------------------------------------------------------------------------------------------------
    def s_rollback(self, vid, acts, ctx):
        if ctx.get("r3"):
            return self.s_reverse(vid, acts, ctx)
        return self.s_r2(vid, acts, ctx)

    def s_r2(self, vid, acts, ctx):
        """R2 (and R1): until the switch was accepted. Unfreeze, remove the destination record, remove what was made, unlock."""
        fab, leg = self.fabric(), ctx.get("leg") or {}
        vol = acts.volume(vid)
        mig, status = vol.get("os-vol-mig-status-attr:migstat"), str(vol.get("status"))
        if ctx.get("swap_sent") or ctx.get("completion_sent"):
            on_target = plan.backend_of(vol.get("os-vol-host-attr:host")) == leg.get("dst")
            if mig in ("migrating", "completing") or status == "retyping" and mig not in ("error", "failed"):
                return self.snote(vid, "rollback waits: the switch is still running in the cloud (migration_status=%s)" % mig)
            if mig == "success" and on_target:
                return self.fail_hard(vid, "the switch of %s finished while it was being rolled back: a person must decide (reverse or keep)" % vid)
        if ctx.get("frozen") and mig == "migrating" and not ctx.get("unfrozen"):
            fr = ctx["frozen"]
            acts.op(vid, "unfreeze", {"restore": fr}, {"undo": "freeze again"}, lambda: fab.helper("unfreeze", {"volume": vid, "status": fr.get("status") or ctx.get("frozen_prev"),
                    "previous_status": fr.get("previous_status"), "migration_status": fr.get("migration_status")}))
            self.sset(vid, unfrozen=True)
            return self.snote(vid, "rollback: the source record is unfrozen")
        if ctx.get("dest_id") and not ctx.get("record_deleted"):
            acts.op(vid, "record_delete", {"record": ctx["dest_id"]}, {"undo": "create the destination record again"}, lambda: fab.helper("record-delete", {"new": ctx["dest_id"]}))
            self.sset(vid, record_deleted=True)
            return self.snote(vid, "rollback: the destination record is removed")
        left = self.remove_made(vid, acts, ctx)
        if left:
            return self.snote(vid, "rollback: removing %s" % left)
        self.unlock_ours(vid, acts)
        if acts.locked_by_us(vid):
            return
        want = (ctx.get("frozen") or {}).get("status")                   # the status the source had before the freeze; "as it was" is checked, not assumed
        now_status = str(acts.volume(vid).get("status"))
        if want and now_status != want:
            return self.fail_hard(vid, "the rollback of %s is finished but the source is %s, not %s as before the freeze: a person must reset it" % (vid, now_status, want))
        L.enter(self.state.journal, vid, "done", {"result": "rolled back (R2): " + str(L.current(self.state.journal, vid).get("reason", ""))}, now=self.clock())
        self.snote(vid, "rolled back: the source is as it was; everything the run made was removed")

    # ---- reverse (R3) -----------------------------------------------------------------------------------------------------------------
    def s_reverse_request(self, vid, reason, by):
        ctx = self.sctx(vid)
        if not ctx.get("completed"):
            L.enter(self.state.journal, vid, L.ROLLBACK, {"reason": reason, "by": by, "r": "R2"}, now=self.clock())
            return
        first = {k: ctx.get(k) for k in ("leg", "src_name_id", "chain", "dest_id", "rb")}
        L.enter(self.state.journal, vid, L.ROLLBACK, {"reason": reason, "by": by, "r": "R3"}, now=self.clock())
        self.sset(vid, _leg_start=True, r3=True, first=first, by=by, reason=reason, gen=int(ctx.get("gen", 0)) + 1)

    def s_reverse(self, vid, acts, ctx):
        fab = self.fabric()
        first = ctx["first"]
        if not ctx.get("leg"):
            vol, snaps = acts.volume(vid), acts.snapshots(vid)
            types = acts.volume_types()
            stype = first["leg"]["stype"]
            dest_host = next((p["name"] for p in acts.pools() if plan.backend_of(p["name"]) == first["leg"]["src"]), None)
            dtype_id = next((t["id"] for t in types if t["name"] == stype), None)
            if not dest_host or not dtype_id:
                return self.fail_hard(vid, "the reverse cannot find the original pool or volume type (%s)" % stype)
            leg = {"kind": "reverse", "src": first["leg"]["dst"], "dst": first["leg"]["src"], "dtype": stype, "dtype_id": dtype_id, "dest_host": dest_host, "stype": first["leg"]["dtype"]}
            chain = [{"id": s["id"], "bytes": s["size"] * GIB, "state": "todo", "tries": 0} for s in sorted(snaps, key=lambda x: (x.get("created_at") or "", x["id"]))]
            self.sset(vid, leg=leg, size_bytes=vol["size"] * GIB, chain=chain, src_name_id=name_id_of(vol), dropped=[], cut="rev_stage")
            return self.snote(vid, "reverse (R3): rebuilding the volume and its %d snapshot(s) on %s" % (len(chain), leg["dst"]))
        step = ctx.get("cut") or "rev_stage"
        if step in ("rev_stage", "stage_more"):
            if not ctx.get("rev_cleared"):
                prior = dict(ctx, leg=first["leg"], chain=first["chain"], src_name_id=first["src_name_id"], dest_id=first["dest_id"], rb=first["rb"])
                left = self.release_first_leg(vid, acts, prior)
                if left:
                    return self.snote(vid, "reverse: clearing what the first move left on the original array: " + left)
                self.sset(vid, rev_cleared=True)
            if self.safe_copy(vid, acts) == "done" and not self.sctx(vid).get("aborting"):
                self.sset(vid, cut="lock")
                self.snote(vid, "reverse: the volume is rebuilt on the original array; switching")
            return
        done = self.s_cutover(vid, acts, ctx, rev=True)
        ctx = self.sctx(vid)
        if ctx.get("reversed_ok") is not None:
            self.finish_reverse(vid, acts, ctx)

    def finish_reverse(self, vid, acts, ctx):
        """After the reverse switch succeeded: release what the live (target) side held, unlock, done."""
        fab, leg = self.fabric(), ctx["leg"]
        if not ctx.get("reversed_ok"):
            return self.fail_hard(vid, "the post-check after the reverse of %s failed" % vid)
        chain_ids = [c["id"] for c in ctx["chain"]]
        if not ctx.get("rev_released"):
            acts.op(vid, "release_source", {"backend": leg["src"]}, {"undo": "none"}, lambda: fab.adapter.release_source(leg["src"], "volume-%s" % ctx["src_name_id"], chain_ids))
            self.sset(vid, rev_released=True)
        rec = ctx.get("dest_id")
        if rec:
            try:
                acts.volume(rec)
                if not ctx.get("rev_force_sent"):
                    acts.force_delete(vid, rec)
                    self.sset(vid, rev_force_sent=True)
                return self.snote(vid, "reverse: waiting for Cinder to delete the old record")
            except Exception:
                pass
        sf = (ctx.get("safety") or {}).get("name")
        if sf and fab.adapter.exists(leg["src"], sf):
            acts.op(vid, "remove", {"remove": sf, "on": leg["src"], "why": "the reverse is verified; the safety copy is no longer needed"}, {"undo": "none"},
                    lambda: fab.adapter.remove(leg["src"], sf))
            return self.snote(vid, "reverse: the safety copy is removed")
        self.unlock_ours(vid, acts)
        if acts.locked_by_us(vid):
            return
        L.enter(self.state.journal, vid, "done", {"result": "reversed: back on %s with type %s" % (leg["dst"], leg["dtype"])}, now=self.clock())
        self.snote(vid, "reversed: the volume is back on the original array; the instance is unlocked")
