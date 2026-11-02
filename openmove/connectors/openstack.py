# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""OpenStack API access: Keystone v3 authentication, the service catalogue, versioned GETs.

The password is held in memory for the session only (to renew an expired token) and is
never logged, journaled or returned. Only read calls are provided here; calls that change
the cloud belong to engine steps (they go through the journal and the profile check).
"""
import re
from urllib.parse import urlencode, urlsplit, urlunsplit

from .httpclient import ConnectorError, Http

VERSION_SEGMENT = re.compile(r"^v\d+(\.\d+)?$")

# service types in the order they are tried
BLOCK_STORAGE_TYPES = ("volumev3", "block-storage", "volumev2", "volume")
COMPUTE_TYPES = ("compute",)
IDENTITY_TYPES = ("identity",)


def service_root(url):
    """Strip the version segment and everything after it: .../volume/v3/<project> -> .../volume/"""
    parts = urlsplit(url)
    segs = [s for s in parts.path.split("/") if s]
    keep = []
    for s in segs:
        if VERSION_SEGMENT.match(s):
            break
        keep.append(s)
    path = "/" + "/".join(keep) + ("/" if keep else "")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def keystone_v3_url(auth_url):
    parts = urlsplit(auth_url.strip())
    path = parts.path.rstrip("/")
    if not re.search(r"/v3(\.\d+)?$", path):
        path += "/v3"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class OpenStackSession:
    def __init__(self, auth_url, username, password, project_name, user_domain="Default",
                 project_domain="Default", region=None, interface="public", verify=True, ca_file=None, timeout=30):
        if not auth_url or not username or not password or not project_name:
            raise ConnectorError("auth URL, user name, password and project are required")
        self.auth_url = keystone_v3_url(auth_url)
        self.username = username
        self.project_name = project_name
        self.user_domain = user_domain or "Default"
        self.project_domain = project_domain or "Default"
        self.region = region or None
        self.interface = interface or "public"
        self.verify = bool(verify)
        self.http = Http(verify=verify, ca_file=ca_file, timeout=timeout)
        self._password = password
        self._token = None
        self.catalog = []
        self.project_id = None
        self.user_id = None

    # ---- authentication -----------------------------------------------------
    def authenticate(self):
        body = {"auth": {
            "identity": {"methods": ["password"], "password": {"user": {
                "name": self.username, "domain": {"name": self.user_domain}, "password": self._password}}},
            "scope": {"project": {"name": self.project_name, "domain": {"name": self.project_domain}}}}}
        status, headers, data = self.http.request("POST", self.auth_url + "/auth/tokens", body=body)
        token = headers.get("x-subject-token")
        if not token or not isinstance(data, dict) or "token" not in data:
            raise ConnectorError("the identity service did not return a token")
        self._token = token
        t = data["token"]
        self.catalog = t.get("catalog") or []
        self.project_id = (t.get("project") or {}).get("id")
        self.user_id = (t.get("user") or {}).get("id")
        return {"project_id": self.project_id, "user_id": self.user_id,
                "roles": [r.get("name") for r in t.get("roles", [])],
                "expires_at": t.get("expires_at")}

    @property
    def connected(self):
        return self._token is not None

    def close(self):
        self._token = None
        self._password = None
        self.catalog = []

    # ---- catalogue ----------------------------------------------------------
    def endpoint(self, service_types):
        """Return (service type, url) for the first matching service type, or (None, None)."""
        for stype in service_types:
            for svc in self.catalog:
                if svc.get("type") != stype:
                    continue
                for ep in svc.get("endpoints", []):
                    if ep.get("interface") != self.interface:
                        continue
                    if self.region and ep.get("region_id", ep.get("region")) not in (None, self.region):
                        continue
                    if ep.get("url"):
                        return stype, ep["url"].rstrip("/")
        return None, None

    # ---- requests -----------------------------------------------------------
    def _headers(self, extra=None):
        h = {"X-Auth-Token": self._token}
        h.update(extra or {})
        return h

    def get_url(self, url, params=None, headers=None, authenticated=True):
        if authenticated and not self._token:
            raise ConnectorError("not authenticated")
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        hdrs = self._headers(headers) if authenticated else dict(headers or {})
        try:
            return self.http.request("GET", url, headers=hdrs)[2]
        except ConnectorError as exc:
            if authenticated and exc.status == 401 and self._password:
                self.authenticate()
                return self.http.request("GET", url, headers=self._headers(headers))[2]
            raise

    def get(self, service_types, path, params=None, microversion=None):
        stype, base = self.endpoint(service_types)
        if not base:
            raise ConnectorError("no %s endpoint in the service catalogue" % "/".join(service_types))
        headers = {}
        if microversion:
            kind = "volume" if service_types is BLOCK_STORAGE_TYPES else "compute"
            headers["OpenStack-API-Version"] = "%s %s" % (kind, microversion)
            if kind == "compute":
                headers["X-OpenStack-Nova-API-Version"] = microversion
        return self.get_url(base + path, params=params, headers=headers)

    def paginate(self, service_types, path, key, params=None, page=500, microversion=None, max_items=1000000):
        """Collect every item of a list call, following the marker."""
        items, marker, seen = [], None, set()
        while True:
            q = dict(params or {})
            q["limit"] = page
            if marker:
                q["marker"] = marker
            data = self.get(service_types, path, q, microversion)
            batch = (data or {}).get(key)
            if not isinstance(batch, list):
                raise ConnectorError("unexpected answer from %s: no '%s' list" % (path, key))
            items.extend(batch)
            if len(batch) < page or not batch:
                return items
            marker = batch[-1].get("id")
            if marker is None or marker in seen:
                raise ConnectorError("paging of %s does not advance" % path)
            seen.add(marker)
            if len(items) > max_items:
                raise ConnectorError("%s returned more than %d items" % (path, max_items))
