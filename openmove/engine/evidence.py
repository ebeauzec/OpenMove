# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The evidence folder of one volume: what the cloud said before, every change sent, and what it said after.

Folder: <data folder>/evidence/<volume id>/ with before.json, after.json, validated.json and calls.jsonl (one line per change sent or refused).
Files hold cloud answers (ids, states, sizes). They never hold a password or a token.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def _safe(name):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(name))[:120] or "_"


class Evidence:
    def __init__(self, root):
        self.root = Path(root)

    def folder(self, volume):
        p = self.root / _safe(volume)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def save(self, volume, name, obj):
        path = self.folder(volume) / _safe(name)
        path.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str), encoding="utf-8")
        return str(path)

    def load(self, volume, name):
        try:
            return json.loads((self.folder(volume) / _safe(name)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def call(self, volume, entry):
        line = dict(entry, at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        with open(self.folder(volume) / "calls.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, sort_keys=True, default=str) + "\n")
