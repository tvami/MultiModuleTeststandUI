#!/usr/bin/env bash
# collect_plots.sh SLOT RUNTAG [LABEL] -- the slot's newest hexmaps into the gallery.
#
# run_slot.sh leaves its maps beside the data on the CLIENT host:
#
#   $RESULTS_DIR/<serial>/Mux<S>_<label>/pedestal_run/run_<UTC>/Mux<S>_adc_mean.png
#
# app_daqsummary.py serves DataLoc/daqplots/<runTAG>/<key>-adc_mean.png on the GUI
# host, with key here being the slot letter. So: pick the newest run that has
# maps, rename them to the gallery's shape in a staging directory on the client,
# and pull that directory back. When the GUI host is the client host the pull is
# a local copy.
#
# Missing plots are not an error: a slot whose bring-up failed has none, and the
# other slots' results should still reach the gallery.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ON_CLIENT="$HERE/on_client.sh"
CONF="${MMTS_CONF:-$ROOT/data/mmts_configurations.yaml}"

SLOT="${1:?slot A|B|C}"
RUNTAG="${2:?run tag, e.g. run1}"
LABEL="${3:-ped}"

# These three are interpolated into a command that runs on the client host.
case "$SLOT"   in A|B|C) ;; *) echo "collect_plots: slot must be A, B or C"; exit 2;; esac
case "$RUNTAG" in *[!A-Za-z0-9_-]*|'') echo "collect_plots: bad run tag"; exit 2;; esac
case "$LABEL"  in *[!A-Za-z0-9_-]*|'') echo "collect_plots: bad label";   exit 2;; esac

CLIENT="$(python3 "$ROOT/scripts/access_yaml_var.py" "$CONF" MMTS_bench.client_host)"
DATALOC="$(python3 "$ROOT/scripts/access_yaml_var.py" \
            "${AndrewModuleTestingGUI_BASE:-$ROOT/external_packages/hgcal-module-testing-gui}/configuration.yaml" DataLoc)"
[ -n "$DATALOC" ] || { echo "collect_plots: DataLoc is not set"; exit 1; }
DEST="$DATALOC/daqplots/$RUNTAG"

# Stage on the client under $MMTS_ROOT/daqplots/<runTAG>/, named as the gallery
# wants them. The three maps are the two the gallery shows plus the robust width,
# which is the one to compare boards on (adc_stdd is quantised).
staged=$("$ON_CLIENT" "
    set -u
    slot=$SLOT; label=$LABEL; runtag=$RUNTAG
"'
    serial=$("$MM/module_of.py" "$slot" 2>/dev/null)
    [ -n "$serial" ] || { echo "collect_plots: slot $slot is not in the registry, no plots"; exit 0; }
    runs="$RESULTS_DIR/$serial/Mux${slot}_${label}/pedestal_run"
    # newest run directory that actually holds maps
    src=""
    for d in $(ls -1d "$runs"/run_* 2>/dev/null | sort -r); do
        [ -f "$d/Mux${slot}_adc_mean.png" ] && { src="$d"; break; }
    done
    [ -n "$src" ] || { echo "collect_plots: no hexmaps under $runs"; exit 0; }
    out="$MMTS_ROOT/daqplots/$runtag"
    mkdir -p "$out"
    # Clear this slot first: run tags restart at run1 when the GUI restarts, so
    # a leftover map from an earlier session would be pulled back as this run.
    # (No apostrophes in here: the whole block is one single-quoted string.)
    rm -f "$out/${slot}-"*.png "$out/${slot}.serial"
    cp -f "$src/Mux${slot}_adc_mean.png"         "$out/${slot}-adc_mean.png"
    cp -f "$src/Mux${slot}_adc_stdd.png"         "$out/${slot}-adc_stdd.png"
    cp -f "$src/Mux${slot}_robust_adc_stdd.png"  "$out/${slot}-robust.png" 2>/dev/null
    # The gallery keys on the slot letter, so the serial cannot live in the file
    # name. Carry it alongside instead; the block heading reads it back.
    printf %s "$serial" > "$out/${slot}.serial"
    echo "collect_plots: STAGED slot $slot <- $(basename "$src")  ($serial)"
' 2>&1) || true
echo "$staged"
### Nothing staged is the normal outcome for a slot whose bring-up failed, and
### it is not worth an rsync or an error.
case "$staged" in *"collect_plots: STAGED"*) ;; *) exit 0;; esac

mkdir -p "$DEST"
if [ -z "$CLIENT" ]; then
    CLIENT_ROOT="$("$ON_CLIENT" 'echo "$MMTS_ROOT"')"
    cp -f "$CLIENT_ROOT/daqplots/$RUNTAG"/"$SLOT"-*.png "$DEST/" 2>/dev/null
    cp -f "$CLIENT_ROOT/daqplots/$RUNTAG/$SLOT.serial"  "$DEST/" 2>/dev/null
else
    # A remote MMTS_ROOT has to be resolved on the far side before rsync sees it.
    CLIENT_ROOT="$("$ON_CLIENT" 'echo "$MMTS_ROOT"')"
    rsync -a --include="$SLOT-*.png" --include="$SLOT.serial" --exclude='*' \
        "$CLIENT:$CLIENT_ROOT/daqplots/$RUNTAG/" "$DEST/" 2>/dev/null \
        || echo "collect_plots: rsync of slot $SLOT plots failed (run kept on the client)"
fi
ls -1 "$DEST" 2>/dev/null | sed "s/^/collect_plots: gallery has /"
