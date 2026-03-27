# CLAUDE.md

## Project overview

Loom is an iTerm2 Toolbelt plugin that manages AI agent and terminal sessions in a visual grid. It's a Python daemon (aiohttp server) that communicates with a webview UI over a local WebSocket.

## Key commands

```bash
make deploy     # Stop old instance, install files, then restart from Scripts menu
make stop       # Kill running instance on port 18932
make check      # Show Python path, dep status, install status
```

After `make deploy`, always restart from: **iTerm2 → Scripts menu → loom**

## Architecture

- **Entry point**: `loom.py` — thin wrapper that anchors paths and calls `iterm2.run_forever(main)`
- **Python package** (`loom/`):
  - `config.py` — env vars, paths, logging setup
  - `state.py` — `AgentCell` dataclass (with `parent_id` for terminal→agent hierarchy), `GroupSettings` dataclass (with `dispatch_lane`), `BoardTask` dataclass (task, group, instructions, context, criteria, assignee, labels, agent_id, lane, position), `MatrixState` (persistence, `_children` index, cascade delete, WS broadcast). Reserved lanes (`Backlog`, `In Progress`) are enforced — cannot be renamed or deleted.
  - `templates.py` — `TemplateManager` (Jinja2 rendering, variable auto-discovery from AST, lenient/strict parse modes, `render_template` returns flat dict with `task`, `group`, `instructions`, `context`, `criteria`, `labels`, `worktree`, `terminals`). Templates use `task:` (not `prompt:`) for the main text field. Backward compat: old templates with `prompt:` still work.
  - `bridge.py` — `ITerm2Bridge` (create/close/focus/update sessions, tab color, per-window tab reorder, PromptMonitor, VariableMonitor for jobName+path, git branch resolution, SessionTerminationMonitor, FocusMonitor for window/session tracking, orphan reconnection, `on_session_terminated` callback)
  - `worktree.py` — `WorktreeManager` (create/remove/validate worktrees, checkpoint/count_commits/list_checkpoints/rollback, diff_summary, is_merged, .gitignore management). Worktrees live in `.loom/worktrees/` in the repo root, branches named `loom/{agent-name}-{short-id}`
  - `server.py` — `main()`, aiohttp routes (`/` serves webview, `/ws` WebSocket, `/events` hook receiver, `/static/*` assets), all command dispatch, periodic worktree diff updater, `render_template` command (renders template fields without creating an agent, used by board "From template" flow)
  - `events.py` — `EventBus` (throttled broadcast, `on_session_end` callback for auto-checkpoint), `EventLog` (per-cell ring buffer), `health_check` (30s periodic scan)
  - `notifications.py` — `NotificationManager` (macOS notifications via osascript, 5s batching window)
  - `keybindings.py` — global iTerm2 key binding lifecycle (RPC registration, install/remove bindings, Cmd+Option+Arrow for cell/agent nav, Cmd+Shift+B for broadcast)
  - `adapters/` — provider-agnostic agent awareness:
    - `base.py` — `AgentEvent` dataclass, `AgentAdapter` base class
    - `claude_code.py` — full integration: HTTP hooks (command hook for SessionStart), event parsing, activity inference, hook install/uninstall, session resume
    - `codex.py` — template (process matching only)
    - `gemini_cli.py` — template (process matching only)
    - `generic.py` — fallback (process monitoring only)
- **Webview** (`webview.html` + `static/`):
  - `static/style.css` — all styles (dark theme, narrow toolbelt layout)
  - `static/js/constants.js` — icon maps, process badge map, tab color presets, feature flags (`FILTER_BY_WINDOW`), agent type labels
  - `static/js/ws.js` — WebSocket client, shared `state`, auto-reconnect, `selectedAgentId`, `focusedItemId`, `dragInProgress`, tab-focus sync, action messages
  - `static/js/render.js` — `render()`, agent cells (three-state status dot: gray/green/red, activity detail, type label, worktree branch badge with diff stats), terminal drawer, FLIP animation, group collapse, per-group window filtering, `_navItems`/`_navAgents` lists for keyboard navigation
  - `static/js/commands.js` — agent click/dblclick, focus, remove (cascade-aware), drag-and-drop (agents, terminals, groups, reparent), broadcast, right-click context menu (with worktree ops: Create/Checkpoint/Remove Worktree)
  - `static/js/modals.js` — add group/agent/terminal modals (with `parent_id` support), edit agent/terminal modal, group settings modal (tabbed: Group/Agents/Terminals; Agents tab has boot command, worktree config, session resume, idle timeout, notifications), confirm dialog, color picker, hint tooltip popovers, task create/edit modal (task, group, assignee, instructions, context, criteria, labels), template-to-task flow (`openTaskFromTemplate` → `render_template` → pre-fills task modal)
  - `static/js/main.js` — keyboard navigation (arrows within group, Tab/Shift+Tab between groups, Enter, Delete, N/G/T/B/R shortcuts), boot, drag setup

