# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Volume classification (playbook Phase 1, Step 1.4).

Classes, first match wins:

  E  not handled by this method: an image-cache volume, encrypted, multiattach, in a group, replicated, status not
     available or in-use, a migration in a non-final state, provider_id missing or malformed
  D  attached to an instance that is not ACTIVE or PAUSED
  A  no snapshots
  B  snapshots exist, at least one was deleted in the last 60 days, and every live snapshot
     is younger than the window (the set rotates, so the watcher can wait)
  C  everything else (long-lived snapshots)

What the API cannot show is never guessed. provider_id and snapshot history live in the
Cinder database, so when they were not supplied the volume is classified without them and
the result says so in 'assumptions'. A volume with snapshots whose rotation is not known is
class C, which the staged method handles in either case.
"""
import re

PROVIDER_RE = re.compile(r"^\d+ \d+ \S+$")
IMAGE_CACHE_RE = re.compile(r"^image-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")


def is_image_cache(name, metadata):
    """Cinder's own image-volume cache keeps a read-only volume named image-<image id> in an internal project (playbook step 1.7)."""
    return bool(IMAGE_CACHE_RE.match(str(name or ""))) and str((metadata or {}).get("readonly")).lower() == "true"
OK_INSTANCE_STATES = ("ACTIVE", "PAUSED")
FINAL_MIGRATION_STATES = (None, "", "success", "error")
UNKNOWN = object()


def _truthy(v):
    return str(v).lower() in ("1", "true", "yes")


def notes_for(vol, snapshot_limit):
    out = []
    snaps = int(vol.get("snapshots") or 0)
    mn = vol.get("min_snapshot_size_gib")
    if mn not in (None, "") and int(mn) < int(vol["size"]):
        out.append("extended after snapshot")
    if snapshot_limit and snaps >= snapshot_limit:
        out.append("at snapshot limit")
    return out


def classify(vol, churn=None, instance_states=None, root_volumes=None, window_hours=14 * 24, snapshot_limit=None):
    """Classify one normalised volume record. Returns {'class', 'reason', 'notes', 'assumptions'}."""
    instance_states = instance_states or {}
    root_volumes = root_volumes or ()
    snaps = int(vol.get("snapshots") or 0)
    assumptions = []
    notes = notes_for(vol, snapshot_limit)
    suffix = ("; " + ", ".join(notes)) if notes else ""

    def result(cls, reason):
        return {"class": cls, "reason": reason, "notes": notes, "assumptions": assumptions}

    if vol.get("status") not in ("available", "in-use"):
        return result("E", "status %s" % vol.get("status"))
    if vol.get("migration_status") not in FINAL_MIGRATION_STATES:
        return result("E", "migration_status %s" % vol.get("migration_status"))
    if vol.get("image_cache"):
        return result("E", "image-cache volume: Cinder keeps it as a cache and builds it again when needed")
    if vol.get("encrypted"):
        return result("E", "encrypted")
    if _truthy(vol.get("multiattach")):
        return result("E", "multiattach")
    if vol.get("group_id") or vol.get("consistencygroup_id"):
        return result("E", "group or consistency group")
    if vol.get("replication_status") not in (None, "", "disabled", "not-capable"):
        return result("E", "replication_status %s" % vol.get("replication_status"))
    if "provider_id" in vol:
        pid = vol.get("provider_id")
        if not pid or not PROVIDER_RE.match(pid):
            return result("E", "provider_id missing or malformed (Step 1.9)")
    else:
        assumptions.append("provider_id not checked (needs database access)")

    attached = [a.get("server_id") for a in vol.get("attachments", []) if a.get("server_id")]
    known = [s for s in (instance_states.get(sid) for sid in attached) if s is not None]
    if attached and len(known) < len(attached):
        assumptions.append("instance state not known for an attachment")
    bad = [s for s in known if s.get("status") not in OK_INSTANCE_STATES]
    if bad:
        reason = "instance %s" % bad[0].get("status")
        if vol.get("id") in root_volumes:
            reason = "root-wait, " + reason
        return result("D", reason + suffix)
    if any(s.get("task_state") for s in known):
        notes.append("instance has a task in progress: wait until it clears")
        suffix = ("; " + ", ".join(notes))

    if snaps == 0:
        return result("A", "no snapshots" + suffix)
    oldest = float(vol.get("oldest_snapshot_age_h") or 0)
    if churn is None:
        assumptions.append("snapshot rotation not assessed (needs snapshot history from the database)")
        return result("C", "snapshots present, rotation unknown (oldest %.0f h)%s" % (oldest, suffix))
    ch = churn.get(vol.get("id"))
    if ch and int(ch.get("deleted_last_60d") or 0) > 0 and oldest <= window_hours:
        return result("B", "rotates: %s deleted in 60d, oldest live snapshot %.0f h%s" % (
            ch["deleted_last_60d"], oldest, suffix))
    return result("C", "long-lived snapshots (oldest %.0f h)%s" % (oldest, suffix))


def classify_all(volumes, churn=None, instance_states=None, root_volumes=None, window_days=14, snapshot_limit=None):
    """Classify every volume. Returns (list of records with class fields added, counts, assumptions)."""
    out, counts, assumed = [], {}, {}
    for v in volumes:
        r = classify(v, churn, instance_states, root_volumes, window_days * 24.0, snapshot_limit)
        rec = dict(v)
        rec.update({"class": r["class"], "reason": r["reason"], "notes": r["notes"], "assumptions": r["assumptions"]})
        out.append(rec)
        counts[r["class"]] = counts.get(r["class"], 0) + 1
        for a in r["assumptions"]:
            assumed[a] = assumed.get(a, 0) + 1
    return out, counts, assumed
