#!/usr/bin/env bash
# preflight.sh -- the read-only checks behind task4's Initialize.
#
# No power, no bring-up, nothing written on the bench. It fails loudly on the
# first problem, because every one of these otherwise surfaces an hour later as
# a hung scan with a symptom that names nothing.
#
#   1. the client host has the bench stack and its site exports
#   2. the hexacontroller answers on 22
#   3. the puller port on the client host is free, or held by a puller of ours
#      (6001, or the lane's own port when the GUI drives several Krias)
#   4. no second puller is already pulling this Kria's stream (8888)
#   5. the GUI's daqplots directory is emptied, so the result dropdown starts clean
#      (for a lane: only that lane's runs, the other Krias' plots stay)
#
# Exit 0 = safe to run. Any other exit = do not press Run.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ON_CLIENT="$HERE/on_client.sh"
CONF="${MMTS_CONF:-$ROOT/data/mmts_configurations.yaml}"

step() { printf '\n---------- preflight %s\n' "$*"; }
fail() { printf '\nPREFLIGHT FAILED: %s\n' "$*" >&2; exit 1; }

CLIENT="$(python3 "$ROOT/scripts/access_yaml_var.py" "$CONF" MMTS_bench.client_host)"
printf 'client host: %s\n' "${CLIENT:-<this machine>}"

# --- 1. the bench stack -----------------------------------------------------
step '1/5  client host: site exports, the bench scripts, daq-client and unpack'
"$ON_CLIENT" '
    set -u
    : "${MMTS_ROOT:?not exported on the client host -- see instructions 0.5}"
    : "${KRIA_IP:?not exported on the client host -- see instructions 0.5}"
    : "${MM:?not exported on the client host -- see instructions 0.5}"
    echo "MMTS_ROOT=$MMTS_ROOT  KRIA_IP=$KRIA_IP  KRIA_USER=${KRIA_USER:-daq}"
    ls "$MM/run_slot.sh" "$MM/register_boards.py" "$MM/lib.sh"
    command -v daq-client unpack
' || fail "the client host is missing part of the bench stack (step 1 above)"

# --- 2. the hexacontroller --------------------------------------------------
# The exec 3<> form, not `cat < /dev/tcp/...`: the redirection form returns as
# soon as the connection is made, while cat sits on the socket until timeout
# kills it and always reports 124.
step "2/5  hexacontroller answers on port 22"
"$ON_CLIENT" '
    set -u
    timeout 5 bash -c "exec 3<>/dev/tcp/$KRIA_IP/22" \
        && echo "$KRIA_IP:22 open" \
        || { echo "no route to $KRIA_IP:22 -- is the hexacontroller powered and on the network?"; exit 1; }
' || fail "the hexacontroller is not reachable from the client host"

# --- 3. port 6001 -----------------------------------------------------------
# daq-client receives the event stream on 6001. If a process we do not own holds
# it, lib.sh's puller_restart cannot kill it, then fails to bind, and every scan
# reports "no summary.json". On pi0 that process is daq-client.service, the
# single-module stand's own puller, running as root. Print who and stop; the fix
# is the operator's and it is systemctl, never pkill.
PORT="${MMTS_PULLER_PORT:-6001}"
step "3/5  port $PORT on the client host is free or ours"
"$ON_CLIENT" '
    set -u
    port=${PULLER_PORT:-6001}
    line=$(ss -ltnp 2>/dev/null | grep ":$port ")
    if [ -z "$line" ]; then echo "$port free"; exit 0; fi
    echo "$line"

    # ss -p names the process only when this account owns it.
    case "$line" in *\"daq-client\"*)
        echo "held by our own daq-client -- puller_restart will replace it"; exit 0;; esac
    if [ "$port" != 6001 ]; then
        echo
        echo "port $port is held by a process this account does not own. Pick another"
        echo "puller_port for this lane in MMTS_bench.lanes, or find the holder."
        exit 1
    fi

    # The service check comes FIRST and does not look at who we are. A live
    # daq-client.service is the single-module stand, whatever uid we hold, and
    # lib.sh puller_restart kills daq-client by name with no uid filter: run the
    # GUI as root and that one pkill takes down the other stand mid-campaign.
    # is-active, not kill -0: the service runs as root, and kill -0 against a
    # root pid fails with EPERM for an unprivileged user, which would let this
    # check fall through to the uid test it is here to override.
    if systemctl is-active --quiet daq-client.service 2>/dev/null; then
        svc=$(systemctl show -p MainPID --value daq-client.service 2>/dev/null)
        echo
        echo "6001 is held by daq-client.service (pid ${svc:-unknown}), the single-module stand."
        echo "For this session:"
        echo "    sudo systemctl stop daq-client.service"
        echo "and when you are done:"
        echo "    sudo systemctl start daq-client.service"
        echo "Never pkill it."
        exit 1
    fi

    # -x on the process NAME, not -f on the command line: this very script
    # mentions daq-client.service, and a -f pattern matches our own shell.
    if pgrep -u "$(id -u)" -x daq-client >/dev/null 2>&1; then
        echo "held by our own daq-client -- puller_restart will replace it"
        exit 0
    fi
    echo
    echo "6001 is held by a process this account does not own. Find it before"
    echo "starting: a puller that is not ours makes every scan report no data."
    exit 1
