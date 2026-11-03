# Playbook v1.2 and v1.3: what OpenMove covers, step by step

The question: does OpenMove contain every step of the owner's playbook (runbook v1.2, and v1.3 which added the second review round)? **No.** This page shows, for each phase and step, what exists today. It does not round up. The step numbers are the same in both versions; the changes in v1.3 are listed in their own section below.

The playbook is the authority. `MIGRATION_METHOD.md` is the working summary. This page is the audit of the tool against it.

## Status words

| Word | Meaning |
|---|---|
| **Built** | The logic exists in OpenMove and is tested against the mock cloud and the demo. It has not run on a customer cloud. |
| **Built, lab-proven** | As Built, and it has also run against the OpenStack lab on a development machine (a real Keystone, Nova and Cinder; the storage back-ends are LVM simulations, not SolidFire or FlashArray). See `LAB.md`. |
| **Check only** | A check for it is in the catalogue. Where OpenMove has no access to the component (database, host, array), the check shows "not checked", and a named person must attest it. Nothing is verified automatically. |
| **Different** | OpenMove does it in another way, on purpose. The difference is stated. |
| **Not built** | No code. The step is only described in the documents. |

## The short answer

| Layer | State |
|---|---|
| Method, classes A to E, check catalogue, gates, rollback rules, failure modes (documents) | written |
| Discovery, inventory, classification, pre-flight checks, reports, the rules that order a migration, the safety layer, the gated GUI wizard | **built** (mock cloud and demo; the read paths and the gates also run against the lab) |
| Phase 0: the 21 lab tests T1 to T21 as guided, recorded procedures | **not built** (only the record that gate G0 passed is kept) |
| Phase 3 (retype of class A and B) and rollback R3 as actions that change the cloud | **built, lab-proven** (`openmove/engine`): detached, attached and boot volumes, the way back, and the failures listed in `LAB.md`. Never run on a customer cloud. |
| Phase 4 (class B) as the engine's wait for snapshots to rotate away | **built** against the mock cloud; the lab has no rotating snapshots yet |
| Phase 5 (class C) and the rollbacks R1, R2 as actions that change the cloud | **not built**. The demo simulates them. Class C needs a data mover and the arrays. |
| Per-volume gate sign-offs G2, G3, G4 by a second person | built (see "Gaps that matter") |
| Anything run against a customer cloud or a real array | **never done** |

## Phase 0: lab validation (gate G0)

| Playbook | OpenMove | Status |
|---|---|---|
| Tests T1 to T22 (T22 is new in v1.3: the instance lock is read and respected) | No guided procedures. A validation result for a release can be recorded by two different people with their evidence (`profiles.py`, `POST /api/validation`), and check P16 blocks every changing step until it is. The engine itself covers the T22 rule: it reads the lock with microversion 2.9, locks only an unlocked instance and unlocks only what it locked. The retype path was also exercised by hand in the lab (see "What the lab showed"). | **Not built** (record only) |
| Gate G0, second person signs | The pre-flight gate takes two sign-offs. G0 itself is the P16 check. | **Check only** |

## Phase 1: discovery and triage

| Step | What the playbook does | OpenMove | Status |
|---|---|---|---|
| 1.1 | Inventory query A1 on the Cinder database | Inventory from the Cinder and Nova APIs. A database export (playbook A1, A2, A4) can be imported. | **Different** (API first, no database access) |
| 1.2 | Snapshot churn A2 | From the API snapshot data, and from the imported export | **Built** (partly: needs the export for soft-deleted history) |
| 1.3 | Attachments A3, instance states | Inventory and server data (V8) | **Built** |
| 1.4 | Classify A to E | `classification.py` | **Built** |
| 1.5 | Batch plan by class, compute host, project | Groups by class, project, attachment, size, snapshots, host, instance, risk. Concurrency setting. No generated batch-plan document that spreads load per compute host. | **Built** (partly) |
| 1.6 | `classify.py` | Replaced by `classification.py` | **Built** |
| 1.7 | A7: image-cache and internal tenant volumes, decided per volume | The image-cache volume is recognised (read-only, named `image-<image id>`) and held back as class E, cause `image_cache`. Other internal-tenant volumes are not looked for. | **Partly built** (found in the lab) |
| 1.8 | A8: drift before every batch | V10 | **Built** |
| 1.9 | Repair `provider_id` | V3 finds a bad one (class E, cause `bad_provider_id`). The repair is not done. | **Check only** (no repair) |

## Phase 2: preparation (gate G1)

