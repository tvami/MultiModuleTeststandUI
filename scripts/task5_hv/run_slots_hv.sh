#!/usr/bin/env bash
# run_slots_hv.sh -- pedestals versus bias voltage, every filled slot, one at a time.
#
# task4's run_slots.sh with a bias loop wrapped around the pedestals. Reads its
# inputs from the environment (makefile_task5 passes them):
#   moduleIDA moduleIDB moduleIDC   serials; an empty one skips that slot
#   powerBoardA/B/C                 auto (default) | fitted | none
#   nRUNS  label  skipPROBE  runTAG  bringupTRIES
#   biasVOLTS                       comma list, e.g. 1,10,100,300,500
#   hvSwitch                        none (cable, with a pause) | vitrek
#   maxLEAK                         uA; more than this below 500 V aborts the slot
#   useHV                           1 (default) | 0 = no HV at all: the plain
#                                   Multi Pedestal Run, the Keithley untouched
#   temperature humidity            C and %RH, optional; saved with the hexmaps
#
# Every pedestal and noise hexmap is also copied to <RESULTS_DIR>/<serial>/hexmaps/
# ($MM/hexmap_archive.py), named by bias voltage, for the module gallery.
#
# Per slot:
#   bring-up + gate            run_slot.sh with ONE pedestal: unchanged task4
#   take the Keithley          one lane and slot at a time (hv_lock.sh)
#   route the HV               relay (vitrek) or cable move (none), output off
#   HV on, leakage check       abort the slot if it draws too much
#   for each BV               set, settle, read leakage, nRUNS pedestals, hexmaps
#   HV to 0, output off        ALWAYS, including on Stop and on any failure
#   relays open, Keithley free for the next lane
#
# With the cable the HV is routed BEFORE the bring-up (the operator moves it
# once per slot), so the lock is held from there. With the Vitrek routing is a
# relay, so the bring-up and the gate run first and the Keithley is only held
# for the HV phase itself: the other lanes' bring-ups are not kept waiting.
#
# 🔑 run_slot.sh does bring-up, gate and pedestals in one call and its minimum is
# one pedestal, so the gate call spends one run at 0 V under the label
# <label>_gate. That pedestal is also the stall check: without it a wedged slot
# would be found only after the HV had been ramped. Every later BV goes straight
# to ped_run.sh, which does NOT bring up, so the module keeps the one bring-up
# that won its lottery for the whole bias scan.
#
# SEQUENTIAL, and not by preference: one Kria, one i2c-server, one daq-server,
# one trigger claim, and exactly one HV cable. Never -j.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
T4="$ROOT/scripts/task4_mmts_pedestal"
ON_CLIENT="$T4/on_client.sh"
PARAMS="$T4/board_params.py"
COLLECT="$T4/collect_plots.sh"
SWITCH="$HERE/hv_switch.py"
CONF="${MMTS_CONF:-$ROOT/data/mmts_configurations.yaml}"
. "$HERE/hv_lock.sh"

cfg() { python3 "$ROOT/scripts/access_yaml_var.py" "$CONF" "$1"; }

# A GUI lane's run commands record their client session, so its Stop ends only
# this lane's processes. hv.sh turns this off for its own calls.
export MMTS_LANE_TRACK=1

### Room conditions: numbers or empty, since they reach a client command line.
TEMP="${temperature:-}"; RH="${humidity:-}"
case "$TEMP" in *[!0-9.-]*) echo "temperature must be a number, got '$TEMP'"; exit 2;; esac
case "$RH"   in *[!0-9.]*)  echo "humidity must be a number, got '$RH'";      exit 2;; esac
printf '\n########## conditions: %s C, %s %%RH   [%s]\n' "${TEMP:-?}" "${RH:-?}" "$(date '+%H:%M:%S %Z')"
# on_client.sh hands these to every bench command: hexmap_robust.py puts them in the map title
export MMTS_TEMP="$TEMP" MMTS_RH="$RH"

# archive_runs SLOT DUT SINCE [LEAK_UA]: this run's hexmaps into <serial>/hexmaps/
archive_runs() {
    case "${MMTS_DRYRUN:-0}" in 1) echo "[dry-run] archive: slot $1 $2"; return 0;; esac
    "$T4/on_client.sh" "
    slot=$1; dut=$2; since=$3; leak=${4:-}
