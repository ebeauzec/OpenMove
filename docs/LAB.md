# The OpenMove lab: a real OpenStack on this machine

The demonstration (`OpenMove-Demo.bat`) shows the screens with a **mock** cloud and a **simulated** migration. The lab (`OpenMove-Lab.bat`) is different: a real Keystone, Nova and Cinder run in a disposable WSL 2 distro on this machine (DevStack), and the real engine moves real volumes in it with Cinder's retype.

## What the lab proves, and what it does not

| It proves | It does not prove |
|---|---|
| The engine's calls are accepted by a real Cinder and Nova: lock, retype with on-demand migration, unlock. | Anything about the customer's **Queens** build. The lab runs a recent release (DevStack `stable/2025.1`). The Queens profile stays unvalidated. |
| The order, the permit per call, the polling of `migration_status`, the before and after comparison, the reverse (retype back) work against real behaviour, including attached volumes (KVM) and boot-from-volume. | Anything about **SolidFire or FlashArray**. The two back-ends are LVM volume groups on loop devices (`sf-sim` stands in for the source, `pure-sim` for the target). |
| Failure handling for faults that can be caused in a real cloud (a stopped service, a volume in use, a refused retype). | The **SolidFire and FlashArray adapters**: the lab's class C moves run through the `lvm` adapter, so the copy, the history rebuild, the record swap and the rollbacks are proven against a real Cinder, but not the arrays' own behaviour. |
| Class C, the staged method: the data mover and the Cinder helper on two separate hosts (two WSL 2 distros), SSH with pinned host keys, the history rebuild, the destination record in Cinder's object layer, the switch, rollback R2. | The impact of a copy on a production array; a compute host that is another machine. |

A lab result is evidence that the engine behaves. It is **not** the lab validation (gate G0) of a customer's release; people must still run the playbook's tests on a copy of the customer's platform.

## Safety rules of lab mode

* The lab file (address, password) lives outside the repository, in `C:\OpenMoveLab` (or `OPENMOVE_LAB_DIR`). The password is lab-only, is never printed, and is never written to the journal.
* The lab validation shortcut ("Lab: validate, attest and sign the gate") works only in lab mode, only for a release whose profile is marked `"lab": true`, and says so in the journal. A failed check still blocks. The two signers are named "Lab operator" and "Lab reviewer" so no report can pass them off as a customer sign-off.
* The seeding tool (`tools/lab/seed.py`) refuses an address that is not local.
* The engine's own rules apply unchanged: dry run first, a permit for every call, G2 and G3 signed by a second person, a halt stops the next call, an unknown outcome halts and is never retried.

## Commands

| Command | What it does |
|---|---|
| `OpenMove-Lab.bat` | Starts the lab if it is not answering, then OpenMove in lab mode (it connects by itself). It opens **two browser windows**: OpenMove, and the OpenStack dashboard (Horizon, `/dashboard`), already signed in as `admin`: OpenMove logs in on the browser's behalf, on this machine only. `--no-browser` opens neither. |
| `OpenMove-Lab.bat up` | Starts the lab: keeps the distro running, loads the kernel modules, re-attaches the loop devices behind the two volume groups, starts the services. |
| `OpenMove-Lab.bat more` | Adds the second, larger set of volumes of every class on top of the first (see "The larger catalogue"). It grows the two volume groups first. |
| `python tools/lab/lab.py fresh-host` | Makes (or readies) a third, bare WSL distro `openmove-fresh` with only `sshd` and the root password login, as a customer would hand over a new Linux host. Prints its address, port (2203) and login. Use it to try **Set up a new host from here** on the Hosts tab (see "A bare host for the host setup"). |
| `OpenMove-Lab.bat seed` | Creates the test objects (all named `lab-*`; see "The seeded catalogue" below). |
| `OpenMove-Lab.bat clean` / `list` | Removes or lists them. |
| `OpenMove-Lab.bat status` | Says whether Keystone answers. |
| `OpenMove-Lab.bat down` or `OpenMove-Lab-Stop.bat` | Stops the lab gracefully: instances first, then the OpenStack services, the web server, the queue and the database, then the volume groups, then the distro. Its data is kept. It refuses while DevStack is still installing. |
| `python tools/lab/lab_staged.py status` | What each lab volume is (class, status, back-end, snapshots) and the readiness of the two lab hosts. Also `dry`, `run NAME`, `run NAME --resume`, `fill NAME [MiB]` and `snapshot NAME [COUNT]` (class C, below). It refuses any volume not named `lab-*`. |
| `OpenMove-Lab.bat reset` | Lab mode with an empty journal and evidence folder. |

