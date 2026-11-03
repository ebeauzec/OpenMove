# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Does this installation hold everything that class A, B and C moves need? (`python -m openmove --self-check`, docs/DEPLOYMENT.md)

It reads only this machine: the program's own files, the data mover and the Cinder helper that are sent to hosts, the system's OpenSSH client, the data
folder. It does not connect to any host or cloud (the Hosts tab does that). It is the first thing to run after an installation, and the packaged program
and the container image are checked with it too, because a missing data file is otherwise found only in the middle of a run.

Each row is {"status": "ok" | "WARN" | "FAIL", "name", "detail"}. A FAIL means a class of volume cannot be moved; a WARN means something is not set up yet.
"""
import ast
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import hosts as hosts_mod
from .connectors import ssh

PACKAGE = Path(__file__).resolve().parent
# What each class of move needs from the program's own files.
FILES = (
    ("web/index.html", "the interface (all classes)"), ("web/app.js", "the interface (all classes)"), ("web/styles.css", "the interface (all classes)"),
    ("releases.json", "release detection (all classes)"), ("release_profiles.json", "release profiles (all classes)"),
    ("backends.json", "the support matrix (all classes)"), ("phase0_tests.json", "the lab tests (all classes)"),
    ("mover/mover.py", "the data mover (class C)"), ("mover/cinder_helper.py", "the Cinder helper (class C)"),
    ("deploy/prepare_host.sh", "the host setup script (class C)"), ("deploy/configure_storage.sh", "the storage paths setup script (class C)"),
)


def row(status, name, detail):
    return {"status": status, "name": name, "detail": detail}


def run(home):
    rows = []
    rows.append(row("ok" if sys.version_info >= (3, 9) else "FAIL", "Python for OpenMove", "%s (3.9 or later)" % sys.version.split()[0]))
    try:
        ver = (PACKAGE.parent / "version.json").read_text(encoding="utf-8")
    except OSError:
        ver = ""
    try:
        from . import __version__
        rows.append(row("ok", "Version", str(__version__)))
    except Exception:
        rows.append(row("WARN", "Version", "not readable" + (" (version.json is there)" if ver else "")))
    res = PACKAGE / "resources"
    for rel, why in FILES:
        p = res / rel
        rows.append(row("ok" if p.is_file() and p.stat().st_size > 0 else "FAIL", "File " + rel, "%s: %s" % (why, "%d bytes" % p.stat().st_size if p.is_file() else "missing")))
    for rel, minimum in (("mover/mover.py", (3, 6)), ("mover/cinder_helper.py", (3, 6))):
        p = res / rel
        if not p.is_file():
            continue
        src = p.read_bytes()
        try:
            ast.parse(src.decode("utf-8"), filename=rel, feature_version=minimum)
            rows.append(row("ok", "Syntax of " + rel, "valid for Python %d.%d and later (sha256 %s)" % (minimum[0], minimum[1], hashlib.sha256(src).hexdigest()[:12])))
        except (SyntaxError, ValueError) as exc:
            rows.append(row("FAIL", "Syntax of " + rel, "%s: it would not run on the hosts" % exc))
    for tool in ("ssh", "ssh-keygen", "ssh-keyscan"):
        path = shutil.which(tool)
        if not path:
            rows.append(row("FAIL", "SSH client: " + tool, "not found on this machine's path: class C needs OpenSSH (Windows 10 and later: Settings, Optional features; Linux: the openssh-client package)"))
            continue
        ver = ""
        if tool == "ssh":
            try:
                r = subprocess.run([path, "-V"], capture_output=True, timeout=20)
                ver = (r.stderr or r.stdout).decode("utf-8", "replace").strip().splitlines()[0]
            except Exception as exc:
                ver = "version not readable (%s)" % exc
        rows.append(row("ok", "SSH client: " + tool, "%s %s" % (path, ver)))
    try:
        Path(home).mkdir(parents=True, exist_ok=True)
        probe = Path(home) / ".selfcheck"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        free = shutil.disk_usage(str(home)).free
        rows.append(row("ok" if free > 2 * 1024 ** 3 else "WARN", "Data folder", "%s is writable, %.1f GiB free (the journal and evidence need little; a support bundle can need more)" % (home, free / 1024 ** 3)))
    except OSError as exc:
        rows.append(row("FAIL", "Data folder", "%s is not writable: %s" % (home, exc)))
    try:
        pub = ssh.public_key(home)
        rows.append(row("ok" if pub else "WARN", "OpenMove's SSH key", "present" if pub else "not made yet: it is made when the first host is saved, or by  --host-setup"))
    except Exception as exc:
        rows.append(row("WARN", "OpenMove's SSH key", "could not be read: %s" % exc))
    hs = hosts_mod.load(home)
    movers = [h for h in hs if "mover" in (h.get("roles") or [h.get("role")])]
    cinders = [h for h in hs if "cinder" in (h.get("roles") or [h.get("role")])]
    rows.append(row("ok" if movers and cinders else "WARN", "Hosts for class C", "%d migration host(s), %d Cinder host(s) registered%s" % (
        len(movers), len(cinders), "" if movers and cinders else "; class A and B work without them, class C volumes are held back until both exist (docs/DEPLOYMENT.md)")))
    if os.name == "nt" and not shutil.which("ssh"):
        rows.append(row("WARN", "Windows", "ssh.exe is part of Windows 10 and later; install the 'OpenSSH Client' optional feature"))
    return rows
