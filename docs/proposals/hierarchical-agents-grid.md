# Proposal: Hierarchical Agents Grid

**Status**: Draft proposal
**Audience**: product, frontend UI (Panelsmith), backend/state
**Primary goal**: replace the current flat sequential ordering of the agents grid with an explicit two-dimensional hierarchy that makes architect → engineer → worker ownership visually unambiguous, supports detached user-owned workers as first-class citizens, and prepares the grid for near-future detach/reparent flows.

Supersedes the visual-hierarchy approach shipped in LOOM:72 (sequential ordering with indentation + typographic cues). That was a good temporary solution; this proposal is the durable replacement.

---

## Executive summary

Today the agents grid renders every cell (architects, engineers, workers, terminals) in a single vertical stream ordered by kind and parent relationship, with typographic hierarchy (font weight, indentation, subtle left borders) carrying the ownership information. That works when there are 1–2 architects and a handful of engineers. It breaks down as the group fills up: operators cannot tell at a glance which engineer a given worker belongs to, nor which architect owns a given engineer, without tracing the indentation line by line.

The proposed model replaces sequential ordering with a **two-dimensional grid**:

- **Architect sections** stack vertically. Each section is owned by one architect (or the synthetic "User" owner at the top).
- Inside a section, a short **architect header row** holds the architect card on the left and section-level controls (`+ New Engineer`, status pills) inline.
- Below the header, each **engineer row** contains the engineer card on the left followed by that engineer's workers, left-aligned, wrapping to the next line when they overflow the row width.
- A **loose-workers strip** sits above the engineer rows in the User section, holding user-owned detached workers as first-class cards that do not belong to any engineer.
- Horizontal dividers separate architect sections.
- Terminals render inside their parent agent's drawer (unchanged from today).

The central simplification is:

- **rows encode ownership**: the cell to the left owns the cells to the right.
- **sections encode the architect boundary**: a horizontal divider is a hard ownership break.
- **loose strips encode "no parent"**: a row of bare worker cards with no leading agent means these workers report to no engineer.

That lets an operator answer three questions instantly — _who owns this worker?_, _who owns this engineer?_, _which agents belong to no one?_ — by purely spatial reading, without color, without borders, without reading labels.

---

## Problem statement

The designated engineer operating Loom today routinely asks questions like:

- Which engineer does this worker belong to?
- How many workers does Courier currently own?
- Is this detached worker mine, or is it orphaned from a dismissed engineer?
- Which architect is this engineer hired by?
- Where do I click to add an engineer _to this specific architect_?

With the current flat grid these questions require reading labels, tracing indentation, or remembering the kinds-refactor invariant (`owner_engineer_id`, `hired_by_architect_id`, `created_by_architect_id`). As group density grows — multiple architects, multiple engineers per architect, multiple workers per engineer, plus detached user-owned workers — the sequential layout collapses under its own ambiguity.

Two related problems the sequential layout can never solve on its own:

1. **The "+ New" button is ambiguous.** Clicking `+ New` today opens a kind picker; there is no way to express "add an engineer _to this specific architect_" or "add a worker _to this specific engineer_" directly from the grid. Affordance lives in a menu, not in space.
2. **Detached agents have no home.** User-owned workers with no engineer parent render inline with their peers, indistinguishable from engineer-owned workers except by absence of an indentation line. This will get worse when we allow detaching engineers from architects and workers from engineers (see Near-future evolution below).

---

## Design principles

1. **Containment over typography.** Ownership should be expressed by physical containment in a row or section, not by font weight, indentation, or color. Colors and labels remain as secondary cues, not load-bearing.
2. **One read, one answer.** An operator should be able to answer "who owns this?" by looking at what is to the left, what is above, and nothing else.
3. **Controls live where their effect lands.** A `+ New Engineer` button lives in the architect header row it will affect. A `+ New Worker` button lives in the engineer row it will affect.
4. **Detached agents are first-class, not residual.** User-owned workers without an engineer parent get their own visible strip, not a silent fallback slot.
5. **Preserve today's drawer model for terminals.** Terminals are per-agent, open inside the parent agent's drawer. The grid change is about agent ownership, not terminal surfacing.

