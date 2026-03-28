# Implementation Plan: Pipelines (Phase 4b)

**Roadmap phase**: 4 — Workflow Automation
**Status**: Planned
**Goal**: Multi-step agent workflows through task derivation. An agent completes its task and creates a new derived task for the next agent. The chain is always forward-moving — no cycles, no retries on the same task. The "pipeline" is emergent from the parent-child task chain, not a declared DAG.

---

## The Problem

Loom can dispatch a single task to a single agent. But real workflows have multiple stages: implement → test → review → merge. Today, the only way to chain stages is a shell script calling `loom task dispatch --wait` sequentially. This works but has drawbacks:

- The shell script is the orchestrator — if it dies, the pipeline dies
- Agents can't make runtime decisions about what comes next
- There's no audit trail linking the stages together
- There's no way for an agent to escalate to a human mid-pipeline

The goal is to let agents themselves drive the pipeline forward by deriving new tasks, while keeping the model simple enough that there are no cycle bugs, no state machines, and no pipeline engine to maintain.

## Design Principles

1. **Tasks only move forward** — An agent can mark its task as done and derive a new task. It cannot reopen its own task or send work backward. If a reviewer rejects work, it creates a *new* forward task ("fix this"), not a cycle back to the original.
2. **Pipelines are emergent** — There is no `Pipeline` object. A pipeline is just a chain of tasks linked by `parent_task_id`. The chain is visible in the board UI and queryable via CLI, but Loom doesn't enforce a pipeline schema.
3. **Templates declare valid transitions** — Each template lists which templates it can derive into via a `transitions` field. The agent picks from those options; the server rejects anything not on the list. This makes pipeline structure visible and enforceable without a separate pipeline config object. The pipeline graph is emergent from the union of all templates' transition lists.
4. **Human-in-the-loop is a first-class concept** — `loom ai ask` creates a derived task in Backlog instead of dispatching it. A human reviews, edits, and manually dispatches. This is the HITL gate.
5. **Depth limits prevent runaway chains** — A configurable `max_pipeline_depth` (global setting, overridable per template) caps how deep a chain can go. When exceeded, the task gets a `needs_attention` flag instead of deriving.
6. **The entire chain shares one worktree** — When an agent derives a new task, the new agent inherits the parent agent's worktree. This is essential: a review agent must see the code the implement agent wrote. The worktree is created for the root task and reused by every derived task in the chain. No new worktree is created on derive.

---

## Architecture

### Task derivation flow

```
Agent A (impl) finishes task T1
  │
  ├── loom ai done                          ← marks T1 as Done
  ├── loom ai derive "Review the login      ← creates T2 in dispatch lane
  │     implementation" --template review      T2.parent_task_id = T1.id
  │                                            T2.pipeline_depth = T1.pipeline_depth + 1
  │                                            T2.pipeline_root_id = T1.pipeline_root_id
  │
  └── Agent B (review) boots with T2
        │
        ├── (happy path) loom ai done       ← T2 Done, chain complete
        │
        └── (rejection) loom ai derive      ← creates T3
              "Fix auth validation"            T3.parent_task_id = T2.id
              --template fix                   T3.pipeline_depth = 2
              Agent C (fix) boots with T3
                │
                └── loom ai derive          ← creates T4
                      "Re-review auth"
                      --template review
                      ...chain continues
```

### Human-in-the-loop flow

```
Agent A finishes task T1
  │
  ├── loom ai ask "Impl is done.           ← creates T2 in Backlog (not dispatched)
  │     Should we deploy or add               T2.parent_task_id = T1.id
  │     more tests?"                          T2 has label: "human"
  │
  └── Human sees T2 in Backlog
        ├── edits task description
        ├── picks a template
        └── dispatches manually             ← T2 moves to In Progress, agent launches
```

### Depth limit enforcement

```
Agent tries: loom ai derive "..." --template review
  │
  Server checks: task.pipeline_depth + 1 > max_pipeline_depth?
  │
  ├── No  → create derived task, dispatch agent
  └── Yes → refuse derivation, set needs_attention on current task,
            return warning: "Pipeline depth limit (N) reached"
```

### Data flow

