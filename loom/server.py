"""aiohttp server, WebSocket command handler, and runtime entry point."""

import asyncio
import json
import mimetypes
import os
from textwrap import dedent
import shutil
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import web
from .config import WS_PORT, DB_FILE, WEBVIEW_FILE, STANDALONE, BIND_HOST, ATTACHMENTS_DIR, log
from .db import LoomDB
from dataclasses import asdict
from .state import (
    ARCHIVED_LANE,
    MatrixState,
    merge_cleanup_flags,
    task_counts_as_done,
    task_is_closed,
)
from .events import EventLog, EventBus, PanelEventLog, health_check
from .adapters import get_adapter, get_providers
from .notifications import NotificationManager
from .worktree import WorktreeManager
from .worktree_boundaries import (
    boundary_summary,
    branch_boundary_tasks,
    latest_boundary_task,
    mark_branch_boundaries_merged,
    queued_successor_tasks,
    retarget_queued_successor_tasks,
    started_successor_tasks,
    task_boundary,
)
from .actions import ActionManager, LOOM_CONTEXT_STUB
from .artifacts import (
    normalize_artifacts,
    task_artifacts,
)
from .server_artifacts import (
    finalize_task_attachments,
    remove_task_owned_artifacts_by_filename,
    serialize_task_artifact,
    store_task_upload,
)
from .task_ids import is_canonical_task_id, is_draft_task_token
from .memory import (
    build_memory_entry,
    build_memory_link,
    build_prompt_memory_block,
    detect_current_task,
    infer_project_key,
    load_visible_memory_entries,
    normalize_entry_type,
    normalize_link_target_kind,
    normalize_retention_kind,
)
from .templates import TemplateManager
from .external_tickets import (
    ExternalTicketError,
    build_completion_comment,
    import_ticket as import_external_ticket,
    normalize_link as normalize_external_link,
    open_ticket_url,
    post_ticket_comment,
    push_ticket_status,
)
from .mcp import create_mcp_handler

from .server_actions import _action_to_yaml
from .server_agent import (
    AgentLaunchService,
    _append_task_artifacts,
    _build_self_dispatch_prompt,
    _new_agent_prompt_sequence,
    _startup_prompt_for_new_agent,
)
from .server_dispatch import (
    _find_active_worktree_owner,
    _pump_auto_dispatch_queue,
    _scheduler_loop,
    _should_handoff_shared_worktree,
    _should_queue_existing_agent_dispatch,
)
from .server_worktrees import (
    _generate_merge_message,
    _worktree_diff_updater,
    _worktree_full_diff,
)


def _should_install_keybindings() -> bool:
    """Keybindings/RPCs are only installed in iTerm2-hosted mode."""
    return not STANDALONE


