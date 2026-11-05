#!/bin/bash
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
#
# The OpenMove lab on a Linux host (docs/LAB.md, "The lab on a Linux host"): the host side. It makes and runs the lab's virtual machines with QEMU and KVM,
# and the private network they share. Nothing here touches OpenStack; tools/lab/lab.py calls it, and you can call it by hand.
#
#   bash labvm.sh prereqs [--install]     check (or install, on Debian and Ubuntu) what the host needs
#   bash labvm.sh net up|down|status      the bridge, the addresses and the NAT the machines use
#   bash labvm.sh build lab|fresh|vmhost  make the machine's disk (needs root and the internet; the first build of the base takes some minutes)
#   bash labvm.sh start NAME | stop NAME | status NAME | wait NAME [SECONDS] | destroy NAME | list
#
# Machines (names are the lab's):
#   lab      the OpenStack machine: DevStack, the two Cinder back-ends, the guests, and the migration host (a separate root file system on its kernel)
#   fresh    a bare host with sshd and the root password login, for trying the host setup from the Helper servers tab
#   vmhost   a second bare host (own kernel) that logs in to a test iSCSI target with two portals, for iSCSI and multipath
# Everything lives in the lab folder (default ~/.openmove-lab, or OPENMOVE_LAB_DIR): vm/NAME/disk.img, ssh/lab_ed25519 (the key the lab tools use).
# Settings (environment): LAB_PASSWORD (root password of the bare machines; required for build), LAB_MEM, LAB_CPUS, LAB_DISK (the lab machine; default
# 12G, 6, 300G sparse), LAB_SUBNET (default 192.168.221), LAB_BRIDGE (omlab0), LAB_DNS, LAB_MIRROR (the Ubuntu archive).
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
LAB="${OPENMOVE_LAB_DIR:-$HOME/.openmove-lab}"
VMDIR="$LAB/vm"
SUBNET="${LAB_SUBNET:-192.168.221}"
BRIDGE="${LAB_BRIDGE:-omlab0}"
GW="$SUBNET.1"
DNS="${LAB_DNS:-1.1.1.1 8.8.8.8}"
MIRROR="${LAB_MIRROR:-http://archive.ubuntu.com/ubuntu}"
KEY="$LAB/ssh/lab_ed25519"
SSHO="-n -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5"

die() { echo "labvm: $*" >&2; exit 1; }
need_root() { [ "$(id -u)" -eq 0 ] || exec sudo -E env OPENMOVE_LAB_DIR="$LAB" LAB_PASSWORD="${LAB_PASSWORD:-}" bash "$0" "$@"; }
own() { [ -n "${SUDO_USER:-}" ] && chown -R "$SUDO_USER": "$@" 2>/dev/null || true; }

spec() {            # sets the variables of a machine
  case "$1" in
    lab)    IP="$SUBNET.10"; TAP=omtap-lab;    MAC=52:54:00:6f:6d:10; MEM="${LAB_MEM:-12G}"; CPUS="${LAB_CPUS:-6}"; DISK="${LAB_DISK:-300G}"; HN=openmove-lab;    KIND=lab ;;
    fresh)  IP="$SUBNET.12"; TAP=omtap-fresh;  MAC=52:54:00:6f:6d:12; MEM=1G;  CPUS=1; DISK=8G; HN=openmove-fresh;  KIND=bare ;;
    vmhost) IP="$SUBNET.13"; TAP=omtap-vmhost; MAC=52:54:00:6f:6d:13; MEM=2G;  CPUS=2; DISK=8G; HN=openmove-vmhost; KIND=bare ;;
    *) die "unknown machine '$1' (lab, fresh or vmhost)" ;;
  esac
  MDIR="$VMDIR/$1"; PID="$MDIR/vm.pid"
}
running() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }

