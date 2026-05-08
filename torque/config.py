"""Shared configuration, paths, and logging setup."""

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

WS_PORT = int(os.environ.get("TORQUE_PORT", 18932))
DEFAULT_COMMAND = os.environ.get("TORQUE_DEFAULT_CMD", "claude")

# Default boot nudges for long-running agents whose role config has no
# initial_prompt. Architects/engineers boot into idle and need a kickoff to
# run their wake protocol; workers receive their dispatch prompt instead and
# do not fire these. Configurable via GlobalSettings — these are the
# code-side defaults.
DEFAULT_ARCHITECT_BOOT_NUDGE = (
    "Wake. Run boot protocol — read journal, decisions, engineer list, "
    "board summary, recent events."
)
DEFAULT_ENGINEER_BOOT_NUDGE = (
    "Wake. Run engineer wake protocol — check assigned tasks, journal, "
    "worker status."
)

# Seed knob for the future architect settings surface.  Architects are
# planning/scoping sessions, so new architects run from the main checkout by
# default instead of receiving a per-agent git worktree.
ARCHITECT_USES_WORKTREE: bool = False

# Standalone mode: daemon runs outside iTerm2's script environment,
# no toolbelt webview registration.  Dual mode (toolbelt + browser)
# works without this flag — just open http://127.0.0.1:<port>/.
STANDALONE = os.environ.get("TORQUE_STANDALONE", "").lower() in ("1", "true", "yes")
BIND_HOST = "0.0.0.0" if os.environ.get("TORQUE_BIND_ALL") else "127.0.0.1"

# Paths resolve relative to the *installed* entry-point script, not this file.
# The entry point sets SCRIPT_DIR before anything imports config.
SCRIPT_DIR: Path = Path(__file__).parent
WEBVIEW_FILE: Path = SCRIPT_DIR / "webview.html"
LEGACY_ATTACHMENTS_DIR: Path = Path.home() / ".torque" / "attachments"


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def _profile_enabled_env() -> bool:
    """Return whether profile-only instrumentation/endpoints are enabled.

    ``TORQUE_PROFILE`` already names the runtime data profile for normal Torque
    launches, so only boolean-looking values opt into instrumentation through
    that compatibility flag.  Harness launches should prefer
    ``TORQUE_PERF_PROFILE=1`` or ``TORQUE_PROFILE_ENABLED=1``.
    """
    if any(_truthy_env(name) for name in (
            "TORQUE_PROFILE_ENABLED",
            "TORQUE_PERF_PROFILE",
            "TORQUE_PERF_HARNESS",
    )):
        return True
    return os.environ.get("TORQUE_PROFILE", "").lower() in ("1", "true", "yes")


PROFILE_ENABLED: bool = _profile_enabled_env()
PROFILE_SKIP_PTY: bool = _truthy_env("TORQUE_PROFILE_SKIP_PTY")


def _slugify_profile(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip().lower())
    text = text.strip(".-_")
    return text or "default"


def resolve_data_dir(script_dir: Path) -> Path:
    explicit = os.environ.get("TORQUE_DATA_DIR", "").strip()
    if explicit:
        return Path(os.path.expanduser(explicit))

    profile = os.environ.get("TORQUE_PROFILE", "").strip()
    if not profile and _truthy_env("TORQUE_STANDALONE"):
        profile = "standalone"
    if profile:
        return Path.home() / ".torque" / "profiles" / _slugify_profile(profile)
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
DB_FILE: Path = DATA_DIR / "torque.db"
LOG_FILE: Path = DATA_DIR / "torque.log"
ATTACHMENTS_DIR: Path = _resolve_attachments_dir(SCRIPT_DIR, DATA_DIR)
EVENT_INGEST_SOCKET_FILE: Path = DATA_DIR / "event_ingest.sock"
EVENT_INGEST_PID_FILE: Path = DATA_DIR / "event_ingest.pid"
EVENT_INGEST_DB_FILE: Path = DATA_DIR / "event_ingest.db"
EVENT_INGEST_LOG_FILE: Path = DATA_DIR / "event_ingest.log"
EVENT_INGEST_MAX_ROWS: int = int(
    os.environ.get("TORQUE_EVENT_INGEST_MAX_ROWS", "50000"))
