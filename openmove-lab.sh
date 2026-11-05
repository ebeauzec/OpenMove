#!/bin/sh
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
# OpenMove against the OpenStack LAB on this Linux host (a real Keystone, Nova and Cinder in a KVM virtual machine; docs/LAB.md, "The lab on a Linux host").
# This is the Linux counterpart of OpenMove-Lab.bat and OpenMove-Lab-Stop.bat. The engine really moves volumes, inside the lab only.
#
#   ./openmove-lab.sh            start the lab if needed, then OpenMove in lab mode (under the supervisor: docs/RECOVERY.md)
#   ./openmove-lab.sh deploy     build the whole lab from nothing (add --install-prereqs to let it install the host packages)
#   ./openmove-lab.sh up         start the lab cloud
#   ./openmove-lab.sh down       stop the lab gracefully (its data is kept)
#   ./openmove-lab.sh seed | more | clean | list     the test volumes and servers
#   ./openmove-lab.sh status     is the lab cloud answering?
#   ./openmove-lab.sh shell [lab|mover|fresh|vmhost]  a root shell in a lab machine
#   ./openmove-lab.sh reset      start OpenMove in lab mode with an empty journal and evidence
#   ./openmove-lab.sh destroy --yes   remove every lab machine and its disk
#   ./openmove-lab.sh --port 9000 --no-browser    any option that starts with a dash goes on to OpenMove
cd "$(dirname "$0")" || exit 1
PY=
for py in python3 python; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)' 2>/dev/null; then PY=$py; break; fi
done
[ -n "$PY" ] || { echo "Python 3 was not found. Install it first." >&2; exit 1; }
export OPENMOVE_LAB_BACKEND=linux
case "${1:-}" in
    "" | -*)
        if ! "$PY" tools/lab/lab.py status >/dev/null 2>&1; then
            echo "The lab cloud is not answering. Starting it ..."
            "$PY" tools/lab/lab.py up
            if ! "$PY" tools/lab/lab.py status >/dev/null 2>&1; then
                echo "The lab cloud did not start. The message above says why." >&2
                exit 1
            fi
            echo "(the lab cloud answers; if a guest did not start the message above says which)"
        fi
        exec "$PY" -m openmove --lab --supervise "$@"
        ;;
    up | down | seed | more | clean | list | status | deploy | shell | destroy | prereqs | net | fresh-host | vm-up | vm-down)
        exec "$PY" tools/lab/lab.py "$@"
        ;;
    reset)
        exec "$PY" -m openmove --lab --lab-reset
        ;;
    *)
        echo "Unknown command \"$1\". See the top of this file." >&2
        exit 2
        ;;
esac
