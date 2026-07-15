"""SQLite persistence for Dynamic Behavior overlays and proposals."""

from __future__ import annotations

import json
import time

from ..behavior_overlay import (
    BehaviorOverlayScope,
    behavior_overlay_scope_id,
    coerce_behavior_overlay_scope,
    overlay_text_bytes,
    overlay_text_sha256,
)
from .common import (
    json_loads_default as _json_loads_default,
    snapshot_db_payload as _snapshot_db_payload,
)


def _decode_behavior_overlay_version_row(row, cols) -> dict:
    version = dict(zip(cols, row))
    version["scope_kind"] = str(version.get("scope_kind", "") or "agent")
    version["scope_group"] = str(version.get("scope_group", "") or "")
    version["scope_key"] = str(
        version.get("scope_key", "") or version.get("agent_id", "") or ""
    )
    version["scope_id"] = behavior_overlay_scope_id(
        scope_kind=version["scope_kind"],
        scope_group=version["scope_group"],
        scope_key=version["scope_key"],
        agent_id=str(version.get("agent_id", "") or ""),
    )
    version["version_number"] = int(version.get("version_number", 0) or 0)
    version["created_at"] = float(version.get("created_at", 0) or 0)
    version["metadata"] = _json_loads_default(
        version.pop("metadata_json", "{}"), {}
    )
    text = str(version.get("text", "") or "")
    version["text_sha256"] = str(
        version.get("text_sha256", "") or overlay_text_sha256(text)
    )
    version["text_bytes"] = overlay_text_bytes(text)
    return version


def _decode_behavior_overlay_active_row(row, cols) -> dict:
    active = dict(zip(cols, row))
    active["scope_kind"] = str(active.get("scope_kind", "") or "agent")
    active["scope_group"] = str(active.get("scope_group", "") or "")
    active["scope_key"] = str(
        active.get("scope_key", "") or active.get("agent_id", "") or ""
    )
    active["scope_id"] = behavior_overlay_scope_id(
        scope_kind=active["scope_kind"],
        scope_group=active["scope_group"],
        scope_key=active["scope_key"],
        agent_id=str(active.get("agent_id", "") or ""),
    )
    active["updated_at"] = float(active.get("updated_at", 0) or 0)
    return active


def _decode_behavior_overlay_proposal_row(row, cols) -> dict:
    proposal = dict(zip(cols, row))
    proposal["scope_kind"] = str(proposal.get("scope_kind", "") or "agent")
    proposal["scope_group"] = str(proposal.get("scope_group", "") or "")
    proposal["scope_key"] = str(
        proposal.get("scope_key", "") or proposal.get("agent_id", "") or ""
    )
    proposal["scope_id"] = behavior_overlay_scope_id(
        scope_kind=proposal["scope_kind"],
        scope_group=proposal["scope_group"],
        scope_key=proposal["scope_key"],
        agent_id=str(proposal.get("agent_id", "") or ""),
    )
    proposal["requires_user_approval"] = bool(
        proposal.get("requires_user_approval", 0)
    )
    for key in (
            "architect_approved_at", "user_approved_at", "resolved_at",
            "applied_at"):
        proposal[key] = float(proposal.get(key, 0) or 0)
    for key in ("created_at", "updated_at"):
        proposal[key] = float(proposal.get(key, 0) or 0)
    proposal["lint_warnings"] = _json_loads_default(
        proposal.pop("lint_warnings_json", "[]"), []
    )
    text = str(proposal.get("proposed_text", "") or "")
    proposal["proposed_text_sha256"] = str(
        proposal.get("proposed_text_sha256", "") or overlay_text_sha256(text)
    )
    proposal["proposed_text_bytes"] = overlay_text_bytes(text)
    return proposal


