# Design

Initial design, derived from `REQUIREMENTS.md` and the method in `MIGRATION_METHOD.md`. It is a proposal until the owner confirms the open questions in section 9.

## 1. What the playbook changes about the requirements

The playbook is a staged migration that touches more than the OpenStack API. To move a volume with snapshots it needs:

| Component | Used for | Access |
|---|---|---|
| OpenStack APIs (Keystone, Cinder, Nova) | Inventory, type and quota changes, instance lock, volume swap, completion call. | HTTPS, admin |
| Cinder database | Inventory queries; a small named set of writes (freeze, unfreeze). | MySQL, read and write |
| Cinder host | Runs a helper that creates and syncs the destination record through Cinder's own object layer, and runs `cinder-manage`. | SSH |
| Compute hosts | Read-only hypervisor inspection (`virsh`, multipath), block-job throttle, domain repair. | SSH, root |
| Source array (SolidFire) | Snapshots, clones, access groups, deletes. | HTTPS (management VIP) |
| Target array (FlashArray) | Staging volume, named snapshots, rename, recover, destroy. | HTTPS or SSH |
| Migration host | Presents clones and the staging volume over iSCSI and copies and compares block devices. | Local or SSH |

This goes further than "ideally only OpenStack" (R2). The requirement stands as written, and the rule that follows from it is that OpenMove contacts only the components a chosen method needs, and each is declared. Class A and class B moves (standard retype or migrate) need only the OpenStack APIs and the database. Class C (staged) needs everything above. The communication inventory is generated from the method in use and printed in the plan and the report.

## 2. Where it runs (R10)

OpenMove runs on a local system of any operating system: Windows, macOS or Linux. It is split in two so that this holds:

- **Control plane.** The application itself: GUI, engine, checks, reports, journal, connectors. Pure Python, no operating-system-specific tools. Runs wherever the operator is.
- **Data mover.** The part that must touch block devices: presenting clones and the staging volume over iSCSI, rescanning multipath, `dd`-style copy, byte compare, sampled compare, delta copy. This needs Linux with iSCSI and multipath, so it runs on the migration host. OpenMove ships it as a small stdlib-only Python helper, sends it over SSH on stdin (the playbook already ships its record helper this way) and starts it there. Nothing is installed on the migration host. The control plane talks to it over the SSH channel with a small request and response protocol, and every operation is journaled on the control plane.

If the operator's machine is itself the Linux migration host, the data mover runs locally through the same interface. Class A and B moves and all inventory, checks and reports need no data mover.

The same split applies to the other remote tools the method uses: the destination-record helper runs on the Cinder host, and read-only hypervisor inspection runs on compute hosts, all as shipped helpers over SSH. The playbook's shell scripts are ported to Python so the same code runs on every operating system.

Delivery, as for ARIA: a packaged application per operating system, a container image, and source that runs with a plain Python install. The GUI is a web interface served on the local machine and opened in a browser (or in an embedded window where the packaged build provides one).

SSH from the operator's machine uses the operating system's OpenSSH client where present (current Windows, macOS and Linux all ship one), or a bundled pure-Python SSH implementation. Which is used is decided when the connector is built and recorded in `THIRD_PARTY_NOTICES.md`.

## 2a. Container, Linux deployment and the migration host (R11)

**Container image.** One image, two run modes:

| Mode | Purpose | Notes |
|---|---|---|
| Controller | Runs the control plane. Points at a remote migration host over SSH. | Needs only outbound network access to the cloud, the arrays and the migration host. No special privileges. This is the default. |
| Migration host | Runs the control plane and the data mover together, so the container host is the migration host. | Needs access to the host's iSCSI and multipath stack and to block devices (host networking, device access, the host's iSCSI daemon). Privileged access is required and the image's documentation says exactly which. |

The image is built from a pinned base, holds no credentials and no data, and keeps its state in one mounted volume (journal, settings, reports). The composition file starts either mode. Whether the migration-host mode is reliable enough to ship is a point for lab validation, because iSCSI sessions and multipath devices live in the host kernel and not in the container.

**Own Linux deployment.** If the lab shows that the container's migration-host mode is not safe enough, or a site has no suitable Linux host, OpenMove ships a minimal Linux image (virtual machine image and installable media) with OpenMove, the data mover and the required iSCSI and multipath packages. It starts OpenMove as a service, exposes the GUI, and can serve as the migration host. Only the migration-host role needs it; the controller still runs anywhere.

**Pointing OpenMove at a migration host.** In the tool the operator adds a migration host: address, SSH access (key or password, held in memory and in the protected store, never in logs or reports), and its role. OpenMove then:

1. connects and records host facts (operating system, kernel, Python, initiator name);
2. runs the readiness checks: iSCSI initiator and daemon, multipath, block-device tools, free space for evidence, network reach to both arrays' data ports, clock, permissions;
3. shows what is missing and offers the fix as a plan step with its undo (for example installing a package, or starting a service), applied only on approval;
4. ships and starts the data mover, and proves it with a throwaway copy in both directions (check P8), recording the throughput;
5. registers the host's initiator on the arrays (access group on the source array, host object on the target array), as plan steps with undo.

