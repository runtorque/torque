# PTY Supervisor Visualization Panel Sketch

## Scope summary

Build a read-only, task-manager-style view of the standalone PTY supervisor sessions. The bounded MVP should visualize what `PtySupervisorClient.list_sessions()` can report today, enriched with the owning Torque cell from `MatrixState`, without changing `torque/pty_supervisor.py`'s protocol and without adding management actions such as kill/restart.

**Approval path:** Engineer approval is enough for the bounded read-only viewer described here. Human/product approval is required if implementation expands into session management controls, supervisor protocol changes, output-buffer inspection, or a broader "managed daemons" registry.

## Current architecture findings

- Standalone mode starts the sidecar in `torque/server.py` via `pty_supervisor.ensure_running(DATA_DIR)` and then uses `SupervisedPtyAdapter` (`torque/local_pty.py`).
- `SupervisedPtyAdapter` owns daemon-side session bookkeeping but delegates PTY create/write/resize/close/list/subscribe to `PtySupervisorClient` (`torque/pty_supervisor_client.py`).
- `PtySupervisor` (`torque/pty_supervisor.py`) owns PTY master FDs and child shell processes. It is standalone-only; iTerm2/toolbelt mode does not use it.
- Sessions are created for Torque standalone terminal/agent cells. The supervisor starts the shell (`shell_argv`) and the daemon then sends startup commands (hooks, env sourcing, `cell.command`) into that PTY. Therefore the supervisor does **not** directly know the provider command as a first-class field.
- Exited sessions remain in the supervisor registry only briefly (`_drop_after(..., 5.0)`), so "Exited" rows are transient unless we add persistence/protocol changes later.

## What `pty_supervisor_client.list_sessions()` exposes today

`PtySupervisorClient.list_sessions()` sends `op: "list"` and returns `sessions` from each `SupervisorSession.snapshot()`.

| Field | Present today? | Notes for UI |
|---|---:|---|
| `session_id` | yes | Stable PTY supervisor session id; can match `AgentCell.session_id`. |
| `cell_id` | yes | Owning Torque cell id as supplied at create. Enrich from `state.agents[cell_id]` for name/group/kind/status. |
| `pid` | yes | PID of the shell process spawned under the PTY. Child AI CLIs under that shell are not tracked separately. |
| `alive` | yes | `true` until session finalization. `false` can appear for only ~5 seconds before removal. |
| `cols`, `rows` | yes | Last known PTY size. |
| `total_bytes` | yes | Cumulative bytes read from PTY output stream. There is no input-byte count in `list()`. |
| `bootstrap_dir` | yes | Temporary shell bootstrap path; useful only as debug detail. Avoid making it a primary column. |
| `shell_argv` | yes | The shell launch argv (for example zsh), not necessarily the agent command. UI should label this as PTY argv or use owner `cell.command` for the main Command column. |
| `cwd` | yes | Initial cwd passed to process spawn. Current cwd is better approximated from owner `cell.current_path`. |
| `started_at` | no | Do not fake this. Show `—`/`unknown` in MVP. True session start time needs a supervisor protocol/data change. |
| `exit_status` | no in list | Only `subscribe` exit frames include `exit_status`; list only exposes `alive`. |
| subscribers/counts/buffer | no in list | Snapshot frames have buffer data, but the list operation does not. The panel should not display terminal output. |

## Does the supervisor manage more than xterm/PTY sessions?

No, not today. More precise wording: the sidecar manages backend PTY sessions that the standalone xterm.js UI renders. It owns shell processes for Torque standalone agents/terminals and preserves those PTYs across daemon restarts.

It does **not** manage:

- iTerm2/toolbelt tabs or windows;
- the main Torque daemon;
- the separate event-ingest sidecar (`torque/event_ingest_daemon.py`);
- MCP child processes as separate managed units;
- provider CLI subprocesses as separate rows beyond whatever runs inside the shell PTY;
- future managed services/daemons.

If the product intent is a broader sidecar/daemon registry, that is a separate design and needs human approval. The plan below stays scoped to the PTY supervisor `list()` surface.

## Proposed UI placement

Choose a **dedicated standalone panel app named "Supervisor"**:

- Add a taskbar button shown only in embedded/standalone runtime.
- Do not include it in the default open layout, to avoid cluttering the current right rail tabs.
- When opened, dock it to the existing right rail by default (`_standalonePanelDefaults.supervisor = 'right'`).
- It can be floated or detached using the existing standalone panel manager, satisfying the "mini-window" use case without creating a one-off window system.
- In toolbelt/iTerm2 runtime, hide the taskbar button or show an unavailable empty state if accessed directly.

Why not Library/Context or Global Settings:

- Library and Context are content/workflow panels, not runtime observability.
- Global Settings > System already has daemon status, but a task-manager view wants live refresh, sorting, and detachment. A modal is the wrong interaction pattern.
- A dedicated panel keeps the feature discoverable and reusable in a detached OS window.

## Wireframes

### Wide / bottom / detached layout

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Supervisor                                            ● Connected  7 sessions │
│ Read-only PTY sidecar sessions                         ⟳ Refresh  [✓] Auto  │
├─────────┬──────────────┬─────────┬───────┬────────────────┬────────┬───────┤
│ State   │ Owner        │ Session │ PID   │ Command        │ Bytes  │ TTY   │
├─────────┼──────────────┼─────────┼───────┼────────────────┼────────┼───────┤
│ Alive   │ worker-a     │ 6d645f… │ 87321 │ codex          │ 1.2 MB │120×32 │
│ Alive   │ terminal-1   │ 0be1c4… │ 87344 │ /bin/zsh -il   │ 44 KB  │100×28 │
│ Exited  │ old-worker   │ a90fd1… │ 87001 │ claude         │ 3.8 MB │120×32 │
└─────────┴──────────────┴─────────┴───────┴────────────────┴────────┴───────┘
  Detail-on-row-expand: cell id, group, cwd/current path, shell_argv,
  bootstrap_dir (debug), orphan/stale ownership warning.
