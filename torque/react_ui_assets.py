"""Safe lookup and cache policy for generated React UI assets."""

from __future__ import annotations

from pathlib import Path


UI_DEFAULT_VALUES = frozenset({"react", "legacy"})


def normalize_ui_default(value: str | None) -> str:
    """Normalize the process-local default renderer or reject a typo."""
    normalized = str(value or "react").strip().lower()
    if normalized not in UI_DEFAULT_VALUES:
        choices = ", ".join(sorted(UI_DEFAULT_VALUES))
        raise ValueError(
            f"Invalid TORQUE_UI_DEFAULT '{value}'. Expected one of: {choices}."
        )
    return normalized


def react_ui_request_path(request_path: str, matched_path: str) -> str:
    """Restore the root asset prefix consumed by aiohttp route matching."""
    relative = str(matched_path or "").lstrip("/")
    if str(request_path or "").startswith("/assets/"):
        return f"assets/{relative}"
    return relative


def resolve_react_ui_file(root: Path, request_path: str) -> Path | None:
    """Resolve one generated React asset without permitting path traversal."""
    root = root.resolve()
    relative = str(request_path or "").lstrip("/") or "index.html"
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def react_ui_cache_headers(path: Path) -> dict[str, str]:
    """Return cache policy for generated index and content-hashed assets."""
    if path.name == "index.html":
        return {"Cache-Control": "no-store"}
    if "assets" in path.parts:
        return {"Cache-Control": "public, max-age=31536000, immutable"}
    return {"Cache-Control": "no-cache"}
