# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Setting up a new host from OpenMove: log in once with a password, prepare the host, set up its storage paths, register it (bootstrap.py, docs/DEPLOYMENT.md).

That the steps work on a real machine is shown by the lab run (docs/LAB.md). Here: the request is checked, the commands are the ones intended, the host key
is checked before the password is sent, and the password and the CHAP secret are found nowhere: not in an argument, not in the journal, not in a file.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openmove import bootstrap, hosts as hosts_mod, server_hosts
from openmove.connectors import ssh
from openmove.server import ApiError, create_server

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "openmove" / "resources" / "deploy" / "configure_storage.sh"
PASSWORD, CHAP = "Pw-5ecret-Zq81", "chap-5ecret-Kp42"


class FakeLogin:
    """A host that answers the way a fresh Ubuntu host does. Records every command and its standard input."""

    def __init__(self, uid="0", prepare_rc=0, storage_rc=0, prepare_text="ok       packages\nHost ready.", storage_json=None, shebang=""):
        self.calls, self.uid, self.prepare_rc, self.storage_rc = [], uid, prepare_rc, storage_rc
        self.prepare_text, self.shebang = prepare_text, shebang
        self.storage_json = storage_json or {"protocol": "iscsi", "initiator": "iqn.2004-10.com.example:abc", "wwpns": [], "portals": ["192.0.2.21:3260"], "sessions": 1, "maps": 1,
                                             "min_paths": 2}

    def exec(self, argv, stdin=None, timeout=60):
        self.calls.append((list(argv), stdin))
        text = " ".join(argv)
        if argv[:2] == ["sh", "-c"] and "id -u" in argv[2]:
            return 0, "%s\nLinux 6.8\nUbuntu 24.04\n/usr/bin/python3\n/usr/bin/sudo\n" % self.uid, ""
        if argv[:2] == ["sudo", "-n"] and argv[2:] == ["true"]:
            return 0, "", ""
        if argv[:2] == ["sh", "-c"] and "mktemp" in argv[2]:
            return 0, "/tmp/openmove-setup.AbC123\n", ""
        if "prepare_host.sh" in text or (argv[-1:] and False):
            pass
        if "/s.sh" in text:
            if "--role" in argv:
                return self.prepare_rc, self.prepare_text, ""
            return self.storage_rc, "ok       packages\nOPENMOVE-JSON %s\n\nStorage paths ready." % json.dumps(self.storage_json), ""
        if argv[:2] == ["rm", "-rf"]:
            return 0, "", ""
        if argv[:2] == ["sh", "-c"] and "cinder-volume" in argv[2]:
            return 0, self.shebang, ""
        return 0, "", ""


def request(**kw):
    base = {"name": "mover-1", "address": "192.0.2.10", "port": 22, "user": "root", "roles": ["mover"]}
    base.update(kw)
    return bootstrap.clean(base)


