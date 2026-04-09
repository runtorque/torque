import asyncio
import importlib
import os
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
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.pty_mod = importlib.import_module("loom.local_pty")
        self.pty_mod = importlib.reload(self.pty_mod)

    async def test_create_session_emits_output_and_tracks_focus(self):
        state = self.state_mod.MatrixState()
        state.add_group("Loom")
        cell = state.add_terminal(
            name="Terminal 1",
            group="Loom",
            terminal_backend="pty",
            command="printf 'hello-loom\\n'",
        )
        adapter = self.pty_mod.LocalPtyAdapter(state)
        seen = asyncio.Future()

        async def on_output(cell_id, session_id, text):
            if (
                cell_id == cell.id
                and "hello-loom" in text
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

    async def test_write_input_accepts_raw_terminal_bytes(self):
        state = self.state_mod.MatrixState()
        state.add_group("Loom")
        with tempfile.TemporaryDirectory() as tmpdir:
            cell = state.add_terminal(
                name="Terminal 1",
                group="Loom",
                terminal_backend="pty",
                directory=tmpdir,
            )
            adapter = self.pty_mod.LocalPtyAdapter(state)
            seen = asyncio.Future()

            async def on_output(cell_id, session_id, text):
                if (
                    cell_id == cell.id
                    and "raw-loom" in text
                    and not seen.done()
                ):
                    seen.set_result((cell_id, session_id, text))

            adapter.on_terminal_output = on_output
            await adapter.start()
            await adapter.create_session(cell)
            self.assertIsNotNone(cell.session_id)

            await adapter.write_input(
                cell.session_id,
                "printf 'raw-loom\\n'\r",
            )
            result = await asyncio.wait_for(seen, timeout=4)

            self.assertEqual(result[0], cell.id)
            self.assertIn("raw-loom", result[2])

            await adapter.close_session(cell.session_id)

    async def test_shutdown_closes_live_sessions(self):
        state = self.state_mod.MatrixState()
        state.add_group("Loom")
        cell = state.add_terminal(
            name="Terminal 1",
            group="Loom",
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

    async def test_send_text_waits_for_input_ready_signal_for_hook_based_agent(self):
        state = self.state_mod.MatrixState()
        state.add_group("Loom")
        cell = state.add_agent(
            name="Weaver",
            group="Loom",
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
        await asyncio.wait_for(send_task, timeout=1)

        self.assertEqual(
            writes,
            [
                ("session-1", "Initial prompt"),
                ("session-1", "\r"),
            ],
        )

    async def test_create_session_installs_hooks_in_resolved_cwd_when_directory_blank(self):
        state = self.state_mod.MatrixState()
        state.add_group("Loom")
        with tempfile.TemporaryDirectory() as tmpdir:
            cell = state.add_agent(
                name="Weaver",
                group="Loom",
                terminal_backend="pty",
                command="",
                directory="",
            )
            cell.agent_type = "claude-code"
            adapter = self.pty_mod.LocalPtyAdapter(state)
            prev_cwd = os.getcwd()

            try:
                os.chdir(tmpdir)
                with mock.patch.dict(os.environ, {"LOOM_PORT": "18933"}, clear=False):
                    await adapter.start()
                    await adapter.create_session(cell)
                resolved_tmpdir = os.path.realpath(tmpdir)
                settings_file = Path(resolved_tmpdir) / ".claude" / "settings.local.json"
                self.assertEqual(os.path.realpath(cell.directory), resolved_tmpdir)
                self.assertTrue(settings_file.exists())
                self.assertIn("http://localhost:18933/events", settings_file.read_text())
            finally:
                os.chdir(prev_cwd)
                if cell.session_id:
                    await adapter.close_session(cell.session_id)


if __name__ == "__main__":
    unittest.main()
