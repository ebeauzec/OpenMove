#!/bin/bash
# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
#
# Sets up the storage paths of a migration host: the iSCSI initiator, multipath and the logins to the arrays (docs/DEPLOYMENT.md, "Storage paths").
# Run as root on the host. OpenMove sends it and runs it for you (Hosts tab, "Set up a host"); it can also be run by hand. Safe to run again:
# every step looks first and changes only what differs. It never creates, maps, formats or deletes a volume, and it does not touch OpenStack.
#
#   bash configure_storage.sh --protocol iscsi|fc [--portal IP[:PORT]]... [--initiator IQN] [--chap-user NAME --chap-secret-stdin]
#                             [--array pure|solidfire]... [--multipath-extra-b64 TEXT] [--replacement-timeout SECONDS] [--check]
#   --chap-secret-stdin   the CHAP secret is read from the first line of standard input (never from the command line, so it is not in ps or in logs)
#   --multipath-extra-b64 extra multipath.conf text, base64 encoded, appended to OpenMove's drop-in file
#   --check               only report what is missing; change nothing
#
# What it does:
#   1. installs open-iscsi (iscsi) and multipath-tools from the system's own package repository
#   2. sets iscsid.conf: automatic start, the replacement timeout, CHAP when given (the original file is kept once as iscsid.conf.openmove-orig)
#   3. sets the initiator name when one is given (otherwise keeps the one the host has) and prints it: register it on each array
#   4. writes /etc/multipath/conf.d/openmove.conf (a drop-in; the main /etc/multipath.conf is only created when the host has none)
#   5. starts iscsid and multipathd; for each portal: discovers the targets, logs in, makes the login automatic at boot
#   6. rescans and reports the sessions, the multipath maps and the paths per map, and prints one line  OPENMOVE-JSON {...}  for OpenMove
# For fc it does 1, 4 and 5 without the iSCSI parts and prints the host's port names (WWPNs): zone them and register them on each array.
set -u

PROTO=""; PORTALS=(); INITIATOR=""; CHAP_USER=""; CHAP_STDIN=0; ARRAYS=(); EXTRA_B64=""; TIMEO=15; CHECK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --protocol) PROTO="$2"; shift 2 ;;
    --portal) PORTALS+=("$2"); shift 2 ;;
    --initiator) INITIATOR="$2"; shift 2 ;;
    --chap-user) CHAP_USER="$2"; shift 2 ;;
    --chap-secret-stdin) CHAP_STDIN=1; shift ;;
    --array) ARRAYS+=("$2"); shift 2 ;;
    --multipath-extra-b64) EXTRA_B64="$2"; shift 2 ;;
    --replacement-timeout) TIMEO="$2"; shift 2 ;;
    --check) CHECK=1; shift ;;
    -h|--help) sed -n 5,26p "$0"; exit 0 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done
case "$PROTO" in iscsi|fc) ;; *) echo "--protocol must be iscsi or fc"; exit 2 ;; esac
case "$TIMEO" in ''|*[!0-9]*) echo "--replacement-timeout must be a number of seconds"; exit 2 ;; esac
for p in "${PORTALS[@]:-}"; do
  [ -z "$p" ] && continue
  case "$p" in *[!A-Za-z0-9.:_\[\]-]*) echo "the portal '$p' is not an address"; exit 2 ;; esac
done
case "$INITIATOR" in ""|iqn.*|eui.*|naa.*) ;; *) echo "--initiator must be an iqn., eui. or naa. name"; exit 2 ;; esac
case "$CHAP_USER" in *[!A-Za-z0-9._@:-]*) echo "the CHAP user name holds a character that is not allowed"; exit 2 ;; esac
for a in "${ARRAYS[@]:-}"; do case "$a" in ""|pure|solidfire) ;; *) echo "--array must be pure or solidfire"; exit 2 ;; esac; done
[ "$(id -u)" -eq 0 ] || { echo "run this as root"; exit 2; }
CHAP_SECRET=""
if [ "$CHAP_STDIN" -eq 1 ]; then IFS= read -r CHAP_SECRET || true; fi
if [ -n "$CHAP_USER" ] && [ -z "$CHAP_SECRET" ]; then echo "a CHAP user needs --chap-secret-stdin and a secret on the first line of standard input"; exit 2; fi

PROBLEMS=0
say() { printf '%-8s %s\n' "$1" "$2"; }
bad() { PROBLEMS=$((PROBLEMS + 1)); say "MISSING" "$1"; }
ok() { say "ok" "$1"; }
warn() { say "note" "$1"; }
act() { if [ "$CHECK" -eq 1 ]; then bad "$1"; return 1; fi; return 0; }

