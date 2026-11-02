# CONTEXT.md — OpenMove

Architecture and project state. Updated with every change that alters either.

## 1. What This Project Is

OpenMove is a GUI-driven tool that automates the audit, reporting and migration of OpenStack Cinder volumes. It integrates directly with OpenStack and carries out its work by calling OpenStack services and clients.

Product requirements are in `docs/REQUIREMENTS.md`: a standalone application deployable on site, communicating only with OpenStack, with maximum control over migration, verbose enterprise-standard reporting that doubles as the operations team's progress report, checks before, during and after migration, resilience to any failure, rollback of every action, and support for as many Cinder back-ends as possible (focus: NetApp SolidFire, NetApp ONTAP, Pure Storage).

The functional specification (the playbook) is supplied by the owner and is stored in `docs/`. Where this file and the specification disagree, the specification wins and this file is corrected.

## 2. Architecture

Proposed in `docs/DESIGN.md` (awaiting the owner's confirmation of the open questions there): a single Python application with an embedded SQLite journal, a local web GUI, connectors for the OpenStack APIs, the Cinder database, SSH and the arrays, back-end adapters with capability profiles, a version-detecting discovery module with per-release profiles (R9), a step engine with write-ahead journal and a recorded inverse for every step, a check framework, and a report model rendered to Word and other formats. The method it automates is summarised in `docs/MIGRATION_METHOD.md`.

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
| `docs/REQUIREMENTS.md` | Product requirements R1-R9 from the owner. |
| `docs/MIGRATION_METHOD.md` | The migration method (from the owner's playbook): classes, phases, gates, rollback, check catalogue. |
| `docs/DESIGN.md` | Proposed architecture and open questions. |
| `docs/DEVELOPMENT.md` | Development workflow, hooks, guard and release steps. |
| `tools/guard_sensitive.py` | Blocks credentials, cloud data and disallowed wording at commit and push. |
| `tools/dev/release.py` | Version bump and changelog entry. |
| `.githooks/commit-msg` | Strips any assistant trailer from a commit message. |
| `.githooks/pre-commit` | Runs the guard on staged files. |
| `.githooks/pre-push` | Runs the guard on unpushed commits and checks authorship. |
| `data/README.md` | Explains that `data/` is local only. |

## 4. Interface

Not yet defined.

## 5. What Is Complete

- Project scaffold, documentation set, versioning and release helper.
- Repository safeguards: hooks and sensitive-data guard.

## 6. What Is Partially Done

Nothing.

## 7. What Is Not Started

- Everything in the specification: OpenStack connection, audit, reporting, migration, interface.

## 8. Known Issues / Bugs

None recorded.

## 9. Code Quality Observations

None yet.

## 10. Version History Summary

| Version | Date | Summary |
|---|---|---|
| 0.1.0 | 2026-11-02 | Project scaffold. |

Full history is in `CHANGELOG.md` and the git log.

## 11. Logical Next Steps

1. Owner confirms the open questions in `docs/DESIGN.md` section 9 (components, where it runs, stack, report formats, authentication).
2. Build the platform discovery and release profile module, then read-only inventory and classification.
3. Then the check framework, the before report, the class A move with rollback, and the staged migration.
