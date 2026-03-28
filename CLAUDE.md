# CLAUDE.md

## Project overview

Loom is a terminal session manager for AI agents. It's a Python daemon (aiohttp server) that communicates with one or more webview UI clients over a local WebSocket. It can run inside iTerm2's Toolbelt sidebar, as a standalone browser/desktop window, or both simultaneously (dual mode). The terminal backend is abstracted behind a `TerminalAdapter` protocol; the current implementation (`ITerm2Adapter` in `bridge.py`) controls iTerm2.

## Key commands

```bash
make deploy     # Stop old instance, install files, then restart from Scripts menu
make stop       # Kill running instance on port 18932
make check      # Show Python path, dep status, install status
make open       # Open standalone UI in default browser (dual mode)
make standalone # Launch daemon in standalone-only mode (no toolbelt)
```

After `make deploy`, always restart from: **iTerm2 → Scripts menu → loom**
For dual mode, also run `make open` to get a browser window alongside the toolbelt.

## Architecture

- **Entry point**: `loom.py` — thin wrapper that anchors paths and calls `iterm2.run_forever(main, retry=STANDALONE)`. In standalone mode, `retry=True` waits for iTerm2 and reconnects on restart.
- **Python package** (`loom/`):
  - `config.py` — env vars, paths (`STATE_FILE`, `DB_FILE`, `WEBVIEW_FILE`, `LOG_FILE`), mode flags (`STANDALONE`, `BIND_HOST`), logging setup
  - `db.py` — `LoomDB` (SQLite persistence layer, WAL mode, schema with 8 tables: `agents`, `groups`, `group_members`, `group_settings`, `board_tasks`, `board_lanes`, `ui_state`, `global_settings`). Targeted write methods (`save_agent`, `save_board_task`, `save_global_settings`, etc.), `load_all`, `migrate_from_json` (one-time state.json→SQLite migration), `save_all` (bulk write, used only by migration)
  - `state.py` — `AgentCell` dataclass (with `slug` auto-generated from name, `parent_id` for terminal→agent hierarchy), `GroupSettings` dataclass (with `dispatch_lane`), `GlobalSettings` dataclass (app-wide: `default_command`, `filter_by_window`, `focus_new_tabs`, `default_lanes`, `keybindings`), `BoardTask` dataclass (with `slug` auto-generated from task text; task, group, template_name, template_vars, assignee, labels, agent_id, lane, position), `MatrixState` (SQLite persistence via `LoomDB`, `group_slugs` dict, `_children` index, cascade delete, delta WS broadcast, `_slugify`/`_unique_slug` helpers for slug generation). `get_default_command()` resolves boot command priority: global settings → `LOOM_DEFAULT_CMD` env var → `"claude"`. Slugs are unique per resource type (agents+terminals share one namespace, groups another, tasks another). Agent slugs are derived from the agent name (e.g. `my-agent`). Terminal slugs are prefixed with their parent agent's slug using `:` separator (e.g. `my-agent:logs`); standalone terminals use the group slug as prefix. Slugs are regenerated on rename/reparent and cascaded to children when a parent agent is renamed. Reserved lanes (`Backlog`, `In Progress`) are enforced — cannot be renamed or deleted. Delta accumulator (`_emit`, `_delta_ops`, `_seq`) tracks changes per mutation; `broadcast()` sends only deltas, `snapshot_msg()` sends full state on connect/resync.
  - `templates.py` — `TemplateManager` (Jinja2 rendering, variable auto-discovery from AST, lenient/strict parse modes, `render_template` returns flat dict with `prompt`, `group`, `labels`, `worktree`, `terminals` + agent settings, `render_prompt` returns rendered prompt string for dispatch/preview, `validate_prompt` enforces `{{ TASK }}` requirement, `_coalesce_prompt` merges old-format fields into single prompt). Templates use `prompt:` for the main text field. All templates must contain `{{ TASK }}`. Backward compat: old templates with `task:`+`instructions:`+`context:`+`criteria:` are auto-coalesced into `prompt` on load.
  - `terminal_adapter.py` — `TerminalAdapter` Protocol defining the terminal backend interface. Implemented by `ITerm2Adapter`; designed for future Ghostty adapter.
  - `bridge.py` — `ITerm2Adapter` (implements `TerminalAdapter`; create/close/focus/update sessions, tab color, per-window tab reorder, PromptMonitor, VariableMonitor for jobName+path, git branch resolution, SessionTerminationMonitor, FocusMonitor for window/session tracking, orphan reconnection, `on_session_terminated` callback)
  - `worktree.py` — `WorktreeManager` (create/remove/validate worktrees, checkpoint/count_commits/list_checkpoints/rollback, diff_summary, is_merged, check_base_advanced, .gitignore management). Worktrees live in `.loom/worktrees/` in the repo root, branches named `loom/{agent-name}-{short-id}`. Merge detection: `is_merged` uses ancestry check (regular merges) then `git merge-tree --write-tree` simulation (squash merges). `check_base_advanced` is a fallback for squash merges with overlapping changes — verifies the base branch advanced and its new commits touch all files the branch changed.
  - `server.py` — `main()`, aiohttp routes (`/` serves webview, `/ws` WebSocket, `/events` hook receiver, `/static/*` assets), conditional toolbelt registration (skipped when `STANDALONE`), configurable bind address (`BIND_HOST`), all command dispatch, periodic worktree diff updater, `render_template` command (renders template for preview), `preview_prompt` command (renders full dispatch prompt for a task), `save_template` / `delete_template` commands (CRUD for template YAML files, scope-aware: project `.loom/templates/` or user `~/.loom/templates/`, validates `{{ TASK }}` on save), `get_global_settings` / `update_global_settings` commands (keybinding changes trigger `keybindings.reinstall()` at runtime), `dispatch_task` command (creates or reuses an agent, links task, moves to dispatch lane — if task has `template_name`, renders template prompt with `{TASK: task.task, **template_vars}`; if template missing, returns `dispatch_template_missing` warning; `force_no_template` flag bypasses; legacy fallback for old tasks with instructions/context/criteria — new agents get 2s boot delay before send; postscript appends `loom ai` reporting instructions), `ai_report` command (single handler for all `loom ai` actions — updates agent cell ephemeral fields + linked board task labels/lane/external_url atomically; actions: done, blocked, pr, merged, error, progress, ready)
  - `events.py` — `EventBus` (throttled broadcast, `on_session_end` callback for auto-checkpoint), `EventLog` (per-cell ring buffer), `health_check` (30s periodic scan)
  - `notifications.py` — `NotificationManager` (macOS notifications via osascript, 5s batching window)
  - `keybindings.py` — global iTerm2 key binding lifecycle (RPC registration, install/remove/reinstall bindings). Default bindings: Cmd+Option+Arrow for cell/agent nav, Cmd+Shift+B for broadcast. Bindings are configurable via global settings (`_ACTION_DEFAULTS` dict, `_resolve_binding_specs` merges overrides). `reinstall()` swaps bindings at runtime when settings change. `get_default_bindings()` exports defaults for the frontend keybinding editor.
  - `adapters/` — provider-agnostic agent awareness:
    - `base.py` — `AgentEvent` dataclass, `AgentAdapter` base class
    - `claude_code.py` — full integration: HTTP hooks (command hook for SessionStart), event parsing, activity inference, hook install/uninstall, session resume
    - `codex.py` — template (process matching only)
    - `gemini_cli.py` — template (process matching only)
    - `generic.py` — fallback (process monitoring only)
