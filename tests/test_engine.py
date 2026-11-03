# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The retype engine against a mock cloud that answers like Cinder and Nova (requirement R14: order, refusals, evidence).

The mock proves the logic and the safety rules. It does not prove that a real Queens cloud behaves the same way: that is the lab (tests T1 to T21)."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from openmove import lifecycle as L
from openmove import safety
from openmove.connectors.openstack import OpenStackSession
from openmove.demo.mock_cloud import PASSWORD, MockCloud, snapshot, volume
from openmove.engine import plan
from openmove.engine.runner import Engine, EngineError
from openmove.journal import Journal

OPERATOR, SECOND = "Olive Operator", "Sam Second"
GATE = {"status": "approved", "message": "ok", "run_id": "r1", "signoffs": {"operator": {"by": OPERATOR}, "reviewer": {"by": "Rex Reviewer"}}}
V1, V2, V3, V4 = ("aaaaaaaa-0000-0000-0000-00000000000%d" % i for i in range(1, 5))
SRV = "srv-active"


def build_cloud():
    c = MockCloud()
    c.types = [{"id": "t1", "name": "fast", "extra_specs": {"volume_backend_name": "sf-1"}},
               {"id": "t2", "name": "pure", "extra_specs": {"volume_backend_name": "pure-1"}}]
    att = [{"server_id": SRV, "device": "/dev/vdb", "host_name": "cmp1"}]
    c.volumes = [volume(V1, size=20), volume(V2, status="in-use", size=50, attachments=att), volume(V3, size=10),
                 volume(V4, size=5)]
    c.snapshots = [snapshot("bbbbbbbb-0000-0000-0000-000000000001", V3, 20, size=10)]
    c.servers = [{"id": SRV, "status": "ACTIVE", "OS-EXT-STS:task_state": None, "locked": False, "image": {"id": "img"}}]
    c.start()
    return c


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cloud = build_cloud()
        self.session = OpenStackSession(self.cloud.auth_url, "admin", PASSWORD, "demo")
        self.session.authenticate()
        j = Journal(Path(self.dir.name) / "j.db")
        vols = [{"id": V1, "class": "A", "volume_type": "fast"}, {"id": V2, "class": "A", "volume_type": "fast"},
                {"id": V3, "class": "B", "volume_type": "fast"}, {"id": V4, "class": "C", "volume_type": "fast"}]
        self.state = SimpleNamespace(
            home=Path(self.dir.name), journal=j, session=self.session, inventory={"volumes": vols}, demo=False, access="api", options={
                "hold_before_cutover": False, "concurrency": 8, "soak_days": 1, "stop_after_failures": 0},
            checks={"source_backend": "sf-1", "target_backend": "pure-1", "admitted": [V1, V2, V3, V4], "risk": {}},
            selection=None, released=set(), servers={})
        self.now = datetime(2026, 11, 2, 9, 0, 0, tzinfo=timezone.utc)
        self.engine = Engine(self.state, self.permit, lambda: GATE, clock=lambda: self.now)

    def permit(self):
        return safety.grant(self.state.journal, lambda: {"status": "validated"}, lambda: GATE)

    def tearDown(self):
        self.engine.stop()
        self.cloud.stop()
        self.state.journal.close()
        self.dir.cleanup()

    def vol(self, vid):
        return next(v for v in self.cloud.volumes if v["id"] == vid)

    def phase(self, vid):
        return L.phase_of(self.state.journal, vid)

    def sign(self, vid, *gates):
        for g in gates:
            L.sign_gate(self.state.journal, vid, g, SECOND, "checked the before-state and the target", GATE, now=self.now)

    def run_until(self, vid, phase, ticks=12):
        for _ in range(ticks):
            if self.phase(vid) == phase:
                return
            self.engine.tick()
        if self.phase(vid) == phase:
            return
        self.fail("%s is in %s after %d ticks, not %s. Note: %s" % (vid, self.phase(vid), ticks, phase, self.engine.notes.get(vid)))

    def start(self, ids):
        self.engine.start(ids=ids, thread=False)

    def posts(self, vid):
        return [(p.split("/")[-2] + "/" + p.split("/")[-1], b) for p, b in self.cloud.posts if vid in p or SRV in p]


