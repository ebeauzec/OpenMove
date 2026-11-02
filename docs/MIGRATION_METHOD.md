# Migration method

The method OpenMove automates, taken from the owner's playbook (version 1.2) and written without customer detail. The playbook stays the authority; this document is the working summary the design and the reports are built from. The first back-end pair is NetApp SolidFire to Pure Storage FlashArray on an OpenStack Queens cloud; other pairs reuse the same structure (see `DESIGN.md`, "Back-end adapters").

## 1. The problem

Cinder's normal tool for moving a volume between back-ends, a retype with on-demand migration (or `cinder migrate`), refuses any volume that has snapshots. Many tenant volumes carry snapshots that tenant processes depend on, and the operator cannot ask tenants to delete them, stop their workloads, or accept new volume or snapshot IDs.

## 2. Ground rules

Every step is written against these. A step that cannot meet them stops and leaves the volume exactly as it was.

1. No customer involvement.
2. No downtime: instances keep running and volumes stay attached.
3. No ID changes: volume UUID, snapshot UUIDs, name, description, metadata, project and quota accounting are unchanged.
4. No data-loss window: the source copy is untouched and authoritative until the cutover; after it, clones and a safety snapshot are kept until sign-off.
5. Evidence for every volume.
6. One volume, one operator, one checklist. Parallel across volumes, never within one.
7. No tenant-visible change, including edits a tenant makes during the move.
8. A quiet platform: no live migration, evacuation, host reboot or service restart on a compute host that carries a cutover.

## 3. How the method works

1. **History rebuild.** Each source snapshot is cloned (thin, independent, read-only, in a dedicated migration account) and copied in chronological order onto a staging volume on the target array. After each copy, a target snapshot is taken with the name the target driver expects for that Cinder snapshot. The staging volume ends up carrying the whole chain.
2. **Destination record.** A Cinder volume record for the staging volume is created through Cinder's own object layer, the way Cinder creates the temporary destination of a standard migration: every field of the source copied (including `created_at`, `snapshot_id`, `source_volid`), host set to the target back-end, target volume type, status `available`, `migration_status` `target:<source id>`. The staging volume is renamed to the name the target driver derives for that record.
3. **Cutover.** The source record is frozen (`migration_status` `migrating`). For an attached volume, Nova's volume swap runs a hypervisor block copy under the running instance and calls Cinder's migration completion. For a detached volume, a final clone is copied and the completion is called directly.
4. **Completion.** Cinder swaps the two records. The original UUID keeps its identity, gains the destination as its name id, takes the target volume type, keeps its attachments, and its snapshot rows resolve by name to the staged snapshots. The old record is deleted on the source back-end.
5. **Field sync.** Until completion, fields a tenant can still change (name, description, bootable, metadata) are copied from the tenant's record to the destination record, so a tenant edit is not overwritten by the swap.

Nothing in Cinder or Nova is patched. Everything from the cutover on is the standard retype code path.

## 4. Volume classes

| Class | Definition | Moved by |
|---|---|---|
| A | No snapshots. | Standard retype or migrate (Phase 3). |
| B | Snapshots that rotate away within an agreed window. | Rotation watcher fires the standard path when the snapshot count reaches zero (Phase 4). |
| C | Snapshots that do not rotate, or a mix. | Staged migration (Phase 5). |
| D | Class A or C attached to an instance that is not `ACTIVE` or `PAUSED`. | Phase 5, detached variant, after an admin lock and detach. A root volume of a boot-from-volume instance waits until the instance runs. |
| E | Encrypted, multiattach, in a group, replicated, wrong status, or a malformed `provider_id`. | Not migrated by this method; listed separately for a follow-up plan. |

## 5. Phases

| Phase | Content |
|---|---|
| 0 | Lab validation of the full procedure (21 tests, T1 to T21). Gate G0. |
| 1 | Discovery and triage: inventory, snapshot churn, attachments, classification, batch plan, `provider_id` repair. |
| 2 | Preparation: target back-end and volume types, reference volume, compute connectivity, migration host, database backup, migration account, clone QoS, decisions. Gate G1. |
| 3 | Class A: standard retype or migrate, with evidence. |
| 4 | Class B: rotation watcher. |
| 5 | Class C: staged migration (5.1 pre-flight to 5.12 cleanup after the soak period). |

## 6. Gates and reversibility

| Gate | Where | Reversible | Rollback |
|---|---|---|---|
| G0 | End of lab validation. | n/a | Do not start production. |
| G1 | End of preparation. | Yes | Remove the test volume. |
| G2 | Staging volume built and verified: every image compared, snapshot names match, chain reconciled. | Yes, no trace | R1 |
| G3 | Destination record exists, source frozen, rollback clone taken, instance locked. | Yes, no trace | R2 |
| G4 | Cutover complete and validated. | Reverse migration only | R3 |

A gate is signed by a person other than the operator who ran the steps. If a gate check fails, the volume goes back to the previous gate and the batch pauses; nothing is fixed live.

## 7. Rollback

| Rollback | When | What it does |
|---|---|---|
| R1 | After G2, before the destination record exists. | Destroy the staging volume and, if the volume will not be retried, the source clones. |
| R2 | After G3, before the cutover starts, or after a failed swap. | Restore the source record's status; delete the destination record through `cinder-manage` leaving `migration_status` as `target:<id>` (so no quota is released); optionally recover the destroyed staging volume and its snapshots inside the array's pending-delete window; unlock the instance. |
| R3 | After G4. | Reverse migration with the roles swapped: Cinder's own create flow builds the destination on the original back-end, the history is rebuilt from the target's snapshots, and the same freeze and swap run. The live volume is the data source, so tenant writes since the cutover are kept. The recovery points (rollback clone, safety snapshot) are used only if the live volume is suspect, and using one accepts data loss back to its point in time. |