"'
    serial=$("$MM/module_of.py" "$slot" 2>/dev/null) || exit 0
    [ -n "$serial" ] || exit 0
    "${MMTS_VENV:-$MMTS_ROOT/venv}/bin/python3" "$MM/hexmap_archive.py" "$RESULTS_DIR/$serial/$dut/pedestal_run"/run_* --since "$since" \
        --leak-ua "$leak" --temp "'"$TEMP"'" --rh "'"$RH"'" --runtag "'"${runTAG:-}"'" \
        --lane "${MMTS_LANE:-}" --kria "$KRIA_IP" | grep -v "already archived"; true
' || echo "WARNING: hexmap archive failed for slot $1 (the run itself is fine)"
}

# HV off: exactly the Multi Pedestal Run, with N pedestals per slot under the
# plain label. Nothing below this case runs, so the Keithley is never opened.
case "${useHV:-1}" in
    0) START=$(date +%s)
       bash "$T4/run_slots.sh"; rc=$?
       for S in ${slots:-A B C}; do
           eval "raw=\${moduleID$S:-}"
           [ -n "$raw" ] && archive_runs "$S" "Mux${S}_${label:-ped}" "$START"
       done
       exit $rc ;;
    1) ;;
    *) echo "useHV must be 0 or 1, got '${useHV}'"; exit 2 ;;
esac

SLOTS="${slots:-A B C}"
NRUNS="${nRUNS:-5}"
LABEL="${label:-ped}"
SKIP_PROBE="${skipPROBE:-1}"
TRIES="${bringupTRIES:-8}"
RUNTAG="${runTAG:-run1}"
BIAS="${biasVOLTS:-1,10,100,300,500}"

### HV settings: the form wins, then the config, then a safe default.
HVSWITCH="${hvSwitch:-$(cfg MMTS_bench.hv.switch)}"; HVSWITCH="${HVSWITCH:-none}"
SETTLE="$(cfg MMTS_bench.hv.settle_s)";             SETTLE="${SETTLE:-5}"
LEAKV="$(cfg MMTS_bench.hv.leak_check_volts | tr -d '[],')"
LEAKV="${LEAKV:-0 1 10 100 300}"
MAXLEAK="${maxLEAK:-$(cfg MMTS_bench.hv.leak_abort_uA)}"; MAXLEAK="${MAXLEAK:-1}"
PAUSE_TIMEOUT="$(cfg MMTS_bench.hv.pause_timeout_s)";     PAUSE_TIMEOUT="${PAUSE_TIMEOUT:-3600}"
### how long a slot waits for another lane to give the Keithley back
LOCK_TIMEOUT="$(cfg MMTS_bench.hv.lock_timeout_s)";       LOCK_TIMEOUT="${LOCK_TIMEOUT:-14400}"
### Compliance and the instrument ceiling are the Keithley's own, not the bench's.
LIMIT_A="$(cfg MMTS_hardwares.keithley.CurrentLimit)";    LIMIT_A="${LIMIT_A:-10e-6}"
LIMIT_UA="$(python3 -c "print(float('$LIMIT_A') * 1e6)")"
HVMAX="$(cfg MMTS_hardwares.keithley.MaxVoltage)";        HVMAX="${HVMAX:-800}"

case "$NRUNS"      in *[!0-9]*|'') echo "nRUNS must be a number, got '$NRUNS'";  exit 2;; esac
case "$LABEL"      in *[!A-Za-z0-9_-]*|'') echo "bad label '$LABEL'";            exit 2;; esac
case "$SKIP_PROBE" in 0|1) ;; *) echo "skipPROBE must be 0 or 1";                exit 2;; esac
case "$TRIES" in ""|*[!0-9]*) echo "bringupTRIES must be a number";              exit 2;; esac
[ "$TRIES" -ge 1 ] && [ "$TRIES" -le 40 ] || { echo "bringupTRIES must be 1..40"; exit 2; }
case "$HVSWITCH"   in none|vitrek) ;; *) echo "hvSwitch must be none or vitrek"; exit 2;; esac

### The bias list, validated before anything is powered: a typo that reaches the
### Keithley as 5000 is not worth discovering with a module on the cable.
BVLIST=""
for bv in ${BIAS//,/ }; do
    case "$bv" in ""|*[!0-9]*) echo "biasVOLTS holds '$bv'; every entry is a whole number of volts"; exit 2;; esac
    [ "$bv" -le "$HVMAX" ] || { echo "biasVOLTS holds ${bv} V, above the instrument limit ${HVMAX} V"; exit 2; }
    BVLIST="$BVLIST $bv"
done
[ -n "$BVLIST" ] || { echo "biasVOLTS is empty -- nothing to scan"; exit 2; }