```
loom ai derive "Review login" --template review
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

A pipeline's entire task chain runs in a single worktree. This is critical — if the implement agent writes code in `loom/impl-login-a3b2/`, the review agent must see those files, not start from a clean branch.

```
Root task T1 (implement)
  │
  ├── Agent A spawns in worktree: .loom/worktrees/impl-login-a3b2/
  │   branch: loom/impl-login-a3b2
  │   Agent A writes code, commits, calls `loom ai derive -t review`
  │
  ├── derive creates T2 (review)
  │   Agent B spawns in THE SAME worktree: .loom/worktrees/impl-login-a3b2/
  │   Agent B sees all of Agent A's changes
  │   Agent B calls `loom ai derive -t fix`
  │
  └── derive creates T3 (fix)
      Agent C spawns in THE SAME worktree: .loom/worktrees/impl-login-a3b2/
      Agent C sees Agent A's code + Agent B's review comments (if committed)
```

**How it works:**

1. The root task's agent creates a worktree normally (via group settings or template `worktree: true`). The worktree path and branch are stored on the `AgentCell`.
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

### Template additions

```yaml
# .loom/templates/implement.yaml
name: implement
# ... existing fields ...

# Pipeline fields (new)
max_depth: 5          # override global max_pipeline_depth for chains using this template

transitions:
  - template: review
    when: "Implementation is complete and ready for review"
  - template: test
    when: "Implementation needs dedicated test coverage first"
  - template: fix
    when: "Found a bug during implementation that needs a separate fix"
  - ask: true
    when: "Need human input on how to proceed"
```

#### `transitions` field

A list of valid next steps for this template. Each entry is either:

- `{template: "<name>", when: "<description>"}` — derive into another template. `when` is a human-readable hint included in the dispatch postscript so the agent knows when to pick this option.
- `{ask: true, when: "<description>"}` — escalate to human. Always valid even if not listed, but listing it makes the option visible in the postscript and pipeline graph.

**`done` is always implicitly valid** — an agent can always end the chain by calling `loom ai done` without deriving. Templates don't need to list it.

**Server enforcement:** When an agent calls `loom ai derive -t <template>`, the server checks that the target template name appears in the current template's `transitions` list. If not, the derive is rejected with an error: `"Template 'review' cannot transition to 'deploy'. Valid transitions: review, fix"`. This prevents agents from inventing transitions that the user hasn't sanctioned.

**Templates with no `transitions` field** are terminal — calling `derive` from them always fails. The agent can only call `done`, `ask`, or `blocked`.

The depth check uses the **deriving template's** `max_depth` if set, otherwise the global `max_pipeline_depth`.

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
| `human` | Task requires human decision (created by `loom ai ask`) |
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
  "template": "review",
  "template_vars": {"TEST_COMMAND": "pytest"},
  "group": ""
}
```

**Behavior:**
1. Resolve the calling agent's linked task (`parent_task`) and the calling agent (`parent_agent`)
2. Validate transition: load `parent_task.template_name`'s `transitions` list. If the target template is not in the list → refuse with error: `"Template '{current}' cannot transition to '{target}'. Valid: {list}"`
3. Check depth: `parent_task.pipeline_depth + 1 > max_depth` → refuse with `needs_attention`
4. Stop the parent agent (end its terminal session, freeing the worktree directory)
5. Mark `parent_task` as Done (same as `ai_report(action="done")`)
6. Create new `BoardTask`:
   - `task` = message
   - `group` = payload group or inherit from parent task
   - `template_name` = template
   - `template_vars` = template_vars
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

### `loom ai derive`

```
loom ai derive <description> [flags]

Arguments:
  description              Task description for the next agent

Flags:
  -t, --template NAME      Template for the new task (required in most workflows)
  -v, --var KEY=VALUE       Template variables (repeatable)
  -g, --group GROUP         Target group (default: inherit from current task)
```

**Examples:**

```bash
# After implementing, hand off to review
loom ai derive "Review the login implementation" -t review

# After review rejection, send back for fixes
loom ai derive "Fix auth validation — reviewer found edge case with expired tokens" -t fix

# With custom variables
loom ai derive "Write tests for login" -t test -v TEST_COMMAND=pytest
```

**Behavior:**
- Auto-detects calling agent via `$LOOM_CELL_ID`
- Calls `ai_report(action="derive", ...)`
- Prints the new task ID and agent name
- Does NOT block (the new agent runs independently)

### `loom ai ask`

```
loom ai ask <question>

Arguments:
  question                 Question or status for the human to review
```

**Examples:**

