# Weaver

The Weaver is Loom's per-group orchestrator agent. It watches the board, receives event digests, dispatches work to agents, keeps a persistent journal, and asks the human for guidance when needed.

The Weaver is deliberately semi-autonomous:

- it can plan, dispatch, review, merge, and clean up on its own
- it should use `weaver_ask` only when priorities, approvals, or design choices need a blocking human decision
- it should recover from `/clear` or restart by reading the journal and current board state instead of relying on chat history

## What the Weaver does

Each group can have at most one Weaver. It acts as the control loop for that group's work:

1. Read the board and decide what should happen next.
2. Dispatch tasks to agents in waves.
3. React to digests and replies from running agents.
4. Keep a journal of decisions, observations, checkpoints, and plans.
5. Review diffs, merge worktrees, open PRs, and clean up sessions.
6. Pause and ask the human when the next step should not be guessed.

The journal belongs to the group, not the individual agent. If you recreate the Weaver later, it inherits the same journal history.

## Creating and configuring the Weaver

Open the **Weaver** panel, switch to **Settings**, and click **+ Create Weaver**.

The Weaver must be created through this flow because Loom needs to boot it with a persistent system prompt. You cannot turn an existing agent into a Weaver after the fact.

### Weaver panel

The panel has two tabs:

- **Journal** shows the persistent journal, blocking asks, and non-blocking notes/questions from the Weaver.
- **Settings** controls the Weaver agent, backend override, custom instructions, and notification settings.

The panel header also shows:

- buffered event count
- time until the next digest push
- a pause/resume toggle for event delivery

### Settings tab

The current product behavior is:

- **Agent** section shows the current Weaver agent or the create button.
- **Backend** lets you override the provider and boot command just for the Weaver. If empty, the Weaver uses the group's defaults.
- **Custom Instructions** appends extra operating rules to the Weaver system prompt.
- **Notifications** configures push interval, max interval, and which optional event types appear in digests.

### Agent cell behavior

The Weaver agent is visually distinct:

- pinned first in the group's agent grid and tab order
- amber left border
- `? awaiting input` state when a human reply is pending
- taskbar attention state when the Weaver has asked a question

## Operating model

The Weaver is most effective when it works in short control loops instead of trying to solve everything in one huge plan.

### A practical day-to-day loop

1. Read the current state with `weaver_board_summary`.
2. Use `weaver_task_show`, `weaver_agent_show`, or `weaver_action_show` only for the tasks that need deeper inspection.
3. Dispatch a wave of work with `weaver_task_dispatch` or `weaver_batch_dispatch`.
4. Wait for Loom digests instead of polling constantly.
5. When a digest arrives, react to the changed tasks and agents only.
6. Journal significant decisions and write periodic checkpoints.
7. Review diffs, merge, or ask the human when approval is needed.

### Dispatch philosophy

The Weaver system prompt steers it toward a few practical habits:

- dispatch in waves, not all at once
- reuse the same agent when context matters
- separate work across agents when tasks are independent
- inspect diff stats before deep review
- treat an idle board with remaining backlog as a new planning turn, not as done
- clean up worktrees and sessions intentionally after merge

## Event digests and delivery

The Weaver does not need to poll constantly. Loom pushes event digests into the Weaver's terminal when appropriate.

### How digests work

Digests are:

- **idle-gated**: Loom only pushes them when the Weaver is idle or waiting
- **buffered**: events accumulate between pushes
- **heartbeat-aware**: an idle heartbeat can arrive if no digest was sent
  for `heartbeat_interval`
- **scoped**: each Weaver only sees events for its own group

The notification controls expose three separate intervals:

- `push_interval`: the normal digest cadence
- `max_interval`: the cap for regular digest timing
- `heartbeat_interval`: send an idle heartbeat if no digest was sent for
  this long

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

These can be enabled or disabled in the Weaver settings:

- `agent_started`
- `task_dispatched`
- `task_derived`
- `agent_progress`

### What a digest contains

A digest includes:

- the buffered events
- a compact board summary
- active-agent summary when there are no new events
- a context-usage warning if the Weaver's token usage is getting large

## Journal and recovery

The journal is the Weaver's persistent memory. It is what lets the Weaver recover after `/clear`, a restart, or a long pause without needing the old conversation history.

### Journal entry types

| Type | Use it for |
|------|------------|
| **`decision`** | A choice the Weaver made and why |
| **`observation`** | Something learned from events, agents, or the human |
| **`checkpoint`** | A compact snapshot of board state and next steps |
| **`plan`** | Intended next actions |

