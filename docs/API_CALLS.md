# Calls reference

Every call OpenMove makes or accepts, in one place, so it can be reviewed line by line. Three groups:

1. **Outbound calls to the cloud** (section 2). In this release there are 17 distinct read calls and the login, a `POST` that creates a token and changes nothing.
2. **Outbound calls that change the cloud** (section 3). Three exist in this release (lock, unlock, retype), used only for class A and B volumes. The rules every one must obey are fixed here.
3. **The local interface** (section 4): the calls the browser makes to OpenMove on this machine.

Section 5 lists the events OpenMove writes to its journal. Section 6 lists everything it writes to disk.

A test (`tests/test_docs_sync.py`) fails when the code makes a call, serves a route or records an event that this file does not list. If you change a call, change this file in the same commit.

## 1. Rules for every outbound call

| Rule | How it is enforced |
|---|---|
| Reads only, by construction. | `Http.request` (`openmove/connectors/httpclient.py`) refuses every method except `GET` and `HEAD`, and the one login `POST`, unless the call carries a permit. It refuses before anything is sent. |
| A change needs a permit, checked again for each call. | `OpenStackSession.mutate` needs a `safety.Permit`. The permit re-checks the release profile, the pre-flight gate and the halt switch on every call. Any error while checking is a refusal. See `SAFETY.md`. |
| No retry of a change. | A change that times out or loses its connection is reported with `outcome_unknown`. The caller must read the state back before it does anything else. |
| Credentials stay in memory. | The password is kept in the session object only, cleared on disconnect, and never written to the journal, a log, an error message or a report. Tokens are sent only in the `X-Auth-Token` header, which never appears in an error. |
| Bounded. | Every call has a timeout (30 s by default) and a 32 MiB answer limit. A list that returns more than 1,000,000 items, or a page that does not advance, is an error. Lists are read in pages of 500. |
| TLS verified. | On by default. Turning it off is a per-connection choice that is shown on screen and stated in every report. A CA file can be given instead. |
| A missing right is not a failure. | Every call that needs an administrator right is optional: if the cloud answers 403 or 404, the result carries a warning and the parts that depended on it say "not checked". They are never reported as passed. |
| Nothing is sent anywhere else. | OpenMove contacts the cloud endpoints named in the Keystone service catalogue and nothing else: no telemetry, no update check, no external service. |

Every call below sends `Accept: application/json`. Calls to the services also send `X-Auth-Token`. Calls marked "unauthenticated" send no token.

## 2. Calls to the cloud that read

`{auth}` is the Keystone v3 URL (the address typed on the Connect tab, with `/v3` added if missing). `{block}` and `{compute}` are the endpoints from the service catalogue, chosen by service type (block storage: `volumev3`, `block-storage`, `volumev2`, `volume`, in that order; compute: `compute`) and by the interface and region on the Connect tab.

