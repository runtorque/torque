# Loom (Rust) — Architecture

Read this first when picking up the project cold. For strategy / roadmap, see [`PLAN.md`](PLAN.md). For the current state, see [`STATUS.md`](STATUS.md). For accumulated gotchas, see [`DECISIONS.md`](DECISIONS.md).

## Product shape

Native macOS desktop app. Single binary (`loom-app`). One process owns:

- The **engine** — SQLite persistence, command dispatcher, state machine, delta broadcast, MCP handler, action renderer, git/worktree management.
- The **HTTP/WS surface** on `127.0.0.1:18932` — for the existing `bin/loom` CLI (Python), Claude Code hooks, and MCP. The native UI does **not** use it; it talks to the engine directly in-process.
- The **native UI** — AppKit via `objc2`, main thread owned by `NSApplication.run()`. Subscribes to the engine's event bus, dispatches commands via direct function calls.
- **Embedded libghostty terminals** — each agent cell renders inside an `NSView` subclass that hosts a `ghostty_surface_t`. Ghostty owns the child PTY.

Linux is a future consumer: a `loom-ui-linux` crate will consume the same `EngineBridge`, replacing AppKit with GTK4. Engine doesn't move.

```
┌──────────────────────── loom-app binary ────────────────────────┐
│                                                                   │
│  NSApplication.run() (main thread)                                │
│  │                                                                │
│  ├── NSWindow → outer NSView (dock-layout container)              │
│  │   └── outer NSSplitView  (vertical layout, horizontal dividers) │
│  │       ├── Top zone        (optional Leaf/Split of panels)      │
│  │       ├── middle NSSplitView (horizontal layout, vertical dividers) │
│  │       │   ├── Left zone   (Sidebar panel by default)           │
│  │       │   ├── Center zone (LayoutNode tree, always visible)    │
│  │       │   │     ├── Leaf(Terminal) → GhosttyView ── ghostty_surface_t │
│  │       │   │     ├── Leaf(Sidebar)  → SidebarView (cached)      │
│  │       │   │     ├── Leaf(Board)    → BoardView (cached)        │
│  │       │   │     ├── Leaf(Actions/Memory/...) → placeholder     │
│  │       │   │     └── Split { axis, ratio, first, second } recurses │
│  │       │   └── Right zone  (optional)                           │
│  │       └── Bottom zone     (Board panel by default)             │
│  │                                                                │
│  │   Each edge zone wears a 22pt panel header (Move-to / Hide).   │
│  │                                                                │
│  └── Menubar → Quit (Cmd-Q)                                       │
│                                                                   │
│  tokio runtime (worker threads)                                   │
│  │                                                                │
│  ├── axum HTTP/WS server on :18932                                │
│  │     ├─ /ws              snapshot + deltas                      │
│  │     ├─ /api/cmd         command dispatch                       │
│  │     ├─ /events          Claude Code hook receiver              │
│  │     └─ /mcp             JSON-RPC tools (17 wired today)        │
│  │                                                                │
│  ├── MatrixState (in-memory, lives behind Arc<Mutex>)             │
│  │     ├─ content_layout  (center LayoutNode)                     │
│  │     └─ dock_edges      (top/left/right/bottom + ratios)        │
│  ├── LoomDb (rusqlite, WAL mode)                                  │
│  ├── EventBus (broadcast::Sender<OutMessage>)                     │
│  ├── UiAgentRegistry (agent_id → mpsc::Sender<String>)            │
│  ├── Scheduler (15s tick, fires scheduled_at tasks)               │
│  └── Optional PTY backend (LocalPtyBackend) for non-ghostty cells │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
```

## Workspace layout

