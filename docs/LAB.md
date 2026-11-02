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
| `OpenMove-Lab.bat` | Starts the lab if it is not answering, then OpenMove in lab mode (it connects by itself). |
| `OpenMove-Lab.bat up` | Starts the lab: keeps the distro running, loads the kernel modules, re-attaches the loop devices behind the two volume groups, starts the services. |
| `OpenMove-Lab.bat seed` | Creates the test objects (all named `lab-*`): four detached volumes, two attached to running servers, one boot-from-volume, one with a snapshot (class B), one on a stopped server (class D, held back). |
| `OpenMove-Lab.bat clean` / `list` | Removes or lists them. |
| `OpenMove-Lab.bat status` | Says whether Keystone answers. |
| `OpenMove-Lab.bat down` | Stops the distro. Its data is kept. |
| `OpenMove-Lab.bat reset` | Lab mode with an empty journal and evidence folder. |

## A first run, in order

1. `OpenMove-Lab.bat seed` once, then `OpenMove-Lab.bat`.
2. Platform, Inventory, Audit: read-only, as against any cloud.
3. Pre-flight: choose `sf-sim` as the source and `pure-sim` as the target, access level "OpenStack APIs only", run it, then press "Lab: validate, attest and sign the gate".
4. Move: press **Dry run** and read it. Then **Start the run**, sign G2 and G3 as a second person (a name that is not "Lab operator"), release, and watch. After the post-check, sign G4. Try **Reverse** on one volume.
5. Look at the evidence folder (`C:\OpenMoveLab\openmove-home\evidence\<volume id>\`).

## Setting the lab up the first time

See `tools/lab/README.md`. In short: an Ubuntu 24.04 WSL image is imported as the distro `openmove-lab`, and `tools/lab/provision_devstack.sh` installs DevStack with two LVM back-ends. It takes about 40 minutes and several gigabytes of downloads (Ubuntu packages, Python wheels, OpenStack source from opendev.org).
