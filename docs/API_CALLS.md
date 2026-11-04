# Calls reference

Every call OpenMove makes or accepts, in one place, so it can be reviewed line by line. Three groups:

1. **Outbound calls to the cloud** (section 2). In this release there are 17 distinct read calls and the login, a `POST` that creates a token and changes nothing.
2. **Outbound calls that change the cloud** (section 3). Six exist in this release: lock, unlock and retype for class A and B volumes, and the volume swap, the migration completion and the forced delete of the old record for class C volumes (the staged method). Section 3 also lists the operations the staged method makes on the migration host and the Cinder host over SSH. The rules every one must obey are fixed here.
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
| N4 | `GET {compute}/os-services?binary=nova-compute&host=<host>` | administrator | Before the swap of an attached class C volume: is the compute service of the instance's host up and enabled. Found in the lab, where a dead compute service made the swap wait for ever. The same read of the instance also checks the guest's power state. | The cutover does not start: rolled back before the swap (R2). |

Notes:

* B0, N0 and K1 are the unauthenticated version documents. The login (K2) is the only `POST`.
* The Platform tab runs K1, B0, N0, B1, B2 and N1. The Inventory tab runs B3, B4 and N2. The Audit tab runs B1 to B7, K3 and the inventory calls. The pre-flight and the reports make **no** call: they use what was read.
* Page size is 500; the marker is the id of the last item of the previous page.
* The reads above go to the cloud's API only. A Cinder host, a migration host or an array is reached only by the staged method and the Hosts and Diagnostics screens, over SSH to hosts a person registered (section 3, `MOVER.md`, `NETWORK.md`).

## 3. Calls that change the cloud

Every change goes through `OpenStackSession.mutate(permit, ...)` and obeys `SAFETY.md`: a permit checked for each call (release validated, gate approved, no halt), the step's intent and its inverse written to the journal before the call and its result after, no retry of an unknown outcome, and the order of `PROCESS.md` enforced by `openmove/lifecycle.py`. All of it is in one file, `openmove/engine/actions.py`.

| ID | Call | Needs | Used for | Inverse | If it fails |
|---|---|---|---|---|---|
| C1 | `POST {compute}/servers/{id}/action` with `{"lock": null}` | administrator | Locks the instance that uses the volume, so that nobody changes it while the volume moves (playbook 3.2). Skipped if the instance is already locked by someone else. | C2 | A refusal by the cloud: nothing is moved, the volume is marked rolled back. |
| C2 | `POST {compute}/servers/{id}/action` with `{"unlock": null}` | administrator | Unlocks the instance after the post-check passed, or after a rollback. Only an instance that OpenMove locked. | C1 | The step is retried at the next tick; the instance stays locked until it works. |
| C3 | `POST {block}/volumes/{id}/action` with `{"os-retype": {"new_type": T, "migration_policy": "on-demand"}}` | administrator | The move itself (playbook 3.3): Cinder asks Nova to copy the volume to the target back-end and switch the guest, with the instance running. The same call, with the first type, is the rollback (R3). | C3 with the first type | A refusal (4xx): nothing was moved. A lost answer or a 5xx: the outcome is unknown, everything halts, and the volume is read back before anything else is done. |
| C4 | `PUT {compute}/servers/{server}/os-volume_attachments/{volume}` with `{"volumeAttachment": {"volumeId": new}}` | administrator | The cutover of an attached class C volume (staged method): Nova copies the disk under the running instance from the old volume to the destination volume and switches the instance. | The old volume is untouched until the completion; reverse (R3) uses the same method with the roles swapped. | A refusal: the volume is rolled back (R2). A lost answer or a 5xx: the outcome is unknown, everything halts, and the attachment is read back. |
| C5 | `POST {block}/volumes/{id}/action` with `{"os-migrate_volume_completion": {"new_volume": new, "error": false}}` | administrator | The two Cinder records trade places, so the volume keeps its id and now sits on the target array. Sent by the tool itself for a detached volume; Nova sends it after a swap for an attached one. | Reverse (R3). | As C4. |
| C6 | `POST {block}/volumes/{id}/action` with `{"os-force_delete": null}` | administrator | Deletes the temporary record that holds the old backing volume, after the soak. The old backing volume is left alone if Cinder's driver cannot delete it; this is logged as a leftover. | None: the source is released only after the soak and the post-check. | The step is retried at the next tick. |

A call is never repeated by the engine. After an answer is lost, the next tick reads the volume (B8) and closes the open step with what the cloud shows: moving or moved is "done", unchanged is "failed" and the volume is rolled back.

### Operations on the migration host and the Cinder host (class C, the staged method)

