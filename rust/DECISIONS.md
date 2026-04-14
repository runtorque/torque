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

### D8. Edge-dock zones around a tileable center
**Decision**: The native window's outer layout is a 5-zone cross: `Top`, `Left`, `Right`, `Bottom` edges + a `Center` region. The Center remains the existing tileable grid (the `LayoutNode` tree, unchanged). Only the edges are new; each edge zone holds an `Option<LayoutNode>` (None = hidden), each with a 22pt panel header for move/hide.
**Why**: User directive ("proper system for attaching panels to different locations") plus the need to dock Board at the bottom and the Sidebar at the left. Classic IDE shape (Xcode / VSCode) — each zone is a clear home for a panel.
**Consequence**: `MatrixState.dock_edges` is the new authoritative source for edge placement; `content_layout` is the Center. `effective_dock_layout()` combines them. Single-panel-per-zone for v1 — tabbed docks (multiple panels per zone) remain a future extension point. Re-docking uses two dispatches: clear source zone, set target zone. Drag-to-redock (VSCode zone indicators) is deferred.

### D9. Sidebar is a `PanelKind`, not a hardcoded rail
**Decision**: The agent/terminal outline is `PanelKind::Sidebar` — a normal dockable panel that defaults to the Left zone. Moving it to Right (or Top / Bottom) works the same way as moving any other panel.
**Why**: Architectural uniformity with the dock system. Treating the sidebar as "special" would fork every dock-related code path.
**Consequence**: `SidebarView::install` is called from `mount_panel_into` when a leaf's panel kind is `Sidebar`. The sidebar view is cached in `ContentState.sidebar_cache` so its expansion state + selection survive dock rebuilds. Same pattern as `BoardView`.

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

### T16. Layout-rebuild signature short-circuit
The content area's reconcile pass walks the layout tree and rebuilds nested NSSplitViews on each tick. Naively rebuilding every 500 ms breaks GhosttyView (re-parenting churns the surface, focus jumps, visible flicker). Fix: compute a JSON signature of `{ layout, selected_agent_id }` per tick and skip rebuild when unchanged. The `agent_id → CachedAgent` map keeps `Retained<GhosttyView>` alive across rebuilds, so terminals keep their PTYs + scrollback when the layout *does* change.

### T17. Two terminal-spawn paths must not race
A UI-attached agent has its PTY owned by libghostty's `Surface`. If `dispatch_task` falls through to `LocalPtyBackend::spawn` for the same agent, you get two child processes and the prompt lands in the headless one. Fix: `UiAgentRegistry::is_attached()` is the gate; check it before any PTY spawn or write. The UI registers each `GhosttyView` it creates and unregisters on cache eviction.

### T18. `tokio::sync::mpsc::UnboundedReceiver::try_recv` for sync drains
The dispatch path queues prompts via `mpsc::unbounded_channel`. The AppKit refresh tick (sync code on the main thread) drains via `try_recv` in a loop until `Empty`. Don't reach for `recv().await` — the tick isn't async, and blocking the main thread freezes the UI.

### T19. `serde_json::Map::entry` needs explicit type annotation
`obj.entry("source_kind".into()).or_insert_with(...)` fails to infer `S: Into<String>` because both `&str` and `String` satisfy it. Use `if !obj.contains_key("source_kind") { obj.insert("source_kind".into(), ...); }` instead.

### T20. NSArray in objc2 0.5 has no `iter()` method
Iterating subviews: use `subviews.count()` + `subviews.objectAtIndex(i)`, not `for v in subviews.iter()`.

### T21. NSSplitView axis semantics are flipped from intuition
`NSSplitView::setVertical(true)` makes the *divider* vertical, which means the panes sit **side by side** (horizontal layout). Our `SplitAxis::Horizontal` (panes side by side) maps to `setVertical(true)`. Pay attention.

### T22. `NSSplitView::setPosition_ofDividerAtIndex` needs bounds
Calling this before the split view has a non-zero frame is a no-op. We compute the position from the *parent's* current bounds when applying a ratio; the next resize tick re-applies if needed.

