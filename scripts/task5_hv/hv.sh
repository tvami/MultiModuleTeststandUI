#!/usr/bin/env bash
# hv.sh <hv.py arguments> -- run one hv.py command on the client host.
#
# The single call site for the Keithley. hv.py itself is the only thing on the
# bench that talks to the instrument; this is the only thing that calls hv.py,
# so the interpreter and the GUI directory are resolved from the config once and
# the runner, the makefile and Stop cannot drift apart.
#
#   hv.sh idn
#   hv.sh off
#   hv.sh set 300 --limit-ua 10 --settle 5
#
# Exit code is hv.py's: 0 ok, 3 abort (a leakcheck over the limit), 1 fail.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
CONF="${MMTS_CONF:-$ROOT/data/mmts_configurations.yaml}"

cfg() { python3 "$ROOT/scripts/access_yaml_var.py" "$CONF" "$1"; }

HV_PY="$(cfg MMTS_bench.hv.python)";   HV_PY="${HV_PY:-python3}"
HV_GUI_DIR="$(cfg MMTS_bench.hv.gui_dir)"

### 🔑 ONE hv.py at a time. Two processes opening the serial port together get
### "device reports readiness to read but returned no data (multiple access on
### port?)" and the command fails. It happens whenever a run's own exit trap,
### the GUI's Stop and a manual call land together. A failed OPEN changes
### nothing on the instrument, so this is noise rather than danger -- but it
### hides real failures, so serialise instead of relying on the retry.
###
### mkdir, not flock: macOS has no flock(1), and mkdir is atomic everywhere.
LOCK="${MMTS_HV_LOCK:-${TMPDIR:-/tmp}/mmts_hv.lock}"
for _try in $(seq 1 120); do
    if mkdir "$LOCK" 2>/dev/null; then
        trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM
        break
    fi
    ### A lock older than 5 minutes is a crashed caller, not a live one: no
    ### single hv.py command takes that long, and the longest (a leakcheck with
    ### ramps) is well under two.
    if [ -d "$LOCK" ] && [ -z "$(find "$LOCK" -maxdepth 0 -mmin -5 2>/dev/null)" ]; then
        echo "  hv.sh: clearing a stale lock at $LOCK" >&2
        rmdir "$LOCK" 2>/dev/null
    fi
    [ "$_try" = 1 ] && echo "  hv.sh: another HV command is running, waiting..." >&2
    sleep 1
done

# Never inside a lane's tracked session: a lane Stop must not cut an hv.py
# ramp short, it calls hv.sh off itself.
MMTS_LANE_TRACK=0 "$ROOT/scripts/task4_mmts_pedestal/on_client.sh" \
    "${HV_GUI_DIR:+HV_GUI_DIR=$HV_GUI_DIR }$HV_PY \"\$MM/hv.py\" $*"
