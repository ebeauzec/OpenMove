# Changelog

## 0.12.5 (2026-11-05)

### Changed
- The limit on simultaneous copies now counts only volumes that move data (copy, sync, cutover, rollback). A volume that waits for a person (the approvals, the release), for the run window, for its snapshots to rotate away or in its safety period no longer holds a copy place. A volume takes a copy place when everything else is in place and waits for a free place otherwise, with a note that says so. A second limit, volumes in progress at once (default 30, new setting max_in_flight), bounds the volumes that are copying or ready and waiting, so the space on the new storage, the staging copies and the open approvals stay bounded; volumes that wait on the clock are not counted, at most as many are watched as there are copy places, and class B volumes start last. The default for copies at the same time is 10 (was 8). The interface: every control has a tooltip; the Move step lists the three conditions of a real move with their state and a button for each; the helper servers are named before the safety checks, where the choice depends on them; the second copy of the approval buttons on the Move step is gone (they are in the open actions at the top and on the Progress step); the wording is plainer (Standard and Staged copy for the groups, Action required, No action required, Session restored). GET /api/engine carries slots.

## 0.12.4 (2026-11-05)

### Changed
- The Open actions at the top of every page now carry the button that does the thing: Approve before switching, Release them for the switch, Approve the result, and, before the move, Approve as first approver, Approve as second approver, and a button that shows the safety checks that failed, need a person or are out of date. The button opens the same dialog as the one lower on the page, so nobody has to scroll and look for it. Before this, the approvals of the safety checks were only at the bottom of the Safety checks step and the top said nothing about them. The Back and Next buttons moved from the bottom of the page to the top, under the tabs. When something waits for a person and no move runs yet, the status says 'Waiting for you' and not 'No move is running'. GET /api/status carries do and cta for each open action.

## 0.12.3 (2026-11-04)

### Fixed
- A move with more volumes than places looked stuck: the ten volumes that had been switched sat in their seven-day safety period and kept all the places, so the 37 waiting volumes never started. A volume in its safety period no longer takes a place, and a place that is freed goes to the next volume in the queue in the same pass (before, at the next look, five seconds later). The board and the Progress page no longer count waiting volumes as being worked on; they say how many are being worked on, how many wait for a free place and how many are in the safety period. The notice 'Back where you were' stays closed once it is closed (it came back at every refresh of the page). New on the Progress page: a panel that explains the stages and the checks G2, G3 and G4 in plain words. The approval dialog (Approve before switching, Approve the result) now lists the volumes being approved: name, size, kind, type and back-end, the instance that uses it, snapshots, stage and what OpenMove found; GET /api/live rows carry volume_type, snapshots, attached_to, bootable and source_backend. The gate codes G2, G3 and G4 no longer appear on the page: the buttons, the table headings, the dialog, the notes and the board say 'the copy is correct', 'ready to switch' and 'the switch worked' (the help panel names the codes once, for the documents).

## 0.12.2 (2026-11-04)

### Changed
- The top of every page now shows three things, coloured by how it is: Status (green moving or finished, amber waiting for a person or trying again, red stopped, grey no move running), Progress (the bar, the volumes switched and how many are at each stage, in plain words) and Open actions (what only a person can do, who may not do it, and which button, with a button that takes you there). The tab that holds the button shows a number and the button pulses. The page opens on Progress when something waits, instead of on Connect. The old thin progress bar is gone. Buttons are named for what they do (Approve before switching, Approve the result). GET /api/status carries board and waiting. Fixed: the step label showed the tab badge (Progress10). Fixed: Release said "no selected volume to release" for volumes already in flight that were not in today's choice (after a restart or a changed choice); it now releases every volume the move holds.

## 0.12.1 (2026-11-04)