| Step | What the playbook does | OpenMove | Status |
|---|---|---|---|
| 2.1 | Add the FlashArray backend to `cinder.conf` | P2 reads the services and pools from the API. OpenMove changes no configuration. | **Check only** |
| 2.2 | Create the FlashArray volume type | P4, P11 | **Check only** |
| 2.3 | Reference test volume on the target | Part of the G1 check (attach and detach on every compute) | **Check only** |
| 2.4 | Compute hosts reach the array, multipath | P7 | **Check only** |
| 2.5 | Libvirt can do a block copy to a block device | P7 | **Check only** |
| 2.6 | Build the migration host | P8 (readiness per host). The tool does not build it. | **Check only** |
| 2.7 | Test the copy path both ways, measure MB/s | P8 | **Check only** |
| 2.8 | Database backup, restore proven | P10 | **Check only** |
| 2.9 | The only hand writes are the ones the playbook names | The safety layer: read-only client, a `Permit` for each change, halt switch. The hazards and the safety rules are in `SAFETY.md`. | **Built** (rule), the writes themselves **not built** |
| 2.10 | Monitor logs during a batch | Live tab (journal, phase, progress). No log tail from the cloud. | **Built** (partly) |
| 2.11 | Dedicated source-array account for clones | P12 | **Check only** |
| 2.12 | Clone QoS | P12 | **Check only** |
| 2.13 | Python interpreter and the record script `--dry-run` | Nothing runs the record script | **Not built** |
| 2.14 | Operations notice and window rules | Not enforced and not generated. | **Not built** |
| 2.15 | Volume type strategy | P11 (the decision is recorded) | **Check only** |
| 2.16 | Project access to the type, per-type quota | P11, V9 (quota effect) | **Check only** |

## Phase 3: class A volumes

| Step | OpenMove | Status |
|---|---|---|
| 3.1 Before state | The inventory of each run is kept in the journal. The raw database rows of Appendix E need the export. | **Built** (partly) |
| 3.2 Lock the instance | `engine/actions.py`: locks only an instance that is not locked; refuses an instance whose lock state cannot be read | **Built, lab-proven** |
| 3.3 Retype with on-demand migration | One `os-retype` with `migration_policy: on-demand`; never retried; permit checked before it | **Built, lab-proven** |
| 3.4 Wait for success | Polls `migration_status` every 5 s up to 6 hours; an unknown outcome halts | **Built, lab-proven** |
| 3.5 Unlock, after state | Post-check against the before-state, then unlock (only what OpenMove locked), then soak | **Built, lab-proven** |

OpenMove adds, around these steps: validate (G2 and G3 by a second person), hold and release, the retype, the **post-check**, G4 by a second person and a **soak** period. The order is enforced in `lifecycle.py` (`RETYPE_ORDER`). The staged order (copy, sync, rechecks, cutover) belongs to class C and to the demonstration.

## Phase 4: class B volumes

| Step | OpenMove | Status |
|---|---|---|
| 4.1 Watcher: count snapshots once a minute, fire at zero | The engine rechecks the snapshot rule on every tick (5 s) and starts the volume when the count reaches zero; it gives the volume up after 72 hours. It only reads; it never deletes a snapshot. | **Built** (mock cloud; no rotating snapshots in the lab yet) |
| 4.2 The watcher never deletes a snapshot | Rule of the safety layer (the client is read-only; changing calls need a permit) | **Built** (rule) |
| 4.3 Concurrency cap per compute host | A global concurrency setting. Not per host. | **Built** (partly) |

## Phase 5: class C volumes

| Step | What the playbook does | OpenMove | Status |
|---|---|---|---|
| 5.1.1 | Confirm the class again | Precheck | **Built** |
| 5.1.2 | Instance state, no migration in flight | V8, class D | **Built** |
| 5.1.3 | Before state, Appendix E 1 to 6 | Report | **Built** (partly) |
| 5.1.4 | Sizes in bytes and GiB | S1 | **Check only** |
| 5.2 | Map Cinder snapshots to source-array snapshots | V7 | **Check only** |
| 5.3 | Clone each snapshot, log in on the migration host | Rules only (S2) | **Not built** |
| 5.4 | Create the staging volume | Rules only (S1) | **Not built** |
| 5.5 | Copy the chain, take named snapshots, compare | The copy rules are built (images compared, ids match). The copy is not. | **Not built** |
| 5.6 | Create the destination record (Appendix B), rename | C1, C2 | **Not built** |
| 5.7 | Reconcile the chain, repeat until the diff is empty | Rechecks must reconcile; X3 | **Built** (rule), the loop **not built** |
| 5.8 | Freeze: lock, rollback clone, record fields, field sync | C3, C4 | **Not built** |
| 5.9a | Attached: Nova swap, watch the block job | Simulated | **Not built** |
| 5.9b | Detached: final clone, completion call | Simulated | **Not built** |
| 5.9c | Class D, detach and re-attach | Class D is held | **Not built** |
| 5.10 | Post-cutover validation, G4 | X4 to X7 and the post-check rule | **Check only** |
| 5.11 | Unlock, file the after state | Simulated | **Not built** |
| 5.12 | Cleanup after the soak, per batch | Soak at least 1 day (default 7) is enforced before "done". The cleanup itself, the backend removal (5.12.4) and the quota check (5.12.5) are not built. | **Built** (soak rule), cleanup **not built** |

