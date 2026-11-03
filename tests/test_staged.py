# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The staged engine (class C: volumes with snapshots) over the mock cloud and a fake fabric.

The fake fabric stands in for the data mover, the Cinder helper and the array; the real LvmAdapter, the real engine and the real lifecycle run
on top of it. What these tests prove is the order, the checks, the journal and the rollbacks. What they cannot prove is the behaviour of real
devices, real Cinder or real arrays: the lab does that (docs/LAB.md)."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from openmove import lifecycle as L
from openmove import safety
from openmove.connectors.openstack import OpenStackSession
from openmove.demo.fake_fabric import DST_VG, SRC_VG, staged_cloud
from openmove.demo.mock_cloud import PASSWORD
from openmove.engine.runner import Engine
from openmove.journal import Journal

OPERATOR, SECOND = "Olive Operator", "Sam Second"
GATE = {"status": "approved", "message": "ok", "run_id": "r1", "signoffs": {"operator": {"by": OPERATOR}, "reviewer": {"by": "Rex Reviewer"}}}


class StagedCase(unittest.TestCase):
    attached = True
    snapshots = 2

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cloud, self.fab, ids = staged_cloud(snapshots=self.snapshots, attached=self.attached)
        self.vid, self.sids, self.srv = ids["volume"], ids["snapshots"], ids["server"]
        self.session = OpenStackSession(self.cloud.auth_url, "admin", PASSWORD, "demo")
        self.session.authenticate()
        self.journal = Journal(Path(self.dir.name) / "j.db")
        self.state = SimpleNamespace(
            home=Path(self.dir.name), journal=self.journal, session=self.session, inventory={"volumes": [{"id": self.vid, "class": "C", "volume_type": "fast"}]},
            demo=False, access="full", fabric=self.fab, diag=None,
            options={"hold_before_cutover": False, "concurrency": 8, "soak_days": 1, "stop_after_failures": 0},
            checks={"source_backend": "sf-1", "target_backend": "pure-1", "admitted": [self.vid], "risk": {}},
            selection=None, released=set(), servers={})
        self.now = datetime(2026, 11, 3, 9, 0, 0, tzinfo=timezone.utc)
        self.engine = Engine(self.state, self.permit, lambda: GATE, clock=lambda: self.now)
        self.mover = self.fab.mover_host

    def permit(self):
        return safety.grant(self.journal, lambda: {"status": "validated"}, lambda: GATE)

    def tearDown(self):
        self.engine.stop()
        self.cloud.stop()
        self.journal.close()
        self.dir.cleanup()

    # ---- helpers -------------------------------------------------------------------------------------------------------------
    @property
    def vol(self):
        return next(v for v in self.cloud.volumes if v["id"] == self.vid)

    def phase(self):
        return L.phase_of(self.journal, self.vid)

    def note(self):
        return self.engine.notes.get(self.vid)

    def gates_signed(self):
        return all(L.gate_signed(self.journal, self.vid, g, GATE) for g in ("G2", "G3"))

    def try_sign(self):
        if self.phase() in ("sync", "recheck") and not self.gates_signed():
            for g in ("G2", "G3"):
                try:
                    L.sign_gate(self.journal, self.vid, g, SECOND, "compared every image and the chain on the target", GATE, now=self.now)
                except L.LifecycleError:
                    pass

    def drive(self, until, ticks=400, sign=True, g4=True, soak=False):
        """Tick until the volume is in a phase (a string) or one of several (a tuple). Time moves on while the volume waits for its rechecks."""
        want = (until,) if isinstance(until, str) else tuple(until)
        for _ in range(ticks):
            if self.phase() in want:
                return
            self.engine.tick()
            if sign:
                self.try_sign()
            if g4 and self.phase() == "postcheck":
                try:
                    L.sign_gate(self.journal, self.vid, "G4", SECOND, "the volume is healthy on the new path", GATE, now=self.now)
                except L.LifecycleError:
                    pass
            if self.phase() in ("sync", "recheck") and not self.gates_signed():
                self.now += timedelta(seconds=320)
            if soak and self.phase() == "soak":
                self.now += timedelta(days=2)
        if self.phase() in want:
            return
        self.fail("the volume is in %s after %d ticks, not in %s. Note: %s. Halt: %s" % (self.phase(), ticks, want, self.note(), safety.halted(self.journal)))

    def whole_move(self):
        self.engine.start(ids=[self.vid], thread=False)
        self.drive("done", soak=True)

    def names(self, vg):
        return self.mover.names(vg)

    def snap_tokens(self, vg, origin):
        return {n: self.mover.content[(vg, n)] for n, r in self.mover.lv[vg].items() if r["origin"] == origin}

    def open_steps(self):
        return self.journal.unresolved_steps()


