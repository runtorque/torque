# Torque Design System

Status: living document
Last updated: 2026-07-15

This document is the source of truth for Torque's product design language. It
records the rules that make the interface feel like one system and the decisions
that should survive individual redesign passes.

The system is intentionally practical. Torque is a dense, long-running operator
workspace, so clarity, stability, and information hierarchy matter more than
decoration. When implementation and this document disagree, either bring the
implementation back to the documented standard or update the standard and add a
decision entry explaining why.

## Working agreement

Every UI change should:

1. Reuse or extend the shared tokens in `static/styles/tokens-base.css`.
2. Apply the same component rule everywhere that component appears.
3. Preserve focus, caret, scroll position, selection, drafts, and expanded state
   across routine rerenders.
4. Keep keyboard, hover, focus, active, disabled, loading, and error states clear.
5. Add focused regression coverage for durable visual or interaction contracts.
6. Update this document when a component standard or design decision changes.

Avoid one-off values when an existing token expresses the same intent. If a new
value is genuinely needed, add a semantic token before repeating the literal.
Feature-specific canvas dimensions, data-visualization geometry, and responsive
breakpoints may remain local literals when they do not redefine a shared
component. Repeated control height, padding, radius, type, color, focus, or
elevation values belong in the token and component layers.

## Design principles

### Operator-first density

Torque should fit substantial live state on screen without feeling cramped.
Prefer compact controls, restrained spacing, and strong alignment. Do not make a
frequently used surface larger merely to make it look more consumer-oriented.

### Calm hierarchy

Use surface tone, borders, spacing, and typography to establish hierarchy.
Reserve the accent color for selection, focus, primary actions, and meaningful
live state. Avoid decorative gradients, excessive shadows, and competing accent
colors.

### Stable workspaces

The interface is a workspace rather than a sequence of pages. Rerenders must not
make panels jump, inputs lose focus, drafts disappear, or scroll positions reset.
Layouts and controls should remain recognizable across standalone, desktop, and
embedded widths.

### Explicit state

Selected, running, stopped, blocked, destructive, and disabled states must be
distinguishable without relying on color alone. Labels, icons, borders, and
accessible names should reinforce state where appropriate.

### One visual grammar

Components with the same job should look and behave the same across panels.
Differences should communicate a real semantic distinction, not the history of
which feature introduced the component.

## Foundations

### Typography

- Torque uses the shared monospace stack in `--font` throughout the product.
- Base UI text is `--ui-font-size` with a compact `1.4` line height.
- Use weight, color, and spacing before introducing a new font size.
- Text labels should use sentence case. Reserve uppercase for short machine-like
  status labels and established identifiers.

### Color

- Use the semantic tokens in `static/styles/tokens-base.css`; do not hard-code a
  new color when `--text`, `--text-dim`, `--border`, `--accent`, `--green`,
  `--amber`, or `--red` already expresses the intent.
- `--accent` communicates focus, selection, and the primary action.
- Green, amber, and red communicate success, warning, and danger respectively.
- Muted content must remain legible in the high-contrast theme.

### Spacing

Torque uses a 4px base rhythm:

| Token | Value | Typical use |
|---|---:|---|
| `--space-1` | 4px | Tight inline gaps and compact control groups |
| `--space-2` | 8px | Standard control and card spacing |
| `--space-3` | 12px | Section padding and related groups |
| `--space-4` | 16px | Strong section separation |
| `--space-5` | 24px | Major layout separation |

Use intermediate literals only when a compact control's geometry requires one.

### Corner radius

Rounded corners indicate component scale, not decoration:

| Token | Value | Use |
|---|---:|---|
| `--radius-sm` | 4px | Compact buttons, navigation tabs, segmented controls |
| `--radius` | 6px | Inputs, standard buttons, cards, and embedded surfaces |
| `--radius-lg` | 10px | Modals, popovers, and floating surfaces |

`999px` is reserved for genuinely circular controls and semantic pills such as
status badges, counts, tags, and presence indicators. Navigation and ordinary
action buttons must not use pill geometry.

### Control geometry

Interactive controls share a compact geometry scale:

| Token | Value | Typical use |
|---|---:|---|
| `--control-height-xs` | 22px | Dense navigation such as group tabs |
| `--control-height-sm` | 24px | Panel tabs and compact icon controls |
| `--control-height-md` | 28px | Standard buttons and form controls |
| `--control-padding-x-xs` | 7px | Dense tabs and compact actions |
| `--control-padding-x-sm` | 8px | Small buttons and toolbar controls |
| `--control-padding-x-md` | 10px | Standard text buttons |
| `--control-font-size-xs` | 10px | Compact control labels |
| `--control-font-size-sm` | 11px | Standard control labels |
| `--control-border` | `1px solid var(--border)` | Default interactive boundary |

Use the smallest size that remains readable and operable in its context. A
component may combine adjacent steps—for example, a 24px panel tab can use the
10px label and 7px padding—but it should not introduce another near-duplicate
literal. Icon-only controls keep equal width and height.

### Borders, focus, and elevation

- Use `--border` for default boundaries, `--border-subtle` for quiet separation,
  and `--border-strong` for emphasized boundaries.
- All keyboard-focusable controls use `--focus-ring` through `:focus-visible`.
- Use `--shadow-float` only for content that floats above the workspace. Avoid
  shadows on inline cards and tabs.

### Motion

- Short state transitions use `--transition-fast`.
- Motion must explain a state or spatial change; it should not run decoratively.
- Honor `prefers-reduced-motion`.

## Component standards

### Buttons

- Compact text and icon buttons use `--radius-sm`.
- Action buttons use `--radius-sm`; inputs and contained surfaces keep their
  larger component-appropriate radius.
- Icon-only buttons need a stable square hit area, an accessible name, and a
  tooltip when the icon is not universally understood.
- Primary actions use the accent treatment. Destructive actions use danger
  styling and require clear wording; do not communicate danger through color
  alone.
- Button groups should align heights, padding, and border treatment.

The canonical CSS API lives in `static/styles/components.css`:

- `.btn` is the shared base and neutral/default treatment.
- `.btn-primary`, `.btn-secondary`, `.btn-quiet`, `.btn-danger`,
  `.btn-danger-outline`, `.btn-success`, and `.btn-warning` express intent.
- `.btn-sm` and `.btn-xs` select smaller control geometry.
- `.btn-link` is reserved for an inline text action rather than a contained
  control.
- Existing `.btn-cancel`, `.btn-green`, and `.btn-rebase` classes are temporary
  compatibility aliases for quiet, success, and warning intent.

New markup should use `.btn` plus an intent class. Variant-only markup remains
supported while existing surfaces migrate.

### Navigation tabs

