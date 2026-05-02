# Standalone xterm.js Execution Plan

## Goal

Make Torque run in both of these modes without forking the product:

1. iTerm2-native mode
2. full standalone mode with local terminals rendered through xterm.js

The same Torque daemon, SQLite state model, board, worktree flow, agent hooks,
and MCP/reporting behavior must work across both modes.

## Non-Goals

- Replacing iTerm2 as the primary terminal runtime
- Rewriting the frontend into a bundled framework
- Introducing a second product core separate from the Python daemon
- Dropping existing toolbelt behavior

## Current Constraints

- The frontend is already browser-safe and multi-client capable.
- The daemon is still tightly coupled to iTerm2 for session metadata,
  profile enumeration, toolbelt registration, and keybindings.
- `torque/terminal_adapter.py` exists, but it does not yet cover everything
  `server.py`, `server_agent.py`, and `bridge.py` need.
- Standalone browser mode today is UI-only; terminals still live in iTerm2.

## Recommendation

Use a two-backend architecture:

- Keep `ITerm2Adapter` for iTerm2-managed sessions.
- Add `LocalPtyAdapter` for standalone-managed sessions.
- Render `LocalPtyAdapter` sessions through xterm.js in the frontend.
- Package the standalone desktop app with `pywebview` first.
- Preserve a clean upgrade path to Tauri later.

This keeps one product core and one persistence model while allowing both
terminal backends to coexist.

## Alternatives

### Option A: Browser/Desktop shell around the current iTerm2 backend

Description:
- Keep iTerm2 as the only session backend.
- Open the existing web UI in a browser, pywebview, or Tauri shell.

Pros:
- Lowest effort
- No terminal-streaming work
- Dual-mode already mostly works

Cons:
- Not a true standalone Torque
- Linux app story is weak
- xterm.js adds no value because iTerm2 is still the terminal runtime

Decision:
- Useful as a compatibility mode, not the end-state.

### Option B: Python daemon + Local PTY backend + xterm.js + pywebview

Description:
- Extend the existing daemon with a local PTY-backed terminal adapter.
- Add terminal streaming websocket endpoints.
- Use xterm.js for standalone sessions in both browser and desktop shells.
- Ship a desktop window through pywebview.

Pros:
- Reuses the existing Torque backend
- Works on macOS and Linux
- Keeps the no-build-step frontend viable by vendoring xterm assets
- Simplest path to a true standalone app

Cons:
- PTY lifecycle, resize, and process tracking work is non-trivial
- pywebview is functional but less polished than Tauri

Decision:
- Recommended initial implementation.

### Option C: Python daemon + Local PTY backend + xterm.js + Tauri

Description:
- Same backend architecture as Option B.
- Replace pywebview shell with Tauri for packaging and native window features.

Pros:
- Better desktop polish
- Better tray/window management
- Strong long-term packaging story

Cons:
- Adds Rust/tooling complexity immediately
- Sidecar process management must be designed up front

Decision:
- Strong follow-up after the standalone runtime is proven.

### Option D: Electron + node-pty + xterm.js

Description:
- Move standalone terminal runtime into an Electron shell with `node-pty`.

Pros:
- Proven xterm.js stack
- Strong PTY ergonomics
- Mature packaging

Cons:
- Heavier app
- Splits Torque across Python and Node runtimes
- Makes “same core supports iTerm2 and standalone” harder to maintain

Decision:
- Not recommended unless the Python PTY path becomes unworkable.

## Target Architecture

```text
                         +----------------------+
                         |  SQLite / MatrixState|
                         +----------+-----------+
                                    |
                           +--------v--------+
                           |   Torque Daemon   |
                           | aiohttp + MCP   |
                           +--------+--------+
                                    |
            +-----------------------+------------------------+
            |                                                |
   +--------v--------+                              +--------v--------+
   | ITerm2Adapter   |                              | LocalPtyAdapter |
   | native tabs     |                              | local PTYs      |
   +--------+--------+                              +--------+--------+
            |                                                |
      +-----v-----+                                  +-------v--------+
      |  iTerm2   |                                  | terminal ws    |
      | toolbelt  |                                  | xterm.js UI    |
      +-----------+                                  +----------------+
```

