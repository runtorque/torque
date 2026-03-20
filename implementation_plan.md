# Agent Matrix — iTerm2 Toolbelt Plugin Implementation Plan

## What This Is

An iTerm2 Toolbelt plugin that displays terminal tabs in a matrix/grid layout, grouped by project or purpose. The primary use case is managing multiple AI agent sessions (Claude Code, Aider, etc.) simultaneously. It lives in iTerm2's right-side Toolbelt panel as a webview.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│ iTerm2 Window                                            │
├────────────────────────────────────┬─────────────────────┤
│                                    │ ◉ AGENT MATRIX      │
│                                    │ ┌───┬───┬───┐       │
│   Active terminal session          │ │ A1│ A2│ A3│ Grp A │
│   (whichever agent you clicked)    │ └───┴───┴───┘       │
│                                    │ ┌───┬───┐           │
│                                    │ │ B1│ B2│     Grp B │
│                                    │ └───┴───┘           │
│                                    │ ┌───┬───┬───┬───┐   │
│                                    │ │ C1│ C2│ C3│ C4│ C │
│                                    │ └───┴───┴───┴───┘   │
├────────────────────────────────────┴─────────────────────┤
│ Status bar                                               │
└──────────────────────────────────────────────────────────┘
```

Two components communicate over a local WebSocket:

```
┌──────────────┐         ┌──────────────────────────┐
│   Webview     │  WS     │  Python Daemon            │
│  (HTML/JS)    │◄───────►│  agent_matrix.py          │
│  in Toolbelt  │ :18932  │                           │
└──────────────┘         │  ┌──────────────────────┐  │
                          │  │ MatrixState           │  │
                          │  │ - groups, agents      │  │
                          │  │ - persisted to JSON   │  │
                          │  └──────────────────────┘  │
                          │          │                  │
                          │          ▼                  │
                          │  ┌──────────────────────┐  │
                          │  │ iTerm2 Python API     │  │
                          │  │ - create/close tabs   │  │
                          │  │ - focus sessions      │  │
                          │  │ - send text           │  │
                          │  │ - monitor prompts     │  │
                          │  └──────────────────────┘  │
                          └──────────────────────────────┘
