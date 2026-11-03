# Volume classes: what is handled, how, and what is not built yet

The owner's question was whether all the logic is in place for every class of volume, including those with snapshots. This page answers it class by class, with no rounding up. The class definitions and the method are in `MIGRATION_METHOD.md` (sections 3 and 4); the order every volume follows is in `PROCESS.md` (section 4). A test (`tests/test_handling.py`) keeps this page's claims in step with the code.

## The short answer

**No. The decision logic is built. The part that moves data is not.**

| Layer | State |
|---|---|
| Classification of every volume into A to E, with a reason, and what could not be known stated (`classification.py`) | built, tested |
| Which classes may enter a migration, and which are held back (`checks/run.py` admits A, B, C with no failed check; D and E never start) | built, tested |
| The rules each class must meet inside the order of a migration (`lifecycle.py`, section below) | built, tested |
| What happens to a volume that is not migrated: cause, how, state left behind, proposed steps (`handling.py`, the not-migrated report) | built, tested |
| A simulated run in which every class takes its own path and fails in its own ways (demonstration only) | built, tested |
| The code that moves class A and B volumes: lock, retype, follow, post-check, unlock, reverse (R3), and the class B wait for the rotation | built, tested against a mock cloud only |
| The code that moves class C: the history rebuild, freeze and swap, rollback R1 and R2 | **not built** |
| Lab validation of any of it (playbook Phase 0, tests T1 to T21) | **not done**; nothing has run against a real cloud |

So for snapshots in particular: class B and class C volumes are recognised, kept apart from class A, held to stricter rules, and shown with their own failure causes. The staged rebuild of a snapshot history on the target array (playbook phase 5) is **not written yet**.

## Class by class

| Class | Meaning | What the lifecycle enforces today | What the engine must still do | Not-migrated causes |
|---|---|---|---|---|
| A | No snapshots. | Class given at precheck. At validation: zero snapshots now, and room on the target. Then the common order. | Standard retype or migrate; the copy and sync for a block-level move. | no room, snapshot appeared, copy not verified, source never stable, cutover refused |
| B | Snapshots that rotate away within the window. | At validation: refused unless the snapshot count is zero at that moment ("wait for the rotation"). Then the class A path. | The rotation watcher that waits and fires the standard path. | snapshots did not rotate away (timeout), then as class A |
| C | Long-lived snapshots. | The copy must record how many snapshot images it rebuilds. The copy cannot leave `copy` unless every image compared identical and the snapshot ids match (`chain_match`, `snapshots_matched`). Every recheck must reconcile (no tenant snapshot created or deleted since the last pass). | Rollback clone; staging volume built from the oldest snapshot; each snapshot applied in order and taken on the target with the same id; destination record; freeze; swap; postcheck G4. | chain mismatch, tenant snapshot raced the freeze, swap refused (rolled back), plus the class A causes |
| D | Attached to an instance that is not `ACTIVE` or `PAUSED`. | Refused at precheck. It never enters the order. | After the instance runs it is classified again (A or C). The detached variant needs an admin lock and detach and a recorded decision. | instance not running |
| E | An image-cache volume (Cinder's own cache), encrypted, multiattach, grouped, replicated, wrong status, a migration in a non-final state, or a malformed provider id. | Refused at precheck. It never enters the order. | Nothing: not moved by this method. A follow-up plan per reason. | encrypted, multiattach, grouped, replicated, bad status, migration in flight, bad provider id |

A volume that fails a per-volume check (for example no volume type, a snapshot that is not available, no room on the target) is held back whatever its class, with the failed check quoted.

## What the rules do

The class is recorded at precheck and every later rule reads it from the journal, not from the caller:

* `precheck`: the class must be A, B or C. D and E are refused with the reason. A missing or unknown class is refused.
* `validate`: classes A and B need `snapshots_now` equal to zero, and `room_ok` must not be false.
* `copy` (class C): `snapshots` (an integer) must be recorded.
* `sync` after the copy (class C): `chain_match` must be true and `snapshots_matched` must equal the recorded `snapshots`, on top of the size and hash that every class needs.
* `recheck` (class C): the recheck passes only if `reconciled` is true. A failed recheck goes back to sync and the count of passes starts again.
* `cutover`: the seven conditions of `PROCESS.md`, for every class.

Each rule is a refusal that is recorded as a `refusal` event with its reason, so a wrong class or missing evidence is visible in the Live tab and the report. The tests are `tests/test_lifecycle.py` and `tests/test_handling.py` (`ClassRuleTests`).

## Volumes that are not migrated

For any reason, a volume that is not migrated is written up in one place: the **not-migrated report** (Reports tab, or the Live tab). Each volume has:

* what happened (stopped, rolled back, held back, not admitted, not started), the phase reached and the timeline from the journal, including every step the tool refused;
* why, in plain words (`handling.CAUSES`);
* the state the source and the target were left in. Before the cutover the source was only read; after a rollback it was restored;
* the proposed steps to resolve it, and who should act.

The pre-migration report also lists, for every held-back volume, the same proposed steps. The steps are proposals for people; the tool does none of them for you.

| Cause | Typical class | Source after |
|---|---|---|
| encrypted, multiattach, grouped, replicated | E | untouched |
| image-cache volume (Cinder's own, read-only, named `image-<image id>`) | E | untouched; Cinder builds the cache again on the new back-end |
| already on the target back-end | not admitted | nothing to move |
| bad status, migration in flight, bad provider id | E | untouched |
| instance not running | D | untouched |
| a pre-flight check failed | A, B, C | untouched |
| snapshots did not rotate away | B | untouched |
| snapshot appeared before the move | A, B | untouched |
| no room on the target | any | untouched |
| copy did not verify | A, B | untouched, target copy discarded |
| snapshot history does not match | C | untouched, staging volume discarded |
| source kept changing | any | untouched |
| cutover refused | any | untouched |
| tenant snapshot at the freeze | C | restored |
| swap refused | C, A attached | restored by rollback R2 |
| halted, gate closed, not started | any | as it was |

## What is in the demonstration

The enterprise scenario has volumes of every class and, in the simulated run, at least one of each failure above, so the report can be shown with real-looking content. The two smaller scenarios have all classes too. All evidence in the simulation is made up and labelled; the rules that refuse and the report that explains are the real ones.

## Workload risk, on top of the class

A class says what the volume is. It does not say what is on it. A class A volume may hold a legacy Windows server that will not survive the swap. Check V11 looks at hints for that (`WORKLOAD_RISKS.md`). A high-risk volume fails the check whatever its class and is held back with the cause `risky_workload`. It moves only after two people clear it.