### 🔑 The leakage check must never probe HIGHER than the scan itself will reach.
### leak_check_volts is a fixed config list (the single-module stand's 0 1 10 100
### 300), so without this a scan of 1,10,50 V still drove the module to 300 V
### during the check. On boards with a known breakdown -- humidity does it, see
### the IV curves -- that aborts the slot at 100 V before a single bias point is
### measured, and puts the sensor through a voltage the run never wanted.
BVMAX=0
for bv in $BVLIST; do [ "$bv" -gt "$BVMAX" ] && BVMAX="$bv"; done
KEPT=""
for lv in $LEAKV; do
    case "$lv" in ''|*[!0-9]*) continue;; esac
    [ "$lv" -le "$BVMAX" ] && KEPT="$KEPT $lv"
done
### The HIGHEST bias point is always screened, even when the config list has no
### entry there. The bias loop has NO leakage abort of its own -- only this check
### does -- so without it a scan of 1,10,50 V would screen to 10 V and then sit at
### 50 V for the whole pedestal run with nothing but the compliance to stop it.
case " $KEPT " in
    *" $BVMAX "*) ;;
    *) KEPT="$KEPT $BVMAX" ;;
esac
### 0 V always survives: it is the baseline every other reading is judged against.
LEAKV="${KEPT# }"
[ -n "$LEAKV" ] || LEAKV="0"

say() { printf '\n########## %s   [%s]\n' "$*" "$(date '+%H:%M:%S %Z')"; }

DRY="${MMTS_DRYRUN:-0}"
client() { "$ON_CLIENT" "$1"; }

### hv.sh is the single call site for hv.py, which is the only thing on the
### bench that talks to the Keithley. Stop and Destroy use the same script.
hv() { "$HERE/hv.sh" "$@"; }

### Nothing below may leave voltage on a cable someone is about to move, so the
### ramp to 0 and the output off are on the way out of this script as well:
### Stop pkills us, and the trap is what catches that.
### 🔑 HV_TOUCHED means "this run holds the Keithley". Only then may it touch
### the instrument: with several lanes, another lane's module can be biased.
HV_TOUCHED=0
hv_take() {    # hv_take WHAT: wait for the Keithley, announce who holds it
    hv_lock_acquire "$1" "$LOCK_TIMEOUT" say || return 1
    HV_TOUCHED=1
}
hv_give() {    # ramp down, open the relays, release the Keithley
    [ "$HV_TOUCHED" = "1" ] || return 0
    say "HV off (ramp to 0, output off)"
    hv off --limit-ua "$LIMIT_UA" || echo "WARNING: hv.py off failed -- check the Keithley by hand"
    if [ "$HVSWITCH" = "vitrek" ]; then
        python3 "$SWITCH" vitrek --clear || echo "WARNING: the Vitrek did not confirm all relays open"
    fi
    hv_lock_release
    # Once is enough: on a signal both the INT/TERM trap and the EXIT trap fire.
    HV_TOUCHED=0
}
hv_safe_off() { hv_give; }
trap 'hv_safe_off; exit 130' INT TERM
trap 'hv_safe_off' EXIT

# --- resolve every slot BEFORE touching hardware ----------------------------
FILLED=""
for S in $SLOTS; do
    case "$S" in A|B|C) ;; *) echo "MMTS_bench.slots holds '$S'; the mux board has A, B and C"; exit 2;; esac
    eval "raw=\${moduleID$S:-}"
    [ -n "$raw" ] || continue
    eval "override=\${powerBoard$S:-auto}"
    if ! out=$(python3 "$PARAMS" "$raw" --power "$override" --shell 2>&1); then
        echo "slot $S: $out"
        exit 2
    fi
    eval "$(echo "$out" | sed "s/^/${S}_/")"
    # The geometry's own ceiling, and the instrument's, whichever is lower.
    eval "bmax=\${${S}_MAXV}"
    [ "$bmax" -le "$HVMAX" ] || bmax="$HVMAX"
    for bv in $BVLIST; do
        [ "$bv" -le "$bmax" ] || {
            eval "d=\${${S}_DESC}"
            echo "slot $S ($d) takes at most ${bmax} V, and biasVOLTS asks for ${bv} V"
            exit 2; }
    done
    FILLED="$FILLED $S"
done
[ -n "$FILLED" ] || { echo "no slot has a module serial -- nothing to run"; exit 2; }

say "bias points:$BVLIST V   compliance ${LIMIT_UA} uA   leak check ${LEAKV} V, abort above ${MAXLEAK} uA"
say "HV routing: $HVSWITCH$([ "$HVSWITCH" = none ] && echo ' (one cable, moved by hand between slots)')"

