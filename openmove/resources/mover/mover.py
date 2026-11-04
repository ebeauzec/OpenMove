# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""OpenMove data mover: the helper that OpenMove ships to a migration host over SSH (docs/MOVER.md).

It is one self-contained file that uses only the Python 3 standard library (3.6 or later). OpenMove sends it to the host, checks its hash,
and runs it as `python3 <file> <verb>` with the arguments as one JSON object on standard input. It answers with one JSON object on standard
output: {"ok": true, ...} or {"ok": false, "error": "..."}.

What it does, and what it never does:
  * It keeps everything in its own folder (OPENMOVE_MOVER_HOME, default ~/.openmove-mover) and `cleanup` removes that folder.
  * It writes only to devices it created itself (a logical volume that carries the tag "openmove") or to files inside its own folder.
    It reads other devices (the source volume, the source snapshots) and never writes to them.
  * It removes only what it created (tag "openmove"); the single exception is `lv.remove_source_snapshots`, which removes named snapshots
    of one named origin, and nothing else.
  * It never runs a shell: every program is started with an argument list.
  * It calls an array only through a short, fixed list of calls per family (sf.call for SolidFire, pure.cli for FlashArray; docs/ARRAY_API.md).
    A call off the list is refused. It changes only clones in the migration account (SolidFire) and volumes it made itself (FlashArray).
    The login of an array is a root-only file in its own folder; it is never returned and never logged.
  * A copy or a hash runs as a background job that survives the loss of the SSH connection, writes its progress to a status file, and can
    be started again: a copy compares first and writes only the blocks that differ, so repeating it is safe and cheap.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback

VERSION = "1"
HOME = os.environ.get("OPENMOVE_MOVER_HOME") or os.path.join(os.path.expanduser("~"), ".openmove-mover")
JOBS = os.path.join(HOME, "jobs")
TAG = "openmove"
CHUNK = 4 * 1024 * 1024
NAME_OK = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$")
JOB_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{3,80}$")
TOOLS = ("lvm", "lvs", "lvcreate", "lvremove", "lvrename", "blockdev", "iscsiadm", "multipath", "sha256sum")


class MoverError(Exception):
    pass


def run(cmd, timeout=120):
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    except OSError as exc:
        raise MoverError("could not start %s: %s" % (cmd[0], exc))
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        raise MoverError("%s timed out after %d s" % (cmd[0], timeout))
    return p.returncode, out, err


def which(name):
    for d in os.environ.get("PATH", "").split(os.pathsep) + ["/usr/sbin", "/sbin"]:
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def name_ok(value, what):
    if not isinstance(value, str) or not NAME_OK.match(value) or len(value) > 120:
        raise MoverError("%s %r is not a safe name" % (what, value))
    return value


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---- logical volumes (the lab back-ends and any host that sees an array through LVM) ---------------------------------------------
def lvs(vg=None):
    cmd = ["lvs", "--noheadings", "--separator", "|", "--units", "b", "--nosuffix", "-o", "lv_name,vg_name,lv_size,lv_attr,origin,lv_tags,lv_path"]
    if vg:
        cmd.append(name_ok(vg, "volume group"))
    rc, out, err = run(cmd)
    if rc != 0:
        raise MoverError("lvs failed: %s" % err.strip())
    rows = []
    for line in out.splitlines():
        p = [x.strip() for x in line.strip().split("|")]
        if len(p) < 7:
            continue
        rows.append({"name": p[0], "vg": p[1], "bytes": int(float(p[2])), "attr": p[3], "origin": p[4], "tags": [t for t in p[5].split(",") if t],
                     "path": p[6]})
    return rows


def lv_find(vg, name):
    for r in lvs(vg):
        if r["name"] == name:
            return r
    return None


def lv_ours(vg, name):
    r = lv_find(vg, name)
    if r is None:
        raise MoverError("no logical volume %s/%s" % (vg, name))
    if TAG not in r["tags"]:
        raise MoverError("%s/%s was not created by OpenMove (no %r tag): it is not touched" % (vg, name, TAG))
    return r


def v_lv_list(a):
    return {"lvs": lvs(a.get("vg"))}


def v_lv_create(a):
    vg, name, size = name_ok(a["vg"], "volume group"), name_ok(a["name"], "name"), int(a["bytes"])
    if size <= 0:
        raise MoverError("the size must be positive")
    have = lv_find(vg, name)
    if have:
        if TAG in have["tags"] and have["bytes"] >= size:
            return {"lv": have, "existed": True}
        raise MoverError("%s/%s already exists" % (vg, name))
    cmd = ["lvcreate", "-y", "-n", name, "-L", "%db" % size, "--addtag", TAG, vg]
    rc, out, err = run(cmd)
    if rc != 0:
        raise MoverError("lvcreate failed: %s" % err.strip())
    return {"lv": lv_find(vg, name), "existed": False}


