#!/bin/sh
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
# Starts OpenMove on macOS and Linux:  ./openmove.sh            (the real tool)
#                                      ./openmove.sh --demo     (built-in cloud, made-up data)
# Other options are passed on, for example:  ./openmove.sh --port 9000 --no-browser
# It uses the OpenMove program next to this file if there is one, otherwise Python 3 and the source.
cd "$(dirname "$0")" || exit 1
if [ -x "./OpenMove" ]; then
    exec ./OpenMove "$@"
fi
for py in python3 python; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)' 2>/dev/null; then
        exec "$py" -m openmove "$@"
    fi
done
echo "Python 3 was not found. Install it, or use the OpenMove program package." >&2
exit 1
