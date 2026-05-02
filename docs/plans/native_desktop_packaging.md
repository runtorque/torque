# Native Desktop Packaging Roadmap

## Executive Summary

Torque now has a working native desktop shell built with `pywebview`. That shell is intentionally thin: it opens the existing Torque web UI inside a native window and reuses the existing Python daemon, HTTP/WebSocket server, frontend assets, and SQLite state model.

That was the correct first step. It proved that Torque can run as a real native window without forking the product into a second frontend or a second backend.

The next step is different in kind: turn that native shell into a packaged desktop product that can be installed and operated without depending on the current iTerm-oriented runtime layout.

The recommended path is:

1. keep the current application stack
2. move desktop runtime ownership out of the iTerm-managed environment
3. package the desktop shell as a real app
4. harden terminal portability per platform
5. add release automation and distribution artifacts

This keeps the implementation pragmatic:

- no Electron-scale footprint
- no frontend rewrite
- no product fork
- no regression to the existing iTerm integration

---

## Current Baseline

After the native desktop-shell work, Torque currently has:

- a native desktop entrypoint (`torque_desktop.py`)
- a desktop launcher/service wrapper (`torque/desktop.py`)
- CLI entrypoints (`torque desktop`, `make desktop`, `make desktop-attach`)
- explicit spawn vs attach behavior
- desktop-specific defaults for profile, port, and data directory
- guardrails preventing accidental attachment to the iTerm-hosted Toolbelt daemon
- targeted regression coverage and native-window smoke verification

What it does **not** have yet:

- packaged app artifacts (`.app`, `.dmg`, AppImage, `.exe`, etc.)
- a desktop-owned runtime bootstrap flow
- a terminal backend story for Windows
- release CI and publishable desktop artifacts

That is the scope of this roadmap.

---

## Product Constraints

The desktop product must preserve these constraints:

1. **Do not break the iTerm product.**  
   iTerm-hosted Torque remains a first-class product surface. Desktop work must not regress Toolbelt behavior, install flows, or existing operator habits.

2. **Do not fork the core product.**  
   The desktop app should continue to use the same backend, state model, command handling, and frontend assets as the existing Torque product.

3. **Stay lightweight.**  
   Avoid introducing a heavy desktop stack unless the current approach proves insufficient under measured constraints.

4. **Be explicit about platform reality.**  
   `pywebview` is cross-platform, but a real Torque desktop product also depends on terminal/process integration. That portability work is separate and must be planned honestly.

---

## Recommended Stack

### Keep

- **Python** for the backend and launcher
- **`pywebview`** for the native shell
- **`aiohttp`** for the HTTP/WebSocket server
- the current **no-build HTML/CSS/JS frontend**
- **SQLite** for persistence

### Add

- **PyInstaller** as the first packaging path
- platform-specific release wrappers only where needed

### Defer

- Electron
- Tauri
- a frontend rewrite
- a backend rewrite
- Nuitka or other optimizer-driven packaging until startup size/perf is a measured issue

### Why this stack

This is the lowest-risk path to a packaged product:

- smallest architectural delta from what already works
- easiest to verify against current Torque behavior
- lowest chance of introducing desktop-only feature drift
- avoids a second application core

PyInstaller is the right first packaging choice because it works with the existing Python + `pywebview` structure and can produce installable artifacts without forcing a product rewrite.

---

## Target Architecture

### Shared product core

- `torque/server.py`
- `torque/state.py`
- existing adapters, worktree logic, and CLI surfaces
- existing frontend assets

### Desktop-specific wrapper

- desktop launcher / lifecycle
- packaged runtime bootstrap
- native window creation
- platform-specific packaging and release metadata

### Separation boundary

The desktop app and the iTerm app should be treated as two launch products over one shared core:

- **iTerm Torque**
  - Toolbelt-integrated
  - current install/deploy path
  - current runtime assumptions

- **Torque Desktop**
  - desktop-owned runtime
  - packaged native entrypoint
  - desktop-owned defaults and install flow

This is the right boundary. It keeps the product coherent without pretending the two launch surfaces are operationally identical.

---

## Phase 1 — Desktop-Owned Runtime Bootstrap

### Goal

Remove the desktop product's dependency on the iTerm-managed Python environment.

### Why this is first

Packaging before runtime ownership would produce a fragile desktop story. The app would still depend on a runtime layout that belongs to another product surface.

### Deliverables

- a desktop bootstrap/install path that does **not** require `make install`
- desktop dependency installation in an app-owned runtime
- explicit desktop runtime verification checks
- updated operator docs for desktop bootstrap vs iTerm install

### Likely implementation shape