def v_lv_snapshot(a):
    vg, origin, name, size = name_ok(a["vg"], "volume group"), name_ok(a["origin"], "origin"), name_ok(a["name"], "name"), int(a["bytes"])
    have = lv_find(vg, name)
    if have:
        if TAG in have["tags"] and have["origin"] == origin:
            return {"lv": have, "existed": True}
        raise MoverError("%s/%s already exists" % (vg, name))
    lv_ours(vg, origin)
    cmd = ["lvcreate", "-y", "-s", "-n", name, "-L", "%db" % size, "--addtag", TAG, "%s/%s" % (vg, origin)]
    rc, out, err = run(cmd)
    if rc != 0:
        raise MoverError("lvcreate (snapshot) failed: %s" % err.strip())
    return {"lv": lv_find(vg, name), "existed": False}


def v_lv_extend(a):
    """Grow a staging volume that OpenMove made (a tenant extended the source volume while it was being copied). Never shrinks."""
    vg, name, size = name_ok(a["vg"], "volume group"), name_ok(a["name"], "name"), int(a["bytes"])
    have = lv_ours(vg, name)
    if have["bytes"] == size:
        return {"lv": have, "existed": True}
    if size < have["bytes"]:
        raise MoverError("%s/%s has %d bytes; it is never made smaller (asked for %d)" % (vg, name, have["bytes"], size))
    rc, out, err = run(["lvextend", "-L", "%db" % size, "%s/%s" % (vg, name)])
    if rc != 0:
        raise MoverError("lvextend failed: %s" % err.strip())
    return {"lv": lv_find(vg, name), "existed": False}


def v_lv_rename(a):
    vg, old, new = name_ok(a["vg"], "volume group"), name_ok(a["old"], "name"), name_ok(a["new"], "new name")
    if lv_find(vg, new):
        have = lv_find(vg, new)
        if lv_find(vg, old) is None and TAG in have["tags"]:
            return {"lv": have, "existed": True}
        raise MoverError("%s/%s already exists" % (vg, new))
    lv_ours(vg, old)
    rc, out, err = run(["lvrename", vg, old, new])
    if rc != 0:
        raise MoverError("lvrename failed: %s" % err.strip())
    return {"lv": lv_find(vg, new), "existed": False}


def v_lv_remove(a):
    vg, name = name_ok(a["vg"], "volume group"), name_ok(a["name"], "name")
    if lv_find(vg, name) is None:
        return {"removed": False, "absent": True}
    lv_ours(vg, name)
    # a snapshot of this volume that OpenMove made goes first; one it did not make stops the removal
    for r in lvs(vg):
        if r["origin"] == name:
            if TAG not in r["tags"]:
                raise MoverError("%s/%s has a snapshot %s that OpenMove did not create; it is not removed" % (vg, name, r["name"]))
            rc, out, err = run(["lvremove", "-f", "%s/%s" % (vg, r["name"])])
            if rc != 0:
                raise MoverError("lvremove failed: %s" % err.strip())
    rc, out, err = run(["lvremove", "-f", "%s/%s" % (vg, name)])
    if rc != 0:
        raise MoverError("lvremove failed: %s" % err.strip())
    return {"removed": True}


def v_lv_remove_source_snapshots(a):
    """The one removal of something OpenMove did not create: named snapshots of one named origin, after the move is signed off."""
    vg, origin = name_ok(a["vg"], "volume group"), name_ok(a["origin"], "origin")
    names = [name_ok(n, "snapshot") for n in a["names"]]
    removed = []
    for r in lvs(vg):
        if r["name"] in names:
            if r["origin"] != origin:
                raise MoverError("%s is not a snapshot of %s; it is not removed" % (r["name"], origin))
            rc, out, err = run(["lvremove", "-f", "%s/%s" % (vg, r["name"])])
            if rc != 0:
                raise MoverError("lvremove failed: %s" % err.strip())
            removed.append(r["name"])
    return {"removed": removed}


# ---- devices and jobs --------------------------------------------------------------------------------------------------------------
def dev_bytes(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    rc, out, err = run(["blockdev", "--getsize64", path])
    if rc != 0:
        raise MoverError("cannot read the size of %s: %s" % (path, err.strip()))
    return int(out.strip())


def check_readable(path):
    if not isinstance(path, str) or not (path.startswith("/dev/") or path.startswith(HOME + os.sep)):
        raise MoverError("%r is not a device path or a file in the mover folder" % (path,))
    if not os.path.exists(path):
        raise MoverError("%s does not exist" % path)


def check_writable(path):
    """Only a logical volume with our tag, or a file inside our own folder."""
    check_readable(path)
    if path.startswith(HOME + os.sep):
        return
    rc, out, err = run(["lvs", "--noheadings", "-o", "lv_tags", path])
    if rc != 0 or TAG not in [t.strip() for t in out.strip().split(",")]:
        raise MoverError("%s is not a device OpenMove created: it is never written" % path)


def job_dir(job):
    if not JOB_OK.match(str(job)):
        raise MoverError("%r is not a job id" % (job,))
    return os.path.join(JOBS, job)


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:                 # Windows only: a reader holds the file for a moment; Linux never gets here
            if attempt == 39:
                raise
            time.sleep(0.05)


def read_json(path):
    with open(path) as f:
        return json.load(f)


def pid_alive(pid):
    if os.name == "nt":                         # os.kill(pid, 0) would end the process on Windows (used by the tests and a local host)
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return bool(ok) and code.value == 259               # STILL_ACTIVE
        except (OSError, ValueError, TypeError, AttributeError):
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def v_job_start(a):
    """Start a copy, a hash or a compare in the background. The same job id again returns the job as it is (never a second process)."""
    verb = a["verb"]
    if verb not in ("copy", "hash", "compare"):
        raise MoverError("a job is a copy, a hash or a compare")
    d = job_dir(a["job"])
    args = a["args"]
    if verb == "copy":
        check_readable(args["src"])
        check_writable(args["dst"])
    elif verb == "hash":
        check_readable(args["dev"])
    else:
        check_readable(args["a"])
        check_readable(args["b"])
    if os.path.exists(os.path.join(d, "status.json")):
        st = job_status({"job": a["job"]})
        if st["state"] == "running":
            return {"job": st, "existed": True}
        if st["state"] == "done" and a.get("reuse", True):
            return {"job": st, "existed": True}
    os.makedirs(d, exist_ok=True)
    write_json(os.path.join(d, "job.json"), {"verb": verb, "args": args})
    write_json(os.path.join(d, "status.json"), {"job": a["job"], "verb": verb, "state": "running", "pid": None, "done": 0, "total": 0,
                                                "started": now(), "updated": now(), "result": None, "error": ""})
    log = open(os.path.join(d, "log.txt"), "ab")
    kw = {"start_new_session": True} if os.name != "nt" else {}
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "run-job", a["job"]], stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kw)
    return {"job": read_json(os.path.join(d, "status.json")), "existed": False}


