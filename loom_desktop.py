#!/usr/bin/env python3
"""Loom desktop-shell entry point."""

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

if not os.environ.get("LOOM_STANDALONE"):
    os.environ["LOOM_STANDALONE"] = "1"
if not os.environ.get("LOOM_PROFILE"):
    os.environ["LOOM_PROFILE"] = "desktop"
if not os.environ.get("LOOM_PORT"):
    os.environ["LOOM_PORT"] = "18933"
if not os.environ.get("LOOM_DESKTOP_SHELL"):
    os.environ["LOOM_DESKTOP_SHELL"] = "pywebview"

from loom.config import init_paths  # noqa: E402

init_paths(SCRIPT_DIR)

from loom.desktop import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
