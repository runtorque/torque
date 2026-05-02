# Stage 1 Kinds Refactor Acceptance

> Stage-1-only manual smoke for the Agent Kinds Refactor. Later stages should
> add their own acceptance docs instead of extending this one indefinitely.

This protocol assumes you are upgrading an existing Torque install that already
has a live Engineer/torque-group workflow. Fresh-install and edge-case migration
paths are covered separately by automated tests.

## Preflight

```bash
TORQUE_DIR="$HOME/Library/Application Support/iTerm2/Scripts/torque/torque"
DB="$TORQUE_DIR/torque.db"
LOG="$TORQUE_DIR/torque.log"
BACKUP="$TORQUE_DIR/torque.db.pre-kinds.bak"
```

Record the current row counts so you can confirm nothing disappears:

```bash
sqlite3 "$DB" "SELECT 'agents', COUNT(*) FROM agents UNION ALL SELECT 'tasks', COUNT(*) FROM board_tasks;"
```

Record the pre-migration database size:

```bash
ls -lh "$DB"
```

## Apply the migration

Deploy the build:

```bash
make deploy
```

Restart Torque from the iTerm2 Scripts menu:

```text
iTerm2 → Scripts → torque
```

Tail the daemon log and watch the first boot:

```bash
tail -f "$LOG"
```

Expect these lines on the first boot after deploy:

- `migration: kinds schema applied (version=1, backup=...)`
- `migration: kinds backfill applied (...)`

On later boots, neither line should appear again.

## Verify backup

```bash
ls -lh "$BACKUP"
```

Expected: the file exists and is non-empty.

## Verify schema

```bash
sqlite3 "$DB" "PRAGMA table_info(agents);"
```

Expected new `agents` columns:

- `kind`
- `role`
- `owner_engineer_id`
- `hired_by_architect_id`
- `persistent`

```bash
sqlite3 "$DB" "PRAGMA table_info(board_tasks);"
```

Expected new `board_tasks` columns:

- `assigned_engineer_id`
- `created_by_architect_id`
- `suggested_action`

```bash
sqlite3 "$DB" ".schema decisions"
```

Expected: a `CREATE TABLE decisions (...)` statement is printed.

## Verify backfill

Exactly one engineer row should exist for the promoted Engineer:

```bash
sqlite3 "$DB" "SELECT id, name, kind, persistent FROM agents WHERE kind='engineer';"
```

Expected: exactly one row; name `Engineer`; `persistent=1`.

No unmigrated agents should remain:

```bash
sqlite3 "$DB" "SELECT COUNT(*) FROM agents WHERE kind='';"
```

Expected: `0`

Worker rows with a legacy template should also have the new role populated:

```bash
sqlite3 "$DB" "SELECT COUNT(*) FROM agents WHERE kind='worker' AND template != '' AND role = '';"
```

Expected: `0`

## Verify `torque doctor`

```bash
torque doctor
```

Expected:

- exit code `0`
- `Result: PASS`
- drift counters are all `0`
- engineer count is `1`

```bash
torque doctor --json | jq '.result'
```

Expected:

```json
"pass"
```

## Verify dual-write live

Create a new worker agent in the UI with:

- name: `Kinds smoke worker`
- a non-empty template/role selection

Then confirm the legacy and new agent columns match:

```bash
sqlite3 "$DB" "SELECT id, template, role FROM agents WHERE name='Kinds smoke worker';"
```

Expected: one row; `template` and `role` are both non-empty and identical.

Next, dispatch a task through the existing UI flow, then re-run:

```bash
torque doctor
```

Expected: still `Result: PASS`.

## Rollback (if anything above fails)

Stop the daemon:

```bash
make stop
```

Restore the pre-migration backup:

```bash
cp "$BACKUP" "$DB"
```

Then either revert the deploy or fix the cause and re-run the migration on the
next startup.