## What `OpenMove-Lab.bat` starts

One double-click brings up everything the lab needs to operate fully. It does these steps in order and prints what each one found:

| Step | What happens | If it goes wrong |
|---|---|---|
| 1 | The WSL distro `openmove-lab` is started and kept running; the kernel modules and the loop devices behind the two volume groups are brought back | The message says which one |
| 2 | Every DevStack service is started; a unit that failed at boot (for example `nova-compute`, which failed 17 seconds after a restart before the queue and libvirt were ready) is restarted, up to four times | The units still failed are named |
| 3 | The lab waits for Keystone and for the compute service to report `up` | After 5 minutes it says which is missing |
| 4 | The lab guests that should run are started if they are not (a guest that Nova calls `ACTIVE` while its compute service was down is read by power state, not by status). The guests that the catalogue keeps stopped, paused or suspended on purpose are left as they are | The guests that did not start are named |
| 5 | OpenMove starts in lab mode, connects, and **prepares both lab hosts**: the second distro `openmove-mover` (made from the same image if it is missing), `sshd` with key login on 2201 and 2202, OpenMove's key, the host-key pins, the data mover and the Cinder helper, and the readiness checks | The Hosts tab shows the failed check |
| 6 | Horizon opens, signed in as `admin` | |

The catalogue itself is data, not infrastructure: `OpenMove-Lab.bat seed` makes the first set and `OpenMove-Lab.bat more` the larger one. Neither runs by itself.

## A bare host for the host setup

`python tools/lab/lab.py fresh-host` makes the distro `openmove-fresh` from the same image as the lab, installs only `openssh-server`, sets the root password to the lab password and starts `sshd` on port 2203 with password login. Nothing of OpenMove is on it: no account, no key, no data mover, no LVM tools. That is the state of a new host that a customer prepared.

In OpenMove (lab mode or not), Hosts tab, **Set up a new host from here**: name `fresh-1`, the address the command printed, port 2203, login `root`, the lab password, role migration host. Press **Read the host key**, compare it with `wsl -d openmove-fresh -u root -- ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`, type the fingerprint and a name, and press **Set up the host**. The steps S1, S2 and S5 pass, the host is registered with the service account `openmove`, and H1 to H7 pass: the data mover is placed and the host sees both volume groups (the distros share one kernel).