## Required Design Decisions

### 1. Runtime Separation

Separate these concerns explicitly:

- UI host
  - toolbelt webview
  - browser
  - pywebview desktop window
  - Tauri desktop window
- terminal backend
  - iTerm2
  - local PTY

These are independent axes. A browser window can observe iTerm2-backed agents.
A desktop shell can also host xterm.js sessions from the local PTY backend.

### 2. Backend Selection Model

Add backend selection at two levels:

- global default backend
- per-group / per-agent backend override

Proposed values:

- `iterm2`
- `pty`

The persisted backend belongs on `AgentCell`. Group defaults should seed it.

### 3. Capability Gating

Not every backend supports every feature.

Example capability differences:

- iTerm2 supports:
  - profile enumeration
  - tab color
  - tab reordering
  - global keybindings
  - toolbelt registration
- local PTY supports:
  - terminal streaming
  - resize
  - desktop-native embedding

UI and server code must branch by declared adapter capabilities, not backend name.

## Implementation Phases

### Phase 0: Contract Completion

Objective:
- Make the terminal adapter real enough that `server.py` no longer reaches
  around it for routine session metadata.

Work:
- Expand `torque/terminal_adapter.py`
- Add launch-context and capability dataclasses
- Move profile listing, current-session inspection, and input-ready priming
  behind the adapter
- Remove private `bridge._input_ready_sessions` usage from `server.py`
- Remove direct iTerm2 metadata reads from `server.py` and `server_agent.py`

Deliverables:
- richer adapter interface
- updated `ITerm2Adapter`
- tests for the new interface

### Phase 1: Backend Registry

Objective:
- Let Torque instantiate terminal backends without hardcoding iTerm2 in
  `server.py`.

Work:
- Add a small adapter factory / registry
- Introduce backend capability reporting
- Make iTerm2-specific imports lazy
- Keep keybindings/toolbelt registration conditional on adapter capability

Deliverables:
- runtime backend selection plumbing
- server import path that can load without immediately requiring iTerm2

### Phase 2: Local PTY Runtime

Objective:
- Support true standalone sessions managed entirely by Torque.

Work:
- Implement `LocalPtyAdapter`
- Spawn shells and agent commands under a PTY
- Support:
  - create session
  - write input
  - stream output
  - resize
  - close session
  - exit detection
- Seed session metadata from launch parameters
- Track process status, cwd, and git info with best-effort polling

Implementation notes:
- Start with Unix-only support for macOS/Linux.
- Prefer Python-native PTY support for v1.
- Accept that `cwd` and foreground process detection may begin as polling-based.

Deliverables:
- local PTY adapter
- server-side terminal session manager
- PTY lifecycle tests

### Phase 3: Terminal Streaming API

Objective:
- Expose standalone PTY sessions to the frontend.

Work:
- Add websocket endpoint per terminal session, for example:
  - `/ws/terminal/{session_id}`
- Define a message protocol:
  - `data`
  - `resize`
  - `input`
  - `exit`
  - `title`
  - `error`
- Support reconnect behavior
- Decide buffering policy for late subscribers

Deliverables:
- terminal websocket transport
- protocol docs/tests

### Phase 4: xterm.js Frontend Integration

Objective:
- Render standalone PTY sessions inside Torque.

Work:
- Vendor xterm.js and required addons into `static/vendor`
- Add terminal mount points to the existing UI
- Support:
  - attach/detach
  - input
  - resize
  - reconnect
  - theme sync
- Keep iTerm2-backed cells behaving as they do today
- Add UI affordances showing which backend owns a session

Proposed UI behavior:
- iTerm2-backed agents:
  - existing click-to-focus-tab behavior remains
- PTY-backed agents:
  - click reveals or focuses embedded xterm.js panel

Deliverables:
- xterm.js renderer
- standalone terminal panel / drawer
- frontend regression coverage

### Phase 5: Desktop Shell

Objective:
- Ship a real app for macOS and Linux.

