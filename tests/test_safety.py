# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Safety (requirement R13): nothing changes a cloud without a permit, and a permit fails closed."""
import ast
import tempfile
import unittest
from pathlib import Path

from openmove import demo, safety
from openmove import server as S
from openmove.connectors.httpclient import ConnectorError, Http, ReadOnlyViolation
from openmove.connectors.openstack import BLOCK_STORAGE_TYPES, OpenStackSession
from openmove.journal import Journal
from openmove.server import create_server
from tests.mock_openstack import PASSWORD, sample_cloud


class ReadOnlyClientTests(unittest.TestCase):
    def setUp(self):
        self.cloud = sample_cloud().start()
        self.s = OpenStackSession(self.cloud.auth_url, "admin", PASSWORD, "admin")
        self.s.authenticate()
        self.cloud.calls.clear()

    def tearDown(self):
        self.cloud.stop()

    def test_every_change_is_refused_without_a_permit_and_nothing_is_sent(self):
        base = self.s.endpoint(BLOCK_STORAGE_TYPES)[1]
        for method in ("POST", "PUT", "DELETE", "PATCH", "post", "Delete"):
            with self.assertRaises(ReadOnlyViolation):
                self.s.http.request(method, base + "/volumes/x/action", headers=self.s._headers(), body={"os-force_delete": None})
        self.assertEqual(self.cloud.calls, [])

    def test_a_login_lookalike_without_auth_is_refused(self):
        with self.assertRaises(ReadOnlyViolation):
            self.s.http.request("POST", self.cloud.auth_url + "/v3/auth/tokens", body={"something": "else"})
        with self.assertRaises(ReadOnlyViolation):
            self.s.http.request("POST", self.cloud.auth_url + "/volumes/auth/tokens-not", body={"auth": {}})

    def test_mutate_needs_a_permit(self):
        with self.assertRaises(ReadOnlyViolation):
            self.s.mutate(None, "POST", BLOCK_STORAGE_TYPES, "/volumes/x/action", {"a": 1})
        with self.assertRaises(ConnectorError):
            self.s.mutate(None, "GET", BLOCK_STORAGE_TYPES, "/volumes")  # reads do not go through mutate
        self.assertEqual(self.cloud.calls, [])

    def test_a_permit_that_raises_or_says_no_is_a_refusal(self):
        class Raises:
            def check(self, *a):
                raise RuntimeError("boom")

        class Falsy:
            def check(self, *a):
                return False
        for p in (Raises(), Falsy(), object()):
            with self.assertRaises(ReadOnlyViolation):
                self.s.mutate(p, "DELETE", BLOCK_STORAGE_TYPES, "/volumes/x")
        self.assertEqual(self.cloud.calls, [])

    def test_reads_still_work(self):
        self.assertTrue(self.s.get(BLOCK_STORAGE_TYPES, "/volumes/detail", params={"all_tenants": 1}))

    def test_unknown_outcome_is_flagged_for_changes_only(self):
        http = Http(timeout=0.5)

        class Yes:
            def check(self, *a):
                return True
        with self.assertRaises(ConnectorError) as cm:
            http.request("DELETE", "http://127.0.0.1:9/x", permit=Yes(), expect_json=False)
        self.assertTrue(cm.exception.outcome_unknown)
        with self.assertRaises(ConnectorError) as cm:
            http.request("GET", "http://127.0.0.1:9/x")
        self.assertFalse(cm.exception.outcome_unknown)


class HaltTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.j = Journal(Path(self.dir.name) / "j.db")

    def tearDown(self):
        self.j.close()
        self.dir.cleanup()

    def test_halt_and_resume(self):
        self.assertIsNone(safety.halted(self.j))
        safety.halt(self.j, "Pat Example", "array latency is rising")
        self.assertEqual(safety.halted(self.j)["by"], "Pat Example")
        safety.resume(self.j, "Sam Sample", "latency back to normal")
        self.assertIsNone(safety.halted(self.j))
        safety.halt(self.j, "Pat Example", "second halt for a test")
        self.assertIsNotNone(safety.halted(self.j))

    def test_halt_needs_a_name_and_a_reason(self):
        with self.assertRaises(ValueError):
            safety.halt(self.j, "", "a proper reason")
        with self.assertRaises(ValueError):
            safety.halt(self.j, "Pat Example", "stop")
        self.assertIsNone(safety.halted(self.j))


