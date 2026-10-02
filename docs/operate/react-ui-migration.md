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

The [behavior and field ledger](../plans/react-ui-parity-matrix.md) is the authority for parity. Phase 4 feature parity was accepted on 2026-10-02: 412 required/equivalent behaviors accepted, four explicit retirements, and a complete 219/219 browser pass. Logs, aggregate read-only Chat and Pipelines have dedicated Control Center sections; organization, lane visibility, operational detail and typed settings have granular acceptance evidence. The ledger records native and external-fixture testing boundaries. Classic retirement still requires the separate burn-in and retirement gates below.

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


Prompt-editor acceptance: `prompt-editor-live.spec.ts` runs against the disposable PTY-disabled daemon and a temporary project action. It checks native insertion/undo, typed syntax colors, wide/compact wrap and scroll geometry, pointer resize, reconnect/caret retention, forced-colors fallback, rejected saves, unmodified save payloads and actual backend preview. The backend's existing trailing-newline normalization is checked separately from editing fidelity. Inspect `prompt-wide.png` and `prompt-compact.png`; this test does not certify native IME/device behavior. Empty action collections and serializer-quoted multiline/Unicode text have daemon/CLI round-trip regressions in `test_action_authoring.py` and `test_action_yaml_collections.py`.

### Engineer identity regression

`engineer-rename-live.spec.ts` uses two generic Engineers in a disposable profile. It checks actual duplicate rejection, blank validation, failed-draft reconnect, pending acknowledgement/dismissal, history naming, partial-save retry and an external identity change. `TORQUE_PROFILE_SKIP_PTY=1` skips the supervisor; explicit generic Engineer creation still opens local sessions. It removes its Engineers afterward. Run after `make ui-check`; no commercial provider or native Tauri window is exercised.

### Stale completed-task archive regression

`stale-archive-live.spec.ts` advances only the browser clock to compare six-day and eight-day eligibility. It creates real tasks in two disposable groups, verifies filtered scope and hidden/Archive behavior, injects one batch refusal, then archives through the actual daemon command. Keyboard submission, unrelated draft/caret/selection/scroll, reload/reconnect and persisted task lanes are checked. Test teardown removes its task records. No daemon clock, production data, provider or native window is changed.

### Per-agent settings refresh and recovery regression

`ui/e2e/agent-settings-live.spec.ts` uses a disposable generic Engineer in an isolated profile to verify changed defaults, reconnect, retained reset/draft/caret, dirty dismissal, failed-read retry and launch-success/digest-failure recovery. The test verifies persisted values after external updates and reload, checks compact discard layout and removes its own Engineer. Run against the isolated browser QA daemon using the same Playwright environment as the other live regressions; never target the default profile.

### Agent creation regression

`ui/e2e/agent-create-live.spec.ts` creates disposable generic agents and terminals in an isolated profile. It verifies real role/group resolution, reconnect draft preservation, refused reads/writes, pending close guards and replay of a cached creation result after simulated acknowledgement loss. Its second case checks Architect/Engineer/attached-terminal creation and reload. Temporary project files and created agents are cleaned up; run only against an isolated non-default profile.


The Agent creation browser suite also creates two temporary projects with the same Agent Class ID and different identities. It checks scoped discovery, failed-read retry with retained caret, archive-on-reconnect refusal, a matching real launch from the second project and persisted identity after reload. Use a disposable profile; the test removes its generated agents and project files.


GitHub settings acceptance: `github-settings-live.spec.ts` uses a disposable daemon with `ui/e2e/fixtures/github-settings` prepended to that daemon's `PATH` and `TORQUE_GITHUB_SETTINGS_FIXTURE=1`. Set the same flag for the browser test, plus its isolated `TORQUE_UI_BASE_URL`. The fixture executable accepts only version/auth/repository/project reads and returns deterministic metadata; it never accesses GitHub. The case verifies unsaved project discovery, selection and Status/lane suggestions, repository detection, refusal/retry, explicit save and reload through the production UI and real daemon. Board sync stays disabled. This is integration evidence with a simulated external provider, not live GitHub credentials/network acceptance. Never prepend the fixture path to a live profile.


