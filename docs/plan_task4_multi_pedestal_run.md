# Plan: the "Multi Pedestal Run" job (task4)

Drive the MMTS bench flow of `MMTS_MAC_INSTRUCTIONS.md` (three hexaboards on
one Kria through the mux board, measured one slot at a time) from this GUI, as
a fourth job mode next to Single Pedestal Run and IV Scan.

Written 2026-09-10 against `MultiModuleTeststandUI` at `c65bcc2`, the bench
scripts in `hexactrl-script/multimodule/` (`run_slot.sh` and friends), and a
read-only survey of pi0 and klong the same day.

## 1. What the job does

One press of Run does, for every slot that has a serial in the form, in order
A then B then C:

```
register_boards.py A=.. B=.. C=..                       once, before any bring-up
POWER=<..> SKIP_PROBE=1 run_slot.sh <S> <FAMILY> <BOARD> <NROCS> <label> <N>
    = bring-up -> gate -> N pedestals -> finder line -> hexmaps -> per-half check
collect the slot's hexmap PNGs into DataLoc/daqplots/<runTAG>/<S>-*.png
```

`run_slot.sh` already contains every rule the instructions insist on (kill the
stale i2c-server, up to 8 bring-ups, gate before pedestal, unit test one
pedestal before spending N, fresh puller per run). The GUI does not re-implement
any of it. The GUI's job is: collect the inputs, decide the four per-slot
parameters from the serial, run the slots **sequentially**, show progress per
slot, stop cleanly, and put the plots where the existing gallery finds them.

## 2. Topology, and the one seam that makes the klong move trivial

Three roles. They are on three machines during development and on one machine
in production:

| role | needs | dev (now) | production (later) |
|---|---|---|---|
| GUI host | this Flask app | the Mac | klong |
| client host | `daq-client`, `unpack`, ROOT, the `$MM` scripts, `$MMTS_ROOT/venv` | `hep@pi0` | klong |
| hexacontroller | bring-up, i2c-server, daq-server | kria11 | klong's Kria |

The Mac cannot be the client host: no hexactrl build, no ROOT, no uproot3.
So during dev every bench command goes **GUI host → ssh → client host**, and the
client host's own `~/.bashrc` (already set up on pi0, verified today:
`MMTS_ROOT=/data/mmts`, `KRIA_IP=kria11.physics.ucsb.edu`, `MM`, `SCRIPTS`,
`HEXACTRL`, `MMTS_VENV`, `RESULTS_DIR`, `GUI_HEXMAP`) supplies the site values
that `lib.sh` reads. Kria commands go through the client host too
(`ssh client 'ssh $KRIA_USER@$KRIA_IP ...'`), which is what the `$MM` scripts
do anyway.

**The seam.** One new config section and one wrapper script. Nothing else in
task4 knows whether the client host is remote.

`data/mmts_configurations.yaml` (and both `.default` templates):

```yaml
MMTS_bench:
  client_host: 'hep@pi0.physics.ucsb.edu'   # '' means: the GUI host is the client host (klong)
  client_shell: 'bash -lc'                  # login shell so ~/.bashrc exports apply
  slots: [A, B, C]
  default_nruns: 5
  default_label: ped
  skip_probe: 1
```

`scripts/task4_mmts_pedestal/on_client.sh`:

```bash
#!/usr/bin/env bash
# on_client.sh 'command' -- run a command on the client host, local or over ssh.
host=$(python3 scripts/access_yaml_var.py data/mmts_configurations.yaml MMTS_bench.client_host)
if [ -z "$host" ]; then bash -lc "$1"
else ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" "bash -lc $(printf %q "$1")"
fi
```

(`access_yaml_var.py` takes a top-level key today; teach it dotted paths.)

Moving to klong is then: set `client_host: ''`, set `DataLoc`, done. Do not
point `client_host` at klong before the thermal-cycle campaign there is over;
it is a production box mid-run.

## 3. Files

| add | what |
|---|---|
| `flask_apps/app_task4.py` | blueprint, cloned from `app_task1.py`; see 6 |
| `templates/index_task4.html` | cloned from `index_task1.html`; see 7 |
| `makefile_task4` | `initialize / run / stop / destroy`; see 5 |
| `scripts/task4_mmts_pedestal/on_client.sh` | the seam, above |
| `scripts/task4_mmts_pedestal/board_params.py` | serial → FAMILY, BOARD, NROCS, POWER; see 4 |
| `scripts/task4_mmts_pedestal/run_slots.sh` | register, then loop the slots through `run_slot.sh` |
| `scripts/task4_mmts_pedestal/collect_plots.sh` | newest run's PNGs per slot → `daqplots/<runTAG>/`, rsync back when remote |
| `scripts/task4_mmts_pedestal/preflight.sh` | the checks behind Initialize; see 5 |
| `README.task4.md` | how to run it by hand, same shape as `README.task3.md` |

