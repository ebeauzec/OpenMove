# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Redaction for dark sites (docs/DIAGNOSTICS.md): no customer data in a bundle, and enough structure left to find a fault."""
import json
import tempfile
import unittest
from pathlib import Path

from openmove import redact
from openmove.redact import Redactor

UUID_A = "3f2b8c1e-9d4a-4b7e-8a61-0c5d2e7f9a10"
UUID_B = "a1b2c3d4-0000-4000-8000-123456789abc"


class TextTests(unittest.TestCase):
    def test_none_changes_nothing(self):
        r = Redactor("none")
        s = "volume %s on 10.20.30.40 for ops@example.com" % UUID_A
        self.assertEqual(r.text(s), s)
        self.assertEqual(r.data({"password": "x", "name": "n"}), {"password": "x", "name": "n"})

    def test_a_value_always_gets_the_same_token_and_relations_stay_visible(self):
        r = Redactor("standard")
        out = r.text("copy %s to %s then compare %s again" % (UUID_A, UUID_B, UUID_A))
        self.assertNotIn(UUID_A, out)
        self.assertNotIn(UUID_B, out)
        first, second, third = [w for w in out.split() if w.startswith("uuid-")]
        self.assertEqual(first, third)
        self.assertNotEqual(first, second)

    def test_addresses_hosts_and_mail_are_removed_but_the_shape_stays(self):
        r = Redactor("standard")
        out = r.text("connect to https://keystone.customer.example:5000/v3 via 192.0.2.17 and fe80::1 mail ops@customer.example host cinder-vol-3.customer.example")
        for secret in ("customer.example", "192.0.2.17", "fe80::1", "ops@", "cinder-vol-3"):
            self.assertNotIn(secret, out)
        self.assertIn("https://host-", out)
        self.assertIn(":5000/v3", out)                 # the port and the path are not customer data and help the developer

    def test_a_cinder_host_string_keeps_its_back_end_at_standard_and_loses_it_at_strict(self):
        s = "cinder-vol-1@sf-prod#pool-a"
        std, strict = Redactor("standard").text(s), Redactor("strict").text(s)
        self.assertNotIn("cinder-vol-1", std)
        self.assertIn("@sf-prod#pool-a", std)
        self.assertNotIn("sf-prod", strict)
        self.assertNotIn("pool-a", strict)

    def test_checksums_stay_comparable(self):
        r = Redactor("standard")
        h = "ab" * 32
        a, b = r.text("source " + h), r.text("target " + h)
        self.assertEqual(a.split()[1], b.split()[1])
        self.assertNotIn(h, a)

    def test_file_names_and_code_names_are_not_taken_for_hosts(self):
        r = Redactor("standard")
        s = "see openmove/engine/staged.py and cinder.volume.manager and mover.py in docs.md"
        self.assertEqual(r.text(s), s)

    def test_user_names_in_paths_and_long_secrets_are_removed(self):
        r = Redactor("standard")
        out = r.text("open C:\\Users\\jsmith\\x and /home/alice/y with gAAAAABk" + "x" * 60)
        self.assertNotIn("jsmith", out)
        self.assertNotIn("alice", out)
        self.assertNotIn("gAAAAAB", out)

    def test_known_names_are_replaced_in_free_text_on_word_boundaries(self):
        r = Redactor("standard")
        r.learn("name", ["acme-billing-db", "x", "admin"])
        out = r.text("volume acme-billing-db is attached; acme-billing-db2 is not; admin stays")
        self.assertNotIn("acme-billing-db is", out)
        self.assertIn("acme-billing-db2", out)          # a longer word is a different name
        self.assertIn("admin", out)                      # too common to be customer data
        self.assertEqual(out.count("name-1"), 1)


class DataTests(unittest.TestCase):
    def test_secrets_are_removed_whatever_the_level(self):
        for level in ("standard", "strict"):
            out = Redactor(level).data({"password": "hunter2", "X-Auth-Token": "abc", "nested": [{"api_key": "k", "size_gb": 10}]})
            self.assertEqual(out["password"], "[redacted]")
            self.assertEqual(out["X-Auth-Token"], "[redacted]")
            self.assertEqual(out["nested"][0], {"api_key": "[redacted]", "size_gb": 10})

    def test_numbers_states_and_structure_are_untouched(self):
        d = {"status": "in-use", "size": 50, "snapshots": 3, "ok": True, "when": "2026-11-03T10:00:00Z", "ids": [1, 2]}
        self.assertEqual(Redactor("strict").data(d), d)

    def test_names_become_tokens_by_key_and_ids_stay_as_tokens_too(self):
        out = Redactor("standard").data({"name": "payroll-db", "project_name": "finance", "id": UUID_A, "volume_type": "sf-gold"})
        self.assertTrue(out["name"].startswith("name-"))
        self.assertTrue(out["project_name"].startswith("name-"))
        self.assertTrue(out["id"].startswith("uuid-"))
        self.assertEqual(out["volume_type"], "sf-gold")                 # standard keeps policy names

    def test_strict_also_hides_policy_names_and_cuts_free_text(self):
        out = Redactor("strict").data({"volume_type": "sf-gold", "backend": "sf-prod", "error": "device busy " * 20})
        self.assertNotIn("sf-gold", json.dumps(out))
        self.assertNotIn("sf-prod", json.dumps(out))
        self.assertTrue(out["error"].endswith("...[cut]"))
        self.assertLess(len(out["error"]), 80)

    def test_standard_never_cuts_an_error_text(self):
        text = "lvcreate failed: " + "device busy, retry later. " * 20
        self.assertEqual(Redactor("standard").data({"error": text})["error"], text)


class MapTests(unittest.TestCase):
    def test_the_map_survives_and_decodes_only_at_the_site(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "diag" / "redaction-map.json"
            r = Redactor("standard", p)
            tok = r.text("host 192.0.2.17").split()[1]
            r.save()
            again = Redactor("standard", p)
            self.assertEqual(again.text("host 192.0.2.17").split()[1], tok)      # the same value keeps its token across runs
            self.assertEqual(again.decode(tok), "192.0.2.17")
            self.assertIsNone(again.decode("ip-999"))

    def test_nothing_is_saved_at_level_none_and_secrets_never_reach_the_map(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "map.json"
            Redactor("none", p).save()
            self.assertFalse(p.exists())
            r = Redactor("standard", p)
            r.data({"password": "hunter2", "name": "x-system"})
            r.save()
            self.assertNotIn("hunter2", p.read_text(encoding="utf-8"))

    def test_a_damaged_map_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "map.json"
            p.write_text("{not json", encoding="utf-8")
            self.assertTrue(Redactor("standard", p).text("a 192.0.2.1").startswith("a ip-"))

    def test_levels_are_checked(self):
        with self.assertRaises(ValueError):
            Redactor("paranoid")
        self.assertEqual(redact.level_of({"redaction": "nonsense"}), "standard")
        self.assertEqual(redact.level_of({"redaction": "strict"}), "strict")

    def test_lines_are_redacted_one_by_one(self):
        out = Redactor("standard").lines("a 192.0.2.1\nb 192.0.2.1\nc 192.0.2.2")
        rows = out.splitlines()
        self.assertEqual(rows[0].split()[1], rows[1].split()[1])
        self.assertNotEqual(rows[0].split()[1], rows[2].split()[1])


if __name__ == "__main__":
    unittest.main()
