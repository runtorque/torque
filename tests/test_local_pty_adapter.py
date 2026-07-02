import asyncio
import importlib
import json
import os
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class LocalPtyAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.pty_mod = importlib.import_module("torque.local_pty")
        self.pty_mod = importlib.reload(self.pty_mod)

    def test_capabilities_expose_embedded_terminal_without_toolbelt_registration(self):
        self.assertTrue(self.pty_mod.LocalPtyAdapter.capabilities.supports_embedded_terminal)
        self.assertTrue(self.pty_mod.LocalPtyAdapter.capabilities.supports_focus_tracking)
        self.assertFalse(self.pty_mod.LocalPtyAdapter.capabilities.supports_toolbelt_registration)

    async def test_create_session_emits_output_and_tracks_focus(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_terminal(
            name="Terminal 1",
            group="Torque",
            terminal_backend="pty",
            command="printf 'hello-torque\\n'",
        )
        adapter = self.pty_mod.LocalPtyAdapter(state)
        seen = asyncio.Future()

        async def on_output(cell_id, session_id, text):
            if (
                cell_id == cell.id
                and "hello-torque" in text
                and not seen.done()
            ):
                seen.set_result((cell_id, session_id, text))

        adapter.on_terminal_output = on_output
        await adapter.start()
        await adapter.create_session(cell)

        result = await asyncio.wait_for(seen, timeout=4)

        self.assertEqual(result[0], cell.id)
        self.assertEqual(state.active_session_id, cell.session_id)
        self.assertEqual(state.current_window_id, "standalone")
        self.assertIsNotNone(cell.session_id)

        await adapter.close_session(cell.session_id)

    async def test_create_session_preserves_existing_focus(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        first = state.add_terminal(
            name="Terminal 1",
            group="Torque",
            terminal_backend="pty",
            command="sleep 30",
        )
        second = state.add_terminal(
            name="Terminal 2",
            group="Torque",
            terminal_backend="pty",
            command="sleep 30",
        )
        adapter = self.pty_mod.LocalPtyAdapter(state)

        # Capture every batch of delta ops that gets flushed. `broadcast()`
        # is the only path that flushes `_delta_ops`, so we can verify the
        # quiet create still surfaces the new agent to the UI without
        # relying on the focus_session code path.
        flushed_batches: list[list[dict]] = []
        orig_broadcast = state.broadcast

        async def recording_broadcast():
            if state._delta_ops:
                flushed_batches.append(list(state._delta_ops))
            await orig_broadcast()

        state.broadcast = recording_broadcast

        try:
            await adapter.start()
            await adapter.create_session(first)
            first_session = first.session_id
            self.assertIsNotNone(first_session)
            self.assertEqual(state.active_session_id, first_session)

            batches_before_second = len(flushed_batches)
            await adapter.create_session(second)
            self.assertIsNotNone(second.session_id)
            self.assertNotEqual(second.session_id, first_session)
            # Creating a second session must not steal focus from the first.
            self.assertEqual(state.active_session_id, first_session)

            # And crucially, the quiet path must still flush the queued
            # `agent_upsert` so the UI learns about the new cell right
            # away instead of having to wait for an unrelated later
            # mutation to piggyback the delta.
            self.assertGreater(len(flushed_batches), batches_before_second)
            new_ops = [
                op
                for batch in flushed_batches[batches_before_second:]
                for op in batch
            ]
            self.assertTrue(
                any(
                    op.get("op") == "agent_upsert" and op.get("id") == second.id
                    for op in new_ops
                ),
                f"second session's agent_upsert missing from flushed ops: {new_ops}",
            )
            self.assertEqual(state._delta_ops, [])
        finally:
            state.broadcast = orig_broadcast
            if first.session_id:
                await adapter.close_session(first.session_id)
            if second.session_id:
                await adapter.close_session(second.session_id)

    async def test_create_session_focuses_when_active_session_has_exited(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_terminal(
            name="Terminal 1",
            group="Torque",
            terminal_backend="pty",
            command="sleep 30",
        )
        # Simulate a previously active session that has already been torn down
        # (e.g., the user closed the focused agent). The adapter should refocus
        # the newly created session since nothing is actively tracked.
        state.active_session_id = "stale-session-id"
        adapter = self.pty_mod.LocalPtyAdapter(state)

        try:
            await adapter.start()
            await adapter.create_session(cell)
            self.assertIsNotNone(cell.session_id)
            self.assertEqual(state.active_session_id, cell.session_id)
        finally:
            if cell.session_id:
                await adapter.close_session(cell.session_id)

    async def test_create_session_restore_focus_to_prev_tab_never_steals_focus(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_terminal(
            name="Background Worker",
            group="Torque",
            terminal_backend="pty",
            command="sleep 30",
        )
        state.active_session_id = "stale-session-id"
        adapter = self.pty_mod.LocalPtyAdapter(state)

        emitted = []
        flushed_batches: list[list[dict]] = []
        orig_emit = state._emit
        orig_broadcast = state.broadcast

        def recording_emit(op, **payload):
            emitted.append((op, payload))
            return orig_emit(op, **payload)

        async def recording_broadcast():
            if state._delta_ops:
                flushed_batches.append(list(state._delta_ops))
            await orig_broadcast()

        state._emit = recording_emit
        state.broadcast = recording_broadcast

        try:
            await adapter.start()
            await adapter.create_session(cell, restore_focus_to_prev_tab=True)
            self.assertIsNotNone(cell.session_id)
            self.assertEqual(state.active_session_id, "stale-session-id")
            self.assertFalse(
                any(op == "focus_update" for op, _payload in emitted),
                f"background create emitted focus_update: {emitted}",
            )
            self.assertTrue(
                any(
                    op.get("op") == "agent_upsert" and op.get("id") == cell.id
                    for batch in flushed_batches
                    for op in batch
                ),
                f"background agent_upsert missing from flushed ops: {flushed_batches}",
            )
        finally:
            state._emit = orig_emit
            state.broadcast = orig_broadcast
            if cell.session_id:
                await adapter.close_session(cell.session_id)

    async def test_client_scoped_focus_does_not_broadcast_to_other_clients(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        first = state.add_terminal(
            name="Terminal 1",
            group="Torque",
            terminal_backend="pty",
        )
        second = state.add_terminal(
            name="Terminal 2",
            group="Torque",
            terminal_backend="pty",
        )
        first.session_id = "session-a"
        second.session_id = "session-b"
        adapter = self.pty_mod.LocalPtyAdapter(state)
        adapter._sessions[first.session_id] = self.pty_mod._PtySession(
            session_id=first.session_id,
            cell_id=first.id,
            process=None,
            master_fd=-1,
        )
        adapter._sessions[second.session_id] = self.pty_mod._PtySession(
            session_id=second.session_id,
            cell_id=second.id,
            process=None,
            master_fd=-1,
        )

        class FakeWs:
            def __init__(self):
                self.sent = []

            async def send_str(self, payload):
                self.sent.append(json.loads(payload))

        ws_a = FakeWs()
        ws_b = FakeWs()
        async with state._ws_clients_lock:
            state._register_ws_client_locked(ws_a, "client-a")
            state._register_ws_client_locked(ws_b, "client-b")

        state.active_session_id = first.session_id
        state.current_window_id = "standalone"
        emitted = []
        broadcasts = []
        state._emit = lambda op, **payload: emitted.append((op, payload))

        async def broadcast():
            broadcasts.append(True)

        state.broadcast = broadcast

        ok = await adapter.focus_session(second.session_id, client_id="client-b")

        self.assertTrue(ok)
        self.assertEqual(state.active_session_id, first.session_id)
        self.assertEqual(emitted, [])
        self.assertEqual(broadcasts, [])
        self.assertEqual(ws_a.sent, [])
        self.assertEqual(
            ws_b.sent,
            [{
                "type": "focus_update",
                "client_scoped": True,
                "active_session_id": second.session_id,
                "current_window_id": "standalone",
            }],
        )
        self.assertEqual(
            state.client_focus_state("client-b"),
            {
                "active_session_id": second.session_id,
                "current_window_id": "standalone",
            },
        )

    async def test_stopping_session_clears_only_clients_focused_on_it(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        first = state.add_terminal(
            name="Terminal 1",
            group="Torque",
            terminal_backend="pty",
        )
        second = state.add_terminal(
            name="Terminal 2",
            group="Torque",
            terminal_backend="pty",
        )
        first.session_id = "session-a"
        second.session_id = "session-b"
        first.status = "running"
        second.status = "running"
        adapter = self.pty_mod.LocalPtyAdapter(state)

        class FakeWs:
            def __init__(self):
                self.sent = []

            async def send_str(self, payload):
                self.sent.append(json.loads(payload))

        ws_a = FakeWs()
        ws_b = FakeWs()
        async with state._ws_clients_lock:
            state._register_ws_client_locked(ws_a, "client-a")
            state._register_ws_client_locked(ws_b, "client-b")
        state.set_client_focus_state(
            "client-a",
            active_session_id=first.session_id,
            current_window_id="standalone",
        )
        state.set_client_focus_state(
            "client-b",
            active_session_id=second.session_id,
            current_window_id="standalone",
        )
        state.active_session_id = second.session_id

        emitted = []
        state._emit_agent = lambda cell: emitted.append(
            ("agent", cell.id, cell.status, cell.session_id))
        state._db_save_agent = lambda cell: None
        state._emit = lambda op, **payload: emitted.append((op, payload))

        async def broadcast():
            emitted.append(("broadcast",))

        state.broadcast = broadcast

        await adapter._mark_session_stopped(first, first.session_id, announce=False)

        self.assertEqual(ws_b.sent, [])
        self.assertEqual(
            ws_a.sent,
            [{
                "type": "focus_update",
                "client_scoped": True,
                "active_session_id": None,
                "current_window_id": "standalone",
            }],
        )
        self.assertEqual(
            state.client_focus_state("client-a"),
            {
                "active_session_id": None,
                "current_window_id": "standalone",
            },
        )
        self.assertEqual(
            state.client_focus_state("client-b")["active_session_id"],
            second.session_id,
        )
        self.assertEqual(state.active_session_id, second.session_id)

    async def test_global_stop_focus_delta_preserves_other_client_focus(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        first = state.add_terminal(
            name="Terminal 1",
            group="Torque",
            terminal_backend="pty",
        )
        second = state.add_terminal(
            name="Terminal 2",
            group="Torque",
            terminal_backend="pty",
        )
        first.session_id = "session-a"
        second.session_id = "session-b"
        first.status = "running"
        second.status = "running"
        state._delta_ops = []
        state._seq = 0
        adapter = self.pty_mod.LocalPtyAdapter(state)

        class FakeWs:
            def __init__(self):
                self.sent = []

            async def send_str(self, payload):
                self.sent.append(json.loads(payload))

        ws_global = FakeWs()
        ws_client_b = FakeWs()
        async with state._ws_clients_lock:
            state._register_ws_client_locked(ws_global, "")
            state._register_ws_client_locked(ws_client_b, "client-b")

        state.active_session_id = first.session_id
        state.current_window_id = "standalone"
        state.set_client_focus_state(
            "client-b",
            active_session_id=second.session_id,
            current_window_id="standalone",
        )

        await adapter._mark_session_stopped(first, first.session_id, announce=False)

        self.assertEqual(
            state.client_focus_state("client-b")["active_session_id"],
            second.session_id,
        )

        global_focus_ops = [
            op
            for msg in ws_global.sent
            if msg.get("type") == "delta"
            for op in msg.get("ops", [])
            if op.get("op") == "focus_update"
        ]
        client_b_focus_ops = [
            op
            for msg in ws_client_b.sent
            if msg.get("type") == "delta"
            for op in msg.get("ops", [])
            if op.get("op") == "focus_update"
        ]

        self.assertEqual(
            global_focus_ops[-1],
            {
                "op": "focus_update",
                "active_session_id": None,
                "current_window_id": "standalone",
            },
        )
        self.assertEqual(
            client_b_focus_ops[-1],
            {
                "op": "focus_update",
                "active_session_id": second.session_id,
                "current_window_id": "standalone",
                "client_scoped": True,
            },
        )

    async def test_write_input_accepts_raw_terminal_bytes(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        with tempfile.TemporaryDirectory() as tmpdir:
            cell = state.add_terminal(
                name="Terminal 1",
                group="Torque",
                terminal_backend="pty",
                directory=tmpdir,
            )
            adapter = self.pty_mod.LocalPtyAdapter(state)
            seen = asyncio.Future()

            async def on_output(cell_id, session_id, text):
                if (
                    cell_id == cell.id
                    and "raw-torque" in text
                    and not seen.done()
                ):
                    seen.set_result((cell_id, session_id, text))

            adapter.on_terminal_output = on_output
            await adapter.start()
            await adapter.create_session(cell)
            self.assertIsNotNone(cell.session_id)

            await adapter.write_input(
                cell.session_id,
                "printf 'raw-torque\\n'\r",
            )
            result = await asyncio.wait_for(seen, timeout=4)

            self.assertEqual(result[0], cell.id)
            self.assertIn("raw-torque", result[2])

            await adapter.close_session(cell.session_id)

    async def test_shutdown_closes_live_sessions(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_terminal(
            name="Terminal 1",
            group="Torque",
            terminal_backend="pty",
            command="sleep 30",
        )
        adapter = self.pty_mod.LocalPtyAdapter(state)

        await adapter.start()
        await adapter.create_session(cell)

        self.assertIsNotNone(cell.session_id)
        self.assertEqual(cell.status, "running")

        await adapter.shutdown()

        self.assertIsNone(cell.session_id)
        self.assertEqual(cell.status, "stopped")
        self.assertEqual(adapter._sessions, {})

    async def test_finalize_session_marks_process_exit_stopped_and_persists(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_agent(
            name="Worker",
            group="Torque",
            terminal_backend="pty",
            command="codex",
            directory="/tmp",
        )
        cell.status = "running"
        cell.session_id = "session-exited"
        cell.current_process = "codex"
        cell.current_path = "/tmp"
        cell.current_branch = "feature/test"
        cell.git_root = "/tmp"
        cell.activity = "thinking"
        cell.activity_detail = "Running tests"
        state.active_session_id = cell.session_id

        adapter = self.pty_mod.LocalPtyAdapter(state)
        adapter._sessions[cell.session_id] = self.pty_mod._PtySession(
            session_id=cell.session_id,
            cell_id=cell.id,
            process=None,
            master_fd=-1,
        )

        emitted = []
        saved = []
        broadcasts = []
        state._emit_agent = lambda agent: emitted.append(
            (agent.id, agent.status, agent.session_id))
        state._db_save_agent = lambda agent: saved.append(
            (agent.id, agent.status, agent.session_id))
        state._emit = lambda op, **payload: emitted.append(
            (op, payload.get("active_session_id")))

        async def broadcast():
            broadcasts.append(True)

        state.broadcast = broadcast

        await adapter._finalize_session("session-exited")

        self.assertNotIn("session-exited", adapter._sessions)
        self.assertEqual(cell.status, "stopped")
        self.assertIsNone(cell.session_id)
        self.assertEqual(cell.current_process, "")
        self.assertEqual(cell.current_path, "")
        self.assertEqual(cell.current_branch, "")
        self.assertEqual(cell.git_root, "")
        self.assertEqual(cell.activity, "")
        self.assertEqual(cell.activity_detail, "")
        self.assertIn((cell.id, "stopped", None), emitted)
        self.assertEqual(saved, [(cell.id, "stopped", None)])
        self.assertEqual(broadcasts, [True])
        self.assertIsNone(state.active_session_id)

    async def test_send_text_waits_for_input_ready_signal_for_hook_based_agent(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_agent(
            name="Engineer",
            group="Torque",
            terminal_backend="pty",
            command="claude",
            directory="/tmp",
        )
        cell.agent_type = "claude-code"
        cell.session_id = "session-1"
        adapter = self.pty_mod.LocalPtyAdapter(state)
        adapter._sessions[cell.session_id] = SimpleNamespace(cell_id=cell.id)
        writes: list[tuple[str, str]] = []

        async def fake_write_input(session_id, data):
            writes.append((session_id, data))

        adapter.write_input = fake_write_input

        send_task = asyncio.create_task(
            adapter.send_text(cell.session_id, "Initial prompt")
        )
        await asyncio.sleep(0.05)
        self.assertEqual(writes, [])

        adapter.signal_input_ready(cell.id)
        # Timeout must exceed claude's post_ready_delay (2.5s) plus headroom.
        await asyncio.wait_for(send_task, timeout=5)

        self.assertEqual(
            writes,
            [
                ("session-1", "Initial prompt"),
                ("session-1", "\r"),
            ],
        )

    async def test_send_text_claude_multiline_uses_newline_shortcut_then_submit(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_agent(
            name="Engineer",
            group="Torque",
            terminal_backend="pty",
            command="claude",
            directory="/tmp",
        )
        cell.agent_type = "claude-code"
        cell.session_id = "session-1"
        adapter = self.pty_mod.LocalPtyAdapter(state)
        adapter._sessions[cell.session_id] = SimpleNamespace(cell_id=cell.id)
        writes: list[tuple[str, str]] = []

        async def fake_write_input(session_id, data):
            writes.append((session_id, data))

        adapter.write_input = fake_write_input
        adapter._input_ready_sessions.add(cell.session_id)

        await adapter.send_text(cell.session_id, "line one\nline two")

        self.assertEqual(
            writes,
            [
                ("session-1", "line one"),
                ("session-1", "\n"),
                ("session-1", "line two"),
                ("session-1", "\r"),
            ],
        )

    async def test_send_text_single_line_submits_without_settled_delay_by_default(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_agent(
            name="Engineer",
            group="Torque",
            terminal_backend="pty",
            command="codex",
            directory="/tmp",
        )
        cell.agent_type = "codex"
        cell.session_id = "session-1"
        adapter = self.pty_mod.LocalPtyAdapter(state)
        adapter._sessions[cell.session_id] = SimpleNamespace(cell_id=cell.id)
        adapter._input_ready_sessions.add(cell.session_id)
        writes: list[tuple[str, str]] = []

        async def fake_write_input(session_id, data):
            writes.append((session_id, data))

        delays = []

        async def fake_sleep(delay):
            delays.append(delay)

        adapter.write_input = fake_write_input
        orig_sleep = self.pty_mod.asyncio.sleep
        self.pty_mod.asyncio.sleep = fake_sleep
        try:
            await adapter.send_text(cell.session_id, "Initial prompt")
        finally:
            self.pty_mod.asyncio.sleep = orig_sleep

        self.assertEqual(
            writes,
            [
                ("session-1", "Initial prompt"),
                ("session-1", "\r"),
            ],
        )
        self.assertEqual(delays, [])

    async def test_send_text_settled_submit_waits_before_single_line_submit(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_agent(
            name="Engineer",
            group="Torque",
            terminal_backend="pty",
            command="codex",
            directory="/tmp",
        )
        cell.agent_type = "codex"
        cell.session_id = "session-1"
        adapter = self.pty_mod.LocalPtyAdapter(state)
        adapter._sessions[cell.session_id] = SimpleNamespace(cell_id=cell.id)
        adapter._input_ready_sessions.add(cell.session_id)
        writes: list[tuple[str, str]] = []

        async def fake_write_input(session_id, data):
            writes.append((session_id, data))

        delays = []

        async def fake_sleep(delay):
            delays.append(delay)

        adapter.write_input = fake_write_input
        orig_sleep = self.pty_mod.asyncio.sleep
        self.pty_mod.asyncio.sleep = fake_sleep
        try:
            await adapter.send_text(
                cell.session_id,
                "Initial prompt",
                settled_submit=True,
            )
        finally:
            self.pty_mod.asyncio.sleep = orig_sleep

        self.assertEqual(
            writes,
            [
                ("session-1", "Initial prompt"),
                ("session-1", "\r"),
            ],
        )
        self.assertEqual(delays, [0.3])

    async def test_send_text_waits_for_codex_ready_screen_once_in_standalone(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_agent(
            name="Engineer",
            group="Torque",
            terminal_backend="pty",
            command="codex",
            directory="/tmp",
        )
        cell.agent_type = "codex"
        cell.session_id = "session-2"
        adapter = self.pty_mod.LocalPtyAdapter(state)
        adapter._sessions[cell.session_id] = SimpleNamespace(
            cell_id=cell.id,
            session_id=cell.session_id,
        )
        writes: list[tuple[str, str]] = []
        screen_reads = 0
        ready_screen = "\n".join([
            "OpenAI Codex",
            "model: gpt-5.4 high",
            "directory: ~/repo",
            "› Ready",
        ])
        screen_snapshots = [
            "Loading Codex...",
            ready_screen,
            ready_screen,
        ]

        async def fake_write_input(session_id, data):
            writes.append((session_id, data))

        async def fake_read_screen_text(session):
            nonlocal screen_reads
            idx = min(screen_reads, len(screen_snapshots) - 1)
            screen_reads += 1
            return screen_snapshots[idx]

        delays = []

        async def fake_sleep(delay):
            delays.append(delay)

        adapter.write_input = fake_write_input
        adapter._read_screen_text = fake_read_screen_text
        orig_sleep = self.pty_mod.asyncio.sleep
        self.pty_mod.asyncio.sleep = fake_sleep
        try:
            await adapter.send_text(cell.session_id, "line one\nline two")
            first_reads = screen_reads
            await adapter.send_text(cell.session_id, "follow up")
        finally:
            self.pty_mod.asyncio.sleep = orig_sleep

        self.assertEqual(
            writes,
            [
                ("session-2", "line one\nline two"),
                ("session-2", "\r"),
                ("session-2", "follow up"),
                ("session-2", "\r"),
            ],
        )
        self.assertEqual(first_reads, 3)
        self.assertEqual(screen_reads, first_reads)
        # delays: poll-interval (Loading→ready), poll-interval (ready→stable),
        # post_ready_delay (codex 2.5s), submit-key multiline delay (0.3).
        self.assertEqual(delays, [0.25, 0.25, 2.5, 0.3])

    async def test_send_text_claude_applies_post_ready_delay_after_hook_signal(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_agent(
            name="Engineer",
            group="Torque",
            terminal_backend="pty",
            command="claude",
            directory="/tmp",
        )
        cell.agent_type = "claude-code"
        cell.session_id = "session-claude"
        adapter = self.pty_mod.LocalPtyAdapter(state)
        adapter._sessions[cell.session_id] = SimpleNamespace(
            cell_id=cell.id,
            session_id=cell.session_id,
        )
        writes: list[tuple[str, str]] = []

        async def fake_write_input(session_id, data):
            writes.append((session_id, data))

        delays = []

        async def fake_sleep(delay):
            delays.append(delay)

        adapter.write_input = fake_write_input
        adapter.signal_input_ready(cell.id)
        orig_sleep = self.pty_mod.asyncio.sleep
        self.pty_mod.asyncio.sleep = fake_sleep
        try:
            await adapter.send_text(cell.session_id, "Initial prompt")
        finally:
            self.pty_mod.asyncio.sleep = orig_sleep

        self.assertEqual(
            writes,
            [
                ("session-claude", "Initial prompt"),
                ("session-claude", "\r"),
            ],
        )
        self.assertEqual(delays, [2.5])

    async def test_adopt_supervisor_session_primes_awareness_input_ready(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_agent(
            name="Engineer",
            group="Torque",
            terminal_backend="pty",
            command="claude",
            directory="",
        )
        cell.agent_type = "claude-code"
        cell.session_id = "session-adopted"
        cell.status = "stopped"
        adapter = self.pty_mod.SupervisedPtyAdapter(
            state, "/tmp/torque-test-supervisor.sock")
        subscribed: list[str] = []

        class FakeClient:
            async def subscribe(self, session_id, *, on_output, on_exit):
                del on_output, on_exit
                subscribed.append(session_id)

        adapter._client = FakeClient()
        adapter._input_ready_events[cell.id] = self.pty_mod.asyncio.Event()

        await adapter._adopt_supervisor_session(cell, {
            "session_id": "session-adopted",
            "cols": 120,
            "rows": 32,
            "bootstrap_dir": "",
            "pid": 0,
            "alive": True,
        })

        self.assertEqual(subscribed, ["session-adopted"])
        self.assertIn("session-adopted", adapter._input_ready_sessions)
        self.assertNotIn(cell.id, adapter._input_ready_events)

        writes: list[tuple[str, str]] = []

        async def fake_write_input(session_id, data):
            writes.append((session_id, data))

        async def fail_wait_for(awaitable, timeout):
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise AssertionError(
                f"adopted session should not wait for readiness ({timeout})")

        adapter.write_input = fake_write_input
        orig_wait_for = self.pty_mod.asyncio.wait_for
        self.pty_mod.asyncio.wait_for = fail_wait_for
        try:
            await adapter.send_text("session-adopted", "hello")
        finally:
            self.pty_mod.asyncio.wait_for = orig_wait_for

        self.assertEqual(
            writes,
            [
                ("session-adopted", "hello"),
                ("session-adopted", "\r"),
            ],
        )

    async def test_codex_idle_screen_backstop_emits_once_without_teardown(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_agent(
            name="Worker",
            group="Torque",
            terminal_backend="pty",
            command="codex",
            directory="/tmp",
        )
        cell.agent_type = "codex"
        cell.session_id = "session-codex"
        cell.status = "running"
        adapter = self.pty_mod.LocalPtyAdapter(state)
        session = SimpleNamespace(
            cell_id=cell.id,
            session_id=cell.session_id,
            buffer="OpenAI Codex\nWorking on your request",
            closed=False,
        )
        adapter._sessions[cell.session_id] = session
        seen: list[tuple[str, dict]] = []

        async def on_detected(done_cell, data):
            seen.append((done_cell.id, dict(data)))

        adapter.on_agent_session_end_detected = on_detected
        ready_screen = "OpenAI Codex\nmodel: gpt-5\ndirectory: /tmp\n›"

        self.assertFalse(await adapter._poll_codex_idle_session_end(
            cell, session, stable_polls=2))
        session.buffer = ready_screen
        self.assertFalse(await adapter._poll_codex_idle_session_end(
            cell, session, stable_polls=2))
        self.assertTrue(await adapter._poll_codex_idle_session_end(
            cell, session, stable_polls=2))
        self.assertFalse(await adapter._poll_codex_idle_session_end(
            cell, session, stable_polls=2))

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], cell.id)
        self.assertEqual(seen[0][1]["reason"], "pty_idle_screen")
        self.assertIn(cell.session_id, adapter._sessions)
        self.assertIs(adapter._sessions[cell.session_id], session)

    async def test_codex_idle_backstop_ignores_stale_ready_in_append_buffer(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_agent(
            name="Worker",
            group="Torque",
            terminal_backend="pty",
            command="codex",
            directory="/tmp",
        )
        cell.agent_type = "codex"
        cell.session_id = "session-codex"
        cell.status = "running"
        adapter = self.pty_mod.LocalPtyAdapter(state)
        session = self.pty_mod._PtySession(
            session_id=cell.session_id,
            cell_id=cell.id,
        )
        adapter._sessions[cell.session_id] = session
        seen: list[tuple[str, dict]] = []

        async def on_detected(done_cell, data):
            seen.append((done_cell.id, dict(data)))

        adapter.on_agent_session_end_detected = on_detected

        stale_ready = "OpenAI Codex\nmodel: gpt-5\ndirectory: /tmp\n›"
        busy_output = "OpenAI Codex\nWorking on your request\nRunning tests"
        final_ready_repaint = (
            "\x1b[2J\x1b[H"
            "OpenAI Codex\nmodel: gpt-5\ndirectory: /tmp\n›"
        )

        adapter._record_terminal_output(session, stale_ready)
        self.assertFalse(await adapter._poll_codex_idle_session_end(
            cell, session, stable_polls=2))

        adapter._mark_codex_turn_submitted(
            cell, session, clear_screen=True)
        adapter._record_terminal_output(session, busy_output)
        # The replay buffer remains append-only and still contains the old
        # ready composer, but readiness must be evaluated from current screen.
        self.assertIn("directory: /tmp", session.buffer)
        current_screen = await adapter._read_screen_text(session)
        self.assertIn("Working on your request", current_screen)
        self.assertNotIn("directory: /tmp", current_screen)
        self.assertFalse(await adapter._poll_codex_idle_session_end(
            cell, session, stable_polls=2))

        adapter._record_terminal_output(session, final_ready_repaint)
        self.assertFalse(await adapter._poll_codex_idle_session_end(
            cell, session, stable_polls=2))
        self.assertTrue(await adapter._poll_codex_idle_session_end(
            cell, session, stable_polls=2))
        self.assertFalse(await adapter._poll_codex_idle_session_end(
            cell, session, stable_polls=2))

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], cell.id)
        self.assertEqual(seen[0][1]["source"], "codex_idle_screen_backstop")
        self.assertIs(adapter._sessions[cell.session_id], session)

    async def test_send_text_codex_applies_post_ready_delay_via_screen_path(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_agent(
            name="Worker",
            group="Torque",
            terminal_backend="pty",
            command="codex",
            directory="/tmp",
        )
        cell.agent_type = "codex"
        cell.session_id = "session-codex"
        adapter = self.pty_mod.LocalPtyAdapter(state)
        adapter._sessions[cell.session_id] = SimpleNamespace(
            cell_id=cell.id,
            session_id=cell.session_id,
        )

        async def fake_read_screen_text(session):
            return "OpenAI Codex\nmodel: gpt-5\ndirectory: /tmp\n›"
        adapter._read_screen_text = fake_read_screen_text

        writes: list[tuple[str, str]] = []

        async def fake_write_input(session_id, data):
            writes.append((session_id, data))

        delays = []

        async def fake_sleep(delay):
            delays.append(delay)

        adapter.write_input = fake_write_input
        orig_sleep = self.pty_mod.asyncio.sleep
        self.pty_mod.asyncio.sleep = fake_sleep
        try:
            await adapter.send_text(cell.session_id, "First prompt")
        finally:
            self.pty_mod.asyncio.sleep = orig_sleep

        self.assertEqual(
            writes,
            [
                ("session-codex", "First prompt"),
                ("session-codex", "\r"),
            ],
        )
        # Codex policy: stable_polls=2, poll_interval=0.25, post_ready_delay=2.5.
        # Both polls return ready (fake_read_screen_text returns ready always),
        # so: 1 poll-interval sleep + 1 post_ready_delay. Single-line prompt
        # skips the multiline submit-key delay.
        self.assertEqual(delays, [0.25, 2.5])

    async def test_create_session_installs_hooks_in_resolved_cwd_when_directory_blank(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        with tempfile.TemporaryDirectory() as tmpdir:
            cell = state.add_agent(
                name="Engineer",
                group="Torque",
                terminal_backend="pty",
                command="",
                directory="",
            )
            cell.agent_type = "claude-code"
            adapter = self.pty_mod.LocalPtyAdapter(state)
            prev_cwd = os.getcwd()

            try:
                os.chdir(tmpdir)
                with mock.patch.dict(os.environ, {"TORQUE_PORT": "18933"}, clear=False):
                    await adapter.start()
                    await adapter.create_session(cell)
                resolved_tmpdir = os.path.realpath(tmpdir)
                settings_file = Path(resolved_tmpdir) / ".claude" / "settings.local.json"
                self.assertEqual(os.path.realpath(cell.directory), resolved_tmpdir)
                self.assertTrue(settings_file.exists())
                settings = json.loads(settings_file.read_text())
                self.assertIs(settings.get("autoMemoryEnabled"), False)
                self.assertIn(
                    "http://localhost:18933/events",
                    settings_file.read_text(),
                )
            finally:
                os.chdir(prev_cwd)
                if cell.session_id:
                    await adapter.close_session(cell.session_id)

    async def test_claude_agent_directory_disables_auto_memory_for_roles(self):
        for kind in ("engineer", "architect"):
            state = self.state_mod.MatrixState()
            state.add_group("Torque")
            with tempfile.TemporaryDirectory() as tmpdir:
                settings_file = (
                    Path(tmpdir) / ".claude" / "settings.local.json"
                )
                settings_file.parent.mkdir(parents=True, exist_ok=True)
                settings_file.write_text(json.dumps({
                    "theme": "dark",
                    "autoMemoryEnabled": True,
                }))
                cell = state.add_agent(
                    name=kind.title(),
                    group="Torque",
                    terminal_backend="pty",
                    command="",
                    directory=tmpdir,
                )
                cell.agent_type = "claude-code"
                cell.kind = kind
                adapter = self.pty_mod.LocalPtyAdapter(state)

                await adapter.start()
                await adapter.create_session(cell)
                try:
                    settings = json.loads(settings_file.read_text())
                    self.assertEqual(settings.get("theme"), "dark")
                    self.assertIs(settings.get("autoMemoryEnabled"), False)
                    self.assertIn("hooks", settings)
                finally:
                    if cell.session_id:
                        await adapter.close_session(cell.session_id)

    def test_session_environment_scrubs_iterm_markers_and_sets_standalone_defaults(self):
        state = self.state_mod.MatrixState()
        adapter = self.pty_mod.LocalPtyAdapter(state)

        with mock.patch.dict(
            os.environ,
            {
                "ITERM_SESSION_ID": "w1t0p0:deadbeef",
                "ITERM_PROFILE": "Default",
                "LC_TERMINAL": "iTerm2",
                "LC_TERMINAL_VERSION": "3.6.7",
                "TERM_SESSION_ID": "session-123",
                "TERM_FEATURES": "24bitcc",
                "TERMINFO_DIRS": "/Applications/iTerm.app/Contents/Resources/terminfo",
                "TERM_PROGRAM": "iTerm.app",
                "TERM_PROGRAM_VERSION": "3.6.7",
                "TERM": "dumb",
                "STARSHIP_SESSION_KEY": "starship-session",
                "STARSHIP_SHELL": "zsh",
            },
            clear=False,
        ):
            env = adapter._session_environment(
                "cell-123",
                {"CUSTOM_PATH": "~/torque-test"},
            )

        self.assertEqual(env["TORQUE_CELL_ID"], "cell-123")
        self.assertEqual(env["TORQUE_STANDALONE_PTY"], "1")
        self.assertEqual(env["TERM"], "xterm-256color")
        self.assertEqual(env["COLORTERM"], "truecolor")
        self.assertEqual(env["CLAUDE_GATEWAY_NO_AUTO_UPDATE"], "true")
        self.assertEqual(env["DISABLE_AUTOUPDATER"], "1")
        self.assertEqual(env["CUSTOM_PATH"], os.path.expanduser("~/torque-test"))
        self.assertNotIn("ITERM_SESSION_ID", env)
        self.assertNotIn("ITERM_PROFILE", env)
        self.assertNotIn("LC_TERMINAL", env)
        self.assertNotIn("LC_TERMINAL_VERSION", env)
        self.assertNotIn("TERM_SESSION_ID", env)
        self.assertNotIn("TERM_FEATURES", env)
        self.assertNotIn("TERMINFO_DIRS", env)
        self.assertNotIn("TERM_PROGRAM", env)
        self.assertNotIn("TERM_PROGRAM_VERSION", env)
        self.assertNotIn("STARSHIP_SESSION_KEY", env)
        self.assertNotIn("STARSHIP_SHELL", env)

    def test_prepare_zsh_bootstrap_wraps_original_profiles_and_installs_precmd_hook(self):
        state = self.state_mod.MatrixState()
        adapter = self.pty_mod.LocalPtyAdapter(state)

        with tempfile.TemporaryDirectory() as zdotdir:
            env = {"ZDOTDIR": zdotdir}
            bootstrap_dir = adapter._prepare_zsh_bootstrap(env)
            self.addCleanup(shutil.rmtree, bootstrap_dir, ignore_errors=True)

            self.assertEqual(env["ZDOTDIR"], bootstrap_dir)
            self.assertEqual(env["TORQUE_ORIGINAL_ZDOTDIR"], zdotdir)

            zshrc = Path(bootstrap_dir) / ".zshrc"
            zshenv = Path(bootstrap_dir) / ".zshenv"
            self.assertTrue(zshrc.exists())
            self.assertTrue(zshenv.exists())
            zshrc_text = zshrc.read_text()
            self.assertIn('source "$ZDOTDIR/.zshrc"', zshrc_text)
            self.assertIn("add-zsh-hook precmd _torque_precmd", zshrc_text)
            self.assertIn("printf '\\033]7;file://%s%s\\007'", zshrc_text)

    def test_prepare_zsh_bootstrap_preserves_original_zdotdir_across_nested_torque_sessions(self):
        state = self.state_mod.MatrixState()
        adapter = self.pty_mod.LocalPtyAdapter(state)

        with tempfile.TemporaryDirectory() as original_zdotdir, \
                tempfile.TemporaryDirectory(prefix="torque-zsh-bootstrap-parent-") as parent_bootstrap:
            env = {
                "ZDOTDIR": parent_bootstrap,
                "TORQUE_ORIGINAL_ZDOTDIR": original_zdotdir,
            }

            bootstrap_dir = adapter._prepare_zsh_bootstrap(env)
            self.addCleanup(shutil.rmtree, bootstrap_dir, ignore_errors=True)

            self.assertEqual(env["ZDOTDIR"], bootstrap_dir)
            self.assertEqual(env["TORQUE_ORIGINAL_ZDOTDIR"], original_zdotdir)
            self.assertNotEqual(env["TORQUE_ORIGINAL_ZDOTDIR"], parent_bootstrap)

    def test_codex_startup_commands_use_torque_owned_config_and_leave_project_codex(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as data_dir:
            project_codex = Path(tmp) / ".codex"
            project_codex.mkdir()
            project_config = project_codex / "config.toml"
            project_hooks = project_codex / "hooks.json"
            project_config.write_text('[profiles.default]\nmodel = "gpt-5"\n')
            project_hooks.write_text('{"hooks": {}}')
            prompt_file = Path(tmp) / ".torque" / "torque-system-prompt-agent-1.md"
            prompt_file.parent.mkdir()
            prompt_file.write_text("Persistent prompt.\n")
            cell = state.add_agent(
                name="Codex",
                group="Torque",
                terminal_backend="pty",
                command="codex --model gpt-5",
                directory=tmp,
            )
            cell.id = "agent-1"
            cell.agent_type = "codex"
            adapter = self.pty_mod.LocalPtyAdapter(state)

            with mock.patch.dict(
                os.environ,
                {"TORQUE_DATA_DIR": data_dir, "TORQUE_PORT": "18933"},
                clear=False,
            ):
                commands = adapter._startup_commands(
                    cell,
                    shell_name="zsh",
                    cwd=tmp,
                    mcp_entrypoint="torque/mcp_engineer.py",
                )

            self.assertEqual(len(commands), 1)
            boot_cmd = commands[0]
            launch_script = Path(data_dir) / "codex" / "agents" / "agent-1" / "launch.sh"
            self.assertEqual(boot_cmd, shlex.quote(str(launch_script)))
            self.assertLess(len(boot_cmd), 256)
            launch_text = launch_script.read_text()
            self.assertIn("exec codex --model gpt-5", launch_text)
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", launch_text)
            self.assertIn("--config", launch_text)
            self.assertIn("mcp_servers.torque.url", launch_text)
            self.assertIn("hooks.SessionStart", launch_text)
            command_parts = shlex.split(launch_text.splitlines()[-1].removeprefix("exec "))
            config_values = [
                value
                for index, value in enumerate(command_parts)
                if index > 0 and command_parts[index - 1] == "--config"
            ]
            state_flags = [
                value for value in config_values if value.startswith("hooks.state=")
            ]
            self.assertEqual(len(state_flags), 1)
            self.assertIn(json.dumps("/config.toml:session_start:0:0"), state_flags[0])
            self.assertIn(json.dumps("/config.toml:pre_tool_use:0:0"), state_flags[0])
            self.assertIn(
                json.dumps("/config.toml:permission_request:0:0"),
                state_flags[0],
            )
            self.assertIn(json.dumps("/config.toml:stop:0:0"), state_flags[0])
            self.assertIn("trusted_hash = \"sha256:", state_flags[0])
            self.assertNotIn("--dangerously-bypass-hook-trust", launch_text)
            generated = Path(data_dir) / "codex" / "agents" / "agent-1" / "config.toml"
            generated_text = generated.read_text()
            self.assertIn("[mcp_servers.torque]", generated_text)
            self.assertIn("[[hooks.SessionStart]]", generated_text)
            self.assertIn("model_instructions_file", generated_text)
            self.assertIn("env_http_headers", generated_text)
            self.assertEqual(project_config.read_text(), '[profiles.default]\nmodel = "gpt-5"\n')
            self.assertEqual(project_hooks.read_text(), '{"hooks": {}}')


    async def test_codex_zsh_startup_uses_short_launch_shim_that_executes(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        with tempfile.TemporaryDirectory() as tmp, \
                tempfile.TemporaryDirectory() as bin_dir, \
                tempfile.TemporaryDirectory() as data_dir:
            marker = Path(tmp) / "codex-ran.txt"
            fake_codex = Path(bin_dir) / "codex"
            fake_codex.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$@\" > {shlex.quote(str(marker))}\n"
                "sleep 5\n"
            )
            fake_codex.chmod(0o755)
            cell = state.add_agent(
                name="Codex",
                group="Torque",
                terminal_backend="pty",
                command=str(fake_codex),
                directory=tmp,
            )
            cell.id = "agent-1"
            cell.agent_type = "codex"
            adapter = self.pty_mod.LocalPtyAdapter(state)

            with mock.patch.dict(
                os.environ,
                {"TORQUE_DATA_DIR": data_dir, "TORQUE_PORT": "18933"},
                clear=False,
            ):
                await adapter.start()
                await adapter.create_session(cell, shell="/bin/zsh")

            try:
                for _ in range(25):
                    if marker.exists():
                        break
                    await asyncio.sleep(0.1)

                self.assertTrue(marker.exists())
                argv = marker.read_text()
                self.assertIn("--dangerously-bypass-approvals-and-sandbox", argv)
                self.assertIn("--config", argv)
                self.assertIn("mcp_servers.torque.url", argv)
                self.assertIn("hooks.SessionStart", argv)
                launch_script = (
                    Path(data_dir) / "codex" / "agents" / "agent-1" / "launch.sh"
                )
                self.assertTrue(launch_script.exists())
                self.assertLess(len(shlex.quote(str(launch_script))), 256)
            finally:
                if cell.session_id:
                    await adapter.close_session(cell.session_id)


    async def test_codex_zsh_resume_uses_short_launch_shim_that_executes(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        with tempfile.TemporaryDirectory() as tmp, \
                tempfile.TemporaryDirectory() as bin_dir, \
                tempfile.TemporaryDirectory() as data_dir:
            marker = Path(tmp) / "codex-resume-ran.txt"
            fake_codex = Path(bin_dir) / "codex"
            fake_codex.write_text(
                "#!/bin/sh\n"
                f"printf '%s\n' \"$@\" > {shlex.quote(str(marker))}\n"
                "sleep 5\n"
            )
            fake_codex.chmod(0o755)
            cell = state.add_agent(
                name="Codex",
                group="Torque",
                terminal_backend="pty",
                command=f"{shlex.quote(str(fake_codex))} --model gpt-5 'Resume prompt.'",
                directory=tmp,
            )
            cell.id = "agent-1"
            cell.agent_type = "codex"
            cell.agent_session_id = "session-123"
            cell.session_resume = True
            adapter = self.pty_mod.LocalPtyAdapter(state)

            with mock.patch.dict(
                os.environ,
                {"TORQUE_DATA_DIR": data_dir, "TORQUE_PORT": "18933"},
                clear=False,
            ):
                await adapter.start()
                await adapter.create_session(cell, shell="/bin/zsh")

            try:
                for _ in range(25):
                    if marker.exists():
                        break
                    await asyncio.sleep(0.1)

                self.assertTrue(marker.exists())
                argv = marker.read_text().splitlines()
                self.assertIn("resume", argv)
                self.assertIn("session-123", argv)
                self.assertIn("--model", argv)
                self.assertIn("gpt-5", argv)
                self.assertIn("--dangerously-bypass-approvals-and-sandbox", argv)
                self.assertIn("--config", argv)
                self.assertIn("Resume prompt.", argv)
                self.assertTrue(
                    any(value.startswith("mcp_servers.torque.url=") for value in argv)
                )
                self.assertTrue(any(value.startswith("hooks.SessionStart=") for value in argv))
                launch_script = (
                    Path(data_dir) / "codex" / "agents" / "agent-1" / "launch.sh"
                )
                self.assertTrue(launch_script.exists())
                launch_text = launch_script.read_text()
                self.assertIn('if [ "${1:-}" = "resume" ]; then', launch_text)
                self.assertIn('"$@"', launch_text)
                self.assertIn("--dangerously-bypass-approvals-and-sandbox", launch_text)
                self.assertLess(len(shlex.quote(str(launch_script))), 256)
            finally:
                if cell.session_id:
                    await adapter.close_session(cell.session_id)

    def test_claude_startup_commands_still_use_project_config(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        with tempfile.TemporaryDirectory() as tmp:
            cell = state.add_agent(
                name="Claude",
                group="Torque",
                terminal_backend="pty",
                command="claude",
                directory=tmp,
            )
            cell.agent_type = "claude-code"
            adapter = self.pty_mod.LocalPtyAdapter(state)

            commands = adapter._startup_commands(cell, shell_name="zsh", cwd=tmp)

            self.assertEqual(commands, ["claude"])
            self.assertTrue((Path(tmp) / ".claude" / "settings.local.json").exists())
            self.assertTrue((Path(tmp) / ".mcp.json").exists())

    def test_startup_commands_skip_typed_bootstrap_for_zsh_sessions(self):
        state = self.state_mod.MatrixState()
        state.add_group("Torque")
        cell = state.add_terminal(
            name="Terminal 1",
            group="Torque",
            terminal_backend="pty",
            directory="/tmp",
        )
        adapter = self.pty_mod.LocalPtyAdapter(state)

        zsh_commands = adapter._startup_commands(cell, shell_name="zsh", cwd="/tmp")
        bash_commands = adapter._startup_commands(cell, shell_name="bash", cwd="/tmp")

        self.assertEqual(zsh_commands, [])
        self.assertTrue(any("PROMPT_COMMAND=" in command for command in bash_commands))
        self.assertTrue(any("printf '\\033]7;file://" in command for command in bash_commands))

    async def test_read_screen_text_strips_ansi_sequences_for_readiness_checks(self):
        state = self.state_mod.MatrixState()
        adapter = self.pty_mod.LocalPtyAdapter(state)
        session = SimpleNamespace(
            buffer=(
                "\x1b[2m╭────────────────╮\x1b[0m\r\n"
                "\x1b[1mOpenAI Codex\x1b[0m\r\n"
                "\x1b[2mmodel:\x1b[0m gpt-5.4 high\r\n"
                "\x1b[2mdirectory:\x1b[0m ~/repo\r\n"
                "\x1b[1m›\x1b[0m Ready\r\n"
                "\x1b]0;title\x07"
            )
        )

        screen_text = await adapter._read_screen_text(session)

        self.assertIn("OpenAI Codex", screen_text)
        self.assertIn("model: gpt-5.4 high", screen_text)
        self.assertIn("directory: ~/repo", screen_text)
        self.assertIn("› Ready", screen_text)
        self.assertNotIn("\x1b", screen_text)


if __name__ == "__main__":
    unittest.main()
