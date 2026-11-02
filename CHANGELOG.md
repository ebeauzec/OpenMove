# Changelog

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
