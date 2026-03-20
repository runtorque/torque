# Agent Matrix — iTerm2 Toolbelt Plugin Implementation Plan

## What This Is

An iTerm2 Toolbelt plugin that displays terminal tabs in a matrix/grid layout, grouped by project or purpose. The primary use case is managing multiple AI agent sessions (Claude Code, Aider, etc.) and plain terminal sessions simultaneously. It lives in iTerm2's right-side Toolbelt panel as a webview.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│ iTerm2 Window                                            │
├────────────────────────────────┬─────────────────────────┤
│                                │ ● AGENT MATRIX    ↻ +Grp│
│                                │                         │
│  Active terminal session       │ AGENTS                  │
│  (whichever tab you clicked)   │ ┌───┬───┬───┐           │
│                                │ │ A1│ A2│ A3│           │
│                                │ └───┴───┴───┘           │
│                                │ [+ New ▾]               │
│                                │                         │
│                                │ TERMINALS               │
│                                │ [❯_] dev    main|myapp  │
│                                │ [SSH] prod  ~/logs      │
│                                │ [+ New ▾]               │
├────────────────────────────────┴─────────────────────────┤
│ Status bar                                               │
└──────────────────────────────────────────────────────────┘
```

Two components communicate over a local WebSocket:

```
┌──────────────┐         ┌──────────────────────────┐
│   Webview     │  WS     │  Python Daemon            │
│  (HTML/JS)    │◄───────►│  agent_matrix/            │
│  in Toolbelt  │ :18932  │                           │
└──────────────┘         │  MatrixState ──► JSON     │
                          │       │                    │
                          │       ▼                    │
                          │  iTerm2 Python API         │
                          │  - create/close tabs       │
                          │  - focus sessions          │
                          │  - send text               │
                          │  - monitor prompts/vars    │
                          │  - reorder tabs            │
                          │  - set tab colors          │
                          │  - query profiles          │
                          └──────────────────────────────┘
```

1. **Python daemon** (`agent_matrix.py` + `agent_matrix/` package) — Long-running iTerm2 script. Manages state, runs a local aiohttp HTTP + WebSocket server on `127.0.0.1:18932`, registers the Toolbelt webview, and bridges commands to the iTerm2 Python API.

2. **Webview UI** (`webview.html` + `static/`) — HTML shell loaded by iTerm2's Toolbelt. Connects to the daemon via WebSocket, renders the UI, sends commands back. No build step, no framework — plain vanilla JS split across six script files.

## Prior Art & Validation

Only two existing projects use the `async_register_web_view_tool` API:

- **Targeted Input** — iTerm2's official example. aiohttp + webview, sends text to broadcast domain sessions.
- **Command Queue** (github.com/LogicTortoise/iterm2-plugins) — Toolbelt webview with aiohttp + WebSocket for queuing commands per session.

Both use the exact same pattern this project uses. The architecture is validated but the space is virtually empty — this is the only agent orchestration tool for iTerm2.

## Key iTerm2 Python API Surfaces

| API | Purpose |
|-----|---------|
| `iterm2.tool.async_register_web_view_tool()` | Register the Toolbelt webview panel |
| `Window.async_create_tab(profile)` | Create a new tab for an agent/terminal |
| `Window.async_set_tabs(tabs_list)` | Reorder tabs within a window |
| `Tab.async_activate()` | Switch to a tab when user clicks a cell |
| `Tab.async_set_title(title)` | Label tabs with `[Group] Name` |
| `Session.async_activate()` | Focus a specific session/pane |
| `Session.async_send_text(text)` | Send commands to sessions |
| `Session.async_close(force=True)` | Close a session |
| `Session.async_get_variable(name)` | Read session variables (path, jobName, profileName) |
| `Session.async_set_profile_properties(profile)` | Set tab color via `LocalWriteOnlyProfile` |
| `PromptMonitor` | Detect idle/busy state per session |
| `VariableMonitor` | Watch jobName and path changes in real time |
| `SessionTerminationMonitor` | Detect when any session closes |
| `FocusMonitor` | Track current window and active session in real time |
| `PartialProfile.async_query()` | List all available iTerm2 profiles |
| `iterm2.run_forever(main)` | Keep daemon alive |

The Python API must be enabled in Preferences → General → Magic → Enable Python API.

## Dependencies

- `aiohttp` — HTTP + WebSocket server (install into iTerm2's bundled Python env via `make deps`)
- `iterm2` — Already bundled with iTerm2
- No other dependencies. The webview is plain HTML/CSS/JS with no npm/build step.

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `AGENT_MATRIX_PORT` | `18932` | TCP port for the HTTP + WebSocket server |
| `AGENT_MATRIX_DEFAULT_CMD` | `claude` | Default boot command for new agents |

| JS constant | Default | Description |
|---|---|---|
| `FILTER_BY_WINDOW` | `true` | Only show agents/terminals belonging to the current iTerm2 window |

## File Structure

```
agent_matrix.py                  # Entry point (anchors paths, boots daemon)
agent_matrix/
  __init__.py
  config.py                      # Env vars, paths, logging setup
  state.py                       # AgentCell dataclass, MatrixState persistence
  bridge.py                      # iTerm2 bridge (sessions, monitors, tab reorder, tab color)
  server.py                      # aiohttp server, WebSocket commands, toolbelt registration
