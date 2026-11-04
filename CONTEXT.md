# CONTEXT.md — OpenMove

Architecture and project state. Updated with every change that alters either.

## 1. What This Project Is

OpenMove is a GUI-driven tool that automates the audit, reporting and migration of OpenStack Cinder volumes. It integrates directly with OpenStack and carries out its work by calling OpenStack services and clients.

Product requirements are in `docs/REQUIREMENTS.md`: a standalone application deployable on site, communicating only with OpenStack, with maximum control over migration, verbose enterprise-standard reporting that doubles as the operations team's progress report, checks before, during and after migration, resilience to any failure, rollback of every action, and support for as many Cinder back-ends as possible (focus: NetApp SolidFire, NetApp ONTAP, Pure Storage).

The functional specification (the playbook) is supplied by the owner and is stored in `docs/`. Where this file and the specification disagree, the specification wins and this file is corrected.

## 2. Architecture

The architecture as built is in `docs/ARCHITECTURE.md`: one Python process (standard library only) with a local web interface, a journal in SQLite, an engine that moves class A and B volumes with Cinder's retype, and a safety layer (release profile, gate, halt, a permit asked again before every change). `docs/DESIGN.md` is the earlier proposal for the parts that are not built (SSH, data mover, array adapters, the staged method for class C, the container).

## 3. File Inventory & Purpose

