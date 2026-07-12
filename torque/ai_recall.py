"""Semantic recall over Torque's local AI embedding index.

The recall path is intentionally read-only and best-effort.  It embeds the
query through ``LocalEmbeddingService`` (off the event loop), reads ANN
matches from a fresh AI SQLite connection, and lets the caller provide the
role-specific visibility predicate that drops invisible snippets before any
result is returned.
"""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from torque.ai_embeddings import EmbeddingFailure, EmbeddingResult, LocalEmbeddingService
from torque.ai_index import AIIndexDependencyMissing
from torque.config import DATA_DIR
from torque.state import AI_DEFAULT_EMBEDDING_MODEL

DEFAULT_RECALL_LIMIT = 5
MAX_RECALL_LIMIT = 20
INITIAL_OVERFETCH_FACTOR = 8
MAX_OVERFETCH = 500
SNIPPET_CHARS = 900


@dataclass(frozen=True)
class RecallCandidate:
    chunk_id: int
    source_key: str
    source_type: str
    source_id: str
    source_sub_id: str
    group_name: str
    owner_kind: str
    owner_id: str
    participant_ids: tuple[str, ...]
    participant_kinds: dict[str, str]
    visibility_json: dict[str, Any]
    title: str
    source_updated_at: str
    text: str
    score: float

    def as_result(self, rank: int) -> dict:
        return {
            "rank": int(rank),
            "score": self.score,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "title": self.title,
            "group": self.group_name,
            "snippet": _snippet(self.text),
            "updated_at": self.source_updated_at,
            # Internal ACL anchors are removed by the common MCP result gate
            # before protocol output. They retain the source relationship
            # needed to narrow semantic recall below the handler's platform
            # visibility ceiling without exposing indexed ownership metadata.
            "_acl_owner_id": self.owner_id,
            "_acl_participant_ids": list(self.participant_ids),
        }


VisibilityFilter = Callable[[RecallCandidate], bool]


def normalize_recall_limit(value) -> tuple[int, str]:
    if value in (None, ""):
        return DEFAULT_RECALL_LIMIT, ""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return 0, "limit must be an integer"
    if limit < 1:
        return 0, "limit must be at least 1"
    return min(limit, MAX_RECALL_LIMIT), ""