---

## The grid shape

### Sections

The grid is a vertical stack of **architect sections**, separated by horizontal dividers. Two kinds of section exist:

- **User section** (synthetic, always present, always first): holds engineers hired by the user (no architect parent) and user-owned detached workers.
- **Architect sections** (one per architect, ordered by creation): hold engineers hired by that architect.

A section is composed of:

1. Optional **loose-workers strip** (User section only in v1): a single row of user-owned detached worker cards. Omitted when empty.
2. An **architect header row** (full grid width): architect card on the left, controls inline to the right. For the User section, this header renders a synthetic "User" card.
3. Zero or more **engineer rows** (full grid width): engineer card on the left, workers wrapping to the right, section-level `+ New Worker` affordance at the end of the last worker (though worker creation from the UI remains engineer-driven — see Creation semantics below).
4. A **`+ New Engineer`** affordance inline in the architect header row (one per section).
5. A `+ New Architect` button below the last architect section.

### Row shape

Every engineer row reads left-to-right as:

```
[ engineer card ] [ worker 1 ] [ worker 2 ] [ worker 3 ] ...
                  [ worker 4 ] [ worker 5 ] (wrapped)
```

The engineer card is always the first card in the row and does not wrap. Worker cards wrap to the next line within the row, left-aligned flush with the first worker (not indented under the engineer card — the engineer card is a fixed anchor, not a parent column header). A subtle horizontal rule separates one engineer row from the next inside the same section.

The loose-workers strip reads left-to-right as:

```
[ worker ] [ worker ] [ worker ] ...
```

No leading agent card. The absence of a leading card is the visual signal that these workers have no engineer parent.

### Sketch

```
┌───────────────────────────────────────────────────────────────┐
│ User section                                                  │
│                                                               │
│   Loose-workers strip:                                        │
│     [worker] [worker]                                         │
│   ─────────────────────────────────────────                   │
│   [User]  ( + New Engineer )                                  │
│     [Engineer 1] [worker] [worker]                            │
│     ─────────────────────────────                             │
│     [Engineer 2] [worker]                                     │
│                                                               │
├─ ─ ─ architect section divider ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┤
│                                                               │
│ Architect 1 section                                           │
│                                                               │
│   [Architect 1]  ( + New Engineer )                           │
│     [Engineer 1] [worker] [worker]                            │
│     ─────────────────────────────                             │
│     [Engineer 2] [worker] [worker] [worker] [worker]          │
│                  [worker]  (wrapped to next line)             │
│     ─────────────────────────────                             │
│     [Engineer 3] [worker]                                     │
│                                                               │
├─ ─ ─ architect section divider ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┤
│                                                               │
│ Architect 2 section                                           │
│                                                               │
│   [Architect 2]  ( + New Engineer )                           │
│     [Engineer 1] [worker] [worker]                            │
│                                                               │
├───────────────────────────────────────────────────────────────┤
│   ( + New Architect )                                         │
└───────────────────────────────────────────────────────────────┘
```

---

## Creation semantics

The grid's creation affordances align with the ownership rules:

- **`+ New Architect`** (bottom of grid): creates a new architect at group scope. Opens the existing architect-creation modal.
- **`+ New Engineer`** (inline in an architect header row): creates an engineer hired by that specific architect, or for the User section, a user-created engineer. The architect context is implicit from the section the button sits in — no more disambiguation modal step.
- **Worker creation remains engineer-driven**: an engineer creates workers attached to itself via `loom ai derive` or engineer MCP surfaces. The grid does not expose a direct `+ New Worker` button on engineer rows in v1. Rationale: per the kinds-refactor invariants, workers are engineer-dispatched; surfacing a grid button would invite the wrong mental model ("users create workers for engineers"). We may revisit this in a follow-up if engineers want a manual worker-spawn affordance.
- **Detached worker creation**: a dedicated `+ New Worker` button lives in the User section, anchored to the loose-workers strip. This is the only path through which a user creates a worker directly; it always produces a detached (no-engineer) worker. Users cannot create a worker under an engineer they did not hire, and cannot create a worker under an engineer at all — the engineer owns that surface.
- **Terminal creation**: unchanged from today. Terminals attach to any agent kind (architect, engineer, worker) and render in that agent's drawer. A terminal is never a first-class grid cell in this proposal.

