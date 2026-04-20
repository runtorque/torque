# Agent Kinds Refactor — Implementation Plan

## 0. Goals

1. Replace the legacy single-engineer Weaver model with multiple named persistent **Engineers**, scoped to only see their own agents/tasks and derived descendants.
2. Add a new **Architect** kind: product-level, creates and assigns tasks, maintains a typed decision log and journal, can hire engineers, talks only to engineers and the user.
3. Rename regular agents to **Workers**; rename **Templates → Roles** and attach them to workers as persistent personas (provider, env, and a behavior preamble injected at dispatch).
4. User sees everything in the UI, ordered hierarchically: Architect → its Engineers → each Engineer's Workers.
5. Branch namespacing per engineer (`loom/<engineer-slug>/...`).
6. Orphan policy on engineer deletion: transfer to user.

## 1. Data model changes

### 1.1 AgentCell

Add:

- `kind: str` — one of `"architect"`, `"engineer"`, `"worker"`, `"terminal"`. Replaces the implicit `cell_type` distinction for agents. `cell_type == "terminal"` stays as-is; `cell_type == "agent"` splits into `kind`.
- `role: str` — slug of the role (workers only). Replaces `template` semantically; `template` stays as a deprecated alias during migration.
- `owner_engineer_id: str` — FK to the engineer that owns this agent. Empty = owned by user. For workers, set at creation by the engineer that dispatched them. For engineers, set at creation by the architect that hired them (empty if user-created). For architects, always empty.
- `hired_by_architect_id: str` — FK (engineers only). Empty = user-created.
- `persistent: bool` — engineers and architects are persistent; workers are not. Drives "relaunch vs hard delete" UI behavior.

Reuse `created_by_weaver_id` as `owner_engineer_id` during migration — it's the same concept, renamed.

### 1.2 BoardTask

Add:

- `assigned_engineer_id: str` — which engineer is responsible. Replaces `weaver_owner_id`. For architect-created tasks, set by the architect. For user-created tasks, may be empty until assigned.
- `created_by_architect_id: str` — provenance, immutable.
- `suggested_action: str` — architect's non-binding hint (separate from `action_name`, which is what the engineer actually chose).

Ownership rule on derive: if a worker or engineer derives a task, the new task inherits `assigned_engineer_id` from its parent automatically. Never crosses engineer boundaries unless an architect explicitly reassigns.

### 1.3 Role (replacing Template)

Primary role files live in `~/.loom/roles/` (and project `.loom/roles/`) with
compat read-through from the legacy template locations `~/.loom/agents/` and
project `.loom/agents/` during the transition.

Schema adds:

