"""Backfill/refresh Codex account usage from persisted rollout transcripts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import glob
import json
import os
import re
from pathlib import Path

from .adapters.codex import (
    _codex_home,
    _latest_codex_context_and_provider_usage_from_transcript,
)
from .config import log
from .provider_usage import normalize_provider_usage, provider_usage_fingerprint

_ROLLOUT_SESSION_ID_RE = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(?P<sid>.+)\.jsonl$"
)


@dataclass
class CodexUsageBackfillReport:
    """Accounting for one Codex provider_usage refresh pass."""

    changed: int = 0
    considered: int = 0
    skipped: Counter[str] = field(default_factory=Counter)

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())

    def skip(self, reason: str) -> None:
        self.skipped[str(reason or "unknown")] += 1

    def summary(self) -> str:
        reasons = ", ".join(
            f"{reason}={count}"
            for reason, count in sorted(self.skipped.items())
        )
        if not reasons:
            reasons = "none"
        return (
            "Codex provider_usage backfill summary: "
            f"Backfilled {self.changed} / skipped {self.skipped_total} "
            f"(considered={self.considered}; {reasons})"
        )


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


def _rollout_session_id_from_path(path: Path) -> str:
    match = _ROLLOUT_SESSION_ID_RE.match(path.name)
    return str(match.group("sid") if match else "").strip()


def _path_candidates_for_cell(cell) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []
    for attr in ("worktree_path", "current_path", "directory"):
        raw = str(getattr(cell, attr, "") or "").strip()
        if not raw:
            continue
        expanded = os.path.abspath(os.path.expanduser(raw))
        for candidate in (expanded, os.path.realpath(expanded)):
            if candidate and candidate not in seen:
                seen.add(candidate)
                paths.append(candidate)
    return paths


def _rollout_session_meta_cwd(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for _ in range(20):
                line = fh.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                payload = item.get("payload") if isinstance(item, dict) else None
                if not isinstance(payload, dict):
                    continue
                if item.get("type") != "session_meta":
                    continue
                cwd = str(payload.get("cwd", "") or "").strip()
                if cwd:
                    return os.path.abspath(os.path.expanduser(cwd))
    except OSError:
        return ""
    except Exception:
        log.exception("Failed to read Codex rollout session_meta from %s", path)
        return ""
    return ""


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


def locate_latest_codex_rollout_for_workdir(
    workdirs: list[str] | tuple[str, ...] | set[str],
    *,
    codex_home: str | Path | None = None,
) -> tuple[Path, str] | None:
    """Locate the newest Codex rollout whose session_meta cwd matches a cell.

    This supports live/adopted Codex PTY sessions whose provider session id was
    never persisted by hooks.  We only infer a session id from a rollout when the
    transcript's session_meta cwd exactly matches one of the cell's launch/worktree
    directories, avoiding a global newest-rollout guess.
    """
    wanted: set[str] = set()
    for raw in workdirs or []:
        value = str(raw or "").strip()
        if not value:
            continue
        expanded = os.path.abspath(os.path.expanduser(value))
        wanted.add(expanded)
        wanted.add(os.path.realpath(expanded))
    wanted.discard("")
    if not wanted:
        return None

    sessions_dir = _codex_rollout_sessions_dir(codex_home)
    if not sessions_dir.is_dir():
        return None

    matches: list[Path] = []
    try:
        for path in sessions_dir.glob("**/rollout-*.jsonl"):
            try:
                if path.is_file():
                    matches.append(path)
            except OSError:
                continue
    except Exception:
        log.exception("Failed to scan Codex rollouts for workdir match")
        return None

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    for path in sorted(matches, key=_mtime, reverse=True):
        cwd = _rollout_session_meta_cwd(path)
        if not cwd:
            continue
        normalized = {cwd, os.path.realpath(cwd)}
        if not (normalized & wanted):
            continue
        sid = _rollout_session_id_from_path(path)
        if sid and _rollout_matches_session(path, sid):
            return (path, sid)
    return None


def _is_codex_agent(cell) -> bool:
    return (
        getattr(cell, "cell_type", "agent") == "agent"
        and getattr(cell, "agent_type", "") == "codex"
    )


def _is_dormant_codex_agent(cell) -> bool:
    return (
        _is_codex_agent(cell)
        and not getattr(cell, "session_id", None)
        and bool(str(getattr(cell, "agent_session_id", "") or "").strip())
    )


def backfill_codex_provider_usage_for_dormant_agents(
    state,
    *,
    codex_home: str | Path | None = None,
) -> int:
    """Backfill provider_usage for dormant Codex agents from their rollouts.

    Compatibility wrapper for the original dormant-only behavior.  Runtime code
    should prefer ``refresh_codex_provider_usage_for_agents`` so live/adopted
    Codex sessions are repaired too.
    """
    report = refresh_codex_provider_usage_for_agents(
        state,
        codex_home=codex_home,
        include_live=False,
        include_session_inference=False,
    )
    return report.changed


def refresh_codex_provider_usage_for_agents(
    state,
    *,
    codex_home: str | Path | None = None,
    include_live: bool = True,
    include_session_inference: bool = True,
) -> CodexUsageBackfillReport:
    """Refresh Codex provider_usage for active cells from rollout transcripts.

    Returns an accounting report with explicit skip reasons so a changed=0 pass
    is observable in logs.  Tombstoned cells are intentionally excluded from the
    mutation pass, but counted separately for diagnostics.
    """
    report = CodexUsageBackfillReport()
    for cell in list(getattr(state, "agents", {}).values()):
        if not _is_codex_agent(cell):
            continue
        if state.agent_is_tombstoned(cell):
            report.skip("tombstoned")
            continue
        report.considered += 1
        try:
            if not include_live and getattr(cell, "session_id", None):
                report.skip("active_session")
                continue
            result = _refresh_codex_provider_usage_for_cell(
                state,
                cell,
                codex_home=codex_home,
                include_session_inference=include_session_inference,
            )
            if result == "changed":
                report.changed += 1
            else:
                report.skip(result)
        except Exception:
            report.skip("exception")
            log.exception(
                "Codex provider_usage backfill failed for cell %s",
                getattr(cell, "id", ""),
            )
    return report


def backfill_codex_provider_usage_for_cell(
    state,
    cell,
    *,
    codex_home: str | Path | None = None,
) -> bool:
    """Backfill one dormant Codex cell, returning True on emitted change."""
    if not _is_dormant_codex_agent(cell):
        return False
    return _refresh_codex_provider_usage_for_cell(
        state,
        cell,
        codex_home=codex_home,
        include_session_inference=False,
    ) == "changed"


def _resolve_rollout_for_cell(
    cell,
    *,
    codex_home: str | Path | None,
    include_session_inference: bool,
) -> tuple[Path | None, str, str | None]:
    agent_session_id = str(getattr(cell, "agent_session_id", "") or "").strip()
    if agent_session_id:
        rollout_path = locate_codex_rollout_for_session(
            agent_session_id,
            codex_home=codex_home,
        )
        if rollout_path is None:
            return (None, agent_session_id, "no_rollout")
        return (rollout_path, agent_session_id, None)

    if not include_session_inference:
        return (None, "", "no_agent_session_id")

    workdirs = _path_candidates_for_cell(cell)
    if not workdirs:
        return (None, "", "no_agent_session_id")
    located = locate_latest_codex_rollout_for_workdir(
        workdirs,
        codex_home=codex_home,
    )
    if located is None:
        return (None, "", "no_rollout")
    rollout_path, inferred_sid = located
    return (rollout_path, inferred_sid, None)


def _refresh_codex_provider_usage_for_cell(
    state,
    cell,
    *,
    codex_home: str | Path | None = None,
    include_session_inference: bool = True,
) -> str:
    """Refresh one Codex cell; return ``changed`` or a skip reason."""
    if not _is_codex_agent(cell):
        return "not_codex"

    rollout_path, agent_session_id, skip_reason = _resolve_rollout_for_cell(
        cell,
        codex_home=codex_home,
        include_session_inference=include_session_inference,
    )
    if skip_reason:
        return skip_reason
    if rollout_path is None or not agent_session_id:
        return "no_rollout"

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

    cell_changed = False
    persisted_sid = str(getattr(cell, "agent_session_id", "") or "").strip()
    if agent_session_id and persisted_sid != agent_session_id:
        cell.agent_session_id = agent_session_id
        cell_changed = True

    if normalized_usage is not None:
        if provider_usage_fingerprint(
            getattr(cell, "provider_usage", None)
        ) != provider_usage_fingerprint(normalized_usage):
            cell.provider_usage = normalized_usage
            cell_changed = True

    if context_window and getattr(cell, "context_window", {}) != context_window:
        cell.context_window = context_window
        cell_changed = True

    if not cell_changed:
        if normalized_usage is None:
            return "no_rate_limits"
        return "fingerprint_unchanged"

    state._emit_agent(cell, coalesce_ephemeral=True)
    state._db_save_agent(cell)
    return "changed"
