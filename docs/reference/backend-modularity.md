# Backend modularity boundaries

Torque keeps a small set of compatibility modules because they are imported by
the daemon, CLI, tests, and third-party local tooling. These modules are
composition roots, not default homes for new behavior.

## Compatibility roots and budgets

| Module | Maximum lines | Direct-ownership rule |
| --- | ---: | --- |
| `torque/server.py` | 6,000 | Transport/bootstrap composition; `handle_command` stays at or below 450 lines. |
| `torque/state.py` | 5,000 | Shared state contracts and `MatrixState` composition; `MatrixState` has at most 103 direct methods. |
| `torque/db.py` | 2,500 | `TorqueDB` connection/core persistence facade; it has at most 50 direct methods and domain methods belong in persistence mixins. |
| `torque/mcp_tools_shared.py` | 2,500 | Authentication/scoped-dispatch composition only. |
| `torque/worktree.py` | 2,500 | `WorktreeManager` is a structural composition facade: its only direct method is `__init__`. |
| `torque/db_schema.py` | 3,800 | Declarative DDL inventory and ordered migration ledger. |
| `torque/doctor.py` | 2,600 | Read-only diagnostic collection and text rendering. |
| `torque/mcp.py` | 2,600 | Reviewed post-authorization/pre-write validation seam in the transport composition root. |

All other backend Python modules must stay at or below 2,500 lines unless an
architecture review adds a documented structural exception and a tighter
file-specific budget.

## Headroom reporting and budget policy

The modularity guard reports its current, explicitly reviewed margins on every
green `tests.test_backend_modularity` run: the three direct-facade method
budgets and the five file-specific line budgets above.  The report uses
`actual/limit (headroom N)`, so a green `103/103` is visibly different from a
green `30/103`.  This is reporting, not a new failure mode; one concise line
keeps regular test output actionable without making a green suite appear red.

The merge preflight keeps its crossing-only blocking predicate.  Its successful
result now includes `headroom` for each changed backend file, with both the
target-base and candidate margins, plus a non-blocking `warnings` entry when
the candidate has 0--10 lines remaining.  The author-runnable command prints
that data as JSON:

```bash
python3 -m torque.backend_invariants \
  --repo . --base-ref <current-target-ref> --candidate-ref <candidate-ref>
```

This answers five policy questions deliberately:

1. **Proximity is reported, not inferred from a verdict.**  The green test
   report covers all eight reviewed quantities; the merge preflight covers
   each changed candidate file against its target.
2. **Slack is not a silently raised limit.**  Limits remain architectural caps.
   Headroom is an observable margin, and zero is a visible signal to extract
   responsibility rather than padding a number.  Changing a limit requires a
   separate architecture decision, rationale, documentation, and review; this
   guard does not create such a bypass.
3. **`WorktreeManager` at 1/1 is structural, not capacity planning.**  It
   defends a facade that constructs and composes domain mixins only.  Direct
   worktree behavior belongs to exactly one `worktree_manager/` mixin.
4. **Legitimate facade growth has a non-`force` path.**  Put the behavior in
   the existing or a new focused mixin/service and compose it into the facade.
   If the public contract truly requires changing the facade boundary itself,
   propose a separately reviewed architectural boundary change with its tests
   and documentation before implementation.  Do not edit around the guard or
   force a merge.
5. **Concurrent work uses the current target as the source of truth.**  Before
   handoff/merge, run the command above (or let the mandatory preflight run)
   against the current target tip; it exposes target and candidate margins and
   blocks a newly crossed limit.  Git has no authoritative record of an
   unmerged sibling's intended future line count, so this change deliberately
   does not invent a reservation system.  A future worktree-stream surface can
   compare every live candidate to the current target using this returned
   headroom data; until then, target refresh/rebase plus the documented command
   is the supported coordination path.

## Responsibility splits

- `torque/server_user_commands.py` handles local task-watch plus
  one-shot-reminder user commands.
- `torque/server_engineer_commands.py` handles Engineer journal, digest, plus
  flush commands.
- `torque/worktree_stream_readiness.py` provides cached Git probes for
  worktree stream synthesis.
- `torque/backend_invariants.py` detects backend file-size invariant crossings
  between Git revisions.

Each purpose above is intentionally singular. A future split that cannot state
each resulting module's purpose in one sentence without using "and" needs an
explicit architecture review rather than a size-driven partition.

## Merge-path invariant preflight

The shared direct/PR worktree preflight calls
`check_backend_modularity_crossings()` before merge side effects. It checks
backend Python files touched between the target base ref and candidate branch,
then blocks files whose candidate line count newly exceeds the applicable
reviewed limit. The trusted target base supplies both the default and per-path
line-limit policy, so a candidate cannot authorize its own growth and the
result does not depend on the running daemon's code revision. A reviewed budget
change must therefore merge without the target growth first; the growing
candidate must then be based on, or rebased onto, the revision containing that
budget. No daemon relaunch is required. Applicability comes from Torque's
backend-modularity test marker on the trusted target base, so a candidate
cannot self-disable the gate by deleting or renaming that marker. Target bases
without the marker are outside this repository-specific gate. Unreadable or
malformed base policy blocks the check rather than falling back to in-process
defaults.

The same check remains author-runnable for diagnosis:

```bash
python3 -m torque.backend_invariants \
  --repo . --base-ref <base> --candidate-ref <candidate>
```

Exit status `1` means a crossing was found. Exit status `2` means the check
could not produce trustworthy evidence; the automated merge preflight treats
that outcome as blocking.

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