- add a desktop bootstrap script, e.g.:
  - `scripts/bootstrap_desktop.py`
  - or `make desktop-bootstrap`
- provision an app-owned venv/runtime for desktop launch
- install desktop dependencies there:
  - `aiohttp`
  - `jinja2`
  - `pyyaml`
  - `pywebview`
  - any additional runtime dependencies actually required by desktop mode
- keep the iTerm-managed runtime path intact for the Toolbelt product

### Verification gates

- desktop launch works without relying on the iTerm-managed runtime
- `torque desktop` clearly reports which runtime it is using
- iTerm install/deploy flows remain unchanged

---

## Phase 2 — Package macOS as a Real App

### Goal

Produce a real macOS desktop artifact from the existing native shell.

### Deliverables

- packaged `.app`
- optional `.dmg` wrapper
- app icon and bundle metadata
- clean app launch behavior
- clean shutdown behavior in spawn and attach modes

### Recommended tooling

- PyInstaller for `.app` generation
- code signing and notarization later, once the packaging path is stable

### Verification gates

- app launches from Finder
- native window opens and closes cleanly
- spawn mode starts and stops a desktop-owned server correctly
- attach mode leaves the external standalone server alive
- logs and user data land in the intended desktop paths

---

## Phase 3 — Package Linux

### Goal

Ship a Linux desktop build without introducing desktop-only product divergence.

### Deliverables

- packaged Linux artifact
  - first a tarball / folder bundle
  - then AppImage and/or distro-specific package if worthwhile
- documented system dependency requirements for the webview backend
- Linux desktop smoke coverage

### Main risks

- system webview/backend availability
- packaging variance across distros
- terminal behavior differences under Linux shells and window managers

### Verification gates

- app launches on a clean Linux environment
- native window loads the existing Torque UI correctly
- terminal session creation works
- TUI smoke (including NeoVim) remains stable

---

## Phase 4 — Windows Terminal Portability

### Goal

Make Torque's desktop app truly viable on Windows.

### Why this is a separate phase

The native window is not the hard problem on Windows. The terminal/process model is.

### Deliverables

- a Windows terminal adapter based on **ConPTY**
- process lifecycle handling appropriate for Windows
- shell/profile bootstrap and environment normalization
- path handling and runtime path cleanup
- Windows-specific TUI validation

### Risks

- ConPTY semantics differ materially from POSIX PTYs
- shell bootstrap and env propagation are different
- signal/process shutdown expectations are different
- terminal correctness is the core risk surface, not the webview

### Verification gates

- desktop window launches on Windows
- shell session opens and can run normal CLI workflows
- NeoVim/TUI smoke passes
- relaunch/close behavior is deterministic

---

## Phase 5 — Release Automation and Distribution

### Goal

Make the desktop app releasable instead of only locally buildable.

### Deliverables

- CI jobs to build desktop artifacts per platform
- checksums and release metadata
- release notes / operator guidance
- smoke automation for core launch flows

### CI matrix

- macOS build
- Linux build
- Windows build

### Minimum smoke coverage

- app launch
- server startup
- window open/close
- spawn mode
- attach mode
- terminal open
- TUI smoke
- verification that iTerm-hosted Torque remains unaffected

---

## Phase 6 — Desktop Product Polish

These are appropriate only after the desktop product is packaged and stable.

### Candidates

- single-instance behavior
- “open new window” / “reopen existing window” semantics
- native menus and shortcuts
- file/folder open integration
- tray/menu-bar presence where justified
- crash log export
- auto-update channel

These are product improvements, not prerequisites for first shipment.

---

## Non-Goals for the Next Wave

The following should **not** be folded into the first packaging push:

- replacing `pywebview`
- rewriting the frontend
- replacing the Torque daemon architecture
- changing persistence or state architecture
- redesigning the desktop UI again
- adding platform-specific feature creep unrelated to packaging/runtime ownership

That work would expand scope without improving the shortest path to a real packaged app.

---

## Recommended Implementation Order

1. **Desktop runtime bootstrap**
2. **macOS packaging**
3. **Linux packaging**
4. **Windows terminal backend**
5. **release CI**
6. **desktop polish**

This order keeps risk front-loaded where it belongs:

- first runtime ownership
- then packaging
- then terminal portability

---

## Exit Criteria

This roadmap is complete when Torque Desktop can be described accurately as:

> a packaged native desktop app, using the same Torque core as the iTerm product, installable and operable without a browser and without dependence on the iTerm-managed runtime, with clear platform support boundaries and release artifacts for supported targets.

At that point, the iTerm integration remains intact, but the desktop product is no longer merely “the browser/standalone UI in a native shell.” It becomes an actual distributable application.
