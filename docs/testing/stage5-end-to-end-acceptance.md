# Stage 5 End-to-End Acceptance

> Manual smoke for Stage 5 of the Agent Kinds Refactor: worktree branch
> namespacing, architect↔engineer coordination, worker reporting, communication
> graph enforcement, and the new `loom doctor` worktree namespace checks.

This assumes Stage 1–4 are already deployed and healthy. Run the whole flow in
one session so you exercise the full ownership chain:

```text
User → Architect → Engineer → Worker
```

## Shared setup

Keep the live database, backup, and log paths handy:

```bash
LOOM_DIR="$HOME/Library/Application Support/iTerm2/Scripts/loom/loom"
DB="$LOOM_DIR/loom.db"
BACKUP="$LOOM_DIR/loom.db.pre-kinds.bak"
LOG="$LOOM_DIR/loom.log"
```

If you want live logs in a second terminal:

```bash
tail -f "$LOG"
```

## Phase 1 — Preflight and deploy

### 1.1 Confirm doctor is clean before the stage-5 flow

```bash
loom doctor
```

Expected:

- exit code `0`
- `Result: PASS`
- `[architects]`, `[pending_hires]`, and `[worktrees]` sections are present
- `[worktrees]` shows `nonconforming:         0`

Capture the starting counts:

```bash
loom doctor --json | jq '{result: .result, architects: .architects.total, engineers: .engineers.total, pending_hires: .pending_hires.pending, worktrees: .worktrees}'
```

Expected before this smoke begins:

- `architects = 0`
- `engineers = 1` (the default `Weaver` engineer)
- `pending_hires = 0`

Capture the current default engineer id for reference:

```bash
WEAVER_ID="$(sqlite3 "$DB" "SELECT id FROM agents WHERE kind='engineer' AND name='Weaver' LIMIT 1;")"
echo "$WEAVER_ID"
test -n "$WEAVER_ID"
```

### 1.2 Deploy the build and restart Loom

```bash
make deploy
```

Then restart from:

```text
iTerm2 → Scripts → loom
```

## Phase 2 — Create the architect and route the work

### 2.1 Add the architect in the UI

1. Open the **Architects** panel.
2. Click **+ Add Architect**.
3. Enter `productmind` as the name.
4. Keep the default launch settings unless you intentionally need an override.
5. Submit the modal.

Capture the architect id:

```bash
PRODUCTMIND_ID="$(sqlite3 "$DB" "SELECT id FROM agents WHERE kind='architect' AND name='productmind' ORDER BY rowid DESC LIMIT 1;")"
echo "$PRODUCTMIND_ID"
test -n "$PRODUCTMIND_ID"
```

### 2.2 Verify the architect session binding

Open the `productmind` terminal and paste this prompt:

```text
Before doing anything else:
1. Use a shell command to print the LOOM_ARCHITECT_ID environment variable.
2. List the available MCP tool names in this session.
3. Confirm architect_decision_create, architect_engineer_hire, architect_task_create, architect_engineer_message, and architect_reply are present.
Return only the environment value and the tool names.
```

Expected:

- the printed `LOOM_ARCHITECT_ID` matches `$PRODUCTMIND_ID`
- the tool list contains the `architect_*` surface
- there is no architect worker-messaging tool in the list

### 2.3 Create the initial decision

In the `productmind` session, paste:

```text
Call architect_decision_create with:
{"title":"Ship the thing","rationale":"Q2 priority"}
Return only the JSON result.
```

Capture the decision id:

```bash
DECISION_ID="$(sqlite3 "$DB" "SELECT id FROM decisions WHERE architect_id='$PRODUCTMIND_ID' AND title='Ship the thing' ORDER BY rowid DESC LIMIT 1;")"
echo "$DECISION_ID"
test -n "$DECISION_ID"
```

Expected: the decision appears in the Architects UI with `status='proposed'`.

### 2.4 Request a hire for `alice`

In the `productmind` session, paste:

```text
Call architect_engineer_hire with:
{"name":"alice"}
Return only the JSON result.
```

Capture the pending-hire id:

```bash
HIRE_ID="$(sqlite3 "$DB" "SELECT id FROM pending_hires WHERE architect_id='$PRODUCTMIND_ID' AND requested_name='alice' ORDER BY created_at DESC LIMIT 1;")"
echo "$HIRE_ID"
test -n "$HIRE_ID"
```

Expected: the pending-hire banner appears in the UI.

### 2.5 Approve the hire in the UI

Approve `alice` from the pending-hire banner / modal.

Then capture the new engineer id:

```bash
ALICE_ID="$(sqlite3 "$DB" "SELECT id FROM agents WHERE kind='engineer' AND name='alice' ORDER BY rowid DESC LIMIT 1;")"
echo "$ALICE_ID"
test -n "$ALICE_ID"
sqlite3 "$DB" "SELECT id, name, kind, hired_by_architect_id FROM agents WHERE id='$ALICE_ID';"
```

Expected:

- `kind='engineer'`
- `hired_by_architect_id='$PRODUCTMIND_ID'`
- the hierarchical agent list shows `alice` nested under `productmind`

### 2.6 Create the architect-routed task

In the `productmind` session, paste this prompt after replacing `ALICE_ID_VALUE`:

```text
Call architect_task_create with:
{"title":"Implement feature X","group":"loom","assigned_engineer_id":"ALICE_ID_VALUE","suggested_action":"feature/implement"}
Return only the JSON result.
```

Capture the task id:

```bash
TASK_ID="$(sqlite3 "$DB" "SELECT id FROM board_tasks WHERE created_by_architect_id='$PRODUCTMIND_ID' AND task='Implement feature X' ORDER BY rowid DESC LIMIT 1;")"
echo "$TASK_ID"
test -n "$TASK_ID"
sqlite3 "$DB" "SELECT id, assigned_engineer_id, created_by_architect_id, suggested_action FROM board_tasks WHERE id='$TASK_ID';"
```

Expected:

- `assigned_engineer_id='$ALICE_ID'`
- `created_by_architect_id='$PRODUCTMIND_ID'`
- `suggested_action='feature/implement'`
- the task appears in alice's scope in the UI

## Phase 3 — Engineer dispatch and worker execution

### 3.1 Verify alice's scoped engineer session

Open alice's engineer terminal and paste:

```text
Before dispatching anything:
1. Use a shell command to print the LOOM_ENGINEER_ID environment variable.
2. Call engineer_agents_list with an empty object.
3. Call engineer_board_summary with an empty object.
Return the environment value followed by the two JSON results.
```

Expected:

- `LOOM_ENGINEER_ID` matches `$ALICE_ID`
- `engineer_agents_list()` shows alice and only agents alice owns
- `engineer_board_summary()` includes `Implement feature X`

### 3.2 Dispatch a worker from alice

In alice's session, paste:

```text
Call engineer_task_dispatch with:
{"task":"TASK_ID_VALUE","name":"feature-x-worker"}
Replace TASK_ID_VALUE with the real task id before sending.
Return only the JSON result.
```

Capture the new worker id and branch:

```bash
WORKER_ID="$(sqlite3 "$DB" "SELECT id FROM agents WHERE kind='worker' AND owner_engineer_id='$ALICE_ID' ORDER BY rowid DESC LIMIT 1;")"
WORKER_BRANCH="$(sqlite3 "$DB" "SELECT worktree_branch FROM agents WHERE id='$WORKER_ID';")"
echo "WORKER_ID=$WORKER_ID"
echo "WORKER_BRANCH=$WORKER_BRANCH"
test -n "$WORKER_ID"
test -n "$WORKER_BRANCH"
```

Expected:

- the worker row has `owner_engineer_id='$ALICE_ID'`
- `WORKER_BRANCH` matches `loom/alice/<worker-slug>-<shortid>`
- `git -C "$(git rev-parse --show-toplevel)" branch --list 'loom/alice/*'` shows the new branch

### 3.3 Worker reports progress

Open the worker terminal and run:

```bash
loom ai progress "working on it"
```

Expected: alice sees the worker progress update in the board / agent history.

### 3.4 Worker derives a follow-up task

In the worker terminal, run:

```bash
loom ai derive "add a regression test" -a feature/implement
```

Capture the derived task id and verify ownership inheritance:

```bash
DERIVED_TASK_ID="$(sqlite3 "$DB" "SELECT id FROM board_tasks WHERE parent_task_id='$TASK_ID' ORDER BY rowid DESC LIMIT 1;")"
echo "$DERIVED_TASK_ID"
test -n "$DERIVED_TASK_ID"
sqlite3 "$DB" "SELECT id, parent_task_id, assigned_engineer_id, action_name FROM board_tasks WHERE id='$DERIVED_TASK_ID';"
```

Expected:

- `parent_task_id='$TASK_ID'`
- `assigned_engineer_id='$ALICE_ID'`
- `action_name='feature/implement'`

### 3.5 Worker asks a blocking question

In the worker terminal, run:

```bash
loom ai ask "should X be configurable or hardcoded?"
```

Capture the newest child task (the human-ask task):

```bash
ASK_TASK_ID="$(sqlite3 "$DB" "SELECT id FROM board_tasks WHERE parent_task_id='$TASK_ID' ORDER BY rowid DESC LIMIT 1;")"
echo "$ASK_TASK_ID"
test -n "$ASK_TASK_ID"
sqlite3 "$DB" "SELECT id, lane, labels FROM board_tasks WHERE id='$ASK_TASK_ID';"
```

Expected:

- the ask task is in `Backlog`
- the worker's parent task shows `Awaiting Input`
- alice can see the ask in the board UI

## Phase 4 — Engineer escalation and architect response

### 4.1 Alice escalates the worker question to productmind

In alice's engineer session, paste this prompt after replacing `PRODUCTMIND_ID_VALUE`:

```text
Call engineer_message_architect with:
{"architect_id":"PRODUCTMIND_ID_VALUE","message":"worker is asking whether X should be configurable; need product call"}
Return only the JSON result.
```

Copy the returned `message_id` into a shell variable:

```bash
ENGINEER_TO_ARCHITECT_MESSAGE_ID="paste-the-message_id-from-the-tool-result-here"
echo "$ENGINEER_TO_ARCHITECT_MESSAGE_ID"
test -n "$ENGINEER_TO_ARCHITECT_MESSAGE_ID"
```

### 4.2 Productmind accepts the decision and replies

In the `productmind` session, paste this prompt after replacing `DECISION_ID_VALUE` and `MESSAGE_ID_VALUE`:

```text
1. Call architect_decision_update with:
{"id":"DECISION_ID_VALUE","rationale":"Configurable — will matter for enterprise","status":"accepted"}
2. Call architect_reply with:
{"message_id":"MESSAGE_ID_VALUE","message":"configurable, please"}
Return only the two JSON results.
```

Verify the decision row:

```bash
sqlite3 "$DB" "SELECT id, status, rationale FROM decisions WHERE id='$DECISION_ID';"
```

Expected:

- the decision is now `accepted`
- alice receives the architect reply in her engineer session

### 4.3 Alice relays the answer to the worker

In alice's engineer session, paste this prompt after replacing `WORKER_ID_VALUE`:

```text
Call engineer_agent_message with:
{"agent":"WORKER_ID_VALUE","message":"make it configurable"}
Return only the JSON result.
```

Expected: the message appears in the worker session / history.

## Phase 5 — Worker completion, engineer review, and merge

### 5.1 Worker completes the task

In the worker terminal, run:

```bash
loom ai done "feature X with configurable option"
```

Expected: the worker task moves to `Done` and alice sees the completion update.

### 5.2 Alice reviews the diff and merges the worker branch

In alice's engineer session, paste these prompts after replacing `WORKER_ID_VALUE`:

```text
Call engineer_diff with:
{"agent":"WORKER_ID_VALUE","summary_only":true}
Return only the JSON result.
```

```text
Call engineer_merge with:
{"agent":"WORKER_ID_VALUE"}
Return only the JSON result.
```

Verify the worker branch naming and clean merge outcome:

```bash
echo "$WORKER_BRANCH"
git -C "$(git rev-parse --show-toplevel)" branch --list "$WORKER_BRANCH"
```

Expected:

- the diff summary call succeeds
- the merge call succeeds
- the branch name used for the worker is namespaced under `loom/alice/`

## Phase 6 — Postflight doctor and persistence

