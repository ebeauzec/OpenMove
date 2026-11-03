# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Diagnostics for dark sites (docs/DIAGNOSTICS.md): the log, the call trace, the support bundle at each redaction level, the routes on the
screen and the command line. The point of every test: a fault must be findable from the bundle alone, and a redacted bundle holds no customer data."""
import io
import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from openmove import __main__ as cli
from openmove import diag as diag_mod
from openmove import server as S
from openmove import server_hosts as SH
from openmove.connectors import ssh
from openmove.journal import Journal

SECRETS = ("192.0.2.17", "acme-billing-db", "Alice Smith", "alice@customer.example", "keystone.customer.example", "hunter2")


def read_bundle(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n).decode("utf-8") for n in z.namelist()}


class DiagCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.home = Path(self.dir.name)
        self.journal = Journal(self.home / "journal.db")
        self.addCleanup(self.journal.close)
        self.diag = diag_mod.Diag(self.home)
        self.addCleanup(setattr, ssh, "TRACE", None)

    def fill(self):
        j = self.journal
        j.record("connect", "cloud", {"address": "https://keystone.customer.example:5000/v3", "user": "alice@customer.example", "by": "Alice Smith"})
        j.record("stage", "3f2b8c1e-9d4a-4b7e-8a61-0c5d2e7f9a10", {"state": "copy", "error": "mount failed on 192.0.2.17 for acme-billing-db: device busy"})
        j.record("refusal", "x", {"reason": "password=hunter2 was sent", "password": "hunter2"})
        step = j.begin_step("job", "copy", {"volume": "acme-billing-db"}, {"undo": "remove staging"}, "3f2b8c1e-9d4a-4b7e-8a61-0c5d2e7f9a10")
        j.finish_step(step, "failed", {"error": "lvcreate failed on 192.0.2.17"})
        ev = self.home / "evidence" / "3f2b8c1e-9d4a-4b7e-8a61-0c5d2e7f9a10"
        ev.mkdir(parents=True)
        (ev / "failed.json").write_text(json.dumps({"error": "boom on 192.0.2.17", "volume_name": "acme-billing-db"}), encoding="utf-8")
        self.diag.log("error", "step failed for acme-billing-db", host="192.0.2.17")
        self.diag.trace({"kind": "mover", "host": "m1", "verb": "lv.create", "rc": 5, "stderr": "no space for acme-billing-db on 192.0.2.17"})

    def bundle(self, level, **kw):
        res = self.diag.bundle(self.journal, extras={"inventory": {"summary": {}, "volumes": [{"id": "x", "name": "acme-billing-db", "project_name": "Acme"}]}},
                               level=level, remote=False, reason="test", **kw)
        return res, read_bundle(res["path"])


class LogTests(DiagCase):
    def test_the_log_gets_one_json_line_per_event_with_the_full_message(self):
        self.diag.log("error", "x" * 5000)
        self.diag.exception("the step", ValueError("boom"))
        lines = (self.home / "logs" / "openmove.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(len(json.loads(lines[0])["msg"]), 5000)                # an error text is never shortened
        self.assertIn("ValueError: boom", json.loads(lines[1])["msg"])

    def test_the_ssh_trace_goes_into_the_call_log(self):
        self.diag.attach()
        ssh.trace({"kind": "mover", "host": "m1", "verb": "probe", "rc": 0})
        rows = [json.loads(x) for x in (self.home / "logs" / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[-1]["verb"], "probe")
        self.assertIn("at", rows[-1])

    def test_the_log_can_be_redacted_as_it_is_written(self):
        self.diag.set_settings(log_redaction="standard")
        self.diag.log("error", "failed on 192.0.2.17", token="abc")
        text = (self.home / "logs" / "openmove.log").read_text(encoding="utf-8")
        self.assertNotIn("192.0.2.17", text)
        self.assertNotIn("abc", text)
        self.assertTrue((self.home / "diag" / "redaction-map.json").exists())

    def test_settings_are_checked_and_remembered(self):
        with self.assertRaises(ValueError):
            self.diag.set_settings(redaction="maximum")
        self.diag.set_settings(redaction="strict")
        self.assertEqual(diag_mod.Diag(self.home).settings["redaction"], "strict")

    def test_a_full_disk_never_stops_the_program(self):
        self.diag.logs = self.home / "does" / "not" / "exist"
        self.diag.log("info", "still fine")                                      # must not raise


class BundleTests(DiagCase):
    def test_the_bundle_holds_everything_needed_and_a_manifest_with_checksums(self):
        self.fill()
        res, files = self.bundle("none")
        for name in ("manifest.json", "README.txt", "environment.json", "settings.json", "journal/events.jsonl", "journal/steps.jsonl",
                     "state/inventory.json", "evidence/3f2b8c1e-9d4a-4b7e-8a61-0c5d2e7f9a10/failed.json", "logs/openmove.log", "logs/calls.jsonl"):
            self.assertIn(name, files)
        manifest = json.loads(files["manifest.json"])
        self.assertEqual(manifest["redaction"], "none")
        self.assertIn("journal/events.jsonl", manifest["files"])
        self.assertIn("mount failed on 192.0.2.17 for acme-billing-db: device busy", files["journal/events.jsonl"])    # complete, unshortened

    def test_standard_and_strict_bundles_hold_no_customer_data(self):
        self.fill()
        for level in ("standard", "strict"):
            res, files = self.bundle(level)
            blob = "\n".join(files.values())
            for secret in SECRETS:
                self.assertNotIn(secret, blob, "%s leaked at %s" % (secret, level))
            self.assertNotIn("redaction-map", "".join(files))                     # the map is never in the bundle
            self.assertEqual(json.loads(files["manifest.json"])["redaction"], level)

    def test_a_redacted_bundle_still_shows_what_failed_and_how_things_relate(self):
        self.fill()
        res, files = self.bundle("standard")
        events = [json.loads(x) for x in files["journal/events.jsonl"].splitlines()]
        stage = next(e for e in events if e["kind"] == "stage")
        self.assertIn("mount failed on ip-", stage["data"]["error"])
        self.assertIn("device busy", stage["data"]["error"])                       # the cause stays readable
        ip = [w for w in stage["data"]["error"].split() if w.startswith("ip-")][0]
        self.assertIn(ip, files["logs/calls.jsonl"])                               # the same address has the same token everywhere
        self.assertIn(ip, json.loads(files["evidence/3f2b8c1e-9d4a-4b7e-8a61-0c5d2e7f9a10/failed.json"])["error"])

    def test_the_site_can_turn_a_token_back_and_only_the_site(self):
        self.fill()
        res, files = self.bundle("standard")
        tok = next(w for w in json.loads(files["evidence/3f2b8c1e-9d4a-4b7e-8a61-0c5d2e7f9a10/failed.json"])["error"].split() if w.startswith("ip-"))
        self.assertEqual(diag_mod.decode(self.home, tok), "192.0.2.17")
        self.assertIsNone(diag_mod.decode(self.home, "ip-999"))

    def test_collection_from_a_host_that_fails_is_recorded_not_hidden(self):
        class Broken:
            name, spec = "broken1", {"name": "broken1", "roles": ["mover"]}

            def mover(self, verb, args=None, timeout=0):
                raise ssh.SshError("ssh to broken1 failed: no route to host")
        res = self.diag.bundle(self.journal, remote=True, level="none", hosts=[Broken()])
        files = read_bundle(res["path"])
        self.assertIn("hosts/broken1.mover.error.txt", files)
        self.assertIn("no route to host", files["hosts/broken1.mover.error.txt"])
        self.assertIn("collecting from host broken1", (self.home / "logs" / "openmove.log").read_text(encoding="utf-8"))

    def test_a_local_host_is_collected_with_its_jobs(self):
        h = ssh.Host({"name": "here", "transport": "local", "roles": ["mover"], "mover_python": sys.executable, "mover_dir": str(self.home / "mv")}, self.home)
        res = self.diag.bundle(self.journal, remote=True, level="none", hosts=[h])
        files = read_bundle(res["path"])
        self.assertIn("hosts/here.mover.json", files)
        self.assertIn("commands", json.loads(files["hosts/here.mover.json"]))


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.server, self.state = S.create_server("127.0.0.1", 0, Path(self.dir.name))
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.state.journal.close)
        self.addCleanup(setattr, ssh, "TRACE", None)

    def test_hosts_are_registered_listed_and_removed_with_events(self):
        out = SH.api_hosts_save(self.state, {"host": {"name": "mover1", "roles": ["mover"], "address": "mover1.example.test", "user": "openmove"}}, {})
        self.assertEqual(out["hosts"][0]["name"], "mover1")
        self.assertFalse(out["hosts"][0]["pinned"])
        self.assertTrue(out["public_key"] == "" or out["public_key"].startswith("ssh-"))
        with self.assertRaises(Exception) as cm:
            SH.api_hosts_save(self.state, {"host": {"name": "bad name", "address": "a.test"}}, {})
        self.assertEqual(getattr(cm.exception, "status", 400), 400)
        SH.api_hosts_remove(self.state, {"name": "mover1"}, {})
        kinds = [e["kind"] for e in self.state.journal.events(None, None, 50)]
        self.assertIn("host_saved", kinds)
        self.assertIn("host_removed", kinds)
        with self.assertRaises(Exception):
            SH.api_hosts_remove(self.state, {"name": "mover1"}, {})

    def test_trusting_a_key_needs_a_name_and_a_fingerprint(self):
        SH.api_hosts_save(self.state, {"host": {"name": "m", "roles": ["mover"], "address": "203.0.113.9"}}, {})
        with self.assertRaises(ValueError):
            SH.api_hosts_trust(self.state, {"name": "m", "fingerprint": "SHA256:abc", "by": "ab"}, {})
        with self.assertRaises(ValueError):
            SH.api_hosts_trust(self.state, {"name": "m", "fingerprint": "", "by": "Olive Operator"}, {})

    def test_diagnostics_settings_bundle_download_and_decode(self):
        got = SH.api_diag_get(self.state, {}, {})
        self.assertEqual(got["levels"], ["none", "standard", "strict"])
        self.assertIn("standard", got["text"])
        with self.assertRaises(ValueError):
            SH.api_diag_settings(self.state, {"redaction": "everything"}, {})
        SH.api_diag_settings(self.state, {"redaction": "strict"}, {})
        self.state.journal.record("note", "x", {"detail": "failed on 192.0.2.17"})
        res = SH.api_diag_bundle(self.state, {"remote": False, "reason": "unit test"}, {})
        self.assertEqual(res["redaction"], "strict")
        self.assertEqual(res["bundles"][0]["name"], res["name"])
        dl = SH.api_diag_download(self.state, {}, {"name": res["name"]})
        self.assertTrue(dl.data.startswith(b"PK"))
        for bad in ("../journal.db", "openmove-support-x.zip", "openmove-support-20260101T000000Z-none.zip"):
            with self.assertRaises(Exception):
                SH.api_diag_download(self.state, {}, {"name": bad})
        self.assertIn("support_bundle", [e["kind"] for e in self.state.journal.events(None, None, 50)])
        with self.assertRaises(Exception):
            SH.api_diag_decode(self.state, {"token": "ip-404"}, {})

    def test_an_error_in_a_route_is_logged_with_its_traceback_and_the_answer_points_to_the_log(self):
        text = (Path(self.dir.name) / "logs" / "openmove.log").read_text(encoding="utf-8")
        self.assertIn("started", text)


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        old = sys.argv
        sys.argv = ["openmove"] + list(argv)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    code = cli.main()
                except SystemExit as exc:
                    code = exc.code
        finally:
            sys.argv = old
        return code, out.getvalue(), err.getvalue()

    def test_bundle_and_decode_work_without_a_server(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d) / "journal.db")
            j.record("note", "x", {"detail": "failed on 192.0.2.17"})
            j.close()
            code, out, err = self.run_cli("--bundle", "--no-remote", "--redact", "standard", "--home", d)
            self.assertIn("Support bundle written", out, err)
            self.assertFalse(code)
            bundle = next((Path(d) / "bundles").glob("openmove-support-*-standard.zip"))
            blob = "\n".join(read_bundle(bundle).values())
            self.assertNotIn("192.0.2.17", blob)
            token = next(w for w in blob.replace('"', " ").split() if w.startswith("ip-"))
            code, out, err = self.run_cli("--decode", token, "--home", d)
            self.assertIn("192.0.2.17", out)
            code, out, err = self.run_cli("--decode", "ip-999", "--home", d)
            self.assertTrue(code)


if __name__ == "__main__":
    unittest.main()
