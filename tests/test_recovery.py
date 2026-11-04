# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The failsafe: a stop of the program, the computer or the network in the middle of a move costs nothing (docs/RECOVERY.md)."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

from openmove import recovery, supervisor
from openmove import server as S
from openmove.demo.mock_cloud import PASSWORD, MockCloud
from openmove.engine import runner
from openmove.server import create_server

from tests.test_engine import GATE, SECOND, V1, EngineCase


class EngineLoopTests(EngineCase):
    """The engine's loop does not give up at the first error, notices a sleep, and is started again if its thread dies."""

    def setUp(self):
        super().setUp()
        self._pause = runner.LOOP_PAUSE
        runner.LOOP_PAUSE = 0.001                                  # no waiting in the tests

    def tearDown(self):
        runner.LOOP_PAUSE = self._pause
        super().tearDown()

    def ends(self):
        return [e["data"] for e in self.state.journal.events("engine_end", None, 20)]

    def test_an_error_is_retried_and_the_run_goes_on(self):
        self.start([V1])
        real, calls = self.engine.tick, {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("the database was locked")
            real()
            self.engine.status = "finished"
        self.engine.tick = flaky
        self.engine._loop()
        self.assertEqual(calls["n"], 3)
        self.assertEqual(self.engine.status, "finished")
        self.assertEqual(self.engine.trouble, "")                  # recovered: nothing left on the screen
        self.assertEqual(self.ends()[0]["reason"], "finished")

    def test_the_loop_hands_back_after_too_many_errors_in_a_row(self):
        old = runner.MAX_RETRIES
        runner.MAX_RETRIES = 3
        try:
            self.start([V1])
            self.engine.tick = lambda: (_ for _ in ()).throw(RuntimeError("cannot read the cloud"))
            self.engine._loop()
        finally:
            runner.MAX_RETRIES = old
        self.assertTrue(self.engine.status.startswith("stopped after an error"))
        self.assertEqual(self.ends()[0]["reason"], "error")
        self.assertTrue(recovery.engine_should_resume(self.state.journal))      # an error is not a decision: the next start tries again
        self.assertIn("retrying", self.engine.trouble)

    def test_a_long_silence_between_two_looks_is_noticed_and_noted(self):
        self.start([V1])
        real, calls = self.engine.tick, {"n": 0}

        def sleepy():
            calls["n"] += 1
            real()
            if calls["n"] == 1:
                self.now += timedelta(seconds=900)                  # the computer slept for 15 minutes
            if calls["n"] == 2:
                self.engine.status = "finished"
        self.engine.tick = sleepy
        self.engine._loop()
        self.assertEqual(len(self.engine.gaps), 1)
        self.assertGreaterEqual(self.engine.gaps[0], 900)
        self.assertEqual(self.state.journal.latest("engine_gap")["data"]["seconds"], self.engine.gaps[0])

    def test_a_dead_thread_is_started_again(self):
        self.start([V1])                                            # thread=False: the run is meant to go on, and nothing is running it
        real = self.engine.tick

        def once():
            real()
            self.engine.status = "finished"
        self.engine.tick = once
        self.assertTrue(self.engine.ensure_alive())
        self.engine._thread.join(5)
        self.assertEqual(self.engine.revived, 1)
        self.assertEqual(self.state.journal.latest("engine_revive")["data"]["times"], 1)
        self.assertFalse(self.engine.ensure_alive())                # finished: nothing to start

    def test_a_live_thread_is_left_alone(self):
        self.start([V1])
        gate = threading.Event()
        self.engine._thread = threading.Thread(target=gate.wait, daemon=True)
        self.engine._thread.start()
        try:
            self.assertFalse(self.engine.ensure_alive())
        finally:
            gate.set()

    def test_closing_the_program_mid_run_is_carried_on_but_a_persons_stop_is_not(self):
        self.start([V1])
        self.engine.stop("shutdown")
        self.assertTrue(recovery.engine_should_resume(self.state.journal))
        j = self.state.journal
        j.record("engine_run", "batch", {})
        self.assertTrue(recovery.engine_should_resume(j))           # a new run with no end yet: it was cut off
        j.record("engine_end", "batch", {"reason": "stopped"})
        self.assertFalse(recovery.engine_should_resume(j))
        j.record("engine_run", "batch", {})
        j.record("engine_end", "batch", {"reason": "finished"})
        self.assertFalse(recovery.engine_should_resume(j))

    def test_a_volume_under_way_that_is_not_in_the_inventory_read_after_a_restart_keeps_its_spec(self):
        """After a restart the inventory is read for the source back-end only, and a volume that was moved is no longer there."""
        from openmove import lifecycle as L
        L.enter(self.state.journal, V1, "precheck", {"class": "A", "method": L.RETYPE, "source_backend": "sf-1", "target_backend": "pure-1",
                                                      "source_type": "fast", "target_type": "pure"}, gate=GATE, now=self.now)
        self.state.inventory = {"volumes": []}                      # the volume left the source back-end
        sp = self.engine.specs(self.engine.acts(), [V1], {})[V1]
        self.assertEqual((sp["target_type"], sp["source_type"], sp["class"]), ("pure", "fast", "A"))
        sp = self.engine.specs(self.engine.acts(), ["unknown"], {})["unknown"]       # no record: no type, and a reason, not a crash
        self.assertIsNone(sp["target_type"])
        self.assertIn("no record", sp["why_no_type"])

    def test_the_view_tells_the_page_about_trouble_gaps_and_revivals(self):
        v = self.engine.view()
        self.assertEqual((v["trouble"], v["gaps"], v["revived"]), ("", [], 0))


class FakeEngine:
    def __init__(self, status):
        self.status, self.alive_calls = status, 0

    def ensure_alive(self):
        self.alive_calls += 1


class GuardianTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.server, self.state = create_server("127.0.0.1", 0, Path(self.dir.name))
        self.awake = []
        self._keep = recovery.keep_awake
        recovery.keep_awake = lambda on: self.awake.append(on)

    def tearDown(self):
        recovery.keep_awake = self._keep
        self.server.server_close()
        self.state.journal.close()
        self.dir.cleanup()

    def test_while_a_run_goes_on_the_computer_is_asked_to_stay_awake_and_the_thread_is_watched(self):
        g = recovery.Guardian(self.state)
        self.state.engine = FakeEngine("running")
        g.look()
        g.look()
        self.assertEqual(self.awake, [True])                         # asked once, not on every look
        self.assertEqual(self.state.engine.alive_calls, 2)
        self.state.engine.status = "finished"
        g.look()
        self.assertEqual(self.awake, [True, False])

    def test_the_token_survives_a_restart_and_a_new_one_can_be_had_by_deleting_the_file(self):
        first = self.state.token
        self.assertEqual(recovery.token(self.dir.name), first)
        (Path(self.dir.name) / recovery.TOKEN_FILE).unlink()
        self.assertNotEqual(recovery.token(self.dir.name), first)

    def test_the_health_address_answers_without_a_token_and_says_nothing_else(self):
        t = threading.Thread(target=self.server.serve_forever, daemon=True)
        t.start()
        try:
            port = self.server.server_address[1]
            with urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=5) as r:
                body = json.loads(r.read())
            self.assertEqual(set(body), {"ok", "version"})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/api/status" % port), timeout=5)
            self.assertEqual(ctx.exception.code, 401)
        finally:
            self.server.shutdown()


