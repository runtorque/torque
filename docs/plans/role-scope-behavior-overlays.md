# Role-scope behavior overlays — design spec + phased plan

**Task:** TORQUE:761 — Role-scope behavior overlays layered with per-agent overlays  
**Status:** Design/spec only; no implementation performed.  
**Review gate:** **Human/Torqly approval required before build** because this extends persisted schema, prompt composition, governance/routing, MCP/CLI/API contracts, and user-facing approval flows.

---

## 1. Executive summary

Dynamic Behavior Phase 1 shipped a governed, versioned, per-agent prompt overlay for persistent Architects and Engineers. This spec extends that architecture with **role-scope overlays**: a shared behavior overlay per agent kind — `architect`, `engineer`, and `worker` — layered with the existing per-individual overlays.

The effective model becomes:

```text
base Torque/system/action prompt (immutable, authoritative)
+ role overlay for this agent kind (shared defaults)
+ agent overlay for this individual agent, where supported
```

Scope support in this phase:

- **Architects:** role overlay + existing per-agent overlay.
- **Engineers:** role overlay + existing per-agent overlay.
- **Workers:** role overlay only. Workers still get **no per-agent overlay** because individual workers are ephemeral; the durable customization point is the shared worker-role behavior.

Governance is intentionally stricter for role overlays than per-agent overlays:

- **Role overlays are architect-authored and user-approved for all three kinds.**
- The existing `engineer_behavior_requires_user_approval` group setting continues to apply only to **per-agent Engineer overlays**.
- Role overlays always route to user diff approval, regardless of that setting.
- Engineers do **not** get a formal “propose engineer-role overlay” MCP path in v1. They can still feed convergence findings informally through Architect/User conversations, exactly as happened in this session.

Rollout default is empty/no-op. The four converged Engineer defaults from this session become the **first governed Engineer-role proposal after the tier ships**, not hardcoded migration or prompt-builder content.

### Key recommendation: group-scoped role overlays

I recommend making role overlays **group-scoped per kind**, not workspace-global, even though the minimum storage address is `scope_kind + scope_key`.

Reasoning:

- Existing role behavior, settings, Architect authority, Engineers, and Workers are all group-centered.
- The UI naturally belongs in Group Settings → per-kind Behavior panes.
- A workspace-global Engineer role overlay would allow an Architect in one group to propose behavior affecting unrelated groups, which is a surprising blast radius even with user approval.

Concretely, a role overlay address should be:

```text
scope_kind = "role"
scope_group = <group name>
scope_key  = "architect" | "engineer" | "worker"
```

Agent overlays remain:

```text
scope_kind = "agent"
scope_group = ""        # group is derived from the target agent when needed
scope_key  = <agent_id>
```

If Torqly/user intended exactly one workspace-global overlay per kind, the same design can collapse `scope_group` to `""` for role rows. That product choice should be resolved before implementation; this spec proceeds with the group-scoped recommendation because it matches the confirmed UI/governance shape.

---

## 2. Research summary: current Phase 1 surface

Authoritative current code verified in this worktree:

### Helpers

- `torque/behavior_overlay.py`
  - `BEHAVIOR_OVERLAY_MAX_BYTES = 4 * 1024`
  - `validate_overlay_text()` rejects oversized text; it never truncates.
  - `render_behavior_overlay_block()` renders a fenced, subordinate block and fails closed to empty text on corrupt oversized persisted content.
  - `behavior_overlay_diff()` uses `difflib.unified_diff`.
  - `version_summary()` / `proposal_summary()` intentionally omit full text for normal snapshots/deltas.

### Persistence

- `torque/db_schema.py`, `torque/db.py`
  - Tables: `behavior_overlay_versions`, `behavior_overlay_active`, `behavior_overlay_proposals`, `behavior_overlay_activations`.
  - They are currently keyed by `agent_id`; `behavior_overlay_active.agent_id` is the primary key.
  - The current extension point is to generalize each table from `agent_id` to a composite scope address.

### State/governance

- `torque/state.py`
  - `ensure_behavior_overlay_seed(agent_id)` creates an explicit empty floor version and active pointer.
  - `_behavior_overlay_route(target, author_kind)` routes Architect overlays to user approval and Engineer overlays to Architect or Architect→User depending on `engineer_behavior_requires_user_approval`.
  - Workers are currently rejected with “worker behavior overlays are not supported in v1”.
  - User approval tasks use label `behavior-overlay-approval` plus `proposal:<id>`.

### Prompt composition

- `torque/engineer.py`
  - `build_engineer_system_prompt(..., behavior_overlay_block="")` appends one overlay block when supplied.
- `torque/architect.py`
  - `build_architect_system_prompt(..., behavior_overlay_block="")` appends one overlay block when supplied.
- `torque/server.py`
  - `_behavior_overlay_prompt_block_for_cell()` resolves agent overlays for Architects/Engineers only.
  - `_build_cell_persistent_prompt()` and `_architect_persistent_prompt_text()` pass the per-agent overlay into the prompt builders.
  - `_assemble_worker_prompt()` currently assembles worker dispatch prompts with identity anchor, role preamble, task/action prompt, and postscript; workers currently receive no Dynamic Behavior overlay.
- `torque/server_agent.py`
  - `AgentLaunchService.apply_persistent_prompt()` has a fallback that appends an agent overlay to Architect/Engineer prompts if the marker is missing. This must be updated carefully when multiple overlay blocks exist.

### MCP/API/CLI/UI

- `torque/mcp_engineer.py`, `torque/mcp_architect.py`, `torque/mcp_tools_shared.py`, `torque/mcp_retry.py`
  - Engineer and Architect behavior-overlay tools exist for per-agent read/list/diff/propose/approve/reject/rollback flows.
