"""Local PTY terminal adapter for standalone Torque sessions."""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import errno
import os
import pty
import shlex
import shutil
import signal
import tempfile
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .adapters import detect_by_command, get_adapter
from .config import log
from .pty_core import (
    ANSI_ESCAPE_RE as _ANSI_ESCAPE_RE,
    BUFFER_LIMIT as _BUFFER_LIMIT,
    OSC7_RE as _OSC7_RE,
    PROMPT_HOOK_LIMIT as _PROMPT_HOOK_LIMIT,
    READINESS_BUFFER_LIMIT as _READINESS_BUFFER_LIMIT,
    TerminalScreenBuffer as _TerminalScreenBuffer,
    Utf8IncrementalDecoder as _Utf8IncrementalDecoder,
    preexec_acquire_ctty as _preexec_acquire_ctty,
    set_winsize as _pty_set_winsize,
)
from .server_agent import mcp_entrypoint_for_cell
from .session_end_backstop import CodexIdleSessionEndDetector
from .state import AgentCell, MatrixState
from .terminal_adapter import TerminalCapabilities, TerminalLaunchContext
from .worktree import ensure_git_exclude


@dataclass
class _PtySession:
    session_id: str
    cell_id: str
    process: Optional[asyncio.subprocess.Process] = None
    master_fd: int = -1
    shell_path: str = ""
    buffer: str = ""
    parse_tail: str = ""
    cols: int = 120
    rows: int = 32
    closed: bool = False
    reader_task: Optional[asyncio.Task] = None
    bootstrap_dir: str = ""
    # Supervisor mode only — informational for display.
    supervisor_pid: int = 0
    decoder: codecs.IncrementalDecoder = field(
        default_factory=lambda: _Utf8IncrementalDecoder(errors="replace")
    )
    screen: _TerminalScreenBuffer = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.screen = _TerminalScreenBuffer(cols=self.cols, rows=self.rows)


