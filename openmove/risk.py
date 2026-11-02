# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Workload risk (check V11, requirement R17): volumes that host a workload with a known risk of a failed migration.

What a migration tool can know from the cloud API is limited, and this module says so on every screen. It reads hints:

  * the image the volume or instance came from (os_type, os_distro, os_version, image name, hw_disk_bus, hw_qemu_guest_agent),
    from the volume's image metadata (Cinder) and the instance's metadata (Nova);
  * whether the volume is a boot volume, how big it is, and what the instance is called.

It cannot see inside the guest: whether the disk drivers are really installed, whether the file system is clean, whether a
database is mid-transaction, whether the VSS service works. A volume with no hint is NOT declared safe; it is simply not
flagged, and the report says the check looks at hints only.

Levels:
  high    a known cause of failed or unsafe migrations. Left out of the default selection, always held for a person's release
          before the cutover, and expected to be run on its own with a tested way back. The operator can still select it.
  medium  worth a decision: a boot volume (a failure is an outage), a very large volume, a guess from names.
  none    nothing known.
"""
import hashlib
import re

LEGACY_WINDOWS = re.compile(r"(2000|2003|2008|\bxp\b|vista|\bnt\b|windows[ _-]?7\b|\bwin7\b|2012(?!\s*r?2)|\bwin2k)", re.I)
EMULATED_BUS = ("ide", "sata")
LARGE_GIB = 1024
YES = ("yes", "true", "1")

LATENCY_NAMES = re.compile(r"(oracle|\bora\d|mssql|sqlserver|sql-?srv|postgres|pgsql|mysql|maria|mongo|cassandra|redis|kafka|elastic|\bdb\d*\b|-db-|_db_|database|sap-?hana|\bhana\b)", re.I)

ADVICE = {
    "mark": ["Follow the instructions recorded with the mark (who marked it and why are in the journal). Remove the mark only with the owner's agreement."],
    "clone": ["The volume was created from a snapshot or another volume. On some arrays a clone depends on its parent; a move can fail or be refused. Test it in the lab."],
    "latency": ["Latency-sensitive workload (guessed from the name): the copy adds I/O load. Move it in a quiet window, with the rate cap low, and watch its latency."],
    "legacy": ["Treat it as a project of its own: a tested restore of a copy first, in a lab, with the same Windows version.",
               "Confirm the VirtIO (or the target's) disk drivers are installed in the guest before the cutover, and that a rollback boots.",
               "Plan a window with the application owner, a full backup, and a person at the console during the cutover."],
    "agent": ["Install and enable the QEMU guest agent (and the VSS provider for Windows) so the file system can be quiesced, or accept in writing that the cutover is crash-consistent.",
              "Take an application-level backup first."],
    "bus": ["The guest sees the disk on an emulated bus; after the swap it may not find its boot disk. Test the swap on a clone first."],
    "windows": ["Run it in a separate, small batch, outside business hours, with the owner informed and a console open."],
    "boot": ["A boot volume that does not come back is an outage: keep it for a deliberate batch with a tested rollback (R2/R3)."],
    "large": ["A very large volume has a long copy and a long window in which the source can change: schedule a quiet period and watch the recheck results."],
    "unknown": ["The guest OS is not known from the cloud. Ask the owner what it runs before including it."],
    "name": ["The name suggests Windows; confirm with the owner. Treat it as Windows until you know."],
}


def _low(x):
    return str(x or "").strip().lower()


def _meta(vol, srvs):
    m = {_low(k): _low(v) for k, v in (vol.get("image_meta") or {}).items()}
    for s in srvs:
        for k, v in (s.get("metadata") or {}).items():
            m.setdefault(_low(k), _low(v))
    return m


def assess(vol, servers, roots, mark=None):
    """{'level': 'high'|'medium'|'none', 'reasons': [...], 'advice': [...]} for one inventory volume.

    mark is a person's own statement about the volume ({'by', 'note'}), for what the cloud cannot show. It makes the volume high risk."""
    srvs = [servers.get(a.get("server_id")) for a in vol.get("attachments", []) if a.get("server_id")]
    srvs = [s for s in srvs if s]
    m = _meta(vol, srvs)
    boot = vol["id"] in roots
    names = " ".join(_low(x) for x in [vol.get("name")] + [s.get("name") for s in srvs])
    text = " ".join([m.get("os_distro", ""), m.get("os_version", ""), m.get("image_name", ""), m.get("os_type", "")])
    windows = m.get("os_type") == "windows" or m.get("os_distro") == "windows" or "windows" in text
    reasons, keys, level = [], [], "none"

    def add(lvl, text_, key):
        nonlocal level
        reasons.append("%s: %s" % (lvl.upper(), text_))
        keys.append(key)
        if lvl == "high" or level == "none":
            level = lvl

    if mark:
        add("high", "marked as a risk by %s: %s" % (mark.get("by"), mark.get("note")), "mark")
    if windows:
        ver = m.get("os_version") or m.get("image_name") or "version not recorded"
        if LEGACY_WINDOWS.search(text):
            add("high", "legacy Windows guest (%s): old guests often lack current disk drivers, cannot be quiesced and may not survive a disk swap" % ver, "legacy")
        if m.get("hw_qemu_guest_agent") not in YES:
            add("high", "Windows guest without the QEMU guest agent: the file system cannot be quiesced (no VSS freeze) at the cutover", "agent")
        if m.get("hw_disk_bus") in EMULATED_BUS:
            add("high", "Windows guest on an emulated %s disk bus: the guest may not find its disk after the swap" % m["hw_disk_bus"], "bus")
        if level != "high":
            add("medium", "Windows guest: file-system and driver state cannot be seen from the cloud", "windows")
        elif boot:
            reasons.append("HIGH: it is the boot volume, so a failure is an outage of the server")
    elif re.search(r"\b(win|windows|w2k|w2k8|w2k3|w2k12|srv\d|dc\d+)", names) and not m.get("os_type"):
        add("medium", "the name suggests a Windows server (a guess from the name)", "name")
    if boot and level != "high":
        if not (m.get("os_type") or m.get("os_distro")):
            add("medium", "boot volume of an instance whose operating system is not known from the cloud", "unknown")
        else:
            add("medium", "boot volume: if it does not come back, the server is down", "boot")
    if (vol.get("snapshot_id") or vol.get("source_volid")) and level != "high":
        add("medium", "created from a %s: a clone may depend on its parent on the array" % ("snapshot" if vol.get("snapshot_id") else "volume"), "clone")
    if LATENCY_NAMES.search(names) and level != "high":
        add("medium", "the name suggests a database or latency-sensitive service (a guess from the name)", "latency")
    if int(vol.get("size") or 0) > LARGE_GIB and vol.get("attachments"):
        add("medium", "large attached volume (%d GiB): a long copy and a long time for the source to change" % int(vol["size"]), "large")
    advice = []
    for k in keys:
        for line in ADVICE.get(k, []):
            if line not in advice:
                advice.append(line)
    return {"level": level, "reasons": reasons, "advice": advice}


