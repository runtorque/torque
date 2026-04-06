# Implementation Plan: Weaver (Phase 5)

**Roadmap phase**: 5 — Autonomous Orchestration
**Status**: Planning
**Goal**: A dedicated orchestrator agent per group that manages tasks, dispatches agents, reacts to events, and maintains a persistent decision journal. The weaver is a first-class concept in Loom — a special agent unique to each group with its own UI panel, event subscription system, and context management strategy.

---

## The Problem

Loom can dispatch tasks to agents and agents can self-organize via pipelines (`derive`, `ask`). But there is no entity that looks at the whole board, decides what to do next, responds to events, and drives the project forward. Today that role falls to the human — manually dispatching tasks, monitoring agent progress, resolving questions, and reacting to errors.

The weaver fills this gap. It's an AI agent (Claude Code, Codex, etc.) that acts as a project manager for a group:

- **Dispatches work** — creates tasks, assigns actions, launches agents
- **Reacts to events** — when an agent finishes, errors, or asks a question, the weaver decides the next step
- **Maintains memory** — keeps a persistent decision journal that survives context cleanup
- **Communicates with agents** — sends messages to agents and receives replies

The weaver is NOT a pipeline definition (that's actions + transitions). It's a live, thinking agent that makes runtime decisions about the project. Pipelines define *what can happen*; the weaver decides *what should happen now*.

---

## Design Principles

1. **One weaver per group** — Each group can have at most one designated weaver agent. Multiple weavers in the same group would conflict on decisions. The UI enforces this: once a weaver exists, the create button is disabled.
2. **Push-primary events** — The weaver receives event digests via text pushed to its terminal (same mechanism as `resolve_ask`). Polling via `weaver_events` is secondary — for catching up after context cleanup or manual checks.
3. **Idle-gated delivery** — Event digests are only sent when the weaver agent is idle (activity is empty / waiting). Never interrupt a weaver mid-thought or mid-tool-call. Events buffer until the weaver goes idle.
4. **Mandatory + optional events** — Some events always appear in digests (task completed, agent error, agent reply). Others are configurable (agent started, progress updates). A max interval (default 5 minutes) ensures the weaver gets periodic heartbeats even when nothing critical happened.
5. **Journal as persistent brain** — The weaver writes structured journal entries (decisions, observations, checkpoints, plans). On context cleanup, the journal + current board state is enough to resume orchestration. The journal is per-group and stored in SQLite.
6. **Pausable by the user** — A pause/resume button in the UI suspends event pushes so the human can interact with the weaver directly without competing with automated digests.

---

## Architecture

### Weaver lifecycle

```
User designates agent as weaver (UI or API)
  │
  ├── GroupSettings.weaver_agent_id = cell.id
  ├── Weaver gets weaver_* MCP tools exposed
  ├── Event buffer + timer created for this group
  │
  └── Weaver action prompt boots the agent
        │
        ├── weaver_journal_read → recover context
        ├── weaver_board_list → current board state
        ├── weaver_events → catch up on missed events
        │
        └── Orchestration loop:
              │
              ├── Weaver dispatches tasks, makes decisions
              ├── Weaver writes journal entries
              ├── Weaver goes idle
              │     │
              │     └── Loom pushes event digest
              │           │
              │           └── Weaver wakes up, processes events
              │                 ├── Dispatch next task
              │                 ├── Resolve ask
              │                 ├── Send message to agent
              │                 └── Write journal entry
              │
              └── (repeat until paused or context exhausted)
```

### Event push flow

```
Agent calls loom_done (or error, blocked, reply, etc.)
  │
  ├── Panel event emitted (existing)
  │
  ├── Weaver event buffer check:
  │     │
  │     ├── Is there a weaver for this group?
  │     ├── Is the weaver paused? → skip
  │     ├── Is this event type enabled? (mandatory events always pass)
  │     │
  │     └── Buffer the event
  │
  └── Delivery timer check:
        │
        ├── Is the weaver idle? (activity == "" or "waiting")
        │     ├── Yes → flush buffer, send digest to terminal
        │     └── No → wait, check again on next activity change
        │
        └── Has max_interval elapsed since last digest?
              ├── Yes → mark as due, deliver on next idle
              └── No → wait for timer or next event
```

### Agent reply flow

```
Weaver → weaver_agent_message(agent="fix-auth", message="Rebase on main")
  │
  ├── Loom sends formatted message to agent's terminal:
  │     ── Message from Weaver ────────────────────
  │     Rebase on main, the auth PR was merged.
  │     Reply with: loom_reply("your response")
  │     ────────────────────────────────────────────
  │
  └── Agent reads message, does work, then:
        │
        Agent → loom_reply(message="Rebased successfully")
          │
          ├── Panel event: kind="agent_reply", message="Rebased successfully"
          ├── Buffered for weaver digest (mandatory event)
          │
          └── Next digest to weaver includes:
                • agent_reply: fix-auth → "Rebased successfully"
```

### Context management

```
Weaver running, context growing
  │
  ├── Token tracking (existing session_tokens_in/out)
  │
  ├── When context > 80% threshold:
  │     Digest includes: "⚠ Context usage: 82%. Consider writing a checkpoint."
  │
  ├── Weaver writes checkpoint:
  │     weaver_journal(type="checkpoint", entry="Board: 2 IP, 4 TD...")
  │
  └── On context cleanup (/clear or session restart):
        │
        └── Weaver action prompt instructs:
              1. weaver_journal_read(tail=20) → recover decisions
              2. weaver_board_list → current state
              3. weaver_events(since=last_checkpoint) → missed events
              4. Resume orchestration
```

---

## Data Model

### GroupSettings additions

```python
@dataclass
class GroupSettings:
    # ... existing fields ...

    # Weaver
    weaver_agent_id: str = ""              # the designated weaver agent for this group
```

### New: WeaverSettings (per-group, stored in DB)

```python
@dataclass
class WeaverSettings:
    group: str                             # group name (key)
    push_interval: int = 60               # seconds between digest pushes (min: 10)
    max_interval: int = 300               # max seconds between pushes (heartbeat)
    paused: bool = False                   # user paused event pushes
    enabled_events: list[str] = field(     # optional events (mandatory always on)
        default_factory=lambda: [
            "agent_started",
            "task_dispatched",
            "task_derived",
        ]
    )
```

**Mandatory events** (always included in digests, cannot be disabled):
- `task_completed` — agent finished a task
- `agent_reply` — agent replied to weaver message
- `agent_error` — unrecoverable error
- `agent_blocked` — agent needs help
- `ask_created` — agent needs human/weaver input

**Optional events** (on by default, weaver can toggle):
- `agent_started` — new agent session started
- `task_dispatched` — task was dispatched to an agent
- `task_derived` — subtask was created via derive
- `agent_progress` — agent progress update (off by default — too noisy)

### New: WeaverJournalEntry (SQLite table)

```python
@dataclass
class WeaverJournalEntry:
    id: int                                # auto-increment
    group: str                             # group name
    timestamp: float                       # time.time()
    entry_type: str                        # "decision" | "observation" | "checkpoint" | "plan"
    entry: str                             # free-text content
```

### DB schema changes

```sql
-- New columns on group_settings
weaver_agent_id TEXT NOT NULL DEFAULT ''

-- New table: weaver_settings
CREATE TABLE IF NOT EXISTS weaver_settings (
    group_name TEXT PRIMARY KEY,
    push_interval INTEGER NOT NULL DEFAULT 60,
    max_interval INTEGER NOT NULL DEFAULT 300,
    paused INTEGER NOT NULL DEFAULT 0,
    enabled_events TEXT NOT NULL DEFAULT '["agent_started","task_dispatched","task_derived"]'
);

-- New table: weaver_journal
CREATE TABLE IF NOT EXISTS weaver_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name TEXT NOT NULL,
    timestamp REAL NOT NULL,
    entry_type TEXT NOT NULL,
    entry TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weaver_journal_group
    ON weaver_journal(group_name, id DESC);
```

### AgentCell additions

```python
@dataclass
class AgentCell:
    # ... existing fields ...

    # Weaver message tracking
    pending_weaver_message: bool = False   # agent has an unread message from weaver
```

This is ephemeral (not persisted) — used to validate that `loom_reply` is only available when the agent has received a weaver message.

---

## MCP Tools

### Agent-side: new tool

#### `loom_reply`

Reply to a message from the weaver. Only works when the agent has a pending weaver message.

```json
{
    "name": "loom_reply",
    "description": "Reply to a message from the weaver (orchestrator agent). The reply is delivered to the weaver in its next event digest.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Your reply to the weaver."
            }
        },
        "required": ["message"]
    }
}
```

**Server handling**: `ai_report(action="reply")` — creates panel event with kind `agent_reply`, clears `cell.pending_weaver_message`.

### Weaver-side: final tool list

Tools are served from the same `/mcp` endpoint. The `weaver_` prefix provides namespace separation. Weaver tools don't require `X-Loom-Cell-Id` (though they accept it for audit logging).

#### Read tools

##### `weaver_board_list` (keep)

List all tasks on the board grouped by lane. Supports optional filters by lane, label, group, or text search.

```json
{
    "properties": {
        "lane": {"type": "string", "description": "Filter to a specific lane."},
        "label": {"type": "string", "description": "Filter to tasks with this label."},
        "group": {"type": "string", "description": "Filter to tasks in this group."},
        "search": {"type": "string", "description": "Text search across task title and description."}
    }
}
```

##### `weaver_task_show` (enriched — absorbs `task_chain`)

Show full details for a task by slug or ID. When the task is part of a pipeline, the response automatically includes the pipeline chain summary (task titles, lanes, depths, agents) — no need to call `task_chain` separately.

```json
{
    "properties": {
        "task": {"type": "string", "description": "Task slug or ID."}
    },
    "required": ["task"]
}
```

**Response includes** (when pipeline task):
```json
{
    "...task fields...",
    "pipeline_chain": [
        {"id": "...", "title": "...", "lane": "Done", "depth": 0, "agent": "impl-login"},
        {"id": "...", "title": "...", "lane": "In Progress", "depth": 1, "agent": "review-login"}
    ]
}
```

##### `weaver_agents_list` (keep)

List all active agents with their name, slug, status, group, current task, and activity detail.

##### `weaver_actions_list` (keep)

List available actions (project and user scope) with name, description, variables, and scope.

```json
{
    "properties": {
        "group": {"type": "string", "description": "Group name to resolve project-scoped actions."}
    }
}
```

##### `weaver_action_show` (keep)

Show full details of an action including its YAML contents, prompt template, transitions, and discovered variables.

```json
{
    "properties": {
        "name": {"type": "string", "description": "Action name (e.g. 'feature/implement')."},
        "group": {"type": "string", "description": "Group name to resolve project-scoped actions."}
    },
    "required": ["name"]
}
```

#### Write tools

##### `weaver_task_create` (keep)

Create a new task on the board. Specify a title and optionally attach an action, group, lane, and labels.

```json
{
    "properties": {
        "title": {"type": "string", "description": "Short task title."},
        "description": {"type": "string", "description": "Longer description or context."},
        "group": {"type": "string", "description": "Target group for the task."},
        "lane": {"type": "string", "description": "Lane to place the task in (default: Backlog)."},
        "action": {"type": "string", "description": "Action name to attach (e.g. 'feature/implement')."},
        "action_vars": {"type": "object", "description": "Action variable values as key-value pairs.", "additionalProperties": {"type": "string"}},
        "labels": {"type": "array", "items": {"type": "string"}, "description": "Labels to attach to the task."}
    },
    "required": ["title"]
}
```

##### `weaver_task_edit` (keep)

Edit fields on an existing task. Only the fields provided are updated — omitted fields are unchanged.

```json
{
    "properties": {
        "task": {"type": "string", "description": "Task slug or ID to edit."},
        "title": {"type": "string", "description": "New task title."},
        "description": {"type": "string", "description": "New description."},
        "labels": {"type": "array", "items": {"type": "string"}, "description": "New label list (replaces existing)."},
        "action": {"type": "string", "description": "New action name."},
        "action_vars": {"type": "object", "description": "New action variable values.", "additionalProperties": {"type": "string"}}
    },
    "required": ["task"]
}
```

##### `weaver_task_move` (keep)

Move a task to a different lane on the board.

```json
{
    "properties": {
        "task": {"type": "string", "description": "Task slug or ID."},
        "lane": {"type": "string", "description": "Target lane name."}
    },
    "required": ["task", "lane"]
}
```

##### `weaver_task_dispatch` (keep)

Dispatch a task to an agent. Creates a new agent by default, or dispatches to an existing agent if specified. The task moves to In Progress and the agent receives the rendered prompt.

```json
{
    "properties": {
        "task": {"type": "string", "description": "Task slug or ID to dispatch."},
        "agent": {"type": "string", "description": "Existing agent slug or ID to dispatch to. If omitted, a new agent is created."}
    },
    "required": ["task"]
}
```

##### `weaver_task_resolve` (keep)

Resolve an ask task by providing an answer. The answer is sent to the parent task's agent and the ask task moves to Done.

```json
{
    "properties": {
        "task": {"type": "string", "description": "Slug or ID of the ask task to resolve."},
        "answer": {"type": "string", "description": "The answer to send to the agent."}
    },
    "required": ["task", "answer"]
}
```

#### Event tools

##### `weaver_events` (new)

Poll for recent events. Primary use: catching up after context cleanup. Secondary use: manually checking what happened. Returns events from `PanelEventLog` filtered by the weaver's group.

```json
{
    "properties": {
        "since_id": {"type": "integer", "description": "Return events after this event ID (cursor). Omit for latest events."},
        "limit": {"type": "integer", "description": "Max events to return (default: 50)."},
        "types": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Filter to specific event types. Omit for all types."
        }
    }
}
```

**Response:**
```json
{
    "events": [
        {
            "id": 142,
            "timestamp": 1712419200.0,
            "kind": "task_completed",
            "agent_name": "fix-auth-bug",
            "message": "Task 'Fix authentication race condition' completed",
            "task_id": "abc123"
        }
    ],
    "cursor": 142
}
```

##### `weaver_notifications` (new)

Configure event push settings. Sets which optional events the weaver wants in its digests and the push interval. Mandatory events are always included.

```json
{
    "properties": {
        "push_interval": {
            "type": "integer",
            "description": "Seconds between digest pushes (min: 10, default: 60)."
        },
        "max_interval": {
            "type": "integer",
            "description": "Max seconds between pushes including heartbeats (default: 300)."
        },
        "enable": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional event types to enable: agent_started, task_dispatched, task_derived, agent_progress."
        },
        "disable": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional event types to disable."
        }
    }
}
```

#### Context tools

##### `weaver_journal` (new)

Append an entry to the weaver's persistent decision journal. Use this to record decisions, observations, and periodic checkpoints. The journal survives context cleanup — read it back with `weaver_journal_read` to resume orchestration.

```json
{
    "properties": {
        "type": {
            "type": "string",
            "enum": ["decision", "observation", "checkpoint", "plan"],
            "description": "Entry type: decision (action taken + rationale), observation (something noted), checkpoint (board state summary for context recovery), plan (intended next steps)."
        },
        "entry": {
            "type": "string",
            "description": "Journal entry content. Be concise but include rationale for decisions."
        }
    },
    "required": ["type", "entry"]
}
```

##### `weaver_journal_read` (new)

Read recent journal entries. Use after context cleanup or startup to recover the weaver's decision history and resume orchestration.

```json
{
    "properties": {
        "tail": {
            "type": "integer",
            "description": "Number of most recent entries to return (default: 20)."
        },
        "type": {
            "type": "string",
            "enum": ["decision", "observation", "checkpoint", "plan"],
            "description": "Filter to a specific entry type."
        }
    }
}
```

**Response:**
```json
{
    "entries": [
        {
            "id": 15,
            "timestamp": 1712419200.0,
            "type": "checkpoint",
            "entry": "Board: 2 In Progress, 4 To Do, 8 Backlog, 3 Done. Active: fix-auth (thinking), add-logging (tool_call). Next: dispatch caching task when IP count < 3."
        },
        {
            "id": 14,
            "timestamp": 1712418900.0,
            "type": "decision",
            "entry": "Dispatched 'Implement auth flow' → new agent. Action: feature/implement. Rationale: highest priority, no blockers, auth on critical path."
        }
    ]
}
```

#### Interaction tool

##### `weaver_agent_message` (new)

Send a message to any agent's terminal. The agent can reply via `loom_reply`, which appears in the weaver's next event digest. Use for: redirecting agents, providing context, answering questions without the full ask/resolve flow.

```json
{
    "properties": {
        "agent": {
            "type": "string",
            "description": "Agent slug or ID."
        },
        "message": {
            "type": "string",
            "description": "Message to send to the agent."
        }
    },
    "required": ["agent", "message"]
}
```

**Message format sent to agent's terminal:**
```
── Message from Weaver ────────────────────────
Rebase on main, the auth PR was merged.

Reply with: loom_reply("your response")
────────────────────────────────────────────────
```

### Tools removed

| Tool | Reason |
|---|---|
| `weaver_lanes_list` | Lanes are near-static (Backlog/To Do/In Progress/Done). Already visible in `board_list` output. |
| `weaver_pipelines_list` | Static analysis of action transitions. Useful for humans designing pipelines, not for active orchestration. Derivable from `actions_list`. |
| `weaver_task_chain` | Merged into `weaver_task_show` — pipeline chain is auto-included when the task has a `pipeline_root_id`. |

### Tool count summary

| Category | Tools | Count |
|---|---|---|
| Read | `board_list`, `task_show`, `agents_list`, `actions_list`, `action_show` | 5 |
| Write | `task_create`, `task_edit`, `task_move`, `task_dispatch`, `task_resolve` | 5 |
| Events | `events`, `notifications` | 2 |
| Context | `journal`, `journal_read` | 2 |
| Interaction | `agent_message` | 1 |
| **Total** | | **15** |

Plus 1 new agent-side tool: `loom_reply`.

---

## CLI Commands

### `loom ai reply`

```
loom ai reply <message>

Arguments:
  message              Reply text to send back to the weaver
```

**Behavior:**
- Auto-detects calling agent via `$LOOM_CELL_ID`
- Calls `ai_report(action="reply", message=...)`
- Errors if agent has no pending weaver message

### `loom weaver journal`

```
loom weaver journal [--group GROUP] [--tail N] [--type TYPE]

Flags:
  -g, --group GROUP    Group name (default: auto-detect from agent)
  -n, --tail N         Number of entries to show (default: 20)
  -t, --type TYPE      Filter by entry type (decision/observation/checkpoint/plan)
```

Reads the weaver journal directly from SQLite (works offline like other `loom` read commands).

---

## Event Push System

### WeaverEventBuffer (new class in `loom/weaver.py`)

Manages per-group event buffering and digest delivery.

```python
class WeaverEventBuffer:
    def __init__(self, state, bridge, panel_log):
        self._state = state
        self._bridge = bridge        # for send_text to weaver terminal
        self._panel_log = panel_log
        self._buffers: dict[str, list[dict]] = {}   # group → buffered events
        self._last_push: dict[str, float] = {}       # group → timestamp of last digest
        self._last_cursor: dict[str, int] = {}        # group → last event ID sent
        self._timer: asyncio.TimerHandle | None = None

    def on_panel_event(self, event: dict):
        """Called when a panel event is emitted. Buffer if a weaver cares."""

    def on_agent_idle(self, cell):
        """Called when an agent goes idle. Check if it's a weaver and flush."""

    async def _flush(self, group: str):
        """Format and send buffered events as a digest to the weaver's terminal."""

    def _format_digest(self, events: list[dict], board_summary: str) -> str:
        """Format events into a readable digest string."""

    def _format_heartbeat(self, board_summary: str) -> str:
        """Format a no-events heartbeat with board status."""
```

### Delivery rules

1. **Idle-gated**: Digests are only sent when the weaver's `activity` is `""` (empty) or `"waiting"`. The `on_agent_idle` hook fires when `EventBus._apply()` transitions a cell to idle.
2. **Interval-batched**: Events accumulate in the buffer. A timer fires every `push_interval` seconds. If the weaver is idle and events are buffered, flush. If not idle, the flush defers to the next `on_agent_idle` call.
3. **Max interval heartbeat**: If `max_interval` seconds pass since the last digest (even with no events), a heartbeat is sent on the next idle transition. The heartbeat includes board status but no events.
4. **Pause/resume**: When `WeaverSettings.paused` is `True`, events still buffer but no digests are sent. On resume, buffered events flush on next idle.
5. **Mandatory events**: `task_completed`, `agent_reply`, `agent_error`, `agent_blocked`, `ask_created` — always buffered regardless of `enabled_events` filter.
6. **Optional events**: `agent_started`, `task_dispatched`, `task_derived`, `agent_progress` — only buffered if in `enabled_events`.
7. **Group scoping**: Events are matched to the weaver's group via the agent's group field. Cross-group events are not delivered.

### Digest format

```
── Loom Digest (3 events) ─────────────────────
• task_completed: "fix-auth-race" done by agent fix-auth-bug
• agent_reply: fix-auth-bug → "Rebased, continuing with implementation"
• agent_blocked: refactor-db → "Need migration schema clarification"

Board: 3 In Progress · 2 To Do · 5 Backlog · 4 Done
────────────────────────────────────────────────
```

### Heartbeat format

```
── Loom Heartbeat ─────────────────────────────
No new events in last 5m.
Board: 3 In Progress · 2 To Do · 5 Backlog · 4 Done
Active: fix-auth-bug (thinking) · add-logging (tool_call)
────────────────────────────────────────────────
```

### Context warning (appended to digest when threshold exceeded)

```
⚠ Context usage: 82% (~820K/1M tokens). Consider writing a checkpoint.
```

---

## UI Design

### Weaver Panel

A new panel in the taskbar, between Agents and Events. Contains two sections:

#### Journal section

Scrollable feed of journal entries, most recent at top. Each entry shows:

```
┌─────────────────────────────────────────────┐
│  Weaver — my-project                        │
│                                             │
│  ┌─ checkpoint · 2m ago ──────────────────┐ │
│  │ Board: 2 IP, 4 TD, 8 BL, 3 Done.     │ │
│  │ Active: fix-auth (thinking), add-      │ │
│  │ logging (tool_call). Next: dispatch    │ │
│  │ caching task when IP < 3.             │ │
│  └────────────────────────────────────────┘ │
│                                             │
│  ┌─ decision · 5m ago ────────────────────┐ │
│  │ Dispatched "Implement auth flow" →     │ │
│  │ new agent. Action: feature/implement.  │ │
│  │ Rationale: highest priority, no deps.  │ │
│  └────────────────────────────────────────┘ │
│                                             │
│  ┌─ observation · 12m ago ────────────────┐ │
│  │ Agent fix-auth-bug completed. Tests    │ │
│  │ passing. Branch ready for review.      │ │
│  └────────────────────────────────────────┘ │
│                                             │
│  ─── Settings ────────────────────────────  │
│                                             │
│  Weaver agent: fix-auth-bug  [Designate]    │
│  Push interval: [60s ▾]                     │
│  Max interval:  [300s ▾]                    │
│  Paused: [ ] ← checkbox                    │
│                                             │
│  Events:                                    │
│  [x] task_completed (mandatory)             │
│  [x] agent_error (mandatory)               │
│  [x] agent_reply (mandatory)               │
│  [x] agent_blocked (mandatory)             │
│  [x] ask_created (mandatory)               │
│  [x] agent_started                          │
│  [x] task_dispatched                        │
│  [x] task_derived                           │
│  [ ] agent_progress                         │
│                                             │
│                        [⏸ Pause] / [▶ Resume] │
└─────────────────────────────────────────────┘
```

- Entry type shown as a colored badge: `decision` (blue), `observation` (gray), `checkpoint` (green), `plan` (yellow)
- Timestamps shown as relative ("2m ago", "1h ago")
- Settings section is collapsible
- Mandatory event checkboxes are checked and disabled (grayed out)
- Pause/Resume button toggles `WeaverSettings.paused` — prominent, always visible

#### Weaver designation

- In the group settings modal (existing), a new "Weaver" section
- "Designate weaver" dropdown lists agents in the group (only agents, not terminals)
- Once designated, the dropdown is replaced with the agent name + "Remove weaver" button
- If a weaver already exists for the group, the "Add weaver" option in the main UI is disabled

#### Pause/Resume button

- Shown in the Weaver panel settings section
- Also shown as a small toggle icon on the weaver's agent cell in the Agents panel
- When paused: event buffer still accumulates, but no digests are sent
- When resumed: if events are buffered, they flush on next idle check
- Visual indicator: the weaver agent cell shows a "paused" badge when paused

---

## Server Commands

### New commands

#### `weaver_message`

Send a message from the weaver to an agent's terminal.

**Payload:**
```json
{
    "cmd": "weaver_message",
    "agent_id": "abc123",
    "message": "Rebase on main, the auth PR was merged."
}
```

**Behavior:**
1. Resolve agent by ID/slug
2. Validate agent exists and is running
3. Format message with weaver header/footer
4. Send to agent's terminal via `bridge.send_text()`
5. Set `cell.pending_weaver_message = True`
6. Emit panel event: kind `weaver_message`, message preview
7. Return `{"type": "ok"}`

#### `ai_report` — new action: `reply`

Reply from an agent to the weaver.

**Payload:**
```json
{
    "cmd": "ai_report",
    "action": "reply",
    "cell_id": "abc123",
    "message": "Rebased successfully, continuing with implementation"
}
```

**Behavior:**
1. Resolve calling agent
2. Validate `cell.pending_weaver_message` is True (else error: "No pending weaver message")
3. Clear `cell.pending_weaver_message`
4. Find the weaver for this agent's group
5. Emit panel event: kind `agent_reply`, with agent name and message
6. Buffer event for weaver digest delivery
7. Return `{"type": "ok"}`

#### `weaver_journal_append`

Append an entry to the weaver's journal.

**Payload:**
```json
{
    "cmd": "weaver_journal_append",
    "group": "my-project",
    "entry_type": "decision",
    "entry": "Dispatched auth flow task..."
}
```

**Behavior:**
1. Validate entry_type is one of: decision, observation, checkpoint, plan
2. Insert into `weaver_journal` table
3. Emit delta: `journal_append` (new delta op type for the Weaver panel)
4. Return `{"type": "ok", "id": entry_id}`

#### `weaver_journal_read`

Read recent journal entries.

**Payload:**
```json
{
    "cmd": "weaver_journal_read",
    "group": "my-project",
    "tail": 20,
    "entry_type": ""
}
```

**Behavior:**
1. Query `weaver_journal` table: `WHERE group_name = ? ORDER BY id DESC LIMIT ?`
2. Optional type filter: `AND entry_type = ?`
3. Return `{"type": "journal", "entries": [...]}`

#### `weaver_update_settings`

Update weaver notification settings.

**Payload:**
```json
{
    "cmd": "weaver_update_settings",
    "group": "my-project",
    "push_interval": 60,
    "max_interval": 300,
    "enabled_events": ["agent_started", "task_dispatched", "task_derived"]
}
```

#### `weaver_pause` / `weaver_resume`

Toggle event push delivery.

**Payload:**
```json
{
    "cmd": "weaver_pause",
    "group": "my-project"
}
```

### Modified commands

#### `add_agent` — weaver enforcement

When adding an agent to a group that already has a weaver, the agent is created normally (it's not a weaver). The UI prevents creating a second weaver — but the server also validates: if the request includes `is_weaver: true` and the group already has a `weaver_agent_id`, return an error.

#### `remove_agent` — weaver cleanup

When the weaver agent is removed, clear `GroupSettings.weaver_agent_id`. The journal persists (it belongs to the group, not the agent). A new weaver can be designated and will inherit the journal.

---

## Implementation Steps

### Step 1: Data model (`loom/state.py`, `loom/db.py`)

- Add `weaver_agent_id` to `GroupSettings` dataclass
- Add `WeaverJournalEntry` dataclass
- Add `pending_weaver_message` ephemeral field to `AgentCell`
- DB: add `weaver_agent_id` column to `group_settings` via ALTER TABLE migration
- DB: create `weaver_settings` table
- DB: create `weaver_journal` table with index
- DB: `save_weaver_settings()`, `load_weaver_settings()`, `save_journal_entry()`, `load_journal_entries(group, limit, entry_type)` methods
- `MatrixState`: `get_weaver_for_group(group)` helper, `journal_append()`, `journal_read()` methods
- Delta op: `journal_append` (new type for Weaver panel live updates)

### Step 2: Event buffer (`loom/weaver.py`)

- New file: `loom/weaver.py` — `WeaverEventBuffer` class
- `on_panel_event(event)` — check if event's group has a weaver, check event type filter, buffer if yes
- `on_agent_idle(cell)` — if cell is a weaver and has buffered events (or max_interval exceeded), flush
- `_flush(group)` — format digest, send to weaver terminal via `bridge.send_text()`, update `_last_push` and `_last_cursor`
- `_format_digest(events, board_summary)` — render digest text block
- `_format_heartbeat(board_summary)` — render heartbeat text block
- `_board_summary(group)` — count tasks per lane for the group
- Integrate into `EventBus`: after emitting a panel event, call `weaver_buffer.on_panel_event()`
- Integrate into `EventBus._apply()`: when activity transitions to idle, call `weaver_buffer.on_agent_idle(cell)`
- Periodic timer: every 10 seconds, check if any weaver is due for a heartbeat (max_interval exceeded) and mark it pending for next idle flush

### Step 3: Server commands (`loom/server.py`)

- `weaver_message` command handler: resolve agent, format message, send via bridge, set pending flag, emit panel event
- `ai_report(action="reply")` handler: validate pending message, emit `agent_reply` panel event, buffer for weaver
- `weaver_journal_append` command handler: validate, insert, emit delta
- `weaver_journal_read` command handler: query, return
- `weaver_update_settings` command handler: validate, save, reconfigure buffer timers
- `weaver_pause` / `weaver_resume` command handlers: toggle `WeaverSettings.paused`, emit delta
- Modify `add_agent`: support `is_weaver` flag, enforce one-per-group
- Modify `remove_agent`: clear `weaver_agent_id` when weaver is removed

### Step 4: MCP tools — agent side (`loom/mcp.py`)

- Add `loom_reply` tool definition to `TOOLS` list
- Add `reply` to `action_map` in `_dispatch_tool()`
- CLI: add `loom ai reply` subcommand to `bin/loom`

### Step 5: MCP tools — weaver side (`loom/mcp_weaver.py`)

- Remove `weaver_lanes_list`, `weaver_pipelines_list`, `weaver_task_chain` tool definitions
- Enrich `weaver_task_show` response: when task has `pipeline_root_id`, include `pipeline_chain` summary
- Add `weaver_events` tool: delegate to `panel_log.get_page()` with group filter and type filter
- Add `weaver_notifications` tool: delegate to `weaver_update_settings` command
- Add `weaver_journal` tool: delegate to `weaver_journal_append` command
- Add `weaver_journal_read` tool: delegate to `weaver_journal_read` command
- Add `weaver_agent_message` tool: delegate to `weaver_message` command

### Step 6: UI — Weaver panel (`static/js/weaver.js`, `static/style.css`)

- New file: `static/js/weaver.js` — loaded between `board.js` and `main.js`
- `renderWeaverPanel(group)` — render journal entries + settings section
- Journal entries: scrollable feed, type badges (color-coded), relative timestamps
- Settings section: collapsible, push interval dropdown, event checkboxes (mandatory ones disabled), pause/resume button
- Weaver designation: in group settings modal, agent dropdown + remove button
- Delta handling: `journal_append` delta op updates the panel in real-time
- Wire into `static/js/main.js` panel toggle (B for board, W for weaver, A for actions)

### Step 7: UI — Weaver agent indicators (`static/js/render.js`, `static/js/commands.js`)

- Weaver agents get a distinct badge in the Agents panel (e.g., a small "W" icon or crown icon)
- "Designate as weaver" option in agent right-click context menu (only if group has no weaver)
- "Remove as weaver" option in weaver agent's context menu
- Paused indicator: small pause icon on the weaver cell when digests are paused
- Disable "Add weaver" / "Designate as weaver" when the group already has one

### Step 8: UI — Pause/resume controls

- Pause/Resume button in Weaver panel settings section
- Small pause/play toggle icon on the weaver's agent cell
- Both update `WeaverSettings.paused` via WS command
- Visual feedback: when paused, the weaver cell shows a muted "paused" badge; the Weaver panel shows "Event pushes paused" banner

### Step 9: Context warning integration

- In `WeaverEventBuffer._flush()`: check weaver agent's `session_tokens_in + session_tokens_out` against a configurable threshold (default: 80% of model context)
- If exceeded, append context warning line to the digest
- The threshold is approximate — based on token counts from cost_update events
- No auto-cleanup: the weaver decides when to checkpoint. The warning is advisory.

### Step 10: Weaver action template

- Create a default weaver action: `.loom/actions/weaver/orchestrate.yaml`
- Prompt template instructs the weaver on:
  - Reading its journal on startup
  - Checking the board for current state
  - Making dispatch decisions based on priorities
  - Writing journal entries at key decision points
  - Writing checkpoints periodically
  - Responding to events in digests
- This is a starting point — users customize it for their project
- The action has no `transitions` (the weaver doesn't participate in pipelines — it manages them)

---

## What We're NOT Building (Yet)

- **Multi-group weavers** — A weaver is scoped to one group. Cross-group orchestration (e.g., a "super-weaver" that manages multiple groups) is a future consideration.
- **Automatic context cleanup** — The weaver gets warnings but decides when to checkpoint. Auto-`/clear` or auto-restart is too risky — the weaver might be mid-decision.
- **Weaver-to-weaver communication** — If two groups each have a weaver, they don't talk to each other. The human coordinates cross-group work.
- **Agent stop/kill tool** — The weaver can message agents but can't forcefully stop them. This prevents accidental work loss. The human handles stuck agents.
- **Structured decision format** — The journal is free-text. Structured fields (e.g., `{"action": "dispatch", "task": "...", "rationale": "..."}`) would enable analytics but add complexity. Free-text is sufficient for context recovery.
- **Token-based auto-checkpoint** — Beyond the advisory warning, we don't force checkpoints. The weaver action template should instruct periodic checkpointing.
- **Event replay** — `weaver_events` returns raw events but doesn't support replaying them as if they were new pushes. Replay would require re-triggering the weaver's decision loop, which is the weaver's job, not Loom's.

---

## File Changes Summary

| File | Change |
|---|---|
| `loom/state.py` | Add `weaver_agent_id` to `GroupSettings`, `pending_weaver_message` to `AgentCell`, `WeaverJournalEntry` dataclass, `get_weaver_for_group()`, `journal_append()`, `journal_read()` methods, `journal_append` delta op |
| `loom/db.py` | Add `weaver_agent_id` migration, `weaver_settings` table, `weaver_journal` table + index, CRUD methods for both |
| `loom/weaver.py` | **New file.** `WeaverEventBuffer` class — event buffering, idle-gated delivery, digest formatting, heartbeat timer, pause/resume |
| `loom/events.py` | Hook `WeaverEventBuffer.on_panel_event()` into `EventBus` panel event emission, hook `on_agent_idle()` into activity transition |
| `loom/server.py` | New commands: `weaver_message`, `weaver_journal_append`, `weaver_journal_read`, `weaver_update_settings`, `weaver_pause`, `weaver_resume`. Extend `ai_report` with `action="reply"`. Modify `add_agent`/`remove_agent` for weaver lifecycle. Initialize `WeaverEventBuffer` at startup. |
| `loom/mcp.py` | Add `loom_reply` tool definition, `reply` action mapping |
| `loom/mcp_weaver.py` | Remove 3 tools (`lanes_list`, `pipelines_list`, `task_chain`), enrich `task_show` with chain data, add 5 new tools (`events`, `notifications`, `journal`, `journal_read`, `agent_message`) |
| `bin/loom` | Add `loom ai reply` subcommand, `loom weaver journal` subcommand |
| `static/js/weaver.js` | **New file.** Weaver panel renderer — journal feed, settings section, pause/resume |
| `static/js/render.js` | Weaver badge on agent cells, paused indicator |
| `static/js/commands.js` | "Designate as weaver" / "Remove as weaver" context menu items |
| `static/js/modals.js` | Weaver section in group settings modal |
| `static/js/ws.js` | Handle `journal_append` delta op, route weaver settings updates |
| `static/js/main.js` | Weaver panel toggle (W key), load `weaver.js` |
| `static/style.css` | Weaver panel styles, journal entry cards, type badges, pause indicator, weaver agent badge |
| `webview.html` | Include `weaver.js` script tag, weaver panel container div |
| `.loom/actions/weaver/orchestrate.yaml` | Default weaver action template |
| `CLAUDE.md` | Document weaver concept, MCP tools, event push system, journal |
