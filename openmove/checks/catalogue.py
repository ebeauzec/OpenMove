# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
"""The check catalogue (docs/MIGRATION_METHOD.md section 8): every check the method defines.

Each entry: id, group, title, and what access it needs. A check that cannot run with the access
OpenMove has is reported as 'not checked' with the access it is waiting for, never as passed.
"""

NEEDS = {
    "api": "OpenStack APIs",
    "db": "Cinder database",
    "cinder_host": "Cinder host (SSH)",
    "compute_host": "Compute hosts (SSH)",
    "src_array": "Source array",
    "tgt_array": "Target array",
    "migration_host": "Migration host",
    "records": "Operator records",
}

GROUPS = {
    "platform": "Platform (once per cloud and back-end pair)",
    "volume": "Per volume (eligibility and classification)",
    "staging": "Staging (class C)",
    "cutover": "Cutover readiness",
    "post": "Post-cutover validation (gate G4)",
}

# Which phase runs the checks that cannot run before the migration.
PHASE = {"staging": "Staging copy", "cutover": "Before cutover", "post": "After cutover"}

CHECKS = [
    ("P1", "platform", "OpenStack release detected; service versions recorded; release profile selected", "api"),
    ("P2", "platform", "Source and target back-ends enabled, their services up, pools reported", "api"),
    ("P3", "platform", "Target driver variant and array API version supported", "cinder_host,tgt_array"),
    ("P4", "platform", "Target volume type has no replication or other feature that changes completion", "api"),
    ("P5", "platform", "Volume and snapshot name templates and the source driver prefix are the defaults the method assumes", "cinder_host"),
    ("P6", "platform", "Target setting that keeps deleted volumes recoverable is on", "cinder_host"),
    ("P7", "platform", "Every compute host reaches the target array and has working multipath", "compute_host"),
    ("P8", "platform", "Data mover host(s) reach their arrays (and each other in a relay); copy throughput measured; no changes needed on production hosts", "migration_host"),
    ("P9", "platform", "Admin access to each component proven with a read-only probe", "api,db,cinder_host,compute_host,src_array,tgt_array"),
    ("P10", "platform", "Database backup taken and restore proven", "db"),
    ("P11", "platform", "Volume-type strategy decided and recorded; type access and quota set for each project", "records"),
    ("P12", "platform", "Dedicated migration account on the source array; clone QoS within the agreed ceiling", "src_array"),
    ("P13", "platform", "Availability zones of the two back-ends align", "api"),
    ("P14", "platform", "Target objects are in the array root namespace (no pods)", "tgt_array"),
    ("P15", "platform", "Source array API version supported and accepts the clone call with a new account", "src_array"),
    ("P16", "platform", "Lab validation (gate G0) complete and signed", "records"),

    ("V1", "volume", "Status is available or in-use; migration_status is none, success or error", "api"),
    ("V2", "volume", "Not encrypted, not multiattach, not in a group, replication none or disabled", "api"),
    ("V3", "volume", "provider_id is well formed (volume id, account id, cluster id)", "db"),
    ("V4", "volume", "Volume has a volume type", "api"),
    ("V5", "volume", "Snapshot count, age of oldest and newest, churn over 60 days; class assigned with a reason", "api,db"),
    ("V6", "volume", "Every snapshot is available (none creating, deleting or in error)", "api"),
    ("V7", "volume", "Each Cinder snapshot maps to one source-array snapshot; size growth and snapshot limit", "src_array"),
    ("V8", "volume", "Attachment: instance state ACTIVE or PAUSED, no task in progress, lock state, boot-from-volume", "api"),
    ("V9", "volume", "Capacity on the target pool; quota effect on the project", "api"),
    ("V10", "volume", "Drift: volumes created since the previous inventory are found", "api"),
    ("V11", "volume", "Workload risk: a volume hosting something that may break (for example a legacy Windows guest) is not moved until two people clear it", "api"),

    ("S1", "staging", "Staging volume byte size equals the source size (a source extended during the copy extends the staging volume)", "tgt_array"),
    ("S2", "staging", "Each clone's size equals its snapshot's size; sizes never shrink along the chain", "src_array"),
    ("S3", "staging", "Every image compares identical to the staging volume", "migration_host"),
    ("S4", "staging", "Target snapshot IDs equal the Cinder snapshot IDs (gate G2)", "tgt_array"),
    ("S5", "staging", "Reconciliation shows no new or deleted tenant snapshots since the last pass", "api,db"),

    ("C1", "cutover", "Destination record equals the source record on every tenant-visible field (gate G3)", "db"),
    ("C2", "cutover", "Array volume name equals the name the target driver derives for the destination record", "tgt_array"),
    ("C3", "cutover", "Rollback clone exists", "src_array"),
    ("C4", "cutover", "Instance is locked (attached case)", "api"),
    ("C5", "cutover", "Both cinder-volume services are up", "api"),
    ("C6", "cutover", "Domain definitions captured before the swap", "compute_host"),
    ("C7", "cutover", "No tenant snapshot raced the freeze", "api,db"),

    ("X1", "post", "Host, volume type, migration_status, name id and status are as expected", "api,db"),
    ("X2", "post", "Attachment intact and connected to the expected array volume only", "api,compute_host"),
    ("X3", "post", "Snapshot chain on the array matches Cinder", "tgt_array"),
    ("X4", "post", "Instance health: disk source on the new device, no block job, all multipath paths active", "compute_host"),
    ("X5", "post", "Create-from-snapshot test as admin succeeds on the target", "api"),
    ("X6", "post", "Fixed fields identical to the before capture; size, project, user and snapshot list identical to the capture at the end of the freeze", "api"),
    ("X7", "post", "Safety snapshot taken on the target volume", "tgt_array"),
    ("X8", "post", "After the soak period: source volume gone, source clones removed, quota counters reconciled", "api,src_array"),
]

BY_ID = {c[0]: {"id": c[0], "group": c[1], "title": c[2], "needs": [NEEDS[n] for n in c[3].split(",")]} for c in CHECKS}


def of_group(group):
    return [BY_ID[c[0]] for c in CHECKS if c[1] == group]
