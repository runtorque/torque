# Loom Rust port — implementation plan

**Status**: proposal / pre-work
**Scope**: standalone spin-off product. Not a drop-in replacement for the Python daemon. The iTerm2 toolbelt integration is intentionally dropped; terminals are owned by this app via `libghostty`.

## 1. Goals and non-goals

### Goals

- **Single process.** Server, UI shell, and terminal rendering all live in one native binary.
- **Speed.** Eliminate the Python/GIL tax on the delta broadcast, health-check, and worktree-stream hot paths. Amortize git calls via `libgit2` where it pays.
- **Terminal ownership.** Render terminals through `libghostty` inside the app, not via an external emulator.
- **CLI parity.** The existing `bin/loom` CLI keeps working unchanged by talking to the new daemon over the same HTTP surface. SQLite schema is shared so offline reads continue to work.
- **Action system parity.** Existing `.loom/actions/*.yaml` files continue to render correctly, Jinja2-style.
- **Spec-level parity with Python Loom**, not line-level. When the Python design has obvious Rust-native improvements (typed delta ops, enum-based commands, compile-time-checked templates), take them.

### Non-goals

- No iTerm2 SDK. `bridge.py` and `keybindings.py` have no Rust counterparts.
- No Python interop shim. This is a clean rewrite.
- No Windows support in v1 (macOS first, Linux second).
- No cross-version DB migrations from Python Loom's `loom.db`. We reuse the SQLite schema but start from a fresh DB; the user runs Python Loom or Rust Loom, not both against the same DB.

## 2. Product shape

```
┌────────────────────────────────────────────────────────┐
│ Loom.app  (single process)                             │
│                                                        │
│  ┌──────────────────────┐   ┌───────────────────────┐  │
│  │ Rust engine (tokio)  │   │ UI shell              │  │
│  │                      │   │                       │  │
│  │ - axum HTTP/WS       │   │ - main window         │  │
│  │ - state (MatrixState)│◄──┤   (Loom chrome: webview│ │
│  │ - SQLite             │   │    or native)         │  │
│  │ - actions / Jinja2   │   │ - terminal surfaces   │  │
│  │ - worktree / git2    │   │   (libghostty-backed) │  │
│  │ - adapters           │   │                       │  │
│  │ - MCP weaver         │   └───────────────────────┘  │
│  │ - scheduler / cron   │              ▲               │
│  └──────────────────────┘              │               │
│            ▲   HTTP :18932              │ in-process    │
│            │   (CLI, hooks, MCP)        │ channel       │
└────────────│───────────────────────────│───────────────┘
             │                            │
   ┌─────────┴──────────┐          ┌─────┴──────────┐
   │ bin/loom CLI       │          │ Agent PTYs     │
   │ (unchanged Python) │          │ (claude, codex)│
   └────────────────────┘          └────────────────┘
```

- One binary. The engine exposes HTTP on 18932 for the existing CLI + Claude Code hooks + MCP. The UI talks to the engine over an **in-process channel** (not localhost sockets) for latency and reliability.
- Terminals are rendered inside the app via `libghostty`. Each agent/terminal cell in the UI has a backing ghostty instance.
- The CLI (`bin/loom`) does not change. It keeps reading SQLite directly and posting to `/api/cmd`.

## 3. Workspace layout

```
rust/
├── Cargo.toml                 # workspace manifest
├── PLAN.md                    # this file
├── crates/
│   ├── loom-core/             # state, dataclasses, delta ops, SQLite
│   ├── loom-actions/          # YAML + Jinja2 (minijinja) action engine
│   ├── loom-worktree/         # git + worktree + checkpoint ops
│   ├── loom-adapters/         # Claude Code / Codex / Gemini / generic
│   ├── loom-weaver/           # weaver + MCP + task health + hints
│   ├── loom-server/           # axum HTTP/WS + command dispatcher
│   ├── loom-pty/              # PTY management via portable-pty
│   ├── loom-ghostty/          # libghostty FFI bindings + terminal view bridge
│   ├── loom-ui-tauri/         # (UI path A) Tauri commands + events
│   └── loom-app/              # main binary, wires everything
└── assets/
    └── web/                   # copy of /static + webview.html (shared)
```

