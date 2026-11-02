# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Release inference, profiles, journal, classification and exports."""
import os
import tempfile
import unittest
from pathlib import Path

from openmove import classification, exports, profiles, releases
from openmove.connectors.openstack import keystone_v3_url, service_root
from openmove.journal import Journal


class ReleaseInference(unittest.TestCase):
    def test_exact_queens(self):
        r = releases.infer("3.50", "2.60")
        self.assertEqual(r["candidates"], ["Queens"])
        self.assertEqual(r["confidence"], "exact")

    def test_shared_cinder_maximum_is_resolved_by_compute(self):
        self.assertEqual(releases.infer("3.59", "2.72")["candidates"], ["Stein"])
        self.assertEqual(releases.infer("3.59", "2.79")["candidates"], ["Train"])

    def test_shared_maximum_alone_lists_both(self):
        r = releases.infer("3.59", None)
        self.assertEqual(r["candidates"], ["Stein", "Train"])
        self.assertEqual(r["release"], "Stein")  # the oldest, the cautious reading

    def test_disagreement_uses_the_oldest(self):
        r = releases.infer("3.50", "2.79")
        self.assertEqual(r["confidence"], "disagree")
        self.assertEqual(r["release"], "Queens")

    def test_value_between_releases_is_approximate(self):
        r = releases.infer("3.52", "2.65")
        self.assertEqual(r["candidates"], ["Rocky"])
        self.assertEqual(r["confidence"], "approximate")

    def test_newer_than_the_table(self):
        r = releases.infer("3.99", "2.200")
        self.assertEqual(r["confidence"], "newer_than_known")
        self.assertIsNone(r["release"])

    def test_one_service_newer_uses_the_other(self):
        r = releases.infer("3.99", "2.60")
        self.assertEqual(r["release"], "Queens")
        self.assertEqual(r["confidence"], "approximate")

    def test_nothing_reported(self):
        self.assertEqual(releases.infer(None, None)["confidence"], "unknown")

    def test_parse_version(self):
        self.assertEqual(releases.parse_version("3.50"), (3, 50))
        self.assertIsNone(releases.parse_version("v3"))
        self.assertIsNone(releases.parse_version(None))


class Urls(unittest.TestCase):
    def test_service_root(self):
        self.assertEqual(service_root("http://h:8776/v3/abc"), "http://h:8776/")
        self.assertEqual(service_root("http://h/volume/v3/abc"), "http://h/volume/")
        self.assertEqual(service_root("https://h/compute/v2.1"), "https://h/compute/")
        self.assertEqual(service_root("https://h:8774/v2.1/"), "https://h:8774/")

    def test_keystone_url(self):
        self.assertEqual(keystone_v3_url("https://k:5000"), "https://k:5000/v3")
        self.assertEqual(keystone_v3_url("https://k:5000/v3/"), "https://k:5000/v3")
        self.assertEqual(keystone_v3_url("https://k/identity"), "https://k/identity/v3")


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "j.db"
        self.j = Journal(self.path)

    def tearDown(self):
        self.j.close()
        self.dir.cleanup()

    def test_events_and_latest(self):
        self.j.record("a", "s1", {"x": 1})
        self.j.record("a", "s2", {"x": 2})
        self.assertEqual(self.j.latest("a")["data"], {"x": 2})
        self.assertEqual(self.j.latest("a", "s1")["data"], {"x": 1})
        self.assertIsNone(self.j.latest("b"))

    def test_secrets_are_scrubbed(self):
        self.j.record("connect", "p", {"user": "u", "password": "hunter2", "nested": {"api_key": "k", "ok": 1}})
        data = self.j.latest("connect")["data"]
        self.assertEqual(data["password"], "[redacted]")
        self.assertEqual(data["nested"], {"api_key": "[redacted]", "ok": 1})
        self.assertNotIn("hunter2", self.path.read_bytes().decode("latin-1"))

    def test_step_lifecycle(self):
        sid = self.j.begin_step("job1", "freeze", {"volume": "v"}, {"undo": "unfreeze"}, volume="v")
        self.assertEqual(len(self.j.unresolved_steps()), 1)
        self.j.finish_step(sid, "done", {"rows": 1})
        self.assertEqual(self.j.unresolved_steps(), [])
        with self.assertRaises(ValueError):
            self.j.finish_step(sid, "done")  # already closed

    def test_step_needs_an_inverse(self):
        with self.assertRaises(ValueError):
            self.j.begin_step("job1", "x", {}, None)

    def test_unresolved_step_survives_a_crash(self):
        self.j.begin_step("job1", "swap", {"a": 1}, "cannot be undone")
        self.j.close()  # the process dies with the step open
        j2 = Journal(self.path)
        try:
            open_steps = j2.unresolved_steps()
            self.assertEqual([s["name"] for s in open_steps], ["swap"])
        finally:
            j2.close()
        self.j = Journal(self.path)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.j = Journal(Path(self.dir.name) / "j.db")

    def tearDown(self):
        self.j.close()
        self.dir.cleanup()

    def test_queens_is_untested_until_validated(self):
        inf = releases.infer("3.50", "2.60")
        st = profiles.status_for(inf, self.j)
        self.assertEqual(st["status"], "untested")
        with self.assertRaises(profiles.ReadOnlyRelease):
            profiles.require_mutation_allowed(st)
        profiles.record_validation(self.j, "Queens", True, {"tests": "T1-T21"})
        st = profiles.status_for(inf, self.j)
        self.assertEqual(st["status"], "validated")
        profiles.require_mutation_allowed(st)

    def test_failed_validation_does_not_count(self):
        profiles.record_validation(self.j, "Queens", False)
        st = profiles.status_for(releases.infer("3.50", "2.60"), self.j)
        self.assertEqual(st["status"], "untested")

    def test_release_without_a_profile_is_untested(self):
        st = profiles.status_for(releases.infer("3.55", "2.65"), self.j)
        self.assertEqual(st["status"], "untested")
        self.assertIn("no profile", st["reason"])

    def test_unreliable_reading_is_untested_even_with_a_validation(self):
        profiles.record_validation(self.j, "Queens", True)
        st = profiles.status_for(releases.infer("3.50", "2.79"), self.j)  # signals disagree
        self.assertEqual(st["status"], "untested")


