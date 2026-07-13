"""Schema DDL and versioned migration helpers for :class:`TorqueDB`.

The original Torque schema evolved through an additive, idempotent bootstrap
routine.  That routine remains migration ``1`` so existing installations can
be adopted safely.  Migration ``2`` introduces the durable migration ledger;
all subsequent schema changes should be expressed as ordered migrations rather
than appended to the legacy reconciliation function.  The legacy routine still
runs as a compatibility repair pass while historical partial-schema fixtures
remain supported; it no longer owns the current schema version.
"""

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Callable, Iterable

SCHEMA_VERSION = "13"


@dataclass(frozen=True)
class SchemaMigration:
    """One ordered database migration with a stable audit checksum."""

    version: int
    name: str
    signature: str
    apply: Callable[[sqlite3.Connection, Callable], None]
    phase: str = "schema"
    requires_runner: bool = False
    repair_on_boot: bool = False

    @property
    def checksum(self) -> str:
        payload = f"{self.version}:{self.name}:{self.signature}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


AI_INDEX_STATE_COLUMNS = {
    "id": "TEXT PRIMARY KEY DEFAULT 'default'",
    "desired_model_id": "TEXT NOT NULL DEFAULT 'BAAI/bge-m3'",
    "active_model_id": "TEXT NOT NULL DEFAULT ''",
    "active_dims": "INTEGER NOT NULL DEFAULT 0",
    "status": "TEXT NOT NULL DEFAULT 'not_built'",
    "rebuild_required": "INTEGER NOT NULL DEFAULT 0",
    "rebuild_reason": "TEXT NOT NULL DEFAULT ''",
    "corpus_config": "TEXT NOT NULL DEFAULT '{}'",
    "last_scan_at": "REAL NOT NULL DEFAULT 0",
    "last_built_at": "REAL NOT NULL DEFAULT 0",
    "last_error": "TEXT NOT NULL DEFAULT ''",
}

AI_EMBEDDING_SOURCE_COLUMNS = {
    "source_key": "TEXT PRIMARY KEY",
    "source_type": "TEXT NOT NULL DEFAULT ''",
    "source_id": "TEXT NOT NULL DEFAULT ''",
    "source_sub_id": "TEXT NOT NULL DEFAULT ''",
    "group_name": "TEXT NOT NULL DEFAULT ''",
    "owner_kind": "TEXT NOT NULL DEFAULT ''",
    "owner_id": "TEXT NOT NULL DEFAULT ''",
    "participant_ids": "TEXT NOT NULL DEFAULT '[]'",
    "participant_kinds": "TEXT NOT NULL DEFAULT '{}'",
    "visibility_json": "TEXT NOT NULL DEFAULT '{}'",
    "title": "TEXT NOT NULL DEFAULT ''",
    "source_updated_at": "TEXT NOT NULL DEFAULT ''",
    "content_hash": "TEXT NOT NULL DEFAULT ''",
    "indexed_content_hash": "TEXT NOT NULL DEFAULT ''",
    "state": "TEXT NOT NULL DEFAULT 'pending'",
    "discovered_at": "REAL NOT NULL DEFAULT 0",
    "last_seen_at": "REAL NOT NULL DEFAULT 0",
    "indexed_at": "REAL NOT NULL DEFAULT 0",
    "error": "TEXT NOT NULL DEFAULT ''",
}

AI_EMBEDDING_CHUNK_COLUMNS = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "source_key": "TEXT NOT NULL DEFAULT ''",
    "chunk_index": "INTEGER NOT NULL DEFAULT 0",
    "text": "TEXT NOT NULL DEFAULT ''",
    "chunk_hash": "TEXT NOT NULL DEFAULT ''",
    "embedding_model_id": "TEXT NOT NULL DEFAULT ''",
    "embedding_dims": "INTEGER NOT NULL DEFAULT 0",
    "indexed_at": "REAL NOT NULL DEFAULT 0",
    "distance_metric": "TEXT NOT NULL DEFAULT 'cosine'",
    "error": "TEXT NOT NULL DEFAULT ''",
}

AI_INDEX_JOB_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "mode": "TEXT NOT NULL DEFAULT 'incremental'",
    "status": "TEXT NOT NULL DEFAULT 'queued'",
    "reason": "TEXT NOT NULL DEFAULT ''",
    "started_at": "REAL NOT NULL DEFAULT 0",
    "updated_at": "REAL NOT NULL DEFAULT 0",
    "completed_at": "REAL NOT NULL DEFAULT 0",
    "totals": "TEXT NOT NULL DEFAULT '{}'",
    "error": "TEXT NOT NULL DEFAULT ''",
}

AI_SUMMARY_COLUMNS = {
    "summary_key": "TEXT PRIMARY KEY",
    "summary_type": "TEXT NOT NULL DEFAULT ''",
    "scope_kind": "TEXT NOT NULL DEFAULT ''",
    "scope_ref": "TEXT NOT NULL DEFAULT ''",
    "provider": "TEXT NOT NULL DEFAULT ''",
    "model": "TEXT NOT NULL DEFAULT ''",
    "prompt_version": "TEXT NOT NULL DEFAULT ''",
    "source_hash": "TEXT NOT NULL DEFAULT ''",
    "source_counts": "TEXT NOT NULL DEFAULT '{}'",
    "summary_text": "TEXT NOT NULL DEFAULT ''",
    "status": "TEXT NOT NULL DEFAULT 'empty'",
    "generated_at": "REAL NOT NULL DEFAULT 0",
    "updated_at": "REAL NOT NULL DEFAULT 0",
    "error": "TEXT NOT NULL DEFAULT ''",
}


def create_ai_embedding_vec_table(
    conn: sqlite3.Connection,
    dims: int,
    *,
    recreate: bool = False,
) -> None:
    """Create the sqlite-vec table once an embedding dimension is known.

    sqlite-vec virtual tables have a fixed vector dimension, so base schema
    migrations must not create this table unconditionally.  AI index code calls
    this helper on a fresh connection after loading the sqlite-vec extension.
    """

    try:
        dims_value = int(dims)
    except (TypeError, ValueError):
        raise ValueError("embedding dims must be a positive integer") from None
    if dims_value <= 0:
        raise ValueError("embedding dims must be a positive integer")
    if recreate:
        conn.execute("DROP TABLE IF EXISTS ai_embedding_vec")
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS ai_embedding_vec "
        f"USING vec0(embedding float[{dims_value}])"
    )


def drop_ai_embedding_vec_table(conn: sqlite3.Connection) -> None:
    """Drop the dynamic sqlite-vec table if it exists."""

    conn.execute("DROP TABLE IF EXISTS ai_embedding_vec")


def _ensure_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: dict[str, str],
) -> None:
    existing = {
        str(row[1] or "")
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for name, definition in columns.items():
        if name in existing:
            continue
        # SQLite cannot add PRIMARY KEY columns to an existing table.  Existing
        # installs should only need additive non-key columns; new installs get
        # the primary keys from the CREATE TABLE DDL above.
        add_definition = str(definition)
        if "PRIMARY KEY" in add_definition.upper():
            continue
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {name} {add_definition}"
        )


def _ensure_table(
    conn: sqlite3.Connection,
    table: str,
    columns: dict[str, str],
) -> None:
    definitions = ", ".join(
        f"{name} {definition}" for name, definition in columns.items()
    )
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({definitions})")
    _ensure_columns(conn, table, columns)


def _ensure_ai_index_schema(conn: sqlite3.Connection) -> None:
    """Keep AI index tables additive/idempotent across partial migrations."""

    for table, columns in (
        ("ai_index_state", AI_INDEX_STATE_COLUMNS),
        ("ai_embedding_sources", AI_EMBEDDING_SOURCE_COLUMNS),
        ("ai_embedding_chunks", AI_EMBEDDING_CHUNK_COLUMNS),
        ("ai_index_jobs", AI_INDEX_JOB_COLUMNS),
    ):
        _ensure_columns(conn, table, columns)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_embedding_sources_state "
        "ON ai_embedding_sources(state)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_embedding_sources_scope "
        "ON ai_embedding_sources(group_name, source_type, owner_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_embedding_chunks_source "
        "ON ai_embedding_chunks(source_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_embedding_chunks_model "
        "ON ai_embedding_chunks(embedding_model_id, embedding_dims)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_index_jobs_status "
        "ON ai_index_jobs(status, updated_at DESC)"
    )


def _ensure_ai_summary_schema(conn: sqlite3.Connection) -> None:
    """Keep AI summary-cache tables additive/idempotent."""

    _ensure_columns(conn, "ai_summaries", AI_SUMMARY_COLUMNS)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_summaries_scope "
        "ON ai_summaries(summary_type, scope_kind, scope_ref)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_summaries_status "
        "ON ai_summaries(status, updated_at DESC)"
    )

MCP_IDEMPOTENCY_COLUMNS = {
    "idempotency_key": "TEXT PRIMARY KEY",
    "surface": "TEXT NOT NULL DEFAULT ''",
    "tool_name": "TEXT NOT NULL DEFAULT ''",
    "request_hash": "TEXT NOT NULL DEFAULT ''",
    "response_json": "TEXT NOT NULL DEFAULT '{}'",
    "response_bytes": "INTEGER NOT NULL DEFAULT 0",
    "expires_at": "REAL NOT NULL DEFAULT 0",
    "compacted_at": "REAL NOT NULL DEFAULT 0",
    "created_at": "REAL NOT NULL DEFAULT 0",
    "updated_at": "REAL NOT NULL DEFAULT 0",
}


BOARD_TASK_ROUTING_COLUMNS = {
    "assigned_engineer_id": "TEXT NOT NULL DEFAULT ''",
    "assigned_architect_id": "TEXT NOT NULL DEFAULT ''",
    "created_by_architect_id": "TEXT NOT NULL DEFAULT ''",
    "created_by_engineer_id": "TEXT NOT NULL DEFAULT ''",
    "suggested_action": "TEXT NOT NULL DEFAULT ''",
    "suggested_specialization": "TEXT NOT NULL DEFAULT ''",
}

AGENT_LIFECYCLE_COLUMNS = {
    "queue_empty_emitted": "INTEGER NOT NULL DEFAULT 1",
    "dismissed_at": "INTEGER NOT NULL DEFAULT 0",
    "deleted_at": "REAL NOT NULL DEFAULT 0",
    "permanent_delete_after": "REAL NOT NULL DEFAULT 0",
    "engineer_specializations": "TEXT NOT NULL DEFAULT '[]'",
    "agent_class_id": "TEXT NOT NULL DEFAULT ''",
    "agent_class_version": "TEXT NOT NULL DEFAULT ''",
    "agent_class_assigned_at": "REAL NOT NULL DEFAULT 0",
    "agent_class_assigned_by": "TEXT NOT NULL DEFAULT ''",
    "effective_agent_class_id": "TEXT NOT NULL DEFAULT ''",
    "effective_agent_class_version": "TEXT NOT NULL DEFAULT ''",
    "effective_agent_class_snapshot": "TEXT NOT NULL DEFAULT '{}'",
    "effective_agent_class_applied_at": "REAL NOT NULL DEFAULT 0",
}

AGENT_ACTIVITY_TIMESTAMP_COLUMNS = {
    "last_progress_at": "REAL NOT NULL DEFAULT 0",
    "last_heartbeat_at": "REAL NOT NULL DEFAULT 0",
    "last_activity_at": "REAL NOT NULL DEFAULT 0",
}

AGENT_KIND_COLUMNS = {
    "kind": "TEXT NOT NULL DEFAULT ''",
    "role": "TEXT NOT NULL DEFAULT ''",
    "owner_engineer_id": "TEXT NOT NULL DEFAULT ''",
    "hired_by_architect_id": "TEXT NOT NULL DEFAULT ''",
    "persistent": "INTEGER NOT NULL DEFAULT 0",
}

AGENT_ACTIVITY_TIMESTAMP_LEGACY_META_KEY = (
    "schema_agent_activity_timestamps_version"
)
KINDS_LEGACY_META_KEY = "schema_kinds_migration_version"
KINDS_LEGACY_COMPLETE_VERSION = 4


class _PostInitMigrationNotReady(RuntimeError):
    """Internal signal for a guarded migration that should retry later."""


def _legacy_kinds_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM meta WHERE key=?",
        (KINDS_LEGACY_META_KEY,),
    ).fetchone()
    try:
        return int((row or (0,))[0] or 0)
    except (TypeError, ValueError):
        return 0

AGENT_MESSAGE_LOOP_RUNTIME_COLUMNS = {
    "deferred_at": "REAL NOT NULL DEFAULT 0",
    "deferred_reason": "TEXT NOT NULL DEFAULT ''",
}

AGENT_CLASS_AUDIT_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "agent_id": "TEXT NOT NULL DEFAULT ''",
    "agent_name": "TEXT NOT NULL DEFAULT ''",
    "event": "TEXT NOT NULL DEFAULT ''",
    "actor_kind": "TEXT NOT NULL DEFAULT 'user'",
    "actor_id": "TEXT NOT NULL DEFAULT ''",
    "actor_label": "TEXT NOT NULL DEFAULT ''",
    "previous_class_id": "TEXT NOT NULL DEFAULT ''",
    "previous_class_version": "TEXT NOT NULL DEFAULT ''",
    "assigned_class_id": "TEXT NOT NULL DEFAULT ''",
    "assigned_class_version": "TEXT NOT NULL DEFAULT ''",
    "effective_class_id": "TEXT NOT NULL DEFAULT ''",
    "effective_class_version": "TEXT NOT NULL DEFAULT ''",
    "snapshot_hash": "TEXT NOT NULL DEFAULT ''",
    "snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
    "message": "TEXT NOT NULL DEFAULT ''",
    "created_at": "REAL NOT NULL DEFAULT 0",
}

DECISION_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "architect_id": "TEXT NOT NULL DEFAULT ''",
    "title": "TEXT NOT NULL DEFAULT ''",
    "rationale": "TEXT NOT NULL DEFAULT ''",
    "status": "TEXT NOT NULL DEFAULT 'proposed'",
    "supersedes": "TEXT DEFAULT NULL",
    "linked_task_ids": "TEXT NOT NULL DEFAULT '[]'",
    "linked_engineer_ids": "TEXT NOT NULL DEFAULT '[]'",
    "archived": "INTEGER NOT NULL DEFAULT 0",
    "created_at": "INTEGER NOT NULL DEFAULT 0",
    "updated_at": "INTEGER NOT NULL DEFAULT 0",
    "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
}

PENDING_HIRE_LIFECYCLE_COLUMNS = {
    "requested_specializations": "TEXT NOT NULL DEFAULT '[]'",
}

AGENT_PEER_MESSAGE_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "thread_id": "TEXT NOT NULL DEFAULT ''",
    "reply_to_id": "TEXT NOT NULL DEFAULT ''",
    "group_name": "TEXT NOT NULL DEFAULT ''",
    "sender_id": "TEXT NOT NULL DEFAULT ''",
    "sender_kind": "TEXT NOT NULL DEFAULT 'architect'",
    "sender_name": "TEXT NOT NULL DEFAULT ''",
    "recipient_id": "TEXT NOT NULL DEFAULT ''",
    "recipient_kind": "TEXT NOT NULL DEFAULT 'architect'",
    "recipient_name": "TEXT NOT NULL DEFAULT ''",
    "message": "TEXT NOT NULL DEFAULT ''",
    "message_type": "TEXT NOT NULL DEFAULT 'message'",
    "created_at": "REAL NOT NULL DEFAULT 0",
    "ack_required": "INTEGER NOT NULL DEFAULT 0",
    "blocking": "INTEGER NOT NULL DEFAULT 0",
    "source_task_id": "TEXT NOT NULL DEFAULT ''",
    "context_task_ids": "TEXT NOT NULL DEFAULT '[]'",
    "context_engineer_ids": "TEXT NOT NULL DEFAULT '[]'",
    "context_decision_ids": "TEXT NOT NULL DEFAULT '[]'",
    "context_summary": "TEXT NOT NULL DEFAULT ''",
    "context_snapshot": "TEXT NOT NULL DEFAULT '{}'",
    "delivery_state": "TEXT NOT NULL DEFAULT 'buffered'",
    "delivery_reason": "TEXT NOT NULL DEFAULT ''",
    "delivered_at": "REAL NOT NULL DEFAULT 0",
    "read_at": "REAL NOT NULL DEFAULT 0",
    "archived_at": "REAL NOT NULL DEFAULT 0",
    "idempotency_key": "TEXT NOT NULL DEFAULT ''",
}

ENGINEER_JOURNAL_PROVENANCE_COLUMNS = {
    "author_cell_id": "TEXT NOT NULL DEFAULT ''",
    "source_key": "TEXT NOT NULL DEFAULT ''",
}

SLUGGED_ENTITY_TABLES = ("agents", "groups", "board_tasks")

GROUP_PROVIDER_RUNTIME_COLUMNS = {
    "agent_terminal_profile": "TEXT NOT NULL DEFAULT ''",
    "agent_provider": "TEXT NOT NULL DEFAULT ''",
    "agent_boot_command": "TEXT NOT NULL DEFAULT ''",
    "agent_model": "TEXT NOT NULL DEFAULT ''",
    "agent_reasoning_effort": "TEXT NOT NULL DEFAULT ''",
    "worker_provider": "TEXT NOT NULL DEFAULT ''",
    "worker_boot_command": "TEXT NOT NULL DEFAULT ''",
    "worker_model": "TEXT NOT NULL DEFAULT ''",
    "worker_reasoning_effort": "TEXT NOT NULL DEFAULT ''",
}


