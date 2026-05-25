"""Install and legacy path helpers for Torque.

Keep this module side-effect free: callers use it during CLI startup, doctor
formatting, and tests that patch ``HOME``. Do not import torque.config here.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

LEGACY_TOOLBELT_RELATIVE = Path(
    "Library/Application Support/iTerm2/Scripts/torque/torque"
)
LEGACY_PROJECT_ITERM2ENV_RELATIVE = Path(
    "Library/Application Support/iTerm2/Scripts/torque/iterm2env"
)


def _home(home: Path | str | None = None) -> Path:
    if home is None:
        return Path.home()
    return Path(os.path.expanduser(str(home)))


def slugify_profile(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip().lower())
    text = text.strip(".-_")
    return text or "default"


def torque_home(home: Path | str | None = None) -> Path:
    return _home(home) / ".torque"


def primary_app_dir(home: Path | str | None = None) -> Path:
    return torque_home(home) / "app"


def primary_runtime_root(home: Path | str | None = None) -> Path:
    return torque_home(home) / "runtime"


def primary_runtime_venv(home: Path | str | None = None) -> Path:
    return primary_runtime_root(home) / "venv"


def primary_runtime_python(home: Path | str | None = None) -> Path:
    venv = primary_runtime_venv(home)
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def profiles_root(home: Path | str | None = None) -> Path:
    return torque_home(home) / "profiles"


def profile_data_dir(profile: str, home: Path | str | None = None) -> Path:
    return profiles_root(home) / slugify_profile(profile)


def legacy_toolbelt_dir(home: Path | str | None = None) -> Path:
    return _home(home) / LEGACY_TOOLBELT_RELATIVE


def legacy_project_iterm2env_dir(home: Path | str | None = None) -> Path:
    return _home(home) / LEGACY_PROJECT_ITERM2ENV_RELATIVE


def legacy_iterm2_python_candidates(home: Path | str | None = None) -> list[Path]:
    home_dir = _home(home)
    project_candidates = {
        path.resolve()
        for path in home_dir.glob(
            "Library/Application Support/iTerm2/Scripts/torque/iterm2env/versions/*/bin/python3"
        )
    }
    fallback_candidates = {
        path.resolve()
        for pattern in (
            "Library/Application Support/iTerm2/iterm2env-*/versions/*/bin/python3",
            ".config/iterm2/AppSupport/iterm2env-*/versions/*/bin/python3",
        )
        for path in home_dir.glob(pattern)
    }

    candidates = project_candidates or fallback_candidates
    if not candidates:
        return []

    def _sort_key(path: Path) -> tuple:
        version = path.parents[1].name
        parts: list[int | str] = []
        for part in version.split("."):
            try:
                parts.append(int(part))
            except ValueError:
                parts.append(part)
        return tuple(parts)

    return sorted(candidates, key=_sort_key)