- `torque/server.py`
  - HTTP/WS commands include `behavior_overlay_read`, `versions`, `proposals`, `diff`, `propose`, `architect_approve`, `user_approve`, `user_reject`, and `user_rollback`.
- `bin/torque`
  - `torque behavior read|versions|proposals|diff|approve|reject|rollback|setting` reads SQLite directly for offline read paths and calls daemon APIs for writes.
- `static/js/behavior_overlay.js`, `static/js/agent_panel.js`, `webview.html`, `static/style.css`
  - Phase 2 UI added per-agent Behavior tabs, version timelines, proposal cards, and a required diff approval modal.
  - Current frontend caches are keyed by `agent_id`; role scope requires a generalized `scope_id` key.

---

## 3. Product semantics

### 3.1 Scope tiers

| Agent kind | Role overlay | Per-agent overlay | Notes |
| --- | --- | --- | --- |
| `architect` | Yes | Yes | Role overlay covers group-wide Architect defaults; per-agent overlay covers one Architect. |
| `engineer` | Yes | Yes | Role overlay covers group-wide Engineer defaults; per-agent behavior remains Phase 1. |
| `worker` | Yes | No | Role overlay is the durable shared Worker behavior home. Per-worker overlays remain unsupported. |

### 3.2 Default empty rollout

- Migration must not inject behavioral text.
- Missing role overlays mean “empty/no-op role overlay”.
- Empty seed versions are created lazily on first read/proposal for a scope, not eagerly for every group/kind during migration.
- Prompt rendering should avoid prompt bloat from empty role overlays. Recommendation: **do not inject an empty role block into prompts**; only non-empty active role text renders. Keep the explicit empty seed in storage once the scope is touched so diffs and rollback have a floor.
- Existing Phase 1 per-agent empty-block behavior can remain unchanged for compatibility, but new role rendering should not add three empty fenced blocks to every prompt on rollout.

### 3.3 Apply boundary

- Architect/Engineer role and agent overlays apply to their persistent prompt on next launch/relaunch/session boot. They do not mutate a currently running session.
- Worker role overlays apply at the next task dispatch prompt for that worker. Existing in-flight dispatch prompts are not edited.
- Prompt previews should show the relevant active role overlay so the operator can inspect the next-launch/next-dispatch behavior.

---

## 4. Composition and precedence

### 4.1 Ordering

For Architects and Engineers:

```text
1. Immutable base prompt assembled by Torque
   - Torque Agent preamble where applicable
   - Architect/Engineer base system prompt
   - group settings, custom instructions, specializations, owner guidance
2. Role Dynamic Behavior overlay for (group, kind)
3. Agent Dynamic Behavior overlay for this agent_id
```

For Workers, the runtime has both persistent launch prompt and per-task dispatch prompt. The role overlay should be inserted into the **dispatch prompt** so newly approved role defaults reach reused workers on their next task:

```text
1. Worker identity anchor / existing role preamble
2. Worker-role Dynamic Behavior overlay for (group, worker)
3. Task/action prompt body
4. Torque completion postscript / deliverable contract
```

This placement keeps task/action instructions and mandatory completion guidance closer to the worker than the shared role overlay. The overlay fence must explicitly say it is subordinate to earlier system/Torque/tool/governance instructions **and to task/action/completion instructions for the current dispatch**.

### 4.2 Fencing

Extend `render_behavior_overlay_block()` to understand scope metadata. Each rendered layer remains a separate fenced block; do not merge text bodies.

Recommended rendered titles:

```text
## Dynamic Behavior Overlay (role-scoped: engineer, approved)
<!-- torque:behavior-overlay scope_kind="role" scope_group="Torque" scope_key="engineer" version_id="..." sha256="..." -->
...
<!-- /torque:behavior-overlay -->

## Dynamic Behavior Overlay (agent-scoped, approved)
<!-- torque:behavior-overlay scope_kind="agent" scope_key="f944dd2c" agent_id="f944dd2c" version_id="..." sha256="..." -->
...
<!-- /torque:behavior-overlay -->
```

The block body should keep Phase 1’s subordination statement, updated for scope:

- The overlay is additive and subordinate.
- It may refine style, working habits, and task-handling preferences only.
- If it conflicts with Torque/system/safety/tool/MCP/governance/base instructions, ignore the overlay.
- For worker dispatch prompts, if it conflicts with the task/action prompt or completion contract, follow the task/action/completion instructions.

### 4.3 Conflict/precedence semantics

- **Base always wins.** Overlays cannot edit or override system, MCP, safety, governance, action, or deliverable requirements.
- **Role and agent overlays are additive.** They are not merged structurally and do not delete each other’s text.
- **Agent overlay is more specific and later.** If an Engineer/Architect agent overlay refines a role overlay, the agent overlay should be followed only to the extent both remain subordinate to base/Torque requirements.
- **Rollback is per scope.** Rolling back an agent overlay does not change the role overlay. Rolling back a role overlay affects all future effective prompts for that group/kind.
- **No truncation.** Overbudget layers are rejected at proposal time and omitted/fail-closed at render time if corrupted later.

### 4.4 Size budget recommendation

Phase 1 used a 4 KiB cap for one per-agent overlay. Role overlays should not become a second full 4 KiB prompt expansion.

Recommended constants:

```text
Agent overlay body cap:       4096 bytes  (unchanged from Phase 1)
Role overlay body cap:        2048 bytes  (tight shared-default cap)
Effective combined body cap:  6144 bytes  (role + agent maximum)
```

Rationale:

- 2 KiB is enough for a handful of high-signal shared defaults, including the four converged Engineer defaults, without encouraging role overlays to become a parallel system prompt.
- Keeping the per-agent cap at 4 KiB preserves Phase 1 compatibility and avoids invalidating existing approved agent overlays.
- A 6 KiB combined cap is still materially bounded and equals the maximum valid role+agent body sum; it catches future cap drift or corrupted persisted rows without surprising current users.

