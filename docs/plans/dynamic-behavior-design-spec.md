# Dynamic Behavior — design spec + phased build plan

**Task:** TORQUE:758 — Dynamic Behavior (per-agent governed prompt overlay)
**Status:** Design/spec only; no implementation performed.
**Review gate:** Human/Torqly review required before build dispatch because this introduces new persisted schema, governance behavior, approval workflows, prompt-build changes, and a required user-facing diff approval surface.

---

## 1. Executive summary

Dynamic Behavior adds a **versioned, governed, per-agent prompt overlay** for persistent architect and engineer agents. The overlay is an additive, size-capped, clearly fenced block appended at a known prompt insertion point. It is keyed by **individual `AgentCell.id`**, not by kind or role. Approved changes create immutable version rows and update a non-destructive active-version pointer. Rollback repoints that active pointer to an earlier version and records an activation event; no version text is deleted.

V1 scope is intentionally narrow:

- **Architect overlays:** supported; the architect may propose changes to its own overlay, and the **user always approves/rejects via a diff** before the change can apply.
- **Engineer overlays:** supported; two author paths are supported:
  1. An engineer proposes a change to its own overlay; its hiring architect reviews it.
  2. The architect may directly author/modify an overlay for one of its hired engineers.
- **Worker overlays:** explicitly **not supported in v1**. Workers are ephemeral and do not fit the “improves its own behavior over time” product intent. The data model remains kind-agnostic so worker support can be added later without schema reshape; the v1 exclusion is enforced in tool/authorization logic.

Engineer-scope governance has a new persisted setting:

- `engineer_behavior_requires_user_approval` default **false**.
- When false, the architect is final authority for engineer overlays.
- When true, **any engineer-overlay modification**, including an architect’s direct edit, also requires user diff approval before it applies.

The governance gate is the primary content control. The only hard content-adjacent enforcement is the overlay size cap. Optional advisory lint can flag likely base-override phrasing for approver attention, but it must not block approval.

---

## 2. Resolved scope decisions and remaining user decision

### Resolved for v1

1. **Per individual agent, not per kind.** Storage is keyed by `agent_id == AgentCell.id`.
2. **Architect + engineer overlays only.** No worker overlay tools or prompt injection in v1.
3. **Base prompt immutable by this mechanism.** Dynamic Behavior can only append a fenced block; it never edits base prompt strings, MCP/tool contracts, safety text, identity text, or dispatch instructions.
4. **Apply only at next session/dispatch boundary.** Updating the active version never mutates a running agent’s prompt or current PTY session.
5. **Architect overlay requires user approval.** Always.
6. **Engineer overlay has two author paths.** Engineer-proposed and architect-authored direct edit are both first-class.
7. **Engineer user-approval setting.** A group setting controls whether engineer-scope changes also require user diff approval.
8. **Content guardrail policy.** Governance gate is primary; size cap is hard; advisory lint is soft.
9. **Phase 2 diff UI is required.** The rendered user-facing diff approval surface is not polish; it is the required user-facing half of the feature.

### Remaining product decision: default block seeding

The data model and prompt integration should support either option, but the spec recommends **Option A**.

#### Option A — start empty in v1 (recommended)

- Seed each supported agent with an empty overlay version or treat missing active overlay as the empty default block.
- The rendered fence exists, but contains no behavioral instructions beyond the non-behavioral subordination/default statement.
- Benefits:
  - Zero behavior delta on rollout.
  - Crisp invariant: base remains the complete shipped behavior until a governed overlay is explicitly approved.
  - Empty version is a safe rollback floor.
  - Easier testing: active overlay absent/empty must produce exactly the same behavioral intent as today.
- Cost:
  - The first useful personalization requires an explicit proposal.

#### Option B — seed from a curated slice of today’s prompt

- Create initial non-empty overlay versions from selected existing prompt guidance.
- Benefits:
  - Gives agents a meaningful starting overlay and demonstrates the system’s intent immediately.
  - May reduce early repetitive proposals if there is known “custom behavior” already embedded in prompts.
- Costs:
  - Blurs the base/overlay boundary at rollout.
  - Risks accidental behavior change if the curated slice is not semantically identical in the new location.
  - Makes rollback-to-empty less obviously equivalent to current behavior.

**Recommendation:** Start empty for v1. Treat curated seeding as a deliberate fast-follow only if review history shows the same overlay text is repeatedly re-added.

---

## 3. Research summary: relevant architecture and files

### Prompt builders and launch paths

- `torque/server_prompts.py`
  - `build_torque_system_prompt(...)` builds the core Torque Agent prompt currently used for worker/system preamble and as part of architect persistent prompt composition.
  - `build_dispatch_postscript(...)` builds worker dispatch/task guidance. V1 does not inject worker overlays here.
- `torque/engineer.py`
  - `_build_engineer_base_system_prompt(...)` assembles engineer base sections.
  - `build_engineer_system_prompt(...)` appends architect-escalation and owner-user guidance. This is the natural engineer overlay insertion point.
- `torque/architect.py`
  - `build_architect_system_prompt(...)` is architect-specific and separate from the worker base prompt. This is the natural architect overlay insertion point.
