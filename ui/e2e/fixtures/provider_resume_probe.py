"""Local executable fixture; observe resume arguments without invoking a provider."""
import json
import os
import sys
from pathlib import Path

path = Path(__file__).parent / (os.environ["TORQUE_CELL_ID"] + ".jsonl")
# Do not capture generated configuration, credentials, or the provider prompt.
with path.open("a") as output:
    output.write(json.dumps({
        "resume": "resume" in sys.argv[1:],
        "sessions": [arg for arg in sys.argv[1:] if arg.startswith("qa-session-")],
        "pid": os.getpid(),
        "directory": os.getcwd(),
    }) + "\n")
print("OpenAI Codex model: QA directory: local ›", flush=True)
for line in sys.stdin:
    if line.strip() == "exit-probe":
        break