Fail-closed behavior:

1. Validate proposed text against the per-scope cap before saving proposals.
2. Validate persisted text at render time. If one layer is oversized/corrupt, omit that layer’s behavior text; never truncate.
3. Compose role then agent. If the combined body bytes still exceed the combined cap, omit less-specific layers first:
   - Drop role overlay, keep valid agent overlay if it fits.
   - If the agent overlay alone exceeds its cap or the combined cap, drop it too.
4. Log enough scope/version metadata for debugging, but do not inject warning text into agent prompts.

This preserves the most-specific customization where safe and prevents role-overlay corruption from disabling an otherwise valid per-agent overlay.

---

## 5. Governance model and state machine deltas

### 5.1 Locked governance decision for role overlays

Role overlays are always:

```text
Architect-authored -> User-approved with rendered diff -> Applied
```

This applies to all role kinds:

- `architect` role
- `engineer` role
- `worker` role

Role proposals are always user-governed regardless of:

- authoring Architect
- target role kind
- group setting `engineer_behavior_requires_user_approval`

### 5.2 Why Architect-authored-only for v1

Do **not** add an Engineer “propose Engineer-role overlay” tool in v1.

Rationale:

- Role overlays have group-wide blast radius, so the formal write path should stay narrow.
- This session’s convergence process already showed an effective informal feed-in path: Engineers/Workers surface repeated lessons through normal status, review, and Architect/User conversation; the Architect curates them into a role proposal; the user approves the diff.
- Avoiding formal Engineer-role proposals keeps MCP schemas, scoping, approval queues, and UI simpler for the first role-scope release.
- The future option remains clear if informal feed-in proves insufficient.

Future revisit trigger:

- Add an Engineer→Architect→User role proposal path only if repeated shared Engineer defaults are being lost, delayed, or retyped because there is no formal intake queue.

### 5.3 Per-agent governance remains Phase 1

Per-agent Architect overlay:

```text
Architect proposes own agent overlay -> User approves -> Applied
```

Per-agent Engineer overlay:

```text
Engineer proposes own overlay -> Architect approves -> Applied
```

or, when `engineer_behavior_requires_user_approval=true`:

```text
Engineer proposes -> Architect approves -> User approves -> Applied
```

Architect-authored per-agent Engineer edits keep Phase 1 behavior:

- auto-apply when the group setting is false;
- Architect-endorsed then user-approved when the setting is true.

Workers still have no per-agent overlay route.

### 5.4 Route computation delta

Introduce a scope-aware route function, conceptually:

```text
_behavior_overlay_route(scope, author_kind, target_agent_or_group)
```

Rules:

1. If `scope_kind == "role"`:
   - require `author_kind == "architect"` for proposal creation;
   - require active, non-dismissed Architect in `scope_group`;
   - route = `user`;
   - `requires_user_approval = true`;
   - `next_actor_kind = "user"`.
2. If `scope_kind == "agent"`:
   - preserve Phase 1 route behavior.
3. If `scope_kind == "agent"` and target kind is `worker`:
   - reject: workers do not support per-agent overlays.
4. If `author_kind == "user"` for rollback/administrative flows:
   - user may directly create/apply rollback for any visible scope through CLI/UI commands.

### 5.5 Proposal state machine

Keep existing statuses: `proposed`, `approved`, `rejected`, `applied`.

Role proposal states:

```text
proposed / next_actor_kind=user / approval_route=user
  -> user approve -> applied
  -> user reject  -> rejected
```

There is no `approved` intermediate state for role proposals because Architect endorsement is implicit in authorship.

Architect withdrawal/cancel behavior:

- Optional but recommended: allow the authoring Architect to reject/withdraw its own pending role proposal before user action.
- Persist as `rejected` with `resolved_by_kind="architect"` and a note such as “withdrawn by author”.
- User approval task should be resolved to Done with rejection/withdrawal status.

### 5.6 User approval tasks

Role user-approval Backlog tasks should reuse the existing `behavior-overlay-approval` label and add scope-specific labels:

```text
behavior-overlay-approval
proposal:bop-...
scope:role
role:engineer
group:Torque
```

Task description should include:

- Proposal id
- Scope label: e.g. `Engineer role overlay for group Torque`
- Author Architect
- Approval route: `user`
- Rationale
- CLI hint using scope-aware `torque behavior diff --proposal <id>` and `torque behavior approve|reject <id>`

The existing approval modal can render the same proposal diff payload if it receives scope metadata.

---

## 6. Data model and migration

### 6.1 Scope address

Add a canonical behavior-overlay scope address:

```text
scope_kind  TEXT NOT NULL  # "agent" | "role"
scope_group TEXT NOT NULL DEFAULT ''
scope_key   TEXT NOT NULL  # agent_id for agent scope; kind for role scope
```

Add a computed/transient `scope_id` in Python/JS payloads:

```text
agent:<agent_id>
role:<group>:<kind>
```

For workspace-global role overlays, `scope_group` can be `""` and `scope_id` can become `role::<kind>`; this spec recommends group-scoped role rows.

Keep `agent_id` as a compatibility/denormalized column in v2 tables for agent rows:

- For agent scope: `agent_id == scope_key`.
- For role scope: `agent_id == ''`.

This makes old row decoding/backfill simpler and helps transitional CLI/UI code, while all new uniqueness/lookups use the scope columns.

### 6.2 Final table shapes

#### `behavior_overlay_versions`

Add:

- `scope_kind TEXT NOT NULL DEFAULT 'agent'`
- `scope_group TEXT NOT NULL DEFAULT ''`
- `scope_key TEXT NOT NULL DEFAULT ''`

Keep existing fields, including `agent_id` as compatibility metadata.

