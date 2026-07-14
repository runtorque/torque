"""MatrixState compatibility delegates for extracted domain services."""

from __future__ import annotations

from .state import BehaviorOverlayScope, Path


class StateServicesMixin:
    def _emit_initiative(self, initiative: dict | None) -> None:
        self._initiative_service._emit_initiative(initiative)

    def resolve_initiative_id(self, identifier: str, *, group: str = "") -> str:
        return self._initiative_service.resolve_initiative_id(
            identifier,
            group=group,
        )

    def list_initiatives(self, *, group: str = "",
                         include_archived: bool = False) -> list[dict]:
        return self._initiative_service.list_initiatives(
            group=group,
            include_archived=include_archived,
        )

    def load_initiative(self, initiative_id: str) -> dict | None:
        return self._initiative_service.load_initiative(initiative_id)

    def initiative_links(self, initiative_id: str,
                         link_type: str = "") -> list[dict]:
        return self._initiative_service.initiative_links(
            initiative_id,
            link_type,
        )

    def _initiative_linked_task_payload(self, task_ids: list[str], *,
                                        visible_task_ids: set[str] | None = None
                                        ) -> dict:
        return self._initiative_service._initiative_linked_task_payload(
            task_ids,
            visible_task_ids=visible_task_ids,
        )

    def initiative_payload(self, initiative_id: str, *,
                           visible_task_ids: set[str] | None = None,
                           visible_decision_ids: set[str] | None = None,
                           include_links: bool = True) -> dict | None:
        return self._initiative_service.initiative_payload(
            initiative_id,
            visible_task_ids=visible_task_ids,
            visible_decision_ids=visible_decision_ids,
            include_links=include_links,
        )

    async def create_initiative_async(self, row_dict: dict) -> dict | None:
        return await self._initiative_service.create_initiative_async(row_dict)

    async def update_initiative_async(self, initiative_id: str,
                                      patch: dict) -> dict | None:
        return await self._initiative_service.update_initiative_async(
            initiative_id,
            patch,
        )

    async def archive_initiative_async(self, initiative_id: str, **kwargs
                                       ) -> dict | None:
        return await self._initiative_service.archive_initiative_async(
            initiative_id,
            **kwargs,
        )

    async def save_initiative_link_async(self, initiative_id: str,
                                         link_type: str,
                                         target_id: str,
                                         **kwargs) -> dict | None:
        return await self._initiative_service.save_initiative_link_async(
            initiative_id,
            link_type,
            target_id,
            **kwargs,
        )

    async def delete_initiative_link_async(self, initiative_id: str,
                                           link_type: str,
                                           target_id: str) -> bool:
        return await self._initiative_service.delete_initiative_link_async(
            initiative_id,
            link_type,
            target_id,
        )

    def _emit_area(self, area: dict | None) -> None:
        self._area_service._emit_area(area)

    def resolve_area_id(self, identifier: str, *, group: str = "") -> str:
        return self._area_service.resolve_area_id(identifier, group=group)

    def list_areas(self, *, group: str = "", include_archived: bool = False,
                   limit: int = 100) -> list[dict]:
        return self._area_service.list_areas(
            group=group,
            include_archived=include_archived,
            limit=limit,
        )

    def load_area(self, area_id: str) -> dict | None:
        return self._area_service.load_area(area_id)

    def area_links(self, area_id: str, link_type: str = "") -> list[dict]:
        return self._area_service.area_links(area_id, link_type)

    def area_notes(self, area_id: str, *, include_archived: bool = False,
                   limit: int = 50) -> list[dict]:
        return self._area_service.area_notes(
            area_id,
            include_archived=include_archived,
            limit=limit,
        )

    def load_area_note(self, note_id) -> dict | None:
        return self._area_service.load_area_note(note_id)

    def area_payload(self, area_id: str, *,
                     visible_task_ids: set[str] | None = None,
                     visible_decision_ids: set[str] | None = None,
                     include_links: bool = True,
                     include_notes: bool = True,
                     decision_details: bool = False,
                     note_limit: int = 50) -> dict | None:
        return self._area_service.area_payload(
            area_id,
            visible_task_ids=visible_task_ids,
            visible_decision_ids=visible_decision_ids,
            include_links=include_links,
            include_notes=include_notes,
            decision_details=decision_details,
            note_limit=note_limit,
        )

    async def create_area_async(self, row_dict: dict) -> dict | None:
        return await self._area_service.create_area_async(row_dict)

    async def update_area_async(self, area_id: str, patch: dict) -> dict | None:
        return await self._area_service.update_area_async(area_id, patch)

    async def archive_area_async(self, area_id: str, **kwargs) -> dict | None:
        return await self._area_service.archive_area_async(area_id, **kwargs)

    async def save_area_link_async(self, area_id: str, link_type: str,
                                   target_id: str, **kwargs) -> dict | None:
        return await self._area_service.save_area_link_async(
            area_id,
            link_type,
            target_id,
            **kwargs,
        )

    async def delete_area_link_async(self, area_id: str, link_type: str,
                                     target_id: str, relation: str = "") -> bool:
        return await self._area_service.delete_area_link_async(
            area_id,
            link_type,
            target_id,
            relation,
        )

    async def create_area_note_async(self, area_id: str,
                                     row_dict: dict) -> dict | None:
        return await self._area_service.create_area_note_async(area_id, row_dict)

    async def update_area_note_async(self, note_id,
                                     patch: dict) -> dict | None:
        return await self._area_service.update_area_note_async(note_id, patch)

    async def archive_area_note_async(self, note_id, **kwargs) -> dict | None:
        return await self._area_service.archive_area_note_async(note_id, **kwargs)

    def thinking_snapshot(self, *, group: str='', include_archived: bool=False) -> dict:
        return self._thinking_service.thinking_snapshot(group=group, include_archived=include_archived)

    def _emit_scratchpad_note(self, note: dict | None) -> None:
        return self._thinking_service._emit_scratchpad_note(note)

    def _emit_mind_map(self, mind_map: dict | None) -> None:
        return self._thinking_service._emit_mind_map(mind_map)

    def _emit_mind_map_node(self, node: dict | None) -> None:
        return self._thinking_service._emit_mind_map_node(node)

    def _emit_mind_map_link(self, link: dict | None) -> None:
        return self._thinking_service._emit_mind_map_link(link)

    def resolve_scratchpad_note_id(self, identifier: str, *, group: str='') -> str:
        return self._thinking_service.resolve_scratchpad_note_id(identifier, group=group)

    def list_scratchpad_notes(self, *, group: str='', include_archived: bool=False, include_deleted: bool=False, limit: int=200) -> list[dict]:
        return self._thinking_service.list_scratchpad_notes(group=group, include_archived=include_archived, include_deleted=include_deleted, limit=limit)

    def load_scratchpad_note(self, note_id: str) -> dict | None:
        return self._thinking_service.load_scratchpad_note(note_id)

    async def create_scratchpad_note_async(self, row_dict: dict) -> dict | None:
        return await self._thinking_service.create_scratchpad_note_async(row_dict)

    async def update_scratchpad_note_async(self, note_id: str, patch: dict) -> dict | None:
        return await self._thinking_service.update_scratchpad_note_async(note_id, patch)

    async def archive_scratchpad_note_async(self, note_id: str, **kwargs) -> dict | None:
        return await self._thinking_service.archive_scratchpad_note_async(note_id, **kwargs)

    async def delete_scratchpad_note_async(self, note_id: str, **kwargs) -> dict | None:
        return await self._thinking_service.delete_scratchpad_note_async(note_id, **kwargs)

    def resolve_mind_map_id(self, identifier: str, *, group: str='') -> str:
        return self._thinking_service.resolve_mind_map_id(identifier, group=group)

    def list_mind_maps(self, *, group: str='', include_archived: bool=False, include_deleted: bool=False, include_counts: bool=True, limit: int=200) -> list[dict]:
        return self._thinking_service.list_mind_maps(group=group, include_archived=include_archived, include_deleted=include_deleted, include_counts=include_counts, limit=limit)

    def load_mind_map(self, map_id: str, *, include_counts: bool=False) -> dict | None:
        return self._thinking_service.load_mind_map(map_id, include_counts=include_counts)

    def mind_map_payload(self, map_id: str, *, include_archived: bool=False, include_deleted: bool=False) -> dict | None:
        return self._thinking_service.mind_map_payload(map_id, include_archived=include_archived, include_deleted=include_deleted)

    def load_mind_map_node(self, node_id: str) -> dict | None:
        return self._thinking_service.load_mind_map_node(node_id)

    def load_mind_map_link(self, link_id: str) -> dict | None:
        return self._thinking_service.load_mind_map_link(link_id)

    async def create_mind_map_async(self, row_dict: dict) -> dict | None:
        return await self._thinking_service.create_mind_map_async(row_dict)

    async def update_mind_map_async(self, map_id: str, patch: dict) -> dict | None:
        return await self._thinking_service.update_mind_map_async(map_id, patch)

    async def archive_mind_map_async(self, map_id: str, **kwargs) -> dict | None:
        return await self._thinking_service.archive_mind_map_async(map_id, **kwargs)

    async def delete_mind_map_async(self, map_id: str, **kwargs) -> dict | None:
        return await self._thinking_service.delete_mind_map_async(map_id, **kwargs)

    async def create_mind_map_node_async(self, map_id: str, row_dict: dict) -> dict | None:
        return await self._thinking_service.create_mind_map_node_async(map_id, row_dict)

    async def update_mind_map_node_async(self, node_id: str, patch: dict) -> dict | None:
        return await self._thinking_service.update_mind_map_node_async(node_id, patch)

    async def delete_mind_map_node_async(self, node_id: str, **kwargs) -> dict | None:
        return await self._thinking_service.delete_mind_map_node_async(node_id, **kwargs)

    async def reorder_mind_map_nodes_async(self, map_id: str, node_order: list, **kwargs) -> list[dict]:
        return await self._thinking_service.reorder_mind_map_nodes_async(map_id, node_order, **kwargs)

    async def create_mind_map_link_async(self, map_id: str, row_dict: dict) -> dict | None:
        return await self._thinking_service.create_mind_map_link_async(map_id, row_dict)

    async def update_mind_map_link_async(self, link_id: str, patch: dict) -> dict | None:
        return await self._thinking_service.update_mind_map_link_async(link_id, patch)

    async def delete_mind_map_link_async(self, link_id: str, **kwargs) -> dict | None:
        return await self._thinking_service.delete_mind_map_link_async(link_id, **kwargs)

    async def reorder_mind_map_links_async(self, map_id: str, link_order: list, **kwargs) -> list[dict]:
        return await self._thinking_service.reorder_mind_map_links_async(map_id, link_order, **kwargs)

    def idea_brief_snapshot(self, *, group: str='', include_archived: bool=False) -> dict:
        return self._idea_brief_service.idea_brief_snapshot(group=group, include_archived=include_archived)

    def _emit_idea_brief(self, brief: dict | None) -> None:
        return self._idea_brief_service._emit_idea_brief(brief)

    def resolve_idea_brief_id(self, identifier: str, *, group: str='') -> str:
        return self._idea_brief_service.resolve_idea_brief_id(identifier, group=group)

    def list_idea_briefs(self, *, group: str='', status: str='', include_archived: bool=False, created_by_id: str='', limit: int=200) -> list[dict]:
        return self._idea_brief_service.list_idea_briefs(group=group, status=status, include_archived=include_archived, created_by_id=created_by_id, limit=limit)

    def load_idea_brief(self, brief_id: str) -> dict | None:
        return self._idea_brief_service.load_idea_brief(brief_id)

    def _idea_brief_link_error(self, message: str) -> ValueError:
        return self._idea_brief_service._idea_brief_link_error(message)

    def normalize_idea_brief_thinking_links(self, links, *, group: str) -> list[dict]:
        return self._idea_brief_service.normalize_idea_brief_thinking_links(links, group=group)

    def idea_brief_contract(self) -> dict:
        return self._idea_brief_service.idea_brief_contract()

    async def create_idea_brief_async(self, row_dict: dict) -> dict | None:
        return await self._idea_brief_service.create_idea_brief_async(row_dict)

    async def update_idea_brief_async(self, brief_id: str, patch: dict) -> dict | None:
        return await self._idea_brief_service.update_idea_brief_async(brief_id, patch)

    async def refine_idea_brief_async(self, brief_id: str, patch: dict) -> dict | None:
        return await self._idea_brief_service.refine_idea_brief_async(brief_id, patch)

    async def park_idea_brief_async(self, brief_id: str, **kwargs) -> dict | None:
        return await self._idea_brief_service.park_idea_brief_async(brief_id, **kwargs)

    async def archive_idea_brief_async(self, brief_id: str, **kwargs) -> dict | None:
        return await self._idea_brief_service.archive_idea_brief_async(brief_id, **kwargs)

    async def propose_idea_brief_async(self, brief_id: str, **kwargs) -> dict | None:
        return await self._idea_brief_service.propose_idea_brief_async(brief_id, **kwargs)

    def load_decision(self, decision_id: str) -> dict | None:
        return self._architect_governance_service.load_decision(decision_id)

    def save_decision(self, row_dict: dict) -> dict | None:
        return self._architect_governance_service.save_decision(row_dict)

    async def save_decision_async(self, row_dict: dict) -> dict | None:
        return await self._architect_governance_service.save_decision_async(row_dict)

    def load_decisions_for_architect(self, architect_id: str, *, include_archived: bool=False) -> list[dict]:
        return self._architect_governance_service.load_decisions_for_architect(architect_id, include_archived=include_archived)

    def load_all_decisions(self, *, include_archived: bool=False) -> list[dict]:
        return self._architect_governance_service.load_all_decisions(include_archived=include_archived)

    def delete_decision(self, decision_id: str) -> dict | None:
        return self._architect_governance_service.delete_decision(decision_id)

    def hard_delete_decision(self, decision_id: str) -> None:
        return self._architect_governance_service.hard_delete_decision(decision_id)

    def load_pending_hire(self, hire_id: str) -> dict | None:
        return self._architect_governance_service.load_pending_hire(hire_id)

    def save_pending_hire(self, row_dict: dict) -> dict | None:
        return self._architect_governance_service.save_pending_hire(row_dict)

    async def save_pending_hire_async(self, row_dict: dict) -> dict | None:
        return await self._architect_governance_service.save_pending_hire_async(row_dict)

    def load_pending_hires(self, *, status_filter: str='', architect_id: str='') -> list[dict]:
        return self._architect_governance_service.load_pending_hires(status_filter=status_filter, architect_id=architect_id)

    def delete_pending_hire(self, hire_id: str) -> None:
        return self._architect_governance_service.delete_pending_hire(hire_id)

    def _behavior_scope_for_agent(self, agent_id: str) -> BehaviorOverlayScope:
        return self._behavior_overlay_service._behavior_scope_for_agent(agent_id)

    def _behavior_scope_from_args(self, *, agent_id: str='', scope_kind: str='', scope_group: str='', scope_key: str='', group: str='', role_kind: str='') -> BehaviorOverlayScope:
        return self._behavior_overlay_service._behavior_scope_from_args(agent_id=agent_id, scope_kind=scope_kind, scope_group=scope_group, scope_key=scope_key, group=group, role_kind=role_kind)

    def _behavior_overlay_scope_payload(self, scope: BehaviorOverlayScope) -> dict:
        return self._behavior_overlay_service._behavior_overlay_scope_payload(scope)

    def _behavior_overlay_scope_target_kind(self, scope: BehaviorOverlayScope) -> str:
        return self._behavior_overlay_service._behavior_overlay_scope_target_kind(scope)

    def _emit_behavior_overlay_version(self, version: dict | None):
        return self._behavior_overlay_service._emit_behavior_overlay_version(version)

    def _emit_behavior_overlay_active(self, active: dict | None, agent_id: str='', scope: BehaviorOverlayScope | None=None):
        return self._behavior_overlay_service._emit_behavior_overlay_active(active, agent_id, scope)

    def _emit_behavior_overlay_proposal(self, proposal: dict | None):
        return self._behavior_overlay_service._emit_behavior_overlay_proposal(proposal)

    def load_behavior_overlay_version(self, version_id: str) -> dict | None:
        return self._behavior_overlay_service.load_behavior_overlay_version(version_id)

    def load_behavior_overlay_active(self, agent_id: str='', **scope_kwargs) -> dict | None:
        return self._behavior_overlay_service.load_behavior_overlay_active(agent_id, **scope_kwargs)

    def load_behavior_overlay_active_version(self, agent_id: str='', **scope_kwargs) -> dict | None:
        return self._behavior_overlay_service.load_behavior_overlay_active_version(agent_id, **scope_kwargs)

    def list_behavior_overlay_versions(self, agent_id: str='', *, limit: int=50, **scope_kwargs) -> list[dict]:
        return self._behavior_overlay_service.list_behavior_overlay_versions(agent_id, limit=limit, **scope_kwargs)

    def load_behavior_overlay_proposal(self, proposal_id: str) -> dict | None:
        return self._behavior_overlay_service.load_behavior_overlay_proposal(proposal_id)

    def list_behavior_overlay_proposals(self, *, status_filter: str='', agent_id: str='', scope_kind: str='', scope_group: str='', scope_key: str='', group: str='', role_kind: str='', next_actor_kind: str='', proposed_by_agent_id: str='', limit: int=100) -> list[dict]:
        return self._behavior_overlay_service.list_behavior_overlay_proposals(status_filter=status_filter, agent_id=agent_id, scope_kind=scope_kind, scope_group=scope_group, scope_key=scope_key, group=group, role_kind=role_kind, next_actor_kind=next_actor_kind, proposed_by_agent_id=proposed_by_agent_id, limit=limit)

    def ensure_behavior_overlay_seed(self, agent_id: str='', *, scope_kind: str='', scope_group: str='', scope_key: str='', group: str='', role_kind: str='', actor_kind: str='system', actor_id: str='', reason: str='default empty behavior overlay seed') -> dict | None:
        return self._behavior_overlay_service.ensure_behavior_overlay_seed(agent_id, scope_kind=scope_kind, scope_group=scope_group, scope_key=scope_key, group=group, role_kind=role_kind, actor_kind=actor_kind, actor_id=actor_id, reason=reason)

    def render_behavior_overlay_for_agent(self, agent_id: str, *, seed: bool=False) -> str:
        return self._behavior_overlay_service.render_behavior_overlay_for_agent(agent_id, seed=seed)

    def _behavior_overlay_valid_layer(self, scope: BehaviorOverlayScope, version: dict | None, *, include_empty: bool) -> tuple[BehaviorOverlayScope, dict, str] | None:
        return self._behavior_overlay_service._behavior_overlay_valid_layer(scope, version, include_empty=include_empty)

    def render_behavior_overlay_stack_for_cell(self, cell, *, include_role: bool=True, include_agent: bool=True, seed_agent: bool=True, seed_role: bool=False, worker_dispatch: bool=False) -> str:
        return self._behavior_overlay_service.render_behavior_overlay_stack_for_cell(cell, include_role=include_role, include_agent=include_agent, seed_agent=seed_agent, seed_role=seed_role, worker_dispatch=worker_dispatch)

    def _behavior_overlay_current_base(self, scope: BehaviorOverlayScope) -> dict | None:
        return self._behavior_overlay_service._behavior_overlay_current_base(scope)

    def _behavior_overlay_route(self, scope: BehaviorOverlayScope, target, author_kind: str) -> tuple[str, bool]:
        return self._behavior_overlay_service._behavior_overlay_route(scope, target, author_kind)

    def create_behavior_overlay_proposal(self, *, agent_id: str='', scope_kind: str='', scope_group: str='', scope_key: str='', group: str='', role_kind: str='', proposed_by_agent_id: str='', proposed_by_kind: str='', text: str='', rationale: str='', proposal_type: str='set_text', target_version_id: str='', expected_base_version_id: str='', idempotency_key: str='', architect_approver_id: str='', auto_apply_architect_direct: bool=False) -> dict:
        return self._behavior_overlay_service.create_behavior_overlay_proposal(agent_id=agent_id, scope_kind=scope_kind, scope_group=scope_group, scope_key=scope_key, group=group, role_kind=role_kind, proposed_by_agent_id=proposed_by_agent_id, proposed_by_kind=proposed_by_kind, text=text, rationale=rationale, proposal_type=proposal_type, target_version_id=target_version_id, expected_base_version_id=expected_base_version_id, idempotency_key=idempotency_key, architect_approver_id=architect_approver_id, auto_apply_architect_direct=auto_apply_architect_direct)

    def _behavior_overlay_next_version_number(self, scope: BehaviorOverlayScope) -> int:
        return self._behavior_overlay_service._behavior_overlay_next_version_number(scope)

    def apply_behavior_overlay_proposal(self, proposal_id: str, *, actor_kind: str, actor_id: str='', note: str='') -> dict:
        return self._behavior_overlay_service.apply_behavior_overlay_proposal(proposal_id, actor_kind=actor_kind, actor_id=actor_id, note=note)

    def architect_approve_behavior_overlay_proposal(self, proposal_id: str, *, architect_id: str, expected_proposed_text_sha256: str='', note: str='') -> dict:
        return self._behavior_overlay_service.architect_approve_behavior_overlay_proposal(proposal_id, architect_id=architect_id, expected_proposed_text_sha256=expected_proposed_text_sha256, note=note)

    def reject_behavior_overlay_proposal(self, proposal_id: str, *, actor_kind: str, actor_id: str='', note: str='') -> dict:
        return self._behavior_overlay_service.reject_behavior_overlay_proposal(proposal_id, actor_kind=actor_kind, actor_id=actor_id, note=note)

    def behavior_overlay_diff_payload(self, *, proposal_id: str='', from_version_id: str='', to_version_id: str='', agent_id: str='', scope_kind: str='', scope_group: str='', scope_key: str='', group: str='', role_kind: str='') -> dict:
        return self._behavior_overlay_service.behavior_overlay_diff_payload(proposal_id=proposal_id, from_version_id=from_version_id, to_version_id=to_version_id, agent_id=agent_id, scope_kind=scope_kind, scope_group=scope_group, scope_key=scope_key, group=group, role_kind=role_kind)

    def create_behavior_overlay_user_task(self, proposal_id: str, *, note: str='') -> str:
        return self._behavior_overlay_service.create_behavior_overlay_user_task(proposal_id, note=note)

    def resolve_behavior_overlay_user_task(self, task_id: str, *, status: str, note: str='') -> None:
        return self._behavior_overlay_service.resolve_behavior_overlay_user_task(task_id, status=status, note=note)

    def cancel_behavior_overlay_proposals_for_agent(self, agent_id: str, *, reason: str, actor_kind: str='system', actor_id: str='') -> int:
        return self._behavior_overlay_service.cancel_behavior_overlay_proposals_for_agent(agent_id, reason=reason, actor_kind=actor_kind, actor_id=actor_id)

    def clear_behavior_overlay_active_for_agent(self, agent_id: str, *, reason: str='agent deleted') -> bool:
        return self._behavior_overlay_service.clear_behavior_overlay_active_for_agent(agent_id, reason=reason)

    def cleanup_behavior_overlay_for_agent_delete(self, agent_id: str, *, reason: str='agent deleted') -> dict:
        return self._behavior_overlay_service.cleanup_behavior_overlay_for_agent_delete(agent_id, reason=reason)

    def cleanup_behavior_overlay_for_architect_delete(self, architect_id: str, *, hired_engineer_ids: list[str] | None=None, reason: str='architect deleted') -> dict:
        return self._behavior_overlay_service.cleanup_behavior_overlay_for_architect_delete(architect_id, hired_engineer_ids=hired_engineer_ids, reason=reason)

    def _architect_journal_path(self, architect_id: str) -> Path:
        return self._journal_service._architect_journal_path(architect_id)

    def _architect_journal_entry_id(self, architect_id: str, idempotency_key: str) -> str:
        return self._journal_service._architect_journal_entry_id(architect_id, idempotency_key)

    def _recover_architect_journal_entry(self, architect_id: str, *, record_id: str, request_hash: str='') -> dict | None:
        return self._journal_service._recover_architect_journal_entry(architect_id, record_id=record_id, request_hash=request_hash)

    def architect_journal_append(self, architect_id: str, entry_type: str, entry: str, *, idempotency_key: str='', request_hash: str='') -> dict:
        return self._journal_service.architect_journal_append(architect_id, entry_type, entry, idempotency_key=idempotency_key, request_hash=request_hash)

    def architect_journal_read(self, architect_id: str, *, since: float=0, limit: int=20) -> list[dict]:
        return self._journal_service.architect_journal_read(architect_id, since=since, limit=limit)

    def _append_engineer_worklog_entry(self, group: str, entry: dict):
        return self._journal_service._append_engineer_worklog_entry(group, entry)

    def engineer_worklog_read(self, group: str, limit: int=50) -> list[dict]:
        return self._journal_service.engineer_worklog_read(group, limit)