Specialization acceptance: `specialization-picker-live.spec.ts` creates a temporary project with three project-scoped definitions and a generic `/bin/cat` Engineer in the isolated profile. It verifies inherited creation defaults, catalog selection, keyboard ordering/primary markers, creation persistence, reconnect draft/focus retention, rejected specialization save/retry, and ordered group defaults through save/reload. It removes the Engineer and temporary project afterward. Component tests cover explicit empty overrides, stale/failed catalog reads, manual trailing-newline/caret preservation, and unknown selected slugs. Commercial-provider and full native acceptance remain separate.


Notification preset acceptance: `notification-presets-live.spec.ts` uses an isolated profile and a disposable generic `/bin/cat` Engineer. It checks group preset matching, custom-event drafts across a real reconnect, refused save/retry and reload; creation with an explicitly empty enabled-event override; and per-agent preset refusal/retry followed by acknowledged relaunch. It removes the Engineer afterward. Run after `make ui-check`, alongside `agent-settings-live.spec.ts`, `agent-create-live.spec.ts` and `settings-reconnect-live.spec.ts`. Bundle values are compared directly to the checked-in Classic constant in the Node frontend tests. This scenario checks persisted and resolved configuration, not actual digest/heartbeat timer delivery or commercial-provider behavior.


Worktree diff acceptance: `worktree-diff-live.spec.ts` creates a temporary Git repository, a generic worker and a real Torque worktree under that repository. It checkpoints changes to a large and a small file, then verifies automatic disclosure and keyboard expansion, progressive 400-line mounting, retained line budgets across History/Changes and explicit refresh, path identity, scroll position and bulk collapse/expand. It permanently removes only its own disposable worker and deletes its temporary repository afterward. Run against a non-default isolated daemon after `make ui-check`. It does not create a PR, merge changes, or certify worktree mutation/reconnect lifecycle behavior.


The worktree diff scenario also exercises real WebSocket reconnect while Changes/preflight reads are refused. It verifies retained line budgets, file DOM, scroll, merge draft/focus/caret, a disabled merge action until fresh preflight, then successful retry against a newer real checkpoint. Closing the inspector and reconnecting must issue no worktree reads. Component tests separately verify malformed/mismatched responses, aborts on scope/hide/unmount, and the 30-second read timeout. This does not certify acknowledgement or retry semantics for worktree mutations.

### MCP capture and event retention acceptance

`settings-ingest-live.spec.ts` uses an isolated daemon to save/reload each capture choice, post synthetic MCP hook events and query persisted call records. It checks both result formats, Metadata allowlist glob/regex behavior, Off precedence, unchanged older capture decisions, invalid-draft no-write/focus behavior and actual row-cap trimming. Its settings are restored on exit; the test necessarily deletes older events when exercising the row cap and must never target a production profile. The zero-day age-expiry contract also has a controlled-time regression in `tests/test_event_ingest.py`.

### Settings search acceptance

`settings-search-live.spec.ts` searches real settings across global/group, Engineer/Architect defaults, AI, appearance and shortcuts. It checks closed-section reveal, keyboard focus/viewport, retained directory and secret drafts, caret selection, no writes from search or Enter, updated map-field results after a real WebSocket reconnect, and wide/narrow screenshots. The fixture uses an isolated group and synthetic values; it never submits its secret draft.

### Remaining numeric settings boundaries

`settings-bounds-live.spec.ts` validates cadence, Context TTL and perceived-empty detector bounds. It closes each control's disclosure before invalid saves, checks reveal/focus and absence of writes, then saves and reloads both endpoints through real global/group commands. The Python settings-contract suite also verifies every endpoint after SQLite reload. Personal transcript replay tests may skip when their files are absent; do not count those skips as detector runtime verification.


### Creation delivery recovery acceptance

`ui/e2e/agent-creation-recovery-live.spec.ts` requires an isolated daemon and a production build from `make ui-check`. It holds the first response after creating a generic terminal, waits for the actual 30-second deadline, verifies command-navigator protection and reconnect retention, and recovers the same ID through explicit receipt replay. Its second scenario enters a malformed startup command that fails after terminal allocation, verifies the exact incomplete target, and deliberately inspects it. It removes its targets afterward. Run alongside `agent-create-live.spec.ts`, `agent-creation-reads-live.spec.ts` and `creation-roles-live.spec.ts`; inspect the compact screenshots. These tests do not certify forced page reload, daemon crash, native handoff or commercial-provider delivery.