- `torque/server.py`
  - `_build_cell_persistent_prompt(...)` chooses prompt construction for engineer/architect/worker launches. This should fetch and pass the active overlay for supported kinds.
  - `_architect_persistent_prompt_text(...)` builds architect persistent prompt composition.
  - `_build_group_system_prompt_preview(...)` mirrors launch prompts for settings preview and must avoid showing a misleading active overlay unless previewing a specific agent.
  - Pending-hire approval handlers are a precedent for user-visible approval tasks but do not currently render a true diff.
- `torque/server_agent.py`
  - `AgentLaunchService.apply_persistent_prompt(...)` writes provider persistent-prompt files at launch time. This aligns with next-session application.

### Persistence/state patterns

- `torque/state.py`
  - `AgentCell.id` is the stable persistent identity key. Slug is not suitable as a storage key.
  - `GroupSettings` is the best home for the new engineer user-approval setting.
  - State mutations must follow the project invariant: mutate through `MatrixState`, emit deltas, and persist via DB save methods.
- `torque/db_schema.py` and `torque/db.py`
  - Add new SQLite tables and DB methods; keep CLI/offline reads working.
  - Existing `pending_hires` and `decisions` are useful patterns for persisted approval/audit rows.

### MCP/scoping patterns

- `torque/mcp_engineer.py`
  - Add engineer-side `engineer_*` tools to propose/read/diff/list own overlay state.
- `torque/mcp_architect.py`
  - Add architect-side `architect_*` tools for own proposals, hired-engineer direct edits, proposal review, diffs, versions, and rollback.
- `torque/mcp_tools_shared.py`
  - Enforce caller kind, self-only engineer reads/writes, architect-to-hired-engineer scoping, dismissed architect write restrictions, and hidden/denied worker support.
- `torque/mcp_retry.py`
  - Classify new scoped write tools for retry/idempotency behavior.

### UI patterns

- `webview.html`, `static/js/agent_panel.js`, `static/js/ws.js`, `static/js/diff.js`, `static/style.css`
  - Add behavior overlay timeline/diff UI in Phase 2.
  - Existing pending-hire UI is a list/approval precedent but not sufficient because Dynamic Behavior requires a rendered diff.
  - Preserve frontend state across deltas/rerenders per existing surface-invalidation discipline.

---

## 4. Data model

### Design choice: separate tables, not `AgentCell` fields

Use dedicated overlay tables keyed by `agent_id`, with optional compact summaries surfaced through state. Do **not** store overlay text or version history on `AgentCell`.

Reasons:

- Overlay history is unbounded relative to `AgentCell` and should not bloat every agent snapshot.
- Version/proposal/activation rows have their own lifecycle and indexing needs.
- The key remains kind-agnostic (`agent_id`) while v1 governance can be kind-gated in authorization.
- CLI/offline reads can query SQLite directly without reconstructing large state snapshots.

### Tables

#### `behavior_overlay_versions`

Immutable approved version text.

Fields:

- `id TEXT PRIMARY KEY`
- `agent_id TEXT NOT NULL`
- `version_number INTEGER NOT NULL`
- `parent_version_id TEXT NOT NULL DEFAULT ''`
- `text TEXT NOT NULL`
- `text_sha256 TEXT NOT NULL`
- `author_agent_id TEXT NOT NULL DEFAULT ''`
- `author_kind TEXT NOT NULL DEFAULT ''` — `engineer`, `architect`, `system`, or future values.
- `rationale TEXT NOT NULL DEFAULT ''`
- `approver_id TEXT NOT NULL DEFAULT ''`
- `approver_kind TEXT NOT NULL DEFAULT ''` — `architect`, `user`, `system`, or future values.
- `source_proposal_id TEXT NOT NULL DEFAULT ''`
- `created_at REAL NOT NULL`
- `metadata_json TEXT NOT NULL DEFAULT '{}'`

Indexes/constraints:

- Unique `(agent_id, version_number)`.
- Index `(agent_id, version_number DESC)`.
- Index `(source_proposal_id)`.

Notes:

- If Option A is chosen, either create a v0/v1 empty row when the first overlay record is needed, or treat missing active row as the empty default. Prefer an explicit empty v0/v1 row on first touch because it gives rollback a concrete floor and simplifies diffs.

#### `behavior_overlay_active`

Current active pointer per agent.

Fields:

- `agent_id TEXT PRIMARY KEY`
- `active_version_id TEXT NOT NULL`
- `updated_at REAL NOT NULL`
- `updated_by_kind TEXT NOT NULL DEFAULT ''`
- `updated_by_id TEXT NOT NULL DEFAULT ''`
- `reason TEXT NOT NULL DEFAULT ''`

Notes:

- This is the only row prompt building needs for a target agent, plus the referenced version text.
- Rollback updates this pointer; it does not delete or mutate versions.

#### `behavior_overlay_proposals`

Pending and resolved proposals, including architect direct edits for audit.

Fields:

- `id TEXT PRIMARY KEY`
- `agent_id TEXT NOT NULL` — target agent.
- `target_kind TEXT NOT NULL DEFAULT ''` — captured at proposal time for audit only; not the storage key.
- `proposal_type TEXT NOT NULL DEFAULT 'set_text'` — `set_text` or `rollback`.
- `base_version_id TEXT NOT NULL DEFAULT ''`
- `target_version_id TEXT NOT NULL DEFAULT ''` — for rollback proposals.
- `proposed_text TEXT NOT NULL DEFAULT ''` — for `set_text`; rollback can store a snapshot for stable diff display.
- `proposed_text_sha256 TEXT NOT NULL DEFAULT ''`
- `proposed_by_agent_id TEXT NOT NULL DEFAULT ''`
- `proposed_by_kind TEXT NOT NULL DEFAULT ''` — `engineer` or `architect` in v1.
- `rationale TEXT NOT NULL DEFAULT ''`
- `status TEXT NOT NULL DEFAULT 'proposed'` — `proposed`, `approved`, `rejected`, `applied`.
- `approval_route TEXT NOT NULL` — `architect`, `user`, or `architect_then_user`.
- `next_actor_kind TEXT NOT NULL DEFAULT ''` — `architect`, `user`, or empty for terminal states.
- `requires_user_approval INTEGER NOT NULL DEFAULT 0`
- `architect_approver_id TEXT NOT NULL DEFAULT ''`
- `architect_approved_at REAL`
- `user_task_id TEXT NOT NULL DEFAULT ''`
- `user_approved_at REAL`
- `lint_warnings_json TEXT NOT NULL DEFAULT '[]'`
- `resolved_by_kind TEXT NOT NULL DEFAULT ''`
- `resolved_by_id TEXT NOT NULL DEFAULT ''`
- `resolved_at REAL`
- `resolution_note TEXT NOT NULL DEFAULT ''`
- `applied_version_id TEXT NOT NULL DEFAULT ''`
- `applied_at REAL`
- `idempotency_key TEXT NOT NULL DEFAULT ''`
- `created_at REAL NOT NULL`
- `updated_at REAL NOT NULL`

Indexes/constraints:

- Index `(agent_id, status, created_at DESC)`.
- Index `(approval_route, status)`.
- Index `(next_actor_kind, status)`.
- Index `(user_task_id)`.
- Unique `(proposed_by_agent_id, idempotency_key)` when `idempotency_key != ''`.

Status semantics:

- `proposed`: proposal exists and awaits its current `next_actor_kind`.
- `approved`: the architect-level approval/endorsement is complete, but user approval or application is still pending. This is mainly used for engineer-scope proposals when the group setting requires user approval, and for architect-authored engineer edits waiting on user approval.
- `rejected`: terminal; no active pointer change.
- `applied`: terminal; a version row and/or activation row has been written and the active pointer changed.

#### `behavior_overlay_activations`

Audit log of active-pointer changes, including rollback.

Fields:

- `id TEXT PRIMARY KEY`
- `agent_id TEXT NOT NULL`
- `previous_version_id TEXT NOT NULL DEFAULT ''`
- `active_version_id TEXT NOT NULL`
- `proposal_id TEXT NOT NULL DEFAULT ''`
- `actor_kind TEXT NOT NULL DEFAULT ''`
- `actor_id TEXT NOT NULL DEFAULT ''`
- `action TEXT NOT NULL` — `apply`, `rollback`, `seed`.
- `reason TEXT NOT NULL DEFAULT ''`
- `created_at REAL NOT NULL`

Indexes:

- `(agent_id, created_at DESC)`.
- `(proposal_id)`.

### New persisted setting

Add to `GroupSettings`:

- `engineer_behavior_requires_user_approval: bool = False`

Recommendation: place this in `GroupSettings`, not per-architect settings.

Rationale:

- Engineer/architect governance is group-scoped in current Torque hierarchy.
- Group settings already carry prompt/policy/governance-adjacent configuration and are threaded into prompt builders/settings UI.
- A group-level switch is easier to reason about for all hired engineers in that group.
- Per-architect settings would complicate engineer transfer/reassignment and create surprising differences among engineers in the same group. That can be added later if product demand appears.

### SQLite serialization and migrations

Phase 1 should update:

- `torque/db_schema.py`: create tables and indexes idempotently in `initialize_database(...)`.
- `torque/db.py`: add load/save/list methods for versions, active pointer, proposals, activations, and group-setting serialization.
- `torque/state.py`: add compact dataclasses or dict serializers for proposal/version summaries and methods that call DB writes and emit deltas.
- `bin/torque`: add offline CLI reads for versions/diffs/proposals and HTTP writes for approve/reject/rollback/settings.
- Tests should verify old DBs migrate idempotently and CLI reads work with daemon stopped.

### Snapshot, delta, and broadcast implications

Do not broadcast full overlay text in routine snapshots/deltas. Full text should be loaded lazily via explicit diff/read commands.

Recommended state/delta operations:

- `behavior_overlay_version_append` — summary only: version id, agent id, number, author, approver, created timestamp, text hash, length.
- `behavior_overlay_active_update` — agent id, active version id, updated timestamp, actor/reason.
- `behavior_overlay_proposal_upsert` — summary only: proposal id, target agent, status, next actor, route, lint warning count, timestamps.
- `behavior_overlay_proposal_resolve` or status update — terminal status and resolved metadata.
- `group_settings_update` — include `engineer_behavior_requires_user_approval` with existing group settings payload.

Frontend invalidation must be targeted: only refresh behavior surfaces for the focused agent or relevant architect/hired engineer, not the whole engineer panel on every proposal/version delta.

