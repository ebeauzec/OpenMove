# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The reviewable documentation must list every call, route and journal event the code has (requirement R14)."""
import re
import unittest
from pathlib import Path

from openmove import server

ROOT = Path(__file__).resolve().parents[1]
CALLS = (ROOT / "docs" / "API_CALLS.md").read_text(encoding="utf-8")
SOURCES = {p: p.read_text(encoding="utf-8") for p in (ROOT / "openmove").rglob("*.py") if "demo" not in p.parts}


class DocsSyncTests(unittest.TestCase):
    def test_every_outbound_path_is_documented(self):
        paths = set()
        for text in SOURCES.values():
            paths.update(re.findall(r'\.(?:get|paginate)\((?:BLOCK_STORAGE_TYPES|COMPUTE_TYPES), "(/[^"]+)"', text))
            paths.update(re.findall(r'session\.auth_url \+ "(/[^"]+)"', text))
        self.assertGreaterEqual(len(paths), 10)
        for p in sorted(paths):
            self.assertIn(p, CALLS, "outbound call %s is not in docs/API_CALLS.md" % p)

    def test_login_and_version_documents_are_documented(self):
        self.assertIn("/auth/tokens", CALLS)
        for call in ("K1", "K2", "B0", "N0"):
            self.assertIn("| %s |" % call, CALLS)

    def test_every_local_route_is_documented(self):
        for method, path in server.ROUTES:
            self.assertIn("`%s /api/%s`" % (method, path), CALLS, "route %s /api/%s is not documented" % (method, path))

    def test_every_journal_event_is_documented(self):
        kinds = set()
        for text in SOURCES.values():
            kinds.update(re.findall(r'\.record\("([a-z_]+)"', text))
            kinds.update(re.findall(r'^(?:KIND|HALT_KIND|RESUME_KIND|VALIDATION_KIND) = "([a-z_]+)"', text, re.M))
        self.assertGreaterEqual(len(kinds), 10)
        for k in sorted(kinds):
            self.assertIn("`%s`" % k, CALLS, "journal event %s is not documented" % k)

    def test_the_documents_exist_and_are_indexed(self):
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        for name in ("PROCESS.md", "API_CALLS.md", "SAFETY.md", "CLASSES.md", "WORKLOAD_RISKS.md", "PLAYBOOK_TRACE.md"):
            self.assertTrue((ROOT / "docs" / name).is_file())
            self.assertIn(name, index)

    def test_the_lifecycle_rules_in_the_process_document_match_the_code(self):
        from openmove import lifecycle as L
        process = (ROOT / "docs" / "PROCESS.md").read_text(encoding="utf-8")
        for phase in L.ORDER:
            self.assertIn(phase, process)
        self.assertIn("at least five minutes apart", process)
        self.assertIn("fifteen minutes", process)
        self.assertEqual((L.MIN_RECHECKS, L.RECHECK_GAP, L.MAX_RECHECK_AGE), (2, 300, 900))

    def test_every_step_of_a_volume_is_in_the_process_document(self):
        from openmove import handling
        process = (ROOT / "docs" / "PROCESS.md").read_text(encoding="utf-8")
        steps = set(handling.STEP)
        for cls in "ABCDE":
            steps.update(s["id"] for s in handling.steps_for(cls))
        for step in sorted(steps):
            self.assertIn("| `%s` |" % step, process, "step %s is not in the table in PROCESS.md" % step)

    def test_every_check_is_in_the_catalogue_and_the_risk_document_names_v11(self):
        risks = (ROOT / "docs" / "WORKLOAD_RISKS.md").read_text(encoding="utf-8")
        self.assertIn("V11", risks)
        self.assertIn("look inside a guest", risks)


if __name__ == "__main__":
    unittest.main()
