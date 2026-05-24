# MCP tools reference

Every MCP tool Torque exposes, organized by role. For the rules that govern who can see and call each surface, read [MCP scoping](../team/mcp-scoping.md) first.

Tool names appear here without the `mcp__torque__` prefix that providers wrap them with at runtime. A worker's MCP client sees `mcp__torque__torque_done`; this page calls it `torque_done`.

## Worker tools (`torque_*`)

Available to **every authenticated agent**. These are how Workers report progress, hand off pipeline work, attach artifacts, and use the shared memory store.

### Status reporting

| Tool | What it does |
|---|---|
| `torque_progress` | Non-blocking status update with a one-line message. Updates the agent's activity detail. |
| `torque_done` | Marks the linked task complete and moves it to Done. Cascades up parent chain when the whole pipeline is done. |
| `torque_blocked` | Marks the task as needing user input. Adds `blocked` label. |
| `torque_error` | Records an unrecoverable error. Adds `error` label, surfaces the agent for attention. |
| `torque_ready` | Done + unlink agent + cascade. Used when the agent should be cleared from this task entirely. |
| `torque_verify` | Records manual deploy/restart/smoke verification details (state, tests run, notes). |

### Pipeline handoff

| Tool | What it does |
|---|---|
| `torque_derive` | Hand off the next pipeline step. Validates the target action against the current action's `transitions`. → [Pipelines](../tasks/pipelines.md) |
| `torque_ask` | Blocking human-in-the-loop question. Creates a derived task in Backlog with `human` label. |
| `torque_message_user` | Non-blocking durable direct message to the user-facing conversation panel. |
| `torque_reply` | Reply to a follow-up question from the Engineer. Resolves the matching follow-up task. |

### Direct user messaging

Use `torque_message_user(message, thread_id='', reply_to_id='', idempotency_key='')`
to answer a `## Message from the User` injection or send user-visible context
without blocking work. Use `torque_ask` only when progress must stop for a
human decision or approval.

Direct messages render in the below-terminal panel for the agent the operator
is viewing. Replies from that panel are injected back into the agent as a
`## Message from the User` block; agents should answer through their
`*_message_user` tool, not by relying on free-text terminal output. Blocking
asks still use `torque_ask`/`engineer_ask`/`architect_ask` and keep their
Backlog/pending-question semantics, but Torque mirrors asks and ask replies into
the same panel for chronology.

### Identity and context

