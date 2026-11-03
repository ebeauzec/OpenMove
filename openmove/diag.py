# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Diagnostics for dark sites (docs/DIAGNOSTICS.md).

OpenMove is used where the developer cannot log in. When anything goes wrong, everything needed to find and fix the fault must be on the
machine and must be easy to carry out. This module keeps three things:

  logs/openmove.log   one line per event the program itself thinks matters (start, a run, a stage, a refusal, a surprise, every traceback)
  logs/calls.jsonl    one line per command OpenMove ran on any host: the exact command, how long it took, its exit code and its COMPLETE output
  the support bundle  one zip file with the journal, the steps, the evidence of every volume, the call trace, the log, the settings, the
                      readiness of every host, what each host reports about itself (devices, logical volumes, jobs, kernel messages, the
                      Cinder configuration without secrets, the Cinder log lines about the volumes of the run) and the versions of everything.

Redaction (redact.py) is an option for both the files and the bundle: none, standard or strict. The bundle is redacted when it is built, so
a site can keep complete logs on its own disk and still send out a bundle that holds no customer data. The map from token to original stays
in the data folder and is never put in a bundle.

Nothing here changes a cloud, a host or a volume. Collecting from a host runs read-only commands.
"""
import hashlib
import io
import json
import os
import platform
import sys
import time
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from . import redact as redact_mod
from .connectors import ssh as ssh_mod

MAX_LOG = 20 * 1024 * 1024
KEEP = 5
SETTINGS = {"redaction": "standard", "log_redaction": "none"}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


class Diag:
    def __init__(self, home):
        self.home = Path(home)
        self.logs, self.dir = self.home / "logs", self.home / "diag"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.settings = dict(SETTINGS)
        try:
            self.settings.update(json.loads((self.dir / "diag.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        self.live = self._live_redactor()

    # ---- settings --------------------------------------------------------------------------------------------------------------
    def _live_redactor(self):
        lv = redact_mod.level_of({"redaction": self.settings.get("log_redaction")}) if self.settings.get("log_redaction") in redact_mod.LEVELS else "none"
        return redact_mod.Redactor(lv, self.dir / "redaction-map.json")

    def set_settings(self, redaction=None, log_redaction=None):
        for k, v in (("redaction", redaction), ("log_redaction", log_redaction)):
            if v is not None:
                if v not in redact_mod.LEVELS:
                    raise ValueError("%s must be one of %s" % (k, ", ".join(redact_mod.LEVELS)))
                self.settings[k] = v
        (self.dir / "diag.json").write_text(json.dumps(self.settings, indent=1), encoding="utf-8")
        self.live = self._live_redactor()
        self.log("info", "diagnostics settings changed", **self.settings)
        return dict(self.settings)

    # ---- the log and the call trace -----------------------------------------------------------------------------------------------
    def _append(self, name, line):
        path = self.logs / name
        try:
            if path.exists() and path.stat().st_size > MAX_LOG:
                for i in range(KEEP, 0, -1):
                    src = path.with_name("%s.%d" % (name, i - 1)) if i > 1 else path
                    if src.exists():
                        src.replace(path.with_name("%s.%d" % (name, i)))
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass                                    # a full disk must not stop a migration; the journal is the record

    def log(self, level, message, **data):
        rec = {"at": now(), "level": level, "msg": message}
        if data:
            rec["data"] = data
        if self.live.level != "none":
            rec = self.live.data(rec)
            self.live.save()
        self._append("openmove.log", json.dumps(rec, sort_keys=True, default=str))

    def exception(self, where, exc=None):
        exc = exc or sys.exc_info()[1]
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-6000:] if exc is not None else ""
        self.log("error", "%s: %s: %s" % (where, type(exc).__name__, exc), trace=tb)

    def trace(self, entry):
        rec = dict(entry, at=now())
        if self.live.level != "none":
            rec = self.live.data(rec)
            self.live.save()
        self._append("calls.jsonl", json.dumps(rec, sort_keys=True, default=str))

    def attach(self):
        """Route the SSH connector's trace into the call log."""
        ssh_mod.TRACE = self.trace
        self.log("info", "OpenMove %s started" % __version__, python=sys.version.split()[0], platform=platform.platform())

    # ---- the bundle ------------------------------------------------------------------------------------------------------------
    def environment(self):
        ssh_v = ""
        try:
            rc, out, err = ssh_mod.run_local([ssh_mod.tool("ssh"), "-V"], timeout=10)
            ssh_v = (err or out).decode("utf-8", "replace").strip()
        except Exception as exc:
            ssh_v = "not available: %s" % exc
        free = None
        try:
            import shutil
            free = shutil.disk_usage(str(self.home)).free
        except OSError:
            pass
        return {"openmove": __version__, "python": sys.version, "platform": platform.platform(), "machine": platform.machine(), "ssh_client": ssh_v,
                "time_utc": now(), "time_local": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "data_folder_free_bytes": free,
                "env_names": sorted(k for k in os.environ if k.startswith("OPENMOVE")), "frozen": bool(getattr(sys, "frozen", False)),
                "argv": [os.path.basename(sys.argv[0])] + sys.argv[1:]}

    def bundle(self, journal, extras=None, level=None, remote=True, reason="", hosts=None, volumes=None, out_dir=None):
        """Build the support bundle. Returns {'path', 'files', 'redaction', 'bytes'}.

        journal: the open Journal. extras: {name: JSON-like} the running program adds (inventory summary, checks, engine view, wizard).
        hosts: [Host] to collect from (read-only). volumes: ids of the volumes of interest, for the Cinder log lines."""
        level = level or self.settings.get("redaction", "standard")
        red = redact_mod.Redactor(level, self.dir / "redaction-map.json")
        extras = dict(extras or {})
        files = {}
        notes = []

        events = list(reversed(journal.events(None, None, 5000000)))
        steps = list(reversed(journal.steps(None, 5000000)))
        # names to hide wherever they appear in free text
        names = set()
        for e in events:
            d = e.get("data") or {}
            for k in ("by", "customer", "prepared_by", "operator", "reviewer", "name"):
                if isinstance(d.get(k), str):
                    names.add(d[k])
        inv = (extras.get("inventory") or {}).get("volumes") if isinstance(extras.get("inventory"), dict) else None
        for v in inv or []:
            names.update(x for x in (v.get("name"), v.get("project_name")) if x)
        for h in hosts or []:
            names.update(x for x in (h.spec.get("name"), h.spec.get("address"), h.spec.get("user")) if x)
        red.learn("name", sorted(names))

        def put(path, content):
            if isinstance(content, (dict, list)):
                content = json.dumps(red.data(content), indent=1, sort_keys=True, default=str)
            elif isinstance(content, bytes):
                content = content.decode("utf-8", "replace")
            if not isinstance(content, str):
                content = str(content)
            files[path] = red.lines(content) if level != "none" else content

        put("environment.json", self.environment())
        put("settings.json", dict(self.settings, reason=reason, redaction_applied=level))
        put("journal/events.jsonl", "\n".join(json.dumps(red.data(e), sort_keys=True, default=str) for e in events))
        put("journal/steps.jsonl", "\n".join(json.dumps(red.data(s), sort_keys=True, default=str) for s in steps))
        for name, obj in extras.items():
            try:
                put("state/%s.json" % name, obj)
            except Exception as exc:
                notes.append("could not write %s: %s" % (name, exc))
        ev_root = self.home / "evidence"
        if ev_root.is_dir():
            for p in sorted(ev_root.rglob("*")):
                if p.is_file() and p.stat().st_size < 5 * 1024 * 1024:
                    rel = p.relative_to(ev_root).as_posix()
                    try:
                        text = p.read_text(encoding="utf-8")
                        if p.suffix == ".json":
                            try:
                                put("evidence/" + rel, json.loads(text))
                                continue
                            except ValueError:
                                pass
                        put("evidence/" + rel, text)
                    except (OSError, UnicodeDecodeError) as exc:
                        notes.append("could not read evidence %s: %s" % (rel, exc))
        for name in ("openmove.log", "calls.jsonl"):
            for i in range(KEEP, -1, -1):
                p = self.logs / (name if i == 0 else "%s.%d" % (name, i))
                if p.exists():
                    try:
                        put("logs/" + p.name, p.read_text(encoding="utf-8", errors="replace")[-12 * 1024 * 1024:])
                    except OSError as exc:
                        notes.append("could not read %s: %s" % (p.name, exc))
        if remote:
            for h in hosts or []:
                roles = h.spec.get("roles") or [h.spec.get("role")]
                for role in roles:
                    try:
                        if role == "cinder":
                            ids = list(volumes or [])
                            recs = sorted({e["data"].get("dest_id") for e in events if e["kind"] == "stage" and e["data"].get("dest_id")})
                            res = h.cinder("diag", {"volumes": ids[:60], "records": recs[:60], "log_paths": h.spec.get("log_paths")}, timeout=180)
                        else:
                            res = h.mover("diag", {}, timeout=180)
                        put("hosts/%s.%s.json" % (h.name or "host", role), res)
                    except Exception as exc:
                        self.exception("collecting from host %s (%s)" % (h.name, role), exc)
                        put("hosts/%s.%s.error.txt" % (h.name or "host", role), "%s: %s\n%s" % (type(exc).__name__, exc, traceback.format_exc()))
        if notes:
            put("collection-notes.txt", "\n".join(notes))
        red.save()
        counts = {}
        for k in red.map:
            counts[k.split(":", 1)[0]] = counts.get(k.split(":", 1)[0], 0) + 1
        manifest = {"bundle_format": 1, "openmove": __version__, "created": now(), "reason": reason, "redaction": level, "tokens_by_kind": counts,
                    "files": {p: {"bytes": len(c.encode("utf-8")), "sha256": hashlib.sha256(c.encode("utf-8")).hexdigest()} for p, c in sorted(files.items())}}
        files["manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True)
        files["README.txt"] = README % {"level": level, "when": manifest["created"]}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        folder = Path(out_dir) if out_dir else self.home / "bundles"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("openmove-support-%s-%s.zip" % (stamp, level))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, content in sorted(files.items()):
                z.writestr(name, content)
        path.write_bytes(buf.getvalue())
        self.log("info", "support bundle written", path=str(path), redaction=level, files=len(files), bytes=len(buf.getvalue()), reason=reason)
        return {"path": str(path), "files": len(files), "redaction": level, "bytes": len(buf.getvalue()), "tokens": counts}


README = """OpenMove support bundle
=======================
Created: %(when)s   Redaction: %(level)s

What this is: everything OpenMove knows about a run, so that a fault can be found without access to the site.

Contents
  manifest.json        the list of files with their checksums, the OpenMove version and what was redacted
  environment.json     versions of OpenMove, Python and the SSH client; the clock
  journal/             every event and every step (intent, undo, result) the program recorded, oldest first
  state/               what the screen showed: inventory summary, pre-flight result, engine state, hosts and their readiness
  evidence/            per volume: the state before, the state after, every change sent and its answer
  logs/                the program's own log and the trace of every command run on every host, with complete output
  hosts/               what each migration host and Cinder host reported about itself (logical volumes, jobs, kernel messages,
                       Cinder configuration without secrets, Cinder log lines about the volumes of the run)

Redaction
  none      nothing changed.
  standard  addresses, host names, e-mail addresses, identifiers, names of people, projects, volumes, servers and hosts, user names in
            paths, secrets. The same value always has the same token (ip-3, host-2, uuid-0007, name-12): relations stay visible.
  strict    also back-end, volume type, zone and pool names, and free text cut to 60 characters.
The map from token to original is NOT in this file. To find out what a token stands for, the person at the site runs:
    python -m openmove --decode uuid-0007
Passwords, tokens and keys are never stored by OpenMove; any that appear in a command output are removed at every level above none.
"""


def decode(home, token):
    r = redact_mod.Redactor("standard", Path(home) / "diag" / "redaction-map.json")
    return r.decode(token)
