# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""R2: every place where the program opens a network connection is named in docs/NETWORK.md."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "openmove"
OPENS = re.compile(r"urlopen\(|http\.client|socket\.socket|socket\.create_connection|smtplib|ftplib|telnetlib|paramiko|subprocess|os\.system")
SKIP = ("demo", "resources")          # the demonstration cloud and the pages are not clients


class NetworkTests(unittest.TestCase):
    def files_that_connect(self):
        found = {}
        for path in PACKAGE.rglob("*.py"):
            rel = path.relative_to(PACKAGE).as_posix()
            if rel.split("/")[0] in SKIP:
                continue
            hits = sorted(set(OPENS.findall(path.read_text(encoding="utf-8"))))
            if hits:
                found[rel] = hits
        return found

    def test_only_the_documented_modules_open_connections(self):
        found = self.files_that_connect()
        self.assertEqual(set(found), {"connectors/httpclient.py", "lab/__init__.py"}, "a module opens a connection that docs/NETWORK.md does not name: %s" % found)
        doc = (ROOT / "docs" / "NETWORK.md").read_text(encoding="utf-8")
        for rel in found:
            self.assertIn(rel.replace("/__init__.py", "/").replace("__init__.py", ""), doc.replace("lab/__init__.py", "lab/"), rel)

    def test_no_ssh_or_database_client_exists_yet(self):
        text = " ".join(p.read_text(encoding="utf-8") for p in PACKAGE.rglob("*.py"))
        for word in ("paramiko", "sqlite3.connect(\"mysql", "pymysql", "psycopg"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
