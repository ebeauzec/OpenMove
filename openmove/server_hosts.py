# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The API handlers for hosts (docs/MOVER.md) and diagnostics (docs/DIAGNOSTICS.md). Registered in server.ROUTES.

Same shape as the other handlers: (state, body, query) -> JSON-able, or a server.Download for a file.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

from . import arrays, backends, bootstrap, hosts as hosts_mod
from . import redact as redact_mod
from .connectors import ssh


def _err(message, status=409):
    from .server import ApiError
    return ApiError(message, status)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pair(state, body):
    """(source, target) back-ends: the ones given, or the ones of the latest pre-flight."""
    c = state.checks or {}
    return body.get("source_backend") or c.get("source_backend"), body.get("target_backend") or c.get("target_backend")


def family_for(state, source, target):
    """(adapter family, reason). Both back-ends must be of one family an adapter exists for."""
    if not (source and target and state.platform):
        return None, "choose the source and the target back-end first (pre-flight)"
    a = backends.assess(state.platform, source, target)
    if a["status"] == "fail":
        return None, a["evidence"]
    if a["source"] != a["target"]:
        return None, "there is no array adapter for a move from %s to %s yet (built: %s)" % (
            backends.title(a["source"]), backends.title(a["target"]), ", ".join(f["family"] for f in arrays.families() if f["built"]))
    if a["source"] not in [f["family"] for f in arrays.families() if f["built"]]:
        return None, "there is no array adapter for %s yet (built: %s)" % (backends.title(a["source"]), ", ".join(f["family"] for f in arrays.families() if f["built"]))
    return a["source"], ""


def refresh_fabric(state, source=None, target=None, bench=False):
    """Check the registered hosts for this pair and set state.fabric when everything needed is ready. Returns the stored result."""
    source, target = source or (state.checks or {}).get("source_backend"), target or (state.checks or {}).get("target_backend")
    out = {"at": _now(), "source": source, "target": target, "family": None, "ready": False, "why": "", "checks": []}
    state.fabric = None
    registered = hosts_mod.load(state.home)
    if not registered:
        out["why"] = "no host is registered"
        state.host_checks = out
        return out
    family, why = family_for(state, source, target)
    out["family"] = family
    if not family:
        out["why"] = why
        state.host_checks = out
        return out
    movers, cinders = hosts_mod.hosts_with(state.home, "mover"), hosts_mod.hosts_with(state.home, "cinder")
    if not movers or not cinders:
        out["why"] = "a migration host (role mover) and a Cinder host (role cinder) are both needed"
        out["checks"] = []
        state.host_checks = out
        return out
    fab = hosts_mod.Fabric(movers[0], cinders[0], family, source, target, state.home)
    checks = hosts_mod.readiness(fab.mover_host, state.home, source, target, family, bench)
    if fab.cinder_host.name != fab.mover_host.name:
        checks += hosts_mod.readiness(fab.cinder_host, state.home, source, target, family, False)
    elif "cinder" not in (fab.mover_host.spec.get("roles") or []):
        pass
    out["checks"] = checks
    bad = [c for c in checks if c["status"] == "fail"]
    out["ready"] = not bad
    out["why"] = "; ".join("%s: %s" % (c["title"], c["detail"]) for c in bad)
    if out["ready"]:
        state.fabric = fab
    state.host_checks = out
    if state.diag:
        state.diag.log("info", "host readiness", ready=out["ready"], family=family, failed=[c["id"] for c in bad])
    return out


# ---- the host setup script ---------------------------------------------------------------------------------------------------------
DEPLOY_SCRIPT = "prepare_host.sh"


def setup_script():
    """The text of the host setup script that ships with OpenMove (resources/deploy/prepare_host.sh)."""
    return (Path(__file__).resolve().parent / "resources" / "deploy" / DEPLOY_SCRIPT).read_text(encoding="utf-8")


