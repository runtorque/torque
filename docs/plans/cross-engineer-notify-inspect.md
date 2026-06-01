# Cross-engineer notify-and-inspect design plan

Task: `TORQUE:801`  
Status: Design-only; no implementation performed  
Decision context: `decision-3009400b2022` reverses strict hub-and-spoke `decision-05799b1c51a6` for sanctioned engineer↔engineer coordination.  
Recommendation: Ship a narrow **same-supervising-architect, same-group Engineer notify-and-inspect** surface. Do **not** forward every message/event to the architect.

## 0. Scope/auth blast radius first

### Recommended V1 scope widening

Widen engineer visibility by the smallest amount that supports the requested behavior:

1. **Peer discovery:** an Engineer may discover **only active Engineer siblings in the same group with the same non-empty `hired_by_architect_id`**.
   - This means “same supervising Architect” in V1, not “all same-group Engineers.”
   - Exclude self, tombstoned Engineers, dismissed Engineers by default, non-Engineers, workers, terminals, Architects, other groups, user-owned Engineers with no hiring Architect, and Engineers hired by another Architect.
   - Return identity metadata only: `id`, `slug`, `name`, `group`, `status`, `hired_by_architect_id`, optional `dismissed_at` if explicitly included later. Do **not** expose peer workers, peer current tasks, peer queue, worktree path, MCP history, or journals in the discovery list.

2. **Peer notification:** a sibling Engineer may send a **single durable notification message** to a discovered peer with optional task/stream context that the sender is already allowed to see.
   - The notification can be delivered to the recipient Engineer’s terminal and stored in a durable thread.
   - This is not event forwarding. It is one explicit user/agent action.

3. **Notification-granted read-only inspection:** the recipient Engineer may inspect only the task/stream context attached to a notification thread in which they are a participant.
   - The grant is **thread/context scoped**, not global.
   - It does not change `engineer_agents_list`, `engineer_agent_show`, `engineer_board_list`, `engineer_task_show`, `engineer_streams_list`, or `engineer_stream_show` default scopes.
   - It does not grant write access to peer tasks, peer workers, peer terminals, worktrees, journals, decisions, MCP logs, or dispatch/merge controls.

4. **Architect visibility:** the supervising Architect for both Engineers may list and inspect those Engineer↔Engineer threads on demand, and gets a bounded digest notification that a thread opened or became active.
   - Because V1 restricts the participants to the same supervising Architect, only one Architect visibility boundary is widened and the existing “Architect only owns hired Engineers” model remains intact.
   - Other same-group Architects do not see these threads in V1.

5. **No user-owned/no-Architect V1:** user-owned Engineers with empty `hired_by_architect_id` should not use this surface yet.
   - Reason: the requested invariant is “architect visibility.” Without an Architect observer, an Engineer↔Engineer thread would create a blind side-channel relative to the requested model.
   - If product later wants user-owned Engineer peer messaging, design a separate “user-visible observer” rule.

### What stays denied

The following denials should remain true and be covered by tests:

- Engineer A still cannot use generic agent tools against Engineer B or B’s workers/terminals.
- Engineer A still cannot send `engineer_agent_message` to Engineer B or B’s workers.
- Engineer A still cannot dispatch, relaunch, close, merge, rebase, diff, checkpoint, upload artifacts to, verify, move, edit, or resolve B-owned resources unless existing task ownership rules already allow it.
- Engineer A still cannot read B’s journal or MCP call history.
- Engineer A still cannot read all of B’s board tasks/streams by default.
- A notification to inspect one task/stream does not grant access to unrelated tasks on the same board.
- Cross-group Engineer notifications are denied.
- Cross-Architect-hire Engineer notifications are denied in V1.
- Architect A cannot inspect threads between Engineers hired by Architect B.
- The Architect notification path must not inject every Engineer↔Engineer message into the Architect terminal.

### Current-state diagnosis

Relevant code anchors and current behavior:

- `torque/state.py::agent_is_visible_to_engineer(...)`
  - Same group required.
  - Engineer sees self.
  - Engineer sees same-group `architect`/`user`/`human` principals for upward coordination.
  - Engineer **does not** see other Engineers (`kind == "engineer"` returns false).
  - Engineer sees workers/terminals only when `owner_engineer_id` or `created_by_engineer_id` matches, or terminal parent is self/owned.

- `torque/state.py::engineer_can_access_task(...)`
  - Same group required.
  - Access granted when `assigned_engineer_id == caller`.
  - Some tool paths allow `created_by_engineer_id == caller`.
  - Some tool paths can explicitly allow unassigned tasks.

