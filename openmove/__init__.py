# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""OpenMove: OpenStack Cinder volume audit, reporting and migration."""
import json
from pathlib import Path


def _read_version():
    here = Path(__file__).resolve().parent
    for p in (here.parent / "version.json", here / "resources" / "version.json"):
        try:
            return json.loads(p.read_text(encoding="utf-8"))["version"]
        except (OSError, ValueError, KeyError):
            continue
    return "unknown"


__version__ = _read_version()