def vol(**kw):
    v = {"id": "v1", "status": "available", "size": 10, "snapshots": 0, "attachments": [],
         "encrypted": False, "multiattach": False, "replication_status": "disabled", "migration_status": None}
    v.update(kw)
    return v


class Classification(unittest.TestCase):
    def cls(self, v, **kw):
        return classification.classify(v, **kw)["class"]

    def test_class_a(self):
        r = classification.classify(vol())
        self.assertEqual(r["class"], "A")
        self.assertIn("provider_id not checked (needs database access)", r["assumptions"])

    def test_class_e_reasons(self):
        for kw, text in [({"status": "error"}, "status error"), ({"encrypted": True}, "encrypted"),
                         ({"multiattach": True}, "multiattach"), ({"group_id": "g"}, "group"),
                         ({"replication_status": "enabled"}, "replication_status enabled"),
                         ({"migration_status": "migrating"}, "migration_status migrating"),
                         ({"provider_id": None}, "provider_id"), ({"provider_id": "garbage"}, "provider_id")]:
            r = classification.classify(vol(**kw))
            self.assertEqual(r["class"], "E", kw)
            self.assertIn(text, r["reason"])

    def test_well_formed_provider_id_passes(self):
        self.assertEqual(self.cls(vol(provider_id="12 3 abcd-ef")), "A")

    def test_class_d_and_root_wait(self):
        v = vol(id="v9", status="in-use", attachments=[{"server_id": "s1"}])
        st = {"s1": {"status": "SHUTOFF"}}
        self.assertEqual(self.cls(v, instance_states=st), "D")
        r = classification.classify(v, instance_states=st, root_volumes={"v9"})
        self.assertIn("root-wait", r["reason"])

    def test_active_and_paused_are_not_class_d(self):
        v = vol(status="in-use", attachments=[{"server_id": "s1"}])
        self.assertEqual(self.cls(v, instance_states={"s1": {"status": "ACTIVE"}}), "A")
        self.assertEqual(self.cls(v, instance_states={"s1": {"status": "PAUSED"}}), "A")

    def test_unknown_instance_state_is_stated(self):
        v = vol(status="in-use", attachments=[{"server_id": "s1"}])
        r = classification.classify(v, instance_states={})
        self.assertIn("instance state not known for an attachment", r["assumptions"])

    def test_snapshots_without_history_are_class_c(self):
        r = classification.classify(vol(snapshots=2, oldest_snapshot_age_h=100))
        self.assertEqual(r["class"], "C")
        self.assertTrue(any("rotation not assessed" in a for a in r["assumptions"]))

    def test_class_b_rotating(self):
        v = vol(id="v2", snapshots=3, oldest_snapshot_age_h=48)
        self.assertEqual(self.cls(v, churn={"v2": {"deleted_last_60d": 5}}), "B")

    def test_class_c_when_a_snapshot_outlives_the_window(self):
        v = vol(id="v2", snapshots=3, oldest_snapshot_age_h=24 * 30)
        self.assertEqual(self.cls(v, churn={"v2": {"deleted_last_60d": 5}}), "C")

    def test_class_c_when_nothing_was_deleted(self):
        v = vol(id="v2", snapshots=3, oldest_snapshot_age_h=10)
        self.assertEqual(self.cls(v, churn={"v2": {"deleted_last_60d": 0}}), "C")

    def test_notes_extended_and_limit(self):
        v = vol(snapshots=32, min_snapshot_size_gib=5, oldest_snapshot_age_h=999)
        r = classification.classify(v, churn={}, snapshot_limit=32)
        self.assertEqual(r["class"], "C")
        self.assertIn("extended after snapshot", r["notes"])
        self.assertIn("at snapshot limit", r["notes"])