- `torque/mcp_tools_shared.py::_filter_tasks_for_caller(...)`
  - Architect scoped views currently include same-group tasks.
  - Engineer scoped views include same-group tasks assigned to the caller **or unassigned**; some tools then narrow further.
  - `engineer_board_summary` additionally narrows visible actionable tasks to `assigned_engineer_id == caller`.

- `torque/mcp_tools_shared.py::_visible_agent_ids_for_caller(...)` and `_resolve_visible_agent(...)`
  - Engineer read/write agent tools are backed by `agent_is_visible_to_engineer`.
  - This is why `engineer_agents_list`, `engineer_agent_show`, `engineer_agent_message`, `engineer_merge`, etc. cannot target peer Engineers or peer workers.

- `torque/mcp_tools_shared.py::_resolve_group_engineer(...)`
  - Some existing tools, notably `engineer_task_reassign`, already resolve same-group Engineers as assignment targets.
  - This is target resolution only, not broad visibility/control.

- Architect↔Engineer messaging today:
  - Tool specs: `torque/mcp_architect.py` and `torque/mcp_engineer.py`.
  - Shared implementation: `torque/mcp_tools_shared.py`.
  - `architect_engineer_message` requires `_resolve_architect_hired_engineer(...)`; Architects can message only Engineers they hired.
  - `engineer_message_architect` requires `_resolve_architect_for_engineer(...)`; Engineers can message only their hiring Architect.
  - `engineer_reply` and `architect_reply` continue those Architect↔Engineer threads.
  - Delivery uses `agent_peer_messages` persistence plus bounded `AgentCell.mcp_messages` caches and `inject_mcp_message`.

- Architect peer messaging today:
  - `architect_peer_list`, `architect_peer_message`, `architect_peer_inbox`, and `architect_reply` already model same-group peer messages, durable rows, context snapshots, delivery state, and reply threads.
  - This is the closest implementation pattern to mirror, but it is Architect-specific and hard-codes some `architect` sender/recipient assumptions.

- Read-only Chat panel today:
  - `agent_peer_messages` is the durable table for non-user peer messages and direct-message rows.
  - `torque/db.py::_AGENT_PEER_CHAT_WHERE` currently includes Architect↔Architect and Architect↔Engineer rows, and intentionally excludes Engineer↔Engineer rows.
  - `torque/state.py::_is_agent_peer_thread_row(...)` likewise requires an Architect participant.
  - `static/js/chat.js` renders the aggregate read-only Chat panel from `state.agent_peer_threads`; it can mostly render Engineer participants already, but the backend currently excludes Engineer↔Engineer rows.

- Digest routing today:
  - `torque/digest_routing.py` routes candidate recipients by event source and filters by per-agent digest settings.
  - Architect digests accept only `ARCHITECT_COARSE_EVENTS`.
  - Architect defaults are quiet: `architect_push_interval=300`, `architect_max_interval=600`, `architect_heartbeat_interval=0`, `architect_suppress_empty_digests=True`.
  - `torque/engineer.py::EngineerEventBuffer` buffers events per recipient and respects pause/enabled-event settings.

## 1. Product behavior decisions proposed for sign-off

### Decision A — V1 peer set

**Pick:** Same group + same non-empty `hired_by_architect_id`.

Why:

- Preserves the Architect ownership invariant: the Architect who hired both Engineers can always inspect.
- Avoids cross-Architect privacy/scope expansion in V1.
- Avoids user-owned blind channels.
- Provides the main value: sibling Engineers under one Architect can coordinate without routing every turn through the Architect.

Rejected for V1:

- **All same-group Engineers:** too broad; would require multi-Architect visibility and changes to Architect scoping for other Architects’ hired Engineers.
- **Only when notified, with no discovery:** too hard to use; Engineers need a safe roster of eligible siblings.
- **Owned-only:** Engineers do not own other Engineers; this would make the feature unusable.

### Decision B — Channel mechanism

**Pick:** Add explicit Engineer peer MCP tools backed by the existing `agent_peer_messages` table.

New Engineer tools:

1. `engineer_peer_list(include_dismissed=false)`
   - Lists eligible same-supervising-Architect sibling Engineers.

