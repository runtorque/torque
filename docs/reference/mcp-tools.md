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
| `torque_verify` | Records tests/deploy/restart/smoke verification details, including full-suite/flaky-test taxonomy, deploy attempted/not attempted, and live-smoke pending. |

### Pipeline handoff

| Tool | What it does |
|---|---|
| `torque_derive` | Hand off the next pipeline step. Validates the target action against the current action's `transitions`. → [Pipelines](../tasks/pipelines.md) |
| `torque_ask` | Blocking human-in-the-loop question. Creates a derived task in Backlog with `human` label. |
| `torque_message_user` | Non-blocking durable direct message to the user-facing conversation panel. |
| `torque_reply` | Reply to a follow-up question from the Engineer. Resolves the matching follow-up task. |

### Direct user messaging

Use `torque_message_user(message, reply_to_id='', idempotency_key='')`
to answer a `## Message from the User` injection or send user-visible context
without blocking work. Use `torque_ask` only when progress must stop for a
human decision or approval.

Agents normally omit `thread_id`: Torque derives the single user-facing
conversation lane from the bound caller identity. When replying to an injected
user message, pass the prompt's `reply_to_id` so the message is linked while
still using the caller-derived lane.

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

### Help docs

| Tool | What it does |
|---|---|
| `torque_help_list` | List maintained Torque Help topics from the docs allow-list. |
| `torque_help_show` | Show one Help topic or section by topic id, source path, or `path#anchor`. |
| `torque_help_search` | Deterministically search Help docs and return excerpts with source references. |
| `torque_help_query` | Answer a question with extractive Help snippets and source references. |

See [Help docs contract](help.md) for the source allow-list, response schema,
restricted-agent safety model, and Panelsmith UI fields. Help reads maintained
markdown only; it does not expose board state, journals, logs, secrets, or
arbitrary files.

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
| `engineer_boot_summary` | Read this Engineer's cached AI boot-recovery summary. Never performs a live provider call on read. |
| `engineer_semantic_recall` | Read-only semantic search over indexed AI text snippets visible to this Engineer. |
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
| `engineer_task_upload_artifact` | Upload and attach a markdown/text report, log, screenshot, generated document, or file reference directly to a scoped task (`task`; `task_id` is accepted as an alias). |
| `engineer_task_resolve` | Resolve an `ask` task and send the answer back to the waiting agent. |
| `engineer_hint_snooze` | Snooze or clear a deterministic hint fingerprint, for example acknowledging retained-by-policy merged workers without deleting them. |

### Dispatch

| Tool | What it does |
|---|---|
| `engineer_task_dispatch` | Dispatch one task to a new or existing agent. |
| `engineer_batch_dispatch` | Dispatch a planned batch with a concurrency cap. The Engineer's main wave-orchestration tool. |

### Review, merge, worktree

| Tool | What it does |
|---|---|
| `engineer_diff` | Structured diff: `summary_only`, `stat_only`, `paths`, or full text. Start with `summary_only=true`. |
| `engineer_merge` | Default PR-based merge: push the worktree branch, create/reuse a GitHub PR, request a squash merge, sync the local base, then run cleanup after the merge is confirmed. For real configured `ee/` deltas, first push/open the `torque-ee` PR, merge it with a merge commit, and bump the parent gitlink to merged ee-main; zero-delta ee creates no ee PR. Returns conflict context or `pending: true` when parent or ee checks/reviews block the PR. Pass `pr_title` (short imperative PR title/squash subject) and `pr_body` (Markdown PR description/squash body covering what changed, why, task IDs, and tests). Use `force_direct=true` only for the explicit local fallback; it does not bypass the ee PR-first flow. |
| `engineer_rebase` | Rebase a conflicted worktree branch onto its base. Aborts on conflict and returns details. |
| `engineer_create_pr` | Push and open a GitHub PR via `gh`; create-only, with no merge attempt or cleanup. |
| `engineer_worktree_checkpoint` | Snapshot a worktree before a risky operation. |
| `engineer_worktree_remove` | Remove a worktree after merge or cleanup. |

`engineer_merge` stores PR metadata on the latest open worktree boundary.
Cleanup flags are recorded there while the PR is pending, but cleanup is
executed only after an actual merge. Torque V1 does not poll GitHub in the
background; rerun `engineer_merge` after branch-protection checks pass to
refresh status and finalize the boundary. Nested ee PR metadata is stored
alongside the parent boundary, and reruns are idempotent after an ee PR merged
but before the parent PR did.

