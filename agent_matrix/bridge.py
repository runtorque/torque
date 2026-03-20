"""iTerm2 bridge — translates matrix commands into iTerm2 Python API calls."""

import asyncio
import re
import shlex
from typing import Optional

import iterm2

from .config import log
from .state import AgentCell, MatrixState

_TITLE_RE = re.compile(r"^\[(.+?)\]\s+(.+)$")


class ITerm2Bridge:

    def __init__(self, connection: iterm2.Connection, state: MatrixState):
        self.conn = connection
        self.state = state
        self._prompt_tasks: dict[str, asyncio.Task] = {}
        self._job_tasks: dict[str, asyncio.Task] = {}
        self._term_task: Optional[asyncio.Task] = None

    async def start(self):
        self._term_task = asyncio.create_task(self._watch_terminations())

    async def reconnect_orphans(self):
        """Re-link persisted cells to existing iTerm2 sessions after restart."""
        app = await iterm2.async_get_app(self.conn)

        # Index stopped cells by persisted session_id and by (group, name)
        by_sid: dict[str, AgentCell] = {}
        by_title: dict[tuple[str, str], AgentCell] = {}
        for cell in self.state.agents.values():
            if cell.status != "stopped":
                continue
            if cell.session_id:
                by_sid[cell.session_id] = cell
            by_title[(cell.group, cell.name)] = cell

        if not by_sid and not by_title:
            log.info("Orphan reconnect: no stopped cells to re-link")
            return

        matched: set[str] = set()  # cell ids that were matched

        for window in app.windows:
            for tab in window.tabs:
                session = tab.current_session
                if not session:
                    continue
                sid = session.session_id
                cell = None

                # Primary: match by persisted session_id
                if sid in by_sid:
                    cell = by_sid[sid]

                # Secondary: match by tab title "[group] name"
                if not cell:
                    title = getattr(session, "name", None)
                    if title:
                        m = _TITLE_RE.match(title)
                        if m:
                            key = (m.group(1), m.group(2))
                            candidate = by_title.get(key)
                            if candidate and candidate.id not in matched:
                                cell = candidate

                if not cell or cell.id in matched:
                    continue

                matched.add(cell.id)
                cell.session_id = sid
                cell.status = "idle"
                log.info("Reconnected '%s' [%s] → session %s",
                         cell.name, cell.group, sid)

                # Seed ephemeral fields
                try:
                    path = await session.async_get_variable("path")
                    cell.current_path = path or ""
                except Exception:
                    log.debug("Could not read path for reconnected '%s'",
                              cell.name)

                if cell.cell_type == "terminal":
                    try:
                        job = await session.async_get_variable("jobName")
                        cell.current_process = job or ""
                    except Exception:
                        log.debug("Could not read jobName for '%s'",
                                  cell.name)
                    await self._resolve_git_info(cell)
                    self._start_terminal_monitors(cell)

                self._start_prompt_monitor(cell)

        # Clear session_id for cells that weren't matched (sessions truly gone)
        for cell in self.state.agents.values():
            if cell.status == "stopped" and cell.session_id \
                    and cell.id not in matched:
                log.debug("Session %s gone for '%s' — clearing",
                          cell.session_id, cell.name)
                cell.session_id = None

        self.state.save()
        stopped = sum(1 for c in self.state.agents.values()
                      if c.status == "stopped")
        log.info("Orphan reconnect: %d re-linked, %d remain stopped",
                 len(matched), stopped)

    # -- Session lifecycle --------------------------------------------------

    async def create_session(self, cell: AgentCell):
        log.info("Creating session for %s '%s' [%s]",
                 cell.cell_type, cell.name, cell.group)
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

        # Tab color — set all variants to cover both unified and split modes
        if cell.tab_color:
            try:
                r = int(cell.tab_color[1:3], 16)
                g = int(cell.tab_color[3:5], 16)
                b = int(cell.tab_color[5:7], 16)
                color = iterm2.Color(r, g, b)
                change = iterm2.LocalWriteOnlyProfile()
                change.set_use_tab_color(True)
                change.set_use_tab_color_light(True)
                change.set_use_tab_color_dark(True)
                change.set_tab_color(color)
                change.set_tab_color_light(color)
                change.set_tab_color_dark(color)
                await session.async_set_profile_properties(change)
                log.info("Tab color set for '%s': #%02x%02x%02x",
                         cell.name, r, g, b)
            except Exception:
                log.exception("Failed to set tab color for '%s'", cell.name)

        # Directory
        if cell.directory:
            await session.async_send_text(
                f"cd {shlex.quote(cell.directory)}\n")

        # Boot command
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
                log.exception("Failed to read initial jobName for '%s'",
                              cell.name)
            try:
                path = await session.async_get_variable("path")
                cell.current_path = path or ""
            except Exception:
                log.exception("Failed to read initial path for '%s'",
                              cell.name)
            await self._resolve_git_info(cell)
            self._start_terminal_monitors(cell)

        self.state.save()
        self._start_prompt_monitor(cell)
        await self.reorder_tabs()

    async def reorder_tabs(self):
        """Keep managed tabs last, ordered by group then position."""
        try:
            app = await iterm2.async_get_app(self.conn)
            window = app.current_window
            if not window:
                return

            managed_sids: dict[str, tuple[int, int]] = {}
            for gi, gname in enumerate(self.state.groups):
                for pos, aid in enumerate(self.state.groups[gname]):
                    cell = self.state.agents.get(aid)
                    if cell and cell.session_id:
                        managed_sids[cell.session_id] = (gi, pos)

            unmanaged = []
            managed = []
            for tab in window.tabs:
                sid = (tab.current_session.session_id
                       if tab.current_session else None)
                if sid in managed_sids:
                    managed.append((managed_sids[sid], tab))
                else:
                    unmanaged.append(tab)

            if not managed:
                return

            managed.sort(key=lambda pair: pair[0])
            new_order = unmanaged + [tab for _, tab in managed]

            if [t.tab_id for t in new_order] != \
               [t.tab_id for t in window.tabs]:
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
            self._monitor_prompt(cell))

    def _start_terminal_monitors(self, cell: AgentCell):
        old = self._job_tasks.pop(cell.id, None)
        if old:
            old.cancel()
        self._job_tasks[cell.id] = asyncio.create_task(
            self._monitor_terminal_vars(cell))

    async def _monitor_prompt(self, cell: AgentCell):
        try:
            async with iterm2.PromptMonitor(
                    self.conn, cell.session_id) as mon:
                while True:
                    await mon.async_get()
                    if cell.session_id:
                        cell.status = "idle"
                        self.state.save()
                        await self.state.broadcast()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("PromptMonitor failed for '%s' (session %s)",
                          cell.name, cell.session_id)

    async def _resolve_git_info(self, cell: AgentCell):
        path = cell.current_path
        if not path:
            cell.current_branch = ""
            cell.git_root = ""
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", path, "rev-parse",
                "--show-toplevel", "--abbrev-ref", "HEAD",
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
            log.exception("git resolve failed for '%s' at %s",
                          cell.name, path)
            cell.git_root = ""
            cell.current_branch = ""

    async def _monitor_terminal_vars(self, cell: AgentCell):
        async def _watch_job():
            try:
                async with iterm2.VariableMonitor(
                    self.conn, iterm2.VariableScopes.SESSION,
                    "jobName", cell.session_id,
                ) as mon:
                    while True:
                        val = await mon.async_get()
                        cell.current_process = val or ""
                        log.debug("Job changed for '%s': %s",
                                  cell.name, val)
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
                        log.debug("Path changed for '%s': %s",
                                  cell.name, val)
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
        try:
            async with iterm2.SessionTerminationMonitor(self.conn) as mon:
                while True:
                    sid = await mon.async_get()
                    for cell in self.state.agents.values():
                        if cell.session_id == sid:
                            log.info("Session terminated: '%s' (session %s)",
                                     cell.name, sid)
                            cell.status = "stopped"
                            cell.session_id = None
                            cell.current_process = ""
                            cell.current_path = ""
                            cell.current_branch = ""
                            cell.git_root = ""
                            self.state.save()
                            for tasks in (self._prompt_tasks,
                                          self._job_tasks):
                                task = tasks.pop(cell.id, None)
                                if task:
                                    task.cancel()
                            await self.state.broadcast()
                            break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("SessionTerminationMonitor failed")
