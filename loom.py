#!/usr/bin/env python3
"""Loom — iTerm2 Toolbelt plugin entry point.

This thin wrapper anchors paths to the install directory
and delegates to the loom package.
"""

import sys
from pathlib import Path

# The installed layout puts this file and the loom/ package
# in the same directory.  Make sure Python can find the package.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from loom.config import init_paths  # noqa: E402
init_paths(SCRIPT_DIR)

import iterm2                       # noqa: E402
from loom.server import main        # noqa: E402

iterm2.run_forever(main)
