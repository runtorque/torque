"""HTTP, WebSocket, upload, profile, and event route factories.

The factories preserve request-handler closure semantics while keeping aiohttp
transport code out of the server composition root.
"""

from __future__ import annotations

import aiohttp
import contextlib
import json
import mimetypes
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from . import profiling
from .attachment_uploads import AttachmentUploadError, save_message_attachment_stream
from .config import log
from .event_ingest_db import redact_event_for_mcp_call_log
from .events import build_event_ingest_envelope
from .mcp import dispatch_mcp_rpc_body
from .mcp_retry import api_request_hash, is_api_write_command, replay_failed_writes


@dataclass(frozen=True, slots=True)
class EventRoutes:
    handle_events: Any
    panel_event: Any
    replay_failed_write: Any
    run_failed_write_replay: Any
    observe_direct_mcp_call: Any


@dataclass(frozen=True, slots=True)
class HttpRoutes:
    handle_index: Any
    handle_ws: Any
    handle_terminal_ws: Any
    handle_api_cmd: Any
    handle_profile_get: Any
    handle_profile_reset: Any
    handle_profile_synthetic_agents: Any
    handle_upload: Any
    handle_upload_cleanup: Any
    handle_attachment_upload: Any
    handle_logs: Any
    handle_ui_state: Any
    handle_serve_attachment: Any
    cleanup_orphan_attachments: Any


