# Plan: one pedestal page, up to 8 Krias in parallel, HV optional

Written 2026-10-14. Status: steps 1 to 6 implemented and unit tested the same day;
step 7 partly (bench scripts on pi0, a real Initialize of K1 on kria6, a puller
on 6101); no real multi-lane Run yet. Destroy is labeled **Clear** on the page.

## Goal

1. **One page instead of two.** The HV Pedestal Scan (task5) gets an **HV off**
   switch. With HV off it does exactly what the Multi Pedestal Run (task4) does:
   bring-up, gate, N pedestals, hexmaps, per slot. The task4 radio button goes
   away once the merged page has run on the bench.
2. **Up to 8 Krias at once.** The page shows up to 8 *lanes*, one per Kria. Each
   lane has its own address, its own slots A/B/C, its own Initialize, Configure,
   Run, Stop and Destroy, its own LEDs and its own log. Lanes run in parallel;
   inside a lane the slots still run one at a time (one i2c-server, one
   daq-server, one trigger claim per Kria).

## What stops two Krias from running side by side today

Everything below lives on the **client host** (pi0), which all lanes share.

| # | shared thing | where | what goes wrong with 2 lanes |
|---|---|---|---|
| 1 | daq-client port **6001** | `lib.sh`, `pedestal_run.py`/`delay_scan.py` default | the second puller cannot bind |
| 2 | `puller_restart` kills **every** `daq-client` by name | `lib.sh` | lane 2 starting a run kills lane 1's puller mid-pedestal |
| 3 | one `daq-client.log` | `lib.sh` | interleaved, unreadable |
| 4 | one registry `module_ids.json`, keyed by slot letter only | `module_of.py`, `register_boards.py` | lane 2 registering closes lane 1's windows; both lanes' slot A resolve to the same serial |
| 5 | temp configs `configs/_crc_<S>_<label>.yaml`, `<FAMILY>_mux<S>_probe12_auto.yaml` | `ped_run.sh`, `run_slot.sh` | two lanes with the same slot and label overwrite each other's config |
| 6 | the 12+12 probe writes the measured map into the shared `<FAMILY>_mux<S>_ped.yaml` | `ld5_writemap.py` | a probe in one lane changes the map another lane is reading |
| 7 | Stop: `pkill -f run_slot.sh`, `ped_run.sh`, ... and `pkill -f "make -f makefile_task5"` | makefiles, `app_task*.py` | stopping one Kria stops all of them |
| 8 | gallery staging `daqplots/<runTAG>/<S>-*.png`, preflight clears all of `daqplots/` | `collect_plots.sh`, `preflight.sh` | lanes overwrite each other's plots; one lane's Initialize wipes the others' |

Per-Kria things (`~/bu_<S>.log`, `~/daq-server.log`, `up_verified.sh`, the
Kria-side Stop) are already separate, and `pedestal_run.py`/`delay_scan.py` are
already stopped per Kria because their command line carries `-i $KRIA_IP`.

## Design

### Lanes in the config

```yaml
MMTS_bench:
  client_host: 'hep@pi0.physics.ucsb.edu'
  lanes:                       # up to 8; an empty kria is typed on the page
    - {kria: kria6.physics.ucsb.edu, puller_port: 6101}
    - {kria: '',                     puller_port: 6102}
    # ... to 6108
```

Ports 6101 to 6108: 6001 is the single-module stand's `daq-client.service` and
`makefile_initialize_this_GUI` reserves 6002 to 6025 for its per-module
services. Nothing on pi0 listens in 6000 to 6199 today.

### Bench scripts (Mac copy is the source of truth, rsync to pi0 after)

Every change is opt-in through an environment variable. **With the variables
unset the scripts behave exactly as today**, so hand-run commands and the
current pages keep working.

| file | change |
|---|---|
| `lib.sh` | `PULLER_PORT` (default 6001). Start `daq-client -p $PULLER_PORT`; `puller_alive`/`puller_restart` match only that port's daq-client (for 6001 also the bare legacy `daq-client`); wait for `:$PULLER_PORT`; log to `daq-client.log` for 6001, `daq-client-<port>.log` otherwise |
| `ped_run.sh`, `delay_scan.sh` | pass `--pullerPort "$PULLER_PORT"`; temp config name gets the port |
| `run_slot.sh` | probe config name gets the port |
| `module_of.py` | `registry_path()` honors `MMTS_REGISTRY`; `register_boards.py` and `hexmap_robust.py` already go through it |

A lane sets `PULLER_PORT=<its port>` and
`MMTS_REGISTRY=$RESULTS_DIR/module_ids_<kria host>.json`. The registry follows
the Kria, not the lane number, because the slots are physically on that Kria.
Result directories (`$RESULTS_DIR/<serial>/Mux<S>_<label>/...`) are already
unique per module and do not change.

