# Implementation Plan: Task Board (Phase 5a)

**Roadmap phase**: 5 — Task & Ticketing Integration
**Status**: Implemented
**Goal**: Add a built-in Kanban board to Torque's toolbelt so users can track tasks alongside their agents. The board lives in a collapsible bottom panel with a taskbar — an extensible dock for "panel apps" where the Kanban board is the first one.

---

## The Problem

Torque manages agents and terminals but has no concept of _what work_ those agents are doing. Users track tasks externally (Linear, Jira, Notion, sticky notes) and manually coordinate between the task tracker and Torque. There's no way to see "what's in the backlog" and "which agent is working on what" in the same view.

The goal is a lightweight, in-Torque task board that:
- Gives a quick overview of work status without leaving iTerm2
- Can later sync with external trackers (Jira, Linear, GitHub Issues)
- Links tasks to agents so you can see "Agent X is working on Task Y"

## Design Principles

1. **Panel apps, not one-off panels** — The bottom area is a generic "taskbar + panel" system. The Kanban board is the first app. Future apps (logs viewer, cost dashboard, diff viewer) plug into the same dock. Each app is a self-contained render/command module.
2. **One lane at a time** — The toolbelt is narrow (~280px). Showing all lanes side-by-side doesn't work. Instead, show lane tabs at the top and the selected lane's cards below. This scales to any number of lanes.
3. **Provider-ready data model** — Every task has optional `provider` and `external_id` fields from day one. The board works standalone (manual cards), but the schema is ready for Jira/Linear sync without migration.
4. **Board state lives in MatrixState** — Same persistence model as agents/groups. Board mutations go through `MatrixState` methods, trigger `save()` + `broadcast()`, and the UI re-renders. No separate persistence layer.
5. **Drag-and-drop between lanes** — Cards can be dragged to lane tabs to move them. Same drag-drop patterns as agent/terminal reordering.

---

## Architecture

```
┌─────────────────────────────────┐
│         TORQUE TOOLBELT           │
│                                 │
│  ┌───────────────────────────┐  │
│  │       Groups / Agents      │  │
│  │     (existing UI, flex:1)  │  │
│  └───────────────────────────┘  │
│                                 │
│  ┌───────────────────────────┐  │
│  │      Bottom Panel          │  │  ← collapsible, resizable
│  │  ┌─────────────────────┐  │  │
│  │  │  Lane Tabs (scroll) │  │  │  ← < [Backlog] [To Do] [In Progress] [Done] > [+] [⚙]
│  │  ├─────────────────────┤  │  │
│  │  │  Card List           │  │  │  ← tasks in selected lane
│  │  │  ┌─────────────────┐ │  │  │
│  │  │  │ Task card        │ │  │  │
│  │  │  │ Task card        │ │  │  │
│  │  │  │ + Add task       │ │  │  │
│  │  │  └─────────────────┘ │  │  │
│  │  └─────────────────────┘  │  │
│  └───────────────────────────┘  │
│                                 │
│  ┌───────────────────────────┐  │
│  │  Taskbar: [📋 Board]      │  │  ← dock for panel apps
│  └───────────────────────────┘  │
└─────────────────────────────────┘
```

### Data flow

```
User action (add task, move card, rename lane)
  │
  ├──► send({ cmd: 'board_*', ... })           ← WS command
  │
  ├──► server.py handle_command()
  │      ├── state.board_add_task(...)          ← MatrixState method
  │      ├── state.save()
  │      └── state.broadcast()                 ← full state includes board
  │
  └──► UI re-renders board panel
```

### Provider integration (future)

```
┌─────────────┐     ┌──────────────────┐     ┌────────────────┐
│ Torque Board  │ ←── │ Provider Adapter  │ ←── │ External API   │
│ (MatrixState)│ ──► │ (sync layer)     │ ──► │ (Jira/Linear)  │
└─────────────┘     └──────────────────┘     └────────────────┘

Provider adapters implement:
  - pull_tasks(lane_mapping) → [Task]     # fetch from external
  - push_task(task, lane_mapping) → void  # update external
  - map_lanes(provider_statuses) → dict   # map provider statuses to board lanes
```

Providers are NOT part of this implementation. The data model is designed so that adding them later requires zero schema changes.

---

## Data Model

### BoardTask dataclass