| change | what |
|---|---|
| `app.py` | `import flask_apps.app_task4`, `register_blueprint(..., url_prefix='/task4')` |
| `templates/index_mainpage.html` | fourth radio "Multi Pedestal Run" (`task4`), fourth `TASK_URLS` entry, fourth branch in the iframe `src` |
| `flask_apps/shared_state.py` | `task4_slots = {}` and `task4_log = deque(maxlen=300)` |
| `flask_apps/app_daqsummary.py` | accept slot keys; see 8 |
| `data/mmts_configurations.yaml.default`, `data/mmts_configurations.ucsb.yaml.default` | the `MMTS_bench` section |
| `scripts/access_yaml_var.py` | dotted key path |

Leave task3 alone. It is a byte copy of task2 and unrelated; replacing it is a
separate cleanup.

## 4. Serial → bring-up parameters

Characters 5 and 6 of the serial decide everything (instructions 2.1). Table
from 2.1 and 11, plus the `POWER` spelling `run_slot.sh` wants:

| chars 5-6 | type | FAMILY | BOARD | NROCS | power distribution board | `POWER` env |
|---|---|---|---|---|---|---|
| LF | LD Full | `initLD-Full-3b` | `LD-Full` | 3 | fitted | `POWER=` (empty) |
| L5 | LD Five | `initLD-Five-3b` | `LD-Five` | 3 | none | unset (default `--external-power`) |
| LL, LR | LD Left, Right | `initLD-Left-3b` | `LD-Semi` | 2 | none | unset |
| LB, LT | LD Bottom, Top | `initLD-Bottom-3b` | `LD-Semi` | 2 | none | unset |
| HF | HD Full | `initHD-Full-trophyV3` | `HD-Full` | 6 | fitted | `POWER=` |
| HT | HD Top | `initHD-Top-trophyV3` | `HD-Top` | 3 | none | unset |
| HB | HD Bottom | `initHD-Bottom-trophyV3` | `HD-Bottom` | 4 | not yet seen, start as none | unset |
| HL, HR | HD Semi | `initHD-Semi-trophyV3` | `HD-Semi` | 2 | not yet seen, start as none | unset |

All eight config families exist in `hexactrl-script/configs/` as
`<FAMILY>_mux{A,B,C}_ped.yaml`. `board_params.py SERIAL` prints
`FAMILY BOARD NROCS POWERFLAG` and exits 2 on an unknown code, so the makefile
stops before touching hardware. It also gives the JS the same table (serve it
as JSON from the blueprint) so the form shows "LD Full, 3 ROCs, power board"
next to the field as the user types.

The form keeps a **power board override** (auto / fitted / none). Two reasons:
the HB and HL/HR rows are guesses, and the flag is the single most expensive
thing to get wrong (a module with the board fitted and the flag wrong is never
powered and reads `no ROCs`). Default auto.

Serial normalisation: uppercase, strip dashes, require `^320[A-Z0-9]{12}$`.
The `$MM` scripts, `module_ids.json` and `hexmap_robust.py` all read chars 5-6
positionally, so the dashed form the task1 validator allows must not reach
them.

## 5. `makefile_task4` and the state machine

The blueprint's Init / Configure / Run / Stop / Destroy states are unchanged
(`PythonTools/server_status.py`). What each target does:

**`initialize`** = `preflight.sh`, all through `on_client.sh`. Read-only, no
power, no bring-up. Fails loudly on the first problem, because every one of
these otherwise surfaces an hour later as a hung scan:

1. `echo $MMTS_ROOT $KRIA_IP; ls $MM/run_slot.sh; command -v daq-client unpack`
2. Kria reachable: `timeout 5 bash -c 'exec 3<>/dev/tcp/$KRIA_IP/22'`
   (the `exec 3<>` form; `cat < /dev/tcp` always returns 124)
3. **Port 6001 on the client host is free or ours.** `ss -ltnp | grep :6001`.
   If a process we do not own holds it, print who and stop. Never kill it.
   On pi0 that is `daq-client.service`, the single-module stand, root-owned,
   active today; `lib.sh`'s `puller_restart` cannot kill it and then fails to
   bind, and every scan reports `no summary.json`. The fix is the operator's:
   `sudo systemctl stop daq-client.service` for the session, `start` after.
