# Array calls the migration host must make

This page answers one question: which calls to the arrays does the staged method (class C) need, and what does each family of back-end offer for them? It is the specification for the SolidFire, FlashArray and ONTAP adapters (`openmove/arrays/`). It is written from the sources named in section 7, not from memory. Each fact carries a mark:

* **[driver]** read in the Cinder driver of the Queens release (tag `queens-eol`), the release of the owner's cloud.
* **[playbook]** read in the playbook's own scripts (v1.4, `lib/element.sh` and `lib/pure.sh`).
* **[docs]** read in the vendor's documentation.
* **[confirm]** not confirmed by a source. A person checks it in Phase 0 (T23 and the array tests) before it is trusted.

Nothing here has run against a real SolidFire, FlashArray or ONTAP system. See `COVERAGE.md`.

## 1. Why not "all the calls"

The Element API has several hundred methods, the Purity CLI and REST interface have as many, and ONTAP has more. The staged method needs about twenty operations per family. OpenMove therefore does **not** offer every call. It offers a closed list per family (section 3 to 5). A call that is not on the list is refused by the data mover on the migration host, whatever the controller sends. The reasons:

* A call that is never made cannot damage a tenant volume. The method reads the source array and writes only to objects it made itself.
* The list can be read, reviewed and signed by the customer's storage team. "Everything the API can do" cannot.
* Each call on the list can be tested. Each call off the list would be a claim without a test.

If the method later needs another call, it is added to the list in the same commit as its test and its row here.

## 2. What the method does on the arrays

The same eight needs apply to every family. Each need is one or more calls.

| # | Need | Source array (read, plus clones made by OpenMove) | Target array (OpenMove makes everything it touches) |
|---|---|---|---|
| 1 | Find the volume and its size | yes | yes (the staging volume) |
| 2 | List the snapshots of a volume | yes | yes (to check the chain, check X3) |
| 3 | Present a snapshot image to the migration host as a device | yes (clone a snapshot, give the host access, log in) | no |
| 4 | Make the staging volume with the exact source size | no | yes |
| 5 | Grow the staging volume (a tenant extended the source, playbook v1.4) | no | yes |
| 6 | Connect the staging volume to the migration host only | no | yes |
| 7 | Snapshot the staging volume under the name the **target** Cinder driver expects for each Cinder snapshot | no | yes |
| 8 | Rename the staging volume to the name the target driver derives for the destination record, and remove what OpenMove made | clones | staging volume, safety copy |

After the cutover Cinder itself deletes the source volume through its own driver. OpenMove does not delete a tenant volume on the source array (`SAFETY.md`).

## 3. SolidFire (Element API, JSON-RPC over HTTPS to the management address)

**Names and ids [driver].**

* A Cinder volume is named `UUID-<cinder volume id>` (the prefix is the driver setting `sf_volume_prefix`) and carries the attribute `uuid` with the Cinder id. The tenant account is named from the project id. The volume's `provider_id` in Cinder holds the Element volume id, the account id and the cluster id.
* A Cinder snapshot is an Element snapshot named `UUID-<cinder snapshot id>`.
* When a volume is the target of a Cinder migration, the driver names it after the **source** id and keeps the new id in the attribute `migration_uuid`. OpenMove does not rely on this path.
* The driver finds a volume by the name or by the attribute `uuid`, and raises an error if two volumes match. The adapter must do the same and refuse on two matches.

**The list OpenMove would allow.**

| Call | Why | Source |
|---|---|---|
| `GetClusterInfo`, `GetClusterVersionInfo`, `GetClusterCapacity` | Reachability, Element version (host check), free capacity | [driver] uses them |
| `ListAccounts` or `GetAccountByName` | Find the migration account that owns the clones | [driver], [playbook] (`SF_MIG_ACCOUNT_ID`) |
| `ListVolumes` (by `volumeIDs`, `accounts`, `volumeName`) | Needs 1 and 3: size (`totalSize` in bytes), `iqn`, `scsiNAADeviceID`, status, attributes | [driver], [playbook], [docs] |
| `ListSnapshots` (by `volumeID`) | Need 2: snapshot id, name, size, creation time | [driver], [playbook] |
| `CloneVolume` (`volumeID`, optional `snapshotID`, `name` of at most 64 characters, `newAccountID`, `access` `readOnly`, `attributes`) | Need 3: a read-only copy of a snapshot, or of the volume's current state when `snapshotID` is left out (the rollback clone). The call is asynchronous and returns an `asyncHandle`. A clone does **not** inherit membership of a volume access group [docs] | [playbook], [docs] |
| `GetAsyncResult` (`asyncHandle`) | Wait for the clone | [playbook], [docs] |
| `ListSyncJobs` | Progress of a clone (shown to the operator; optional) | [docs] |
| `ListVolumeAccessGroups` | Check that the migration host's initiator is in a group | [driver] |
| `AddVolumesToVolumeAccessGroup`, `RemoveVolumesFromVolumeAccessGroup` | Give the host access to one clone at a time, and take it away | [driver], [playbook] |
| `DeleteVolume` then `PurgeDeletedVolume` (only on volumes OpenMove cloned) | Remove its clones. The purge also removes their snapshots | [playbook] |
| `ListDeletedVolumes` | Show clones that were deleted and not yet purged | [driver] |

