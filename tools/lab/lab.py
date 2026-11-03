# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Control of the OpenMove lab: a disposable WSL distro that runs a real single-node OpenStack (DevStack).

    python tools/lab/lab.py status | up | down | seed | more | fresh-host | vm-up | vm-down | clean | list | shell-help

Windows with WSL 2 is assumed (the lab is a lab for this machine, not part of the shipped program). Setting the lab up the first time
(downloading the Ubuntu image, importing the distro, running DevStack, about 40 minutes) is described in tools/lab/README.md; the commands
here run it every day after that. Standard library only.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from openmove import lab  # noqa: E402

UP_SCRIPT = Path(__file__).with_name("lab_up.sh")
DOWN_SCRIPT = Path(__file__).with_name("lab_down.sh")


def wsl(*args, root=True, check=False, capture=True):
    cmd = ["wsl", "-d", lab.DISTRO] + (["-u", "root"] if root else []) + ["--"] + list(args)
    return subprocess.run(cmd, capture_output=capture, text=True, check=check, errors="replace", cwd=NEUTRAL_DIR)


# WSL cannot translate a cloud-drive folder (G:\My Drive) as its working directory and says so on every call; a neutral folder avoids that
NEUTRAL_DIR = os.environ.get("SystemRoot", "C:\\") if sys.platform == "win32" else None


def distro_exists():
    out = subprocess.run(["wsl", "-l", "-q"], capture_output=True, cwd=NEUTRAL_DIR).stdout.decode("utf-16-le", "ignore").replace("\x00", "")
    return lab.DISTRO in [l.strip() for l in out.splitlines()]


def keepalive():
    """WSL stops a distro when no session is open. One detached `sleep` keeps the lab running until `down`."""
    flags = 0x08000000 | 0x00000200          # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP: no console window of its own
    subprocess.Popen(["wsl", "-d", lab.DISTRO, "-u", "root", "--", "sleep", "infinity"], creationflags=flags if sys.platform == "win32" else 0,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=NEUTRAL_DIR)


def status():
    cfg = lab.load_config()
    up = lab.reachable(cfg["auth_url"])
    print("distro %s: %s" % (lab.DISTRO, "present" if distro_exists() else "MISSING (see tools/lab/README.md)"))
    print("keystone at %s: %s" % (cfg["auth_url"], "answers" if up else "does not answer (run: lab.py up)"))
    return 0 if up else 1


def up():
    if not distro_exists():
        print("The lab distro %s does not exist. Follow tools/lab/README.md to set it up." % lab.DISTRO)
        return 2
    keepalive()
    time.sleep(2)
    # the script is copied next to the lab files, because the distro reaches C: but not always the repository folder (a cloud drive)
    target = lab.lab_dir() / "lab_up.sh"
    target.write_bytes(UP_SCRIPT.read_bytes().replace(bytes([13, 10]), bytes([10])))
    r = wsl("bash", "/mnt/c/%s" % str(target)[3:].replace("\\", "/") if sys.platform == "win32" else str(target))
    print((r.stdout or "") + (r.stderr or ""))
    cfg = lab.load_config()
    for _ in range(60):
        if lab.reachable(cfg["auth_url"]):
            print("the lab cloud answers at %s" % cfg["auth_url"])
            return ensure_guests()
        time.sleep(5)
    print("the lab cloud did not answer in 5 minutes; look at the services:  wsl -d %s -u root -- systemctl list-units 'devstack*'" % lab.DISTRO)
    return 1


def ensure_guests():
    """After a restart: wait for the compute service, then start the lab guests that are meant to run (every lab-srv-* except the stopped, paused and suspended ones that the catalogue keeps in that state on purpose).
    Nova keeps saying ACTIVE for a guest that is not running while the compute service is down, so the power state is read, not the status."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import seed
    try:
        c = seed.Cloud(lab.load_config())
        for _ in range(60):
            rows = [s for s in c.call("GET", "compute", "/os-services?binary=nova-compute")["services"]]
            if rows and all(r["state"] == "up" and r["status"] == "enabled" for r in rows):
                break
            time.sleep(5)
        else:
            print("the compute service is not up after 5 minutes: the lab guests were not started")
            return 1
        print("the compute service is up")
        servers = c.call("GET", "compute", "/servers/detail?all_tenants=1")["servers"]
        want = [s for s in servers if s["name"].startswith("lab-srv-") and not s["name"].startswith(("lab-srv-off-", "lab-srv-paused-", "lab-srv-susp-"))]
        todo = [s for s in want if s.get("OS-EXT-STS:power_state") != 1 or s["status"] != "ACTIVE"]
        for s in todo:
            act = {"os-start": None} if s["status"] == "SHUTOFF" else {"reboot": {"type": "HARD"}}
            c.call("POST", "compute", "/servers/%s/action" % s["id"], act, soft=True)
        for s in todo:
            c.wait("server " + s["name"], lambda i=s["id"]: _running(c, i), ("yes",), (), 300)
        print("lab guests running: %d of %d (%d started now)" % (len(want), len(want), len(todo)))
        return 0
    except (Exception, SystemExit) as exc:
        print("could not check the lab guests: %s" % exc)
        return 1


def _running(c, sid):
    s = c.call("GET", "compute", "/servers/" + sid)["server"]
    return "yes" if s["status"] == "ACTIVE" and s.get("OS-EXT-STS:power_state") == 1 else "no"


def down():
    """Graceful: the script in the distro stops instances and services in order, then the distro is stopped. It refuses while DevStack installs."""
    if not distro_exists():
        print("The lab distro %s does not exist; nothing to stop." % lab.DISTRO)
        return 0
    target = lab.lab_dir() / "lab_down.sh"
    target.write_bytes(DOWN_SCRIPT.read_bytes().replace(bytes([13, 10]), bytes([10])))
    r = wsl("bash", "/mnt/c/%s" % str(target)[3:].replace("\\", "/") if sys.platform == "win32" else str(target))
    print((r.stdout or "") + (r.stderr or ""))
    if r.returncode == 3:
        return 3
    subprocess.run(["wsl", "--terminate", lab.DISTRO], cwd=NEUTRAL_DIR)
    print("lab stopped (its data is kept)")
    return 0


VM_HOST = "openmove-vmhost"          # the distro that builds and runs the virtual machine (QEMU with KVM)
VM_DIR = "/var/lib/openmove-vm"
VM_PORT = 2204
VM_IQN = "iqn.2026-11.test.openmove:vm-1"
VM_CHAP_USER = "omuser"


def vm_script(name):
    return (Path(__file__).with_name("vm") / name).read_bytes().decode("utf-8").replace("\r\n", "\n")


def vm_chap_secret(cfg):
    """A 16-character CHAP secret for the lab's test target, derived from the lab password so that no second secret is kept anywhere."""
    import hashlib
    return hashlib.sha256((cfg["password"] + ":chap").encode("utf-8")).hexdigest()[:16]


