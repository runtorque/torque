"""Caller identity, scope, visibility, and shared MCP payload helpers."""

import copy
import json
from dataclasses import replace

from torque.behavior_overlay import (
    BEHAVIOR_OVERLAY_ROLE_KINDS,
    BehaviorOverlayScope,
)
from torque.config import log
from torque.mcp_engineer_tools.shared import resolve_agent as _resolve_agent
from torque.worktree_boundaries import latest_boundary_task


_ARCHITECT_BOARD_SUMMARY_TITLE_LIMIT = 120
_TASK_DISPATCH_LAUNCH_OVERRIDE_ARGS = (
    "name",
    "provider",
    "agent_type",
    "command",
    "model",
    "reasoning_effort",
    "adopt_worktree_path",
    "adopt_branch",
    "adopt_base_branch",
)

def engineer_not_found_error(caller_id: str) -> str:
    return json.dumps({
        "type": "error",
        "message": f"no engineer with id={caller_id} exists",
    })


def normalize_tool_name(name: str, tool_prefix: str) -> str:
    prefix = str(tool_prefix or "").strip()
    if prefix and str(name or "").startswith(prefix):
        return str(name)[len(prefix):]
    return str(name or "")


def _record_engineer_dispatch_shape(state, **kwargs):
    recorder = getattr(state, "record_engineer_dispatch_shape", None)
    if not callable(recorder):
        return None
    try:
        return recorder(**kwargs)
    except Exception:
        log.exception("Failed to record engineer dispatch shape metric")
        return None


def _engineer_dispatch_shape_summary(
        state,
        engineer_id: str,
        *,
        group: str = "",
        window: int = 20) -> dict:
    summarizer = getattr(state, "engineer_dispatch_shape_summary", None)
    if not callable(summarizer):
        return {}
    try:
        return summarizer(engineer_id, group=group, window=window)
    except Exception:
        log.exception("Failed to summarize engineer dispatch shape metric")
        return {}


def _has_task_dispatch_launch_overrides(args: dict) -> bool:
    return any(
        bool(str(args.get(key, "") or "").strip())
        for key in _TASK_DISPATCH_LAUNCH_OVERRIDE_ARGS
    )


def _batch_dispatch_shape(valid_entries: list[dict]) -> tuple[str, dict]:
    agent_group_counts: dict[str, int] = {}
    statuses: dict[str, int] = {}
    task_ids: list[str] = []
    for entry in valid_entries:
        status = str(entry.get("status", "") or "").strip()
        if status:
            statuses[status] = statuses.get(status, 0) + 1
        task_id = str(entry.get("task_id", "") or "").strip()
        if task_id:
            task_ids.append(task_id)
        agent_group = str(entry.get("agent_group", "") or "").strip()
        if agent_group:
            agent_group_counts[agent_group] = (
                agent_group_counts.get(agent_group, 0) + 1
            )
    clustered_entry_count = sum(
        count for count in agent_group_counts.values()
        if count > 1
    )
    valid_entry_count = len(valid_entries)
    shape = (
        "warm_cluster"
        if clustered_entry_count
        else "batch"
        if valid_entry_count > 1
        else "serial"
    )
    metadata = {
        "entry_count": valid_entry_count,
        "statuses": statuses,
        "agent_group_counts": agent_group_counts,
        "clustered_entry_count": clustered_entry_count,
        "independent_entry_count": valid_entry_count - clustered_entry_count,
    }
    return shape, {"task_ids": task_ids, "metadata": metadata}


def tool_name_with_prefix(tool_prefix: str, suffix: str) -> str:
    prefix = str(tool_prefix or "").rstrip("_")
    return f"{prefix}_{suffix}" if prefix else str(suffix or "")


def _effective_owner_engineer_id(cell) -> str:
    owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip()
    if owner_id:
        return owner_id
    return str(getattr(cell, "created_by_engineer_id", "") or "").strip()


def _effective_assigned_engineer_id(task) -> str:
    return str(getattr(task, "assigned_engineer_id", "") or "").strip()


