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
| `docs/REQUIREMENTS.md` | Product requirements R1-R11 from the owner. |
| `docs/MIGRATION_METHOD.md` | The migration method (from the owner's playbook): classes, phases, gates, rollback, check catalogue. |
| `docs/DESIGN.md` | Proposed architecture and open questions. |
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
| `openmove/connectors/httpclient.py` | Minimal HTTP client on the standard library: timeouts, TLS options, size cap, no tokens in errors. |
| `openmove/connectors/openstack.py` | Keystone v3 session, service catalogue, paging, one re-authentication on 401. The password lives in memory only. |
| `openmove/discovery.py` | Detects identity, block storage and compute versions, release, profile status, back-end pools, Cinder services and compute hosts. |
| `openmove/classification.py` | Volume classes A-E from the playbook rules; states what it could not check. |
| `openmove/exports.py` | Reads the playbook's query exports for clouds where the database is not reachable. |
| `openmove/inventory.py` | Collects volumes, snapshots and servers through the APIs and builds the classified inventory. |
| `openmove/server.py` | Local web server: per-run access token, Host header check, security headers, JSON routes. |
| `openmove/resources/releases.json` | Microversion table per release, with source and retrieval date. |
| `openmove/resources/release_profiles.json` | Per-release behaviour (Queens profile from the playbook). |
| `openmove/resources/web/` | The interface: `index.html`, `styles.css`, `app.js`. Text is inserted as text, never as markup. |
| `tests/mock_openstack.py` | A mock Keystone, Cinder and Nova cloud with made-up data. |
| `tests/test_core.py` | Tests for release inference, profiles, journal, classification and exports. |
| `tests/test_cloud.py` | Tests for discovery, inventory and the web server against the mock cloud. |

## 4. Interface

A local web interface, started with `python -m openmove`. It listens on the loopback address only, and the page and every call need the per-run token printed at start. Tabs: **Connect** (Keystone details; the password stays in memory), **Platform** (release, API versions, back-ends, services, compute hosts, read-only status), **Inventory** (read from the cloud or imported from the playbook's query results; counts by class, filter and search), **Journal** (recorded events and unresolved steps).

## 5. What Is Complete

- Project scaffold, documentation set, versioning and release helper.
- Repository safeguards: hooks and sensitive-data guard.
- Release detection from API versions, with a table verified against the OpenStack documentation, and release profiles that keep an unvalidated release read only.
- Read-only inventory and classification (classes A-E) through the OpenStack APIs, or from imported playbook exports.
- Local web interface for connecting, detecting the platform, reading the inventory and viewing the journal.
- Journal with write-ahead steps and recorded inverses (the engine that later steps use).
- 57 automated tests against a mock cloud.

## 6. What Is Partially Done

- Classification without database access cannot check `provider_id` or snapshot rotation; the result lists these as not checked. Importing the playbook's query results closes the gap.

## 7. What Is Not Started

- Check framework (P, V, S, C, X catalogue), before and after reports and the Word renderer.
- Any step that changes the cloud: class A move, staged migration, rollback runner.
- Data mover helper, migration host registry, SSH, array adapters.
- Container image, own Linux image, packaging as a single executable.

## 8. Known Issues / Bugs

- Nothing has run against a real cloud yet; the Queens profile is `untested` until a lab validation is recorded.
- The authentication model for the interface, the SSH client and the report formats beyond Word are still open questions (`docs/DESIGN.md` section 9).

## 9. Code Quality Observations

Standard library only, so there is nothing to add to `THIRD_PARTY_NOTICES.md`.

## 10. Version History Summary

| Version | Date | Summary |
|---|---|---|
| 0.1.0 | 2026-11-02 | Project scaffold. |
| 0.2.x | 2026-11-02 | Requirements R1-R11, migration method, design. |
| 0.3.0 | 2026-11-02 | Version detection, read-only inventory, local web interface, journal, tests. |

Full history is in `CHANGELOG.md` and the git log.

## 11. Logical Next Steps

1. Owner confirms the remaining open questions in `docs/DESIGN.md` section 9 (SSH client, report formats, authentication).
2. Check framework and the before report, with the Word renderer.
3. Class A move with rollback, then the staged migration, the data mover helper and the container image.
