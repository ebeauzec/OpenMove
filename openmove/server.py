# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The local web server: the GUI and its JSON API.

It binds to the loopback address by default. Every API call needs the access token that is
printed at start-up (and put in the address that opens the browser), and the Host header must
name this server, which stops a web page on another site from reaching it. The cloud session
lives in memory only.
"""
import hmac
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import hosts as hosts_mod
from . import server_hosts
from . import __version__, discovery, exports, handling, inventory, lifecycle, paths, phase0, profiles, safety, selection, wizard
from . import risk as risk_mod
from . import tree as tree_mod
from . import recovery
from . import audit as audit_mod
from .checks import gate as gate_mod
from .checks import run as checks_run
from .connectors.httpclient import ConnectorError
from .connectors.openstack import OpenStackSession
from .journal import Journal
from .engine.evidence import Evidence
from .reports import after as after_mod
from .reports import build as report_build
from .reports import sheet as sheet_mod
from .reports import docx as report_docx

MAX_REQUEST = 20 * 1024 * 1024
STATIC = {"/app.js": ("app.js", "application/javascript; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8")}


class AppState:
    def __init__(self, home):
        self.home = home
        self.journal = Journal(home / "journal.db")
        self.token = recovery.token(home)          # kept in the data folder: the open page and the printed address still work after a restart
        self.recovery = {"status": "none"}       # what the last stop left behind and how far the restore is (openmove/recovery.py)
        self.scan_args = None      # backend and window of the last inventory read from the API, so a restart can read the same again
        self.lock = threading.RLock()
        self.session = None
        self.platform = None
        self.inventory = None
        self.servers = {}          # instance states from the last inventory
        self.roots = ()            # probable root volumes from the last inventory
        self.previous_ids = None   # volume ids of the inventory before the last one (drift check V10)
        self.audit = None
        self.extra = {}            # volume types, QoS, backups and project names from the last audit
        self.checks = None         # the latest pre-flight run
        self.demo = False
        self.lab = False           # connected to the lab cloud (openmove/lab): a real OpenStack on this machine, not a customer cloud
        self.demo_cloud = None     # the built-in mock cloud, in demonstration mode
        self.demo_scenario = None  # id of the scenario it plays
        self.sim = None            # the simulated migration run, in demonstration mode
        self.selection = None      # the volumes chosen for the batch; None means every admitted volume (the default)
        self.options = None        # the operator's options; None means the defaults
        self.released = set()      # volumes the operator released for cutover
        self.selection_ok = None   # (pre-flight run, selected volumes) the operator confirmed on the Select step; wizard.py
        self.engine = None         # the migration engine (openmove/engine), created on first use
        self.access = "api"        # what OpenMove can reach: "api" (the OpenStack APIs only) or "full" (also hosts and arrays)
        self.diag = None           # the log, the call trace and the support bundle (openmove/diag.py), set by create_server
        self.fabric = None         # the migration host, the Cinder host and the array adapter for the chosen pair, once they are ready (openmove/hosts.py)
        self.host_checks = None    # the result of the last host readiness check
        self.auto_sign = None      # demo only: the made-up second person who signs the volume gates
        self.extra_hosts = ()  # Host header values accepted besides loopback; set with --allow-host
        self._raw = None  # last API collection, kept so a back-end filter can be re-applied without calling the cloud

    def disconnect(self, reason="stopped"):
        with self.lock:
            if self.session:
                self.session.close()
            self.session = self.platform = self.inventory = self._raw = None
            self.audit = self.checks = None
            self.servers, self.roots, self.extra = {}, (), {}
            self.selection = self.options = self.selection_ok = None
            self.released = set()
            if self.engine:
                self.engine.stop(reason)
                self.engine = None


def make_handler(state, port_getter):
    web = paths.resource_dir() / "web"

    class Handler(BaseHTTPRequestHandler):
        server_version = "OpenMove"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # no request lines in the console: they can hold identifiers
            pass

        # ---- helpers ----
        def _send(self, status, body, ctype="application/json; charset=utf-8", extra_headers=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; "
                             "img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self):
            host = (self.headers.get("Host") or "").lower()
            port = port_getter()
            return host in ("127.0.0.1:%d" % port, "localhost:%d" % port, "[::1]:%d" % port) or \
                host in state.extra_hosts

        def _token_ok(self, supplied):
            return bool(supplied) and hmac.compare_digest(supplied.encode(), state.token.encode())

        def _lab_dashboard(self, q):
            """Lab mode only: log in to the lab's OpenStack dashboard for the browser and send it there, already signed in. The browser keeps
            cookies by host name, not by port, so the session cookies set from this address are sent to the dashboard on the same host."""
            if not self._token_ok((q.get("token") or [""])[0]):
                return self._send(403, b"Open the address that OpenMove printed when it started.", "text/plain; charset=utf-8")
            if not state.lab:
                return self._send(404, {"error": "not available outside lab mode"})
            from . import lab as lab_mod
            try:
                cfg = lab_mod.load_config()
            except lab_mod.LabError as exc:
                return self._send(502, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
            cloud = urlsplit(cfg["auth_url"])
            host = (self.headers.get("Host") or "127.0.0.1").rsplit(":", 1)[0]
            if cloud.hostname and host.lower() != cloud.hostname.lower() and ":" not in cloud.hostname:
                # The lab answers on one name only (WSL forwards "localhost" but not always 127.0.0.1), and cookies belong to a host name.
                # So go back to this route under the lab's own name first; the cookies are then set where the dashboard will read them.
                self.send_response(302)
                self.send_header("Location", "http://%s:%d/lab/dashboard?token=%s" % (cloud.hostname, port_getter(), state.token))
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            try:
                cookies = lab_mod.horizon_session(cfg)
            except lab_mod.LabError as exc:
                return self._send(502, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
            port = cloud.port
            self.send_response(302)
            self.send_header("Location", "http://%s%s/dashboard/" % (host, ":%d" % port if port else ""))
            for name, value in cookies.items():
                self.send_header("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Lax" % (name, value))
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_REQUEST:
                raise ValueError("request is too large")
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return {}
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("the request body must be a JSON object")
            return data

        def _log(self, level, status, path, exc):
            if state.diag and path.startswith("/api/"):
                state.diag.log(level, "API %s answered %d: %s" % (path, status, exc))

        # ---- routing ----
        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def _route(self, method):
            if not self._host_ok():
                return self._send(403, {"error": "unexpected Host header"})
            url = urlsplit(self.path)
            path = url.path
            try:
                if method == "GET" and path == "/healthz":                    # is the program alive? (the supervisor and the page ask this; it says nothing else)
                    return self._send(200, {"ok": True, "version": __version__})
                if method == "GET" and path == "/":
                    q = parse_qs(url.query)
                    if not self._token_ok((q.get("token") or [""])[0]):
                        return self._send(403, b"Open the address that OpenMove printed when it started.",
                                          "text/plain; charset=utf-8")
                    html = (web / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", state.token)
                    return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                if method == "GET" and path == "/lab/dashboard":
                    return self._lab_dashboard(parse_qs(url.query))
                if method == "GET" and path in STATIC:
                    name, ctype = STATIC[path]
                    return self._send(200, (web / name).read_bytes(), ctype)
                if not path.startswith("/api/"):
                    return self._send(404, {"error": "not found"})
                if not self._token_ok(self.headers.get("X-OpenMove-Token", "")):
                    return self._send(401, {"error": "missing or wrong access token"})
                handler = ROUTES.get((method, path[len("/api/"):]))
                if not handler:
                    return self._send(404, {"error": "not found"})
                body = self._body() if method == "POST" else {}
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                result = handler(state, body, query)
                if method == "POST":
                    try:
                        recovery.save(state)                                  # whatever the call changed is on disk before the page hears the answer
                    except Exception:
                        pass
                if isinstance(result, Download):
                    return self._send(200, result.data, result.ctype, {
                        "Content-Disposition": 'attachment; filename="%s"' % result.filename})
                return self._send(200, result)
            except ApiError as exc:
                self._log("info", exc.status, path, exc)
                return self._send(exc.status, {"error": str(exc)})
            except ConnectorError as exc:
                self._log("warn", 502, path, exc)
                return self._send(502, {"error": str(exc)})
            except (ValueError, KeyError) as exc:
                self._log("info", 400, path, exc)
                return self._send(400, {"error": str(exc)})
            except Exception as exc:  # a defect: say so, without internals on the screen, and keep everything in the log
                if state.diag:
                    state.diag.exception("%s %s" % (method, path), exc)
                return self._send(500, {"error": "internal error (%s); the details are in logs/openmove.log. Make a support bundle from Diagnostics." % type(exc).__name__})

    return Handler


class Download:
    """A handler result that is a file, not JSON."""
    def __init__(self, data, filename, ctype):
        self.data, self.filename, self.ctype = data, filename, ctype


class ApiError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


# ---- API handlers: (state, body, query) -> JSON-able ---------------------------------------------

def api_status(state, body, query):
    rows = _engine_rows(state)
    waiting = _waiting(state, rows)
    with state.lock:
        g = _gate(state)
        return {"version": __version__, "home": str(state.home), "connected": state.session is not None,
                "project": state.session.project_name if state.session else None, "demo": state.demo, "lab": state.lab,
                "demo_scenario": _demo_info(state), "halted": safety.halted(state.journal),
                "platform": state.platform is not None, "inventory": state.inventory is not None,
                "audit": state.audit is not None, "checks": state.checks is not None, "gate": g["status"] if g else None,
                "read_only": (state.platform or {}).get("read_only", True),
                "unresolved_steps": len(state.journal.unresolved_steps()), "lab_form": _lab_form(state), "recovery": _recovery_view(state), "waiting": waiting, "board": _board(state, rows, waiting),
                "wizard": wizard.steps(state, g)}


def _recovery_view(state):
    r = dict(state.recovery or {"status": "none"})
    e = state.engine
    r["engine"] = {"status": e.status, "trouble": e.trouble, "gaps": e.gaps[-3:], "revived": e.revived} if e else None
    return r


def api_recover(state, body, query):
    """Try again to put the saved work back (after a failed restore, or after signing in to the cloud)."""
    if not state.session:
        raise ApiError("sign in to the cloud first (step 1, Connect)", 409)
    if state.recovery.get("status") not in ("waiting", "failed"):
        return _recovery_view(state)
    if not recovery.start_restore(state):
        raise ApiError(state.recovery.get("message") or "there is nothing saved to put back", 409)
    return _recovery_view(state)


def api_recover_discard(state, body, query):
    """Start fresh: forget the saved work. The history of every step already taken stays."""
    by = (body.get("by") or "").strip()
    if len(by) < 3:
        raise ValueError("forgetting the saved work needs the name of the person who decides")
    recovery.discard(state)
    return _recovery_view(state)


def _lab_form(state):
    """Lab mode only: what the Connect form is filled in with. Never the password; a blank password means the lab's own (see api_connect)."""
    if not state.lab:
        return None
    from . import lab as lab_mod
    try:
        cfg = lab_mod.load_config()
    except lab_mod.LabError:
        return None
    return {"auth_url": cfg["auth_url"], "username": cfg.get("user", "admin"), "project_name": cfg.get("project", "admin"),
            "user_domain": "Default", "project_domain": "Default", "interface": "public"}


