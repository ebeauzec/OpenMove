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


class ConnectorError(Exception):
    def __init__(self, message, status=None, url=None):
        super().__init__(message)
        self.status = status
        self.url = url


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

    def request(self, method, url, headers=None, body=None, expect_json=True):
        """Return (status, response headers as a dict with lower-case names, parsed body)."""
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
            detail = ""
            try:
                detail = exc.read(2048).decode("utf-8", "replace")
            except OSError:
                pass
            raise ConnectorError("%s %s answered %s %s" % (method, _safe_url(url), exc.code, _brief(detail)),
                                 status=exc.code, url=url) from None
        except (urllib.error.URLError, OSError, ssl.SSLError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ConnectorError("%s %s failed: %s" % (method, _safe_url(url), reason), url=url) from None
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