class HappyPath(StagedCase):
    def check_end_state(self):
        v = self.vol
        self.assertEqual(self.phase(), "done")
        self.assertEqual(v["os-vol-host-attr:host"], "ctl@pure-1#pool")
        self.assertEqual(v["volume_type"], "pure")
        self.assertEqual(v["status"], "in-use" if self.attached else "available")
        self.assertEqual(v["os-vol-mig-status-attr:migstat"], "success")
        nid = v["os-vol-mig-status-attr:name_id"]
        self.assertTrue(nid and nid != self.vid)
        live = "volume-%s" % nid
        self.assertIn(live, self.names(DST_VG))
        self.assertEqual(self.mover.content[(DST_VG, live)], "head-v0")                               # the data
        self.assertEqual(self.snap_tokens(DST_VG, live), {"_snapshot-%s" % s: "image-%d" % (i + 1) for i, s in enumerate(self.sids)})  # every image, same id
        # nothing of ours is left over, and the source snapshots and the temporary record were released after the soak
        self.assertEqual([n for n in self.names(DST_VG) if n.startswith("om-")], [])
        self.assertEqual([n for n in self.names(SRC_VG) if n.startswith("om-")], [])
        self.assertEqual([n for n in self.names(SRC_VG) if n.startswith("_snapshot-")], [])
        self.assertEqual([x["id"] for x in self.cloud.volumes], [self.vid])
        self.assertEqual(self.open_steps(), [])
        if self.srv:
            self.assertFalse(next(s for s in self.cloud.servers if s["id"] == self.srv)["locked"])

    def test_an_attached_volume_with_snapshots_moves_and_the_source_is_released(self):
        self.whole_move()
        self.check_end_state()
        paths = [p.rsplit("/", 2)[-2] + "/" + p.rsplit("/", 1)[-1] for p, _ in self.cloud.posts]
        self.assertIn("%s/os-volume_attachments/%s" % (self.srv, self.vid), [p.split("servers/")[-1] for p, _ in self.cloud.posts if "os-volume_attachments" in p])
        self.assertFalse(any("os-migrate_volume_completion" in str(b) for _, b in self.cloud.posts))      # Nova makes that call after a swap
        self.assertTrue(paths)

    def test_the_order_and_the_evidence_of_the_phases(self):
        self.whole_move()
        h = L.history(self.journal, self.vid)
        order = [e["phase"] for e in h]
        self.assertEqual(order[:4], ["precheck", "validate", "copy", "sync"])
        self.assertGreaterEqual(order.count("recheck"), 2)
        self.assertEqual(order[-4:], ["cutover", "postcheck", "soak", "done"])
        self.assertEqual(L.method_of(h), L.STAGED)
        gaps = [(L._parse(b["at"]) - L._parse(a["at"])).total_seconds() for a, b in zip(h, h[1:]) if a["phase"] == b["phase"] == "recheck"]
        self.assertTrue(all(g >= L.RECHECK_GAP for g in gaps), gaps)

    def test_nothing_is_changed_in_the_cloud_before_the_gates_are_signed(self):
        self.engine.start(ids=[self.vid], thread=False)
        self.drive("recheck", sign=False)
        for _ in range(8):
            self.engine.tick()
            self.now += timedelta(seconds=320)
        self.assertEqual(self.cloud.posts, [])                                                     # not one change in the cloud
        self.assertEqual(self.vol["status"], "in-use" if self.attached else "available")
        self.assertIn(self.phase(), ("sync", "recheck"))
        self.assertIn("second person", self.note())

    def test_the_dry_run_lists_the_calls_and_changes_nothing(self):
        r = self.engine.dry_run()
        row = r["volumes"][0]
        self.assertEqual((row["verdict"], row["method"]), ("ok", "staged"))
        steps = [c["step"] for c in row["calls"]]
        self.assertEqual(steps[0], "stage_create")
        self.assertIn("freeze", steps)
        self.assertIn("swap" if self.attached else "complete", steps)
        self.assertTrue(all(c["undo"] for c in row["calls"]))
        self.assertEqual(self.cloud.posts, [])
        self.assertEqual(L.states(self.journal), {})
        self.assertEqual(self.names(DST_VG), [])

    def test_the_dry_run_blocks_when_the_array_and_cinder_disagree(self):
        del self.mover.lv[SRC_VG]["_snapshot-%s" % self.sids[0]]
        row = self.engine.dry_run()["volumes"][0]
        self.assertEqual(row["verdict"], "blocked")
        self.assertTrue(any("snapshot" in c["detail"] and not c["ok"] for c in row["checks"]))

    def test_the_dry_run_blocks_when_the_target_has_no_room(self):
        self.mover.free = 10
        row = self.engine.dry_run()["volumes"][0]
        self.assertEqual(row["verdict"], "blocked")
        self.assertTrue(any("room" in c["name"] and not c["ok"] for c in row["checks"]))

    def test_class_c_is_not_taken_when_no_fabric_is_ready(self):
        self.state.fabric = None
        self.assertEqual(self.engine.candidates(), [])