- Group tabs and panel tabs use `--radius-sm`; they are compact rectangular tabs,
  not pills.
- Feature navigation uses one of two shared variants: contained tabs for peer
  surfaces and underline tabs for dense navigation within a panel.
- Group tabs are 22px tall. Panel tabs are 24px tall.
- The active tab uses accent color plus a visible border/background change.
- Hover and keyboard focus must remain distinct from the active state.
- Labels should remain readable under constrained widths; use truncation only
  when a switcher or tooltip exposes the complete label.

The canonical CSS API lives in `static/styles/components.css`:

- `.ui-tab` is the shared base for new markup.
- `.ui-tab--contained` is a 24px rectangular tab with a visible boundary and
  selected background. Planning, Thinking, and agent-event tabs currently use
  compatibility aliases for this variant.
- `.ui-tab--underline` is a 24px borderless tab with an active underline. Agent
  panel, settings subnavigation, and narrow board-lane tabs currently use
  compatibility aliases for this variant.
- `.agent-group-tab` and `.standalone-panel-tab` remain compact workspace-specific
  navigation variants.
- `.gs-tab` remains a roomier primary settings rail item because it carries a
  title and description; it still uses rectangular geometry and shared states.

Segmented toggles, filters, terminal cards, badges, and tags are not navigation
tabs. Do not apply tab styles to them merely because their class name contains
“tab.”

### Segmented controls

- Segmented controls choose one mutually exclusive local mode or view. They use
  compact rectangular geometry rather than pills.
- The outer control is 24px tall with `--radius-sm`; items share boundaries and
  do not round their individual corners.
- Selected state uses the accent-soft surface, an inset accent boundary, and an
  explicit `aria-pressed` or `aria-selected` state.
- Use segmented controls only when the options are peers and always visible.
  Use a select when space is constrained or the option set is long.

The canonical CSS API lives in `static/styles/components.css`:

- `.segmented-control` is the shared container.
- `.segmented-control__item` is the shared option.
- Grid/Canvas, Editor/DAG, Library sections, log targets, and schedule type use
  compatibility aliases while their markup migrates.

Filter chips, on/off switches, navigation tabs, and semantic badges are separate
components even if they also expose selected state.

### Filter chips and presets

- Filter chips narrow or reveal content and keep a visible selected state. They
  use compact 24px rectangular geometry and `aria-pressed` for direct toggles.
- A filter that opens a menu uses `aria-haspopup` and `aria-expanded`; its active
  visual state may also indicate that the resulting filter is non-empty.
- Applied filter values remain operable removal buttons, not non-semantic spans.
  Their accessible name states which filter will be removed.
- Preset buttons apply a value immediately and do not remain selected. They share
  filter-chip geometry, but never borrow selected-state semantics.

The canonical CSS API lives in `static/styles/components.css`:

- `.filter-chip` is the shared stateful filter or reveal control.
- `.preset-button` is the shared momentary preset action.
- Board filters and applied values, initiative secondary buckets, schedule cron
  presets, and worktree symlink presets use compatibility aliases while their
  markup migrates.

Counts, statuses, labels, and other semantic metadata remain pills where that
shape helps them read as annotations rather than controls.

### Inputs and selectors

- Inputs, selects, and textareas use `--radius`, `--bg-inset`, and `--border`.
- Standard single-line fields are 28px tall with an 11px label. Compact editor
  and toolbar fields are 24px tall with a 10px label.
- Every field needs a visible label or an equivalent accessible name.
- Placeholder text provides an example or hint, never the only label.
- Validation and disabled state must remain legible without relying on opacity
  alone for the explanatory text.

The canonical CSS API lives in `static/styles/components.css`:

- Native text-like inputs, selects, and textareas receive the default primitive
  automatically; `.form-control` is the explicit API for new markup.
- `.form-control-sm` selects compact geometry. Existing agent-panel and action-
  editor selectors are temporary compatibility aliases.
- `.is-invalid` or `aria-invalid="true"` applies the shared invalid treatment.
- `.form-error` styles the adjacent explanatory message. Invalid color alone is
  not a substitute for useful error text.

Checkboxes, radio buttons, range controls, color pickers, file controls, and
switches keep their native or dedicated component geometry and are not text
fields.

### Cards and contained surfaces

- Cards use `--radius`, `--border`, and `--bg-cell`; they are separated primarily
  by boundary and surface contrast rather than large shadows.
- Interactive cards strengthen their boundary and surface on hover. Selected
  cards use an accent boundary plus a quiet accent-soft surface.
- Compact cards use 8px internal padding. Comfortable list cards use 10px.
  Density-sensitive cards may own their padding while retaining shared states.
- Semantic edge accents may communicate status or direction, but do not replace
  the shared card boundary.
- Avoid nesting multiple fully bordered cards when spacing or a subtle divider
  can express the same hierarchy.
- Repeated cards in a list must keep their action placement consistent.

The canonical CSS API lives in `static/styles/components.css`:

- `.ui-card` is the shared boundary and surface primitive.
- `.ui-card--interactive` adds hover affordance.
- `.ui-card--compact` and `.ui-card--comfortable` select internal padding.
- `.is-selected` or `aria-selected="true"` applies the selected state.
- Board tasks, Context entries, agent messages, and agent work streams use
  compatibility aliases while markup migrates.

### Panels and toolbars

- Panel headers use compact, aligned controls and preserve the content width for
  the panel's primary information.
- Header identity copy stays on the leading edge. Local actions stay on the
  trailing edge and wrap only when the panel cannot preserve both regions.
- Search, filter, and editor controls belong in a toolbar row rather than being
  mixed into the title hierarchy.
- Repeated panel actions belong in the same order: navigation first, then local
  actions, then layout/window controls.
- Resizable panels must preserve operator-selected dimensions and content state.

The canonical CSS API lives in `static/styles/components.css`:

- `.ui-panel-header` establishes the shared header boundary and responsive row.
- `.ui-panel-header--surface` adds the quiet raised header surface.
- `.ui-panel-header__copy`, `__title-row`, `__title`, and `__subtitle` define the
  identity hierarchy.
- `.ui-panel-header__actions` aligns local actions at the trailing edge.
- `.ui-toolbar` is the compact wrapping row for search, filters, and editors;
  `.ui-toolbar--bordered` separates it from following content.
- Top-level panel headers and working-control rows opt into the canonical API
  directly. Consumer classes may retain layout or surface-specific behavior but
  must not duplicate the shared geometry.
- Modal, artifact, and settings headers remain governed by their modal family
  until those large surfaces migrate deliberately.

### Status bar segments

- The bottom status bar is a compact segmented workspace rail, not a collection
  of badges. Segments keep square shared boundaries and may contain short status
  text, a presence dot, or a count annotation.
