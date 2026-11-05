#!/bin/bash
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
#
# Lab only (docs/LAB.md, "The lab on a Linux host"): makes the lab's MIGRATION HOST inside the lab machine, as the WSL lab does with a second distro:
# its own root file system (its own packages, its own sshd on port 2202, nothing of DevStack in it) on the SAME kernel as DevStack. That is what a
# migration host needs: it sees the same logical volumes as the machine that runs the two Cinder back-ends. Run as root in the lab machine.
#
#   bash mover_chroot.sh create          make the root file system (debootstrap, a few minutes) and the service  openmove-mover
#   bash mover_chroot.sh key "SSH PUBLIC KEY"   add a key to root's authorized_keys of the migration host, and restart its sshd
#   bash mover_chroot.sh remove
set -eu
R=/var/lib/openmove-mover/rootfs
UNIT=/etc/systemd/system/openmove-mover.service
RUN=/usr/local/sbin/openmove-mover-run
CMD="${1:?create|key|remove}"
export DEBIAN_FRONTEND=noninteractive

case "$CMD" in
  create)
    command -v debootstrap >/dev/null 2>&1 || { apt-get update -qq >/dev/null 2>&1; apt-get install -y -qq debootstrap >/dev/null 2>&1 || { echo "could not install debootstrap"; exit 1; }; }
    if [ ! -x "$R/usr/bin/python3" ]; then
      rm -rf "$R"; mkdir -p "$R"
      debootstrap --variant=minbase --include=python3,lvm2,openssh-server,sudo,ca-certificates,util-linux,coreutils,iproute2,udev,less \
        noble "$R" "${LAB_MIRROR:-http://archive.ubuntu.com/ubuntu}"
    fi
    mkdir -p "$R/etc/ssh/sshd_config.d" "$R/run/sshd" "$R/root/.ssh" "$R/etc/lvm"
    printf 'Port 2202\nListenAddress 0.0.0.0\nPermitRootLogin prohibit-password\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nPubkeyAuthentication yes\n' \
      > "$R/etc/ssh/sshd_config.d/10-openmove.conf"
    # this kernel's device nodes are the host's (bound below), udev is the host's: lvm in here must make its nodes itself and not wait for a udev it cannot see
    printf 'activation {\n  udev_sync = 0\n  udev_rules = 0\n  verify_udev_operations = 0\n}\n' > "$R/etc/lvm/lvm.conf"
    touch "$R/root/.ssh/authorized_keys"; chmod 700 "$R/root/.ssh"; chmod 600 "$R/root/.ssh/authorized_keys"
    chroot "$R" ssh-keygen -A >/dev/null 2>&1
    cat > "$RUN" <<'EOS'
#!/bin/bash
# starts sshd of the lab's migration host in its own mount namespace and root: /dev, /proc and /sys are this machine's; everything else is its own
set -eu
R=/var/lib/openmove-mover/rootfs
cp -L /etc/resolv.conf "$R/etc/resolv.conf" 2>/dev/null || true
mkdir -p "$R/run/sshd" "$R/run/lock/lvm"
exec unshare --mount --propagation private bash -c "
  mount --rbind /dev '$R/dev' && mount -t proc proc '$R/proc' && mount --rbind /sys '$R/sys'
  exec chroot '$R' /usr/sbin/sshd -D -e"
EOS
    chmod 755 "$RUN"
    cat > "$UNIT" <<EOF
[Unit]
Description=OpenMove lab migration host (its own root file system on this kernel)
After=network-online.target

[Service]
ExecStart=$RUN
Restart=always
RestartSec=3
KillMode=control-group

[Install]
WantedBy=multi-user.target
EOF
    mkdir -p "$R/dev" "$R/proc" "$R/sys"
    systemctl daemon-reload
    systemctl enable openmove-mover >/dev/null 2>&1
    systemctl restart openmove-mover
    for _ in $(seq 1 20); do ss -ltn | grep -q ':2202 ' && break; sleep 1; done
    ss -ltn | grep -q ':2202 ' && echo "migration host ready on 2202" || { echo "the migration host's sshd is not listening on 2202: journalctl -u openmove-mover"; exit 1; }
    ;;
  key)
    KEY="${2:?public key}"
    [ -d "$R/root/.ssh" ] || { echo "the migration host does not exist yet (mover_chroot.sh create)"; exit 1; }
    grep -qF "$KEY" "$R/root/.ssh/authorized_keys" || echo "$KEY" >> "$R/root/.ssh/authorized_keys"
    chmod 600 "$R/root/.ssh/authorized_keys"
    systemctl is-active --quiet openmove-mover || systemctl restart openmove-mover
    echo "key added"
    ;;
  remove)
    systemctl disable --now openmove-mover >/dev/null 2>&1 || true
    rm -f "$UNIT" "$RUN"; systemctl daemon-reload
    for d in dev/pts dev proc sys; do umount -l "$R/$d" 2>/dev/null || true; done
    rm -rf /var/lib/openmove-mover
    echo "migration host removed"
    ;;
  *) echo "create, key or remove"; exit 2 ;;
esac