webview.html                     # Toolbelt UI shell (loads CSS + JS)
static/
  style.css                      # Dark theme styles
  js/
    constants.js                 # Icon maps, process badge map, tab color presets, feature flags
    ws.js                        # WebSocket client, auto-reconnect, shared state
    render.js                    # UI rendering (groups, agent cells, terminal rows, FLIP animation, collapse, window filter)
    commands.js                  # Actions (focus, remove, broadcast, restart, drag-and-drop)
    modals.js                    # Add/confirm dialogs, color picker, directory selector
    main.js                      # Keyboard bindings, boot
Makefile                         # install, deploy, stop, check, deps targets
README.md                        # User-facing documentation
CLAUDE.md                        # Developer context for AI assistants
```

Auto-generated at runtime (in the install directory):
- `state.json` — persisted groups and agents
- `agent_matrix.log` — debug/error log

Installed to: `~/Library/Application Support/iTerm2/Scripts/iterm2-agent-orchestrator/iterm2-agent-orchestrator/`

---

## Implementation Status

### Phase 1: Core Daemon — DONE

1. **State management** — DONE
   - `AgentCell` dataclass with fields: `id`, `name`, `group`, `cell_type` (agent/terminal), `session_id`, `profile`, `command`, `directory`, `tab_color`, `window_id`, `status`, `current_process`, `current_path`, `current_branch`, `git_root`
   - `MatrixState` class: agents dict, groups dict (ordered, preserves insertion), active session tracking, current window tracking, WS client set
   - Persistence to `state.json` on every mutation. On load, agents marked as `stopped` with ephemeral fields cleared. `session_id` and `window_id` are preserved for orphan reconnection.

2. **aiohttp server** — DONE
   - Binds to `127.0.0.1:18932` with `reuse_address=True`
   - `GET /` → serves `webview.html`
   - `GET /ws` → WebSocket endpoint
   - `GET /static/*` → serves CSS and JS assets
   - On WS connect: sends full state snapshot
   - On WS message: parses command, executes, broadcasts updated state

3. **WebSocket command protocol** — DONE

   | Command | Payload | Action |
   |---------|---------|--------|
   | `refresh` | — | Re-send full state |
   | `add_group` | `{group}` | Create empty group |
   | `remove_group` | `{group}` | Close all sessions in group, remove it |
   | `rename_group` | `{group, new_name}` | Rename a group |
   | `add_agent` | `{name, group, profile?, command?, directory?, tab_color?}` | Create agent + iTerm2 tab |
   | `add_terminal` | `{name, group, profile?, directory?, tab_color?}` | Create terminal + iTerm2 tab |
   | `remove_agent` | `{id}` | Close iTerm2 session, remove from state |
   | `focus_agent` | `{id}` | Activate that agent's tab + session |
   | `send_text` | `{id, text}` | Send text to a specific session |
   | `broadcast_to_group` | `{group, text}` | Send text to all sessions in a group |
   | `relaunch_agent` | `{id}` | Create a new tab for a stopped agent, re-run its command |
   | `move_agent` | `{id, target_group, before?}` | Move/reorder agent (insert before ID, or append) |
   | `move_group` | `{group, before?}` | Reorder group (insert before name, or append) |
   | `get_config` | `{group}` | Return profiles, current session info, group cell paths |
   | `restart` | — | Save state, `os.execv` to re-exec the daemon in-place |

   Server → client message types:
   | Type | Description |
   |------|-------------|
   | `state` | Full state snapshot (agents, groups, active_session_id, current_window_id) |
   | `config` | Response to `get_config` (profiles, current_path, current_profile, group_cells) |
   | `error` | Exception message from a failed command |

4. **iTerm2 integration** — DONE
   - `create_session()` — Create tab, set title, set tab color (all three light/dark variants), cd to directory, send boot command, seed terminal variables, start monitors, store `window_id`, reorder tabs
   - `focus_session()` — Find and activate tab + session (searches all windows)
   - `close_session()` — Find and force-close
   - `send_text()` — Find session and send text
   - `reorder_tabs()` — Keep managed tabs last in each window, sorted by group then position. Iterates all windows with managed tabs independently.

5. **Session status monitoring** — DONE
   - `PromptMonitor` per session: detects shell prompt → marks `idle`
   - `SessionTerminationMonitor` (global): detects closed sessions → marks `stopped`, clears ephemeral fields, cancels monitor tasks
   - `VariableMonitor` for `jobName` (terminals): tracks foreground process in real time
   - `VariableMonitor` for `path` (terminals): tracks working directory in real time, triggers git info resolution on every change
   - `FocusMonitor` (global): tracks current window (`current_window_id`) and active session (`active_session_id`) in real time, broadcasts on change

6. **Git integration** — DONE
   - On every path change, runs `git -C <path> rev-parse --show-toplevel --abbrev-ref HEAD` asynchronously
   - Stores `current_branch` and `git_root` on the cell
   - Webview displays `branch | repo-name/relative/path` for terminals in git repos

7. **Tab colors** — DONE
   - Sets all six profile properties: `set_use_tab_color` + `set_tab_color` for default, light, and dark mode variants
   - Uses `LocalWriteOnlyProfile` + `session.async_set_profile_properties()`
   - Eight preset swatches in the UI (red, orange, yellow, green, teal, blue, purple, pink)

8. **Toolbelt registration** — DONE

9. **Logging** — DONE
   - File-based logging to `agent_matrix.log`
   - Every WS command logged with parameters
   - All iTerm2 API errors logged with full tracebacks (no silent swallowing)
   - Startup, server bind, toolbelt registration logged
   - Port-in-use error detection with helpful message

10. **In-place restart** — DONE
    - `os.execv(sys.executable, [sys.executable] + sys.argv)` replaces the process
    - State saved before restart, loaded on boot
    - `reuse_address=True` on TCPSite for immediate port rebind
    - Webview auto-reconnects after the brief disconnection

### Phase 2: Webview UI — DONE

Split across `webview.html` (shell) + `static/style.css` + `static/js/*.js` (6 files).

1. **Design** — DONE
   - Dark theme (CSS custom properties), monospace font, narrow toolbelt layout (~250-300px)
   - Responsive agent grid: `repeat(auto-fill, minmax(72px, 1fr))`
   - Terminal rows: horizontal layout with process badge, name, path, status dot, action buttons

2. **Layout** — DONE
   - Sticky header: connection dot, "AGENT MATRIX" title, restart button (↻), "+ Group" button
   - Group cards with: name, count badge, broadcast button (⌘), remove button (✕)
   - Two sections per group (always shown): Agents (grid) and Terminals (rows)
   - Split "New" button per section: main click for quick-add (auto-named), dropdown chevron for "Custom..." modal
   - Broadcast bar (slides up from bottom, scoped to a group)
   - Empty state when no groups exist

3. **Agent cells** — DONE
   - Geometric icon (hash-based: ⬡ ◈ ◆ ▣ etc.)
   - Status dot (gray=idle, green+pulse=running, red=error, amber=stopped)
   - Name (truncated with ellipsis)
   - Close button (✕) on hover
   - Relaunch button when stopped
   - Active cell: accent border + glow

4. **Terminal rows** — DONE
   - Color-coded process badge (50+ process mappings: shells, editors, languages, devops, AI tools)
   - Name + working directory (git-aware: `main | repo-name/path` or `~/path`)
   - Status dot, close/relaunch buttons on hover

5. **Modals** — DONE
   - **Add Group**: name input
   - **Add Agent/Terminal** (shared, async): name, group dropdown, boot command (agents only), directory selector (current session / same as other cell / custom path), profile dropdown (populated live from iTerm2), tab color swatches
   - **Confirm dialog**: custom replacement for `window.confirm()` which is blocked in WKWebView

6. **WebSocket client** — DONE
   - Auto-reconnect every 2 seconds on disconnect
   - Connection status dot (green/red)
   - Handles `state`, `config`, and `error` message types
   - Full UI re-render on every state snapshot

7. **Interactions** — DONE
   - Click cell/row → focus agent
   - Hover → show close button
   - Escape → close any modal, broadcast bar, or dropdown menu
   - Enter → submit from modal inputs
   - Click outside modal → close

### Phase 3: Persistence — DONE

- State saved to `state.json` on every mutation
- On daemon startup: load state, mark all agents as `stopped`, clear ephemeral fields
- Relaunch creates a fresh tab with the original command, directory, and tab color
- `directory` and `tab_color` are persisted so they survive restarts

### Phase 4: Polish & Edge Cases — DONE

| Feature | Status |
|---------|--------|
| Tab reordering (managed tabs last, sorted by group, all windows) | DONE |
| Tab colors (per session, all light/dark variants) | DONE |
| In-place daemon restart from UI | DONE |
| File logging with full error tracebacks | DONE |
| Code split: Python into 4 modules, JS into 6 files | DONE |
| Orphan detection (re-link sessions on restart by session_id + tab title) | DONE |
| Drag-and-drop reorder (agents, terminals, and groups) | DONE |
| FLIP animation on drag-and-drop (smooth position transitions) | DONE |
| Group collapse/expand (CSS grid-template-rows animation) | DONE |
| Window awareness (window_id per cell, FocusMonitor, per-window filtering) | DONE |
| Active session tracking via FocusMonitor | DONE |
| Keyboard navigation (arrows, enter, delete) | NOT STARTED |

### Future Ideas

- **Environment variables** — inject key=value pairs before the boot command
- **Auto-restart** — automatically relaunch agents that exit unexpectedly
- **Shell selector** — choose bash/zsh/fish for terminal sessions
- **Keyboard navigation** — arrow keys to move between cells, Enter to focus, Delete to remove

## Testing Checklist

- [x] Daemon starts and registers toolbelt webview
- [x] Webview connects via WebSocket and shows empty state
- [x] Create a group → appears in UI
- [x] Add an agent with boot command → tab created in iTerm2, cell appears with running status
- [x] Add a terminal → tab created, process badge and path shown
- [x] Click agent cell → iTerm2 switches to that tab
- [x] Remove agent → confirm dialog, iTerm2 session closes, cell disappears
- [x] Broadcast to group → text sent to all sessions in group
- [x] Remove group → confirm dialog, all sessions closed, group removed
- [x] Restart daemon → state loads from JSON, agents marked as stopped
- [x] Relaunch stopped agent → new tab created with original command and settings
- [x] PromptMonitor correctly transitions between idle/running
- [x] Terminal process badge updates in real time (e.g., launch vim → badge changes)
- [x] Terminal path updates in real time on cd, shows git branch and repo name
- [x] Tab color set correctly (all light/dark variants)
- [x] Tabs reordered automatically (managed tabs last)
- [x] Quick add (+ New) creates with auto-generated name
- [x] Custom modal populated with profiles, directory options, color swatches
- [x] Webview reconnects automatically after daemon restart
- [x] Multiple webview clients stay in sync
- [x] Log file captures all commands and errors
- [x] Daemon restart re-links running sessions (orphan detection)
- [x] Orphan reconnection matches by session_id (primary) and tab title (secondary)
- [x] Drag agent cells to reorder within or between groups
- [x] Drag terminal rows to reorder within or between groups
- [x] Drag group headers to reorder groups
- [x] FLIP animation smoothly slides items to new positions after drop
- [x] Drop indicators (inset box-shadow) appear without layout shift
- [x] Renders deferred during drag (dragInProgress flag) to prevent DOM destruction
- [x] Collapse/expand groups via chevron toggle with smooth animation
- [x] Collapsed state preserved across WS reconnects
- [x] Window ID stored on each agent at creation and on orphan reconnect
- [x] FocusMonitor tracks current window and active session in real time
- [x] Active cell/row highlight updates when switching tabs
- [x] FILTER_BY_WINDOW hides agents not in current window when enabled
- [x] Empty groups hidden when window filter is active
- [x] Tab reordering works across all windows independently

## Makefile Targets

| Target | Description |
|---|---|
| `make install` | Copy all files to iTerm2 Scripts directory |
| `make deploy` | Stop running instance + install + prompt to restart |
| `make stop` | Kill any process on port 18932 |
| `make autolaunch` | Install + create auto-launch symlink |
| `make uninstall` | Remove installed files and autolaunch symlink |
| `make deps` | Install `aiohttp` into iTerm2's Python |
| `make run` | Launch directly via iTerm2's Python |
| `make check` | Print diagnostics (Python path, deps, install status) |