- Passive runtime/provider/workload metadata uses a non-interactive segment.
  Navigation, deploy, metrics, task, and attention actions use native buttons.
- Normal, warning, danger, muted, and unknown states may change text, tint, and
  the leading status edge, but the segment label remains the primary state cue.
- Status changes update stable nodes in place so focus and panel navigation are
  preserved across WebSocket deltas.

The canonical CSS API lives in `static/styles/workspace-shell.css`:

- `.statusbar-segment` owns the shared 24px segment geometry and divider.
- `--passive` and `--action` distinguish metadata from operable controls.
- `--normal`, `--warning`, `--danger`, `--muted`, and `--unknown` express state.
- Consumer classes such as `.statusbar-chip-tasks` own only responsive visibility
  and surface-specific layout. They do not redefine the segment primitive.

### Modals, menus, and popovers

- Use Torque's custom overlay, modal, and context-menu patterns rather than
  native blocking dialogs.
- Use `--radius-lg` for the floating container and standard radii for controls
  inside it.
- Opening a surface moves focus into it; closing it restores focus to the control
  that opened it.
- Escape closes dismissible transient surfaces. Destructive confirmation should
  name the affected object.

Dialogs use the canonical modal API in `static/styles/components.css`:

- `.ui-modal` defines the raised boundary, 10px corners, shared floating shadow,
  and 360px default width. `--sm`, `--md`, `--lg`, `--xl`, and `--full` cap
  dialogs at 360px, 520px, 760px, 920px, and 1100px.
- `--tall` establishes a column shell capped at 85vh; `--viewport` caps
  workspace-style dialogs against the viewport while feature layout may choose
  a smaller content height.
- `.ui-modal--structured` separates the shell into `.ui-modal__header`,
  `.ui-modal__body`, and `.ui-modal__footer`. The footer owns the action boundary
  and keeps secondary actions before the primary action.
- `__header--bordered`, `__body--flush`, and `__footer--split` cover raised
  workspaces and content-led viewers without rebuilding the outer shell.
- `.ui-modal__title`, `__subtitle`, and `__message` establish the dialog type
  hierarchy. Dialog titles are visible and provide the accessible label.
- Shared confirm and input dialogs, New Group, and Edit Agent/Terminal use the
  structured shell and shared focus controller. Confirmations focus the explicit
  commit action; simple editors focus and select their primary field.
- Task, settings, history, prompt, artifact, diff, Help browser, log viewer, and
  attachment-preview dialogs use the canonical boundary and explicit size.
  Structured consumers declare header/body/footer regions directly while their
  feature styles retain internal grids, diff rows, navigation, and content layout.

Menus and compact popovers use the canonical API in
`static/styles/components.css`:

- `.ui-popover` defines the raised boundary, 10px corners, shadow, and viewport
  height limit.
- `.ui-menu-item` defines 28px rows with shared hover and keyboard-focus states.
- `.is-selected` or `aria-checked="true"` applies the selected state;
  `.ui-menu-item--danger` applies destructive intent without turning the whole
  row into a solid warning block.
- `.ui-menu-label`, `.ui-menu-separator`, and `.ui-menu-section` define internal
  grouping.
- Opening a menu focuses its first working control. Escape returns focus to the
  invoker; outside-pointer dismissal does not steal focus from the new target.
- Autocomplete listboxes are the deliberate focus exception: their editor keeps
  focus while Arrow keys update `aria-selected` on canonical option rows.
- Group actions, the compact group switcher, Board filters and View, task label
  and dependency suggestions, inline Board editors, terminal completion and
  history surfaces, the panel switcher, and context menus use the shared API.
  Large dialogs remain governed by the modal family.

### Badges, tags, and counts

- Pill geometry is appropriate because these elements describe metadata rather
  than offer navigation.
- Keep badges short and visually subordinate to the primary label.
- Status badges use semantic color tokens and a textual or iconographic cue.
- Badges are annotations, not controls. Clickable filters, toggles, tabs, and
  actions use their own component families even when they contain a count.

The canonical CSS API lives in `static/styles/components.css`:

- `.ui-badge` defines the shared pill boundary, compact type, and inline
  alignment. `--compact` and `--micro` preserve dense task-card and agent-card
  layouts without inventing new badge geometry.
- `--neutral`, `--accent`, `--success`, `--warning`, and `--danger` express
  semantic intent with text, border, and a restrained tint. The nearby label or
  badge text remains the primary state cue; color is reinforcement.
- `--count` uses tabular numerals and a stable minimum width for numeric totals.
- Agent identity badges, Agent-panel journal entry types, Health runtime and
  coverage states, Board task metadata, Board count indicators, and Agent Profile
  assignment and preview metadata, History identity/status/outcome markers, and
  dismissed-event markers are canonical consumers. Workspace, Agent-panel, Chat,
  Events, Actions, Mission Control, and Initiatives counts use the same primitive.
- A count may use the badge primitive inside a tab, filter, or menu item without
  changing the parent control's semantics. The count is an annotation; the
  containing tab, filter, or menu item remains the interactive target.

### Empty, loading, and error states

- Operator-facing absence, progress, failure, and informational notes use the
  `.ui-state` primitive. `.ui-state--empty`, `--loading`, `--error`, and `--note`
  communicate intent; `--compact`, `--inline`, and `--fill` describe placement.
- Empty states explain what is absent and provide one clear next action when one
  exists. Actions live in `.ui-state__actions` and use the shared button family.
- Loading states preserve layout geometry where practical, use `role="status"`
  with polite announcements for asynchronous panel content, and respect reduced
  motion.
- Errors state what failed and what the operator can do next, and use
  `role="alert"` when newly inserted after an operation. Do not replace useful
  content with a generic error if stale content can remain safely visible.
- Optional `.ui-state__title`, `__message`, and `__meta` regions establish a
  consistent hierarchy for full states. Compact one-line states may omit them.
- Feature classes may own placement, width, and minimum height. They must not
  rebuild state boundaries, semantic colors, type hierarchy, or loading motion.
- Metadata such as “no assignment,” disabled explanations, validation text
  beside a field, and specialized canvas instructions remain local when they do
  not replace a content surface.

## Responsive and embedded behavior

- Torque must work in standalone browser, desktop, and embedded layouts.
- Prefer container queries for controls whose available panel width matters more
  than the browser viewport.
- Compact variants must preserve the same actions and accessible names.
- A responsive transition must not silently hide the only path to an action.

## Accessibility baseline

- Interactive controls are reachable and operable by keyboard.
- Tab interfaces keep only the selected tab in the sequential focus order and
  support Arrow, Home, and End navigation with automatic activation.