async def semantic_recall_payload(
    *,
    state,
    query: str,
    limit: int,
    visibility_filter: VisibilityFilter,
) -> dict:
    """Return a non-error recall payload for operational/dependency states.

    ``ValueError`` is reserved for malformed caller input.  All runtime/index
    degradation paths return an empty ``results`` payload with a human-readable
    message so MCP callers can continue without retry storms.
    """

    query = str(query or "").strip()
    if not query:
        raise ValueError("query is required")
    limit = max(1, min(int(limit or DEFAULT_RECALL_LIMIT), MAX_RECALL_LIMIT))

    settings = getattr(state, "global_settings", None)
    if not bool(getattr(settings, "ai_enabled", False)):
        return _degraded("disabled", "AI semantic recall is disabled.")

    db = getattr(state, "db", None)
    if db is None:
        return _degraded("not_ready", "AI semantic recall index is not ready.")

    desired_model = (
        str(getattr(settings, "ai_embedding_model", "") or "").strip()
        or AI_DEFAULT_EMBEDDING_MODEL
    )
    loader = _sqlite_vec_loader_for_state(state)

    try:
        preflight = await asyncio.to_thread(
            _preflight_sync,
            db,
            desired_model,
            loader,
        )
    except AIIndexDependencyMissing:
        return _degraded(
            "dependency_missing",
            "sqlite-vec is required for AI semantic recall. Run `make ai-deps`.",
        )

    if preflight.get("degraded"):
        return _degraded(preflight["status"], preflight["message"])

    embedding_service, owns_embedding_service = _embedding_service_for_state(state)
    try:
        embedding_response = await embedding_service.embed_texts(
            desired_model,
            [query],
        )
    finally:
        if owns_embedding_service:
            await embedding_service.shutdown()

    if isinstance(embedding_response, EmbeddingFailure):
        status = (
            "dependency_missing"
            if embedding_response.kind == "dependency_missing"
            else "not_ready"
        )
        return _degraded(status, embedding_response.message)
    if (
        not isinstance(embedding_response, EmbeddingResult)
        or not embedding_response.vectors
    ):
        return _degraded("not_ready", "Embedding service returned no query vector.")
    query_vector = list(embedding_response.vectors[0] or [])
    active_dims = int(preflight.get("active_dims", 0) or 0)
    if len(query_vector) != active_dims:
        return _degraded(
            "model_mismatch",
            "Query embedding dimensions do not match the active AI index.",
        )

    fetch_limit = min(
        max(limit * INITIAL_OVERFETCH_FACTOR, limit),
        MAX_OVERFETCH,
    )
    visible: list[RecallCandidate] = []
    while True:
        try:
            candidates = await asyncio.to_thread(
                _ann_query_sync,
                db,
                query_vector,
                desired_model,
                active_dims,
                fetch_limit,
                loader,
            )
        except AIIndexDependencyMissing:
            return _degraded(
                "dependency_missing",
                "sqlite-vec is required for AI semantic recall. Run `make ai-deps`.",
            )
        except sqlite3.Error:
            return _degraded("not_ready", "AI semantic recall index is not ready.")

        visible = [
            candidate
            for candidate in candidates
            if visibility_filter(candidate)
        ]
        if (
            len(visible) >= limit
            or fetch_limit >= MAX_OVERFETCH
            or len(candidates) < fetch_limit
        ):
            break
        fetch_limit = min(fetch_limit * 2, MAX_OVERFETCH)

    results = [
        candidate.as_result(rank)
        for rank, candidate in enumerate(visible[:limit], start=1)
    ]
    return {
        "type": "semantic_recall",
        "status": "ok",
        "results": results,
        "message": (
            "No visible semantic recall results."
            if not results
            else f"Returned {len(results)} visible semantic recall result(s)."
        ),
    }


def _degraded(status: str, message: str) -> dict:
    return {
        "type": "semantic_recall",
        "status": str(status or "not_ready"),
        "results": [],
        "message": str(message or "AI semantic recall is unavailable."),
    }


def _embedding_service_for_state(state) -> tuple[LocalEmbeddingService, bool]:
    service = getattr(state, "ai_embedding_service", None)
    if service is not None:
        return service, False
    index_service = getattr(state, "ai_index_service", None)
    service = getattr(index_service, "embedding_service", None)
    if service is not None:
        return service, False
    return LocalEmbeddingService(data_dir=Path(DATA_DIR)), True


def _sqlite_vec_loader_for_state(state):
    loader = getattr(state, "ai_recall_sqlite_vec_loader", None)
    if callable(loader):
        return loader
    index_service = getattr(state, "ai_index_service", None)
    loader = getattr(index_service, "_sqlite_vec_loader", None)
    if callable(loader):
        return loader
    return None


def _load_sqlite_vec(conn: sqlite3.Connection, loader) -> None:
    try:
        if callable(loader):
            loader(conn)
            return
        import sqlite_vec  # type: ignore

        conn.enable_load_extension(True)
        try:
            sqlite_vec.load(conn)
        finally:
            conn.enable_load_extension(False)
    except Exception as exc:
        raise AIIndexDependencyMissing(
            "sqlite-vec is required for AI semantic recall. Run `make ai-deps`."
        ) from exc


