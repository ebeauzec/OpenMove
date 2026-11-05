# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""A small HTTP client built on the standard library.

Every call has a timeout and a size limit. Answers are validated: a non-JSON answer where
JSON is required is an error. Request headers (which carry tokens) never appear in an error.
TLS verification is on unless the operator turns it off, and the setting is kept so that
reports can state it.
"""
import json
import ssl
import urllib.error
import urllib.request

MAX_BODY = 32 * 1024 * 1024


READ_METHODS = ("GET", "HEAD")


class ConnectorError(Exception):
    def __init__(self, message, status=None, url=None, outcome_unknown=False):
        super().__init__(message)
        self.status = status
        self.url = url
        # True when a request that changes something may or may not have been carried out (timeout, dropped
        # connection). The caller must read the state back before it does anything else; it must never assume.
        self.outcome_unknown = outcome_unknown


class ReadOnlyViolation(ConnectorError):
    """A request that could change the cloud was attempted without a valid permit. Nothing was sent."""


def _is_login(method, url, body):
    """The Keystone token request is a POST that changes nothing, so it is the one allowed exception."""
    return method == "POST" and url.split("?", 1)[0].rstrip("/").endswith("/auth/tokens") and isinstance(body, dict) and "auth" in body


class Http:
    def __init__(self, verify=True, ca_file=None, timeout=30):
        self.verify = bool(verify)
        self.ca_file = ca_file or None
        self.timeout = float(timeout)
        if not self.verify:
            self._ctx = ssl._create_unverified_context()
        elif self.ca_file:
            self._ctx = ssl.create_default_context(cafile=self.ca_file)
        else:
            self._ctx = ssl.create_default_context()

    def request(self, method, url, headers=None, body=None, expect_json=True, permit=None):
        """Return (status, response headers as a dict with lower-case names, parsed body).

        Read-only by construction: anything but GET, HEAD and the login is refused unless a permit
        (openmove.safety.Permit) says yes right now. Any error while asking the permit is a refusal."""
        method = (method or "").upper()
        writes = method not in READ_METHODS and not _is_login(method, url, body)
        if writes:
            try:
                ok = bool(permit is not None and permit.check(method, url))
                why = "" if ok else "no permit"
            except Exception as exc:
                ok, why = False, str(exc) or type(exc).__name__
            if not ok:
                raise ReadOnlyViolation("%s %s refused, nothing was sent: %s" % (method, _safe_url(url), why), url=url)
        data = None
        hdrs = {"Accept": "application/json"}
        hdrs.update(headers or {})
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                status, rheaders, raw = resp.status, resp.headers, resp.read(MAX_BODY + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 300 and method in READ_METHODS:
                # 300 Multiple Choices is how Cinder (and others) answer their version document: it is a normal answer with the versions in the body
                try:
                    status, rheaders, raw = 300, exc.headers, exc.read(MAX_BODY + 1)
                except OSError:
                    raise ConnectorError("%s %s answered 300 and the body could not be read" % (method, _safe_url(url)), status=300, url=url) from None
            else:
                detail = ""
                try:
                    detail = exc.read(2048).decode("utf-8", "replace")
                except OSError:
                    pass
                raise ConnectorError("%s %s answered %s %s" % (method, _safe_url(url), exc.code, _brief(detail)),
                                     status=exc.code, url=url) from None
        except (urllib.error.URLError, OSError, ssl.SSLError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ConnectorError("%s %s failed: %s" % (method, _safe_url(url), reason), url=url,
                                 outcome_unknown=writes) from None
        if len(raw) > MAX_BODY:
            raise ConnectorError("answer from %s is larger than the %d byte limit" % (_safe_url(url), MAX_BODY), url=url)
        parsed = None
        if raw.strip():
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                if expect_json:
                    raise ConnectorError("answer from %s is not JSON" % _safe_url(url), status=status, url=url) from None
        elif expect_json and status != 204:
            raise ConnectorError("answer from %s is empty" % _safe_url(url), status=status, url=url)
        return status, {k.lower(): v for k, v in rheaders.items()}, parsed


def _safe_url(url):
    return url.split("?", 1)[0]


def _brief(text):
    text = " ".join(text.split())
    return text[:200]