What this lab cannot show: the iSCSI logins and the multipath maps. Pick the protocol iSCSI and step S4 fails with the reasons (`iscsid` cannot bind its socket because the Cinder host's `iscsid` owns it in the shared network namespace; `multipathd` is skipped inside a container). That is the honest result of the lab; a real machine or virtual machine and the arrays are needed to prove those parts (`COVERAGE.md`).

To start over, `wsl --unregister openmove-fresh`, then the command again.

## The seeded catalogue

`seed` makes about 35 objects across five volume types, all bound to the source back-end `sf-sim` (`sf-sim`, `sf-gold`, `sf-bulk`, `sf-multiattach`, `sf-encrypted`), so a retype to `pure-sim` has to leave each of them. The "class" column is what the engine should decide; the lab shows whether it does.

| Name | Count | What it is | Expected |
|---|---|---|---|
| `lab-a-detached-*` | 8 | Detached, 1 to 5 GiB, types `sf-sim`, `sf-gold`, `sf-bulk` | Class A: moved |
| `lab-a-image-*` | 3 | Bootable volumes made from the cirros image, so they hold real data | Class A: moved |
| `lab-a-attached-*` | 4 | Attached to running servers (`lab-srv-*`) | Class A: instance locked around the move |
| `lab-boot-*` | 2 | Boot volumes of running servers (`lab-srv-boot-*`) | Class A: moved with the instance running |
| `lab-b-snap-*` | 3 | One to two snapshots each | Class C: the API cannot show snapshot history, so rotation is unknown (see below). Held back with cause `no_data_mover` until the lab hosts are registered; moved by the staged method after that |
| `lab-d-stopped-*` | 2 | Attached to stopped servers (`lab-srv-off-*`) | Class D: held back |
| `lab-e-multiattach` | 1 | Multi-attach type | Class E: held back |
| `lab-e-encrypted` | 1 | LUKS-encrypted type (the lab's fixed key manager) | Class E: held back |
| `lab-t-on-target-*` | 2 | Already on `pure-sim` | Not admitted, cause `already_on_target` |
| `image-<image id>` | 1 | Not seeded: DevStack's image-volume cache creates it when a volume is made from an image | Class E, cause `image_cache` |

Class B needs the history of deleted snapshots, which only the Cinder database holds. With the APIs only, any volume with snapshots is class C ("rotation unknown"), as the classifier's own rule says. To exercise the class B path in the lab, import a playbook export that carries the churn figures. What the real lab showed beyond the mock: the image-cache volume, and that the "on target" volumes were admitted until the pre-flight learned to leave them out.

### The larger catalogue

`OpenMove-Lab.bat more` adds a second set on top of the first, so that every class can be tested more than once and in more shapes. It first grows the two volume groups (sparse files, so the disk is used only as the volumes are written): `sf-sim` to 220 GiB and `pure-sim` to 260 GiB, because a class C move needs the volume and every snapshot of room on the target. It then waits for Cinder to see the new capacity (Cinder learns it from the back-end's report, once a minute; volumes made earlier are refused with "no valid backend"). It takes several minutes, most of it booting guests.

| Name | Count | What it is | Expected |
|---|---|---|---|
| `lab-a2-detached-*` | 12 | Detached, 1 to 4 GiB, all three types | Class A |
| `lab-a2-image-*` | 3 | Bootable volumes with data | Class A |
| `lab-a2-attached-*` | 4 | Two volumes each on two running guests (`lab-srv-more-1`, `-2`) | Class A: one instance with two volumes |
| `lab-a2-boot-*` | 2 | Boot volumes of two running guests | Class A |
| `lab-a2-paused-1` | 1 | Attached to a paused guest | Class A (a paused guest is allowed) |
| `lab-c-snap-*` | 6 | Detached, with 1, 2, 3, 4, 2 and 1 snapshots; the last is 2 GiB | Class C |
| `lab-c-wide-1` | 1 | One volume with 8 snapshots | Class C (many snapshots) |
| `lab-c-att-*` | 3 | Attached to one running guest (`lab-srv-more-3`), one forced snapshot each | Class C, attached (Nova swap) |
| `lab-d-stopped-3` to `-5` | 3 | Attached to a stopped guest (`lab-srv-off-3`) | Class D |
| `lab-d-suspended-1` | 1 | Attached to a suspended guest | Class D |
| `lab-e-multiattach-2`, `lab-e-encrypted-2` | 2 | A second multi-attach and a second encrypted volume | Class E |
| `lab-e-error-1` | 1 | In status `error` | Class E |
| `lab-e-migrating-1` | 1 | `migration_status` set to `migrating`, as if a move were in flight | Class E |
| `lab-t-on-target-3`, `-4` | 2 | Already on `pure-sim` | Not admitted |

With both sets the lab holds about 70 volumes and 16 guests. OpenMove classes them as 46 class A (before the class C moves of `lab-b-snap-1` and `lab-a-attached-1`), 11 class C, 6 class D and 8 class E, plus the volumes that are not admitted. The volume names carry no class when the cloud is a customer's; here they say what the engine should do, so a result can be compared with the name at a glance.

Class B has no volume of its own. With the APIs only, any volume with snapshots is class C, because rotation can be read only from the Cinder database. To see class B, import a churn export that marks a volume's snapshots as rotating (the playbook's query results; `docs/USER_GUIDE.md`).

If the lab refuses the encryption settings of the encrypted type, the seeder says so and carries on. `seed` stops when `lab-*` objects already exist; run `clean` first. Booting the servers is the slow part (a few minutes).

## What the lab has shown so far

First runs of the real engine against the lab (a detached volume, a volume attached to a running instance, a boot volume; then the way back for the last two). Every result below was read from the cloud itself, not only from the engine's post-check.

| Result | Detail |
|---|---|
| The forward move works | Lock, retype with on-demand migration, follow `migration_status`, post-check, unlock. About 25 seconds per volume here (small volumes on local LVM). Types, back-ends, attachments and instance states match the before-state; no instance is left locked. |
| The way back works | A retype back returns the volume to `sf-sim` with its attachment and a running instance. |
| **Found: the way back was not locked** | The forward move locked the instance and the reverse did not. Fixed: the reverse locks the instance too and unlocks it at the end. |
| **Found: Cinder's image-cache volume** | DevStack's cache keeps a read-only volume named `image-<image id>` in an internal project. OpenMove admitted it as class A. Now class E, cause `image_cache`. |
| **Found: volumes already on the target** | They were admitted and only blocked at the dry run. Now not admitted, cause `already_on_target`. |
| **Found: the lock state was invisible to the audit** | The inventory read of instances sent no Nova microversion, so `locked` was always empty. Now 2.9, and the engine refuses an instance whose lock state is not shown. |
| **Found: a detached volume shows `retyping`** | While a detached volume is retyped its status is `retyping`, not `available`, so it cannot be attached in the middle of the move. The mock now does the same. |
| **Found: Cinder accepts a retype of a volume with a snapshot and silently undoes it** | The call is accepted, the volume goes back to normal, the type is unchanged and there is no error. The engine waits 30 seconds, sees a volume that is normal and not on the target, treats it as an undone retype, rolls back and records why. |
| **Found: the post-check ignored who the volume belongs to** | It now also compares the creation time, project, owner, snapshot id, source volume id, availability zone and multi-attach flag. |
| **Found: a class C volume could be started** | Asking the engine for a volume that is not class A or B now fails with a clear message instead of starting nothing. |
| Class B needs the database | With the APIs only, a volume with snapshots is class C (rotation unknown). The class B path (wait for rotation) is covered by the mock tests only. |

## Class C in the lab

Class C needs a migration host and a Cinder host (`MOVER.md`). In the lab both are WSL 2 distros on this machine, and OpenMove makes them without a terminal (`openmove/lab/hosts.py`, run when OpenMove starts in lab mode):

| Host | Distro | SSH port | What runs there |
|---|---|---|---|
| `lab-cinder` | `openmove-lab` (the DevStack distro) | 2201 | The Cinder helper, run with DevStack's own Python (`/opt/stack/data/venv/bin/python`) |
| `lab-mover` | `openmove-mover`, a second distro made from the same image | 2202 | The data mover. It is a separate instance; it sees the same volume groups because WSL 2 distros share one kernel, which is what a migration host needs |

The setup does what an administrator would do: it creates the second distro if it is missing, installs OpenSSH, Python and LVM, starts `sshd` with key login only, adds OpenMove's own public key, registers both hosts under the address the distros have now (it changes at every restart), pins the host keys and runs the host checks H1 to H9, C1 and C2. In the lab the fingerprint is read from the distro itself, and the journal says that no person compared it. On a customer site a person does that.

`python tools/lab/lab_staged.py` drives OpenMove's own handlers against the lab (the same ones the screen calls), signs G2, G3 and G4 as a made-up second person, and reads the result back. It does not shorten the method: two rechecks 300 seconds apart still take five minutes.

| Result | Detail |
|---|---|
| **A detached volume with one snapshot moved from `sf-sim` to `pure-sim`** | `lab-b-snap-1`: precheck, validate, copy, sync, two rechecks, cutover (all nine steps), postcheck, G4, soak. About 7 minutes, 5 of them the wait between the rechecks. Read from outside OpenMove: Cinder shows the volume and its snapshot on `pure-sim`; the logical volume and the snapshot image on `pure-sim` exist; the SHA-256 of the volume on `sf-sim` and on `pure-sim` is identical. The source stays until the soak ends, as the method says |
| **An attached volume with one snapshot moved from `sf-sim` to `pure-sim` under a running guest** | `lab-a-attached-1`, with random data written before and after the snapshot. The cutover took the nine steps; the Nova swap ran as a live libvirt block copy (seen at 69% on the guest's second disk) and the guest kept running. Read from outside OpenMove: Cinder shows the volume `in-use` on `pure-sim`, attached to the same instance; libvirt shows the guest's disk on a new device; the snapshot image is on `pure-sim`; the rollback clone is kept for the soak. **Not measured**: guest IO during the copy and the length of the pivot (the lab guest does no workload) |
| **Found: the Cinder helper refused a source frozen by OpenMove** | `record-create` rejected a source in maintenance, which is what the freeze step had just made it. Fixed in `cinder_helper.py` (a source that OpenMove froze is accepted); test added |
| **Found: R2 run against real Cinder** | A run stopped in the middle of its rollback left the source frozen. On restart the engine refused to start the volume because a frozen volume reads as class E. Fixed in `engine/runner.py` (a volume the journal holds is allowed to start again); the engine then finished R2: the record deleted, the staging volume and the clones removed, the source unlocked. Test added |
| **Found: the Nova volume swap body was wrong** | The first attached class C move stopped at the `switch` step: real Nova answered 400, `'volumeId' is a required property`. The engine had sent `volume_id`, and the mock had accepted it. R2 ran by itself and left the lab clean (the record deleted, the staging volume and the clones removed, the volume unfrozen and unlocked). Fixed in `engine/actions.py`; the mock now validates the body as Nova does, so every attached test would fail on the old key |
| **Found: a swap waits for ever when the compute service is down** | After the laptop shut down, `nova-compute` had failed to start and the guests were off, but Nova still said `ACTIVE` and the API accepted the swap. The volume stayed `retyping`/`migrating` for minutes. Fixed: the plan and the cutover now check the guest's power state, and the cutover checks that the compute service of the instance's host is up (N4); either failure stops the volume before the swap. Cinder's state was reset by hand (an administrator reset of `migration_status`), the engine saw the failed switch and ran R2 by itself |
| **Found: R2 said "as it was" for a source left `retyping`** | The end of R2 now compares the source's status with the one before the freeze and halts for a person if it differs |
| Resume after a crash | The laptop shut down in the middle of a run; the next start read the journal and carried on from the step it had reached |
| Hosts without a terminal | The distro, `sshd`, the key, the host-key pins and all readiness checks (including the real Cinder helper in DevStack's Python) are set up and passed by one start |

**Failure injection in the lab (2026-11-03).** Each was run against the real cloud and read from the cloud itself, not only from the engine:

| Failure | Result |
|---|---|
| An instance locked by a tenant before the move | The dry run says it is left locked; the move completed and the engine sent no lock and no unlock for it |
| HALT pressed during a real move of an attached volume | Halted at once; the engine sent nothing more; Cinder finished its copy on its own; after resume the engine read the result, ran the post-check and carried on; the instance was never left locked |
| The Cinder API stopped for about 25 seconds during a move | The engine said it could not continue, sent nothing, did not guess, did not halt (it only reads); when the API returned it saw the move had finished and ran the post-check |
| The Cinder volume service stopped for 75 seconds during a move | The engine kept waiting and watching (a retype may legitimately take hours); when the service returned the copy finished and the post-check passed |

Not exercised: a power cut on the Cinder or compute host, a lock that Nova refuses, and a soak of real length (the lab soak was not waited out). The Horizon auto-login works against the real dashboard.

## A first run, in order

1. `OpenMove-Lab.bat seed` once, then `OpenMove-Lab.bat`.
2. Platform, Inventory, Audit: read-only, as against any cloud.
3. Pre-flight: choose `sf-sim` as the source and `pure-sim` as the target, access level "OpenStack APIs only", run it, then press "Lab: validate, attest and sign the gate".
4. Move: press **Dry run** and read it. Then **Start the run**, sign G2 and G3 as a second person (a name that is not "Lab operator"), release, and watch. After the post-check, sign G4. Try **Reverse** on one volume.
5. Look at the evidence folder (`C:\OpenMoveLab\openmove-home\evidence\<volume id>\`).

## Setting the lab up the first time

See `tools/lab/README.md`. In short: an Ubuntu 24.04 WSL image is imported as the distro `openmove-lab`, and `tools/lab/provision_devstack.sh` installs DevStack with two LVM back-ends. It takes about 40 minutes and several gigabytes of downloads (Ubuntu packages, Python wheels, OpenStack source from opendev.org).

## Troubleshooting the lab

These come from setting the lab up. Where something was not seen but is expected, the text says so.

| Problem | Cause and fix |
|---|---|
| A second run of `stack.sh` fails at the first network with "No tenant network is available for allocation". | A re-run recreates the Neutron database while the old Neutron server is still running, so the network-id table is empty (`ml2_geneve_allocations` has no rows) and the OVN hash ring is empty. **Stop every DevStack service before a re-run:** `systemctl stop 'devstack@*'` inside the distro, then run `stack.sh`. |
| `stack.sh` stops at its first package install with "Could not get lock /var/lib/dpkg/lock-frontend ... unattended-upgr". | After the distro starts, Ubuntu's automatic updater holds the package lock. Stop `unattended-upgrades` and wait until the lock is free, then run `stack.sh` again. |
| `stack.sh` ends with an error in `create_userrc.sh` ("Failed to discover available identity versions when contacting http://localhost:5000/v3/"). | That step only writes per-user credential files. By then Keystone, Cinder, Nova, Glance, Neutron and Horizon are installed and running. Check with the dashboard (`/dashboard/auth/login/` answers 200) and `OpenMove-Lab.bat status`. Nothing else is needed. |
| `OpenMove-Lab.bat seed` stops with "Maximum number of volumes allowed (10) exceeded". | DevStack's default quotas are small. The seeder now raises the lab admin project to 200 volumes, 2000 GiB, 200 snapshots, 50 instances and 100 cores before it creates anything. |
| `Failed to translate 'G:\My Drive\...'` when `wsl` runs. | `wsl` was started from a folder it cannot map (a cloud-drive path). The lab launcher starts it from a neutral folder. For a command of your own, start it from `C:\`. |
| A helper script run in the distro fails with a strange character error. | The script has Windows line endings. Scripts run by `bash` inside the distro need LF endings. |
| A path such as `/mnt/c/...` is rewritten when passed to `wsl` from a Unix-style shell on Windows. | Run `wsl` commands from PowerShell, not from Git Bash. |
| Windows reaches Keystone at `localhost` but not the other services after a restart. | **Expected, not yet seen.** The service catalogue holds the address the distro had when `stack.sh` ran, and WSL can give the distro another one after a restart. Re-run `stack.sh` (stop the services first, see above) to write the catalogue again. |
| The loop devices behind the two volume groups are gone after a restart. | `OpenMove-Lab.bat up` attaches them again and starts the services. Use `OpenMove-Lab-Stop.bat` rather than closing the window, so the services stop cleanly. |
| `OpenMove-Lab-Stop.bat` says it refuses. | `stack.sh` is still running. Wait for it, or stop it by hand. |
| The dashboard does not open in the second window. | Horizon is not installed or still starting. The console says so; open `/dashboard` yourself. The automatic sign-in has been tried against the real dashboard and works. |
| `OpenMove-Lab.bat` says the lab cloud does not answer. | Start it: `OpenMove-Lab.bat up`. The launcher does this by itself when it can. |
| The Connect page says "POST http://localhost/v3/auth/tokens failed ... actively refused it". | Two causes. The lab is not running (WSL stops an idle distro; start it with `OpenMove-Lab.bat up`), or the address is wrong: Keystone is at `http://localhost/identity`, not `http://localhost`. In lab mode the Connect form is filled in for you, with the lab's address, `admin`, the project and both domains; leave the password blank and press *Connect* (the lab's own password is used, and only for the lab's own address). |
| The dashboard login page shows, but I am not signed in. | You opened `http://localhost/dashboard/` yourself. Start the lab with `OpenMove-Lab.bat`; its second window signs in by itself. The user is `admin`, the password is in `C:\OpenMoveLab\lab-secret.json`. A new lab uses whatever password you put there; the lab built here uses the simple value the owner chose, because it is a local lab with nothing in it that matters. |
| `127.0.0.1` refuses the connection but `localhost` works. | WSL forwards `localhost` to the distro; `127.0.0.1` may not be forwarded. OpenMove uses `localhost` for every address it hands the browser in lab mode, and the browser keeps its cookies by host name, so the dashboard sign-in is set under `localhost` too. |
| Two OpenMove windows are open and the link in one says "Open the address that OpenMove printed". | The token is different at every start, and the second program could not take the port, so the link points at the first one. Close both and start the lab once. |

## Resetting the lab

* **Test objects only:** `OpenMove-Lab.bat clean`, then `OpenMove-Lab.bat seed`. `clean` removes everything named `lab-*` (servers first, then snapshots, then volumes).
* **OpenMove's records:** `OpenMove-Lab.bat reset` starts lab mode with an empty journal and evidence folder.
* **The whole cloud:** `wsl --unregister openmove-lab`, then set it up again from `tools/lab/README.md`. Allow about 40 minutes.