def _preflight_sync(db, desired_model: str, loader) -> dict:
    conn = db.open_ai_index_connection()
    try:
        state = db.ai_get_index_state(conn)
        status = str(state.get("status", "") or "not_built")
        if status == "dependency_missing":
            return {
                "degraded": True,
                "status": "dependency_missing",
                "message": (
                    str(state.get("last_error", "") or "")
                    or "sqlite-vec is required for AI semantic recall. Run `make ai-deps`."
                ),
            }
        if status == "rebuild_pending" or bool(state.get("rebuild_required")):
            return {
                "degraded": True,
                "status": "rebuild_pending",
                "message": "AI semantic recall index rebuild is pending.",
            }
        if status != "ready":
            return {
                "degraded": True,
                "status": "not_ready",
                "message": "AI semantic recall index is not ready.",
            }
        active_model = str(state.get("active_model_id", "") or "").strip()
        active_dims = int(state.get("active_dims", 0) or 0)
        if not active_model or active_model != str(desired_model or "").strip():
            return {
                "degraded": True,
                "status": "model_mismatch",
                "message": "AI semantic recall index model does not match settings.",
            }
        if active_dims <= 0 or not db.ai_index_vector_table_exists(conn):
            return {
                "degraded": True,
                "status": "not_ready",
                "message": "AI semantic recall index has no vectors.",
            }
        _load_sqlite_vec(conn, loader)
        vector_count = int(
            conn.execute("SELECT COUNT(*) FROM ai_embedding_vec").fetchone()[0]
            or 0
        )
        chunk_count = int(
            conn.execute("SELECT COUNT(*) FROM ai_embedding_chunks").fetchone()[0]
            or 0
        )
        if vector_count <= 0 or chunk_count <= 0:
            return {
                "degraded": True,
                "status": "not_ready",
                "message": "AI semantic recall index has no vectors.",
            }
        return {
            "degraded": False,
            "active_model": active_model,
            "active_dims": active_dims,
        }
    finally:
        conn.close()


def _ann_query_sync(
    db,
    query_vector: list[float],
    model_id: str,
    dims: int,
    limit: int,
    loader,
) -> list[RecallCandidate]:
    conn = db.open_ai_index_connection()
    conn.row_factory = sqlite3.Row
    try:
        _load_sqlite_vec(conn, loader)
        try:
            rows = conn.execute(
                """
                WITH matches AS (
                    SELECT rowid, distance
                    FROM ai_embedding_vec
                    WHERE embedding MATCH ? AND k = ?
                )
                SELECT
                    c.id AS chunk_id,
                    c.text AS text,
                    s.source_key AS source_key,
                    s.source_type AS source_type,
                    s.source_id AS source_id,
                    s.source_sub_id AS source_sub_id,
                    s.group_name AS group_name,
                    s.owner_kind AS owner_kind,
                    s.owner_id AS owner_id,
                    s.participant_ids AS participant_ids,
                    s.participant_kinds AS participant_kinds,
                    s.visibility_json AS visibility_json,
                    s.title AS title,
                    s.source_updated_at AS source_updated_at,
                    matches.distance AS distance
                FROM matches
                JOIN ai_embedding_chunks c ON c.id = matches.rowid
                JOIN ai_embedding_sources s ON s.source_key = c.source_key
                WHERE c.embedding_model_id = ?
                  AND c.embedding_dims = ?
                  AND s.state = 'indexed'
                ORDER BY matches.distance ASC, c.id ASC
                LIMIT ?
                """,
                (
                    json.dumps([float(value) for value in query_vector], separators=(",", ":")),
                    max(1, int(limit or 1)),
                    str(model_id or ""),
                    int(dims or 0),
                    max(1, int(limit or 1)),
                ),
            ).fetchall()
            return [_candidate_from_row(row) for row in rows]
        except sqlite3.Error:
            if _vec_table_is_virtual(conn):
                raise
            return _fallback_scan_query(conn, query_vector, model_id, dims, limit)
    finally:
        conn.close()


