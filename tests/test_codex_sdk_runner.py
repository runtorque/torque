import asyncio
import builtins
import unittest
from enum import Enum
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.codex_sdk_runner import (
    CodexSdkReadonlyRunner,
    CodexSdkSetupError,
    DefaultCodexSdkClientFactory,
)
from torque.runner_backends import CODEX_SDK_READONLY_BACKEND
from torque.state import AgentCell, MatrixState


class FakeClient:
    def __init__(self, *, thread_id="thread-1", response="ok", fail_run=None, close_error=None):
        self.thread_id = thread_id
        self.response = response
        self.fail_run = fail_run
        self.close_error = close_error
        self.create_calls = []
        self.run_calls = []
        self.read_only_sandbox = "read_only"
        self.closed = False

    async def create_thread(self, *, sandbox, system_prompt=""):
        self.create_calls.append({"sandbox": sandbox, "system_prompt": system_prompt})
        return {"id": self.thread_id}

    async def run(self, *, thread_id, prompt, sandbox):
        self.run_calls.append({"thread_id": thread_id, "prompt": prompt, "sandbox": sandbox})
        if self.fail_run:
            raise self.fail_run
        return {"text": self.response}

    async def close(self):
        if self.close_error:
            raise self.close_error
        self.closed = True


class DocLikeThread:
    def __init__(self, thread_id="doc-thread", response="doc ok", fail_run=None):
        self.id = thread_id
        self.response = response
        self.fail_run = fail_run
        self.run_calls = []

    async def run(self, prompt, *, sandbox):
        self.run_calls.append({"prompt": prompt, "sandbox": sandbox})
        if self.fail_run:
            raise self.fail_run
        return {"text": self.response}


class DocLikeAsyncCodex:
    def __init__(self, thread=None):
        self.thread = thread or DocLikeThread()
        self.entered = False
        self.exited = False
        self.thread_start_calls = []

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True

    async def thread_start(self, **kwargs):
        self.thread_start_calls.append(kwargs)
        return self.thread


class StringLikeSandbox(str, Enum):
    read_only = "read-only"
    workspace_write = "workspace-write"

    def __str__(self):
        return f"{type(self).__name__}.{self.name}"