- Use semantic roles and native controls before adding ARIA.
- Icon-only controls have explicit `aria-label` text.
- Visible field labels are programmatically associated with their control;
  placeholders supplement labels instead of replacing them.
- Focus remains visible in every theme.
- Text and meaningful boundaries maintain sufficient contrast.
- State is not communicated by color alone.
- Respect reduced-motion preferences.

## Implementation map

The frontend has no build step. CSS cascade order is explicit in `webview.html`:

1. `static/styles/tokens-base.css`
2. `static/styles/components.css`
3. `static/styles/workspace-grid.css`
4. `static/styles/modals.css`
5. `static/styles/workspace-shell.css`
6. `static/styles/board-panels.css`
7. `static/styles/agent-panel.css`
8. `static/styles/desktop-features.css`
9. `static/styles/feature-panels.css`

Shared foundations belong in tokens. Component rules belong in the narrowest
existing stylesheet that owns the surface. Avoid late global overrides unless
the rule is intentionally global and documented here.

## Standardization inventory

The first component-standardization pass is complete as of 2026-07-15. Every
listed family has a canonical primitive or an explicit semantic boundary, direct
consumer opt-in, focused regression coverage, and live-browser evidence. The
“Next concern” column is ongoing maintenance guidance, not unfinished migration
scope.

| Component family | Status | Next concern |
|---|---|---|
| Foundations and tokens | Standardized, literal audit complete | Tokenize repeated component semantics; keep feature geometry local |
| Group tabs | Standardized, compact parity audited | Preserve search, keyboard, create, and group-action access in both modes |
| Panel tabs | Standardized, all zones audited | Keep dock and rail tab rows roving, scrollable, and action-safe |
| Feature navigation tabs | Standardized, accessibility audited | Keep long labels reachable without wrapping the panel |
| Segmented controls | Standardized, accessibility audited | Keep tab-style segments roving and button groups natively operable |
| Filter chips and presets | Standardized, residual toggles audited | New persistent filters opt into `filter-chip`; local modes use segmented controls |
| Buttons | Standardized, accessibility audited | Keep specialized editor actions explicitly named |
| Inputs and selectors | Standardized, accessibility audited | Keep new dynamic editors explicitly labelled |
| Cards | Standardized, grid identity audited | New nested contained surfaces declare card intent directly |
| Toolbars and panel headers | Standardized, responsive audited | Preserve action access when identity and controls wrap |
| Status bar segments | Standardized, narrow priority audited | Never hide Attention before passive or redundant status |
| Menus and popovers | Standardized, responsive audited | Keep anchored placement inside the viewport as labels grow |
| Modals | Standardized, responsive audited | Keep nested-dialog focus and compact footer actions stable |
| Badges, tags, and status | Standardized, density audited | Keep metadata legible at an 8px minimum and truncate safely |
| Count indicators | Standardized | Keep prose metrics and countdown text outside the badge grammar |
| Empty/loading/error states | Standardized, recovery semantics audited | New async failures name a recovery path; loading and inserted errors announce once |

## Decision log

### D-001 — Compact navigation tabs are rectangular

- Date: 2026-07-14
- Status: accepted
- Decision: Group tabs and panel tabs use `--radius-sm` (4px). Navigation tabs
  must not use the `999px` semantic-pill radius.
- Rationale: Rectangular tabs read more clearly as workspace navigation, create a
  calmer visual rhythm, and align with Torque's compact operator-tool character.
- Scope: `.agent-group-tab` and `.standalone-panel-tab`.
- Constraints: Status badges, tags, counts, circular icon controls, and other
  semantic pills are not affected.
- Verification: `tests/frontend_navigation.test.js` asserts both component rules.

### D-002 — Controls share a semantic geometry scale

- Date: 2026-07-14
- Status: accepted
- Decision: Compact and standard controls use shared height, horizontal-padding,
  label-size, and border tokens. The initial scale is 22px, 24px, and 28px for
  height; 7px, 8px, and 10px for horizontal padding; and 10px and 11px for
  labels.
- Rationale: Shared geometry aligns controls across unrelated surfaces, prevents
  near-duplicate literals, and lets later density changes happen centrally.
- Scope: Foundation tokens, header controls, group tabs, and panel tabs. Other
  component families migrate as their consolidation slice begins.
- Constraints: Component-specific content may require a larger height, but new
  sizes must be justified and promoted to tokens only when they are reusable.
- Verification: `tests/frontend_navigation.test.js` asserts the scale and its
  first consumers; live browser checks confirm computed dimensions remain stable.

### D-003 — Action buttons use one shared primitive

- Date: 2026-07-14
- Status: accepted
- Decision: Contained action buttons share a 28px default geometry, 4px radius,
  common interaction states, and explicit neutral, primary, secondary, quiet,
  danger, success, and warning intents. Small variants use the foundation scale.
- Rationale: Button styling had been split between modal and board stylesheets,
  producing inconsistent padding, radii, hover behavior, and disabled treatment.
  One primitive makes action hierarchy predictable across every panel.
- Scope: `static/styles/components.css`, modal actions, context/event size
  modifiers, diff success actions, and rebase warning actions.
- Constraints: Navigation tabs, icon-only chrome, segmented controls, and inline
  text links remain separate components. Feature styles may change layout width
  but should not redefine core button geometry or intent colors.
- Verification: `tests/frontend_components.test.js` protects the shared API and
  prevents core button rules from drifting back into feature stylesheets.

### D-004 — Feature navigation has contained and underline variants

- Date: 2026-07-14
- Status: accepted
- Decision: Peer feature surfaces use 24px contained rectangular tabs; dense
  in-panel navigation uses 24px underline tabs. Both variants share typography,
  interaction transitions, active-state semantics, and the control geometry
  scale.
- Rationale: Planning, Thinking, agent events, settings subsections, agent panel
  views, and board lanes had independently evolved near-duplicate tab styles.
  Two semantic variants preserve the hierarchy each context needs without
  retaining unrelated pill shapes, sizes, and active treatments.
- Scope: `static/styles/components.css`, Planning and Thinking feature tabs,
  agent panel and agent-event tabs, settings subnavigation, narrow board-lane
  tabs, and the primary settings rail's corner geometry.
- Constraints: Group/panel workspace tabs retain their denser variants. The
  primary settings rail remains larger because each item includes descriptive
  copy. Segmented toggles, filters, terminal cards, badges, and tags are outside
  this decision.
- Verification: `tests/frontend_components.test.js` protects both shared
  variants and prevents feature stylesheets from redefining their core visual
  grammar.

### D-005 — Text fields use default and compact primitives

