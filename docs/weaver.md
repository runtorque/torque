# Weaver

Each group can designate an engineer to watch the board, receive event digests, dispatch work to agents, keep a persistent journal, and ask the human for guidance when needed.

That engineer is deliberately semi-autonomous:

- it can plan, dispatch, review, merge, and clean up on its own
- it should use `engineer_ask` only when priorities, approvals, or design choices need a blocking human decision
- it should recover from `/clear` or restart by reading the journal and current board state instead of relying on chat history

## What the designated engineer does

Each group can have multiple engineers. One designated engineer owns that group's orchestration loop and uses the `engineer_*` MCP surface:

1. Read the board and decide what should happen next.
2. Dispatch tasks to agents in waves.
3. React to digests and replies from running agents.
4. Keep a journal of decisions, observations, checkpoints, and plans.
5. Review diffs, merge worktrees, open PRs, and clean up sessions.
6. Pause and ask the human when the next step should not be guessed.

The journal belongs to the group, not the individual agent. If you recreate that engineer later, it inherits the same journal history.

## Creating and configuring the designated engineer

Use the target group's **+ New** dropdown and choose **Engineer**.

After that engineer exists, open **Group Settings** for that group and switch to the **Weaver** tab to manage its operating-style presets, advanced digest settings, and expert overrides.

The designated engineer still uses Loom's persistent Weaver launch/settings flow because Loom needs to boot it with a dedicated system prompt. You cannot retroactively convert an arbitrary existing agent into that orchestration endpoint.

### Agent panel

The panel's **Journal** tab keeps chronology separate from synthesis:

- **Journal** keeps the persistent journal plus blocking asks and non-blocking notes/questions from the designated engineer
- **Session Map** is opened from a button at the top of Journal and shows the current deterministic orchestration snapshot on demand

The panel header also shows:

- buffered event count
- time until the next digest push
- a pause/resume toggle for event delivery

In standalone mode, the **Board** remains the default lower workspace. Open **Agent** when you need orchestration detail, journal context, or the deterministic session map.

When event delivery is paused, Loom keeps buffering matching events for that engineer instead of dropping them. Resuming delivery flushes the buffered events in order, so pausing is safe even during busy boards.

### Group Settings → Weaver

The Weaver tab in group settings contains the editable designated-engineer configuration:

- **Agent** section shows the current designated engineer for the group or the create button.
- **Launch controls** let you set or relaunch the designated engineer's provider, boot command, model, reasoning effort, and custom instructions from the same modal flow.
- **Operating style** sets the designated engineer's autonomy mode and default worker concurrency.
- **Digest details** configures push interval, max interval, heartbeat interval, and which optional event types appear in digests.
- **Expert overrides** let you override the provider, boot command, model, and reasoning effort just for the designated engineer and append custom instructions. If left empty, the designated engineer uses the group's defaults and built-in policy.

### Agent cell behavior

The designated engineer agent is visually distinct:

- pinned first in the group's agent grid and tab order
- amber left border
- `? awaiting input` state when a human reply is pending
- taskbar attention state when the designated engineer has asked a question
- a dedicated restart action that reopens the same launch dialog instead of the generic relaunch flow

## Operating model

The designated engineer is most effective when it works in short control loops instead of trying to solve everything in one huge plan.

Loom's newer orchestration model is **stream-centered**:

- the designated engineer schedules **waves**
- each branch/worktree execution lane is represented as a **stream**
- stream state now explains queue pauses, blocker loops, validation gates, and merge readiness more directly

See [Streams & Waves](streams-and-waves.md) for the detailed model and UI interpretation guide.

### A practical day-to-day loop

1. Read the current state with `engineer_board_summary`.
2. Use `engineer_task_show`, `engineer_agent_show`, or `engineer_action_show` only for the tasks that need deeper inspection.
3. Dispatch a wave of work with `engineer_task_dispatch` or `engineer_batch_dispatch`.
4. Wait for Loom digests instead of polling constantly.
5. When a digest arrives, react to the changed tasks and agents only.
6. Journal significant decisions and write periodic checkpoints.
7. Review diffs, merge, or ask the human when approval is needed.

### Dispatch philosophy

The designated engineer system prompt steers it toward a few practical habits:

- dispatch in waves, not all at once
- reuse the same agent when context matters
- separate work across agents when tasks are independent
- inspect diff stats before deep review
- treat an idle board with remaining backlog as a new planning turn, not as done
- clean up worktrees and sessions intentionally after merge

Group settings now expose two safe first-class policy controls:

- **Autonomy mode** — `Suggest only`, `Dispatch when clear`, or `Aggressive auto-continue`
- **Default worker concurrency** — the fallback worker cap Loom uses when the designated engineer dispatches a batch without explicitly passing `max_concurrent`

## Event digests and delivery

The designated engineer does not need to poll constantly. Loom pushes event digests into the designated engineer's terminal when appropriate.

### How digests work

Digests are:

- **idle-gated**: Loom only pushes them when the designated engineer is idle or waiting
- **buffered**: events accumulate between pushes
- **heartbeat-aware**: an idle heartbeat can arrive if no digest was sent
  for `heartbeat_interval`
- **scoped**: each designated engineer only sees events for its own group

The notification controls expose three separate intervals:

- `push_interval`: the normal digest cadence
- `max_interval`: the cap for regular digest timing
- `heartbeat_interval`: send an idle heartbeat if no digest was sent for
  this long

Paused delivery does not bypass these rules. Events continue accumulating in the per-group buffer while paused, then resume normal digest scheduling when delivery is unpaused.

If there are no new events, Loom can still send a heartbeat-style digest
with a board summary, active-agent summary, and compact blocked/unhealthy
task context when the heartbeat interval is reached. Set the heartbeat
interval to `0` or `Off` to disable it.

### Mandatory events

These always appear regardless of notification settings:

- `task_completed`
- `agent_reply`
- `agent_error`
- `agent_blocked`
- `ask_created`

### Optional events

These can be enabled or disabled in the designated engineer settings:

- `agent_started`
- `task_dispatched`
- `task_derived`
- `agent_progress`

### What a digest contains

A digest includes:

- the buffered events
- a compact board summary
- active-agent summary when there are no new events
- a context-usage warning if the designated engineer's token usage is getting large

## Journal and recovery

The journal is the designated engineer's persistent memory. It is what lets the designated engineer recover after `/clear`, a restart, or a long pause without needing the old conversation history.

### Journal entry types

| Type | Use it for |
|------|------------|
| **`decision`** | A choice the designated engineer made and why |
| **`observation`** | Something learned from events, agents, or the human |
| **`checkpoint`** | A compact snapshot of board state and next steps |
| **`plan`** | Intended next actions |

### Recovery sequence

After `/clear` or restart, the current product behavior is to recover in this order:

```text
engineer_journal_read -> engineer_session_map -> engineer_events
```

Use `engineer_board_summary` when the compact summary is enough, and `engineer_board_list` only when you need the full task inventory. That keeps recovery fast and avoids blowing context on the full board when the summary already tells you what changed.

### Journal discipline

The designated engineer should write journal entries for:

- dispatch decisions
- priority changes
- notable failures or blocked states
- merge and PR decisions
- human answers that change the plan

It should also write periodic **checkpoint** entries so recovery does not require replaying dozens of smaller decisions.

The Journal tab in the UI shows entries newest-first, and entries can be deleted with a right-click.

## Asking the human

`engineer_note` is the non-blocking path for notes, soft questions, status
updates, or proposed next waves that should stay visible without pausing
orchestration.

`engineer_ask` is the designated engineer's blocking human-in-the-loop mechanism. Use
it only when the next orchestration step should stop until the human
answers.

When the designated engineer posts a non-blocking note:

1. the note appears in the Journal tab as an informational banner
2. event pushes continue normally
3. Loom does not enter awaiting-input state
4. the note is recorded in the journal and survives restart until dismissed

When the designated engineer asks a question:

1. the question appears in the Journal tab as an amber banner
2. event pushes are automatically paused
3. the designated engineer agent shows an awaiting-input state
4. the question is recorded in the journal as an observation

If the board is idle with backlog remaining and the designated engineer only needs to
surface its recommended next wave or a soft preference question, it
should use `engineer_note`, not `engineer_ask`.

### Reply paths

The human can answer in two ways:

- **Via the panel**: Loom sends the reply to the designated engineer terminal and automatically unpauses events.
- **Directly in the terminal**: the designated engineer receives the reply in its own session and should call `engineer_resume` after handling it.

If the designated engineer becomes active again after being idle with a pending question, Loom clears the pending-question state and unpauses delivery. The safest explicit pattern is still:

1. receive the answer
2. incorporate it into the plan
3. call `engineer_resume`

## MCP tool surface

All `engineer_*` tools are available through the same `/mcp` endpoint as agent tools.

However, they are **only** visible and callable from the designated engineer session for that group. Loom authorizes them using the caller's `X-Loom-Cell-Id` header:

- the designated engineer sees both `loom_*` and `engineer_*` tools
- regular agents only see `loom_*` tools
- direct calls to `engineer_*` from other agents are rejected

