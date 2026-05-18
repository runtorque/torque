# Architect-to-Architect Communication Surface

Status: research plan  
Task: `TORQUE:443` / `feature/research`  
Date: 2026-05-18

## Scope and recommendation

Ship a **same-group direct peer messaging surface** for Architects first.

The first version should let one Architect:

- discover other Architects in the same group;
- send a durable direct message to one named Architect;
- mark a message as `ack_required` when it is a question/decision request;
- reply in the same thread with the existing `architect_reply` mental model;
- attach lightweight context references for tasks/engineers/decisions;
- recover pending/undelivered peer messages after `/clear`, daemon restart, dismissal, or rehire;
- see the thread in the existing Architect Agent Panel `Messages` tab.

Defer these larger primitives until direct messaging proves useful:

- broadcast/shared Architect channel;
- formal task-scope ownership transfer;
- formal Engineer ownership transfer;
- shared/co-authored decision records;
- cross-group Architect messaging.

This gives Architects a coordination path without weakening existing ownership rules around Engineers, tasks, decisions, or journals.

## Current-state findings

### Existing Architect MCP surface

- Architect tool specs live in `torque/mcp_architect.py`.
- Architect/Engineer shared implementation is mostly in `torque/mcp_tools_shared.py` via `dispatch_scoped_tool(...)`.
- Architect write tools are rejected when the caller Architect is dismissed (`dismissed_at > 0`) unless the tool is in `_ARCHITECT_READ_TOOL_NAMES`.
- Architect-scoped state views currently include all non-tombstoned agents in the Architect's group, but specific tools further narrow authority:
  - `architect_engineer_message` requires a hired Engineer via `_resolve_architect_hired_engineer(...)`.
  - `architect_decision_*` only load decisions whose `architect_id == caller_id`.
  - `architect_journal_*` reads/writes only the caller Architect's JSONL journal.

### Existing Architect ↔ Engineer messaging

The closest implementation is `_deliver_architect_engineer_message(...)` in `torque/mcp_tools_shared.py`:

- It creates `msg-...` entries with `id`, `thread_id`, `reply_to_id`, `action`, `sender_id`, `sender_kind`, `peer_id`, `peer_kind`, `direction`, and optional `ack_required`.
- It appends bounded copies to both participants' `AgentCell.mcp_messages`.
- It calls `history_record_message(...)`, which writes to `agent_messages` with only `agent_id`, `task_id`, `timestamp`, `action`, and `message`.
- It injects the message into the recipient terminal through the server `inject_mcp_message` command.
- Delivery failures/no-session are marked on the recipient-side `mcp_messages` entry and replayed by `_replay_buffered_cross_kind_messages(...)` when an Architect/Engineer is rehired.

Important limitation: `AgentCell.mcp_messages` and `pending_engineer_message` are explicitly listed as ephemeral in `torque/state.py`. SQLite persists only the reduced `agent_messages` audit rows, not full thread/peer/delivery metadata. That is acceptable for the current UI cache, but it is not a sufficient source of truth for durable Architect ↔ Architect coordination.

### Existing user-directed Architect asks

`architect_ask` is intentionally different from peer messaging:

- it creates a visible Backlog task with labels `torque:human` and `architect-ask`;
- it sets `status="Awaiting Input"`, `created_by_architect_id`, and `reply_agent_id`;
- user replies are delivered back to the Architect and the task is completed.

This task-backed pattern is good for human approvals but would create board noise if every Architect peer question became a task.

### Existing UI surface

- `static/js/agent_panel.js` already has an Architect `Messages` tab backed by `agent.mcp_messages`.
- The message renderer already understands direction, sender kind, action label, timestamps, virtualized lists, and scroll-anchor preservation.
- `static/js/ws.js` invalidates the Architect message-list cache when an `agent_upsert` includes `mcp_messages`.
- Architect decisions/journals have separate tabs and are per-Architect.

The lowest-risk UI is therefore to extend the current Architect `Messages` tab rather than add a new panel.