def _resolve_task_id(state, identifier: str) -> str:
    """Resolve a task by exact canonical ID, legacy alias, or ID prefix."""
    ident = str(identifier or "").strip()
    if not ident:
        return ""
    if ident in state.board_tasks:
        return ident
    aliased = state.resolve_task_alias(ident)
    if aliased in state.board_tasks:
        return aliased
    prefix_matches = [
        task.id for task in state.board_tasks.values()
        if task.id.startswith(ident)
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    return ident


def _resolve_agent_id(state, identifier: str) -> str:
    """Resolve an agent by exact ID, slug, name, or ID prefix."""
    ident = str(identifier or "").strip()
    if not ident:
        return ""
    if ident in state.agents:
        cell = state.agents[ident]
        if cell.cell_type == "agent":
            return cell.id
    ident_lower = ident.lower()
    for cell in state.agents.values():
        if cell.cell_type != "agent":
            continue
        if cell.slug == ident_lower:
            return cell.id
    for cell in state.agents.values():
        if cell.cell_type != "agent":
            continue
        if cell.name.lower() == ident_lower:
            return cell.id
    for cell in state.agents.values():
        if cell.cell_type != "agent":
            continue
        if cell.id.startswith(ident):
            return cell.id
    return ""


def _resolve_memory_cell_and_task(state, cell_id: str = "",
                                  task_id: str = ""):
    """Resolve best-effort agent/task context for memory commands."""
    resolved_task = None
    resolved_cell = None

    task_id = str(task_id or "").strip()
    if task_id:
        resolved_task = state.board_tasks.get(state.resolve_task_alias(task_id))

    cell_id = str(cell_id or "").strip()
    if cell_id:
        resolved_cell = state.agents.get(cell_id)
        if resolved_task is None:
            resolved_task = detect_current_task(
                state,
                cell_id,
                explicit_task_id=task_id,
            )

    if resolved_cell is None and resolved_task and getattr(
        resolved_task, "agent_id", ""
    ):
        resolved_cell = state.agents.get(resolved_task.agent_id)

    return resolved_cell, resolved_task


def _resolve_memory_scope_ref(scope_kind: str, scope_ref: str = "",
                              *, cell=None, task=None) -> str:
    """Resolve memory scope refs from explicit values or active context."""
    kind = str(scope_kind or "").strip()
    ref = str(scope_ref or "").strip()
    if ref or not kind:
        return ref

    if kind == "task":
        if task and getattr(task, "id", ""):
            return task.id
        raise ValueError("Task scope requires an active task or scope_ref")

    if kind == "pipeline":
        if task:
            return getattr(task, "pipeline_root_id", "") or task.id
        raise ValueError("Pipeline scope requires an active task or scope_ref")

    if kind == "group":
        group_name = ""
        if task:
            group_name = getattr(task, "group", "") or ""
        if not group_name and cell:
            group_name = getattr(cell, "group", "") or ""
        if group_name:
            return group_name
        raise ValueError("Group scope requires a group or scope_ref")

    if kind == "project":
        project_key = infer_project_key(cell=cell, task=task)
        if project_key:
            return project_key
        raise ValueError(
            "Project scope requires a repo/worktree context or explicit scope_ref"
        )

    return ref


def _resolve_memory_link_ref(target_kind: str, target_ref: str = "",
                             *, cell=None, task=None) -> str:
    """Resolve memory-link target refs from explicit values or active context."""
    kind = str(target_kind or "").strip()
    ref = str(target_ref or "").strip()
    if ref:
        return ref

    if kind == "task":
        if task and getattr(task, "id", ""):
            return task.id
        raise ValueError("Task link requires an active task or target_ref")

    if kind == "agent":
        if cell and getattr(cell, "id", ""):
            return cell.id
        raise ValueError("Agent link requires an active agent or target_ref")

    if kind == "pipeline":
        if task:
            return getattr(task, "pipeline_root_id", "") or task.id
        raise ValueError("Pipeline link requires an active task or target_ref")

    return ref


def _apply_verification_report(task, payload, actor_name, save_task,
                               *, root_task=None, timestamp=None):
    """Apply a verification checkpoint update to a task and optional root."""
    if not task:
        return "", None

    from datetime import datetime, timezone

    summary = dict(task.verification_summary or {})
    if "tests_run" in payload:
        tests_run = str(payload.get("tests_run", "") or "").strip()
        if tests_run:
            summary["tests_run"] = tests_run
        else:
            summary.pop("tests_run", None)
    if "manual_smoke_done" in payload:
        summary["manual_smoke_done"] = bool(
            payload.get("manual_smoke_done")
        )
    smoke_status = str(payload.get("smoke_status", "") or "").strip()
    if smoke_status in {"passed", "failed"}:
        summary["manual_smoke_done"] = True
    if "deploy_needed" in payload:
        summary["deploy_needed"] = bool(
            payload.get("deploy_needed")
        )
    if "deploy_attempted" in payload:
        summary["deploy_attempted"] = bool(
            payload.get("deploy_attempted")
        )
    if "human_validation_pending" in payload:
        human_pending = str(
            payload.get("human_validation_pending", "") or ""
        ).strip()
        if human_pending:
            summary["human_validation_pending"] = human_pending
        else:
            summary.pop("human_validation_pending", None)

    if "verification_mode" in payload:
        mode = str(payload.get("verification_mode", "") or "").strip()
        task.verification_mode = (
            mode if mode in {"", "deploy", "restart"} else ""
        )

    verification_state = None
    if "verification_state" in payload:
        verify_state = str(
            payload.get("verification_state", "") or ""
        ).strip()
        verification_state = (
            verify_state if verify_state in {
                "", "pending", "attempted", "passed", "failed"
            } else ""
        )
    elif smoke_status in {"passed", "failed"}:
        verification_state = smoke_status
    elif "deploy_attempted" in payload and payload.get("deploy_attempted"):
        verification_state = "attempted"
    if verification_state is not None:
        task.verification_state = verification_state

    if "verification_notes" in payload:
        task.verification_notes = str(
            payload.get("verification_notes", "") or ""
        ).strip()

    task.verification_summary = summary
    task.verification_updated_at = (
        timestamp
        or datetime.now(timezone.utc).isoformat()
    )
    task.verification_updated_by = actor_name

    parts = []
    if task.verification_state:
        parts.append(f"state={task.verification_state}")
    if task.verification_mode:
        parts.append(f"mode={task.verification_mode}")
    if summary.get("tests_run"):
        parts.append(f"tests={summary['tests_run']}")
    if summary.get("manual_smoke_done"):
        parts.append("manual smoke done")
    if summary.get("deploy_needed"):
        parts.append("deploy needed")
    if summary.get("deploy_attempted"):
        parts.append("deploy attempted")
    if summary.get("human_validation_pending"):
        parts.append(
            "human validation="
            + summary["human_validation_pending"]
        )
    if task.verification_notes:
        parts.append(f"notes={task.verification_notes}")

    msg = "Verification updated"
    if parts:
        msg += ": " + "; ".join(parts)

    save_task(task)

    if root_task:
        root_task.verification_mode = task.verification_mode
        root_task.verification_state = task.verification_state
        root_task.verification_notes = task.verification_notes
        root_task.verification_updated_at = task.verification_updated_at
        root_task.verification_updated_by = task.verification_updated_by
        root_task.verification_summary = dict(summary)
        save_task(root_task)

    return msg, root_task


async def _relaunch_agent_after_worktree_removal(
        cell, *,
        bridge,
        state,
        resolve_base_dir,
        resolve_agent_launch_config,
        apply_persistent_prompt,
        build_cell_persistent_prompt):
    """Reset an agent session after its worktree is removed."""
    if cell.cell_type != "agent":
        return
    if cell.session_id:
        await bridge.close_session(cell.session_id)
    cell.status = "stopped"
    cell.session_id = None
    cell.agent_session_id = ""
    base_dir = cell.worktree_repo_root or cell.directory \
        or await resolve_base_dir(cell.group)
    launch_cfg = resolve_agent_launch_config(
        cell.group,
        base_dir=base_dir,
        explicit_template=cell.template,
        overrides={},
    )
    apply_persistent_prompt(
        cell, launch_cfg,
        build_cell_persistent_prompt(cell, launch_cfg))
    state._emit_agent(cell)
    state._db_save_agent(cell)
    await bridge.create_session(
        cell,
        env_vars=launch_cfg.get("env_vars"),
        env_file=launch_cfg.get("env_file", ""),
        shell=launch_cfg.get("shell", ""),
        system_prompt=launch_cfg.get("system_prompt", ""))


async def main(connection=None):
    log.info("Loom starting (port=%d)", WS_PORT)
    db = LoomDB(DB_FILE)
    db.init()
    log.info("SQLite database opened at %s", DB_FILE)
    state = MatrixState(db=db)
    state.load()
    log.info("State loaded: %d agents, %d groups",
             len(state.agents), len(state.groups))

    event_log = EventLog()
    panel_log = PanelEventLog(
        max_size=state.global_settings.max_event_log, db=db)
    state.panel_log = panel_log
    notifier = NotificationManager(state)
    notifier.start()
    event_bus = EventBus(state, event_log, notifier, panel_log=panel_log)
    event_bus.start()
    asyncio.create_task(health_check(state, event_log, event_bus, notifier))
    log.info("Event bus, health monitor, and notifications started")

    if STANDALONE:
        from .local_pty import LocalPtyAdapter

        bridge = LocalPtyAdapter(state)
    else:
        from .bridge import ITerm2Adapter

        bridge = ITerm2Adapter(connection, state)
    worktree_mgr = WorktreeManager()
    action_mgr = ActionManager()
    template_mgr = TemplateManager()
    agent_launch = AgentLaunchService(
        state=state,
        connection=connection,
        bridge=bridge,
        worktree_mgr=worktree_mgr,
        template_mgr=template_mgr,
    )

    from .weaver import WeaverEventBuffer
    weaver_buffer = WeaverEventBuffer(state, bridge)
    weaver_buffer.start()
    event_bus._weaver_buffer = weaver_buffer
    panel_log.on_event = weaver_buffer.on_panel_event
    log.info("Weaver event buffer started")


    async def _safe_remove_worktree(cell):
        """Remove a worktree only if no other agent shares it."""
        if not cell.worktree_path:
            return True
        other_users = [a for a in state.agents.values()
                       if a.id != cell.id
                       and a.worktree_path == cell.worktree_path]
        if other_users:
            log.info("Skipping worktree removal for '%s' — shared with %s",
                     cell.name,
                     ", ".join(a.name for a in other_users))
            cell.worktree_path = ""
            cell.worktree_branch = ""
            cell.worktree_base_branch = ""
            cell.worktree_repo_root = ""
            cell.worktree_checkpoints = 0
            return True
        else:
            return await worktree_mgr.remove(cell)

    async def _cleanup_after_merge(cell, *,
                                   close_agent: bool = False,
                                   remove_worktree: bool = False) -> dict:
        """Apply optional post-merge cleanup and return a status summary."""
        cleanup = {
            "close_agent": close_agent,
            "remove_worktree": remove_worktree,
            "agent_closed": False,
            "worktree_removed": False,
            "errors": [],
        }
        if not close_agent and not remove_worktree:
            return cleanup

        if close_agent:
            removed = state.remove_agent(cell.id)
            cleanup["agent_closed"] = True
            for c in removed:
                if c.session_id:
                    try:
                        await bridge.close_session(c.session_id)
                    except Exception as exc:
                        cleanup["errors"].append(
                            f"Failed to close session for '{c.name}': {exc}"
                        )
                if c.agent_type and c.directory:
                    adapter = get_adapter(c.agent_type)
                    try:
                        if hasattr(adapter, "uninstall_hooks"):
                            adapter.uninstall_hooks(
                                os.path.expanduser(c.directory))
                        if hasattr(adapter, "uninstall_mcp_config"):
                            adapter.uninstall_mcp_config(
                                os.path.expanduser(c.directory))
                        adapter.uninstall_persistent_prompt(
                            os.path.expanduser(c.directory),
                            _persistent_prompt_filename(c))
                    except Exception:
                        log.exception(
                            "Failed post-merge adapter cleanup for '%s'",
                            c.name)
                event_bus.cleanup_cell(c.id)
            if remove_worktree:
                removed_worktree = False
                for c in removed:
                    if not c.worktree_path:
                        continue
                    ok = await _safe_remove_worktree(c)
                    if ok:
                        removed_worktree = True
                    else:
                        cleanup["errors"].append(
                            f"Failed to remove worktree for '{c.name}'."
                        )
                cleanup["worktree_removed"] = removed_worktree
            return cleanup

        repo_root = cell.worktree_repo_root
        ok = await _safe_remove_worktree(cell)
        if ok:
            cleanup["worktree_removed"] = True
        else:
            cleanup["errors"].append(
                f"Failed to remove worktree for '{cell.name}'."
            )
        if repo_root:
            cell.directory = repo_root
        if ok and cell.cell_type == "agent" and cell.session_id:
            await _relaunch_agent_after_worktree_removal(
                cell,
                bridge=bridge,
                state=state,
                resolve_base_dir=_resolve_base_dir,
                resolve_agent_launch_config=_resolve_agent_launch_config,
                apply_persistent_prompt=_apply_persistent_prompt,
                build_cell_persistent_prompt=_build_cell_persistent_prompt,
            )
        else:
            state._emit_agent(cell)
            state._db_save_agent(cell)
        return cleanup

    def _checkpoint_message(cell) -> str:
        """Build a checkpoint commit message from the agent's last summary."""
        summary = cell.last_summary.strip()
        n = cell.worktree_checkpoints + 1
        subject = f"loom: checkpoint {n} — {cell.name}"
        if summary:
            return f"{subject}\n\n{summary}"
        return subject

    async def _on_agent_session_end(cell):
        """Handle agent turn completion: auto-checkpoint."""
        state.history_snapshot_tokens(cell)
        # Auto-checkpoint
        if cell.worktree_path and cell.cell_type == "agent":
            if cell.worktree_auto_checkpoint:
                msg = _checkpoint_message(cell)
                sha = await worktree_mgr.checkpoint(cell, message=msg)
                if sha:
                    state._db_save_agent(cell)

    # Minimum seconds between progress-triggered checkpoints per agent.
    _CHECKPOINT_INTERVAL = 300  # 5 minutes

    async def _checkpoint_on_report(cell, message: str = ""):
        """Checkpoint worktree on ai progress/done if enabled and throttled."""
        if not cell.worktree_path or cell.cell_type != "agent":
            return
        if not cell.checkpoint_on_progress:
            return
        now = time.time()
        if (cell.last_checkpoint_at
                and now - cell.last_checkpoint_at < _CHECKPOINT_INTERVAL):
            return
        n = cell.worktree_checkpoints + 1
        subject = f"loom: checkpoint {n} — {cell.name}"
        if message:
            msg = f"{subject}\n\n{message}"
        else:
            msg = subject
        sha = await worktree_mgr.checkpoint(cell, message=msg)
        if sha:
            cell.last_checkpoint_at = now
            state._db_save_agent(cell)

    async def _broadcast_toast(message, level="info"):
        """Send a toast notification to all WS clients."""
        msg = json.dumps({"type": "toast", "message": message,
                          "level": level})
        dead = set()
        for ws_client in state._ws_clients:
            try:
                await ws_client.send_str(msg)
            except Exception:
                dead.add(ws_client)
        state._ws_clients -= dead

    # Signal bridge when agent TUI is ready (hook-based session_start)
    event_bus.on_session_start = lambda cell: bridge.signal_input_ready(cell.id)
    # Handle agent turn completion (hook-based session_end)
    event_bus.on_session_end = _on_agent_session_end
    # Also checkpoint when the terminal session is actually closed (tab closed)
    bridge.on_session_terminated = _on_agent_session_end

    def _runtime_payload() -> dict:
        return {
            "standalone": STANDALONE,
            "embedded_terminal": bridge.capabilities.supports_embedded_terminal,
            "layout": "ide" if bridge.capabilities.supports_embedded_terminal else "classic",
            "terminal_backend": "pty" if STANDALONE else "iterm2",
            "home_directory": str(Path.home()),
        }

    def _state_payload() -> dict:
        return {
            "type": "state",
            "seq": state._seq,
            **state.to_dict(),
            "providers": get_providers(),
            "runtime": _runtime_payload(),
        }

    terminal_clients: dict[str, set[web.WebSocketResponse]] = {}

    async def _broadcast_terminal_output(cell_id: str, session_id: str, text: str):
        if not text:
            return
        payload = json.dumps({
            "type": "output",
            "cell_id": cell_id,
            "session_id": session_id,
            "data": text,
        })
        dead = set()
        for ws_client in terminal_clients.get(cell_id, set()):
            try:
                await ws_client.send_str(payload)
            except Exception:
                dead.add(ws_client)
        if dead:
            terminal_clients.get(cell_id, set()).difference_update(dead)

    bridge.on_terminal_output = _broadcast_terminal_output

    async def _on_terminal_disconnected(cell):
        """Auto-remove a terminal when its tab is closed (close_on_disconnect)."""
        log.info("Auto-removing terminal '%s' (close_on_disconnect)", cell.name)
        removed = state.remove_agent(cell.id)
        for c in removed:
            event_bus.cleanup_cell(c.id)

    bridge.on_terminal_disconnected = _on_terminal_disconnected
    await bridge.start()
    log.info("Startup checkpoint: bridge started")
    await bridge.reconnect_orphans()
    log.info("Startup checkpoint: orphan reconnect complete")
    asyncio.create_task(_worktree_diff_updater(state, worktree_mgr))
    log.info("Startup checkpoint: worktree diff updater scheduled")

    keybindings = None
    _displaced = [[]]

    async def _resolve_base_dir(group: str = "") -> str:
        return await agent_launch.resolve_base_dir(group)

    def _resolve_provider_command(
        provider: str, boot_command: str, default_command: str,
    ) -> tuple[str, str]:
        return agent_launch.resolve_provider_command(
            provider, boot_command, default_command
        )

    def _suggest_template_agent_name(group: str, template_name: str,
                                     base_dir: str = "") -> str:
        return agent_launch.suggest_template_agent_name(
            group, template_name, base_dir
        )

    def _resolve_agent_launch_config(group: str, *,
                                     base_dir: str = "",
                                     explicit_template: str = "",
                                     overrides: dict | None = None) -> dict:
        return agent_launch.resolve_agent_launch_config(
            group,
            base_dir=base_dir,
            explicit_template=explicit_template,
            overrides=overrides,
        )

    def _resolve_weaver_launch_config(group: str, *,
                                      base_dir: str = "",
                                      explicit_template: str = "",
                                      overrides: dict | None = None) -> dict:
        return agent_launch.resolve_weaver_launch_config(
            group,
            base_dir=base_dir,
            explicit_template=explicit_template,
            overrides=overrides,
        )

    async def _create_child_terminals(group: str, parent_cell,
                                      terminals: list[dict] | None = None,
                                      count: int = 0):
        return await agent_launch.create_child_terminals(
            group, parent_cell, terminals=terminals, count=count
        )

    def _persistent_prompt_filename(cell) -> str:
        return agent_launch.persistent_prompt_filename(cell)

    def _apply_persistent_prompt(cell, launch_cfg: dict,
                                 prompt_text: str = "") -> None:
        agent_launch.apply_persistent_prompt(cell, launch_cfg, prompt_text)

    async def _create_agent_with_config(group: str, name: str,
                                        launch_cfg: dict, *,
                                        explicit_template: str = "",
                                        target_session_id: str = "",
                                        target_window_id: str = "",
                                        persistent_prompt_text: str = ""):
        return await agent_launch.create_agent_with_config(
            group,
            name,
            launch_cfg,
            explicit_template=explicit_template,
            target_session_id=target_session_id,
            target_window_id=target_window_id,
            persistent_prompt_text=persistent_prompt_text,
        )

    async def _send_agent_prompt(cell, prompt: str, *,
                                 delay: float = 0,
                                 persist: bool = False,
                                 background: bool = False):
        return await agent_launch.send_agent_prompt(
            cell,
            prompt,
            delay=delay,
            persist=persist,
            background=background,
        )

    # -- Persistent system prompt ---------------------------------------------

    def _build_loom_system_prompt() -> str:
        """Build the persistent Loom system prompt for dispatched agents.

        Written to a file and injected via ``--append-system-prompt-file``.
        Survives ``/clear``.  Task-specific details (transitions, pipeline
        context) are in the dispatch postscript instead.
        """
        return dedent("""\
            # Loom Agent

            You are running inside Loom, an AI agent orchestration system.
            Loom tracks your task, manages your worktree, and coordinates
            you with other agents in a pipeline.

            ## Reporting tools

            Use the Loom MCP tools to report progress and completion:

            - `loom_done(message="summary")` — task complete, no follow-up needed
            - `loom_ready()` — task complete and release this agent for future work
            - `loom_progress(message="current activity")` — update your activity status
            - `loom_blocked(reason="reason")` — signal that you need help
            - `loom_error(message="message")` — report an unrecoverable error
            - `loom_verify(state="passed", tests_run="...", notes="...")` — record manual deploy/restart/smoke verification details when relevant
            - `loom_derive(description="title", action="action-name", context="details")` — create a subtask and dispatch it according to the allowed transition
            - `loom_ask(question="question", description="details")` — request a blocking human decision or approval when the task cannot continue safely without it
            - `loom_context()` — view your current task, agent info, and pipeline state

            ## Important

            Always signal completion via one of the tools above.
            Your dispatch prompt specifies which transitions are available —
            use those to determine valid `derive` targets.
            Use `loom_ask` only when a blocking human answer or approval is
            required to continue safely. If you can keep moving, do so.
            For status updates, non-blocking observations, or optional
            follow-up ideas, continue working and report them via
            `loom_progress`, `loom_done`, `loom_blocked`, or derived-task
            context instead of pausing the task.
            When in doubt, call `loom_context()` to see your current state.
        """)

    def _build_dispatch_persistent_prompt(system_prompt: str = "") -> str:
        parts = []
        if system_prompt:
            parts.append(system_prompt.rstrip())
        parts.append(_build_loom_system_prompt().rstrip())
        return "\n\n".join(parts) + "\n"

    def _build_cell_persistent_prompt(cell, launch_cfg: dict) -> str:
        if cell.cell_type != "agent" or not launch_cfg.get("agent_type"):
            return ""
        gs = state.get_group_settings(cell.group)
        if gs.weaver_agent_id == cell.id:
            from .weaver import build_weaver_system_prompt
            ws = state.get_weaver_settings(cell.group)
            return build_weaver_system_prompt(
                cell.group, ws, launch_cfg.get("system_prompt", ""),
                group_settings=gs)
        return _build_dispatch_persistent_prompt(
            launch_cfg.get("system_prompt", ""))

    def _is_designated_weaver(cell) -> bool:
        if not cell or cell.cell_type != "agent":
            return False
        gs = state.get_group_settings(cell.group)
        return bool(gs and gs.weaver_agent_id == cell.id)

    def _record_task_dispatch(cell, task, lane: str) -> None:
        """Link a task to an agent and persist dispatch history."""
        repo_root = cell.worktree_repo_root or cell.git_root or ""
        next_boundary_task_id = ""
        if cell.worktree_branch and repo_root:
            latest = latest_boundary_task(
                state.board_tasks.values(),
                repo_root=repo_root,
                branch=cell.worktree_branch,
                statuses={"open"},
            )
            if latest and latest.id != task.id:
                next_boundary_task_id = latest.id
        if task.resume_after_boundary_task_id != next_boundary_task_id:
            task.resume_after_boundary_task_id = next_boundary_task_id
        state.board_update_task(task.id, agent_id=cell.id, lane=lane)
        state.auto_dispatch_queue_remove_task(task.id)
        cell.current_task_id = task.id
        state.history_record_dispatch(cell, task)

    def _iso_to_unix(ts: str) -> float | None:
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts).timestamp()
        except (TypeError, ValueError):
            return None

    def _current_board_tasks_for_agent(agent_id: str) -> list[dict]:
        """Best-effort fallback for active agents missing persisted task rows."""
        tasks = []
        for task in state.board_tasks.values():
            if task.agent_id != agent_id:
                continue
            outcome = ""
            if task_counts_as_done(task):
                outcome = "done"
            elif task.lane == "Archived":
                outcome = "archived"
            elif "loom:error" in (task.labels or []):
                outcome = "error"
            elif "loom:blocked" in (task.labels or []):
                outcome = "blocked"
            tasks.append({
                "agent_id": agent_id,
                "task_id": task.id,
                "task_title": task.task,
                "started_at": (_iso_to_unix(task.updated_at)
                                or _iso_to_unix(task.created_at)),
                "completed_at": (_iso_to_unix(task.updated_at)
                                  if task_is_closed(task) else None),
                "outcome": outcome,
            })
        tasks.sort(key=lambda t: t.get("started_at") or 0, reverse=True)
        return tasks

    def _enrich_history_record(record: dict) -> dict:
        """Overlay live task counts for active agents."""
        if not record:
            return record
        cell = state.agents.get(record.get("id", ""))
        if cell and cell.cell_type == "agent":
            live_count = max(
                int(cell.tasks_dispatched or 0),
                len(_current_board_tasks_for_agent(cell.id)),
            )
            record["total_tasks"] = max(
                int(record.get("total_tasks") or 0), live_count)
        return record

    def _save_task_record(task) -> None:
        if not task:
            return
        task.updated_at = datetime.now(timezone.utc).isoformat()
        state._emit("task_upsert", **asdict(task))
        state._db_save_task(task)

    def _branch_boundary_tasks_for_cell(cell, statuses: set[str] | None = None
                                        ) -> list:
        repo_root = ""
        if cell:
            repo_root = cell.worktree_repo_root or cell.git_root or ""
        if not cell or not repo_root or not cell.worktree_branch:
            return []
        return branch_boundary_tasks(
            state.board_tasks.values(),
            repo_root=repo_root,
            branch=cell.worktree_branch,
            statuses=statuses,
        )

    async def _latest_boundary_state_for_cell(cell) -> dict:
        if not cell or not cell.worktree_path:
            return {"latest": None, "clean": None, "reason": ""}

        latest = latest_boundary_task(
            state.board_tasks.values(),
            repo_root=cell.worktree_repo_root or cell.git_root or "",
            branch=cell.worktree_branch,
            statuses={"open"},
        )
        if not latest:
            return {"latest": None, "clean": None, "reason": ""}

        queued = queued_successor_tasks(state.board_tasks.values(), latest.id)
        started = started_successor_tasks(state.board_tasks.values(), latest.id)
        summary = boundary_summary(
            latest,
            queued_followers=queued,
            started_followers=started,
        )
        summary["clean_mergeable"] = False

        boundary = task_boundary(latest)
        commit_sha = boundary.get("commit_sha", "")
        if not commit_sha:
            summary["reason"] = boundary.get("reason", "") or "missing_commit_sha"
            return {
                "latest": summary,
                "clean": None,
                "reason": summary["reason"],
            }

        head_sha = await worktree_mgr.current_head(cell)
        summary["head_sha"] = head_sha or ""
        if started:
            summary["reason"] = "started_successor"
            return {
                "latest": summary,
                "clean": None,
                "reason": summary["reason"],
            }
        if not head_sha:
            summary["reason"] = "missing_head_sha"
            return {
                "latest": summary,
                "clean": None,
                "reason": summary["reason"],
            }
        if head_sha != commit_sha:
            summary["reason"] = "branch_tip_moved"
            return {
                "latest": summary,
                "clean": None,
                "reason": summary["reason"],
            }

        summary["clean_mergeable"] = True
        return {"latest": summary, "clean": summary, "reason": ""}

    def _boundary_reason_message(reason: str, boundary: dict | None = None) -> str:
        if reason == "started_successor":
            if boundary and boundary.get("started_followers"):
                follower = boundary["started_followers"][0]
                return (
                    "Latest task boundary is no longer cleanly mergeable "
                    f"because follow-up task \"{follower.get('task_title', '')}\""
                    " has already started."
                )
            return (
                "Latest task boundary is no longer cleanly mergeable "
                "because a follow-up task has already started."
            )
        if reason == "branch_tip_moved":
            return (
                "Latest task boundary no longer matches the branch tip. "
                "A newer commit or external rewrite moved the branch."
            )
        if reason == "missing_head_sha":
            return "Cannot verify the current branch tip for the latest task boundary."
        if reason == "missing_commit_sha":
            return "Latest task boundary is missing its recorded commit SHA."
        if boundary:
            task_title = boundary.get("task_title", "")
            if task_title:
                return f"Latest task boundary for \"{task_title}\" is not mergeable."
        return "Latest task boundary is not mergeable."

    def _task_boundary_checkpoint_message(task, cell, message: str) -> str:
        subject = f"loom: task boundary — {task.task[:72]}"
        body_lines = [f"Task: {task.task}"]
        if cell.worktree_branch:
            body_lines.append(f"Branch: {cell.worktree_branch}")
        if message and message.strip() and message.strip() != "Done":
            body_lines.append("")
            body_lines.append(message.strip())
        return subject + "\n\n" + "\n".join(body_lines)

    async def _record_task_boundary(task, cell, message: str = "") -> dict | None:
        if not task or not cell or not cell.worktree_path:
            return None

        dirty = await worktree_mgr.has_uncommitted_changes(cell)
        boundary_sha = ""
        kind = "marker"
        reason = ""
        if dirty:
            boundary_sha = await worktree_mgr.checkpoint(
                cell,
                message=_task_boundary_checkpoint_message(task, cell, message),
            ) or ""
            kind = "checkpoint"
            if not boundary_sha:
                reason = "checkpoint_failed"
        else:
            boundary_sha = await worktree_mgr.current_head(cell) or ""
            if not boundary_sha:
                reason = "missing_head_sha"

        recorded_at = datetime.now(timezone.utc).isoformat()

        for older in _branch_boundary_tasks_for_cell(cell, statuses={"open"}):
            if older.id == task.id:
                continue
            older_boundary = dict(task_boundary(older))
            older_boundary["status"] = "superseded"
            older_boundary["superseded_by_task_id"] = task.id
            older_boundary.pop("reason", None)
            older.worktree_boundary = older_boundary
            _save_task_record(older)

        task.worktree_boundary = {
            "version": "1",
            "branch": cell.worktree_branch or "",
            "repo_root": cell.worktree_repo_root or cell.git_root or "",
            "base_branch": cell.worktree_base_branch or "",
            "commit_sha": boundary_sha,
            "kind": kind,
            "status": "open" if boundary_sha else "invalid",
            "recorded_at": recorded_at,
            "recorded_by_agent_id": cell.id,
            "message": message.strip(),
            "superseded_by_task_id": "",
            "merged_at": "",
            "merge_commit_sha": "",
            "reason": reason,
        }
        _save_task_record(task)

        for queued_task in retarget_queued_successor_tasks(
                state.board_tasks.values(),
                agent_id=cell.id,
                boundary_task_id=task.id,
                exclude_task_id=task.id):
            _save_task_record(queued_task)

        return dict(task.worktree_boundary)

    def _mark_branch_boundaries_merged(cell, merge_sha: str) -> None:
        if not cell:
            return
        repo_root = cell.worktree_repo_root or cell.git_root or ""
        for branch_task in mark_branch_boundaries_merged(
                state.board_tasks.values(),
                repo_root=repo_root,
                branch=cell.worktree_branch or "",
                merge_sha=merge_sha):
            _save_task_record(branch_task)

    # -- Postscript builder -------------------------------------------------

    def _build_postscript(task, amgr, base_dir="", is_clean=True,
                          cell=None):
        """Build the loom-ai instruction block appended to dispatch prompts.

        Only shows commands relevant to the action's transitions.
        ``done``, ``blocked``, and ``error`` are always included.
        ``derive`` only appears when the action declares transitions.
        ``ask`` only appears when an ``ask`` transition exists.

        When ``is_clean`` is False (agent already has context from prior
        tasks), emits an abbreviated version with derive/ask commands
        if the action has transitions.
        """
        # Resolve transitions for this action
        transitions = []
        if task.action_name:
            transitions = amgr.get_transitions(task.action_name,
                                               base_dir)

        def _derive_line(tr):
            when = tr.get("when", "")
            desc = f" — {when}" if when else ""
            suffix = ""
            if tr.get("target") == "self":
                suffix = " (continues in the same agent)"
            return (f"- `loom_derive(description=\"short title\", "
                    f"context=\"details\", "
                    f"action=\"{tr['action']}\")`{desc}{suffix}")

        has_transitions = any(
            isinstance(tr, dict) and tr.get("action")
            for tr in transitions)
        has_ask = any(
            isinstance(tr, dict) and tr.get("ask")
            for tr in transitions)

        commit_hint = ""
        if (cell and cell.worktree_branch
                and not cell.worktree_auto_checkpoint
                and not cell.checkpoint_on_progress):
            commit_hint = ("\nBefore reporting done, commit all your "
                           "changes with a descriptive commit message.")

        mandate = ""
        if has_transitions or has_ask:
            mandate = (
                "\n\nIMPORTANT: When you are done, you MUST use one "
                "of the Loom MCP tools below. Do NOT ask the "
                "user directly. Use `loom_ask(...)` only for a "
                "blocking human decision or approval so Loom can "
                "track it. Do not use it for status updates or "
                "optional suggestions. Do NOT just stop — always "
                "signal completion via one of these tools.")

        if not is_clean:
            abbrev = ("\n\n---" + mandate)
            if has_transitions or has_ask:
                abbrev += "\nAvailable transitions:"
                for tr in transitions:
                    if isinstance(tr, dict) and tr.get("action"):
                        abbrev += "\n" + _derive_line(tr)
                if has_ask:
                    abbrev += ("\n- `loom_ask(question=\"title\", "
                               "description=\"details\")` "
                              "— blocking human decision/approval only")
                abbrev += ("\n- `loom_done(message=\"brief summary\")` "
                           "— task complete, no follow-up")
            else:
                abbrev += ("\nUse `loom_done(message=\"brief summary\")` "
                           "when finished, or "
                           "`loom_blocked(reason=\"reason\")` "
                           "if stuck.")
            return abbrev + commit_hint

        lines = [
            mandate,
            "\nReport your progress with these Loom MCP tools:",
            "- `loom_done(message=\"brief summary\")` — task complete, no follow-up needed",
            "- `loom_ready()` — task complete and release this agent for new work",
        ]

        # Dynamic derive/ask lines from action transitions
        for tr in transitions:
            if isinstance(tr, dict) and tr.get("action"):
                lines.append(_derive_line(tr))
        if has_ask:
            lines.append(
                "- `loom_ask(question=\"title\", description=\"details\")` "
                "— blocking human decision/approval only "
                "(creates a task in Backlog for review; "
                "`description` is optional)")
        lines.extend([
            "- `loom_blocked(reason=\"reason\")` — need user input",
            "- `loom_error(message=\"message\")` — unrecoverable error",
        ])
        lines.append(
            "- `loom_verify(state=\"pending|attempted|passed|failed\", "
            "mode=\"deploy|restart\", tests_run=\"...\", notes=\"...\")` "
            "— record manual deploy/restart/smoke "
            "verification details when relevant"
        )

        # Pipeline context for derived tasks
        if task.parent_task_id:
            max_d = state.global_settings.max_pipeline_depth or "∞"
            parent = state.board_tasks.get(task.parent_task_id)
            root = state.board_tasks.get(task.pipeline_root_id)
            ctx = (f"\n\nThis task is part of a pipeline "
                   f"(depth {task.pipeline_depth}/{max_d}).")
            if parent:
                p_agent = ""
                if parent.agent_id:
                    a = state.agents.get(parent.agent_id)
                    if a:
                        p_agent = f", agent: {a.slug or a.name}"
                p_status = ""
                if parent.status:
                    p_status = f", status: {parent.status}"
                ctx += (f"\nParent task: \"{parent.task[:80]}\" "
                        f"({parent.lane}{p_status}{p_agent})")
            if root and root.id != (parent.id if parent else ""):
                ctx += f"\nRoot task: \"{root.task[:80]}\""
            lines.append(ctx)

        if commit_hint:
            lines.append(commit_hint)

        return "\n".join(lines)

    # -- Loom context builder -----------------------------------------------

    def _build_loom_context(cell, task):
        """Build the ``loom`` namespace dict for Jinja2 template rendering.

        Provides agent identity, dispatch history, worktree state, task
        metadata, and child terminal info — all derived from existing
        state at render time.
        """
        # Agent identity
        agent_ctx = {
            "name": cell.name,
            "slug": cell.slug,
            "type": cell.agent_type,
            "group": cell.group,
            "directory": cell.directory,
        }

        # Dispatch history
        linked = sorted(
            (t for t in state.board_tasks.values()
             if t.agent_id == cell.id and t.id != task.id),
            key=lambda t: t.created_at,
        )
        context_ctx = {
            "is_clean": cell.tasks_dispatched == 0,
            "tasks_dispatched": cell.tasks_dispatched,
            "previous_tasks": [
                {"task": t.task, "lane": t.lane, "action": t.action_name}
                for t in linked
            ],
        }

        # Worktree state
        worktree_ctx = {
            "active": bool(cell.worktree_path),
            "path": cell.worktree_path,
            "branch": cell.worktree_branch,
            "base_branch": cell.worktree_base_branch,
            "dirty": cell.worktree_dirty,
            "diff": cell.worktree_diff or {},
            "checkpoints": cell.worktree_checkpoints,
        }

        # Current task metadata
        parent_agent_slug = ""
        parent_agent_name = ""
        parent_agent_id = ""
        if task.parent_task_id:
            pt = state.board_tasks.get(task.parent_task_id)
            if pt and pt.agent_id:
                pa = state.agents.get(pt.agent_id)
                if pa:
                    parent_agent_id = pa.id
                    parent_agent_name = pa.name
                    parent_agent_slug = pa.slug or pa.name
        task_ctx = {
            "id": task.id,
            "title": task.task,
            "slug": task.slug,
            "description": task.description,
            "depth": task.pipeline_depth,
            "is_derived": bool(task.parent_task_id),
            "parent_task_id": task.parent_task_id,
            "parent_agent_id": parent_agent_id,
            "parent_agent_name": parent_agent_name,
            "parent_agent_slug": parent_agent_slug,
            "labels": list(task.labels),
            "group": task.group,
            "status": task.status,
            "verification_mode": task.verification_mode,
            "verification_state": task.verification_state,
            "verification_notes": task.verification_notes,
            "verification_updated_at": task.verification_updated_at,
            "verification_updated_by": task.verification_updated_by,
            "verification_summary": task.verification_summary or {},
            "worktree_boundary": task.worktree_boundary or {},
            "resume_after_boundary_task_id": (
                task.resume_after_boundary_task_id or ""
            ),
            "attachments": [
                {"path": a["path"], "filename": a["filename"]}
                for a in (task.attachments or [])
            ],
            "artifacts": task_artifacts(task.attachments or [],
                                         task.artifacts or []),
        }

        # Child terminals of the target agent
        terminals_ctx = []
        for cid in state._children.get(cell.id, []):
            ch = state.agents.get(cid)
            if ch:
                terminals_ctx.append({
                    "name": ch.name,
                    "slug": ch.slug,
                    "current_path": ch.current_path,
                    "current_process": ch.current_process,
                    "current_branch": ch.current_branch,
                })

        return {
            "agent": agent_ctx,
            "context": context_ctx,
            "worktree": worktree_ctx,
            "task": task_ctx,
            "terminals": terminals_ctx,
        }

    # -- Command handler ----------------------------------------------------

    async def handle_command(data: dict) -> dict | None:
        """Handle a command, return a direct-response dict or None.

        Direct-response commands (get_config, get_group_settings,
        worktree_history) return immediately without broadcasting.
        Mutation commands broadcast state to all WS clients and
        optionally return a result dict.
        """
        cmd = data.get("cmd")
        log.info("CMD %s %s", cmd,
                 {k: v for k, v in data.items() if k != "cmd"})

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
                "resolved_agent_defaults": resolved_defaults,
                "providers": get_providers(),
                "templates": template_mgr.list_templates(current_path
                                                          or await _resolve_base_dir(group)),
                "playbooks": state.list_playbooks(group=group,
                                                   status="published",
                                                   limit=200),
                "runtime": _runtime_payload(),
            }

        # get_group_settings: respond directly, no state mutation
        if cmd == "get_group_settings":
            group = data.get("group", "")
            gs = state.get_group_settings(group)
            pnames = await bridge.list_profiles()
            base_dir = await _resolve_base_dir(group)
            return {
                "type": "group_settings",
                "group": group,
                "settings": asdict(gs),
                "weaver_settings": asdict(state.get_weaver_settings(group)),
                "resolved_agent_defaults": template_mgr.resolve_agent_config(
                    "", gs, {}, base_dir=base_dir),
                "profiles": pnames,
                "providers": get_providers(),
                "templates": template_mgr.list_templates(base_dir),
                "actions": action_mgr.list_actions(base_dir),
                "playbooks": state.list_playbooks(group=group,
                                                   status="published",
                                                   limit=200),
                "runtime": _runtime_payload(),
            }

        # get_global_settings: respond directly
        if cmd == "get_global_settings":
            return {
                "type": "global_settings",
                "settings": asdict(state.global_settings),
                "keybinding_defaults": keybindings.get_default_bindings(),
            }

        # get_events: paginated event log query
        if cmd == "get_events":
            before_id = int(data.get("before_id", 0))
            limit = min(int(data.get("limit", 50)), 200)
            events = panel_log.get_page(limit=limit, before_id=before_id)
            return {"type": "events_page", "events": events}

        # Agent history queries
        if cmd == "get_agent_history":
            status_filter = data.get("status", "")
            limit = min(int(data.get("limit", 50)), 200)
            offset = int(data.get("offset", 0))
            records = db.load_agent_history(
                status_filter=status_filter, limit=limit, offset=offset)
            records = [_enrich_history_record(r) for r in records]
            return {"type": "agent_history_list",
                    "records": records}

        if cmd == "get_agent_history_detail":
            agent_id = data.get("agent_id", "")
            if not agent_id:
                return {"type": "error",
                        "message": "agent_id required"}
            record = db.load_agent_history_detail(agent_id)
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

        if cmd == "get_playbook_candidates":
            limit = min(int(data.get("limit", 50)), 200)
            return {
                "type": "playbook_candidates",
                "group": data.get("group", ""),
                "candidates": state.list_playbook_candidates(
                    group=data.get("group", ""), limit=limit),
            }

        if cmd == "extract_playbook_candidates":
            return {
                "type": "playbook_candidates",
                "group": data.get("group", ""),
                "candidates": state.extract_playbook_candidates(
                    group=data.get("group", "")),
            }

        if cmd == "get_playbooks":
            limit = min(int(data.get("limit", 50)), 200)
            return {
                "type": "playbooks",
                "group": data.get("group", ""),
                "status": data.get("status", ""),
                "playbooks": state.list_playbooks(
                    group=data.get("group", ""),
                    status=data.get("status", ""),
                    limit=limit,
                ),
            }

        if cmd == "get_playbook":
            playbook_id = data.get("id", "")
            if not playbook_id:
                return {"type": "error", "message": "id required"}
            playbook = state.get_playbook(playbook_id)
            if not playbook:
                return {"type": "error", "message": "Playbook not found"}
            return {"type": "playbook_detail", "playbook": playbook}

        if cmd == "generate_playbook_draft":
            candidate_id = data.get("candidate_id", "")
            if not candidate_id:
                return {"type": "error",
                        "message": "candidate_id required"}
            candidate = db.load_playbook_candidate(candidate_id)
            if not candidate:
                return {"type": "error",
                        "message": "Playbook candidate not found"}
            base_dir = await _resolve_base_dir(
                data.get("group", "") or candidate.get("group", ""))
            from .playbooks import build_playbook_draft

            draft = build_playbook_draft(
                candidate, action_mgr, template_mgr, base_dir=base_dir)
            existing = state.get_playbook(draft["id"])
            if existing and existing.get("status") == "published":
                draft["created_at"] = existing.get("created_at",
                                                    draft["created_at"])
                draft["published_at"] = existing.get("published_at")
                draft["status"] = existing.get("status", "published")
            state.save_playbook(draft)
            return {"type": "playbook_detail", "playbook": draft}

        if cmd == "publish_playbook_draft":
            playbook_id = data.get("id", "")
            if not playbook_id:
                return {"type": "error", "message": "id required"}
            playbook = state.get_playbook(playbook_id)
            if not playbook:
                return {"type": "error", "message": "Playbook not found"}
            if playbook.get("status") != "draft":
                return {"type": "error",
                        "message": "Only draft playbooks can be published"}
            preview = playbook.get("publication_preview", {})
            if not preview.get("ready_to_publish", False):
                return {"type": "error",
                        "message": "Draft is missing required action "
                                   "or template references"}
            from .playbooks import publish_playbook_record

            published = publish_playbook_record(playbook)
            state.save_playbook(published)
            return {"type": "playbook_detail", "playbook": published}

        if cmd == "discard_playbook_draft":
            playbook_id = data.get("id", "")
            if not playbook_id:
                return {"type": "error", "message": "id required"}
            playbook = state.get_playbook(playbook_id)
            if not playbook:
                return {"type": "error", "message": "Playbook not found"}
            if playbook.get("status") != "draft":
                return {"type": "error",
                        "message": "Only draft playbooks can be discarded"}
            from .playbooks import discard_playbook_record

            discarded = discard_playbook_record(playbook)
            state.save_playbook(discarded)
            return {"type": "playbook_detail", "playbook": discarded}

        # list_actions: respond directly
        if cmd == "list_actions":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            actions = action_mgr.list_actions(base_dir)
            return {"type": "actions", "group": data.get("group", ""),
                    "actions": actions}

        if cmd == "list_templates":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            return {
                "type": "templates",
                "group": data.get("group", ""),
                "templates": template_mgr.list_templates(base_dir),
            }

        if cmd == "get_template":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            scope = data.get("scope", "")
            tpl = template_mgr.load_template(
                data.get("name", ""), base_dir, scope=scope)
            if not tpl:
                return {"type": "error",
                        "message": f"Template \"{data['name']}\" not found"}
            return {"type": "template_detail", "name": data["name"],
                    "template": tpl}

        if cmd == "save_template":
            name = data.get("name", "").strip()
            if not name:
                return {"type": "error", "message": "Template name required"}
            base_dir = await _resolve_base_dir(data.get("group", ""))
            scope = data.get("scope", "project")
            old_name = data.get("old_name", "").strip()
            if old_name and old_name != name:
                template_mgr.delete_template(old_name, base_dir=base_dir)
                template_mgr.delete_template(old_name, scope="user",
                                             base_dir=base_dir)
            template_mgr.save_template(
                name, data.get("template", {}), scope=scope, base_dir=base_dir)
            return {
                "type": "templates",
                "group": data.get("group", ""),
                "templates": template_mgr.list_templates(base_dir),
                "saved": name,
            }

        if cmd == "delete_template":
            name = data.get("name", "").strip()
            if not name:
                return {"type": "error", "message": "Template name required"}
            base_dir = await _resolve_base_dir(data.get("group", ""))
            deleted = template_mgr.delete_template(
                name, scope=data.get("scope", ""), base_dir=base_dir)
            if not deleted:
                return {"type": "error",
                        "message": f"Template \"{name}\" not found"}
            return {
                "type": "templates",
                "group": data.get("group", ""),
                "templates": template_mgr.list_templates(base_dir),
                "deleted": name,
            }

        if cmd == "render_template":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            group = data.get("group", "")
            gs = state.get_group_settings(group)
            rendered = template_mgr.resolve_agent_config(
                data.get("name", ""), gs, data.get("overrides", {}),
                base_dir=base_dir)
            return {
                "type": "template_rendered",
                "name": data.get("name", ""),
                "config": rendered,
            }

        # get_action: respond directly
        if cmd == "get_action":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            scope = data.get("scope", "")
            # Scope-aware loading: search only the target directory
            raw = None
            if scope == "user":
                gdir = os.path.expanduser("~/.loom/actions")
                for suffix in ("", ".yaml", ".yml"):
                    p = os.path.join(gdir, data["name"] + suffix)
                    if os.path.isfile(p):
                        with open(p) as f:
                            raw = f.read()
                        break
            if raw is None:
                raw = action_mgr._load_raw(data["name"], base_dir)
            if not raw:
                return {"type": "error",
                        "message": f"Action \"{data['name']}\" not found"}
            # Editor mode: parse raw YAML without Jinja2 rendering
            from .actions import parse_yaml
            try:
                act = parse_yaml(raw) or {}
            except Exception:
                act = {}
            avars = action_mgr.get_action_vars(raw)
            return {"type": "action_detail", "name": data["name"],
                    "action": act, "vars": avars}

        # render_action: render action prompt without creating an agent
        if cmd == "render_action":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            raw = action_mgr._load_raw(data["name"], base_dir)
            if not raw:
                return {"type": "error",
                        "message": f"Action \"{data['name']}\" not found"}
            variables = data.get("vars", {})
            rendered = action_mgr.render_action(raw, variables)
            return {"type": "action_rendered",
                    "name": data["name"],
                    "prompt": rendered.get("prompt", ""),
                    "group": rendered.get("group", ""),
                    "labels": rendered.get("labels", [])}

        # save_action: write action YAML to disk
        if cmd == "save_action":
            name = data.get("name", "").strip()
            if not name:
                return {"type": "error", "message": "Action name required"}
            act_data = data.get("action", {})
            # Validate {{ TASK }} in prompt
            prompt = act_data.get("prompt", "")
            if not action_mgr.validate_prompt(prompt):
                return {"type": "error",
                        "message": "Action prompt must contain {{ TASK }}"}
            # Reject 'loom' as a variable name (reserved namespace)
            avars = action_mgr.get_action_vars(prompt)
            for av in avars:
                if av.get("name") == "loom":
                    return {"type": "error",
                            "message": "'loom' is a reserved variable "
                                       "name"}
            scope = data.get("scope", "project")  # "project" or "user"
            base_dir = await _resolve_base_dir(data.get("group", ""))

            if scope == "user":
                tdir = os.path.expanduser("~/.loom/actions")
                os.makedirs(tdir, exist_ok=True)
            else:
                tdir = action_mgr.find_actions_dir(base_dir)
                if not tdir:
                    d = base_dir or os.getcwd()
                    tdir = os.path.join(d, ".loom", "actions")
                    os.makedirs(tdir, exist_ok=True)
            # Rename or scope change: delete old file from any location
            old_name = data.get("old_name", "")
            if old_name:
                for old_dir in action_mgr.find_actions_dirs(base_dir):
                    for suffix in (".yaml", ".yml"):
                        old_path = os.path.join(old_dir, old_name + suffix)
                        if os.path.isfile(old_path):
                            os.remove(old_path)
                            break
            path = os.path.join(tdir, name + ".yaml")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            yaml_text = _action_to_yaml(name, act_data)
            with open(path, "w") as f:
                f.write(yaml_text)
            # Return updated list
            actions = action_mgr.list_actions(base_dir)
            return {"type": "actions",
                    "group": data.get("group", ""),
                    "actions": actions,
                    "saved": name}

        # delete_action: remove action file from disk
        if cmd == "delete_action":
            name = data.get("name", "").strip()
            if not name:
                return {"type": "error", "message": "Action name required"}
            base_dir = await _resolve_base_dir(data.get("group", ""))
            deleted = False
            for tdir in action_mgr.find_actions_dirs(base_dir):
                for suffix in (".yaml", ".yml"):
                    path = os.path.join(tdir, name + suffix)
                    if os.path.isfile(path):
                        os.remove(path)
                        deleted = True
                        break
                if deleted:
                    break
            if not deleted:
                return {"type": "error",
                        "message": f"Action \"{name}\" not found"}
            actions = action_mgr.list_actions(base_dir)
            return {"type": "actions",
                    "group": data.get("group", ""),
                    "actions": actions,
                    "deleted": name}

        # -- Mutation commands: broadcast state at the end --
        result = None
        try:
            if cmd == "refresh":
                pass

            elif cmd == "resync":
                # Client detected a sequence gap — send full snapshot
                return _state_payload()

            elif cmd == "add_group":
                state.add_group(data["group"])

            elif cmd == "update_group_settings":
                settings = data.get("settings", {})
                state.update_group_settings(data["group"], **settings)

            elif cmd == "update_global_settings":
                settings = data.get("settings", {})
                old_kb = state.global_settings.keybindings.copy()
                state.update_global_settings(**settings)
                new_kb = state.global_settings.keybindings
                if new_kb != old_kb and _should_install_keybindings() and keybindings:
                    _displaced[0] = await keybindings.reinstall(
                        connection, _displaced[0],
                        overrides=new_kb or None)
                # Propagate max_event_log to panel log
                new_max = state.global_settings.max_event_log
                if panel_log._max_size != new_max:
                    panel_log._max_size = new_max
                    panel_log._events = deque(
                        panel_log._events, maxlen=new_max)
                    if panel_log._db:
                        panel_log._db.trim_panel_events(new_max)

            elif cmd == "suspend_keybindings" and _should_install_keybindings() and keybindings:
                await keybindings.remove(connection, _displaced[0])

            elif cmd == "resume_keybindings" and _should_install_keybindings() and keybindings:
                _kb_overrides = (state.global_settings.keybindings
                                 or None)
                _displaced[0] = await keybindings.reinstall(
                    connection, _displaced[0],
                    overrides=_kb_overrides)

            elif cmd == "remove_group":
                removed = state.remove_group(data["group"])
                for c in removed:
                    if c.session_id:
                        await bridge.close_session(c.session_id)
                    if c.agent_type and c.directory:
                        adapter = get_adapter(c.agent_type)
                        if hasattr(adapter, "uninstall_hooks"):
                            adapter.uninstall_hooks(
                                os.path.expanduser(c.directory))
                        if hasattr(adapter, "uninstall_mcp_config"):
                            adapter.uninstall_mcp_config(
                                os.path.expanduser(c.directory))
                        adapter.uninstall_persistent_prompt(
                            os.path.expanduser(c.directory),
                            _persistent_prompt_filename(c))
                    event_bus.cleanup_cell(c.id)
                    await _safe_remove_worktree(c)

            elif cmd == "rename_group":
                state.rename_group(data["group"], data["new_name"])

            elif cmd == "add_agent":
                group = data["group"]
                is_weaver = data.get("is_weaver", False)

                # Enforce one weaver per group
                if is_weaver:
                    gs_check = state.get_group_settings(group)
                    if gs_check.weaver_agent_id:
                        existing = state.agents.get(
                            gs_check.weaver_agent_id)
                        ename = existing.name if existing else "unknown"
                        result = {
                            "type": "error",
                            "message": (
                                f"Group '{group}' already has a "
                                f"weaver: {ename}")}
                        # Skip agent creation — jump to broadcast
                        is_weaver = False
                        data = {}  # prevent fallthrough

                if data:
                    base_dir = await _resolve_base_dir(group)
                    explicit_template = data.get("template", "").strip()
                    _overrides = dict(data)
                    resolver = (
                        _resolve_weaver_launch_config
                        if is_weaver else _resolve_agent_launch_config
                    )
                    launch_cfg = resolver(
                        group,
                        base_dir=base_dir,
                        explicit_template=explicit_template,
                        overrides=_overrides,
                    )

                    persistent_prompt_text = ""
                    # Weaver: build persistent prompt and skip worktree
                    if is_weaver:
                        from .weaver import build_weaver_system_prompt
                        ws = state.get_weaver_settings(group)
                        action_sp = launch_cfg.get("system_prompt", "")
                        persistent_prompt_text = build_weaver_system_prompt(
                            group, ws, action_sp,
                            group_settings=state.get_group_settings(group))
                        launch_cfg["worktree"] = False
                    startup_prompt = _startup_prompt_for_new_agent(
                        agent_type=launch_cfg.get("agent_type", ""),
                        persistent_prompt_text=persistent_prompt_text,
                        is_weaver=is_weaver,
                    )

                    name = (data.get("name", "") or "").strip()
                    if not name:
                        if is_weaver:
                            name = "Weaver"
                        elif explicit_template:
                            name = _suggest_template_agent_name(
                                group, explicit_template, base_dir)
                        else:
                            name = state.next_cell_name(group, "agent")
                    cell = await _create_agent_with_config(
                        group, name, launch_cfg,
                        explicit_template=explicit_template,
                        target_session_id=data.get(
                            "target_session_id", ""),
                        target_window_id=data.get(
                            "target_window_id", ""),
                        persistent_prompt_text=persistent_prompt_text)
                    if cell:
                        # Designate as weaver
                        if is_weaver:
                            state.update_group_settings(
                                group, weaver_agent_id=cell.id)
                            # Reorder now that weaver_agent_id is set
                            # (the reorder in create_session ran too early)
                            await bridge.reorder_tabs()

                        if launch_cfg.get("terminals"):
                            await _create_child_terminals(
                                group, cell,
                                terminals=launch_cfg["terminals"])
                        else:
                            gs = state.get_group_settings(group)
                            if gs.auto_terminals > 0:
                                await _create_child_terminals(
                                    group, cell,
                                    count=gs.auto_terminals)
                        if cell.session_id:
                            for prompt_text, send_kwargs in \
                                    _new_agent_prompt_sequence(
                                        launch_cfg,
                                        startup_prompt=startup_prompt):
                                await _send_agent_prompt(
                                    cell, prompt_text, **send_kwargs)

            elif cmd == "add_terminal":
                group = data.get("group", "")
                parent_id = data.get("parent_id", "")
                # Resolve group from parent if needed
                resolve_group = group
                if parent_id:
                    p = state.agents.get(parent_id)
                    if p:
                        resolve_group = p.group
                gs = state.get_group_settings(resolve_group or group)
                profile = data.get("profile") or gs.terminal_profile or gs.profile or "Default"
                parent_wt = p.worktree_path if parent_id and p and p.worktree_path else ""
                directory = data.get("directory") or parent_wt or gs.terminal_directory or gs.default_directory or ""
                _tc = gs.terminal_tab_color
                tab_color = data.get("tab_color") or (_tc if _tc != "none" else "") or gs.tab_color or ""
                shell = data.get("shell") or gs.terminal_shell or gs.shell or ""
                env = {**gs.env_vars, **gs.terminal_env_vars, **(data.get("env_vars") or {})} or None
                command = data.get("command") or gs.terminal_boot_command or ""
                cmd_args = data.get("command_args") or gs.terminal_command_args or ""
                if cmd_args and command:
                    command = (command + " " + cmd_args).strip()
                init_script = data.get("init_script") or gs.terminal_init_script or ""
                env_file = data.get("env_file") or gs.terminal_env_file or gs.env_file

                cell = state.add_terminal(
                    name=data["name"], group=group,
                    terminal_backend="pty" if bridge.capabilities.supports_embedded_terminal else "iterm2",
                    profile=profile, command=command,
                    directory=directory, tab_color=tab_color,
                    parent_id=parent_id,
                )
                if cell:
                    await bridge.create_session(
                        cell, env_vars=env,
                        env_file=env_file,
                        init_script=init_script,
                        shell=shell)

            elif cmd == "remove_agent":
                removed = state.remove_agent(data["id"])
                for c in removed:
                    if c.cell_type == "agent":
                        state.history_remove_agent(c)
                    if c.session_id:
                        await bridge.close_session(c.session_id)
                    # Clean up hooks, MCP config, persistent prompt
                    if c.agent_type and c.directory:
                        adapter = get_adapter(c.agent_type)
                        if hasattr(adapter, "uninstall_hooks"):
                            adapter.uninstall_hooks(
                                os.path.expanduser(c.directory))
                        if hasattr(adapter, "uninstall_mcp_config"):
                            adapter.uninstall_mcp_config(
                                os.path.expanduser(c.directory))
                        adapter.uninstall_persistent_prompt(
                            os.path.expanduser(c.directory),
                            _persistent_prompt_filename(c))
                    # Clean up event bus state
                    event_bus.cleanup_cell(c.id)
                    await _safe_remove_worktree(c)

            elif cmd == "update_agent":
                cell = state.agents.get(data["id"])
                if cell:
                    old_name = cell.name
                    new_name = data.get("name", cell.name)
                    new_color = data.get("tab_color", cell.tab_color)
                    new_icon = data.get("icon", cell.icon)
                    state.update_agent(data["id"], name=new_name,
                                       tab_color=new_color,
                                       icon=new_icon)
                    if new_name != old_name and cell.cell_type == "agent":
                        state.history_update_agent(
                            cell, name=new_name, slug=cell.slug)
                    if cell.session_id:
                        await bridge.update_session(cell, old_name)

            elif cmd == "focus_agent":
                cell = state.agents.get(data["id"])
                if cell and cell.session_id:
                    await bridge.focus_session(cell.session_id)

            elif cmd == "send_text":
                cell = state.agents.get(data["id"])
                if cell and cell.session_id:
                    await bridge.send_text(cell.session_id, data["text"])
                    cell.status = "running"
                    state._emit_agent(cell)
                    # Ephemeral status — no DB write needed

            elif cmd == "broadcast_to_group":
                for aid in state.groups.get(data["group"], []):
                    cell = state.agents.get(aid)
                    if cell and cell.session_id:
                        await bridge.send_text(
                            cell.session_id, data["text"])
                        cell.status = "running"
                        state._emit_agent(cell)
                    # Also send to child terminals
                    for child_id in state._children.get(aid, []):
                        child = state.agents.get(child_id)
                        if child and child.session_id:
                            await bridge.send_text(
                                child.session_id, data["text"])
                            child.status = "running"
                            state._emit_agent(child)
                # Ephemeral status — no DB write needed

            elif cmd == "relaunch_agent":
                cell = state.agents.get(data["id"])
                if cell and cell.status == "stopped":
                    owner = _find_active_worktree_owner(state, cell)
                    if owner:
                        result = {
                            "type": "error",
                            "message":
                                f"Cannot relaunch '{cell.name}' while "
                                f"'{owner.name}' is active on "
                                f"{owner.worktree_branch or owner.worktree_path}",
                        }
                    else:
                        gs = state.get_group_settings(cell.group)
                        base_dir = cell.worktree_repo_root or cell.directory \
                            or await _resolve_base_dir(cell.group)
                        resolver = (
                            _resolve_weaver_launch_config
                            if _is_designated_weaver(cell)
                            else _resolve_agent_launch_config
                        )
                        launch_cfg = resolver(
                            cell.group,
                            base_dir=base_dir,
                            explicit_template=cell.template,
                            overrides={},
                        )
                        cell.session_resume = bool(
                            launch_cfg.get(
                                "session_resume", cell.session_resume))
                        cell.idle_timeout = int(
                            launch_cfg.get(
                                "idle_timeout", cell.idle_timeout) or 0)
                        if cell.cell_type == "agent":
                            cell.command = launch_cfg.get(
                                "command", cell.command)
                            cell.profile = launch_cfg.get(
                                "profile", cell.profile)
                            cell.tab_color = launch_cfg.get(
                                "tab_color", cell.tab_color)
                            cell.icon = launch_cfg.get("icon", cell.icon)
                            cell.agent_type = launch_cfg.get(
                                "agent_type", cell.agent_type)
                            if not cell.worktree_path:
                                cell.directory = launch_cfg.get(
                                    "directory", cell.directory)
                        cell.worktree_base_dir = (
                            launch_cfg.get("worktree_base_dir")
                            or cell.worktree_base_dir
                            or ".loom/worktrees")
                        cell.worktree_auto_checkpoint = bool(
                            launch_cfg.get(
                                "worktree_auto_checkpoint",
                                cell.worktree_auto_checkpoint))
                        cell.checkpoint_on_progress = bool(
                            launch_cfg.get(
                                "checkpoint_on_progress",
                                cell.checkpoint_on_progress))
                        cell.worktree_merge_squash = bool(
                            launch_cfg.get(
                                "worktree_merge_squash",
                                cell.worktree_merge_squash))
                        state._emit_agent(cell)
                        state._db_save_agent(cell)
                        if cell.cell_type == "terminal":
                            env = {**gs.env_vars, **gs.terminal_env_vars} \
                                or None
                            ef = gs.terminal_env_file or gs.env_file
                            shell = gs.terminal_shell or gs.shell or ""
                            init = gs.terminal_init_script
                        else:
                            env = launch_cfg.get("env_vars")
                            ef = launch_cfg.get("env_file", "")
                            shell = launch_cfg.get("shell", "")
                            init = ""
                            prev_directory = cell.directory
                            # Worktree handling on relaunch
                            if cell.worktree_path:
                                if await worktree_mgr.validate(cell):
                                    # Reuse existing worktree
                                    cell.directory = cell.worktree_path
                                    log.info("Reusing worktree for '%s': %s",
                                             cell.name, cell.worktree_path)
                                else:
                                    # Worktree gone — clear and recreate if enabled
                                    log.warning("Worktree invalid for '%s', "
                                                "clearing", cell.name)
                                    cell.worktree_path = ""
                                    cell.worktree_branch = ""
                                    cell.worktree_repo_root = ""
                                    cell.worktree_base_branch = ""
                                    state._emit_agent(cell)
                                    state._db_save_agent(cell)
                            # Create new worktree if enabled and none exists
                            if not cell.worktree_path \
                                    and launch_cfg.get("worktree") \
                                    and cell.directory:
                                repo_root = await worktree_mgr.get_repo_root(
                                    cell.directory)
                                if repo_root:
                                    wt_path = await worktree_mgr.create(
                                        cell, repo_root,
                                        base_dir=cell.worktree_base_dir
                                        or ".loom/worktrees",
                                        base_branch=launch_cfg.get(
                                            "worktree_base_branch", "")
                                        or "",
                                        symlinks=launch_cfg.get(
                                            "worktree_symlinks", []))
                                    if wt_path:
                                        cell.directory = wt_path
                                        state._emit_agent(cell)
                                        state._db_save_agent(cell)
                            if (cell.agent_type and prev_directory
                                    and prev_directory != cell.directory):
                                get_adapter(cell.agent_type) \
                                    .uninstall_persistent_prompt(
                                        os.path.expanduser(prev_directory),
                                        _persistent_prompt_filename(cell))
                        _apply_persistent_prompt(
                            cell, launch_cfg,
                            _build_cell_persistent_prompt(cell, launch_cfg))
                        state._emit_agent(cell)
                        state._db_save_agent(cell)
                        await bridge.create_session(
                            cell, env_vars=env, env_file=ef,
                            init_script=init, shell=shell,
                            system_prompt=launch_cfg.get(
                                "system_prompt", ""))

            elif cmd == "move_group":
                state.move_group(data["group"], data.get("before", ""))
                await bridge.reorder_tabs()

            elif cmd == "move_agent":
                state.move_agent(data["id"], data["target_group"],
                                 data.get("before", ""))
                await bridge.reorder_tabs()

            elif cmd == "reparent_terminal":
                state.reparent_terminal(data["id"],
                                        data.get("parent_id", ""))
                await bridge.reorder_tabs()

            elif cmd == "reorder_child":
                state.reorder_child(data["id"], data["parent_id"],
                                    data.get("before", ""))
                await bridge.reorder_tabs()

            elif cmd == "clear_agent_context":
                cell = state.agents.get(data.get("id", ""))
                if cell and cell.session_id and cell.cell_type == "agent":
                    if cell.agent_type in ("claude-code", "codex"):
                        await bridge.send_text(
                            cell.session_id, "/clear\r")
                    cell.tasks_dispatched = 0
                    cell.agent_session_id = ""
                    cell.current_task_id = ""
                    cell.mcp_messages = []
                    state._emit_agent(cell)
                    state._db_save_agent(cell)

            elif cmd == "worktree_create":
                cell = state.agents.get(data["id"])
                if cell and not cell.worktree_path and cell.directory:
                    gs = state.get_group_settings(cell.group)
                    repo_root = await worktree_mgr.get_repo_root(
                        cell.directory)
                    if repo_root:
                        wt_path = await worktree_mgr.create(
                            cell, repo_root,
                            base_dir=cell.worktree_base_dir
                                or ".loom/worktrees",
                            base_branch=cell.worktree_base_branch
                                or gs.worktree_base_branch or "",
                            symlinks=gs.worktree_symlinks)
                        if wt_path:
                            cell.directory = wt_path
                            state._emit_agent(cell)
                            state._db_save_agent(cell)
                            # Relaunch if requested by the UI
                            if data.get("relaunch"):
                                if cell.session_id:
                                    await bridge.close_session(
                                        cell.session_id)
                                cell.status = "stopped"
                                cell.session_id = None
                                # Clear session ID — the old session
                                # may not exist (no prompts sent yet)
                                cell.agent_session_id = ""
                                base_dir = cell.worktree_repo_root \
                                    or cell.directory \
                                    or await _resolve_base_dir(cell.group)
                                launch_cfg = _resolve_agent_launch_config(
                                    cell.group,
                                    base_dir=base_dir,
                                    explicit_template=cell.template,
                                    overrides={},
                                )
                                if cell.agent_type:
                                    get_adapter(cell.agent_type) \
                                        .uninstall_persistent_prompt(
                                            os.path.expanduser(repo_root),
                                            _persistent_prompt_filename(cell))
                                _apply_persistent_prompt(
                                    cell, launch_cfg,
                                    _build_cell_persistent_prompt(
                                        cell, launch_cfg))
                                state._emit_agent(cell)
                                state._db_save_agent(cell)
                                await bridge.create_session(
                                    cell,
                                    env_vars=launch_cfg.get("env_vars"),
                                    env_file=launch_cfg.get("env_file", ""),
                                    shell=launch_cfg.get("shell", ""),
                                    system_prompt=launch_cfg.get(
                                        "system_prompt", ""),
                                    target_session_id=data.get(
                                        "target_session_id", ""),
                                    target_window_id=data.get(
                                        "target_window_id", ""))

            elif cmd == "worktree_remove":
                cell = state.agents.get(data["id"])
                if cell and cell.worktree_path:
                    # Restore directory to original repo root
                    repo_root = cell.worktree_repo_root
                    await _safe_remove_worktree(cell)
                    if repo_root:
                        cell.directory = repo_root
                    # Relaunch if requested by the UI
                    if data.get("relaunch") and cell.cell_type == "agent":
                        await _relaunch_agent_after_worktree_removal(
                            cell,
                            bridge=bridge,
                            state=state,
                            resolve_base_dir=_resolve_base_dir,
                            resolve_agent_launch_config=_resolve_agent_launch_config,
                            apply_persistent_prompt=_apply_persistent_prompt,
                            build_cell_persistent_prompt=_build_cell_persistent_prompt,
                        )
                    else:
                        state._emit_agent(cell)
                        state._db_save_agent(cell)

            elif cmd == "worktree_checkpoint":
                cell = state.agents.get(data["id"])
                if cell and cell.worktree_path:
                    msg = _checkpoint_message(cell)
                    await worktree_mgr.checkpoint(cell, message=msg)
                    state._emit_agent(cell)
                    state._db_save_agent(cell)

            elif cmd == "worktree_history":
                cell = state.agents.get(data.get("id", ""))
                commits = []
                if cell and cell.worktree_path:
                    commits = await worktree_mgr.list_checkpoints(cell)
                return {
                    "type": "worktree_history",
                    "id": data.get("id", ""),
                    "branch": cell.worktree_branch if cell else "",
                    "base_branch": cell.worktree_base_branch
                    if cell else "",
                    "commits": commits,
                }

            elif cmd == "worktree_diff_full":
                cell = state.agents.get(data.get("id", ""))
                result = await _worktree_full_diff(cell, worktree_mgr)
                if cell and cell.worktree_path:
                    boundary_state = await _latest_boundary_state_for_cell(
                        cell
                    )
                    result["boundary"] = boundary_state.get("latest")
                    result["clean_boundary"] = boundary_state.get("clean")
                result["type"] = "worktree_diff_full"
                result["id"] = data.get("id", "")
                return result

            elif cmd == "worktree_check_merge":
                cell = state.agents.get(data.get("id", ""))
                aid = data.get("id", "")
                if cell and cell.worktree_path \
                        and cell.worktree_branch:
                    boundary_state = await _latest_boundary_state_for_cell(
                        cell
                    )
                    dirty = await worktree_mgr.has_uncommitted_changes(
                        cell)
                    if dirty:
                        result = {
                            "type": "worktree_check_merge",
                            "id": aid, "clean": False,
                            "dirty": True, "conflicts": [],
                            "boundary": boundary_state.get("latest"),
                            "clean_boundary": boundary_state.get("clean"),
                        }
                    elif boundary_state.get("latest") \
                            and not boundary_state.get("clean"):
                        result = {
                            "type": "worktree_check_merge",
                            "id": aid,
                            "clean": False,
                            "dirty": False,
                            "conflicts": [],
                            "boundary": boundary_state.get("latest"),
                            "clean_boundary": None,
                            "error": _boundary_reason_message(
                                boundary_state.get("reason", ""),
                                boundary_state.get("latest"),
                            ),
                        }
                    else:
                        check = await \
                            worktree_mgr.check_merge_conflicts(cell)
                        check["type"] = "worktree_check_merge"
                        check["id"] = aid
                        check["boundary"] = boundary_state.get("latest")
                        check["clean_boundary"] = boundary_state.get("clean")
                        if check.get("clean"):
                            squash = cell.worktree_merge_squash
                            check["default_message"] = \
                                await _generate_merge_message(
                                    cell, worktree_mgr, squash,
                                    state=state)
                        result = check
                else:
                    result = {
                        "type": "worktree_check_merge",
                        "id": data.get("id", ""),
                        "error": "No worktree",
                    }
                return result

            elif cmd == "worktree_rebase":
                cell = state.agents.get(data.get("id", ""))
                aid = data.get("id", "")
                if cell and cell.worktree_path:
                    check = await worktree_mgr.check_merge_conflicts(cell)
                    ok = await worktree_mgr.rebase_onto_base(cell)
                    if ok:
                        cell.worktree_checkpoints = \
                            await worktree_mgr.count_commits(cell)
                        cell.worktree_dirty = False
                        cell.worktree_diff = {}
                        cell.worktree_changed_files = []
                        state._emit_agent(cell)
                        result = {"type": "worktree_rebase",
                                  "id": aid, "ok": True}
                    else:
                        result = {
                            "type": "worktree_rebase",
                            "id": aid, "ok": False,
                            "error": "Rebase failed — conflicts "
                                     "require manual resolution",
                            "conflicts": check.get("conflicts", []),
                        }
                else:
                    result = {"type": "worktree_rebase",
                              "id": aid, "error": "No worktree"}

            elif cmd == "worktree_rollback":
                cell = state.agents.get(data.get("id", ""))
                sha = data.get("sha", "")
                if cell and cell.worktree_path and sha:
                    await worktree_mgr.rollback(cell, sha)
                    state._emit_agent(cell)
                    state._db_save_agent(cell)

            elif cmd == "worktree_diff":
                cell = state.agents.get(data.get("id", ""))
                if not cell or not cell.worktree_path:
                    result = {"type": "error",
                              "message": "Agent has no worktree."}
                elif not cell.worktree_base_branch:
                    result = {"type": "error",
                              "message": "No base branch configured."}
                else:
                    import asyncio as _aio
                    stat_only = data.get("stat_only", False)
                    summary_only = data.get("summary_only", False)
                    paths = data.get("paths", [])
                    if summary_only:
                        summary = await worktree_mgr.diff_files_summary(
                            cell,
                            paths=paths,
                        )
                        result = {
                            "type": "ok",
                            "summary": {
                                "agent_name": cell.name,
                                "branch": cell.worktree_branch or "",
                                "base_branch": cell.worktree_base_branch,
                                "path_filters": paths,
                                **summary,
                            },
                        }
                    else:
                        diff_args = [
                            "git", "-C", cell.worktree_path,
                            "diff",
                        ]
                        if stat_only:
                            diff_args.append("--stat")
                        diff_args.append(
                            f"{cell.worktree_base_branch}...HEAD")
                        if paths:
                            diff_args.append("--")
                            diff_args.extend(paths)
                        proc = await _aio.create_subprocess_exec(
                            *diff_args,
                            stdout=_aio.subprocess.PIPE,
                            stderr=_aio.subprocess.PIPE,
                        )
                        stdout, stderr = await proc.communicate()
                        if proc.returncode != 0:
                            result = {"type": "error",
                                      "message": stderr.decode().strip()
                                      or "git diff failed"}
                        else:
                            diff_text = stdout.decode()
                            # Truncate if too large (100K chars)
                            if len(diff_text) > 100_000:
                                diff_text = (
                                    diff_text[:100_000]
                                    + "\n\n... truncated (too large) ..."
                                )
                            result = {"type": "ok",
                                      "diff": diff_text}

            elif cmd == "worktree_check_conflicts":
                cell = state.agents.get(data.get("id", ""))
                if not cell or not cell.worktree_path:
                    result = {"type": "error",
                              "message": "Agent has no worktree."}
                else:
                    boundary_state = await _latest_boundary_state_for_cell(
                        cell
                    )
                    if boundary_state.get("latest") \
                            and not boundary_state.get("clean"):
                        result = {
                            "type": "ok",
                            "clean": False,
                            "conflicts": [],
                            "error": _boundary_reason_message(
                                boundary_state.get("reason", ""),
                                boundary_state.get("latest"),
                            ),
                            "boundary": boundary_state.get("latest"),
                        }
                    else:
                        conflict_info = \
                            await worktree_mgr.check_merge_conflicts(cell)
                        result = {
                            "type": "ok",
                            "clean": conflict_info.get("clean", True),
                            "conflicts": conflict_info.get(
                                "conflicts", []),
                            "boundary": boundary_state.get("latest"),
                            "clean_boundary": boundary_state.get("clean"),
                        }

            elif cmd == "worktree_create_pr":
                cell = state.agents.get(data.get("id", ""))
                if not cell or not cell.worktree_path:
                    result = {"type": "worktree_pr",
                              "error": "Agent has no worktree."}
                else:
                    # Build PR title from linked task or agent name
                    title = data.get("title", "")
                    if not title:
                        for t in state.board_tasks.values():
                            if t.agent_id == cell.id:
                                title = t.task
                                break
                    if not title:
                        title = cell.name
                    body = data.get("body", "")
                    pr_result = await worktree_mgr.create_pr(
                        cell, title=title, body=body)
                    if "error" in pr_result:
                        result = {"type": "worktree_pr",
                                  "error": pr_result["error"]}
                    else:
                        msg = ("PR already exists"
                               if pr_result.get("existing")
                               else "PR created")
                        result = {"type": "worktree_pr",
                                  "url": pr_result["url"],
                                  "message": msg}

            elif cmd == "worktree_merge":
                cell = state.agents.get(data.get("id", ""))
                aid = data.get("id", "")
                if cell and cell.worktree_path and cell.worktree_branch:
                    boundary_state = await _latest_boundary_state_for_cell(
                        cell
                    )
                    # Block merge if worktree has uncommitted changes
                    dirty = await worktree_mgr.has_uncommitted_changes(
                        cell)
                    if dirty:
                        result = {
                            "type": "worktree_merge", "id": aid,
                            "ok": False,
                            "error": "Commit or checkpoint changes "
                                     "before merging.",
                        }
                    elif boundary_state.get("latest") \
                            and not boundary_state.get("clean"):
                        result = {
                            "type": "worktree_merge",
                            "id": aid,
                            "ok": False,
                            "error": _boundary_reason_message(
                                boundary_state.get("reason", ""),
                                boundary_state.get("latest"),
                            ),
                        }
                    else:
                        squash = cell.worktree_merge_squash
                        msg = data.get("message", "").strip()
                        if not msg:
                            msg = await _generate_merge_message(
                                cell, worktree_mgr, squash,
                                state=state)
                        merge_result = \
                            await worktree_mgr.server_merge(
                                cell, msg, squash=squash)
                        if merge_result["ok"]:
                            _mark_branch_boundaries_merged(
                                cell, merge_result["sha"]
                            )
                            state.cleanup_stale_boundary_successors()
                            cell.worktree_checkpoints = 0
                            cell.worktree_merged = True
                            cell.worktree_changed_files = []
                            state.history_update_agent(
                                cell, status="merged")
                            state._emit_agent(cell)
                            await _broadcast_toast(
                                f'"{cell.name}" merged to '
                                f"{cell.worktree_base_branch}",
                                "success")
                            # Unlink completed/archive-closed tasks from this agent so
                            # they don't re-appear in future merge
                            # messages.  Tasks stay on the board as a
                            # historical record.
                            for t in list(
                                    state.board_tasks.values()):
                                if t.agent_id == cell.id \
                                        and task_is_closed(t):
                                    t.agent_id = ""
                                    state._emit(
                                        "task_upsert", **asdict(t))
                                    state._db_save_task(t)

                            legacy_close_flag = bool(
                                data.get("close_on_merge"))
                            explicit_close = (
                                "close_agent_on_merge" in data
                            )
                            explicit_remove = (
                                "remove_worktree_on_merge" in data
                            )
                            if explicit_close or explicit_remove:
                                close_flag = bool(
                                    data.get("close_agent_on_merge"))
                                remove_flag = bool(
                                    data.get("remove_worktree_on_merge"))
                            elif legacy_close_flag:
                                close_flag = True
                                remove_flag = True
                            else:
                                close_flag, remove_flag = (
                                    merge_cleanup_flags(
                                        state.get_group_settings(
                                            cell.group
                                        ).worktree_merge_cleanup
                                    )
                                )
                            queued_followups = [
                                t for t in state.board_tasks.values()
                                if t.agent_id == cell.id
                                and t.lane in {"Backlog", "To Do"}
                            ]
                            if queued_followups:
                                close_flag = False
                                remove_flag = False
                            clear_flag = bool(
                                data.get("clear_context"))
                            if queued_followups or close_flag \
                                    or remove_flag:
                                clear_flag = False
                            cleanup = {
                                "close_agent": close_flag,
                                "remove_worktree": remove_flag,
                                "agent_closed": False,
                                "worktree_removed": False,
                                "errors": [],
                            }
                            if clear_flag and not close_flag \
                                    and not remove_flag \
                                    and cell.session_id:
                                await bridge.send_text(
                                    cell.session_id, "/clear\r")
                                cell.tasks_dispatched = 0
                                state._emit_agent(cell)
                                state._db_save_agent(cell)
                                log.info(
                                    "Cleared context for '%s' "
                                    "after merge", cell.name)
                            if close_flag or remove_flag:
                                cleanup = await _cleanup_after_merge(
                                    cell,
                                    close_agent=close_flag,
                                    remove_worktree=remove_flag,
                                )
                            elif cell.worktree_path:
                                # Reset worktree branch to base tip
                                # so new work starts fresh (avoids
                                # re-merging already-merged commits)
                                valid = await \
                                    worktree_mgr.validate(cell)
                                if valid:
                                    ok = await worktree_mgr\
                                        .reset_to_base(cell)
                                    if ok:
                                        cell.worktree_checkpoints =\
                                            await worktree_mgr\
                                            .count_commits(cell)
                                        cell.worktree_dirty = False
                                        cell.worktree_diff = {}
                                        if queued_followups:
                                            cell.worktree_merged = False
                                        state._emit_agent(cell)
                                    else:
                                        log.warning(
                                            "Post-merge reset "
                                            "failed for '%s'",
                                            cell.name)
                            result = {
                                "type": "worktree_merge",
                                "id": aid, "ok": True,
                                "sha": merge_result["sha"],
                                "cleanup": cleanup,
                            }
                        else:
                            result = {
                                "type": "worktree_merge",
                                "id": aid, "ok": False,
                                "error": merge_result.get(
                                    "error", "Merge failed"),
                            }
                else:
                    result = {
                        "type": "worktree_merge", "id": aid,
                        "ok": False,
                        "error": "Agent has no worktree.",
                    }

            # -- Board commands (Phase 5) --
            elif cmd == "board_add_task":
                # Apply per-group board defaults for fields not
                # explicitly provided by the client
                group = data.get("group", "")
                gs = state.get_group_settings(group)
                lane = data.get("lane", "") or gs.board_default_lane
                action_name = data.get("action_name", "") or \
                    gs.board_default_action
                labels = data.get("labels", [])
                if not labels and gs.board_default_labels:
                    labels = list(gs.board_default_labels)
                ext_link = normalize_external_link(
                    data.get("provider", ""),
                    data.get("external_id", ""),
                    data.get("external_url", ""),
                )
                add_kwargs = dict(
                    task=data.get("task", ""),
                    group=group,
                    lane=lane,
                    description=data.get("description", ""),
                    action_name=action_name,
                    action_vars=data.get("action_vars", {}),
                    agent_template=data.get("agent_template", ""),
                    agent_id=data.get("agent_id", ""),
                    labels=labels,
                    provider=ext_link["provider"],
                    external_id=ext_link["external_id"],
                    external_url=ext_link["external_url"],
                    depends_on=data.get("depends_on", []),
                    scheduled_at=data.get("scheduled_at", ""),
                    verification_mode=data.get("verification_mode", ""),
                    verification_state=data.get("verification_state", ""),
                    verification_notes=data.get("verification_notes", ""),
                    verification_updated_at=data.get(
                        "verification_updated_at", ""),
                    verification_updated_by=data.get(
                        "verification_updated_by", ""),
                    verification_summary=data.get(
                        "verification_summary", {}),
                )
                # Pass client-provided ID (for pre-uploaded attachments)
                draft_upload_id = ""
                incoming_id = str(data.get("id", "") or "").strip()
                if incoming_id:
                    if is_draft_task_token(incoming_id) or (
                        not is_canonical_task_id(incoming_id)
                    ):
                        draft_upload_id = incoming_id
                    else:
                        add_kwargs["id"] = incoming_id
                # Attachments from client (already uploaded to disk)
                if data.get("attachments"):
                    add_kwargs["attachments"] = data["attachments"]
                if data.get("artifacts"):
                    add_kwargs["artifacts"] = data["artifacts"]
                bt = state.board_add_task(**add_kwargs)
                if not bt:
                    result = {"type": "error",
                              "message": "Invalid lane, group, or empty task"}
                else:
                    if draft_upload_id:
                        attachments, artifacts = finalize_task_attachments(
                            bt.attachments,
                            bt.artifacts,
                            draft_task_id=draft_upload_id,
                            task_id=bt.id,
                        )
                        state.board_update_task(
                            bt.id,
                            attachments=attachments,
                            artifacts=artifacts,
                        )
                        bt = state.board_tasks.get(bt.id, bt)
                    result = {
                        "type": "external_imported" if bt.external_id
                        or bt.external_url else "board_task_added",
                        "task_id": bt.id,
                        "title": bt.task,
                    }

            elif cmd == "board_archive_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                changed = state.board_archive_task(
                    tid,
                    include_descendants=bool(
                        data.get("include_descendants", True)
                    ),
                )
                if not changed:
                    result = {"type": "error", "message": "Task not found"}
                else:
                    result = {
                        "type": "board_task_archived",
                        "task_id": tid,
                        "changed_ids": changed,
                    }

            elif cmd == "board_unarchive_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                changed = state.board_unarchive_task(
                    tid,
                    lane=data.get("lane", ""),
                    include_descendants=bool(
                        data.get("include_descendants", True)
                    ),
                )
                if not changed:
                    result = {"type": "error", "message": "Task not found"}
                else:
                    result = {
                        "type": "board_task_unarchived",
                        "task_id": tid,
                        "changed_ids": changed,
                    }

            elif cmd == "board_update_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                fields = {k: v for k, v in data.items()
                          if k not in ("cmd", "id")}
                if {"provider", "external_id", "external_url"} & set(fields):
                    link = normalize_external_link(
                        fields.get("provider", ""),
                        fields.get("external_id", ""),
                        fields.get("external_url", ""),
                    )
                    fields["provider"] = link["provider"]
                    fields["external_id"] = link["external_id"]
                    fields["external_url"] = link["external_url"]
                state.board_update_task(tid, **fields)
                # Auto-dispatch if agent_id was set and agent is idle
                _new_aid = fields.get("agent_id", "")
                if _new_aid:
                    _tsk = state.board_tasks.get(tid)
                    _cell = state.agents.get(_new_aid)
                    if (_tsk and _cell
                            and _tsk.lane == "To Do"
                            and not state.agent_is_busy(_new_aid)
                            and _cell.cell_type == "agent"
                            and state.board_deps_met(_tsk)):
                        await handle_command({
                            "cmd": "dispatch_task",
                            "id": tid, "agent_id": _new_aid})

            elif cmd == "board_verify_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error", "message": "Task not found"}
                else:
                    actor_name = str(
                        data.get("actor_name", "") or "loom"
                    ).strip()

                    def _save_verified_task(current_task):
                        current_task.updated_at = datetime.now(
                            timezone.utc
                        ).isoformat()
                        state._emit("task_upsert", **asdict(current_task))
                        state._db_save_task(current_task)

                    root_task = None
                    root_id = task.pipeline_root_id or ""
                    if root_id and root_id != task.id:
                        root_task = state.board_tasks.get(root_id)
                    payload = {}
                    for key in (
                        "verification_mode",
                        "verification_state",
                        "verification_notes",
                        "tests_run",
                        "manual_smoke_done",
                        "deploy_needed",
                        "deploy_attempted",
                        "human_validation_pending",
                        "smoke_status",
                    ):
                        if key in data:
                            payload[key] = data[key]
                    verify_msg, _updated_root = _apply_verification_report(
                        task,
                        payload,
                        actor_name,
                        _save_verified_task,
                        root_task=root_task,
                    )
                    _panel_event(
                        "task_verification_updated",
                        "",
                        actor_name,
                        task.group,
                        verify_msg,
                        task_id=task.id,
                    )
                    state.recompute_task_health()
                    result = {
                        "type": "verification_updated",
                        "task_id": task.id,
                        "message": verify_msg,
                    }

            elif cmd == "external_import_task":
                group = data.get("group", "")
                lane = data.get("lane", "") or "Backlog"
                labels = data.get("labels", [])
                try:
                    imported = import_external_ticket(
                        data.get("ref", ""),
                        provider=data.get("provider", ""),
                        title=data.get("title", ""),
                        description=data.get("description", ""),
                        external_id=data.get("external_id", ""),
                        external_url=data.get("external_url", ""),
                    )
                    bt = state.board_add_task(
                        task=imported.title,
                        group=group,
                        lane=lane,
                        description=imported.description,
                        labels=labels,
                        provider=imported.provider,
                        external_id=imported.external_id,
                        external_url=imported.external_url,
                    )
                    if not bt:
                        result = {"type": "error",
                                  "message": "Invalid group, lane, or task"}
                    else:
                        result = {
                            "type": "external_imported",
                            "task_id": bt.id,
                            "title": bt.task,
                            "provider": bt.provider,
                            "external_id": bt.external_id,
                            "external_url": bt.external_url,
                        }
                except ExternalTicketError as exc:
                    result = {"type": "error", "message": str(exc)}

            elif cmd == "external_link_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                else:
                    link = normalize_external_link(
                        data.get("provider", ""),
                        data.get("external_id", ""),
                        data.get("external_url", ""),
                        ref=data.get("ref", ""),
                    )
                    state.board_update_task(
                        tid,
                        provider=link["provider"],
                        external_id=link["external_id"],
                        external_url=link["external_url"],
                    )
                    result = {
                        "type": "external_unlinked"
                        if not link["provider"]
                        and not link["external_id"]
                        and not link["external_url"]
                        else "external_linked",
                        "task_id": tid,
                        **link,
                    }

            elif cmd == "external_open_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                else:
                    try:
                        url = open_ticket_url(
                            task.provider,
                            task.external_id,
                            task.external_url,
                        )
                        result = {
                            "type": "external_open",
                            "task_id": tid,
                            "url": url,
                        }
                    except ExternalTicketError as exc:
                        result = {"type": "error", "message": str(exc)}

            elif cmd == "external_push_task_status":
                tid = _resolve_task_id(state, data.get("id", ""))
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                else:
                    try:
                        pushed = push_ticket_status(
                            task,
                            status=data.get("status", "") or task.status
                            or task.lane,
                            note=data.get("note", ""),
                        )
                        task.messages.append({
                            "timestamp": time.time(),
                            "action": "external_status",
                            "message": pushed,
                            "agent_name": "loom",
                        })
                        state.board_update_task(tid, messages=task.messages)
                        result = {
                            "type": "external_status_pushed",
                            "task_id": tid,
                            "message": pushed,
                        }
                    except ExternalTicketError as exc:
                        result = {"type": "error", "message": str(exc)}

            elif cmd == "external_post_task_comment":
                tid = _resolve_task_id(state, data.get("id", ""))
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                else:
                    try:
                        comment = (data.get("comment", "") or "").strip()
                        if not comment:
                            comment = build_completion_comment(
                                task.task,
                                data.get("summary", ""),
                            )
                        posted = post_ticket_comment(task, comment=comment)
                        task.messages.append({
                            "timestamp": time.time(),
                            "action": "external_comment",
                            "message": posted,
                            "agent_name": "loom",
                        })
                        state.board_update_task(tid, messages=task.messages)
                        result = {
                            "type": "external_comment_posted",
                            "task_id": tid,
                            "message": posted,
                        }
                    except ExternalTicketError as exc:
                        result = {"type": "error", "message": str(exc)}

            elif cmd == "board_remove_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                state.board_remove_task(tid)
                # Clean up attachment files
                att_dir = ATTACHMENTS_DIR / tid
                if att_dir.is_dir():
                    shutil.rmtree(att_dir, ignore_errors=True)

            elif cmd == "board_archive_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                state.board_archive_task(tid)

            elif cmd == "board_unarchive_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                state.board_unarchive_task(
                    tid,
                    lane=data.get("lane", ""),
                    position=data.get("position"),
                )

            elif cmd == "remove_attachment":
                tid = _resolve_task_id(state, data.get("task_id", ""))
                fname = data.get("filename", "")
                task = state.board_tasks.get(tid)
                if fname:
                    fpath = ATTACHMENTS_DIR / tid / fname
                    if fpath.is_file():
                        fpath.unlink()
                if task and fname:
                    task.attachments = [
                        a for a in task.attachments
                        if a.get("filename") != fname]
                    task.artifacts = remove_task_owned_artifacts_by_filename(
                        task.artifacts,
                        fname,
                        task_id=tid,
                    )
                    state.board_update_task(
                        tid,
                        attachments=task.attachments,
                        artifacts=task.artifacts,
                    )

            elif cmd == "task_upload_artifact":
                tid = _resolve_task_id(state, data.get("task_id", ""))
                cell_id = data.get("cell_id", "")
                if not tid and cell_id:
                    current_task = state.agent_current_task(cell_id)
                    if current_task:
                        tid = current_task.id
                task = state.board_tasks.get(tid)
                if not task:
                    result = {
                        "type": "error",
                        "message": (
                            "Task not found"
                            if data.get("task_id")
                            else "No active task available for this agent"
                        ),
                    }
                else:
                    actor = state.agents.get(cell_id) if cell_id else None
                    provenance = {
                        "source": (
                            "weaver"
                            if actor and actor.id == state.get_group_settings(
                                task.group
                            ).weaver_agent_id
                            else "agent"
                        ),
                        "agent_id": actor.id if actor else "",
                        "agent_name": (actor.slug or actor.name) if actor else "",
                    }
                    try:
                        artifact = store_task_upload(
                            task_id=tid,
                            local_path=data.get("local_path", ""),
                            filename=data.get("filename", ""),
                            content_base64=data.get("content_base64", ""),
                            content_text=data.get("content_text", ""),
                            artifact_type=data.get("artifact_type", ""),
                            title=data.get("title", ""),
                            mime_type=data.get("mime_type", ""),
                            summary=data.get("summary", ""),
                            prompt_mode=data.get("prompt_mode", ""),
                            provenance=provenance,
                        )
                    except FileNotFoundError as exc:
                        result = {"type": "error", "message": str(exc)}
                    except ValueError as exc:
                        result = {"type": "error", "message": str(exc)}
                    else:
                        artifacts = normalize_artifacts(task.artifacts or [])
                        artifacts.append(artifact)
                        state.board_update_task(tid, artifacts=artifacts)
                        refreshed = state.board_tasks.get(tid)
                        result = {
                            "type": "task_artifact_uploaded",
                            "task_id": tid,
                            "artifact": serialize_task_artifact(
                                artifact,
                                task_id=tid,
                                task_label=refreshed.task if refreshed else task.task,
                            ),
                        }

            elif cmd == "board_move_task":
                _mv_id = _resolve_task_id(state, data.get("id", ""))
                _mv_task = state.board_tasks.get(_mv_id)
                _mv_done_before = task_counts_as_done(_mv_task)
                _mv_new = data.get("lane", "")
                state.board_move_task(
                    _mv_id, _mv_new, data.get("position"))
                _mv_task_after = state.board_tasks.get(_mv_id)
                # Moving out of Done may re-block dependents
                if _mv_done_before and not task_counts_as_done(_mv_task_after):
                    for _dt in state.board_get_dependents(_mv_id):
                        if not task_is_closed(_dt):
                            _panel_event(
                                "task_blocked_by_dep", "",
                                "", _dt.group,
                                f"Task '{_dt.task[:60]}' is "
                                "blocked again (dependency "
                                "moved out of Done)",
                                task_id=_dt.id)

            elif cmd == "board_reorder_task":
                state.board_reorder_task(
                    data.get("id", ""),
                    data.get("position", 0))

            elif cmd == "dispatch_task":
                tid = _resolve_task_id(state, data.get("id", ""))
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                else:
                    group = task.group
                    if not group or group not in state.groups:
                        # Fall back to first group
                        group = next(iter(state.groups), "")
                    if not group:
                        result = {"type": "error",
                                  "message": "No group available"}
                    elif not state.board_deps_met(task):
                        unmet = [
                            state.board_tasks[d].task[:40]
                            for d in task.depends_on
                            if d in state.board_tasks
                            and not task_counts_as_done(
                                state.board_tasks[d]
                            )]
                        result = {
                            "type": "error",
                            "message":
                                "Blocked by dependencies: "
                                + ", ".join(unmet)}
                    else:
                        cell = None
                        base_dir = await _resolve_base_dir(group)
                        act_meta = action_mgr.load_action(
                            task.action_name, base_dir) \
                            if task.action_name else None
                        action_template = ""
                        if isinstance(act_meta, dict):
                            raw_agent = act_meta.get("agent", "")
                            if isinstance(raw_agent, str):
                                action_template = raw_agent
                        explicit_template = task.agent_template or action_template
                        agent_id = data.get("agent_id", "")
                        handoff_from = data.get(
                            "handoff_worktree_from", "")
                        if agent_id and agent_id not in state.agents:
                            result = {"type": "error",
                                      "message": "Agent not found"}
                        elif agent_id:
                            # Dispatch to existing agent
                            cell = state.agents.get(agent_id)
                            active = state.agent_current_task(cell.id)
                            if active and active.id != tid:
                                allow_self_dispatch = bool(
                                    data.get("_self_dispatch")
                                    and cell.id == agent_id
                                )
                                if _should_queue_existing_agent_dispatch(
                                        active,
                                        target_task_id=tid,
                                        self_dispatch=allow_self_dispatch):
                                    # Agent is busy — queue the task
                                    state.board_update_task(
                                        tid, agent_id=cell.id)
                                    state.board_move_task(tid, "To Do")
                                    _panel_event(
                                        "task_queued", cell.id,
                                        cell.name, cell.group,
                                        task.task[:80],
                                        task_id=tid)
                                    result = {
                                        "type": "queued",
                                        "task_id": tid,
                                        "agent_id": cell.id}
                                    cell = None  # skip dispatch below
                        elif data.get("create_agent"):
                            # Create a new agent
                            from loom.state import _slugify
                            agent_name = data.get("name", "")
                            if not agent_name:
                                slug = _slugify(task.task)
                                agent_name = slug or "agent"
                            launch_overrides = {}
                            agent_type = (data.get("agent_type", "")
                                          or "").strip()
                            if agent_type:
                                launch_overrides["provider"] = agent_type
                            command_override = (data.get("command", "")
                                                or "").strip()
                            if command_override:
                                launch_overrides["command"] = (
                                    command_override)
                            launch_cfg = _resolve_agent_launch_config(
                                group,
                                base_dir=base_dir,
                                explicit_template=explicit_template,
                                overrides=launch_overrides,
                            )
                            persistent_prompt_text = ""
                            startup_prompt = ""
                            if launch_cfg.get("agent_type"):
                                persistent_prompt_text = \
                                    _build_dispatch_persistent_prompt(
                                        launch_cfg.get("system_prompt", ""))
                                startup_prompt = _startup_prompt_for_new_agent(
                                    agent_type=launch_cfg.get(
                                        "agent_type", ""),
                                    persistent_prompt_text=
                                    persistent_prompt_text,
                                )
                            cell = await _create_agent_with_config(
                                group, agent_name, launch_cfg,
                                explicit_template=explicit_template,
                                target_session_id=data.get(
                                    "target_session_id", ""),
                                target_window_id=data.get(
                                    "target_window_id", ""),
                                persistent_prompt_text=persistent_prompt_text,
                            )
                            if cell:
                                # Worktree inheritance (pipeline)
                                inherit_from = data.get(
                                    "inherit_worktree_from", "")
                                if inherit_from:
                                    src = state.agents.get(inherit_from)
                                    if src and src.worktree_path:
                                        cell.worktree_path = \
                                            src.worktree_path
                                        cell.worktree_branch = \
                                            src.worktree_branch
                                        cell.worktree_repo_root = \
                                            src.worktree_repo_root
                                        cell.worktree_base_branch = \
                                            src.worktree_base_branch
                                        cell.worktree_changed_files = list(
                                            src.worktree_changed_files
                                            or [])
                                        cell.directory = \
                                            src.worktree_path
                                        state._emit_agent(cell)
                                        state._db_save_agent(cell)
                                elif not cell.worktree_path \
                                        and task.parent_task_id:
                                    # HITL dispatch: walk parent
                                    # chain to find worktree
                                    _ptid = task.parent_task_id
                                    while _ptid:
                                        _pt = state.board_tasks.get(
                                            _ptid)
                                        if not _pt:
                                            break
                                        if _pt.agent_id:
                                            _pa = state.agents.get(
                                                _pt.agent_id)
                                            if _pa and \
                                                    _pa.worktree_path:
                                                cell.worktree_path = \
                                                    _pa.worktree_path
                                                cell.worktree_branch =\
                                                    _pa.worktree_branch
                                                cell.worktree_repo_root = \
                                                    _pa.worktree_repo_root
                                                cell.worktree_base_branch = \
                                                    _pa.worktree_base_branch
                                                cell.worktree_changed_files = list(
                                                    _pa.worktree_changed_files
                                                    or [])
                                                cell.directory = \
                                                    _pa.worktree_path
                                                state._emit_agent(cell)
                                                state._db_save_agent(
                                                    cell)
                                                break
                                        _ptid = _pt.parent_task_id

                                if launch_cfg.get("terminals"):
                                    await _create_child_terminals(
                                        group, cell,
                                        terminals=launch_cfg["terminals"])

                                # Auto-create child terminals (off by default for dispatch)
                                gs = state.get_group_settings(group)
                                if gs.dispatch_auto_terminals \
                                        and gs.auto_terminals > 0:
                                    await _create_child_terminals(
                                        group, cell, count=gs.auto_terminals)

                        if cell:
                            # Link task to agent and move to In Progress
                            owner = _find_active_worktree_owner(state, cell)
                            if owner:
                                if _should_handoff_shared_worktree(
                                        owner,
                                        target_agent_id=cell.id,
                                        handoff_from=handoff_from):
                                    owner.current_task_id = ""
                                    owner.activity = ""
                                    owner.activity_detail = ""
                                    state._emit_agent(owner)
                                    state._db_save_agent(owner)
                                else:
                                    state.board_update_task(
                                        tid, agent_id=cell.id)
                                    state.board_move_task(tid, "To Do")
                                    _panel_event(
                                        "task_queued", cell.id,
                                        cell.name, cell.group,
                                        f"{task.task[:80]} "
                                        f"(waiting for {owner.name})",
                                        task_id=tid)
                                    result = {
                                        "type": "queued",
                                        "task_id": tid,
                                        "agent_id": cell.id,
                                        "reason":
                                            "shared_worktree_busy",
                                        "blocked_by_agent_id":
                                            owner.id,
                                        "blocked_by_task_id":
                                            (
                                                state.agent_current_task(
                                                    owner.id
                                                ).id
                                                if state.agent_current_task(
                                                    owner.id
                                                ) else ""
                                            ),
                                    }
                                    cell = None
                        if cell:
                            # Link task to agent and move to In Progress
                            dispatch_lane = \
                                state.get_group_settings(group) \
                                    .dispatch_lane or "In Progress"
                            _record_task_dispatch(
                                cell, task, dispatch_lane)

                            final_prompt = ""
                            shared_context_block = build_prompt_memory_block(
                                state.db,
                                cell=cell,
                                task=task,
                            )
                            if data.get("_self_dispatch"):
                                final_prompt = _build_self_dispatch_prompt(
                                    shared_context_block,
                                )
                            else:
                                # Build loom context for template rendering
                                loom_ctx = _build_loom_context(cell, task)
                                # Compose prompt: action-aware
                                prompt = None
                                base_dir = ""
                                if task.action_name \
                                        and not data.get("force_no_action"):
                                    base_dir = cell.worktree_repo_root \
                                        or cell.directory \
                                        or await _resolve_base_dir(group)
                                    tvars = {"TASK": task.task,
                                             **(task.action_vars or {})}
                                    rendered = action_mgr.render_prompt(
                                        task.action_name, tvars,
                                        base_dir=base_dir,
                                        loom_context=loom_ctx)
                                    if rendered is None:
                                        # Action deleted — warn frontend
                                        result = {
                                            "type":
                                                "dispatch_action_missing",
                                            "task_id": tid,
                                            "action_name":
                                                task.action_name}
                                        prompt = None
                                    else:
                                        prompt = rendered
                                elif task.instructions or task.context \
                                        or task.criteria:
                                    # Legacy fallback for old tasks
                                    parts = []
                                    if task.task:
                                        parts.append(task.task)
                                    if task.instructions:
                                        parts.append(task.instructions)
                                    if task.context:
                                        parts.append(task.context)
                                    if task.criteria:
                                        parts.append(task.criteria)
                                    prompt = "\n\n".join(parts)
                                else:
                                    prompt = task.task

                                if prompt:
                                    prompt = _append_task_artifacts(
                                        prompt,
                                        task.attachments,
                                        task.artifacts,
                                    )
                                    is_clean = \
                                        loom_ctx["context"]["is_clean"]
                                    final_prompt = prompt
                                    final_prompt += shared_context_block
                                    final_prompt += _build_postscript(
                                        task, action_mgr,
                                        base_dir if task.action_name
                                        else "",
                                        is_clean=is_clean,
                                        cell=cell)

                            if final_prompt:
                                # Track dispatch count
                                cell.tasks_dispatched += 1
                                state._emit_agent(cell)
                                state._db_save_agent(cell)
                                _panel_event(
                                    "task_dispatched", cell.id,
                                    cell.name, cell.group,
                                    task.task[:80],
                                    task_id=task.id)

                                if agent_id and cell.session_id:
                                    delay = 3 if data.get(
                                        "_self_dispatch") else 0
                                    if delay:
                                        # Self-dispatch: delay so
                                        # prompt arrives after current
                                        # agent turn finishes
                                        await _send_agent_prompt(
                                            cell, final_prompt,
                                            delay=delay,
                                            background=True)
                                    else:
                                        # Existing agent — send now
                                        await _send_agent_prompt(cell, final_prompt)
                                elif data.get("create_agent") \
                                        and cell.session_id:
                                    for prompt_text, send_kwargs in \
                                            _new_agent_prompt_sequence(
                                                launch_cfg,
                                                startup_prompt=
                                                startup_prompt,
                                                final_prompt=final_prompt):
                                        await _send_agent_prompt(
                                            cell,
                                            prompt_text,
                                            **send_kwargs)

            elif cmd == "resolve_ask":
                # Resolve an ask task: send answer to parent's agent
                tid = data.get("id", "")
                answer = data.get("answer", "")
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                elif "loom:human" not in (task.labels or []):
                    result = {"type": "error",
                              "message": "Not an ask task"}
                elif not answer.strip():
                    result = {"type": "error",
                              "message": "Answer is required"}
                elif not task.parent_task_id:
                    result = {"type": "error",
                              "message": "Ask task has no parent"}
                else:
                    parent = state.board_tasks.get(
                        task.parent_task_id)
                    if not parent:
                        result = {"type": "error",
                                  "message": "Parent task not found"}
                    elif not parent.agent_id:
                        result = {"type": "error",
                                  "message": "Parent task has no "
                                             "linked agent"}
                    else:
                        agent = state.agents.get(parent.agent_id)
                        if not agent or not agent.session_id:
                            result = {
                                "type": "error",
                                "message": "Parent agent not "
                                           "available"}
                        else:
                            # Send answer to the parent's agent
                            q = task.task
                            if len(q) > 120:
                                q = q[:120] + "…"
                            msg = (f"Answer to your question "
                                   f"\"{q}\":\n{answer}")
                            await bridge.send_text(
                                agent.session_id,
                                msg + "\r")
                            agent.status = "running"
                            state._emit_agent(agent)

                            # Move ask task to Done (no cascade)
                            from datetime import datetime, timezone
                            if not task_is_closed(task):
                                state.board_move_task(
                                    task.id, "Done")
                            task.status = ""
                            task.updated_at = datetime.now(
                                timezone.utc).isoformat()
                            state._emit("task_upsert",
                                        **asdict(task))
                            state._db_save_task(task)

                            # Clear parent "Awaiting Input" status
                            parent.status = ""
                            parent.updated_at = datetime.now(
                                timezone.utc).isoformat()
                            state._emit("task_upsert",
                                        **asdict(parent))
                            state._db_save_task(parent)

                            # Clear root task status too
                            root_id = parent.pipeline_root_id \
                                or parent.id
                            if root_id != parent.id:
                                root = state.board_tasks.get(
                                    root_id)
                                if root and root.status:
                                    root.status = ""
                                    root.updated_at = datetime.now(
                                        timezone.utc).isoformat()
                                    state._emit("task_upsert",
                                                **asdict(root))
                                    state._db_save_task(root)

                            _panel_event(
                                "ask_resolved", agent.id,
                                agent.name, agent.group,
                                "Resolved: " + q,
                                task_id=tid)
                            result = {"type": "ok",
                                      "task_id": tid}

            elif cmd == "preview_prompt":
                # Preview rendered prompt for a task or inline params
                tid = data.get("id", "")
                act_name = data.get("action_name", "")
                task_text = data.get("task", "")
                avars = data.get("action_vars", {})
                act_group = data.get("group", "")
                attachments = data.get("attachments", [])
                artifacts = normalize_artifacts(data.get("artifacts", []))

                if tid and not task_text:
                    t = state.board_tasks.get(tid)
                    if t:
                        task_text = t.task
                        act_name = act_name or t.action_name
                        avars = avars or t.action_vars or {}
                        act_group = act_group or t.group
                        attachments = t.attachments or []
                        artifacts = normalize_artifacts(t.artifacts or [])

                task_desc = data.get("description", "")
                if tid and not task_desc:
                    t = state.board_tasks.get(tid)
                    if t:
                        task_desc = t.description or ""

                if act_name:
                    base_dir = await _resolve_base_dir(act_group)
                    loom_ctx = {
                        **LOOM_CONTEXT_STUB,
                        "task": {
                            **LOOM_CONTEXT_STUB["task"],
                            "title": task_text,
                            "description": task_desc,
                            "group": act_group,
                            "attachments": [
                                {"path": a.get("path", ""),
                                 "filename": a.get("filename", "")}
                                for a in (attachments or [])
                            ],
                            "artifacts": task_artifacts(
                                attachments or [],
                                artifacts or [],
                            ),
                        },
                    }
                    rendered = action_mgr.render_prompt(
                        act_name,
                        {"TASK": task_text, **avars},
                        base_dir=base_dir,
                        loom_context=loom_ctx)
                    if rendered is None:
                        result = {"type": "prompt_preview",
                                  "prompt": task_text,
                                  "warning": f"Action "
                                             f"\"{act_name}\" not found"}
                    else:
                        result = {"type": "prompt_preview",
                                  "prompt": _append_task_artifacts(
                                      rendered,
                                      attachments,
                                      artifacts,
                                  )}
                else:
                    result = {"type": "prompt_preview",
                              "prompt": _append_task_artifacts(
                                  task_text,
                                  attachments,
                                  artifacts,
                              )}

            elif cmd == "memory_list":
                if not state.db:
                    result = {
                        "type": "error",
                        "message": "Memory storage is unavailable",
                    }
                else:
                    cell, task = _resolve_memory_cell_and_task(
                        state,
                        data.get("cell_id", ""),
                        data.get("task_id", ""),
                    )
                    scope_kind = (data.get("scope_kind", "") or "").strip()
                    try:
                        scope_ref = _resolve_memory_scope_ref(
                            scope_kind,
                            (data.get("scope_ref", "") or "").strip(),
                            cell=cell,
                            task=task,
                        )
                    except ValueError as exc:
                        result = {
                            "type": "error",
                            "message": str(exc),
                        }
                        scope_ref = ""
                    group_name = (data.get("group_name", "") or "").strip()
                    project_key = (data.get("project_key", "") or "").strip()
                    linked_target_kind = (
                        data.get("linked_target_kind", "") or ""
                    ).strip()
                    linked_target_ref = (
                        data.get("linked_target_ref", "") or ""
                    ).strip()
                    if linked_target_kind:
                        try:
                            linked_target_kind = normalize_link_target_kind(
                                linked_target_kind
                            )
                        except ValueError as exc:
                            result = {
                                "type": "error",
                                "message": str(exc),
                            }
                            linked_target_kind = ""
                    if result is None and linked_target_kind:
                        try:
                            linked_target_ref = _resolve_memory_link_ref(
                                linked_target_kind,
                                linked_target_ref,
                                cell=cell,
                                task=task,
                            )
                        except ValueError as exc:
                            result = {
                                "type": "error",
                                "message": str(exc),
                            }
                    if result is None:
                        if not scope_kind and not scope_ref and not group_name:
                            if task:
                                scope_kind = "task"
                                scope_ref = task.id
                            elif cell and cell.group:
                                scope_kind = "group"
                                scope_ref = cell.group
                                group_name = cell.group
                        if not group_name:
                            if task and task.group:
                                group_name = task.group
                            elif scope_kind == "group" and scope_ref:
                                group_name = scope_ref
                            elif cell and cell.group and not project_key:
                                group_name = cell.group
                        search_text = (data.get("search", "") or "").strip()
                        entries = load_visible_memory_entries(
                            state.db,
                            group_name=group_name,
                            project_key=project_key,
                            scope_kind=scope_kind,
                            scope_ref=scope_ref,
                            entry_type=(data.get("entry_type", "") or "").strip(),
                            task_id=_resolve_task_id(
                                state,
                                (data.get("filter_task_id", "") or "").strip()
                            ),
                            pinned_only=bool(data.get("pinned_only", False)),
                            search=search_text,
                            linked_target_kind=linked_target_kind,
                            linked_target_ref=linked_target_ref,
                            limit=int(data.get("limit", 20) or 20),
                            offset=int(data.get("offset", 0) or 0),
                            compact=not bool(search_text),
                        )
                        result = {
                            "type": "memory_entries",
                            "entries": entries,
                            "scope_kind": scope_kind,
                            "scope_ref": scope_ref,
                            "group_name": group_name,
                            "project_key": project_key,
                            "linked_target_kind": linked_target_kind,
                            "linked_target_ref": linked_target_ref,
                        }

            elif cmd == "memory_read":
                if not state.db:
                    result = {
                        "type": "error",
                        "message": "Memory storage is unavailable",
                    }
                else:
                    entry_id = (data.get("entry_id", "") or "").strip()
                    entry = state.db.load_memory_entry(entry_id)
                    if not entry:
                        result = {
                            "type": "error",
                            "message": "Memory entry not found",
                        }
                    else:
                        result = {"type": "memory_entry", "entry": entry}

            elif cmd == "board_add_lane":
                name = data.get("name", "").strip()
                if not name:
                    result = {"type": "error",
                              "message": "Lane name cannot be empty"}
                else:
                    state.board_add_lane(name, data.get("position"))

            elif cmd == "board_rename_lane":
                state.board_rename_lane(
                    data.get("old_name", ""),
                    data.get("new_name", "").strip())

            elif cmd == "board_remove_lane":
                state.board_remove_lane(
                    data.get("name", ""),
                    data.get("move_tasks_to", ""))

            elif cmd == "board_reorder_lanes":
                state.board_reorder_lanes(data.get("lanes", []))

            elif cmd == "board_set_panel":
                if "active" in data:
                    state.panel_active = str(data["active"])
                    state._emit("ui_update", key="panel_active",
                                value=state.panel_active)
                    state._db_save_ui("panel_active",
                                      state.panel_active)
                elif "open" in data:
                    # Backward compat
                    state.panel_active = "board" if data["open"] \
                        else ""
                    state._emit("ui_update", key="panel_active",
                                value=state.panel_active)
                    state._db_save_ui("panel_active",
                                      state.panel_active)
                if "height" in data:
                    state.board_panel_height = int(data["height"])
                    state._emit("ui_update", key="board_panel_height",
                                value=state.board_panel_height)
                    state._db_save_ui("board_panel_height",
                                      state.board_panel_height)

            elif cmd == "events_dismiss":
                item_id = str(data.get("id", "") or "").strip()
                if not item_id:
                    result = {"type": "error", "message": "Missing event id"}
                else:
                    try:
                        timestamp = float(data.get("timestamp", 0) or 0)
                    except (TypeError, ValueError):
                        timestamp = 0.0
                    if timestamp <= 0:
                        timestamp = time.time()
                    state.events_dismissed_attention[item_id] = timestamp
                    state._emit(
                        "ui_update",
                        key="events_dismissed_attention",
                        value=state.events_dismissed_attention,
                    )
                    state._db_save_ui(
                        "events_dismissed_attention",
                        json.dumps(state.events_dismissed_attention),
                    )

            elif cmd == "board_set_filters":
                raw_filters = data.get("filters_by_group", {})
                if isinstance(raw_filters, dict):
                    state.board_filters_by_group = raw_filters
                else:
                    state.board_filters_by_group = {}
                state._emit("ui_update", key="board_filters_by_group",
                            value=state.board_filters_by_group)
                state._db_save_ui(
                    "board_filters_by_group",
                    json.dumps(state.board_filters_by_group),
                )

            elif cmd == "board_set_saved_views":
                raw_views = data.get("saved_views_by_group", {})
                if isinstance(raw_views, dict):
                    state.board_saved_views_by_group = raw_views
                else:
                    state.board_saved_views_by_group = {}
                state._emit("ui_update", key="board_saved_views_by_group",
                            value=state.board_saved_views_by_group)
                state._db_save_ui(
                    "board_saved_views_by_group",
                    json.dumps(state.board_saved_views_by_group),
                )

            elif cmd == "board_set_lane_sorts":
                raw_sorts = data.get("lane_sorts_by_group", {})
                if isinstance(raw_sorts, dict):
                    state.board_lane_sorts_by_group = raw_sorts
                else:
                    state.board_lane_sorts_by_group = {}
                state._emit("ui_update", key="board_lane_sorts_by_group",
                            value=state.board_lane_sorts_by_group)
                state._db_save_ui(
                    "board_lane_sorts_by_group",
                    json.dumps(state.board_lane_sorts_by_group),
                )

            elif cmd == "board_set_card_density":
                raw_density = data.get("card_density_by_group", {})
                if isinstance(raw_density, dict):
                    state.board_card_density_by_group = raw_density
                else:
                    state.board_card_density_by_group = {}
                state._emit("ui_update", key="board_card_density_by_group",
                            value=state.board_card_density_by_group)
                state._db_save_ui(
                    "board_card_density_by_group",
                    json.dumps(state.board_card_density_by_group),
                )

            # -- Schedule commands ------------------------------------------

            elif cmd == "schedule_create":
                name = data.get("name", "").strip()
                group = data.get("group", "")
                if not name:
                    result = {"type": "error",
                              "message": "Schedule name is required"}
                elif not group or group not in state.groups:
                    result = {"type": "error",
                              "message": "Valid group is required"}
                else:
                    cron_expr = data.get("cron_expr", "")
                    scheduled_at = data.get("scheduled_at", "")
                    tz = data.get("timezone", "")
                    if not cron_expr and not scheduled_at:
                        result = {"type": "error",
                                  "message": "Either cron_expr or "
                                             "scheduled_at is required"}
                    else:
                        if cron_expr:
                            try:
                                from .cron import parse_cron, \
                                    next_run as cron_next
                                parse_cron(cron_expr)
                                from datetime import datetime, \
                                    timezone as dt_tz
                                nxt = cron_next(
                                    cron_expr,
                                    datetime.now(dt_tz.utc), tz=tz)
                                next_run_at = nxt.isoformat()
                            except ValueError as e:
                                result = {
                                    "type": "error",
                                    "message": f"Invalid cron: {e}"}
                                next_run_at = None
                        else:
                            next_run_at = scheduled_at

                        if next_run_at is not None:
                            kwargs = {
                                "task_template":
                                    data.get("task_template", ""),
                                "description":
                                    data.get("description", ""),
                                "action_name":
                                    data.get("action_name", ""),
                                "action_vars":
                                    data.get("action_vars", {}),
                                "agent_template":
                                    data.get("agent_template", ""),
                                "labels": data.get("labels", []),
                                "cron_expr": cron_expr,
                                "scheduled_at": scheduled_at,
                                "timezone": tz,
                                "next_run_at": next_run_at,
                                "enabled":
                                    data.get("enabled", True),
                            }
                            sched = state.schedule_add(
                                name, group, **kwargs)
                            if sched:
                                result = {"type": "ok",
                                          "schedule_id": sched.id}
                            else:
                                result = {"type": "error",
                                          "message":
                                              "Failed to create "
                                              "schedule"}

            elif cmd == "schedule_update":
                sid = data.get("id", "")
                sched = state.schedules.get(sid)
                if not sched:
                    result = {"type": "error",
                              "message": "Schedule not found"}
                else:
                    fields = {}
                    for k in ("name", "task_template", "description",
                              "group", "action_name", "action_vars",
                              "agent_template", "labels", "cron_expr",
                              "scheduled_at", "timezone", "enabled"):
                        if k in data:
                            fields[k] = data[k]
                    new_cron = fields.get("cron_expr", sched.cron_expr)
                    new_at = fields.get("scheduled_at",
                                        sched.scheduled_at)
                    new_tz = fields.get("timezone", sched.timezone)
                    if "cron_expr" in fields or "scheduled_at" in fields \
                            or "timezone" in fields:
                        if new_cron:
                            try:
                                from .cron import parse_cron, \
                                    next_run as cron_next
                                parse_cron(new_cron)
                                from datetime import datetime, \
                                    timezone as dt_tz
                                nxt = cron_next(
                                    new_cron,
                                    datetime.now(dt_tz.utc),
                                    tz=new_tz)
                                fields["next_run_at"] = nxt.isoformat()
                            except ValueError as e:
                                result = {
                                    "type": "error",
                                    "message":
                                        f"Invalid cron: {e}"}
                                fields = None
                        elif new_at:
                            fields["next_run_at"] = new_at
                    if fields is not None:
                        state.schedule_update(sid, **fields)

            elif cmd == "schedule_remove":
                sid = data.get("id", "")
                if sid in state.schedules:
                    state.schedule_remove(sid)
                else:
                    result = {"type": "error",
                              "message": "Schedule not found"}

            elif cmd == "schedule_enable":
                sid = data.get("id", "")
                sched = state.schedules.get(sid)
                if not sched:
                    result = {"type": "error",
                              "message": "Schedule not found"}
                else:
                    fields = {"enabled": True}
                    if sched.cron_expr:
                        from .cron import next_run as cron_next
                        from datetime import datetime, timezone as dt_tz
                        nxt = cron_next(sched.cron_expr,
                                        datetime.now(dt_tz.utc),
                                        tz=sched.timezone)
                        fields["next_run_at"] = nxt.isoformat()
                    elif sched.scheduled_at:
                        fields["next_run_at"] = sched.scheduled_at
                    state.schedule_update(sid, **fields)

            elif cmd == "schedule_disable":
                sid = data.get("id", "")
                if sid in state.schedules:
                    state.schedule_update(sid, enabled=False)
                else:
                    result = {"type": "error",
                              "message": "Schedule not found"}

            elif cmd == "schedule_list":
                result = {
                    "type": "schedule_list",
                    "schedules": [
                        asdict(s) for s in state.schedules.values()
                    ],
                }

            elif cmd == "schedule_run":
                sid = data.get("id", "")
                sched = state.schedules.get(sid)
                if not sched:
                    result = {"type": "error",
                              "message": "Schedule not found"}
                elif sched.group not in state.groups:
                    result = {"type": "error",
                              "message": "Schedule group not found"}
                else:
                    from datetime import datetime, timezone as dt_tz
                    now = datetime.now(dt_tz.utc)
                    title = sched.task_template or sched.name
                    title = (title
                             .replace("{date}",
                                      now.strftime("%Y-%m-%d"))
                             .replace("{time}",
                                      now.strftime("%H:%M"))
                             .replace("{datetime}",
                                      now.strftime("%Y-%m-%d %H:%M")))
                    task = state.board_add_task(
                        task=title, group=sched.group,
                        lane="Backlog",
                        description=sched.description,
                        action_name=sched.action_name,
                        action_vars=dict(sched.action_vars),
                        agent_template=sched.agent_template,
                        labels=list(sched.labels))
                    if task:
                        await handle_command({
                            "cmd": "dispatch_task",
                            "id": task.id,
                            "create_agent": True})
                        sched.last_run_at = now.isoformat()
                        sched.run_count += 1
                        sched.last_task_id = task.id
                        state._emit("schedule_upsert",
                                    **asdict(sched))
                        state._db_save_schedule(sched)
                        _panel_event("schedule_fired", "",
                                     sched.name, sched.group,
                                     title, task_id=task.id)
                        result = {"type": "ok",
                                  "task_id": task.id}

            elif cmd == "ai_report":
                cell_id = data.get("cell_id", "")
                action = data.get("action", "")
                message = data.get("message", "")
                task_id = data.get("task_id", "")

                cell = state.agents.get(cell_id)
                if not cell:
                    result = {"type": "error",
                              "message": f"Cell {cell_id} not found"}
                else:
                    # Resolve task: explicit ID → current_task_id →
                    # auto-detect from cell's active tasks.
                    task = None
                    if task_id:
                        task = state.board_tasks.get(task_id)
                    elif cell.current_task_id:
                        task = state.board_tasks.get(
                            cell.current_task_id)
                    if not task and not task_id:
                        linked = [
                            t for t in state.board_tasks.values()
                            if t.agent_id == cell_id
                            and t.lane not in ("Done", "Backlog", ARCHIVED_LANE)]
                        if len(linked) == 1:
                            task = linked[0]

                    def _add_label(t, label):
                        if label not in t.labels:
                            t.labels.append(label)

                    def _save_task(t):
                        from datetime import datetime, timezone
                        t.updated_at = datetime.now(
                            timezone.utc).isoformat()
                        state._emit("task_upsert", **asdict(t))
                        state._db_save_task(t)

                    def _cascade_done(task_id):
                        """Walk up parent chain, completing ancestors
                        whose children are all Done."""
                        t = state.board_tasks.get(task_id)
                        if not t or not t.parent_task_id:
                            return
                        pid = t.parent_task_id
                        while pid:
                            parent = state.board_tasks.get(pid)
                            if not parent:
                                break
                            if task_is_closed(parent):
                                pid = parent.parent_task_id
                                continue
                            children = state.board_get_children(pid)
                            if all(task_counts_as_done(c) for c in children):
                                parent.status = ""
                                state.board_move_task(pid, "Done")
                                _save_task(parent)
                                pid = parent.parent_task_id
                            else:
                                break

                    def _append_mcp(c, act, msg=""):
                        """Append an MCP message to the cell log."""
                        c.mcp_messages.insert(0, {
                            "action": act,
                            "message": msg,
                            "timestamp": time.time(),
                        })
                        if len(c.mcp_messages) > 20:
                            c.mcp_messages[:] = c.mcp_messages[:20]

                    def _append_task_msg(t, act, msg, agent_name):
                        """Append to the task's persisted activity log."""
                        if t:
                            t.messages.append({
                                "timestamp": time.time(),
                                "action": act,
                                "message": msg,
                                "agent_name": agent_name,
                            })

                    def _record_history_msg(c, act, msg=""):
                        """Persist to agent_messages history table."""
                        state.history_record_message(
                            c.id, act, msg,
                            task_id=task.id if task else "")

                    async def _auto_dispatch_next(c):
                        """Pick the next queued task for this agent."""
                        queued = sorted(
                            [t for t in state.board_tasks.values()
                             if t.agent_id == c.id
                             and t.lane == "To Do"
                             and state.board_deps_met(t)],
                            key=lambda t: t.position)
                        if not queued:
                            return
                        nxt = queued[0]
                        _panel_event(
                            "task_auto_dispatched", c.id,
                            c.name, c.group,
                            nxt.task[:80], task_id=nxt.id)
                        await state.broadcast()
                        await handle_command({
                            "cmd": "dispatch_task",
                            "id": nxt.id,
                            "agent_id": c.id})

                    async def _drain_auto_dispatch_queue(group_name: str):
                        if not group_name:
                            return
                        await _pump_auto_dispatch_queue(
                            state,
                            handle_command,
                            _panel_event,
                            group=group_name,
                        )

                    if result and result.get("type") == "error":
                        pass  # auto-resolve failed; skip action

                    elif action == "done":
                        cell.activity = ""
                        cell.activity_detail = ""
                        cell.needs_attention = False
                        cell.error_message = ""
                        if message:
                            cell.last_summary = message
                        cell.current_task_id = ""
                        _append_mcp(cell, "done", message or "Done")
                        _append_task_msg(task, "done",
                                         message or "Done", cell.name)
                        _record_history_msg(
                            cell, "done", message or "Done")
                        if task:
                            state.history_complete_task(
                                cell.id, task.id, "done")
                        if task:
                            await _record_task_boundary(
                                task, cell, message or "Done"
                            )
                        state._emit_agent(cell)
                        if task and not task_counts_as_done(task):
                            state.board_move_task(task.id, "Done")
                        if task:
                            task.status = ""
                            _save_task(task)
                            if data.get("push_external") \
                                    and (task.provider or task.external_url):
                                try:
                                    posted = post_ticket_comment(
                                        task,
                                        comment=build_completion_comment(
                                            task.task, message),
                                    )
                                    _append_task_msg(
                                        task, "external_comment",
                                        posted, "loom")
                                    _save_task(task)
                                    result = {
                                        "type": "external_comment_posted",
                                        "task_id": task.id,
                                        "message": posted,
                                    }
                                except ExternalTicketError as exc:
                                    _append_task_msg(
                                        task, "external_error",
                                        str(exc), "loom")
                                    _save_task(task)
                                    result = {
                                        "type": "warning",
                                        "message": str(exc),
                                        "task_id": task.id,
                                    }
                            _cascade_done(task.id)
                            # Notify dependents that are now unblocked
                            for _dt in state.board_get_dependents(
                                    task.id):
                                if not task_is_closed(_dt) \
                                        and state.board_deps_met(_dt):
                                    _panel_event(
                                        "task_unblocked", "",
                                        "", _dt.group,
                                        f"Task '{_dt.task[:60]}'"
                                        " is now unblocked",
                                        task_id=_dt.id)
                        _panel_event(
                            "task_completed", cell.id,
                            cell.name, cell.group,
                            message or "Task completed")
                        await _auto_dispatch_next(cell)
                        await _drain_auto_dispatch_queue(
                            task.group if task else cell.group
                        )

                    elif action == "blocked":
                        cell.needs_attention = True
                        cell.activity = "waiting"
                        cell.activity_detail = message
                        _append_mcp(cell, "blocked", message)
                        _append_task_msg(task, "blocked",
                                         message, cell.name)
                        _record_history_msg(cell, "blocked", message)
                        state._emit_agent(cell)
                        if task:
                            _add_label(task, "loom:blocked")
                            _save_task(task)
                        _panel_event(
                            "agent_blocked", cell.id,
                            cell.name, cell.group, message)
                        state.recompute_task_health()

                    elif action == "error":
                        cell.error_message = message
                        cell.needs_attention = True
                        _append_mcp(cell, "error", message)
                        _append_task_msg(task, "error",
                                         message, cell.name)
                        _record_history_msg(cell, "error", message)
                        state._emit_agent(cell)
                        if task:
                            _add_label(task, "loom:error")
                            _save_task(task)
                        _panel_event(
                            "agent_error", cell.id,
                            cell.name, cell.group, message)
                        state.recompute_task_health()

                    elif action == "progress":
                        cell.activity_detail = message
                        if cell.needs_attention:
                            cell.needs_attention = False
                        _append_mcp(cell, "progress", message)
                        _append_task_msg(task, "progress",
                                         message, cell.name)
                        _record_history_msg(cell, "progress", message)
                        state._emit_agent(cell)
                        if task:
                            _save_task(task)
                        # Auto-checkpoint on progress (throttled)
                        await _checkpoint_on_report(cell, message)
                        # Panel event — replace last progress
                        # for this agent to avoid flooding
                        pe = panel_log.replace_last(
                            "agent_progress", cell.id,
                            agent_name=cell.name,
                            group=cell.group,
                            message=message)
                        state._emit("event_append", **pe)
                        state.recompute_task_health()

                    elif action == "verify":
                        if not task:
                            result = {"type": "error",
                                      "message": "No linked task to verify"}
                        else:
                            payload = {}
                            for key in (
                                "verification_mode",
                                "verification_state",
                                "verification_notes",
                                "tests_run",
                                "manual_smoke_done",
                                "deploy_needed",
                                "deploy_attempted",
                                "human_validation_pending",
                            ):
                                if key in data:
                                    payload[key] = data[key]
                            root_task = None
                            root_id = task.pipeline_root_id or ""
                            if root_id and root_id != task.id:
                                root_task = state.board_tasks.get(root_id)
                            verify_msg, _root_task = _apply_verification_report(
                                task,
                                payload,
                                cell.name,
                                _save_task,
                                root_task=root_task,
                            )
                            _append_mcp(cell, "verify", verify_msg)
                            _append_task_msg(task, "verify",
                                             verify_msg, cell.name)
                            _record_history_msg(cell, "verify", verify_msg)
                            state._emit_agent(cell)
                            _panel_event(
                                "task_verification_updated",
                                cell.id, cell.name, cell.group,
                                verify_msg, task_id=task.id,
                            )
                            result = {
                                "type": "verification_updated",
                                "task_id": task.id,
                                "message": verify_msg,
                            }

                    elif action == "ready":
                        cell.activity = ""
                        cell.activity_detail = "ready"
                        cell.needs_attention = False
                        cell.error_message = ""
                        cell.current_task_id = ""
                        _append_mcp(cell, "ready", "Ready")
                        _append_task_msg(task, "ready",
                                         message or "Ready", cell.name)
                        _record_history_msg(
                            cell, "ready", message or "Ready")
                        if task:
                            state.history_complete_task(
                                cell.id, task.id, "ready")
                            await _record_task_boundary(
                                task, cell, message or "Ready"
                            )
                        state._emit_agent(cell)
                        if task:
                            if not task_counts_as_done(task):
                                state.board_move_task(
                                    task.id, "Done")
                            task.agent_id = ""
                            task.status = ""
                            _save_task(task)
                            _cascade_done(task.id)
                            # Notify dependents now unblocked
                            for _dt in state.board_get_dependents(
                                    task.id):
                                if not task_is_closed(_dt) \
                                        and state.board_deps_met(_dt):
                                    _panel_event(
                                        "task_unblocked", "",
                                        "", _dt.group,
                                        f"Task '{_dt.task[:60]}'"
                                        " is now unblocked",
                                        task_id=_dt.id)
                        _panel_event(
                            "task_completed", cell.id,
                            cell.name, cell.group,
                            "Ready (task completed)")
                        await _auto_dispatch_next(cell)
                        await _drain_auto_dispatch_queue(
                            task.group if task else cell.group
                        )

                    elif action == "derive":
                        # Derive a new task and dispatch it
                        act_name = data.get("action_name", "")
                        act_vars = data.get("action_vars", {})
                        derive_group = data.get("group", "")
                        reuse_self = data.get("reuse_self", False)
                        target_agent = data.get("target_agent", "")

                        if not task:
                            result = {"type": "error",
                                      "message":
                                          "No linked task to derive from"}
                        elif not message:
                            result = {"type": "error",
                                      "message":
                                          "Derive requires a description"}
                        else:
                            # Validate transition
                            base_dir = cell.worktree_repo_root \
                                or cell.directory \
                                or await _resolve_base_dir(
                                    task.group)
                            cur_transitions = \
                                action_mgr.get_transitions(
                                    task.action_name, base_dir)
                            valid_targets = [
                                t["action"] for t in cur_transitions
                                if isinstance(t, dict)
                                and t.get("action")]
                            if cur_transitions and act_name \
                                    and act_name not in valid_targets:
                                result = {
                                    "type": "error",
                                    "message":
                                        f"Action '{task.action_name}'"
                                        f" cannot transition to "
                                        f"'{act_name}'. Valid: "
                                        f"{', '.join(valid_targets)}"}
                            else:
                                # Check depth limit
                                new_depth = task.pipeline_depth + 1
                                act_meta = \
                                    action_mgr.load_action(
                                        act_name, base_dir) \
                                    if act_name else None
                                max_d = (
                                    (act_meta or {}).get("max_depth")
                                    or state.global_settings
                                        .max_pipeline_depth
                                    or 0)
                                if max_d and new_depth > max_d:
                                    cell.needs_attention = True
                                    state._emit_agent(cell)
                                    if task:
                                        _add_label(task,
                                                   "loom:depth-limit")
                                        _save_task(task)
                                    result = {
                                        "type": "error",
                                        "message":
                                            f"Pipeline depth limit "
                                            f"({max_d}) reached"}
                                else:
                                    # Keep parent in In Progress;
                                    # update its status from transition
                                    cell.activity = ""
                                    cell.activity_detail = ""
                                    cell.needs_attention = False
                                    cell.error_message = ""
                                    state._emit_agent(cell)
                                    # Determine status from transition
                                    derive_status = ""
                                    if cur_transitions and act_name:
                                        for tr in cur_transitions:
                                            if isinstance(tr, dict) \
                                                    and tr.get("action") \
                                                    == act_name:
                                                derive_status = tr.get(
                                                    "status", "")
                                                break
                                    if not derive_status and act_name:
                                        derive_status = act_name
                                    # Update parent task status
                                    task.status = derive_status
                                    _save_task(task)
                                    # Propagate status to root
                                    root_id_s = \
                                        task.pipeline_root_id \
                                        or task.id
                                    if root_id_s != task.id:
                                        root_t = \
                                            state.board_tasks.get(
                                                root_id_s)
                                        if root_t:
                                            root_t.status = \
                                                derive_status
                                            _save_task(root_t)
                                    # Create derived task
                                    grp = derive_group \
                                        or task.group
                                    root_id = \
                                        task.pipeline_root_id \
                                        or task.id
                                    derive_desc = data.get(
                                        "description", "")
                                    new_task = state.board_add_task(
                                        task=message,
                                        group=grp,
                                        lane="Backlog",
                                        action_name=act_name,
                                        action_vars=act_vars,
                                        labels=["loom:derived"],
                                        parent_task_id=task.id,
                                        pipeline_depth=new_depth,
                                        pipeline_root_id=root_id,
                                        description=derive_desc,
                                    )
                                    if new_task:
                                        _append_mcp(
                                            cell, "derive",
                                            message[:80])
                                        _append_task_msg(
                                            task, "derive",
                                            message, cell.name)
                                        _record_history_msg(
                                            cell, "derive",
                                            message[:80])
                                        _save_task(task)
                                        state._emit_agent(cell)
                                        _panel_event(
                                            "task_derived",
                                            cell.id, cell.name,
                                            cell.group,
                                            message[:80],
                                            task_id=new_task.id)
                                        # Determine dispatch target
                                        # Enforce transition's declared
                                        # target — ignore --self/--agent
                                        # if the transition specifies a
                                        # different target
                                        tr_target = ""
                                        if cur_transitions and act_name:
                                            for tr in cur_transitions:
                                                if isinstance(tr, dict) \
                                                        and tr.get(
                                                            "action"
                                                        ) == act_name:
                                                    tr_target = tr.get(
                                                        "target", "")
                                                    break
                                        if tr_target == "self":
                                            reuse_self = True
                                            target_agent = ""
                                        elif tr_target == "parent":
                                            reuse_self = False
                                            pt = state.board_tasks.get(
                                                task.parent_task_id) \
                                                if task.parent_task_id \
                                                else None
                                            if pt and pt.agent_id:
                                                a = state.agents.get(
                                                    pt.agent_id)
                                                if a:
                                                    target_agent = \
                                                        a.slug or a.name
                                        elif tr_target == "root":
                                            reuse_self = False
                                            rid = task.pipeline_root_id \
                                                or task.id
                                            rt = state.board_tasks.get(
                                                rid)
                                            if rt and rt.agent_id:
                                                a = state.agents.get(
                                                    rt.agent_id)
                                                if a:
                                                    target_agent = \
                                                        a.slug or a.name
                                        elif tr_target == "" \
                                                and cur_transitions:
                                            # No target declared — force
                                            # new agent even if agent
                                            # passed --self/--agent
                                            reuse_self = False
                                            target_agent = ""
                                        target_id = None
                                        if reuse_self:
                                            target_id = cell.id
                                        elif target_agent:
                                            target_id = \
                                                _resolve_agent_id(
                                                    state,
                                                    target_agent)
                                            if not target_id:
                                                result = {
                                                    "type": "error",
                                                    "message":
                                                        "Agent not "
                                                        "found: "
                                                        + target_agent
                                                }

                                        if result and \
                                                result.get("type") \
                                                == "error":
                                            pass  # skip dispatch
                                        elif target_id == cell.id:
                                            # Self-dispatch through the
                                            # normal delayed same-agent
                                            # prompt path.
                                            dispatch_data = {
                                                "cmd": "dispatch_task",
                                                "id": new_task.id,
                                                "agent_id": cell.id,
                                                "_self_dispatch": True,
                                            }
                                            await state.broadcast()
                                            dr = await handle_command(
                                                dispatch_data)
                                            if dr and dr.get("type") \
                                                    == "error":
                                                result = dr
                                            else:
                                                result = {
                                                    "type": "ok",
                                                    "task_id":
                                                        new_task.id,
                                                    "agent_id":
                                                        cell.id,
                                                }
                                        elif target_id:
                                            # Dispatch to different
                                            # existing agent
                                            tgt = state.agents.get(
                                                target_id)
                                            dispatch_data = {
                                                "cmd": "dispatch_task",
                                                "id": new_task.id,
                                                "agent_id": target_id,
                                            }
                                            # Inherit worktree from
                                            # target agent
                                            if tgt and \
                                                    tgt.worktree_path:
                                                dispatch_data[
                                                    "inherit_worktree"
                                                    "_from"
                                                ] = target_id
                                            if cell.worktree_path:
                                                dispatch_data[
                                                    "handoff_worktree_from"
                                                ] = cell.id
                                            elif cell.worktree_branch:
                                                dispatch_data[
                                                    "handoff_worktree_from"
                                                ] = cell.id
                                            if not (tgt and
                                                    tgt.worktree_path) \
                                                    and cell.worktree_path:
                                                dispatch_data[
                                                    "inherit_worktree"
                                                    "_from"
                                                ] = cell.id
                                            await state.broadcast()
                                            dr = \
                                                await handle_command(
                                                    dispatch_data)
                                            result = {
                                                "type": "ok",
                                                "task_id":
                                                    new_task.id,
                                                "agent_id":
                                                    target_id}
                                        else:
                                            # Default: new agent
                                            dispatch_data = {
                                                "cmd":
                                                    "dispatch_task",
                                                "id": new_task.id,
                                                "create_agent": True,
                                            }
                                            # Inherit worktree from
                                            # parent agent
                                            if cell.worktree_path:
                                                dispatch_data[
                                                    "inherit_worktree"
                                                    "_from"
                                                ] = cell.id
                                                dispatch_data[
                                                    "handoff_worktree_from"
                                                ] = cell.id
                                            if cell.session_id:
                                                dispatch_data[
                                                    "target_session_id"
                                                ] = cell.session_id
                                            if cell.window_id:
                                                dispatch_data[
                                                    "target_window_id"
                                                ] = cell.window_id
                                            await state.broadcast()
                                            dr = \
                                                await handle_command(
                                                    dispatch_data)
                                            result = {
                                                "type": "ok",
                                                "task_id":
                                                    new_task.id,
                                                "agent_id":
                                                    new_task.agent_id}
                                    else:
                                        result = {
                                            "type": "error",
                                            "message":
                                                "Failed to create "
                                                "derived task"}

                    elif action == "ask":
                        # Create a derived task in Backlog for human
                        if not task:
                            result = {"type": "error",
                                      "message":
                                          "No linked task to derive from"}
                        elif not message:
                            result = {"type": "error",
                                      "message":
                                          "Ask requires a question"}
                        else:
                            # Keep parent in In Progress with
                            # "Awaiting Input" status
                            cell.activity = ""
                            cell.activity_detail = ""
                            cell.needs_attention = True
                            cell.error_message = ""
                            _append_mcp(cell, "ask", message)
                            _append_task_msg(task, "ask",
                                             message, cell.name)
                            _record_history_msg(cell, "ask", message)
                            state._emit_agent(cell)
                            task.status = "Awaiting Input"
                            _save_task(task)
                            # Propagate status to root
                            root_id_s = task.pipeline_root_id \
                                or task.id
                            if root_id_s != task.id:
                                root_t = state.board_tasks.get(
                                    root_id_s)
                                if root_t:
                                    root_t.status = "Awaiting Input"
                                    _save_task(root_t)
                            # Create HITL task in Backlog
                            grp = task.group
                            root_id = task.pipeline_root_id \
                                or task.id
                            ask_desc = data.get(
                                "description", "")
                            new_task = state.board_add_task(
                                task=message,
                                group=grp,
                                lane="Backlog",
                                labels=["loom:human", "loom:derived"],
                                parent_task_id=task.id,
                                pipeline_depth=
                                    task.pipeline_depth + 1,
                                pipeline_root_id=root_id,
                                description=ask_desc,
                            )
                            if new_task:
                                result = {
                                    "type": "ok",
                                    "task_id": new_task.id}
                                _panel_event(
                                    "ask_created", cell.id,
                                    cell.name, cell.group,
                                    message,
                                    task_id=new_task.id)
                            else:
                                result = {
                                    "type": "error",
                                    "message":
                                        "Failed to create ask task"}

                    elif action == "name":
                        if not message:
                            result = {"type": "error",
                                      "message":
                                          "Name is required"}
                        else:
                            old_name = cell.name
                            _append_mcp(cell, "name", message)
                            _append_task_msg(task, "name",
                                             message, cell.name)
                            _record_history_msg(
                                cell, "name", message)
                            if task:
                                _save_task(task)
                            state.update_agent(cell.id,
                                               name=message)
                            state.history_update_agent(
                                cell, name=message,
                                slug=cell.slug)
                            if cell.session_id:
                                await bridge.update_session(
                                    cell, old_name)
                            _panel_event(
                                "agent_renamed", cell.id,
                                cell.name, cell.group,
                                f"{old_name} \u2192 {cell.name}")
                            state.recompute_task_health()
                            result = {"type": "ok",
                                      "slug": cell.slug}

                    elif action == "reply":
                        if not message:
                            result = {"type": "error",
                                      "message":
                                          "Reply message is required"}
                        elif not cell.pending_weaver_message:
                            result = {"type": "error",
                                      "message":
                                          "No pending weaver message "
                                          "to reply to"}
                        else:
                            cell.pending_weaver_message = False
                            _append_mcp(cell, "reply", message)
                            _record_history_msg(cell, "reply", message)
                            _panel_event(
                                "agent_reply", cell.id,
                                cell.name, cell.group,
                                message[:200])
                            result = {"type": "ok"}

                    else:
                        result = {"type": "error",
                                  "message":
                                      f"Unknown ai action: {action}"}

            elif cmd == "memory_publish":
                if not state.db:
                    result = {
                        "type": "error",
                        "message": "Memory storage is unavailable",
                    }
                else:
                    cell, task = _resolve_memory_cell_and_task(
                        state,
                        data.get("cell_id", ""),
                        data.get("task_id", ""),
                    )
                    existing = None
                    entry_id = (data.get("entry_id", "") or "").strip()
                    if entry_id:
                        existing = state.db.load_memory_entry(entry_id)
                        if not existing:
                            result = {
                                "type": "error",
                                "message": "Memory entry not found",
                            }
                    try:
                        if result is not None:
                            raise RuntimeError("__skip_memory_publish__")
                        scope_kind = (data.get("scope_kind", "") or "").strip()
                        if not scope_kind and existing:
                            scope_kind = existing.get("scope_kind", "")
                        scope_ref = _resolve_memory_scope_ref(
                            scope_kind,
                            data.get("scope_ref", "")
                            if "scope_ref" in data
                            else (existing.get("scope_ref", "") if existing else ""),
                            cell=cell,
                            task=task,
                        )
                        entry_type = data.get("entry_type", "")
                        if not entry_type and existing:
                            entry_type = existing.get("entry_type", "")
                        title = data.get("title", "")
                        if "title" not in data and existing:
                            title = existing.get("title", "")
                        content = data.get("content", "")
                        if "content" not in data and existing:
                            content = existing.get("content", "")
                        pinned = (
                            bool(data.get("pinned", False))
                            if "pinned" in data
                            else bool(existing.get("pinned", False))
                            if existing
                            else False
                        )
                        retention_kind = (
                            (data.get("retention_kind", "") or "").strip()
                            if "retention_kind" in data
                            else (existing.get("retention_kind", "") if existing else "")
                        )
                        if retention_kind:
                            retention_kind = normalize_retention_kind(retention_kind)
                        source_kind = data.get("source_kind", "")
                        if not source_kind and existing:
                            source_kind = existing.get("source_kind", "agent")
                        pending_links = []
                        for raw_link in (data.get("link_targets", []) or []):
                            if not isinstance(raw_link, dict):
                                raise ValueError(
                                    "Each link target must be an object"
                                )
                            target_kind = normalize_link_target_kind(
                                raw_link.get("target_kind", "")
                            )
                            pending_links.append(
                                build_memory_link(
                                    state,
                                    entry_id=entry_id or "__pending__",
                                    target_kind=target_kind,
                                    target_ref=_resolve_memory_link_ref(
                                        target_kind,
                                        raw_link.get("target_ref", ""),
                                        cell=cell,
                                        task=task,
                                    ),
                                    cell=cell,
                                    task=task,
                                )
                            )
                        entry = build_memory_entry(
                            state,
                            cell=cell,
                            task=task,
                            entry_type=normalize_entry_type(entry_type),
                            title=title,
                            content=content,
                            scope_kind=scope_kind,
                            scope_ref=scope_ref,
                            pinned=pinned,
                            source_kind=source_kind or "agent",
                            retention_kind=retention_kind,
                        )
                    except RuntimeError as exc:
                        if str(exc) != "__skip_memory_publish__":
                            raise
                    except ValueError as exc:
                        result = {"type": "error", "message": str(exc)}
                    else:
                        if result is None:
                            if existing:
                                entry["id"] = existing["id"]
                                entry["created_at"] = existing.get(
                                    "created_at", entry["created_at"]
                                )
                                entry["source_kind"] = existing.get(
                                    "source_kind", entry["source_kind"]
                                )
                                entry["source_id"] = existing.get(
                                    "source_id", entry["source_id"]
                                )
                                entry["source_name"] = existing.get(
                                    "source_name", entry["source_name"]
                                )
                            state.db.save_memory_entry(entry)
                            for link in pending_links:
                                link["entry_id"] = entry["id"]
                                state.db.save_memory_link(link)
                            entry = state.db.load_memory_entry(entry["id"]) or entry
                            result = {"type": "memory_entry", "entry": entry}

            elif cmd == "memory_pin":
                if not state.db:
                    result = {
                        "type": "error",
                        "message": "Memory storage is unavailable",
                    }
                else:
                    entry_id = (data.get("entry_id", "") or "").strip()
                    entry = state.db.load_memory_entry(entry_id)
                    if not entry:
                        result = {
                            "type": "error",
                            "message": "Memory entry not found",
                        }
                    else:
                        now = time.time()
                        state.db.set_memory_entry_pinned(entry_id, True, now)
                        entry = state.db.load_memory_entry(entry_id) or entry
                        result = {"type": "memory_entry", "entry": entry}

            elif cmd == "memory_link":
                if not state.db:
                    result = {
                        "type": "error",
                        "message": "Memory storage is unavailable",
                    }
                else:
                    entry_id = (data.get("entry_id", "") or "").strip()
                    entry = state.db.load_memory_entry(entry_id)
                    if not entry:
                        result = {
                            "type": "error",
                            "message": "Memory entry not found",
                        }
                    else:
                        cell, task = _resolve_memory_cell_and_task(
                            state,
                            data.get("cell_id", ""),
                            data.get("task_id", ""),
                        )
                        try:
                            target_kind = normalize_link_target_kind(
                                data.get("target_kind", "")
                            )
                            link = build_memory_link(
                                state,
                                entry_id=entry_id,
                                target_kind=target_kind,
                                target_ref=_resolve_memory_link_ref(
                                    target_kind,
                                    data.get("target_ref", ""),
                                    cell=cell,
                                    task=task,
                                ),
                                cell=cell,
                                task=task,
                            )
                        except ValueError as exc:
                            result = {"type": "error", "message": str(exc)}
                        else:
                            state.db.save_memory_link(link)
                            entry = state.db.load_memory_entry(entry_id)
                            result = {"type": "memory_entry", "entry": entry}

            elif cmd == "memory_unpin":
                if not state.db:
                    result = {
                        "type": "error",
                        "message": "Memory storage is unavailable",
                    }
                else:
                    entry_id = (data.get("entry_id", "") or "").strip()
                    entry = state.db.load_memory_entry(entry_id)
                    if not entry:
                        result = {
                            "type": "error",
                            "message": "Memory entry not found",
                        }
                    else:
                        now = time.time()
                        state.db.set_memory_entry_pinned(entry_id, False, now)
                        entry = state.db.load_memory_entry(entry_id) or entry
                        result = {"type": "memory_entry", "entry": entry}

            elif cmd == "task_chain":
                tid = data.get("task_id", "")
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                else:
                    chain = state.board_get_chain(tid)
                    result = {
                        "type": "ok",
                        "root_id": task.pipeline_root_id or task.id,
                        "chain": [
                            {"id": t.id, "task": t.task,
                             "lane": t.lane,
                             "status": t.status,
                             "depth": t.pipeline_depth,
                             "agent_id": t.agent_id,
                             "action_name": t.action_name,
                             "parent_task_id": t.parent_task_id,
                             "labels": t.labels}
                            for t in chain
                        ],
                    }

            elif cmd == "discover_pipelines":
                base_dir = await _resolve_base_dir(
                    data.get("group", ""))
                pipelines = action_mgr.discover_pipelines(base_dir)
                result = {"type": "pipelines", "pipelines": pipelines}

            # -- Weaver commands ------------------------------------------

            elif cmd == "weaver_message":
                agent_ident = data.get("agent_id", "")
                msg_text = data.get("message", "")
                agent_id = _resolve_agent_id(state, agent_ident)
                if not agent_id:
                    result = {"type": "error",
                              "message": f"Agent not found: {agent_ident}"}
                elif not msg_text:
                    result = {"type": "error",
                              "message": "Message is required"}
                else:
                    target = state.agents.get(agent_id)
                    if not target or not target.session_id:
                        result = {"type": "error",
                                  "message": "Agent is not running"}
                    else:
                        formatted = (
                            "\n"
                            "── Message from Weaver "
                            "────────────────────────\n"
                            f"{msg_text}\n\n"
                            'Reply with: loom_reply("your response")\n'
                            "────────────────────────────────────────"
                            "────────\n"
                        )
                        # Pre-mark as input-ready (agent is
                        # likely idle/waiting for this message)
                        bridge.prime_input_ready(target.session_id)
                        await bridge.send_text(
                            target.session_id, formatted)
                        target.pending_weaver_message = True
                        state._emit_agent(target)
                        _panel_event(
                            "weaver_message", target.id,
                            target.name, target.group,
                            msg_text[:200])
                        result = {"type": "ok"}

            elif cmd == "weaver_journal_append":
                group = data.get("group", "")
                entry_type = data.get("entry_type", "")
                entry_text = data.get("entry", "")
                if entry_type not in (
                        "decision", "observation", "checkpoint", "plan"):
                    result = {"type": "error",
                              "message":
                                  "entry_type must be one of: decision, "
                                  "observation, checkpoint, plan"}
                elif not entry_text:
                    result = {"type": "error",
                              "message": "Entry text is required"}
                else:
                    evt = state.journal_append(
                        group, entry_type, entry_text)
                    result = {"type": "ok", "id": evt["id"]}

            elif cmd == "weaver_journal_read":
                group = data.get("group", "")
                tail = data.get("tail", 20)
                entry_type = data.get("entry_type", "")
                entries = state.journal_read(group, tail, entry_type)
                result = {"type": "journal", "entries": entries}

            elif cmd == "weaver_journal_delete":
                group = data.get("group", "")
                entry_id = data.get("entry_id", 0)
                if entry_id and db:
                    db._conn.execute(
                        "DELETE FROM weaver_journal WHERE id=? "
                        "AND group_name=?", (entry_id, group))
                    db._conn.commit()
                    state._emit("journal_delete",
                                group=group, id=entry_id)
                result = {"type": "ok"}

            elif cmd == "weaver_update_settings":
                group = data.get("group", "")
                fields = {}
                for k in ("push_interval", "max_interval",
                          "heartbeat_interval",
                          "default_worker_concurrency",
                          "autonomy_mode",
                          "wave_size_preference",
                          "same_agent_follow_up_preference",
                          "digest_verbosity",
                          "escalation_style",
                          "pending_note", "pending_note_kind",
                          "custom_instructions", "enabled_events",
                          "paused", "weaver_provider",
                          "weaver_boot_command"):
                    if k in data:
                        fields[k] = data[k]
                state.update_weaver_settings(group, **fields)
                result = {"type": "ok"}

            elif cmd == "weaver_ask":
                group = data.get("group", "")
                question = data.get("question", "")
                if not question:
                    result = {"type": "error",
                              "message": "Question is required"}
                else:
                    state.update_weaver_settings(
                        group,
                        pending_question=question,
                        paused=True)
                    # Also log to journal
                    state.journal_append(
                        group, "observation",
                        f"Asked human: {question}")
                    result = {"type": "ok"}

            elif cmd == "weaver_note":
                group = data.get("group", "")
                message = data.get("message", "")
                kind = data.get("kind", "note")
                if not message:
                    result = {"type": "error",
                              "message": "Message is required"}
                elif kind not in {"note", "question"}:
                    result = {"type": "error",
                              "message": "kind must be 'note' or 'question'"}
                else:
                    state.update_weaver_settings(
                        group,
                        pending_note=message,
                        pending_note_kind=kind)
                    prefix = "Soft question" if kind == "question" else "Note"
                    state.journal_append(
                        group, "observation",
                        f"{prefix} for human: {message}")
                    result = {"type": "ok"}

            elif cmd == "weaver_dismiss_note":
                group = data.get("group", "")
                state.update_weaver_settings(
                    group,
                    pending_note="",
                    pending_note_kind="")
                result = {"type": "ok"}

            elif cmd == "weaver_reply":
                group = data.get("group", "")
                answer = data.get("answer", "")
                if not answer:
                    result = {"type": "error",
                              "message": "Answer is required"}
                else:
                    weaver = state.get_weaver_for_group(group)
                    if not weaver or not weaver.session_id:
                        result = {"type": "error",
                                  "message": "Weaver is not running"}
                    else:
                        # Send answer to weaver's terminal
                        formatted = (
                            "\n"
                            "── Human Reply "
                            "──────────────────────────────\n"
                            f"{answer}\n"
                            "────────────────────────────────────────"
                            "────────\n"
                        )
                        # Pre-mark as input-ready so send_text
                        # skips the wait (weaver is already idle)
                        bridge.prime_input_ready(weaver.session_id)
                        await bridge.send_text(
                            weaver.session_id, formatted)
                        # Clear question, unpause events
                        state.update_weaver_settings(
                            group,
                            pending_question="",
                            paused=False)
                        # Log to journal
                        state.journal_append(
                            group, "observation",
                            f"Human replied: {answer}")
                        result = {"type": "ok"}

            elif cmd == "weaver_pause":
                group = data.get("group", "")
                state.update_weaver_settings(group, paused=True)
                result = {"type": "ok"}

            elif cmd == "weaver_resume":
                group = data.get("group", "")
                state.update_weaver_settings(
                    group, paused=False, pending_question="")
                result = {"type": "ok"}

            elif cmd == "restart":
                log.info("Restart requested — cleaning up and re-executing")
                if _should_install_keybindings() and keybindings:
                    await keybindings.remove(connection, _displaced[0])
                # Persist all agents (status etc.) before restart
                for cell in state.agents.values():
                    state._db_save_agent(cell)
                os.execv(sys.executable, [sys.executable] + sys.argv)

        except Exception as exc:
            log.exception("Command '%s' failed", cmd)
            result = {"type": "error", "message": str(exc)}

        await state.broadcast()
        return result

    async def _confirm_keybinding_close(cell) -> bool:
        if not keybindings:
            return False
        try:
            import iterm2
            message = keybindings.build_close_cell_confirmation_message(
                state, cell)
            alert = iterm2.Alert(
                "Close cell?",
                message,
                window_id=cell.window_id or state.current_window_id or None,
            )
            alert.add_button("Cancel")
            alert.add_button("Remove")
            return await alert.async_run(connection) == 1001
        except Exception:
            log.exception("Failed to confirm close-cell keybinding for '%s'",
                          cell.name)
            return False

    async def _run_keybinding_command(payload: dict,
                                      description: str) -> dict | None:
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            log.warning("Keybinding command '%s' failed: %s",
                        description, result.get("message", "unknown error"))
        return result

    async def _handle_keybinding_add_agent(*,
                                           group: str,
                                           name: str = "",
                                           target_session_id: str = "",
                                           target_window_id: str = ""):
        payload = {"cmd": "add_agent", "group": group}
        if name:
            payload["name"] = name
        if target_session_id:
            payload["target_session_id"] = target_session_id
        if target_window_id:
            payload["target_window_id"] = target_window_id
        await _run_keybinding_command(payload, "add_agent")

    async def _handle_keybinding_add_terminal(*,
                                              group: str,
                                              parent_id: str,
                                              name: str = ""):
        payload = {
            "cmd": "add_terminal",
            "group": group,
            "parent_id": parent_id,
        }
        if name:
            payload["name"] = name
        await _run_keybinding_command(payload, "add_terminal")

    async def _handle_keybinding_close_cell(cell):
        if await _confirm_keybinding_close(cell):
            await _run_keybinding_command(
                {"cmd": "remove_agent", "id": cell.id},
                "remove_agent",
            )

    # Register iTerm2 RPCs and global key bindings only when Loom is
    # running as an iTerm2-hosted script. External standalone mode still
    # controls sessions through the adapter, but it should not try to
    # register iTerm2-global shortcuts before the HTTP server binds.
    if _should_install_keybindings():
        _kb_overrides = state.global_settings.keybindings or None
        from . import keybindings

        displaced_bindings = await keybindings.setup(
            connection,
            state,
            bridge,
            overrides=_kb_overrides,
            add_agent_handler=_handle_keybinding_add_agent,
            add_terminal_handler=_handle_keybinding_add_terminal,
            close_cell_handler=_handle_keybinding_close_cell,
        )
        # Mutable container so nested closures can reassign on keybinding change
        _displaced = [displaced_bindings]
        log.info("Startup checkpoint: keybindings installed")
    else:
        log.info("Standalone mode — iTerm2 keybindings skipped")
    log.info("Startup checkpoint: post-keybinding setup")

    # -- Events endpoint (agent hooks) ----------------------------------------

    async def handle_events(request):
        """Receive events from agent hooks (Claude Code HTTP hooks, etc.)."""
        try:
            raw = await request.json()
        except Exception:
            return web.json_response({}, status=400)

        # Correlate: X-Loom-Cell-Id header (primary) → cwd match (fallback)
        cell_id = request.headers.get("X-Loom-Cell-Id", "")
        cell = state.agents.get(cell_id) if cell_id else None

        if not cell:
            # Fallback: match by cwd
            cwd = raw.get("cwd", "")
            if cwd:
                for c in state.agents.values():
                    if c.session_id and c.directory and \
                            os.path.realpath(c.directory) == os.path.realpath(cwd):
                        cell = c
                        break

        if not cell:
            log.debug("Event from unknown cell (id=%s, cwd=%s), discarding",
                      cell_id, raw.get("cwd", ""))
            return web.json_response({})

        # Parse through the adapter
        adapter = get_adapter(cell.agent_type)
        event = adapter.parse_event(raw, cell)
        if event:
            await event_bus.emit(event)

        # Always return 200 with empty JSON — never block the agent
        return web.json_response({})

    # -- Panel event helper -------------------------------------------------

    def _panel_event(kind, cell_id, agent_name, group, message,
                     task_id=""):
        """Append a panel event and queue a delta broadcast."""
        pe = panel_log.append(
            kind=kind, cell_id=cell_id, agent_name=agent_name,
            group=group, message=message, task_id=task_id)
        state._emit("event_append", **pe)

    # -- Scheduler ----------------------------------------------------------

    asyncio.create_task(
        _pump_auto_dispatch_queue(state, handle_command, _panel_event)
    )
    asyncio.create_task(
        _scheduler_loop(state, handle_command, _panel_event))
    log.info("Task scheduler and auto-dispatch queue pump started")
    log.info("Startup checkpoint: scheduler tasks scheduled")

    # -- HTTP / WS routes ---------------------------------------------------

    async def handle_index(_request):
        from .config import WEBVIEW_FILE  # re-read after init_paths
        return web.FileResponse(WEBVIEW_FILE)

    async def handle_ws(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        state._ws_clients.add(ws)
        await ws.send_str(json.dumps(_state_payload()))
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        result = await handle_command(
                            json.loads(msg.data))
                        if result:
                            await ws.send_str(json.dumps(result))
                    except json.JSONDecodeError:
                        log.warning("Received malformed JSON from webview")
                    except Exception:
                        log.exception("Error handling WS command")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
        finally:
            state._ws_clients.discard(ws)
        return ws

    async def handle_terminal_ws(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        cell_id = request.match_info.get("cell_id", "")
        terminal_clients.setdefault(cell_id, set()).add(ws)
        try:
            if not bridge.capabilities.supports_embedded_terminal:
                await ws.send_str(json.dumps({
                    "type": "error",
                    "message": "Embedded terminals are unavailable in this runtime.",
                }))
            cell = state.agents.get(cell_id)
            if cell and cell.session_id:
                await ws.send_str(json.dumps({
                    "type": "snapshot",
                    "cell_id": cell_id,
                    "session_id": cell.session_id,
                    "data": bridge.get_terminal_buffer(cell.session_id),
                }))
            else:
                await ws.send_str(json.dumps({
                    "type": "snapshot",
                    "cell_id": cell_id,
                    "session_id": "",
                    "data": "",
                }))
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
                    await bridge.resize_session(
                        cell.session_id,
                        int(payload.get("cols", 0) or 0),
                        int(payload.get("rows", 0) or 0),
                    )
                elif payload.get("type") == "focus":
                    await bridge.focus_session(cell.session_id)
        finally:
            terminal_clients.get(cell_id, set()).discard(ws)
        return ws

    # -- REST API endpoint --------------------------------------------------

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

        try:
            result = await handle_command(data)
        except Exception as exc:
            log.exception("API command '%s' failed", cmd)
            return web.json_response(
                {"ok": False, "error": str(exc)}, status=500)

        if result and result.get("type") == "error":
            return web.json_response(
                {"ok": False, "error": result.get("message", "")})

        payload = result if result else _state_payload()
        return web.json_response({"ok": True, "data": payload})

    # -- Attachment upload/serve endpoints -----------------------------------

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

    # -- Orphan attachment cleanup on startup --------------------------------

    def _cleanup_orphan_attachments():
        if not ATTACHMENTS_DIR.is_dir():
            return
        task_ids = set(state.board_tasks.keys())
        for entry in ATTACHMENTS_DIR.iterdir():
            if entry.is_dir() and entry.name not in task_ids:
                shutil.rmtree(entry, ignore_errors=True)
                log.info("Cleaned up orphan attachments for %s", entry.name)

    _cleanup_orphan_attachments()

    # -- Start server -------------------------------------------------------

    app_server = web.Application()
    app_server.router.add_get("/", handle_index)
    app_server.router.add_get("/ws", handle_ws)
    app_server.router.add_get("/ws/terminal/{cell_id}", handle_terminal_ws)
    app_server.router.add_post("/events", handle_events)
    app_server.router.add_post("/api/cmd", handle_api_cmd)
    app_server.router.add_post("/mcp", create_mcp_handler(handle_command, state))
    app_server.router.add_post("/api/upload", handle_upload)
    app_server.router.add_post("/api/upload/cleanup", handle_upload_cleanup)
    app_server.router.add_get(
        "/attachments/{task_id}/{filename}", handle_serve_attachment)
    from .config import SCRIPT_DIR
    app_server.router.add_static("/static", SCRIPT_DIR / "static")
    log.info("Startup checkpoint: routes registered")

    runner = web.AppRunner(app_server)
    log.info("Startup checkpoint: AppRunner created")
    try:
        await runner.setup()
        log.info("Startup checkpoint: runner setup complete")
        site = web.TCPSite(runner, BIND_HOST, WS_PORT, reuse_address=True)
        log.info("Startup checkpoint: TCPSite created")
        try:
            await site.start()
        except OSError as exc:
            log.error("Cannot bind port %d: %s — is another instance running?",
                      WS_PORT, exc)
            raise
        log.info("Startup checkpoint: site start complete")
        log.info("HTTP/WS server listening on %s:%d", BIND_HOST, WS_PORT)

        # -- Register toolbelt (skipped in standalone-only mode) ----------------

        if not STANDALONE:
            registered = await bridge.register_web_view_tool(
                display_name="Loom",
                identifier="com.loom.toolbelt",
                reveal_if_already_registered=True,
                url=f"http://127.0.0.1:{WS_PORT}/",
            )
            if registered:
                log.info("Toolbelt webview registered — Loom ready")
            else:
                log.warning("Toolbelt webview registration unavailable")
        else:
            log.info("Standalone mode — toolbelt registration skipped")
            log.info("Open http://127.0.0.1:%d/ in a browser", WS_PORT)

        await asyncio.Future()
    finally:
        for ws_clients in terminal_clients.values():
            for ws_client in list(ws_clients):
                try:
                    await ws_client.close()
                except Exception:
                    pass
        terminal_clients.clear()
        for ws_client in list(state._ws_clients):
            try:
                await ws_client.close()
            except Exception:
                pass
        state._ws_clients.clear()
        try:
            await bridge.shutdown()
        except Exception:
            log.exception("Terminal adapter shutdown failed")
        try:
            await runner.cleanup()
        except Exception:
            log.exception("HTTP runner cleanup failed")
