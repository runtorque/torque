-- Relay schema v5: single-use, short-lived, hashed client-establish codes.
-- The same-origin /establish endpoint trades a code for a freshly minted client
-- session (the long-lived session token is born server-side and lives only in
-- the HttpOnly cookie). Parallels relay_pairing_tokens: only the hash is stored,
-- redemption is atomic + single-use.
CREATE TABLE IF NOT EXISTS relay_client_establish_codes (
  id TEXT PRIMARY KEY,
  code_hash TEXT NOT NULL UNIQUE,
  owner_user_id TEXT NOT NULL,
  daemon_id TEXT NOT NULL DEFAULT '',
  label TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT NOT NULL DEFAULT '',
  revoked_at TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_relay_client_establish_codes_owner
  ON relay_client_establish_codes (owner_user_id, expires_at ASC);

INSERT OR REPLACE INTO relay_meta (key, value) VALUES ('schema_version', '5');