### Missing referenced plan

The task referenced `docs/plans/agent-kinds-refactor.md`, but that file is not present in this worktree. I used the current docs instead, especially `docs/team/architects.md`, `docs/team/mcp-scoping.md`, `docs/team/team-model.md`, and `docs/architecture.md`.

## Use cases mapped to primitives

| Use case | Recommended primitive | V1? | Notes |
|---|---|---:|---|
| Ask another Architect for input | Direct peer message with `ack_required=true` | Yes | Durable thread + terminal injection + inbox polling. Not a blocking runtime lock. |
| Send FYI/status to another Architect | Direct peer message with `ack_required=false` | Yes | Does not create a reply obligation. |
| Reply/follow up | Reuse/extend `architect_reply(message_id, message, ack_required=false)` | Yes | Keep one reply muscle memory for Architect threads. |
| Add task/engineer context | Optional context refs on direct message | Yes | Snapshot enough context into the message so the recipient is not forced into private surfaces. |
| Broadcast to all Architects | Group broadcast/channel | No | Defer; creates spam/subscription/product-policy questions. |
| Formal task handoff | Dedicated handoff command changing ownership | No | Defer; it mutates `created_by_architect_id` authority and should require explicit acceptance. |
| Formal Engineer handoff | Dedicated transfer command changing `hired_by_architect_id` | No | Defer; this is budget/ownership-sensitive and likely user-approved. |
| Shared decision | Owner-local decision + peer notification | Partial | Do not add shared decisions in V1. Discussed decisions should be recorded by the owning Architect and linked/mentioned in the message thread. |

## Options considered

### Option A — Reuse only `AgentCell.mcp_messages`

**Shape:** Add Architect ↔ Architect entries to the existing per-agent message cache.

**Pros**

- Minimal code.
- The existing Agent Panel `Messages` tab can render it quickly.
- Existing injection/replay code almost works.

**Cons**

- `mcp_messages` is intentionally ephemeral and not persisted across daemon restart.
- Existing `agent_messages` audit rows lack thread/peer/delivery/context fields.
- No reliable inbox query for boot recovery.

**Decision:** Reject as the source of truth. Keep `mcp_messages` only as a bounded UI/projection cache.

### Option B — Model peer asks as board tasks

**Shape:** `architect_peer_ask` creates a Backlog task assigned/replying to the recipient Architect.

**Pros**

- Durable today with no new table.
- Visible on the board and fits blocking-like asks.

**Cons**

- Too noisy for ordinary peer coordination.
- Awkward for non-blocking FYIs and ongoing chat threads.
- Board tasks imply work ownership/lane management, not conversation.

**Decision:** Reject for V1 direct messages. Keep task-backed asks for human approval and maybe future formal handoff workflows.

### Option C — Broadcast channel

**Shape:** A group-scoped channel that every Architect in a group subscribes to.

**Pros**

- Useful for announcements and shared situational awareness.
- Natural place for group-level decisions.

**Cons**

- Needs subscription/unread semantics and spam controls.
- Harder to keep responsibility clear.
- Does not solve one Architect waiting on one other Architect's answer as cleanly as direct messages.

**Decision:** Defer. Direct messages should ship first; a broadcast channel can be a later layer if usage demands it.

### Option D — Durable peer-message table + direct MCP tools

**Shape:** Add a canonical persisted peer-message store and project recent rows into existing UI caches.

**Pros**

- Durable across restarts and offline periods.
- Supports thread/reply/ack/delivery metadata cleanly.
- Keeps UI simple by reusing the Architect `Messages` tab.
- Can eventually unify Architect ↔ Engineer message persistence too.

**Cons**

- Requires SQLite schema work and migration tests.
- Adds new MCP API surface.
- Needs careful scoping so same-group messaging does not accidentally imply access to decisions/journals/Engineers.

**Decision:** Recommend.

## Recommended V1 design

### Permission model

V1 should use these rules:

1. **Same-group only.** Architect A can message Architect B only if both are non-tombstoned agents with `kind="architect"` and the same `group`.
2. **No self-messages.** Return a validation error for `recipient_architect_id == caller_id`.
3. **Dismissed caller cannot send.** Existing dismissed-Architect mutation gating should apply.
4. **Dismissed recipient can receive buffered messages.** The row is saved, delivery state is `buffered`, and it replays when the recipient is rehired.
5. **Deleted/tombstoned recipient cannot receive new messages.** Existing threads remain readable to the surviving participant but replies should fail with a clear unavailable/tombstoned error.
6. **No opt-in handshake in V1.** Same-group Architects already share board-level product scope. Handshake can be revisited if broadcast/cross-group messaging is added.

### MCP tool surface

Add Architect peer tools; avoid an omnibus tool.

#### `architect_peer_list`

Purpose: discover same-group Architects that can receive messages.

Inputs:

```json
{
  "include_dismissed": false
}
```

Return shape:

```json
{
  "type": "architect_peers",
  "architects": [
    {
      "id": "arch-b",
      "slug": "platform",
      "name": "Platform",
      "group": "Torque",
      "status": "running",
      "dismissed_at": 0,
      "current_task_id": "TORQUE:123",
      "current_task": "Plan transport work"
    }
  ]
}
```

Notes:

- Exclude the caller.
- Exclude tombstoned Architects always.
- Include dismissed Architects only when requested so senders can intentionally leave buffered handoff context.

#### `architect_peer_message`

Purpose: start or continue a direct thread to one named Architect.

Inputs:

```json
{
  "architect_id": "arch-b",
  "message": "Can you sanity-check the API boundary before I route work?",
  "ack_required": true,
  "context_task_ids": ["TORQUE:443"],
  "context_engineer_ids": ["eng-api"],
  "context_decision_ids": ["decision-abc123"],
  "context_summary": "I am deciding whether the API work belongs in your surface."
}
```

Return shape:

```json
{
  "type": "ok",
  "message_id": "msg-...",
  "thread_id": "msg-...",
  "recipient_architect_id": "arch-b",
  "ack_required": true,
  "delivery": {
    "state": "delivered",
    "reason": ""
  }
}
```

Notes:

- `context_*` fields are optional.
- `context_task_ids` must resolve to tasks in the shared group.
- `context_engineer_ids` should accept same-group Engineers that the sender is allowed to mention. If the Engineer is not visible/control-authorized to the recipient, the message should include a lightweight snapshot (id/name/status/hired-by/assigned-task counts) but not grant journal or messaging access.
- `context_decision_ids` should resolve only to the sender's own decisions. Because decisions remain per-Architect, include a snapshot of title/status/rationale excerpt in the message context so the recipient is not forced to read another Architect's private decision store.
- Add a conservative message length cap (for example 8-16 KiB) and return a validation error when exceeded.

#### Extend `architect_reply`

Purpose: reply to both Architect ↔ Engineer and Architect ↔ Architect threads.

Current `architect_reply` assumes the peer is a hired Engineer. Change it to inspect the loaded message entry:

- if `peer_kind == "engineer"`, preserve current hired-Engineer checks and behavior;
- if `peer_kind == "architect"`, use same-group Architect peer checks and deliver `architect_peer_reply` action;
- otherwise return `Message peer kind is not replyable`.

Add optional `ack_required` to Architect replies so an Architect can ask a follow-up question in the same peer thread. This mirrors `engineer_reply`'s existing `ack_required` option.

Return shape remains compatible:

```json
{
  "type": "ok",
  "message_id": "msg-...",
  "thread_id": "msg-original"
}
```

#### `architect_peer_inbox`

Purpose: durable recovery/read tool for boot checklists and `/clear` recovery.

Inputs:

```json
{
  "peer_architect_id": "",
  "thread_id": "",
  "requires_reply": false,
  "since": 0,
  "limit": 20
}
```

Return shape:

```json
{
  "type": "architect_peer_inbox",
  "threads": [
    {
      "thread_id": "msg-...",
      "peer_architect_id": "arch-b",
      "peer_name": "Platform",
      "last_message_at": 1779140000.0,
      "requires_reply": true,
      "messages": [
        {
          "id": "msg-...",
          "thread_id": "msg-...",
          "reply_to_id": "",
          "direction": "received",
          "sender_id": "arch-b",
          "sender_kind": "architect",
          "peer_id": "arch-b",
          "peer_kind": "architect",
          "message": "...",
          "timestamp": 1779140000.0,
          "ack_required": true,
          "delivery_state": "delivered",
          "context": {
            "task_ids": ["TORQUE:443"],
            "engineer_ids": [],
            "decision_ids": [],
            "summary": "..."
          }
        }
      ]
    }
  ]
}
```

`requires_reply` should be computed, not manually toggled: an incoming message with `ack_required=true` requires reply until the caller sends a later message in the same thread.

### Persistence

Add a canonical peer-message table rather than persisting `mcp_messages` directly.

Recommended table name: `agent_peer_messages`.

Suggested columns:

- `id TEXT PRIMARY KEY`
- `thread_id TEXT NOT NULL`
- `reply_to_id TEXT NOT NULL DEFAULT ''`
- `group_name TEXT NOT NULL DEFAULT ''`
- `sender_id TEXT NOT NULL`
- `sender_kind TEXT NOT NULL`
- `recipient_id TEXT NOT NULL`
- `recipient_kind TEXT NOT NULL`
- `message TEXT NOT NULL`
- `created_at REAL NOT NULL`
- `ack_required INTEGER NOT NULL DEFAULT 0`
- `context_task_ids TEXT NOT NULL DEFAULT '[]'`
- `context_engineer_ids TEXT NOT NULL DEFAULT '[]'`
- `context_decision_ids TEXT NOT NULL DEFAULT '[]'`
- `context_summary TEXT NOT NULL DEFAULT ''`
- `context_snapshot TEXT NOT NULL DEFAULT '{}'`
- `delivery_state TEXT NOT NULL DEFAULT 'buffered'` (`delivered`, `buffered`, `failed`)
- `delivery_reason TEXT NOT NULL DEFAULT ''`
- `delivered_at REAL NOT NULL DEFAULT 0`
- `archived_at REAL NOT NULL DEFAULT 0`

Indexes:

- `idx_agent_peer_messages_recipient_recent(recipient_id, created_at DESC, id DESC)`
- `idx_agent_peer_messages_sender_recent(sender_id, created_at DESC, id DESC)`
- `idx_agent_peer_messages_thread(thread_id, created_at ASC, id ASC)`
- `idx_agent_peer_messages_group_recent(group_name, created_at DESC, id DESC)`

Implementation notes:

- Add DDL to `torque/db_schema.py` for new databases.
- Add migration/ensure logic in `torque/db.py` for existing databases.
- Add DB helpers such as:
  - `save_agent_peer_message(row)`
  - `load_agent_peer_message(message_id)`
  - `load_agent_peer_messages_for_agent(agent_id, limit=..., since=..., peer_id=..., thread_id=...)`
  - `load_buffered_agent_peer_messages(recipient_id)`
  - `update_agent_peer_message_delivery(message_id, delivery_state, reason='', delivered_at=...)`
- Keep `agent_messages` as the broad audit/history table. It is not rich enough for the canonical thread store.
- Continue projecting recent peer messages into `AgentCell.mcp_messages` for the existing UI, but treat that field as a bounded cache reconstructed from `agent_peer_messages` on daemon load/Architect rehire.

### Delivery and recovery

The peer-message send flow should be:

1. Resolve/authorize sender and recipient.
2. Validate context references and build `context_snapshot`.
3. Save canonical row in `agent_peer_messages` with `delivery_state='buffered'`.
4. Append projected entries to sender and recipient `mcp_messages` caches.
5. Emit `agent_upsert` for both participants.
6. Attempt `inject_mcp_message` into the recipient's terminal.
7. Update canonical delivery state and recipient cache entry to `delivered` or `buffered/failed` with reason.
8. Record `history_record_message(...)` for audit.