### Recovery sequence

After `/clear` or restart, the current product behavior is to recover in this order:

```text
weaver_journal_read -> weaver_board_summary -> weaver_events
```

Use `weaver_board_list` only when the compact summary is not enough. That keeps recovery fast and avoids blowing context on the full board when the summary already tells you what changed.

### Journal discipline

The Weaver should write journal entries for:

- dispatch decisions
- priority changes
- notable failures or blocked states
- merge and PR decisions
- human answers that change the plan

It should also write periodic **checkpoint** entries so recovery does not require replaying dozens of smaller decisions.

The Journal tab in the UI shows entries newest-first, and entries can be deleted with a right-click.

## Asking the human

`weaver_note` is the non-blocking path for notes, soft questions, status
updates, or proposed next waves that should stay visible without pausing
orchestration.

`weaver_ask` is the Weaver's blocking human-in-the-loop mechanism. Use
it only when the next orchestration step should stop until the human
answers.

When the Weaver posts a non-blocking note:

1. the note appears in the Journal tab as an informational banner
2. event pushes continue normally
3. Loom does not enter awaiting-input state
4. the note is recorded in the journal and survives restart until dismissed

When the Weaver asks a question:

1. the question appears in the Journal tab as an amber banner
2. event pushes are automatically paused
3. the Weaver agent shows an awaiting-input state
4. the question is recorded in the journal as an observation

If the board is idle with backlog remaining and the Weaver only needs to
surface its recommended next wave or a soft preference question, it
should use `weaver_note`, not `weaver_ask`.

### Reply paths

The human can answer in two ways:

- **Via the panel**: Loom sends the reply to the Weaver terminal and automatically unpauses events.
- **Directly in the terminal**: the Weaver receives the reply in its own session and should call `weaver_resume` after handling it.

If the Weaver becomes active again after being idle with a pending question, Loom clears the pending-question state and unpauses delivery. The safest explicit pattern is still:

1. receive the answer
2. incorporate it into the plan
3. call `weaver_resume`

## MCP tool surface

All Weaver tools are available through the same `/mcp` endpoint as agent tools, using the `weaver_` prefix. They are scoped to the Weaver's group.

### Board and planning

| Tool | What it is for |
|------|-----------------|
| `weaver_board_summary` | Compact overview of lanes, asks, labels, and agent state |
| `weaver_board_list` | Full lane-grouped task list with optional filters |
| `weaver_task_show` | Full details for one task, including pipeline chain when relevant |
| `weaver_agents_list` | Quick view of all agents in the group |
| `weaver_agent_show` | Deep inspection of one agent: session, worktree, tasks, terminals |
| `weaver_actions_list` | Discover available actions and their variables |
| `weaver_action_show` | Inspect one action's YAML, variables, and transitions |

### Task editing and dispatch

| Tool | What it is for |
|------|-----------------|
| `weaver_task_create` | Create a board task |
| `weaver_task_edit` | Change task title, description, labels, action, or action vars |
| `weaver_task_verify` | Record deploy/restart attempted, smoke passed/failed, and verification notes |
| `weaver_task_move` | Move a task between lanes |
| `weaver_task_dispatch` | Dispatch one task to a new or existing agent |
| `weaver_batch_dispatch` | Dispatch a planned batch with concurrency control |
| `weaver_task_resolve` | Resolve an ask task and send the answer back to the waiting agent |

### Events and recovery

| Tool | What it is for |
|------|-----------------|
| `weaver_events` | Poll recent panel events, especially after recovery |
| `weaver_notifications` | Configure digest timing and optional event types |
| `weaver_resume` | Unpause event delivery after a `weaver_ask` exchange |
| `weaver_journal` | Append a journal entry |
| `weaver_journal_read` | Read recent journal entries |

### Communication and agent control

| Tool | What it is for |
|------|-----------------|
| `weaver_agent_message` | Send a message into another agent's terminal |
| `weaver_note` | Post a non-blocking note or soft question for the human |
| `weaver_ask` | Ask the human a question and pause event pushes |
| `weaver_agent_close` | Close an agent session while leaving its worktree on disk |
| `weaver_agent_relaunch` | Relaunch a stopped agent, reusing worktree and provider resume when available |

### Review, merge, and worktree operations

| Tool | What it is for |
|------|-----------------|
| `weaver_diff` | Review structured summaries, diff stats, or full diffs before merge |
| `weaver_merge` | Merge a worktree branch into its base branch |
| `weaver_rebase` | Rebase a conflicted worktree branch onto its base branch |
| `weaver_create_pr` | Push and open a GitHub PR with `gh` |
| `weaver_worktree_checkpoint` | Snapshot the worktree before a risky operation |
| `weaver_worktree_remove` | Remove the worktree after merge or cleanup |