- **Webview** (`webview.html` + `static/`):
  - `static/style.css` — all styles (dark theme, narrow toolbelt layout, responsive breakpoints at 400/600/900px for standalone/dual mode)
  - `static/js/constants.js` — icon maps, process badge map, tab color presets, `getFilterByWindow()` (reads from `state.global_settings`, falls back to `true`), agent type labels
  - `static/js/ws.js` — WebSocket client, shared `state`, auto-reconnect, `selectedAgentId`, `focusedItemId`, `dragInProgress`, tab-focus sync, action messages, delta patching (`_applyDelta`, `_rebuildChildren`, `_expectedSeq` sequence tracking, `resync` on gap)
  - `static/js/render.js` — `render()`, agent cells (three-state status dot: gray/green/red, activity detail, type label, worktree branch badge with diff stats), terminal drawer, FLIP animation, group collapse, per-group window filtering, `_navItems`/`_navAgents` lists for keyboard navigation
  - `static/js/commands.js` — agent click/dblclick, focus, remove (cascade-aware), drag-and-drop (agents, terminals, groups, reparent), broadcast, right-click context menu (with worktree ops: Create/Checkpoint/Remove Worktree)
  - `static/js/modals.js` — add group/agent/terminal modals (with `parent_id` support), edit agent/terminal modal, group settings modal (tabbed: Group/Agents/Terminals; Agents tab has boot command, worktree config, session resume, idle timeout, notifications), global settings modal (tabbed: General/Keybindings; General has Server/Board sub-tabs; Keybindings tab has interactive key capture for rebinding), confirm dialog, color picker, hint tooltip popovers, task create/edit modal (auto-growing textarea for task, group, template picker, dynamic template variable textareas in fieldset, assignee, labels, preview prompt button), template-to-task flow (`openTaskFromTemplate` → pre-selects template in task modal), prompt preview modal (scrollable, max-height)
  - `static/js/board.js` — board panel app (lane tabs, task cards with template badge, drag-and-drop reorder/move). Inline task creation: `+ Add task` row at top with auto-growing textarea (draft preserved across blur, Enter submits, Shift+Enter newline, Escape clears). `From template` button opens an inline overlay dropdown (grouped by Project/User, closes on outside click) — picking a template opens the task modal with it pre-selected. Card context menu with viewport overflow adjustment. Dispatch flow with missing-template warning dialog.
  - `static/js/templates.js` — templates panel app (dropdown selector with Project/User optgroups, structured editor form with single `prompt` field with Jinja2 syntax highlighting, `{{ TASK }}` validation on save, save/duplicate/delete, scope picker, auto-discovered variables display)
  - `static/js/main.js` — keyboard navigation (arrows within group, Tab/Shift+Tab between groups, Enter, Delete, N/G/T/B/R shortcuts), panel toggle (board + templates), boot, drag setup
