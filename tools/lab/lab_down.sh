#!/bin/bash
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
#
# Stops the OpenMove lab gracefully, inside the distro, as root (lab.py down does). Order: guests, OpenStack services, web server,
# message queue and database, then the volume groups and loop devices, so nothing is cut off in the middle of a write.
# The lab's data is kept; OpenMove-Lab.bat up starts it again.
set -u
if pgrep -f '/stack.sh' >/dev/null; then
  echo "DevStack is still installing (stack.sh is running). Stopping now could break the install. Wait for it to finish."
  exit 3
fi

echo "stopping instances ..."
if command -v virsh >/dev/null; then
  for d in $(virsh list --name 2>/dev/null); do virsh shutdown "$d" >/dev/null 2>&1; done
  for _ in $(seq 1 30); do [ -z "$(virsh list --name 2>/dev/null)" ] && break; sleep 2; done
  for d in $(virsh list --name 2>/dev/null); do echo "instance $d did not stop in 60 s; forcing it"; virsh destroy "$d" >/dev/null 2>&1; done
fi

echo "stopping OpenStack services ..."
units=$(systemctl list-unit-files --no-legend 'devstack*' | awk '{print $1}')
# the compute and volume services first, then the rest
for u in $units; do case "$u" in *n-cpu*|*c-vol*) systemctl stop "$u" 2>/dev/null;; esac; done
for u in $units; do systemctl stop "$u" 2>/dev/null; done

echo "stopping the web server, queue and database ..."
for s in apache2 libvirtd ovn-controller ovn-northd ovn-ovsdb-server-sb ovn-ovsdb-server-nb openvswitch-switch rabbitmq-server etcd mysql; do
  systemctl stop "$s" 2>/dev/null
done

sync
echo "releasing the volume groups ..."
vgchange -an >/dev/null 2>&1
losetup -D 2>/dev/null
sync
echo "the lab is stopped"
