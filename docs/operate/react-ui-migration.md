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

The browser suite shares daemon state, so run it serially. Finish `make test` and any UI builds before starting browser QA: the backend packaging checks rebuild `ui/dist`, which can briefly make the shared QA daemon return a missing-build 503. This PTY-disabled harness validates UI and command workflows but does not certify actual terminal I/O or native Tauri windows. The expanded suite includes deterministic transport/host fixtures and live Board/Planning/pipeline/endpoint checks. Native release testing still follows the Tauri section above.

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

### Initiative and Decision lifecycle regression

`planning-editors-live.spec.ts` creates its own groups and generic Architect/Engineer records on an isolated daemon. It validates every offered status through backend reads, typed relationships, sparse scope/rationale saves, failure retention, archive and restore. It can run in the PTY-disabled profile harness; no commercial provider is used. Generic agents are removed afterward, while Planning evidence remains in the disposable profile. A separate scenario reviews an unsaved Initiative task prefill, cancels without mutation, injects create/link failures, resumes the acknowledged task link, and verifies task fields and unchanged Initiative scope through backend reads. It does not certify reload/crash recovery or existing-task save/preview failure handling. `task-create-live.spec.ts` separately covers external references, named variables, pre-creation prompt rendering and explicit cancellation; project actions live in a disposable directory, with provider sync disabled.

### Settings reconnect and validation regression

`settings-reconnect-live.spec.ts` and `settings-validation-live.spec.ts` run against the disposable PTY-disabled profile after `make ui-check`. The reconnect scenario changes server settings while local edits and a section reset are pending, closes the real WebSocket, and checks untouched values, nested map additions/removals, input identity, focus and caret. It injects a refresh failure, retries in place, saves and verifies the merged values through backend reads; hidden Settings must issue no refresh reads. The validation scenario checks numeric boundaries and retry after a partial save. These checks do not certify provider runtime effects or recovery after a process crash.

### Shared Context acknowledgement and reconnect regression

`context-live.spec.ts` uses the disposable PTY-disabled profile. It changes an entry through a second client, reconnects the real WebSocket, and checks that untouched fields refresh while local content, focus, caret and unapplied search remain intact. It injects list, edit, publish and pin failures; verifies sparse persisted edits; and checks that retrying a failed list after successful publication creates no duplicate. Hidden Context must issue no reads. `context-links-live.spec.ts` separately checks optional task/pipeline/agent links, a disappearing draft target, rejection/retry, real reconnect, preserved links after sparse edits, saved-target navigation across groups, compact pipeline-root resolution and Cancel. History reconnect and the Context split ratio remain separate acceptance rows.

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


`task-create-live.spec.ts` uses its own disposable groups and a cross-group dependency. It uploads an image and report, removes a staged file, adds an external path reference and an inline artifact, injects one rejected create, then reads the persisted task and canonical attachment URLs. A cancelled draft must return 404 for its former upload; successful creation must preserve the external reference and remove the draft URL. The scenario reopens verification to inspect the persisted state. It requires an isolated daemon and does not dispatch a provider.

### Existing-task edit and prompt-preview regression

`task-edit-live.spec.ts` requires the isolated profiling harness (`TORQUE_PROFILE_ENABLED=1`), with PTY spawning disabled. It creates/removes only its own synthetic worker and temporary project actions/roles. It checks actual active-dispatch rejection, preserved drafts, role/context preview without persistence, delayed save acknowledgement, sparse updates and schedule preservation in an America/Sao_Paulo browser. A second scenario proves a rejected edit retains the file, a failed cleanup retains a retryable dialog, and retry removes the file without repeating the save. These tests do not dispatch a provider or test native windows.

`task-evidence-live.spec.ts` exercises structured artifact edit/cancel/rejected save/retry/reopen, nested text and image previews, upload classification and cancellation cleanup, and task activity hydration/paging/live updates while retaining drafts. It seeds activity through the disposable daemon's ordinary task-update command; it does not run agents or provider services. Artifact preview file fetches are additionally covered by component tests, including error and abort handling.

`settings-validation-live.spec.ts` checks blank, negative, fractional, out-of-range and unsafe integer drafts before transport; hidden-field focus; literal environment-map values; numeric persistence including zero; and partial global/group/AI save recovery. It injects a group refusal after actual global persistence, changes the completed global scope externally, and verifies retry preserves that change. Run only on a disposable profile: the test changes global runtime and AI cadence values, with AI and PTYs kept disabled.

History acceptance: in an isolated temporary profile, run `history-live.spec.ts` to seed persisted agent history, reconnect the real WebSocket, retain search/caret/pane scroll and message expansion, inject independent list/detail failures, retry, and follow task/message links to hydrated Board editors. The fixture writes only to a non-default daemon with a temporary data directory. Review wide and compact screenshots; this does not certify native lifecycle or archived-task discovery.

Context pane acceptance: `context-split-live.spec.ts` uses an isolated profile to verify one write after pointer drag, keyboard bounds, saved width after reload, failure/retry, external updates, real reconnect, and compact/wide transitions with an unsaved editor. Inspect wide/compact screenshots and verify both pane scrolling and draft/caret retention.


Agent Class acceptance: `agent-classes-live.spec.ts` runs on the isolated PTY-disabled profile and creates its own temporary project catalog. It checks structured authority, raw deny-rule and prompt/metadata preservation, reconnect with an unsaved draft/caret, rejected reads and writes, current-draft validation, duplicate staging, delayed creation acknowledgement, and archive/delete retry. Inspect the authority screenshot. The test removes its temporary project afterward; no provider dispatch or native lifecycle is exercised.


Catalog acceptance: `catalog-live.spec.ts` exercises Roles, compatibility Templates and Specializations separately in temporary project directories on the disposable daemon. It checks full-definition and YAML preservation, typed prompt/priority/terminal fields, reconnect with an unsaved caret selection, failed list/detail reads, refused and delayed saves, duplicate staging, rename and confirmed deletion retry. All live writes stay in project scope; temporary-file backend tests separately verify project/user shadowing and scope moves. Native lifecycle, provider effects and crash recovery remain separate gates.

`actions-live.spec.ts` uses a temporary project directory on a non-default disposable daemon. It verifies typed action fields and retained full metadata through persisted YAML, unsaved variable preview, real reconnect draft/caret/scroll continuity, failed/delayed writes, duplicate/rename/delete and post-save read retry. Wide and compact layouts are captured. User/project scope moves and cross-scope collision preservation use isolated backend fixtures; live acceptance never writes the operator's global action library.


Help acceptance: `help-live.spec.ts` reads the actual maintained documentation on the isolated PTY-disabled daemon. It verifies empty-search reset, audience requests, section/answer-source navigation, example and hash metadata, real WebSocket reconnect with unsubmitted inputs/caret/scroll/disclosures retained, refused detail retry, and hidden-panel inactivity. Inspect `help-wide.png` and `help-compact.png`. No maintained document is edited by the test; data freshness/cancellation/mismatched responses and Markdown safety have separate component coverage. Native external-link and lifecycle acceptance remain separate gates.
