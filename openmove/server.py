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
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import __version__, discovery, exports, inventory, paths
from .connectors.httpclient import ConnectorError
from .connectors.openstack import OpenStackSession
from .journal import Journal

MAX_REQUEST = 20 * 1024 * 1024
STATIC = {"/app.js": ("app.js", "application/javascript; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8")}


class AppState:
    def __init__(self, home):
        self.home = home
        self.journal = Journal(home / "journal.db")
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.RLock()
        self.session = None
        self.platform = None
        self.inventory = None
        self.extra_hosts = ()  # Host header values accepted besides loopback; set with --allow-host
        self._raw = None  # last API collection, kept so a back-end filter can be re-applied without calling the cloud

    def disconnect(self):
        with self.lock:
            if self.session:
                self.session.close()
            self.session = self.platform = self.inventory = self._raw = None


def make_handler(state, port_getter):
    web = paths.resource_dir() / "web"

    class Handler(BaseHTTPRequestHandler):
        server_version = "OpenMove"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # no request lines in the console: they can hold identifiers
            pass

        # ---- helpers ----
        def _send(self, status, body, ctype="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
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
                if method == "GET" and path == "/":
                    q = parse_qs(url.query)
                    if not self._token_ok((q.get("token") or [""])[0]):
                        return self._send(403, b"Open the address that OpenMove printed when it started.",
                                          "text/plain; charset=utf-8")
                    html = (web / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", state.token)
                    return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
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
                return self._send(200, handler(state, body, query))
            except ApiError as exc:
                return self._send(exc.status, {"error": str(exc)})
            except ConnectorError as exc:
                return self._send(502, {"error": str(exc)})
            except (ValueError, KeyError) as exc:
                return self._send(400, {"error": str(exc)})
            except Exception:  # a defect: say so, without internals
                return self._send(500, {"error": "internal error; see the journal"})

    return Handler


class ApiError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


# ---- API handlers: (state, body, query) -> JSON-able ---------------------------------------------

def api_status(state, body, query):
    with state.lock:
        return {"version": __version__, "home": str(state.home), "connected": state.session is not None,
                "project": state.session.project_name if state.session else None,
                "platform": state.platform is not None, "inventory": state.inventory is not None,
                "read_only": (state.platform or {}).get("read_only", True),
                "unresolved_steps": len(state.journal.unresolved_steps())}


def api_connect(state, body, query):
    s = OpenStackSession(
        auth_url=body.get("auth_url"), username=body.get("username"), password=body.pop("password", None),
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
    return {"ok": True, "project_id": info["project_id"], "roles": info["roles"]}


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


def api_inventory_run(state, body, query):
    session = _need_session(state)
    window = float(body.get("window_days") or 14)
    backend = body.get("backend") or None
    raw = inventory.collect(session)
    result = inventory.build(raw["volumes"], raw["servers"], raw["root_volumes"], None, backend, window)
    result["warnings"] = raw["warnings"]
    with state.lock:
        state._raw = raw
        state.inventory = result
    state.journal.record("inventory", session.project_name, {
        "backend": backend, "total": result["total"], "total_gib": result["total_gib"], "counts": result["counts"],
        "source": "api"})
    return result


def api_inventory_import(state, body, query):
    a1 = body.get("a1")
    if not a1:
        raise ValueError("the inventory export (query A1) is required")
    volumes = exports.parse_inventory(a1)
    churn = exports.parse_churn(body["a2"]) if body.get("a2") else None
    roots = exports.parse_root_volumes(body["a4"]) if body.get("a4") else ()
    servers = exports.apply_instances(volumes, exports.parse_instances(body["inst"])) if body.get("inst") else {}
    result = inventory.build(volumes, servers, roots, churn, body.get("backend") or None,
                             float(body.get("window_days") or 14), snapshot_limit=body.get("snapshot_limit"))
    result["warnings"] = []
    with state.lock:
        state.inventory = result
    state.journal.record("inventory", "export", {"backend": body.get("backend"), "total": result["total"],
                                                 "counts": result["counts"], "source": "database export"})
    return result


def api_inventory_get(state, body, query):
    if not state.inventory:
        raise ApiError("no inventory yet", 404)
    return state.inventory


def api_journal(state, body, query):
    return {"events": state.journal.events(query.get("kind"), query.get("subject"), int(query.get("limit", 100))),
            "unresolved_steps": state.journal.unresolved_steps()}


def api_disconnect(state, body, query):
    state.disconnect()
    state.journal.record("disconnect", "", {})
    return {"ok": True}


ROUTES = {
    ("GET", "status"): api_status,
    ("POST", "connect"): api_connect,
    ("POST", "disconnect"): api_disconnect,
    ("POST", "discover"): api_discover,
    ("GET", "platform"): api_platform,
    ("POST", "inventory/run"): api_inventory_run,
    ("POST", "inventory/import"): api_inventory_import,
    ("GET", "inventory"): api_inventory_get,
    ("GET", "journal"): api_journal,
}


def create_server(host="127.0.0.1", port=0, home=None):
    """Create (but do not start) the server. Returns (server, state)."""
    state = AppState(home or paths.app_home())
    holder = {}
    handler = make_handler(state, lambda: holder["server"].server_address[1])
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    holder["server"] = server
    return server, state