### T23. Process-wide test races — three known
`#[test]` functions run in parallel by default. Three pre-existing tests raced because they mutate process-wide state: `LOOM_PROJECT_ROOT` (`loom-server/tests/actions.rs`), `LOOM_DEFAULT_CMD` (`loom-core/src/config.rs`), and the `SURFACES` map (`loom-ghostty/src/ffi.rs`). Each is now gated by a `static MUTEX`. New tests touching the same state must hold the same lock — see `ARCHITECTURE.md` § "Race-prone tests".

### T24. `Retained<AnyObject>` has no `.downcast()` in objc2 0.5.2
Bare `Retained<AnyObject>::downcast::<T>()` doesn't exist. Pattern is: check `isKindOfClass:` via `msg_send!` then call `Retained::cast::<T>(obj)` (unsafe). We ship two helpers in `sidebar.rs` and `board.rs`:

```rust
unsafe fn downcast_retained<T: ClassType>(obj: Retained<AnyObject>) -> Option<Retained<T>> {
    let cls = T::class();
    let is_kind: bool = msg_send![&*obj, isKindOfClass: cls];
    if is_kind { Some(Retained::cast::<T>(obj)) } else { None }
}
```

For cases where we put the object in the outline ourselves (so we know the class by construction), we skip the check and call `Retained::cast` directly.

### T25. `declare_class!` method bodies can't early-return
`#[method(...)]` and `#[method_id(...)]` wrap *only* the tail expression in the ObjC ABI return type (`Bool`, `IdReturnValue`). An early `return None` or `return false` inside the body fails the trait check.

Fix: keep the whole method body a single expression. Use `match` or `if/else` instead of early `return`:

```rust
#[method_id(foo:)]
fn foo(&self, ...) -> Option<Retained<NSMenu>> {
    if bad { None } else { Some(...) }   // NOT: if bad { return None; } ...
}
```

### T26. `NSArray::from_slice(&[&NSString])` fails; use `from_vec`
`NSArray::from_slice` requires `T: MutabilityIsRetainable`. `NSString` is `ImmutableWithMutableSubclass<NSMutableString>`, which isn't `MutabilityIsRetainable`. Workaround: build `NSArray::from_vec(vec![retained_nsstring])` — takes owned `Retained<NSString>` values.

Occurs when registering dragged types on `NSTableView` (see `board.rs` and `sidebar.rs`).

### T27. NSClipView anchors smaller document views at its origin
In a horizontally-scrolling `NSScrollView`, when the document view is shorter than the clip view's visible area, `NSClipView` positions the doc view at its origin — which in non-flipped coordinates is the *bottom*. Result: the whole content strip floats to the bottom of the panel with empty space above.

Fix: set `NSViewHeightSizable` on the doc view *and* on any direct children, plus re-run our own `layout_columns`/frame-update on every refresh tick (even when data is unchanged) so window resizes propagate immediately. Saw this as the "board pinned to bottom of panel" bug.

### T28. `NSTableColumn::initWithIdentifier` not exposed in objc2-app-kit 0.2
The identifier-taking initializer isn't generated (its arg type `NSUserInterfaceItemIdentifier` is gated behind a feature we don't enable). Workaround: plain `msg_send_id![alloc, init]` to get a default column. Our code doesn't reference columns by identifier anywhere, so the default is fine. `setIdentifier:` also isn't available in the same way — skip setting it.

### T29. `NSBezelStyle` + `NSSegmentStyle` variants missing; use raw NSInteger
Several enum variants we'd reach for aren't exposed in objc2-app-kit 0.2:

- `NSBezelStyle::NSBezelStyleRounded` — missing. We just omit `setBezelStyle:` on our buttons; the default (rounded push-button) is what we want anyway.
- `NSSegmentStyle::NSSegmentStyleTexturedRounded` — missing. Send raw NSInteger 0 (NSSegmentStyleAutomatic) via `msg_send!` instead.
- `NSTableViewSelectionHighlightStyle::NSTableViewSelectionHighlightStyleSourceList` — missing. Send raw NSInteger 1 via `msg_send!`.

