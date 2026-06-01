"""Tests for ``SupervisedPtyAdapter``: the daemon-side adapter that
delegates PTY ownership to an in-process supervisor.

These are integration-level tests that stand up a real ``PtySupervisor``
on a temp unix socket and run the adapter against it. Exercises the
same happy-path surface as ``test_local_pty_adapter`` plus the
reconcile-after-reconnect scenario.
"""

import asyncio
import importlib
import tempfile
import time
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

from torque.pty_supervisor import PtySupervisor


class SupervisedPtyAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.pty_mod = importlib.import_module("torque.local_pty")
        self.pty_mod = importlib.reload(self.pty_mod)

        self.tmp = tempfile.TemporaryDirectory()
        self.sock_path = Path(self.tmp.name) / "sup.sock"
        self.supervisor = PtySupervisor()
        self.server = await asyncio.start_unix_server(
            self.supervisor.handle_client, path=str(self.sock_path))

    async def asyncTearDown(self):
        await self.supervisor.shutdown()
        self.server.close()
        await self.server.wait_closed()
        self.tmp.cleanup()

    async def _make_adapter(self):
        state = self.state_mod.MatrixState()
        adapter = self.pty_mod.SupervisedPtyAdapter(state, self.sock_path)
        await adapter.start()
        return state, adapter

    async def test_list_supervisor_sessions_delegates_to_client(self):
        state = self.state_mod.MatrixState()
        adapter = self.pty_mod.SupervisedPtyAdapter(state, self.sock_path)
        calls = []

        class FakeClient:
            async def list_sessions(self):
                calls.append("list")
                return [{"session_id": "sess-delegated"}]

            async def list_state(self):
                calls.append("state")
                return {
                    "supervisor": {"pid": 999, "started_at": 10},
                    "sessions": [{"session_id": "sess-delegated"}],
                }

        adapter._client = FakeClient()

        self.assertEqual(
            await adapter.list_supervisor_sessions(),
            [{"session_id": "sess-delegated"}],
        )
        self.assertEqual(
            await adapter.list_supervisor_state(),
            {
                "supervisor": {"pid": 999, "started_at": 10},
                "sessions": [{"session_id": "sess-delegated"}],
            },
        )
        self.assertEqual(calls, ["list", "state"])

    async def test_terminate_supervisor_session_delegates_untracked_session(self):
        state = self.state_mod.MatrixState()
        adapter = self.pty_mod.SupervisedPtyAdapter(state, self.sock_path)
        calls = []

        class FakeClient:
            async def close_session(self, session_id):
                calls.append(session_id)
                return {"type": "ok", "op": "close"}

        adapter._client = FakeClient()

        await adapter.terminate_supervisor_session("orphan-session")

        self.assertEqual(calls, ["orphan-session"])

    async def test_create_session_spawns_via_supervisor_and_emits_output(self):
        state, adapter = await self._make_adapter()
        try:
            state.add_group("Torque")
            cell = state.add_terminal(
                name="Term 1",
                group="Torque",
                terminal_backend="pty",
                command="printf 'hello-sup-ad\\n'",
            )
            seen = asyncio.Future()

            async def on_output(cell_id, session_id, text):
                if cell_id == cell.id and "hello-sup-ad" in text:
                    if not seen.done():
                        seen.set_result(True)

            adapter.on_terminal_output = on_output
            await adapter.create_session(cell)
            await asyncio.wait_for(seen, timeout=4.0)
            self.assertIsNotNone(cell.session_id)
            self.assertEqual(state.active_session_id, cell.session_id)
            self.assertEqual(state.current_window_id, "standalone")
        finally:
            await adapter.shutdown()

    async def test_write_input_round_trips_through_cat(self):
        state, adapter = await self._make_adapter()
        try:
            state.add_group("Torque")
            cell = state.add_terminal(
                name="Term 2",
                group="Torque",
                terminal_backend="pty",
                command="cat",
            )
            got = asyncio.Event()

            async def on_output(cell_id, session_id, text):
                if cell_id == cell.id and "raw-sup" in text:
                    got.set()

            adapter.on_terminal_output = on_output
            await adapter.create_session(cell)
            # Write after session is alive and subscribed.
            await asyncio.sleep(0.1)
            await adapter.write_input(cell.session_id, "raw-sup\r")
            await asyncio.wait_for(got.wait(), timeout=3.0)
        finally:
            await adapter.shutdown()

    async def test_write_breaker_opens_short_circuits_and_recovers(self):
        import types
        state = self.state_mod.MatrixState()
        adapter = self.pty_mod.SupervisedPtyAdapter(state, self.sock_path)
        state.add_group("Torque")
        cell = state.add_terminal(
            name="Term BK",
            group="Torque",
            terminal_backend="pty",
            command="cat",
        )
        sid = "sess-breaker"
        cell.session_id = sid
        adapter._sessions[sid] = types.SimpleNamespace(
            closed=False, cell_id=cell.id)

        attempts = {"n": 0}

        class FailingClient:
            async def write_input(self, session_id, payload):
                attempts["n"] += 1
                raise OSError("boom")

        adapter._client = FailingClient()

        # Consecutive failures open the breaker at the threshold.
        for _ in range(adapter.WRITE_BREAKER_THRESHOLD):
            await adapter.write_input(sid, "x")
        self.assertEqual(attempts["n"], adapter.WRITE_BREAKER_THRESHOLD)
        self.assertIn(sid, adapter.supervisor_write_breaker_snapshot())
        self.assertTrue(cell.needs_attention)

        # While open, further writes short-circuit (no new round-trips).
        for _ in range(5):
            await adapter.write_input(sid, "x")
        self.assertEqual(attempts["n"], adapter.WRITE_BREAKER_THRESHOLD)

        # After the cooldown, a single probe write is allowed through.
        adapter._write_breaker[sid]["opened_at"] = (
            time.monotonic() - adapter.WRITE_BREAKER_COOLDOWN_SECONDS - 1
        )
        await adapter.write_input(sid, "x")
        self.assertEqual(attempts["n"], adapter.WRITE_BREAKER_THRESHOLD + 1)

    async def test_close_session_marks_cell_stopped(self):
        state, adapter = await self._make_adapter()
        try:
            state.add_group("Torque")
            cell = state.add_terminal(
                name="Term 3",
                group="Torque",
                terminal_backend="pty",
                command="sleep 30",
            )
            await adapter.create_session(cell)
            session_id = cell.session_id
            self.assertIsNotNone(session_id)

            await adapter.close_session(session_id)
            # Give the exit frame time to propagate.
            deadline = asyncio.get_running_loop().time() + 3.0
            while (asyncio.get_running_loop().time() < deadline
                   and cell.session_id is not None):
                await asyncio.sleep(0.05)
            self.assertIsNone(cell.session_id)
            self.assertEqual(cell.status, "stopped")
        finally:
            await adapter.shutdown()

    async def test_resize_propagates_to_supervisor(self):
        state, adapter = await self._make_adapter()
        try:
            state.add_group("Torque")
            cell = state.add_terminal(
                name="Term 4",
                group="Torque",
                terminal_backend="pty",
                command="sleep 30",
            )
            await adapter.create_session(cell)
            await adapter.resize_session(cell.session_id, 100, 40)
            sup_session = self.supervisor.sessions[cell.session_id]
            self.assertEqual(sup_session.cols, 100)
            self.assertEqual(sup_session.rows, 40)
        finally:
            await adapter.shutdown()

    async def test_reconnect_orphans_reclaims_surviving_sessions(self):
        """Simulate daemon restart: adapter A creates a session, shuts
        down (closes client only; supervisor keeps the session). A
        fresh adapter B starts, runs ``reconnect_orphans``, and should
        re-adopt the session + resume output.
        """
        state_a, adapter_a = await self._make_adapter()
        state_a.add_group("Torque")
        cell_a = state_a.add_terminal(
            name="Survivor",
            group="Torque",
            terminal_backend="pty",
            command="i=0; while [ $i -lt 50 ]; do "
                    "printf 'tick-%d\\n' $i; i=$((i+1)); sleep 0.1; done",
        )
        await adapter_a.create_session(cell_a)
        session_id = cell_a.session_id
        self.assertIsNotNone(session_id)
        # Read a bit of output to ensure the session is live.
        await asyncio.sleep(0.4)
        await adapter_a.shutdown()

        # Supervisor still has the session.
        self.assertIn(session_id, self.supervisor.sessions)

        # New "daemon image" starts. Persist the cell into a fresh
        # state that has the same id/session_id.
        state_b = self.state_mod.MatrixState()
        state_b.add_group("Torque")
        cell_b = state_b.add_terminal(
            name="Survivor",
            group="Torque",
            terminal_backend="pty",
        )
        # Align cell ids + session_id as if loaded from SQLite.
        state_b.agents[cell_a.id] = cell_b
        if cell_b.id != cell_a.id:
            del state_b.agents[cell_b.id]
            cell_b.id = cell_a.id
            state_b.agents[cell_b.id] = cell_b
        cell_b.session_id = session_id
        cell_b.status = "running"

        adapter_b = self.pty_mod.SupervisedPtyAdapter(
            state_b, self.sock_path)

        got_after = asyncio.Event()

        async def on_output(cell_id, sid, text):
            if sid == session_id and "tick-" in text:
                got_after.set()

        adapter_b.on_terminal_output = on_output
        await adapter_b.start()
        try:
            await adapter_b.reconnect_orphans()
            # Cell should still reference the same session.
            self.assertEqual(cell_b.session_id, session_id)
            self.assertNotEqual(cell_b.status, "stopped")
            # Output should resume through adapter B.
            await asyncio.wait_for(got_after.wait(), timeout=3.0)
        finally:
            await adapter_b.shutdown()

    async def test_adopt_supervisor_session_reinstalls_mcp_and_hooks(self):
        """When the daemon restarts and adopts a live supervisor session for an
        awareness agent, it must re-install hooks, MCP config, and skills in the
        agent's working directory so files removed between restarts are
        recreated.
        """
        state, adapter = await self._make_adapter()
        try:
            state.add_group("Torque")
            cell = state.add_agent(
                name="Awareness",
                group="Torque",
                terminal_backend="pty",
                command="sleep 3",
                directory=self.tmp.name,
            )
            cell.agent_type = "claude-code"
            await adapter.create_session(cell)
            # Simulate `.mcp.json` being removed between restarts.
            mcp_file = Path(self.tmp.name) / ".mcp.json"
            if mcp_file.exists():
                mcp_file.unlink()
            # Adopt the existing session into a fresh state as if the
            # daemon was restarted and loaded the cell from SQLite.
            info = {
                "session_id": cell.session_id,
                "cols": 120,
                "rows": 32,
                "bootstrap_dir": "",
                "pid": 0,
                "alive": True,
            }
            await adapter._adopt_supervisor_session(cell, info)
            self.assertTrue(
                mcp_file.exists(),
                "adopted awareness agent should have .mcp.json re-installed",
            )
        finally:
            await adapter.shutdown()

    async def test_reconnect_orphans_clears_cells_without_live_sessions(self):
        state, adapter = await self._make_adapter()
        try:
            state.add_group("Torque")
            cell = state.add_terminal(
                name="Ghost",
                group="Torque",
                terminal_backend="pty",
            )
            # Cell has a stale session_id but no supervisor session.
            cell.session_id = "never-existed"
            cell.status = "running"
            await adapter.reconnect_orphans()
            self.assertIsNone(cell.session_id)
            self.assertEqual(cell.status, "stopped")
        finally:
            await adapter.shutdown()

    async def test_fresh_reconnect_event_finalizes_tracked_sessions(self):
        """When ``_on_client_reconnect`` reports ``fresh=True`` (the
        supervisor crashed and respawned), the adapter should fire a
        supervisor event and finalize every tracked session. Exercises
        the logic directly — the full client reconnect path is already
        covered by ``test_pty_supervisor_client``.
        """
        state, adapter = await self._make_adapter()
        kinds: list[str] = []

        async def on_event(kind, detail):
            kinds.append(kind)

        adapter.on_supervisor_event = on_event
        try:
            state.add_group("Torque")
            cell = state.add_terminal(
                name="Evt",
                group="Torque",
                terminal_backend="pty",
                command="sleep 30",
            )
            await adapter.create_session(cell)
            self.assertIsNotNone(cell.session_id)

            await adapter._on_client_reconnect({
                "previous_pid": 1,
                "supervisor_pid": 2,
                "fresh": True,
            })

            self.assertIn("fresh_instance", kinds)
            self.assertIsNone(cell.session_id)
            self.assertEqual(cell.status, "stopped")
            self.assertNotIn(cell.id, [
                s.cell_id for s in adapter._sessions.values()
            ])
        finally:
            await adapter.shutdown()

    async def test_nonfresh_reconnect_event_keeps_sessions(self):
        """A reconnect to the same supervisor instance should NOT
        finalize sessions — only the ``reconnected`` event fires.
        """
        state, adapter = await self._make_adapter()
        kinds: list[str] = []

        async def on_event(kind, detail):
            kinds.append(kind)

        adapter.on_supervisor_event = on_event
        try:
            state.add_group("Torque")
            cell = state.add_terminal(
                name="Ev2",
                group="Torque",
                terminal_backend="pty",
                command="sleep 30",
            )
            await adapter.create_session(cell)
            session_id_before = cell.session_id

            await adapter._on_client_reconnect({
                "previous_pid": 99,
                "supervisor_pid": 99,
                "fresh": False,
            })

            self.assertIn("reconnected", kinds)
            self.assertNotIn("fresh_instance", kinds)
            self.assertEqual(cell.session_id, session_id_before)
            self.assertNotEqual(cell.status, "stopped")
        finally:
            await adapter.shutdown()

    async def test_restart_epoch_event_keeps_sessions_and_emits_restarted(self):
        state, adapter = await self._make_adapter()
        kinds: list[str] = []

        async def on_event(kind, detail):
            kinds.append(kind)

        adapter.on_supervisor_event = on_event
        try:
            state.add_group("Torque")
            cell = state.add_terminal(
                name="Restarted",
                group="Torque",
                terminal_backend="pty",
                command="sleep 30",
            )
            await adapter.create_session(cell)
            session_id_before = cell.session_id

            await adapter._on_client_reconnect({
                "previous_pid": 99,
                "supervisor_pid": 99,
                "fresh": False,
                "restarted": True,
                "restart_epoch": 2,
                "restart_report": {
                    "adopted_sessions": 1,
                    "lost_sessions": 0,
                },
            })

            self.assertIn("restarted", kinds)
            self.assertEqual(cell.session_id, session_id_before)
            self.assertNotEqual(cell.status, "stopped")
        finally:
            await adapter.shutdown()

    async def test_restart_worker_loss_finalizes_only_reported_sessions(self):
        state = self.state_mod.MatrixState()
        adapter = self.pty_mod.SupervisedPtyAdapter(state, self.sock_path)
        state.add_group("Torque")
        cell_lost = state.add_terminal(
            name="Lost restart",
            group="Torque",
            terminal_backend="pty",
            command="sleep 30",
        )
        cell_kept = state.add_terminal(
            name="Kept restart",
            group="Torque",
            terminal_backend="pty",
            command="sleep 30",
        )
        lost_sid = "lost-restart-session"
        kept_sid = "kept-restart-session"
        cell_lost.session_id = lost_sid
        cell_lost.status = "running"
        cell_kept.session_id = kept_sid
        cell_kept.status = "running"
        adapter._sessions[lost_sid] = self.pty_mod._PtySession(
            session_id=lost_sid,
            cell_id=cell_lost.id,
            process=None,
            master_fd=-1,
            shell_path="",
        )
        adapter._sessions[kept_sid] = self.pty_mod._PtySession(
            session_id=kept_sid,
            cell_id=cell_kept.id,
            process=None,
            master_fd=-1,
            shell_path="",
        )
        kinds: list[tuple[str, dict]] = []

        async def on_event(kind, detail):
            kinds.append((kind, detail))

        adapter.on_supervisor_event = on_event

        await adapter._on_client_reconnect({
            "previous_pid": 99,
            "supervisor_pid": 99,
            "fresh": False,
            "restarted": True,
            "worker_loss": True,
            "lost_session_ids": [lost_sid],
            "restart_report": {
                "lost_sessions": 1,
                "lost_session_ids": [lost_sid],
                "clean_fallback": False,
            },
        })

        self.assertIsNone(cell_lost.session_id)
        self.assertEqual(cell_lost.status, "stopped")
        self.assertEqual(cell_kept.session_id, kept_sid)
        self.assertNotEqual(cell_kept.status, "stopped")
        self.assertIn(kept_sid, adapter._sessions)
        self.assertEqual(kinds[0][0], "restart_worker_loss")
        self.assertEqual(kinds[0][1]["lost_sessions"], 1)

    async def test_restart_timeout_marks_lost_and_invokes_clean_fallback(self):
        state = self.state_mod.MatrixState()
        adapter = self.pty_mod.SupervisedPtyAdapter(state, self.sock_path)
        state.add_group("Torque")
        cell = state.add_terminal(
            name="Timeout lost",
            group="Torque",
            terminal_backend="pty",
            command="sleep 30",
        )
        sid = "timeout-lost-session"
        cell.session_id = sid
        cell.status = "running"
        adapter._sessions[sid] = self.pty_mod._PtySession(
            session_id=sid,
            cell_id=cell.id,
            process=None,
            master_fd=-1,
            shell_path="",
        )

        class HangingRestartClient:
            async def restart_supervisor(self):
                return {
                    "type": "ok",
                    "op": "restart",
                    "restart_epoch": 5,
                    "restart_nonce": "hang",
                }

            async def wait_for_restart(self, **_kwargs):
                raise RuntimeError("restart timed out")

        adapter._client = HangingRestartClient()
        events: list[tuple[str, dict]] = []
        fallbacks = []

        async def on_event(kind, detail):
            events.append((kind, dict(detail or {})))

        def ensure_running(data_dir):
            fallbacks.append(data_dir)

        adapter.on_supervisor_event = on_event

        with self.assertRaises(RuntimeError):
            await adapter.restart_supervisor(
                timeout=0.1,
                data_dir="/tmp/supervisor-timeout",
                ensure_running=ensure_running,
            )

        self.assertIsNone(cell.session_id)
        self.assertEqual(cell.status, "stopped")
        self.assertEqual(fallbacks, ["/tmp/supervisor-timeout"])
        self.assertIn("restart_requested", [kind for kind, _ in events])
        self.assertIn("restart_failed_lost", [kind for kind, _ in events])
        failed = next(detail for kind, detail in events
                      if kind == "restart_failed_lost")
        self.assertEqual(failed["lost_sessions"], 1)

    async def test_mark_supervisor_lost_emits_explicit_exit_reason(self):
        state = self.state_mod.MatrixState()
        adapter = self.pty_mod.SupervisedPtyAdapter(state, self.sock_path)
        state.add_group("Torque")
        cell = state.add_terminal(
            name="Lost",
            group="Torque",
            terminal_backend="pty",
            command="sleep 30",
        )
        session_id = "lost-session"
        cell.session_id = session_id
        cell.status = "running"
        adapter._sessions[session_id] = self.pty_mod._PtySession(
            session_id=session_id,
            cell_id=cell.id,
            process=None,
            master_fd=-1,
            shell_path="",
        )
        output = []

        async def on_output(_cell_id, _session_id, text):
            output.append(text)

        adapter.on_terminal_output = on_output

        lost = await adapter.mark_supervisor_lost(reason="supervisor_lost")

        self.assertEqual(lost, 1)
        self.assertIsNone(cell.session_id)
        self.assertEqual(cell.status, "stopped")
        self.assertIn("supervisor_lost", "".join(output))
        self.assertEqual(cell.last_event_text, "supervisor_lost")


if __name__ == "__main__":
    unittest.main()
