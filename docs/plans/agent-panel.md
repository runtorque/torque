# Agent Panel — Implementation Plan

## Motivation

The right-hand panel used to be called "Architects & Engineers" and was rendered by `renderWeaverPanel()` in `static/js/weaver.js:577`. It was built when there was exactly **one Weaver per group**, so it was scoped to the group and mixed two concerns:

1. **Roster surface** — lists every architect and engineer in the group, with inline "+ Add Architect" and "+ Add Engineer" buttons (see `static/js/weaver.js:629-630`) plus the architect decision-log controls.
2. **Weaver detail view** — Journal, Events, and Worklog tabs, all keyed by group (`state.weaver_worklog[group]`, `state.weaver_sent_events[group]`, `state.weaver_journal[group]`, `state.weaver_buffer_stats[group]`).

Since the Agent Kinds refactor ([agent-kinds-refactor.md](agent-kinds-refactor.md)) we now have **multiple engineers per group**, plus architects and workers. The group-scoped detail view no longer makes sense: there is no single "the engineer" to show a journal for. The panel has to become **agent-scoped** — when the user focuses an agent in the sidebar, the panel shows that agent's context.

This plan refactors the panel into a per-agent **Agent Panel**, moves the creation buttons into the grid's existing `+ New` split button, and defers a bird's-eye "Team" overview to a later iteration.

## Design principles

1. **One agent, one panel.** The panel binds to the currently focused agent (`focusedItemId`) and renders sections based on `agent.kind`. No group-level mixing in the default view.
2. **Creation lives where agents live.** Architect / Engineer creation moves into the agents grid's `+ New` split button (`renderSplitBtn` in `static/js/render.js:72`), alongside the existing role/template entries and replacing the retired Weaver entry. The roster table disappears from the panel.
3. **No server-side churn in phase 1.** `state.weaver_worklog`, `state.weaver_sent_events`, `state.weaver_journal`, and `state.weaver_settings` remain keyed by group. The client filters at render time to the focused engineer's group. Re-keying by `agent_id` is tracked as a follow-up, not a blocker.
4. **Retire the Weaver UI surface.** The lingering "Weaver" menu entry (`_renderWeaverMenuItem` in `static/js/render.js:104`) and the `newWeaver` code path are removed in the same pass. The CLI `weaver_*` tool surface is already gone per CLAUDE.md invariants; this closes the last UI gap.
5. **Graceful empty states.** No focused agent → a prompt to pick one from the grid. Focused agent has no data yet (freshly created engineer, worker with no events) → a per-tab empty message instead of a blank pane.
6. **Preserve operator context.** The rerender guardrail in CLAUDE.md still applies — scroll anchor, focused element, inline drafts, and expanded sub-rows must survive WebSocket rerenders. The existing `_captureSurfaceState` / `_restoreSurfaceState` helpers keep working against the new panel root.

## Kind → sections matrix

| Kind      | Tabs                                      | Notes                                                                                                         |
|-----------|-------------------------------------------|---------------------------------------------------------------------------------------------------------------|
| architect | **Decisions**, **Hired engineers**, **Messages** | Decisions = current architect's entries only. Hired engineers = engineers with `hired_by_architect_id == architect.id`. Messages = `architect_*` / `engineer_*` thread. |
| engineer  | **Journal**, **Events**, **Worklog**             | Same three tabs the panel shows today, but filtered to this engineer (see §"Filtering strategy").             |
| worker    | **Events**, **Worklog**                          | Worklog = this worker's task history (assigned + completed). Events = this worker's own event stream (per-cell events, not weaver digest). |
| terminal  | (none — simple status stub)               | Terminals don't have journal/events/worklog semantics. Panel shows `cell.name`, branch, process, and a hint to open the drawer. |

Sub-sections inside each tab (queued vs sent events, journal subviews, etc.) stay as they are today — the refactor only changes how the data is scoped and labeled, not the layout of each individual tab.

### Worker Worklog semantics

For a worker, Worklog is its **task history**: every board task where `task.agent_id == worker.id`, newest first, showing title, lane, status, dispatched-at, and the action used. This re-uses the existing worklog row template in `_weaverRenderWorklogItem` (`static/js/weaver.js:1144`); only the data source changes.

### Worker Events semantics

For a worker, Events means its own activity stream — the per-cell events already surfaced in `static/js/events.js`. We reuse that renderer here rather than the Weaver digest queue renderer. Concretely: the "Events" tab for a worker delegates to the same function that powers the existing Events panel, filtered to this agent's cell ID.

For an engineer, Events keeps its current meaning: the digest queue (queued + sent) for this engineer's group. Sharing the label "Events" across kinds is acceptable because the selection context (kind badge in the header) makes the difference obvious.

## Concrete changes

### Frontend

**New file `static/js/agent_panel.js`.** Replaces `static/js/weaver.js` as the panel renderer. The weaver file is deleted in the same commit — we are not maintaining two parallel panels. Public surface:

- `renderAgentPanel()` — called from `render()` in `static/js/render.js` wherever `renderWeaverPanel()` is called today.
- `agentPanelSelectTab(tab)` — tab switch; persists last-selected tab per agent-kind combo in a module-local dict.
- Internal: `_renderArchitectPanel(agent)`, `_renderEngineerPanel(agent)`, `_renderWorkerPanel(agent)`, `_renderTerminalPanel(agent)`, `_resolveFocusedAgent()`.

