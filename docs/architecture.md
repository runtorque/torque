# Architecture

Loom is a local orchestration system built around a long-running Python daemon and a lightweight web UI.

## Major Components

### Python daemon

The daemon:

- manages groups, agents, terminals, tasks, schedules, and engineer state
- exposes HTTP and WebSocket endpoints
- talks to iTerm2 through the Python API
- persists state in SQLite
- handles action rendering, task dispatch, worktrees, hooks, and MCP tools

Primary modules:

- `loom/server.py`
- `loom/state.py`
- `loom/db.py`
- `loom/bridge.py`
- `loom/actions.py`
- `loom/worktree.py`
- `loom/mcp.py`
- `loom/mcp_engineer.py`

### Frontend

The UI is plain HTML, CSS, and JavaScript with no build step. The same frontend runs in:

- the iTerm2 Toolbelt webview
- a browser window in dual mode
- a browser window in standalone-only mode

The frontend consumes a full snapshot on connect and then live delta updates over WebSocket.

### SQLite persistence

SQLite is the source of truth for persistent state, including:

- agents and groups
- group settings
- board tasks and lanes
- schedules
- engineer settings and journal
- agent history

This is also what lets some CLI reads work without the daemon.

## High-Level Data Flow

```text
UI or CLI action
  -> daemon command handler
  -> state mutation
  -> SQLite write
  -> WebSocket delta broadcast
  -> UI re-render
```

For CLI writes, `bin/loom` sends `POST /api/cmd` requests. For many CLI reads, `bin/loom` reads SQLite directly.

## Session Control

Loom abstracts terminal control behind a terminal adapter interface. Today the concrete implementation is iTerm2-focused, but the design supports additional backends.

Key session responsibilities:

- create/focus/close tabs
- monitor prompt, process, and path changes
- reconnect to existing sessions
- apply tab colors and ordering

## Action and Task Model

Loom separates:

- **agent templates**: who should do the work
- **actions**: what the work prompt should say
- **board tasks**: the concrete tracked unit of work

During dispatch, Loom resolves settings, renders the action prompt with task and Loom context, links the task to an agent, and appends the reporting postscript that enables `loom ai` status transitions.

## Worktrees

When enabled, Loom creates one git worktree per agent. The worktree manager handles:

- creation
- validation
- diff summary
- checkpoints
- rollback
- merge status

See [Worktrees](worktrees.md) for the user workflow.

## Engineer and MCP

Loom exposes two MCP-oriented surfaces:

- agent-facing Loom tools for reporting progress and deriving work
- engineer-facing orchestration tools for board control, journaling, notifications, agent messaging, and worktree operations

The engineer is implemented as a special per-group agent with persistent instructions and a journal-backed recovery path.

## Runtime Modes

The same daemon can serve:

- Toolbelt only
- Toolbelt plus browser
- browser only

That works because the UI is already served over HTTP/WebSocket and is not tied to iTerm2-specific browser APIs.

## Related Docs

- [Operations](operations.md)
- [Actions & Templates](actions.md)
- [Engineer](engineer.md)
- [Plans Archive](plans/index.md)