def _ensure_mcp_idempotency_schema(conn: sqlite3.Connection) -> None:
    """Keep idempotency retention metadata additive/idempotent."""

    _ensure_columns(conn, "mcp_idempotency", MCP_IDEMPOTENCY_COLUMNS)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mcp_idempotency_tool "
        "ON mcp_idempotency(surface, tool_name, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mcp_idempotency_retention "
        "ON mcp_idempotency(compacted_at, updated_at)"
    )

INITIATIVE_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "slug": "TEXT NOT NULL DEFAULT ''",
    "group_name": "TEXT NOT NULL DEFAULT ''",
    "title": "TEXT NOT NULL DEFAULT ''",
    "summary": "TEXT NOT NULL DEFAULT ''",
    "why": "TEXT NOT NULL DEFAULT ''",
    "in_scope": "TEXT NOT NULL DEFAULT ''",
    "out_of_scope": "TEXT NOT NULL DEFAULT ''",
    "done_definition": "TEXT NOT NULL DEFAULT ''",
    "planning_status": "TEXT NOT NULL DEFAULT 'triage'",
    "priority": "TEXT NOT NULL DEFAULT ''",
    "owner_kind": "TEXT NOT NULL DEFAULT 'user'",
    "owner_id": "TEXT NOT NULL DEFAULT ''",
    "created_by_kind": "TEXT NOT NULL DEFAULT 'user'",
    "created_by_id": "TEXT NOT NULL DEFAULT ''",
    "updated_by_kind": "TEXT NOT NULL DEFAULT ''",
    "updated_by_id": "TEXT NOT NULL DEFAULT ''",
    "archived_by_kind": "TEXT NOT NULL DEFAULT ''",
    "archived_by_id": "TEXT NOT NULL DEFAULT ''",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
    "archived_at": "TEXT NOT NULL DEFAULT ''",
}

INITIATIVE_LINK_COLUMNS = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "initiative_id": "TEXT NOT NULL",
    "link_type": "TEXT NOT NULL",
    "target_id": "TEXT NOT NULL",
    "created_by_kind": "TEXT NOT NULL DEFAULT ''",
    "created_by_id": "TEXT NOT NULL DEFAULT ''",
    "created_at": "TEXT NOT NULL DEFAULT ''",
}

AREA_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "slug": "TEXT NOT NULL DEFAULT ''",
    "group_name": "TEXT NOT NULL DEFAULT ''",
    "title": "TEXT NOT NULL DEFAULT ''",
    "area_type": "TEXT NOT NULL DEFAULT ''",
    "lifecycle": "TEXT NOT NULL DEFAULT 'planned'",
    "summary": "TEXT NOT NULL DEFAULT ''",
    "user_purpose": "TEXT NOT NULL DEFAULT ''",
    "system_purpose": "TEXT NOT NULL DEFAULT ''",
    "in_scope": "TEXT NOT NULL DEFAULT ''",
    "out_of_scope": "TEXT NOT NULL DEFAULT ''",
    "owner_kind": "TEXT NOT NULL DEFAULT 'user'",
    "owner_id": "TEXT NOT NULL DEFAULT ''",
    "created_by_kind": "TEXT NOT NULL DEFAULT 'user'",
    "created_by_id": "TEXT NOT NULL DEFAULT ''",
    "updated_by_kind": "TEXT NOT NULL DEFAULT ''",
    "updated_by_id": "TEXT NOT NULL DEFAULT ''",
    "archived_by_kind": "TEXT NOT NULL DEFAULT ''",
    "archived_by_id": "TEXT NOT NULL DEFAULT ''",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
    "archived_at": "TEXT NOT NULL DEFAULT ''",
}

AREA_LINK_COLUMNS = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "area_id": "TEXT NOT NULL",
    "link_type": "TEXT NOT NULL",
    "target_id": "TEXT NOT NULL",
    "relation": "TEXT NOT NULL DEFAULT ''",
    "created_by_kind": "TEXT NOT NULL DEFAULT ''",
    "created_by_id": "TEXT NOT NULL DEFAULT ''",
    "created_at": "TEXT NOT NULL DEFAULT ''",
}

AREA_NOTE_COLUMNS = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "area_id": "TEXT NOT NULL",
    "note_type": "TEXT NOT NULL",
    "title": "TEXT NOT NULL DEFAULT ''",
    "body": "TEXT NOT NULL DEFAULT ''",
    "target_type": "TEXT NOT NULL DEFAULT ''",
    "target_id": "TEXT NOT NULL DEFAULT ''",
    "created_by_kind": "TEXT NOT NULL DEFAULT ''",
    "created_by_id": "TEXT NOT NULL DEFAULT ''",
    "updated_by_kind": "TEXT NOT NULL DEFAULT ''",
    "updated_by_id": "TEXT NOT NULL DEFAULT ''",
    "archived_by_kind": "TEXT NOT NULL DEFAULT ''",
    "archived_by_id": "TEXT NOT NULL DEFAULT ''",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
    "archived_at": "TEXT NOT NULL DEFAULT ''",
}

THINKING_SCRATCHPAD_NOTE_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "slug": "TEXT NOT NULL DEFAULT ''",
    "group_name": "TEXT NOT NULL DEFAULT ''",
    "title": "TEXT NOT NULL DEFAULT ''",
    "body": "TEXT NOT NULL DEFAULT ''",
    "context_json": "TEXT NOT NULL DEFAULT '{}'",
    "links_json": "TEXT NOT NULL DEFAULT '[]'",
    "created_by_kind": "TEXT NOT NULL DEFAULT 'user'",
    "created_by_id": "TEXT NOT NULL DEFAULT ''",
    "updated_by_kind": "TEXT NOT NULL DEFAULT ''",
    "updated_by_id": "TEXT NOT NULL DEFAULT ''",
    "archived_by_kind": "TEXT NOT NULL DEFAULT ''",
    "archived_by_id": "TEXT NOT NULL DEFAULT ''",
    "deleted_by_kind": "TEXT NOT NULL DEFAULT ''",
    "deleted_by_id": "TEXT NOT NULL DEFAULT ''",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
    "archived_at": "TEXT NOT NULL DEFAULT ''",
    "deleted_at": "TEXT NOT NULL DEFAULT ''",
}

THINKING_MIND_MAP_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "slug": "TEXT NOT NULL DEFAULT ''",
    "group_name": "TEXT NOT NULL DEFAULT ''",
    "title": "TEXT NOT NULL DEFAULT ''",
    "description": "TEXT NOT NULL DEFAULT ''",
    "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
    "created_by_kind": "TEXT NOT NULL DEFAULT 'user'",
    "created_by_id": "TEXT NOT NULL DEFAULT ''",
    "updated_by_kind": "TEXT NOT NULL DEFAULT ''",
    "updated_by_id": "TEXT NOT NULL DEFAULT ''",
    "archived_by_kind": "TEXT NOT NULL DEFAULT ''",
    "archived_by_id": "TEXT NOT NULL DEFAULT ''",
    "deleted_by_kind": "TEXT NOT NULL DEFAULT ''",
    "deleted_by_id": "TEXT NOT NULL DEFAULT ''",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
    "archived_at": "TEXT NOT NULL DEFAULT ''",
    "deleted_at": "TEXT NOT NULL DEFAULT ''",
}

THINKING_MIND_MAP_NODE_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "map_id": "TEXT NOT NULL",
    "label": "TEXT NOT NULL DEFAULT ''",
    "title": "TEXT NOT NULL DEFAULT ''",
    "notes": "TEXT NOT NULL DEFAULT ''",
    "node_type": "TEXT NOT NULL DEFAULT ''",
    "tags_json": "TEXT NOT NULL DEFAULT '[]'",
    "color": "TEXT NOT NULL DEFAULT ''",
    "x": "REAL NOT NULL DEFAULT 0",
    "y": "REAL NOT NULL DEFAULT 0",
    "position_json": "TEXT NOT NULL DEFAULT '{}'",
    "sort_order": "INTEGER NOT NULL DEFAULT 0",
    "created_by_kind": "TEXT NOT NULL DEFAULT 'user'",
    "created_by_id": "TEXT NOT NULL DEFAULT ''",
    "updated_by_kind": "TEXT NOT NULL DEFAULT ''",
    "updated_by_id": "TEXT NOT NULL DEFAULT ''",
    "deleted_by_kind": "TEXT NOT NULL DEFAULT ''",
    "deleted_by_id": "TEXT NOT NULL DEFAULT ''",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
    "deleted_at": "TEXT NOT NULL DEFAULT ''",
}

THINKING_MIND_MAP_LINK_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "map_id": "TEXT NOT NULL",
    "source_node_id": "TEXT NOT NULL",
    "target_node_id": "TEXT NOT NULL",
    "label": "TEXT NOT NULL DEFAULT ''",
    "link_type": "TEXT NOT NULL DEFAULT ''",
    "sort_order": "INTEGER NOT NULL DEFAULT 0",
    "created_by_kind": "TEXT NOT NULL DEFAULT 'user'",
    "created_by_id": "TEXT NOT NULL DEFAULT ''",
    "updated_by_kind": "TEXT NOT NULL DEFAULT ''",
    "updated_by_id": "TEXT NOT NULL DEFAULT ''",
    "deleted_by_kind": "TEXT NOT NULL DEFAULT ''",
    "deleted_by_id": "TEXT NOT NULL DEFAULT ''",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
    "deleted_at": "TEXT NOT NULL DEFAULT ''",
}

IDEA_BRIEF_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "slug": "TEXT NOT NULL DEFAULT ''",
    "group_name": "TEXT NOT NULL DEFAULT ''",
    "title": "TEXT NOT NULL DEFAULT ''",
    "status": "TEXT NOT NULL DEFAULT 'draft'",
    "problem_opportunity": "TEXT NOT NULL DEFAULT ''",
    "why_it_matters": "TEXT NOT NULL DEFAULT ''",
    "proposed_shape": "TEXT NOT NULL DEFAULT ''",
    "smallest_useful_version": "TEXT NOT NULL DEFAULT ''",
    "risks_tradeoffs": "TEXT NOT NULL DEFAULT ''",
    "open_questions": "TEXT NOT NULL DEFAULT ''",
    "thinking_links_json": "TEXT NOT NULL DEFAULT '[]'",
    "source_context_json": "TEXT NOT NULL DEFAULT '{}'",
    "proposal_json": "TEXT NOT NULL DEFAULT '{}'",
    "refinement_log_json": "TEXT NOT NULL DEFAULT '[]'",
    "created_by_kind": "TEXT NOT NULL DEFAULT 'user'",
    "created_by_id": "TEXT NOT NULL DEFAULT ''",
    "updated_by_kind": "TEXT NOT NULL DEFAULT ''",
    "updated_by_id": "TEXT NOT NULL DEFAULT ''",
    "parked_by_kind": "TEXT NOT NULL DEFAULT ''",
    "parked_by_id": "TEXT NOT NULL DEFAULT ''",
    "archived_by_kind": "TEXT NOT NULL DEFAULT ''",
    "archived_by_id": "TEXT NOT NULL DEFAULT ''",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
    "parked_at": "TEXT NOT NULL DEFAULT ''",
    "archived_at": "TEXT NOT NULL DEFAULT ''",
}


def _ensure_thinking_schema(conn: sqlite3.Connection) -> None:
    """Keep Thinking Scratchpad and Mind Map tables additive/idempotent."""

    _ensure_columns(
        conn,
        "thinking_scratchpad_notes",
        THINKING_SCRATCHPAD_NOTE_COLUMNS,
    )
    _ensure_columns(conn, "thinking_mind_maps", THINKING_MIND_MAP_COLUMNS)
    _ensure_columns(
        conn,
        "thinking_mind_map_nodes",
        THINKING_MIND_MAP_NODE_COLUMNS,
    )
    _ensure_columns(
        conn,
        "thinking_mind_map_links",
        THINKING_MIND_MAP_LINK_COLUMNS,
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_thinking_scratchpad_group "
        "ON thinking_scratchpad_notes(group_name, deleted_at, archived_at, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_thinking_scratchpad_slug "
        "ON thinking_scratchpad_notes(group_name, slug)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_thinking_mind_maps_group "
        "ON thinking_mind_maps(group_name, deleted_at, archived_at, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_thinking_mind_maps_slug "
        "ON thinking_mind_maps(group_name, slug)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_thinking_mind_map_nodes_map "
        "ON thinking_mind_map_nodes(map_id, deleted_at, sort_order, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_thinking_mind_map_links_map "
        "ON thinking_mind_map_links(map_id, deleted_at, sort_order, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_thinking_mind_map_links_nodes "
        "ON thinking_mind_map_links(source_node_id, target_node_id)"
    )


def _ensure_idea_brief_schema(conn: sqlite3.Connection) -> None:
    """Keep Idea Brief tables additive/idempotent across partial migrations."""

    _ensure_columns(conn, "idea_briefs", IDEA_BRIEF_COLUMNS)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_idea_briefs_group_status "
        "ON idea_briefs(group_name, status, archived_at, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_idea_briefs_group_slug "
        "ON idea_briefs(group_name, slug)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_idea_briefs_owner "
        "ON idea_briefs(created_by_kind, created_by_id, updated_at DESC)"
    )


def _ensure_areas_schema(conn: sqlite3.Connection) -> None:
    """Keep Planning Area tables additive/idempotent across partial migrations."""

    _ensure_columns(conn, "planning_areas", AREA_COLUMNS)
    _ensure_columns(conn, "planning_area_links", AREA_LINK_COLUMNS)
    _ensure_columns(conn, "planning_area_notes", AREA_NOTE_COLUMNS)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_planning_areas_group_lifecycle "
        "ON planning_areas(group_name, lifecycle, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_planning_areas_owner "
        "ON planning_areas(owner_kind, owner_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_planning_area_links_area "
        "ON planning_area_links(area_id, link_type, target_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_planning_area_links_target "
        "ON planning_area_links(link_type, target_id, area_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_planning_area_links_unique "
        "ON planning_area_links(area_id, link_type, target_id, relation)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_planning_area_notes_area "
        "ON planning_area_notes(area_id, archived_at, updated_at DESC)"
    )


