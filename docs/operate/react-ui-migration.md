# React UI cutover, burn-in, and rollback

React is the default browser and Tauri renderer. It uses the same daemon
protocol and SQLite profile as the classic fallback, so switching renderers
does not copy, rewrite, or downgrade workspace data. During burn-in, the stable
routes are:

| Surface | Route |
| --- | --- |
| React default | `/` |
| React compatibility alias | `/ui-next/` |
| Classic fallback | `/legacy/` |

## Run in a browser

Build the committed client and run an isolated profile from a non-worker shell:

```bash
make ui-check
make standalone-bg TORQUE_PORT=18948 TORQUE_PROFILE=react-preview
```

Open `http://127.0.0.1:18948/`. Keep the isolated profile while testing creation
and mutation flows. Use `http://127.0.0.1:18948/legacy/` or
`make open-legacy TORQUE_PORT=18948` to compare the same state in the classic UI.

For live frontend development, point Vite at that daemon in a second shell:

```bash
TORQUE_PORT=18948 make ui-dev
```

Vite listens on `http://127.0.0.1:5173/` and proxies HTTP and WebSocket traffic
to the selected daemon.

## Run in Tauri

Run the React renderer and Tauri host together with an isolated profile:

```bash
make ui-tauri-dev TORQUE_PORT=18948 TORQUE_PROFILE=react-preview
```

This exercises native menus, custom confirmation dialogs, allow-listed external
links, detached windows, window-bounds persistence, log access, first-run
onboarding, and managed-daemon shutdown. Production Tauri bundles load the
daemon root and therefore use React by default.

The welcome flow writes `~/.torque/.first_run_complete`. It records only that
onboarding was completed; it does not alter a profile. **Help → Show Welcome**
opens it again.

## Roll back

Rollback during burn-in is immediate and renderer-only. For one browser window:

1. Leave the React window open if its diagnostics are useful.
2. Open `/legacy/` on the same daemon.
3. Continue working against the same profile and database.

To make the classic renderer the root for one profile process, stop that
isolated daemon normally and relaunch it with the explicit environment override:

```bash
TORQUE_UI_DEFAULT=legacy make standalone TORQUE_PORT=18948 TORQUE_PROFILE=react-preview
# Tauri development shell against the same profile-level rollback:
TORQUE_UI_DEFAULT=legacy make tauri-dev TORQUE_PORT=18948 TORQUE_PROFILE=react-preview
```

Unset `TORQUE_UI_DEFAULT` or set it to `react` on the next launch to restore the
cutover default. The override is process-local configuration; it is not written
to SQLite or shared with another profile.

Do not restore or downgrade `torque.db` merely to change renderers. If a React
interaction exposes a protocol defect, stop mutating through the preview, save
the active profile log and browser console output, and reproduce through
`/legacy/`. Detached-panel and window-bounds preferences are safe to leave in
place; either renderer ignores keys it does not own.

## Troubleshooting

- A blank `/` or `/ui-next/` usually means the production client was not built. Run
  `make ui-build` and reload.
- A disconnected banner means the UI cannot reach the daemon. Confirm the port,
  profile, and `TORQUE_UI_DAEMON_ORIGIN` used by Vite.
- Native controls are intentionally unavailable in a normal browser. Test them
  with `make ui-tauri-dev`.
- Production Content Security Policy permits only the local daemon origin,
  local WebSocket traffic, and bundled assets. External pages open through the
  native host and only absolute `http` or `https` URLs are accepted.
- Logs live at `~/.torque/profiles/<profile>/torque.log`; the desktop Help menu
  can reveal that directory.
- Protocol compatibility and unhandled React errors are redacted, bounded, and
  reported to the durable Inbox during burn-in. Preserve the notice and profile
  log when filing a regression.

## Parity repair status and isolated QA

The [behavior and field ledger](../plans/react-ui-parity-matrix.md) is the release authority for parity. Logs, aggregate read-only Chat and Pipelines now have dedicated Control Center sections. Organization, lane visibility, operational detail and typed settings have focused coverage; the remaining acceptance rows still block classic retirement.

For source-only QA without Make's install prerequisite, use an unused port and a new disposable data directory from a non-worker shell:

```bash
TORQUE_DATA_DIR=/tmp/torque-react-qa TORQUE_PROFILE=react-qa TORQUE_PORT=18958 TORQUE_PROFILE_ENABLED=1 TORQUE_PROFILE_SKIP_PTY=1 python3 torque.py
# In a second shell after make ui-check:
TORQUE_UI_BASE_URL=http://127.0.0.1:18958 TORQUE_PLAYWRIGHT_CHANNEL=chrome npm --prefix ui run test:e2e -- --workers=1
```

