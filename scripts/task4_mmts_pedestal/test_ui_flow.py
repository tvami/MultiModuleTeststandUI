#!/usr/bin/env python3
"""Drive all five task4 buttons with no bench attached.

MMTS_DRYRUN=1 makes on_client.sh print every bench command instead of running
it, so this exercises the real path: the makefile, run_slots.sh, the parameter
table, the banners and the progress parser. Nothing reaches the client host or
the Kria.

    source .venv/bin/activate && source ./init_bash_vars.sh
    python3 scripts/task4_mmts_pedestal/test_ui_flow.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["MMTS_DRYRUN"] = "1"

import app as mainapp                      # noqa: E402
import flask_apps.shared_state as state    # noqa: E402

mainapp.app.config["WTF_CSRF_ENABLED"] = False
c = mainapp.app.test_client()

fails = 0


def check(what, got, want):
    global fails
    if got != want:
        print("FAIL %-30s got %r want %r" % (what, got, want))
        fails += 1


def wait_for(*statuses, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        s = c.get("/task4/status").get_json()["status"]
        if s in statuses:
            return s
        time.sleep(0.2)
    return c.get("/task4/status").get_json()["status"]


c.post("/set_option", json={"option": "task4"})
check("jobmode", state.jobmode, "task4")

# --- Initialize -------------------------------------------------------------
c.post("/task4/init", json={})
check("after Init", wait_for("initialized", "error"), "initialized")

# --- Configure --------------------------------------------------------------
form = {
    "inspector": "tester", "moduleSTATUS": "Untaped",
    "moduleIDA": "320-XLF-4CQ-H00443",     # dashed on the way in
    "moduleIDB": "",
    "moduleIDC": "320XLR4DQE00020",
    "powerBoardA": "auto", "powerBoardB": "auto", "powerBoardC": "auto",
    "nRUNS": 2, "label": "ped", "skipPROBE": "1", "bringupTRIES": 8,
}
r = c.post("/task4/submit", json=form)
check("Configure status", r.status_code, 200)
body = r.get_json()
check("Configure ok", body.get("status"), "success")
if "320XLF4CQH00443   LD Full, 3 ROCs, power board" not in body.get("message", ""):
    print("FAIL Configure message does not show the power decision:\n%s" % body.get("message"))
    fails += 1
check("serial normalised", state and mainapp.app_task4.CONF_DICT["moduleIDA"], "320XLF4CQH00443")
check("empty slot stays empty", mainapp.app_task4.CONF_DICT["moduleIDB"], "")

# a bad serial must be refused, and must not change the stored configuration
bad = dict(form, moduleIDC="320XZZ4DQE00020")
r = c.post("/task4/submit", json=bad)
check("bad type code refused", r.status_code, 400)
check("nRUNS out of range refused",
      c.post("/task4/submit", json=dict(form, nRUNS=99)).status_code, 400)
check("no slots refused",
      c.post("/task4/submit", json=dict(form, moduleIDA="", moduleIDC="")).status_code, 400)

# put a good configuration back
c.post("/task4/submit", json=form)
check("status after Configure", state.server_status, "configured")

# --- Run --------------------------------------------------------------------
c.post("/task4/run", json={})
check("after Run", wait_for("idle", "error", timeout=90), "idle")

st = c.get("/task4/status").get_json()
slots = st["slots"]
check("slots reported", sorted(slots), ["A", "C"])
check("A finished", slots["A"]["state"], "done")
check("C finished", slots["C"]["state"], "done")
check("A led", slots["A"]["led"], "green")

log = "\n".join(st["log"])
for want in ('"$MM/register_boards.py" A=320XLF4CQH00443 C=320XLR4DQE00020',
             'POWER= SKIP_PROBE=1 BRINGUP_TRIES=8 "$MM/run_slot.sh" A initLD-Full-3b LD-Full 3 ped 2',
             ' SKIP_PROBE=1 BRINGUP_TRIES=8 "$MM/run_slot.sh" C initLD-Left-3b LD-Semi 2 ped 2'):
    if want not in log:
        print("FAIL log is missing: %s" % want)
        fails += 1
# A partial must NOT get the power-board flag: with it, the 0x27 EN_Mx write
# runs against a board that has no power distribution board.
cline = [l for l in st["log"] if '"$MM/run_slot.sh" C' in l]
check("slot C command found", len(cline), 1)
if cline and "POWER=" in cline[0]:
    print("FAIL slot C (a partial) was given the power-board flag: %s" % cline[0])
    fails += 1

# --- Stop and Destroy -------------------------------------------------------
c.post("/task4/stop", json={})
check("after Stop", state.server_status, "stopped")
c.post("/task4/destroy", json={})
check("after Destroy", state.server_status, "destroyed")

# --- Kria picked on the page ------------------------------------------------
# A shell metacharacter must be refused before anything runs.
r = c.post("/task4/init", json={"kria": "kria11; rm -rf ~"})
check("bad Kria refused", r.status_code, 400)
check("status after bad Kria", state.server_status, "destroyed")

c.post("/task4/init", json={"kria": "kria-test.example.org"})
check("Init with a Kria", wait_for("initialized", "error"), "initialized")
st = c.get("/task4/status").get_json()
check("Kria reported", st["kria"], "kria-test.example.org")
if "export KRIA_IP=kria-test.example.org;" not in "\n".join(st["log"]):
    print("FAIL the picked Kria did not reach the client command")
    fails += 1
c.post("/task4/destroy", json={})

print("ui flow: %d failures" % fails)
sys.exit(1 if fails else 0)
