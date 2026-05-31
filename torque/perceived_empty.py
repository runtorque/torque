"""Perceived-empty tool-result channel detector.

This module watches Claude Code PostToolUse hook envelopes that Torque already
persists through event-ingest.  The anomaly it detects is not an actually empty
result: transcripts show the tool results are present on disk.  Instead, the
worker behaves as though its result channel went blank/dead immediately after
Torque observed non-empty tool results.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque
import hashlib
import json
import os
import re
import time

DEAD_CHANNEL_REASON_RE = re.compile(
    r"tool.?result|channel.*(dead|empty)|results?.*(blank|empty)|env.?fault",
    re.IGNORECASE,
)

_DEFAULT_THRESHOLD = 5
_DEFAULT_WINDOW_SECONDS = 120
_MAX_WINDOW_CALLS = 50

_COMMON_TOOLSEARCH_TOKENS = {
    "select",
    "mcp",
    "torque",
    "tool",
    "tools",
    "max",
    "results",
    "result",
}


@dataclass(slots=True)
class ToolCallEvidence:
    """Compact evidence retained for detector windows and persisted episodes."""

    timestamp: float
    cursor: int
    cell_id: str
    group: str
    agent_name: str
    session_id: str
    transcript_path: str
    tool_name: str
    input_hash: str
    input_family: str
    result_len: int
    result_content_type: str
    has_result_payload: bool
    result_non_empty: bool
    trigger_text: str = ""

    def to_record(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "cursor": self.cursor,
            "tool_name": self.tool_name,
            "input_hash": self.input_hash,
            "input_family": self.input_family,
            "result_len": self.result_len,
            "result_content_type": self.result_content_type,
            "has_result_payload": self.has_result_payload,
            "result_non_empty": self.result_non_empty,
        }


@dataclass(slots=True)
class PerceivedEmptyEpisode:
    """Episode record emitted when the behavioral signature crosses threshold."""

    timestamp: float
    cell_id: str
    group: str
    agent_name: str
    session_id: str
    transcript_path: str
    trigger_reason: str
    confidence: str
    threshold_n: int
    window_seconds: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def to_db_record(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "cell_id": self.cell_id,
            "group_name": self.group,
            "agent_name": self.agent_name,
            "session_id": self.session_id,
            "transcript_path": self.transcript_path,
            "trigger_reason": self.trigger_reason,
            "confidence": self.confidence,
            "threshold_n": self.threshold_n,
            "window_seconds": self.window_seconds,
            "tool_calls_json": json.dumps(
                self.tool_calls,
                separators=(",", ":"),
                sort_keys=True,
            ),
        }


def coerce_perceived_empty_threshold(value: Any) -> int:
    try:
        threshold = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_THRESHOLD
    return max(2, min(25, threshold))


def coerce_perceived_empty_window_seconds(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_WINDOW_SECONDS
    return max(10, min(3600, seconds))


def claude_transcript_path(cwd: str, session_id: str) -> str:
    """Return Claude Code's best-known transcript path for ``cwd/session_id``.

    Claude stores project directories under ``~/.claude/projects`` with path
    separators and punctuation replaced by dashes (for example
    ``/repo/.torque/worktrees/a`` -> ``-repo--torque-worktrees-a``).
    Hook payloads often include ``transcript_path`` already; this helper is the
    fallback requested for turnkey evidence records.
    """

    session = str(session_id or "").strip()
    directory = str(cwd or "").strip()
    if not session or not directory:
        return ""
    try:
        directory = os.path.realpath(os.path.expanduser(directory))
    except Exception:
        directory = os.path.expanduser(directory)
    mangled = re.sub(r"[^A-Za-z0-9_-]", "-", directory)
    return str(Path.home() / ".claude" / "projects" / mangled / f"{session}.jsonl")


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


def stable_input_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8", "replace")).hexdigest()


def _result_payload(raw: dict[str, Any]) -> tuple[bool, Any]:
    for key in (
        "tool_response",
        "toolResponse",
        "toolUseResult",
        "tool_use_result",
        "tool_output",
        "result",
        "response",
    ):
        if key in raw:
            return True, raw.get(key)
    return False, None


def _string_payload_len(value: str) -> int:
    return len(value.encode("utf-8", "replace"))


def classify_result_payload(value: Any) -> tuple[int, str]:
    """Return ``(payload_length, content_type)`` without text-only bias.

    ToolSearch returns content blocks of type ``tool_reference``.  Those are
    non-empty results even though they are not text, so this routine counts the
    real payload rather than just concatenating text fields.
    """

    if value is None:
        return 0, "absent"
    if isinstance(value, str):
        return _string_payload_len(value), "text" if value else "empty"
    if isinstance(value, bytes):
        return len(value), "other" if value else "empty"
    if isinstance(value, list):
        block_types: list[str] = []
        has_text = False
        has_tool_reference = False
        for item in value:
            if isinstance(item, dict):
                typ = str(item.get("type") or "").strip()
                if typ:
                    block_types.append(typ)
                if typ == "text" or isinstance(item.get("text"), str):
                    if str(item.get("text") or ""):
                        has_text = True
                if typ == "tool_reference":
                    has_tool_reference = True
            elif isinstance(item, str) and item:
                has_text = True
        payload_len = _string_payload_len(_canonical_json(value))
        if payload_len <= 2:
            return 0, "empty"
        unique_types = {t for t in block_types if t}
        if has_tool_reference and not has_text and unique_types <= {"tool_reference"}:
            return payload_len, "tool_reference"
        if has_text and not has_tool_reference:
            return payload_len, "text"
        if has_text or has_tool_reference:
            return payload_len, "mixed"
        return payload_len, "other"
    if isinstance(value, dict):
        # Claude Bash hook results are dicts with stdout/stderr strings.  MCP
        # references may be wrapped in content arrays.  Preserve those as their
        # semantic type for later secondary analysis.
        if isinstance(value.get("content"), list):
            return classify_result_payload(value.get("content"))
        if isinstance(value.get("text"), str):
            length = _string_payload_len(value.get("text") or "")
            return length, "text" if length else "empty"
        stdout = value.get("stdout")
        stderr = value.get("stderr")
        if isinstance(stdout, str) or isinstance(stderr, str):
            length = _string_payload_len(str(stdout or "") + str(stderr or ""))
            if length:
                return length, "text"
        matches = value.get("matches")
        if isinstance(matches, list) and matches:
            return _string_payload_len(_canonical_json(value)), "tool_reference"
        payload_len = _string_payload_len(_canonical_json(value))
        if payload_len <= 2:
            return 0, "empty"
        return payload_len, "other"
    payload_len = _string_payload_len(_canonical_json(value))
    return payload_len, "other" if payload_len else "empty"


def _tool_input(raw: dict[str, Any]) -> Any:
    if "tool_input" in raw:
        return raw.get("tool_input")
    if "input" in raw:
        return raw.get("input")
    if "arguments" in raw:
        return raw.get("arguments")
    return None


def _toolsearch_tokens(query: str) -> set[str]:
    normalized = query.replace("__", "_").replace("-", "_").lower()
    tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", normalized)
        if token and token not in _COMMON_TOOLSEARCH_TOKENS
    }
    expanded: set[str] = set(tokens)
    for token in list(tokens):
        if "_" in token:
            expanded.update(part for part in token.split("_") if part)
    return {token for token in expanded if token not in _COMMON_TOOLSEARCH_TOKENS}


def _is_torque_reporting_toolsearch(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    query = str(value.get("query") or "")
    if not query:
        return False
    lowered = query.lower()
    if "torque" not in lowered:
        return False
    tokens = _toolsearch_tokens(query)
    return bool(tokens.intersection({
        "progress",
        "derive",
        "done",
        "blocked",
        "error",
        "ask",
        "verify",
        "context",
        "memory",
        "publish",
        "message",
        "user",
        "ready",
    }))


def _is_trivial_bash_probe(command: str) -> bool:
    cmd = str(command or "").strip().lower()
    if not cmd:
        return False
    if len(cmd) > 220:
        return False
    if re.search(r"\b(output_marker|retry_check|post_flag_probe|hello-test-output|marker-)\b", cmd):
        return True
    if re.fullmatch(r"(?:builtin\s+)?(?:echo|printf)\b.*", cmd, re.DOTALL):
        return True
    if re.fullmatch(r"date(?:\s+\+[%a-z0-9:_-]+)?", cmd):
        return True
    if re.fullmatch(r"pwd(?:\s*;\s*)?", cmd):
        return True
    if "/nonexistent" in cmd and ("echo" in cmd or "stderr" in cmd):
        return True
    return False


def input_family(tool_name: str, value: Any) -> str:
    """Return a coarse key for redundant-probe detection."""

    tool = str(tool_name or "").strip()
    if tool == "ToolSearch" and _is_torque_reporting_toolsearch(value):
        return "toolsearch:torque-reporting"
    if tool == "Bash" and isinstance(value, dict):
        if _is_trivial_bash_probe(str(value.get("command") or "")):
            return "bash:trivial-sanity-probe"
    return f"{tool}:sha256:{stable_input_hash(value)[:16]}"


def _trigger_text(tool_name: str, value: Any) -> str:
    tool = str(tool_name or "")
    if not (
        tool.endswith("torque_blocked")
        or tool.endswith("torque_error")
        or tool.endswith("__torque_blocked")
        or tool.endswith("__torque_error")
    ):
        return ""
    if not isinstance(value, dict):
        return ""
    for key in ("reason", "message", "error", "description"):
        text = str(value.get(key) or "").strip()
        if text:
            return text
    return ""


def observation_from_post_tool_use(
    envelope: dict[str, Any],
    *,
    cursor: int = 0,
    cell: Any = None,
) -> ToolCallEvidence | None:
    """Extract detector evidence from one event-ingest envelope."""

    raw = dict((envelope or {}).get("raw") or {})
    hook = str(raw.get("hook_event_name") or raw.get("type") or "")
    if hook != "PostToolUse":
        return None
    tool_name = str(raw.get("tool_name") or raw.get("name") or "").strip()
    if not tool_name:
        return None
    headers = dict((envelope or {}).get("headers") or {})
    cell_id = str(
        headers.get("X-Torque-Cell-Id")
        or getattr(cell, "id", "")
        or ""
    ).strip()
    if not cell_id:
        return None
    try:
        timestamp = float((envelope or {}).get("received_at") or time.time())
    except (TypeError, ValueError):
        timestamp = time.time()
    tool_input = _tool_input(raw)
    has_result, result = _result_payload(raw)
    result_len, result_content_type = classify_result_payload(result)
    session_id = str(raw.get("session_id") or getattr(cell, "agent_session_id", "") or "")
    cwd = str(raw.get("cwd") or getattr(cell, "current_path", "") or getattr(cell, "directory", "") or "")
    transcript_path = str(raw.get("transcript_path") or "").strip()
    if not transcript_path:
        transcript_path = claude_transcript_path(cwd, session_id)
    group = str(getattr(cell, "group", "") or raw.get("group", "") or "")
    agent_name = str(getattr(cell, "name", "") or raw.get("agent_name", "") or "")
    return ToolCallEvidence(
        timestamp=timestamp,
        cursor=max(0, int(cursor or 0)),
        cell_id=cell_id,
        group=group,
        agent_name=agent_name,
        session_id=session_id,
        transcript_path=transcript_path,
        tool_name=tool_name,
        input_hash=stable_input_hash(tool_input),
        input_family=input_family(tool_name, tool_input),
        result_len=result_len,
        result_content_type=result_content_type,
        has_result_payload=has_result,
        result_non_empty=bool(has_result and result_len > 0),
        trigger_text=_trigger_text(tool_name, tool_input),
    )


class PerceivedEmptyDetector:
    """Per-cell rolling-window detector for perceived-empty episodes."""

    def __init__(self):
        self._windows: dict[str, Deque[ToolCallEvidence]] = {}
        self._last_episode_at: dict[str, float] = {}

    def ingest_envelope(
        self,
        envelope: dict[str, Any],
        *,
        cursor: int = 0,
        cell: Any = None,
        threshold_n: int = _DEFAULT_THRESHOLD,
        window_seconds: int = _DEFAULT_WINDOW_SECONDS,
    ) -> PerceivedEmptyEpisode | None:
        observation = observation_from_post_tool_use(
            envelope,
            cursor=cursor,
            cell=cell,
        )
        if observation is None:
            return None
        return self.ingest_observation(
            observation,
            threshold_n=threshold_n,
            window_seconds=window_seconds,
        )

    def ingest_observation(
        self,
        observation: ToolCallEvidence,
        *,
        threshold_n: int = _DEFAULT_THRESHOLD,
        window_seconds: int = _DEFAULT_WINDOW_SECONDS,
    ) -> PerceivedEmptyEpisode | None:
        threshold = coerce_perceived_empty_threshold(threshold_n)
        window = coerce_perceived_empty_window_seconds(window_seconds)
        calls = self._windows.setdefault(observation.cell_id, deque(maxlen=_MAX_WINDOW_CALLS))
        calls.append(observation)
        cutoff = observation.timestamp - window
        while calls and calls[0].timestamp < cutoff:
            calls.popleft()

        episode = self._evaluate(observation, list(calls), threshold, window)
        if episode is None:
            return None
        # Suppress repeated rows for the same live episode.  A later recurrence
        # after the configured observation window still produces new evidence.
        last = self._last_episode_at.get(observation.cell_id, 0)
        if last and observation.timestamp - last < window:
            return None
        self._last_episode_at[observation.cell_id] = observation.timestamp
        return episode

    def _evaluate(
        self,
        observation: ToolCallEvidence,
        calls: list[ToolCallEvidence],
        threshold: int,
        window: int,
    ) -> PerceivedEmptyEpisode | None:
        reason_text = observation.trigger_text
        if reason_text and DEAD_CHANNEL_REASON_RE.search(reason_text):
            prior = [call for call in calls if call is not observation]
            non_empty_prior = any(call.result_non_empty for call in prior)
            confidence = "high" if non_empty_prior else "behavioral"
            return self._episode(
                observation,
                calls,
                trigger_reason=(
                    "dead-channel blocker/error after non-empty tool results"
                    if non_empty_prior else "dead-channel blocker/error reason"
                ),
                confidence=confidence,
                threshold=threshold,
                window=window,
            )

        family = observation.input_family
        if not family:
            return None
        if not (observation.result_non_empty or not observation.has_result_payload):
            return None
        suffix: list[ToolCallEvidence] = []
        for call in reversed(calls):
            if call.input_family != family:
                if suffix:
                    break
                continue
            if not (call.result_non_empty or not call.has_result_payload):
                break
            suffix.append(call)
        suffix.reverse()
        if len(suffix) >= threshold:
            has_payload = any(call.has_result_payload for call in suffix)
            all_non_empty = all(
                call.result_non_empty
                for call in suffix
                if call.has_result_payload
            )
            if has_payload and all_non_empty:
                confidence = "high"
                trigger = f"{len(suffix)} redundant non-empty {family} probes"
            else:
                confidence = "behavioral"
                trigger = f"{len(suffix)} redundant {family} probes"
            return self._episode(
                observation,
                suffix,
                trigger_reason=trigger,
                confidence=confidence,
                threshold=threshold,
                window=window,
            )
        return None

    @staticmethod
    def _episode(
        observation: ToolCallEvidence,
        calls: list[ToolCallEvidence],
        *,
        trigger_reason: str,
        confidence: str,
        threshold: int,
        window: int,
    ) -> PerceivedEmptyEpisode:
        return PerceivedEmptyEpisode(
            timestamp=observation.timestamp,
            cell_id=observation.cell_id,
            group=observation.group,
            agent_name=observation.agent_name,
            session_id=observation.session_id,
            transcript_path=observation.transcript_path,
            trigger_reason=trigger_reason,
            confidence=confidence,
            threshold_n=threshold,
            window_seconds=window,
            tool_calls=[call.to_record() for call in calls],
        )


def _parse_iso_timestamp(value: str) -> float:
    text = str(value or "").strip()
    if not text:
        return time.time()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return time.time()


def transcript_observations(
    path: str | Path,
    *,
    cell_id: str,
    group: str = "",
    agent_name: str = "",
) -> list[ToolCallEvidence]:
    """Replay a Claude Code JSONL transcript as detector observations.

    This is used by regression tests against the real Atlas transcripts.  It
    pairs assistant ``tool_use`` blocks with following user ``tool_result``
    blocks and mirrors the same evidence shape that live PostToolUse hooks
    provide.
    """

    transcript = Path(path).expanduser()
    pending: dict[str, tuple[str, Any]] = {}
    observations: list[ToolCallEvidence] = []
    if not transcript.exists():
        return observations
    for line in transcript.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        session_id = str(event.get("sessionId") or event.get("session_id") or "")
        timestamp = _parse_iso_timestamp(str(event.get("timestamp") or ""))
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "tool_use":
                tool_id = str(block.get("id") or "")
                if tool_id:
                    pending[tool_id] = (
                        str(block.get("name") or ""),
                        block.get("input"),
                    )
            elif block_type == "tool_result":
                tool_id = str(block.get("tool_use_id") or "")
                if not tool_id or tool_id not in pending:
                    continue
                tool_name, tool_input = pending.pop(tool_id)
                has_result = "content" in block or "toolUseResult" in event
                result = block.get("content") if "content" in block else event.get("toolUseResult")
                result_len, result_content_type = classify_result_payload(result)
                observations.append(ToolCallEvidence(
                    timestamp=timestamp,
                    cursor=len(observations) + 1,
                    cell_id=str(cell_id or ""),
                    group=str(group or ""),
                    agent_name=str(agent_name or ""),
                    session_id=session_id,
                    transcript_path=str(transcript),
                    tool_name=tool_name,
                    input_hash=stable_input_hash(tool_input),
                    input_family=input_family(tool_name, tool_input),
                    result_len=result_len,
                    result_content_type=result_content_type,
                    has_result_payload=has_result,
                    result_non_empty=bool(has_result and result_len > 0),
                    trigger_text=_trigger_text(tool_name, tool_input),
                ))
    return observations


def replay_transcript_for_episode(
    path: str | Path,
    *,
    cell_id: str,
    group: str = "",
    agent_name: str = "",
    threshold_n: int = _DEFAULT_THRESHOLD,
    window_seconds: int = _DEFAULT_WINDOW_SECONDS,
) -> list[PerceivedEmptyEpisode]:
    detector = PerceivedEmptyDetector()
    episodes: list[PerceivedEmptyEpisode] = []
    for observation in transcript_observations(
        path,
        cell_id=cell_id,
        group=group,
        agent_name=agent_name,
    ):
        episode = detector.ingest_observation(
            observation,
            threshold_n=threshold_n,
            window_seconds=window_seconds,
        )
        if episode is not None:
            episodes.append(episode)
    return episodes
