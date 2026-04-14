# Loom Rust — status

Snapshot of what exists, what works, and what's left. Paired with [`PLAN.md`](PLAN.md). Per-feature checklist in [`FEATURES.md`](FEATURES.md).

## Build + test

```sh
cd rust/
cargo test --workspace                                        # 120 tests passing
cargo build --bin loom-app                                    # engine + UI stub
cargo run --bin loom-app --features loom-ui-native/appkit     # native window
```

## Product direction

**Native macOS app built around libghostty** (updated 2026-04-14).

- The Python Loom webview / JS frontend is **not** ported. `rust/assets/web/` was removed.
- `loom-ui-native` rebuilds the UI chrome natively. The content area is a tileable grid: any pane can be split horizontally or vertically into nested children, each leaf hosts a typed `PanelKind` (terminal / board / actions / weaver / memory / etc).
- Terminal panes are backed by libghostty's Metal renderer.
- HTTP/WS stays up for the existing `bin/loom` CLI + Claude Code hooks + MCP. The UI bypasses HTTP and talks to the engine in-process.

## What works end-to-end

- **HTTP/WS engine** on axum (port via `LOOM_PORT`, default 18932).
- **Snapshot on connect + delta broadcast** — same wire format consumers (CLI/MCP) expect. Snapshot now includes `selected_agent_id`, `panel_active`, `content_layout`.
- **Lagged receivers trigger forced resync** — matches Python's seq-gap behavior.
- **SQLite persistence** with WAL, targeted writes per mutation, full load on startup, ephemeral clearing. Schema includes `memory_entries`, `memory_links`, `schedules`, `weaver_settings`, `weaver_worklog`, `ui_state`.
- **~63 commands** wired through the in-process dispatcher: groups, agents (incl. `select_agent`, `clear_agent_context`), tasks (incl. `board_verify_task`, the four `board_set_*` view-state setters, `set_layout` / `standalone_set_panel_layout`, `board_set_panel`), actions, templates, worktree, dispatch (incl. `resolve_ask`), MCP, schedule CRUD (7), memory CRUD (6). Exposed both via `/api/cmd` and `loom_server::commands::dispatch_command` for the native UI.
- **Action engine** (`loom-actions`) — YAML + subdir namespaces + minijinja + `loom.*` context, fixtures from `.loom/actions/` pass.
- **Template engine** (`loom-server::commands::templates`) — YAML config bundles in `.loom/agents/` (project) or `~/.loom/agents/` (user). `render_template` deep-merges overrides onto the template.
- **Memory engine** — `MemoryEntry` + `MemoryLink` model, scope-filtered list (group, scope_kind, scope_ref, entry_type, task_id, pinned_only, linked_target_kind/ref, search). Pinned entries sort first, then durable, then newest.
- **Worktree engine** (`loom-worktree`) — git2 diff + git worktree/checkpoint/rollback shell-out, merge detection.
- **PTY engine** (`loom-pty`) — `portable-pty` via `/bin/sh -c`. Spawn/write/resize/close tested.
- **Dispatch + ai_report** — create/reuse agents, render action prompts with full `loom.*` context, spawn PTY, update task + agent state. Now routes through `UiAgentRegistry` first: if an agent is UI-attached (a `GhosttyView` is mounted for it), text goes through the registry channel into the existing Ghostty PTY — no duplicate spawn.
- **MCP handler** on `/mcp` — initialize, tools/list, tools/call. **17 tools wired**: `loom_progress|done|ready|blocked|error|ask|context|derive|verify|name|reply` plus `loom_memory_publish|list|read|pin|unpin|link`. Memory tools auto-stamp `source_kind/source_id/source_name` from the calling agent.
- **Cron scheduler** — 15s tick, fires `scheduled_at` tasks. CRUD commands manage `Schedule` rows; `schedule_run` bypasses the tick.
- **Native UI** (`loom-ui-native`):
  - `render` module: pure-Rust display logic. Sidebar nests child terminals under their parent agent and marks the selected agent with `▶`.
  - `appkit` module (feature-gated): full object graph — NSApplication, menubar (App menu → Quit/Cmd-Q), NSWindow (Titled/Closable/Resizable), outer NSSplitView with sidebar + content container.
  - **Content area** is a recursive tree mount: `reconcile_content` walks `snapshot.content_layout`, builds nested NSSplitViews via `build_layout_into`, mounts a `GhosttyView` per `Terminal` leaf and a placeholder NSScrollView per non-terminal leaf. A signature short-circuit skips rebuilds when the layout JSON + selection haven't changed, so terminal PTYs and focus survive ticks.
  - **`UiAgentRegistry`** on `AppState`: when a `GhosttyView` is created, the UI registers an unbounded channel against the agent id. The 500 ms refresh tick drains pending text from each cached view's receiver and forwards it to `GhosttyView::send_text`. Cache eviction (agent removed) auto-unregisters.
  - `bridge` module: `EngineBridge::snapshot/dispatch/subscribe`, plus `resolve_command` (cell override → global default → claude/zsh by cell type) and `resolve_cwd`.
  - NSTimer-based 500ms refresh loop. Verified: binary launches, NSApplication run loop holds the process, engine stays responsive on HTTP during UI lifetime, dispatched prompts land inside the embedded terminal.