| ID | Call | Needs | Used for | If it fails |
|---|---|---|---|---|
| K1 | `GET {auth}` (unauthenticated) | nothing | Identity version document: the Keystone API version shown on the Platform tab. | Warning; identity shown as unavailable. |
| K2 | `POST {auth}/auth/tokens` | a valid user | The login. Sends the user name, domain, password and project scope. This `POST` creates a token and changes nothing in the cloud. | The connection is refused with the cloud's message (the password is not repeated). |
| K3 | `GET {auth}/projects` | administrator | Project names for the audit and the reports (volumes are listed by project id otherwise). | Warning; ids are shown instead of names. |
| B0 | `GET {block root}` (unauthenticated) | nothing | Block Storage version document: the highest API microversion, used to infer the OpenStack release. | Warning; the release is inferred from Compute alone, with lower confidence. |
| N0 | `GET {compute root}` (unauthenticated) | nothing | Compute version document: the highest microversion. | As B0. |
| B1 | `GET {block}/os-services` | administrator | Cinder services (scheduler, volume, backup): host, zone, enabled, up or down, last report. Feeds check P2, the copy-host plan (P8) and audit findings A1 and A2. | Warning; the checks that depend on it say "not checked". |
| B2 | `GET {block}/scheduler-stats/get_pools?detail=True` | administrator | Back-end pools: vendor, driver version, protocol, capacity, free, provisioned, over-subscription ratio, reserved percentage, replication and multiattach support. Feeds the back-end lists, checks P2 and V9, and audit findings A14 and A22. | Warning; no back-end list, capacity checks "not checked". |
| N1 | `GET {compute}/os-hypervisors/detail` | administrator | Compute hosts: hypervisor type and version, state, status, running instances. Feeds check P1's version record and audit findings A15 and the mixed-version note. | Warning; compute hosts not listed. |
| B3 | `GET {block}/volumes/detail?all_tenants=1&limit=500[&marker=<id>]` | administrator | The volume inventory: id, name, status, size, project, type, host and back-end, migration status, encryption, multiattach, group, replication, bootable, attachments, creation and update times, the snapshot and source volume it was created from, and a short list of image properties (`volume_image_metadata`: os_type, os_distro, os_version, image_name, hw_disk_bus, hw_qemu_guest_agent and a few more; nothing else from the image is kept). Feeds the inventory, classification, the audit, checks V1 to V10 and the workload-risk check V11. | The inventory stops with the cloud's message. Nothing partial is kept. |
| B4 | `GET {block}/snapshots/detail?all_tenants=1&limit=500[&marker=<id>]` | administrator | Snapshots: id, volume, status, size, creation time. Gives each volume its snapshot count, ages and sizes, and the audit its orphan and stuck snapshots. | As B3. |
| N2 | `GET {compute}/servers/detail?all_tenants=1&limit=500[&marker=<id>]`, with `OpenStack-API-Version` and `X-OpenStack-Nova-API-Version` when a compute microversion is given | administrator | Instances: state, task state, lock, host, project, whether they boot from an image, their name, flavor name and the same short list of image properties from their metadata. Gives each attached volume its instance state (class D, check V8) and finds probable boot volumes. | Warning "servers not read"; volumes are classified with the note that instance state is unknown. |
| B5 | `GET {block}/types` | any | Volume types and their extra specs (back-end name, QoS, features). Audit finding A19 and the report. | Warning. |
| B6 | `GET {block}/qos-specs` | administrator | QoS specifications. The audit and the report. | Warning. |
| B7 | `GET {block}/backups/detail?all_tenants=1&limit=500[&marker=<id>]` | administrator | Backups: status, size, age. Audit finding A18. | Warning. |
| B8 | `GET {block}/volumes/{id}` | administrator | One volume, read afresh by the engine before and after each step: status, migration status, back-end, type, size, attachments, bootable. | The engine does not continue with that volume; it looks again at the next tick. |
| B9 | `GET {block}/snapshots/detail?volume_id={id}&all_tenants=1` | administrator | The snapshots of one volume, read afresh before a move (class A and B are moved only with none). | As B8. |
| N3 | `GET {compute}/servers/{id}`, with compute microversion 2.9 so that the lock state is shown | administrator | One instance, read before a move (running, idle, locked) and after it. | As B8. |

Notes:

* B0, N0 and K1 are the unauthenticated version documents. The login (K2) is the only `POST`.
* The Platform tab runs K1, B0, N0, B1, B2 and N1. The Inventory tab runs B3, B4 and N2. The Audit tab runs B1 to B7, K3 and the inventory calls. The pre-flight and the reports make **no** call: they use what was read.
* Page size is 500; the marker is the id of the last item of the previous page.
* No call is ever made to a Cinder host, a compute host or an array in this release. Those need SSH or an array API and arrive with the migration engine (section 3).

## 3. Calls that change the cloud

Every change goes through `OpenStackSession.mutate(permit, ...)` and obeys `SAFETY.md`: a permit checked for each call (release validated, gate approved, no halt), the step's intent and its inverse written to the journal before the call and its result after, no retry of an unknown outcome, and the order of `PROCESS.md` enforced by `openmove/lifecycle.py`. All of it is in one file, `openmove/engine/actions.py`.

