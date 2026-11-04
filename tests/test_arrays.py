# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The array calls of the data mover (docs/ARRAY_API.md): the closed lists for SolidFire (Element API) and FlashArray (Purity CLI), the rule
that only clones and volumes the mover made are changed, the login file, and the iSCSI and device verbs.

The Element API is a small fake HTTP server written from the vendor's documentation and the playbook's scripts; the Purity CLI and iscsiadm are
replaced by a recording function. What these tests prove is the list, the refusals and the bookkeeping. They cannot prove how a real SolidFire
or FlashArray answers: that is Phase 0 (T23 and the array tests) on the customer's systems."""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from openmove import hosts as hosts_mod
from openmove.connectors import ssh

MOVER = Path(__file__).resolve().parents[1] / "openmove" / "resources" / "mover" / "mover.py"
PASSWORD = "Zq7-unique-element-password-5521"
MIG = 41                                          # the migration account


def load_mover(home):
    os.environ["OPENMOVE_MOVER_HOME"] = str(home)
    spec = importlib.util.spec_from_file_location("mover_under_test_%s" % uuid.uuid4().hex, str(MOVER))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeElement:
    """An Element cluster as far as the mover uses it: volumes with an owner, clones, access groups, errors."""

    def __init__(self):
        self.calls = []
        self.volumes = {10: {"volumeID": 10, "accountID": 7, "name": "UUID-tenant", "totalSize": 2 * 1024 ** 3},
                        11: {"volumeID": 11, "accountID": MIG, "name": "mig-clone-1", "totalSize": 2 * 1024 ** 3},
                        12: {"volumeID": 12, "accountID": MIG, "name": "mig-clone-2", "totalSize": 2 * 1024 ** 3}}
        self.next_id = 100
        self.deleted = {}
        self.fail = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.calls.append((self.path, body, self.headers.get("Authorization")))
                out = outer.answer(body["method"], body.get("params") or {})
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.address = "127.0.0.1:%d" % self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def answer(self, method, p):
        if method in self.fail:
            return {"error": {"name": self.fail[method], "message": "refused by the cluster", "code": 500}}
        if method == "ListVolumes":
            return {"result": {"volumes": [v for k, v in self.volumes.items() if k in p.get("volumeIDs", [])]}}
        if method == "GetClusterVersionInfo":
            return {"result": {"clusterVersion": "12.5.0.897", "clusterAPIVersion": "12.5"}}
        if method == "ListDeletedVolumes":
            return {"result": {"volumes": list(self.deleted.values())}}
        if method == "CloneVolume":
            self.next_id += 1
            self.volumes[self.next_id] = {"volumeID": self.next_id, "accountID": p["newAccountID"], "name": p["name"]}
            return {"result": {"volumeID": self.next_id, "asyncHandle": 9}}
        if method == "DeleteVolume":
            self.deleted[p["volumeID"]] = self.volumes.pop(p["volumeID"])
        if method == "PurgeDeletedVolume":
            self.deleted.pop(p["volumeID"])
        return {"result": {"ok": True, "method": method}}

    def methods(self):
        return [b["method"] for _, b, _ in self.calls]