def setup_command(home, role="both", user="openmove", cinder_user="cinder", source_address=""):
    """The command an administrator runs on the host, with OpenMove's public key filled in. Creates OpenMove's key when it does not exist yet."""
    from .connectors import ssh
    if role not in ("mover", "cinder", "both"):
        raise ValueError("role must be mover, cinder or both")
    for label, v in (("user", user), ("cinder user", cinder_user)):
        if not re.match(r"^[a-z_][a-z0-9_-]{0,31}$", v):
            raise ValueError("the %s %r is not a plain account name" % (label, v))
    if source_address and not re.match(r"^[A-Za-z0-9_.:/*,!-]{1,120}$", source_address):
        raise ValueError("the address to allow is not valid")
    ssh.ensure_key(home)
    key = ssh.public_key(home)
    cmd = "sudo bash prepare_host.sh --role %s --user %s --key '%s'" % (role, user, key)
    if role in ("cinder", "both") and cinder_user != "cinder":
        cmd += " --cinder-user %s" % cinder_user
    if source_address:
        cmd += " --from '%s'" % source_address
    return cmd


def api_hosts_setup_script(state, body, query):
    """The host setup script and the command to run it, ready to copy (docs/DEPLOYMENT.md). Changes nothing on any host."""
    try:
        cmd = setup_command(state.home, str(query.get("role") or "both"), str(query.get("user") or "openmove"), str(query.get("cinder_user") or "cinder"),
                            str(query.get("from") or ""))
    except ValueError as exc:
        raise _err(str(exc), 400) from None
    return {"filename": DEPLOY_SCRIPT, "script": setup_script(), "command": cmd,
            "notes": ["Copy prepare_host.sh to the host, run the command as an administrator, then register the host here, compare and pin its host key, and run the checks.",
                      "The script is safe to run again. Add --check to only report what is missing."]}


# ---- hosts ------------------------------------------------------------------------------------------------------------------------
def api_hosts_get(state, body, query):
    v = hosts_mod.view(state.home)
    return dict(v, checks=getattr(state, "host_checks", None), families=arrays.families(), ready=state.fabric is not None,
                notes=["The migration host runs the data mover; the Cinder host runs the Cinder helper; one host can do both.",
                       "OpenMove logs in with its own key (shown above). Add the public key to the user's authorized_keys on each host."])


def api_hosts_save(state, body, query):
    try:
        spec = hosts_mod.upsert(state.home, body.get("host") or body)
    except hosts_mod.HostError as exc:
        raise _err(str(exc), 400) from None
    state.fabric = None
    state.journal.record("host_saved", spec["name"], {"roles": spec["roles"], "transport": spec["transport"], "address": spec.get("address"), "port": spec.get("port"),
                                                       "user": spec.get("user")})
    return api_hosts_get(state, {}, {})


def api_hosts_remove(state, body, query):
    name = str(body.get("name") or "")
    if not hosts_mod.remove(state.home, name):
        raise _err("no host named %r" % name, 404)
    state.fabric = None
    state.journal.record("host_removed", name, {})
    return api_hosts_get(state, {}, {})


def api_hosts_probe(state, body, query):
    """The host key a machine offers, before it is registered (the first step of a setup). Nothing is trusted yet."""
    address, port = str(body.get("address") or "").strip(), int(body.get("port") or 22)
    if not bootstrap.ADDRESS.match(address):
        raise _err("give the address of the host", 400)
    try:
        keys = ssh.scan_host_key(address, port)
    except ssh.SshError as exc:
        raise _err(str(exc), 502) from None
    return {"keys": [{"type": k["type"], "fingerprint": k["fingerprint"]} for k in keys], "pinned": ssh.is_pinned(state.home, address, port)}


