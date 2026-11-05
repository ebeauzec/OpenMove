#!/bin/bash
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
#
# Prepares a Linux host to be an OpenMove migration host and/or a Cinder host (docs/DEPLOYMENT.md). Run it as root on that host.
# It is safe to run again: every step looks first and changes only what is missing. It never touches storage, volumes or OpenStack.
#
#   sudo bash prepare_host.sh --role mover|cinder|both --key "ssh-ed25519 AAAA... openmove" [--user openmove] [--cinder-user cinder] [--from ADDRESS] [--python /usr/bin/python3]
#   --from ADDRESS   only that address (the machine that runs OpenMove) may log in with the key (written as from="ADDRESS" in authorized_keys)
#   sudo bash prepare_host.sh --role mover --check        only report what is missing; change nothing
#
# What it does:
#   1. installs what the role needs from the system's own package repository (python3; lvm2 and util-linux for a mover; nothing for a Cinder host)
#   2. creates the service user (default "openmove"), with no password and no login by password
#   3. puts OpenMove's public key in that user's authorized_keys (the key is shown on the Hosts tab and by  python -m openmove --host-key)
#   4. gives that user exactly the sudo rights the role needs, in /etc/sudoers.d/openmove (never more; see the rights below)
#   5. makes sure sshd runs and allows key login
#   6. prints a report that lines up with the host checks H1 to H9 and C1, C2 on the Hosts tab
#
# Rights granted (sudoers, no password):
#   mover   run the data mover as root:  env OPENMOVE_MOVER_HOME=* <python> */.openmove-mover/mover-*.py *
#           The data mover itself writes only to volumes that carry its tag and to files in its own folder, but OpenMove places it in the service user's
#           own folder, so treat this account as root on this host: key only, dedicated host, --from the OpenMove machine (docs/DEPLOYMENT.md, security).
#           run /usr/local/sbin/openmove-hostctl as root: it installs OpenMove's storage scripts (checked by hash) and runs them, nothing else
#   cinder  run the Cinder helper as the Cinder service user:  (cinder) every command   -- that user already owns the Cinder database credentials
# Not granted: root shell, any other command, any other user.
set -u

ROLE=""; KEY=""; USER_NAME="openmove"; CINDER_USER="cinder"; PY=""; CHECK=0; FROM=""
while [ $# -gt 0 ]; do
  case "$1" in
    --role) ROLE="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    --key-file) KEY="$(cat "$2")"; shift 2 ;;
    --user) USER_NAME="$2"; shift 2 ;;
    --cinder-user) CINDER_USER="$2"; shift 2 ;;
    --python) PY="$2"; shift 2 ;;
    --from) FROM="$2"; shift 2 ;;
    --check) CHECK=1; shift ;;
    -h|--help) sed -n 5,30p "$0"; exit 0 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done
case "$ROLE" in mover|cinder|both) ;; *) echo "--role must be mover, cinder or both"; exit 2 ;; esac
[ "$(id -u)" -eq 0 ] || { echo "run this as root (sudo bash $0 ...)"; exit 2; }
if [ "$CHECK" -eq 0 ] && [ -z "$KEY" ]; then echo "--key is needed: the public key shown on the OpenMove Hosts tab"; exit 2; fi
case "$USER_NAME" in *[!a-z0-9_-]*|"") echo "the user name may hold only a-z, 0-9, _ and -"; exit 2 ;; esac

PROBLEMS=0
say() { printf '%-8s %s\n' "$1" "$2"; }
bad() { PROBLEMS=$((PROBLEMS + 1)); say "MISSING" "$1"; }
ok() { say "ok" "$1"; }
act() { if [ "$CHECK" -eq 1 ]; then bad "$1"; return 1; fi; return 0; }

# ---- 1. packages -----------------------------------------------------------------------------------------------------------------
pm=""
for c in apt-get dnf yum zypper; do command -v "$c" >/dev/null 2>&1 && { pm="$c"; break; }; done
install_pkgs() {
  [ $# -gt 0 ] || return 0
  case "$pm" in
    apt-get) DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" >/dev/null ;;
    dnf|yum) "$pm" install -y -q "$@" >/dev/null ;;
    zypper) zypper --non-interactive install "$@" >/dev/null ;;
    *) echo "no supported package manager: install by hand: $*"; return 1 ;;
  esac
}
need=""
if [ -z "$PY" ]; then PY="$(command -v python3 2>/dev/null || true)"; fi
if [ -z "$PY" ] || [ ! -x "$PY" ]; then need="$need python3"; fi
if [ "$ROLE" = "mover" ] || [ "$ROLE" = "both" ]; then
  command -v lvs >/dev/null 2>&1 || need="$need lvm2"
  command -v blockdev >/dev/null 2>&1 || need="$need util-linux"
  command -v sha256sum >/dev/null 2>&1 || need="$need coreutils"
