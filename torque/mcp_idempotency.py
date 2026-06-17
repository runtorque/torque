"""Bounded MCP idempotency receipt storage helpers."""

from __future__ import annotations

import json
import sqlite3
import time

MCP_IDEMPOTENCY_FULL_TTL_SECONDS = 7 * 24 * 60 * 60
MCP_IDEMPOTENCY_TOMBSTONE_TTL_SECONDS = 0  # 0 means keep compacted tombstones indefinitely
MCP_IDEMPOTENCY_MAX_RESPONSE_BYTES = 64 * 1024
MCP_IDEMPOTENCY_PRUNE_BATCH_SIZE = 500
MCP_IDEMPOTENCY_MAINTENANCE_INTERVAL_SECONDS = 10 * 60

MCP_IDEMPOTENCY_WARN_TABLE_BYTES = 256 * 1024 * 1024
MCP_IDEMPOTENCY_WARN_RESPONSE_BYTES = 128 * 1024 * 1024
MCP_IDEMPOTENCY_WARN_ROW_COUNT = 50_000
MCP_IDEMPOTENCY_WARN_MAX_RESPONSE_BYTES = 1024 * 1024
MCP_IDEMPOTENCY_WARN_AVG_RESPONSE_BYTES = 64 * 1024

_COMPACTED_TYPE = "idempotency_receipt_compacted"


def json_response_bytes(response_json: str) -> int:
    return len(str(response_json or "").encode("utf-8"))


def compacted_idempotency_response(
    *,
    surface: str,
    tool_name: str,
    reason: str,
    original_response_bytes: int,
    max_response_bytes: int = MCP_IDEMPOTENCY_MAX_RESPONSE_BYTES,
) -> dict:
    """Return a small safe replay payload for a receipt whose full body was pruned.

    The payload intentionally does *not* report success: reusing the same key
    will not repeat the original side effect, but exact replay is no longer
    available.  Operators/agents must inspect current state and issue a fresh
    request with a new idempotency key if more work is required.
    """
    surface = str(surface or "")
    tool_name = str(tool_name or "")
    reason = str(reason or "compacted")
    message = (
        "The original write for this idempotency key already completed, but "
        f"Torque compacted its stored replay receipt ({reason}) to keep the "
        "database bounded. Exact replay is unavailable; inspect current state "
        "and use a new idempotency key for any new write."
    )
    metadata = {
        "type": _COMPACTED_TYPE,
        "reason": reason,
        "surface": surface,
        "tool_name": tool_name,
        "original_response_bytes": int(original_response_bytes or 0),
        "max_response_bytes": int(max_response_bytes or 0),
    }
    if surface == "api":
        return {
            "ok": False,
            "type": _COMPACTED_TYPE,
            "error": message,
            "idempotency_receipt": metadata,
        }
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
        "idempotency_receipt": metadata,
    }


def is_compacted_idempotency_response(value) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("type") == _COMPACTED_TYPE:
        return True
    receipt = value.get("idempotency_receipt")
    return isinstance(receipt, dict) and receipt.get("type") == _COMPACTED_TYPE


def collect_mcp_idempotency_storage_stats(conn: sqlite3.Connection) -> dict:
    """Return row/byte stats for doctor and metrics surfaces.

    ``dbstat`` is optional in SQLite builds.  When unavailable, the section
    still returns logical payload stats and marks table byte accounting as
    unavailable instead of failing doctor/metrics reads.
    """
    section = {
        "exists": False,
        "row_count": 0,
        "full_row_count": 0,
        "compacted_row_count": 0,
        "response_bytes": 0,
        "avg_response_bytes": 0,
        "max_response_bytes": 0,
        "table_bytes": None,
        "dbstat_available": False,
        "thresholds": {
            "table_bytes": MCP_IDEMPOTENCY_WARN_TABLE_BYTES,
            "response_bytes": MCP_IDEMPOTENCY_WARN_RESPONSE_BYTES,
            "row_count": MCP_IDEMPOTENCY_WARN_ROW_COUNT,
            "max_response_bytes": MCP_IDEMPOTENCY_WARN_MAX_RESPONSE_BYTES,
            "avg_response_bytes": MCP_IDEMPOTENCY_WARN_AVG_RESPONSE_BYTES,
        },
        "warnings": [],
    }
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='mcp_idempotency' LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return section
    if not row:
        return section
    section["exists"] = True
    try:
        columns = {
            str(info[1] or "")
            for info in conn.execute("PRAGMA table_info(mcp_idempotency)").fetchall()
        }
        compacted_expr = (
            "COALESCE(SUM(CASE WHEN COALESCE(compacted_at, 0) > 0 "
            "THEN 1 ELSE 0 END), 0)"
            if "compacted_at" in columns
            else "0"
        )
        row = conn.execute(
            "SELECT COUNT(*), "
            "COALESCE(SUM(length(COALESCE(response_json, ''))), 0), "
            "COALESCE(MAX(length(COALESCE(response_json, ''))), 0), "
            "COALESCE(AVG(length(COALESCE(response_json, ''))), 0), "
            f"{compacted_expr} FROM mcp_idempotency"
        ).fetchone()
    except sqlite3.Error:
        row = None
    if row:
        row_count = int(row[0] or 0)
        compacted = int(row[4] or 0)
        section.update({
            "row_count": row_count,
            "response_bytes": int(row[1] or 0),
            "max_response_bytes": int(row[2] or 0),
            "avg_response_bytes": float(row[3] or 0.0),
            "compacted_row_count": compacted,
            "full_row_count": max(0, row_count - compacted),
        })
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(pgsize), 0) FROM dbstat "
            "WHERE name='mcp_idempotency'"
        ).fetchone()
        section["table_bytes"] = int((row or [0])[0] or 0)
        section["dbstat_available"] = True
    except sqlite3.Error:
        section["table_bytes"] = None
        section["dbstat_available"] = False

    thresholds = section["thresholds"]
    if section["table_bytes"] is not None and section["table_bytes"] >= thresholds["table_bytes"]:
        section["warnings"].append("table_bytes")
    if section["response_bytes"] >= thresholds["response_bytes"]:
        section["warnings"].append("response_bytes")
    if section["row_count"] >= thresholds["row_count"]:
        section["warnings"].append("row_count")
    if section["max_response_bytes"] >= thresholds["max_response_bytes"]:
        section["warnings"].append("max_response_bytes")
    if section["avg_response_bytes"] >= thresholds["avg_response_bytes"]:
        section["warnings"].append("avg_response_bytes")
    return section


