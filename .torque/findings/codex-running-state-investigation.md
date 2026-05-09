# Codex running-state investigation

Date: 2026-05-09  
Agent/task: `codex-running-state-investigation` / TORQUE:401  
Scope: research + plan only; no code changes were made.

## Executive summary

The live repro confirms the symptom for a real Codex worker: the worker visibly ran a shell command in the Codex TUI, but Torque state stayed `activity: ""`, the durable event-ingest ring stayed empty, and the frontend received only `agent_upsert` deltas with `status: "running"` and empty `activity`.

The dropped signal is upstream of `/events`: Torque installs Codex hooks, but current Codex (observed `v0.130.0`) refuses to run those project-local hooks until they are reviewed. The TUI printed `3 hooks need review before they can run`, and no hook POST reached Torque while the worker was actively tool-calling.

Downstream layers are not the primary drop point. A synthetic Codex `PreToolUse` POST to `/events` for the same cell was accepted, drained, parsed by `CodexAdapter`, mapped by `EventBus` to `activity: "tool_call"`, emitted as an agent update, and would render green. The frontend gray dot is therefore a faithful rendering of missing activity, with one caveat: for awareness agents (`agent_type` set), `static/js/render.js::agentStatusClass` intentionally treats `activity` as source of truth and ignores `status: "running"` once an event/progress clock exists.

Recommended follow-up: a bounded `oneshot/fix` or `feature/implement` should make Torque-managed Codex hooks trusted/auto-runnable under current Codex and update the stale Codex hook feature flag from the deprecated `codex_hooks` key to the current `hooks` key. Engineer approval is enough if the change stays inside the Codex adapter/config/tests and the live smoke below passes.

## Live repro evidence

### Setup

Isolated standalone daemon:

```sh
TORQUE_PORT=18934 \
TORQUE_PROFILE=codex-running-state-18934 \
TORQUE_DATA_DIR=/tmp/torque-codex-running-state-18934 \
make standalone
```

Then:

```sh
./bin/torque --port 18934 group add Repro
./bin/torque --port 18934 task dispatch \
  -g Repro \
  -n codex-repro \
  -c 'codex --ask-for-approval never --sandbox danger-full-access' \
  -d /tmp/torque-codex-worker-repro \
  'Repro task: immediately use a shell/tool call to run `python3 -c "import time; print("start_sleep", flush=True); time.sleep(20); print("end_sleep", flush=True)"`, then briefly report what happened. Do not modify files.'
```

Resulting cell:

- agent id: `238ebe55`
- agent type: `codex`
- session id: `ba5df9bab226470986cb4ecace273ca9`
- command: `codex --ask-for-approval never --sandbox danger-full-access`
- isolated data dir: `/tmp/torque-codex-running-state-18934`

The isolated daemon and its sidecars were stopped after the investigation. The temporary worker directory was removed. I left `/tmp/torque-codex-running-state-18934` in place as local evidence while this report is attached.

### Codex-side evidence

The spawned Codex TUI showed the relevant warning before any tool call:

```text
⚠ 3 hooks need review before they can run. Open /hooks to review them.
⚠ `[features].codex_hooks` is deprecated. Use `[features].hooks` instead.
```

The same terminal later showed active work:

```text
• Working ...
• Waiting for background terminal ...
  └ python3 -c "import time; print(\"start_sleep\", flush=True); time.sleep(20); print(\"end_sleep\", flush=True)"
• The command ran successfully. It printed start_sleep, waited about 20 seconds, then printed end_sleep and exited with code 0.
```

### Torque state while Codex was working

While the terminal was in the `Working` / background-terminal phase, repeated state polls stayed empty for activity and the ingest DB stayed empty:

```text
13:13:32 status=running activity='' detail='' last_event_text='' last_event_at=1778343083.804 last_progress_at=1778343083.804 needs=False
13:13:32 ingest_events=0
...
13:14:27 status=running activity='' detail='' last_event_text='' last_event_at=1778343083.804 last_progress_at=1778343083.804 needs=False
13:14:27 ingest_events=0
```

No real hook events were stored:

```sh
sqlite3 /tmp/torque-codex-running-state-18934/event_ingest.db \
  'select count(*) from events;'
# => 0
```

Relevant daemon log lines around the run:

```text
13:11:13 INFO Installed hooks for 'codex-repro' (type=codex) in /tmp/torque-codex-worker-repro
13:11:13 INFO Installed MCP config for 'codex-repro' in /tmp/torque-codex-worker-repro
13:11:24 INFO MCP initialize id=0 cell=238ebe55
13:11:24 INFO MCP notification: notifications/initialized
13:11:24 INFO MCP tools/list id=1 cell=238ebe55
```