- Date: 2026-07-14
- Status: accepted
- Decision: Text-like inputs, selects, and textareas share one visual primitive.
  Standard single-line fields use 28px geometry and compact editor/toolbar fields
  use 24px geometry. Both share radius, surface, border, hover, focus, disabled,
  and invalid-state behavior.
- Rationale: Form styling lived in the reset layer and was repeatedly rebuilt in
  agent, Planning, Thinking, modal, and action-editor styles. A component-level
  primitive makes fields align with buttons and tabs while keeping density an
  explicit variant instead of a feature-local accident.
- Scope: `static/styles/components.css`, native text-like fields, agent decision
  and filter fields, Planning and Thinking forms, action-template editors, event
  resolution input, and modal textareas.
- Constraints: Search experiences may intentionally use larger geometry.
  Checkboxes, radios, ranges, color/file controls, switches, and specialized
  code-editor behavior are separate components. Layout width and resize behavior
  remain owned by their surfaces.
- Verification: `tests/frontend_components.test.js` protects the shared API and
  keeps form primitives out of the reset and migrated feature stylesheets. Live
  browser checks verify default and compact computed geometry.

### D-006 — Mutually exclusive local modes use segmented controls

- Date: 2026-07-14
- Status: accepted
- Decision: Always-visible choices between mutually exclusive local modes use a
  shared 24px segmented control with 4px outer corners, square internal items,
  quiet default styling, and an accent-soft selected state.
- Rationale: Grid/Canvas, Editor/DAG, Library modes, log targets, and schedule
  type had independently evolved different heights, corner radii, borders, text
  sizes, and selected treatments. A shared primitive makes mode switching read
  consistently without conflating it with navigation or metadata pills.
- Scope: `static/styles/components.css`, agent view mode, Actions and Library
  view controls, log target selection, schedule type, and their selected-state
  ARIA attributes.
- Constraints: Navigation tabs follow D-001 and D-004. Filters, presets, on/off
  switches, checkboxes, badges, tags, and terminal cards are not segmented
  controls. Long or responsive option sets should use a select instead.
- Verification: `tests/frontend_components.test.js` protects shared geometry and
  keeps the migrated visual primitive out of feature stylesheets. Existing
  interaction tests protect behavior; live browser checks verify computed sizes
  and selected state.

### D-007 — Filters and presets use rectangular action geometry

- Date: 2026-07-14
- Status: accepted
- Decision: Stateful filter chips and momentary preset buttons share the 24px
  control height, 4px corners, compact typography, and interaction treatment.
  Filters expose pressed or expanded state; presets do not imply persistence.
- Rationale: Board filters were still rendered as full pills while feature
  filters and presets used several unrelated sizes and radii. Shared geometry
  makes these controls read as actions without conflating them with navigation,
  segmented modes, or metadata.
- Scope: `static/styles/components.css`, board filter triggers and removable
  values, saved board views, initiative secondary buckets, idea-brief archive
  visibility, schedule cron presets, and worktree symlink presets.
- Constraints: Status, count, label, and identity pills remain semantic metadata.
  Ordinary commands continue to use the button primitive. Mutually exclusive
  always-visible choices continue to use segmented controls.
- Verification: `tests/frontend_components.test.js` protects shared geometry,
  semantic state, and the removal of feature-local pill geometry. Live browser
  checks verify board filters at default and active states.

### D-008 — Repeated content cards share one boundary and state grammar

- Date: 2026-07-14
- Status: accepted
- Decision: Repeated content cards use a 6px radius, the standard card surface
  and boundary, shared fast transitions, a stronger hover boundary, and a quiet
  accent selected state. Compact and comfortable padding remain explicit.
- Rationale: Board tasks, Context entries, and agent message/stream cards had
  converged on similar structures but still used unrelated radii, border colors,
  backgrounds, and transitions. One primitive makes card hierarchy predictable
  without erasing the information density each surface needs.
- Scope: `static/styles/components.css`, board task cards, Context entry cards,
  agent message cards, and agent work-stream cards.
- Constraints: Board task cards retain lane/status edge accents and density
  modes. Agent messages retain direction accents. Detail editors and large
  contained forms are surfaces rather than repeated list cards. Metadata pills
  inside cards remain governed separately.
- Verification: `tests/frontend_components.test.js` protects the shared API,
  canonical markup, and removal of duplicate feature geometry. Existing board,
  Context, and agent-panel regression suites protect rendering and rerender
  behavior; live checks verify computed card geometry.

### D-009 — Panel identity and toolbar controls use separate shared rows

- Date: 2026-07-14
- Status: accepted
- Decision: Panel headers share an 8px-by-10px wrapping boundary, a consistent
  title/subtitle hierarchy, and trailing local actions. Search, filter, and
  editor controls use a separate compact toolbar primitive with optional bottom
  boundary.
- Rationale: Planning and Thinking reserved a full-width second row for a count
  and refresh action, while Events and Agent independently duplicated nearly the
  same header geometry. Board search/filter layout was another local flex row.
  Separating identity from working controls keeps headers compact and makes
  responsive wrapping predictable.
- Scope: `static/styles/components.css`, Planning and Thinking headers, Events
  header, Agent headers, Board search/filter toolbar, Actions, Library, History,
  Context, Help, Health, Supervisor, Chat, Mission Control, and nested working
  toolbars.
- Constraints: Feature controls that genuinely require a full-width editor row
  may retain that row through `.ui-toolbar`. Modal, artifact, and settings
  headers remain part of the large-modal family. Window/layout chrome stays
  outside panel content headers.
- Verification: `tests/frontend_components.test.js` protects the shared API,
  canonical consumer markup, and removal of duplicated header geometry from
  feature styles. Existing panel suites protect rendering behavior; live checks
  verify same-row Planning/Thinking actions and wrapping toolbar geometry.

### D-010 — Menus share a raised surface, compact rows, and focus lifecycle

- Date: 2026-07-14
- Status: accepted
- Decision: Compact menus and popovers use a 10px raised surface with the shared
  floating shadow and border, 28px menu rows, explicit selected/destructive/
  disabled states, viewport height limits, and keyboard-aware focus lifecycle.
- Rationale: Group actions used 6px corners and 24px rows while Board filter and
  View popovers used separate 4px surfaces and unrelated hover/selected states.
  They also differed in whether opening moved focus and whether Escape returned
  it. One primitive makes transient controls predictable visually and
  operationally.
- Scope: `static/styles/components.css`, the shared context menu used by group
  actions, the compact group switcher, Board filter popovers and View, task
  label/dependency suggestions, inline Board assignment/lane/batch editors,
  terminal task/slash/history surfaces, the panel switcher, and all context-menu
  producers.
- Constraints: Searchable filters and View controls use dialog semantics because
  they contain inputs and selects, not only commands. Outside-pointer dismissal
  must preserve the pointer's new focus target. Autocomplete listboxes keep focus
  in their owning editor and expose the active option through `aria-selected`.
  Full modals remain governed by the modal family.