def job_status(a):
    d = job_dir(a["job"])
    p = os.path.join(d, "status.json")
    if not os.path.exists(p):
        raise MoverError("no job %s" % a["job"])
    st = read_json(p)
    if st["state"] == "running" and st.get("pid") and not pid_alive(st["pid"]):
        time.sleep(0.3)                         # it may have just finished
        st = read_json(p)
        if st["state"] == "running":
            st["state"] = "dead"
            st["error"] = "the job process is gone before it finished; start the job again to continue"
    return st


def v_job_status(a):
    return {"job": job_status(a)}


def v_job_cancel(a):
    st = job_status(a)
    if st["state"] == "running" and st.get("pid"):
        try:
            os.kill(int(st["pid"]), 15)
        except OSError:
            pass
        st["state"] = "cancelled"
        write_json(os.path.join(job_dir(a["job"]), "status.json"), st)
    return {"job": st}


class Progress:
    def __init__(self, job, total):
        self.path, self.last = os.path.join(job_dir(job), "status.json"), 0.0
        self.st = read_json(self.path)
        self.st["total"], self.st["pid"] = total, os.getpid()

    def update(self, done, force=False):
        t = time.time()
        if force or t - self.last >= 1.0:
            self.st["done"], self.st["updated"], self.last = done, now(), t
            write_json(self.path, self.st)


def read_exact(f, n):
    buf = b""
    while len(buf) < n:
        part = f.read(n - len(buf))
        if not part:
            break
        buf += part
    return buf


def throttle(started, done, mbps):
    if mbps:
        want = done / (float(mbps) * 1024 * 1024)
        spent = time.time() - started
        if want > spent:
            time.sleep(want - spent)


def zero_padded(f, off, n, avail):
    if off >= avail:
        return b"\0" * n
    data = read_exact(f, min(n, avail - off))
    return data + b"\0" * (n - len(data))


def run_copy(job, a, prog):
    src, dst = a["src"], a["dst"]
    nsrc, ndst = dev_bytes(src), dev_bytes(dst)
    total = int(a.get("bytes") or ndst)
    if total > ndst:
        raise MoverError("the destination (%d bytes) is smaller than the copy (%d bytes)" % (ndst, total))
    if nsrc > total:
        raise MoverError("the source (%d bytes) is larger than the copy (%d bytes): it would be cut off" % (nsrc, total))
    diff, mbps, h = a.get("diff", True), a.get("rate_mbps"), hashlib.sha256()
    prog.st["total"] = total
    written, off, started = 0, 0, time.time()
    with open(src, "rb", buffering=0) as fs, open(dst, "r+b", buffering=0) as fd:
        while off < total:
            n = min(CHUNK, total - off)
            data = zero_padded(fs, off, n, nsrc)
            h.update(data)
            if diff:
                fd.seek(off)
                if read_exact(fd, n) != data:
                    fd.seek(off)
                    fd.write(data)
                    written += n
            else:
                fd.seek(off)
                fd.write(data)
                written += n
            off += n
            prog.update(off)
            throttle(started, off, mbps)
        fd.flush()
        os.fsync(fd.fileno())
    if os.path.exists(dst) and not os.path.isfile(dst):
        run(["blockdev", "--flushbufs", dst])
    return {"bytes": total, "written": written, "source_sha256": h.hexdigest(), "seconds": round(time.time() - started, 2)}


def run_hash(job, a, prog):
    dev, total = a["dev"], None
    n = dev_bytes(dev)
    total = int(a.get("bytes") or n)
    if os.path.exists(dev) and not os.path.isfile(dev):
        run(["blockdev", "--flushbufs", dev])
    h, off, started = hashlib.sha256(), 0, time.time()
    prog.st["total"] = total
    with open(dev, "rb", buffering=0) as f:
        while off < total:
            m = min(CHUNK, total - off)
            h.update(zero_padded(f, off, m, n))
            off += m
            prog.update(off)
            throttle(started, off, a.get("rate_mbps"))
    return {"bytes": total, "sha256": h.hexdigest(), "seconds": round(time.time() - started, 2)}


