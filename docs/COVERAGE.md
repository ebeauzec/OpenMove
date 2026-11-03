# Requirements and proposal: what the tool does and what it does not

This page checks OpenMove against the owner's requirements (`REQUIREMENTS.md`, R1 to R18) and the proposal (`DESIGN.md`). It does not round up. Each line says **met**, **partly**, or **not built**, with the reason and, for what is not built, what is needed to build it.

The short answer to "is there anything the tool cannot do?": **yes**. It does not move class C volumes (volumes with long-lived snapshots), it has no SSH, database or array access, and nothing has run on a customer cloud. Everything in the table below that could be closed without that access has been closed; what is left needs access, an environment or a decision from the owner.

Status words: **met** = built, tested, and (for the retype path) run against the OpenStack lab. **partly** = built for the retype path or for part of the requirement. **not built** = no code.

## The requirements

| # | Requirement | Status | What exists | What is missing, and why |
|---|---|---|---|---|
| R1 | Standalone, deployable on site, no internet | **met** (source) / **partly** (packages) | Python standard library only; pages load nothing from outside; own state in the data folder (`tests/test_self_contained.py`). A Windows program is built by `tools/build/build_exe.py` | The packaged program has never run on a machine without Python. The container image (`Dockerfile`) is written but has not been built: the Docker engine on the development machine was not running |
| R2 | Minimal communication | **met** | Talks to the OpenStack endpoints of the cloud and nothing else; every destination is listed in `NETWORK.md`; a test fails when code opens a connection that page does not name | None |
| R3 | Maximum control | **met** for the retype path | Selection by volume and by group, settings with floors, dry run before a real run, stop taking new volumes, HALT and resume, a **run window** (UTC start and stop) outside which no volume is taken and no retype is sent, concurrency, target type | A per-compute-host concurrency cap (the cap is global); pausing a single volume by hand (HALT and the hold-before-cutover setting cover it) |
| R4 | Extensive checks and reports | **partly** | Pre-flight (P, V, S, C, X), environment audit, post-check against the before-state, reports for audit, plan, update, outcome and not-migrated volumes | Checks that need the Cinder database, the hosts or the arrays (P3 variant, P5 to P8, P10, P12) show "not checked" and need a named person's attestation. They need SSH, database and array access |
| R5 | Bomb-proof | **met** for the retype path | The journal records intent before every change; restart resumes from it; one change is never sent twice; timeouts; unexpected answers halt; a retype Cinder accepted and undid is detected. Failure tests on the mock cloud, and on the lab: an instance locked by a tenant, Cinder API down, volume service down, HALT during a move, a snapshot volume | The staged method is not built, so its failure modes are not tested. A power cut on a real host has not been injected |
| R6 | Rollback of every action | **partly** | Every change has its recorded undo and the dry run shows it. A volume can be put back with a retype back; **the whole run can be put back, and a preview shows exactly what that would do before it runs**. A rollback is journaled, checked and repeatable | Rolling back a single *step* of a volume (for example only the unlock) is not offered: a person halts and looks. Rollback after the soak has ended is not possible (the source is released). The staged rollbacks R1 and R2 are not built |
| R7 | Verbose, enterprise reporting | **partly** | Word reports with a controlled-document title block, numbered chapters, tables, sign-off: pre-migration (with the audit), environment audit, volumes not migrated, **update report, after report (every volume compared with its before-state, each difference marked expected or unexpected), and the volume list as Excel and CSV**. The Word renderer is the project's own copy and is bundled | **PDF is not built**: a plain PDF writer is possible with the standard library, but the owner has not confirmed the format (the proposal's open question 4). A page-fixed hand-over format is therefore missing |
| R8 | As many back-ends as possible; support matrix | **partly** | Back-end families are data (`resources/backends.json`); the engine never tests for a vendor name; check P3 fails for a back-end it does not know ("unsupported", with the reason) and warns for a known pair nobody verified; the support matrix is generated (`SUPPORT_MATRIX.md`) and a test keeps it current | Only one pair is verified (two LVM back-ends in the lab). SolidFire, FlashArray, ONTAP and Ceph are recognised but **not verified**: that needs Phase 0 on real arrays. Array adapters for the staged method are not built |
| R9 | Detects the OpenStack version and acts on it | **met** | Discovery, release inference, profiles as data, read-only for an untested release | A Queens profile exists but is `untested` until Phase 0 is recorded; that is by design |
| R10 | Platform independent | **partly** | Pure Python, no shell scripts; the whole test suite passes on Windows and on Linux (Ubuntu in WSL) | **macOS has never been run.** Packaged programs exist only for Windows. Reports and the journal are portable files, but a job opened on a second system has not been tried |
| R11 | Container, own Linux deployment, everything from the tool | **not built**, except the container file | `Dockerfile` and `.dockerignore` (not yet built or run). Everything on the retype path is controlled from the page; no terminal is needed | The own Linux image; the migration-host registry, readiness checks and preparation; preparing arrays and Cinder (types, access, quota, reference volume) from the tool; database backup; running the lab tests from the tool (they are now guided and recorded, but a person runs them). Needs the data mover, SSH and array access first |
| R12 | Works with the existing Cinder hosts alone | **partly** | The retype path uses only the Cinder APIs and the cinder-volume hosts that already exist; it adds no host and installs nothing on any | The data-mover relay between two cinder-volume hosts (for class C) is not built; it needs SSH and a lab with both arrays |
| R13 | Safe: no failure may cause an outage | **met** for the retype path | Read and change are separated; a permit is checked again for every change; the source is read-only until the retype; HALT; 40 hazards with a control and a test each (`SAFETY.md`) | Proven on the mock and the lab only. Not proven on a customer cloud |
| R14 | The fixed order | **met** for the retype path | `lifecycle.py` enforces the order; the cutover conditions are asked at the moment of the change | The staged order (copy, sync, recheck twice, cutover) is built as rules and simulated in the demonstration, not as actions |
| R15 | A volume that is not migrated is explained | **met** | The not-migrated report with cause, state left behind, proposed steps and who acts | None |
| R16 | The operator chooses what moves | **met** | Selection, groups, defaults, floors, confirmation | None |
| R17 | A workload that may break is not moved | **met** within what can be seen | Check V11, marks, two-person clearance | It cannot look inside a guest; hints only (said in the tool and `WORKLOAD_RISKS.md`) |
| R18 | Always clear what is happening to each volume | **met** | The per-volume step view, the cutover conditions, `PROCESS.md` kept in step by a test | None |