- Verification: `tests/frontend_components.test.js` protects shared geometry,
  canonical markup, semantics, keyboard traversal, Escape restoration, and
  removal of duplicate feature geometry. Existing Board and group navigation
  suites protect behavior; live checks verify computed surface/item geometry,
  focus entry, Escape restoration, and absence of console errors.

### D-011 — Small dialogs use a structured raised shell

- Date: 2026-07-14
- Status: accepted
- Decision: Small dialogs use a 360px raised surface with 10px corners, the
  shared floating shadow, and explicit header, scrollable body, and bordered
  action footer regions. The visible title supplies the accessible name, and
  simple dialog focus is managed by the shared controller.
- Rationale: The legacy 280px unshadowed shell made routine forms feel cramped,
  while titles, summaries, fields, and actions relied on incidental whole-panel
  padding. Confirmations also used an invisible accessible label instead of a
  visible title. A structured shell gives routine dialogs a predictable reading
  and keyboard order without forcing large editors into the same layout.
- Scope: `static/styles/components.css`, shared confirm and input dialogs, New
  Group, Edit Agent/Terminal, and the shared modal focus controller.
- Constraints: The destructive commit remains visually explicit and follows the
  cancel action. Task, group/global settings, artifact, diff, Engineer launch,
  and other large or multi-section dialogs keep specialized layouts pending
  deliberate migration. The overlay/backdrop remains modal infrastructure in
  `static/styles/modals.css`.
- Verification: `tests/frontend_components.test.js` protects shared geometry,
  canonical markup, visible labelling, focus-controller adoption, and removal of
  duplicated shell/footer geometry. Modal helper and edit-popup suites protect
  focus, Escape, submit, and payload behavior; live checks verify New Group and
  confirmation geometry, focus restoration, and console cleanliness.

### D-012 — Semantic metadata uses one badge grammar

- Date: 2026-07-14
- Status: accepted
- Decision: Non-interactive metadata badges use a shared pill boundary, compact
  typography, density variants, and neutral/accent/success/warning/danger intent
  classes. Status meaning is carried by text and reinforced by border and tint.
- Rationale: Health states used padded pills, journal types used small rounded
  rectangles without borders, and agent identity badges repeated the same
  micro-pill geometry five times. One primitive makes metadata recognizable
  across surfaces while preserving Torque's dense agent grid.
- Scope: `static/styles/components.css`, agent-card identity/class badges,
  Agent-panel journal entry types, Health supervisor/coverage states, Board task
  metadata labels, Board lane/filter/selection count indicators, and Agent Profile
  assignment/status/lifecycle preview badges, History identity/status/outcome
  markers, and dismissed-event markers.
- Constraints: Pills remain appropriate for short metadata. Interactive filter
  chips, presets, tabs, actions, and compound status-bar controls are not badges
  and retain their own semantics and geometry. A nested count badge annotates a
  control without replacing its control boundary. Clickable Board engineer,
  GitHub, dependency, and attachment chips remain control chips rather than
  badges. Agent Profile class selectors and assignment actions remain controls;
  History filters, task links, focus actions, and expandable event rows remain
  controls. Status-bar segments remain a separate rectangular component family.
- Verification: `tests/frontend_components.test.js` protects shared geometry,
  semantic variants, canonical consumer markup, and removal of duplicated badge
  geometry. Existing Agent-panel, Health, and Board suites protect rendered
  content; live checks verify card, profile, History, and event metadata, nested
  counts, control-chip separation, and console cleanliness.

### D-013 — Status metadata and actions use explicit status-bar segments

- Date: 2026-07-14
- Status: accepted
- Decision: The bottom workspace rail uses one rectangular status-segment
  primitive with explicit passive and action variants. Action segments are native
  buttons; passive segments remain non-interactive metadata. State uses normal,
  warning, danger, muted, and unknown modifiers without adopting badge geometry.
- Rationale: Provider/runtime metadata and task/navigation actions previously
  shared an undifferentiated “chip” class, and Tasks/Attention simulated buttons
  on spans. Explicit semantics make keyboard behavior native and keep the rail's
  compact segmented identity distinct from passive pills elsewhere in Torque.
- Scope: `webview.html`, `static/js/status_bar.js`, `static/js/relay_status.js`,
  and `static/styles/workspace-shell.css`.
- Constraints: Stable element ids remain unchanged because status updates patch
  nodes in place. Presence dots remain circular. Count text within an action does
  not change the native button boundary.
- Verification: Status-bar and metrics suites protect state mapping and stable
  updates; component and standalone-layout tests protect semantic markup,
  geometry, responsive behavior, and the passive/action split. Live verification
  checks connection, runtime, provider, task, and attention segments plus console
  cleanliness.

### D-014 — Counts are passive badge annotations

- Date: 2026-07-14
- Status: accepted
- Decision: Passive totals use `.ui-badge`, a density variant, semantic intent,
  and `.ui-badge--count`. Dense grid annotations use `--micro`; panel and feature
  annotations use `--compact`. Neutral is the default, while attention, pending,
  acknowledgement, and delivery counts use danger, warning, or accent intent.
- Rationale: Group, terminal, Chat, Events, Mission Control, Initiatives, and
  Agent-panel totals repeated slightly different padding, radius, typography,
  and color rules despite serving the same annotation role.
- Scope: Group headers and tabs, terminal drawers and agent cards, pending hires,
  Chat thread/message state, action transitions, Events attention, Mission
  Control sections, Planning and Thinking tabs and summaries, Board schedule
  runs, Behavior collection totals, Initiative and Area summaries, Agent
  hierarchy, worklog, event, message, journal, roster, stream, task-health, and
  verification counts.
- Constraints: A count inside a tab, filter, disclosure, or menu does not become
  a separate control. Prose metrics, time-based countdowns, and values in data
  tables remain text. Count labels may include a short noun when the number alone
  would be ambiguous.
- Verification: Component tests require canonical count classes across every
  consumer family and reject reintroduced feature-local count geometry. Focused
  frontend suites protect rendered labels; live checks cover grid, panel, and
  feature counts plus console cleanliness.

### D-015 — Panel chrome opts into canonical structure directly

- Date: 2026-07-14
- Status: accepted
- Decision: Every top-level panel uses the canonical header hierarchy directly,
  and every search, filter, selector, or editor row uses the canonical toolbar
  primitive. Legacy consumer class names may remain as hooks but no longer act as
  compatibility aliases for shared geometry.
- Rationale: Compatibility selectors hid incomplete migrations and allowed a
  panel to appear standardized without declaring its structure. Direct opt-in
  makes markup intent reviewable and prevents later consumer rules from quietly
  rebuilding the same header and toolbar geometry.