Change uniqueness/indexes:

- Drop unique `(agent_id, version_number)`.
- Add unique `(scope_kind, scope_group, scope_key, version_number)`.
- Add index `(scope_kind, scope_group, scope_key, version_number DESC)`.
- Keep index on `source_proposal_id`.

Recommended extra metadata:

- Store `metadata.scope_label` and `metadata.max_bytes` for audit/debug.

#### `behavior_overlay_active`

Repoint primary key from `agent_id` to the composite scope key.

Final columns:

- `scope_kind TEXT NOT NULL DEFAULT 'agent'`
- `scope_group TEXT NOT NULL DEFAULT ''`
- `scope_key TEXT NOT NULL`
- `agent_id TEXT NOT NULL DEFAULT ''` — compatibility only
- `active_version_id TEXT NOT NULL`
- `updated_at REAL NOT NULL`
- `updated_by_kind TEXT NOT NULL DEFAULT ''`
- `updated_by_id TEXT NOT NULL DEFAULT ''`
- `reason TEXT NOT NULL DEFAULT ''`
- `PRIMARY KEY(scope_kind, scope_group, scope_key)`

This is the migration’s highest-risk schema change because SQLite cannot alter a primary key in place.

#### `behavior_overlay_proposals`

Add:

- `scope_kind TEXT NOT NULL DEFAULT 'agent'`
- `scope_group TEXT NOT NULL DEFAULT ''`
- `scope_key TEXT NOT NULL DEFAULT ''`

Keep `agent_id` for compatibility; set it to `''` for role proposals.

Current `target_kind` should remain:

- Agent scope: captured target agent kind (`architect` or `engineer`).
- Role scope: role kind (`architect`, `engineer`, or `worker`).

Indexes:

- Replace `(agent_id, status, created_at DESC)` with `(scope_kind, scope_group, scope_key, status, created_at DESC)`.
- Keep route, next actor, user task indexes.
- Replace idempotency uniqueness with `(proposed_by_agent_id, scope_kind, scope_group, scope_key, idempotency_key)` where key is non-empty. This prevents accidental collisions when the same Architect submits different role proposals with the same idempotency key shape.

#### `behavior_overlay_activations`

Add:

- `scope_kind TEXT NOT NULL DEFAULT 'agent'`
- `scope_group TEXT NOT NULL DEFAULT ''`
- `scope_key TEXT NOT NULL DEFAULT ''`

Keep `agent_id` as compatibility metadata.

Indexes:

- Replace activation agent index with `(scope_kind, scope_group, scope_key, created_at DESC)`.
- Keep `proposal_id` index.

### 6.3 DB API shape

Add a small scope helper/dataclass in `torque/behavior_overlay.py` or `torque/state.py`, for example:

```text
BehaviorOverlayScope(kind, key, group='')
```

Responsibilities:

- normalize `scope_kind`, `scope_key`, `scope_group`;
- validate role kind;
- derive legacy `agent_id` value;
- produce `scope_id` for payload/cache keys;
- produce human labels.

Then generalize DB methods:

- `load_behavior_overlay_active(scope)`
- `load_behavior_overlay_active_version(scope)`
- `list_behavior_overlay_versions(scope, limit=50)`
- `next_behavior_overlay_version_number(scope)`
- `save_behavior_overlay_version(row)` requiring scope fields
- `save_behavior_overlay_active(row)` requiring scope fields
- `list_behavior_overlay_proposals(scope=None, status_filter='', limit=...)`

Keep compatibility wrappers for existing per-agent callers during migration:

- `load_behavior_overlay_active_for_agent(agent_id)` or existing method accepting `agent_id` and internally building `agent` scope.
- Existing call sites can be converted incrementally within the same implementation slice, but external API payloads should expose scope fields immediately.

### 6.4 Idempotent migration plan

Because Phase 1 just shipped, most real databases may have little or no overlay data. Still implement a full idempotent migration per Torque persistence invariants.

Migration steps:

1. Detect whether v2 scope columns already exist using `PRAGMA table_info`.
2. If a table is already v2, leave it alone except to ensure indexes exist.
3. For old v1 tables, run a transaction with `PRAGMA foreign_keys=off`.
4. Create v2 temp tables with final schema.
5. Copy old rows with backfill:

   ```text
   scope_kind  = 'agent'
   scope_group = ''
   scope_key   = old.agent_id
   agent_id    = old.agent_id
   ```

6. Drop old indexes/tables.
7. Rename v2 temp tables to the original names.
8. Recreate v2 indexes.
9. Commit.
10. Run the migration a second time in tests to prove idempotency.

Specific `behavior_overlay_active` handling:

- Old primary key: `agent_id`.
- New primary key: `(scope_kind, scope_group, scope_key)`.
- Since SQLite cannot alter a primary key, this table must be rebuilt.
- Backfilled agent active rows must preserve `active_version_id`, `updated_at`, `updated_by_kind`, `updated_by_id`, and `reason` exactly.

### 6.5 Snapshot/delta payloads

Current frontend caches use `agent_id`. Generalize to `scope_id` while retaining `agent_id` on agent rows for compatibility.

Recommended summary payload fields:

```json
{
  "scope_kind": "role",
  "scope_group": "Torque",
  "scope_key": "engineer",
  "scope_id": "role:Torque:engineer",
  "agent_id": "",
  "target_kind": "engineer"
}
```

Delta ops remain semantically similar:

- `behavior_overlay_version_append`
- `behavior_overlay_active_update`
- `behavior_overlay_proposal_upsert`
- `behavior_overlay_proposal_resolve`

but must include scope fields. Frontend invalidation should refresh only:

- the focused per-agent Behavior tab if the affected scope is that agent or the focused agent’s role scope;
- the Group Settings role Behavior pane if open for the affected group/kind;
- the approval modal if it references the proposal.