The GitHub settings suite also verifies deadline recovery using a browser transport that deliberately ignores cancellation. It retains real fixture project choices and an unsaved repository draft through the 30-second deadline, retries, delivers the expired result, and checks that only explicit Save persists settings. Start the isolated daemon with the existing `ui/e2e/fixtures/github-settings` directory prepended to PATH and `TORQUE_GITHUB_SETTINGS_FIXTURE=1`; pass that flag to Playwright as well. This fixture never calls external GitHub.


`message-loop-live.spec.ts` now includes a real 30-second cancellation deadline. It uses only generic local targets, holds the response after actual cancellation, changes selection, reconnects, and verifies explicit replay returns the same cancellation record without changing the unrelated composer draft. It also retains the replacement-loop guard scenario. Run against an isolated PTY-enabled daemon after `make ui-check`; inspect the compact timeout screenshot. Created targets are removed, and no commercial-provider loop delivery is exercised.


### Composer delivery recovery acceptance

`ui/e2e/composer-recovery-live.spec.ts` requires an isolated PTY-enabled daemon, the production UI build and an absolute `TORQUE_PTY_PYTHON` receiver executable. It holds actual daemon acknowledgements past the real 30-second deadlines, switches cells, reconnects, and explicitly retries the exact send/cancellation commands. It verifies one receiver marker, identical replayed receipts, rejection of expired responses during retry, retained unrelated drafts, correlated no-input refusal and the compact review dialog. Inspect `composer-unknown-compact.png` and `composer-review-compact.png`. Run alongside composer-live, composer-attachments-live, composer-keyboard-live, composer-completion-live and message-loop-live. All created agents and temporary receiver files are removed by the tests; verify zero sessions and stop only the identified QA profile's daemon and sidecars. These tests use generic local providers and do not certify commercial-provider interruption, upload deadlines, forced-page-reload recovery or the daemon-crash window before receipt persistence.


### Direct-state broadcast recovery acceptance

The full browser run at `6c7b183b` finished 142/147 passing. Revalidate the Planning archive-recovery case with `planning-recovery-live.spec.ts`, then the related Planning editors/lazy/Thinking cases. The backend subscriber tests in `test_planning_broadcast.py` and `test_direct_mutation_broadcast.py` deliberately run without periodic broadcasts; they exercise real SQLite/state through the direct handler and, for an external initiative update, the HTTP route. Agent Class assignment/clear, Engineer specializations and successful durable Relay credential persistence must publish before acknowledgement. Credential persistence failure must leave settings untouched and publish no optimistic update. Relay generation is stubbed in the backend regression; no external service call is required.

Run `board-read-recovery-live.spec.ts` before `board.spec.ts` to retain the cross-test state that exposed ambiguous Board smoke locators. Also rerun `context-links-live.spec.ts` and `task-edit-live.spec.ts`; retain draft/focus/caret and attachment-file assertions while observing the current HTTP detail transport and full preservation error. Use only an isolated profile. Freeze source/builds during browser runs and record the actual complete-run result separately from these targeted repairs.


### Composer upload recovery acceptance

`composer-upload-recovery-live.spec.ts` has separate request and response-body cases with real 30-second deadlines. Use an isolated production-build daemon and absolute `TORQUE_PTY_PYTHON` receiver executable. The tests retain a draft offscreen through reconnect, explicitly attach again, release expired replies while the new upload is pending, and verify no message was sent until explicit Send. The real receiver must see one line with only the accepted image path. Inspect `upload-request-timeout.png` / `upload-body-timeout.png` at the compact viewport. Run alongside the existing composer attachments, completion, keyboard and ordinary delivery scenarios. The multipart upload endpoint has no idempotent receipt; this is draft/observation recovery, not proof an expired upload saved no file. Verify zero sessions and stop only the identified QA profile after acceptance.


### Raw terminal image-drop recovery acceptance

`terminal-drop-recovery-live.spec.ts` uses a real local Python PTY receiver, actual image uploads, and controlled HTTP refusal and delayed request/body acknowledgements. Set `TORQUE_PTY_PYTHON` to an absolute executable on an isolated production-build daemon. Verify failed batches send no input; partial successes retain order; a real 30-second deadline releases recovery feedback; expired receipts do not settle a fresh drop; and leaving/reopening a pane suppresses late paste and focus. Inspect the compact `terminal-drop-request-timeout.png` and `terminal-drop-body-timeout.png` screenshots. The receiver must see only accepted paths after explicit Enter. Tests remove their terminals and temporary receivers; verify zero sessions and stop only the exact QA daemon and sidecars. No external provider or upload-idempotency claim is involved.