- `preamble: str` — block-scalar system-prompt-style text injected at the top of every dispatch prompt for workers with this role. This is the "behavior" layer, distinct from actions.
- `priorities: list[str]` — optional ordered list surfaced in the preamble as a bullet list (lightweight structured guidance so users don't have to write free-form preambles).
- All existing template fields (provider/command/model/env_vars/worktree/etc.) carry over.

Dispatch prompt assembly becomes: `role.preamble → action.prompt(vars) → loom_postscript`. Actions may opt out of the preamble injection by setting `disable_role_preamble: true` — for edge cases like one-shot diagnostics where a persona would be noise.

### 1.4 Decision log (Architect)

New SQLite table `decisions`:

```
id TEXT PK, architect_id TEXT, title TEXT, rationale TEXT,
status TEXT ('proposed'|'accepted'|'revised'|'rejected'),
supersedes TEXT NULL, linked_task_ids JSON, linked_engineer_ids JSON,
created_at INT, updated_at INT
```

Rendered in the UI as a list grouped by status, with click-through to linked tasks. Simple, queryable, extensible.

### 1.5 Architect journal

Reuse the existing journal storage pattern (JSONL per agent). One file per architect, keyed by architect id. Same entry shape as the legacy engineer journal today. Separate from decisions — the journal is the audit trail, decisions are the curated product map.

### 1.6 Schema migrations

One SQLite migration in `db.py`:

- `ALTER TABLE agents ADD COLUMN kind TEXT DEFAULT ''`
- `ALTER TABLE agents ADD COLUMN role TEXT DEFAULT ''`
- `ALTER TABLE agents ADD COLUMN owner_engineer_id TEXT DEFAULT ''`
- `ALTER TABLE agents ADD COLUMN hired_by_architect_id TEXT DEFAULT ''`
- `ALTER TABLE agents ADD COLUMN persistent INTEGER DEFAULT 0`
- `ALTER TABLE board_tasks ADD COLUMN assigned_engineer_id TEXT DEFAULT ''`
- `ALTER TABLE board_tasks ADD COLUMN created_by_architect_id TEXT DEFAULT ''`
- `ALTER TABLE board_tasks ADD COLUMN suggested_action TEXT DEFAULT ''`
- `CREATE TABLE IF NOT EXISTS decisions (...)`

Data backfill on first load:

- Existing Weaver(s) → `kind='engineer'`, `persistent=1`, `owner_engineer_id=''`, name becomes `"Weaver"` (preserving provenance; user can rename from the Engineers panel).
- Existing non-Weaver agents → `kind='worker'`, `role=template`, `owner_engineer_id=created_by_weaver_id`.
- Existing tasks → `assigned_engineer_id=weaver_owner_id`.
- Copy `template` → `role`.

Old columns (`template`, `weaver_owner_id`, `created_by_weaver_id`) stay writable for one release to keep rollback safe, then are removed.

## 2. Tool surface split

Currently one MCP surface (`weaver_*`) does everything. Split by kind:

### Architect tools (`architect_*`)

- `architect_task_create` (with optional `suggested_action`, required `assigned_engineer_id`)
- `architect_task_reassign` (move a task between engineers)
- `architect_engineer_hire` (create a new engineer; user confirmation required — see §5)
- `architect_engineer_list` (only engineers it has hired, plus user-visible engineers)
- `architect_engineer_message` (direct message to engineer) and `architect_reply` (reply to engineer messages)
- `architect_decision_create` / `_update` / `_link` / `_list`
- `architect_journal` / `architect_journal_read`
- Read surface: `architect_board_summary` (scoped to tasks it created or engineers it hired)
- **No** `*_dispatch`, **no** `*_agent_message` to workers.

### Engineer tools (`engineer_*`)

Identical behavior to today's `weaver_*`, but ownership-filtered:

- All current `weaver_*` tools, filtered to `owner_engineer_id == self.id` (transitively via task-derivation chain).
- Tasks the user or an architect assigned to this engineer appear in its scope.
- Worker creation happens implicitly via the existing dispatch/create flow.

### Worker tools (`loom_*`, unchanged)

- `loom ai done|blocked|error|progress|ready|derive|ask` — the CLI agent reports directly.

### Implementation

One shared tool implementation layer takes a `caller_kind` + `caller_id` and applies the scoping filter. The MCP entrypoint modules (`mcp_architect.py`, `mcp_engineer.py`) each expose the subset of tools permitted for that kind.

## 3. Scoping & visibility

The core query: "what can engineer E see?"

- The engineer row for `E` itself, plus worker/terminal agents where
  `owner_engineer_id == E.id`.
- Tasks in the engineer's own group where `assigned_engineer_id == E.id`.
- Unassigned tasks (`assigned_engineer_id == ''`) in the engineer's own group as
  a shared inbox. These stay out of `engineer_board_summary` until assigned.
- Derived descendants: walk `parent_task_id` / `pipeline_root_id` — since ownership follows parent on derive, this is implicit in the FK, no graph walk needed at read time.
- Children (terminals) of visible worker agents.
- Writes that create agents or tasks stamp `owner_engineer_id` /
  `assigned_engineer_id` to `E.id`; stage 3 engineers cannot override that.

Implemented in the shared MCP tool layer, not the UI: the UI still reads
unfiltered state over WebSocket deltas.

Architect scoping is symmetric: `architect_id == self.id` on engineers it hired, tasks it created.

## 4. UI hierarchy

The UI stays "see everything" but the order changes:

- Sort agents top-to-bottom:
  1. Architects (sorted by name)
  2. For each architect: its engineers (by hire order), and under each engineer its workers
  3. Engineers with no architect (user-hired) and their workers
  4. Orphan workers (no engineer) last
- Visual treatment: indentation + a faint connector line. Each kind gets a distinct icon/badge.
- Group header becomes optional — hierarchy replaces it as the primary organizing principle. (Groups still exist for color/settings, but they're no longer the top-level axis.)
- Panel rename: "Weaver" panel → "Engineers" panel. New "Architects" panel parallel to it. Roles editor replaces Templates editor.
- Architect detail UI also carries the live decision log and pending-hire review surfaces. Those views update from `decision_*` and `pending_hire_*` WebSocket deltas instead of a separate polling loop.

## 5. Hire / fire / relaunch semantics

- **Hire (architect → engineer)**: architect calls `architect_engineer_hire`. This queues a pending hire visible to the user with approve/reject. The tool returns immediately with `status='pending'`; the architect must poll for resolution. On approve, a new engineer row is created (`persistent=1`) with `hired_by_architect_id=architect.id`. On reject, the architect gets a tool error and logs the rejection in its journal. Every hire requires explicit user approval in v1 — no pre-approved/trusted-architect mode. We'll revisit after observing usage.
- **Relaunch engineer**: existing agent relaunch flow; works because `persistent=1` keeps the row alive after session end.
- **Delete engineer**: hard delete only via the Engineers panel with user confirmation. On delete: all agents with `owner_engineer_id == deleted.id` get `owner_engineer_id=''` (transfer to user); all tasks with `assigned_engineer_id == deleted.id` get `assigned_engineer_id=''`. Worktrees stay (cleanup is separate). Deleting the last engineer is still allowed; `weaver_*` aliases then fail with a clear "create an engineer" error until one exists again.
- **Delete architect**: same pattern — its engineers transfer to user (they keep running). Its decision log is archived (kept readable but marked `archived=1`), not deleted, so the audit trail survives.

## 6. Branch namespacing

Worker worktree branches go from `loom/<agent-slug>-<shortid>` to `loom/<engineer-slug>/<worker-slug>-<shortid>`. Workers owned by the user (no engineer) use `loom/user/<worker-slug>-<shortid>`. Engineer and architect worktrees stay flat (`loom/<engineer-slug>-<shortid>` / `loom/<architect-slug>-<shortid>`) because they are already the ownership root. This lives in `worktree.py`. Migration: existing branches are grandfathered (no rename), new ones use the new scheme.

## 7. Dispatch prompt changes

Today's dispatch prompt is `action.prompt(vars) + loom_postscript`. New:

```
{role.preamble}            # omitted if action.disable_role_preamble or no role

{action.prompt(vars)}

{loom_postscript}
```

The `loom` context namespace gets additions:

- `loom.agent.kind` — `"worker"` / `"engineer"` / `"architect"`
- `loom.agent.role` — role name (workers only)
- `loom.agent.owner_engineer` — engineer name (workers only)

Architects and engineers get their own boot prompts when launched (like the legacy `weaver_*` boot prompt), stored as special system roles (not user-editable in the first pass).

## 8. Communication graph enforcement

Enforce in the MCP layer, not via runtime checks:

- `architect_engineer_message` is the only cross-kind messaging tool architects have. No worker-targeting tool exists on the architect surface.
- Architect task assignment / reassignment and architect messaging are limited to engineers the architect hired; workers are never direct architect targets.
- `engineer_*` tools that target agents filter to `owner_engineer_id == self.id`, so an engineer can't message another engineer's worker.
- Engineers can talk only to their hiring architect via `engineer_message_architect` and reply to architect messages via `engineer_reply`. No user approval — routine coordination.
- `weaver_*` compatibility aliases stay bound to the explicit engineer session when one is present; fallback to the default engineer only when no engineer session id is available.
- The engineer boot prompt explicitly instructs: *when a non-trivial product or scope decision arises, call the architect via `engineer_message_architect` before committing to a direction.* This keeps decision ownership with the architect.
- Workers never initiate messages to engineers or architects — they use `loom ai` to report, which surfaces in the dashboards.

## 9. Staging

Six stages, each independently shippable with a concrete acceptance criterion.

### Stage 1 — Schema & migration groundwork ✓ shipped

**Deliverable**: a safely-migrated database with new columns populated, zero behavior change, and a verification tool.

- Back up `loom.db` to `loom.db.pre-kinds.bak` before migration runs (once, idempotent — skip if backup already exists).
- Add all new columns (`kind`, `role`, `owner_engineer_id`, `hired_by_architect_id`, `persistent`, `assigned_engineer_id`, `created_by_architect_id`, `suggested_action`) and create `decisions` table.
- Define an explicit **legacy-engineer identification rule** for backfill: an agent is treated as the stage-1 migrated engineer if (a) it's in the "loom" reserved group and (b) its `template` matches the legacy Weaver template slug, else fall back to the agent whose MCP session registers the `weaver_*` tool surface on startup. Document this rule in the migration module.
- Backfill:
  - Each identified Weaver → `kind='engineer'`, `persistent=1`, name coerced to `"Weaver"` (with uniqueness suffix if needed).
  - Other agents → `kind='worker'`, `role=template`, `owner_engineer_id=created_by_weaver_id`.
  - Tasks → `assigned_engineer_id=weaver_owner_id`, `created_by_architect_id=''`.
- **Dual-write safety net**: until stage 6, every write path that sets `template` also sets `role`, every write that sets `weaver_owner_id` also sets `assigned_engineer_id`, and vice versa. Implemented as a thin wrapper layer in `db.py` so no call site has to remember. Prevents drift while old and new code coexist.
- Add a `loom doctor` CLI subcommand (and `/api/cmd doctor` endpoint) that reports: total agents by `kind`, total tasks with non-empty `assigned_engineer_id`, drift between old/new columns (should be zero), backup file presence, migration timestamp. This is the verification surface.

**Acceptance criterion**: on an existing database, `loom doctor` reports zero drift, exactly one engineer (named `"Weaver"`), every worker has `owner_engineer_id` set or empty-for-orphans, every task has `assigned_engineer_id` matching legacy `weaver_owner_id`. No UI or dispatch behavior change observed by the user.

### Stage 2 — Roles + preamble (worker persona layer) ✓ shipped

**Deliverable**: a working Role concept with a preamble field that takes effect on dispatch.

- Rename the effective template storage to `roles/` with compat read-through from legacy `agents/`: if a name exists in both paths, `roles/` wins and a warning is logged. Writes always go to `roles/`.
- Add `preamble: str` (block scalar) and `priorities: list[str]` to the role schema. `priorities` renders into the preamble as a bullet list at render time (not stored twice).
- Add `disable_role_preamble: bool` to the action schema and honor it in the dispatch prompt assembler: when true, skip the preamble block entirely.
- Dispatch prompt assembly (workers only): `role.preamble → action.prompt(vars) → loom_postscript`. Engineers and architects bypass role preamble — they have their own boot prompts (stage 3/4).
- Extend the `loom` Jinja context: `loom.agent.kind`, `loom.agent.role`, `loom.agent.owner_engineer`.
- UI: rename Templates editor to Roles, add a `preamble` textarea with Jinja-aware editing and a `priorities` list editor.

**Acceptance criterion**: create a role with a preamble + two priorities, assign it to a worker, dispatch a task → preview prompt shows the preamble at the top followed by the action prompt. Create an action with `disable_role_preamble: true` → preview omits the preamble. Existing templates load and dispatch unchanged (compat read works).

### Stage 3 — Engineer kind + scoped MCP surface + multiple engineers ✓ shipped

**Deliverable**: the user can create multiple named engineers; each sees only its own agents and tasks; existing `weaver_*` clients still work via an alias.

- **Engineer launch infra**: new "Add Engineer" action in the Engineers panel. Prompts for name + provider/command (default: the same Claude Code command the default engineer uses today). Spawns a persistent agent with `kind='engineer'`, unique `LOOM_ENGINEER_ID` env var, and the engineer MCP config pointing at `mcp_engineer.py`.
- **MCP session binding**: engineer MCP server runs through a local stdio entrypoint (`mcp_engineer.py`), reads `LOOM_ENGINEER_ID` on startup, rejects tool calls if the env var is missing or the referenced engineer has been deleted, and scopes every read/write by that id.
- Split the tool implementation: the existing single-surface module splits into a shared core (ownership-filtered CRUD) and per-kind entrypoints. `weaver_*` names stay as aliases routed to a deterministic "default engineer" — the one named `"Weaver"` if present, else the first engineer by creation order. When the alias is called from a bound legacy Weaver or engineer session, it stays bound to that session instead of jumping to the global default. If zero engineers exist, the alias returns a clear error telling the user to create one.
- Enforce ownership scoping on all `engineer_*` reads and writes. Writes that create agents or tasks auto-stamp `owner_engineer_id` / `assigned_engineer_id` to the caller, and engineer task access is group-scoped.
- CLI: `loom task create` from a terminal remains unassigned by default (`assigned_engineer_id=''`). Add `--engineer <slug>` for explicit assignment, extend `loom task edit` with the same reassignment flag, and add offline-capable `loom engineer list`.
- Keybindings: `Cmd+Option+A` continues to add a worker (default); new `Cmd+Option+E` adds an engineer. Document in settings.
- UI:
  - Rename "Weaver" panel → "Engineers" panel, list all engineers with status/rename/delete controls.
  - Agent list re-sorted hierarchically: user-owned content → engineers (creation order) → each engineer's workers indented underneath.
  - Hierarchical sort composes with group filter: when a group filter is active, sort stays hierarchical within the filtered set.
  - Relaunch button on engineer cells (uses existing relaunch path, `persistent=1` row survives).
  - Delete engineer: confirmation modal warns about transfer-to-user, then runs the orphan transfer.

**Acceptance criterion**: create two engineers (Alice, Bob). Dispatch a worker from each. Run an `engineer_agents_list` call as Alice → sees only Alice's worker, not Bob's. Delete Alice → her worker transfers to the user (visible in UI with no engineer parent). Existing `weaver_*` external clients continue to work against the default engineer.

### Stage 4 — Architect kind + decision log + hire flow + journal ✓ shipped

**Deliverable**: the user can spawn an Architect that creates tasks, assigns them to engineers, writes typed decisions, and can request hiring new engineers via a user-approved flow.

- **Architect launch infra**: new "Add Architect" action in the Architects panel. Same shape as engineer launch but with the architect MCP config (`mcp_architect.py`) and architect boot prompt. Persistent=1.
- **Architect MCP surface** (`architect_*`): task create/reassign (scoped to tasks it created), engineer list/message/reply/hire, decision CRUD, journal, board summary (scoped to tasks it created + engineers it hired). No dispatch, no worker messaging.
- **Engineer ↔ architect messaging** (both surfaces): `architect_engineer_message`, `architect_reply`, `engineer_message_architect`, `engineer_reply`. Messages surface on both agents' cells.
- **Engineer boot prompt update**: explicit instruction to escalate non-trivial product/scope decisions to the architect via `engineer_message_architect`.
- **Pending hire queue**: new `pending_hires` table (`id`, `architect_id`, `requested_name`, `requested_command`, `requested_provider`, `status ∈ {pending,approved,rejected}`, `created_at`). User sees pending hires in the Architects panel with approve/reject buttons. On approve → engineer created with `hired_by_architect_id` set; on reject → architect receives a tool-call error with the user's reason (if any) and journals it.
- **Reassignment rule**: an architect can only reassign tasks it created. Tasks created by users or by other architects are read-only from this architect's surface. (Documented; can relax later.)
- **Decision delta ops**: add `decision_upsert` / `decision_remove` to the WS delta protocol (`static/js/ws.js`) so the UI stays live.
- UI:
  - Architects panel (parallel to Engineers).
  - Decision log viewer: list grouped by status, with filter by architect, click-through to linked tasks/engineers.
  - Hire approval banner/modal.
  - Sort: architects → engineers under each architect → workers under each engineer.

**Acceptance criterion**: launch an architect → it writes a decision (`status=proposed`) → creates a task with `suggested_action='feature/foo'` and `assigned_engineer_id=Alice.id` → Alice sees the task and the suggested action; architect requests hire → user approves → new engineer appears and is tagged as hired by this architect; architect updates the decision to `accepted` → change appears live in the UI via delta. Restart daemon → decision log + journal + pending-hire history persist.

### Stage 5 — Branch namespacing + communication graph enforcement + end-to-end acceptance ✓ shipped

**Deliverable**: worktree branches are engineer-namespaced, the communication graph is fully enforced (no stray tools on the wrong surfaces), and we run a full acceptance pass.

- Change new worktree branch scheme to `loom/<engineer-slug>/<worker-slug>-<shortid>` (or `loom/user/...` for user-owned workers). Existing branches are grandfathered.
- Audit every MCP tool registration to confirm: architects cannot message workers, engineers cannot message workers owned by other engineers, workers have no cross-agent messaging tools. Remove or gate anything that violates this.
- Remove the "hints" in code comments / docs that still reference the legacy single-engineer model.
- **Full acceptance test**: architect → hires engineer (with approval) → creates task → engineer dispatches worker → worker runs, reports progress, derives a sub-task (inherits `assigned_engineer_id`) → worker asks a clarifying question → engineer escalates to architect → architect writes a decision and updates the task → engineer re-dispatches → worker completes → merge back. All communication flows through the permitted paths; no scope leakage.

**Acceptance criterion**: the end-to-end scenario above runs green. `loom doctor` still reports zero drift. No `weaver_*` alias call makes it to an unintended surface.

### Stage 6 — Cleanup & compatibility sunset

**Deliverable**: legacy names and columns are removed; upgrade path is explicit.

- **Upgrade guard**: on boot, if the database has legacy columns populated but `kind` is empty on any row, refuse to start and print an actionable error: "this version requires a prior upgrade; install version X first, run once, then upgrade". Prevents skipping stage 1.
- Drop columns: `template`, `weaver_owner_id`, `created_by_weaver_id`. Use the SQLite 14-step table-rebuild pattern so WAL doesn't get confused.
- Remove `weaver_*` MCP tool aliases and the default-engineer routing layer.
- Remove legacy `~/.loom/agents/` compat read; roles live in `~/.loom/roles/` only.
- Bump major version.

**Acceptance criterion**: fresh install + upgrade from stage-5 db succeed; attempt to upgrade from pre-stage-1 db is refused with a clear message. `loom doctor` reports clean state with no legacy-column references.

## 10. Risks & mitigations

- **MCP surface churn**: external agents relying on `weaver_*` tool names will break. → Keep aliases through stages 3–5; document the rename; one-release deprecation window.
- **Decision map scope creep**: tempting to build a full PM tool. → Constrain to typed log + links for v1; defer graph UI to v2.
- **Hire approval loop UX**: every hire requires user approval in v1. → Accepted friction for now; revisit if it proves disruptive. Deferred: "pre-approved" mode (user checkbox to trust an architect to hire without confirmation).
- **Ordering under groups**: we're effectively subordinating groups to hierarchy. Users who relied on groups for organization may dislike. → Keep groups visible as a secondary filter; allow switching between hierarchy view and group view.
- **Engineer visibility edge cases** (user-created workers, manual cell creation): → explicit rule — anything not owned by an engineer is owned by the user; user sees all, engineers don't see user-owned workers by default. Architect can reassign.
- **Migration of in-flight work**: users upgrading mid-session need existing tasks/agents to keep working. → Stage 1 is pure additive + backfill + dual-write; zero behavior change, so upgrades are safe even with live sessions.
- **Dual-write drift between old and new columns** (stages 1–5): a future code path might update only one side. → All writes go through a thin wrapper layer in `db.py` that writes both columns. `loom doctor` is run in CI and reports any drift, catching regressions.
- **Weaver identification ambiguity**: if migration can't uniquely identify the existing Weaver (e.g. no group match, no tool-surface registration history), backfill would silently misclassify. → Migration refuses to proceed and prints a diagnostic asking the user to confirm which agent was the stage-1 migrated engineer, falling back to an interactive prompt or a CLI flag (`loom migrate --weaver-id <id>`).
- **MCP session binding spoofing**: a compromised env var could let an engineer session claim another engineer's id. → Not a hardening goal for v1 (local trust model), but documented; future could sign session tokens.
- **Architect task-reassign authority**: allowing any architect to reassign any task creates unclear ownership. → V1: architect can only reassign tasks it created. Revisit if workflow demands broader reassignment.
- **Version skip during cleanup** (stage 6): users upgrading directly from pre-stage-1 to post-stage-6 would lose data. → Upgrade guard refuses to boot with a clear "install intermediate version first" error.
- **Worktree orphans on engineer delete**: when an engineer is deleted and its workers transfer to the user, worktree branches still exist under the old `loom/<engineer-slug>/...` prefix. → Keep the branches as-is (don't rename — breaks git refs); document that branch names reflect creation-time ownership, not current.
- **Pending-hire table growth**: approved/rejected hires pile up. → Auto-archive entries older than 30 days; expose a "clear history" action in the Architects panel.

## 11. Testing plan per stage

Each stage lands with:

- Unit tests for the new data model / scoping queries.
- Integration test: spawn fresh daemon → migrate old `loom.db` from fixture → verify backfill and tool surface behave correctly.
- Manual smoke: UI hierarchy renders; dispatch prompt includes role preamble; engineer scoping doesn't leak; architect hire flow requires approval; decision log persists.

## 12. Cross-cutting implementation notes

These apply across multiple stages; listed here once to avoid repetition.

- **Agent launch paths**: engineers and architects are launched through the shared helpers in `server_agent.py`, which stamp the right env vars (`LOOM_ENGINEER_ID` or `LOOM_ARCHITECT_ID`), choose the MCP entrypoint, and install the per-provider MCP config. Engineers use the local stdio `mcp_engineer.py` entrypoint so session binding is validated before proxying tool calls to the daemon.
- **MCP tool registration**: add `mcp_engineer.py` and `mcp_architect.py` as thin entrypoints that import from a shared implementation module. Each entrypoint registers only the tools permitted for its kind. The existing `mcp_weaver.py` stays as a compat shim that delegates to `mcp_engineer.py` via the default-engineer alias.
- **Delta protocol additions**: `static/js/ws.js` must learn new op types — `decision_upsert`, `decision_remove`, `pending_hire_upsert`, `pending_hire_resolve`. Server-side `_emit()` helpers mirror the existing pattern.
- **CLI ownership behavior**: user-originated `loom task create` commands default to `assigned_engineer_id=''` (unassigned). `loom task create` and `loom task edit` accept `--engineer <slug>` for explicit assignment / reassignment. Unassigned tasks are visible to all engineers within their own group but scoped out of `engineer_board_summary` until assigned.
- **Keybindings**: `Cmd+Option+A` → add worker (existing), `Cmd+Option+E` → add engineer (stage 3), `Cmd+Option+P` → add architect (stage 4). All user-configurable via the existing keybindings settings.
- **Backup on migration**: the stage-1 migration writes `loom.db.pre-kinds.bak` before altering the schema. Idempotent — skipped on second run.
- **Boot-prompt management**: engineer and architect boot prompts live in `loom/prompts/engineer_boot.md` and `loom/prompts/architect_boot.md`, loaded at launch. Not user-editable in v1 but easy to find for iteration.
- **Session persistence**: engineers and architects get `session_resume=True` by default (persistent kinds benefit most from resume). `reconnect_orphans` in the adapter needs no change — it already handles persistent agents.
- **Dispatch context** (`loom` Jinja namespace): stage 2 adds `loom.agent.kind` / `loom.agent.role` / `loom.agent.owner_engineer`. Stage 4 adds `loom.agent.hired_by_architect` for workers whose engineer was hired by an architect.
- **`loom doctor` command**: landed in stage 1 and extended in every subsequent stage — each stage adds checks for the new invariants it introduces. Runs in CI on a fixture db so regressions are caught automatically.

## 13. Resolved decisions

1. **Stage 1 backfill name**: auto-migrate to an engineer named `"Weaver"`. User can rename afterwards.
2. **Architect ↔ engineer message surface**: land `engineer_reply` and `engineer_message_architect` in v1. The engineer boot prompt explicitly tells engineers to escalate non-trivial product or scope decisions to the architect.
3. **Role preamble placement**: always injected by default; actions may opt out via `disable_role_preamble: true` for edge cases like one-shot diagnostics.
4. **"Pre-approved hiring"**: deferred. V1 requires user approval on every hire; revisit after observing real usage.
