# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""R1: nothing to download or configure. The program uses only the Python standard library, and its pages load nothing from outside."""
import ast
import re
import sys
import unittest
from pathlib import Path

from openmove.reports import docx, markdown

ROOT = Path(__file__).resolve().parents[1]


class SelfContainedTests(unittest.TestCase):
    def test_only_standard_library_and_own_modules_are_imported(self):
        std = set(sys.stdlib_module_names)
        bad = []
        for py in (ROOT / "openmove").rglob("*.py"):
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module.split(".")[0]]
                bad += ["%s imports %s" % (py.name, n) for n in names if n not in std and n != "openmove"]
        self.assertEqual(bad, [])

    def test_web_files_load_nothing_from_outside(self):
        web = ROOT / "openmove" / "resources" / "web"
        for f in web.iterdir():
            text = f.read_text(encoding="utf-8")
            self.assertEqual(re.findall(r"""(?:src|href)\s*=\s*["']https?://|url\(\s*["']?https?://|@import\s+["']?https?://""", text), [], f.name)

    def test_the_page_sets_a_strict_content_security_policy(self):
        text = (ROOT / "openmove" / "server.py").read_text(encoding="utf-8")
        self.assertIn("default-src 'self'", text)


class WriterTests(unittest.TestCase):
    def test_markdown_becomes_blocks_and_a_word_file(self):
        doc = markdown.parse("# Title\n\nCustomer: Example Customer\n\n## Section\n\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode line\n```\n\nAfter code.\n")
        kinds = [b["t"] for b in doc["blocks"]]
        for want in ("h", "bullet", "table", "code", "p"):
            self.assertIn(want, kinds)
        data = docx.build_docx(doc)
        self.assertTrue(data.startswith(b"PK"))

    def test_text_is_escaped_in_the_word_file(self):
        import io
        import zipfile
        data = docx.build_docx({"title": "T & <x>", "customer": "", "blocks": [{"t": "p", "text": "a < b & c > d"}]})
        body = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8")
        self.assertNotIn("a < b", body)
        self.assertIn("a &lt; b &amp; c &gt; d", body)


if __name__ == "__main__":
    unittest.main()