def build_event_routes(
    *,
    db,
    event_ingest_client,
    ensure_event_ingest_configured,
    notifier,
    perceived_empty_detector,
    state,
    panel_log,
    handle_command,
    torque_ai_mcp_report_tool_names,
    mcp_call_rows_for_ui,
    record_mcp_call_observation,
    replay_api_failed_write_payload,
    replay_internal_failed_write_payload,
) -> EventRoutes:
    _ensure_event_ingest_configured = ensure_event_ingest_configured
    _TORQUE_AI_MCP_REPORT_TOOL_NAMES = torque_ai_mcp_report_tool_names
    _mcp_call_rows_for_ui = mcp_call_rows_for_ui
    _record_mcp_call_observation = record_mcp_call_observation
    async def handle_events(request):
            """Receive events from agent hooks (Claude Code HTTP hooks, etc.)."""
            request_started = time.perf_counter()
            profiling.recorder().incr("events_endpoint_received")
            try:
                raw = await request.json()
            except Exception:
                profiling.recorder().incr("events_dropped_invalid_json")
                if hasattr(db, "record_mcp_health_event_safe"):
                    db.record_mcp_health_event_safe(
                        surface="events",
                        event="drop",
                        error="invalid JSON",
                    )
                profiling.recorder().observe_ms(
                    "event_endpoint_ms", time.perf_counter() - request_started)
                return web.json_response({}, status=400)

            headers = {
                "X-Torque-Cell-Id": request.headers.get("X-Torque-Cell-Id", ""),
            }
            # TORQUE:238 cutover: workers now report exclusively via
            # `mcp__torque__torque_*` MCP tools (no Bash CLI rewrite bridge).
            # Claude Code already emits PostToolUse hooks with the real
            # mcp__ tool name, so persistence + on-demand fetch +
            # capture clause all see the right shape directly.
            envelope = build_event_ingest_envelope(raw, headers=headers)
            idempotency_key = None
            explicit_event_id = raw.get("event_id")
            if not explicit_event_id and isinstance(raw.get("data"), dict):
                explicit_event_id = raw["data"].get("event_id")
            if explicit_event_id:
                idempotency_key = (
                    f"events:{headers['X-Torque-Cell-Id']}:{explicit_event_id}"
                )

            try:
                await _ensure_event_ingest_configured()
                response = await event_ingest_client.append(
                    envelope,
                    idempotency_key=idempotency_key,
                )
            except Exception as exc:
                log.exception("Failed to durably enqueue agent event")
                profiling.recorder().incr("events_dropped_ingest_unavailable")
                if hasattr(db, "record_mcp_health_event_safe"):
                    db.record_mcp_health_event_safe(
                        surface="events",
                        event="drop",
                        error=str(exc) or type(exc).__name__,
                    )
                profiling.recorder().observe_ms(
                    "event_endpoint_ms", time.perf_counter() - request_started)
                return web.json_response(
                    {"ok": False, "error": str(exc) or "event ingest unavailable"},
                    status=503,
                )
            if response.get("type") == "error":
                profiling.recorder().incr("events_dropped_ingest_error")
                if hasattr(db, "record_mcp_health_event_safe"):
                    db.record_mcp_health_event_safe(
                        surface="events",
                        event="drop",
                        error=str(response.get("message", "ingest error")),
                    )
                profiling.recorder().observe_ms(
                    "event_endpoint_ms", time.perf_counter() - request_started)
                return web.json_response(
                    {"ok": False, "error": response.get("message", "ingest error")},
                    status=500,
                )
            if response.get("duplicate") and hasattr(db, "record_mcp_health_event_safe"):
                db.record_mcp_health_event_safe(
                    surface="events",
                    event="dedupe",
                )
            if not response.get("duplicate"):
                try:
                    cell_id = headers.get("X-Torque-Cell-Id", "")
                    cell = state.agents.get(cell_id) if cell_id else None
                    episode = perceived_empty_detector.ingest_envelope(
                        envelope,
                        cursor=int(response.get("cursor") or 0),
                        cell=cell,
                        threshold_n=(
                            state.global_settings
                            .perceived_empty_probe_threshold
                        ),
                        window_seconds=(
                            state.global_settings
                            .perceived_empty_window_seconds
                        ),
                    )
                    if episode is not None:
                        episode_record = episode.to_db_record()
                        episode_id = 0
                        if hasattr(db, "record_perceived_empty_episode_safe"):
                            episode_id = db.record_perceived_empty_episode_safe(
                                **episode_record
                            )
                        elif hasattr(db, "record_perceived_empty_episode"):
                            episode_id = db.record_perceived_empty_episode(
                                **episode_record
                            )
                        message = (
                            "Perceived-empty tool-result episode detected "
                            f"({episode.confidence}): {episode.trigger_reason}. "
                            "Tool results were present in Torque evidence; "
                            "operator review needed."
                        )
                        state.flag_perceived_empty_episode(
                            episode.cell_id,
                            detail=message,
                        )
                        payload = {
                            **episode_record,
                            "id": episode_id,
                            "tool_calls": episode.tool_calls,
                        }
                        state._emit(
                            "perceived_empty_episode",
                            group=episode.group,
                            cell_id=episode.cell_id,
                            episode=payload,
                        )
                        event_cell = cell or state.agents.get(episode.cell_id)
                        _panel_event(
                            "perceived_empty_episode",
                            episode.cell_id,
                            episode.agent_name
                            or getattr(event_cell, "name", "")
                            or episode.cell_id,
                            episode.group
                            or getattr(event_cell, "group", ""),
                            message,
                            task_id=getattr(event_cell, "current_task_id", ""),
                        )
                        if notifier:
                            notifier.on_health_alert(episode.cell_id, message)
                        state.recompute_task_health()
                        if hasattr(db, "record_mcp_health_event_safe"):
                            db.record_mcp_health_event_safe(
                                surface="events",
                                event="perceived_empty_episode",
                                tool_name=episode.trigger_reason[:100],
                                error=episode.cell_id,
                            )
                except Exception:
                    log.exception("Failed to process perceived-empty detector")
            try:
                raw_tool = str(raw.get("tool_name") or raw.get("name") or "")
                raw_hook = str(raw.get("hook_event_name") or raw.get("type") or "")
                # TORQUE:238: when this PostToolUse came from a worker calling
                # one of the `mcp__torque__torque_*` reporting tools, the live
                # `mcp_call_append` delta was already emitted by the
                # ai_report handler on the same broadcast as the report's
                # event_append + agent_upsert (see `_append_mcp` wrapper).
                # Suppress the duplicate emission here so the frontend sees
                # ONE delta-bundle per call (rAF-coalesced), not two
                # ~30-100ms apart. The persistent ingest row is still
                # written above, so on-demand `cmd=mcp_calls` fetches still
                # resolve the row by cursor — same shape as engineer/
                # architect MCP calls, except those don't go through
                # ai_report so their mcp_call_append fires from this clause
                # alone.
                already_emitted_via_ai_report = (
                    raw_tool in _TORQUE_AI_MCP_REPORT_TOOL_NAMES
                )
                if (
                    raw_tool.startswith("mcp__")
                    and raw_hook == "PostToolUse"
                    and not bool(response.get("duplicate"))
                    and not already_emitted_via_ai_report
                ):
                    redacted_envelope = redact_event_for_mcp_call_log(
                        envelope,
                        args_capture=state.global_settings.mcp_call_log_args_capture,
                        full_capture_tools=(
                            state.global_settings.mcp_call_log_full_capture_tools
                        ),
                    )
                    record = {
                        "cursor": int(response.get("cursor") or 0),
                        "idempotency_key": idempotency_key or "",
                        "event": redacted_envelope,
                        "appended_at": time.time(),
                    }
                    rows = _mcp_call_rows_for_ui(state, [record])
                    if rows:
                        state._emit(
                            "mcp_call_append",
                            group=rows[0].get("group", ""),
                            call=rows[0],
                        )
            except Exception:
                log.exception("Failed to emit MCP call live delta")
            profiling.recorder().incr("events_enqueued")

            profiling.recorder().observe_ms(
                "event_endpoint_ms", time.perf_counter() - request_started)

            # Return 200 only after the ingest daemon has committed the event.
            return web.json_response({})

    def _panel_event(kind, cell_id, agent_name, group, message,
                         task_id=""):
            """Append a panel event and queue a delta broadcast."""
            pe = panel_log.append(
                kind=kind, cell_id=cell_id, agent_name=agent_name,
                group=group, message=message, task_id=task_id)
            state._emit("event_append", **pe)

    async def _replay_failed_write(write: dict):
            endpoint = str(write.get("endpoint", "") or "")
            payload = dict(write.get("payload", {}) or {})
            if endpoint == "/internal/cmd":
                return await replay_internal_failed_write_payload(
                    db,
                    payload,
                    handle_command,
                )
            if endpoint == "/mcp":
                response, _status = await dispatch_mcp_rpc_body(
                    payload,
                    cell_id=str(write.get("caller_id", "") or ""),
                    handle_command=handle_command,
                    state=state,
                )
                if response.get("error"):
                    log.info(
                        "Queued MCP write replay reached tool surface with error: %s",
                        response.get("error"),
                    )
                return response
            if endpoint == "/api/cmd":
                return await replay_api_failed_write_payload(
                    db,
                    payload,
                    handle_command,
                )
            raise ValueError(f"Unsupported failed-write endpoint: {endpoint}")

    async def _run_failed_write_replay() -> None:
            # Run off the startup critical path: a queued write whose replay blocks
            # (e.g. one that triggers an auto-dispatch that hangs) must never hold
            # up the HTTP/WS bind below, or the daemon never becomes reachable and
            # the launcher times out. Replay still happens, just concurrently.
            try:
                replay_summary = await replay_failed_writes(db, _replay_failed_write)
                if replay_summary.get("attempted"):
                    log.info("Failed-write replay summary: %s", replay_summary)
            except Exception:
                log.exception("Failed-write replay on startup errored")

    async def _observe_direct_mcp_call(observation):
            await _record_mcp_call_observation(
                state,
                event_ingest_client,
                observation,
                ensure_configured=_ensure_event_ingest_configured,
            )

    return EventRoutes(
        handle_events=handle_events,
        panel_event=_panel_event,
        replay_failed_write=_replay_failed_write,
        run_failed_write_replay=_run_failed_write_replay,
        observe_direct_mcp_call=_observe_direct_mcp_call,
    )