| ID | Call | Needs | Used for | Inverse | If it fails |
|---|---|---|---|---|---|
| C1 | `POST {compute}/servers/{id}/action` with `{"lock": null}` | administrator | Locks the instance that uses the volume, so that nobody changes it while the volume moves (playbook 3.2). Skipped if the instance is already locked by someone else. | C2 | A refusal by the cloud: nothing is moved, the volume is marked rolled back. |
| C2 | `POST {compute}/servers/{id}/action` with `{"unlock": null}` | administrator | Unlocks the instance after the post-check passed, or after a rollback. Only an instance that OpenMove locked. | C1 | The step is retried at the next tick; the instance stays locked until it works. |
| C3 | `POST {block}/volumes/{id}/action` with `{"os-retype": {"new_type": T, "migration_policy": "on-demand"}}` | administrator | The move itself (playbook 3.3): Cinder asks Nova to copy the volume to the target back-end and switch the guest, with the instance running. The same call, with the first type, is the rollback (R3). | C3 with the first type | A refusal (4xx): nothing was moved. A lost answer or a 5xx: the outcome is unknown, everything halts, and the volume is read back before anything else is done. |

A call is never repeated by the engine. After an answer is lost, the next tick reads the volume (B8) and closes the open step with what the cloud shows: moving or moved is "done", unchanged is "failed" and the volume is rolled back.

### Still candidates (class C, the staged method)

These are taken from the owner's playbook, are **not** implemented, and are listed so they can be reviewed before any of them is written. They need a data mover host and the arrays, so they are only for the `full` access level.