```

### Narrow / right rail layout

```text
┌──────────────────────────────┐
│ Supervisor      ● Connected  │
│ 7 sessions   ⟳  [✓] Auto     │
├──────────────────────────────┤
│ ● worker-a        1.2 MB     │
│ codex · pid 87321 · 120×32   │
│ 6d645f307fe6                 │
├──────────────────────────────┤
│ ● terminal-1       44 KB     │
│ /bin/zsh -il · pid 87344     │
├──────────────────────────────┤
│ ◌ old-worker      exited     │
│ claude · pid 87001           │
└──────────────────────────────┘
```

## Columns / display rules

Primary columns/cards:

1. **State**: Alive/Exited/Unavailable; use color pills.
2. **Owner**: cell name, group, kind/cell_type; mark `orphan` if no matching active cell or `cell.session_id !== session_id`.
3. **Session**: abbreviated `session_id` with title/copy-friendly full id.
4. **PID**: child shell PID.
5. **Command**: prefer owner `cell.command` when present; otherwise render `shell_argv`. Label raw shell value as "PTY argv" in details.
6. **Bytes**: humanized `total_bytes` from PTY output.
7. **TTY**: `cols × rows`.
8. **Path**: prefer owner `current_path`, fallback list `cwd`.
9. **Started**: `—` in MVP with tooltip "Not exposed by supervisor list()". Keep this placeholder only if product specifically wants the column; otherwise omit it until protocol adds `started_at`.

## Refresh / live update model

Use WebSocket command polling for MVP:

- New UI command: `supervisor_sessions_list`.
- Panel requests once when opened and then every 2 seconds while visible and Auto is enabled.
- Pause polling when the panel is hidden and when a detached panel window is closed.
- Manual Refresh always sends one request.
- On errors/unavailable supervisor, keep the last successful list visible, show a banner, and back off to ~5 seconds.
- Optionally refresh immediately on existing `system_banner` supervisor events.

Reasoning: the supervisor protocol has no list-change stream today. Polling a local Unix socket list frame is low cost and avoids adding new protocol broadcasts. A push/live WS feed can be a later optimization if needed.

## Proposed WS response shape

Server command response from `supervisor_sessions_list`:

```json
{
  "type": "supervisor_sessions",
  "available": true,
  "mode": "standalone",
  "terminal_backend": "pty",
  "refreshed_at": 1778344000.123,
  "sessions": [
    {
      "session_id": "6d645f307fe64d03875ef72e6787ba66",
      "cell_id": "edb1d992",
      "pid": 87321,
      "alive": true,
      "cols": 120,
      "rows": 32,
      "total_bytes": 1234567,
      "cwd": "/repo",
      "current_path": "/repo/.torque/worktrees/edb1d992",
      "shell_argv": ["/bin/zsh", "-il"],
      "display_command": "codex",
      "owner": {
        "id": "edb1d992",
        "name": "pty-supervisor-panel-sketch",
        "group": "Torque",
        "cell_type": "agent",
        "kind": "worker",
        "status": "running"
      },
      "orphan": false,
      "started_at": null
    }
  ],
  "missing_fields": ["started_at", "exit_status", "input_bytes"]
}
```

Unavailable/fallback response should still use `type: "supervisor_sessions"` so the frontend does not route it through generic context/error handlers:

```json
{
  "type": "supervisor_sessions",
  "available": false,
  "mode": "toolbelt",
  "terminal_backend": "iterm2",
  "sessions": [],
  "message": "PTY supervisor is only available in standalone embedded-terminal mode."
}
```

## What it is NOT

- Not a replacement for the agent grid, board, or Agent panel.
- Not a terminal output viewer; do not stream or display PTY buffer contents.
- Not a process manager in MVP; no kill/restart/attach buttons.
- Not an iTerm2 window/tab manager.
- Not a generic registry of all Torque sidecars/daemons.
- Not a persistence/history feature for exited sessions; exited rows are transient unless a future design adds storage.

## Implementation plan

### 1. Backend: expose normalized read-only session list

Files:

- `torque/local_pty.py`
- `torque/server_supervisor.py` (new helper module) or a small helper section in `torque/server.py`
- `torque/server.py`

Steps:

1. Add `SupervisedPtyAdapter.list_supervisor_sessions()` that returns `await self._client.list_sessions()`.
   - Do not add this to `TerminalAdapter` unless desired; duck-typing is enough for a supervisor-only diagnostic surface.
   - Do not change `torque/pty_supervisor.py` or the wire protocol.
2. Add a backend normalizer helper, preferably `torque/server_supervisor.py`, with functions like:
   - `normalize_supervisor_session(info, state)`
   - `build_supervisor_sessions_payload(bridge, state, runtime_payload_func)`
3. Enrichment rules:
   - owner lookup via `state.agents.get(cell_id)`;
   - `display_command = cell.command || shell_argv_joined`;
   - `current_path = cell.current_path || info.cwd`;
   - `orphan = !cell || cell.session_id !== session_id`;
   - `started_at = None` and include a missing-fields note.
4. Add `elif cmd == "supervisor_sessions_list"` in `handle_command` to return the payload.
5. If not standalone/supervised, return a non-error `supervisor_sessions` payload with `available: false` and a clear message.
6. If the supervisor client raises `SupervisorUnavailable` or another exception, catch it, log it, and return `available: false` with the last error message; do not crash the WS command path.

### 2. Frontend: add Supervisor panel app

Files:

- `webview.html`
- `static/js/supervisor.js` (new)
- `static/js/ws.js`
- `static/js/main.js`
- `static/js/render.js`
- `static/js/panel_manager.js`
- `static/style.css`

Steps:

1. Add `<div id="panel-supervisor" class="panel-hidden"></div>` to the bottom panel host.
2. Add a standalone-only taskbar button, for example `▣ Supervisor`, with `data-app="supervisor"` and `onclick="togglePanel('supervisor')"`.
3. Load `static/js/supervisor.js` before `panel_manager.js` / `main.js` in `webview.html`.
4. Register the panel app:
   - `_panelIds` includes `panel-supervisor`;
   - `_standalonePanelApps` includes `supervisor`;
   - `_standalonePanelTitles.supervisor = 'Supervisor'`;
   - `_standalonePanelDefaults.supervisor = 'right'`;
   - do **not** add it to `_standaloneDefaultLayout().right.tabs`.
5. Update surface rendering:
   - `render.js` `_surfacePanelApp('supervisor')` and `_renderSurface('supervisor')`;
   - `main.js` `_loadPanelApp('supervisor')`, restore logic, and group UI state capture list.
6. Implement `static/js/supervisor.js`:
   - state: sessions, loading, error/message, `available`, `lastUpdated`, `autoRefresh`, timer, sort key, selected/expanded row, scroll position;
   - `supervisorEnsureLoaded()` requests immediately and schedules polling;
   - `supervisorRequestSessions(force)` sends `{cmd:'supervisor_sessions_list'}`;
   - `supervisorReceiveSessions(msg)` stores response and re-renders if visible;
   - `renderSupervisorPanel()` renders header, status, controls, and responsive table/card list;
   - `supervisorSetAutoRefresh(checked)` toggles polling;
   - `supervisorRefresh()` manual refresh.
7. Update `ws.js` to route `msg.type === 'supervisor_sessions'` to `supervisorReceiveSessions(msg)`.
8. Add CSS for the panel, pills, table/card responsive behavior, and hide the taskbar button outside embedded runtime:
   - default `.taskbar-app[data-app="supervisor"] { display: none; }`
   - `body.runtime-embedded .taskbar-app[data-app="supervisor"] { display: inline-flex; }`

### 3. Frontend state-preservation requirements

Because panels re-render from WS responses:

- Capture and restore `#panel-supervisor` scrollTop around render.
- Preserve sort key/direction, expanded row, selected row, and auto-refresh toggle in JS state.
- Do not replace any focused input while the user is interacting. The MVP controls are buttons/checkbox/sort headers only; if future filters/search are added, preserve caret and draft text like Context/Board do.
- Avoid rendering if the panel is not visible except to update cached data; render on next open.

