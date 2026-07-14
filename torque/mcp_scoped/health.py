"""Engineer stream and task/agent health projection helpers."""

import time
from datetime import datetime, timezone

from torque.worktree_streams import compute_worktree_streams, member_task_ids_for_stream

_STREAM_STATES = (
    "implementing",
    "reviewing",
    "fixing_blockers",
    "awaiting_human_validation",
    "ready_to_merge",
    "merged",
)
_HEALTH_SUMMARY_SILENT_AFTER_SECS = 5 * 60
_HEALTH_SUMMARY_LIMIT = 120

def _agent_visible_to_engineer(state, engineer_cell, agent_id: str) -> bool:
    if not engineer_cell:
        return False
    return state.agent_is_visible_to_engineer(engineer_cell.id, agent_id)


def _task_agent_payload_for_engineer(state, engineer_cell, agent_id: str) -> dict:
    """Return safe agent details for task views without leaking hidden agents."""
    if not agent_id:
        return {}
    agent = state.agents.get(agent_id)
    if not agent or agent.cell_type != "agent":
        if engineer_cell and state.engineer_restricts_to_created_agents(
                engineer_cell.group):
            return {"agent_hidden": True}
        return {}
    if _agent_visible_to_engineer(state, engineer_cell, agent_id):
        return {
            "agent_name": agent.slug or agent.name,
            "agent_status": agent.status,
        }
    if state.engineer_restricts_to_created_agents(engineer_cell.group):
        return {"agent_hidden": True}
    return {}


def _stream_payload_for_engineer(state, engineer_cell, stream: dict) -> dict:
    """Return a stream payload with hidden agent identity scrubbed."""
    payload = dict(stream or {})
    agent_id = str(payload.get("agent_id", "") or "").strip()
    if not agent_id:
        return payload
    if _agent_visible_to_engineer(state, engineer_cell, agent_id):
        return payload
    payload["agent_id"] = ""
    payload["agent_name"] = ""
    payload["agent_slug"] = ""
    if state.engineer_restricts_to_created_agents(engineer_cell.group):
        payload["agent_hidden"] = True
    return payload


def _stream_state_counts(streams: list[dict]) -> dict[str, int]:
    counts = {name: 0 for name in _STREAM_STATES}
    for stream in streams:
        state_name = str(stream.get("state", "") or "").strip()
        if not state_name:
            continue
        if state_name not in counts:
            counts[state_name] = 0
        counts[state_name] += 1
    return counts


