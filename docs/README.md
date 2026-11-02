# OpenMove documentation

| Document | Contents |
|---|---|
| `DEVELOPMENT.md` | Development workflow: hooks, the sensitive-data guard, versioning, releases. |
| `REQUIREMENTS.md` | Product requirements from the owner: standalone, minimal communication, control, checks, resilience, rollback. |
| `MIGRATION_METHOD.md` | The migration method taken from the owner's playbook: classes, phases, gates, rollback, check catalogue, evidence. |
| `USER_GUIDE.md` | How to start OpenMove (launchers, standalone program, source), the demonstration scenarios, a tour of every screen, the pre-flight and gate, reports, halt, options, troubleshooting and a glossary. |
| `PROCESS.md` | The process OpenMove follows, step by step: reading, audit, pre-flight, gate, and the fixed order of a migration with its failure handling. For review. |
| `API_CALLS.md` | Every call OpenMove makes to a cloud, every call its local interface accepts and every journal event, with the rules each must obey. For review. |
| `CLASSES.md` | Each volume class: what is enforced today, what the engine must still do, and why a volume might not be migrated. |
| `WORKLOAD_RISKS.md` | Workloads that may fail this migration (legacy Windows, clusters, databases and more), how sure we are of each, whether OpenMove can see it, and what it does. |
| `PLAYBOOK_TRACE.md` | Playbook v1.2 step by step: what OpenMove covers, what is only checked, what is not built, and the gaps. |
| `SAFETY.md` | The safety case: hazards, the control for each, where it is in the code, which test proves it, and what is not yet built. |
| `DESIGN.md` | Proposed architecture: components, back-end adapters, version detection and release profiles, step contract, open questions. |

Every document in this folder is kept in step with the code. A change that alters behaviour updates the matching document in the same commit.
