#!/usr/bin/env python3
"""Drive the pedestal page with two Krias at once, no bench, no Keithley.

MMTS_DRYRUN=1 makes on_client.sh print every bench command instead of running
it, so this exercises the real path: the makefile, run_slots_hv.sh and
run_slots.sh, the parameter table, the banners and the progress parser.
Nothing reaches the client host, a Kria or the Keithley.

K1 runs with HV on and K2 with HV off, side by side. What must never drift:

  - HV order on K1: gate before HV, leakcheck before any bias point, the
    output off before Stop kills anything
  - K2 never calls hv.py, not in Run, Stop or Clear
  - each lane reaches the bench with its own Kria, puller port and registry
  - one lane's Stop and Clear leave the other lane alone
  - one HV lane at a time; the 12+12 probe runs alone

    source .venv/bin/activate && source ./init_bash_vars.sh
    python3 scripts/task5_hv/test_ui_flow_hv.py
"""
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["MMTS_DRYRUN"] = "1"
### private locks, so a test never waits on (or frees) a real run's Keithley
import tempfile  # noqa: E402
_tmp = tempfile.mkdtemp()
os.environ["MMTS_HV_SESSION_LOCK"] = os.path.join(_tmp, "hv_session.lock")
os.environ["MMTS_HV_LOCK"] = os.path.join(_tmp, "hv.lock")
os.environ["MMTS_VITREK_LOCK"] = os.path.join(_tmp, "vitrek.lock")

import app as mainapp                      # noqa: E402
import flask_apps.shared_state as state    # noqa: E402
from flask_apps.lanes import Lane          # noqa: E402

t5 = mainapp.app_task5
mainapp.app.config["WTF_CSRF_ENABLED"] = False
c = mainapp.app.test_client()

fails = 0


def check(what, got, want):
    global fails
    if got != want:
        print("FAIL %-45s got %r want %r" % (what, got, want))
        fails += 1


def lane(name):
    return t5.BY_NAME[name]


def status(name):
    for d in c.get("/task5/status").get_json()["lanes"]:
        if d["name"] == name:
            return d
    return {}


