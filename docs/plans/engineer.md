# Implementation Plan: Engineer (Phase 5)

**Roadmap phase**: 5 — Semi-Autonomous Orchestration
**Status**: Implemented (core: data model, event buffer, MCP tools, CLI, Agent panel, human interaction)
**Goal**: A dedicated semi-autonomous orchestrator agent per group that manages tasks, dispatches agents, reacts to events, consults with the human at key decision points, and maintains a persistent decision journal. The engineer is a first-class concept in Loom — a special agent unique to each group with its own UI panel, event subscription system, human interaction flow, and context management strategy.

---

## The Problem

Loom can dispatch tasks to agents and agents can self-organize via pipelines (`derive`, `ask`). But there is no entity that looks at the whole board, decides what to do next, responds to events, and drives the project forward. Today that role falls to the human — manually dispatching tasks, monitoring agent progress, resolving questions, and reacting to errors.

The engineer fills this gap. It's an AI agent (Claude Code, Codex, etc.) that acts as a semi-autonomous project manager for a group:

- **Consults the human** — asks questions at key decision points (priorities, design decisions, approvals) and waits for answers before proceeding
- **Dispatches work** — creates tasks, assigns actions, launches agents
- **Reacts to events** — when an agent finishes, errors, or asks a question, the engineer decides the next step
- **Maintains memory** — keeps a persistent decision journal that survives context cleanup
- **Communicates with agents** — sends messages to agents and receives replies

The engineer is NOT fully autonomous — it's a semi-independent orchestrator that analyzes tasks, talks to the human about what needs to be done, and orchestrates work around those decisions. It decides when it needs human input and when it can operate on its own. Pipelines define *what can happen*; the engineer decides *what should happen now*, with human guidance.

---

## Design Principles

1. **One engineer per group** — Each group can have at most one designated engineer agent. Multiple engineers in the same group would conflict on decisions. The UI enforces this: once a engineer exists, the create button is disabled.
2. **Push-primary events** — The engineer receives event digests via text pushed to its terminal (same mechanism as `resolve_ask`). Polling via `engineer_events` is secondary — for catching up after context cleanup or manual checks.
3. **Idle-gated delivery** — Event digests are only sent when the engineer agent is idle (activity is empty / waiting). Never interrupt a engineer mid-thought or mid-tool-call. Events buffer until the engineer goes idle.
4. **Mandatory + optional events** — Some events always appear in digests (task completed, agent error, agent reply). Others are configurable (agent started, progress updates). A max interval (default 5 minutes) ensures the engineer gets periodic heartbeats even when nothing critical happened.
5. **Journal as persistent brain** — The engineer writes structured journal entries (decisions, observations, checkpoints, plans). On context cleanup, the journal + current board state is enough to resume orchestration. The journal is per-group and stored in SQLite.
6. **Pausable by the user** — A pause/resume button in the UI suspends event pushes so the human can interact with the engineer directly without competing with automated digests.
7. **Human-in-the-loop** — The engineer is semi-autonomous: it uses `engineer_ask` to post questions to the human, which auto-pauses event delivery and shows the question in the Agent panel. The human can reply via the panel (Loom sends the answer to the terminal) or type directly into the designated engineer's Claude Code terminal. When the engineer becomes active again, the pending question auto-clears.
8. **Engineer creation via UI** — The legacy `engineer_*` entrypoint is created through the Agent panel settings flow rather than by designating an arbitrary existing agent. This ensures the `--append-system-prompt-file` flag is set on boot.

---

## Architecture

### Engineer lifecycle

```
User designates agent as engineer (UI or API)
  │
  ├── GroupSettings.engineer_agent_id = cell.id
  ├── Engineer gets engineer_* MCP tools exposed
  ├── Event buffer + timer created for this group
  │
  └── Engineer agent boots with:
        │   • --append-system-prompt: engineer role identity + custom instructions
        │   • Action prompt (user message): initial task / "check journal and resume"
        │
        ├── engineer_journal_read → recover context
        ├── engineer_board_list → current board state
        ├── engineer_events → catch up on missed events
        │
        └── Orchestration loop:
              │
              ├── Engineer dispatches tasks, makes decisions
              ├── Engineer writes journal entries
              ├── Engineer goes idle
              │     │
              │     └── Loom pushes event digest
              │           │
              │           └── Engineer wakes up, processes events
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
  ├── Engineer event buffer check:
  │     │
  │     ├── Is there a engineer for this group?
  │     ├── Is the engineer paused? → skip
  │     ├── Is this event type enabled? (mandatory events always pass)
  │     │
  │     └── Buffer the event
  │
  └── Delivery timer check:
        │
        ├── Is the engineer idle? (activity == "" or "waiting")
        │     ├── Yes → flush buffer, send digest to terminal
        │     └── No → wait, check again on next activity change
        │
        └── Has max_interval elapsed since last digest?
              ├── Yes → mark as due, deliver on next idle
              └── No → wait for timer or next event
```

### Agent reply flow