LOG_ROTATION_MAX_BYTES: int = int(
    os.environ.get("TORQUE_LOG_ROTATION_MAX_BYTES", str(10 * 1024 * 1024)))
LOG_ROTATION_BACKUP_COUNT: int = int(
    os.environ.get("TORQUE_LOG_ROTATION_BACKUP_COUNT", "5"))
_STALE_PRE_KINDS_BACKUP_NAME = "torque.db.pre-kinds.bak"


def init_paths(script_dir: Path):
    """Called once from the entry point to anchor code and data paths."""
    global SCRIPT_DIR, DATA_DIR, STATE_FILE, DB_FILE, WEBVIEW_FILE, LOG_FILE, ATTACHMENTS_DIR
    global EVENT_INGEST_SOCKET_FILE, EVENT_INGEST_PID_FILE, EVENT_INGEST_DB_FILE, EVENT_INGEST_LOG_FILE
    SCRIPT_DIR = script_dir
    DATA_DIR = resolve_data_dir(script_dir)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE = DATA_DIR / "state.json"
    DB_FILE = DATA_DIR / "torque.db"
    WEBVIEW_FILE = script_dir / "webview.html"
    LOG_FILE = DATA_DIR / "torque.log"
    ATTACHMENTS_DIR = _resolve_attachments_dir(script_dir, DATA_DIR)
    EVENT_INGEST_SOCKET_FILE = DATA_DIR / "event_ingest.sock"
    EVENT_INGEST_PID_FILE = DATA_DIR / "event_ingest.pid"
    EVENT_INGEST_DB_FILE = DATA_DIR / "event_ingest.db"
    EVENT_INGEST_LOG_FILE = DATA_DIR / "event_ingest.log"
    _setup_logging()
    log.info("Logging initialized at %s", LOG_FILE)
    log.info("Runtime data directory: %s", DATA_DIR)
    _cleanup_stale_pre_kinds_backup(DATA_DIR)


log = logging.getLogger("torque")


def _managed_log_handlers():
    for handler in list(log.handlers):
        if getattr(handler, "_torque_managed", False):
            yield handler


def _build_log_handler(target: Path) -> logging.Handler:
    try:
        return RotatingFileHandler(
            str(target),
            maxBytes=LOG_ROTATION_MAX_BYTES,
            backupCount=LOG_ROTATION_BACKUP_COUNT,
            encoding="utf-8",
        )
    except Exception as exc:  # pragma: no cover - exercised via fallback test
        try:
            handler = logging.FileHandler(target, encoding="utf-8")
        except Exception:
            handler = logging.StreamHandler(sys.stderr)
        handler._torque_rotation_error = repr(exc)
        return handler


def _setup_logging():
    target = LOG_FILE.expanduser().resolve()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target_str = str(target)
    except Exception as exc:
        target_str = ""
        target = None
        setup_error = exc
    else:
        setup_error = None

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
    fh = _build_log_handler(target) if target is not None else logging.StreamHandler(sys.stderr)
    fh._torque_managed = True
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
    ))
    log.addHandler(fh)
    if setup_error is not None:
        log.warning("Logging path setup failed for %s: %s", LOG_FILE, setup_error)
    elif getattr(fh, "_torque_rotation_error", None):
        log.warning(
            "Rotating log handler unavailable for %s; using fallback: %s",
            target,
            fh._torque_rotation_error,
        )


def _cleanup_stale_pre_kinds_backup(data_dir: Path | None = None) -> bool:
    backup_path = Path(data_dir or DATA_DIR) / _STALE_PRE_KINDS_BACKUP_NAME
    try:
        backup_path.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning("Could not remove stale pre-kinds backup %s: %s", backup_path, exc)
        return False
    log.info("Removed stale pre-kinds backup %s", backup_path)
    return True


_setup_logging()
