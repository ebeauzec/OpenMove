#!/bin/bash
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
#
# Builds the OpenMove lab inside a disposable Ubuntu 24.04 WSL distro: a real single-node OpenStack (DevStack) with
#   * Keystone, Glance, Nova (KVM), Neutron, Placement, Cinder
#   * two Cinder LVM back-ends that stand in for the two arrays:  sf-sim (the "source") and pure-sim (the "target"),
#     each with a volume type of the same name.
# It is a lab for the engine, NOT a copy of the customer's cloud: it is a recent release, not Queens, and its back-ends are LVM.
# Run as root inside the distro:   LAB_PASSWORD=... bash provision_devstack.sh
# The password is for this lab only. It never leaves the distro and the lab file on this machine (see tools/lab/lab.py).
set -euo pipefail
: "${LAB_PASSWORD:?set LAB_PASSWORD}"
BRANCH="${DEVSTACK_BRANCH:-stable/2025.1}"
export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y git sudo curl ca-certificates python3 lvm2 thin-provisioning-tools open-iscsi

id stack >/dev/null 2>&1 || useradd -s /bin/bash -d /opt/stack -m stack
chmod 755 /opt/stack
echo "stack ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/stack
chmod 440 /etc/sudoers.d/stack

# kernel pieces Cinder (LVM + LIO iSCSI) and Nova (iSCSI attach, KVM) need; WSL does not load them by itself
cat > /etc/modules-load.d/openmove-lab.conf <<'EOF'
iscsi_tcp
dm_snapshot
dm_thin_pool
target_core_mod
iscsi_target_mod
target_core_iblock
tcm_loop
openvswitch
kvm_amd
EOF
for m in $(cat /etc/modules-load.d/openmove-lab.conf); do modprobe "$m" || echo "note: $m not loaded"; done

su - stack -c "test -d /opt/stack/devstack || git clone --depth 1 -b $BRANCH https://opendev.org/openstack/devstack /opt/stack/devstack"
cat > /opt/stack/devstack/local.conf <<EOF
[[local|localrc]]
ADMIN_PASSWORD=$LAB_PASSWORD
DATABASE_PASSWORD=$LAB_PASSWORD
RABBIT_PASSWORD=$LAB_PASSWORD
SERVICE_PASSWORD=$LAB_PASSWORD
LOGFILE=/opt/stack/logs/stack.sh.log
LOG_COLOR=False
GIT_DEPTH=1

# two Cinder back-ends standing in for the source and the target array
CINDER_ENABLED_BACKENDS=lvm:sf-sim,lvm:pure-sim
VOLUME_BACKING_FILE_SIZE=60G
CINDER_LVM_TYPE=default
# the lab VM has KVM (AMD); fall back to qemu if it is not usable
LIBVIRT_TYPE=kvm

# Horizon (the OpenStack dashboard) stays on: OpenMove-Lab.bat opens it next to OpenMove
disable_service tempest
disable_service n-novnc n-spice n-cauth
EOF
chown stack:stack /opt/stack/devstack/local.conf
chmod 600 /opt/stack/devstack/local.conf

mkdir -p /opt/stack/logs && chown stack:stack /opt/stack/logs
su - stack -c "cd /opt/stack/devstack && nohup ./stack.sh > /opt/stack/logs/stack.out 2>&1 &"
echo "stack.sh started; follow it with: tail -f /opt/stack/logs/stack.out"
