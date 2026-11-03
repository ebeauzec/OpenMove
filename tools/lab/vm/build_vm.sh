#!/bin/bash
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
#
# Lab only (docs/LAB.md, "A real virtual machine for the storage paths"): builds a small, bare Ubuntu 24.04 virtual machine disk that has its OWN kernel,
# so that iSCSI and multipath can be tried for real (the WSL distros share one kernel and cannot). The machine is what a customer hands over for a host
# setup: sshd with root password login, a kernel, python3, and nothing of OpenMove's. Run as root in a distro that has debootstrap and e2fsprogs.
#
#   bash build_vm.sh FOLDER ROOT_PASSWORD [SIZE]
#
# Writes FOLDER/disk.img, FOLDER/vmlinuz, FOLDER/initrd.img. The image is made from a directory (mke2fs -d), so nothing is mounted.
set -eu
OUT="${1:?folder}"; PASSWORD="${2:?root password}"; SIZE="${3:-8G}"
ROOT="$OUT/rootfs"
mkdir -p "$OUT"
if [ ! -x "$ROOT/usr/bin/python3" ]; then
  rm -rf "$ROOT"
  debootstrap --arch amd64 --variant=minbase \
    --include=systemd-sysv,linux-image-generic,initramfs-tools,openssh-server,python3,sudo,iproute2,udev,kmod,ca-certificates,netbase,iputils-ping,less \
    noble "$ROOT" http://archive.ubuntu.com/ubuntu
fi
# configuration of the bare machine
echo "openmove-vm" > "$ROOT/etc/hostname"
printf '/dev/vda / ext4 defaults 0 1\n' > "$ROOT/etc/fstab"
mkdir -p "$ROOT/etc/systemd/network" "$ROOT/etc/ssh/sshd_config.d" "$ROOT/run/sshd"
printf '[Match]\nName=e*\n[Network]\nDHCP=yes\n' > "$ROOT/etc/systemd/network/10-dhcp.network"
printf 'PermitRootLogin yes\nPasswordAuthentication yes\n' > "$ROOT/etc/ssh/sshd_config.d/10-fresh.conf"
chroot "$ROOT" /bin/bash -c "echo 'root:$PASSWORD' | chpasswd; ssh-keygen -A >/dev/null 2>&1; systemctl enable systemd-networkd ssh >/dev/null 2>&1; systemctl enable serial-getty@ttyS0 >/dev/null 2>&1; true"
cp -L "$ROOT/boot/vmlinuz" "$OUT/vmlinuz"
cp -L "$ROOT/boot/initrd.img" "$OUT/initrd.img"
rm -f "$OUT/disk.img"
truncate -s "$SIZE" "$OUT/disk.img"
mke2fs -q -t ext4 -F -L openmove-vm -d "$ROOT" "$OUT/disk.img"
ls -l "$OUT/disk.img" "$OUT/vmlinuz" "$OUT/initrd.img"
echo "virtual machine disk ready in $OUT"
