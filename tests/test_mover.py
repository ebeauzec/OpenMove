# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The data mover (docs/MOVER.md), run for real as a separate process on a local host: job verbs on files under its own folder, the path
rules, safe names, and the hosts registry. Logical-volume verbs need LVM and are proven in the lab and with the fake fabric."""
import hashlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from openmove import hosts as hosts_mod
from openmove.connectors import ssh


class MoverCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.home = Path(self.dir.name)
        self.mhome = self.home / "mover"
        self.host = ssh.Host({"name": "t", "transport": "local", "roles": ["mover"], "mover_python": sys.executable, "mover_dir": str(self.mhome)}, self.home)
        self.host.install_mover()

    def put(self, name, data):
        p = self.mhome / name
        p.write_bytes(data)
        return str(p)

    def wait(self, job, limit=30):
        end = time.time() + limit
        while time.time() < end:
            st = self.host.mover("job.status", {"job": job})["job"]
            if st["state"] != "running":
                return st
            time.sleep(0.1)
        self.fail("job %s did not finish" % job)


class JobTests(MoverCase):
    def test_a_copy_job_writes_the_destination_and_reports_the_checksum(self):
        data = os.urandom(300000)
        src, dst = self.put("a.img", data), self.put("b.img", b"\0" * 300000)
        self.host.mover("job.start", {"job": "om-test-copy-1", "verb": "copy", "args": {"src": src, "dst": dst}})
        st = self.wait("om-test-copy-1")
        self.assertEqual(st["state"], "done", st)
        self.assertEqual(st["result"]["source_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(Path(dst).read_bytes(), data)

    def test_the_same_job_id_is_never_a_second_process(self):
        src, dst = self.put("a.img", os.urandom(1000)), self.put("b.img", b"\0" * 1000)
        args = {"job": "om-test-copy-2", "verb": "copy", "args": {"src": src, "dst": dst}}
        self.assertFalse(self.host.mover("job.start", args)["existed"])
        self.wait("om-test-copy-2")
        self.assertTrue(self.host.mover("job.start", args)["existed"])

    def test_a_repeated_copy_writes_nothing_when_nothing_differs(self):
        data = os.urandom(5000)
        src, dst = self.put("a.img", data), self.put("b.img", b"\0" * 5000)
        self.host.mover("job.start", {"job": "om-test-copy-3", "verb": "copy", "args": {"src": src, "dst": dst}})
        self.wait("om-test-copy-3")
        self.host.mover("job.start", {"job": "om-test-copy-4", "verb": "copy", "args": {"src": src, "dst": dst}})
        self.assertEqual(self.wait("om-test-copy-4")["result"]["written"], 0)

    def test_compare_finds_a_single_difference_and_says_where(self):
        a = bytearray(os.urandom(10000))
        b = bytearray(a)
        b[7777] ^= 1
        pa, pb = self.put("a.img", bytes(a)), self.put("b.img", bytes(b))
        self.host.mover("job.start", {"job": "om-test-cmp-1", "verb": "compare", "args": {"a": pa, "b": pa}})
        self.assertTrue(self.wait("om-test-cmp-1")["result"]["identical"])
        self.host.mover("job.start", {"job": "om-test-cmp-2", "verb": "compare", "args": {"a": pa, "b": pb}})
        res = self.wait("om-test-cmp-2")["result"]
        self.assertFalse(res["identical"])
        self.assertEqual(res["first_difference"], 0)           # the chunk that differs starts at the beginning (one chunk covers the file)
        self.assertEqual(res["different_chunks"], 1)

    def test_hash_matches_sha256(self):
        data = os.urandom(4096)
        self.host.mover("job.start", {"job": "om-test-hash-1", "verb": "hash", "args": {"dev": self.put("a.img", data)}})
        self.assertEqual(self.wait("om-test-hash-1")["result"]["sha256"], hashlib.sha256(data).hexdigest())

    def test_a_failed_job_keeps_its_complete_error(self):
        src, dst = self.put("a.img", os.urandom(2000)), self.put("b.img", b"\0" * 1000)
        self.host.mover("job.start", {"job": "om-test-fail-1", "verb": "copy", "args": {"src": src, "dst": dst}})
        st = self.wait("om-test-fail-1")
        self.assertEqual(st["state"], "failed")
        self.assertIn("larger than the copy", st["error"])

    def test_finished_jobs_are_cleaned_and_the_whole_folder_can_be_removed(self):
        src, dst = self.put("a.img", b"x" * 100), self.put("b.img", b"\0" * 100)
        self.host.mover("job.start", {"job": "om-test-clean-1", "verb": "copy", "args": {"src": src, "dst": dst}})
        self.wait("om-test-clean-1")
        self.assertEqual(self.host.mover("cleanup", {})["removed_jobs"], 1)
        self.assertTrue(self.host.remove_mover()["removed_all"])
        self.assertFalse(self.mhome.exists())

    def test_diag_reports_the_jobs_and_files_without_failing_on_a_host_with_no_tools(self):
        src, dst = self.put("a.img", b"x" * 100), self.put("b.img", b"\0" * 100)
        self.host.mover("job.start", {"job": "om-test-diag-1", "verb": "copy", "args": {"src": src, "dst": dst}})
        self.wait("om-test-diag-1")
        d = self.host.mover("diag")
        self.assertIn("om-test-diag-1", d["jobs"])
        self.assertEqual(d["jobs"]["om-test-diag-1"]["status"]["state"], "done")
        self.assertTrue(any(n.startswith("mover-") for n in d["mover_files"]))
        self.assertIn("commands", d)

    def test_bench_reads_and_reports_a_rate(self):
        res = self.host.mover("bench", {"dev": self.put("a.img", os.urandom(200000))})
        self.assertEqual(res["bytes"], 200000)
        self.assertGreater(res["mib_per_s"], 0)


class RuleTests(MoverCase):
    def refused(self, verb, args):
        with self.assertRaises(ssh.SshError) as cm:
            self.host.mover(verb, args)
        return str(cm.exception)

    def test_a_job_cannot_write_outside_its_own_folder_or_a_tagged_volume(self):
        outside = Path(self.dir.name) / "outside.img"
        outside.write_bytes(b"\0" * 100)
        src = self.put("a.img", b"x" * 100)
        msg = self.refused("job.start", {"job": "om-test-rule-1", "verb": "copy", "args": {"src": src, "dst": str(outside)}})
        self.assertIn("not a device path or a file in the mover folder", msg)
        self.assertEqual(outside.read_bytes(), b"\0" * 100)

    def test_a_job_cannot_read_an_arbitrary_file(self):
        msg = self.refused("job.start", {"job": "om-test-rule-2", "verb": "hash", "args": {"dev": str(Path(self.dir.name) / "journal.db")}})
        self.assertIn("not a device path", msg)

    def test_unsafe_names_and_job_ids_are_refused(self):
        self.assertIn("not a safe name", self.refused("lv.create", {"vg": "vg0", "name": "../etc", "bytes": 10}))
        self.assertIn("not a safe name", self.refused("lv.remove", {"vg": "vg0 -f", "name": "x"}))
        self.assertIn("not a safe name", self.refused("lv.extend", {"vg": "vg0", "name": "../etc", "bytes": 10}))
        self.assertIn("not a job id", self.refused("job.status", {"job": "../../x"}))

    def test_an_unknown_verb_and_a_job_that_does_not_exist_are_refused_with_a_reason(self):
        self.assertIn("unknown verb", self.refused("rm-rf", {}))
        self.assertIn("no job", self.refused("job.status", {"job": "om-test-nothing"}))


class CinderHelperRuleTests(unittest.TestCase):
    """The rule that decides whether a destination record may be made from a source volume (found in the lab: the source is already frozen)."""

    def problems(self, *args):
        import importlib.util
        spec = importlib.util.spec_from_file_location("cinder_helper", Path(__file__).resolve().parents[1] / "openmove" / "resources" / "mover" / "cinder_helper.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.source_problems(*args)

    def test_a_quiet_volume_and_a_volume_frozen_by_openmove_are_accepted(self):
        self.assertEqual(self.problems("available", None, None, False, None), [])
        self.assertEqual(self.problems("in-use", "success", None, False, None), [])
        self.assertEqual(self.problems("maintenance", "migrating", None, False, None), [])
        self.assertEqual(self.problems("retyping", "migrating", None, False, None), [])

    def test_anything_else_is_refused_with_the_reason(self):
        self.assertIn("source status deleting", self.problems("deleting", None, None, False, None)[0])
        self.assertTrue(any("mid-migration" in p for p in self.problems("available", "migrating", None, False, None)))
        self.assertTrue(any("mid-migration" in p for p in self.problems("available", "starting", None, False, None)))
        self.assertTrue(any("status" in p for p in self.problems("maintenance", None, None, False, None)))
        self.assertIn("encrypted or multiattach", self.problems("available", None, "key", False, None))
        self.assertIn("in a group", self.problems("available", None, None, False, "g1"))


class HostRegistryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.home = Path(self.dir.name)

    def test_a_host_is_saved_listed_changed_and_removed(self):
        hosts_mod.upsert(self.home, {"name": "mover1", "roles": ["mover"], "address": "mover1.example.test", "user": "openmove", "port": 2201})
        hosts_mod.upsert(self.home, {"name": "cinder1", "role": "cinder", "address": "192.0.2.10"})
        self.assertEqual([h["name"] for h in hosts_mod.load(self.home)], ["cinder1", "mover1"])
        hosts_mod.upsert(self.home, {"name": "mover1", "roles": ["mover", "cinder"], "address": "mover1.example.test"})
        self.assertEqual(hosts_mod.host(self.home, "mover1").spec["roles"], ["cinder", "mover"])
        self.assertTrue(hosts_mod.remove(self.home, "cinder1"))
        self.assertFalse(hosts_mod.remove(self.home, "cinder1"))
        self.assertEqual([h.name for h in hosts_mod.hosts_with(self.home, "mover")], ["mover1"])

    def test_bad_input_is_refused_with_a_reason(self):
        for spec, word in (({"name": "bad name", "address": "a.test"}, "name"), ({"name": "h", "roles": ["root"], "address": "a.test"}, "role"),
                           ({"name": "h", "address": "a.test; rm -rf /"}, "address"), ({"name": "h", "address": "a.test", "port": 70000}, "port"),
                           ({"name": "h", "address": "a.test", "user": "x y"}, "user"), ({"name": "h", "address": "a.test", "python": "py;x"}, "python"),
                           ({"name": "h", "address": "a.test", "transport": "telnet"}, "transport")):
            with self.assertRaises(hosts_mod.HostError, msg=spec) as cm:
                hosts_mod.upsert(self.home, spec)
            self.assertIn(word, str(cm.exception))
        self.assertEqual(hosts_mod.load(self.home), [])

    def test_the_view_for_the_screen_holds_no_key_material(self):
        hosts_mod.upsert(self.home, {"name": "m", "address": "m.test"})
        v = hosts_mod.view(self.home)
        self.assertEqual(v["hosts"][0]["name"], "m")
        self.assertFalse(v["hosts"][0]["pinned"])
        self.assertNotIn("PRIVATE KEY", str(v))

    def test_a_local_host_passes_the_basic_readiness_checks(self):
        hosts_mod.upsert(self.home, {"name": "here", "transport": "local", "roles": ["mover"], "mover_python": sys.executable, "mover_dir": str(self.home / "mv")})
        checks = hosts_mod.readiness(hosts_mod.host(self.home, "here"), self.home, family="lvm")
        by_id = {c["id"]: c for c in checks}
        self.assertEqual(by_id["H1"]["status"], "pass")
        self.assertEqual(by_id["H3"]["status"], "pass", by_id["H3"])
        self.assertEqual(by_id["H4"]["status"], "pass")
        for c in checks:
            self.assertIn(c["status"], ("pass", "warn", "fail"))
            self.assertTrue(c["detail"])

    def test_a_cinder_helper_answer_that_is_not_json_is_reported_with_the_host_output(self):
        h = ssh.Host({"name": "c", "transport": "local", "roles": ["cinder"], "python": sys.executable}, self.home)
        with self.assertRaises(ssh.SshError) as cm:
            h.cinder("show", {"id": "x"})              # no Cinder on this machine: the import fails, and the error says so
        self.assertIn("Cinder helper", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
