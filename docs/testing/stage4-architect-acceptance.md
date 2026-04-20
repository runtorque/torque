# Stage 4 Architect Acceptance

> Manual smoke for Stage 4 of the Agent Kinds Refactor: architect launch,
> decision log CRUD, pending-hire approval, architect↔engineer messaging, and
> persistence after restart.

This assumes Stage 1–3 are already deployed and healthy, the Stage 4 UI is
present, and at least one engineer already exists for routing/assignment (the
default `Weaver` engineer is fine).

## Preflight

Keep the live database and backup paths handy:

```bash
LOOM_DIR="$HOME/Library/Application Support/iTerm2/Scripts/loom/loom"
DB="$LOOM_DIR/loom.db"
BACKUP="$LOOM_DIR/loom.db.pre-kinds.bak"
LOG="$LOOM_DIR/loom.log"
```

Run doctor before deploy:

```bash
loom doctor
```

Expected:

- exit code `0`
- `Result: PASS`
- `[engineers]`, `[architects]`, and `[pending_hires]` sections are present

Record the current architect / engineer counts:

```bash
loom doctor --json | jq '{architects: .architects.total, engineers: .engineers.total, pending_hires: .pending_hires.pending}'
```

Capture the default engineer id for later task routing:

```bash
WEAVER_ID="$(sqlite3 "$DB" "SELECT id FROM agents WHERE kind='engineer' AND name='Weaver' LIMIT 1;")"
echo "$WEAVER_ID"
test -n "$WEAVER_ID"
```

## Deploy and restart

Deploy the build:

```bash
make deploy
```

Restart Loom from:

```text
iTerm2 → Scripts → loom
```

If you want startup logs in parallel:

```bash
tail -f "$LOG"
```

## Add the architect

1. Open the **Architects** panel.
2. Click **+ Add Architect**.
3. Enter `productmind` as the name.
4. Keep the default boot command unless you need a local override.
5. Submit the modal.

Verify the persisted row:

```bash
sqlite3 "$DB" "SELECT id, name, kind, persistent FROM agents WHERE name='productmind';"
```

Expected: one row where `kind='architect'` and `persistent=1`.

Capture the architect id and directory:

```bash
PRODUCTMIND_ID="$(sqlite3 "$DB" "SELECT id FROM agents WHERE name='productmind' AND kind='architect' LIMIT 1;")"
PRODUCTMIND_DIR="$(sqlite3 "$DB" "SELECT directory FROM agents WHERE id='$PRODUCTMIND_ID';")"
echo "PRODUCTMIND_ID=$PRODUCTMIND_ID"
echo "PRODUCTMIND_DIR=$PRODUCTMIND_DIR"
test -n "$PRODUCTMIND_ID"
test -n "$PRODUCTMIND_DIR"
```

Verify the MCP config points at the architect entrypoint:

```bash
rg -n "loom\\.mcp_architect|mcp_architect\\.py" \
  "$PRODUCTMIND_DIR/.codex/config.toml" \
  "$PRODUCTMIND_DIR/.mcp.json"
```

Expected: one of those provider config files contains the architect stdio
entrypoint. The live `LOOM_ARCHITECT_ID` binding is checked in the next step
from inside the architect session itself.

## Verify the architect MCP session

Open productmind's agent terminal and paste this prompt:

```text
Before doing anything else:
1. Use a shell command to print the LOOM_ARCHITECT_ID environment variable.
2. List the MCP tool names available in this session.
3. Confirm whether these tools are present: architect_decision_create, architect_decision_list, architect_task_create, architect_engineer_hire, architect_engineer_message.
Return only the environment value and the tool names.
```

Expected:

- the printed `LOOM_ARCHITECT_ID` matches `$PRODUCTMIND_ID`
- the tool list includes the `architect_*` surface

## Create and verify a decision

In the same productmind session, paste:

```text
Call architect_decision_create with:
{"title":"Test decision","rationale":"Smoke"}
Return only the JSON result.
```

Then capture the decision id:

```bash
DECISION_ID="$(sqlite3 "$DB" "SELECT id FROM decisions WHERE architect_id='$PRODUCTMIND_ID' AND title='Test decision' LIMIT 1;")"
echo "$DECISION_ID"
test -n "$DECISION_ID"
```

Now ask productmind to list its decisions:

```text
Call architect_decision_list with an empty object and return only the JSON result.
```

Expected: the returned list includes `Test decision` with the same `$DECISION_ID`.

## Create an architect-routed task

In the productmind session, paste:

```text
Call architect_task_create with:
{"title":"Do the thing","group":"loom","assigned_engineer_id":"WEAVER_ID_VALUE","suggested_action":"feature/implement"}
Replace WEAVER_ID_VALUE with the real engineer id before sending.
Return only the JSON result.
```

Use the real `$WEAVER_ID` in the prompt above. Then capture the task id:

```bash
TASK_ID="$(sqlite3 "$DB" "SELECT id FROM board_tasks WHERE created_by_architect_id='$PRODUCTMIND_ID' AND task='Do the thing' ORDER BY rowid DESC LIMIT 1;")"
echo "$TASK_ID"
test -n "$TASK_ID"
sqlite3 "$DB" "SELECT id, assigned_engineer_id, created_by_architect_id, suggested_action FROM board_tasks WHERE id='$TASK_ID';"
```

Expected:

- the row exists
- `assigned_engineer_id='$WEAVER_ID'`
- `created_by_architect_id='$PRODUCTMIND_ID'`
- `suggested_action='feature/implement'`

Also verify in the UI that the task appears in the engineer backlog / inbox with
the suggested action visible.

## Request and approve an engineer hire

In the productmind session, paste:

```text
Call architect_engineer_hire with:
{"name":"bob"}
Return only the JSON result.
```

Capture the pending hire id:

```bash
HIRE_ID="$(sqlite3 "$DB" "SELECT id FROM pending_hires WHERE architect_id='$PRODUCTMIND_ID' ORDER BY created_at DESC LIMIT 1;")"
echo "$HIRE_ID"
test -n "$HIRE_ID"
sqlite3 "$DB" "SELECT id, architect_id, requested_name, status FROM pending_hires WHERE id='$HIRE_ID';"
```

Expected:

- the row exists
- `status='pending'`
- the UI shows the pending-hire banner / approval affordance for `bob`

Approve it in the UI.

Then verify the engineer row:

```bash
BOB_ID="$(sqlite3 "$DB" "SELECT id FROM agents WHERE kind='engineer' AND name='bob' ORDER BY rowid DESC LIMIT 1;")"
echo "$BOB_ID"
test -n "$BOB_ID"
sqlite3 "$DB" "SELECT id, name, kind, persistent, hired_by_architect_id FROM agents WHERE id='$BOB_ID';"
sqlite3 "$DB" "SELECT id, status, created_engineer_id FROM pending_hires WHERE id='$HIRE_ID';"
```

Expected:

- the engineer row exists
- `kind='engineer'`
- `persistent=1`
- `hired_by_architect_id='$PRODUCTMIND_ID'`
- the pending-hire row is now `status='approved'` with `created_engineer_id='$BOB_ID'`

## Verify engineer visibility from the architect

In the productmind session, paste:

```text
Call architect_engineer_list with an empty object and return only the JSON result.
```

Expected:

- the list includes `bob`
- the list still includes any same-group user-visible engineers
- `bob` is marked as hired by this architect

## Verify architect → engineer messaging

In the productmind session, paste:

```text
Call architect_engineer_message with:
{"engineer_id":"BOB_ID_VALUE","message":"please start on TASK_ID_VALUE"}
Replace BOB_ID_VALUE and TASK_ID_VALUE with the real ids before sending.
Return only the JSON result.
```

Expected:

- the call succeeds
- the message appears on bob's cell in the UI

## Verify engineer → architect messaging

Open bob's engineer MCP session and paste:

```text
Call engineer_message_architect with:
{"architect_id":"PRODUCTMIND_ID_VALUE","message":"acknowledged"}
Replace PRODUCTMIND_ID_VALUE with the real architect id before sending.
Return only the JSON result.
```

Expected:

- the call succeeds
- the reply appears on productmind's cell in the UI

## Update the decision and confirm live deltas

Back in the productmind session, paste:

```text
Call architect_decision_update with:
{"id":"DECISION_ID_VALUE","status":"accepted"}
Replace DECISION_ID_VALUE with the real decision id before sending.
Return only the JSON result.
```

Expected:

- the tool call succeeds
- the architect decision UI updates live without a manual refresh

You can confirm the stored row directly:

```bash
sqlite3 "$DB" "SELECT id, status, archived FROM decisions WHERE id='$DECISION_ID';"
```

Expected: `status='accepted'` and `archived=0`.

## Restart persistence check

Restart Loom again from the Scripts menu.

Expected after restart:

- `productmind` still exists as an architect
- `bob` still exists as an engineer hired by `productmind`
- the decision log still contains `Test decision`
- the task `Do the thing` still exists with `suggested_action='feature/implement'`

Re-run:

```bash
loom doctor
```

Expected:

- exit code `0`
- `Result: PASS`
- the architect and pending-hire sections still show sane counts

## Delete the architect and verify transfer / archival

1. Delete `productmind` from the UI.
2. Confirm the modal explains that hired engineers transfer to the user.

Then verify the transfer and archival:

```bash
sqlite3 "$DB" "SELECT id, name, hired_by_architect_id FROM agents WHERE id='$BOB_ID';"
sqlite3 "$DB" "SELECT id, architect_id, archived FROM decisions WHERE architect_id='$PRODUCTMIND_ID';"
```

Expected:

- bob still exists
- bob now has `hired_by_architect_id=''`
- the decision row(s) still exist
- every matching decision row now has `archived=1`

## Recovery / rollback

If the migration or runtime state is corrupted, stop Loom and restore the
stage-1 backup:

```bash
make stop
cp "$BACKUP" "$DB"
```

Then restart Loom from the Scripts menu and re-run this guide after fixing the
underlying issue.