def run_compare(job, a, prog):
    pa, pb = a["a"], a["b"]
    na, nb = dev_bytes(pa), dev_bytes(pb)
    total = int(a.get("bytes") or max(na, nb))
    off, first, bad, started = 0, None, 0, time.time()
    prog.st["total"] = total
    with open(pa, "rb", buffering=0) as fa, open(pb, "rb", buffering=0) as fb:
        while off < total:
            m = min(CHUNK, total - off)
            if zero_padded(fa, off, m, na) != zero_padded(fb, off, m, nb):
                bad += 1
                if first is None:
                    first = off
            off += m
            prog.update(off)
            throttle(started, off, a.get("rate_mbps"))
    return {"bytes": total, "identical": bad == 0, "first_difference": first, "different_chunks": bad, "seconds": round(time.time() - started, 2)}


def v_run_job(job):
    d = job_dir(job)
    spec = read_json(os.path.join(d, "job.json"))
    prog = Progress(job, 0)
    prog.update(0, force=True)                          # shows the process id at once
    try:
        fn = {"copy": run_copy, "hash": run_hash, "compare": run_compare}[spec["verb"]]
        result = fn(job, spec["args"], prog)
        prog.st.update(state="done", result=result, done=prog.st["total"], updated=now(), error="")
    except Exception as exc:
        prog.st.update(state="failed", error=str(exc), updated=now())
    write_json(prog.path, prog.st)


# ---- the host itself -----------------------------------------------------------------------------------------------------------------
def v_probe(a):
    tools = {t: bool(which(t)) for t in TOOLS}
    out = {"version": VERSION, "python": sys.version.split()[0], "platform": sys.platform, "user": os.environ.get("USER") or os.environ.get("LOGNAME") or "",
           "euid": os.geteuid() if hasattr(os, "geteuid") else None, "home": HOME, "tools": tools, "time": now()}
    try:
        os.makedirs(HOME, exist_ok=True)
        st = os.statvfs(HOME) if hasattr(os, "statvfs") else None
        out["free_bytes"] = st.f_bavail * st.f_frsize if st else None
    except OSError as exc:
        out["home_error"] = str(exc)
    if tools["lvs"]:
        try:
            out["volume_groups"] = sorted({r["vg"] for r in lvs()})
        except MoverError as exc:
            out["lvm_error"] = str(exc)
    return out


def v_cleanup(a):
    """Remove finished job folders; with all=true remove the whole mover folder (nothing is left on the host)."""
    import shutil
    removed = 0
    if a.get("all"):
        if os.path.isdir(HOME):
            for jid in os.listdir(JOBS) if os.path.isdir(JOBS) else []:
                st = os.path.join(JOBS, jid, "status.json")
                if os.path.exists(st) and read_json(st).get("state") == "running" and pid_alive(read_json(st).get("pid")):
                    raise MoverError("job %s is still running; cancel it first" % jid)
            shutil.rmtree(HOME)
            removed = 1
        return {"removed_all": bool(removed)}
    if os.path.isdir(JOBS):
        for jid in os.listdir(JOBS):
            p = os.path.join(JOBS, jid, "status.json")
            if os.path.exists(p) and read_json(p).get("state") in ("done", "failed", "cancelled", "dead"):
                shutil.rmtree(os.path.join(JOBS, jid))
                removed += 1
    return {"removed_jobs": removed}


# ---- diagnostics (read only) --------------------------------------------------------------------------------------------------------
DIAG_COMMANDS = [
    ("uname", ["uname", "-a"]), ("os-release", ["cat", "/etc/os-release"]), ("uptime", ["uptime"]), ("df", ["df", "-h"]), ("free", ["free", "-m"]),
    ("ip", ["ip", "-brief", "addr"]), ("lsblk", ["lsblk", "-b"]), ("lvm-version", ["lvm", "version"]),
    ("vgs", ["vgs", "--units", "b"]), ("lvs", ["lvs", "-a", "-o", "+lv_tags,origin,devices,lv_path", "--units", "b"]),
    ("dmsetup-ls", ["dmsetup", "ls"]), ("iscsi-sessions", ["iscsiadm", "-m", "session"]), ("multipath", ["multipath", "-ll"]),
    ("processes", ["ps", "-eo", "pid,etime,user,args"]), ("dmesg", ["dmesg"]), ("who-am-i", ["id"])]