def _fallback_scan_query(
    conn: sqlite3.Connection,
    query_vector: list[float],
    model_id: str,
    dims: int,
    limit: int,
) -> list[RecallCandidate]:
    rows = conn.execute(
        """
        SELECT
            c.id AS chunk_id,
            c.text AS text,
            s.source_key AS source_key,
            s.source_type AS source_type,
            s.source_id AS source_id,
            s.source_sub_id AS source_sub_id,
            s.group_name AS group_name,
            s.owner_kind AS owner_kind,
            s.owner_id AS owner_id,
            s.participant_ids AS participant_ids,
            s.participant_kinds AS participant_kinds,
            s.visibility_json AS visibility_json,
            s.title AS title,
            s.source_updated_at AS source_updated_at,
            v.embedding AS embedding
        FROM ai_embedding_vec v
        JOIN ai_embedding_chunks c ON c.id = v.rowid
        JOIN ai_embedding_sources s ON s.source_key = c.source_key
        WHERE c.embedding_model_id = ?
          AND c.embedding_dims = ?
          AND s.state = 'indexed'
        """,
        (str(model_id or ""), int(dims or 0)),
    ).fetchall()
    scored = []
    for row in rows:
        vector = _json_loads(row["embedding"], [])
        distance = _cosine_distance(query_vector, vector)
        scored.append((distance, int(row["chunk_id"] or 0), row))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [
        _candidate_from_row(row, distance=distance)
        for distance, _chunk_id, row in scored[: max(1, int(limit or 1))]
    ]


def _vec_table_is_virtual(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='ai_embedding_vec' LIMIT 1"
    ).fetchone()
    sql = str((row[0] if row else "") or "").upper()
    return "VIRTUAL TABLE" in sql


def _candidate_from_row(row, *, distance=None) -> RecallCandidate:
    distance_value = row["distance"] if distance is None and "distance" in row.keys() else distance
    return RecallCandidate(
        chunk_id=int(row["chunk_id"] or 0),
        source_key=str(row["source_key"] or ""),
        source_type=str(row["source_type"] or ""),
        source_id=str(row["source_id"] or ""),
        source_sub_id=str(row["source_sub_id"] or ""),
        group_name=str(row["group_name"] or ""),
        owner_kind=str(row["owner_kind"] or ""),
        owner_id=str(row["owner_id"] or ""),
        participant_ids=tuple(
            str(item or "").strip()
            for item in _json_loads(row["participant_ids"], [])
            if str(item or "").strip()
        ),
        participant_kinds={
            str(key): str(value)
            for key, value in _json_loads(row["participant_kinds"], {}).items()
        },
        visibility_json=_json_loads(row["visibility_json"], {}),
        title=str(row["title"] or ""),
        source_updated_at=str(row["source_updated_at"] or ""),
        text=str(row["text"] or ""),
        score=_score_from_distance(distance_value),
    )


def _json_loads(value, default):
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        decoded = default
    if isinstance(default, dict):
        return decoded if isinstance(decoded, dict) else {}
    if isinstance(default, list):
        return decoded if isinstance(decoded, list) else []
    return decoded if decoded is not None else default


def _cosine_distance(left: list[float], right: list[float]) -> float:
    try:
        a = [float(value) for value in left]
        b = [float(value) for value in right]
    except (TypeError, ValueError):
        return float("inf")
    if not a or len(a) != len(b):
        return float("inf")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a <= 0 or norm_b <= 0:
        return float("inf")
    cosine = max(-1.0, min(1.0, dot / (norm_a * norm_b)))
    return 1.0 - cosine


def _score_from_distance(distance) -> float:
    try:
        value = float(distance)
    except (TypeError, ValueError):
        value = float("inf")
    if not math.isfinite(value):
        return 0.0
    return round(1.0 / (1.0 + max(0.0, value)), 4)


def _snippet(text: str) -> str:
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= SNIPPET_CHARS:
        return collapsed
    return collapsed[: SNIPPET_CHARS - 1].rstrip() + "…"
