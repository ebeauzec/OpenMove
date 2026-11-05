# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The lab's hosts, set up without a terminal (docs/LAB.md, docs/MOVER.md).

Class C needs a migration host and a Cinder host. In the lab both are WSL 2 distros on this machine (on a Linux host the same two machines exist as the lab
virtual machine and a separate root file system inside it: docs/LAB.md, "The lab on a Linux host"):

  lab-cinder  the DevStack distro itself (it runs cinder-volume, so the Cinder helper runs there, with DevStack's own Python)  sshd on 2201
  lab-mover   a second, separate distro that holds only the data mover: the demonstration of a migration host as its own instance   sshd on 2202

WSL 2 distros share one kernel, so the second distro sees the same logical volumes as the first; that is what a migration host needs to see
(both back-ends' storage). `prepare` does what an administrator would do by hand, in the order a real site would do it: create the second
distro if it is missing, install what the data mover needs, start sshd with a key login for OpenMove's own key, register both hosts under the
address the distros have now (it changes at every restart), pin their host keys (the lab is this machine, so the fingerprint is read from the
distro itself instead of being compared by a person; the journal says so) and run the readiness checks.

Lab only. Nothing here is used with a customer cloud, where a person registers the hosts, compares the fingerprints and adds the key.
"""
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from .. import hosts as hosts_mod
from ..connectors import ssh
from . import LabError, backend, lab_dir, lab_key, linux_target, load_config

MOVER_DISTRO = "openmove-mover"
FRESH_DISTRO = "openmove-fresh"
FRESH_PORT = 2203
CINDER_PYTHON = "/opt/stack/data/venv/bin/python"
HOSTS = {"lab-cinder": {"port": 2201, "roles": ["cinder"]}, "lab-mover": {"port": 2202, "roles": ["mover"]}}

SETUP = r"""#!/bin/bash
# Lab only: sshd on PORT in this distro, key login for root, no passwords; the packages the data mover needs.
set -e
PORT="$1"; KEY="$2"
export DEBIAN_FRONTEND=noninteractive
need=""
command -v sshd >/dev/null 2>&1 || need="$need openssh-server"
command -v python3 >/dev/null 2>&1 || need="$need python3"
command -v lvs >/dev/null 2>&1 || need="$need lvm2"
if [ -n "$need" ]; then
  apt-get update -qq >/dev/null 2>&1 || true
  apt-get install -y -qq $need >/dev/null 2>&1 || { echo "could not install:$need"; exit 1; }
fi
mkdir -p /etc/ssh/sshd_config.d /root/.ssh /run/sshd
cat > /etc/ssh/sshd_config.d/10-openmove.conf <<EOF
Port $PORT
ListenAddress 0.0.0.0
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
EOF
chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys
grep -qF "$KEY" /root/.ssh/authorized_keys || echo "$KEY" >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
ssh-keygen -A >/dev/null 2>&1
if [ -d /run/systemd/system ]; then
  systemctl disable --now ssh.socket >/dev/null 2>&1 || true
  systemctl enable ssh >/dev/null 2>&1 || true
  systemctl restart ssh
else
  pkill -x sshd >/dev/null 2>&1 || true
  /usr/sbin/sshd
fi
sleep 1
ss -ltn | grep -q ":$PORT " || { echo "sshd is not listening on $PORT"; exit 1; }
vgscan --mknodes >/dev/null 2>&1 || true
echo "sshd ready on $PORT"
"""


LINUX_KEY = r"""#!/bin/bash
# Lab only: OpenMove's key may log in to root of the lab machine (its sshd already listens on the port; labvm.sh made it so).
set -e
PORT="$1"; KEY="$2"
mkdir -p /root/.ssh; chmod 700 /root/.ssh; touch /root/.ssh/authorized_keys
grep -qF "$KEY" /root/.ssh/authorized_keys || echo "$KEY" >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
ss -ltn | grep -q ":$PORT " || { echo "sshd is not listening on $PORT"; exit 1; }
vgscan --mknodes >/dev/null 2>&1 || true
echo "sshd ready on $PORT"
"""


FRESH = r"""#!/bin/bash
# Lab only: a bare host as a customer would hand it over for a host setup: sshd on PORT that accepts the root password, and nothing of OpenMove's.
set -e
PASSWORD="$1"; PORT="$2"
export DEBIAN_FRONTEND=noninteractive
command -v sshd >/dev/null 2>&1 || { apt-get update -qq >/dev/null 2>&1 || true; apt-get install -y -qq openssh-server >/dev/null 2>&1 || { echo "could not install openssh-server"; exit 1; }; }
echo "root:$PASSWORD" | chpasswd
mkdir -p /etc/ssh/sshd_config.d /run/sshd
printf 'Port %s\nPermitRootLogin yes\nPasswordAuthentication yes\n' "$PORT" > /etc/ssh/sshd_config.d/10-fresh.conf
ssh-keygen -A >/dev/null 2>&1
pkill -x sshd >/dev/null 2>&1 || true
sleep 1
/usr/sbin/sshd
sleep 1
ss -ltn | grep -q ":$PORT " || { echo "sshd is not listening on $PORT"; exit 1; }
echo "bare host ready on $PORT"
"""


def wsl_exe():
    return shutil.which("wsl") or "wsl.exe"


def neutral_dir():
    """A folder wsl.exe can translate; it complains about some synced and virtual folders as a working directory."""
    return os.environ.get("SystemRoot") or None


def local(argv, stdin=None, timeout=60):
    return ssh.run_local(argv, stdin, timeout, cwd=neutral_dir())


MOVER_MARK = "mover.made"
LINUX_DIR = Path(__file__).resolve().parents[2] / "tools" / "lab" / "linux"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=15"]
# the migration host on a Linux host is a root file system inside the lab machine: its commands are run in its mount namespace and root
MOVER_ENTER = 'exec nsenter -t "$(systemctl show -p MainPID --value openmove-mover)" -m -r -- bash -s -- "$@"'


def labvm(*args, check=True):
    """Run tools/lab/linux/labvm.sh (the host side of the lab on a Linux host); it asks for sudo itself when it needs root. Output goes to the terminal."""
    env = dict(os.environ, OPENMOVE_LAB_DIR=str(lab_dir()))
    try:
        env["LAB_PASSWORD"] = load_config()["password"]
    except LabError:
        pass
    rc = subprocess.run(["bash", str(LINUX_DIR / "labvm.sh")] + list(args), env=env).returncode
    if check and rc != 0:
        raise LabError("labvm.sh %s failed (exit code %d)" % (" ".join(args), rc))
    return rc


def linux_run(machine, script, args=(), timeout=600):
    ip, port = linux_target(machine)
    if machine == "openmove-mover":
        ip, port = linux_target("openmove-lab")
        remote = ["bash", "-c", MOVER_ENTER, "x"] + list(args)
    else:
        remote = ["bash", "-s", "--"] + list(args)
    argv = [shutil.which("ssh") or "ssh", "-i", str(lab_key()), "-p", str(port)] + SSH_OPTS + ["root@" + ip] + [shlex.quote(a) for a in remote]
    rc, out, err = local(argv, script.encode("utf-8"), timeout)
    return rc, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def run(distro, script, args=(), timeout=600):
    """Run a bash script as root in a distro (script on standard input). Returns (rc, out, err) as text."""
    if backend() == "linux":
        return linux_run(distro, script, args, timeout)
    rc, out, err = local([wsl_exe(), "-d", distro, "-u", "root", "--", "bash", "-s", "--"] + list(args), script.encode("utf-8"), timeout)
    return rc, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def distros():
    if backend() == "linux":
        vm = lab_dir() / "vm"
        found = {"openmove-" + n for n in ("lab", "fresh", "vmhost") if (vm / n / "disk.img").exists()}
        if (vm / "lab" / MOVER_MARK).exists():       # the migration host lives inside the lab machine's disk; the mark goes with it when that is destroyed
            found.add("openmove-mover")
        return found
    rc, out, err = local([wsl_exe(), "-l", "-q"], None, 30)
    return {x.strip() for x in out.decode("utf-16-le", "ignore").replace("\x00", "").splitlines() if x.strip()}


def keep_running(distro):
    """WSL stops a distro when no session is open; one detached sleep keeps it up until the lab is stopped. On a Linux host: start the virtual machine if it is stopped."""
    if backend() == "linux":
        machine = "lab" if distro in ("openmove-lab", "openmove-mover") else distro.replace("openmove-", "")
        if labvm("status", machine, check=False) == 0:
            return False
        labvm("start", machine)
        labvm("wait", machine, "300")
        return True
    rc, out, err = run(distro, "pgrep -f 'sleep infinity' >/dev/null && echo yes || echo no", timeout=60)
    if out.strip() == "yes":
        return False
    flags = (0x08000000 | 0x00000008) if os.name == "nt" else 0               # no window, detached
    subprocess.Popen([wsl_exe(), "-d", distro, "-u", "root", "--", "sleep", "infinity"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=flags, cwd=neutral_dir())
    time.sleep(2)
    return True


def address(distro):
    if backend() == "linux":
        return linux_target(distro)[0]
    rc, out, err = run(distro, "hostname -I | awk '{print $1}'", timeout=60)
    ip = out.strip()
    if rc != 0 or not ip:
        raise LabError("could not read the address of the distro %s: %s" % (distro, (err or out).strip()))
    return ip


def ensure_distro(name, subfolder):
    """A distro made from the same image as the lab's, when it is missing. Returns true when it was made.
    On a Linux host: the bare machines are built (labvm.sh) and the migration host is made inside the lab machine (mover_chroot.sh)."""
    if name in distros():
        return False
    if backend() == "linux":
        if name == "openmove-mover":
            keep_running("openmove-lab")
            rc, out, err = linux_run("openmove-lab", (LINUX_DIR / "mover_chroot.sh").read_text(encoding="utf-8").replace("\r\n", "\n"), ["create"], timeout=1800)
            if rc != 0:
                raise LabError("could not make the migration host: %s" % (out + err).strip()[-800:])
            (lab_dir() / "vm" / "lab" / MOVER_MARK).write_text("made by OpenMove\n", encoding="utf-8")
            return True
        if name in ("openmove-fresh", "openmove-vmhost"):
            labvm("build", name.replace("openmove-", ""))
            return True
        raise LabError("%s is not a lab machine" % name)
    folder = lab_dir()
    image = next(iter(sorted(folder.glob("ubuntu-*.wsl"))), None)
    if image is None:
        raise LabError("the distro %s is missing and the image to make it from is not in %s" % (name, folder))
    target = folder / subfolder
    target.mkdir(parents=True, exist_ok=True)
    rc, out, err = local([wsl_exe(), "--import", name, str(target), str(image), "--version", "2"], None, 900)
    if rc != 0:
        raise LabError("could not make the distro %s: %s" % (name, (err or out).decode("utf-16-le", "ignore").replace("\x00", "").strip()))
    run(name, "printf '[boot]\\nsystemd=true\\n' > /etc/wsl.conf", timeout=60)
    local([wsl_exe(), "--terminate", name], None, 60)
    return True


def ensure_mover_distro(cfg):
    """The second distro: the migration host that holds only the data mover."""
    return ensure_distro(MOVER_DISTRO, "mover")


def ensure_fresh_host():
    """A third, bare distro for trying the host setup from the Hosts tab: only sshd, with the root password login a customer's new host would have.
    Returns {address, port, user, made}. The password is the lab's own (lab-secret.json) and is not printed."""
    cfg = load_config()
    made = ensure_distro(FRESH_DISTRO, "fresh")
    keep_running(FRESH_DISTRO)
    if backend() == "linux":                         # the machine was built bare: sshd on 22 with the root password login
        return {"address": address(FRESH_DISTRO), "port": linux_target(FRESH_DISTRO)[1], "user": "root", "made": made}
    rc, out, err = run(FRESH_DISTRO, FRESH, [cfg["password"], str(FRESH_PORT)], timeout=900)
    if rc != 0:
        raise LabError("could not prepare the bare host %s: %s" % (FRESH_DISTRO, (out + err).strip()))
    return {"address": address(FRESH_DISTRO), "port": FRESH_PORT, "user": "root", "made": made}


def prepare(state, journal_note=True):
    """Make the lab's two hosts ready and registered. Returns what was done and the readiness result; raises LabError when it cannot."""
    cfg = load_config()
    lab_distro = cfg.get("wsl_distro") or "openmove-lab"
    home = state.home
    done = []
    if ensure_mover_distro(cfg):
        done.append("made the distro %s from the lab's image" % MOVER_DISTRO)
    for distro in (lab_distro, MOVER_DISTRO):
        if keep_running(distro):
            done.append(("started %s" if backend() == "linux" else "kept %s running") % distro)
    ssh.ensure_key(home)
    pub = ssh.public_key(home)
    ports = {"lab-cinder": (lab_distro, HOSTS["lab-cinder"]["port"]), "lab-mover": (MOVER_DISTRO, HOSTS["lab-mover"]["port"])}
    for name, (distro, port) in ports.items():
        if backend() == "linux":
            if name == "lab-mover":
                rc, out, err = run(lab_distro, (LINUX_DIR / "mover_chroot.sh").read_text(encoding="utf-8").replace("\r\n", "\n"), ["key", pub], timeout=300)
            else:
                rc, out, err = run(lab_distro, LINUX_KEY, [str(port), pub], timeout=300)
        else:
            rc, out, err = run(distro, SETUP, [str(port), pub], timeout=900)
        if rc != 0:
            raise LabError("could not prepare %s (%s): %s" % (name, distro, (out + err).strip()))
        done.append("%s: %s" % (name, out.strip().splitlines()[-1] if out.strip() else "ready"))
    ip = address(lab_distro)
    for name, (distro, port) in ports.items():
        spec = {"name": name, "roles": HOSTS[name]["roles"], "address": ip, "port": port, "user": "root", "notes": "OpenMove lab: " + ("a virtual machine on this host" if backend() == "linux" else "a WSL 2 distro on this machine")}
        if name == "lab-cinder":
            spec["python"] = CINDER_PYTHON
            spec["log_paths"] = ["/var/log/cinder/cinder-volume.log"]
        hosts_mod.upsert(home, spec)
        if not ssh.is_pinned(home, ip, port):
            keys = ssh.scan_host_key(ip, port)
            if not keys:
                raise LabError("%s offered no host key at %s:%d" % (name, ip, port))
            ssh.pin_host_key(home, keys[0]["line"])
            state.journal.record("host_trusted", name, {"by": "OpenMove lab (automatic)", "fingerprint": keys[0]["fingerprint"], "type": keys[0]["type"],
                                                         "note": "the lab is this machine; no person compared the fingerprint"})
    from ..server_hosts import refresh_fabric
    if state.platform:
        result = refresh_fabric(state, "sf-sim", "pure-sim")
    else:
        result = {"ready": None, "why": "the readiness is checked at the pre-flight, when the platform is known", "checks": []}
    if journal_note:
        state.journal.record("lab", "hosts", {"done": done, "address": ip, "ready": result["ready"], "why": result["why"]})
    return {"done": done, "address": ip, "ready": result["ready"], "why": result["why"], "checks": result["checks"]}