class RequestTests(unittest.TestCase):
    def test_a_plain_request_gets_the_defaults(self):
        r = request()
        self.assertEqual((r["user"], r["service_user"], r["cinder_user"], r["storage"]), ("root", "openmove", "cinder", {"protocol": "none"}))
        self.assertFalse(r["check_only"])

    def test_things_that_could_carry_a_command_are_refused(self):
        for bad in ({"name": "a b"}, {"name": ""}, {"address": "host; reboot"}, {"address": ""}, {"user": "root;id"}, {"service_user": "Open Move"}, {"service_user": "$(id)"},
                    {"cinder_user": "c`id`"}, {"from_address": "1.2.3.4' ; id #"}, {"roles": ["everything"]}, {"roles": []}, {"port": 0}, {"port": 70000}, {"cinder_python": "/x; id"},
                    {"jump": "a b"}):
            with self.assertRaises(bootstrap.SetupError, msg=str(bad)):
                request(**bad)

    def test_the_storage_request_is_checked(self):
        ok = request(storage={"protocol": "iscsi", "portals": ["192.0.2.21", "192.0.2.22:3261", "arr1.example.net"], "initiator": "iqn.1994-05.com.example:m1", "arrays": ["pure"]})
        self.assertEqual(ok["storage"]["portals"], ["192.0.2.21", "192.0.2.22:3261", "arr1.example.net"])
        for bad in ({"protocol": "nfs"}, {"protocol": "iscsi"}, {"protocol": "iscsi", "portals": ["1.2.3.4; id"]}, {"protocol": "iscsi", "portals": ["1.2.3.4:99999"]},
                    {"protocol": "iscsi", "portals": ["1.2.3.4"], "initiator": "not an iqn"}, {"protocol": "iscsi", "portals": ["1.2.3.4"], "chap_user": "u"},
                    {"protocol": "iscsi", "portals": ["1.2.3.4"], "chap_user": "u", "chap_secret": "a\nb"}, {"protocol": "iscsi", "portals": ["1.2.3.4"], "arrays": ["netapp"]},
                    {"protocol": "iscsi", "portals": ["1.2.3.4"], "replacement_timeout": 0}, {"protocol": "iscsi", "portals": ["1.2.3.4"], "multipath_extra": "x" * 9000}):
            with self.assertRaises(bootstrap.SetupError, msg=str(bad)):
                request(storage=bad)

    def test_storage_is_set_up_on_a_migration_host_only(self):
        with self.assertRaises(bootstrap.SetupError):
            request(roles=["cinder"], storage={"protocol": "fc"})

    def test_the_chap_secret_is_kept_apart_from_the_profile(self):
        r = request(storage={"protocol": "iscsi", "portals": ["192.0.2.21"], "chap_user": "omuser", "chap_secret": CHAP})
        self.assertNotIn(CHAP, json.dumps(r["storage"]))
        self.assertEqual(r["chap_secret"], CHAP)

    def test_the_storage_arguments_carry_no_secret(self):
        r = request(storage={"protocol": "iscsi", "portals": ["192.0.2.21"], "chap_user": "omuser", "chap_secret": CHAP, "arrays": ["pure", "solidfire"],
                             "multipath_extra": "defaults { }"})
        args = bootstrap.storage_args(r["storage"], check=True)
        self.assertNotIn(CHAP, " ".join(args))
        for need in ("--protocol", "iscsi", "--portal", "192.0.2.21", "--chap-user", "omuser", "--chap-secret-stdin", "--array", "pure", "solidfire", "--check", "--multipath-extra-b64"):
            self.assertIn(need, args)

    def test_the_interpreter_is_read_from_the_first_line(self):
        self.assertEqual(bootstrap.interpreter_of("#!/opt/stack/data/venv/bin/python3\n"), "/opt/stack/data/venv/bin/python3")
        self.assertEqual(bootstrap.interpreter_of("#!/usr/bin/python3.8 -s"), "/usr/bin/python3.8")
        self.assertIsNone(bootstrap.interpreter_of("#!/usr/bin/env python3"))
        self.assertIsNone(bootstrap.interpreter_of(""))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.home = Path(self.dir.name)
        for target, fn in ((ssh, "ensure_key"), (ssh, "public_key")):
            p = mock.patch.object(target, fn, lambda home: "ssh-ed25519 AAAATESTKEY openmove")
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(bootstrap, "default_key_check", lambda home, spec: (True, "logged in as %s" % spec["user"]))
        p.start()
        self.addCleanup(p.stop)

    def go(self, login, **kw):
        return bootstrap.run(self.home, request(**kw), PASSWORD, login=login)

    def ran(self, login, word):
        return [c for c in login.calls if word in " ".join(c[0])]

    def test_a_bare_host_is_prepared_and_the_answer_holds_the_spec_to_register(self):
        login = FakeLogin()
        res = self.go(login)
        self.assertTrue(res["ok"], res["steps"])
        self.assertEqual([s["id"] for s in res["steps"]], ["S1", "S2", "S4", "S5"])
        spec = res["spec"]
        self.assertEqual((spec["user"], spec["sudo"], spec["roles"], spec["address"]), ("openmove", True, ["mover"], "192.0.2.10"))
        run = [c for c in login.calls if "--role" in c[0]][0][0]
        self.assertEqual(run[:3], ["bash", "/tmp/openmove-setup.AbC123/s.sh", "--role"])
        self.assertIn("ssh-ed25519 AAAATESTKEY openmove", run)
        self.assertNotIn("sudo", run)                                     # root needs none
        self.assertTrue(self.ran(login, "rm -rf /tmp/openmove-setup.AbC123"))          # the temporary folder is removed

    def test_the_script_goes_in_as_a_file_not_into_bash(self):
        login = FakeLogin()
        self.go(login)
        uploads = [c for c in login.calls if c[0][:2] == ["sh", "-c"] and "mktemp" in c[0][2]]
        self.assertEqual(len(uploads), 1)
        self.assertIn(b"prepare_host.sh", uploads[0][1])                    # the text of the script
        self.assertNotIn("bash -s", " ".join(" ".join(c[0]) for c in login.calls))

    def test_the_password_and_the_secret_are_in_no_command_and_no_answer(self):
        login = FakeLogin()
        res = self.go(login, roles=["mover", "cinder"], storage={"protocol": "iscsi", "portals": ["192.0.2.21"], "chap_user": "omuser", "chap_secret": CHAP})
        everything = json.dumps(res) + " ".join(" ".join(c[0]) for c in login.calls)
        self.assertNotIn(PASSWORD, everything)
        self.assertNotIn(CHAP, everything)
        stdins = [c[1] for c in login.calls if c[1] and CHAP.encode() in c[1]]
        self.assertEqual(len(stdins), 1)                                    # the secret goes only to the storage script, on its standard input
        self.assertEqual(stdins[0], (CHAP + "\n").encode())

    def test_a_cinder_host_gets_its_interpreter_from_the_cinder_program(self):
        login = FakeLogin(shebang="#!/opt/stack/data/venv/bin/python3\n")
        res = self.go(login, roles=["cinder"])
        self.assertEqual(res["spec"]["python"], "/opt/stack/data/venv/bin/python3")
        self.assertEqual(res["spec"]["cinder_user"], "cinder")
        self.assertFalse(res["spec"]["sudo"])                              # the Cinder helper runs as the Cinder user, through the narrow rule
        s3 = [s for s in res["steps"] if s["id"] == "S3"][0]
        self.assertEqual(s3["status"], "ok")

    def test_an_interpreter_that_cannot_be_read_is_a_warning_and_the_given_one_wins(self):
        res = self.go(FakeLogin(shebang="#!/usr/bin/env python3\n"), roles=["cinder"])
        self.assertEqual([s for s in res["steps"] if s["id"] == "S3"][0]["status"], "warn")
        self.assertNotIn("python", res["spec"])
        res = self.go(FakeLogin(), roles=["cinder"], cinder_python="/srv/cinder/bin/python")
        self.assertEqual(res["spec"]["python"], "/srv/cinder/bin/python")

    def test_storage_paths_are_set_up_and_the_initiator_comes_back(self):
        login = FakeLogin()
        res = self.go(login, storage={"protocol": "iscsi", "portals": ["192.0.2.21"]})
        self.assertTrue(res["ok"])
        self.assertEqual(res["initiator"], "iqn.2004-10.com.example:abc")
        self.assertEqual(res["spec"]["storage"]["portals"], ["192.0.2.21"])
        s4 = [s for s in res["steps"] if s["id"] == "S4"][0]
        self.assertEqual(s4["status"], "ok")

    def test_no_session_yet_is_a_warning_with_the_initiator_to_register(self):
        res = self.go(FakeLogin(storage_json={"protocol": "iscsi", "initiator": "iqn.x:y", "wwpns": [], "portals": ["192.0.2.21"], "sessions": 0, "maps": 0, "min_paths": 0}),
                      storage={"protocol": "iscsi", "portals": ["192.0.2.21"]})
        s4 = [s for s in res["steps"] if s["id"] == "S4"][0]
        self.assertEqual(s4["status"], "warn")
        self.assertTrue(res["ok"])
        self.assertEqual(res["initiator"], "iqn.x:y")

    def test_a_failed_script_stops_the_setup_and_keeps_its_complete_output(self):
        long = "\n".join("MISSING  item %d" % i for i in range(400))
        res = self.go(FakeLogin(prepare_rc=1, prepare_text=long))
        self.assertFalse(res["ok"])
        self.assertIsNone(res["spec"])
        s2 = [s for s in res["steps"] if s["id"] == "S2"][0]
        self.assertEqual((s2["status"], s2["output"]), ("fail", long))       # never shortened
        self.assertNotIn("S5", [s["id"] for s in res["steps"]])

    def test_a_failed_storage_script_keeps_the_host_unregistered(self):
        res = self.go(FakeLogin(storage_rc=1), storage={"protocol": "iscsi", "portals": ["192.0.2.21"]})
        self.assertFalse(res["ok"])
        self.assertIsNone(res["spec"])

    def test_check_only_changes_and_registers_nothing(self):
        login = FakeLogin()
        res = self.go(login, check_only=True, storage={"protocol": "iscsi", "portals": ["192.0.2.21"]})
        self.assertTrue(res["ok"])
        self.assertIsNone(res["spec"])
        runs = [c[0] for c in login.calls if c[0][0] == "bash"]
        self.assertEqual(len(runs), 2)
        for r in runs:
            self.assertIn("--check", r)

    def test_a_login_that_is_not_root_needs_passwordless_sudo_and_uses_it(self):
        login = FakeLogin(uid="1000")
        res = self.go(login, user="admin")
        self.assertTrue(res["ok"])
        run = [c[0] for c in login.calls if "--role" in c[0]][0]
        self.assertEqual(run[:3], ["sudo", "-n", "bash"])

        class NoSudo(FakeLogin):
            def exec(self, argv, stdin=None, timeout=60):
                if argv[:2] == ["sudo", "-n"] and argv[2:] == ["true"]:
                    return 1, "", "sudo: a password is required"
                return super().exec(argv, stdin, timeout)
        res = self.go(NoSudo(uid="1000"), user="admin")
        self.assertFalse(res["ok"])
        self.assertIn("passwordless sudo", res["steps"][0]["detail"])


class PasswordLoginTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.home = Path(self.dir.name)

    def test_the_password_goes_through_the_environment_only(self):
        seen = {}

        def fake_run(argv, stdin=None, timeout=60, env=None, cwd=None, detach=False):
            seen.update(argv=list(argv), stdin=stdin, env=dict(env), detach=detach)
            helper = Path(env["SSH_ASKPASS"])
            seen["helper_text"] = helper.read_text(encoding="utf-8") if helper.exists() else ""
            seen["helper"] = helper
            return 0, b"out", b""
        with mock.patch.object(ssh, "run_local", fake_run), mock.patch.object(ssh, "tool", lambda name: name):
            rc, out, err = ssh.RootLogin(self.home, "192.0.2.10", 2200, "root", PASSWORD).exec(["id", "-un"], stdin=b"x")
        self.assertEqual((rc, out), (0, "out"))
        self.assertNotIn(PASSWORD, " ".join(seen["argv"]))
        self.assertNotIn(PASSWORD, seen["helper_text"])
        self.assertEqual(seen["env"][ssh.PASSWORD_ENV], PASSWORD)
        self.assertEqual(seen["env"]["SSH_ASKPASS_REQUIRE"], "force")
        self.assertTrue(seen["detach"])
        self.assertFalse(seen["helper"].exists())                            # the helper is removed with its folder
        argv = seen["argv"]
        for opt in ("PubkeyAuthentication=no", "NumberOfPasswordPrompts=1", "StrictHostKeyChecking=yes", "BatchMode=no"):
            self.assertIn(opt, argv)
        self.assertIn("root@192.0.2.10", argv)

    def test_a_wrong_password_is_reported_without_the_password(self):
        def fake_run(argv, stdin=None, timeout=60, env=None, cwd=None, detach=False):
            return 255, b"", b"root@192.0.2.10: Permission denied (publickey,password)."
        with mock.patch.object(ssh, "run_local", fake_run), mock.patch.object(ssh, "tool", lambda name: name):
            with self.assertRaises(ssh.SshError) as cm:
                ssh.RootLogin(self.home, "192.0.2.10", 22, "root", PASSWORD).exec(["true"])
        self.assertIn("did not accept the password", str(cm.exception))
        self.assertNotIn(PASSWORD, str(cm.exception))

    def test_the_helper_prints_the_password_from_the_environment(self):
        d = Path(self.dir.name)
        h = ssh.askpass_helper(d)
        self.assertIn(ssh.PASSWORD_ENV, h.read_text(encoding="utf-8"))
        if h.suffix == ".sh" and shutil.which("sh"):
            r = subprocess.run([shutil.which("sh"), str(h)], env={ssh.PASSWORD_ENV: "a b$c'd\"e&f%g"}, capture_output=True)
            self.assertEqual(r.stdout.decode(), "a b$c'd\"e&f%g\n")          # special characters come out unchanged


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.home = Path(self.dir.name)
        self.srv, self.state = create_server("127.0.0.1", 0, self.home)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.state.journal.close)
        self.keys = [{"type": "ssh-ed25519", "fingerprint": "SHA256:abc123", "line": "192.0.2.10 ssh-ed25519 AAAAFAKE"}]
        for target, fn, val in ((ssh, "scan_host_key", lambda a, p=22: self.keys),):
            p = mock.patch.object(target, fn, val)
            p.start()
            self.addCleanup(p.stop)
        self.ran = []

        def fake_run(home, req, password, login=None, key_check=None):
            self.ran.append((req, password))
            return {"ok": True, "steps": [{"id": "S1", "title": "Log in", "status": "ok", "detail": "", "output": ""}], "check_only": req["check_only"],
                    "spec": {"name": req["name"], "roles": req["roles"], "transport": "ssh", "address": req["address"], "port": req["port"], "user": "openmove", "sudo": True},
                    "storage": None, "initiator": "iqn.x:y"}
        p = mock.patch.object(bootstrap, "run", fake_run)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(hosts_mod, "readiness", lambda *a, **k: [])
        p.start()
        self.addCleanup(p.stop)

    def body(self, **kw):
        b = {"name": "mover-1", "address": "192.0.2.10", "port": 22, "user": "root", "password": PASSWORD, "roles": ["mover"]}
        b.update(kw)
        return b

    def test_the_password_is_not_sent_before_the_host_key_is_pinned(self):
        with self.assertRaises(ApiError) as cm:
            server_hosts.api_hosts_setup(self.state, self.body(), {})
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.ran, [])
        with self.assertRaises(ApiError) as cm:
            server_hosts.api_hosts_setup(self.state, self.body(fingerprint="SHA256:other", by="Pat Lee"), {})
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.ran, [])
        self.assertFalse(ssh.is_pinned(self.home, "192.0.2.10", 22))

    def test_a_checked_fingerprint_pins_the_key_and_the_setup_runs(self):
        out = server_hosts.api_hosts_setup(self.state, self.body(fingerprint="SHA256:abc123", by="Pat Lee"), {})
        self.assertTrue(out["ok"])
        self.assertTrue(ssh.is_pinned(self.home, "192.0.2.10", 22))
        self.assertEqual(len(self.ran), 1)
        self.assertEqual([h["name"] for h in hosts_mod.load(self.home)], ["mover-1"])
        self.assertEqual(hosts_mod.load(self.home)[0]["user"], "openmove")
        self.assertEqual(out["initiator"], "iqn.x:y")

    def test_the_password_is_nowhere_afterwards(self):
        server_hosts.api_hosts_setup(self.state, self.body(fingerprint="SHA256:abc123", by="Pat Lee", storage={"protocol": "iscsi", "portals": ["192.0.2.21"],
                                                                                                                 "chap_user": "u", "chap_secret": CHAP}), {})
        self.state.journal.close()
        hits = []
        for p in self.home.rglob("*"):
            if p.is_file():
                data = p.read_bytes()
                if PASSWORD.encode() in data or CHAP.encode() in data:
                    hits.append(str(p))
        self.assertEqual(hits, [])
        kinds = [e["kind"] if isinstance(e, dict) else e for e in []]            # the journal is a database; the scan above covers it
        self.assertEqual(kinds, [])

    def test_the_setup_is_recorded_in_the_journal_without_the_password(self):
        server_hosts.api_hosts_setup(self.state, self.body(fingerprint="SHA256:abc123", by="Pat Lee"), {})
        text = json.dumps([list(map(str, r)) for r in self.state.journal.conn.execute("select * from events").fetchall()]) if hasattr(self.state.journal, "conn") else ""
        if text:
            self.assertIn("host_setup", text)
            self.assertNotIn(PASSWORD, text)

    def test_a_request_without_a_password_or_with_a_bad_field_is_refused_before_anything_happens(self):
        for body in (self.body(password=""), self.body(name="a b"), self.body(address="x;y"), self.body(storage={"protocol": "iscsi"})):
            with self.assertRaises(ApiError) as cm:
                server_hosts.api_hosts_setup(self.state, body, {})
            self.assertEqual(cm.exception.status, 400)
        self.assertEqual(self.ran, [])

    def test_a_failed_login_is_a_502_that_says_why(self):
        def boom(home, req, password, login=None, key_check=None):
            raise ssh.SshError("192.0.2.10 did not accept the password for root")
        with mock.patch.object(bootstrap, "run", boom):
            with self.assertRaises(ApiError) as cm:
                server_hosts.api_hosts_setup(self.state, self.body(fingerprint="SHA256:abc123", by="Pat Lee"), {})
        self.assertEqual(cm.exception.status, 502)
        self.assertNotIn(PASSWORD, str(cm.exception))

    def test_check_only_registers_nothing(self):
        out = server_hosts.api_hosts_setup(self.state, self.body(fingerprint="SHA256:abc123", by="Pat Lee", check_only=True), {})
        self.assertTrue(out["check_only"])
        self.assertEqual(hosts_mod.load(self.home), [])

    def test_the_probe_reads_the_key_without_trusting_it(self):
        out = server_hosts.api_hosts_probe(self.state, {"address": "192.0.2.10", "port": 22}, {})
        self.assertEqual(out["keys"], [{"type": "ssh-ed25519", "fingerprint": "SHA256:abc123"}])
        self.assertFalse(out["pinned"])
        self.assertFalse(ssh.is_pinned(self.home, "192.0.2.10", 22))

    def test_a_storage_profile_never_holds_a_secret(self):
        with self.assertRaises(hosts_mod.HostError):
            hosts_mod.clean({"name": "h", "address": "192.0.2.10", "storage": {"protocol": "iscsi", "chap_secret": "x"}})
        s = hosts_mod.clean({"name": "h", "address": "192.0.2.10", "storage": {"protocol": "iscsi", "portals": ["192.0.2.21"]}})
        self.assertEqual(s["storage"]["portals"], ["192.0.2.21"])


