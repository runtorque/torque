# React UI audit and improvement backlog

**Status:** P1 and P3 implemented; P2 remains proposed
**Audit date:** 2026-08-12
**Scope:** React/Vite UI served by the standalone daemon, using an isolated `ui-audit` profile. The audit covered Board, Agents, Planning, Control Center, Inbox, global navigation, dialogs, keyboard interaction, and a compact viewport.

## Outcome

The React UI is ready for continued daily-use testing. The audit fixed the interaction and data-integrity defects discovered while creating and editing real records, and it leaves a smaller set of mostly information-architecture and visual-density improvements for deliberate product decisions.

## Fixed during the audit

### Board

- Card dragging starts from unused card space while title, description, owner, and label text remain selectable only over their rendered glyphs. This includes the empty space to the right of task IDs and task text.
- Large lanes progressively mount tasks instead of rendering hundreds of cards at once.
- Each lane owns its vertical scroll, dividers fill the available board height, and lane widths no longer grow with long task titles or labels.
- Cross-lane hit testing tolerates divider gaps, and same-lane reordering uses stable card geometry.
- Single clicks open a task without exposing bulk controls; Shift/Ctrl/Command selection activates multi-select.
- New-task controls live at the top of lanes, while the global New task button opens the full create dialog.
- Task details hydrate before editing and reject malformed JSON objects rather than overwriting durable fields with stale or invalid values.
- Archive counts, empty states, actions, and restore precedence now reflect the active/archive scope correctly.
- Duplicate action and role options are removed, and quick filters expose pressed state to assistive technology.

### Agents and terminals

- Standalone terminals appear in the hierarchy, contribute to header counts, participate in keyboard navigation, and can be selected as first-class workspace cells.
- A selected standalone terminal opens its PTY without showing agent-only Inspect, Settings, worktree, or direct-message controls.
- The empty Agents layout no longer depends on a hidden agent selection to expose a terminal.

### Planning

- Relationship selectors have accessible names.
- Read-only Hires & journals and Schedules sections no longer show a nonfunctional New button.
- Architect decisions require an Architect before creation.
- Scratchpad and Idea Brief structured fields reject invalid JSON with an inline error instead of sending a string in place of an object or list.

### Control Center and shell

- Agent and terminal counts are reported separately; the Mission metric excludes terminals from the agent count.
- Restart-supervisor and terminate-session actions use explicit in-app confirmation dialogs.
- Action definitions reject invalid array/object JSON instead of silently ignoring Save.
- Duplicate built-in/project action rows are removed.
- The sidebar no longer produces a stray horizontal scrollbar, and compact layouts retain access to every primary product area.

## Prioritized improvement backlog

### P1 — improve core-task clarity (implemented 2026-08-12)

1. **Recompose task details.** Implemented as a wide two-column primary editor with persistent status/actions and independently scrolling Execution, Verification, Integrations, and Evidence tabs.
2. **Simplify the empty Agents workspace.** Implemented as one centered, action-oriented onboarding state; hierarchy and focused workspace panes appear only when a selectable cell exists.
3. **Make command search real.** Implemented with fuzzy filtering over labels and keywords, Arrow-key result navigation, Enter execution, Escape dismissal, result counts, and an explicit empty-result state.

### P2 — reduce density and long-page fatigue

1. **Establish Board action hierarchy.** Group lower-frequency header actions into an overflow menu and preserve New task, search, and view switching as the primary controls. Define the compact breakpoint behavior explicitly.
2. **Progressively disclose Board filters.** Collapse empty facets, make selected values visually dominant, and keep long labels from overwhelming the saved-view dialog.
3. **Break up Control Catalog.** Replace the giant nested editor with selection-first navigation and a dedicated detail surface. Keep raw JSON as an expert mode rather than the default visual weight.
4. **Collapse advanced settings.** Dedicated fields should remain the primary Settings surface; place the four raw JSON documents behind an Advanced disclosure with unsaved/error indicators.
5. **Make Help search-first.** Show compact topic summaries and filter before rendering all topic bodies. Consider progressive mounting if the catalog grows.
6. **Raise microcopy readability.** Audit the 8–9px muted labels and footer/status text for minimum readable size and contrast in dark, light, and high-contrast themes.

### P3 — polish and consistency (implemented 2026-08-12)

1. Shared card padding, content-gap, and metadata-gap tokens now normalize Board, Agents, Planning, and Control cards.
2. Empty Board lanes, Agents, Planning collections, Mission Control sections, and command results now provide concise next-action guidance.
3. Long Board labels, filter values, saved views, Planning cards, agent task/branch metadata, task selectors, and Mission Control chips truncate safely while exposing their complete value through accessible text and a native title tooltip.
4. Aggregate panel counts now appear in panel headers; lane or collection counts remain in their local section headers; navigation tabs do not duplicate counts. The Board footer is reserved for interaction guidance.

## Recommended sequence

1. Use the implemented P1/P3 surfaces for normal work and collect follow-up friction.
2. Consolidate Board header/filter behavior and compact breakpoints.
3. Refactor Control Center density after the interaction model is proven; avoid styling a catalog structure that is likely to change.
4. Finish the P2 typography, theme, Help, and advanced-settings work.

## Coverage and remaining risk

- The audit used a real isolated standalone daemon and real Board, Planning, group, saved-view, and PTY records.
- A compact browser viewport was tested, but the native Tauri window and detached-window behavior were not exercised in this pass.
- Provider-backed agent dispatch, external board sync, Relay, secrets, destructive session termination, and daemon restart were intentionally not executed.
- Dark theme received the primary visual pass. Light and high-contrast themes still need a dedicated manual contrast review.
