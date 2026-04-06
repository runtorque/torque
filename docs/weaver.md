# Weaver

The weaver is a semi-autonomous orchestrator agent that manages the task board, dispatches work to other agents, and consults with the human at key decision points. Each group can have one weaver. It acts as a project manager --- analyzing priorities, creating and dispatching tasks, reacting to events, and maintaining a persistent decision journal.

The weaver is not fully autonomous. It asks the human for guidance when needed (priorities, design decisions, approvals) and operates independently between those checkpoints. It decides when it needs input and when it can proceed on its own.

## Creating a weaver

Open the **Weaver** panel from the taskbar, switch to the **Settings** tab, and click **+ Create Weaver**. This creates a special agent with a system prompt that defines the weaver role and available tools.

The weaver must be created through this flow --- you cannot designate an existing agent as a weaver, because the system prompt (`--append-system-prompt-file`) must be set at boot time.

Each group can have at most one weaver. The create button is hidden when a weaver already exists. Removing the weaver agent clears the designation; the journal persists and is inherited by a new weaver.

## How it works

### System prompt

The weaver boots with a system prompt appended via Claude Code's `--append-system-prompt-file` flag. This prompt defines:

- The weaver's role and available MCP tools
- Operating guidelines (journal discipline, event response, context recovery)
- Instructions to call `weaver_ask` on first start to get human guidance

The system prompt survives `/clear` --- when context is cleaned, the weaver retains its identity and instructions. Only the conversation history is lost.

### Custom instructions

In the Weaver panel's **Settings** tab, you can write custom instructions that are appended to the system prompt. These steer the weaver without editing action YAML:

```
Focus on the auth and payments modules first.
Never dispatch more than 3 agents concurrently.
Always create a review task after implementation.
```

Custom instructions persist in SQLite and apply on every weaver boot or restart.

### Decision journal

The weaver maintains a persistent journal in SQLite. It writes structured entries:

| Type | Purpose |
|------|---------|
| **decision** | An action taken with rationale ("Dispatched auth task because it's highest priority") |
| **observation** | Something noted ("Agent fix-auth completed, tests passing") |
| **checkpoint** | Board state summary for context recovery |
| **plan** | Intended next steps |

The journal is visible in the Weaver panel's **Journal** tab, with entries shown newest-first and color-coded by type. Right-click an entry to delete it.

After a `/clear` or restart, the weaver reads its journal to reconstruct context:

```
weaver_journal_read  -->  weaver_board_summary  -->  weaver_events
```

Use `weaver_board_list` only when the compact summary is not enough.

### Human interaction

The weaver uses `weaver_ask` to post questions to the human. When it calls this tool:

1. The question appears as an amber banner in the Weaver panel's Journal tab
2. Event delivery is automatically paused
3. The weaver's agent cell shows a pulsing "? awaiting input" indicator
4. The Weaver taskbar button gets an attention badge

The human can reply two ways:

- **Via the panel**: type in the reply textarea and click "Send Reply". Loom sends the answer to the weaver's terminal and unpauses events.
- **Directly in the terminal**: type into the weaver's Claude Code session. The weaver processes the input and calls `weaver_resume` to unpause events.

### Event digests

The weaver receives event notifications as formatted text pushed to its terminal. Events are:

- **Idle-gated**: only delivered when the weaver is idle, never mid-thought
- **Batched**: accumulated over a configurable interval (default 60 seconds)
- **Filtered**: mandatory events always appear; optional events are configurable

**Mandatory events** (always included):

- `task_completed` --- agent finished a task
- `agent_error` --- unrecoverable error
- `agent_blocked` --- agent needs help
- `agent_reply` --- agent replied to a weaver message
- `ask_created` --- agent needs human/weaver input

**Optional events** (configurable in Settings tab):

- `agent_started`, `task_dispatched`, `task_derived`, `agent_progress`

A **heartbeat** is sent if no events occur within the max interval (default 5 minutes), showing current board status and active agents.

The Weaver panel header shows the number of buffered events and time until the next push.

### Pause / resume

The pause button in the Weaver panel header suspends event delivery. Events still buffer but aren't sent until resumed. Use this when you want to interact with the weaver directly without competing with automated digests.

`weaver_ask` auto-pauses events. `weaver_resume` (or a panel reply) unpauses them.

## MCP tools

The weaver has access to these tools via MCP:

### Read tools

