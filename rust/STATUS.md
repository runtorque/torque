# Loom Rust — status

Snapshot of what exists, what works, and what's left. Paired with [`PLAN.md`](PLAN.md).

## Build + test

```sh
cd rust/
cargo test --workspace                                        # 80 tests passing
cargo build --bin loom-app                                    # engine + UI stub
cargo run --bin loom-app --features loom-ui-native/appkit     # native window
```

## Product direction

**Native macOS app built around libghostty** (updated 2026-04-13).

- The Python Loom webview / JS frontend is **not** ported. `rust/assets/web/` was removed.
- `loom-ui-native` rebuilds the UI chrome natively (NSWindow, NSSplitView, NSOutlineView).
- Terminal panes inside the main window are backed by libghostty's Metal renderer (Phase 8).
- HTTP/WS stays up for the existing `bin/loom` CLI + Claude Code hooks + MCP. The UI bypasses HTTP and talks to the engine in-process.

## What works end-to-end

- **HTTP/WS engine** on axum (port via `LOOM_PORT`, default 18932).
- **Snapshot on connect + delta broadcast** — same wire format consumers (CLI/MCP) expect.
- **Lagged receivers trigger forced resync** — matches Python's seq-gap behavior.
- **SQLite persistence** with WAL, targeted writes per mutation, full load on startup, ephemeral clearing.
- **35 commands** wired through the in-process dispatcher (groups, agents, tasks, actions, worktree, dispatch, MCP). Exposed both via `/api/cmd` and via `loom_server::commands::dispatch_command` for the native UI.
- **Action engine** (`loom-actions`) — YAML + subdir namespaces + minijinja + `loom.*` context, fixtures from `.loom/actions/` pass.
- **Worktree engine** (`loom-worktree`) — git2 diff + git worktree/checkpoint/rollback shell-out, merge detection.
- **PTY engine** (`loom-pty`) — `portable-pty` via `/bin/sh -c`. Spawn/write/resize/close tested.
- **Dispatch + ai_report** — create/reuse agents, render action prompts with full `loom.*` context, spawn PTY, update task + agent state.
- **MCP handler** on `/mcp` — initialize, tools/list, tools/call for `loom_progress|done|ready|blocked|error|ask|context|derive`.
- **Cron scheduler** — 15s tick, fires `scheduled_at` tasks.
- **Native UI** (`loom-ui-native`):
  - `render` module: pure-Rust display logic, 5 unit tests.
  - `appkit` module (feature-gated): full object graph wired — NSApplication, menubar (App menu → Quit/Cmd-Q), NSWindow (Titled/Closable/Resizable), NSSplitView with sidebar + content NSScrollView+NSTextView (monospace system font, auto-resizing). NSTimer-based 500ms refresh loop redraws both text views from the engine snapshot. Verified: binary launches, NSApplication run loop holds the process, engine stays responsive on HTTP during UI lifetime.
  - `bridge` module: `EngineBridge::snapshot/dispatch/subscribe` for in-process UI↔engine comms.

## Test summary

```
loom-core:        33 unit tests
loom-actions:     13 unit tests
loom-adapters:     2 unit tests
loom-ghostty:      3 unit tests (ffi.register + symbols_linked)
loom-pty:          1 integration (spawns /bin/sh)
loom-server:      23 integration across 6 files (api_cmd, ws, actions, worktree, dispatch, mcp)
loom-worktree:     1 unit test
loom-ui-native:    5 unit tests (render logic)
-------------------------------------
total:            82 passing, 0 failing
```

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
- Wired into the main window's content area: `cargo run --bin loom-app --features loom-ui-native/appkit` opens a native Cocoa window with the Loom sidebar on the left and a fully interactive libghostty-rendered `/bin/zsh` on the right. Verified by hand.

## What's deferred (follow-up work)

Commands present in Python Loom but not yet in Rust:

- `memory_*`, `template_*`, `weaver_*`, `schedule_*` CRUD, `external_*`, `playbook_*`, multipart uploads, advanced worktree ops (merge/rebase/pr/conflicts/diff_full), Claude Code hook install, agent template system.

All mechanical ports now that the skeleton is in place.

## Next steps

1. **Tie GhosttyView to sidebar selection**: right now the content area unconditionally spawns `/bin/zsh`. Replace with "one GhosttyView per agent cell, created on first selection, reused on re-select, freed on agent removal". The command, cwd, and env come from the `AgentCell` fields.
2. **Dispatch prompts to the surface**: on `dispatch_task`, call `view.send_text(rendered_prompt + "\r")` on the target agent's GhosttyView. This replaces the current `loom-pty` spawn path for UI-attached agents.
3. **Mouse events** — `mouseDown:`/`mouseMoved:`/`scrollWheel:` → `ghostty_surface_mouse_*`. Needed for selection/link-hover/scroll.
4. **Full key translation** — route Cmd+C / Cmd+V through Ghostty's clipboard callbacks; handle Return/Tab/arrow keys via keycode→ghostty_input_key_e mapping.
5. **Sidebar interactivity**: swap the NSTextView sidebar for NSOutlineView + data source so rows are clickable and drive agent selection.
6. **Claude Code hook install** — dispatched agents need `.claude/settings.local.json` wired before `ai_report` round-trips.
7. **Linux UI** — when Mac+engine is stable, start `loom-ui-linux` (GTK4 via `gtk4-rs`, libghostty-vt or full libghostty's GTK path for terminals). Engine doesn't move.
