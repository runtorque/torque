#!/usr/bin/env python3
"""Loom desktop-shell entry point."""

import os
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

def _slugify_profile(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip().lower())
    text = text.strip(".-_")
    return text or "default"


if not os.environ.get("LOOM_STANDALONE"):
    os.environ["LOOM_STANDALONE"] = "1"
desktop_profile = (os.environ.get("LOOM_DESKTOP_PROFILE", "") or "").strip() or "desktop"
desktop_port = (os.environ.get("LOOM_DESKTOP_PORT", "") or "").strip() or "18933"
desktop_data_dir = (os.environ.get("LOOM_DESKTOP_DATA_DIR", "") or "").strip()
if not desktop_data_dir:
    desktop_data_dir = str(
        Path.home() / ".loom" / "profiles" / _slugify_profile(desktop_profile)
    )
os.environ["LOOM_DESKTOP_PROFILE"] = desktop_profile
os.environ["LOOM_DESKTOP_PORT"] = desktop_port
os.environ["LOOM_DESKTOP_DATA_DIR"] = desktop_data_dir
os.environ["LOOM_PROFILE"] = desktop_profile
os.environ["LOOM_PORT"] = desktop_port
os.environ["LOOM_DATA_DIR"] = desktop_data_dir
if not os.environ.get("LOOM_DESKTOP_MODE"):
    os.environ["LOOM_DESKTOP_MODE"] = "spawn"
if not os.environ.get("LOOM_DESKTOP_SHELL"):
    os.environ["LOOM_DESKTOP_SHELL"] = "pywebview"

from loom.config import init_paths  # noqa: E402

init_paths(SCRIPT_DIR)

from loom.desktop import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