### Fixed
- Two faults found while proving the failsafe on the lab. (1) The Progress page counted every volume in the journal against only the selected ones, so a real move could show 282% done, and it listed volumes that were not part of the move; the volumes waiting for a second person (G2 and G3 before the switch, G4 after it) were only counted for the simulation, so a real move that waited for approval looked stuck and the page said nothing. It now counts the volumes the engine holds, never shows more than 100%, and says in a sentence what each volume waits for (approval, release, a retry). (2) After a restart the engine could not take up a volume that was under way but had already left the source back-end, because the inventory is read for the source back-end only and the spec of the volume came from it; the restore failed with a missing-value error. The spec of such a volume is now read from its precheck event in the journal, never guessed.

## 0.12.0 (2026-11-04)

### Added
- A stop of OpenMove, of the computer or of the network in the middle of a move no longer costs anything. Found when a person came back to a move that looked stuck and a page that was broken: the copy had finished and was waiting for a second person's approval (G2 and G3), the computer then restarted, and with it the lab and OpenMove, whose memory held the pre-flight, the choice and the access address. New: a supervisor (--supervise, used by OpenMove.bat and OpenMove-Lab.bat) starts OpenMove again within seconds if it ends or stops answering, and gives up with a clear message after eight quick stops; the work is saved to resume.json (pre-flight run, choice, options, releases; never a password or cloud token) and put back after the cloud sign-in, which the lab does by itself, and the move that was running is carried on through the engine under the usual permit; the access address is kept across restarts so the open page keeps working; the engine loop retries after an error instead of ending (up to 40 in a row), records a sleep or stall, and a guardian thread starts it again if its thread dies and keeps Windows awake while a move runs; one OpenMove per data folder (a lock; two programs could share a port on Windows before); the page shows a bar when OpenMove is not answering and carries on by itself when it is back, shows what was restored, and the Progress page says in a sentence when volumes only wait for a second person's approval or the move is retrying. New routes POST /api/recover and /api/recover/discard, GET /healthz, journal events server_start, server_stop, engine_end, engine_gap, engine_revive, engine_resume, recovery. Proven on the lab by killing OpenMove five times with volumes in flight, once just after a retype was sent. Documented in docs/RECOVERY.md. The sleep prevention is Windows only; a restart of the whole computer still needs OpenMove started again.

## 0.11.0 (2026-11-04)

### Changed
- The interface was reworked for a person who has not read the migration plan. Nine technical steps became seven: Connect, Scan (platform, inventory and health check in one button), Safety checks, Choose, Move, Progress, Reports. The server still holds every step closed until the one before it is done, and every rule is unchanged. New: the cloud is shown as a tree (project, instance, attached volumes, volumes not attached) with each volume's group in plain words (Easy, Waits for snapshots, Careful copy, Instance is off, Not moved by this tool) and, after the safety checks, whether it will be moved and why not (GET /api/tree); the Choose step uses the same tree with a box on every project, instance and volume. Messages are fixed to the top of the window, so an error is never off screen (this was why Attest looked broken: its error appeared above the visible page). Every question is one dialog that keeps the server's refusal inside it. Confirm and Approve replace attest and sign-off; a panel at every step says what to do; a Help button explains the words. The safety checks are shown as a to-do list: what failed, what needs a person, what is confirmed, who approved. The page no longer shows safety-check results the server has dropped. Fixed: the lab SSH key file had permissions that let other users read it, so ssh refused it and check P8 failed; OpenMove now limits the key to its owner (icacls on Windows, chmod elsewhere).

## 0.10.2 (2026-11-04)

### Fixed
- On the Pre-flight tab the Attest button was the last column of the platform checks table, so on a normal window it was off the right edge and needed a sideways scroll. It is now the column after Status, and the Check column has a minimum width so its text no longer wraps one word to a line.

## 0.10.1 (2026-11-04)

### Fixed
- OpenMove-Lab.bat from a cold machine stopped after DevStack came up: nova-compute starts before Keystone answers and fails about 30 seconds later, after lab_up.sh had already looked for failed units, so the compute service stayed down and the batch file stopped before OpenMove started. lab_up.sh now waits for Keystone and checks that nova-compute stays up, restarting it up to five times. The batch file starts OpenMove even when the cloud answers but a guest did not start, passes options that start with a dash on to OpenMove, and the batch files are kept CRLF (.gitattributes).