def _pin_for_setup(state, req, body):
    """The host key must be pinned before the password is sent. When it is not, the person who checked the fingerprint gives it here."""
    if ssh.is_pinned(state.home, req["address"], req["port"]):
        return
    fp, by = str(body.get("fingerprint") or "").strip(), str(body.get("by") or "").strip()
    if not fp or len(by) < 3:
        raise _err("the host key of %s:%s is not pinned. Read it (Read the host key), compare the fingerprint with the host's administrator, then give the "
                   "fingerprint you checked and your name. The password is not sent before that." % (req["address"], req["port"]), 409)
    try:
        keys = ssh.scan_host_key(req["address"], req["port"])
    except ssh.SshError as exc:
        raise _err(str(exc), 502) from None
    key = next((k for k in keys if k["fingerprint"] == fp), None)
    if key is None:
        raise _err("the host now offers %s, not %s; nothing was trusted and the password was not sent" % (", ".join(k["fingerprint"] for k in keys), fp), 409)
    ssh.pin_host_key(state.home, key["line"])
    state.journal.record("host_trusted", req["name"], {"by": by, "fingerprint": fp, "type": key["type"]})


def api_hosts_setup(state, body, query):
    """Set up a new host from here: log in with the administrator password, prepare the host, set up its storage paths, register it, check it (docs/DEPLOYMENT.md).

    The password is read from this request, used for the setup and dropped: not stored, not journaled, not in a log, not in an answer."""
    password = str(body.get("password") or "")
    try:
        req = bootstrap.clean(body)
    except bootstrap.SetupError as exc:
        raise _err(str(exc), 400) from None
    if not password:
        raise _err("type the administrator password of the host (it is used for this setup only and is not kept)", 400)
    _pin_for_setup(state, req, body)
    meta = {"address": req["address"], "port": req["port"], "login": req["user"], "roles": req["roles"], "service_user": req["service_user"],
            "storage": {k: v for k, v in req["storage"].items() if k != "multipath_extra"}, "check_only": req["check_only"]}
    try:
        res = bootstrap.run(state.home, req, password)
    except ssh.SshError as exc:
        state.journal.record("host_setup", req["name"], dict(meta, ok=False, error=str(exc)))
        if state.diag:
            state.diag.log("error", "host setup could not log in", host=req["name"], error=str(exc))
        raise _err(str(exc), 502) from None
    finally:
        password = ""
    state.journal.record("host_setup", req["name"], dict(meta, ok=res["ok"], steps=[{"id": s["id"], "status": s["status"]} for s in res["steps"]],
                                                          initiator=res.get("initiator") or ""))
    if state.diag and not res["ok"]:
        state.diag.log("error", "host setup did not finish", host=req["name"], steps=[(s["id"], s["status"]) for s in res["steps"]])
    checks = []
    if res["ok"] and not req["check_only"]:
        spec = hosts_mod.upsert(state.home, res["spec"])
        state.fabric = None
        state.journal.record("host_saved", spec["name"], {"roles": spec["roles"], "transport": spec["transport"], "address": spec.get("address"), "port": spec.get("port"),
                                                           "user": spec.get("user"), "by": "host setup"})
        s, t = _pair(state, {})
        family = "iscsi" if req["storage"]["protocol"] != "none" else "lvm"
        try:
            checks = hosts_mod.readiness(hosts_mod.host(state.home, spec["name"]), state.home, s, t, family)
        except ssh.SshError as exc:
            checks = [hosts_mod.rec("H2", "Key login works", False, str(exc))]
    return dict(res, checks=checks, hosts=api_hosts_get(state, {}, {}))


def _strip_storage(storage):
    """The storage description as it is kept and journaled: no secret, and not the free text."""
    return {k: v for k, v in storage.items() if k not in ("multipath_extra", "chap_secret")}


