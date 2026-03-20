#!/usr/bin/env python3
"""Agent Matrix - iTerm2 Toolbelt plugin for managing AI agent sessions."""

import asyncio
import json
import logging
import os
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import web
import iterm2

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WS_PORT = int(os.environ.get("AGENT_MATRIX_PORT", 18932))
DEFAULT_COMMAND = os.environ.get("AGENT_MATRIX_DEFAULT_CMD", "claude")
SCRIPT_DIR = Path(__file__).parent
STATE_FILE = SCRIPT_DIR / "state.json"
WEBVIEW_FILE = SCRIPT_DIR / "webview.html"
LOG_FILE = SCRIPT_DIR / "agent_matrix.log"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log = logging.getLogger("agent_matrix")
log.setLevel(logging.DEBUG)
_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
))
log.addHandler(_fh)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
@dataclass
class AgentCell:
    id: str
    name: str
    group: str
    cell_type: str = "agent"  # "agent" | "terminal"
    session_id: Optional[str] = None
    profile: str = "Default"
    command: str = ""
    status: str = "stopped"  # idle | running | error | stopped
    current_process: str = ""  # foreground job name (tracked for terminals)
    current_path: str = ""  # working directory (tracked for terminals)
    current_branch: str = ""  # git branch (empty if not in a repo)
    git_root: str = ""  # git repo root (empty if not in a repo)


class MatrixState:
    """In-memory state for all groups and agents, with JSON persistence."""

    def __init__(self):
        self.agents: dict[str, AgentCell] = {}
        self.groups: dict[str, list[str]] = {}  # group_name -> [agent_ids]
        self.active_session_id: Optional[str] = None
        self._ws_clients: set[web.WebSocketResponse] = set()

    # -- Serialization ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "agents": {aid: asdict(a) for aid, a in self.agents.items()},
            "groups": self.groups,
            "active_session_id": self.active_session_id,
        }

    def save(self):
        payload = {
            "agents": {aid: asdict(a) for aid, a in self.agents.items()},
            "groups": self.groups,
        }
        STATE_FILE.write_text(json.dumps(payload, indent=2))

    def load(self):
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text())
            fields = set(AgentCell.__dataclass_fields__)
            for aid, raw in data.get("agents", {}).items():
                filtered = {k: v for k, v in raw.items() if k in fields}
                cell = AgentCell(**filtered)
                cell.status = "stopped"
                cell.session_id = None
                cell.current_process = ""
                cell.current_path = ""
                cell.current_branch = ""
                cell.git_root = ""
                self.agents[aid] = cell
            self.groups = data.get("groups", {})
            for gname in list(self.groups):
                self.groups[gname] = [
                    aid for aid in self.groups[gname] if aid in self.agents
                ]
        except (json.JSONDecodeError, TypeError, KeyError):
            log.exception("Failed to load state from %s", STATE_FILE)

    # -- Mutations ----------------------------------------------------------

    def add_group(self, name: str):
        if name and name not in self.groups:
            self.groups[name] = []
            self.save()

    def remove_group(self, name: str) -> list[AgentCell]:
        removed: list[AgentCell] = []
        if name in self.groups:
            for aid in self.groups[name]:
                cell = self.agents.pop(aid, None)
                if cell:
                    removed.append(cell)
            del self.groups[name]
            self.save()
        return removed

    def rename_group(self, old: str, new: str):
        if old in self.groups and new and new not in self.groups:
            self.groups[new] = self.groups.pop(old)
            for aid in self.groups[new]:
                if aid in self.agents:
                    self.agents[aid].group = new
            self.save()

    def _add_cell(
        self,
        name: str,
        group: str,
        cell_type: str,
        profile: str = "Default",
        command: str = "",
    ) -> Optional[AgentCell]:
        if group not in self.groups:
            return None
        aid = uuid.uuid4().hex[:8]
        cell = AgentCell(
            id=aid,
            name=name,
            group=group,
            cell_type=cell_type,
            profile=profile,
            command=command or (DEFAULT_COMMAND if cell_type == "agent" else ""),
        )
        self.agents[aid] = cell
        self.groups[group].append(aid)
        self.save()
        return cell

    def add_agent(
        self,
        name: str,
        group: str,
        profile: str = "Default",
        command: str = "",
    ) -> Optional[AgentCell]:
        return self._add_cell(name, group, "agent", profile, command)

    def add_terminal(
        self,
        name: str,
        group: str,
        profile: str = "Default",
    ) -> Optional[AgentCell]:
        return self._add_cell(name, group, "terminal", profile, "")

    def remove_agent(self, aid: str) -> Optional[AgentCell]:
        cell = self.agents.pop(aid, None)
        if cell and cell.group in self.groups:
            self.groups[cell.group] = [
                x for x in self.groups[cell.group] if x != aid
            ]
            self.save()
        return cell

    def move_agent(self, aid: str, target_group: str):
        cell = self.agents.get(aid)
        if not cell or target_group not in self.groups:
            return
        if cell.group in self.groups:
            self.groups[cell.group] = [
                x for x in self.groups[cell.group] if x != aid
            ]
        self.groups[target_group].append(aid)
        cell.group = target_group
        self.save()

    # -- WebSocket broadcast ------------------------------------------------

    async def broadcast(self):
        snapshot = json.dumps({"type": "state", **self.to_dict()})
        dead: set[web.WebSocketResponse] = set()
        for ws in self._ws_clients:
            try:
                await ws.send_str(snapshot)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead


# ---------------------------------------------------------------------------
# iTerm2 bridge
# ---------------------------------------------------------------------------
class ITerm2Bridge:
    """Translates matrix commands into iTerm2 Python API calls."""

    def __init__(self, connection: iterm2.Connection, state: MatrixState):
        self.conn = connection
        self.state = state
        self._prompt_tasks: dict[str, asyncio.Task] = {}
        self._job_tasks: dict[str, asyncio.Task] = {}
        self._term_task: Optional[asyncio.Task] = None

    async def start(self):
        self._term_task = asyncio.create_task(self._watch_terminations())

    # -- Session lifecycle --------------------------------------------------

    async def create_session(self, cell: AgentCell):
        log.info("Creating session for %s '%s' [%s]", cell.cell_type, cell.name, cell.group)
        app = await iterm2.async_get_app(self.conn)
        window = app.current_window
        if not window:
            log.error("No current window — cannot create tab for '%s'", cell.name)
            cell.status = "error"
            self.state.save()
            return

        tab = await window.async_create_tab(profile=cell.profile)
        session = tab.current_session
        cell.session_id = session.session_id
        log.info("Tab created: session_id=%s", session.session_id)
        await tab.async_set_title(f"[{cell.group}] {cell.name}")

        if cell.command:
            await session.async_send_text(cell.command + "\n")
            cell.status = "running"
        else:
            cell.status = "idle"

        # For terminals, seed live variables and start monitors
        if cell.cell_type == "terminal":
            try:
                job = await session.async_get_variable("jobName")
                cell.current_process = job or ""
            except Exception:
                log.exception("Failed to read initial jobName for '%s'", cell.name)
            try:
                path = await session.async_get_variable("path")
                cell.current_path = path or ""
            except Exception:
                log.exception("Failed to read initial path for '%s'", cell.name)
            await self._resolve_git_info(cell)
            self._start_terminal_monitors(cell)

        self.state.save()
        self._start_prompt_monitor(cell)
        await self.reorder_tabs()

    async def reorder_tabs(self):
        """Keep managed tabs last, ordered by group then position within group."""
        try:
            app = await iterm2.async_get_app(self.conn)
            window = app.current_window
            if not window:
                return

            # Build a sort key for managed session_ids: (group_index, position)
            managed_sids: dict[str, tuple[int, int]] = {}
            for gi, gname in enumerate(self.state.groups):
                for pos, aid in enumerate(self.state.groups[gname]):
                    cell = self.state.agents.get(aid)
                    if cell and cell.session_id:
                        managed_sids[cell.session_id] = (gi, pos)

            unmanaged = []
            managed = []
            for tab in window.tabs:
                sid = tab.current_session.session_id if tab.current_session else None
                if sid in managed_sids:
                    managed.append((managed_sids[sid], tab))
                else:
                    unmanaged.append(tab)

            if not managed:
                return

            managed.sort(key=lambda pair: pair[0])
            new_order = unmanaged + [tab for _, tab in managed]

            # Only call API if the order actually changed
            if [t.tab_id for t in new_order] != [t.tab_id for t in window.tabs]:
                await window.async_set_tabs(new_order)
                log.debug("Tabs reordered: %d unmanaged + %d managed",
                          len(unmanaged), len(managed))
        except Exception:
            log.exception("Failed to reorder tabs")

    async def close_session(self, session_id: str):
        session = await self._find_session(session_id)
        if session:
            try:
                await session.async_close(force=True)
            except Exception:
                log.exception("Failed to close session %s", session_id)

    async def focus_session(self, session_id: str) -> bool:
        app = await iterm2.async_get_app(self.conn)
        for window in app.windows:
            for tab in window.tabs:
                for session in tab.sessions:
                    if session.session_id == session_id:
                        await tab.async_activate()
                        await session.async_activate()
                        return True
        return False

    async def send_text(self, session_id: str, text: str):
        session = await self._find_session(session_id)
        if session:
            await session.async_send_text(text)

    # -- Helpers ------------------------------------------------------------

    async def _find_session(self, session_id: str):
        if not session_id:
            return None
        app = await iterm2.async_get_app(self.conn)
        for window in app.windows:
            for tab in window.tabs:
                for session in tab.sessions:
                    if session.session_id == session_id:
                        return session
        return None

    # -- Monitoring ---------------------------------------------------------

    def _start_prompt_monitor(self, cell: AgentCell):
        if cell.id in self._prompt_tasks:
            self._prompt_tasks[cell.id].cancel()
        self._prompt_tasks[cell.id] = asyncio.create_task(
            self._monitor_prompt(cell)
        )

    def _start_terminal_monitors(self, cell: AgentCell):
        old = self._job_tasks.pop(cell.id, None)
        if old:
            old.cancel()
        self._job_tasks[cell.id] = asyncio.create_task(
            self._monitor_terminal_vars(cell)
        )

    async def _monitor_prompt(self, cell: AgentCell):
        """Watch for shell prompt appearances (idle transitions)."""
        try:
            async with iterm2.PromptMonitor(self.conn, cell.session_id) as mon:
                while True:
                    await mon.async_get()
                    if cell.session_id:
                        cell.status = "idle"
                        self.state.save()
                        await self.state.broadcast()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("PromptMonitor failed for '%s' (session %s)", cell.name, cell.session_id)

    async def _resolve_git_info(self, cell: AgentCell):
        """Run git to detect repo root and branch for the cell's current path."""
        path = cell.current_path
        if not path:
            cell.current_branch = ""
            cell.git_root = ""
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", path, "rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                lines = stdout.decode().strip().splitlines()
                cell.git_root = lines[0] if len(lines) > 0 else ""
                cell.current_branch = lines[1] if len(lines) > 1 else ""
            else:
                cell.git_root = ""
                cell.current_branch = ""
        except Exception:
            log.exception("git resolve failed for '%s' at %s", cell.name, path)
            cell.git_root = ""
            cell.current_branch = ""

    async def _monitor_terminal_vars(self, cell: AgentCell):
        """Watch jobName and path changes for terminal cells."""

        async def _watch_job():
            try:
                async with iterm2.VariableMonitor(
                    self.conn, iterm2.VariableScopes.SESSION,
                    "jobName", cell.session_id,
                ) as mon:
                    while True:
                        val = await mon.async_get()
                        cell.current_process = val or ""
                        log.debug("Job changed for '%s': %s", cell.name, val)
                        self.state.save()
                        await self.state.broadcast()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("jobName monitor failed for '%s' (session %s)",
                              cell.name, cell.session_id)

        async def _watch_path():
            try:
                async with iterm2.VariableMonitor(
                    self.conn, iterm2.VariableScopes.SESSION,
                    "path", cell.session_id,
                ) as mon:
                    while True:
                        val = await mon.async_get()
                        cell.current_path = val or ""
                        log.debug("Path changed for '%s': %s", cell.name, val)
                        await self._resolve_git_info(cell)
                        self.state.save()
                        await self.state.broadcast()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("path monitor failed for '%s' (session %s)",
                              cell.name, cell.session_id)

        await asyncio.gather(_watch_job(), _watch_path())

    async def _watch_terminations(self):
        """Global monitor: detect when any tracked session closes."""
        try:
            async with iterm2.SessionTerminationMonitor(self.conn) as mon:
                while True:
                    sid = await mon.async_get()
                    for cell in self.state.agents.values():
                        if cell.session_id == sid:
                            log.info("Session terminated: '%s' (session %s)", cell.name, sid)
                            cell.status = "stopped"
                            cell.session_id = None
                            cell.current_process = ""
                            cell.current_path = ""
                            cell.current_branch = ""
                            cell.git_root = ""
                            self.state.save()
                            for tasks in (self._prompt_tasks, self._job_tasks):
                                task = tasks.pop(cell.id, None)
                                if task:
                                    task.cancel()
                            await self.state.broadcast()
                            break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("SessionTerminationMonitor failed")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main(connection: iterm2.Connection):
    log.info("Agent Matrix starting (port=%d, default_cmd=%s)", WS_PORT, DEFAULT_COMMAND)
    state = MatrixState()
    state.load()
    log.info("State loaded: %d agents, %d groups", len(state.agents), len(state.groups))

    bridge = ITerm2Bridge(connection, state)
    await bridge.start()

    # -- Command handler ----------------------------------------------------

    async def handle_command(data: dict, ws: web.WebSocketResponse):
        cmd = data.get("cmd")
        log.info("CMD %s %s", cmd, {k: v for k, v in data.items() if k != "cmd"})
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
                )
                if cell:
                    await bridge.create_session(cell)

            elif cmd == "add_terminal":
                cell = state.add_terminal(
                    name=data["name"],
                    group=data["group"],
                    profile=data.get("profile", "Default"),
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
                        await bridge.send_text(cell.session_id, data["text"])
                        cell.status = "running"
                state.save()

            elif cmd == "relaunch_agent":
                cell = state.agents.get(data["id"])
                if cell and cell.status == "stopped":
                    await bridge.create_session(cell)

            elif cmd == "move_agent":
                state.move_agent(data["id"], data["target_group"])
                await bridge.reorder_tabs()

            elif cmd == "restart":
                log.info("Restart requested — re-executing")
                state.save()
                os.execv(sys.executable, [sys.executable] + sys.argv)

        except Exception as exc:
            log.exception("Command '%s' failed", cmd)
            await ws.send_str(
                json.dumps({"type": "error", "message": str(exc)})
            )

        await state.broadcast()

    # -- HTTP / WS routes ---------------------------------------------------

    async def handle_index(_request):
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

    runner = web.AppRunner(app_server)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", WS_PORT, reuse_address=True)
    try:
        await site.start()
    except OSError as exc:
        log.error("Cannot bind port %d: %s — is another instance still running?", WS_PORT, exc)
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


iterm2.run_forever(main)
