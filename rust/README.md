# Loom (Rust)

Rust port of the Loom daemon. Standalone spin-off product — not a drop-in replacement for the Python daemon. See [`PLAN.md`](PLAN.md) for the full implementation plan.

## Status

Under active development. Phases 0–6 (engine, actions, worktree, dispatch, MCP) aim for feature parity with Python Loom minus iTerm2 integration. Phases 7 (Tauri UI) and 8 (libghostty terminals) are the new-product layer.

## Toolchain

- Rust 1.78+ (stable channel — pinned in `rust-toolchain.toml`).
- macOS 13+ (Apple Silicon or Intel).
- `git` 2.35+ on `$PATH` (for shell-out paths in `loom-worktree`).
- **Phase 8 only**: Zig 0.13+ for building vendored libghostty. Install via `brew install zig`.

## Build

```sh
cd rust/
cargo build                 # debug
cargo build --release       # release
cargo test --workspace      # all tests
cargo clippy --workspace -- -D warnings
cargo fmt --check
```

## Run the engine (headless, no UI)

```sh
cargo run --bin loom-app -- --headless
```

Listens on `http://127.0.0.1:18932` for the existing `bin/loom` CLI, Claude Code hooks, and MCP.

## Workspace layout

```
crates/
├── loom-core/       State, delta ops, SQLite persistence, event bus
├── loom-actions/    YAML action loading + minijinja rendering
├── loom-worktree/   Git worktree + checkpoint + merge detection
├── loom-pty/        PTY spawning and I/O via portable-pty
├── loom-adapters/   Claude Code / Codex / Gemini / generic agent adapters
├── loom-weaver/     MCP handler, weaver, task-health, hints
├── loom-server/     Axum HTTP/WS server + command dispatcher
├── loom-ghostty/    libghostty FFI bindings + terminal view bridge (Phase 8)
└── loom-app/        Main binary — wires the engine and UI shell
```

Each crate has its own `src/` + unit tests. Integration tests live alongside the crate that owns the flow under test.

## Testing philosophy

- **Unit tests** per crate, colocated with the module they cover.
- **Golden-file tests** for action rendering: every `.loom/actions/*.yaml` fixture renders to a stored expected output.
- **Integration tests** for server command parity — run real axum handlers against an in-memory sqlite DB.
- The existing Python test suite under `../tests/` is the source of truth for behavior; port a Rust equivalent per phase.

## Data locations

New app uses its own paths (no collision with Python Loom):

- SQLite DB: `~/Library/Application Support/Loom/loom.db`
- Attachments: `~/Library/Application Support/Loom/attachments/`
- Logs: `~/Library/Application Support/Loom/loom.log`

The `bin/loom` CLI needs a minor patch to discover this new path. That change lives on the CLI side and is tracked separately.

## What does NOT port

- `loom/bridge.py` — iTerm2 SDK adapter. Replaced by `loom-ghostty` + `loom-pty`.
- `loom/keybindings.py` — iTerm2 global key bindings. Replaced by Tauri global-shortcut plugin in Phase 7.
- `loom/desktop.py` — the Python desktop launcher. Replaced by the `loom-app` binary.