---

## 5. Prompt-build integration

### Fenced overlay format

Use a single helper, e.g. `torque/behavior_overlay.py`, to render the block consistently:

```text
## Dynamic Behavior Overlay (agent-scoped, approved)
<!-- torque:behavior-overlay agent_id="..." version_id="..." sha256="..." -->
This block is additive and subordinate. It may refine this agent's style,
working habits, and task-handling preferences only. If anything below
conflicts with earlier Torque, system, safety, tool, MCP, or governance
instructions, ignore this block and follow the earlier instructions.

<approved overlay text, or empty/default text>
<!-- /torque:behavior-overlay -->
```

The subordination statement is a prompt-level instruction, not a hard technical content lock. The base remains structurally authoritative because the overlay mechanism cannot edit it.

### Default vs active overlay composition

For each supported persistent agent prompt:

1. Build the existing base prompt exactly as today.
2. Resolve active overlay by `AgentCell.id`.
3. If no active version exists, render the shipped default block.
4. If an active version exists and passes render-time size validation, render its text inside the fence.
5. If active version text exceeds the cap or is corrupt, fail closed by rendering the empty/default block, log/report the validation failure, and do not truncate.

If Option A is selected, the shipped default block contains no behavioral instructions beyond the fence/subordination metadata. If Option B is selected, default seeding happens through version rows, not by hardcoding mutable behavior into prompt builders.

### Size cap enforcement

Recommended initial cap: **4 KiB UTF-8 bytes** for overlay text excluding fence/metadata. The exact value can be adjusted during review, but the implementation should centralize it in one constant.

Hard checks:

- Propose-time validation: reject over-cap proposals. Do not truncate.
- Apply-time validation: re-check before creating/applying a version.
- Prompt-render validation: re-check active version before rendering; fail closed to the empty/default block if invalid.

### Insertion points

#### Architect

Files:

- `torque/architect.py`
- `torque/server.py`

Change:

- Extend `build_architect_system_prompt(...)` to accept an optional rendered behavior overlay block or overlay payload.
- Append the overlay as the final section after existing custom instructions/policy sections.
- In `server.py`, update `_architect_persistent_prompt_text(...)` and `_build_cell_persistent_prompt(...)` so architect prompts fetch active overlay for the architect `AgentCell.id`.

Reasoning:

- Architect has its own prompt builder separate from `build_torque_system_prompt(...)`.
- Architect overlay must apply only at future launches/relaunches.

#### Engineer

Files:

- `torque/engineer.py`
- `torque/server.py`

Change:

- Extend `build_engineer_system_prompt(...)` to accept an optional behavior overlay.
- Append it as the final section after architect-escalation and owner-user message composition.
- In `server.py`, update engineer prompt launch paths, including new engineer creation and `_build_cell_persistent_prompt(...)`, to fetch by engineer `AgentCell.id`.

#### Worker

Files:

- No worker prompt injection in v1.

Change:

- Do not pass overlays to worker dispatch prompt construction.
- Keep `build_torque_system_prompt(...)` capable of future overlay rendering only if useful for shared code, but server code must not supply worker overlays in v1.
- Worker MCP tools do not include behavior proposal/read/write tools in v1.

### Apply-at-boundary rule

Approving or rolling back an overlay updates SQLite and the active pointer only. It must not call provider APIs or mutate a running session’s live prompt. The new active overlay appears when the persistent prompt is rebuilt for the agent’s next launch/relaunch/session boundary.

---

## 6. Governance and approval workflow

### Actors

- **Engineer:** may propose changes to its own overlay only if it is hired by an architect. It cannot propose for workers, other engineers, or architects.
- **Architect:** may propose changes to its own overlay; approve/reject proposals from its hired engineers; directly author/edit overlays for its hired engineers; request rollback for itself or hired engineers.
- **User:** approves/rejects architect-scope changes always, and engineer-scope changes when `engineer_behavior_requires_user_approval` is true.

### Proposal route computation

Compute and persist the route when the proposal is created so later setting changes do not silently move an in-flight proposal.

| Target | Author path | Setting | Route | First visible approver | Final applier |
|---|---|---:|---|---|---|
| Architect self | architect proposes own overlay | n/a | `user` | user | user approval applies |
| Engineer self | engineer proposes own overlay | false | `architect` | hiring architect | architect approval applies |
| Engineer self | engineer proposes own overlay | true | `architect_then_user` | hiring architect | user approval applies after architect endorsement |
| Engineer hired by architect | architect direct edit | false | `architect` | architect author is final authority | applies atomically |
| Engineer hired by architect | architect direct edit | true | `architect_then_user` | architect author/endorsement | user approval applies |

### State transitions

#### Architect own overlay

1. Architect calls propose tool.
2. Proposal row created: `status=proposed`, `approval_route=user`, `next_actor_kind=user`.
3. User-facing Backlog attention item is created with proposal id and diff metadata.
4. User approves:
   - Validate proposal is still active and base version is not stale.
   - Create version row.
   - Update active pointer.
   - Create activation event.
   - Mark proposal `applied`.
5. User rejects:
   - Mark proposal `rejected` with note.
   - No version or active-pointer change.

#### Engineer proposes own overlay; setting false

