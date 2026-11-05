# Operations guide

How to run OpenMove, from the day before a run to the day after. It is written for the people who operate a migration: the operator who runs the tool, and the second person who checks and signs. For installation see `INSTALL.md`. For each screen see `USER_GUIDE.md`. For what happens at each step inside the tool see `PROCESS.md`, and for the hazards and their controls see `SAFETY.md`.

**What this version can do.** It moves class A volumes (no snapshots) and class B volumes (snapshots that rotate away) between two back-ends of one cloud by Cinder's retype, using the OpenStack APIs only. It does not move class C volumes (long-lived snapshots). It has been run against a development lab, never against a customer cloud. Treat the first run in any real environment as a rehearsal on a test environment.

## 1. People and roles

| Role | Does |
|---|---|
| **Operator** | Connects, runs the pre-flight, selects volumes, runs the dry run, starts the run, releases volumes, watches. Signs the pre-flight gate as *operator*. |
| **Reviewer** | Signs the pre-flight gate as *reviewer*. A different person from the operator. |
| **Second person** | Signs G2 and G3 for each volume before its move, and G4 after its post-check. Must not be the operator. Looks at the evidence, not at the button. |
| **Platform owner** | Owns the cloud: confirms capacity, volume types, maintenance window and the way back. |
| **Instance and volume owners** | Are told what moves and when; check their application after the move. |

The tool records every name and what was typed with it. A name is a claim by whoever types it; the interface has no login in this version, so the process (who sits at the keyboard) has to hold the four-eyes rule.

## 2. Before the day

Do these first. None needs OpenMove except the first.

| # | Check | Why |
|---|---|---|
| 1 | The OpenStack release has a **recorded lab validation** (gate G0). If not, people run the playbook's lab tests on a copy of the platform and record the result (section 5, step 2). | Until it is recorded, the tool is read only. |
| 2 | A target **volume type** exists, is bound to the target back-end, and is visible to the projects whose volumes move. | The retype needs it. The tool refuses a type that is not bound to the target back-end. |
| 3 | The **target back-end has room** for everything you select, with margin. | A retype that runs out of space fails late. The pre-flight checks the pool when the cloud reports it. |
| 4 | **Project quotas** for the target type are large enough. | A retype can fail on a quota. |
| 5 | A **backup of the Cinder database** is taken and a restore proven (check P10). With the APIs only this is an attestation by a named person. | The first thing needed if something goes wrong that OpenMove cannot see. |
| 6 | A **maintenance window** is agreed and the owners of attached volumes are told. | The instance is locked for the duration of its move. |
| 7 | **Snapshots**: volumes with snapshots are class C. They are moved by the staged method through a **migration host** and a **Cinder host**: register both on the Helper servers tab, add OpenMove's public key to each, pin the host keys, and run the host checks until they pass (`MOVER.md`). Without them, class C volumes are held back. | The staged method needs the data mover and the Cinder helper. |
| 8 | **Workloads that may break**: look at the Select tab's workload risk panel. Legacy Windows, clusters and databases are flagged. | A high-risk volume is not moved unless two people clear it with evidence. |
| 9 | Decide the **settings**: hold before the cutover (leave on), soak days (7 or more), concurrency (start low), stop after N failures. | They can only be made stricter than the process minimums. |
| 10 | **Back up the OpenMove data folder** if a previous run left records you need. | `INSTALL.md` section 6. |

### Class C: what to prepare in addition

| # | Check | Why |
|---|---|---|
| C-1 | A **migration host** (Linux, Python 3.6 or later, root or passwordless sudo) that sees the storage of **both** back-ends, and a **Cinder host** (the machine that runs `cinder-volume` for them). One machine may be both. | The data mover copies from array to array there; the Cinder helper creates and removes records there. |
| C-2 | OpenMove's **public key** (shown on the Helper servers tab) is in `authorized_keys` of the SSH user on each host. No password is ever used. | Keys only (`MOVER.md`, section 2). |
| C-3 | A person **compares the host key fingerprint** with the host's own and pins it (Check host key). | A changed key later refuses the connection. |
| C-4 | The **host checks** (H1 to H9, C1, C2) all pass for the chosen pair of back-ends; the data mover is placed by the check. | A failed check keeps class C volumes out of the run. |
| C-5 | Free space on the target: **size x (1 + number of snapshots)** per volume. | The staging volume holds the volume and every snapshot image. |
| C-6 | The **storage paths** of the migration host are configured and the host's names are registered on the arrays: use **Read the host** on the Helper servers tab, copy the iSCSI initiator name (or the Fibre Channel port names), register it on each array (host entry or access group), zone Fibre Channel, then apply the configuration (section "Configuring a helper server" below). | The host must see the volumes of both back-ends. OpenMove cannot register a host on an array. |
| C-7 | **Support bundle settings** are decided (Diagnostics tab): the level of redaction the site needs, and a person knows how to carry a bundle out (`DIAGNOSTICS.md`). | If anything goes wrong, the data to fix it is already on the machine. |

