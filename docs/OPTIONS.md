# Every option and setting

One page for everything that can be chosen in OpenMove: how it is started, what each screen asks for, every setting with its default and its limits, and the fixed values that cannot be changed. The process these options belong to is in `WALKTHROUGH.md`.

## 1. Starting it

### Launchers (Windows)

| File | Does |
|---|---|
| `OpenMove.bat` | Starts OpenMove against a real cloud |
| `OpenMove-Demo.bat` | Starts the demonstration: a built-in cloud with made-up data. Nothing real is touched |
| `OpenMove-Lab.bat` | Starts the OpenStack lab on this machine if needed, then OpenMove in lab mode, and opens the lab's dashboard already signed in. Sub-commands: `up`, `down`, `seed`, `clean`, `list`, `status`, `reset` |
| `OpenMove-Lab-Stop.bat` | Stops the lab gracefully (its data is kept). Close OpenMove first |
| `openmove.sh` | The same as `OpenMove.bat` on macOS and Linux |

### Command line

`python -m openmove [options]`

| Option | Default | Meaning |
|---|---|---|
| `--demo` | off | Demonstration mode: a built-in cloud, made-up data, a temporary data folder that is deleted on exit. Every screen has a purple banner |
| `--scenario <id>` | `sf-to-pure` | With `--demo`: which cloud to play. `--list-scenarios` lists them |
| `--list-scenarios` | | Print the scenarios and exit |
| `--lab` | off | Lab mode: connect to the OpenStack lab on this machine and use the real engine. Uses its own data folder `C:\OpenMoveLab\openmove-home` |
| `--lab-reset` | off | With `--lab`: forget the lab's journal and evidence first |
| `--host <address>` | `127.0.0.1` | Address to listen on. Anything else lets other machines use the tool with the token and prints a warning. There is no user login in this version |
| `--port <n>` | `8765` | Port to listen on |
| `--home <folder>` | the user data folder | Where the journal and evidence are kept |
| `--allow-host <name>` | none | An extra name clients may use in the address (behind a container port mapping or a reverse proxy). Repeatable |
| `--no-browser` | off | Print the link, do not open the browser |
| `--supervise` | off (on in `OpenMove.bat` and `OpenMove-Lab.bat`) | Run under a supervisor that starts OpenMove again if it stops or stops answering, and carries on a move that was running (`RECOVERY.md`) |
| `--self-check` | off | Check that this installation holds everything class A, B and C need and exit (`DEPLOYMENT.md`) |
| `--host-setup ROLE` | off | Write `prepare_host.sh` (to `--out`, default the current folder) and print the command that runs it, with OpenMove's key filled in; ROLE is `mover`, `cinder` or `both` |

### Environment variables

| Variable | Meaning |
|---|---|
| `OPENMOVE_HOME` | Same as `--home` (the container image sets it to `/data`) |
| `OPENMOVE_LAB_DIR` | Where the lab's `lab.json` and `lab-secret.json` are (default `C:\OpenMoveLab`) |
| `OPENMOVE_BUILD_DIR`, `OPENMOVE_DIST_DIR` | For building the packaged program on a synced folder (`INSTALL.md`) |

### Demonstration scenarios

| Id | Cloud |
|---|---|
| `sf-to-pure` | One SolidFire and one Pure FlashArray back-end. The simplest end-to-end demonstration |
| `sf-to-ontap` | The same cloud with NetApp ONTAP as the destination |
| `enterprise` | Large and busy: two SolidFire clusters, Pure (production and DR), ONTAP and Ceph; 14 projects; over 300 volumes covering every class and many problems. Use *Load the demonstration database export* on the Inventory step to add what only the Cinder database holds |

The scenario can also be switched on the page; doing so forgets everything read so far and closes the later steps.

## 2. Modes

| Mode | Cloud | Engine | Second person | Use for |
|---|---|---|---|---|
| Real | Your OpenStack | Real (class A and B, by retype) | A real second person | The migration |
| Demonstration (`--demo`) | Built in, made up | Simulated (the staged method, a virtual clock) | A button signs for a made-up person | Learning, showing, checking the screens |
| Lab (`--lab`) | The OpenStack lab on this machine | Real | Buttons for the lab only | Testing the engine |

## 3. Step 1: Connect