```bash
# Escalate to human
loom ai ask "Implementation is done but I found a pre-existing bug in auth.py — should I fix it or just file an issue?"

# Request human decision on next step
loom ai ask "All tests pass. Ready for review or should I also update the docs?"
```

**Behavior:**
- Auto-detects calling agent via `$LOOM_CELL_ID`
- Calls `ai_report(action="ask", ...)`
- Prints the new task ID
- The agent's current task is marked Done

### `loom task chain`

```
loom task chain <task>

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

### `loom task list` — new flags

```bash
# Filter to a pipeline
loom task list --pipeline <task>     # show only tasks in this chain

# Show pipeline info in output
loom task list --chains              # add depth/root columns to table
```

---

## Dispatch Postscript

The postscript appended to every dispatched prompt is now **dynamic** — it's generated from the current template's `transitions` field. This is how agents learn what they can do next, and it's the primary mechanism for guiding agent behavior within a pipeline.

**Postscript for a template with transitions:**

If the dispatched task uses the `implement` template with transitions `[review, test, fix, ask]`:

```
Report your progress with these commands:
- `loom ai done` — task complete, no follow-up needed
- `loom ai derive "description" -t review` — implementation is complete and ready for review
- `loom ai derive "description" -t test` — implementation needs dedicated test coverage first
- `loom ai derive "description" -t fix` — found a bug during implementation that needs a separate fix
- `loom ai ask "question"` — need human input on how to proceed
- `loom ai pr URL` — opened a pull request
- `loom ai merged` — PR merged
- `loom ai blocked "reason"` — need user input
- `loom ai error "message"` — unrecoverable error
```

The `when` descriptions from the template's `transitions` field become the help text for each derive option. The agent sees exactly which templates it can hand off to and when each one is appropriate.

**Postscript for a template with NO transitions (terminal):**

```
Report your progress with these commands:
- `loom ai done` — task complete
- `loom ai ask "question"` — need human decision on next step
- `loom ai pr URL` — opened a pull request
- `loom ai merged` — PR merged
- `loom ai blocked "reason"` — need user input
- `loom ai error "message"` — unrecoverable error
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

The `[Dispatch ▸]` button on human tasks opens the dispatch flow (pick template, fill vars, launch agent) without needing the context menu.

---

## Pipeline Visualization

Templates define transitions. The union of all templates' transitions forms a directed graph — the pipeline graph. Loom renders this graph so users can see the full flow at a glance, including loops.

### Pipeline discovery

A pipeline is a **connected component** in the template transition graph:

1. Load all templates (project + user scope)
2. Build a directed graph: nodes = template names, edges = `transitions[].template` values
3. Find connected components (undirected — if A→B, both A and B are in the same pipeline)
4. Templates with no `transitions` field AND not referenced by any other template are standalone (not part of a pipeline)
5. Each connected component is a named pipeline. The name is derived from the entry-point template (a node with no incoming edges, or the first alphabetically if the graph is fully cyclic)

**Example:** Given these templates:

```yaml
# implement.yaml
transitions: [{template: review}, {template: test}]

# review.yaml
transitions: [{template: fix}]

# fix.yaml
transitions: [{template: review}]

# test.yaml
transitions: []

# investigate.yaml (standalone — no transitions, not referenced)
```

Discovery produces:
- **Pipeline "implement"**: `implement → review ⇄ fix`, `implement → test`
- **Standalone**: `investigate`

### Where it lives: Templates panel

The existing Templates panel in the taskbar gains a **view toggle** at the top:

```
[Templates ▾]  [Editor | Pipelines]
```

- **Editor** (existing) — the template dropdown + form editor
- **Pipelines** (new) — the pipeline graph view

When the user switches to the Pipelines view, Loom scans all templates, discovers pipelines, and renders them.

### Graph rendering

Each discovered pipeline renders as a **vertical flow diagram** inside the panel. Nodes are template boxes, edges are arrows with transition labels. Loops are shown as back-edges curving along the right side.

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
│ implement       │  ← template name
│ 2 transitions   │  ← outgoing count
└─────────────────┘
```

Clicking a node opens that template in the Editor view. Hovering a node highlights its edges. Hovering an edge shows the `when` description as a tooltip.

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

- **Click node** → switches to Editor view with that template selected
- **Hover node** → highlights all edges connected to it
- **Hover edge** → tooltip shows the `when` description
- **Click edge** → no-op (edges aren't actionable)
- **Zoom** — if the graph is taller than the panel, it scrolls vertically. No pinch-to-zoom (keep it simple).

### CLI: `loom pipeline`

```bash
# List discovered pipelines
loom pipeline list

