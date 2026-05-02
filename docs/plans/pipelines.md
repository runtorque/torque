# Implementation Plan: Pipelines (Phase 4b)

**Roadmap phase**: 4 — Workflow Automation
**Status**: Implemented (core pipeline mechanics, action transitions, pipeline visualization, board chain indicators)
**Goal**: Multi-step agent workflows through task derivation. An agent completes its task and creates a new derived task for the next agent. The chain is always forward-moving — no cycles, no retries on the same task. The "pipeline" is emergent from the parent-child task chain, not a declared DAG.

---

## The Problem

Torque can dispatch a single task to a single agent. But real workflows have multiple stages: implement → test → review → merge. Today, the only way to chain stages is a shell script calling `torque task dispatch --wait` sequentially. This works but has drawbacks:

- The shell script is the orchestrator — if it dies, the pipeline dies
- Agents can't make runtime decisions about what comes next
- There's no audit trail linking the stages together
- There's no way for an agent to escalate to a human mid-pipeline

The goal is to let agents themselves drive the pipeline forward by deriving new tasks, while keeping the model simple enough that there are no cycle bugs, no state machines, and no pipeline engine to maintain.

## Design Principles

1. **Tasks only move forward** — An agent can mark its task as done and derive a new task. It cannot reopen its own task or send work backward. If a reviewer rejects work, it creates a *new* forward task ("fix this"), not a cycle back to the original.
2. **Pipelines are emergent** — There is no `Pipeline` object. A pipeline is just a chain of tasks linked by `parent_task_id`. The chain is visible in the board UI and queryable via CLI, but Torque doesn't enforce a pipeline schema.
3. **Actions declare valid transitions** — Each action lists which actions it can derive into via a `transitions` field. The agent picks from those options; the server rejects anything not on the list. This makes pipeline structure visible and enforceable without a separate pipeline config object. The pipeline graph is emergent from the union of all actions' transition lists.
4. **Human-in-the-loop is a first-class concept** — `torque ai ask` creates a derived task in Backlog instead of dispatching it. A human reviews, edits, and manually dispatches. This is the HITL gate.
5. **Depth limits prevent runaway chains** — A configurable `max_pipeline_depth` (global setting, overridable per action) caps how deep a chain can go. When exceeded, the task gets a `needs_attention` flag instead of deriving.
6. **The entire chain shares one worktree** — When an agent derives a new task, the new agent inherits the parent agent's worktree. This is essential: a review agent must see the code the implement agent wrote. The worktree is created for the root task and reused by every derived task in the chain. No new worktree is created on derive.

---

## Architecture

### Task derivation flow

```
Agent A (impl) finishes task T1
  │
  ├── torque ai done                          ← marks T1 as Done
  ├── torque ai derive "Review the login      ← creates T2 in dispatch lane
  │     implementation" --action review         T2.parent_task_id = T1.id
  │                                            T2.pipeline_depth = T1.pipeline_depth + 1
  │                                            T2.pipeline_root_id = T1.pipeline_root_id
  │
  └── Agent B (review) boots with T2
        │
        ├── (happy path) torque ai done       ← T2 Done, chain complete
        │
        └── (rejection) torque ai derive      ← creates T3
              "Fix auth validation"            T3.parent_task_id = T2.id
              --action fix                      T3.pipeline_depth = 2
              Agent C (fix) boots with T3
                │
                └── torque ai derive          ← creates T4
                      "Re-review auth"
                      --action review
                      ...chain continues
```

### Human-in-the-loop flow

```
Agent A finishes task T1
  │
  ├── torque ai ask "Impl is done.           ← creates T2 in Backlog (not dispatched)
  │     Should we deploy or add               T2.parent_task_id = T1.id
  │     more tests?"                          T2 has label: "human"
  │
  └── Human sees T2 in Backlog
        ├── edits task description
        ├── picks an action
        └── dispatches manually             ← T2 moves to In Progress, agent launches
```

### Depth limit enforcement

```
Agent tries: torque ai derive "..." --action review
  │
  Server checks: task.pipeline_depth + 1 > max_pipeline_depth?
  │
  ├── No  → create derived task, dispatch agent
  └── Yes → refuse derivation, set needs_attention on current task,
            return warning: "Pipeline depth limit (N) reached"
```

### Data flow