2. `engineer_peer_notify(engineer_id, message, context_task_ids=[], context_stream_refs=[], context_summary="", ack_required=false)`
   - Opens or continues a durable Engineer↔Engineer notification thread.
   - Requires at least one meaningful pointer: `context_task_ids`, `context_stream_refs`, or a non-empty `context_summary`. Prefer requiring task/stream refs for V1 if product wants stricter “look at X” semantics.
   - Validates all context is sender-visible under existing Engineer task/stream scope.
   - Saves a row with `sender_kind="engineer"`, `recipient_kind="engineer"`.
   - Delivers to the peer Engineer terminal if possible; otherwise buffers for replay.

3. `engineer_peer_reply(message_id, message, ack_required=false)`
   - Replies in an existing Engineer↔Engineer thread.
   - Requires caller to be a thread participant.

4. `engineer_peer_inbox(peer_engineer_id="", thread_id="", requires_reply=false, since=0, limit=20)`
   - Lets an Engineer recover their own Engineer↔Engineer threads after `/clear` or restart.
   - Returns only threads involving the caller.

5. `engineer_peer_inspect(message_id="", thread_id="", include_live=true)`
   - Returns the durable thread plus notification-granted read-only task/stream context.
   - Requires caller to be a thread participant.

Do **not** overload `engineer_agent_message`. That tool is for owned workers/terminals and should remain scoped to `agent_is_visible_to_engineer`.

### Decision C — Notify mechanism to Architect

**Pick:** Emit only coarse panel/digest events, never forward message bodies one-by-one to the Architect terminal.

Add coarse event kinds:

- `engineer_peer_thread_opened` — emitted once when a new Engineer↔Engineer thread is created.
- `engineer_peer_thread_active` — emitted only when a previously notified thread becomes active after a quiet floor.

Notification rules:

- The event source should be the sender Engineer (`cell_id=sender_engineer_id`) and the primary task id should be included when the notification points at a task.
- Add these event kinds to `ARCHITECT_COARSE_EVENTS` and Architect default enabled events.
- Do **not** add them to `ENGINEER_MANDATORY_EVENTS` or Engineer notification presets. Recipient Engineers get the direct peer message; sender Engineers should not receive self-digests by default.
- Route through `EngineerEventBuffer` only. Do not call `inject_mcp_message` for the Architect notification.
- Respect Architect paused delivery, enabled-event filtering, `push_interval`, `max_interval`, `suppress_empty`, and the existing digest buffer.
- Throttle `engineer_peer_thread_active` per `(architect_id, thread_id)` to at most once per Architect quiet floor. Use `max(architect_settings.architect_push_interval, architect_settings.architect_max_interval, 300)` as the minimum active-notice interval unless product picks a different constant.
- The digest line should be bounded, e.g. `Courier ↔ Panelsmith: peer thread opened — TORQUE:801 / stream torque/courier/...`; include `thread_id` only as a compact reference, not the full message body.

This satisfies “notify-and-inspect”: the Architect gets a low-noise signal, then pulls details with an inspect tool.

### Decision D — Architect inspect surface

**Pick:** Add Architect read tools for Engineer↔Engineer threads; no inline Architect posting into those threads in V1.

New Architect tools:

1. `architect_engineer_peer_threads(engineer_id="", thread_id="", active_since=0, limit=20)`
   - Lists Engineer↔Engineer threads where both participants are hired by the caller Architect.
   - Optional `engineer_id` filters to threads involving one hired Engineer.
   - Returns thread metadata, participants, last activity, context summary, task ids/stream refs, ack-required counts, and delivery state summary.

2. `architect_engineer_peer_inspect(thread_id="", message_id="", include_live=true, limit=100)`
   - Returns the full durable thread tail and the read-only context payload.
   - Requires all Engineer participants to be hired by the caller Architect.
   - If a thread somehow crosses Architect boundaries due future data migration or corruption, V1 should deny and surface `thread not found in scope`.

Steering/intervention in V1:

- Use existing `architect_engineer_message` / `architect_reply` to intervene with one or both Engineers after inspection.
- Do not add a three-party `architect_post_to_engineer_peer_thread` in V1; that would be a new multi-party channel and wider product decision.
- If sign-off requires inline intervention, add it as a separate explicitly reviewed V1.1 decision.

## 2. Data and persistence design

### Use existing table, no schema migration for V1

The existing `agent_peer_messages` schema is already generic enough:

- `sender_id`, `sender_kind`, `recipient_id`, `recipient_kind`
- `thread_id`, `reply_to_id`
- `group_name`
- `message`, `message_type`, `created_at`
- `ack_required`, `blocking`
- `context_task_ids`, `context_engineer_ids`, `context_decision_ids`, `context_summary`, `context_snapshot`
- delivery/read/archive/idempotency fields