def wait_for(name, *statuses, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        if lane(name).status in statuses:
            return lane(name).status
        time.sleep(0.2)
    return lane(name).status


def post(name, action, payload=None):
    return c.post(f"/task5/lane/{name}/{action}", json=payload or {})


def since(name, fragment):
    """The lane's log from the last line containing fragment on."""
    lines = list(lane(name).log)
    at = max([i for i, l in enumerate(lines) if fragment in l] or [0])
    return lines[at:]


check("8 lanes", [l.name for l in t5.LANES], [f"K{i}" for i in range(1, 9)])
check("ports 6101..6108", [l.port for l in t5.LANES], list(range(6101, 6109)))

c.post("/set_option", json={"option": "task5"})
check("jobmode", state.jobmode, "task5")

# --- Initialize ---------------------------------------------------------------
check("Init K1", post("K1", "init", {"kria": "kria-test-1.example.org"}).status_code, 204)
check("Init K2", post("K2", "init", {"kria": "kria-test-2.example.org"}).status_code, 204)
### one puller per Kria: a second lane on the same Kria is refused
check("same Kria on two lanes refused",
      post("K3", "init", {"kria": "kria-test-1.example.org"}).status_code, 409)
check("bad host refused", post("K4", "init", {"kria": "kria;rm -rf"}).status_code, 400)
check("empty host refused", post("K4", "init", {"kria": ""}).status_code, 400)
check("K1 initialized", wait_for("K1", "initialized", "error"), "initialized")
check("K2 initialized", wait_for("K2", "initialized", "error"), "initialized")
check("K3 untouched", lane("K3").status, "startup")
### a lane's Initialize clears only its own gallery runs
k1init = "\n".join(lane("K1").log)
check("Init clears only K1's plots", "daqplots/K1_*" in k1init, True)
check("Init K1 uses port 6101", "port 6101 on the client host" in k1init, True)

# --- Configure ------------------------------------------------------------------
form = {
    "inspector": "tester", "moduleSTATUS": "Untaped",
    "moduleIDA": "320-MHF-2TD-SB0193",     # dashed on the way in, HD Full
    "moduleIDB": "",
    "moduleIDC": "320XLR4DQE00020",        # LD Right, a partial: caps at 600 V
    "powerBoardA": "auto", "powerBoardB": "auto", "powerBoardC": "auto",
    "nRUNS": 2, "label": "ped", "skipPROBE": "1", "bringupTRIES": 8,
    "biasVOLTS": "0,300", "hvSwitch": "none", "maxLEAK": 1, "useHV": "1",
}
r = post("K1", "submit", form)
check("Configure K1", r.status_code, 200)
msg = (r.get_json() or {}).get("message", "")
for want in ("320MHF2TDSB0193   HD Full, 6 ROCs, power board", "HV ON: bias points 0, 300 V"):
    if want not in msg:
        print("FAIL K1 Configure message is missing %r:\n%s" % (want, msg))
        fails += 1
check("serial normalised", lane("K1").conf["moduleIDA"], "320MHF2TDSB0193")
check("bias list stored", lane("K1").conf["biasVOLTS"], "0,300")
check("K1 has HV", lane("K1").use_hv, True)

# refusals on the HV lane, none of which may change the stored configuration
check("bad type code refused", post("K1", "submit", dict(form, moduleIDC="320XZZ4DQE00020")).status_code, 400)
check("bias above the instrument refused", post("K1", "submit", dict(form, biasVOLTS="0,900")).status_code, 400)
check("bias above an LD geometry refused", post("K1", "submit", dict(form, biasVOLTS="0,700")).status_code, 400)
check("bias above an LD allowed without the LD",
      post("K1", "submit", dict(form, moduleIDC="", biasVOLTS="0,700")).status_code, 200)
check("empty bias refused with HV", post("K1", "submit", dict(form, biasVOLTS="")).status_code, 400)
check("bad switch refused", post("K1", "submit", dict(form, hvSwitch="relay")).status_code, 400)
check("no slots refused", post("K1", "submit", dict(form, moduleIDA="", moduleIDC="")).status_code, 400)
check("good K1 configuration back", post("K1", "submit", form).status_code, 200)

### one Keithley: a second HV lane is refused
r = post("K2", "submit", dict(form, useHV="1", moduleIDA="320MHF2TDSB0192", moduleIDC=""))
check("second HV lane refused", r.status_code, 400)
check("refusal names the HV lane", "K1 already has HV on" in json.dumps(r.get_json()), True)

### HV off: no bias list needed, and the Keithley settings are ignored
form2 = dict(form, moduleIDA="320MHF2TDSB0192", moduleIDC="", useHV="0", biasVOLTS="")
r = post("K2", "submit", form2)
check("Configure K2 (HV off)", r.status_code, 200)
check("K2 message says HV off", "HV off: the Keithley is not touched" in (r.get_json() or {}).get("message", ""), True)
check("K2 has no HV", lane("K2").use_hv, False)

# --- the 12+12 probe runs alone -------------------------------------------------
lane("K1").status = "running"
probe_form = dict(form2, skipPROBE="0")
check("K2 probe configured", post("K2", "submit", probe_form).status_code, 200)
check("probe refused while K1 runs", post("K2", "run").status_code, 409)
lane("K1").status = "configured"
check("K2 probe off again", post("K2", "submit", form2).status_code, 200)

# --- Run both at once -------------------------------------------------------------
check("Run K1", post("K1", "run").status_code, 204)
check("Run K2", post("K2", "run").status_code, 204)
check("aggregate while running", state.server_status in ("running", "idle"), True)
check("K1 idle", wait_for("K1", "idle", "error", timeout=180), "idle")
check("K2 idle", wait_for("K2", "idle", "error", timeout=180), "idle")

st1 = status("K1")
check("K1 slots", sorted(st1["slots"]), ["A", "C"])
check("K1 nothing left paused", st1["paused"], None)
import re  # noqa: E402
check("K1 run tag", bool(re.fullmatch(r"K1_\d{8}_\d{6}", st1["runtag"])), True)
check("K2 slots", sorted(status("K2")["slots"]), ["A"])
check("K2 slot A done", status("K2")["slots"]["A"]["state"], "done")

l1 = list(lane("K1").log)
log1 = "\n".join(l1)
log2 = "\n".join(lane("K2").log)


def index_of(lines, fragment, after=0):
    for i in range(after, len(lines)):
        if fragment in lines[i]:
            return i
    return -1


# The order is the safety property: gate, then HV, then bias, then off.
for want in ('"$MM/register_boards.py" A=320MHF2TDSB0193 C=320XLR4DQE00020',
             '"$MM/run_slot.sh" A initHD-Full-trophyV3 HD-Full 6 ped_gate 1',
             '"$MM/hv.py" on --limit-ua', '"$MM/hv.py" leakcheck 0 1 10 100 300',
             '"$MM/hv.py" set 0 --limit-ua', '"$MM/hv.py" set 300 --limit-ua',
             '"$MM/ped_run.sh"', '"$MM/hv.py" off'):
    if want not in log1:
        print("FAIL K1 log is missing: %s" % want)
        fails += 1
i_gate = index_of(l1, '"$MM/run_slot.sh" A ')
i_on = index_of(l1, '"$MM/hv.py" on', max(i_gate, 0))
i_leak = index_of(l1, '"$MM/hv.py" leakcheck', max(i_on, 0))
i_set = index_of(l1, '"$MM/hv.py" set 0', max(i_leak, 0))
i_ped = index_of(l1, '"$MM/ped_run.sh"', max(i_set, 0))
check("gate runs before HV goes on", i_gate >= 0 and i_on > i_gate, True)
check("leakcheck runs before any bias point", i_leak >= 0 and i_set > i_leak, True)
check("pedestals run after the bias is set", i_ped >= 0 and i_ped > i_set, True)

### 🔑 power flags: HD Full gets POWER=, the LD Right does not
aline = [l for l in l1 if '"$MM/run_slot.sh" A ' in l]
check("K1 slot A command found", len(aline), 1)
if aline and "; POWER= " not in aline[0]:
    print("FAIL slot A (power board fitted) did not get the power flag: %s" % aline[0])
    fails += 1
cline = [l for l in l1 if '"$MM/run_slot.sh" C ' in l]
if cline and "POWER=" in cline[0]:
    print("FAIL slot C (a partial) was given the power-board flag: %s" % cline[0])
    fails += 1

### each lane reaches the bench as itself
for what, log, want in (
        ("K1 Kria", log1, "export KRIA_IP=kria-test-1.example.org"),
        ("K1 port", log1, "export PULLER_PORT=6101"),
        ("K1 registry", log1, "module_ids_kria-test-1.example.org.json"),
        ("K1 tracked", log1, "client (lane K1, tracked)"),
        ("K2 Kria", log2, "export KRIA_IP=kria-test-2.example.org"),
        ("K2 port", log2, "export PULLER_PORT=6102"),
        ("K2 registry", log2, "module_ids_kria-test-2.example.org.json"),
        ("K2 full pedestals", log2, '"$MM/run_slot.sh" A initHD-Full-trophyV3 HD-Full 6 ped 2'),
        ("K2 gallery", log2, "collect_plots.sh A %s ped" % lane("K2").runtag),
        ("K2 archive", log2, "[dry-run] archive: slot A MuxA_ped"),
        ("K1 archive", log1, "[dry-run] archive: slot A MuxA_ped_BV300"),
        ("K1 gallery", log1, "_BV300 ped_BV300")):
    check(what, want in log, True)
check("K2 never calls hv.py", "hv.py" in log2, False)
check("K1 never sees K2's Kria", "kria-test-2" in log1, False)

# --- the cable pause, per lane ----------------------------------------------------
check("Continue with nothing waiting", post("K1", "continue").status_code, 400)
os.makedirs(lane("K1").pause_dir, exist_ok=True)
with open(lane("K1").pause_file, "w") as f:
    json.dump({"prompt": "MOVE THE HV CABLE to slot B", "from": "A", "to": "B"}, f)
check("K1 shows the pause", (status("K1")["paused"] or {}).get("to"), "B")
check("K2 does not", status("K2")["paused"], None)
check("Continue accepted", post("K1", "continue").status_code, 200)
check("Continue file written", os.path.exists(lane("K1").continue_file), True)
lane("K1").clear_pause()

# --- Stop: per lane ---------------------------------------------------------------
post("K1", "stop")
check("K1 stopped", lane("K1").status, "stopped")
check("K2 untouched by K1's Stop", lane("K2").status, "idle")
slog = "\n".join(since("K1", "makefile_task5 stop"))
check("Stop ramps the HV down", '"$MM/hv.py" off' in slog, True)
i_hv, i_kill = slog.find('"$MM/hv.py" off'), slog.find("pkill")
check("HV comes down before anything is killed", i_hv >= 0 and i_kill > i_hv, True)
check("K1 Stop ends only its own session", "gui_lanes/$MMTS_LANE.sid" in slog and "pkill -s" in slog, True)
check("K1 Stop has no by-name run_slot kill", "[r]un_slot.sh" in slog, False)

post("K2", "stop")
s2 = "\n".join(since("K2", "makefile_task5 stop"))
check("K2 Stop does not touch the Keithley", "hv.py" in s2, False)
check("K2 Stop scoped to its Kria", "export KRIA_IP=kria-test-2.example.org" in s2, True)

# --- Clear: per lane ----------------------------------------------------------------
post("K2", "clear")
check("K2 cleared", lane("K2").status, "destroyed")
check("K1 untouched by K2's Clear", lane("K1").status, "stopped")
d2 = "\n".join(since("K2", "makefile_task5 destroy"))
check("Clear drops payload power", "kconn_pwr off" in d2, True)
check("Clear K2 ends its own puller", '[d]aq-client -p $p' in d2, True)
check("Clear K2 does not touch the Keithley", "hv.py" in d2, False)

post("K1", "clear")
check("K1 cleared", lane("K1").status, "destroyed")
d1 = "\n".join(since("K1", "makefile_task5 destroy"))
check("Clear K1 ends with the HV off", '"$MM/hv.py" off' in d1.split("kconn_pwr off")[-1], True)
check("aggregate after Clear", state.server_status, "destroyed")
check("HV free again", lane("K1").use_hv, False)

# --- the Vitrek: several HV lanes take turns on the Keithley -------------------------
vform = dict(form, hvSwitch="vitrek", useHV="1")
for L, k in (("K3", "kria-test-3.example.org"), ("K4", "kria-test-4.example.org")):
    check(f"Init {L}", post(L, "init", {"kria": k}).status_code, 204)
    check(f"{L} initialized", wait_for(L, "initialized", "error"), "initialized")
r = post("K3", "submit", vform)
check("Configure K3 with the Vitrek", r.status_code, 200)
msg = (r.get_json() or {}).get("message", "")
### K3 is tray row 3: slot A = 3L = relay 7, slot C = 3R = relay 9
check("K3 message names its relays", "A relay 7 (3L)" in msg and "C relay 9 (3R)" in msg, True)
check("a second Vitrek HV lane is allowed",
      post("K4", "submit", dict(vform, moduleIDC="")).status_code, 200)
check("a cable HV lane next to Vitrek lanes is refused",
      post("K5", "init", {"kria": "kria-test-5.example.org"}).status_code, 204)
wait_for("K5", "initialized", "error")
r = post("K5", "submit", dict(form, useHV="1", hvSwitch="none", moduleIDC=""))
check("...refused", r.status_code, 400)
post("K5", "clear")

check("Run K3", post("K3", "run").status_code, 204)
check("Run K4", post("K4", "run").status_code, 204)
check("K3 idle", wait_for("K3", "idle", "error", timeout=240), "idle")
check("K4 idle", wait_for("K4", "idle", "error", timeout=240), "idle")
l3 = list(lane("K3").log)
log3, log4 = "\n".join(l3), "\n".join(lane("K4").log)
check("K3 slot A routed to relay 7", '"$MM/vitrek.py" route 7 ' in log3, True)
check("K3 slot C routed to relay 9", '"$MM/vitrek.py" route 9 ' in log3, True)
check("K4 slot A routed to relay 10", '"$MM/vitrek.py" route 10 ' in log4, True)
check("the Vitrek is checked before any bring-up", '"$MM/vitrek.py" idn' in log3 + log4, True)
check("no cable pause with the Vitrek", "would wait for Continue" in log3 + log4, False)
g = index_of(l3, '"$MM/run_slot.sh" A ')
rt = index_of(l3, '"$MM/vitrek.py" route 7', max(g, 0))
on = index_of(l3, '"$MM/hv.py" on', max(rt, 0))
off = index_of(l3, '"$MM/hv.py" off', max(on, 0))
rs = index_of(l3, '"$MM/vitrek.py" reset', max(off, 0))
check("gate, then the relay", g >= 0 and rt > g, True)
check("relay, then HV on", on > rt, True)
check("HV off, then every relay open", off > on and rs > off, True)
check("K4 never routed K3's relays", "route 7 " in log4 or "route 9 " in log4, False)
check("Keithley free after both runs", status("K3") and c.get("/task5/status").get_json()["keithley"], "")

post("K3", "stop")
s3 = "\n".join(since("K3", "makefile_task5 stop"))
check("Stop with the Vitrek goes through the guard", "hv_guard.sh" in s3, True)
check("free Keithley: K3's Stop ramps down", '"$MM/hv.py" off' in s3, True)
check("free Keithley: K3's Stop opens the relays", '"$MM/vitrek.py" reset' in s3, True)
for L in ("K3", "K4"):
    post(L, "clear")

# --- a lane's Stop kills its own local process group, fast ---------------------------
probe = Lane("KX", "nowhere", 6199, ROOT)
t = threading.Thread(target=probe.run_command, args=("sleep 30; echo SHOULD-NOT-PRINT",))
t0 = time.time()
t.start()
time.sleep(0.5)
probe.stop_flag.set()
probe.kill_group()
t.join(10)
check("kill_group ends the lane's make", t.is_alive(), False)
check("within seconds", time.time() - t0 < 5, True)
### the "$ ..." line is the command itself, echoed into the log
check("nothing printed after the kill",
      any(l == "SHOULD-NOT-PRINT" for l in probe.log), False)

print("ui flow hv: %d failures" % fails)
sys.exit(1 if fails else 0)