def api_halt(state, body, query):
    """Stop all changes at once. Needs a name and a reason; anyone may press it."""
    safety.halt(state.journal, body.get("by"), body.get("reason"))
    return {"halted": safety.halted(state.journal)}


def api_resume(state, body, query):
    safety.resume(state.journal, body.get("by"), body.get("reason"))
    return {"halted": safety.halted(state.journal)}


def make_permit(state, scope=""):
    """What the migration engine calls to get permission to change the cloud. Raises safety.Refused if it may not."""
    def profile():
        return (state.platform or {}).get("profile") or {"status": "untested", "reason": "the platform has not been read"}
    return safety.grant(state.journal, profile, lambda: _gate(state), scope)


def _demo_info(state):
    if not state.demo:
        return None
    from .demo import scenarios
    return dict(scenarios.describe(state.demo_scenario), scenarios=scenarios.listing())


def api_demo_approve(state, body, query):
    """Demo mode only: do the human steps of the gate for a demonstration (attest open items, sign twice), labelled as synthetic.
    A failed check still blocks: it cannot be waived."""
    if not state.demo:
        raise ApiError("not available outside demo mode", 404)
    return _synthetic_approve(state, "Demo operator", "Demo reviewer", "demonstration", api_demo_validate)


def api_lab_approve(state, body, query):
    """Lab mode only: the same shortcut for the lab cloud, labelled as the lab's. It records the lab validation of the lab release
    (gate G0, only for the lab's own profile), attests the open items and signs as two lab users. A failed check still blocks."""
    if not state.lab:
        raise ApiError("not available outside lab mode", 404)
    return _synthetic_approve(state, "Lab operator", "Lab reviewer", "OpenMove lab", _lab_validate)


def _lab_validate(state, body, query):
    plat = _need(state, "platform")
    rel = (plat.get("release") or {}).get("release")
    prof = load_lab_profile(rel)
    if not prof:
        raise ApiError("the lab release %r has no lab profile; the engine will not change a release without one" % rel, 409)
    profiles.record_validation(state.journal, rel, True, {"lab": True, "note": "OpenMove lab (a real OpenStack on this machine). This is not a validation of any customer release.",
                                                       "by": "Lab operator", "second": "Lab reviewer"})
    api_discover(state, {}, {})
    return {"ok": True, "release": rel}


def load_lab_profile(release):
    return (profiles.load_profiles().get(release) or {}) if (profiles.load_profiles().get(release) or {}).get("lab") else None


def _synthetic_approve(state, operator, reviewer, label, validate):
    c = _need(state, "checks")
    if (state.platform or {}).get("profile", {}).get("status") != "validated":
        validate(state, {}, {})
    c = checks_run_again(state, c)
    g = _gate(state)
    if g["status"] == "blocked":
        raise ApiError("the gate is blocked, and a failed check cannot be waived: " + "; ".join(
            "%s %s" % (b["id"], b["title"]) for b in g["blockers"]), 409)
    for item in g["open_items"]:
        gate_mod.attest(state.journal, c["source_backend"], c["target_backend"], item["id"], operator,
                        "%s: synthetic attestation of an open item" % label)
    gate_mod.sign(state.journal, c["run_id"], "operator", operator)
    gate_mod.sign(state.journal, c["run_id"], "reviewer", reviewer)
    return _checks_view(state)


def checks_run_again(state, previous):
    return api_checks_run(state, {"source_backend": previous["source_backend"], "target_backend": previous["target_backend"]}, {})


def api_demo_simulate(state, body, query):
    """Demo mode only: start or stop a simulated migration run (made-up evidence, nothing is sent to any system)."""
    if not state.demo:
        raise ApiError("not available outside demo mode", 404)
    from .demo.simulate import Simulation
    action = body.get("action")
    if action == "autosign":
        state.auto_sign = "Demo reviewer (synthetic)" if body.get("on") else None
        return api_live(state, {}, {})
    if state.sim:
        state.sim.stop()
    if action == "stop":
        return api_live(state, {}, {})
    if action != "start":
        raise ValueError("action must be 'start' or 'stop'")
    sim = Simulation(state, lambda: _gate(state))
    try:
        sim.start()
    except RuntimeError as exc:
        raise ApiError(str(exc), 409)
    state.sim = sim
    return api_live(state, {}, {})


def _event_line(e):
    d, k = e.get("data") or {}, e["kind"]
    if k == "phase":
        extra = d.get("reason") or ("source changed: back to sync" if d.get("regression") else "")
        return "%s enters %s%s" % (e["subject"][-12:], d.get("phase"), (" (%s)" % extra) if extra else "")
    if k == "refusal":
        return "REFUSED %s %s: %s" % (e["subject"][-12:], d.get("attempt"), d.get("reason"))
    if k in ("halt", "resume"):
        return "%s by %s: %s" % (k.upper(), d.get("by"), d.get("reason"))
    return "%s %s" % (k, (e.get("subject") or "")[-24:])


