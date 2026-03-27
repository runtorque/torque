"""iTerm2 bridge — translates matrix commands into iTerm2 Python API calls."""

import asyncio
import os
import re
import shlex
from typing import Optional

import iterm2

from .config import log
from .state import AgentCell, MatrixState
from .adapters import detect_agent_type, detect_by_command, get_adapter

_TITLE_RE = re.compile(r"^\[(.+?)\]\s+(.+)$")


class ITerm2Bridge:

    def __init__(self, connection: iterm2.Connection, state: MatrixState):
        self.conn = connection
        self.state = state
        self._prompt_tasks: dict[str, asyncio.Task] = {}
        self._job_tasks: dict[str, asyncio.Task] = {}
        self._term_task: Optional[asyncio.Task] = None
        self._focus_task: Optional[asyncio.Task] = None
        self.on_session_terminated = None  # async callback(cell)
        self.on_terminal_disconnected = None  # async callback(cell) — auto-remove

    async def start(self):
        self._term_task = asyncio.create_task(self._watch_terminations())
        self._focus_task = asyncio.create_task(self._watch_focus())
        # Seed current window and active session from the focused state at startup
        try:
            app = await iterm2.async_get_app(self.conn)
            if app.current_window:
                self.state.current_window_id = app.current_window.window_id
                tab = app.current_window.current_tab
                if tab and tab.current_session:
                    self.state.active_session_id = tab.current_session.session_id
        except Exception:
            log.debug("Could not seed current_window/session at startup")

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
                cell.window_id = window.window_id
                # Awareness agents start as idle — hooks will update
                # Non-awareness agents with a boot command assume running
                if cell.agent_type:
                    cell.status = "idle"
                else:
                    cell.status = "running" if cell.command else "idle"
                log.info("Reconnected '%s' [%s] → session %s (window %s, "
                         "status=%s)",
                         cell.name, cell.group, sid, window.window_id,
                         cell.status)

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
                    self._start_terminal_monitors(cell)

                # Resolve git info for all cell types
                await self._resolve_git_info(cell)

                # Re-install hooks for awareness agents (in case files
                # were lost or working dir changed)
                if cell.agent_type and cell.directory:
                    adapter = get_adapter(cell.agent_type)
                    hook_dir = os.path.expanduser(cell.directory)
                    if hasattr(adapter, "install_hooks"):
                        if adapter.install_hooks(hook_dir):
                            log.info("Re-installed hooks for '%s' in %s",
                                     cell.name, hook_dir)

                self._start_prompt_monitor(cell)

        # Clear session_id for cells that weren't matched (sessions truly gone)
        for cell in self.state.agents.values():
            if cell.status == "stopped" and cell.session_id \
                    and cell.id not in matched:
                log.debug("Session %s gone for '%s' — clearing",
                          cell.session_id, cell.name)
                cell.session_id = None

        # Validate worktree paths still exist on disk
        for cell in self.state.agents.values():
            if cell.worktree_path and not os.path.isdir(cell.worktree_path):
                log.warning("Worktree path gone for '%s': %s — clearing",
                            cell.name, cell.worktree_path)
                cell.worktree_path = ""
                cell.worktree_branch = ""
                cell.worktree_repo_root = ""
                cell.worktree_base_branch = ""

        for cell in self.state.agents.values():
            self.state._emit_agent(cell)
            self.state._db_save_agent(cell)
        stopped = sum(1 for c in self.state.agents.values()
                      if c.status == "stopped")
        log.info("Orphan reconnect: %d re-linked, %d remain stopped",
                 len(matched), stopped)

    # -- Session lifecycle --------------------------------------------------

    async def create_session(self, cell: AgentCell, *,
                             env_vars: dict[str, str] | None = None,
                             init_script: str = "",
                             shell: str = ""):
        log.info("Creating session for %s '%s' [%s]",
                 cell.cell_type, cell.name, cell.group)
        app = await iterm2.async_get_app(self.conn)
        window = app.current_window
        if not window:
            log.error("No current window — cannot create tab for '%s'",
                      cell.name)
            cell.status = "error"
            self.state._emit_agent(cell)
            self.state._db_save_agent(cell)
            return

        # Remember the active tab so we can restore focus if needed
        prev_tab = window.current_tab

        tab = await window.async_create_tab(profile=cell.profile)
        session = tab.current_session
        cell.session_id = session.session_id
        cell.window_id = window.window_id
        log.info("Tab created: session_id=%s window=%s",
                 session.session_id, window.window_id)
        if cell.parent_id:
            parent = self.state.agents.get(cell.parent_id)
            parent_name = parent.name if parent else "?"
            await tab.async_set_title(
                f"[{cell.group}] {parent_name}/{cell.name}")
        else:
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

        # Shell override
        if shell:
            await session.async_send_text(f"exec {shell}\n")
            await asyncio.sleep(0.3)

        # Directory (expanduser so ~ works even when quoted)
        if cell.directory:
            d = os.path.expanduser(cell.directory)
            await session.async_send_text(f"cd {shlex.quote(d)}\n")

        # Auto-detect agent type from boot command
        if cell.cell_type == "agent" and not cell.agent_type and cell.command:
            adapter = detect_by_command(cell.command)
            if adapter:
                cell.agent_type = adapter.name
                log.info("Auto-detected agent type '%s' for '%s' "
                         "(command: %s)", adapter.name, cell.name,
                         cell.command)

        # Inject LOOM_CELL_ID for all cells (used by hook correlation)
        env_vars = dict(env_vars) if env_vars else {}
        env_vars["LOOM_CELL_ID"] = cell.id

        # Environment variables
        if env_vars:
            exports = " ".join(
                f"{k}={shlex.quote(os.path.expanduser(v))}"
                for k, v in env_vars.items())
            await session.async_send_text(f"export {exports}\n")

        # Install agent hooks (if adapter supports it)
        if cell.agent_type:
            adapter = get_adapter(cell.agent_type)
            hook_dir = os.path.expanduser(cell.directory) if cell.directory else ""
            if hook_dir and hasattr(adapter, "install_hooks"):
                if adapter.install_hooks(hook_dir):
                    log.info("Installed hooks for '%s' (type=%s) in %s",
                             cell.name, cell.agent_type, hook_dir)
                else:
                    log.warning("Failed to install hooks for '%s' in %s",
                                cell.name, hook_dir)

        # Init script
        if init_script:
            await session.async_send_text(
                f"source {shlex.quote(os.path.expanduser(init_script))}\n")

        # Boot command (with session resume for supported agents)
        boot_cmd = cell.command
        if boot_cmd and cell.agent_session_id and cell.agent_type == "claude-code":
            gs = self.state.get_group_settings(cell.group)
            if gs.agent_session_resume:
                boot_cmd = f"{boot_cmd} --resume {shlex.quote(cell.agent_session_id)}"
                log.info("Resuming Claude Code session %s for '%s'",
                         cell.agent_session_id, cell.name)
        if boot_cmd:
            await session.async_send_text(boot_cmd + "\n")
            # Awareness agents start as idle — hooks will set activity
            # when the agent actually starts working
            cell.status = "running" if not cell.agent_type else "idle"
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
            self._start_terminal_monitors(cell)
        else:
            # For agents, seed current_path from directory for git resolution
            if cell.directory and not cell.current_path:
                cell.current_path = os.path.expanduser(cell.directory)

        # Resolve git info for all cell types
        await self._resolve_git_info(cell)

        self.state._emit_agent(cell)
        self.state._db_save_agent(cell)
        self._start_prompt_monitor(cell)
        await self.reorder_tabs()

        # Restore focus to the previously active tab if configured
        if not self.state.global_settings.focus_new_tabs and prev_tab:
            try:
                await prev_tab.async_select()
            except Exception:
                log.debug("Could not restore focus to previous tab")

    async def reorder_tabs(self):
        """Keep managed tabs last in each window, ordered by group then position."""
        try:
            app = await iterm2.async_get_app(self.conn)

            # Sort key: (group_idx, parent_pos, is_child, child_pos)
            # This clusters child terminals right after their parent agent.
            managed_sids: dict[str, tuple] = {}
            for gi, gname in enumerate(self.state.groups):
                for pos, aid in enumerate(self.state.groups[gname]):
                    cell = self.state.agents.get(aid)
                    if cell and cell.session_id:
                        managed_sids[cell.session_id] = (gi, pos, 0, 0)
                    # Add child terminals after parent
                    for ci, child_id in enumerate(
                            self.state._children.get(aid, [])):
                        child = self.state.agents.get(child_id)
                        if child and child.session_id:
                            managed_sids[child.session_id] = (
                                gi, pos, 1, ci)

            if not managed_sids:
                return

            for window in app.windows:
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
                    continue

                managed.sort(key=lambda pair: pair[0])
                new_order = unmanaged + [tab for _, tab in managed]

                if [t.tab_id for t in new_order] != \
                   [t.tab_id for t in window.tabs]:
                    await window.async_set_tabs(new_order)
                    log.debug("Tabs reordered in window %s: "
                              "%d unmanaged + %d managed",
                              window.window_id,
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

    async def update_session(self, cell: AgentCell, old_name: str = ""):
        """Apply name and tab color changes to a live session."""
        session = await self._find_session(cell.session_id)
        if not session:
            return
        try:
            # Update tab title
            app = await iterm2.async_get_app(self.conn)
            for window in app.windows:
                for tab in window.tabs:
                    if tab.current_session and \
                            tab.current_session.session_id == cell.session_id:
                        if cell.parent_id:
                            parent = self.state.agents.get(cell.parent_id)
                            parent_name = parent.name if parent else "?"
                            await tab.async_set_title(
                                f"[{cell.group}] {parent_name}/{cell.name}")
                        else:
                            await tab.async_set_title(
                                f"[{cell.group}] {cell.name}")
                        break
        except Exception:
            log.exception("Failed to update tab title for '%s'", cell.name)

        # Update tab color
        try:
            change = iterm2.LocalWriteOnlyProfile()
            if cell.tab_color:
                r = int(cell.tab_color[1:3], 16)
                g = int(cell.tab_color[3:5], 16)
                b = int(cell.tab_color[5:7], 16)
                color = iterm2.Color(r, g, b)
                change.set_use_tab_color(True)
                change.set_use_tab_color_light(True)
                change.set_use_tab_color_dark(True)
                change.set_tab_color(color)
                change.set_tab_color_light(color)
                change.set_tab_color_dark(color)
            else:
                change.set_use_tab_color(False)
                change.set_use_tab_color_light(False)
                change.set_use_tab_color_dark(False)
            await session.async_set_profile_properties(change)
        except Exception:
            log.exception("Failed to update tab color for '%s'", cell.name)

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
                        self.state._emit_agent(cell)
                        self.state._db_save_agent(cell)
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
                        # Auto-detect agent type on first meaningful process
                        if val and not cell.agent_type:
                            adapter = detect_agent_type(val)
                            if adapter.name != "generic":
                                cell.agent_type = adapter.name
                                log.info("Auto-detected agent type '%s' "
                                         "for '%s' (process: %s)",
                                         adapter.name, cell.name, val)
                        self.state._emit_agent(cell)
                        # Only persist if agent_type changed (persistent field)
                        if val and cell.agent_type:
                            self.state._db_save_agent(cell)
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
                        self.state._emit_agent(cell)
                        # Ephemeral fields only — no DB write needed
                        await self.state.broadcast()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("path monitor failed for '%s' (session %s)",
                              cell.name, cell.session_id)

        await asyncio.gather(_watch_job(), _watch_path())

    async def _watch_focus(self):
        try:
            async with iterm2.FocusMonitor(self.conn) as mon:
                while True:
                    update = await mon.async_get_next_update()
                    changed = False
                    if update.window_changed:
                        wc = update.window_changed
                        if wc.event == (iterm2.FocusUpdateWindowChanged
                                        .Reason
                                        .TERMINAL_WINDOW_BECAME_KEY):
                            self.state.current_window_id = wc.window_id
                            changed = True
                    if update.active_session_changed:
                        self.state.active_session_id = (
                            update.active_session_changed.session_id)
                        changed = True
                    if changed:
                        self.state._emit("focus_update",
                            active_session_id=self.state.active_session_id,
                            current_window_id=self.state.current_window_id)
                        await self.state.broadcast()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("FocusMonitor failed")

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
                            # Clear awareness fields
                            cell.activity = ""
                            cell.activity_detail = ""
                            cell.error_message = ""
                            cell.needs_attention = False
                            self.state._emit_agent(cell)
                            self.state._db_save_agent(cell)
                            for tasks in (self._prompt_tasks,
                                          self._job_tasks):
                                task = tasks.pop(cell.id, None)
                                if task:
                                    task.cancel()
                            # Notify server for post-termination work
                            if self.on_session_terminated:
                                try:
                                    await self.on_session_terminated(cell)
                                except Exception:
                                    log.exception("on_session_terminated "
                                                  "callback failed")
                            # Auto-remove terminal if close_on_disconnect
                            if (cell.cell_type == "terminal"
                                    and self.on_terminal_disconnected):
                                gs = self.state.get_group_settings(
                                    cell.group)
                                if gs.terminal_close_on_disconnect:
                                    try:
                                        await self.on_terminal_disconnected(
                                            cell)
                                    except Exception:
                                        log.exception(
                                            "on_terminal_disconnected "
                                            "callback failed")
                            await self.state.broadcast()
                            break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("SessionTerminationMonitor failed")
