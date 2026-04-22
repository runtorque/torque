"""Shared configuration, paths, and logging setup."""

import logging
import os
import re
from pathlib import Path

WS_PORT = int(os.environ.get("LOOM_PORT", 18932))
DEFAULT_COMMAND = os.environ.get("LOOM_DEFAULT_CMD", "claude")

# Seed knob for the future architect settings surface.  Architects are
# planning/scoping sessions, so new architects run from the main checkout by
# default instead of receiving a per-agent git worktree.
ARCHITECT_USES_WORKTREE: bool = False

# Standalone mode: daemon runs outside iTerm2's script environment,
# no toolbelt webview registration.  Dual mode (toolbelt + browser)
# works without this flag — just open http://127.0.0.1:<port>/.
STANDALONE = os.environ.get("LOOM_STANDALONE", "").lower() in ("1", "true", "yes")
BIND_HOST = "0.0.0.0" if os.environ.get("LOOM_BIND_ALL") else "127.0.0.1"

# Paths resolve relative to the *installed* entry-point script, not this file.
# The entry point sets SCRIPT_DIR before anything imports config.
SCRIPT_DIR: Path = Path(__file__).parent
WEBVIEW_FILE: Path = SCRIPT_DIR / "webview.html"
LEGACY_ATTACHMENTS_DIR: Path = Path.home() / ".loom" / "attachments"


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def _slugify_profile(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip().lower())
    text = text.strip(".-_")
    return text or "default"


def resolve_data_dir(script_dir: Path) -> Path:
    explicit = os.environ.get("LOOM_DATA_DIR", "").strip()
    if explicit:
        return Path(os.path.expanduser(explicit))

    profile = os.environ.get("LOOM_PROFILE", "").strip()
    if not profile and _truthy_env("LOOM_STANDALONE"):
        profile = "standalone"
    if profile:
        return Path.home() / ".loom" / "profiles" / _slugify_profile(profile)
    return script_dir


def _resolve_attachments_dir(script_dir: Path, data_dir: Path) -> Path:
    try:
        if data_dir.expanduser().resolve() == script_dir.expanduser().resolve():
            return LEGACY_ATTACHMENTS_DIR
    except FileNotFoundError:
        pass
    return data_dir / "attachments"


DATA_DIR: Path = resolve_data_dir(SCRIPT_DIR)
STATE_FILE: Path = DATA_DIR / "state.json"
DB_FILE: Path = DATA_DIR / "loom.db"
LOG_FILE: Path = DATA_DIR / "loom.log"
ATTACHMENTS_DIR: Path = _resolve_attachments_dir(SCRIPT_DIR, DATA_DIR)


def init_paths(script_dir: Path):
    """Called once from the entry point to anchor code and data paths."""
    global SCRIPT_DIR, DATA_DIR, STATE_FILE, DB_FILE, WEBVIEW_FILE, LOG_FILE, ATTACHMENTS_DIR
    SCRIPT_DIR = script_dir
    DATA_DIR = resolve_data_dir(script_dir)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE = DATA_DIR / "state.json"
    DB_FILE = DATA_DIR / "loom.db"
    WEBVIEW_FILE = script_dir / "webview.html"
    LOG_FILE = DATA_DIR / "loom.log"
    ATTACHMENTS_DIR = _resolve_attachments_dir(script_dir, DATA_DIR)
    _setup_logging()
    log.info("Logging initialized at %s", LOG_FILE)
    log.info("Runtime data directory: %s", DATA_DIR)


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