## Rollbacks and recovery

| Playbook | OpenMove | Status |
|---|---|---|
| R3 (the way back after G4) for class A and B | A retype back to the original type, with the instance locked first, a name and a reason, inside the soak | **Built, lab-proven** |
| R3 on a failed post-check or a silently undone retype | The engine rolls back by itself and records why | **Built, lab-proven** |
| R1, R2 | Class C only. A rolled-back state exists in the lifecycle and in the reports. No rollback action. | **Not built** |
| 18.1 stuck block job | Halt, X4. No recovery action. | **Not built** |
| 18.2 completion fails | Halt. No recovery action. | **Not built** |
| 18.3 compute host fails | Halt | **Not built** |
| Section 14 failure modes | Each one has a cause code, a description and proposed steps in the not-migrated report | **Built** (reporting) |

## Gates

| Gate | Playbook | OpenMove | Status |
|---|---|---|---|
| G0 | Lab validation, signed | P16 | **Check only** |
| G1 | End of Phase 2, signed | The pre-flight gate: all checks, attestations, **two people**, bound to the run | **Built** |
| G2 | Staging verified, second person signs, per volume | Per-volume sign-off by a second person, enforced before the cutover (`POST /api/volume-gate`). The checks S1 to S4 behind it are not run automatically. | **Built** (sign-off), checks **check only** |
| G3 | Record and freeze verified, second person signs, per volume | Per-volume sign-off, enforced before the cutover. In this release the freeze is inside the cutover phase, so G3 is signed on the readiness conditions; it moves between the freeze and the swap when the engine splits them. | **Built** (sign-off), checks **check only** |
| G4 | Cutover verified, second person signs, per volume | Per-volume sign-off, enforced before the soak starts. | **Built** (sign-off), checks **check only** |

## Review feedback on the playbook, and what it means here

A reviewer read the playbook and the script bundle after v1.2. Most of the findings are about the staged class C method and its scripts, which OpenMove does not run; they belong in the playbook and its bundle, not here. What touches OpenMove:

| Finding | Where it lands in OpenMove | Status |
|---|---|---|
| The instance lock is never read because the lock field only exists from Nova microversion 2.9 and a Queens-era client defaults to 2.1 (the field comes back empty and is tolerated). | The inventory read of instances sent no microversion, so the pre-flight could not see a lock. The engine read single instances with 2.9 but accepted an answer without the field. Now both send 2.9, and the engine refuses an instance whose lock state is not true or false (safety row 35). | **Fixed** |
| After a reverse migration the driver may delete the real target volume, which then sits in the array's pending-eradication window (recovery by purevol recover only inside it). | OpenMove's way back for classes A and B is a retype back, which the cloud does itself, so the engine adds no array call. The method text (R3) now carries the finding. For class C, which is not built, an explicit array copy before the record swap is to be designed and proven in the lab. | **Recorded** |
| The post-check should compare the fields that must not change, not everything (a rename or a metadata edit during the window must not fail it). | The engine's post-check compares status, size, attachments, bootable flag and instance state. It does not compare names, metadata or the tenant snapshot list. | **Already so** |
| A move of a detached volume should lock the volume, because reserving a volume in Queens does not check the migration status. | The retype path never uses a bare migrate. Observed in the lab: while a detached volume is retyped its status is `retyping`, not `available`, so it cannot be attached. Queens may differ; the post-check also compares the attachments. | **Observed in the lab; confirm on Queens in Phase 0** |
| The staged class C method and its scripts are a lot of new surface for a one-off migration; keep it out of the critical path. | Matches the current plan: access level "OpenStack APIs only" is the default, class C is held back with a cause, and nothing in the staged method is built. | **Owner decision** |

## What changed in v1.3

The second review round (v1.2 to v1.3) accepted the 13 answers of the first round, withdrew two of its own points, corrected rollback R3 and fixed two script defects. Each point was checked against the Queens source by the playbook's author. What each means for OpenMove:

| v1.3 item | In OpenMove | Status |
|---|---|---|
| R3: after a reverse migration Cinder destroys the real array volume, so R3 now takes a safety copy first | A retype back leaves the cloud to do it, and the engine adds no array call. A safety copy of a class C volume belongs to the unbuilt class C method | **Recorded**, class C not built |
| `--rpc-create` copied `snapshot_id` and `source_volid` into the record | A defect in a script of the staged method. OpenMove does not run it | **Not applicable** |
| The instance lock is never read (the Nova field exists from microversion 2.9; the Queens client defaults to 2.1) | The inventory and the engine both send microversion 2.9; an instance whose lock state is not true or false is refused; OpenMove unlocks only what it locked. New test T22 | **Fixed** (safety row 35) |
| The post-check fails on a tenant rename, a metadata edit or a snapshot delete | The post-check compares only the fields that must not change: status, size, attachments, bootable flag, instance state and, since 0.7.0, the identity fields (creation time, project, owner, snapshot id, source volume id, availability zone, multi-attach flag) | **Already so**, extended |
| `pure_connected` column order; `user_friendly_names no`; `70_cleanup.sh` bypassed the dry run; the unfreeze restored `NULL`; `MODE=migrate` on a detached volume needs `--lock-volume` | Scripts and host settings of the staged method. The host settings are items for the migration host check (P8) | **Not applicable** to the retype path |
| The compare costs two or three times the I/O | Staged method only | **Not applicable** |
| Keep OpenMove out of the critical path | A scoping choice. The playbook, its scripts and Phase 0 do not depend on OpenMove | **Owner decision** |

## What the lab showed (real Cinder and Nova, not the mock)

The lab (`LAB.md`) is a DevStack on a development machine with two LVM back-ends standing in for the source and the target array. It proves the OpenStack side of the engine. It says nothing about SolidFire, FlashArray or Queens. What it found:

| Finding | What was done | Where it is tested |
|---|---|---|
| A detached volume shows the status `retyping` while it is retyped | The mock does the same, and the engine and the post-check expect the status to return to normal | `test_engine` |
| Real Cinder **accepts** a retype of a volume that has a snapshot, and then silently puts it back (status normal, type unchanged, no error) | After a 30 second grace a volume that is back to normal and not on the target counts as an undone retype: the engine rolls back and records why. A mock fault mode reproduces it | `test_engine` (undone retype) |
| A retype call being accepted proves nothing about the snapshot rule | The snapshot rule stays a blocking check; class C is never retyped | `test_engine`, `test_core` |
| Instances stay SHUTOFF after the WSL distribution restarts, which turns their volumes into class D | Documented as a lab troubleshooting row | `LAB.md` |
| Nova hides `locked` below microversion 2.9 | As above | safety row 35 |
| Horizon and Keystone answer on `localhost` but not on `127.0.0.1` | The dashboard link and the Connect form use the lab's own host name | `test_lab` |

## Gaps that matter

1. **Class C.** The engine for classes A and B exists and has run in the lab. Class C (the staged method with a data mover, hosts and arrays) is not built; every "Not built" row above is class C or Phase 0.
2. **Per-volume gates G2, G3, G4.** Now built (see the gates table). What stays open: G3 sits at the entry of the cutover phase until the engine separates the freeze from the swap.
3. **Internal-tenant volumes (step 1.7).** The image-cache volume is found and held back; other volumes of Cinder's internal project are not looked for.
4. **`provider_id` repair (step 1.9).** Found, not repaired.
5. **Phase 0.** No guided lab procedures for T1 to T22. The lab does not replace Phase 0 on the customer's Queens and arrays.
6. **Checks on hosts and arrays** show "not checked" until the access exists. They need attestations in the meantime.

## What OpenMove adds beyond the playbook

| Addition | Where |
|---|---|
| Rechecks (two or more, apart in time) and a lag-zero test before every cutover | `lifecycle.py` |
| Soak period enforced before the source is released | `lifecycle.py` |
| Workload risk check V11, marks, two-person clearance | `WORKLOAD_RISKS.md` |
| Selection of volumes, by volume and by group, with options and floors | `selection.py` |
| Not-migrated report with cause, state left behind and proposed steps | `handling.py` |
| Halt switch, automatic halt after failures, hold and release before the cutover | `safety.py`, `server.py` |
| Demo mode with three scenarios | `openmove/demo/` |
| A gated wizard: a step opens only when every step before it is done, the selection must be confirmed, and a dry run must cover the volumes before a real run | `wizard.py`, `WALKTHROUGH.md` |
| Detection of a retype that Cinder accepted and silently undid | `engine/runner.py` |
| A post-check of the identity fields of the volume | `engine/runner.py` |

## How this page stays true

Each row is checked by hand against the playbook sections named. Change a row in the same commit that changes the code. Before a release, read the "Not built" rows again and make sure the README and the user guide do not claim more.