Group settings default to keep-warm post-merge continuity. Opting the group
into the auto-sweep cleanup mode makes the same confirmed-merge finalization
close the worker and remove the merged worktree/local branch by default.

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
| `engineer_peer_list` | List same-group Engineer peers hired by the same Architect for the explicit peer notify surface. |
| `engineer_peer_notify` | Notify a peer Engineer to inspect a referenced task/stream; requires `context_task_ids` or `context_stream_refs`. |
| `engineer_peer_reply` | Reply to an existing Engineer↔Engineer peer thread. |
| `engineer_peer_inbox` | Read durable Engineer↔Engineer threads involving this Engineer. |
| `engineer_peer_inspect` | Read-only inspect of the task/stream context attached to a peer thread. |

Engineer↔Engineer peer tools are **notify-and-inspect, not
forward-everything**. V1 peers must be in the same group and share the same
non-empty `hired_by_architect_id`; the supervising Architect can inspect the
threads on demand. Peer inspection does not widen generic
`engineer_agents_list`, `engineer_agent_show`, `engineer_agent_message`,
`engineer_merge`, board, journal, or worker-control scope.

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

### Help docs

Engineer sessions also see `engineer_help_list`, `engineer_help_show`,
`engineer_help_search`, and `engineer_help_query`. These mirror the Worker Help
tools with the Engineer prefix and return only maintained docs allow-list
content.

## Architect tools (`architect_*`)

Visible only to agents with `kind: architect`. Group-scoped, with further per-Architect scoping on decisions, hires, journal, and peer threads.

### Board reads

| Tool | What it does |
|---|---|
| `architect_board_summary` | Compact board overview with task excerpts, `created_by` attribution, compact `board_sync` state, and peer-message counts. |
| `architect_boot_summary` | Read this Architect's cached AI boot-recovery summary. Never performs a live provider call on read. |
| `architect_semantic_recall` | Read-only semantic search over indexed AI text snippets visible to this Architect. |
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
| `architect_engineer_hire` | Queue a new Engineer hire for user approval, optionally with an ordered `specializations` list. Returns `status: "pending"`. The Architect must poll status before treating the Engineer as live. |
| `architect_engineer_set_specializations` | Full-replace the ordered specialization list for an Engineer hired by this Architect. No fresh user approval is needed. |
| `architect_engineer_dismiss` | Pause a hired Engineer. Closes session, preserves history. Reversible. |
| `architect_engineer_rehire` | Resume a previously dismissed Engineer with full history. |
| `architect_engineer_restore` | Restore from a 7-day deleted window. |

`architect_engineer_hire(name, command?, provider?, directory?, specializations?)`
stores `specializations` on the pending hire until the user approves it.
`architect_engineer_set_specializations(engineer_id, specializations)` is a
hired-Engineer-only roster edit. For both tools, `specializations` is the
complete ordered list: the first slug is primary, and `[]` means
generalist/no specialization. Valid slugs are the project taxonomy:
`ui-ux`, `orchestration-core`, `runtime-pty`, `desktop-shell`,
`worktree-release`, `prompts-config`, and `quality-observability`. Unknown
slugs are rejected with the valid set listed in the error.

### Messaging

| Tool | What it does |
|---|---|
| `architect_engineer_message` | Direct message to a hired Engineer. |
| `architect_peer_list` | List same-group Architect peers that can receive direct messages. |
| `architect_peer_message` | Send a durable same-group direct message to one Architect, optionally with context snapshots. |
| `architect_peer_inbox` | Read durable Architect peer message threads, including reply-required filters. |
| `architect_engineer_peer_threads` | List Engineer↔Engineer notify-and-inspect threads where both Engineers were hired by this Architect. |
| `architect_engineer_peer_inspect` | Inspect a full Engineer↔Engineer thread and its referenced read-only task/stream context. |
| `architect_reply` | Reply to an existing Architect ↔ Engineer or Architect ↔ Architect thread. |
| `architect_message_user` | Non-blocking durable direct message to the user-facing conversation panel. |
| `architect_ask` | Blocking question to the human. Creates a Backlog task with `human` label. |

#### User direct-message signatures

Participant kinds in the unified `agent_peer_messages` direct-message store are
`architect`, `engineer`, `worker`, and `user`. The frontend should treat
`message_type` as one of `message`, `ask`, `ask_reply`, or `system`; the
blocking ask badge comes from `blocking=true`.

- `architect_message_user(message: string, reply_to_id?: string = '', context_task_ids?: string[], context_engineer_ids?: string[], context_decision_ids?: string[], context_summary?: string, idempotency_key?: string = '')`
- `engineer_message_user(message: string, reply_to_id?: string = '', context_task_ids?: string[], context_summary?: string, idempotency_key?: string = '')`
- `torque_message_user(message: string, reply_to_id?: string = '', idempotency_key?: string = '')`