## Code conventions

- Python: no framework beyond aiohttp + iterm2. All state mutations go through `MatrixState` methods which call `self.save()`. Every iTerm2 API error must be caught and logged (never bare `except: pass`).
- JS: no build step, no framework. Eight plain script files loaded in order (constants → ws → render → commands → modals → board → templates → main). All functions are global. State is re-rendered from scratch on every WS message.
- CSS: single file, CSS custom properties for theming, monospace font throughout.
- `window.confirm()` and `window.alert()` do not work in iTerm2's WKWebView — use the custom `showConfirm()` modal instead.

## iTerm2 API gotchas

- **Tab color**: Must set all three variants (`set_tab_color`, `set_tab_color_light`, `set_tab_color_dark`) plus their `set_use_tab_color*` flags — otherwise color won't show depending on user's light/dark mode settings.
- **Tab reorder**: Use `window.async_set_tabs(list)` with the full tab list in desired order. No per-tab move API.
- **PromptMonitor**: Only fires when shell integration is active. For TUI apps (Claude Code, vim), the prompt won't fire until the app exits.
- **VariableMonitor**: `jobName` and `path` are session-scoped variables. Monitor triggers on change, not on initial value — seed with `async_get_variable()` first.
- **Port conflicts**: If the daemon crashes on start, an old instance is likely still holding port 18932. Run `make stop` first.
- **Restart**: `os.execv(sys.executable, [sys.executable] + sys.argv)` replaces the process in-place. Must set `reuse_address=True` on the TCPSite so the new process can bind immediately.
- **Global key bindings**: `async_set_global_key_bindings` is replace-all — must read→merge→write. Displaced user bindings are saved and restored on shutdown. On crash, stale `loom_*` bindings are cleaned up on next startup.
- **RPC invocation**: `INVOKE_SCRIPT_FUNCTION` param format is `"function_name()"`. RPCs are registered via `@iterm2.RPC` decorator + `async_register(connection)`. Arrow key bindings require the `FUNCTION` modifier flag alongside other modifiers.
- **Menu shortcut precedence**: iTerm2's built-in menu shortcuts (e.g., Cmd+Shift+Left/Right for Move Tab) override global key bindings. Use Cmd+Option for arrow bindings to avoid conflicts.
- **Right-click menus**: `window.confirm()` and native context menus don't work in WKWebView — use custom `contextmenu` event handlers with positioned `<div>` elements instead.

## Claude Code hooks gotchas

- **SessionStart only supports command hooks**: `SessionStart`, `WorktreeCreate`, and `WorktreeRemove` events only work with `type: "command"` hooks. HTTP hooks (`type: "http"`) are silently ignored for these events. Use a `curl`-based command hook instead.
- **allowedEnvVars required for HTTP headers**: HTTP hooks that interpolate env vars in headers (e.g., `"X-Loom-Cell-Id": "$LOOM_CELL_ID"`) must list those vars in `allowedEnvVars`. Without it, the variable is not interpolated and the header is empty.
- **Hook config location**: Claude Code reads `.claude/settings.local.json` relative to the project root (git root). Loom writes hooks there using a merge strategy — Loom hooks are identified by their URL (`localhost:18932/events`) and can be added/removed without affecting user hooks.
- **Session ID**: Available in all hook payloads as the top-level `session_id` field. Persisted by Loom for `claude --resume` on relaunch. A `/clear` command generates a new session ID.

## Testing changes

There are no automated tests. To test changes:
1. `make deploy`
2. Restart from Scripts menu
3. Check `loom.log` in the install dir for errors:
   ```
   ~/Library/Application\ Support/iTerm2/Scripts/loom/loom/loom.log
   ```
4. Test in the Toolbelt panel: create groups, add agents, click agent to select → add child terminals, drag terminals between drawer and standalone, remove agent (cascade), relaunch, broadcast, right-click → edit name/color, keyboard nav (arrows within group, Tab between groups, Cmd+Option+Arrows from terminal)

## Install location

Files are installed to:
```
~/Library/Application Support/iTerm2/Scripts/loom/loom/
```
This is an iTerm2 "full environment" script project with its own bundled Python 3.14 at:
```
~/Library/Application Support/iTerm2/Scripts/loom/iterm2env/versions/3.14.0/bin/python3
```
