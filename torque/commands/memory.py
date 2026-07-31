"""Durable memory read and mutation command handlers.

The server owns transport and dependency composition; this module owns the
command semantics for the memory surface.
"""

from __future__ import annotations

import time

from ..dispatch_registry import AsyncHandlerRegistry
from ..memory import (
    build_memory_entry,
    build_memory_link,
    load_visible_memory_entries,
    normalize_entry_type,
    normalize_link_target_kind,
)
from ..state import MatrixState


MEMORY_COMMAND_NAMES = frozenset({
    "memory_list",
    "memory_read",
    "memory_publish",
    "memory_pin",
    "memory_link",
    "memory_unpin",
})


async def _handle_memory_command(
    data: dict,
    state: MatrixState,
    *,
    resolve_cell_and_task,
    resolve_scope_ref,
    resolve_link_ref,
    resolve_task_id,
) -> dict:
    cmd = str(data.get("cmd", "") or "").strip()
    if not state.db:
        return {
            "type": "error",
            "message": "Memory storage is unavailable",
        }

    if cmd == "memory_list":
        cell, task = resolve_cell_and_task(
            state,
            data.get("cell_id", ""),
            data.get("task_id", ""),
        )
        scope_kind = (data.get("scope_kind", "") or "").strip()
        try:
            scope_ref = resolve_scope_ref(
                scope_kind,
                (data.get("scope_ref", "") or "").strip(),
                cell=cell,
                task=task,
            )
        except ValueError as exc:
            return {"type": "error", "message": str(exc)}

        group_name = (data.get("group_name", "") or "").strip()
        project_key = (data.get("project_key", "") or "").strip()
        linked_target_kind = (
            data.get("linked_target_kind", "") or ""
        ).strip()
        linked_target_ref = (
            data.get("linked_target_ref", "") or ""
        ).strip()
        if linked_target_kind:
            try:
                linked_target_kind = normalize_link_target_kind(
                    linked_target_kind
                )
                linked_target_ref = resolve_link_ref(
                    linked_target_kind,
                    linked_target_ref,
                    cell=cell,
                    task=task,
                )
            except ValueError as exc:
                return {"type": "error", "message": str(exc)}

        if not scope_kind and not scope_ref and not group_name:
            if task:
                scope_kind = "task"
                scope_ref = task.id
            elif cell and cell.group:
                scope_kind = "group"
                scope_ref = cell.group
                group_name = cell.group
        if not group_name:
            if task and task.group:
                group_name = task.group
            elif scope_kind == "group" and scope_ref:
                group_name = scope_ref
            elif cell and cell.group and not project_key:
                group_name = cell.group

        search_text = (data.get("search", "") or "").strip()
        entries = load_visible_memory_entries(
            state.db,
            group_name=group_name,
            project_key=project_key,
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            entry_type=(data.get("entry_type", "") or "").strip(),
            task_id=resolve_task_id(
                state,
                (data.get("filter_task_id", "") or "").strip(),
            ),
            pinned_only=bool(data.get("pinned_only", False)),
            search=search_text,
            linked_target_kind=linked_target_kind,
            linked_target_ref=linked_target_ref,
            limit=int(data.get("limit", 20) or 20),
            offset=int(data.get("offset", 0) or 0),
        )
        return {
            "type": "memory_entries",
            "entries": entries,
            "scope_kind": scope_kind,
            "scope_ref": scope_ref,
            "group_name": group_name,
            "project_key": project_key,
            "linked_target_kind": linked_target_kind,
            "linked_target_ref": linked_target_ref,
        }

    if cmd == "memory_read":
        entry_id = (data.get("entry_id", "") or "").strip()
        entry = state.db.load_memory_entry(entry_id)
        if not entry:
            return {"type": "error", "message": "Memory entry not found"}
        return {"type": "memory_entry", "entry": entry}

    if cmd == "memory_publish":
        cell, task = resolve_cell_and_task(
            state,
            data.get("cell_id", ""),
            data.get("task_id", ""),
        )
        entry_id = (data.get("entry_id", "") or "").strip()
        existing = state.db.load_memory_entry(entry_id) if entry_id else None
        if entry_id and not existing:
            return {"type": "error", "message": "Memory entry not found"}
        # Agent-originated updates are author-only. Operator/UI calls do not
        # carry ``cell_id`` and retain their established edit path.
        if (existing and str(data.get("cell_id", "") or "").strip() and cell
                and existing.get("source_id", "") != cell.id):
            return {
                "type": "error",
                "message": (
                    "Only the original author may update a shared memory "
                    "entry; publish a new entry and link it instead."
                ),
            }

        try:
            scope_kind = (data.get("scope_kind", "") or "").strip()
            if not scope_kind and existing:
                scope_kind = existing.get("scope_kind", "")
            scope_ref = resolve_scope_ref(
                scope_kind,
                data.get("scope_ref", "")
                if "scope_ref" in data
                else (existing.get("scope_ref", "") if existing else ""),
                cell=cell,
                task=task,
            )
            entry_type = data.get("entry_type", "")
            if not entry_type and existing:
                entry_type = existing.get("entry_type", "")
            title = data.get("title", "")
            if "title" not in data and existing:
                title = existing.get("title", "")
            content = data.get("content", "")
            if "content" not in data and existing:
                content = existing.get("content", "")
            pinned = (
                bool(data.get("pinned", False))
                if "pinned" in data
                else bool(existing.get("pinned", False))
                if existing
                else False
            )
            # Accepted for callers dispatched before the TTL lifecycle, but
            # intentionally ignored by ``build_memory_entry``.
            retention_kind = (data.get("retention_kind", "") or "").strip()
            source_kind = data.get("source_kind", "")
            if not source_kind and existing:
                source_kind = existing.get("source_kind", "agent")

            pending_links = []
            for raw_link in (data.get("link_targets", []) or []):
                if not isinstance(raw_link, dict):
                    raise ValueError("Each link target must be an object")
                target_kind = normalize_link_target_kind(
                    raw_link.get("target_kind", "")
                )
                pending_links.append(
                    build_memory_link(
                        state,
                        entry_id=entry_id or "__pending__",
                        target_kind=target_kind,
                        target_ref=resolve_link_ref(
                            target_kind,
                            raw_link.get("target_ref", ""),
                            cell=cell,
                            task=task,
                        ),
                        cell=cell,
                        task=task,
                    )
                )
            entry = build_memory_entry(
                state,
                cell=cell,
                task=task,
                entry_type=normalize_entry_type(entry_type),
                title=title,
                content=content,
                scope_kind=scope_kind,
                scope_ref=scope_ref,
                pinned=pinned,
                source_kind=source_kind or "agent",
                retention_kind=retention_kind,
            )
        except ValueError as exc:
            return {"type": "error", "message": str(exc)}

        if existing:
            entry["id"] = existing["id"]
            entry["created_at"] = existing.get(
                "created_at", entry["created_at"]
            )
            for field in ("source_kind", "source_id", "source_name"):
                entry[field] = existing.get(field, entry[field])
            # Updating content must neither extend a TTL nor stamp legacy NULL
            # rows; TORQUE:1477 owns the latter migration.
            entry["expires_at"] = existing.get("expires_at")
        state.db.save_memory_entry(entry)
        for link in pending_links:
            link["entry_id"] = entry["id"]
            state.db.save_memory_link(link)
        entry = state.db.load_memory_entry(entry["id"]) or entry
        return {
            "type": "memory_entry",
            "entry": entry,
            "retention_note": (
                "retention_kind is deprecated; all entries expire per group "
                "TTL, and pinning affects ranking only"
            ),
        }

    if cmd in {"memory_pin", "memory_unpin"}:
        entry_id = (data.get("entry_id", "") or "").strip()
        entry = state.db.load_memory_entry(entry_id)
        if not entry:
            return {"type": "error", "message": "Memory entry not found"}
        state.db.set_memory_entry_pinned(
            entry_id,
            cmd == "memory_pin",
            time.time(),
        )
        entry = state.db.load_memory_entry(entry_id) or entry
        return {"type": "memory_entry", "entry": entry}

    if cmd == "memory_link":
        entry_id = (data.get("entry_id", "") or "").strip()
        entry = state.db.load_memory_entry(entry_id)
        if not entry:
            return {"type": "error", "message": "Memory entry not found"}
        cell, task = resolve_cell_and_task(
            state,
            data.get("cell_id", ""),
            data.get("task_id", ""),
        )
        try:
            target_kind = normalize_link_target_kind(
                data.get("target_kind", "")
            )
            link = build_memory_link(
                state,
                entry_id=entry_id,
                target_kind=target_kind,
                target_ref=resolve_link_ref(
                    target_kind,
                    data.get("target_ref", ""),
                    cell=cell,
                    task=task,
                ),
                cell=cell,
                task=task,
            )
        except ValueError as exc:
            return {"type": "error", "message": str(exc)}
        state.db.save_memory_link(link)
        return {
            "type": "memory_entry",
            "entry": state.db.load_memory_entry(entry_id),
        }

    return {"type": "error", "message": f"Unknown memory command: {cmd}"}


_MEMORY_COMMAND_REGISTRY = AsyncHandlerRegistry()
_MEMORY_COMMAND_REGISTRY.register_many(
    MEMORY_COMMAND_NAMES,
    _handle_memory_command,
    label="memory",
)