**Everything from the tool.** Each phase of the method is a set of GUI functions built on the same step contract (section 6):

| Phase | What the tool does for the operator |
|---|---|
| Connect | Enter and test credentials for the cloud, the database, the arrays and the hosts; discover versions (section 5a). |
| Prepare (Phase 2) | Create the migration account, clone QoS and access group; create the host object; create volume types and set type access and quota; create the reference volume; take and test-restore the database backup; verify compute-host reach to the target array; record the volume-type decision. Things the tool does not own, such as the Cinder service configuration, are shown as exact required changes and checked afterwards. |
| Validate (Phase 0) | Run the lab tests T1 to T21 as guided, recorded procedures against a test project, store the results, and sign gate G0. |
| Discover (Phase 1) | Inventory, churn, attachments, classification, `provider_id` repair, batch plan. |
| Migrate (Phases 3 to 5) | Plans, gates, cutover, monitoring, rollback R1 to R3, soak-period cleanup. |
| Report | Before, update and after reports, check results and the evidence record. |

A terminal is never required. For support, the tool can show every command and call it issued, and export the journal.

## 3. Technology

Proposed: **Python** for the application, with the standard library first and a minimal set of bundled dependencies; an **embedded SQLite** database for the job journal; a **local web GUI** (HTML, CSS and JavaScript bundled in the package, no CDN); **packaged as a single executable** per operating system, plus a container image, so nothing is installed by hand. Python is also what makes R10 practical: one code base, no shell scripts, the same behaviour on Windows, macOS and Linux.

Reasons:
- The playbook's own tooling is shell and Python; the destination-record helper is Python 2 and 3 compatible by necessity and is shipped to the Cinder host as a payload.
- The Word renderer proven in ARIA is JavaScript that runs in the browser; a web GUI runs it unchanged and offline.
- It matches how ARIA is built and packaged, which the owner already operates.
- Python reaches every component through a stable API or SSH, with no compiler on the target.

Alternatives considered and not proposed: a compiled single binary (strong for packaging, no reuse of the report code, the helper still needs Python), and a desktop toolkit GUI (cannot run on a headless migration host).

## 4. Components

```
GUI (browser)  <->  HTTP API (localhost, authenticated)
                          |
        +-----------------+------------------+
        |                 |                  |
     Engine           Checks             Reports
  (plans, state      (one framework,     (report model,
   machine, journal)  all stages)         Word, PDF, Excel, CSV)
        |
   Connectors ---- OpenStack API | Cinder DB | SSH runner | Array adapters
```

- **Connectors.** One module per kind of remote system. All network and SSH access goes through them. Each call has a timeout, a validated response and a defined failure.
- **Back-end adapters.** One per array family, each with a capability profile (section 5).
- **Discovery and release profiles.** Detects the platform version at connection time and supplies the profile every other component consults (section 5a).
- **Engine.** Turns an approved plan into steps, runs them, and records everything in the journal.
- **Checks.** The catalogue in `MIGRATION_METHOD.md` section 8, as code. A check returns a record: ID, what it looked at, evidence, result, meaning.
- **Reports.** A report model built from the job record, rendered by separate writers.
- **GUI.** Inventory, classification, plan builder, job control, gate sign-off, reports, settings.

## 5. Back-end adapters

The engine never tests for a vendor name. It asks the adapter. The operations the staged method needs, taken from the playbook's command reference:

| Operation | Source adapter | Target adapter |
|---|---|---|
| Find a volume, read its size in bytes | yes | yes |
| List snapshots with sizes and times | yes | yes |
| Point-in-time readable copy (clone) of a snapshot or of the volume's current state | yes | for the reverse direction |
| Present and withdraw a volume to the migration host | yes | yes |
| Create a volume of an exact byte size | | yes |
| Take a snapshot with a given name | | yes |
| Rename a volume (snapshots follow) | | yes |
| Destroy, recover within the pending window, eradicate | | yes |
| List connections to a volume | yes | yes |
| Delete a clone and its access-group membership | yes | |

A capability profile says what a back-end can do, which migration methods exist for a pair (standard retype or migrate, staged), which are verified, and the limits (for example a per-volume snapshot limit, name length). A pair with no verified method is reported as unsupported with the reason.

Planned order: SolidFire to FlashArray (the playbook), then FlashArray to SolidFire (rollback R3 needs it anyway), then NetApp ONTAP in both directions, then others through the standard retype path first and the staged method where an adapter exists.

## 5a. Version detection and release profiles (R9)

**Discovery.** On connection OpenMove builds a platform profile before it plans anything:

| What | How |
|---|---|
| Identity, block storage and compute API versions and supported microversion ranges | API version discovery on each endpoint from the service catalogue. |
| Release | Inferred from the microversion ranges and service versions with a mapping table kept as data. Several signals are compared; if they point to different releases the profile says so and takes the oldest. |
| Service and client versions on hosts | With the SSH access the method needs: the Cinder, Nova and driver package versions, the hypervisor and QEMU versions, the interpreter that runs Cinder (this decides how the destination-record helper is launched), the clients installed on the migration host. |
| Back-end facts | Driver in use per back-end, array software and API versions, enabled options that change behaviour (for example eradicate-on-delete, name templates, volume-name prefix). |
| Attach flow | Which attach flow the cloud negotiates (old style or attachment-id style). |

The result is stored with the job and printed in the plan and in every report.

**Release profiles.** A profile is a data file per release (and per vendor build where a vendor patches the code) holding:

- capability flags (what the API and drivers support in that release);
- the command and call variants to use (for example which client has the live-migration listing, or whether a manage command for quota exists);
- the procedure variants that apply (attach flow, interpreter, how the destination helper is run);
- known deviations from upstream, and the checks that must pass before it is used;
- a **status**: `validated` (a lab run has been recorded in OpenMove for this release and these back-ends), or `untested`.

**Acting accordingly.** The engine and connectors read the profile; they never hard-code a release. Where the profile has a variant, that variant is used. Where it has none, the step is not attempted. A release without a `validated` profile is read-only: inventory, classification, checks and reports run, and every step that changes the cloud is refused with the reason, until a lab validation for that release is recorded (the G0 evidence).

The first profile is the release the playbook was written against. The validation of a vendor build against upstream behaviour is part of its lab validation.

## 6. The step contract

Every action the engine performs is a step with:

1. **Preconditions**, checked before it runs. A failed precondition stops the step.
2. **Intent**, written to the journal before the action starts (write-ahead).
3. **Action**, run through the single mutate function, which honours dry run.
4. **Verification**, a read-back that proves the action had the effect.
5. **Inverse**, the recorded way to undo it, or an explicit "cannot be undone".
6. **Idempotence key**, so a repeated step observes the existing result instead of repeating the action.

On restart the engine reads the journal, finds any step with an intent and no verified result, and resolves it by reading the real state of the cloud. An unresolved step blocks its volume and is reported; it is never guessed at. Rollback is a plan made of inverses, shown and approved like any other, and journaled the same way.

## 7. Control, gates and the four-eyes rule

- A plan is produced for a batch: volumes, class, method, order, concurrency, window, throttle settings. It is shown in full, including every step and its inverse, and the communication inventory.
- A plan runs only after approval. The approved plan is frozen and becomes the before report.
- Gates G0 to G4 are stops. A gate is signed by a user other than the operator; the GUI enforces this.
- A running job can pause at a gate or at a step boundary, resume, or stop. Stop never abandons a half-done step: it finishes or reverses it.
- Settings the playbook leaves open (compare mode, copy mode, clone QoS, stall and cutover time limits, soak period, concurrency per host) are settings with the playbook's values as defaults.

## 8. Safety rules carried over from the playbook

- Database writes are limited to the allowlist the playbook names; each is preceded by a read stored in the record and followed by a read to confirm, and an update that changes any row count other than the expected one stops the job.
- The destination record is created only through Cinder's object layer on the Cinder host, never by raw insert.
- The destination record is deleted only with `cinder-manage` and with `migration_status` left as `target:<id>`.
- An error from a lookup that guards a decision (for example the live-migration check) is a stop, never "none".
- An overwrite copy never skips zero blocks, and every image is compared after copying.
- The block job is never aborted by the tool; a stall is reported and a person decides.
- Every call to an array or to OpenStack that can hang has a timeout.
- Credentials are read from protected files or entered in the GUI and held in memory; they never appear in a process list, a log, a report or the journal.
- TLS verification is on by default; turning it off is a setting that is shown in the report.

## 9. Open questions for the owner

1. **Components.** Confirm that OpenMove may contact the components in section 1 for the staged method, and only the OpenStack APIs and database for class A and B.
2. **Where it runs.** Settled by R10 and R11: any operating system, a container, or a dedicated Linux image, with the data mover on a migration host that the tool is pointed at. Open: is there a Linux migration host at each site, as the playbook assumes, and may OpenMove start helpers on it over SSH and, with approval, install missing packages there? If a site has none, the OpenMove Linux image is the answer.
3. **Stack.** Confirm Python, local web GUI, single-executable packaging.
4. **Report formats.** Word is settled. Confirm PDF for archive and Excel for the volume lists.
5. **Authentication.** Local users with roles (operator, second pair of eyes, escalation, read-only), or integration with a directory?
6. **First build target.** Proposed: inventory and classification (read-only), then the check catalogue, then the before report, then the class A move with rollback, then class C.
7. **Test environment.** The scripts were never run against real systems. A lab with a SolidFire, a FlashArray and a Queens cloud is needed for validation. Until then the engine is tested against mocks of Element, Purity, Cinder and Nova, including fault injection.