def api_hosts_inspect(state, body, query):
    """Read what a host is and what it sees: initiator name, Fibre Channel ports and the target ports they see, interfaces, sessions, multipath maps.
    A registered host is read with OpenMove's key (`name`); a host that is not set up yet with the administrator password (used for this call only).
    Changes nothing on the host."""
    if body.get("name"):
        name = str(body["name"])
        res = bootstrap.inspect(_host(state, name))
        state.journal.record("host_inspected", name, {"ok": res["ok"], "mode": "key"})
        return res
    password = str(body.get("password") or "")
    try:
        req = bootstrap.clean(dict(body, name=body.get("name") or "new-host"))
    except bootstrap.SetupError as exc:
        raise _err(str(exc), 400) from None
    if not password:
        raise _err("give the name of a registered host, or the address and the administrator password of a host that is not set up yet", 400)
    _pin_for_setup(state, req, body)
    try:
        res = bootstrap.inspect_with_password(state.home, req, password)
    except ssh.SshError as exc:
        state.journal.record("host_inspected", req["address"], {"ok": False, "mode": "password", "error": str(exc)})
        raise _err(str(exc), 502) from None
    finally:
        password = ""
    state.journal.record("host_inspected", req["address"], {"ok": res["ok"], "mode": "password"})
    return res


def api_hosts_configure(state, body, query):
    """Change the storage configuration of a registered host (portals, target names, interfaces, addresses, MTU, session settings, Fibre Channel ports) with
    OpenMove's key only. The host can be managed through OpenMove alone. The CHAP secret is used for this call only. With `check_only` nothing changes."""
    name, by = str(body.get("name") or ""), str(body.get("by") or "").strip()
    h = _host(state, name)
    if len(by) < 3:
        raise _err("give your name: it is saved with the change", 400)
    try:
        storage, secret = bootstrap.clean_storage(body.get("storage"))
    except bootstrap.SetupError as exc:
        raise _err(str(exc), 400) from None
    if storage["protocol"] == "none":
        raise _err("choose iSCSI or Fibre Channel", 400)
    check = bool(body.get("check_only"))
    try:
        res = bootstrap.reconfigure(h, storage, secret, check)
    except ssh.SshError as exc:
        state.journal.record("host_configured", name, {"by": by, "ok": False, "check_only": check, "error": str(exc)})
        raise _err(str(exc), 502) from None
    finally:
        secret = ""
    state.journal.record("host_configured", name, {"by": by, "ok": res["ok"], "check_only": check, "storage": _strip_storage(storage),
                                                    "steps": [{"id": s["id"], "status": s["status"]} for s in res["steps"]], "initiator": res.get("initiator") or ""})
    if res["ok"] and not check:
        spec = dict(h.spec)
        spec["storage"] = _strip_storage(storage)
        hosts_mod.upsert(state.home, spec)
        state.fabric = None
    return dict(res, hosts=api_hosts_get(state, {}, {}))


def api_hosts_array(state, body, query):
    """Store, list, forget or check the login of an array on a migration host (docs/ARRAY_API.md). The password is used for this request only:
    it is sent to the host's data mover, which keeps it in a root-only file. It is not stored here, not journaled, not logged and not returned."""
    h = _host(state, body.get("host"))
    action = str(body.get("action") or "")
    if action not in ("set", "forget", "list", "check"):
        raise _err("action is set, forget, list or check", 400)
    arr = dict(body.get("array") or {})
    name = str(arr.get("name") or body.get("name") or "")
    try:
        if action == "list":
            return {"arrays": h.mover("array.list")["arrays"]}
        if action == "forget":
            res = h.mover("array.forget", {"name": name})
            state.journal.record("array_login", h.name, {"array": name, "action": "forget"})
            return res
        if action == "check":
            rows = [r for r in h.mover("array.list")["arrays"] if r["name"] == name]
            if not rows:
                raise _err("no login is stored for the array %r on %s" % (name, h.name), 404)
            ok, detail = array_check_row(h, rows[0])
            return {"ok": ok, "detail": detail}
        password = arr.pop("password", None)
        args = dict(arr, name=name)
        if password:
            args["password"] = str(password)
        try:
            res = h.mover("array.set", args)
        finally:
            password = ""
            args.pop("password", None)
        state.journal.record("array_login", h.name, {"array": name, "action": "set", "family": arr.get("family"), "mgmt": arr.get("mgmt"), "user": arr.get("user") or ""})
        ok, detail = array_check_row(h, res)
        return dict(res, check={"ok": ok, "detail": detail})
    except ssh.SshError as exc:
        raise _err(str(exc), 502) from None