Recovery should happen through two paths:

- `architect_peer_inbox` reads canonical rows on boot and after `/clear`.
- `_replay_buffered_cross_kind_messages(...)` should either read from `agent_peer_messages` directly or work from a cache that was reconstructed from the DB before replay. Prefer reading canonical buffered rows directly to avoid cache-loss bugs.

The injection prompt can reuse `_format_injected_mcp_message_prompt(...)`; for Architect recipients the reply hint should point at `mcp__torque__architect_reply(message_id="...", message="...")`. If `ack_required=false`, phrase the hint as optional or omit it; if `ack_required=true`, make the reply request explicit.

### Visibility and UI

Use the existing Architect Agent Panel rather than building a new panel.

Changes:

- Extend `static/js/agent_panel.js` message direction/action handling for:
  - `architect_peer_message`
  - `architect_peer_reply`
- Show peer names where available, not only sender kind.
- Show `ack_required` / `requires reply` badge on incoming peer messages.
- Render context chips/links for task IDs, engineer IDs, and decision snapshots when present.
- Keep virtualization and scroll-anchor behavior for message-list rerenders.
- Add a lightweight Architect card/principal indicator for incoming peer messages requiring reply, but do not clutter cards with decision counts or pending human asks.
- Ensure `static/js/ws.js` invalidates message caches when `mcp_messages` changes; if a new delta op is added for peer messages, handle that op and preserve panel state.

`architect_events_recent` should include peer-message visibility. Two implementation options:

1. Merge recent `agent_peer_messages` involving the caller into the returned event list with `kind='architect_peer_message'` / `architect_peer_reply`.
2. Also emit panel events for peer messages, but do not rely on `panel_events` as the source of truth because it lacks recipient/thread metadata.

Prefer option 1 for correctness. If panel events are emitted for UI/history, treat them as coarse display/audit only.

`architect_board_summary` can include a small `peer_messages` summary:

```json
{
  "peer_messages": {
    "requires_reply_count": 1,
    "recent_count": 3,
    "oldest_unanswered_at": 1779140000.0
  }
}
```

The detailed recovery path remains `architect_peer_inbox`.

### Decision artifacts

Do not create shared decisions in V1.

Recommended V1 behavior:

- The Architect who owns the product decision records it in their own `decisions` table row.
- That Architect can include the decision as `context_decision_ids` in a peer message; the message stores a snapshot so the recipient can understand it without direct decision-log access.
- If the recipient adopts the decision for their own surface, they may create their own local decision and mention the thread ID in the rationale.

Future shared-decision work can add a separate `shared_decisions` or `decision_participants` model after the messaging primitive exists. Do not overload the current per-Architect `decisions.architect_id` ownership semantics in V1.

### Worker continuity and context

Architect B can answer many questions from shared board context, but cannot automatically see Architect A's private decision log, private journal, or hired Engineer journal.

V1 should therefore make context explicit in the message:

- `context_task_ids` gives B task-level state already visible in the group.
- `context_engineer_ids` includes a lightweight snapshot only; it does not let B message/read that Engineer unless B already owns the Engineer.
- `context_decision_ids` includes a snapshot only; it does not let B call `architect_decision_update/read` on A's decision.
- For worker-level details, A should summarize the relevant Engineer/Worker state or ask A's Engineer for a concise handoff first.

Do not auto-grant extra scope just because a message references an entity.

### Failure modes and mitigations

