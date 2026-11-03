# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Redaction of logs and support bundles for dark sites (docs/DIAGNOSTICS.md).

The point: nothing that identifies the customer leaves the site, and enough stays for the developer to find the fault. So values are not
blanked, they are replaced by stable tokens: the same address is always `ip-3`, the same volume always `uuid-0007`, the same server name
always `name-12`. Structure, counts, sizes, times, states, error texts and the order of events all stay; relations stay visible (this
volume, that snapshot, this host) without saying which.

Levels:
  none      nothing changed (for the site's own use).
  standard  addresses, host names, e-mail addresses, identifiers, names of people, projects, volumes, servers and hosts, paths with user
            names, secrets. Back-end names, volume type names, driver names and versions stay (the developer needs them).
  strict    also back-end, pool, volume type, zone and domain names; and free-text fields are cut to 60 characters.

The map from token to original is kept in the data folder (diag/redaction-map.json). It is never put in a bundle. The person at the site
can turn a token the developer mentions back into the original with `python -m openmove --decode name-12`.
Secrets (passwords, tokens, keys, authorization headers) are removed at every level above none, and never stored in the map. This holds for a
value under a secret-looking key and for a secret written into free text (`password=x`, `Authorization: Bearer x`).
"""
import json
import re
from pathlib import Path

LEVELS = ("none", "standard", "strict")
SECRET_KEY = re.compile(r"pass(word|wd)?|secret|token|api[_-]?key|private|credential|authorization|auth[_-]?key|x-auth", re.I)
NAME_KEYS = {"name", "display_name", "display_description", "description", "by", "customer", "prepared_by", "operator", "reviewer", "user", "user_name",
             "project", "project_name", "tenant", "server_name", "host_name", "hostname", "address", "owner", "email", "title", "comment", "evidence_by"}
POLICY_KEYS = {"backend", "source_backend", "target_backend", "volume_type", "source_type", "target_type", "type", "dtype", "src", "dst", "pool",
               "availability_zone", "zone", "domain", "volume_backend_name", "vg"}
FREE_TEXT_KEYS = {"reason", "note", "notes", "message", "detail", "error", "stderr", "stdout", "text", "evidence", "output"}
UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
HEX32 = re.compile(r"\b[0-9a-f]{32}\b")
HEX64 = re.compile(r"\b[0-9a-f]{64}\b")
IPV4 = re.compile(r"(?<![\w.-])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\w-]|\.\d)")
IPV6 = re.compile(r"(?<![\w:])(?:(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,6}:(?:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,5})?|::(?:[0-9a-fA-F]{1,4}:){0,6}[0-9a-fA-F]{1,4})(?![\w:])")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
URLHOST = re.compile(r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)(?:[^/\s@]+@)?(?P<host>\[[0-9a-fA-F:]+\]|[A-Za-z0-9][A-Za-z0-9.-]*)")
FQDN = re.compile(r"\b(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9-]*(?:\.[A-Za-z0-9][A-Za-z0-9-]*){2,}\b")
HOME_PATH = re.compile(r"(?i)(?:[A-Z]:\\Users\\|/home/|/Users/)([^\\/\s\"']+)")
LONG_SECRET = re.compile(r"\b(?:gAAAA|[A-Za-z0-9_-]{40,})\b[A-Za-z0-9_=-]*")
SAFE_FQDN = re.compile(r"(?i)\.(py|json|log|txt|md|conf|html|js|css|db|zip|sql|sh)$|^(?:ssh-ed25519|openstack|oslo|cinder|nova|keystone|neutron)\.")
SECRET_ASSIGN = re.compile(r"(?i)\b(pass(?:word|wd)?|secret|token|api[_-]?key|auth[_-]?key|credentials?|x-auth-token|authorization|private[_-]?key)(['\"]?\s*[=:]\s*)"
                           r"(?:(?:bearer|basic)\s+)?(\"[^\"]*\"|'[^']*'|[^\s,;&\"'}\]]+)")      # password=x, "token": "x", Authorization: Bearer x
BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
HOST_AT = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9-]*)@([A-Za-z0-9][A-Za-z0-9-]*)(#[\w-]+)?")      # a Cinder host string: host@backend#pool


class Redactor:
    def __init__(self, level="standard", map_path=None):
        if level not in LEVELS:
            raise ValueError("redaction level must be one of %s" % (LEVELS,))
        self.level, self.map_path = level, Path(map_path) if map_path else None
        self.map = {}                     # "kind:original" -> token
        self.counters = {}
        self.known = []                   # [(original, kind)] exact values to replace anywhere in text
        if self.map_path and self.map_path.exists():
            try:
                d = json.loads(self.map_path.read_text(encoding="utf-8"))
                self.map, self.counters = d.get("map", {}), d.get("counters", {})
            except (OSError, ValueError):
                pass

    # ---- the map ----------------------------------------------------------------------------------------------------------------
    def token(self, kind, original):
        key = "%s:%s" % (kind, original)
        t = self.map.get(key)
        if t is None:
            n = self.counters.get(kind, 0) + 1
            self.counters[kind] = n
            t = self.map[key] = "%s-%04d" % (kind, n) if kind == "uuid" else "%s-%d" % (kind, n)
        return t

    def save(self):
        if self.map_path and self.level != "none":
            self.map_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.map_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"map": self.map, "counters": self.counters}, indent=1, sort_keys=True), encoding="utf-8")
            tmp.replace(self.map_path)

    def decode(self, token):
        for key, t in self.map.items():
            if t == token:
                return key.split(":", 1)[1]
        return None

    def learn(self, kind, values):
        """Names that must be replaced wherever they appear in free text (volume names, project names, server names, host names)."""
        for v in values:
            v = str(v or "").strip()
            if len(v) >= 3 and not UUID.fullmatch(v) and v not in ("None", "none", "true", "false", "admin", "nova"):
                if (v, kind) not in self.known:
                    self.known.append((v, kind))
        self.known.sort(key=lambda x: -len(x[0]))

    # ---- text -------------------------------------------------------------------------------------------------------------------
    def text(self, s):
        if self.level == "none" or not isinstance(s, str) or not s:
            return s
        s = SECRET_ASSIGN.sub(lambda m: m.group(1) + m.group(2) + (m.group(3)[0] if m.group(3)[0] in "\"'" else "") + "[redacted]" +
                              (m.group(3)[0] if m.group(3)[0] in "\"'" else ""), s)     # secrets first, so that no later rule turns one into a token
        s = BEARER.sub(lambda m: m.group(1) + " [redacted]", s)
        for original, kind in self.known:
            if original in s:
                s = re.sub(r"(?<![A-Za-z0-9])" + re.escape(original) + r"(?![A-Za-z0-9])", lambda m, o=original, k=kind: self.token(k, o), s)
        s = URLHOST.sub(lambda m: m.group("scheme") + self._host(m.group("host")), s)
        s = EMAIL.sub(lambda m: self.token("email", m.group(0).lower()), s)
        s = HOST_AT.sub(lambda m: self._cinder_host(m), s)
        s = UUID.sub(lambda m: self.token("uuid", m.group(0).lower()), s)
        s = HEX64.sub(lambda m: self.token("hash", m.group(0).lower()), s)       # a checksum: equal checksums stay equal
        s = HEX32.sub(lambda m: self.token("uuid", m.group(0).lower()), s)
        s = IPV4.sub(lambda m: self.token("ip", m.group(0)), s)
        s = IPV6.sub(lambda m: self.token("ip", m.group(0).lower()), s)
        s = FQDN.sub(lambda m: m.group(0) if SAFE_FQDN.search(m.group(0)) else self.token("host", m.group(0).lower()), s)
        s = HOME_PATH.sub(lambda m: m.group(0).replace(m.group(1), "USER"), s)
        s = LONG_SECRET.sub("[redacted]", s)
        return s

    def _host(self, h):
        if IPV4.fullmatch(h):
            return self.token("ip", h)
        if h.startswith("["):
            return "[%s]" % self.token("ip", h.strip("[]").lower())
        return h if h in ("localhost",) else self.token("host", h.lower())

    def _cinder_host(self, m):
        host, backend, pool = m.group(1), m.group(2), m.group(3) or ""
        out = self.token("host", host.lower())
        be = self.token("backend", backend) if self.level == "strict" else backend
        pl = "#" + (self.token("backend", pool[1:]) if self.level == "strict" else pool[1:]) if pool else ""
        return "%s@%s%s" % (out, be, pl)

    # ---- structures -------------------------------------------------------------------------------------------------------------
    def data(self, obj, key=""):
        """A copy of any JSON-like structure, redacted. The key of the value decides how names are treated."""
        if self.level == "none":
            return obj
        k = str(key or "").lower()
        if isinstance(obj, dict):
            return {kk: self.data(v, kk) for kk, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.data(v, key) for v in obj]
        if isinstance(obj, str):
            if SECRET_KEY.search(k):
                return "[redacted]" if obj else obj
            if k in NAME_KEYS and obj and not UUID.fullmatch(obj):
                return self.token("name", obj)
            if self.level == "strict" and k in POLICY_KEYS and obj:
                return self.token("backend", obj)
            out = self.text(obj)
            if self.level == "strict" and k in FREE_TEXT_KEYS and len(out) > 60:
                out = out[:60] + "...[cut]"
            return out
        return obj

    def lines(self, text):
        return "\n".join(self.text(ln) for ln in str(text).splitlines())


def level_of(settings):
    lv = (settings or {}).get("redaction", "standard")
    return lv if lv in LEVELS else "standard"