1. Engineer calls propose tool.
2. Proposal row created: `status=proposed`, `approval_route=architect`, `next_actor_kind=architect`.
3. Hiring architect sees proposal via architect tools/messages.
4. Architect approves:
   - Validate base version/staleness and size cap.
   - Create version, update active pointer, activation event.
   - Mark proposal `applied`.
5. Architect rejects:
   - Mark proposal `rejected`.

#### Engineer proposes own overlay; setting true

1. Engineer calls propose tool.
2. Proposal row created: `status=proposed`, `approval_route=architect_then_user`, `next_actor_kind=architect`.
3. Hiring architect approves or rejects first.
4. If architect rejects: terminal `rejected`.
5. If architect approves:
   - Record `architect_approver_id` and `architect_approved_at`.
   - Set `status=approved`, `next_actor_kind=user`.
   - Create user Backlog approval item.
6. User approves:
   - Validate base/staleness and size cap.
   - Create version, update active pointer, activation event.
   - Mark proposal `applied`.
7. User rejects:
   - Mark proposal `rejected`.

This preserves architect governance while adding user approval when the setting is enabled.

#### Architect direct edit of engineer overlay

1. Architect calls direct-edit tool for a hired engineer.
2. If setting false:
   - Persist proposal row for audit.
   - Validate, create version, update active pointer, activation event, mark `applied` in one transaction.
3. If setting true:
   - Persist proposal row with `approval_route=architect_then_user`, `status=approved`, `next_actor_kind=user`, and architect endorsement fields set.
   - Create user Backlog approval item.
   - User approval/rejection follows the same branch as above.

#### Rollback

Rollback is modeled as a proposal type rather than a destructive mutation:

- `proposal_type=rollback`
- `target_version_id=<earlier version>`
- Applying rollback writes an activation event and repoints `behavior_overlay_active.active_version_id`.
- It does **not** create a new version row and does **not** delete later versions.
- Approval route is the same as a text change for that target agent and setting.

### Staleness and idempotency

- Propose tools should accept `expected_base_version_id`. If omitted, server uses current active version.
- Approval/apply must ensure the active version is still the proposal’s `base_version_id`; if not, return a stale-base error and require a new proposal/diff.
- Create proposal tools should support `idempotency_key` to avoid duplicate proposals on MCP retry.
- Approve/reject operations are idempotent for already-terminal proposals:
  - Re-approve applied proposal returns the existing applied version summary.
  - Re-reject rejected proposal returns the existing rejected summary.
  - Approve rejected or reject applied returns a clear conflict.

### User approval surface

Phase 1 should create a Backlog attention item for user-routed proposals, labeled distinctly, e.g.:

- `torque:human`
- `behavior-overlay-approval`
- `proposal:<proposal_id>`

The task/body should include target agent, target kind, author, rationale, lint warnings, and a reference to CLI/server commands for viewing the diff. Phase 2 replaces/augments this with the required rendered diff card/modal.

Do not reuse generic `architect_ask` semantics unless the resolver is explicitly extended to handle structured approve/reject actions; behavior overlay approval needs proposal ids, stale-base validation, and terminal state transitions.

---

## 7. Content guardrails policy

The primary control is the approval gate:

- Architect reviews exact engineer overlay text before it applies, unless the user-approval setting escalates it further.
- User reviews exact architect overlay text before it applies.
- When enabled, user also reviews engineer overlay text before it applies.

Hard technical checks:

- Size cap only.
- Reject on overflow; never truncate.
- Render-time fail-closed if persisted text somehow exceeds cap.

Advisory checks:

- Optional lint scans for phrases that appear to override base/tool/safety instructions, such as “ignore the safety section,” “disregard tool rules,” or “do not use required MCP reporting.”
- Lint warnings are stored on the proposal and surfaced prominently to the approver.
- Lint warnings do not block approval; the human/architect decides.

Do not claim a hard content lock beyond append-only structure and human governance. An appended instruction can still influence a model; that risk is managed by review, not by pretending the model can be technically forced to ignore all conflicting overlay text.

---

## 8. MCP and command surface

Tool names follow existing final conventions: worker `torque_*`, engineer `engineer_*`, architect `architect_*`. V1 adds no worker tools.

### Engineer tools

Engineer tools target the caller’s own overlay only.

Recommended tools:

- `engineer_behavior_overlay_read()`
  - Returns active overlay summary/text for the calling engineer.
- `engineer_behavior_overlay_versions(limit?: int)`
  - Lists the caller’s version timeline.
- `engineer_behavior_overlay_diff(from_version_id?: string, to_version_id?: string, proposal_id?: string)`
  - Computes active-vs-proposed or version-vs-version diff.
- `engineer_behavior_overlay_propose(text: string, rationale: string, expected_base_version_id?: string, idempotency_key?: string)`
  - Creates proposal for the caller’s own overlay.
- `engineer_behavior_overlay_request_rollback(version_id: string, rationale: string, expected_base_version_id?: string, idempotency_key?: string)`
  - Creates rollback proposal for the caller’s own overlay.

Authorization:

- Caller must be `kind=engineer`.
- Target is always caller `AgentCell.id`.
- In v1, if the engineer has no `hired_by_architect_id`, propose/rollback should be hidden or return a clear unsupported/no-architect-governor error. Read/list can still return empty/default state if desired.

