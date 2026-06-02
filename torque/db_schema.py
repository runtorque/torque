"""Schema DDL and migration helpers for TorqueDB."""

import json
import sqlite3

SCHEMA_VERSION = "1"

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
    last_progress_at      REAL NOT NULL DEFAULT 0,
    last_heartbeat_at     REAL NOT NULL DEFAULT 0,
    last_activity_at      REAL NOT NULL DEFAULT 0,
    session_resume        INTEGER NOT NULL DEFAULT 1,
    idle_timeout          INTEGER NOT NULL DEFAULT 0,
    tasks_dispatched      INTEGER NOT NULL DEFAULT 0,
    queue_empty_emitted   INTEGER NOT NULL DEFAULT 1,
    dismissed_at          INTEGER NOT NULL DEFAULT 0,
    deleted_at            REAL NOT NULL DEFAULT 0,
    permanent_delete_after REAL NOT NULL DEFAULT 0,
    engineer_specializations TEXT NOT NULL DEFAULT '[]'
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
    agent_profile               TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS agent_peer_messages (
    id                   TEXT PRIMARY KEY,
    thread_id            TEXT NOT NULL,
    reply_to_id          TEXT NOT NULL DEFAULT '',
    group_name           TEXT NOT NULL DEFAULT '',
    sender_id            TEXT NOT NULL,
    sender_kind          TEXT NOT NULL,
    sender_name          TEXT NOT NULL DEFAULT '',
    recipient_id         TEXT NOT NULL,
    recipient_kind       TEXT NOT NULL,
    recipient_name       TEXT NOT NULL DEFAULT '',
    message              TEXT NOT NULL,
    message_type         TEXT NOT NULL DEFAULT 'message',
    created_at           REAL NOT NULL,
    ack_required         INTEGER NOT NULL DEFAULT 0,
    blocking             INTEGER NOT NULL DEFAULT 0,
    source_task_id       TEXT NOT NULL DEFAULT '',
    context_task_ids     TEXT NOT NULL DEFAULT '[]',
    context_engineer_ids TEXT NOT NULL DEFAULT '[]',
    context_decision_ids TEXT NOT NULL DEFAULT '[]',
    context_summary      TEXT NOT NULL DEFAULT '',
    context_snapshot     TEXT NOT NULL DEFAULT '{}',
    delivery_state       TEXT NOT NULL DEFAULT 'buffered',
    delivery_reason      TEXT NOT NULL DEFAULT '',
    delivered_at         REAL NOT NULL DEFAULT 0,
    read_at              REAL NOT NULL DEFAULT 0,
    archived_at          REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_agent_peer_messages_recipient_recent
    ON agent_peer_messages(recipient_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_agent_peer_messages_sender_recent
    ON agent_peer_messages(sender_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_agent_peer_messages_thread
    ON agent_peer_messages(thread_id, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS idx_agent_peer_messages_group_recent
    ON agent_peer_messages(group_name, created_at DESC, id DESC);

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
    requested_specializations TEXT NOT NULL DEFAULT '[]',
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


def initialize_database(conn: sqlite3.Connection, backfill_agent_history):
    """Create tables and apply in-place SQLite migrations."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate_legacy_engineer_schema_names(conn)
    _migrate_behavior_overlay_scope_schema(conn)
    conn.executescript(_SCHEMA_SQL)
    _migrate_behavior_overlay_scope_schema(conn)
    # Migrate: add journal author provenance for engineer-scoped reads
    try:
        conn.execute("SELECT author_cell_id FROM engineer_journal LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE engineer_journal ADD COLUMN "
            "author_cell_id TEXT NOT NULL DEFAULT ''")
        conn.commit()
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_engineer_journal_group_author "
        "ON engineer_journal(group_name, author_cell_id, id DESC)")
    try:
        conn.execute("SELECT source_key FROM engineer_journal LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE engineer_journal ADD COLUMN "
            "source_key TEXT NOT NULL DEFAULT ''")
        conn.commit()
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_engineer_journal_source_key "
        "ON engineer_journal(group_name, author_cell_id, source_key) "
        "WHERE source_key <> ''")
    conn.commit()
    # Migrate: add slug columns to existing tables
    for table in ("agents", "groups", "board_tasks"):
        try:
            conn.execute(f"SELECT slug FROM {table} LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN slug "
                f"TEXT NOT NULL DEFAULT ''")
            conn.commit()
    # Migrate: add agent_provider column
    try:
        conn.execute(
            "SELECT agent_provider FROM group_settings LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE group_settings ADD COLUMN "
            "agent_provider TEXT NOT NULL DEFAULT ''")
        conn.commit()
    for col in ("agent_model", "agent_reasoning_effort"):
        try:
            conn.execute(
                f"SELECT {col} FROM group_settings LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE group_settings ADD COLUMN "
                f"{col} TEXT NOT NULL DEFAULT ''")
            conn.commit()
    for col in (
            "worker_provider",
            "worker_boot_command",
            "worker_model",
            "worker_reasoning_effort"):
        try:
            conn.execute(
                f"SELECT {col} FROM group_settings LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute(
                f"ALTER TABLE group_settings ADD COLUMN "
                f"{col} TEXT NOT NULL DEFAULT ''")
            conn.commit()
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
        ("queue_empty_emitted", "INTEGER", "1"),
        ("worktree_base_dir", "TEXT", "'.torque/worktrees'"),
        ("worktree_auto_checkpoint", "INTEGER", "0"),
        ("checkpoint_on_progress", "INTEGER", "0"),
        ("worktree_merge_squash", "INTEGER", "1"),
        ("session_resume", "INTEGER", "1"),
        ("idle_timeout", "INTEGER", "0"),
        ("deleted_at", "REAL", "0"),
        ("permanent_delete_after", "REAL", "0"),
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
    # Migrate: add requested_specializations column to pending_hires
    try:
        conn.execute(
            "SELECT requested_specializations FROM pending_hires LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE pending_hires ADD COLUMN "
            "requested_specializations TEXT NOT NULL DEFAULT '[]'")
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
    # Set schema version if not present
    row = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,))
    conn.commit()

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
