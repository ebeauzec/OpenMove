# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Set up a new migration host or Cinder host from OpenMove (docs/DEPLOYMENT.md, "Set up a host from OpenMove").

A person gives the address of a Linux host and the administrator login (usually root and its password), and the storage paths the host needs.
OpenMove then does, over SSH and in this order:

  1. logs in with the password (connectors/ssh.py RootLogin; the host key must be pinned first, by a person) and reads what the host is;
  2. sends resources/deploy/prepare_host.sh and runs it: packages, the service account, OpenMove's own key, the narrow sudo rights, sshd;
  3. finds the interpreter Cinder uses (the first line of the cinder-volume program) on a Cinder host;
  4. sends resources/deploy/configure_storage.sh and runs it: iSCSI initiator, multipath, the logins to the arrays (when storage paths were asked for);
  5. logs in again as the service account, with the key only, to prove that the password is no longer needed.

The caller (server_hosts.py) then registers the host and runs the readiness checks. The password is used for steps 1 to 4 and then dropped: it is not
stored, not journaled, not in the trace and not on the host. After the setup OpenMove has only its key. Both scripts are idempotent, so a failed
setup is repaired by running it again; every step's complete output is kept in the answer, so a failure on a dark site can be read afterwards.
"""
import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .connectors import ssh

DEPLOY = Path(__file__).resolve().parent / "resources" / "deploy"
ACCOUNT = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
ADDRESS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:_-]*$")
PORTAL = re.compile(r"^(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9][A-Za-z0-9._-]*)(:[0-9]{1,5})?$")
IQN = re.compile(r"^(iqn\.[0-9]{4}-[0-9]{2}\.[a-z0-9.-]+(:[A-Za-z0-9._:-]+)?|eui\.[0-9A-Fa-f]{16}|naa\.[0-9A-Fa-f]{16,32})$")
PROTOCOLS = ("none", "iscsi", "fc")
ARRAYS = ("pure", "solidfire")
SPEC_SECRETS = ("chap_secret",)


class SetupError(ValueError):
    """The request is not valid (nothing was done)."""


def script(name):
    return (DEPLOY / name).read_text(encoding="utf-8")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_storage(raw):
    """The storage request, checked. Returns (profile without secrets, chap_secret). A profile of protocol 'none' means: no storage paths to set up."""
    raw = dict(raw or {})
    proto = str(raw.get("protocol") or "none").lower()
    if proto not in PROTOCOLS:
        raise SetupError("the storage protocol is none, iscsi or fc")
    out = {"protocol": proto}
    if proto == "none":
        return out, ""
    portals = [str(p).strip() for p in (raw.get("portals") or []) if str(p).strip()]
    if len(portals) > 16:
        raise SetupError("at most 16 portals")
    for p in portals:
        m = PORTAL.match(p)
        if not m or (m.group(2) and not 0 < int(m.group(2)[1:]) < 65536):
            raise SetupError("the portal %r is not an address (use IP or IP:port)" % p)
    if proto == "iscsi" and not portals:
        raise SetupError("iSCSI needs at least one portal (the address of an array's iSCSI port); with none, only the initiator is set up")
    out["portals"] = portals
    ini = str(raw.get("initiator") or "").strip()
    if ini and not IQN.match(ini):
        raise SetupError("the initiator name %r is not an iqn., eui. or naa. name" % ini)
    if ini:
        out["initiator"] = ini
    user = str(raw.get("chap_user") or "").strip()
    secret = str(raw.get("chap_secret") or "")
    if user:
        if not re.match(r"^[A-Za-z0-9._@:-]{1,64}$", user):
            raise SetupError("the CHAP user name holds a character that is not allowed")
        if not secret or "\n" in secret or "\r" in secret:
            raise SetupError("a CHAP user needs a secret on one line")
        out["chap_user"] = user
    arrays = [a for a in (raw.get("arrays") or []) if a]
    if any(a not in ARRAYS for a in arrays):
        raise SetupError("the array types are %s" % ", ".join(ARRAYS))
    out["arrays"] = sorted(set(arrays))
    extra = str(raw.get("multipath_extra") or "")
    if "\x00" in extra or len(extra) > 8000:
        raise SetupError("the extra multipath text is too long or not text")
    if extra.strip():
        out["multipath_extra"] = extra
    try:
        out["replacement_timeout"] = int(15 if raw.get("replacement_timeout") in (None, "") else raw.get("replacement_timeout"))
    except (TypeError, ValueError):
        raise SetupError("the replacement timeout is a number of seconds") from None
    if not 1 <= out["replacement_timeout"] <= 600:
        raise SetupError("the replacement timeout is between 1 and 600 seconds")
    return out, secret


def clean(req):
    """The setup request, checked. Returns a plain dict. Raises SetupError."""
    r = {}
    r["name"] = str(req.get("name") or "").strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,40}$", r["name"]):
        raise SetupError("give the host a name of letters, digits, dot, dash and underscore")
    r["address"] = str(req.get("address") or "").strip()
    if not ADDRESS.match(r["address"]):
        raise SetupError("give the address of the host")
    try:
        r["port"] = int(22 if req.get("port") in (None, "") else req.get("port"))
    except (TypeError, ValueError):
        raise SetupError("the port is a number") from None
    if not 0 < r["port"] < 65536:
        raise SetupError("the port must be between 1 and 65535")
    r["user"] = str(req.get("user") or "root").strip()
    if not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$", r["user"]):
        raise SetupError("the login user name is not valid")
    roles = req.get("roles")
    roles = ["mover"] if roles is None else [roles] if isinstance(roles, str) else list(roles)
    if not roles or any(x not in ("mover", "cinder") for x in roles):
        raise SetupError("the role of a host is mover, cinder or both")
    r["roles"] = sorted(set(roles))
    for key, default in (("service_user", "openmove"), ("cinder_user", "cinder")):
        r[key] = str(req.get(key) or default).strip()
        if not ACCOUNT.match(r[key]):
            raise SetupError("the %s %r is not a plain account name" % (key.replace("_", " "), r[key]))
    r["from_address"] = str(req.get("from_address") or "").strip()
    if r["from_address"] and not re.match(r"^[A-Za-z0-9_.:/*,!-]{1,120}$", r["from_address"]):
        raise SetupError("the address to allow is not valid")
    r["cinder_python"] = str(req.get("cinder_python") or "").strip()
    if r["cinder_python"] and not re.match(r"^[A-Za-z0-9_@:./+~-]+$", r["cinder_python"]):
        raise SetupError("the Cinder interpreter path holds a character that is not allowed")
    r["jump"] = str(req.get("jump") or "").strip()
    if r["jump"] and not re.match(r"^[A-Za-z0-9_@:.,-]+$", r["jump"]):
        raise SetupError("the jump host holds a character that is not allowed")
    r["storage"], r["chap_secret"] = clean_storage(req.get("storage"))
    if r["storage"]["protocol"] != "none" and "mover" not in r["roles"]:
        raise SetupError("storage paths are set up on a migration host (role mover); a Cinder-only host does not need them")
    r["check_only"] = bool(req.get("check_only"))
    return r


def role_word(roles):
    return "both" if len(roles) == 2 else roles[0]


class Steps:
    """The steps of one setup, each with its complete output."""

    def __init__(self):
        self.items = []

    def add(self, sid, title, status, detail="", output=""):
        self.items.append({"id": sid, "title": title, "status": status, "detail": detail, "output": output})
        return status

    @property
    def failed(self):
        return any(s["status"] == "fail" for s in self.items)


def run_script(login, name, args, stdin=b"", sudo=False, timeout=900):
    """Send one deploy script to a private temporary folder on the host, run it there, remove the folder. Returns (rc, output).

    The script is not piped into bash: a command inside it that reads standard input (a package manager does) would eat the rest of the script.
    The input of the run (a secret) is the standard input of the second call only."""
    text = script(name).encode("utf-8")
    rc, out, err = login.exec(["sh", "-c", 'umask 077; d=$(mktemp -d /tmp/openmove-setup.XXXXXX) || exit 97; cat > "$d/s.sh" && echo "$d"'], stdin=text, timeout=120)
    folder = out.strip().splitlines()[-1] if rc == 0 and out.strip() else ""
    if rc != 0 or not folder.startswith("/tmp/openmove-setup."):
        raise ssh.SshError("could not place %s on the host (rc %s): %s" % (name, rc, (err or out).strip()[:500]))
    try:
        cmd = (["sudo", "-n"] if sudo else []) + ["bash", folder + "/s.sh"] + list(args)
        rc, out, err = login.exec(cmd, stdin=stdin, timeout=timeout)
    finally:
        try:
            login.exec(["rm", "-rf", folder], timeout=60)
        except ssh.SshError:
            pass
    return rc, (out + (("\n" + err) if err.strip() else "")).strip()


def storage_args(storage, check):
    a = ["--protocol", storage["protocol"], "--replacement-timeout", str(storage.get("replacement_timeout", 15))]
    for p in storage.get("portals", []):
        a += ["--portal", p]
    if storage.get("initiator"):
        a += ["--initiator", storage["initiator"]]
    if storage.get("chap_user"):
        a += ["--chap-user", storage["chap_user"], "--chap-secret-stdin"]
    for arr in storage.get("arrays", []):
        a += ["--array", arr]
    if storage.get("multipath_extra"):
        a += ["--multipath-extra-b64", base64.b64encode(storage["multipath_extra"].encode("utf-8")).decode("ascii")]
    if check:
        a.append("--check")
    return a


def parse_json_line(output):
    for ln in reversed(output.splitlines()):
        if ln.startswith("OPENMOVE-JSON "):
            try:
                return json.loads(ln[len("OPENMOVE-JSON "):])
            except ValueError:
                return None
    return None


def interpreter_of(shebang):
    """The Python that runs a program, from its first line; None for '#!/usr/bin/env python' (that says nothing about which one)."""
    m = re.match(r"^#!\s*(/\S*python[0-9.]*)(\s|$)", shebang.strip())
    return m.group(1) if m else None


def run(home, req, password, login=None, key_check=None):
    """Do the setup. req is the checked request (clean()). Returns {ok, steps, spec, storage, initiator}. Raises ssh.SshError only for the first login.

    login and key_check can be replaced (the tests do): login has exec(argv, stdin, timeout); key_check(spec) -> (ok, detail)."""
    st = Steps()
    out = {"ok": False, "steps": st.items, "spec": None, "storage": None, "initiator": "", "check_only": req["check_only"]}
    login = login or ssh.RootLogin(home, req["address"], req["port"], req["user"], password, req["jump"])
    root = req["user"] == "root"

    # 1. who we are
    rc, o, e = login.exec(["sh", "-c", "id -u; uname -sr; (. /etc/os-release 2>/dev/null && echo \"$PRETTY_NAME\"); command -v python3 || true; command -v sudo || true"], timeout=60)
    lines = o.splitlines() + [""] * 5
    uid, kernel, osname = lines[0].strip(), lines[1].strip(), lines[2].strip()
    if rc != 0 or not uid.isdigit():
        st.add("S1", "Log in with the password", "fail", "the login worked but the host answered unexpectedly (rc %s)" % rc, (o + e))
        return out
    sudo = False
    if uid != "0":
        r2, o2, e2 = login.exec(["sudo", "-n", "true"], timeout=60)
        if r2 != 0:
            st.add("S1", "Log in with the password", "fail", "%s is not root and has no passwordless sudo: use root, or give %s the right to run sudo without a password" % (req["user"], req["user"]),
                   o2 + e2)
            return out
        sudo = True
    st.add("S1", "Log in with the password", "ok", "%s on %s (%s)%s" % (osname or "Linux", kernel or "?", req["address"], "" if root else ", sudo as " + req["user"]), "")
    if not sudo and uid != "0":
        return out

    # 2. the host setup script
    ssh.ensure_key(home)
    key = ssh.public_key(home)
    args = ["--role", role_word(req["roles"]), "--user", req["service_user"], "--key", key]
    if "cinder" in req["roles"] and req["cinder_user"] != "cinder":
        args += ["--cinder-user", req["cinder_user"]]
    if req["from_address"]:
        args += ["--from", req["from_address"]]
    if req["check_only"]:
        args.append("--check")
    rc, output = run_script(login, "prepare_host.sh", args, sudo=sudo)
    st.add("S2", "Prepare the host (account, key, packages, rights, sshd)", "ok" if rc == 0 else "fail",
           "ready" if rc == 0 else "the script reported items that need attention (script exit code %s); the output below says which" % rc, output)
    if rc != 0:
        return out

    # 3. the interpreter of Cinder
    python = req["cinder_python"]
    if "cinder" in req["roles"]:
        rc, o, e = login.exec(["sh", "-c", 'p=$(command -v cinder-volume || command -v cinder-api || true); [ -n "$p" ] && head -n 1 "$p"'], timeout=60)
        found = interpreter_of(o) if rc == 0 else None
        if python:
            st.add("S3", "Interpreter of Cinder", "ok", "%s (given)" % python)
        elif found:
            python = found
            st.add("S3", "Interpreter of Cinder", "ok", "%s (read from the first line of the cinder-volume program)" % found, o)
        else:
            st.add("S3", "Interpreter of Cinder", "warn", "not found: this host has no cinder-volume program on its path, or it starts through env. Enter the interpreter that Cinder uses "
                   "(for example the python of its virtual environment), or the Cinder helper will use python3", o + e)

    # 4. the storage paths
    stor = req["storage"]
    if stor["protocol"] != "none":
        secret = (req["chap_secret"] + "\n").encode("utf-8") if stor.get("chap_user") else b""
        rc, output = run_script(login, "configure_storage.sh", storage_args(stor, req["check_only"]), stdin=secret, sudo=sudo)
        info = parse_json_line(output)
        out["storage"] = info
        if info:
            out["initiator"] = info.get("initiator") or ""
        status, detail = ("ok", "configured") if rc == 0 else ("fail", "the script reported items that need attention (exit code %s); the output below says which" % rc)
        if rc == 0 and info and stor["protocol"] == "iscsi" and info.get("sessions", 0) == 0:
            status, detail = "warn", "configured, but no iSCSI session is open yet"
        st.add("S4", "Storage paths (%s, multipath)" % stor["protocol"], status, detail, output)
        if rc != 0:
            return out
    else:
        st.add("S4", "Storage paths", "skipped", "none asked for (the host sees its storage in another way, or it is the lab's logical volumes)")

    if req["check_only"]:
        out["ok"] = not st.failed
        return out

    # 5. the registration and the proof that the key works
    spec = {"name": req["name"], "roles": req["roles"], "transport": "ssh", "address": req["address"], "port": req["port"], "user": req["service_user"],
            "sudo": "mover" in req["roles"], "notes": "set up by OpenMove on %s" % now()}
    if python:
        spec["python"] = python
    if "cinder" in req["roles"]:
        spec["cinder_user"] = req["cinder_user"]
    if req["jump"]:
        spec["jump"] = req["jump"]
    if stor["protocol"] != "none":
        spec["storage"] = {k: v for k, v in stor.items() if k not in SPEC_SECRETS}
    out["spec"] = spec
    ok, detail = (key_check or default_key_check)(home, spec)
    st.add("S5", "Log in again with the key only", "ok" if ok else "fail", detail)
    out["ok"] = not st.failed
    return out


def default_key_check(home, spec):
    try:
        rc, o, e = ssh.Host(spec, home).exec(["id", "-un"], timeout=60)
    except ssh.SshError as exc:
        return False, str(exc)
    return rc == 0, ("logged in as %s with OpenMove's key; the password is not needed any more" % o.strip()) if rc == 0 else "login failed: %s" % e.strip()