### Configuring a helper server

A helper server is managed through OpenMove only. Once it is set up, nobody needs to log in to it to change a storage path.

1. **First setup** (Helper servers tab, step 2): name, address, administrator login and password, then **read the server's key** and compare its fingerprint with the server's own. Choose iSCSI or Fibre Channel and press **Read the host**: the interfaces, the initiator name, the Fibre Channel ports and the sessions are listed. Fill in the form (portals, target names, CHAP, interfaces to bind, addresses and MTU of the storage interfaces, or the Fibre Channel target ports). Press **Check only**, read the result, then **Set up the server**. The password is used once and dropped.
2. **Register the names on the arrays.** The names to use are shown by Read the host, with a Copy button: the iSCSI initiator name and the Fibre Channel port names. OpenMove cannot do this on the arrays; the storage administrator does. For Fibre Channel, zone the ports to the array ports.
3. **Later changes** (step 3 of the tab): choose the server, **Read the host**, change the form, enter your name, **Check only**, then **Apply the configuration**. It needs OpenMove's key only. The CHAP secret is asked again.
4. **Verify.** Read the host again: the sessions are `LOGGED_IN`, each multipath map shows all its paths working, the Fibre Channel target ports expected are seen. Then run the host checks (H1 to H9, C1, C2) with **Check the servers**.
5. A server prepared before the control program existed shows "run the setup of this host once more": run step 2 once with the administrator password.

The management interface (the one OpenMove connects through) is shown locked and is never changed. A change to an interface address or MTU is written to `/etc/openmove/network.conf` and applied again at every boot. `DEPLOYMENT.md` section 5.2 has the limits.

## 3. Start of the day

1. Start OpenMove (`OpenMove.bat`, `./openmove.sh`, or `python -m openmove`). Keep the console window open: it shows the link and the data folder, and closing it stops OpenMove.
2. Make sure the second person can use the same screen, or is at the machine, when a signature is needed.
3. Make sure nothing else is changing the cloud (another migration, a maintenance task).

## 4. The procedure, step by step

The screen has seven numbered steps (Connect, Scan, Safety checks, Choose, Move, Progress, Reports). The headings below use the technical names, which are the ones in the journal and in the other documents: *Platform*, *Inventory* and *Audit* are the three parts of the screen step Scan; *Pre-flight and the gate* is Safety checks; *Select* is Choose; *the run* is Move and Progress. *Back* and *Next* sit at the top, under the tabs. Everything that waits for a person (approvals, releases, confirmations) is listed as **Open actions** in the board at the top of every page, with the button that does it. The steps are **gated**: a step opens only when every step before it is done, and the page says what is missing. You cannot skip the pre-flight, the selection confirmation or the dry run. Screenshots of every step are in `WALKTHROUGH.md`; every option is in `OPTIONS.md`.

### Step 1. Connect

* Enter the Keystone address (not Horizon), an administrator account, project, user and project domains, region and interface. Leave TLS verification on.
* Press *Connect*. The password stays in memory only.
* Expect: "Connected". The header shows the connection.

### Step 2. Platform

* Press *Detect version and back-ends*.
* Read: the release and its confidence; whether the profile is `validated` or `untested`; the back-ends with their pools; the Cinder services (all up?); the compute hosts.
* If the release is **untested**, the banner says **Read only**. Nothing will be moved until G0 is recorded (see step 7, "Record lab validation").
* If a Cinder service for either back-end is down, stop and fix that first.

### Step 3. Inventory