4. **One puller per Kria.** `ssh kria 'ss -tn | grep 8888'` must show no
   established peer before we start. A stray one steals half the event stream
   and the symptom names nothing.
5. `rm -rf DataLoc/daqplots/*` as task1 does, so the result dropdown starts empty.

Not in initialize: `kconn_pwr on`. `up_verified.sh` defaults to `--recover`,
which cycles payload power itself (instructions 1.2). Not `findslot.py`
either; it is cheap but the instructions say not to repeat it, and Initialize
gets pressed more than once.

**`run`** = `run_slots.sh`, one process, sequential:

```
register_boards.py A=$moduleIDA B=$moduleIDB C=$moduleIDC   (only the filled ones)
for S in A B C:  [ -n "$moduleID$S" ] || continue
    read FAMILY BOARD NROCS POWERFLAG < board_params.py $moduleID$S   (or the override)
    echo "########## $S: params $FAMILY $BOARD $NROCS ${POWERFLAG:-external-power}"
    ${POWERFLAG} SKIP_PROBE=$skipPROBE $MM/run_slot.sh $S $FAMILY $BOARD $NROCS $label $nRUNS
    rc=$?   ->  echo "########## $S: EXIT $rc"   (1 bring-up, 2 probe, 3 registry/map, 4 gate, 5 stall, 0 ran)
    collect_plots.sh $S $runTAG
```

Sequential is not a preference. One Kria, one i2c-server, one daq-server, and
exactly one slot holds the trigger claim at a time (1.1). Do not reuse task1's
`$(addprefix pedestal_, ...)` pattern; with `-j` it runs slots in parallel.
Slot order is A, B, C; a later knob could reorder, since the trigger goes to
the first slot scanned.

One slot failing does not stop the others. Bring-up on one slot failing eight
times while the next comes up first try is normal on this bench.

**`stop`**. The blueprint's Stop already does `pkill make` on the GUI host, but
that only kills the local make; the ssh child and everything behind it keep
running. The target reaches through:

```
on_client.sh 'pkill -f "[r]un_slots.sh"; pkill -f "[r]un_slot.sh"; pkill -f "[p]ed_run.sh";
              pkill -f "[d]elay_scan.sh"; pkill -f "[p]edestal_run.py"; pkill -f "[d]elay_scan.py"'
on_client.sh 'ssh $KRIA_USER@$KRIA_IP "pkill -f \"[u]p_verified.sh\"; pkill -f \"[m]mts_bringup.sh\""'
```

Brackets everywhere, or `pkill -f` matches the ssh command line that carries
it and kills its own session. Not `daq-client`: on pi0 that is the other
stand's service. Nothing to restore afterwards; the instructions say a failed
or interrupted run needs daq-server and the puller restarted, and the next
bring-up does both.

**`destroy`** = stop, then `pkill -f "[z]mq_server"` and `sudo kconn_pwr off`
on the Kria, matching what task1's destroy does with `kconn_pwr off`.

`debug_mode` (`LOG_LEVEL=DEBUG`, port 5005) keeps the `make -n` dry-run trick
from `app_task2.py`. That is how the whole UI gets exercised on the Mac with no
hardware: every button prints the commands it would run.

## 6. `app_task4.py`

Clone `app_task1.py` (the four blueprints are near-identical; factoring a base
class is worth doing later, not now). Differences:

- `JOBMODE = 'task4'`, `makefile_task4`.
- `CONF_DICT`: `inspector`, `moduleSTATUS`, `moduleIDA`, `moduleIDB`,
  `moduleIDC`, `nRUNS` (default from config), `label`, `skipPROBE`,
  `powerBoardA/B/C` (auto/fitted/none). `moduleSTATUS` and `inspector` stay for
  parity with task1 and for a later DB upload; they are not used by the bench
  scripts.
- Validation: the serial regex of section 4 after normalisation; at least one
  slot filled; `nRUNS` 1..20.
- `ExecCMD('Run')` → `make -f makefile_task4 run runTAG=runN moduleIDA=.. nRUNS=.. label=.. skipPROBE=..`.
  No `-j`.
