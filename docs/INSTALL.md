# Installation guide

This guide covers installing OpenMove on a workstation, checking the installation, keeping it up to date and removing it. It also covers what the OpenStack cloud must provide. For how to use OpenMove, see `OPERATIONS.md` and `USER_GUIDE.md`. For the OpenStack lab used to test the engine, see `LAB.md`.

## 1. What you are installing

OpenMove is one Python program that uses the standard library only. It starts a web server that listens on the local machine and opens the interface in a browser. It has no installer, no service and no database server. Its journal is one SQLite file.

| You can run it as | Needs on the machine | Status |
|---|---|---|
| Source (Windows, macOS, Linux) | Python 3.9 or later | Tested (Python 3.12 on Windows) |
| A packaged program (`OpenMove.exe` or `OpenMove`) | Nothing; Python is inside it | Built by a script; not yet run on a clean machine; not code-signed |
| A container or its own Linux image | | **Not built** |

## 2. Requirements

### The machine OpenMove runs on

| Item | Requirement |
|---|---|
| Operating system | Windows 10 or 11, macOS, or Linux |
| Python (source only) | 3.9 or later. Nothing else to install |
| Browser | Any current browser. It must run on the same machine, or reach it through a tunnel you set up |
| Disk | The source is a few megabytes. A packaged program is larger because it contains Python. The journal and evidence grow with use (a few megabytes for a test run) |
| Network | Outbound HTTPS to the Keystone, Cinder and Nova endpoints of the cloud. Nothing else. No internet access is needed |
| Port | A free local port, 8765 by default (`--port` changes it) |

### The cloud

| Item | Requirement |
|---|---|
| Identity | The address of the Keystone v3 API, for example `https://cloud.example:5000`. Not the Horizon address |
| Account | An administrator account (the all-projects lists of volumes, instances, snapshots and services are visible only to an administrator). With a lesser account OpenMove still starts and says what it could not read |
| Endpoints | The Cinder (block storage v3) and Nova (compute) endpoints in the catalogue must be reachable from the machine. Choose public, internal or admin on the Connect tab |
| Nova | Microversion 2.9 or later, to see whether an instance is locked. Every release since Mitaka has it |
| TLS | Verified by default. Give the cloud's CA file, or knowingly turn verification off; reports state the setting |

### To change anything in a cloud

OpenMove is read only until all of these hold:

1. The OpenStack release has a recorded, passed lab validation (gate G0). People run the playbook's lab tests on a copy of the platform and record the result on the Move tab.
2. The pre-flight gate is approved: no failed check, every open item attested by a named person, two different people signed, the run is less than 24 hours old.
3. For each volume, a second person has signed G2 and G3 (and G4 later).

None of these is an installation step. They are the controls of `SAFETY.md`.

## 3. Install from source

### Windows

1. Install Python 3 from `python.org`. Tick "Add python.exe to PATH". Check it:

   ```powershell
   python --version
   ```

2. Get the source. Clone the repository (the owner gives access), or copy the folder:

   ```powershell
   git clone https://github.com/ebeauzec/OpenMove.git
   cd OpenMove
   ```

3. Start it by double-clicking `OpenMove.bat`, or:

   ```powershell
   python -m openmove
   ```

### macOS and Linux

```bash
python3 --version          # 3.9 or later
git clone https://github.com/ebeauzec/OpenMove.git
cd OpenMove
./openmove.sh              # or: python3 -m openmove
```

`openmove.sh` may need `chmod +x openmove.sh` the first time.

### What happens at start

OpenMove prints its version, the data folder and a link such as `http://127.0.0.1:8765/?token=...`, and opens that link in your browser. The token changes every start. If the browser does not open, copy the link from the console.

## 4. Install the packaged program

The packaged program is built once, on the operating system it is for. A Windows build is made on Windows, a macOS build on macOS and a Linux build on Linux.

```bash
python -m pip install pyinstaller
python tools/build/build_exe.py
```

The result is in `dist/`: `OpenMove.exe` (or `OpenMove`) and the launchers. Copy that whole folder to the target machine. Nothing else needs to be installed there.