```python
@dataclass
class BoardTask:
    id: str                          # 8-char hex
    task: str                        # actionable task description (required)
    group: str = ""                  # owning group (must exist)
    instructions: str = ""           # agent instructions (optional)
    context: str = ""                # additional context (optional)
    criteria: str = ""               # acceptance criteria (optional)
    lane: str = "Backlog"            # must match a lane name
    position: int = 0                # sort order within lane
    assignee: str = ""               # agent name prefix for pool assignment
    agent_id: str = ""               # concrete agent working on this
    labels: list[str] = field(default_factory=list)
    created_at: str = ""             # ISO 8601
    updated_at: str = ""             # ISO 8601

    # Provider fields (unused in v1, ready for sync)
    provider: str = ""               # "jira", "linear", "github"
    external_id: str = ""            # e.g. "PROJ-123"
    external_url: str = ""           # link back to provider
```

**Reserved lanes**: `Backlog` and `In Progress` cannot be renamed or deleted. They are enforced on load and in the rename/remove methods. The UI shows them with italic text and a lock icon on hover.

### BoardState (embedded in MatrixState)

```python
# New fields on MatrixState:
board_lanes: list[str]              # ordered lane names
board_tasks: dict[str, BoardTask]   # id → task
board_selected_lane: str            # currently visible lane (ephemeral, not persisted)
```

Default lanes on first load: `["Backlog", "To Do", "In Progress", "Done"]`

### Serialization

Board state is persisted alongside existing state in the same JSON file:

```json
{
  "agents": { ... },
  "groups": { ... },
  "group_settings": { ... },
  "board_lanes": ["Backlog", "To Do", "In Progress", "Done"],
  "board_tasks": {
    "a1b2c3d4": {
      "id": "a1b2c3d4",
      "task": "Fix login redirect",
      "group": "Backend",
      "instructions": "Check auth.py for the redirect logic.",
      "criteria": "Login redirects to /dashboard after success.",
      "lane": "In Progress",
      "position": 0,
      "assignee": "fix",
      "agent_id": "e5f6g7h8",
      "labels": ["bugfix", "urgent"]
    }
  }
}
```

---

## Server Commands

All commands are prefixed with `board_` and follow existing patterns.

### Mutation commands (mutate → broadcast)

| Command | Payload | Behavior |
|---|---|---|
| `board_add_task` | `{task, group, lane?, instructions?, context?, criteria?, assignee?, agent_id?, labels?}` | Create task in lane (default: Backlog). Position = end. Group required + must exist. |
| `board_update_task` | `{id, task?, group?, instructions?, context?, criteria?, assignee?, lane?, position?, agent_id?, labels?}` | Update task fields. If `lane` changes, reposition in new lane. |
| `board_remove_task` | `{id}` | Delete task. |
| `board_move_task` | `{id, lane, position?}` | Move task to lane at position (default: end). Reindex positions in both source and target lanes. |
| `board_add_lane` | `{name, position?}` | Add lane at position (default: end). |
| `board_rename_lane` | `{old_name, new_name}` | Rename lane. Update all tasks in that lane. |
| `board_remove_lane` | `{name, move_tasks_to?}` | Remove lane. Tasks in it move to `move_tasks_to` (default: first remaining lane). |
| `board_reorder_lanes` | `{lanes}` | Replace lane order with new list (for drag reorder). |
| `board_reorder_task` | `{id, position}` | Reorder task within its current lane. |

### Broadcast payload

The existing `broadcast()` sends `state.to_dict()`. We extend `to_dict()` to include:

```json
{
  "type": "state",
  "agents": { ... },
  "groups": { ... },
  "board_lanes": ["Backlog", "To Do", "In Progress", "Done"],
  "board_tasks": { ... }
}
```

`board_selected_lane` is client-side only — not persisted or broadcast.

---

## UI Design

### Taskbar

A fixed bar at the very bottom of the toolbelt. Contains "app buttons" — small icons/labels that toggle their panel open/closed.

```html
<div id="taskbar">
  <button class="taskbar-app active" data-app="board">📋 Board</button>
  <!-- future apps go here -->
</div>
```

Clicking an app button:
- If panel is collapsed → expand and show that app
- If panel is showing a different app → switch to that app
- If panel is already showing that app → collapse

### Bottom Panel

A section between `<main>` and the taskbar. Height is user-resizable via a drag handle at the top of the panel. Default height is 55vh. The panel content is rendered by the active app.

```html
<div id="bottom-panel" class="collapsed">
  <div class="panel-resize-handle" id="panel-resize-handle"></div>
  <div id="panel-board">
    <!-- rendered by board app -->
  </div>
  <!-- future app panels go here -->
</div>
```

The resize handle is a 5px drag target with a pill indicator. Dragging up/down resizes the panel (clamped to 80px–window height minus 80px). The height is stored in a CSS custom property (`--panel-height`) and persists for the session.

### Lane Tabs

