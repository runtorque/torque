# Loom

An iTerm2 Toolbelt plugin for managing AI agent and terminal sessions in a visual grid.

If you work primarily in the terminal, you know how productive that environment can be. But running multiple AI agents alongside companion terminals quickly turns into tab chaos. Loom gives you a structured way to organize it all: **groups** for context, **agents** for AI sessions, and **child terminals** for supporting tasks — all managed from iTerm2's Toolbelt sidebar. Spin up an agent with its own isolated environment, attach terminals for tests or logs, do your work, and tear it all down when you're done.

Loom also supports a **standalone mode** where the UI runs in a browser window instead of the Toolbelt. This is the foundation for supporting other terminal emulators (e.g. Ghostty) in the future.

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
┌──────────────────┐         ┌────────────────────────┐
│   Webview         │  WS     │  Python Daemon          │
│  (HTML/JS)        │◄───────►│  aiohttp server :18932  │
│  in Toolbelt      │         │  MatrixState ──► SQLite  │
│  or Browser       │         │       │                  │
└──────────────────┘         │       ▼                  │
                              │  iTerm2 Python API       │
                              │  (tabs, sessions,        │
                              │   monitors, profiles)    │
                              └────────────────────────┘
```

**Python daemon** — Long-running iTerm2 script. Manages state, runs an aiohttp HTTP + WebSocket server on `127.0.0.1:18932`, registers the Toolbelt webview, and bridges commands to the iTerm2 Python API. The terminal backend is abstracted behind a `TerminalAdapter` protocol, designed for future support of other terminals (e.g. Ghostty).

**Webview UI** — Loaded by iTerm2's Toolbelt panel. Connects to the daemon via WebSocket, renders the agent grid, sends commands back. No build step, no framework — plain vanilla JS. In standalone mode, the same UI is served to a browser window instead.

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

1. **Scripts menu → loom** to start the daemon
2. **View → Show Toolbelt** (Cmd+Shift+B)
3. **Toolbelt gear menu → check "Loom"**

### Standalone mode

Loom can also run as a standalone browser window instead of the Toolbelt. This exists primarily to support terminal emulators other than iTerm2 in the future.

```bash
make standalone    # starts daemon with LOOM_STANDALONE=1 (no Toolbelt)
make open          # opens http://127.0.0.1:18932/ in your browser
```

You can also use `make open` alongside the Toolbelt to get the UI in a wider browser window.

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

The same dropdown also surfaces saved **agent templates**, which let you reuse provider/model/system-prompt/worktree presets across groups and task dispatches.

### Broadcast

Click the **⌘** button on a group header to open the broadcast bar. Text is sent to all live sessions in that group.

### Restart

Click **↻** in the header to restart the daemon in-place. State is preserved; active sessions are marked as stopped and can be relaunched.

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `LOOM_PORT` | `18932` | HTTP + WebSocket server port |
| `LOOM_DEFAULT_CMD` | `claude` | Default boot command for new agents |
| `LOOM_STANDALONE` | (unset) | Set to `1` to skip Toolbelt registration (standalone mode) |
| `LOOM_BIND_ALL` | (unset) | Set to `1` to bind to `0.0.0.0` instead of `127.0.0.1` |

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
| `make standalone` | Launch in standalone mode (no Toolbelt) |
| `make open` | Open the UI in the default browser |
| `make check` | Print diagnostics (Python path, deps, install status) |

## File structure

```
loom.py              # Entry point (anchors paths, boots daemon)
loom/
  config.py                  # Env vars, paths, mode flags, logging setup
  terminal_adapter.py        # TerminalAdapter protocol (backend abstraction)
  state.py                   # AgentCell, GroupSettings, MatrixState persistence
  bridge.py                  # ITerm2Adapter (sessions, monitors, worktrees)
  server.py                  # aiohttp server, WebSocket command handler
  keybindings.py             # Global iTerm2 key binding lifecycle
webview.html                 # UI shell (loads CSS + JS)
static/
  style.css                  # Dark theme styles + responsive breakpoints
  js/
    constants.js             # Icon maps, process badges, color presets
    ws.js                    # WebSocket client, auto-reconnect
    render.js                # UI rendering (groups, agent cells, terminal rows)
    commands.js              # Actions (focus, remove, drag-drop, broadcast)
    modals.js                # Add/edit/settings dialogs, color picker, tooltips
    main.js                  # Keyboard bindings, boot
docs/                        # mkdocs documentation
Makefile                     # Install, deploy, stop, standalone, open targets
```

Auto-generated at runtime (in the install directory, not in the repo):
- `loom.db` — SQLite state database
- `loom.log` — debug log

## Troubleshooting

**Nothing happens when I click buttons:**
An old daemon instance may be running. Run `make deploy` to kill it and install fresh, then restart from the Scripts menu.

**Log location:**
```
~/Library/Application\ Support/iTerm2/Scripts/loom/loom/loom.log
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
