#!/usr/bin/env python3
"""Loom entry point.

This thin wrapper anchors paths to the install directory
and delegates to the loom package.
"""

import asyncio
import sys
from pathlib import Path

# The installed layout puts this file and the loom/ package
# in the same directory.  Make sure Python can find the package.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from loom.config import init_paths, STANDALONE  # noqa: E402
init_paths(SCRIPT_DIR)

from loom.server import main  # noqa: E402



def _run_standalone() -> None:
    try:
        asyncio.run(main(None))
    except KeyboardInterrupt:
        pass


if STANDALONE:
    _run_standalone()
else:
    import iterm2  # noqa: E402

    iterm2.run_forever(main, retry=False)
