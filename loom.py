#!/usr/bin/env python3
"""Loom entry point.

This thin wrapper anchors paths to the install directory
and delegates to the loom package.
"""

import asyncio
import os
import re
import sys
from pathlib import Path

# The installed layout puts this file and the loom/ package
# in the same directory.  Make sure Python can find the package.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))



def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")



def _slugify_profile(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip().lower())
    text = text.strip(".-_")
    return text or "default"



def _configure_rust_bridge_env() -> bool:
    if not _truthy(os.environ.get("LOOM_RUST_BRIDGE")):
        return False

    rust_profile = (os.environ.get("LOOM_RUST_PROFILE") or "").strip() or "rust-bridge"
    rust_port = (os.environ.get("LOOM_RUST_PORT") or "").strip() or "18934"
    rust_data_dir = (os.environ.get("LOOM_RUST_DATA_DIR") or "").strip()
    if not rust_data_dir:
        rust_data_dir = str(
            Path.home() / ".loom" / "profiles" / _slugify_profile(rust_profile)
        )

    os.environ["LOOM_RUST_PROFILE"] = rust_profile
    os.environ["LOOM_RUST_PORT"] = rust_port
    os.environ["LOOM_RUST_DATA_DIR"] = rust_data_dir
    os.environ["LOOM_PROFILE"] = rust_profile
    os.environ["LOOM_PORT"] = rust_port
    os.environ["LOOM_DATA_DIR"] = rust_data_dir
    if not os.environ.get("LOOM_RUST_BRIDGE_MODE"):
        os.environ["LOOM_RUST_BRIDGE_MODE"] = "spawn"
    return True


RUST_BRIDGE_MODE = _configure_rust_bridge_env()

from loom.config import init_paths, STANDALONE  # noqa: E402
init_paths(SCRIPT_DIR)

if RUST_BRIDGE_MODE:
    from loom.rust_bridge_runtime import main  # noqa: E402
else:
    from loom.server import main  # noqa: E402



def _run_standalone() -> None:
    try:
        asyncio.run(main(None))
    except KeyboardInterrupt:
        pass


if RUST_BRIDGE_MODE:
    import iterm2  # noqa: E402

    iterm2.run_forever(main, retry=False)
elif STANDALONE:
    _run_standalone()
else:
    import iterm2  # noqa: E402

    iterm2.run_forever(main, retry=False)