- Scope: Actions, Library, History, Context, Help, Health, Supervisor, Chat,
  Events, Mission Control, Thinking list views, Agent events/session maps, and
  diff controls, plus the shared header and toolbar definitions.
- Constraints: Consumer classes continue to own sticky positioning, local
  backgrounds, content-specific widths, and other surface behavior. Modal,
  artifact, and settings chrome follows D-017.
- Verification: Component tests require direct canonical classes across every
  migrated consumer and reject compatibility geometry in shared or feature CSS.
  Focused panel suites protect rendering and interaction; live checks cover
  representative panels, working controls, focus, and console cleanliness.

### D-016 — Transient choices use one surface with two focus models

- Date: 2026-07-14
- Status: accepted
- Decision: Command menus, searchable popovers, and editor suggestions share the
  canonical raised surface and row grammar. Command menus move focus into the
  first working row and support Arrow/Home/End plus Escape restoration.
  Autocomplete listboxes keep focus in their input while their canonical option
  rows expose `aria-selected` as keyboard selection changes.
- Rationale: Task suggestions, terminal completions, message history, inline
  Board menus, and context-menu subflows had independently rebuilt surface,
  option, selected, and focus behavior. One visual grammar plus two explicit
  interaction models avoids both styling drift and the accessibility error of
  moving focus out of a live text editor.
- Scope: Shared context-menu normalization, task title/label/dependency
  listboxes, inline Board label/agent/lane and bulk-editor popovers, terminal
  task/slash/history surfaces, and the panel switcher.
- Constraints: Multi-section dialogs remain modals even when visually compact.
  Feature CSS may own placement, width, overflow, and content-specific inner
  layout, but not floating-surface or menu-row geometry.
- Verification: Component tests require canonical opt-in and reject duplicate
  surface geometry. Board, task-modal, terminal, Chat, navigation, and context
  menu suites protect selection and lifecycle behavior; live checks verify
  computed geometry, keyboard focus, Escape restoration, and console cleanliness.

### D-017 — Every dialog declares one canonical shell and explicit regions

- Date: 2026-07-15
- Status: accepted
- Decision: Every modal shell opts into `.ui-modal`, an explicit size variant,
  and an accessible dialog name. Large viewers and workspaces use the same raised
  boundary as small dialogs. Structured dialogs declare canonical header, body,
  and footer regions; height modifiers describe shell behavior rather than being
  recreated in feature styles.
- Rationale: Task, settings, history, prompt, artifact, diff, Help, logs, and
  attachment previews had compatible-looking but independent shell geometry.
  Viewport breakpoints could also override every `.modal` indiscriminately,
  making a declared size unreliable. Direct shell opt-in makes size and structure
  reviewable while preserving each feature's useful internal layout.
- Scope: Static dialogs in `webview.html` and dynamically rendered diff, task
  history, artifact preview, Help browser, log viewer, and attachment-preview
  shells. The shared API includes `--xl`, `--full`, `--tall`, `--viewport`, and
  bordered/flush/split region modifiers.
- Constraints: Feature CSS may own responsive width within the declared size
  ceiling, content height, internal grids, sticky rows, and scroll behavior. It
  must not recreate the outer border, corner radius, background, shadow, shell
  padding reset, or generic tall/wide compatibility classes.
- Verification: Component tests require canonical shells, accessible names,
  explicit regions for major multi-section dialogs, and absence of legacy
  `.modal-tall`, `.modal-wide`, global `.modal` width overrides, and settings
  boundary duplication. Focused modal/diff/artifact suites and live checks cover
  layout, scrolling, controls, Escape behavior, and console cleanliness.

### D-018 — Content states share one semantic surface grammar

- Date: 2026-07-15
- Status: accepted
- Decision: Operator-facing empty, loading, error, and informational states use
  `.ui-state` with an explicit semantic modifier and an explicit placement
  variant where needed. Full states may declare title, message, metadata, and
  recovery-action regions; compact states may remain one line.
- Rationale: Board, Terminal, Help, Planning, Thinking, Agent, history, diff,
  artifact, settings, and navigation surfaces independently rebuilt dashed
  boundaries, muted text, error tints, loading copy, and vertical centering.
  One grammar makes state meaning immediately recognizable and gives recovery
  copy, announcements, and reduced motion a consistent baseline.
- Scope: `static/styles/components.css`, the primary workspace empty state,
  Board, Terminal, Help, Mission Control, Planning, Thinking, Agent and behavior
  panels, task history, worktree diff/history, artifacts, logs, Settings search
  and mappings, and navigation search surfaces.
- Constraints: Feature classes retain placement and content-specific minimum
  height. Field validation, passive metadata, empty select options, disabled
  explanations, specialized canvas guidance, and stale-content notices that do
  not replace a surface remain local. Loading states added asynchronously expose
  polite status semantics; failures added after an operation expose alerts.
- Verification: Component tests protect the shared API, semantic consumer
  markup, reduced-motion behavior, and removal of duplicate feature geometry.
  Focused frontend suites protect rendering and interactions; live checks cover
  representative empty, loading, error, and recovery states plus console
  cleanliness.

### D-019 — Shared visual primitives require direct markup opt-in

- Date: 2026-07-15
- Status: accepted
- Decision: Consumers declare canonical component classes in their markup.
  Shared CSS selectors target `.ui-tab`, `.segmented-control`, `.filter-chip`,
  `.preset-button`, `.ui-card`, `.form-control-sm`, and their documented
  variants—not feature class names. A compact form may opt in once through
  `.form-control-group-sm` when every text-like control shares the same density.
- Rationale: Grouped compatibility selectors made a feature look standardized
  without exposing that intent in its markup. They also coupled the shared
  stylesheet to Planning, Thinking, Board, Agent, Settings, Actions, Library,
  and log-viewer implementation names, so renaming or adding a consumer could
  silently drop the shared behavior.
- Scope: Feature and settings tabs, Agent event subtabs, Board lane tabs,
  Grid/Canvas, Actions/Library, log-target and schedule segmented controls,
  Board and Planning filters, schedule presets, compact Actions/Library/Events
  fields, Board/Context/Agent cards, canonical success/warning actions, and
  `static/styles/components.css`.
- Constraints: Feature classes remain stable hooks for behavior, tests, local
  layout, and content-specific states. Button intent classes such as
  `.btn-primary` and badge intent classes are themselves canonical public API,
  not compatibility aliases. Native text-like controls continue to receive the
  default field primitive automatically.
