# Changelog

## 0.7.0 (2026-11-03)

### Added
- Gated wizard enforced by the server (`wizard.py`): a step opens only when every step before it is done; the selection must be confirmed for the exact pre-flight run and volume ids; a real run is refused until a dry run covered the same volumes; changing the selection or running the pre-flight again closes the later steps
- Detection of a retype that Cinder accepted and silently undid (seen in the lab with a volume that has a snapshot): rollback with the reason recorded
- Post-check of the identity fields (creation time, project, owner, snapshot id, source volume id, availability zone, multi-attach flag)
- Lab: the Connect form is pre-filled, the dashboard link uses the lab's own host name, the lab's admin password is simply `password`
- Documentation: `docs/WALKTHROUGH.md` (the process with screenshots and diagrams), `docs/OPTIONS.md` (every option and setting), `tools/dev/screenshots.py` (takes the screenshots from the real interface); playbook v1.3 compared in `docs/PLAYBOOK_TRACE.md`; safety rows 36 to 40; README, user guide, operations guide, architecture, lab guide and CONTEXT brought up to date

### Fixed
- The engine started nothing, without saying why, when asked for class C volumes: it now refuses with a message
- The inventory step did not refresh the wizard state
- The demonstration banner contrast

## 0.6.1 (2026-11-03)

### Added
- Documentation: architecture overview, installation guide, operation guide; README, CONTEXT, user guide and lab troubleshooting brought up to date

## 0.6.0 (2026-11-03)

### Added
- Real-lab results: Horizon sign-in, graceful stop, seeded catalogue, image-cache and lock fixes

## 0.5.0 (2026-11-02)

### Added
- The engine that moves class A and B volumes with Cinder's retype (lock, retype, follow, post-check, unlock, reverse), working with the OpenStack APIs only; the access level (api or full) with the new cause 'no data mover' for class C; the retype order in the lifecycle; the Move tab (dry run, run, sign G2 to G4, release, reverse) and the lab validation record (gate G0); lab mode and OpenMove-Lab.bat for a real OpenStack on this machine (DevStack in WSL). Tested against a mock cloud with fault injection (244 tests). Not yet run on the lab, and nothing has run on a customer cloud.

## 0.4.2 (2026-11-02)

### Added
- Engine build plan

## 0.4.1 (2026-11-02)

### Added
- Per-volume gates G2, G3 and G4 signed by a second person; wizard Back and Next buttons

## 0.4.0 (2026-11-02)

### Added
- Pre-flight checks and gate, environment audit, demo mode, safety layer, volume selection, workload risk, unmigrated-volume reports

## 0.3.0 (2026-11-02)

### Added
- Version detection, read-only inventory and local web interface

## 0.2.3 (2026-11-02)

### Changed
- Design: a Cinder host can act as the migration host, with conditions.

## 0.2.2 (2026-11-02)

### Added
- Requirement R11: container image, own Linux deployment, migration host registry, every phase controlled from the tool.

## 0.2.1 (2026-11-02)

### Added
- Requirement R10 (runs on Windows, macOS and Linux); design split into control plane and data mover.

## 0.2.0 (2026-11-02)

### Added
- Added MIGRATION_METHOD.md and DESIGN.md from the playbook; requirement R9 (detect the OpenStack version and act accordingly).

## 0.1.3 (2026-11-02)

### Added
- Requirement R7 extended: before/after report pair, update reports, Word renderer.

## 0.1.2 (2026-11-02)

### Added
- Requirements R7 (verbose enterprise-standard reporting) and R8 (broad back-end support) added.

## 0.1.1 (2026-11-02)

### Added
- Added docs/REQUIREMENTS.md (standalone, minimal communication, control, checks, resilience, rollback).

## 0.1.0 (2026-11-02)

### Added
- Project scaffold: README, CONTEXT, `docs/`, version file, git hooks and the sensitive-data guard (`tools/guard_sensitive.py`).
- Release helper `tools/dev/release.py`.
