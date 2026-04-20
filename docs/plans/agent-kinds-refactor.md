# Agent Kinds Refactor

This path is the stable documentation anchor for the staged Agent Kinds
Refactor referenced by migration notes and contributor guidance.

## 1.2 Legacy ownership model

- Before the kinds refactor, task ownership was group-level.
- There was never a persisted per-task `board_tasks.weaver_owner_id` column in
  Loom's on-disk schema.
- Stage 1 therefore maps `board_tasks.assigned_engineer_id` from
  `group_settings.weaver_agent_id` for the task's `group_name`.
- If a task's group has no configured `weaver_agent_id`, that task remains
  unassigned and is surfaced by `loom doctor` as a warning when an engineer is
  present elsewhere in the database.

## 1.6 Schema + data migration notes

- schema migration for the new `agents`/`board_tasks` kinds columns
- one-shot Weaver/worker backfill
- dual-write protection between legacy and new columns
- `loom doctor` verification surface
- manual smoke protocol: [Stage 1 Kinds Refactor Acceptance](../testing/stage1-kinds-acceptance.md)

Additional stage-1 data rules:

- `agents.role` is backfilled from `agents.template`.
- `agents.owner_engineer_id` is backfilled from `agents.created_by_weaver_id`.
- `board_tasks.assigned_engineer_id` is backfilled from
  `group_settings.weaver_agent_id` for the task's group.
- Rows whose group has no `weaver_agent_id` stay unassigned; this is expected
  and `loom doctor` reports it as a warning rather than a drift failure.

## 10. Risks

- False-green drift counters can hide real semantic gaps when both sides of a
  legacy/new column pair are empty.
- Stage 1 mitigates that risk by keeping drift counters for true column drift
  while also having `loom doctor` emit a warning when an engineer exists but
  tasks remain unassigned.