```
torque ai derive "Review login" --action review
  │
  ├──► POST /api/cmd  {"cmd": "ai_report", "action": "derive", ...}
  │
  ├──► server.py ai_report(action="derive")
  │      ├── validate depth limit
  │      ├── create BoardTask (parent_task_id, pipeline_depth, pipeline_root_id)
  │      ├── call dispatch_task() to launch agent + send prompt
  │      └── broadcast delta (task_upsert × 2: parent Done + child created)
  │
  └──► UI re-renders: new task card with chain indicator
```

### Worktree inheritance

A pipeline's entire task chain runs in a single worktree. This is critical — if the implement agent writes code in `torque/impl-login-a3b2/`, the review agent must see those files, not start from a clean branch.

```
Root task T1 (implement)
  │
  ├── Agent A spawns in worktree: .torque/worktrees/impl-login-a3b2/
  │   branch: torque/impl-login-a3b2
  │   Agent A writes code, commits, calls `torque ai derive -t review`
  │
  ├── derive creates T2 (review)
  │   Agent B spawns in THE SAME worktree: .torque/worktrees/impl-login-a3b2/
  │   Agent B sees all of Agent A's changes
  │   Agent B calls `torque ai derive -t fix`
  │
  └── derive creates T3 (fix)
      Agent C spawns in THE SAME worktree: .torque/worktrees/impl-login-a3b2/
      Agent C sees Agent A's code + Agent B's review comments (if committed)
```

**How it works:**

1. The root task's agent creates a worktree normally (via group settings or action `worktree: true`). The worktree path and branch are stored on the `AgentCell`.
2. When `derive` creates a new agent, it copies the parent agent's `worktree_path`, `worktree_branch`, and `git_root` fields to the new agent. It does NOT call `WorktreeManager.create()` — no new worktree, no new branch.
3. The new agent's working directory is set to the inherited worktree path. The boot command runs inside it.
4. The worktree's branch badge in the UI now shows which agent is currently active in it (the latest in the chain).
5. When the chain completes (final task Done, no derivation), the worktree belongs to the last agent. It can be merged, checkpointed, or removed via the normal worktree operations.

**What about the old agent's session?** When `derive` marks the parent task as Done, the parent agent is stopped (its terminal session ends). This frees the worktree directory — there's no risk of two agents writing to the same worktree simultaneously.

**What about `ask` (HITL)?** When `ask` creates a Backlog task, the parent agent stops but the worktree persists. When the human dispatches the task later, the new agent inherits the worktree from the chain's last active agent (resolved via `parent_task_id` → `agent_id` → worktree fields).

**What about checkpoints?** Auto-checkpoint on agent stop (if enabled) still fires for each agent in the chain. This means the worktree accumulates checkpoints from every stage — a full audit trail of what each agent did.

---

## Data Model

### BoardTask additions

```python
@dataclass
class BoardTask:
    # ... existing fields ...

    # Pipeline fields (new)
    parent_task_id: str = ""       # task this was derived from (empty for root tasks)
    pipeline_depth: int = 0        # 0 for root, auto-incremented from parent
    pipeline_root_id: str = ""     # ID of the chain's root task (self.id for root tasks)
```

**Invariants:**
- `parent_task_id` is immutable once set — you cannot reparent a derived task
- `pipeline_depth` = `parent.pipeline_depth + 1`, computed on creation
- `pipeline_root_id` = `parent.pipeline_root_id` (inherited from parent), or `self.id` for root tasks
- A task with `parent_task_id == ""` is a root task (depth 0)

### GlobalSettings addition

```python
@dataclass
class GlobalSettings:
    # ... existing fields ...

    # Pipeline
    max_pipeline_depth: int = 10   # 0 = unlimited
```

### Action additions

```yaml
# .torque/actions/implement.yaml
name: implement
# ... existing fields ...

# Pipeline fields (new)
max_depth: 5          # override global max_pipeline_depth for chains using this action

transitions:
  - action: review
    when: "Implementation is complete and ready for review"
  - action: test
    when: "Implementation needs dedicated test coverage first"
  - action: fix
    when: "Found a bug during implementation that needs a separate fix"
  - ask: true
    when: "Need human input on how to proceed"
```

#### `transitions` field

A list of valid next steps for this action. Each entry is either:

- `{action: "<name>", when: "<description>"}` — derive into another action. `when` is a human-readable hint included in the dispatch postscript so the agent knows when to pick this option.
- `{ask: true, when: "<description>"}` — escalate to human. Always valid even if not listed, but listing it makes the option visible in the postscript and pipeline graph.