There were no `Event: cell='codex-repro' ...` lines during the real Codex tool call.

### WS evidence

A `/ws` listener during a follow-up Codex send saw only `agent_upsert` updates with empty activity and no hook-derived event/MCP deltas:

```json
{
  "op": "agent_upsert",
  "id": "238ebe55",
  "agent_type": "codex",
  "status": "running",
  "activity": "",
  "activity_detail": "",
  "last_event_text": "",
  "last_event_at": 1778343317.849895,
  "last_progress_at": 1778343317.849895,
  "needs_attention": false
}
```

A second update had the same shape at `last_event_at=1778343325.917403`; event-ingest count remained `0`.

### Synthetic downstream control

To verify the downstream path was healthy, I posted a synthetic Codex `PreToolUse` event for the same cell:

```sh
curl -X POST http://127.0.0.1:18934/events \
  -H 'Content-Type: application/json' \
  -H 'X-Torque-Cell-Id: 238ebe55' \
  --data-binary @/tmp/synthetic_pretool.json
# HTTP/1.1 200 OK
```

After the drainer ran, state changed as expected:

```python
{
  'status': 'running',
  'activity': 'tool_call',
  'activity_detail': 'Using Bash',
  'last_event_text': 'Using Bash',
  'last_event_at': 1778343365.86717,
  'last_progress_at': 1778343365.86717,
  'last_heartbeat_at': 1778343365.86717,
  'needs_attention': False,
  'agent_type': 'codex'
}
```

Event-ingest row:

```text
1|238ebe55|PreToolUse|Bash|synthetic-pretool-1
```

Daemon log:

```text
13:16:05 INFO Event: cell='codex-repro' type=tool_start activity='tool_call' detail='Using Bash'
```

This proves `/events` ingest, Codex adapter parsing, EventBus activity mapping, state emission, and the WS/frontend-visible state shape work when a Codex hook payload actually arrives.

Note: the synthetic `tool_input.command` was redacted to metadata by `event_ingest_db.redact_event_for_mcp_call_log` before adapter parsing, so the detail became `Using Bash` instead of `Running: ...`. That is a separate detail-quality issue, not the gray-dot root cause because `activity` still became non-empty.

## Per-layer trace

### 1. Codex hook installation

Relevant files:

- `torque/adapters/codex.py`

Current behavior:

- `CodexAdapter.get_hook_config()` writes command hooks for `SessionStart`, `PreToolUse`, and `Stop` into `.codex/hooks.json`.
- `CodexAdapter.install_mcp_config()` writes the Torque MCP server and calls `_ensure_codex_hooks_enabled()`.
- `_ensure_codex_hooks_enabled()` writes:

```toml
# -- Torque Codex hooks feature (managed by Torque, do not edit) --
[features]
codex_hooks = true
```

Live generated files in the repro had the correct port/cell-env-aware curl command (`http://localhost:18934/events` plus `X-Torque-Cell-Id: $TORQUE_CELL_ID`).

Problem observed at this layer:

- Codex discovered the hooks but refused to run them pending hook review.
- Codex also warned that `codex_hooks` is deprecated in favor of `hooks`.

### 2. Hook execution → `/events` POST

Expected:

- Codex runs the command hook on `PreToolUse`.
- The command hook reads JSON from stdin, adds deterministic `event_id`, and POSTs to `/events`.

Observed:

- During real Codex tool calls, `event_ingest.db.events` stayed at `0` rows.
- There were no `/events`-driven `Event:` log lines.
- Therefore no real hook command executed successfully.

Dropped-signal layer: **Codex hook execution/trust/review**, before Torque receives the event.

### 3. `/events` ingest

Relevant files:

- `torque/server.py::handle_events`
- `torque/events.py::build_event_ingest_envelope`
- `torque/event_ingest_client.py`
- `torque/event_ingest_daemon.py`
- `torque/event_ingest_db.py`

Observed:

- No real events reached this layer during Codex activity.
- Synthetic control event returned 200, persisted in `event_ingest.db.events`, drained, and was acknowledged.

Conclusion: ingest is not the root drop point.

### 4. Adapter activity inference

Relevant files:

- `torque/adapters/codex.py::parse_event`
- `torque/adapters/base.py::AgentEvent`

Current behavior:

- `SessionStart` → normalized `session_start`
- `Stop` → normalized `session_end`
- `PreToolUse` → normalized `tool_start`
- `PostToolUse` → normalized `tool_end` (though `PostToolUse` is not installed by default for Codex)

Synthetic control proved `PreToolUse` maps to `tool_start`. The adapter is not the root drop point.

### 5. MatrixState / EventBus activity fields

Relevant files:

- `torque/events.py::EventBus._apply`
- `torque/state.py::AgentCell`

Current behavior:

- `tool_start` sets:
  - `cell.activity = "tool_call"`
  - `cell.activity_detail = detail`
  - clears attention/error fields
  - emits `agent_upsert`
- `session_end` clears activity and sets status idle.
- `send_text` itself marks progress and emits `status: "running"`, but does not set `activity`.

Synthetic control proved EventBus sets activity correctly. Without hook events, `activity` remains empty forever even while Codex is visibly working.

### 6. WS delta

Relevant files:

- `torque/state.py::_emit_agent`
- `torque/state.py::broadcast`
- `static/js/ws.js`

Observed during real Codex activity:

- WS emitted `agent_upsert` with `status: "running"` and empty `activity` from `send_text` / progress clocks.
- No hook-driven `agent_upsert` with `activity: "tool_call"` arrived.

Conclusion: WS is carrying the state it is given; it is not dropping a populated activity field.

### 7. Frontend dot rendering

Relevant files:

- `static/js/render.js::agentStatusClass`
- `static/style.css::.cell-status.*`

Current mapping:

```js
function agentStatusClass(a) {
  if (a.needs_attention) return 'attention';
  if (a.status === 'stopped') return 'disconnected';
  if (a.agent_type) {
    if (a.activity) return 'working';
    if (a.last_event_at > 0) return 'idle';
  }
  if (a.status === 'running') return 'working';
  return 'idle';
}
```

So for Codex in the repro:

- `agent_type: "codex"`
- `activity: ""`
- `last_event_at > 0` from `send_text` / progress marking
- `status: "running"`

`agentStatusClass` returns `idle`; `.cell-status.idle` is the gray blinking dot.

Conclusion: frontend rendering is consistent with the state. It is not dropping a valid activity signal. It does, however, amplify the upstream missing-hook issue by ignoring `status: "running"` for awareness agents once any event/progress clock exists.

## Root cause

Torque's Codex worker activity depends on Codex command hooks. Current Torque correctly writes hook files, but current Codex requires those project-local hooks to be reviewed before execution. In the live repro, Codex explicitly warned that the three Torque-managed hooks needed review, then ran tool calls without executing any hook. Because no hook POST reached `/events`, `EventBus` never received `tool_start`, `activity` stayed empty, WS never sent an activity-bearing delta, and the frontend mapped the awareness agent to idle/gray.

The stale `codex_hooks` feature key is also real: Codex warned it is deprecated in favor of `hooks`. It may not be the direct drop point in this run (Codex discovered the hooks), but it should be fixed with the trust issue.

## Implementation plan

Approval path: **Engineer approval is enough** if implementation stays within Codex adapter/config/tests and does not add a new user-visible security policy. Human approval is required if the chosen trust strategy installs global user-level hooks for all Codex sessions or changes product behavior outside Torque-managed workers.

### Step 1 — Add failing regression coverage for current Codex hook config

Files:

- `tests/test_agent_template_adapters.py`

Add/adjust tests around `CodexAdapter`:

1. `install_mcp_config()` writes `[features] hooks = true` for current Codex, not the deprecated managed `codex_hooks = true` block.
2. `uninstall_mcp_config()` removes both the new managed `hooks = true` block and any older Torque-managed `codex_hooks = true` block.
3. `install_hooks()` writes Torque-managed hook entries with whatever current Codex requires to treat them as already trusted/reviewed (see Step 2).
4. Existing user hook entries remain preserved; stale Torque hook entries with old ports are still removed.

### Step 2 — Make Torque-managed Codex hooks auto-runnable under current Codex

Files:

- `torque/adapters/codex.py`

Preferred fix shape:

1. Update the Codex hook entry generator (`get_hook_config()` / `_cmd_hook()`) to include Codex's current trusted-hook metadata for Torque-managed command hooks.
   - Current Codex binary schema strings include `trusted_hash` on `HookHandlerConfig`; verify the exact contract during implementation.
   - Compute the trust value from the exact command payload Codex will execute, after the port-specific curl command is finalized.
   - Attach it to each generated command hook entry in `.codex/hooks.json` so Codex does not block Torque-owned hooks behind the `/hooks` review UI.