| Failure mode | Mitigation |
|---|---|
| Recipient offline/dismissed | Save row first, mark buffered, replay on rehire, expose in inbox. |
| Daemon restarts before delivery | Canonical row survives; inbox and replay read DB. |
| Silent drop | Tool return includes delivery state; inbox shows buffered/failed rows. |
| A asks B, B asks A back | `ack_required` is advisory/inbox state, not a blocking daemon lock. Cycles do not deadlock. Stale unanswered threads can be surfaced by age. |
| Spam/broadcast | V1 has named one-recipient messages only; no broadcast. Add length cap and bounded inbox responses. |
| Dismissed/deleted recipient mid-thread | Dismissed buffers; tombstoned rejects new sends/replies with clear error. Existing rows remain readable. |
| Context leak | Same-group only; referenced decisions/engineers are included as bounded snapshots, not scope grants. |
| Duplicate send after MCP retry | Use existing idempotency plumbing/command receipts for peer-message writes or deterministic message IDs when an idempotency key is present. |

## Decomposed implementation task list

### 1. Add durable peer-message persistence

Files:

- `torque/db_schema.py`
- `torque/db.py`
- `torque/state.py`
- `tests/test_db.py`
- `tests/test_state.py`

Work:

1. Add `agent_peer_messages` DDL and indexes.
2. Add migration/ensure logic for existing DBs.
3. Implement save/load/update-delivery helpers.
4. Add canonical row normalization for JSON context fields and booleans.
5. Add state helpers that project canonical rows into `mcp_messages` entries for a caller.
6. Seed recent Architect peer messages into relevant Architect caches on daemon load or first access.

Tests:

- New DB has table/indexes.
- Existing DB migration adds table idempotently.
- JSON context fields round-trip.
- Recipient/sender/thread queries order correctly.
- Delivery state updates are persisted.
- Restart/load can reconstruct a recent peer-message cache or inbox.

### 2. Add Architect peer MCP tools and scoping

Files:

- `torque/mcp_architect.py`
- `torque/mcp_tools_shared.py`
- `torque/mcp.py` (write-tool/idempotency classification if needed)
- `tests/test_mcp.py`
- `tests/test_communication_graph.py`
- `tests/test_architect_scoping.py`
- `tests/test_mcp_reliability.py`

Work:

1. Add tool specs for `architect_peer_list`, `architect_peer_message`, and `architect_peer_inbox`.
2. Add resolver `_resolve_architect_peer(...)` with same-group/non-self/non-tombstoned rules.
3. Add context validation/snapshot helpers.
4. Implement `architect_peer_list` read path.
5. Implement `architect_peer_inbox` read path with `requires_reply` computation.
6. Implement `architect_peer_message` write path with persistence, cache projection, injection attempt, and JSON return.
7. Extend `architect_reply` to support `peer_kind="architect"` while preserving current Architect ↔ Engineer behavior.
8. Add `ack_required` to Architect replies.
9. Ensure new write tools are classified for MCP idempotency/replay.

Tests:

- Same-group Architect can list and message peer.
- Cross-group, self, worker, Engineer, and tombstoned targets are rejected.
- Dismissed caller cannot send; dismissed recipient buffers.
- Reply preserves `thread_id` and computes `requires_reply` correctly.
- Existing Architect ↔ Engineer messaging tests still pass.
- Write-tool classification includes new tools.
- Idempotent retry does not duplicate peer messages.

### 3. Harden delivery/replay behavior

Files:

- `torque/server.py`
- `torque/mcp_tools_shared.py`
- `tests/test_server_modules.py`
- `tests/test_architect_scoping.py`

Work:

1. Generalize buffered message replay to read canonical peer-message rows for Architect recipients.
2. Update delivery state in DB after injection/replay success/failure.
3. Ensure rehire/relaunch paths replay Architect peer messages the same way Engineer/Architect messages are replayed today.
4. Update injected prompt copy for optional vs required replies.
5. Emit optional panel/audit events without making `panel_events` canonical.

Tests:

- Message to stopped/dismissed Architect is buffered and replays on rehire.
- Delivery failure persists `delivery_state`/`delivery_reason`.
- Daemon restart before delivery does not lose the inbox row.
- Injection prompt points to `architect_reply` with the correct `message_id`.

### 4. Surface messages in Architect UI