**`done` is always implicitly valid** — an agent can always end the chain by calling `torque ai done` without deriving. Actions don't need to list it.

**Server enforcement:** When an agent calls `torque ai derive -t <action>`, the server checks that the target action name appears in the current action's `transitions` list. If not, the derive is rejected with an error: `"Action 'review' cannot transition to 'deploy'. Valid transitions: review, fix"`. This prevents agents from inventing transitions that the user hasn't sanctioned.

**Actions with no `transitions` field** are terminal — calling `derive` from them always fails. The agent can only call `done`, `ask`, or `blocked`.

The depth check uses the **deriving action's** `max_depth` if set, otherwise the global `max_pipeline_depth`.

### DB schema changes

```sql
-- New columns on board_tasks (added via ALTER TABLE migration)
parent_task_id   TEXT NOT NULL DEFAULT ''
pipeline_depth   INTEGER NOT NULL DEFAULT 0
pipeline_root_id TEXT NOT NULL DEFAULT ''
```

### Label conventions

New labels used by the pipeline system:

| Label | Meaning |
|---|---|
| `human` | Task requires human decision (created by `torque ai ask`) |
| `derived` | Task was created by derivation (auto-added) |
| `depth-limit` | Derivation was refused due to depth limit |

These coexist with existing labels (`pr:open`, `blocked`, `error`, etc.).

---

## Server Commands

### `ai_report` — new actions

The existing `ai_report` handler gains two new actions alongside `done`, `blocked`, `pr`, etc.

#### `derive`

Creates a derived task and dispatches it.

**Payload:**
```json
{
  "cmd": "ai_report",
  "action": "derive",
  "cell_id": "abc123",
  "message": "Review the login implementation",
  "action": "review",
  "action_vars": {"TEST_COMMAND": "pytest"},
  "group": ""
}
```

**Behavior:**
1. Resolve the calling agent's linked task (`parent_task`) and the calling agent (`parent_agent`)
2. Validate transition: load `parent_task.action_name`'s `transitions` list. If the target action is not in the list → refuse with error: `"Action '{current}' cannot transition to '{target}'. Valid: {list}"`
3. Check depth: `parent_task.pipeline_depth + 1 > max_depth` → refuse with `needs_attention`
4. Stop the parent agent (end its terminal session, freeing the worktree directory)
5. Mark `parent_task` as Done (same as `ai_report(action="done")`)
6. Create new `BoardTask`:
   - `task` = message
   - `group` = payload group or inherit from parent task
   - `action_name` = action
   - `action_vars` = action_vars
   - `parent_task_id` = parent_task.id
   - `pipeline_depth` = parent_task.pipeline_depth + 1
   - `pipeline_root_id` = parent_task.pipeline_root_id
   - `labels` = ["derived"]
   - `lane` = group's dispatch lane (default: "In Progress")
7. Dispatch the new task with worktree inheritance:
   - Reuse `dispatch_task` logic for agent creation, prompt rendering, and send
   - But **skip worktree creation** — instead, copy `worktree_path`, `worktree_branch`, and `git_root` from `parent_agent` to the new agent
   - Set the new agent's working directory to the inherited worktree path
8. Return `{"ok": true, "data": {"task_id": new_task.id, "agent_id": new_agent.id}}`

#### `ask`

Creates a derived task in Backlog for human review. Does NOT dispatch.

**Payload:**
```json
{
  "cmd": "ai_report",
  "action": "ask",
  "cell_id": "abc123",
  "message": "Impl is done. Should we deploy or add more tests?"
}
```

**Behavior:**
1. Resolve the calling agent's linked task (`parent_task`) and the calling agent (`parent_agent`)
2. Stop the parent agent (end its terminal session, freeing the worktree directory)
3. Mark `parent_task` as Done
4. Create new `BoardTask`:
   - `task` = message
   - `group` = inherit from parent task
   - `parent_task_id` = parent_task.id
   - `pipeline_depth` = parent_task.pipeline_depth + 1
   - `pipeline_root_id` = parent_task.pipeline_root_id
   - `labels` = ["human", "derived"]
   - `lane` = "Backlog" (always — human must review and dispatch)
