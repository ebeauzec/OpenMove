# The OpenMove lab: a real OpenStack on this machine

The demonstration (`OpenMove-Demo.bat`) shows the screens with a **mock** cloud and a **simulated** migration. The lab (`OpenMove-Lab.bat`) is different: a real Keystone, Nova and Cinder run in a disposable WSL 2 distro on this machine (DevStack), and the real engine moves real volumes in it with Cinder's retype.

## What the lab proves, and what it does not

| It proves | It does not prove |
|---|---|
| The engine's calls are accepted by a real Cinder and Nova: lock, retype with on-demand migration, unlock. | Anything about the customer's **Queens** build. The lab runs a recent release (DevStack `stable/2025.1`). The Queens profile stays unvalidated. |
| The order, the permit per call, the polling of `migration_status`, the before and after comparison, the reverse (retype back) work against real behaviour, including attached volumes (KVM) and boot-from-volume. | Anything about **SolidFire or FlashArray**. The two back-ends are LVM volume groups on loop devices (`sf-sim` stands in for the source, `pure-sim` for the target). |
| Failure handling for faults that can be caused in a real cloud (a stopped service, a volume in use, a refused retype). | Class C (long-lived snapshots, staged rebuild): it is not built, and the lab cannot stand in for the arrays. |

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
| `OpenMove-Lab.bat seed` | Creates the test objects (all named `lab-*`; see "The seeded catalogue" below). |
| `OpenMove-Lab.bat clean` / `list` | Removes or lists them. |
| `OpenMove-Lab.bat status` | Says whether Keystone answers. |
| `OpenMove-Lab.bat down` or `OpenMove-Lab-Stop.bat` | Stops the lab gracefully: instances first, then the OpenStack services, the web server, the queue and the database, then the volume groups, then the distro. Its data is kept. It refuses while DevStack is still installing. |
| `OpenMove-Lab.bat reset` | Lab mode with an empty journal and evidence folder. |

## The seeded catalogue

`seed` makes about 35 objects across five volume types, all bound to the source back-end `sf-sim` (`sf-sim`, `sf-gold`, `sf-bulk`, `sf-multiattach`, `sf-encrypted`), so a retype to `pure-sim` has to leave each of them. The "class" column is what the engine should decide; the lab shows whether it does.

| Name | Count | What it is | Expected |
|---|---|---|---|
| `lab-a-detached-*` | 8 | Detached, 1 to 5 GiB, types `sf-sim`, `sf-gold`, `sf-bulk` | Class A: moved |
| `lab-a-image-*` | 3 | Bootable volumes made from the cirros image, so they hold real data | Class A: moved |
| `lab-a-attached-*` | 4 | Attached to running servers (`lab-srv-*`) | Class A: instance locked around the move |
| `lab-boot-*` | 2 | Boot volumes of running servers (`lab-srv-boot-*`) | Class A: moved with the instance running |
| `lab-b-snap-*` | 3 | One to two snapshots each | Class C with API-only access: the API cannot show snapshot history, so rotation is unknown (see below); held back with cause `no_data_mover` |
| `lab-d-stopped-*` | 2 | Attached to stopped servers (`lab-srv-off-*`) | Class D: held back |
| `lab-e-multiattach` | 1 | Multi-attach type | Class E: held back |
| `lab-e-encrypted` | 1 | LUKS-encrypted type (the lab's fixed key manager) | Class E: held back |
| `lab-t-on-target-*` | 2 | Already on `pure-sim` | Not admitted, cause `already_on_target` |
| `image-<image id>` | 1 | Not seeded: DevStack's image-volume cache creates it when a volume is made from an image | Class E, cause `image_cache` |

Class B needs the history of deleted snapshots, which only the Cinder database holds. With the APIs only, any volume with snapshots is class C ("rotation unknown"), as the classifier's own rule says. To exercise the class B path in the lab, import a playbook export that carries the churn figures. What the real lab showed beyond the mock: the image-cache volume, and that the "on target" volumes were admitted until the pre-flight learned to leave them out.

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
| Class B needs the database | With the APIs only, a volume with snapshots is class C (rotation unknown). The class B path (wait for rotation) is covered by the mock tests only. |

Not yet exercised in the lab: failure injection (a refused lock, a stopped service during a move, a failed retype), the halt button during a real move, a retype of a volume with a snapshot, and the Horizon auto-login against the real dashboard.

## A first run, in order

1. `OpenMove-Lab.bat seed` once, then `OpenMove-Lab.bat`.
2. Platform, Inventory, Audit: read-only, as against any cloud.
3. Pre-flight: choose `sf-sim` as the source and `pure-sim` as the target, access level "OpenStack APIs only", run it, then press "Lab: validate, attest and sign the gate".
4. Move: press **Dry run** and read it. Then **Start the run**, sign G2 and G3 as a second person (a name that is not "Lab operator"), release, and watch. After the post-check, sign G4. Try **Reverse** on one volume.
5. Look at the evidence folder (`C:\OpenMoveLab\openmove-home\evidence\<volume id>\`).

## Setting the lab up the first time

See `tools/lab/README.md`. In short: an Ubuntu 24.04 WSL image is imported as the distro `openmove-lab`, and `tools/lab/provision_devstack.sh` installs DevStack with two LVM back-ends. It takes about 40 minutes and several gigabytes of downloads (Ubuntu packages, Python wheels, OpenStack source from opendev.org).