In other words, `engineer_*` tools are group-scoped **and** designated-engineer-only.

### Board and planning

| Tool | What it is for |
|------|-----------------|
| `engineer_board_summary` | Compact overview of lanes, asks, labels, and agent state |
| `engineer_session_map` | Deterministic structured recovery snapshot for the current group |
| `engineer_board_list` | Full lane-grouped task list with optional filters |
| `engineer_task_show` | Full details for one task, including pipeline chain plus task artifact metadata when relevant |
| `engineer_agents_list` | Quick view of all agents in the group |
| `engineer_agent_show` | Deep inspection of one agent: session, worktree, tasks, terminals |
| `engineer_actions_list` | Discover available actions and their variables |
| `engineer_action_show` | Inspect one action's YAML, variables, and transitions |

### Task editing and dispatch

| Tool | What it is for |
|------|-----------------|
| `engineer_task_create` | Create a board task |
| `engineer_task_edit` | Change task title, description, labels, action, or action vars |
| `engineer_task_upload_artifact` | Upload and attach an image or other artifact directly to a task |
| `engineer_task_verify` | Record deploy/restart attempted, smoke passed/failed, and verification notes |
| `engineer_task_move` | Move a task between lanes |
| `engineer_task_dispatch` | Dispatch one task to a new or existing agent |
| `engineer_batch_dispatch` | Dispatch a planned batch with concurrency control |
| `engineer_task_resolve` | Resolve an ask task and send the answer back to the waiting agent |

### Events and recovery

| Tool | What it is for |
|------|-----------------|
| `engineer_events` | Poll recent panel events, especially after recovery |
| `engineer_notifications` | Configure digest timing and optional event types |
| `engineer_resume` | Unpause event delivery after a `engineer_ask` exchange |
| `engineer_journal` | Append a journal entry |
| `engineer_journal_read` | Read recent journal entries |

### Communication and agent control

| Tool | What it is for |
|------|-----------------|
| `engineer_agent_message` | Send a message into another agent's terminal and create a visible follow-up task for the reply |
| `engineer_note` | Post a non-blocking note or soft question for the human |
| `engineer_ask` | Ask the human a question and pause event pushes |
| `engineer_agent_close` | Close an agent session while leaving its worktree on disk |
| `engineer_agent_relaunch` | Relaunch a stopped agent, reusing worktree and provider resume when available |

### Weaver-to-worker follow-up tasks

`engineer_agent_message` is now audited on the board instead of being a purely ephemeral terminal nudge:

- If the target worker already has an active task, Loom creates a derived follow-up task under that task.
- If the worker is otherwise idle, Loom creates a standalone root follow-up task in the same group.
- The follow-up stores the worker it expects to hear back from, so replies can be resolved unambiguously later.

Workers answer through `loom_reply(...)`:

- If there is only one open designated-engineer follow-up task for that worker, `loom_reply(message=\"...\")` is enough.
- If multiple follow-ups are open, the worker must pass the specific task as well.

When the worker replies, Loom appends the reply to the follow-up task's history and marks only that follow-up task as answered/done. It does **not** auto-complete the parent implementation/review task just because the side conversation is over.

### Review, merge, and worktree operations

| Tool | What it is for |
|------|-----------------|
| `engineer_diff` | Review structured summaries, diff stats, or full diffs before merge |
| `engineer_merge` | Merge a worktree branch into its base branch |
| `engineer_rebase` | Rebase a conflicted worktree branch onto its base branch |
| `engineer_create_pr` | Push and open a GitHub PR with `gh` |
| `engineer_worktree_checkpoint` | Snapshot the worktree before a risky operation |
| `engineer_worktree_remove` | Remove the worktree after merge or cleanup |

## Batch dispatch and orchestration patterns

`engineer_batch_dispatch` is the designated engineer's main tool for deliberate orchestration instead of ad-hoc dispatching.

### How batch dispatch works

Batch dispatch:

- processes tasks in the order you pass them
- enforces `max_concurrent` against active non-designated-engineer agents in the group
- can keep related tasks on the same agent with `agent_group`
- refuses tasks that are already assigned, already done, already in progress, or blocked by dependencies

If you omit `max_concurrent`, Loom uses the group's stored Weaver default worker concurrency.

### Result states

Batch results can come back as:

- **`dispatched`** when the task was launched immediately
- **`queued`** when Loom routed the work to an existing busy agent
- **`deferred`** when dispatch would exceed `max_concurrent`
- **`failed`** when the task was invalid for dispatch

### When to use `agent_group`

Use `agent_group` when several ordered tasks should stay on the same worker agent. This is useful for:

- a small implementation sequence that benefits from shared context
- follow-up cleanup after a first task on the same area
- keeping related tasks from consuming multiple worker slots

## Review, merge, and cleanup flows

The designated engineer is expected to handle the operational end of agent work, not just the initial dispatch.

### Review flow

A practical review sequence is:

1. inspect the agent with `engineer_agent_show`
2. start with `engineer_diff(..., summary_only=true)` to get machine-readable changed-file signals without loading raw patch text
3. use `engineer_diff(..., stat_only=true)` if you want a quick human-readable diffstat to size the change
4. inspect specific risky paths with `engineer_diff(..., paths=[...])` if needed
5. ask the agent for clarification with `engineer_agent_message` if the diff is unclear

For shared same-agent branches, `engineer_agent_show` also exposes task-boundary metadata so the designated engineer can tell which completed task is the latest clean mergeable boundary and which queued tasks resume after it.

### Merge flow

`engineer_merge` is a server-side merge operation. If Loom detects conflicts, it returns an error with conflict context and the designated engineer can run `engineer_rebase` before retrying the merge.

On shared sequential branches, `engineer_merge` also refuses to merge when the latest task boundary is no longer cleanly mergeable, for example because a queued follow-up already started or the branch tip moved after the boundary was recorded.

`engineer_rebase` uses the same merge-readiness checks before it runs, aborts automatically if the rebase hits conflicts, and returns enough conflict detail for the designated engineer to decide whether to retry or escalate to the human for a manual plan.

Typical flow:

1. review diff
2. merge with `engineer_merge`
3. if merge reports conflicts, run `engineer_rebase` and retry `engineer_merge`
4. optionally create a PR with `engineer_create_pr`
5. close the agent and/or remove the worktree

`engineer_merge` also supports:

- `close_agent_on_merge`
- `remove_worktree_on_merge`

### Cleanup flow

After merge, the designated engineer can use:

- `engineer_agent_close` to remove the live session
- `engineer_worktree_remove` to clean the branch checkout from disk
- `engineer_agent_relaunch` when a stopped agent should continue working instead of being retired

## Practical usage patterns

### Starting a new orchestration session

When there is no useful journal history yet, the designated engineer should introduce itself and ask the human what to focus on before dispatching anything substantial.

### Running a wave

Use a compact pattern:

1. `engineer_board_summary`
2. `engineer_actions_list` or `engineer_action_show` if action choice matters
3. `engineer_batch_dispatch` for the next wave
4. wait for Loom digests

### Idle board with backlog remaining

When a wave finishes, the designated engineer should distinguish between two states:

- **Waiting on active work**: agents are still running or tasks are still in `In Progress`. In that case, wait for Loom digests.
- **Idle with backlog remaining**: there are 0 active agents, 0 `In Progress` tasks, and work still sits in `Backlog` or `To Do`. That is not a terminal steady state.

In the second case, the designated engineer should read `engineer_board_summary` and then either:

- dispatch the next best wave if the user's standing priorities already make the next step clear
- post a `engineer_note` that proposes the next wave and explains what ambiguity or constraint is preventing automatic dispatch

Use `engineer_ask` in this state only if the missing information is a
blocking human checkpoint and the board should pause until the answer
arrives.

It should remain idle only when the backlog is truly exhausted or a human checkpoint, approval, or blocking answer is still pending.

### Recovering after `/clear`

Use the recovery sequence, then make one decision at a time:

1. read journal
2. read board summary
3. read recent events
4. inspect only the tasks or agents that changed
5. write a checkpoint if the recovered state is non-trivial

### Handling blocked work

When an agent is blocked or errors:

- inspect the task and agent state
- message the agent if more context can unblock it
- use `engineer_task_resolve` if the blocker is a human-answer task
- use `engineer_ask` if the next decision belongs to the human

## CLI

The main Weaver-facing CLI surface today is journal inspection plus reply flow from agent sessions:

```bash
loom weaver journal
loom weaver journal -n 50
loom weaver journal -t checkpoint
loom weaver journal --json

loom ai reply "your response"
```

Most orchestration control happens through the designated engineer's MCP tools rather than separate CLI commands.

## Practical summary

The best way to think about the designated engineer is:

- a board-scoped orchestrator, not a generic autonomous agent
- driven by digests and recovery, not by constant polling
- stateful because of the journal, not because chat history is permanent
- safest when dispatching in waves and escalating ambiguous decisions to the human

If you want the designated engineer to work well, give it:

- clear actions and transitions
- bounded concurrency
- explicit review and merge habits
- regular journal checkpoints

That combination matches how Loom's current product behavior is designed to operate.