### Workspace navigation save recovery acceptance

Run `workspace-navigation-recovery-live.spec.ts` alongside `workspace-navigation-live.spec.ts` against an isolated production-build daemon. The request case holds the old request before it reaches the daemon, waits the real 30-second deadline, commits a newer navigation, and then delivers the old request to prove persistent stale-write rejection. The body case holds an actual acknowledgement, explicitly retries the same revision, queues newer navigation, and releases the expired body during retry. Both reconnect without replay, retain local navigation and reload the final saved destination. Inspect `workspace-request-timeout.png` and `workspace-body-timeout.png` at compact width. Backend tests in `test_react_workspace_state.py` additionally cover actual database reopen, schema-30 upgrade, transactional failure/retry, cross-window exact replay and cancellation-safe subscriber publication. Do not infer a general daemon-crash delivery guarantee from these preference-specific tests.


### Ownership-tree navigation retention acceptance

`parity.spec.ts` now extends the hierarchy/draft fixture across Board and Control navigation and keyboard expansion after return, and adds group-scoped Expand all acceptance. Use an isolated production-build daemon; these hierarchy scenarios supply protocol fixtures and do not launch provider sessions. Inspect `retained-ownership-collapse.png` at compact width. Run the complete parity fixture file so corrected workspace-save acknowledgement metadata is also exercised in attention review. The actual daemon-backed Area/Thinking scenarios remain in that file and should pass unchanged.


### Native window restoration QA

From a graphical desktop session, run `cargo run --offline --manifest-path src-tauri/Cargo.toml --example window_restore_qa` for an isolated native geometry check. It creates and destroys blank windows with a separate application identifier; it does not start or connect to a Torque daemon. It checks main and detached sizing policies, physical/legacy captures, offscreen and oversized recovery, then serialized bounds after window recreation. The command prints requested and observed geometry and exits nonzero on failure. Pure multi-monitor/negative-origin/mixed-scale cases run in the normal Rust suite. This smoke test does not replace physical cross-monitor, sleep/crash, or other-platform acceptance.


### Group runtime policy and Board default regressions

After the production UI build, run against a disposable PTY-enabled daemon with
TORQUE_PROFILE_ENABLED=1. The runtime-policy test requires an explicit local
Python executable and the opt-in below:

```bash
TORQUE_UI_BASE_URL=http://127.0.0.1:19085 TORQUE_PLAYWRIGHT_CHANNEL=chrome TORQUE_RUNTIME_POLICY_QA=1 TORQUE_PTY_PYTHON=/absolute/path/to/python npm --prefix ui run test:e2e -- settings-runtime-policy-live.spec.ts settings-board-defaults-live.spec.ts
```

The policy file saves and reloads the group settings, checks both behavior
approval routes, waits for the real one-minute idle threshold, and exercises
session resume through the actual provider launch wrapper. Its temporary local
executable only records resume/session arguments; it never invokes a provider
service or records generated configuration. Allow about two minutes for the
health-loop case. All agents and temporary recorder files are removed afterward.

The Board-default file checks actual task lane, label and action inheritance,
explicit choices through reconnect/refusal, and fallback after clearing settings.
It uses a temporary project action and removes its tasks/files afterward. Neither
file modifies the default daemon; review and stop only the identity-verified QA
runtime and its own sidecars. Proposal/group records remain in that disposable
profile. These tests do not certify external provider conversation restoration,
commercial inference or native window recovery.


### Group organization and shell confirmation regression

Run `group-lifecycle-live.spec.ts` against an isolated non-default profile with real local PTYs enabled. It creates, renames, orders and removes only its own groups; the populated removal uses a generic `/bin/cat` Worker and child terminal and verifies both sessions close. It checks keyboard-accessible placement, pointer drop, reload, Cancel/Escape and focus restoration. The companion scenario intercepts daemon restart/stop/supervisor commands at the browser transport and never forwards them. No live daemon lifecycle operation or hosted provider call is required.


### External-status note regression

Run `external-status-note-live.spec.ts` against the isolated QA profile. It creates a disposable task linked to a local-only provider identity with Board sync disabled. Browser transport intercepts every external command and supplies local refused/accepted responses, checking note/status trimming, empty note, retained caret/drafts and reconnect without any hosted provider writes. `tests.test_external_tickets` separately verifies the shared adapter receives the note.