def array_check_row(h, row):
    return hosts_mod.array_check(h, row)


def _host(state, name):
    try:
        return hosts_mod.host(state.home, str(name or ""))
    except hosts_mod.HostError as exc:
        raise _err(str(exc), 404) from None


def api_hosts_scan(state, body, query):
    """The host key the machine offers now. Nothing is trusted yet: a person compares the fingerprint and presses Trust."""
    h = _host(state, body.get("name"))
    if h.local:
        return {"keys": [], "note": "this machine needs no host key"}
    try:
        keys = ssh.scan_host_key(h.spec["address"], h.spec.get("port") or 22)
    except ssh.SshError as exc:
        raise _err(str(exc), 502) from None
    return {"keys": [{"type": k["type"], "fingerprint": k["fingerprint"]} for k in keys], "pinned": ssh.is_pinned(state.home, h.spec["address"], h.spec.get("port") or 22)}


def api_hosts_trust(state, body, query):
    """Pin the host key whose fingerprint the person confirmed. The host is read again here, so a different key than the one shown is refused."""
    h = _host(state, body.get("name"))
    fp, by = str(body.get("fingerprint") or "").strip(), str(body.get("by") or "").strip()
    if len(by) < 3:
        raise ValueError("trusting a host key needs the name of the person who checked the fingerprint")
    if not fp:
        raise ValueError("give the fingerprint that was checked")
    try:
        keys = ssh.scan_host_key(h.spec["address"], h.spec.get("port") or 22)
    except ssh.SshError as exc:
        raise _err(str(exc), 502) from None
    key = next((k for k in keys if k["fingerprint"] == fp), None)
    if key is None:
        raise _err("the host now offers %s, not %s; nothing was trusted" % (", ".join(k["fingerprint"] for k in keys), fp), 409)
    ssh.pin_host_key(state.home, key["line"])
    state.journal.record("host_trusted", h.name, {"by": by, "fingerprint": fp, "type": key["type"]})
    return api_hosts_get(state, {}, {})


def api_hosts_check(state, body, query):
    """The readiness checks of the registered hosts for the chosen pair of back-ends. Places the data mover where it is needed (a file in its own folder)."""
    s, t = _pair(state, body)
    return refresh_fabric(state, s, t, bench=bool(body.get("bench")))


def api_hosts_mover(state, body, query):
    """Place or remove the data mover on one host. 'remove' leaves nothing behind but refuses while a job runs."""
    h = _host(state, body.get("name"))
    action = body.get("action")
    try:
        if action == "install":
            info = h.install_mover()
            state.journal.record("mover_installed", h.name, {"path": info.get("path"), "sha256": info.get("sha256")})
            return {"installed": info}
        if action == "remove":
            res = h.remove_mover()
            state.fabric = None
            state.journal.record("mover_removed", h.name, {})
            return {"removed": res}
    except ssh.SshError as exc:
        raise _err(str(exc), 502) from None
    raise ValueError("action must be install or remove")


# ---- diagnostics ------------------------------------------------------------------------------------------------------------------
def _diag(state):
    if state.diag is None:
        raise _err("diagnostics are not running", 503)
    return state.diag