## 0.10.0 (2026-11-04)

### Added
- Researched which array calls the staged method needs (docs/ARRAY_API.md: Element API, Purity CLI and what the Queens drivers do for SolidFire, FlashArray and ONTAP, with each fact marked as read in a driver, in the playbook scripts, in vendor documentation, or still to confirm). The data mover now makes array calls only from a closed list per family: sf.call (Element API; a clone must be read only and in the migration account; delete, purge and access changes are refused for volumes outside it) and pure.cli (Purity CLI over SSH with a key; changes only volumes the mover made, tracked in a ledger; never eradicate, copy, pods or host objects). New verbs: array.set, array.list, array.forget (the array login is a root-only file on the host and is never returned or traced), iscsi.login, iscsi.logout (only sessions the mover opened), dev.wait. New route POST /api/hosts/array and host check H10. Call-log arguments named password, secret or token are masked. The SolidFire and FlashArray adapters are not built; ONTAP stores snapshots as LUN clones and needs a design first. Safety rows 55 and 56.

## 0.9.2 (2026-11-04)

### Changed
- Playbook v1.4 reviewed against the engine (docs/PLAYBOOK_TRACE.md, section 'What changed in v1.4'). The staged engine now compares the source with what was staged at every sync tick, right before the freeze and before the switch: a volume extended during the copy grows the staging volume (new mover verb lv.extend, only on volumes OpenMove made, never smaller); a changed volume type gives the volume up; a size change after the last recheck rolls back with nothing frozen. The post-check compares size, project, user and the snapshot list with a capture taken at the end of the freeze (freeze.json) and the fixed fields with the capture before the cutover, so a tenant transfer or snapshot before the freeze no longer fails it. New Phase 0 test T23. The rollback clone text says it is a point in time. Safety row 54.

## 0.9.1 (2026-11-03)

### Added
- The host setup's iSCSI, CHAP and multipath paths are proven on a real virtual machine with its own kernel (two portals, one multipath device with two paths, a lost path, a reboot); the setup script now waits for the multipath map before it reports; lab commands vm-up and vm-down with tools/lab/vm scripts

## 0.9.0 (2026-11-03)

