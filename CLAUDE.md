# CLAUDE.md

## Project overview

Torque is a local agent-orchestration workspace: a long-running Python daemon, an iTerm2/PTY terminal adapter, a no-build-step HTML/CSS/JS frontend, and SQLite as the persistent source of truth. The product center of gravity is `torque/server.py` plus `torque/state.py`; most other modules hang off those.

Primary operator surfaces are standalone/browser and desktop app modes. The iTerm2 Toolbelt is a deprecated secondary integration; the Makefile no longer installs or updates the old Scripts copy, and old data should be migrated with `scripts/migrate_toolbelt_to_profile.py`.

## Key commands

```bash
make deploy          # Primary install/update: ~/.torque/app + CLI refresh
make run             # Launch the desktop app (standalone daemon, desktop profile)
make standalone      # Foreground browser-only daemon (then make open)
make stop            # Free TORQUE_PORT (18932 unless overridden)
make check           # Python path, dependency, install status
make open            # Open standalone/browser UI
make test            # Full regression suite
```

After `make deploy`, relaunch with `make run` or `make standalone` + `make open`. Migrate old Toolbelt data with `scripts/migrate_toolbelt_to_profile.py` (TORQUE:645 P1b).

## Never deploy/stop mid-session

If you are a Torque worker or engineer running inside the live daemon, do **not** run `make deploy`, `make stop`, or `make restart` against the daemon that spawned you. Killing that daemon corrupts in-memory dispatch state (PTY subscriptions, pending-prompt queues, session cache) and can make subsequent workers boot DOA.

The Makefile refuses `stop` / `deploy` / `restart` when `TORQUE_CELL_ID` is set or when pwd is under `.torque/worktrees/`; HTTP lifecycle commands reject worker-context requests unless `force=true`. Override (`FORCE=1` / `force=true`) only with a specific reason and explicit acceptance of the corruption risk.

Safe alternatives:

- Commit your change and ask the user to deploy/relaunch from their own shell.
- Test on a different port/profile, e.g. `make standalone-bg TORQUE_PORT=18933 TORQUE_PROFILE=desktop`.
- Ship code/logging and let it take effect on the next natural restart.

## Architecture map

See [docs/reference/architecture.md](docs/reference/architecture.md) for the detailed file-by-file reference. High-level map:

- `torque.py`: installed entrypoint; anchors runtime paths and starts the standalone daemon loop.
- `torque/server.py`: aiohttp routes, command dispatch, agent launch/reuse, action rendering, worker/engineer/architect integration glue.
- `torque/server_agent.py`, `server_dispatch.py`, `server_worktrees.py`, `server_artifacts.py`, `server_actions.py`: extracted helpers for server-heavy concerns.
- `torque/state.py`: core dataclasses (`AgentCell`, `BoardTask`, settings) and `MatrixState` mutation/delta logic.
- `torque/db*.py`: SQLite schema, persistence, board, and shared-memory helpers.
- `torque/actions.py`: YAML action discovery plus Jinja2 `prompt` rendering; only `prompt` renders, `torque` is reserved, and `transitions` define valid derives.
- `torque/templates.py`, `roles.py`, `specializations.py`: agent template/config discovery and role/specialization resolution.
- `torque/worktree.py`, `worktree_boundaries.py`: git worktree lifecycle, checkpointing, merge/boundary safety.
- `torque/mcp*.py`, `mcp_engineer_tools/`: worker, engineer, architect MCP tool surfaces and scoping.
- `torque/engineer.py`, `architect.py`: persistent role prompts, journals/digests/decisions, orchestration behavior.
- `torque/adapters/`: provider integrations (`claude-code`, `codex`, `gemini-cli`, generic).
- `webview.html` + `static/js/*` + `static/style.css`: plain frontend; script load order is architectural.
- `bin/torque`: CLI; writes go through HTTP, many reads go directly to SQLite.

## Persistence and state

- SQLite (`torque.db`) is the persistent source of truth and uses WAL mode for daemon writes plus CLI reads.
- Web clients receive a snapshot on connect/resync and then WebSocket deltas from `MatrixState._emit()` / `broadcast()`.
- CLI read paths must keep working offline against SQLite when the daemon is stopped.
- Ephemeral agent fields (activity, current process/path/branch, token counts, needs_attention, live worktree diff, etc.) intentionally live only in memory and clear on restart.
- Slugs are persisted for agents, terminals, groups, and board tasks; startup fills missing/legacy slugs. CLI identifiers accept slugs, IDs, prefixes, and names where supported.
- Schema/state-shape changes usually require coordinated updates in dataclasses, SQLite serialization, server command/serialization, CLI read paths, frontend consumers, and tests.

### Kinds refactor invariants