### Architect tools

Architect tools target the caller architect or engineers hired by that architect.

Recommended tools:

- `architect_behavior_overlay_read(agent_id?: string)`
  - No `agent_id` means own overlay. With `agent_id`, target must be a hired engineer.
- `architect_behavior_overlay_versions(agent_id?: string, limit?: int)`
  - Lists version timeline for own or hired engineer.
- `architect_behavior_overlay_diff(agent_id?: string, from_version_id?: string, to_version_id?: string, proposal_id?: string)`
  - Computes active-vs-proposed or version-vs-version diff.
- `architect_behavior_overlay_proposal_list(status_filter?: string, agent_id?: string)`
  - Lists proposals visible to the architect.
- `architect_behavior_overlay_propose(text: string, rationale: string, expected_base_version_id?: string, idempotency_key?: string)`
  - Propose change to architect’s own overlay; always routes to user.
- `architect_behavior_overlay_propose_for_engineer(engineer_id: string, text: string, rationale: string, expected_base_version_id?: string, idempotency_key?: string)`
  - Direct author/edit path for a hired engineer; applies or routes to user based on group setting.
- `architect_behavior_overlay_approve(proposal_id: string, expected_proposed_text_sha256?: string, note?: string)`
  - Approves engineer-originated proposals visible to this architect. If setting false, applies. If setting true, endorses and creates/advances user approval.
- `architect_behavior_overlay_reject(proposal_id: string, note?: string)`
  - Rejects visible engineer-originated proposals awaiting architect action.
- `architect_behavior_overlay_rollback(agent_id?: string, version_id: string, rationale: string, expected_base_version_id?: string, idempotency_key?: string)`
  - Creates/applies rollback route for own or hired engineer according to target scope and setting.

Authorization:

- Caller must be `kind=architect`.
- Own-overlay tools target caller `AgentCell.id`.
- Engineer-targeted tools must resolve through the existing hired-engineer scoping pattern, not arbitrary agent lookup.
- Dismissed architects should retain safe read/list/diff access if consistent with existing policy, but write tools must be blocked.

### User/server/CLI commands

These are not MCP tools for agents; they are trusted operator surfaces.

Recommended CLI/server commands:

- List pending overlay approvals.
- Show proposal metadata.
- Render diff for proposal or two versions.
- Approve proposal.
- Reject proposal with note.
- List versions for an agent.
- Request rollback for an agent/version.
- Read/update `engineer_behavior_requires_user_approval`.

CLI read paths should work offline against SQLite. Writes should go through HTTP when daemon is running, following existing CLI conventions.

---

## 9. UI design scope (Phase 2, required)

Phase 2 is the required user-facing half of the feature. Phase 1 can prove the backend loop via MCP/CLI, but the product requirement is not fully satisfied until the user can review rendered diffs before approval.

### Agent panel Behavior tab

Add a `Behavior` tab for supported persistent agents.

Engineer-focused panel:

- Active overlay summary.
- Version timeline.
- Pending proposals for that engineer.
- Diff viewer for active-vs-proposed and version-vs-version.
- Proposal form/request rollback controls where allowed.

Architect-focused panel:

- Architect’s own overlay summary/timeline/proposals.
- Hired engineer overlay governance section: pending engineer proposals, direct edit entry point, timeline/diff for selected hired engineer.
- Approve/reject controls for engineer proposals awaiting architect.

### User approval UI

For Backlog tasks labeled `behavior-overlay-approval`:

- Render a dedicated approval card/button: “Review Dynamic Behavior diff.”
- Modal/panel shows:
  - Target agent name, kind, id/slug.
  - Author and rationale.
  - Current active version metadata.
  - Proposed version or rollback target metadata.
  - Advisory lint warnings.
  - Rendered diff, not just raw text.
  - Approve and reject controls with optional note.
- Approve/reject actions must call structured server commands with proposal id and expected hash/version metadata.

### Version timeline and rollback UI

- Timeline rows: version number, active marker, author, approver(s), source proposal, rationale, timestamp, text length/hash.
- Diff any two versions.
- Rollback button creates a governed rollback proposal; it does not immediately repoint unless governance route allows it.

### Frontend implementation notes

- Reuse or adapt existing `static/js/diff.js` and diff styles where practical.
- Add a small behavior-overlay JS module if needed; avoid framework/build-step changes.
- Preserve scroll, focus, expanded rows, and text drafts during delta-driven rerenders.
- WebSocket delta handling must mark only relevant surfaces.

---

## 10. Phased build plan

### Phase 0 — review and decision gate

**Owner:** Torqly/user.
**Output:** Approved spec, resolved default-block choice, accepted size cap or adjusted constant.

Tasks:

1. Review this design.
2. Decide default seeding option:
   - Recommended: Option A, empty overlay v1.
3. Confirm or adjust initial size cap:
   - Recommended: 4 KiB UTF-8 bytes.
4. Confirm Phase 1 can proceed as backend/CLI/MCP loop, with Phase 2 required for user-facing diff UI.

### Phase 1 — backend, persistence, prompts, MCP/CLI workflow

**Recommended owner:** orchestration-core / backend agent.
**Shippable state:** The backend loop works end-to-end via MCP and CLI/server commands. It is testable independently, but feature should remain considered incomplete for end users until Phase 2 renders the required diff UI.

