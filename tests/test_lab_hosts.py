# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The lab's hosts (openmove/lab/hosts.py) with every call to WSL and to SSH stubbed: the order of what is done, the refusals, and that the
host keys are pinned and the journal says that no person compared them. That the real distros come up is shown by the live lab run (docs/LAB.md)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openmove import hosts as hosts_mod
from openmove import lab
from openmove.lab import hosts as lh


class Journal:
    def __init__(self):
        self.events = []

    def record(self, kind, subject, data):
        self.events.append((kind, subject, data))


class State:
    def __init__(self, home):
        self.home = Path(home)
        self.journal = Journal()
        self.platform = None


class LabHostsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        os.environ["OPENMOVE_LAB_DIR"] = self.dir.name
        self.addCleanup(os.environ.pop, "OPENMOVE_LAB_DIR", None)
        (Path(self.dir.name) / "lab.json").write_text(json.dumps({"auth_url": "http://127.0.0.1:1", "wsl_distro": "lab-one"}), encoding="utf-8")
        (Path(self.dir.name) / "lab-secret.json").write_text(json.dumps({"password": "unused-test-value"}), encoding="utf-8")
        self.home = Path(self.dir.name) / "home"
        self.home.mkdir()
        self.state = State(self.home)
        self.pinned = set()
        self.ran = []

    def fake_run(self, distro, script, args=(), timeout=600):
        self.ran.append((distro, tuple(args)))
        if "hostname -I" in script:
            return 0, "192.0.2.10\n", ""
        if "sleep infinity" in script:
            return 0, "yes\n", ""
        return 0, "sshd ready on %s\n" % (args[0] if args else "?"), ""

    def patches(self, existing=("lab-one", lh.MOVER_DISTRO)):
        return [
            mock.patch.object(lh, "run", self.fake_run),
            mock.patch.object(lh, "distros", lambda: set(existing)),
            mock.patch.object(lh.ssh, "ensure_key", lambda home: None),
            mock.patch.object(lh.ssh, "public_key", lambda home: "ssh-ed25519 AAAA test"),
            mock.patch.object(lh.ssh, "is_pinned", lambda home, a, p: (a, p) in self.pinned),
            mock.patch.object(lh.ssh, "scan_host_key", lambda a, p: [{"line": "[%s]:%d ssh-ed25519 K" % (a, p), "fingerprint": "SHA256:x", "type": "ssh-ed25519"}]),
            mock.patch.object(lh.ssh, "pin_host_key", lambda home, line: self.pinned.add(self._key(line))),
        ]

    @staticmethod
    def _key(line):
        host = line.split(" ")[0].strip("[]")
        a, p = host.split("]:")
        return (a, int(p))

    def prepare(self, **kw):
        from contextlib import ExitStack
        with ExitStack() as st:
            for p in self.patches(**kw):
                st.enter_context(p)
            return lh.prepare(self.state)

    def test_both_hosts_are_set_up_registered_and_pinned(self):
        res = self.prepare()
        self.assertEqual(res["address"], "192.0.2.10")
        self.assertIsNone(res["ready"])                      # no platform known yet: the readiness is checked at the pre-flight
        self.assertEqual({d for d, _ in self.ran if _}, {"lab-one", lh.MOVER_DISTRO})
        reg = {h["name"]: h for h in hosts_mod.load(self.home)}
        self.assertEqual(reg["lab-cinder"]["port"], 2201)
        self.assertEqual(reg["lab-mover"]["port"], 2202)
        self.assertEqual(reg["lab-cinder"]["python"], lh.CINDER_PYTHON)
        self.assertEqual(self.pinned, {("192.0.2.10", 2201), ("192.0.2.10", 2202)})

    def test_the_journal_says_that_no_person_compared_the_fingerprint(self):
        self.prepare()
        trusted = [e for e in self.state.journal.events if e[0] == "host_trusted"]
        self.assertEqual(len(trusted), 2)
        for _, _, data in trusted:
            self.assertIn("no person compared", data["note"])
        self.assertTrue(any(e[0] == "lab" and e[1] == "hosts" for e in self.state.journal.events))

    def test_a_host_that_is_already_pinned_is_not_pinned_again(self):
        self.pinned = {("192.0.2.10", 2201), ("192.0.2.10", 2202)}
        self.prepare()
        self.assertEqual([e for e in self.state.journal.events if e[0] == "host_trusted"], [])

    def test_a_failed_setup_script_stops_with_its_output(self):
        def failing(distro, script, args=(), timeout=600):
            if "hostname -I" in script or "sleep infinity" in script:
                return self.fake_run(distro, script, args, timeout)
            return 1, "could not install: lvm2", "boom"
        from contextlib import ExitStack
        with ExitStack() as st:
            for p in self.patches():
                st.enter_context(p)
            st.enter_context(mock.patch.object(lh, "run", failing))
            with self.assertRaises(lab.LabError) as cm:
                lh.prepare(self.state)
        self.assertIn("could not install: lvm2", str(cm.exception))
        self.assertIn("boom", str(cm.exception))

    def test_a_missing_image_for_the_second_distro_says_so(self):
        with mock.patch.object(lh, "distros", lambda: {"lab-one"}):
            with self.assertRaises(lab.LabError) as cm:
                lh.ensure_mover_distro(lab.load_config())
        self.assertIn(lh.MOVER_DISTRO, str(cm.exception))

    def test_the_setup_script_uses_keys_only_and_a_port_argument(self):
        self.assertIn("PasswordAuthentication no", lh.SETUP)
        self.assertIn('Port $PORT', lh.SETUP)


if __name__ == "__main__":
    unittest.main()
