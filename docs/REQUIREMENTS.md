# Requirements

Product requirements as stated by the owner. They apply to every design decision. The technical playbook (to be added to this folder) defines the detail; where the two differ, the playbook wins and this file is corrected.

## R1. Standalone, deployable on site

- OpenMove is one self-contained application. It runs on a machine at the customer site without internet access and without installing a server stack, database service or runtime by hand.
- Everything it needs ships with it: libraries, fonts, scripts and documentation. Nothing is loaded from a CDN or a package index at run time.
- It stores its own state locally (job journal, settings, reports) and nowhere else.

## R2. Minimal communication

- OpenMove talks to OpenStack and nothing else. Ideally the only endpoints it contacts are the OpenStack API endpoints of the cloud it is pointed at (identity, block storage, and any other service the playbook shows is needed).
- No telemetry, no update check, no licence call-home, no external lookup.
- Every network destination is listed in the documentation. Adding one is a documented design decision.

## R3. Maximum control over migration

- The operator decides what moves, where, when and how: selection of volumes, target backend and volume type, migration policy, order, concurrency, scheduling.
- A plan is shown in full before anything runs (dry run). Nothing changes the cloud until the operator approves.
- A running job can be paused, resumed or stopped at defined points.
- Behaviour that the playbook leaves open is a setting, not a hard-coded choice.

## R4. Extensive reporting and checks

- Checks before a migration: capacity, backend and type compatibility, attachment and state, snapshots and dependencies, quotas, and the health of the services involved.
- Checks during and after: progress, state transitions, and verification that the result matches the plan (size, type, backend, attachment, metadata).
- Reports for audit, plan, execution and outcome, in formats an operator can keep and hand over.
- Every check states what it looked at, what it found and what it means.

## R5. Bomb-proof

- A failure, a crash, a lost connection or a power cut never leaves a volume in an unknown state. The application writes what it is about to do before it does it, and recovers from that record on restart.
- Every operation is safe to repeat.
- Input from the operator and answers from OpenStack are validated; unexpected answers stop the job with a clear message instead of being guessed at.
- Every call to OpenStack has a timeout and a defined response to failure.
- The application never removes or overwrites source data until the result has been verified.
- The application is tested against failure cases, not only against success.

## R6. Rollback of every action

- Every change OpenMove makes to the cloud is recorded together with how to undo it.
- The operator can roll back a single step, a volume, or a whole job, and sees what a rollback will do before it runs.
- Rollback is itself journaled, checked and repeatable.
- Where an action cannot be undone, OpenMove says so before it runs and requires an explicit confirmation. This is part of the plan shown under R3.

## Consequences for the design

| Requirement | Design consequence |
|---|---|
| R1, R2 | Single installable package with bundled dependencies and an embedded local datastore. All network access goes through one OpenStack client module. |
| R3 | A plan object that is shown, approved, then executed. Settings for each decision point. |
| R4 | Check framework with a result record per check, reused by pre-flight, in-flight and post-migration stages. |
| R5 | Write-ahead journal and a resumable state machine per volume. Idempotent steps. Fault-injection tests. |
| R6 | Each journal entry carries its inverse. Rollback is a plan like any other. |

These are consequences of the requirements, not decisions about technology. The stack is chosen after the playbook is read.