# ---------------------------------------------------------------- prerequisites
prereqs() {
  local install=0 missing="" miss_pkgs=""
  [ "${1:-}" = "--install" ] && install=1
  for c in qemu-system-x86_64 debootstrap mke2fs ip ssh ssh-keygen chroot truncate; do command -v "$c" >/dev/null 2>&1 || missing="$missing $c"; done
  command -v nft >/dev/null 2>&1 || command -v iptables >/dev/null 2>&1 || missing="$missing nft-or-iptables"
  [ -e /dev/net/tun ] || echo "warning: /dev/net/tun is missing (modprobe tun)"
  if [ -e /dev/kvm ]; then echo "ok  /dev/kvm"; else echo "warning: no /dev/kvm: the machines would be very slow. Enable virtualisation in the firmware, and 'modprobe kvm_intel' or 'kvm_amd'."; fi
  if [ -n "$missing" ]; then
    echo "missing:$missing"
    if [ "$install" -eq 1 ]; then
      need_root prereqs --install
      if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq && apt-get install -y -qq qemu-system-x86 qemu-utils debootstrap e2fsprogs openssh-client iproute2 nftables ubuntu-keyring ca-certificates curl
      else
        die "automatic install is for Debian and Ubuntu. Install qemu (qemu-system-x86_64), debootstrap, e2fsprogs, openssh, iproute2 and nftables with your package manager, and the Ubuntu archive keyring for debootstrap."
      fi
    else
      echo "run:  bash $0 prereqs --install   (Debian and Ubuntu), or install them yourself"; return 1
    fi
  else
    echo "ok  commands present"
  fi
  local free; free="$(df -Pk "$(dirname "$LAB")" | awk 'NR==2{print int($4/1048576)}')"
  echo "free disk in $(dirname "$LAB"): ${free} GiB (the lab needs about 60 GiB of it in use; the disk image is sparse)"
}

# ---------------------------------------------------------------- the network: a bridge, the gateway address, NAT to the outside
net_up() {
  need_root net up
  ip link show "$BRIDGE" >/dev/null 2>&1 || ip link add "$BRIDGE" type bridge
  ip addr show dev "$BRIDGE" | grep -q "$GW/24" || ip addr add "$GW/24" dev "$BRIDGE"
  ip link set "$BRIDGE" up
  sysctl -qw net.ipv4.ip_forward=1
  if command -v nft >/dev/null 2>&1; then
    nft delete table ip openmove_lab 2>/dev/null || true
    nft -f - <<EOF || die "could not load the nft rules of the lab network"
table ip openmove_lab {
  chain postrouting_lab { type nat hook postrouting priority 100; policy accept; ip saddr $SUBNET.0/24 oifname != "$BRIDGE" masquerade; }
  chain forward_lab { type filter hook forward priority -10; policy accept; iifname "$BRIDGE" accept; oifname "$BRIDGE" accept; }
}
EOF
  elif command -v iptables >/dev/null 2>&1; then
    iptables -t nat -C POSTROUTING -s "$SUBNET.0/24" ! -o "$BRIDGE" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s "$SUBNET.0/24" ! -o "$BRIDGE" -j MASQUERADE
  fi
  # A firewall that drops forwarded packets by default (Docker, ufw) ends the packet even after the accept of the lab's own table, because every base
  # chain of the hook is evaluated: the accept must also be in that chain. With iptables (legacy or nft) it is put in FORWARD and in DOCKER-USER;
  # with nft alone, in the chain that iptables-nft made.
  if command -v iptables >/dev/null 2>&1; then
    for ch in FORWARD DOCKER-USER; do
      iptables -nL "$ch" >/dev/null 2>&1 || continue
      iptables -C "$ch" -i "$BRIDGE" -j ACCEPT 2>/dev/null || iptables -I "$ch" -i "$BRIDGE" -j ACCEPT
      iptables -C "$ch" -o "$BRIDGE" -j ACCEPT 2>/dev/null || iptables -I "$ch" -o "$BRIDGE" -j ACCEPT
    done
  elif command -v nft >/dev/null 2>&1 && nft list chain ip filter FORWARD >/dev/null 2>&1; then
    nft -a list chain ip filter FORWARD 2>/dev/null | grep -q "openmove-lab" || {
      nft insert rule ip filter FORWARD iifname "$BRIDGE" counter accept comment "openmove-lab" 2>/dev/null
      nft insert rule ip filter FORWARD oifname "$BRIDGE" counter accept comment "openmove-lab" 2>/dev/null
    } || true
  fi
  echo "network $BRIDGE up: host $GW, lab machines $SUBNET.10 (lab), .12 (fresh), .13 (vmhost); NAT to the outside"
}
net_down() {
  need_root net down
  for m in lab fresh vmhost; do spec "$m"; ip link del "$TAP" 2>/dev/null || true; done
  ip link del "$BRIDGE" 2>/dev/null || true
  nft delete table ip openmove_lab 2>/dev/null || true
  if command -v iptables >/dev/null 2>&1; then
    iptables -t nat -D POSTROUTING -s "$SUBNET.0/24" ! -o "$BRIDGE" -j MASQUERADE 2>/dev/null || true
    iptables -D FORWARD -i "$BRIDGE" -j ACCEPT 2>/dev/null || true
    iptables -D FORWARD -o "$BRIDGE" -j ACCEPT 2>/dev/null || true
    iptables -D DOCKER-USER -i "$BRIDGE" -j ACCEPT 2>/dev/null || true
    iptables -D DOCKER-USER -o "$BRIDGE" -j ACCEPT 2>/dev/null || true
  fi
  if command -v nft >/dev/null 2>&1; then        # the rules put in a firewall chain of another tool carry the comment openmove-lab
    local h
    for h in $(nft -a list chain ip filter FORWARD 2>/dev/null | awk '/openmove-lab/ {print $NF}'); do nft delete rule ip filter FORWARD handle "$h" 2>/dev/null || true; done
  fi
  echo "network $BRIDGE removed"
}
net_status() { ip -br addr show "$BRIDGE" 2>/dev/null || { echo "network $BRIDGE is not up"; return 1; }; }