class DryRunTests(EngineCase):
    def test_a_dry_run_changes_nothing_and_lists_the_calls(self):
        r = self.engine.dry_run()
        self.assertEqual(self.cloud.posts, [])
        self.assertEqual(L.states(self.state.journal), {})
        by = {x["id"]: x for x in r["volumes"]}
        self.assertEqual(set(by), {V1, V2, V3})                      # class C is not a candidate for the retype engine
        self.assertEqual(by[V1]["verdict"], "ok")
        self.assertEqual([c["step"] for c in by[V2]["calls"]], ["lock", "retype", "unlock"])
        self.assertEqual([c["step"] for c in by[V1]["calls"]], ["retype"])
        self.assertEqual(by[V3]["verdict"], "wait")                  # class B: snapshots still there, not a failure
        self.assertEqual(r["counts"], {"ok": 2, "wait": 1, "blocked": 0})
        self.assertEqual(self.state.journal.latest("engine_dry_run")["data"]["volumes"], 3)

    def test_a_dry_run_blocks_what_cannot_be_moved(self):
        self.vol(V1)["encrypted"] = True
        self.vol(V2)["status"] = "error"
        by = {x["id"]: x for x in self.engine.dry_run()["volumes"]}
        self.assertEqual(by[V1]["verdict"], "blocked")
        self.assertEqual(by[V2]["verdict"], "blocked")
        self.assertEqual(by[V1]["calls"], [])

    def test_a_volume_type_that_cannot_be_chosen_blocks(self):
        self.cloud.types.append({"id": "t3", "name": "pure2", "extra_specs": {"volume_backend_name": "pure-1"}})
        by = {x["id"]: x for x in self.engine.dry_run()["volumes"]}
        self.assertEqual(by[V1]["verdict"], "blocked")
        self.assertTrue(any("several volume types" in c["detail"] for c in by[V1]["checks"]))
        by = {x["id"]: x for x in self.engine.dry_run(type_map={"fast": "pure"})["volumes"]}
        self.assertEqual(by[V1]["verdict"], "ok")

    def test_a_dry_run_is_remembered_for_the_volumes_it_covered_and_the_pre_flight_run(self):
        self.state.checks["run_id"] = "r1"
        self.assertFalse(self.engine.dry_covers([V1]))
        self.engine.dry_run([V1], None)
        self.assertTrue(self.engine.dry_covers([V1]))
        self.assertFalse(self.engine.dry_covers([V1, V2]))               # V2 was not read
        self.state.checks["run_id"] = "r2"                               # a new pre-flight run: the dry run is out of date
        self.assertFalse(self.engine.dry_covers([V1]))

    def test_a_dry_run_needs_a_pre_flight(self):
        self.state.checks = None
        with self.assertRaises(EngineError):
            self.engine.dry_run()


