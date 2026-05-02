# Proposal: Hierarchical Agents Grid

**Status**: Draft proposal
**Audience**: product, frontend UI (Panelsmith), backend/state
**Primary goal**: replace the current flat sequential ordering of the agents grid with an explicit two-dimensional hierarchy that makes architect → engineer → worker ownership visually unambiguous, supports detached user-owned workers as first-class citizens, and prepares the grid for near-future detach/reparent flows.

Supersedes the visual-hierarchy approach shipped in TORQUE:72 (sequential ordering with indentation + typographic cues). That was a good temporary solution; this proposal is the durable replacement.

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

The designated engineer operating Torque today routinely asks questions like:

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

1. A **fixed-width architect column** on the left: a single fixed-width + fixed-height anchor card for the architect (or the synthetic "User" card in the User section). The card **row-spans** across all of this section's engineer rows — it is not repeated per engineer and it is not a header row sitting above the engineers.
2. One or more **engineer rows** stacked vertically to the right of the architect column, inside the section. Each engineer row reads left-to-right as: `[ engineer card ] [ worker 1 ] [ worker 2 ] [ worker 3 ] ...` with workers wrapping within the same row when they overflow horizontally (flush-left with the first worker, NOT indented under the engineer card — the wrapping stays inside that engineer's row and does not visually bleed into the next row).
3. A **`+ New Engineer` ghost card** at the bottom of the section's engineer-row stack (in the right column, aligned to the engineer-row left edge). Ghost card = dashed outline, same width as a real engineer card, 1/4 the height of a real engineer card, text label "+ New Engineer". Clicking it creates an engineer hired by this architect.
4. In the User section only: a **loose-workers strip** rendered above the User section's engineer rows. Always rendered — if empty, it is a single-ghost-card row holding only the `+ New Worker` ghost card.
5. A **`+ New Architect` ghost card** inside the User section, positioned in the architect column directly below the User architect card (before the User-to-Architect-1 section divider). Same dashed-outline ghost-card style: same width as a real architect card, 1/4 height, text label "+ New Architect". Semantically it belongs to the User section because architects are user-created only (per the kinds invariants); visually this means the User section's left column is taller than just the User card, extending down to include the ghost.

Section boundaries are horizontal dividers that span the full grid width.

### Column structure

The grid has two logical columns:

- **Left column (fixed width)**: holds architect cards. Every architect card — plus the synthetic User card — aligns to the same left edge and the same fixed width. This keeps the architect spine visually consistent no matter how many engineers each architect has.
- **Right region (flex width)**: holds engineer rows for the current section. Engineer rows stack vertically. Within each engineer row, the engineer card is first, and workers flow right of it and wrap inside that row.

The architect card itself has a **fixed height** — it does NOT grow to match the height of its section's engineer rows. When an architect has many engineers, the right column's engineer stack may be much taller than the architect card; the architect card stays at its natural compact height at the top-left of the section, and the space below the architect card (still inside the left column) stays empty. This matches the fixed-width constraint — both dimensions of the architect card are stable across sections, regardless of how many engineers or workers that architect owns.

### Row shape

Every engineer row reads left-to-right as:

```
[ engineer card ] [ worker 1 ] [ worker 2 ] [ worker 3 ] ...
                  [ worker 4 ] [ worker 5 ] (wrapped inside the same row)
```

The engineer card is the fixed anchor on the left of that row. Worker cards wrap to the next line **within the same engineer row** — flush-left with the first worker, not under the engineer card, and never spilling into the next engineer's row. A subtle horizontal rule separates one engineer row from the next inside the same section.

The loose-workers strip reads left-to-right as:

```
[ worker ] [ worker ] [ worker ] ... [ + New Worker ]
```

No leading agent card. The absence of a leading card is the visual signal that these workers have no engineer parent.

### Sketch

Notation in the sketch: real cards are drawn with solid boxes (`[Name]` / `┌─┐ │ └─┘`), ghost cards with a compact dashed style (`┌╌╌╌╌┐ │+New X│ └╌╌╌╌┘`). Ghost cards are 1/4 the height of their real-card counterparts.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│ USER SECTION                                                                │
│                                                                             │
│  ┌────────┐   [worker] [worker] ┌╌╌╌╌╌╌╌╌╌╌┐          ← loose-workers       │
│  │        │                      │+ New Worker│           strip             │
│  │  User  │                      └╌╌╌╌╌╌╌╌╌╌┘                               │
│  │ OWNER  │   ─────────────────────────────────                             │
│  └────────┘   [Engineer 1] [worker] [worker]          ← engineer row 1      │
│  ┌╌╌╌╌╌╌╌╌┐   ─────────────────────────────────                             │
│  │+ New   │   [Engineer 2] [worker]                    ← engineer row 2     │
│  │Architect│   ─────────────────────────────────                             │
│  └╌╌╌╌╌╌╌╌┘   ┌╌╌╌╌╌╌╌╌╌╌╌╌┐                          ← engineer ghost     │
│               │+ New Engineer│                                              │
│               └╌╌╌╌╌╌╌╌╌╌╌╌┘                                                │
│                                                                             │
│ ─── architect section divider ─────────────────────────────────────────     │
│                                                                             │
│ ARCHITECT 1 SECTION                                                         │
│                                                                             │
│  ┌────────┐   [Engineer 1] [worker] [worker] [worker] [worker]              │
│  │        │                [worker] [worker] (wrapped inside row 1)         │
│  │Architect│   ─────────────────────────────                                │
│  │  1     │   [Engineer 2] [worker]                                         │
│  │        │   ─────────────────────────────                                 │
│  └────────┘   ┌╌╌╌╌╌╌╌╌╌╌╌╌┐                                                │
│               │+ New Engineer│                                              │
│               └╌╌╌╌╌╌╌╌╌╌╌╌┘                                                │
│                                                                             │
│ ─── architect section divider ─────────────────────────────────────────     │
│                                                                             │
│ ARCHITECT 2 SECTION                                                         │
│                                                                             │
│  ┌────────┐   [Engineer 1] [worker] [worker]                                │
│  │Architect│   ─────────────────────────────                                │
│  │  2     │   ┌╌╌╌╌╌╌╌╌╌╌╌╌┐                                                │
│  └────────┘   │+ New Engineer│                                              │
│               └╌╌╌╌╌╌╌╌╌╌╌╌┘                                                │
│                                                                             │
│                         (grid ends at last architect section, no orphan)    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

Note the left column of the User section now extends vertically to accommodate both the User card AND the `+ New Architect` ghost below it. The right column still holds its own content (loose strip + engineer rows + `+ New Engineer` ghost). The two columns have independent vertical extents; the section's total height is `max(left_column_height, right_column_height)`.

**Key non-shapes** (what this layout is NOT):

- NOT a horizontal header row with engineers stacked below. The architect is a left-column anchor, not a top-bar.
- NOT a stacked column with architect card then engineer cards vertically beneath. Engineers live to the **right** of their architect, not below.
- NOT a per-engineer repeated architect card. Each architect renders exactly once, anchoring its entire engineer stack on the left.
- NOT a grid where workers wrap onto separate rows outside their engineer. Wrapped workers stay inside their engineer's row.
- NOT an architect card that stretches vertically to fill its section. The architect card is fixed-height; the left column below it stays empty when the right column's engineer stack is taller.

---

## Creation semantics

The grid's creation affordances align with the ownership rules:

- **`+ New Architect`** (ghost card inside the User section's architect column, directly below the User card): creates a new architect at group scope. Opens the existing architect-creation modal.
- **`+ New Engineer`** (ghost card at the bottom of each section's engineer-row stack): creates an engineer hired by that specific architect, or for the User section, a user-created engineer. The architect context is implicit from the section the ghost card sits in — no more disambiguation modal step.
- **Worker creation remains engineer-driven**: an engineer creates workers attached to itself via `torque ai derive` or engineer MCP surfaces. The grid does not expose a direct `+ New Worker` ghost on engineer rows in v1. Rationale: per the kinds-refactor invariants, workers are engineer-dispatched; surfacing a grid ghost would invite the wrong mental model ("users create workers for engineers"). We may revisit this in a follow-up if engineers want a manual worker-spawn affordance.
- **Detached worker creation**: a dedicated `+ New Worker` ghost card lives in the User section's loose-workers strip. This is the only path through which a user creates a worker directly; it always produces a detached (no-engineer) worker. Users cannot create a worker under an engineer they did not hire, and cannot create a worker under an engineer at all — the engineer owns that surface.
- **Terminal creation**: unchanged from today. Terminals attach to any agent kind (architect, engineer, worker) and render in that agent's drawer. A terminal is never a first-class grid cell in this proposal.
- **Legacy `+ New` kind-picker dropdown**: retired. It was the only creation surface before the per-section ghost cards existed. With architect/engineer/worker all having explicit ghost-card creation surfaces tied to their spatial context, the kind-picker is redundant and should be removed from the grid entirely.

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

**Kind contract for the strip:** the strip renders exactly **user-owned cells with `kind == worker` and null `owner_engineer_id`** — nothing else. In particular:

- **Terminals never render in the strip.** Per the kinds model, terminals always carry a `parent_id` to some agent and render in that agent's drawer. A terminal without a parent is a data-integrity issue, not a strip resident — surface it via `torque doctor` rather than normalizing it into worker rendering.
- **Engineers never render in the strip.** User-created engineers render as engineer rows in the User section with the leading-engineer-card + wrapping-workers layout. They are not "detached workers" even when they have no architect.
- **The `+ New Worker` button always produces `kind == worker`.** There is no "quick-add with unspecified kind" path through the strip; the button's output kind is fixed.

This contract closes the earlier ambiguity around whether "any detached agent regardless of kind" belonged in the strip. The strip is a worker-only surface; other kinds appearing in it would be a bug signal (upstream grouping logic leaked), not a rendering variant to accommodate.

---

## Ghost-card creation affordances

All three creation surfaces (`+ New Architect`, `+ New Engineer`, `+ New Worker`) render as **ghost cards** — dashed-outline placeholder cards that occupy the same spatial slot as the agent kind they create. This replaces the prior solid-button affordances and the legacy `+ New` kind-picker dropdown.

**Visual style (applies to all three ghost cards):**

- **Outline**: dashed border, no filled background. Distinguishes ghost slots from real agent cards at a glance.
- **Width**: exactly the same as a real card of the kind it creates. `+ New Architect` matches an architect card's width (fixed); `+ New Engineer` matches an engineer card's width; `+ New Worker` matches a worker card's width.
- **Height**: **1/4 the height** of a real card of the kind it creates. Low visual weight so real cards remain the focal point; still large enough to read the text label and click comfortably.
- **Label**: text label inside the dashed outline: `+ New Architect` / `+ New Engineer` / `+ New Worker`. No icon-only variant.
- **Hover/focus**: standard button-like affordance (highlight on hover, focus ring on keyboard focus).

**Positioning:**

- **`+ New Architect` ghost**: inside the User section, in the architect column, directly below the User architect card (before the User-to-Architect-1 divider). Aligned to the architect column's fixed-width gutter. Rationale: architects are user-created only per the kinds invariants; placing the ghost beneath the User card makes the "user creates architects" mental model spatially literal, and eliminates the orphan-at-bottom-of-grid look.
- **`+ New Engineer` ghost**: at the bottom of each section's engineer-row stack, in the section's right column, aligned to the engineer-row left edge (directly under the first engineer card's position, NOT under the architect column). One per section.
- **`+ New Worker` ghost**: in the User section's loose-workers strip, after any existing detached worker cards (right-most on the strip's first line; wraps to a new line only if the strip has many workers).

**Behavior:**

- Clicking a ghost card opens the existing creation modal for that kind, with the architect/section context pre-filled implicitly (no disambiguation step).
- Ghost cards are always rendered, even when the section is empty. The `+ New Engineer` ghost shows in a section with zero engineers as a cue to fill the slot.
- Ghost cards are NOT drop targets for drag-and-drop in v1 — the interaction is click-only. Drag-to-reparent is the future drag-to-detach proposal.

**Why ghost cards over solid buttons:**

- **Spatial ownership is unambiguous**: a ghost card in the architect column clearly creates an architect; one in an engineer-row stack clearly creates an engineer for that architect. Solid buttons stacked at the bottom of the grid created the ambiguity ("is this for the last section or global?") that TORQUE:110's first iteration had.
- **Consistent visual language**: all three creation surfaces share one grammar (dashed card = "empty slot you can fill"). The loose-workers strip already used this pattern; extending it to engineer/architect creation unifies the grid.
- **Reduced visual weight**: dashed outlines read as "empty slot," not "attention-demanding action." Real agent cards remain the focal point of the grid.

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
- Engineer card: existing controls (dismiss, rehire, remove). Dismissed engineers render greyed in-place.
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

TORQUE:72's current typographic hierarchy remains in place until step 1 lands, then is removed (visual classes retired) as part of step 1's diff.

Near-future evolution (detach flows) is a separate proposal/spec; this proposal intentionally stops at the render layer so the visual model is in place first.
