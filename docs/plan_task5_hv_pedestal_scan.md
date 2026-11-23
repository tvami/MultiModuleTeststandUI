# Plan: pedestals versus bias voltage on the multi-module bench (task5)

Run the multi-module pedestal job at several bias voltages per slot, the way
the single-module stand on pi0 does it, and put the plots where the gallery
finds them.

Written 2026-09-16 against `MultiModuleTeststandUI` (task4 as of today), the
bench scripts in `hexactrl-script/multimodule/`, and a read-only look at the
single-module stand on pi0 (`/home/hep/hgcal-module-testing-gui`, commit
`347a379`, plus one local edit to `KeithleyPowerSupply.py`).

## 1. How the single-module stand does it today

Everything HV lives in Andrew's GUI package, not in `pedestal_run.py`.

**Hardware.** One Keithley 2410 on RS232, `HVResource: ASRL/dev/ttyUSB0::INSTR`
(a Prolific USB-serial adapter, the only serial device on pi0), front
terminal, `HVWiresPolarization: Reverse`, `HasHVSwitch: false` (that flag is
a box interlock, not a channel switch), `HasRHSensor: false`.

**Driver.** `KeithleyPowerSupply.py`, pyvisa over serial. pi0's copy differs
from upstream only in the port settings: 9600 8N1 with `\r` termination,
labelled "UCSB HighBay". Reuse that copy, not upstream. The API the GUI uses:

| call | what |
|---|---|
| `outputOn()` / `outputOff()` | output, with the interlock if configured |
| `setVoltage(v)` | ramps in 25 V steps every 0.5 s when the change is larger than a step, then sets |
| `measureCurrentLoop()` | returns `(_, current, _)`, the leakage read after each run |
| `set_current_limit(i)` | compliance |
| `get_output()` | state |

Polarity: `BV_to_use = -BV` when `HVWiresPolarization == 'Forward'`, else
`+BV`. On pi0 it is `Reverse`, so the Keithley is driven to `+BV`.

**Per pedestal run** (`InteractionGUI.CLI.run_pedestals`): output on if
needed, `setVoltage`, launch `pedestal_run.py` through `ExternalPC` with the
usual `-i host -f config -o DataLoc/<module> -d <dut> -I --pullerPort`, wait,
then `measureCurrentLoop()` into `state['-Leakage-Current-']`, then rename the
run directory with `_BV{int}_RH{rh}_T{t}_{untrimmed|trimmed300}`, upload to the
DB, make hexmaps. **`pedestal_run.py` never sees the bias voltage.** BV is the
Keithley plus a name.

**The standard sequence** (`TestingGUIBase`): leakage check at
`[0, 1, 10, 100, 300]` V (abort offered if any reading below 500 V is above
1 µA), untrimmed pedestals at `[1, 10, 100, 300, min(maxV, 800)]`, trim at
300 V (`pedestal_run` + `pedestal_scan` + `vrefnoinv_scan` + `vrefinv_scan`,
whose yaml outputs are merged into the working config), then the same list
again tagged `trimmed300`, and a leakage estimate via `inputdac_scan` if the
leakage exceeds 2 µA. The run directory listing on pi0 shows exactly that
shape: `BV300 untrimmed`, then `BV1, BV10, BV100, BV300, BV500 trimmed300`.

RH and T in the tag come from the DB or a sensor; with `HasRHSensor: false`
they are typed in.

## 2. What the MMTS GUI already has

- `MMTS_hardwares.keithley` and `MMTS_hardwares.vitek` in
  `data/mmts_configurations.yaml`, and `scripts/initialize_rs232_devices.py`
  to check both.
- Task2/task3 "IV scan": `scripts/turn_on_HV_switch.py` drives a **Vitrek
  964i** relay switch (`JobModule/device_VITREK_switch_control.py`, `reset()`,
  `set_relay_state(n, "ON")`), one channel per tray position from an
  `MMTS_channel_config.<pos>.HVchannel` map, and `IVscan.run.sh` does
  *switch on channel → getIV.py (pymeasure Keithley2400, ramps) → switch reset*.
  That is the production MMTS pattern at klong: one Keithley, many modules,
  the switch always changed with the Keithley at 0 V.
- Task4: one slot at a time, `run_slot.sh` puts the label into the DUT
  directory (`Mux<S>_<label>`), `hexmap_robust.py` titles the maps from it,
  `collect_plots.sh` keys the gallery on the slot letter.