def vm_up():
    """A real virtual machine (its own kernel) as a bare host, and an iSCSI target with two portals for it to log in to (docs/LAB.md)."""
    from openmove.lab import hosts as H
    cfg = lab.load_config()
    if H.ensure_distro(VM_HOST, "vmhost"):
        print("made the distro %s" % VM_HOST)
    H.keep_running(VM_HOST)
    rc, out, err = H.run(VM_HOST, "command -v qemu-system-x86_64 >/dev/null && command -v debootstrap >/dev/null || { export DEBIAN_FRONTEND=noninteractive; apt-get update -qq >/dev/null 2>&1; "
                         "apt-get install -y -qq qemu-system-x86 qemu-utils debootstrap e2fsprogs >/dev/null 2>&1; }; ls -l /dev/kvm", timeout=1500)
    if "/dev/kvm" not in out:
        print("this machine gives WSL no /dev/kvm (CPU virtualisation is off, or nested virtualisation is not available): %s" % (out + err).strip())
        return 1
    rc, out, err = H.run(VM_HOST, "test -f %s/disk.img && echo yes || echo no" % VM_DIR, timeout=60)
    if out.strip() != "yes":
        print("building the virtual machine disk (a few minutes: Ubuntu 24.04 from the package archive) ...")
        rc, out, err = H.run(VM_HOST, vm_script("build_vm.sh"), [VM_DIR, cfg["password"], "8G"], timeout=3000)
        if rc != 0:
            print("the build failed: %s" % (out + err)[-1500:])
            return 1
    rc, out, err = H.run(VM_HOST, vm_script("run_vm.sh"), ["start", VM_DIR, str(VM_PORT)], timeout=120)
    print(out.strip() or err.strip())
    ip = H.address(VM_HOST)
    lab_target = vm_script("make_target.sh")
    H.run(lab.DISTRO, lab_target, ["remove"], timeout=60)
    rc, out, err = H.run(lab.DISTRO, lab_target, ["create", ip, VM_IQN, VM_CHAP_USER, vm_chap_secret(cfg)], timeout=120)
    if rc != 0:
        print("could not make the iSCSI target: %s" % (out + err)[-800:])
        return 1
    print(out.strip().splitlines()[-1])
    print("")
    print("The virtual machine is a bare host: address %s, SSH port %d, login root, password: the lab password. Wait about 30 seconds for it to boot." % (ip, VM_PORT))
    print("In OpenMove (Hosts tab, Set up a new host from here): role migration host, protocol iSCSI,")
    print("  portals          %s:3261 and %s:3262" % (ip, ip))
    print("  initiator name   %s" % VM_IQN)
    print("  CHAP user        %s" % VM_CHAP_USER)
    print("  CHAP secret      %s   (lab only)" % vm_chap_secret(cfg))
    print("Then the result should show 2 sessions and 1 multipath device with 2 paths.")
    return 0


def vm_down():
    from openmove.lab import hosts as H
    rc, out, err = H.run(VM_HOST, vm_script("run_vm.sh"), ["stop", VM_DIR], timeout=120)
    print(out.strip() or err.strip())
    rc, out, err = H.run(lab.DISTRO, vm_script("make_target.sh"), ["remove"], timeout=60)
    print(out.strip().splitlines()[-1] if out.strip() else "no target")
    return 0


def main(argv):
    try:
        return run(argv)
    except lab.LabError as exc:
        print(str(exc))
        return 2


def run(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        return status()
    if cmd == "vm-up":
        return vm_up()
    if cmd == "vm-down":
        return vm_down()
    if cmd == "fresh-host":
        from openmove.lab import hosts as lab_hosts
        r = lab_hosts.ensure_fresh_host()
        print("A bare host %s: address %s, port %d, login %s, password: the lab password (lab-secret.json)." % ("made" if r["made"] else "is ready", r["address"], r["port"], r["user"]))
        print("In OpenMove (Hosts tab, Set up a new host from here) give that address, port and login; read the host key and compare it with:")
        print("  wsl -d %s -u root -- ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub" % lab_hosts.FRESH_DISTRO)
        return 0
    if cmd == "up":
        return up()
    if cmd == "down":
        return down()
    if cmd in ("seed", "more", "clean", "list"):
        sys.argv = ["seed.py", cmd]
        import runpy
        runpy.run_path(str(Path(__file__).with_name("seed.py")), run_name="__main__")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
