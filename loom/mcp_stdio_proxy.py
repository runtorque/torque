"""Minimal MCP stdio proxy helpers for local Loom entrypoints."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def _loom_mcp_url() -> str:
    port = str(os.environ.get("LOOM_PORT", "18932") or "").strip() or "18932"
    return f"http://127.0.0.1:{port}/mcp"


def _read_framed_message(reader) -> bytes | None:
    # MCP stdio transport is newline-delimited JSON-RPC: one message per line.
    while True:
        line = reader.readline()
        if not line:
            return None
        stripped = line.strip()
        if not stripped:
            continue
        return stripped


def _write_framed_message(writer, body: bytes) -> None:
    writer.write(body)
    if not body.endswith(b"\n"):
        writer.write(b"\n")
    writer.flush()


def _proxy_request(payload: bytes, *, caller_id: str) -> bytes:
    req = urllib.request.Request(
        _loom_mcp_url(),
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Loom-Cell-Id": str(caller_id or "").strip(),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def serve_http_proxy(caller_id: str) -> int:
    """Proxy stdio-framed MCP requests to Loom's local HTTP endpoint."""
    reader = sys.stdin.buffer
    writer = sys.stdout.buffer
    while True:
        payload = _read_framed_message(reader)
        if payload is None:
            return 0
        request_id = None
        try:
            parsed = json.loads(payload.decode("utf-8"))
            request_id = parsed.get("id")
            response_payload = _proxy_request(payload, caller_id=caller_id)
        except urllib.error.HTTPError as exc:
            response_payload = exc.read() or json.dumps({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }).encode("utf-8")
        except Exception as exc:
            response_payload = json.dumps({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }).encode("utf-8")
        if response_payload:
            _write_framed_message(writer, response_payload)