```
Engineer → engineer_agent_message(agent="fix-auth", message="Rebase on main")
  │
  ├── Loom sends formatted message to agent's terminal:
  │     ── Message from Engineer ────────────────────
  │     Rebase on main, the auth PR was merged.
  │     Reply with: loom_reply("your response")
  │     ────────────────────────────────────────────
  │
  └── Agent reads message, does work, then:
        │
        Agent → loom_reply(message="Rebased successfully")
          │
          ├── Panel event: kind="agent_reply", message="Rebased successfully"
          ├── Buffered for engineer digest (mandatory event)
          │
          └── Next digest to engineer includes:
                • agent_reply: fix-auth → "Rebased successfully"
```

### Human interaction flow

The engineer operates in two modes: **autonomous** (processing events, dispatching tasks) and **awaiting input** (question posted, events paused, waiting for human).

```
Engineer needs human guidance
  │
  ├── engineer_ask("Which tasks should I prioritize?")
  │     │
  │     ├── EngineerSettings.pending_question = "Which tasks..."
  │     ├── EngineerSettings.paused = True (events auto-pause)
  │     ├── Journal: "Asked human: Which tasks..."
  │     ├── Agent panel shows amber banner with question + reply textarea
  │     │
  │     └── Tool response to engineer:
  │           "Events paused. Call engineer_resume after the human responds."
  │
  ├── Path A: Human replies via Agent panel
  │     │
  │     ├── Types answer in textarea, clicks "Send Reply"
  │     ├── engineer_reply command:
  │     │     ├── Sends formatted "── Human Reply ──" block to engineer terminal
  │     │     ├── Clears pending_question
  │     │     ├── Sets paused = False (events resume)
  │     │     └── Journal: "Human replied: ..."
  │     │
  │     └── Engineer processes answer, calls engineer_resume (no-op, already unpaused)
  │
  └── Path B: Human types directly into Claude Code terminal
        │
        ├── Engineer receives input, starts thinking
        ├── on_agent_activity_change detects engineer became active
        │     └── Auto-clears pending_question (panel updates)
        ├── Engineer processes answer
        └── Engineer calls engineer_resume to unpause event delivery
```

### Context management

```
Engineer running, context growing
  │
  ├── Token tracking (existing session_tokens_in/out)
  │
  ├── When context > 80% threshold:
  │     Digest includes: "⚠ Context usage: 82%. Consider writing a checkpoint."
  │
  ├── Engineer writes checkpoint:
  │     engineer_journal(type="checkpoint", entry="Board: 2 IP, 4 TD...")
  │
  └── On context cleanup (/clear or session restart):
        │
        ├── System prompt persists (--append-system-prompt): engineer identity
        │   + custom instructions survive /clear automatically
        │
        └── Action prompt (user message) instructs:
              1. engineer_journal_read(tail=20) → recover decisions
              2. engineer_board_list → current state
              3. engineer_events(since=last_checkpoint) → missed events
              4. Resume orchestration
```

---

## Data Model

### GroupSettings additions

```python
@dataclass
class GroupSettings:
    # ... existing fields ...

    # Engineer
    engineer_agent_id: str = ""              # the designated engineer agent for this group
```

### New: EngineerSettings (per-group, stored in DB)

```python
@dataclass
class EngineerSettings:
    group: str                             # group name (key)
    push_interval: int = 60               # seconds between digest pushes (min: 10)
    max_interval: int = 300               # max seconds between pushes (heartbeat)
    paused: bool = False                   # user paused event pushes
    custom_instructions: str = ""          # user-defined instructions appended to engineer prompt
    pending_question: str = ""            # question awaiting human reply (non-empty = awaiting input)
    enabled_events: list[str] = field(     # optional events (mandatory always on)
        default_factory=lambda: [
            "agent_started",
            "task_dispatched",
            "task_derived",
        ]
    )
```

