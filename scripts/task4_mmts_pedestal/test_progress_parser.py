#!/usr/bin/env python3
"""Replay a recorded run_slots.sh transcript through app_task4's progress parser.

The stage banners and exit codes are the progress API. This test is what catches
a bench script changing its banners under the GUI.

    source .venv/bin/activate && source ./init_bash_vars.sh
    python3 scripts/task4_mmts_pedestal/test_progress_parser.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import flask_apps.app_task4 as t4          # noqa: E402
import flask_apps.shared_state as state    # noqa: E402

FIXTURE = os.path.join(ROOT, "scripts", "task4_mmts_pedestal",
                       "fixtures", "run_slots_transcript.txt")

fails = 0


def check(what, got, want):
    global fails
    if got != want:
        print("FAIL %-28s got %r want %r" % (what, got, want))
        fails += 1


# --- the states a slot passes through, in order ----------------------------
t4.reset_progress()
cur = None
seen_A = []
with open(FIXTURE) as f:
    for line in f:
        cur = t4.parse_progress(line, cur)
        a = state.task4_slots.get("A")
        if a and (not seen_A or seen_A[-1] != a["state"]):
            seen_A.append(a["state"])

check("slot A state sequence", seen_A,
      ["starting", "bringup", "up", "gate", "gated", "pedestal", "done"])

A, B, C = (state.task4_slots[s] for s in "ABC")

check("A exit",      A["exit"], "0")
check("A led",       A["led"], "green")
check("A pedestals", A["peds"], 5)
check("A nruns",     A["nruns"], 5)
check("A params",    A["params"],
      "initLD-Full-3b LD-Full 3 POWER=  (320XLF4CQH00443: LD Full, 3 ROCs, power board)")

check("B state",     B["state"], "failed")
check("B exit",      B["exit"], "4")
check("B led",       B["led"], "red")
check("B stage",     B["stage"], "gate failed, pedestals not spent")
check("B pedestals", B["peds"], 0)

check("C state",     C["state"], "failed")
check("C exit",      C["exit"], "5")
check("C led",       C["led"], "red")

# a bring-up try updates the stage text, and it is the try line, not a banner,
# that carries the count
t4.reset_progress()
cur = t4.parse_progress("########## B: bring-up (LD-Semi, 2 ROCs)   [18:15:05 CDT]", None)
check("banner sets current", cur, "B")
t4.parse_progress("bringup try 3 of 8: running (60 to 90 s) ...", cur)
check("try text", state.task4_slots["B"]["stage"], "bring-up try 3 of 8")

# the log keeps the tail, bounded
check("log bounded", state.task4_log.maxlen, 300)

print("progress parser: %d failures" % fails)
sys.exit(1 if fails else 0)