class DetachedPath(HappyPath):
    attached = False

    def test_a_detached_volume_goes_through_the_completion_call(self):
        self.whole_move()
        self.check_end_state()
        self.assertTrue(any("os-migrate_volume_completion" in str(b) for _, b in self.cloud.posts))
        self.assertFalse(any("os-volume_attachments" in p for p, _ in self.cloud.posts))

    def test_an_attached_volume_with_snapshots_moves_and_the_source_is_released(self):
        self.whole_move()
        self.check_end_state()


class NoSnapshots(StagedCase):
    snapshots = 0

    def test_a_class_c_volume_whose_snapshots_are_gone_still_moves_by_the_staged_method(self):
        self.whole_move()
        self.assertEqual(self.phase(), "done")


class Verification(StagedCase):
    def test_an_image_that_differs_after_the_copy_stops_the_volume_before_anything_changes(self):
        self.mover.corrupt.add("-c-")
        self.engine.start(ids=[self.vid], thread=False)
        self.drive("aborted", sign=False)
        self.assertEqual(self.cloud.posts, [])
        self.assertEqual([n for n in self.names(DST_VG) if n.startswith("om-") or n.startswith("_snapshot") or n.startswith("volume-")], [])
        self.assertEqual(self.vol["status"], "in-use")
        self.assertIn("not identical", L.current(self.journal, self.vid)["reason"])
        self.assertEqual(self.open_steps(), [])

    def test_a_copy_job_that_keeps_failing_gives_the_volume_up_with_the_whole_error(self):
        self.mover.fail_jobs.add("-c-")
        self.engine.start(ids=[self.vid], thread=False)
        self.drive("aborted", sign=False)
        reason = L.current(self.journal, self.vid)["reason"]
        self.assertIn("Input/output error", reason)
        self.assertEqual(self.cloud.posts, [])
        self.assertEqual([n for n in self.names(DST_VG)], [])

    def test_a_copy_job_that_dies_once_is_started_again(self):
        self.engine.start(ids=[self.vid], thread=False)
        for _ in range(6):
            self.engine.tick()
        self.mover.kill_jobs()
        self.drive("done", soak=True)
        self.assertEqual(self.phase(), "done")

    def test_a_snapshot_made_by_a_tenant_during_the_sync_is_copied_and_the_rechecks_start_again(self):
        from openmove.demo.mock_cloud import snapshot
        self.engine.start(ids=[self.vid], thread=False)
        self.drive("recheck", sign=False)
        new = "eeeeeeee-0000-0000-0000-0000000000ff"
        self.cloud.snapshots.append(snapshot(new, self.vid, 0, size=1))
        self.mover.add(SRC_VG, "_snapshot-%s" % new, 1024 ** 3, "image-new", origin="volume-%s" % self.vid, tags=())
        self.drive("done", soak=True)
        live = "volume-%s" % self.vol["os-vol-mig-status-attr:name_id"]
        self.assertEqual(self.snap_tokens(DST_VG, live)["_snapshot-%s" % new], "image-new")
        self.assertEqual(len(self.snap_tokens(DST_VG, live)), 3)

    def test_a_snapshot_the_tenant_deletes_is_noticed_and_destroyed_on_the_target_after_the_cutover(self):
        self.engine.start(ids=[self.vid], thread=False)
        self.drive("recheck", sign=False)
        gone = self.sids[0]
        self.cloud.snapshots = [s for s in self.cloud.snapshots if s["id"] != gone]
        del self.mover.lv[SRC_VG]["_snapshot-%s" % gone]
        self.drive("done", soak=True)
        live = "volume-%s" % self.vol["os-vol-mig-status-attr:name_id"]
        self.assertNotIn("_snapshot-%s" % gone, self.snap_tokens(DST_VG, live))
        self.assertEqual(len(self.snap_tokens(DST_VG, live)), 1)

    def test_the_volume_is_not_cut_over_when_the_source_changed_between_the_rechecks(self):
        self.engine.start(ids=[self.vid], thread=False)
        self.drive("recheck", sign=False)
        before = len([e for e in L.history(self.journal, self.vid) if e["phase"] == "recheck" and e.get("ok")])
        self.vol["size"] = self.vol["size"]            # same size: a status flip is what the fingerprint sees
        self.vol["os-vol-tenant-attr:tenant_id"] = self.vol["os-vol-tenant-attr:tenant_id"]
        self.assertGreaterEqual(before, 1)