```
rust/
├── Cargo.toml                       # workspace (10 member crates)
├── rust-toolchain.toml              # stable
├── PLAN.md                          # roadmap, decisions, tradeoffs
├── STATUS.md                        # what works today, what's next
├── ARCHITECTURE.md                  # this file
├── DECISIONS.md                     # log of load-bearing decisions
├── README.md                        # quickstart
├── third_party/
│   ├── README.md                    # Ghostty vendoring + build procedure
│   ├── ghostty/                     # (gitignored) Ghostty source
│   └── ghostty-build/               # (gitignored) libghostty.a + ghostty.h
└── crates/
    ├── loom-core/                   # State, delta ops, SQLite, event bus
    ├── loom-actions/                # YAML action loading + minijinja
    ├── loom-worktree/               # git2 + shell-out worktree management
    ├── loom-pty/                    # portable-pty backend (optional, non-ghostty)
    ├── loom-adapters/               # Claude Code / Codex / Gemini adapters
    ├── loom-weaver/                 # Weaver, MCP tool specs, task health (stubs)
    ├── loom-server/                 # axum HTTP/WS + command dispatcher + MCP + scheduler
    ├── loom-ghostty/                # libghostty FFI + GhosttyApp + Surface
    ├── loom-ui-native/              # AppKit shell (appkit feature-gated):
    │   ├── render.rs                #   pure-Rust: sidebar tree + board trees
    │   ├── bridge.rs                #   EngineBridge snapshot/dispatch/subscribe
    │   ├── appkit.rs                #   window + dock reconcile + refresh tick
    │   ├── sidebar.rs               #   NSOutlineView + context menu
    │   ├── board.rs                 #   kanban columns + drag-drop
    │   ├── panel_header.rs          #   Move-to / Hide popup per edge zone
    │   ├── modal.rs                 #   NSAlert-based prompt_form / confirm
    │   └── ghostty_view.rs          #   NSView subclass over ghostty_surface_t
    └── loom-app/                    # main binary — wires engine + UI
```

## Data flow

**UI mutation** (user clicks something, or a native menu action fires):

```
AppKit event
  → loom-ui-native bridge → EngineBridge::dispatch(cmd, body)
  → loom-server::commands::dispatch_command(ctx, cmd, body)
  → MatrixState mutation (.add_group / .upsert_task / ...)
  → state.emit(DeltaOp::*)
  → state.drain_deltas() → EventBus::send(OutMessage::Delta)
  → db.save_* (targeted row write via tokio::spawn_blocking)
```

**UI refresh** (periodic + on every delta):

```
NSTimer tick (500ms)
  → tick(mtm, bridge, views, state)
  → EngineBridge::snapshot() → clone state into MatrixStateSnapshot
  → reconcile_dock(mtm, ...) — rebuild NSSplitView tree only when the
      dock-layout + selection signature changed (cheap JSON hash compare).
      Cached SidebarView / BoardView / per-agent GhosttyView instances
      survive rebuilds via ContentState; only the outer splits get redone.
  → drain_pending_text(state) — pump queued UiAgentRegistry text into
      the matching GhosttyView via ghostty_surface_text.
  → sidebar.reload_if_changed(snapshot), board.reload_if_changed(snapshot)
      — each panel short-circuits on its own signature.
```

For libghostty panes, rendering bypasses this entirely — Ghostty owns the NSView region and draws via Metal directly.

**External mutation** (Python CLI or Claude Code hook):

```
HTTP POST /api/cmd {"cmd": "...", ...}
  → loom-server::commands::handle_cmd
  → (same dispatch path as UI)
  → response 200 + JSON result
  → UI sees the mutation on its next NSTimer tick (state is shared)
```

**Agent → engine feedback** (MCP):

```
Agent process calls loom_progress / loom_done / loom_derive / loom_memory_* / ... via MCP
  → HTTP POST /mcp {"method": "tools/call", "params": {...}}
  → loom-server::mcp dispatches to dispatch_cmd::ai_report (or memory::*, board::verify_task, etc)
  → updates agent ephemeral state + board task lane/labels (or memory entries)
  → emits deltas
```

**Engine → terminal output** (UI-attached agent dispatch):

```
dispatch_task / send_text / broadcast_to_group
  → UiAgentRegistry::is_attached(agent_id)?
       yes → UiAgentRegistry::send(agent_id, text)
              → unbounded mpsc channel (one per cached GhosttyView)
              → AppKit refresh tick drains receiver, calls GhosttyView::send_text
              → ghostty_surface_text() writes the prompt; surface owns the PTY
       no  → fallback: LocalPtyBackend.spawn() + .write()
```

This routing keeps libghostty as the sole owner of UI-visible PTYs (no double-spawn), while headless/test/CLI flows stay on `LocalPtyBackend`.

## Key design decisions

(Rationale for each is in [`DECISIONS.md`](DECISIONS.md).)