# Output:
# feature    implement → review ⇄ fix, implement → test
# hotfix     triage → fix → deploy

# Show a pipeline's full graph
loom pipeline show feature

# Output:
# Pipeline: feature (4 templates)
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

### Step 1: Data model (`loom/state.py`, `loom/db.py`)

- Add `parent_task_id`, `pipeline_depth`, `pipeline_root_id` fields to `BoardTask`
- Add `max_pipeline_depth` to `GlobalSettings` (default: 10)
- Add DB migration: `ALTER TABLE board_tasks ADD COLUMN parent_task_id/pipeline_depth/pipeline_root_id`
- Update `save_board_task` and `load_all` to handle new columns
- Add `board_get_chain(task_id)` method to `MatrixState` — returns all tasks with the same `pipeline_root_id`, ordered by depth then created_at

### Step 2: Server — derive and ask actions (`loom/server.py`)

- Extend `ai_report` handler with `action="derive"` and `action="ask"` branches
- `derive`: validate transition → validate depth → stop parent agent → mark parent Done → create child task → dispatch with worktree inheritance (reuse `dispatch_task` internals but skip `WorktreeManager.create()`)
- `ask`: stop parent agent → mark parent Done → create child task in Backlog with `human` label → set `needs_attention`. Worktree persists on disk for later pickup.
- Add `task_chain` command handler
- Add depth limit check helper: `_check_pipeline_depth(task, template)` → reads template's `max_depth`, falls back to global `max_pipeline_depth`
- Add worktree inheritance helper: `_inherit_worktree(parent_agent, new_agent)` → copies `worktree_path`, `worktree_branch`, `git_root` from parent to child, sets child's working directory to the worktree path

### Step 2b: Worktree inheritance in dispatch (`loom/server.py`)

- Modify `dispatch_task` to accept an optional `inherit_worktree_from` agent ID
- When set: skip `WorktreeManager.create()`, instead copy worktree fields from the source agent and set the new agent's directory to the worktree path
- Modify HITL dispatch path: when dispatching a task with `parent_task_id`, walk the parent chain to find the last agent with a worktree and inherit from it
- Ensure `WorktreeManager.remove()` is NOT called when stopping an agent that's part of an active pipeline chain (the worktree belongs to the chain, not the individual agent)

### Step 3: Template support (`loom/templates.py`)

- Add `transitions` as an optional template field (list of `{template, when}` or `{ask, when}` dicts — parsed but not rendered through Jinja2)
- Add `max_depth` as an optional template field (parsed but not rendered through Jinja2)
- `render_template` passes through `transitions` and `max_depth` in its return dict
- Add `get_transitions(template_name)` helper — returns the parsed transitions list for a template
- Add `discover_pipelines(base_dir)` — scans all templates, builds the transition graph, returns connected components as `[{name, templates, edges}]`

### Step 4: Dispatch postscript (`loom/server.py`)

- Make the postscript dynamic: read the dispatched template's `transitions` field
- Generate `loom ai derive -t <name>` lines from each transition, using `when` as the help text
- Omit `derive` lines entirely for templates with no transitions
- When dispatching a derived task, append pipeline context (parent task info, depth, root)

### Step 5: CLI — derive, ask, and pipeline (`bin/loom`)

- Add `loom ai derive` subcommand: parse args → `api_call("ai_report", action="derive", ...)`
- Add `loom ai ask` subcommand: parse args → `api_call("ai_report", action="ask", ...)`
- Add `loom task chain` subcommand: resolve task → `api_call("task_chain", ...)` → render tree
- Add `--pipeline` and `--chains` flags to `loom task list`
- Add `loom pipeline list` subcommand: call `discover_pipelines` → render summary table
- Add `loom pipeline show <name>` subcommand: render adjacency list with transition descriptions

### Step 6: Board UI — chain indicators (`static/js/board.js`)

- Extend `renderTaskCard` to show chain badge when `parent_task_id` is non-empty
- Add parent task title resolution (look up in `state.board_tasks`)
- Add `human` label visual treatment (open circle, inline dispatch button)
- Add "View pipeline" to card context menu

### Step 7: Board UI — pipeline thread view (`static/js/board.js`)

