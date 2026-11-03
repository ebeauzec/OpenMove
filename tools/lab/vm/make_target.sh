#!/bin/bash
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
#
# Lab only (docs/LAB.md, "A real virtual machine for the storage paths"): a small iSCSI target with TWO portals, so that a host sees one LUN over two paths
# and multipath has something to do. It stands in for an array. It uses the Linux target (LIO, `targetcli`) of the lab distro, separate from the targets
# that Cinder makes for the lab's own volumes: its own name, its own backing file, its own ports. Run as root in the lab distro.
#
#   bash make_target.sh create ADDRESS INITIATOR_IQN CHAP_USER CHAP_SECRET [SIZE]   ports ADDRESS:3261 and ADDRESS:3262, only INITIATOR_IQN may log in
#   bash make_target.sh remove
#   bash make_target.sh show
set -eu
CMD="${1:?create|remove|show}"
IQN="iqn.2026-11.test.openmove:array1"
FILE=/var/lib/openmove-target/omtest.img
case "$CMD" in
  create)
    ADDR="${2:?address}"; INIT="${3:?initiator iqn}"; CU="${4:?chap user}"; CS="${5:?chap secret}"; SIZE="${6:-256M}"
    mkdir -p "$(dirname "$FILE")"
    targetcli "/backstores/fileio create name=omtest file_or_dev=$FILE size=$SIZE" >/dev/null
    targetcli "/iscsi create $IQN" >/dev/null
    targetcli "/iscsi/$IQN/tpg1/portals delete 0.0.0.0 3260" >/dev/null 2>&1 || true
    targetcli "/iscsi/$IQN/tpg1/portals create $ADDR 3261" >/dev/null
    targetcli "/iscsi/$IQN/tpg1/portals create $ADDR 3262" >/dev/null
    targetcli "/iscsi/$IQN/tpg1 set attribute authentication=1 generate_node_acls=0 demo_mode_write_protect=0 cache_dynamic_acls=0" >/dev/null
    targetcli "/iscsi/$IQN/tpg1/luns create /backstores/fileio/omtest" >/dev/null
    targetcli "/iscsi/$IQN/tpg1/acls create $INIT" >/dev/null
    targetcli "/iscsi/$IQN/tpg1/acls/$INIT set auth userid=$CU password=$CS" >/dev/null
    echo "target $IQN: portals $ADDR:3261 and $ADDR:3262, initiator $INIT, CHAP user $CU"
    ;;
  remove)
    targetcli "/iscsi delete $IQN" >/dev/null 2>&1 || true
    targetcli "/backstores/fileio delete omtest" >/dev/null 2>&1 || true
    rm -f "$FILE"
    echo "target removed"
    ;;
  show)
    targetcli "/iscsi/$IQN ls"
    ;;
  *) echo "create, remove or show"; exit 2 ;;
esac