#### 1. Add overlay helper module

Files:

- New: `torque/behavior_overlay.py`

Implement:

- Constants: max bytes, default empty text/fence metadata.
- Text validation and byte counting.
- Advisory lint helper.
- Render helper for fenced block.
- Diff helper wrapper for text pairs/proposals.

Tests:

- Size cap accepts boundary and rejects overflow.
- Lint surfaces warning metadata without blocking.
- Render helper includes fence metadata and does not truncate.

#### 2. Add SQLite schema and DB methods

Files:

- `torque/db_schema.py`
- `torque/db.py`
- Tests in `tests/test_db.py` or new `tests/test_behavior_overlay_db.py`

Implement:

- Tables/indexes described above.
- Load/save/list methods for versions, active pointer, proposals, activations.
- Migration-safe idempotent initialization.
- Group setting serialization/deserialization update.

Tests:

- Old DB migration idempotent.
- Version/proposal/active/activation rows round-trip.
- Active pointer rollback is non-destructive.
- Setting default false persists and reloads.

#### 3. Add MatrixState methods and deltas

Files:

- `torque/state.py`
- `torque/server.py` snapshot/delta plumbing if needed

Implement:

- Compact dataclasses/dicts for version/proposal summaries.
- State methods for create proposal, approve, reject, apply, rollback.
- `_emit` operations for overlay summaries.
- `_db_save_*` or direct DB helper calls following existing persistence invariants.

Tests:

- Mutations emit expected delta ops.
- DB save happens for persistent changes.
- Snapshot excludes full text but exposes enough summary/pending count.

#### 4. Integrate prompt builders

Files:

- `torque/architect.py`
- `torque/engineer.py`
- `torque/server_prompts.py` only if shared helper signature is useful; no worker injection in v1.
- `torque/server.py`

Implement:

- Optional overlay parameter in architect/engineer prompt builders.
- Fetch active overlay in launch-time persistent prompt build for architect/engineer only.
- Append overlay as final fenced section.
- Render empty/default block if absent or invalid.
- Do not update running sessions.

Tests:

- Architect prompt includes final overlay fence.
- Engineer prompt includes final overlay fence.
- Worker dispatch/persistent prompt does not include a behavior overlay in v1.
- Existing base prompt sections remain unchanged apart from appended block.
- Over-cap active text fails closed.

#### 5. Add governance logic and MCP tools

Files:

- `torque/mcp_engineer.py`
- `torque/mcp_architect.py`
- `torque/mcp_tools_shared.py`
- `torque/mcp_retry.py`
- Tests in MCP/scoping suites

Implement:

- Engineer read/list/diff/propose/request-rollback tools.
- Architect read/list/diff/proposal-list/propose/direct-edit/approve/reject/rollback tools.
- Route computation and state transitions.
- User approval task creation for user-routed proposals.
- Architect message/channel surfacing for engineer-originated proposals.
- Strict scoping:
  - Engineer self-only.
  - Architect own or hired engineers only.
  - No worker targets.
  - Dismissed architect write restrictions.
- Retry/idempotency classification.

Tests:

- Engineer can propose only own overlay and only with a hiring architect.
- Architect can govern only own/hired engineer overlays.
- Cross-architect engineer access denied.
- Worker targets rejected.
- Setting false vs true produces correct route.
- Architect direct edit respects setting.
- Idempotent proposal/approve/reject behavior.

#### 6. Add server/CLI operator commands

Files:

- `torque/server.py`
- `bin/torque`
- Possibly command routing/action rendering tests

Implement:

- Trusted approve/reject endpoints for user-routed proposals.
- Diff/list/show commands for CLI.
- Settings read/update command for `engineer_behavior_requires_user_approval`.
- Offline SQLite read support for list/show/diff where feasible.

Tests:

- CLI can list/show/diff versions and proposals offline.
- CLI/server approve/reject validates proposal id, expected hash/version, status, and staleness.
- User approval creates/applies version and active pointer.

#### 7. Phase 1 verification

Run targeted tests first, then full suite if practical:

- DB/persistence tests.
- Prompt tests.
- MCP scoping/retry tests.
- CLI command tests.
- Existing engineer/architect prompt regressions.

Manual smoke, from a non-worker shell only:

1. Start alternate test profile/port if needed.
2. Create/identify architect and hired engineer.
3. Engineer proposes overlay.
4. Architect approves with setting false; confirm next engineer launch includes overlay.
5. Toggle setting true.
6. Architect direct-edits engineer overlay; confirm user approval task is created and no prompt changes until user approval.
7. User approves via CLI; confirm next launch includes overlay.
8. Roll back to empty/default; confirm active pointer changes and versions remain.

### Phase 2 — required diff/timeline UI

**Recommended owner:** ui-ux / Panelsmith.
**Shippable state:** The user can review required rendered diffs and approve/reject in the UI; architect/engineer can inspect timelines and proposal state in panels.

#### 1. Add behavior state hydration and WS delta handling

Files:

- `static/js/ws.js`
- `static/js/state.js` or existing state modules
- Server snapshot payloads as needed

Implement:

- Store behavior overlay summaries and pending proposal summaries.
- Apply overlay deltas.
- Targeted surface invalidation only.

Tests:

- Frontend state regression for proposal/version/active update deltas.
- No unrelated panel full-rerenders on irrelevant overlay deltas.

#### 2. Add Behavior tab to agent panels

Files:

- `static/js/agent_panel.js`
- New optional `static/js/behavior_overlay.js`
- `static/style.css`
- `webview.html` script order update if new module

Implement:

- Engineer behavior tab.
- Architect behavior tab with own and hired-engineer governance sections.
- Timeline, active summary, pending proposals, diff entry points.
- Preserve focus/scroll/drafts.

Tests:

- Node frontend tests for render and rerender stability.
- Timeline active marker and proposal statuses update from deltas.

#### 3. Add user-facing Backlog diff approval UI

Files:

- Board/task rendering modules.
- Diff modal/module.
- Server command handlers as needed.

Implement:

- Detect `behavior-overlay-approval` tasks.
- Render “Review Dynamic Behavior diff” action.
- Modal with metadata, warnings, rendered diff, approve/reject controls.
- Structured approve/reject calls with expected hash/version.

Tests:

- Approval card appears for labeled task.
- Diff renders active-vs-proposed and rollback diffs.
- Approve/reject updates UI and terminal proposal status.
- Rerender preserves modal/draft note state.

#### 4. Add settings UI

Files:

- Existing group/settings modal modules.
- `static/style.css`

Implement:

- Toggle for `engineer_behavior_requires_user_approval` with default false and explanatory copy.
- Persist via existing settings command path.

Tests:

- Toggle persists and rerenders.
- Governance route in new proposals follows setting after update; existing proposals keep captured route.

### Optional Phase 3 — fast-follow enhancements

Only after Phase 1+2 are reviewed:

- Curated default seed if user rejects empty-start recommendation.
- Richer advisory lint categories.
- Worker overlay support if product direction changes; schema already supports agent-id keyed overlays, but v1 tool/prompt layers intentionally exclude workers.
- Search/filter across version history.

---

## 11. Test strategy summary

### Persistence

- Schema creation/migration idempotent.
- Version, proposal, active pointer, activation rows serialize/deserialize.
- Empty default seed behavior round-trips.
- Group setting default false and update persist.
- CLI offline reads work.

### Prompt composition

- Architect and engineer prompts append exactly one fenced overlay block as final section.
- Worker prompts have no v1 behavior overlay block.
- Base prompt text remains otherwise unchanged.
- Missing active overlay renders default empty block.
- Over-cap active overlay fails closed without truncation.
- Active pointer changes appear only when prompt is next rebuilt.

### Governance/state machine

- Architect own proposal always routes to user.
- Engineer proposal with setting false routes to architect and applies on architect approval.
- Engineer proposal with setting true routes architect then user.
- Architect direct engineer edit with setting false applies atomically and records audit proposal.
- Architect direct engineer edit with setting true routes to user before applying.
- Rejection by current actor is terminal.
- Stale-base approval fails and requires re-proposal.
- Idempotency keys prevent duplicate proposals.
- Rollback repoints active version without deleting versions.

### Scoping/security

- Engineers can read/propose only for self.
- Engineers without hiring architect cannot propose in v1.
- Architects can target self and hired engineers only.
- Worker targets rejected by tools/auth even though schema is kind-agnostic.
- Dismissed architect write tools blocked.
- User approval endpoints require trusted operator context and proposal metadata checks.

### Frontend Phase 2

- Behavior tab renders timelines, active state, proposals, and diffs.
- Backlog approval card renders required diff before approve/reject.
- WebSocket deltas update only relevant surfaces.
- Rerender preserves scroll/focus/caret/drafts/expanded state.
- Settings toggle persists and new proposals reflect it.

---

## 12. Risks and mitigations

1. **False confidence in “base authoritative.”**
   - Mitigation: document structural authority only; rely on governance and advisory lint for content risk.
2. **Prompt bloat.**
   - Mitigation: small hard size cap, reject overflow, fail closed at render.
3. **Governance ambiguity for engineer direct edits.**
   - Mitigation: persist route at proposal creation; both engineer-proposed and architect-authored engineer changes funnel through the same setting-gated branch.
4. **UI diff requirement delayed.**
   - Mitigation: Phase 2 is required, not optional. Phase 1 should not be presented as full product completion.
5. **State snapshot bloat.**
   - Mitigation: broadcast summaries only; load full text/diff on demand.
6. **Live-session mutation risk.**
   - Mitigation: active pointer changes only; prompt rebuilt at next launch/relaunch/session boundary.

---

## 13. Approval path

This plan requires **human/Torqly approval** before implementation because it introduces:

- New SQLite schema/migrations.
- New prompt-build behavior for architect and engineer agents.
- New governance setting and approval state machine.
- New MCP/CLI/server command surface.
- Required user-facing diff approval UI in Phase 2.
- A remaining product decision on default overlay seeding.

Recommended approval decision:

1. Approve the architecture and phased split.
2. Choose **Option A: start empty** for v1 default block.
3. Accept or adjust the recommended 4 KiB overlay text cap.
4. Dispatch Phase 1 to backend/orchestration-core only after those decisions are recorded.
5. Dispatch Phase 2 to UI/Panelsmith after Phase 1 exposes stable diff/proposal/version APIs.
