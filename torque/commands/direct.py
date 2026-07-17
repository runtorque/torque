"""Direct-response configuration, diagnostics, history, and control commands."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from .. import cloud_hooks
from ..adapters import get_providers_async
from ..deploy_state import architect_deploy_state_payload
from ..events import get_cell_event_stream
from ..mission_control import build_mission_control_summary
from ..server_supervisor import (
    build_supervisor_restart_payload,
    build_supervisor_sessions_payload,
    build_supervisor_terminate_payload,
)
from ..worktree_streams import (
    compute_worktree_streams,
    prefill_branch_exists_for_state,
)
from .agent_classes import (
    AGENT_CLASS_COMMAND_NAMES,
    _AGENT_CLASS_COMMAND_REGISTRY,
)
from .behavior_overlays import (
    BEHAVIOR_OVERLAY_READ_COMMAND_NAMES,
    _BEHAVIOR_OVERLAY_READ_COMMAND_REGISTRY,
)
from .catalog import CATALOG_COMMAND_NAMES, _CATALOG_COMMAND_REGISTRY
from .planning import PLANNING_COMMAND_NAMES, _PLANNING_COMMAND_REGISTRY
from .settings import SETTINGS_READ_COMMAND_NAMES, _SETTINGS_READ_COMMAND_REGISTRY


log = logging.getLogger(__name__)

DIRECT_COMMAND_NAMES = frozenset({
    "get_config", "preview_system_prompt", "test_relay_connection",
    "generate_relay_device_link", "generate_daemon_credential", "doctor",
    "help_list", "help_show", "help_search", "help_query",
    "get_metrics_history", "report_frontend_render",
    "get_system_health_metrics", "get_deploy_state", "get_mission_control",
    "supervisor_sessions_list", "supervisor_session_terminate",
    "supervisor_restart", "get_events", "task_detail",
    "get_agent_message_history", "decisions_snapshot",
    "pending_hires_snapshot", "archived_tasks",
    "engineer_journal_snapshot", "architect_journal_read", "mcp_calls",
    "architect_mcp_calls", "engineer_mcp_calls", "architect_task_list",
    "get_cell_events", "get_agent_history", "get_agent_history_detail",
}).union(
    SETTINGS_READ_COMMAND_NAMES,
    AGENT_CLASS_COMMAND_NAMES,
    PLANNING_COMMAND_NAMES,
    BEHAVIOR_OVERLAY_READ_COMMAND_NAMES,
    CATALOG_COMMAND_NAMES,
)


@dataclass(slots=True)
class DirectCommandRuntime:
    DATA_DIR: Any
    agent_overrides_from_role_settings: Any
    architect_deploy_state_payload: Any
    build_ai_settings_response: Any
    build_group_system_prompt_preview: Any
    current_board_tasks_for_agent: Any
    dispatch_architect_ui_tool: Any
    enrich_history_record: Any
    handle_agent_message_history_command: Any
    handle_architect_journal_read_command: Any
    handle_archived_tasks_command: Any
    handle_decisions_snapshot_command: Any
    handle_doctor_command: Any
    handle_engineer_journal_snapshot_command: Any
    handle_mcp_calls_command: Any
    handle_pending_hires_snapshot_command: Any
    handle_task_detail_command: Any
    history_records_with_live_agents: Any
    live_history_record: Any
    compute_worktree_streams: Any
    prefill_branch_exists_for_state: Any
    preview_architect_settings_for_prompt: Any
    preview_engineer_settings_for_prompt: Any
    preview_group_settings_for_prompt: Any
    relay_settings_fingerprint: Any
    resolve_base_dir: Any
    restart_cloud_connector: Any
    runtime_payload: Any
    sort_history_records: Any
    action_mgr: Any
    bridge: Any
    catalog_command_runtime: Any
    cloud_connector_runtime_holder: Any
    db: Any
    event_ingest_client: Any
    event_log: Any
    panel_log: Any
    specialization_mgr: Any
    state: Any
    template_mgr: Any


async def handle_direct_command(
    data: dict, runtime: DirectCommandRuntime,
) -> dict | None:
    """Execute a command whose response bypasses the mutation broadcast tail."""
    cmd = str(data.get("cmd", "") or "").strip()
    DATA_DIR = runtime.DATA_DIR
    _agent_overrides_from_role_settings = runtime.agent_overrides_from_role_settings
    architect_deploy_state_payload = runtime.architect_deploy_state_payload
    _build_ai_settings_response = runtime.build_ai_settings_response
    _build_group_system_prompt_preview = runtime.build_group_system_prompt_preview
    _current_board_tasks_for_agent = runtime.current_board_tasks_for_agent
    _dispatch_architect_ui_tool = runtime.dispatch_architect_ui_tool
    _enrich_history_record = runtime.enrich_history_record
    _handle_agent_message_history_command = runtime.handle_agent_message_history_command
    _handle_architect_journal_read_command = runtime.handle_architect_journal_read_command
    _handle_archived_tasks_command = runtime.handle_archived_tasks_command
    _handle_decisions_snapshot_command = runtime.handle_decisions_snapshot_command
    _handle_doctor_command = runtime.handle_doctor_command
    _handle_engineer_journal_snapshot_command = runtime.handle_engineer_journal_snapshot_command
    _handle_mcp_calls_command = runtime.handle_mcp_calls_command
    _handle_pending_hires_snapshot_command = runtime.handle_pending_hires_snapshot_command
    _handle_task_detail_command = runtime.handle_task_detail_command
    _history_records_with_live_agents = runtime.history_records_with_live_agents
    _live_history_record = runtime.live_history_record
    compute_worktree_streams = runtime.compute_worktree_streams
    prefill_branch_exists_for_state = runtime.prefill_branch_exists_for_state
    _preview_architect_settings_for_prompt = runtime.preview_architect_settings_for_prompt
    _preview_engineer_settings_for_prompt = runtime.preview_engineer_settings_for_prompt
    _preview_group_settings_for_prompt = runtime.preview_group_settings_for_prompt
    _relay_settings_fingerprint = runtime.relay_settings_fingerprint
    _resolve_base_dir = runtime.resolve_base_dir
    _restart_cloud_connector = runtime.restart_cloud_connector
    _runtime_payload = runtime.runtime_payload
    _sort_history_records = runtime.sort_history_records
    action_mgr = runtime.action_mgr
    bridge = runtime.bridge
    catalog_command_runtime = runtime.catalog_command_runtime
    cloud_connector_runtime_holder = runtime.cloud_connector_runtime_holder
    db = runtime.db
    event_ingest_client = runtime.event_ingest_client
    event_log = runtime.event_log
    panel_log = runtime.panel_log
    specialization_mgr = runtime.specialization_mgr
    state = runtime.state
    template_mgr = runtime.template_mgr

    # get_config: respond directly, no state mutation
    if cmd == "get_config":
        profile_names = await bridge.list_profiles()
        ctx = await bridge.get_launch_context()
        current_path = ctx.current_path
        current_profile = ctx.current_profile

        group = data.get("group", "")
        group_cells = []
        for aid in state.groups.get(group, []):
            c = state.agents.get(aid)
            if c and c.session_id and c.current_path:
                group_cells.append({
                    "id": c.id, "name": c.name,
                    "current_path": c.current_path,
                })

        gs = state.get_group_settings(group)
        resolved_defaults = template_mgr.resolve_agent_config(
            "", gs, {}, base_dir=current_path or await _resolve_base_dir(group))
        return {
            "type": "config",
            "profiles": profile_names,
            "current_path": current_path,
            "current_profile": current_profile,
            "group_cells": group_cells,
            "group_settings": asdict(gs),
            "architect_settings": asdict(
                state.get_architect_settings(group)
            ),
            "resolved_agent_defaults": resolved_defaults,
            "providers": await get_providers_async(),
            "templates": template_mgr.list_templates(current_path
                                                      or await _resolve_base_dir(group)),
            "playbooks": state.list_playbooks(group=group,
                                               status="published",
                                               limit=200),
            "runtime": _runtime_payload(bridge=bridge, state=state),
        }

    settings_read_dispatch = await _SETTINGS_READ_COMMAND_REGISTRY.dispatch(
        cmd,
        data,
        state,
        bridge=bridge,
        resolve_base_dir=_resolve_base_dir,
        template_mgr=template_mgr,
        action_mgr=action_mgr,
        providers=get_providers_async,
        runtime_payload=_runtime_payload,
        resolve_relay_config=lambda settings: cloud_hooks.resolve_relay_config(
            settings,
            data_dir=str(DATA_DIR),
        ),
        build_ai_settings_response=_build_ai_settings_response,
        db=db,
    )
    if settings_read_dispatch.handled:
        return settings_read_dispatch.value

    if cmd == "preview_system_prompt":
        kind = str(data.get("kind", "") or "").strip().lower()
        if kind not in {"engineer", "architect"}:
            return {
                "type": "error",
                "message": "kind must be 'engineer' or 'architect'",
            }
        group = str(data.get("group", "") or "").strip()
        settings_payload = dict(data.get("settings", {}) or {})
        group_settings_payload = dict(
            data.get("group_settings", {}) or {}
        )
        group_settings = _preview_group_settings_for_prompt(
            state, group, group_settings_payload)
        if kind == "engineer":
            role_settings = _preview_engineer_settings_for_prompt(
                state, group, settings_payload)
        else:
            role_settings = _preview_architect_settings_for_prompt(
                state, group, settings_payload)
        base_dir = await _resolve_base_dir(group)
        resolved = template_mgr.resolve_agent_config(
            "",
            group_settings,
            _agent_overrides_from_role_settings(kind, role_settings),
            base_dir=base_dir,
            apply_default_template=False,
        )
        specializations_preamble = ""
        specialization_names = []
        if kind == "engineer":
            raw_specializations = getattr(
                group_settings, "default_engineer_specializations", []
            )
            if isinstance(raw_specializations, list):
                specialization_names = [
                    str(item or "").strip()
                    for item in raw_specializations
                    if str(item or "").strip()
                ]
            if specialization_names:
                try:
                    specializations_preamble = (
                        specialization_mgr.render_engineer_preamble(
                            specialization_names,
                            base_dir=base_dir,
                        )
                    )
                except Exception:
                    log.exception(
                        "failed to render system prompt preview "
                        "specializations for group=%s", group)
        prompt = _build_group_system_prompt_preview(
            state,
            group,
            kind,
            settings_payload=settings_payload,
            group_settings_payload=group_settings_payload,
            action_system_prompt=resolved.get("system_prompt", ""),
            specializations_preamble=specializations_preamble,
        )
        return {
            "type": "system_prompt_preview",
            "request_id": str(data.get("request_id", "") or ""),
            "kind": kind,
            "group": group,
            "prompt": prompt,
            "metadata": {
                "provider": resolved.get("provider", ""),
                "template": resolved.get("template", ""),
                "specializations": specialization_names,
            },
        }

    # test_relay_connection: daemon-side connectivity probe for the Settings
    # "Relay" section "Test connection" button. Bounded + defensive; rides the
    # connector's certifi context so a CA-missing failure is distinguished
    # from "unreachable". Returns a structured {status, message, detail}.
    if cmd == "test_relay_connection":
        result = await cloud_hooks.probe_relay_connection(
            state.global_settings, data_dir=str(DATA_DIR)
        )
        return {"type": "relay_test_result", **result}

    # generate_relay_device_link: b2 daemon-mediated mint of a single-use
    # relay device link (replaces the manual `wrangler d1` OTC seed). The mint
    # rides the daemon's authenticated relay WS; the relay derives the owner
    # from the authed attach and enforces replay/fencing/rate-limit.
    #
    # LOCAL-CONFIRMATION gesture is REQUIRED (security invariant): the caller
    # MUST pass confirm=true, so a remote reach to this command cannot
    # silently mint a bearer credential. On confirm, the raw code + establish
    # URL are returned to the LOCAL caller exactly ONCE and are NEVER
    # persisted or logged by the daemon. Frontend (QR/display-once) is owned
    # by a separate task and is intentionally out of scope here.
    if cmd == "generate_relay_device_link":
        if not bool(data.get("confirm")):
            return {
                "type": "relay_device_link",
                "ok": False,
                "status": "confirmation_required",
                "message": (
                    "Generating a device link mints a single-use, "
                    "short-lived credential. Confirm to proceed."
                ),
            }
        result = await cloud_hooks.mint_relay_device_link(
            cloud_connector_runtime_holder[0],
            label=str(data.get("label", "") or ""),
        )
        return {"type": "relay_device_link", **result}

    # generate_daemon_credential: client half of the :676 in-app daemon
    # credential provisioning flow. The daemon generates the ES256 keypair
    # locally, posts the PUBLIC JWK plus the pasted one-time pairing token to
    # /v1/pair, and persists ONLY the resulting credential_id + private key
    # FILE PATH into Global Settings. The raw private key is never stored in
    # SQLite and never returned to the browser.
    if cmd == "generate_daemon_credential":
        result = await cloud_hooks.generate_daemon_credential(
            state.global_settings,
            pairing_token=str(data.get("pairing_token", "") or ""),
            data_dir=str(DATA_DIR),
        )
        if result.get("ok"):
            credential_id = str(result.get("credential_id", "") or "").strip()
            private_key_path = str(result.get("private_key_path", "") or "").strip()
            if credential_id and private_key_path:
                old_relay = _relay_settings_fingerprint()
                try:
                    await state.update_global_settings_durable(
                        relay_credential_id=credential_id,
                        relay_private_key_path=private_key_path,
                    )
                except Exception:
                    log.exception(
                        "Relay accepted daemon credential but Torque could not save "
                        "it to Settings; credential_id=%s private_key_path=%s",
                        credential_id,
                        private_key_path,
                    )
                    result = {
                        "ok": False,
                        "error": "settings_write_failed",
                        "recoverable": True,
                        "credential_id": credential_id,
                        "private_key_path": private_key_path,
                        "message": (
                            "Relay accepted the credential but Torque couldn't save "
                            "it to Settings. Recover by setting Settings → Relay "
                            "credential ID and private key path to the values shown, "
                            "or ask the relay admin to revoke the credential, then retry."
                        ),
                    }
                    response = {"type": "daemon_credential", **result}
                    try:
                        response["relay_config"] = cloud_hooks.resolve_relay_config(
                            state.global_settings, data_dir=str(DATA_DIR)
                        )
                    except Exception:
                        log.exception(
                            "Failed to resolve relay config after daemon credential generation"
                        )
                    return response
                if _relay_settings_fingerprint() != old_relay:
                    try:
                        await _restart_cloud_connector()
                    except Exception:
                        log.exception(
                            "Cloud connector apply-on-daemon-credential failed"
                        )
            else:
                result = {
                    "ok": False,
                    "error": "invalid_response",
                    "message": (
                        "Relay pairing did not return a credential_id and "
                        "private key path to store."
                    ),
                }
        response = {"type": "daemon_credential", **result}
        try:
            response["relay_config"] = cloud_hooks.resolve_relay_config(
                state.global_settings, data_dir=str(DATA_DIR)
            )
        except Exception:
            log.exception("Failed to resolve relay config after daemon credential generation")
        return response

    if cmd == "doctor":
        return await _handle_doctor_command(db, bridge)

    if cmd in {"help_list", "help_show", "help_search", "help_query"}:
        from ..help_docs import handle_help_command

        return handle_help_command(data)

    agent_class_dispatch = await _AGENT_CLASS_COMMAND_REGISTRY.dispatch(
        cmd,
        data,
        state,
        db,
        _resolve_base_dir,
    )
    if agent_class_dispatch.handled:
        return agent_class_dispatch.value

    if cmd == "get_metrics_history":
        try:
            await state.flush_db_writes()
            if panel_log and hasattr(panel_log, "flush"):
                await panel_log.flush()
            return state.metrics_history(
                window=data.get("window", "24h"),
                group=data.get("group", ""),
            )
        except ValueError as exc:
            return {"type": "error", "message": str(exc)}

    if cmd == "report_frontend_render":
        meter = getattr(state, "metrics_collector", None)
        if meter is not None and meter.enabled:
            meter.record_frontend_render(data)
        return {"type": "ok", "schema_version": 1}

    if cmd == "get_system_health_metrics":
        try:
            await state.flush_db_writes()
            if panel_log and hasattr(panel_log, "flush"):
                await panel_log.flush()
            return state.system_health_metrics(
                window=data.get("window", "24h"),
                group=data.get("group", ""),
            )
        except ValueError as exc:
            return {"type": "error", "message": str(exc)}

    if cmd == "get_deploy_state":
        group = str(data.get("group", "") or "")
        payload = architect_deploy_state_payload(state, group)
        payload["type"] = "deploy_state"
        payload["group"] = group
        payload.setdefault("error", "")
        pending = payload.get("pending_deploy")
        if not isinstance(pending, dict):
            payload["pending_deploy"] = {"count": 0, "torque_task_ids": []}
        else:
            pending.setdefault("count", 0)
            pending.setdefault("torque_task_ids", [])
        if "daemon_uptime_seconds" not in payload:
            payload["daemon_uptime_seconds"] = None
        return payload

    if cmd == "get_mission_control":
        if "group" not in data:
            return {
                "type": "error",
                "message": "group required",
            }
        group = str(data.get("group", "") or "")
        try:
            limit_per_section = int(
                data.get("limit_per_section", 20) or 20
            )
        except (TypeError, ValueError):
            limit_per_section = 20
        include_recent_completed_raw = data.get(
            "include_recent_completed", True)
        if isinstance(include_recent_completed_raw, str):
            include_recent_completed = (
                include_recent_completed_raw.strip().lower()
                not in {"0", "false", "no", "off"}
            )
        else:
            include_recent_completed = bool(include_recent_completed_raw)
        try:
            recent_completed_seconds = int(
                data.get("recent_completed_seconds", 604800) or 604800
            )
        except (TypeError, ValueError):
            recent_completed_seconds = 604800

        deploy_payload = None
        source_errors = {}
        try:
            deploy_payload = architect_deploy_state_payload(state, group)
        except Exception as exc:
            log.exception("failed to compute Mission Control deploy state")
            source_errors["deploy_state"] = str(exc) or exc.__class__.__name__
            deploy_payload = {"error": source_errors["deploy_state"]}

        try:
            await prefill_branch_exists_for_state(state, group=group)
        except Exception as exc:
            log.exception("failed to prefill Mission Control branch cache")
            source_errors["branch_cache"] = str(exc) or exc.__class__.__name__

        try:
            streams = compute_worktree_streams(
                state,
                group=group,
                visibility_limit=max(1, min(limit_per_section, 100)),
                include_orphaned=False,
            )
        except Exception as exc:
            log.exception("failed to compute Mission Control streams")
            source_errors["streams"] = str(exc) or exc.__class__.__name__
            streams = []

        return build_mission_control_summary(
            state,
            group=group,
            limit_per_section=limit_per_section,
            include_recent_completed=include_recent_completed,
            recent_completed_seconds=recent_completed_seconds,
            deploy_state=deploy_payload,
            streams=streams,
            source_errors=source_errors,
        )

    if cmd == "supervisor_sessions_list":
        return await build_supervisor_sessions_payload(
            bridge, state, _runtime_payload)

    if cmd == "supervisor_session_terminate":
        return await build_supervisor_terminate_payload(
            bridge, state, _runtime_payload,
            str(data.get("session_id") or ""),
        )

    if cmd == "supervisor_restart":
        from .. import pty_supervisor as _pty_supervisor_mod
        try:
            timeout = float(
                data.get(
                    "timeout",
                    _pty_supervisor_mod.DEFAULT_RESTART_TIMEOUT_SECONDS,
                )
                or _pty_supervisor_mod.DEFAULT_RESTART_TIMEOUT_SECONDS
            )
        except (TypeError, ValueError):
            timeout = _pty_supervisor_mod.DEFAULT_RESTART_TIMEOUT_SECONDS
        return await build_supervisor_restart_payload(
            bridge,
            state,
            _runtime_payload,
            timeout=timeout,
            data_dir=DATA_DIR,
            ensure_running=_pty_supervisor_mod.ensure_running,
        )

    # get_events: paginated event log query
    if cmd == "get_events":
        before_id = int(data.get("before_id", 0))
        limit = min(int(data.get("limit", 50)), 200)
        events = panel_log.get_page(limit=limit, before_id=before_id)
        return {"type": "events_page", "events": events}

    if cmd == "task_detail":
        return _handle_task_detail_command(data, state)

    planning_result = await _PLANNING_COMMAND_REGISTRY.dispatch(
        cmd,
        data,
        state,
    )
    if planning_result.handled:
        return planning_result.value

    if cmd == "get_agent_message_history":
        return _handle_agent_message_history_command(data, state)

    if cmd == "decisions_snapshot":
        return _handle_decisions_snapshot_command(data, state)

    if cmd == "pending_hires_snapshot":
        return _handle_pending_hires_snapshot_command(data, state)

    behavior_overlay_read_result = (
        await _BEHAVIOR_OVERLAY_READ_COMMAND_REGISTRY.dispatch(
            cmd,
            data,
            state,
        )
    )
    if behavior_overlay_read_result.handled:
        return behavior_overlay_read_result.value

    if cmd == "archived_tasks":
        return _handle_archived_tasks_command(data, state)

    if cmd == "engineer_journal_snapshot":
        return await _handle_engineer_journal_snapshot_command(data, state)

    if cmd == "architect_journal_read":
        return _handle_architect_journal_read_command(data, state)

    if cmd == "mcp_calls":
        return await _handle_mcp_calls_command(
            data,
            state,
            event_ingest_client,
            scope="trusted",
        )

    if cmd == "architect_mcp_calls":
        return await _handle_mcp_calls_command(
            data,
            state,
            event_ingest_client,
            scope="architect",
        )

    if cmd == "engineer_mcp_calls":
        return await _handle_mcp_calls_command(
            data,
            state,
            event_ingest_client,
            scope="engineer",
        )

    if cmd == "architect_task_list":
        return await _dispatch_architect_ui_tool(cmd, data, state)

    if cmd == "get_cell_events":
        cell_id = str(data.get("cell_id", "") or "")
        limit = min(int(data.get("limit", 200)), 200)
        cell = state.agents.get(cell_id)
        if not cell:
            return {"type": "error", "message": "Cell not found"}
        events = get_cell_event_stream(
            cell,
            event_log,
            panel_log=panel_log,
            db=db,
            limit=limit,
        )
        return {
            "type": "cell_events",
            "cell_id": cell_id,
            "events": events,
        }

    # Agent history queries
    if cmd == "get_agent_history":
        status_filter = data.get("status", "")
        limit = min(int(data.get("limit", 50)), 200)
        offset = int(data.get("offset", 0))
        records = db.load_agent_history(
            status_filter=status_filter,
            limit=min(max(limit + offset, limit), 200),
            offset=0)
        records = _history_records_with_live_agents(records)
        if status_filter:
            records = [
                r for r in records if r.get("status") == status_filter
            ]
        records = _sort_history_records(records)[offset:offset + limit]
        return {"type": "agent_history_list",
                "records": records}

    if cmd == "get_agent_history_detail":
        agent_id = data.get("agent_id", "")
        if not agent_id:
            return {"type": "error",
                    "message": "agent_id required"}
        record = db.load_agent_history_detail(agent_id)
        live_cell = state.agents.get(agent_id)
        if live_cell and live_cell.cell_type == "agent":
            record = _live_history_record(live_cell, record)
        if not record:
            return {"type": "error",
                    "message": "Agent not found in history"}
        record = _enrich_history_record(record)
        tasks = db.load_agent_tasks(agent_id)
        if not tasks:
            tasks = _current_board_tasks_for_agent(agent_id)
        messages = db.load_agent_messages(
            agent_id,
            limit=int(data.get("message_limit", 100)))
        return {"type": "agent_history_detail",
                "record": record,
                "tasks": tasks,
                "messages": messages}

    catalog_dispatch = await _CATALOG_COMMAND_REGISTRY.dispatch(
        cmd,
        data,
        catalog_command_runtime,
    )
    if catalog_dispatch.handled:
        return catalog_dispatch.value

    return {"type": "error", "message": f"Unhandled direct command: {cmd}"}
