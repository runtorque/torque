# Compact snapshot v1

`compact-v1` is the default browser WebSocket snapshot protocol. The client opts in by connecting with `?compact=1` (the bundled frontend does this unless `localStorage["torque:snapshot_protocol"]` is `legacy`, `off`, `0`, or `false`). Legacy snapshots remain available for rollback and CLI/offline SQLite reads are unchanged.

## Default state payload

The default `board_tasks` map contains active, non-archived task cards only. Card entries keep fields needed for board lanes, labels, dispatch, health/verification badges, external links, dependency badges, and branch-boundary grouping.

The following task fields are intentionally omitted from compact cards and are loaded through `cmd: "task_detail"` when a focused surface needs them:

- `description`, `instructions`, `context`, `criteria`
- `action_vars`, `agent_template`
- `attachments`, `artifacts`
- full `messages` activity history (compact cards carry a count/last-action preview)
- full `messages_thread` inline message bodies (compact cards carry `messages_thread_summary`)
- `verification_notes`, full `verification_summary`, and `completion_evidence`
- provider-specific board-sync history/details (compact cards carry common sync state)
- full worktree-boundary detail (compact cards carry branch/PR summary fields)

Archived tasks, architect decisions, pending hires, engineer journals/worklog, and engineer streams are also deferred to explicit lazy-load commands.

## Lazy-load commands

- `task_detail` returns one full `BoardTask` dict and resolves task aliases.
- `archived_tasks` returns full archived task rows, optionally group-scoped.
- `decisions_snapshot`, `pending_hires_snapshot`, and `engineer_journal_snapshot` return their deferred snapshot slices.

WebSocket `task_upsert` deltas remain backward-compatible full task upserts so focused/hydrated views stay correct across mixed legacy and compact clients.
