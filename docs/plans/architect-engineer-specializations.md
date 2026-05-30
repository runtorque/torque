# TORQUE:774 plan — architect-managed Engineer specializations

Status: design only. Build should wait for Courier review and architect sign-off.

## Decisions

1. **Tool name + semantics**
   - Add `architect_engineer_set_specializations`.
   - Semantics: **full replace**. Caller passes the complete ordered list; omitted/removed items are cleared.
   - Why not add/remove: ordering makes incremental APIs ambiguous (`add` where? how to change primary?); replace is idempotent, supports reorder/clear in one call, and matches existing replace-style fields such as labels/action vars and the internal `set_engineer_specializations` command.

2. **Ordering / primary / empty**
   - Preserve caller order exactly after normalization.
   - Slot 0 is the **primary specialization** and is the one rendered as `(primary)` by `SpecializationManager.render_engineer_preamble()` / `build_engineer_system_prompt()`.
   - Allow `[]`. Existing `AgentCell.engineer_specializations` and group defaults already default to empty; `[]` means a generalist/no-specialization Engineer and removes the specialization prompt block.

3. **Validation / dedupe**
   - Validate against the canonical 7 project slugs from `.torque/specializations/*.yaml` via `SpecializationManager`/a small helper in `specializations.py`; do **not** copy the taxonomy into the MCP tool spec or server handler.
   - Valid slugs today: `ui-ux`, `orchestration-core`, `runtime-pty`, `desktop-shell`, `worktree-release`, `prompts-config`, `quality-observability`.
   - Dedupe by keeping first occurrence and dropping later duplicates, preserving the caller's first primary choice. Drop blank strings.
   - Reject unknown slugs with a clear error that includes the valid set, e.g. `Unknown specialization(s): security. Valid specializations: ...`.
   - Important gotcha: the existing `_known_specialization_names()` includes user/global specializations too; architect-managed routing should use the project taxonomy helper so a local `~/.torque/specializations/*` file cannot become architect routing metadata by accident.

4. **Approval**
   - Hire-time specializations ride the existing pending-hire user approval.
   - Post-hire edits require **no fresh user approval**: this is routing metadata under the hiring Architect's authority over Engineers they hired.
   - A real change still emits an agent delta and updates the persisted field / rendered preamble on next preview or relaunch; do not silently mutate without UI/MCP visibility.

## Existing flow to thread through

- MCP `architect_engineer_hire` is declared in `torque/mcp_architect.py` and dispatched by `torque/mcp_tools_shared.dispatch_scoped_tool()` as `tool_name == "engineer_hire"`.
- That calls server command `architect_engineer_hire`, handled by `server._handle_architect_engineer_hire_command()`.
- The handler writes a pending row through `MatrixState.save_pending_hire_async()` → `TorqueDB.save_pending_hire_async()` → `pending_hires`, then `MatrixState._emit_pending_hire()` emits `pending_hire_upsert`.
- UI/user approval sends `pending_hire_approve`, handled by `server._handle_pending_hire_approve_command()`, which calls `_handle_add_engineer_command(..., hired_by_architect_id=architect.id)`.
- `server_agent.AgentLaunchService.create_agent_with_config()` persists `kind="engineer"` and `hired_by_architect_id`; this already binds ownership correctly.
- Current read surfaces already use the field:
  - `architect_engineer_list` returns `engineers[].specializations`.
  - `architect_board_summary(specialization_engineer_id=...)` filters by `engineer.engineer_specializations`.
  - `architect_task_create` warning checks `suggested_specialization not in assigned_engineer.engineer_specializations`.
  - `_build_cell_persistent_prompt()` / `_resolve_engineer_specializations_preamble()` render the Engineer specialization preamble.

## Implementation plan

### Phase 1 — canonical taxonomy + normalization

Files:
- `torque/specializations.py`
- `torque/server.py`
- `tests/test_specializations.py`

Steps:
1. Add a reusable helper such as `SpecializationManager.canonical_project_names(base_dir)` or equivalent module function that reads project `.torque/specializations` through the manager and returns the canonical slug list/set.
2. Amend/reuse `_normalize_engineer_specialization_selection()` so callers can pass the valid slug list and get:
   - list input required,
   - blanks removed,
   - duplicates dropped after first occurrence,
   - unknowns rejected with the valid set in the message.
3. Use group/project base dir for validation, not the requested Engineer working directory override.

### Phase 2 — hire-time carry-through

Files:
- `torque/mcp_architect.py`
- `torque/mcp_tools_shared.py`
- `torque/server.py`
- `torque/db_schema.py`
- `torque/db.py`

Steps:
1. Extend `architect_engineer_hire` schema with optional `specializations: string[]`.
2. Forward `specializations` from `mcp_tools_shared`'s `engineer_hire` branch to the `architect_engineer_hire` server command.
3. Add `pending_hires.requested_specializations TEXT NOT NULL DEFAULT '[]'` plus migration/ensure logic.
4. Encode/decode `requested_specializations` in `save_pending_hire()`, `load_pending_hire()`, and `load_pending_hires()`; preserve the value when resolving an existing row.
5. In `_handle_architect_engineer_hire_command()`, validate/normalize before `save_pending_hire_async()`. If invalid, return an error and create no pending hire.
6. Include normalized `requested_specializations` in pending-hire responses/deltas so the approval banner/list can show what will be approved.
7. In `_handle_pending_hire_approve_command()`, pass the saved list into `_handle_add_engineer_command()` as `specializations`.
8. Update `_handle_add_engineer_command()` to render the specialization preamble into the first Engineer prompt when `specializations` is present, and set/persist `cell.engineer_specializations` immediately after creation. Return the normalized list in the create result.

