# Plan: finishing task4 on the day the Kria is available

Companion to `plan_task4_multi_pedestal_run.md`, which is built and tested.
This one covers only what could not be proven without a hexacontroller, in the
order to do it, with the proof for each step.

Written 2026-09-10. Everything in sections 1 to 6, 8 and 9 of the build plan is
done; `README.task4.md` documents the result. What is left is its step 10.4
(runner with hardware), 10.7 (Stop and Destroy against hardware), and the parts
of the preflight and the plot collection that need a live bench.

## 0. The one rule for the day

**Prove the bench by hand first, then hand the same slot to the GUI.** Debugging
new GUI code against an unverified bench makes every GUI bug look like a bench
bug and every bench bug look like a GUI bug, and this bench has enough of the
latter to burn the whole session. Section 2 is the hand run. Do not skip it
even if the bench came up cleanly last time.

Budget roughly two hours. A bring-up is 60 to 90 s per try and gets up to 8
tries, the gate is 20 to 40 s, and each pedestal is about 30 s.

## 1. Before touching the GUI

Site values as they stand on the client host today, all confirmed present:

| | |
|---|---|
| GUI host | the Mac, `python3 app.py`, port 5001 |
| client host | `hep@pi0.physics.ucsb.edu`, `MMTS_ROOT=/data/mmts` |
| hexacontroller | `daq@kria11.physics.ucsb.edu` |
| `$MM` | `/data/mmts/hexactrl-sw/hexactrl-script/multimodule` |
| `RESULTS_DIR` | `/data/mmts/Results` (does not exist yet; `register_boards.py` creates it) |
| `MMTS_FW` | `multimodule-hd-tester-trophy-v3` |
| configs | all eight families present, none carrying the `linklink` bug |

kria11 is the box formerly called kria4, so `hostname` on it still answers
`kria-4`. That is the right machine, not a stale entry.

- [ ] **1.1 Power the Kria and confirm it answers.**
      `ssh daq@kria11.physics.ucsb.edu 'hostname; uptime'` from pi0.
- [ ] **1.2 Confirm the Kria has the bring-up scripts.**
      `ssh daq@kria11... 'ls ~/up_verified.sh ~/multimodule/; command -v fw-loader kconn_pwr'`.
      `up_verified.sh` missing is the one thing that stops everything, and it is
      what `run_slot.sh` calls.
- [ ] **1.3 Confirm passwordless sudo for the two tools.** `ssh daq@kria11...
      'sudo -n kconn_pwr --help >/dev/null && echo ok'`. Destroy needs this; if
      it is missing, instructions 0.4 has the sudoers line.
- [ ] **1.4 Free port 6001 on pi0.** This is the operator step and it is not
      optional:
      ```
      ssh hep@pi0 'sudo systemctl stop daq-client.service'
      ```
      **Write yourself a note to restart it.** Section 7 is that note. Never
      `pkill` it.
- [ ] **1.5 Know which modules are in which slot, and their serials.** The
      registry is written from what you type, and a wrong entry files the whole
      session under the wrong module.

## 2. A hand run, one slot, no GUI

Pick the simplest board available, ideally an LD Full, and use slot A.

- [ ] **2.1** From pi0:
      ```
      "$MM/register_boards.py" A=<serial>
      POWER= SKIP_PROBE=1 "$MM/run_slot.sh" A initLD-Full-3b LD-Full 3 ped 1
      ```
      (`POWER=` only for LF and HF. Every partial takes no prefix at all.)
- [ ] **2.2 Proof:** it ends in `########## A: DONE` and a pedestal row with a
      six figure entry count.

If this fails, the day is a bench day and the GUI work waits. Nothing below is
worth doing against a bench that cannot run a slot by hand.

## 3. Preflight, the two steps that were never exercised

Steps 1, 3 and 5 are already proven. Steps 2 and 4 have never run against a
live Kria.

- [ ] **3.1** From the Mac: `bash scripts/task4_mmts_pedestal/preflight.sh`
- [ ] **3.2 Proof:** all five steps pass and it prints `PREFLIGHT OK`.
- [ ] **3.3 Prove step 4 actually bites.** Start a second puller somewhere
      (`ssh hep@pi0 'setsid /opt/hexactrl/ROCv3-alper-dev/bin/daq-client &'` in a
      throwaway shell), re-run preflight, and confirm it refuses and names the
      peer. Then kill that one puller. A check that has never failed is a check
      that might not work.