V1 can store Engineer↔Engineer rows with:

```json
{
  "sender_kind": "engineer",
  "recipient_kind": "engineer",
  "message_type": "message",
  "blocking": false,
  "context_task_ids": ["TORQUE:801"],
  "context_engineer_ids": ["sender", "recipient"],
  "context_summary": "Please inspect the design stream",
  "context_snapshot": {
    "tasks": ["bounded task snapshots"],
    "streams": ["bounded stream snapshots"],
    "inspect_grant": {
      "scope": "thread_context",
      "source_engineer_id": "eng-a",
      "recipient_engineer_id": "eng-b",
      "supervising_architect_id": "arch-1"
    }
  }
}
```

`context_snapshot.streams` can carry `stream_id`, `repo_root`, `branch`, `source_task_ids`, `state`, `agent_id` redacted/summary fields, and `captured_at`. A dedicated `context_stream_refs` column is not required for V1 because inspection starts from `message_id`/`thread_id`, not arbitrary stream search.

### Loader/read-model changes

Add or update DB/state helpers instead of new tables:

- `torque/db.py`
  - Add `load_engineer_peer_messages_for_agent(...)` for rows where both participant kinds are `engineer` and one participant is `agent_id`.
  - Add `load_engineer_peer_messages_for_thread(...)`.
  - Add `load_engineer_peer_messages_for_architect(architect_id, ...)` or a group-level loader plus in-Python auth filter.
  - Update `_AGENT_PEER_CHAT_WHERE` or add a new chat WHERE constant to include Engineer↔Engineer rows in the read-only aggregate while still excluding user/direct/worker/blocking rows.

- `torque/state.py`
  - Update `_agent_peer_message_action(...)` / `_agent_peer_message_row_to_entry(...)` equivalents to label Engineer↔Engineer rows as `engineer_peer_notify` or `engineer_peer_reply`.
  - Update `_is_agent_peer_thread_row(...)` to include Engineer↔Engineer rows.
  - Update `_is_peer_message_cache_entry(...)` to include new Engineer peer action labels.
  - Seed peer-message caches for Engineers as well as Architects when relevant, or at least ensure `engineer_peer_inbox` reads from SQLite so MCP recovery does not depend on ephemeral `mcp_messages`.
  - Keep `agent_peer_threads` as a read-only aggregate, with participant `kind="engineer"` supported.

### Thread identity

Use the current pair-key aggregate (`agent-pair:<sorted-id-a>:<sorted-id-b>`) for the read-only Chat panel, but preserve original row `thread_id` for reply continuity.

For `engineer_peer_notify`:

- If the caller supplies no thread id, generate a new message id and use it as the row `thread_id`.
- `agent_peer_threads` can continue merging multiple row threads between the same participant pair into one pair aggregate, as it does today for Architect↔Engineer rows.
- MCP inbox/inspect should preserve row thread ids and also return `pair_thread_id` for the aggregate view.

## 3. Live delivery and replay

### Recipient Engineer delivery

Reuse the existing `inject_mcp_message` command path, but update prompt formatting:

- `torque/server.py::_format_injected_mcp_message_prompt(...)` currently picks reply tools by recipient kind and will choose `engineer_reply` for an Engineer recipient.
- For Engineer↔Engineer rows, it must instead hint `mcp__torque__engineer_peer_reply(message_id="...", message="...")`.
- Include a short inspect hint, e.g. `Inspect referenced context with mcp__torque__engineer_peer_inspect(message_id="...")`.
- Preserve buffering/replay through `_replay_buffered_cross_kind_messages(...)`, but update replay action labels so Engineer↔Engineer buffered rows replay correctly.

### Delivery state

Use the existing delivery states:

- `delivered` when prompt injection succeeds.
- `buffered` with `reason="no_session"` or `recipient_dismissed` when the peer cannot receive now.
- `failed` only for actual injection errors.

No board task should be created for a peer notification. This avoids board noise and preserves the “notify, then inspect” model.

## 4. Implementation steps

### Step 1 — Add tests for the desired auth matrix first

Files:

- `tests/test_mcp_scoping.py`
- `tests/test_architect_scoping.py`
- `tests/test_communication_graph.py`
- `tests/test_digest_routing.py`

Add failing tests that assert:

- `engineer_peer_list` includes same-group/same-hiring-Architect siblings only.
- It excludes self, other group, other Architect’s hired Engineers, user-owned Engineers, workers, Architects, tombstoned Engineers, dismissed Engineers by default.
- `engineer_peer_notify` rejects out-of-scope peers and context tasks not visible to sender.
- `engineer_peer_notify` does not make peer workers visible through `engineer_agents_list` or `engineer_agent_show`.
- `engineer_peer_inspect` is available only to sender/recipient participants.
- `architect_engineer_peer_threads` / `architect_engineer_peer_inspect` are available only to the supervising Architect.
- Other Architects get “thread not found in scope.”
- Generic write tools remain denied for peer resources after a notification grant.

### Step 2 — Add generic Engineer peer persistence helpers

Files:

- `torque/db.py`
- `torque/state.py`
- `tests/test_db.py`
- `tests/test_state.py`

Changes:

- Add Engineer↔Engineer DB loader helpers over `agent_peer_messages`.
- Update chat/thread predicates to include Engineer↔Engineer non-user rows and continue excluding worker/user/direct/blocking rows.
- Add action-label support for `engineer_peer_notify` and `engineer_peer_reply`.
- Ensure `agent_peer_threads` snapshots include Engineer participants and sorted thread updates.
- Ensure direct user messages still do not pollute peer/chat loaders.

No migration should be needed. If implementation discovers `context_stream_refs` cannot fit cleanly in `context_snapshot`, pause and request follow-up sign-off before adding schema.

### Step 3 — Add Engineer peer MCP tools

Files:

- `torque/mcp_engineer_tools/tool_specs.py`
- `torque/mcp_engineer.py` if tool gating/search metadata needs updates
- `torque/mcp_tools_shared.py`
- `tests/test_mcp_scoping.py`
- `tests/test_communication_graph.py`

Changes:

- Add specs for `engineer_peer_list`, `engineer_peer_notify`, `engineer_peer_reply`, `engineer_peer_inbox`, `engineer_peer_inspect`.
- Implement `_resolve_engineer_peer(...)` with same group + same non-empty `hired_by_architect_id` + not self + not tombstoned + not dismissed by default.
- Implement `_normalize_engineer_peer_context(...)`:
  - Resolve task ids through the caller’s existing scoped state.
  - Resolve stream refs through caller-visible `engineer_streams_list/show` semantics.
  - Store bounded snapshots.
  - Reject context outside caller scope.
- Implement save/inject helpers that persist rows before delivery and update delivery state after injection.
- Make idempotency deterministic using the existing peer-message idempotency pattern.

### Step 4 — Add Architect read/inspect MCP tools

Files:

- `torque/mcp_architect.py`
- `torque/mcp_tools_shared.py`
- `tests/test_architect_scoping.py`

Changes:

- Add `architect_engineer_peer_threads` and `architect_engineer_peer_inspect` specs.
- Implement `_architect_can_inspect_engineer_peer_thread(...)` requiring all Engineer participants to have `hired_by_architect_id == caller_id`.
- Return thread metadata and messages with bounded tails.
- Return live context only after revalidating group and participant scope; otherwise return persisted snapshot plus `live_unavailable_reason`.
- Do not add Architect write access into the Engineer↔Engineer thread.

### Step 5 — Add coarse Architect digest notifications

Files:

- `torque/digest_routing.py`
- `torque/state.py`
- `torque/engineer.py`
- `torque/mcp_tools_shared.py`
- `tests/test_digest_routing.py`
- `tests/test_engineer.py`

Changes:

- Add `engineer_peer_thread_opened` and `engineer_peer_thread_active` to Architect coarse/default events only.
- Add display labels in `_ARCHITECT_EVENT_LABELS`.
- Emit a panel event from the peer notify/reply helper only when:
  - a new thread is opened, or
  - a thread becomes active after the per-thread quiet floor.
- Ensure no direct Architect injection occurs.
- Ensure sender Engineer does not get a self digest by default.
- Add digest formatting tests showing one bounded line, not message-body firehose.

### Step 6 — Update live replay prompt hints

Files:

- `torque/server.py`
- `tests/test_communication_graph.py` or a server-focused test module

Changes:

- Update `_format_injected_mcp_message_prompt(...)` so Engineer↔Engineer notifications hint `engineer_peer_reply`, not `engineer_reply`.
- Add an inspect hint for Engineer peer notifications.
- Ensure Architect↔Engineer and Architect↔Architect prompt hints remain unchanged.
- Update `_peer_message_row_replay_entry(...)` / replay classification for Engineer peer action labels.