2. Keep the hook command itself gated by `TORQUE_CELL_ID` and pointed at the current `TORQUE_PORT`, as it is today.
3. Continue identifying stale Torque hooks by the localhost `/events` URL so port changes cleanly replace old managed entries.
4. If current Codex has no stable project-local `trusted_hash` contract, use the fallback design below instead and mark it human-approval-required if it broadens hook scope.

Fallback design if `trusted_hash` is unavailable or unsuitable:

- Move Torque telemetry hooks to a Codex config layer that Codex already treats as trusted, such as a Torque-managed user/global hook with a guard that exits immediately unless `TORQUE_CELL_ID` is set.
- This may affect all user Codex launches at config-discovery time, so it should get human approval before implementation.

### Step 3 — Update Codex feature flag management

Files:

- `torque/adapters/codex.py`
- `tests/test_agent_template_adapters.py`

Replace the managed `codex_hooks` block helpers with current `hooks` helpers:

- `_FEATURE_LINE_RE` should target `hooks = ...` for the managed current key.
- Cleanup should also recognize and remove the old Torque-managed `codex_hooks = true` block/section.
- `install_mcp_config()` should leave user-owned feature settings alone and only replace Torque-managed blocks.
- `uninstall_mcp_config()` should remove Torque-managed `hooks` and old `codex_hooks` blocks without deleting unrelated `[features]` content.

### Step 4 — Consider a small UI resilience patch only after hooks are fixed

Files, if needed:

- `static/js/render.js`
- `tests/frontend_state_regression.test.js` or a nearby frontend state test

Do **not** use this as the primary fix. If hooks still have startup gaps, consider a bounded fallback in `agentStatusClass` such as "for awareness agents with `status === 'running'` and a very recent `last_progress_at` from a Torque send, render `working` until the first `session_end`/idle signal." This has a trade-off: without `Stop` hooks it can make Codex stay green forever, so it should be avoided unless tests define a safe timeout.

### Step 5 — Live verification recipe

Run on an isolated port that is neither 18932 nor 18933:

```sh
PORT=18934
DATA_DIR=/tmp/torque-codex-running-state-$PORT
rm -rf "$DATA_DIR" /tmp/torque-codex-worker-repro
mkdir -p "$DATA_DIR" /tmp/torque-codex-worker-repro
TORQUE_PORT=$PORT TORQUE_PROFILE=codex-running-state-$PORT TORQUE_DATA_DIR=$DATA_DIR make standalone
```

In another shell:

```sh
./bin/torque --port $PORT group add Repro
./bin/torque --port $PORT task dispatch \
  -g Repro \
  -n codex-repro \
  -c 'codex --ask-for-approval never --sandbox danger-full-access' \
  -d /tmp/torque-codex-worker-repro \
  'Use a shell/tool call to run `python3 -c "import time; print("start", flush=True); time.sleep(20); print("done", flush=True)"`, then report completion.'
```

Acceptance checks:

1. Codex TUI no longer shows `hooks need review before they can run` for Torque-managed hooks.
2. `event_ingest.db.events` increments during `SessionStart` and/or `PreToolUse`.
3. During the sleep command:
   - state has `activity: "tool_call"` (or another non-empty working activity),
   - WS has an `agent_upsert` with non-empty `activity`,
   - UI dot class is `cell-status working` / green.
4. After `Stop`, state returns to idle/empty activity.
5. Tear down the isolated daemon and any sidecars.

### Step 6 — Test commands before shipping

Targeted first:

```sh
python3 -m unittest tests.test_agent_template_adapters -v
python3 -m unittest tests.test_events -v
node --test tests/frontend_state_regression.test.js
```

Then full suite:

```sh
make test
```

Do not run `make deploy` from a worker worktree.

## Suggested follow-up task

Title: `Fix Codex worker running-state hooks`

Action: `oneshot/fix` (or `feature/implement` if oneshot is unavailable)

Context:

- Finding: `.torque/findings/codex-running-state-investigation.md`
- Root cause: Codex v0.130.0 discovers Torque's project-local hooks but blocks them pending `/hooks` review, so no `PreToolUse`/`Stop` events reach `/events`; `activity` remains empty and the frontend correctly renders the Codex awareness agent idle/gray.
- Fix scope: update `torque/adapters/codex.py` to use current `[features].hooks` and make Torque-managed Codex command hooks trusted/auto-runnable (prefer `trusted_hash` if supported), plus adapter tests and isolated live smoke.
- Keep frontend fallback out of scope unless hook trust cannot be made reliable.
