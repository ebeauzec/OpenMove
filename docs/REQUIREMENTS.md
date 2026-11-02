# Requirements

Product requirements R1-R9 as stated by the owner. They apply to every design decision. The technical playbook (to be added to this folder) defines the detail; where the two differ, the playbook wins and this file is corrected.

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

## R7. Verbose, enterprise-standard reporting

- Reports are verbose. Nothing that was checked, decided or done is left out.
- A migration report contains: every pre-migration check with its result and evidence, the volumes to be moved (with source, target, size, type, state, attachment and owner), the plan, the risks found, and the rollback plan.
- The same report series serves as the operations team's progress and update reports: status counts, the state of every volume, what changed since the last report, open issues, and next steps.
- Format is enterprise standard: a controlled document (title, document identifier, version, date, status, author, distribution), an executive summary, numbered sections, tables with consistent columns, an issues and risks log, an appendix with raw evidence, and a sign-off block. The report carries the copyright notice required by `LEGAL.md`.
- Reports are produced as documents operations can open without OpenMove (a page-fixed format for hand-over and archive, and an editable office format), and the volume lists are also produced as spreadsheet data. The exact set of formats is confirmed with the owner before the reporting module is built.
- **Before and after.** Every migration job produces a matched pair of reports so progress is clear at a glance:
  - *Before report* (baseline): the state of every volume in scope as found, all pre-migration checks, the plan, the risks and the rollback plan. It is fixed when the plan is approved.
  - *After report*: the state of the same volumes once the job ends, the post-migration verification, and a side-by-side comparison with the before report for each volume (back-end, volume type, size, status, attachment, host, metadata), with every difference marked as expected or unexpected.
  - *Update reports* between the two use the same layout and show, against the before baseline, what has moved, what is running, what failed and what was rolled back.
  - A rolled-back job gets an after report that shows the volumes back at their baseline, and states whether they match it.
- **Word renderer.** Word output uses the document parser and renderer proven in ARIA: a text/Markdown-style report is parsed into a document model (title, metadata, headings, tables, lists, charts) and rendered to a .docx with a document control block on the title page, numbered chapters, a contents list, consistent tables and a running header and footer. OpenMove gets its own generic copy, with every ARIA- and NetApp-specific rule and name removed, and with the matching layout lint (the equivalent of ARIA's `wordlint.py`) and its rule tests. The copy is bundled so the report is built offline.
- Reports are generated offline, are reproducible from the stored job record, and name the OpenMove version and the cloud they describe.
- Reports contain real cloud data, so they stay on the machine (see section 7 of the README); they are never committed.

## R8. As many storage back-ends as possible

- OpenMove migrates volumes between Cinder back-ends and supports as many back-ends as can be done safely, with a focus on moves to and from:
  - NetApp SolidFire
  - NetApp ONTAP
  - Pure Storage
  - other back-ends, as the playbook and testing allow (for example Ceph RBD, LVM, and the other vendor drivers Cinder supports)
- Back-end support is data plus a small adapter per back-end, not code scattered through the application. Adding a back-end must not change the migration engine.
- For each back-end pair OpenMove knows (or discovers from the cloud) which migration method is available, for example a driver-assisted move or a host-assisted copy, and shows the choice, its limits and its risks in the plan.
- A back-end whose behaviour is not known or not verified is reported as unsupported for that pair, and the operator is told why, instead of being attempted.
- A support matrix (back-end by back-end, direction by direction, with the method and the verification status) is part of the documentation and of every report.

## R9. Detects the OpenStack version and acts accordingly

- OpenMove finds out which OpenStack release it is talking to, and the versions of the services and clients that matter (identity, block storage, compute, the hypervisor, the back-end drivers), before it plans anything. It does not rely on the operator to say.
- Detection uses what the cloud reports (API version discovery and supported microversions) and, where the operator has allowed it, what the hosts report (package and driver versions). Where sources disagree, OpenMove reports the disagreement and uses the most cautious reading.
- Behaviour follows the detected version. Commands, API calls and payloads, which tools are used, how a result is read, and which migration procedure variant applies are chosen from a profile of that release. A step that exists in one release and not in another is replaced or reported, never attempted blind.
- A release OpenMove has not been validated against is treated as untested: inventory, checks and reports run (read only), and every step that changes the cloud is refused until a lab validation for that release has been recorded in OpenMove. This is the playbook's rule that a different release needs Phase 0 again.
- The detected versions, the profile used, and any difference from the validated build are printed in the plan and in every report.
- Supporting a new release means adding a profile and its tests. It does not mean changing the engine.

## Consequences for the design

| Requirement | Design consequence |
|---|---|
| R1, R2 | Single installable package with bundled dependencies and an embedded local datastore. All network access goes through one OpenStack client module. |
| R3 | A plan object that is shown, approved, then executed. Settings for each decision point. |
| R4 | Check framework with a result record per check, reused by pre-flight, in-flight and post-migration stages. |
| R5 | Write-ahead journal and a resumable state machine per volume. Idempotent steps. Fault-injection tests. |
| R6 | Each journal entry carries its inverse. Rollback is a plan like any other. |
| R7 | A before snapshot stored with the job, taken at approval and never changed. An after snapshot taken at the end, and a comparison built from the two. A report model built from the job record, rendered by separate writers per format (Word through the ported ARIA parser/renderer, which also decides how the application can run that code: ARIA's is JavaScript, so the stack decision must allow it or replace it with an equivalent). Report sections are generated from the same check and journal data the engine uses, so a report cannot disagree with what happened. |
| R9 | A discovery module that builds a platform profile (release, service and client versions) at connection time, and release profiles as data that the engine and connectors consult. Mutating steps check the profile status (validated, untested) before they run. |
| R8 | A back-end adapter interface and a capability profile per back-end. The engine asks the adapter what is possible; it never tests for a vendor name. A support matrix generated from the profiles. |

These are consequences of the requirements, not decisions about technology. The stack is chosen after the playbook is read.