def v_diag(a):
    """Everything a developer needs to understand this host, read only. Output is cut, never filtered for meaning."""
    out = {}
    for name, cmd in DIAG_COMMANDS:
        if not which(cmd[0]):
            out[name] = {"missing": cmd[0]}
            continue
        try:
            rc, o, e = run(cmd, timeout=30)
        except MoverError as exc:
            out[name] = {"error": str(exc)}
            continue
        lines = o.splitlines()
        if name == "dmesg":
            lines = lines[-200:]
        if name == "processes":
            lines = [x for x in lines if "mover" in x or "openmove" in x or "lvcreate" in x or "dd " in x] or lines[:1]
        out[name] = {"rc": rc, "out": "\n".join(lines[-400:])[:60000], "err": e.strip()[:2000]}
    jobs = {}
    if os.path.isdir(JOBS):
        for jid in sorted(os.listdir(JOBS))[-60:]:
            d = os.path.join(JOBS, jid)
            item = {}
            try:
                item["status"] = read_json(os.path.join(d, "status.json"))
                item["job"] = read_json(os.path.join(d, "job.json"))
            except (OSError, ValueError) as exc:
                item["error"] = str(exc)
            lp = os.path.join(d, "log.txt")
            if os.path.exists(lp):
                with open(lp, "rb") as f:
                    item["log_tail"] = f.read()[-4000:].decode("utf-8", "replace")
            jobs[jid] = item
    files = {}
    if os.path.isdir(HOME):
        for fn in sorted(os.listdir(HOME)):
            p = os.path.join(HOME, fn)
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    files[fn] = {"bytes": os.path.getsize(p), "sha256": hashlib.sha256(f.read()).hexdigest()}
    return {"commands": out, "jobs": jobs, "mover_files": files, "probe": v_probe({}), "time": now()}


def v_bench(a):
    """Read up to `bytes` (default 64 MiB) from a device and report MiB/s: the copy throughput of this host (check P8). Writes nothing."""
    dev = a["dev"]
    check_readable(dev)
    want = min(int(a.get("bytes") or 64 * 1024 * 1024), dev_bytes(dev))
    started, done = time.time(), 0
    with open(dev, "rb", buffering=0) as f:
        while done < want:
            part = f.read(min(CHUNK, want - done))
            if not part:
                break
            done += len(part)
    secs = max(time.time() - started, 0.001)
    return {"bytes": done, "seconds": round(secs, 3), "mib_per_s": round(done / secs / 1048576.0, 1)}


def v_vg_free(a):
    vg = name_ok(a["vg"], "volume group")
    rc, out, err = run(["vgs", "--noheadings", "--units", "b", "--nosuffix", "-o", "vg_size,vg_free", vg])
    if rc != 0:
        raise MoverError("vgs failed: %s" % err.strip())
    size, free = out.split()[:2]
    return {"vg": vg, "bytes": int(float(size)), "free_bytes": int(float(free))}


# ---- arrays: closed lists of calls (docs/ARRAY_API.md) --------------------------------------------------------------------------
# The mover calls an array only through a short list per family. A call that is not on the list is refused, whatever the controller sends.
# The login for each array is a root-only file in the mover's own folder, written by `array.set`. It is never returned and never logged.
ARRAYS = os.path.join(HOME, "arrays")
ARRAY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,40}$")
ADDRESS_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,120}$")
USER_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,60}$")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
SF_READ = ("GetClusterInfo", "GetClusterVersionInfo", "GetClusterCapacity", "ListAccounts", "GetAccountByName", "ListVolumes", "ListSnapshots",
           "GetAsyncResult", "ListSyncJobs", "ListVolumeAccessGroups", "ListDeletedVolumes")
SF_WRITE = ("CloneVolume", "AddVolumesToVolumeAccessGroup", "RemoveVolumesFromVolumeAccessGroup", "DeleteVolume", "PurgeDeletedVolume")
SF_CLONE_KEYS = ("volumeID", "snapshotID", "name", "newAccountID", "access", "attributes")
STAGE_NAME = re.compile(r"^(stage|om)-[A-Za-z0-9][A-Za-z0-9._-]{0,58}$")
CINDER_NAME = re.compile(r"^volume-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-cinder$")
SNAP_SUFFIX = re.compile(r"^(snapshot-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|om-[A-Za-z0-9._-]{1,50})$")
SIZE_OK = re.compile(r"^[1-9][0-9]{0,9}[MGT]$")
IQN_OK = re.compile(r"^(iqn\.[0-9]{4}-[0-9]{2}\.[A-Za-z0-9.:_-]{1,200}|eui\.[0-9A-Fa-f]{16})$")
PORTAL_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,100}:[0-9]{1,5}$")
DEVICE_DIRS = ("/dev/disk/by-path/", "/dev/mapper/", "/dev/disk/by-id/")


def array_file(name, ext="json"):
    if not isinstance(name, str) or not ARRAY_NAME.match(name):
        raise MoverError("array name %r is not a safe name" % (name,))
    return os.path.join(ARRAYS, "%s.%s" % (name, ext))


def write_private(path, text):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def array_config(name):
    try:
        with open(array_file(name)) as f:
            return json.load(f)
    except (IOError, OSError):
        raise MoverError("no login is stored for the array %r on this host (array.set)" % name)
    except ValueError:
        raise MoverError("the stored login of the array %r cannot be read" % name)