def assess_all(volumes, servers, roots, marks=None):
    """{volume id: assessment} for every volume that has a risk."""
    out = {}
    roots = set(roots or ())
    marks = marks or {}
    for v in volumes:
        r = assess(v, servers or {}, roots, marks.get(v["id"]))
        if r["level"] != "none":
            out[v["id"]] = r
    return out


# ---- clearance: the only way a high-risk volume can be moved -------------------------------------------------------------
KIND = "risk_clearance"
ROLES = ("operator", "reviewer")


def signature(assessment):
    """Identifies the risk as it was assessed. A clearance holds only for the same reasons; if they change, it no longer applies."""
    return hashlib.sha256("|".join(assessment["reasons"]).encode("utf-8")).hexdigest()[:12]


class ClearanceError(ValueError):
    pass


def record(journal, volume, sig, role, by, evidence, withdraw=False):
    """One person's statement about one high-risk volume. Two different people (operator and reviewer) are needed."""
    by, evidence = str(by or "").strip(), str(evidence or "").strip()
    if role not in ROLES:
        raise ClearanceError("role must be operator or reviewer")
    if len(by) < 3:
        raise ClearanceError("a clearance needs the name of the person who gives it")
    if not withdraw and len(evidence) < 20:
        raise ClearanceError("a clearance needs the evidence behind it (at least 20 characters): what was tested, what the owner agreed, what the way back is")
    journal.record(KIND, volume, {"role": role, "by": by, "evidence": evidence, "signature": sig, "withdrawn": bool(withdraw)})


def clearances(journal):
    """{volume: {'signature', 'operator': {...}, 'reviewer': {...}}} from the journal, oldest event first. A withdrawal voids the volume's clearance."""
    out = {}
    for e in reversed(journal.events(KIND, None, 100000)):
        d, v = e["data"], e["subject"]
        if d.get("withdrawn"):
            out.pop(v, None)
            continue
        cur = out.get(v)
        if not cur or cur["signature"] != d.get("signature"):
            cur = out[v] = {"signature": d.get("signature")}
        cur[d["role"]] = {"by": d["by"], "evidence": d["evidence"], "at": e["ts"]}
    return out


def active(journal, assessments):
    """{volume: signature} for volumes whose risk is cleared right now: both roles, two different people, the same risk as assessed."""
    out = {}
    for v, c in clearances(journal).items():
        a = assessments.get(v)
        if not a or a["level"] != "high" or c["signature"] != signature(a):
            continue
        op, rv = c.get("operator"), c.get("reviewer")
        if op and rv and op["by"].strip().lower() != rv["by"].strip().lower():
            out[v] = c["signature"]
    return out


# ---- marks: what the customer knows and the cloud does not show -------------------------------------------------------------
MARK_KIND = "risk_mark"


def mark(journal, volume, by, note, withdraw=False):
    by, note = str(by or "").strip(), str(note or "").strip()
    if len(by) < 3:
        raise ClearanceError("a mark needs the name of the person who makes it")
    if not withdraw and len(note) < 8:
        raise ClearanceError("a mark needs a reason (at least 8 characters), for example the application and the owner")
    journal.record(MARK_KIND, volume, {"by": by, "note": note, "withdrawn": bool(withdraw)})


def marks(journal):
    """{volume: {'by', 'note'}} in force: the newest event of each volume, unless it is a withdrawal."""
    out = {}
    for e in reversed(journal.events(MARK_KIND, None, 100000)):
        if e["data"].get("withdrawn"):
            out.pop(e["subject"], None)
        else:
            out[e["subject"]] = {"by": e["data"]["by"], "note": e["data"]["note"]}
    return out