1. **Shared engine, per-platform UI.** Engine is pure Rust and portable; UI is AppKit now, GTK4 later. `EngineBridge` is the cross-boundary contract.
2. **Native AppKit over webview.** Rejected Tauri/webview mid-project — terminals need native Metal surfaces and tileable layouts that webviews can't host cleanly.
3. **libghostty full, not libghostty-vt.** Ghostty owns Metal rendering, font atlas, and PTY management. We just provide the NSView. Trade: Apple-only today, but Ghostty's Swift embedding API proves the pattern.
4. **PTY owned by Ghostty, not us.** Each `ghostty_surface_new` call takes a `command` — Ghostty spawns it in its own PTY internally. Our `loom-pty::LocalPtyBackend` remains for non-ghostty (headless / test) spawns.
5. **In-process command dispatch for the UI.** Native UI doesn't round-trip through HTTP — it calls `loom_server::commands::dispatch_command(ctx, cmd, body)` directly. HTTP is for external consumers (CLI, hooks, MCP).
6. **Delta broadcast with seq gap = resync.** Ported from Python Loom. WS clients that fall behind get a forced snapshot.
7. **All mutations flow through `MatrixState`.** `emit()` queues deltas; `drain_deltas()` hands them to the bus; targeted `db.save_*` persists. No direct field manipulation from outside the state module.

## Extension points

### Adding a new command

1. Add a function in `loom-server/src/commands/<area>.rs` with signature `async fn(&CmdContext, &Value) -> CmdResult`.
2. Register it in the `match cmd` in `loom-server/src/commands/mod.rs::dispatch`.
3. Mutate `ctx.state.lock().await`, emit deltas, persist via `ctx.db.save_*`, call `flush(ctx).await` to broadcast.
4. Write an integration test in `loom-server/tests/`.

### Adding a new adapter (AI provider)

1. Create `loom-adapters/src/<name>.rs` implementing the `AgentAdapter` trait.
2. Register it in `loom-adapters/src/registry.rs::PROVIDERS`.
3. Add boot-command detection in `detect_by_command`.

### Wiring a new UI surface (e.g. a Preferences window)

1. Build the Cocoa object graph under `loom-ui-native/src/<window>.rs`.
2. Use `EngineBridge::dispatch` for writes, `EngineBridge::snapshot` for reads.
3. Subscribe to `EngineBridge::subscribe()` if you need live updates.

### Porting to Linux

1. Create `crates/loom-ui-linux/` using `gtk4-rs`.
2. Build the same engine consumption shape as `loom-ui-native` — an `EngineBridge` with `snapshot / dispatch / subscribe`.
3. For terminals: either `libghostty-vt` (pure state machine, you render) or investigate Ghostty's GTK embedding (linked via Zig, not C API).
4. `cfg` gate in `loom-app/src/main.rs` to pick the right UI crate per target.

## The engine's domain model

Everything in `loom-core::state` mirrors Python Loom's dataclasses (JSON field names preserved for wire compatibility):

- **`AgentCell`** — an agent or terminal. Has `cell_type`, `parent_id` (for child terminals), `slug`, `status`, a stack of ephemeral fields (activity, tokens, errors), worktree metadata.
- **`BoardTask`** — a task on the board. `id`, `task`, `lane`, `group`, `action_name`, `action_vars`, pipeline linkage (`parent_task_id`, `pipeline_depth`, `pipeline_root_id`), health, artifacts, verification metadata.
- **`GroupSettings`** — per-group defaults (directory, shell, worktree config, dispatch lane, weaver agent).
- **`WeaverSettings`** — per-group weaver config (autonomy mode, push intervals, escalation style, enabled events).
- **`GlobalSettings`** — app-wide (default_command, filter_by_window, keybindings, max_pipeline_depth).
- **`Schedule`** — cron / one-shot task scheduling.
- **`MemoryEntry`** + **`MemoryLink`** — shared findings/decisions/warnings/handoffs/notes, lightly scoped (project/group/pipeline/task) with denormalized links to other Loom objects.
- **`PanelKind`** — what kind of panel a leaf hosts (`Terminal { id }`, **`Sidebar`**, `Board`, `Actions`, `Memory`, `Events`, `Templates`, `Context`, `Weaver`, `Placeholder`). Sidebar is a first-class dockable panel, not a hardcoded rail.
- **`LayoutNode`** — layout tree: `Leaf { panel }` or `Split { axis, ratio, first, second }`. Used both inside the Center zone and for optional per-zone splits.
- **`DockZone`** — `Top / Left / Right / Bottom / Center`. Edges are dockable; Center is always visible and holds the existing tileable-grid `LayoutNode`.
- **`DockEdges`** — `Option<LayoutNode>` per edge + `DockRatios { top, left, right, bottom }`. Persisted in `ui_state[dock_edges]`.
- **`DockLayout`** — UI-facing combined view (edges + center + ratios). Built via `MatrixState::effective_dock_layout()` for snapshotting.
- **`MatrixState`** — the container; indexes by group, builds slugs, cascades deletes, enforces reserved lanes, emits deltas on every mutation. Owns `selected_agent_id`, `content_layout` (center), `dock_edges` (edge zones), `panel_active`, the per-group board view-state maps, plus the memory entry map.

