# OpenMove documentation

| Document | Contents |
|---|---|
| `WALKTHROUGH.md` | **Start here.** The process from the first click to the last, with screenshots and diagrams: the nine gated steps, what each does, who does what, what unlocks the next, what happens to each volume, and what to do when something goes wrong. |
| `COVERAGE.md` | The tool checked against the requirements R1 to R18 and the proposal: what is met, what is partly built, what is not built and what would close it. |
| `SUPPORT_MATRIX.md` | Back-end families, how each is recognised, which pairs are verified, generated from `openmove/resources/backends.json`. |
| `NETWORK.md` | Every network destination of the program, and what it never does. A test keeps it complete. |
| `OPTIONS.md` | Every option and setting in one place: launchers, command line, modes, each screen's fields, the settings with defaults and limits, the gates, and the fixed values. |
| `ARCHITECTURE.md` | How OpenMove is built today: context, layers, runtime, the order of a move with diagrams, the safety architecture, data, modes, tests and what is not built. Start here to understand the code. |
| `INSTALL.md` | Requirements, installing from source or as a packaged program, checking the installation, data folders, upgrading and removing. |
| `RECOVERY.md` | If OpenMove, the computer or the network stops in the middle of a move: what protects the move (journal, saved work, supervisor, retrying loop, guardian, one program per data folder), what the screen shows, and what is not covered. |
| `OPERATIONS.md` | How to run a migration, step by step: roles, preparation, the procedure from Connect to Soak, the way back, halting and restarting, what every note means, checklists. |
| `DEVELOPMENT.md` | Development workflow: hooks, the sensitive-data guard, versioning, releases. |
| `REQUIREMENTS.md` | Product requirements from the owner: standalone, minimal communication, control, checks, resilience, rollback. |
| `MIGRATION_METHOD.md` | The migration method taken from the owner's playbook: classes, phases, gates, rollback, check catalogue, evidence. |
| `USER_GUIDE.md` | How to start OpenMove (launchers, standalone program, source), the demonstration scenarios, a tour of every screen, the pre-flight and gate, reports, halt, options, troubleshooting and a glossary. |
| `PROCESS.md` | The process OpenMove follows, step by step: reading, audit, pre-flight, gate, and the fixed order of a migration with its failure handling. For review. |
| `API_CALLS.md` | Every call OpenMove makes to a cloud, every call its local interface accepts and every journal event, with the rules each must obey. For review. |
| `CLASSES.md` | Each volume class: what is enforced today, what the engine must still do, and why a volume might not be migrated. |
| `WORKLOAD_RISKS.md` | Workloads that may fail this migration (legacy Windows, clusters, databases and more), how sure we are of each, whether OpenMove can see it, and what it does. |
| `DEPLOYMENT.md` | What to install where so that class A, B and C moves work in production: the migration host and the Cinder host, the setup script, the rights, the network, the checklist. |
| `MOVER.md` | Class C in practice: the migration host and the Cinder host, SSH (keys only, pinned host keys), the data mover and the Cinder helper, the host checks, and what each one writes. |
| `DIAGNOSTICS.md` | For dark sites: the logs, the support bundle, the redaction levels (none, standard, strict) and how a bundle is read and decoded. |
| `LAB.md` | The OpenMove lab: a real OpenStack (DevStack) on this machine for the real engine, what it proves and what it does not, and how to use it. |
| `PLAYBOOK_TRACE.md` | Playbook v1.2 and v1.3 step by step: what OpenMove covers, what is only checked, what is not built, and the gaps. |
| `ENGINE_PLAN.md` | The plan for building the migration engine for a test environment: milestones, what fakes can prove, what only a lab can prove, and what the environment must provide. |
| `SAFETY.md` | The safety case: hazards, the control for each, where it is in the code, which test proves it, and what is not yet built. |
| `DESIGN.md` | The original proposal (partly built; `ARCHITECTURE.md` describes what exists):  components, back-end adapters, version detection and release profiles, step contract, open questions. |

Every document in this folder is kept in step with the code. A change that alters behaviour updates the matching document in the same commit.
