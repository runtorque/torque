# Decisions + gotchas

Load-bearing choices made during the port, with rationale + what broke along the way. Read before second-guessing any of the below.

## Strategy

### D1. Spin-off product, not a port
**Decision**: New macOS-first product. Does not replace Python Loom; they coexist on different data paths (different SQLite file locations, different app identity). `bin/loom` CLI continues to work against either daemon.
**Why**: User preference. Python Loom stays shippable for iTerm2 users; Rust Loom is the "native desktop" direction.
**Consequence**: No cross-daemon migration. No shared state directory. `~/Library/Application Support/Loom/` is the new home.

### D2. Engine is portable Rust; UI is per-platform
**Decision**: Every crate except `loom-ui-native` is platform-independent. Linux UI is a future `loom-ui-linux` crate consuming the same `EngineBridge`.
**Why**: Ghostty itself uses this pattern (Swift/AppKit on Mac, Zig/GTK on Linux, shared Zig core). Native feel per OS with zero engine duplication.
**Consequence**: v1 ships Mac-only. Linux is deferred, not blocked.

### D3. AppKit via `objc2`, not webview
**Decision**: Rejected Tauri + webview mid-project (was Path A in the original plan). Went native AppKit.
**Why**: User wants "full-fledged native application built around libghostty." Embedded terminals need native Metal surfaces; webviews can't interleave with native rendering cleanly.
**Consequence**: The entire JS frontend from Python Loom is not ported. UI chrome gets rewritten native. This is ~4–6 weeks of future work beyond the v1 scaffold.

### D4. Full libghostty, not libghostty-vt
**Decision**: Use the full `ghostty_surface_t` embedding API. Ghostty owns rendering + PTY + input translation.
**Why**: Zero renderer code for us. Best terminal quality with least effort. libghostty-vt would mean we own the renderer — hundreds of lines of wgpu/Metal glyph code.
**Consequence**: Mac-only on the terminal side. The C embedding API's platform enum is `MACOS | IOS | INVALID` — no Linux. A future Linux UI will need a different terminal integration (libghostty-vt, or Ghostty's GTK path via Zig).

### D5. PTY ownership: Ghostty, not us
**Decision**: Each `ghostty_surface_config_s` carries `command`, `working_directory`, `env_vars`. Ghostty spawns the child. Our `loom-pty::LocalPtyBackend` remains for non-UI / headless / test paths.
**Why**: Ghostty's PTY management handles signal propagation, window size propagation, escape sequence handling — complex work we don't want to duplicate.
**Consequence**: Two terminal backends coexist. UI agents run through Ghostty; headless code keeps using LocalPtyBackend. Dispatching prompts to a UI agent uses `GhosttyView::send_text` (which calls `ghostty_surface_text` — the IME path — OR should call `ghostty_surface_key` for real keystrokes; see G5 below).

### D6. In-process dispatch for the UI
**Decision**: The AppKit layer calls `loom_server::commands::dispatch_command(ctx, cmd, body)` directly. Does not open HTTP connections to its own server.
**Why**: Latency + reliability. The HTTP surface is the external API for the CLI / hooks / MCP; internal UI traffic doesn't need it.
**Consequence**: `CmdContext` is shared between HTTP handlers and native UI callers. Both must work from any thread the tokio runtime schedules on.

### D7. Delta broadcast with seq gap = resync
**Decision**: Matches Python Loom. `MatrixState::drain_deltas` returns `(seq, ops)`; WS clients that lag trigger a forced snapshot.
**Why**: Frontend-compat — the existing JS frontend uses this contract. Even though the Rust UI doesn't use it, the CLI and future web clients might.
**Consequence**: Every mutation must go through a method that calls `emit()`. Bypassing this path (mutating state directly from a raw handle) silently breaks broadcast.

## Technical

### T1. `objc2` 0.5 — `MainThreadOnly` alloc path
`NSView`, `NSWindow`, etc. are `MainThreadOnly`. The `ClassType::alloc()` (zero-arg) fails the `MutabilityIsAllocableAnyThread` bound for these. Use `mtm.alloc::<T>()` (via `MainThreadMarker::alloc`) instead.

### T2. `objc2-foundation::MainThreadMarker`, not `objc2::MainThreadMarker`
The re-export moved between objc2 versions. In 0.5 it lives in `objc2_foundation`.

### T3. Feature flags for `objc2-app-kit`
Every AppKit type needs its feature gated in Cargo.toml. `NSEvent`, `NSTimer`, `NSTextView`, `NSScrollView` etc. are all separate features. Missing features produce "unresolved import" errors that look unrelated to features.

### T4. Enum variant names in `objc2-app-kit`
Variants carry their `NS*` prefixes. Not `NSBackingStoreType::Buffered` but `NSBackingStoreType::NSBackingStoreBuffered`. Not `NSBorderType::NoBorder` but `NSBorderType::NSNoBorder`. Not `NSEventModifierFlags::Shift` but `NSEventModifierFlags::NSEventModifierFlagShift`. The compiler's "did you mean" hint is reliable — use it.