```

1. **Python daemon** (`agent_matrix.py`) — Long-running iTerm2 script. Manages state, runs a local aiohttp HTTP + WebSocket server on `127.0.0.1:18932`, registers the Toolbelt webview, and bridges commands to the iTerm2 Python API.

2. **Webview UI** (`webview.html`) — Single-file HTML/CSS/JS loaded by iTerm2's Toolbelt. Connects to the daemon via WebSocket, renders the agent grid, sends commands back. No build step, no framework — plain vanilla JS.

## Prior Art & Validation

Only two existing projects use the `async_register_web_view_tool` API:

- **Targeted Input** — iTerm2's official example. aiohttp + webview, sends text to broadcast domain sessions.
- **Command Queue** (github.com/LogicTortoise/iterm2-plugins) — Toolbelt webview with aiohttp + WebSocket for queuing commands per session.

Both use the exact same pattern this project will use. The architecture is validated but the space is virtually empty — this would be the only agent orchestration tool for iTerm2.

## Key iTerm2 Python API surfaces to use

| API | Purpose |
|-----|---------|
| `iterm2.tool.async_register_web_view_tool()` | Register the Toolbelt webview panel |
| `Window.async_create_tab(profile, command)` | Create a new tab for an agent |
| `Tab.async_activate()` | Switch to a tab when user clicks a cell |
| `Session.async_activate()` | Focus a specific session/pane |
| `Session.async_send_text(text)` | Send commands to agent sessions |
| `Session.async_close(force=True)` | Close an agent session |
| `Tab.async_set_title(title)` | Label tabs with `[Group] AgentName` |
| `PromptMonitor` | Detect idle/busy state per session |
| `iterm2.run_forever(main)` | Keep daemon alive |

The Python API must be enabled in Preferences → General → Magic → Enable Python API.

## Dependencies

- `aiohttp` — HTTP + WebSocket server (install into iTerm2's bundled Python env)
- `iterm2` — Already bundled with iTerm2
- No other dependencies. The webview is plain HTML/JS with no npm/build step.

Install aiohttp:
```bash
# Find iTerm2's Python
ls ~/.config/iterm2/AppSupport/iterm2env*/versions/*/bin/python3
# Install into it
~/.config/iterm2/AppSupport/iterm2env-*/versions/*/bin/python3 -m pip install aiohttp
```

## File Structure

```
agent_matrix/
├── agent_matrix.py      # Python daemon (iTerm2 script)
├── webview.html          # Toolbelt UI (served via localhost)
├── state.json            # Persisted groups/agents (auto-created)
└── README.md
```

Installed to: `~/Library/Application Support/iTerm2/Scripts/agent_matrix/`

## Implementation Tasks

### Phase 1: Core Daemon

File: `agent_matrix.py`

1. **State management**
   - `AgentCell` dataclass: `id`, `name`, `group`, `session_id`, `profile`, `command`, `status` (idle/running/error/stopped)
   - `MatrixState` class: dict of agents by id, dict of groups → agent id lists, active session tracking, websocket client set
   - Persistence: save/load state to `state.json` next to the script. Save on every mutation. On load, don't try to reconnect to old session IDs (they won't survive an iTerm2 restart) — mark those agents as `stopped` and let the user re-launch them.

2. **aiohttp server**
   - Bind to `127.0.0.1:18932` (localhost only, no auth needed)
   - `GET /` → serve `webview.html`
   - `GET /ws` → WebSocket endpoint
   - On WS connect: send full state snapshot as JSON
   - On WS message: parse command, execute, broadcast updated state to all clients

3. **WebSocket command protocol** (JSON messages from webview → daemon)

   | Command | Payload | Action |
   |---------|---------|--------|
   | `refresh` | — | Re-send full state |
   | `add_group` | `{group}` | Create empty group |
   | `remove_group` | `{group}` | Close all sessions in group, remove it |
   | `rename_group` | `{group, new_name}` | Rename a group |
   | `add_agent` | `{name, group, profile?, command?}` | Create agent + iTerm2 tab |
   | `remove_agent` | `{id}` | Close iTerm2 session, remove from state |
   | `focus_agent` | `{id}` | Activate that agent's tab + session |
   | `send_text` | `{id, text}` | Send text to a specific agent's session |
   | `broadcast_to_group` | `{group, text}` | Send text to all agents in a group |
   | `relaunch_agent` | `{id}` | Create a new tab for a stopped agent, re-run its command |
   | `move_agent` | `{id, target_group}` | Move agent between groups |

4. **iTerm2 integration helpers**
   - `_create_session_for_agent(agent)` — Create tab in current window, set title to `[GroupName] AgentName`, optionally send boot command, store `session_id`
   - `_focus_session(session_id)` — Iterate windows/tabs/sessions to find and activate
   - `_close_session(session_id)` — Find and force-close
   - `_send_text(session_id, text)` — Find session and send text

5. **Session status monitoring**
   - On agent creation, start a `PromptMonitor` for the session
   - When prompt appears → status = `idle`
   - When command starts → status = `running`
   - If session closes unexpectedly → status = `stopped`
   - Broadcast state changes to webview on every status transition

6. **Toolbelt registration**
   ```python
   await iterm2.tool.async_register_web_view_tool(
       connection,
       display_name="Agent Matrix",
       identifier="com.agentmatrix.toolbelt",
       reveal_if_already_registered=True,
       url=f"http://127.0.0.1:{WS_PORT}/",
   )
   ```

7. **Entry point**
   ```python
   iterm2.run_forever(main)
   ```

### Phase 2: Webview UI

File: `webview.html` — single file, no build step, no framework.

1. **Design constraints**
   - The Toolbelt panel is narrow (~250–300px). Everything must work at this width.
   - Dark theme to match iTerm2's Minimal theme. Monospace font.
   - Grid cells should be compact: ~70–90px wide, showing an icon + agent name + status dot.
   - Responsive grid: `grid-template-columns: repeat(auto-fill, minmax(70px, 1fr))`

2. **Layout (top to bottom)**
   - **Sticky header**: connection status dot, "AGENT MATRIX" title, `+ Group` and `+ Agent` buttons
   - **Group cards**: one card per group, each with:
     - Group header: name, agent count, broadcast button (⌘), remove button (✕)
     - Matrix grid of agent cells inside
   - **Broadcast bar** (bottom, shown on demand): target label, text input, send button, close button
   - **Empty state**: shown when no groups exist

3. **Agent cell**
   - Status indicator dot (top-right corner):
     - Gray = idle
     - Green + pulse animation = running
     - Red = error
     - Amber = stopped
   - Icon (geometric shape — ⬡ ◈ ◆ ▣ etc.)
   - Agent name (truncated with ellipsis)
   - Close button (✕) visible on hover, top-left
   - Active cell gets accent border + subtle glow
   - Click → sends `focus_agent` command

4. **Dialogs** (overlay modals)
   - **Add Group**: text input for group name
   - **Add Agent**: name input, group dropdown, optional boot command input

5. **WebSocket client**
   - Connect to `ws://127.0.0.1:18932/ws`
   - On open: set connection dot to green
   - On message: parse state snapshot, re-render entire UI
   - On close: set connection dot to red, auto-reconnect every 2 seconds
   - Send commands as JSON: `ws.send(JSON.stringify({cmd: "...", ...}))`

6. **Interactions**
   - Click cell → focus agent
   - Hover cell → show close button
   - Click close → remove agent (with iTerm2 session close)
   - Click group ⌘ → open broadcast bar scoped to that group
   - Click group ✕ → confirm, then remove group + all agents
   - Escape key → close any open overlay or broadcast bar

### Phase 3: Persistence

- On every state mutation (add/remove agent, add/remove group, status change), serialize state to `state.json` next to `agent_matrix.py`.
- On daemon startup, load `state.json` if it exists. Mark all agents with stale `session_id`s as `stopped` (since iTerm2 sessions don't survive restarts).
- User can "relaunch" stopped agents, which creates a fresh tab and re-runs the original boot command.

### Phase 4: Polish & Edge Cases

- **Orphan detection**: On startup, scan all existing iTerm2 sessions and try to match them to persisted agents by tab title pattern `[Group] Name`. Re-link if found.
- **Drag-and-drop reorder**: Allow dragging cells between groups in the webview (HTML5 drag API), send `move_agent` command.
- **Group collapse**: Allow collapsing/expanding groups to save vertical space.
- **Keyboard navigation**: Arrow keys to move between cells, Enter to focus, Delete to remove.
- **Tab color**: Set tab color per group using `Session.async_set_profile_property` so agents are visually grouped even in the native tab bar.

## Installation Script

Include a small shell helper:

```bash
#!/bin/bash
# install.sh
SCRIPTS_DIR="$HOME/Library/Application Support/iTerm2/Scripts"
DEST="$SCRIPTS_DIR/agent_matrix"

mkdir -p "$DEST"
cp agent_matrix.py webview.html "$DEST/"

# Auto-launch on iTerm2 start
mkdir -p "$SCRIPTS_DIR/AutoLaunch"
ln -sf "$DEST/agent_matrix.py" "$SCRIPTS_DIR/AutoLaunch/agent_matrix.py"

echo "Installed to $DEST"
echo ""
echo "Next steps:"
echo "  1. Install aiohttp into iTerm2's Python:"
echo "     ls ~/.config/iterm2/AppSupport/iterm2env*/versions/*/bin/python3"
echo "     <that_path>/python3 -m pip install aiohttp"
echo "  2. Enable Python API: Preferences → General → Magic → Enable Python API"
echo "  3. Run: Scripts menu → agent_matrix"
echo "  4. Show Toolbelt: View → Show Toolbelt (⌘⇧B)"
echo "  5. Check 'Agent Matrix' in the Toolbelt gear menu"
```

## Testing Checklist

- [ ] Daemon starts and registers toolbelt webview
- [ ] Webview connects via WebSocket and shows empty state
- [ ] Create a group → appears in UI
- [ ] Add an agent with boot command → tab created in iTerm2, cell appears with running status
- [ ] Click agent cell → iTerm2 switches to that tab
- [ ] Remove agent → iTerm2 session closes, cell disappears
- [ ] Broadcast to group → text sent to all sessions in group
- [ ] Remove group → all sessions closed, group removed
- [ ] Restart daemon → state loads from JSON, agents marked as stopped
- [ ] Relaunch stopped agent → new tab created with original command
- [ ] PromptMonitor correctly transitions between idle/running
- [ ] Multiple webview clients stay in sync (e.g., if Toolbelt is open in two windows)
- [ ] Webview reconnects automatically after daemon restart
