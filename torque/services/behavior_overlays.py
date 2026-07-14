"""Dynamic Behavior overlay state orchestration."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from ..behavior_overlay import (
    BEHAVIOR_OVERLAY_COMBINED_MAX_BYTES,
    BEHAVIOR_OVERLAY_ROLE_KINDS,
    BehaviorOverlayScope,
    BehaviorOverlayValidationError,
    DEFAULT_BEHAVIOR_OVERLAY_TEXT,
    behavior_overlay_diff,
    coerce_behavior_overlay_scope,
    lint_overlay_text,
    overlay_text_bytes,
    overlay_text_sha256,
    proposal_summary,
    render_behavior_overlay_block,
    validate_overlay_text,
    version_summary,
)
from ..config import log


class BehaviorOverlayService:
    def __init__(self, state: Any):
        self._state = state

    def _behavior_scope_for_agent(self, agent_id: str) -> BehaviorOverlayScope:
        agent_id = str(agent_id or "").strip()
        cell = self._state.agents.get(agent_id)
        group = str(getattr(cell, "group", "") or "").strip() if cell else ""
        return BehaviorOverlayScope.agent(agent_id, group=group)

    def _behavior_scope_from_args(
            self,
            *,
            agent_id: str = "",
            scope_kind: str = "",
            scope_group: str = "",
            scope_key: str = "",
            group: str = "",
            role_kind: str = "") -> BehaviorOverlayScope:
        if str(role_kind or "").strip():
            return BehaviorOverlayScope.role(
                str(group or scope_group or "").strip(),
                str(role_kind or "").strip(),
            )
        if str(scope_kind or "").strip() == "role":
            return BehaviorOverlayScope.role(
                str(group or scope_group or "").strip(),
                str(scope_key or "").strip(),
            )
        if str(scope_kind or "").strip() == "agent" and scope_key:
            return self._behavior_scope_for_agent(str(scope_key or ""))
        return self._behavior_scope_for_agent(str(agent_id or scope_key or ""))

    def _behavior_overlay_scope_payload(
            self, scope: BehaviorOverlayScope) -> dict:
        return scope.as_row_fields()

    def _behavior_overlay_scope_target_kind(
            self, scope: BehaviorOverlayScope) -> str:
        if scope.scope_kind == "role":
            return scope.scope_key
        target = self._state.agents.get(scope.scope_key)
        return str(getattr(target, "kind", "") or "").strip() if target else ""

    def _emit_behavior_overlay_version(self, version: dict | None):
        payload = version_summary(version)
        if payload:
            self._state._emit("behavior_overlay_version_append", **payload)

    def _emit_behavior_overlay_active(self, active: dict | None,
                                      agent_id: str = "",
                                      scope: BehaviorOverlayScope | None = None):
        if active:
            self._state._emit("behavior_overlay_active_update", **dict(active))
            return
        try:
            scope_obj = scope or self._behavior_scope_for_agent(agent_id)
        except Exception:
            scope_obj = None
        if scope_obj:
            self._state._emit(
                "behavior_overlay_active_update",
                **scope_obj.as_row_fields(),
                active_version_id="",
                updated_at=time.time(),
                updated_by_kind="system",
                updated_by_id="",
                reason="cleared",
            )

    def _emit_behavior_overlay_proposal(self, proposal: dict | None):
        payload = proposal_summary(proposal)
        if not payload:
            return
        status = str(payload.get("status", "") or "")
        op = (
            "behavior_overlay_proposal_upsert"
            if status in {"proposed", "approved"}
            else "behavior_overlay_proposal_resolve"
        )
        self._state._emit(op, **payload)

    def load_behavior_overlay_version(self, version_id: str) -> dict | None:
        if self._state.db:
            try:
                return self._state.db.load_behavior_overlay_version(version_id)
            except Exception:
                log.exception("Failed to load behavior overlay version %s",
                              version_id)
        return None

    def load_behavior_overlay_active(self, agent_id: str = "", **scope_kwargs) -> dict | None:
        if self._state.db:
            try:
                if scope_kwargs:
                    scope = self._behavior_scope_from_args(
                        agent_id=agent_id,
                        **scope_kwargs,
                    )
                    return self._state.db.load_behavior_overlay_active(scope)
                return self._state.db.load_behavior_overlay_active(
                    self._behavior_scope_for_agent(agent_id)
                )
            except Exception:
                log.exception("Failed to load behavior overlay active %s",
                              agent_id)
        return None

    def load_behavior_overlay_active_version(
            self, agent_id: str = "", **scope_kwargs) -> dict | None:
        if self._state.db:
            try:
                if scope_kwargs:
                    scope = self._behavior_scope_from_args(
                        agent_id=agent_id,
                        **scope_kwargs,
                    )
                    return self._state.db.load_behavior_overlay_active_version(scope)
                return self._state.db.load_behavior_overlay_active_version(
                    self._behavior_scope_for_agent(agent_id)
                )
            except Exception:
                log.exception(
                    "Failed to load behavior overlay active version %s",
                    agent_id,
                )
        return None

    def list_behavior_overlay_versions(
            self, agent_id: str = "", *, limit: int = 50,
            **scope_kwargs) -> list[dict]:
        if self._state.db:
            try:
                if scope_kwargs:
                    scope = self._behavior_scope_from_args(
                        agent_id=agent_id,
                        **scope_kwargs,
                    )
                    return self._state.db.list_behavior_overlay_versions(
                        scope,
                        limit=limit,
                    )
                return self._state.db.list_behavior_overlay_versions(
                    self._behavior_scope_for_agent(agent_id),
                    limit=limit,
                )
            except Exception:
                log.exception("Failed to list behavior overlay versions %s",
                              agent_id)
        return []

    def load_behavior_overlay_proposal(self, proposal_id: str) -> dict | None:
        if self._state.db:
            try:
                return self._state.db.load_behavior_overlay_proposal(proposal_id)
            except Exception:
                log.exception("Failed to load behavior overlay proposal %s",
                              proposal_id)
        return None

    def list_behavior_overlay_proposals(
            self, *,
            status_filter: str = "",
            agent_id: str = "",
            scope_kind: str = "",
            scope_group: str = "",
            scope_key: str = "",
            group: str = "",
            role_kind: str = "",
            next_actor_kind: str = "",
            proposed_by_agent_id: str = "",
            limit: int = 100) -> list[dict]:
        if self._state.db:
            try:
                scope = None
                if scope_kind or scope_key or role_kind:
                    scope = self._behavior_scope_from_args(
                        agent_id=agent_id,
                        scope_kind=scope_kind,
                        scope_group=scope_group,
                        scope_key=scope_key,
                        group=group,
                        role_kind=role_kind,
                    )
                return self._state.db.list_behavior_overlay_proposals(
                    status_filter=status_filter,
                    agent_id=agent_id,
                    scope=scope,
                    next_actor_kind=next_actor_kind,
                    proposed_by_agent_id=proposed_by_agent_id,
                    limit=limit,
                )
            except Exception:
                log.exception("Failed to list behavior overlay proposals")
        return []

    def ensure_behavior_overlay_seed(
            self,
            agent_id: str = "",
            *,
            scope_kind: str = "",
            scope_group: str = "",
            scope_key: str = "",
            group: str = "",
            role_kind: str = "",
            actor_kind: str = "system",
            actor_id: str = "",
            reason: str = "default empty behavior overlay seed") -> dict | None:
        """Ensure an explicit empty floor version + active row exists."""
        if not self._state.db:
            return None
        try:
            scope = self._behavior_scope_from_args(
                agent_id=agent_id,
                scope_kind=scope_kind,
                scope_group=scope_group,
                scope_key=scope_key,
                group=group,
                role_kind=role_kind,
            )
        except Exception:
            return None
        active_version = self._state.db.load_behavior_overlay_active_version(scope)
        if active_version:
            return active_version
        now = time.time()
        metadata = {
            "default_empty": True,
            "scope_label": scope.label,
            "max_bytes": scope.max_bytes,
        }
        version = self._state.db.save_behavior_overlay_version({
            "id": "bov-" + uuid.uuid4().hex[:12],
            **self._behavior_overlay_scope_payload(scope),
            "version_number": self._state.db.next_behavior_overlay_version_number(scope),
            "parent_version_id": "",
            "text": DEFAULT_BEHAVIOR_OVERLAY_TEXT,
            "text_sha256": overlay_text_sha256(DEFAULT_BEHAVIOR_OVERLAY_TEXT),
            "author_agent_id": str(actor_id or ""),
            "author_kind": actor_kind,
            "rationale": reason,
            "approver_id": str(actor_id or ""),
            "approver_kind": actor_kind,
            "source_proposal_id": "",
            "created_at": now,
            "metadata": metadata,
        })
        active = self._state.db.save_behavior_overlay_active({
            **self._behavior_overlay_scope_payload(scope),
            "active_version_id": version["id"],
            "updated_at": now,
            "updated_by_kind": actor_kind,
            "updated_by_id": str(actor_id or ""),
            "reason": reason,
        })
        activation = self._state.db.save_behavior_overlay_activation({
            "id": "boa-" + uuid.uuid4().hex[:12],
            **self._behavior_overlay_scope_payload(scope),
            "previous_version_id": "",
            "active_version_id": version["id"],
            "proposal_id": "",
            "actor_kind": actor_kind,
            "actor_id": str(actor_id or ""),
            "action": "seed",
            "reason": reason,
            "created_at": now,
        })
        del activation
        self._state._emit_behavior_overlay_version(version)
        self._state._emit_behavior_overlay_active(active)
        return version

    def render_behavior_overlay_for_agent(
            self, agent_id: str, *, seed: bool = False) -> str:
        """Return the rendered prompt block for a supported agent."""
        agent_id = str(agent_id or "").strip()
        scope = self._behavior_scope_for_agent(agent_id)
        version = (
            self.ensure_behavior_overlay_seed(agent_id) if seed
            else self._state.db.load_behavior_overlay_active_version(scope) if self._state.db else None
        )
        return render_behavior_overlay_block(
            **scope.as_row_fields(),
            version_id=str((version or {}).get("id", "") or ""),
            text=str((version or {}).get("text", "") or ""),
            sha256=str((version or {}).get("text_sha256", "") or ""),
            fail_closed=True,
        )

    def _behavior_overlay_valid_layer(
            self,
            scope: BehaviorOverlayScope,
            version: dict | None,
            *,
            include_empty: bool) -> tuple[BehaviorOverlayScope, dict, str] | None:
        if not version:
            return None
        text = str(version.get("text", "") or "")
        if not text and not include_empty:
            return None
        try:
            text = validate_overlay_text(
                text,
                scope_kind=scope.scope_kind,
                max_bytes=scope.max_bytes,
            )
        except BehaviorOverlayValidationError:
            log.warning(
                "Dropping invalid behavior overlay layer scope=%s version=%s",
                scope.scope_id,
                version.get("id", ""),
            )
            return None
        return scope, version, text

    def render_behavior_overlay_stack_for_cell(
            self,
            cell,
            *,
            include_role: bool = True,
            include_agent: bool = True,
            seed_agent: bool = True,
            seed_role: bool = False,
            worker_dispatch: bool = False) -> str:
        """Render role then agent overlay blocks for a cell.

        Empty role overlays are omitted to preserve zero behavior delta on
        rollout.  The Phase-1 empty agent seed block is preserved when
        ``seed_agent`` is true for persistent Architect/Engineer prompts.
        """
        if not cell or not self._state.db:
            return ""
        kind = str(getattr(cell, "kind", "") or "").strip()
        group = str(getattr(cell, "group", "") or "").strip()
        layers: list[tuple[BehaviorOverlayScope, dict, str]] = []
        if include_role and kind in BEHAVIOR_OVERLAY_ROLE_KINDS and group:
            role_scope = BehaviorOverlayScope.role(group, kind)
            role_version = (
                self.ensure_behavior_overlay_seed(
                    scope_kind="role",
                    scope_group=group,
                    scope_key=kind,
                )
                if seed_role else
                self._state.db.load_behavior_overlay_active_version(role_scope)
            )
            layer = self._behavior_overlay_valid_layer(
                role_scope,
                role_version,
                include_empty=False,
            )
            if layer:
                layers.append(layer)
        if include_agent and kind in {"architect", "engineer"}:
            agent_scope = self._behavior_scope_for_agent(
                str(getattr(cell, "id", "") or "")
            )
            agent_version = (
                self.ensure_behavior_overlay_seed(agent_scope.scope_key)
                if seed_agent else
                self._state.db.load_behavior_overlay_active_version(agent_scope)
            )
            layer = self._behavior_overlay_valid_layer(
                agent_scope,
                agent_version,
                include_empty=bool(seed_agent),
            )
            if layer:
                layers.append(layer)
        # Render-time combined body cap: drop less-specific role first.
        if sum(overlay_text_bytes(text) for _s, _v, text in layers) > (
                BEHAVIOR_OVERLAY_COMBINED_MAX_BYTES):
            role_layers = [
                layer for layer in layers if layer[0].scope_kind == "role"
            ]
            if role_layers:
                log.warning(
                    "Dropping role behavior overlay on combined cap overflow: %s",
                    ", ".join(layer[0].scope_id for layer in role_layers),
                )
            layers = [
                layer for layer in layers if layer[0].scope_kind != "role"
            ]
        if sum(overlay_text_bytes(text) for _s, _v, text in layers) > (
                BEHAVIOR_OVERLAY_COMBINED_MAX_BYTES):
            log.warning(
                "Dropping behavior overlay stack on combined cap overflow for cell=%s",
                getattr(cell, "id", ""),
            )
            layers = []
        blocks = []
        for scope, version, text in layers:
            blocks.append(render_behavior_overlay_block(
                **scope.as_row_fields(),
                version_id=str(version.get("id", "") or ""),
                text=text,
                sha256=str(version.get("text_sha256", "") or ""),
                fail_closed=True,
                worker_dispatch=worker_dispatch and scope.scope_kind == "role",
            ).rstrip())
        return ("\n\n".join(blocks) + "\n") if blocks else ""

    def _behavior_overlay_current_base(
            self, scope: BehaviorOverlayScope) -> dict | None:
        return self.ensure_behavior_overlay_seed(
            agent_id=scope.agent_id,
            scope_kind=scope.scope_kind,
            scope_group=scope.scope_group,
            scope_key=scope.scope_key,
        )

    def _behavior_overlay_route(
            self,
            scope: BehaviorOverlayScope,
            target,
            author_kind: str) -> tuple[str, bool]:
        if scope.scope_kind == "role":
            if str(author_kind or "").strip() == "user":
                return "user", True
            if str(author_kind or "").strip() != "architect":
                raise ValueError(
                    "role behavior overlays are architect-authored and "
                    "user-approved in v1; engineer role writes are not supported"
                )
            return "user", True
        target_kind = str(getattr(target, "kind", "") or "").strip()
        if str(author_kind or "").strip() == "user":
            return "user", True
        if target_kind == "architect":
            return "user", True
        if target_kind != "engineer":
            raise ValueError("behavior overlays are supported only for architects and engineers")
        group = str(getattr(target, "group", "") or "").strip()
        requires_user = bool(
            getattr(
                self._state.get_group_settings(group),
                "engineer_behavior_requires_user_approval",
                False,
            )
        )
        return ("architect_then_user" if requires_user else "architect",
                requires_user)

    def create_behavior_overlay_proposal(
            self,
            *,
            agent_id: str = "",
            scope_kind: str = "",
            scope_group: str = "",
            scope_key: str = "",
            group: str = "",
            role_kind: str = "",
            proposed_by_agent_id: str = "",
            proposed_by_kind: str = "",
            text: str = "",
            rationale: str = "",
            proposal_type: str = "set_text",
            target_version_id: str = "",
            expected_base_version_id: str = "",
            idempotency_key: str = "",
            architect_approver_id: str = "",
            auto_apply_architect_direct: bool = False) -> dict:
        """Create a governed overlay proposal.

        Routes are computed and persisted at creation time.  ``auto_apply`` is
        used only for architect-authored engineer edits when the group setting
        leaves the architect as final authority.
        """
        if not self._state.db:
            raise RuntimeError("database is required for behavior overlays")
        author_id = str(proposed_by_agent_id or "").strip()
        author_kind = str(proposed_by_kind or "").strip()
        scope = self._behavior_scope_from_args(
            agent_id=agent_id,
            scope_kind=scope_kind,
            scope_group=scope_group,
            scope_key=scope_key,
            group=group,
            role_kind=role_kind,
        )
        idempotency_key = str(idempotency_key or "").strip()
        if idempotency_key:
            existing = self._state.db.load_behavior_overlay_proposal_by_idempotency(
                author_id,
                idempotency_key,
                scope,
            )
            if existing:
                return existing
        target = None
        if scope.scope_kind == "agent":
            target = self._state.agents.get(scope.scope_key)
            if not target or str(getattr(target, "cell_type", "") or "") != "agent":
                raise ValueError("target agent not found")
            target_kind = str(getattr(target, "kind", "") or "").strip()
            if target_kind not in {"architect", "engineer"}:
                raise ValueError("worker behavior overlays are not supported in v1")
        else:
            target_kind = scope.scope_key
            author = self._state.agents.get(author_id)
            if author_kind not in {"architect", "user"}:
                raise ValueError(
                    "role behavior overlays are architect-authored and "
                    "user-approved in v1; engineer role writes are not supported"
                )
            if author_kind == "architect" and (
                    not author
                    or str(getattr(author, "kind", "") or "") != "architect"
                    or str(getattr(author, "group", "") or "") != scope.scope_group
                    or int(getattr(author, "dismissed_at", 0) or 0) > 0
                    or float(getattr(author, "deleted_at", 0.0) or 0.0) > 0):
                raise ValueError("active architect in scope group is required for role behavior overlays")
        proposal_type = str(proposal_type or "set_text").strip() or "set_text"
        if proposal_type not in {"set_text", "rollback"}:
            raise ValueError("proposal_type must be set_text or rollback")

        base = self._behavior_overlay_current_base(scope)
        if not base:
            raise RuntimeError("failed to initialize behavior overlay base")
        base_version_id = str(base.get("id", "") or "")
        expected_base_version_id = str(expected_base_version_id or "").strip()
        if expected_base_version_id and expected_base_version_id != base_version_id:
            raise ValueError("stale behavior overlay base version")

        route, requires_user = self._behavior_overlay_route(scope, target, author_kind)
        if proposal_type == "set_text":
            proposed_text = validate_overlay_text(
                str(text or ""),
                scope_kind=scope.scope_kind,
                max_bytes=scope.max_bytes,
            )
            target_version_id = ""
        else:
            target_version_id = str(target_version_id or "").strip()
            target_version = self.load_behavior_overlay_version(target_version_id)
            if (
                    not target_version
                    or target_version.get("scope_kind") != scope.scope_kind
                    or target_version.get("scope_group") != scope.scope_group
                    or target_version.get("scope_key") != scope.scope_key):
                raise ValueError("rollback target version not found for scope")
            proposed_text = validate_overlay_text(
                str(target_version.get("text", "") or ""),
                scope_kind=scope.scope_kind,
                max_bytes=scope.max_bytes,
            )
        warnings = lint_overlay_text(proposed_text)
        now = time.time()
        status = "proposed"
        next_actor = "user" if route == "user" else "architect"
        arch_id = ""
        arch_approved_at = None
        if scope.scope_kind == "role":
            next_actor = "user"
        elif (
                auto_apply_architect_direct
                and author_kind == "architect"
                and target_kind == "engineer"
                and route == "architect"):
            arch_id = str(architect_approver_id or author_id)
            arch_approved_at = now
        elif (
                author_kind == "architect"
                and target_kind == "engineer"
                and route == "architect_then_user"):
            # Architect-authored direct edit is already architect-endorsed, but
            # the persisted route still captures the setting-gated user step.
            status = "approved"
            next_actor = "user"
            arch_id = str(architect_approver_id or author_id)
            arch_approved_at = now
        proposal = self._state.db.save_behavior_overlay_proposal({
            "id": "bop-" + uuid.uuid4().hex[:12],
            **self._behavior_overlay_scope_payload(scope),
            "target_kind": target_kind,
            "proposal_type": proposal_type,
            "base_version_id": base_version_id,
            "target_version_id": target_version_id,
            "proposed_text": proposed_text,
            "proposed_text_sha256": overlay_text_sha256(proposed_text),
            "proposed_by_agent_id": author_id,
            "proposed_by_kind": author_kind,
            "rationale": str(rationale or ""),
            "status": status,
            "approval_route": route,
            "next_actor_kind": next_actor,
            "requires_user_approval": requires_user,
            "architect_approver_id": arch_id,
            "architect_approved_at": arch_approved_at,
            "lint_warnings": warnings,
            "idempotency_key": idempotency_key,
            "created_at": now,
            "updated_at": now,
        })
        self._state._emit_behavior_overlay_proposal(proposal)
        if scope.scope_kind == "agent" and auto_apply_architect_direct and route == "architect":
            proposal = self.apply_behavior_overlay_proposal(
                proposal["id"],
                actor_kind="architect",
                actor_id=str(architect_approver_id or author_id),
                note=str(rationale or ""),
            )
        return proposal

    def _behavior_overlay_next_version_number(self, scope: BehaviorOverlayScope) -> int:
        if not self._state.db:
            return 0
        return self._state.db.next_behavior_overlay_version_number(scope)

    def apply_behavior_overlay_proposal(
            self,
            proposal_id: str,
            *,
            actor_kind: str,
            actor_id: str = "",
            note: str = "") -> dict:
        if not self._state.db:
            raise RuntimeError("database is required for behavior overlays")
        proposal = self.load_behavior_overlay_proposal(proposal_id)
        if not proposal:
            raise ValueError("behavior overlay proposal not found")
        if proposal.get("status") == "applied":
            return proposal
        if proposal.get("status") == "rejected":
            raise ValueError("behavior overlay proposal has already been rejected")
        scope = coerce_behavior_overlay_scope(proposal)
        active = self._state.db.load_behavior_overlay_active(scope)
        active_version_id = str((active or {}).get("active_version_id", "") or "")
        if active_version_id != str(proposal.get("base_version_id", "") or ""):
            raise ValueError("stale behavior overlay base version")

        now = time.time()
        proposal_type = str(proposal.get("proposal_type", "") or "set_text")
        if proposal_type == "rollback":
            target_version = self.load_behavior_overlay_version(
                proposal.get("target_version_id", "")
            )
            if (
                    not target_version
                    or target_version.get("scope_kind") != scope.scope_kind
                    or target_version.get("scope_group") != scope.scope_group
                    or target_version.get("scope_key") != scope.scope_key):
                raise ValueError("rollback target version not found for scope")
            validate_overlay_text(
                str(target_version.get("text", "") or ""),
                scope_kind=scope.scope_kind,
                max_bytes=scope.max_bytes,
            )
            new_active_version_id = str(target_version.get("id", "") or "")
            action = "rollback"
            version = target_version
        else:
            proposed_text = validate_overlay_text(
                str(proposal.get("proposed_text", "") or ""),
                scope_kind=scope.scope_kind,
                max_bytes=scope.max_bytes,
            )
            version = self._state.db.save_behavior_overlay_version({
                "id": "bov-" + uuid.uuid4().hex[:12],
                **self._behavior_overlay_scope_payload(scope),
                "version_number": self._behavior_overlay_next_version_number(scope),
                "parent_version_id": active_version_id,
                "text": proposed_text,
                "text_sha256": overlay_text_sha256(proposed_text),
                "author_agent_id": proposal.get("proposed_by_agent_id", ""),
                "author_kind": proposal.get("proposed_by_kind", ""),
                "rationale": proposal.get("rationale", ""),
                "approver_id": str(actor_id or ""),
                "approver_kind": str(actor_kind or ""),
                "source_proposal_id": proposal.get("id", ""),
                "created_at": now,
            })
            new_active_version_id = version["id"]
            action = "apply"
            self._state._emit_behavior_overlay_version(version)
        active = self._state.db.save_behavior_overlay_active({
            **self._behavior_overlay_scope_payload(scope),
            "active_version_id": new_active_version_id,
            "updated_at": now,
            "updated_by_kind": str(actor_kind or ""),
            "updated_by_id": str(actor_id or ""),
            "reason": str(note or proposal.get("rationale", "") or ""),
        })
        self._state.db.save_behavior_overlay_activation({
            "id": "boa-" + uuid.uuid4().hex[:12],
            **self._behavior_overlay_scope_payload(scope),
            "previous_version_id": active_version_id,
            "active_version_id": new_active_version_id,
            "proposal_id": proposal.get("id", ""),
            "actor_kind": str(actor_kind or ""),
            "actor_id": str(actor_id or ""),
            "action": action,
            "reason": str(note or proposal.get("rationale", "") or ""),
            "created_at": now,
        })
        saved = self._state.db.save_behavior_overlay_proposal({
            "id": proposal["id"],
            "status": "applied",
            "next_actor_kind": "",
            "resolved_by_kind": str(actor_kind or ""),
            "resolved_by_id": str(actor_id or ""),
            "resolved_at": now,
            "resolution_note": str(note or ""),
            "applied_version_id": new_active_version_id,
            "applied_at": now,
            "user_approved_at": now if actor_kind == "user" else proposal.get("user_approved_at"),
            "updated_at": now,
        })
        self._state._emit_behavior_overlay_active(active)
        self._state._emit_behavior_overlay_proposal(saved)
        self.resolve_behavior_overlay_user_task(
            str(saved.get("user_task_id", "") or ""),
            status="Approved",
            note=str(note or ""),
        )
        return saved

    def architect_approve_behavior_overlay_proposal(
            self,
            proposal_id: str,
            *,
            architect_id: str,
            expected_proposed_text_sha256: str = "",
            note: str = "") -> dict:
        proposal = self.load_behavior_overlay_proposal(proposal_id)
        if not proposal:
            raise ValueError("behavior overlay proposal not found")
        expected = str(expected_proposed_text_sha256 or "").strip()
        if expected and expected != str(proposal.get("proposed_text_sha256", "") or ""):
            raise ValueError("proposed text hash does not match")
        if proposal.get("status") == "applied":
            return proposal
        if proposal.get("status") == "rejected":
            raise ValueError("behavior overlay proposal has already been rejected")
        if str(proposal.get("next_actor_kind", "") or "") != "architect":
            raise ValueError("behavior overlay proposal is not awaiting architect approval")
        route = str(proposal.get("approval_route", "") or "")
        if route == "architect":
            return self.apply_behavior_overlay_proposal(
                proposal_id,
                actor_kind="architect",
                actor_id=architect_id,
                note=note,
            )
        if route != "architect_then_user":
            raise ValueError("behavior overlay proposal route is not architect-governed")
        now = time.time()
        saved = self._state.db.save_behavior_overlay_proposal({
            "id": proposal["id"],
            "status": "approved",
            "next_actor_kind": "user",
            "architect_approver_id": str(architect_id or ""),
            "architect_approved_at": now,
            "resolution_note": str(note or ""),
            "updated_at": now,
        })
        self._state._emit_behavior_overlay_proposal(saved)
        return saved

    def reject_behavior_overlay_proposal(
            self,
            proposal_id: str,
            *,
            actor_kind: str,
            actor_id: str = "",
            note: str = "") -> dict:
        proposal = self.load_behavior_overlay_proposal(proposal_id)
        if not proposal:
            raise ValueError("behavior overlay proposal not found")
        if proposal.get("status") == "rejected":
            return proposal
        if proposal.get("status") == "applied":
            raise ValueError("behavior overlay proposal has already been applied")
        if (
                str(actor_kind or "").strip() == "architect"
                and str(proposal.get("scope_kind", "") or "agent") == "role"
                and str(proposal.get("proposed_by_agent_id", "") or "") != str(actor_id or "").strip()):
            raise ValueError("architect may withdraw only its own role behavior overlay proposal")
        now = time.time()
        resolution_note = str(note or "")
        if (
                str(actor_kind or "").strip() == "architect"
                and str(proposal.get("scope_kind", "") or "agent") == "role"
                and not resolution_note):
            resolution_note = "withdrawn by author"
        saved = self._state.db.save_behavior_overlay_proposal({
            "id": proposal["id"],
            "status": "rejected",
            "next_actor_kind": "",
            "resolved_by_kind": str(actor_kind or ""),
            "resolved_by_id": str(actor_id or ""),
            "resolved_at": now,
            "resolution_note": resolution_note,
            "updated_at": now,
        })
        self._state._emit_behavior_overlay_proposal(saved)
        self.resolve_behavior_overlay_user_task(
            str(saved.get("user_task_id", "") or ""),
            status="Rejected",
            note=str(note or ""),
        )
        return saved

    def behavior_overlay_diff_payload(
            self,
            *,
            proposal_id: str = "",
            from_version_id: str = "",
            to_version_id: str = "",
            agent_id: str = "",
            scope_kind: str = "",
            scope_group: str = "",
            scope_key: str = "",
            group: str = "",
            role_kind: str = "") -> dict:
        target_agent_id = str(agent_id or "").strip()
        scope = None
        if scope_kind or scope_key or role_kind:
            scope = self._behavior_scope_from_args(
                agent_id=agent_id,
                scope_kind=scope_kind,
                scope_group=scope_group,
                scope_key=scope_key,
                group=group,
                role_kind=role_kind,
            )
        from_label = "from"
        to_label = "to"
        if proposal_id:
            proposal = self.load_behavior_overlay_proposal(proposal_id)
            if not proposal:
                raise ValueError("behavior overlay proposal not found")
            if (
                    target_agent_id
                    and str(proposal.get("agent_id", "") or "") != target_agent_id):
                raise ValueError("behavior overlay proposal not found")
            if scope and (
                    proposal.get("scope_kind") != scope.scope_kind
                    or proposal.get("scope_group") != scope.scope_group
                    or proposal.get("scope_key") != scope.scope_key):
                raise ValueError("behavior overlay proposal not found")
            base = self.load_behavior_overlay_version(
                proposal.get("base_version_id", "")
            ) or {}
            from_text = str(base.get("text", "") or "")
            to_text = str(proposal.get("proposed_text", "") or "")
            from_label = str(proposal.get("base_version_id", "") or "base")
            to_label = proposal_id
            return {
                "type": "behavior_overlay_diff",
                "proposal": proposal,
                "from_version": version_summary(base),
                "to_proposal": proposal_summary(proposal),
                "diff": behavior_overlay_diff(
                    from_text,
                    to_text,
                    from_label=from_label,
                    to_label=to_label,
                ),
            }
        if not from_version_id and target_agent_id:
            active = self.load_behavior_overlay_active(target_agent_id) or {}
            from_version_id = str(active.get("active_version_id", "") or "")
        if not from_version_id and scope:
            active = self._state.db.load_behavior_overlay_active(scope) if self._state.db else {}
            from_version_id = str((active or {}).get("active_version_id", "") or "")
        from_version = self.load_behavior_overlay_version(from_version_id) or {}
        to_version = self.load_behavior_overlay_version(to_version_id) or {}
        if not from_version or not to_version:
            raise ValueError("behavior overlay version not found")
        if target_agent_id:
            if (
                    str(from_version.get("agent_id", "") or "") != target_agent_id
                    or str(to_version.get("agent_id", "") or "") != target_agent_id):
                raise ValueError("behavior overlay version not found")
        if scope:
            for version in (from_version, to_version):
                if (
                        str(version.get("scope_kind", "") or "") != scope.scope_kind
                        or str(version.get("scope_group", "") or "") != scope.scope_group
                        or str(version.get("scope_key", "") or "") != scope.scope_key):
                    raise ValueError("behavior overlay version not found")
        return {
            "type": "behavior_overlay_diff",
            "from_version": version_summary(from_version),
            "to_version": version_summary(to_version),
            "diff": behavior_overlay_diff(
                str(from_version.get("text", "") or ""),
                str(to_version.get("text", "") or ""),
                from_label=from_version.get("id", "from"),
                to_label=to_version.get("id", "to"),
            ),
        }

    def create_behavior_overlay_user_task(
            self,
            proposal_id: str,
            *,
            note: str = "") -> str:
        """Create (or return existing) Backlog attention task for user route."""
        proposal = self.load_behavior_overlay_proposal(proposal_id)
        if not proposal:
            return ""
        existing_task_id = str(proposal.get("user_task_id", "") or "")
        if existing_task_id and existing_task_id in self._state.board_tasks:
            return existing_task_id
        scope_kind = str(proposal.get("scope_kind", "") or "agent")
        target = self._state.agents.get(str(proposal.get("agent_id", "") or ""))
        group = (
            str(proposal.get("scope_group", "") or "")
            if scope_kind == "role"
            else str(getattr(target, "group", "") or "") if target else ""
        )
        if not group or group not in self._state.groups:
            return ""
        title = "Dynamic Behavior overlay approval"
        if scope_kind == "role":
            target_label = (
                f"{proposal.get('target_kind', proposal.get('scope_key', ''))} "
                f"role overlay for group {group}"
            )
        else:
            target_label = (
                f"{getattr(target, 'name', '')} "
                f"({getattr(target, 'kind', '')}:{getattr(target, 'id', '')})"
                if target else proposal.get("agent_id", "")
            )
        description = "\n".join([
            "A governed Dynamic Behavior overlay proposal is awaiting user approval.",
            "",
            f"Proposal: {proposal_id}",
            f"Target: {target_label}",
            f"Route: {proposal.get('approval_route', '')}",
            f"Author: {proposal.get('proposed_by_kind', '')}:{proposal.get('proposed_by_agent_id', '')}",
            f"Rationale: {proposal.get('rationale', '')}",
            "",
            "Use `torque behavior diff --proposal "
            f"{proposal_id}` to inspect the text diff, then approve/reject "
            "with `torque behavior approve` or `torque behavior reject`.",
            str(note or "").strip(),
        ]).strip()
        task = self._state.board_add_task(
            task=title,
            group=group,
            lane="Backlog" if "Backlog" in self._state.board_lanes else "",
            description=description,
            labels=[
                "torque:human",
                "behavior-overlay-approval",
                f"proposal:{proposal_id}",
                f"scope:{scope_kind}",
            ] + (
                [
                    f"role:{proposal.get('target_kind', proposal.get('scope_key', ''))}",
                    f"group:{group}",
                ]
                if scope_kind == "role" else []
            ),
            created_by_architect_id=str(
                proposal.get("architect_approver_id", "")
                or (
                    proposal.get("proposed_by_agent_id", "")
                    if proposal.get("proposed_by_kind") == "architect"
                    else ""
                )
            ),
        )
        if not task:
            return ""
        saved = self._state.db.save_behavior_overlay_proposal({
            "id": proposal_id,
            "user_task_id": task.id,
            "updated_at": time.time(),
        })
        self._state._emit_behavior_overlay_proposal(saved)
        return task.id

    def resolve_behavior_overlay_user_task(
            self,
            task_id: str,
            *,
            status: str,
            note: str = "") -> None:
        task_id = self._state.resolve_task_alias(str(task_id or "").strip())
        if not task_id or task_id not in self._state.board_tasks:
            return
        task = self._state.board_tasks.get(task_id)
        labels = set(getattr(task, "labels", []) or [])
        if "behavior-overlay-approval" not in labels:
            return
        status_text = str(status or "Resolved").strip()
        message = f"Behavior overlay approval {status_text.lower()}."
        if note:
            message += f" Note: {note}"
        fields = {"status": status_text}
        if "Done" in self._state.board_lanes:
            fields["lane"] = "Done"
        fields["messages"] = list(getattr(task, "messages", []) or []) + [{
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "behavior_overlay",
            "message": message,
            "agent_name": "Torque",
        }]
        self._state.board_update_task(task_id, **fields)

    def cancel_behavior_overlay_proposals_for_agent(
            self,
            agent_id: str,
            *,
            reason: str,
            actor_kind: str = "system",
            actor_id: str = "") -> int:
        count = 0
        for proposal in self.list_behavior_overlay_proposals(agent_id=agent_id,
                                                             limit=500):
            if proposal.get("status") in {"rejected", "applied"}:
                continue
            self.reject_behavior_overlay_proposal(
                proposal["id"],
                actor_kind=actor_kind,
                actor_id=actor_id,
                note=reason,
            )
            count += 1
        return count

    def clear_behavior_overlay_active_for_agent(
            self,
            agent_id: str,
            *,
            reason: str = "agent deleted") -> bool:
        if not self._state.db:
            return False
        active = self.load_behavior_overlay_active(agent_id)
        if not active:
            return False
        self._state.db.delete_behavior_overlay_active(agent_id)
        self._state._emit_behavior_overlay_active(None, agent_id=agent_id)
        return True

    def cleanup_behavior_overlay_for_agent_delete(
            self,
            agent_id: str,
            *,
            reason: str = "agent deleted") -> dict:
        """Tombstone overlay lifecycle for a deleted target agent.

        Version and activation history remains immutable; the active pointer is
        cleared and pending proposals/user approval tasks are rejected/resolved.
        """
        cancelled = self.cancel_behavior_overlay_proposals_for_agent(
            agent_id,
            reason=reason,
            actor_kind="system",
        )
        active_cleared = self.clear_behavior_overlay_active_for_agent(
            agent_id,
            reason=reason,
        )
        return {
            "cancelled_proposals": cancelled,
            "active_cleared": active_cleared,
        }

    def cleanup_behavior_overlay_for_architect_delete(
            self,
            architect_id: str,
            *,
            hired_engineer_ids: list[str] | None = None,
            reason: str = "architect deleted") -> dict:
        cancelled = 0
        # Architect's own target overlay is no longer active.
        own = self.cleanup_behavior_overlay_for_agent_delete(
            architect_id,
            reason=reason,
        )
        cancelled += int(own.get("cancelled_proposals", 0) or 0)
        # Proposals authored/endorsed by the architect or targeting engineers
        # whose governor is being removed must not dangle.
        target_ids = set(str(x or "").strip() for x in (hired_engineer_ids or []))
        for proposal in self.list_behavior_overlay_proposals(limit=500):
            if proposal.get("status") in {"rejected", "applied"}:
                continue
            if (
                    proposal.get("agent_id") in target_ids
                    or proposal.get("proposed_by_agent_id") == architect_id
                    or proposal.get("architect_approver_id") == architect_id):
                self.reject_behavior_overlay_proposal(
                    proposal["id"],
                    actor_kind="system",
                    actor_id=architect_id,
                    note=reason,
                )
                cancelled += 1
        return {
            "cancelled_proposals": cancelled,
            "active_cleared": bool(own.get("active_cleared")),
        }