**DOM root.** The panel uses `#panel-agent`. `static/js/panel_manager.js` and CSS selectors target the neutral `agent-panel-*` prefix.

**Split-button menu changes (`static/js/render.js`).** Inside `+ New` for each group:

- Add a menu entry "Architect" → calls `openAddArchitectModal(group)`.
- Add a menu entry "Engineer" → calls `openAddEngineerModal(group)`.
- Remove `_renderWeaverMenuItem` and its call site. Delete `newWeaver` and any helpers it drags in.
- Keep the existing role/template entries unchanged; Architect and Engineer slot in above them with a separator.

**Focus plumbing.** `focusedItemId` (already maintained in `static/js/ws.js`) is the source of truth. When it changes, `renderAgentPanel()` rerenders. The WebSocket `focus_update` delta op already triggers a rerender path; no new op is needed.

**Empty state.** If `focusedItemId` is falsy or resolves to nothing, panel body is a single `.agent-panel-empty` block with copy "Select an agent from the grid to see its context." (No roster shown.)

### Server / Python

No data-model changes required for phase 1. The filtering is purely client-side.

Server cleanup that goes with the UI retire:

- Delete the `new_weaver` command handler if still wired up.
- Keep `weaver_*` state stores in place for now — they are still populated and still carry valid per-engineer data (the engineer *is* the weaver semantically). The follow-up plan (§"Follow-ups") re-keys them.

### Filtering strategy (phase 1)

For an engineer, the panel reads from the group-keyed stores (`state.weaver_worklog[agent.group]`, etc.) and filters to entries whose `agent_id` / `created_by_*` matches the focused engineer where a per-agent distinction exists. Where the store is inherently per-group (e.g. digest queue), we show the group's data but label it "for group *<name>*" so the user understands the scope.

For a worker, Worklog reads `state.board_tasks` directly, filtered by `agent_id == worker.id`. No dependency on the weaver stores.

For a worker, Events reuses the per-cell event log (`state.events[cell.id]` or equivalent — confirm exact shape during implementation; `static/js/events.js` already reads this).

For an architect, the Decisions section reads from the architect-specific decision store that the current panel already uses (`_weaverArchitectDecisionUi`, backed by delta ops). Filtering is by `architect_id`.

## Migration & rollout

**Phase 1 (this plan).**

1. Move `+ Add Architect` / `+ Add Engineer` into the `+ New` split-button menu. Retire the "Weaver" menu entry and `newWeaver` code path.
2. Replace `renderWeaverPanel` with `renderAgentPanel`, binding to `focusedItemId`.
3. Implement the four kind renderers (architect / engineer / worker / terminal) reusing the existing tab body renderers where possible.
4. Delete `static/js/weaver.js`. Keep the neutral `#panel-agent` root and `agent-panel-*` CSS prefixes.
5. Verify rerender guardrails: scroll anchor, focus, inline drafts survive WebSocket deltas on each kind.

**Phase 2 (follow-up, out of scope here).**

- Re-key `weaver_worklog` / `weaver_sent_events` / `weaver_journal` / `weaver_buffer_stats` by `agent_id` server-side. At that point the engineer panel shows only its own data, not a group-scoped projection.
- Re-introduce a "Team" overview — either as a toggle on the panel header (Agent ↔ Team view) or as a separate panel reachable from the group header. Defer design until we feel the lack of it.
- Audit the `weaver_*` SQL tables and column names; rename to `engineer_*` to match terminology, with migration.

## Testing plan

Manual (no automated tests per CLAUDE.md):

1. `make deploy` → restart from Scripts menu → open toolbelt.
2. In a group with no agents, confirm empty state in the Agent Panel.
3. Click `+ New` on the grid → confirm menu now lists Architect, Engineer, roles, and no Weaver. Create an architect, an engineer, and a worker.
4. Focus each kind in turn and confirm the correct tab set renders:
   - Architect: Decisions + Hired engineers + Messages populate.
   - Engineer: Journal entries from CLI `loom ai` or MCP `engineer_*` show up. Events and Worklog render.
   - Worker: dispatch a task; confirm Worklog shows it. Trigger a few events (activity, blocked) and confirm Events shows them.
   - Terminal: confirm the stub renders without errors.
5. Delete the architect → confirm engineers fall back to "user-owned" in the grid, panel for a user-owned engineer still works.
6. Open two browser clients (`make open` alongside toolbelt) and verify focused agent syncs across clients via `focus_update`.
7. Rerender guardrail: scroll the Worklog partway, trigger a WebSocket delta (e.g. dispatch another task); confirm scroll and focused element survive.

## Open questions

- **Messages tab for architects.** The thread surface between architects and their hired engineers exists in the data model per CLAUDE.md, but we haven't yet nailed the UI for it. Phase 1 renders a read-only list; a full composer is a separate task.
- **Terminal panel.** A pure status stub is the simplest thing. If it feels too empty in practice, the next iteration can add a miniature command history or a "dispatch-into-this-terminal" affordance. Flagged for feedback after we ship phase 1.
- **Per-kind tab memory.** Should "last selected tab" be remembered per agent, per kind, or globally? Simplest is per kind (one Events/Worklog/Journal memory per kind), at the cost of losing the tab when you bounce between workers. Start there; revisit if it's annoying.
