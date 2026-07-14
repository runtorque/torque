"""Initiative domain orchestration.

The service owns Initiative reads, payload construction, mutations, and delta
emission.  ``MatrixState`` delegates its compatibility API here so callers can
migrate incrementally without changing WebSocket or persistence contracts.
"""

from __future__ import annotations

from typing import Any

from ..config import log


class InitiativeService:
    def __init__(self, state: Any):
        self._state = state

    def _emit_initiative(self, initiative: dict | None) -> None:
        if not initiative:
            return
        self._state._emit("initiative_upsert", **dict(initiative))

    def resolve_initiative_id(self, identifier: str, *, group: str = "") -> str:
        ident = str(identifier or "").strip()
        if not ident or not self._state.db:
            return ""
        if self._state.db.load_initiative(ident):
            return ident
        group = str(group or "").strip()
        ident_lower = ident.lower()
        try:
            for item in self._state.db.list_initiatives(
                    group=group, include_archived=True):
                if str(item.get("slug", "") or "").lower() == ident_lower:
                    return str(item.get("id", "") or "")
        except Exception:
            log.exception("Failed to resolve initiative %s", ident)
        return ""

    def list_initiatives(self, *, group: str = "",
                         include_archived: bool = False) -> list[dict]:
        if not self._state.db:
            return []
        try:
            return self._state.db.list_initiatives(
                group=group,
                include_archived=include_archived,
            )
        except Exception:
            log.exception("Failed to list initiatives")
            return []

    def load_initiative(self, initiative_id: str) -> dict | None:
        if not self._state.db:
            return None
        try:
            return self._state.db.load_initiative(initiative_id)
        except Exception:
            log.exception("Failed to load initiative %s", initiative_id)
            return None

    def initiative_links(self, initiative_id: str,
                         link_type: str = "") -> list[dict]:
        if not self._state.db:
            return []
        try:
            return self._state.db.list_initiative_links(initiative_id, link_type)
        except Exception:
            log.exception("Failed to list initiative links %s", initiative_id)
            return []

    def _initiative_linked_task_payload(self, task_ids: list[str], *,
                                        visible_task_ids: set[str] | None = None
                                        ) -> dict:
        visible_task_ids = (
            visible_task_ids
            if visible_task_ids is not None
            else set(self._state.board_tasks)
        )
        items = []
        by_lane: dict[str, int] = {}
        hidden_count = 0
        for task_id in task_ids:
            if task_id not in visible_task_ids:
                hidden_count += 1
                continue
            task = self._state.board_tasks.get(task_id)
            if not task:
                continue
            lane = str(getattr(task, "lane", "") or "")
            by_lane[lane] = by_lane.get(lane, 0) + 1
            items.append({
                "id": task.id,
                "title": task.task,
                "lane": task.lane,
                "status": task.status,
                "dispatch_state": getattr(task, "dispatch_state", "queued") or "queued",
                "health_state": getattr(task, "health_state", "healthy") or "healthy",
                "assigned_engineer_id": getattr(task, "assigned_engineer_id", "") or "",
                "assigned_architect_id": getattr(task, "assigned_architect_id", "") or "",
                "updated_at": getattr(task, "updated_at", "") or "",
                "archived_at": getattr(task, "archived_at", "") or "",
            })
        return {
            "count": len(items),
            "hidden_count": hidden_count,
            "by_lane": by_lane,
            "items": items,
        }

    def initiative_payload(self, initiative_id: str, *,
                           visible_task_ids: set[str] | None = None,
                           visible_decision_ids: set[str] | None = None,
                           include_links: bool = True) -> dict | None:
        initiative = self.load_initiative(initiative_id)
        if not initiative:
            return None
        payload = dict(initiative)
        if include_links:
            links = self.initiative_links(initiative_id)
            task_ids = [
                str(link.get("target_id", "") or "")
                for link in links
                if str(link.get("link_type", "") or "") == "task"
            ]
            decision_ids = [
                str(link.get("target_id", "") or "")
                for link in links
                if str(link.get("link_type", "") or "") == "decision"
            ]
            visible_link_task_ids = (
                [
                    task_id for task_id in task_ids
                    if task_id in visible_task_ids
                ]
                if visible_task_ids is not None else task_ids
            )
            visible_link_decision_ids = (
                [
                    decision_id for decision_id in decision_ids
                    if decision_id in visible_decision_ids
                ]
                if visible_decision_ids is not None else decision_ids
            )
            payload["links"] = {
                "tasks": visible_link_task_ids,
                "decisions": visible_link_decision_ids,
            }
            payload["linked_tasks"] = self._initiative_linked_task_payload(
                task_ids,
                visible_task_ids=visible_task_ids,
            )
            payload["linked_decisions"] = {
                "count": len(visible_link_decision_ids),
                "hidden_count": (
                    len(decision_ids) - len(visible_link_decision_ids)
                    if visible_decision_ids is not None else 0
                ),
                "items": visible_link_decision_ids,
            }
        return payload

    async def create_initiative_async(self, row_dict: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            saved = await self._state.db.create_initiative_async(row_dict)
            self._emit_initiative(saved)
            return saved
        except Exception:
            log.exception("Failed to create initiative")
            raise

    async def update_initiative_async(self, initiative_id: str,
                                      patch: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            saved = await self._state.db.update_initiative_async(initiative_id, patch)
            self._emit_initiative(saved)
            return saved
        except Exception:
            log.exception("Failed to update initiative %s", initiative_id)
            raise

    async def archive_initiative_async(self, initiative_id: str, **kwargs
                                       ) -> dict | None:
        if not self._state.db:
            return None
        try:
            saved = await self._state.db.archive_initiative_async(initiative_id, **kwargs)
            self._emit_initiative(saved)
            return saved
        except Exception:
            log.exception("Failed to archive initiative %s", initiative_id)
            raise

    async def save_initiative_link_async(self, initiative_id: str,
                                         link_type: str,
                                         target_id: str,
                                         **kwargs) -> dict | None:
        if not self._state.db:
            return None
        try:
            link = await self._state.db.save_initiative_link_async(
                initiative_id,
                link_type,
                target_id,
                **kwargs,
            )
            self._state._emit("initiative_link_upsert", **dict(link))
            return link
        except Exception:
            log.exception("Failed to save initiative link")
            raise

    async def delete_initiative_link_async(self, initiative_id: str,
                                           link_type: str,
                                           target_id: str) -> bool:
        if not self._state.db:
            return False
        try:
            removed = await self._state.db.delete_initiative_link_async(
                initiative_id,
                link_type,
                target_id,
            )
            self._state._emit(
                "initiative_link_remove",
                initiative_id=initiative_id,
                link_type=link_type,
                target_id=target_id,
            )
            return bool(removed)
        except Exception:
            log.exception("Failed to delete initiative link")
            raise