# ---- 1. packages -----------------------------------------------------------------------------------------------------------------
pm=""
for c in apt-get dnf yum zypper; do command -v "$c" >/dev/null 2>&1 && { pm="$c"; break; }; done
case "$pm" in
  apt-get) PK_ISCSI="open-iscsi"; PK_MP="multipath-tools" ;;
  dnf|yum) PK_ISCSI="iscsi-initiator-utils"; PK_MP="device-mapper-multipath" ;;
  zypper) PK_ISCSI="open-iscsi"; PK_MP="multipath-tools" ;;
  *) PK_ISCSI="open-iscsi"; PK_MP="multipath-tools" ;;
esac
need=""
if [ "$PROTO" = "iscsi" ]; then command -v iscsiadm >/dev/null 2>&1 || need="$need $PK_ISCSI"; fi
command -v multipath >/dev/null 2>&1 || need="$need $PK_MP"
if [ -n "$need" ]; then
  if act "packages needed:$need"; then
    case "$pm" in
      apt-get) apt-get update -qq </dev/null >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $need </dev/null >/dev/null ;;
      dnf|yum) "$pm" install -y -q $need </dev/null >/dev/null ;;
      zypper) zypper --non-interactive install $need </dev/null >/dev/null ;;
      *) echo "no supported package manager: install by hand:$need" ;;
    esac
    if { [ "$PROTO" != "iscsi" ] || command -v iscsiadm >/dev/null 2>&1; } && command -v multipath >/dev/null 2>&1; then ok "installed:$need"; else bad "could not install:$need"; fi
  fi
else
  ok "packages: $( [ "$PROTO" = iscsi ] && echo 'iscsiadm, ')multipath"
fi

# A managed value in a "key = value" file: replaces the line when the key is there, adds it when not. The original is kept once.
setkey() {
  local file="$1" key="$2" value="$3"
  [ -f "$file.openmove-orig" ] || cp -p "$file" "$file.openmove-orig"
  if grep -Eq "^[[:space:]]*${key//./\\.}[[:space:]]*=" "$file"; then
    sed -i -E "s|^[[:space:]]*${key//./\\.}[[:space:]]*=.*|${key} = ${value}|" "$file"
  else
    printf '%s = %s\n' "$key" "$value" >> "$file"
  fi
}
getkey() { awk -F= -v k="$2" '{gsub(/[ \t]/, "", $1)} $1==k {v=$2; gsub(/^[ \t]+|[ \t]+$/, "", v); print v}' "$1" 2>/dev/null | tail -1; }
wantkey() {          # file key value label
  local cur; cur="$(getkey "$1" "$2")"
  if [ "$cur" = "$3" ]; then ok "$4 is $3"
  elif act "$4 is '${cur:-unset}', should be $3"; then setkey "$1" "$2" "$3" && ok "$4 set to $3"; fi
}

# ---- 2 and 3. the iSCSI initiator ---------------------------------------------------------------------------------------------------
IQN=""
if [ "$PROTO" = "iscsi" ] && command -v iscsiadm >/dev/null 2>&1; then
  CONF=/etc/iscsi/iscsid.conf
  if [ -f "$CONF" ]; then
    wantkey "$CONF" node.startup automatic "iscsid node.startup"
    wantkey "$CONF" node.session.timeo.replacement_timeout "$TIMEO" "iscsid replacement timeout"
    if [ -n "$CHAP_USER" ]; then
      cur_u="$(getkey "$CONF" node.session.auth.username)"; cur_a="$(getkey "$CONF" node.session.auth.authmethod)"; cur_p="$(getkey "$CONF" node.session.auth.password)"
      if [ "$cur_a" = "CHAP" ] && [ "$cur_u" = "$CHAP_USER" ] && [ "$cur_p" = "$CHAP_SECRET" ]; then ok "CHAP user $CHAP_USER is set"
      elif act "CHAP is not set up for $CHAP_USER"; then
        setkey "$CONF" node.session.auth.authmethod CHAP; setkey "$CONF" node.session.auth.username "$CHAP_USER"; setkey "$CONF" node.session.auth.password "$CHAP_SECRET"
        chmod 600 "$CONF"; ok "CHAP set for $CHAP_USER (the secret is in $CONF, readable by root only)"
      fi
    fi
  else
    bad "$CONF is missing (open-iscsi is not installed properly)"
  fi
  NAMEFILE=/etc/iscsi/initiatorname.iscsi
  [ -f "$NAMEFILE" ] || { [ "$CHECK" -eq 0 ] && mkdir -p /etc/iscsi && { [ -x /sbin/iscsi-iname ] && echo "InitiatorName=$(/sbin/iscsi-iname)" > "$NAMEFILE" || true; }; }
  IQN="$(sed -n 's/^InitiatorName=//p' "$NAMEFILE" 2>/dev/null | tail -1)"
  if [ -n "$INITIATOR" ] && [ "$IQN" != "$INITIATOR" ]; then
    if act "initiator name is '${IQN:-none}', should be $INITIATOR"; then
      if iscsiadm -m session >/dev/null 2>&1; then bad "the initiator name cannot be changed while sessions are open; log out first"
      else echo "InitiatorName=$INITIATOR" > "$NAMEFILE"; IQN="$INITIATOR"; ok "initiator name set to $INITIATOR"; fi
    fi
  elif [ -n "$IQN" ]; then ok "initiator name: $IQN"
  else bad "the host has no initiator name"; fi