**Not on the list.** `CreateVolume`, `CreateSnapshot`, `DeleteSnapshot`, `RollbackToSnapshot`, `ModifyVolume` (size, QoS, access) on a tenant volume, every account, cluster, node, replication and pairing method. The playbook deliberately creates no Element snapshot on a tenant volume (the limit is 32 per volume). A clone inherits the QoS of its source [docs]; the playbook's `sf_set_qos` is optional and stays off the list until a test needs it.

**On the migration host.** Log in to the storage address (SVIP) of the cluster with `iscsiadm`. The device is `/dev/disk/by-path/ip-<SVIP>:3260-iscsi-<iqn>-lun-0` [playbook]. The clone is read-only. One clone is connected at a time.

**Version [driver, playbook].** The driver calls methods at API versions 1.0 to 8.0. The playbook sends `11.0`. The adapter sends the lowest version that has the method and reads `GetClusterVersionInfo` first. **[confirm]** the version of the customer's cluster.

## 4. Pure Storage FlashArray

**Names [driver].**

* A Cinder volume is the Purity volume `<cinder volume name>-cinder`, that is `volume-<id>-cinder`, in the root namespace. The Queens driver has no pod handling, so **a pod is never used**.
* A Cinder snapshot is the Purity snapshot `<purity volume name>.<cinder snapshot name>`, that is `volume-<id>-cinder.snapshot-<snapshot id>`. The suffix is `snapshot-<snapshot id>`. A rename of the volume is expected to rename its snapshots with it **[confirm]**.
* The driver makes hosts named `<name>-<32 hex>-cinder` and deletes only hosts that match this pattern. OpenMove uses one host object for the migration host, made by a person or by the setup, and **never** deletes a host.
* Manage-existing renames a volume to the `-cinder` name and refuses a volume that is connected to a host. The staging volume is not connected when it is renamed.

**Interface.**

* The Queens driver uses REST 1.2 to 1.5 (Purity 4.0 or later) [driver]. Newer Purity releases also offer REST 2.x [docs]. The playbook uses the **Purity CLI over SSH with a key** from the migration host (`pureuser`) [playbook]. The adapter uses the CLI, because the playbook's author wrote and reviewed those forms. **[confirm]** the exact forms on the customer's Purity release in Phase 0.

**The list OpenMove would allow (CLI forms from the playbook).**

| Need | Call | Source |
|---|---|---|
| 1, 2 | `purevol list --notitle --csv <volume>` (columns Name, Size, Source, Created, Serial) | [playbook]; column order **[confirm]** |
| 2 | `purevol list --snap --notitle --csv <volume>` | [playbook] |
| 4 | `purevol create --size <N>G <volume>` | [playbook] |
| 5 | `purevol setattr --size <N>G <volume>` | [playbook] (named by the author, checked in T23); **[confirm]** |
| 6 | `purehost connect --vol <volume> <migration host>`, `purehost disconnect --vol <volume> <host>`, `purevol list --connect` | [playbook] |
| 7 | `purevol snap --suffix snapshot-<id> <volume>` | [playbook] |
| 8 | `purevol rename <old> <new>` | **[confirm]** the form |
| 8 | `purevol destroy <volume>` and `purevol destroy <volume>.<suffix>` (Purity keeps a destroyed object for 24 hours) | [playbook], [docs] |
| Checks | `purearray list` (version, name), `purearray list --space` (free), `pureport list` | **[confirm]** |

