# Stage 6 Final Acceptance

This smoke confirms the full kinds-refactor cleanup shipped and that no legacy stage-1-through-5 compatibility path is still active.

## Preconditions

- Start from a stage-5 style repo / DB with at least:
  - one engineer
  - one worker with a role-backed launch preset
  - one task assigned to that engineer
- Also keep one legacy `~/.loom/agents/*.yaml` file around for the ignored-file warning check.

## 1. Normal boot migrates to cleanup-complete state

Start Loom on a normally migrated database.

Expected:

- boot succeeds
- `loom doctor` reports `Result: PASS` or `PASS (with warnings)`
- `[stage_6_cleanup]` is present
- `legacy_columns_present: false`
- `weaver_tool_aliases_present: false`
- if `~/.loom/agents/*.yaml` still exists without a matching role, `legacy_template_files_ignored` is non-zero and the warning names the ignored file(s)

## 2. Legacy role files are ignored

With a file that exists only under `~/.loom/agents/`:

- confirm Loom logs a warning on startup naming the ignored file and directing you to move it into `~/.loom/roles/`
- confirm the role editor / loader does not surface that file unless an equivalent slug exists under `roles/`

## 3. Stage-5 end-to-end scenario still works

Run the stage-5 scenario again:

1. architect creates or updates a decision
2. architect creates a task and assigns it to an engineer
3. engineer dispatches a worker
4. worker reports progress, derives a follow-up, and completes
5. engineer / architect messaging and approval flows still behave normally

Expected:

- ownership fields remain correct (`owner_engineer_id`, `assigned_engineer_id`, `created_by_architect_id`, `hired_by_architect_id`)
- worker prompts still include role preamble / priorities when configured
- no `weaver_*` tool name appears anywhere in the active flow

## 4. Unsupported direct upgrade is refused

Try booting this version against a pre-stage-1 DB (legacy columns populated, new kinds fields empty).

Expected:

- Loom refuses to start before serving HTTP
- stderr / `loom.log` include the actionable migration error directing the operator to install Loom 1.x first, boot once, then upgrade to Loom 2.0.0

## 5. Schema cleanup verification

Check the live database directly:

```bash
sqlite3 "$DB" ".schema agents"
sqlite3 "$DB" ".schema board_tasks"
```

Expected:

- `agents` no longer contains `template` or `created_by_weaver_id`
- `board_tasks` no longer contains `weaver_owner_id`
- `meta.schema_kinds_migration_version` is `3`
