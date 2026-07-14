"""Group, Architect, global, and AI settings commands."""

from __future__ import annotations

from dataclasses import asdict

from ..dispatch_registry import AsyncHandlerRegistry
from ..state import ArchitectSettings, MatrixState


SETTINGS_READ_COMMAND_NAMES = frozenset({
    "get_group_settings",
    "get_architect_settings",
    "get_global_settings",
    "get_ai_settings",
})
SETTINGS_MUTATION_COMMAND_NAMES = frozenset({
    "update_group_settings",
    "update_architect_settings",
})


async def _handle_settings_read_command(
    data: dict,
    state: MatrixState,
    *,
    bridge,
    resolve_base_dir,
    template_mgr,
    action_mgr,
    providers,
    runtime_payload,
    resolve_relay_config,
    build_ai_settings_response,
    db,
) -> dict | None:
    cmd = str(data.get("cmd", "") or "").strip()
    if cmd == "get_group_settings":
        group = data.get("group", "")
        group_settings = state.get_group_settings(group)
        profile_names = await bridge.list_profiles()
        base_dir = await resolve_base_dir(group)
        return {
            "type": "group_settings",
            "group": group,
            "settings": asdict(group_settings),
            "engineer_settings": asdict(state.get_engineer_settings(group)),
            "architect_settings": asdict(state.get_architect_settings(group)),
            "resolved_agent_defaults": template_mgr.resolve_agent_config(
                "", group_settings, {}, base_dir=base_dir
            ),
            "profiles": profile_names,
            "providers": providers(),
            "templates": template_mgr.list_templates(base_dir),
            "actions": action_mgr.list_actions(base_dir),
            "playbooks": state.list_playbooks(
                group=group,
                status="published",
                limit=200,
            ),
            "runtime": runtime_payload(bridge=bridge, state=state),
        }

    if cmd == "get_architect_settings":
        group = data.get("group", "")
        return {
            "type": "architect_settings",
            "group": group,
            "settings": asdict(state.get_architect_settings(group)),
        }

    if cmd == "get_global_settings":
        return {
            "type": "global_settings",
            "settings": asdict(state.global_settings),
            "keybinding_defaults": {},
            "relay_config": resolve_relay_config(state.global_settings),
        }

    if cmd == "get_ai_settings":
        return build_ai_settings_response(state, db or state.db)

    return None


def _handle_settings_mutation_command(
    data: dict,
    state: MatrixState,
) -> dict | None:
    cmd = str(data.get("cmd", "") or "").strip()
    if cmd == "update_group_settings":
        state.update_group_settings(
            data["group"],
            **dict(data.get("settings", {}) or {}),
        )
        return None

    if cmd == "update_architect_settings":
        group = data.get("group", "")
        settings = dict(data.get("settings", {}) or {})
        valid = set(ArchitectSettings.__dataclass_fields__)
        for key, value in data.items():
            if key in valid and key != "group":
                settings[key] = value
        state.update_architect_settings(group, **settings)
        return {
            "type": "ok",
            "settings": asdict(state.get_architect_settings(group)),
        }

    return None


_SETTINGS_READ_COMMAND_REGISTRY = AsyncHandlerRegistry()
_SETTINGS_READ_COMMAND_REGISTRY.register_many(
    SETTINGS_READ_COMMAND_NAMES,
    _handle_settings_read_command,
    label="settings.read",
)

_SETTINGS_MUTATION_COMMAND_REGISTRY = AsyncHandlerRegistry()
_SETTINGS_MUTATION_COMMAND_REGISTRY.register_many(
    SETTINGS_MUTATION_COMMAND_NAMES,
    _handle_settings_mutation_command,
    label="settings.mutation",
)