- **Progress parsing.** `run_command()` already reads the process line by line.
  Add a hook that updates `shared_state.task4_slots[S]` from the banner lines
  `run_slot.sh` prints, which are designed for this:

  | line | state |
  |---|---|
  | `########## A: params ...` | A: queued → starting |
  | `########## A: bring-up ...` | A: bring-up |
  | `bringup try 3 of 8: running` | A: bring-up, try 3 |
  | `READY  bringup=2 i2c=1` | A: up |
  | `########## A: gate` / `GATE: PASS` / `GATE FAILED` | A: gate / gated / gate-failed |
  | `########## A: pedestal 1 of 5` / `pedestals 2..5` | A: pedestal k/N |
  | `^1 +2347488 +1.000 ...` | one CRC row; count them |
  | `########## A: DONE` | A: done |
  | `########## A: EXIT 4` (ours) | A: failed, reason by code |

  Every line also goes into `shared_state.task4_log` (deque of 300).
- `/status` returns `{status, jobmode, DAQres, slots: task4_slots, log: last 40 lines}`.
  Poll interval 1 s is already what the page does; 40 lines per second is fine.

## 7. `index_task4.html`

Clone `index_task1.html`. Keep the five control buttons, the status text, the
inspector datalist, the module status radios, and the result dropdown plus
Check button. Replace the 1L..2R grid with three rows:

```
[LED] A  [ serial ............ ]  LD Full · 3 ROCs · power board   [power: auto ▾]   stage: bring-up try 2
[LED] B  [ serial ............ ]  (empty = skip)
[LED] C  [ serial ............ ]  LD Right · 2 ROCs · external      [power: auto ▾]   stage: pedestal 3/5
```

Below them one options row (runs N, label, skip probe checkbox), and a
`<pre>` log pane fed from `/status.log`, auto-scrolling. The LED buttons and
`function_LEDStatusMap.js` exist already; map queued gray, bring-up yellow,
running green, done green, failed red. Per-slot stage text comes from
`/status.slots`.

The board-type hint next to each field is filled client-side from the same
table as `board_params.py`, exposed at `/task4/board_table`.

## 8. Results and the gallery

Where the data lands, on the client host:

```
$RESULTS_DIR/<serial>/Mux<S>_<label>/pedestal_run/run_<UTC>/
    pedestal_run0.root, daq-server.log, initial_full_config.yaml,
    Mux<S>_adc_mean.png, Mux<S>_adc_stdd.png,
    Mux<S>_robust_adc_stdd.png, Mux<S>_stddOverRob_adc_stdd.png, Mux<S>_stddClipped_adc_stdd.png
```

(`hexmap_robust.py` labels the PNGs `Mux<S>` from the DUT directory name and
writes them beside the data; verified in `plot_summary.py` line 433.)

`app_daqsummary.py` expects `DataLoc/daqplots/<run>/<key>-adc_mean.png` and
`<key>-adc_stdd.png` with `key` in `1L..2R`. Two small changes make it serve
both stands:

- `FNAME_RE` key becomes `[1-9][LCR]|[ABC]`.
- `gallery()` derives the block list from the files present in the run
  directory rather than the hardcoded `BLOCKS`, falling back to `BLOCKS` when
  the directory is empty.

`collect_plots.sh S runTAG` copies the newest `run_*` PNGs of `Mux<S>_<label>`
to `daqplots/<runTAG>/<S>-adc_mean.png`, `<S>-adc_stdd.png`, and also
`<S>-robust.png` for later use, then, when `client_host` is remote,
`rsync -a client:daqplots/<runTAG>/ DataLoc/daqplots/<runTAG>/`. On klong the
rsync is skipped. The existing `/status` mtime check on `daqplots/` then
populates the dropdown exactly as for task1.

The `.root` files stay on the client host. Nothing in the GUI needs them.

## 9. Dev prerequisites, as found today

- pi0 has the full client stack and the bashrc exports. `Results/` and
  `module_ids.json` do not exist yet; `register_boards.py` creates both.
- **kria11 was unreachable from pi0** (`No route to host` on 22). It needs to
  be powered and on the network before anything past `preflight.sh` step 2
  can be tested.
- **6001 on pi0 is held by `daq-client.service`** (active). See initialize
  step 3. Stop it for a dev session with systemctl, never with pkill.
- The Mac has a `pi0` ssh entry and `Host *` keepalives (`ServerAliveInterval
  30`), which matters because one slot can spend ten minutes in bring-up
  inside a single ssh call. No `ControlMaster` is set; adding one for pi0
  (`ControlMaster auto`, `ControlPersist 10m`) removes a handshake from every
  `on_client.sh` call and is the instructions' own 0.3 advice.
