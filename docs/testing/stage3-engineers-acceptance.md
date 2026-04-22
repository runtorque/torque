# Stage 3 Engineers Acceptance

> Manual smoke for Stage 3 of the Agent Kinds Refactor: multi-engineer MCP
> scoping, engineer lifecycle UI, CLI assignment flags, and doctor reporting.

This assumes Stage 1 and Stage 2 are already deployed and healthy, and that the
existing migrated Weaver engineer is still present.

## Preflight

Record the current engineer baseline before deploy:

```bash
loom engineer list
```

Expected: at least the existing `Weaver` engineer is listed.

Keep the live database and backup paths handy:

```bash
LOOM_DIR="$HOME/Library/Application Support/iTerm2/Scripts/loom/loom"
DB="$LOOM_DIR/loom.db"
BACKUP="$LOOM_DIR/loom.db.pre-kinds.bak"
LOG="$LOOM_DIR/loom.log"
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

If you want to watch startup in parallel:

```bash
tail -f "$LOG"
```

## Verify `loom doctor`

Run:

```bash
loom doctor
```

Expected:

- exit code `0`
- `Result: PASS`
- an `[engineers]` section is present
- `total` is `1`
- the default `weaver_*` routing target is `Weaver`

## Add a second engineer

1. Open the **Agent** panel.
2. Click **+ Add Engineer**.
3. Enter `alice` as the name.
4. Keep the default boot command unless you need a local override.
5. Submit the modal.

Expected:

- `alice` appears in the Agent panel immediately
- `alice` also appears in the main agent list
- `loom engineer list` now shows both `Weaver` and `alice`

## Assign work to Alice

Create a task explicitly assigned to Alice from the CLI:

```bash
loom task create "Stage 3 smoke task" --group loom --engineer alice
```

Expected:

- the task is created successfully
- `loom --json task show <task-slug-or-id>` includes Alice's `assigned_engineer_id`

If you prefer a UI-only smoke, assign the task to Alice through the task modal
instead, but keep the equivalent expectation: the task must end up with
`assigned_engineer_id=<alice.id>`.

## Verify Alice can create and own a worker

Open Alice's MCP-bound session and use the engineer tool surface to create or
launch a worker through the normal engineer flow.

Expected in the UI:

- the new worker appears indented underneath `alice`
- it does **not** appear under `Weaver`

## Verify engineer MCP scoping

From Alice's MCP session, run `engineer_agents_list`.

Expected:

- the response includes `alice`
- the response includes Alice's worker(s)
- the response does **not** include `Weaver`
- the response does **not** include Weaver-owned workers

## Verify legacy `weaver_*` compatibility

From the original Weaver engineer session, run a legacy alias such as
`weaver_agents_list`.

Expected:

- the call still succeeds
- the response reflects Weaver's scope, not Alice's

## Delete Alice and verify orphan transfer

1. In the **Agent** panel, click **Delete** for `alice`.
2. Confirm the modal text includes the worker/task transfer counts.
3. Confirm the deletion.

Expected:

- Alice disappears from the Agent panel
- Alice disappears from the main agent list
- Alice's former worker(s) move to the user-owned section at the bottom
- Alice's former task(s) become unassigned instead of disappearing

Re-run:

```bash
loom doctor
```

Expected:

- exit code `0`
- `Result: PASS`
- one engineer remains
- no new drift is reported

## Restart persistence check

Restart Loom again from the Scripts menu.

Expected after restart:

- `alice` stays deleted
- `Weaver` still exists and is still persistent/relaunchable
- the transferred worker(s) remain user-owned
- `loom doctor` still reports a healthy Stage 3 state

## Recovery / rollback

If the migration or engineer state is corrupted, stop Loom and restore the
stage-1 backup:

```bash
make stop
cp "$BACKUP" "$DB"
```

Then restart Loom from the Scripts menu and re-run the acceptance flow after
fixing the underlying issue.