fi
command -v sshd >/dev/null 2>&1 || [ -x /usr/sbin/sshd ] || need="$need openssh-server"
if [ -n "$need" ]; then
  if act "packages needed:$need"; then
    [ "$pm" = "apt-get" ] && apt-get update -qq >/dev/null 2>&1
    install_pkgs $need && ok "installed:$need" || { bad "could not install:$need"; }
  fi
else
  ok "packages: python3, sshd$( [ "$ROLE" != cinder ] && echo ', lvm2, util-linux')"
fi
[ -z "$PY" ] && PY="$(command -v python3 2>/dev/null || true)"
if [ -n "$PY" ] && "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 6) else 1)' 2>/dev/null; then
  ok "python: $("$PY" -c 'import sys; print(sys.version.split()[0])') at $PY (3.6 or later is needed)"
else
  bad "python 3.6 or later was not found (use --python PATH for another interpreter)"
fi

# ---- 2. the service user ---------------------------------------------------------------------------------------------------------
if id "$USER_NAME" >/dev/null 2>&1; then
  ok "user $USER_NAME exists"
elif act "user $USER_NAME is missing"; then
  useradd --create-home --shell /bin/bash "$USER_NAME" && passwd -l "$USER_NAME" >/dev/null 2>&1 && ok "user $USER_NAME created (password locked)" || bad "could not create $USER_NAME"
fi

# ---- 3. the key ------------------------------------------------------------------------------------------------------------------
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
if [ -n "$HOME_DIR" ] && [ -f "$HOME_DIR/.ssh/authorized_keys" ] && [ -n "$KEY" ] && grep -qF "$KEY" "$HOME_DIR/.ssh/authorized_keys"; then
  ok "OpenMove's key is in $HOME_DIR/.ssh/authorized_keys"
elif [ "$CHECK" -eq 1 ]; then
  if [ -n "$HOME_DIR" ] && [ -s "$HOME_DIR/.ssh/authorized_keys" ]; then ok "authorized_keys has keys (the key to compare was not given)"; else bad "no authorized_keys for $USER_NAME"; fi
elif [ -n "$HOME_DIR" ]; then
  case "$KEY" in ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*) ;; *) echo "the key does not look like a public key"; exit 2 ;; esac
  install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR/.ssh"
  touch "$HOME_DIR/.ssh/authorized_keys"
  opts="no-port-forwarding,no-X11-forwarding,no-agent-forwarding"
  [ -n "$FROM" ] && opts="from=\"$FROM\",$opts"
  echo "$opts $KEY" >> "$HOME_DIR/.ssh/authorized_keys"
  chown "$USER_NAME:$USER_NAME" "$HOME_DIR/.ssh/authorized_keys"; chmod 600 "$HOME_DIR/.ssh/authorized_keys"
  ok "OpenMove's key added to $HOME_DIR/.ssh/authorized_keys"
fi

# ---- 4a. the control program: how OpenMove changes this host later, without the password -----------------------------------------------------
# After this setup OpenMove has only its key. The host's storage settings are read and changed through this one program, which takes only
# three commands: install a script of OpenMove (checked by its hash), run it, and print the hashes. Nothing else is reachable through it.
HOSTCTL=/usr/local/sbin/openmove-hostctl
if [ "$ROLE" = "mover" ] || [ "$ROLE" = "both" ]; then
  tmp="$(mktemp)"
  cat > "$tmp" <<'HOSTCTL_EOF'