Preferred order:

1. pywebview
2. Tauri

#### Phase 5A: pywebview

Work:
- Add standalone launcher script
- Open the Torque UI in a native webview window
- Start or connect to the daemon on a non-default app port
- Ensure the shell can find agent CLIs in packaged environments

Deliverables:
- native desktop window
- local development launcher

#### Phase 5B: Tauri

Work:
- Add Tauri wrapper as an optional shell
- Manage the Python daemon as a sidecar
- Add tray, window-state, and packaging polish

Deliverables:
- production-grade native desktop shell

### Phase 6: Distribution

Objective:
- Package standalone Torque for users.

macOS:
- `.app` bundle first
- notarization later

Linux:
- AppImage or distro package first
- Flatpak later if needed

Keep iTerm2 install path intact:
- current script install remains supported

## Data Model Changes

### AgentCell

Add:

- `terminal_backend: str = "iterm2"`

Potential future additions:

- `terminal_capabilities: dict` as derived runtime metadata, not persisted
- `terminal_stream_attached: bool` as ephemeral UI/runtime state

### GroupSettings

Add:

- `default_terminal_backend: str = "iterm2"`
- possibly separate defaults for agent backend vs child terminal backend

### Migration

Rules:

- existing rows default to `iterm2`
- no behavior change for current users

## Backend Capability Matrix

| Capability | iTerm2 | Local PTY |
|---|---:|---:|
| Create sessions | Yes | Yes |
| Send input | Yes | Yes |
| Close sessions | Yes | Yes |
| Focus native session | Yes | Partial |
| Embedded xterm.js | No | Yes |
| Tab color | Yes | No |
| Tab reorder | Yes | N/A |
| Profile enumeration | Yes | No |
| Global hotkeys | Yes | App-level later |
| Toolbelt registration | Yes | No |

## API Changes

### New backend-aware config payload

Add to config responses:

- available terminal backends
- backend capabilities
- current runtime backend

### New terminal websocket

Server endpoints:

- `GET /ws/terminal/{session_id}`

Command payloads:

- `{"type":"input","data":"..."}`
- `{"type":"resize","cols":120,"rows":40}`

Server events:

- `{"type":"data","data":"..."}`
- `{"type":"exit","exit_code":0}`
- `{"type":"title","title":"..." }`
- `{"type":"error","message":"..." }`

## Testing Plan

### Keep existing baseline green

- `make test`

### New backend tests

- adapter contract tests
- launch-context tests
- standalone PTY lifecycle tests
- terminal websocket tests
- xterm attach/detach smoke tests

### New desktop smoke

Use a non-default port, for example `18933`, for standalone smoke runs.

Smoke checklist:

1. start daemon on alternate port
2. open standalone shell
3. create PTY-backed agent
4. send input
5. verify streamed output appears
6. resize terminal
7. stop session
8. reconnect UI and verify state recovery

## Rollout Plan

### Milestone 1

- contract completion
- backend registry
- no visible user change

### Milestone 2

- local PTY backend behind a feature flag
- browser-only xterm prototype

### Milestone 3

- pywebview desktop shell
- explicit backend selector in UI

### Milestone 4

- desktop packaging
- docs and operational guidance

## Risks

### PATH and CLI discovery in packaged apps

Impact:
- high

Mitigation:
- explicit environment bootstrap
- settings UI for command paths
- packaged-shell diagnostics view

### PTY metadata fidelity

Impact:
- medium

Mitigation:
- start with best-effort polling
- keep git/path resolution independent from the terminal backend where possible

### Feature parity pressure

Impact:
- medium

Mitigation:
- use capability gating rather than forcing parity for every feature

### Desktop shell churn

Impact:
- medium

Mitigation:
- keep the Python daemon and xterm.js protocol stable
- treat pywebview/Tauri as replaceable shells

## Execution Status

- `Done`: architecture investigation, alternatives analysis, baseline test run
- `In Progress`: adapter contract completion and server cleanup
- `Pending`: local PTY backend, terminal websocket, xterm.js UI, desktop shell
