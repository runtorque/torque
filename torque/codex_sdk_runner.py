"""Read-only Codex SDK runner prototype.

The real Codex SDK dependency is optional. Tests inject fake clients through the
``client_factory`` boundary; CI never needs a live SDK or network.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .adapters.base import AgentEvent
from .config import log
from .runner_backends import CODEX_SDK_READONLY_BACKEND

_TRANSCRIPT_LIMIT = 64_000

EventSink = Callable[[AgentEvent], Awaitable[None] | None]


class CodexSdkSetupError(RuntimeError):
    """SDK setup/import failed before a reusable runtime session existed."""


@dataclass
class CodexSdkSession:
    runtime_session_id: str
    cell_id: str
    sdk_thread_id: str = ""
    client: Any = None
    thread: Any = None
    sandbox: Any = None
    client_context: Any = None
    buffer: str = ""
    closed: bool = False


@dataclass
class CodexSdkRunResult:
    text: str = ""
    raw: Any = None


def _bounded_append(existing: str, addition: str) -> str:
    text = (existing or "") + (addition or "")
    if len(text) > _TRANSCRIPT_LIMIT:
        return text[-_TRANSCRIPT_LIMIT:]
    return text


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _extract_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    for attr in ("final_response", "output_text", "text", "content"):
        value = getattr(result, attr, None)
        if isinstance(value, str) and value:
            return value
    if isinstance(result, dict):
        for key in ("final_response", "output_text", "text", "content"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value
    return str(result)


def _extract_thread_id(thread: Any) -> str:
    if thread is None:
        return ""
    if isinstance(thread, str):
        return thread
    if isinstance(thread, dict):
        return str(thread.get("id", "") or thread.get("thread_id", "") or "")
    return str(getattr(thread, "id", "") or getattr(thread, "thread_id", "") or "")


def _has_supported_run_surface(client: Any, thread: Any) -> bool:
    if callable(getattr(thread, "run", None)):
        return True
    if callable(getattr(client, "run", None)) or callable(getattr(client, "run_turn", None)):
        return True
    threads = getattr(client, "threads", None)
    return callable(getattr(threads, "run", None)) if threads is not None else False


def _read_only_sandbox_token(value: Any) -> str:
    return str(value).strip().lower().replace("_", "-")


def _is_read_only_sandbox(value: Any) -> bool:
    """Return whether an SDK sandbox sentinel represents read-only mode.

    The official Codex SDK exposes Sandbox.read_only as a string-like enum whose
    value is ``read-only`` while ``str(enum_member)`` may be ``Sandbox.read_only``.
    Prefer the string payload and enum-like ``value``/``name`` attributes over a
    blind ``str(...)`` check so that fail-closed validation accepts only the
    SDK's read-only sentinel and keeps broader sandbox modes rejected.
    """
    if value in (None, False):
        return False

    candidates: list[str] = []
    if isinstance(value, str):
        candidates.append(value)
    for attr in ("value", "name"):
        candidate = getattr(value, attr, None)
        if isinstance(candidate, str):
            candidates.append(candidate)

    return any(
        _read_only_sandbox_token(candidate) in {"read-only", "readonly"}
        for candidate in candidates
    )


class DefaultCodexSdkClientFactory:
    """Best-effort optional import boundary for the real Codex SDK."""

    def __call__(self):
        try:
            from openai_codex import AsyncCodex, Sandbox  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised with injection
            raise CodexSdkSetupError(
                "Codex SDK is not installed; install/enable it explicitly to "
                "use runner_backend='codex-sdk-readonly'."
            ) from exc
        read_only = getattr(Sandbox, "read_only", None)
        if read_only is None:
            raise CodexSdkSetupError(
                "Installed Codex SDK does not expose Sandbox.read_only."
            )
        return AsyncCodex(), read_only


class CodexSdkReadonlyRunner:
    """Owns SDK runtime sessions and exact v0 lifecycle cleanup."""

    def __init__(
        self,
        state,
        *,
        event_sink: EventSink | None = None,
        client_factory: Callable[[], Any] | None = None,
        terminal_output: Callable[[str, str, str], Awaitable[None] | None] | None = None,
    ) -> None:
        self.state = state
        self.event_sink = event_sink
        self.client_factory = client_factory or DefaultCodexSdkClientFactory()
        self.terminal_output = terminal_output
        self.sessions: dict[str, CodexSdkSession] = {}
        self.session_by_cell: dict[str, str] = {}
        self.active_runs: dict[str, asyncio.Task] = {}

    async def _emit_event(self, cell_id: str, event_type: str, data: dict | None = None) -> None:
        if not self.event_sink:
            return
        result = self.event_sink(AgentEvent(
            cell_id=cell_id,
            timestamp=time.time(),
            event_type=event_type,
            data=data or {},
        ))
        if inspect.isawaitable(result):
            await result

    async def _terminal_output(self, session: CodexSdkSession, text: str) -> None:
        if not text:
            return
        session.buffer = _bounded_append(session.buffer, text)
        if self.terminal_output:
            result = self.terminal_output(session.cell_id, session.runtime_session_id, text)
            if inspect.isawaitable(result):
                await result

    def _save_emit(self, cell, *, coalesce_ephemeral: bool = False) -> None:
        self.state._emit_agent(cell, coalesce_ephemeral=coalesce_ephemeral)
        self.state._db_save_agent(cell)

    def _clear_runtime_ids(self, cell) -> None:
        cell.session_id = None
        cell.agent_session_id = ""
        cell.current_process = ""
        cell.current_path = ""
        cell.current_branch = ""
        cell.git_root = ""
        cell.activity = ""
        cell.activity_detail = ""

    async def _enter_client(self, client: Any) -> tuple[Any, Any | None]:
        enter = getattr(client, "__aenter__", None)
        if not callable(enter):
            return client, None
        entered = await _maybe_await(enter())
        return entered or client, client

    async def _close_client(self, session: CodexSdkSession) -> None:
        context_manager = session.client_context
        if context_manager is not None:
            exit_ = getattr(context_manager, "__aexit__", None)
            if callable(exit_):
                await _maybe_await(exit_(None, None, None))
                return
        for method_name in ("aclose", "close"):
            close = getattr(session.client, method_name, None)
            if callable(close):
                await _maybe_await(close())
                return

    async def create_session(
        self,
        cell,
        *,
        sdk_system_prompt: str = "",
        system_prompt: str = "",
        **_kwargs,
    ) -> None:
        client_for_cleanup = None
        context_for_cleanup = None
        try:
            factory_result = self.client_factory()
            factory_result = await _maybe_await(factory_result)
            if isinstance(factory_result, tuple):
                client, read_only = factory_result
            else:
                client = factory_result
                read_only = getattr(client, "read_only_sandbox", "read_only")
            if read_only in (None, False):
                raise CodexSdkSetupError(
                    "Codex SDK read-only sandbox is unavailable."
                )
            if not _is_read_only_sandbox(read_only):
                raise CodexSdkSetupError(
                    "Codex SDK runner only supports read-only sandbox mode."
                )
            client, context_for_cleanup = await self._enter_client(client)
            client_for_cleanup = client
            thread = await self._create_thread(
                client,
                sandbox=read_only,
                system_prompt=sdk_system_prompt or system_prompt or "",
                cwd=cell.directory or None,
            )
            if not _has_supported_run_surface(client, thread):
                raise CodexSdkSetupError(
                    "Codex SDK client does not expose a supported thread run API."
                )
            thread_id = _extract_thread_id(thread)
            runtime_session_id = "sdk-" + uuid.uuid4().hex
            session = CodexSdkSession(
                runtime_session_id=runtime_session_id,
                cell_id=cell.id,
                sdk_thread_id=thread_id,
                client=client,
                thread=thread,
                sandbox=read_only,
                client_context=context_for_cleanup,
            )
            self.sessions[runtime_session_id] = session
            self.session_by_cell[cell.id] = runtime_session_id
            cell.runner_backend = CODEX_SDK_READONLY_BACKEND
            cell.session_id = runtime_session_id
            cell.agent_session_id = thread_id
            cell.status = "idle"
            cell.current_process = "codex-sdk"
            cell.current_path = cell.directory or ""
            cell.current_branch = ""
            cell.git_root = ""
            cell.activity = ""
            cell.activity_detail = ""
            cell.error_message = ""
            cell.needs_attention = False
            cell.worktree_path = ""
            cell.worktree_branch = ""
            cell.worktree_repo_root = ""
            cell.worktree_base_branch = ""
            cell.worktree_auto_checkpoint = False
            cell.checkpoint_on_progress = False
            self.state.mark_agent_heartbeat(cell, emit=False)
            self._save_emit(cell)
            await self._emit_event(cell.id, "session_start", {
                "runner_backend": CODEX_SDK_READONLY_BACKEND,
                "torque_session_id": runtime_session_id,
                "sdk_thread_id": thread_id,
                "session_id": thread_id,
            })
        except Exception as exc:
            self.sessions.pop(str(getattr(cell, "session_id", "") or ""), None)
            self.session_by_cell.pop(getattr(cell, "id", ""), None)
            self.active_runs.pop(getattr(cell, "id", ""), None)
            cell.runner_backend = CODEX_SDK_READONLY_BACKEND
            self._clear_runtime_ids(cell)
            cell.status = "error"
            cell.error_message = str(exc) or exc.__class__.__name__
            cell.needs_attention = True
            cell.last_event_text = cell.error_message
            self.state.mark_agent_heartbeat(cell, emit=False)
            self._save_emit(cell)
            await self._emit_event(cell.id, "error", {"error": cell.error_message})
            if client_for_cleanup is not None:
                session = CodexSdkSession(
                    runtime_session_id="",
                    cell_id=getattr(cell, "id", ""),
                    client=client_for_cleanup,
                    client_context=context_for_cleanup,
                )
                with contextlib.suppress(Exception):
                    await self._close_client(session)
            raise

    async def _create_thread(self, client, *, sandbox, system_prompt: str, cwd: str | None = None):
        thread_start = getattr(client, "thread_start", None)
        if callable(thread_start):
            kwargs = {"sandbox": sandbox}
            if cwd:
                kwargs["cwd"] = cwd
            if system_prompt:
                kwargs["developer_instructions"] = system_prompt
            return await _maybe_await(thread_start(**kwargs))
        if hasattr(client, "create_thread"):
            return await _maybe_await(client.create_thread(
                sandbox=sandbox,
                system_prompt=system_prompt,
            ))
        threads = getattr(client, "threads", None)
        creator = getattr(threads, "create", None) if threads is not None else None
        if callable(creator):
            return await _maybe_await(creator(
                sandbox=sandbox,
                system_prompt=system_prompt,
            ))
        raise CodexSdkSetupError(
            "Codex SDK client does not expose a supported thread setup API."
        )

    async def _run_turn(self, session: CodexSdkSession, prompt: str, *, sandbox):
        client = session.client
        thread_run = getattr(session.thread, "run", None)
        if callable(thread_run):
            return await _maybe_await(thread_run(prompt, sandbox=sandbox))
        if hasattr(client, "run"):
            return await _maybe_await(client.run(
                thread_id=session.sdk_thread_id,
                prompt=prompt,
                sandbox=sandbox,
            ))
        if hasattr(client, "run_turn"):
            return await _maybe_await(client.run_turn(
                session.sdk_thread_id,
                prompt,
                sandbox=sandbox,
            ))
        threads = getattr(client, "threads", None)
        runner = getattr(threads, "run", None) if threads is not None else None
        if callable(runner):
            return await _maybe_await(runner(
                session.sdk_thread_id,
                prompt=prompt,
                sandbox=sandbox,
            ))
        raise RuntimeError("Codex SDK client does not expose a supported run API")

    async def send_text(self, session_id: str, text: str) -> None:
        prompt = str(text or "").rstrip("\r\n")
        if not prompt:
            return
        session = self.sessions.get(session_id)
        if not session or session.closed:
            return
        cell = self.state.agents.get(session.cell_id)
        if not cell:
            return
        if session.cell_id in self.active_runs:
            raise RuntimeError("Codex SDK read-only session already has an active run")
        read_only = session.sandbox or getattr(session.client, "read_only_sandbox", "read_only")

        async def _run() -> None:
            try:
                cell.status = "running"
                cell.activity = "thinking"
                cell.activity_detail = "Codex SDK read-only run started"
                cell.error_message = ""
                cell.needs_attention = False
                cell.last_event_text = cell.activity_detail
                self.state.mark_agent_progress(cell, emit=False)
                self._save_emit(cell, coalesce_ephemeral=True)
                await self._emit_event(cell.id, "progress", {"detail": cell.activity_detail})
                await self._terminal_output(session, f"\n> {prompt}\n")
                result = await self._run_turn(session, prompt, sandbox=read_only)
                response = _extract_text(result)
                await self._terminal_output(session, response + ("\n" if response else ""))
                await self._emit_event(cell.id, "message", {"message": response})
                await self._emit_event(cell.id, "session_end", {
                    "reason": "completed",
                    "summary": response[:1000],
                    "runner_backend": CODEX_SDK_READONLY_BACKEND,
                })
                # Explicit final state wins over generic EventBus semantics.
                if cell.id in self.session_by_cell:
                    cell.status = "idle"
                    cell.session_id = session.runtime_session_id
                    cell.agent_session_id = session.sdk_thread_id
                    cell.activity = ""
                    cell.activity_detail = ""
                    cell.error_message = ""
                    cell.needs_attention = False
                    self.state.mark_agent_heartbeat(cell, emit=False)
                    self._save_emit(cell)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                await self._emit_event(cell.id, "error", {"error": message})
                await self._emit_event(cell.id, "session_end", {
                    "reason": "error",
                    "runner_backend": CODEX_SDK_READONLY_BACKEND,
                })
                self.sessions.pop(session.runtime_session_id, None)
                self.session_by_cell.pop(cell.id, None)
                self._clear_runtime_ids(cell)
                cell.status = "error"
                cell.error_message = message
                cell.needs_attention = True
                cell.last_event_text = message
                self.state.mark_agent_heartbeat(cell, emit=False)
                self._save_emit(cell)
                raise
            finally:
                self.active_runs.pop(session.cell_id, None)

        task = asyncio.create_task(_run())
        self.active_runs[session.cell_id] = task
        await task

    async def close_session(self, session_id: str, *, reason: str = "SDK run cancelled") -> None:
        session = self.sessions.get(session_id)
        if not session:
            return
        cell = self.state.agents.get(session.cell_id)
        run_task = self.active_runs.pop(session.cell_id, None)
        try:
            if run_task and not run_task.done():
                run_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run_task
            await self._close_client(session)
            self.sessions.pop(session_id, None)
            self.session_by_cell.pop(session.cell_id, None)
            session.closed = True
            if cell:
                self._clear_runtime_ids(cell)
                cell.status = "stopped"
                cell.error_message = ""
                cell.needs_attention = False
                cell.last_event_text = reason or "SDK run cancelled"
                self.state.mark_agent_heartbeat(cell, emit=False)
                self._save_emit(cell)
                await self._emit_event(cell.id, "session_end", {
                    "reason": "cancelled",
                    "runner_backend": CODEX_SDK_READONLY_BACKEND,
                })
        except Exception as exc:
            self.sessions.pop(session_id, None)
            self.session_by_cell.pop(session.cell_id, None)
            if cell:
                self._clear_runtime_ids(cell)
                cell.status = "error"
                cell.error_message = str(exc) or exc.__class__.__name__
                cell.needs_attention = True
                cell.last_event_text = cell.error_message
                self.state.mark_agent_heartbeat(cell, emit=False)
                self._save_emit(cell)
                await self._emit_event(cell.id, "error", {"error": cell.error_message})
            raise

    async def reconnect_orphans(self) -> None:
        for cell in list(getattr(self.state, "agents", {}).values()):
            if str(getattr(cell, "runner_backend", "") or "pty") != CODEX_SDK_READONLY_BACKEND:
                continue
            if not getattr(cell, "session_id", None) and getattr(cell, "status", "") == "stopped":
                continue
            old_session = str(getattr(cell, "session_id", "") or "")
            if old_session:
                self.sessions.pop(old_session, None)
            self.session_by_cell.pop(cell.id, None)
            task = self.active_runs.pop(cell.id, None)
            if task and not task.done():
                task.cancel()
            self._clear_runtime_ids(cell)
            cell.status = "stopped"
            cell.error_message = ""
            cell.needs_attention = False
            cell.last_event_text = "SDK session not resumed after restart"
            self.state.mark_agent_heartbeat(cell, emit=False)
            self._save_emit(cell)

    def get_terminal_buffer(self, session_id: str) -> str:
        session = self.sessions.get(session_id)
        return session.buffer if session else ""