class RestartTests(unittest.TestCase):
    """A program that is cut off, started again on the same data folder, and signed in again: the work is back."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cloud = MockCloud()
        self.cloud.start()
        self.opened = []

    def tearDown(self):
        for server, state in self.opened:
            state.disconnect()
            server.server_close()
            try:
                state.journal.close()
            except Exception:
                pass
        self.cloud.stop()
        self.dir.cleanup()

    def up(self):
        server, state = create_server("127.0.0.1", 0, Path(self.dir.name))
        self.opened.append((server, state))
        return state

    def sign_in(self, state):
        return S.api_connect(state, {"auth_url": self.cloud.auth_url, "username": "admin", "password": PASSWORD, "project_name": "demo"}, {})

    def scan(self, state):
        self.sign_in(state)
        S.api_discover(state, {}, {})
        S.api_inventory_run(state, {}, {})

    def wait_for(self, state, want, seconds=30):
        end = time.time() + seconds
        while time.time() < end:
            if state.recovery.get("status") in want:
                return
            time.sleep(0.05)
        self.fail("the restore did not finish: %s" % state.recovery)

    def test_a_first_start_has_nothing_to_put_back(self):
        state = self.up()
        self.assertEqual(state.recovery["status"], "none")
        self.assertFalse(state.recovery["unclean"])

    def test_the_saved_context_has_no_password_and_no_cloud_token(self):
        state = self.up()
        self.scan(state)
        state.checks = {"run_id": "r1", "source_backend": "a", "target_backend": "b", "admitted": []}
        self.assertTrue(recovery.save(state, force=True))
        text = (Path(self.dir.name) / recovery.RESUME_FILE).read_text(encoding="utf-8")
        self.assertNotIn(PASSWORD, text)
        saved = json.loads(text)
        self.assertEqual(saved["connection"]["project_name"], "demo")
        self.assertEqual(saved["checks"]["run_id"], "r1")
        self.assertIn("scan", saved)
        self.assertNotIn("token", json.dumps(saved["connection"]).lower())

    def test_a_cut_off_program_waits_for_the_sign_in_and_then_puts_everything_back(self):
        a = self.up()
        self.scan(a)
        a.checks = {"run_id": "r1", "source_backend": "x", "target_backend": "y", "admitted": []}
        a.options = {"concurrency": 3}
        a.released = {"v-1"}
        total = a.inventory["total"]
        recovery.save(a, force=True)
        token = a.token                                              # ... and then nothing: no clean stop, as in a crash or a power cut
        b = self.up()
        self.assertEqual(b.token, token)                             # the open page still works
        self.assertTrue(b.recovery["unclean"])
        self.assertEqual(b.recovery["status"], "waiting")
        self.assertEqual(b.recovery["connection"]["project_name"], "demo")
        self.assertFalse(recovery.save(b, force=True))               # the saved file is not replaced by an empty state while we wait
        self.assertTrue(json.loads((Path(self.dir.name) / recovery.RESUME_FILE).read_text())["checks"])
        self.sign_in(b)
        self.wait_for(b, ("done", "failed"))
        self.assertEqual(b.recovery["status"], "done", b.recovery)
        self.assertEqual(b.inventory["total"], total)
        self.assertEqual(b.checks["run_id"], "r1")                   # the same run: the approvals kept in the journal still count
        self.assertEqual((b.options, b.released), ({"concurrency": 3}, {"v-1"}))
        self.assertEqual(b.journal.latest("recovery")["data"]["ok"], True)

    def test_a_clean_stop_with_nothing_running_starts_quietly(self):
        a = self.up()
        self.scan(a)
        recovery.finish(a, clean=True)
        b = self.up()
        self.assertFalse(b.recovery["unclean"])
        self.assertEqual(b.recovery["status"], "none")

    def test_signing_in_to_another_project_does_not_put_the_saved_work_into_it(self):
        a = self.up()
        self.scan(a)
        recovery.save(a, force=True)
        b = self.up()
        saved = recovery.load(self.dir.name)
        saved["connection"]["project_name"] = "somewhere-else"
        (Path(self.dir.name) / recovery.RESUME_FILE).write_text(json.dumps(saved), encoding="utf-8")
        self.sign_in(b)
        time.sleep(0.2)
        self.assertEqual(b.recovery["status"], "waiting")
        self.assertIn("different cloud", b.recovery["message"])
        self.assertIsNone(b.inventory)

    def test_start_fresh_forgets_the_saved_work_but_not_the_history(self):
        a = self.up()
        self.scan(a)
        recovery.save(a, force=True)
        b = self.up()
        with self.assertRaises(ValueError):
            S.api_recover_discard(b, {"by": "x"}, {})
        S.api_recover_discard(b, {"by": "Olive Operator"}, {})
        self.assertFalse((Path(self.dir.name) / recovery.RESUME_FILE).exists())
        self.assertEqual(b.recovery["status"], "none")
        self.assertTrue(b.journal.events("inventory", None, 5))      # every step taken is still in the history

    def test_a_run_that_was_cut_off_is_known_after_the_restart(self):
        a = self.up()
        self.scan(a)
        a.journal.record("engine_run", "batch", {"queued": 2})
        recovery.save(a, force=True)
        b = self.up()
        self.assertTrue(b.recovery["engine_was_running"])
        self.assertIn("A move was running", b.recovery["message"])

    def test_the_status_carries_the_recovery_note_for_the_page(self):
        a = self.up()
        self.scan(a)
        recovery.save(a, force=True)
        b = self.up()
        st = S.api_status(b, {}, {})
        self.assertEqual(st["recovery"]["status"], "waiting")
        self.assertIn("engine", st["recovery"])


class OneInstanceTests(unittest.TestCase):
    def test_a_second_program_on_the_same_data_folder_is_refused_and_a_closed_one_leaves_no_lock(self):
        with tempfile.TemporaryDirectory() as d:
            first = recovery.acquire_lock(d)
            try:
                with self.assertRaises(recovery.AlreadyRunning) as ctx:
                    recovery.acquire_lock(d)
                self.assertIn("already running", str(ctx.exception))
            finally:
                first.close()                                       # the end of the process does the same, also after a crash
            again = recovery.acquire_lock(d)
            again.close()

    def test_the_port_cannot_be_shared_on_windows(self):
        import os
        from openmove.server import _Server
        self.assertEqual(_Server.allow_reuse_address, os.name != "nt")


class FakeViewEngine:
    status = "running"
    trouble, gaps, revived = "", [], 0

    def __init__(self, rows):
        self.rows = rows

    def view(self):
        return {"volumes": self.rows}


class ProgressPageTests(unittest.TestCase):
    """The Progress page counts only what the engine holds, and says what each volume waits for."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.server, self.state = create_server("127.0.0.1", 0, Path(self.dir.name))

    def tearDown(self):
        self.server.server_close()
        self.state.journal.close()
        self.dir.cleanup()

    def row(self, vid, phase, g2=False, g3=False, g4=False):
        return {"id": vid, "phase": phase, "note": "note %s" % vid, "gates": {"G2": g2, "G3": g3, "G4": g4}}

    def phase(self, vid, phase):
        from openmove import lifecycle
        self.state.journal.record(lifecycle.KIND, vid, {"phase": phase})

    def test_volumes_that_need_a_second_person_are_counted_and_the_percentage_stays_under_100(self):
        for vid, ph in (("a", "validate"), ("b", "recheck"), ("c", "postcheck"), ("old1", "done"), ("old2", "done")):
            self.phase(vid, ph)                                    # old1 and old2 are earlier work that is not part of this run
        self.state.engine = FakeViewEngine([self.row("a", "validate"), self.row("b", "recheck"), self.row("c", "postcheck"), self.row("q", "queued")])
        live = S.api_live(self.state, {}, {})
        self.assertEqual(live["total"], 4)
        self.assertEqual((live["need_g23"], live["need_g4"], live["ready"]), (2, 1, 0))
        self.assertLessEqual(live["overall"], 100)
        self.assertEqual({v["id"] for v in live["volumes"]}, {"a", "b", "c", "q"})
        self.assertEqual(next(v for v in live["volumes"] if v["id"] == "a")["note"], "note a")

    def test_what_waits_for_a_person_is_listed_with_where_to_do_it(self):
        self.state.journal.record("engine_start", "batch", {"by": "Olive Operator"})
        self.state.engine = FakeViewEngine([self.row("a", "validate"), self.row("b", "recheck"), self.row("c", "validate", True, True),
                                            self.row("d", "postcheck"), self.row("e", "postcheck", g4=True), self.row("f", "queued")])
        w = {x["kind"]: x for x in S.api_status(self.state, {}, {})["waiting"]}
        self.assertEqual({k: v["count"] for k, v in w.items()}, {"g23": 2, "release": 1, "g4": 1})
        self.assertEqual((w["g23"]["tab"], w["g23"]["button"]), ("live", "sign-g23"))
        self.assertIn("Olive Operator", w["g23"]["text"])           # who may not approve
        self.assertIn("Approve before switching", w["g23"]["action"])      # where, and what to press
        self.state.released = {"c"}
        self.assertNotIn("release", {x["kind"] for x in S.api_status(self.state, {}, {})["waiting"]})

    def test_release_reaches_a_volume_in_flight_that_is_not_in_todays_choice(self):
        self.state.engine = FakeViewEngine([self.row("held", "recheck", True, True)])
        self.state.selection = set()                                 # the choice no longer holds it (a restart, a changed choice)
        with self.assertRaises(ValueError):
            S.api_release(self.state, {"all": True, "by": "x"}, {})
        S.api_release(self.state, {"all": True, "by": "Olive Operator"}, {})
        self.assertEqual(self.state.released, {"held"})

    def test_nothing_waits_when_nothing_needs_a_person(self):
        self.assertEqual(S.api_status(self.state, {}, {})["waiting"], [])
        self.state.engine = FakeViewEngine([self.row("a", "cutover"), self.row("f", "queued")])
        self.assertEqual(S.api_status(self.state, {}, {})["waiting"], [])

    def test_signed_volumes_wait_for_the_release_not_for_a_person(self):
        self.phase("a", "validate")
        self.state.engine = FakeViewEngine([self.row("a", "validate", True, True)])
        live = S.api_live(self.state, {}, {})
        self.assertEqual((live["need_g23"], live["ready"]), (0, 1))
        self.state.released = {"a"}
        self.assertEqual(S.api_live(self.state, {}, {})["ready"], 0)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, n):
        self.t += n