Horizontal scrollable tab bar. Structure:

```
[<] [Backlog] [To Do] [In Progress] [Done] [>]  [+] [⚙]
```

- `<` / `>` scroll buttons fade in/out based on scroll position (use `opacity` + `pointer-events`, not `display`, to keep layout stable)
- Scroll buttons advance by exactly one tab: `>` reveals the first clipped tab on the right, `<` reveals the last clipped tab on the left
- Scroll position persists across re-renders; active tab auto-scrolls into view on lane switch
- `.board-lane-tabs` has `position: relative` so child `offsetLeft` is relative to the scroll container
- Selected lane tab has `.active` style
- Clicking an already-selected tab is a no-op (no re-render)
- Right-click on a lane tab shows Rename/Delete context menu
- `+` opens an inline input to name a new lane
- `⚙` opens a popover for the selected lane (Rename, Delete, future settings)

Clicking a lane tab switches the view to that lane. This is client-side only (no server roundtrip needed — the tasks are already in state).

### Task Cards

Each card in the lane list shows:

```
┌────────────────────────────────┐
│ ○ Fix login redirect       ⋮  │  ← title + context menu
│   🤖 fix-agent-a3b2            │  ← linked agent (if any)
└────────────────────────────────┘
```

- Left dot: color based on linked agent status (gray if unlinked)
- Title: clickable to edit inline
- `⋮` menu: Edit, Move to [lane], Link Agent, Unlink Agent, Delete
- Linked agent: small badge showing agent name. Clicking focuses the agent.
- Cards are draggable within the lane (reorder) and to lane tabs (move between lanes)
- `+ Add task` button at the bottom of the list, opens inline input

### Panel Collapse/Expand

- Default: collapsed (only taskbar visible)
- Expanded height: 20vh default, user-resizable via drag handle
- Open/closed state and height persist across daemon restarts via `board_panel_open` and `board_panel_height` fields in `MatrixState`
- On first WS connect, the panel is restored to its previous state

---

## Keyboard Shortcuts

When the board panel is focused:

| Key | Action |
|---|---|
| `K` | Toggle board panel (when not in a modal) |
| `←` / `→` | Switch lanes (when board is open) |
| `↑` / `↓` | Navigate cards within lane |
| `Enter` | Edit selected card |
| `Delete` | Remove selected card (with confirm) |

These bindings activate only when the bottom panel is focused. The existing group/agent navigation keeps working when the main area is focused.

---

## Provider Interface (not implemented, design only)

```python
# torque/providers/base.py

class TaskProvider(ABC):
    """Base class for external task providers."""

    name: str  # "jira", "linear", "github"

    @abstractmethod
    async def fetch_tasks(self, config: dict) -> list[dict]:
        """Fetch tasks from external system.
        Returns list of dicts matching BoardTask fields.
        Must include: external_id, title, description.
        May include: labels, external_url."""
        ...

    @abstractmethod
    async def push_update(self, task: BoardTask, config: dict) -> None:
        """Push task changes (lane/status, title, description) to external system."""
        ...

    @abstractmethod
    def map_lane_to_status(self, lane: str, config: dict) -> str | None:
        """Map a board lane name to a provider status.
        Returns None if no mapping (task won't be synced).
        Config contains user-defined lane↔status mappings."""
        ...

    @abstractmethod
    def map_status_to_lane(self, status: str, config: dict) -> str | None:
        """Map a provider status to a board lane name.
        Returns None if no mapping (task won't be imported)."""
        ...
```

Provider config lives in group settings (future):

```python
# Addition to GroupSettings (future)
task_provider: str | None = None           # "jira", "linear", etc.
task_provider_config: dict = field(default_factory=dict)  # provider-specific settings
task_lane_mapping: dict = field(default_factory=dict)      # lane_name → provider_status
```

This is design-only. No provider code ships in v1.

---

## Implementation Steps

### Step 1: Data model (`torque/state.py`)

- Add `BoardTask` dataclass
- Add `board_lanes` and `board_tasks` fields to `MatrixState`
- Initialize defaults: `board_lanes = ["Backlog", "To Do", "In Progress", "Done"]`, `board_tasks = {}`
- Add methods:
  - `board_add_task(title, lane, **kwargs) → BoardTask`
  - `board_update_task(id, **kwargs)`
  - `board_remove_task(id)`
  - `board_move_task(id, lane, position)`
  - `board_reorder_task(id, position)`
  - `board_add_lane(name, position)`
  - `board_rename_lane(old_name, new_name)`
  - `board_remove_lane(name, move_tasks_to)`
  - `board_reorder_lanes(lanes)`