These are not calls to the cloud. They run over SSH, through `connectors/ssh.py`, on hosts a person registered on the Hosts tab and whose host key a person pinned. Each is made through `Actions.op`, which holds the same rules as a cloud change: a permit now, the intent and its undo in the journal first, the result after, no retry of an unknown outcome. Every operation is safe to repeat: it returns what the first call made. Details and the job contract are in `MOVER.md`.

| Operation | Runs on | Changes |
|---|---|---|
| Data mover file placed, removed | migration host | One file (`mover.py`) in OpenMove's own folder on the host. Removal leaves nothing behind and is refused while a job runs. |
| Logical-volume list, create, snapshot, rename, extend, remove, remove the source snapshots, free space (`lv.list`, `lv.create`, `lv.snapshot`, `lv.rename`, `lv.extend`, `lv.remove`, `lv.remove_source_snapshots`, `vg.free`) | migration host | Creates and removes only the staging volumes, clones and safety copies that OpenMove made (named `om-...`). Never a volume it did not make. |
| Copy and compare jobs (`job.start`, `job.status`, `job.cancel`, `cleanup`) | migration host | A background job (one id per snapshot, generation and try) copies the image of one snapshot onto the staging volume of the target array, or reads both sides back and compares them. Reads the source only; the job keeps its status and log in its own folder. |
| Record operations (`show`, `find-host`, `record-create`, `freeze`, `unfreeze`, `sync-fields`, `record-delete`, `conf`) | Cinder host | Creates the destination record through Cinder's object layer, freezes the source record during the cutover, and on rollback unfreezes it and deletes the destination record. |
| Readiness, diagnostics and benchmark (`probe`, `diag`, `bench`) | both | Reads only (checks `H1` to `H9`), plus a small write-and-delete test in the staging area when a person asks for the benchmark. |
| Array calls from the migration host (`sf.call`, `pure.cli`; `ARRAY_API.md`) | the staged method, class C, when an adapter for SolidFire or FlashArray exists; and the host check H10 | **Element API** on the management address, only these methods: `GetClusterInfo`, `GetClusterVersionInfo`, `GetClusterCapacity`, `ListAccounts`, `GetAccountByName`, `ListVolumes`, `ListSnapshots`, `GetAsyncResult`, `ListSyncJobs`, `ListVolumeAccessGroups`, `ListDeletedVolumes` (reads) and `CloneVolume` (read-only, in the migration account), `Add` and `RemoveVolumesFromVolumeAccessGroup`, `DeleteVolume`, `PurgeDeletedVolume` (only on volumes of the migration account). **Purity CLI** over SSH with a key, only: `purevol list` (volume, `--snap`, `--connect`), `purearray list`, `pureport list` (reads) and `purevol create`, `setattr --size`, `snap --suffix`, `rename`, `destroy`, `purehost connect` and `disconnect` (only on volumes the mover made) | Clones in the migration account; staging volumes on the target array. Never a tenant volume, never an eradicate, never a pod or a host object |
| Host setup (`hosts/setup`): a login with the administrator password, `prepare_host.sh`, `configure_storage.sh` | a new host, once, when a person asks | Installs packages; creates the service account, its key and its sudo rights (`/etc/sudoers.d/openmove`); starts `sshd`; for storage paths changes `/etc/iscsi/iscsid.conf` (the original is kept), the initiator name, `/etc/multipath/conf.d/openmove.conf`, `/etc/modules-load.d/openmove-storage.conf`, starts `iscsid` and `multipathd` and logs in to the portals given. Uses a private temporary folder `/tmp/openmove-setup.*` that it removes. Never creates, maps or deletes a volume. The password is given to `ssh` only through the environment. |

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
| `GET /api/tree` | The volumes as a tree: project, instance, the volumes attached to each, and the volumes not attached to anything, each with its group in plain words and, once the pre-flight has run, whether it will be moved and why not. Read from the inventory, the pre-flight and the selection; makes no cloud call. | | no |
| `GET /api/selection` | Which volumes are selected for the migration, the eligible ones, the groups (by class, project, attachment, size, snapshots, Cinder host, instance and workload risk), and the settings with their defaults and floors. Read from the inventory, the pre-flight and the journal; makes no cloud call. | | no |
| `POST /api/selection/confirm` | The operator confirms the selection as it stands. Needs the approved pre-flight gate and a name. It opens the Move step of the wizard and is void when the selection or the pre-flight run changes. Sends nothing to the cloud. | name | no |
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
| `POST /api/engine/reverse` | Put a moved volume back with a retype to its first type (R3). Needs a name and a reason. With `preview` it only reads and lists, for each volume that was cut over, the calls the reverse would make and their undo (R6). With `all` it puts every cut-over volume of the run back, each journaled and checked on its own. | id or all or preview, name, reason | yes (C3) |
| `POST /api/validation` | Record the lab validation of the release (gate G0): two different people declare which lab tests were run, where, and the result. Lifts the read-only state of the release. With `from_tests` the evidence is built from the Phase 0 results and the call is refused unless every test of the retype path has passed for this release. | name, second person, evidence or from_tests, passed, environment | no |
| `GET /api/phase0` | The Phase 0 lab tests (T1 to T23 and M1 to M4) as guided procedures, with the newest recorded result of each for the detected release, and the count still open. | | no |
| `POST /api/phase0/record` | Record one test's result (pass or fail) with a name and at least 40 characters of what was done and seen. The newest result of a test counts. | test, result, name, evidence, environment | no |
| `POST /api/lab/approve` | Lab mode only: record the lab validation of the lab release (only for a release whose profile is marked as a lab), attest the open items and sign as two lab users, labelled as the lab's. A failed check still blocks; it cannot be waived. | | no |
| `GET /api/hosts/setup-script` | Query: `role`, `user`, `cinder_user`, `from`. The host setup script (`prepare_host.sh`) and the command to run it, with OpenMove's public key filled in. Makes OpenMove's key if it does not exist. Changes nothing on any host. Refuses a role or account name that is not plain (400). |
| `GET /api/hosts` | The registered hosts (migration host, Cinder host), OpenMove's own public key to add to each host, the last readiness checks, the back-end families and whether the fabric is ready. Makes no call. | | no |
| `POST /api/hosts/probe` | Read the host key a machine offers before it is registered (the first step of a host setup). Nothing is trusted. Over SSH, no login. | address, port | no |
| `POST /api/hosts/array` | Store, list, forget or check the login of an array on a migration host (`ARRAY_API.md`). Body: `host`, `action` (`set`, `forget`, `list`, `check`) and, for `set`, `array` with `name`, `family` (`solidfire` or `pure`), `mgmt` (the management address) and, for SolidFire, `user`, `password` and `migration_account_id`; for a FlashArray `user` (it logs in with a key), `host_object` (the migration host's name on the array) and an optional `identity` file. The password goes to the host's data mover only, which keeps it in a root-only file; it is not stored, journaled, logged or returned. A `set` also runs one call that changes nothing to prove the login. | no |
| `POST /api/hosts/setup` | Set up a new host: log in once with the administrator password, prepare the host, set up its storage paths, register it and check it (`DEPLOYMENT.md`, section 4.1). The password and the CHAP secret are used for this call only and are never stored, logged or returned. The host key must be pinned first, or the fingerprint a person checked and the person's name must be given: the password is not sent before that. With `check_only` nothing is changed or registered. Over SSH. | name, address, port, user, password, roles, service_user, cinder_user, cinder_python, from_address, jump, fingerprint, by, storage, check_only | no (changes the host being set up) |
| `POST /api/hosts/save` | Register or change a host: name, roles, address, port, user, transport (`ssh` or `local`), the back-end family. Nothing is contacted. | host | no |
| `POST /api/hosts/remove` | Forget a host. | name | no |
| `POST /api/hosts/scan` | Read the host key the machine offers now (SSH key scan) and show its fingerprint. Nothing is trusted yet. | name | no |
| `POST /api/hosts/trust` | Pin the host key whose fingerprint a person confirmed. The host is scanned again and a different key is refused. Needs the name of the person. | name, fingerprint, by | no |
| `POST /api/hosts/check` | Run the host readiness checks (`H1` to `H9`) for a pair of back-ends, placing the data mover where it is needed. With `bench` it also measures the copy speed. Over SSH. | source, target, bench | no (writes the data mover file on the host) |
| `POST /api/hosts/mover` | Place (`install`) or remove (`remove`) the data mover file on one host. Removal is refused while a job runs. Over SSH. | name, action | no (host only) |
| `GET /api/diagnostics` | The diagnostics settings (redaction level, log redaction), the log files, the support bundles made so far and what each level removes. | | no |
| `POST /api/diagnostics/bundle` | Build a support bundle (a zip of the logs, the journal, the evidence, the screen state and, when asked, a read-only collection from each registered host) at a redaction level. | level, remote, reason | no |
| `POST /api/diagnostics/settings` | Change the redaction level of bundles and of the log. | redaction, log_redaction | no |
| `GET /api/diagnostics/download` | Download a support bundle made earlier (the name must be one OpenMove made). | name (query) | no |
| `POST /api/diagnostics/decode` | Turn a redaction token back into the value it stands for. Works only on the site that made the bundle: the token map never leaves it. | token | no |
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
| `phase0_test` | The result of one Phase 0 lab test is recorded. | Test id, result, name, evidence, environment. |
| `halt` / `resume` | The halt switch is used. | Name and reason. |
| `refusal` | The order, the gate or the halt refused a step. | The step attempted and the exact reasons. In the simulation they are marked simulated. |
| `phase` | A volume enters a phase of the migration order. | Phase, time, evidence (the volume class from the precheck, verification results, snapshot image comparison for class C, fingerprints, lag). An abort or rollback also carries its reason and cause code. |
| `selection_confirmed` | The operator confirms the selection (step 6). | Name, pre-flight run id, count of volumes, whether it is the default selection. |
| `selection` | The selection or the settings change. | Action, count, the ids or the group, the settings, name. |
| `volume_gate` | A second person signs, or withdraws, G2, G3 or G4 for one volume. | Volume, gate, name, evidence, withdrawn. |
| `release` | A person releases volumes held before the cutover. | Volume ids, name. |
| `risk_clearance` | A person clears, or withdraws the clearance of, a high-risk volume. | Volume, role, name, evidence, the risk signature. |
| `risk_mark` | A person marks a volume as a risk, or withdraws the mark. | Volume, name, note. |
| `engine_dry_run` | A dry run is made. | Counts of volumes ready, waiting and blocked. |
| `engine_run` | A real run starts. | Volumes queued and resumed, the type map. |
| `engine_start` | A person starts a run. A real run needs a dry run read for the same volumes first (or an earlier run to resume). The run may carry a window (UTC start and stop): outside it no new volume is taken and no retype is sent. | Name, queued and resumed counts, the window. |
| `engine_reverse_all` | A person puts the whole run back. | Name, reason, number of volumes. |
| `stage` | The staged method (class C) records one fact about one volume: a state change, a counter, a job id, a gate, an error. The engine reads these back after a restart, so every step can be repeated safely. | Volume, the keys that changed. Cloud and host answers; never a password or token. |
| `host_saved` / `host_removed` | A person registers, changes or forgets a migration host or Cinder host. | Name, roles, transport, address, port, user. No key material. |
| `array_login` | A person stores or removes the login of an array on a migration host (`POST /api/hosts/array`). | Host, array name, action, family, management address and user. Never the password. |
| `host_setup` | A person sets up a new host from the Hosts tab, or tries to (also when it fails or is only checked). | Host, address, login, roles, service account, storage description without secrets and without the extra multipath text, the status of each step, the initiator name, the error if the login failed. Never the password or the CHAP secret. |
| `host_trusted` | A person pins a host key after checking its fingerprint. | Host, fingerprint, key type, name of the person. |
| `mover_installed` / `mover_removed` | The data mover file is placed on, or removed from, a host. | Host, path, SHA-256 of the file. |
| `support_bundle` | A support bundle is built. | File name, redaction level, number of files, size. |
| `lab` | Lab mode connects to the lab cloud. | Address, roles, a note that it is not a customer cloud. Never the password. |
| `demo` | Demo mode starts, or a simulated run starts. | Scenario, or the simulation note. |

## 6. What OpenMove writes to disk

| File | Content |
|---|---|
| `journal.db` (and `-wal`, `-shm`) in the data folder | The journal above. The data folder is the user's application-data folder, or the folder given with `--home`, or a throw-away temporary folder in demo mode. |
| Reports | Only where the browser saves a download. OpenMove keeps no copy. |
| `evidence/<volume id>/` in the data folder | Per volume the engine moves: `validated.json`, `before.json`, `after.json`, `calls.jsonl` (every change sent or refused, with time and result) and, after a failure or a reverse, `failed.json` or `after-reverse.json`. Cloud answers only; never a password or token. |

| `hosts.json`, `ssh/` (OpenMove's own key pair and `known_hosts`) in the data folder | The registered hosts and the keys. The private key never leaves the folder and never goes into a bundle. |
| `logs/openmove.log`, `logs/calls.jsonl`, `diag/` in the data folder | The diagnostic log (every start, run, stage, refusal and traceback, redacted as set), the settings and the local token map for redaction. The token map is never put in a bundle. |
| `bundles/openmove-support-*.zip` in the data folder | Support bundles. They are made when a person asks, and automatically (a light bundle without the remote collection) when a run halts or an unexpected error occurs, once per reason, at most eight. |

On the migration host and the Cinder host OpenMove writes only inside its own folder (`mover.py`, the job folders and the staging volumes it names `om-...`), and never touches a volume it did not make. See `MOVER.md`.
