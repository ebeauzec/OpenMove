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
import http.cookiejar
import json
import os
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urlsplit

LAB_KIND = "lab"
DISTRO = "openmove-lab"
DEFAULT_CONFIG = {"auth_url": "http://localhost/identity", "user": "admin", "project": "admin"}
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
        secret = json.loads((d / "lab-secret.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise LabError("the lab is not set up: %s needs lab-secret.json (the lab-only password). See tools/lab/README.md" % d) from None
    try:                    # lab.json is optional: DevStack on this machine is always reached the same way
        cfg = json.loads((d / "lab.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        cfg = dict(DEFAULT_CONFIG)
    if not cfg.get("auth_url") or not secret.get("password"):
        raise LabError("lab.json needs auth_url and lab-secret.json needs password (folder %s)" % d)
    return dict(cfg, password=secret["password"])


def horizon_url(cfg):
    """The OpenStack dashboard of the lab: the same host as Keystone, path /dashboard (DevStack serves it from the same web server)."""
    u = urlsplit(cfg["auth_url"])
    return "%s://%s/dashboard" % (u.scheme, u.netloc)


def horizon_answers(cfg, timeout=5):
    try:
        with urllib.request.urlopen(horizon_url(cfg) + "/auth/login/", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


class _LoginForm(HTMLParser):
    """The fields of Horizon's login form: hidden inputs (the CSRF token) and the first option of each select (the region)."""

    def __init__(self):
        super().__init__()
        self.fields, self._select = {}, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("name"):
            self.fields.setdefault(a["name"], a.get("value") or "")
        elif tag == "select" and a.get("name"):
            self._select = a["name"]
        elif tag == "option" and self._select and self._select not in self.fields:
            self.fields[self._select] = a.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None


def horizon_session(cfg, timeout=30):
    """Log in to the lab's Horizon on the browser's behalf and return its cookies {name: value}. The lab is a local, throw-away cloud, so the
    login is automatic; the password is used here, on this machine, and never goes into a page or an address."""
    base = horizon_url(cfg)
    origin = "%s://%s" % (urlsplit(base).scheme, urlsplit(base).netloc)
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        page = op.open(base + "/auth/login/", timeout=timeout).read().decode("utf-8", "replace")
        form = _LoginForm()
        form.feed(page)
        fields = dict(form.fields, username=cfg.get("user", "admin"), password=cfg["password"], domain="Default")
        req = urllib.request.Request(base + "/auth/login/", urlencode(fields).encode(), {"Referer": base + "/auth/login/", "Origin": origin})
        op.open(req, timeout=timeout).read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise LabError("could not log in to the lab dashboard at %s: %s" % (base, exc)) from None
    cookies = {c.name: c.value for c in jar}
    if "sessionid" not in cookies:
        raise LabError("the lab dashboard did not accept the lab login (user %s)" % cfg.get("user", "admin"))
    return cookies


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