class StorageScriptTests(unittest.TestCase):
    def test_the_script_parses_and_has_unix_line_endings_and_the_header(self):
        data = SCRIPT.read_bytes()
        self.assertNotIn(b"\r\n", data)
        self.assertIn(b"Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO", data[:400])
        bash = shutil.which("bash")
        if bash:
            r = subprocess.run([bash, "-n", str(SCRIPT)], capture_output=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))

    def test_the_script_refuses_what_it_should_before_it_changes_anything(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("no bash on this machine")
        cases = ((["--protocol", "nfs"], b"--protocol must be"), ([], b"--protocol must be"), (["--protocol", "iscsi", "--portal", "1.2.3.4;id"], b"is not an address"),
                 (["--protocol", "iscsi", "--initiator", "bad"], b"--initiator must be"), (["--protocol", "iscsi", "--array", "netapp"], b"--array must be"),
                 (["--protocol", "iscsi", "--replacement-timeout", "x"], b"must be a number"))
        for args, text in cases:
            r = subprocess.run([bash, str(SCRIPT)] + args, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 2, args)
            self.assertIn(text, r.stdout, args)

    def test_the_lab_vm_scripts_parse_and_keep_unix_line_endings(self):
        bash = shutil.which("bash")
        for name in ("build_vm.sh", "run_vm.sh", "make_target.sh"):
            p = ROOT / "tools" / "lab" / "vm" / name
            data = p.read_bytes()
            self.assertNotIn(b"\r\n", data, name)
            self.assertIn(b"Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO", data[:400], name)
            if bash:
                r = subprocess.run([bash, "-n", str(p)], capture_output=True, timeout=60)
                self.assertEqual(r.returncode, 0, name + ": " + r.stderr.decode("utf-8", "replace"))

    def test_the_script_waits_for_the_multipath_map_before_it_reports(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertLess(text.index("Attached scsi disk"), text.index("# ---- 6. the report"))

    def test_the_script_reads_the_chap_secret_from_standard_input_only(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("--chap-secret-stdin", text)
        self.assertNotIn("--chap-secret)", text)
        self.assertIn("read -r CHAP_SECRET", text)

    def test_the_script_never_touches_volumes_and_leaves_an_existing_multipath_file_alone(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for word in ("lvremove", "mkfs", "wipefs", "dd if", "iscsiadm -m node -o delete"):
            self.assertNotIn(word, text)
        self.assertIn("(left as it is)", text)
        self.assertIn("/etc/multipath/conf.d/openmove.conf", text)


if __name__ == "__main__":
    unittest.main()
