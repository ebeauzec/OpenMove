# OpenMove — OpenStack Cinder Volume Automation

OpenMove is a GUI-driven tool that automates the audit, reporting and migration of OpenStack Cinder volumes. It connects directly to an OpenStack cloud and does its work by calling OpenStack's own services and clients, so every action is one the cloud already supports and records.

> **Status:** early development (version 0.3.0). Version detection, the read-only inventory with volume classification, and the local web interface work and are tested against a mock cloud. Checks, reports and migration are not built yet, and nothing has run against a real cloud. Until a release has a recorded lab validation, the tool is read only.

## Table of Contents

1. [What It Does](#1-what-it-does)
2. [How It Works](#2-how-it-works)
3. [Getting Started](#3-getting-started)
4. [Repository Layout](#4-repository-layout)
5. [Documentation](#5-documentation)
6. [Development](#6-development)
7. [Data Handling](#7-data-handling)
8. [License and Ownership](#8-license-and-ownership)

---

## 1. What It Does

| Area | Purpose |
|---|---|
| **Audit** | Inventory Cinder volumes across projects and backends, and report their state, attachment, type, size and placement. |
| **Reporting** | Verbose, enterprise-standard reports: every pre-migration check, the volumes to be moved, and progress and update reports for the operations team. |
| **Migration** | Plan and run volume moves between back-ends, with checks before and after each move and rollback of every action. |

Back-end support is broad by design, with a focus on moves to and from NetApp SolidFire, NetApp ONTAP and Pure Storage. Each back-end pair is listed in a support matrix with its migration method and verification status.

The detailed behaviour of each area is defined by the specification in `docs/`.

## 2. How It Works

- **GUI first.** Every function is reachable from the interface; nothing needs a command line.
- **Direct OpenStack integration.** OpenMove authenticates against Keystone and drives Cinder (and the services it depends on) directly. It does not keep its own copy of cloud state beyond what a report or a running job needs.
- **Standalone and on site.** One self-contained application that runs without internet access and contacts OpenStack only.
- **Platform independent.** Runs on Windows, macOS and Linux, as a packaged application, a container or from source. Work that needs Linux block-device tools runs on the remote migration host through a helper OpenMove ships and starts there.
- **Containerised, and controlled entirely from the tool.** Runs as a container, or from its own Linux image where needed. The operator points it at a migration host and does every step, from preparing the arrays and the host to the last report, from the interface. No terminal is required.
- **Version aware.** It detects the OpenStack release and the versions of the services around it, and follows the procedure that release needs. A release it has not been validated against is read only.
- **Safe by design.** Every change is journaled with its undo, so any action can be rolled back; a crash or lost connection never leaves a volume in an unknown state.
- **Read before write.** Audit and reporting are read-only. Migration is the only function that changes the cloud, and it shows what it will do before it does it.

## 3. Getting Started

**Prerequisite:** Python 3.9 or later (developed and tested on 3.12). OpenMove uses the standard library only; nothing else is installed.

```bash
python -m openmove
```

OpenMove starts a web server on the loopback address, prints a link that holds a one-time access token and opens it in the default browser. Use `--no-browser` to only print the link, `--port` to change the port (default 8765) and `--home` to choose where the journal is kept.

1. **Connect:** enter the Keystone address and an administrator account. The password stays in memory and is never written to disk, the journal or a report.
2. **Platform:** detect the OpenStack release, the API versions, the back-ends, the Cinder services and the compute hosts.
3. **Inventory:** read the volumes through the APIs and see them by class (A to E). If the Cinder database is not reachable, import the playbook's query results instead.
4. **Journal:** review what was recorded.

A packaged application and a container image will follow.

## 4. Repository Layout

| Path | Contents |
|---|---|
| `README.md` | This file: what the project is and how to use it. |
| `CONTEXT.md` | Architecture, file inventory, what is complete, partial and not started. |
| `CHANGELOG.md` | Release history. |
| `version.json` | Current version. |
| `LICENSE` | Proprietary software licence. |
| `LEGAL.md` | Ownership and intellectual property declaration. |
| `THIRD_PARTY_NOTICES.md` | Licences of third-party components and trademark notices. |
| `openmove/` | The application (Python, standard library only). |
| `tests/` | Automated tests and a mock OpenStack cloud. |
| `docs/` | Specification, design and operating documents. See `docs/README.md`. |
| `tools/` | Repository tooling, including the sensitive-data guard. |
| `tools/dev/` | Developer helpers (release bump). |
| `.githooks/` | Git hooks that enforce the rules in section 7. |
| `data/` | Local runtime data. Never committed. |

## 5. Documentation

Everything in the project is documented, and the documentation changes in the same commit as the code it describes.

| Document | Describes |
|---|---|
| `README.md` | Purpose, usage and layout. |
| `CONTEXT.md` | Architecture and project state. |
| `CHANGELOG.md` | What changed in each version. |
| `docs/` | Specification and detailed design. |

## 6. Development

See `docs/DEVELOPMENT.md` for the workflow: hooks, the guard, versioning and the release helper.

## 7. Data Handling

OpenMove works with real cloud data (project names, host names, addresses, volume identifiers). None of it may reach this repository. Credential files, exports and reports stay on the machine that produced them, the git hooks and `tools/guard_sensitive.py` block them at commit and push, and examples in documents and tests use placeholders only.

The sole author of this repository is its owner.

## 8. License and Ownership

OpenMove is proprietary software. Copyright © 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved. Personal and non-commercial use is free; commercial use, redistribution and derivative works need the owners' prior written consent. See [LICENSE](LICENSE), [LEGAL.md](LEGAL.md) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
