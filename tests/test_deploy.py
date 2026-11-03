# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The deployment: what an installation must hold for class A, B and C (selfcheck.py), the host setup script and the route and command that hand it out
(docs/DEPLOYMENT.md). That the script prepares a real host is shown by the lab run (docs/LAB.md); here the text, the checks and the refusals are tested."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openmove import selfcheck, server_hosts
from openmove.connectors import ssh
from openmove.server import create_server

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "openmove" / "resources" / "deploy" / "prepare_host.sh"


class Home(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.home = Path(self.dir.name)


class SelfCheckTests(Home):
    def test_a_complete_installation_has_nothing_failed(self):
        rows = selfcheck.run(self.home)
        self.assertEqual([r for r in rows if r["status"] == "FAIL" and not r["name"].startswith("SSH client")], [])
        names = {r["name"] for r in rows}
        for need in ("File mover/mover.py", "File mover/cinder_helper.py", "File deploy/prepare_host.sh", "Syntax of mover/mover.py", "Data folder"):
            self.assertIn(need, names)

    def test_the_mover_and_the_helper_must_parse_as_python_3_6(self):
        rows = {r["name"]: r for r in selfcheck.run(self.home)}
        self.assertEqual(rows["Syntax of mover/mover.py"]["status"], "ok")
        self.assertEqual(rows["Syntax of mover/cinder_helper.py"]["status"], "ok")

    def test_a_missing_data_mover_is_a_failure_that_names_the_class(self):
        copy = Path(self.dir.name) / "pkg"
        shutil.copytree(ROOT / "openmove" / "resources", copy / "resources")
        (copy / "resources" / "mover" / "mover.py").unlink()
        with mock.patch.object(selfcheck, "PACKAGE", copy):
            rows = {r["name"]: r for r in selfcheck.run(self.home)}
        self.assertEqual(rows["File mover/mover.py"]["status"], "FAIL")
        self.assertIn("class C", rows["File mover/mover.py"]["detail"])

    def test_a_missing_ssh_client_is_a_failure(self):
        with mock.patch.object(selfcheck.shutil, "which", lambda name: None):
            rows = {r["name"]: r for r in selfcheck.run(self.home)}
        self.assertEqual(rows["SSH client: ssh"]["status"], "FAIL")

    def test_no_hosts_is_a_warning_not_a_failure(self):
        rows = {r["name"]: r for r in selfcheck.run(self.home)}
        self.assertEqual(rows["Hosts for class C"]["status"], "WARN")
        self.assertIn("class A and B work without them", rows["Hosts for class C"]["detail"])


class SetupCommandTests(Home):
    def setUp(self):
        super().setUp()
        self.key = "ssh-ed25519 AAAATESTKEY openmove"
        patcher = mock.patch.object(ssh, "ensure_key", lambda home: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        p2 = mock.patch.object(ssh, "public_key", lambda home: self.key)
        p2.start()
        self.addCleanup(p2.stop)

    def test_the_command_carries_the_key_and_the_role(self):
        cmd = server_hosts.setup_command(self.home, "mover")
        self.assertIn("--role mover", cmd)
        self.assertIn("--key '%s'" % self.key, cmd)
        self.assertNotIn("--from", cmd)

    def test_the_source_address_is_passed_when_given(self):
        self.assertIn("--from '192.0.2.5'", server_hosts.setup_command(self.home, "both", source_address="192.0.2.5"))

    def test_a_role_or_account_that_could_carry_a_command_is_refused(self):
        for bad in ({"role": "mover; reboot"}, {"user": "a b"}, {"user": "x;rm"}, {"user": "$(id)"}, {"cinder_user": "root`id`"}, {"source_address": "1.2.3.4' ; id #"}):
            kw = {"role": "both", "user": "openmove", "cinder_user": "cinder", "source_address": ""}
            kw.update(bad)
            with self.assertRaises(ValueError, msg=str(bad)):
                server_hosts.setup_command(self.home, **kw)

    def test_the_route_returns_the_script_and_the_command_and_changes_nothing(self):
        srv, state = create_server("127.0.0.1", 0, self.home)
        self.addCleanup(srv.server_close)
        self.addCleanup(state.journal.close)
        before = state.journal.count() if hasattr(state.journal, "count") else None
        out = server_hosts.api_hosts_setup_script(state, {}, {"role": "cinder", "user": "omove"})
        self.assertEqual(out["filename"], "prepare_host.sh")
        self.assertTrue(out["script"].startswith("#!/bin/bash"))
        self.assertIn("--user omove", out["command"])
        if before is not None:
            self.assertEqual(state.journal.count(), before)

    def test_the_route_refuses_a_bad_role_with_a_400(self):
        srv, state = create_server("127.0.0.1", 0, self.home)
        self.addCleanup(srv.server_close)
        self.addCleanup(state.journal.close)
        with self.assertRaises(Exception) as cm:
            server_hosts.api_hosts_setup_script(state, {}, {"role": "everything"})
        self.assertEqual(getattr(cm.exception, "status", 400), 400)


class ScriptTests(unittest.TestCase):
    def test_the_script_parses_in_bash(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("no bash on this machine")
        r = subprocess.run([bash, "-n", str(SCRIPT)], capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))

    def test_the_script_has_unix_line_endings_and_the_copyright_header(self):
        data = SCRIPT.read_bytes()
        self.assertNotIn(b"\r\n", data)
        self.assertIn(b"Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO", data[:400])

    def test_the_script_asks_for_a_role_and_refuses_without_one(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("no bash on this machine")
        r = subprocess.run([bash, str(SCRIPT)], capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"--role must be", r.stdout)

    def test_the_script_grants_no_blanket_root_to_a_mover(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("(root) NOPASSWD: /usr/bin/env OPENMOVE_MOVER_HOME=*", text)
        self.assertIn("ALL=($CINDER_USER) NOPASSWD: ALL", text)          # only as the Cinder service user, never as root

    def test_the_container_image_has_the_ssh_client_and_the_packaged_program_has_the_class_c_modules(self):
        self.assertIn("openssh-client", (ROOT / "Dockerfile").read_text(encoding="utf-8"))
        build = (ROOT / "tools" / "build" / "build_exe.py").read_text(encoding="utf-8")
        for mod in ("openmove.engine.staged", "openmove.connectors.ssh", "openmove.arrays.lvm", "openmove.selfcheck", "openmove.server_hosts", "openmove.bootstrap"):
            self.assertIn('"%s"' % mod, build)


if __name__ == "__main__":
    unittest.main()