def _task_created_by_classifier(task) -> str:
    # Synthesized to avoid redundant storage; parent/pipeline refs are the derive/ask "system" heuristic.
    architect_id = str(
        getattr(task, "created_by_architect_id", "") or ""
    ).strip()
    if architect_id:
        return f"architect:{architect_id}"
    engineer_id = str(
        getattr(task, "created_by_engineer_id", "") or ""
    ).strip()
    if engineer_id:
        return f"engineer:{engineer_id}"
    if (
        str(getattr(task, "parent_task_id", "") or "").strip()
        or str(getattr(task, "pipeline_root_id", "") or "").strip()
    ):
        return "system"
    return "user"


def _summary_task_title(task) -> str:
    """Return a bounded first-line title for compact board summaries."""
    title = str(getattr(task, "task", "") or "").strip()
    if not title:
        return ""
    first_line = title.splitlines()[0].strip()
    if len(first_line) <= _ARCHITECT_BOARD_SUMMARY_TITLE_LIMIT:
        return first_line
    return first_line[:_ARCHITECT_BOARD_SUMMARY_TITLE_LIMIT - 1].rstrip() + "…"


def _task_board_sync_inline_state(task) -> dict:
    """Return the compact board-sync state exposed by MCP read surfaces."""
    sync = getattr(task, "board_sync", {}) or {}
    if not isinstance(sync, dict) or not sync:
        return {}
    payload = {
        "provider": str(sync.get("provider", "") or ""),
        "sync_state": str(sync.get("sync_state", "") or ""),
        "last_error": str(sync.get("last_error", "") or ""),
    }
    for key in (
        "last_attempt_at",
        "next_retry_at_iso",
        "last_error_at",
        "last_error_provider",
        "last_error_attempt",
        "last_cleared_error",
        "last_error_cleared_at",
        "last_error_cleared_reason",
    ):
        value = sync.get(key)
        if value not in (None, "", [], {}):
            payload[key] = value
    if "next_retry_at" in sync and "next_retry_at_iso" not in payload:
        payload["next_retry_at"] = sync.get("next_retry_at")
    return payload if any(payload.values()) else {}


def _attach_task_board_sync_inline_state(item: dict, task) -> dict:
    board_sync = _task_board_sync_inline_state(task)
    if board_sync:
        item["board_sync"] = board_sync
    return item


def _task_review_inline_state(task) -> dict:
    """Return compact structured review verdict state for MCP read surfaces."""
    evidence = getattr(task, "completion_evidence", {}) or {}
    if not isinstance(evidence, dict):
        return {}
    review = evidence.get("review", {}) or {}
    if not isinstance(review, dict):
        return {}
    verdict = str(review.get("verdict", "") or "").strip()
    if not verdict:
        return {}
    payload = {
        "verdict": verdict,
        "follow_up_classification": str(
            review.get("follow_up_classification", "") or ""
        ).strip(),
        "source_action": str(review.get("source_action", "") or "").strip(),
        "recorded_at": str(review.get("recorded_at", "") or "").strip(),
    }
    for key in ("derived_action", "derived_task_id", "agent_name"):
        value = str(review.get(key, "") or "").strip()
        if value:
            payload[key] = value
    return {k: v for k, v in payload.items() if v not in ("", None)}


def _attach_task_review_inline_state(item: dict, task) -> dict:
    review = _task_review_inline_state(task)
    if review:
        item["review"] = review
    return item


def _is_engineer_like_cell(state, cell) -> bool:
    if not cell or getattr(cell, "cell_type", "") != "agent":
        return False
    if _agent_is_tombstoned(state, cell):
        return False
    return str(getattr(cell, "kind", "") or "").strip() == "engineer"


def _is_architect_cell(cell, state=None) -> bool:
    return bool(
        cell
        and getattr(cell, "cell_type", "") == "agent"
        and not (state is not None and _agent_is_tombstoned(state, cell))
        and str(getattr(cell, "kind", "") or "").strip() == "architect"
    )


