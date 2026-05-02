# Compact WebSocket Snapshot v1

`compact-v1` is the default WebSocket snapshot contract for clients that
lazy-load heavy board data instead of receiving every persisted field on
initial connect. It eagerly keeps the small board-semantic fields the
standalone UI needs for sorting, dependency traversal, verification/health
previews, external-link badges, and branch-boundary notes. The legacy full
snapshot is still available as an opt-out for rollback and for test harnesses
that want the full shape.

## Opt-in / opt-out

The official frontend ships compact-v1 by default. It appends `?compact=1`
to the WebSocket URL unless the operator explicitly opts out by setting
`localStorage["torque:snapshot_protocol"]` to one of `legacy`, `off`, `0`, or
`false`. Backend handshake accepts any of these equivalent opt-in signals,
which external consumers or pinned tests can use explicitly:

URL query opt-in before the socket opens:

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

### Rollback to the legacy snapshot

The official frontend opts in to compact-v1 whenever
`localStorage["torque:snapshot_protocol"]` is unset or holds any non-opt-out
value, so **clearing the key does not roll back** — it leaves the client
on compact. To drop a browser session back onto the legacy full snapshot,
explicitly store one of the opt-out sentinels before reloading:

```js
// In devtools, on the Torque origin:
localStorage.setItem('torque:snapshot_protocol', 'legacy');
// then reload the page
```

`legacy`, `off`, `0`, and `false` all count as opt-out sentinels. The
backend still honours any client that omits both the `?compact=1` query
and the `protocol_version`/`compact` payload flags, so a pinned external
consumer can keep the legacy shape by simply not sending any of the
opt-in signals listed above.

## Initial `state` snapshot

The frame still has `type: "state"`, `seq`, runtime/provider metadata, agents,
groups, settings, lanes, schedules, filters, and other lightweight UI state. It
also includes:

```json
{ "snapshot_protocol": "compact-v1" }
```

### Compact `board_tasks` entries

Each non-archived task in `board_tasks` is a card summary with exactly these
eager fields:

```json
{
  "id": "TORQUE:134",
  "task": "short title",
  "slug": "short-title",
  "group": "torque",
  "lane": "In Progress",
  "position": 39,
  "action_name": "feature/implement",
  "labels": ["performance"],
  "agent_id": "b1d337a4",
  "assigned_engineer_id": "f944dd2c",
  "parent_task_id": "",
  "pipeline_depth": 0,
  "status": "",
  "created_at": "2026-04-22T22:00:00+00:00",
  "updated_at": "2026-04-22T23:30:03.758052+00:00",
  "scheduled_at": "",
  "depends_on": ["TORQUE:120"],
  "provider": "github",
  "external_id": "123",
  "external_url": "https://github.com/openai/openai/issues/123",
  "health_state": "healthy",
  "health_since": "2026-04-22T23:30:03.758052+00:00",
  "health_details": { "reasons": ["recent_activity"] },
  "verification_state": "pending",
  "verification_mode": "deploy",
  "verification_notes": "needs smoke",
  "verification_summary": { "tests_run": "targeted" },
  "messages": [
    {
      "count": 3,
      "action": "progress",
      "message": "3 updates · last progress"
    }
  ],
  "lane_entered_at": "2026-04-22T23:30:03.758052+00:00",
  "worktree_boundary": {
    "repo_root": "/repo",
    "branch": "feature/review-point",
    "status": "open"
  },
  "resume_after_boundary_task_id": ""
}
```

### Eager card fields

Compact cards eagerly carry:

- card identity/layout fields (`id`, `task`, `slug`, `group`, `lane`,
  `position`, `labels`, `action_name`, assignee/pipeline status fields)
- timestamps used by board sorting/chips (`created_at`, `updated_at`,
  `lane_entered_at`, `scheduled_at`)
- dependency + external-link metadata (`depends_on`, `provider`, `external_id`,
  `external_url`)
- health + verification previews (`health_state`, `health_since`,
  `health_details`, `verification_state`, `verification_mode`,
  `verification_notes`, `verification_summary`)
- branch-boundary metadata (`worktree_boundary`,
  `resume_after_boundary_task_id`)
- `messages` as a metadata-only preview list: zero or one preview entry. When
  present, `count` is the total number of task messages and `action` is the
  last message type. `message` is a derived label only; full message bodies are
  still deferred.

### Deferred detail fields

The compact summary still defers heavy/detail fields such as `description`,
`instructions`, `context`, `criteria`, `action_vars`, `agent_template`,
`attachments`, `artifacts`, and full `messages` bodies.

Archived tasks are omitted from the initial compact snapshot entirely.

### Description search scope under compact mode

The board's text search matches across eager fields (title, id, action,
agent, labels, verification notes, etc.) for every card. Description
bodies stay lazy, so the search only looks inside `description` once the
task's full detail has been hydrated — typically after the user opens its
detail panel, edit modal, or artifact browser. The board's search input
exposes this via a `title` tooltip so operators can opt in by clicking
into the tasks they want described-text search to cover. A future phase
may add a server-side text-search command if broader coverage is needed.

## Lazy-load commands

All commands are direct WebSocket commands and return a single response frame.

### `task_detail`

Fetches one full `BoardTask` dict for modals/history/detail panels.

Request:

```json
{ "cmd": "task_detail", "id": "TORQUE:134" }
```

Response:

```json
{ "type": "task_detail", "id": "TORQUE:134", "task": { "id": "TORQUE:134" } }
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
{ "cmd": "archived_tasks", "group": "torque" }
```

Response shape:

```json
{ "type": "archived_tasks", "group": "torque", "board_tasks": { "task-id": {} } }
```

### `engineer_journal_snapshot`

Fetches deferred per-group Engineer panel data.

```json
{ "cmd": "engineer_journal_snapshot", "group": "torque", "limit": 50 }
```

Response shape:

```json
{
  "type": "engineer_journal_snapshot",
  "group": "torque",
  "engineer_journal": { "torque": [] },
  "engineer_worklog": { "torque": [] },
  "engineer_streams": { "torque": { "count": 0, "by_state": {}, "items": [], "truncated": false } }
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
5. Load Engineer journal/worklog/streams when the Engineer panel opens for a group.
6. Keep existing delta application logic; compact mode does not change op names
   or payload shapes.