**Why a workspace of crates?** Each crate maps to a Python module boundary, compiles independently, and lets `loom-core`/`loom-actions`/`loom-worktree` be unit-tested without pulling in the UI shell or ghostty. `loom-ghostty` isolates the only unsafe FFI in the project.

## 4. Tech stack

| Concern | Python (today) | Rust choice | Why |
|---|---|---|---|
| Async runtime | `asyncio` | `tokio` | Default, matches our multi-socket/polling design. |
| HTTP server | `aiohttp` | `axum` | Tokio-native, tower middleware, built-in WS via `tokio-tungstenite`. |
| WebSocket | `aiohttp` WS | `axum::extract::ws` | First-class. |
| SQLite | `sqlite3` (stdlib) | `rusqlite` + `tokio::task::spawn_blocking` | Sync rusqlite in a blocking pool mirrors the current pattern of synchronous SQLite inside an async server; keeps WAL + targeted writes simple. `sqlx` is overkill for our embedded-only DB. |
| Templating | `Jinja2` | `minijinja` | Written by the Jinja2 author; near drop-in for our `SandboxedEnvironment`, `StrictUndefined`, AST var discovery, `\| default()` filter. Closer to Jinja2 than `tera`. |
| YAML | `PyYAML` | `serde_yaml` (or `serde_yml` fork) | Round-trips, preserves our raw-YAML / rendered-YAML split. |
| Git | `subprocess` | `git2` + selective `Command` | `git2` (libgit2) for status/diff/ancestry hot paths. Fall back to shelling out for `worktree add`, `merge-tree`, `rebase`, `push` — behaviors where parity with the `git` binary matters more than speed. |
| PTY (fallback backend) | `pty` (stdlib) | `portable-pty` | Used by wezterm; mature; same API shape as `local_pty.py`. |
| Terminal rendering | iTerm2 | `libghostty` via hand-written FFI bindings | No existing Rust crate; we write bindings from the public C header. |
| Notifications | `osascript` subprocess | `notify-rust` (macOS)  | One-line replacement. |
| Logging | `logging` | `tracing` + `tracing-subscriber` | Structured logs, async-aware. |
| Serialization | `json` | `serde_json` | Used everywhere: WS messages, MCP, action vars. |
| UUIDs | `uuid` | `uuid` (v4) | Same API shape. |
| CLI | `argparse` (stdlib) | `clap` | Only used if we port the CLI later. V1 keeps `bin/loom` in Python. |

## 5. Async and threading model

