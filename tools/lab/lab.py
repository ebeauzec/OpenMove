# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Control of the OpenMove lab: a real single-node OpenStack (DevStack) that runs on this machine.

    python tools/lab/lab.py status | up | down | seed | more | fresh-host | vm-up | vm-down | clean | list | shell-help
    python tools/lab/lab.py deploy [--install-prereqs] [--no-seed]      (Linux host) build the whole lab from nothing
    python tools/lab/lab.py shell [lab|mover|fresh|vmhost]               (Linux host) a root shell in a lab machine
    python tools/lab/lab.py destroy --yes                                (Linux host) remove every lab machine and its disk

Two ways to run it, chosen by the operating system (OPENMOVE_LAB_BACKEND=wsl|linux forces one):
  * Windows with WSL 2: the lab is a distro (the lab is a lab for this machine, not part of the shipped program). Setting it up the first time
    (downloading the Ubuntu image, importing the distro, running DevStack, about 40 minutes) is described in tools/lab/README.md.
  * a Linux host: the lab is KVM virtual machines on a private bridge (tools/lab/linux/labvm.sh); `deploy` builds all of it, docs/LAB.md.
The commands here run it every day after that. Standard library only.
"""
import json
import os
import secrets
import shlex
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


def linux():
    return lab.backend() == "linux"


def hosts_mod():
    from openmove.lab import hosts as H
    return H


def distro_exists():
    if linux():
        return lab.DISTRO in hosts_mod().distros()
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
    if linux():
        print("lab machines (virtual machines on this host):")
        hosts_mod().labvm("list", check=False)
    else:
        print("distro %s: %s" % (lab.DISTRO, "present" if distro_exists() else "MISSING (see tools/lab/README.md)"))
    print("keystone at %s: %s" % (cfg["auth_url"], "answers" if up else "does not answer (run: lab.py up)"))
    return 0 if up else 1


def script_text(path):
    return Path(path).read_bytes().decode("utf-8").replace("\r\n", "\n")


def up():
    if not distro_exists():
        if linux():
            print("The lab machine does not exist yet. Build it with:  python tools/lab/lab.py deploy")
        else:
            print("The lab distro %s does not exist. Follow tools/lab/README.md to set it up." % lab.DISTRO)
        return 2
    if linux():
        H = hosts_mod()
        H.keep_running(lab.DISTRO)                   # starts the lab virtual machine when it is stopped
        rc, out, err = H.run(lab.DISTRO, script_text(UP_SCRIPT), timeout=1800)
        print(out + err)
    else:
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
        # A start request sent while nova-compute was only just starting can be lost: Nova then keeps the guest in the task "powering-on" for ever (Horizon: "starting")
        # and libvirt never sees a start. So: send the start, give it two minutes, and when a guest is still not running, reset its stale task and start it again (three rounds).
        for attempt in (1, 2, 3):
            servers = c.call("GET", "compute", "/servers/detail?all_tenants=1")["servers"]
            want = [s for s in servers if s["name"].startswith("lab-srv-") and not s["name"].startswith(("lab-srv-off-", "lab-srv-paused-", "lab-srv-susp-"))]
            todo = [s for s in want if s.get("OS-EXT-STS:power_state") != 1 or s["status"] != "ACTIVE"]
            if not todo:
                print("lab guests running: %d of %d%s" % (len(want), len(want), "" if attempt == 1 else " (after %d round%s)" % (attempt - 1, "" if attempt == 2 else "s")))
                return 0
            print("starting %d lab guest%s (round %d)" % (len(todo), "" if len(todo) == 1 else "s", attempt))
            for s in todo:
                path = "/servers/%s/action" % s["id"]
                if s.get("OS-EXT-STS:task_state") and (attempt > 1 or _stale(s)):          # a task that is old, or still there after a round: the request was lost
                    c.call("POST", "compute", path, {"os-resetState": {"state": "active"}}, soft=True)
                    c.call("POST", "compute", path, {"reboot": {"type": "HARD"}}, soft=True)
                    continue
                act = {"os-start": None} if s["status"] == "SHUTOFF" and not s.get("OS-EXT-STS:task_state") else {"reboot": {"type": "HARD"}}
                c.call("POST", "compute", path, act, soft=True)
            for _ in range(24):                                              # two minutes
                time.sleep(5)
                if all(_running(c, s["id"]) == "yes" for s in todo):
                    break
        servers = c.call("GET", "compute", "/servers/detail?all_tenants=1")["servers"]
        left = [s["name"] for s in servers if s["name"].startswith("lab-srv-") and not s["name"].startswith(("lab-srv-off-", "lab-srv-paused-", "lab-srv-susp-"))
                and (s.get("OS-EXT-STS:power_state") != 1 or s["status"] != "ACTIVE")]
        if left:
            print("these lab guests did not start after three rounds: %s" % ", ".join(sorted(left)))
            return 1
        print("lab guests running")
        return 0
    except (Exception, SystemExit) as exc:
        print("could not check the lab guests: %s" % exc)
        return 1


def _stale(s, seconds=120):
    """A task older than this is a request that was lost (the compute service restarted before it took the message)."""
    from datetime import datetime, timezone
    try:
        age = (datetime.now(timezone.utc) - datetime.strptime(s["updated"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds()
    except (KeyError, ValueError, TypeError):
        return False
    return age > seconds


def _running(c, sid):
    s = c.call("GET", "compute", "/servers/" + sid)["server"]
    return "yes" if s["status"] == "ACTIVE" and s.get("OS-EXT-STS:power_state") == 1 else "no"


def down():
    """Graceful: the script in the distro stops instances and services in order, then the distro is stopped. It refuses while DevStack installs."""
    if not distro_exists():
        print("The lab %s does not exist; nothing to stop." % ("machine" if linux() else "distro " + lab.DISTRO))
        return 0
    if linux():
        H = hosts_mod()
        if H.labvm("status", "lab", check=False) != 0:
            print("the lab machine is not running; nothing to stop")
            return 0
        rc, out, err = H.run(lab.DISTRO, script_text(DOWN_SCRIPT), timeout=1800)
        print(out + err)
        if rc == 3:
            return 3
        H.labvm("stop", "lab")
        print("lab stopped (its data is kept)")
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


H_SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]
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
    if linux():
        return linux_vm_up(H, cfg)
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


def linux_vm_up(H, cfg):
    """On a Linux host the bare machine `vmhost` is the virtual machine with its own kernel; the iSCSI target (two portals) is made on the lab machine."""
    if H.ensure_distro(VM_HOST, "vmhost"):
        print("built the machine %s" % VM_HOST)
    H.keep_running(VM_HOST)
    ip = H.address(VM_HOST)
    lab_ip = H.address(lab.DISTRO)
    lab_target = vm_script("make_target.sh")
    H.run(lab.DISTRO, lab_target, ["remove"], timeout=60)
    rc, out, err = H.run(lab.DISTRO, lab_target, ["create", lab_ip, VM_IQN, VM_CHAP_USER, vm_chap_secret(cfg)], timeout=120)
    if rc != 0:
        print("could not make the iSCSI target: %s" % (out + err)[-800:])
        return 1
    print(out.strip().splitlines()[-1])
    print("")
    print("The virtual machine is a bare host: address %s, SSH port 22, login root, password: the lab password (lab-secret.json)." % ip)
    print("In OpenMove (Hosts tab, Set up a new host from here): role migration host, protocol iSCSI,")
    print("  portals          %s:3261 and %s:3262" % (lab_ip, lab_ip))
    print("  initiator name   %s" % VM_IQN)
    print("  CHAP user        %s" % VM_CHAP_USER)
    print("  CHAP secret      %s   (lab only)" % vm_chap_secret(cfg))
    print("Then the result should show 2 sessions and 1 multipath device with 2 paths.")
    return 0


def vm_down():
    from openmove.lab import hosts as H
    if linux():
        rc, out, err = H.run(lab.DISTRO, vm_script("make_target.sh"), ["remove"], timeout=60) if H.labvm("status", "lab", check=False) == 0 else (0, "", "")
        print(out.strip().splitlines()[-1] if out.strip() else "no target")
        if H.labvm("status", "vmhost", check=False) == 0:
            H.labvm("stop", "vmhost")
        return 0
    rc, out, err = H.run(VM_HOST, vm_script("run_vm.sh"), ["stop", VM_DIR], timeout=120)
    print(out.strip() or err.strip())
    rc, out, err = H.run(lab.DISTRO, vm_script("make_target.sh"), ["remove"], timeout=60)
    print(out.strip().splitlines()[-1] if out.strip() else "no target")
    return 0


STACK_DONE = "stack.sh completed in"


def seed_cmd(cmd):
    sys.argv = ["seed.py", cmd]
    import runpy
    runpy.run_path(str(Path(__file__).with_name("seed.py")), run_name="__main__")


def devstack_state(H):
    """'done', 'running', 'failed' or 'none' for DevStack inside the lab machine (read from its own log)."""
    rc, out, err = H.run(lab.DISTRO, "if [ -f /opt/stack/logs/stack.out ]; then if grep -q '%s' /opt/stack/logs/stack.out; then echo done; "
                         "elif pgrep -f '/stack.sh' >/dev/null; then echo running; else echo failed; fi; else echo none; fi" % STACK_DONE, timeout=60)
    return out.strip().splitlines()[-1] if out.strip() else "none"


def deploy(args):
    """Linux host: everything from nothing, each stage skipped when it is already done, so a stopped run can be started again."""
    if not linux():
        print("deploy builds the lab on a Linux host. On Windows the lab is made as tools/lab/README.md describes.")
        return 2
    H = hosts_mod()
    d = lab.lab_dir()
    d.mkdir(parents=True, exist_ok=True)
    print("== 1. what this host needs")
    H.labvm("prereqs", *(["--install"] if "--install-prereqs" in args else []))
    secret = d / "lab-secret.json"
    if not secret.exists():
        secret.write_text(json.dumps({"password": secrets.token_hex(8)}), encoding="utf-8")
        try:
            os.chmod(str(secret), 0o600)
        except OSError:
            pass
        print("== made the lab-only password in %s (it is not printed)" % secret)
    print("== 2. the private network of the lab")
    H.labvm("net", "up")
    print("== 3. the lab machine (Ubuntu 24.04, the first build takes some minutes)")
    if lab.DISTRO not in H.distros():
        H.labvm("build", "lab")
    H.keep_running(lab.DISTRO)
    auth = "http://%s/identity" % H.address(lab.DISTRO)
    cfgfile = d / "lab.json"
    if not cfgfile.exists():
        cfgfile.write_text(json.dumps({"auth_url": auth, "user": "admin", "project": "admin", "wsl_distro": lab.DISTRO}, indent=2), encoding="utf-8")
    cfg = lab.load_config()
    print("== 4. DevStack in the lab machine (about 40 to 90 minutes)")
    state = devstack_state(H)
    if state == "failed":
        print("the earlier DevStack run did not finish; starting it again (stack.sh can be run again)")
    if state in ("none", "failed"):
        script = "export LAB_PASSWORD=%s\n%s" % (shlex.quote(cfg["password"]), script_text(Path(__file__).with_name("provision_devstack.sh")))
        rc, out, err = H.run(lab.DISTRO, script, timeout=3600)
        print((out + err)[-1500:])
        if rc != 0:
            print("the preparation of the lab machine failed (above)")
            return 1
    waited = 0
    while True:
        state = devstack_state(H)
        if state == "done":
            break
        if state == "failed":
            print("DevStack failed. Read the end of the log:  python tools/lab/lab.py shell lab   then   tail -n 80 /opt/stack/logs/stack.out")
            return 1
        if waited % 300 == 0:
            print("   DevStack is installing (%d minutes so far) ..." % (waited // 60))
        time.sleep(30)
        waited += 30
    for _ in range(60):
        if lab.reachable(cfg["auth_url"]):
            break
        time.sleep(5)
    else:
        print("DevStack finished but Keystone does not answer at %s" % cfg["auth_url"])
        return 1
    print("the lab cloud answers at %s" % cfg["auth_url"])
    print("== 5. the migration host (its own root file system in the lab machine, sshd on 2202)")
    if H.ensure_mover_distro(cfg):
        print("made the migration host")
    print("== 6. the lab guests and volumes")
    if "--no-seed" in args:
        print("skipped (--no-seed): run  python tools/lab/lab.py seed  and  more  later")
    else:
        seed_cmd("seed")
        seed_cmd("more")
    print("")
    print("The lab is built. Start OpenMove on it with  ./openmove-lab.sh  (stop everything with  ./openmove-lab.sh down).")
    print("Dashboard: http://%s/dashboard (admin, the lab password in %s)" % (H.address(lab.DISTRO), secret))
    return 0


def shell(args):
    if not linux():
        print("shell is for a Linux host. On Windows:  wsl -d %s" % lab.DISTRO)
        return 2
    names = {"lab": "openmove-lab", "mover": "openmove-mover", "fresh": "openmove-fresh", "vmhost": "openmove-vmhost"}
    machine = names.get(args[0] if args else "lab")
    if not machine:
        print("lab, mover, fresh or vmhost")
        return 2
    ip, port = lab.linux_target("openmove-lab" if machine == "openmove-mover" else machine)
    cmd = ["ssh", "-t", "-i", str(lab.lab_key()), "-p", str(port)] + H_SSH_OPTS + ["root@" + ip]
    if machine == "openmove-mover":
        cmd.append('nsenter -t "$(systemctl show -p MainPID --value openmove-mover)" -m -r bash -l')
    os.execvp("ssh", cmd)


def destroy(args):
    if not linux():
        print("destroy is for a Linux host. On Windows: wsl --unregister <distro> for each lab distro.")
        return 2
    if "--yes" not in args:
        print("This removes every lab machine and its disk (the OpenStack, the volumes, the migration host). Add --yes to do it.")
        return 2
    H = hosts_mod()
    for m in ("vmhost", "fresh", "lab"):
        H.labvm("destroy", m)
    H.labvm("net", "down", check=False)
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
        if linux():
            print("  python tools/lab/lab.py shell fresh     then:  ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub")
        else:
            print("  wsl -d %s -u root -- ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub" % lab_hosts.FRESH_DISTRO)
        return 0
    if cmd == "deploy":
        return deploy(argv[2:])
    if cmd == "shell":
        return shell(argv[2:])
    if cmd == "destroy":
        return destroy(argv[2:])
    if cmd in ("prereqs", "net"):
        return hosts_mod().labvm(cmd, *argv[2:], check=False)
    if cmd == "up":
        return up()
    if cmd == "down":
        return down()
    if cmd in ("seed", "more", "clean", "list"):
        seed_cmd(cmd)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
