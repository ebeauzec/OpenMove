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
# Keystone must answer before nova-compute can start: nova-compute logs in to it, and when it starts first it fails about 30 seconds after the boot
# ("Could not find versioned identity endpoints"), which is after the check below has already looked. Wait for Keystone, then make sure nova-compute is
# running and has stayed up.
for _ in $(seq 1 60); do curl -fs -o /dev/null http://localhost/identity && break; sleep 3; done
cpu=$(systemctl list-unit-files --no-legend 'devstack*' | awk '{print $1}' | grep -m1 'n-cpu')
if [ -n "$cpu" ]; then
  for attempt in 1 2 3 4 5; do
    if systemctl is-active --quiet "$cpu"; then
      sleep 20                                                  # it failed after about 28 seconds before: give it that long to prove it stays up
      systemctl is-active --quiet "$cpu" && break
    fi
    echo "restarting $cpu (attempt $attempt)"
    systemctl reset-failed "$cpu" 2>/dev/null; systemctl restart "$cpu" 2>/dev/null || true
  done
  systemctl is-active --quiet "$cpu" && echo "$cpu is running" || echo "WARNING: $cpu is not running: journalctl -u $cpu"
fi
# a unit can fail when it starts before the database, the queue or libvirt are ready (seen after a power loss: nova-compute failed 17 seconds after the boot and
# stayed failed); give the base services time, then restart whatever failed, up to four times
for round in 1 2 3 4; do
  sleep 8
  failed=$(systemctl list-units --state=failed --no-legend 'devstack*' | awk '{print $2}')
  [ -z "$failed" ] && break
  echo "restarting failed units (round $round): $(echo $failed | tr '
' ' ')"
  for unit in $failed; do systemctl reset-failed "$unit" 2>/dev/null; systemctl restart "$unit" 2>/dev/null || true; done
done
systemctl is-active mysql rabbitmq-server apache2 libvirtd iscsid 2>/dev/null | paste -sd' ' | sed 's/^/services: /'
vgs --noheadings -o vg_name,vg_size,vg_free 2>/dev/null | sed 's/^/volume group: /'
