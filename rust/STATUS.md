# Loom Rust — status

Snapshot of what exists, what works, and what's left. Paired with [`PLAN.md`](PLAN.md). Per-feature checklist in [`FEATURES.md`](FEATURES.md).

## Build + test

```sh
cd rust/
cargo test --workspace                                        # 137 tests passing
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
- **Snapshot on connect + delta broadcast** — same wire format consumers (CLI/MCP) expect. Snapshot now includes `selected_agent_id`, `panel_active`, `content_layout`, `dock_edges`.
- **Lagged receivers trigger forced resync** — matches Python's seq-gap behavior.
- **SQLite persistence** with WAL, targeted writes per mutation, full load on startup, ephemeral clearing. Schema includes `memory_entries`, `memory_links`, `schedules`, `weaver_settings`, `weaver_worklog`, `ui_state`. New `ui_state[dock_edges]` row persists the edge-zone layout + ratios; legacy installs fall through to sensible defaults.
- **~65 commands** wired through the in-process dispatcher: groups, agents (incl. `select_agent`, `clear_agent_context`), tasks (incl. `board_verify_task`, the four `board_set_*` view-state setters, `set_layout` / `standalone_set_panel_layout`, `board_set_panel`), actions, templates, worktree, dispatch (incl. `resolve_ask`), MCP, schedule CRUD (7), memory CRUD (6), **dock system (`dock_panel`, `set_dock_ratios`)**. Exposed both via `/api/cmd` and `loom_server::commands::dispatch_command` for the native UI.
- **Action engine** (`loom-actions`) — YAML + subdir namespaces + minijinja + `loom.*` context, fixtures from `.loom/actions/` pass.
- **Template engine** (`loom-server::commands::templates`) — YAML config bundles in `.loom/agents/` (project) or `~/.loom/agents/` (user). `render_template` deep-merges overrides onto the template.
- **Memory engine** — `MemoryEntry` + `MemoryLink` model, scope-filtered list (group, scope_kind, scope_ref, entry_type, task_id, pinned_only, linked_target_kind/ref, search). Pinned entries sort first, then durable, then newest.
- **Worktree engine** (`loom-worktree`) — git2 diff + git worktree/checkpoint/rollback shell-out, merge detection.
- **PTY engine** (`loom-pty`) — `portable-pty` via `/bin/sh -c`. Spawn/write/resize/close tested.
- **Dispatch + ai_report** — create/reuse agents, render action prompts with full `loom.*` context, spawn PTY, update task + agent state. Now routes through `UiAgentRegistry` first: if an agent is UI-attached (a `GhosttyView` is mounted for it), text goes through the registry channel into the existing Ghostty PTY — no duplicate spawn. Before text is sent, `dispatch_task` runs provider-specific integration install (hooks + MCP + skills) against the agent's working directory — idempotent, merges with any existing user config, deduped by `/events` URL marker across port changes. Claude Code is the only adapter with a non-trivial implementation today.
- **MCP handler** on `/mcp` — initialize, tools/list, tools/call. **17 tools wired**: `loom_progress|done|ready|blocked|error|ask|context|derive|verify|name|reply` plus `loom_memory_publish|list|read|pin|unpin|link`. Memory tools auto-stamp `source_kind/source_id/source_name` from the calling agent.
- **Cron scheduler** — 15s tick, fires `scheduled_at` tasks. CRUD commands manage `Schedule` rows; `schedule_run` bypasses the tick.
- **Native UI** (`loom-ui-native`):
  - `render` module: pure-Rust display logic. Ships `build_sidebar_tree` (typed group/agent/terminal tree) and `build_board_columns` (one column per non-Archived lane, with nested derived tasks).
  - `appkit` module (feature-gated): full object graph — NSApplication, menubar (App menu → Quit/Cmd-Q), NSWindow (Titled/Closable/Resizable), outer **dock-layout container** hosting top / left / center / right / bottom zones.
  - **Dock system**: `reconcile_dock` walks `snapshot.dock_layout` and builds a cross-layout NSSplitView (vertical-divider outer split × horizontal-divider middle row). Edge zones (top/left/right/bottom) are optional `Option<LayoutNode>`; center is always present. Each edge zone is wrapped with a 22pt `panel_header.rs` bar with a "…" menu for Move-to / Hide. Signature short-circuit skips rebuilds when the dock layout + selection are unchanged.
  - **Sidebar panel** (`sidebar.rs`): NSOutlineView with agent tree, context menu (Rename / Edit / Relaunch / Remove / Add child), inline "+ Add Group" button, modal-driven Add/Edit forms via `modal.rs`. Mounted as `PanelKind::Sidebar` in the Left dock zone by default.
  - **Board panel** (`board.rs`): all-lanes-visible kanban. Horizontal NSScrollView → column host → one column per non-Archived lane. Each column has bold header ("Backlog · 3"), inline "+ Add task" NSTextField (tagged by column index), scrollable NSTableView with nested derived tasks (✓/↳ prefix, dimmed for Done). Card context menu (Dispatch / Edit / Move to / Remove). Drag-drop: within-lane reorder via `board_reorder_task`, cross-lane drop onto another column's NSTableView via `board_move_task`. Mounted as `PanelKind::Board` in the Bottom dock zone by default.
  - **Content area (center zone)**: still a recursive tree mount of `LayoutNode::Leaf | Split`. Terminal leaves host `GhosttyView` (libghostty); other `PanelKind`s fall through to the native panel renderers or a placeholder.
  - **`UiAgentRegistry`** on `AppState`: when a `GhosttyView` is created, the UI registers an unbounded channel against the agent id. The 500 ms refresh tick drains pending text from each cached view's receiver and forwards it to `GhosttyView::send_text`. Cache eviction (agent removed) auto-unregisters.
  - **Panel cache**: `ContentState.sidebar_cache` + `board_cache` hold `Retained<SidebarView>` / `Retained<BoardView>` so they survive dock rebuilds — their internal state (outline expansion, board signature) is preserved.
  - `bridge` module: `EngineBridge::snapshot/dispatch/subscribe`, plus `resolve_command` (cell override → global default → claude/zsh by cell type) and `resolve_cwd`. `MatrixStateSnapshot` now includes `dock_layout` (full edges + center + ratios).
  - NSTimer-based 500ms refresh loop. Verified: binary launches, NSApplication run loop holds the process, engine stays responsive on HTTP during UI lifetime, dispatched prompts land inside the embedded terminal, sidebar context menu works, kanban board renders + accepts inline adds + drag-drop.

## Test summary

```
loom-core:        50 unit tests  (+6 dock: DockEdges/DockRatios/effective_dock_layout/set_dock_edge/set_dock_ratios)
loom-actions:     13 unit tests
loom-adapters:    13 unit tests  (+11 claude_code: hooks/MCP/skills install + dedupe + uninstall)
loom-ghostty:      3 unit tests
loom-pty:          1 integration (spawns /bin/sh)
loom-server:      52 integration across 7 files
loom-worktree:     1 unit test
loom-ui-native:   25 unit tests  (render + bridge + sidebar tree + board tree + board columns)
-------------------------------------
total:           147 passing, 0 failing
```

Three pre-existing parallel-test races were fixed earlier: `LOOM_PROJECT_ROOT` (actions tests), `LOOM_DEFAULT_CMD` (config tests), ghostty `SURFACES` map (ffi tests). Each gated with a process-wide mutex.

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

## Phase 10 — native sidebar (shipped 2026-04-14)

- `sidebar.rs` module: custom `NSOutlineView` subclass with source-list selection highlight, floating group rows, pooled `SidebarItem` NSObject rows keyed by `(id, kind)` so expansion state survives reloads.
- Pure-Rust `render::build_sidebar_tree(snapshot) -> SidebarTree` with typed group/agent/terminal nodes, status dot glyphs, and `branch · directory` subtitles.
- Context menu per row: Rename / Edit / Relaunch / Remove (+ Add Agent / Add Terminal on group rows; Add child Terminal on agent rows). Destructive actions gated through a `modal::confirm` NSAlert.
- `modal.rs`: reusable `prompt_form(title, message, fields, submit_label) -> Option<HashMap>` helper built on `NSAlert` + accessory view with stacked labeled `NSTextField`s. Used by all Add/Edit/Rename flows.
- Click selects → dispatches `select_agent` (dispatch-first per [D7](DECISIONS.md)). "+ Add Group" button sits above the outline.

## Phase 11 — dock system + native Board panel (shipped 2026-04-14)

- **Engine dock model** (`loom-core::state`):
  - `PanelKind::Sidebar` variant — sidebar is now a first-class dockable panel.
  - `DockZone { Top, Left, Right, Bottom, Center }` + `DockRatios` + `DockEdges` structs + `DockLayout` (full view with edges + center + ratios).
  - `MatrixState.dock_edges` field + `effective_dock_layout()` / `set_dock_edge()` / `set_dock_ratios()` methods. Defaults: Left=Sidebar (0.22), Bottom=Board (0.32), Top/Right hidden.
  - SQLite load/save for `ui_state[dock_edges]`. Legacy installs with just `content_layout` fall through to the defaults.
  - Commands: `dock_panel { zone, layout }` and `set_dock_ratios { top/left/right/bottom }`. Delta op: `UiUpdate { dock_edges }`.
- **AppKit outer shell** (`appkit.rs`):
  - Outer NSSplitView (vertical layout) × middle-row NSSplitView (horizontal layout). Zone hosts mounted only when `Some(layout)`. Cached `SidebarView` + `BoardView` + per-agent `GhosttyView` survive dock rebuilds.
  - Each edge zone gets a 22pt `panel_header.rs` bar with a "…" popup menu for Move-to / Hide. Dispatches two `dock_panel` calls (clear source → set target) to perform the move.
- **Native Board panel** (`board.rs`): all-lanes-visible kanban, **not** tab-switched.
  - Horizontal NSScrollView → column host → one column per non-Archived lane.
  - Per-column: bold header ("Backlog · 3"), inline "+ Add task" NSTextField (tag=column index), scrollable NSTableView with nested derived tasks (`↳` prefix, `✓` for Done, dimmed subordinates).
  - Card context menu: Dispatch / Edit / Move to lane / Remove.
  - Drag-drop: within-lane reorder via `board_reorder_task`, cross-lane drop on another column's NSTableView via `board_move_task`. Same data source handles both by comparing source lane ≠ destination lane.
  - Reload strategy: rebuild column widgets only when the lane set changes; otherwise `reloadData` on each. `layout_columns()` runs every tick (cheap setFrame calls) so window resizes propagate — NSClipView otherwise anchors a shorter document view to its origin, floating the strip to the bottom of the panel.
- **render.rs**: `build_board_columns(snapshot, group_filter)` returns one `BoardColumn { lane, rows }` per non-Archived lane. Reuses `build_board_tree` per lane so nesting + filter rules are consistent.

## What's deferred (follow-up work)

Commands present in Python Loom but not yet in Rust (see [`FEATURES.md`](FEATURES.md) for the canonical checklist):

- **Weaver**: 13 server commands + 34 weaver MCP tools + the weaver subsystem itself (`loom-weaver` is stubbed).
- **External tickets** (Jira/GitHub): 5 commands, shell out to `gh`.
- **Playbooks**: 7 commands.
- **Advanced worktree**: `diff_full`, `check_conflicts`, `rebase`, `merge`, `create_pr`, plus the streams/boundaries synthesis.
- **Artifacts/uploads**: `task_upload_artifact`, `remove_attachment`, the multipart handler.
- **Events**: per-cell `EventLog` ring buffer, `get_events`, `events_dismiss`, throttled broadcast.
- **Adapters**: full event parsing + activity inference (Python is ~1.4k LOC, Rust crates are stubs).
- **Native panel renderers**: Actions (incl. Pipelines view), Memory, Weaver, Context, Events, Templates — placeholders today. Sidebar + Board shipped in phases 10 + 11.
- **Archived-lane toggle** on the Board — `build_board_columns` already excludes Archived; need a reveal affordance (e.g. a toggle button above the columns).
- **Drag-to-redock panels** — today re-docking is via each panel's `…` popup menu. No VSCode-style zone drop indicators yet.

## Next steps

Roughly ordered for highest user-visible impact first:

1. **Adapter event parsing** — port `claude_code` HTTP hook handling; populate the per-cell Events log; surface activity badges in the sidebar. (Hooks now install — `/events` receives real payloads but still routes to the scaffolded `parse_hook` stub, not the full Python event taxonomy.)
2. **Actions panel** — native form editor for `.loom/actions/*.yaml` (next most-used panel after Board). Mountable via `dock_panel { zone: right, ... }` or inside the center grid.
3. **Archived-lane toggle** on the Board.
4. **Pane keyboard shortcuts + drag-from-sidebar** — Cmd+D / Cmd+Shift+D / Cmd+W + dragging a sidebar row onto a pane to mount its terminal.
5. **Weaver** — stand up `loom-weaver` (event buffer, digest, hints, session map) and the 34 MCP tools. Big chunk; do incrementally.
6. **Mouse + clipboard in GhosttyView** — `mouseDown/Moved/scrollWheel:` → `ghostty_surface_mouse_*`; clipboard callbacks for Cmd-C/Cmd-V.
7. **Linux UI** (deferred until Mac is steady) — `loom-ui-linux` crate consuming the same `EngineBridge`.