* Choose the source back-end and the rotation window (days). Press *Read from the cloud*.
* Read the class counts. A to E, with a reason for each volume. Class B and the array volume id need the Cinder database; without it OpenMove says what it assumed, and a volume with snapshots is class C.
* If you have the database export the playbook defines (queries A1, A2, A4), import it here. It lets the classifier see snapshot history.
* Check that the volumes you expect are there, in the classes you expect. Volumes you did not know about (for example Cinder's image-cache volume) appear here.

### Step 4. Audit

* Press *Run the audit* and read the findings by severity. Fix or accept each high finding before you go on: services down, volumes stuck in odd states, orphans, nearly full pools.
* This step only reads.

### Step 5. Pre-flight and the gate

1. Choose the **source** and **target** back-end. Choose **What OpenMove can reach**: "OpenStack APIs only" (default).
2. Press *Run all pre-flight checks*.
3. Read the result. For each check: pass, info, warn, not checked, fail.
   * **Fail** cannot be waived. Fix the cause and run again.
   * **Warn** and **not checked**: fix, or attest. To attest, press *Attest*, type your name and the evidence (at least eight characters: what you checked, where, what you saw).
4. When the gate shows **Awaiting sign-off**, two different people sign: one as *operator*, one as *reviewer*.
5. The gate must show **Approved**. It goes **Stale** after 24 hours. Sign-offs do not carry over to a new run.

Volumes that fail a per-volume check are held back and listed with the reason. Volumes already on the target back-end are not admitted (nothing to move).

### Step 6. Select

* Default: every admitted volume is selected except the high-risk ones.
* Change by group (class, project, attachment, size, snapshots, Cinder host, instance, workload risk) or by single volume.
* Check the settings. A value stricter than the minimum is fine; a looser one is refused.
* For a first run in a new environment, **select a handful**: one detached volume, one attached to a running instance, and (if you have one) a boot volume. Add more after those succeed.
* The selection cannot change while a run is going.
* **Confirm this selection** (your name). Step 7 stays closed until you do. If you change the selection, or run the pre-flight again, the confirmation is void and you confirm again.

### Step 7. Move

The Move tab. The note at the top lists the three things a real run needs: a recorded lab validation, an approved gate, and no halt.

1. **Record lab validation (G0)**, if the release is untested and the lab tests were run. Two different people give their names and describe what was tested and what came out (at least 40 characters: which tests, on which cloud, which result). This is a declaration, kept in the journal. Nothing is tested by pressing the button.
2. **Target volume type.** Type the name of the target volume type. If you leave it empty, the tool uses the single type bound to the target back-end, and refuses if there are several or none.
3. **Dry run** (required: *Start the run* is refused until a dry run has covered the same volumes). Press *Dry run*. Nothing is sent. Read each row:
   * the verdict: `ok`, `wait` (a class B volume with snapshots still to rotate) or `blocked`;
   * for `blocked`, the failed check and its reason;
   * the exact calls the run would make, each with its undo: a lock, one retype with `migration_policy: on-demand`, an unlock after the post-check.
   Stop here if anything surprises you. A blocked volume is not a failure of the tool; it is the tool refusing something unsafe.
4. **Start the run.** Press *Start the run* and enter your name (the operator). The engine takes the selected volumes, up to the concurrency setting.
5. Each volume now goes through the **validate** phase and stops at **"waiting for a second person to sign G2 and G3"**.

### Step 8. During the run

**The second person signs G2 and G3.** Before signing, they check, for each volume: the dry-run verdict, the before-state in the volume panel (class, size, type, attachments, instance), the evidence file `validated.json`, and that the target is right. On the Move tab press *Sign G2 and G3 (second person)*, enter your name and what you checked.

**The operator releases.** If *hold before the cutover* is on (the default) a ready volume waits for a release. Press *Release all for the retype*, or release volumes one by one on the Select tab. A high-risk volume always waits for a release.

**Cutover.** For each released volume the engine:

1. reads everything again; if anything changed since the validation, it gives the volume up (nothing moved);
2. records the before-state (`before.json`);
3. locks the instance, if the volume is attached and not already locked;
4. sends one retype with on-demand migration;
5. reads the volume every few seconds, showing `migration_status` and `status`.

**Post-check.** When Cinder reports success, the engine compares the volume with the before-state: status, size, attachments, bootable flag, and that each instance is ACTIVE. If it passes, it unlocks the instance and waits for G4. If it fails, it **halts everything** and leaves the instance locked.

**What you watch.** The Move tab's table shows, per volume, the phase, G2, G3, G4 and a note. The Live tab shows the overall progress, the order, the event feed and the volumes not migrated. The OpenStack dashboard (Horizon) and the Cinder service logs show the cloud's own view. In the lab a 1 GiB volume took about 25 seconds. A large volume on a busy back-end takes as long as the copy.

### Step 9. After the post-check: verify and sign G4

* Ask the owner of the instance to check the application from its new path. For a boot volume, check that the instance is running and reachable.
* Read `after.json`: it holds the volume and the servers after the move and the post-check results.
* The second person presses *Sign G4 (second person)* and enters what was verified. Without G4 the soak does not start.

### Step 10. Soak

* After G4 the volume is in **soak** for the set number of days (default 7, minimum 1). With a retype, Cinder deletes the source volume itself when the move ends, so the soak is an observation period: it keeps the way back (a retype back) open and gives the owners time to find a problem.
* While the volume is in soak, the engine keeps it in its active list. After the days pass, the volume is `done`.
* You may close OpenMove during the soak. See section 6 to resume.

### Step 11. Report

On the Reports tab:

* **Pre-migration report** (Word): the platform, every check, the volumes to move, the volumes held back and why, assumptions, sign-offs. Build it **before** the run and keep it.
* **Volumes not migrated** (Word): every volume that stopped, was rolled back, or was held back, with the cause and proposed steps.
* **Environment audit** (Word).

Reports are built from what OpenMove read. "Not checked" stays "not checked".

## 5. Reversing a volume

Use it when the owner reports a problem, or when a post-check or the application check fails.

1. Find the volume on the Move tab. It must be in `cutover`, `postcheck` or `soak`.
2. Press *Reverse*. Enter your name and a reason of at least eight characters.
3. The engine enters **rollback**, locks the instance (if attached), sends a retype back to the first type, waits for it, unlocks the instance and records `done`. It reads the volume to check it is on the source back-end and type again.
4. Tell the owner. Check the application again.

The way back is itself a move and has the same risks. Do not reverse a volume that is moving. Wait until the first retype ends.

## 6. Stopping, halting, restarting

| Action | What it does | What it does not do |
|---|---|---|
| **Stop taking new volumes** | Stops the engine's loop. Retypes already sent carry on in the cloud. | It does not cancel a retype. There is no way to cancel one from here. |
| **HALT** (red button, header) | Needs a name and a reason. The next change is refused, whatever else is true. Any person may press it. | It does not undo anything. |
| **Resume** (red banner) | Needs a name and a reason. Do it only when the cause is understood. | It does not retry a lost call by itself. The engine reads the cloud and settles it. |
| **Close OpenMove** | Stops the process. The journal is safe. | A retype already sent continues in the cloud. |

**After a restart, or after a crash** (`RECOVERY.md` has the whole story):

1. If OpenMove was started with the batch file it restarts by itself within seconds when only the program ended; after a restart of the computer, start the batch file again. The page that was open keeps its address and carries on by itself.
2. Sign in to the cloud again (the password is not stored; the lab signs in by itself). The page shows "OpenMove was restarted. Your work is saved." with a **Sign in again** button that fills in the address, user and project.
3. OpenMove reads the cloud again and puts the saved work back: the safety checks of the same run (their approvals are in the journal and still count), the choice of volumes, the options, the releases. It then **carries on the move that was running**. You do nothing else.
4. For a step that has an intent and no result, the engine reads the volume and closes the step with what the cloud shows. It never sends a retype twice.
5. If a halt was in force, resume it first, with a reason. If the page says it could not put everything back, press **Try again**, or **Start fresh** (the history stays; the move is then started from the Move step again, and it takes up every volume that is not finished).

## 7. What the notes mean

The "What is happening" column and the event feed use these. When a note is a problem, the right-hand column says what to do.

| Note (or part) | Meaning | What to do |
|---|---|---|
| `validated: waiting for a second person to sign G2 and G3` | Ready. Waiting for the second person. | They check and sign. |
| `ready: G2 and G3 are signed; waiting for you to release it` | Held before the cutover. | Release it when the window is right. |
| `not started: ...` / `not cut over yet: ...` | A rule refused a step. The text says which. | Read it. Fix the cause or leave the volume. |
| `waiting for the N snapshot(s) to rotate away` | Class B. Snapshots remain. | Wait (up to 72 hours), or remove them with their owner. |
| `retype sent: the cloud is copying the volume` | The retype was accepted. | Wait. |
| `retype running: migration_status=migrating, status=retyping` | Cinder and Nova are copying. | Wait. |
| `moved; the post-check passed: waiting for a second person to sign G4` | Done and checked. | Verify, then sign G4. |
| `G4 signed: the soak period has started` | Soak. | Nothing until the days pass. |
| `the cloud refused a call; nothing was moved: ...` | A lock or the retype was refused (HTTP error). The volume went to rollback with the reason. | Read the reason (quota, type, state, policy). Fix and run again. |
| `the move failed in the cloud: rolling back` | `migration_status` is `error` and the volume is usable. | Wait for the rollback. Look at the Cinder logs. |
| `the volume is in status error...: halted for a person to look` | The volume is in an error status. | Do not retry. Look at the cloud first; an administrator may need to reset the state. |
| `the post-check failed: halted. The instance stays locked.` | The result differs from the before-state. | Look at the volume and the instance. Reverse, or fix by hand. Then resume. |
| `an unknown answer was read back and settled` | A call had no answer; the engine read the cloud and closed the step. | Check the volume. |
| `waiting: all changes are halted by ...` | HALT is in force. | Read the reason. Resume when resolved. |
| `waiting: ... gate ...` | The gate went stale or a sign-off was withdrawn. | Run the pre-flight again and sign. |
| `could not continue (...); will look again` | A read failed (network, token). | Wait. If it repeats, check the connection. |
| `the retype of ... has run for more than 6 hours` | Timeout. The engine halts. | Look at the Cinder and Nova logs and the hosts. |
| `instance ... is already locked by someone else` (dry run) | The instance was locked before. | It is left locked, and not unlocked. Ask who locked it. |

## 8. Things that can go wrong in the cloud that OpenMove cannot see

OpenMove sees the OpenStack APIs. It does not see the hypervisor, the arrays, the network between them or the guest. Plan for:

* A retype that runs very long on a busy back-end. The engine halts after 6 hours; it does not cancel.
* A volume left in `retyping` or `error`. An administrator may need to reset the state with Cinder's own tools. OpenMove does not do this.
* A guest that did not like the move (a legacy Windows server, a cluster member, a database). The post-check proves the instance is ACTIVE, not that the application is healthy. The owner's check is part of G4.
* A quota or a type-access rule that blocks the target. Check before the run.

## 9. Evidence and records to keep

| Record | Where | Keep because |
|---|---|---|
| The journal | `journal.db` in the data folder | Who signed what, every change and its result. |
| The evidence folder | `evidence/<volume id>/` | The before, validated and after state and every call. |
| The pre-migration report and the not-migrated report | Your downloads | What was planned, and what was held back and why. |
| The lab validation record | The journal and the lab's own notes | The basis for lifting read only. |

Do not put any of them in a source repository.

## 10. Checklists

**Before pressing *Start the run***

- [ ] The release has a recorded lab validation.
- [ ] The platform shows all Cinder services up, and the target pool has room.
- [ ] The pre-flight is less than a few hours old and the gate shows **Approved**.
- [ ] The selection is what you intend (a handful on a first run).
- [ ] The dry run was read, and nothing in it surprised anyone.
- [ ] The second person is present.
- [ ] A database backup exists and a way back is agreed with the platform owner.
- [ ] The owners know the window.

**During**

- [ ] The second person checked each volume before signing G2 and G3.
- [ ] Concurrency is low enough for the load on the back-ends.
- [ ] Someone watches the table and the cloud's own monitoring.
- [ ] Any surprise: press HALT first, then read.

**After**

- [ ] Each moved volume was verified by its owner, then G4 was signed.
- [ ] The not-migrated report was built and every held-back volume has a decision.
- [ ] The journal and evidence folder are backed up.
- [ ] The soak period is in the calendar.
- [ ] Anything the run showed that differs from the documents is written down (and sent to the maintainer).

## 11. If you only want to try it

Use the demonstration (`OpenMove-Demo.bat`) to learn the screens with made-up data and a simulated migration. Use the lab (`OpenMove-Lab.bat`, see `LAB.md`) to run the real engine against a real OpenStack on your own machine. Neither touches a customer cloud.
