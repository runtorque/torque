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
from . import keybindings


async def main(connection: iterm2.Connection):
    log.info("Loom starting (port=%d)", WS_PORT)
    state = MatrixState()
    state.load()
    log.info("State loaded: %d agents, %d groups",
             len(state.agents), len(state.groups))

    bridge = ITerm2Bridge(connection, state)
    await bridge.start()
    await bridge.reconnect_orphans()

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
                    if c.worktree_path:
                        await bridge.remove_worktree(c)

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

                cell = state.add_agent(
                    name=data["name"], group=group,
                    profile=profile,
                    command=data.get("command", ""),
                    directory=directory, tab_color=tab_color,
                )
                if cell:
                    # Git worktree
                    if gs.git_worktree and cell.directory:
                        wt_path = await bridge.create_worktree(
                            cell, cell.directory)
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
                    if c.worktree_path:
                        await bridge.remove_worktree(c)

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