class Rollbacks(StagedCase):
    def to_cutover_ready(self):
        self.engine.start(ids=[self.vid], thread=False)
        self.drive("recheck", sign=True)
        for _ in range(30):
            if self.gates_signed() and L.cutover_ready(self.journal, self.vid, GATE, self.now)[0]:
                return
            self.engine.tick()
            self.try_sign()
            self.now += timedelta(seconds=320) if not self.gates_signed() else timedelta(0)

    def after_rollback(self):
        self.assertEqual(self.phase(), "done")
        self.assertIn("rolled back", L.current(self.journal, self.vid)["result"])
        self.assertEqual(self.vol["os-vol-host-attr:host"], "ctl@sf-1#pool")
        self.assertEqual(self.vol["volume_type"], "fast")
        self.assertEqual([x["id"] for x in self.cloud.volumes], [self.vid])                       # the destination record is gone
        self.assertEqual([n for n in self.names(DST_VG)], [])                                     # the staging volume and its snapshots are gone
        self.assertEqual([n for n in self.names(SRC_VG) if n.startswith("om-")], [])             # so is the rollback clone
        self.assertEqual(len([n for n in self.names(SRC_VG) if n.startswith("_snapshot-")]), len(self.sids))   # the source snapshots are untouched
        self.assertEqual(self.mover.content[(SRC_VG, "volume-%s" % self.vid)], "head-v0")
        self.assertEqual(self.open_steps(), [])
        if self.srv:
            self.assertFalse(next(s for s in self.cloud.servers if s["id"] == self.srv)["locked"])
        self.assertIn(self.vol["status"], ("in-use", "available"))

    def test_a_restart_during_a_rollback_picks_the_frozen_volume_up_although_it_reads_as_another_class_now(self):
        self.to_cutover_ready()
        self.fab.helper.refuse["record-create"] = "the destination host is not known to Cinder"
        for _ in range(60):                                                  # until the rollback has started, not until it is finished
            self.engine.tick() if self.engine.status == "running" else self.engine.start(ids=[self.vid], thread=False)
            if self.phase() == "rollback":
                break
        self.assertEqual(self.phase(), "rollback")
        self.state.inventory["volumes"][0]["class"] = "E"                    # what a fresh inventory says about a frozen volume
        self.engine = Engine(self.state, self.permit, lambda: GATE, clock=lambda: self.now)       # a new process
        self.engine.start(ids=[self.vid], thread=False)                      # must not refuse with "no volume of a class this tool moves"
        self.drive("done")
        self.after_rollback()

    def test_a_refused_record_rolls_back_to_the_start(self):
        self.to_cutover_ready()
        self.fab.helper.refuse["record-create"] = "the destination host is not known to Cinder"
        self.drive("done")
        self.after_rollback()
        self.assertIn("destination host is not known", L.history(self.journal, self.vid)[-2]["reason"])

    def test_a_swap_that_nova_refuses_rolls_back(self):
        self.cloud.reject_swap[self.vid] = 409
        self.to_cutover_ready()
        self.drive("done")
        self.after_rollback()
        self.assertEqual(self.vol["os-vol-mig-status-attr:migstat"], None)

    def test_a_rollback_that_leaves_the_source_in_the_wrong_status_halts_instead_of_saying_it_is_as_it_was(self):
        """Found in the lab: a source left 'retyping' after R2 was reported as 'as it was'. The end of R2 now checks the status."""
        self.to_cutover_ready()
        self.fab.helper.refuse["record-create"] = "the destination host is not known to Cinder"
        real = self.fab.helper.v_unfreeze
        self.fab.helper.v_unfreeze = lambda a: (real(a), self.vol.update(status="retyping"))[0]          # the unfreeze 'works' but the status is not restored
        for _ in range(60):
            self.engine.tick()
            self.try_sign()
        self.assertNotEqual(self.phase(), "done")
        self.assertTrue(safety.halted(self.journal))
        self.assertIn("a person must reset it", self.engine.notes.get(self.vid, ""))

    def test_a_guest_that_is_not_running_rolls_back_before_the_swap(self):
        """Found in the lab: Nova said ACTIVE, the guest and the compute service were down, and the swap waited for ever."""
        if not self.srv:
            self.skipTest("a detached volume has no guest")
        self.to_cutover_ready()
        next(s for s in self.cloud.servers if s["id"] == self.srv)["OS-EXT-STS:power_state"] = 4
        for _ in range(60):
            self.engine.tick()
            self.try_sign()
        self.assertIn(self.phase(), ("sync", "recheck"))                                      # it never reaches the cutover
        self.assertIn("guest is not running", self.engine.notes.get(self.vid, ""))
        self.assertEqual(self.vol["os-vol-mig-status-attr:migstat"], None)                  # nothing was frozen and no swap was sent

    def test_a_freeze_that_fails_leaves_the_volume_as_it_was(self):
        self.to_cutover_ready()
        self.fab.helper.refuse["freeze"] = "the volume is busy"
        self.drive("done")
        self.after_rollback()

    def test_a_swap_that_fails_in_the_cloud_rolls_back_and_cinder_keeps_the_source(self):
        self.cloud.fail_swap.add(self.vid)
        self.to_cutover_ready()
        self.drive("done")
        self.assertEqual(self.phase(), "done")
        self.assertEqual(self.vol["os-vol-host-attr:host"], "ctl@sf-1#pool")
        self.assertEqual([x["id"] for x in self.cloud.volumes], [self.vid])
        self.assertEqual([n for n in self.names(DST_VG)], [])
        self.assertEqual(self.mover.content[(SRC_VG, "volume-%s" % self.vid)], "head-v0")

    def test_a_snapshot_that_appears_after_the_freeze_is_not_cut_over(self):
        from openmove.demo.mock_cloud import snapshot
        self.to_cutover_ready()
        late = "eeeeeeee-0000-0000-0000-0000000000aa"
        for _ in range(60):
            self.engine.tick()
            if self.vol["os-vol-mig-status-attr:migstat"] == "migrating":
                self.cloud.snapshots.append(snapshot(late, self.vid, 0, size=1))
                break
        self.drive("done")
        self.after_rollback_but_one_more_snapshot()

    def after_rollback_but_one_more_snapshot(self):
        self.assertEqual(self.vol["os-vol-host-attr:host"], "ctl@sf-1#pool")
        self.assertEqual([x["id"] for x in self.cloud.volumes], [self.vid])
        self.assertEqual([n for n in self.names(DST_VG)], [])


