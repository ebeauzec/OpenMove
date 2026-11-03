# Support matrix

Generated from `openmove/resources/backends.json` by `tools/dev/support_matrix.py`; a test keeps this file in step. Do not edit it by hand: change the data, run the tool.

OpenMove recognises a back-end by what its Cinder driver reports (vendor name and protocol). A back-end that matches no family below is **unsupported** for every pair, check P3 fails with the reason, and nothing is attempted on it. A known pair that nobody has verified is checked and planned, but reported as **not verified**, and a named person must attest it.

## Families

| Family | Recognised by (vendor name, protocol as the driver reports them) |
|---|---|
| NetApp SolidFire | vendor `solidfire inc` or `solidfire` |
| Pure Storage FlashArray | vendor `pure storage` |
| NetApp ONTAP | vendor `netapp` |
| Ceph RBD | vendor `open source`, protocol `ceph` |
| LVM (Cinder reference driver) | vendor `open source`, protocol `iscsi` or `iser` or `nvmet` or `lvm` |

## Method

Cinder retype with migration_policy on-demand, sent through the OpenStack APIs. Whether the drivers move the data themselves or Cinder copies it through a cinder-volume host is their decision and OpenMove cannot see it; the post-check reads the result.

The staged method (copy, sync, recheck, cutover with the snapshot history rebuilt) is not built. Class C volumes are held back.

## Verified pairs

| From | To | Where | What it covers |
|---|---|---|---|
| LVM (Cinder reference driver) | LVM (Cinder reference driver) | the OpenMove lab (DevStack 2025.1, two LVM back-ends) | classes A: detached, attached and boot volumes, the way back, a retype that Cinder silently undoes, failures of the API, the volume service and the halt button |

A verification in the lab proves the OpenStack side of the engine only. It says nothing about another release or about the arrays of a customer; Phase 0 (gate G0) is still needed for those.

## Every pair

| From | To | Classes A and B | Class C | Verification |
|---|---|---|---|---|
| NetApp SolidFire | NetApp SolidFire | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| NetApp SolidFire | Pure Storage FlashArray | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| NetApp SolidFire | NetApp ONTAP | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| NetApp SolidFire | Ceph RBD | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| NetApp SolidFire | LVM (Cinder reference driver) | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| Pure Storage FlashArray | NetApp SolidFire | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| Pure Storage FlashArray | Pure Storage FlashArray | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| Pure Storage FlashArray | NetApp ONTAP | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| Pure Storage FlashArray | Ceph RBD | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| Pure Storage FlashArray | LVM (Cinder reference driver) | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| NetApp ONTAP | NetApp SolidFire | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| NetApp ONTAP | Pure Storage FlashArray | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| NetApp ONTAP | NetApp ONTAP | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| NetApp ONTAP | Ceph RBD | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| NetApp ONTAP | LVM (Cinder reference driver) | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| Ceph RBD | NetApp SolidFire | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| Ceph RBD | Pure Storage FlashArray | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| Ceph RBD | NetApp ONTAP | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| Ceph RBD | Ceph RBD | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| Ceph RBD | LVM (Cinder reference driver) | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| LVM (Cinder reference driver) | NetApp SolidFire | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| LVM (Cinder reference driver) | Pure Storage FlashArray | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| LVM (Cinder reference driver) | NetApp ONTAP | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| LVM (Cinder reference driver) | Ceph RBD | retype, on-demand migration (APIs only) | staged method, not built | not verified |
| LVM (Cinder reference driver) | LVM (Cinder reference driver) | retype, on-demand migration (APIs only) | staged method, not built | lab only: the OpenMove lab (DevStack 2025.1, two LVM back-ends) |

## Adding a back-end

Add a family (and, when a pair has been verified, a `verified` entry) to `openmove/resources/backends.json`, with a test; run `python tools/dev/support_matrix.py`. The engine is not changed: it never tests for a vendor name.