No full overlay text should be sent in routine snapshots/deltas.

---

## 7. Prompt integration plan

### 7.1 New rendering API

Add a stack renderer, conceptually:

```text
render_behavior_overlay_stack_for_cell(state, cell, include_agent=True, include_role=True)
```

It should:

1. Resolve role scope `(cell.group, cell.kind)` for all three kinds.
2. Resolve agent scope for Architects/Engineers only.
3. Load active versions without forcing non-empty prompt text.
4. Validate per-layer caps and combined cap.
5. Render role block first, agent block second.
6. Omit empty role blocks from prompts.
7. Preserve fail-closed behavior with no truncation.

### 7.2 Architect/Engineer persistent prompts

Update:

- `torque/server.py::_behavior_overlay_prompt_block_for_cell()` or replace it with a stack-aware helper.
- `torque/server.py::_build_cell_persistent_prompt()`
- `torque/server.py::_architect_persistent_prompt_text()`
- `torque/engineer.py::build_engineer_system_prompt()`
- `torque/architect.py::build_architect_system_prompt()`

The prompt builders can continue accepting a single `behavior_overlay_block` string, but that string now contains a stack of 0–2 fenced blocks.

### 7.3 Worker dispatch prompts

Update:

- `torque/server.py::_assemble_worker_prompt()` to accept `behavior_overlay_block`.
- Worker dispatch call sites around task dispatch and prompt preview.

Recommended final worker dispatch shape:

```text
identity anchor

role YAML preamble/priorities (existing)

Dynamic Behavior role overlay (worker, if non-empty)

task/action prompt

Torque completion postscript
```

Do **not** add a worker role overlay to both persistent prompt and dispatch prompt in v1, or workers will see duplicate guidance. Dispatch insertion is preferred because it reaches reused workers at the next task boundary.

### 7.4 Prompt preview

Update settings/system prompt preview endpoints to make role overlays visible:

- Group Settings → Engineer prompt preview: show active Engineer-role overlay, not any per-agent overlay.
- Group Settings → Architect prompt preview: show active Architect-role overlay, not any per-agent overlay.
- Worker prompt preview: show active Worker-role overlay in the dispatch preview.
- Per-agent preview, if/when present, should show both role and agent layers in order.

### 7.5 `server_agent.apply_persistent_prompt()` fallback gotcha

Current fallback checks for `BEHAVIOR_OVERLAY_START_MARKER` and appends a per-agent overlay only if no marker exists. With multiple layers, that check can become wrong: a prompt with a role marker but missing the agent marker would skip the agent overlay.

Implementation should either:

- remove the fallback and make all supported prompt builders responsible for complete stack composition; or
- make the fallback stack-aware and idempotent by checking scope-specific markers.

Recommendation: make the primary builders complete and change fallback to call the same stack renderer only when no overlay stack is present at all. Avoid ad hoc marker-count logic.

---

## 8. Surface deltas

### 8.1 HTTP/WS commands

Generalize existing behavior commands to accept scope:

- `scope_kind`: `agent` or `role`
- `agent_id`: compatibility shorthand for `scope_kind=agent`
- `role_kind`: `architect|engineer|worker` shorthand for `scope_kind=role, scope_key=<kind>`
- `group`: required for role scope under the group-scoped recommendation

Commands to update:

- `behavior_overlay_read`
- `behavior_overlay_versions`
- `behavior_overlay_proposals`
- `behavior_overlay_diff`
- `behavior_overlay_propose`
- `behavior_overlay_architect_approve` (agent-scope only in v1)
- `behavior_overlay_architect_reject` (agent and optional role withdrawal)
- `behavior_overlay_user_approve`
- `behavior_overlay_user_reject`
- `behavior_overlay_user_rollback`

Backwards compatibility:

- Existing payloads with only `agent_id` must continue working for per-agent overlays.
- Existing response fields should retain `agent_id` for agent rows, but add scope fields everywhere.

### 8.2 MCP tools

#### Engineer MCP

Keep existing per-agent tools working by default.

Extend read-only tools to expose the inherited Engineer-role overlay:

- `engineer_behavior_overlay_read(scope_kind="agent"|"role"|"effective")`
  - default `agent` for compatibility;
  - `role` reads the caller’s group Engineer-role overlay;
  - `effective` returns summaries/text for role then agent layers.
- `engineer_behavior_overlay_versions(scope_kind="agent"|"role")`
- `engineer_behavior_overlay_diff(scope_kind="agent"|"role", ...)`

Keep write tools agent-only:

- `engineer_behavior_overlay_propose` rejects `scope_kind=role` with a clear v1 message.
- `engineer_behavior_overlay_request_rollback` rejects `scope_kind=role`.

This gives Engineers transparency into shared defaults without adding formal role-proposal machinery.

#### Architect MCP

Add explicit role-write tools to avoid overloading agent ids:

- `architect_behavior_overlay_propose_for_role(role_kind, text, rationale, expected_base_version_id?, idempotency_key?)`
- `architect_behavior_overlay_rollback_role(role_kind, version_id, rationale, expected_base_version_id?, idempotency_key?)`

Extend existing read/list/diff tools:

- `architect_behavior_overlay_read(scope_kind="agent"|"role"|"effective", agent_id?, role_kind?)`
- `architect_behavior_overlay_versions(scope_kind="agent"|"role", agent_id?, role_kind?, limit?)`
- `architect_behavior_overlay_diff(scope_kind="agent"|"role", agent_id?, role_kind?, proposal_id?, from_version_id?, to_version_id?)`
- `architect_behavior_overlay_proposal_list(scope_kind?, agent_id?, role_kind?, status_filter?, limit?)`

Scoping:

- Architect agent scope remains self + hired Engineers only.
- Architect role scope is the caller’s group and any of the three role kinds.
- Dismissed/tombstoned Architects cannot write role proposals.
- Role proposals always route to user; `architect_behavior_overlay_approve` remains only for per-agent Engineer proposals awaiting Architect approval.

Update `torque/mcp_retry.py` idempotent-write classifications for new propose/rollback role tools.

### 8.3 CLI

Keep existing commands for agent scope:

```bash
torque behavior read <agent>
torque behavior versions <agent>
torque behavior rollback <agent> <version_id>
```

Add role-scope flags:

```bash
torque behavior read --role engineer --group Torque
torque behavior versions --role worker --group Torque
torque behavior proposals --role architect --group Torque
torque behavior rollback --role engineer --group Torque <version_id>
```

Existing commands remain:

```bash
torque behavior diff --proposal <proposal_id>
torque behavior diff --from-version <v1> --to-version <v2>
torque behavior approve <proposal_id>
torque behavior reject <proposal_id>
torque behavior setting <group> --requires-user-approval true|false
```

Notes:

- `torque behavior setting` remains the per-agent Engineer governance setting; docs/help text must clarify it does not affect role overlays.
- Offline CLI reads must synthesize scope fields when reading an old/unmigrated v1 DB, and use scope fields after migration.

### 8.4 UI

Recommended placement: **Group Settings → per-kind Behavior panes**.

Add a “Role Dynamic Behavior overlay” section to:

- Worker Behavior pane: edits Worker-role overlay.
- Engineer Behavior pane: edits Engineer-role overlay and keeps the existing `Require user approval for Engineer behavior overlays` checkbox clearly labeled as **per-agent Engineer overlays only**.
- Architect Behavior pane: edits Architect-role overlay.

Each role section should reuse the Phase 2 per-agent behavior UI components:

- active version summary;
- full text loaded on demand;
- editor with rationale;
- draft diff preview;
- proposal cards;
- version timeline and rollback;
- advisory lint display;
- user approval modal with rendered diff.

Per-agent Behavior tab updates:

- Engineers/Architects should show an “Inherited role overlay” read-only summary above the per-agent editor, so the user can understand effective behavior order.
- Per-agent editor/timeline remains scoped to the individual agent.
- Architect “Hired engineer governance” remains per-agent Engineer governance; do not mix it with role overlay authoring.

Worker agent panel:

- Recommendation for v1: do not add a Worker Behavior tab that looks editable per worker.
- If Panelsmith wants visibility, add a read-only “Inherited worker role behavior” affordance only, with a link/cue to Group Settings.

Frontend state model:

- Replace agent-id-only cache keys with `scope_id`.
- Preserve legacy helper paths for agent scopes.
- Delta invalidation must remain narrow per surface-invalidation discipline. Role-scope deltas should not rebuild the entire Engineer/Architect panel unless the focused panel is showing Behavior and the affected role applies to the focused agent.

---

## 9. Engineer-role inaugural seed

The four converged Engineer defaults are **not** migration defaults and are **not** hardcoded in prompt builders.

After the role tier ships, the inaugural content should be created as the first governed proposal:

```text
Scope:       role
Group:       <target group, likely Torque>
Role kind:   engineer
Author:      Architect
Route:       user approval
Base:        empty role seed version
```

Suggested proposal text, compressed to fit the 2 KiB role cap:

```markdown
- Treat idle/stale health hints as advisory. Before messaging or intervening, inspect read-only signals such as activity_detail, progress, diff, and checkpoint deltas. Do not interrupt an agent that is legitimately working, waiting on a subprocess/test, mid-checkpoint, or reviewing on an existing branch. Reserve action for the true DOA signature.
- Set a task's action atomically before dispatch. Never dispatch into an unset or inherited action; this is especially important when auto-dispatch could front-run the intended action update.
- Keep logic, governance, prompt-build, and schema changes behind an independent review boundary. A green unit suite is necessary but not sufficient; value manual or simulation inspection. Use self-review only for mechanically provable verbatim moves.
- Verify before declaring merges done. Do not blind-retry. Independently confirm origin/main is at the expected SHA, content and submodule/gitlink deltas are correct, and PR state matches the claim; diagnose post-success failures with git/gh evidence.
```

Operational seed plan:

1. Backend/UI ships with empty role overlays.
2. Architect opens Group Settings → Engineer → Behavior or uses MCP `architect_behavior_overlay_propose_for_role(role_kind="engineer", ...)`.
3. Architect submits the text above with rationale: “Inaugural shared Engineer defaults converged during the role-overlay design session.”
4. User reviews rendered diff from empty to proposed text.
5. User approves.
6. Future Engineer launches include the Engineer-role overlay before any per-agent Engineer overlay.

---

## 10. Phased build plan

### Phase A — Scope model + migration (backend/orchestration-core)

Files:

- `torque/behavior_overlay.py`
- `torque/db_schema.py`
- `torque/db.py`
- `torque/state.py`
- `tests/test_behavior_overlay.py`
- likely `tests/test_db.py` or a new focused migration test file

Work:

1. Add canonical behavior overlay scope helper (`agent` and `role`).
2. Add role and combined cap constants.
3. Extend summaries to include scope fields and `scope_id`.
4. Rebuild overlay tables to v2 schema with `scope_kind/scope_group/scope_key`.
5. Implement idempotent backfill from old `agent_id` rows.
6. Generalize DB load/list/save methods to accept scope.
7. Keep compatibility wrappers for existing per-agent call sites.
8. Add migration tests, including old-DB round trip and second-run idempotency.

Exit criteria:

- Existing per-agent tests still pass.
- Old Phase 1 DB rows survive migration and are readable through new scope APIs.
- Role active/version/proposal rows can coexist with agent rows.

### Phase B — State/governance/API role support (backend/orchestration-core)

Files:

- `torque/state.py`
- `torque/server.py`
- `torque/mcp_tools_shared.py`
- `torque/mcp_engineer.py`
- `torque/mcp_architect.py`
- `torque/mcp_retry.py`
- `tests/test_behavior_overlay.py`
- `tests/test_mcp_scoping.py` / `tests/test_mcp_tools_shared.py`

Work:

1. Generalize `ensure_behavior_overlay_seed`, proposal creation, apply, reject, diff, activation, cleanup, and user-task creation to scope.
2. Implement role route: Architect-authored only, user-approved always.
3. Preserve Phase 1 per-agent governance exactly.
4. Add role proposal user task labels/description.
5. Add role read/propose/rollback/diff/list command handling.
6. Extend MCP tool schemas and scoped dispatch.
7. Update retry/idempotency classification.
8. Ensure dismissed Architects cannot write role proposals.
9. Ensure Engineers can read but not write role overlays.

Exit criteria:

- Role proposal created by Architect routes to user for all role kinds.
- `engineer_behavior_requires_user_approval` has no effect on role routes.
- Engineer role-write attempts are rejected with clear v1 messaging.
- Per-agent behavior remains backward-compatible.

### Phase C — Prompt composition (backend/prompts-config)

Files:

- `torque/behavior_overlay.py`
- `torque/server.py`
- `torque/server_agent.py`
- `torque/engineer.py`
- `torque/architect.py`
- `torque/server_prompts.py` if helper signatures need preview support
- `tests/test_behavior_overlay.py`
- `tests/test_engineer_prompt.py`
- `tests/test_architect_prompt.py`
- `tests/test_dispatch_preamble.py` or a new worker prompt test

Work:

1. Add stack renderer for role + agent layers.
2. Inject role+agent stack into Architect/Engineer persistent prompts in correct order.
3. Inject Worker-role overlay into worker dispatch prompt between role preamble and task/action body.
4. Update prompt previews to show role overlays where relevant.
5. Fix `AgentLaunchService.apply_persistent_prompt()` fallback so it cannot skip a missing agent layer just because a role marker exists.
6. Add tests for ordering, empty role no-op, fail-closed overbudget behavior, and worker insertion location.

Exit criteria:

- Engineer/Architect prompt tests prove base → role → agent order.
- Worker prompt test proves role overlay is before task body/postscript and not duplicated in persistent prompt.
- Empty role overlays produce zero rollout prompt behavior delta.

### Phase D — CLI/offline support (backend/CLI)

Files:

- `bin/torque`
- `tests/test_cli_context.py` or `tests/test_behavior_overlay.py` CLI portions
- possibly `docs/reference/reference-guide.md` / CLI docs if maintained

Work:

1. Decode v2 scope fields in offline SQLite read path.
2. Support old v1 DB reads by synthesizing scope fields if migration has not run.
3. Add `--role` and `--group` flags to read/versions/proposals/rollback.
4. Clarify help text for `behavior setting` as per-agent Engineer governance only.
5. Ensure approve/reject/diff remain proposal/version-id based and scope-agnostic.

Exit criteria:

- Existing `torque behavior read <agent>` still works.
- New role commands work offline for reads and via daemon for rollback.
- CLI tests cover old and new schemas.

### Phase E — UI role surfaces (Panelsmith / UI-UX)

Files:

- `webview.html`
- `static/js/behavior_overlay.js`
- `static/js/agent_panel.js`
- `static/js/modals.js`
- `static/js/ws.js`
- `static/js/events.js` if approval cards need label parsing changes
- `static/style.css`
- `tests/frontend_agent_panel.test.js`
- `tests/frontend_group_settings_modal.test.js`
- `tests/frontend_render_surface_focus.test.js` if rerender stability is touched

Work:

1. Generalize frontend behavior overlay state from `agent_id` to `scope_id`.
2. Reuse editor/timeline/diff components for role scopes.
3. Add role overlay sections to Group Settings per-kind Behavior panes.
4. Add inherited role overlay summary to Engineer/Architect per-agent Behavior tabs.
5. Keep Worker per-agent UI read-only or absent; edit Worker-role only in Group Settings.
6. Update approval modal to display scope labels for role proposals.
7. Update WS delta handling with narrow invalidation for role scopes.
8. Add frontend regression tests for role editor, approval modal, no fetch loops, and focus/scroll preservation.

Exit criteria:

- User can propose role overlay changes for all three kinds through UI.
- User can approve/reject role proposal diffs through existing modal.
- Per-agent Behavior tab still works and shows role inheritance without mixing scopes.
- Worker UI does not imply per-worker editing.

### Phase F — Inaugural seed + docs/manual verification

Files/process:

- `docs/plans/role-scope-behavior-overlays.md` can remain as design record.
- Update operator docs if this feature has a docs surface.
- Manual seed through UI/MCP after deployment.

Work:

1. After feature approval/deploy, Architect submits the four-default Engineer-role proposal through the governed flow.
2. User approves rendered diff.
3. Relaunch an Engineer or inspect prompt preview to confirm role overlay appears before any agent overlay.
4. Dispatch a Worker to confirm Worker-role overlay appears in the dispatch prompt only when non-empty.

Exit criteria:

- Engineer-role inaugural overlay is applied as a normal version/proposal/activation, not a migration artifact.
- Rollback to empty works from the role version timeline/CLI.

---

## 11. Test strategy

### Backend unit/integration tests

Add or update tests for:

- Scope helper normalization and labels.
- Per-layer caps: 4 KiB agent, 2 KiB role, 6 KiB combined.
- Render ordering: role before agent.
- Fail-closed behavior: corrupt role omitted; valid agent preserved; no truncation.
- Empty role no-op in prompts.
- Role proposal routes always `approval_route=user`, `next_actor_kind=user`.
- `engineer_behavior_requires_user_approval` does not affect role routes.
- Architect-authored-only role proposals; Engineer role writes rejected.
- Worker per-agent proposal still rejected; Worker-role proposal accepted.
- User approval task creation/resolution for role proposals.
- Role rollback creates activation and repoints only role active pointer.
- Scope-specific stale base checks.
- Visibility/scoping across groups.