def v_array_set(a):
    """Store the login of one array: root-only file, never returned. SolidFire: management address, user, password, the id of the migration
    account that owns the clones. FlashArray: management address, a user that logs in with a key, the name of the migration host's host object."""
    name, family, mgmt = a.get("name"), a.get("family"), a.get("mgmt")
    array_file(name)
    if family not in ("solidfire", "pure"):
        raise MoverError("family must be solidfire or pure")
    if not isinstance(mgmt, str) or not ADDRESS_OK.match(mgmt):
        raise MoverError("the management address %r is not valid" % (mgmt,))
    user = a.get("user") or ("pureuser" if family == "pure" else "")
    if not USER_OK.match(user or ""):
        raise MoverError("the user name is not valid")
    cfg = {"name": name, "family": family, "mgmt": mgmt, "user": user}
    if family == "solidfire":
        scheme = a.get("scheme") or "https"
        if scheme not in ("https", "http") or (scheme == "http" and mgmt.split(":")[0] not in LOOPBACK):
            raise MoverError("the scheme must be https (http only to this machine, for tests)")
        acct, pw = a.get("migration_account_id"), a.get("password")
        if not isinstance(acct, int) or isinstance(acct, bool) or acct <= 0:
            raise MoverError("migration_account_id must be the number of the account that owns the clones")
        if not isinstance(pw, str) or not pw:
            raise MoverError("a password is needed")
        cfg.update(scheme=scheme, password=pw, migration_account_id=acct, insecure=bool(a.get("insecure")), api_version=str(a.get("api_version") or "11.0"))
    else:
        host = a.get("host_object")
        if not isinstance(host, str) or not NAME_OK.match(host):
            raise MoverError("host_object must be the name of the migration host's host object on the array")
        ident = a.get("identity")
        if ident is not None and (not isinstance(ident, str) or not os.path.isfile(ident)):
            raise MoverError("the identity file %r does not exist on this host" % (ident,))
        cfg.update(host_object=host, identity=ident)
    write_private(array_file(name), json.dumps(cfg))
    return {"stored": True, "name": name, "family": family, "mgmt": mgmt, "insecure": bool(cfg.get("insecure"))}


def v_array_list(a):
    out = []
    if os.path.isdir(ARRAYS):
        for fn in sorted(os.listdir(ARRAYS)):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(ARRAYS, fn)) as f:
                        c = json.load(f)
                except (IOError, OSError, ValueError):
                    continue
                out.append({k: c.get(k) for k in ("name", "family", "mgmt", "user", "scheme", "insecure", "migration_account_id", "host_object")})
    return {"arrays": out}


def v_array_forget(a):
    gone = False
    for ext in ("json", "made"):
        try:
            os.remove(array_file(a.get("name"), ext))
            gone = gone or ext == "json"
        except OSError:
            pass
    return {"removed": gone}


# ---- SolidFire: the Element API --------------------------------------------------------------------------------------------------
def sf_post(cfg, method, params, version=None):
    import base64
    import ssl
    import urllib.error
    import urllib.request
    url = "%s://%s/json-rpc/%s" % (cfg.get("scheme", "https"), cfg["mgmt"], version or cfg.get("api_version") or "11.0")
    body = json.dumps({"method": method, "params": params, "id": 1}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(
        ("%s:%s" % (cfg["user"], cfg["password"])).encode()).decode()})
    ctx = ssl._create_unverified_context() if cfg.get("insecure") and url.startswith("https") else None
    try:
        with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise MoverError("Element %s: HTTP %s: %s" % (method, exc.code, exc.read().decode("utf-8", "replace")[:2000]))
    except Exception as exc:
        raise MoverError("Element %s: %s: %s" % (method, type(exc).__name__, exc))
    try:
        data = json.loads(raw)
    except ValueError:
        raise MoverError("Element %s gave an answer that is not JSON: %s" % (method, raw[:500]))
    if "error" in data:
        raise MoverError("Element %s error: %s" % (method, json.dumps(data["error"])))
    return data.get("result")


def sf_int(value, what):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise MoverError("%s must be a positive whole number" % what)
    return value


def sf_owned(cfg, volume_id):
    """True when the volume belongs to the migration account (only clones OpenMove made live there)."""
    res = sf_post(cfg, "ListVolumes", {"volumeIDs": [volume_id]}) or {}
    vols = res.get("volumes") or []
    if not vols:
        res = sf_post(cfg, "ListDeletedVolumes", {}) or {}
        vols = [v for v in res.get("volumes") or [] if v.get("volumeID") == volume_id]
    return bool(vols) and vols[0].get("accountID") == cfg["migration_account_id"]


def sf_check(cfg, method, p):
    if method in SF_READ:
        return
    if method not in SF_WRITE:
        raise MoverError("the Element method %s is not on the list of calls the mover makes (docs/ARRAY_API.md)" % method)
    if method == "CloneVolume":
        extra = sorted(set(p) - set(SF_CLONE_KEYS))
        if extra:
            raise MoverError("CloneVolume does not take %s here" % ", ".join(extra))
        sf_int(p.get("volumeID"), "volumeID")
        if "snapshotID" in p:
            sf_int(p["snapshotID"], "snapshotID")
        if p.get("newAccountID") != cfg["migration_account_id"]:
            raise MoverError("a clone must belong to the migration account %s" % cfg["migration_account_id"])
        if p.get("access") != "readOnly":
            raise MoverError("a clone is made read only")
        nm = p.get("name")
        if not isinstance(nm, str) or not NAME_OK.match(nm) or len(nm) > 64:
            raise MoverError("the clone name must be a safe name of at most 64 characters")
        return
    if method in ("AddVolumesToVolumeAccessGroup", "RemoveVolumesFromVolumeAccessGroup"):
        sf_int(p.get("volumeAccessGroupID"), "volumeAccessGroupID")
        vols = p.get("volumes")
        if not isinstance(vols, list) or not vols:
            raise MoverError("volumes must be a list of volume ids")
        for v in vols:
            if not sf_owned(cfg, sf_int(v, "a volume id")):
                raise MoverError("volume %s does not belong to the migration account: the mover does not change its access" % v)
        return
    vid = sf_int(p.get("volumeID"), "volumeID")                     # DeleteVolume, PurgeDeletedVolume
    if not sf_owned(cfg, vid):
        raise MoverError("volume %s does not belong to the migration account: the mover removes only the clones it made" % vid)


