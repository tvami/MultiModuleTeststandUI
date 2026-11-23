#!/usr/bin/env python3
"""The HV pieces of task5, with no bench and no Keithley attached.

Three things, in order of how expensive getting them wrong is:

  1. hv.py against a fake driver -- polarity, the ramp, compliance before output
     on, zero before off, the leakage abort. That lives next to hv.py itself
     (test_hv.py in the multimodule scripts), and is run from here so one command
     covers the whole HV path. Skipped with a printed note when the mirror is not
     checked out beside this repository.
  2. hv_switch.py -- the cable pause blocks until Continue, releases on it,
     gives up at the timeout, and never blocks in a dry run.
  3. the bias list -- the parser the form and the runner share.

    source .venv/bin/activate && source ./init_bash_vars.sh
    python3 scripts/task5_hv/test_hv_cli.py
"""
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SWITCH = os.path.join(HERE, "hv_switch.py")

### The Mac mirror of the bench scripts, which is where hv.py and its own test
### live. On pi0 it is $MM; here it sits beside the GUI checkout.
MIRROR = os.environ.get("MMTS_MULTIMODULE_DIR", os.path.join(
    os.path.dirname(ROOT), "multimodule", "hexactrl-sw", "hexactrl-script", "multimodule"))

fails = 0


def check(what, got, want):
    global fails
    if got != want:
        print("FAIL %-40s got %r want %r" % (what, got, want))
        fails += 1


# --- 1. hv.py itself --------------------------------------------------------
### and vitrek.py against a fake 964i: a route passes only on a clean readback
for name in ("test_hv.py", "test_vitrek.py"):
    path = os.path.join(MIRROR, name)
    if os.path.exists(path):
        p = subprocess.run([sys.executable, path], capture_output=True, text=True)
        print(p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "(no output)")
        check(f"{name} fake-instrument test", p.returncode, 0)
        if p.returncode:
            print(p.stdout)
    else:
        print("SKIP %s: no mirror at %s" % (name, MIRROR))


# --- 2. the cable pause -----------------------------------------------------
def switch(args, env=None, timeout=30):
    e = dict(os.environ)
    e.pop("MMTS_DRYRUN", None)
    e.update(env or {})
    return subprocess.run([sys.executable, SWITCH] + args, env=e,
                          capture_output=True, text=True, timeout=timeout)

d = tempfile.mkdtemp()

# it blocks, and Continue releases it
released = []


def press_continue():
    for _ in range(100):
        if os.path.exists(os.path.join(d, "hv_pause.json")):
            open(os.path.join(d, "hv_continue"), "w").close()
            released.append(True)
            return
        time.sleep(0.1)


t = threading.Thread(target=press_continue)
t.start()
p = switch(["none", "--to", "B", "--from", "A", "--pause-dir", d, "--timeout", "20"])
t.join()
check("pause waited for Continue", bool(released), True)
check("pause released rc", p.returncode, 0)
check("pause names the slot", "to slot B" in p.stdout, True)
check("pause says the output is off", "output off" in p.stdout, True)
check("pause file cleaned up", os.path.exists(os.path.join(d, "hv_pause.json")), False)
check("continue file cleaned up", os.path.exists(os.path.join(d, "hv_continue")), False)

# nobody presses it
p = switch(["none", "--to", "C", "--pause-dir", d, "--timeout", "2"])
check("pause timeout rc", p.returncode, 4)
check("pause timeout cleans up", os.path.exists(os.path.join(d, "hv_pause.json")), False)

# a dry run must walk the whole job without blocking
p = switch(["none", "--to", "A", "--pause-dir", d], env={"MMTS_DRYRUN": "1"}, timeout=10)
check("dry run does not block", p.returncode, 0)
check("dry run says so", "[dry-run]" in p.stdout, True)

# bad arguments
check("no slot refused", switch(["none", "--pause-dir", d]).returncode, 2)
check("bad slot refused", switch(["none", "--to", "D", "--pause-dir", d]).returncode, 2)
check("clear is safe when nothing waits",
      switch(["none", "--clear", "--pause-dir", d]).returncode, 0)


# --- 3. the bias list -------------------------------------------------------
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("AndrewModuleTestingGUI_BASE",
                      os.path.join(ROOT, "external_packages", "hgcal-module-testing-gui"))
import flask_apps.app_task5 as t5  # noqa: E402

check("bias list parsed", t5.parse_bias("1, 10,300"), [1, 10, 300])
check("bias list keeps its order", t5.parse_bias("300,10,1"), [300, 10, 1])
check("zero volts is a bias point", t5.parse_bias("0"), [0])
for bad in ("", "  ", "1,-10", "1,abc", "1,10.5"):
    try:
        t5.parse_bias(bad)
        print("FAIL bias list %r was accepted" % bad)
        fails += 1
    except ValueError:
        pass
try:
    t5.parse_bias("1,900", 800)
    print("FAIL bias list above the cap was accepted")
    fails += 1
except ValueError:
    pass

### The geometry's ceiling, which is the reason board_params carries maxv.
check("LD caps at 600", t5.board_params.TABLE["LF"]["maxv"], 600)
check("HD caps at 800", t5.board_params.TABLE["HF"]["maxv"], 800)

print("hv cli: %d failures" % fails)
sys.exit(1 if fails else 0)