#!/bin/bash
# openmove-hostctl: written by OpenMove's prepare_host.sh. install NAME SHA256 (script on standard input) | run NAME [ARGS] | version
set -u
DIR=/usr/local/lib/openmove
case "${1:-}" in
  install)
    name="${2:-}"; sha="${3:-}"
    case "$name" in configure_storage.sh|inspect_host.py) ;; *) echo "not an OpenMove script: $name" >&2; exit 2 ;; esac
    case "$sha" in *[!0-9a-f]*|"") echo "give the sha256 of the script" >&2; exit 2 ;; esac
    [ "${#sha}" -eq 64 ] || { echo "give the sha256 of the script" >&2; exit 2; }
    mkdir -p "$DIR"; chmod 755 "$DIR"
    t="$(mktemp "$DIR/.new.XXXXXX")" || exit 1
    cat > "$t"
    got="$(sha256sum "$t" | cut -d' ' -f1)"
    if [ "$got" != "$sha" ]; then rm -f "$t"; echo "the script arrived damaged (sha256 $got, expected $sha)" >&2; exit 3; fi
    chmod 755 "$t"; mv -f "$t" "$DIR/$name"; echo "$sha" > "$DIR/$name.sha256"; echo "installed $name"
    ;;
  run)
    name="${2:-}"; shift 2
    case "$name" in
      configure_storage.sh) [ -f "$DIR/$name" ] || { echo "install $name first" >&2; exit 4; }; exec bash "$DIR/$name" "$@" ;;
      inspect_host.py) [ -f "$DIR/$name" ] || { echo "install $name first" >&2; exit 4; }; exec python3 "$DIR/$name" "$@" ;;
      *) echo "not an OpenMove script: $name" >&2; exit 2 ;;
    esac
    ;;
  version) for f in "$DIR"/*.sha256; do [ -f "$f" ] && echo "$(basename "$f" .sha256) $(cat "$f")"; done; exit 0 ;;
  *) echo "usage: openmove-hostctl install NAME SHA256 | run NAME [ARGS] | version" >&2; exit 2 ;;
esac
HOSTCTL_EOF
  if [ -f "$HOSTCTL" ] && cmp -s "$tmp" "$HOSTCTL"; then ok "the control program is in place ($HOSTCTL)"
  elif act "the control program is missing or different ($HOSTCTL)"; then
    mkdir -p /usr/local/lib/openmove && chmod 755 /usr/local/lib/openmove
    install -m 755 -o root -g root "$tmp" "$HOSTCTL" && ok "the control program written to $HOSTCTL" || bad "could not write $HOSTCTL"
  fi
  rm -f "$tmp"
fi

# ---- 4. sudo rights --------------------------------------------------------------------------------------------------------------
SUDOERS=/etc/sudoers.d/openmove
want=""
if [ "$ROLE" = "mover" ] || [ "$ROLE" = "both" ]; then
  want="$want$USER_NAME ALL=(root) NOPASSWD: /usr/bin/env OPENMOVE_MOVER_HOME=* * */.openmove-mover/mover-*.py *"$'
'
  want="$want$USER_NAME ALL=(root) NOPASSWD: $HOSTCTL *"$'
'
fi
if [ "$ROLE" = "cinder" ] || [ "$ROLE" = "both" ]; then
  want="$want$USER_NAME ALL=($CINDER_USER) NOPASSWD: ALL"$'
'
fi
tmp="$(mktemp)"; { echo "# OpenMove: written by prepare_host.sh, role $ROLE"; printf '%s' "$want"; } > "$tmp"
if [ -f "$SUDOERS" ] && cmp -s "$tmp" "$SUDOERS"; then
  ok "sudo rights are in place ($SUDOERS)"
elif act "sudo rights are missing or different ($SUDOERS)"; then
  if command -v visudo >/dev/null 2>&1 && ! visudo -cf "$tmp" >/dev/null 2>&1; then bad "the sudoers text did not pass visudo; nothing was written"
  else install -m 440 -o root -g root "$tmp" "$SUDOERS" && ok "sudo rights written to $SUDOERS"; fi
fi
rm -f "$tmp"
[ "$ROLE" = "cinder" ] || [ "$ROLE" = "both" ] && { id "$CINDER_USER" >/dev/null 2>&1 && ok "the Cinder service user $CINDER_USER exists" || bad "no user $CINDER_USER on this host: is this the host that runs cinder-volume? (--cinder-user NAME)"; }

# ---- 5. sshd ---------------------------------------------------------------------------------------------------------------------
if pgrep -x sshd >/dev/null 2>&1; then
  ok "sshd is running"
elif act "sshd is not running"; then
  if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then systemctl enable --now sshd 2>/dev/null || systemctl enable --now ssh 2>/dev/null; else /usr/sbin/sshd; fi
  pgrep -x sshd >/dev/null 2>&1 && ok "sshd started" || bad "sshd did not start"
fi
if command -v sshd >/dev/null 2>&1 || [ -x /usr/sbin/sshd ]; then
  pk="$( (sshd -T 2>/dev/null || /usr/sbin/sshd -T 2>/dev/null) | awk '$1=="pubkeyauthentication"{print $2}')"
  [ "$pk" = "yes" ] || [ -z "$pk" ] && ok "sshd allows key login" || bad "sshd does not allow key login (PubkeyAuthentication is $pk)"
fi

# ---- 6. the report ---------------------------------------------------------------------------------------------------------------
if [ "$ROLE" = "mover" ] || [ "$ROLE" = "both" ]; then
  if command -v vgs >/dev/null 2>&1; then
    n="$(vgs --noheadings -o vg_name 2>/dev/null | wc -l)"
    [ "$n" -gt 0 ] && ok "this host sees $n volume group(s): $(vgs --noheadings -o vg_name 2>/dev/null | tr -s ' \n' ' ')" || say "note" "no volume group is visible yet: the migration host must see the storage of BOTH back-ends (check H8) -- see docs/DEPLOYMENT.md, section on storage access"
  fi
fi
echo
if [ "$PROBLEMS" -eq 0 ]; then echo "Host ready. Now register it on the OpenMove Hosts tab, compare and pin its host key, and run the checks."; else echo "$PROBLEMS item(s) need attention (see MISSING above)."; fi
[ "$PROBLEMS" -eq 0 ]