def v_sf_call(a):
    cfg = array_config(a.get("array"))
    if cfg.get("family") != "solidfire":
        raise MoverError("%r is not a SolidFire array" % a.get("array"))
    method, params = a.get("method"), a.get("params") or {}
    if not isinstance(method, str) or not isinstance(params, dict):
        raise MoverError("method must be a name and params an object")
    sf_check(cfg, method, params)
    return {"reply": sf_post(cfg, method, params, a.get("version")), "method": method}


# ---- FlashArray: the Purity CLI over SSH ----------------------------------------------------------------------------------------
def made_names(array):
    try:
        with open(array_file(array, "made")) as f:
            return set(x.strip() for x in f if x.strip())
    except (IOError, OSError):
        return set()


def save_made(array, names):
    write_private(array_file(array, "made"), "\n".join(sorted(names)) + ("\n" if names else ""))


def pure_check(cfg, argv):
    """Returns (kind, ledger change) for an allowed command, or raises. Write commands touch only volumes this mover made."""
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        raise MoverError("argv must be a list of strings")
    made, a = made_names(cfg["name"]), argv
    vname = lambda x: isinstance(x, str) and (STAGE_NAME.match(x) or CINDER_NAME.match(x))
    ours = lambda x: x in made
    if a[:2] == ["purearray", "list"] and a[2:] in ([], ["--space"]) or a == ["pureport", "list"]:
        return "read", None
    if a[:2] == ["purevol", "list"]:
        rest = a[2:]
        for flag in (["--snap"], ["--connect"], []):
            if rest[:len(flag)] == flag and rest[len(flag):len(flag) + 2] == ["--notitle", "--csv"] and len(rest) == len(flag) + 3:
                nm = rest[-1]
                if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$", nm):
                    raise MoverError("%r is not a volume name" % nm)
                return "read", None
        raise MoverError("this purevol list form is not on the list")
    if len(a) == 5 and a[:3] == ["purevol", "create", "--size"] and SIZE_OK.match(a[3]) and STAGE_NAME.match(a[4]):
        return "write", ("add", a[4])
    if len(a) == 5 and a[:3] == ["purevol", "setattr", "--size"] and SIZE_OK.match(a[3]) and ours(a[4]):
        return "write", None
    if len(a) == 5 and a[:3] == ["purevol", "snap", "--suffix"] and SNAP_SUFFIX.match(a[3]) and ours(a[4]):
        return "write", None
    if len(a) == 4 and a[:2] == ["purevol", "rename"] and ours(a[2]) and vname(a[3]):
        return "write", ("rename", a[2], a[3])
    if len(a) == 5 and a[:2] == ["purehost", "connect"] and a[2] == "--vol" and ours(a[3]) and a[4] == cfg["host_object"]:
        return "write", None
    if len(a) == 5 and a[:2] == ["purehost", "disconnect"] and a[2] == "--vol" and ours(a[3]) and a[4] == cfg["host_object"]:
        return "write", None
    if len(a) == 3 and a[:2] == ["purevol", "destroy"]:
        base, _, suffix = a[2].partition(".")
        if ours(a[2]):
            return "write", ("remove", a[2])
        if suffix and ours(base) and SNAP_SUFFIX.match(suffix):
            return "write", None
    raise MoverError("this Purity command is not on the list of commands the mover runs, or it names an object this mover did not make (docs/ARRAY_API.md)")


def v_pure_cli(a):
    cfg = array_config(a.get("array"))
    if cfg.get("family") != "pure":
        raise MoverError("%r is not a FlashArray" % a.get("array"))
    argv = a.get("argv")
    kind, change = pure_check(cfg, argv)
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=yes"]
    if cfg.get("identity"):
        cmd += ["-i", cfg["identity"]]
    cmd += ["%s@%s" % (cfg["user"], cfg["mgmt"])] + list(argv)
    rc, out, err = run(cmd, timeout=int(a.get("timeout") or 120))
    if rc == 0 and change:
        made = made_names(cfg["name"])
        if change[0] == "add":
            made.add(change[1])
        elif change[0] == "rename":
            made.discard(change[1])
            made.add(change[2])
        elif change[0] == "remove":
            made.discard(change[1])
        save_made(cfg["name"], made)
    return {"rc": rc, "stdout": out, "stderr": err, "kind": kind}


# ---- iSCSI sessions and devices the arrays present ---------------------------------------------------------------------------------
def sessions_file():
    return os.path.join(ARRAYS, "iscsi-sessions.made")