So the bias loop is a wrapper around what exists, not new DAQ.

## 3. Hardware for the mux bench, and one decision

The instrument is a Keithley (confirmed). Two ways to reach three modules,
both through the same code behind one config switch, `hv.switch: none|vitrek`:

| | `none` | `vitrek` |
|---|---|---|
| cabling | one HV cable, moved by hand | three cables into a Vitrek 964i, channels A/B/C |
| between slots | job ramps to 0, output off, **pauses and asks** for the cable move, then continues | job ramps to 0, output off, switches the relay, continues |
| new hardware | none | the Vitrek plus a second serial adapter |
| good for | this bench, three modules | klong |

**Decided 2026-09-16: `none` now, the Vitrek later.** The pause is one line
and the Vitrek path is the existing `turn_on_HV_switch.py` with `A/B/C`
entries added to the map.

**Decided 2026-09-16: the bench uses pi0's existing Keithley on `ttyUSB0`.**
That is the single-module stand's instrument and pi0's only serial adapter.
Consequences: the two stands cannot run HV at the same time; the HV cable
moves between benches by hand, exactly as it will move between slots; and
the resource stays `ASRL/dev/ttyUSB0::INSTR` on the understanding that pi0
is not rebooted. If it ever is, check `/dev/serial/by-id/` before the next
HV run, since the numbering can swap and `README.task3.md` already asks for
by-id names for that reason.

**No box, no interlock.** The single-module stand only enables HV with the
box closed. The mux bench has open HV leads. Cover them, and keep the
compliance low (10 µA is plenty for leakage) so a short trips before it hurts.

**Bare boards.** The three HD Fulls on the bench have no sensor. HV on them
does nothing and the leakage reads ~0 A. The job must run to completion in
that state, since that is how it gets debugged.

## 4. What the job does

One press of Run, for every slot with a serial, A then B then C:

```
register once
for S in A B C:
    bring-up, gate                                    (task4, unchanged)
    HV: output on, leakage check at 0/1/10/100/300 V  (abort the slot on > 1 µA below 500 V)
    for BV in list:                                   default 1 10 100 300 500
        setVoltage(BV), settle 5 s, read leakage
        N pedestals, label ped_BV<BV>                 -> Mux<S>_ped_BV<BV>/
        finder line, hexmaps, collect into daqplots/<runTAG>_BV<BV>/<S>-*.png
    setVoltage(0), output off
    (switch: none)   pause "move the HV cable to slot <next>", wait for OK
    (switch: vitrek) relay reset, relay <next> on
```

Rules carried over from task4: one slot at a time, never `-j`, `SKIP_PROBE=1`,
`BRINGUP_TRIES` from the form, Stop must reach pi0 and the Kria **and the
Keithley** (ramp to 0, output off) before killing anything.

HV is applied only after the gate passes, so a slot that never comes up never
gets voltage, and voltage is always removed before `power_down_others` runs
for the next slot.

## 5. Files

