# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Where OpenMove keeps its files, on every operating system (R10).

Everything that depends on the operating system is in this module.
"""
import os
import sys
from pathlib import Path


def app_home():
    """The folder for the journal, settings, snapshots and reports. Created if missing.

    OPENMOVE_HOME overrides the default.
    """
    env = os.environ.get("OPENMOVE_HOME")
    if env:
        home = Path(env)
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        home = Path(base) / "OpenMove"
    elif sys.platform == "darwin":
        home = Path.home() / "Library" / "Application Support" / "OpenMove"
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        home = Path(base) / "openmove"
    home.mkdir(parents=True, exist_ok=True)
    return home


def resource_dir():
    """The folder with the files that ship with the program (release data, web interface)."""
    return Path(__file__).resolve().parent / "resources"
