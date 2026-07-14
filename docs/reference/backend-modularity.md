# Backend modularity boundaries

Torque keeps a small set of compatibility modules because they are imported by
the daemon, CLI, tests, and third-party local tooling. These modules are
composition roots, not default homes for new behavior.

## Compatibility roots and budgets

| Module | Maximum lines | Direct-ownership rule |
| --- | ---: | --- |
| `torque/server.py` | 6,000 | Transport/bootstrap composition; `handle_command` stays at or below 450 lines. |
| `torque/state.py` | 5,000 | Shared state contracts and `MatrixState` composition; direct methods may not increase beyond the audited baseline. |
| `torque/db.py` | 2,500 | `TorqueDB` connection/core persistence facade; domain methods belong in persistence mixins. |
| `torque/mcp_tools_shared.py` | 2,500 | Authentication/scoped-dispatch composition only. |
| `torque/worktree.py` | 2,500 | `WorktreeManager` composition plus initialization only. |
| `torque/db_schema.py` | 3,750 | Declarative DDL inventory and ordered migration ledger. |
| `torque/doctor.py` | 2,600 | Read-only diagnostic collection and text rendering. |

All other backend Python modules must stay at or below 2,500 lines unless an
architecture review adds a documented structural exception and a tighter
file-specific budget.

## Domain ownership

- `torque/commands/` owns backend command semantics and route manifests.
- `torque/server_*.py` owns reusable daemon integrations; `server.py` supplies
  live runtime dependencies explicitly.
- `torque/services/` owns business orchestration behind state/server
  compatibility surfaces.
- `torque/persistence/` owns domain SQLite reads and writes composed into
  `TorqueDB`.
- `torque/mcp_scoped/` owns Engineer/Architect scoped-tool behavior and domain
  dispatchers.
- `torque/worktree_manager/` owns Git/worktree primitives composed into
  `WorktreeManager`.
- `torque/services/worktrees/` owns server-facing merge, PR, gate, and evidence
  orchestration.
- `torque/state_*.py` owns focused `MatrixState` behavior.

## Dependency rules

1. Domain modules do not import the compatibility roots they implement:
   `torque.server`, `torque.db`, `torque.mcp_tools_shared`, or
   `torque.worktree`.
2. Runtime builders receive callbacks from `main()` explicitly. They must not
   depend on unresolved closure-local names.
3. New command branches belong in a `torque.commands` module. The server
   dispatcher should only select a domain handler and compose its runtime.
4. Persistence mixins own complete domain operations; the `TorqueDB` facade
   preserves stable names and connection lifecycle.
5. MCP domain dispatchers return `UNHANDLED` when a tool is outside their
   namespace and never bypass caller authorization/scoped state construction.
6. Worktree implementation methods belong to exactly one domain mixin; the
   facade owns only construction.

These rules are executable in `tests/test_backend_modularity.py`. Update the
documentation and tests together when a boundary intentionally changes.