# ---------------------------------------------------------------- the machines' disks
mounts_off() { for d in dev/pts dev proc sys; do umount "$1/$d" 2>/dev/null || true; done; }
chroot_run() { local r="$1" rc=0; shift; mount --bind /dev "$r/dev"; mount -t proc proc "$r/proc"; mount -t sysfs sys "$r/sys"
               chroot "$r" env DEBIAN_FRONTEND=noninteractive LC_ALL=C /bin/bash -c "$*" || rc=$?; mounts_off "$r"; return $rc; }

base_rootfs() {      # one Ubuntu 24.04 base, made once and copied for every machine
  local base="$VMDIR/base/rootfs"
  if [ ! -x "$base/usr/bin/python3" ]; then
    echo "making the Ubuntu 24.04 base (a few minutes) ..."
    rm -rf "$base"; mkdir -p "$base"
    debootstrap --arch amd64 --variant=minbase \
      --include=systemd-sysv,linux-image-generic,initramfs-tools,openssh-server,python3,sudo,iproute2,udev,kmod,ca-certificates,netbase,iputils-ping,less,curl,gnupg,lvm2 \
      noble "$base" "$MIRROR"
    rm -f "$base/etc/resolv.conf"; cp -L /etc/resolv.conf "$base/etc/resolv.conf" 2>/dev/null || echo "nameserver 1.1.1.1" > "$base/etc/resolv.conf"
    printf 'deb %s noble main universe
' "$MIRROR" > "$base/etc/apt/sources.list"
    chroot_run "$base" 'apt-get update -qq >/dev/null 2>&1; kver=$(ls /lib/modules | head -1); apt-get install -y -qq linux-modules-extra-$kver >/dev/null 2>&1 || echo "note: linux-modules-extra-$kver not installed"
      apt-mark hold linux-image-generic linux-image-$kver linux-modules-extra-$kver >/dev/null 2>&1 || true; apt-get clean' || true
    # from here on the machine takes the updates of the archive, except for the kernel that it boots (it is held: the kernel file is read from outside the disk)
    printf 'deb %s noble main universe
deb %s noble-updates main universe
deb %s noble-security main universe
' "$MIRROR" "$MIRROR" "$MIRROR" > "$base/etc/apt/sources.list"
  fi
}