' || fail "port $PORT on the client host is taken (step 3 above)"

# --- 4. one puller per Kria -------------------------------------------------
# The Kria PUSHes events round-robin over 8888. A second puller anywhere on the
# network -- often a forgotten daq-client in a Docker container on another
# machine -- silently takes half of every run.
step "4/5  no other puller is attached to the hexacontroller (port 8888)"
"$ON_CLIENT" '
    set -u
    peers=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "${KRIA_USER:-daq}@$KRIA_IP" \
                "ss -tn 2>/dev/null | grep 8888" 2>/dev/null | grep ESTAB)
    if [ -z "$peers" ]; then echo "no established peer on 8888"; exit 0; fi
    echo "$peers"

    # Our OWN puller is not a second puller. Step 3 has already identified it,
    # and lib.sh puller_restart kills and replaces it at the start of every run,
    # so a client host that is still attached from a stopped run is normal. Only
    # a peer on ANOTHER machine steals half the stream. Without this, every
    # Initialize after a stopped run refused, naming our own daq-client.
    foreign="$peers"
    for ip in $(hostname -I 2>/dev/null); do
        foreign=$(printf "%s\n" "$foreign" | grep -v " ${ip}:" || true)
    done
    if [ -z "$foreign" ]; then
        echo "the only peer is this client host -- ours, puller_restart replaces it"
        exit 0
    fi

    echo
    echo "Another puller already holds this Kria stream. It will steal half of"
    echo "every run. Find it (the peer address above) and stop it first."
    exit 1
' || fail "a second puller is attached to the hexacontroller (step 4 above)"

# --- 5. the GUI's own plot directory ----------------------------------------
step "5/5  clearing the GUI daqplots directory"
DATALOC="$(python3 "$ROOT/scripts/access_yaml_var.py" \
            "${AndrewModuleTestingGUI_BASE:-$ROOT/external_packages/hgcal-module-testing-gui}/configuration.yaml" DataLoc)"
[ -n "$DATALOC" ] || fail "DataLoc is not set in the module testing GUI configuration"
mkdir -p "$DATALOC/daqplots"
# This is the only destructive step in preflight and it runs on the GUI host, so
# it never went through on_client.sh and never saw the dry-run flag. A dry run
# then really deleted a live gallery, which is how run1's plots were lost.
# A lane owns only the runs tagged with its name; the other Krias' stay.
PICK="*"
[ -n "${MMTS_LANE:-}" ] && PICK="${MMTS_LANE}_*"
if [ "${MMTS_DRYRUN:-0}" = "1" ]; then
    echo "[dry-run] would clear $DATALOC/daqplots/$PICK"
else
    /bin/rm -rf "$DATALOC"/daqplots/$PICK
    echo "cleared $DATALOC/daqplots/$PICK"
fi

printf '\nPREFLIGHT OK -- the bench is ready for Run\n'
