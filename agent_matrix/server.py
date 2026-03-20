"""aiohttp server, WebSocket command handler, and iTerm2 entry point."""

import asyncio
import json
import os
import sys

import aiohttp
from aiohttp import web
import iterm2

from .config import WS_PORT, WEBVIEW_FILE, log
from .state import MatrixState
from .bridge import ITerm2Bridge


async def main(connection: iterm2.Connection):
    log.info("Agent Matrix starting (port=%d)", WS_PORT)
    state = MatrixState()
    state.load()
    log.info("State loaded: %d agents, %d groups",
             len(state.agents), len(state.groups))

    bridge = ITerm2Bridge(connection, state)
    await bridge.start()
    await bridge.reconnect_orphans()

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

            await ws.send_str(json.dumps({
                "type": "config",
                "profiles": profile_names,
                "current_path": current_path,
                "current_profile": current_profile,
                "group_cells": group_cells,
            }))
            return

        try:
            if cmd == "refresh":
                pass

            elif cmd == "add_group":
                state.add_group(data["group"])

            elif cmd == "remove_group":
                removed = state.remove_group(data["group"])
                for c in removed:
                    if c.session_id:
                        await bridge.close_session(c.session_id)

            elif cmd == "rename_group":
                state.rename_group(data["group"], data["new_name"])

            elif cmd == "add_agent":
                cell = state.add_agent(
                    name=data["name"],
                    group=data["group"],
                    profile=data.get("profile", "Default"),
                    command=data.get("command", ""),
                    directory=data.get("directory", ""),
                    tab_color=data.get("tab_color", ""),
                )
                if cell:
                    await bridge.create_session(cell)

            elif cmd == "add_terminal":
                cell = state.add_terminal(
                    name=data["name"],
                    group=data["group"],
                    profile=data.get("profile", "Default"),
                    directory=data.get("directory", ""),
                    tab_color=data.get("tab_color", ""),
                )
                if cell:
                    await bridge.create_session(cell)

            elif cmd == "remove_agent":
                cell = state.remove_agent(data["id"])
                if cell and cell.session_id:
                    await bridge.close_session(cell.session_id)

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
                state.save()

            elif cmd == "relaunch_agent":
                cell = state.agents.get(data["id"])
                if cell and cell.status == "stopped":
                    await bridge.create_session(cell)

            elif cmd == "move_agent":
                state.move_agent(data["id"], data["target_group"],
                                 data.get("before", ""))
                await bridge.reorder_tabs()

            elif cmd == "restart":
                log.info("Restart requested — re-executing")
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
        display_name="Agent Matrix",
        identifier="com.agentmatrix.toolbelt",
        reveal_if_already_registered=True,
        url=f"http://127.0.0.1:{WS_PORT}/",
    )
    log.info("Toolbelt webview registered — Agent Matrix ready")

    await asyncio.Future()