5. The worktree is NOT cleaned up — it persists on disk. When the human later dispatches this task, `dispatch_task` resolves the worktree from the chain: walk `parent_task_id` → find the parent agent → inherit its `worktree_path`/`worktree_branch`/`git_root`.
6. Set `needs_attention` on the parent agent (before stopping, so the UI shows it)
7. Return `{"ok": true, "data": {"task_id": new_task.id}}`

### `task_chain` — new command

Query the full derivation chain for a task.

**Payload:**
```json
{
  "cmd": "task_chain",
  "task_id": "abc123"
}
```

**Response:**
```json
{
  "ok": true,
  "data": {
    "root_id": "root123",
    "chain": [
      {"id": "root123", "task": "Implement login", "lane": "Done", "depth": 0, "agent_id": "..."},
      {"id": "abc123", "task": "Review login", "lane": "In Progress", "depth": 1, "agent_id": "..."},
    ]
  }
}
```

Returns all tasks sharing the same `pipeline_root_id`, ordered by `pipeline_depth` then `created_at`. This handles branching — a root task with two derivations at depth 1 both appear.

---

## CLI Commands

### `torque ai derive`

```
torque ai derive <description> [flags]

Arguments:
  description              Task description for the next agent

Flags:
  -t, --action NAME        Action for the new task (required in most workflows)
  -v, --var KEY=VALUE       Action variables (repeatable)
  -g, --group GROUP         Target group (default: inherit from current task)
```

**Examples:**

```bash
# After implementing, hand off to review
torque ai derive "Review the login implementation" -t review

# After review rejection, send back for fixes
torque ai derive "Fix auth validation — reviewer found edge case with expired tokens" -t fix

# With custom variables
torque ai derive "Write tests for login" -t test -v TEST_COMMAND=pytest
```

**Behavior:**
- Auto-detects calling agent via `$TORQUE_CELL_ID`
- Calls `ai_report(action="derive", ...)`
- Prints the new task ID and agent name
- Does NOT block (the new agent runs independently)

### `torque ai ask`

```
torque ai ask <question>

Arguments:
  question                 Question or status for the human to review
```

**Examples:**

```bash
# Escalate to human
torque ai ask "Implementation is done but I found a pre-existing bug in auth.py — should I fix it or just file an issue?"

# Request human decision on next step
torque ai ask "All tests pass. Ready for review or should I also update the docs?"
```

**Behavior:**
- Auto-detects calling agent via `$TORQUE_CELL_ID`
- Calls `ai_report(action="ask", ...)`
- Prints the new task ID
- The agent's current task is marked Done

### `torque task chain`

```
torque task chain <task>

Arguments:
  task                     Task ID, slug, or title (any unique identifier)

Flags:
  --json                   Machine-readable output
```

**Output:**

```
Pipeline: root123 (depth 3)

  ✓ [0] Implement login feature          Done        impl-login-a3b2
  ✓ [1] Review login implementation      Done        review-login-f1e2
  → [2] Fix auth validation edge case    In Progress fix-auth-c4d5
```

Shows the full chain with depth, status indicator, lane, and linked agent name.

### `torque task list` — new flags

```bash
# Filter to a pipeline
torque task list --pipeline <task>     # show only tasks in this chain

# Show pipeline info in output
torque task list --chains              # add depth/root columns to table
```

---

## Dispatch Postscript

The postscript appended to every dispatched prompt is now **dynamic** — it's generated from the current action's `transitions` field. This is how agents learn what they can do next, and it's the primary mechanism for guiding agent behavior within a pipeline.

**Postscript for an action with transitions:**

If the dispatched task uses the `implement` action with transitions `[review, test, fix, ask]`:

```
Report your progress with these commands:
- `torque ai done` — task complete, no follow-up needed
- `torque ai derive "description" -t review` — implementation is complete and ready for review
- `torque ai derive "description" -t test` — implementation needs dedicated test coverage first
- `torque ai derive "description" -t fix` — found a bug during implementation that needs a separate fix
- `torque ai ask "question"` — need human input on how to proceed
- `torque ai pr URL` — opened a pull request
- `torque ai merged` — PR merged
- `torque ai blocked "reason"` — need user input
- `torque ai error "message"` — unrecoverable error
```

The `when` descriptions from the action's `transitions` field become the help text for each derive option. The agent sees exactly which actions it can hand off to and when each one is appropriate.

**Postscript for an action with NO transitions (terminal):**

