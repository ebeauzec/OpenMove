# CONTEXT.md — OpenMove

Architecture and project state. Updated with every change that alters either.

## 1. What This Project Is

OpenMove is a GUI-driven tool that automates the audit, reporting and migration of OpenStack Cinder volumes. It integrates directly with OpenStack and carries out its work by calling OpenStack services and clients.

Product requirements are in `docs/REQUIREMENTS.md`: a standalone application deployable on site, communicating only with OpenStack, with maximum control over migration, verbose enterprise-standard reporting that doubles as the operations team's progress report, checks before, during and after migration, resilience to any failure, rollback of every action, and support for as many Cinder back-ends as possible (focus: NetApp SolidFire, NetApp ONTAP, Pure Storage).

The functional specification (the playbook) is supplied by the owner and is stored in `docs/`. Where this file and the specification disagree, the specification wins and this file is corrected.

## 2. Architecture

Proposed in `docs/DESIGN.md` (awaiting the owner's confirmation of the open questions there): platform independent (R10), available as a container and as its own Linux image, pointed at a migration host and controlling every phase from the interface (R11), split into a control plane that runs on any operating system and a data mover helper that runs on a Linux migration host over SSH; a single Python code base with an embedded SQLite journal, a local web GUI, connectors for the OpenStack APIs, the Cinder database, SSH and the arrays, back-end adapters with capability profiles, a version-detecting discovery module with per-release profiles (R9), a step engine with write-ahead journal and a recorded inverse for every step, a check framework, and a report model rendered to Word and other formats. The method it automates is summarised in `docs/MIGRATION_METHOD.md`.

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
| `docs/REQUIREMENTS.md` | Product requirements R1-R18 from the owner (R13 safety, R14 the order, R15 unmigrated volumes, R16 selection, R17 workload risk, R18 per-volume process). |
| `docs/CLASSES.md` | What is handled for each volume class, and why a volume may not be migrated. |
| `docs/WORKLOAD_RISKS.md` | Workloads that may fail the migration and how OpenMove identifies them (check V11). |
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
| `tests/mock_openstack.py` | A mock Keystone, Cinder and Nova cloud with made-up data. |
| `tests/test_core.py` | Tests for release inference, profiles, journal, classification and exports. |
| `tests/test_cloud.py` | Tests for discovery, inventory and the web server against the mock cloud. |
| `tests/test_checks.py`, `test_demo.py`, `test_safety.py`, `test_lifecycle.py`, `test_docs_sync.py`, `test_self_contained.py` | Checks and gate; scenarios, simulation and reports; the safety rules; the order of a migration; documentation in step with the code; standard library only. |

## 4. Interface

A local web interface, started with `python -m openmove`. It listens on the loopback address only, and the page and every call need the per-run token printed at start. Tabs: **Connect** (Keystone details; the password stays in memory), **Platform** (release, API versions, back-ends, services, compute hosts, read-only status), **Inventory** (read from the cloud or imported from the playbook's query results; counts by class, filter and search), **Audit** (the environment audit), **Pre-flight** (checks, attestations, sign-off, the gate), **Reports** (Word), **Live** (progress meter, the order, the gate, the event feed), **Journal** (recorded events and unresolved steps). The header carries the HALT button. Every table sorts by clicking a column header. In demonstration mode a banner offers three scenarios.

## 5. What Is Complete

- Project scaffold, documentation set, versioning and release helper.
- Repository safeguards: hooks and sensitive-data guard.
- Release detection from API versions, with a table verified against the OpenStack documentation, and release profiles that keep an unvalidated release read only.
- Read-only inventory and classification (classes A-E) through the OpenStack APIs, or from imported playbook exports.
- Local web interface: connect, platform, inventory, audit, pre-flight, reports, live view, journal, halt; sortable tables.
- The check framework (P, V, S, C, X catalogue): every check that can run, honest "not checked" for the rest, attestations, four-eyes sign-off and a fail-closed gate.
- Environment audit (22 rules) and Word reports (pre-migration report with the audit as a separate part).
- Safety layer: read-only client, permit, halt switch, and the lifecycle (the fixed order and the cutover conditions).
- Demonstration mode with three scenarios (SolidFire to Pure, SolidFire to ONTAP, a large enterprise cloud) and a simulated migration run.
- Launchers, and a build script for a standalone program with nothing to install.
- Documentation of the process and every call, kept in step with the code by a test.
- Journal with write-ahead steps and recorded inverses (the engine that later steps use).
- About 135 automated tests against a mock cloud.

## 6. What Is Partially Done

- Classification without database access cannot check `provider_id` or snapshot rotation; the result lists these as not checked. Importing the playbook's query results closes the gap.

## 7. What Is Not Started

- The after report and the before/after comparison.
- Any step that changes the cloud: class A move, staged migration, rollback runner.
- Data mover helper, migration host registry, SSH, array adapters.
- Container image and own Linux image. The standalone program is built by `tools/build/build_exe.py` but has not been run on a machine without Python.
- Lab validation of any release. Nothing has run against a real cloud.

## 8. Known Issues / Bugs

- Nothing has run against a real cloud yet; the Queens profile is `untested` until a lab validation is recorded.
- The authentication model for the interface, the SSH client and the report formats beyond Word are still open questions (`docs/DESIGN.md` section 9).

## 9. Code Quality Observations

Standard library only (a test enforces it). The standalone program contains the CPython runtime and the PyInstaller bootloader, listed in `THIRD_PARTY_NOTICES.md`.

## 10. Version History Summary

| Version | Date | Summary |
|---|---|---|
| 0.1.0 | 2026-11-02 | Project scaffold. |
| 0.2.x | 2026-11-02 | Requirements R1-R11, migration method, design. |
| 0.3.0 | 2026-11-02 | Version detection, read-only inventory, local web interface, journal, tests. |
| 0.4.0 | 2026-11-02 | Checks, gate and reports; environment audit; demonstration mode and scenarios; safety layer and the order of a migration; live view; launchers and standalone build; documented process and calls. |

Full history is in `CHANGELOG.md` and the git log.

## 11. Logical Next Steps

1. Owner confirms the remaining open questions in `docs/DESIGN.md` section 9 (SSH client, report formats, authentication).
2. The migration engine, built on `safety.py` and `lifecycle.py`: class A move with rollback, then the staged migration and the data mover helper; fault-injection tests for every step that changes anything (`docs/SAFETY.md`).
3. Lab validation of a release; array and database access for the checks that are "not checked" today; the container image.