class RunTests(EngineCase):
    def test_a_detached_volume_goes_all_the_way(self):
        self.start([V1])
        self.run_until(V1, "validate")
        self.assertEqual(self.cloud.posts, [])                       # validated, but nothing is sent before G2 and G3
        self.engine.tick()
        self.assertEqual(self.cloud.posts, [])
        self.sign(V1, "G2", "G3")
        self.run_until(V1, "postcheck")
        v = self.vol(V1)
        self.assertEqual((v["volume_type"], v["os-vol-host-attr:host"]), ("pure", "ctl@pure-1#pool"))
        self.assertEqual(self.posts(V1), [("%s/action" % V1, {"os-retype": {"new_type": "pure", "migration_policy": "on-demand"}})])
        self.assertTrue(L.current(self.state.journal, V1)["ok"])
        self.sign(V1, "G4")
        self.engine.tick()
        self.assertEqual(self.phase(V1), "soak")
        self.now += timedelta(days=2)
        self.engine.tick()
        self.assertEqual(self.phase(V1), "done")
        self.assertEqual(self.engine.status, "finished")
        folder = Path(self.dir.name) / "evidence" / V1
        for name in ("validated.json", "before.json", "after.json", "calls.jsonl"):
            self.assertTrue((folder / name).is_file(), name)

    def test_an_attached_volume_is_locked_around_the_move(self):
        self.start([V2])
        self.run_until(V2, "validate")
        self.sign(V2, "G2", "G3")
        self.run_until(V2, "cutover", 1)
        self.assertTrue(self.cloud.servers[0]["locked"])             # locked as the cutover begins
        self.run_until(V2, "postcheck")
        self.assertFalse(self.cloud.servers[0]["locked"])            # and released once the post-check passed
        kinds = [p.split("/")[-1] + ":" + ",".join(b) for p, b in self.cloud.posts]
        self.assertEqual([k.split(":")[0] for k in kinds], ["action"] * 3)
        self.assertEqual([list(b)[0] for _, b in self.cloud.posts], ["lock", "os-retype", "unlock"])
        steps = {r["name"]: r for r in self.state.journal.steps("retype:" + V2)}
        self.assertTrue(all(steps[n]["status"] == "done" for n in ("lock", "retype", "unlock")))
        self.assertIn("retype", steps["retype"]["inverse"] and steps["retype"]["inverse"])        # an inverse was recorded

    def test_nothing_is_sent_without_g2_and_g3_or_with_the_operator_signing(self):
        self.start([V1])
        self.run_until(V1, "validate")
        with self.assertRaises(L.LifecycleError):
            L.sign_gate(self.state.journal, V1, "G2", OPERATOR, "checked the before-state", GATE)
        for _ in range(5):
            self.engine.tick()
        self.assertEqual(self.cloud.posts, [])
        self.assertIn("G2 and G3", self.engine.notes[V1])

    def test_a_held_volume_waits_for_the_release(self):
        self.state.options["hold_before_cutover"] = True
        self.start([V1])
        self.run_until(V1, "validate")
        self.sign(V1, "G2", "G3")
        for _ in range(3):
            self.engine.tick()
        self.assertEqual(self.cloud.posts, [])
        self.assertIn("release", self.engine.notes[V1])
        self.state.released.add(V1)
        self.run_until(V1, "postcheck")

    def test_a_high_risk_volume_always_waits_for_the_release(self):
        self.state.checks["risk"] = {V1: {"level": "high"}}
        self.start([V1])
        self.run_until(V1, "validate")
        self.sign(V1, "G2", "G3")
        for _ in range(3):
            self.engine.tick()
        self.assertEqual(self.cloud.posts, [])

    def test_a_halt_stops_the_next_change(self):
        self.start([V1])
        self.run_until(V1, "validate")
        self.sign(V1, "G2", "G3")
        safety.halt(self.state.journal, OPERATOR, "stop everything for the test")
        for _ in range(3):
            self.engine.tick()
        self.assertEqual(self.cloud.posts, [])
        self.assertIn("halted", self.engine.notes[V1])
        safety.resume(self.state.journal, OPERATOR, "carry on with the test")
        self.run_until(V1, "postcheck")

    def test_a_volume_that_changed_after_validation_is_not_moved(self):
        self.start([V1])
        self.run_until(V1, "validate")
        self.sign(V1, "G2", "G3")
        self.cloud.snapshots.append(snapshot("bbbbbbbb-0000-0000-0000-000000000009", V1, 1))   # a snapshot appears
        self.engine.tick()
        self.assertEqual(self.phase(V1), "aborted")
        self.assertEqual(self.cloud.posts, [])

    def test_a_class_b_volume_waits_for_its_snapshots_to_rotate_away(self):
        self.start([V3])
        for _ in range(3):
            self.engine.tick()
        self.assertEqual(self.phase(V3), "precheck")
        self.assertIn("rotate", self.engine.notes[V3])
        self.cloud.snapshots.clear()                                  # the rotation removed it
        self.run_until(V3, "validate")

    def test_a_class_b_volume_is_given_up_when_the_rotation_never_comes(self):
        self.start([V3])
        self.engine.tick()
        self.now += timedelta(hours=73)
        self.engine.tick()
        self.assertEqual(self.phase(V3), "aborted")
        self.assertIn("rotate", L.current(self.state.journal, V3)["reason"])

    def test_class_c_is_never_started_by_this_engine(self):
        self.start(None)
        for _ in range(3):
            self.engine.tick()
        self.assertIsNone(self.phase(V4))

    def test_a_run_needs_a_permit(self):
        safety.halt(self.state.journal, OPERATOR, "stop everything for the test")
        with self.assertRaises(safety.Refused):
            self.engine.start(ids=[V1], thread=False)
        safety.resume(self.state.journal, OPERATOR, "carry on with the test")
        self.state.checks = dict(self.state.checks)
        self.engine._gate = lambda: {"status": "stale", "message": "old"}
        self.engine._permit = lambda: safety.grant(self.state.journal, lambda: {"status": "validated"}, lambda: {"status": "stale", "message": "old"})
        with self.assertRaises(safety.Refused):
            self.engine.start(ids=[V1], thread=False)

    def test_an_unvalidated_release_cannot_be_changed(self):
        self.engine._permit = lambda: safety.grant(self.state.journal, lambda: {"status": "untested", "reason": "no lab run"}, lambda: GATE)
        with self.assertRaises(Exception):
            self.engine.start(ids=[V1], thread=False)
        self.assertEqual(self.cloud.posts, [])


