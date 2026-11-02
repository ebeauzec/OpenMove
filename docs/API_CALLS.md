# Calls reference

Every call OpenMove makes or accepts, in one place, so it can be reviewed line by line. Three groups:

1. **Outbound calls to the cloud** (section 2). In this release there are 14 distinct calls. Thirteen are reads; the fourteenth is the login, a `POST` that creates a token and changes nothing.
2. **Outbound calls that change the cloud** (section 3). None exist in this release. The rules every future one must obey are fixed here first.
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

## 2. Calls to the cloud today (all reads)

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

Notes:

* B0, N0 and K1 are the unauthenticated version documents. The login (K2) is the only `POST`.
* The Platform tab runs K1, B0, N0, B1, B2 and N1. The Inventory tab runs B3, B4 and N2. The Audit tab runs B1 to B7, K3 and the inventory calls. The pre-flight and the reports make **no** call: they use what was read.
* Page size is 500; the marker is the id of the last item of the previous page.
* No call is ever made to a Cinder host, a compute host or an array in this release. Those need SSH or an array API and arrive with the migration engine (section 3).

## 3. Calls that change the cloud (none yet)

The migration engine is not built. When it is, every change goes through `OpenStackSession.mutate(permit, ...)` and obeys `SAFETY.md`: permit checked per call, step intent written to the journal before the call and its result after, an inverse recorded for every step, no retry of an unknown outcome, and the order of `PROCESS.md` section 4 enforced by `openmove/lifecycle.py`.

The calls below are **candidates** taken from the owner's playbook. They are listed so they can be reviewed before any of them is written, not because they exist. The exact list is fixed when the engine is built and is proven in the lab (playbook Phase 0, tests T1 to T21) before a release profile can be marked validated.

| Step (see `PROCESS.md`) | Candidate call | Changes |
|---|---|---|
| Cutover, attached volume | Nova volume swap: `PUT {compute}/servers/{server}/os-volume_attachments/{volume}` | The instance's disk is copied to the destination by the hypervisor and switched, with the instance running. |
| Cutover, completion | Cinder migration completion for the pair of volume records | The two Cinder records swap. The volume keeps its id and gains the destination as its backing volume. |
| Freeze and unfreeze | Setting the migration status of the source record (through Cinder's own interfaces on the Cinder host) | The source record stops accepting changes during the cutover and is released after. |
| Lock | `POST {compute}/servers/{server}/action` with `lock` and `unlock` | Prevents other operations on the instance during the cutover. |
| Detached volumes | Cinder's standard retype or migrate (`POST {block}/volumes/{volume}/action`) | Moves a class A volume. |
| Destination record and cleanup | Cinder's object layer and `cinder-manage`, on a Cinder host, over SSH | Creates and, on rollback, removes the destination record. |
| Copy | A self-contained data mover script run on a Cinder host over SSH, reading the source array and writing the target array | Writes the staging volume on the target array. Reads the source only. |
| Array calls | The source array's snapshot-clone call; the target array's volume and snapshot calls | Creates clones and the staging volume. Never writes to a source volume. |

A change that cannot be undone is shown as such in the plan and needs its own approval. The cutover and the cleanup after the soak period are the only two.

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
| `POST /api/checks/run` | Run the pre-flight checks for a source and target back-end. Makes no cloud call. | source back-end, target back-end | no |
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
| `POST /api/demo/validate` | Demo mode only: record a labelled, synthetic lab validation. | | no |
| `POST /api/demo/approve` | Demo mode only: attest the open items and sign as two made-up people, labelled synthetic. A failed check still blocks; it cannot be waived. | | no |
| `POST /api/demo/simulate` | Demo mode only: start or stop a simulated migration run with made-up evidence and a virtual clock. It uses the real lifecycle, gate and halt rules and sends nothing to any system. It cannot start unless the gate is approved. | action (`start` or `stop`) | no |
| `POST /api/demo/scenario` | Demo mode only: switch scenario and start clean. | scenario id | no |
| `POST /api/demo/db-export` | Demo mode only: load a made-up database export. | | no |

Static files (`/`, `/app.js`, `/styles.css`) are served from the program's own resources.

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
| `demo` | Demo mode starts, or a simulated run starts. | Scenario, or the simulation note. |

## 6. What OpenMove writes to disk

| File | Content |
|---|---|
| `journal.db` (and `-wal`, `-shm`) in the data folder | The journal above. The data folder is the user's application-data folder, or the folder given with `--home`, or a throw-away temporary folder in demo mode. |
| Reports | Only where the browser saves a download. OpenMove keeps no copy. |

OpenMove writes nothing on any Cinder host, compute host or array in this release.
