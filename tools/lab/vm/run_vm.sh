#!/bin/bash
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
#
# Lab only (docs/LAB.md): starts or stops the bare virtual machine that build_vm.sh made, with KVM, in the background.
#
#   bash run_vm.sh start FOLDER [SSH_PORT]     the machine's sshd is reached at SSH_PORT (default 2204) on this machine's address
#   bash run_vm.sh stop FOLDER
#   bash run_vm.sh status FOLDER
#
# The network is QEMU's user mode: the machine can reach any address this distro can reach (the lab's iSCSI target, for example) and nothing
# outside can start a connection to it except the forwarded SSH port. Its console is written to FOLDER/console.log.
set -eu
CMD="${1:?start|stop|status}"; OUT="${2:?folder}"; PORT="${3:-2204}"
PID="$OUT/vm.pid"
running() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }
case "$CMD" in
  start)
    running && { echo "already running (pid $(cat "$PID"))"; exit 0; }
    : > "$OUT/console.log"
    qemu-system-x86_64 -enable-kvm -cpu host -smp 2 -m 2048 \
      -kernel "$OUT/vmlinuz" -initrd "$OUT/initrd.img" -append "root=/dev/vda rw console=ttyS0 net.ifnames=0 quiet" \
      -drive file="$OUT/disk.img",format=raw,if=virtio \
      -netdev user,id=n0,hostfwd=tcp:0.0.0.0:"$PORT"-:22 -device virtio-net-pci,netdev=n0 \
      -display none -serial file:"$OUT/console.log" -daemonize -pidfile "$PID"
    echo "started (pid $(cat "$PID")); ssh at port $PORT"
    ;;
  stop)
    if running; then kill "$(cat "$PID")"; sleep 2; echo "stopped"; else echo "not running"; fi
    ;;
  status)
    running && echo "running (pid $(cat "$PID"))" || echo "not running"
    ;;
  *) echo "start, stop or status"; exit 2 ;;
esac
