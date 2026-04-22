# Compact WebSocket Snapshot v1

`compact-v1` is an opt-in WebSocket snapshot contract for clients that lazy-load
heavy board data instead of receiving every persisted field on initial connect.
The legacy full snapshot remains the default and keeps its existing shape.

## Opt-in

Use either URL query opt-in before the socket opens:

```text
/ws?compact=1
/ws?protocol_version=compact-v1
```

or send a WebSocket connect/resync payload with the protocol flag:

```json
{ "type": "connect", "protocol_version": "compact-v1" }
{ "cmd": "resync", "protocol_version": "compact-v1" }
```

Query opt-in is preferred because it applies to the first snapshot frame.

## Initial `state` snapshot

The frame still has `type: "state"`, `seq`, runtime/provider metadata, agents,
groups, settings, lanes, schedules, filters, and other lightweight UI state. It
also includes:

```json
{ "snapshot_protocol": "compact-v1" }
```

### Compact `board_tasks` entries

Each non-archived task in `board_tasks` is a card summary with exactly these
fields:

```json
{
  "id": "LOOM:134",
  "task": "short title",
  "slug": "short-title",
  "group": "loom",
  "lane": "In Progress",
  "position": 39,
  "action_name": "feature/implement",
  "labels": ["performance"],
  "agent_id": "b1d337a4",
  "assigned_engineer_id": "f944dd2c",
  "parent_task_id": "",
  "pipeline_depth": 0,
  "status": "",
  "health_state": "healthy",
  "verification_state": "pending",
  "verification_mode": "deploy",
  "lane_entered_at": "2026-04-22T23:30:03.758052+00:00"
}
```

The compact summary intentionally omits heavy/detail fields such as `messages`,
`description`, `context`, `criteria`, `instructions`, `attachments`, `artifacts`,
`health_details`, `verification_summary`, `verification_notes`,
`worktree_boundary`, and `action_vars`.

Archived tasks are omitted from the initial compact snapshot entirely.

## Lazy-load commands

All commands are direct WebSocket commands and return a single response frame.

### `task_detail`

Fetches one full `BoardTask` dict for modals/history/detail panels.

Request:

```json
{ "cmd": "task_detail", "id": "LOOM:134" }
```

Response:

```json
{ "type": "task_detail", "id": "LOOM:134", "task": { "id": "LOOM:134" } }
```

`task` is the same full BoardTask shape used by the legacy full snapshot.

### `decisions_snapshot`

Fetches deferred architect decisions.

```json
{ "cmd": "decisions_snapshot" }
```

Response shape:

```json
{ "type": "decisions_snapshot", "decisions": { "decision-id": {} } }
```

### `pending_hires_snapshot`

Fetches deferred pending hires. Defaults to `status_filter: "pending"`.

```json
{ "cmd": "pending_hires_snapshot" }
```

Response shape:

```json
{ "type": "pending_hires_snapshot", "pending_hires": { "hire-id": {} } }
```

### `archived_tasks`

Fetches full archived tasks on archived-tab open. `group` is optional.

```json
{ "cmd": "archived_tasks", "group": "loom" }
```

Response shape:

```json
{ "type": "archived_tasks", "group": "loom", "board_tasks": { "task-id": {} } }
```

### `weaver_journal_snapshot`

Fetches deferred per-group Weaver panel data.

```json
{ "cmd": "weaver_journal_snapshot", "group": "loom", "limit": 50 }
```

Response shape:

```json
{
  "type": "weaver_journal_snapshot",
  "group": "loom",
  "weaver_journal": { "loom": [] },
  "weaver_worklog": { "loom": [] },
  "weaver_streams": { "loom": { "count": 0, "by_state": {}, "items": [], "truncated": false } }
}
```

## Delta compatibility

Delta ops are unchanged. In particular, `task_upsert` continues to carry the full
BoardTask dict even for compact clients. A compact client can merge a delta-only
change without immediately re-fetching `task_detail`.

## Frontend migration notes

1. Opt in via `/ws?compact=1` only after the client can handle compact task
   summaries and missing deferred maps.
2. Initialize `state.decisions` and `state.pending_hires` to `{}` locally, then
   merge `decisions_snapshot` and `pending_hires_snapshot` responses when they
   arrive.
3. Open task detail/modal/history flows by fetching `task_detail` when the local
   task entry lacks full-detail fields.
4. Load archived tasks only when the archived tab opens.
5. Load Weaver journal/worklog/streams when the Weaver panel opens for a group.
6. Keep existing delta application logic; compact mode does not change op names
   or payload shapes.