## Delta op catalog

Wire format: JSON objects with an `op` tag + op-specific fields. The JS frontend (no longer used) and the CLI consume these verbatim.

```
agent_upsert         agent_remove
group_update         group_remove        group_rename    groups_reorder
group_settings_update
global_settings_update
task_upsert          task_remove
lanes_update
ui_update            focus_update        panel_update
schedule_upsert      schedule_remove
weaver_settings_update   weaver_worklog_append
events_update
memory_upsert        memory_remove
```

`ui_update` is the catch-all for UI-state changes — it carries one or more of `{ selected_agent_id, panel_active, content_layout, dock_edges, board_filters_by_group, board_saved_views_by_group, board_lane_sorts_by_group, board_card_density_by_group }` per emit.

Each mutation on `MatrixState` emits one or more of these; `drain_deltas()` hands them to the bus as `{type: "delta", seq: N, ops: [...]}`.

## Testing topology

- **Unit tests** colocated with each module. Pure-logic modules (state, delta, slug, task_ids, render, action manager) have heavy coverage.
- **Integration tests** under `loom-server/tests/*.rs` — spin up a real axum server against an in-memory SQLite and exercise the HTTP surface. One of them (`worktree.rs`) creates a real temp git repo.
- **Render tests** for the native UI: `loom-ui-native/src/render.rs` is pure Rust, fully tested without any AppKit dependency.
- **PTY integration** — `loom-pty/tests/spawn.rs` spawns real `/bin/sh`.
- **FFI smoke** — `loom-ghostty::ffi::ffi_smoke::symbols_linked` proves the linker resolved `ghostty_app_new` / `surface_new` etc.

137 tests total, all green. (See [`STATUS.md`](STATUS.md) for the per-crate breakdown.)

### Race-prone tests

Three test races are gated by process-wide mutexes. **Add to the lock if you mutate the same shared state in a new test:**

- `loom-core::config::tests::ENV_LOCK` — gates anything that mutates `LOOM_DEFAULT_CMD` or `LOOM_INSTALL_DIR`.
- `loom-server::tests::actions::ENV_LOCK` — gates anything that mutates `LOOM_PROJECT_ROOT`.
- `loom-ghostty::ffi::tests::SURFACES_LOCK` — gates anything that touches the process-wide `SURFACES` map.

Tests that need to set env vars or share global state should follow the pattern:
```rust
let _g = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
std::env::set_var("FOO", "bar");
// ...test body...
```

For `loom-server::tests::actions` (async), the lock is `tokio::sync::Mutex` — `let _g = env_lock().await;` at the top of each `#[tokio::test]`.

## Build procedure, condensed

Requirements: macOS 13+, Rust stable, Xcode (full), Metal Toolchain, Zig 0.15.2.

```sh
# One-time libghostty build + stage
cd rust/third_party
git clone --depth 20 https://github.com/ghostty-org/ghostty.git
cd ghostty
TOOLCHAINS=Metal zig build -Demit-xcframework=true \
  -Dxcframework-target=native -Doptimize=ReleaseFast
cd ..
mkdir -p ghostty-build/{lib,include}
cp -R ghostty/include/. ghostty-build/include/
cp "$(find ghostty/.zig-cache -name libghostty-fat.a | head -1)" \
   ghostty-build/lib/libghostty.a

# Ongoing Rust iteration
cd ../
cargo test --workspace
cargo build --bin loom-app --features loom-ui-native/appkit
LOOM_PORT=28932 cargo run --bin loom-app --features loom-ui-native/appkit
```

Full details: [`third_party/README.md`](third_party/README.md).

## File reading order for new contributors

1. This file.
2. [`DECISIONS.md`](DECISIONS.md) — why things are the way they are.
3. [`STATUS.md`](STATUS.md) — what works today.
4. `crates/loom-core/src/state.rs` — the domain model (including DockEdges / DockZone / PanelKind::Sidebar).
5. `crates/loom-core/src/delta.rs` — the wire contract.
6. `crates/loom-server/src/commands/mod.rs` — the dispatch fanout.
7. `crates/loom-server/src/commands/board.rs` — `dock_panel`, `set_dock_ratios`, `set_layout`, task CRUD, reorder/move.
8. `crates/loom-ghostty/src/{app,surface,ffi}.rs` — the libghostty layer.
9. `crates/loom-ui-native/src/{appkit,bridge,render}.rs` — the UI core.
10. `crates/loom-ui-native/src/{sidebar,board,panel_header,modal,ghostty_view}.rs` — per-panel renderers + chrome.

Everything else is straightforwardly derived from those.