- Port 5001 is free on pi0, so running the GUI itself on pi0 is possible if the
  ssh hop turns out to be a nuisance. The seam in section 2 makes that a
  config change too (`client_host: ''` on pi0).

## 10. Order of work, with what proves each step

1. **Seam.** `MMTS_bench` config, `access_yaml_var.py` dotted keys,
   `on_client.sh`. Proof: `on_client.sh 'echo $MMTS_ROOT; ls $MM/run_slot.sh'`
   from the Mac prints pi0's values.
2. **Preflight.** `preflight.sh` and the `initialize` target. Proof: it stops
   at step 3 on pi0 today, naming `daq-client.service`; after
   `systemctl stop` it passes to step 2 (or 4 once kria11 is up).
3. **Board table.** `board_params.py` with a unit test over the serials in
   instructions 2.1 (`320XLR4DQE00020` → LD-Semi 2 external, `320XLF4CQH00443`
   → LD-Full 3 power board, `320XHF4DPM02021` → HD-Full 6 power board) and an
   unknown code exiting 2.
4. **Runner.** `run_slots.sh`, `makefile_task4 run`, `collect_plots.sh`. Proof
   without hardware: `LOG_LEVEL=DEBUG` dry run prints the exact
   `run_slot.sh` lines. Proof with hardware: one LD Full in slot A from the
   Mac's shell, `make -f makefile_task4 run moduleIDA=... nRUNS=1`, ends in
   `########## A: DONE` and `daqplots/run1/A-adc_mean.png` exists on the Mac.
5. **Blueprint and page.** `app_task4.py`, `index_task4.html`, main page
   registration, `/status` with slots and log. Proof: dry-run mode drives all
   five buttons and the log pane shows the would-be commands; the LEDs follow
   a replayed `run_slot.sh` transcript fed through the parser (keep one from
   step 4 as a fixture).
6. **Gallery.** The two `app_daqsummary.py` changes. Proof: Check on `run1`
   shows the A block.
7. **Stop and destroy** against real hardware: press Stop mid bring-up, then
   `ssh kria 'pgrep -af up_verified'` is empty and `ss -ltn` on pi0 shows no
   `daq-client` of ours.
8. **klong**, only after its campaign: `client_host: ''`, `DataLoc`,
   `RESULTS_DIR` on klong, `MMTS.service` restart. Nothing else changes.

## 11. Rules the implementation must not lose

From the instructions and the bench notes; each has cost real time.

- Slots run one at a time. Never `-j`.
- `POWER=` empty for LF and HF; unset for every partial. Wrong flag = dead
  module reading `no ROCs`. Show the decision in the UI before Run.
- `SKIP_PROBE=1` by default. A 12+12 probe poisons the bring-up it runs in;
  `run_slot.sh` compensates with a second bring-up, which costs a bring-up.
- Never bring up under a live `zmq_server`; `up_verified.sh` kills it first,
  so always go through `run_slot.sh` or `up_verified.sh`, never
  `mmts_bringup.sh` from automation.
- Stop must reach the Kria (`up_verified.sh` loops for ten minutes on its own).
- `pkill -f` with the bracket trick, always.
- Never `pkill daq-client` on pi0. Never touch `daq-client.service` except via
  `systemctl`, and only the operator does that.
- One puller per Kria: check 8888 peers before starting.
- Stage banners and exit codes from `run_slot.sh` are the progress API. Do not
  parse deeper than that; the scripts change.
- `run_slot.sh` refuses without a registry entry, so `register_boards.py`
  runs first, every Run, before the first bring-up.
- The serial reaches the scripts in the 15-character dashless form.
- klong is production. `client_host` never points at it during dev.

## 12. Open questions

- **Database upload.** task1 writes pedestal results to `hgcdb` through
  Andrew's `InteractionGUI.CLI.py`. The `$MM` path writes nothing to any DB.
  Is a DB entry per slot wanted for the multi run, and if so which table and
  fields? Not in this plan's scope; `inspector` and `moduleSTATUS` are kept in
  the form so it can be added without a UI change.
- **Trigger slot order.** The first slot scanned holds the trigger claim.
  A, B, C is assumed; say if the bench wants otherwise.
- **HD Bottom and HD Semi** power distribution board: unknown, table says
  none. The override covers it until measured.
- **Where the GUI runs during dev.** Mac with an ssh hop (assumed) or on pi0
  itself. The seam supports both; the hop is the assumption in section 10.
