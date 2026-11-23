#!/usr/bin/env python3
"""Replay recorded runs through a lane's progress parser.

The stage banners, the run_slot.sh exit codes and hv.py's "HV <cmd> ok {...}"
lines ARE the progress API. When a bench script changes what it prints, this is
what says so, instead of the page quietly going blank mid-run.

Two transcripts: a run with HV on (task5's), and one with HV off, which is
exactly the Multi Pedestal Run (task4's).

    source .venv/bin/activate && source ./init_bash_vars.sh
    python3 scripts/task5_hv/test_progress_hv.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from flask_apps.lanes import Lane  # noqa: E402

fails = 0


def check(what, got, want):
    global fails
    if got != want:
        print("FAIL %-40s got %r want %r" % (what, got, want))
        fails += 1


def replay(lane, path):
    lane.reset_progress()
    current = None
    with open(path) as f:
        for line in f:
            current = lane.parse_progress(line, current)
    return lane.slots


# --- HV on --------------------------------------------------------------------
hv = Lane("K1", "kria-test", 6101)
hv.use_hv = True
slots = replay(hv, os.path.join(HERE, "fixtures", "run_slots_hv_transcript.txt"))
check("both slots seen", sorted(slots), ["A", "C"])

a = slots["A"]
check("A finished", a["state"], "done")
check("A led", a["led"], "green")
check("A counted both bias points", a["points"], 2)
check("A ended at the last bias point", a["bv"], 300)
### The leakage at the bias point is the number this job exists to record.
check("A leakage in uA", a["leak_uA"], 0.084)
check("A pedestals counted", a["peds"], 2)
check("A gate exit kept", a["exit"], "0")

c = slots["C"]
check("C failed", c["state"], "failed")
check("C led", c["led"], "red")
check("C says why", c["stage"], "gate failed, no HV applied")
### 🔑 A slot that never passed the gate must never have been given voltage.
check("C never reached a bias point", c["bv"], None)
check("C has no leakage reading", c["leak_uA"], None)

### A leakage abort is its own state: red, and it names the reason.
hv.reset_progress()
cur = None
for line in ["########## A: leakage check at 0 1 10 100 300 V   [09:00:00 PDT]",
             "########## A: LEAKAGE ABORT -- more than 1 uA below 500 V, no pedestals   [09:01:00 PDT]"]:
    cur = hv.parse_progress(line, cur)
check("leak abort state", hv.slots["A"]["state"], "leak-abort")
check("leak abort led", hv.slots["A"]["led"], "red")

### An hv.py failure turns the slot red without waiting for a banner.
hv.reset_progress()
cur = hv.parse_progress("########## A: BV 300 V -- ramp and read leakage   [09:00:00 PDT]", None)
cur = hv.parse_progress('HV set fail {"error": "Output must be on to change output voltage"}', cur)
check("hv fail state", hv.slots["A"]["state"], "failed")
check("hv fail stage", hv.slots["A"]["stage"], "HV set failed")

# --- HV off: the Multi Pedestal Run's own transcript --------------------------
off = Lane("K2", "kria-test-2", 6102)
slots = replay(off, os.path.join(ROOT, "scripts", "task4_mmts_pedestal", "fixtures",
                                 "run_slots_transcript.txt"))
A, B, C = slots["A"], slots["B"], slots["C"]
### exit 0 without HV means the pedestals ran, not "gate passed"
check("off: A exit", A["exit"], "0")
check("off: A state", A["state"], "done")
check("off: A led", A["led"], "green")
check("off: A pedestals", A["peds"], 5)
check("off: A nruns", A["nruns"], 5)
check("off: B state", B["state"], "failed")
check("off: B exit", B["exit"], "4")
check("off: B stage", B["stage"], "gate failed, pedestals not spent")
check("off: C state", C["state"], "failed")
check("off: C exit", C["exit"], "5")

### lanes do not share progress
check("lanes are separate", sorted(hv.slots), ["A"])
check("log bounded", off.log.maxlen, 2000)

print("progress hv: %d failures" % fails)
sys.exit(1 if fails else 0)
