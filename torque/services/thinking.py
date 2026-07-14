"""Thinking domain orchestration for Scratchpad notes and Mind Maps."""

from __future__ import annotations

from typing import Any

from ..config import log


class ThinkingService:
    def __init__(self, state: Any):
        self._state = state

    def thinking_snapshot(self, *, group: str = "",
                          include_archived: bool = False) -> dict:
        if not self._state.db:
            return {"scratchpad_notes": {}, "mind_maps": {}}
        try:
            notes = self._state.db.list_scratchpad_notes(
                group=group,
                include_archived=include_archived,
                include_deleted=False,
                limit=1000,
            )
            maps = self._state.db.list_mind_maps(
                group=group,
                include_archived=include_archived,
                include_deleted=False,
                include_counts=True,
                limit=1000,
            )
            return {
                "scratchpad_notes": {
                    str(note.get("id", "") or ""): note
                    for note in notes
                    if str(note.get("id", "") or "")
                },
                "mind_maps": {
                    str(item.get("id", "") or ""): item
                    for item in maps
                    if str(item.get("id", "") or "")
                },
            }
        except Exception:
            log.exception("Failed to load thinking snapshot")
            return {"scratchpad_notes": {}, "mind_maps": {}}

    def _emit_scratchpad_note(self, note: dict | None) -> None:
        if not note:
            return
        self._state._emit("thinking_scratchpad_note_upsert", **dict(note))

    def _emit_mind_map(self, mind_map: dict | None) -> None:
        if not mind_map:
            return
        self._state._emit("thinking_mind_map_upsert", **dict(mind_map))

    def _emit_mind_map_node(self, node: dict | None) -> None:
        if not node:
            return
        self._state._emit("thinking_mind_map_node_upsert", **dict(node))

    def _emit_mind_map_link(self, link: dict | None) -> None:
        if not link:
            return
        self._state._emit("thinking_mind_map_link_upsert", **dict(link))

    def resolve_scratchpad_note_id(self, identifier: str, *,
                                   group: str = "") -> str:
        ident = str(identifier or "").strip()
        if not ident or not self._state.db:
            return ""
        note = self._state.db.load_scratchpad_note(ident)
        if note:
            return str(note.get("id", "") or "")
        group = str(group or "").strip()
        ident_lower = ident.lower()
        try:
            for item in self._state.db.list_scratchpad_notes(
                    group=group,
                    include_archived=True,
                    include_deleted=True,
                    limit=1000):
                if str(item.get("slug", "") or "").lower() == ident_lower:
                    return str(item.get("id", "") or "")
        except Exception:
            log.exception("Failed to resolve scratchpad note %s", ident)
        return ""

    def list_scratchpad_notes(self, *, group: str = "",
                              include_archived: bool = False,
                              include_deleted: bool = False,
                              limit: int = 200) -> list[dict]:
        if not self._state.db:
            return []
        try:
            return self._state.db.list_scratchpad_notes(
                group=group,
                include_archived=include_archived,
                include_deleted=include_deleted,
                limit=limit,
            )
        except Exception:
            log.exception("Failed to list scratchpad notes")
            return []

    def load_scratchpad_note(self, note_id: str) -> dict | None:
        if not self._state.db:
            return None
        try:
            return self._state.db.load_scratchpad_note(note_id)
        except Exception:
            log.exception("Failed to load scratchpad note %s", note_id)
            return None

    async def create_scratchpad_note_async(self, row_dict: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            note = await self._state.db.create_scratchpad_note_async(row_dict)
            self._emit_scratchpad_note(note)
            return note
        except Exception:
            log.exception("Failed to create scratchpad note")
            raise
    async def update_scratchpad_note_async(self, note_id: str,
                                           patch: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            note = await self._state.db.update_scratchpad_note_async(note_id, patch)
            self._emit_scratchpad_note(note)
            return note
        except Exception:
            log.exception("Failed to update scratchpad note %s", note_id)
            raise

    async def archive_scratchpad_note_async(self, note_id: str,
                                            **kwargs) -> dict | None:
        if not self._state.db:
            return None
        try:
            note = await self._state.db.archive_scratchpad_note_async(note_id, **kwargs)
            self._emit_scratchpad_note(note)
            return note
        except Exception:
            log.exception("Failed to archive scratchpad note %s", note_id)
            raise

    async def delete_scratchpad_note_async(self, note_id: str,
                                           **kwargs) -> dict | None:
        if not self._state.db:
            return None
        try:
            note = await self._state.db.delete_scratchpad_note_async(note_id, **kwargs)
            self._emit_scratchpad_note(note)
            return note
        except Exception:
            log.exception("Failed to delete scratchpad note %s", note_id)
            raise

    def resolve_mind_map_id(self, identifier: str, *, group: str = "") -> str:
        ident = str(identifier or "").strip()
        if not ident or not self._state.db:
            return ""
        mind_map = self._state.db.load_mind_map(ident)
        if mind_map:
            return str(mind_map.get("id", "") or "")
        group = str(group or "").strip()
        ident_lower = ident.lower()
        try:
            for item in self._state.db.list_mind_maps(
                    group=group,
                    include_archived=True,
                    include_deleted=True,
                    include_counts=False,
                    limit=1000):
                if str(item.get("slug", "") or "").lower() == ident_lower:
                    return str(item.get("id", "") or "")
        except Exception:
            log.exception("Failed to resolve mind map %s", ident)
        return ""

    def list_mind_maps(self, *, group: str = "",
                       include_archived: bool = False,
                       include_deleted: bool = False,
                       include_counts: bool = True,
                       limit: int = 200) -> list[dict]:
        if not self._state.db:
            return []
        try:
            return self._state.db.list_mind_maps(
                group=group,
                include_archived=include_archived,
                include_deleted=include_deleted,
                include_counts=include_counts,
                limit=limit,
            )
        except Exception:
            log.exception("Failed to list mind maps")
            return []

    def load_mind_map(self, map_id: str, *, include_counts: bool = False
                      ) -> dict | None:
        if not self._state.db:
            return None
        try:
            return self._state.db.load_mind_map(map_id, include_counts=include_counts)
        except Exception:
            log.exception("Failed to load mind map %s", map_id)
            return None

    def mind_map_payload(self, map_id: str, *,
                         include_archived: bool = False,
                         include_deleted: bool = False) -> dict | None:
        if not self._state.db:
            return None
        try:
            return self._state.db.mind_map_payload(
                map_id,
                include_archived=include_archived,
                include_deleted=include_deleted,
            )
        except Exception:
            log.exception("Failed to load mind map payload %s", map_id)
            return None

    def load_mind_map_node(self, node_id: str) -> dict | None:
        if not self._state.db:
            return None
        try:
            return self._state.db.load_mind_map_node(node_id)
        except Exception:
            log.exception("Failed to load mind map node %s", node_id)
            return None

    def load_mind_map_link(self, link_id: str) -> dict | None:
        if not self._state.db:
            return None
        try:
            return self._state.db.load_mind_map_link(link_id)
        except Exception:
            log.exception("Failed to load mind map link %s", link_id)
            return None

    async def create_mind_map_async(self, row_dict: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            mind_map = await self._state.db.create_mind_map_async(row_dict)
            self._emit_mind_map(mind_map)
            return mind_map
        except Exception:
            log.exception("Failed to create mind map")
            raise

    async def update_mind_map_async(self, map_id: str,
                                    patch: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            mind_map = await self._state.db.update_mind_map_async(map_id, patch)
            self._emit_mind_map(mind_map)
            return mind_map
        except Exception:
            log.exception("Failed to update mind map %s", map_id)
            raise

    async def archive_mind_map_async(self, map_id: str,
                                     **kwargs) -> dict | None:
        if not self._state.db:
            return None
        try:
            mind_map = await self._state.db.archive_mind_map_async(map_id, **kwargs)
            self._emit_mind_map(mind_map)
            return mind_map
        except Exception:
            log.exception("Failed to archive mind map %s", map_id)
            raise

    async def delete_mind_map_async(self, map_id: str,
                                    **kwargs) -> dict | None:
        if not self._state.db:
            return None
        try:
            mind_map = await self._state.db.delete_mind_map_async(map_id, **kwargs)
            self._emit_mind_map(mind_map)
            return mind_map
        except Exception:
            log.exception("Failed to delete mind map %s", map_id)
            raise

    async def create_mind_map_node_async(self, map_id: str,
                                         row_dict: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            node = await self._state.db.create_mind_map_node_async(map_id, row_dict)
            self._emit_mind_map_node(node)
            self._emit_mind_map(self._state.db.load_mind_map(map_id, include_counts=True))
            return node
        except Exception:
            log.exception("Failed to create mind map node")
            raise

    async def update_mind_map_node_async(self, node_id: str,
                                         patch: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            node = await self._state.db.update_mind_map_node_async(node_id, patch)
            self._emit_mind_map_node(node)
            if node:
                self._emit_mind_map(
                    self._state.db.load_mind_map(node.get("map_id", ""), include_counts=True)
                )
            return node
        except Exception:
            log.exception("Failed to update mind map node %s", node_id)
            raise

    async def delete_mind_map_node_async(self, node_id: str,
                                         **kwargs) -> dict | None:
        if not self._state.db:
            return None
        try:
            node = await self._state.db.delete_mind_map_node_async(node_id, **kwargs)
            self._emit_mind_map_node(node)
            if node:
                self._emit_mind_map(
                    self._state.db.load_mind_map(node.get("map_id", ""), include_counts=True)
                )
            return node
        except Exception:
            log.exception("Failed to delete mind map node %s", node_id)
            raise

    async def reorder_mind_map_nodes_async(self, map_id: str,
                                           node_order: list,
                                           **kwargs) -> list[dict]:
        if not self._state.db:
            return []
        try:
            nodes = await self._state.db.reorder_mind_map_nodes_async(
                map_id,
                node_order,
                **kwargs,
            )
            for node in nodes:
                self._emit_mind_map_node(node)
            self._emit_mind_map(self._state.db.load_mind_map(map_id, include_counts=True))
            return nodes
        except Exception:
            log.exception("Failed to reorder mind map nodes %s", map_id)
            raise

    async def create_mind_map_link_async(self, map_id: str,
                                         row_dict: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            link = await self._state.db.create_mind_map_link_async(map_id, row_dict)
            self._emit_mind_map_link(link)
            self._emit_mind_map(self._state.db.load_mind_map(map_id, include_counts=True))
            return link
        except Exception:
            log.exception("Failed to create mind map link")
            raise

    async def update_mind_map_link_async(self, link_id: str,
                                         patch: dict) -> dict | None:
        if not self._state.db:
            return None
        try:
            link = await self._state.db.update_mind_map_link_async(link_id, patch)
            self._emit_mind_map_link(link)
            if link:
                self._emit_mind_map(
                    self._state.db.load_mind_map(link.get("map_id", ""), include_counts=True)
                )
            return link
        except Exception:
            log.exception("Failed to update mind map link %s", link_id)
            raise

    async def delete_mind_map_link_async(self, link_id: str,
                                         **kwargs) -> dict | None:
        if not self._state.db:
            return None
        try:
            link = await self._state.db.delete_mind_map_link_async(link_id, **kwargs)
            self._emit_mind_map_link(link)
            if link:
                self._emit_mind_map(
                    self._state.db.load_mind_map(link.get("map_id", ""), include_counts=True)
                )
            return link
        except Exception:
            log.exception("Failed to delete mind map link %s", link_id)
            raise

    async def reorder_mind_map_links_async(self, map_id: str,
                                           link_order: list,
                                           **kwargs) -> list[dict]:
        if not self._state.db:
            return []
        try:
            links = await self._state.db.reorder_mind_map_links_async(
                map_id,
                link_order,
                **kwargs,
            )
            for link in links:
                self._emit_mind_map_link(link)
            self._emit_mind_map(self._state.db.load_mind_map(map_id, include_counts=True))
            return links
        except Exception:
            log.exception("Failed to reorder mind map links %s", map_id)
            raise