# --- the instruments, once, and the Keithley's polarity in the banner ---------
# 🔑 hv.py idn resets the Keithley, so it runs only while nobody else holds it.
# A Keithley another lane is using is evidently there.
if hv_lock_held_by_other; then
    say "HV instrument: in use by $(hv_lock_owner | cut -d' ' -f1,2), skipping the check"
elif hv_lock_try idn; then
    say "HV instrument"
    hv idn --limit-ua "$LIMIT_UA"; irc=$?
    if [ $irc -eq 0 ] && [ "$HVSWITCH" = "vitrek" ]; then
        python3 "$SWITCH" vitrek --idn; irc=$?
    fi
    hv_lock_release
    [ $irc -eq 0 ] || { echo "an HV instrument did not answer -- not starting any bring-up"; exit 3; }
else
    say "HV instrument: just taken by $(hv_lock_owner | cut -d' ' -f1,2), skipping the check"
fi

# --- the registry, once, before any bring-up --------------------------------
REG=""
for S in $FILLED; do
    eval "REG=\"\$REG $S=\${${S}_SERIAL}\""
done
say "registering boards:$REG"
client "\"\$MM/register_boards.py\"$REG" \
    || { echo "register_boards.py failed -- not starting any bring-up"; exit 3; }

# --- nRUNS pedestals at one bias point, then the hexmaps --------------------
# ped_run.sh, not run_slot.sh: the slot is already up and gated, and a second
# bring-up here would re-roll a lottery that has already been won.
pedestals_at() {
    local S="$1" BV="$2" FAMILY="$3" BOARD="$4" EXT="$5"
    client "
    set -u
    slot=$S; fam=$FAMILY; board=$BOARD; ext='$EXT'; n=$NRUNS
    dut=Mux${S}_${LABEL}_BV${BV}; lab=${LABEL}_BV${BV}
"'
    serial=$("$MM/module_of.py" "$slot" 2>/dev/null)
    [ -n "$serial" ] || { echo "slot $slot is not in the registry"; exit 3; }
    PED_BASECFG="$SCRIPTS/configs/${fam}_mux${slot}_ped.yaml" PED_DUT="$dut" \
    PED_BOARD="$board" PED_EXTPOWER="$ext" \
        "$MM/ped_run.sh" "$slot" "$n" "$lab" 10000 || exit $?
    runs="$RESULTS_DIR/$serial/$dut/pedestal_run"
    echo "--- finder header positions (23 everywhere = right edge)"
    "$MM/finder_positions.sh" "$runs"/run_* 2>/dev/null | tail -3
    for d in "$runs"/run_*; do
        [ -f "$d/pedestal_run0.root" ] || continue
        echo "=== $(basename "$d")"
        # The bench venv (uproot3), as run_slot.sh gets it from lib.sh: a bare
        # python3 here is the GUI .venv, which has no uproot3 (2026-11-19).
        "${MMTS_VENV:-$MMTS_ROOT/venv}/bin/python3" "$MM/hexmap_robust.py" "$d" 2>&1 \
            | grep -E "adc_stdd|above|Error" || true
    done   # a grep with no match must not turn a good pedestal into "failed"
'
}