| File | Purpose |
|---|---|
| `README.md` | Project overview and usage. |
| `CONTEXT.md` | This file. |
| `CHANGELOG.md` | Release history. |
| `version.json` | Current version, date and note. |
| `LICENSE` | Proprietary licence. Rights holders: Eugene Beauzec and Obi1 - FZCO. |
| `LEGAL.md` | Ownership and intellectual property declaration. |
| `THIRD_PARTY_NOTICES.md` | Third-party licences and trademark notices. Updated whenever a dependency is added. |
| `docs/README.md` | Index of the documents in `docs/`. |
| `docs/WALKTHROUGH.md` | The process from the first click to the last, with screenshots (`docs/images/`) and diagrams: the nine gated steps, who does what, what happens to each volume, what to do when something goes wrong. |
| `docs/OPTIONS.md` | Every option and setting in one place: launchers, command line, modes, each screen's fields, defaults and limits, gates, fixed values. |
| `openmove/wizard.py` | The gating rules of the wizard: which step is open, the selection confirmation, the dry-run-before-run rule. Used by `server.py` (`/api/status` carries `wizard`). |
| `tests/test_wizard.py` | The gated wizard, through the rules and through the real handlers in demonstration mode. |
| `tools/dev/screenshots.py` | Takes the documentation screenshots from the real interface with a headless Edge or Chrome, in demonstration mode (and, with `--lab`, the Move step from the lab). |
| `docs/REQUIREMENTS.md` | Product requirements R1-R18 from the owner (R13 safety, R14 the order, R15 unmigrated volumes, R16 selection, R17 workload risk, R18 per-volume process). |
| `docs/CLASSES.md` | What is handled for each volume class, and why a volume may not be migrated. |
| `docs/WORKLOAD_RISKS.md` | Workloads that may fail the migration and how OpenMove identifies them (check V11). |
| `docs/ENGINE_PLAN.md` | The plan for the migration engine: milestones E1 to E7, the test environment inputs, the open design decision. |
| `docs/PLAYBOOK_TRACE.md` | Playbook v1.2 step by step: covered, check only, not built, gaps. |
| `docs/MIGRATION_METHOD.md` | The migration method (from the owner's playbook): classes, phases, gates, rollback, check catalogue. |
| `docs/DESIGN.md` | Proposed architecture and open questions. |
| `docs/USER_GUIDE.md` | How to start, demonstrate and use OpenMove; troubleshooting; glossary. |
| `docs/PROCESS.md` | The process step by step, the fixed order of a migration and the failure handling. For review. |
| `docs/API_CALLS.md` | Every cloud call, local route and journal event. For review; a test keeps it in step with the code. |
| `docs/SAFETY.md` | Hazards, the control for each, where it is and which test proves it. |
| `OpenMove.bat`, `OpenMove-Demo.bat`, `openmove.sh` | Launchers: use `OpenMove.exe` next to them, else Python and the source. The browser opens by itself. |
| `tools/build/entry.py`, `tools/build/build_exe.py` | Builds the standalone program (PyInstaller, one file) with the owner's details in its properties. |
| `docs/DEVELOPMENT.md` | Development workflow, hooks, guard and release steps. |
| `tools/guard_sensitive.py` | Blocks credentials, cloud data and disallowed wording at commit and push. |
| `tools/dev/release.py` | Version bump and changelog entry. |
| `.githooks/commit-msg` | Strips any assistant trailer from a commit message. |
| `.githooks/pre-commit` | Runs the guard on staged files. |
| `.githooks/pre-push` | Runs the guard on unpushed commits and checks authorship. |
| `data/README.md` | Explains that `data/` is local only. |
| `openmove/__init__.py` | Package; reads the version from `version.json`. |
| `openmove/__main__.py` | Entry point (`python -m openmove`): starts the local web server and opens the browser. |
| `openmove/paths.py` | Per-user data folder (`OPENMOVE_HOME` overrides it) and bundled resource folder. |
| `openmove/journal.py` | SQLite journal (WAL, synchronous FULL): events and write-ahead steps. A step records its intent and its inverse before it runs; secrets are redacted before anything is written. |
| `openmove/releases.py` | Infers the OpenStack release from the Block Storage and Compute API microversion maxima. |
| `openmove/profiles.py` | Release profiles and their status. A release is `validated` only when a passed lab validation is recorded in the journal; otherwise the tool is read only. |
| `openmove/connectors/httpclient.py` | Minimal HTTP client on the standard library: timeouts, TLS options, size cap, no tokens in errors. Read-only by construction: refuses every change without a permit. |
| `openmove/connectors/openstack.py` | Keystone v3 session, service catalogue, paging, one re-authentication on 401. The password lives in memory only. |
| `openmove/discovery.py` | Detects identity, block storage and compute versions, release, profile status, back-end pools, Cinder services and compute hosts. |
| `openmove/classification.py` | Volume classes A-E from the playbook rules; states what it could not check. |
| `openmove/exports.py` | Reads the playbook's query exports for clouds where the database is not reachable. |
| `openmove/inventory.py` | Collects volumes, snapshots and servers through the APIs and builds the classified inventory. |
| `openmove/server.py` | Local web server: per-run access token, Host header check, security headers, JSON routes (status, platform, inventory, audit, checks, attest, sign-off, halt, live, report, demo). |
| `openmove/safety.py` | The one place that decides whether anything may change a cloud: the permit (release validated, gate approved, no halt; checked again for every change; fails closed) and the halt switch. |
| `openmove/lifecycle.py` | The fixed order of a migration per volume and the seven cutover conditions, kept in the journal. |
| `openmove/audit.py` | The environment audit: sections and findings A1-A22. |
| `openmove/checks/catalogue.py`, `run.py`, `gate.py` | The check catalogue, the pre-flight run, attestations, four-eyes sign-off and the gate. |
| `openmove/reports/` | Word writer (`docx.py`, a port of the house renderer), Markdown reader and the report builders. |
| `openmove/demo/` | Demonstration mode: mock cloud, three scenarios (`scenarios.py`), the made-up database export, the simulated migration run. |
| `openmove/resources/releases.json` | Microversion table per release, with source and retrieval date. |
| `openmove/resources/release_profiles.json` | Per-release behaviour (Queens profile from the playbook). |
| `openmove/resources/web/` | The interface: `index.html`, `styles.css`, `app.js`. Text is inserted as text, never as markup. |
| `docs/ARCHITECTURE.md` | The architecture as built, with diagrams. |
| `docs/INSTALL.md` | Installation guide. |
| `docs/OPERATIONS.md` | Operation guide: the procedure, the way back, halt and restart, notes, checklists. |
| `docs/MOVER.md` | Class C in practice: the hosts, SSH, the data mover and the Cinder helper, the host checks. |
| `docs/DIAGNOSTICS.md` | Logs, support bundles and redaction for dark sites. |
| `openmove/engine/staged.py` | The staged method for class C (`StagedMixin`): copy, sync, rechecks, the cutover steps, R1, R2, R3, per-volume state in the journal. |
| `openmove/hosts.py`, `openmove/server_hosts.py` | The host registry (`hosts.json`), the readiness checks H1 to H9, C1, C2, the routes of the Hosts and Diagnostics tabs. |
| `openmove/connectors/ssh.py` | SSH with the system OpenSSH client: keys only, OpenMove's own key, pinned host keys, a trace of every call. |
| `openmove/resources/mover/` | `mover.py` (the data mover, Python 3.6+, standard library) and `cinder_helper.py` (run with Cinder's own Python); placed on the hosts by the tool. |
| `openmove/arrays/` | Array adapters: `base.py`, `lvm.py` (the only one built). |
| `openmove/diag.py`, `openmove/redact.py` | Logs, trace, support bundles; redaction at three levels with a local token map. |
| `openmove/lab/hosts.py` | Makes, starts, registers and checks the two lab hosts (WSL 2 distros). |
| `tests/test_staged.py`, `test_mover.py`, `test_diag.py`, `test_redact.py`, `test_lab_hosts.py` | The staged method against the fake fabric, the data mover and the Cinder helper, the hosts registry, diagnostics and bundles, redaction, the lab host setup (calls stubbed). |
| `tools/lab/vm/` (`build_vm.sh`, `run_vm.sh`, `make_target.sh`), `lab.py vm-up` | The lab's real virtual machine (own kernel, QEMU with KVM) as a bare host and an iSCSI target with two portals, to prove the storage paths of a host setup. |
| `openmove/bootstrap.py`, `openmove/resources/deploy/configure_storage.sh`, `tests/test_bootstrap.py` | Set up a new host from OpenMove: one login with the administrator password (`ssh.RootLogin`), `prepare_host.sh`, the storage paths (iSCSI initiator, multipath, logins), registration, the key-only proof. Route `POST /api/hosts/setup`; the password is never kept. |
| `openmove/selfcheck.py`, `openmove/resources/deploy/prepare_host.sh`, `docs/DEPLOYMENT.md`, `tests/test_deploy.py` | The installation self-check; the host setup script (account, packages, key, narrow sudo rights, sshd); the production deployment guide; their tests. |
| `tools/lab/lab_staged.py` | Drives a real class C move against the lab. |
| `docs/LAB.md` | The OpenStack lab: what it proves, commands, the seeded catalogue, what it has shown, troubleshooting. |
| `OpenMove-Lab.bat`, `OpenMove-Lab-Stop.bat` | Start OpenMove against the lab (and the dashboard); stop the lab gracefully. |
| `tools/lab/` | `lab.py` (status, up, down, seed, clean, list), `seed.py`, `lab_up.sh`, `lab_down.sh`, `provision_devstack.sh`, `README.md`. |
| `openmove/engine/` | The retype engine: `runner.py` (what comes next), `plan.py` (checks and dry run), `actions.py` (the only code that sends a change), `evidence.py`. |
| `openmove/lab/__init__.py` | Lab mode: configuration, connect, the Horizon sign-in. |
| `openmove/handling.py` | What happens to each class, and the cause and steps for every volume that is not migrated. |
| `openmove/selection.py`, `openmove/risk.py` | The selection and the settings (R16); workload risk (R17). |
| `openmove/recovery.py`, `openmove/supervisor.py` | The failsafe (`docs/RECOVERY.md`): the access token kept across restarts, `resume.json` (the saved work), the restore after a cut-off, the guardian thread, the one-program-per-data-folder lock; the supervisor that starts the program again. |
| `openmove/tree.py` | The volume tree (project, instance, attached volumes) in plain words, with each volume's verdict once the pre-flight has run; `GET /api/tree`. Read only. |
| `tests/test_engine.py`, `test_access.py`, `test_lab.py`, `test_handling.py`, `test_selection.py`, `test_tree.py`, `test_recovery.py` | The engine against a mock cloud with fault injection; access levels and real-lab findings; lab configuration; causes; selection; the volume tree. |
| `tests/mock_openstack.py` | A mock Keystone, Cinder and Nova cloud with made-up data. |
| `tests/test_core.py` | Tests for release inference, profiles, journal, classification and exports. |
| `tests/test_cloud.py` | Tests for discovery, inventory and the web server against the mock cloud. |
| `tests/test_checks.py`, `test_demo.py`, `test_safety.py`, `test_lifecycle.py`, `test_docs_sync.py`, `test_self_contained.py` | Checks and gate; scenarios, simulation and reports; the safety rules; the order of a migration; documentation in step with the code; standard library only. |

## 4. Interface

A local web interface, started with `python -m openmove`. It listens on the loopback address only; the page and every call need the per-run token printed at start. Tabs: **Connect**, **Platform**, **Inventory**, **Audit**, **Pre-flight**, **Select**, **Move**, **Reports**, **Live**, **Journal**, with Back and Next buttons and a red **HALT** button in the header. Text is inserted as text, never as markup. Modes: real, `--demo` (made-up cloud, simulated migration) and `--lab` (a real OpenStack on this machine, with the real engine). See `docs/USER_GUIDE.md` and `docs/OPERATIONS.md`.

## 5. What Is Complete

- Project scaffold, documentation set, versioning and release helper; repository safeguards (hooks, sensitive-data guard).
- Release detection from API versions, release profiles, and read only until a lab validation is recorded.
- Read-only inventory and classification (classes A-E, including Cinder's image-cache volume as class E) through the OpenStack APIs, or from imported playbook exports.
- The check framework (P, V, S, C, X), attestations, four-eyes sign-off and a fail-closed gate; the environment audit; Word reports (pre-migration, audit, not migrated).
- The safety layer: read-only client, permit asked before every change, halt, the fixed order of a migration, per-volume gates G2, G3, G4 signed by a second person.
- Selection with granular control and settings (R16); workload risk (R17); the per-volume process view (R18); the not-migrated report with causes and steps (R15).
- The retype engine for class A and B (APIs only): lock, retype with on-demand migration, follow, post-check against the before-state (status, size, attachments, bootable flag, instance state and the identity fields), unlock, soak, reverse by retype back; a retype that Cinder accepted and silently undid is detected and rolled back; unknown outcomes halt; resumable from the journal.
- Reports: Word (pre-migration, audit, not migrated, update, after with every volume compared to its before-state) and the volume list as Excel and CSV (`reports/after.py`, `reports/sheet.py`). PDF is not built (owner decision).
- Back-end families and the support matrix as data (`backends.py`, `resources/backends.json`, `docs/SUPPORT_MATRIX.md` generated by `tools/dev/support_matrix.py`); check P3 uses it.
- Phase 0 as guided procedures (`phase0.py`, `resources/phase0_tests.json`): 26 tests, results recorded, gate G0 recordable from them.
- A run window (UTC start and stop), a preview of a reverse, and putting the whole run back (`engine/runner.py`).
- `Dockerfile` (written, not built), `docs/NETWORK.md` with a test, `docs/COVERAGE.md`.
- The **gated wizard** (`wizard.py`): a step opens only when every earlier step is done, the selection must be confirmed for the exact pre-flight run and volume ids, a real run needs a dry run that covered the same volumes. Enforced by the server, shown by the page.
- Access levels: OpenStack APIs only (default) or APIs, hosts and arrays.
- Demonstration mode (three scenarios, simulated migration) and lab mode (a real OpenStack in WSL, a seeded catalogue of 26 volumes and 8 servers, Horizon sign-in, graceful stop).
- Class C, the staged method (`engine/staged.py`), the data mover and the Cinder helper, SSH, the Hosts tab, the `lvm` adapter and a fake fabric; diagnostics and redaction for dark sites. Run against the real lab: a detached and an attached volume with a snapshot moved end to end (detached data identical), R2 on real Cinder, a resume after a crash.
- 403 automated tests; the real engine has moved detached, attached and boot volumes in the lab and reversed two of them.
- Documentation: a walkthrough with screenshots and diagrams, every option, architecture, installation, operation, user guide, process, every call, safety case, classes, workload risks, playbook trace (v1.2 and v1.3), lab.

## 6. What Is Partially Done

- Classification without database access cannot check `provider_id` or snapshot rotation. A volume with snapshots is then class C ("rotation unknown"). Importing the playbook's query results closes the gap. The class B path (wait for rotation) is covered by mock tests only.
- Playbook step 1.7 (internal-tenant volumes): the image-cache volume is found; others are not.
- The failure paths of the engine are tested against a mock and the fake fabric with fault injection. In the lab: the failures listed in `LAB.md`; a copy killed in the middle and a mover killed are only in the fake fabric.
- Class C is proven on LVM only. The SolidFire and FlashArray adapters are not written; Queens and a customer cloud were never reached.

## 7. What Is Not Started

- The SolidFire and FlashArray array adapters, direct database access, PDF reports.
- Container image and own Linux image. The standalone program is built by `tools/build/build_exe.py` but has not been run on a machine without Python.
- Authentication and roles for the interface.

## 8. Known Issues / Bugs

- **Nothing has run against a customer cloud.** No customer release has a recorded lab validation; the Queens profile is `untested`.
- The lab proves the engine against a recent release (2025.1) with LVM back-ends. It says nothing about Queens, SolidFire or FlashArray.
- The lab's `stack.sh` may stop at its last step (`create_userrc.sh`) with an address error. The cloud is complete when that happens (`docs/LAB.md`).
- Open questions for the owner (`docs/DESIGN.md` section 9): the SSH client, report formats beyond Word, interface authentication and roles, whether medium-risk volumes should also be blocked.

## 9. Code Quality Observations

Standard library only (a test enforces it). The standalone program contains the CPython runtime and the PyInstaller bootloader, listed in `THIRD_PARTY_NOTICES.md`.

## 10. Version History Summary

| Version | Date | Summary |
|---|---|---|
| 0.1.0 | 2026-11-02 | Project scaffold. |
| 0.2.x | 2026-11-02 | Requirements R1-R11, migration method, design. |
| 0.3.0 | 2026-11-02 | Version detection, read-only inventory, local web interface, journal, tests. |
| 0.4.x | 2026-11-02 | Checks, gate and reports; audit; demonstration mode; safety layer and the order of a migration; selection, workload risk, unmigrated reports; per-volume gates G2, G3, G4; wizard Back and Next. |
| 0.5.0 | 2026-11-02 | The retype engine for class A and B (APIs only); access levels; the lab. |
| 0.6.0 | 2026-11-03 | First runs against a real OpenStack lab: Horizon sign-in, graceful stop, a seeded catalogue; fixes found there (the way back is locked, the image-cache volume, volumes already on the target, the Nova lock state). |
| 0.6.1 | 2026-11-03 | Documentation: architecture overview, installation guide and operation guide; README, CONTEXT, user guide and lab troubleshooting brought up to date. |
| 0.7.0 | 2026-11-03 | The gated wizard (server-enforced), selection confirmation, dry run before a real run; detection of a silently undone retype; identity fields in the post-check; the lab sign-in and Connect form pre-filled; a walkthrough with screenshots, an options reference, playbook v1.3 compared. |
| 0.8.0 | 2026-11-03 | Gap closure: update and after reports, Excel and CSV, the support matrix, Phase 0 as guided procedures, run window, whole-run reverse, Dockerfile (not built), lab failure injection. |
| 0.9.0 | 2026-11-03 | Class C: the staged method, the data mover and the Cinder helper, SSH, hosts, diagnostics and redaction for dark sites, the lab hosts made without a terminal; proven on LVM in the lab. |

Full history is in `CHANGELOG.md` and the git log.

## 11. Logical Next Steps

1. Run the packaged program and the container image on clean machines, first with `--demo`; run the tests on macOS.
2. A power cut on a real host as a failure test.
3. Phase 0 on the customer's cloud (the tests are guided and recorded; a person runs them), so a lab validation (gate G0) can be recorded from them.
4. Owner decisions: report formats (PDF), interface authentication and roles, whether to block medium-risk volumes. Write the SolidFire and FlashArray adapters (`arrays/base.py` is the contract; `arrays/lvm.py` the model) as soon as an array is reachable, and repeat the lab proofs there.
5. A test-environment deployment of the packaged program, run first as a dry run.