| Tool | What it does |
|---|---|
| `torque_context` | Read-only dump of the calling agent's identity, group, current task, worktree, and terminal state. |
| `torque_name` | Suggest a more descriptive name for the agent (often the Worker knows what it's working on better than the dispatching action did). |

### Artifacts

| Tool | What it does |
|---|---|
| `torque_task_upload_artifact` | Upload an artifact (image, file, log, snippet, diff, test report, generated doc) and attach it to the linked task. |

### Memory

A small key-value store scoped to the calling group, used for sharing rendered facts across Workers.

| Tool | What it does |
|---|---|
| `torque_memory_publish` | Publish a memory entry under a key. |
| `torque_memory_list` | List memory entries in scope. |
| `torque_memory_read` | Read a memory entry. |
| `torque_memory_pin` | Pin a memory entry to the top of the list. |
| `torque_memory_unpin` | Unpin a memory entry. |
| `torque_memory_link` | Link a memory entry to a task. |

## Engineer tools (`engineer_*`)

Visible only to agents with `kind: engineer`. All operations are scoped to the calling Engineer's group; cross-group references return `not found in scope`.

### Board reads

| Tool | What it does |
|---|---|
| `engineer_board_summary` | Compact overview of lanes, pending asks, blocked tasks, agent state, hints, tracked `board_sync` state, and the caller's recent `dispatch_shapes`. The first call in any orchestration loop. |
| `engineer_board_list` | Full lane-grouped task list with optional filters; includes compact `board_sync` state when present. |
| `engineer_session_map` | Deterministic structured snapshot of streams, asks, queued follow-ups, NEXT/PRODUCT/WORKFLOW context per stream, hints, and the caller's recent `dispatch_shapes`. The orientation surface for recovery. |
| `engineer_task_show` | Full details for one task, including pipeline chain, compact `board_sync` state, and artifact metadata. |
| `engineer_task_chain` | Walk the derivation chain for a task. (Used internally; usually you want `engineer_task_show`.) |
| `engineer_agents_list` | Quick view of all agents in the group. |
| `engineer_agent_show` | Deep inspection of one agent: session, worktree, tasks, terminals, task-boundary metadata for shared-agent branches. |
| `engineer_actions_list` | Discover available actions and their variables. |
| `engineer_action_show` | Inspect one action's YAML, variables, transitions, gates. |
| `engineer_streams_list` | List active streams. |
| `engineer_stream_show` | Show one stream's full state. |

`dispatch_shapes` is an advisory, in-memory read model scoped to the
calling Engineer. It summarizes the last 20 recent dispatch-shape events:

| Field | Meaning |
|---|---|
| `counts.serial` | Direct serial dispatches, usually one `engineer_task_dispatch` to a new Worker. Single-entry batch dispatches also count as serial. |
| `counts.batch` | Multi-entry `engineer_batch_dispatch` calls with no shared `agent_group` cluster. |
| `counts.warm_cluster` | Existing-agent reuse or batch entries grouped onto one warm agent. |
| `hintable_serial` | Serial new-agent dispatches with no per-task launch overrides; used only for low-noise batch-affordance hints. |
| `derives_total` / `derives_by_shape` | Worker `torque_derive` handoffs, counted separately from direct Engineer dispatches. |

The metric is volatile and not a durable audit log; use
`engineer_mcp_calls` when you need raw call history.

Board sync state in these read tools is intentionally compact: provider,
enabled/tracked state, sync_state, issue URL/number, last push/pull timestamps,
and last_error. Provider-private payloads such as full GitHub project metadata
are omitted from list/summary surfaces; use the task modal or CLI JSON for
operator sync actions.

### Task editing

| Tool | What it does |
|---|---|
| `engineer_task_create` | Create a board task. |
| `engineer_task_edit` | Change task title, description, labels, action, or action vars. |
| `engineer_task_move` | Move a task between lanes. |
| `engineer_task_reassign` | Reassign a task to a different Engineer. |
| `engineer_task_verify` | Record deploy/restart attempted, smoke passed/failed, and verification notes. |
| `engineer_task_upload_artifact` | Upload and attach an image or other artifact directly to a task. |
| `engineer_task_resolve` | Resolve an `ask` task and send the answer back to the waiting agent. |

### Dispatch

| Tool | What it does |
|---|---|
| `engineer_task_dispatch` | Dispatch one task to a new or existing agent. |
| `engineer_batch_dispatch` | Dispatch a planned batch with a concurrency cap. The Engineer's main wave-orchestration tool. |

### Review, merge, worktree

| Tool | What it does |
|---|---|
| `engineer_diff` | Structured diff: `summary_only`, `stat_only`, `paths`, or full text. Start with `summary_only=true`. |
| `engineer_merge` | Default PR-based merge: push the worktree branch, create/reuse a GitHub PR, request a squash merge, sync the local base, then run cleanup after the merge is confirmed. Returns conflict context or `pending: true` when checks/reviews block the PR. Pass `pr_title` (short imperative PR title/squash subject) and `pr_body` (Markdown PR description/squash body covering what changed, why, task IDs, and tests). Use `force_direct=true` only for the explicit local fallback. |
| `engineer_rebase` | Rebase a conflicted worktree branch onto its base. Aborts on conflict and returns details. |
| `engineer_create_pr` | Push and open a GitHub PR via `gh`; create-only, with no merge attempt or cleanup. |
| `engineer_worktree_checkpoint` | Snapshot a worktree before a risky operation. |
| `engineer_worktree_remove` | Remove a worktree after merge or cleanup. |

`engineer_merge` stores PR metadata on the latest open worktree boundary.
Cleanup flags are recorded there while the PR is pending, but cleanup is
executed only after an actual merge. Torque V1 does not poll GitHub in the
background; rerun `engineer_merge` after branch-protection checks pass to
refresh status and finalize the boundary.

For GitHub board-sync groups, the PR path also appends missing linked-issue
closing refs to the created or reused PR body when
`board_sync_github.github_close_issues_via_pr` is enabled. Same-repo issues use
`Closes #123`; cross-repo issues use `Closes owner/repo#123`. Direct-local
merge mode cannot close issues this way because no PR body is written.

### Agent control

| Tool | What it does |
|---|---|
| `engineer_agent_message` | Send a message into another agent's terminal. Creates a follow-up task to record the side conversation. |
| `engineer_agent_close` | Close an agent's session while leaving its worktree on disk. |
| `engineer_agent_relaunch` | Relaunch a stopped agent, reusing its worktree and provider resume. |
| `engineer_launch_settings` | Inspect or update the Engineer's own launch configuration. |

### Notifications and journal

| Tool | What it does |
|---|---|
| `engineer_notifications` | Configure digest timing and optional event types. |
| `engineer_resume` | Unpause event delivery after an `engineer_ask` exchange. |
| `engineer_events` | Poll recent panel events, especially after recovery. |
| `engineer_journal` | Append a journal entry (`decision`, `observation`, `checkpoint`, `plan`). |
| `engineer_journal_read` | Read recent journal entries. |
| `engineer_mcp_calls` | Recent MCP call history filtered to the Engineer's scope. |

### Communication

| Tool | What it does |
|---|---|
| `engineer_note` | Non-blocking note or soft question for the human. |
| `engineer_ask` | Blocking question. Pauses event pushes until the human answers. |
| `engineer_message_user` | Non-blocking durable direct message to the user-facing conversation panel. |
| `engineer_message_architect` | Message the Architect that hired this Engineer. |
| `engineer_reply` | Reply to a thread in an existing message conversation. |

### Specializations

A specialization is saved Engineer prompt/routing guidance with attached
priorities and skills. Project specializations live in
`.torque/specializations/`; see
[Roles and specializations](specializations.md) for the project taxonomy and
default worker-role mapping.

| Tool | What it does |
|---|---|
| `engineer_specializations_list` | List defined specializations. |
| `engineer_specialization_show` | Inspect one specialization. |
| `engineer_specialization_save` | Create or update a specialization. |
| `engineer_specialization_delete` | Delete a specialization. |

### Tool discovery

| Tool | What it does |
|---|---|
| `engineer_tools` | Eager catalog of available Engineer tools (visible at list time). |
| `engineer_tool_search` | Search the deferred tool catalog and return schemas on demand. |

## Architect tools (`architect_*`)

Visible only to agents with `kind: architect`. Group-scoped, with further per-Architect scoping on decisions, hires, journal, and peer threads.

### Board reads

| Tool | What it does |
|---|---|
| `architect_board_summary` | Compact board overview with task excerpts, `created_by` attribution, compact `board_sync` state, and peer-message counts. |
| `architect_task_list` | Tasks with label/lane/engineer/creator/archived filters and compact `board_sync` state when present. AND semantics on labels. |
| `architect_task_show` | Full details for one task, including compact `board_sync` state when present. |
| `architect_task_chain` | Full derived-task tree for a pipeline with summary stats. |
| `architect_events_recent` | Recent coarse architect-scoped panel events (`task_done`, `agent_error`, `engineer_hired`, peer messages, etc.). |
| `architect_mcp_calls` | Recent MCP call history filtered to the Architect's scope. |
| `architect_deploy_state` | Read-only daemon git state and pending commit count. |
| `architect_get_architect_settings` | Read persisted architect settings for the group. |

### Engineer roster

| Tool | What it does |
|---|---|
| `architect_engineer_list` | Visible engineers (hired or not), with `dismissed_at` timestamps and current state. |
| `architect_engineer_journal_read` | Recent journal entries from a hired engineer (with type filter). |
| `architect_engineer_pending_question` | Current blocking human-input question for a hired engineer. |
| `architect_engineer_answer` | Answer a hired engineer's pending blocking question (the owner-routed ask): delivers the answer and resumes the engineer. Architect-side counterpart to `engineer_task_resolve`. |
| `architect_pending_hire_list` | Pending hire requests created by this Architect. |
| `architect_pending_hire_status` | Status for a single pending hire. |

### Task routing

The Architect can update only tasks it created itself or that the user created. Other Architects' tasks, Engineer-created tasks, and system-derived parent tasks are read-only.

| Tool | What it does |
|---|---|
| `architect_task_create` | Create a task for a specific Engineer. Stamps `created_by_architect_id`. |
| `architect_task_update` | Update title, description, labels, action binding (own tasks or user-created only). |
| `architect_task_reassign` | Reassign a task created by this Architect to another visible Engineer. |
| `architect_task_move` | Move any visible task between lanes. |

### Hiring

| Tool | What it does |
|---|---|
| `architect_engineer_hire` | Queue a new Engineer hire for user approval. Returns `status: "pending"`. The Architect must poll status before treating the Engineer as live. |
| `architect_engineer_dismiss` | Pause a hired Engineer. Closes session, preserves history. Reversible. |
| `architect_engineer_rehire` | Resume a previously dismissed Engineer with full history. |
| `architect_engineer_restore` | Restore from a 7-day deleted window. |

### Messaging

| Tool | What it does |
|---|---|
| `architect_engineer_message` | Direct message to a hired Engineer. |
| `architect_peer_list` | List same-group Architect peers that can receive direct messages. |
| `architect_peer_message` | Send a durable same-group direct message to one Architect, optionally with context snapshots. |
| `architect_peer_inbox` | Read durable Architect peer message threads, including reply-required filters. |
| `architect_reply` | Reply to an existing Architect ↔ Engineer or Architect ↔ Architect thread. |
| `architect_message_user` | Non-blocking durable direct message to the user-facing conversation panel. |
| `architect_ask` | Blocking question to the human. Creates a Backlog task with `human` label. |

#### User direct-message signatures

Participant kinds in the unified `agent_peer_messages` direct-message store are
`architect`, `engineer`, `worker`, and `user`. The frontend should treat
`message_type` as one of `message`, `ask`, `ask_reply`, or `system`; the
blocking ask badge comes from `blocking=true`.

- `architect_message_user(message: string, thread_id?: string = '', reply_to_id?: string = '', context_task_ids?: string[], context_engineer_ids?: string[], context_decision_ids?: string[], context_summary?: string, idempotency_key?: string = '')`
- `engineer_message_user(message: string, thread_id?: string = '', reply_to_id?: string = '', context_task_ids?: string[], context_summary?: string, idempotency_key?: string = '')`
- `torque_message_user(message: string, thread_id?: string = '', reply_to_id?: string = '', idempotency_key?: string = '')`

All three persist `sender_kind=<calling agent kind>` and `recipient_kind=user`,
emit a `direct_message_upsert` delta, return `type`, `message_id`, `thread_id`,
`reply_to_id`, delivery/read metadata, and never create a Backlog ask task.
V1 normalizes user↔agent messages to one thread per viewed agent. User replies
from the panel use `user_agent_message(agent_id|cell_id|target_agent_id,
message|text, thread_id?, reply_to_id?, idempotency_key?)`; the send is
persisted first, queued non-interruptively, and buffered for replay when the
agent is down, dismissed, or temporarily unavailable.

#### Architect peer messaging signatures

Peer messages are same-group only. They do not grant access to another Architect's journal, decision log, hired-Engineer controls, or cross-group state. Dismissed recipients buffer delivery; tombstoned/deleted recipients reject new sends.

**`architect_peer_list(include_dismissed?: boolean = false)`**

Example call: `{}`

Returns `type`, the caller `architect_id`, and an `architects` list with each peer's id, name, status, dismissal timestamp, and current task summary.

**`architect_peer_message(architect_id: string, message: string, ack_required?: boolean = false, context_task_ids?: string[], context_engineer_ids?: string[], context_decision_ids?: string[], context_summary?: string)`**

```json
{
  "architect_id": "arch-ui",
  "message": "Can you sanity-check whether this belongs in your panel scope?",
  "ack_required": true,
  "context_task_ids": ["TORQUE:443"],
  "context_engineer_ids": ["eng-panels"],
  "context_decision_ids": ["D-20"],
  "context_summary": "I am deciding where to route the peer-message UI follow-up."
}
```

Returns `message_id`, `thread_id`, `recipient_architect_id`, `ack_required`, and delivery `state`/`reason`.

Context references are optional. Task references must be visible same-group tasks; Engineer references must resolve to visible same-group Engineers; decision references must belong to the sending Architect. The message plus `context_summary` is capped at 16 KiB.

**`architect_peer_inbox(peer_architect_id?: string, thread_id?: string, requires_reply?: boolean = false, since?: number = 0, limit?: integer = 20)`**

Example call: `{"requires_reply": true, "limit": 10}`

```json
{
  "type": "architect_peer_inbox",
  "threads": [
    {
      "thread_id": "msg-abc123",
      "peer_architect_id": "arch-ui",
      "peer_name": "UI Architect",
      "last_message_at": 1779140000.0,
      "requires_reply": true,
      "messages": [
        {
          "id": "msg-abc123",
          "direction": "received",
          "action": "architect_peer_message",
          "message": "Can you sanity-check whether this belongs in your panel scope?",
          "ack_required": true,
          "context_task_ids": ["TORQUE:443"]
        }
      ]
    }
  ]
}
```

`requires_reply` is computed per thread: an incoming `ack_required=true` message requires reply until the caller sends a later message in that thread.

**`architect_reply(message_id: string, message: string, ack_required?: boolean = false)`**

```json
{
  "message_id": "msg-abc123",
  "message": "Yes — route the panel rendering follow-up to my UI Engineer.",
  "ack_required": false
}
```

Returns `{ "type": "ok", "message_id": "...", "thread_id": "..." }`.

For Architect ↔ Engineer threads, `architect_reply` preserves the existing hired-Engineer scope checks. For Architect ↔ Architect threads, it preserves the original peer thread and `ack_required` can request another answer.

### Decisions

The architect's durable product log. Decisions can be updated and archived but task/engineer links are append-only.

| Tool | What it does |
|---|---|
| `architect_decision_create` | Create a new decision (`proposed` / `accepted` / `revised` / `rejected`, optionally linking tasks/engineers). |
| `architect_decision_update` | Update title, rationale, status, supersedes, archive flag. |
| `architect_decision_link` | Append one task or engineer link to an existing decision. |
| `architect_decision_list` | This Architect's persisted decisions, with filters. |

### Journal

| Tool | What it does |
|---|---|
| `architect_journal` | Append entry (`decision`, `observation`, `checkpoint`, `plan`) to this Architect's private journal. |
| `architect_journal_read` | Read recent journal entries (timestamp filter). |

### Tool discovery

| Tool | What it does |
|---|---|
| `architect_tool_search` | Search the deferred Architect tool catalog and return schemas on demand. |

## See also

- [MCP scoping](../team/mcp-scoping.md) — how each role's surface is filtered server-side.
- [Workers](../team/workers.md) — the role with the smallest tool surface.
- [Engineers](../team/engineers.md) — the operating loop these tools support.
- [Architects](../team/architects.md) — the planning role with per-actor scoping.
- [CLI reference](cli.md) — equivalent commands for human + offline-script use.