### 4. Tests

Backend tests:

- Add `tests/test_server_supervisor.py` for normalization/payload helpers:
  - supervised bridge returns available payload with enriched owner info;
  - orphan session is marked when `cell_id` is missing or `cell.session_id` mismatches;
  - unavailable/non-supervised bridge returns `available: false`, not generic error;
  - `started_at` is `None`/missing-fields documents current protocol limits.
- Add or extend `tests/test_supervised_pty_adapter.py` to assert `SupervisedPtyAdapter.list_supervisor_sessions()` delegates to the client and returns raw list entries.

Frontend tests:

- Add `tests/frontend_supervisor_panel.test.js` plus Python wrapper `tests/test_frontend_supervisor_panel.py`, or place targeted coverage near `tests/frontend_state_regression.test.js`.
- Cover:
  - panel renders alive/exited rows and humanized byte counts;
  - unavailable state renders a clear message;
  - opening the panel sends `supervisor_sessions_list` and auto-refresh schedules only while visible;
  - `supervisorReceiveSessions` preserves selected/expanded row and scroll;
  - taskbar button is present in HTML and hidden outside `runtime-embedded` by CSS;
  - panel manager recognizes `supervisor`, docks it to the right when selected, and does not include it in the default layout.

Run before shipping:

```sh
python3 -m unittest tests.test_server_supervisor tests.test_supervised_pty_adapter tests.test_frontend_supervisor_panel -v
node --test tests/frontend_supervisor_panel.test.js
make test
```

Do not run `make deploy` from a worker worktree.

## Trade-offs and decisions

- **Polling vs push:** choose polling over the existing WS command path. It is simple, bounded, and avoids supervisor protocol changes. Push can be added later if the local list poll is too noisy.
- **Dedicated panel vs Settings modal:** choose dedicated panel because users need a live, detachable task-manager surface. Settings is too hidden and modal.
- **Started time:** do not fabricate it. Display unknown or omit the column in MVP. Adding real `started_at` requires changing `SupervisorSession` and the protocol snapshot/list shape, which is explicitly out of scope for this sketch.
- **Command:** display owner `cell.command` as the user-facing command and expose raw `shell_argv` as PTY argv. The supervisor only knows the shell process at create time.
- **Debug internals:** include `bootstrap_dir` only in row details or omit from default UI; it is mostly implementation detail.
- **Controls:** no kill/restart in MVP. Those are operationally destructive and require product/human approval.

## Suggested follow-up task

Title: `Implement read-only PTY Supervisor panel`

Action: `feature/implement`

Context: Implement the bounded read-only viewer from `.torque/sketches/pty-supervisor-panel.md`. Stay off `torque/pty_supervisor.py` protocol changes. Engineer approval is enough if implementation remains read-only and scoped to existing `list_sessions()` data.
