# Testing

Loom's automated coverage is intentionally layered. Most confidence should come
from Python unit and integration tests, with only a thin UI/state smoke layer
for frontend regressions.

## Baseline Stabilization

Before expanding coverage, the suite needed one explicit cleanup:

- `tests/test_worktree_gitignore.py` had drifted from the current product
  behavior. `loom/worktree.py` only writes `.loom/worktrees/` to `.gitignore`;
  Loom-injected local files belong in `.git/info/exclude`. The test now matches
  that contract so the baseline can go green before broader coverage expansion.

## Regression Matrix

| Surface | Baseline Coverage | This Wave | Recommended Layer | Remaining Gap |
|---|---|---|---|---|
| Agent templates and adapter flags | `tests/test_agent_template_adapters.py`, `tests/test_action_agent_templates.py`, plus broader adapter coverage already landing in `tests-implementation-wave` | No duplicate additions here | Unit | Full provider hook/MCP lifecycle should continue to live in adapter-focused unit tests |
| Board, task lifecycle, dependencies, smoke task flow | Existing `tests/test_state.py`; larger workflow coverage already landing in `tests-implementation-wave` via `tests/test_state.py` and `tests/test_smoke_flows.py` | No duplicate additions here | Unit + integration smoke | Full `server.py` command-path coverage is still thinner than the underlying workflow model coverage |
| Worktree lifecycle | `tests/test_worktree_gitignore.py`; deeper lifecycle coverage already landing in `tests-implementation-wave` via `tests/test_worktree_lifecycle.py` | Fixed the stale gitignore baseline expectation | Unit + git-backed integration | PR creation and more server-driven merge flows remain lightly covered |
| MCP agent tool dispatch | None before this wave | `tests/test_mcp.py` covers tool-to-`ai_report` mapping and JSON-RPC handler behavior | Integration | `server.py` command execution through `/api/cmd` still needs broader end-to-end coverage |
| SQLite persistence and weaver journal state | None before this wave | `tests/test_db.py` covers round-trips for agents, tasks, schedules, panel events, weaver settings, and journal entries | Unit/integration | Schema migration edge cases and historical backfill paths remain thin |
| Cron scheduler parsing | None before this wave | `tests/test_cron.py` covers parsing, timezone-aware next-run calculation, and invalid expressions | Unit | More schedule execution coverage should remain in higher-level smoke tests |
| Event bus and health monitoring | Limited indirect coverage through weaver tests | `tests/test_events.py` covers session start handling, blocked-label clearing, and stuck-agent health checks | Unit/integration | Full broadcast throttling and panel-log persistence interactions remain thinner than core state updates |
| Notifications | None before this wave | `tests/test_notifications.py` covers batching, grouping, and notification gating | Unit | Actual macOS `osascript` delivery remains intentionally untested |
| Global keybindings | None before this wave | `tests/test_keybindings.py` covers binding resolution, ordering helpers, and displaced-binding preservation | Unit | Real iTerm2 RPC registration remains untested outside manual validation |
| Frontend state sync and regressions | Coverage already landing in `tests-implementation-wave` via `tests/test_frontend_state.py` and `tests/frontend_state_regression.test.js` | No duplicate additions here | UI/state smoke | Full browser-driven standalone smoke remains intentionally thin |

## Running Tests

Use the repo entrypoint:

```bash
make test
```

This runs the Python regression suite with:

```bash
python3 -m unittest discover -s tests -v
```

If the `tests-implementation-wave` frontend regression files are present, they
run through the Python suite as part of that same entrypoint.