def v_iscsi_login(a):
    portal, iqn = a.get("portal"), a.get("iqn")
    if not isinstance(portal, str) or not PORTAL_OK.match(portal) or not isinstance(iqn, str) or not IQN_OK.match(iqn):
        raise MoverError("portal must be address:port and iqn a valid target name")
    rc, out, err = run(["iscsiadm", "-m", "session"])
    if rc == 0 and any((" %s," % portal) in line and (" %s " % iqn) in line + " " for line in out.splitlines()):
        return {"existed": True}
    rc, out, err = run(["iscsiadm", "-m", "discovery", "-t", "sendtargets", "-p", portal])
    if rc != 0:
        raise MoverError("iscsiadm discovery on %s failed (rc %s): %s" % (portal, rc, (err or out).strip()))
    rc, out, err = run(["iscsiadm", "-m", "node", "-T", iqn, "-p", portal, "-l"])
    if rc != 0 and "already present" not in (err + out):
        raise MoverError("iscsiadm login to %s at %s failed (rc %s): %s" % (iqn, portal, rc, (err or out).strip()))
    keep = set()
    try:
        with open(sessions_file()) as f:
            keep = set(x.strip() for x in f if x.strip())
    except (IOError, OSError):
        pass
    keep.add("%s %s" % (portal, iqn))
    write_private(sessions_file(), "\n".join(sorted(keep)) + "\n")
    return {"existed": False}


def v_iscsi_logout(a):
    """Only sessions this mover opened are closed: never the sessions Cinder's own volume service uses."""
    portal, iqn = a.get("portal"), a.get("iqn")
    if not isinstance(portal, str) or not PORTAL_OK.match(portal) or not isinstance(iqn, str) or not IQN_OK.match(iqn):
        raise MoverError("portal must be address:port and iqn a valid target name")
    keep = set()
    try:
        with open(sessions_file()) as f:
            keep = set(x.strip() for x in f if x.strip())
    except (IOError, OSError):
        pass
    key = "%s %s" % (portal, iqn)
    if key not in keep:
        raise MoverError("the mover did not log in to %s at %s: it does not log out of it" % (iqn, portal))
    rc, out, err = run(["iscsiadm", "-m", "node", "-T", iqn, "-p", portal, "-u"])
    if rc != 0 and "No matching sessions" not in (err + out) and "not logged in" not in (err + out).lower():
        raise MoverError("iscsiadm logout from %s at %s failed (rc %s): %s" % (iqn, portal, rc, (err or out).strip()))
    keep.discard(key)
    write_private(sessions_file(), "\n".join(sorted(keep)) + ("\n" if keep else ""))
    return {"logged_out": True}


def v_dev_wait(a):
    """Rescan and wait until a device path exists; report its size and, when asked, check it (a path under /dev/disk or /dev/mapper only)."""
    path = a.get("path")
    if not isinstance(path, str) or ".." in path or not any(path.startswith(d) for d in DEVICE_DIRS):
        raise MoverError("%r is not a device path under /dev/disk or /dev/mapper" % (path,))
    end = time.time() + min(int(a.get("timeout") or 60), 600)
    while True:
        if os.path.exists(path):
            rc, out, err = run(["blockdev", "--getsize64", path])
            if rc == 0 and out.strip().isdigit():
                size = int(out.strip())
                want = a.get("bytes")
                if want is not None and int(want) != size:
                    raise MoverError("%s has %d bytes, not the %d expected" % (path, size, int(want)))
                return {"path": path, "bytes": size}
        if time.time() >= end:
            raise MoverError("the device %s did not appear in time" % path)
        if which("iscsiadm"):
            run(["iscsiadm", "-m", "session", "--rescan"])
        if which("multipath"):
            run(["multipath", "-r"])
        time.sleep(2)


VERBS = {"probe": v_probe, "lv.list": v_lv_list, "lv.create": v_lv_create, "lv.snapshot": v_lv_snapshot, "lv.rename": v_lv_rename, "lv.extend": v_lv_extend,
         "array.set": v_array_set, "array.list": v_array_list, "array.forget": v_array_forget, "sf.call": v_sf_call, "pure.cli": v_pure_cli,
         "iscsi.login": v_iscsi_login, "iscsi.logout": v_iscsi_logout, "dev.wait": v_dev_wait,
         "lv.remove": v_lv_remove, "lv.remove_source_snapshots": v_lv_remove_source_snapshots, "job.start": v_job_start,
         "job.status": v_job_status, "job.cancel": v_job_cancel, "cleanup": v_cleanup, "diag": v_diag, "bench": v_bench, "vg.free": v_vg_free}


def main(argv):
    if len(argv) < 2:
        print(json.dumps({"ok": False, "error": "no verb given"}))
        return 2
    verb = argv[1]
    if verb == "run-job":
        v_run_job(argv[2])
        return 0
    try:
        os.makedirs(JOBS, exist_ok=True)
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        args = json.loads(raw) if raw.strip() else {}
        if verb not in VERBS:
            raise MoverError("unknown verb %r" % verb)
        res = VERBS[verb](args)
        res["ok"] = True
    except MoverError as exc:
        res = {"ok": False, "error": str(exc)}
    except Exception as exc:
        res = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc), "trace": traceback.format_exc()[-600:]}
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