Pattern:
```rust
let _: () = msg_send![&outline, setSelectionHighlightStyle: 1isize];
```

### T30. `NSTextField.setTarget` target isn't retained — hold it yourself
Cocoa's `NSControl.target` is a weak/unretained pointer. If the target object drops while the control references it, keystrokes / clicks produce EXC_BAD_ACCESS. Our `BoardMenuTarget` / `MenuActionTarget` / `HeaderTarget` are kept alive by Retained fields in `BoardView` / `SidebarView` / (for the panel header) a `std::mem::forget`-held reference — v1 accepts the leak; follow-up should use associated objects on the button to attach the target's lifetime to the NSButton.

### T31. `NSScrollView::contentSize` is unsafe in objc2 0.5
`contentSize()` on `NSScrollView` requires an `unsafe` block. Don't forget the wrapper when reading it for layout math.

## Cleanup landmines

Things that look like dead code but aren't:

- `sys_types` re-export from `loom-ghostty/src/lib.rs` — the key-forwarding code in `loom-ui-native` imports from here. Don't inline it.
- `ghostty_config_t` in `GhosttyApp` — don't drop before `ghostty_app` because `ghostty_app_free` references the config internally.
- `CachedAgent.rx` in `loom-ui-native::appkit` — read on every refresh tick via `try_recv`. Removing it breaks UI-attached dispatch silently.
- `UiAgentRegistry` field on `AppState` — looks unused in tests that pass `Default::default()`, but the dispatch path checks `ctx.ui_agents.is_attached()` on every call. If you delete it, headless dispatch still works but real UI dispatch goes through the wrong path.

## Known regressions / rough edges

- **No mouse support in terminal.** `mouseDown:`/`mouseMoved:`/`scrollWheel:` are not wired on `GhosttyView`. Text selection in the terminal doesn't work.
- **No clipboard in terminal.** Cmd+C / Cmd+V isn't wired — the clipboard callbacks are stubs.
- **Sidebar drag-drop not wired.** Click-to-select, context menu, and keyboard nav work. Dragging a row to reorder groups or move an agent across groups isn't implemented yet.
- **Board autogrowing textarea.** Inline add-task is a single-line `NSTextField`; the Python UI's autogrowing textarea is the follow-up.
- **Non-Sidebar/Board panels render as placeholders.** Actions, Memory, Weaver, Context, Events, Templates show a single-line hint until each one's native renderer lands. The layout machinery, persistence, and command surface are all in place — only the per-panel views are missing.
- **No drag-to-split / close-pane / pane keyboard shortcuts.** Layout has to be set programmatically via `set_layout` / `dock_panel`.
- **No drag-to-redock.** Panels re-dock via the `…` popup menu on each header bar (Move to → {Top,Left,Right,Bottom}); no VSCode-style drop indicators yet.
- **`cron_expr` is stored but not fired.** The scheduler tick only fires `scheduled_at` one-shots; recurring schedules need a cron parser + `next_run_at` advancement.
- **Memory transient-entry expiry isn't swept.** `retention_kind: "transient"` + `expires_at` are honored on read filters but no background sweep purges expired rows.
- **`/events` HTTP receiver is scaffolded but not routed into a per-cell EventLog.** Activity badges in the sidebar are based on `ai_report` / PTY events only.
- **Archived-lane reveal toggle missing.** `build_board_columns` filters out Archived; CLI + API still archive/unarchive tasks. UI affordance to reveal them is a small follow-up.
- **Panel-header target leak.** `HeaderTarget` in `panel_header.rs` is held alive via `std::mem::forget` because `NSMenuItem.target` isn't retained by Cocoa. Leaks one NSObject per edge-zone rebuild — acceptable for v1.
