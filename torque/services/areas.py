"""Planning Area domain orchestration.

The service owns Area reads, payload construction, mutations, notes, links, and
delta emission. ``MatrixState`` keeps compatibility delegates for callers.
"""

from __future__ import annotations

from typing import Any

from ..config import log


class AreaService:
    def __init__(self, state: Any):
        self._state = state

    def _emit_area(self, area: dict | None) -> None:
        if not area:
            return
        self._state._emit("area_upsert", **dict(area))

    def resolve_area_id(self, identifier: str, *, group: str = "") -> str:
        ident = str(identifier or "").strip()
        if not ident or not self._state.db:
            return ""
        if self._state.db.load_area(ident):
            return ident
        group = str(group or "").strip()
        ident_lower = ident.lower()
        try:
            for item in self._state.db.list_areas(
                    group=group, include_archived=True, limit=500):
                if str(item.get("slug", "") or "").lower() == ident_lower:
                    return str(item.get("id", "") or "")
        except Exception:
            log.exception("Failed to resolve area %s", ident)
        return ""

    def list_areas(self, *, group: str = "", include_archived: bool = False,
                   limit: int = 100) -> list[dict]:
        if not self._state.db:
            return []
        try:
            return self._state.db.list_areas(
                group=group,
                include_archived=include_archived,
                limit=limit,
            )
        except Exception:
            log.exception("Failed to list areas")
            return []

    def load_area(self, area_id: str) -> dict | None:
        if not self._state.db:
            return None
        try:
            return self._state.db.load_area(area_id)
        except Exception:
            log.exception("Failed to load area %s", area_id)
            return None

    def area_links(self, area_id: str, link_type: str = "") -> list[dict]:
        if not self._state.db:
            return []
        try:
            return self._state.db.list_area_links(area_id, link_type)
        except Exception:
            log.exception("Failed to list area links %s", area_id)
            return []

    def area_notes(self, area_id: str, *, include_archived: bool = False,
                   limit: int = 50) -> list[dict]:
        if not self._state.db:
            return []
        try:
            return self._state.db.list_area_notes(
                area_id,
                include_archived=include_archived,
                limit=limit,
            )
        except Exception:
            log.exception("Failed to list area notes %s", area_id)
            return []

    def load_area_note(self, note_id) -> dict | None:
        if not self._state.db:
            return None
        try:
            return self._state.db.load_area_note(note_id)
        except Exception:
            log.exception("Failed to load area note %s", note_id)
            return None

    def area_payload(self, area_id: str, *,
                     visible_task_ids: set[str] | None = None,
                     visible_decision_ids: set[str] | None = None,
                     include_links: bool = True,
                     include_notes: bool = True,
                     decision_details: bool = False,
                     note_limit: int = 50) -> dict | None:
        area = self.load_area(area_id)
        if not area:
            return None
        payload = dict(area)
        if include_links:
            links = self.area_links(area_id)
            grouped = {
                "tasks": [],
                "initiatives": [],
                "decisions": [],
                "areas": [],
            }
            hidden = {
                "tasks": 0,
                "initiatives": 0,
                "decisions": 0,
                "areas": 0,
            }
            task_ids = []
            decision_ids = []
            for link in links:
                link_type = str(link.get("link_type", "") or "")
                target_id = str(link.get("target_id", "") or "")
                if not target_id:
                    continue
                if link_type == "task":
                    task_ids.append(target_id)
                    if visible_task_ids is not None and target_id not in visible_task_ids:
                        hidden["tasks"] += 1
                    else:
                        grouped["tasks"].append(target_id)
                elif link_type == "decision":
                    decision_ids.append(target_id)
                    if visible_decision_ids is not None and target_id not in visible_decision_ids:
                        hidden["decisions"] += 1
                    else:
                        grouped["decisions"].append(target_id)
                elif link_type == "initiative":
                    grouped["initiatives"].append(target_id)
                elif link_type == "area":
                    grouped["areas"].append({
                        "area_id": target_id,
                        "relation": str(link.get("relation", "") or "related"),
                    })
            payload["links"] = grouped
            payload["hidden_link_counts"] = hidden
            payload["linked_tasks"] = self._state._initiative_service._initiative_linked_task_payload(
                task_ids,
                visible_task_ids=visible_task_ids,
            )
            visible_decisions = list(grouped["decisions"])
            decision_items = []
            if decision_details:
                for decision_id in visible_decisions[:25]:
                    decision = self._state.load_decision(decision_id)
                    if decision:
                        decision_items.append({
                            "id": decision.get("id", ""),
                            "title": decision.get("title", ""),
                            "status": decision.get("status", ""),
                            "updated_at": decision.get("updated_at", ""),
                            "archived": bool(decision.get("archived", False)),
                        })
            payload["linked_decisions"] = {
                "count": len(visible_decisions),
                "hidden_count": hidden["decisions"],
                "items": decision_items if decision_details else [],
            }
            if decision_details:
                payload["linked_decisions"]["ids"] = visible_decisions[:25]
        if include_notes:
            notes = []
            for note in self.area_notes(area_id, include_archived=False, limit=note_limit):
                item = dict(note)
                target_type = str(item.get("target_type", "") or "")
                target_id = str(item.get("target_id", "") or "")
                if (
                        target_type == "decision"
                        and visible_decision_ids is not None
                        and target_id not in visible_decision_ids):
                    item["target_id"] = ""
                    item["target_hidden"] = True
                if (
                        target_type == "task"
                        and visible_task_ids is not None
                        and target_id not in visible_task_ids):
                    item["target_id"] = ""
                    item["target_hidden"] = True
                notes.append(item)
            payload["notes"] = notes
            payload["note_count"] = len(notes)
        return payload

    async def create_area_async(self, row_dict: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            saved = await self._state.db.create_area_async(row_dict)
            self._emit_area(saved)
            return saved
        except Exception:
            log.exception("Failed to create area")
            raise
    async def update_area_async(self, area_id: str, patch: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            saved = await self._state.db.update_area_async(area_id, patch)
            self._emit_area(saved)
            return saved
        except Exception:
            log.exception("Failed to update area %s", area_id)
            raise

    async def archive_area_async(self, area_id: str, **kwargs) -> dict | None:
        if not self._state.db:
            return None
        try:
            saved = await self._state.db.archive_area_async(area_id, **kwargs)
            self._emit_area(saved)
            return saved
        except Exception:
            log.exception("Failed to archive area %s", area_id)
            raise

    async def save_area_link_async(self, area_id: str, link_type: str,
                                   target_id: str, **kwargs) -> dict | None:
        if not self._state.db:
            return None
        try:
            link = await self._state.db.save_area_link_async(
                area_id,
                link_type,
                target_id,
                **kwargs,
            )
            self._state._emit("area_link_upsert", **dict(link))
            return link
        except Exception:
            log.exception("Failed to save area link")
            raise

    async def delete_area_link_async(self, area_id: str, link_type: str,
                                     target_id: str, relation: str = "") -> bool:
        if not self._state.db:
            return False
        try:
            removed = await self._state.db.delete_area_link_async(
                area_id,
                link_type,
                target_id,
                relation,
            )
            self._state._emit(
                "area_link_remove",
                area_id=area_id,
                link_type=link_type,
                target_id=target_id,
                relation=relation,
            )
            return bool(removed)
        except Exception:
            log.exception("Failed to delete area link")
            raise

    async def create_area_note_async(self, area_id: str, row_dict: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            note = await self._state.db.create_area_note_async(area_id, row_dict)
            self._state._emit("area_note_upsert", **dict(note))
            return note
        except Exception:
            log.exception("Failed to create area note")
            raise

    async def update_area_note_async(self, note_id, patch: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            note = await self._state.db.update_area_note_async(note_id, patch)
            self._state._emit("area_note_upsert", **dict(note))
            return note
        except Exception:
            log.exception("Failed to update area note %s", note_id)
            raise

    async def archive_area_note_async(self, note_id, **kwargs) -> dict | None:
        if not self._state.db:
            return None
        try:
            note = await self._state.db.archive_area_note_async(note_id, **kwargs)
            self._state._emit("area_note_upsert", **dict(note))
            return note
        except Exception:
            log.exception("Failed to archive area note %s", note_id)
            raise
