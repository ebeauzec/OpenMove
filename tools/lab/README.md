# Setting up the OpenMove lab

Read `docs/LAB.md` first: it says what the lab is for. This page is the one-time setup on Windows with WSL 2.

Needs: WSL 2, about 20 GB of free disk space, 8 GB of memory or more, and hardware virtualisation (for KVM). Nothing is installed on Windows itself; everything lives in one disposable WSL distro.

1. Download the official Ubuntu 24.04 WSL image (about 390 MB) from `https://releases.ubuntu.com/noble/` (the file `ubuntu-24.04.4-wsl-amd64.wsl`) and check it against `SHA256SUMS` on that page.
2. Import it. The distro must be called `openmove-lab`:
   `wsl --import openmove-lab C:\OpenMoveLab\distro C:\OpenMoveLab\ubuntu-24.04.4-wsl-amd64.wsl --version 2`
3. Make a lab-only password and keep it outside the repository, in `C:\OpenMoveLab\lab-secret.json`: `{"password": "..."}`.
4. Run `tools/lab/provision_devstack.sh` inside the distro as root with `LAB_PASSWORD` set from that file. It installs the packages, creates the `stack` user, clones DevStack (`stable/2025.1`) and starts `stack.sh` in the background (about 40 minutes). Follow it with `tail -f /opt/stack/logs/stack.out`.
5. When `stack.sh` has finished, write `C:\OpenMoveLab\lab.json`: `{"auth_url": "<the Keystone address the end of stack.out prints, without /v3>", "user": "admin", "project": "admin"}`.
6. `OpenMove-Lab.bat seed`, then `OpenMove-Lab.bat`.

The kernel pieces WSL does not load by itself (iSCSI initiator and target, device mapper, Open vSwitch) are listed in `/etc/modules-load.d/openmove-lab.conf` by the script; `OpenMove-Lab.bat up` loads them again after a restart.

## Re-running `stack.sh`

Run it again only if you must (for example to turn on another service). Stop the services first, and make sure the automatic updater is not holding the package lock; otherwise the second run fails in the network step or at the first package install. `docs/LAB.md` ("Troubleshooting the lab") gives the cause and the fix of each problem met while setting the lab up, including the harmless error in `create_userrc.sh` at the very end.

`OpenMove-Lab.bat seed` raises the lab project's quotas itself, so no quota step is needed before seeding.