class PermitTests(unittest.TestCase):
    OK_PROFILE = {"status": "validated", "release": "Queens"}
    OK_GATE = {"status": "approved", "message": "ok"}

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.j = Journal(Path(self.dir.name) / "j.db")
        self.profile, self.gate = dict(self.OK_PROFILE), dict(self.OK_GATE)

    def tearDown(self):
        self.j.close()
        self.dir.cleanup()

    def permit(self):
        return safety.grant(self.j, lambda: self.profile, lambda: self.gate)

    def test_granted_when_everything_holds(self):
        self.assertTrue(self.permit().check("POST", "http://x/y"))

    def test_untested_release_is_refused(self):
        self.profile = {"status": "untested", "reason": "no validation"}
        with self.assertRaises(safety.Refused):
            self.permit()

    def test_every_gate_state_but_approved_is_refused(self):
        for st in ("blocked", "open", "awaiting_signoff", "stale", None, ""):
            self.gate = {"status": st, "message": "x"}
            with self.assertRaises(safety.Refused, msg=str(st)):
                self.permit()
        self.gate = None
        with self.assertRaises(safety.Refused):
            self.permit()

    def test_conditions_are_checked_again_for_every_change(self):
        p = self.permit()
        p.check()
        self.gate = {"status": "stale", "message": "too old"}      # the pre-flight went stale mid-run
        with self.assertRaises(safety.Refused):
            p.check()
        self.gate = dict(self.OK_GATE)
        p.check()
        safety.halt(self.j, "Pat Example", "something looks wrong")  # the halt button
        with self.assertRaises(safety.Halted):
            p.check()
        safety.resume(self.j, "Sam Sample", "all clear after review")
        p.check()

    def test_a_broken_gate_function_fails_closed(self):
        p = safety.Permit(self.j, lambda: self.profile, lambda: 1 / 0)
        with self.assertRaises(safety.Refused):
            p.check()
        p = safety.Permit(self.j, lambda: (_ for _ in ()).throw(KeyError("platform")), lambda: self.gate)
        with self.assertRaises(safety.Refused):
            p.check()

    def test_a_permit_drives_a_real_request_only_while_valid(self):
        cloud = sample_cloud().start()
        try:
            s = OpenStackSession(cloud.auth_url, "admin", PASSWORD, "admin")
            s.authenticate()
            cloud.calls.clear()
            p = self.permit()
            safety.halt(self.j, "Pat Example", "stop everything now")
            with self.assertRaises(ReadOnlyViolation):
                s.mutate(p, "POST", BLOCK_STORAGE_TYPES, "/volumes/x/action", {})
            self.assertEqual(cloud.calls, [])
            safety.resume(self.j, "Sam Sample", "cleared to continue")
            with self.assertRaises(ConnectorError) as cm:        # the mock has no such route: it answers 404/405, and we see it
                s.mutate(p, "POST", BLOCK_STORAGE_TYPES, "/volumes/x/action", {})
            self.assertNotIsInstance(cm.exception, ReadOnlyViolation)
            self.assertTrue(cloud.calls)
        finally:
            cloud.stop()


class ServerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.server, self.state = create_server("127.0.0.1", 0, Path(self.dir.name))
        demo.start(self.state, scenario="sf-to-pure")
        S.api_discover(self.state, {}, {})

    def tearDown(self):
        self.server.server_close()
        self.state.disconnect()
        demo.stop(self.state)
        self.state.journal.close()
        self.dir.cleanup()

    def test_no_permit_before_the_gate_is_approved(self):
        with self.assertRaises(safety.Refused):
            S.make_permit(self.state)

    def test_halt_shows_in_status_and_resume_clears_it(self):
        self.assertIsNone(S.api_status(self.state, {}, {})["halted"])
        S.api_halt(self.state, {"by": "Pat Example", "reason": "stop for review"}, {})
        self.assertEqual(S.api_status(self.state, {}, {})["halted"]["by"], "Pat Example")
        S.api_resume(self.state, {"by": "Sam Sample", "reason": "review complete"}, {})
        self.assertIsNone(S.api_status(self.state, {}, {})["halted"])

    def test_full_reading_workflow_sends_no_change_to_the_cloud(self):
        S.api_inventory_run(self.state, {}, {})
        S.api_audit_run(self.state, {}, {})
        S.api_demo_dbexport(self.state, {}, {})
        S.api_checks_run(self.state, {"source_backend": "sf-prod", "target_backend": "pure-prod"}, {})
        S.api_report(self.state, {"kind": "before"}, {})
        written = [c for c in self.state.demo_cloud.calls if c[0] not in ("GET",) and not c[1].endswith("/auth/tokens")]
        self.assertEqual(written, [])


class SourceScanTests(unittest.TestCase):
    """The code that reads must not even contain a way to change the cloud: only safety/connectors may name write methods."""

    def test_only_the_client_names_write_methods(self):
        root = Path(__file__).resolve().parents[1] / "openmove"
        allowed = {"connectors/httpclient.py", "connectors/openstack.py", "safety.py", "engine/actions.py"}   # actions.py: the only code that sends a change, always through a permit
        bad = []
        for py in root.rglob("*.py"):
            rel = py.relative_to(root).as_posix()
            if rel in allowed or rel.startswith("demo/"):
                continue
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and node.value in ("PUT", "DELETE", "PATCH"):
                    bad.append("%s:%d" % (rel, node.lineno))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