def maintain_mcp_idempotency_storage(
    conn: sqlite3.Connection,
    *,
    now: float | None = None,
    full_ttl_seconds: int = MCP_IDEMPOTENCY_FULL_TTL_SECONDS,
    tombstone_ttl_seconds: int = MCP_IDEMPOTENCY_TOMBSTONE_TTL_SECONDS,
    max_response_bytes: int = MCP_IDEMPOTENCY_MAX_RESPONSE_BYTES,
    batch_size: int = MCP_IDEMPOTENCY_PRUNE_BATCH_SIZE,
) -> dict:
    """Compact/delete MCP idempotency receipts in bounded batches.

    Full receipts older than ``full_ttl_seconds`` or larger than
    ``max_response_bytes`` are compacted to small tombstones that keep the
    idempotency key and request hash, preserving conflict checks and preventing
    same-key retries from repeating side effects.  By default tombstones are
    retained indefinitely; callers may pass a positive ``tombstone_ttl_seconds``
    for an explicit row-count cleanup window.
    """
    now_value = float(time.time() if now is None else now)
    batch = max(1, int(batch_size or MCP_IDEMPOTENCY_PRUNE_BATCH_SIZE))
    max_bytes = max(1, int(max_response_bytes or MCP_IDEMPOTENCY_MAX_RESPONSE_BYTES))
    full_cutoff = now_value - max(0, int(full_ttl_seconds or 0))
    tombstone_ttl = max(0, int(tombstone_ttl_seconds or 0))
    tombstone_cutoff = now_value - tombstone_ttl
    summary = {
        "compacted": 0,
        "deleted": 0,
        "batch_size": batch,
        "max_response_bytes": max_bytes,
        "full_ttl_seconds": int(full_ttl_seconds or 0),
        "tombstone_ttl_seconds": int(tombstone_ttl_seconds or 0),
    }
    try:
        rows = conn.execute(
            "SELECT idempotency_key, surface, tool_name, response_json, "
            "updated_at FROM mcp_idempotency "
            "WHERE COALESCE(compacted_at, 0) <= 0 "
            "AND (updated_at < ? OR length(COALESCE(response_json, '')) > ?) "
            "ORDER BY updated_at ASC LIMIT ?",
            (full_cutoff, max_bytes, batch),
        ).fetchall()
    except sqlite3.Error:
        return summary

    for key, surface, tool_name, response_json, updated_at in rows:
        original_bytes = json_response_bytes(response_json)
        reason = "oversize" if original_bytes > max_bytes else "expired"
        compacted = compacted_idempotency_response(
            surface=str(surface or ""),
            tool_name=str(tool_name or ""),
            reason=reason,
            original_response_bytes=original_bytes,
            max_response_bytes=max_bytes,
        )
        compacted_json = json.dumps(compacted, separators=(",", ":"))
        compacted_bytes = json_response_bytes(compacted_json)
        conn.execute(
            "UPDATE mcp_idempotency SET response_json=?, response_bytes=?, "
            "expires_at=?, compacted_at=? WHERE idempotency_key=?",
            (
                compacted_json,
                compacted_bytes,
                float(updated_at or now_value),
                now_value,
                str(key or ""),
            ),
        )
        summary["compacted"] += 1

    if tombstone_ttl > 0:
        try:
            cur = conn.execute(
                "DELETE FROM mcp_idempotency WHERE idempotency_key IN ("
                "SELECT idempotency_key FROM mcp_idempotency "
                "WHERE COALESCE(compacted_at, 0) > 0 AND compacted_at < ? "
                "ORDER BY compacted_at ASC LIMIT ?)",
                (tombstone_cutoff, batch),
            )
            summary["deleted"] = int(cur.rowcount if cur.rowcount is not None else 0)
        except sqlite3.Error:
            summary["deleted"] = 0
    if summary["compacted"] or summary["deleted"]:
        conn.commit()
    return summary
