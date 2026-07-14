"""Agent Class authoring, assignment, and audit commands."""

from __future__ import annotations

import os

from ..agent_classes import (
    AGENT_CLASS_SCHEMA_VERSION,
    agent_class_authoring_contract,
    agent_class_definition_by_id,
    archive_custom_agent_class,
    delete_custom_agent_class,
    enriched_agent_class_preview,
    load_agent_classes,
    save_custom_agent_class,
    validate_agent_class_draft,
)
from ..db import TorqueDB
from ..dispatch_registry import AsyncHandlerRegistry
from ..state import MatrixState


AGENT_CLASS_COMMAND_NAMES = frozenset({
    "agent_class_list",
    "agent_class_validate",
    "agent_class_draft_validate",
    "agent_class_create",
    "agent_class_save",
    "agent_class_update",
    "agent_class_archive",
    "agent_class_disable",
    "agent_class_delete",
    "agent_class_preview",
    "agent_class_assign",
    "agent_class_clear",
    "agent_class_status",
    "agent_class_audit",
})


def _agent_class_authoring_payload_from_command(data: dict) -> dict:
    for key in ("agent_class", "definition"):
        value = data.get(key)
        if isinstance(value, dict):
            return dict(value)
    return {
        key: value
        for key, value in dict(data or {}).items()
        if key not in {"cmd", "request_id", "_client_id", "base_dir", "mode"}
    }