Step 4 is the one most likely to misbehave: it greps `ss -tn | grep 8888` on the
Kria for `ESTAB`. If it reports a peer that is our own daq-client and refuses a
healthy bench, relax it to ignore the client host's own address rather than
deleting the check.

## 4. The runner, from the shell, one slot

Still no GUI. This tests `on_client.sh`, `run_slots.sh`, the parameter table and
`collect_plots.sh` against real output.

- [ ] **4.1** From the Mac, capturing everything:
      ```
      make -f makefile_task4 run moduleIDA=<serial> nRUNS=1 runTAG=run1 \
          2>&1 | tee /tmp/task4_slotA_real.txt
      ```
- [ ] **4.2 Proof:** ends in `########## A: EXIT 0`, and
      `DAQresults/daqplots/run1/A-adc_mean.png` exists **on the Mac**. That last
      part proves the staging and the rsync back, which have only ever run
      against fabricated files.
- [ ] **4.3 Check the real PNG names.** `collect_plots.sh` expects
      `Mux<S>_adc_mean.png`, `Mux<S>_adc_stdd.png` and
      `Mux<S>_robust_adc_stdd.png` beside the data. That came from reading
      `plot_summary.py`, not from a real run. If the names differ, the fix is
      three `cp` lines:
      ```
      ssh hep@pi0 'ls /data/mmts/Results/<serial>/MuxA_ped/pedestal_run/run_*/*.png'
      ```
- [ ] **4.4 Confirm the registry was created properly.**
      `ssh hep@pi0 'cat /data/mmts/Results/module_ids.json'` should hold one open
      window per slot you named.

## 5. The transcript, and the progress parser

This is the highest value step of the day and it costs nothing extra, because
step 4 already captured what it needs.

The fixture the parser is tested against
(`scripts/task4_mmts_pedestal/fixtures/run_slots_transcript.txt`) was written by
hand from the instructions. Real output may differ in spacing, case or wording,
and the LEDs are driven entirely by matching it.

- [ ] **5.1** Replace the fixture with the real capture from 4.1, trimming it to
      one representative slot plus the surrounding `params`/`EXIT` banners.
- [ ] **5.2** `python3 scripts/task4_mmts_pedestal/test_progress_parser.py`
- [ ] **5.3** Fix whatever regexes the real output breaks, in `parse_progress()`
      in `flask_apps/app_task4.py`. The expected state sequence is
      `starting, bringup, up, gate, gated, pedestal, done`.
- [ ] **5.4** Keep both fixtures if the real one is short: a failure case (gate
      failed, or a stall) is worth as much as a clean one.

## 6. The GUI, for real

- [ ] **6.1** Start it on the Mac, select Multi Pedestal Run, press Initialize.
      Expect `initialized`, not `error`.
- [ ] **6.2** Fill slot A only, Configure, and read the popup. It must name the
      board type, the ROC count and the power decision. **Check the power column
      before pressing Run.**
- [ ] **6.3** Press Run and **watch the page while the slot runs.** This is the
      real test of step 5:
      - the A LED goes yellow within a few seconds
      - the stage line tracks `bring-up try N of 8`, then `gate passed`, then
        `pedestal k/N`
      - the log pane scrolls in step with the run, not in one lump at the end
- [ ] **6.4** If the log arrives only at the end, output is being block
      buffered somewhere in the chain. The lines the LEDs need are all `bash
      printf`, which should not buffer, so suspect the ssh hop first: try
      `ssh -tt` in `on_client.sh`, and if that fixes it keep it only for the
      run path, since `-tt` also changes how signals reach the far side.
- [ ] **6.5 Proof:** the run ends, the slot LED is green, the DAQ Result
      dropdown gains its run tag, and Check shows a Slot A panel with two
      hexmaps.

## 7. Three slots, then Stop and Destroy

- [ ] **7.1** All three slots, `nRUNS=5`. Proof: they run strictly one after
      another, A then B then C, never overlapping, and each slot's LED tells its
      own story. A slot failing bring-up while the others pass is the expected
      shape, not a bug.
