# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The host registry, the readiness checks and the fabric (docs/MOVER.md).

A host is a machine OpenMove runs commands on, reached by SSH with OpenMove's own key (connectors/ssh.py), or the machine OpenMove itself
runs on ("local"). It has a role:
  mover    a Linux host that sees the arrays' devices. The data mover (resources/mover/mover.py) runs there. It may be a cinder-volume host
           that already exists (nothing new is installed on it except the mover's own folder, which `remove` takes away again).
  cinder   a host with the Cinder code and cinder.conf (a cinder-api or cinder-volume host). The Cinder helper runs there through the
           interpreter Cinder uses. One host can have both roles.
The registry is hosts.json in the data folder. It holds no password and no key, only where the key file is.

The fabric is what the engine uses: the data mover, the Cinder helper and the array adapter for the back-end family of the run.
"""
import json
import re
from pathlib import Path

from .arrays import AdapterError, adapter_for
from .connectors import ssh

ROLES = ("mover", "cinder")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,40}$")
FIELDS = ("name", "role", "transport", "address", "port", "user", "sudo", "python", "cinder_user", "mover_dir", "mover_python", "jump", "notes", "arrays",
          "rate_mbps", "log_paths", "roles", "storage")


class HostError(ValueError):
    pass


def path(home):
    return Path(home) / "hosts.json"


def load(home):
    try:
        return json.loads(path(home).read_text(encoding="utf-8")).get("hosts", [])
    except (OSError, ValueError):
        return []


def save(home, hosts):
    p = path(home)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"hosts": hosts}, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def clean(spec):
    s = {k: spec[k] for k in FIELDS if k in spec and spec[k] not in (None, "")}
    if not NAME.match(str(s.get("name", ""))):
        raise HostError("a host needs a name of letters, digits, dot, dash and underscore")
    roles = s.get("roles") or [s.get("role") or "mover"]
    if isinstance(roles, str):
        roles = [roles]
    if not roles or any(r not in ROLES for r in roles):
        raise HostError("the role of a host is mover, cinder or both")
    s["roles"], s["role"] = sorted(set(roles)), sorted(set(roles))[0]
    s["transport"] = s.get("transport") or "ssh"
    if s["transport"] not in ("ssh", "local"):
        raise HostError("the transport is ssh or local")
    if s["transport"] == "ssh":
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9.:_-]*$", str(s.get("address", ""))):
            raise HostError("a host reached by SSH needs an address")
        s["port"] = int(s.get("port") or 22)
        if not 0 < s["port"] < 65536:
            raise HostError("the port must be between 1 and 65535")
        if not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$", str(s.get("user") or "root")):
            raise HostError("the user name is not valid")
        s["user"] = s.get("user") or "root"
    s["sudo"] = bool(s.get("sudo"))
    if "storage" in s:
        if not isinstance(s["storage"], dict) or "chap_secret" in s["storage"]:
            raise HostError("the storage profile of a host is a plain record and never holds a secret")
    for k in ("python", "mover_python", "cinder_user", "mover_dir", "jump"):
        if k in s and not re.match(r"^[A-Za-z0-9_@:./\\~+-]+$", str(s[k])):
            raise HostError("%s contains a character that is not allowed" % k)
    return s


def upsert(home, spec):
    s = clean(spec)
    hosts = [h for h in load(home) if h["name"] != s["name"]]
    hosts.append(s)
    save(home, sorted(hosts, key=lambda h: h["name"]))
    return s


def remove(home, name):
    hosts = load(home)
    keep = [h for h in hosts if h["name"] != name]
    save(home, keep)
    return len(keep) != len(hosts)


def host(home, name):
    spec = next((h for h in load(home) if h["name"] == name), None)
    if spec is None:
        raise HostError("no host named %r" % name)
    return ssh.Host(spec, home)


def hosts_with(home, role):
    return [ssh.Host(h, home) for h in load(home) if role in (h.get("roles") or [h.get("role")])]


def view(home):
    """For the screen: the registry without anything secret, plus whether each host key is pinned."""
    out = []
    for h in load(home):
        d = ssh.describe(h)
        d["roles"] = h.get("roles") or [h.get("role")]
        d["pinned"] = h.get("transport") == "local" or ssh.is_pinned(home, h.get("address", ""), h.get("port") or 22)
        out.append(d)
    return {"hosts": out, "public_key": ssh.public_key(home), "ssh": ssh.platform_notes()}


# ---- readiness --------------------------------------------------------------------------------------------------------------------
def rec(cid, title, ok, detail, hint="", level=None):
    return {"id": cid, "title": title, "status": level or ("pass" if ok else "fail"), "detail": detail, "hint": hint}


def readiness(h, home, source=None, target=None, family="lvm", bench=False):
    """Checks of one host. Each is {id, title, status pass|warn|fail, detail, hint}. Reads only (the data mover is placed on the host)."""
    out = []
    spec = h.spec
    roles = spec.get("roles") or [spec.get("role")]
    if h.local:
        out.append(rec("H1", "Reached", True, "this machine"))
    else:
        pinned = ssh.is_pinned(home, spec["address"], spec.get("port") or 22)
        out.append(rec("H1", "Host key pinned", pinned, "the host key of %s:%s is pinned" % (spec["address"], spec.get("port") or 22) if pinned else
                       "the host key is not pinned yet", "Check the fingerprint with the host's administrator, then press Trust host key."))
        if not pinned:
            return out
        try:
            rc, o, e = h.exec(["true"], timeout=30)
            out.append(rec("H2", "Key login works", rc == 0, "logged in as %s" % spec.get("user") if rc == 0 else "login failed: %s" % e.strip(),
                           "Add OpenMove's public key to ~/.ssh/authorized_keys of that user on the host."))
        except ssh.SshError as exc:
            out.append(rec("H2", "Key login works", False, str(exc), "Add OpenMove's public key to ~/.ssh/authorized_keys of that user on the host."))
            return out
    if "mover" in roles:
        try:
            info = h.install_mover()
            out.append(rec("H3", "Data mover placed and checked", True, "%s (sha256 %s...)" % (info["path"], info["sha256"][:12])))
            pr = h.mover("probe")
        except ssh.SshError as exc:
            out.append(rec("H3", "Data mover placed and checked", False, str(exc), "Python 3.6 or later and write access to the user's home are needed."))
            return out
        pv = tuple(int(x) for x in pr["python"].split(".")[:2])
        out.append(rec("H4", "Python 3.6 or later", pv >= (3, 6), "Python %s" % pr["python"], "Install python3 on the host."))
        root = pr.get("euid") == 0
        if not root and not spec.get("sudo"):
            out.append(rec("H5", "Rights to manage volumes", False, "logged in as %s without root and without sudo" % pr.get("user"), "Use root or set sudo for this host (passwordless sudo)."))
        else:
            out.append(rec("H5", "Rights to manage volumes", True, "root" if root else "sudo -n as %s" % pr.get("user")))
        free = pr.get("free_bytes") or 0
        out.append(rec("H6", "Room for the mover's own folder", free > 200 * 1024 * 1024, "%d MiB free" % (free // 1048576), "Free some space or set mover_dir."))
        tools = pr.get("tools", {})
        if family == "lvm":
            need = [t for t in ("lvs", "lvcreate", "lvremove", "lvrename", "blockdev") if not tools.get(t)]
            out.append(rec("H7", "Tools for the LVM adapter", not need, "all present" if not need else "missing: " + ", ".join(need), "Install lvm2."))
        else:
            out.append(rec("H7", "Tools for iSCSI and multipath", bool(tools.get("iscsiadm") and tools.get("multipath")),
                           "iscsiadm %s, multipath %s" % ("present" if tools.get("iscsiadm") else "missing", "present" if tools.get("multipath") else "missing"),
                           "Install open-iscsi and multipath-tools."))
        if family == "lvm" and source and target:
            vgs = pr.get("volume_groups") or []
            try:
                ad = adapter_for("lvm", h)
                sv, tv = ad.vg_of(source), ad.vg_of(target)
                out.append(rec("H8", "Sees both back-ends' storage", True, "source %s -> %s, target %s -> %s" % (source, sv, target, tv)))
                if bench:
                    lv = next((r for r in ad.lvs(source) if r["name"].startswith("volume-")), None)
                    if lv:
                        b = h.mover("bench", {"dev": lv["path"], "bytes": 64 * 1024 * 1024})
                        out.append(rec("H9", "Copy throughput measured", b["mib_per_s"] > 5, "%.0f MiB/s reading %d MiB" % (b["mib_per_s"], b["bytes"] // 1048576),
                                       "Slow reads make long copies; check the path to the storage.", level=None if b["mib_per_s"] > 5 else "warn"))
            except AdapterError as exc:
                out.append(rec("H8", "Sees both back-ends' storage", False, "%s (volume groups visible: %s)" % (exc, ", ".join(vgs) or "none"),
                               "The data mover host must see the logical volumes of both back-ends."))
    if "mover" in roles:
        out += array_checks(h)
    if "cinder" in roles:
        try:
            for be in [b for b in (source, target) if b]:
                r = h.cinder("find-host", {"backend": be})
                out.append(rec("C1", "Cinder helper finds back-end %s" % be, bool(r["hosts"]), ", ".join(r["hosts"]) or "no cinder-volume service for it",
                               "Check the interpreter (python) and the user that can read cinder.conf."))
            r = h.cinder("conf", {"options": ["DEFAULT.volume_name_template", "DEFAULT.snapshot_name_template"]})["options"]
            ok = r.get("DEFAULT.volume_name_template") == "volume-%s" and r.get("DEFAULT.snapshot_name_template") == "snapshot-%s"
            out.append(rec("C2", "Name templates are the defaults (P5)", ok, json.dumps(r), "The method assumes volume-%s and snapshot-%s.", level=None if ok else "warn"))
        except ssh.SshError as exc:
            out.append(rec("C1", "Cinder helper runs", False, str(exc), "Set the interpreter Cinder uses (python) and, if needed, the user (cinder_user)."))
    return out


def array_check(h, row):
    """One call that changes nothing, to prove that the stored login of an array works and that the array is one the adapter knows. (ok, detail)."""
    try:
        if row["family"] == "solidfire":
            r = h.mover("sf.call", {"array": row["name"], "method": "GetClusterVersionInfo"})["reply"] or {}
            ver = r.get("clusterVersion") or "unknown version"
            return True, "Element %s at %s" % (ver, row.get("mgmt"))
        r = h.mover("pure.cli", {"array": row["name"], "argv": ["purearray", "list"]})
        if r["rc"] != 0:
            return False, "the Purity CLI answered with rc %s: %s" % (r["rc"], (r["stderr"] or r["stdout"]).strip())
        return True, "FlashArray at %s: %s" % (row.get("mgmt"), r["stdout"].strip().splitlines()[-1] if r["stdout"].strip() else "answered")
    except ssh.SshError as exc:
        return False, str(exc)


def array_checks(h):
    """H10: each array whose login is stored on the host answers. Nothing is checked when no login is stored (the LVM lab, or a host that is not set up for arrays yet)."""
    try:
        rows = h.mover("array.list")["arrays"]
    except ssh.SshError:
        return []
    out = []
    for row in rows:
        ok, detail = array_check(h, row)
        out.append(rec("H10", "Array %s answers" % row["name"], ok, detail,
                       "Store the login again, check the management address from the host, and for a FlashArray add the host's key to the array user (docs/ARRAY_API.md)."))
    return out


def ready(checks):
    return not any(c["status"] == "fail" for c in checks)


# ---- the fabric -------------------------------------------------------------------------------------------------------------------
class Fabric:
    """What the staged engine needs: the data mover, the Cinder helper and the array adapter, for one source and one target back-end."""

    def __init__(self, mover, cinder, family, source, target, home=None, settings=None):
        self.mover_host, self.cinder_host, self.family = mover, cinder, family
        self.source, self.target, self.home, self.settings = source, target, home, dict(settings or {})
        self.adapter = adapter_for(family, mover, (mover.spec.get("arrays") or {}))

    def helper(self, verb, args=None, timeout=180):
        return self.cinder_host.cinder(verb, args, timeout=timeout)

    def rate(self):
        return self.mover_host.spec.get("rate_mbps")

    def check(self, home=None):
        checks = readiness(self.mover_host, home or self.home, self.source, self.target, self.family)
        if self.cinder_host is not self.mover_host:
            checks += readiness(self.cinder_host, home or self.home, self.source, self.target, self.family)
        return checks

    def ready(self):
        checks = self.check()
        bad = [c for c in checks if c["status"] == "fail"]
        return not bad, "; ".join("%s: %s" % (c["title"], c["detail"]) for c in bad)

    def hosts(self):
        return [self.mover_host] if self.mover_host is self.cinder_host else [self.mover_host, self.cinder_host]


def build_fabric(home, family, source, target):
    """The fabric from the registry: the first mover host and the first Cinder host. None when either is missing."""
    movers, cinders = hosts_with(home, "mover"), hosts_with(home, "cinder")
    if not movers or not cinders:
        return None
    return Fabric(movers[0], cinders[0], family, source, target, home)
