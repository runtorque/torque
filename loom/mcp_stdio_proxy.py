"""Minimal MCP stdio proxy helpers for local Loom entrypoints."""

from __future__ import annotations

import json
import os
import sys
import asyncio
import urllib.error

from .config import DB_FILE
from .db import LoomDB
from .mcp_retry import (
    DEFAULT_ATTEMPTS,
    IDEMPOTENCY_HEADER,
    RetryExhausted,
    ensure_mcp_payload_idempotency,
    loom_mcp_url,
    mcp_tool_surface,
    post_json_bytes_with_retry,
    queue_failed_mcp_write,
)

def _loom_mcp_url() -> str:
    return loom_mcp_url()


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


def _tool_call_result(request_id, text: str, *, is_error: bool = False) -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": str(text or "")}],
                "isError": bool(is_error),
            },
        }
    ).encode("utf-8")


def _jsonrpc_error(request_id, exc: Exception) -> bytes:
    return json.dumps({
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32000, "message": str(exc)},
    }).encode("utf-8")


async def _proxy_request(payload: bytes, *, caller_id: str) -> bytes:
    parsed = json.loads(payload.decode("utf-8"))
    parsed, idempotency_key, tool_name = ensure_mcp_payload_idempotency(parsed)
    headers = {
        "Content-Type": "application/json",
        "X-Loom-Cell-Id": str(caller_id or "").strip(),
    }
    if idempotency_key:
        headers[IDEMPOTENCY_HEADER] = idempotency_key
    db = None
    try:
        db = LoomDB(DB_FILE)
        db.init()
    except Exception:
        db = None
    try:
        return await post_json_bytes_with_retry(
            _loom_mcp_url(),
            parsed,
            headers=headers,
            surface=mcp_tool_surface(tool_name),
            tool_name=tool_name,
            db=db,
        )
    except RetryExhausted as exc:
        queued = False
        if idempotency_key and tool_name:
            queued = queue_failed_mcp_write(
                payload=parsed,
                caller_id=caller_id,
                idempotency_key=idempotency_key,
                tool_name=tool_name,
                attempts=exc.attempts or DEFAULT_ATTEMPTS,
                last_error=exc.last_error or str(exc),
            )
        if queued and parsed.get("method") == "tools/call":
            return _tool_call_result(
                parsed.get("id"),
                (
                    "Loom write could not reach the daemon after retries; "
                    "it has been queued durably and will replay on the next "
                    "daemon boot."
                ),
                is_error=True,
            )
        raise
    finally:
        if db is not None:
            db.close()


async def _serve_http_proxy_async(caller_id: str) -> int:
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
            response_payload = await _proxy_request(payload, caller_id=caller_id)
        except urllib.error.HTTPError as exc:
            response_payload = exc.read() or _jsonrpc_error(request_id, exc)
        except Exception as exc:
            response_payload = _jsonrpc_error(request_id, exc)
        if response_payload:
            _write_framed_message(writer, response_payload)


def serve_http_proxy(caller_id: str) -> int:
    """Proxy stdio-framed MCP requests to Loom's local HTTP endpoint."""
    return asyncio.run(_serve_http_proxy_async(caller_id))