## The proposal (`DESIGN.md`)

| Component or item | Status | Note |
|---|---|---|
| Connector: OpenStack API | **met** | `connectors/` |
| Connector: Cinder database | **not built** | Needs read access to the database. The playbook's query results can be imported instead |
| Connector: SSH runner | **not built** | Needs the owner's decision on the SSH client (system OpenSSH or a bundled library) and hosts to test on |
| Connector: array adapters | **not built** | Needs the arrays |
| Discovery and release profiles | **met** | |
| Engine for class A and B | **met**, lab-proven | Class C: **not built** |
| Checks (catalogue P, V, S, C, X) | **partly** | See R4 |
| Reports: Word | **met** | |
| Reports: Excel, CSV | **met** (volume list) | |
| Reports: PDF | **not built** | Owner decision |
| GUI: inventory, plan, job control, gates, reports, settings | **met** | A gated nine-step wizard |
| Authentication and roles | **not built** | The interface has a per-start token only. Roles (operator, second person, read-only) need the owner's decision: local users or a directory |
| Phase 0 lab tests as guided procedures | **met** as guidance and record | `Phase 0` panel on the Move step: 26 tests, a result per test with a name and evidence, gate G0 recorded from them only when every test of the retype path has passed. A person runs the tests; OpenMove cannot |
| Single executable | **partly** | Windows only, never run on a clean machine |
| Container | **partly** | File written, not built |

## What would close the rest

| To build | Needs |
|---|---|
| Class C engine, data mover, relay, SSH | An environment with both arrays and a Queens cloud; the owner's decision on the SSH client; the owner's decision on whether to build class C at all (the second review advises keeping it out of the critical path) |
| Database connector and the database checks | Read access to the Cinder database of a test cloud |
| Array adapters and the checks on arrays | The arrays, or their simulators |
| PDF reports | The owner's confirmation of the format |
| Authentication and roles | The owner's decision |
| macOS and clean-machine runs, the container build | A Mac; a clean Windows machine; a running Docker engine |
| Verified pairs in the support matrix | Phase 0 on the customer's Queens and arrays |
