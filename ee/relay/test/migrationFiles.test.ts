// Regression: the migration FILE sequence under migrations/ must raw-apply
// cleanly on a fresh DB, exactly as `wrangler d1 migrations apply` runs it —
// each file's SQL executed in order, with NO PRAGMA table_info guards. A
// previous regression had 0001 ship the full schema (incl. the v4 coordination
// columns), so 0002's `ALTER TABLE ... ADD COLUMN fencing_epoch` threw
// "duplicate column name", halting the sequence before 0003 could create
// relay_client_establish_codes. The runtime migrate() path dodges this with
// guarded ALTERs, but the raw file sequence does not — so this test locks the
// incremental file history (v3 -> v4 -> v5) and asserts it converges on the
// SAME final schema as the runtime RELAY_SCHEMA_STATEMENTS fresh-create path.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
// @ts-ignore -- node:sqlite exists at runtime on Node >=22.5; @types can lag.
import { DatabaseSync } from "node:sqlite";

import { RELAY_SCHEMA_STATEMENTS, RELAY_SCHEMA_VERSION } from "../src/core/sql.js";

const migrationsDir = resolve(dirname(fileURLToPath(import.meta.url)), "../../migrations");

interface SchemaSnapshot {
  tables: string[];
  columns: Record<string, string[]>;
  indexes: string[];
  schemaVersion: string | undefined;
}

function snapshotSchema(db: any): SchemaSnapshot {
  const tables = (db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all() as Array<{ name: string }>)
    .map((r) => r.name)
    .filter((name) => !name.startsWith("sqlite_"))
    .sort();
  const columns: Record<string, string[]> = {};
  for (const table of tables) {
    columns[table] = (db.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name: string }>)
      .map((r) => r.name)
      .sort();
  }
  const indexes = (db.prepare("SELECT name FROM sqlite_master WHERE type='index'").all() as Array<{ name: string }>)
    .map((r) => r.name)
    .filter((name) => !name.startsWith("sqlite_"))
    .sort();
  const version = db.prepare("SELECT value FROM relay_meta WHERE key='schema_version'").get() as
    | { value: string }
    | undefined;
  return { tables, columns, indexes, schemaVersion: version?.value };
}

test("migration FILE sequence raw-applies cleanly on a fresh DB (wrangler d1 migrations apply parity)", () => {
  const files = readdirSync(migrationsDir)
    .filter((f) => /^\d+.*\.sql$/.test(f))
    .sort();
  assert.ok(files.length >= 3, `expected at least 3 migration files, found ${files.length}: ${files.join(", ")}`);

  const db = new DatabaseSync(":memory:");
  try {
    // Apply each migration file in lexical order, raw — no idempotency guards.
    // A duplicate-column ALTER (or any SQL error) throws here and fails the test.
    for (const file of files) {
      const sql = readFileSync(join(migrationsDir, file), "utf8");
      db.exec(sql);
    }

    const snapshot = snapshotSchema(db);

    // Every relay table must exist after the raw sequence completes (i.e. the
    // sequence did NOT halt early — 0003 ran).
    for (const table of [
      "relay_meta",
      "relay_instances",
      "relay_messages",
      "relay_pairing_tokens",
      "relay_daemon_credentials",
      "relay_auth_nonces",
      "relay_client_sessions",
      "relay_client_establish_codes",
    ]) {
      assert.ok(snapshot.tables.includes(table), `missing table after raw migration apply: ${table}`);
    }

    // relay_instances must carry the v4 coordination columns (added by 0002).
    for (const col of ["fencing_epoch", "active_credential_id", "coordination_updated_at"]) {
      assert.ok(
        snapshot.columns["relay_instances"].includes(col),
        `relay_instances missing coordination column after raw migration apply: ${col}`,
      );
    }

    // schema_version must land on the latest, matching the runtime constant.
    assert.equal(snapshot.schemaVersion, String(RELAY_SCHEMA_VERSION));
    assert.equal(snapshot.schemaVersion, "5");
  } finally {
    db.close();
  }
});

test("migration FILE sequence converges on the same schema as the runtime RELAY_SCHEMA_STATEMENTS path", () => {
  const files = readdirSync(migrationsDir)
    .filter((f) => /^\d+.*\.sql$/.test(f))
    .sort();

  const fileDb = new DatabaseSync(":memory:");
  const runtimeDb = new DatabaseSync(":memory:");
  try {
    for (const file of files) {
      fileDb.exec(readFileSync(join(migrationsDir, file), "utf8"));
    }
    for (const statement of RELAY_SCHEMA_STATEMENTS) {
      runtimeDb.exec(statement);
    }

    const fileSchema = snapshotSchema(fileDb);
    const runtimeSchema = snapshotSchema(runtimeDb);

    // Compare table SETS, per-table column SETS, index SETS, and schema_version.
    // Column ORDER intentionally differs (ALTER appends coordination columns at
    // the end of relay_instances, while RELAY_SCHEMA_STATEMENTS lists them
    // inline) — both paths use explicit column lists, so set-equality is the
    // correct invariant.
    assert.deepEqual(fileSchema.tables, runtimeSchema.tables, "table set diverged between file and runtime paths");
    assert.deepEqual(fileSchema.columns, runtimeSchema.columns, "column set diverged between file and runtime paths");
    assert.deepEqual(fileSchema.indexes, runtimeSchema.indexes, "index set diverged between file and runtime paths");
    assert.equal(fileSchema.schemaVersion, runtimeSchema.schemaVersion);
  } finally {
    fileDb.close();
    runtimeDb.close();
  }
});