def _parse_health_timestamp(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _safe_int(value, default: int = 0) -> int:
    try:
        if value not in (None, ""):
            return int(value)
    except (TypeError, ValueError):
        pass
    return default


def _format_health_duration(seconds: int | float | None) -> str:
    total = max(0, int(seconds or 0))
    if total < 60:
        return f"{total} sec"
    minutes = total // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    rem_minutes = minutes % 60
    if hours < 24:
        if rem_minutes:
            return f"{hours} hr {rem_minutes} min"
        return f"{hours} hr"
    days = hours // 24
    rem_hours = hours % 24
    if rem_hours:
        return f"{days} day {rem_hours} hr"
    return f"{days} day"


def _format_health_clock(ts: float | None) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M UTC")


def _clip_health_fragment(value: str, *, limit: int = 24) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _limit_health_summary(parts: list[str], fallback_parts: list[str]) -> str:
    summary = "; ".join(part for part in parts if part).strip()
    if summary and not summary.endswith("."):
        summary += "."
    if len(summary) <= _HEALTH_SUMMARY_LIMIT:
        return summary
    summary = "; ".join(part for part in fallback_parts if part).strip()
    if summary and not summary.endswith("."):
        summary += "."
    if len(summary) <= _HEALTH_SUMMARY_LIMIT:
        return summary
    return summary[: max(0, _HEALTH_SUMMARY_LIMIT - 1)].rstrip() + "…"


def _fresh_health_details(details, *, now_ts: float) -> tuple[dict, int | None,
                                                             float | None]:
    fresh = dict(details or {}) if isinstance(details, dict) else {}
    last_activity_ts = _parse_health_timestamp(
        fresh.get("last_progress_at") or fresh.get("last_activity_at")
    )
    silence_secs = None
    if last_activity_ts is not None:
        silence_secs = max(0, int(now_ts - last_activity_ts))
        if "silence_secs" in fresh:
            fresh["silence_secs"] = silence_secs
    return fresh, silence_secs, last_activity_ts


def _source_task_for_health(state, task, details: dict):
    source_task_id = str((details or {}).get("source_task_id", "") or "").strip()
    if source_task_id and source_task_id in state.board_tasks:
        return state.board_tasks[source_task_id]
    return task


def _source_agent_for_health(state, task, details: dict):
    source_task = _source_task_for_health(state, task, details)
    agent_id = str(getattr(source_task, "agent_id", "") or "").strip()
    if not agent_id:
        agent_id = str(getattr(task, "agent_id", "") or "").strip()
    if not agent_id:
        return None
    return state.agents.get(agent_id)


def _health_summary(health_state: str, *, details: dict, agent=None,
                    now_ts: float, silence_secs: int | None = None,
                    last_activity_ts: float | None = None) -> str:
    state_name = str(health_state or "healthy").strip() or "healthy"
    if last_activity_ts is None:
        last_activity_ts = _parse_health_timestamp(
            (details or {}).get("last_progress_at")
            or (details or {}).get("last_activity_at")
        )
    if last_activity_ts is None and agent and getattr(agent, "last_progress_at", 0):
        last_activity_ts = float(getattr(agent, "last_progress_at", 0) or 0)
    if silence_secs is None and last_activity_ts is not None:
        silence_secs = max(0, int(now_ts - last_activity_ts))

    status = str(getattr(agent, "status", "") or "unknown").strip()
    tokens_in = _safe_int(getattr(agent, "session_tokens_in", 0) if agent else 0)
    tokens_out = _safe_int(getattr(agent, "session_tokens_out", 0) if agent else 0)
    tokens_part = f"tokens={tokens_in}/{tokens_out}"
    activity_detail = _clip_health_fragment(
        getattr(agent, "activity_detail", "") if agent else ""
    )
    activity_part = f"activity={activity_detail}" if activity_detail else ""
    clock = _format_health_clock(last_activity_ts)

    should_signal_silence = (
        silence_secs is not None
        and (
            silence_secs >= _HEALTH_SUMMARY_SILENT_AFTER_SECS
            or state_name in {"idle-risk", "stalled", "stale-in-progress"}
        )
    )
    if should_signal_silence:
        duration = _format_health_duration(silence_secs)
        parts = [
            f"Silent {duration}",
            f"status={status}",
            activity_part,
            tokens_part,
            f"last {clock}" if clock else "",
        ]
        fallback = [
            f"Silent {duration}",
            f"status={status}",
            tokens_part,
            f"last {clock}" if clock else "",
        ]
        return _limit_health_summary(parts, fallback)

    if state_name != "healthy":
        label = state_name.replace("-", " ").title()
        parts = [
            label,
            f"status={status}",
            activity_part,
            tokens_part,
            f"last {clock}" if clock else "",
        ]
        fallback = [label, f"status={status}", tokens_part]
        return _limit_health_summary(parts, fallback)

    if last_activity_ts is not None and silence_secs is not None:
        parts = [
            "Healthy",
            f"status={status}",
            f"last activity {_format_health_duration(silence_secs)} ago",
        ]
    else:
        parts = ["Healthy", f"status={status}"]
    return _limit_health_summary(parts, parts)


def _task_health_payload_for_response(state, task, *,
                                      now_ts: float | None = None) -> dict:
    """Return task health fields with freshness calculated at read time."""
    if now_ts is None:
        now_ts = time.time()
    health_state = str(getattr(task, "health_state", "") or "healthy")
    details, silence_secs, last_activity_ts = _fresh_health_details(
        getattr(task, "health_details", {}) or {},
        now_ts=now_ts,
    )
    agent = _source_agent_for_health(state, task, details)
    return {
        "health_state": health_state,
        "health_details": details,
        "health_summary": _health_summary(
            health_state,
            details=details,
            agent=agent,
            now_ts=now_ts,
            silence_secs=silence_secs,
            last_activity_ts=last_activity_ts,
        ),
    }


def _agent_health_payload_for_response(state, cell, *, current_task=None,
                                       now_ts: float | None = None) -> dict:
    """Return top-level health fields for agent detail responses."""
    if now_ts is None:
        now_ts = time.time()
    if current_task is not None:
        return _task_health_payload_for_response(
            state,
            current_task,
            now_ts=now_ts,
        )
    return {
        "health_state": "healthy",
        "health_summary": _health_summary(
            "healthy",
            details={},
            agent=cell,
            now_ts=now_ts,
        ),
    }


def _engineer_streams(state, engineer_cell, group: str, *,
                    include_merged: bool = True,
                    include_orphaned: bool = False,
                    visibility_limit: int = 10,
                    state_filter: str = "",
                    branch_filter: str = "",
                    repo_root_filter: str = "") -> list[dict]:
    streams = [
        _stream_payload_for_engineer(state, engineer_cell, stream)
        for stream in compute_worktree_streams(
            state,
            group=group,
            visibility_limit=visibility_limit,
            include_orphaned=include_orphaned,
        )
    ]
    if not include_merged:
        streams = [
            stream for stream in streams
            if stream.get("state", "") != "merged"
        ]
    if state_filter:
        streams = [
            stream for stream in streams
            if stream.get("state", "") == state_filter
        ]
    if branch_filter:
        streams = [
            stream for stream in streams
            if stream.get("branch", "") == branch_filter
        ]
    if repo_root_filter:
        streams = [
            stream for stream in streams
            if stream.get("repo_root", "") == repo_root_filter
        ]
    return streams


def _resolve_stream_payload(streams: list[dict], *, stream_ident: str = "",
                            repo_root: str = "", branch: str = "",
                            task_id: str = "") -> tuple[dict | None, str]:
    if task_id:
        matches = [
            stream for stream in streams
            if task_id in member_task_ids_for_stream(stream)
        ]
        if len(matches) == 1:
            return matches[0], ""
        if not matches:
            return None, f"Stream not found for task: {task_id}"
        return None, (
            "Multiple streams reference that task; provide stream id or "
            "repo_root + branch"
        )

    stream_ident = str(stream_ident or "").strip()
    repo_root = str(repo_root or "").strip()
    branch = str(branch or "").strip()
    if not branch and stream_ident:
        if stream_ident.startswith("stream:"):
            matches = [
                stream for stream in streams
                if stream.get("stream_id", "") == stream_ident
            ]
            if len(matches) == 1:
                return matches[0], ""
            return None, f"Stream not found: {stream_ident}"
        if "::" in stream_ident:
            repo_root, branch = stream_ident.split("::", 1)
        else:
            branch = stream_ident

    if not branch:
        return None, "Provide stream, branch, or task"

    matches = [
        stream for stream in streams
        if stream.get("branch", "") == branch
        and (not repo_root or stream.get("repo_root", "") == repo_root)
    ]
    if len(matches) == 1:
        return matches[0], ""
    if not matches:
        if repo_root:
            return None, f"Stream not found for {repo_root}::{branch}"
        return None, f"Stream not found for branch: {branch}"
    return None, (
        "Multiple streams match that branch; provide repo_root or stream id"
    )
