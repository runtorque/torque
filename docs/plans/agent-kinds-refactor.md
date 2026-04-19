# Agent Kinds Refactor

This path is the stable documentation anchor for the staged Agent Kinds
Refactor referenced by migration notes and contributor guidance.

## Stage 1 snapshot

- schema migration for the new `agents`/`board_tasks` kinds columns
- one-shot Weaver/worker backfill
- dual-write protection between legacy and new columns
- `loom doctor` verification surface
- manual smoke protocol: [Stage 1 Kinds Refactor Acceptance](../testing/stage1-kinds-acceptance.md)