class FailureTests(EngineCase):
    def go(self, vid):
        self.start([vid])
        self.run_until(vid, "validate")
        self.sign(vid, "G2", "G3")

    def test_a_refused_retype_leaves_everything_as_it_was(self):
        self.cloud.reject_retype[V2] = 400
        self.go(V2)
        self.run_until(V2, "done")
        self.assertEqual(self.vol(V2)["volume_type"], "fast")
        self.assertFalse(self.cloud.servers[0]["locked"])            # the lock taken for the move was released again
        self.assertIn(V2, self.engine.failed)
        self.assertEqual([list(b)[0] for _, b in self.cloud.posts], ["lock", "os-retype", "unlock"])
        phases = [e["phase"] for e in L.history(self.state.journal, V2)]
        self.assertIn("rollback", phases)

    def test_a_move_that_fails_in_the_cloud_is_rolled_back(self):
        self.cloud.fail_retype.add(V1)
        self.go(V1)
        self.run_until(V1, "done")
        self.assertEqual((self.vol(V1)["volume_type"], self.vol(V1)["os-vol-host-attr:host"]), ("fast", "ctl@sf-1#pool"))
        self.assertIn(V1, self.engine.failed)

    def test_a_retype_that_the_cloud_accepts_and_silently_undoes_is_rolled_back(self):
        # Seen on a real Cinder: a volume with a snapshot. The call is answered 202, the volume goes back to normal, nothing says why.
        self.cloud.revert_retype.add(V2)
        self.go(V2)
        self.run_until(V2, "cutover", 1)
        for _ in range(4):
            self.engine.tick()                                           # the cloud has undone it by now
        self.assertEqual(self.phase(V2), "cutover")                      # inside the grace time it is still given the benefit of the doubt
        self.now += timedelta(seconds=60)
        self.run_until(V2, "done")
        self.assertIn(V2, self.engine.failed)
        self.assertEqual(self.vol(V2)["volume_type"], "fast")
        self.assertFalse(self.cloud.servers[0]["locked"])                # and the instance lock taken for the move is gone
        reason = [e for e in L.history(self.state.journal, V2) if e["phase"] == "rollback"][0]["reason"]
        self.assertIn("put the volume back unchanged", reason)

    def test_a_run_for_volumes_that_are_not_class_a_or_b_is_refused_not_ended_quietly(self):
        with self.assertRaises(EngineError):
            self.engine.start(ids=[V4], thread=False)                    # V4 is class C: this method does not move it
        self.assertEqual(self.cloud.posts, [])

    def test_an_instance_that_cannot_be_locked_means_no_retype(self):
        self.cloud.reject_lock.add(SRV)
        self.go(V2)
        self.run_until(V2, "done")
        self.assertEqual([list(b)[0] for _, b in self.cloud.posts], ["lock"])
        self.assertEqual(self.vol(V2)["volume_type"], "fast")

    def test_an_instance_already_locked_by_someone_else_stays_locked(self):
        self.cloud.servers[0]["locked"] = True
        self.go(V2)
        self.run_until(V2, "postcheck")
        self.assertTrue(self.cloud.servers[0]["locked"])
        self.assertEqual([list(b)[0] for _, b in self.cloud.posts], ["os-retype"])

    def test_a_lost_answer_halts_everything_and_is_read_back_after_a_person_resumes(self):
        self.cloud.drop_retype.add(V1)
        self.go(V1)
        self.engine.tick()
        h = safety.halted(self.state.journal)
        self.assertIsNotNone(h)
        self.assertIn("unknown", h["reason"])
        self.assertEqual(len(self.state.journal.unresolved_steps()), 1)
        n = len(self.cloud.posts)
        for _ in range(3):
            self.engine.tick()
        self.assertEqual(len(self.cloud.posts), n)                    # no retry, ever
        self.assertEqual(len(self.state.journal.unresolved_steps()), 1)
        safety.resume(self.state.journal, OPERATOR, "looked at the cloud, resuming the test")
        self.run_until(V1, "postcheck")
        self.assertEqual(len(self.cloud.posts), n)                    # still one retype call only
        self.assertEqual(self.state.journal.unresolved_steps(), [])
        self.assertEqual(self.vol(V1)["volume_type"], "pure")

    def test_a_failed_post_check_halts_and_blocks_the_soak(self):
        self.go(V1)
        self.run_until(V1, "cutover", 1)
        self.vol(V1)["size"] = 99                                      # the cloud shows a different size after the move
        self.run_until(V1, "postcheck")
        self.assertFalse(L.current(self.state.journal, V1)["ok"])
        self.assertIsNotNone(safety.halted(self.state.journal))
        self.sign(V1, "G4")
        with self.assertRaises(L.LifecycleError):
            L.enter(self.state.journal, V1, "soak", gate=GATE, now=self.now)

    def test_a_changed_identity_field_fails_the_post_check(self):
        self.go(V1)
        self.run_until(V1, "cutover", 1)
        self.vol(V1)["created_at"] = "2020-01-01T00:00:00.000000"      # a retype must never touch what the tenant sees
        self.run_until(V1, "postcheck")
        self.assertFalse(L.current(self.state.journal, V1)["ok"])
        failed = [c["name"] for c in L.current(self.state.journal, V1)["checks"] if not c["ok"]]
        self.assertEqual(failed, ["Same creation time"])

    def test_a_rename_or_metadata_edit_during_the_move_does_not_fail_the_post_check(self):
        self.go(V1)
        self.run_until(V1, "cutover", 1)
        self.vol(V1)["name"] = "renamed-by-the-tenant"
        self.vol(V1)["description"] = "edited"
        self.vol(V1)["metadata"] = {"owner": "someone"}
        self.run_until(V1, "postcheck")
        self.assertTrue(L.current(self.state.journal, V1)["ok"])
        self.assertIsNone(safety.halted(self.state.journal))

    def test_too_many_failures_halt_the_run(self):
        self.state.options["stop_after_failures"] = 1
        self.cloud.reject_retype[V1] = 400
        self.go(V1)
        self.run_until(V1, "rollback", 3)
        self.engine.tick()
        h = safety.halted(self.state.journal)
        self.assertIsNotNone(h)                                         # the limit halted the run
        self.assertIn("1 volumes failed", h["reason"])
        self.engine.tick()
        self.assertEqual(self.phase(V1), "rollback")                    # and nothing more is done until a person resumes

    def test_an_error_status_after_the_retype_halts_for_a_person(self):
        self.go(V1)
        self.run_until(V1, "cutover", 1)
        self.vol(V1)["status"] = "error"
        self.engine.tick()
        self.assertIsNotNone(safety.halted(self.state.journal))
        self.assertEqual(self.phase(V1), "cutover")