- **CLI** (`bin/loom`):
  - Standalone Python script (stdlib + PyYAML). Talks to daemon via HTTP `POST /api/cmd` for writes, reads SQLite directly for read-only commands (task/board list, show, lanes).
  - `get_state_local(port)` — tries SQLite first (`~/Library/Application Support/iTerm2/Scripts/loom/loom/loom.db`), falls back to HTTP daemon. Used by all task/board commands.
  - Task commands: `create` (with `--template`/`--var`), `dispatch`, `show`, `list`, `edit` (opens `$EDITOR` with YAML or inline flags; `--template`/`--var` replace old `-i`/`-x`/`-k`), `move`, `assign`
  - Board commands: `list`, `add`, `move`, `remove`, `lanes`
  - AI agent reporting (`loom ai`): `done` (mark task complete, move to Done), `blocked "reason"` (set needs_attention, add `blocked` label), `pr URL` (store PR URL on task, add `pr:open` label; `--draft` for `pr:draft`), `merged` (move task to Done, set `pr:merged`), `error "msg"` (set error_message, add `error` label), `progress "msg"` (update activity_detail), `ready` (done + unlink agent), `context` (read-only dump of agent/task info, works offline). All `loom ai` commands auto-detect the calling agent via `LOOM_CELL_ID` env var and auto-resolve the linked board task. Dispatched prompts include a `loom ai` instruction block as postscript. Server-side: single `ai_report` command handles all actions atomically. Label conventions: `pr:open`, `pr:draft`, `pr:merged`, `blocked`, `error` — exclusive within `pr:` prefix.
  - `resolve_cell(state_data, identifier)` — resolves agent/terminal by ID, slug, name (case-insensitive), or ID prefix. Slug match takes priority over name match.
  - `resolve_group(state_data, identifier)` — resolves group by exact name, slug, or case-insensitive name
  - `resolve_task(state_data, identifier)` — resolves task by ID, slug, ID prefix, or title match with ambiguity handling

## Persistence

- **SQLite** (`loom.db`) is the persistence layer, using WAL mode for concurrent daemon writes + CLI reads.
- **Ephemeral fields** (activity, activity_detail, last_event_at, session_tokens_in/out, error_message, needs_attention, last_summary, current_process, current_path, current_branch, git_root, worktree_dirty, worktree_diff, worktree_checkpoints) live in-memory only — not persisted to SQLite, cleared on restart.
- **Delta broadcasts**: Every mutation calls `_emit()` to queue a delta op. `broadcast()` sends `{"type": "delta", "seq": N, "ops": [...]}` to WS clients. Full state (`snapshot_msg()`) is sent only on initial WS connect or `resync` command. 12 delta op types: `agent_upsert`, `agent_remove`, `group_update`, `group_remove`, `group_rename`, `groups_reorder`, `group_settings_update`, `global_settings_update`, `task_upsert`, `task_remove`, `lanes_update`, `ui_update`, `focus_update`.
- **Slugs**: Every agent, terminal, group, and board task has a `slug` column persisted in SQLite. Slugs are auto-generated from the resource name via `_slugify()` and are unique per resource type. On startup, any resource missing a slug (or a terminal with an old-format slug lacking `:`) gets one generated automatically. The CLI accepts slugs as identifiers everywhere IDs or names are accepted.
- **Migration**: On first startup, if `loom.db` is empty but `state.json` exists, data is imported automatically and `state.json` is renamed to `state.json.bak`. Schema migrations (e.g. adding `slug`, `template_name`, `template_vars` columns) use `ALTER TABLE ... ADD COLUMN` in `LoomDB.init()`, guarded by try/except. `template_vars` is stored as JSON text in SQLite, decoded on load.

