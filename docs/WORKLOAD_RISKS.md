# Workloads that may fail this migration, and how OpenMove finds them

The rule: **a volume that hosts something that may break is not moved.** This page lists what can break, how sure we are, whether OpenMove can see it, and what it does about it. Requirement R17 and check V11 are built on it.

Read the limits first. They apply to every row below.

* OpenMove reads the cloud API. It cannot look inside a guest. It cannot see if the disk drivers are installed, if the file system is clean, if a database is in the middle of a transaction, or if a cluster holds a disk reservation.
* So the check works from **hints**: image properties, instance metadata, names, size and the volume's history. A volume with no hint is **not** declared safe. It is only "nothing known".
* What the customer knows, and the cloud does not show, goes in as a **mark** (below). A marked volume is blocked like any other high-risk volume.
* The basis column is honest. "Playbook" means the owner's runbook v1.2 says it. "Upstream" means it comes from OpenStack, QEMU or libvirt documentation or code that was read during the research. "Inference" means engineering reasoning that has **not** been proven on this cloud. Every inference must be tested in the lab (playbook Phase 0) before anyone relies on it.

## How the method moves a volume (why these workloads are at risk)

On Queens the live move of an attached volume is a Nova **swap volume**: QEMU copies the disk to the new device while the guest runs, then switches the guest to the new device (block copy and pivot). Cinder then completes the migration and the volume keeps its ID. For a volume with snapshots, the history is rebuilt on the target first (the staged method, playbook Phase 5). Anything that makes the copy not converge, the switch not clean, or the guest not accept a changed disk can fail the move or the workload.

## The catalogue

| # | Risk | Mechanism | Basis | Can OpenMove see it? | What OpenMove does | Mitigation |
|---|---|---|---|---|---|---|
| 1 | Multiattach volume | Block copy and swap work for one guest. More than one writer cannot be switched together. | Playbook (class E) and upstream | Yes: Cinder `multiattach` flag (V2) | **Blocked.** Class E, refused at precheck. | Not moved by this method. Plan it apart. |
| 2 | Encrypted volume | The copy path does not handle the key and the encrypted layer the same way on the new device. | Playbook (class E) and upstream | Yes: `encrypted` flag (V2) | **Blocked.** Class E. | Not moved by this method. Plan it apart. |
| 3 | Instance not `ACTIVE` or `PAUSED` (stopped, suspended, soft-deleted, error, migrating) | Nova swap volume needs a running or paused guest. | Playbook (class D, 5.9c) | Yes: server status and task state (V8) | **Held.** Class D. Never enters the order. | Start the instance, or follow the detached variant with a recorded decision. Classify again. |
| 4 | Volume with snapshots | A plain retype loses the snapshot chain. The history must be rebuilt, image by image. | Playbook (classes B, C) | Yes: snapshot count and sizes (V6, V7, V1) | Class B waits for rotation. Class C needs the staged rebuild and the chain checks. **The rebuild engine is not built yet.** | Lab tests T2, T3, T9, T21 before any production use. |
| 5 | Volume created from a snapshot or another volume | On some arrays a clone depends on its parent. A move can be refused or can break the link. | Inference | Yes: `snapshot_id` and `source_volid` | **Flagged** (medium). Shown with advice. Not blocked. | Test one in the lab. Ask the array owner about clone dependencies. |
| 6 | Write-heavy workload that never converges | The block copy keeps getting new writes. QEMU reports the job is not ready to switch, so the pivot never happens or the window is very long. | Upstream and inference | **Partly, at run time only.** Not from the API before the copy. | The recheck rules see lag and a changing source (`never_stable`, `snapshot_appeared`). The volume stops and is reported. | Move in a quiet window. Lower the rate cap. Ask the owner to pause the writer. |
| 7 | Legacy Windows guest (2000, 2003, 2008, XP, Vista, 7, 2012) | Old guests often lack current disk drivers, cannot be quiesced and may not find the disk after the swap. | Inference, widely reported | **Partly:** image `os_type`, `os_version`, `image_name` | **Blocked** (high) until two people clear it. | Lab restore of a copy with the same Windows version. Check drivers. Console open during the cutover. |
| 8 | Windows guest without the QEMU guest agent | No VSS freeze. The cutover is crash-consistent only. | Upstream (agent) and inference | **Partly:** `hw_qemu_guest_agent` image property | **Blocked** (high) until cleared. | Install and enable the agent and the VSS provider. Or accept crash-consistency in writing. |
| 9 | Windows guest on an emulated disk bus (IDE, SATA) | After the swap the guest may not find its disk. | Inference | **Partly:** `hw_disk_bus` image property | **Blocked** (high) until cleared. | Test the swap on a clone. Move the guest to a virtio bus first, if the owner allows. |
| 10 | Any other Windows guest | File system and driver state cannot be seen from the cloud. | Inference | **Partly:** image properties | **Flagged** (medium). | Separate batch, quiet hours, owner informed. |
| 11 | Boot-from-volume instance | If the root volume does not come back, the server is down. The swap on a root volume must be proven on this Nova build. | Playbook (test T4) | Yes: root volume list (V8) | **Flagged** (medium); a root volume of a risky guest is high. | Own batch. Tested rollback. Lab test T4. |
| 12 | Clustered or shared-disk workload (SCSI reservations, quorum disks, failover clusters) | The workload holds a reservation or a lock on the disk. A copy and a switch of the device breaks it. | **Research gap.** Nothing found that proves it either way. | **No.** The cloud does not show it. | Only by **mark**. | Ask the owner. If a cluster uses the disk, do not move it without the cluster vendor's method. |
| 13 | Libvirt or compute-service restart during the copy; stuck block job | An orphan block job or a half-defined domain can be left behind. | Playbook (18.1, test T14) and upstream | **No, until it happens.** | The halt switch and the instance-health check (X4). The volume stops and is reported. | Playbook 18.1 recovery. Do not restart services during a batch. |
| 14 | Stale domain definition after the swap | The persistent domain XML can still point to the old device. A later restart or live migration fails. | Playbook (15, X4) and Nova bug 1713857 | After the swap only (X4, C6) | Post-check fails; the volume is rolled back. | Lab test T13 (reboot, shutdown/start, live migration after a swap). |
| 15 | Very large volume | A long copy. A long time for the source to change. | Inference | Yes: size (over 1024 GiB and attached) | **Flagged** (medium). | Quiet window. Watch the rechecks. |
| 16 | Latency-sensitive database | The copy adds I/O load. The switch pauses I/O for a moment. | Inference | **Partly:** a guess from names (oracle, postgres, mssql, mongo, kafka, db, ...) | **Flagged** (medium, "a guess from the name"). | Low rate cap, quiet window, watch latency. Mark it if the owner confirms. |
| 17 | Disk serial or identifier change | A guest that mounts by device path, serial or a disk signature may not find its disk. | **Unconfirmed.** Inference only. | **No.** | Only by **mark**. | Lab test T13 and T3 with a guest that mounts by serial. Prefer UUID mounts. |
| 18 | Source changed during the staged rebuild (snapshots created or deleted by the tenant) | The rebuilt chain no longer matches. | Playbook (5.7, tests T9, T10) | At run time (rechecks, S4, X3) | Rechecks must reconcile; a mismatch stops the volume (`chain_mismatch`). | Playbook 5.7. |

