"""Coalescing terminal-output fan-out (server_routes broadcaster factory)."""

import asyncio
import importlib
import json
import unittest

from tests.helpers import install_aiohttp_stub


class FakeWS:
    def __init__(self, fail: bool = False):
        self.frames = []
        self.fail = fail

    async def send_str(self, msg):
        if self.fail:
            raise ConnectionError("gone")
        self.frames.append(json.loads(msg))


class TerminalOutputBroadcastTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.server_mod = importlib.import_module("torque.server_routes")

    async def _drain(self):
        # Two flush windows plus slack: enough for the trailing timer to
        # fire and the flusher task to finish.
        await asyncio.sleep(
            self.server_mod._TERMINAL_OUTPUT_FLUSH_SECONDS * 3)

    async def test_chunks_coalesce_into_one_ordered_frame(self):
        ws = FakeWS()
        terminal_clients = {"cell-1": {ws}}
        broadcast = self.server_mod.make_terminal_output_broadcaster(
            terminal_clients)

        await broadcast("cell-1", "sess-1", "hello ")
        await broadcast("cell-1", "sess-1", "wor")
        await broadcast("cell-1", "sess-1", "ld")
        await self._drain()

        self.assertEqual(len(ws.frames), 1)
        frame = ws.frames[0]
        self.assertEqual(frame["type"], "output")
        self.assertEqual(frame["cell_id"], "cell-1")
        self.assertEqual(frame["session_id"], "sess-1")
        self.assertEqual(frame["data"], "hello world")

    async def test_sessions_flush_separately_and_empty_text_is_ignored(self):
        ws = FakeWS()
        terminal_clients = {"cell-1": {ws}}
        broadcast = self.server_mod.make_terminal_output_broadcaster(
            terminal_clients)

        await broadcast("cell-1", "sess-1", "a")
        await broadcast("cell-1", "sess-2", "b")
        await broadcast("cell-1", "sess-1", "")
        await self._drain()

        self.assertEqual(len(ws.frames), 2)
        by_session = {frame["session_id"]: frame["data"]
                      for frame in ws.frames}
        self.assertEqual(by_session, {"sess-1": "a", "sess-2": "b"})

    async def test_dead_subscriber_is_pruned_without_stalling_others(self):
        live = FakeWS()
        dead = FakeWS(fail=True)
        terminal_clients = {"cell-1": {live, dead}}
        broadcast = self.server_mod.make_terminal_output_broadcaster(
            terminal_clients)

        await broadcast("cell-1", "sess-1", "data")
        await self._drain()

        self.assertEqual(len(live.frames), 1)
        self.assertEqual(terminal_clients["cell-1"], {live})

    async def test_output_arriving_mid_flush_lands_in_next_frame(self):
        ws = FakeWS()
        terminal_clients = {"cell-1": {ws}}
        broadcast = self.server_mod.make_terminal_output_broadcaster(
            terminal_clients)

        await broadcast("cell-1", "sess-1", "first")
        # Land a second chunk while the first flush window is still open
        # for another session's cell; then let both windows elapse.
        await asyncio.sleep(
            self.server_mod._TERMINAL_OUTPUT_FLUSH_SECONDS / 2)
        await broadcast("cell-1", "sess-1", " second")
        await self._drain()

        joined = "".join(frame["data"] for frame in ws.frames)
        self.assertEqual(joined, "first second")

    async def test_subscriberless_output_is_dropped_quietly(self):
        terminal_clients = {}
        broadcast = self.server_mod.make_terminal_output_broadcaster(
            terminal_clients)
        await broadcast("cell-x", "sess-1", "data")
        await self._drain()


if __name__ == "__main__":
    unittest.main()