class ReverseTests(EngineCase):
    def moved(self, vid):
        self.start([vid])
        self.run_until(vid, "validate")
        self.sign(vid, "G2", "G3")
        self.run_until(vid, "postcheck")

    def test_a_moved_volume_can_be_reversed_with_a_retype_back(self):
        self.moved(V2)
        self.engine.reverse(V2, "the application owner reports a problem", "Olive Operator")
        self.run_until(V2, "done", 15)
        v = self.vol(V2)
        self.assertEqual((v["volume_type"], v["os-vol-host-attr:host"]), ("fast", "ctl@sf-1#pool"))
        self.assertFalse(self.cloud.servers[0]["locked"])
        # the way back is a move too: the instance is locked around it, as on the way there (the real lab showed it was not)
        self.assertEqual([list(b)[0] for _, b in self.cloud.posts], ["lock", "os-retype", "unlock", "lock", "os-retype", "unlock"])
        steps = {r["name"]: r for r in self.state.journal.steps("retype:" + V2)}
        self.assertEqual(steps["retype_back"]["status"], "done")

    def test_a_reverse_needs_a_reason_and_a_cut_over_volume(self):
        self.start([V1])
        self.run_until(V1, "validate")
        with self.assertRaises(EngineError):
            self.engine.reverse(V1, "too early to reverse", "Olive Operator")
        self.sign(V1, "G2", "G3")
        self.run_until(V1, "postcheck")
        with self.assertRaises(EngineError):
            self.engine.reverse(V1, "short", "Olive Operator")


