"""iTerm2 bridge — translates matrix commands into iTerm2 Python API calls."""

import asyncio
import contextlib
import os
import re
import shlex
from typing import Optional

import iterm2

from .config import log
from .state import AgentCell, MatrixState
from .adapters import detect_agent_type, detect_by_command, get_adapter
from .terminal_adapter import (
    TerminalCapabilities,
    TerminalLaunchContext,
)
from .worktree import ensure_git_exclude

_TITLE_RE = re.compile(r"^\[(.+?)\]\s+(.+)$")


class ITerm2Adapter:
    capabilities = TerminalCapabilities(
        supports_profiles=True,
        supports_tab_color=True,
        supports_tab_reorder=True,
        supports_focus_tracking=True,
        supports_global_keybindings=True,
        supports_toolbelt_registration=True,
    )

    def __init__(self, connection: iterm2.Connection, state: MatrixState):
        self.conn = connection
        self.state = state
        self._prompt_tasks: dict[str, asyncio.Task] = {}
        self._job_tasks: dict[str, asyncio.Task] = {}
        self._input_ready_sessions: set[str] = set()
        self._input_ready_events: dict[str, asyncio.Event] = {}
        self._term_task: Optional[asyncio.Task] = None
        self._focus_task: Optional[asyncio.Task] = None
        self.on_session_terminated = None  # async callback(cell)
        self.on_terminal_disconnected = None  # async callback(cell) — auto-remove
        self.on_terminal_output = None  # async callback(cell_id, session_id, text)

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

    async def shutdown(self) -> None:
        tasks = []
        for task in (self._term_task, self._focus_task):
            if task:
                task.cancel()
                tasks.append(task)
        for task in self._prompt_tasks.values():
            task.cancel()
            tasks.append(task)
        for task in self._job_tasks.values():
            task.cancel()
            tasks.append(task)
        self._prompt_tasks.clear()
        self._job_tasks.clear()
        self._term_task = None
        self._focus_task = None
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

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

                # Re-install hooks and MCP config for awareness agents
                # (in case files were lost or working dir changed)
                if cell.agent_type and cell.directory:
                    adapter = get_adapter(cell.agent_type)
                    hook_dir = os.path.expanduser(cell.directory)
                    if hasattr(adapter, "install_hooks"):
                        if adapter.install_hooks(hook_dir):
                            log.info("Re-installed hooks for '%s' in %s",
                                     cell.name, hook_dir)
                    if hasattr(adapter, "install_mcp_config"):
                        if adapter.install_mcp_config(hook_dir):
                            log.info("Re-installed MCP config for '%s' in %s",
                                     cell.name, hook_dir)
                    if hasattr(adapter, "install_skills"):
                        if adapter.install_skills(hook_dir):
                            log.info("Re-installed skills for '%s' in %s",
                                     cell.name, hook_dir)
                    ensure_git_exclude(hook_dir)

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

    async def list_profiles(self) -> list[str]:
        """Return sorted iTerm2 profile names."""
        try:
            all_profiles = await iterm2.PartialProfile.async_query(self.conn)
            return sorted(set(p.name for p in all_profiles if p.name))
        except Exception:
            log.exception("Failed to query profiles")
            return ["Default"]

    async def get_launch_context(self) -> TerminalLaunchContext:
        """Return the current session metadata used for launching new sessions."""
        try:
            app = await iterm2.async_get_app(self.conn)
            win = getattr(app, "current_terminal_window", None) \
                or getattr(app, "current_window", None)
            if not win:
                return TerminalLaunchContext(
                    current_window_id=self.state.current_window_id or "",
                    active_session_id=self.state.active_session_id or "",
                )
            tab = getattr(win, "current_tab", None)
            session = getattr(tab, "current_session", None) if tab else None
            if not session:
                return TerminalLaunchContext(
                    current_window_id=getattr(win, "window_id", "") or "",
                    active_session_id=self.state.active_session_id or "",
                )
            return TerminalLaunchContext(
                current_path=await session.async_get_variable("path") or "",
                current_profile=(
                    await session.async_get_variable("profileName")
                    or "Default"
                ),
                current_window_id=getattr(win, "window_id", "") or "",
                active_session_id=getattr(session, "session_id", "") or "",
            )
        except Exception:
            log.exception("Failed to get current session info")
            return TerminalLaunchContext(
                current_window_id=self.state.current_window_id or "",
                active_session_id=self.state.active_session_id or "",
            )

    def prime_input_ready(self, session_id: str) -> None:
        """Mark a session as ready for immediate input delivery."""
        if session_id:
            self._input_ready_sessions.add(session_id)

    async def register_web_view_tool(self, *, display_name: str,
                                     identifier: str, url: str,
                                     reveal_if_already_registered: bool = True
                                     ) -> bool:
        """Register the Loom toolbelt webview in iTerm2."""
        try:
            await iterm2.tool.async_register_web_view_tool(
                self.conn,
                display_name=display_name,
                identifier=identifier,
                reveal_if_already_registered=reveal_if_already_registered,
                url=url,
            )
            return True
        except Exception:
            log.exception("Failed to register iTerm2 web view tool")
            return False

    # -- Session lifecycle --------------------------------------------------

    def _find_window_for_session(self, app, session_id: str):
        """Return the iTerm2 window containing the given session ID."""
        if not session_id:
            return None
        for window in getattr(app, "windows", []):
            for tab in getattr(window, "tabs", []):
                sessions = getattr(tab, "sessions", None)
                if not sessions:
                    current = getattr(tab, "current_session", None)
                    sessions = [current] if current else []
                for session in sessions:
                    if session and session.session_id == session_id:
                        return window
        return None

    def _find_window_by_id(self, app, window_id: str):
        """Return the iTerm2 window matching the given window ID."""
        if not window_id:
            return None
        for window in getattr(app, "windows", []):
            if getattr(window, "window_id", "") == window_id:
                return window
        return None

    async def create_session(self, cell: AgentCell, *,
                             env_vars: dict[str, str] | None = None,
                             env_file: str = "",
                             init_script: str = "",
                             shell: str = "",
                             system_prompt: str = "",
                             target_session_id: str = "",
                             target_window_id: str = "",
                             restore_focus_to_prev_tab: bool = False):
        log.info("Creating session for %s '%s' [%s]",
                 cell.cell_type, cell.name, cell.group)
        app = await iterm2.async_get_app(self.conn)
        launch_dir = ""
        if cell.directory:
            launch_dir = os.path.expanduser(cell.directory)
        else:
            try:
                launch_ctx = await self.get_launch_context()
                launch_dir = os.path.expanduser(launch_ctx.current_path or "")
            except Exception:
                log.debug("Could not resolve launch context for '%s'", cell.name)
            if launch_dir:
                cell.directory = launch_dir
        if cell.parent_id:
            parent = self.state.agents.get(cell.parent_id)
            if parent:
                target_session_id = target_session_id or parent.session_id or ""
                target_window_id = target_window_id or parent.window_id or ""

        window = self._find_window_for_session(app, target_session_id)
        if not window:
            window = self._find_window_by_id(app, target_window_id)
        if not window:
            window = app.current_window
        if not window:
            log.error("No current window — cannot create tab for '%s'",
                      cell.name)
            cell.status = "error"
            self.state._emit_agent(cell)
            self.state._db_save_agent(cell)
            return

        # Remember tabs up front so we can restore user focus if needed.
        prev_target_tab = window.current_tab
        active_window = app.current_window
        prev_active_tab = active_window.current_tab if active_window else None

        tab = await window.async_create_tab(profile=cell.profile)
        session = tab.current_session
        cell.session_id = session.session_id
        self._input_ready_sessions.discard(session.session_id)
        self._input_ready_events.pop(cell.id, None)
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
        if launch_dir:
            await session.async_send_text(f"cd {shlex.quote(launch_dir)}\n")

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

        # Source .env file (after env vars, before init script)
        if env_file:
            expanded = os.path.expanduser(env_file)
            await session.async_send_text(
                f"[ -f {shlex.quote(expanded)} ] && source {shlex.quote(expanded)}\n")

        # Install agent hooks and MCP config (if adapter supports it)
        if cell.agent_type:
            adapter = get_adapter(cell.agent_type)
            hook_dir = launch_dir
            if hook_dir and system_prompt:
                extra_flags = adapter.inject_system_prompt(
                    hook_dir, system_prompt)
                if extra_flags and extra_flags not in cell.command:
                    cell.command = (cell.command + extra_flags).strip()
            if hook_dir and hasattr(adapter, "install_hooks"):
                if adapter.install_hooks(hook_dir):
                    log.info("Installed hooks for '%s' (type=%s) in %s",
                             cell.name, cell.agent_type, hook_dir)
                else:
                    log.warning("Failed to install hooks for '%s' in %s",
                                cell.name, hook_dir)
            if hook_dir and hasattr(adapter, "install_mcp_config"):
                if adapter.install_mcp_config(hook_dir):
                    log.info("Installed MCP config for '%s' in %s",
                             cell.name, hook_dir)
            if hook_dir and hasattr(adapter, "install_skills"):
                if adapter.install_skills(hook_dir):
                    log.info("Installed skills for '%s' in %s",
                             cell.name, hook_dir)
            # Exclude Loom-injected files from git status
            if hook_dir:
                ensure_git_exclude(hook_dir)

        # Init script
        if init_script:
            await session.async_send_text(
                f"source {shlex.quote(os.path.expanduser(init_script))}\n")

        # Boot command (with session resume for supported agents)
        boot_cmd = cell.command
        if boot_cmd and cell.agent_session_id and cell.agent_type:
            if cell.session_resume:
                adapter = get_adapter(cell.agent_type)
                resumed = adapter.get_resume_command(
                    boot_cmd, cell.agent_session_id)
                if resumed:
                    boot_cmd = resumed
                    log.info("Resuming %s session %s for '%s'",
                             adapter.display_name,
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
            if launch_dir and not cell.current_path:
                cell.current_path = launch_dir

        # Resolve git info for all cell types
        await self._resolve_git_info(cell)

        self.state._emit_agent(cell)
        self.state._db_save_agent(cell)
        self._start_prompt_monitor(cell)
        await self.reorder_tabs()

        # Restore focus when dispatch explicitly asked to preserve the user's
        # current tab, or when the global setting disables focusing new tabs.
        restore_tab = None
        if restore_focus_to_prev_tab:
            restore_tab = prev_active_tab or prev_target_tab
        elif not self.state.global_settings.focus_new_tabs:
            restore_tab = prev_target_tab
        if restore_tab:
            try:
                await restore_tab.async_select()
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
                gs = self.state.group_settings.get(gname)
                weaver_id = gs.weaver_agent_id if gs else ""
                for pos, aid in enumerate(self.state.groups[gname]):
                    cell = self.state.agents.get(aid)
                    if cell and cell.session_id:
                        # Weaver gets position -1 so it sorts first
                        p = -1 if aid == weaver_id else pos
                        managed_sids[cell.session_id] = (gi, p, 0, 0)
                    # Add child terminals after parent
                    for ci, child_id in enumerate(
                            self.state._children.get(aid, [])):
                        child = self.state.agents.get(child_id)
                        if child and child.session_id:
                            managed_sids[child.session_id] = (
                                gi, p, 1, ci)

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
        """Send text to a terminal session.

        The body is sent first and the submit key is sent separately.
        Some TUIs treat ``async_send_text()`` as a paste operation, which
        can leave an appended return sitting in the composer instead of
        actually submitting the prompt. Multi-line sends keep a short
        delay before submit; single-line sends submit immediately after
        the body. The first send to adapters with an input-ready policy
        still waits until the visible screen stabilizes.
        """
        session = await self._find_session(session_id)
        if session:
            cell = self._find_cell_by_session(session_id)
            adapter = get_adapter(cell.agent_type) if cell else None
            submit_key = adapter.get_submit_key() if adapter else "\r"
            submit_delay = (
                adapter.get_multiline_submit_delay()
                if adapter else 0.3
            )
            if cell:
                await self._wait_for_input_ready(session, cell)
            body = text.rstrip("\r\n")
            chunks = (
                adapter.get_input_chunks(body)
                if adapter else ([body] if body else [])
            )
            for chunk in chunks:
                await session.async_send_text(chunk)
            if "\n" in body:
                await asyncio.sleep(submit_delay)
            await session.async_send_text(submit_key)

    async def write_input(self, session_id: str, data: str) -> None:
        session = await self._find_session(session_id)
        if session:
            await session.async_send_text(data)

    async def resize_session(self, session_id: str, cols: int, rows: int) -> None:
        _ = (session_id, cols, rows)
        return

    def get_terminal_buffer(self, session_id: str) -> str:
        _ = session_id
        return ""

    # -- Helpers ------------------------------------------------------------

    def _find_cell_by_session(self, session_id: str) -> AgentCell | None:
        for cell in self.state.agents.values():
            if cell.session_id == session_id:
                return cell
        return None

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

    async def _read_screen_text(self, session) -> str:
        try:
            line_info = await session.async_get_line_info()
            first = max(
                getattr(line_info, "overflow", 0),
                getattr(line_info, "first_visible_line_number", 0) - 10,
            )
            count = max(getattr(line_info, "mutable_area_height", 24) + 10, 24)
            contents = await session.async_get_contents(first, count)
            return "\n".join(
                getattr(line, "string", "")
                for line in contents
            )
        except Exception:
            log.debug("Could not read visible screen for session %s",
                      session.session_id)
            return ""

    def signal_input_ready(self, cell_id: str):
        """Signal that an agent's TUI is ready for input (called on hook event)."""
        evt = self._input_ready_events.get(cell_id)
        if evt:
            evt.set()
        else:
            # Hook arrived before _wait_for_input_ready — pre-set the event
            evt = asyncio.Event()
            evt.set()
            self._input_ready_events[cell_id] = evt

    async def _wait_for_input_ready(self, session, cell: AgentCell):
        if cell.session_id in self._input_ready_sessions:
            return
        if not cell.agent_type:
            self._input_ready_sessions.add(cell.session_id)
            return

        adapter = get_adapter(cell.agent_type)
        policy = adapter.get_input_ready_policy()
        if not policy.enabled:
            self._input_ready_sessions.add(cell.session_id)
            return

        if policy.hook_event:
            evt = self._input_ready_events.get(cell.id)
            if not evt:
                evt = asyncio.Event()
                self._input_ready_events[cell.id] = evt
            detected = False
            if policy.screen_fallback:
                # Hybrid: wait for hook event OR screen-based detection
                deadline = asyncio.get_running_loop().time() + policy.timeout_seconds
                stable_polls = 0
                while asyncio.get_running_loop().time() < deadline:
                    if evt.is_set():
                        log.info("Input-ready via hook for '%s'", cell.name)
                        detected = True
                        break
                    screen_text = await self._read_screen_text(session)
                    if screen_text and adapter.is_input_ready_screen(
                            screen_text):
                        stable_polls += 1
                        if stable_polls >= max(policy.stable_polls, 1):
                            log.info("Input-ready via screen for '%s'",
                                     cell.name)
                            detected = True
                            break
                    else:
                        stable_polls = 0
                    try:
                        await asyncio.wait_for(
                            evt.wait(), policy.poll_interval_seconds)
                        log.info("Input-ready via hook for '%s'", cell.name)
                        detected = True
                        break
                    except asyncio.TimeoutError:
                        pass
                if not detected:
                    log.info("Input-ready timed out for '%s' "
                             "(type=%s, session=%s)",
                             cell.name, cell.agent_type, cell.session_id)
            else:
                try:
                    await asyncio.wait_for(evt.wait(), policy.timeout_seconds)
                    log.info("Input-ready via hook for '%s'", cell.name)
                    detected = True
                except asyncio.TimeoutError:
                    log.info("Hook-based input-ready timed out for '%s' "
                             "(type=%s, session=%s)",
                             cell.name, cell.agent_type, cell.session_id)
            if detected and policy.post_ready_delay > 0:
                await asyncio.sleep(policy.post_ready_delay)
            self._input_ready_events.pop(cell.id, None)
            self._input_ready_sessions.add(cell.session_id)
            return

        deadline = asyncio.get_running_loop().time() + policy.timeout_seconds
        stable_polls = 0
        while asyncio.get_running_loop().time() < deadline:
            screen_text = await self._read_screen_text(session)
            if screen_text and adapter.is_input_ready_screen(screen_text):
                stable_polls += 1
                if stable_polls >= max(policy.stable_polls, 1):
                    self._input_ready_sessions.add(cell.session_id)
                    return
            else:
                stable_polls = 0
            await asyncio.sleep(policy.poll_interval_seconds)

        log.info("Input-ready wait timed out for '%s' (type=%s, session=%s)",
                 cell.name, cell.agent_type, cell.session_id)

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
                            self._input_ready_sessions.discard(sid)
                            self._input_ready_events.pop(cell.id, None)
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