| Tool | Description |
|------|-------------|
| `weaver_board_summary` | Compact board overview with lane counts, active agent status, pending asks, and key label counts. |
| `weaver_board_list` | List tasks grouped by lane, with optional filters (lane, label, search). Scoped to the weaver's group. |
| `weaver_task_show` | Full task details by slug or ID. Auto-includes pipeline chain for pipeline tasks. |
| `weaver_agents_list` | All agents in the group with status, current task, and activity. |
| `weaver_actions_list` | Available actions (project + user scope) with variables and descriptions. |
| `weaver_action_show` | Full action details including prompt template, transitions, and variables. |

### Write tools

| Tool | Description |
|------|-------------|
| `weaver_task_create` | Create a task with title, description, action, labels, and lane. |
| `weaver_task_edit` | Update fields on an existing task (partial update). |
| `weaver_task_move` | Move a task to a different lane. |
<<<<<<< HEAD
| `weaver_task_dispatch` | Dispatch a task to a new or existing agent. Supports custom naming via `name`, backend selection via `agent_type`, and boot-command override via `command` for new agents. |
| `weaver_batch_dispatch` | Dispatch an ordered batch with `max_concurrent`. Tasks may share an `agent_group` so later tasks queue on the same agent instead of consuming another worker slot. |
| `weaver_task_resolve` | Resolve an ask task by sending an answer to the waiting agent. |

### Event tools

| Tool | Description |
|------|-------------|
| `weaver_events` | Poll for recent events (for catching up after `/clear`). |
| `weaver_notifications` | Configure push interval and enable/disable optional event types. |
| `weaver_resume` | Unpause event delivery after a `weaver_ask` exchange. |

### Journal tools

| Tool | Description |
|------|-------------|
| `weaver_journal` | Append a decision, observation, checkpoint, or plan entry. |
| `weaver_journal_read` | Read recent entries, optionally filtered by type. |

### Interaction tools

| Tool | Description |
|------|-------------|
| `weaver_agent_message` | Send a message to any agent's terminal. The agent can reply via `loom_reply`. |
| `weaver_ask` | Ask the human a question. Auto-pauses events and shows the question in the panel. |
| `weaver_agent_close` | Close an agent (end session, remove from group). Worktree is preserved. |

### Worktree tools

| Tool | Description |
|------|-------------|
| `weaver_merge` | Merge an agent's worktree branch into the base branch. Checks for conflicts first --- if found, instructs the weaver to ask the human for permission to rebase. Optional `close_agent_on_merge` and `remove_worktree_on_merge` flags enable explicit post-merge cleanup. |
| `weaver_create_pr` | Push branch and create a GitHub PR via `gh`. Accepts custom title and body. |
| `weaver_diff` | Get the diff of an agent's worktree vs base branch. Supports `stat_only` mode and path filtering. |

## CLI

```bash
loom weaver journal                        # show recent journal entries
loom weaver journal -n 50                  # show last 50 entries
loom weaver journal -t checkpoint          # filter by type
loom weaver journal --json                 # machine-readable output

loom ai reply "your response"              # reply to a weaver message (from agent terminal)
```

## UI

### Weaver panel

The Weaver panel sits in the taskbar between Events and other panels. It has two tabs:

**Journal tab** (default):
- Pending question banner (amber) with reply textarea when the weaver is waiting for input
- Journal entries newest-first, with colored type badges and relative timestamps
- Right-click entries to delete them

**Settings tab**:
- **Agent**: shows the weaver's name and status, or a create button
- **Custom Instructions**: textarea for instructions appended to the system prompt
- **Notifications**: push/max interval dropdowns and event type checkboxes

### Agent cell indicators

- The weaver agent has an **amber left border** to distinguish it from regular agents
- It is always **pinned first** in the group's agent grid and iTerm2 tab order
- When awaiting human input: **pulsing glow** and "? awaiting input" text
- The Weaver **taskbar button** gets a pulsing amber border when a question is pending

## Design notes

- **One per group**: enforced at creation time. The journal belongs to the group, not the agent --- a new weaver inherits the previous journal.
- **System prompt vs user prompt**: the weaver's identity (system prompt) survives `/clear`; the task (user prompt) is what changes between dispatches.
- **Semi-autonomous**: the weaver is not a pipeline or a script. It makes runtime decisions, but checks in with the human via `weaver_ask` at key decision points.
- **Context management**: the journal is the weaver's persistent brain. Periodic checkpoint entries compress the weaver's mental model so context cleanup doesn't mean starting over.