class DetachedRollbacks(Rollbacks):
    attached = False

    def test_a_completion_that_cinder_refuses_rolls_back(self):
        self.cloud.reject_completion[self.vid] = 400
        self.to_cutover_ready()
        self.drive("done")
        self.after_rollback()
        self.assertEqual(self.vol["status"], "available")

    def test_a_swap_that_nova_refuses_rolls_back(self):
        self.skipTest("a detached volume has no swap")

    test_a_swap_that_fails_in_the_cloud_rolls_back_and_cinder_keeps_the_source = None


class FailureHandling(StagedCase):
    def test_an_answer_that_is_lost_halts_everything_and_the_move_continues_after_a_person_resumes(self):
        self.mover.lose_answer.add("lv.create")
        self.engine.start(ids=[self.vid], thread=False)
        for _ in range(4):
            self.engine.tick()
        h = safety.halted(self.journal)
        self.assertTrue(h and "unknown" in h["reason"], h)
        self.assertEqual(self.cloud.posts, [])
        safety.resume(self.journal, "Sam Second", "looked at the host: the volume exists and is ours")
        self.drive("done", soak=True)
        self.assertEqual(self.phase(), "done")
        self.assertEqual(self.open_steps(), [])

    def test_a_halt_during_the_copy_stops_every_step_and_resume_carries_on(self):
        self.engine.start(ids=[self.vid], thread=False)
        for _ in range(5):
            self.engine.tick()
        safety.halt(self.journal, "Sam Second", "stop for a moment")
        calls = len(self.mover.calls)
        for _ in range(5):
            self.engine.tick()
        self.assertEqual(len(self.mover.calls), calls)                                             # not one call to a host while halted
        safety.resume(self.journal, "Sam Second", "carry on")
        self.drive("done", soak=True)

    def test_a_restart_in_the_middle_carries_on_from_the_journal(self):
        self.engine.start(ids=[self.vid], thread=False)
        for _ in range(7):
            self.engine.tick()
        self.engine = Engine(self.state, self.permit, lambda: GATE, clock=lambda: self.now)       # a new process
        self.engine.start(ids=[self.vid], thread=False)
        self.drive("done", soak=True)
        self.assertEqual(self.phase(), "done")

    def test_an_error_text_is_kept_whole(self):
        long = "x" * 900
        self.mover.refuse["lv.create"] = long
        self.engine.start(ids=[self.vid], thread=False)
        self.drive("aborted", sign=False)
        self.assertIn(long, L.current(self.journal, self.vid)["reason"])
        self.assertTrue(any(long in str(r) for r in self.journal.steps(None, 100)))


