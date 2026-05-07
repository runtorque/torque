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
| `torque_reply` | Reply to a follow-up question from the Engineer. Resolves the matching follow-up task. |

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
| `engineer_board_summary` | Compact overview of lanes, pending asks, blocked tasks, agent state. The first call in any orchestration loop. |
| `engineer_board_list` | Full lane-grouped task list with optional filters. |
| `engineer_session_map` | Deterministic structured snapshot of streams, asks, queued follow-ups, NEXT/PRODUCT/WORKFLOW context per stream. The orientation surface for recovery. |
| `engineer_task_show` | Full details for one task, including pipeline chain and artifact metadata. |
| `engineer_task_chain` | Walk the derivation chain for a task. (Used internally; usually you want `engineer_task_show`.) |
| `engineer_agents_list` | Quick view of all agents in the group. |
| `engineer_agent_show` | Deep inspection of one agent: session, worktree, tasks, terminals, task-boundary metadata for shared-agent branches. |
| `engineer_actions_list` | Discover available actions and their variables. |
| `engineer_action_show` | Inspect one action's YAML, variables, transitions, gates. |
| `engineer_streams_list` | List active streams. |
| `engineer_stream_show` | Show one stream's full state. |

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
| `engineer_merge` | Server-side merge of a worktree branch into its base. Returns conflict context if conflicts are detected. |
| `engineer_rebase` | Rebase a conflicted worktree branch onto its base. Aborts on conflict and returns details. |
| `engineer_create_pr` | Push and open a GitHub PR via `gh`. |
| `engineer_worktree_checkpoint` | Snapshot a worktree before a risky operation. |
| `engineer_worktree_remove` | Remove a worktree after merge or cleanup. |

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
| `engineer_message_architect` | Message the Architect that hired this Engineer. |
| `engineer_reply` | Reply to a thread in an existing message conversation. |

### Specializations

A specialization is a saved Engineer launch preset with attached priorities and skills.

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

Visible only to agents with `kind: architect`. Group-scoped, with further per-Architect scoping on decisions, hires, and journal.

### Board reads

| Tool | What it does |
|---|---|
| `architect_board_summary` | Compact board overview with task excerpts and `created_by` attribution. |
| `architect_task_list` | Tasks with label/lane/engineer/creator/archived filters. AND semantics on labels. |
| `architect_task_show` | Full details for one task. |
| `architect_task_chain` | Full derived-task tree for a pipeline with summary stats. |
| `architect_events_recent` | Recent coarse architect-scoped panel events (`task_done`, `agent_error`, `engineer_hired`, etc.). |
| `architect_mcp_calls` | Recent MCP call history filtered to the Architect's scope. |
| `architect_deploy_state` | Read-only daemon git state and pending commit count. |
| `architect_get_architect_settings` | Read persisted architect settings for the group. |

### Engineer roster

| Tool | What it does |
|---|---|
| `architect_engineer_list` | Visible engineers (hired or not), with `dismissed_at` timestamps and current state. |
| `architect_engineer_journal_read` | Recent journal entries from a hired engineer (with type filter). |
| `architect_engineer_pending_question` | Current blocking human-input question for a hired engineer. |
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
| `architect_reply` | Reply to an existing Architect ↔ Engineer thread. |
| `architect_ask` | Blocking question to the human. Creates a Backlog task with `human` label. |

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