def build_http_routes(
    *,
    state,
    handle_command,
    state_payload,
    supervisor_banner_state,
    bridge,
    terminal_clients,
    daemon_stop_state,
    db,
    attachments_dir,
    data_dir,
    log_max_lines,
    api_worker_context_guard,
    daemon_stop_rejection_payload,
    hot_json_response,
    log_path_for_target,
    payload_wants_compact_snapshot,
    register_ready_ui_ws_client,
    request_wants_compact_snapshot,
    send_ui_ws_json,
    tail_log_entries,
    ui_client_id_from_request,
) -> HttpRoutes:
    _state_payload = state_payload
    ATTACHMENTS_DIR = attachments_dir
    DATA_DIR = data_dir
    _LOG_MAX_LINES = log_max_lines
    _api_worker_context_guard = api_worker_context_guard
    _daemon_stop_rejection_payload = daemon_stop_rejection_payload
    _hot_json_response = hot_json_response
    _log_path_for_target = log_path_for_target
    _payload_wants_compact_snapshot = payload_wants_compact_snapshot
    _register_ready_ui_ws_client = register_ready_ui_ws_client
    _request_wants_compact_snapshot = request_wants_compact_snapshot
    _send_ui_ws_json = send_ui_ws_json
    _tail_log_entries = tail_log_entries
    _ui_client_id_from_request = ui_client_id_from_request
    async def handle_index(_request):
            from .config import WEBVIEW_FILE  # re-read after init_paths
            return web.FileResponse(WEBVIEW_FILE)

    async def handle_ws(request):
            # heartbeat: aiohttp sends a WS ping every 30s and closes the
            # connection if no pong is returned, so the daemon detects and prunes
            # dead/half-open UI clients instead of holding zombie sockets open.
            ws = web.WebSocketResponse(heartbeat=30)
            await ws.prepare(request)
            compact_snapshot = _request_wants_compact_snapshot(request)
            ui_client_id = _ui_client_id_from_request(request)

            async def ws_state_payload():
                payload = await _state_payload(compact=compact_snapshot)
                return state.overlay_client_focus_state(payload, ui_client_id)

            if not await _register_ready_ui_ws_client(
                    state, ws, ws_state_payload, client_id=ui_client_id):
                return ws
            # Replay the current supervisor banner (if any) to the new client.
            banner = supervisor_banner_state.get("banner")
            if banner is not None:
                with contextlib.suppress(Exception):
                    await ws.send_str(json.dumps({
                        "type": "system_banner",
                        "banner": banner,
                    }))
            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                            if ui_client_id:
                                data["_client_id"] = ui_client_id
                            if _payload_wants_compact_snapshot(data):
                                compact_snapshot = True
                            if data.get("type") == "connect":
                                sent = await _register_ready_ui_ws_client(
                                    state, ws, ws_state_payload,
                                    client_id=ui_client_id)
                                if not sent:
                                    break
                                continue
                            if data.get("cmd") == "resync":
                                sent = await _register_ready_ui_ws_client(
                                    state, ws, ws_state_payload,
                                    client_id=ui_client_id)
                                if not sent:
                                    break
                                continue

                            result = await handle_command(data)
                            if result:
                                if result.get("type") == "state":
                                    sent = await _register_ready_ui_ws_client(
                                        state, ws, ws_state_payload)
                                else:
                                    sent = await _send_ui_ws_json(ws, result)
                                if not sent:
                                    break
                        except json.JSONDecodeError:
                            log.warning("Received malformed JSON from webview")
                        except Exception:
                            log.exception("Error handling WS command")
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        break
            finally:
                async with state._ws_clients_lock:
                    state._discard_ws_clients_locked({ws})
            return ws

    async def handle_terminal_ws(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            cell_id = request.match_info.get("cell_id", "")
            ui_client_id = _ui_client_id_from_request(request)
            terminal_clients.setdefault(cell_id, set()).add(ws)
            try:
                # Initial sends use `_send_ui_ws_json` so client disconnects
                # mid-handshake (browser tab closed before WS upgrade settles
                # — common during reload) are swallowed cleanly instead of
                # raising `ClientConnectionResetError` to the request handler.
                if not bridge.capabilities.supports_embedded_terminal:
                    if not await _send_ui_ws_json(ws, {
                        "type": "error",
                        "message": "Embedded terminals are unavailable in this runtime.",
                    }):
                        return ws
                cell = state.agents.get(cell_id)
                if cell and cell.session_id:
                    if not await _send_ui_ws_json(ws, {
                        "type": "snapshot",
                        "cell_id": cell_id,
                        "session_id": cell.session_id,
                        "data": bridge.get_terminal_buffer(cell.session_id),
                    }):
                        return ws
                else:
                    if not await _send_ui_ws_json(ws, {
                        "type": "snapshot",
                        "cell_id": cell_id,
                        "session_id": "",
                        "data": "",
                    }):
                        return ws
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        payload = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    cell = state.agents.get(cell_id)
                    if not cell or not cell.session_id:
                        continue
                    if payload.get("type") == "input":
                        await bridge.write_input(cell.session_id, payload.get("data", ""))
                    elif payload.get("type") == "resize":
                        resize_cols = int(payload.get("cols", 0) or 0)
                        resize_rows = int(payload.get("rows", 0) or 0)
                        if resize_cols <= 0 or resize_rows <= 0:
                            # Degenerate geometry (e.g. a hidden/zero-size client
                            # surface) would otherwise be floored to the 20x4 clamp
                            # and win last-writer-wins over the real terminal. Drop
                            # it like other malformed payloads above.
                            continue
                        await bridge.resize_session(
                            cell.session_id,
                            resize_cols,
                            resize_rows,
                        )
                    elif payload.get("type") == "focus":
                        await bridge.focus_session(
                            cell.session_id,
                            client_id=ui_client_id,
                        )
            finally:
                terminal_clients.get(cell_id, set()).discard(ws)
            return ws

    async def handle_api_cmd(request):
            """REST endpoint for CLI and scripting access.

            Accepts the same {"cmd": ..., ...} payloads as the WS handler.
            Returns {"ok": true, "data": ...} on success or
            {"ok": false, "error": "..."} on failure.
            """
            try:
                data = await request.json()
            except Exception:
                return web.json_response(
                    {"ok": False, "error": "invalid JSON"}, status=400)

            cmd = data.get("cmd")
            if not cmd:
                return web.json_response(
                    {"ok": False, "error": "missing 'cmd'"}, status=400)
            if daemon_stop_state.should_reject_api_request(cmd):
                return web.json_response(_daemon_stop_rejection_payload(), status=503)

            guard = _api_worker_context_guard(
                data,
                request.headers,
                getattr(request, "remote", "") or "",
            )
            if guard:
                return web.json_response(
                    {"ok": False, "error": guard["message"],
                     "type": "worker_lifecycle_guard"},
                    status=guard["status"])

            idempotency_key = str(data.get("idempotency_key", "") or "").strip()
            request_hash = ""
            if idempotency_key and is_api_write_command(cmd):
                request_hash = api_request_hash(data)
                existing = db.load_mcp_idempotency(idempotency_key)
                if existing:
                    if (
                        str(existing.get("request_hash", "") or "")
                        and str(existing.get("request_hash", "") or "") != request_hash
                    ):
                        return web.json_response(
                            {
                                "ok": False,
                                "error": (
                                    "idempotency key was reused for a different "
                                    f"API command ({cmd})"
                                ),
                            },
                            status=409,
                        )
                    try:
                        cached = json.loads(existing.get("response_json", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        cached = {}
                    db.record_mcp_health_event(
                        surface="api",
                        tool_name=str(cmd or ""),
                        event="dedupe",
                    )
                    return web.json_response(cached)

            try:
                result = await handle_command(data)
            except Exception as exc:
                log.exception("API command '%s' failed", cmd)
                return web.json_response(
                    {"ok": False, "error": str(exc)}, status=500)

            if result and result.get("type") == "error":
                return web.json_response(
                    {"ok": False, "error": result.get("message", "")})
            if result and result.get("type") == "deliverable_missing":
                # Hard-gate refusal: surface as a CLI/REST failure so the
                # documented `torque ai done`/`ready` paths see the same outcome
                # workers see via MCP. Skip idempotency caching so a retry
                # after the artifact is uploaded actually re-runs the gate.
                return web.json_response(
                    {
                        "ok": False,
                        "error": result.get(
                            "message",
                            "Deliverable artifact required before completion.",
                        ),
                        "type": "deliverable_missing",
                    },
                    status=409,
                )
            if result and result.get("type") == "review_required":
                # Mandatory-review hard-gate refusal (TORQUE:256). Same shape as
                # deliverable_missing — non-2xx, no idempotency cache, so a
                # retry after the worker derives the review re-runs the gate.
                return web.json_response(
                    {
                        "ok": False,
                        "error": result.get(
                            "message",
                            "Review required by action contract before completion.",
                        ),
                        "type": "review_required",
                    },
                    status=409,
                )

            payload = result if result else await _state_payload()
            response_payload = {"ok": True, "data": payload}
            if idempotency_key and is_api_write_command(cmd):
                db.save_mcp_idempotency(
                    idempotency_key=idempotency_key,
                    surface="api",
                    tool_name=str(cmd or ""),
                    request_hash=request_hash or api_request_hash(data),
                    response=response_payload,
                )
            if isinstance(payload, dict) and payload.get("type") == "state":
                return await _hot_json_response(response_payload)
            return web.json_response(response_payload)

    def _remove_profile_synthetic_agents(group: str, prefix: str) -> int:
            removed = 0
            members = list(state.groups.get(group, []))
            for aid in members:
                cell = state.agents.get(aid)
                if not cell:
                    continue
                if (
                        cell.command != "torque-profile-harness"
                        and not cell.name.startswith(prefix)
                ):
                    continue
                for child_id in list(state._children.get(aid, [])):
                    child = state.agents.pop(child_id, None)
                    if child:
                        state._emit("agent_remove", id=child_id,
                                    group=child.group,
                                    cell_type=child.cell_type)
                        state._db_delete_agent(child_id)
                state._children.pop(aid, None)
                state.agents.pop(aid, None)
                if aid in state.groups.get(group, []):
                    state.groups[group].remove(aid)
                state._emit("agent_remove", id=aid,
                            group=group,
                            cell_type=cell.cell_type)
                state._db_delete_agent(aid)
                removed += 1
            if removed:
                state._emit_group(group)
                state._db_save_groups()
            return removed

    async def handle_profile_get(request):
            if not profiling.is_enabled():
                raise web.HTTPNotFound()
            profiling.recorder().set_gauge("agent_count", len(state.agents))
            profiling.recorder().set_gauge("group_count", len(state.groups))
            profiling.recorder().set_gauge("board_task_count", len(state.board_tasks))
            data = profiling.recorder().snapshot()
            limit = int(request.query.get("cprofile_limit", "30") or 30)
            data["cprofile_top"] = profiling.cprofile_top(limit=limit)
            return web.json_response({"ok": True, "data": data})

    async def handle_profile_reset(request):
            if not profiling.is_enabled():
                raise web.HTTPNotFound()
            profiling.reset()
            return web.json_response({"ok": True})

    async def handle_profile_synthetic_agents(request):
            """Create N in-memory/persisted fake cells for the perf harness.

            This deliberately bypasses terminal/session creation.  It is only
            registered when profiling is enabled and is intended for ephemeral
            standalone harness data directories.
            """
            if not profiling.is_enabled():
                raise web.HTTPNotFound()
            try:
                data = await request.json()
            except Exception:
                return web.json_response(
                    {"ok": False, "error": "invalid JSON"}, status=400)
            group = str(data.get("group", "perf-harness") or "perf-harness")
            prefix = str(data.get("prefix", "perf-agent") or "perf-agent")
            count = max(0, int(data.get("count", 10) or 0))
            reset_existing = bool(data.get("reset", True))
            removed = 0
            if reset_existing:
                removed = _remove_profile_synthetic_agents(group, prefix)
            if group not in state.groups:
                state.add_group(group)

            directory = str(data.get("directory", "") or DATA_DIR)
            agent_type = str(data.get("agent_type", "claude-code")
                             or "claude-code")
            ids: list[str] = []
            for index in range(count):
                name = f"{prefix}-{index + 1:02d}"
                cell = state.add_agent(
                    name=name,
                    group=group,
                    profile="Synthetic",
                    command="torque-profile-harness",
                    directory=directory,
                )
                if not cell:
                    continue
                cell.terminal_backend = "synthetic"
                cell.session_id = f"synthetic-{uuid.uuid4().hex[:12]}"
                cell.agent_session_id = f"profile-{uuid.uuid4().hex[:12]}"
                cell.agent_type = agent_type
                cell.status = "running"
                cell.current_process = "synthetic-agent"
                cell.current_path = directory
                cell.kind = "worker"
                state._emit_agent(cell)
                state._db_save_agent(cell)
                ids.append(cell.id)

            await state.broadcast()
            profiling.recorder().set_gauge("synthetic_agent_count", len(ids))
            return web.json_response({
                "ok": True,
                "data": {
                    "group": group,
                    "agent_ids": ids,
                    "removed": removed,
                },
            })

    async def handle_upload(request):
            """POST /api/upload — multipart file upload for task images/artifacts."""
            reader = await request.multipart()
            task_id = ""
            saved = []
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name == "task_id":
                    task_id = (await part.text()).strip()
                elif part.name == "file":
                    if not task_id:
                        return web.json_response(
                            {"ok": False, "error": "task_id must come before file parts"},
                            status=400)
                    fname = part.filename or "artifact.bin"
                    # Sanitize filename — no spaces (paths with spaces
                    # get lost during terminal paste to Claude Code)
                    fname = fname.replace("/", "_").replace("\\", "_")
                    fname = fname.replace(" ", "_")
                    att_dir = ATTACHMENTS_DIR / task_id
                    att_dir.mkdir(parents=True, exist_ok=True)
                    # Deduplicate: if name exists, add suffix
                    dest = att_dir / fname
                    if dest.exists():
                        stem = dest.stem
                        suffix = dest.suffix
                        i = 1
                        while dest.exists():
                            dest = att_dir / f"{stem}_{i}{suffix}"
                            fname = dest.name
                            i += 1
                    fdata = await part.read()
                    dest.write_bytes(fdata)
                    mime = part.headers.get(
                        aiohttp.hdrs.CONTENT_TYPE,
                        mimetypes.guess_type(fname)[0] or "application/octet-stream")
                    entry = {
                        "path": str(dest),
                        "filename": fname,
                        "mime_type": mime,
                        "size_bytes": len(fdata),
                    }
                    saved.append(entry)
            if not task_id:
                return web.json_response(
                    {"ok": False, "error": "missing task_id"}, status=400)
            # Don't update the task here — the client sends attachments
            # on submit (so Cancel discards uploads properly).
            return web.json_response({"ok": True, "data": saved})

    async def handle_upload_cleanup(request):
            """POST /api/upload/cleanup — remove attachment dir for cancelled drafts."""
            try:
                data = await request.json()
            except Exception:
                return web.json_response(
                    {"ok": False, "error": "invalid JSON"}, status=400)
            task_id = data.get("task_id", "").strip()
            if not task_id:
                return web.json_response(
                    {"ok": False, "error": "missing task_id"}, status=400)
            # Only clean up if no task with this ID exists
            if task_id not in state.board_tasks:
                att_dir = ATTACHMENTS_DIR / task_id
                if att_dir.is_dir():
                    shutil.rmtree(att_dir, ignore_errors=True)
            return web.json_response({"ok": True})

    async def handle_attachment_upload(request):
            """POST /api/attachment/upload — image drops for agent message compose."""
            try:
                reader = await request.multipart()
            except Exception:
                return web.json_response(
                    {"ok": False, "error": "invalid multipart upload"}, status=400)

            agent_id = ""
            saved = []
            try:
                while True:
                    part = await reader.next()
                    if part is None:
                        break
                    if part.name == "agent_id":
                        agent_id = (await part.text()).strip()
                    elif part.name == "file":
                        if not agent_id:
                            raise AttachmentUploadError(
                                "agent_id must come before file parts", status=400)
                        mime = part.headers.get(
                            aiohttp.hdrs.CONTENT_TYPE,
                            "application/octet-stream")
                        entry = await save_message_attachment_stream(
                            agent_id=agent_id,
                            filename=part.filename or "screenshot",
                            mime_type=mime,
                            stream=part,
                            attachments_dir=ATTACHMENTS_DIR,
                        )
                        saved.append(entry)
            except AttachmentUploadError as exc:
                # Keep multi-file drops all-or-nothing from the endpoint's
                # perspective.  A later invalid/oversized part should not leave
                # earlier files from the same compose drop behind.
                for entry in saved:
                    try:
                        Path(entry.get("path", "")).unlink()
                    except Exception:
                        pass
                return web.json_response(
                    {"ok": False, "error": str(exc)}, status=exc.status)
            except Exception as exc:
                for entry in saved:
                    try:
                        Path(entry.get("path", "")).unlink()
                    except Exception:
                        pass
                log.exception("Attachment upload failed")
                return web.json_response(
                    {"ok": False, "error": str(exc) or "attachment upload failed"},
                    status=500)

            if not agent_id:
                return web.json_response(
                    {"ok": False, "error": "missing agent_id"}, status=400)
            if not saved:
                return web.json_response(
                    {"ok": False, "error": "missing file"}, status=400)
            return web.json_response({"ok": True, "data": saved})

    async def handle_logs(request):
            """GET /logs — bounded tail for daemon or supervisor profile logs."""
            try:
                since = float(request.query.get("since", "0") or 0)
            except (TypeError, ValueError):
                since = 0.0
            try:
                limit = int(request.query.get("limit", _LOG_MAX_LINES) or _LOG_MAX_LINES)
            except (TypeError, ValueError):
                limit = _LOG_MAX_LINES
            target, log_path = _log_path_for_target(
                request.query.get("target", "daemon"))
            payload = _tail_log_entries(log_path, since=since, limit=limit)
            payload["target"] = target
            payload["follow"] = request.query.get("follow", "0") in {"1", "true", "yes"}
            return web.json_response(payload)

    async def handle_ui_state(_request):
            """GET /api/ui_state — lightweight state needed before first paint.

            The Tauri shell uses this before showing the main window so native
            window geometry can be restored without waiting for the WebSocket
            snapshot.
            """
            return web.json_response({
                "window_bounds": state.window_bounds or {},
                "detached_panels": state.detached_panels or {},
                "active_group": state.active_group or "",
            })

    async def handle_serve_attachment(request):
            """GET /attachments/{task_id}/{filename} — serve attachment file."""
            task_id = request.match_info["task_id"]
            filename = request.match_info["filename"]
            fpath = ATTACHMENTS_DIR / task_id / filename
            if not fpath.is_file():
                raise web.HTTPNotFound()
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            return web.FileResponse(fpath, headers={
                "Content-Type": mime,
                "Cache-Control": "max-age=3600",
            })

    def _cleanup_orphan_attachments():
            if not ATTACHMENTS_DIR.is_dir():
                return
            task_ids = set(state.board_tasks.keys())
            for entry in ATTACHMENTS_DIR.iterdir():
                if entry.is_dir() and entry.name not in task_ids:
                    shutil.rmtree(entry, ignore_errors=True)
                    log.info("Cleaned up orphan attachments for %s", entry.name)

    return HttpRoutes(
        handle_index=handle_index,
        handle_ws=handle_ws,
        handle_terminal_ws=handle_terminal_ws,
        handle_api_cmd=handle_api_cmd,
        handle_profile_get=handle_profile_get,
        handle_profile_reset=handle_profile_reset,
        handle_profile_synthetic_agents=handle_profile_synthetic_agents,
        handle_upload=handle_upload,
        handle_upload_cleanup=handle_upload_cleanup,
        handle_attachment_upload=handle_attachment_upload,
        handle_logs=handle_logs,
        handle_ui_state=handle_ui_state,
        handle_serve_attachment=handle_serve_attachment,
        cleanup_orphan_attachments=_cleanup_orphan_attachments,
    )
