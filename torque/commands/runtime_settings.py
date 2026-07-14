"""Group lifecycle and daemon-wide runtime settings commands."""

from __future__ import annotations

import logging
import os
from collections import deque
from dataclasses import dataclass
from typing import Any

from ..adapters import get_adapter
from .settings import (
    SETTINGS_MUTATION_COMMAND_NAMES,
    _SETTINGS_MUTATION_COMMAND_REGISTRY,
)


log = logging.getLogger(__name__)

RUNTIME_SETTINGS_COMMAND_NAMES = frozenset({
    "add_group", "update_global_settings", "update_ai_settings",
    "ai_index_start", "remove_group",
}) | SETTINGS_MUTATION_COMMAND_NAMES


@dataclass(slots=True)
class RuntimeSettingsCommandRuntime:
    apply_ai_settings_update_command: Any
    build_ai_settings_response: Any
    configure_event_ingest_client: Any
    persistent_prompt_filename: Any
    relay_settings_fingerprint: Any
    restart_cloud_connector: Any
    safe_remove_worktree: Any
    ai_index_service: Any
    ai_summary_service: Any
    bridge: Any
    db: Any
    event_bus: Any
    event_ingest_client: Any
    event_ingest_configured: Any
    panel_log: Any
    state: Any
    worktree_mgr: Any


async def handle_runtime_settings_command(
    data: dict, runtime: RuntimeSettingsCommandRuntime,
) -> dict | None:
    """Apply group/settings mutations and their live runtime side effects."""
    cmd = str(data.get("cmd", "") or "").strip()
    result = None
    _apply_ai_settings_update_command = runtime.apply_ai_settings_update_command
    _build_ai_settings_response = runtime.build_ai_settings_response
    _configure_event_ingest_client = runtime.configure_event_ingest_client
    _persistent_prompt_filename = runtime.persistent_prompt_filename
    _relay_settings_fingerprint = runtime.relay_settings_fingerprint
    _restart_cloud_connector = runtime.restart_cloud_connector
    _safe_remove_worktree = runtime.safe_remove_worktree
    ai_index_service = runtime.ai_index_service
    ai_summary_service = runtime.ai_summary_service
    bridge = runtime.bridge
    db = runtime.db
    event_bus = runtime.event_bus
    event_ingest_client = runtime.event_ingest_client
    event_ingest_configured = runtime.event_ingest_configured
    panel_log = runtime.panel_log
    state = runtime.state
    worktree_mgr = runtime.worktree_mgr

    if cmd == "add_group":
        group_name = data["group"]
        state.add_group(group_name)
        default_directory = (data.get("default_directory") or "").strip()
        if default_directory and group_name in state.groups:
            state.update_group_settings(
                group_name, default_directory=default_directory
            )

    elif cmd in SETTINGS_MUTATION_COMMAND_NAMES:
        settings_mutation = await _SETTINGS_MUTATION_COMMAND_REGISTRY.dispatch(
            cmd,
            data,
            state,
        )
        result = settings_mutation.value

    elif cmd == "update_global_settings":
        settings = data.get("settings", {})
        old_relay = _relay_settings_fingerprint()
        state.update_global_settings(**settings)
        # Apply-on-change: restart the cloud connector when any relay
        # field changed so the new config takes effect without a daemon
        # restart. Defensive / non-fatal.
        if _relay_settings_fingerprint() != old_relay:
            try:
                await _restart_cloud_connector()
            except Exception:
                log.exception(
                    "Cloud connector apply-on-change failed")
        # Propagate max_event_log to panel log
        new_max = state.global_settings.max_event_log
        if panel_log._max_size != new_max:
            panel_log._max_size = new_max
            panel_log._events = deque(
                panel_log._events, maxlen=new_max)
            if panel_log._db:
                panel_log._db.trim_panel_events(new_max)
        try:
            await _configure_event_ingest_client(event_ingest_client, state)
            event_ingest_configured[0] = True
        except Exception:
            event_ingest_configured[0] = False
            log.exception("Failed to reconfigure event ingest daemon")

    elif cmd == "update_ai_settings":
        result = _apply_ai_settings_update_command(
            state,
            db or state.db,
            data,
            ai_index_service=ai_index_service,
            ai_summary_service=ai_summary_service,
        )

    elif cmd == "ai_index_start":
        index_result = await ai_index_service.start(
            mode=str(data.get("mode", "incremental") or "incremental"),
            confirm=bool(data.get("confirm")),
            reason="manual",
        )
        if index_result.get("type") == "ai_index_job":
            index_result["settings"] = _build_ai_settings_response(
                state,
                db or state.db,
            ).get("settings", {})
        result = index_result

    elif cmd == "remove_group":
        removed = state.remove_group(data["group"])
        for c in removed:
            if c.session_id:
                await bridge.close_session(c.session_id)
            if c.agent_type and c.directory:
                adapter = get_adapter(c.agent_type)
                expanded_dir = os.path.expanduser(c.directory)
                if hasattr(adapter, "cleanup_agent_config"):
                    adapter.cleanup_agent_config(c, expanded_dir)
                if hasattr(adapter, "uninstall_hooks"):
                    adapter.uninstall_hooks(expanded_dir)
                if hasattr(adapter, "uninstall_mcp_config"):
                    adapter.uninstall_mcp_config(expanded_dir)
                adapter.uninstall_persistent_prompt(
                    expanded_dir,
                    _persistent_prompt_filename(c))
            event_bus.cleanup_cell(c.id)
            worktree_mgr.forget_refresh_state(c.id)
            await _safe_remove_worktree(c)

    return result