### Phase 3 — post-hire setter MCP tool

Files:
- `torque/mcp_architect.py`
- `torque/mcp_tools_shared.py`
- `torque/server.py`
- `tests/test_architect_scoping.py`
- `tests/test_mcp.py`

Steps:
1. Add tool spec `architect_engineer_set_specializations` with required `engineer_id` and `specializations` array.
2. In `mcp_tools_shared`, add `tool_name == "engineer_set_specializations"` for architects:
   - require `engineer_id`,
   - resolve via `_resolve_architect_hired_engineer()` (strictly `hired_by_architect_id == caller`),
   - call a server command with canonical engineer id and caller architect id.
3. Add server handler/command (e.g. `_handle_architect_engineer_set_specializations_command`) that repeats the scope check defensively, validates/normalizes, and full-replaces `cell.engineer_specializations`.
4. Persist via `state._db_save_agent(cell)` and emit via `state._emit_agent(cell)` only when the normalized list actually changes. No-op returns current state without extra noise.
5. Response shape:
   ```json
   {
     "type": "engineer_specializations",
     "engineer_id": "...",
     "specializations": ["ui-ux", "desktop-shell"],
     "primary_specialization": "ui-ux"
   }
   ```
   Optional but useful: include `valid_specializations` or `preamble_preview` if Panelsmith needs inline UI feedback; keep large full-prompt previews out of the default response.

### Phase 4 — deltas, serialization, prompt reflection

Files:
- `torque/state.py`
- `torque/db.py`
- `static/js/ws.js` only if Panelsmith implements same-cycle UI editing

Steps:
1. Reuse existing `AgentCell.engineer_specializations` and `agents.engineer_specializations` JSON persistence; no new agent column is needed.
2. Verify `state.to_dict()` already serializes the field via `asdict()` and DB load decodes it.
3. Use the existing `agent_upsert` delta; do not add a new broad delta type.
4. Confirm affected pull surfaces read current state on next call: `architect_engineer_list`, `architect_board_summary`, and `architect_task_create` mismatch warning.
5. Prompt behavior: no live restart/injection on post-hire edit. The updated field is used by `_build_cell_persistent_prompt()` on prompt preview/relaunch; hire-time creation includes it in the initial prompt.
6. Surface-invalidation rule for any UI follow-up: refresh the Engineer/Architect panel only when the focused agent is the changed Engineer or the focused Architect owns that Engineer via `hired_by_architect_id`. Do not unconditionally mark the engineer panel on every agent delta.

## Panelsmith UI API contract (note only)

- **Hire dialog**
  - Read valid options from the existing specialization list command / manager-backed response; do not hardcode the 7 in JS.
  - Present an ordered multi-select with up/down/remove controls; first item displays `(primary)`.
  - Submit `architect_engineer_hire({ name, command?, provider?, directory?, specializations? })`.
  - Send `[]` when the user intentionally wants no specialization.

- **Engineer panel editor**
  - Read current values from `architect_engineer_list().engineers[].specializations`.
  - Write with `architect_engineer_set_specializations({ engineer_id, specializations })`; the array is the complete replacement.
  - Handle errors exactly: non-list → `specializations must be a list`; unknown slug → message includes valid set; out of scope → `engineer not found in scope`.
  - Do not call the existing unscoped `set_engineer_specializations` from architect UI.

## Tests

- Hire request with `specializations` creates a pending hire containing the normalized ordered list, emits it in the pending-hire snapshot/delta, and approval creates an Engineer with `hired_by_architect_id` and the same ordered list.
- Invalid hire-time slug rejects before a pending row exists and lists the valid set.
- `architect_engineer_set_specializations` full-replaces, preserves order, dedupes first occurrence, allows `[]`, persists through DB reload, and updates `architect_engineer_list`.
- Scope enforcement rejects user-owned Engineers and Engineers hired by another Architect with `engineer not found in scope`.
- `architect_task_create` mismatch warning and `architect_board_summary(specialization_engineer_id=...)` reflect the updated list after the setter.
- MCP registration tests include the new architect tool and the hire schema argument.
- Pending-hire DB migration/default round trip covers older rows with no `requested_specializations`.
- Prompt tests assert slot 0 renders as primary and hire-time creation passes the rendered specialization block into the initial Engineer prompt.

## Approval path / open questions

- This is a small but user-visible Architect MCP/API and pending-hire persistence change, so **do not treat Engineer approval alone as final build approval**. Per TORQUE:774, Courier should review this plan and route it to the Architect for sign-off before implementation.
- No blocking design questions remain. Optional future follow-up: decide whether Architects also need a read-only `architect_specializations_list` MCP tool; this plan keeps scope to hire-time + setter and relies on manager-backed UI reads plus validation errors.