class CodexSdkRunnerTests(unittest.IsolatedAsyncioTestCase):
    def _state_cell(self):
        state = MatrixState()
        cell = AgentCell(
            id="a1", name="SDK", group="g", slug="sdk",
            agent_type="codex", runner_backend=CODEX_SDK_READONLY_BACKEND,
            directory="/repo", worktree_path="/stale", worktree_branch="stale",
            worktree_repo_root="/repo", worktree_auto_checkpoint=True,
            checkpoint_on_progress=True,
        )
        state.agents[cell.id] = cell
        state.groups["g"] = [cell.id]
        return state, cell

    async def test_setup_failure_is_error_and_clears_ids(self):
        state, cell = self._state_cell()
        events = []
        runner = CodexSdkReadonlyRunner(
            state,
            event_sink=lambda event: events.append(event),
            client_factory=lambda: (_ for _ in ()).throw(CodexSdkSetupError("missing sdk")),
        )
        with self.assertRaises(CodexSdkSetupError):
            await runner.create_session(cell)
        self.assertEqual(cell.status, "error")
        self.assertTrue(cell.needs_attention)
        self.assertIn("missing sdk", cell.error_message)
        self.assertIsNone(cell.session_id)
        self.assertEqual(cell.agent_session_id, "")
        self.assertEqual(runner.sessions, {})
        self.assertEqual(runner.active_runs, {})
        self.assertEqual(events[-1].event_type, "error")

    def test_default_factory_missing_sdk_dependency_is_actionable_setup_error(self):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "openai_codex":
                raise ModuleNotFoundError(name)
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            with self.assertRaises(CodexSdkSetupError) as ctx:
                DefaultCodexSdkClientFactory()()

        self.assertIn("Codex SDK is not installed", str(ctx.exception))
        self.assertIn("runner_backend='codex-sdk-readonly'", str(ctx.exception))

    async def test_ready_before_prompt_is_idle_with_sdk_ids_no_worktree(self):
        state, cell = self._state_cell()
        fake = FakeClient(thread_id="thread-x")
        events = []
        runner = CodexSdkReadonlyRunner(
            state,
            event_sink=lambda event: events.append(event),
            client_factory=lambda: (fake, "read_only"),
        )
        await runner.create_session(cell, sdk_system_prompt="sys")
        self.assertEqual(cell.status, "idle")
        self.assertTrue(cell.session_id.startswith("sdk-"))
        self.assertEqual(cell.agent_session_id, "thread-x")
        self.assertFalse(cell.needs_attention)
        self.assertEqual(cell.worktree_path, "")
        self.assertFalse(cell.worktree_auto_checkpoint)
        self.assertEqual(fake.create_calls[0]["sandbox"], "read_only")
        self.assertEqual(fake.create_calls[0]["system_prompt"], "sys")
        self.assertEqual(events[-1].event_type, "session_start")
        self.assertEqual(events[-1].data["runner_backend"], CODEX_SDK_READONLY_BACKEND)

    async def test_documented_async_codex_thread_start_and_thread_run_shape(self):
        state, cell = self._state_cell()
        thread = DocLikeThread(thread_id="doc-thread", response="doc final")
        client = DocLikeAsyncCodex(thread=thread)
        runner = CodexSdkReadonlyRunner(
            state,
            client_factory=lambda: (client, "read_only"),
        )
        await runner.create_session(cell, sdk_system_prompt="sys")
        sid = cell.session_id
        self.assertEqual(cell.status, "idle")
        self.assertEqual(cell.agent_session_id, "doc-thread")
        self.assertTrue(client.entered)
        self.assertEqual(client.thread_start_calls[0]["sandbox"], "read_only")
        self.assertEqual(client.thread_start_calls[0]["cwd"], "/repo")
        self.assertEqual(client.thread_start_calls[0]["developer_instructions"], "sys")
        self.assertNotIn("system_prompt", client.thread_start_calls[0])

        await runner.send_text(sid, "hello")
        self.assertEqual(cell.status, "idle")
        self.assertEqual(cell.session_id, sid)
        self.assertEqual(thread.run_calls[0], {"prompt": "hello", "sandbox": "read_only"})
        self.assertIn("doc final", runner.get_terminal_buffer(sid))

        await runner.close_session(sid)
        self.assertTrue(client.exited)
        self.assertEqual(cell.status, "stopped")

    async def test_unsupported_setup_fails_closed_without_fabricated_thread_id(self):
        class UnsupportedClient:
            read_only_sandbox = "read_only"

            def __init__(self):
                self.entered = False
                self.exited = False

            async def __aenter__(self):
                self.entered = True
                return self

            async def __aexit__(self, exc_type, exc, tb):
                self.exited = True

        state, cell = self._state_cell()
        client = UnsupportedClient()
        runner = CodexSdkReadonlyRunner(
            state,
            client_factory=lambda: (client, "read_only"),
        )
        with self.assertRaises(CodexSdkSetupError):
            await runner.create_session(cell)
        self.assertTrue(client.entered)
        self.assertTrue(client.exited)
        self.assertEqual(cell.status, "error")
        self.assertIsNone(cell.session_id)
        self.assertEqual(cell.agent_session_id, "")
        self.assertEqual(runner.sessions, {})

    async def test_run_success_returns_idle_retaining_reusable_ids(self):
        state, cell = self._state_cell()
        fake = FakeClient(thread_id="thread-x", response="final answer")
        events = []
        runner = CodexSdkReadonlyRunner(
            state,
            event_sink=lambda event: events.append(event),
            client_factory=lambda: (fake, "read_only"),
        )
        await runner.create_session(cell)
        sid = cell.session_id
        await runner.send_text(sid, "hello\r")
        self.assertEqual(cell.status, "idle")
        self.assertEqual(cell.session_id, sid)
        self.assertEqual(cell.agent_session_id, "thread-x")
        self.assertEqual(runner.active_runs, {})
        self.assertIn("final answer", runner.get_terminal_buffer(sid))
        self.assertEqual(fake.run_calls[0]["sandbox"], "read_only")
        self.assertIn("session_end", [event.event_type for event in events])

    async def test_run_error_fails_closed_and_clears_maps(self):
        state, cell = self._state_cell()
        fake = FakeClient(thread_id="thread-x", fail_run=RuntimeError("boom"))
        events = []
        runner = CodexSdkReadonlyRunner(
            state,
            event_sink=lambda event: events.append(event),
            client_factory=lambda: (fake, "read_only"),
        )
        await runner.create_session(cell)
        with self.assertRaises(RuntimeError):
            await runner.send_text(cell.session_id, "hello")
        self.assertEqual(cell.status, "error")
        self.assertTrue(cell.needs_attention)
        self.assertIn("boom", cell.error_message)
        self.assertIsNone(cell.session_id)
        self.assertEqual(cell.agent_session_id, "")
        self.assertEqual(runner.sessions, {})
        self.assertEqual(runner.active_runs, {})
        self.assertIn("error", [event.event_type for event in events])

    async def test_explicit_close_idle_stops_and_clears_ids(self):
        state, cell = self._state_cell()
        fake = FakeClient(thread_id="thread-x")
        events = []
        runner = CodexSdkReadonlyRunner(
            state,
            event_sink=lambda event: events.append(event),
            client_factory=lambda: (fake, "read_only"),
        )
        await runner.create_session(cell)
        await runner.close_session(cell.session_id)
        self.assertEqual(cell.status, "stopped")
        self.assertIsNone(cell.session_id)
        self.assertEqual(cell.agent_session_id, "")
        self.assertEqual(cell.current_process, "")
        self.assertEqual(cell.current_path, "")
        self.assertFalse(cell.needs_attention)
        self.assertTrue(fake.closed)
        self.assertEqual(events[-1].data["reason"], "cancelled")

    async def test_cancel_exception_is_error_and_clears_ids(self):
        state, cell = self._state_cell()
        fake = FakeClient(thread_id="thread-x", close_error=RuntimeError("close boom"))
        runner = CodexSdkReadonlyRunner(
            state,
            client_factory=lambda: (fake, "read_only"),
        )
        await runner.create_session(cell)
        with self.assertRaises(RuntimeError):
            await runner.close_session(cell.session_id)
        self.assertEqual(cell.status, "error")
        self.assertIsNone(cell.session_id)
        self.assertEqual(cell.agent_session_id, "")
        self.assertEqual(runner.sessions, {})

    async def test_reconnect_non_adoption_marks_stale_sdk_stopped(self):
        state, cell = self._state_cell()
        cell.session_id = "sdk-old"
        cell.agent_session_id = "thread-old"
        cell.status = "running"
        cell.current_process = "codex-sdk"
        runner = CodexSdkReadonlyRunner(state)
        await runner.reconnect_orphans()
        self.assertEqual(cell.status, "stopped")
        self.assertIsNone(cell.session_id)
        self.assertEqual(cell.agent_session_id, "")
        self.assertEqual(cell.last_event_text, "SDK session not resumed after restart")


    async def test_hyphenated_read_only_sandbox_values_are_accepted(self):
        for sandbox in ("read-only", StringLikeSandbox.read_only):
            with self.subTest(sandbox=repr(sandbox)):
                state, cell = self._state_cell()
                fake = FakeClient(thread_id="thread-x")
                runner = CodexSdkReadonlyRunner(
                    state,
                    client_factory=lambda sandbox=sandbox: (fake, sandbox),
                )
                await runner.create_session(cell)
                self.assertEqual(cell.status, "idle")
                self.assertEqual(fake.create_calls[0]["sandbox"], sandbox)
                self.assertEqual(runner.sessions[cell.session_id].sandbox, sandbox)

    async def test_broader_sandbox_values_are_rejected(self):
        rejected = (
            "workspace_write",
            "workspace-write",
            "danger-full-access",
            StringLikeSandbox.workspace_write,
            True,
            object(),
        )
        for sandbox in rejected:
            with self.subTest(sandbox=repr(sandbox)):
                state, cell = self._state_cell()
                fake = FakeClient()
                runner = CodexSdkReadonlyRunner(
                    state,
                    client_factory=lambda sandbox=sandbox: (fake, sandbox),
                )
                with self.assertRaises(CodexSdkSetupError):
                    await runner.create_session(cell)
                self.assertEqual(cell.status, "error")
                self.assertEqual(fake.create_calls, [])

    async def test_write_sandbox_sentinel_rejected(self):
        state, cell = self._state_cell()
        fake = FakeClient()
        runner = CodexSdkReadonlyRunner(
            state,
            client_factory=lambda: (fake, "workspace_write"),
        )
        with self.assertRaises(CodexSdkSetupError):
            await runner.create_session(cell)
