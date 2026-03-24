"""aiohttp server, WebSocket command handler, and iTerm2 entry point."""

import asyncio
import json
import os
import sys

import aiohttp
from aiohttp import web
import iterm2

from .config import WS_PORT, WEBVIEW_FILE, log
from dataclasses import asdict
from .state import MatrixState
from .bridge import ITerm2Bridge
from .events import EventLog, EventBus, health_check
from .adapters import get_adapter
from .notifications import NotificationManager
from .worktree import WorktreeManager
from . import keybindings


async def _worktree_diff_updater(state: MatrixState,
                                 worktree_mgr: WorktreeManager):
    """Periodically update diff stats for cells with active worktrees."""
    while True:
        await asyncio.sleep(60)
        changed = False
        for cell in state.agents.values():
            if not cell.worktree_path:
                continue
            diff = await worktree_mgr.diff_summary(cell)
            dirty = await worktree_mgr.has_uncommitted_changes(cell)
            checkpoints = await worktree_mgr.count_commits(cell)
            if diff != cell.worktree_diff or dirty != cell.worktree_dirty \
                    or checkpoints != cell.worktree_checkpoints:
                cell.worktree_diff = diff
                cell.worktree_dirty = dirty
                cell.worktree_checkpoints = checkpoints
                changed = True
        if changed:
            await state.broadcast()


