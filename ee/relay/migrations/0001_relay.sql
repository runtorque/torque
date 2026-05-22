CREATE TABLE IF NOT EXISTS relay_instances (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL DEFAULT '',
  label TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS relay_messages (
  id TEXT PRIMARY KEY,
  daemon_id TEXT NOT NULL,
  direction TEXT NOT NULL,
  kind TEXT NOT NULL,
  source_json TEXT NOT NULL DEFAULT '{}',
  target_json TEXT NOT NULL DEFAULT '{}',
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  delivered_at TEXT NOT NULL DEFAULT '',
  acked_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_relay_messages_daemon_created
  ON relay_messages (daemon_id, created_at ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_relay_messages_daemon_direction_created
  ON relay_messages (daemon_id, direction, created_at ASC, id ASC);
