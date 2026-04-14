# Loom (Rust)

Native macOS port of the Loom daemon. Standalone spin-off product — not a drop-in replacement for Python Loom.

**Read before diving in**: [`ARCHITECTURE.md`](ARCHITECTURE.md) · [`DECISIONS.md`](DECISIONS.md) · [`STATUS.md`](STATUS.md) · [`PLAN.md`](PLAN.md)

## Status

v1 macOS scaffold is runnable. Engine (Phases 0–6), native AppKit shell (Phase 7), and libghostty embedded terminal (Phase 8) all shipped. Window opens, shell runs inside a libghostty-rendered pane, keystrokes reach the child process. 82 tests green.

## Requirements

- macOS 13+
- **Full Xcode** (not just Command Line Tools) — see [`DECISIONS.md` T6](DECISIONS.md#t6-xcode-full-install-is-required-not-just-command-line-tools)
- **Metal Toolchain**: `sudo xcodebuild -downloadComponent MetalToolchain`
- Rust stable (currently 1.94)
- Zig 0.15.2: `brew install zig`
- `git` 2.35+

## One-time setup

```sh
# Clone Ghostty source + build libghostty
cd rust/third_party
git clone --depth 20 https://github.com/ghostty-org/ghostty.git
cd ghostty
TOOLCHAINS=Metal zig build -Demit-xcframework=true \
  -Dxcframework-target=native -Doptimize=ReleaseFast

# Stage artifacts where loom-ghostty's build.rs looks for them
cd ..
mkdir -p ghostty-build/{lib,include}
cp -R ghostty/include/. ghostty-build/include/
cp "$(find ghostty/.zig-cache -name libghostty-fat.a | head -1)" \
   ghostty-build/lib/libghostty.a
```

Details + troubleshooting: [`third_party/README.md`](third_party/README.md).

## Running

```sh
cd rust/

# All tests
cargo test --workspace

# Headless engine only — HTTP on 127.0.0.1:18932
# (Use LOOM_PORT=28932 to avoid clash with Python Loom daemon)
cargo run --bin loom-app

# Full native UI + embedded libghostty terminal
cargo run --bin loom-app --features loom-ui-native/appkit
```

Once the native UI is running, drive the engine from another terminal:

```sh
curl -X POST http://127.0.0.1:18932/api/cmd \
  -H 'content-type: application/json' \
  -d '{"cmd":"add_group","name":"Eng"}'
```

The sidebar repaints within 500 ms.

## Workspace layout

```
crates/loom-core/       State, delta ops, SQLite persistence, event bus
crates/loom-actions/    YAML action loading + minijinja rendering
crates/loom-worktree/   Git worktree + checkpoint + merge detection
crates/loom-pty/        portable-pty backend (non-UI / headless)
crates/loom-adapters/   Claude Code / Codex / Gemini / generic agent adapters
crates/loom-weaver/     Weaver / MCP tool specs / task health (scaffolded)
crates/loom-server/     axum HTTP/WS + command dispatcher + MCP + scheduler
crates/loom-ghostty/    libghostty FFI + GhosttyApp + Surface wrappers
crates/loom-ui-native/  AppKit window + GhosttyView (feature = "appkit")
crates/loom-app/        Main binary — wires engine + UI
```

## Data locations

- SQLite DB: `~/Library/Application Support/Loom/loom.db`
- Attachments: `~/Library/Application Support/Loom/attachments/`
- Logs: `~/Library/Application Support/Loom/loom.log`

Override with `LOOM_INSTALL_DIR=/some/path`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `DarwinSdkNotFound` on `zig build` | Command Line Tools only | Install full Xcode ([`DECISIONS.md` T6](DECISIONS.md#t6-xcode-full-install-is-required-not-just-command-line-tools)) |
| `cannot execute tool 'metal'` | Missing Metal Toolchain | `sudo xcodebuild -downloadComponent MetalToolchain` |
| `metal --version` fails after install | Toolchain is in cryptex mount | Prefix builds with `TOOLCHAINS=Metal` ([T8](DECISIONS.md#t8-toolchainsmetal-for-the-zig-build)) |
| `Undefined symbols: _TISCopyCurrentKeyboardLayoutInputSource` | Carbon framework missing | See `loom-ghostty/build.rs` — should be listed |
| `Address already in use (os error 48)` on port 18932 | Python Loom daemon running | `LOOM_PORT=28932 cargo run ...` |
| `libghostty not staged` panic during `cargo build` | Did not stage the `.a` | Re-run one-time setup above |
| Terminal renders but keystrokes don't reach shell | Using `ghostty_surface_text` instead of `ghostty_surface_key` | See [`DECISIONS.md` T5](DECISIONS.md#t5-ghostty_surface_text-vs-ghostty_surface_key--dont-confuse) |

## What does NOT port

From Python Loom:

- `loom/bridge.py` (iTerm2 SDK) — replaced by `loom-ghostty` + embedded terminal.
- `loom/keybindings.py` (iTerm2 global bindings) — replaced by `NSMenuItem` key equivalents.
- `webview.html` + `static/js/*` — the JS frontend. Not ported. Native UI is rebuilt in AppKit.
- `loom/desktop.py` (Python desktop launcher) — replaced by `loom-app` binary.