Files:

- `static/js/agent_panel.js`
- `static/js/render.js`
- `static/js/ws.js`
- `static/style.css`
- `tests/frontend_agent_panel.test.js`
- `tests/frontend_state_regression.test.js`
- `tests/frontend_architects_panel.test.js`

Work:

1. Render Architect peer message actions and direction correctly.
2. Display peer names and Architect sender/recipient labels.
3. Add `ack_required`/`requires reply` badge styling.
4. Render context references as compact chips/links.
5. Add a lightweight card/principal indicator for incoming peer messages requiring reply.
6. Preserve message-list scroll/focus/caret behavior across `agent_upsert` and any new peer-message deltas.
7. If `architect_board_summary` adds `peer_messages`, display it only where useful; do not overpopulate principal cards.

Tests:

- Architect `Messages` tab renders Architect peer messages and existing Engineer messages.
- Incoming/outgoing direction is correct for sender and recipient.
- Ack badge appears only when appropriate.
- Context chips render escaped text and stable links.
- Scroll anchor is preserved when a new message inserts above the viewport.
- Architect principal/card indicator appears for peer reply obligations and does not reintroduce decision/pending-ask counts intentionally omitted by existing tests.

### 5. Add peer messages to Architect recovery/read surfaces

Files:

- `torque/mcp_tools_shared.py`
- `torque/architect.py`
- `tests/test_architect_prompt.py`
- `tests/test_architect_scoping.py`

Work:

1. Add `peer_messages` counts to `architect_board_summary`.
2. Merge caller-involved peer messages into `architect_events_recent` or document that `architect_peer_inbox` is the canonical recovery surface.
3. Update the Architect boot checklist in `torque/architect.py` to include `architect_peer_inbox(requires_reply=true)` after decisions/journal or after board/events.
4. Update operating guidelines: use peer messages for cross-Architect coordination, use `architect_ask` for user/product approval, and file local decisions for durable outcomes.

Tests:

- Board summary reports peer-message counts for the caller only.
- Events/inbox do not leak messages between unrelated Architects.
- Prompt tests include the new boot checklist/tool references.

### 6. Update product docs and MCP reference

Files:

- `docs/team/architects.md`
- `docs/team/team-model.md`
- `docs/team/mcp-scoping.md`
- `docs/reference/mcp-tools.md`
- optionally `docs/architecture.md`

Work:

1. Replace statements that Architects cannot message each other with the new V1 behavior.
2. Document same-group-only permission rules.
3. Document that decisions/journals remain per-Architect.
4. Document deferred/non-goal behavior for broadcast, handoff, and shared decisions.
5. Add the new tools to the MCP reference.

Tests:

- Existing docs tests, if any, plus normal full suite.

### 7. Follow-up implementation tasks to file separately, not in V1

These should be separate product/architecture tasks after V1 usage is evaluated:

1. **Formal task handoff:** `architect_task_handoff(task_id, recipient_architect_id, message)` with recipient acceptance; transfers `created_by_architect_id` only after acceptance.
2. **Engineer ownership transfer:** user-approved transfer that changes `hired_by_architect_id` and updates affected prompts/docs.
3. **Shared decisions:** add participant/co-author model (`shared_decisions` or `decision_participants`) if local-decision + peer notification is not enough.
4. **Broadcast channel:** group-scoped Architect channel with subscription/unread/spam controls.
5. **Generic migration of Architect ↔ Engineer messages:** migrate/direct all cross-kind messages through `agent_peer_messages` so Architect/Engineer threads also survive daemon restart with full metadata.

## Approval path

This research artifact stays within the requested product direction and is suitable for Engineer approval of the plan artifact.

Do **not** auto-derive implementation from this worker. The recommended implementation changes product behavior, adds Architect MCP API, and introduces a SQLite table. The Architect/human reviewer should explicitly accept the V1 scope above before filing the implementation tasks. Once accepted, the decomposed tasks are bounded enough to execute independently with normal Engineer review.