### Step 7 — Frontend read-only visibility updates

Files:

- `static/js/chat.js`
- `static/js/ws.js`
- `static/js/agent_panel.js`
- `static/js/grid/agent-card.js`
- `static/style.css` only if new badges need styling
- `tests/frontend_state_regression.test.js`

Changes:

- Ensure the read-only Chat panel renders Engineer↔Engineer threads with clear participant chips and left/right attribution.
- Add action labels for `engineer_peer_notify` and `engineer_peer_reply`.
- Keep WS invalidation scoped to `chat` and focused message participants; avoid broad Engineer-panel rerenders.
- Do not add a compose UI in V1 unless product explicitly asks; agents use MCP tools.
- Optionally add an Engineer Agent Panel `Messages` tab later, but not required for the backend notify-and-inspect contract.

### Step 8 — Prompt/docs/invariants update

Files:

- `CLAUDE.md`
- `AGENTS.md`
- `torque/engineer.py`
- `torque/architect.py`
- `docs/reference/mcp-tools.md`
- Optional: `docs/team/...` if that directory exists in the target branch

Changes:

- Update the engineer-scoping invariant to something like:
  > Engineers see only themselves, owned workers/terminals, in-group tasks assigned/created/unassigned where existing tool rules allow, and **same-supervising-Architect Engineer peers for the explicit peer notify surface only**. Peer notification grants read-only inspection of the referenced thread context; it does not grant generic peer resource visibility or write/control access.
- Update Architect prompt boot checklist to include `architect_engineer_peer_threads` after peer inbox/events if this surface ships.
- Update Engineer prompt to explain when to use `engineer_peer_notify`: only for actionable peer coordination on a task/stream, not status chatter, and never as a bypass around Architect decisions.
- Document “notify-and-inspect, not forward-everything.”

### Step 9 — Verification and independent review

Run targeted tests first, then full suite:

- `python -m pytest tests/test_mcp_scoping.py tests/test_architect_scoping.py tests/test_communication_graph.py tests/test_digest_routing.py tests/test_engineer.py tests/test_db.py tests/test_state.py`
- `node --test tests/frontend_state_regression.test.js` or the project’s existing frontend test command
- `make test` before merge

Required independent review:

- Assign an independent review focused specifically on scope/auth and digest-noise behavior before merging.
- Review checklist:
  - no cross-group leak;
  - no cross-Architect-hire leak;
  - no peer worker/terminal control leak;
  - no generic board/task/stream widening;
  - no write access from inspect grant;
  - no Architect message-body firehose;
  - replay and idempotency do not bypass scoping;
  - frontend deltas do not introduce broad Engineer-panel flicker.

## 5. Risk notes

1. **Most dangerous risk: accidental generic scope widening.**
   - Avoid changing `agent_is_visible_to_engineer(...)` for the general case.
   - Add separate peer-specific resolvers instead of teaching generic agent visibility that Engineers can see Engineers.

2. **Architect multi-owner ambiguity.**
   - Restricting V1 to same supervising Architect avoids deciding whether one or both Architects inspect cross-Architect Engineer threads.

3. **Digest firehose regression.**
   - Do not emit panel/digest events per message by default.
   - Do not inject peer messages into Architect terminals.
   - Add explicit tests for one open notification and throttled active notification.

4. **Stale inspect grants.**
   - Persist the notification snapshot for audit, but revalidate live details at inspect time.
   - If live access no longer matches scope, show the snapshot and mark live details unavailable.

5. **Existing Architect peer code has hard-coded Architect assumptions.**
   - Do not blindly reuse `_save_architect_peer_message(...)` without generalizing sender/recipient kinds.
   - Add tests that `sender_kind`/`recipient_kind` round-trip as Engineer for Engineer peer rows.

## 6. Approval path

This is **not Engineer-approvable** because it changes product behavior, widens load-bearing Engineer scoping invariants, adds MCP/API surface, and affects security/privacy boundaries. It requires human/product/Architect sign-off before implementation.

Specific sign-off requests:

1. Approve V1 peer eligibility as **same group + same non-empty supervising Architect only**.
2. Approve **no inline Architect posting** into Engineer↔Engineer threads in V1; Architects inspect and intervene via existing Architect↔Engineer messages.
3. Approve using the existing `agent_peer_messages` table with `context_snapshot.streams` rather than adding a schema migration.
4. Approve Architect notification semantics: coarse `opened` plus throttled `active`, delivered only through the existing digest buffer, never per-message forwarding.