def _agent_is_tombstoned(state, cell) -> bool:
    checker = getattr(state, "agent_is_tombstoned", None)
    if callable(checker):
        return bool(checker(cell))
    try:
        return float(getattr(cell, "deleted_at", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def _resolve_agent_including_tombstoned(state, identifier: str) -> str | None:
    ident = str(identifier or "").strip()
    if not ident:
        return None
    cell = state.agents.get(ident)
    if cell and getattr(cell, "cell_type", "") == "agent":
        return str(getattr(cell, "id", "") or "")
    ident_lower = ident.lower()
    iterator = getattr(state, "iter_agents", None)
    cells = (
        iterator(include_tombstoned=True)
        if callable(iterator) else state.agents.values()
    )
    for cell in cells:
        if getattr(cell, "cell_type", "") != "agent":
            continue
        if str(getattr(cell, "slug", "") or "").strip().lower() == ident_lower:
            return str(getattr(cell, "id", "") or "")
    cells = (
        iterator(include_tombstoned=True)
        if callable(iterator) else state.agents.values()
    )
    for cell in cells:
        if getattr(cell, "cell_type", "") != "agent":
            continue
        if str(getattr(cell, "name", "") or "").strip().lower() == ident_lower:
            return str(getattr(cell, "id", "") or "")
    cells = (
        iterator(include_tombstoned=True)
        if callable(iterator) else state.agents.values()
    )
    for cell in cells:
        if getattr(cell, "cell_type", "") != "agent":
            continue
        if str(getattr(cell, "id", "") or "").startswith(ident):
            return str(getattr(cell, "id", "") or "")
    return None


def _agent_dismissed_at(cell) -> int:
    try:
        return int(getattr(cell, "dismissed_at", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _engineer_dismissed_error(engineer_id: str) -> str:
    return json.dumps({
        "type": "error",
        "reason": "engineer_dismissed",
        "message": f"engineer {engineer_id} is dismissed",
        "engineer_id": str(engineer_id or "").strip(),
    })


def _architect_dismissed_error(architect_id: str) -> str:
    return json.dumps({
        "type": "error",
        "reason": "architect_dismissed",
        "message": f"architect {architect_id} is dismissed",
        "architect_id": str(architect_id or "").strip(),
    })


def _caller_group(state, caller_id: str) -> str:
    caller = state.agents.get(str(caller_id or "").strip())
    return str(getattr(caller, "group", "") or "").strip() if caller else ""


def _dedupe_strings(values) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    out = []
    seen = set()
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    return out


def _architect_visible_engineers(
        state,
        caller_id: str,
        *,
        include_tombstoned: bool = False) -> dict[str, tuple[object, str]]:
    caller_group = _caller_group(state, caller_id)
    if not caller_group:
        return {}
    visible = {}
    for cell in state.iter_agents(include_tombstoned=include_tombstoned):
        if not cell or getattr(cell, "cell_type", "") != "agent":
            continue
        if str(getattr(cell, "kind", "") or "").strip() != "engineer":
            continue
        if not include_tombstoned and _agent_is_tombstoned(state, cell):
            continue
        if str(getattr(cell, "group", "") or "").strip() != caller_group:
            continue
        hired_by = str(getattr(cell, "hired_by_architect_id", "") or "").strip()
        if hired_by == str(caller_id or "").strip():
            visible[cell.id] = (cell, "hired")
        elif not hired_by:
            visible[cell.id] = (cell, "visible")
    return visible


def _architect_hired_engineer_ids(state, caller_id: str) -> set[str]:
    return {
        engineer_id
        for engineer_id, (_cell, relation) in _architect_visible_engineers(
            state, caller_id
        ).items()
        if relation == "hired"
    }


def _resolve_architect_engineer(state, caller_id: str,
                                engineer_ident: str) -> tuple[str | None, str]:
    engineer_id = _resolve_agent_including_tombstoned(state, engineer_ident)
    if not engineer_id:
        return None, f"Engineer not found: {engineer_ident}"
    engineer = state.agents.get(engineer_id)
    if _agent_is_tombstoned(state, engineer):
        return None, "engineer is tombstoned"
    visible = _architect_visible_engineers(state, caller_id)
    if engineer_id not in visible:
        return None, "engineer not found in scope"
    return engineer_id, ""


def _resolve_architect_hired_engineer(state, caller_id: str,
                                      engineer_ident: str, *,
                                      include_tombstoned: bool = False
                                      ) -> tuple[str | None, str]:
    engineer_id = _resolve_agent_including_tombstoned(state, engineer_ident)
    if not engineer_id:
        return None, f"Engineer not found: {engineer_ident}"
    engineer = state.agents.get(engineer_id)
    if _agent_is_tombstoned(state, engineer) and not include_tombstoned:
        return None, "engineer is tombstoned"
    hired_ids = {
        eid for eid, (_cell, relation) in _architect_visible_engineers(
            state, caller_id, include_tombstoned=include_tombstoned
        ).items()
        if relation == "hired"
    }
    if engineer_id not in hired_ids:
        return None, "engineer not found in scope"
    return engineer_id, ""


def _resolve_behavior_overlay_architect_target(
        state,
        caller_id: str,
        agent_ident: str = "") -> tuple[str | None, str]:
    """Resolve architect overlay target: caller self or a hired engineer."""
    ident = str(agent_ident or "").strip()
    if not ident:
        return str(caller_id or "").strip(), ""
    engineer_id, error = _resolve_architect_hired_engineer(
        state,
        caller_id,
        ident,
    )
    if engineer_id:
        return engineer_id, ""
    resolved = _resolve_agent_including_tombstoned(state, ident)
    resolved_cell = state.agents.get(resolved or "")
    if resolved_cell and str(getattr(resolved_cell, "kind", "") or "") == "worker":
        return None, "worker behavior overlays are not supported in v1"
    return None, error


def _behavior_overlay_visible_to_architect(
        state,
        caller_id: str,
        proposal: dict | None) -> bool:
    if not proposal:
        return False
    if str(proposal.get("scope_kind", "") or "agent") == "role":
        return (
            str(proposal.get("scope_group", "") or "")
            == _caller_group(state, caller_id)
        )
    target_id = str(proposal.get("agent_id", "") or "").strip()
    if target_id == str(caller_id or "").strip():
        return True
    hired_ids = {
        eid for eid, (_cell, relation) in _architect_visible_engineers(
            state,
            caller_id,
        ).items()
        if relation == "hired"
    }
    return target_id in hired_ids


def _behavior_role_scope_for_caller(
        state,
        caller_id: str,
        role_kind: str = "") -> tuple[BehaviorOverlayScope | None, str]:
    group = _caller_group(state, caller_id)
    if not group:
        return None, "caller group not found"
    kind = str(role_kind or "").strip()
    if kind not in BEHAVIOR_OVERLAY_ROLE_KINDS:
        return None, "role_kind must be architect, engineer, or worker"
    try:
        return BehaviorOverlayScope.role(group, kind), ""
    except Exception as exc:
        return None, str(exc)


def _behavior_scope_from_mcp_args(
        state,
        caller_kind: str,
        caller_id: str,
        args: dict,
        *,
        default: str = "agent") -> tuple[BehaviorOverlayScope | None, str]:
    requested = str(args.get("scope_kind", "") or default or "agent").strip()
    if requested == "effective":
        requested = "effective"
    if caller_kind == "engineer":
        if requested == "role":
            return _behavior_role_scope_for_caller(state, caller_id, "engineer")
        if requested == "agent":
            return BehaviorOverlayScope.agent(
                str(caller_id or "").strip(),
                group=_caller_group(state, caller_id),
            ), ""
        return None, "scope_kind must be agent or role"
    if caller_kind == "architect":
        if requested == "role":
            return _behavior_role_scope_for_caller(
                state,
                caller_id,
                str(args.get("role_kind", "") or args.get("role", "") or ""),
            )
        if requested == "agent":
            target_id, target_error = _resolve_behavior_overlay_architect_target(
                state,
                caller_id,
                args.get("agent_id", ""),
            )
            if not target_id:
                return None, target_error
            target = state.agents.get(target_id)
            return BehaviorOverlayScope.agent(
                target_id,
                group=str(getattr(target, "group", "") or ""),
            ), ""
    return None, "behavior overlay tools are not available to this caller"


def _resolve_group_engineer(state, caller_id: str,
                            engineer_ident: str) -> tuple[str | None, str]:
    engineer_id = _resolve_agent_including_tombstoned(state, engineer_ident)
    if not engineer_id:
        return None, f"Engineer not found: {engineer_ident}"
    engineer = state.agents.get(engineer_id)
    if _agent_is_tombstoned(state, engineer):
        return None, "engineer is tombstoned"
    if not _is_engineer_like_cell(state, engineer):
        return None, "engineer not found in scope"
    caller_group = _caller_group(state, caller_id)
    engineer_group = str(getattr(engineer, "group", "") or "").strip()
    if not caller_group or engineer_group != caller_group:
        return None, "engineer not found in scope"
    return engineer_id, ""


def _event_task_chain(state, event: dict) -> list:
    task_id = str((event or {}).get("task_id", "") or "").strip()
    task = getattr(state, "board_tasks", {}).get(task_id)
    chain = []
    seen: set[str] = set()
    while task:
        current_id = str(getattr(task, "id", "") or "").strip()
        if current_id:
            if current_id in seen:
                break
            seen.add(current_id)
        chain.append(task)
        next_id = str(getattr(task, "parent_task_id", "") or "").strip()
        root_id = str(getattr(task, "pipeline_root_id", "") or "").strip()
        if not next_id and root_id and root_id != current_id:
            next_id = root_id
        task = getattr(state, "board_tasks", {}).get(next_id) if next_id else None
    return chain


def _visible_agent_ids_for_caller(state, caller_kind: str,
                                  caller_id: str) -> set[str]:
    if caller_kind == "architect":
        caller_id = str(caller_id or "").strip()
        visible = {caller_id} if caller_id in state.agents else set()
        visible.update(_architect_hired_engineer_ids(state, caller_id))
        return visible
    if caller_kind != "engineer":
        return set()
    visible = set()
    caller_id = str(caller_id or "").strip()
    for cell in state.iter_active_agents():
        if state.agent_is_visible_to_engineer(
                caller_id,
                str(getattr(cell, "id", "") or "").strip()):
            visible.add(cell.id)
    return visible


def _filter_agents_for_caller(state, caller_kind: str,
                              caller_id: str) -> dict[str, object]:
    visible_agent_ids = _visible_agent_ids_for_caller(
        state, caller_kind, caller_id
    )
    filtered = {}
    for cell in state.iter_active_agents():
        if cell.id in visible_agent_ids:
            filtered[cell.id] = cell
    return filtered


def _filter_tasks_for_caller(state, caller_kind: str,
                             caller_id: str) -> dict[str, object]:
    if caller_kind == "architect":
        caller_id = str(caller_id or "").strip()
        caller_group = _caller_group(state, caller_id)
        if not caller_group:
            return {}
        filtered = {}
        for task in state.board_tasks.values():
            if str(getattr(task, "group", "") or "").strip() != caller_group:
                continue
            filtered[task.id] = task
        return filtered
    if caller_kind != "engineer":
        return {}
    caller_id = str(caller_id or "").strip()
    caller = state.agents.get(caller_id)
    caller_group = str(getattr(caller, "group", "") or "").strip() if caller else ""
    if not caller_group:
        return {}
    filtered = {}
    for task in state.board_tasks.values():
        if str(getattr(task, "group", "") or "").strip() != caller_group:
            continue
        assigned_engineer_id = _effective_assigned_engineer_id(task)
        if not assigned_engineer_id or assigned_engineer_id == caller_id:
            filtered[task.id] = task
    return filtered


def _state_agent_visible_to_caller(state, caller_kind: str, caller_id: str,
                                   agent_id: str) -> bool:
    return str(agent_id or "").strip() in _visible_agent_ids_for_caller(
        state, caller_kind, caller_id
    )


def _resolve_visible_agent(state, caller_kind: str, caller_id: str,
                           agent_ident: str) -> tuple[str | None, str]:
    agent_id = _resolve_agent(state, agent_ident)
    if not agent_id:
        return None, f"Agent not found: {agent_ident}"
    if not _state_agent_visible_to_caller(state, caller_kind, caller_id, agent_id):
        if caller_kind == "engineer":
            return None, "agent not found in scope"
        return None, f"Agent not found: {agent_ident}"
    return agent_id, ""


def _tombstoned_merge_target_visible_to_caller(
    state,
    caller_kind: str,
    caller_id: str,
    agent_id: str,
) -> bool:
    """Allow post-success merge recovery for a caller's just-closed worker."""
    cell = state.agents.get(str(agent_id or "").strip())
    if not cell or getattr(cell, "cell_type", "") != "agent":
        return False
    if not _agent_is_tombstoned(state, cell):
        return False
    caller = state.agents.get(str(caller_id or "").strip())
    if not caller or getattr(caller, "cell_type", "") != "agent":
        return False
    if str(getattr(cell, "group", "") or "").strip() != str(
        getattr(caller, "group", "") or ""
    ).strip():
        return False
    if caller_kind == "engineer":
        return str(getattr(cell, "owner_engineer_id", "") or "").strip() == str(
            caller_id or ""
        ).strip() or str(
            getattr(cell, "created_by_engineer_id", "") or ""
        ).strip() == str(caller_id or "").strip()
    return False


def _worktree_path_args(args: dict) -> tuple[str, str]:
    path = str(args.get("worktree_path", "") or "").strip()
    branch = str(args.get("branch", "") or args.get("worktree_branch", "") or "").strip()
    return path, branch


def _has_path_target_args(args: dict) -> bool:
    path, branch = _worktree_path_args(args)
    return bool(path or branch)


def _validate_exactly_one_worktree_target(args: dict) -> tuple[bool, str]:
    has_agent = bool(str(args.get("agent", "") or "").strip())
    has_path = _has_path_target_args(args)
    if has_agent == has_path:
        return False, "Specify exactly one target mode: agent OR worktree_path+branch."
    if has_path:
        path, branch = _worktree_path_args(args)
        if not path or not branch:
            return False, "worktree_path and branch are both required for driverless mode."
    return True, ""


def _engineer_can_access_worktree_branch(real_state, caller_id: str, *,
                                         repo_root: str = "",
                                         branch: str = "") -> tuple[bool, str]:
    caller_id = str(caller_id or "").strip()
    repo_root = str(repo_root or "").strip()
    branch = str(branch or "").strip()
    if not caller_id or not branch:
        return False, "missing caller or branch"
    if repo_root:
        latest = latest_boundary_task(
            real_state.board_tasks.values(),
            repo_root=repo_root,
            branch=branch,
            statuses={"open", "merged", "superseded"},
        )
        if latest:
            assigned = _effective_assigned_engineer_id(latest)
            if not assigned or assigned == caller_id:
                return True, "boundary"
            return False, "branch boundary is outside engineer scope"
    caller = real_state.agents.get(caller_id)
    slug = str(getattr(caller, "slug", "") or "").strip()
    if slug and branch.startswith(f"torque/{slug}/"):
        return True, "branch_prefix"
    if branch.startswith("torque/user/"):
        return True, "branch_prefix_user"
    return False, "no visible boundary or owned branch prefix"


def _driverless_payload_from_args(args: dict, *, caller_id: str, group: str) -> dict:
    path, branch = _worktree_path_args(args)
    payload = {
        "worktree_path": path,
        "branch": branch,
        "repo_root": str(args.get("repo_root", "") or "").strip(),
        "base_branch": str(args.get("base_branch", "") or "").strip(),
        "group": group,
        "caller_id": str(caller_id or "").strip(),
        "caller_kind": "engineer",
    }
    return {key: value for key, value in payload.items() if value not in ("", None)}


def authorize_caller(state, *, caller_kind: str, caller_id: str):
    caller_id = str(caller_id or "").strip()
    label = {
        "engineer": "engineer",
        "architect": "architect",
    }.get(caller_kind, caller_kind)
    if caller_kind not in {"engineer", "architect"}:
        return None, "", caller_kind, json.dumps({
            "type": "error",
            "message": f"unsupported caller kind: {caller_kind}",
        }), True
    if not caller_id:
        missing_message = (
            "TORQUE_ENGINEER_ID is required"
            if caller_kind == "engineer"
            else "TORQUE_ARCHITECT_ID is required"
            if caller_kind == "architect"
            else "caller id is required"
        )
        return None, "", caller_kind, json.dumps({
            "type": "error",
            "message": missing_message,
        }), True
    cell = state.agents.get(caller_id)
    if caller_kind == "architect":
        valid = _is_architect_cell(cell, state)
    else:
        valid = _is_engineer_like_cell(state, cell)
    if not valid:
        error_text = (
            json.dumps({
                "type": "error",
                "message": f"no architect with id={caller_id} exists",
            })
            if caller_kind == "architect"
            else
            engineer_not_found_error(caller_id)
        )
        return None, "", caller_kind, error_text, True
    group = str(getattr(cell, "group", "") or "").strip()
    if not group:
        return None, "", caller_kind, json.dumps({
            "type": "error",
            "message": f"{label} {caller_id} is not assigned to a group",
        }), True
    return cell, group, caller_kind, "", False


def build_scoped_state_view(state, *, caller_kind: str, caller_id: str,
                            caller_cell, caller_group: str):
    if caller_kind == "architect":
        visible_agents = {
            cell_id: cell
            for cell_id, cell in state.agents.items()
            if str(getattr(cell, "group", "") or "").strip() == caller_group
            and not _agent_is_tombstoned(state, cell)
        }
    else:
        visible_agents = _filter_agents_for_caller(state, caller_kind, caller_id)
    visible_tasks = _filter_tasks_for_caller(state, caller_kind, caller_id)
    visible_agent_ids = {
        cell_id for cell_id, cell in visible_agents.items()
        if getattr(cell, "cell_type", "") == "agent"
    }
    scoped_agents = dict(visible_agents)

    view_state = copy.copy(state)
    view_state.agents = scoped_agents
    view_state.board_tasks = dict(visible_tasks)
    scoped_agent_ids = {
        cell_id for cell_id, cell in scoped_agents.items()
        if getattr(cell, "cell_type", "") == "agent"
    }
    view_state.groups = {
        group_name: [
            agent_id for agent_id in agent_ids
            if agent_id in scoped_agent_ids
        ]
        for group_name, agent_ids in state.groups.items()
    }
    if caller_group and caller_group not in view_state.groups:
        view_state.groups[caller_group] = []
        view_state._children = {
            parent_id: [
                child_id for child_id in child_ids
                if child_id in view_state.agents
            ]
            for parent_id, child_ids in getattr(state, "_children", {}).items()
        if parent_id in scoped_agent_ids
    }
    view_state.group_settings = dict(getattr(state, "group_settings", {}))
    if caller_group:
        group_settings = state.get_group_settings(caller_group)
        view_state.group_settings[caller_group] = replace(
            group_settings,
            engineer_agent_id=str(getattr(caller_cell, "id", "") or ""),
        )
    view_state.agent_is_visible_to_engineer = (
        lambda _engineer_id, agent_id: str(agent_id or "").strip()
        in visible_agent_ids
    )
    view_state.engineer_restricts_to_created_agents = lambda _group: False
    if caller_kind == "engineer":
        real_journal_read = state.journal_read
        caller_author_id = str(caller_id or "").strip()

        def _engineer_scoped_journal_read(
            group,
            limit=20,
            entry_type="",
            author_cell_id="",
        ):
            return real_journal_read(
                group,
                limit,
                entry_type,
                author_cell_id=(
                    str(author_cell_id or "").strip()
                    or caller_author_id
                ),
            )

        view_state.journal_read = _engineer_scoped_journal_read
    return view_state


def _peer_row_context(row: dict) -> dict:
    summary = str((row or {}).get("context_summary", "") or "")
    if len(summary) > 240:
        summary = summary[:239].rstrip() + "…"
    return {
        "task_ids": list((row or {}).get("context_task_ids", []) or []),
        "engineer_ids": list((row or {}).get("context_engineer_ids", []) or []),
        "decision_ids": list((row or {}).get("context_decision_ids", []) or []),
        "summary": summary,
    }


def _load_architect_decision(state, caller_id: str,
                             decision_id: str) -> tuple[dict | None, str]:
    """Load a decision owned by the caller for a mutation path.

    Keep ownership separate from the group-read helper below.  Decision
    updates, links, reviews, and supersession remain caller-owned even though
    every Architect may now inspect a peer's same-group decision.
    """
    decision_id = str(decision_id or "").strip()
    if not decision_id:
        return None, "decision id is required"
    decision = state.load_decision(decision_id)
    if not decision or str(decision.get("architect_id", "") or "").strip() != str(
        caller_id or ""
    ).strip():
        return None, "Decision not found"
    return decision, ""


def _load_same_group_architect_decision(
        state, caller_id: str, decision_id: str,
) -> tuple[dict | None, str]:
    """Load a decision when its author is an Architect in the caller's group.

    Decisions do not persist a group themselves; their author is the group
    anchor.  Preserve cross-group non-disclosure by returning the historical
    not-found response outside the caller's group, while allowing all
    same-group Architect readers through.
    """
    decision_id = str(decision_id or "").strip()
    if not decision_id:
        return None, "decision id is required"
    decision = state.load_decision(decision_id)
    caller_group = _caller_group(state, caller_id)
    author_id = str((decision or {}).get("architect_id", "") or "").strip()
    author = state.agents.get(author_id) if author_id else None
    if (
            not decision
            or not caller_group
            or not _is_architect_cell(author, state)
            or str(getattr(author, "group", "") or "").strip() != caller_group):
        return None, "Decision not found"
    return decision, ""


def _load_same_group_architect_decisions(
        state, caller_id: str, *, include_archived: bool = False,
) -> list[dict]:
    """Return all decisions authored by Architects in the caller's group."""
    caller_group = _caller_group(state, caller_id)
    if not caller_group:
        return []
    decisions = []
    for decision in state.load_all_decisions(include_archived=include_archived):
        author = state.agents.get(
            str(decision.get("architect_id", "") or "").strip()
        )
        if (
                _is_architect_cell(author, state)
                and str(getattr(author, "group", "") or "").strip()
                == caller_group):
            decisions.append(decision)
    return decisions


def _agent_peer_message_row_to_entry(row: dict, agent_id: str) -> dict:
    sender_id = str(row.get("sender_id", "") or "").strip()
    recipient_id = str(row.get("recipient_id", "") or "").strip()
    sender_kind = str(row.get("sender_kind", "") or "").strip() or "architect"
    recipient_kind = str(row.get("recipient_kind", "") or "").strip() or "architect"
    direction = "sent" if str(agent_id or "").strip() == sender_id else "received"
    peer_id = recipient_id if direction == "sent" else sender_id
    peer_kind = recipient_kind if direction == "sent" else sender_kind
    created_at = float(row.get("created_at", row.get("timestamp", 0)) or 0)
    delivery_state = str(row.get("delivery_state", "") or "").strip() or "buffered"
    has_reply = bool(str(row.get("reply_to_id", "") or "").strip())
    if sender_kind == "architect" and recipient_kind == "engineer":
        action = "architect_reply" if has_reply else "architect_message"
    elif sender_kind == "engineer" and recipient_kind == "architect":
        action = "engineer_reply" if has_reply else "engineer_message_architect"
    elif sender_kind == "engineer" and recipient_kind == "engineer":
        action = "engineer_peer_reply" if has_reply else "engineer_peer_notify"
    else:
        action = "architect_peer_reply" if has_reply else "architect_peer_message"
    context = {
        "task_ids": list(row.get("context_task_ids", []) or []),
        "engineer_ids": list(row.get("context_engineer_ids", []) or []),
        "decision_ids": list(row.get("context_decision_ids", []) or []),
        "summary": str(row.get("context_summary", "") or ""),
        "snapshot": dict(row.get("context_snapshot", {}) or {}),
    }
    return {
        "id": str(row.get("id", "") or ""),
        "thread_id": str(row.get("thread_id", "") or ""),
        "reply_to_id": str(row.get("reply_to_id", "") or ""),
        "direction": direction,
        "action": action,
        "sender_id": sender_id,
        "sender_kind": sender_kind,
        "recipient_id": recipient_id,
        "recipient_kind": recipient_kind,
        "peer_id": peer_id,
        "peer_kind": peer_kind,
        "message": str(row.get("message", "") or ""),
        "timestamp": created_at,
        "ack_required": bool(row.get("ack_required", False)),
        "delivery_state": delivery_state,
        "delivery_reason": str(row.get("delivery_reason", "") or ""),
        "delivered_at": float(row.get("delivered_at", 0) or 0),
        "context": context,
        "context_task_ids": context["task_ids"],
        "context_engineer_ids": context["engineer_ids"],
        "context_decision_ids": context["decision_ids"],
        "context_summary": context["summary"],
        "context_snapshot": context["snapshot"],
    }


def _thread_requires_architect_reply(messages: list[dict],
                                     caller_id: str) -> bool:
    latest_outgoing = 0.0
    latest_incoming_ack = 0.0
    caller_id = str(caller_id or "").strip()
    for row in messages:
        ts = float(row.get("created_at", row.get("timestamp", 0)) or 0)
        if str(row.get("sender_id", "") or "").strip() == caller_id:
            latest_outgoing = max(latest_outgoing, ts)
        elif bool(row.get("ack_required", False)):
            latest_incoming_ack = max(latest_incoming_ack, ts)
    return latest_incoming_ack > latest_outgoing


def _optional_bool_arg(args: dict, key: str, default: bool = False
                       ) -> tuple[bool, str]:
    if key not in args or args.get(key) is None:
        return bool(default), ""
    value = args.get(key)
    if isinstance(value, bool):
        return value, ""
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True, ""
        if lowered in {"false", "0", "no", "off", ""}:
            return False, ""
    return bool(default), f"{key} must be a boolean"
