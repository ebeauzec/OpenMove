# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The lab on a Linux host (docs/LAB.md, "The lab on a Linux host"): the choice of backend, the addresses of the machines, the ssh commands built to reach
them (the lab machine and the migration host inside it), the stages of `lab.py deploy` with every call to the machines stubbed, and the syntax and line
endings of the shell scripts. That the machines really come up is shown by a run on a Linux host, not here."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openmove import lab
from openmove.lab import hosts as lh

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ["openmove-lab.sh", "tools/lab/provision_devstack.sh", "tools/lab/lab_up.sh", "tools/lab/lab_down.sh", "tools/lab/linux/labvm.sh",
           "tools/lab/linux/mover_chroot.sh", "tools/lab/vm/make_target.sh"]


def load_tool():
    spec = importlib.util.spec_from_file_location("lab_tool", str(ROOT / "tools" / "lab" / "lab.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LinuxBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        for key, value in (("OPENMOVE_LAB_DIR", self.dir.name), ("OPENMOVE_LAB_BACKEND", "linux")):
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)
        self.path = Path(self.dir.name)


class BackendTests(LinuxBase):
    def test_the_backend_is_chosen_by_the_system_and_can_be_forced(self):
        os.environ.pop("OPENMOVE_LAB_BACKEND")
        self.assertEqual(lab.backend(), "wsl" if os.name == "nt" else "linux")
        os.environ["OPENMOVE_LAB_BACKEND"] = "wsl"
        self.assertEqual(lab.backend(), "wsl")
        os.environ["OPENMOVE_LAB_BACKEND"] = "nonsense"
        self.assertEqual(lab.backend(), "wsl" if os.name == "nt" else "linux")

    def test_the_machines_have_fixed_addresses_and_ports(self):
        self.assertEqual(lab.linux_target("openmove-lab"), ("192.168.221.10", 22))
        self.assertEqual(lab.linux_target("openmove-mover"), ("192.168.221.10", 2202))
        self.assertEqual(lab.linux_target("openmove-fresh"), ("192.168.221.12", 22))
        self.assertEqual(lab.linux_target("openmove-vmhost"), ("192.168.221.13", 22))
        with self.assertRaises(lab.LabError):
            lab.linux_target("somewhere-else")

    def test_the_subnet_can_be_changed_in_the_lab_file(self):
        (self.path / "lab.json").write_text(json.dumps({"auth_url": "http://x/identity", "linux_subnet": "198.51.100"}), encoding="utf-8")
        self.assertEqual(lab.linux_target("openmove-lab")[0], "198.51.100.10")

    def test_without_a_lab_file_the_cloud_is_the_lab_machine(self):
        (self.path / "lab-secret.json").write_text(json.dumps({"password": "unused-test-value"}), encoding="utf-8")
        self.assertEqual(lab.load_config()["auth_url"], "http://192.168.221.10/identity")

    def test_the_lab_key_is_in_the_lab_folder(self):
        self.assertEqual(lab.lab_key(), self.path / "ssh" / "lab_ed25519")


class SshTests(LinuxBase):
    def capture(self):
        calls = []

        def local(argv, stdin=None, timeout=600):
            calls.append((argv, stdin))
            return 0, b"out\n", b""
        return calls, mock.patch.object(lh, "local", local)

    def test_a_command_in_the_lab_machine_goes_over_ssh_with_the_lab_key(self):
        calls, patch = self.capture()
        with patch:
            rc, out, err = lh.run("openmove-lab", "echo hello", ["a b", "c"])
        argv, stdin = calls[0]
        self.assertEqual((rc, out), (0, "out\n"))
        self.assertEqual(stdin, b"echo hello")
        self.assertIn(str(lab.lab_key()), argv)
        self.assertEqual(argv[argv.index("-p") + 1], "22")
        self.assertIn("root@192.168.221.10", argv)
        self.assertEqual(argv[-5:], ["bash", "-s", "--", "'a b'", "c"])
        self.assertIn("BatchMode=yes", argv)

    def test_a_command_in_the_migration_host_enters_its_root_file_system(self):
        calls, patch = self.capture()
        with patch:
            lh.run("openmove-mover", "ls /", ["x"])
        argv, stdin = calls[0]
        self.assertIn("root@192.168.221.10", argv)                      # the machine that holds the migration host, on its own sshd
        self.assertEqual(argv[argv.index("-p") + 1], "22")
        text = " ".join(argv)
        self.assertIn("nsenter -t", text)
        self.assertIn("openmove-mover", text)
        self.assertEqual(stdin, b"ls /")

    def test_the_wsl_program_is_not_used_on_a_linux_host(self):
        calls, patch = self.capture()
        with patch, mock.patch.object(lh, "wsl_exe", side_effect=AssertionError("wsl must not be called")):
            lh.run("openmove-lab", "true")
            self.assertEqual(lh.address("openmove-fresh"), "192.168.221.12")

    def test_machines_are_known_by_their_disk_and_the_migration_host_by_its_mark(self):
        self.assertEqual(lh.distros(), set())
        (self.path / "vm" / "lab").mkdir(parents=True)
        (self.path / "vm" / "lab" / "disk.img").write_bytes(b"")
        self.assertEqual(lh.distros(), {"openmove-lab"})
        (self.path / "vm" / "lab" / lh.MOVER_MARK).write_text("x", encoding="utf-8")
        self.assertEqual(lh.distros(), {"openmove-lab", "openmove-mover"})

    def test_a_stopped_machine_is_started_and_waited_for(self):
        calls = []

        def labvm(*args, check=True):
            calls.append(args)
            return 1 if args[0] == "status" else 0
        with mock.patch.object(lh, "labvm", labvm):
            self.assertTrue(lh.keep_running("openmove-mover"))      # the migration host runs inside the lab machine
        self.assertEqual(calls, [("status", "lab"), ("start", "lab"), ("wait", "lab", "300")])
        with mock.patch.object(lh, "labvm", lambda *a, check=True: 0):
            self.assertFalse(lh.keep_running("openmove-fresh"))

    def test_the_migration_host_is_made_once(self):
        (self.path / "vm" / "lab").mkdir(parents=True)
        (self.path / "vm" / "lab" / "disk.img").write_bytes(b"")
        ran = []

        def linux_run(machine, script, args=(), timeout=600):
            ran.append((machine, tuple(args)))
            return 0, "migration host ready on 2202\n", ""
        with mock.patch.object(lh, "linux_run", linux_run), mock.patch.object(lh, "keep_running", lambda d: False):
            self.assertTrue(lh.ensure_distro("openmove-mover", "mover"))
            self.assertFalse(lh.ensure_distro("openmove-mover", "mover"))
        self.assertEqual(ran, [("openmove-lab", ("create",))])

    def test_the_bare_host_is_ready_without_the_wsl_setup(self):
        (self.path / "lab-secret.json").write_text(json.dumps({"password": "unused-test-value"}), encoding="utf-8")
        with mock.patch.object(lh, "ensure_distro", lambda n, f: True), mock.patch.object(lh, "keep_running", lambda d: True), \
                mock.patch.object(lh, "run", side_effect=AssertionError("nothing is run in a bare host on a Linux host")):
            r = lh.ensure_fresh_host()
        self.assertEqual(r, {"address": "192.168.221.12", "port": 22, "user": "root", "made": True})


class DeployTests(LinuxBase):
    def setUp(self):
        super().setUp()
        self.tool = load_tool()
        self.calls = []
        outer = self

        class Fake:
            def labvm(self, *args, check=True):
                outer.calls.append(("labvm",) + args)
                return 0

            def distros(self):
                return set(outer.machines)

            def keep_running(self, d):
                outer.calls.append(("keep_running", d))
                return False

            def address(self, d):
                return lab.linux_target(d)[0]

            def run(self, distro, script, args=(), timeout=600):
                if "completed in" in script:                 # the state of DevStack
                    return 0, outer.states.pop(0) + "\n", ""
                outer.calls.append(("provision", "LAB_PASSWORD" in script))
                return 0, "stack.sh started\n", ""

            def ensure_mover_distro(self, cfg):
                outer.calls.append(("mover",))
                return True
        self.machines = set()
        self.states = ["none", "running", "done"]
        patches = [mock.patch.object(self.tool, "hosts_mod", lambda: Fake()), mock.patch.object(self.tool.lab, "reachable", lambda url, **k: True),
                   mock.patch.object(self.tool, "seed_cmd", lambda c: self.calls.append(("seed", c))), mock.patch.object(self.tool.time, "sleep", lambda s: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_the_stages_run_in_order_and_nothing_secret_is_printed(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.tool.deploy(["--install-prereqs"])
        self.assertEqual(rc, 0)
        kinds = [c[:2] if c[0] == "labvm" else c[:1] for c in self.calls]
        self.assertEqual(kinds, [("labvm", "prereqs"), ("labvm", "net"), ("labvm", "build"), ("keep_running",), ("provision",), ("mover",), ("seed",), ("seed",)])
        self.assertEqual(self.calls[0], ("labvm", "prereqs", "--install"))
        self.assertEqual(self.calls[4], ("provision", True))
        secret = json.loads((self.path / "lab-secret.json").read_text(encoding="utf-8"))["password"]
        self.assertGreaterEqual(len(secret), 12)
        self.assertNotIn(secret, buf.getvalue())
        cfg = json.loads((self.path / "lab.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["auth_url"], "http://192.168.221.10/identity")
        self.assertEqual([c[1] for c in self.calls if c[0] == "seed"], ["seed", "more"])

    def test_a_finished_devstack_is_not_installed_again(self):
        self.machines = {"openmove-lab"}
        self.states = ["done", "done"]
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            rc = self.tool.deploy(["--no-seed"])
        self.assertEqual(rc, 0)
        self.assertNotIn(("provision", True), self.calls)
        self.assertFalse([c for c in self.calls if c[0] == "seed"])
        self.assertNotIn(("labvm", "build", "lab"), self.calls)

    def test_a_failed_devstack_is_reported(self):
        self.states = ["none", "failed"]
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.tool.deploy(["--no-seed"])
        self.assertEqual(rc, 1)
        self.assertIn("DevStack failed", buf.getvalue())

    def test_destroy_needs_an_explicit_yes(self):
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            self.assertEqual(self.tool.destroy([]), 2)
        self.assertFalse(self.calls)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(self.tool.destroy(["--yes"]), 0)
        self.assertEqual([c[1:3] for c in self.calls if c[1] == "destroy"], [("destroy", "vmhost"), ("destroy", "fresh"), ("destroy", "lab")])

    def test_deploy_refuses_on_windows(self):
        import io
        from contextlib import redirect_stdout
        os.environ["OPENMOVE_LAB_BACKEND"] = "wsl"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(self.tool.deploy([]), 2)
            self.assertEqual(self.tool.destroy(["--yes"]), 2)
        self.assertFalse(self.calls)


class GuestStartTests(unittest.TestCase):
    """After a restart a start request can be lost (Nova keeps the task "powering-on", Horizon says "starting"): lab.py up must notice and start the guest again."""

    def make_cloud(self, lose_first):
        outer = self

        class Cloud:
            def __init__(self, cfg):
                outer.log = []
                outer.servers = {"a": {"id": "a", "name": "lab-srv-1", "status": "SHUTOFF", "OS-EXT-STS:task_state": "powering-on", "OS-EXT-STS:power_state": 4, "updated": "2020-01-01T00:00:00Z"},
                                 "b": {"id": "b", "name": "lab-srv-off-1", "status": "SHUTOFF", "OS-EXT-STS:task_state": None, "OS-EXT-STS:power_state": 4, "updated": "2020-01-01T00:00:00Z"}}

            def call(self, method, service, path, body=None, soft=False):
                if path.startswith("/os-services"):
                    return {"services": [{"state": "up", "status": "enabled"}]}
                if path.startswith("/servers/detail"):
                    return {"servers": [dict(v) for v in outer.servers.values()]}
                if path.startswith("/servers/") and body is not None or (body is None and path.endswith("/action")):
                    sid = path.split("/")[2]
                    outer.log.append((sid, sorted(body)[0] if body else "os-start"))
                    sv = outer.servers[sid]
                    if body and "os-resetState" in body:
                        sv["OS-EXT-STS:task_state"] = None
                        sv["status"] = "ACTIVE"
                    elif not sv["OS-EXT-STS:task_state"]:
                        sv["status"], sv["OS-EXT-STS:power_state"] = "ACTIVE", 1
                    return {}
                if path.startswith("/servers/"):
                    return {"server": dict(outer.servers[path.split("/")[2]])}
                raise AssertionError(path)
        return Cloud

    def test_a_guest_stuck_in_a_task_is_reset_and_started_again(self):
        import io
        import sys
        from contextlib import redirect_stdout
        tool = load_tool()
        sys.path.insert(0, str(ROOT / "tools" / "lab"))
        import seed
        os.environ["OPENMOVE_LAB_DIR"] = tempfile.mkdtemp()
        self.addCleanup(os.environ.pop, "OPENMOVE_LAB_DIR", None)
        (Path(os.environ["OPENMOVE_LAB_DIR"]) / "lab-secret.json").write_text(json.dumps({"password": "unused-test-value"}), encoding="utf-8")
        (Path(os.environ["OPENMOVE_LAB_DIR"]) / "lab.json").write_text(json.dumps({"auth_url": "http://127.0.0.1:1/identity"}), encoding="utf-8")
        with mock.patch.object(seed, "Cloud", self.make_cloud(True)), mock.patch.object(tool.time, "sleep", lambda s: None), redirect_stdout(io.StringIO()) as out:
            rc = tool.ensure_guests()
        self.assertEqual(rc, 0, out.getvalue())
        self.assertEqual(self.log[0][0], "a")
        self.assertEqual([k for sid, k in self.log if sid == "a"][:2], ["os-resetState", "reboot"])
        self.assertNotIn("b", [sid for sid, k in self.log])                 # a guest that the catalogue keeps stopped is left alone
        self.assertEqual(self.servers["a"]["status"], "ACTIVE")


class ScriptTests(unittest.TestCase):
    def test_the_scripts_have_unix_line_endings_and_the_copyright_notice(self):
        for name in SCRIPTS:
            data = (ROOT / name).read_bytes()
            self.assertNotIn(b"\r", data, name)
            self.assertIn(b"Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO", data[:400], name)

    @unittest.skipUnless(shutil.which("bash"), "bash is needed to check the syntax")
    def test_the_scripts_parse(self):
        for name in SCRIPTS:
            r = subprocess.run(["bash", "-n"], input=(ROOT / name).read_bytes(), capture_output=True)
            self.assertEqual(r.returncode, 0, "%s: %s" % (name, r.stderr.decode("utf-8", "replace")))


if __name__ == "__main__":
    unittest.main()
