"""Server-side orchestration for asynchronous board sync pushes/pulls.

The provider package owns external-board details.  This module owns the daemon
plumbing around it: coalescing local board mutations into background pushes,
persisting task-visible sync state, and exposing user-initiated preflight/pull
helpers for the WebSocket command surface.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
from typing import Awaitable, Callable, Iterable

from .board_sync import get_provider, normalize_provider_name
from .config import log
from .state import ARCHIVED_LANE, BoardTask, MatrixState

PanelEventCallback = Callable[[str, str, str, str, str, str], None]
ToastCallback = Callable[[str, str], Awaitable[None] | None]
ProviderFactory = Callable[[str], object]

_SYNC_RELEVANT_FIELDS = {
    "task",
    "description",
    "labels",
    "agent_id",
    "assigned_engineer_id",
    "assigned_architect_id",
    "lane",
    "status",
    "provider",
    "external_id",
    "external_url",
    "board_sync",
}
_PULL_APPLY_FIELDS = {
    "task",
    "description",
    "labels",
    "agent_id",
    "assigned_engineer_id",
    "assigned_architect_id",
    "lane",
    "status",
    "provider",
    "external_id",
    "external_url",
}
_AUTO_TRACK_VALUES = {"all", "all_tasks", "auto", "create", "created", "new"}
_LINKED_ONLY_VALUES = {"linked", "existing", "manual", "false", "off", "none"}
_AUTO_DEBOUNCE_SECONDS = 1.0
_AUTO_MAX_COALESCE_SECONDS = 5.0
_AUTO_RETRY_BACKOFF_SECONDS = (30.0, 120.0, 300.0, 900.0)
_AUTO_RETRY_MAX_ATTEMPTS = 5
_AUTO_RETRY_METADATA_KEYS = (
    "auto_retry_attempts",
    "next_retry_at",
    "next_retry_at_iso",
    "retry_backoff_seconds",
)
_BOARD_SYNC_ERROR_METADATA_KEYS = (
    "last_error_at",
    "last_error_provider",
    "last_error_attempt",
    "last_error_reasons",
    "last_error_current",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_from_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _task_title(task: BoardTask | None) -> str:
    title = str(getattr(task, "task", "") or "").strip()
    return title[:80] if title else str(getattr(task, "id", "") or "task")


def _maybe_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _provider_nested_settings(group_settings, provider_name: str) -> dict:
    provider_name = normalize_provider_name(provider_name)
    candidates = []
    if provider_name and provider_name != "none":
        candidates.append(f"board_sync_{provider_name}")
    # V1 ships GitHub settings, but keep the lookup generic for future
    # providers that follow the same naming convention.
    candidates.append("board_sync_github")
    for attr in candidates:
        value = getattr(group_settings, attr, None)
        if isinstance(value, dict):
            return value
    return {}


def _github_repo_owner(repo: object) -> str:
    """Return the owner portion from a GitHub ``owner/repo``-style value."""
    text = str(repo or "").strip()
    if not text:
        return ""
    if text.startswith("git@github.com:"):
        text = text.split(":", 1)[1]
    marker = "github.com/"
    if marker in text:
        text = text.split(marker, 1)[1]
    text = text.strip().strip("/")
    if text.endswith(".git"):
        text = text[:-4]
    parts = [part.strip() for part in text.split("/") if part.strip()]
    if len(parts) < 2:
        return ""
    return parts[0].lstrip("@")


def _github_project_owner_queries(nested: dict, explicit_owner: str = "") -> list[str]:
    """Owners to query for GitHub Projects in the config dropdown.

    The default path should not require a manual owner.  Query the
    authenticated user's projects first, then the configured repo owner, then
    any advanced/manual owner value as a fallback.
    """
    owners: list[str] = []
    seen: set[str] = set()

    def add(owner: object) -> None:
        value = str(owner or "").strip()
        if not value:
            return
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        owners.append(value)

    add("@me")
    add(_github_repo_owner(nested.get("github_repo")))
    add(explicit_owner)
    add(nested.get("github_project_owner"))
    return owners


def _command_settings(group_settings, provider_name: str = "", overrides=None):
    """Return a temporary settings object for read-only helper commands.

    Group Settings helpers are often used before the modal is saved.  The
    frontend can pass the current form values as ``overrides`` so provider
    preflight/listing commands inspect the user's draft configuration without
    mutating persisted group settings.
    """
    settings = replace(group_settings)
    settings.board_sync_github = copy.deepcopy(
        getattr(group_settings, "board_sync_github", {}) or {}
    )
    overrides = overrides if isinstance(overrides, dict) else {}
    if provider_name:
        settings.board_sync_provider = normalize_provider_name(provider_name)
    if "board_sync_provider" in overrides:
        settings.board_sync_provider = normalize_provider_name(
            overrides.get("board_sync_provider")
        )
    if "provider" in overrides:
        settings.board_sync_provider = normalize_provider_name(
            overrides.get("provider")
        )
    if "board_sync_enabled" in overrides:
        settings.board_sync_enabled = _maybe_bool(
            overrides.get("board_sync_enabled")
        )
    nested = overrides.get("board_sync_github")
    if isinstance(nested, dict):
        merged = dict(settings.board_sync_github)
        merged.update(copy.deepcopy(nested))
        settings.board_sync_github = merged
    # Accept direct GitHub keys too; useful for lightweight CLI/API callers.
    direct = {
        key: copy.deepcopy(value)
        for key, value in overrides.items()
        if str(key).startswith("github_")
    }
    if direct:
        merged = dict(settings.board_sync_github)
        merged.update(direct)
        settings.board_sync_github = merged
    return settings


def group_auto_tracks_new_tasks(group_settings) -> bool:
    """Return whether unlinked newly-created tasks should be pushed.

    Step 2's provider settings do not add a first-class dataclass field for
    this yet, so this intentionally supports both a future generic
    ``auto_track_new_tasks`` boolean and provider-nested sync-default values.
    """
    for attr in ("auto_track_new_tasks", "board_sync_auto_track_new_tasks"):
        if hasattr(group_settings, attr):
            return _maybe_bool(getattr(group_settings, attr))
    provider_name = normalize_provider_name(
        getattr(group_settings, "board_sync_provider", "")
    )
    nested = _provider_nested_settings(group_settings, provider_name)
    for key in ("auto_track_new_tasks", "board_sync_auto_track_new_tasks"):
        if key in nested:
            return _maybe_bool(nested.get(key))
    sync_default = str(
        nested.get("sync_default")
        or nested.get("github_sync_default")
        or ""
    ).strip().lower()
    if sync_default in _AUTO_TRACK_VALUES:
        return True
    if sync_default in _LINKED_ONLY_VALUES:
        return False
    return provider_name == "github"


def task_allows_auto_create_for_board_sync(task: BoardTask | None) -> bool:
    """Return whether an unlinked task is eligible for automatic issue create.

    The GitHub mirror is the product board, not an orchestration trace.  Auto
    create therefore defaults to top-level user/architect product tasks only.
    Derived/review/ask/scheduled internal nodes can still opt in with
    task-level ``board_sync.enabled = true`` (handled by
    ``task_is_tracked_for_board_sync``).
    """
    if not task:
        return False
    sync = getattr(task, "board_sync", {}) or {}
    if isinstance(sync, dict):
        if sync.get("auto_track") is False or sync.get("auto_sync_excluded"):
            return False
        if _maybe_bool(sync.get("enabled")):
            return True
    if str(getattr(task, "parent_task_id", "") or "").strip():
        return False
    try:
        if int(getattr(task, "pipeline_depth", 0) or 0) != 0:
            return False
    except (TypeError, ValueError):
        return False
    labels = {
        str(label or "").strip().casefold()
        for label in (getattr(task, "labels", []) or [])
    }
    if "torque:derived" in labels:
        return False
    if str(getattr(task, "created_by_engineer_id", "") or "").strip():
        return False
    return True


def task_is_tracked_for_board_sync(task: BoardTask | None) -> bool:
    if not task:
        return False
    sync = getattr(task, "board_sync", {}) or {}
    if isinstance(sync, dict):
        # Task-level opt-out is authoritative.  A task can remain linked to a
        # GitHub issue (provider metadata/external_url preserved for display)
        # while explicitly opting out of automatic board sync.
        if sync.get("enabled") is False:
            return False
        if _maybe_bool(sync.get("enabled")):
            return True
        if str(sync.get("provider", "") or "").strip():
            return True
    return any(
        str(getattr(task, attr, "") or "").strip()
        for attr in ("provider", "external_id", "external_url")
    )


def board_sync_fields_trigger(fields: Iterable[str]) -> bool:
    return bool(_SYNC_RELEVANT_FIELDS.intersection({str(f) for f in fields or []}))


def _normalize_match_value(value: object) -> str:
    return str(value or "").strip().lower()


def _item_marker_task_id(item: dict) -> str:
    marker = item.get("torque_marker") if isinstance(item.get("torque_marker"), dict) else {}
    return str(
        item.get("matched_task_id")
        or item.get("task_id")
        or marker.get("task_id")
        or ""
    ).strip()


def _task_match_summary(task: BoardTask | None) -> dict:
    if not task:
        return {}
    return {
        "id": task.id,
        "task": task.task,
        "group": task.group,
        "lane": task.lane,
        "provider": task.provider,
        "external_id": task.external_id,
        "external_url": task.external_url,
    }


@dataclass
class _DirtyItem:
    task_id: str
    explicit: bool = False
    force: bool = False
    reasons: set[str] = field(default_factory=set)
    fields: set[str] = field(default_factory=set)
    enqueued_at: float = field(default_factory=time.time)
    first_marked_at: float = field(default_factory=time.time)
    last_marked_at: float = field(default_factory=time.time)
    due_at: float = 0.0
    retry_attempt: int = 0

    def merge(
        self,
        *,
        explicit: bool,
        force: bool,
        reason: str,
        fields: Iterable[str] | None = None,
        debounce_seconds: float = _AUTO_DEBOUNCE_SECONDS,
        max_coalesce_seconds: float = _AUTO_MAX_COALESCE_SECONDS,
        due_at: float | None = None,
    ) -> None:
        now = time.time()
        self.explicit = self.explicit or bool(explicit)
        self.force = self.force or bool(force)
        if reason:
            self.reasons.add(reason)
        for field_name in fields or []:
            value = str(field_name or "").strip()
            if value:
                self.fields.add(value)
        self.enqueued_at = now
        self.last_marked_at = now
        if self.explicit:
            self.due_at = now
            return
        trailing_due = now + max(0.0, float(debounce_seconds or 0.0))
        max_due = self.first_marked_at + max(0.0, float(max_coalesce_seconds or 0.0))
        auto_due = min(trailing_due, max_due)
        if due_at is not None:
            auto_due = max(auto_due, due_at)
        self.due_at = auto_due


class _DirtyQueueCompat:
    """Tiny compatibility facade for older tests that used manager.queue."""

    def __init__(self, manager: "BoardSyncManager"):
        self.manager = manager

    def qsize(self) -> int:
        return len(self.manager._dirty_by_task)

    async def join(self) -> None:
        await self.manager.join()


class BoardSyncManager:
    """Coalesce board mutations and push them through BoardSyncProvider.

    All MatrixState writes remain local and fast.  External I/O only happens in
    the background worker or explicit user-initiated command helpers.
    """

    def __init__(
        self,
        state: MatrixState,
        *,
        provider_factory: ProviderFactory | None = None,
        panel_event: PanelEventCallback | None = None,
        toast: ToastCallback | None = None,
        debounce_seconds: float = _AUTO_DEBOUNCE_SECONDS,
        max_coalesce_seconds: float = _AUTO_MAX_COALESCE_SECONDS,
        retry_backoff_seconds: Iterable[float] = _AUTO_RETRY_BACKOFF_SECONDS,
        max_auto_retry_attempts: int = _AUTO_RETRY_MAX_ATTEMPTS,
    ):
        self.state = state
        self.provider_factory = provider_factory or get_provider
        self.panel_event = panel_event
        self.toast = toast
        self.debounce_seconds = max(0.0, float(debounce_seconds or 0.0))
        self.max_coalesce_seconds = max(
            self.debounce_seconds,
            float(max_coalesce_seconds or 0.0),
        )
        backoffs = [max(0.0, float(value or 0.0))
                    for value in (retry_backoff_seconds or [])]
        self.retry_backoff_seconds = backoffs or list(_AUTO_RETRY_BACKOFF_SECONDS)
        self.max_auto_retry_attempts = max(0, int(max_auto_retry_attempts or 0))
        self.queue = _DirtyQueueCompat(self)
        self._dirty_by_task: dict[str, _DirtyItem] = {}
        self._task_fingerprints: dict[str, tuple] = {}
        self._task_locks: dict[str, asyncio.Lock] = {}
        self._target_locks: dict[str, asyncio.Lock] = {}
        self._provider_cache: dict[str, object] = {}
        self._wake_event: asyncio.Event | None = None
        self._idle_event: asyncio.Event | None = None
        self._inflight_count = 0
        self._observer_unregister: Callable[[], None] | None = None
        self._delta_observer_unregister: Callable[[], None] | None = None
        self._suppress_observer_depth = 0
        self._worker: asyncio.Task | None = None
        self._stopping = False
        self._group_sync_fingerprints: dict[str, tuple] = {}

    # -- Lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._worker and not self._worker.done():
            return
        self._stopping = False
        self._seed_task_fingerprints()
        self._seed_group_sync_fingerprints()
        self._wake_event = asyncio.Event()
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        registrar = getattr(self.state, "register_task_upsert_observer", None)
        if callable(registrar) and self._observer_unregister is None:
            self._observer_unregister = registrar(self._on_task_upsert)
        delta_registrar = getattr(self.state, "register_delta_observer", None)
        if callable(delta_registrar) and self._delta_observer_unregister is None:
            self._delta_observer_unregister = delta_registrar(
                self._on_delta,
                ops={"group_settings_update"},
            )
        self._recover_durable_queue()
        self._worker = asyncio.create_task(self._run(), name="board-sync-manager")

    async def stop(self) -> None:
        self._stopping = True
        if self._observer_unregister:
            try:
                self._observer_unregister()
            finally:
                self._observer_unregister = None
        if self._delta_observer_unregister:
            try:
                self._delta_observer_unregister()
            finally:
                self._delta_observer_unregister = None
        self._wake()
        worker = self._worker
        if not worker:
            return
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        finally:
            self._worker = None
            self._wake_event = None
            self._idle_event = None

    # -- Enqueue / state transitions --------------------------------------

    def enqueue_for_local_change(
        self,
        task_id: str,
        *,
        reason: str,
        fields: Iterable[str] | None = None,
        explicit: bool = False,
        force: bool = False,
    ) -> dict:
        if fields is not None and not board_sync_fields_trigger(fields) and not force:
            return {
                "ok": False,
                "queued": False,
                "reason": "no_sync_relevant_fields",
            }
        return self.enqueue_task(
            task_id,
            reason=reason,
            explicit=explicit,
            force=force,
            fields=fields,
        )

    def enqueue_task(
        self,
        task_id: str,
        *,
        reason: str = "mutation",
        explicit: bool = False,
        force: bool = False,
        fields: Iterable[str] | None = None,
    ) -> dict:
        tid = self._resolve_task_id(task_id)
        task = self.state.board_tasks.get(tid)
        if not task:
            return {"ok": False, "queued": False, "reason": "task_not_found"}
        ok, not_eligible_reason, provider_name = self._task_sync_eligibility(
            task,
            explicit=explicit,
            force=force,
        )
        if not ok:
            self._dirty_by_task.pop(tid, None)
            self._clear_stale_sync(task, not_eligible_reason, provider_name)
            return {
                "ok": False,
                "queued": False,
                "reason": not_eligible_reason,
            }

        existing_sync = getattr(task, "board_sync", {}) or {}
        retry_due_at = 0.0
        if not (explicit or force):
            retry_due_at = self._cooldown_due_at(existing_sync)

        sync = self._queued_sync(task, provider_name, reason=reason)
        if retry_due_at and not (explicit or force):
            for key in _AUTO_RETRY_METADATA_KEYS:
                if key in existing_sync:
                    sync[key] = existing_sync[key]
            sync["next_retry_at"] = retry_due_at
            sync["next_retry_at_iso"] = _iso_from_timestamp(retry_due_at)
        self._save_task_sync(task, sync)

        item = self._dirty_by_task.get(tid)
        if item:
            item.merge(
                explicit=explicit,
                force=force,
                reason=reason,
                fields=fields,
                debounce_seconds=self.debounce_seconds,
                max_coalesce_seconds=self.max_coalesce_seconds,
                due_at=retry_due_at or None,
            )
            self._wake()
            return {
                "ok": True,
                "queued": True,
                "coalesced": True,
                "task_id": tid,
                "reason": reason,
            }
        now = time.time()
        item = _DirtyItem(
            task_id=tid,
            explicit=bool(explicit),
            force=bool(force),
            reasons={reason} if reason else set(),
            fields={str(field or "").strip() for field in (fields or []) if str(field or "").strip()},
            enqueued_at=now,
            first_marked_at=now,
            last_marked_at=now,
            due_at=now if (explicit or force) else (
                max(
                    now + self.debounce_seconds,
                    retry_due_at,
                ) if retry_due_at else now + self.debounce_seconds
            ),
        )
        if not (explicit or force):
            item.due_at = min(
                item.due_at,
                item.first_marked_at + self.max_coalesce_seconds,
            ) if not retry_due_at else item.due_at
        self._dirty_by_task[tid] = item
        if self._idle_event:
            self._idle_event.clear()
        self._wake()
        return {
            "ok": True,
            "queued": True,
            "coalesced": False,
            "task_id": tid,
            "reason": reason,
        }

    def enqueue_group(
        self,
        group: str,
        *,
        explicit: bool = True,
        force: bool = False,
        reason: str = "group_sync",
    ) -> dict:
        group = str(group or "").strip()
        if not group:
            return {"ok": False, "type": "board_sync_group", "message": "group is required"}
        if group not in self.state.groups:
            return {"ok": False, "type": "board_sync_group", "message": "Group not found"}
        queued = []
        skipped = []
        for task in list(self.state.board_tasks.values()):
            if task.group != group:
                continue
            result = self.enqueue_task(
                task.id,
                reason=reason,
                explicit=explicit,
                force=force,
            )
            if result.get("queued"):
                queued.append(task.id)
            else:
                skipped.append({"task_id": task.id, "reason": result.get("reason", "")})
        return {
            "ok": True,
            "type": "board_sync_group",
            "group": group,
            "queued": queued,
            "queued_count": len(queued),
            "skipped_count": len(skipped),
            "skipped": skipped,
        }

    # -- Explicit command helpers -----------------------------------------

    async def preflight(
        self,
        group: str,
        *,
        provider_name: str = "",
        settings_overrides=None,
    ) -> dict:
        group = str(group or "").strip()
        if not group:
            result = {"ok": False, "type": "board_sync_preflight", "error": "group is required"}
            await self._notify_failure("board_sync_preflight_failed", group, result["error"])
            return result
        if group not in self.state.groups:
            result = {"ok": False, "type": "board_sync_preflight", "group": group, "error": "Group not found"}
            await self._notify_failure("board_sync_preflight_failed", group, result["error"])
            return result
        settings = _command_settings(
            self.state.get_group_settings(group),
            provider_name=provider_name,
            overrides=settings_overrides,
        )
        provider_name = normalize_provider_name(
            getattr(settings, "board_sync_provider", "")
        )
        provider = self.provider_factory(provider_name)
        try:
            provider_result = await provider.preflight(settings)
        except Exception as exc:
            log.exception("Board sync preflight failed for group %s", group)
            provider_result = {
                "ok": False,
                "provider": provider_name,
                "phase": "preflight",
                "error": str(exc) or type(exc).__name__,
            }
        result = {
            "type": "board_sync_preflight",
            "group": group,
            "provider": provider_name,
            **(provider_result if isinstance(provider_result, dict) else {"result": provider_result}),
        }
        self._attach_lane_status_suggestion(result, settings)
        if result.get("ok"):
            self._emit_panel(
                "board_sync_preflight_ok",
                group,
                f"Board sync preflight passed for {group}",
            )
        else:
            await self._notify_failure(
                "board_sync_preflight_failed",
                group,
                result.get("error") or "Board sync preflight failed",
            )
        return result

    async def list_projects(
        self,
        group: str,
        *,
        owner: str = "",
        provider_name: str = "",
        settings_overrides=None,
    ) -> dict:
        group = str(group or "").strip()
        if not group:
            return {
                "ok": False,
                "type": "board_sync_list_projects",
                "error": "group is required",
            }
        if group not in self.state.groups:
            return {
                "ok": False,
                "type": "board_sync_list_projects",
                "group": group,
                "error": "Group not found",
            }
        settings = _command_settings(
            self.state.get_group_settings(group),
            provider_name=provider_name,
            overrides=settings_overrides,
        )
        provider_name = normalize_provider_name(
            getattr(settings, "board_sync_provider", "")
        )
        nested = _provider_nested_settings(settings, provider_name)
        if provider_name == "github":
            owners = _github_project_owner_queries(nested, owner)
        else:
            if not owner:
                owner = str(nested.get("github_project_owner") or "").strip()
            owners = [owner] if owner else [""]
        provider = self.provider_factory(provider_name)
        raw_projects: list[dict] = []
        for project_owner in owners:
            try:
                owner_projects = await provider.list_projects(project_owner or None)
            except Exception as exc:
                log.exception(
                    "Board sync project list failed for group %s owner %s",
                    group,
                    project_owner or "@me",
                )
                owner_projects = [{
                    "ok": False,
                    "phase": "list_projects",
                    "provider": provider_name,
                    "owner": project_owner or "",
                    "error": str(exc) or type(exc).__name__,
                }]
            if not isinstance(owner_projects, list):
                owner_projects = [{
                    "ok": False,
                    "phase": "list_projects",
                    "provider": provider_name,
                    "owner": project_owner or "",
                    "error": "Provider returned invalid project list",
                }]
            for item in owner_projects:
                if not isinstance(item, dict):
                    continue
                project = dict(item)
                project.setdefault("query_owner", project_owner or "")
                if project.get("ok") is False:
                    project.setdefault("owner", project_owner or "")
                elif not str(project.get("owner") or "").strip():
                    project["owner"] = project_owner or ""
                raw_projects.append(project)

        errors: list[dict] = []
        projects: list[dict] = []
        seen_project_ids: set[str] = set()
        seen_project_fallbacks: set[tuple[str, str, str]] = set()
        for item in raw_projects:
            if item.get("ok") is False:
                errors.append(item)
                continue
            project_id = str(
                item.get("id")
                or item.get("node_id")
                or item.get("nodeId")
                or ""
            ).strip()
            if project_id:
                project_key = project_id.lower()
                if project_key in seen_project_ids:
                    continue
                seen_project_ids.add(project_key)
            else:
                fallback_key = (
                    str(item.get("owner") or "").strip().lower(),
                    str(item.get("number") or "").strip(),
                    str(item.get("name") or item.get("title") or "").strip().lower(),
                )
                if fallback_key in seen_project_fallbacks:
                    continue
                seen_project_fallbacks.add(fallback_key)
            projects.append(item)
        ok = not errors or bool(projects)
        return {
            "ok": ok,
            "type": "board_sync_list_projects",
            "group": group,
            "provider": provider_name,
            "owner": owner or "",
            "owners": owners,
            "projects": projects,
            "project_count": len(projects),
            "errors": errors,
            **({"error": errors[0].get("error", "")} if errors and not projects else {}),
        }

    async def pull_preview(self, task_id: str) -> dict:
        task, settings, provider_name, error = self._task_provider_context(task_id)
        if error:
            await self._notify_failure("board_pull_preview_failed", "", error)
            return {"ok": False, "type": "board_pull_preview", "error": error}
        provider = self.provider_factory(provider_name)
        try:
            preview = await provider.pull_task(task, settings)
        except Exception as exc:
            log.exception("Board pull preview failed for task %s", task.id)
            preview = {
                "ok": False,
                "phase": "pull_preview",
                "provider": provider_name,
                "error": str(exc) or type(exc).__name__,
            }
        result = {
            "type": "board_pull_preview",
            "task_id": task.id,
            "group": task.group,
            "provider": provider_name,
            **(preview if isinstance(preview, dict) else {"preview": preview}),
        }
        if not result.get("ok"):
            await self._notify_failure(
                "board_pull_preview_failed",
                task.group,
                result.get("error") or "Board pull preview failed",
                task_id=task.id,
            )
        return result

    async def pull_apply(self, task_id: str, fields: Iterable[str]) -> dict:
        task, settings, provider_name, error = self._task_provider_context(task_id)
        wanted = [str(field or "").strip() for field in (fields or [])]
        wanted = [field for field in wanted if field]
        if error:
            await self._notify_failure("board_pull_apply_failed", "", error)
            return {"ok": False, "type": "board_pull_apply", "error": error}
        provider = self.provider_factory(provider_name)
        try:
            applied = await provider.apply_pull(task, settings, wanted)
        except Exception as exc:
            log.exception("Board pull apply failed for task %s", task.id)
            applied = {
                "ok": False,
                "phase": "apply_pull",
                "provider": provider_name,
                "error": str(exc) or type(exc).__name__,
            }
        if not isinstance(applied, dict):
            applied = {"ok": False, "error": "Provider returned invalid apply payload"}
        if not applied.get("ok"):
            await self._notify_failure(
                "board_pull_apply_failed",
                task.group,
                applied.get("error") or "Board pull apply failed",
                task_id=task.id,
            )
            return {
                "type": "board_pull_apply",
                "task_id": task.id,
                "group": task.group,
                "provider": provider_name,
                **applied,
            }

        remote_fields = applied.get("fields")
        if not isinstance(remote_fields, dict):
            remote_fields = {}
        update_fields = {
            key: value
            for key, value in remote_fields.items()
            if key in _PULL_APPLY_FIELDS and key in wanted
        }
        current = self.state.board_tasks.get(task.id) or task
        sync = dict(getattr(current, "board_sync", {}) or {})
        sync.update({
            "version": 1,
            "provider": provider_name,
            "enabled": True,
            "sync_state": "idle",
            "last_pull_at": _now_iso(),
            "last_error": "",
        })
        update_fields["board_sync"] = sync
        with self.suppress_auto_sync_observer():
            self.state.board_update_task(task.id, **update_fields)
        refreshed = self.state.board_tasks.get(task.id)
        self._emit_panel(
            "board_pull_applied",
            refreshed.group if refreshed else task.group,
            f"Applied external board changes to '{_task_title(refreshed or task)}'",
            task_id=task.id,
        )
        await self._maybe_broadcast()
        return {
            "type": "board_pull_apply",
            "ok": True,
            "task_id": task.id,
            "group": task.group,
            "provider": provider_name,
            "applied_fields": sorted(update_fields.keys() - {"board_sync"}),
            "result": applied,
        }

    async def import_preview(self, group: str) -> dict:
        group = str(group or "").strip()
        if not group:
            result = {"ok": False, "type": "board_import_preview", "error": "group is required"}
            await self._notify_failure("board_import_preview_failed", group, result["error"])
            return result
        if group not in self.state.groups:
            result = {
                "ok": False,
                "type": "board_import_preview",
                "group": group,
                "error": "Group not found",
            }
            await self._notify_failure("board_import_preview_failed", group, result["error"])
            return result
        settings = self.state.get_group_settings(group)
        provider_name = normalize_provider_name(
            getattr(settings, "board_sync_provider", "")
        )
        if provider_name == "none":
            return {
                "ok": False,
                "type": "board_import_preview",
                "group": group,
                "provider": provider_name,
                "error": "Board sync provider is disabled",
            }
        if not bool(getattr(settings, "board_sync_enabled", False)):
            return {
                "ok": False,
                "type": "board_import_preview",
                "group": group,
                "provider": provider_name,
                "error": "Board sync is disabled",
            }
        provider = self.provider_factory(provider_name)
        try:
            raw_items = await provider.list_external_items(settings)
        except Exception as exc:
            log.exception("Board import preview failed for group %s", group)
            raw_items = [{
                "ok": False,
                "phase": "list_external_items",
                "provider": provider_name,
                "error": str(exc) or type(exc).__name__,
            }]
        if not isinstance(raw_items, list):
            raw_items = [{
                "ok": False,
                "phase": "list_external_items",
                "provider": provider_name,
                "error": "Provider returned invalid item list",
            }]

        errors = [
            dict(item)
            for item in raw_items
            if isinstance(item, dict) and item.get("ok") is False
        ]
        unlinked = []
        matched = []
        for raw in raw_items:
            if not isinstance(raw, dict) or raw.get("ok") is False:
                continue
            item = dict(raw)
            match = self._match_external_item(item, provider_name)
            if match:
                matched_item = dict(item)
                matched_item.update({
                    "matched": True,
                    "matched_by": match["matched_by"],
                    "matched_task_id": match["task"].id,
                    "matched_task": _task_match_summary(match["task"]),
                })
                matched.append(matched_item)
            else:
                item.setdefault("matched", False)
                unlinked.append(item)

        ok = not errors or bool(unlinked or matched)
        if errors and ok:
            log.warning(
                "Board import preview skipped %d provider item(s) for group %s",
                len(errors),
                group,
            )
        result = {
            "ok": ok,
            "type": "board_import_preview",
            "group": group,
            "provider": provider_name,
            "items": unlinked,
            "unlinked_items": unlinked,
            "matched": matched,
            "matched_items": matched,
            "item_count": len(unlinked),
            "unlinked_count": len(unlinked),
            "matched_count": len(matched),
            "errors": errors,
        }
        if not ok:
            await self._notify_failure(
                "board_import_preview_failed",
                group,
                errors[0].get("error") or "Board import preview failed",
            )
        return result

    # -- Worker -------------------------------------------------------------

    async def _run(self) -> None:
        while not self._stopping:
            item = await self._next_due_item()
            if item is None:
                continue
            try:
                self._inflight_count += 1
                await self._sync_one(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Board sync worker failed for task %s", item.task_id)
            finally:
                self._inflight_count = max(0, self._inflight_count - 1)
                self._set_idle_if_drained()

    async def join(self) -> None:
        """Wait until the current dirty map and in-flight syncs drain."""
        while self._dirty_by_task or self._inflight_count:
            event = self._idle_event
            if event is None:
                await asyncio.sleep(0.01)
                continue
            await event.wait()

    async def _next_due_item(self) -> _DirtyItem | None:
        while not self._stopping:
            now = time.time()
            if self._dirty_by_task:
                due_items = [
                    item for item in self._dirty_by_task.values()
                    if item.due_at <= now
                ]
                if due_items:
                    item = min(
                        due_items,
                        key=lambda candidate: (
                            candidate.due_at,
                            candidate.first_marked_at,
                            candidate.task_id,
                        ),
                    )
                    current = self._dirty_by_task.get(item.task_id)
                    if current is item:
                        self._dirty_by_task.pop(item.task_id, None)
                    return item
                next_due = min(item.due_at for item in self._dirty_by_task.values())
                timeout = max(0.0, next_due - now)
            else:
                self._set_idle_if_drained()
                timeout = None

            wake = self._wake_event
            if wake is None:
                await asyncio.sleep(timeout if timeout is not None else 0.05)
                continue
            try:
                if timeout is None:
                    await wake.wait()
                else:
                    await asyncio.wait_for(wake.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            wake.clear()
        return None

    async def _sync_one(self, item: _DirtyItem) -> None:
        tid = self._resolve_task_id(item.task_id)
        task = self.state.board_tasks.get(tid)
        if not task:
            return
        ok, reason, provider_name = self._task_sync_eligibility(
            task,
            explicit=getattr(item, "explicit", False),
            force=getattr(item, "force", False),
        )
        if not ok:
            self._clear_stale_sync(task, reason, provider_name)
            return
        settings = self.state.get_group_settings(task.group)
        provider = self._provider(provider_name)
        lock_key = self._lock_key(task, settings, provider_name)
        task_lock = self._task_locks.setdefault(tid, asyncio.Lock())
        target_lock = self._target_locks.setdefault(lock_key, asyncio.Lock())
        async with task_lock:
            async with target_lock:
                task = self.state.board_tasks.get(tid)
                if not task:
                    return
                sync = getattr(task, "board_sync", {}) or {}
                if isinstance(sync, dict) and sync.get("enabled") is False and not item.explicit:
                    return
                syncing = dict(getattr(task, "board_sync", {}) or {})
                previous_error = str(syncing.get("last_error", "") or "").strip()
                attempt = _safe_int(syncing.get("sync_attempt", 0)) + 1
                now_iso = _now_iso()
                syncing.update({
                    "version": 1,
                    "provider": provider_name,
                    "enabled": True,
                    "sync_state": "syncing",
                    "sync_attempt": attempt,
                    "last_attempt_at": now_iso,
                    "last_attempt_provider": provider_name,
                    "last_attempt_reason": ",".join(sorted(getattr(item, "reasons", set()) or [])),
                })
                if previous_error:
                    syncing["last_cleared_error"] = previous_error
                    syncing["last_error_cleared_at"] = now_iso
                    syncing["last_error_cleared_reason"] = "superseded_by_attempt"
                syncing["last_error"] = ""
                for key in _BOARD_SYNC_ERROR_METADATA_KEYS:
                    syncing.pop(key, None)
                self._save_task_sync(task, syncing)
                await self._maybe_broadcast()
                task = self.state.board_tasks.get(tid) or task
                try:
                    provider_sync = await provider.push_task(task, settings)
                except Exception as exc:
                    log.exception("Board sync push failed for task %s", tid)
                    provider_sync = self._error_sync(
                        task,
                        provider_name,
                        str(exc) or type(exc).__name__,
                    )
                await self._apply_provider_sync(tid, provider_name, provider_sync, item)

    async def _apply_provider_sync(
        self,
        task_id: str,
        provider_name: str,
        provider_sync,
        item: _DirtyItem,
    ) -> None:
        task = self.state.board_tasks.get(task_id)
        if not task:
            return
        if not isinstance(provider_sync, dict):
            provider_sync = self._error_sync(
                task,
                provider_name,
                "Provider returned invalid sync metadata.",
            )
        sync = dict(getattr(task, "board_sync", {}) or {})
        sync.update(provider_sync)
        sync.setdefault("version", 1)
        sync.setdefault("provider", provider_name)
        sync.setdefault("enabled", True)
        if sync.get("sync_state") not in {"idle", "queued", "syncing", "error"}:
            sync["sync_state"] = "idle"
        if sync.get("sync_state") != "error":
            task_sync = getattr(task, "board_sync", {}) or {}
            prior_error = str(
                task_sync.get("last_error", "")
                if isinstance(task_sync, dict)
                else ""
            ).strip()
            sync["sync_state"] = "idle"
            if prior_error:
                sync["last_cleared_error"] = prior_error
                sync["last_error_cleared_at"] = _now_iso()
                sync["last_error_cleared_reason"] = "success"
            sync["last_error"] = ""
            sync["last_success_at"] = _now_iso()
            for key in _BOARD_SYNC_ERROR_METADATA_KEYS:
                sync.pop(key, None)
            self._clear_retry_metadata(sync)
        else:
            attempt = _safe_int(sync.get("sync_attempt", 0))
            sync["last_error"] = str(
                sync.get("last_error") or sync.get("error") or "unknown error"
            ).strip()
            sync["last_error_at"] = _now_iso()
            sync["last_error_provider"] = provider_name
            sync["last_error_attempt"] = attempt
            sync["last_error_reasons"] = sorted(getattr(item, "reasons", set()) or [])
            sync["last_error_current"] = True
            if not self._schedule_retry_if_needed(
                task_id,
                provider_name,
                sync,
                item,
            ):
                self._clear_retry_metadata(sync)
        self._save_task_sync(task, sync)
        await self._maybe_broadcast()

        refreshed = self.state.board_tasks.get(task_id) or task
        if sync.get("sync_state") == "error":
            await self._notify_failure(
                "board_sync_failed",
                refreshed.group,
                f"Board sync failed for '{_task_title(refreshed)}': "
                f"{sync.get('last_error') or 'unknown error'}",
                task_id=task_id,
            )
        else:
            self._emit_panel(
                "board_sync_succeeded",
                refreshed.group,
                f"Board sync completed for '{_task_title(refreshed)}'",
                task_id=task_id,
            )
            if item.explicit:
                await self._emit_toast(
                    f"Board sync completed for '{_task_title(refreshed)}'",
                    "success",
                )

    # -- Internal helpers ---------------------------------------------------

    @contextmanager
    def suppress_auto_sync_observer(self):
        self._suppress_observer_depth += 1
        try:
            yield
        finally:
            self._suppress_observer_depth = max(
                0,
                self._suppress_observer_depth - 1,
            )

    def _provider(self, provider_name: str):
        provider_name = normalize_provider_name(provider_name)
        provider = self._provider_cache.get(provider_name)
        if provider is None:
            provider = self.provider_factory(provider_name)
            self._provider_cache[provider_name] = provider
        return provider

    def _task_is_schedule_created(self, task: BoardTask | None) -> bool:
        if not task:
            return False
        task_id = str(getattr(task, "id", "") or "").strip()
        if not task_id:
            return False
        for schedule in getattr(self.state, "schedules", {}).values():
            if str(getattr(schedule, "last_task_id", "") or "").strip() == task_id:
                return True
        return False

    def _wake(self) -> None:
        if self._wake_event is not None:
            self._wake_event.set()

    def _set_idle_if_drained(self) -> None:
        if (
                self._idle_event is not None
                and not self._dirty_by_task
                and self._inflight_count <= 0):
            self._idle_event.set()

    def _seed_task_fingerprints(self) -> None:
        self._task_fingerprints = {
            task.id: self._task_sync_fingerprint(task)
            for task in self.state.board_tasks.values()
        }

    def _seed_group_sync_fingerprints(self) -> None:
        self._group_sync_fingerprints = {
            group: self._group_sync_fingerprint(self.state.get_group_settings(group))
            for group in self.state.groups.keys()
        }

    @staticmethod
    def _group_sync_fingerprint(group_settings) -> tuple:
        provider_name = normalize_provider_name(
            getattr(group_settings, "board_sync_provider", "")
        )
        nested = _provider_nested_settings(group_settings, provider_name)
        try:
            nested_key = json.dumps(nested, sort_keys=True, default=str)
        except TypeError:
            nested_key = str(nested)
        return (
            ("provider", provider_name),
            ("enabled", bool(getattr(group_settings, "board_sync_enabled", False))),
            ("settings", nested_key),
        )

    def _on_delta(self, delta: dict) -> None:
        if self._suppress_observer_depth:
            return
        if not isinstance(delta, dict):
            return
        if delta.get("op") != "group_settings_update":
            return
        group = str(delta.get("name", "") or "").strip()
        if not group:
            return
        new_fingerprint = self._group_sync_fingerprint(
            self.state.get_group_settings(group)
        )
        old_fingerprint = self._group_sync_fingerprints.get(group)
        self._group_sync_fingerprints[group] = new_fingerprint
        if old_fingerprint == new_fingerprint:
            return
        self._reconcile_group_settings_change(group)

    def _recover_durable_queue(self) -> None:
        """Rehydrate persisted queued/retry sync state after daemon restart.

        The in-memory dirty map is only a coalescing implementation detail.
        Task-visible ``board_sync`` state is persisted in SQLite, so startup
        reconstructs dirty items for tasks that were queued/syncing or waiting
        for a bounded automatic retry when the previous daemon exited.
        """
        now = time.time()
        for task in list(self.state.board_tasks.values()):
            sync = getattr(task, "board_sync", {}) or {}
            if not isinstance(sync, dict) or not sync:
                continue
            state_name = str(sync.get("sync_state", "") or "").strip().lower()
            if state_name not in {"queued", "syncing", "error"}:
                continue
            ok, reason, provider_name = self._task_sync_eligibility(task)
            if not ok:
                self._clear_stale_sync(task, reason, provider_name)
                continue
            due_at = self._cooldown_due_at(sync)
            if state_name == "error":
                error = str(sync.get("last_error") or sync.get("error") or "").strip()
                if not due_at or not self._is_transient_error(error):
                    continue
            if state_name == "syncing":
                queued = self._queued_sync(
                    task,
                    provider_name,
                    reason="restart_recover",
                    clear_error=False,
                )
                queued["last_recovered_at"] = _now_iso()
                self._save_task_sync(task, queued)
            self._dirty_by_task[task.id] = _DirtyItem(
                task_id=task.id,
                explicit=False,
                force=False,
                reasons={"restart_recover" if state_name != "error" else "auto_retry"},
                fields=set(_SYNC_RELEVANT_FIELDS),
                enqueued_at=now,
                first_marked_at=now,
                last_marked_at=now,
                due_at=max(now + self.debounce_seconds, due_at or 0.0),
                retry_attempt=_safe_int(sync.get("auto_retry_attempts", 0)),
            )
        if self._dirty_by_task and self._idle_event:
            self._idle_event.clear()
            self._wake()

    def _reconcile_group_settings_change(self, group: str) -> None:
        for task in list(self.state.board_tasks.values()):
            if task.group != group:
                continue
            sync = getattr(task, "board_sync", {}) or {}
            if not isinstance(sync, dict) or not sync:
                continue
            state_name = str(sync.get("sync_state", "") or "").strip().lower()
            has_error = bool(str(sync.get("last_error", "") or "").strip())
            if state_name not in {"queued", "syncing", "error"} and not has_error:
                continue
            ok, reason, provider_name = self._task_sync_eligibility(task)
            if not ok:
                self._dirty_by_task.pop(task.id, None)
                self._clear_stale_sync(task, reason, provider_name)
                continue
            if state_name == "error" or has_error:
                self.enqueue_task(
                    task.id,
                    reason="provider_config_change",
                    fields={"board_sync"},
                )

    def _on_task_upsert(self, payload: dict) -> None:
        task_id = str((payload or {}).get("id", "") or "").strip()
        if not task_id:
            return
        task = self.state.board_tasks.get(task_id)
        if not task:
            return
        new_fingerprint = self._task_sync_fingerprint(task)
        old_fingerprint = self._task_fingerprints.get(task_id)
        self._task_fingerprints[task_id] = new_fingerprint
        if self._suppress_observer_depth:
            return
        if old_fingerprint == new_fingerprint:
            return
        changed_fields = self._changed_fingerprint_fields(
            old_fingerprint,
            new_fingerprint,
        )
        if not old_fingerprint:
            reason = "task_create"
        else:
            reason = "task_update"
        self.enqueue_for_local_change(
            task_id,
            reason=reason,
            fields=changed_fields or _SYNC_RELEVANT_FIELDS,
        )

    @staticmethod
    def _task_sync_fingerprint(task: BoardTask | None) -> tuple:
        if not task:
            return ()
        labels = tuple(
            str(label or "").strip()
            for label in (getattr(task, "labels", []) or [])
            if str(label or "").strip()
        )
        sync = getattr(task, "board_sync", {}) or {}
        sync = sync if isinstance(sync, dict) else {}
        enabled = sync.get("enabled", None)
        return (
            ("task", str(getattr(task, "task", "") or "")),
            ("description", str(getattr(task, "description", "") or "")),
            ("labels", labels),
            ("agent_id", str(getattr(task, "agent_id", "") or "")),
            ("assigned_engineer_id", str(getattr(task, "assigned_engineer_id", "") or "")),
            ("assigned_architect_id", str(getattr(task, "assigned_architect_id", "") or "")),
            ("lane", str(getattr(task, "lane", "") or "")),
            ("status", str(getattr(task, "status", "") or "")),
            ("provider", str(getattr(task, "provider", "") or "")),
            ("external_id", str(getattr(task, "external_id", "") or "")),
            ("external_url", str(getattr(task, "external_url", "") or "")),
            ("parent_task_id", str(getattr(task, "parent_task_id", "") or "")),
            ("pipeline_depth", int(getattr(task, "pipeline_depth", 0) or 0)),
            ("created_by_architect_id", str(getattr(task, "created_by_architect_id", "") or "")),
            ("created_by_engineer_id", str(getattr(task, "created_by_engineer_id", "") or "")),
            ("board_sync_enabled", enabled),
            ("board_sync_provider", str(sync.get("provider", "") or "")),
            ("board_sync_auto_track", sync.get("auto_track", None)),
            ("board_sync_auto_excluded", sync.get("auto_sync_excluded", None)),
        )

    @staticmethod
    def _changed_fingerprint_fields(old: tuple | None, new: tuple | None) -> set[str]:
        if not old or not new:
            return set(_SYNC_RELEVANT_FIELDS)
        old_map = dict(old)
        new_map = dict(new)
        changed = {
            key for key in set(old_map) | set(new_map)
            if old_map.get(key) != new_map.get(key)
        }
        fields = {
            key for key in changed
            if key in _SYNC_RELEVANT_FIELDS
        }
        if changed & {
            "parent_task_id",
            "pipeline_depth",
            "assigned_architect_id",
            "created_by_architect_id",
            "created_by_engineer_id",
            "board_sync_auto_track",
            "board_sync_auto_excluded",
        }:
            fields.add("board_sync")
        if changed & {"board_sync_enabled", "board_sync_provider"}:
            fields.add("board_sync")
        return fields

    @staticmethod
    def _cooldown_due_at(sync: dict) -> float:
        if not isinstance(sync, dict):
            return 0.0
        try:
            return max(0.0, float(sync.get("next_retry_at", 0.0) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _clear_retry_metadata(sync: dict) -> None:
        for key in _AUTO_RETRY_METADATA_KEYS:
            sync.pop(key, None)

    def _schedule_retry_if_needed(
        self,
        task_id: str,
        provider_name: str,
        sync: dict,
        item: _DirtyItem,
    ) -> bool:
        if getattr(item, "explicit", False) or getattr(item, "force", False):
            return False
        error = str(sync.get("last_error") or sync.get("error") or "").strip()
        if not self._is_transient_error(error):
            return False
        prior_attempts = int(sync.get("auto_retry_attempts", 0) or 0)
        attempt = max(prior_attempts, getattr(item, "retry_attempt", 0)) + 1
        if attempt > self.max_auto_retry_attempts:
            return False
        backoff = self.retry_backoff_seconds[
            min(attempt - 1, len(self.retry_backoff_seconds) - 1)
        ]
        due_at = time.time() + backoff
        sync["auto_retry_attempts"] = attempt
        sync["retry_backoff_seconds"] = backoff
        sync["next_retry_at"] = due_at
        sync["next_retry_at_iso"] = _iso_from_timestamp(due_at)
        retry_item = self._dirty_by_task.get(task_id)
        if retry_item:
            retry_item.retry_attempt = attempt
            retry_item.merge(
                explicit=False,
                force=False,
                reason="auto_retry",
                fields=_SYNC_RELEVANT_FIELDS,
                debounce_seconds=self.debounce_seconds,
                max_coalesce_seconds=self.max_coalesce_seconds,
                due_at=due_at,
            )
        else:
            now = time.time()
            self._dirty_by_task[task_id] = _DirtyItem(
                task_id=task_id,
                explicit=False,
                force=False,
                reasons={"auto_retry"},
                fields=set(_SYNC_RELEVANT_FIELDS),
                enqueued_at=now,
                first_marked_at=now,
                last_marked_at=now,
                due_at=due_at,
                retry_attempt=attempt,
            )
        if self._idle_event:
            self._idle_event.clear()
        self._wake()
        return True

    @staticmethod
    def _is_transient_error(error: str) -> bool:
        lower = str(error or "").casefold()
        if not lower:
            return False
        return any(
            needle in lower
            for needle in (
                "rate limit",
                "secondary rate limit",
                "timeout",
                "timed out",
                "temporarily",
                "temporary",
                "try again",
                "connection reset",
                "connection aborted",
                "connection refused",
                "network",
                "econn",
                "5xx",
                "500",
                "502",
                "503",
                "504",
                "abuse detection",
            )
        )

    def _resolve_task_id(self, task_id: str) -> str:
        resolver = getattr(self.state, "resolve_task_alias", None)
        tid = str(task_id or "").strip()
        if callable(resolver):
            return resolver(tid)
        return tid

    def _task_sync_eligibility(
        self,
        task: BoardTask | None,
        *,
        explicit: bool = False,
        force: bool = False,
    ) -> tuple[bool, str, str]:
        if not task:
            return False, "task_not_found", ""
        settings = self.state.get_group_settings(task.group)
        provider_name = normalize_provider_name(
            getattr(settings, "board_sync_provider", "")
        )
        if provider_name == "none":
            return False, "provider_disabled", provider_name
        if not bool(getattr(settings, "board_sync_enabled", False)):
            return False, "sync_disabled", provider_name
        sync = getattr(task, "board_sync", {}) or {}
        if isinstance(sync, dict) and sync.get("enabled") is False and not force:
            return False, "task_opted_out", provider_name
        tracked = task_is_tracked_for_board_sync(task)
        auto_track = (
            group_auto_tracks_new_tasks(settings)
            and task_allows_auto_create_for_board_sync(task)
            and not self._task_is_schedule_created(task)
        )
        if not (force or explicit or tracked or auto_track):
            return False, "task_not_tracked", provider_name
        return True, "", provider_name

    def _queued_sync(
        self,
        task: BoardTask,
        provider_name: str,
        *,
        reason: str = "",
        clear_error: bool = True,
    ) -> dict:
        sync = dict(getattr(task, "board_sync", {}) or {})
        prior_error = str(sync.get("last_error", "") or "").strip()
        sync.update({
            "version": 1,
            "provider": provider_name,
            "enabled": True,
            "sync_state": "queued",
            "queued_at": _now_iso(),
            "queued_reason": str(reason or "").strip(),
        })
        if clear_error:
            if prior_error:
                sync["last_cleared_error"] = prior_error
                sync["last_error_cleared_at"] = _now_iso()
                sync["last_error_cleared_reason"] = reason or "superseded"
            sync["last_error"] = ""
            for key in _BOARD_SYNC_ERROR_METADATA_KEYS:
                sync.pop(key, None)
            self._clear_retry_metadata(sync)
        return sync

    def _error_sync(self, task: BoardTask, provider_name: str, error: str) -> dict:
        sync = dict(getattr(task, "board_sync", {}) or {})
        sync.update({
            "version": 1,
            "provider": provider_name,
            "enabled": True,
            "sync_state": "error",
            "last_error": str(error or "").strip(),
        })
        return sync

    def _clear_stale_sync(
        self,
        task: BoardTask,
        reason: str,
        provider_name: str = "",
    ) -> None:
        sync = dict(getattr(task, "board_sync", {}) or {})
        if not sync:
            return
        state_name = str(sync.get("sync_state", "") or "").strip().lower()
        last_error = str(sync.get("last_error", "") or "").strip()
        if state_name not in {"queued", "syncing", "error"} and not last_error:
            return
        if last_error:
            sync["last_cleared_error"] = last_error
            sync["last_error_cleared_at"] = _now_iso()
            sync["last_error_cleared_reason"] = reason or "stale"
        sync["sync_state"] = "idle"
        sync["last_error"] = ""
        sync["last_reconciled_at"] = _now_iso()
        sync["last_reconciled_reason"] = reason or "stale"
        sync["last_reconciled_provider"] = normalize_provider_name(provider_name)
        for key in _BOARD_SYNC_ERROR_METADATA_KEYS:
            sync.pop(key, None)
        self._clear_retry_metadata(sync)
        self._save_task_sync(task, sync)

    def _save_task_sync(self, task: BoardTask, sync: dict) -> None:
        with self.suppress_auto_sync_observer():
            self.state.board_update_task(task.id, board_sync=sync)

    def _task_provider_context(self, task_id: str):
        tid = self._resolve_task_id(task_id)
        task = self.state.board_tasks.get(tid)
        if not task:
            return None, None, "", "Task not found"
        settings = self.state.get_group_settings(task.group)
        provider_name = normalize_provider_name(
            getattr(settings, "board_sync_provider", "")
        )
        if provider_name == "none":
            return task, settings, provider_name, "Board sync provider is disabled"
        if not bool(getattr(settings, "board_sync_enabled", False)):
            return task, settings, provider_name, "Board sync is disabled"
        return task, settings, provider_name, ""

    def _match_external_item(self, item: dict, provider_name: str) -> dict:
        provider_name = normalize_provider_name(
            item.get("provider") or provider_name
        )
        external_id = _normalize_match_value(item.get("external_id"))
        if not external_id:
            repo = str(item.get("issue_repo") or "").strip()
            number = str(item.get("issue_number") or "").strip()
            if repo and number:
                external_id = _normalize_match_value(f"{repo}#{number}")
        external_url = _normalize_match_value(
            item.get("external_url") or item.get("issue_url")
        )
        marker_task_id = _item_marker_task_id(item)
        marker_resolved = self._resolve_task_id(marker_task_id) if marker_task_id else ""

        if marker_resolved:
            task = self.state.board_tasks.get(marker_resolved)
            if task:
                return {"matched_by": "torque_marker", "task": task}

        for task in self.state.board_tasks.values():
            task_provider = normalize_provider_name(getattr(task, "provider", ""))
            if provider_name != "none" and task_provider not in {provider_name, "none"}:
                continue
            if external_id and _normalize_match_value(task.external_id) == external_id:
                return {"matched_by": "external_id", "task": task}
            if external_url and _normalize_match_value(task.external_url) == external_url:
                return {"matched_by": "external_url", "task": task}

            sync = getattr(task, "board_sync", {}) or {}
            provider_sync = sync.get(provider_name) if isinstance(sync, dict) else None
            provider_sync = provider_sync if isinstance(provider_sync, dict) else {}
            sync_url = _normalize_match_value(
                provider_sync.get("issue_url") or provider_sync.get("external_url")
            )
            sync_repo = str(provider_sync.get("issue_repo") or "").strip()
            sync_number = str(provider_sync.get("issue_number") or "").strip()
            sync_external_id = _normalize_match_value(
                f"{sync_repo}#{sync_number}" if sync_repo and sync_number else ""
            )
            if external_id and sync_external_id == external_id:
                return {"matched_by": "board_sync.external_id", "task": task}
            if external_url and sync_url == external_url:
                return {"matched_by": "board_sync.external_url", "task": task}

        return {}

    def _attach_lane_status_suggestion(self, result: dict, group_settings) -> None:
        if not result.get("ok"):
            return
        status_names = self._status_option_names(result)
        if not status_names:
            return
        nested = _provider_nested_settings(
            group_settings,
            getattr(group_settings, "board_sync_provider", ""),
        )
        existing = nested.get("github_lane_status_map")
        if isinstance(existing, dict) and existing:
            result["lane_status_map_preserved"] = True
            result["lane_status_map_suggestion"] = {}
            return

        lanes = [
            str(lane or "").strip()
            for lane in (getattr(self.state, "board_lanes", []) or [])
            if str(lane or "").strip()
            and str(lane or "").strip() != ARCHIVED_LANE
        ]
        if not lanes:
            return
        mapping: dict[str, str] = {}
        strategy = "name"
        if len(status_names) == len(lanes):
            strategy = "position"
            mapping = {
                lane: status_names[index]
                for index, lane in enumerate(lanes)
            }
        else:
            by_name = {
                _normalize_match_value(name): name
                for name in status_names
            }
            for lane in lanes:
                matched = by_name.get(_normalize_match_value(lane))
                if matched:
                    mapping[lane] = matched
        unmatched = [lane for lane in lanes if lane not in mapping]
        result["lane_status_map_suggestion"] = mapping
        result["lane_status_map_strategy"] = strategy
        result["lane_status_map_unmatched_lanes"] = unmatched
        result["lane_status_map_matched_count"] = len(mapping)
        result["lane_status_map_lanes"] = lanes
        result["lane_status_map_statuses"] = status_names

    @staticmethod
    def _status_option_names(result: dict) -> list[str]:
        raw_list = result.get("status_options_list")
        names: list[str] = []
        if isinstance(raw_list, list):
            for item in raw_list:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                else:
                    name = str(item or "").strip()
                if name:
                    names.append(name)
        if names:
            return names
        raw_options = result.get("status_options")
        if isinstance(raw_options, dict):
            return [
                str(name or "").strip()
                for name in raw_options.keys()
                if str(name or "").strip()
            ]
        if isinstance(raw_options, list):
            for item in raw_options:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                else:
                    name = str(item or "").strip()
                if name:
                    names.append(name)
        return names

    def _lock_key(self, task: BoardTask, group_settings, provider_name: str) -> str:
        nested = _provider_nested_settings(group_settings, provider_name)
        sync = getattr(task, "board_sync", {}) or {}
        provider_sync = sync.get(provider_name) if isinstance(sync, dict) else None
        provider_sync = provider_sync if isinstance(provider_sync, dict) else {}
        pieces = [provider_name or "none", str(getattr(task, "group", "") or "")]
        for key in (
            "repo",
            "repository",
            "github_repo",
            "project",
            "project_id",
            "github_project_owner",
            "github_project_number",
            "github_project_id",
        ):
            value = nested.get(key)
            if value not in (None, ""):
                pieces.append(f"{key}={value}")
        for key in (
            "issue_repo",
            "project_owner",
            "project_number",
            "project_id",
            "project_item_id",
        ):
            value = provider_sync.get(key)
            if value not in (None, ""):
                pieces.append(f"{key}={value}")
        if len(pieces) <= 2:
            for attr in ("provider", "external_id", "external_url"):
                value = str(getattr(task, attr, "") or "").strip()
                if value:
                    pieces.append(f"{attr}={value}")
        return "|".join(pieces)

    def _emit_panel(
        self,
        kind: str,
        group: str,
        message: str,
        *,
        task_id: str = "",
    ) -> None:
        if not self.panel_event:
            return
        try:
            self.panel_event(kind, "", "Board Sync", group, message, task_id=task_id)
        except TypeError:
            # Tests sometimes use a simple positional callback.
            self.panel_event(kind, "", "Board Sync", group, message, task_id)
        except Exception:
            log.exception("Failed to emit board sync panel event")

    async def _emit_toast(self, message: str, level: str = "info") -> None:
        if not self.toast:
            return
        try:
            result = self.toast(message, level)
            if inspect.isawaitable(result):
                await result
        except Exception:
            log.exception("Failed to emit board sync toast")

    async def _notify_failure(
        self,
        kind: str,
        group: str,
        message: str,
        *,
        task_id: str = "",
    ) -> None:
        self._emit_panel(kind, group, message, task_id=task_id)
        await self._emit_toast(message, "error")

    async def _maybe_broadcast(self) -> None:
        broadcast = getattr(self.state, "broadcast", None)
        if callable(broadcast):
            await broadcast()


__all__ = [
    "BoardSyncManager",
    "board_sync_fields_trigger",
    "group_auto_tracks_new_tasks",
    "task_allows_auto_create_for_board_sync",
    "task_is_tracked_for_board_sync",
]
