#!/usr/bin/env python3
"""The Keithley lock and Stop's guard, with no instrument attached.

  - two lanes asking at once: one holds, the other waits, never both
  - a lock whose holder died is taken over
  - Stop on lane K1 leaves the Keithley alone while K2's live run holds it,
    and ramps it down when nobody does
  - the Vitrek relay comes from the lane's tray row (MMTS_HV_ROW)

    source .venv/bin/activate && source ./init_bash_vars.sh
    python3 scripts/task5_hv/test_hv_lock.py
"""
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
os.chdir(ROOT)

fails = 0


def check(what, got, want):
    global fails
    if got != want:
        print("FAIL %-48s got %r want %r" % (what, got, want))
        fails += 1


tmp = tempfile.mkdtemp()
LOCK = os.path.join(tmp, "hv_session.lock")
ENV = dict(os.environ, MMTS_HV_SESSION_LOCK=LOCK, MMTS_DRYRUN="1",
           MMTS_HV_LOCK=os.path.join(tmp, "hv.lock"),
           MMTS_VITREK_LOCK=os.path.join(tmp, "vitrek.lock"))


def bash(script, lane, **kw):
    env = dict(ENV, MMTS_LANE=lane, ROOT=ROOT)
    return subprocess.Popen(["bash", "-c", f'ROOT="{ROOT}"; . scripts/task5_hv/hv_lock.sh; {script}'],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kw)


# --- mutual exclusion: each holder writes start/end marks into one log ---------
log = os.path.join(tmp, "marks")
hold = (f'hv_lock_acquire A 60 echo || exit 1; echo "$MMTS_LANE in" >> {log}; '
        f'sleep 1.5; echo "$MMTS_LANE out" >> {log}; hv_lock_release')
p1, p2 = bash(hold, "K1"), bash(hold, "K2")
o1, o2 = p1.communicate()[0], p2.communicate()[0]
marks = open(log).read().split("\n")[:-1]
check("both got their turn", sorted(m.split()[0] for m in marks if m.endswith("in")), ["K1", "K2"])
check("never two holders at once", [m.split()[1] for m in marks], ["in", "out", "in", "out"])
check("the waiter said who held it", "waiting for the Keithley (held by" in o1 + o2, True)
check("lock gone afterwards", os.path.exists(LOCK), False)

# --- a dead holder's lock is taken over -------------------------------------------
os.makedirs(LOCK)
open(os.path.join(LOCK, "owner"), "w").write("K5 B 999999\n")
out = bash("hv_lock_try B && echo TOOK; hv_lock_release", "K1").communicate()[0]
check("stale lock taken over", "TOOK" in out, True)

# --- Stop's guard ------------------------------------------------------------------
sleeper = subprocess.Popen(["sleep", "30"])
os.makedirs(LOCK, exist_ok=True)
open(os.path.join(LOCK, "owner"), "w").write(f"K2 B {sleeper.pid}\n")
g = subprocess.run(["bash", "scripts/task5_hv/hv_guard.sh"], env=dict(ENV, MMTS_LANE="K1", hvSwitch="vitrek"),
                   capture_output=True, text=True)
check("K1's Stop leaves K2's Keithley alone", "held by K2 B" in g.stdout, True)
check("...and calls no hv.py", "hv.py" in g.stdout, False)
check("...and switches no relay", "vitrek.py" in g.stdout, False)
check("K2 still holds it", open(os.path.join(LOCK, "owner")).read().split()[0], "K2")
sleeper.kill()
sleeper.wait()

### K2's run is gone: its lock is stale, so K1's Stop may ramp down
g = subprocess.run(["bash", "scripts/task5_hv/hv_guard.sh"], env=dict(ENV, MMTS_LANE="K1", hvSwitch="vitrek"),
                   capture_output=True, text=True)
check("free Keithley: Stop ramps it down", '"$MM/hv.py" off' in g.stdout, True)
check("free Keithley: Stop opens every relay", '"$MM/vitrek.py" reset' in g.stdout, True)
check("guard releases the lock", os.path.exists(LOCK), False)

# --- relay per lane row --------------------------------------------------------------
def relay(lane, row, slot):
    r = subprocess.run([sys.executable, "scripts/task5_hv/hv_switch.py", "vitrek", "--to", slot],
                       env=dict(ENV, MMTS_LANE=lane, MMTS_HV_ROW=str(row)), capture_output=True, text=True)
    return r.stdout


check("K1 A -> 1L -> relay 1", '"$MM/vitrek.py" route 1 ' in relay("K1", 1, "A"), True)
check("K1 C -> 1R -> relay 3", '"$MM/vitrek.py" route 3 ' in relay("K1", 1, "C"), True)
check("K8 B -> 8C -> relay 23", '"$MM/vitrek.py" route 23 ' in relay("K8", 8, "B"), True)
r = subprocess.run([sys.executable, "scripts/task5_hv/hv_switch.py", "vitrek", "--to", "A"],
                   env=dict(ENV, MMTS_HV_ROW="9"), capture_output=True, text=True)
check("row 9 has no channel: refused", r.returncode != 0 and "no HVchannel" in (r.stdout + r.stderr), True)

print("hv lock: %d failures" % fails)
sys.exit(1 if fails else 0)
