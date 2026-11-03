# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Platform discovery (R9): find out what the cloud is before anything is planned.

Everything here is read only. A source that is not available (for example because the
account is not an administrator) is recorded as a warning and never stops discovery.
The result contains no credentials.
"""
from datetime import datetime, timezone

from . import backends, profiles, releases
from .connectors.httpclient import ConnectorError
from .connectors.openstack import BLOCK_STORAGE_TYPES, COMPUTE_TYPES, service_root


def pick_current(versions):
    """From a version-discovery document pick the entry that is CURRENT (else the last one)."""
    if isinstance(versions, dict):
        versions = versions.get("versions", versions.get("version"))
        if isinstance(versions, dict):
            versions = versions.get("values", [versions])
    if not isinstance(versions, list) or not versions:
        return None
    current = [v for v in versions if str(v.get("status", "")).upper() == "CURRENT"]
    return (current or versions)[-1]


def _api(session, service_types, name, warnings):
    stype, url = session.endpoint(service_types)
    if not url:
        warnings.append("%s: no endpoint in the service catalogue" % name)
        return {"available": False}
    root = service_root(url)
    info = {"available": True, "service_type": stype, "endpoint": url, "root": root}
    try:
        doc = session.get_url(root, authenticated=False)
        cur = pick_current(doc)
        if not cur:
            raise ConnectorError("no version in the discovery document")
        info.update({"version_id": cur.get("id"), "max": cur.get("version") or None,
                     "min": cur.get("min_version") or None, "status": cur.get("status")})
    except ConnectorError as exc:
        warnings.append("%s: version discovery failed: %s" % (name, exc))
        info.update({"max": None, "min": None})
    return info


def decode_hypervisor_version(value):
    """Nova reports 2012000 for 2.12.0."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return "%d.%d.%d" % (v // 1000000, (v // 1000) % 1000, v % 1000)


def _backend_name(pool_name):
    """'host@backend#pool' -> 'backend'"""
    return pool_name.split("@", 1)[1].split("#", 1)[0] if "@" in pool_name else pool_name


def discover(session, journal=None, profile_table=None):
    warnings = []
    out = {"collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "tls_verification": session.verify, "warnings": warnings}

    # identity
    try:
        doc = session.get_url(session.auth_url, authenticated=False)
        cur = pick_current(doc)
        out["identity"] = {"available": True, "endpoint": session.auth_url, "version_id": (cur or {}).get("id")}
    except ConnectorError as exc:
        warnings.append("identity: version discovery failed: %s" % exc)
        out["identity"] = {"available": False, "endpoint": session.auth_url}

    out["block_storage"] = _api(session, BLOCK_STORAGE_TYPES, "block storage", warnings)
    out["compute"] = _api(session, COMPUTE_TYPES, "compute", warnings)

    inference = releases.infer(out["block_storage"].get("max"), out["compute"].get("max"))
    out["release"] = inference
    out["profile"] = profiles.status_for(inference, journal, profile_table)
    out["read_only"] = out["profile"]["status"] != "validated"

    # cinder services and back-ends (administrator only)
    out["cinder_services"], out["backends"] = [], []
    if out["block_storage"].get("available"):
        try:
            data = session.get(BLOCK_STORAGE_TYPES, "/os-services")
            out["cinder_services"] = [
                {k: s.get(k) for k in ("binary", "host", "zone", "status", "state", "disabled_reason", "updated_at")}
                for s in (data or {}).get("services", [])]
        except ConnectorError as exc:
            warnings.append("block storage services not available: %s" % exc)
        try:
            data = session.get(BLOCK_STORAGE_TYPES, "/scheduler-stats/get_pools", {"detail": "True"})
            for p in (data or {}).get("pools", []):
                cap = p.get("capabilities", {})
                out["backends"].append({
                    "pool": p.get("name"), "backend": cap.get("volume_backend_name") or _backend_name(p.get("name", "")),
                    "vendor": cap.get("vendor_name"), "driver_version": cap.get("driver_version"),
                    "protocol": cap.get("storage_protocol"),
                    "total_gb": cap.get("total_capacity_gb"), "free_gb": cap.get("free_capacity_gb"),
                    "provisioned_gb": cap.get("provisioned_capacity_gb"),
                    "max_over_subscription_ratio": cap.get("max_over_subscription_ratio"),
                    "reserved_percentage": cap.get("reserved_percentage"),
                    "replication_enabled": cap.get("replication_enabled"),
                    "multiattach": cap.get("multiattach"), "thin": cap.get("thin_provisioning_support"),
                    "family": backends.family_of(cap.get("vendor_name"), cap.get("storage_protocol"))})
        except ConnectorError as exc:
            warnings.append("back-end pools not available: %s" % exc)

    # compute hosts (administrator only)
    out["compute_hosts"] = []
    if out["compute"].get("available"):
        try:
            data = session.get(COMPUTE_TYPES, "/os-hypervisors/detail")
            for h in (data or {}).get("hypervisors", []):
                out["compute_hosts"].append({
                    "host": h.get("hypervisor_hostname"), "type": h.get("hypervisor_type"),
                    "version_raw": h.get("hypervisor_version"),
                    "version": decode_hypervisor_version(h.get("hypervisor_version")),
                    "state": h.get("state"), "status": h.get("status"), "running_vms": h.get("running_vms")})
        except ConnectorError as exc:
            warnings.append("compute hosts not available: %s" % exc)

    if out["profile"]["status"] != "validated":
        warnings.append("read only: %s" % out["profile"]["reason"])
    return out