build() {
  need_root build "$@"
  local name="${1:?lab|fresh|vmhost}"; spec "$name"
  [ -n "${LAB_PASSWORD:-}" ] || die "set LAB_PASSWORD (the root password of the machine's console and, for bare machines, of its ssh)"
  mkdir -p "$VMDIR" "$MDIR" "$LAB/ssh"
  [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N '' -C openmove-lab -f "$KEY"
  base_rootfs
  local r="$MDIR/rootfs"
  echo "making the disk of $name ..."
  rm -rf "$r"; cp -a "$VMDIR/base/rootfs" "$r"
  echo "$HN" > "$r/etc/hostname"
  printf '127.0.0.1 localhost\n%s %s\n' "$IP" "$HN" > "$r/etc/hosts"
  printf '/dev/vda / ext4 defaults,noatime 0 1\n' > "$r/etc/fstab"
  mkdir -p "$r/etc/systemd/network" "$r/etc/ssh/sshd_config.d" "$r/run/sshd" "$r/root/.ssh"
  printf '[Match]\nName=e*\n[Network]\nAddress=%s/24\nGateway=%s\n' "$IP" "$GW" > "$r/etc/systemd/network/10-lab.network"
  for d in $DNS; do printf 'DNS=%s\n' "$d" >> "$r/etc/systemd/network/10-lab.network"; done
  rm -f "$r/etc/resolv.conf"; for d in $DNS; do echo "nameserver $d"; done > "$r/etc/resolv.conf"
  cp "$KEY.pub" "$r/root/.ssh/authorized_keys"; chmod 700 "$r/root/.ssh"; chmod 600 "$r/root/.ssh/authorized_keys"
  if [ "$KIND" = bare ]; then
    printf 'PermitRootLogin yes\nPasswordAuthentication yes\n' > "$r/etc/ssh/sshd_config.d/10-lab.conf"       # a host as a customer hands it over
  else
    printf 'Port 22\nPort 2201\nPermitRootLogin prohibit-password\nPasswordAuthentication no\n' > "$r/etc/ssh/sshd_config.d/10-lab.conf"
    printf 'APT::Install-Recommends "false";\n' > "$r/etc/apt/apt.conf.d/99-lab"
    printf 'stack soft nofile 65536\nstack hard nofile 65536\n' > "$r/etc/security/limits.d/stack.conf" 2>/dev/null || true
    chroot_run "$r" 'apt-get install -y -qq git open-iscsi thin-provisioning-tools apparmor >/dev/null 2>&1 || echo "note: some packages were not installed; stack.sh installs what it needs"; apt-get clean' || true
  fi
  chroot_run "$r" "echo 'root:$LAB_PASSWORD' | chpasswd; ssh-keygen -A >/dev/null 2>&1; systemctl enable systemd-networkd ssh serial-getty@ttyS0 >/dev/null 2>&1; true"
  rm -f "$r/etc/ssh/ssh_host_"* 2>/dev/null; chroot_run "$r" 'ssh-keygen -A >/dev/null 2>&1'      # a host key of its own, not the base's
  cp -L "$r/boot/vmlinuz" "$MDIR/vmlinuz"; cp -L "$r/boot/initrd.img" "$MDIR/initrd.img"
  rm -f "$MDIR/disk.img"; truncate -s "$DISK" "$MDIR/disk.img"
  mke2fs -q -t ext4 -F -i 32768 -E lazy_itable_init=1 -L "$HN" -d "$r" "$MDIR/disk.img"
  rm -rf "$r"
  own "$LAB"
  ls -lhs "$MDIR/disk.img" | awk '{print "disk image: " $6 " apparent, " $1 " used"}'
  echo "machine $name built: $IP, user root (key $KEY)"
}

# ---------------------------------------------------------------- running a machine
start() {
  need_root start "$@"
  local name="${1:?machine}"; spec "$name"
  [ -f "$MDIR/disk.img" ] || die "the machine $name is not built (bash $0 build $name)"
  running && { echo "$name is running (pid $(cat "$PID"))"; return 0; }
  net_up >/dev/null
  ip link show "$TAP" >/dev/null 2>&1 || { ip tuntap add dev "$TAP" mode tap; }
  ip link set "$TAP" master "$BRIDGE" up
  local accel="-enable-kvm -cpu host"; [ -e /dev/kvm ] || accel="-cpu max"
  : > "$MDIR/console.log"
  # shellcheck disable=SC2086
  qemu-system-x86_64 $accel -smp "$CPUS" -m "$MEM" \
    -kernel "$MDIR/vmlinuz" -initrd "$MDIR/initrd.img" -append "root=/dev/vda rw console=ttyS0 net.ifnames=0 quiet" \
    -drive file="$MDIR/disk.img",format=raw,if=virtio,cache=writeback \
    -netdev tap,id=n0,ifname="$TAP",script=no,downscript=no -device virtio-net-pci,netdev=n0,mac="$MAC" \
    -device virtio-rng-pci -display none -serial file:"$MDIR/console.log" -daemonize -pidfile "$PID"
  own "$LAB"
  echo "$name started (pid $(cat "$PID")): $IP"
}
wait_ssh() {
  local name="${1:?machine}" secs="${2:-300}" i; spec "$name"
  for i in $(seq 1 "$secs"); do
    # shellcheck disable=SC2086
    ssh $SSHO -i "$KEY" "root@$IP" true 2>/dev/null && { echo "$name answers at $IP"; return 0; }
    sleep 1
  done
  die "$name did not answer at $IP in $secs seconds (console: $MDIR/console.log)"
}
stop() {
  need_root stop "$@"
  local name="${1:?machine}" i; spec "$name"
  running || { echo "$name is not running"; return 0; }
  # shellcheck disable=SC2086
  ssh $SSHO -i "$KEY" "root@$IP" 'systemctl poweroff' 2>/dev/null || true
  for i in $(seq 1 90); do running || break; sleep 2; done
  if running; then echo "$name did not power off in 180 s; stopping it"; kill "$(cat "$PID")"; sleep 3; fi
  ip link del "$TAP" 2>/dev/null || true
  echo "$name stopped"
}
status() { local name="${1:?machine}"; spec "$name"; if running; then echo "$name running (pid $(cat "$PID")), $IP"; else echo "$name not running"; return 1; fi; }
destroy() { need_root destroy "$@"; local name="${1:?machine}"; spec "$name"; running && stop "$name"; rm -rf "$MDIR"; echo "$name removed"; }
list() { for m in lab fresh vmhost; do spec "$m"; if [ -f "$MDIR/disk.img" ]; then if running; then echo "$m: running, $IP"; else echo "$m: built, stopped"; fi; else echo "$m: not built"; fi; done; }

cmd="${1:-}"; [ $# -gt 0 ] && shift
case "$cmd" in
  prereqs) prereqs "$@" ;;
  net) case "${1:-}" in up) net_up ;; down) net_down ;; status) net_status ;; *) die "net up|down|status" ;; esac ;;
  build) build "$@" ;;
  start) start "$@" ;;
  stop) stop "$@" ;;
  status) status "$@" ;;
  wait) wait_ssh "$@" ;;
  destroy) destroy "$@" ;;
  list) list ;;
  *) sed -n 5,16p "$0"; exit 2 ;;
esac
