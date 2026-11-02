# Changelog

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
