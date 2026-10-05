"""Harmless real-PTY fixture: bounded initial output, ticks and input/size evidence."""
import json
import os
import select
import signal
import sys
import time
from pathlib import Path

log = Path(sys.argv[1])


def record(kind, **values):
    with log.open("a") as output:
        output.write(json.dumps({"kind": kind, "at": time.time(), **values}) + "\n")


def geometry(*_args):
    size = os.get_terminal_size()
    record("resize", cols=size.columns, rows=size.lines)


signal.signal(signal.SIGWINCH, geometry)
geometry()
for index in range(100):
    print(f"SCROLLBACK_LINE_{index:03}", flush=True)
sequence = 0
emitting = True
while True:
    ready, _, _ = select.select([sys.stdin], [], [], 0.05)
    if ready:
        line = sys.stdin.readline()
        if not line:
            break
        record("input", text=line.strip())
        print("RECEIVED:" + line.strip(), flush=True)
        if line.strip() == "pause":
            emitting = False
        elif line.strip() == "resume":
            emitting = True
    if emitting:
        sequence += 1
        print(f"LIVE_TICK_{sequence:05}", flush=True)