## Batch dispatch and orchestration patterns

`weaver_batch_dispatch` is the Weaver's main tool for deliberate orchestration instead of ad-hoc dispatching.

### How batch dispatch works

Batch dispatch:

- processes tasks in the order you pass them
- enforces `max_concurrent` against active non-Weaver agents in the group
- can keep related tasks on the same agent with `agent_group`
- refuses tasks that are already assigned, already done, already in progress, or blocked by dependencies

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

The Weaver is expected to handle the operational end of agent work, not just the initial dispatch.

### Review flow

A practical review sequence is:

1. inspect the agent with `weaver_agent_show`
2. start with `weaver_diff(..., summary_only=true)` to get machine-readable changed-file signals without loading raw patch text
3. use `weaver_diff(..., stat_only=true)` if you want a quick human-readable diffstat to size the change
4. inspect specific risky paths with `weaver_diff(..., paths=[...])` if needed
5. ask the agent for clarification with `weaver_agent_message` if the diff is unclear

For shared same-agent branches, `weaver_agent_show` also exposes task-boundary metadata so the Weaver can tell which completed task is the latest clean mergeable boundary and which queued tasks resume after it.

### Merge flow

`weaver_merge` is a server-side merge operation. If Loom detects conflicts, it returns an error with conflict context and the Weaver can run `weaver_rebase` before retrying the merge.

On shared sequential branches, `weaver_merge` also refuses to merge when the latest task boundary is no longer cleanly mergeable, for example because a queued follow-up already started or the branch tip moved after the boundary was recorded.

`weaver_rebase` uses the same merge-readiness checks before it runs, aborts automatically if the rebase hits conflicts, and returns enough conflict detail for the Weaver to decide whether to retry or escalate to the human for a manual plan.

Typical flow:

1. review diff
2. merge with `weaver_merge`
3. if merge reports conflicts, run `weaver_rebase` and retry `weaver_merge`
4. optionally create a PR with `weaver_create_pr`
5. close the agent and/or remove the worktree

`weaver_merge` also supports:

- `close_agent_on_merge`
- `remove_worktree_on_merge`

### Cleanup flow

After merge, the Weaver can use:

- `weaver_agent_close` to remove the live session
- `weaver_worktree_remove` to clean the branch checkout from disk
- `weaver_agent_relaunch` when a stopped agent should continue working instead of being retired

## Practical usage patterns

### Starting a new orchestration session

When there is no useful journal history yet, the Weaver should introduce itself and ask the human what to focus on before dispatching anything substantial.

### Running a wave

Use a compact pattern:

1. `weaver_board_summary`
2. `weaver_actions_list` or `weaver_action_show` if action choice matters
3. `weaver_batch_dispatch` for the next wave
4. wait for Loom digests

### Idle board with backlog remaining

When a wave finishes, the Weaver should distinguish between two states:

- **Waiting on active work**: agents are still running or tasks are still in `In Progress`. In that case, wait for Loom digests.
- **Idle with backlog remaining**: there are 0 active agents, 0 `In Progress` tasks, and work still sits in `Backlog` or `To Do`. That is not a terminal steady state.

In the second case, the Weaver should read `weaver_board_summary` and then either:

- dispatch the next best wave if the user's standing priorities already make the next step clear
- post a `weaver_note` that proposes the next wave and explains what ambiguity or constraint is preventing automatic dispatch

Use `weaver_ask` in this state only if the missing information is a
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
- use `weaver_task_resolve` if the blocker is a human-answer task
- use `weaver_ask` if the next decision belongs to the human

## CLI

The main Weaver-facing CLI surface today is journal inspection plus reply flow from agent sessions:

```bash
loom weaver journal
loom weaver journal -n 50
loom weaver journal -t checkpoint
loom weaver journal --json

loom ai reply "your response"
```

Most orchestration control happens through the Weaver's MCP tools rather than separate CLI commands.

## Practical summary

The best way to think about the Weaver is:

- a board-scoped orchestrator, not a generic autonomous agent
- driven by digests and recovery, not by constant polling
- stateful because of the journal, not because chat history is permanent
- safest when dispatching in waves and escalating ambiguous decisions to the human

If you want the Weaver to work well, give it:

- clear actions and transitions
- bounded concurrency
- explicit review and merge habits
- regular journal checkpoints

That combination matches how Loom's current product behavior is designed to operate.