## Test summary

```
loom-core:        44 unit tests
loom-actions:     13 unit tests
loom-adapters:     2 unit tests
loom-ghostty:      3 unit tests (ffi.register + symbols_linked)
loom-pty:          1 integration (spawns /bin/sh)
loom-server:      52 integration across 7 files
                  (api_cmd 5, ws 7, actions 5, worktree 6,
                   dispatch 10, mcp 4, batch_ports 8 [schedule/memory/board/layout])
loom-worktree:     1 unit test
loom-ui-native:   14 unit tests (render + bridge resolve_command logic)
-------------------------------------
total:           120 passing, 0 failing
```

Three pre-existing parallel-test races were fixed today: `LOOM_PROJECT_ROOT` (actions tests), `LOOM_DEFAULT_CMD` (config tests), ghostty `SURFACES` map (ffi tests). Each gated with a process-wide mutex so `cargo test --workspace` is now reliable.

## Workspace layout

```
rust/
├── Cargo.toml
├── PLAN.md / STATUS.md / README.md
└── crates/
    ├── loom-core/       State, delta ops, SQLite, event bus
    ├── loom-actions/    YAML + minijinja + loom context
    ├── loom-worktree/   git2 + worktree + checkpoint + merge
    ├── loom-pty/        portable-pty backend
    ├── loom-adapters/   Claude Code / Codex / Gemini / generic
    ├── loom-weaver/     Weaver + MCP tools + task health (stubs)
    ├── loom-server/     axum HTTP/WS + command dispatcher + MCP + scheduler
    ├── loom-ghostty/    libghostty FFI (stubbed) + GhosttyBackend
    ├── loom-ui-native/  AppKit shell (render tested; appkit feature-gated)
    └── loom-app/        main binary — wires engine + UI
```

## Phase 8 — libghostty integration (shipped)

- Static library builds via `zig build`, staged into `third_party/ghostty-build/` (see `third_party/README.md`).
- `loom-ghostty` links `libghostty.a` + Carbon/AppKit/Metal/CoreText/etc. frameworks; bindgen produces 88 functions + 49 structs.
- `GhosttyApp` singleton wires the 6 runtime callbacks; `Surface` wraps `ghostty_surface_t` with `new_macos`, `set_size`, `set_content_scale`, `set_focus`, `draw`, `write_text`, `send_key`.
- `loom-ui-native::ghostty_view::GhosttyView` — custom NSView subclass (objc2 `declare_class!`). On `viewDidMoveToWindow` it calls `ghostty_surface_new` passing its own raw NSView pointer. `keyDown:` / `keyUp:` translate NSEvent modifier flags + keyCode + characters into `ghostty_input_key_s` and call `ghostty_surface_key`. `setFrameSize:` propagates to `ghostty_surface_set_size` with the window's backing scale factor.