class PlanTests(unittest.TestCase):
    def test_backend_of(self):
        self.assertEqual(plan.backend_of("ctl@sf-1#pool"), "sf-1")
        self.assertEqual(plan.backend_of("sf-1"), "sf-1")
        self.assertEqual(plan.backend_of(None), "")

    def test_an_instance_whose_lock_state_is_not_shown_is_never_locked_blind(self):
        vol = {"id": "v", "status": "in-use", "volume_type": "slow", "os-vol-host-attr:host": "ctl@sf-1#pool", "attachments": [{"server_id": "s"}]}
        spec = {"class": "A", "source_backend": "sf-1", "target_backend": "pure-1", "target_type": "fast",
                "types": [{"name": "fast", "extra_specs": {"volume_backend_name": "pure-1"}}]}
        shown = {"s": {"status": "ACTIVE", "locked": False}}
        hidden = {"s": {"status": "ACTIVE"}}                    # an answer from a Nova microversion older than 2.9
        self.assertEqual(plan.verdict(plan.validate(vol, [], shown, spec)), "ok")
        checks = plan.validate(vol, [], hidden, spec)
        self.assertEqual(plan.verdict(checks), "blocked")
        self.assertIn("never locked blind", [c["detail"] for c in checks if not c["ok"]][0])


if __name__ == "__main__":
    unittest.main()