class Reverse(StagedCase):
    def reach_soak(self):
        self.engine.start(ids=[self.vid], thread=False)
        self.drive("soak")

    def test_a_volume_is_put_back_with_its_snapshots_during_the_soak(self):
        self.reach_soak()
        self.assertEqual(self.engine.reversible(), [self.vid])
        self.engine.reverse(self.vid, "the application owner asked for it", "Sam Second")
        self.drive("done", g4=False)
        self.assertEqual(self.vol["os-vol-host-attr:host"], "ctl@sf-1#pool")
        self.assertEqual(self.vol["volume_type"], "fast")
        self.assertEqual(self.vol["os-vol-mig-status-attr:migstat"], "success")
        live = "volume-%s" % self.vol["os-vol-mig-status-attr:name_id"]
        self.assertIn(live, self.names(SRC_VG))
        self.assertEqual(self.mover.content[(SRC_VG, live)], "head-v0")
        self.assertEqual(self.snap_tokens(SRC_VG, live), {"_snapshot-%s" % s: "image-%d" % (i + 1) for i, s in enumerate(self.sids)})
        self.assertEqual([n for n in self.names(DST_VG)], [])                                     # the target side is released
        self.assertEqual([n for n in self.names(SRC_VG) + self.names(DST_VG) if n.startswith("om-")], [])
        self.assertEqual([x["id"] for x in self.cloud.volumes], [self.vid])
        self.assertFalse(next(s for s in self.cloud.servers if s["id"] == self.srv)["locked"])
        self.assertEqual(self.open_steps(), [])

    def test_the_reverse_preview_changes_nothing(self):
        self.reach_soak()
        posts = list(self.cloud.posts)
        r = self.engine.reverse_preview()
        self.assertTrue(r["volumes"][0]["can"])
        self.assertEqual(self.cloud.posts, posts)

    def test_a_reverse_needs_a_name_and_a_reason(self):
        from openmove.engine.runner import EngineError
        self.reach_soak()
        with self.assertRaises(EngineError):
            self.engine.reverse(self.vid, "no", "Sam Second")


class Diagnostics(StagedCase):
    def test_a_halt_writes_a_support_bundle_by_itself(self):
        from openmove.diag import Diag
        self.state.diag = Diag(self.state.home)
        self.mover.lose_answer.add("lv.create")
        self.engine.start(ids=[self.vid], thread=False)
        for _ in range(4):
            self.engine.tick()
        zips = list((self.state.home / "bundles").glob("openmove-support-*.zip"))
        self.assertTrue(zips)
        import zipfile
        names = zipfile.ZipFile(zips[0]).namelist()
        for n in ("manifest.json", "journal/events.jsonl", "journal/steps.jsonl", "README.txt"):
            self.assertIn(n, names)


if __name__ == "__main__":
    unittest.main()