def _unmigrated(state):
    g = _gate(state)
    names = {p["id"]: p["name"] for p in state.extra.get("projects", [])}
    return handling.unmigrated(state.journal, _need(state, "inventory"), _need(state, "checks"), state.servers, names,
                               halted=bool(safety.halted(state.journal)), gate_ok=bool(g and g["status"] == "approved"),
                               selected=set(selection.selected_ids(state)))


def api_unmigrated(state, body, query):
    """Every volume that is not migrated, with the cause, how it happened and the proposed steps (requirement R15)."""
    data = _unmigrated(state)
    causes = {}
    for r in data["rows"]:
        c = causes.setdefault(r["cause"], {"code": r["cause"], "title": r["title"], "why": r["why"], "source": r["source"], "target": r["target"],
                                           "steps": r["steps"], "who": r["who"], "count": 0})
        c["count"] += 1
    data = dict(data, causes=sorted(causes.values(), key=lambda c: -c["count"]))
    data["rows"] = [dict(r, timeline=r["timeline"][-12:]) for r in data["rows"][:400]]
    data["rows_more"] = max(0, data["total"] - data["migrated"] - len(data["rows"]))
    return data


def _failed_checks(state):
    failed = {}
    for c in (state.checks or {}).get("volume", []):
        for f in c.get("findings", []) or []:
            if f.get("status") == "fail":
                failed.setdefault(f["volume"], []).append(c["id"])
    return failed


def api_selection_get(state, body, query):
    """What is selected for the batch, the groups to select by, the options in force and their defaults (requirement R16)."""
    inv, checks = _need(state, "inventory"), _need(state, "checks")
    names = {p["id"]: p["name"] for p in state.extra.get("projects", [])}
    el = selection.eligible_ids(state)
    sel = set(selection.selected_ids(state))
    failed = _failed_checks(state)
    vols, gib = [], 0
    for v in inv["volumes"][:3000]:
        why = ""
        if v["id"] not in el:
            why = "class %s: %s" % (v.get("class"), v.get("reason") or "not admitted")
            if failed.get(v["id"]):
                why += " (failed check %s)" % ", ".join(failed[v["id"]])
        if v["id"] in sel:
            gib += int(v.get("size") or 0)
        vols.append({"id": v["id"], "name": v.get("name") or "", "class": v.get("class"), "project": names.get(v.get("project_id")) or str(v.get("project_id") or "")[:8],
                     "size": v.get("size"), "snapshots": v.get("snapshots") or 0, "attached": [(state.servers.get(a.get("server_id")) or {}).get("name") or a.get("server_id") or ""
                                                                                             for a in v.get("attachments", [])],
                     "eligible": v["id"] in el, "selected": v["id"] in sel, "why": why})
    return {"eligible": len(el), "selected": len(sel), "selected_gib": gib, "default": state.selection is None, "total": len(inv["volumes"]),
            "groups": selection.groups(inv, names, state.servers, state.roots, el, checks.get("risk")), "group_kinds": [list(k) for k in selection.KINDS],
            "options": selection.options_of(state), "defaults": selection.defaults(state.demo), "options_default": state.options is None,
            "limits": {k: [lo, hi, text] for k, (lo, hi, text) in selection.LIMITS.items()}, "labels": selection.LABELS,
            "volumes": vols, "running": bool(state.sim and state.sim.running), "released": len(state.released)}


def api_tree(state, body, query):
    """The volumes as a tree: project, instance, attached volumes, and the volumes that are not attached. Read only; it needs only the inventory.
    Once the pre-flight has run each volume also says whether it will be moved, and why not."""
    inv = _need(state, "inventory")
    names = {p["id"]: p["name"] for p in state.extra.get("projects", [])}
    eligible = selected = None
    failed = {}
    if state.checks:
        eligible, selected, failed = set(selection.eligible_ids(state)), set(selection.selected_ids(state)), _failed_checks(state)
    return tree_mod.build(inv, state.servers, names, state.roots, eligible, selected, failed)


def api_selection_confirm(state, body, query):
    """The operator confirms the selection as it stands. It opens the Move step, and it is void as soon as the selection or the pre-flight run changes."""
    inv = _need(state, "inventory")
    _need(state, "checks")
    g = _gate(state)
    if not g or g["status"] != "approved":
        raise ApiError("the pre-flight gate is not approved: finish step 5 first", 409)
    by = (body.get("by") or "").strip()
    if len(by) < 3:
        raise ValueError("confirming the selection needs the name of the person who confirms it")
    chosen = selection.selected_ids(state)
    if not chosen:
        raise ApiError("nothing is selected: select at least one volume, or stop here", 409)
    wizard.confirm_selection(state)
    state.journal.record("selection_confirmed", "batch", {"by": by, "run_id": state.checks.get("run_id"), "volumes": len(chosen), "default": state.selection is None})
    return {"ok": True, "volumes": len(chosen)}


def api_selection_set(state, body, query):
    """Change what is selected (by volume, by group, all, none or back to the default) and/or the options. Every change is journaled."""
    inv = _need(state, "inventory")
    _need(state, "checks")
    action, refused = body.get("action"), []
    if state.sim and state.sim.running and action:
        raise ApiError("a run is in progress: stop it before changing the selection (options can be changed at any time)", 409)
    by = (body.get("by") or "").strip()
    record = {"by": by or "operator"}
    if body.get("reset_options") or body.get("options"):
        merged = {} if body.get("reset_options") else dict(state.options or {})
        merged.update(body.get("options") or {})
        selection.check_options(merged, state.demo)               # raises SelectionError (a bad value is refused, never clamped)
        state.options = merged or None
        record["options"] = selection.options_of(state)
    if action:
        el = selection.eligible_ids(state)
        known = {v["id"] for v in inv["volumes"]}
        names = {p["id"]: p["name"] for p in state.extra.get("projects", [])}
        ids = list(body.get("ids") or [])
        grp = body.get("group")
        if grp:
            ids += selection.members(inv, names, state.servers, state.roots, grp.get("kind"), grp.get("key"), el, (state.checks or {}).get("risk"))
            record["group"] = {"kind": grp.get("kind"), "key": grp.get("key")}
        if action not in ("set", "add", "remove", "reset", "none"):
            raise ValueError("action must be set, add, remove, reset or none")
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise ValueError("unknown volume id: %s" % unknown[0])
        if action in ("set", "add"):
            refused = [{"id": i, "reason": "not admitted by the pre-flight"} for i in ids if i not in el]
        cur = set(selection.selected_ids(state))
        new = set() if action == "none" else selection.apply(cur, el, action, ids)
        state.selection = None if action == "reset" else new
        record.update({"action": action, "count": len(selection.selected_ids(state)), "ids": ids[:50], "ids_total": len(ids)})
    state.journal.record("selection", "batch", record)
    out = api_selection_get(state, {}, {})
    out["refused"] = refused
    return out


def api_release(state, body, query):
    """Release volumes that are in sync and checked so that their cutover may be attempted. The cutover rules still all apply."""
    by = (body.get("by") or "").strip()
    if len(by) < 3:
        raise ValueError("releasing needs the name of the person who releases")
    sel = selection.selected_ids(state)
    if state.engine is not None and not state.demo:         # a volume already in flight is released even when today's choice no longer holds it (a restart, a changed choice)
        sel = list(dict.fromkeys(list(sel) + [r["id"] for r in _engine_rows(state)]))
    if body.get("all"):
        ids = list(sel)
    else:
        inv = _need(state, "inventory")
        names = {p["id"]: p["name"] for p in state.extra.get("projects", [])}
        ids = list(body.get("ids") or [])
        grp = body.get("group")
        if grp:
            ids += selection.members(inv, names, state.servers, state.roots, grp.get("kind"), grp.get("key"), set(sel), (state.checks or {}).get("risk"))
    ids = [i for i in ids if i in set(sel)]
    if not ids:
        raise ValueError("no selected volume to release")
    state.released |= set(ids)
    state.journal.record("release", "batch", {"by": by, "count": len(ids), "ids": ids[:50]})
    return api_live(state, {}, {})


