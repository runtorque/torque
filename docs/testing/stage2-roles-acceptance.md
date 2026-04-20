# Stage 2 Roles Acceptance

> Manual smoke for Stage 2 of the Agent Kinds Refactor: roles, worker
> preambles, and the Roles UI. Run this after the stage-2 branch is deployed.

This assumes an existing Loom install with at least one working group and one
worker-capable dispatch flow already available.

## Preflight

Record the current legacy template baseline:

```bash
find "$HOME/.loom/agents" -type f \( -name '*.yaml' -o -name '*.yml' \) | wc -l
```

Expected: a numeric count. Keep it for comparison after deploy.

If you already have a stage-2 smoke role from a prior run, remove it first:

```bash
rm -f "$HOME/.loom/roles/careful-reviewer.yaml"
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

## Verify the Roles UI surface

1. Open the **Agents** panel and switch to the **Roles** view.
2. Confirm the header reads **Role Library**.
3. Confirm previously-existing legacy templates still appear in the list.

Expected:

- the Roles view opens without errors
- legacy entries are still visible
- no existing role/template names disappeared

## Create a new role

Create a role with these fields:

- Name: `careful-reviewer`
- Preamble (behavior): `Be careful.`
- Priorities:
  - `ship small`
  - `test first`

Save it in **User** scope unless you specifically want a project-local smoke.

Expected:

- the role appears immediately in the Roles list
- re-opening the role shows the same preamble and priorities

Verify the write path:

```bash
cat "$HOME/.loom/roles/careful-reviewer.yaml"
```

Expected:

- the file exists under `~/.loom/roles/`
- no new file was written under `~/.loom/agents/`
- the YAML contains `preamble:` and `priorities:`

## Assign the role through group settings

1. Open **Group settings** for a group that dispatches worker tasks.
2. Find the **Default role** dropdown.
3. Select `careful-reviewer`.
4. Save the settings.

Expected:

- the saved group settings keep `careful-reviewer` selected
- existing agents/tasks continue to load normally

## Verify task preview includes the role preamble

1. Open a task in the task modal for that same group.
2. Choose an action that renders a prompt preview.
3. Click **Preview**.

Expected:

- the preview begins with:

  ```text
  Be careful.

  Priorities:
  - ship small
  - test first
  ```

- the action prompt appears below that block
- the usual Loom postscript still appears at the end

## Verify action opt-out

1. Open the **Actions** editor.
2. Edit the same action used above.
3. Enable **Disable role preamble (don't inject the worker's role preamble for this action)**.
4. Save the action.
5. Re-open the task modal and click **Preview** again.

Expected:

- the preamble block is gone
- the action prompt still renders
- the Loom postscript is still present

Turn the checkbox back off before continuing if you do not want to keep the
opt-out enabled.

## Verify dispatched prompt matches preview

1. From the task modal, dispatch the task to a worker that will inherit the
   selected/default role.
2. Open the receiving agent's transcript/output.
3. Compare the dispatched prompt with the copied **Preview** output.

Expected:

- the agent receives the same rendered prompt shown in Preview
- when opt-out is disabled, the role preamble is present at the top
- when opt-out is enabled, the preamble is absent

## Verify `loom doctor`

Run:

```bash
loom doctor
```

Expected:

- exit code `0`
- `Result: PASS`
- a `[roles]` section is present
- `roles_dir` points at `~/.loom/roles`
- the role counts include the new role
- warnings are empty unless you intentionally introduced a shadowed legacy file

## Verify persistence after restart

Restart Loom again from the Scripts menu.

Expected after restart:

- `careful-reviewer` is still present in the Roles list
- `loom doctor` still reports the roles section correctly
- no migration rerun is required
- dispatch preview behavior is unchanged after restart

## Recovery / rollback

To revert only the stage-2 role files created by this smoke:

```bash
rm -rf "$HOME/.loom/roles"
```

Then restart Loom. Legacy templates under `~/.loom/agents/` remain available
through compatibility reads.