class FakeProc:
    def __init__(self, clock, lifetime, code):
        self.clock, self.start, self.lifetime, self.code = clock, clock.t, lifetime, code
        self.terminated = False

    def poll(self):
        if self.terminated:
            return -15
        return self.code if self.clock.t - self.start >= self.lifetime else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def wait(self, timeout=None):
        return self.poll()


class SupervisorTests(unittest.TestCase):
    def run_with(self, lives, check=lambda h, p: True, args=("--lab", "--port", "9100")):
        clock, said, started = FakeClock(), [], []
        queue = list(lives)

        def popen(cmd, env=None):
            started.append((cmd, env))
            life, code = queue.pop(0)
            return FakeProc(clock, life, code)
        rc = supervisor.run(list(args) + ["--supervise"], say=said.append, popen=popen, sleep=clock.sleep, clock=clock.now, check=check, health_every=20)
        return rc, said, started

    def test_a_normal_end_is_not_started_again(self):
        rc, said, started = self.run_with([(600, 0)])
        self.assertEqual((rc, len(started)), (0, 1))
        self.assertEqual(said, [])

    def test_a_setting_that_is_refused_is_not_started_again(self):
        rc, said, started = self.run_with([(2, 2)])
        self.assertEqual((rc, len(started)), (2, 1))

    def test_a_crash_is_started_again_without_opening_another_browser_window(self):
        rc, said, started = self.run_with([(300, 1), (600, 0)])
        self.assertEqual((rc, len(started)), (0, 2))
        first, second = started[0][0], started[1][0]
        self.assertNotIn("--supervise", first + second)
        self.assertNotIn("--no-browser", first)
        self.assertIn("--no-browser", second)
        self.assertEqual(started[1][1][supervisor.CHILD_ENV], "1")
        self.assertIn("Your work is saved", said[0])

    def test_quick_stops_in_a_row_end_with_a_clear_message_and_nothing_is_lost(self):
        rc, said, started = self.run_with([(3, 1)] * 20)
        self.assertEqual(rc, 1)
        self.assertEqual(len(started), supervisor.MAX_RAPID)
        self.assertTrue(any("will not be started again by itself" in t for t in said))

    def test_a_long_run_resets_the_count_of_quick_stops(self):
        rc, said, started = self.run_with([(3, 1)] * 5 + [(500, 1)] + [(3, 1)] * 5 + [(10, 0)])
        self.assertEqual(rc, 0)
        self.assertEqual(len(started), 12)

    def test_a_program_that_runs_but_does_not_answer_is_stopped_and_started_again(self):
        answers = iter([False] * 50)
        rc, said, started = self.run_with([(10 ** 6, 0), (50, 0)], check=lambda h, p: next(answers))
        self.assertEqual((rc, len(started)), (0, 2))
        self.assertTrue(any("stopped answering" in s for s in said))

    def test_the_health_address_is_found_from_the_options(self):
        self.assertEqual(supervisor.port_of(["--port", "9100"]), ("127.0.0.1", 9100))
        self.assertEqual(supervisor.port_of(["--port=9200", "--host", "0.0.0.0"]), ("127.0.0.1", 9200))
        self.assertEqual(supervisor.port_of([]), ("127.0.0.1", 8765))


if __name__ == "__main__":
    unittest.main()
