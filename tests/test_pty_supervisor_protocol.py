import asyncio
import json
import struct
import unittest

from torque import pty_supervisor


class _FakeWriter:
    """Minimal asyncio.StreamWriter-ish stub for framing tests."""

    def __init__(self):
        self.chunks: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.chunks.append(bytes(data))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _reader_from(payload: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    return reader


class FramingTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_trip_write_then_read(self):
        writer = _FakeWriter()
        await pty_supervisor.write_frame(writer, {"op": "ping"})
        self.assertEqual(len(writer.chunks), 1)
        wire = writer.chunks[0]
        self.assertEqual(struct.unpack(">I", wire[:4])[0], len(wire) - 4)
        reader = _reader_from(wire)
        msg = await pty_supervisor.read_frame(reader)
        self.assertEqual(msg, {"op": "ping"})

    async def test_read_eof_returns_none(self):
        reader = _reader_from(b"")
        msg = await pty_supervisor.read_frame(reader)
        self.assertIsNone(msg)

    async def test_read_truncated_body_returns_none(self):
        # advertise 100 bytes, deliver 10
        wire = struct.pack(">I", 100) + b"{}partial"
        reader = _reader_from(wire)
        msg = await pty_supervisor.read_frame(reader)
        self.assertIsNone(msg)

    async def test_read_zero_length_frame_is_empty_dict(self):
        wire = struct.pack(">I", 0)
        reader = _reader_from(wire)
        msg = await pty_supervisor.read_frame(reader)
        self.assertEqual(msg, {})

    async def test_read_oversized_frame_raises(self):
        wire = struct.pack(">I", pty_supervisor.MAX_FRAME_BYTES + 1)
        reader = _reader_from(wire)
        with self.assertRaises(ValueError):
            await pty_supervisor.read_frame(reader)

    async def test_write_rejects_oversized_body(self):
        writer = _FakeWriter()
        payload = {"blob": "x" * (pty_supervisor.MAX_FRAME_BYTES + 1)}
        with self.assertRaises(ValueError):
            await pty_supervisor.write_frame(writer, payload)

    async def test_multiple_frames_back_to_back(self):
        writer = _FakeWriter()
        await pty_supervisor.write_frame(writer, {"op": "a"})
        await pty_supervisor.write_frame(writer, {"op": "b", "n": 2})
        wire = b"".join(writer.chunks)
        reader = _reader_from(wire)
        a = await pty_supervisor.read_frame(reader)
        b = await pty_supervisor.read_frame(reader)
        self.assertEqual(a, {"op": "a"})
        self.assertEqual(b, {"op": "b", "n": 2})


class ProtocolConstantsTests(unittest.TestCase):
    def test_version_is_int(self):
        self.assertIsInstance(pty_supervisor.PROTOCOL_VERSION, int)
        self.assertGreaterEqual(pty_supervisor.PROTOCOL_VERSION, 1)

    def test_default_path_names(self):
        self.assertTrue(
            pty_supervisor.DEFAULT_SOCKET_NAME.endswith(".sock"))
        self.assertTrue(
            pty_supervisor.DEFAULT_PID_FILE_NAME.endswith(".pid"))
        self.assertTrue(
            pty_supervisor.DEFAULT_LOG_FILE_NAME.endswith(".log"))


if __name__ == "__main__":
    unittest.main()
