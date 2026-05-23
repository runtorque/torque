-- Relay schema v4 coordination fields. Runtime adapters apply these ALTERs
-- only after checking PRAGMA table_info(relay_instances), because SQLite/D1
-- do not both support ALTER TABLE ... ADD COLUMN IF NOT EXISTS.
ALTER TABLE relay_instances ADD COLUMN fencing_epoch INTEGER NOT NULL DEFAULT 0;
ALTER TABLE relay_instances ADD COLUMN active_credential_id TEXT NOT NULL DEFAULT '';
ALTER TABLE relay_instances ADD COLUMN coordination_updated_at TEXT NOT NULL DEFAULT '';
INSERT OR REPLACE INTO relay_meta (key, value) VALUES ('schema_version', '4');
