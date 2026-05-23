CREATE TABLE IF NOT EXISTS relay_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

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
  envelope_json TEXT NOT NULL DEFAULT '{}',
  envelope_hash TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  delivery_state TEXT NOT NULL DEFAULT 'pending',
  delivered_at TEXT NOT NULL DEFAULT '',
  acked_at TEXT NOT NULL DEFAULT '',
  failed_at TEXT NOT NULL DEFAULT '',
  delivery_attempts INTEGER NOT NULL DEFAULT 0,
  last_delivery_error TEXT NOT NULL DEFAULT '',
  last_delivery_epoch INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS relay_pairing_tokens (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  owner_user_id TEXT NOT NULL,
  daemon_id TEXT NOT NULL DEFAULT '',
  label TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT NOT NULL DEFAULT '',
  revoked_at TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS relay_daemon_credentials (
  credential_id TEXT PRIMARY KEY,
  daemon_id TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  public_key_jwk_json TEXT NOT NULL DEFAULT '{}',
  alg TEXT NOT NULL DEFAULT 'ES256',
  created_at TEXT NOT NULL,
  last_used_at TEXT NOT NULL DEFAULT '',
  revoked_at TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS relay_auth_nonces (
  credential_id TEXT NOT NULL,
  nonce_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  PRIMARY KEY (credential_id, nonce_hash)
);

CREATE TABLE IF NOT EXISTS relay_client_sessions (
  session_id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  owner_user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_relay_messages_daemon_created
  ON relay_messages (daemon_id, created_at ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_relay_messages_daemon_direction_created
  ON relay_messages (daemon_id, direction, created_at ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_relay_messages_pending
  ON relay_messages (daemon_id, direction, acked_at, created_at ASC, id ASC);

CREATE INDEX IF NOT EXISTS idx_relay_pairing_tokens_owner
  ON relay_pairing_tokens (owner_user_id, expires_at ASC);

CREATE INDEX IF NOT EXISTS idx_relay_daemon_credentials_daemon
  ON relay_daemon_credentials (daemon_id, owner_user_id, credential_id);

CREATE INDEX IF NOT EXISTS idx_relay_auth_nonces_expires
  ON relay_auth_nonces (expires_at ASC);

CREATE INDEX IF NOT EXISTS idx_relay_client_sessions_owner
  ON relay_client_sessions (owner_user_id, expires_at ASC);

INSERT OR REPLACE INTO relay_meta (key, value) VALUES ('schema_version', '3');
