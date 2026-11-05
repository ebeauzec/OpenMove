#!/usr/bin/env python3
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Reads what a migration host is and what it sees, and prints one line  OPENMOVE-JSON {...}  for OpenMove. Changes nothing.

OpenMove sends it and runs it as root through openmove-hostctl (Helper servers tab, "Read the host"). It can also be run by hand:
    python3 inspect_host.py [--mgmt-peer IP]
What it reports: the system, the iSCSI initiator name, the Fibre Channel ports (WWPN, state, speed) and the target ports they see, the network
interfaces (address, MTU, speed, which one carries the management connection), the iSCSI sessions (portal, target, interface, state), the multipath
devices and their paths, the tools and services, the clock, the disk space and the firewall. Standard library only; works from Python 3.6.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys


def run(argv, timeout=20):
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout)
        return p.returncode, p.stdout
    except Exception:
        return 127, ""


def read(path):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


def hexname(path):
    return read(path).replace("0x", "").lower()


def have(cmd):
    return shutil.which(cmd) is not None


def system():
    osname = ""
    for ln in read("/etc/os-release").splitlines():
        if ln.startswith("PRETTY_NAME="):
            osname = ln.split("=", 1)[1].strip('"')
    up = read("/proc/uptime").split()
    return {"hostname": read("/proc/sys/kernel/hostname"), "os": osname, "kernel": os.uname().release, "python": sys.version.split()[0],
            "uptime_s": int(float(up[0])) if up else 0, "cpus": os.cpu_count() or 0,
            "memory_mib": next((int(ln.split()[1]) // 1024 for ln in read("/proc/meminfo").splitlines() if ln.startswith("MemTotal:")), 0)}


def management_nic(peer):
    if peer and have("ip"):
        rc, out = run(["ip", "-o", "route", "get", peer])
        m = re.search(r" dev (\S+)", out)
        if rc == 0 and m:
            return m.group(1)
    rc, out = run(["ip", "-o", "route", "show", "default"])
    m = re.search(r" dev (\S+)", out)
    return m.group(1) if m else ""


def nics(mgmt):
    out = []
    addrs = {}
    rc, text = run(["ip", "-o", "-4", "addr", "show"])
    for ln in text.splitlines():
        m = re.match(r"\d+:\s+(\S+)\s+inet\s+(\S+)", ln)
        if m:
            addrs.setdefault(m.group(1), []).append(m.group(2))
    rc, text = run(["ip", "-o", "-6", "addr", "show", "scope", "global"])
    for ln in text.splitlines():
        m = re.match(r"\d+:\s+(\S+)\s+inet6\s+(\S+)", ln)
        if m:
            addrs.setdefault(m.group(1), []).append(m.group(2))
    for path in sorted(glob.glob("/sys/class/net/*")):
        name = os.path.basename(path)
        if name == "lo":
            continue
        kind = "bond" if os.path.isdir(path + "/bonding") else "vlan" if os.path.exists("/proc/net/vlan/" + name) else \
            "virtual" if os.path.islink(path) and "/virtual/" in os.path.realpath(path) else "physical"
        speed = read(path + "/speed")
        out.append({"name": name, "mac": read(path + "/address"), "state": read(path + "/operstate"), "mtu": int(read(path + "/mtu") or 0),
                    "speed_mbps": int(speed) if speed.lstrip("-").isdigit() and int(speed) > 0 else None, "kind": kind,
                    "addresses": addrs.get(name, []), "management": name == mgmt, "driver": os.path.basename(os.path.realpath(path + "/device/driver")) if os.path.exists(path + "/device/driver") else ""})
    return out


def fc():
    ports, targets = [], []
    for h in sorted(glob.glob("/sys/class/fc_host/host*")):
        ports.append({"host": os.path.basename(h), "wwpn": hexname(h + "/port_name"), "wwnn": hexname(h + "/node_name"), "state": read(h + "/port_state"),
                      "speed": read(h + "/speed"), "type": read(h + "/port_type"), "fabric": hexname(h + "/fabric_name"), "model": read("/sys/class/scsi_host/%s/modelname" % os.path.basename(h))})
    for r in sorted(glob.glob("/sys/class/fc_remote_ports/rport-*")):
        m = re.match(r"rport-(\d+):", os.path.basename(r))
        targets.append({"host": "host" + m.group(1) if m else "", "wwpn": hexname(r + "/port_name"), "wwnn": hexname(r + "/node_name"), "state": read(r + "/port_state"),
                        "roles": read(r + "/roles")})
    return ports, targets


def iscsi():
    info = {"initiator": "", "sessions": [], "installed": have("iscsiadm")}
    for ln in read("/etc/iscsi/initiatorname.iscsi").splitlines():
        if ln.startswith("InitiatorName="):
            info["initiator"] = ln.split("=", 1)[1].strip()
    if not info["installed"]:
        return info
    rc, text = run(["iscsiadm", "-m", "session", "-P", "1"])
    cur = None
    for ln in text.splitlines():
        t = ln.strip()
        if t.startswith("Target:"):
            cur = {"target": t.split()[1], "portal": "", "iface": "", "state": ""}
            info["sessions"].append(cur)
        elif cur is not None and t.startswith("Current Portal:"):
            cur["portal"] = t.split(":", 1)[1].strip().split(",")[0]
        elif cur is not None and t.startswith("Iface Name:"):
            cur["iface"] = t.split(":", 1)[1].strip()
        elif cur is not None and t.startswith("iSCSI Session State:"):
            cur["state"] = t.split(":", 1)[1].strip()
    conf = {}
    for ln in read("/etc/iscsi/iscsid.conf").splitlines():
        m = re.match(r"^\s*(node\.[^=\s]+)\s*=\s*(.*)$", ln)
        if m and "password" not in m.group(1):
            conf[m.group(1)] = m.group(2).strip()
    info["settings"] = conf
    return info


def multipath():
    maps = []
    if not have("multipath"):
        return {"installed": False, "maps": maps}
    rc, text = run(["multipath", "-ll"], timeout=30)
    cur = None
    for ln in text.splitlines():
        m = re.match(r"^(\S+)\s+\((\S+)\)\s+(dm-\d+)\s+(.*)$", ln) or re.match(r"^(\S+)\s+(dm-\d+)\s+(.*)$", ln)
        if m and not ln.startswith((" ", "|", "`")):
            g = m.groups()
            cur = {"name": g[0], "wwid": g[1] if len(g) == 4 else g[0], "dm": g[-3] if len(g) == 4 else g[1], "product": g[-1], "size": "", "paths": []}
            maps.append(cur)
            continue
        if cur is None:
            continue
        s = re.search(r"size=(\S+)", ln)
        if s and not cur["size"]:
            cur["size"] = s.group(1)
        p = re.search(r"(\d+:\d+:\d+:\d+)\s+(\S+)\s+\S+\s+(\S+)\s+(\S+)", ln)
        if p:
            cur["paths"].append({"hctl": p.group(1), "dev": p.group(2), "dm_state": p.group(3), "state": p.group(4)})
    return {"installed": True, "maps": maps, "drop_in": os.path.exists("/etc/multipath/conf.d/openmove.conf")}


def active(unit):
    rc, out = run(["systemctl", "is-active", unit])
    return out.strip() or "unknown"


def main():
    peer = ""
    if "--mgmt-peer" in sys.argv:
        peer = sys.argv[sys.argv.index("--mgmt-peer") + 1]
    mgmt = management_nic(peer)
    ports, targets = fc()
    tl = {c: have(c) for c in ("iscsiadm", "multipath", "multipathd", "lsblk", "sg_inq", "rescan-scsi-bus.sh")}
    rc, ntp = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    fw = "none"
    if have("ufw"):
        rc, out = run(["ufw", "status"])
        fw = "ufw " + ("active" if "active" in out.lower().split("\n")[0] and "inactive" not in out.lower().split("\n")[0] else "inactive")
    elif have("firewall-cmd"):
        rc, out = run(["firewall-cmd", "--state"])
        fw = "firewalld " + out.strip()
    free = {}
    for label, path in (("tmp", "/tmp"), ("var", "/var")):
        try:
            free[label] = shutil.disk_usage(path).free // (1024 ** 3)
        except Exception:
            pass
    info = {"system": system(), "management_nic": mgmt, "nics": nics(mgmt), "fc_ports": ports, "fc_targets": targets, "iscsi": iscsi(), "multipath": multipath(),
            "tools": tl, "services": {u: active(u) for u in ("iscsid", "multipathd")} if have("systemctl") else {}, "clock_synchronised": ntp.strip() == "yes",
            "free_gib": free, "firewall": fw, "network_unit": os.path.exists("/etc/openmove/network.conf"),
            "hostctl": os.path.exists("/usr/local/sbin/openmove-hostctl"),
            "scripts": {os.path.basename(f)[:-7]: read(f)[:12] for f in sorted(glob.glob("/usr/local/lib/openmove/*.sha256"))}}
    print("OPENMOVE-JSON " + json.dumps(info, sort_keys=True))


if __name__ == "__main__":
    main()