- **One `tokio` runtime**, multi-threaded scheduler.
- **State is `Arc<RwLock<MatrixState>>`** — the Python `MatrixState` already treats itself as an exclusive-mutation authority under `asyncio` (single-threaded event loop). In Rust we need explicit locking, but contention will be low because mutations pass through a single command dispatcher anyway.
- **DB writes go through `spawn_blocking`** with a small dedicated pool (size 2). Reads via the same path. This matches today's "mutate in memory → queue delta → persist targeted rows" pattern.
- **Broadcast channel for deltas**: `tokio::sync::broadcast::Sender<DeltaMessage>`. Each connected WS subscriber clones the receiver; dropped messages trigger a resync (same semantics as today's seq gap → snapshot).
- **Event bus** (`events.py` `EventBus`): `tokio::sync::mpsc` with a bounded buffer and the same 200 ms throttle window.

## 6. State model

Port the Python dataclasses to Rust structs with `serde` derive:

```rust
// loom-core/src/state.rs
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentCell {
    pub id: String,
    pub slug: String,
    pub name: String,
    pub group: String,
    pub parent_id: Option<String>,
    pub tasks_dispatched: u32,
    pub directory: Option<PathBuf>,
    pub worktree_branch: Option<String>,
    // … (mirror AgentCell in loom/state.py)

    // ephemeral (not persisted)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub activity: Option<Activity>,
    // …
}
```

**Delta ops become a typed enum** (the single biggest quality-of-life upgrade over the Python):

```rust
// loom-core/src/delta.rs
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum DeltaOp {
    AgentUpsert { agent: AgentCell },
    AgentRemove { id: String },
    GroupUpdate { name: String, fields: GroupFields },
    GroupRename { from: String, to: String },
    GroupsReorder { order: Vec<String> },
    GroupRemove { name: String },
    GroupSettingsUpdate { name: String, settings: GroupSettings },
    GlobalSettingsUpdate { settings: GlobalSettings },
    TaskUpsert { task: BoardTask },
    TaskRemove { id: String },
    LanesUpdate { lanes: Vec<Lane> },
    UiUpdate { fields: UiState },
    FocusUpdate { id: Option<String> },
}
```

The JSON on the wire is identical to what `render.js` / `board.js` expect today (`{ "op": "agent_upsert", "agent": { … } }`), so the frontend doesn't change.

**Slug generation, cascade delete, reserved lanes, unique-slug-per-type** all port as pure functions — easy unit tests.

## 7. Persistence

- `loom-core` owns `rusqlite::Connection`, wrapped in a `Mutex<Connection>` held inside a blocking task pool.
- Schema identical to `db_schema.py` with a single migration step: on first boot, if `loom.db` doesn't exist, run the same `CREATE TABLE` statements.
- `MatrixState::save_agent`, `save_board_task`, etc. mirror the Python methods 1:1. Same "mutate memory → emit delta → persist row" triple.
- Attachments directory logic unchanged.

## 8. Module-by-module mapping

| Python | Lines | Rust target | Notes |
|---|---|---|---|
| `loom/state.py` | 3091 | `loom-core::state` | Structs, delta emitter, slug logic, board queries. Big file but mostly straightforward. |
| `loom/db.py` + `db_schema.py` + `db_board.py` + `db_memory.py` | ~3.3k | `loom-core::db` | `rusqlite` port. Targeted save methods become `impl MatrixState`. |
| `loom/config.py` | 114 | `loom-core::config` | Env vars, paths. Trivial. |
| `loom/task_ids.py` | 66 | `loom-core::task_ids` | Pure functions. |
| `loom/artifacts.py` | 484 | `loom-core::artifacts` | Normalize/validate; pure. |
| `loom/actions.py` | 696 | `loom-actions` | **Biggest Jinja2 port.** `minijinja::Environment` replaces `SandboxedEnvironment`. Var discovery via `minijinja::machinery::parse` + AST walk. `\| default(x)` filter works out of the box. |
| `loom/templates.py` | 399 | `loom-actions::templates` | Same engine. |
| `loom/worktree.py` + `worktree_streams.py` + `worktree_boundaries.py` | ~4.1k | `loom-worktree` | Hybrid: `git2` for ancestry/diff/status; shell out for `worktree add`, `merge-tree --write-tree`, `rebase`, `push`. Keep the checkpoint ring buffer semantics. |
| `loom/events.py` | 526 | `loom-core::events` | `tokio::sync::mpsc` + `broadcast`. |
| `loom/task_health.py` | 336 | `loom-core::task_health` | Pure. |
| `loom/memory.py` | 604 | `loom-core::memory` | Pure data mgmt. |
| `loom/playbooks.py` | 507 | `loom-core::playbooks` | Pure. |
| `loom/notifications.py` | 190 | `loom-core::notifications` | `notify-rust`. |
| `loom/cron.py` | 120 | `loom-core::cron` | `tokio-cron-scheduler` or hand-rolled with `tokio::time::sleep_until`. |
| `loom/external_tickets.py` | 302 | `loom-core::external_tickets` | Shell out to `gh`. Unchanged. |
| `loom/server.py` | 7049 | `loom-server` | **The biggest port.** ~120 command handlers, each becomes a match arm (or discrete handler fn). Plan to split into `loom-server/src/cmds/{agents,board,worktree,actions,dispatch,memory,weaver,schedule,ai_report,…}.rs` mirroring the existing `server_*.py` helpers. |
| `loom/server_agent.py` | 512 | `loom-server::cmds::agents` | 1:1 port. |
| `loom/server_artifacts.py` | 666 | `loom-server::cmds::artifacts` | 1:1 port. |
| `loom/server_dispatch.py` | 499 | `loom-server::cmds::dispatch` | 1:1 port. |
| `loom/server_worktrees.py` | 240 | `loom-server::cmds::worktree` | 1:1 port. |
| `loom/server_actions.py` | 103 | `loom-server::cmds::actions` | 1:1 port. |
| `loom/server_prompts.py` | 130 | `loom-server::cmds::prompts` | 1:1 port. |
| `loom/mcp.py` + `mcp_weaver.py` + `mcp_weaver_tools/*` | ~2.5k | `loom-weaver` + `loom-server::cmds::weaver` | JSON-RPC over HTTP. Straightforward. |
| `loom/weaver.py` + `weaver_hints.py` + `weaver_session_map.py` | ~2.3k | `loom-weaver` | Pure business logic. |
| `loom/adapters/*.py` | ~1.4k | `loom-adapters` | One module per adapter. Hook file I/O + event parsing. |
| `loom/local_pty.py` | 854 | `loom-pty` | `portable-pty` replacement. Event emitter shape preserved. |
| `loom/terminal_adapter.py` | 137 | `loom-core::terminal` | Trait in Rust (already a Protocol today). Two impls: `LocalPtyBackend` and `GhosttyBackend`. |
| `loom/bridge.py` | 1005 | **dropped** | iTerm2. |
| `loom/keybindings.py` | 505 | `loom-ui-*::keybindings` | Rebuilt against the chosen UI toolkit (AppKit keybindings, Tauri global shortcuts, etc.) with the same settings schema. |
| `loom/desktop.py` | 453 | `loom-app` | Boot, signal handling, lockfile. |
| `loom.py` | 25 | `loom-app::main` | Entry point. |

## 9. UI path — native AppKit (`loom-ui-native`)

**Decision (post-Phase-6 pivot)**: fully native macOS UI via `objc2` / AppKit. No webview, no Tauri. The Python Loom frontend (`webview.html` + `static/js/*`) is not ported — the Rust app rebuilds its chrome natively.

### Why

Terminals are first-class. libghostty renders into `CAMetalLayer`-backed `NSView`s inside the main window. For tiling/split-pane layouts, drag-to-rearrange terminals, and native feel (menus, accessibility, keybindings), a webview chrome is an obstacle — its rendering surface can't easily interleave with native Metal views.

### Crate

`crates/loom-ui-native/` with two layers:

1. **`render`** — pure-Rust display logic (sidebar text, content summary). Platform-free, unit-tested.
2. **`appkit`** (feature-gated on `appkit` + `target_os = "macos"`) — NSApplication / NSWindow / NSSplitView / NSOutlineView / NSView hierarchy, event dispatch, main-thread marshalling. Consumes `render` output.

This split keeps the workspace building on any platform while letting Mac-connected developers iterate on the Cocoa layer under `--features loom-ui-native/appkit`.

### Object graph (target)

```
NSApplication
└─ NSWindow (Titled, Resizable, Closable, Miniaturizable)
   └─ NSSplitView (vertical)
      ├─ Sidebar NSScrollView → NSOutlineView
      │  └─ rows driven by render::render_sidebar(snapshot)
      └─ Content NSSplitView (horizontal — terminals tile here)
         ├─ Terminal NSView (libghostty-backed CAMetalLayer)
         ├─ Terminal NSView (...)
         └─ …
```

### Engine ↔ UI

- `EngineBridge::snapshot()` — read the full state atomically into a plain struct for the UI to consume. Avoids holding `tokio::Mutex` across the Cocoa boundary.
- `EngineBridge::dispatch(cmd, body)` — in-process call into the same dispatcher the HTTP `/api/cmd` endpoint uses.
- `EngineBridge::subscribe()` — broadcast receiver that wakes the UI on every delta; the event-pump thread marshals a `refresh` onto the main thread via `dispatch2::Queue::main().exec_async(...)`.

### What stays from Python Loom's UX

- The board / lanes / cards concept maps 1:1 to NSOutlineView + NSCollectionView.
- Agent cells and their status badges map to sidebar rows with status indicators.
- The action editor becomes a dedicated native form (NSForm / NSStackView), not a modal overlay.
- Keyboard shortcuts from `loom/keybindings.py` become standard `NSMenuItem` key equivalents.

### HTTP role after the pivot

The HTTP/WS server still runs (for the existing `bin/loom` CLI, Claude Code hooks, and the MCP endpoint). The native UI does **not** use it — it talks to the engine via direct function calls. The WS + snapshot-on-connect machinery stays because CLI/MCP consumers still depend on it; it's just no longer in the UI hot path.

## 10. libghostty integration

This is the one genuinely novel piece. Plan:

1. **FFI bindings** (`loom-ghostty` crate)
   - Pull libghostty's public C header (`ghostty.h`).
   - Generate bindings with `bindgen` in a `build.rs`.
   - Hand-write safe Rust wrappers over the `unsafe extern "C"` bindings: `GhosttyInstance`, `GhosttyConfig`, `GhosttyCallbacks`.
   - Vendor or dynamically link? **Vendor** for v1 — pin a known-good libghostty revision as a git submodule; build via Zig in a `build.rs` step.
2. **Rendering surface**
   - macOS: create a `CAMetalLayer`-backed `NSView`. Pass the layer handle to libghostty. Ghostty's renderer writes directly; we own the view lifecycle.
   - Linux (later): EGL surface on GTK4 or winit.
3. **Input**
   - Native key/mouse events → translate → `ghostty_input_*` calls.
   - Bypass the webview for terminal inputs entirely (the native view is in front of or beside the webview, not inside it).
4. **PTY pairing**
   - Two options: let libghostty manage its own PTY, or drive it externally. For parity with our current `local_pty.py` event model (activity detection, hook dispatch, adapter events), we **drive the PTY ourselves** via `portable-pty`, feed bytes into libghostty's terminal state machine, and pipe writes out the other side. This keeps adapters untouched.
5. **Integration point**
   - `TerminalBackend` trait in `loom-core` has two impls: `GhosttyBackend` (Rust, in-app) and `LocalPtyBackend` (headless, for tests or server-only mode).

### libghostty risks

- **No established Rust crate.** We're first movers. Budget 1–2 weeks for bindings + a "hello world" terminal window before integrating with the Loom state machine.
- **Build complexity.** Requires Zig toolchain on contributors' machines. Mitigation: pre-built artifacts in CI, documented setup in `rust/README.md`.
- **API stability.** libghostty is young. Pin a commit, track upstream manually, budget re-sync work each quarter.

## 11. Phased roadmap

Each phase ends with a runnable, demoable artifact.

### Phase 0 — Scaffolding *(1 week)*
- Cargo workspace, CI (fmt, clippy, test), README.
- Empty crates compiling.
- Copy `static/` + `webview.html` into `rust/assets/web/`.
- **Exit criterion**: `cargo build` succeeds, CI green.

### Phase 1 — Core data model + DB *(2 weeks)*
- Port `state.py`, `db*.py`, `task_ids.py`, `artifacts.py`, `config.py` to `loom-core`.
- Implement `MatrixState` with all targeted save methods.
- Delta op enum + emit/broadcast/seq machinery.
- Unit tests: slug generation, cascade delete, unique-slug, reserved-lane enforcement, delta-op round-trip.
- **Exit criterion**: can open an existing `loom.db`, round-trip all entities, emit correct deltas for every mutation.

### Phase 2 — HTTP/WS server + CLI compat *(2 weeks)*
- `loom-server` with axum, ws handler, `/api/cmd`, `/events`, `/api/upload*`, static asset serving.
- Command dispatcher stub returning errors for unimplemented commands.
- Port read-only commands first: `get_config`, `list_actions`, `get_action`, `get_events`, `get_*_settings`, `board_*` reads.
- **Exit criterion**: `bin/loom` CLI (unchanged) can `task list`, `task show`, `board list`, `lanes` against the Rust daemon.

### Phase 3 — Actions + templating *(1.5 weeks)*
- `loom-actions` on `minijinja`.
- Port `ActionManager`, YAML coalesce, var discovery, transitions, preview.
- Port `render_action`, `save_action`, `delete_action`, `preview_prompt`, `discover_pipelines`, `render_template`.
- **Exit criterion**: every `.loom/actions/*.yaml` file in the existing repo renders identically to Python (compare `preview_prompt` output byte-for-byte via a test harness that drives both daemons).

### Phase 4 — Worktree + git *(2 weeks)*
- `loom-worktree` with `git2` + shell-out hybrid.
- Checkpoint ring buffer, diff summary, merge detection (ancestry + `merge-tree`), base-advanced fallback.
- Port all `worktree_*` commands.
- Port `worktree_streams` + `worktree_boundaries`.
- **Exit criterion**: existing worktrees are discoverable; create/checkpoint/rollback/merge-check parity tests pass.

### Phase 5 — Dispatch, adapters, PTY *(2 weeks)*
- `loom-pty` via `portable-pty`.
- `loom-adapters` — Claude Code, Codex, Gemini stub, generic.
- Hook install/uninstall, session resume, event parsing, activity inference.
- Port `dispatch_task`, `ai_report`, `broadcast_to_group`, `send_text`, `relaunch_agent`.
- **Exit criterion**: a dispatched task launches a PTY running `claude`, the agent's activity badge updates live, `loom ai progress/done` round-trips.

### Phase 6 — MCP weaver + scheduler *(2 weeks)*
- `loom-weaver` port of weaver.py, weaver_hints, weaver_session_map, mcp_weaver, task_health.
- MCP handler on `/mcp`.
- Cron scheduler.
- **Exit criterion**: MCP tool calls from an agent (via `loom_progress`, `loom_done`, etc.) work end-to-end.

### Phase 7 — UI shell (native AppKit via `objc2`) *(4–5 weeks — scope increased by pivot)*
- `loom-ui-native` crate with `render` (pure-Rust, tested) + `appkit` (feature-gated) layers.
- NSApplication + menubar + NSWindow + NSSplitView + NSOutlineView (sidebar) + content area.
- Main-thread event pump marshals deltas to UI refresh via `dispatch2::Queue::main()`.
- Keyboard shortcuts via `NSMenuItem` key equivalents.
- **Exit criterion**: `cargo run --bin loom-app --features loom-ui-native/appkit` opens a native Cocoa window showing current groups/agents, with a placeholder content area ready for Phase 8's terminal views.

### Phase 8 — libghostty terminals *(3–4 weeks)*
- `loom-ghostty` bindings + Metal layer view.
- `GhosttyBackend` impl of `TerminalBackend`.
- Terminals render as `NSView` subviews of the content area (not separate windows — native embedded layout).
- Wire PTY bytes, input events (key/mouse → `ghostty_surface_key()`), resize.
- **Exit criterion**: selecting an agent in the sidebar mounts a libghostty terminal view in the content area; text round-trips; closing the cell frees the surface.

### Phase 9 — Polish *(ongoing)*
- Notifications, tab-color equivalent (window accent), boot-time parity, installer (`.app` bundle on macOS), crash reporting, telemetry opt-in.

### Total estimated effort

**~15 engineering weeks** (1 dev full-time) to reach feature parity with Python Loom's standalone mode, minus iTerm2-specific features. Phase 8 carries the most uncertainty (libghostty is new territory); everything 0–6 is mechanical porting with known-good Rust crates.

## 12. CLI compatibility strategy

- `bin/loom` is not ported in v1. It already reads SQLite directly and POSTs to `/api/cmd` — both work against the Rust daemon unchanged.
- SQLite schema is identical (same `CREATE TABLE` statements). We pin the schema by running the Python daemon's `db_schema.py::initialize_database` semantics in Rust.
- The daemon's install location changes: `~/Library/Application Support/Loom/` (new app) instead of `.../iTerm2/Scripts/loom/loom/`. The CLI's SQLite path discovery (`get_state_local`) needs one new fallback path.
- Port `bin/loom` itself to Rust with `clap` in a later phase (not blocking v1) if startup latency on CLI calls matters.

## 13. Testing strategy

- **Unit tests per crate.** `loom-core` state transitions, `loom-actions` rendering, `loom-worktree` git helpers.
- **Golden-file tests** for action rendering: every action in `.loom/actions/` + `~/.loom/actions/` renders to a stored expected output. Regression-proof against minijinja behavior drift.
- **Cross-daemon parity tests** (Phases 2–6): a small harness that sends the same `/api/cmd` payload to Python and Rust daemons and diffs the response/DB state. Gate each phase's exit criterion on this passing.
- **Playwright UI tests** already exist in `tests/`. They're WebSocket-level, so they should pass unchanged against the Rust server. Use them as integration tests.
- **libghostty** has the least test coverage — plan for manual QA in Phase 8.

## 14. Open questions

- **libghostty licensing and linking.** Ghostty is MIT-licensed (confirm before vendoring). Static vs. dynamic link decision drops out of that review.
- **Which minijinja features we need beyond defaults.** We rely on `SandboxedEnvironment`, `StrictUndefined`, `| default()`, macros (if any action uses them), and AST variable discovery. Audit all existing actions before Phase 3 to confirm.
- **Delta broadcast fan-out.** Python's single-consumer WS assumption breaks if we want the UI and CLI to both subscribe. `tokio::sync::broadcast` handles this, but backpressure policy needs a call (we propose: lagged receivers get a forced resync, matching today's seq-gap behavior).
- **PTY ↔ libghostty pairing.** Decision pending: drive the PTY ourselves (parity with `local_pty.py`) vs. let libghostty own it (simpler but forks the adapter event model).
- **Webview asset loading under Tauri.** Today the webview fetches `static/js/*.js` over HTTP from the daemon. Under Tauri we can serve them as `asset://` URLs for speed — need to verify no existing code assumes same-origin with the WS endpoint.
- **Global shortcuts on Linux**. Tauri's global-shortcut plugin on Linux is less robust. May need a dedicated path per OS.

