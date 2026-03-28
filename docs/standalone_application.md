# Standalone Application Plan

## Executive Summary

Loom can become a standalone desktop application that controls iTerm2 (and eventually Ghostty) from outside the toolbelt. The current architecture is already well-suited for this: the UI is a plain HTML/CSS/JS webview served by an aiohttp server, and all iTerm2 interaction is isolated in `bridge.py` and `keybindings.py`. The iTerm2 Python API works identically from external processes — no API capabilities are lost by moving outside the toolbelt.

Ghostty support is feasible but limited. Ghostty's AppleScript API (macOS, v1.3+) covers session creation and text input, but lacks the event monitoring and session observation that Loom relies on heavily. A Ghostty adapter would require polling-based workarounds and would not reach feature parity with the iTerm2 adapter.

---

## Current Architecture

```
┌──────────────────────────────────────────────────┐
│ iTerm2 (WKWebView Toolbelt Panel)                │
│                                                  │
│  webview.html ←──HTTP──→ aiohttp server          │
│       ↕ WebSocket           ↕ Python iterm2 API  │
│  static/js/*            bridge.py, keybindings.py│
│                         adapters/, worktree.py   │
│                         state.py, db.py (SQLite) │
└──────────────────────────────────────────────────┘
```

**Key observation:** The webview and the server are already decoupled. The webview connects via `ws://${location.host}/ws` — no iTerm2-specific JavaScript APIs are used. All iTerm2 interaction happens server-side through `bridge.py`.

### iTerm2 API Surface (35 methods across 3 modules)

| Category | Methods | Used For |
|----------|---------|----------|
| **Lifecycle** | `run_forever`, `async_get_app` | Daemon loop, app state |
| **Sessions** | `async_send_text`, `async_get_variable`, `async_close`, `async_activate` | Agent/terminal control |
| **Windows/Tabs** | `async_create_tab`, `async_set_tabs`, `async_set_title`, `async_select` | Tab management, reordering |
| **Profile** | `LocalWriteOnlyProfile`, `set_tab_color*`, `async_set_profile_properties` | Tab colors |
| **Monitors** | `PromptMonitor`, `VariableMonitor`, `FocusMonitor`, `SessionTerminationMonitor` | Real-time state tracking |
| **Keybindings** | `async_get/set_global_key_bindings`, `@iterm2.RPC` | Global shortcuts |
| **Toolbelt** | `async_register_web_view_tool` | Webview panel (standalone removes this) |
| **Profiles** | `PartialProfile.async_query` | Profile list for modals |

### iTerm2 WKWebView Workarounds Already in Place