def _decode_behavior_overlay_activation_row(row, cols) -> dict:
    activation = dict(zip(cols, row))
    activation["scope_kind"] = str(activation.get("scope_kind", "") or "agent")
    activation["scope_group"] = str(activation.get("scope_group", "") or "")
    activation["scope_key"] = str(
        activation.get("scope_key", "") or activation.get("agent_id", "") or ""
    )
    activation["scope_id"] = behavior_overlay_scope_id(
        scope_kind=activation["scope_kind"],
        scope_group=activation["scope_group"],
        scope_key=activation["scope_key"],
        agent_id=str(activation.get("agent_id", "") or ""),
    )
    activation["created_at"] = float(activation.get("created_at", 0) or 0)
    return activation


class BehaviorOverlayPersistenceMixin:
    """TorqueDB API for Dynamic Behavior overlay persistence."""

    _BEHAVIOR_VERSION_COLS = (
        "id, scope_kind, scope_group, scope_key, agent_id, version_number, "
        "parent_version_id, text, text_sha256, author_agent_id, author_kind, "
        "rationale, approver_id, approver_kind, source_proposal_id, "
        "created_at, metadata_json"
    )
    _BEHAVIOR_PROPOSAL_COLS = (
        "id, scope_kind, scope_group, scope_key, agent_id, target_kind, "
        "proposal_type, base_version_id, target_version_id, proposed_text, "
        "proposed_text_sha256, proposed_by_agent_id, proposed_by_kind, "
        "rationale, status, approval_route, next_actor_kind, "
        "requires_user_approval, architect_approver_id, "
        "architect_approved_at, user_task_id, user_approved_at, "
        "lint_warnings_json, resolved_by_kind, resolved_by_id, resolved_at, "
        "resolution_note, applied_version_id, applied_at, idempotency_key, "
        "created_at, updated_at"
    )

    def _behavior_scope(self, scope=None, **kwargs):
        scope_obj = coerce_behavior_overlay_scope(scope, **kwargs)
        if scope_obj.scope_kind == "agent" and not scope_obj.scope_group:
            try:
                row = self._conn.execute(
                    "SELECT group_name FROM agents WHERE id=?",
                    (scope_obj.scope_key,),
                ).fetchone()
            except Exception:
                row = None
            group = str(row[0] if row else "" or "").strip()
            if group:
                return BehaviorOverlayScope.agent(scope_obj.scope_key, group=group)
        return scope_obj

    def load_behavior_overlay_version(self, version_id: str) -> dict | None:
        version_id = str(version_id or "").strip()
        if not version_id:
            return None
        cursor = self._conn.execute(
            f"SELECT {self._BEHAVIOR_VERSION_COLS} "
            "FROM behavior_overlay_versions WHERE id=?",
            (version_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _decode_behavior_overlay_version_row(
            row, [d[0] for d in cursor.description]
        )

    def load_behavior_overlay_active(self, scope=None, **kwargs) -> dict | None:
        try:
            scope_obj = self._behavior_scope(scope, **kwargs)
        except ValueError:
            return None
        cursor = self._conn.execute(
            "SELECT scope_kind, scope_group, scope_key, agent_id, "
            "active_version_id, updated_at, updated_by_kind, updated_by_id, "
            "reason FROM behavior_overlay_active "
            "WHERE scope_kind=? AND scope_group=? AND scope_key=?",
            (scope_obj.scope_kind, scope_obj.scope_group, scope_obj.scope_key),
        )
        row = cursor.fetchone()
        if (
                not row
                and scope_obj.scope_kind == "agent"
                and not scope_obj.scope_group):
            cursor = self._conn.execute(
                "SELECT scope_kind, scope_group, scope_key, agent_id, "
                "active_version_id, updated_at, updated_by_kind, updated_by_id, "
                "reason FROM behavior_overlay_active "
                "WHERE scope_kind='agent' AND scope_key=? "
                "ORDER BY updated_at DESC LIMIT 1",
                (scope_obj.scope_key,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return _decode_behavior_overlay_active_row(
            row, [d[0] for d in cursor.description]
        )

    def load_all_behavior_overlay_active(
            self, *, scope_kind: str = "") -> list[dict]:
        """Load active overlay pointers in one query for snapshot assembly."""
        scope_kind = str(scope_kind or "").strip()
        sql = (
            "SELECT scope_kind, scope_group, scope_key, agent_id, "
            "active_version_id, updated_at, updated_by_kind, updated_by_id, "
            "reason FROM behavior_overlay_active"
        )
        params: tuple = ()
        if scope_kind:
            sql += " WHERE scope_kind=?"
            params = (scope_kind,)
        sql += " ORDER BY updated_at DESC"
        cursor = self._conn.execute(sql, params)
        cols = [d[0] for d in cursor.description]
        return [
            _decode_behavior_overlay_active_row(row, cols)
            for row in cursor.fetchall()
        ]

    def load_behavior_overlay_active_version(
            self, scope=None, **kwargs) -> dict | None:
        active = self.load_behavior_overlay_active(scope, **kwargs)
        if not active:
            return None
        return self.load_behavior_overlay_version(
            active.get("active_version_id", "")
        )

    def list_behavior_overlay_versions(
            self, scope=None, *, limit: int = 50, **kwargs) -> list[dict]:
        try:
            scope_obj = self._behavior_scope(scope, **kwargs)
        except ValueError:
            return []
        try:
            limit_int = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            limit_int = 50
        if scope_obj.scope_kind == "agent" and not scope_obj.scope_group:
            cursor = self._conn.execute(
                f"SELECT {self._BEHAVIOR_VERSION_COLS} "
                "FROM behavior_overlay_versions "
                "WHERE scope_kind='agent' AND scope_key=? "
                "ORDER BY version_number DESC LIMIT ?",
                (scope_obj.scope_key, limit_int),
            )
        else:
            cursor = self._conn.execute(
                f"SELECT {self._BEHAVIOR_VERSION_COLS} "
                "FROM behavior_overlay_versions "
                "WHERE scope_kind=? AND scope_group=? AND scope_key=? "
                "ORDER BY version_number DESC LIMIT ?",
                (
                    scope_obj.scope_kind,
                    scope_obj.scope_group,
                    scope_obj.scope_key,
                    limit_int,
                ),
            )
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [_decode_behavior_overlay_version_row(row, cols) for row in rows]

    def next_behavior_overlay_version_number(self, scope=None, **kwargs) -> int:
        try:
            scope_obj = self._behavior_scope(scope, **kwargs)
        except ValueError:
            return 0
        row = self._conn.execute(
            "SELECT MAX(version_number) FROM behavior_overlay_versions "
            "WHERE scope_kind=? AND scope_group=? AND scope_key=?",
            (scope_obj.scope_kind, scope_obj.scope_group, scope_obj.scope_key),
        ).fetchone()
        current_value = row[0] if row else None
        current = -1 if current_value is None else int(current_value)
        return current + 1

    def save_behavior_overlay_version(self, row_dict: dict) -> dict:
        row = dict(row_dict or {})
        version_id = str(row.get("id", "") or "").strip()
        if not version_id:
            raise ValueError("behavior overlay version id is required")
        scope_obj = self._behavior_scope(row)
        text = str(row.get("text", "") or "")
        metadata = row.get("metadata", row.get("metadata_json", {})) or {}
        if not isinstance(metadata, dict):
            metadata = {}
        self._conn.execute(
            "INSERT OR REPLACE INTO behavior_overlay_versions "
            f"({self._BEHAVIOR_VERSION_COLS}) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                version_id,
                scope_obj.scope_kind,
                scope_obj.scope_group,
                scope_obj.scope_key,
                scope_obj.agent_id,
                int(row.get("version_number", 0) or 0),
                str(row.get("parent_version_id", "") or ""),
                text,
                str(row.get("text_sha256", "") or overlay_text_sha256(text)),
                str(row.get("author_agent_id", "") or ""),
                str(row.get("author_kind", "") or ""),
                str(row.get("rationale", "") or ""),
                str(row.get("approver_id", "") or ""),
                str(row.get("approver_kind", "") or ""),
                str(row.get("source_proposal_id", "") or ""),
                float(row.get("created_at", time.time()) or time.time()),
                json.dumps(metadata, separators=(",", ":")),
            ),
        )
        self._conn.commit()
        saved = self.load_behavior_overlay_version(version_id)
        if not saved:
            raise RuntimeError(
                f"failed to load saved behavior overlay version {version_id}"
            )
        return saved

    async def save_behavior_overlay_version_async(self, row_dict: dict) -> dict:
        return await self._enqueue_async_write(
            "behavior_overlay_versions",
            "save_behavior_overlay_version",
            _snapshot_db_payload(row_dict or {}),
        )

    def save_behavior_overlay_active(self, row_dict: dict) -> dict:
        row = dict(row_dict or {})
        scope_obj = self._behavior_scope(row)
        active_version_id = str(row.get("active_version_id", "") or "").strip()
        if not active_version_id:
            raise ValueError("behavior overlay active_version_id is required")
        self._conn.execute(
            "INSERT OR REPLACE INTO behavior_overlay_active "
            "(scope_kind, scope_group, scope_key, agent_id, active_version_id, "
            "updated_at, updated_by_kind, updated_by_id, reason) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                scope_obj.scope_kind,
                scope_obj.scope_group,
                scope_obj.scope_key,
                scope_obj.agent_id,
                active_version_id,
                float(row.get("updated_at", time.time()) or time.time()),
                str(row.get("updated_by_kind", "") or ""),
                str(row.get("updated_by_id", "") or ""),
                str(row.get("reason", "") or ""),
            ),
        )
        self._conn.commit()
        saved = self.load_behavior_overlay_active(scope_obj)
        if not saved:
            raise RuntimeError(
                f"failed to load saved behavior overlay active row {scope_obj.scope_id}"
            )
        return saved

    def delete_behavior_overlay_active(self, scope=None, **kwargs) -> None:
        try:
            scope_obj = self._behavior_scope(scope, **kwargs)
        except ValueError:
            return
        self._conn.execute(
            (
                "DELETE FROM behavior_overlay_active "
                "WHERE scope_kind=? AND scope_group=? AND scope_key=?"
                if scope_obj.scope_group or scope_obj.scope_kind != "agent"
                else "DELETE FROM behavior_overlay_active "
                     "WHERE scope_kind=? AND scope_key=?"
            ),
            (
                (scope_obj.scope_kind, scope_obj.scope_group, scope_obj.scope_key)
                if scope_obj.scope_group or scope_obj.scope_kind != "agent"
                else (scope_obj.scope_kind, scope_obj.scope_key)
            ),
        )
        self._conn.commit()

    def save_behavior_overlay_activation(self, row_dict: dict) -> dict:
        row = dict(row_dict or {})
        activation_id = str(row.get("id", "") or "").strip()
        scope_obj = self._behavior_scope(row)
        active_version_id = str(row.get("active_version_id", "") or "").strip()
        if not activation_id:
            raise ValueError("behavior overlay activation id is required")
        if not active_version_id:
            raise ValueError(
                "behavior overlay activation active_version_id is required"
            )
        self._conn.execute(
            "INSERT OR REPLACE INTO behavior_overlay_activations "
            "(id, scope_kind, scope_group, scope_key, agent_id, "
            "previous_version_id, active_version_id, proposal_id, actor_kind, "
            "actor_id, action, reason, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                activation_id,
                scope_obj.scope_kind,
                scope_obj.scope_group,
                scope_obj.scope_key,
                scope_obj.agent_id,
                str(row.get("previous_version_id", "") or ""),
                active_version_id,
                str(row.get("proposal_id", "") or ""),
                str(row.get("actor_kind", "") or ""),
                str(row.get("actor_id", "") or ""),
                str(row.get("action", "") or ""),
                str(row.get("reason", "") or ""),
                float(row.get("created_at", time.time()) or time.time()),
            ),
        )
        self._conn.commit()
        cursor = self._conn.execute(
            "SELECT id, scope_kind, scope_group, scope_key, agent_id, "
            "previous_version_id, active_version_id, proposal_id, actor_kind, "
            "actor_id, action, reason, created_at "
            "FROM behavior_overlay_activations WHERE id=?",
            (activation_id,),
        )
        saved = cursor.fetchone()
        if not saved:
            raise RuntimeError(
                f"failed to load saved behavior overlay activation {activation_id}"
            )
        return _decode_behavior_overlay_activation_row(
            saved, [d[0] for d in cursor.description]
        )

    def load_behavior_overlay_proposal(self, proposal_id: str) -> dict | None:
        proposal_id = str(proposal_id or "").strip()
        if not proposal_id:
            return None
        cursor = self._conn.execute(
            f"SELECT {self._BEHAVIOR_PROPOSAL_COLS} "
            "FROM behavior_overlay_proposals WHERE id=?",
            (proposal_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _decode_behavior_overlay_proposal_row(
            row, [d[0] for d in cursor.description]
        )

    def load_behavior_overlay_proposal_by_idempotency(
            self, proposed_by_agent_id: str,
            idempotency_key: str,
            scope=None,
            **kwargs) -> dict | None:
        proposed_by_agent_id = str(proposed_by_agent_id or "").strip()
        idempotency_key = str(idempotency_key or "").strip()
        if not proposed_by_agent_id or not idempotency_key:
            return None
        params = [proposed_by_agent_id, idempotency_key]
        query = (
            f"SELECT {self._BEHAVIOR_PROPOSAL_COLS} "
            "FROM behavior_overlay_proposals "
            "WHERE proposed_by_agent_id=? AND idempotency_key=?"
        )
        if scope is not None or kwargs:
            scope_obj = self._behavior_scope(scope, **kwargs)
            query += " AND scope_kind=? AND scope_group=? AND scope_key=?"
            params.extend([
                scope_obj.scope_kind,
                scope_obj.scope_group,
                scope_obj.scope_key,
            ])
        query += " ORDER BY created_at DESC LIMIT 1"
        cursor = self._conn.execute(query, tuple(params))
        row = cursor.fetchone()
        if not row:
            return None
        return _decode_behavior_overlay_proposal_row(
            row, [d[0] for d in cursor.description]
        )

    def save_behavior_overlay_proposal(self, row_dict: dict) -> dict:
        row = dict(row_dict or {})
        proposal_id = str(row.get("id", "") or "").strip()
        if not proposal_id:
            raise ValueError("behavior overlay proposal id is required")
        existing = self.load_behavior_overlay_proposal(proposal_id) or {}
        scope_obj = self._behavior_scope({**existing, **row})
        now = time.time()
        created_at = float(
            row.get("created_at", existing.get("created_at", now)) or now
        )
        updated_at = float(row.get("updated_at", now) or now)
        proposed_text = str(
            row.get("proposed_text", existing.get("proposed_text", "")) or ""
        )
        lint_warnings = row.get(
            "lint_warnings",
            row.get("lint_warnings_json", existing.get("lint_warnings", [])),
        )
        if not isinstance(lint_warnings, list):
            lint_warnings = []
        status = str(
            row.get("status", existing.get("status", "proposed")) or "proposed"
        )
        if status not in {"proposed", "approved", "rejected", "applied"}:
            raise ValueError(
                "status must be one of: proposed, approved, rejected, applied"
            )
        proposal_type = str(
            row.get("proposal_type", existing.get("proposal_type", "set_text"))
            or "set_text"
        )
        if proposal_type not in {"set_text", "rollback"}:
            raise ValueError("proposal_type must be set_text or rollback")
        approval_route = str(
            row.get("approval_route", existing.get("approval_route", "")) or ""
        )
        if approval_route not in {"architect", "user", "architect_then_user"}:
            raise ValueError(
                "approval_route must be architect, user, or architect_then_user"
            )
        self._conn.execute(
            "INSERT OR REPLACE INTO behavior_overlay_proposals "
            f"({self._BEHAVIOR_PROPOSAL_COLS}) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                proposal_id,
                scope_obj.scope_kind,
                scope_obj.scope_group,
                scope_obj.scope_key,
                scope_obj.agent_id,
                str(row.get("target_kind", existing.get("target_kind", "")) or ""),
                proposal_type,
                str(row.get("base_version_id", existing.get("base_version_id", "")) or ""),
                str(row.get("target_version_id", existing.get("target_version_id", "")) or ""),
                proposed_text,
                str(
                    row.get(
                        "proposed_text_sha256",
                        existing.get(
                            "proposed_text_sha256",
                            overlay_text_sha256(proposed_text),
                        ),
                    ) or overlay_text_sha256(proposed_text)
                ),
                str(row.get("proposed_by_agent_id", existing.get("proposed_by_agent_id", "")) or ""),
                str(row.get("proposed_by_kind", existing.get("proposed_by_kind", "")) or ""),
                str(row.get("rationale", existing.get("rationale", "")) or ""),
                status,
                approval_route,
                str(row.get("next_actor_kind", existing.get("next_actor_kind", "")) or ""),
                int(bool(row.get(
                    "requires_user_approval",
                    existing.get("requires_user_approval", False),
                ))),
                str(row.get("architect_approver_id", existing.get("architect_approver_id", "")) or ""),
                row.get("architect_approved_at", existing.get("architect_approved_at", None)),
                str(row.get("user_task_id", existing.get("user_task_id", "")) or ""),
                row.get("user_approved_at", existing.get("user_approved_at", None)),
                json.dumps(lint_warnings, separators=(",", ":")),
                str(row.get("resolved_by_kind", existing.get("resolved_by_kind", "")) or ""),
                str(row.get("resolved_by_id", existing.get("resolved_by_id", "")) or ""),
                row.get("resolved_at", existing.get("resolved_at", None)),
                str(row.get("resolution_note", existing.get("resolution_note", "")) or ""),
                str(row.get("applied_version_id", existing.get("applied_version_id", "")) or ""),
                row.get("applied_at", existing.get("applied_at", None)),
                str(row.get("idempotency_key", existing.get("idempotency_key", "")) or ""),
                created_at,
                updated_at,
            ),
        )
        self._conn.commit()
        saved = self.load_behavior_overlay_proposal(proposal_id)
        if not saved:
            raise RuntimeError(
                f"failed to load saved behavior overlay proposal {proposal_id}"
            )
        return saved

    async def save_behavior_overlay_proposal_async(self, row_dict: dict) -> dict:
        return await self._enqueue_async_write(
            "behavior_overlay_proposals",
            "save_behavior_overlay_proposal",
            _snapshot_db_payload(row_dict or {}),
        )

    def list_behavior_overlay_proposals(
            self, *,
            status_filter: str = "",
            agent_id: str = "",
            scope=None,
            scope_kind: str = "",
            scope_group: str = "",
            scope_key: str = "",
            next_actor_kind: str = "",
            proposed_by_agent_id: str = "",
            limit: int = 100) -> list[dict]:
        query = f"SELECT {self._BEHAVIOR_PROPOSAL_COLS} FROM behavior_overlay_proposals WHERE 1=1"
        params: list = []
        for column, value in (
                ("status", status_filter),
                ("next_actor_kind", next_actor_kind),
                ("proposed_by_agent_id", proposed_by_agent_id)):
            value = str(value or "").strip()
            if value:
                query += f" AND {column}=?"
                params.append(value)
        if scope is not None or scope_kind or scope_key:
            scope_obj = self._behavior_scope(
                scope,
                scope_kind=scope_kind,
                scope_group=scope_group,
                scope_key=scope_key,
                agent_id=agent_id,
            )
            query += " AND scope_kind=? AND scope_group=? AND scope_key=?"
            params.extend([
                scope_obj.scope_kind,
                scope_obj.scope_group,
                scope_obj.scope_key,
            ])
        elif str(agent_id or "").strip():
            # Compatibility filter for old per-agent callers.
            query += " AND agent_id=?"
            params.append(str(agent_id or "").strip())
        try:
            limit_int = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            limit_int = 100
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit_int)
        cursor = self._conn.execute(query, tuple(params))
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [_decode_behavior_overlay_proposal_row(row, cols) for row in rows]