def _engine(state):
    if state.demo:
        raise ApiError("the engine does not run in demonstration mode; the simulation in step 6, Progress, shows the process", 409)
    _need(state, "checks")
    if state.engine is None:
        from .engine.runner import Engine
        state.engine = Engine(state, lambda: make_permit(state, "engine"), lambda: _gate(state))
    return state.engine


def _refusals(fn):
    """A permit that says no, or an unknown outcome, is a 409 with the reason, never a 500."""
    from .engine.actions import OutcomeUnknown
    try:
        return fn()
    except (safety.Refused, OutcomeUnknown, profiles.ReadOnlyRelease, gate_mod.GateClosed) as exc:
        raise ApiError(str(exc), 409) from None


def api_engine_get(state, body, query):
    if state.engine is None:
        return {"status": "idle", "ticks": 0, "queued": 0, "active": 0, "finished": 0, "failed": 0, "volumes": [], "more": 0, "access": state.access}
    return dict(state.engine.view(), access=state.access)


def api_engine_dry_run(state, body, query):
    """What a run would do, from fresh reads. Reads only: it changes nothing and needs no permit."""
    return _engine(state).dry_run(body.get("ids") or None, body.get("type_map") or None)


def _when(text, name):
    """A UTC time for the run window: 2026-11-07T22:00:00Z (the Z is optional), or empty for no limit."""
    text = str(text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError("%s must be a UTC time like 2026-11-07T22:00:00Z" % name)


def api_engine_start(state, body, query):
    """Start moving the selected class A and B volumes by Cinder's retype. Needs a permit now: validated release, approved gate, no halt."""
    by = (body.get("by") or "").strip()
    if len(by) < 3:
        raise ValueError("starting a run needs the name of the person who starts it")
    e = _engine(state)
    why = wizard.start_refusal(state, e, body.get("ids"))
    if why:
        raise ApiError(why, 409)
    window = (_when(body.get("start_at"), "start_at"), _when(body.get("stop_at"), "stop_at"))
    _refusals(lambda: e.start(body.get("ids") or None, body.get("type_map") or None, window=window))
    state.journal.record("engine_start", "batch", {"by": by, "queued": len(e.queue), "resumed": len(e.active),
                                                   "start_at": body.get("start_at") or None, "stop_at": body.get("stop_at") or None})
    return e.view()


def api_engine_stop(state, body, query):
    if state.engine:
        state.engine.stop()
    return api_engine_get(state, {}, {})


def api_engine_reverse(state, body, query):
    """Put a moved volume back with a retype to its first type (R3). With `preview` only show what it would do; with `all` put the whole run back."""
    e = _engine(state)
    if body.get("preview"):                                   # R6: see what a reverse would do before it runs; nothing is changed
        return e.reverse_preview([str(body["id"])] if body.get("id") else None)
    if body.get("all"):                                       # R6: the whole run
        ids = e.reverse_all(body.get("reason"), body.get("by"))
        state.journal.record("engine_reverse_all", "batch", {"by": str(body.get("by") or "").strip(), "reason": str(body.get("reason") or "").strip(), "volumes": len(ids)})
    else:
        e.reverse(str(body.get("id") or ""), body.get("reason"), body.get("by"))
    return e.view()


def api_validation(state, body, query):
    """Record the lab validation of the release (gate G0): two different people say that the lab tests were run and what came out.
    This is what lifts the read-only state of the release. Nothing is tested here: it is the people's declaration, kept in the journal."""
    rel = (_need(state, "platform").get("release") or {}).get("release")
    if not rel:
        raise ApiError("the release is not identified", 409)
    by, second = (body.get("by") or "").strip(), (body.get("second") or "").strip()
    note = (body.get("evidence") or "").strip()
    if len(by) < 3 or len(second) < 3 or by.lower() == second.lower():
        raise ValueError("the validation needs two different people")
    if body.get("from_tests"):
        note = phase0.evidence_text(state.journal, rel)           # refuses unless every test of the retype path has passed for this release
        body = dict(body, passed=True)
    if len(note) < 40:
        raise ValueError("describe what was tested and what came out (at least 40 characters): which lab tests, on which cloud, with which result")
    passed = bool(body.get("passed"))
    profiles.record_validation(state.journal, rel, passed, {"by": by, "second": second, "evidence": note, "environment": (body.get("environment") or "test")[:20],
                                                           "auth_url": state.session.auth_url if state.session else None})
    api_discover(state, {}, {})
    return {"ok": True, "release": rel, "result": "passed" if passed else "failed"}


def _release_of(state):
    rel = (_need(state, "platform").get("release") or {}).get("release")
    if not rel:
        raise ApiError("the release is not identified", 409)
    return rel


def api_phase0_get(state, body, query):
    """The Phase 0 lab tests as guided procedures, with the recorded result of each for this release (gate G0 can be recorded from them)."""
    return phase0.status(state.journal, _release_of(state))


def api_phase0_record(state, body, query):
    """Record the result of one Phase 0 test, with a name and what was done and seen. The newest result of a test counts."""
    rel = _release_of(state)
    phase0.record(state.journal, rel, str(body.get("test") or ""), str(body.get("result") or ""), body.get("by") or "", body.get("evidence") or "",
                  body.get("environment") or "test")
    return phase0.status(state.journal, rel)


def api_volume_gate(state, body, query):
    """Sign (or withdraw) G2, G3 or G4 for volumes, as a second person who is not the operator of the run. Each volume is checked on its own:
    a volume that is not at the right point is refused with the reason. With `all`, volumes not at that point are skipped quietly."""
    name = str(body.get("gate") or "").upper()
    if name not in lifecycle.VOLUME_GATES:
        raise ValueError("gate must be G2, G3 or G4")
    withdraw = bool(body.get("withdraw"))
    ids = list(body.get("ids") or [])
    every = bool(body.get("all"))
    if every:
        ids = list(lifecycle.states(state.journal))
    if not ids:
        raise ValueError("choose at least one volume that has started")
    g = _gate(state)
    now = state.sim.vnow if state.sim else None
    rules = selection.rules(selection.options_of(state))
    signed, refused, skipped = [], [], 0
    for vid in ids:
        if every and not withdraw and lifecycle.gate_signed(state.journal, vid, name, g):
            skipped += 1
            continue
        try:
            lifecycle.sign_gate(state.journal, vid, name, body.get("by"), body.get("evidence"), g, withdraw, now, rules)
            signed.append(vid)
        except lifecycle.LifecycleError as exc:
            if every and "is signed" in str(exc):
                skipped += 1
            elif every and "cannot be signed yet" in str(exc):
                skipped += 1
            else:
                refused.append({"id": vid, "reason": str(exc)})
    if not signed and refused:
        raise ValueError(refused[0]["reason"])
    out = api_live(state, {}, {})
    out.update({"signed": len(signed), "skipped": skipped, "refused": refused[:20]})
    return out


def api_volume(state, body, query):
    """Everything about one volume: its path, where it is on it, what is happening now, what blocks the cutover (requirement R18)."""
    inv = _need(state, "inventory")
    vid = query.get("id")
    vol = next((v for v in inv["volumes"] if v["id"] == vid), None)
    if not vol:
        raise ApiError("no such volume in the inventory", 404)
    gate = _gate(state)
    opts = selection.options_of(state)
    sim = state.sim
    urow = None
    if state.checks:
        urow = next((r for r in _unmigrated(state)["rows"] if r["volume"] == vid), None)
    phase = lifecycle.phase_of(state.journal, vid)
    cond = []
    if phase in ("sync", "recheck"):
        cond = lifecycle.readiness(state.journal, vid, gate, sim.vnow if sim else None, selection.rules(opts))
    view = handling.volume_view(state.journal, vol, sim.notes.get(vid) if sim else None, urow, cond)
    risk = ((state.checks or {}).get("risk") or {}).get(vid)
    view.update({"risk": risk, "selected": vid in set(selection.selected_ids(state)), "released": vid in state.released,
                 "attached_to": [(state.servers.get(a.get("server_id")) or {}).get("name") or a.get("server_id") or "" for a in vol.get("attachments", [])],
                 "simulated": bool(sim), "held_for_release": bool(risk and risk["level"] == "high") or opts["hold_before_cutover"]})
    return view


STAGES = (("queued", "waiting to start"), ("copy", "being copied and checked"), ("switch", "being switched"), ("verify", "switched, being verified"),
          ("soak", "in the safety period"), ("done", "finished"), ("stopped", "stopped or put back"))
STAGE_OF = {"queued": "queued", "precheck": "copy", "validate": "copy", "sync": "copy", "copy": "copy", "recheck": "copy", "cutover": "switch", "postcheck": "verify",
            "soak": "soak", "done": "done", "aborted": "stopped", "rollback": "stopped"}


def _engine_rows(state):
    if state.demo or state.engine is None:
        return []
    try:
        return state.engine.view()["volumes"]
    except Exception:
        return []


def _board(state, rows, waiting):
    """The three things shown at the top of every page: the status of the move, how far it has got, and what a person has to do. Plain words, one level
    (ok, warn, bad, idle) that decides the colour."""
    if state.demo or state.session is None:
        return None
    e, h = state.engine, safety.halted(state.journal)
    counts = {}
    for r in rows:
        k = STAGE_OF.get(r["phase"], "copy")
        counts[k] = counts.get(k, 0) + 1
    total = len(rows)
    switched = sum(counts.get(k, 0) for k in ("verify", "soak", "done"))
    failed = sum(1 for r in rows if r.get("failed"))
    active = total - counts.get("done", 0) - counts.get("soak", 0) - counts.get("stopped", 0) - counts.get("queued", 0)
    pct = round(sum(lifecycle.progress(r["phase"]) for r in rows if r["phase"] != "queued") / total, 1) if total else 0
    status = (e.status if e else "idle") or "idle"
    n = len(waiting)
    if h:
        level, name, detail = "bad", "Everything is stopped", "%s pressed the emergency stop: %s. Nothing is changed until someone allows changes again." % (h["by"], h["reason"])
    elif status.startswith("stopped after an error"):
        level, name, detail = "bad", "Stopped by an error", "%s. Nothing was lost; start the move again from step 5, Move." % status
    elif not rows and status in ("idle", "finished"):
        level, name, detail = "idle", "No move is running", "Nothing is moving. Go on with the steps; the move is started in step 5, Move."
    elif e and e.trouble:
        level, name, detail = "warn", "Trying again", "The move hit a problem and tries again by itself: %s" % e.trouble
    elif n:
        level, name, detail = "warn", "Waiting for you", "%d thing%s only a person can do. Nothing else is holding the move back." % (n, "" if n == 1 else "s")
    elif status == "finished" or (rows and active == 0 and not counts.get("queued")):
        level, name, detail = "ok", "Finished", "Every volume of this move has reached its end."
    elif status == "stopped":
        level, name, detail = "warn", "Stopped by a person", "No new volume is taken. Volumes already sent carry on in the cloud."
    else:
        bits = []
        if active:
            bits.append("%d being worked on" % active)
        if counts.get("queued"):
            bits.append("%d waiting for a free place" % counts["queued"])
        if counts.get("soak"):
            bits.append("%d in the safety period" % counts["soak"])
        level, name, detail = "ok", "Moving", "%s. Nothing needs you right now." % (", ".join(bits) or "Working")
    return {"level": level, "state": name, "detail": detail, "total": total, "switched": switched, "active": active, "failed": failed, "percent": min(100.0, pct),
            "stages": [{"id": k, "label": label, "count": counts[k]} for k, label in STAGES if counts.get(k)], "actions": waiting}


def _waits(state, rows):
    """What a real move waits for, from the engine's rows: (volumes that need a second person for G2 and G3, volumes that wait for the release, volumes that need a second person for G4)."""
    wait23 = held = wait4 = 0
    for r in rows:
        ph, gs = r["phase"], r["gates"]
        if ph in ("validate", "recheck") and not (gs["G2"] and gs["G3"]):
            wait23 += 1
        elif ph in ("validate", "recheck") and r["id"] not in state.released:
            held += 1
        elif ph == "postcheck" and not gs["G4"]:
            wait4 += 1
    return wait23, held, wait4


def _operator(state):
    e = state.journal.latest("engine_start")
    return ((e or {}).get("data") or {}).get("by") or ""


def _waiting(state, rows):
    """Everything a person has to do before the move can go on, in plain words, and where to do it. Empty when nothing waits for a person."""
    if not rows:
        return []
    w23, held, w4 = _waits(state, rows)
    who = _operator(state)
    other = ("%s started the move and cannot approve it." % who) if who else "The person who started the move cannot approve it."
    out = []
    if w23:
        out.append({"kind": "g23", "count": w23, "tab": "live", "button": "sign-g23",
                    "title": "%d volume%s waiting for a second person to approve %s before %s switched" % (w23, " is" if w23 == 1 else "s are", "it" if w23 == 1 else "them", "it is" if w23 == 1 else "they are"),
                    "text": other,
                    "action": "Press \"Approve before switching\" (it pulses). A second person looks at them first (the copy is correct and the volume is ready to switch)."})
    if held:
        out.append({"kind": "release", "count": held, "tab": "live", "button": "live-release",
                    "title": "%d volume%s approved and waiting for you to release %s" % (held, " is" if held == 1 else "s are", "it" if held == 1 else "them"),
                    "text": "Nothing is switched until you say so.", "action": "Press \"Release all volumes that are ready to switch\" (it pulses)."})
    if w4:
        out.append({"kind": "g4", "count": w4, "tab": "live", "button": "sign-g4",
                    "title": "%d volume%s switched over and waiting for a second person to approve the result" % (w4, " is" if w4 == 1 else "s are"),
                    "text": other,
                    "action": "Press \"Approve the result\" (it pulses). A second person confirms the volume works on its new storage."})
    return out


def api_live(state, body, query):
    """What is happening now: per-phase counts, per-volume progress, the gate, the halt, and the newest journal events."""
    st = lifecycle.states(state.journal)
    inv = {v["id"]: v for v in (state.inventory or {}).get("volumes", [])}
    admitted = selection.selected_ids(state)
    sim = state.sim
    mine = {}                                          # the real engine: only the volumes it holds count, and what each one waits for comes from the engine
    if state.engine is not None and not state.demo:
        mine = {r["id"]: r for r in state.engine.view()["volumes"]}
        st = {k: st.get(k) or {"phase": "queued", "at": None} for k in mine}
    opts = selection.options_of(state)
    ready = {v for v, pl in (sim._plan.items() if sim else ()) if pl.get("stage") == "ready"}
    g4wait = {v for v, pl in (sim._plan.items() if sim else ()) if pl.get("stage") == "g4wait"}
    copy_pct = sim.progress if sim else {}
    g = _gate(state)
    counts, rows = {}, []
    wait23 = wait4 = held = 0
    if mine:
        wait23, held, wait4 = _waits(state, list(mine.values()))
    for vid, e in st.items():
        ph = e["phase"]
        counts[ph] = counts.get(ph, 0) + 1
        v = inv.get(vid, {})
        rows.append({"id": vid, "name": v.get("name"), "size": v.get("size"), "class": v.get("class"), "phase": ph,
                     "volume_type": v.get("volume_type"), "snapshots": v.get("snapshots") if isinstance(v.get("snapshots"), int) else len(v.get("snapshots") or []),
                     "attached_to": [(state.servers.get(a.get("server_id")) or {}).get("name") or a.get("server_id") or "" for a in v.get("attachments", [])],
                     "bootable": v.get("bootable"), "source_backend": v.get("backend"),
                     "progress": lifecycle.progress(ph, copy_pct.get(vid)), "at": e.get("at"), "ready": vid in ready, "g4wait": vid in g4wait,
                     "gates": ({k: (s or {}).get("by") for k, s in lifecycle.gates_of(state.journal, vid, g).items()}
                               if ph in ("sync", "recheck", "postcheck") else None),
                     "note": (sim.notes.get(vid) if sim and ph not in ("aborted", "rollback") else None) or e.get("reason") or
                             ("source changed: back to sync" if e.get("regression") else "")})
        if mine:                                       # what each volume waits for, read from the engine and the gates
            rows[-1]["note"] = mine[vid]["note"] or rows[-1]["note"]
            rows[-1]["gates"] = {k: (s or {}).get("by") for k, s in lifecycle.gates_of(state.journal, vid, g).items()}
    total = len(mine) if mine else (len(admitted) or len(st))
    done = sum(r["progress"] for r in rows)
    active = [r for r in rows if r["phase"] not in ("soak", "done", "aborted", "rollback") and not (mine and r["phase"] == "queued")]
    rows.sort(key=lambda r: (r["phase"] in ("soak", "done", "aborted", "rollback"), -r["progress"], r["id"]))
    unresolved = state.journal.unresolved_steps()
    need_g23 = wait23 if mine else sum(1 for r in rows if r["ready"] and r["gates"] and not (r["gates"]["G2"] and r["gates"]["G3"]))
    events = [{"id": e["id"], "ts": e["ts"], "kind": e["kind"], "line": _event_line(e)} for e in state.journal.events(None, None, 40)
              if e["kind"] in ("phase", "refusal", "halt", "resume", "preflight", "signoff", "attestation", "demo", "report", "audit", "inventory", "platform")]
    return {"mode": ("simulation" if sim else "idle") if state.demo else "idle",
            "engine": "not built" if not state.demo else "simulated, demonstration only",
            "simulation": {"status": sim.status, "running": sim.running, "throughput_mb_s": round(sim.throughput),
                           "rate_cap_mb_s": opts["rate_cap_mb_s"], "virtual_time": sim.vnow.strftime("%Y-%m-%d %H:%M"), "ticks": sim.ticks} if sim else None,
            "total": total, "started": len(st), "active": len(active), "counts": counts, "ready": held if mine else len(ready), "need_g23": need_g23, "need_g4": wait4 if mine else len(g4wait), "auto_sign": state.auto_sign, "options": opts,
            "by_class": {k: sum(1 for v in admitted if inv.get(v, {}).get("class") == k) for k in "ABC"} if admitted else {},
            "overall": min(100.0, round(done / total, 1)) if total else 0, "volumes": rows[:150], "volumes_more": max(0, len(rows) - 150),
            "gate": {"status": g["status"], "message": g["message"]} if g else None,
            "halted": safety.halted(state.journal), "unresolved_steps": len(unresolved), "events": events,
            "cutover_rules": ["two passed rechecks in a row", "at least 5 minutes apart", "newest no older than 15 minutes",
                              "source unchanged between them", "nothing left to copy", "gate approved right now", "no halt", "the copy and the readiness approved by a second person"]}


def api_demo_scenario(state, body, query):
    """Demo mode only: switch to another scenario. Everything read so far is discarded."""
    if not state.demo:
        raise ApiError("not available outside demo mode", 404)
    from . import demo
    demo.switch(state, body.get("id"))
    return _demo_info(state)


def api_demo_dbexport(state, body, query):
    """Demo mode only: load a made-up database export (provider ids and snapshot history) as the playbook queries would give it."""
    if not state.demo or not state.demo_cloud:
        raise ApiError("not available outside demo mode", 404)
    from .demo import dbexport, scenarios
    exp = dbexport.build(state.demo_cloud)
    exp["backend"] = body.get("backend") or scenarios.get(state.demo_scenario)["source"]
    return api_inventory_import(state, exp, {})


def api_connect(state, body, query):
    if state.demo:
        raise ApiError("demo mode is connected to the demonstration cloud and stays connected", 409)
    password = body.pop("password", None)
    if state.lab and not password:                 # lab mode: the form leaves the password out; it is the lab's own, and only for the lab's own address
        from . import lab as lab_mod
        try:
            cfg = lab_mod.load_config()
        except lab_mod.LabError as exc:
            raise ApiError(str(exc), 409) from None
        if (body.get("auth_url") or "").rstrip("/") != cfg["auth_url"].rstrip("/"):
            raise ApiError("the lab password is used only for the lab's own address, %s" % cfg["auth_url"], 400)
        password = cfg["password"]
    s = OpenStackSession(
        auth_url=body.get("auth_url"), username=body.get("username"), password=password,
        project_name=body.get("project_name"), user_domain=body.get("user_domain"),
        project_domain=body.get("project_domain"), region=body.get("region"),
        interface=body.get("interface") or "public", verify=body.get("verify", True) is not False,
        ca_file=body.get("ca_file"), timeout=float(body.get("timeout") or 30))
    info = s.authenticate()
    state.disconnect()
    with state.lock:
        state.session = s
    state.journal.record("connect", s.project_name, {"auth_url": s.auth_url, "user": s.username,
                                                     "tls_verification": s.verify, "roles": info["roles"]})
    restoring = recovery.start_restore(state)           # the work saved before a stop is put back now that the cloud answers
    return {"ok": True, "project_id": info["project_id"], "roles": info["roles"], "restoring": restoring}


def _need_session(state):
    if not state.session:
        raise ApiError("connect to a cloud first", 409)
    return state.session


def api_discover(state, body, query):
    session = _need_session(state)
    result = discovery.discover(session, state.journal)
    with state.lock:
        state.platform = result
    state.journal.record("platform", session.project_name, {
        "release": result["release"], "profile": {k: v for k, v in result["profile"].items() if k != "variants"},
        "block_storage": result["block_storage"].get("max"), "compute": result["compute"].get("max"),
        "backends": [b["backend"] for b in result["backends"]]})
    return result


def api_platform(state, body, query):
    if not state.platform:
        raise ApiError("platform has not been discovered yet", 404)
    return state.platform


def _remember_inventory(state, result, raw_servers, roots, subject, source):
    """Keep what the checks and reports need, and journal the inventory (with the volume ids, for the drift check)."""
    prev = state.journal.latest("inventory")
    prev_ids = prev["data"].get("ids") if prev and prev["data"].get("backend") == result.get("backend") else None
    with state.lock:
        state.inventory = result
        state.servers, state.roots, state.previous_ids = raw_servers, tuple(roots), prev_ids
        state.checks = None
    state.journal.record("inventory", subject, {
        "backend": result.get("backend"), "total": result["total"], "total_gib": result["total_gib"], "counts": result["counts"],
        "source": source, "ids": sorted(v["id"] for v in result["volumes"])})


def api_inventory_run(state, body, query):
    session = _need_session(state)
    window = float(body.get("window_days") or 14)
    backend = body.get("backend") or None
    raw = inventory.collect(session)
    result = inventory.build(raw["volumes"], raw["servers"], raw["root_volumes"], None, backend, window)
    result["warnings"] = raw["warnings"]
    with state.lock:
        state._raw = raw
        state.scan_args = {"backend": backend, "window_days": window}
    _remember_inventory(state, result, raw["servers"], raw["root_volumes"], session.project_name, "api")
    return result


def api_inventory_import(state, body, query):
    a1 = body.get("a1")
    if not a1:
        raise ValueError("the inventory export (query A1) is required")
    volumes = exports.parse_inventory(a1)
    churn = exports.parse_churn(body["a2"]) if body.get("a2") else None
    roots = exports.parse_root_volumes(body["a4"]) if body.get("a4") else ()
    servers = exports.apply_instances(volumes, exports.parse_instances(body["inst"])) if body.get("inst") else {}
    if state._raw:                                    # the database export has no image hints: keep the ones the API gave (workload risk, V11)
        api_v = {v["id"]: v for v in state._raw.get("volumes", [])}
        for v in volumes:
            for k in ("image_meta", "snapshot_id", "source_volid"):
                if api_v.get(v["id"], {}).get(k):
                    v[k] = api_v[v["id"]][k]
        for sid, sv in (servers or {}).items():
            for k in ("metadata", "flavor"):
                if (state._raw.get("servers", {}).get(sid) or {}).get(k):
                    sv[k] = state._raw["servers"][sid][k]
    result = inventory.build(volumes, servers, roots, churn, body.get("backend") or None,
                             float(body.get("window_days") or 14), snapshot_limit=body.get("snapshot_limit"))
    result["warnings"] = []
    _remember_inventory(state, result, servers, roots, "export", "database export")
    return result


def api_inventory_get(state, body, query):
    if not state.inventory:
        raise ApiError("no inventory yet", 404)
    return state.inventory


def api_journal(state, body, query):
    events = state.journal.events(query.get("kind"), query.get("subject"), int(query.get("limit", 100)))
    for e in events:  # long id lists would flood the screen; show how many instead
        d = e.get("data")
        if isinstance(d, dict):
            e["data"] = {k: ("%d items" % len(v) if isinstance(v, list) and len(v) > 20 else v) for k, v in d.items()}
    return {"events": events, "unresolved_steps": state.journal.unresolved_steps()}


def api_disconnect(state, body, query):
    if state.demo:
        raise ApiError("demo mode stays connected to the demonstration cloud", 409)
    state.disconnect()
    state.journal.record("disconnect", "", {})
    return {"ok": True}


def _gate(state):
    """The gate for the latest pre-flight run, or None before one has run."""
    c = state.checks
    if not c:
        return None
    att = gate_mod.attestations(state.journal, c["source_backend"], c["target_backend"])
    return gate_mod.evaluate(c, att, gate_mod.signoffs(state.journal, c["run_id"]))


def _need(state, what):
    if getattr(state, what) is None:
        raise ApiError({"platform": "detect the platform first", "inventory": "read the inventory first",
                        "audit": "run the audit first", "checks": "run the pre-flight checks first"}[what], 409)
    return getattr(state, what)


def api_audit_run(state, body, query):
    session = _need_session(state)
    if not state.platform:
        api_discover(state, {}, {})
    raw = inventory.collect(session)
    extra = audit_mod.collect_extra(session)
    result = audit_mod.build(state.platform, raw, extra, body.get("backend") or None, tls_verification=session.verify)
    with state.lock:
        state.audit, state.extra, state._raw = result, extra, raw
    state.journal.record("audit", session.project_name, {
        "volumes": result["volumes"]["total"], "gib": result["volumes"]["gib"], "snapshots": result["snapshots"]["total"],
        "findings": result["finding_counts"], "finding_ids": [f["id"] for f in result["findings"]]})
    return result


def api_audit_get(state, body, query):
    return _need(state, "audit")


def api_checks_run(state, body, query):
    platform, inv = _need(state, "platform"), _need(state, "inventory")
    src = body.get("source_backend") or inv.get("backend") or None
    tgt = body.get("target_backend") or None
    mk = risk_mod.marks(state.journal)
    cleared = risk_mod.active(state.journal, risk_mod.assess_all(inv["volumes"], state.servers, state.roots, mk))
    access = body.get("access") or state.access
    if access not in checks_run.ACCESS_LEVELS:
        raise ValueError("access must be 'api' or 'full'")
    host_checks = None
    if not state.demo and hosts_mod.load(state.home):
        hc = server_hosts.refresh_fabric(state, src, tgt)       # the registered hosts are checked as part of the pre-flight
        host_checks = hc["checks"] or None
        if hc["ready"]:
            access = "full"
    state.access = access
    result = checks_run.run_all(platform, inv, state.servers, state.roots, src, tgt, state.previous_ids, cleared, mk, access, host_checks=host_checks)
    result["hosts"] = getattr(state, "host_checks", None)
    with state.lock:
        state.checks = result
    state.journal.record("preflight", result["run_id"], {
        "source": src, "target": tgt, "counts": result["counts"], "admitted": len(result["admitted"]), "volumes": result["volumes_total"], "access": access,
        "fingerprint": result["fingerprint"]})
    return _checks_view(state)


def _checks_view(state):
    c = _need(state, "checks")
    return dict(c, gate=_gate(state), attestations=gate_mod.attestations(state.journal, c["source_backend"], c["target_backend"]))


def api_checks_get(state, body, query):
    return _checks_view(state)


def api_risk_get(state, body, query):
    """The volumes that host a workload with a known risk, why, the advice, and who has cleared them (requirement R17)."""
    inv = _need(state, "inventory")
    c = state.checks
    if c is None:
        raise ApiError("run the pre-flight first: the risk check is part of it", 409)
    names = {p["id"]: p["name"] for p in state.extra.get("projects", [])}
    cl = risk_mod.clearances(state.journal)
    by_id = {v["id"]: v for v in inv["volumes"]}
    rows = []
    for vid, a in (c.get("risk") or {}).items():
        v = by_id.get(vid, {})
        cur = cl.get(vid) if (cl.get(vid) or {}).get("signature") == a["signature"] else {}
        rows.append({"id": vid, "name": v.get("name") or "", "class": v.get("class"), "project": names.get(v.get("project_id")) or str(v.get("project_id") or "")[:8],
                     "size": v.get("size"), "level": a["level"], "reasons": a["reasons"], "advice": a["advice"], "signature": a["signature"], "cleared": a["cleared"],
                     "operator": (cur.get("operator") or {}).get("by"), "reviewer": (cur.get("reviewer") or {}).get("by"),
                     "operator_evidence": (cur.get("operator") or {}).get("evidence"), "reviewer_evidence": (cur.get("reviewer") or {}).get("evidence"),
                     "marked": vid in risk_mod.marks(state.journal)})
    rows.sort(key=lambda r: (r["level"] != "high", r["cleared"], r["id"]))
    return {"rows": rows, "high": sum(1 for r in rows if r["level"] == "high"), "medium": sum(1 for r in rows if r["level"] != "high"),
            "cleared": sum(1 for r in rows if r["cleared"]),
            "note": "This check reads hints (image properties, instance metadata, names, size). It cannot see inside a guest. A volume with no hint is not declared safe; "
                    "mark any volume the customer knows to be a risk."}


def api_risk_clear(state, body, query):
    """Record one person's clearance (or withdrawal) of high-risk volumes. Two different people, operator then reviewer, lift the block."""
    c = _need(state, "checks")
    ids = list(body.get("ids") or [])
    if not ids:
        raise ValueError("choose at least one volume")
    withdraw = bool(body.get("withdraw"))
    for vid in ids:
        a = (c.get("risk") or {}).get(vid)
        if not a or a["level"] != "high":
            raise ValueError("volume %s has no high-risk finding to clear" % vid)
        risk_mod.record(state.journal, vid, a["signature"], body.get("role"), body.get("by"), body.get("evidence"), withdraw)
    prev = {"source_backend": c["source_backend"], "target_backend": c["target_backend"]}
    checks_run_again(state, prev)                     # the new pre-flight run needs the gate (and both sign-offs) again
    out = api_risk_get(state, {}, {})
    out["rerun"] = "The safety checks were run again. They need the two approvals again."
    return out


def api_risk_mark(state, body, query):
    """Mark volumes (by id or by group) as a risk, from what the customer knows. A marked volume is not moved until two people clear it."""
    inv = _need(state, "inventory")
    c = _need(state, "checks")
    names = {p["id"]: p["name"] for p in state.extra.get("projects", [])}
    ids = list(body.get("ids") or [])
    grp = body.get("group")
    if grp:
        ids += selection.members(inv, names, state.servers, state.roots, grp.get("kind"), grp.get("key"), {v["id"] for v in inv["volumes"]}, c.get("risk"))
    known = {v["id"] for v in inv["volumes"]}
    if not ids or any(i not in known for i in ids):
        raise ValueError("choose volumes that are in the inventory")
    for vid in ids:
        risk_mod.mark(state.journal, vid, body.get("by"), body.get("note"), bool(body.get("withdraw")))
    checks_run_again(state, {"source_backend": c["source_backend"], "target_backend": c["target_backend"]})
    out = api_risk_get(state, {}, {})
    out["rerun"] = "The safety checks were run again. They need the two approvals again."
    return out


def api_attest(state, body, query):
    c = _need(state, "checks")
    cid = body.get("check_id")
    if cid not in {x["id"] for x in c["platform"] + c["volume"]}:
        raise ValueError("unknown check: %s" % cid)
    if body.get("withdraw"):
        gate_mod.withdraw(state.journal, c["source_backend"], c["target_backend"], cid, body.get("by"))
    else:
        gate_mod.attest(state.journal, c["source_backend"], c["target_backend"], cid, body.get("by"), body.get("note"))
    return _checks_view(state)


def api_signoff(state, body, query):
    c = _need(state, "checks")
    gate_mod.sign(state.journal, c["run_id"], body.get("role"), body.get("by"))
    return _checks_view(state)


def api_demo_validate(state, body, query):
    """Demo mode only: record a lab validation of the demonstration cloud so the whole flow can be shown."""
    if not state.demo:
        raise ApiError("not available outside demo mode", 404)
    rel = (_need(state, "platform").get("release") or {}).get("release")
    if not rel:
        raise ApiError("the release is not identified", 409)
    profiles.record_validation(state.journal, rel, True, {"demo": True, "note": "synthetic validation of the demonstration cloud"})
    api_discover(state, {}, {})
    return {"ok": True, "release": rel}


def api_report(state, body, query):
    kind = body.get("kind") or "before"
    customer = (body.get("customer") or "").strip() or ("Demonstration: %s (synthetic data)" % _demo_info(state)["title"] if state.demo else
                                                         (state.session.project_name if state.session else ""))
    by = (body.get("prepared_by") or "").strip()
    names = {p["id"]: p["name"] for p in state.extra.get("projects", [])}
    if kind == "audit":
        doc = report_build.audit_document(_need(state, "audit"), customer, by, state.demo, __version__)
        name = "OpenMove_environment_audit"
    elif kind == "before":
        checks = _need(state, "checks")
        doc = report_build.before_document(
            _need(state, "platform"), _need(state, "inventory"), checks, _gate(state),
            gate_mod.attestations(state.journal, checks["source_backend"], checks["target_backend"]),
            audit=state.audit if body.get("include_audit", True) else None, customer=customer, prepared_by=by,
            demo=state.demo, version=__version__, project_names=names, servers=state.servers)
        name = "OpenMove_pre-migration_report"
    elif kind == "unmigrated":
        checks = _need(state, "checks")
        g = _gate(state)
        doc = report_build.unmigrated_document(_unmigrated(state), customer, by, state.demo, __version__, g["message"] if g else "",
                                               checks.get("source_backend"), checks.get("target_backend"))
        name = "OpenMove_volumes_not_migrated"
    elif kind in ("after", "update"):
        checks = _need(state, "checks")
        g = _gate(state)
        ev = None if state.demo else Evidence(Path(state.home) / "evidence")
        rows = after_mod.collect(state, _need(state, "inventory"), g, names, ev)
        doc = report_build.after_document(rows, customer, by, state.demo, __version__, checks.get("source_backend"), checks.get("target_backend"),
                                          g["message"] if g else "", final=(kind == "after"))
        name = "OpenMove_after_report" if kind == "after" else "OpenMove_update_report"
    elif kind in ("volumes_xlsx", "volumes_csv"):
        header, vrows = after_mod.volume_list(state, _need(state, "inventory"), names)
        stamp = report_docx.datetime.now(report_docx.timezone.utc).strftime("%Y%m%d_%H%M")
        notes = ["OpenMove %s volume list, generated %s" % (__version__, stamp)] + (["DEMONSTRATION DATA: made up, describes no real system"] if state.demo else [])
        data = sheet_mod.xlsx_bytes(header, vrows, "Volumes", notes) if kind == "volumes_xlsx" else sheet_mod.csv_bytes(header, vrows)
        state.journal.record("report", kind, {"bytes": len(data), "rows": len(vrows), "demo": state.demo})
        ext, ctype = (("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") if kind == "volumes_xlsx" else ("csv", "text/csv; charset=utf-8"))
        return Download(data, "OpenMove_volumes_%s.%s" % (stamp, ext), ctype)
    else:
        raise ValueError("kind must be 'before', 'audit', 'unmigrated', 'after', 'update', 'volumes_xlsx' or 'volumes_csv'")
    data = report_docx.build_docx(doc)
    stamp = report_docx.datetime.now(report_docx.timezone.utc).strftime("%Y%m%d_%H%M")
    state.journal.record("report", kind, {"bytes": len(data), "customer": customer, "prepared_by": by, "demo": state.demo})
    return Download(data, "%s_%s.docx" % (name, stamp), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


ROUTES = {
    ("GET", "status"): api_status,
    ("POST", "connect"): api_connect,
    ("POST", "disconnect"): api_disconnect,
    ("POST", "recover"): api_recover,
    ("POST", "recover/discard"): api_recover_discard,
    ("POST", "discover"): api_discover,
    ("GET", "platform"): api_platform,
    ("POST", "inventory/run"): api_inventory_run,
    ("POST", "inventory/import"): api_inventory_import,
    ("GET", "inventory"): api_inventory_get,
    ("GET", "journal"): api_journal,
    ("POST", "audit/run"): api_audit_run,
    ("GET", "audit"): api_audit_get,
    ("POST", "checks/run"): api_checks_run,
    ("GET", "checks"): api_checks_get,
    ("POST", "checks/attest"): api_attest,
    ("POST", "checks/signoff"): api_signoff,
    ("POST", "report"): api_report,
    ("POST", "halt"): api_halt,
    ("POST", "resume"): api_resume,
    ("POST", "demo/validate"): api_demo_validate,
    ("GET", "live"): api_live,
    ("GET", "unmigrated"): api_unmigrated,
    ("GET", "volume"): api_volume,
    ("GET", "risk"): api_risk_get,
    ("POST", "risk/clear"): api_risk_clear,
    ("POST", "risk/mark"): api_risk_mark,
    ("GET", "selection"): api_selection_get,
    ("GET", "tree"): api_tree,
    ("POST", "selection"): api_selection_set,
    ("POST", "release"): api_release,
    ("POST", "volume-gate"): api_volume_gate,
    ("GET", "engine"): api_engine_get,
    ("POST", "engine/dry-run"): api_engine_dry_run,
    ("POST", "engine/start"): api_engine_start,
    ("POST", "selection/confirm"): api_selection_confirm,
    ("POST", "engine/stop"): api_engine_stop,
    ("POST", "engine/reverse"): api_engine_reverse,
    ("POST", "validation"): api_validation,
    ("GET", "phase0"): api_phase0_get,
    ("POST", "phase0/record"): api_phase0_record,
    ("POST", "demo/approve"): api_demo_approve,
    ("POST", "lab/approve"): api_lab_approve,
    ("POST", "demo/simulate"): api_demo_simulate,
    ("POST", "demo/scenario"): api_demo_scenario,
    ("POST", "demo/db-export"): api_demo_dbexport,
    ("GET", "hosts"): server_hosts.api_hosts_get,
    ("GET", "hosts/setup-script"): server_hosts.api_hosts_setup_script,
    ("POST", "hosts/probe"): server_hosts.api_hosts_probe,
    ("POST", "hosts/setup"): server_hosts.api_hosts_setup,
    ("POST", "hosts/array"): server_hosts.api_hosts_array,
    ("POST", "hosts/save"): server_hosts.api_hosts_save,
    ("POST", "hosts/remove"): server_hosts.api_hosts_remove,
    ("POST", "hosts/scan"): server_hosts.api_hosts_scan,
    ("POST", "hosts/trust"): server_hosts.api_hosts_trust,
    ("POST", "hosts/check"): server_hosts.api_hosts_check,
    ("POST", "hosts/mover"): server_hosts.api_hosts_mover,
    ("GET", "diagnostics"): server_hosts.api_diag_get,
    ("POST", "diagnostics/bundle"): server_hosts.api_diag_bundle,
    ("POST", "diagnostics/settings"): server_hosts.api_diag_settings,
    ("POST", "diagnostics/decode"): server_hosts.api_diag_decode,
    ("GET", "diagnostics/download"): server_hosts.api_diag_download,
}


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second program take the same port; that must never happen to a program that moves volumes.
    allow_reuse_address = os.name != "nt"


def create_server(host="127.0.0.1", port=0, home=None):
    """Create (but do not start) the server. Returns (server, state)."""
    state = AppState(home or paths.app_home())
    from .diag import Diag
    state.diag = Diag(state.home)
    state.diag.attach()
    recovery.begin(state)
    holder = {}
    handler = make_handler(state, lambda: holder["server"].server_address[1])
    server = _Server((host, port), handler)
    server.daemon_threads = True
    holder["server"] = server
    return server, state
