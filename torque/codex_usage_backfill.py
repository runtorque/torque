"""Backfill dormant Codex account usage from persisted rollout transcripts."""

from __future__ import annotations

import glob
from pathlib import Path

from .adapters.codex import (
    _codex_home,
    _latest_codex_context_and_provider_usage_from_transcript,
)
from .config import log
from .provider_usage import normalize_provider_usage, provider_usage_fingerprint


def _codex_rollout_sessions_dir(codex_home: str | Path | None = None) -> Path:
    home = (
        Path(codex_home).expanduser()
        if codex_home is not None
        else _codex_home()
    )
    return home / "sessions"


def _rollout_matches_session(path: Path, agent_session_id: str) -> bool:
    """Return whether ``path`` is a Codex rollout for ``agent_session_id``.

    Codex names rollout transcripts
    ``rollout-<ISO timestamp>-<session uuid>.jsonl``.  The timestamp itself
    contains dashes, so the reliable mapping check is the full trailing
    ``-<session id>.jsonl`` suffix, not a naive split on ``-``.
    """
    sid = str(agent_session_id or "").strip()
    return bool(
        sid
        and path.name.startswith("rollout-")
        and path.name.endswith(f"-{sid}.jsonl")
    )


def locate_codex_rollout_for_session(
    agent_session_id: str,
    *,
    codex_home: str | Path | None = None,
) -> Path | None:
    """Locate the newest rollout transcript for a persisted Codex session id.

    Missing ids or missing rollouts are expected for some agents; callers can
    skip them.  We deliberately only consider files whose names end in the
    persisted session id so we never guess from another agent's newest rollout.
    """
    sid = str(agent_session_id or "").strip()
    if not sid:
        return None

    sessions_dir = _codex_rollout_sessions_dir(codex_home)
    if not sessions_dir.is_dir():
        return None

    pattern = f"**/rollout-*-{glob.escape(sid)}.jsonl"
    matches: list[Path] = []
    try:
        candidates = sessions_dir.glob(pattern)
        for path in candidates:
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            if not _rollout_matches_session(path, sid):
                log.warning(
                    "Skipping Codex rollout with unexpected session mapping: "
                    "agent_session_id=%s path=%s",
                    sid,
                    path,
                )
                continue
            matches.append(path)
    except Exception:
        log.exception("Failed to locate Codex rollout for session %s", sid)
        return None

    if not matches:
        return None

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    return max(matches, key=_mtime)


def _is_dormant_codex_agent(cell) -> bool:
    return (
        getattr(cell, "cell_type", "agent") == "agent"
        and getattr(cell, "agent_type", "") == "codex"
        and not getattr(cell, "session_id", None)
        and bool(str(getattr(cell, "agent_session_id", "") or "").strip())
    )


def backfill_codex_provider_usage_for_dormant_agents(
    state,
    *,
    codex_home: str | Path | None = None,
) -> int:
    """Backfill provider_usage for dormant Codex agents from their rollouts.

    Returns the number of cells whose ephemeral usage/context telemetry changed.
    The pass never changes launch/session state: stopped agents remain stopped
    and ``session_id`` is not restored.
    """
    changed = 0
    for cell in list(state.iter_active_agents()):
        if not _is_dormant_codex_agent(cell):
            continue
        if backfill_codex_provider_usage_for_cell(
            state,
            cell,
            codex_home=codex_home,
        ):
            changed += 1
    return changed


def backfill_codex_provider_usage_for_cell(
    state,
    cell,
    *,
    codex_home: str | Path | None = None,
) -> bool:
    """Backfill one dormant Codex cell, returning True on emitted change."""
    if not _is_dormant_codex_agent(cell):
        return False

    agent_session_id = str(getattr(cell, "agent_session_id", "") or "").strip()
    rollout_path = locate_codex_rollout_for_session(
        agent_session_id,
        codex_home=codex_home,
    )
    if rollout_path is None:
        return False

    try:
        rollout_mtime = rollout_path.stat().st_mtime
    except OSError:
        rollout_mtime = None

    context_window, provider_usage = (
        _latest_codex_context_and_provider_usage_from_transcript(
            str(rollout_path),
            session_id=agent_session_id,
            timestamp=rollout_mtime,
        )
    )
    normalized_usage = normalize_provider_usage(provider_usage)
    if normalized_usage is None:
        return False

    cell_changed = False
    if provider_usage_fingerprint(
        getattr(cell, "provider_usage", None)
    ) != provider_usage_fingerprint(normalized_usage):
        cell.provider_usage = normalized_usage
        cell_changed = True

    if context_window and getattr(cell, "context_window", {}) != context_window:
        cell.context_window = context_window
        cell_changed = True

    if not cell_changed:
        return False

    state._emit_agent(cell, coalesce_ephemeral=True)
    state._db_save_agent(cell)
    return True