These workarounds make the UI portable to any browser/webview:
- Custom `showConfirm()` modal instead of `window.confirm()` (doesn't work in WKWebView)
- Custom context menus instead of native right-click menus
- All state persisted server-side in SQLite (no `localStorage` dependency)
- Dynamic WebSocket URL via `location.host`

---

## Phase 1: Standalone Desktop App (iTerm2 Backend)

**Goal:** Run Loom as a standalone window that controls iTerm2 externally.

### 1.1 External iTerm2 Connection

The `iterm2` Python package works identically from external processes. Connection flow:

1. Library connects to iTerm2's Unix socket at `~/Library/Application Support/iTerm2/private/socket` (or falls back to `ws://localhost:1912`)
2. Authentication via AppleScript: `tell application "iTerm2" to request cookie and key for app named "Loom"`
3. All 35 API methods work without restriction

**Prerequisites for users:**
- iTerm2 Preferences > General > Magic > "Enable Python API" must be on
- First launch triggers a macOS Automation permission prompt (one-time)

**No code changes needed in `bridge.py` or `keybindings.py`** — they already use the `iterm2` package generically. The only difference is how the connection is established.

### 1.2 Entry Point Changes

**Current:** `loom.py` calls `iterm2.run_forever(main)` which manages the connection lifecycle inside iTerm2's script environment.

**Standalone:** Same call, but from an external process. `iterm2.run_forever` handles external authentication automatically.

```python
# loom.py — modified entry point
import os
import sys

STANDALONE = os.environ.get("LOOM_STANDALONE", "").lower() in ("1", "true", "yes")

def main_standalone():
    """Launch as standalone app — connect to iTerm2 externally."""
    import iterm2
    iterm2.run_forever(main, retry=True)  # retry=True waits for iTerm2 to launch

def main_toolbelt():
    """Launch as iTerm2 toolbelt script (current behavior)."""
    import iterm2
    iterm2.run_forever(main)

if __name__ == "__main__":
    if STANDALONE:
        main_standalone()
    else:
        main_toolbelt()
```

### 1.3 Server Changes

Remove toolbelt registration when standalone:

```python
# server.py — conditional toolbelt registration
if not STANDALONE:
    await iterm2.tool.async_register_web_view_tool(
        connection,
        display_name="Loom",
        identifier="com.loom.toolbelt",
        reveal_if_already_registered=True,
        url=f"http://127.0.0.1:{WS_PORT}/",
    )
```

Optionally bind to `0.0.0.0` for network access (useful for remote/iPad access):

```python
bind_host = "0.0.0.0" if os.environ.get("LOOM_BIND_ALL") else "127.0.0.1"
site = web.TCPSite(runner, bind_host, WS_PORT, reuse_address=True)
```

### 1.4 Desktop Window Options

The webview needs a native window. Three viable approaches:

#### Option A: Tauri (Recommended)

- **Why:** ~5MB binary, native WebKit on macOS, Rust backend for system integration
- **How:** Thin Tauri shell that opens `http://127.0.0.1:18932/` in a native webview. The Python daemon runs as a sidecar process.
- **Pros:** Tiny binary, native feel, window management (always-on-top, docking), tray icon support
- **Cons:** Adds Rust build toolchain, sidecar process management

```
┌─────────────────────┐      ┌──────────────────────┐
│ Tauri App (native)  │      │ Python daemon         │
│                     │←HTTP→│ aiohttp server        │
│ WebKit webview      │      │ iterm2 API connection │
│ Tray icon, docking  │      │ SQLite, state         │
└─────────────────────┘      └──────────────────────┘
```

#### Option B: Electron

- **Why:** Proven approach, full Chromium, rich windowing APIs
- **How:** `BrowserWindow` loads `http://127.0.0.1:18932/`. Python daemon as child process.
- **Pros:** Mature ecosystem, easy packaging, cross-platform
- **Cons:** ~150MB binary, high memory usage

#### Option C: PyWebView

- **Why:** Pure Python, zero new language/toolchain
- **How:** `webview.create_window("Loom", "http://127.0.0.1:18932/")` — uses native WebKit on macOS
- **Pros:** Simplest integration, same language as daemon, pip-installable
- **Cons:** Limited windowing features (no tray icon, no docking), less polished

#### Option D: Plain Browser + macOS App Wrapper

- **Why:** Zero packaging overhead
- **How:** Python daemon + `open http://127.0.0.1:18932/` (or a minimal `.app` bundle via `py2app`/`Platypus`)
- **Pros:** No new dependencies, instant
- **Cons:** Browser chrome, no native window controls, no tray icon

**Recommendation:** Start with **Option C (PyWebView)** for rapid iteration since it's pure Python and requires no new toolchain. Graduate to **Option A (Tauri)** when native window features (docking, tray, always-on-top) become important.

### 1.5 UI Adjustments for Standalone

The current CSS is optimized for iTerm2's narrow toolbelt panel (~280px wide). A standalone window can be resized freely.

Changes needed:
- Add responsive breakpoints for wider layouts (side-by-side panels, wider cards)
- Remove hardcoded narrow-width assumptions in `style.css`
- Add window chrome controls if using Tauri (title bar, minimize/maximize/close)
- Add a "Connect to iTerm2" status indicator (connection may not be immediate)

### 1.6 Lifecycle Management

| Concern | Toolbelt (current) | Standalone |
|---------|-------------------|------------|
| **Start** | iTerm2 Scripts menu | `loom` CLI or app launcher |
| **Stop** | Kill process / Scripts menu | Quit app / `make stop` |
| **iTerm2 restart** | `iterm2.run_forever` reconnects | Same — `retry=True` reconnects |
| **Loom crash** | Manual restart from Scripts menu | Launchd/systemd auto-restart |
| **Port conflict** | `make stop` | Same |

For production standalone, register a `launchd` plist for auto-start:

```xml
<!-- ~/Library/LaunchAgents/com.loom.daemon.plist -->
<plist version="1.0">
<dict>
    <key>Label</key><string>com.loom.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/python3</string>
        <string>/path/to/loom.py</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>LOOM_STANDALONE</key><string>1</string>
    </dict>
    <key>KeepAlive</key><true/>
    <key>RunAtLoad</key><true/>
</dict>
</plist>
```

### Phase 1 Effort Estimate

| Task | Files Changed | Scope |
|------|--------------|-------|
| Entry point standalone mode | `loom.py` | ~20 lines |
| Conditional toolbelt registration | `server.py` | ~5 lines |
| Config flag + bind address | `config.py` | ~5 lines |
| PyWebView wrapper | New: `standalone.py` | ~30 lines |
| CSS responsive breakpoints | `static/style.css` | ~50 lines |
| Connection status indicator | `webview.html`, `ws.js` | ~20 lines |
| Makefile target (`make standalone`) | `Makefile` | ~10 lines |

**Total: ~140 lines of changes. No architectural rewrites needed.**

---

## Phase 2: Terminal Adapter Abstraction

**Goal:** Abstract the terminal backend so Loom can control any terminal emulator.

### 2.1 Adapter Interface

Currently, `bridge.py` (`ITerm2Bridge`) directly calls `iterm2.*` methods. Extract a `TerminalAdapter` protocol:

```python
# loom/terminal_adapter.py
from typing import Protocol, Optional, Callable, Awaitable

class TerminalAdapter(Protocol):
    """Abstract interface for terminal emulator control."""

    # Connection lifecycle
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    @property
    def connected(self) -> bool: ...

    # Session management
    async def create_session(self, command: str, profile: Optional[str] = None,
                            title: Optional[str] = None, cwd: Optional[str] = None,
                            window_id: Optional[str] = None) -> str:
        """Create a new terminal session. Returns session_id."""
        ...

    async def close_session(self, session_id: str) -> None: ...
    async def focus_session(self, session_id: str) -> None: ...
    async def send_text(self, session_id: str, text: str) -> None: ...
    async def get_session_variable(self, session_id: str, var: str) -> Optional[str]: ...

    # Window/tab management
    async def get_current_window_id(self) -> Optional[str]: ...
    async def get_window_sessions(self, window_id: str) -> list[str]: ...
    async def set_tab_title(self, session_id: str, title: str) -> None: ...
    async def set_tab_color(self, session_id: str, hex_color: str) -> None: ...
    async def clear_tab_color(self, session_id: str) -> None: ...
    async def reorder_tabs(self, window_id: str, session_ids: list[str]) -> None: ...

    # Profile queries
    async def list_profiles(self) -> list[str]: ...

    # Monitoring (callback-based)
    async def watch_session_terminated(self, callback: Callable[[str], Awaitable[None]]) -> None: ...
    async def watch_focus_changed(self, callback: Callable[[Optional[str], Optional[str]], Awaitable[None]]) -> None: ...
    async def watch_session_variable(self, session_id: str, var: str,
                                     callback: Callable[[str, str], Awaitable[None]]) -> None: ...
    async def watch_prompt(self, session_id: str,
                          callback: Callable[[str], Awaitable[None]]) -> None: ...

    # Global keybindings
    async def install_keybindings(self, bindings: dict) -> None: ...
    async def remove_keybindings(self) -> None: ...
```

### 2.2 iTerm2 Adapter

Wrap the existing `bridge.py` logic:

```python
# loom/adapters/iterm2_terminal.py
class ITerm2Adapter:
    """TerminalAdapter implementation using iTerm2 Python API."""

    def __init__(self, connection: iterm2.Connection):
        self.conn = connection
        # ... existing bridge.py state

    async def create_session(self, command, profile=None, title=None, cwd=None, window_id=None):
        # Existing bridge.py create_session logic
        ...

    async def watch_session_terminated(self, callback):
        # Existing SessionTerminationMonitor loop
        ...

    async def watch_focus_changed(self, callback):
        # Existing FocusMonitor loop
        ...

    # etc. — mostly moving existing code into the new interface
```

This is a **refactor, not a rewrite**. The existing `bridge.py` code moves into the adapter with minimal changes.

### 2.3 Integration with Server/State

```python
# server.py
async def main(connection):
    adapter = ITerm2Adapter(connection)  # or GhosttyAdapter()
    state = MatrixState(db, adapter)
    # ... rest of setup uses adapter instead of bridge directly
```

### Phase 2 Effort Estimate

| Task | Scope |
|------|-------|
| Define `TerminalAdapter` protocol | ~80 lines |
| Extract `ITerm2Adapter` from `bridge.py` | Refactor, ~400 lines moved |
| Extract keybinding management | Refactor, ~200 lines moved |
| Update `server.py` to use adapter | ~30 lines changed |
| Update `state.py` references | ~20 lines changed |

---

## Phase 3: Ghostty Backend

### 3.1 Ghostty API Landscape

Ghostty (v1.3+) exposes automation primarily through AppleScript on macOS:

| Capability | API | Loom Requirement |
|-----------|-----|-----------------|
| **Create window/tab/split** | AppleScript: `new window`, `new tab`, `split` | Session creation |
| **Send text** | AppleScript: `input text` | Agent commands, dispatch |
| **Focus terminal** | AppleScript: `focus`, `activate window` | Navigation |
| **Close session** | AppleScript: `close` | Agent removal |
| **Get working directory** | AppleScript: `working directory` property | Path tracking |
| **Get terminal UUID** | AppleScript: `id` property | Session identification |
| **Execute actions** | AppleScript: `perform action` | ~70 keybind actions |
| **Read terminal content** | Not in stable API (PR pending) | Screen reading |
| **Monitor events** | Not available | Focus, termination, prompt |
| **Set tab color** | Not available programmatically | Visual identification |
| **Global keybindings** | Config file only (`global:` prefix) | Keyboard shortcuts |
| **D-Bus (Linux)** | `+new-window` only | Very limited |

### 3.2 Ghostty Adapter — What Works

```python
# loom/adapters/ghostty_terminal.py
import subprocess
import asyncio

class GhosttyAdapter:
    """TerminalAdapter implementation using Ghostty AppleScript API."""

    async def create_session(self, command, profile=None, title=None, cwd=None, window_id=None):
        script = f'''
            tell application "Ghostty"
                set newTab to new tab
                set term to focused terminal of newTab
                input text "{command}\\n" to term
            end tell
        '''
        result = await self._run_applescript(script)
        # Extract terminal UUID from result
        return terminal_id

    async def send_text(self, session_id, text):
        script = f'''
            tell application "Ghostty"
                set term to terminal id "{session_id}"
                input text "{text}" to term
            end tell
        '''
        await self._run_applescript(script)

    async def focus_session(self, session_id):
        script = f'''
            tell application "Ghostty"
                focus (terminal id "{session_id}")
            end tell
        '''
        await self._run_applescript(script)

    async def close_session(self, session_id):
        script = f'''
            tell application "Ghostty"
                close (terminal id "{session_id}")
            end tell
        '''
        await self._run_applescript(script)

    async def get_session_variable(self, session_id, var):
        if var == "path":
            script = f'''
                tell application "Ghostty"
                    working directory of terminal id "{session_id}"
                end tell
            '''
            return await self._run_applescript(script)
        # jobName not directly available — requires polling
        return None

    async def _run_applescript(self, script):
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"AppleScript error: {stderr.decode()}")
        return stdout.decode().strip()
```

### 3.3 Ghostty Adapter — Gaps and Workarounds

#### Gap 1: No Event Monitors

**Problem:** Ghostty has no equivalent to iTerm2's `SessionTerminationMonitor`, `FocusMonitor`, `VariableMonitor`, or `PromptMonitor`. These are critical for Loom's real-time state tracking.

**Workaround: Polling loop**

```python
async def _poll_sessions(self):
    """Poll Ghostty for session state changes every 2 seconds."""
    while True:
        current_sessions = await self._list_all_terminals()
        current_ids = {t["id"] for t in current_sessions}

        # Detect terminated sessions
        for old_id in self._known_sessions - current_ids:
            await self._on_session_terminated(old_id)

        # Detect focus changes
        focused = await self._get_focused_terminal()
        if focused != self._last_focused:
            await self._on_focus_changed(focused)
            self._last_focused = focused

        # Update working directories
        for t in current_sessions:
            if t["cwd"] != self._known_cwds.get(t["id"]):
                await self._on_variable_changed(t["id"], "path", t["cwd"])
                self._known_cwds[t["id"]] = t["cwd"]

        self._known_sessions = current_ids
        await asyncio.sleep(2)
```

**Impact:** 2-second latency for state updates vs. iTerm2's instant callbacks. Acceptable for most workflows.

#### Gap 2: No Tab Colors

**Problem:** Ghostty doesn't expose programmatic tab color control.

**Workaround:** Skip tab coloring on Ghostty. Use tab title prefixes or terminal names for visual identification. Could also write to Ghostty's config and trigger `reload_config` action, but this is fragile.

#### Gap 3: No Tab Reordering API

**Problem:** No equivalent to iTerm2's `async_set_tabs()`.

**Workaround:** Not possible. Tab order is user-managed only.

#### Gap 4: No Global Keybinding Registration at Runtime

**Problem:** iTerm2 lets Loom install/remove global key bindings dynamically via API. Ghostty only supports keybindings via config file with `global:` prefix.

**Workaround:** Write keybindings to Ghostty config and trigger `reload_config`. Or use macOS system-wide keyboard shortcuts via Accessibility API / `CGEventTap`.

```python
async def install_keybindings(self, bindings):
    # Option A: Write to Ghostty config + reload
    config_path = Path.home() / ".config" / "ghostty" / "config"
    # Append keybind lines, then:
    await self._run_applescript('tell application "Ghostty" to perform action "reload_config"')

    # Option B: Use macOS CGEventTap for system-wide hotkeys
    # (requires Accessibility permission, more complex)
```

#### Gap 5: No Prompt Detection

**Problem:** No `PromptMonitor` equivalent. Shell integration (OSC 133) is internal to Ghostty.

**Workaround:** Use process monitoring. When the foreground process changes from the agent command back to the shell, infer a prompt appeared.

#### Gap 6: No Terminal Content Reading (Stable)

**Problem:** Cannot read terminal buffer. A PR exists (#11208) but isn't merged.

**Workaround:** Not critical for Loom's core function. Loom doesn't read terminal content — it tracks metadata (process, path, status).

#### Gap 7: Linux Support

**Problem:** D-Bus API only supports `+new-window`. No session listing, text input, or focus control.

**Workaround:** On Linux, Ghostty support would require either:
- Waiting for the official scripting API (#2353, no timeline)
- Using `xdotool`/`ydotool` for focus management + process monitoring
- Using the terminal API (escape sequences) for in-terminal control only

**Recommendation:** Ship Ghostty support as macOS-only initially.

### 3.4 Ghostty Feature Parity Matrix

| Loom Feature | iTerm2 | Ghostty | Gap Severity |
|-------------|--------|---------|-------------|
| Create sessions | Full API | AppleScript | None |
| Send commands | Full API | AppleScript | None |
| Close sessions | Full API | AppleScript | None |
| Focus sessions | Full API | AppleScript | None |
| Session termination detection | Instant (monitor) | Polling (~2s delay) | Low |
| Focus tracking | Instant (monitor) | Polling (~2s delay) | Low |
| Working directory tracking | Instant (monitor) | Polling (~2s delay) | Low |
| Process/job tracking | Instant (monitor) | Polling (ps-based) | Medium |
| Prompt detection | PromptMonitor | Process heuristic | Medium |
| Tab colors | Full API | Not available | Low (cosmetic) |
| Tab titles | Full API | AppleScript (terminal name) | Low |
| Tab reordering | Full API | Not available | Low |
| Profile listing | Full API | Not available | Low |
| Global keybindings | Dynamic API | Config file + reload | Medium |
| Broadcast mode | Direct text send | Direct text send | None |

**Overall:** ~80% feature parity achievable. The missing 20% is mostly latency (polling vs callbacks) and cosmetic (tab colors).

---

## Phase 4: Multi-Terminal Architecture

**Goal:** Support running with multiple terminal backends simultaneously.

### 4.1 Adapter Registry

```python
# loom/terminal_registry.py
class TerminalRegistry:
    """Manages multiple terminal adapters."""

    def __init__(self):
        self.adapters: dict[str, TerminalAdapter] = {}
        self.default: str = "iterm2"

    def register(self, name: str, adapter: TerminalAdapter):
        self.adapters[name] = adapter

    def get(self, name: str) -> TerminalAdapter:
        return self.adapters[name]

    async def connect_all(self):
        for name, adapter in self.adapters.items():
            try:
                await adapter.connect()
            except Exception as e:
                log.warning(f"Failed to connect to {name}: {e}")
```

### 4.2 Per-Agent Terminal Backend

Extend `AgentCell` to track which terminal backend owns it:

```python
@dataclass
class AgentCell:
    # ... existing fields
    terminal_backend: str = "iterm2"  # or "ghostty"
```

This allows a single Loom instance to manage agents across both iTerm2 and Ghostty simultaneously.

---

## Phase 5: Distribution and Packaging

### 5.1 macOS App Bundle

For the best user experience, package as a proper `.app`:

```
Loom.app/
  Contents/
    MacOS/
      loom-launcher      # Thin shell that starts Python daemon + webview
    Resources/
      python/            # Embedded Python + dependencies
      loom/              # Application code
      static/            # Web assets
      webview.html
    Info.plist
```

**Tools:** `py2app`, `PyInstaller`, or `briefcase` (from BeeWare).

### 5.2 Homebrew Cask

```ruby
cask "loom" do
  version "1.0.0"
  url "https://github.com/.../releases/download/v#{version}/Loom.app.zip"
  name "Loom"
  desc "AI agent session manager for terminal emulators"
  app "Loom.app"
end
```

### 5.3 Keep Toolbelt Mode

The toolbelt mode should remain supported. Detection:

```python
# loom/config.py
import os

def is_iterm2_script_env():
    """Detect if running inside iTerm2's script environment."""
    return "ITERM2_COOKIE" in os.environ and "ITERM_SESSION_ID" in os.environ

STANDALONE = not is_iterm2_script_env() or os.environ.get("LOOM_STANDALONE") == "1"
```

---

## Implementation Roadmap

### Sprint 1: Standalone iTerm2 (Phase 1)
- Add `LOOM_STANDALONE` flag and conditional toolbelt registration
- Add PyWebView launcher (`standalone.py`)
- Add responsive CSS for wider windows
- Add `make standalone` target
- Test: full functionality via standalone window controlling iTerm2

### Sprint 2: Adapter Abstraction (Phase 2)
- Define `TerminalAdapter` protocol
- Extract `ITerm2Adapter` from `bridge.py`
- Extract keybinding management into adapter
- Update `server.py` and `state.py` to use adapter interface
- Test: identical behavior through adapter layer

### Sprint 3: Ghostty Support (Phase 3)
- Implement `GhosttyAdapter` with AppleScript backend
- Implement polling loop for session/focus/path monitoring
- Handle graceful degradation (no tab colors, no reorder)
- Add terminal backend selector to global settings
- Test: create agents, dispatch tasks, navigate in Ghostty

### Sprint 4: Polish and Distribution (Phases 4-5)
- Multi-adapter registry for simultaneous backends
- macOS `.app` bundle packaging
- Launchd auto-start plist
- Documentation updates

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| iTerm2 external auth fails silently | Low | High | Clear error messages, setup wizard |
| Ghostty AppleScript API changes (pre-1.0 stability) | Medium | Medium | Pin minimum version, adapter versioning |
| Polling overhead for Ghostty monitoring | Low | Low | 2s interval is fine; tune if needed |
| PyWebView rendering differences vs WKWebView | Low | Low | Both use WebKit on macOS |
| Port conflicts with multiple Loom instances | Medium | Low | Already handled by `make stop` |
| Ghostty Linux support too limited | High | Medium | Ship as macOS-only, wait for scripting API |

---

## Decision Log

| Decision | Rationale |
|----------|-----------|
| Keep toolbelt mode alongside standalone | Many users prefer integrated toolbelt; no reason to remove it |
| PyWebView first, Tauri later | Zero new toolchain for initial standalone; upgrade path exists |
| Ghostty macOS-only initially | D-Bus API too limited for Linux; AppleScript covers macOS well |
| Polling for Ghostty monitoring | Only viable approach; 2s latency acceptable |
| Protocol-based adapter (not ABC) | Python Protocol allows structural subtyping; cleaner for optional methods |
| Per-agent terminal backend | Enables mixed workflows (some agents in iTerm2, others in Ghostty) |
