import asyncio
import importlib
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