## Code conventions

- Python: no framework beyond aiohttp + iterm2. All state mutations go through `MatrixState` methods which call `self._emit()` (to queue a delta) then targeted `self._db_save_*()` methods (to persist only the changed rows). External callers (bridge, events, server) that mutate cell fields directly must call `state._emit_agent(cell)` then `state._db_save_agent(cell)` — skip the DB write for ephemeral-only changes (activity, process, path). Every iTerm2 API error must be caught and logged (never bare `except: pass`).
- JS: no build step, no framework. Eight plain script files loaded in order (constants → ws → render → commands → modals → board → templates → main). All functions are global. State is patched in-place from WebSocket delta messages, then re-rendered. Full state is only received on initial connect or after a resync.
- CSS: single file, CSS custom properties for theming, monospace font throughout.
- `window.confirm()` and `window.alert()` do not work in iTerm2's WKWebView — use the custom `showConfirm()` modal instead.

## iTerm2 API gotchas

- **Tab color**: Must set all three variants (`set_tab_color`, `set_tab_color_light`, `set_tab_color_dark`) plus their `set_use_tab_color*` flags — otherwise color won't show depending on user's light/dark mode settings.
- **Tab reorder**: Use `window.async_set_tabs(list)` with the full tab list in desired order. No per-tab move API.
- **PromptMonitor**: Only fires when shell integration is active. For TUI apps (Claude Code, vim), the prompt won't fire until the app exits.
- **VariableMonitor**: `jobName` and `path` are session-scoped variables. Monitor triggers on change, not on initial value — seed with `async_get_variable()` first.
- **Port conflicts**: If the daemon crashes on start, an old instance is likely still holding port 18932. Run `make stop` first.
- **Restart**: `os.execv(sys.executable, [sys.executable] + sys.argv)` replaces the process in-place. Must set `reuse_address=True` on the TCPSite so the new process can bind immediately.
- **Global key bindings**: `async_set_global_key_bindings` is replace-all — must read→merge→write. Displaced user bindings are saved and restored on shutdown. On crash, stale `loom_*` bindings are cleaned up on next startup. Bindings are configurable via global settings — `keybindings.reinstall()` swaps them at runtime without re-registering RPCs.
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
5. Test global settings: click gear icon → Settings modal. General > Server: change default command, toggle filter by window, toggle focus new tabs (uncheck → create agent → verify previous tab keeps focus). General > Board: edit default lanes. Keybindings: rebind a key combo, verify it takes effect immediately. Save → restart daemon → confirm settings persist.
   - Test inline task creation: click `+ Add task` → auto-growing textarea appears. Type and Enter → task created. Escape → clears draft. Blur → draft preserved, clicking `+ Add task` again restores text.
   - Test `From template` button: click → overlay dropdown with Project/User groups. Click outside → dismisses. Pick a template → dropdown closes, task modal opens with template pre-selected and variable fields in fieldset. Preview → shows rendered prompt with variables substituted (scrollable).
   - Test task modal: task textarea auto-grows. Template variable textareas auto-grow. Edit task → template picker pre-selects stored template, vars pre-filled.
   - Test dispatch: task with template → agent receives rendered prompt. Delete template → dispatch → warning dialog → dispatch without template works.
   - Test template editor: open Templates panel → old-format templates should show coalesced `prompt` field. Create new template → `{{ TASK }}` validation on save. Edit prompt → variables auto-discovered below.
6. Verify SQLite persistence: restart daemon, confirm state survives. Check `loom.db` exists in install dir.
7. Verify delta sync: open browser devtools → Network → WS tab, confirm messages are `type: "delta"` (not full state) after initial connect.
8. Verify CLI offline reads: `make stop`, then `loom task list` — should work without daemon running.
9. Test dual mode: with daemon running from Scripts menu, `make open` → browser window should show same state as toolbelt. Actions in either UI should be reflected in both.
10. Test standalone mode: `make stop`, then `make standalone` → `make open` → daemon connects to iTerm2 externally, no toolbelt panel registered. Check log for "Standalone mode — toolbelt registration skipped".

## Install location

Files are installed to:
```
~/Library/Application Support/iTerm2/Scripts/loom/loom/
```
Runtime data (created by the daemon, not installed by `make deploy`):
```
~/Library/Application Support/iTerm2/Scripts/loom/loom/loom.db    # SQLite state
~/Library/Application Support/iTerm2/Scripts/loom/loom/loom.log   # daemon log
```
This is an iTerm2 "full environment" script project with its own bundled Python 3.14 at:
```
~/Library/Application Support/iTerm2/Scripts/loom/iterm2env/versions/3.14.0/bin/python3
```