**`custom_instructions`**: Free-text instructions injected into the engineer's system prompt via `--append-system-prompt`. The user writes these in the Agent panel settings tab. They are concatenated with the engineer's base system prompt (from the action's `system_prompt` field or a built-in default) and passed as a single `--append-system-prompt` flag on boot.

**System prompt structure** (assembled by Loom, passed via `--append-system-prompt`):

```
You are the designated engineer — the orchestrator agent for the "{group}" group in Loom.
Your role is to manage the task board, dispatch work to agents, react to events,
and maintain a decision journal for context continuity.

{action system_prompt field, if any}

── Custom Instructions ────────────────────────
Focus on the auth and payments modules first.
Never dispatch more than 3 agents concurrently.
Always create a review task after implementation.
────────────────────────────────────────────────
```

**Why system prompt, not user message**: The system prompt survives `/clear` — when the engineer's context is cleaned, it retains its identity and custom instructions without needing them re-sent. The action's rendered `prompt` field is sent as the initial user message (the task), which is the part that changes between dispatches.

This gives the user a lightweight way to steer the engineer without editing the action YAML. The instructions persist across engineer restarts — they're part of `EngineerSettings` in SQLite, not the agent's ephemeral state.

**Mandatory events** (always included in digests, cannot be disabled):
- `task_completed` — agent finished a task
- `agent_reply` — agent replied to engineer message
- `agent_error` — unrecoverable error
- `agent_blocked` — agent needs help
- `ask_created` — agent needs human/engineer input

**Optional events** (on by default, engineer can toggle):
- `agent_started` — new agent session started
- `task_dispatched` — task was dispatched to an agent
- `task_derived` — subtask was created via derive
- `agent_progress` — agent progress update (off by default — too noisy)

### New: EngineerJournalEntry (SQLite table)

```python
@dataclass
class EngineerJournalEntry:
    id: int                                # auto-increment
    group: str                             # group name
    timestamp: float                       # time.time()
    entry_type: str                        # "decision" | "observation" | "checkpoint" | "plan"
    entry: str                             # free-text content
```

### DB schema changes

```sql
-- New columns on group_settings
engineer_agent_id TEXT NOT NULL DEFAULT ''

-- New table: engineer_settings
CREATE TABLE IF NOT EXISTS engineer_settings (
    group_name TEXT PRIMARY KEY,
    push_interval INTEGER NOT NULL DEFAULT 60,
    max_interval INTEGER NOT NULL DEFAULT 300,
    paused INTEGER NOT NULL DEFAULT 0,
    custom_instructions TEXT NOT NULL DEFAULT '',
    pending_question TEXT NOT NULL DEFAULT '',
    enabled_events TEXT NOT NULL DEFAULT '["agent_started","task_dispatched","task_derived"]'
);

-- New table: engineer_journal
CREATE TABLE IF NOT EXISTS engineer_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name TEXT NOT NULL,
    timestamp REAL NOT NULL,
    entry_type TEXT NOT NULL,
    entry TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_engineer_journal_group
    ON engineer_journal(group_name, id DESC);
```

### AgentCell additions

```python
@dataclass
class AgentCell:
    # ... existing fields ...

    # Engineer message tracking
    pending_engineer_message: bool = False   # agent has an unread message from engineer
```

This is ephemeral (not persisted) — used to validate that `loom_reply` is only available when the agent has received a engineer message.

---

## Prompt Architecture

The engineer's prompt is split into two layers, using Claude Code's `--append-system-prompt` flag:

### System prompt (persistent across `/clear`)

Assembled by Loom from three parts:

1. **Base engineer identity** — built-in text that defines the engineer role, available tools, and behavioral guidelines (e.g. "write journal entries at decision points", "write checkpoints periodically")
2. **Action `system_prompt` field** (optional) — if the engineer action YAML has a `system_prompt` field, it's included. This lets the action template contribute project-level system instructions.
3. **Custom instructions** — user-written text from `EngineerSettings.custom_instructions`

These are concatenated and passed as `--append-system-prompt` on boot. Because `--append-system-prompt` appends to Claude Code's built-in system prompt, the engineer retains all standard capabilities (file editing, bash, tools) while gaining its orchestrator identity.

**Key benefit**: The system prompt survives `/clear`. When the engineer's context fills up and the user (or engineer) clears it, the engineer still knows *who it is* and *what the custom rules are*. Only the conversation history (tool call results, event digests) is lost — recoverable via journal + board state.

### User prompt (the initial task message)

The action's rendered `prompt` field is sent as text to the engineer's terminal — a regular user message. This is the *task*: what the engineer should do right now.

**First dispatch** (fresh engineer):
```
You are starting a new orchestration session.
Check the board and begin managing tasks for this group.
```

**Re-dispatch** (after context cleanup or restart):
```
You are resuming an orchestration session.
Read your journal to recover context, then check the board and recent events.
```

The action template controls this via `{{ loom.context.is_clean }}` — the same mechanism regular actions use.

### Implementation: `inject_system_prompt` for engineer agents

The current `ClaudeCodeAdapter.inject_system_prompt()` writes to `.claude/instructions.md`, which is loaded as user context, not as a true system prompt. For the engineer, we need the actual `--append-system-prompt` flag.

**Approach**: When dispatching a engineer agent, Loom appends `--append-system-prompt-file <path>` to the boot command instead of using `inject_system_prompt()`. The file is written to a stable path (e.g. `.loom/engineer-system-prompt-{group}.md`) so it persists across restarts.

```python
# In _create_agent_with_config() for engineer agents:
if is_engineer:
    system_prompt_text = _build_engineer_system_prompt(group, engineer_settings)
    prompt_path = os.path.join(git_root, ".loom", f"engineer-system-prompt-{group_slug}.md")
    Path(prompt_path).write_text(system_prompt_text)
    cell.command += f" --append-system-prompt-file {shlex.quote(prompt_path)}"
```

This is cleaner than the `instructions.md` approach because:
- It uses the actual system prompt mechanism (survives `/clear`)
- The file is scoped to the engineer (doesn't pollute `.claude/instructions.md` which other agents may use)
- It's in `.loom/` which is already gitignored

For **Codex**, the adapter would use its equivalent mechanism (if available), or fall back to the instructions-file approach.

---

## MCP Tools

### Agent-side: new tool

#### `loom_reply`

Reply to a message from the engineer. Only works when the agent has a pending engineer message.

```json
{
    "name": "loom_reply",
    "description": "Reply to a message from the engineer (orchestrator agent). The reply is delivered to the engineer in its next event digest.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Your reply to the engineer."
            }
        },
        "required": ["message"]
    }
}
```

**Server handling**: `ai_report(action="reply")` — creates panel event with kind `agent_reply`, clears `cell.pending_engineer_message`.

### Engineer-side: final tool list

Tools are served from the same `/mcp` endpoint. The `engineer_` prefix provides namespace separation. Access is authorized by `X-Loom-Cell-Id`: only the designated engineer session for a group can list or call `engineer_*` tools. Regular agents only see the `loom_*` surface.

#### Read tools

##### `engineer_board_list` (keep)

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

##### `engineer_task_show` (enriched — absorbs `task_chain`)

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

##### `engineer_agents_list` (keep)

List all active agents with their name, slug, status, group, current task, and activity detail.

##### `engineer_actions_list` (keep)

List available actions (project and user scope) with name, description, variables, and scope.

```json
{
    "properties": {
        "group": {"type": "string", "description": "Group name to resolve project-scoped actions."}
    }
}
```

##### `engineer_action_show` (keep)

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

##### `engineer_task_create` (keep)

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

##### `engineer_task_edit` (keep)

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

##### `engineer_task_move` (keep)

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

##### `engineer_task_dispatch` (keep)

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

##### `engineer_batch_dispatch`

Dispatch an ordered wave of tasks with a concurrency cap. Entries that cannot start immediately are kept in a persistent auto-dispatch queue, so Loom can continue launching the next eligible task as worker slots open, even after restart. When entries share an `agent_group`, the first dispatch binds that group to one agent and later queued entries follow that same worker in order.

##### `engineer_task_resolve` (keep)

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

##### `engineer_events` (new)

Poll for recent events. Primary use: catching up after context cleanup. Secondary use: manually checking what happened. Returns events from `PanelEventLog` filtered by the engineer's group.

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

##### `engineer_notifications` (new)

Configure event push settings. Sets which optional events the engineer wants in its digests and the push interval. Mandatory events are always included.

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

##### `engineer_journal` (new)

Append an entry to the engineer's persistent decision journal. Use this to record decisions, observations, and periodic checkpoints. The journal survives context cleanup — read it back with `engineer_journal_read` to resume orchestration.

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

##### `engineer_journal_read` (new)

Read recent journal entries. Use after context cleanup or startup to recover the engineer's decision history and resume orchestration.

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

#### Interaction tools

##### `engineer_agent_message` (new)

Send a message to any agent's terminal. The agent can reply via `loom_reply`, which appears in the engineer's next event digest. Use for: redirecting agents, providing context, answering questions without the full ask/resolve flow.

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
── Message from Engineer ────────────────────────
Rebase on main, the auth PR was merged.

Reply with: loom_reply("your response")
────────────────────────────────────────────────
```

##### `engineer_ask` (new)

Ask the human a question. Posts the question to the Agent panel and auto-pauses event delivery. The human can reply via the panel (Loom sends the answer to the terminal) or type directly into the designated engineer's Claude Code terminal.

```json
{
    "properties": {
        "question": {
            "type": "string",
            "description": "The question for the human."
        }
    },
    "required": ["question"]
}
```

**Tool response:**
```
Question posted to the Agent panel. Event pushes have been paused.
The human will see your question and reply via the panel or directly
in this terminal.

After the human responds, call engineer_resume to unpause event delivery.
```

**Behavior:**
1. Sets `EngineerSettings.pending_question` to the question text
2. Sets `EngineerSettings.paused = True` (auto-pause events)
3. Logs to journal: "Asked human: {question}"
4. Agent panel shows amber banner with the question + reply textarea
5. The engineer goes idle and waits

**Human reply paths:**
- **Via panel**: Human types answer, clicks "Send Reply" → `engineer_reply` command sends formatted answer to terminal, clears `pending_question`, unpauses events, logs to journal
- **Via terminal**: Human types directly into Claude Code → engineer starts thinking → `on_agent_activity_change` auto-clears `pending_question` → engineer calls `engineer_resume` to unpause

### Tools removed

| Tool | Reason |
|---|---|
| `engineer_lanes_list` | Lanes are near-static (Backlog/To Do/In Progress/Done). Already visible in `board_list` output. |
| `engineer_pipelines_list` | Static analysis of action transitions. Useful for humans designing pipelines, not for active orchestration. Derivable from `actions_list`. |
| `engineer_task_chain` | Merged into `engineer_task_show` — pipeline chain is auto-included when the task has a `pipeline_root_id`. |

### Tool count summary

| Category | Tools | Count |
|---|---|---|
| Read | `board_list`, `task_show`, `agents_list`, `actions_list`, `action_show` | 5 |
| Write | `task_create`, `task_edit`, `task_move`, `task_dispatch`, `task_resolve` | 5 |
| Events | `events`, `notifications` | 2 |
| Context | `journal`, `journal_read` | 2 |
| Interaction | `agent_message`, `ask` | 2 |
| **Total** | | **16** |

Plus 1 new agent-side tool: `loom_reply`.

---

## CLI Commands

### `loom ai reply`

```
loom ai reply <message>

Arguments:
  message              Reply text to send back to the engineer
```

**Behavior:**
- Auto-detects calling agent via `$LOOM_CELL_ID`
- Calls `ai_report(action="reply", message=...)`
- Errors if agent has no pending engineer message

### `loom engineer journal`

```
loom engineer journal [--group GROUP] [--tail N] [--type TYPE]

Flags:
  -g, --group GROUP    Group name (default: auto-detect from agent)
  -n, --tail N         Number of entries to show (default: 20)
  -t, --type TYPE      Filter by entry type (decision/observation/checkpoint/plan)
```

Reads the engineer journal directly from SQLite (works offline like other `loom` read commands).

---

## Event Push System

### EngineerEventBuffer (new class in `loom/engineer.py`)

Manages per-group event buffering and digest delivery.

```python
class EngineerEventBuffer:
    def __init__(self, state, bridge, panel_log):
        self._state = state
        self._bridge = bridge        # for send_text to engineer terminal
        self._panel_log = panel_log
        self._buffers: dict[str, list[dict]] = {}   # group → buffered events
        self._last_push: dict[str, float] = {}       # group → timestamp of last digest
        self._last_cursor: dict[str, int] = {}        # group → last event ID sent
        self._timer: asyncio.TimerHandle | None = None

    def on_panel_event(self, event: dict):
        """Called when a panel event is emitted. Buffer if a engineer cares."""

    def on_agent_idle(self, cell):
        """Called when an agent goes idle. Check if it's a engineer and flush."""

    async def _flush(self, group: str):
        """Format and send buffered events as a digest to the engineer's terminal."""

    def _format_digest(self, events: list[dict], board_summary: str) -> str:
        """Format events into a readable digest string."""

    def _format_heartbeat(self, board_summary: str) -> str:
        """Format a no-events heartbeat with board status."""
```

### Delivery rules

1. **Idle-gated**: Digests are only sent when the engineer's `activity` is `""` (empty) or `"waiting"`. The `on_agent_idle` hook fires when `EventBus._apply()` transitions a cell to idle.
2. **Interval-batched**: Events accumulate in the buffer. A timer fires every `push_interval` seconds. If the engineer is idle and events are buffered, flush. If not idle, the flush defers to the next `on_agent_idle` call.
3. **Max interval heartbeat**: If `max_interval` seconds pass since the last digest (even with no events), a heartbeat is sent on the next idle transition. The heartbeat includes board status but no events.
4. **Pause/resume**: When `EngineerSettings.paused` is `True`, events still buffer but no digests are sent. On resume, buffered events flush on next idle.
5. **Mandatory events**: `task_completed`, `agent_reply`, `agent_error`, `agent_blocked`, `ask_created` — always buffered regardless of `enabled_events` filter.
6. **Optional events**: `agent_started`, `task_dispatched`, `task_derived`, `agent_progress` — only buffered if in `enabled_events`.
7. **Group scoping**: Events are matched to the engineer's group via the agent's group field. Cross-group events are not delivered.

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

### Engineer Panel

A new panel in the taskbar, between Agents and Events. Uses a tab bar at the top — same pattern as the Agents panel's `Templates` / `History` tabs.

**Tabs**: `Journal` | `Settings`

The panel header shows the group name and the engineer agent's status. A small pause/resume toggle sits in the header bar, always visible regardless of active tab.

```
┌─────────────────────────────────────────────┐
│  Engineer — my-project        [⏸ Pause]       │
│  [Journal]  [Settings]                      │
│─────────────────────────────────────────────│
│  ...tab content...                          │
└─────────────────────────────────────────────┘
```

#### Journal tab (default)

Scrollable feed of journal entries, most recent at top:

```
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
```

- Entry type shown as a colored badge: `decision` (blue), `observation` (gray), `checkpoint` (green), `plan` (yellow)
- Timestamps shown as relative ("2m ago", "1h ago")
- Entries are scrollable with "Load more" at the bottom (paginated from SQLite)

#### Settings tab

All engineer configuration in one place. Sections separated by subtle dividers.

```
│  ─── Agent ───────────────────────────────  │
│                                             │
│  Engineer agent: fix-auth-bug  [Designate]    │
│                                             │
│  ─── Custom Instructions ─────────────────  │
│                                             │
│  ┌────────────────────────────────────────┐ │
│  │ Focus on auth and payments modules     │ │
│  │ first. Never dispatch more than 3      │ │
│  │ agents concurrently. Always create a   │ │
│  │ review task after implementation.      │ │
│  │                                        │ │
│  └────────────────────────────────────────┘ │
│                              [Save]         │
│                                             │
│  ─── Notifications ───────────────────────  │
│                                             │
│  Push interval: [60s ▾]                     │
│  Max interval:  [300s ▾]                    │
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
```

- **Custom Instructions**: Auto-growing textarea. Content is appended to the engineer's prompt on dispatch (and on re-dispatch after context cleanup). Saved to `EngineerSettings.custom_instructions`. The Save button only appears when the content has changed (dirty state).
- **Notifications**: Push interval and max interval dropdowns, event type checkboxes. Mandatory events are checked and disabled (grayed out). Changes apply immediately (no save button — sent via WS on change).

#### Engineer designation

- In the group settings modal (existing), a new "Engineer" section
- "Designate engineer" dropdown lists agents in the group (only agents, not terminals)
- Once designated, the dropdown is replaced with the agent name + "Remove engineer" button
- If a engineer already exists for the group, the "Add engineer" option in the main UI is disabled

#### Pause/Resume button

- Shown in the Agent panel settings section
- Also shown as a small toggle icon on the engineer's agent cell in the Agents panel
- When paused: event buffer still accumulates, but no digests are sent
- When resumed: if events are buffered, they flush on next idle check
- Visual indicator: the engineer agent cell shows a "paused" badge when paused

---

## Server Commands

### New commands

#### `engineer_message`

Send a message from the engineer to an agent's terminal.

**Payload:**
```json
{
    "cmd": "engineer_message",
    "agent_id": "abc123",
    "message": "Rebase on main, the auth PR was merged."
}
```

**Behavior:**
1. Resolve agent by ID/slug
2. Validate agent exists and is running
3. Format message with engineer header/footer
4. Send to agent's terminal via `bridge.send_text()`
5. Set `cell.pending_engineer_message = True`
6. Emit panel event: kind `engineer_message`, message preview
7. Return `{"type": "ok"}`

#### `ai_report` — new action: `reply`

Reply from an agent to the engineer.

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
2. Validate `cell.pending_engineer_message` is True (else error: "No pending engineer message")
3. Clear `cell.pending_engineer_message`
4. Find the engineer for this agent's group
5. Emit panel event: kind `agent_reply`, with agent name and message
6. Buffer event for engineer digest delivery
7. Return `{"type": "ok"}`

#### `engineer_journal_append`

Append an entry to the engineer's journal.

**Payload:**
```json
{
    "cmd": "engineer_journal_append",
    "group": "my-project",
    "entry_type": "decision",
    "entry": "Dispatched auth flow task..."
}
```

**Behavior:**
1. Validate entry_type is one of: decision, observation, checkpoint, plan
2. Insert into `engineer_journal` table
3. Emit delta: `journal_append` (new delta op type for the Agent panel)
4. Return `{"type": "ok", "id": entry_id}`

#### `engineer_journal_read`

Read recent journal entries.

**Payload:**
```json
{
    "cmd": "engineer_journal_read",
    "group": "my-project",
    "tail": 20,
    "entry_type": ""
}
```

**Behavior:**
1. Query `engineer_journal` table: `WHERE group_name = ? ORDER BY id DESC LIMIT ?`
2. Optional type filter: `AND entry_type = ?`
3. Return `{"type": "journal", "entries": [...]}`

#### `engineer_update_settings`

Update engineer settings. Supports partial updates — only the fields provided are changed.

**Payload:**
```json
{
    "cmd": "engineer_update_settings",
    "group": "my-project",
    "push_interval": 60,
    "max_interval": 300,
    "custom_instructions": "Focus on auth and payments modules first.",
    "enabled_events": ["agent_started", "task_dispatched", "task_derived"]
}
```

#### `engineer_ask`

Post a question from the engineer to the human. Auto-pauses events.

**Payload:**
```json
{
    "cmd": "engineer_ask",
    "group": "my-project",
    "question": "Which tasks should I prioritize?"
}
```

**Behavior:**
1. Set `EngineerSettings.pending_question = question`
2. Set `EngineerSettings.paused = True`
3. Append journal entry: "Asked human: {question}"
4. Emit delta (`engineer_settings_update`) so the panel shows the question
5. Return `{"type": "ok"}`

#### `engineer_reply`

Human replies to the engineer's pending question.

**Payload:**
```json
{
    "cmd": "engineer_reply",
    "group": "my-project",
    "answer": "Focus on the auth module first."
}
```

**Behavior:**
1. Validate engineer exists and is running
2. Format answer as `── Human Reply ──` block
3. Send to engineer's terminal via `bridge.send_text()`
4. Clear `pending_question`, set `paused = False`
5. Append journal entry: "Human replied: {answer}"
6. Return `{"type": "ok"}`

#### `engineer_pause` / `engineer_resume`

Toggle event push delivery. `engineer_resume` also clears `pending_question`.

**Payload:**
```json
{
    "cmd": "engineer_pause",
    "group": "my-project"
}
```

### Modified commands

#### `add_agent` — engineer creation

When `add_agent` receives `is_engineer: true`:
1. Validates one-per-group (returns error if group already has a engineer)
2. Builds engineer system prompt (base + action system_prompt + custom instructions)
3. Writes to `.loom/engineer-system-prompt-{group}.md`
4. Appends `--append-system-prompt-file <path>` to the boot command
5. Creates agent, sets `GroupSettings.engineer_agent_id`
6. Default name: "Engineer" (user can override)

The engineer must be created as a engineer from the start — designating an existing agent is not supported because the `--append-system-prompt-file` flag must be set on boot.

#### `remove_agent` — engineer cleanup

When the engineer agent is removed, clear `GroupSettings.engineer_agent_id`. The journal persists (it belongs to the group, not the agent). A new engineer can be designated and will inherit the journal.

---

## Implementation Steps

### Step 1: Data model (`loom/state.py`, `loom/db.py`) ✅

- Add `engineer_agent_id` to `GroupSettings` dataclass
- Add `EngineerSettings` dataclass with `pending_question` field
- Add `pending_engineer_message` ephemeral field to `AgentCell`
- DB: add `engineer_agent_id` column to `group_settings` via ALTER TABLE migration
- DB: create `engineer_settings` table (with `pending_question` column)
- DB: create `engineer_journal` table with index
- DB: CRUD methods for both tables, `pending_question` migration for existing DBs
- `MatrixState`: `get_engineer_for_group(group)` helper, `journal_append()`, `journal_read()`, `update_engineer_settings()` methods
- Delta ops: `journal_append`, `engineer_settings_update`
- Engineer cleanup on agent/group removal

### Step 2: Event buffer (`loom/engineer.py`) ✅

- New file: `loom/engineer.py` — `EngineerEventBuffer` class
- `on_panel_event(event)` — check if event's group has a engineer, check event type filter, buffer if yes
- `on_agent_activity_change(cell)` — if engineer goes idle → flush; if engineer becomes active → auto-clear `pending_question`
- `_flush(group)` — format digest, send to engineer terminal via `bridge.send_text()`
- `_format_digest()` / `_format_heartbeat()` — render text blocks
- `_context_warning()` — advisory message when token count exceeds threshold
- Periodic timer: every 10 seconds, check heartbeat due
- `build_engineer_system_prompt(group, settings, action_sp)` — assembles base identity + action system_prompt + custom instructions
- Integrated into `EventBus` via `_engineer_buffer` attribute and `PanelEventLog.on_event` callback

### Step 3: Server commands (`loom/server.py`) ✅

- `engineer_message` command: resolve agent, format message, send via bridge, set `pending_engineer_message`, emit panel event
- `ai_report(action="reply")` handler: validate pending message, emit `agent_reply` panel event
- `engineer_journal_append` / `engineer_journal_read` command handlers
- `engineer_update_settings` command handler (including `custom_instructions`, `pending_question`)
- `engineer_ask` command: set `pending_question`, auto-pause, log to journal
- `engineer_reply` command: send formatted answer to engineer terminal, clear question, unpause, log to journal
- `engineer_pause` / `engineer_resume` commands (`resume` also clears `pending_question`)
- `add_agent` with `is_engineer: true`: enforce one-per-group, build system prompt file, append `--append-system-prompt-file` to boot command, set `engineer_agent_id`
- `remove_agent`: clear `engineer_agent_id` when engineer is removed
- `EngineerEventBuffer` initialization and wiring at startup

### Step 4: MCP tools — agent side (`loom/mcp.py`) ✅

- Add `loom_reply` tool definition to `TOOLS` list
- Add `reply` to `action_map` in `_dispatch_tool()`
- CLI: `loom ai reply` subcommand in `bin/loom`

### Step 5: MCP tools — engineer side (`loom/mcp_engineer.py`) ✅

- Remove `engineer_lanes_list`, `engineer_pipelines_list`, `engineer_task_chain` tool definitions
- Enrich `engineer_task_show` response with auto-included `pipeline_chain`
- Add `engineer_events`, `engineer_notifications`, `engineer_journal`, `engineer_journal_read`, `engineer_agent_message` tools
- Add `engineer_ask` tool: delegates to `engineer_ask` command, returns instructional response telling engineer to call `engineer_resume` after human responds

### Step 6: UI — Agent panel (`static/js/agent_panel.js`, `static/style.css`) ✅

- New file: `static/js/agent_panel.js` — loaded between `events.js` and `main.js`
- Tabbed layout: `Journal` | `Settings` tabs
- Panel header: group name (follows `_currentGroup()` — the currently selected group), pause/resume toggle
- **Journal tab** (default):
  - Pending question banner (amber): shown when `pending_question` is set, with reply textarea and "Send Reply" button
  - Journal entries: scrollable feed, type badges (decision=blue, observation=gray, checkpoint=green, plan=yellow), relative timestamps
- **Settings tab**: three sections:
  - Agent: shows engineer name + status, or "+ Create Engineer" button (sends `add_agent` with `is_engineer: true`)
  - Custom Instructions: auto-growing textarea, dirty-state Save button
  - Notifications: push/max interval dropdowns, event type checkboxes (mandatory ones disabled)
- Delta handling: `journal_append` and `engineer_settings_update` ops in `ws.js`
- Wired into `main.js` panel toggle and `render.js` re-render cycle

### Step 7: UI — Engineer agent indicators (`static/js/render.js`, `static/js/commands.js`)

- Engineer agents get a distinct badge in the Agents panel (e.g., a small "W" icon)
- Paused indicator: small pause icon on the engineer cell when digests are paused
- Context menu: "Remove as engineer" option on engineer agent

### Step 8: UI — Pause/resume controls ✅

- Pause/Resume button in Agent panel header (always visible across tabs)
- Updates `EngineerSettings.paused` via WS command
- Visual feedback: button text toggles between "Pause" and "Resume", styled differently when paused

### Step 9: Context warning integration ✅

- In `EngineerEventBuffer._context_warning()`: check engineer agent's `session_tokens_in + session_tokens_out` against threshold (~800K tokens)
- If exceeded, append warning line to the digest
- No auto-cleanup: the engineer decides when to checkpoint

### Step 10: Engineer action template and base system prompt ✅ (base prompt only)

- **Base system prompt** (built into `loom/engineer.py`): hardcoded text defining the engineer role, available tools, behavioral guidelines (journal usage, checkpoint cadence, event response, human interaction via `engineer_ask`). Assembled via `build_engineer_system_prompt()` and written to `.loom/engineer-system-prompt-{group}.md`, passed via `--append-system-prompt-file`.
- **Default engineer action** (`.loom/actions/engineer/orchestrate.yaml`): not yet created — users can create their own action or rely on the base system prompt alone.

### Step 11: Human interaction flow ✅

- `engineer_ask` MCP tool: posts question to panel, auto-pauses events, returns instructions to call `engineer_resume` after human responds
- `engineer_reply` server command: sends formatted answer to engineer terminal, clears question, unpauses events, logs exchange to journal
- `engineer_ask` server command: sets `pending_question` + `paused`, logs to journal
- Auto-clear `pending_question` on engineer activity change (human typed directly into terminal)
- Panel UI: amber banner with question + reply textarea + "Send Reply" button
- Two reply paths: panel (Loom-mediated) or direct terminal input (engineer self-resumes)

---

## Remaining Work

- **Step 7: Engineer agent indicators** — visual badge on engineer agent cell, context menu options
- **Default engineer action template** — `.loom/actions/engineer/orchestrate.yaml` with `is_clean` branching
- **CLAUDE.md documentation** — document engineer concept, MCP tools, event push system, journal
- **Journal pagination** — "Load more" button in journal tab (SQLite pagination exists, UI not wired)

---

## What We're NOT Building (Yet)

- **Multi-group engineers** — A engineer is scoped to one group. Cross-group orchestration (e.g., a "super-engineer" that manages multiple groups) is a future consideration.
- **Automatic context cleanup** — The engineer gets warnings but decides when to checkpoint. Auto-`/clear` or auto-restart is too risky — the engineer might be mid-decision.
- **Engineer-to-engineer communication** — If two groups each have a engineer, they don't talk to each other. The human coordinates cross-group work.
- **Agent stop/kill tool** — The engineer can message agents but can't forcefully stop them. This prevents accidental work loss. The human handles stuck agents.
- **Structured decision format** — The journal is free-text. Structured fields (e.g., `{"action": "dispatch", "task": "...", "rationale": "..."}`) would enable analytics but add complexity. Free-text is sufficient for context recovery.
- **Token-based auto-checkpoint** — Beyond the advisory warning, we don't force checkpoints. The engineer action template should instruct periodic checkpointing.
- **Event replay** — `engineer_events` returns raw events but doesn't support replaying them as if they were new pushes. Replay would require re-triggering the engineer's decision loop, which is the engineer's job, not Loom's.
- **Designating existing agents as engineer** — The engineer must be created as a engineer (via "+ Create Engineer" or `add_agent` with `is_engineer: true`) because `--append-system-prompt-file` must be set on boot. Existing agents can't be retroactively designated.

---

## File Changes Summary

| File | Change |
|---|---|
| `loom/state.py` | `EngineerSettings` dataclass (with `pending_question`), `ENGINEER_MANDATORY_EVENTS`, `engineer_agent_id` on `GroupSettings`, `pending_engineer_message` on `AgentCell`, engineer helpers on `MatrixState`, engineer settings in snapshot, cleanup on removal |
| `loom/db.py` | `engineer_settings` table (with `pending_question`), `engineer_journal` table + index, `engineer_agent_id` migration, `pending_question` migration, CRUD methods |
| `loom/engineer.py` | **New file.** `EngineerEventBuffer` (event buffering, idle-gated delivery, digest/heartbeat formatting, auto-clear `pending_question` on activity, periodic timer). Base system prompt text. `build_engineer_system_prompt()` assembler. |
| `loom/events.py` | `on_event` callback on `PanelEventLog`, `_engineer_buffer` on `EventBus` with activity-change hook |
| `loom/server.py` | New commands: `engineer_message`, `engineer_ask`, `engineer_reply`, `engineer_journal_append/read`, `engineer_update_settings`, `engineer_pause/resume`. `ai_report(action="reply")`. `add_agent` with `is_engineer` (system prompt file + `--append-system-prompt-file`). `remove_agent` engineer cleanup. `EngineerEventBuffer` init. |
| `loom/mcp.py` | `loom_reply` tool definition + action mapping |
| `loom/mcp_engineer.py` | Remove 3 tools, enrich `task_show` with chain, add 6 tools (`events`, `notifications`, `journal`, `journal_read`, `agent_message`, `ask`) |
| `bin/loom` | `loom ai reply` subcommand, `loom engineer journal` subcommand |
| `static/js/agent_panel.js` | **New file.** Tabbed panel (Journal/Settings), pending question banner with reply box, journal feed, settings with create button + custom instructions + notifications, `engineerCreate()` / `engineerReply()` / `engineerTogglePause()` |
| `static/js/render.js` | Agent panel re-render on delta |
| `static/js/ws.js` | `journal_append` + `engineer_settings_update` delta op handlers |
| `static/js/main.js` | `panel-agent` in panel IDs, render hooks |
| `static/style.css` | Agent panel styles: header, tabs, journal entries with type badges, ask banner (amber), reply textarea/button, settings sections, create button, event checkboxes |
| `webview.html` | `panel-agent` div, taskbar button (⚖ Agent), `agent_panel.js` script tag |
| `Makefile` | Add `loom/mcp_engineer.py` and `loom/engineer.py` to install target |