class WindowAndReverseAllTests(EngineCase):
    """Run window (R3: when) and the whole-run reverse with a preview (R6: see it before it runs)."""

    def moved(self, vids):
        for vid in vids:
            self.run_until(vid, "validate")
            self.sign(vid, "G2", "G3")
        for vid in vids:
            self.run_until(vid, "postcheck")

    def test_nothing_is_taken_or_sent_before_the_window_opens(self):
        opens = self.now + timedelta(hours=2)
        self.engine.start(ids=[V1], thread=False, window=(opens, None))
        for _ in range(4):
            self.engine.tick()
        self.assertIsNone(self.phase(V1))                              # not even taken from the queue
        self.assertEqual(self.cloud.posts, [])
        self.assertFalse(self.engine.view()["window"]["open"])
        self.now = opens + timedelta(minutes=1)
        self.run_until(V1, "validate")
        self.sign(V1, "G2", "G3")
        self.run_until(V1, "postcheck")

    def test_a_volume_ready_when_the_window_closes_waits_for_the_next_one(self):
        closes = self.now + timedelta(hours=1)
        self.engine.start(ids=[V1], thread=False, window=(None, closes))
        self.run_until(V1, "validate")
        self.sign(V1, "G2", "G3")
        self.now = closes + timedelta(minutes=5)
        for _ in range(3):
            self.engine.tick()
        self.assertEqual(self.phase(V1), "validate")
        self.assertEqual(self.cloud.posts, [])
        self.assertIn("run window", self.engine.notes[V1])

    def test_a_window_that_ends_before_it_starts_is_refused(self):
        with self.assertRaises(EngineError):
            self.engine.start(ids=[V1], thread=False, window=(self.now + timedelta(hours=2), self.now + timedelta(hours=1)))

    def test_the_preview_of_a_reverse_changes_nothing_and_lists_the_calls(self):
        self.start([V1, V2])
        self.moved([V1, V2])
        posts = len(self.cloud.posts)
        pv = self.engine.reverse_preview()
        self.assertEqual(len(self.cloud.posts), posts)
        by_id = {r["id"]: r for r in pv["volumes"]}
        self.assertTrue(all(r["can"] and r["to_type"] == "fast" for r in by_id.values()))
        self.assertEqual([c["step"] for c in by_id[V1]["calls"]], ["retype"])
        self.assertEqual([c["step"] for c in by_id[V2]["calls"]], ["lock", "retype", "unlock"])      # the attached one is locked around it
        self.assertEqual(self.phase(V1), "postcheck")                                                # still where it was

    def test_a_volume_that_was_never_cut_over_cannot_be_reversed_or_previewed_as_reversible(self):
        self.start([V1])
        self.run_until(V1, "validate")
        self.assertEqual(self.engine.reversible(), [])
        self.assertFalse(self.engine.reverse_preview([V1])["volumes"][0]["can"])
        with self.assertRaises(EngineError):
            self.engine.reverse_all("the application owner asked for all of it back", "Olive Operator")

    def test_reversing_the_whole_run_puts_every_cut_over_volume_back(self):
        self.start([V1, V2])
        self.moved([V1, V2])
        ids = self.engine.reverse_all("the change window was cancelled by the owner", "Olive Operator")
        self.assertEqual(set(ids), {V1, V2})
        self.run_until(V1, "done", 20)
        self.run_until(V2, "done", 20)
        for vid in (V1, V2):
            v = self.vol(vid)
            self.assertEqual((v["volume_type"], v["os-vol-host-attr:host"]), ("fast", "ctl@sf-1#pool"))
        self.assertFalse(self.cloud.servers[0]["locked"])


class WindowTextTests(unittest.TestCase):
    def test_the_window_times_are_read_as_utc_and_a_bad_one_is_refused(self):
        from openmove import server
        self.assertEqual(server._when("2026-11-07T22:00:00Z", "start_at"), datetime(2026, 11, 7, 22, 0, tzinfo=timezone.utc))
        self.assertEqual(server._when("2026-11-07T22:00", "start_at"), datetime(2026, 11, 7, 22, 0, tzinfo=timezone.utc))
        self.assertIsNone(server._when("", "start_at"))
        with self.assertRaises(ValueError):
            server._when("tomorrow evening", "stop_at")
