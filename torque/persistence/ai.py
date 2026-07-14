"""AI provider, metrics, summaries, and semantic-index persistence."""

import json
import sqlite3
import time
import uuid

from torque.db_schema import (
    create_ai_embedding_vec_table,
    drop_ai_embedding_vec_table,
)
from torque.persistence.common import json_loads_default as _json_loads_default

AI_SECRET_PROVIDERS = {"anthropic", "openai_compatible"}
AI_INDEX_STATE_ID = "default"
AI_INDEX_SOURCE_TYPES = {
    "architect_journal",
    "engineer_journal",
    "decision",
    "task",
    "engineer_peer_thread",
}
AI_SUMMARY_STATUSES = {"empty", "ready", "stale", "refreshing", "error"}
def _normalize_ai_secret_provider(provider: str) -> str:
    provider = str(provider or "").strip().lower()
    if provider not in AI_SECRET_PROVIDERS:
        raise ValueError(
            "provider must be one of "
            f"{', '.join(sorted(AI_SECRET_PROVIDERS))}"
        )
    return provider
def _json_dumps_stable(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
def _ai_index_source_type(value: str) -> str:
    source_type = str(value or "").strip()
    if source_type not in AI_INDEX_SOURCE_TYPES:
        raise ValueError(
            "source_type must be one of "
            f"{', '.join(sorted(AI_INDEX_SOURCE_TYPES))}"
        )
    return source_type
def _ai_source_state_for_hash(
    existing: dict | None,
    content_hash: str,
) -> str:
    if not existing:
        return "pending"
    indexed_hash = str(existing.get("indexed_content_hash", "") or "")
    prior_hash = str(existing.get("content_hash", "") or "")
    prior_state = str(existing.get("state", "") or "")
    if indexed_hash and indexed_hash == content_hash:
        return "indexed"
    if prior_hash and prior_hash != content_hash:
        return "stale"
    if prior_state == "error":
        return "stale" if indexed_hash else "pending"
    return "stale" if indexed_hash else "pending"
def _ai_job_dict(row, cols=None) -> dict | None:
    if not row:
        return None
    cols = cols or [
        "id",
        "mode",
        "status",
        "reason",
        "started_at",
        "updated_at",
        "completed_at",
        "totals",
        "error",
    ]
    item = dict(zip(cols, row))
    item["totals"] = _json_loads_default(item.get("totals", "{}"), {})
    for key in ("started_at", "updated_at", "completed_at"):
        try:
            item[key] = float(item.get(key, 0) or 0)
        except (TypeError, ValueError):
            item[key] = 0.0
    return item
def _ai_index_state_dict(row, cols=None) -> dict:
    defaults = {
        "id": AI_INDEX_STATE_ID,
        "desired_model_id": "BAAI/bge-m3",
        "active_model_id": "",
        "active_dims": 0,
        "status": "not_built",
        "rebuild_required": 0,
        "rebuild_reason": "",
        "corpus_config": "{}",
        "last_scan_at": 0.0,
        "last_built_at": 0.0,
        "last_error": "",
    }
    if row:
        cols = cols or list(defaults)
        defaults.update(dict(zip(cols, row)))
    defaults["active_dims"] = _nonnegative_int(defaults.get("active_dims", 0))
    defaults["rebuild_required"] = bool(defaults.get("rebuild_required", 0))
    defaults["corpus_config"] = _json_loads_default(
        defaults.get("corpus_config", "{}"), {}
    )
    for key in ("last_scan_at", "last_built_at"):
        try:
            defaults[key] = float(defaults.get(key, 0) or 0)
        except (TypeError, ValueError):
            defaults[key] = 0.0
    return defaults
def _ai_source_dict(row, cols=None) -> dict | None:
    if not row:
        return None
    cols = cols or [
        "source_key",
        "source_type",
        "source_id",
        "source_sub_id",
        "group_name",
        "owner_kind",
        "owner_id",
        "participant_ids",
        "participant_kinds",
        "visibility_json",
        "title",
        "source_updated_at",
        "content_hash",
        "indexed_content_hash",
        "state",
        "discovered_at",
        "last_seen_at",
        "indexed_at",
        "error",
    ]
    item = dict(zip(cols, row))
    item["participant_ids"] = _json_loads_default(
        item.get("participant_ids", "[]"), []
    )
    item["participant_kinds"] = _json_loads_default(
        item.get("participant_kinds", "{}"), {}
    )
    item["visibility_json"] = _json_loads_default(
        item.get("visibility_json", "{}"), {}
    )
    for key in ("discovered_at", "last_seen_at", "indexed_at"):
        try:
            item[key] = float(item.get(key, 0) or 0)
        except (TypeError, ValueError):
            item[key] = 0.0
    return item
def _ai_summary_status(value: str) -> str:
    status = str(value or "").strip().lower()
    return status if status in AI_SUMMARY_STATUSES else "empty"
def _ai_summary_dict(row, cols=None) -> dict | None:
    if not row:
        return None
    cols = cols or [
        "summary_key",
        "summary_type",
        "scope_kind",
        "scope_ref",
        "provider",
        "model",
        "prompt_version",
        "source_hash",
        "source_counts",
        "summary_text",
        "status",
        "generated_at",
        "updated_at",
        "error",
    ]
    item = dict(zip(cols, row))
    item["source_counts"] = _json_loads_default(
        item.get("source_counts", "{}"), {}
    )
    item["status"] = _ai_summary_status(item.get("status", "empty"))
    for key in ("generated_at", "updated_at"):
        try:
            item[key] = float(item.get(key, 0) or 0)
        except (TypeError, ValueError):
            item[key] = 0.0
    return item
def _nonnegative_int(value, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default or 0)
    return max(0, parsed)


class AIPersistenceMixin:
    """Persist AI configuration metadata, metrics, summaries, and indexes."""

    def save_ai_provider_secret(self, provider: str, api_key: str) -> dict:
        """Store one raw provider API key and return masked metadata only."""
        provider = _normalize_ai_secret_provider(provider)
        api_key = str(api_key or "")
        if not api_key:
            raise ValueError("api_key must not be blank")
        now = time.time()
        existing = self._conn.execute(
            "SELECT created_at FROM ai_provider_secrets WHERE provider=?",
            (provider,),
        ).fetchone()
        created_at = float(existing[0]) if existing else now
        self._conn.execute(
            """
            INSERT INTO ai_provider_secrets
                (provider, api_key, created_at, updated_at)
            VALUES (?,?,?,?)
            ON CONFLICT(provider) DO UPDATE SET
                api_key=excluded.api_key,
                updated_at=excluded.updated_at
            """,
            (provider, api_key, created_at, now),
        )
        self._conn.commit()
        return self.get_ai_provider_secret_metadata(provider)

    def read_ai_provider_secret(self, provider: str) -> str:
        """Return one raw provider key for future AI call sites only."""
        provider = _normalize_ai_secret_provider(provider)
        row = self._conn.execute(
            "SELECT api_key FROM ai_provider_secrets WHERE provider=?",
            (provider,),
        ).fetchone()
        return str(row[0] or "") if row else ""

    def clear_ai_provider_secret(self, provider: str) -> None:
        provider = _normalize_ai_secret_provider(provider)
        self._conn.execute(
            "DELETE FROM ai_provider_secrets WHERE provider=?",
            (provider,),
        )
        self._conn.commit()

    def get_ai_provider_secret_metadata(self, provider: str) -> dict:
        """Return redacted provider key metadata safe for UI/snapshots."""
        provider = _normalize_ai_secret_provider(provider)
        row = self._conn.execute(
            "SELECT api_key, updated_at FROM ai_provider_secrets "
            "WHERE provider=?",
            (provider,),
        ).fetchone()
        if not row or not str(row[0] or ""):
            return {"configured": False, "last4": "", "updated_at": 0}
        key = str(row[0] or "")
        return {
            "configured": True,
            "last4": key[-4:],
            "updated_at": float(row[1] or 0),
        }

    def record_ai_call_metric(
        self,
        *,
        purpose: str,
        provider: str,
        model: str,
        status: str,
        failure_kind: str = "",
        latency_ms: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
        metric_id: str | None = None,
        created_at: float | None = None,
    ) -> dict:
        """Persist non-sensitive AI call metadata only.

        Prompts, response text, and raw provider keys are intentionally not
        accepted by this helper or represented in ``ai_call_metrics``.
        """
        metric = {
            "id": str(metric_id or f"ai-call-{uuid.uuid4().hex}"),
            "created_at": float(time.time() if created_at is None else created_at),
            "purpose": str(purpose or ""),
            "provider": str(provider or ""),
            "model": str(model or ""),
            "status": str(status or ""),
            "failure_kind": str(failure_kind or ""),
            "latency_ms": _nonnegative_int(latency_ms),
            "input_tokens": _nonnegative_int(input_tokens),
            "output_tokens": _nonnegative_int(output_tokens),
            "cache_creation_input_tokens": _nonnegative_int(
                cache_creation_input_tokens
            ),
            "cache_read_input_tokens": _nonnegative_int(
                cache_read_input_tokens
            ),
        }
        self._conn.execute(
            """
            INSERT INTO ai_call_metrics
                (id, created_at, purpose, provider, model, status,
                 failure_kind, latency_ms, input_tokens, output_tokens,
                 cache_creation_input_tokens, cache_read_input_tokens)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                metric["id"],
                metric["created_at"],
                metric["purpose"],
                metric["provider"],
                metric["model"],
                metric["status"],
                metric["failure_kind"],
                metric["latency_ms"],
                metric["input_tokens"],
                metric["output_tokens"],
                metric["cache_creation_input_tokens"],
                metric["cache_read_input_tokens"],
            ),
        )
        self._conn.commit()
        return metric

    def list_ai_call_metrics(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """Return recent AI call metrics without prompts, keys, or text."""
        limit = max(0, min(_nonnegative_int(limit), 1000))
        offset = _nonnegative_int(offset)
        rows = self._conn.execute(
            """
            SELECT id, created_at, purpose, provider, model, status,
                   failure_kind, latency_ms, input_tokens, output_tokens,
                   cache_creation_input_tokens, cache_read_input_tokens
            FROM ai_call_metrics
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        cols = [
            "id",
            "created_at",
            "purpose",
            "provider",
            "model",
            "status",
            "failure_kind",
            "latency_ms",
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ]
        return [dict(zip(cols, row)) for row in rows]

    # -- AI summary cache helpers ------------------------------------------

    def ai_load_summary(
        self,
        summary_key: str,
        conn: sqlite3.Connection | None = None,
    ) -> dict | None:
        """Load one cached AI summary row."""

        summary_key = str(summary_key or "").strip()
        if not summary_key:
            return None
        executor = conn or self._conn
        cursor = executor.execute(
            "SELECT summary_key, summary_type, scope_kind, scope_ref, "
            "provider, model, prompt_version, source_hash, source_counts, "
            "summary_text, status, generated_at, updated_at, error "
            "FROM ai_summaries WHERE summary_key=?",
            (summary_key,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _ai_summary_dict(row, [d[0] for d in cursor.description])

    def ai_upsert_summary(
        self,
        row_dict: dict,
        conn: sqlite3.Connection | None = None,
        *,
        commit: bool = True,
    ) -> dict:
        """Upsert one cached AI summary row and return the normalized row."""

        row = dict(row_dict or {})
        summary_key = str(row.get("summary_key", "") or "").strip()
        if not summary_key:
            raise ValueError("summary_key is required")
        existing = self.ai_load_summary(summary_key, conn=conn) or {}
        now = float(row.get("updated_at", 0) or time.time())
        source_counts = row.get("source_counts", existing.get("source_counts", {}))
        if not isinstance(source_counts, dict):
            source_counts = _json_loads_default(source_counts, {})
        generated_at = float(
            row.get("generated_at", existing.get("generated_at", 0)) or 0
        )
        status = _ai_summary_status(row.get("status", existing.get("status", "empty")))
        executor = conn or self._conn
        executor.execute(
            """
            INSERT INTO ai_summaries
                (summary_key, summary_type, scope_kind, scope_ref, provider,
                 model, prompt_version, source_hash, source_counts,
                 summary_text, status, generated_at, updated_at, error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(summary_key) DO UPDATE SET
                summary_type=excluded.summary_type,
                scope_kind=excluded.scope_kind,
                scope_ref=excluded.scope_ref,
                provider=excluded.provider,
                model=excluded.model,
                prompt_version=excluded.prompt_version,
                source_hash=excluded.source_hash,
                source_counts=excluded.source_counts,
                summary_text=excluded.summary_text,
                status=excluded.status,
                generated_at=excluded.generated_at,
                updated_at=excluded.updated_at,
                error=excluded.error
            """,
            (
                summary_key,
                str(row.get("summary_type", existing.get("summary_type", "")) or ""),
                str(row.get("scope_kind", existing.get("scope_kind", "")) or ""),
                str(row.get("scope_ref", existing.get("scope_ref", "")) or ""),
                str(row.get("provider", existing.get("provider", "")) or ""),
                str(row.get("model", existing.get("model", "")) or ""),
                str(row.get("prompt_version", existing.get("prompt_version", "")) or ""),
                str(row.get("source_hash", existing.get("source_hash", "")) or ""),
                _json_dumps_stable(source_counts),
                str(row.get("summary_text", existing.get("summary_text", "")) or ""),
                status,
                generated_at,
                now,
                str(row.get("error", existing.get("error", "")) or ""),
            ),
        )
        if commit and conn is None:
            executor.commit()
        saved = self.ai_load_summary(summary_key, conn=executor)
        if not saved:
            raise RuntimeError(f"failed to load saved AI summary {summary_key}")
        return saved

    def ai_delete_summary(
        self,
        summary_key: str,
        conn: sqlite3.Connection | None = None,
        *,
        commit: bool = True,
    ) -> None:
        summary_key = str(summary_key or "").strip()
        if not summary_key:
            return
        executor = conn or self._conn
        executor.execute("DELETE FROM ai_summaries WHERE summary_key=?", (summary_key,))
        if commit and conn is None:
            executor.commit()

    def ai_list_summaries(
        self,
        statuses: list[str] | tuple[str, ...] | set[str] | None = None,
        *,
        summary_type: str = "",
        limit: int = 100,
        conn: sqlite3.Connection | None = None,
    ) -> list[dict]:
        executor = conn or self._conn
        filters = []
        params: list[object] = []
        valid_statuses = [
            _ai_summary_status(status)
            for status in (statuses or [])
            if str(status or "").strip()
        ]
        if valid_statuses:
            filters.append(
                "status IN (" + ",".join(["?"] * len(valid_statuses)) + ")"
            )
            params.extend(valid_statuses)
        summary_type = str(summary_type or "").strip()
        if summary_type:
            filters.append("summary_type=?")
            params.append(summary_type)
        params.append(max(0, min(_nonnegative_int(limit), 1000)))
        where = (" WHERE " + " AND ".join(filters)) if filters else ""
        cursor = executor.execute(
            "SELECT summary_key, summary_type, scope_kind, scope_ref, "
            "provider, model, prompt_version, source_hash, source_counts, "
            "summary_text, status, generated_at, updated_at, error "
            "FROM ai_summaries"
            + where
            + " ORDER BY updated_at DESC, summary_key ASC LIMIT ?",
            tuple(params),
        )
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [_ai_summary_dict(row, cols) for row in rows]

    def ai_get_summary_status_payload(self) -> dict:
        rows = self._conn.execute(
            "SELECT status, COUNT(*), MAX(generated_at), MAX(updated_at) "
            "FROM ai_summaries GROUP BY status"
        ).fetchall()
        by_status = {
            _ai_summary_status(row[0]): {
                "count": int(row[1] or 0),
                "last_generated_at": float(row[2] or 0),
                "last_updated_at": float(row[3] or 0),
            }
            for row in rows
        }
        error_row = self._conn.execute(
            "SELECT error FROM ai_summaries WHERE error<>'' "
            "ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        last_refreshed_at = max(
            [
                float(item.get("last_generated_at", 0) or 0)
                for item in by_status.values()
            ]
            or [0.0]
        )
        return {
            "counts": {
                "ready": int(by_status.get("ready", {}).get("count", 0) or 0),
                "stale": int(by_status.get("stale", {}).get("count", 0) or 0),
                "refreshing": int(
                    by_status.get("refreshing", {}).get("count", 0) or 0
                ),
                "empty": int(by_status.get("empty", {}).get("count", 0) or 0),
                "errors": int(by_status.get("error", {}).get("count", 0) or 0),
            },
            "last_refreshed_at": last_refreshed_at,
            "last_error": str(error_row[0] or "") if error_row else "",
        }

    # -- AI vector index helpers -------------------------------------------

    def open_ai_index_connection(self) -> sqlite3.Connection:
        """Open a fresh SQLite connection for AI index/vector work.

        Background index jobs may run in worker threads and must never share
        the daemon's main TorqueDB connection.  The caller is responsible for
        loading sqlite-vec on this connection before touching ai_embedding_vec.
        """

        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def ai_get_index_state(
        self,
        conn: sqlite3.Connection | None = None,
    ) -> dict:
        executor = conn or self._conn
        row = executor.execute(
            "SELECT id, desired_model_id, active_model_id, active_dims, "
            "status, rebuild_required, rebuild_reason, corpus_config, "
            "last_scan_at, last_built_at, last_error "
            "FROM ai_index_state WHERE id=?",
            (AI_INDEX_STATE_ID,),
        ).fetchone()
        if row:
            return _ai_index_state_dict(row)
        return _ai_index_state_dict(None)

    def ai_update_index_state(
        self,
        conn: sqlite3.Connection | None = None,
        *,
        commit: bool = True,
        **fields,
    ) -> dict:
        executor = conn or self._conn
        current = self.ai_get_index_state(executor)
        row = {
            "id": AI_INDEX_STATE_ID,
            "desired_model_id": str(
                fields.get(
                    "desired_model_id",
                    current.get("desired_model_id", "BAAI/bge-m3"),
                )
                or "BAAI/bge-m3"
            ),
            "active_model_id": str(
                fields.get("active_model_id", current.get("active_model_id", ""))
                or ""
            ),
            "active_dims": _nonnegative_int(
                fields.get("active_dims", current.get("active_dims", 0))
            ),
            "status": str(fields.get("status", current.get("status", "not_built")) or "not_built"),
            "rebuild_required": 1
            if bool(fields.get("rebuild_required", current.get("rebuild_required", False)))
            else 0,
            "rebuild_reason": str(
                fields.get("rebuild_reason", current.get("rebuild_reason", ""))
                or ""
            ),
            "corpus_config": _json_dumps_stable(
                fields.get("corpus_config", current.get("corpus_config", {}))
                if isinstance(
                    fields.get("corpus_config", current.get("corpus_config", {})),
                    dict,
                )
                else _json_loads_default(
                    fields.get("corpus_config", current.get("corpus_config", {})),
                    {},
                )
            ),
            "last_scan_at": float(
                fields.get("last_scan_at", current.get("last_scan_at", 0)) or 0
            ),
            "last_built_at": float(
                fields.get("last_built_at", current.get("last_built_at", 0)) or 0
            ),
            "last_error": str(
                fields.get("last_error", current.get("last_error", "")) or ""
            ),
        }
        executor.execute(
            """
            INSERT INTO ai_index_state
                (id, desired_model_id, active_model_id, active_dims, status,
                 rebuild_required, rebuild_reason, corpus_config,
                 last_scan_at, last_built_at, last_error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                desired_model_id=excluded.desired_model_id,
                active_model_id=excluded.active_model_id,
                active_dims=excluded.active_dims,
                status=excluded.status,
                rebuild_required=excluded.rebuild_required,
                rebuild_reason=excluded.rebuild_reason,
                corpus_config=excluded.corpus_config,
                last_scan_at=excluded.last_scan_at,
                last_built_at=excluded.last_built_at,
                last_error=excluded.last_error
            """,
            (
                row["id"],
                row["desired_model_id"],
                row["active_model_id"],
                row["active_dims"],
                row["status"],
                row["rebuild_required"],
                row["rebuild_reason"],
                row["corpus_config"],
                row["last_scan_at"],
                row["last_built_at"],
                row["last_error"],
            ),
        )
        if commit and conn is None:
            executor.commit()
        return self.ai_get_index_state(executor)

    def ai_index_vector_table_exists(
        self,
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        executor = conn or self._conn
        row = executor.execute(
            "SELECT 1 FROM sqlite_master WHERE name='ai_embedding_vec' "
            "AND type IN ('table','virtual table') LIMIT 1"
        ).fetchone()
        return bool(row)

    def ai_create_embedding_vec_table(
        self,
        conn: sqlite3.Connection,
        dims: int,
        *,
        recreate: bool = False,
    ) -> None:
        create_ai_embedding_vec_table(conn, dims, recreate=recreate)

    def ai_drop_embedding_vec_table(self, conn: sqlite3.Connection) -> None:
        drop_ai_embedding_vec_table(conn)

    def ai_get_index_counts(
        self,
        conn: sqlite3.Connection | None = None,
    ) -> dict:
        executor = conn or self._conn
        source_rows = executor.execute(
            "SELECT state, COUNT(*) FROM ai_embedding_sources GROUP BY state"
        ).fetchall()
        by_state = {
            str(state or ""): int(count or 0)
            for state, count in source_rows
        }
        chunk_count = int(
            executor.execute("SELECT COUNT(*) FROM ai_embedding_chunks").fetchone()[0]
            or 0
        )
        vector_count = 0
        if self.ai_index_vector_table_exists(executor):
            try:
                vector_count = int(
                    executor.execute("SELECT COUNT(*) FROM ai_embedding_vec").fetchone()[0]
                    or 0
                )
            except sqlite3.Error:
                vector_count = 0
        return {
            "sources": sum(by_state.values()),
            "chunks": chunk_count,
            "vectors": vector_count,
            "indexed": by_state.get("indexed", 0),
            "pending": by_state.get("pending", 0),
            "stale": by_state.get("stale", 0),
            "deleted": by_state.get("deleted", 0),
            "errors": by_state.get("error", 0),
            "by_state": by_state,
        }

    def ai_get_index_status_payload(self) -> dict:
        state = self.ai_get_index_state()
        counts = self.ai_get_index_counts()
        job = self.ai_get_current_index_job()
        indexed_rows = int(counts.get("chunks", 0) or 0)
        desired = str(state.get("desired_model_id", "") or "")
        active = str(state.get("active_model_id", "") or "")
        rebuild_required = bool(state.get("rebuild_required"))
        return {
            "state": state,
            "counts": counts,
            "current_job": job,
            "rebuild_warning": {
                "required": bool(
                    rebuild_required
                    or (indexed_rows > 0 and desired and active and desired != active)
                ),
                "reason": (
                    str(state.get("rebuild_reason", "") or "")
                    or ("embedding_model_change" if desired and active and desired != active else "")
                ),
                "estimated_entries": indexed_rows,
            },
        }

    def ai_load_embedding_source(
        self,
        source_key: str,
        conn: sqlite3.Connection | None = None,
    ) -> dict | None:
        executor = conn or self._conn
        row = executor.execute(
            "SELECT source_key, source_type, source_id, source_sub_id, "
            "group_name, owner_kind, owner_id, participant_ids, "
            "participant_kinds, visibility_json, title, source_updated_at, "
            "content_hash, indexed_content_hash, state, discovered_at, "
            "last_seen_at, indexed_at, error "
            "FROM ai_embedding_sources WHERE source_key=?",
            (str(source_key or ""),),
        ).fetchone()
        return _ai_source_dict(row)

    def ai_list_embedding_sources(
        self,
        states: list[str] | tuple[str, ...] | set[str] | None = None,
        *,
        conn: sqlite3.Connection | None = None,
        limit: int = 0,
    ) -> list[dict]:
        executor = conn or self._conn
        params: list = []
        sql = (
            "SELECT source_key, source_type, source_id, source_sub_id, "
            "group_name, owner_kind, owner_id, participant_ids, "
            "participant_kinds, visibility_json, title, source_updated_at, "
            "content_hash, indexed_content_hash, state, discovered_at, "
            "last_seen_at, indexed_at, error FROM ai_embedding_sources"
        )
        if states:
            normalized = [str(state or "").strip() for state in states if str(state or "").strip()]
            if normalized:
                sql += " WHERE state IN (" + ",".join(["?"] * len(normalized)) + ")"
                params.extend(normalized)
        sql += " ORDER BY source_type, source_key"
        if limit:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))
        rows = executor.execute(sql, tuple(params)).fetchall()
        return [item for row in rows if (item := _ai_source_dict(row))]

    def ai_upsert_embedding_source(
        self,
        source: dict,
        *,
        conn: sqlite3.Connection | None = None,
        now: float | None = None,
        commit: bool = True,
    ) -> dict:
        executor = conn or self._conn
        item = dict(source or {})
        source_key = str(item.get("source_key", "") or "").strip()
        if not source_key:
            raise ValueError("source_key is required")
        source_type = _ai_index_source_type(item.get("source_type", ""))
        content_hash = str(item.get("content_hash", "") or "").strip()
        if not content_hash:
            raise ValueError("content_hash is required")
        now_value = float(time.time() if now is None else now)
        existing = self.ai_load_embedding_source(source_key, executor)
        state = _ai_source_state_for_hash(existing, content_hash)
        discovered_at = (
            float(existing.get("discovered_at", 0) or 0)
            if existing else now_value
        )
        participant_ids = list(item.get("participant_ids", []) or [])
        participant_kinds = dict(item.get("participant_kinds", {}) or {})
        visibility_json = dict(item.get("visibility_json", {}) or {})
        executor.execute(
            """
            INSERT INTO ai_embedding_sources
                (source_key, source_type, source_id, source_sub_id,
                 group_name, owner_kind, owner_id, participant_ids,
                 participant_kinds, visibility_json, title, source_updated_at,
                 content_hash, indexed_content_hash, state, discovered_at,
                 last_seen_at, indexed_at, error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_key) DO UPDATE SET
                source_type=excluded.source_type,
                source_id=excluded.source_id,
                source_sub_id=excluded.source_sub_id,
                group_name=excluded.group_name,
                owner_kind=excluded.owner_kind,
                owner_id=excluded.owner_id,
                participant_ids=excluded.participant_ids,
                participant_kinds=excluded.participant_kinds,
                visibility_json=excluded.visibility_json,
                title=excluded.title,
                source_updated_at=excluded.source_updated_at,
                content_hash=excluded.content_hash,
                state=excluded.state,
                last_seen_at=excluded.last_seen_at,
                error=''
            """,
            (
                source_key,
                source_type,
                str(item.get("source_id", "") or ""),
                str(item.get("source_sub_id", "") or ""),
                str(item.get("group_name", "") or ""),
                str(item.get("owner_kind", "") or ""),
                str(item.get("owner_id", "") or ""),
                _json_dumps_stable(participant_ids),
                _json_dumps_stable(participant_kinds),
                _json_dumps_stable(visibility_json),
                str(item.get("title", "") or ""),
                str(item.get("source_updated_at", "") or ""),
                content_hash,
                str(existing.get("indexed_content_hash", "") or "") if existing else "",
                state,
                discovered_at,
                now_value,
                float(existing.get("indexed_at", 0) or 0) if existing else 0.0,
                "",
            ),
        )
        if commit and conn is None:
            executor.commit()
        saved = self.ai_load_embedding_source(source_key, executor)
        if not saved:
            raise RuntimeError(f"failed to load AI source {source_key}")
        return saved

    def ai_mark_sources_deleted_not_seen(
        self,
        seen_source_keys: set[str],
        *,
        conn: sqlite3.Connection | None = None,
        now: float | None = None,
        commit: bool = True,
    ) -> list[str]:
        executor = conn or self._conn
        seen = {str(key or "").strip() for key in (seen_source_keys or set()) if str(key or "").strip()}
        rows = executor.execute(
            "SELECT source_key FROM ai_embedding_sources WHERE state!='deleted'"
        ).fetchall()
        missing = [
            str(row[0] or "")
            for row in rows
            if str(row[0] or "") not in seen
        ]
        if not missing:
            return []
        now_value = float(time.time() if now is None else now)
        for source_key in missing:
            self.ai_delete_chunks_for_source(
                source_key,
                conn=executor,
                commit=False,
            )
            executor.execute(
                "UPDATE ai_embedding_sources SET state='deleted', "
                "last_seen_at=?, error='' WHERE source_key=?",
                (now_value, source_key),
            )
        if commit and conn is None:
            executor.commit()
        return missing

    def ai_delete_chunks_for_source(
        self,
        source_key: str,
        *,
        conn: sqlite3.Connection | None = None,
        commit: bool = True,
    ) -> int:
        executor = conn or self._conn
        key = str(source_key or "").strip()
        if not key:
            return 0
        rows = executor.execute(
            "SELECT id FROM ai_embedding_chunks WHERE source_key=?",
            (key,),
        ).fetchall()
        chunk_ids = [int(row[0]) for row in rows]
        if chunk_ids and self.ai_index_vector_table_exists(executor):
            for chunk_id in chunk_ids:
                executor.execute(
                    "DELETE FROM ai_embedding_vec WHERE rowid=?",
                    (chunk_id,),
                )
        executor.execute(
            "DELETE FROM ai_embedding_chunks WHERE source_key=?",
            (key,),
        )
        if commit and conn is None:
            executor.commit()
        return len(chunk_ids)

    def ai_clear_all_chunks_and_vectors(
        self,
        *,
        conn: sqlite3.Connection | None = None,
        commit: bool = True,
    ) -> None:
        executor = conn or self._conn
        if self.ai_index_vector_table_exists(executor):
            executor.execute("DELETE FROM ai_embedding_vec")
        executor.execute("DELETE FROM ai_embedding_chunks")
        executor.execute(
            "UPDATE ai_embedding_sources SET state='pending', "
            "indexed_content_hash='', indexed_at=0, error='' "
            "WHERE state!='deleted'"
        )
        if commit and conn is None:
            executor.commit()

    def ai_replace_source_chunks(
        self,
        source_key: str,
        chunks: list[dict],
        *,
        conn: sqlite3.Connection,
        model_id: str,
        dims: int,
        content_hash: str,
        indexed_at: float | None = None,
    ) -> int:
        """Replace one source's chunks and vector rows in a transaction.

        The caller must pass a fresh connection and must have already loaded
        sqlite-vec if ai_embedding_vec is a virtual table.  The invariant is
        preserved by inserting vec rows with rowid == ai_embedding_chunks.id.
        """

        key = str(source_key or "").strip()
        if not key:
            raise ValueError("source_key is required")
        model = str(model_id or "").strip()
        dims_value = _nonnegative_int(dims)
        if not model or dims_value <= 0:
            raise ValueError("model_id and dims are required")
        indexed_at = float(time.time() if indexed_at is None else indexed_at)
        self.ai_delete_chunks_for_source(key, conn=conn, commit=False)
        for item in chunks:
            chunk_index = _nonnegative_int(item.get("chunk_index", 0))
            text = str(item.get("text", "") or "")
            chunk_hash = str(item.get("chunk_hash", "") or "").strip()
            vector = list(item.get("vector", []) or [])
            if len(vector) != dims_value:
                raise ValueError("vector dimension does not match active dims")
            cursor = conn.execute(
                """
                INSERT INTO ai_embedding_chunks
                    (source_key, chunk_index, text, chunk_hash,
                     embedding_model_id, embedding_dims, indexed_at,
                     distance_metric, error)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    key,
                    chunk_index,
                    text,
                    chunk_hash,
                    model,
                    dims_value,
                    indexed_at,
                    "cosine",
                    "",
                ),
            )
            chunk_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO ai_embedding_vec(rowid, embedding) VALUES (?, ?)",
                (
                    chunk_id,
                    json.dumps(
                        [float(value) for value in vector],
                        separators=(",", ":"),
                    ),
                ),
            )
        conn.execute(
            "UPDATE ai_embedding_sources SET state='indexed', "
            "indexed_content_hash=?, indexed_at=?, error='' "
            "WHERE source_key=?",
            (str(content_hash or ""), indexed_at, key),
        )
        return len(chunks)

    def ai_mark_source_error(
        self,
        source_key: str,
        error: str,
        *,
        conn: sqlite3.Connection | None = None,
        commit: bool = True,
    ) -> None:
        executor = conn or self._conn
        executor.execute(
            "UPDATE ai_embedding_sources SET state='error', error=? "
            "WHERE source_key=?",
            (str(error or "")[:1000], str(source_key or "")),
        )
        if commit and conn is None:
            executor.commit()

    def ai_create_index_job(
        self,
        *,
        mode: str = "incremental",
        reason: str = "",
        job_id: str | None = None,
        status: str = "queued",
        totals: dict | None = None,
        conn: sqlite3.Connection | None = None,
        commit: bool = True,
    ) -> dict:
        executor = conn or self._conn
        mode = str(mode or "incremental").strip()
        if mode not in {"incremental", "rebuild"}:
            raise ValueError("mode must be incremental or rebuild")
        status = str(status or "queued").strip() or "queued"
        now = time.time()
        jid = str(job_id or f"ai-index-{uuid.uuid4().hex}").strip()
        executor.execute(
            """
            INSERT INTO ai_index_jobs
                (id, mode, status, reason, started_at, updated_at,
                 completed_at, totals, error)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                jid,
                mode,
                status,
                str(reason or ""),
                now if status == "running" else 0,
                now,
                now if status in {"complete", "error", "cancelled"} else 0,
                _json_dumps_stable(totals or {}),
                "",
            ),
        )
        if commit and conn is None:
            executor.commit()
        job = self.ai_get_index_job(jid, conn=executor)
        if not job:
            raise RuntimeError(f"failed to create AI index job {jid}")
        return job

    def ai_get_index_job(
        self,
        job_id: str,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> dict | None:
        executor = conn or self._conn
        cursor = executor.execute(
            "SELECT id, mode, status, reason, started_at, updated_at, "
            "completed_at, totals, error FROM ai_index_jobs WHERE id=?",
            (str(job_id or ""),),
        )
        row = cursor.fetchone()
        return _ai_job_dict(row, [d[0] for d in cursor.description])

    def ai_get_current_index_job(
        self,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> dict | None:
        executor = conn or self._conn
        cursor = executor.execute(
            "SELECT id, mode, status, reason, started_at, updated_at, "
            "completed_at, totals, error FROM ai_index_jobs "
            "WHERE status IN ('queued','running') "
            "ORDER BY updated_at DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return _ai_job_dict(row, [d[0] for d in cursor.description])

    def ai_update_index_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        totals: dict | None = None,
        error: str | None = None,
        started_at: float | None = None,
        completed_at: float | None = None,
        conn: sqlite3.Connection | None = None,
        commit: bool = True,
    ) -> dict | None:
        executor = conn or self._conn
        current = self.ai_get_index_job(job_id, conn=executor)
        if not current:
            return None
        now = time.time()
        next_status = str(status or current.get("status", "queued") or "queued")
        next_started = (
            float(started_at)
            if started_at is not None
            else (
                float(current.get("started_at", 0) or 0)
                or (now if next_status == "running" else 0)
            )
        )
        next_completed = (
            float(completed_at)
            if completed_at is not None
            else (
                now
                if next_status in {"complete", "error", "cancelled"}
                and not float(current.get("completed_at", 0) or 0)
                else float(current.get("completed_at", 0) or 0)
            )
        )
        executor.execute(
            "UPDATE ai_index_jobs SET status=?, started_at=?, updated_at=?, "
            "completed_at=?, totals=?, error=? WHERE id=?",
            (
                next_status,
                next_started,
                now,
                next_completed,
                _json_dumps_stable(totals if totals is not None else current.get("totals", {})),
                str(error if error is not None else current.get("error", "") or "")[:1000],
                str(job_id or ""),
            ),
        )
        if commit and conn is None:
            executor.commit()
        return self.ai_get_index_job(job_id, conn=executor)
