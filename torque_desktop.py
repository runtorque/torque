#!/usr/bin/env python3
"""Torque desktop-shell entry point."""

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


if not os.environ.get("TORQUE_STANDALONE"):
    os.environ["TORQUE_STANDALONE"] = "1"
desktop_profile = (os.environ.get("TORQUE_DESKTOP_PROFILE", "") or "").strip() or "desktop"
desktop_port = (os.environ.get("TORQUE_DESKTOP_PORT", "") or "").strip() or "18933"
desktop_data_dir = (os.environ.get("TORQUE_DESKTOP_DATA_DIR", "") or "").strip()
if not desktop_data_dir:
    desktop_data_dir = str(
        Path.home() / ".torque" / "profiles" / _slugify_profile(desktop_profile)
    )
os.environ["TORQUE_DESKTOP_PROFILE"] = desktop_profile
os.environ["TORQUE_DESKTOP_PORT"] = desktop_port
os.environ["TORQUE_DESKTOP_DATA_DIR"] = desktop_data_dir
os.environ["TORQUE_PROFILE"] = desktop_profile
os.environ["TORQUE_PORT"] = desktop_port
os.environ["TORQUE_DATA_DIR"] = desktop_data_dir
if not os.environ.get("TORQUE_DESKTOP_MODE"):
    os.environ["TORQUE_DESKTOP_MODE"] = "spawn"

from torque.config import init_paths  # noqa: E402

init_paths(SCRIPT_DIR)

from torque.desktop import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