class MoverArrays(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.m = load_mover(Path(self.dir.name) / "mover")
        self.el = FakeElement()
        self.addCleanup(self.el.stop)
        self.ran = []
        self.answers = {}
        self.m.run = self.fake_run

    def fake_run(self, cmd, timeout=120):
        self.ran.append(list(cmd))
        for key, ans in self.answers.items():
            if " ".join(cmd).startswith(key):
                return ans
        return 0, "", ""

    def sf(self, **kw):
        a = dict(name="sf1", family="solidfire", mgmt=self.el.address, scheme="http", user="admin", password=PASSWORD, migration_account_id=MIG)
        a.update(kw)
        return self.m.v_array_set(a)

    def pure(self, **kw):
        a = dict(name="pure1", family="pure", mgmt="192.0.2.50", host_object="migration-host")
        a.update(kw)
        return self.m.v_array_set(a)

    def call(self, method, params=None, **kw):
        return self.m.v_sf_call(dict(array="sf1", method=method, params=params or {}, **kw))

    def refused(self, fn, *args):
        with self.assertRaises(self.m.MoverError) as cm:
            fn(*args)
        return str(cm.exception)

    # ---- the login ---------------------------------------------------------------------------------------------------------------
    def test_a_login_is_stored_in_a_private_file_and_never_returned(self):
        res = self.sf()
        self.assertTrue(res["stored"])
        self.assertNotIn(PASSWORD, json.dumps(res))
        listed = self.m.v_array_list({})
        self.assertEqual([x["name"] for x in listed["arrays"]], ["sf1"])
        self.assertNotIn(PASSWORD, json.dumps(listed))
        path = Path(self.m.ARRAYS) / "sf1.json"
        self.assertIn(PASSWORD, path.read_text())                       # the file holds it: that is its job
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_a_login_with_a_wrong_scheme_account_or_name_is_refused(self):
        self.assertIn("https", self.refused(self.m.v_array_set, dict(name="x", family="solidfire", mgmt="198.51.100.9", scheme="http", user="a", password="p", migration_account_id=1)))
        self.assertIn("migration_account_id", self.refused(self.m.v_array_set, dict(name="x", family="solidfire", mgmt="198.51.100.9", user="a", password="p")))
        self.assertIn("safe name", self.refused(self.m.v_array_set, dict(name="../x", family="pure", mgmt="198.51.100.9", host_object="h")))
        self.assertIn("family", self.refused(self.m.v_array_set, dict(name="x", family="ontap", mgmt="198.51.100.9")))
        self.assertIn("host_object", self.refused(self.m.v_array_set, dict(name="x", family="pure", mgmt="198.51.100.9")))

    def test_a_call_to_an_array_with_no_stored_login_is_refused(self):
        self.assertIn("no login", self.refused(self.m.v_sf_call, dict(array="nothing", method="GetClusterInfo")))

    def test_forget_removes_the_login(self):
        self.sf()
        self.assertTrue(self.m.v_array_forget({"name": "sf1"})["removed"])
        self.assertEqual(self.m.v_array_list({})["arrays"], [])

    # ---- SolidFire: the list ---------------------------------------------------------------------------------------------------
    def test_a_read_call_reaches_the_cluster_with_the_login(self):
        self.sf()
        r = self.call("ListVolumes", {"volumeIDs": [10]})
        self.assertEqual(r["reply"]["volumes"][0]["volumeID"], 10)
        path, body, auth = self.el.calls[0]
        self.assertEqual(path, "/json-rpc/11.0")
        self.assertTrue(auth.startswith("Basic "))

    def test_a_method_that_is_not_on_the_list_never_reaches_the_cluster(self):
        self.sf()
        for m in ("CreateSnapshot", "DeleteSnapshot", "RollbackToSnapshot", "ModifyVolume", "CreateVolume", "AddAccount", "ModifyVolumes", "DeleteAllSnapshots"):
            self.assertIn("not on the list", self.refused(self.call, m, {"volumeID": 10}), m)
        self.assertEqual(self.el.calls, [])

    def test_a_clone_is_read_only_small_named_and_owned_by_the_migration_account(self):
        self.sf()
        ok = dict(volumeID=10, snapshotID=5, name="mig-c-1", newAccountID=MIG, access="readOnly", attributes={"migration": "x"})
        self.assertEqual(self.call("CloneVolume", ok)["reply"]["asyncHandle"], 9)
        for bad, word in ((dict(ok, newAccountID=7), "migration account"), (dict(ok, access="readWrite"), "read only"), (dict(ok, name="n" * 65), "64"),
                          (dict(ok, name="a b"), "safe name"), (dict(ok, enable512e=True), "does not take"), (dict(ok, volumeID="10"), "volumeID")):
            self.assertIn(word, self.refused(self.call, "CloneVolume", bad), word)
        self.assertEqual(self.el.methods(), ["CloneVolume"])

    def test_only_a_clone_in_the_migration_account_can_be_deleted_purged_or_given_access(self):
        self.sf()
        for m in ("DeleteVolume", "PurgeDeletedVolume"):
            self.assertIn("migration account", self.refused(self.call, m, {"volumeID": 10}), m)           # a tenant volume
            self.assertEqual(self.call(m, {"volumeID": 11})["method"], m)                                  # a clone
        for m in ("AddVolumesToVolumeAccessGroup", "RemoveVolumesFromVolumeAccessGroup"):
            self.assertIn("migration account", self.refused(self.call, m, {"volumeAccessGroupID": 3, "volumes": [10]}), m)
            self.assertEqual(self.call(m, {"volumeAccessGroupID": 3, "volumes": [12]})["method"], m)
        self.assertNotIn(10, [b["params"].get("volumeID") for _, b, _ in self.el.calls if b["method"].endswith("Volume")])

    def test_an_error_from_the_cluster_is_kept_whole(self):
        self.sf()
        self.el.fail["ListSnapshots"] = "xVolumeIDDoesNotExist"
        msg = self.refused(self.call, "ListSnapshots", {"volumeID": 999})
        self.assertIn("xVolumeIDDoesNotExist", msg)
        self.assertIn("refused by the cluster", msg)
        self.assertNotIn(PASSWORD, msg)

    def test_a_call_to_a_pure_array_through_the_element_verb_is_refused(self):
        self.pure()
        self.assertIn("not a SolidFire", self.refused(self.m.v_sf_call, dict(array="pure1", method="GetClusterInfo")))

    # ---- FlashArray: the list and the ledger -------------------------------------------------------------------------------------
    def cli(self, *argv, **kw):
        return self.m.v_pure_cli(dict(array="pure1", argv=list(argv), **kw))

    def ssh_lines(self):
        return [c for c in self.ran if c and c[0] == "ssh"]

    def test_the_reads_are_allowed_and_go_out_over_ssh_with_a_key(self):
        self.pure(identity=sys.executable)
        self.cli("purevol", "list", "--notitle", "--csv", "volume-1")
        self.cli("purevol", "list", "--snap", "--notitle", "--csv", "volume-1")
        self.cli("purevol", "list", "--connect", "--notitle", "--csv", "volume-1")
        self.cli("purearray", "list", "--space")
        line = self.ssh_lines()[0]
        self.assertIn("BatchMode=yes", line)
        self.assertIn("StrictHostKeyChecking=yes", line)
        self.assertEqual(line[line.index("-i") + 1], sys.executable)
        self.assertEqual(line[-6:], ["pureuser@192.0.2.50", "purevol", "list", "--notitle", "--csv", "volume-1"])

    def test_a_command_that_is_not_on_the_list_is_never_sent(self):
        self.pure()
        for argv in (["purevol", "eradicate", "stage-1"], ["purevol", "copy", "a", "b"], ["purehost", "delete", "h"], ["purevol", "destroy", "volume-1"],
                     ["purevol", "list", "--notitle", "--csv", "a; rm -rf /"], ["bash", "-c", "id"], ["purepod", "create", "p"], [], "purevol list"):
            self.refused(self.m.v_pure_cli, dict(array="pure1", argv=argv))
        self.assertEqual(self.ssh_lines(), [])

    def test_the_mover_changes_only_volumes_it_made(self):
        self.pure()
        cid = "volume-%s-cinder" % uuid.uuid4()
        snap = "snapshot-%s" % uuid.uuid4()
        tenant = "volume-%s-cinder" % uuid.uuid4()
        self.assertIn("did not make", self.refused(self.cli, "purevol", "setattr", "--size", "2G", tenant))
        self.assertIn("did not make", self.refused(self.cli, "purevol", "destroy", tenant))
        self.assertIn("did not make", self.refused(self.cli, "purevol", "rename", tenant, cid))
        self.assertIn("did not make", self.refused(self.cli, "purehost", "connect", "--vol", tenant, "migration-host"))
        self.cli("purevol", "create", "--size", "2G", "stage-abc")
        self.cli("purevol", "setattr", "--size", "3G", "stage-abc")
        self.cli("purehost", "connect", "--vol", "stage-abc", "migration-host")
        self.cli("purevol", "snap", "--suffix", snap, "stage-abc")
        self.assertIn("not on the list", self.refused(self.cli, "purehost", "connect", "--vol", "stage-abc", "some-other-host"))
        self.cli("purehost", "disconnect", "--vol", "stage-abc", "migration-host")
        self.cli("purevol", "rename", "stage-abc", cid)                                               # the name the target driver derives
        self.assertIn("did not make", self.refused(self.cli, "purevol", "setattr", "--size", "3G", "stage-abc"))
        self.cli("purevol", "destroy", "%s.%s" % (cid, snap))                                         # a snapshot of a volume it made
        self.cli("purevol", "destroy", cid)
        self.assertIn("did not make", self.refused(self.cli, "purevol", "destroy", cid))             # gone from the ledger
        self.assertEqual(self.m.made_names("pure1"), set())

    def test_a_rename_may_only_give_a_stage_name_or_the_cinder_name(self):
        self.pure()
        self.cli("purevol", "create", "--size", "1G", "stage-x")
        self.assertIn("did not make", self.refused(self.cli, "purevol", "rename", "stage-x", "prod-database"))
        self.assertIn("not on the list", self.refused(self.cli, "purevol", "create", "--size", "1G", "prod-database"))

    def test_a_failed_command_does_not_change_the_ledger(self):
        self.pure()
        self.answers["ssh"] = (1, "", "Error: a volume with that name exists")
        r = self.cli("purevol", "create", "--size", "1G", "stage-y")
        self.assertEqual(r["rc"], 1)
        self.assertIn("exists", r["stderr"])
        self.assertEqual(self.m.made_names("pure1"), set())

    # ---- iSCSI and devices ------------------------------------------------------------------------------------------------------
    IQN = "iqn.2010-01.com.solidfire:abcd.uuid-1.5"

    def test_a_login_discovers_logs_in_and_remembers_the_session(self):
        self.answers["iscsiadm -m session"] = (1, "iscsiadm: No active sessions.", "")
        r = self.m.v_iscsi_login({"portal": "192.0.2.10:3260", "iqn": self.IQN})
        self.assertFalse(r["existed"])
        kinds = [c[1:5] for c in self.ran if c[0] == "iscsiadm"]
        self.assertIn(["-m", "discovery", "-t", "sendtargets"], kinds)
        self.assertTrue(any(c[:7] == ["iscsiadm", "-m", "node", "-T", self.IQN, "-p", "192.0.2.10:3260"] and c[-1] == "-l" for c in self.ran))
        self.assertIn("192.0.2.10:3260 " + self.IQN, Path(self.m.sessions_file()).read_text())

    def test_a_session_that_exists_is_not_logged_in_again(self):
        self.answers["iscsiadm -m session"] = (0, "tcp: [3] 192.0.2.10:3260,1 %s (non-flash)\n" % self.IQN, "")
        self.assertTrue(self.m.v_iscsi_login({"portal": "192.0.2.10:3260", "iqn": self.IQN})["existed"])
        self.assertFalse(any("-l" in c for c in self.ran))

    def test_the_mover_logs_out_only_of_sessions_it_opened(self):
        self.assertIn("did not log in", self.refused(self.m.v_iscsi_logout, {"portal": "192.0.2.10:3260", "iqn": self.IQN}))
        self.answers["iscsiadm -m session"] = (1, "", "")
        self.m.v_iscsi_login({"portal": "192.0.2.10:3260", "iqn": self.IQN})
        self.assertTrue(self.m.v_iscsi_logout({"portal": "192.0.2.10:3260", "iqn": self.IQN})["logged_out"])
        self.assertTrue(any(c[-1] == "-u" for c in self.ran))
        self.assertIn("did not log in", self.refused(self.m.v_iscsi_logout, {"portal": "192.0.2.10:3260", "iqn": self.IQN}))

    def test_a_bad_target_name_or_portal_is_refused_before_any_command_runs(self):
        for portal, iqn in (("192.0.2.10", self.IQN), ("192.0.2.10:3260", "iqn.x; reboot"), ("a b:3260", self.IQN)):
            self.refused(self.m.v_iscsi_login, {"portal": portal, "iqn": iqn})
        self.assertEqual(self.ran, [])

    def test_a_device_path_is_limited_to_the_disk_and_mapper_folders(self):
        for p in ("/etc/passwd", "/dev/sda", "/dev/disk/by-path/../../sda", "/dev/mapper/../sda"):
            self.assertIn("not a device path", self.refused(self.m.v_dev_wait, {"path": p}))

    def test_the_device_size_is_checked_when_it_is_asked_for(self):
        real = Path(self.dir.name) / "dev"
        real.write_bytes(b"")
        self.m.DEVICE_DIRS = (str(self.dir.name),)
        self.answers["blockdev"] = (0, "1073741824\n", "")
        self.assertEqual(self.m.v_dev_wait({"path": str(real), "bytes": 1073741824})["bytes"], 1073741824)
        self.assertIn("not the 2147483648", self.refused(self.m.v_dev_wait, {"path": str(real), "bytes": 2147483648}))


class ThroughTheHost(unittest.TestCase):
    """The controller side: a secret sent to the mover is never written to the call log."""

    def test_the_password_of_an_array_is_not_traced(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        home = Path(d.name)
        host = ssh.Host({"name": "t", "transport": "local", "roles": ["mover"], "mover_python": sys.executable, "mover_dir": str(home / "mover")}, home)
        host.install_mover()
        seen = []
        old = ssh.TRACE
        ssh.TRACE = seen.append
        self.addCleanup(setattr, ssh, "TRACE", old)
        res = host.mover("array.set", dict(name="sf1", family="solidfire", mgmt="127.0.0.1:9", scheme="http", user="admin", password=PASSWORD, migration_account_id=5))
        self.assertTrue(res["stored"])
        text = json.dumps(seen)
        self.assertNotIn(PASSWORD, text)
        self.assertIn("***", text)
        listed = host.mover("array.list")
        self.assertEqual(listed["arrays"][0]["name"], "sf1")
        self.assertNotIn(PASSWORD, json.dumps(seen))


class ThroughTheApi(unittest.TestCase):
    """The route that stores an array login on a host, the journal and the host check H10."""

    def setUp(self):
        from types import SimpleNamespace
        from openmove.journal import Journal
        self.dir = tempfile.TemporaryDirectory()
        self.home = Path(self.dir.name)
        self.journal = Journal(self.home / "j.db")
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(self.journal.close)
        hosts_mod.upsert(self.home, {"name": "mh", "transport": "local", "roles": ["mover"], "mover_python": sys.executable, "mover_dir": str(self.home / "mover")})
        self.state = SimpleNamespace(home=self.home, journal=self.journal, diag=None, fabric=None)
        self.el = FakeElement()
        self.addCleanup(self.el.stop)

    def arr(self, **kw):
        a = dict(name="sf1", family="solidfire", mgmt=self.el.address, scheme="http", user="admin", password=PASSWORD, migration_account_id=MIG)
        a.update(kw)
        return a

    def test_storing_a_login_proves_it_and_leaves_the_password_nowhere_but_the_hosts_private_file(self):
        from openmove import server_hosts
        res = server_hosts.api_hosts_array(self.state, {"host": "mh", "action": "set", "array": self.arr()}, {})
        self.assertTrue(res["check"]["ok"], res)
        self.assertIn("12.5.0.897", res["check"]["detail"])
        self.assertNotIn(PASSWORD, json.dumps(res))
        for f in self.home.rglob("*"):
            if f.is_file() and f.suffix in (".json", ".db", ".log") and "mover" + os.sep + "arrays" not in str(f):
                self.assertNotIn(PASSWORD.encode(), f.read_bytes(), str(f))
        listed = server_hosts.api_hosts_array(self.state, {"host": "mh", "action": "list"}, {})
        self.assertEqual([x["name"] for x in listed["arrays"]], ["sf1"])

    def test_the_host_check_reports_each_array_and_a_wrong_login_fails_it(self):
        from openmove import server_hosts
        server_hosts.api_hosts_array(self.state, {"host": "mh", "action": "set", "array": self.arr()}, {})
        checks = hosts_mod.readiness(hosts_mod.host(self.home, "mh"), self.home, None, None, "iscsi")
        h10 = [c for c in checks if c["id"] == "H10"]
        self.assertEqual([c["status"] for c in h10], ["pass"], h10)
        self.el.fail["GetClusterVersionInfo"] = "xUnauthorized"
        h10 = [c for c in hosts_mod.readiness(hosts_mod.host(self.home, "mh"), self.home, None, None, "iscsi") if c["id"] == "H10"]
        self.assertEqual([c["status"] for c in h10], ["fail"])
        self.assertIn("xUnauthorized", h10[0]["detail"])

    def test_a_bad_action_and_a_missing_array_are_refused(self):
        from openmove import server_hosts
        for body in ({"host": "mh", "action": "eradicate"}, {"host": "mh", "action": "check", "name": "nothing"}):
            with self.assertRaises(Exception):
                server_hosts.api_hosts_array(self.state, body, {})


if __name__ == "__main__":
    unittest.main()
