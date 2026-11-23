#!/usr/bin/env bash
# preflight_hv.sh -- task4's read-only bench checks, plus the Keithley.
#
# No power, no bring-up, no bias. `hv.py idn` does construct the driver, which
# resets the instrument and leaves the output OFF at 0 V, so it is safe here and
# it is the only way to know before Run that the cable, the adapter and the
# instrument are all there.
#
# Exit 0 = safe to run. Any other exit = do not press Run.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

bash "$ROOT/scripts/task4_mmts_pedestal/preflight.sh" || exit $?

printf '\n---------- preflight HV: the Keithley answers, and its polarity\n'
### 🔑 This is the single-module stand's own instrument on its own serial
### adapter. Two stands cannot hold HV at once, and the cable has to be on THIS
### bench: idn proves the instrument, not where the cable goes.
### The configured compliance, not hv.py's default: two different numbers in
### one session is exactly what sends someone hunting an imaginary problem.
LIMIT_A="$(python3 "$ROOT/scripts/access_yaml_var.py" \
            "${MMTS_CONF:-$ROOT/data/mmts_configurations.yaml}" \
            MMTS_hardwares.keithley.CurrentLimit)"
LIMIT_UA="$(python3 -c "print(float('${LIMIT_A:-1.05e-3}') * 1e6)")"
"$HERE/hv.sh" idn --limit-ua "$LIMIT_UA" || {
    printf '\nPREFLIGHT FAILED: the Keithley did not answer.\n'
    printf '  - is the single-module stand holding it (its GUI open)?\n'
    printf '  - is the serial adapter still /dev/ttyUSB0 (check /dev/serial/by-id)?\n'
    exit 1; }

### A pause file left by a killed run would release the next one immediately.
python3 "$HERE/hv_switch.py" none --clear >/dev/null

printf '\nPREFLIGHT OK -- the bench and the Keithley are ready for Run\n'
printf 'Check which bench the HV cable is on before pressing Run.\n'