| add | what |
|---|---|
| `$MM/hv.py` (pi0; Mac mirror `multimodule/hexactrl-sw/hexactrl-script/multimodule/hv.py`) | thin CLI over `KeithleyPowerSupply` (pi0's copy): `idn`, `on`, `off`, `set V`, `read`, `zero`, `leakcheck`. Runs from the GUI directory so it inherits the single-module stand's `configuration.yaml` (resource, terminal, polarity): one source of truth for the instrument. Compliance 50 µA by default (`--limit-ua`), set before output on; the single-module GUI never sets one and runs at the driver default of 1.05 mA. Ends every command with one `HV <cmd> ok|abort|fail {json}` line for the runner. `test_hv.py` beside it, fake driver, 13 checks |
| `scripts/task5_hv/hv_switch.py` | `none` (prints the pause prompt) or `vitrek` (wraps `turn_on_HV_switch.py` with the A/B/C map) |
| `scripts/task5_hv/run_slots_hv.sh` | task4's `run_slots.sh` with the BV loop and the HV calls around it |
| `scripts/task5_hv/test_hv_cli.py` | unit test against a fake pyvisa resource: polarity, ramp steps, zero-before-off |
| `flask_apps/app_task5.py`, `templates/index_task5.html`, `makefile_task5` | clone of task4 plus `biasVOLTS` (comma list, each 0..800), `hvSwitch` (none/vitrek), `maxLEAK` µA, and a Continue button for the cable pause |

| change | what |
|---|---|
| `data/mmts_configurations.yaml` (+ both `.default`) | `MMTS_hardwares.keithley`: by-id `Resource`, `CurrentLimit: 10e-6`, `MaxVoltage: 800`; `hv: {switch: none, settle_s: 5, leak_check_volts: [0,1,10,100,300], leak_abort_uA: 1}`; `MMTS_channel_config` gains `A/B/C` with `HVchannel` for the vitrek case |
| `hexmap_robust.py` (pi0, `$MM`) | keep a `BV\d+` token from the DUT name in the title, next to the serial |
| `app_daqsummary.py` | nothing: `<runTAG>_BV300` is just another run directory in the dropdown |
| `README.task5.md` | how to run it by hand |

`pedestal_run.py`, `run_slot.sh`, `ped_run.sh`, `up_verified.sh` are not
touched. The label already carries the tag.

## 6. Order of work, with what proves each step

1. **Keithley alone, no module.** `hv.py idn` prints the instrument;
   `hv.py set 10`, `hv.py read`, `hv.py zero`, `hv.py off` from the Mac through
   `on_client.sh`. Proof: the front panel follows, and `test_hv_cli.py` passes
   against the fake.
2. **Dry run.** `MMTS_DRYRUN=1 make -f makefile_task5 run moduleIDA=... biasVOLTS=0,300`
   prints, in order: bring-up, `hv.py on`, `leakcheck`, `set 0`, pedestals
   labelled `ped_BV0`, `set 300`, pedestals `ped_BV300`, `zero`, `off`, the
   cable prompt.
3. **One bare board, real bench.** Slot A, `biasVOLTS=0,300`, `nRUNS=1`.
   Proof: two run directories `MuxA_ped_BV0`, `MuxA_ped_BV300`, leakage ~0 A
   logged for each, gallery has `run1_BV0` and `run1_BV300` with the serial
   and the BV in the titles.
4. **All three slots** with the cable pause. Proof: the job waits at the
   prompt with the Keithley at 0 V and output off, and continues on Continue.
5. **A real module**, leakage check meaningful, `biasVOLTS=1,10,100,300,500`.
   Proof: the leakage numbers match a single-module IV of the same module.
6. **Vitrek**, only if the bench gets one. Proof: step 4 with no prompt.
7. **Trim at 300 V** (phase 2): `pedestal_scan`, `vrefnoinv_scan`,
   `vrefinv_scan` through the mux path with the mux config, their yaml
   outputs merged into a per-module trimmed config, then the list again as
   `trimmed300`. Same scripts exist in `ROCv3-alper-dev/ctrl`; the work is the
   config merge, which `ExternalPC.outyaml` shows how the single-module stand
   does.
8. **DB upload** (phase 3): out of scope, like task4.

## 7. Rules the implementation must not lose

- HV only after the gate, HV to 0 and output off before the next slot, before
  Stop kills anything, and in Destroy. A killed job must not leave 500 V on a
  cable someone is about to move.
- Never switch the Vitrek with the output on.
- Ramp is the driver's 25 V / 0.5 s; do not shortcut it.
- Compliance set before output on, every time.
- Polarity is a config value, read once, and printed in the run banner.
- The bench Keithley IS the single-module stand's Keithley. Never run HV on
  both stands at once, and never start an HV job without knowing which bench
  the cable is on.
- `hv.py` is the only thing that talks to the Keithley; nothing else imports
  the driver.
- Stage banners and exit codes stay the progress API.

## 8. Open questions

- **Model.** It is the single-module stand's Keithley 2410; `hv.py idn`
  confirms it on first use.
- **Polarity: settled, same as the single-module stand,** `Reverse`. The MMTS
  config currently says `Forward` and must be changed to `Reverse` before the
  first run; `hv.py` prints the polarity in its banner.
- **Vitrek or cable pause: settled, cable pause.**
- **Max voltage per board type** (600 for LD, 800 for HD in the single-module
  defaults). Take it from the serial the same way the power flag is.
- **RH and T** for the tag: typed into the form, or omitted on the bench.
- **DB upload** of pedestals per BV, as for task4.
