#!/usr/bin/env python3
"""Self-checking test for board_params.py. Run it directly: no pytest needed.

    python3 scripts/task4_mmts_pedestal/test_board_params.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BP = os.path.join(HERE, "board_params.py")


def run(*args):
    p = subprocess.run([sys.executable, BP] + list(args),
                       capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


CASES = [
    # the example serials of MMTS_MAC_INSTRUCTIONS 2.1
    ("320XLR4DQE00020", "initLD-Left-3b LD-Semi 2"),
    ("320XLF4CQH00443", "initLD-Full-3b LD-Full 3 POWER="),
    ("320XLL4DQE00016", "initLD-Left-3b LD-Semi 2"),
    ("320XHF4DPM02021", "initHD-Full-trophyV3 HD-Full 6 POWER="),
    # the types the bench has actually run
    ("320XL54DQE00318", "initLD-Five-3b LD-Five 3"),
    ("320XLB4DMR00159", "initLD-Bottom-3b LD-Semi 2"),
    ("320XHT4CPM00019", "initHD-Top-trophyV3 HD-Top 3"),
    # dashes and lower case are normalised away before the type code is read
    ("320-XLF-4CQ-H00443", "initLD-Full-3b LD-Full 3 POWER="),
    ("320xlf4cqh00443", "initLD-Full-3b LD-Full 3 POWER="),
]

FAILURES = [
    ("320XZZ4DQE00020", "unknown type code"),
    ("320XLF4CQH", "too short"),
    ("", "empty"),
]

fails = 0
for serial, want in CASES:
    rc, got = run(serial)
    if rc != 0 or got != want:
        print("FAIL %-20s rc=%d got %r want %r" % (serial, rc, got, want))
        fails += 1

for serial, why in FAILURES:
    rc, got = run(serial)
    if rc != 2:
        print("FAIL %-20s (%s) should exit 2, got rc=%d %r" % (serial, why, rc, got))
        fails += 1

# the power override, the reason it exists: HB and HL/HR are guesses
rc, got = run("320XHB4DPM02021", "--power", "fitted")
if rc != 0 or got != "initHD-Bottom-trophyV3 HD-Bottom 4 POWER=":
    print("FAIL --power fitted: rc=%d got %r" % (rc, got))
    fails += 1
rc, got = run("320XLF4CQH00443", "--power", "none")
if rc != 0 or got != "initLD-Full-3b LD-Full 3":
    print("FAIL --power none: rc=%d got %r" % (rc, got))
    fails += 1
rc, got = run("320XLF4CQH00443", "--describe")
if rc != 0 or got != "LD Full, 3 ROCs, power board":
    print("FAIL --describe: rc=%d got %r" % (rc, got))
    fails += 1

print("board_params: %d checks, %d failures" % (len(CASES) + len(FAILURES) + 3, fails))
sys.exit(1 if fails else 0)