## Phase 9 — content-area hookup + multi-pane scaffold (shipped 2026-04-14)

- Content area no longer hardcodes `/bin/zsh`. `select_agent` engine command + `selected_agent_id` UI state drive which agent's terminal is active. `clear_agent_context` resets a cell's session metadata.
- `UiAgentRegistry` mediates engine ↔ UI: dispatch routes prompts through registered channels and the AppKit tick drains them into the matching `GhosttyView`. Eliminates duplicate-PTY hazard for UI-attached agents.
- Multi-pane layout: `PanelKind` (Terminal/Board/Actions/Memory/Events/Templates/Context/Weaver/Placeholder) + `LayoutNode` (Leaf | Split { axis, ratio, first, second }). `set_layout` command persists to `ui_state[content_layout]`. AppKit recursively builds nested NSSplitViews; cached terminal views survive layout changes.
- Memory subsystem: schema (`memory_entries`, `memory_links`), filtered list, denormalized links on load, MCP `loom_memory_*` (6 tools, source auto-stamped to calling agent).
- Template subsystem: `list/get/save/delete/render_template` over YAML in `.loom/agents/`. Project shadows user.
- Schedule CRUD: 7 commands; existing 15s tick already fires `scheduled_at` rows.
- Board view state: `board_set_filters/saved_views/lane_sorts/card_density` per-group, persisted as JSON in `ui_state`.
- 9 additional MCP tools: `loom_verify`, `loom_name`, `loom_reply`, plus the 6 memory tools.

## What's deferred (follow-up work)

Commands present in Python Loom but not yet in Rust (see [`FEATURES.md`](FEATURES.md) for the canonical checklist):

- **Weaver**: 13 server commands + 34 weaver MCP tools + the weaver subsystem itself (`loom-weaver` is stubbed).
- **External tickets** (Jira/GitHub): 5 commands, shell out to `gh`.
- **Playbooks**: 7 commands.
- **Advanced worktree**: `diff_full`, `check_conflicts`, `rebase`, `merge`, `create_pr`, plus the streams/boundaries synthesis.
- **Artifacts/uploads**: `task_upload_artifact`, `remove_attachment`, the multipart handler.
- **Events**: per-cell `EventLog` ring buffer, `get_events`, `events_dismiss`, throttled broadcast.
- **Adapters**: full event parsing + activity inference (Python is ~1.4k LOC, Rust crates are stubs).
- **Claude Code hook install** on dispatch.
- **Native panel renderers**: Board, Actions (incl. Pipelines view), Memory, Weaver, Context, Events, Templates — placeholders today.

## Next steps

Roughly ordered for highest user-visible impact first:

1. **Sidebar interactivity** — swap the read-only NSTextView for an NSOutlineView with an Outline data source so rows are clickable + draggable. Drives `select_agent` natively without HTTP.
2. **First native panel** — pick Board (most-used) and render it as a leaf via `mount_panel_into`. NSCollectionView for cards, NSTableView for lanes.
3. **Pane keyboard shortcuts + drag-to-split** — Cmd+D split right, Cmd+Shift+D split down, Cmd+W close pane. Drag a sidebar row onto a pane to mount its terminal there.
4. **Claude Code hook install** on dispatch — `.claude/settings.local.json` merge so dispatched agents can `ai_report` back.
5. **Adapter event parsing** — port `claude_code` HTTP hook handling; populate the Events log; surface activity badges in the sidebar.
6. **Weaver** — stand up `loom-weaver` (event buffer, digest, hints, session map) and the 34 MCP tools. Big chunk; do incrementally.
7. **Mouse + clipboard in GhosttyView** — `mouseDown/Moved/scrollWheel:` → `ghostty_surface_mouse_*`; clipboard callbacks for Cmd-C/Cmd-V.
8. **Linux UI** (deferred until Mac is steady) — `loom-ui-linux` crate consuming the same `EngineBridge`.
