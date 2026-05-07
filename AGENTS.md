# AGENTS.md

## What This Repo Is

Torque is a local agent-orchestration workspace built around:

- a long-running Python daemon
- an iTerm2 terminal adapter
- a no-build-step HTML/CSS/JS frontend
- SQLite as the persistent source of truth

The product center of gravity is `torque/server.py` plus `torque/state.py`. Most other modules hang off that.

## Repo Map

- `torque.py`: installed entrypoint; anchors runtime paths and starts the daemon via `iterm2.run_forever(...)`
- `torque/`: backend package
- `bin/torque`: CLI; write commands go through HTTP, many read commands go straight to SQLite
- `webview.html`: frontend shell; script load order matters
- `static/`: plain JS/CSS frontend, no bundler
- `actions/`: starter action YAMLs
- `skills/`: Torque task-specific skills/prompts
- `tests/`: Python `unittest` suite plus Node-based frontend regression tests
- `docs/`: current operator/product docs

## Backend Shape

- `torque/server.py`: aiohttp server, route registration, command dispatch, integration glue
- `torque/server_agent.py`, `torque/server_dispatch.py`, `torque/server_worktrees.py`, `torque/server_artifacts.py`, `torque/server_actions.py`: extracted helpers for server-heavy concerns
- `torque/state.py`: core dataclasses (`AgentCell`, `BoardTask`, `Schedule`, settings) and `MatrixState`
- `torque/db.py`, `torque/db_schema.py`, `torque/db_board.py`, `torque/db_memory.py`: SQLite schema and persistence helpers
- `torque/actions.py`: YAML loading plus Jinja2 prompt rendering for actions
- `torque/templates.py`: agent template discovery and config resolution
- `torque/worktree.py`, `torque/worktree_boundaries.py`: git worktree lifecycle and merge-boundary logic
- `torque/mcp.py`, `torque/mcp_engineer.py`, `torque/mcp_engineer_tools/`: MCP tool surfaces for agents and the per-group engineer
- `torque/engineer.py`: engineer system prompt, digest buffering, idle-gated delivery
- `torque/adapters/`: provider-specific agent integrations (`claude-code`, `codex`, `gemini-cli`, fallback generic)
- `torque/memory.py`, `torque/artifacts.py`, `torque/external_tickets.py`: shared memory, task artifacts, and external issue linkage

## Frontend Shape

The frontend is intentionally simple:

- no framework
- no TypeScript
- no build step
- global state patched in place from WebSocket deltas

Important constraint: script order in [webview.html](webview.html) is part of the architecture. Core files load first, then board/modal submodules, then feature panels.

Live-update constraint: most panels are re-rendered from WebSocket-driven state patches. Frontend changes must preserve operator state across routine rerenders unless the view intentionally navigates away. That includes scroll position, viewport anchor when new content is inserted above the user, hover/focus/caret, inline drafts, expanded sections, and selection/highlight state.

Primary files:

- `static/js/ws.js`: socket client, snapshot/delta application
- `static/js/render.js`: main grid rendering
- `static/js/board*.js`: board UI and card behavior
- `static/js/modals*.js`: modal flows
- `static/js/actions.js`, `templates.js`, `events.js`, `context.js`, `engineer.js`, `diff.js`, `taskhistory.js`: feature panels
- `static/style.css`: single stylesheet

## Data Model Rules

- SQLite is the persistent source of truth.
- The CLI depends on direct SQLite reads for offline/read-only commands.
- Web clients depend on snapshot + delta messages from `MatrixState`.
- Some `AgentCell` fields are intentionally ephemeral and are not persisted across restart.

If you change persisted state or object shape, you usually need to update all of:

1. dataclasses/state normalization in `torque/state.py`
2. SQLite schema/serialization in `torque/db*.py`
3. server serialization / command handlers
4. CLI SQLite read paths in `bin/torque`
5. frontend consumers in `static/js/*`
6. tests

## Project-Specific Rules

- Prefer code and tests over prose docs when they disagree. `CLAUDE.md` is useful, but some parts are stale; for example, the repo now has an automated test suite.
- Action prompts are Jinja2 templates, but only the `prompt` field is rendered. Actions must include `{{ TASK }}` or `{{ torque.task.title }}`.
- Project-local `.torque/actions/` and `.torque/agents/` override user-global definitions under `~/.torque/`.
- Worktree support is a core feature. Changes in task dispatch, merge flow, or agent reuse often also affect worktree inheritance and boundary tracking.
- Engineer behavior is not isolated to one file. Changes often span `engineer.py`, `mcp_engineer.py`, server command handling, board/event UI, and tests.
- Runtime-generated Torque files inside repos/worktrees are intentional. Be careful around `.claude/`, `.codex/`, `.mcp.json`, and `.torque/worktrees/` behavior.
- When changing a live frontend panel, do not replace an interactive subtree unless you either keep its DOM stable or explicitly capture and restore the operator state it owns before paint.
- Frontend rerender-stability fixes must ship with Node frontend regression coverage in `tests/frontend_state_regression.test.js` or a nearby targeted frontend test.

## Commands

- `make install`: copy files into the iTerm2 Scripts project
- `make deploy`: stop old instance and reinstall
- `make stop`: free port `18932`
- `make standalone`: browser-only UI mode, still backed by iTerm2
- `make open`: open the web UI in a browser
- `make cli`: install the `torque` CLI symlink
- `make test`: run the regression suite

**Never `make deploy` from inside a worker/engineer shell.** The Makefile refuses `stop`/`deploy`/`restart` when `TORQUE_CELL_ID` is set or pwd is under `.torque/worktrees/` — killing the daemon you are talking to corrupts the in-memory dispatch state on the next boot and DOA's every subsequent worker. Override with `FORCE=1` only when you accept the risk. Alternative: commit to main and ask the user to deploy, or test on a different port (`TORQUE_PORT=18933`). See `CLAUDE.md → "Never make deploy mid-session"` for the full failure-mode notes.

Useful runtime paths:

- log: `~/Library/Application Support/iTerm2/Scripts/torque/torque/torque.log`
- DB: `~/Library/Application Support/iTerm2/Scripts/torque/torque/torque.db`

## Testing

Use `make test`.

That currently runs:

- Python `unittest` coverage across backend/state/worktree/MCP/frontend wrappers
- Node `--test` regression coverage for frontend state behavior

When touching specific areas, prefer targeted tests first, but keep the full suite green before finishing.

## High-Value Checks Before You Ship

- If you changed task, action, engineer, or MCP behavior, verify both backend and board/UI expectations.
- If you changed frontend state shape, verify delta application and the relevant Node regression tests.
- If you changed persistence, verify daemon writes and CLI offline reads both still work.
- If you changed worktree behavior, check gitignore/exclude handling, inheritance, merge detection, and cleanup paths.