fi

# ---- 4. multipath ------------------------------------------------------------------------------------------------------------------
MPDIR=/etc/multipath/conf.d; MPFILE=$MPDIR/openmove.conf
tmp="$(mktemp)"
{
  echo "# OpenMove: written by configure_storage.sh. Starting values; check them against the array vendor's current guide before production."
  echo "# This file is replaced when the script runs again. Put your own changes in the main /etc/multipath.conf or in another file of this folder."
  echo "devices {"
  for a in "${ARRAYS[@]:-}"; do
    case "$a" in
      pure) cat <<'EOF'
    device {
        vendor "PURE"
        product "FlashArray"
        path_selector "queue-length 0"
        path_grouping_policy group_by_prio
        path_checker tur
        fast_io_fail_tmo 10
        dev_loss_tmo 60
        no_path_retry 0
        hardware_handler "1 alua"
        prio alua
        failback immediate
    }
EOF
      ;;
      solidfire) cat <<'EOF'
    device {
        vendor "SolidFire"
        product "SSD SAN"
        path_grouping_policy multibus
        path_selector "service-time 0"
        path_checker tur
        features "1 queue_if_no_path"
        no_path_retry 24
        failback immediate
        rr_min_io 10
    }
EOF
      ;;
    esac
  done
  echo "}"
  if [ -n "$EXTRA_B64" ]; then echo "# --- text added in OpenMove ---"; printf '%s' "$EXTRA_B64" | base64 -d 2>/dev/null; echo; fi
} > "$tmp"
if [ -f "$MPFILE" ] && cmp -s "$tmp" "$MPFILE"; then ok "multipath drop-in is in place ($MPFILE)"
elif act "multipath drop-in is missing or different ($MPFILE)"; then
  mkdir -p "$MPDIR" && install -m 644 "$tmp" "$MPFILE" && ok "multipath drop-in written to $MPFILE" || bad "could not write $MPFILE"
fi
rm -f "$tmp"
if [ -f /etc/multipath.conf ]; then ok "/etc/multipath.conf exists (left as it is)"
elif act "/etc/multipath.conf is missing"; then
  printf '# OpenMove: made because the host had none. Edit freely.\ndefaults {\n    user_friendly_names no\n    find_multipaths yes\n}\n' > /etc/multipath.conf && ok "/etc/multipath.conf made (find_multipaths yes: local disks are left alone)"
fi

# ---- 5. services and logins ----------------------------------------------------------------------------------------------------------
# Starts a service when it is not running. Returns 0 when it runs. The reason is in the message when it does not (a container, a taken socket, a missing module).
svc() {
  local name="$1" why
  if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    if systemctl is-active --quiet "$name" 2>/dev/null; then ok "$name is running"; return 0; fi
    act "$name is not running" || return 1
    systemctl enable --now "$name" >/dev/null 2>&1
    if systemctl is-active --quiet "$name" 2>/dev/null; then ok "$name started"; return 0; fi
    why="$(systemctl status "$name" --no-pager 2>&1 | grep -E 'Condition|Can not|Failed|rror|denied' | head -3 | sed 's/^[ \t]*//' | tr '\n' ' ')"
    bad "$name did not start: ${why:-no reason given; see: systemctl status $name}"
    return 1
  fi
  if pgrep -x "$name" >/dev/null 2>&1; then ok "$name is running"; return 0; fi
  act "$name is not running" || return 1
  "$name" >/dev/null 2>&1 || true
  if pgrep -x "$name" >/dev/null 2>&1; then ok "$name started"; return 0; fi
  bad "$name did not start"
  return 1
}
if [ "$CHECK" -eq 0 ]; then
  modprobe dm_multipath >/dev/null 2>&1 || true
  [ "$PROTO" = "iscsi" ] && { modprobe iscsi_tcp >/dev/null 2>&1 || true; }
  mkdir -p /etc/modules-load.d && printf 'dm_multipath\n%s' "$( [ "$PROTO" = iscsi ] && echo 'iscsi_tcp