The browser suite shares daemon state, so run it serially. This PTY-disabled harness validates UI and command workflows but does not certify actual terminal I/O or native Tauri windows. The expanded suite includes deterministic transport/host fixtures and live Board/Planning/pipeline/endpoint checks. Native release testing still follows the Tauri section above.

### Real attention delivery regression

Use a disposable profile with PTY spawning enabled and a non-default port. Do not set `TORQUE_PROFILE_SKIP_PTY` for this run. The browser and daemon must run on the same machine, because the test creates a temporary Python input receiver and inspects its input log. No model or provider service is invoked.

```bash
TORQUE_STANDALONE=1 TORQUE_DATA_DIR=/tmp/torque-attention-qa TORQUE_PROFILE=attention-qa TORQUE_PORT=18961 python3 torque.py
# In a second shell after make ui-check:
TORQUE_UI_BASE_URL=http://127.0.0.1:18961 TORQUE_PLAYWRIGHT_CHANNEL=chrome TORQUE_ATTENTION_PYTHON="$(command -v python3)" npm --prefix ui run test:e2e -- attention-live.spec.ts
```

The test refuses the default runtime/profile. It creates its own group, generic agent, temporary receiver directory, parent and ask; it removes the agent/session and temporary files afterward. Proposal records remain confined to newly created QA groups. Without `TORQUE_ATTENTION_PYTHON`, the PTY case skips; the live proposal test still runs. The receiver executable must be an absolute Python path available to the daemon. Shut down only the identity-verified QA daemon and its own supervisor after inspection.

The checks cover an actual two-client HTTP race and PTY input, a transport failure before delivery, replay, compact context updates, real WebSocket reconnects, and real SQLite-backed behavior proposal decisions. They do not assert commercial provider comprehension or exactly-once delivery across a process crash.

### Real terminal viewport regression

Use the same disposable PTY-enabled daemon recipe above. Set `TORQUE_PTY_PYTHON` to an absolute Python executable on the daemon host:

```bash
TORQUE_UI_BASE_URL=http://127.0.0.1:18961 TORQUE_PLAYWRIGHT_CHANNEL=chrome TORQUE_PTY_PYTHON="$(command -v python3)" npm --prefix ui run test:e2e -- terminal-live.spec.ts
```

This opt-in test refuses the default port/profile, launches `ui/e2e/fixtures/streaming_receiver.py` as a generic agent and inspects the actual xterm buffer and terminal WebSocket. It checks scrolling during output, Tail, resizing and reconnect without replacing the terminal. Set both `TORQUE_PTY_PYTHON` and `TORQUE_ATTENTION_PYTHON` for the complete real-PTY browser suite. Native ownership still requires the separate detach/type/resize/close check; browser success does not certify native crash recovery.

### Thinking contract and lifecycle regression

`thinking-live.spec.ts` runs against the same isolated daemon without a provider. It creates a dedicated group and leaves its Planning evidence in the disposable profile. It covers failed creation retention, every persisted brief text field, scratchpad links, refinement, product-only proposal, park/return to draft, reload and archived read-only discovery, plus scratchpad editing and confirmed deletion. Run it after `make ui-check`; a PTY opt-in is unnecessary for this file.

## Classic burn-in and retirement

The Torque maintainers own the fallback. `/legacy/` and classic writes remain
available for at least 30 days and two production releases after the React
cutover. Retirement requires both conditions plus no unresolved P0/P1 React
regression, no required parity gap, and a successful rollback drill on the
release candidate. Removing classic writes is the first retirement change;
deleting `webview.html`, `static/`, and VM-only tests is a later reviewed change.

## Release gate

Before a desktop release, CI runs the locked UI checks and build, daemon route
and protocol tests, Tauri permission lint, fail-closed community-package audit,
and Rust desktop tests. Both macOS architectures are then built. Each app
signature, DMG, zip archive, and per-architecture checksum file is verified
before GitHub release publication.

On a macOS development host, `make tauri-build-mac` performs the local production
gate. It defaults to ad-hoc signing when `APPLE_SIGNING_IDENTITY` is unset, then
strictly verifies the generated app signature and DMG checksum. A dry run remains
the required safe release rehearsal; see [Releasing Torque](releasing.md).
