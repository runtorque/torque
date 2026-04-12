"""Local PTY terminal adapter for standalone Loom sessions."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import fcntl
import json
import os
import pty
import re
import shlex
import shutil
import signal
import struct
import tempfile
import termios
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .adapters import detect_by_command, get_adapter
from .config import log
from .state import AgentCell, MatrixState
from .terminal_adapter import TerminalCapabilities, TerminalLaunchContext
from .worktree import ensure_git_exclude

_OSC7_RE = re.compile("\x1b]7;file://[^/\x07\x1b]*(/.*?)(?:\x07|\x1b\\\\)")
_ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\].*?(?:\x07|\x1B\\))",
    re.DOTALL,
)
_BUFFER_LIMIT = 200_000
_PROMPT_HOOK_LIMIT = 512
_READINESS_BUFFER_LIMIT = 20_000


@dataclass
class _PtySession:
    session_id: str
    cell_id: str
    process: asyncio.subprocess.Process
    master_fd: int
    shell_path: str
    buffer: str = ""
    parse_tail: str = ""
    cols: int = 120
    rows: int = 32
    closed: bool = False
    reader_task: Optional[asyncio.Task] = None
    bootstrap_dir: str = ""
    claude_config_dir: str = ""


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
        self.on_session_terminated = None
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
        for task in reader_tasks:
            with contextlib.suppress(asyncio.CancelledError, OSError):
                await task

    async def reconnect_orphans(self) -> None:
        cleared = 0
        for cell in self.state.agents.values():
            if not cell.session_id and cell.status == "stopped":
                continue
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
        target_session_id: str = "",
        target_window_id: str = "",
        restore_focus_to_prev_tab: bool = False,
    ) -> None:
        del target_session_id, target_window_id, restore_focus_to_prev_tab
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
                cwd = self.state.agents.get(active.cell_id, AgentCell("", "", "")).current_path or ""
        if not cwd:
            cwd = os.getcwd()
        if not cell.directory:
            cell.directory = cwd

        bootstrap_dir = ""
        claude_config_dir = ""
        shell_argv = [shell_path, "-i"]
        if shell_name == "zsh":
            bootstrap_dir = self._prepare_zsh_bootstrap(env)
            shell_argv = [shell_path, "-il"]
        if boot_adapter and boot_adapter.name == "claude-code":
            claude_config_dir = self._prepare_claude_config_overlay(env)

        master_fd, slave_fd = pty.openpty()
        session_id = uuid.uuid4().hex
        self._set_winsize(master_fd, 120, 32)
        try:
            process = await asyncio.create_subprocess_exec(
                *shell_argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
        except Exception:
            if bootstrap_dir:
                shutil.rmtree(bootstrap_dir, ignore_errors=True)
            if claude_config_dir:
                shutil.rmtree(claude_config_dir, ignore_errors=True)
            raise
        finally:
            with contextlib.suppress(OSError):
                os.close(slave_fd)

        session = _PtySession(
            session_id=session_id,
            cell_id=cell.id,
            process=process,
            master_fd=master_fd,
            shell_path=shell_path,
            bootstrap_dir=bootstrap_dir,
            claude_config_dir=claude_config_dir,
        )
        self._sessions[session_id] = session
        session.reader_task = asyncio.create_task(self._read_loop(session))

        cell.session_id = session_id
        cell.window_id = "standalone"
        cell.current_path = cwd
        cell.current_process = self._initial_process_name(cell, shell_path)
        cell.status = "running" if (cell.command and not cell.agent_type) else "idle"
        self._input_ready_sessions.discard(session_id)
        self._input_ready_events.pop(cell.id, None)

        await self._resolve_git_info(cell)
        self.state._emit_agent(cell)
        self.state._db_save_agent(cell)

        setup_commands = self._startup_commands(
            cell,
            shell_name=shell_name,
            cwd=cwd,
            env_file=env_file,
            init_script=init_script,
            system_prompt=system_prompt,
        )
        if setup_commands:
            await asyncio.sleep(0.12)
            await self.write_input(session_id, "".join(cmd + "\r" for cmd in setup_commands))

        await self.focus_session(session_id)

    async def close_session(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if not session or session.closed:
            return
        session.closed = True
        self._input_ready_sessions.discard(session_id)
        cell = self.state.agents.get(session.cell_id)
        self._input_ready_events.pop(session.cell_id, None)
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

    async def focus_session(self, session_id: str) -> bool:
        if session_id not in self._sessions:
            return False
        self.state.active_session_id = session_id
        self.state.current_window_id = "standalone"
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

    async def send_text(self, session_id: str, text: str) -> None:
        session = self._sessions.get(session_id)
        if not session:
            return
        cell = self.state.agents.get(session.cell_id)
        adapter = get_adapter(cell.agent_type) if cell and cell.agent_type else None
        submit_key = adapter.get_submit_key() if adapter else "\r"
        submit_delay = adapter.get_multiline_submit_delay() if adapter else 0.3
        if cell:
            await self._wait_for_input_ready(session, cell)
        body = text.rstrip("\r\n")
        chunks = (
            adapter.get_input_chunks(body)
            if adapter else ([body] if body else [])
        )
        for chunk in chunks:
            await self.write_input(session_id, chunk)
        if body:
            await asyncio.sleep(submit_delay)
        await self.write_input(session_id, submit_key)

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
        env["LOOM_CELL_ID"] = cell_id
        env["LOOM_STANDALONE_PTY"] = "1"
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env["CLAUDE_GATEWAY_NO_AUTO_UPDATE"] = "true"
        env["DISABLE_AUTOUPDATER"] = "1"
        for key, value in env_vars.items():
            env[str(key)] = os.path.expanduser(str(value))
        return env

    def _prepare_claude_config_overlay(self, env: dict[str, str]) -> str:
        config_dir = Path(os.path.expanduser(env.get("CLAUDE_CONFIG_DIR") or "~/.claude"))
        settings_path = config_dir / "settings.json"
        if not settings_path.is_file():
            return ""
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        announcements = settings.get("companyAnnouncements")
        if not isinstance(announcements, list):
            return ""
        cleaned = [
            item
            for item in announcements
            if not isinstance(item, str)
            or (
                "CLAUDE GATEWAY UPDATE AVAILABLE" not in item
                and "→ Latest:" not in item
                and "claude-gateway-helper" not in item
            )
        ]
        if cleaned == announcements:
            return ""
        overlay_dir = tempfile.mkdtemp(prefix="loom-claude-config-")
        try:
            for child in config_dir.iterdir():
                if child.name == "settings.json":
                    continue
                target = Path(overlay_dir) / child.name
                os.symlink(child, target, target_is_directory=child.is_dir())
            home_level_config = Path.home() / ".claude.json"
            if home_level_config.exists():
                os.symlink(home_level_config, Path(overlay_dir) / ".claude.json")
            if cleaned:
                settings["companyAnnouncements"] = cleaned
            else:
                settings.pop("companyAnnouncements", None)
            (Path(overlay_dir) / "settings.json").write_text(
                json.dumps(settings, indent=2) + "\n",
                encoding="utf-8",
            )
            env["CLAUDE_CONFIG_DIR"] = overlay_dir
            return overlay_dir
        except Exception:
            shutil.rmtree(overlay_dir, ignore_errors=True)
            return ""

    def _prepare_zsh_bootstrap(self, env: dict[str, str]) -> str:
        original_zdotdir = os.path.expanduser(env.get("ZDOTDIR") or "~")
        bootstrap_dir = tempfile.mkdtemp(prefix="loom-zsh-bootstrap-")
        env["LOOM_ORIGINAL_ZDOTDIR"] = original_zdotdir
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
                    "function _loom_precmd() {\n"
                    "  printf '\\033]7;file://%s%s\\007' \"${HOST:-localhost}\" \"$PWD\"\n"
                    "}\n"
                    "add-zsh-hook -d precmd _loom_precmd >/dev/null 2>&1\n"
                    "add-zsh-hook precmd _loom_precmd >/dev/null 2>&1\n"
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
            "_loom_bootstrap_zdotdir=\"$ZDOTDIR\"\n"
            "export ZDOTDIR=\"${LOOM_ORIGINAL_ZDOTDIR:-$HOME}\"\n"
            f"if [ -f \"$ZDOTDIR/{filename}\" ]; then\n"
            f"  source \"$ZDOTDIR/{filename}\"\n"
            "fi\n"
            "export ZDOTDIR=\"$_loom_bootstrap_zdotdir\"\n"
            "unset _loom_bootstrap_zdotdir\n"
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
                if adapter.install_mcp_config(hook_dir):
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
                "function _loom_precmd(){ printf '\\033]7;file://%s%s\\007' "
                "\"${HOST:-localhost}\" \"$PWD\"; }; "
                "add-zsh-hook precmd _loom_precmd >/dev/null 2>&1"
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
                    break
                text = chunk.decode("utf-8", errors="replace")
                session.buffer = (session.buffer + text)[-_BUFFER_LIMIT:]
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
        self.state._emit_agent(cell)
        await self.state.broadcast()

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
        if session.claude_config_dir:
            shutil.rmtree(session.claude_config_dir, ignore_errors=True)
        with contextlib.suppress(Exception):
            await session.process.wait()
        cell = self.state.agents.get(session.cell_id)
        if cell:
            exit_note = "\r\n[process exited]\r\n"
            session.buffer = (session.buffer + exit_note)[-_BUFFER_LIMIT:]
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
        self.state._emit_agent(cell)
        self.state._db_save_agent(cell)
        if self.state.active_session_id == session_id:
            self.state.active_session_id = None
            self.state._emit(
                "focus_update",
                active_session_id=None,
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
                    self._input_ready_sessions.add(cell.session_id)
                    return
            else:
                stable_polls = 0
            await asyncio.sleep(policy.poll_interval_seconds)

        log.info("Input-ready wait timed out for '%s' (type=%s, session=%s)",
                 cell.name, cell.agent_type, cell.session_id)

    def _set_winsize(self, fd: int, cols: int, rows: int) -> None:
        packed = struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0)
        with contextlib.suppress(OSError):
            fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)