### The 12+12 probe (item 6)

The probe is off by default and rarely used. Rule: a lane may run with the probe
only while **no other lane is running**, and no lane may start while a probe
lane runs. Enforced at Run.

### HV: one Keithley, one lane

There is one Keithley (the single-module stand's) and one HV cable. **At most one
lane may have HV on**; Configure refuses a second. The other lanes run with HV
off. The cable pause file becomes per lane. When the Vitrek arrives this is
still one instrument, so the rule stays.

HV off for a lane means `run_slots_hv.sh` skips the instrument, the leakage
check and the bias loop, and calls `run_slot.sh` with the full N pedestals
under `<label>`, exactly like `run_slots.sh` today. No `hv.sh` call at all, so a
lane with HV off never touches the Keithley, not even at Stop.

### Stop per lane (item 7)

- **GUI host**: each lane's make runs in its own process group
  (`start_new_session=True`); Stop kills that group, not every make.
- **Client host**: the lane's command runs under `setsid -w`, which writes its
  session id to `$MMTS_ROOT/gui_lanes/<lane>.sid`; Stop runs `pkill -s <sid>`.
  This catches `run_slot.sh`, `ped_run.sh`, `delay_scan.sh` and their children
  of that lane only. `pedestal_run.py`/`delay_scan.py` keep their existing
  `$KRIA_IP` match (GNU `timeout` moves them to their own process group, but
  not out of the session, so both catch them). The lane's puller is killed by
  port.
- **Kria**: unchanged, it is already per `$KRIA_IP`.
- **HV**: `hv.sh off` only in the lane that has HV on.

### Gallery (item 8)

Run tags get the lane name: `K1_run3`, `K1_run3_BV300`. The gallery
(`app_daqsummary.py`) needs no change, it lists directories. A lane's Initialize
clears only its own `daqplots/K<n>_*`.

### GUI

- `flask_apps/lanes.py`: a `Lane` class holding what is now module-global in
  `app_task5.py` (status, conf dict, slots, log, threads, stop flags, run index,
  pause file). Up to 8 instances.
- `app_task5.py` routes take a lane: `/task5/lane/<lane>/init`, `/submit`,
  `/run`, `/stop`, `/clear`, `/continue`; `/task5/status` returns all lanes at
  once. The "all" buttons are the page calling each eligible lane in parallel.
- `shared_state.server_status` becomes the aggregate, so the main page's job
  mode switch still only works when every lane is at startup or destroyed.
- Page: shared run options at the top (pedestals, tries, label, probe, bias
  list, leakage limit, HV routing); one card per lane below, with the Kria
  address, the port, an **HV on this Kria** radio, the three slot rows (LED,
  serial, power, stage, leakage) and a collapsible log.
- Main page: one radio, "Pedestal run (HV optional)". task4's radio stays until
  the merged page has run on the bench, then it goes.

### Tests (none touch the bench)

- Existing `test_ui_flow_hv.py`, `test_progress_hv.py`, `test_hv_cli.py` keep
  passing with one lane.
- New: two lanes under `MMTS_DRYRUN=1`: different ports and registries in the
  printed commands; Stop on lane 1 leaves lane 2 running; HV off issues no
  `hv.sh` call; a second HV lane is refused; Initialize on lane 1 leaves lane 2's
  gallery alone.
- Bench script change checked with `bash -n` and a dry run of `lib.sh` with and
  without `PULLER_PORT`.

## Before 8 lanes can run on the bench

- **Network.** pi0 must reach every Kria. Today it reaches kria6 only; the four
  new boards (10.0.1.36 to .39) are on the Mac's home LAN.
- **Disk.** pi0 `/data` is at 86 % (52 GB free). 8 Krias x 3 slots x N
  pedestals adds up; check the size of one HD Full pedestal run first.
- **CPU.** pi0 has 8 cores; 8 parallel pullers plus `unpack` and the hexmap
  scripts should fit, but the first multi-lane run should watch the load.

## Order of work

1. Bench scripts (`lib.sh`, `ped_run.sh`, `delay_scan.sh`, `run_slot.sh`,
   `module_of.py`), backward compatible, `.bak.20261014` copies.
2. HV off in `run_slots_hv.sh`, `makefile_task5`, the form.
3. `Lane` class and per-lane routes; one lane first, existing tests green.
4. Per-lane Stop, gallery prefix, preflight per port.
5. Page with lane cards; 8 lanes.
6. Two-lane dry-run tests.
7. rsync the bench scripts to pi0, one real lane on kria6, then two lanes as
   soon as a second Kria is reachable from pi0.
8. Retire the task4 radio, update `README.task5.md`.