### Added
- The class C staged method with the data mover, the Cinder helper and SSH; automatic setup of a migration host from an address and an administrator login, including its iSCSI and multipath paths; the deployment guide and self-check; a larger lab catalogue
- Class C: the staged method (`engine/staged.py`): precheck, validate, copy, sync, two rechecks 300 seconds apart, cutover, postcheck, soak. G2, G3 and G4 are signed by a second person. A detached volume is switched with Cinder's migration completion; an attached volume with a Nova volume swap. Rollbacks R1 (abort), R2 (undo the cutover) and R3 (reverse). The state of each volume is kept in the journal, so a restart carries on.
- The data mover (`resources/mover/mover.py`, Python 3.6 or later, standard library only) and the Cinder helper (`resources/mover/cinder_helper.py`), placed and checked on the hosts by the tool itself
- SSH with the system OpenSSH client: keys only, OpenMove's own key, host keys pinned by a person, every call traced (`connectors/ssh.py`)
- The Hosts tab: register a migration host and a Cinder host, show the public key, pin host keys, run the host checks H1 to H9, C1 and C2
- An `lvm` array adapter (`arrays/lvm.py`) and a fake fabric (`demo/fake_fabric.py`) so the whole method runs without arrays
- Diagnostics for dark sites (`diag.py`, `docs/DIAGNOSTICS.md`): a full log and a trace of every host call, a support bundle with a checksum manifest that is written by itself on a halt or an unexpected error, and redaction at three levels with a local-only token map (`redact.py`; `--bundle` and `--decode` on the command line)
- Lab: the two lab hosts (the DevStack distro and a separate data-mover distro) are made, started, registered and checked by `openmove/lab/hosts.py` without a terminal; `tools/lab/lab_staged.py` drives a real class C move
- README rewritten with the operations, the steps and links to every document; diagrams no longer clip their text
- Set up a host from OpenMove: the Hosts tab takes the address and the administrator login of a plain Linux machine; OpenMove logs in once with the password (`ssh.RootLogin`, through `SSH_ASKPASS`, never stored or logged), after a person pinned the host key, runs `prepare_host.sh`, reads the Cinder interpreter, sets up the storage paths with the new `configure_storage.sh` (iSCSI initiator, CHAP, multipath drop-in for Pure FlashArray and SolidFire, logins, rescan; Fibre Channel port names), registers the host under the service account, proves the key-only login and runs the host checks; routes `POST /api/hosts/probe` and `POST /api/hosts/setup`; journal event `host_setup`; SAFETY rows 51 to 53; `tests/test_bootstrap.py`; the lab command `python tools/lab/lab.py fresh-host` makes a bare host to try it on. Proven in the lab on a bare host, also through the interface; the iSCSI logins and multipath maps are not proven (WSL shares one kernel)
- Deployment for class A, B and C (`docs/DEPLOYMENT.md`): a host setup script (`resources/deploy/prepare_host.sh`: account, packages, key, narrow sudo rights, sshd; safe to run again; `--check` changes nothing), shown on the Hosts tab and by `--host-setup`; a self-check of an installation (`--self-check`, `selfcheck.py`) that also runs in the packaged program and the container image; the container image now holds the SSH client; the packaged program names every class C module
- Lab: `OpenMove-Lab.bat` now also restarts units that failed at boot, waits for the compute service and starts the guests that should run, then prepares both lab hosts; a second, larger catalogue of every class (`OpenMove-Lab.bat more`: 12 more detached, attached, boot and paused class A volumes, 6 detached and 3 attached class C volumes with 1 to 8 snapshots, more class D and E cases); `tools/lab/lab_staged.py reverse`
- Documents: `docs/MOVER.md`, `docs/DIAGNOSTICS.md`; safety rows 46 to 50

### Fixed
- Found in the lab: an attached volume was cut over while its guest and compute service were down (Nova still said ACTIVE); the guest's power state and the compute service are now checked before the swap (N4)
- R2 checks that the source is back in its status from before the freeze before it says the volume is as it was
- Found in the lab: the Nova volume swap was sent with `volume_id` instead of `volumeId` and real Nova refused it (the rollback R2 ran by itself); the mock now validates the body
- Found in the lab: the Cinder helper refused a source volume that OpenMove had itself frozen; the engine refused to start again on a volume left frozen in the middle of a rollback
- A secret written into free text (`password=...`, a bearer token) is redacted
- The data mover works on Windows as well (process check, file replacement)

## 0.8.0 (2026-11-03)

### Added
- Reports: the update report and the after report compare every volume the run touched with the state recorded before its cutover, each difference marked expected or unexpected; the volume list as Excel and CSV (standard library only)
- Back-end families and the support matrix as data (`resources/backends.json`, `docs/SUPPORT_MATRIX.md`); check P3 fails for a back-end it does not know and warns for a pair nobody verified
- Phase 0 lab tests T1 to T22 and four OpenMove tests as guided procedures, a recorded result per test, and gate G0 recordable from the results only when every test of the retype path has passed
- A run window (UTC start and stop); a preview of a reverse; putting the whole run back
- `Dockerfile` and `.dockerignore` (written, not built), `docs/NETWORK.md` with a test that every connection-opening module is named, `docs/COVERAGE.md` (the tool against the requirements and the proposal)
- Lab failure injection recorded: an instance locked by a tenant, HALT during an attached move, the Cinder API and the Cinder volume service stopped during a move
- The full test suite passes on Linux (Ubuntu) as well as Windows

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
