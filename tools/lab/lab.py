# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""Control of the OpenMove lab: a disposable WSL distro that runs a real single-node OpenStack (DevStack).

    python tools/lab/lab.py status | up | down | seed | clean | list | shell-help

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
            return 0
        time.sleep(5)
    print("the lab cloud did not answer in 5 minutes; look at the services:  wsl -d %s -u root -- systemctl list-units 'devstack*'" % lab.DISTRO)
    return 1


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
    if cmd == "up":
        return up()
    if cmd == "down":
        return down()
    if cmd in ("seed", "clean", "list"):
        sys.argv = ["seed.py", cmd]
        import runpy
        runpy.run_path(str(Path(__file__).with_name("seed.py")), run_name="__main__")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
