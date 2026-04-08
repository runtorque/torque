# AGENTS.md

## What This Repo Is

Loom is a local agent-orchestration workspace built around:

- a long-running Python daemon
- an iTerm2 terminal adapter
- a no-build-step HTML/CSS/JS frontend
- SQLite as the persistent source of truth

The product center of gravity is `loom/server.py` plus `loom/state.py`. Most other modules hang off that.

## Repo Map

- `loom.py`: installed entrypoint; anchors runtime paths and starts the daemon via `iterm2.run_forever(...)`
- `loom/`: backend package
- `bin/loom`: CLI; write commands go through HTTP, many read commands go straight to SQLite
- `webview.html`: frontend shell; script load order matters
- `static/`: plain JS/CSS frontend, no bundler
- `actions/`: starter action YAMLs
- `skills/`: Loom task-specific skills/prompts
- `tests/`: Python `unittest` suite plus Node-based frontend regression tests
- `docs/`: current operator/product docs

## Backend Shape

- `loom/server.py`: aiohttp server, route registration, command dispatch, integration glue
- `loom/server_agent.py`, `loom/server_dispatch.py`, `loom/server_worktrees.py`, `loom/server_artifacts.py`, `loom/server_actions.py`: extracted helpers for server-heavy concerns
- `loom/state.py`: core dataclasses (`AgentCell`, `BoardTask`, `Schedule`, settings) and `MatrixState`
- `loom/db.py`, `loom/db_schema.py`, `loom/db_board.py`, `loom/db_memory.py`: SQLite schema and persistence helpers
- `loom/actions.py`: YAML loading plus Jinja2 prompt rendering for actions
- `loom/templates.py`: agent template discovery and config resolution
- `loom/worktree.py`, `loom/worktree_boundaries.py`: git worktree lifecycle and merge-boundary logic
- `loom/mcp.py`, `loom/mcp_weaver.py`, `loom/mcp_weaver_tools/`: MCP tool surfaces for agents and the per-group weaver
- `loom/weaver.py`: weaver system prompt, digest buffering, idle-gated delivery
- `loom/adapters/`: provider-specific agent integrations (`claude-code`, `codex`, `gemini-cli`, fallback generic)
- `loom/memory.py`, `loom/artifacts.py`, `loom/external_tickets.py`: shared memory, task artifacts, and external issue linkage

## Frontend Shape

The frontend is intentionally simple:

- no framework
- no TypeScript
- no build step
- global state patched in place from WebSocket deltas

Important constraint: script order in [webview.html](/Users/aleksanderarruda/dev/personal/gh/iterm2-loom/.loom/worktrees/cacfabe5/webview.html) is part of the architecture. Core files load first, then board/modal submodules, then feature panels.

Primary files:

- `static/js/ws.js`: socket client, snapshot/delta application
- `static/js/render.js`: main grid rendering
- `static/js/board*.js`: board UI and card behavior
- `static/js/modals*.js`: modal flows
- `static/js/actions.js`, `templates.js`, `events.js`, `context.js`, `weaver.js`, `diff.js`, `taskhistory.js`: feature panels
- `static/style.css`: single stylesheet

## Data Model Rules

- SQLite is the persistent source of truth.
- The CLI depends on direct SQLite reads for offline/read-only commands.
- Web clients depend on snapshot + delta messages from `MatrixState`.
- Some `AgentCell` fields are intentionally ephemeral and are not persisted across restart.

If you change persisted state or object shape, you usually need to update all of:

1. dataclasses/state normalization in `loom/state.py`
2. SQLite schema/serialization in `loom/db*.py`
3. server serialization / command handlers
4. CLI SQLite read paths in `bin/loom`
5. frontend consumers in `static/js/*`
6. tests

## Project-Specific Rules

- Prefer code and tests over prose docs when they disagree. `CLAUDE.md` is useful, but some parts are stale; for example, the repo now has an automated test suite.
- Action prompts are Jinja2 templates, but only the `prompt` field is rendered. Actions must include `{{ TASK }}` or `{{ loom.task.title }}`.
- Project-local `.loom/actions/` and `.loom/agents/` override user-global definitions under `~/.loom/`.
- Worktree support is a core feature. Changes in task dispatch, merge flow, or agent reuse often also affect worktree inheritance and boundary tracking.
- Weaver behavior is not isolated to one file. Changes often span `weaver.py`, `mcp_weaver.py`, server command handling, board/event UI, and tests.
- Runtime-generated Loom files inside repos/worktrees are intentional. Be careful around `.claude/`, `.codex/`, `.mcp.json`, and `.loom/worktrees/` behavior.

## Commands

- `make install`: copy files into the iTerm2 Scripts project
- `make deploy`: stop old instance and reinstall
- `make stop`: free port `18932`
- `make standalone`: browser-only UI mode, still backed by iTerm2
- `make open`: open the web UI in a browser
- `make cli`: install the `loom` CLI symlink
- `make test`: run the regression suite

Useful runtime paths:

- log: `~/Library/Application Support/iTerm2/Scripts/loom/loom/loom.log`
- DB: `~/Library/Application Support/iTerm2/Scripts/loom/loom/loom.db`

## Testing

Use `make test`.

That currently runs:

- Python `unittest` coverage across backend/state/worktree/MCP/frontend wrappers
- Node `--test` regression coverage for frontend state behavior

When touching specific areas, prefer targeted tests first, but keep the full suite green before finishing.

## High-Value Checks Before You Ship

- If you changed task, action, weaver, or MCP behavior, verify both backend and board/UI expectations.
- If you changed frontend state shape, verify delta application and the relevant Node regression tests.
- If you changed persistence, verify daemon writes and CLI offline reads both still work.
- If you changed worktree behavior, check gitignore/exclude handling, inheritance, merge detection, and cleanup paths.
