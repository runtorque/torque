import importlib
import io
import json
import types
import unittest
from unittest import mock


def _frame(payload: bytes) -> bytes:
    # MCP stdio transport: newline-delimited JSON-RPC.
    return payload + b"\n"


class MCPStdioProxyTests(unittest.TestCase):
    def setUp(self):
        self.proxy_mod = importlib.import_module("torque.mcp_stdio_proxy")
        self.proxy_mod = importlib.reload(self.proxy_mod)

    def test_notification_with_empty_http_body_emits_no_stdio_frame(self):
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }).encode("utf-8")
        stdin = types.SimpleNamespace(buffer=io.BytesIO(_frame(payload)))
        stdout_buffer = io.BytesIO()
        stdout = types.SimpleNamespace(buffer=stdout_buffer)

        with mock.patch.object(self.proxy_mod.sys, "stdin", stdin), \
                mock.patch.object(self.proxy_mod.sys, "stdout", stdout), \
                mock.patch.object(
                    self.proxy_mod.uuid,
                    "uuid4",
                    return_value=types.SimpleNamespace(hex="mcp-session-1"),
                ), \
                mock.patch.object(
                    self.proxy_mod,
                    "_proxy_request",
                    autospec=True,
                    return_value=b"",
                ) as proxy_request:
            rc = self.proxy_mod.serve_http_proxy("eng-1")

        self.assertEqual(rc, 0)
        proxy_request.assert_called_once_with(
            payload,
            caller_id="eng-1",
            mcp_session_id="mcp-session-1",
        )
        self.assertEqual(stdout_buffer.getvalue(), b"")

    def test_request_response_with_body_keeps_stdio_framing(self):
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/list",
        }).encode("utf-8")
        response_payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 7,
            "result": {"tools": []},
        }).encode("utf-8")
        stdin = types.SimpleNamespace(buffer=io.BytesIO(_frame(payload)))
        stdout_buffer = io.BytesIO()
        stdout = types.SimpleNamespace(buffer=stdout_buffer)

        with mock.patch.object(self.proxy_mod.sys, "stdin", stdin), \
                mock.patch.object(self.proxy_mod.sys, "stdout", stdout), \
                mock.patch.object(
                    self.proxy_mod.uuid,
                    "uuid4",
                    return_value=types.SimpleNamespace(hex="mcp-session-2"),
                ), \
                mock.patch.object(
                    self.proxy_mod,
                    "_proxy_request",
                    autospec=True,
                    return_value=response_payload,
                ) as proxy_request:
            rc = self.proxy_mod.serve_http_proxy("eng-2")

        self.assertEqual(rc, 0)
        proxy_request.assert_called_once_with(
            payload,
            caller_id="eng-2",
            mcp_session_id="mcp-session-2",
        )
        self.assertEqual(stdout_buffer.getvalue(), _frame(response_payload))

    def test_proxy_request_forwards_mcp_session_header(self):
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/list",
        }).encode("utf-8")

        with mock.patch.object(
            self.proxy_mod,
            "post_json_bytes_with_retry",
            autospec=True,
            return_value=b"{}",
        ) as post_json, \
                mock.patch.object(
                    self.proxy_mod,
                    "TorqueDB",
                    autospec=True,
                ) as torque_db:
            torque_db.return_value.init.return_value = None
            torque_db.return_value.close.return_value = None
            result = self.proxy_mod.asyncio.run(
                self.proxy_mod._proxy_request(
                    payload,
                    caller_id="eng-3",
                    mcp_session_id="mcp-session-3",
                )
            )

        self.assertEqual(result, b"{}")
        self.assertEqual(
            post_json.call_args.kwargs["headers"],
            {
                "Content-Type": "application/json",
                "X-Torque-Cell-Id": "eng-3",
                "X-Torque-MCP-Session-Id": "mcp-session-3",
            },
        )
