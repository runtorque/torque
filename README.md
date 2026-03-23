# Agent Matrix

An iTerm2 Toolbelt plugin for managing AI agent and terminal sessions in a visual grid.

If you work primarily in the terminal, you know how productive that environment can be. But running multiple AI agents alongside companion terminals quickly turns into tab chaos. Agent Matrix gives you a structured way to organize it all: **groups** for context, **agents** for AI sessions, and **child terminals** for supporting tasks — all managed from iTerm2's Toolbelt sidebar. Spin up an agent with its own isolated environment, attach terminals for tests or logs, do your work, and tear it all down when you're done.

For full documentation, see the [docs site](docs/).

## Features

- **Agent grid** — compact cells with status dots, one-click focus, drag-and-drop reordering
- **Terminal rows** — live process badge, working directory, and git branch tracking
- **Groups** — organize sessions by project; configurable defaults for directory, profile, shell, environment, and more
- **Group settings** — per-agent and per-terminal overrides, git worktree isolation, auto-create companion terminals, max agent caps
- **Quick add** — one click to create with defaults, or use the custom dialog for full control
- **Broadcast** — send a command to all sessions in a group at once
- **Global shortcuts** — navigate between agents from any tab with Cmd+Option+Arrow keys
- **Tab colors** — color-coded iTerm2 tabs for visual organization
- **Window filtering** — pin groups to windows where they have active sessions
- **Persistence** — groups, agents, and settings survive daemon restarts; relaunch stopped sessions with their original configuration

## Architecture

Two components communicate over a local WebSocket:

```
┌──────────────┐         ┌────────────────────────┐
│   Webview     │  WS     │  Python Daemon          │
│  (HTML/JS)    │◄───────►│  agent_matrix/          │
│  in Toolbelt  │ :18932  │                         │
└──────────────┘         │  MatrixState ──► JSON   │
                          │       │                  │
                          │       ▼                  │
                          │  iTerm2 Python API       │
                          │  (tabs, sessions,        │
                          │   monitors, profiles)    │
                          └────────────────────────┘
```

**Python daemon** — Long-running iTerm2 script. Manages state, runs an aiohttp HTTP + WebSocket server on `127.0.0.1:18932`, registers the Toolbelt webview, and bridges commands to the iTerm2 Python API.

**Webview UI** — Loaded by iTerm2's Toolbelt panel. Connects to the daemon via WebSocket, renders the agent grid, sends commands back. No build step, no framework — plain vanilla JS.

## Prerequisites

- macOS with [iTerm2](https://iterm2.com/) (version with Python API support)
- iTerm2 Python API enabled: **Preferences → General → Magic → Enable Python API**

## Install

```bash
git clone https://github.com/your-username/iterm2-agent-orchestration.git
cd iterm2-agent-orchestration

# Install aiohttp into iTerm2's Python environment
make deps

# Copy files to iTerm2 Scripts directory
make install

# (Optional) Auto-start with iTerm2
make autolaunch
```

Then:

1. **Scripts menu → iterm2-agent-orchestrator** to start
2. **View → Show Toolbelt** (Cmd+Shift+B)
3. **Toolbelt gear menu → check "Agent Matrix"**

## Usage

### Quick start

1. Click **+ Group** to create a group (e.g., "frontend")
2. Click **+ New** under Agents to spawn a Claude Code session
3. Click **+ New** under Terminals to open a plain shell
4. Click any cell/row to focus that tab in iTerm2

### Custom sessions

Click the **▾** dropdown next to "+ New" and select **Custom...** to configure:

- **Name** — display name in the matrix
- **Boot command** — command to run on launch (agents only, defaults to `claude`)
- **Directory** — "Current session", "Same as \<agent\>", or a custom path
- **Profile** — any iTerm2 profile (populated live from your config)
- **Tab color** — colored badge on the native iTerm2 tab bar

### Broadcast

Click the **⌘** button on a group header to open the broadcast bar. Text is sent to all live sessions in that group.

### Restart

Click **↻** in the header to restart the daemon in-place. State is preserved; active sessions are marked as stopped and can be relaunched.

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `AGENT_MATRIX_PORT` | `18932` | HTTP + WebSocket server port |
| `AGENT_MATRIX_DEFAULT_CMD` | `claude` | Default boot command for new agents |

## Make targets

| Target | Description |
|---|---|
| `make install` | Copy files to iTerm2 Scripts directory |
| `make deploy` | Stop running instance + install + prompt to restart |
| `make stop` | Kill any process on port 18932 |
| `make autolaunch` | Install + create auto-launch symlink |
| `make uninstall` | Remove installed files and autolaunch symlink |
| `make deps` | Install `aiohttp` into iTerm2's Python |
| `make run` | Launch directly via iTerm2's Python |
| `make check` | Print diagnostics (Python path, deps, install status) |

## File structure

```
agent_matrix.py              # Entry point (anchors paths, boots daemon)
agent_matrix/
  config.py                  # Env vars, paths, logging setup
  state.py                   # AgentCell, GroupSettings, MatrixState persistence
  bridge.py                  # iTerm2 bridge (sessions, monitors, worktrees)
  server.py                  # aiohttp server, WebSocket command handler
  keybindings.py             # Global iTerm2 key binding lifecycle
webview.html                 # Toolbelt UI shell (loads CSS + JS)
static/
  style.css                  # Dark theme styles
  js/
    constants.js             # Icon maps, process badges, color presets
    ws.js                    # WebSocket client, auto-reconnect
    render.js                # UI rendering (groups, agent cells, terminal rows)
    commands.js              # Actions (focus, remove, drag-drop, broadcast)
    modals.js                # Add/edit/settings dialogs, color picker, tooltips
    main.js                  # Keyboard bindings, boot
docs/                        # mkdocs documentation
Makefile                     # Install, deploy, stop, check targets
```

Auto-generated at runtime (in the install directory, not in the repo):
- `state.json` — persisted groups and agents
- `agent_matrix.log` — debug log

## Troubleshooting

**Nothing happens when I click buttons:**
An old daemon instance may be running. Run `make deploy` to kill it and install fresh, then restart from the Scripts menu.

**Log location:**
```
~/Library/Application\ Support/iTerm2/Scripts/iterm2-agent-orchestrator/iterm2-agent-orchestrator/agent_matrix.log
```

**Tab colors not visible:**
With the Minimal theme, tab colors appear as a thin colored line at the very top of the tab. Switch to the Regular theme for more prominent color badges.

**Port already in use:**
```bash
make stop    # kills whatever is on port 18932
```

## Dependencies

- `aiohttp` — HTTP + WebSocket server (installed into iTerm2's Python via `make deps`)
- `iterm2` — bundled with iTerm2
- No npm, no build tools. The webview is plain HTML/CSS/JS.

## License

MIT