### Migration tests

Create an old Phase 1 schema DB fixture in the test:

1. Create v1 overlay tables with `agent_id` primary/unique shapes.
2. Insert at least one version, active pointer, pending proposal, and activation.
3. Run current `TorqueDB.init()` migration.
4. Assert v2 columns and composite indexes exist.
5. Assert rows are backfilled as `scope_kind=agent`, `scope_group=''`, `scope_key=<old agent_id>`.
6. Assert `behavior_overlay_active` primary key is composite by inserting a role active row with no `agent_id` collision.
7. Run migration again and assert no duplicate/loss.

### MCP/scoping tests

- Architect can propose `engineer`, `architect`, and `worker` role overlays for own group.
- Architect cannot propose role overlay after dismissal.
- Architect cannot propose a role overlay for another group unless explicitly authorized in a future design.
- Engineer can read own group Engineer-role overlay but cannot propose/rollback it.
- Architect per-agent visibility remains self + hired Engineers.
- Version diff rejects cross-scope version ids without leaking text.

### CLI tests

- `torque behavior read <agent>` unchanged.
- `torque behavior read --role engineer --group g` returns role active/text.
- `torque behavior versions --role worker --group g` lists role versions.
- `torque behavior proposals --role architect --group g` filters correctly.
- `torque behavior rollback --role engineer --group g <version>` calls daemon with role scope.
- `torque behavior setting` help/JSON remains per-agent Engineer governance.

### Prompt tests

- Engineer prompt contains base content before role block before agent block.
- Architect prompt contains base content before role block before agent block.
- Worker dispatch prompt contains identity/role preamble before worker-role block before task/action prompt before postscript.
- Empty role overlay does not add a role fence to prompts.
- Prompt previews include role overlay for the selected kind.
- `server_agent.apply_persistent_prompt()` fallback does not duplicate or omit stack layers.

### Frontend tests

- Group Settings has role overlay editor sections for Worker, Engineer, Architect Behavior panes.
- Per-agent Behavior tab shows inherited role summary and agent editor separately.
- Approval modal renders role proposal scope labels and diff before enabling approve/reject.
- WS deltas for role scopes invalidate only relevant focused panels/settings panes.
- No proposal-list fetch loop after role proposal updates.
- Textarea focus/caret/scroll preservation survives role overlay deltas.

### Manual smoke

Run from a non-worker shell only; do not deploy/stop from a Torque worker context.

- Create/apply Engineer-role proposal in a test profile/port.
- Preview Engineer prompt and verify role block appears before agent block.
- Apply per-agent Engineer overlay and verify both layers appear in order.
- Dispatch Worker and verify Worker-role block appears in prompt preview/dispatch prompt.
- Reject a Worker-role proposal and verify user task resolves.
- Roll back Engineer-role overlay to empty and verify prompt no longer includes role text.

---

## 12. Risks and gotchas

1. **Group vs workspace-global role scope must be final before build.** This spec recommends group-scoped role overlays. The schema can support global by leaving `scope_group=''`, but UI/scoping logic differs.
2. **`behavior_overlay_active` primary key migration is a real table rebuild.** Do not treat it as a simple `ALTER TABLE ADD COLUMN` migration.
3. **Frontend cache keys must be generalized.** Keeping agent-id-only maps will break role rows and can leak/overwrite state where `agent_id=''`.
4. **Prompt fallback marker check can become wrong with multiple blocks.** Update `server_agent.apply_persistent_prompt()` deliberately.
5. **Worker insertion point matters.** Put Worker-role overlay before task/action prompt and postscript, not after, so shared defaults cannot appear to override task-specific requirements.
6. **Empty role rendering should avoid prompt bloat.** Do not add no-op role fences to every prompt on rollout.
7. **Approval blast radius must be visible.** User approval tasks and modals should label role proposals as affecting all future agents of that kind in the group.
8. **Role overlay is not a replacement for role YAML/custom instructions.** It is governed, versioned, rollbackable behavior defaults; launch configuration and provider/system prompt config remain in role/settings surfaces.

---

## 13. Open questions for Torqly/User review

1. **Confirm group-scoped role overlays.** This spec recommends one role overlay per `(group, kind)`. If the intended product is exactly one workspace-global overlay per kind, change `scope_group` semantics before implementation.
2. **Confirm size constants.** Recommendation: role 2 KiB, agent 4 KiB unchanged, combined 6 KiB with less-specific-layer omission on combined overflow.
3. **Confirm UI placement.** Recommendation: Group Settings → Worker/Engineer/Architect → Behavior panes for editable role overlays; per-agent Behavior tab shows inherited role read-only.
4. **Confirm Worker panel visibility.** Recommendation: no editable Worker Behavior tab; optional read-only inherited-role visibility only.
5. **Confirm role withdrawal.** Should an Architect be allowed to withdraw its own pending role proposal before user action, or should only the user reject role proposals once created?
6. **Confirm seed group and author.** The inaugural Engineer-role proposal should target the Torque group (unless user chooses another group) and be authored by an Architect after deployment.

---

## 14. Approval path

This plan requires **human approval** before implementation because it introduces:

- persisted schema migration;
- prompt composition changes for all agent kinds;
- new governance routing and approval blast radius;
- MCP/API/CLI contract changes;
- UI changes to governed behavior editing; and
- a post-ship seed proposal affecting shared Engineer behavior.

Do not derive implementation from this research task until Torqly/User review resolves the open questions above.