All three persist `sender_kind=<calling agent kind>` and `recipient_kind=user`,
emit a `direct_message_upsert` delta, return `type`, `message_id`, `thread_id`,
`reply_to_id`, delivery/read metadata, and never create a Backlog ask task.
V1 normalizes user↔agent messages to one thread per viewed agent based on the
bound caller. The MCP implementations still reject an explicit canonical
`thread_id` for a different agent as a spoof/stale-binding guard, but callers
should not pass thread ids in the common path. User replies from the panel use
`sender_kind=user` and are scoped to the addressed agent lane. For
`architect_message_user` / `architect_product_message_user`, omitted
`reply_to_id` is inferred only when the calling Architect has exactly one
pending direct user message; multiple pending user messages, an already-answered
user thread, or an explicit `reply_to_id` outside the current Architect↔user
lane returns a clear error. Proactive Architect status messages without any
prior direct user thread may still omit `reply_to_id`.

The UI command is `user_agent_message(agent_id|cell_id|target_agent_id,
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

#### Engineer peer notify-and-inspect signatures

**`engineer_peer_notify(engineer_id: string, message: string, context_task_ids?: string[], context_stream_refs?: array, context_summary?: string, ack_required?: boolean = false, thread_id?: string = '')`**

Requires at least one task or stream reference; `context_summary` alone is
rejected. Returns `message_id`, `thread_id`, `recipient_engineer_id`,
`ack_required`, and delivery state. The notification is delivered to the peer
Engineer only; the Architect can receive coarse digest events when enabled
`engineer_peer_thread_opened` / throttled `engineer_peer_thread_active` and can
pull details with inspect tools.

**`engineer_peer_inspect(message_id?: string, thread_id?: string, include_live?: boolean = true)`**

Requires the caller to be the sender or recipient in the Engineer↔Engineer
thread. Returns the durable message tail plus the referenced task/stream
snapshot and any currently revalidated live task context.

**`architect_engineer_peer_threads(engineer_id?: string, thread_id?: string, active_since?: number = 0, limit?: integer = 20)`**
and
**`architect_engineer_peer_inspect(thread_id?: string, message_id?: string, include_live?: boolean = true, limit?: integer = 100)`**
are read-only Architect inspection surfaces. They require all Engineer
participants in the thread to have `hired_by_architect_id` equal to the caller
Architect. These tools are not gated by digest notification settings, preserving
Architect visibility even when coarse notifications are muted.

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

### Planning Areas

Areas are compact durable product/system map records, not wiki pages or a
second execution tracker. Area IDs use `<GROUP>-A:<n>` (for example
`TORQUE-A:1`) and intentionally do not parse as Board task or Initiative IDs.
Durable rows live in `planning_areas`, `planning_area_links`, and
`planning_area_notes`.

Architects can use `architect_area_list/show/create/update/archive`,
`architect_area_link/unlink_task`, `architect_area_link/unlink_decision`,
`architect_area_link/unlink_initiative`, `architect_area_link/unlink_area`, and
`architect_area_note_create/update/archive`. Architect writes are scoped to
Areas owned or created by that Architect; MCP does not silently transfer Area
ownership. Area↔Area relations are labels only: `related`, `depends_on`, or
`supports`.

Engineers have read-only `engineer_area_list/show`; workers have read-only
`torque_area_list/show`. Agent reads are compact/bounded and filter linked task
IDs to caller-visible tasks. Decision links are exposed to engineers/workers as
counts and hidden counts only — no decision titles or rationale. Linking Areas
to tasks, decisions, initiatives, or other Areas creates/removes link rows only;
it does not mutate the linked Board task, Decision, Initiative, or Area.

Typed Area notes are flat table-backed records: `caveat`, `tech_debt`,
`open_question`, `follow_up`, or `invariant`, with title/body and optional
single target. They are not comments, checklists, assignments, or threaded docs.

Browser/server command payloads use the same names without the actor prefix:
`area_list`, `area_show`, `area_create`, `area_update`, `area_archive`,
`area_link_*`, `area_unlink_*`, `area_note_create`, `area_note_update`, and
`area_note_archive`. The browser/user path is trusted for same-group writes and
validates link/note targets before writing.

### Initiatives

Initiatives are product-intent wrappers above the Board, not a second task
tracker. Durable membership lives in typed link rows:
`architect_initiative_list/show/create/update/archive`,
`architect_initiative_link/unlink_task`, and
`architect_initiative_link/unlink_decision`. Engineers have read-only
`engineer_initiative_list/show`. Linked task summaries are derived from Board
tasks at read time; initiative writes do not move tasks or mutate decisions.
Scoped MCP reads redact linked task/decision IDs that the caller cannot
otherwise see while preserving aggregate hidden counts.

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

### Help docs

Architect sessions also see `architect_help_list`, `architect_help_show`,
`architect_help_search`, and `architect_help_query`. Restricted Architect-derived
profiles such as Product Manager and Creative can see these tools when their
effective profile permits self-context reads; the tools themselves are
hard-limited to maintained documentation and do not broaden planning, execution,
journal, worktree, deploy, or secret visibility.

## AI read tools

AI read tools are available only to Engineers and Architects. There is no
Worker `torque_semantic_recall` or `torque_boot_summary` surface. They are
best-effort helpers: callers must keep deterministic raw reads such as
`engineer_session_map`, `engineer_journal_read`, `architect_journal_read`, and
`architect_decision_list` as the load-bearing recovery path.

### Semantic recall

`engineer_semantic_recall` and `architect_semantic_recall` search the local
embedding index and return only snippets that pass the caller's normal
server-side visibility checks. Engineer recall uses the same group/task,
journal, decision-participant, and same-supervising-Architect peer-inspection
scope as the existing Engineer read tools. Architect recall is scoped to the
calling Architect's decisions, journal, visible tasks, hired-Engineer journals,
and Engineer-peer-thread inspection grants.

Inputs:

| Field | Meaning |
|---|---|
| `query` | Required natural-language search query. |
| `limit` | Optional maximum visible snippets to return. Defaults to 5 and caps at 20. |

Per-call corpus narrowing is not part of the v1 MCP schema. Corpus selection is
configured in Settings → AI for the local index as a whole; caller visibility
is still enforced again at read time.

Successful results have `type: "semantic_recall"`, `status: "ok"`, and a
ranked `results` list of text snippets:

```json
{
  "rank": 1,
  "score": 0.87,
  "source_type": "task",
  "source_id": "TORQUE:123",
  "title": "Short title",
  "group": "Torque",
  "snippet": "Relevant indexed text...",
  "updated_at": "2026-06-02T15:42:10+00:00"
}
```

Non-ready states are returned as non-error payloads with `results: []` and a
human-readable `message` so agents do not retry storm or block orchestration:

| Status | Meaning |
|---|---|
| `disabled` | AI is off. |
| `not_ready` | The index is absent, not built, or temporarily unavailable. |
| `dependency_missing` | Optional embedding/index dependencies such as `sentence-transformers` or `sqlite-vec` are missing; run `make ai-deps` from a non-worker shell. |
| `rebuild_pending` | The configured embedding model/index state requires a rebuild. |
| `model_mismatch` | Query embedding dimensions or model metadata do not match the active index. |

### Cached boot summaries

`engineer_boot_summary` and `architect_boot_summary` return a cached
boot-recovery summary payload. Reads never schedule or perform a live provider
call; generation and refresh happen out of band after AI is explicitly enabled.

Payload fields include:

| Field | Meaning |
|---|---|
| `type` | `engineer_boot` or `architect_boot`. |
| `summary_key` | Stable cache key for the caller scope. |
| `status` | Summary readiness. Treat anything except `ready` as a raw-tool fallback. |
| `summary` | Cached text summary, empty when unavailable. |
| `source_counts` | Redacted source counts used to build the cache row. |
| `generated_at` | Unix timestamp for the cached row. |
| `source_hash` | Content hash for freshness comparison. |
| `message` | Human-readable fallback guidance or error text. |

Readiness statuses are:

| Status | Meaning |
|---|---|
| `ready` | Cached summary is current for the recorded source hash/provider/model. |
| `stale` | A previous summary exists but raw journal/decision/session-map tools are authoritative. |
| `empty` | No usable cached summary exists yet. |
| `refreshing` | An out-of-band refresh is in progress; use raw recovery tools. |
| `error` | Last refresh failed; use raw recovery tools. |

When AI or cached boot summaries are disabled, the MCP read returns
`status: "empty"` with a disabled/fallback message. Treat `empty` and
`refreshing` the same as `stale`: continue with raw deterministic tools and
never block boot, dispatch, review, or merge on the summary.

## See also

- [MCP scoping](../team/mcp-scoping.md) — how each role's surface is filtered server-side.
- [Workers](../team/workers.md) — the role with the smallest tool surface.
- [Engineers](../team/engineers.md) — the operating loop these tools support.
- [Architects](../team/architects.md) — the planning role with per-actor scoping.
- [CLI reference](cli.md) — equivalent commands for human + offline-script use.