### T5. `ghostty_surface_text` vs `ghostty_surface_key` — DON'T CONFUSE
- `ghostty_surface_text(surface, ptr, len)`: writes to the **display buffer as preedit/IME text**. Does **not** send bytes to the PTY child. Using this for keyboard input produces a terminal that shows what you type but the shell never receives it.
- `ghostty_surface_key(surface, ghostty_input_key_s)`: **proper** key event path. Ghostty translates into PTY bytes (including Enter/Tab/arrows). This is what `keyDown:` should call.
- We lost an afternoon to this. The `write_text` helper on `Surface` is for programmatic dispatch (typing a prompt); `send_key` is for real keystrokes.

### T6. Xcode full install is required, not just Command Line Tools
Zig's `LibCInstallation.findNative` hunts specifically for `/Applications/Xcode.app/...`. Command Line Tools' SDK is at `/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk` and doesn't count. Symptoms: `error: DarwinSdkNotFound`. Fix: install full Xcode, `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`.

### T7. Metal Toolchain is a separate Xcode component
Xcode 26 ships without the Metal compiler toolchain by default. Zig's build of libghostty's metallib fails: `cannot execute tool 'metal' due to missing Metal Toolchain`. Fix: `sudo xcodebuild -downloadComponent MetalToolchain`.

### T8. `TOOLCHAINS=Metal` for the Zig build
Even after `xcodebuild -downloadComponent MetalToolchain`, `xcrun -sdk macosx metal` fails: the Metal toolchain lives in a cryptex mount (`/var/run/com.apple.security.cryptexd/...`) that `xcrun`'s default resolver doesn't find. `xcrun -toolchain Metal metal --version` works. Setting `TOOLCHAINS=Metal` as an env var makes `xcrun` pick it up for the duration of the build.

```sh
TOOLCHAINS=Metal zig build -Demit-xcframework=true \
  -Dxcframework-target=native -Doptimize=ReleaseFast
```

### T9. Carbon framework must be linked
libghostty uses `TISCopyCurrentKeyboardLayoutInputSource` + friends for keyboard layout detection. These live in `Carbon.framework`. Without it, link fails with `Undefined symbols: _TISCopyCurrentKeyboardLayoutInputSource` etc. See the frameworks list in `loom-ghostty/build.rs`.

### T10. libghostty artifacts location
The "full" `libghostty.a` with the embedding API (as opposed to `libghostty-vt.a`) is **not** installed to `zig-out/lib/` by default. It lives in `.zig-cache/o/<content-hash>/libghostty-fat.a`. We stage it to `third_party/ghostty-build/lib/libghostty.a` as part of the one-time build procedure. The content hash is unstable — always re-find via `find .zig-cache -name libghostty-fat.a` after rebuilding.

### T11. libghostty.a is a fat binary (arm64 + x86_64)
140 MB. Do not check into git. Listed in `rust/.gitignore`. Rebuild locally per machine.

### T12. `LOOM_PORT` env var for dev iteration
Default port 18932 collides with the Python Loom daemon if it's running. Use `LOOM_PORT=28932 cargo run ...` for dev sessions.

### T13. Custom ObjC classes + `declare_class!`
For subclassing `NSView` you need `type Mutability = mutability::MainThreadOnly;` (since `NSView` itself is). For a plain no-op `NSObject` subclass (e.g. the timer target), `mutability::InteriorMutable` lets you use `Self::alloc()` without a `MainThreadMarker`.

### T14. `ghostty_runtime_config_s` callback signatures are strict
6 callbacks (`wakeup_cb`, `action_cb`, `read_clipboard_cb`, `confirm_read_clipboard_cb`, `write_clipboard_cb`, `close_surface_cb`) — signatures must match what bindgen generated. Particularly `write_clipboard_cb` takes `(userdata, clipboard, contents, count, confirm)` not `(userdata, text, clipboard, confirm)` as the name might suggest. If you guess wrong, you get "incorrect number of function parameters" on `Some(cb_write_clipboard)`.

### T15. Release builds of libghostty are required
`-Doptimize=ReleaseFast` is not optional. Debug builds produce a `libghostty.a` so large (~400 MB+) it chokes linker, and takes forever to build.

## Cleanup landmines

Things that look like dead code but aren't:

- `_UiState` type alias in `loom-ui-native/src/window.rs` — future state for sidebar selection. Remove only after sidebar interactivity lands.
- `sys_types` re-export from `loom-ghostty/src/lib.rs` — the key-forwarding code in `loom-ui-native` imports from here. Don't inline it.
- `ghostty_config_t` in `GhosttyApp` — don't drop before `ghostty_app` because `ghostty_app_free` references the config internally.

## Known regressions / rough edges

- **No mouse support.** `mouseDown:`/`mouseMoved:`/`scrollWheel:` are not wired. Text selection in the terminal doesn't work.
- **No clipboard.** Cmd+C / Cmd+V in the terminal pane isn't wired — the clipboard callbacks are stubs.
- **Sidebar is read-only.** NSTextView dump. No click-to-select, no drag-drop, no context menu. Will become an NSOutlineView.
- **Hardcoded `/bin/zsh`.** The content area unconditionally spawns a shell. Tying GhosttyView lifecycle to sidebar selection is the next real piece of UI work.
- **No dispatch integration with GhosttyView.** The dispatch path spawns a separate `loom-pty` process (for non-UI flows) but doesn't feed the GhosttyView for UI flows yet.
- **Engine's `tasks_dispatched` counter increments even without a real agent.** `dispatch_task` currently can't tell whether the target agent is UI-hosted or PTY-only.
