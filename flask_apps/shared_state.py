#!/usr/bin/env python3
import threading
from collections import deque


jobmode = ''
threading_lock = threading.Lock()
# threading_lock.acquire()
# threading_lock.release()
server_status = 'startup'
debug_mode = False
#job_stop_flag - threading.Event()
#job_thread = {'thread': None}

runidx = 0 ## used to identify run tag. It is always increased

DAQresult_current_modified = '' ## recorded state of os.path.getmtime(dirDAQresult)

### task4 (Multi Pedestal Run): per-slot progress, parsed out of the run_slot.sh
### stage banners, and the tail of the run log the page shows.
task4_slots = {} ## {'A': {'state':..,'led':..,'stage':..}, ...}
task4_log = deque(maxlen=300)

### task5 (HV Pedestal Scan): the same, plus the bias point and the leakage
### hv.py reported at it. Separate containers, so switching job mode never shows
### one job's slots under the other's banners.
task5_slots = {} ## {'A': {'state':..,'led':..,'stage':..,'bv':..,'leak_uA':..}, ...}
task5_log = deque(maxlen=300)

### the Kria task4/task5 drive; '' = the client host's own KRIA_IP. Set at Initialize.
kria_ip = ''