- Verification: Component tests require direct canonical classes on every
  migrated consumer and reject feature selectors in shared component rules.
  Focused navigation, settings, Board, Agent, Actions, Library, and log-viewer
  suites protect behavior; live checks compare geometry and selected states
  before committing.

### D-020 — Compact navigation remains fully keyboard reachable

- Date: 2026-07-15
- Status: accepted
- Decision: Every `tablist` exposes one selected tab in the sequential focus
  order, uses explicit selected state, and supports Arrow, Home, and End keys
  through `uiTablistKeydown`. Radio-style button groups use the same roving
  focus model through `uiRadioGroupKeydown`. Horizontal tablists opt into
  `.ui-tablist` so long labels scroll within their own navigation row instead
  of clipping or forcing the surrounding panel wider.
- Rationale: Native buttons made every tab reachable with repeated Tab presses,
  but did not provide the keyboard model promised by the ARIA roles. Several
  narrow surfaces also clipped later choices. The shared behavior keeps focus,
  selection, and compact geometry aligned without a framework.
- Scope: Planning, Thinking, Agent and behavior tabs, Board lane tabs, Library,
  log targets, Group and application Settings tabs, Settings accent choices,
  shared tab/modal/popover CSS, icon-only panel and card actions, and primary
  modal/toolbar field labels.
- Constraints: Plain segmented button groups continue using native button and
  `aria-pressed` behavior. Group tabs retain their purpose-built keyboard and
  compact-switcher implementation. Feature renderers still own selection state
  and rerendering; the shared handler only moves focus and activates a choice.
- Verification: Component and focused frontend suites protect roving state,
  accessible names, label associations, compact overflow, and compact viewport
  fit. Live desktop checks exercise keyboard selection, focus retention,
  labelled fields, dialog lifecycle, and console cleanliness.

### D-021 — Narrow layouts preserve action priority and component parity

- Date: 2026-07-15
- Status: accepted
- Decision: Responsive substitutions preserve the same essential actions and
  keyboard model as their wide counterpart. Compact group navigation keeps
  search, group creation, group actions, and full Arrow/Home/End movement. Dock
  and rail panel rows expose real roving tablists whose tabs scroll inside the
  header while zone actions remain fixed; workspace rebuilds preserve the
  focused zone tab across the persisted-layout server echo. Status-bar collapse
  removes passive workload and redundant detail before active tasks or deploy
  state, and never hides Attention. Metadata badges truncate within their owner
  and keep an 8px minimum label size.
- Rationale: The individual components looked standardized at wide widths, but
  the audit found that several narrow transitions dropped behavior or reversed
  information priority. A responsive substitute is part of the same component,
  not a reduced feature set.
- Scope: Group tabs and their compact switcher, standalone bottom-dock and
  right-rail tabs plus their rerender focus lifecycle, History modes, Board
  filter clearing, archived-decision filters, Grid agent and terminal identity
  cards, status-bar breakpoints, badge density, and the remaining high-level
  Actions, Library, Context, History, Chat, Events, Supervisor, Health, AI, and
  behavior-overlay states.
- Constraints: Feature-specific layout geometry and breakpoint thresholds may
  remain local literals. Repeated component semantics use shared tokens and
  direct canonical classes. Responsive hiding is allowed only when another
  visible path provides the same action and higher-priority state remains.
- Verification: Source contracts cover compact action parity, responsive
  breakpoints, direct filter/card/state opt-in, badge legibility, and status
  priority. Focused frontend suites exercise the compact switcher, roving zone
  tabs, and focus retention across workspace rebuilds. Live checks exercise
  keyboard switching in both dock zones through the server echo, representative
  state surfaces, status priority, and console cleanliness.

### D-022 — The first component-standardization baseline is complete

- Date: 2026-07-15
- Status: accepted
- Decision: The standardization inventory is the maintained baseline for new
  Torque UI work. Every listed family now has a canonical component grammar or
  an explicit boundary that keeps it outside another family. New consumers opt
  into those primitives directly; a genuinely new family updates this document,
  its source contracts, and its live verification path in the same slice.
- Rationale: Consolidation is only durable when completion means more than a
  visual pass. The baseline couples documented semantics, direct markup intent,
  regression contracts, rerender behavior, responsive behavior, and live
  operator verification so later feature work cannot silently recreate the old
  drift.
- Scope: Foundations and tokens; group, panel, and feature navigation; segmented
  controls; filters and presets; buttons; fields; cards; panel chrome; status
  segments; menus and popovers; dialogs; badges and counts; content states; and
  the accessibility and responsive rules in D-001 through D-021.
- Constraints: “Complete” does not freeze the system or prohibit local feature
  geometry. It means additions reuse the documented grammar, extend it
  deliberately when semantics differ, and keep `DESIGN.md` current. Maintenance
  concerns in the inventory remain active review rules rather than pending
  migration work.
- Verification: The closing audit ran all frontend Node contracts, the Python
  frontend wrappers, and the full 2,447-test regression suite (82 expected
  skips). Live standalone verification selected all six right-rail and all eight
  bottom-dock panels, exercised persisted-layout keyboard focus, opened and
  closed Settings and New Group, confirmed representative async state surfaces
  and status priority, and finished with an empty browser console.

### D-031 — Unbounded review content uses progressive disclosure

- Date: 2026-07-15
- Status: accepted
- Decision: Review surfaces whose content can grow with repository or runtime
  history must bound their initial DOM. The worktree diff viewer keeps small
  diffs fully expanded, opens large multi-file diffs with one reviewable file
  visible, leaves very large single files collapsed, and renders expanded file
  bodies in 400-line chunks with explicit continuation controls.
- Rationale: Building every file, hunk, and line before the first interaction
  makes the interface unresponsive precisely when review volume is highest.
  Progressive disclosure keeps the summary and file inventory immediate while
  preserving direct access to every line on demand.
- Scope: Worktree diff rendering in `static/js/diff.js`; the same bounded-first
  rule applies to future unbounded logs, histories, and review collections.
- Constraints: Small diffs preserve the established fully expanded behavior.
  Collapse state and loaded chunks are view-local and are not persisted. No
  diff content is discarded; chunking only controls browser rendering.
- Verification: Frontend performance regressions cover automatic large-diff
  collapse, very-large-file deferral, 400-line chunking, continuation, and the
  unchanged small-diff path.

## Decision entry template

Copy this section for a new durable decision:

```markdown
### D-NNN — Short decision title

- Date: YYYY-MM-DD
- Status: proposed | accepted | superseded
- Decision: What is standardized.
- Rationale: Why this is the preferred rule.
- Scope: Components and files affected.
- Constraints: Intentional exceptions and non-goals.
- Verification: Tests or manual checks that protect the decision.
```

When a decision changes, keep the old entry, mark it superseded, and link to the
new decision so the design history remains understandable.
