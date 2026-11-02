#!/bin/bash
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
#
# Brings the OpenMove lab back after the WSL distro was stopped or the machine restarted. Run as root inside the distro (lab.py up does).
# DevStack's services are systemd units and start by themselves; what WSL does not restore is the kernel modules and the loop devices that
# back the two Cinder LVM volume groups.
set -u
for m in $(cat /etc/modules-load.d/openmove-lab.conf 2>/dev/null); do modprobe "$m" 2>/dev/null || echo "note: $m not loaded"; done

for f in /opt/stack/data/stack-volumes-*-backing-file; do
  [ -e "$f" ] || continue
  if ! losetup -j "$f" | grep -q .; then
    dev=$(losetup -f --show "$f") && echo "attached $f as $dev"
  fi
done
vgscan --mknodes >/dev/null 2>&1
vgchange -ay >/dev/null 2>&1

# the Cinder volume services need the groups back before they are useful; every DevStack service is a systemd unit named devstack plus an at sign
for unit in $(systemctl list-unit-files --no-legend 'devstack*' | awk '{print $1}'); do
  case "$unit" in *c-vol*) systemctl restart "$unit" 2>/dev/null || true ;; *) systemctl start "$unit" 2>/dev/null || true ;; esac
done
systemctl is-active mysql rabbitmq-server apache2 libvirtd iscsid 2>/dev/null | paste -sd' ' | sed 's/^/services: /'
vgs --noheadings -o vg_name,vg_size,vg_free 2>/dev/null | sed 's/^/volume group: /'
