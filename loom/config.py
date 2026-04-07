"""Shared configuration, paths, and logging setup."""

import logging
import os
from pathlib import Path

WS_PORT = int(os.environ.get("LOOM_PORT", 18932))
DEFAULT_COMMAND = os.environ.get("LOOM_DEFAULT_CMD", "claude")

# Standalone mode: daemon runs outside iTerm2's script environment,
# no toolbelt webview registration.  Dual mode (toolbelt + browser)
# works without this flag — just open http://127.0.0.1:<port>/.
STANDALONE = os.environ.get("LOOM_STANDALONE", "").lower() in ("1", "true", "yes")
BIND_HOST = "0.0.0.0" if os.environ.get("LOOM_BIND_ALL") else "127.0.0.1"

# Paths resolve relative to the *installed* entry-point script, not this file.
# The entry point sets SCRIPT_DIR before anything imports config.
SCRIPT_DIR: Path = Path(__file__).parent
STATE_FILE: Path = SCRIPT_DIR / "state.json"
DB_FILE: Path = SCRIPT_DIR / "loom.db"
WEBVIEW_FILE: Path = SCRIPT_DIR / "webview.html"
LOG_FILE: Path = SCRIPT_DIR / "loom.log"
ATTACHMENTS_DIR: Path = Path.home() / ".loom" / "attachments"


def init_paths(script_dir: Path):
    """Called once from the entry point to anchor paths to the install location."""
    global SCRIPT_DIR, STATE_FILE, DB_FILE, WEBVIEW_FILE, LOG_FILE
    SCRIPT_DIR = script_dir
    STATE_FILE = script_dir / "state.json"
    DB_FILE = script_dir / "loom.db"
    WEBVIEW_FILE = script_dir / "webview.html"
    LOG_FILE = script_dir / "loom.log"
    _setup_logging()
    log.info("Logging initialized at %s", LOG_FILE)


log = logging.getLogger("loom")


def _managed_log_handlers():
    for handler in list(log.handlers):
        if getattr(handler, "_loom_managed", False):
            yield handler


def _setup_logging():
    target = LOG_FILE.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target_str = str(target)

    for handler in list(_managed_log_handlers()):
        if getattr(handler, "baseFilename", "") == target_str:
            log.setLevel(logging.DEBUG)
            log.propagate = False
            return
        log.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    log.setLevel(logging.DEBUG)
    log.propagate = False
    fh = logging.FileHandler(target, encoding="utf-8")
    fh._loom_managed = True
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
    ))
    log.addHandler(fh)


_setup_logging()