def _ensure_initiatives_schema(conn: sqlite3.Connection) -> None:
    """Keep Initiative tables additive/idempotent across partial migrations."""

    _ensure_columns(conn, "initiatives", INITIATIVE_COLUMNS)
    _ensure_columns(conn, "initiative_links", INITIATIVE_LINK_COLUMNS)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_initiatives_group_status "
        "ON initiatives(group_name, planning_status, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_initiatives_owner "
        "ON initiatives(owner_kind, owner_id, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_initiative_links_initiative "
        "ON initiative_links(initiative_id, link_type, target_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_initiative_links_target "
        "ON initiative_links(link_type, target_id, initiative_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_initiative_links_unique "
        "ON initiative_links(initiative_id, link_type, target_id)"
    )

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id                    TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    slug                  TEXT NOT NULL DEFAULT '',
    group_name            TEXT NOT NULL,
    cell_type             TEXT NOT NULL DEFAULT 'agent',
    terminal_backend      TEXT NOT NULL DEFAULT 'pty',
    runner_backend        TEXT NOT NULL DEFAULT 'pty',
    session_id            TEXT,
    profile               TEXT NOT NULL DEFAULT 'Default',
    command               TEXT NOT NULL DEFAULT '',
    directory             TEXT NOT NULL DEFAULT '',
    tab_color             TEXT NOT NULL DEFAULT '',
    icon                  TEXT NOT NULL DEFAULT '',
    window_id             TEXT NOT NULL DEFAULT '',
    parent_id             TEXT NOT NULL DEFAULT '',
    status                TEXT NOT NULL DEFAULT 'stopped',
    worktree_path         TEXT NOT NULL DEFAULT '',
    worktree_branch       TEXT NOT NULL DEFAULT '',
    worktree_repo_root    TEXT NOT NULL DEFAULT '',
    worktree_base_dir     TEXT NOT NULL DEFAULT '.torque/worktrees',
    worktree_base_branch  TEXT NOT NULL DEFAULT '',
    worktree_auto_checkpoint INTEGER NOT NULL DEFAULT 0,
    checkpoint_on_progress   INTEGER NOT NULL DEFAULT 0,
    worktree_merge_squash INTEGER NOT NULL DEFAULT 1,
    agent_type            TEXT NOT NULL DEFAULT '',
    agent_session_id      TEXT NOT NULL DEFAULT '',
    session_resume        INTEGER NOT NULL DEFAULT 1,
    idle_timeout          INTEGER NOT NULL DEFAULT 0,
    tasks_dispatched      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS groups (
    name     TEXT PRIMARY KEY,
    slug     TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS group_members (
    group_name TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (group_name, agent_id)
);

CREATE TABLE IF NOT EXISTS group_settings (
    group_name                  TEXT PRIMARY KEY,
    default_directory           TEXT NOT NULL DEFAULT '',
    default_terminal_backend    TEXT NOT NULL DEFAULT 'pty',
    profile                     TEXT NOT NULL DEFAULT '',
    shell                       TEXT NOT NULL DEFAULT '',
    tab_color                   TEXT NOT NULL DEFAULT '',
    env_vars                    TEXT NOT NULL DEFAULT '{}',
    env_file                    TEXT NOT NULL DEFAULT '',
    auto_terminals              INTEGER NOT NULL DEFAULT 0,
    max_agents                  INTEGER NOT NULL DEFAULT 0,
    collapsed_default           INTEGER NOT NULL DEFAULT 0,
    filter_by_window            INTEGER NOT NULL DEFAULT 0,
    agent_directory             TEXT NOT NULL DEFAULT '',
    agent_terminal_profile      TEXT NOT NULL DEFAULT '',
    agent_shell                 TEXT NOT NULL DEFAULT '',
    agent_tab_color             TEXT NOT NULL DEFAULT '',
    agent_env_vars              TEXT NOT NULL DEFAULT '{}',
    agent_env_file              TEXT NOT NULL DEFAULT '',
    default_agent_template      TEXT NOT NULL DEFAULT '',
    agent_provider              TEXT NOT NULL DEFAULT '',
    agent_boot_command          TEXT NOT NULL DEFAULT '',
    agent_model                 TEXT NOT NULL DEFAULT '',
    agent_reasoning_effort      TEXT NOT NULL DEFAULT '',
    worker_provider             TEXT NOT NULL DEFAULT '',
    worker_boot_command         TEXT NOT NULL DEFAULT '',
    worker_model                TEXT NOT NULL DEFAULT '',
    worker_reasoning_effort     TEXT NOT NULL DEFAULT '',
    git_worktree                INTEGER NOT NULL DEFAULT 0,
    worktree_base_dir           TEXT NOT NULL DEFAULT '.torque/worktrees',
    worktree_base_branch        TEXT NOT NULL DEFAULT '',
    worktree_auto_checkpoint    INTEGER NOT NULL DEFAULT 1,
    checkpoint_on_progress      INTEGER NOT NULL DEFAULT 0,
    worktree_merge_squash       INTEGER NOT NULL DEFAULT 0,
    worktree_merge_instructions TEXT NOT NULL DEFAULT '',
    worktree_merge_cleanup      TEXT NOT NULL DEFAULT 'keep',
    worktree_merge_preserve_diff INTEGER NOT NULL DEFAULT 0,
    engineer_merge_mode         TEXT NOT NULL DEFAULT 'pr',
    worktree_symlinks           TEXT NOT NULL DEFAULT '[]',
    worktree_submodules         TEXT NOT NULL DEFAULT '[]',
    worktree_symlink_gitignored_paths INTEGER NOT NULL DEFAULT 0,
    agent_session_resume        INTEGER NOT NULL DEFAULT 1,
    agent_idle_timeout          INTEGER NOT NULL DEFAULT 0,
    guidance_hint_cadence       INTEGER NOT NULL DEFAULT 4,
    engineer_hint_snoozes       TEXT NOT NULL DEFAULT '{}',
    notifications               INTEGER NOT NULL DEFAULT 0,
    notify_on_finish            INTEGER NOT NULL DEFAULT 1,
    notify_on_error             INTEGER NOT NULL DEFAULT 1,
    notify_on_attention         INTEGER NOT NULL DEFAULT 1,
    terminal_name_prefix        TEXT NOT NULL DEFAULT '',
    terminal_boot_command       TEXT NOT NULL DEFAULT '',
    terminal_command_args       TEXT NOT NULL DEFAULT '',
    terminal_init_script        TEXT NOT NULL DEFAULT '',
    terminal_directory          TEXT NOT NULL DEFAULT '',
    terminal_profile            TEXT NOT NULL DEFAULT '',
    terminal_shell              TEXT NOT NULL DEFAULT '',
    terminal_tab_color          TEXT NOT NULL DEFAULT '',
    terminal_env_vars           TEXT NOT NULL DEFAULT '{}',
    terminal_env_file           TEXT NOT NULL DEFAULT '',
    terminal_always_custom_dialog INTEGER NOT NULL DEFAULT 0,
    terminal_close_on_disconnect INTEGER NOT NULL DEFAULT 0,
    dispatch_lane               TEXT NOT NULL DEFAULT 'In Progress',
    board_default_labels        TEXT NOT NULL DEFAULT '[]',
    board_default_lane          TEXT NOT NULL DEFAULT '',
    board_default_action        TEXT NOT NULL DEFAULT '',
    board_sync_provider         TEXT NOT NULL DEFAULT 'none',
    board_sync_enabled          INTEGER NOT NULL DEFAULT 0,
    board_sync_github           TEXT NOT NULL DEFAULT '{}',
    engineer_agent_id             TEXT NOT NULL DEFAULT '',
    default_engineer_specializations TEXT NOT NULL DEFAULT '[]',
    architect_boot_command        TEXT NOT NULL DEFAULT '',
    architect_provider            TEXT NOT NULL DEFAULT '',
    architect_model               TEXT NOT NULL DEFAULT '',
    architect_reasoning_effort    TEXT NOT NULL DEFAULT '',
    architect_directory           TEXT NOT NULL DEFAULT '',
    architect_profile             TEXT NOT NULL DEFAULT '',
    architect_shell               TEXT NOT NULL DEFAULT '',
    architect_tab_color           TEXT NOT NULL DEFAULT '',
    architect_custom_instructions TEXT NOT NULL DEFAULT '',
    architect_autonomy_mode       TEXT NOT NULL DEFAULT 'dispatch_after_confirm',
    architect_paused              INTEGER NOT NULL DEFAULT 0,
    architect_digest_verbosity    TEXT NOT NULL DEFAULT 'balanced',
    architect_push_interval       INTEGER NOT NULL DEFAULT 300,
    architect_max_interval        INTEGER NOT NULL DEFAULT 600,
    architect_heartbeat_interval  INTEGER NOT NULL DEFAULT 0,
    architect_suppress_empty_digests INTEGER NOT NULL DEFAULT 1,
    architect_enabled_events      TEXT NOT NULL DEFAULT '',
    architect_journal_checkpoint_frequency TEXT NOT NULL DEFAULT 'every_10_actions',
    architect_review_gate_thresholds TEXT NOT NULL DEFAULT '{"ship_direct_max":50,"review_default_above":150,"self_review_bypass_allowed":false}',
    engineer_behavior_requires_user_approval INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS board_tasks (
    id             TEXT PRIMARY KEY,
    task           TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    slug           TEXT NOT NULL DEFAULT '',
    group_name     TEXT NOT NULL DEFAULT '',
    action_name    TEXT NOT NULL DEFAULT '',
    action_vars    TEXT NOT NULL DEFAULT '{}',
    agent_template TEXT NOT NULL DEFAULT '',
    instructions   TEXT NOT NULL DEFAULT '',
    context        TEXT NOT NULL DEFAULT '',
    criteria       TEXT NOT NULL DEFAULT '',
    lane           TEXT NOT NULL DEFAULT 'Backlog',
    position       INTEGER NOT NULL DEFAULT 0,
    agent_id       TEXT NOT NULL DEFAULT '',
    assigned_architect_id TEXT NOT NULL DEFAULT '',
    reply_agent_id TEXT NOT NULL DEFAULT '',
    labels         TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL DEFAULT '',
    lane_entered_at TEXT NOT NULL DEFAULT '',
    provider       TEXT NOT NULL DEFAULT '',
    external_id    TEXT NOT NULL DEFAULT '',
    external_url   TEXT NOT NULL DEFAULT '',
    board_sync     TEXT NOT NULL DEFAULT '{}',
    status         TEXT NOT NULL DEFAULT '',
    dispatch_state TEXT NOT NULL DEFAULT 'queued',
    attachments    TEXT NOT NULL DEFAULT '[]',
    messages_thread TEXT NOT NULL DEFAULT '[]',
    health_state   TEXT NOT NULL DEFAULT 'healthy',
    health_since   TEXT NOT NULL DEFAULT '',
    health_details TEXT NOT NULL DEFAULT '{}',
    artifacts      TEXT NOT NULL DEFAULT '[]',
    verification_mode TEXT NOT NULL DEFAULT '',
    verification_state TEXT NOT NULL DEFAULT '',
    verification_notes TEXT NOT NULL DEFAULT '',
    verification_updated_at TEXT NOT NULL DEFAULT '',
    verification_updated_by TEXT NOT NULL DEFAULT '',
    verification_summary TEXT NOT NULL DEFAULT '{}',
    completion_evidence TEXT NOT NULL DEFAULT '{}',
    worktree_boundary TEXT NOT NULL DEFAULT '{}',
    resume_after_boundary_task_id TEXT NOT NULL DEFAULT '',
    archived_at    TEXT NOT NULL DEFAULT '',
    archived_from_lane TEXT NOT NULL DEFAULT '',
    deliverable_required INTEGER NOT NULL DEFAULT 0,
    deliverable_type TEXT NOT NULL DEFAULT '',
    deliverable_format TEXT NOT NULL DEFAULT '',
    deliverable_artifact_title TEXT NOT NULL DEFAULT '',
    requires_review INTEGER NOT NULL DEFAULT 0,
    pre_approved_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS schedules (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL DEFAULT '',
    task_template   TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    group_name      TEXT NOT NULL DEFAULT '',
    action_name     TEXT NOT NULL DEFAULT '',
    action_vars     TEXT NOT NULL DEFAULT '{}',
    agent_template  TEXT NOT NULL DEFAULT '',
    labels          TEXT NOT NULL DEFAULT '[]',
    cron_expr       TEXT NOT NULL DEFAULT '',
    scheduled_at    TEXT NOT NULL DEFAULT '',
    timezone        TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_run_at     TEXT NOT NULL DEFAULT '',
    next_run_at     TEXT NOT NULL DEFAULT '',
    run_count       INTEGER NOT NULL DEFAULT 0,
    last_task_id    TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agent_message_loops (
    id               TEXT PRIMARY KEY,
    agent_id         TEXT NOT NULL DEFAULT '',
    group_name       TEXT NOT NULL DEFAULT '',
    interval_seconds INTEGER NOT NULL DEFAULT 0,
    message          TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'active',
    created_by       TEXT NOT NULL DEFAULT 'user',
    stopped_by       TEXT NOT NULL DEFAULT '',
    stop_reason      TEXT NOT NULL DEFAULT '',
    created_at       REAL NOT NULL DEFAULT 0,
    updated_at       REAL NOT NULL DEFAULT 0,
    next_run_at      REAL NOT NULL DEFAULT 0,
    last_run_at      REAL NOT NULL DEFAULT 0,
    run_count        INTEGER NOT NULL DEFAULT 0,
    last_message_id  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_agent_message_loops_agent_status
    ON agent_message_loops(agent_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_message_loops_due
    ON agent_message_loops(status, next_run_at);

CREATE TABLE IF NOT EXISTS board_lanes (
    name     TEXT PRIMARY KEY,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ui_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS global_settings (
    key               TEXT PRIMARY KEY,
    value             TEXT NOT NULL,
    xterm_scrollback  INTEGER NOT NULL DEFAULT 2000
);

CREATE TABLE IF NOT EXISTS ai_provider_secrets (
    provider   TEXT PRIMARY KEY,
    api_key    TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ai_call_metrics (
    id          TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    purpose     TEXT NOT NULL DEFAULT '',
    provider    TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT '',
    failure_kind TEXT NOT NULL DEFAULT '',
    latency_ms  INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ai_call_metrics_created
    ON ai_call_metrics(created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS ai_index_state (
    id                  TEXT PRIMARY KEY DEFAULT 'default',
    desired_model_id    TEXT NOT NULL DEFAULT 'BAAI/bge-m3',
    active_model_id     TEXT NOT NULL DEFAULT '',
    active_dims         INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'not_built',
    rebuild_required    INTEGER NOT NULL DEFAULT 0,
    rebuild_reason      TEXT NOT NULL DEFAULT '',
    corpus_config       TEXT NOT NULL DEFAULT '{}',
    last_scan_at        REAL NOT NULL DEFAULT 0,
    last_built_at       REAL NOT NULL DEFAULT 0,
    last_error          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ai_embedding_sources (
    source_key           TEXT PRIMARY KEY,
    source_type          TEXT NOT NULL,
    source_id            TEXT NOT NULL,
    source_sub_id        TEXT NOT NULL DEFAULT '',
    group_name           TEXT NOT NULL DEFAULT '',
    owner_kind           TEXT NOT NULL DEFAULT '',
    owner_id             TEXT NOT NULL DEFAULT '',
    participant_ids      TEXT NOT NULL DEFAULT '[]',
    participant_kinds    TEXT NOT NULL DEFAULT '{}',
    visibility_json      TEXT NOT NULL DEFAULT '{}',
    title                TEXT NOT NULL DEFAULT '',
    source_updated_at    TEXT NOT NULL DEFAULT '',
    content_hash         TEXT NOT NULL,
    indexed_content_hash TEXT NOT NULL DEFAULT '',
    state                TEXT NOT NULL DEFAULT 'pending',
    discovered_at        REAL NOT NULL DEFAULT 0,
    last_seen_at         REAL NOT NULL DEFAULT 0,
    indexed_at           REAL NOT NULL DEFAULT 0,
    error                TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ai_embedding_sources_state
    ON ai_embedding_sources(state);
CREATE INDEX IF NOT EXISTS idx_ai_embedding_sources_scope
    ON ai_embedding_sources(group_name, source_type, owner_id);

CREATE TABLE IF NOT EXISTS ai_embedding_chunks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key          TEXT NOT NULL,
    chunk_index         INTEGER NOT NULL DEFAULT 0,
    text                TEXT NOT NULL,
    chunk_hash          TEXT NOT NULL,
    embedding_model_id  TEXT NOT NULL,
    embedding_dims      INTEGER NOT NULL,
    indexed_at          REAL NOT NULL DEFAULT 0,
    distance_metric     TEXT NOT NULL DEFAULT 'cosine',
    error               TEXT NOT NULL DEFAULT '',
    UNIQUE(source_key, chunk_index),
    FOREIGN KEY(source_key) REFERENCES ai_embedding_sources(source_key)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ai_embedding_chunks_source
    ON ai_embedding_chunks(source_key);
CREATE INDEX IF NOT EXISTS idx_ai_embedding_chunks_model
    ON ai_embedding_chunks(embedding_model_id, embedding_dims);

CREATE TABLE IF NOT EXISTS ai_index_jobs (
    id           TEXT PRIMARY KEY,
    mode         TEXT NOT NULL DEFAULT 'incremental',
    status       TEXT NOT NULL DEFAULT 'queued',
    reason       TEXT NOT NULL DEFAULT '',
    started_at   REAL NOT NULL DEFAULT 0,
    updated_at   REAL NOT NULL DEFAULT 0,
    completed_at REAL NOT NULL DEFAULT 0,
    totals       TEXT NOT NULL DEFAULT '{}',
    error        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ai_index_jobs_status
    ON ai_index_jobs(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS ai_summaries (
    summary_key    TEXT PRIMARY KEY,
    summary_type   TEXT NOT NULL DEFAULT '',
    scope_kind     TEXT NOT NULL DEFAULT '',
    scope_ref      TEXT NOT NULL DEFAULT '',
    provider       TEXT NOT NULL DEFAULT '',
    model          TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    source_hash    TEXT NOT NULL DEFAULT '',
    source_counts  TEXT NOT NULL DEFAULT '{}',
    summary_text   TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'empty',
    generated_at   REAL NOT NULL DEFAULT 0,
    updated_at     REAL NOT NULL DEFAULT 0,
    error          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ai_summaries_scope
    ON ai_summaries(summary_type, scope_kind, scope_ref);
CREATE INDEX IF NOT EXISTS idx_ai_summaries_status
    ON ai_summaries(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS panel_events (
    id         INTEGER PRIMARY KEY,
    timestamp  REAL    NOT NULL,
    kind       TEXT    NOT NULL,
    cell_id    TEXT    NOT NULL DEFAULT '',
    agent_name TEXT    NOT NULL DEFAULT '',
    group_name TEXT    NOT NULL DEFAULT '',
    message    TEXT    NOT NULL DEFAULT '',
    task_id    TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_panel_events_ts ON panel_events (timestamp);

CREATE TABLE IF NOT EXISTS metrics_perf_rollups (
    bucket_start                 INTEGER NOT NULL,
    bucket_seconds               INTEGER NOT NULL,
    sample_count                 INTEGER NOT NULL DEFAULT 0,
    event_loop_lag_p95_ms        REAL NOT NULL DEFAULT 0,
    ws_deltas_per_s              REAL NOT NULL DEFAULT 0,
    db_write_latency_p95_ms      REAL NOT NULL DEFAULT 0,
    rss_mb                       REAL NOT NULL DEFAULT 0,
    cpu_pct                      REAL NOT NULL DEFAULT 0,
    created_at                   REAL NOT NULL DEFAULT 0,
    updated_at                   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket_start, bucket_seconds)
);
CREATE INDEX IF NOT EXISTS idx_metrics_perf_rollups_start
    ON metrics_perf_rollups(bucket_start);

CREATE TABLE IF NOT EXISTS agent_history (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    slug              TEXT DEFAULT '',
    "group"           TEXT DEFAULT '',
    agent_type        TEXT DEFAULT '',
    template          TEXT DEFAULT '',
    created_at        REAL NOT NULL,
    removed_at        REAL,
    worktree_branch   TEXT DEFAULT '',
    total_tokens_in   INTEGER DEFAULT 0,
    total_tokens_out  INTEGER DEFAULT 0,
    total_tasks       INTEGER DEFAULT 0,
    status            TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    task_title  TEXT NOT NULL,
    started_at  REAL NOT NULL,
    completed_at REAL,
    outcome     TEXT DEFAULT '',
    FOREIGN KEY (agent_id) REFERENCES agent_history(id)
);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_agent ON agent_tasks (agent_id);

CREATE TABLE IF NOT EXISTS agent_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    task_id     TEXT DEFAULT '',
    timestamp   REAL NOT NULL,
    action      TEXT NOT NULL,
    message     TEXT DEFAULT '',
    FOREIGN KEY (agent_id) REFERENCES agent_history(id)
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_agent ON agent_messages (agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_messages_task ON agent_messages (task_id);

CREATE TABLE IF NOT EXISTS agent_message_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id  TEXT NOT NULL,
    message   TEXT NOT NULL,
    sent_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_message_history_agent_recent
    ON agent_message_history(agent_id, sent_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS engineer_task_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name  TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    task_title  TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    agent_name  TEXT NOT NULL DEFAULT '',
    agent_slug  TEXT NOT NULL DEFAULT '',
    agent_owned INTEGER NOT NULL DEFAULT 0,
    started_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_engineer_task_log_group
    ON engineer_task_log(group_name, started_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS engineer_settings (
    group_name         TEXT PRIMARY KEY,
    push_interval      INTEGER NOT NULL DEFAULT 60,
    max_interval       INTEGER NOT NULL DEFAULT 300,
    heartbeat_interval INTEGER NOT NULL DEFAULT 300,
    default_worker_concurrency INTEGER NOT NULL DEFAULT 2,
    autonomy_mode      TEXT NOT NULL DEFAULT 'dispatch_when_clear',
    wave_size_preference TEXT NOT NULL DEFAULT 'small',
    same_agent_follow_up_preference TEXT NOT NULL DEFAULT 'balanced',
    digest_verbosity   TEXT NOT NULL DEFAULT 'balanced',
    escalation_style   TEXT NOT NULL DEFAULT 'note_then_ask',
    paused             INTEGER NOT NULL DEFAULT 0,
    custom_instructions TEXT NOT NULL DEFAULT '',
    restrict_to_created_agents INTEGER NOT NULL DEFAULT 0,
    engineer_can_override_worker_provider INTEGER NOT NULL DEFAULT 1,
    pending_question   TEXT NOT NULL DEFAULT '',
    pending_note       TEXT NOT NULL DEFAULT '',
    pending_note_kind  TEXT NOT NULL DEFAULT '',
    pending_note_set_at REAL NOT NULL DEFAULT 0,
    pending_note_actor_id TEXT NOT NULL DEFAULT '',
    enabled_events     TEXT NOT NULL DEFAULT '["agent_started","task_dispatched","task_derived","task_health_alert"]',
    engineer_provider    TEXT NOT NULL DEFAULT '',
    engineer_boot_command TEXT NOT NULL DEFAULT '',
    engineer_model       TEXT NOT NULL DEFAULT '',
    engineer_reasoning_effort TEXT NOT NULL DEFAULT '',
    engineer_directory   TEXT NOT NULL DEFAULT '',
    engineer_profile     TEXT NOT NULL DEFAULT '',
    engineer_shell       TEXT NOT NULL DEFAULT '',
    engineer_tab_color   TEXT NOT NULL DEFAULT '',
    pending_question_set_at REAL NOT NULL DEFAULT 0,
    pending_question_actor_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agent_digest_settings (
    agent_id           TEXT PRIMARY KEY,
    paused             INTEGER NOT NULL DEFAULT 0,
    push_interval      INTEGER NOT NULL DEFAULT 60,
    max_interval       INTEGER NOT NULL DEFAULT 300,
    heartbeat_interval INTEGER NOT NULL DEFAULT 300,
    digest_verbosity   TEXT NOT NULL DEFAULT 'balanced',
    enabled_events     TEXT NOT NULL DEFAULT '["agent_started","task_dispatched","task_derived","task_health_alert"]',
    architect_digest   INTEGER NOT NULL DEFAULT 0,
    wake_on_digest     INTEGER NOT NULL DEFAULT 0,
    suppress_empty     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS digest_queued_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id TEXT NOT NULL,
    event_json   TEXT NOT NULL,
    enqueued_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_digest_queued_recipient
    ON digest_queued_events (recipient_id, id);

CREATE TABLE IF NOT EXISTS digest_sent_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id TEXT NOT NULL,
    event_json   TEXT NOT NULL,
    enqueued_at  REAL NOT NULL,
    delivered_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_digest_sent_recipient
    ON digest_sent_events (recipient_id, delivered_at DESC);

CREATE TABLE IF NOT EXISTS engineer_journal (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name  TEXT NOT NULL,
    timestamp   REAL NOT NULL,
    entry_type  TEXT NOT NULL,
    entry       TEXT NOT NULL,
    author_cell_id TEXT NOT NULL DEFAULT '',
    source_key  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_engineer_journal_group
    ON engineer_journal(group_name, id DESC);

CREATE TABLE IF NOT EXISTS playbook_candidates (
    id                     TEXT PRIMARY KEY,
    group_name             TEXT NOT NULL DEFAULT '',
    family_key             TEXT NOT NULL DEFAULT '',
    status                 TEXT NOT NULL DEFAULT 'draft',
    created_at             REAL NOT NULL,
    updated_at             REAL NOT NULL,
    name                   TEXT NOT NULL DEFAULT '',
    root_action            TEXT NOT NULL DEFAULT '',
    labels                 TEXT NOT NULL DEFAULT '[]',
    normalized_task_family TEXT NOT NULL DEFAULT '',
    entry_action           TEXT NOT NULL DEFAULT '',
    agent_template         TEXT NOT NULL DEFAULT '',
    workflow               TEXT NOT NULL DEFAULT '[]',
    workflow_shape         TEXT NOT NULL DEFAULT '[]',
    dispatch_sequence      TEXT NOT NULL DEFAULT '[]',
    action_combination     TEXT NOT NULL DEFAULT '[]',
    constraints            TEXT NOT NULL DEFAULT '{}',
    evidence               TEXT NOT NULL DEFAULT '{}',
    supporting_runs        TEXT NOT NULL DEFAULT '[]',
    counterexamples        TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_playbook_candidates_group
    ON playbook_candidates(group_name, updated_at DESC);

CREATE TABLE IF NOT EXISTS playbooks (
    id                  TEXT PRIMARY KEY,
    group_name          TEXT NOT NULL DEFAULT '',
    source_candidate_id TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'draft',
    generated           INTEGER NOT NULL DEFAULT 1,
    review_required     INTEGER NOT NULL DEFAULT 1,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    published_at        REAL,
    discarded_at        REAL,
    name                TEXT NOT NULL DEFAULT '',
    description         TEXT NOT NULL DEFAULT '',
    match_data          TEXT NOT NULL DEFAULT '{}',
    entry_action        TEXT NOT NULL DEFAULT '',
    agent_template      TEXT NOT NULL DEFAULT '',
    workflow            TEXT NOT NULL DEFAULT '[]',
    constraints         TEXT NOT NULL DEFAULT '{}',
    evidence            TEXT NOT NULL DEFAULT '{}',
    publication_preview TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_playbooks_group_status
    ON playbooks(group_name, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS auto_dispatch_queue (
    group_name       TEXT NOT NULL,
    position         INTEGER NOT NULL DEFAULT 0,
    task_id          TEXT NOT NULL,
    agent_group      TEXT NOT NULL DEFAULT '',
    max_concurrent   INTEGER NOT NULL DEFAULT 1,
    target_agent_id  TEXT NOT NULL DEFAULT '',
    engineer_owner_id  TEXT NOT NULL DEFAULT '',
    provider         TEXT NOT NULL DEFAULT '',
    enqueued_at      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (group_name, position)
);
CREATE INDEX IF NOT EXISTS idx_auto_dispatch_queue_group
    ON auto_dispatch_queue(group_name, position);

CREATE TABLE IF NOT EXISTS task_id_counters (
    group_prefix     TEXT PRIMARY KEY,
    next_root_number INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS initiative_id_counters (
    group_prefix                TEXT PRIMARY KEY,
    next_initiative_number      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS initiatives (
    id                 TEXT PRIMARY KEY,
    slug               TEXT NOT NULL DEFAULT '',
    group_name         TEXT NOT NULL DEFAULT '',
    title              TEXT NOT NULL DEFAULT '',
    summary            TEXT NOT NULL DEFAULT '',
    why                TEXT NOT NULL DEFAULT '',
    in_scope           TEXT NOT NULL DEFAULT '',
    out_of_scope       TEXT NOT NULL DEFAULT '',
    done_definition    TEXT NOT NULL DEFAULT '',
    planning_status    TEXT NOT NULL DEFAULT 'triage',
    priority           TEXT NOT NULL DEFAULT '',
    owner_kind         TEXT NOT NULL DEFAULT 'user',
    owner_id           TEXT NOT NULL DEFAULT '',
    created_by_kind    TEXT NOT NULL DEFAULT 'user',
    created_by_id      TEXT NOT NULL DEFAULT '',
    updated_by_kind    TEXT NOT NULL DEFAULT '',
    updated_by_id      TEXT NOT NULL DEFAULT '',
    archived_by_kind   TEXT NOT NULL DEFAULT '',
    archived_by_id     TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL DEFAULT '',
    updated_at         TEXT NOT NULL DEFAULT '',
    archived_at        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_initiatives_group_status
    ON initiatives(group_name, planning_status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_initiatives_owner
    ON initiatives(owner_kind, owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS initiative_links (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    initiative_id      TEXT NOT NULL,
    link_type          TEXT NOT NULL,
    target_id          TEXT NOT NULL,
    created_by_kind    TEXT NOT NULL DEFAULT '',
    created_by_id      TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL DEFAULT '',
    UNIQUE(initiative_id, link_type, target_id),
    FOREIGN KEY(initiative_id) REFERENCES initiatives(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_initiative_links_initiative
    ON initiative_links(initiative_id, link_type, target_id);
CREATE INDEX IF NOT EXISTS idx_initiative_links_target
    ON initiative_links(link_type, target_id, initiative_id);

CREATE TABLE IF NOT EXISTS area_id_counters (
    group_prefix                TEXT PRIMARY KEY,
    next_area_number            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS planning_areas (
    id                 TEXT PRIMARY KEY,
    slug               TEXT NOT NULL DEFAULT '',
    group_name         TEXT NOT NULL DEFAULT '',
    title              TEXT NOT NULL DEFAULT '',
    area_type          TEXT NOT NULL DEFAULT '',
    lifecycle          TEXT NOT NULL DEFAULT 'planned',
    summary            TEXT NOT NULL DEFAULT '',
    user_purpose       TEXT NOT NULL DEFAULT '',
    system_purpose     TEXT NOT NULL DEFAULT '',
    in_scope           TEXT NOT NULL DEFAULT '',
    out_of_scope       TEXT NOT NULL DEFAULT '',
    owner_kind         TEXT NOT NULL DEFAULT 'user',
    owner_id           TEXT NOT NULL DEFAULT '',
    created_by_kind    TEXT NOT NULL DEFAULT 'user',
    created_by_id      TEXT NOT NULL DEFAULT '',
    updated_by_kind    TEXT NOT NULL DEFAULT '',
    updated_by_id      TEXT NOT NULL DEFAULT '',
    archived_by_kind   TEXT NOT NULL DEFAULT '',
    archived_by_id     TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL DEFAULT '',
    updated_at         TEXT NOT NULL DEFAULT '',
    archived_at        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_planning_areas_group_lifecycle
    ON planning_areas(group_name, lifecycle, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_planning_areas_owner
    ON planning_areas(owner_kind, owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS planning_area_links (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    area_id            TEXT NOT NULL,
    link_type          TEXT NOT NULL,
    target_id          TEXT NOT NULL,
    relation           TEXT NOT NULL DEFAULT '',
    created_by_kind    TEXT NOT NULL DEFAULT '',
    created_by_id      TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL DEFAULT '',
    UNIQUE(area_id, link_type, target_id, relation),
    FOREIGN KEY(area_id) REFERENCES planning_areas(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_planning_area_links_area
    ON planning_area_links(area_id, link_type, target_id);
CREATE INDEX IF NOT EXISTS idx_planning_area_links_target
    ON planning_area_links(link_type, target_id, area_id);

CREATE TABLE IF NOT EXISTS planning_area_notes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    area_id            TEXT NOT NULL,
    note_type          TEXT NOT NULL,
    title              TEXT NOT NULL DEFAULT '',
    body               TEXT NOT NULL DEFAULT '',
    target_type        TEXT NOT NULL DEFAULT '',
    target_id          TEXT NOT NULL DEFAULT '',
    created_by_kind    TEXT NOT NULL DEFAULT '',
    created_by_id      TEXT NOT NULL DEFAULT '',
    updated_by_kind    TEXT NOT NULL DEFAULT '',
    updated_by_id      TEXT NOT NULL DEFAULT '',
    archived_by_kind   TEXT NOT NULL DEFAULT '',
    archived_by_id     TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL DEFAULT '',
    updated_at         TEXT NOT NULL DEFAULT '',
    archived_at        TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(area_id) REFERENCES planning_areas(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_planning_area_notes_area
    ON planning_area_notes(area_id, archived_at, updated_at DESC);

CREATE TABLE IF NOT EXISTS scratchpad_note_id_counters (
    group_prefix                TEXT PRIMARY KEY,
    next_note_number            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS mind_map_id_counters (
    group_prefix                TEXT PRIMARY KEY,
    next_map_number             INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS thinking_mind_map_item_counters (
    map_id                      TEXT PRIMARY KEY,
    next_node_number            INTEGER NOT NULL DEFAULT 1,
    next_link_number            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS thinking_scratchpad_notes (
    id                 TEXT PRIMARY KEY,
    slug               TEXT NOT NULL DEFAULT '',
    group_name         TEXT NOT NULL DEFAULT '',
    title              TEXT NOT NULL DEFAULT '',
    body               TEXT NOT NULL DEFAULT '',
    context_json       TEXT NOT NULL DEFAULT '{}',
    links_json         TEXT NOT NULL DEFAULT '[]',
    created_by_kind    TEXT NOT NULL DEFAULT 'user',
    created_by_id      TEXT NOT NULL DEFAULT '',
    updated_by_kind    TEXT NOT NULL DEFAULT '',
    updated_by_id      TEXT NOT NULL DEFAULT '',
    archived_by_kind   TEXT NOT NULL DEFAULT '',
    archived_by_id     TEXT NOT NULL DEFAULT '',
    deleted_by_kind    TEXT NOT NULL DEFAULT '',
    deleted_by_id      TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL DEFAULT '',
    updated_at         TEXT NOT NULL DEFAULT '',
    archived_at        TEXT NOT NULL DEFAULT '',
    deleted_at         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_thinking_scratchpad_group
    ON thinking_scratchpad_notes(group_name, deleted_at, archived_at, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_thinking_scratchpad_slug
    ON thinking_scratchpad_notes(group_name, slug);

CREATE TABLE IF NOT EXISTS thinking_mind_maps (
    id                 TEXT PRIMARY KEY,
    slug               TEXT NOT NULL DEFAULT '',
    group_name         TEXT NOT NULL DEFAULT '',
    title              TEXT NOT NULL DEFAULT '',
    description        TEXT NOT NULL DEFAULT '',
    metadata_json      TEXT NOT NULL DEFAULT '{}',
    created_by_kind    TEXT NOT NULL DEFAULT 'user',
    created_by_id      TEXT NOT NULL DEFAULT '',
    updated_by_kind    TEXT NOT NULL DEFAULT '',
    updated_by_id      TEXT NOT NULL DEFAULT '',
    archived_by_kind   TEXT NOT NULL DEFAULT '',
    archived_by_id     TEXT NOT NULL DEFAULT '',
    deleted_by_kind    TEXT NOT NULL DEFAULT '',
    deleted_by_id      TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL DEFAULT '',
    updated_at         TEXT NOT NULL DEFAULT '',
    archived_at        TEXT NOT NULL DEFAULT '',
    deleted_at         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_thinking_mind_maps_group
    ON thinking_mind_maps(group_name, deleted_at, archived_at, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_thinking_mind_maps_slug
    ON thinking_mind_maps(group_name, slug);

CREATE TABLE IF NOT EXISTS thinking_mind_map_nodes (
    id                 TEXT PRIMARY KEY,
    map_id             TEXT NOT NULL,
    label              TEXT NOT NULL DEFAULT '',
    title              TEXT NOT NULL DEFAULT '',
    notes              TEXT NOT NULL DEFAULT '',
    node_type          TEXT NOT NULL DEFAULT '',
    tags_json          TEXT NOT NULL DEFAULT '[]',
    color              TEXT NOT NULL DEFAULT '',
    x                  REAL NOT NULL DEFAULT 0,
    y                  REAL NOT NULL DEFAULT 0,
    position_json      TEXT NOT NULL DEFAULT '{}',
    sort_order         INTEGER NOT NULL DEFAULT 0,
    created_by_kind    TEXT NOT NULL DEFAULT 'user',
    created_by_id      TEXT NOT NULL DEFAULT '',
    updated_by_kind    TEXT NOT NULL DEFAULT '',
    updated_by_id      TEXT NOT NULL DEFAULT '',
    deleted_by_kind    TEXT NOT NULL DEFAULT '',
    deleted_by_id      TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL DEFAULT '',
    updated_at         TEXT NOT NULL DEFAULT '',
    deleted_at         TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(map_id) REFERENCES thinking_mind_maps(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_thinking_mind_map_nodes_map
    ON thinking_mind_map_nodes(map_id, deleted_at, sort_order, id);

CREATE TABLE IF NOT EXISTS thinking_mind_map_links (
    id                 TEXT PRIMARY KEY,
    map_id             TEXT NOT NULL,
    source_node_id     TEXT NOT NULL,
    target_node_id     TEXT NOT NULL,
    label              TEXT NOT NULL DEFAULT '',
    link_type          TEXT NOT NULL DEFAULT '',
    sort_order         INTEGER NOT NULL DEFAULT 0,
    created_by_kind    TEXT NOT NULL DEFAULT 'user',
    created_by_id      TEXT NOT NULL DEFAULT '',
    updated_by_kind    TEXT NOT NULL DEFAULT '',
    updated_by_id      TEXT NOT NULL DEFAULT '',
    deleted_by_kind    TEXT NOT NULL DEFAULT '',
    deleted_by_id      TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL DEFAULT '',
    updated_at         TEXT NOT NULL DEFAULT '',
    deleted_at         TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(map_id) REFERENCES thinking_mind_maps(id) ON DELETE CASCADE,
    FOREIGN KEY(source_node_id) REFERENCES thinking_mind_map_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(target_node_id) REFERENCES thinking_mind_map_nodes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_thinking_mind_map_links_map
    ON thinking_mind_map_links(map_id, deleted_at, sort_order, id);
CREATE INDEX IF NOT EXISTS idx_thinking_mind_map_links_nodes
    ON thinking_mind_map_links(source_node_id, target_node_id);

CREATE TABLE IF NOT EXISTS idea_brief_id_counters (
    group_prefix                TEXT PRIMARY KEY,
    next_brief_number           INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS idea_briefs (
    id                         TEXT PRIMARY KEY,
    slug                       TEXT NOT NULL DEFAULT '',
    group_name                 TEXT NOT NULL DEFAULT '',
    title                      TEXT NOT NULL DEFAULT '',
    status                     TEXT NOT NULL DEFAULT 'draft',
    problem_opportunity        TEXT NOT NULL DEFAULT '',
    why_it_matters             TEXT NOT NULL DEFAULT '',
    proposed_shape             TEXT NOT NULL DEFAULT '',
    smallest_useful_version    TEXT NOT NULL DEFAULT '',
    risks_tradeoffs            TEXT NOT NULL DEFAULT '',
    open_questions             TEXT NOT NULL DEFAULT '',
    thinking_links_json        TEXT NOT NULL DEFAULT '[]',
    source_context_json        TEXT NOT NULL DEFAULT '{}',
    proposal_json              TEXT NOT NULL DEFAULT '{}',
    refinement_log_json        TEXT NOT NULL DEFAULT '[]',
    created_by_kind            TEXT NOT NULL DEFAULT 'user',
    created_by_id              TEXT NOT NULL DEFAULT '',
    updated_by_kind            TEXT NOT NULL DEFAULT '',
    updated_by_id              TEXT NOT NULL DEFAULT '',
    parked_by_kind             TEXT NOT NULL DEFAULT '',
    parked_by_id               TEXT NOT NULL DEFAULT '',
    archived_by_kind           TEXT NOT NULL DEFAULT '',
    archived_by_id             TEXT NOT NULL DEFAULT '',
    created_at                 TEXT NOT NULL DEFAULT '',
    updated_at                 TEXT NOT NULL DEFAULT '',
    parked_at                  TEXT NOT NULL DEFAULT '',
    archived_at                TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_idea_briefs_group_status
    ON idea_briefs(group_name, status, archived_at, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_idea_briefs_group_slug
    ON idea_briefs(group_name, slug);
CREATE INDEX IF NOT EXISTS idx_idea_briefs_owner
    ON idea_briefs(created_by_kind, created_by_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS pipeline_task_counters (
    root_task_id       TEXT PRIMARY KEY,
    next_child_number  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS task_id_aliases (
    legacy_id TEXT PRIMARY KEY,
    task_id   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_id_aliases_task
    ON task_id_aliases(task_id);

CREATE TABLE IF NOT EXISTS memory_entries (
    id          TEXT PRIMARY KEY,
    project_key TEXT NOT NULL DEFAULT '',
    group_name  TEXT NOT NULL DEFAULT '',
    scope_kind  TEXT NOT NULL,
    scope_ref   TEXT NOT NULL DEFAULT '',
    entry_type  TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL DEFAULT '',
    pinned      INTEGER NOT NULL DEFAULT 0,
    task_id     TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT '',
    source_id   TEXT NOT NULL DEFAULT '',
    source_name TEXT NOT NULL DEFAULT '',
    retention_kind TEXT NOT NULL DEFAULT 'durable',
    expires_at  REAL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_entries_scope
    ON memory_entries(scope_kind, scope_ref, pinned, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_entries_group
    ON memory_entries(group_name, pinned, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_entries_project
    ON memory_entries(project_key, pinned, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_entries_type
    ON memory_entries(entry_type, created_at DESC);

CREATE TABLE IF NOT EXISTS memory_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id    TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_ref  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    UNIQUE(entry_id, target_kind, target_ref),
    FOREIGN KEY(entry_id) REFERENCES memory_entries(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_memory_links_entry
    ON memory_links(entry_id, target_kind, target_ref);
CREATE INDEX IF NOT EXISTS idx_memory_links_target
    ON memory_links(target_kind, target_ref, created_at DESC, entry_id);

CREATE TABLE IF NOT EXISTS pending_hires (
    id TEXT PRIMARY KEY,
    architect_id TEXT NOT NULL,
    requested_name TEXT NOT NULL,
    requested_command TEXT NOT NULL DEFAULT '',
    requested_provider TEXT NOT NULL DEFAULT '',
    requested_directory TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    resolution_note TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL DEFAULT 0,
    resolved_at INTEGER NOT NULL DEFAULT 0,
    created_engineer_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pending_hires_status
    ON pending_hires(status);
CREATE INDEX IF NOT EXISTS idx_pending_hires_architect
    ON pending_hires(architect_id);

CREATE TABLE IF NOT EXISTS behavior_overlay_versions (
    id TEXT PRIMARY KEY,
    scope_kind TEXT NOT NULL DEFAULT 'agent',
    scope_group TEXT NOT NULL DEFAULT '',
    scope_key TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT '',
    version_number INTEGER NOT NULL,
    parent_version_id TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    author_agent_id TEXT NOT NULL DEFAULT '',
    author_kind TEXT NOT NULL DEFAULT '',
    rationale TEXT NOT NULL DEFAULT '',
    approver_id TEXT NOT NULL DEFAULT '',
    approver_kind TEXT NOT NULL DEFAULT '',
    source_proposal_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(scope_kind, scope_group, scope_key, version_number)
);
CREATE INDEX IF NOT EXISTS idx_behavior_overlay_versions_scope
    ON behavior_overlay_versions(scope_kind, scope_group, scope_key, version_number DESC);
CREATE INDEX IF NOT EXISTS idx_behavior_overlay_versions_proposal
    ON behavior_overlay_versions(source_proposal_id);

CREATE TABLE IF NOT EXISTS behavior_overlay_active (
    scope_kind TEXT NOT NULL DEFAULT 'agent',
    scope_group TEXT NOT NULL DEFAULT '',
    scope_key TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    active_version_id TEXT NOT NULL,
    updated_at REAL NOT NULL,
    updated_by_kind TEXT NOT NULL DEFAULT '',
    updated_by_id TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(scope_kind, scope_group, scope_key)
);

CREATE TABLE IF NOT EXISTS behavior_overlay_proposals (
    id TEXT PRIMARY KEY,
    scope_kind TEXT NOT NULL DEFAULT 'agent',
    scope_group TEXT NOT NULL DEFAULT '',
    scope_key TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT '',
    target_kind TEXT NOT NULL DEFAULT '',
    proposal_type TEXT NOT NULL DEFAULT 'set_text',
    base_version_id TEXT NOT NULL DEFAULT '',
    target_version_id TEXT NOT NULL DEFAULT '',
    proposed_text TEXT NOT NULL DEFAULT '',
    proposed_text_sha256 TEXT NOT NULL DEFAULT '',
    proposed_by_agent_id TEXT NOT NULL DEFAULT '',
    proposed_by_kind TEXT NOT NULL DEFAULT '',
    rationale TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'proposed',
    approval_route TEXT NOT NULL,
    next_actor_kind TEXT NOT NULL DEFAULT '',
    requires_user_approval INTEGER NOT NULL DEFAULT 0,
    architect_approver_id TEXT NOT NULL DEFAULT '',
    architect_approved_at REAL,
    user_task_id TEXT NOT NULL DEFAULT '',
    user_approved_at REAL,
    lint_warnings_json TEXT NOT NULL DEFAULT '[]',
    resolved_by_kind TEXT NOT NULL DEFAULT '',
    resolved_by_id TEXT NOT NULL DEFAULT '',
    resolved_at REAL,
    resolution_note TEXT NOT NULL DEFAULT '',
    applied_version_id TEXT NOT NULL DEFAULT '',
    applied_at REAL,
    idempotency_key TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_behavior_overlay_proposals_scope_status
    ON behavior_overlay_proposals(scope_kind, scope_group, scope_key, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_behavior_overlay_proposals_route
    ON behavior_overlay_proposals(approval_route, status);
CREATE INDEX IF NOT EXISTS idx_behavior_overlay_proposals_next_actor
    ON behavior_overlay_proposals(next_actor_kind, status);
CREATE INDEX IF NOT EXISTS idx_behavior_overlay_proposals_user_task
    ON behavior_overlay_proposals(user_task_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_behavior_overlay_proposals_idempotency
    ON behavior_overlay_proposals(proposed_by_agent_id, idempotency_key, scope_kind, scope_group, scope_key)
    WHERE idempotency_key != '';

CREATE TABLE IF NOT EXISTS behavior_overlay_activations (
    id TEXT PRIMARY KEY,
    scope_kind TEXT NOT NULL DEFAULT 'agent',
    scope_group TEXT NOT NULL DEFAULT '',
    scope_key TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT '',
    previous_version_id TEXT NOT NULL DEFAULT '',
    active_version_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL DEFAULT '',
    actor_kind TEXT NOT NULL DEFAULT '',
    actor_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_behavior_overlay_activations_scope
    ON behavior_overlay_activations(scope_kind, scope_group, scope_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_behavior_overlay_activations_proposal
    ON behavior_overlay_activations(proposal_id);

CREATE TABLE IF NOT EXISTS mcp_idempotency (
    idempotency_key TEXT PRIMARY KEY,
    surface         TEXT NOT NULL DEFAULT '',
    tool_name       TEXT NOT NULL DEFAULT '',
    request_hash    TEXT NOT NULL DEFAULT '',
    response_json   TEXT NOT NULL DEFAULT '{}',
    response_bytes  INTEGER NOT NULL DEFAULT 0,
    expires_at      REAL NOT NULL DEFAULT 0,
    compacted_at    REAL NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcp_idempotency_tool
    ON mcp_idempotency(surface, tool_name, updated_at DESC);

CREATE TABLE IF NOT EXISTS failed_writes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    endpoint        TEXT NOT NULL DEFAULT '',
    method          TEXT NOT NULL DEFAULT 'POST',
    surface         TEXT NOT NULL DEFAULT '',
    tool_name       TEXT NOT NULL DEFAULT '',
    caller_id       TEXT NOT NULL DEFAULT '',
    payload_json    TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_failed_writes_created
    ON failed_writes(created_at);

CREATE TABLE IF NOT EXISTS command_receipts (
    idempotency_key TEXT PRIMARY KEY,
    surface         TEXT NOT NULL DEFAULT '',
    command_name    TEXT NOT NULL DEFAULT '',
    request_hash    TEXT NOT NULL DEFAULT '',
    response_json   TEXT NOT NULL DEFAULT 'null',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_command_receipts_command
    ON command_receipts(surface, command_name, updated_at DESC);

CREATE TABLE IF NOT EXISTS mcp_health_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  REAL NOT NULL,
    surface    TEXT NOT NULL DEFAULT '',
    tool_name  TEXT NOT NULL DEFAULT '',
    event      TEXT NOT NULL DEFAULT '',
    error      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_mcp_health_events_recent
    ON mcp_health_events(timestamp DESC, surface, tool_name, event);

CREATE TABLE IF NOT EXISTS perceived_empty_episodes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         REAL NOT NULL,
    cell_id           TEXT NOT NULL DEFAULT '',
    group_name        TEXT NOT NULL DEFAULT '',
    agent_name        TEXT NOT NULL DEFAULT '',
    session_id        TEXT NOT NULL DEFAULT '',
    transcript_path   TEXT NOT NULL DEFAULT '',
    trigger_reason    TEXT NOT NULL DEFAULT '',
    confidence        TEXT NOT NULL DEFAULT '',
    threshold_n       INTEGER NOT NULL DEFAULT 0,
    window_seconds    INTEGER NOT NULL DEFAULT 0,
    tool_calls_json   TEXT NOT NULL DEFAULT '[]',
    created_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_perceived_empty_episodes_recent
    ON perceived_empty_episodes(timestamp DESC, cell_id);
"""

_LEGACY_ENGINEER_PREFIX = "wea" + "ver"


def _legacy_engineer_name(suffix: str) -> str:
    return f"{_LEGACY_ENGINEER_PREFIX}_{suffix}"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(row)


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _table_exists(conn, table):
        return False
    try:
        conn.execute(f"SELECT {column} FROM {table} LIMIT 0")
        return True
    except sqlite3.OperationalError:
        return False


def _behavior_overlay_v2_indexes(conn: sqlite3.Connection) -> None:
    """Ensure behavior-overlay v2 indexes and remove v1 index names."""
    for name in (
            "idx_behavior_overlay_versions_agent",
            "idx_behavior_overlay_proposals_agent_status",
            "idx_behavior_overlay_activations_agent",
            "idx_behavior_overlay_proposals_idempotency"):
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_behavior_overlay_versions_scope "
        "ON behavior_overlay_versions(scope_kind, scope_group, scope_key, "
        "version_number DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_behavior_overlay_versions_proposal "
        "ON behavior_overlay_versions(source_proposal_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_behavior_overlay_proposals_scope_status "
        "ON behavior_overlay_proposals(scope_kind, scope_group, scope_key, "
        "status, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_behavior_overlay_proposals_route "
        "ON behavior_overlay_proposals(approval_route, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_behavior_overlay_proposals_next_actor "
        "ON behavior_overlay_proposals(next_actor_kind, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_behavior_overlay_proposals_user_task "
        "ON behavior_overlay_proposals(user_task_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_behavior_overlay_proposals_idempotency "
        "ON behavior_overlay_proposals(proposed_by_agent_id, idempotency_key, "
        "scope_kind, scope_group, scope_key) WHERE idempotency_key != ''"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_behavior_overlay_activations_scope "
        "ON behavior_overlay_activations(scope_kind, scope_group, scope_key, "
        "created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_behavior_overlay_activations_proposal "
        "ON behavior_overlay_activations(proposal_id)"
    )


def _agent_group_expr(agent_column: str) -> str:
    return (
        f"COALESCE((SELECT group_name FROM agents WHERE id={agent_column}), '')"
    )


def _migrate_behavior_overlay_scope_schema(conn: sqlite3.Connection) -> None:
    """Migrate Phase-1 agent-keyed overlay tables to scope-keyed v2.

    ``behavior_overlay_active`` changes primary key shape, so this intentionally
    performs a real table rebuild instead of an ``ALTER ADD COLUMN`` shortcut.
    The helper is idempotent: if all tables already expose scope columns it
    only re-ensures indexes.
    """
    required = (
        "behavior_overlay_versions",
        "behavior_overlay_active",
        "behavior_overlay_proposals",
        "behavior_overlay_activations",
    )
    if not all(_table_exists(conn, table) for table in required):
        return
    already_v2 = all(
        _column_exists(conn, table, "scope_kind")
        and _column_exists(conn, table, "scope_group")
        and _column_exists(conn, table, "scope_key")
        for table in required
    )
    if already_v2:
        _behavior_overlay_v2_indexes(conn)
        conn.commit()
        return

    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        for table in required:
            conn.execute(f"DROP TABLE IF EXISTS __torque_{table}_v2")
        conn.execute("""
            CREATE TABLE __torque_behavior_overlay_versions_v2 (
                id TEXT PRIMARY KEY,
                scope_kind TEXT NOT NULL DEFAULT 'agent',
                scope_group TEXT NOT NULL DEFAULT '',
                scope_key TEXT NOT NULL DEFAULT '',
                agent_id TEXT NOT NULL DEFAULT '',
                version_number INTEGER NOT NULL,
                parent_version_id TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                author_agent_id TEXT NOT NULL DEFAULT '',
                author_kind TEXT NOT NULL DEFAULT '',
                rationale TEXT NOT NULL DEFAULT '',
                approver_id TEXT NOT NULL DEFAULT '',
                approver_kind TEXT NOT NULL DEFAULT '',
                source_proposal_id TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(scope_kind, scope_group, scope_key, version_number)
            )
        """)
        conn.execute("""
            INSERT OR REPLACE INTO __torque_behavior_overlay_versions_v2 (
                id, scope_kind, scope_group, scope_key, agent_id,
                version_number, parent_version_id, text, text_sha256,
                author_agent_id, author_kind, rationale, approver_id,
                approver_kind, source_proposal_id, created_at, metadata_json
            )
            SELECT
                id, 'agent', """ + _agent_group_expr("agent_id") + """,
                agent_id, agent_id, version_number, parent_version_id, text,
                text_sha256, author_agent_id, author_kind, rationale,
                approver_id, approver_kind, source_proposal_id, created_at,
                metadata_json
            FROM behavior_overlay_versions
        """)

        conn.execute("""
            CREATE TABLE __torque_behavior_overlay_active_v2 (
                scope_kind TEXT NOT NULL DEFAULT 'agent',
                scope_group TEXT NOT NULL DEFAULT '',
                scope_key TEXT NOT NULL,
                agent_id TEXT NOT NULL DEFAULT '',
                active_version_id TEXT NOT NULL,
                updated_at REAL NOT NULL,
                updated_by_kind TEXT NOT NULL DEFAULT '',
                updated_by_id TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(scope_kind, scope_group, scope_key)
            )
        """)
        conn.execute("""
            INSERT OR REPLACE INTO __torque_behavior_overlay_active_v2 (
                scope_kind, scope_group, scope_key, agent_id,
                active_version_id, updated_at, updated_by_kind,
                updated_by_id, reason
            )
            SELECT
                'agent', """ + _agent_group_expr("agent_id") + """,
                agent_id, agent_id, active_version_id, updated_at,
                updated_by_kind, updated_by_id, reason
            FROM behavior_overlay_active
        """)

        conn.execute("""
            CREATE TABLE __torque_behavior_overlay_proposals_v2 (
                id TEXT PRIMARY KEY,
                scope_kind TEXT NOT NULL DEFAULT 'agent',
                scope_group TEXT NOT NULL DEFAULT '',
                scope_key TEXT NOT NULL DEFAULT '',
                agent_id TEXT NOT NULL DEFAULT '',
                target_kind TEXT NOT NULL DEFAULT '',
                proposal_type TEXT NOT NULL DEFAULT 'set_text',
                base_version_id TEXT NOT NULL DEFAULT '',
                target_version_id TEXT NOT NULL DEFAULT '',
                proposed_text TEXT NOT NULL DEFAULT '',
                proposed_text_sha256 TEXT NOT NULL DEFAULT '',
                proposed_by_agent_id TEXT NOT NULL DEFAULT '',
                proposed_by_kind TEXT NOT NULL DEFAULT '',
                rationale TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'proposed',
                approval_route TEXT NOT NULL,
                next_actor_kind TEXT NOT NULL DEFAULT '',
                requires_user_approval INTEGER NOT NULL DEFAULT 0,
                architect_approver_id TEXT NOT NULL DEFAULT '',
                architect_approved_at REAL,
                user_task_id TEXT NOT NULL DEFAULT '',
                user_approved_at REAL,
                lint_warnings_json TEXT NOT NULL DEFAULT '[]',
                resolved_by_kind TEXT NOT NULL DEFAULT '',
                resolved_by_id TEXT NOT NULL DEFAULT '',
                resolved_at REAL,
                resolution_note TEXT NOT NULL DEFAULT '',
                applied_version_id TEXT NOT NULL DEFAULT '',
                applied_at REAL,
                idempotency_key TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        conn.execute("""
            INSERT OR REPLACE INTO __torque_behavior_overlay_proposals_v2 (
                id, scope_kind, scope_group, scope_key, agent_id, target_kind,
                proposal_type, base_version_id, target_version_id,
                proposed_text, proposed_text_sha256, proposed_by_agent_id,
                proposed_by_kind, rationale, status, approval_route,
                next_actor_kind, requires_user_approval,
                architect_approver_id, architect_approved_at, user_task_id,
                user_approved_at, lint_warnings_json, resolved_by_kind,
                resolved_by_id, resolved_at, resolution_note,
                applied_version_id, applied_at, idempotency_key,
                created_at, updated_at
            )
            SELECT
                id, 'agent', """ + _agent_group_expr("agent_id") + """,
                agent_id, agent_id, target_kind, proposal_type,
                base_version_id, target_version_id, proposed_text,
                proposed_text_sha256, proposed_by_agent_id, proposed_by_kind,
                rationale, status, approval_route, next_actor_kind,
                requires_user_approval, architect_approver_id,
                architect_approved_at, user_task_id, user_approved_at,
                lint_warnings_json, resolved_by_kind, resolved_by_id,
                resolved_at, resolution_note, applied_version_id, applied_at,
                idempotency_key, created_at, updated_at
            FROM behavior_overlay_proposals
        """)

        conn.execute("""
            CREATE TABLE __torque_behavior_overlay_activations_v2 (
                id TEXT PRIMARY KEY,
                scope_kind TEXT NOT NULL DEFAULT 'agent',
                scope_group TEXT NOT NULL DEFAULT '',
                scope_key TEXT NOT NULL DEFAULT '',
                agent_id TEXT NOT NULL DEFAULT '',
                previous_version_id TEXT NOT NULL DEFAULT '',
                active_version_id TEXT NOT NULL,
                proposal_id TEXT NOT NULL DEFAULT '',
                actor_kind TEXT NOT NULL DEFAULT '',
                actor_id TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            )
        """)
        conn.execute("""
            INSERT OR REPLACE INTO __torque_behavior_overlay_activations_v2 (
                id, scope_kind, scope_group, scope_key, agent_id,
                previous_version_id, active_version_id, proposal_id,
                actor_kind, actor_id, action, reason, created_at
            )
            SELECT
                id, 'agent', """ + _agent_group_expr("agent_id") + """,
                agent_id, agent_id, previous_version_id, active_version_id,
                proposal_id, actor_kind, actor_id, action, reason, created_at
            FROM behavior_overlay_activations
        """)

        for table in required:
            conn.execute(f"DROP TABLE {table}")
            conn.execute(
                f"ALTER TABLE __torque_{table}_v2 RENAME TO {table}"
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
    _behavior_overlay_v2_indexes(conn)
    conn.commit()


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _default_literal(default) -> str:
    text = str(default or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _captured_table_supporting_sql(
        conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE tbl_name=? AND type IN ('index', 'trigger') "
        "AND sql IS NOT NULL ORDER BY type, name",
        (table,),
    ).fetchall()
    return [str(sql) for _type, _name, sql in rows if sql]


def _rebuild_table_with_column_defaults(
        conn: sqlite3.Connection, table: str,
        defaults: dict[str, str]) -> bool:
    columns = conn.execute(
        f"PRAGMA table_info({_quote_ident(table)})"
    ).fetchall()
    if not columns:
        return False

    wanted = {str(k): str(v) for k, v in defaults.items()}
    existing = {str(row[1]): row[4] for row in columns}
    needs_rebuild = any(
        col in existing and _default_literal(existing[col]) != _default_literal(default)
        for col, default in wanted.items()
    )
    if not needs_rebuild:
        return False

    defs: list[str] = []
    pk_columns: list[tuple[int, str]] = []
    for _cid, name, col_type, notnull, default, pk in columns:
        name = str(name)
        parts = [_quote_ident(name)]
        if col_type:
            parts.append(str(col_type))
        if notnull:
            parts.append("NOT NULL")
        default_sql = wanted.get(name, default)
        if default_sql is not None:
            parts.append(f"DEFAULT {default_sql}")
        defs.append(" ".join(parts))
        if pk:
            pk_columns.append((int(pk), name))
    if pk_columns:
        ordered = ", ".join(
            _quote_ident(name) for _pk, name in sorted(pk_columns)
        )
        defs.append(f"PRIMARY KEY ({ordered})")

    new_table = f"__torque_{table}_defaults_migration"
    create_sql = (
        f"CREATE TABLE {_quote_ident(new_table)} (\n    "
        + ",\n    ".join(defs)
        + "\n)"
    )
    supporting_sql = _captured_table_supporting_sql(conn, table)
    copy_columns = ", ".join(_quote_ident(row[1]) for row in columns)

    try:
        conn.execute("BEGIN")
        conn.execute(f"DROP TABLE IF EXISTS {_quote_ident(new_table)}")
        conn.execute(create_sql)
        conn.execute(
            f"INSERT INTO {_quote_ident(new_table)} ({copy_columns}) "
            f"SELECT {copy_columns} FROM {_quote_ident(table)}"
        )
        conn.execute(f"DROP TABLE {_quote_ident(table)}")
        conn.execute(
            f"ALTER TABLE {_quote_ident(new_table)} "
            f"RENAME TO {_quote_ident(table)}"
        )
        for sql in supporting_sql:
            conn.execute(sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True


def _ensure_terminal_backend_defaults_to_pty(
        conn: sqlite3.Connection) -> None:
    """Migrate persisted terminal backend defaults and legacy rows to pty."""
    legacy_backend = "".join(("ite", "rm2"))
    for table, col in (
        ("agents", "terminal_backend"),
        ("group_settings", "default_terminal_backend"),
    ):
        try:
            conn.execute(f"SELECT {col} FROM {table} LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN "
                f"{col} TEXT NOT NULL DEFAULT 'pty'")
            conn.commit()

    conn.execute(
        "UPDATE agents SET terminal_backend=? "
        "WHERE terminal_backend=?",
        ("pty", legacy_backend),
    )
    conn.execute(
        "UPDATE group_settings SET default_terminal_backend=? "
        "WHERE default_terminal_backend=?",
        ("pty", legacy_backend),
    )
    if conn.in_transaction:
        conn.commit()

    for table, col in (
        ("agents", "terminal_backend"),
        ("group_settings", "default_terminal_backend"),
    ):
        _rebuild_table_with_column_defaults(conn, table, {col: "'pty'"})


def _rename_table_if_needed(
        conn: sqlite3.Connection, old_name: str, new_name: str) -> bool:
    if not _table_exists(conn, old_name) or _table_exists(conn, new_name):
        return False
    conn.execute(f"ALTER TABLE {old_name} RENAME TO {new_name}")
    return True


def _rename_column_if_needed(
        conn: sqlite3.Connection, table: str,
        old_name: str, new_name: str) -> bool:
    if (
            not _column_exists(conn, table, old_name)
            or _column_exists(conn, table, new_name)):
        return False
    conn.execute(
        f"ALTER TABLE {table} RENAME COLUMN {old_name} TO {new_name}"
    )
    return True


def _drop_index_if_exists(conn: sqlite3.Connection, index_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    ).fetchone()
    if not row:
        return False
    conn.execute(f"DROP INDEX IF EXISTS {index_name}")
    return True


def _migrate_legacy_engineer_schema_names(conn: sqlite3.Connection) -> None:
    changed = False
    for suffix in ("settings", "task_log", "journal"):
        changed = (
            _rename_table_if_needed(
                conn,
                _legacy_engineer_name(suffix),
                f"engineer_{suffix}",
            )
            or changed
        )

    for suffix in (
        "provider",
        "boot_command",
        "model",
        "reasoning_effort",
        "directory",
        "profile",
        "shell",
        "tab_color",
    ):
        changed = (
            _rename_column_if_needed(
                conn,
                "engineer_settings",
                _legacy_engineer_name(suffix),
                f"engineer_{suffix}",
            )
            or changed
        )

    changed = (
        _rename_column_if_needed(
            conn,
            "group_settings",
            _legacy_engineer_name("agent_id"),
            "engineer_agent_id",
        )
        or changed
    )
    changed = (
        _rename_column_if_needed(
            conn,
            "auto_dispatch_queue",
            _legacy_engineer_name("owner_id"),
            "engineer_owner_id",
        )
        or changed
    )
    changed = (
        _rename_column_if_needed(
            conn,
            "agents",
            "created_by_" + _LEGACY_ENGINEER_PREFIX + "_id",
            "created_by_engineer_id",
        )
        or changed
    )
    changed = (
        _rename_column_if_needed(
            conn,
            "board_tasks",
            _legacy_engineer_name("owner_id"),
            "engineer_owner_id",
        )
        or changed
    )
    for suffix in (
        "task_log_group",
        "journal_group",
        "journal_group_author",
    ):
        changed = (
            _drop_index_if_exists(conn, "idx_" + _legacy_engineer_name(suffix))
            or changed
        )
    if changed:
        conn.commit()


def _migrate_legacy_engineer_payload_names(conn: sqlite3.Connection) -> None:
    legacy_label = f"torque:{_LEGACY_ENGINEER_PREFIX}-message"
    engineer_label = "torque:engineer-message"
    legacy_action = _legacy_engineer_name("message")
    changed = False

    try:
        rows = conn.execute(
            "SELECT id, labels, messages FROM board_tasks"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for task_id, labels_json, messages_json in rows:
        labels_changed = False
        try:
            labels = json.loads(labels_json or "[]")
        except (json.JSONDecodeError, TypeError):
            labels = []
        if isinstance(labels, list):
            next_labels = []
            for label in labels:
                if label == legacy_label:
                    next_labels.append(engineer_label)
                    labels_changed = True
                else:
                    next_labels.append(label)
        else:
            next_labels = labels

        messages_changed = False
        try:
            messages = json.loads(messages_json or "[]")
        except (json.JSONDecodeError, TypeError):
            messages = []
        if isinstance(messages, list):
            next_messages = []
            for message in messages:
                if isinstance(message, dict):
                    message = dict(message)
                    if message.get("action") == legacy_action:
                        message["action"] = "engineer_message"
                        messages_changed = True
                next_messages.append(message)
        else:
            next_messages = messages

        if labels_changed or messages_changed:
            conn.execute(
                "UPDATE board_tasks SET labels=?, messages=? WHERE id=?",
                (
                    json.dumps(next_labels),
                    json.dumps(next_messages),
                    task_id,
                ),
            )
            changed = True

    try:
        conn.execute(
            "UPDATE panel_events SET kind=? WHERE kind=?",
            ("engineer_message", legacy_action),
        )
        changed = changed or bool(conn.total_changes)
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "UPDATE agent_messages SET action=? WHERE action=?",
            ("engineer_message", legacy_action),
        )
        changed = changed or bool(conn.total_changes)
    except sqlite3.OperationalError:
        pass

    try:
        ui_rows = conn.execute("SELECT key, value FROM ui_state").fetchall()
    except sqlite3.OperationalError:
        ui_rows = []
    for key, value in ui_rows:
        if value == _LEGACY_ENGINEER_PREFIX:
            conn.execute(
                "UPDATE ui_state SET value=? WHERE key=?",
                ("engineer", key),
            )
            changed = True
            continue
        if not isinstance(value, str) or _LEGACY_ENGINEER_PREFIX not in value:
            continue
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            continue

        def rewrite(obj):
            nonlocal changed
            if obj == _LEGACY_ENGINEER_PREFIX:
                changed = True
                return "engineer"
            if isinstance(obj, list):
                return [rewrite(item) for item in obj]
            if isinstance(obj, dict):
                next_obj = {}
                for key, item in obj.items():
                    next_key = key
                    if key == _LEGACY_ENGINEER_PREFIX:
                        next_key = "engineer"
                        changed = True
                    next_obj[next_key] = rewrite(item)
                return next_obj
            return obj

        rewritten = rewrite(parsed)
        if rewritten != parsed:
            conn.execute(
                "UPDATE ui_state SET value=? WHERE key=?",
                (json.dumps(rewritten), key),
            )

    if changed:
        conn.commit()


def _reconcile_legacy_schema(conn: sqlite3.Connection, backfill_agent_history):
    """Create/reconcile the pre-ledger schema used by migration 1.

    This function intentionally preserves the historical idempotent behavior,
    including its intermediate commits.  A process interrupted during initial
    adoption or a compatibility repair retries the reconciliation on the next
    boot.  New migrations must not be added here; they belong in
    ``SCHEMA_MIGRATIONS`` and receive an atomic transaction boundary from
    ``_apply_schema_migrations``.
    """
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate_legacy_engineer_schema_names(conn)
    _migrate_behavior_overlay_scope_schema(conn)
    conn.executescript(_SCHEMA_SQL)
    _ensure_ai_index_schema(conn)
    _ensure_ai_summary_schema(conn)
    _ensure_mcp_idempotency_schema(conn)
    _ensure_initiatives_schema(conn)
    _ensure_areas_schema(conn)
    _ensure_thinking_schema(conn)
    _ensure_idea_brief_schema(conn)
    conn.commit()
    _migrate_behavior_overlay_scope_schema(conn)
    # Migrate: add terminal_close_on_disconnect column
    try:
        conn.execute(
            "SELECT terminal_close_on_disconnect FROM group_settings "
            "LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "terminal_close_on_disconnect INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    # Migrate: add memory retention columns
    try:
        conn.execute(
            "SELECT retention_kind FROM memory_entries LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE memory_entries ADD COLUMN "
            "retention_kind TEXT NOT NULL DEFAULT 'durable'")
        conn.commit()
    try:
        conn.execute(
            "SELECT expires_at FROM memory_entries LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE memory_entries ADD COLUMN expires_at REAL")
        conn.commit()
    rebuild_memory_retention_indexes(conn)
    # Migrate: add xterm scrollback setting cache column. Global settings
    # remain persisted as key/value rows; this column keeps the schema
    # migration explicit for installations created before the setting existed.
    try:
        conn.execute("SELECT xterm_scrollback FROM global_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE global_settings ADD COLUMN "
            "xterm_scrollback INTEGER NOT NULL DEFAULT 2000")
        conn.commit()
    # Migrate: add action_name and action_vars columns to board_tasks
    for col, default in [("action_name", "''"),
                         ("action_vars", "'{}'"),
                         ("agent_template", "''")]:
        try:
            conn.execute(
                f"SELECT {col} FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE board_tasks ADD COLUMN {col} "
                f"TEXT NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: add pipeline columns to board_tasks
    for col, default in [("parent_task_id", "''"),
                         ("pipeline_depth", "0"),
                         ("pipeline_root_id", "''")]:
        try:
            conn.execute(
                f"SELECT {col} FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            col_type = "INTEGER" if col == "pipeline_depth" else "TEXT"
            conn.execute(
                f"ALTER TABLE board_tasks ADD COLUMN {col} "
                f"{col_type} NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: add reply_agent_id column to board_tasks
    try:
        conn.execute(
            "SELECT reply_agent_id FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN reply_agent_id "
            "TEXT NOT NULL DEFAULT ''")
        conn.commit()
    # Migrate: add description column to board_tasks
    try:
        conn.execute(
            "SELECT description FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN description "
            "TEXT NOT NULL DEFAULT ''")
        conn.commit()
    # Migrate: add status column to board_tasks
    try:
        conn.execute("SELECT status FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN status "
            "TEXT NOT NULL DEFAULT ''")
        conn.commit()
    # Migrate: add messages column to board_tasks
    try:
        conn.execute("SELECT messages FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN messages "
            "TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    # Migrate: add inline engineer→agent thread column to board_tasks
    board_task_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(board_tasks)")
    }
    if "messages_thread" not in board_task_columns:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN messages_thread "
            "TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    # Migrate: add scheduled_at column to board_tasks
    try:
        conn.execute(
            "SELECT scheduled_at FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN scheduled_at "
            "TEXT NOT NULL DEFAULT ''")
        conn.commit()
    # Migrate: add queued-vs-live task dispatch state.
    try:
        conn.execute("SELECT dispatch_state FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN dispatch_state "
            "TEXT NOT NULL DEFAULT 'queued'")
        conn.execute(
            "UPDATE board_tasks SET dispatch_state='live' "
            "WHERE COALESCE(agent_id, '') != '' OR lane='In Progress'")
        conn.commit()
    # Migrate: add depends_on column to board_tasks
    try:
        conn.execute(
            "SELECT depends_on FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN depends_on "
            "TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    # Migrate: add attachments column to board_tasks
    try:
        conn.execute(
            "SELECT attachments FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN attachments "
            "TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    # Migrate: add board-sync metadata column to board_tasks.
    try:
        conn.execute("SELECT board_sync FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN board_sync "
            "TEXT NOT NULL DEFAULT '{}'")
        conn.commit()
    # Migrate: add task health columns to board_tasks
    for col, default in [
        ("health_state", "'healthy'"),
        ("health_since", "''"),
        ("health_details", "'{}'"),
    ]:
        try:
            conn.execute(
                f"SELECT {col} FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE board_tasks ADD COLUMN {col} "
                f"TEXT NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: add artifacts column to board_tasks
    try:
        conn.execute(
            "SELECT artifacts FROM board_tasks LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE board_tasks ADD COLUMN artifacts "
            "TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    # Migrate: add verification columns to board_tasks
    for col, default in [
        ("lane_entered_at", "''"),
        ("verification_mode", "''"),
        ("verification_state", "''"),
        ("verification_notes", "''"),
        ("verification_updated_at", "''"),
        ("verification_updated_by", "''"),
        ("verification_summary", "'{}'"),
        ("completion_evidence", "'{}'"),
        ("worktree_boundary", "'{}'"),
        ("resume_after_boundary_task_id", "''"),
        ("archived_at", "''"),
        ("archived_from_lane", "''"),
    ]:
        try:
            conn.execute(
                f"SELECT {col} FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE board_tasks ADD COLUMN {col} "
                f"TEXT NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: add deliverable contract columns to board_tasks
    for col, col_type, default in [
        ("deliverable_required", "INTEGER", "0"),
        ("deliverable_type", "TEXT", "''"),
        ("deliverable_format", "TEXT", "''"),
        ("deliverable_artifact_title", "TEXT", "''"),
    ]:
        try:
            conn.execute(f"SELECT {col} FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE board_tasks ADD COLUMN {col} "
                f"{col_type} NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: add mandatory-review contract columns to board_tasks (TORQUE:256)
    for col, col_type, default in [
        ("requires_review", "INTEGER", "0"),
        ("pre_approved_by", "TEXT", "''"),
    ]:
        try:
            conn.execute(f"SELECT {col} FROM board_tasks LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE board_tasks ADD COLUMN {col} "
                f"{col_type} NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: drop assignee column from board_tasks
    try:
        conn.execute(
            "SELECT assignee FROM board_tasks LIMIT 0")
        conn.execute(
            "ALTER TABLE board_tasks DROP COLUMN assignee")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already gone
    # Migrate: add tasks_dispatched column to agents
    for col, col_type, default in [
        ("tasks_dispatched", "INTEGER", "0"),
        ("runner_backend", "TEXT", "'pty'"),
        ("worktree_base_dir", "TEXT", "'.torque/worktrees'"),
        ("worktree_auto_checkpoint", "INTEGER", "0"),
        ("checkpoint_on_progress", "INTEGER", "0"),
        ("worktree_merge_squash", "INTEGER", "1"),
        ("session_resume", "INTEGER", "1"),
        ("idle_timeout", "INTEGER", "0"),
    ]:
        try:
            conn.execute(f"SELECT {col} FROM agents LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE agents ADD COLUMN {col} "
                f"{col_type} NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: add default_agent_template column
    try:
        conn.execute(
            "SELECT default_agent_template FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "default_agent_template TEXT NOT NULL DEFAULT ''")
        conn.commit()
    # Migrate: add board_default_* columns to group_settings
    for col, default in [("board_default_labels", "'[]'"),
                         ("board_default_lane", "''"),
                         ("board_default_action", "''")]:
        try:
            conn.execute(
                f"SELECT {col} FROM group_settings LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE group_settings ADD COLUMN "
                f"{col} TEXT NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: add board-sync group settings.
    for col, col_type, default in (
        ("board_sync_provider", "TEXT", "'none'"),
        ("board_sync_enabled", "INTEGER", "0"),
        ("board_sync_github", "TEXT", "'{}'"),
    ):
        try:
            conn.execute(f"SELECT {col} FROM group_settings LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE group_settings ADD COLUMN {col} "
                f"{col_type} NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: add env_file columns to group_settings
    for col in ("env_file", "agent_env_file", "terminal_env_file"):
        try:
            conn.execute(
                f"SELECT {col} FROM group_settings LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE group_settings ADD COLUMN "
                f"{col} TEXT NOT NULL DEFAULT ''")
            conn.commit()
    # Migrate: add worktree_symlinks column to group_settings
    try:
        conn.execute(
            "SELECT worktree_symlinks FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "worktree_symlinks TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    # Migrate: add worktree_submodules column to group_settings
    try:
        conn.execute(
            "SELECT worktree_submodules FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "worktree_submodules TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    # Migrate: add worktree_symlink_gitignored_paths column to group_settings
    try:
        conn.execute(
            "SELECT worktree_symlink_gitignored_paths "
            "FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "worktree_symlink_gitignored_paths "
            "INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    # Migrate: add worktree_merge_cleanup column to group_settings
    try:
        conn.execute(
            "SELECT worktree_merge_cleanup FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "worktree_merge_cleanup TEXT NOT NULL DEFAULT 'keep'")
        conn.commit()
    # Migrate: add worktree_merge_preserve_diff column to group_settings
    try:
        conn.execute(
            "SELECT worktree_merge_preserve_diff FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "worktree_merge_preserve_diff INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    # Migrate: add engineer_merge_mode column to group_settings
    try:
        conn.execute(
            "SELECT engineer_merge_mode FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "engineer_merge_mode TEXT NOT NULL DEFAULT 'pr'")
        conn.commit()
    # Migrate: add guidance_hint_cadence column to group_settings
    try:
        conn.execute(
            "SELECT guidance_hint_cadence FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "guidance_hint_cadence INTEGER NOT NULL DEFAULT 4")
        conn.commit()
    # Migrate: add engineer_hint_snoozes column to group_settings
    try:
        conn.execute(
            "SELECT engineer_hint_snoozes FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "engineer_hint_snoozes TEXT NOT NULL DEFAULT '{}'")
        conn.commit()
    # Migrate: add engineer_agent_id column to group_settings
    try:
        conn.execute(
            "SELECT engineer_agent_id FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "engineer_agent_id TEXT NOT NULL DEFAULT ''")
        conn.commit()
    # Migrate: add default_engineer_specializations column to group_settings
    try:
        conn.execute(
            "SELECT default_engineer_specializations FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "default_engineer_specializations TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    # Migrate: add architect settings columns to group_settings.
    for col, col_type, default in (
        ("architect_boot_command", "TEXT", "''"),
        ("architect_provider", "TEXT", "''"),
        ("architect_model", "TEXT", "''"),
        ("architect_reasoning_effort", "TEXT", "''"),
        ("architect_directory", "TEXT", "''"),
        ("architect_profile", "TEXT", "''"),
        ("architect_shell", "TEXT", "''"),
        ("architect_tab_color", "TEXT", "''"),
        ("architect_custom_instructions", "TEXT", "''"),
        ("architect_autonomy_mode", "TEXT", "'dispatch_after_confirm'"),
        ("architect_paused", "INTEGER", "0"),
        ("architect_digest_verbosity", "TEXT", "'balanced'"),
        (
            "architect_journal_checkpoint_frequency",
            "TEXT",
            "'every_10_actions'",
        ),
        (
            "architect_review_gate_thresholds",
            "TEXT",
            (
                "'{\"ship_direct_max\":50,"
                "\"review_default_above\":150,"
                "\"self_review_bypass_allowed\":false}'"
            ),
        ),
    ):
        try:
            conn.execute(f"SELECT {col} FROM group_settings LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE group_settings ADD COLUMN {col} "
                f"{col_type} NOT NULL DEFAULT {default}")
            conn.commit()
    # Migrate: add checkpoint_on_progress to group_settings + agents
    for table in ("group_settings", "agents"):
        try:
            conn.execute(
                f"SELECT checkpoint_on_progress FROM {table} LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN "
                "checkpoint_on_progress INTEGER NOT NULL DEFAULT 0")
            conn.commit()
    _ensure_terminal_backend_defaults_to_pty(conn)
    # Migrate: add pending_question column to engineer_settings
    try:
        conn.execute(
            "SELECT pending_question FROM engineer_settings LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute(
                "ALTER TABLE engineer_settings ADD COLUMN "
                "pending_question TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # table may not exist yet
    try:
        conn.execute(
            "SELECT pending_question_set_at FROM engineer_settings LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute(
                "ALTER TABLE engineer_settings ADD COLUMN "
                "pending_question_set_at REAL NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # table may not exist yet
    try:
        conn.execute(
            "SELECT pending_question_actor_id FROM engineer_settings LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute(
                "ALTER TABLE engineer_settings ADD COLUMN "
                "pending_question_actor_id TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # table may not exist yet
    for col in ("pending_note", "pending_note_kind"):
        try:
            conn.execute(
                f"SELECT {col} FROM engineer_settings LIMIT 0")
        except sqlite3.OperationalError:
            try:
                conn.execute(
                    f"ALTER TABLE engineer_settings ADD COLUMN "
                    f"{col} TEXT NOT NULL DEFAULT ''")
                conn.commit()
            except sqlite3.OperationalError:
                pass
    try:
        conn.execute(
            "SELECT pending_note_set_at FROM engineer_settings LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute(
                "ALTER TABLE engineer_settings ADD COLUMN "
                "pending_note_set_at REAL NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute(
            "SELECT pending_note_actor_id FROM engineer_settings LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute(
                "ALTER TABLE engineer_settings ADD COLUMN "
                "pending_note_actor_id TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute(
            "SELECT restrict_to_created_agents FROM engineer_settings LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute(
                "ALTER TABLE engineer_settings ADD COLUMN "
                "restrict_to_created_agents INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute(
            "SELECT engineer_can_override_worker_provider "
            "FROM engineer_settings LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute(
                "ALTER TABLE engineer_settings ADD COLUMN "
                "engineer_can_override_worker_provider "
                "INTEGER NOT NULL DEFAULT 1")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    # Migrate: add engineer_provider and launch override columns
    for col in ("engineer_provider", "engineer_boot_command",
                "engineer_model", "engineer_reasoning_effort",
                "engineer_directory", "engineer_profile",
                "engineer_shell", "engineer_tab_color"):
        try:
            conn.execute(
                f"SELECT {col} FROM engineer_settings LIMIT 0")
        except sqlite3.OperationalError:
            try:
                conn.execute(
                    f"ALTER TABLE engineer_settings ADD COLUMN "
                    f"{col} TEXT NOT NULL DEFAULT ''")
                conn.commit()
            except sqlite3.OperationalError:
                pass
    try:
        conn.execute(
            "SELECT heartbeat_interval FROM engineer_settings LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute(
                "ALTER TABLE engineer_settings ADD COLUMN "
                "heartbeat_interval INTEGER NOT NULL DEFAULT 300")
            conn.execute(
                "UPDATE engineer_settings "
                "SET heartbeat_interval = max_interval")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    for col, default in (
        ("default_worker_concurrency", "2"),
        ("autonomy_mode", "'dispatch_when_clear'"),
        ("wave_size_preference", "'small'"),
        ("same_agent_follow_up_preference", "'balanced'"),
        ("digest_verbosity", "'balanced'"),
        ("escalation_style", "'note_then_ask'"),
    ):
        try:
            conn.execute(
                f"SELECT {col} FROM engineer_settings LIMIT 0")
        except sqlite3.OperationalError:
            try:
                conn.execute(
                    f"ALTER TABLE engineer_settings ADD COLUMN "
                    f"{col} "
                    f"{'INTEGER' if col == 'default_worker_concurrency' else 'TEXT'} "
                    f"NOT NULL DEFAULT {default}")
                conn.commit()
            except sqlite3.OperationalError:
                pass
    try:
        conn.execute(
            "UPDATE engineer_settings "
            "SET enabled_events = ? "
            "WHERE enabled_events = ?",
            (
                json.dumps([
                    "agent_started",
                    "task_dispatched",
                    "task_derived",
                    "task_health_alert",
                ]),
                json.dumps([
                    "agent_started",
                    "task_dispatched",
                    "task_derived",
                ]),
            ),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass
    for col, default in (
        ("architect_digest", "0"),
        ("wake_on_digest", "0"),
        ("suppress_empty", "0"),
    ):
        try:
            conn.execute(
                f"SELECT {col} FROM agent_digest_settings LIMIT 0")
        except sqlite3.OperationalError:
            try:
                conn.execute(
                    f"ALTER TABLE agent_digest_settings ADD COLUMN "
                    f"{col} INTEGER NOT NULL DEFAULT {default}")
                conn.commit()
            except sqlite3.OperationalError:
                pass
    # Migrate: add architect digest tuning columns to group_settings.
    # ``architect_enabled_events`` defaults to '' (empty string), which the
    # runtime decodes as the quiet default: only server-enforced floor events
    # plus any per-architect opt-ins.
    for col, col_type, default in (
        ("architect_push_interval", "INTEGER", "300"),
        ("architect_max_interval", "INTEGER", "600"),
        ("architect_heartbeat_interval", "INTEGER", "0"),
        ("architect_suppress_empty_digests", "INTEGER", "1"),
        ("architect_enabled_events", "TEXT", "''"),
    ):
        try:
            conn.execute(f"SELECT {col} FROM group_settings LIMIT 0")
        except sqlite3.OperationalError:
            try:
                conn.execute(
                    f"ALTER TABLE group_settings ADD COLUMN {col} "
                    f"{col_type} NOT NULL DEFAULT {default}")
                conn.commit()
            except sqlite3.OperationalError:
                pass
    try:
        conn.execute(
            "SELECT engineer_behavior_requires_user_approval "
            "FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute(
                "ALTER TABLE group_settings ADD COLUMN "
                "engineer_behavior_requires_user_approval "
                "INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute(
            "SELECT engineer_owner_id FROM auto_dispatch_queue LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute(
                "ALTER TABLE auto_dispatch_queue ADD COLUMN "
                "engineer_owner_id TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute(
            "SELECT provider FROM auto_dispatch_queue LIMIT 0")
    except sqlite3.OperationalError:
        try:
            conn.execute(
                "ALTER TABLE auto_dispatch_queue ADD COLUMN "
                "provider TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    # Migrate: rename system labels with torque: prefix
    rows = conn.execute(
        "SELECT id, labels FROM board_tasks "
        "WHERE labels != '[]'"
    ).fetchall()
    migrated = False
    for tid, labels_json in rows:
        try:
            labels = json.loads(labels_json)
        except (json.JSONDecodeError, TypeError):
            continue
        new_labels = []
        changed = False
        for lb in labels:
            if lb in ("blocked", "derived", "error",
                      "human", "depth-limit"):
                new_labels.append("torque:" + lb)
                changed = True
            else:
                new_labels.append(lb)
        if changed:
            conn.execute(
                "UPDATE board_tasks SET labels = ? "
                "WHERE id = ?",
                (json.dumps(new_labels), tid))
            migrated = True
    if migrated:
        conn.commit()
    _migrate_legacy_engineer_payload_names(conn)
    # Backfill agent history for existing agents
    backfill_agent_history()
    # The versioned wrapper updates meta.schema_version after every successful
    # ledger migration.  Keep a baseline value for installations interrupted
    # after legacy reconciliation but before the ledger row is recorded.
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', '1') "
        "ON CONFLICT(key) DO NOTHING"
    )
    conn.commit()


def _migration_0001_legacy_reconciliation(
    conn: sqlite3.Connection,
    backfill_agent_history,
) -> None:
    _reconcile_legacy_schema(conn, backfill_agent_history)


def _migration_0002_versioned_ledger(
    conn: sqlite3.Connection,
    _backfill_agent_history,
) -> None:
    """Cut schema ownership over to the ordered migration ledger."""

    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("2",),
    )


def _migration_0003_board_task_routing_contract(
    conn: sqlite3.Connection,
    _backfill_agent_history,
) -> None:
    """Own board-task assignment and dispatch-hint columns in the ledger."""

    _ensure_columns(conn, "board_tasks", BOARD_TASK_ROUTING_COLUMNS)


def _migration_0004_agent_lifecycle_contract(
    conn: sqlite3.Connection,
    _backfill_agent_history,
) -> None:
    """Own agent lifecycle, class-audit, and deferred-loop schema."""

    _ensure_columns(conn, "agents", AGENT_LIFECYCLE_COLUMNS)
    _ensure_columns(
        conn,
        "agent_message_loops",
        AGENT_MESSAGE_LOOP_RUNTIME_COLUMNS,
    )
    _ensure_table(conn, "agent_class_audit", AGENT_CLASS_AUDIT_COLUMNS)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_class_audit_agent_created "
        "ON agent_class_audit(agent_id, created_at DESC)"
    )
    _ensure_table(conn, "decisions", DECISION_COLUMNS)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decisions_architect "
        "ON decisions(architect_id)"
    )
    _ensure_columns(
        conn,
        "pending_hires",
        PENDING_HIRE_LIFECYCLE_COLUMNS,
    )


def _migration_0005_agent_peer_messages(
    conn: sqlite3.Connection,
    _backfill_agent_history,
) -> None:
    """Own durable user, Engineer, and Architect peer-message storage."""

    _ensure_table(conn, "agent_peer_messages", AGENT_PEER_MESSAGE_COLUMNS)
    for sql in (
        "CREATE INDEX IF NOT EXISTS "
        "idx_agent_peer_messages_recipient_recent "
        "ON agent_peer_messages(recipient_id, created_at DESC, id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_agent_peer_messages_sender_recent "
        "ON agent_peer_messages(sender_id, created_at DESC, id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_agent_peer_messages_thread "
        "ON agent_peer_messages(thread_id, created_at ASC, id ASC)",
        "CREATE INDEX IF NOT EXISTS idx_agent_peer_messages_group_recent "
        "ON agent_peer_messages(group_name, created_at DESC, id DESC)",
    ):
        conn.execute(sql)


def _migration_0006_agent_activity_timestamps(
    conn: sqlite3.Connection,
    _backfill_agent_history,
) -> None:
    """Own split progress/heartbeat clocks and their one-time backfill."""

    _ensure_columns(conn, "agents", AGENT_ACTIVITY_TIMESTAMP_COLUMNS)
    marker = conn.execute(
        "SELECT value FROM meta WHERE key=?",
        (AGENT_ACTIVITY_TIMESTAMP_LEGACY_META_KEY,),
    ).fetchone()
    try:
        already_backfilled = int((marker or (0,))[0] or 0) >= 1
    except (TypeError, ValueError):
        already_backfilled = False
    if not already_backfilled:
        conn.execute(
            "UPDATE agents "
            "SET last_progress_at = CASE "
            "WHEN COALESCE(last_progress_at, 0) = 0 "
            "THEN COALESCE(last_activity_at, 0) ELSE last_progress_at END, "
            "last_heartbeat_at = CASE "
            "WHEN COALESCE(last_heartbeat_at, 0) = 0 "
            "THEN COALESCE(last_activity_at, 0) ELSE last_heartbeat_at END"
        )
        conn.execute(
            "UPDATE agents SET last_activity_at = "
            "MAX(COALESCE(last_activity_at, 0), "
            "COALESCE(last_progress_at, 0), "
            "COALESCE(last_heartbeat_at, 0))"
        )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, '1') "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (AGENT_ACTIVITY_TIMESTAMP_LEGACY_META_KEY,),
    )


def _migration_0007_agent_kind_schema(
    conn: sqlite3.Connection,
    _backfill_agent_history,
) -> None:
    """Own final agent-kind columns without bypassing legacy data gates."""

    _ensure_columns(conn, "agents", AGENT_KIND_COLUMNS)


def _migration_0008_kinds_legacy_stages_complete(
    conn: sqlite3.Connection,
    _backfill_agent_history,
) -> None:
    """Ledger the guarded kinds backfill/cleanup only after stage 4."""

    version = _legacy_kinds_version(conn)
    if version < KINDS_LEGACY_COMPLETE_VERSION:
        raise RuntimeError(
            "Kinds legacy stages are incomplete; expected stage 4"
        )


def _migration_0009_digest_settings_backfill(
    _conn: sqlite3.Connection,
    _backfill_agent_history,
) -> None:
    """Ledger the legacy group-to-agent digest settings backfill."""


def _migration_0010_canonical_task_ids(
    _conn: sqlite3.Connection,
    _backfill_agent_history,
) -> None:
    """Ledger canonical task-ID rewriting and reference reconciliation."""


def _migration_0011_engineer_journal_provenance(
    conn: sqlite3.Connection,
    _backfill_agent_history,
) -> None:
    """Own journal author/source provenance and lookup indexes."""

    _ensure_columns(
        conn,
        "engineer_journal",
        ENGINEER_JOURNAL_PROVENANCE_COLUMNS,
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_engineer_journal_group_author "
        "ON engineer_journal(group_name, author_cell_id, id DESC)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_engineer_journal_source_key "
        "ON engineer_journal(group_name, author_cell_id, source_key) "
        "WHERE source_key <> ''"
    )


def _migration_0012_persisted_entity_slugs(
    conn: sqlite3.Connection,
    _backfill_agent_history,
) -> None:
    """Own persisted slugs for agents, groups, and board tasks."""

    for table in SLUGGED_ENTITY_TABLES:
        _ensure_columns(conn, table, {"slug": "TEXT NOT NULL DEFAULT ''"})


def _migration_0013_group_provider_runtime_settings(
    conn: sqlite3.Connection,
    _backfill_agent_history,
) -> None:
    """Own group-level defaults for agent and worker provider launches."""

    _ensure_columns(conn, "group_settings", GROUP_PROVIDER_RUNTIME_COLUMNS)


SCHEMA_MIGRATIONS = (
    SchemaMigration(
        1,
        "legacy_schema_reconciliation",
        "legacy-reconciliation-baseline-2026-07-12",
        _migration_0001_legacy_reconciliation,
    ),
    SchemaMigration(
        2,
        "versioned_migration_ledger",
        "migration-ledger-v1",
        _migration_0002_versioned_ledger,
    ),
    SchemaMigration(
        3,
        "board_task_routing_contract",
        "board-task-routing-columns-v1",
        _migration_0003_board_task_routing_contract,
    ),
    SchemaMigration(
        4,
        "agent_lifecycle_contract",
        "agent-lifecycle-class-audit-v1",
        _migration_0004_agent_lifecycle_contract,
    ),
    SchemaMigration(
        5,
        "agent_peer_messages",
        "agent-peer-message-storage-v1",
        _migration_0005_agent_peer_messages,
    ),
    SchemaMigration(
        6,
        "agent_activity_timestamps",
        "agent-progress-heartbeat-clocks-v1",
        _migration_0006_agent_activity_timestamps,
    ),
    SchemaMigration(
        7,
        "agent_kind_schema",
        "agent-kind-columns-gated-v1",
        _migration_0007_agent_kind_schema,
    ),
    SchemaMigration(
        8,
        "kinds_legacy_stages_complete",
        "kinds-backfill-cleanup-stage4-v1",
        _migration_0008_kinds_legacy_stages_complete,
        phase="post_init",
        requires_runner=True,
    ),
    SchemaMigration(
        9,
        "digest_settings_backfill",
        "legacy-engineer-to-agent-digest-v1",
        _migration_0009_digest_settings_backfill,
        phase="post_init",
        requires_runner=True,
    ),
    SchemaMigration(
        10,
        "canonical_task_ids",
        "canonical-task-ids-and-references-v1",
        _migration_0010_canonical_task_ids,
        phase="post_init",
        requires_runner=True,
    ),
    SchemaMigration(
        11,
        "engineer_journal_provenance",
        "engineer-journal-provenance-v1",
        _migration_0011_engineer_journal_provenance,
        phase="post_init",
        repair_on_boot=True,
    ),
    SchemaMigration(
        12,
        "persisted_entity_slugs",
        "agent-group-task-slugs-v1",
        _migration_0012_persisted_entity_slugs,
        phase="post_init",
        repair_on_boot=True,
    ),
    SchemaMigration(
        13,
        "group_provider_runtime_settings",
        "group-agent-worker-provider-runtime-v1",
        _migration_0013_group_provider_runtime_settings,
        phase="post_init",
        repair_on_boot=True,
    ),
)


def _ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, "
        "name TEXT NOT NULL, "
        "checksum TEXT NOT NULL, "
        "applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    conn.commit()


def _applied_schema_migrations(conn: sqlite3.Connection) -> dict[int, dict]:
    rows = conn.execute(
        "SELECT version, name, checksum, applied_at "
        "FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {
        int(version): {
            "version": int(version),
            "name": str(name),
            "checksum": str(checksum),
            "applied_at": str(applied_at),
        }
        for version, name, checksum, applied_at in rows
    }


def _validate_migration_catalog(
    migrations: Iterable[SchemaMigration],
) -> tuple[SchemaMigration, ...]:
    ordered = tuple(sorted(migrations, key=lambda item: item.version))
    versions = [item.version for item in ordered]
    if not ordered or versions != list(range(1, max(versions) + 1)):
        raise RuntimeError(
            "Schema migrations must be contiguous and start at version 1"
        )
    if len({item.name for item in ordered}) != len(ordered):
        raise RuntimeError("Schema migration names must be unique")
    valid_phases = {"schema", "post_init"}
    invalid_phases = sorted({item.phase for item in ordered} - valid_phases)
    if invalid_phases:
        raise RuntimeError(
            f"Unknown schema migration phase(s): {invalid_phases}"
        )
    invalid_runner_migrations = [
        item.version
        for item in ordered
        if item.requires_runner and item.phase != "post_init"
    ]
    if invalid_runner_migrations:
        raise RuntimeError(
            "Runner-backed migrations must use the post_init phase: "
            f"{invalid_runner_migrations}"
        )
    seen_post_init = False
    for item in ordered:
        if item.phase == "post_init":
            seen_post_init = True
        elif seen_post_init:
            raise RuntimeError(
                "Schema migrations cannot follow post-init migrations"
            )
    return ordered


def _apply_schema_migrations(
    conn: sqlite3.Connection,
    backfill_agent_history,
    *,
    migrations: Iterable[SchemaMigration] = SCHEMA_MIGRATIONS,
    phases: Iterable[str] | None = None,
    versions: Iterable[int] | None = None,
    apply_overrides: dict[int, Callable] | None = None,
) -> None:
    """Apply missing migrations in order and reject ledger drift.

    Migration 1 wraps Torque's historical reconciliation routine, whose
    internal commits cannot be made atomic retroactively.  Every later
    migration is applied and ledgered in one SQLite transaction.
    """

    ordered = _validate_migration_catalog(migrations)
    selected_phases = None if phases is None else set(phases)
    selected_versions = None if versions is None else set(versions)
    overrides = dict(apply_overrides or {})
    _ensure_schema_migrations_table(conn)
    applied = _applied_schema_migrations(conn)
    catalog = {item.version: item for item in ordered}

    unknown = sorted(set(applied) - set(catalog))
    if unknown:
        raise RuntimeError(
            "Database schema is newer than this Torque build; unknown "
            f"migration version(s): {unknown}"
        )

    for version, record in applied.items():
        migration = catalog[version]
        if (
            record["name"] != migration.name
            or record["checksum"] != migration.checksum
        ):
            raise RuntimeError(
                "Schema migration ledger mismatch at version "
                f"{version}: expected {migration.name}"
            )

    applied_versions = sorted(applied)
    if applied_versions and applied_versions != list(
        range(1, max(applied_versions) + 1)
    ):
        raise RuntimeError("Schema migration ledger contains a version gap")

    completed = set(applied)
    for migration in ordered:
        if migration.version in applied:
            continue
        if selected_phases is not None and migration.phase not in selected_phases:
            continue
        if (
            selected_versions is not None
            and migration.version not in selected_versions
        ):
            continue
        missing_predecessors = set(range(1, migration.version)) - completed
        if missing_predecessors:
            raise RuntimeError(
                "Cannot apply schema migration "
                f"{migration.version} before version(s) "
                f"{sorted(missing_predecessors)}"
            )
        apply_migration = overrides.get(migration.version, migration.apply)
        if migration.version == 1:
            apply_migration(conn, backfill_agent_history)
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(migration.version),),
            )
            conn.execute(
                "INSERT INTO schema_migrations(version, name, checksum) "
                "VALUES (?, ?, ?)",
                (migration.version, migration.name, migration.checksum),
            )
            conn.commit()
            completed.add(migration.version)
            continue

        try:
            conn.execute("BEGIN")
            apply_migration(conn, backfill_agent_history)
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(migration.version),),
            )
            conn.execute(
                "INSERT INTO schema_migrations(version, name, checksum) "
                "VALUES (?, ?, ?)",
                (migration.version, migration.name, migration.checksum),
            )
            conn.commit()
            completed.add(migration.version)
        except Exception:
            conn.rollback()
            raise

    # ``schema_migrations`` is authoritative.  Keep the legacy meta mirror in
    # sync even when an older database already had every ledger row but its
    # meta value was missing or stale.
    if completed:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(max(completed)),),
        )
        conn.commit()


def _repair_applied_schema_contracts(
    conn: sqlite3.Connection,
    backfill_agent_history,
) -> None:
    """Reconcile additive contracts explicitly marked as boot-repairable.

    Some historical installations (and recovery workflows) can retain a
    complete migration ledger while an additive table contract is partial.
    Keep that compatibility behavior attached to the owning migration instead
    of accumulating independent column probes in the legacy baseline.
    """

    applied = _applied_schema_migrations(conn)
    for migration in SCHEMA_MIGRATIONS:
        if not migration.repair_on_boot or migration.version not in applied:
            continue
        try:
            conn.execute("BEGIN")
            migration.apply(conn, backfill_agent_history)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def initialize_database(conn: sqlite3.Connection, backfill_agent_history):
    """Configure SQLite and advance the database to ``SCHEMA_VERSION``."""

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema_migrations_table(conn)
    # Compatibility bridge: older releases and recovery tests can contain a
    # partially additive schema even after a previous successful boot.  Keep
    # repairing that baseline until its remaining probes are converted into
    # explicit ledger migrations.  Fresh/unadopted databases run the same pass
    # through migration 1 below, so avoid doing it twice.
    if 1 in _applied_schema_migrations(conn):
        _reconcile_legacy_schema(conn, backfill_agent_history)
    _apply_schema_migrations(
        conn,
        backfill_agent_history,
        phases={"schema"},
    )
    _repair_applied_schema_contracts(conn, backfill_agent_history)


def finalize_database_migrations(
    conn: sqlite3.Connection,
    backfill_agent_history,
    *,
    post_init_runners: dict[int, Callable[[], object]] | None = None,
) -> bool:
    """Apply guarded post-init work and ledger each runner atomically.

    Runners participate in transactions owned here and must not begin, commit,
    or roll back independently.
    """

    runners = dict(post_init_runners or {})
    kinds_version = _legacy_kinds_version(conn)
    applied = _applied_schema_migrations(conn)

    if 8 in applied and kinds_version < KINDS_LEGACY_COMPLETE_VERSION:
        kinds_runner = runners.get(8)
        if kinds_runner is None:
            return False
        try:
            conn.execute("BEGIN")
            kinds_runner()
            if _legacy_kinds_version(conn) < KINDS_LEGACY_COMPLETE_VERSION:
                raise _PostInitMigrationNotReady()
            conn.commit()
        except _PostInitMigrationNotReady:
            conn.rollback()
            return False
        except Exception:
            conn.rollback()
            raise
        kinds_version = _legacy_kinds_version(conn)

    post_init_migrations = [
        migration
        for migration in SCHEMA_MIGRATIONS
        if migration.phase == "post_init"
    ]
    for migration in post_init_migrations:
        if migration.version in applied:
            continue
        runner = runners.get(migration.version)
        if migration.version == 8:
            if kinds_version < KINDS_LEGACY_COMPLETE_VERSION:
                if runner is None:
                    return False

                def apply_guarded_kinds_completion(
                    connection,
                    backfill,
                ) -> None:
                    runner()
                    if (
                        _legacy_kinds_version(connection)
                        < KINDS_LEGACY_COMPLETE_VERSION
                    ):
                        raise _PostInitMigrationNotReady()
                    migration.apply(connection, backfill)

                apply_migration = apply_guarded_kinds_completion
            else:
                apply_migration = migration.apply
        else:
            if runner is None:
                if migration.requires_runner:
                    return False
                apply_migration = migration.apply
            else:

                def apply_runner_migration(
                    connection,
                    backfill,
                    *,
                    _runner=runner,
                    _migration=migration,
                ) -> None:
                    _runner()
                    _migration.apply(connection, backfill)

                apply_migration = apply_runner_migration

        try:
            _apply_schema_migrations(
                conn,
                backfill_agent_history,
                phases={"post_init"},
                versions={migration.version},
                apply_overrides={migration.version: apply_migration},
            )
        except _PostInitMigrationNotReady:
            return False
        applied[migration.version] = {"version": migration.version}
        if migration.version == 8:
            kinds_version = _legacy_kinds_version(conn)
    return True

def rebuild_memory_retention_indexes(conn: sqlite3.Connection):
    """Ensure memory-entry indexes match the current retention schema."""
    for name in (
        "idx_memory_entries_scope",
        "idx_memory_entries_group",
        "idx_memory_entries_project",
        "idx_memory_entries_type",
        "idx_memory_entries_expiry",
    ):
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_entries_scope "
        "ON memory_entries("
        "scope_kind, scope_ref, pinned, retention_kind, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_entries_group "
        "ON memory_entries("
        "group_name, pinned, retention_kind, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_entries_project "
        "ON memory_entries("
        "project_key, pinned, retention_kind, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_entries_type "
        "ON memory_entries(entry_type, created_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_entries_expiry "
        "ON memory_entries(retention_kind, expires_at)"
    )
    conn.commit()