')" > /etc/modules-load.d/openmove-storage.conf 2>/dev/null
fi
ISCSID_OK=1
if [ "$PROTO" = "iscsi" ]; then svc iscsid || ISCSID_OK=0; fi
svc multipathd
if [ "$CHECK" -eq 0 ] && command -v multipathd >/dev/null 2>&1; then multipathd reconfigure >/dev/null 2>&1 || true; fi

SESSIONS=0
if [ "$PROTO" = "iscsi" ] && command -v iscsiadm >/dev/null 2>&1; then
  for p in "${PORTALS[@]:-}"; do
    [ -z "$p" ] && continue
    case "$p" in *:[0-9]*) ;; *) p="$p:3260" ;; esac
    if [ "$ISCSID_OK" -ne 1 ]; then bad "no login to $p: iscsid is not running here"; continue; fi
    if iscsiadm -m session 2>/dev/null | grep -qF " $p,"; then ok "logged in to $p"
    elif act "no session to $p"; then
      out="$(iscsiadm -m discovery -t sendtargets -p "$p" 2>&1)"; rc=$?
      if [ $rc -ne 0 ]; then bad "discovery on $p failed: $(echo "$out" | tr '\n' ' ')"; continue; fi
      if [ -z "$out" ]; then bad "discovery on $p found no target: is the initiator name $IQN registered on the array?"; continue; fi
      iscsiadm -m node -p "$p" -o update -n node.startup -v automatic >/dev/null 2>&1
      lo="$(iscsiadm -m node -p "$p" --login 2>&1)"; rc=$?
      if [ $rc -eq 0 ] || echo "$lo" | grep -q "already present"; then ok "logged in to $p"; else bad "login to $p failed: $(echo "$lo" | tr '\n' ' ')"; fi
    fi
  done
  [ "$CHECK" -eq 0 ] && { iscsiadm -m session --rescan >/dev/null 2>&1 || true; }
  SESSIONS="$(iscsiadm -m session 2>/dev/null | wc -l)"
fi
[ "$CHECK" -eq 0 ] && command -v multipath >/dev/null 2>&1 && { udevadm settle >/dev/null 2>&1 || true; multipath -r >/dev/null 2>&1 || true; }

# ---- 6. the report -----------------------------------------------------------------------------------------------------------------
WWPNS=""
if [ "$PROTO" = "fc" ]; then
  for f in /sys/class/fc_host/host*/port_name; do [ -f "$f" ] && WWPNS="$WWPNS$(sed 's/^0x//' "$f") "; done
  if [ -n "$WWPNS" ]; then ok "FC port names (zone and register these on the arrays): $WWPNS"; else bad "no FC port (HBA) was found on this host"; fi
fi
MAPS=0; MINP=0
if command -v multipath >/dev/null 2>&1; then
  counts="$(multipath -ll 2>/dev/null | awk '/ dm-[0-9]+ /{if(m)print n; m=1; n=0; next} /[0-9]+:[0-9]+:[0-9]+:[0-9]+ +[a-z]+/{n++} END{if(m)print n}')"
  MAPS="$(printf '%s\n' "$counts" | grep -c '[0-9]')"
  MINP="$(printf '%s\n' "$counts" | grep '[0-9]' | sort -n | head -1)"; MINP="${MINP:-0}"
fi
if [ "$MAPS" -gt 0 ]; then
  ok "$MAPS multipath device(s), at least $MINP path(s) each"
  [ "$MINP" -lt 2 ] && warn "a device has a single path: a path failure stops the copy; add a second portal or port"
else
  warn "no multipath device yet: the volumes of the arrays appear after the initiator or the ports are registered and mapped on the arrays"
fi
if [ "$PROTO" = "iscsi" ] && [ "${#PORTALS[@]}" -gt 0 ] && [ -n "${PORTALS[0]:-}" ] && [ "$SESSIONS" -lt "${#PORTALS[@]}" ] && [ "$CHECK" -eq 0 ]; then
  warn "$SESSIONS session(s) for ${#PORTALS[@]} portal(s)"
fi
plist=""; for p in "${PORTALS[@]:-}"; do [ -n "$p" ] && plist="$plist\"$p\","; done
wlist=""; for w in $WWPNS; do wlist="$wlist\"$w\","; done
echo "OPENMOVE-JSON {\"protocol\":\"$PROTO\",\"initiator\":\"$IQN\",\"wwpns\":[${wlist%,}],\"portals\":[${plist%,}],\"sessions\":$SESSIONS,\"maps\":$MAPS,\"min_paths\":$MINP}"
echo
if [ "$PROBLEMS" -eq 0 ]; then echo "Storage paths ready."; else echo "$PROBLEMS item(s) need attention (see MISSING above)."; fi
[ "$PROBLEMS" -eq 0 ]
