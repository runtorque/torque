#!/usr/bin/env python3
"""Torque standalone entry point.

This thin wrapper anchors paths to the install directory
and delegates to the torque package.
"""

import asyncio
import os
import sys
from pathlib import Path

# The installed layout puts this file and the torque/ package
# in the same directory.  Make sure Python can find the package.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# torque.py is standalone-only. Force the mode flag before importing
# torque.config/torque.server so their module-level STANDALONE checks match
# the entrypoint path even when inherited environments omit or override it.
os.environ["TORQUE_STANDALONE"] = "1"

from torque.config import init_paths  # noqa: E402
init_paths(SCRIPT_DIR)

from torque.server import main  # noqa: E402


def _run_standalone() -> None:
    try:
        asyncio.run(main(None))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    _run_standalone()