## 8. Pre-migration and migration checks

The check catalogue. Each check is a record with an ID, what it looked at, the evidence, the result and what it means. The reports (R7) list every check that ran.

### Platform (once per cloud and back-end pair)

| ID | Check | Source |
|---|---|---|
| P1 | OpenStack release detected (R9); Cinder, Nova, libvirt, QEMU and driver versions recorded; release profile selected and its status (validated or untested) shown. An untested release is read only. | API discovery, hosts |
| P2 | Source and target back-ends enabled and their services up; pools reported. | Cinder |
| P3 | Target driver variant and array API version supported. | Cinder host, array |
| P4 | Target volume type has no replication or other feature that changes completion. | Cinder |
| P5 | Volume and snapshot name templates and the source driver prefix are the defaults the method assumes. | Cinder configuration |
| P6 | Target setting that keeps deleted volumes recoverable is on (eradicate-on-delete off). | Cinder configuration |
| P7 | Every compute host reaches the target array and has working multipath; a test volume attached and detached on each. | Compute hosts |
| P8 | Migration host reaches both arrays; copy throughput measured in both directions. | Migration host |
| P9 | Admin access to each component proven with a read-only probe. | All |
| P10 | Database backup taken and restore proven. | Cinder database |
| P11 | Volume-type strategy decided and recorded; type access and quota set for each project. | Cinder |
| P12 | Dedicated migration account on the source array; clone QoS chosen and within the agreed ceiling. | Source array |
| P13 | Availability zones of the two back-ends align. | Cinder |
| P14 | Target objects are in the array root namespace (no pods). | Target array |
| P15 | Source array API version supported and accepts the clone call with a new account. | Source array |
| P16 | Lab validation (G0) complete and signed. | Records |

### Per volume (eligibility and classification)

| ID | Check |
|---|---|
| V1 | Status is `available` or `in-use`; `migration_status` is none, `success` or `error`. |
| V2 | Not encrypted, not multiattach, not in a group or consistency group, replication none or disabled. |
| V3 | `provider_id` is well formed (volume id, account id, cluster id). |
| V4 | Volume has a volume type. |
| V5 | Snapshot count, age of oldest and newest, churn over 60 days; class assigned with a reason. |
| V6 | Every snapshot is `available` (none creating, deleting or in error). |
| V7 | Each Cinder snapshot maps to exactly one source-array snapshot; sizes recorded; volumes extended after a snapshot detected; snapshot count within the source array's per-volume limit. |
| V8 | Attachment: instance state `ACTIVE` or `PAUSED`, `task_state` none, no live migration (an error from the lookup is a stop), lock state, boot-from-volume flag. |
| V9 | Capacity on the target pool; quota effect on the project. |
| V10 | Drift: volumes created since the inventory are found and classified. |

### Staging (class C)

| ID | Check |
|---|---|
| S1 | Staging volume byte size equals the source size. |
| S2 | Each clone's size equals its snapshot's size; sizes never shrink along the chain. |
| S3 | Every image compares identical to the staging volume, and the tail beyond a smaller image reads as zero. |
| S4 | Target snapshot IDs equal the Cinder snapshot IDs (gate G2). |
| S5 | Reconciliation shows no new or deleted tenant snapshots since the last pass. |

### Cutover readiness

| ID | Check |
|---|---|
| C1 | Destination record equals the source record on every tenant-visible field (gate G3). |
| C2 | Array volume name equals the name the target driver derives for the destination record. |
| C3 | Rollback clone exists. |
| C4 | Instance is locked (attached case). |
| C5 | Both cinder-volume services are up. |
| C6 | Domain definitions captured before the swap. |
| C7 | No tenant snapshot raced the freeze. |

### Post-cutover validation (gate G4)

| ID | Check |
|---|---|
| X1 | Host, volume type, `migration_status`, name id and status are as expected. |
| X2 | Attachment intact and connected to the expected array volume only. |
| X3 | Snapshot chain on the array matches Cinder; a snapshot the tenant deleted after the freeze is expected and is destroyed; anything else fails the gate. |
| X4 | Instance health: disk source on the new device, no block job, no guest I/O errors, all multipath paths active, persistent domain definition points at the new device. |
| X5 | Create-from-snapshot test as admin succeeds on the target. |
| X6 | Tenant-visible fields and snapshot list identical to the before capture. |
| X7 | Safety snapshot taken on the target volume. |
| X8 | After the soak period: source volume gone, source clones removed, quota counters reconciled. |

## 9. Evidence per volume

Every volume gets a record with: the before state (database rows, nova view, array listings, sizes, classification, batch, operator, second pair of eyes), the clones, the staging copy log and compare results, the destination record and freeze rows, the cutover progress and logs, the after state and comparison, gate sign-offs with timestamps, and the cleanup log. In OpenMove this is the job record; the before and after reports are built from it.

## 10. Failure modes the engine must handle

Compare mismatch; chain mismatch; record-creation refusal; swap refused (instance state, size, zone); swap accepted but no block job; stalled block job; completion error with the guest already on the new array; compute service restart mid-copy; compute host failure mid-copy; cinder-volume service down at completion; source volume left behind (bad `provider_id`); quota counters wrong after rollback; tenant snapshot activity during staging or after the freeze. The playbook gives the response for each (its sections 14 and 18); the engine implements those responses and, where the answer is a human decision, stops and says so.