- Build `renderPipelineThread(rootId)` — walks the chain, renders indented tree
- Thread overlay positioned within the board panel
- Click-to-focus on tasks in the thread
- Branching support: group children by parent, indent accordingly

### Step 8: Board UI — pipeline filter (`static/js/board.js`)

- Add filter chip when a pipeline task is selected
- Filter lane cards to `pipeline_root_id` match
- Clear filter on chip dismiss or selecting a non-pipeline task

### Step 9: Pipeline visualization — Templates panel (`static/js/templates.js`)

- Add Editor/Pipelines view toggle to the Templates panel header
- Add `renderPipelinesView()` — calls server `discover_pipelines` command, renders discovered pipelines
- Implement layout algorithm: entry point detection → BFS depth assignment → vertical node placement
- Render nodes as styled `<div>` boxes with template name and transition count
- Render edges as SVG `<path>` elements overlaid on the node layout
- Render back-edges (loops) as curved paths along the right margin
- Terminal nodes get dashed border style
- Click-to-edit: clicking a node switches to Editor view with that template selected
- Hover highlighting: hovering a node dims unconnected edges, hovering an edge shows `when` tooltip
- Multiple pipeline support: render separate sections with dividers

### Step 10: Server — pipeline discovery (`loom/server.py`)

- Add `discover_pipelines` command handler: calls `template_mgr.discover_pipelines()`, returns `{pipelines: [{name, templates: [...], edges: [{from, to, when}]}]}`
- This is a read-only command used by both the Templates panel and the CLI

### Step 11: Global settings UI (`static/js/modals.js`)

- Add `max_pipeline_depth` field to Global Settings → General → Board tab
- Number input with "0 = unlimited" hint

---

## What We're NOT Building (Yet)

- **Pipeline runtime objects** — There is no `Pipeline` model in the database. Pipelines are discovered at render time from template transitions. This keeps the data model simple and means editing a template instantly changes the pipeline graph.
- **Parallel branches** — Derivation is always serial (one child per derive call). An agent could call `derive` multiple times to create parallel branches, but there's no fan-out/fan-in primitive.
- **Auto-retry** — If an agent errors, it doesn't auto-retry. A human or parent agent creates a new task.
- **Pipeline-level status** — No aggregate "pipeline is 60% done" progress bar. The chain view shows individual task statuses.
- **Cross-project pipelines** — Tasks and their chains are scoped to one project/board.
- **Derive from UI** — The UI can view pipelines but derivation only happens via `loom ai derive` (agent-initiated) or `loom ai ask` (human gate). There's no "derive" button on task cards — that's what dispatch is for.
- **Graph layout library** — The pipeline graph uses a simple BFS-based layout algorithm, not a full graph layout engine (dagre, elk, etc.). This keeps the zero-dependency constraint. Complex graphs with many cross-edges may not render perfectly, but typical pipelines (3-6 templates) will look clean.

---

## File Changes Summary

| File | Change |
|---|---|
| `loom/state.py` | Add pipeline fields to `BoardTask`, `max_pipeline_depth` to `GlobalSettings`, `board_get_chain()` method |
| `loom/db.py` | Add migration for `parent_task_id`, `pipeline_depth`, `pipeline_root_id` columns |
| `loom/server.py` | Extend `ai_report` with `derive`/`ask` actions (with transition validation + worktree inheritance), add `task_chain` and `discover_pipelines` commands, make dispatch postscript dynamic from template transitions, modify `dispatch_task` for `inherit_worktree_from` path |
| `loom/worktree.py` | Guard against removing worktrees that belong to active pipeline chains |
| `loom/templates.py` | Parse `transitions` and `max_depth` from template YAML, add `get_transitions()`, `discover_pipelines()` |
| `bin/loom` | Add `loom ai derive`, `loom ai ask`, `loom task chain`, `loom pipeline list/show` commands, `--pipeline`/`--chains` flags |
| `static/js/board.js` | Chain indicators on cards, pipeline thread view, pipeline filter, human task treatment |
| `static/js/templates.js` | Editor/Pipelines view toggle, pipeline graph renderer (node layout, SVG edges, back-edge curves, click-to-edit, hover tooltips) |
| `static/js/modals.js` | `max_pipeline_depth` in global settings modal |
| `static/style.css` | Styles for chain badges, thread view, human task cards, pipeline filter chip, pipeline graph nodes/edges/back-edges |
| `docs/roadmap.md` | Update Phase 4 status |