# --- the slots, one at a time -----------------------------------------------
RAN=0
POINTS=0
PREV=""
for S in $FILLED; do
    eval "FAMILY=\${${S}_FAMILY}; BOARD=\${${S}_BOARD}; NROCS=\${${S}_NROCS}"
    eval "POWERFLAG=\${${S}_POWERFLAG}; DESC=\${${S}_DESC}; SERIAL=\${${S}_SERIAL}"
    # run_slot.sh's POWER= (board fitted) is ped_run.sh's PED_EXTPOWER= (empty).
    EXT=1; [ -n "$POWERFLAG" ] && EXT=""

    # The cable reaches this slot before its bring-up, so the operator moves it
    # once per slot and not twice. The output is off either way.
    if [ "$HVSWITCH" = "none" ]; then
        hv_take "$S" || { say "$S: the Keithley never came free -- skipping this slot"; continue; }
        say "$S: HV routing to slot $S"
        python3 "$SWITCH" none --to "$S" ${PREV:+--from "$PREV"} --timeout "$PAUSE_TIMEOUT"
        srcw=$?
        PREV="$S"
        if [ $srcw -ne 0 ]; then
            say "$S: HV could not be routed (hv_switch exited $srcw) -- skipping this slot"
            hv_give
            continue
        fi
    fi

    say "$S: params $FAMILY $BOARD $NROCS ${POWERFLAG:---external-power}  ($SERIAL: $DESC)"

    # bring-up + gate + ONE pedestal at 0 V. $MM stays literal: the client exports it.
    say "$S: bring-up and gate (one pedestal at 0 V)"
    START=$(date +%s)
    cmd=$(printf '%s SKIP_PROBE=%s BRINGUP_TRIES=%s "$MM/run_slot.sh" %s %s %s %s %s 1' \
          "$POWERFLAG" "$SKIP_PROBE" "$TRIES" "$S" "$FAMILY" "$BOARD" "$NROCS" "${LABEL}_gate")
    client "$cmd"
    rc=$?
    case $rc in
        0) why="gate passed" ;;
        1) why="bring-up failed" ;;
        2) why="probe gave no summary" ;;
        3) why="registry or link map refused" ;;
        4) why="delay scan gate failed -- no HV applied" ;;
        5) why="first pedestal stalled -- no HV applied" ;;
        *) why="run_slot.sh exited $rc" ;;
    esac
    say "$S: EXIT $rc ($why)"
    [ $rc -eq 0 ] || { hv_give; continue; }
    archive_runs "$S" "Mux${S}_${LABEL}_gate" "$START"

    # --- the relay, after the gate, with the Keithley held ------------------
    if [ "$HVSWITCH" = "vitrek" ]; then
        hv_take "$S" || { say "$S: the Keithley never came free -- no HV for this slot"; continue; }
        say "$S: HV routing to slot $S (Vitrek)"
        python3 "$SWITCH" vitrek --to "$S"
        srcw=$?
        if [ $srcw -ne 0 ]; then
            say "$S: HV could not be routed (hv_switch exited $srcw) -- no HV for this slot"
            hv_give
            continue
        fi
    fi

    # --- HV, and only now ---------------------------------------------------
    say "$S: HV on, compliance ${LIMIT_UA} uA"
    hv on --limit-ua "$LIMIT_UA" || { say "$S: HV would not turn on -- skipping this slot"; hv_give; continue; }

    say "$S: leakage check at ${LEAKV} V"
    hv leakcheck $LEAKV --limit-ua "$LIMIT_UA" --abort-ua "$MAXLEAK" --settle "$SETTLE"
    lrc=$?
    if [ $lrc -eq 3 ]; then
        say "$S: LEAKAGE ABORT -- more than ${MAXLEAK} uA below 500 V, no pedestals"
        hv_give
        continue
    elif [ $lrc -ne 0 ]; then
        say "$S: leakage check failed (exit $lrc) -- no pedestals"
        hv_give
        continue
    fi

    SLOT_POINTS=0
    for BV in $BVLIST; do
        say "$S: BV ${BV} V -- ramp and read leakage"
        # captured for the archive, then printed for the progress parser
        out=$(hv set "$BV" --limit-ua "$LIMIT_UA" --settle "$SETTLE" 2>&1); src=$?
        printf '%s\n' "$out"
        [ $src -eq 0 ] || { say "$S: could not reach ${BV} V -- stopping this slot's scan"; break; }
        LEAK=$(printf '%s\n' "$out" | sed -n 's/^HV set ok .*"current_A": *\([-0-9.eE+]*\).*/\1/p' | tail -1)
        LEAK_UA=$([ -n "$LEAK" ] && python3 -c "print('%.6g' % (float('$LEAK') * 1e6))" 2>/dev/null)

        say "$S: pedestals 1..$NRUNS at BV${BV}"
        START=$(date +%s)
        pedestals_at "$S" "$BV" "$FAMILY" "$BOARD" "$EXT"
        prc=$?
        archive_runs "$S" "Mux${S}_${LABEL}_BV${BV}" "$START" "$LEAK_UA"
        if [ $prc -ne 0 ]; then
            say "$S: pedestals at BV${BV} failed (exit $prc) -- stopping this slot's scan"
            break
        fi
        SLOT_POINTS=$((SLOT_POINTS + 1))
        POINTS=$((POINTS + 1))

        if [ "$DRY" = "1" ]; then
            echo "[dry-run] collect: $COLLECT $S ${RUNTAG}_BV${BV} ${LABEL}_BV${BV}"
        else
            "$COLLECT" "$S" "${RUNTAG}_BV${BV}" "${LABEL}_BV${BV}"
        fi
    done

    say "$S: bias scan done, $SLOT_POINTS of $(echo $BVLIST | wc -w | tr -d ' ') points"
    hv_give
    [ $SLOT_POINTS -gt 0 ] && RAN=$((RAN + 1))
done

say "ALL SLOTS DONE -- $RAN of $(echo $FILLED | wc -w | tr -d ' ') slots produced pedestals, $POINTS bias point(s) in total"
[ "$RAN" -gt 0 ]