def bundle_extras(state):
    """What the screen knows, for the support bundle. Nothing secret is held in the state; the redactor handles the rest."""
    extras = {}
    inv = state.inventory or {}
    if inv.get("volumes"):
        extras["inventory"] = {"summary": {k: v for k, v in inv.items() if k != "volumes"}, "volumes": [
            {k: v.get(k) for k in ("id", "class", "size_gb", "status", "backend", "volume_type", "snapshots", "attached_to", "cause", "name", "project_name")}
            for v in inv["volumes"]]}
    if state.platform:
        extras["platform"] = state.platform
    if state.checks:
        extras["checks"] = state.checks
    if state.engine:
        try:
            extras["engine"] = state.engine.view()
        except Exception as exc:                       # the view failing is itself a fact worth keeping
            extras["engine"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
    extras["hosts"] = {"registry": hosts_mod.view(state.home), "last_check": getattr(state, "host_checks", None)}
    extras["status"] = {"access": state.access, "demo": state.demo, "lab": state.lab, "connected": state.session is not None,
                        "selection": None if state.selection is None else len(state.selection), "options": state.options}
    return extras


def make_bundle(state, level=None, remote=True, reason="requested from the screen", out_dir=None):
    d = _diag(state)
    hs = [ssh.Host(h, state.home) for h in hosts_mod.load(state.home)] if remote else []
    ids = [v["id"] for v in (state.inventory or {}).get("volumes", []) if v.get("class") == "C"][:60]
    return d.bundle(state.journal, extras=bundle_extras(state), level=level, remote=remote, reason=reason, hosts=hs, volumes=ids, out_dir=out_dir)


def _bundles(state):
    folder = state.home / "bundles"
    out = []
    if folder.is_dir():
        for p in sorted(folder.glob("openmove-support-*.zip"), reverse=True)[:50]:
            st = p.stat()
            out.append({"name": p.name, "bytes": st.st_size, "at": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return out


def api_diag_get(state, body, query):
    d = _diag(state)
    logs = []
    for p in sorted(d.logs.glob("*")):
        if p.is_file():
            logs.append({"name": p.name, "bytes": p.stat().st_size})
    return {"settings": d.settings, "levels": list(redact_mod.LEVELS), "logs": logs, "bundles": _bundles(state), "tokens": len(d.live.map) if hasattr(d.live, "map") else 0,
            "folder": str(state.home / "bundles"),
            "text": {"none": "Nothing is changed. For a site that may share everything.",
                     "standard": "Addresses, host names, e-mail addresses, identifiers, names of people, projects, volumes, servers and hosts, and secrets become tokens. "
                                 "The same value always has the same token, so relations stay visible.",
                     "strict": "Also back-end, volume type, zone and pool names, and free text cut to 60 characters."}}


def api_diag_bundle(state, body, query):
    level = body.get("level")
    if level is not None and level not in redact_mod.LEVELS:
        raise ValueError("level must be one of %s" % ", ".join(redact_mod.LEVELS))
    res = make_bundle(state, level, remote=bool(body.get("remote", True)), reason=str(body.get("reason") or "requested from the screen")[:300])
    state.journal.record("support_bundle", "support", {"name": Path(res["path"]).name, "redaction": res["redaction"], "files": res["files"], "bytes": res["bytes"]})
    return dict(res, name=Path(res["path"]).name, bundles=_bundles(state))


def api_diag_settings(state, body, query):
    d = _diag(state)
    try:
        d.set_settings(redaction=body.get("redaction"), log_redaction=body.get("log_redaction"))
    except ValueError as exc:
        raise ValueError(str(exc)) from None
    return api_diag_get(state, {}, {})


def api_diag_download(state, body, query):
    from .server import Download
    name = str(query.get("name") or "")
    if not re.match(r"^openmove-support-[0-9TZ]+-(none|standard|strict)\.zip$", name):
        raise ValueError("not a support bundle name")
    p = state.home / "bundles" / name
    if not p.is_file():
        raise _err("no such bundle", 404)
    return Download(p.read_bytes(), name, "application/zip")


def api_diag_decode(state, body, query):
    """Translate a redaction token back. Only on the site that made the bundle: the map never leaves it."""
    token = str(body.get("token") or "").strip()
    d = _diag(state)
    found = redact_mod.Redactor("standard", d.dir / "redaction-map.json").decode(token)
    if found is None:
        raise _err("this site has no such token", 404)
    return {"token": token, "value": found}