## 15. Risks

| Risk | Mitigation |
|---|---|
| libghostty bindings stall (most likely schedule risk) | Phase 7 ships without ghostty (UI shell only, terminals still rendered externally via some interim). UI is usable while Phase 8 is in progress. |
| minijinja behavior drift from Jinja2 in edge cases | Golden-file parity tests in Phase 3 catch drift early; fall back to `tera` or a Jinja2-via-embedded-Python shim if catastrophic. |
| `git2` feature gaps (e.g. `merge-tree --write-tree`) | Already in the plan to shell out for those ops — `git2` is a speedup, not a replacement. |
| Frontend code has hidden assumptions about Python daemon quirks | The Playwright suite is load-bearing here. Invest in parity tests early. |
| Scope creep on the UI (Path A → Path B mid-flight) | Hard-commit to Path A through Phase 8. Revisit only after a full feature-parity ship. |

## 16. First commits (Phase 0)

1. `rust/Cargo.toml` workspace manifest with empty crates.
2. `rust/crates/loom-core/src/lib.rs` + `state.rs` with `AgentCell`, `GroupSettings`, `GlobalSettings`, `BoardTask` structs (serde-deriving, no logic yet) — port the dataclass shapes from `loom/state.py`.
3. `rust/crates/loom-core/src/delta.rs` with the `DeltaOp` enum.
4. `rust/README.md` — build instructions, toolchain versions (Rust 1.78+, Zig 0.13+ for libghostty), dev workflow.
5. CI workflow: `cargo fmt --check`, `cargo clippy -- -D warnings`, `cargo test`.

---

**Owner**: Aleksander
**Status tracker**: to be opened as a Loom board lane once Phase 0 lands.