| Field | Default | Meaning |
|---|---|---|
| Identity (Keystone) URL | none (lab: filled in) | The Keystone v3 address, for example `https://cloud.example:5000`. Not the dashboard address. The lab's is `http://localhost/identity` |
| User name | none (lab: `admin`) | An administrator |
| Password | none (lab: left blank, the lab's own is used) | Kept in memory for this session only |
| Project | `admin` | The project to scope the login to |
| User domain, Project domain | `Default` | |
| Region | empty | Only if the cloud has more than one |
| Endpoint interface | `public` | `public`, `internal` or `admin`: which catalogue endpoints to use |
| CA certificate file | empty | A path on this machine, for a cloud with a private CA |
| Verify TLS certificates | on | Turning it off is allowed but is stated in the reports |

## 4. Step 3: Inventory

| Option | Default | Meaning |
|---|---|---|
| Back-end | all back-ends | Only read the volumes of one back-end |
| Rotation window (days) | 14 | A volume whose snapshots all disappear within this window is class B (wait), otherwise class C |
| Import | none | Paste or load the playbook's database exports (A1 volumes, A2 snapshot history, A4 provider data, instances) to learn what only the database knows. Needed to tell class B from class C |

## 5. Step 5: Pre-flight

| Option | Choices | Meaning |
|---|---|---|
| Source back-end | any back-end found in step 2 | Where the volumes are now |
| Target back-end | any back-end found in step 2 | Where they go. Volumes already there are not admitted |
| What OpenMove can reach | **The OpenStack APIs only** (default), or **APIs, hosts and arrays** | With the APIs only, class A and B are moved by Cinder and class C is held back with the cause "No data mover". With hosts registered and their checks passed (the Hosts tab) class C is admitted and moved by the staged method (built; proven on LVM only) |
| Attest | per check | A named person states a warning or not-checked item was checked, with the evidence |
| Sign as operator, Sign as reviewer | two different names | Both signatures are needed |
| Record lab validation (gate G0) | two different people, at least 40 characters of evidence | Lifts the read-only state of a release that has no recorded validation |

## 6. Step 6: Select

### Grouping

Volumes can be chosen by: **class**, **project**, **attachment**, **size**, **snapshots**, **Cinder host**, **instance**, **workload risk**. For each group: **Add** (add its volumes), **Only** (select just these), **Remove**.

Buttons: **Select all eligible**, **Select none**, **Back to the default selection** (every admitted volume except those that need a risk clearance), the filter box, the tick boxes, **Confirm this selection**.

### Settings

They start at their defaults. A value outside its limits is refused, never changed quietly. The floors are the minimum of the process and cannot be lowered. The settings can be changed at any time; the selection cannot be changed while a run is going.

| Setting | Default (real) | Default (demo) | Limits | Effect |
|---|---|---|---|---|
| Copies at the same time (`concurrency`) | 10 | 16 | 1 to 32 | How many volumes move data at the same time: the copy, the sync and the cutover. A volume that waits for a person, for its snapshots to rotate away, or is in its safety period does not use a copy place. A volume that is ready and has both approvals waits for a free copy place, and takes it as soon as one is free. Lower is gentler on production |
| Volumes in progress at once (`max_in_flight`) | 30 | 30 | 1 to 500 | How many volumes are copying, or ready and waiting for a person or a copy place, at once. It bounds the space used on the new storage, the staging copies that can go out of date, and the number of approvals open at one time. It cannot be lower than the copies at the same time. A volume that waits for snapshots to rotate away or is in its safety period does not count |
| Copy rate cap, MB/s per host (`rate_cap_mb_s`) | 200 | 600 | 10 to 2000 | For the staged method: the data mover slows a copy to this rate (`rate_mbps` on a host entry sets it for one host) |
| Rechecks before cutover (`rechecks`) | 2 | 2 | 2 to 5 | Passed rechecks in a row before a cutover (staged method). Two is the minimum of the process |
| Minutes between rechecks (`recheck_gap_min`) | 5 | 5 | 5 to 120 | Staged method. Five is the minimum |
| Soak period, days (`soak_days`) | 7 | 7 | 1 to 90 | How long the source is kept untouched after the move. A reverse needs it. One day is the minimum |
| Hold each volume until released (`hold_before_cutover`) | on | on | on or off | Each volume waits after G2 and G3 until the operator releases it |
| Halt automatically after this many failed volumes (`stop_after_failures`) | 10 | 10 | 0 to 500 (0 = never) | A run that keeps failing stops itself |

### Workload risk

A volume that hosts something that may break (a legacy Windows server, no guest agent, a database, a cluster) is **not moved** until two different people clear it. Options: **Clear ticked as operator**, **Clear ticked as reviewer**, **Withdraw clearance**, **Mark ticked volumes as a risk**, **Remove the mark**. The check reads hints only and cannot see inside a guest. See `WORKLOAD_RISKS.md`.

## 7. Step 7: Move

| Control | Meaning |
|---|---|
| Target volume type | The volume type to move to. Empty means the only type that matches the target back-end, if there is exactly one |
| 1. Dry run (changes nothing) | Reads the cloud again and lists the exact calls and their undo for each volume. Required before a real run |
| 2. Start the run | Needs your name. Needs a permit now: release validated, gate approved and fresh, no halt |
| Run window opens, closes (UTC) | Optional. Outside the window no new volume is taken and no retype is sent; what is already moving carries on and is watched. Empty means no limit. A window that ends before it starts is refused |
| Stop taking new volumes | The run finishes the volumes in flight and takes no new ones. It does not abort a retype that was sent |
| Refresh | Reads the state again (the page also refreshes by itself) |
| Record lab validation (G0) | As in step 5 |
| Sign G2 and G3 | A second person, per volume, before it moves |
| Release all for the retype | Lets the volumes held after G2 and G3 go |
| Sign G4 | A second person, per volume, after the post-check passed and the owner has checked the application |
| Reverse (per volume) | Puts a volume back with a retype back to its original type, locking its instance first. Needs a name and a reason |
| Preview: put the whole run back | Reads the cloud and lists, for every cut-over volume, the calls a reverse would make and their undo. Changes nothing |
| Put the whole run back | Reverses every cut-over volume of the run, each journaled and checked on its own. Needs a name and a reason |
| Phase 0: the lab tests | The playbook's tests T1 to T23 and OpenMove's M1 to M4 as guided procedures. Per test: Record the result (pass or fail, your name, what you did and saw, at least 40 characters). The newest result counts |
| Record gate G0 from these results | Two different people. Refused unless every test of the retype path has passed for this release. Tests of the staged method are listed but not required |
| HALT (always visible) | Stops all changes at once. Needs a name and a reason. Resume needs a name and a reason |

## 8. Step 8: Reports

| Option | Meaning |
|---|---|
| Customer or system name | Printed on the title page and in the footer. A demonstration report says it is synthetic |
| Prepared by | Your name |
| Include the environment audit | In the pre-migration report |
| Reports | Pre-migration (needs the pre-flight), Environment audit (needs the audit), Volumes not migrated (needs the pre-flight), Update report and After report (need a run), Volume list as Excel or CSV (needs the inventory) |

## 9. Gates and signatures at a glance

| Gate | What it covers | Who signs | When |
|---|---|---|---|
| G0 | Lab validation of this OpenStack release | Two different people, with at least 40 characters of evidence | Once per release, before the first real run |
| G1 (the pre-flight gate) | No failed check, every open item attested, both signatures, run less than 24 hours old | Operator and reviewer | Step 5 |
| G2, G3 | The volume is ready to move: before-state and target checked | A second person, not the operator | Per volume, before the retype |
| G4 | The move is verified from the application's point of view | A second person, not the operator | Per volume, after the post-check, before the soak |

## 10. Fixed values (not options)

| Value | Meaning |
|---|---|
| Gate age 24 hours | A gate older than this is stale; run the pre-flight again |
| Tick 5 seconds | How often the engine looks at each volume |
| Retype time limit 6 hours | A retype running longer halts the engine for a person to look |
| Class B wait limit 72 hours | A class B volume that is still holding snapshots after this is given up |
| Undone-retype grace 30 seconds | A volume back to normal and not on the target this long after the retype call counts as a retype that Cinder silently undid, and is rolled back |
| Back-end families | `openmove/resources/backends.json`: SolidFire, Pure FlashArray, ONTAP, Ceph and LVM are recognised from what the Cinder driver reports. A back-end that matches none is unsupported (check P3 fails). A known pair nobody verified is a warning. See `SUPPORT_MATRIX.md` |
| Nova microversion 2.9 | Needed to see whether an instance is locked; an instance whose lock state cannot be read is never touched |
| Soak minimum 1 day, rechecks minimum 2 times 5 minutes | The floors of the process |
| Interface on the local machine | The page listens on `127.0.0.1` only unless `--host` says otherwise, and every call needs the token printed at start |