async def main(connection: iterm2.Connection):
    log.info("Loom starting (port=%d)", WS_PORT)
    state = MatrixState()
    state.load()
    log.info("State loaded: %d agents, %d groups",
             len(state.agents), len(state.groups))

    event_log = EventLog()
    notifier = NotificationManager(state)
    notifier.start()
    event_bus = EventBus(state, event_log, notifier)
    event_bus.start()
    asyncio.create_task(health_check(state, event_log, event_bus, notifier))
    log.info("Event bus, health monitor, and notifications started")

    bridge = ITerm2Bridge(connection, state)
    worktree_mgr = WorktreeManager()

    _pending_merges: set[str] = set()  # cell IDs awaiting merge verification

    def _checkpoint_message(cell) -> str:
        """Build a checkpoint commit message from the agent's last summary."""
        summary = cell.last_summary.strip()
        n = cell.worktree_checkpoints + 1
        subject = f"loom: checkpoint {n} — {cell.name}"
        if summary:
            return f"{subject}\n\n{summary}"
        return subject

    async def _on_agent_session_end(cell):
        """Handle agent turn completion: merge verification + auto-checkpoint."""
        # Check pending merge result
        if cell.id in _pending_merges:
            _pending_merges.discard(cell.id)
            merged = await worktree_mgr.is_merged(cell)
            if merged:
                log.info("Merge verified for '%s': branch %s merged into %s",
                         cell.name, cell.worktree_branch,
                         cell.worktree_base_branch)
                await _broadcast_toast(
                    f'"{cell.name}" merged to {cell.worktree_base_branch}',
                    "success")
            else:
                log.warning("Merge failed for '%s': branch %s not in %s",
                            cell.name, cell.worktree_branch,
                            cell.worktree_base_branch)
                cell.needs_attention = True
                cell.error_message = (
                    "Merge to main failed — merge manually")
                state.save()
            return  # skip auto-checkpoint on merge turn

        # Auto-checkpoint
        if cell.worktree_path and cell.cell_type == "agent":
            gs = state.get_group_settings(cell.group)
            if gs.worktree_auto_checkpoint:
                msg = _checkpoint_message(cell)
                sha = await worktree_mgr.checkpoint(cell, message=msg)
                if sha:
                    state.save()

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

    # Handle agent turn completion (hook-based session_end)
    event_bus.on_session_end = _on_agent_session_end
    # Also checkpoint when the terminal session is actually closed (tab closed)
    bridge.on_session_terminated = _on_agent_session_end
    await bridge.start()
    await bridge.reconnect_orphans()
    asyncio.create_task(_worktree_diff_updater(state, worktree_mgr))

    # Register RPCs and install global key bindings
    displaced_bindings = await keybindings.setup(connection, state, bridge)

    # -- Command handler ----------------------------------------------------

    async def handle_command(data: dict, ws: web.WebSocketResponse):
        cmd = data.get("cmd")
        log.info("CMD %s %s", cmd,
                 {k: v for k, v in data.items() if k != "cmd"})

        # get_config: respond directly, no state mutation
        if cmd == "get_config":
            try:
                all_profiles = await iterm2.PartialProfile.async_query(
                    connection)
                profile_names = sorted(
                    set(p.name for p in all_profiles if p.name))
            except Exception:
                log.exception("Failed to query profiles")
                profile_names = ["Default"]

            current_path = ""
            current_profile = "Default"
            try:
                app = await iterm2.async_get_app(connection)
                win = app.current_terminal_window
                if win and win.current_tab \
                        and win.current_tab.current_session:
                    sess = win.current_tab.current_session
                    current_path = (
                        await sess.async_get_variable("path") or "")
                    current_profile = (
                        await sess.async_get_variable("profileName")
                        or "Default")
            except Exception:
                log.exception("Failed to get current session info")

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
            await ws.send_str(json.dumps({
                "type": "config",
                "profiles": profile_names,
                "current_path": current_path,
                "current_profile": current_profile,
                "group_cells": group_cells,
                "group_settings": asdict(gs),
            }))
            return

        # get_group_settings: respond directly, no state mutation
        if cmd == "get_group_settings":
            group = data.get("group", "")
            gs = state.get_group_settings(group)
            try:
                all_profiles = await iterm2.PartialProfile.async_query(
                    connection)
                pnames = sorted(
                    set(p.name for p in all_profiles if p.name))
            except Exception:
                log.exception("Failed to query profiles")
                pnames = ["Default"]
            await ws.send_str(json.dumps({
                "type": "group_settings",
                "group": group,
                "settings": asdict(gs),
                "profiles": pnames,
            }))
            return

        try:
            if cmd == "refresh":
                pass

            elif cmd == "add_group":
                state.add_group(data["group"])

            elif cmd == "update_group_settings":
                settings = data.get("settings", {})
                state.update_group_settings(data["group"], **settings)

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
                    event_bus.cleanup_cell(c.id)
                    if c.worktree_path:
                        await worktree_mgr.remove(c)

            elif cmd == "rename_group":
                state.rename_group(data["group"], data["new_name"])

            elif cmd == "add_agent":
                group = data["group"]
                gs = state.get_group_settings(group)
                profile = data.get("profile") or gs.agent_profile or gs.profile or "Default"
                directory = data.get("directory") or gs.agent_directory or gs.default_directory or ""
                _ac = gs.agent_tab_color
                tab_color = data.get("tab_color") or (_ac if _ac != "none" else "") or gs.tab_color or ""
                shell = data.get("shell") or gs.agent_shell or gs.shell or ""
                env = {**gs.env_vars, **gs.agent_env_vars, **(data.get("env_vars") or {})} or None

                command = data.get("command", "") or gs.agent_boot_command
                cell = state.add_agent(
                    name=data["name"], group=group,
                    profile=profile,
                    command=command,
                    directory=directory, tab_color=tab_color,
                )
                if cell:
                    # Git worktree — create before session so cwd is correct
                    if gs.git_worktree and cell.directory:
                        repo_root = await worktree_mgr.get_repo_root(
                            cell.directory)
                        if repo_root:
                            wt_path = await worktree_mgr.create(
                                cell, repo_root,
                                base_dir=gs.worktree_base_dir
                                    or ".loom/worktrees",
                                base_branch=gs.worktree_base_branch or "")
                            if wt_path:
                                cell.directory = wt_path
                                state.save()

                    await bridge.create_session(
                        cell, env_vars=env, shell=shell)

                    # Auto-create child terminals
                    t_profile = gs.terminal_profile or gs.profile or "Default"
                    t_dir = gs.terminal_directory or gs.default_directory or ""
                    _ttc = gs.terminal_tab_color
                    t_color = (_ttc if _ttc != "none" else "") or gs.tab_color or ""
                    t_shell = gs.terminal_shell or gs.shell or ""
                    t_env = {**gs.env_vars, **gs.terminal_env_vars} or None
                    t_cmd = gs.terminal_boot_command or ""
                    if gs.terminal_command_args and t_cmd:
                        t_cmd = (t_cmd + " " + gs.terminal_command_args).strip()
                    for i in range(gs.auto_terminals):
                        t_name = state.next_cell_name(group, "terminal")
                        t = state.add_terminal(
                            name=t_name, group=group,
                            profile=t_profile,
                            command=t_cmd,
                            directory=t_dir or cell.directory,
                            tab_color=t_color,
                            parent_id=cell.id,
                        )
                        if t:
                            await bridge.create_session(
                                t, env_vars=t_env,
                                init_script=gs.terminal_init_script,
                                shell=t_shell)

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
                directory = data.get("directory") or gs.terminal_directory or gs.default_directory or ""
                _tc = gs.terminal_tab_color
                tab_color = data.get("tab_color") or (_tc if _tc != "none" else "") or gs.tab_color or ""
                shell = data.get("shell") or gs.terminal_shell or gs.shell or ""
                env = {**gs.env_vars, **gs.terminal_env_vars, **(data.get("env_vars") or {})} or None
                command = data.get("command") or gs.terminal_boot_command or ""
                cmd_args = data.get("command_args") or gs.terminal_command_args or ""
                if cmd_args and command:
                    command = (command + " " + cmd_args).strip()
                init_script = data.get("init_script") or gs.terminal_init_script or ""

                cell = state.add_terminal(
                    name=data["name"], group=group,
                    profile=profile, command=command,
                    directory=directory, tab_color=tab_color,
                    parent_id=parent_id,
                )
                if cell:
                    await bridge.create_session(
                        cell, env_vars=env,
                        init_script=init_script,
                        shell=shell)

            elif cmd == "remove_agent":
                removed = state.remove_agent(data["id"])
                for c in removed:
                    if c.session_id:
                        await bridge.close_session(c.session_id)
                    # Clean up hooks
                    if c.agent_type and c.directory:
                        adapter = get_adapter(c.agent_type)
                        if hasattr(adapter, "uninstall_hooks"):
                            adapter.uninstall_hooks(
                                os.path.expanduser(c.directory))
                    # Clean up event bus state
                    event_bus.cleanup_cell(c.id)
                    if c.worktree_path:
                        await worktree_mgr.remove(c)

            elif cmd == "update_agent":
                cell = state.agents.get(data["id"])
                if cell:
                    old_name = cell.name
                    new_name = data.get("name", cell.name)
                    new_color = data.get("tab_color", cell.tab_color)
                    state.update_agent(data["id"], name=new_name,
                                       tab_color=new_color)
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
                    state.save()

            elif cmd == "broadcast_to_group":
                for aid in state.groups.get(data["group"], []):
                    cell = state.agents.get(aid)
                    if cell and cell.session_id:
                        await bridge.send_text(
                            cell.session_id, data["text"])
                        cell.status = "running"
                    # Also send to child terminals
                    for child_id in state._children.get(aid, []):
                        child = state.agents.get(child_id)
                        if child and child.session_id:
                            await bridge.send_text(
                                child.session_id, data["text"])
                            child.status = "running"
                state.save()

            elif cmd == "relaunch_agent":
                cell = state.agents.get(data["id"])
                if cell and cell.status == "stopped":
                    gs = state.get_group_settings(cell.group)
                    if cell.cell_type == "terminal":
                        env = {**gs.env_vars, **gs.terminal_env_vars} or None
                        shell = gs.terminal_shell or gs.shell or ""
                        init = gs.terminal_init_script
                    else:
                        env = {**gs.env_vars, **gs.agent_env_vars} or None
                        shell = gs.agent_shell or gs.shell or ""
                        init = ""
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
                                state.save()
                        # Create new worktree if enabled and none exists
                        if not cell.worktree_path and gs.git_worktree \
                                and cell.directory:
                            repo_root = await worktree_mgr.get_repo_root(
                                cell.directory)
                            if repo_root:
                                wt_path = await worktree_mgr.create(
                                    cell, repo_root,
                                    base_dir=gs.worktree_base_dir
                                        or ".loom/worktrees",
                                    base_branch=gs.worktree_base_branch
                                        or "")
                                if wt_path:
                                    cell.directory = wt_path
                                    state.save()
                    await bridge.create_session(
                        cell, env_vars=env,
                        init_script=init, shell=shell)

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

            elif cmd == "worktree_create":
                cell = state.agents.get(data["id"])
                if cell and not cell.worktree_path and cell.directory:
                    gs = state.get_group_settings(cell.group)
                    repo_root = await worktree_mgr.get_repo_root(
                        cell.directory)
                    if repo_root:
                        wt_path = await worktree_mgr.create(
                            cell, repo_root,
                            base_dir=gs.worktree_base_dir
                                or ".loom/worktrees",
                            base_branch=gs.worktree_base_branch or "")
                        if wt_path:
                            cell.directory = wt_path
                            state.save()
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
                                env = {**gs.env_vars,
                                       **gs.agent_env_vars} or None
                                shell = (gs.agent_shell
                                         or gs.shell or "")
                                await bridge.create_session(
                                    cell, env_vars=env, shell=shell)

            elif cmd == "worktree_remove":
                cell = state.agents.get(data["id"])
                if cell and cell.worktree_path:
                    # Restore directory to original repo root
                    repo_root = cell.worktree_repo_root
                    await worktree_mgr.remove(cell)
                    if repo_root:
                        cell.directory = repo_root
                    state.save()
                    # Relaunch if requested by the UI
                    if data.get("relaunch") and cell.cell_type == "agent":
                        if cell.session_id:
                            await bridge.close_session(cell.session_id)
                        cell.status = "stopped"
                        cell.session_id = None
                        cell.agent_session_id = ""
                        gs = state.get_group_settings(cell.group)
                        env = {**gs.env_vars,
                               **gs.agent_env_vars} or None
                        shell = gs.agent_shell or gs.shell or ""
                        await bridge.create_session(
                            cell, env_vars=env, shell=shell)

            elif cmd == "worktree_checkpoint":
                cell = state.agents.get(data["id"])
                if cell and cell.worktree_path:
                    msg = _checkpoint_message(cell)
                    await worktree_mgr.checkpoint(cell, message=msg)
                    state.save()

            elif cmd == "worktree_history":
                cell = state.agents.get(data.get("id", ""))
                commits = []
                if cell and cell.worktree_path:
                    commits = await worktree_mgr.list_checkpoints(cell)
                await ws.send_str(json.dumps({
                    "type": "worktree_history",
                    "id": data.get("id", ""),
                    "branch": cell.worktree_branch if cell else "",
                    "base_branch": cell.worktree_base_branch if cell else "",
                    "commits": commits,
                }))
                return  # direct response, no broadcast

            elif cmd == "worktree_rollback":
                cell = state.agents.get(data.get("id", ""))
                sha = data.get("sha", "")
                if cell and cell.worktree_path and sha:
                    await worktree_mgr.rollback(cell, sha)
                    state.save()

            elif cmd == "worktree_merge":
                cell = state.agents.get(data.get("id", ""))
                if cell and cell.worktree_path and cell.worktree_branch:
                    if not cell.session_id:
                        await ws.send_str(json.dumps({
                            "type": "error",
                            "message": "Session not running. Relaunch "
                                       "the agent first, or merge manually.",
                        }))
                    else:
                        gs = state.get_group_settings(cell.group)
                        base = cell.worktree_base_branch or "main"
                        branch = cell.worktree_branch
                        repo = cell.worktree_repo_root or ""
                        squash = gs.worktree_merge_squash
                        method = ("Squash merge" if squash
                                  else "Merge")
                        prompt = (
                            f"{method} the current branch "
                            f"`{branch}` into `{base}`. The "
                            f"main repo is at `{repo}`. If there "
                            f"are merge conflicts, resolve them. "
                            f"Do not delete the worktree branch "
                            f"after merging.")
                        extra = gs.worktree_merge_instructions.strip()
                        if extra:
                            prompt += " " + extra
                        await bridge.send_text(
                            cell.session_id, prompt + "\r")
                        _pending_merges.add(cell.id)
                        cell.status = "running"
                        state.save()

            elif cmd == "restart":
                log.info("Restart requested — cleaning up and re-executing")
                await keybindings.remove(connection, displaced_bindings)
                state.save()
                os.execv(sys.executable, [sys.executable] + sys.argv)

        except Exception as exc:
            log.exception("Command '%s' failed", cmd)
            await ws.send_str(
                json.dumps({"type": "error", "message": str(exc)}))

        await state.broadcast()

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

    # -- HTTP / WS routes ---------------------------------------------------

    async def handle_index(_request):
        from .config import WEBVIEW_FILE  # re-read after init_paths
        return web.FileResponse(WEBVIEW_FILE)

    async def handle_ws(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        state._ws_clients.add(ws)
        await ws.send_str(json.dumps({"type": "state", **state.to_dict()}))
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        await handle_command(json.loads(msg.data), ws)
                    except json.JSONDecodeError:
                        log.warning("Received malformed JSON from webview")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
        finally:
            state._ws_clients.discard(ws)
        return ws

    # -- Start server -------------------------------------------------------

    app_server = web.Application()
    app_server.router.add_get("/", handle_index)
    app_server.router.add_get("/ws", handle_ws)
    app_server.router.add_post("/events", handle_events)
    from .config import SCRIPT_DIR
    app_server.router.add_static("/static", SCRIPT_DIR / "static")

    runner = web.AppRunner(app_server)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", WS_PORT, reuse_address=True)
    try:
        await site.start()
    except OSError as exc:
        log.error("Cannot bind port %d: %s — is another instance running?",
                  WS_PORT, exc)
        raise
    log.info("HTTP/WS server listening on 127.0.0.1:%d", WS_PORT)

    # -- Register toolbelt --------------------------------------------------

    await iterm2.tool.async_register_web_view_tool(
        connection,
        display_name="Loom",
        identifier="com.loom.toolbelt",
        reveal_if_already_registered=True,
        url=f"http://127.0.0.1:{WS_PORT}/",
    )
    log.info("Toolbelt webview registered — Loom ready")

    await asyncio.Future()