A1 = ("id\tstatus\tattach_status\tsize\thost\tvolume_type_id\tmultiattach\tencryption_key_id\tgroup_id\t"
      "consistencygroup_id\treplication_status\tmigration_status\tproject_id\tprovider_id\tsnapshots\t"
      "oldest_snapshot_age_h\tmin_snapshot_size_gib\tprovider_id_bad\n"
      "v-a\tavailable\tdetached\t10\tc@sf#p\tt1\t0\tNULL\tNULL\tNULL\tdisabled\tNULL\tp1\t5 2 uuid\t0\t0\tNULL\t0\n"
      "v-b\tavailable\tdetached\t10\tc@sf#p\tt1\t0\tNULL\tNULL\tNULL\tdisabled\tNULL\tp1\t5 2 uuid\t2\t48\t10\t0\n"
      "v-c\tavailable\tdetached\t10\tc@sf#p\tt1\t0\tNULL\tNULL\tNULL\tdisabled\tNULL\tp1\tNULL\t0\t0\tNULL\t1\n")
A2 = "volume_id\tcreated_last_60d\tdeleted_last_60d\tmax_lifetime_h\nv-b\t10\t9\t300\n"


class ExportsTests(unittest.TestCase):
    def test_playbook_exports_classify(self):
        vols = exports.parse_inventory(A1)
        self.assertEqual([v["backend"] for v in vols], ["sf", "sf", "sf"])
        churn = exports.parse_churn(A2)
        rows, counts, assumed = classification.classify_all(vols, churn=churn)
        got = {r["id"]: r["class"] for r in rows}
        self.assertEqual(got, {"v-a": "A", "v-b": "B", "v-c": "E"})
        self.assertNotIn("provider_id not checked (needs database access)", assumed)

    def test_missing_header_is_refused(self):
        with self.assertRaises(ValueError):
            exports.parse_inventory("foo\tbar\n1\t2\n")

    def test_instances_attach(self):
        vols = exports.parse_inventory(A1)
        states = exports.apply_instances(vols, exports.parse_instances(
            "volume_id\tinstance_uuid\tinstance_status\nv-a\ti-1\tSHUTOFF\n"))
        self.assertEqual(vols[0]["attachments"][0]["server_id"], "i-1")
        self.assertEqual(states["i-1"]["status"], "SHUTOFF")


class PathsTests(unittest.TestCase):
    def test_home_override(self):
        from openmove import paths
        with tempfile.TemporaryDirectory() as d:
            os.environ["OPENMOVE_HOME"] = str(Path(d) / "x")
            try:
                self.assertTrue(paths.app_home().is_dir())
            finally:
                del os.environ["OPENMOVE_HOME"]


if __name__ == "__main__":
    unittest.main()