**Not on the list.** `purevol eradicate` (the 24 hour window is the safety net; an eradicate is a person's step), `purevol copy`, pods, protection groups, host and host-group management, replication, and every call that touches an object OpenMove did not make.

**On the migration host.** Multipath names the device `3624a9370<serial>` (lower case). That name needs `user_friendly_names no` in `/etc/multipath.conf` [playbook]. `configure_storage.sh` writes its own drop-in file and leaves an existing `/etc/multipath.conf` alone, so a person checks that setting once (`DEPLOYMENT.md`, section 5.1). Fibre Channel needs `rescan-scsi-bus.sh` in place of the iSCSI rescan [playbook].

## 5. NetApp ONTAP

ONTAP is not in the owner's playbook. This section records what the Queens driver does, because it decides whether the staged method can work at all.

**Naming and snapshots [driver].**

* The block driver (iSCSI and FC) puts a Cinder volume in a LUN named `volume-<id>` at `/vol/<flexvol>/<lun name>`. The FlexVol is the pool.
* A **Cinder snapshot is a LUN clone** in the same FlexVol, named with the snapshot's name (`snapshot-<id>`). It is **not** an ONTAP volume Snapshot copy. Deleting the Cinder snapshot destroys that LUN.
* Create from snapshot clones the LUN again and grows it when the sizes differ. Extend uses a direct resize, or a sub-clone resize when the LUN is detached and the direct resize is not allowed.
* The driver maps a LUN to an igroup it names `openstack-<uuid>` [driver].

**What this means for the method.** On ONTAP, "the snapshot history" is a set of LUN clones inside one FlexVol. As a **source**, the adapter lists the LUNs that are clones of the volume's LUN and presents each one directly (each clone is already a LUN; no extra clone is needed). As a **target**, the adapter would make the staging LUN, and after each image copy would **clone the staging LUN** under the snapshot's name (not take a volume Snapshot copy). The clone must be in the same FlexVol as its source. This is a different mechanism from SolidFire and Pure. It can work in principle. It has not been designed in detail, and it needs an ONTAP system to prove.

**REST calls that would be on the list [docs].** `POST /api/storage/luns` with `clone.source` (a LUN clone; same FlexVol; no QoS inherited), `GET /api/storage/luns`, `PATCH /api/storage/luns/{uuid}` (increase only), `POST /api/protocols/san/lun-maps` with `GET /api/protocols/san/igroups` (give the host access), `POST /api/storage/volumes/{uuid}/snapshots` (only if a volume copy is ever needed; **not** how Cinder makes snapshots). Every POST is asynchronous, as with the other families. **[confirm]** everything else, including the ONTAP release (older releases use ZAPI, and the Queens driver is ZAPI-based [driver]).

The NFS driver (file clones) is a different driver again and is out of scope here.

## 6. What the migration host had, and what was missing

Before this page the mover had only logical-volume verbs and copy, hash and compare jobs. Those jobs work on any block device path, including `/dev/mapper/...` and `/dev/disk/by-path/...`, so they need no change. What was missing, and what section 7 now records as built:

* iSCSI login and logout of a **named target**, a rescan, and a wait for a device path with a size check.
* A multipath lookup by WWID. This stays **open**: the adapter will compute the path (`/dev/mapper/3624a9370<serial>` for a FlashArray) and wait for it with `dev.wait`.
* The Element API and the Purity CLI, each through a closed list.
* The array login on the host: a root-only file, never logged.
* A host check that each array answers (H10).
* The adapters themselves.

## 7. What is built

| Part | State |
|---|---|
| The closed lists (sections 3 and 4) | **built** in the data mover: `sf.call`, `pure.cli`; tested for every refusal |
| The rule that only clones and volumes OpenMove made are changed | **built** (SolidFire: checked against the cluster at the moment of the call; FlashArray: a ledger file) |
| The array login on the host (`array.set`, `array.list`, `array.forget`), `POST /api/hosts/array` | **built**; the password is never returned or traced |
| iSCSI login and logout of a named target, wait for a device | **built** (`iscsi.login`, `iscsi.logout`, `dev.wait`) |
| Host check H10 (each stored array answers) | **built** |
| Fields in the interface (a form on the Hosts tab) | **not built**; use `POST /api/hosts/array` |
| The adapters `solidfire` (source) and `pure` (target) that turn the method's needs into these calls | **not built**: they need the arrays to prove the device paths, the WWID rules and the Purity forms marked [confirm] |
| ONTAP | **not built**; the Queens driver's use of LUN clones as snapshots needs a design first (section 5) |

What the tests prove: the lists, the refusals, the bookkeeping, and that no secret reaches a log. What they cannot prove: how a real cluster or array answers. The Element side is tested against a small fake server written from the vendor's documentation, and the Purity side against a recording function.

## 8. Sources

* Cinder, Queens release (tag `queens-eol`): `cinder/volume/drivers/pure.py`, `solidfire.py` and `netapp/dataontap/block_base.py`.
* The owner's playbook v1.4, `lib/element.sh` and `lib/pure.sh` (local copy; not in the repository).
* NetApp Element software API reference (`CloneVolume`, `ModifyVolume`, `ListVolumes`, access control) at docs.netapp.com.
* NetApp ONTAP REST API reference (storage LUNs, LUN maps, volume snapshots) at docs.netapp.com.
* Pure Storage FlashArray REST API reference (2.0) and the Python REST client documentation at code.purestorage.com and readthedocs.

## 9. Open decisions for the owner

1. **Where the array credentials live.** The playbook runs on the migration host, so the host needs a login for the Element cluster and an SSH key for the FlashArray. Proposal: a root-only file on the host, written from the interface like the CHAP secret and never shown again.
2. **Pure interface.** The CLI over SSH (what the playbook uses) or REST. Proposal: the CLI, as the playbook does, and REST later.
3. **ONTAP.** Whether it is a source or target in any real migration. If not, it stays "recognised, not built".