| Step (see `PROCESS.md`) | Candidate call | Changes |
|---|---|---|
| Cutover, attached volume | Nova volume swap: `PUT {compute}/servers/{server}/os-volume_attachments/{volume}` | The instance's disk is copied to the destination by the hypervisor and switched, with the instance running. |
| Cutover, completion | Cinder migration completion for the pair of volume records | The two Cinder records swap. The volume keeps its id and gains the destination as its backing volume. |
| Freeze and unfreeze | Setting the migration status of the source record (through Cinder's own interfaces on the Cinder host) | The source record stops accepting changes during the cutover and is released after. |
| Destination record and cleanup | Cinder's object layer and `cinder-manage`, on a Cinder host, over SSH | Creates and, on rollback, removes the destination record. |
| Copy | A self-contained data mover script run on a Cinder host over SSH, reading the source array and writing the target array | Writes the staging volume on the target array. Reads the source only. |
| Array calls | The source array's snapshot-clone call; the target array's volume and snapshot calls | Creates clones and the staging volume. Never writes to a source volume. |

A change that cannot be undone is shown as such in the plan and needs its own approval.

## 4. The local interface

OpenMove serves its screens and a JSON interface on this machine, at `http://127.0.0.1:<port>` (default port 8765). The browser is its only client.

Protection: every `/api/` call needs the per-run token (printed at start-up and placed in the address that opens the browser) in the `X-OpenMove-Token` header; the `Host` header must be this machine (or a name given with `--allow-host`); answers carry a Content Security Policy that allows only OpenMove's own files; cloud data is shown as text, never parsed as HTML. By default only this machine can connect.

| Call | Purpose | Body | Changes the cloud? |
|---|---|---|---|
| `GET /api/status` | Version, connection, demo scenario, what has been read, gate status, halt state, number of unresolved steps. | | no |
| `POST /api/connect` | Log in to a cloud (K2). | address, user, password, project, domains, region, interface, TLS options | no |
| `POST /api/disconnect` | Forget the session and everything read. | | no |
| `POST /api/discover` | Platform detection: K1, B0, N0, B1, B2, N1. Infers the release and the release profile. | | no |
| `GET /api/platform` | The last platform result. | | no |
| `POST /api/inventory/run` | Read and classify volumes: B3, B4, N2. | back-end, rotation window in days | no |
| `POST /api/inventory/import` | Classify from the playbook's database query output instead of (or in addition to) the API. Makes no cloud call. | query results A1, A2, A4, instance list | no |
| `GET /api/inventory` | The last inventory. | | no |
| `GET /api/journal` | Recent journal events (long lists are summarised). | filters | no |
| `POST /api/audit/run` | The full environment audit: B1 to B7, K3 and the inventory calls. | optional back-end | no |
| `GET /api/audit` | The last audit. | | no |
| `POST /api/checks/run` | Run the pre-flight checks for a source and target back-end. Makes no cloud call. | source back-end, target back-end, access level (`api` or `full`) | no |
| `GET /api/checks` | The last pre-flight run, its gate and attestations. | | no |
| `POST /api/checks/attest` | Attest (or withdraw) a check that is a warning or not checked. Needs a name and at least 8 characters of evidence. | check id, name, note, withdraw | no |
| `POST /api/checks/signoff` | Sign the gate as operator or reviewer. Two different people are needed. | role, name | no |
| `POST /api/report` | Build a Word report and return it as a download. | kind (`before`, `audit` or `unmigrated`), customer, prepared by, include audit | no |
| `POST /api/halt` | Stop all changes at once. Needs a name and a reason. | name, reason | no (it only blocks) |
| `POST /api/resume` | Lift the halt. Needs a name and a reason. | name, reason | no |
| `GET /api/live` | The live view: volumes per phase, overall progress, per-volume progress, the gate, the halt, unresolved steps and the newest journal events. Read from the journal; makes no cloud call. The screen polls it about once a second while the Live tab is open. | | no |
| `GET /api/unmigrated` | Every volume that is not migrated, with the cause, how it ended, the state left behind and the proposed steps to resolve it (`CLASSES.md`). Read from the inventory, the pre-flight and the journal; makes no cloud call. | | no |
| `GET /api/selection` | Which volumes are selected for the migration, the eligible ones, the groups (by class, project, attachment, size, snapshots, Cinder host, instance and workload risk), and the settings with their defaults and floors. Read from the inventory, the pre-flight and the journal; makes no cloud call. | | no |
| `POST /api/selection` | Choose volumes by id or by group (`set`, `add`, `remove`, `none`, `reset`), and change the settings. A volume that is not admitted is refused, a setting below its floor is refused (never clamped), and nothing changes while a run is in progress. Every change is recorded. | action, ids, group, options, reset_options, name | no |
| `POST /api/release` | Release volumes that wait in the sync phase, before the cutover. Needs a name. The cutover rules still apply after the release. | ids or all, name | no |
| `POST /api/volume-gate` | Sign, or withdraw, the playbook's volume gate G2, G3 or G4 for volumes, as a second person who is not the operator of the run. Each volume is checked on its own: it must be at the point where that gate is signed. | gate, ids or all, name, evidence, withdraw | no |
| `GET /api/volume` | One volume in full: its class, its steps with the status of each, what is happening now, the cutover conditions, its journal events and, if it was not migrated, the cause and the steps to resolve it. Read from the journal; makes no cloud call. | id (query) | no |
| `GET /api/risk` | The volumes that host a workload with a known risk, the reasons, the advice, and who has cleared or marked them (`WORKLOAD_RISKS.md`). | | no |
| `POST /api/risk/clear` | Record one person's clearance of a high-risk volume, as operator or as reviewer, or withdraw it. Needs a name and evidence of at least 20 characters. Runs the pre-flight again. | ids, role, name, evidence, withdraw | no |
| `POST /api/risk/mark` | Mark volumes, by id or group, as a risk from what the customer knows, or withdraw a mark. Needs a name and a reason. Runs the pre-flight again. | ids, group, name, note, withdraw | no |
| `GET /api/engine` | The engine's state for the Move screen: counters, and one row per volume with its phase, signed gates and what is happening. Read from the journal; makes no cloud call. | | no |
| `POST /api/engine/dry-run` | The dry run: fresh reads (B8, B9, N3, B5) of the selected class A and B volumes, the checks, and the exact changes a run would make with their undo. Sends nothing that changes anything; needs no permit. | target type (optional), ids (optional) | no |
| `POST /api/engine/start` | Start the real run of the selected class A and B volumes. Needs a permit now (validated release, approved gate, no halt) and a name. Nothing is sent for a volume until G2 and G3 are signed by a second person. | name, target type, ids | yes (C1, C2, C3) |
| `POST /api/engine/stop` | Stop taking new volumes. A retype already sent carries on in the cloud. | | no |
| `POST /api/engine/reverse` | Put a moved volume back with a retype to its first type (R3). Needs a name and a reason. | id, name, reason | yes (C3) |
| `POST /api/validation` | Record the lab validation of the release (gate G0): two different people declare which lab tests were run, where, and the result. Lifts the read-only state of the release. | name, second person, evidence, passed, environment | no |
| `POST /api/lab/approve` | Lab mode only: record the lab validation of the lab release (only for a release whose profile is marked as a lab), attest the open items and sign as two lab users, labelled as the lab's. A failed check still blocks; it cannot be waived. | | no |
| `POST /api/demo/validate` | Demo mode only: record a labelled, synthetic lab validation. | | no |
| `POST /api/demo/approve` | Demo mode only: attest the open items and sign as two made-up people, labelled synthetic. A failed check still blocks; it cannot be waived. | | no |
| `POST /api/demo/simulate` | Demo mode only: start or stop a simulated migration run with made-up evidence and a virtual clock. It uses the real lifecycle, gate and halt rules and sends nothing to any system. It cannot start unless the gate is approved. | action (`start` or `stop`) | no |
| `POST /api/demo/scenario` | Demo mode only: switch scenario and start clean. | scenario id | no |
| `POST /api/demo/db-export` | Demo mode only: load a made-up database export. | | no |

Static files (`/`, `/app.js`, `/styles.css`) are served from the program's own resources.

One more address, outside `/api/`, in lab mode only: `GET /lab/dashboard?token=<token>`. It logs in to the lab's own OpenStack dashboard (Horizon, a local throw-away cloud) with the lab user, on this machine, and answers `302` to the dashboard with the session cookies, so the browser arrives signed in. It needs the per-run token, answers 404 outside lab mode, and never puts the password in a page or an address. The two requests it makes to Horizon (`GET` and `POST` of `/dashboard/auth/login/`) go to the lab address only and are not calls to a customer cloud.

## 5. Journal events

The journal is a local SQLite file (`journal.db`). It is append-only in normal use. Every record is scrubbed of secrets before it is written. Events of kind `phase` are the state of the migration of each volume: the process reads them back to decide what is allowed next.

| Kind | Written when | Contents |
|---|---|---|
| `connect` | A login succeeds. | Address, user, TLS setting, roles. Never the password or token. |
| `disconnect` | The user disconnects. | |
| `platform` | Platform detection runs. | Release, versions, profile status, counts. |
| `inventory` | An inventory is read or imported. | Counts per class, total, the volume ids (for the drift check). |
| `audit` | An audit runs. | Counts, finding ids. |
| `preflight` | The pre-flight runs. | Run id, counts per status, fingerprint, gate status. |
| `attestation` | A check is attested or an attestation is withdrawn. | Check, back-end pair, name, evidence. |
| `signoff` | The gate is signed. | Run id, role, name. |
| `report` | A report is built. | Kind, size, customer, prepared by. |
| `validation` | A lab validation of a release is recorded. | Release, result, evidence. |
| `halt` / `resume` | The halt switch is used. | Name and reason. |
| `refusal` | The order, the gate or the halt refused a step. | The step attempted and the exact reasons. In the simulation they are marked simulated. |
| `phase` | A volume enters a phase of the migration order. | Phase, time, evidence (the volume class from the precheck, verification results, snapshot image comparison for class C, fingerprints, lag). An abort or rollback also carries its reason and cause code. |
| `selection` | The selection or the settings change. | Action, count, the ids or the group, the settings, name. |
| `volume_gate` | A second person signs, or withdraws, G2, G3 or G4 for one volume. | Volume, gate, name, evidence, withdrawn. |
| `release` | A person releases volumes held before the cutover. | Volume ids, name. |
| `risk_clearance` | A person clears, or withdraws the clearance of, a high-risk volume. | Volume, role, name, evidence, the risk signature. |
| `risk_mark` | A person marks a volume as a risk, or withdraws the mark. | Volume, name, note. |
| `engine_dry_run` | A dry run is made. | Counts of volumes ready, waiting and blocked. |
| `engine_run` | A real run starts. | Volumes queued and resumed, the type map. |
| `engine_start` | A person starts a run. | Name, queued and resumed counts. |
| `lab` | Lab mode connects to the lab cloud. | Address, roles, a note that it is not a customer cloud. Never the password. |
| `demo` | Demo mode starts, or a simulated run starts. | Scenario, or the simulation note. |

## 6. What OpenMove writes to disk

| File | Content |
|---|---|
| `journal.db` (and `-wal`, `-shm`) in the data folder | The journal above. The data folder is the user's application-data folder, or the folder given with `--home`, or a throw-away temporary folder in demo mode. |
| Reports | Only where the browser saves a download. OpenMove keeps no copy. |
| `evidence/<volume id>/` in the data folder | Per volume the engine moves: `validated.json`, `before.json`, `after.json`, `calls.jsonl` (every change sent or refused, with time and result) and, after a failure or a reverse, `failed.json` or `after-reverse.json`. Cloud answers only; never a password or token. |

OpenMove writes nothing on any Cinder host, compute host or array in this release.