- Agent kinds are explicit and final: `architect`, `engineer`, `worker`, `terminal`.
- Roles live only under `~/.torque/roles/` and project `.torque/roles/`; legacy `.torque/agents/*.yaml` files are ignored and warned by logs / `torque doctor`.
- Project `.torque/actions/**`, `.torque/roles/**`, and `.torque/specializations/**` are versioned config and must stay allow-listed while runtime `.torque/*` stays ignored.
- Keep the seven specialization slugs and matching worker roles in sync with `.torque/roles/*.yaml`, `.torque/specializations/*.yaml`, and [docs/reference/specializations.md](docs/reference/specializations.md).
- Ownership is explicit: workers use `owner_engineer_id`; tasks use `assigned_engineer_id`; engineers may carry `hired_by_architect_id`; architect-created tasks carry `created_by_architect_id`.
- Worker dispatch prepends role `preamble` / `priorities` unless an action sets `disable_role_preamble: true`.
- The Jinja `torque` namespace includes `torque.agent.kind`, `torque.agent.role`, `torque.agent.owner_engineer`, and `torque.agent.hired_by_architect` for architect-hired workers.
- MCP surfaces are final: worker-side `torque_*`, engineer-side `engineer_*`, and architect-side `architect_*`; legacy aliases are gone.
- Engineer scoping is strict: engineers see only themselves, owned workers/terminals, and in-group tasks assigned to them. Engineer-created workers/tasks are auto-stamped with ownership ids. Deleting an engineer clears those ids back to the user.
- Architects are user-created only, never hired. `TORQUE_ARCHITECT_ID` binds architect MCP sessions. Architects can create/reassign only their own architect-created tasks, and only to engineers they hired.
- Architect decisions are persisted in `decisions`; pending hires are user-approved and approval creates engineers with `hired_by_architect_id`.
- Architect ↔ engineer messaging is the only architect cross-kind channel. Engineers may message only their hiring architect. Workers report only through `torque_*` status / derive / ask flows.
- Worker worktrees use `torque/<engineer-slug>/<worker-slug>-<shortid>` or `torque/user/<worker-slug>-<shortid>`; engineer/architect worktrees stay flat; grandfathered flat worker branches remain valid.
- Review-cycle fixes stay on the implementer's branch. A `feature/review` → `feature/implement` fix is parented to the review's parent so worktree inheritance skips the reviewer. Merge refuses sibling review/implement branches with unmerged commits unless `force=true` is explicit after diffing.
- `torque doctor` is the verification surface for migration state, cleanup state, ignored legacy role files, and ownership/scope invariants. Full design record: [Agent Kinds Refactor](docs/plans/agent-kinds-refactor.md).

### Torque context namespace

Action templates can reference the injected `torque` dict: `torque.agent.*`, `torque.context.*`, `torque.worktree.*`, `torque.task.*`, and `torque.terminals`. `torque` is a reserved variable name and is rejected on action save. Preview renders use safe `TORQUE_CONTEXT_STUB` defaults.

## Worker dispatch and reporting

Dispatched workers report through MCP tools only: `torque_progress`, `torque_done`, `torque_blocked`, `torque_error`, `torque_ask`, `torque_derive`, `torque_ready`, `torque_verify`, and related shared-memory/artifact tools. `build_torque_system_prompt()` and `build_dispatch_postscript()` list the current completion paths; `torque_derive` is restricted to the action's declared transitions. The CLI `torque ai *` remains for humans/offline scripts, not worker prompt guidance.

Workers should not ask the user directly. Use `torque_ask` only for blocking human decisions or approvals so Torque can track the request.

## Code conventions

- Python: no framework beyond aiohttp + iterm2. State mutations should go through `MatrixState` methods, which emit deltas and targeted DB writes. Direct cell mutations from bridge/events/server must call `state._emit_agent(cell)` and `state._db_save_agent(cell)` unless only ephemeral fields changed. Catch and log iTerm2 API errors; never use bare `except: pass`.
- JS: no framework, no TypeScript, no build step. `webview.html` script order matters (core globals first, then board/modal submodules, then feature panels). State is patched in place from WS deltas.
- Live frontend panels must preserve operator state across routine rerenders: scroll/viewport anchor, hover/focus/caret, inline drafts, expanded sections, and selection. Prefer shared capture/restore helpers in `static/js/render.js` and add Node frontend regression coverage for rerender-stability fixes.
- CSS: single stylesheet, CSS custom properties for theming, monospace throughout.
- `window.confirm()` and `window.alert()` do not work in iTerm2's WKWebView; use custom modal/context-menu flows.

### Surface-invalidation discipline

For WS delta handling, mark expensive surfaces only when the focused panel actually displays the affected data. The common footgun is unconditional `_markSurface(flags, 'engineer')`, which causes high-frequency full panel rebuilds under normal worker activity.

Rules of thumb:

- Per-cell ops (`event_append`, `mcp_call_append`, `agent_digest_update`) affect engineer panel only when `op.cell_id` is the focused agent.
- Per-agent ops affect engineer panel only when the changed agent is focused or is owned by the focused engineer/architect.
- Per-group engineer/digest/journal ops affect engineer panel only when the focused engineer is in that group.
- Per-architect ops affect engineer panel only when the focused architect id matches the op (resolve cached records for remove/resolve ops when needed).
- `focus_update` is iTerm2 session/window focus, not agent-panel focus; do not mark engineer from it.
- Delta-driven callsites should route through `_agentPanelRefreshCurrentTab()` before falling back to full `renderAgentPanel()`.
- For stubborn flicker/textbox/scroll bugs, use existing `window.__torqueDebugRender` instrumentation before adding speculative gates.

## Testing

Run `make test`; it self-sanitizes inherited `TORQUE_*` runtime variables. Use targeted tests first when practical, but keep the full suite green before finishing. Manual runtime/deploy recipes live in [docs/operate/manual-testing.md](docs/operate/manual-testing.md) and must be run only from a non-worker shell.

## Reference docs

Moved reference material lives here to keep boot context small:

- [Detailed architecture reference](docs/reference/architecture.md)
- [iTerm2 API gotchas](docs/reference/iterm2-gotchas.md)
- [Claude Code hooks gotchas](docs/reference/hooks-gotchas.md)
- [Install locations](docs/reference/install-locations.md)
- [Manual testing](docs/operate/manual-testing.md)