| Point | Detail |
|---|---|
| Synced folders | If the repository is on a cloud drive (Google Drive, OneDrive), the build can fail with "permission denied". Set `OPENMOVE_BUILD_DIR` and `OPENMOVE_DIST_DIR` to a local folder first. |
| Signing | The program is not code-signed. Some antivirus products warn about it. Sign it with your own certificate if the customer requires that. |
| Checking | Start it with `--demo` on a clean machine before you rely on it. This has not been done yet. |

## 5. Check the installation

1. Start the demonstration. It needs no cloud and touches nothing real:

   ```bash
   python -m openmove --demo
   ```

   The console shows `[DEMONSTRATION MODE ...]`, the browser opens, and every screen has a purple banner.

2. From source, run the automated tests (about 90 seconds). All must pass:

   ```bash
   python -m unittest discover -s tests -t .
   ```

   The last lines read `Ran 275 tests` and `OK`.

3. Connect to your cloud on the Connect tab and press *Detect version and back-ends* on the Platform tab. You should see the OpenStack release, the back-ends and the Cinder services. A new release shows **Read only**. That is correct.

## 6. Where OpenMove keeps its files

| What | Where |
|---|---|
| Journal (`journal.db`) and evidence (`evidence/`) | Windows: `%APPDATA%\OpenMove`. macOS: `~/Library/Application Support/OpenMove`. Linux: `~/.local/share/openmove`. `--home <folder>` or the variable `OPENMOVE_HOME` overrides it |
| Demonstration | A temporary folder, deleted on exit |
| Lab mode | `C:\OpenMoveLab\openmove-home` |
| Reports | Where your browser saves downloads. OpenMove keeps no copy |
| Passwords and tokens | Memory only. Never on disk |

**Back up the journal and the evidence folder** before and after a run. They are the record of what was done and who signed what. They contain cloud identifiers (project, host and volume ids), so keep them on the machine or in your own secure store and never in a source repository.

## 7. Network and security notes

* The interface listens on `127.0.0.1` only. Only this machine can reach it, and only with the token.
* `--host 0.0.0.0` lets anyone who can reach the port and has the token use OpenMove, with full rights. It prints a warning. There is no user login in this version. Use it only on a network you control, or through an SSH tunnel.
* Behind a container port mapping or a reverse proxy, add the name clients use with `--allow-host <name>`.
* Run OpenMove on a machine you trust. Anyone with the token can sign gates for the names they type.
* Windows or antivirus software may warn about the unsigned program.

## 8. Upgrade

* From source: `git pull`, then start as before. Run the tests if you changed anything.
* Packaged: build the new version and replace the folder. The journal and evidence stay in the data folder and are read by the new version.
* Read `CHANGELOG.md` first. A new version can change what the pre-flight admits (for example, 0.6.0 stopped admitting volumes that are already on the target back-end). Run the pre-flight again after an upgrade.
* Do not upgrade in the middle of a run. Stop the engine, let any retype in flight finish, then upgrade.

## 9. Uninstall

Delete the program folder. To remove the records as well, delete the data folder (section 6) after you have kept what your process requires. OpenMove adds nothing to the registry, the system path or any service.

## 10. Install the lab (optional, for people developing or testing the engine)

The lab is a real OpenStack on a Windows machine. It is not needed to use OpenMove. It needs WSL 2, hardware virtualisation (KVM), about 20 GB of free disk space and 8 GB of memory or more, and takes about 40 minutes and several gigabytes of downloads. The steps are in `tools/lab/README.md`, and the day-to-day commands in `LAB.md`.

## 11. Installation problems

| Problem | Cause and fix |
|---|---|
| `Python 3 was not found` | Install Python 3 and tick "Add python.exe to PATH", or use the packaged program |
| `Port already in use` | Another OpenMove holds the port. Close it, or use `--port` |
| "Open the address that OpenMove printed when it started." | The token is missing or old. Use the link from the console of the running OpenMove |
| The browser does not open | Copy the "Open:" link from the console. On a server without a desktop, use `--no-browser` and a tunnel |
| Connect fails with a certificate error | Give the CA file on the Connect tab, or turn verification off knowing the risk |
| Platform shows no back-ends or services | The account is not an administrator, or policy hides them |
| The build fails with "permission denied" | The repository is in a synced folder. Set `OPENMOVE_BUILD_DIR` and `OPENMOVE_DIST_DIR` |
| Tests fail on a machine with an old Python | Use Python 3.9 or later |
