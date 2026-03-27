"""Shared configuration, paths, and logging setup."""

import logging
import os
from pathlib import Path

WS_PORT = int(os.environ.get("LOOM_PORT", 18932))
DEFAULT_COMMAND = os.environ.get("LOOM_DEFAULT_CMD", "claude")

# Paths resolve relative to the *installed* entry-point script, not this file.
# The entry point sets SCRIPT_DIR before anything imports config.
SCRIPT_DIR: Path = Path(__file__).parent
STATE_FILE: Path = SCRIPT_DIR / "state.json"
DB_FILE: Path = SCRIPT_DIR / "loom.db"
WEBVIEW_FILE: Path = SCRIPT_DIR / "webview.html"
LOG_FILE: Path = SCRIPT_DIR / "loom.log"


def init_paths(script_dir: Path):
    """Called once from the entry point to anchor paths to the install location."""
    global SCRIPT_DIR, STATE_FILE, DB_FILE, WEBVIEW_FILE, LOG_FILE
    SCRIPT_DIR = script_dir
    STATE_FILE = script_dir / "state.json"
    DB_FILE = script_dir / "loom.db"
    WEBVIEW_FILE = script_dir / "webview.html"
    LOG_FILE = script_dir / "loom.log"
    _setup_logging()


log = logging.getLogger("loom")


def _setup_logging():
    if log.handlers:
        return
    log.setLevel(logging.DEBUG)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
    ))
    log.addHandler(fh)