### 6.1 Run doctor after the whole flow

```bash
loom doctor
```

Expected:

- exit code `0`
- `Result: PASS`
- `architect:   1`
- `engineer:    2` (the original `Weaver` engineer plus `alice`)
- `[pending_hires]` shows `pending: 0`
- `[worktrees]` is populated and `nonconforming:         0`

Capture the post-run counts:

```bash
loom doctor --json | jq '{result: .result, architects: .architects.total, engineers: .engineers.total, pending_hires: .pending_hires.pending, worktrees: .worktrees}'
sqlite3 "$DB" "SELECT COUNT(*) FROM decisions WHERE architect_id='$PRODUCTMIND_ID' AND status='accepted' AND archived=0;"
```

Expected:

- the JSON report shows `architects = 1`, `engineers = 2`, `pending_hires = 0`
- the accepted-decision count is `1`

### 6.2 Restart the daemon and confirm persistence

Stop Loom, then restart it from the Scripts menu:

```bash
make stop
```

Restart from:

```text
iTerm2 → Scripts → loom
```

Then re-run these checks:

```bash
loom doctor
sqlite3 "$DB" "SELECT id, name, kind, hired_by_architect_id FROM agents WHERE id IN ('$PRODUCTMIND_ID', '$ALICE_ID', '$WORKER_ID') ORDER BY rowid;"
sqlite3 "$DB" "SELECT id, status, rationale FROM decisions WHERE id='$DECISION_ID';"
sqlite3 "$DB" "SELECT id, task, assigned_engineer_id, parent_task_id, pipeline_root_id FROM board_tasks WHERE id IN ('$TASK_ID', '$DERIVED_TASK_ID', '$ASK_TASK_ID') ORDER BY rowid;"
```

Expected: the architect, engineer, decision, and task rows all persist across restart.

## Communication-graph rejection checks

Run these after the main happy path so you know the flow still works before you
start probing rejection cases.

### R1. Productmind cannot target a worker directly

In the `productmind` session, paste this prompt after replacing `WORKER_ID_VALUE`:

```text
1. Confirm whether architect_agent_message exists in the current tool list.
2. Call architect_engineer_message with:
{"engineer_id":"WORKER_ID_VALUE","message":"this should fail"}
Return the exact tool availability result and the exact error.
```

Expected:

- there is no `architect_agent_message` tool
- `architect_engineer_message` with the worker id fails with `engineer not found in scope`

### R2. Weaver cannot message alice's worker

In the default `Weaver` engineer session, paste this prompt after replacing `WORKER_ID_VALUE`:

```text
Call weaver_agent_message with:
{"agent":"WORKER_ID_VALUE","message":"this should fail"}
Return only the exact JSON result or error.
```

Expected: the call is rejected because the worker is scoped to alice, not the default engineer.

### R3. Alice cannot message an architect that did not hire her

Add a second architect in the UI named `strategymind`, then capture its id:

```bash
STRATEGYMIND_ID="$(sqlite3 "$DB" "SELECT id FROM agents WHERE kind='architect' AND name='strategymind' ORDER BY rowid DESC LIMIT 1;")"
echo "$STRATEGYMIND_ID"
test -n "$STRATEGYMIND_ID"
```

In alice's engineer session, paste this prompt after replacing `STRATEGYMIND_ID_VALUE`:

```text
Call engineer_message_architect with:
{"architect_id":"STRATEGYMIND_ID_VALUE","message":"this should fail"}
Return only the exact JSON result or error.
```

Expected: the call fails with `architect not found in scope` because alice is hired by `productmind`, not `strategymind`.

## Rollback (stage-4/5 migration)

The safest rollback path is to restore the pre-kinds backup from Stage 1 and
then relaunch a build that still understands the pre-kinds schema.

Stop Loom first:

```bash
make stop
```

Verify the backup exists:

```bash
ls -lh "$BACKUP"
```

Restore it over the live database:

```bash
cp "$BACKUP" "$DB"
```

Then restart the older compatible Loom build from the Scripts menu.

If you need to preserve the current migrated database before rolling back,
copy it aside first:

```bash
cp "$DB" "$DB.stage5-debug-copy"
```