---

## Loose-workers strip

The loose-workers strip is a horizontal row of worker cards appearing above the engineer rows inside the User section. It exists for one reason: user-created detached workers have no engineer parent, and forcing them into a pseudo-engineer row would either (a) lie about ownership or (b) require a placeholder card.

Strip behavior:

- Always rendered in the User section, regardless of whether detached workers exist. When empty, the strip is a single-button row holding only the `+ New Worker` affordance.
- Cards wrap to multiple lines within the strip if needed.
- A `+ New Worker` button sits at the end of the strip (or as the strip's sole content when empty).
- Workers in the strip carry the full worker-card affordances (right-click context menu, focus, dispatch target, remove).
- Visually distinguishable from engineer rows by the absence of a leading agent card. No special color needed.

**v1 scoping decision:** only the User section carries a loose-workers strip. Architect sections cannot hold detached workers in v1 because architects cannot create workers directly — workers are engineer-owned. If a future feature allows architects to own detached workers, the strip primitive extends naturally to architect sections.

---

## Data model

Ownership is already captured in the kinds-refactor invariants; this proposal does not require schema changes. The render layer groups by existing fields:

- Top-level grouping key: `hired_by_architect_id` on engineers. Engineers with a null / missing value render in the User section.
- Second-level grouping key: `owner_engineer_id` on workers. Workers with a null / missing value and `kind == worker` + user-owned render in the User section's loose-workers strip.
- Terminals: grouped by `parent_id` into the parent agent's drawer (no change).

Invariant: every worker either has an `owner_engineer_id` (appears in that engineer's row) or is user-owned (appears in the loose-workers strip). There is no third state.

Sort order within a section:

- Engineers within a section: ordered by creation timestamp ascending (matches today's implicit ordering).
- Workers within an engineer row: ordered by creation timestamp ascending.
- Loose-workers strip: ordered by creation timestamp ascending.
- Sections themselves: User section first, then architect sections by creation timestamp ascending.

---

## Interaction behaviors

### Selection and focus

- Clicking any card selects it and surfaces its drawer (terminals) or detail (architect/engineer/worker status).
- Keyboard navigation preserves today's mental model: arrow keys move within a row, up/down move between rows of the same section, Tab/Shift+Tab move between sections. Horizontal overflow (many workers in a row) scrolls the row horizontally when the cursor moves past the visible edge.
- Focus preservation across WebSocket rerenders follows the existing rerender-guardrail pattern.

### Drag-and-drop

Out of scope for v1. Drag-and-drop reparenting is the near-future evolution described below, and should not be conflated with the grid layout change. The render-level change should ship first so the visual model is in place before interaction primitives are added on top.

### Right-click context menus

- Architect card: existing controls + `+ Hire engineer` shortcut (equivalent to clicking `+ New Engineer` in the header row).
- Engineer card: existing controls (dismiss, rehire, broadcast, remove). Dismissed engineers render greyed in-place.
- Worker card: existing controls.
- Loose-workers strip (empty area): `+ New Worker` shortcut.
- Section divider: no menu in v1 — reserved for future section-level actions.

### Horizontal overflow

Engineer rows wrap workers to multiple lines, which grows vertically. This is the preferred behavior — operators already scroll the panel vertically, and wrapping avoids the need for per-row horizontal scroll controls.

The loose-workers strip follows the same wrap-to-next-line rule.

---

## Near-future evolution

This proposal is designed to accommodate two planned features without further layout changes:

1. **Detach engineer from architect → user ownership**: the engineer's row moves from the architect section to the User section. Layout primitive already handles this — just a re-grouping on the next render.
2. **Detach worker from engineer → user ownership**: the worker card moves from the engineer's row to the User section's loose-workers strip. Layout primitive already handles this — same re-grouping.

In both cases, the grid responds to ownership-field changes without needing a new visual primitive. The underlying interaction (drag-to-detach, right-click → "Detach from owner", etc.) is the scope of the follow-up feature.

A further future extension, out of scope here: allowing detached engineers/workers under an architect other than the original one (i.e., reparent, not just detach). The layout already supports this — engineers render wherever their `hired_by_architect_id` points, workers wherever their `owner_engineer_id` points. Only the interaction needs to exist.

---

## Non-goals (v1)

- Drag-and-drop reparenting.
- Direct worker creation under an engineer from the grid (stays engineer-side).
- Architect-owned loose-workers strips.
- Color-coding architects to their engineers (containment carries the signal).
- Collapsing/expanding sections or rows (can be added later if density demands it — unlikely at current group sizes).
- Multi-select across cells.
- Reordering engineers or workers by drag within a row/section.
- Changing terminal surfacing (stays in drawer).

---

## Success criteria

The redesign is successful when:

1. An operator can answer "who owns this worker?" in one glance, without reading labels or counting indentation.
2. Clicking `+ New Engineer` inside an architect section creates an engineer hired by that architect with no kind-picker step.
3. User-owned detached workers have a stable, visible home above the User section's engineer rows.
4. A detached engineer (future feature) renders under the User section on next render with no additional layout code.
5. Panelsmith's rerender-guardrail tests cover scroll/focus/inline-draft preservation for each of the three new row shapes (loose strip, architect header row, engineer row).
6. Keyboard navigation flows correctly across the new two-dimensional grid — arrow keys within a row, up/down across engineer rows, Tab across sections.

---

## Risks and open questions

### Risks

- **Horizontal density at many workers per engineer.** An engineer with 10+ workers will wrap to many lines inside its row, making the row visually tall. Mitigated by: (a) keeping worker cards compact, (b) wrapping is vertical growth, which is already the grid's native scroll axis. If this becomes a pain point, a per-row collapse toggle is a cheap follow-up.
- **Layout flicker during rerenders.** The grid currently rerenders on each WebSocket delta. The new two-dimensional layout has more moving parts (sections, rows, strips). Must be covered by the existing rerender-guardrail pattern plus new regression tests for the new row shapes. Panelsmith's `feedback_ui_review_block_pattern` memory is the standing guidance here.
- **Loose strip discoverability.** An operator who has never created a detached worker may not know the strip exists when empty (since it's hidden). Acceptable — when empty, there are no detached workers to find, and the `+ New Worker` button in the user section's controls still exists as the entry point.

### Resolved decisions

- **Loose-workers strip is always rendered in the User section** (not hidden when empty). The `+ New Worker` button sits inside the strip; when there are no detached workers, the strip is a single-button row. This keeps the strip as a stable discoverable surface and makes "detached workers live here" a constant truth of the grid.
- **Dismissed engineers stay in-place** inside their architect section, rendered greyed. Row position does not reshuffle on dismiss/rehire. The greyed state is the sole signal.
- **Section order is User-first, then architects by creation timestamp ascending.** Stable across sessions; no activity-based reordering in v1.

---

## Rollout

Phasing, each landing as an independent PR:

1. **Data grouping + new DOM shapes**: render-layer grouping by `hired_by_architect_id` and `owner_engineer_id`, plus the three row primitives (architect header, engineer row, loose strip). No interaction changes.
2. **`+ New Engineer` in architect header**: wire the creation flow to the architect the button sits in.
3. **Loose-workers strip + `+ New Worker` detached**: user-owned detached worker surface.
4. **Keyboard navigation update**: two-dimensional traversal across sections, rows, and strips.
5. **Rerender-guardrail regression tests** (bundled with each of 1–4 where relevant; no standalone PR).

LOOM:72's current typographic hierarchy remains in place until step 1 lands, then is removed (visual classes retired) as part of step 1's diff.

Near-future evolution (detach flows) is a separate proposal/spec; this proposal intentionally stops at the render layer so the visual model is in place first.