class LocalPtyAdapter:
    capabilities = TerminalCapabilities(
        supports_embedded_terminal=True,
        supports_focus_tracking=True,
    )

    def __init__(self, state: MatrixState):
        self.state = state
        self._sessions: dict[str, _PtySession] = {}
        self._input_ready_sessions: set[str] = set()
        self._input_ready_events: dict[str, asyncio.Event] = {}
        self._codex_idle_tasks: dict[str, asyncio.Task] = {}
        self._codex_idle_detector = CodexIdleSessionEndDetector()
        self.on_session_terminated = None
        self.on_agent_session_end_detected = None
        self.on_terminal_disconnected = None
        self.on_terminal_output = None

    async def start(self) -> None:
        self.state.current_window_id = "standalone"

    async def shutdown(self) -> None:
        reader_tasks = []
        for session in list(self._sessions.values()):
            if session.reader_task:
                reader_tasks.append(session.reader_task)
        for session_id in list(self._sessions.keys()):
            await self.close_session(session_id)
        idle_tasks = list(self._codex_idle_tasks.values())
        self._codex_idle_tasks.clear()
        self._codex_idle_detector.clear()
        for task in idle_tasks:
            task.cancel()
        for task in reader_tasks + idle_tasks:
            with contextlib.suppress(asyncio.CancelledError, OSError):
                await task

    async def reconnect_orphans(self) -> None:
        cleared = 0
        for cell in self.state.iter_active_agents():
            if not cell.session_id and cell.status == "stopped":
                continue
            if cell.session_id or cell.status != "stopped":
                cleared += 1
            self._input_ready_sessions.discard(cell.session_id or "")
            self._input_ready_events.pop(cell.id, None)
            self._cancel_codex_idle_monitor(cell.id)
            cell.status = "stopped"
            cell.session_id = None
            cell.window_id = "standalone"
            cell.current_process = ""
            cell.current_path = ""
            cell.current_branch = ""
            cell.git_root = ""
            cell.activity = ""
            cell.activity_detail = ""
            cell.error_message = ""
            cell.needs_attention = False
            self.state.mark_agent_heartbeat(cell, emit=False)
            self.state._emit_agent(cell)
            self.state._db_save_agent(cell)
        self.state.active_session_id = None
        log.info("Standalone reconnect: cleared %d stale sessions", cleared)

    async def create_session(
        self,
        cell: AgentCell,
        *,
        env_vars: dict[str, str] | None = None,
        env_file: str = "",
        init_script: str = "",
        shell: str = "",
        system_prompt: str = "",
        mcp_entrypoint: str = "",
        target_session_id: str = "",
        target_window_id: str = "",
        restore_focus_to_prev_tab: bool = False,
    ) -> None:
        del target_session_id, target_window_id, restore_focus_to_prev_tab
        prep = self._prepare_create(cell, env_vars=env_vars, shell=shell)
        session_id = uuid.uuid4().hex

        master_fd, slave_fd = pty.openpty()
        _pty_set_winsize(master_fd, 120, 32)
        try:
            process = await asyncio.create_subprocess_exec(
                *prep["shell_argv"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=prep["cwd"],
                env=prep["env"],
                start_new_session=True,
                preexec_fn=_preexec_acquire_ctty,
            )
        except Exception:
            if prep["bootstrap_dir"]:
                shutil.rmtree(prep["bootstrap_dir"], ignore_errors=True)
            raise
        finally:
            with contextlib.suppress(OSError):
                os.close(slave_fd)

        session = _PtySession(
            session_id=session_id,
            cell_id=cell.id,
            process=process,
            master_fd=master_fd,
            shell_path=prep["shell_path"],
            bootstrap_dir=prep["bootstrap_dir"],
        )
        self._sessions[session_id] = session
        session.reader_task = asyncio.create_task(self._read_loop(session))

        await self._finalize_create(
            cell,
            session_id,
            prep=prep,
            env_file=env_file,
            init_script=init_script,
            system_prompt=system_prompt,
            mcp_entrypoint=mcp_entrypoint,
        )

    def _prepare_create(
        self,
        cell: AgentCell,
        *,
        env_vars: Optional[dict[str, str]] = None,
        shell: str = "",
    ) -> dict:
        """Pre-spawn setup shared by in-memory and supervisor adapters.

        Side effects: mutates ``cell.agent_type`` (auto-detect) and
        ``cell.directory`` (default to cwd) to preserve legacy behavior.
        """
        shell_path = self._resolve_shell(shell)
        shell_name = os.path.basename(shell_path)
        if cell.cell_type == "agent" and not cell.agent_type and cell.command:
            adapter = detect_by_command(cell.command)
            if adapter:
                cell.agent_type = adapter.name
                log.info(
                    "Auto-detected agent type '%s' for '%s' (command: %s)",
                    adapter.name,
                    cell.name,
                    cell.command,
                )

        env = self._session_environment(cell.id, env_vars or {})
        boot_adapter = get_adapter(cell.agent_type) if cell.agent_type else None
        if not boot_adapter and cell.command:
            boot_adapter = detect_by_command(cell.command)

        cwd = ""
        if cell.directory:
            expanded = os.path.expanduser(cell.directory)
            if os.path.isdir(expanded):
                cwd = expanded
        elif self.state.active_session_id:
            active = self._sessions.get(self.state.active_session_id)
            if active:
                cwd = self.state.agents.get(
                    active.cell_id, AgentCell("", "", "")).current_path or ""
        if not cwd:
            cwd = os.getcwd()
        if not cell.directory:
            cell.directory = cwd

        bootstrap_dir = ""
        shell_argv = [shell_path, "-i"]
        if shell_name == "zsh":
            bootstrap_dir = self._prepare_zsh_bootstrap(env)
            shell_argv = [shell_path, "-il"]

        return {
            "shell_path": shell_path,
            "shell_name": shell_name,
            "shell_argv": shell_argv,
            "env": env,
            "cwd": cwd,
            "bootstrap_dir": bootstrap_dir,
            "boot_adapter": boot_adapter,
        }

    async def _finalize_create(
        self,
        cell: AgentCell,
        session_id: str,
        *,
        prep: dict,
        env_file: str,
        init_script: str,
        system_prompt: str,
        mcp_entrypoint: str = "",
    ) -> None:
        """Post-spawn cell state updates and startup commands.

        Shared between the in-memory and supervisor code paths.
        """
        cell.session_id = session_id
        cell.window_id = "standalone"
        cell.current_path = prep["cwd"]
        cell.current_process = self._initial_process_name(
            cell, prep["shell_path"])
        cell.status = (
            "running" if (cell.command and not cell.agent_type) else "idle")
        self._input_ready_sessions.discard(session_id)
        self._input_ready_events.pop(cell.id, None)

        await self._resolve_git_info(cell)
        self.state.mark_agent_heartbeat(cell, emit=False)
        self.state._emit_agent(cell)
        self.state._db_save_agent(cell)
        self._start_codex_idle_monitor(cell)

        setup_commands = self._startup_commands(
            cell,
            shell_name=prep["shell_name"],
            cwd=prep["cwd"],
            env_file=env_file,
            init_script=init_script,
            system_prompt=system_prompt,
            mcp_entrypoint=mcp_entrypoint,
        )
        if setup_commands:
            await asyncio.sleep(0.12)
            await self.write_input(
                session_id,
                "".join(cmd + "\r" for cmd in setup_commands),
            )

        # Quiet attach: only steal focus to the new session when nothing is
        # currently active (first agent in a fresh UI, or the previously
        # active session has since exited). Otherwise the browser keeps its
        # current terminal focus — `agent_upsert` still surfaces the cell
        # and the user can switch manually when they're ready. The quiet
        # path must still flush the queued `agent_upsert` delta so the UI
        # learns about the new cell right away; without the broadcast the
        # new session would stay invisible until some later unrelated
        # mutation flushed the accumulated ops.
        current_active = self.state.active_session_id or ""
        if not current_active or current_active not in self._sessions:
            await self.focus_session(session_id)
        else:
            await self.state.broadcast()

    async def close_session(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if not session or session.closed:
            return
        session.closed = True
        self._input_ready_sessions.discard(session_id)
        cell = self.state.agents.get(session.cell_id)
        self._input_ready_events.pop(session.cell_id, None)
        self._cancel_codex_idle_monitor(session.cell_id)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(session.process.pid, signal.SIGHUP)
        try:
            await asyncio.wait_for(session.process.wait(), timeout=1.5)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(session.process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(session.process.wait(), timeout=1.5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(session.process.pid, signal.SIGKILL)
        with contextlib.suppress(OSError):
            os.close(session.master_fd)
        if cell:
            await self._mark_session_stopped(cell, session_id, announce=False)

    async def focus_session(self, session_id: str, *, client_id: str = "") -> bool:
        if session_id not in self._sessions:
            return False
        if client_id:
            await self.state.send_client_focus_update(
                client_id,
                active_session_id=session_id,
                current_window_id="standalone",
            )
            return True
        self.state.active_session_id = session_id
        self.state.current_window_id = "standalone"
        self.state.sync_ui_selection_to_session(session_id)
        self.state._emit(
            "focus_update",
            active_session_id=self.state.active_session_id,
            current_window_id=self.state.current_window_id,
        )
        await self.state.broadcast()
        return True

    async def update_session(self, cell: AgentCell, old_name: str = "") -> None:
        del old_name
        self.state._emit_agent(cell)
        self.state._db_save_agent(cell)
        await self.state.broadcast()

    async def send_text(self, session_id: str, text: str, *,
                        settled_submit: bool = False) -> None:
        session = self._sessions.get(session_id)
        if not session:
            return
        cell = self.state.agents.get(session.cell_id)
        adapter = get_adapter(cell.agent_type) if cell and cell.agent_type else None
        submit_key = adapter.get_submit_key() if adapter else "\r"
        submit_delay = adapter.get_multiline_submit_delay() if adapter else 0.3
        if cell:
            self._mark_codex_turn_submitted(cell)
            await self._wait_for_input_ready(session, cell)
        body = text.rstrip("\r\n")
        chunks = (
            adapter.get_input_chunks(body)
            if adapter else ([body] if body else [])
        )
        for chunk in chunks:
            await self.write_input(session_id, chunk)
        if body and (settled_submit or "\n" in body):
            await asyncio.sleep(submit_delay)
        await self.write_input(session_id, submit_key)
        if cell:
            self._mark_codex_turn_submitted(cell, session, clear_screen=True)

    async def write_input(self, session_id: str, data: str) -> None:
        session = self._sessions.get(session_id)
        if not session or session.closed:
            return
        if not data:
            return
        payload = data.encode("utf-8", errors="ignore")
        await asyncio.to_thread(os.write, session.master_fd, payload)

    async def reorder_tabs(self) -> None:
        return

    async def list_profiles(self) -> list[str]:
        return ["Default"]

    async def get_launch_context(self) -> TerminalLaunchContext:
        active = self._sessions.get(self.state.active_session_id or "")
        if active:
            cell = self.state.agents.get(active.cell_id)
            if cell:
                return TerminalLaunchContext(
                    current_path=cell.current_path or cell.directory or "",
                    current_profile=cell.profile or "Default",
                    current_window_id="standalone",
                    active_session_id=active.session_id,
                )
        return TerminalLaunchContext(
            current_path=os.getcwd(),
            current_profile="Default",
            current_window_id="standalone",
            active_session_id=self.state.active_session_id or "",
        )

    def prime_input_ready(self, session_id: str) -> None:
        if session_id:
            self._input_ready_sessions.add(session_id)

    def signal_input_ready(self, cell_id: str) -> None:
        evt = self._input_ready_events.get(cell_id)
        if evt:
            evt.set()
        else:
            evt = asyncio.Event()
            evt.set()
            self._input_ready_events[cell_id] = evt

    async def register_web_view_tool(
        self,
        *,
        display_name: str,
        identifier: str,
        url: str,
        reveal_if_already_registered: bool = True,
    ) -> bool:
        del display_name, identifier, url, reveal_if_already_registered
        return False

    async def resize_session(self, session_id: str, cols: int, rows: int) -> None:
        session = self._sessions.get(session_id)
        if not session:
            return
        session.cols = max(20, int(cols or 0))
        session.rows = max(4, int(rows or 0))
        session.screen.resize(session.cols, session.rows)
        self._set_winsize(session.master_fd, session.cols, session.rows)

    def get_terminal_buffer(self, session_id: str) -> str:
        session = self._sessions.get(session_id)
        return session.buffer if session else ""

    def _resolve_shell(self, shell: str) -> str:
        shell_name = (shell or "").strip()
        if shell_name:
            resolved = shutil.which(shell_name)
            if resolved:
                return resolved
            if os.path.isabs(shell_name) and os.path.exists(shell_name):
                return shell_name
        env_shell = os.environ.get("SHELL", "").strip()
        if env_shell and os.path.exists(env_shell):
            return env_shell
        for candidate in ("/bin/zsh", "/bin/bash", "/bin/sh"):
            if os.path.exists(candidate):
                return candidate
        return "/bin/sh"

    def _session_environment(
        self,
        cell_id: str,
        env_vars: dict[str, str],
    ) -> dict[str, str]:
        env = os.environ.copy()
        for key in list(env.keys()):
            if key.startswith("ITERM_"):
                env.pop(key, None)
        for key in (
            "LC_TERMINAL",
            "LC_TERMINAL_VERSION",
            "TERM_SESSION_ID",
            "TERM_FEATURES",
            "TERMINFO_DIRS",
            "TERM_PROGRAM",
            "TERM_PROGRAM_VERSION",
            "STARSHIP_SESSION_KEY",
            "STARSHIP_SHELL",
        ):
            env.pop(key, None)
        env["TORQUE_CELL_ID"] = cell_id
        env["TORQUE_STANDALONE_PTY"] = "1"
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env["CLAUDE_GATEWAY_NO_AUTO_UPDATE"] = "true"
        env["DISABLE_AUTOUPDATER"] = "1"
        for key, value in env_vars.items():
            env[str(key)] = os.path.expanduser(str(value))
        return env

    def _prepare_zsh_bootstrap(self, env: dict[str, str]) -> str:
        original_zdotdir = os.path.expanduser(env.get("ZDOTDIR") or "~")
        bootstrap_dir = tempfile.mkdtemp(prefix="torque-zsh-bootstrap-")
        env["TORQUE_ORIGINAL_ZDOTDIR"] = original_zdotdir
        env["ZDOTDIR"] = bootstrap_dir
        wrappers = {
            ".zshenv": self._zsh_wrapper_script(".zshenv"),
            ".zprofile": self._zsh_wrapper_script(".zprofile"),
            ".zlogin": self._zsh_wrapper_script(".zlogin"),
            ".zlogout": self._zsh_wrapper_script(".zlogout"),
            ".zshrc": self._zsh_wrapper_script(
                ".zshrc",
                extra=(
                    "autoload -Uz add-zsh-hook >/dev/null 2>&1\n"
                    "function _torque_precmd() {\n"
                    "  printf '\\033]7;file://%s%s\\007' \"${HOST:-localhost}\" \"$PWD\"\n"
                    "}\n"
                    "add-zsh-hook -d precmd _torque_precmd >/dev/null 2>&1\n"
                    "add-zsh-hook precmd _torque_precmd >/dev/null 2>&1\n"
                ),
            ),
        }
        for filename, content in wrappers.items():
            with open(os.path.join(bootstrap_dir, filename), "w", encoding="utf-8") as fh:
                fh.write(content)
        return bootstrap_dir

    def _zsh_wrapper_script(self, filename: str, *, extra: str = "") -> str:
        return (
            "#!/bin/zsh\n"
            "_torque_bootstrap_zdotdir=\"$ZDOTDIR\"\n"
            "export ZDOTDIR=\"${TORQUE_ORIGINAL_ZDOTDIR:-$HOME}\"\n"
            f"if [ -f \"$ZDOTDIR/{filename}\" ]; then\n"
            f"  source \"$ZDOTDIR/{filename}\"\n"
            "fi\n"
            "export ZDOTDIR=\"$_torque_bootstrap_zdotdir\"\n"
            "unset _torque_bootstrap_zdotdir\n"
            f"{extra}"
        )

    def _initial_process_name(self, cell: AgentCell, shell_path: str) -> str:
        if cell.command:
            parts = shlex.split(cell.command)
            if parts:
                return os.path.basename(parts[0])
        return os.path.basename(shell_path)

    def _startup_commands(
        self,
        cell: AgentCell,
        *,
        shell_name: str = "",
        cwd: str,
        env_file: str = "",
        init_script: str = "",
        system_prompt: str = "",
        mcp_entrypoint: str = "",
    ) -> list[str]:
        commands: list[str] = []
        shell_name = shell_name or os.path.basename(self._resolve_shell(""))
        if shell_name != "zsh":
            prompt_hook = self._prompt_hook_command(shell_name)
            if prompt_hook:
                commands.append(prompt_hook)
            commands.append(self._emit_cwd_command())
        if env_file:
            expanded = os.path.expanduser(env_file)
            commands.append(
                f"[ -f {shlex.quote(expanded)} ] && source {shlex.quote(expanded)}"
            )
        if cell.agent_type:
            adapter = get_adapter(cell.agent_type)
            hook_dir = os.path.expanduser(cell.directory or cwd)
            if hook_dir and system_prompt:
                extra_flags = adapter.inject_system_prompt(hook_dir, system_prompt)
                if extra_flags and extra_flags not in cell.command:
                    cell.command = (cell.command + extra_flags).strip()
            if hook_dir and hasattr(adapter, "install_hooks"):
                if adapter.install_hooks(hook_dir):
                    log.info("Installed hooks for '%s' (type=%s) in %s",
                             cell.name, cell.agent_type, hook_dir)
            if hook_dir and hasattr(adapter, "install_mcp_config"):
                if adapter.install_mcp_config(
                        hook_dir,
                        mcp_entrypoint=(
                            mcp_entrypoint or mcp_entrypoint_for_cell(cell)
                        )):
                    log.info("Installed MCP config for '%s' in %s", cell.name, hook_dir)
            if hook_dir and hasattr(adapter, "install_skills"):
                if adapter.install_skills(hook_dir):
                    log.info("Installed skills for '%s' in %s", cell.name, hook_dir)
            if hook_dir:
                ensure_git_exclude(hook_dir)
        if init_script:
            commands.append(f"source {shlex.quote(os.path.expanduser(init_script))}")
        boot_cmd = cell.command or ""
        if boot_cmd and cell.agent_session_id and cell.agent_type and cell.session_resume:
            adapter = get_adapter(cell.agent_type)
            resumed = adapter.get_resume_command(boot_cmd, cell.agent_session_id)
            if resumed:
                boot_cmd = resumed
                log.info("Resuming %s session %s for '%s'",
                         adapter.display_name, cell.agent_session_id, cell.name)
        if boot_cmd:
            commands.append(boot_cmd)
        return commands

    def _prompt_hook_command(self, shell_name: str) -> str:
        if shell_name == "zsh":
            return (
                "autoload -Uz add-zsh-hook >/dev/null 2>&1; "
                "function _torque_precmd(){ printf '\\033]7;file://%s%s\\007' "
                "\"${HOST:-localhost}\" \"$PWD\"; }; "
                "add-zsh-hook precmd _torque_precmd >/dev/null 2>&1"
            )
        if shell_name == "bash":
            return (
                "PROMPT_COMMAND='printf \"\\033]7;file://%s%s\\007\" "
                "\"${HOSTNAME:-localhost}\" \"$PWD\";'"
            )
        return ""

    def _emit_cwd_command(self) -> str:
        return "printf '\\033]7;file://%s%s\\007' \"${HOSTNAME:-localhost}\" \"$PWD\""

    async def _read_loop(self, session: _PtySession) -> None:
        session_id = session.session_id
        try:
            while True:
                try:
                    chunk = await asyncio.to_thread(os.read, session.master_fd, 4096)
                except OSError as exc:
                    if exc.errno in (errno.EIO, errno.EBADF):
                        break
                    raise
                if not chunk:
                    text = session.decoder.decode(b"", final=True)
                    if text:
                        self._record_terminal_output(session, text)
                        self._process_shell_integration(session, text)
                        await self._emit_terminal_output(session, text)
                    break
                text = session.decoder.decode(chunk)
                if not text:
                    continue
                self._record_terminal_output(session, text)
                self._process_shell_integration(session, text)
                await self._emit_terminal_output(session, text)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("PTY read loop failed for session %s", session_id)
        finally:
            await self._finalize_session(session_id)

    def _process_shell_integration(self, session: _PtySession, text: str) -> None:
        combined = (session.parse_tail + text)[-_PROMPT_HOOK_LIMIT:]
        session.parse_tail = combined
        match = None
        for match in _OSC7_RE.finditer(combined):
            pass
        if not match:
            return
        raw_path = match.group(1)
        path = urllib.parse.unquote(raw_path)
        cell = self.state.agents.get(session.cell_id)
        if not cell or path == cell.current_path:
            return
        asyncio.create_task(self._update_cell_path(cell, path))

    async def _update_cell_path(self, cell: AgentCell, path: str) -> None:
        cell.current_path = path
        await self._resolve_git_info(cell)
        self.state.mark_agent_heartbeat(cell, emit=False)
        self.state._emit_agent(cell)
        await self.state.broadcast()

    def _record_terminal_output(
        self,
        session: _PtySession,
        text: str,
        *,
        snapshot: bool = False,
    ) -> None:
        if snapshot:
            session.buffer = text[-_BUFFER_LIMIT:]
            session.screen.reset()
        else:
            session.buffer = (session.buffer + text)[-_BUFFER_LIMIT:]
        session.screen.feed(text)

    async def _emit_terminal_output(self, session: _PtySession, text: str) -> None:
        if not self.on_terminal_output:
            return
        result = self.on_terminal_output(session.cell_id, session.session_id, text)
        if asyncio.iscoroutine(result):
            await result

    async def _finalize_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if not session:
            return
        with contextlib.suppress(OSError):
            os.close(session.master_fd)
        if session.bootstrap_dir:
            shutil.rmtree(session.bootstrap_dir, ignore_errors=True)
        with contextlib.suppress(Exception):
            await session.process.wait()
        cell = self.state.agents.get(session.cell_id)
        if cell:
            exit_note = "\r\n[process exited]\r\n"
            self._record_terminal_output(session, exit_note)
            await self._emit_terminal_output(session, exit_note)
            await self._mark_session_stopped(cell, session_id)

    async def _mark_session_stopped(
        self,
        cell: AgentCell,
        session_id: str,
        *,
        announce: bool = True,
    ) -> None:
        if cell.session_id != session_id:
            return
        self._input_ready_sessions.discard(session_id)
        self._input_ready_events.pop(cell.id, None)
        self._cancel_codex_idle_monitor(cell.id)
        cell.status = "stopped"
        cell.session_id = None
        cell.current_process = ""
        cell.current_path = ""
        cell.current_branch = ""
        cell.git_root = ""
        cell.activity = ""
        cell.activity_detail = ""
        cell.error_message = ""
        cell.needs_attention = False
        self.state.mark_agent_heartbeat(cell, emit=False)
        self.state._emit_agent(cell)
        self.state._db_save_agent(cell)
        if self.state.active_session_id == session_id:
            self.state.active_session_id = None
            self.state._emit(
                "focus_update",
                active_session_id=None,
                current_window_id="standalone",
            )
        await self.state.clear_client_focus_for_session(
            session_id,
            current_window_id="standalone",
        )
        await self.state.broadcast()
        if announce and self.on_session_terminated:
            result = self.on_session_terminated(cell)
            if asyncio.iscoroutine(result):
                await result
        if (
            announce
            and cell.cell_type == "terminal"
            and self.on_terminal_disconnected
            and self.state.get_group_settings(cell.group).terminal_close_on_disconnect
        ):
            result = self.on_terminal_disconnected(cell)
            if asyncio.iscoroutine(result):
                await result

    async def _resolve_git_info(self, cell: AgentCell) -> None:
        path = cell.current_path
        if not path:
            cell.current_branch = ""
            cell.git_root = ""
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                path,
                "rev-parse",
                "--show-toplevel",
                "--abbrev-ref",
                "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                lines = stdout.decode().strip().splitlines()
                cell.git_root = lines[0] if len(lines) > 0 else ""
                cell.current_branch = lines[1] if len(lines) > 1 else ""
            else:
                cell.current_branch = ""
                cell.git_root = ""
        except Exception:
            log.exception("git resolve failed for '%s' at %s", cell.name, path)
            cell.current_branch = ""
            cell.git_root = ""

    async def _read_screen_text(self, session: _PtySession) -> str:
        screen = getattr(session, "screen", None)
        if screen is not None:
            return screen.render()
        raw = getattr(session, "buffer", "") or ""
        if not raw:
            return ""
        text = raw[-_READINESS_BUFFER_LIMIT:]
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = _ANSI_ESCAPE_RE.sub("", text)
        text = text.replace("\x00", "").replace("\x07", "")
        text = "".join(
            ch for ch in text
            if ch in ("\n", "\t") or ord(ch) >= 32
        )
        return text

    async def _wait_for_input_ready(self, session: _PtySession,
                                    cell: AgentCell) -> None:
        if not cell.session_id or cell.session_id in self._input_ready_sessions:
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
                deadline = (
                    asyncio.get_running_loop().time() + policy.timeout_seconds
                )
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
                    if policy.post_ready_delay > 0:
                        await asyncio.sleep(policy.post_ready_delay)
                    self._input_ready_sessions.add(cell.session_id)
                    return
            else:
                stable_polls = 0
            await asyncio.sleep(policy.poll_interval_seconds)

        log.info("Input-ready wait timed out for '%s' (type=%s, session=%s)",
                 cell.name, cell.agent_type, cell.session_id)

    def _mark_codex_turn_submitted(
        self,
        cell: AgentCell,
        session: _PtySession | None = None,
        *,
        clear_screen: bool = False,
    ) -> None:
        if str(getattr(cell, "agent_type", "") or "") == "codex":
            self._codex_idle_detector.reset(cell.id, cell.session_id or "")
            if clear_screen and session is not None:
                screen = getattr(session, "screen", None)
                if screen is not None:
                    screen.reset()

    def _start_codex_idle_monitor(self, cell: AgentCell) -> None:
        if self.on_agent_session_end_detected is None:
            return
        if (
            str(getattr(cell, "cell_type", "") or "") != "agent"
            or str(getattr(cell, "agent_type", "") or "") != "codex"
            or not getattr(cell, "session_id", "")
        ):
            return
        self._cancel_codex_idle_monitor(cell.id)
        self._codex_idle_tasks[cell.id] = asyncio.create_task(
            self._monitor_codex_idle_session_end(cell.id, cell.session_id))

    def _cancel_codex_idle_monitor(self, cell_id: str) -> None:
        task = self._codex_idle_tasks.pop(str(cell_id or ""), None)
        if task:
            task.cancel()
        self._codex_idle_detector.discard(cell_id)

    async def _monitor_codex_idle_session_end(
        self, cell_id: str, session_id: str,
    ) -> None:
        try:
            while True:
                cell = self.state.agents.get(cell_id)
                session = self._sessions.get(session_id)
                if (
                    not cell
                    or not session
                    or session.closed
                    or cell.session_id != session_id
                    or cell.agent_type != "codex"
                ):
                    break
                adapter = get_adapter("codex")
                policy = adapter.get_input_ready_policy()
                await self._poll_codex_idle_session_end(
                    cell, session, adapter=adapter, stable_polls=policy.stable_polls)
                await asyncio.sleep(policy.poll_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Codex idle-session monitor failed for cell %s", cell_id)
        finally:
            if self._codex_idle_tasks.get(cell_id) is asyncio.current_task():
                self._codex_idle_tasks.pop(cell_id, None)

    async def _poll_codex_idle_session_end(
        self,
        cell: AgentCell,
        session: _PtySession,
        *,
        adapter=None,
        stable_polls: int = 1,
    ) -> bool:
        adapter = adapter or get_adapter(cell.agent_type)
        screen_text = await self._read_screen_text(session)
        ready = bool(screen_text) and adapter.is_input_ready_screen(screen_text)
        if not self._codex_idle_detector.observe(
            cell, ready=ready, stable_polls=stable_polls,
        ):
            return False
        await self._emit_agent_session_end_detected(
            cell,
            {
                "reason": "pty_idle_screen",
                "source": "codex_idle_screen_backstop",
            },
        )
        return True

    async def _emit_agent_session_end_detected(
        self, cell: AgentCell, data: dict,
    ) -> None:
        callback = self.on_agent_session_end_detected
        if callback is None:
            return
        result = callback(cell, data)
        if asyncio.iscoroutine(result):
            await result

    def _set_winsize(self, fd: int, cols: int, rows: int) -> None:
        _pty_set_winsize(fd, cols, rows)


class SupervisedPtyAdapter(LocalPtyAdapter):
    """Standalone-mode adapter that delegates PTY ownership to the
    supervisor sidecar.

    Inherits all daemon-side helpers (env/shell/bootstrap/startup
    commands, input-ready policy, git resolve, OSC7 parsing). Overrides
    only the methods that interact with PTY FDs or child processes —
    those go through ``PtySupervisorClient`` instead.

    If the supervisor becomes unreachable the daemon continues running;
    writes are dropped and the user sees a banner (wired from the
    server). See also: ``on_supervisor_event``.
    """

    def __init__(self, state: MatrixState, socket_path):
        super().__init__(state)
        # Deferred import so unit tests that don't need the supervisor
        # don't pay its cost.
        from .pty_supervisor_client import PtySupervisorClient
        self._client = PtySupervisorClient(socket_path)
        self._client.on_reconnect = self._on_client_reconnect
        # Optional hook: ``(kind: str, detail: dict) -> None | awaitable``.
        # ``kind`` is one of: "connect_failed", "disconnected",
        # "reconnected", "fresh_instance".
        self.on_supervisor_event = None
        # Per-session input-write circuit breaker. A hung agent that stops
        # draining its PTY makes every write to it fail (after the supervisor
        # backpressure / client timeout); without a breaker the daemon keeps
        # re-attempting, and each attempt can stall the shared channel. After
        # WRITE_BREAKER_THRESHOLD consecutive failures we "open" the breaker
        # for that session and drop input fast (no supervisor round-trip) until
        # the cooldown lets a single probe through. session_id -> state dict.
        self._write_breaker: dict[str, dict] = {}

    def _prime_reconnected_input_ready(self, cell: AgentCell) -> None:
        sid = cell.session_id or ""
        if sid and cell.agent_type:
            self._input_ready_events.pop(cell.id, None)
            self.prime_input_ready(sid)

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        await super().start()
        try:
            await self._client.connect()
        except Exception:
            log.exception("PTY supervisor client failed to connect")
            await self._emit_supervisor_event("connect_failed", {})
            raise

    async def shutdown(self) -> None:
        # Supervisor owns the sessions — do NOT terminate them.
        # Just close the client connection; supervisor keeps running.
        try:
            await self._client.aclose()
        except Exception:
            log.exception("PTY supervisor client shutdown failed")
        for task in list(self._codex_idle_tasks.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._codex_idle_tasks.clear()
        self._codex_idle_detector.clear()
        # Skip super().shutdown() which would try to kill local sessions.
        self._sessions.clear()

    async def reconnect_orphans(self) -> None:
        """Reconcile persisted cells with the supervisor's live sessions.

        Cells whose session_id matches a live supervisor session are
        re-adopted (the output stream resumes via ``subscribe``).
        Cells without a match are cleared the same way the legacy
        adapter does today. Supervisor sessions no cell references are
        closed.
        """
        try:
            sessions = await self._client.list_sessions()
        except Exception:
            log.exception(
                "Supervisor list failed during reconcile — "
                "treating all cells as stopped")
            # Fall back to legacy behavior: mark everything stopped.
            return await super().reconnect_orphans()

        by_id = {s["session_id"]: s for s in sessions}
        claimed: set[str] = set()
        adopted = 0
        cleared = 0
        for cell in list(self.state.iter_active_agents()):
            sid = cell.session_id or ""
            if sid and sid in by_id and by_id[sid].get("alive"):
                await self._adopt_supervisor_session(cell, by_id[sid])
                claimed.add(sid)
                adopted += 1
                continue
            # No surviving session — clear like today's reconnect_orphans.
            if cell.session_id or cell.status != "stopped":
                cleared += 1
            self._input_ready_sessions.discard(cell.session_id or "")
            self._input_ready_events.pop(cell.id, None)
            cell.status = "stopped"
            cell.session_id = None
            cell.window_id = "standalone"
            cell.current_process = ""
            cell.current_path = ""
            cell.current_branch = ""
            cell.git_root = ""
            cell.activity = ""
            cell.activity_detail = ""
            cell.error_message = ""
            cell.needs_attention = False
            self.state.mark_agent_heartbeat(cell, emit=False)
            self.state._emit_agent(cell)
            self.state._db_save_agent(cell)

        # Close orphan sessions — alive in the supervisor but unreferenced.
        for sid, info in by_id.items():
            if sid in claimed:
                continue
            if not info.get("alive"):
                continue
            try:
                await self._client.close_session(sid)
            except Exception:
                log.exception(
                    "Failed to close orphan supervisor session %s", sid)

        self.state.active_session_id = None
        log.info(
            "Standalone reconcile: adopted %d, cleared %d, orphans=%d",
            adopted, cleared,
            sum(1 for sid in by_id if sid not in claimed))

    async def _adopt_supervisor_session(
        self,
        cell: AgentCell,
        info: dict,
    ) -> None:
        session = _PtySession(
            session_id=info["session_id"],
            cell_id=cell.id,
            process=None,
            master_fd=-1,
            shell_path="",
            cols=int(info.get("cols") or 120),
            rows=int(info.get("rows") or 32),
            bootstrap_dir=str(info.get("bootstrap_dir") or ""),
            supervisor_pid=int(info.get("pid") or 0),
        )
        self._sessions[session.session_id] = session
        await self._client.subscribe(
            session.session_id,
            on_output=self._make_output_handler(session),
            on_exit=self._make_exit_handler(session),
        )
        self._prime_reconnected_input_ready(cell)
        cell.window_id = "standalone"
        if cell.agent_type:
            cell.status = "idle"
        else:
            cell.status = "running" if cell.command else "idle"
        self.state.mark_agent_heartbeat(cell, emit=False)
        self.state._emit_agent(cell)
        self.state._db_save_agent(cell)
        self._start_codex_idle_monitor(cell)

        # Re-install hooks, MCP config, and skills for awareness agents. A
        # daemon restart can happen across `uninstall_mcp_config` cleanups or a
        # git checkout that removes the injected files, so an adopted session
        # needs them re-written on the filesystem even though the PTY process
        # itself is still alive.
        if cell.agent_type and cell.directory:
            adapter = get_adapter(cell.agent_type)
            hook_dir = os.path.expanduser(cell.directory)
            if hook_dir and hasattr(adapter, "install_hooks"):
                if adapter.install_hooks(hook_dir):
                    log.info("Re-installed hooks for '%s' in %s",
                             cell.name, hook_dir)
            if hook_dir and hasattr(adapter, "install_mcp_config"):
                if adapter.install_mcp_config(
                        hook_dir,
                        mcp_entrypoint=mcp_entrypoint_for_cell(cell)):
                    log.info("Re-installed MCP config for '%s' in %s",
                             cell.name, hook_dir)
            if hook_dir and hasattr(adapter, "install_skills"):
                if adapter.install_skills(hook_dir):
                    log.info("Re-installed skills for '%s' in %s",
                             cell.name, hook_dir)
            if hook_dir:
                ensure_git_exclude(hook_dir)

    # -- session ops (delegated to supervisor) -----------------------------

    async def create_session(
        self,
        cell: AgentCell,
        *,
        env_vars: Optional[dict[str, str]] = None,
        env_file: str = "",
        init_script: str = "",
        shell: str = "",
        system_prompt: str = "",
        mcp_entrypoint: str = "",
        target_session_id: str = "",
        target_window_id: str = "",
        restore_focus_to_prev_tab: bool = False,
    ) -> None:
        del target_session_id, target_window_id, restore_focus_to_prev_tab
        prep = self._prepare_create(cell, env_vars=env_vars, shell=shell)
        session_id = uuid.uuid4().hex
        session = _PtySession(
            session_id=session_id,
            cell_id=cell.id,
            process=None,
            master_fd=-1,
            shell_path=prep["shell_path"],
            bootstrap_dir=prep["bootstrap_dir"],
        )
        self._sessions[session_id] = session
        try:
            result = await self._client.create(
                session_id=session_id,
                cell_id=cell.id,
                shell_argv=prep["shell_argv"],
                env=prep["env"],
                cwd=prep["cwd"],
                cols=session.cols,
                rows=session.rows,
                bootstrap_dir=prep["bootstrap_dir"],
            )
        except Exception:
            self._sessions.pop(session_id, None)
            if prep["bootstrap_dir"]:
                shutil.rmtree(prep["bootstrap_dir"], ignore_errors=True)
            raise
        if result.get("type") != "ok":
            self._sessions.pop(session_id, None)
            if prep["bootstrap_dir"]:
                shutil.rmtree(prep["bootstrap_dir"], ignore_errors=True)
            raise RuntimeError(
                f"supervisor create failed: {result.get('message') or result}")
        session.supervisor_pid = int(result.get("pid") or 0)

        await self._client.subscribe(
            session_id,
            on_output=self._make_output_handler(session),
            on_exit=self._make_exit_handler(session),
        )

        await self._finalize_create(
            cell,
            session_id,
            prep=prep,
            env_file=env_file,
            init_script=init_script,
            system_prompt=system_prompt,
            mcp_entrypoint=mcp_entrypoint,
        )

    async def list_supervisor_sessions(self) -> list:
        return await self._client.list_sessions()

    async def list_supervisor_state(self) -> dict:
        return await self._client.list_state()

    async def terminate_supervisor_session(self, session_id: str) -> None:
        """Terminate a supervisor-owned PTY session by raw session id.

        Unlike ``close_session``, this is allowed to target sessions that the
        current daemon has not adopted into ``self._sessions`` yet. That keeps
        the diagnostics panel useful for stale/orphan rows after a restart.
        """
        session_id = str(session_id or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        session = self._sessions.get(session_id)
        if session and not session.closed:
            session.closed = True
            self._input_ready_sessions.discard(session_id)
            self._input_ready_events.pop(session.cell_id, None)
            self._cancel_codex_idle_monitor(session.cell_id)
            result = await self._client.close_session(session_id)
            if result.get("type") == "error":
                raise RuntimeError(
                    result.get("message") or result.get("code")
                    or "supervisor close failed")
            asyncio.create_task(
                self._finalize_local_after_timeout(session_id, 3.0))
            return
        result = await self._client.close_session(session_id)
        if result.get("type") == "error":
            raise RuntimeError(result.get("message") or result.get("code")
                               or "supervisor close failed")
        for cell in list(self.state.iter_active_agents()):
            if cell.session_id == session_id:
                await self._mark_session_stopped(
                    cell, session_id, announce=True)
                break

    async def close_session(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if not session or session.closed:
            return
        session.closed = True
        self._input_ready_sessions.discard(session_id)
        self._input_ready_events.pop(session.cell_id, None)
        self._cancel_codex_idle_monitor(session.cell_id)
        try:
            await self._client.close_session(session_id)
        except Exception:
            log.exception(
                "Supervisor close failed for %s (best-effort)", session_id)
        # The supervisor's `exit` frame will drive _finalize_supervised.
        # If it doesn't arrive in a reasonable time (supervisor down),
        # still finalize locally.
        asyncio.create_task(
            self._finalize_local_after_timeout(session_id, 3.0))

    async def _finalize_local_after_timeout(
        self, session_id: str, timeout: float,
    ) -> None:
        await asyncio.sleep(timeout)
        session = self._sessions.get(session_id)
        if session and session.closed:
            # Exit never came back — unblock UI anyway.
            await self._finalize_supervised(session, announce=False)

    # -- input-write circuit breaker --------------------------------------

    WRITE_BREAKER_THRESHOLD = 3
    WRITE_BREAKER_COOLDOWN_SECONDS = 30.0

    def _write_breaker_should_skip(self, session_id: str) -> bool:
        st = self._write_breaker.get(session_id)
        if not st or not st.get("opened_at"):
            return False
        if time.monotonic() - st["opened_at"] < self.WRITE_BREAKER_COOLDOWN_SECONDS:
            return True
        # Cooldown elapsed: half-open — let one probe write through.
        st["opened_at"] = 0.0
        return False

    def _write_breaker_record_failure(self, session_id: str) -> None:
        st = self._write_breaker.setdefault(
            session_id, {"fails": 0, "opened_at": 0.0})
        st["fails"] += 1
        if st["fails"] >= self.WRITE_BREAKER_THRESHOLD and not st["opened_at"]:
            st["opened_at"] = time.monotonic()
            log.warning(
                "Input-write breaker OPEN for %s after %d consecutive "
                "failures — agent appears to have stopped draining stdin; "
                "dropping input until it recovers",
                session_id, st["fails"])
            self._mark_session_input_stuck(session_id, True)

    def _write_breaker_record_success(self, session_id: str) -> None:
        st = self._write_breaker.pop(session_id, None)
        if st and st.get("opened_at"):
            log.info("Input-write breaker recovered for %s", session_id)
            self._mark_session_input_stuck(session_id, False)

    def _mark_session_input_stuck(self, session_id: str, stuck: bool) -> None:
        session = self._sessions.get(session_id)
        cell = self.state.agents.get(session.cell_id) if session else None
        if not cell:
            return
        # needs_attention/activity_detail are ephemeral (memory-only), so an
        # _emit_agent delta is enough — no DB write.
        if stuck:
            cell.needs_attention = True
            cell.activity_detail = "Input delivery stalled (agent not reading)"
        elif cell.activity_detail == "Input delivery stalled (agent not reading)":
            cell.activity_detail = ""
        self.state._emit_agent(cell)

    def supervisor_write_breaker_snapshot(self) -> dict:
        """Open breakers as ``{session_id: seconds_open}`` (for diagnostics)."""
        now = time.monotonic()
        return {
            sid: round(now - st["opened_at"], 1)
            for sid, st in self._write_breaker.items()
            if st.get("opened_at")
        }

    async def write_input(self, session_id: str, data: str) -> None:
        session = self._sessions.get(session_id)
        if not session or session.closed:
            return
        if not data:
            return
        if self._write_breaker_should_skip(session_id):
            # Breaker open: drop fast without a (likely-stalling) round-trip.
            return
        payload = data.encode("utf-8", errors="ignore")
        try:
            await self._client.write_input(session_id, payload)
        except Exception:
            self._write_breaker_record_failure(session_id)
            log.warning(
                "Supervisor write failed for %s — dropping input",
                session_id)
            return
        self._write_breaker_record_success(session_id)

    async def resize_session(
        self, session_id: str, cols: int, rows: int,
    ) -> None:
        session = self._sessions.get(session_id)
        if not session:
            return
        session.cols = max(20, int(cols or 0))
        session.rows = max(4, int(rows or 0))
        session.screen.resize(session.cols, session.rows)
        try:
            await self._client.resize(
                session_id, session.cols, session.rows)
        except Exception:
            log.exception(
                "Supervisor resize failed for %s", session_id)

    # -- stream handlers ---------------------------------------------------

    def _make_output_handler(self, session: _PtySession):
        async def handler(msg: dict) -> None:
            await self._handle_output_frame(session, msg)
        return handler

    def _make_exit_handler(self, session: _PtySession):
        async def handler(msg: dict) -> None:
            await self._handle_exit_frame(session, msg)
        return handler

    async def _handle_output_frame(
        self, session: _PtySession, msg: dict,
    ) -> None:
        import base64
        data = msg.get("data") or ""
        if not data:
            return
        try:
            raw = base64.b64decode(data)
        except Exception:
            log.warning("Bad base64 from supervisor for %s",
                        session.session_id)
            return
        text = session.decoder.decode(raw)
        if not text:
            return
        # Reset buffer to the supervisor's view on snapshot to avoid
        # doubling up if we adopted an existing session.
        self._record_terminal_output(
            session,
            text,
            snapshot=(msg.get("type") == "snapshot"),
        )
        self._process_shell_integration(session, text)
        await self._emit_terminal_output(session, text)

    async def _handle_exit_frame(
        self, session: _PtySession, msg: dict,
    ) -> None:
        del msg
        await self._finalize_supervised(session)

    async def _finalize_supervised(
        self, session: _PtySession, *, announce: bool = True,
    ) -> None:
        existing = self._sessions.pop(session.session_id, None)
        if existing is None:
            return
        if session.bootstrap_dir:
            shutil.rmtree(session.bootstrap_dir, ignore_errors=True)
        cell = self.state.agents.get(session.cell_id)
        if cell:
            exit_note = "\r\n[process exited]\r\n"
            self._record_terminal_output(session, exit_note)
            await self._emit_terminal_output(session, exit_note)
            await self._mark_session_stopped(
                cell, session.session_id, announce=announce)

    # -- supervisor event wiring ------------------------------------------

    async def _on_client_reconnect(self, info: dict) -> None:
        if info.get("fresh"):
            log.warning(
                "PTY supervisor reconnected with a fresh instance — "
                "existing sessions are gone")
            await self._emit_supervisor_event("fresh_instance", info)
            # Every tracked session is now dead. Finalize them.
            for session in list(self._sessions.values()):
                await self._finalize_supervised(session, announce=True)
        else:
            await self._emit_supervisor_event("reconnected", info)

    async def _emit_supervisor_event(self, kind: str, detail: dict) -> None:
        cb = self.on_supervisor_event
        if cb is None:
            return
        try:
            result = cb(kind, detail)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            log.exception("Supervisor-event callback failed")