- [ ] **7.2 Stop, mid bring-up.** Press Run, wait until a slot is inside
      `bring-up try 2`, press Stop. Proof:
      ```
      ssh hep@pi0 'pgrep -af "[r]un_slot|[p]ed_run"'                  # empty
      ssh hep@pi0 'ssh daq@kria11... "pgrep -af \"[u]p_verified\""'   # empty
      ssh hep@pi0 'systemctl is-active daq-client.service'            # untouched
      ```
      The last line is the one that matters most. Stop must never reach the
      single-module stand.
- [ ] **7.3 Stop, mid pedestal.** Same, while a pedestal is running. Confirm
      pi0 still has its four single-module `pedestal_run.py` processes, if they
      are still there that day:
      `ssh hep@pi0 'pgrep -af "[p]edestal_run.py"'`. Ours carry
      `-i kria11...`, theirs carry `-i kria9...`, and only ours should be gone.
- [ ] **7.4 Destroy.** Proof: `ssh daq@kria11... 'pgrep -af "[z]mq_server"'` is
      empty and the bench supply reads its quiescent current. Note that `0 A` is
      the correct reading once payload power is off, and also before any
      bring-up has run.
- [ ] **7.5** After a Stop or a Destroy, the next Run must work without any
      manual repair. That is the claim `README.task4.md` makes, and it is the
      one worth confirming before trusting the button.

## 8. Leaving pi0 as you found it

Do this before you walk away, every time.

- [ ] **8.1** `ssh hep@pi0 'sudo systemctl start daq-client.service'`
- [ ] **8.2** Confirm: `ssh hep@pi0 'systemctl is-active daq-client.service; ss -ltn | grep :6001'`
- [ ] **8.3** Confirm nothing of ours is left:
      `ssh hep@pi0 'pgrep -af "[r]un_slot|[u]p_verified|[p]ed_run"'` is empty.
- [ ] **8.4** The single-module stand shares `pedestal_run.py` and port 6001
      with us. If its runs were mid campaign when you started, check they are
      still there or that the operator knows they were stopped.

## 9. What each failure would mean

| symptom | where to look |
|---|---|
| Preflight stops at step 1 | the client host's `~/.bashrc`, instructions 0.5 |
| Preflight stops at step 2 | the Kria is off or off the network |
| Preflight stops at step 3 | `daq-client.service` is running; 1.4 |
| Preflight stops at step 4 | a stray puller holds the Kria stream; find it, do not ignore it |
| `no ROCs` on every slot | the power flag, or the loopback, or `EN_Mx` cabling; instructions 2.3 and 11 |
| Bring-up fails 8 times on one slot, others fine | the bench, not the GUI. Hand run that slot per section 2 |
| Banners appear but no LED moves | the parser, section 5 |
| Log arrives in one lump at the end | buffering, 6.4 |
| Plots run but the gallery is empty | `collect_plots.sh` and the real PNG names, 4.3 |
| `EXIT 3` on the first slot | `register_boards.py` did not run or the serial is not in the registry |

## 10. What does not change

These were settled during the build and should not be revisited at the bench.

- Slots run one at a time. Never `-j`.
- `SKIP_PROBE=1` stays the default. A 12+12 probe poisons the bring-up it runs
  in, and `run_slot.sh` pays for a second bring-up to recover.
- `POWER=` empty for LF and HF only.
- `pkill` patterns for `pedestal_run.py` and `delay_scan.py` stay scoped with
  `$KRIA_IP`. Both stands share those script names on pi0.
- Never `pkill daq-client`, never touch `daq-client.service` except with
  `systemctl`, and only the operator does that.
- `client_host` never points at klong while its campaign is running.

## 11. Still open after this

- Database upload per slot: task1 writes to `hgcdb`, the `$MM` path writes to no
  database. The form keeps `inspector` and `moduleSTATUS` so this can be added
  without a UI change.
- Slot order: A, B, C is assumed, and the first slot scanned holds the trigger
  claim. Say so if the bench wants a different order.
- HD Bottom and HD Semi power distribution board: unmeasured, the table assumes
  none, and the per-slot override covers it. Measure it when one of those boards
  is on the bench and update the table in `board_params.py`.
- The klong move, which the build plan defers until its thermal-cycle campaign
  is over.
