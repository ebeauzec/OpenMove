# Building the migration engine: plan for a test environment

Status: **in progress**. Built and tested against a mock cloud: the access level, the retype order, E1 (journal, permit, evidence; the shell executor is not needed for the APIs-only level and comes with E5), E3 for lock, unlock and retype, and E4 (class A and B). Not built: E2, E5, E6, E7. This page is the plan. It says what will be built, in what order, what can be proven without a cloud, and what only a lab can prove. Nothing here has run against a real cloud.

## What "works" can mean

OpenMove can be tested in two ways:

* **With fakes** (a mock cloud, a mock Element array, a scripted shell). This proves the logic, the order, the refusals and the evidence. It cannot prove that a Mirantis Queens build, a SolidFire or a FlashArray behaves as the playbook says.
* **In the lab** (playbook Phase 0, tests T1 to T21). This is the only proof that it works. The engine ships with the tests as guided procedures so that the lab run produces the evidence the gate G0 needs.

The first run in a test environment is a **dry run**: every step that would change the cloud is printed and recorded, and none is executed. A real run is allowed only after the dry run, the lab validation and the gate.

## Milestones

| # | Milestone | Proven with fakes | Needs the lab |
|---|---|---|---|
| E1 | **Foundation.** One way to run a command locally or over SSH (the system OpenSSH client, key or agent only, no password stored), dry run by default, every changing call behind a permit, halt honoured, every call and its output recorded in an evidence folder per volume, an inverse recorded for every change. | yes | no |
| E2 | **Array adapters.** SolidFire Element JSON-RPC (clone, list snapshots, QoS, delete). FlashArray over the array's command line, as the playbook does (volumes, snapshots, copy, rename, connect). | yes | yes (T18, T19) |
| E3 | **Nova and Cinder actions.** Lock and unlock, retype with on-demand migration, the Nova volume swap, attachments, polling of `migration_status` and of the block job; the destination-record script and the migration completion call run on a Cinder host (playbook Appendix B). | yes | yes (T1, T5, T7, T12, T13, T15) |
| E4 | **Class A and class B.** The standard path and the rotation watcher. | yes | yes (T1, T12) |
| E5 | **Class C.** Clones of every snapshot, the migration host logs in, chain copy with byte-for-byte compare (playbook Appendix D), destination record, reconcile loop, freeze, cutover (attached and detached), post-check, safety snapshot, cleanup, rollbacks R1 to R3, recovery 18.1 to 18.3. | yes | yes (T2 to T4, T6 to T10, T14 to T17, T20, T21) |
| E6 | **Lab tests T1 to T21** as guided, recorded procedures against a test project, producing the evidence for gate G0. | partly | yes |
| E7 | **Remaining gaps:** image-cache volumes (A7), `provider_id` repair, the batch plan per compute host, the per-batch cleanup 5.12.4 and 5.12.5. | yes | partly |

E1 to E4 give a class A and class B path that can be tried in a test environment first. E5 is the larger piece.

## Two levels of access

The engine must work when OpenMove has **only the OpenStack APIs** (no migration host, no array access, no SSH). The level is chosen on the Pre-flight tab and recorded with the pre-flight result.

| Level | Reaches | Moves | Held back |
|---|---|---|---|
| `api` (default) | Keystone, Cinder, Nova | Class A and B, by Cinder retype with on-demand migration | Class C, cause `no_data_mover`, with steps to resolve it |
| `full` | also SSH hosts and both arrays | Class A, B and C | none for this reason |

Under `api`, the checks P12, P14 and P15 (source array account, target namespace, clone call) are informational. E1, E3 and E4 need only the `api` level; E2 and E5 need `full`.

## Where it runs

* The **controller** (the OpenMove program, on Windows, macOS or Linux) talks to OpenStack over HTTPS, to the arrays over HTTPS or SSH, and to the Linux hosts over SSH.
* A **migration host** (Linux, with `open-iscsi`, `multipath-tools`, Python 3 and the OpenStack clients) reaches both arrays. Class C needs it. Playbook 2.6.
* A **Cinder host** (with the Cinder code and `cinder.conf`) runs the destination-record script. Playbook Appendix B.
* Nothing is installed on the hosts. Helpers are copied over SSH for the run and removed after (requirement R12).

## What the test environment must give OpenMove

1. OpenStack: the Keystone address and an administrator account (Queens). For class A only, this is enough to start.
2. Two back-ends in Cinder (the source and the target), and a test project with volumes of each class, including volumes with snapshots and an attached boot volume.
3. For class C: SSH key access to a migration host and to a Cinder host; the SolidFire management address and a dedicated migration account; the FlashArray management address and a user for the command line.
4. A rollback agreement: which volumes may be destroyed in the test, and who signs the gates.

## Decision needed before E4

The playbook moves a class A volume with a **standard retype**: Cinder asks Nova, and Nova copies the data with a live block copy and switches the guest to the new device by itself. OpenMove cannot pause that switch to run two rechecks first. The order in `lifecycle.py` (copy, keep in sync, recheck twice, cutover) fits the staged method, where OpenMove controls the copy.

Decided by the owner ("use retype"): class A and B use the playbook's retype and a shorter, enforced order (precheck, validate, G2 and G3, retype as the cutover, post-check, G4, soak). The "recheck twice" rule stays for the staged method. See the question put to the owner.
