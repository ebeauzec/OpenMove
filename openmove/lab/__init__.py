# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Lab mode: OpenMove connected to a real OpenStack that runs on this machine (DevStack in a WSL distro, see tools/lab).

This is not the demonstration. The demonstration uses a mock cloud and simulates the migration. The lab is a real Keystone, Nova and
Cinder (two LVM back-ends standing in for the two arrays), and the real engine moves real volumes in it with Cinder's retype.

What the lab can prove: the engine's calls, order, polling, locking, post-check and reverse against real Cinder and Nova behaviour.
What it cannot prove: anything about a customer's Queens build, SolidFire or FlashArray. The lab release has its own profile, marked
as a lab; the Queens profile is not touched and stays unvalidated until people validate it in their own lab (gate G0).

The lab file lives outside the repository (default C:\\OpenMoveLab, or ~/.openmove-lab; override with OPENMOVE_LAB_DIR):
  lab.json         {"auth_url": "http://127.0.0.1/identity", "user": "admin", "project": "admin", "wsl_distro": "openmove-lab"}
  lab-secret.json  {"password": "..."}      the lab-only password, never printed and never written to the journal
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

LAB_KIND = "lab"
DISTRO = "openmove-lab"
SOURCE_BACKEND = "sf-sim"       # the Cinder back-end that stands in for the source array
TARGET_BACKEND = "pure-sim"     # ... and the target array


class LabError(Exception):
    """The lab is not set up or not reachable. The message says what to do."""


def lab_dir():
    d = os.environ.get("OPENMOVE_LAB_DIR")
    if d:
        return Path(d)
    return Path(r"C:\OpenMoveLab") if os.name == "nt" else Path.home() / ".openmove-lab"


def load_config():
    d = lab_dir()
    try:
        cfg = json.loads((d / "lab.json").read_text(encoding="utf-8-sig"))
        secret = json.loads((d / "lab-secret.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise LabError("the lab is not set up: %s needs lab.json and lab-secret.json. Run  OpenMove-Lab.bat setup" % d) from None
    if not cfg.get("auth_url") or not secret.get("password"):
        raise LabError("lab.json needs auth_url and lab-secret.json needs password (folder %s)" % d)
    return dict(cfg, password=secret["password"])


def home_dir():
    return lab_dir() / "openmove-home"


def reachable(auth_url, timeout=5):
    try:
        with urllib.request.urlopen(auth_url.rstrip("/") + "/v3", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def connect(state):
    """Log in to the lab cloud and detect the platform. Records a `lab` event. Returns the config without the password."""
    from ..connectors.openstack import OpenStackSession
    cfg = load_config()
    if not reachable(cfg["auth_url"]):
        raise LabError("the lab cloud does not answer at %s. Start it with  OpenMove-Lab.bat up" % cfg["auth_url"])
    session = OpenStackSession(cfg["auth_url"], cfg.get("user", "admin"), cfg["password"], cfg.get("project", "admin"))
    info = session.authenticate()
    with state.lock:
        state.session = session
        state.lab = True
        state.access = "api"
    state.journal.record(LAB_KIND, "connect", {"note": "OpenMove lab: a real OpenStack on this machine, not a customer cloud",
                                              "auth_url": cfg["auth_url"], "roles": info["roles"]})
    return {k: v for k, v in cfg.items() if k != "password"}
