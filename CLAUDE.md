# CLAUDE.md

## Project overview

Agent Matrix is an iTerm2 Toolbelt plugin that manages AI agent and terminal sessions in a visual grid. It's a Python daemon (aiohttp server) that communicates with a webview UI over a local WebSocket.

## Key commands

```bash
make deploy     # Stop old instance, install files, then restart from Scripts menu
make stop       # Kill running instance on port 18932
make check      # Show Python path, dep status, install status
```

After `make deploy`, always restart from: **iTerm2 → Scripts menu → iterm2-agent-orchestrator**

## Architecture

- **Entry point**: `agent_matrix.py` — thin wrapper that anchors paths and calls `iterm2.run_forever(main)`
- **Python package** (`agent_matrix/`):
  - `config.py` — env vars, paths, logging setup
  - `state.py` — `AgentCell` dataclass (with `parent_id` for terminal→agent hierarchy), `MatrixState` (persistence, `_children` index, cascade delete, WS broadcast)
  - `bridge.py` — `ITerm2Bridge` (create/close/focus sessions, tab color, per-window tab reorder, PromptMonitor, VariableMonitor for jobName+path, git branch resolution, SessionTerminationMonitor, FocusMonitor for window/session tracking, orphan reconnection)
  - `server.py` — `main()`, aiohttp routes (`/` serves webview, `/ws` WebSocket, `/static/*` assets), all command dispatch
- **Webview** (`webview.html` + `static/`):
  - `static/style.css` — all styles (dark theme, narrow toolbelt layout)
  - `static/js/constants.js` — icon maps, process badge map, tab color presets, feature flags (`FILTER_BY_WINDOW`)
  - `static/js/ws.js` — WebSocket client, shared `state`, auto-reconnect, `selectedAgentId`, `dragInProgress`
  - `static/js/render.js` — `render()`, agent cells, terminal drawer, FLIP animation, group collapse, window filtering
  - `static/js/commands.js` — agent click/dblclick, focus, remove (cascade-aware), drag-and-drop (agents, terminals, groups, reparent), broadcast
  - `static/js/modals.js` — add group/agent/terminal modals (with `parent_id` support), confirm dialog, color picker
  - `static/js/main.js` — keyboard bindings, boot, drag setup

## Code conventions

- Python: no framework beyond aiohttp + iterm2. All state mutations go through `MatrixState` methods which call `self.save()`. Every iTerm2 API error must be caught and logged (never bare `except: pass`).
- JS: no build step, no framework. Six plain script files loaded in order (constants → ws → render → commands → modals → main). All functions are global. State is re-rendered from scratch on every WS message.
- CSS: single file, CSS custom properties for theming, monospace font throughout.
- `window.confirm()` and `window.alert()` do not work in iTerm2's WKWebView — use the custom `showConfirm()` modal instead.

## iTerm2 API gotchas

- **Tab color**: Must set all three variants (`set_tab_color`, `set_tab_color_light`, `set_tab_color_dark`) plus their `set_use_tab_color*` flags — otherwise color won't show depending on user's light/dark mode settings.
- **Tab reorder**: Use `window.async_set_tabs(list)` with the full tab list in desired order. No per-tab move API.
- **PromptMonitor**: Only fires when shell integration is active. For TUI apps (Claude Code, vim), the prompt won't fire until the app exits.
- **VariableMonitor**: `jobName` and `path` are session-scoped variables. Monitor triggers on change, not on initial value — seed with `async_get_variable()` first.
- **Port conflicts**: If the daemon crashes on start, an old instance is likely still holding port 18932. Run `make stop` first.
- **Restart**: `os.execv(sys.executable, [sys.executable] + sys.argv)` replaces the process in-place. Must set `reuse_address=True` on the TCPSite so the new process can bind immediately.

## Testing changes

There are no automated tests. To test changes:
1. `make deploy`
2. Restart from Scripts menu
3. Check `agent_matrix.log` in the install dir for errors:
   ```
   ~/Library/Application\ Support/iTerm2/Scripts/iterm2-agent-orchestrator/iterm2-agent-orchestrator/agent_matrix.log
   ```
4. Test in the Toolbelt panel: create groups, add agents, click agent to select → add child terminals, drag terminals between drawer and standalone, remove agent (cascade), relaunch, broadcast

## Install location

Files are installed to:
```
~/Library/Application Support/iTerm2/Scripts/iterm2-agent-orchestrator/iterm2-agent-orchestrator/
```
This is an iTerm2 "full environment" script project with its own bundled Python 3.14 at:
```
~/Library/Application Support/iTerm2/Scripts/iterm2-agent-orchestrator/iterm2env/versions/3.14.0/bin/python3
```