## How OpenMove identifies them

1. **Inventory.** The inventory reads, for each volume: image properties (`os_type`, `os_distro`, `os_version`, `image_name`, `hw_disk_bus`, `hw_scsi_model`, `hw_qemu_guest_agent`, firmware and machine type, architecture), `snapshot_id`, `source_volid`, and for each server its name, metadata and flavor. Nothing else from the image is kept.
2. **Check V11 (workload risk).** One verdict per volume: no known risk (pass), medium (info), high and not cleared (**fail**: not admitted), high and cleared (warn).
3. **Admission.** A failed check removes the volume from the admitted list. It cannot be selected. The not-migrated report names the cause `risky_workload` and quotes the reasons and the proposed steps.
4. **Run time.** The class rules, the recheck rules, the post-check and the halt switch catch what cannot be seen before: a copy that never converges, a source that keeps changing, an instance that is unhealthy after the swap.

## Marks: what the customer knows

A mark is a person's own statement that a volume hosts something risky. It needs a name and a reason of at least 8 characters, for example "legacy billing application, owner Sam, clustered disk". A mark turns the volume into a high-risk volume. It is the only way to cover rows 12 and 17, and any application the cloud cannot show.

* Mark one volume, or a whole group (a project, an instance, a class) on the **Select** tab.
* A mark is kept in the journal with who and when. A mark is removed with a withdrawal, also in the journal.

## Clearance: the only way a high-risk volume moves

A high-risk volume is moved only after **two different people** record a clearance for it, one as operator and one as reviewer. Each gives a name and evidence of at least 20 characters: what was tested, what the owner agreed, what the way back is.

* The clearance is bound to the risk as it was assessed. If the reasons change (for example a new mark), the clearance no longer applies and it must be given again.
* A clearance causes a new pre-flight run. The gate needs both sign-offs again.
* A cleared volume is **not** in the default selection. It can be selected on purpose.
* A cleared volume is **always held** for a person's release before the cutover, whatever the settings say, and it is watched. If its post-check fails it is rolled back.
* A clearance can be withdrawn. The volume is blocked again.

## What to ask the customer before the audit

Send these questions with the first report. Every "yes" is a mark or a reason to plan apart.

1. Which servers run Windows? Which version? Is the QEMU guest agent installed? Is the disk on a virtio bus?
2. Which servers are older than the current vendor support (for example Windows 2008/2012, old Linux)?
3. Which volumes hold a database or a message queue? What is its latency limit? Can it be paused?
4. Which servers form a cluster, share a disk, use SCSI reservations or a quorum disk?
5. Which servers boot from a volume? Which of them cannot be down for more than a few minutes?
6. Which applications mount disks by device path, serial or disk signature instead of UUID or label?
7. Which volumes were created from a snapshot or a clone and are expected to stay linked to it?
8. Which volumes are written all the time (logs, ingest, backup targets)?
9. Which owners must agree to a window, and who is at the console?

## What OpenMove cannot identify

* Whether a guest has the right drivers, or a clean file system.
* Whether an application is mid-transaction, or has an open file lock.
* Whether a cluster holds a reservation on the disk.
* Anything an image or an instance does not record. A Linux or Windows server built from a custom image with no properties looks the same as any other.
* Whether the cloud's own builds behave as the upstream code does. This is why the lab validation (Phase 0, gate G0) is required.

The report says this on every run. "No known risk" means exactly that and no more.

## Sources and honesty about them

Reviewed during the research, for the mechanism of the swap and its limits: the OpenStack Nova administration guide on managing volumes (swap volume, Queens and later), the Cinder contributor documentation on volume migration and retype, the Cinder and Nova multiattach documentation and specs, the Pure Storage driver documentation, Red Hat OpenStack Platform storage documentation, and OpenStack bug reports on swap volume and block copy. Some findings come from code reading and bug reports rather than a guide, and the Mirantis builds may differ from upstream. Rows marked "Inference" or "Research gap" have no source. Treat them as questions for the lab, not as facts.