async def _handle_agent_class_command(
    data: dict,
    state: MatrixState,
    db: TorqueDB | None,
    resolve_base_dir,
) -> dict | None:
    """Handle trusted browser/server Agent Class commands."""

    cmd = str(data.get("cmd", "") or "").strip()
    if cmd == "agent_class_list":
        base_dir = str(data.get("base_dir", "") or os.getcwd())
        classes, issues = load_agent_classes(base_dir=base_dir)
        authoring_contract = agent_class_authoring_contract()
        return {
            "type": "agent_classes",
            "schema_version": AGENT_CLASS_SCHEMA_VERSION,
            "classes": [
                enriched_agent_class_preview(definition, base_dir=base_dir)
                for definition in classes
            ],
            "issues": [issue.as_dict() for issue in issues],
            "authoring_contract": authoring_contract,
            "capability_catalog": authoring_contract["capability_catalog"],
            "storage": {
                "kind": "project_yaml",
                "config_glob": ".torque/agent_classes/*.yaml",
                "mutates_running_sessions": False,
            },
        }

    if cmd in {"agent_class_validate", "agent_class_draft_validate"}:
        base_dir = str(data.get("base_dir", "") or os.getcwd())
        payload = _agent_class_authoring_payload_from_command(data)
        result = validate_agent_class_draft(payload, base_dir=base_dir)
        result["type"] = "agent_class_validation"
        result["schema_version"] = AGENT_CLASS_SCHEMA_VERSION
        result["request_id"] = str(data.get("request_id", "") or "")
        return result

    if cmd in {"agent_class_create", "agent_class_save", "agent_class_update"}:
        base_dir = str(data.get("base_dir", "") or os.getcwd())
        payload = _agent_class_authoring_payload_from_command(data)
        mode = {
            "agent_class_create": "create",
            "agent_class_update": "update",
        }.get(cmd, str(data.get("mode", "save") or "save"))
        result = save_custom_agent_class(payload, base_dir=base_dir, mode=mode)
        result["type"] = "agent_class_save"
        result["schema_version"] = AGENT_CLASS_SCHEMA_VERSION
        result["request_id"] = str(data.get("request_id", "") or "")
        if result.get("ok"):
            classes, issues = load_agent_classes(base_dir=base_dir)
            result["classes"] = [
                enriched_agent_class_preview(definition, base_dir=base_dir)
                for definition in classes
            ]
            result["registry_issues"] = [issue.as_dict() for issue in issues]
        return result

    if cmd in {"agent_class_archive", "agent_class_disable"}:
        base_dir = str(data.get("base_dir", "") or os.getcwd())
        class_id = str(
            data.get("class_id", data.get("agent_class_id", "")) or ""
        ).strip()
        result = archive_custom_agent_class(class_id, base_dir=base_dir)
        result["schema_version"] = AGENT_CLASS_SCHEMA_VERSION
        result["request_id"] = str(data.get("request_id", "") or "")
        if result.get("ok"):
            classes, issues = load_agent_classes(base_dir=base_dir)
            result["classes"] = [
                enriched_agent_class_preview(definition, base_dir=base_dir)
                for definition in classes
            ]
            result["registry_issues"] = [issue.as_dict() for issue in issues]
        return result

    if cmd == "agent_class_delete":
        base_dir = str(data.get("base_dir", "") or os.getcwd())
        class_id = str(
            data.get("class_id", data.get("agent_class_id", "")) or ""
        ).strip()
        result = delete_custom_agent_class(class_id, base_dir=base_dir)
        result["schema_version"] = AGENT_CLASS_SCHEMA_VERSION
        result["request_id"] = str(data.get("request_id", "") or "")
        if result.get("ok"):
            classes, issues = load_agent_classes(base_dir=base_dir)
            result["classes"] = [
                enriched_agent_class_preview(definition, base_dir=base_dir)
                for definition in classes
            ]
            result["registry_issues"] = [issue.as_dict() for issue in issues]
        return result

    if cmd == "agent_class_preview":
        class_id = str(
            data.get("class_id", data.get("agent_class_id", "")) or ""
        ).strip()
        base_dir = str(data.get("base_dir", "") or os.getcwd())
        definition = agent_class_definition_by_id(
            class_id,
            base_dir=base_dir,
            include_archived=True,
        )
        if not definition:
            return {"type": "error", "message": f"Unknown Agent Class: {class_id}"}
        return {
            "type": "agent_class_preview",
            "schema_version": AGENT_CLASS_SCHEMA_VERSION,
            "agent_class": enriched_agent_class_preview(
                definition,
                base_dir=base_dir,
            ),
        }

    if cmd in {"agent_class_assign", "agent_class_clear"}:
        try:
            agent_id = str(data.get("agent_id", data.get("id", "")) or "").strip()
            cell = state.agents.get(agent_id)
            base_dir = str(data.get("base_dir", "") or "")
            if not base_dir and cell:
                base_dir = (
                    cell.worktree_repo_root
                    or cell.directory
                    or await resolve_base_dir(cell.group)
                )
            class_id = "" if cmd == "agent_class_clear" else str(
                data.get("class_id", data.get("agent_class_id", "")) or ""
            )
            status = state.assign_agent_class(
                agent_id,
                class_id,
                actor_kind="user",
                actor_id=str(data.get("actor_id", "") or ""),
                actor_label=str(
                    data.get("actor_label", "trusted-user") or "trusted-user"
                ),
                base_dir=base_dir,
            )
            return {"type": "agent_class_assignment", "status": status}
        except PermissionError as exc:
            return {
                "type": "error",
                "message": str(exc),
                "code": "trusted_user_required",
            }
        except ValueError as exc:
            return {"type": "error", "message": str(exc)}

    if cmd == "agent_class_status":
        agent_id = str(data.get("agent_id", data.get("id", "")) or "").strip()
        cell = state.agents.get(agent_id)
        if not cell:
            return {"type": "error", "message": "Agent not found"}
        base_dir = str(data.get("base_dir", "") or "")
        if not base_dir:
            base_dir = (
                cell.worktree_repo_root
                or cell.directory
                or await resolve_base_dir(cell.group)
            )
        return {
            "type": "agent_class_status",
            "status": state.agent_class_status_for_cell(cell, base_dir=base_dir),
        }

    if cmd == "agent_class_audit":
        if not db:
            return {"type": "agent_class_audit", "events": []}
        return {
            "type": "agent_class_audit",
            "events": db.list_agent_class_audit(
                agent_id=str(data.get("agent_id", "") or ""),
                limit=int(data.get("limit", 50) or 50),
            ),
        }

    return None


_AGENT_CLASS_COMMAND_REGISTRY = AsyncHandlerRegistry()
_AGENT_CLASS_COMMAND_REGISTRY.register_many(
    AGENT_CLASS_COMMAND_NAMES,
    _handle_agent_class_command,
    label="agent_classes",
)