- Update `to_dict()` to include board state
- Update `load()` to deserialize board state (with defaults for missing keys)

### Step 2: Server commands (`torque/server.py`)

- Add `board_*` command handlers in `handle_command()`
- Each handler: validate input → call `state.board_*()` → `await state.broadcast()`
- Error handling: unknown lane, duplicate lane name, task not found

### Step 3: Taskbar + bottom panel layout (`webview.html`, `static/style.css`)

- Add `#bottom-panel` and `#taskbar` to `webview.html`
- Restructure layout:
  - `<main>` gets `flex: 1; min-height: 0; overflow-y: auto`
  - `#bottom-panel` gets collapsible height
  - `#taskbar` is fixed at bottom
- CSS for panel states: `.collapsed` (hidden), `.expanded` (40% height)
- CSS for taskbar: horizontal flex, app buttons

### Step 4: Board rendering (`static/js/board.js`)

New file — the board "panel app":

- `renderBoard()` — renders lane tabs + card list into `#panel-board`
- `renderLaneTabs(lanes, selected)` — scrollable tab bar with `<` `>` `+` `⚙`
- `renderCards(tasks)` — sorted card list with `+ Add task`
- `renderTaskCard(task)` — individual card HTML
- Lane tab scroll logic: check overflow, show/hide arrows
- Client-side state: `_boardSelectedLane`, `_boardFocusedTask`

### Step 5: Board interactions (`static/js/board.js`)

- Click lane tab → switch lane (client-side only)
- Click `+` → inline input for new lane name → `send({cmd: 'board_add_lane', name})`
- Click `⚙` → popover with Rename/Delete for selected lane
- Click `+ Add task` → inline input → `send({cmd: 'board_add_task', title, lane})`
- Click card `⋮` → context menu: Edit, Move to [lane], Link Agent, Delete
- Click linked agent badge → `send({cmd: 'focus_agent', id})`
- Drag card within lane → `send({cmd: 'board_reorder_task', id, position})`
- Drag card to lane tab → `send({cmd: 'board_move_task', id, lane})`

### Step 6: Taskbar toggle logic (`static/js/main.js`)

- `togglePanel(appName)` — expand/collapse/switch panel
- Keyboard shortcut `K` — toggle board
- Track `_activePanelApp` state
- Update `render()` to call `renderBoard()` when board panel is visible

### Step 7: Agent-task linking

- Card context menu: "Link Agent" shows a list of agents from state → sets `agent_id`
- Card context menu: "Unlink Agent" clears `agent_id`
- Agent details panel: show linked task (if any) with a small badge
- When agent is removed: unlink from any tasks (don't delete the task)

### Step 8: CLI commands (`bin/torque`)

- `torque board` / `torque board list` — show all tasks grouped by lane
- `torque board add <title> [--lane LANE]` — add task
- `torque board move <id-or-title> --lane LANE` — move task
- `torque board rm <id-or-title>` — remove task
- `torque board lanes` — list lanes

---

## What We're NOT Building (Yet)

- **External provider sync** — The provider interface is designed but no adapters ship. Manual board only in v1.
- **Rich content rendering** — Task fields (instructions, context, criteria) are stored but only shown in the edit modal. No markdown rendering, attachments, or comments on cards.
- **Subtasks / checklists** — Flat task list only. Hierarchy is a future addition.
- **Filters / search** — With a narrow toolbelt and one-lane-at-a-time view, filtering is premature.
- **Due dates / priority** — Not in v1. Labels cover basic categorization.
- **Auto-assignment** — `assignee` prefix is stored but agents don't yet auto-pick up matching tickets.
- **Persistence across projects** — Board state is per-project (stored in the same state file). No global board.
- **Multiple boards** — One board per project. Multiple boards (e.g., per-group) are deferred.

---

## File Changes Summary

| File | Change |
|---|---|
| `torque/state.py` | Add `BoardTask` dataclass, board fields + methods on `MatrixState` |
| `torque/server.py` | Add `board_*` command handlers |
| `webview.html` | Add `#bottom-panel`, `#taskbar` containers |
| `static/style.css` | Taskbar, bottom panel, lane tabs, card styles |
| `static/js/board.js` | **New** — board panel app (render, interactions, drag) |
| `static/js/main.js` | Panel toggle logic, `K` shortcut, render integration |
| `static/js/ws.js` | Ensure board state flows through (already handled by `state = msg`) |
| `static/js/render.js` | Call `renderBoard()` after main render when panel is open |
| `bin/torque` | Add `board` subcommand |
| `docs/roadmap.md` | Update Phase 5 status |
