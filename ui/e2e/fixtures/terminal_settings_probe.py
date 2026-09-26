"""Record only QA-owned launch values from a real terminal, then accept input."""
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
keys = ["QA_GROUP", "QA_TERMINAL", "QA_OVERLAP", "QA_FILE", "QA_INIT", "QA_SHELL"]
with path.open("w") as output:
    output.write(json.dumps({"kind": "launch", "directory": os.getcwd(), "args": sys.argv[2:],
                             "env": {key: os.environ.get(key) for key in keys}}) + "\n")
print("TERMINAL_SETTINGS_READY", flush=True)
for line in sys.stdin:
    marker = line.rstrip("\r\n")
    with path.open("a") as output:
        output.write(json.dumps({"kind": "input", "text": marker}) + "\n")
    print("TERMINAL_SETTINGS_INPUT:" + marker, flush=True)
    if marker == "exit-probe":
        break
