# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""What happens to each class of volume, and what is done about a volume that is not migrated (requirements R14, R15).

Two things live here:

  PLANS    the path of each class A to E, step by step, with what each step touches. This is the text the screens, the
           reports and docs/CLASSES.md show. The rules that enforce it are in openmove.lifecycle (a wrong class is refused).
  CAUSES   every reason a volume can fail to migrate, in plain words: why it happens, what state the source and the target
           are left in, and the proposed steps to resolve it.

unmigrated() builds, for every volume that is not migrated, one record that says what happened (from the journal), why
(the cause) and what to do next. The report and the live view are drawn from it, so they cannot disagree.
Nothing here talks to a cloud. Where the data needed is not available the record says so; it never guesses.
"""
from . import lifecycle as L

MIGRATED_PHASES = ("cutover", "postcheck", "soak", "done")

PLANS = {
    "A": {"title": "No snapshots", "method": "Standard retype or migrate (playbook phase 3)",
          "steps": [
              ("precheck", "The pre-flight gate is approved and the volume is in class A.", "reads"),
              ("validate", "Room on the target, mapping, host reach, and still no snapshot.", "reads"),
              ("copy", "A full copy is made to the target. The source is only read.", "reads source, writes target copy"),
              ("sync", "The target is kept in step with the source until the cutover.", "reads source, writes target copy"),
              ("recheck", "Checked twice, at least 5 minutes apart, source unchanged, nothing left to copy.", "reads"),
              ("cutover", "The only step on the production path. Allowed only when every rule holds.", "WRITES production path"),
              ("postcheck", "Checked from the new path: attachment, host, type, name id.", "reads"),
              ("soak", "The source is kept untouched for the agreed period. Rollback stays possible.", "none"),
              ("done", "Only now may the source be released.", "WRITES (release the source)")]},
    "B": {"title": "Snapshots that rotate away", "method": "Wait for the rotation, then the class A path (playbook phase 4)",
          "steps": [
              ("precheck", "As class A.", "reads"),
              ("watch", "Wait for the snapshot count to reach zero. Nothing is copied while snapshots remain. A wait that passes the agreed window is a stop, see the report.", "reads"),
              ("validate", "Refused unless the snapshot count is zero at that moment.", "reads"),
              ("copy ... done", "From here exactly the class A path.", "as class A")]},
    "C": {"title": "Long-lived snapshots", "method": "Staged migration that rebuilds the snapshot history (playbook phase 5)",
          "steps": [
              ("precheck", "The pre-flight gate is approved and the volume is in class C.", "reads"),
              ("validate", "Room for the volume and every snapshot image, snapshot ids map one to one, no snapshot in a transitional state.", "reads"),
              ("copy", "Rollback clone of the source taken. A staging volume is built from the oldest snapshot; each later snapshot is applied in order and "
                       "a snapshot is taken on the target with the SAME id.", "reads source, writes target staging volume"),
              ("sync", "Refused unless every image compares identical, the tail beyond a smaller image reads as zero, and the snapshot ids on the target equal the Cinder ids.", "reads"),
              ("recheck", "As class A, and each pass must also reconcile: no tenant snapshot was created or deleted since the last pass.", "reads"),
              ("cutover", "The destination record is created, the source is frozen, and the swap runs (instance swap if attached; final clone if detached). Rollback R2 is ready.", "WRITES production path"),
              ("postcheck", "Gate G4: the snapshot chain on the array matches Cinder, create-from-snapshot works, tenant-visible fields are identical.", "reads"),
              ("soak", "Rollback R3 stays possible. The source is kept.", "none"),
              ("done", "After the soak period the source and its clones are removed.", "WRITES (release the source)")]},
    "D": {"title": "Attached to an instance that is not running", "method": "Waits (class A or C path afterwards)",
          "steps": [
              ("hold", "Not started. The lifecycle refuses it at precheck. A root volume of a boot-from-volume instance waits until the instance runs.", "none"),
              ("then", "When the instance is ACTIVE or PAUSED the volume is classified again (A or C) and follows that path. The detached variant (admin lock and detach) "
                       "needs a recorded decision and is not started by this tool on its own.", "none")]},
    "E": {"title": "Not handled by this method", "method": "Not migrated; listed for a follow-up plan",
          "steps": [
              ("hold", "Never enters the order: the lifecycle refuses it at precheck. Encrypted, multiattach, in a group, replicated, wrong status, a migration in a "
                       "non-final state, or a malformed provider id.", "none"),
              ("report", "Each volume appears in the not-migrated report with its reason and the proposed steps.", "none")]},
}

# Status of the class handling in this release. Honest: planned and enforced is not the same as built.
COVERAGE = {
    "classification (A to E, with reasons)": "built, tested",
    "class rules in the order of a migration (lifecycle)": "built, tested",
    "admission (A, B, C with no failed check) and holding back (D, E)": "built, tested",
    "not-migrated report with causes and proposed steps": "built, tested",
    "simulated run for every class (demonstration only)": "built, tested",
    "class A copy, sync and cutover on a real cloud": "NOT built (migration engine)",
    "class B rotation watcher on a real cloud": "NOT built (migration engine)",
    "class C history rebuild, freeze and swap on a real cloud": "NOT built (migration engine)",
    "class D detached variant": "NOT built; needs an owner decision",
    "rollback R1 to R3 on a real cloud": "NOT built (migration engine)",
}

# ---- causes ----------------------------------------------------------------------------------------------------------
# code: (title, why, source_state, target_state, steps, who)
SAFE_SRC = "Untouched. The source volume was never changed and is still in service."
NO_TGT = "Nothing was created on the target."
CAUSES = {
    # held back before anything started
    "encrypted": ("Encrypted volume",
                  "The staged method copies blocks below the encryption layer and cannot rebuild the key relationship. Not supported.",
                  SAFE_SRC, NO_TGT,
                  ["Decide with the tenant whether the data can move another way (tenant-side copy to a new encrypted volume on the new type).",
                   "If it must move through Cinder, plan a separate procedure and lab-test it with an encrypted volume first.",
                   "Record the decision against this volume; leave it where it is until then."], "Platform owner and tenant"),
    "multiattach": ("Multi-attach volume",
                    "Several instances use the volume at once, so a single swap would leave the others on the old device.",
                    SAFE_SRC, NO_TGT,
                    ["List every instance attached (see the volume row).",
                     "Agree a maintenance window with all owners, or have them detach all but one.",
                     "Plan a dedicated procedure and lab-test it; this method does not cover it."], "Platform owner and tenants"),
    "grouped": ("Volume in a group or consistency group",
                "Group membership ties the volume to group snapshots on the source back-end that the method does not rebuild.",
                SAFE_SRC, NO_TGT,
                ["Identify the group and who uses its group snapshots.",
                 "Either remove the volume from the group (only with the owner's agreement) and classify it again, or move the whole group in one planned change.",
                 "Run the pre-flight again after any change."], "Tenant and platform owner"),
    "replicated": ("Replicated volume",
                   "Replication on the source back-end would be broken by a copy and a swap.",
                   SAFE_SRC, NO_TGT,
                   ["Confirm with the owner what the replica protects and whether the target back-end can replicate.",
                    "Plan the move together with the replication setup; do not disable replication to fit the method."], "Platform owner"),
    "bad_status": ("Volume not in a movable state",
                   "A volume must be available or in-use. Anything else (error, extending, deleting, detaching, ...) means a Cinder operation is stuck or failed.",
                   SAFE_SRC, NO_TGT,
                   ["Find out why the volume is in that state (Cinder logs, the audit finding for it).",
                    "If an operation is stuck, resolve it the supported way (for example reset-state by an administrator who has verified the array side) and record who did it.",
                    "If the volume is in error and not needed, ask the owner before deleting it.",
                    "Run the pre-flight again; the volume is classified again."], "Cinder administrator"),
    "migration_in_flight": ("A Cinder migration is already running or stuck",
                            "migration_status is not none, success or error. Starting another migration would race the first.",
                            "Unknown until the running migration is understood. Do not touch it.", NO_TGT,
                            ["Check whether the migration is really running (cinder-volume logs, the destination volume).",
                             "Let it finish, or recover it with the supported procedure, before this volume enters the plan.",
                             "Run the pre-flight again."], "Cinder administrator"),
    "bad_provider_id": ("Malformed provider_id",
                        "The database record that links the volume to its array volume is missing or wrong, so the tool cannot be sure which array volume it would copy.",
                        SAFE_SRC, NO_TGT,
                        ["Compare the provider_id with the real array volume (volume id, account id, cluster id).",
                         "Repair the record only with a database backup in place and a second person watching; playbook step 1.9 describes it.",
                         "Import a fresh database export and run the pre-flight again."], "Cinder administrator with a reviewer"),
    "instance_not_running": ("Attached to an instance that is not running",
                             "An instance that is shut off, in error or rebuilding cannot take part in a swap, and its disk cannot be checked afterwards.",
                             SAFE_SRC, NO_TGT,
                             ["Ask the instance owner to start it, or confirm that it may stay off.",
                              "If it is rebuilding or has a task in progress, wait until the task clears.",
                              "A root volume of a boot-from-volume instance waits until the instance runs.",
                              "When the instance is ACTIVE or PAUSED run the pre-flight again; the volume is classified again (A or C).",
                              "If the instance must stay off, a detached move needs an admin lock and detach, with a recorded decision."], "Instance owner"),
    "check_failed": ("A pre-flight check failed for this volume",
                     "A volume is admitted only when none of its volume checks fails.",
                     SAFE_SRC, NO_TGT,
                     ["Read the failed check in the pre-flight (the evidence is quoted in this record).",
                      "Fix the cause it names, for example a missing volume type, a snapshot that is not available, or too little room on the target.",
                      "Run the pre-flight again."], "Operator"),
    "no_data_mover": ("No data mover: class C needs host and array access",
                      "A volume with long-lived snapshots can be moved only by the staged method: its snapshot history is rebuilt on the target array from a host that reaches both arrays. "
                      "Cinder cannot migrate a volume that has snapshots, and OpenMove has the OpenStack APIs only at this level of access.",
                      SAFE_SRC, NO_TGT,
                      ["Decide whether the history of snapshots must be kept. If it is not needed, ask the owner to delete the snapshots; the volume becomes class A and moves by retype.",
                       "If it is needed: provide a Linux host that reaches both arrays (an existing cinder-volume host can be used), give OpenMove SSH access to it and the array accounts, "
                       "set the access level to full, and run the pre-flight again.",
                       "Until then leave the volume where it is: nothing was done to it."], "Application owner and cloud administrator"),
    "not_admitted": ("Not admitted to the migration",
                     "The volume is not in the list the pre-flight admitted.",
                     SAFE_SRC, NO_TGT, ["Run the pre-flight again and read the held-back list."], "Operator"),
    # stopped or failed during the order
    "rotation_timeout": ("Snapshots did not rotate away",
                         "A class B volume is moved only when its snapshot count reaches zero. The agreed window passed and snapshots remain, so the volume is really class C.",
                         SAFE_SRC, NO_TGT + " The watcher only reads.",
                         ["Ask the tenant whether the remaining snapshots are still needed and whether the schedule changed.",
                          "If they are kept on purpose, run the pre-flight again: the volume becomes class C and takes the staged path.",
                          "If they can be deleted, the tenant deletes them; the watcher then continues by itself."], "Tenant"),
    "snapshot_appeared": ("A snapshot appeared before the move",
                          "The volume was class A or B, but a snapshot exists at validation, so it is no longer a no-snapshot volume.",
                          SAFE_SRC, NO_TGT,
                          ["Run the pre-flight again; the volume is classified again (B or C).",
                           "If the snapshot is temporary (a backup job), wait for it to go or move the job's schedule away from the batch."], "Operator"),
    "copy_not_verified": ("The copy did not verify",
                          "After the copy the target did not match the source (size or hash). Moving on would put a bad copy on the production path, so the volume was stopped.",
                          "Untouched. The source was only read.", "The target copy was discarded (nothing is left to clean up by hand).",
                          ["Check the data path first: network between the copy host and the arrays, errors on the target pool, multipath on the host.",
                           "Look at the copy log in the journal for the first mismatch (offset and size).",
                           "When the cause is fixed, remove the volume from the plan and add it again; it starts from the beginning.",
                           "If it fails twice with no explanation, stop and ask for a lab investigation before a third try."], "Operator and storage administrator"),
    "chain_mismatch": ("Snapshot history does not match",
                       "For a class C volume every snapshot image on the target must equal the source's, and the snapshot ids must equal Cinder's. One did not.",
                       "Untouched. The source was only read.", "The staging volume was discarded. Rollback R1 is complete.",
                       ["Open the compare result in the journal and note which snapshot differs (position in the chain, id, bytes).",
                        "Check on the source array that this snapshot was not changed or removed outside Cinder.",
                        "Check for a size change after the snapshot (the volume was extended): playbook section on extended volumes.",
                        "Rebuild the staging volume from scratch once the cause is understood; never reuse the discarded one."], "Storage administrator"),
    "never_stable": ("The source kept changing",
                     "The source changed at every recheck, so there was never a quiet moment with nothing left to copy. A cutover at that point would lose writes.",
                     "Untouched. The source was only read.", "The target copy is kept in sync but no cutover was made, then discarded.",
                     ["Find what writes to the volume (busy database, backup job, replication tool).",
                      "Agree a quiet period with the owner, or a short quiesce at the cutover time.",
                      "Add the volume to a later batch."], "Application owner"),
    "cutover_refused": ("The cutover was refused",
                        "A cutover rule was not met (rechecks, timing, a changed source, the gate or a halt). The refusal is the safety working.",
                        "Untouched. No change was made on the production path.", "The target copy is kept in sync until the rules are met, then discarded.",
                        ["Read the refusal reasons in the timeline below.",
                         "Run the rechecks again (two passes, at least five minutes apart) while the gate is approved and no halt is active.",
                         "If the gate is no longer approved, run the pre-flight again and get the two sign-offs."], "Operator"),
    "swap_refused": ("The swap was refused and the move was rolled back",
                     "Nova or Cinder refused the volume swap at the cutover (for example the instance changed state or has a task in progress). Rollback R2 ran.",
                     "Restored to the state before the cutover and checked from the instance.", "The destination record was removed. The target copy was removed.",
                     ["Check the instance (state, task state, lock) and the Nova and Cinder logs at the time.",
                      "Confirm from the instance that the disk is on the original device and healthy.",
                      "Pick a quiet time, make sure the instance is ACTIVE with no task, and add the volume to a later batch."], "Cloud administrator"),
    "snapshot_raced_freeze": ("A tenant snapshot was taken at the freeze",
                              "A snapshot was created or deleted while the source was being frozen, so the history on the target no longer matched.",
                              "Restored to the state before the cutover.", "The staging volume was discarded.",
                              ["Tell the tenant to pause snapshot jobs for this volume during the batch window.",
                               "Add the volume to a later batch."], "Tenant"),
    "no_room": ("Not enough room on the target",
                "The target pool cannot hold the volume (with its snapshots) with the headroom the plan needs.",
                SAFE_SRC, NO_TGT,
                ["Free space on the target pool or add capacity.", "Reduce the batch size.", "Run the pre-flight again so the capacity check sees the new figure."], "Storage administrator"),
    "not_selected": ("Left out of this batch by the operator",
                     "The volume was admitted by the pre-flight, but the operator did not select it for this batch. Nothing was done to it.",
                     SAFE_SRC, NO_TGT,
                     ["Select it on the Select tab when it should be part of a batch.", "Nothing else is needed: it stays exactly as it is."], "Operator"),
    "risky_workload": ("Hosts a workload that may break",
                       "The tool found a known risk of a failed or unsafe migration for what this volume hosts (for example a legacy Windows server, no guest agent, an emulated disk bus). "
                       "It is NOT moved: a volume that may break its workload is excluded until two different people clear it.",
                       SAFE_SRC, NO_TGT,
                       ["Read the reasons quoted in this record, and the advice for them in the workload-risk list.",
                        "Ask the application owner what it runs and whether it can be moved; agree a window and a way back.",
                        "Test the move on a clone in a lab with the same guest. For a Windows guest: drivers, guest agent, boot after the swap.",
                        "If it should move: record the clearance (what was tested, what the owner agreed) as operator, then as a different person as reviewer, then run the pre-flight again.",
                        "If it should not move: leave it. Nothing is done to it."], "Application owner, operator and reviewer"),
    "postcheck_failed": ("The workload did not come back healthy",
                         "After the cutover the post-check failed (for example a Windows guest that did not find its disk or did not boot). The volume was rolled back to the source (R3).",
                         "Restored to service and checked.", "The destination was removed.",
                         ["Check the guest console and the hypervisor log for the instance at that time.",
                          "Confirm from the instance that it is running on the original volume and the application works.",
                          "Find the cause (disk driver, bus type, boot configuration) on a clone before trying again.",
                          "Add the volume to a later, separate batch with the owner present."], "Application owner and cloud administrator"),
    "halted": ("Stopped by the halt switch", "A person pressed HALT. Nothing changes while the halt is on.", SAFE_SRC, "Whatever existed stays as it was; nothing new is created.",
               ["Read the halt reason in the journal.", "When it is safe, resume with a name and a reason; the volume continues from where it was and nothing is skipped."], "Operator"),
    "gate_closed": ("The gate closed", "The pre-flight gate was no longer approved (stale, or a check failed), so the run stopped.",
                    SAFE_SRC, "Whatever existed stays as it was; nothing new is created.",
                    ["Run the pre-flight again and get the two sign-offs.", "Start the run again; volumes continue from their recorded phase."], "Operator"),
    "not_started": ("Admitted but not started", "The volume was admitted but the run ended or has not reached it yet.",
                    SAFE_SRC, NO_TGT, ["Start or resume the run.", "If it stays here, check that the gate is approved."], "Operator"),
    "in_progress": ("Still in progress", "The volume is part-way through the order.", "Untouched until the cutover.", "A copy is in sync.", ["Wait for it to finish; nothing is needed from anyone."], "Nobody yet"),
    "aborted_other": ("Stopped for another reason", "The volume was aborted. The reason is quoted in this record.", "Untouched before the cutover.", "Discarded.",
                      ["Read the reason in the timeline.", "Fix the cause, then add the volume to a later batch."], "Operator"),
}

# reason text of the classification -> cause code
_REASONS = (("encrypted", "encrypted"), ("multiattach", "multiattach"), ("group", "grouped"), ("replication", "replicated"),
            ("migration_status", "migration_in_flight"), ("status ", "bad_status"), ("provider_id", "bad_provider_id"),
            ("root-wait", "instance_not_running"), ("instance ", "instance_not_running"))


def cause_title(code):
    return CAUSES.get(code, CAUSES["aborted_other"])[0]


def describe_cause(code):
    t = CAUSES.get(code) or CAUSES["aborted_other"]
    return {"code": code, "title": t[0], "why": t[1], "source": t[2], "target": t[3], "steps": list(t[4]), "who": t[5]}


def held_back_cause(vol, failed_checks, no_mover=False):
    """The cause of a volume that never started, from its class reason and its failed checks. failed_checks: [(id, title, evidence)]."""
    reason = str(vol.get("reason") or "")
    if vol.get("class") in ("D", "E"):
        for key, code in _REASONS:
            if reason.startswith(key) or (key in reason and key in ("root-wait",)):
                return code
        return "instance_not_running" if vol.get("class") == "D" else "bad_status"
    if any(c[0] == "V11" for c in failed_checks):
        return "risky_workload"
    if failed_checks:
        return "check_failed"
    if no_mover:
        return "no_data_mover"
    return "not_admitted"


def _timeline(events):
    out = []
    for e in events:
        d = e["data"]
        if e["kind"] == "phase":
            what = "enters %s" % d.get("phase")
            if d.get("regression"):
                what += " (went back to sync: %s)" % (d.get("reason") or "a recheck failed")
            elif d.get("reason"):
                what += " (%s)" % d["reason"]
            out.append({"at": d.get("at") or e["ts"], "what": what, "simulated": bool(d.get("simulated"))})
        elif e["kind"] == "refusal":
            out.append({"at": e["ts"], "what": "REFUSED %s: %s" % (d.get("attempt"), d.get("reason")), "simulated": bool(d.get("simulated"))})
    out.sort(key=lambda r: r["at"])
    return out


def unmigrated(journal, inventory, checks, servers=None, project_names=None, halted=False, gate_ok=True, selected=None):
    """One record for every volume that is not migrated. Returns {'rows': [...], 'migrated': n, 'by_cause': {...}, 'by_class': {...}}.

    A volume counts as migrated once its cutover has completed (cutover, postcheck, soak, done). A volume that was rolled back,
    aborted, held back, waiting or not yet started is listed, with the cause and the proposed steps."""
    names = project_names or {}
    servers = servers or {}
    states = L.states(journal)
    admitted = set((checks or {}).get("admitted") or [])
    failed = {}
    for c in (checks or {}).get("volume", []):
        for f in c.get("findings", []) or []:
            if f.get("status") == "fail":
                failed.setdefault(f["volume"], []).append((c["id"], c["title"], f.get("evidence") or ""))
    rows, migrated = [], 0
    for v in inventory["volumes"]:
        vid = v["id"]
        st = states.get(vid)
        ph = st["phase"] if st else None
        hist = L.history(journal, vid) if st else []
        rolled = next((e for e in reversed(hist) if e["phase"] == L.ROLLBACK), None)
        if ph in MIGRATED_PHASES and not rolled:
            migrated += 1
            continue
        events = journal.events(None, vid, 400) if st else []
        refusals = [e["data"] for e in events if e["kind"] == "refusal"]
        if rolled:
            outcome, code = "rolled back", rolled.get("cause") or "swap_refused"
            how = rolled.get("reason")
        elif ph == L.ABORTED:
            outcome, code = "stopped", st.get("cause") or "aborted_other"
            how = st.get("reason")
        elif ph:
            outcome = "in progress"
            code = "halted" if halted else ("in_progress" if gate_ok else "gate_closed")
            how = "phase %s" % ph
        elif vid in admitted and selected is not None and vid not in selected:
            outcome, code, how = "not selected", "not_selected", "admitted, but not selected for this batch"
        elif vid in admitted:
            outcome, code, how = "not started", ("halted" if halted else ("not_started" if gate_ok else "gate_closed")), "admitted, no phase recorded"
        else:
            outcome = "held back" if v.get("class") in ("D", "E") else "not admitted"
            code = held_back_cause(v, failed.get(vid, []), vid in set((checks or {}).get("no_mover") or ()))
            how = v.get("reason")
        d = describe_cause(code)
        att = [servers.get(a.get("server_id"), {}).get("name") or a.get("server_id") or "" for a in v.get("attachments", [])]
        rows.append({
            "volume": vid, "name": v.get("name") or "", "project": names.get(v.get("project_id")) or (str(v.get("project_id") or "")[:8]),
            "size": v.get("size"), "snapshots": v.get("snapshots") or 0, "class": v.get("class"), "attached_to": [a for a in att if a],
            "outcome": outcome, "phase": ph, "cause": code, "title": d["title"], "why": d["why"], "how": how or "",
            "failed_checks": [{"id": i, "title": t, "evidence": e} for i, t, e in failed.get(vid, [])],
            "risk": ((checks or {}).get("risk") or {}).get(vid),
            "refusals": [{"attempt": r.get("attempt"), "reason": r.get("reason")} for r in refusals][:12],
            "timeline": _timeline(list(reversed(events)))[-40:], "source": d["source"], "target": d["target"], "steps": d["steps"], "who": d["who"],
            "simulated": bool(st and st.get("simulated"))})
    by_cause, by_class, by_outcome = {}, {}, {}
    for r in rows:
        by_cause[r["cause"]] = by_cause.get(r["cause"], 0) + 1
        by_class[r["class"]] = by_class.get(r["class"], 0) + 1
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
    return {"rows": rows, "migrated": migrated, "total": len(inventory["volumes"]), "by_cause": by_cause, "by_class": by_class, "by_outcome": by_outcome}


# ---- one volume, step by step ----------------------------------------------------------------------------------------
# What each step does, what it touches, the evidence it records and what refuses it. docs/PROCESS.md section 4 repeats this
# table; a test keeps them in step. 'touches' says what the step does to the source and to the target.
STEP = {
    "precheck": ("Pre-check", "Confirms the pre-flight gate is approved right now and that the volume's class is one this method moves (A, B or C).",
                 "reads only", "class; the gate run id", "the gate is not approved; class D or E; an unknown class; a halt"),
    "validate": ("Validate", "Checks this volume against the target: room for it, the mapping, reach from the host, and that a class A or B volume still has no snapshot.",
                 "reads only", "snapshots now; room on the target", "a snapshot exists (class A, B); no room on the target"),
    "copy": ("Copy", "Makes a full copy on the target array. The source volume is only read.",
             "source: read. target: a new staging volume is written", "validated flag; the number of snapshot images to rebuild (class C)", "validation did not pass"),
    "sync": ("Keep in sync", "Keeps the target in step with the source until the cutover is possible. Starts only when the copy verified: same size, same hash.",
             "source: read. target: the copy is updated", "size equal; hash match; for class C every image compared and the snapshot ids matched",
             "the copy did not verify; for class C an image or a snapshot id differs"),
    "recheck": ("Recheck", "Checks everything again and then again later: nothing left to copy, the source unchanged since the last recheck. One pass is never enough.",
                "reads only", "ok; lag; blocks pending; source fingerprint; for class C reconciled (no tenant snapshot created or deleted)",
                "pending blocks, a changed source or (class C) a snapshot change sends the volume back to sync and the passes start again"),
    "cutover": ("Cutover", "The only step on the production path: the instance is switched to the new volume. Allowed only when every cutover condition holds.",
                "source: frozen and switched away from. target: becomes the volume", "the conditions that held",
                "any condition not met: not enough rechecks, too close together, too old, source changed, data waiting, gate not approved, halt"),
    "postcheck": ("Post-check", "Checks the volume from its new path: attachment, host, type, name id, and for class C the snapshot chain.",
                  "reads only", "attachment ok", "a failed post-check means rollback"),
    "soak": ("Soak", "Waits the agreed period with the original source kept untouched, so a rollback stays possible.",
             "none: the source is kept", "soak days", "nothing; a rollback is still possible"),
    "done": ("Release", "After the soak period, and with a person's sign-off, the source and its clones are released.",
             "source: released (the only deletion)", "-", "the soak period has not ended"),
}
CLASS_STEP = {
    "B": {"watch": ("Wait for rotation", "Waits for the volume's snapshots to rotate away. Nothing is copied while any snapshot exists. When none are left the class A path runs.",
                    "reads only", "snapshots left", "a wait longer than the agreed window stops the volume: it is really class C")},
    "C": {"copy": ("Copy and rebuild history", "Takes a rollback clone of the source, builds a staging volume from the oldest snapshot, applies each later snapshot in order and takes it on the target with the same id.",
                   "source: read (clones taken). target: staging volume and snapshots written", "validated flag; how many snapshot images were rebuilt", "validation did not pass"),
          "sync": ("Verify history, keep in sync", "Starts only when every snapshot image compares identical (the tail beyond a smaller image reads as zero) and the snapshot ids equal Cinder's. Then keeps the target in step.",
                   "source: read. target: kept in step", "size equal; hash match; chain match; snapshots matched = snapshots rebuilt", "any image or id differs: stopped, staging volume discarded"),
          "cutover": ("Cutover (destination record, freeze, swap)", "Creates the destination record, freezes the source, takes a rollback clone and swaps (an instance swap if attached, a final clone if detached). Rollback R2 is ready.",
                      "source: frozen. target: destination record created", "the conditions that held", "any cutover condition not met; a swap refused is rolled back")}}
NOW = {
    None: "Not started.", "precheck": "Pre-checked. About to validate against the target. Nothing has been changed.",
    "validate": "Validated. About to start the copy. Nothing has been changed on the source.",
    "copy": "Copying to the target. The source is only read.", "sync": "Copy verified. Keeping the target in step with the source. The source is only read.",
    "recheck": "In sync and being checked again. The cutover is not allowed until every condition holds.",
    "cutover": "Cutover under way: the production path is being switched. Rollback is ready.",
    "postcheck": "Cut over. Checking the volume from its new path. The source is kept.",
    "soak": "Migrated. The original source is kept untouched for the soak period; a rollback is still possible.",
    "done": "Done. The source was released after the soak period."}
STATE_AT = {   # (source, target) as they are at the end of the phase
    None: ("In service, untouched.", "Nothing created."), "precheck": ("In service, untouched.", "Nothing created."),
    "validate": ("In service, untouched.", "Nothing created."), "copy": ("In service, only read.", "A staging volume is being written."),
    "sync": ("In service, only read.", "The staging copy is in step with the source."), "recheck": ("In service, only read.", "The staging copy is in step with the source."),
    "cutover": ("Frozen; the instance is being switched away from it.", "The destination record exists and is becoming the volume."),
    "postcheck": ("Kept, no longer in use.", "In service."), "soak": ("Kept untouched for the soak period.", "In service."),
    "done": ("Released.", "In service."),
    "aborted": ("In service, untouched.", "The staging copy was discarded or never made."),
    "rollback": ("Restored to service and checked.", "The destination record and the copy were removed.")}


def steps_for(cls):
    """The steps of a class as a list of dicts: id, title, does, touches, evidence, refused."""
    if cls in ("D", "E"):
        return [{"id": "hold", "title": "Held back", "does": PLANS[cls]["steps"][0][1], "touches": "none", "evidence": "-", "refused": "-"}]
    order = ["precheck"] + (["watch"] if cls == "B" else []) + ["validate", "copy", "sync", "recheck", "cutover", "postcheck", "soak", "done"]
    over = CLASS_STEP.get(cls, {})
    out = []
    for ph in order:
        t = over.get(ph) or STEP.get(ph)
        out.append({"id": ph, "title": t[0], "does": t[1], "touches": t[2], "evidence": t[3], "refused": t[4]})
    return out


_HIDE = ("phase", "at", "id", "class", "gate_run")


def volume_view(journal, vol, note, urow, readiness=None):
    """Everything the screen shows for one volume: its path, where it is on it, what is happening now, what blocks the cutover."""
    vid = vol["id"]
    cls = vol.get("class")
    hist = L.history(journal, vid)
    phases = [h["phase"] for h in hist]
    cur = phases[-1] if phases else None
    failed = cur in (L.ABORTED, L.ROLLBACK) or L.ROLLBACK in phases
    last_ok = next((p for p in reversed(phases) if p not in (L.ABORTED, L.ROLLBACK)), None)
    steps = steps_for(cls)
    for s in steps:
        ph = s["id"]
        if ph == "hold":
            s["status"] = "held"
        elif ph == "watch":
            s["status"] = "done" if "validate" in phases else ("failed" if failed else ("current" if cur == "precheck" else "pending"))
        elif ph in phases:
            s["status"] = "failed" if failed and ph == last_ok else ("current" if ph == cur else "done")
            if cls == "B" and ph == "precheck" and s["status"] == "current":
                s["status"] = "done"                      # a class B volume then waits in the watch step
        else:
            s["status"] = "pending"
    if cur == "done" and not failed:
        for s in steps:
            s["status"] = "done"
    events = []
    for h in hist:
        ev = {k: v for k, v in h.items() if k not in _HIDE}
        events.append({"phase": h["phase"], "at": h.get("at"), "evidence": ", ".join("%s=%s" % (k, ev[k]) for k in sorted(ev)), "simulated": bool(h.get("simulated"))})
    ready = bool(note and str(note).startswith("ready for cutover"))
    if urow and urow["outcome"] in ("stopped", "rolled back"):
        now = "%s: %s" % (urow["outcome"].capitalize(), urow["how"])
    elif ready and "sign-off" in str(note):
        now = "In sync and checked. Waiting for a second person to sign G2 and G3. Every cutover condition is asked again at the cutover."
    elif ready:
        now = "In sync and checked. Waiting for you to release it for cutover. Every cutover condition is asked again at the moment of release."
    elif cur == "postcheck" and "G4" in str(note):
        now = "The post-check passed. Waiting for a second person to sign G4 before the soak starts."
    elif cls == "B" and cur == "precheck" and note:
        now = "Waiting for rotation: " + str(note)
    elif cls in ("D", "E"):
        now = "Held back, not started: " + str((urow or {}).get("how") or vol.get("reason") or "")
    elif urow and urow["outcome"] in ("not admitted", "not selected"):
        now = "Not started: " + str(urow["how"])
    else:
        now = NOW.get(cur, "") + ((" " + str(note) + ".") if note else "")
    key = L.ABORTED if cur == L.ABORTED else (L.ROLLBACK if L.ROLLBACK in phases else cur)
    src, tgt = STATE_AT.get(key, STATE_AT[None])
    return {"id": vid, "name": vol.get("name") or "", "class": cls, "class_title": PLANS.get(cls, {}).get("title", ""), "method": PLANS.get(cls, {}).get("method", ""),
            "size": vol.get("size"), "snapshots": vol.get("snapshots") or 0, "phase": cur, "now": now, "ready": ready,
            "steps": steps, "source_now": src, "target_now": tgt, "readiness": readiness or [], "events": events,
            "unmigrated": urow, "reason": vol.get("reason")}