```
Report your progress with these commands:
- `torque ai done` — task complete
- `torque ai ask "question"` — need human decision on next step
- `torque ai pr URL` — opened a pull request
- `torque ai merged` — PR merged
- `torque ai blocked "reason"` — need user input
- `torque ai error "message"` — unrecoverable error
```

No `derive` options are listed — the agent knows this is a terminal stage.

**Pipeline context for derived tasks:**

When dispatching a task that was derived from a parent, the postscript also includes context about the chain:

```
This task is part of a pipeline (depth 2/10).
Parent task: "Implement login feature" (Done, agent: impl-login-a3b2)
Root task: "Implement login feature"
```

This gives the agent awareness of the chain it's part of.

---

## UI Design

### Chain indicators on task cards

Cards that are part of a pipeline show a chain badge:

```
┌────────────────────────────────────┐
│ ● Fix auth validation          ⋮  │
│   ↳ depth 2 · from: Review login  │  ← chain indicator line
│   🤖 fix-auth-c4d5                 │
└────────────────────────────────────┘
```

- `↳` prefix indicates this is a derived task
- "depth N" shows position in chain
- "from: {parent task title}" links to parent (clicking scrolls/switches to that task's lane)
- Root tasks show nothing extra (no chain badge at depth 0 unless they have children)

### Chain thread view

When a card in a pipeline is right-clicked, the context menu includes **"View pipeline"**. This opens a compact thread view overlaid on the board panel:

```
┌─ Pipeline: Implement login ────────┐
│                                     │
│  ✓ Implement login feature     Done │
│  ↳ ✓ Review login impl        Done │
│    ↳ → Fix auth validation   In Pr │  ← highlighted (current)
│                                     │
│                          [Close]    │
└─────────────────────────────────────┘
```

- Shows the full chain as an indented tree (supports branching — two children at same depth both show)
- Current task is highlighted
- Status indicator: `✓` (Done), `→` (In Progress), `○` (Backlog), `✕` (error)
- Clicking a task in the thread focuses it in the board
- `human` label tasks show a 👤 indicator

### Board lane filtering

The board lane tabs gain a filter toggle:

- **All tasks** (default) — shows everything in the lane
- **Pipeline: {root task}** — when a pipeline card is selected, a filter chip appears that narrows the lane to only tasks in that chain

This helps when the board has many tasks but you want to track one pipeline's progress.

### Backlog "human" tasks

Tasks with the `human` label get a distinct visual treatment in the board:

```
┌────────────────────────────────────┐
│ ◎ Should we deploy or add tests?  │  ← open circle (needs human)
│   ↳ depth 2 · 👤 human decision    │
│   [Dispatch ▸]                     │  ← inline dispatch button
└────────────────────────────────────┘
```

The `[Dispatch ▸]` button on human tasks opens the dispatch flow (pick action, fill vars, launch agent) without needing the context menu.

---

## Pipeline Visualization

Actions define transitions. The union of all actions' transitions forms a directed graph — the pipeline graph. Torque renders this graph so users can see the full flow at a glance, including loops.

### Pipeline discovery

A pipeline is a **connected component** in the action transition graph:

1. Load all actions (project + user scope)
2. Build a directed graph: nodes = action names, edges = `transitions[].action` values
3. Find connected components (undirected — if A→B, both A and B are in the same pipeline)
4. Actions with no `transitions` field AND not referenced by any other action are standalone (not part of a pipeline)
5. Each connected component is a named pipeline. The name is derived from the entry-point action (a node with no incoming edges, or the first alphabetically if the graph is fully cyclic)

**Example:** Given these actions:

```yaml
# implement.yaml
transitions: [{action: review}, {action: test}]

# review.yaml
transitions: [{action: fix}]

# fix.yaml
transitions: [{action: review}]

# test.yaml
transitions: []

# investigate.yaml (standalone — no transitions, not referenced)
```

Discovery produces:
- **Pipeline "implement"**: `implement → review ⇄ fix`, `implement → test`
- **Standalone**: `investigate`

### Where it lives: Actions panel

The existing Actions panel in the taskbar gains a **view toggle** at the top:

```
[Actions ▾]  [Editor | Pipelines]
```

- **Editor** (existing) — the action dropdown + form editor
- **Pipelines** (new) — the pipeline graph view

When the user switches to the Pipelines view, Torque scans all actions, discovers pipelines, and renders them.

### Graph rendering

Each discovered pipeline renders as a **vertical flow diagram** inside the panel. Nodes are action boxes, edges are arrows with transition labels. Loops are shown as back-edges curving along the right side.

**Layout algorithm (simple, no library):**

1. Find entry points (nodes with no incoming edges). If none (fully cyclic), pick the first alphabetically.
2. BFS from entry points to assign depth levels (topological layers).
3. Place nodes top-to-bottom by depth. Nodes at the same depth are stacked vertically within their layer.
4. Draw forward edges as straight vertical/diagonal lines (SVG `<line>` or `<path>`).
5. Draw back-edges (target depth ≤ source depth) as curved paths along the right margin with an arrowhead.
6. Terminal nodes (no outgoing transitions) get a distinct style (dashed border, muted color).

**Toolbelt rendering (~280px):**

```
┌─────────────────────────┐
│  Pipeline: feature      │
│                         │
│  ┌─────────────┐        │
│  │  implement  │        │
│  └──┬───────┬──┘        │
│     │       │           │
│     ▼       ▼           │
│  ┌──────┐ ┌──────┐      │
│  │review│ │ test │      │
│  └──┬───┘ └──────┘      │
│     │          ╌╌ done  │
│     ▼                   │
│  ┌──────┐               │
│  │  fix │───────╮       │
│  └──────┘       │       │
│     ▲           │       │
│     └───────────╯       │
│     (back to review)    │
│                         │
│  ─── = transition       │
│  ╌╌╌ = terminal (done)  │
│  ╭╯  = loop back        │
└─────────────────────────┘
```

**Node box contents:**

```
┌─────────────────┐
│ implement       │  ← action name
│ 2 transitions   │  ← outgoing count
└─────────────────┘
```

Clicking a node opens that action in the Editor view. Hovering a node highlights its edges. Hovering an edge shows the `when` description as a tooltip.

**Standalone/wider rendering (>600px):**

Same layout but nodes can be placed side-by-side at the same depth (horizontal branching), and edge labels (`when` text) are shown inline on the arrows instead of requiring hover.

### Multiple pipelines

If multiple disconnected pipelines exist, they render as separate sections with a divider:

```
Pipeline: feature
  [implement] → [review] ⇄ [fix]
                    ↓
              [test] (terminal)

──────────────────────────

Pipeline: hotfix
  [triage] → [fix] → [deploy] (terminal)
```

### Interactive features

- **Click node** → switches to Editor view with that action selected
- **Hover node** → highlights all edges connected to it
- **Hover edge** → tooltip shows the `when` description
- **Click edge** → no-op (edges aren't actionable)
- **Zoom** — if the graph is taller than the panel, it scrolls vertically. No pinch-to-zoom (keep it simple).

### CLI: `torque pipeline`

```bash
# List discovered pipelines
torque pipeline list

# Output:
# feature    implement → review ⇄ fix, implement → test
# hotfix     triage → fix → deploy

# Show a pipeline's full graph
torque pipeline show feature

# Output:
# Pipeline: feature (4 actions)
#
#   implement
#     → review   "implementation is complete"
#     → test     "needs test coverage first"
#
#   review
#     → fix      "needs changes"
#     (done)     "approved"
#     (ask)      "need human input"
#
#   fix
#     → review   "fixed, ready for re-review"
#
#   test
#     (terminal — no transitions)
```

The CLI renders the adjacency list form — compact and readable in a terminal. The graph visualization is UI-only.

---

## Implementation Steps

### Step 1: Data model (`torque/state.py`, `torque/db.py`) ✅

- Add `parent_task_id`, `pipeline_depth`, `pipeline_root_id` fields to `BoardTask`
- Add `max_pipeline_depth` to `GlobalSettings` (default: 10)
- Add DB migration: `ALTER TABLE board_tasks ADD COLUMN parent_task_id/pipeline_depth/pipeline_root_id`
- Update `save_board_task` and `load_all` to handle new columns
- Add `board_get_chain(task_id)` method to `MatrixState` — returns all tasks with the same `pipeline_root_id`, ordered by depth then created_at

### Step 2: Server — derive and ask actions (`torque/server.py`) ✅

- Extend `ai_report` handler with `action="derive"` and `action="ask"` branches
- `derive`: validate transition → validate depth → mark parent Done → create child task → dispatch with worktree inheritance (reuse `dispatch_task` internals)
- `ask`: mark parent Done → create child task in Backlog with `human` label → set `needs_attention`. Worktree persists on disk for later pickup.
- Add `task_chain` command handler
- Depth limit check reads action's `max_depth`, falls back to global `max_pipeline_depth`

### Step 2b: Worktree inheritance in dispatch (`torque/server.py`) ✅

- `dispatch_task` accepts optional `inherit_worktree_from` agent ID
- When set: skip `WorktreeManager.create()`, copy worktree fields from the source agent and set the new agent's directory to the worktree path
- HITL dispatch path: when dispatching a task with `parent_task_id`, walk the parent chain to find the last agent with a worktree and inherit from it

### Step 3: Action support (`torque/actions.py`) ✅

- `transitions` parsed as an optional action field (list of `{action, when}` or `{ask, when}` dicts — not rendered through Jinja2)
- `max_depth` parsed as an optional action field
- `render_action` passes through `transitions` and `max_depth` in its return dict
- `get_transitions(action_name)` helper returns the parsed transitions list for an action
- `discover_pipelines(base_dir)` scans all actions, builds the transition graph, returns connected components as `[{name, actions, edges}]`

### Step 4: Dispatch postscript (`torque/server.py`) ✅

- `_build_postscript()` generates the postscript dynamically from the action's `transitions` field
- Each transition becomes a `torque ai derive -t <name>` line with the `when` description as help text
- Actions with no transitions get a generic derive line
- Derived tasks get pipeline context (parent task info, depth, root)

### Step 5: CLI — derive, ask, and pipeline (`bin/torque`) ✅

- `torque ai derive` subcommand: parse args → `api_call("ai_report", action="derive", ...)`
- `torque ai ask` subcommand: parse args → `api_call("ai_report", action="ask", ...)`
- `torque task chain` subcommand: resolve task → reads chain locally from SQLite → render tree
- `torque pipeline list` subcommand: call `discover_pipelines` → render summary table
- `torque pipeline show <name>` subcommand: render adjacency list with transition descriptions

### Step 6: Board UI — chain indicators (`static/js/board.js`) ✅

- Cards show chain badge when `parent_task_id` is non-empty (`↳ depth N · from: parent title`)
- `human` label tasks show 👤 indicator in the chain badge
- "View pipeline" added to card context menu (only for tasks in a chain)

### Step 7: Board UI — pipeline thread view (`static/js/board.js`) ✅

- `boardViewPipeline(taskId)` walks the chain, renders an overlay with depth-indented items
- Status indicators: `✓` (Done), `→` (In Progress), `○` (Backlog), `✕` (error)
- Current task highlighted, clicking a task focuses it in the board
- Thread overlay positioned within the board panel, closeable

### Step 8: Board UI — pipeline filter (`static/js/board.js`)

- Not implemented yet. Deferred — the pipeline thread view provides equivalent functionality.

### Step 9: Pipeline visualization — Actions panel (`static/js/actions.js`) ✅

- Editor/Pipelines view toggle in the Actions panel header
- `renderPipelinesView()` renders a pannable/zoomable canvas (CSS transform pan/zoom, mouse drag + scroll wheel)
- BFS-based layout: entry nodes (no inbound) at top, terminal nodes (no outbound) at bottom, centered per layer
- Action nodes as styled HTML divs positioned absolutely — entry nodes have accent left border, terminal nodes have dashed borders. Click to open in editor.
- Ask transitions rendered as small pill-shaped nodes snug to the right of their source, connected by a dashed line. Each action gets its own independent ask node. Layer widths account for ask nodes so they never overlap other actions.
- SVG edges: forward edges as smooth vertical bezier curves with arrowheads. Back-edges route via the left or right side (whichever is closer to the target) using quadratic bezier S-curves that arrive horizontally. Multiple back-edges on the same side get staggered channels.
- Pipeline data is re-fetched on every view switch to reflect action edits

### Step 9b: Transitions editor — Actions panel (`static/js/actions.js`) ✅

- Collapsible "Transitions" section in the action editor form, between Labels and Variables
- Each transition is an entry with: type dropdown (Action / Ask), action picker dropdown (grouped by Project/User, matching the existing action selector pattern), and a full-width auto-growing "When" textarea with label and `?` tooltip
- `+ Add transition` button to add new rows; `×` button to remove
- Transitions count shown as a discrete pill badge in the section summary
- `?` tooltip on the section header explains the purpose
- Action picker shows "(missing)" for saved transitions referencing deleted actions
- Transitions serialized to YAML on save via `_action_to_yaml()` and loaded back on edit

### Step 10: Server — pipeline discovery (`torque/server.py`) ✅

- `discover_pipelines` command handler calls `action_mgr.discover_pipelines()`, returns `{type: "pipelines", pipelines: [{name, actions, edges}]}`
- Used by both the Actions panel (via WS) and the CLI (via REST)

### Step 11: Global settings UI (`static/js/modals.js`) ✅

- `max_pipeline_depth` number input in Global Settings → General → Board tab
- "0 = unlimited" hint label
- Value persisted via `update_global_settings`

### Remaining Work

- **Pipeline lane filter** (Step 8) — filter board lane to show only tasks from one pipeline chain
- **`--pipeline` and `--chains` flags** on `torque task list` CLI command
- **Inline dispatch button** on human/HITL task cards in the board
- **Worktree cleanup guard** — prevent `WorktreeManager.remove()` on worktrees belonging to active pipeline chains

---

## What We're NOT Building (Yet)

- **Pipeline runtime objects** — There is no `Pipeline` model in the database. Pipelines are discovered at render time from action transitions. This keeps the data model simple and means editing an action instantly changes the pipeline graph.
- **Parallel branches** — Derivation is always serial (one child per derive call). An agent could call `derive` multiple times to create parallel branches, but there's no fan-out/fan-in primitive.
- **Auto-retry** — If an agent errors, it doesn't auto-retry. A human or parent agent creates a new task.
- **Pipeline-level status** — No aggregate "pipeline is 60% done" progress bar. The chain view shows individual task statuses.
- **Cross-project pipelines** — Tasks and their chains are scoped to one project/board.
- **Derive from UI** — The UI can view pipelines but derivation only happens via `torque ai derive` (agent-initiated) or `torque ai ask` (human gate). There's no "derive" button on task cards — that's what dispatch is for.
- **Graph layout library** — The pipeline graph uses a simple BFS-based layout algorithm, not a full graph layout engine (dagre, elk, etc.). This keeps the zero-dependency constraint. Complex graphs with many cross-edges may not render perfectly, but typical pipelines (3-6 actions) will look clean.

---

## File Changes Summary

| File | Change |
|---|---|
| `torque/state.py` | Add pipeline fields to `BoardTask`, `max_pipeline_depth` to `GlobalSettings`, `board_get_chain()` method |
| `torque/db.py` | Add migration for `parent_task_id`, `pipeline_depth`, `pipeline_root_id` columns; update `save_board_task` and `save_all` |
| `torque/server.py` | Extend `ai_report` with `derive`/`ask` actions (transition validation + worktree inheritance), add `task_chain` and `discover_pipelines` commands, `_build_postscript()` for dynamic postscript from action transitions, `_action_to_yaml()` serializes transitions, `dispatch_task` supports `inherit_worktree_from` + HITL parent-chain worktree resolution |
| `torque/actions.py` | Parse `transitions` and `max_depth` from action YAML, add `get_transitions()`, `discover_pipelines()` (connected-component discovery) |
| `bin/torque` | Add `torque ai derive`, `torque ai ask`, `torque task chain`, `torque pipeline list/show` commands |
| `static/js/board.js` | Chain indicators on cards, pipeline thread overlay with `boardViewPipeline()`, "View pipeline" context menu |
| `static/js/actions.js` | Editor/Pipelines view toggle, pipeline graph renderer (BFS layout, node boxes with adjacency lists), transitions editor in action form (type dropdown, action picker with Project/User optgroups, auto-growing "When" textarea with label and tooltip) |
| `static/js/ws.js` | Route `pipelines` response type to `tplReceivePipelines()` |
| `static/js/modals.js` | `max_pipeline_depth` in global settings modal |
| `static/style.css` | Styles for chain badges, pipeline thread overlay, pipeline graph nodes, transition editor rows (entry containers, type/action selects, when textarea/label, add/remove buttons), view toggle, count badge |
| `webview.html` | `max_pipeline_depth` input in Board settings sub-tab |
| `CLAUDE.md` | Document pipeline fields, transitions, derive/ask actions, pipeline CLI commands |
