import asyncio
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.codex_sdk_runner import CodexSdkReadonlyRunner, CodexSdkSetupError
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

    async def test_write_sandbox_sentinel_rejected(self):
        state, cell = self._state_cell()
        fake = FakeClient()
        runner = CodexSdkReadonlyRunner(
            state,
            client_factory=lambda: (fake, "workspace_write"),
        )
        with self.assertRaises(CodexSdkSetupError):
            await runner.create_session(cell)
