# Torque Design System

Status: living document
Last updated: 2026-07-17

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

### Scrollbars

- Native scrollbars use `--scrollbar-thumb` with a transparent track. Hover uses
  `--scrollbar-thumb-hover`; feature surfaces must not derive a separate thumb
  color from text or accent tokens.
- Vertical scrollbars are 5px. Compact horizontal navigation scrollbars may be
  4px when space is constrained.
- Panel-tab overflow keeps that 4px visual thumb inside an 8px native hit
  target. Transparent thumb borders enlarge the grab area without adding
  visual weight.
- Surfaces governed by these pixel tokens keep the standard `scrollbar-width`
  and `scrollbar-color` properties at `auto`; non-auto values override the
  explicit WebKit geometry in Chromium.
- Scrollbars remain natively operable. Hiding one is reserved for surfaces with
  an equivalent visible navigation control.

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
- Historical errors appear only while the current status remains unhealthy.
  A current ready or recovered state suppresses stale failure detail.
- Optional `.ui-state__title`, `__message`, and `__meta` regions establish a
  consistent hierarchy for full states. Compact one-line states may omit them.
- Feature classes may own placement, width, and minimum height. They must not
  rebuild state boundaries, semantic colors, type hierarchy, or loading motion.
- Metadata such as “no assignment,” disabled explanations, validation text
  beside a field, and specialized canvas instructions remain local when they do
  not replace a content surface.

### Feedback, alerts, and notifications

- Inline validation stays beside the control or operation it belongs to. It is
  not copied into the Inbox unless the failure outlives that local context or
  needs later recovery.
- Toasts acknowledge transient actions. They stack at the bottom-right, can
  always be dismissed, pause while hovered or focused, and may offer one typed
  action. Information and success feedback expire; error overlays remain until
  dismissed. Closing an overlay never deletes a durable Inbox record.
- Alerts are durable problems with an open/resolved lifecycle. They remain
  prominent until resolved, dismissed, or archived. Repeated occurrences update
  and reopen the existing alert instead of creating an indistinguishable pile.
- Notifications are durable awareness items with an unread/read lifecycle.
  They remain available after their delivery overlay disappears and across
  reconnects or restarts.
- The Inbox is the history and action surface for both types. It opens from a
  global notification bell rather than occupying a dockable workspace panel;
  alerts and notifications are application-level state, not project content.
  The bell badge counts open alerts plus unread notifications. Alert and
  Notification views remain separate because acknowledgement and resolution
  are different acts.
- Inbox actions are typed application routes such as Open task, Open agent,
  Retry, or Open panel. Persisted records never contain executable UI code.
- Desktop notifications are an optional delivery channel. Disabling them does
  not disable durable Inbox recording.

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
| Panel tabs | Standardized, all zones audited | Preserve pickup feedback, explicit insertion order, roving focus, and scrollability |
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

### D-023 — Panel-tab dragging shows and preserves placement intent

- Date: 2026-07-15
- Status: accepted
- Decision: Dragging a docked panel tab creates a compact cursor-following
  preview, visibly lifts the source tab, highlights the destination dock, and
  renders an accent insertion marker between tabs. The dock highlight is a
  pointer-transparent overlay above panel content so opaque surfaces cannot
  obscure it. Dropping on a tab strip persists that exact insertion position
  within the same dock or across docks. Dropping into the body of the panel's
  current dock preserves its existing tab position instead of silently moving
  it to the end. When the strip overflows, it remains natively scrollable but
  uses the canonical compact scrollbar thumb on a transparent track instead of
  platform chrome.
- Rationale: Dock-only highlighting communicates the destination region but not
  the resulting order, and remove-then-append behavior makes an accidental drop
  mutate a layout that appeared unchanged. Pickup and insertion feedback lets
  the preview match the persisted result before the user releases the pointer.
- Scope: Standalone bottom-dock and right-rail panel tabs,
  `static/js/panel_manager.js`, and `static/styles/workspace-shell.css`.
- Constraints: The preview is transient presentation state and is never
  persisted. Layout persistence remains the source of truth after drop. Drops
  on a different dock's panel body append because no more specific insertion
  position was expressed; same-dock body drops are position-preserving.
- Verification: Navigation source contracts protect the lifted source,
  cursor-following preview, insertion marker, and above-content dock overlay.
  Focused frontend regressions cover same-dock reordering, cross-dock indexed
  insertion, same-dock body-drop stability, preview cleanup, and the existing
  dock/float behaviors.

### D-024 — Agent hierarchy uses architect selectors and engineer team bands

- Date: 2026-07-15
- Status: accepted
- Decision: The agents grid presents architects as a labeled selector and marks
  the architect whose hierarchy is currently shown independently from the
  focused agent. The expanded hierarchy is named `<Architect>'s team`, and each
  engineer anchors a contained team band with fixed-width worker cards. The
  architect selector and every team row share the same card-column width and
  horizontal gap and the same row inset, so a surface that fits `N` architects
  also fits one engineer plus `N - 1` workers. The shared inset keeps cards away
  from the team outline without changing that capacity threshold. The band
  provides the ownership signal; dividers and connector rails that consume
  horizontal capacity are intentionally omitted.
  Engineer teams without workers show an explicit `No workers` state.
  Grid-mode and create controls occupy their own in-flow toolbar above the
  selector instead of overlaying the first row of cards.
- Rationale: Agent focus and the hierarchy being inspected can legitimately
  differ. Adjacency alone did not communicate ownership, empty worker shelves
  looked like accidental whitespace, and an absolutely positioned toolbar
  obscured architect cards at narrow widths. Named containment makes the
  architect → engineer → worker relationship readable without changing the
  density or interaction model of the cards themselves.
- Scope: Agents-grid section rendering, architect card hierarchy state, empty
  engineer rows, and grid layout styles in `static/js/grid/sections.js`,
  `static/js/grid/agent-card.js`, and `static/styles/workspace-grid.css`.
- Constraints: Focus, selected-agent state, hierarchy ownership, and runtime
  status remain independent visual signals. Worker cards use the same width as
  engineer cards and wrap without stretching to consume leftover space. Team
  containment uses an inset outline while architect and team rows use the same
  internal spacing, preserving both breathing room and the shared horizontal
  capacity invariant. The hierarchy treatment reflects existing ownership data
  only; it does not mutate assignments. Keyboard order, drag targets,
  narrow-layout scrolling, and card actions remain unchanged.
- Verification: Focused frontend contracts cover the labeled architect selector,
  independent hierarchy-owner state, named team heading, contained engineer
  bands, fixed-width worker wrapping, equal architect/team column capacity,
  explicit empty-worker state, and non-overlaid toolbar geometry.

### D-025 — Worker cards are compact summaries with focus-panel disclosure

- Date: 2026-07-15
- Status: superseded by D-026
- Decision: Worker cards retain the shared fixed column width but use a shorter
  summary geometry. They expose identity, runtime status, current task, and
  current activity; worktree branch, diff, cycle, terminals, and other detail
  remain in the existing focus panel. Clicking a worker selects it and expands
  that focus panel when necessary. Incremental selection updates reconcile the
  complete `selected`, `is-selected`, and `focused` class set so presentation
  cannot retain a stale previous selection.
- Rationale: Workers are the most numerous hierarchy level, so full-detail cards
  made the team bands visually heavy and hard to scan. Progressive disclosure
  preserves operational detail without changing grid capacity or introducing
  an inline expansion mode that would destabilize neighboring cards.
- Scope: Worker rendering and geometry, grid selection synchronization, and
  worker click behavior in `static/js/grid/agent-card.js`,
  `static/styles/workspace-grid.css`, `static/js/render.js`, and
  `static/js/commands.js`.
- Constraints: Compact workers remain full-size drag targets horizontally and
  retain always-visible status, delete/pause controls, provider, context, and
  kind indicators. Task links keep their direct Board action. Engineers and
  architects keep the standard card height. The focus panel is the sole detailed
  disclosure surface and clicking non-workers does not force it open.
- Verification: Frontend regressions cover compact worker markup and geometry,
  omission of duplicated detail, focus-panel reveal on worker click, non-worker
  collapse preservation, and atomic selection/focus class reconciliation.

### D-026 — Worker summaries use a two-line micro-card

- Date: 2026-07-15
- Status: superseded by D-027
- Decision: The compact worker treatment is a two-line micro-card at roughly
  half the engineer-card height. The first line is worker identity. The second
  is the linked task when one exists, otherwise current activity or `no task`.
  Runtime status remains the card's status indicator, while the card tooltip and
  Focus panel carry the fuller activity context. Stopped-worker relaunch stays
  in the Focus panel instead of adding a third in-card control row.
- Rationale: The initial three-line compact card remained visually too close to
  the standard 108px card, especially when the stopped-state relaunch control
  contributed layout height. A worker summary should read as a subordinate
  index item immediately, not as a slightly shorter detail card.
- Scope: Worker-card body rendering and grid geometry in
  `static/js/grid/agent-card.js` and `static/styles/workspace-grid.css`.
- Constraints: Width, drag behavior, status, delete/pause controls, context
  meter, provider, and Worker kind indicator remain unchanged. The linked task
  remains directly actionable. Activity is omitted from the card body when a
  task is present but remains available in the card tooltip and Focus panel.
  Engineer and architect card geometry is unchanged.
- Verification: Frontend contracts protect the two-line conditional body,
  52px/56px compact height tokens, stopped-card height stability, and the
  existing click-to-Focus interaction.

### D-027 — Worker cards return to the standard detail geometry

- Date: 2026-07-15
- Status: accepted
- Decision: Worker cards again use the shared engineer/agent card height and
  show task, cycle, diff, branch, and current activity in place. Clicking a
  worker follows the standard agent-selection behavior and does not forcibly
  expand the Focus panel. The independent incremental-selection reconciliation
  introduced alongside D-025 remains in effect.
- Rationale: Both compact experiments made the hierarchy feel visually
  unbalanced without producing a worker summary that improved the overall grid.
  The established detailed card is more useful than forcing a density treatment
  that does not fit this surface.
- Scope: Worker card rendering and agents-grid geometry in
  `static/js/grid/agent-card.js`, `static/js/commands.js`, and
  `static/styles/workspace-grid.css`.
- Constraints: Fixed worker width, team-band containment, hierarchy capacity,
  selection correctness, and all earlier card actions remain unchanged.
- Verification: Frontend regressions restore full worker detail, shared card
  height, task-cycle rerender behavior, and standard non-forcing selection.

### D-028 — Agent-grid utilities share the group navigation header

- Date: 2026-07-15
- Status: accepted
- Decision: The Grid/Canvas view choices form one compact segmented control,
  and agent creation is a separate square plus action. In standalone and
  desktop modes these controls live at the trailing edge of the active group
  navigation row; in multi-group layouts the scoped creation action lives in
  each corresponding group header. The former dedicated grid-toolbar row is
  removed.
- Rationale: A full-width row devoted to three small controls consumed vertical
  space without adding hierarchy. Group navigation already establishes the
  scope those controls act on, so combining them preserves one-click access
  while giving the agents grid more room.
- Scope: Group-tab and group-header rendering, Canvas/Grid view controls, agent
  creation affordance, and responsive group navigation in
  `static/js/grid/group-tabs.js`, `static/js/grid/main.js`,
  `static/js/canvas.js`, and `static/styles/workspace-grid.css`.
- Constraints: The view modes remain explicit rather than cycling behind one
  icon. Creation remains available for empty groups and reports the existing
  agent-cap disabled state. Group tabs retain horizontal scrolling, while the
  compact group switcher yields name width before hiding any utility action.
- Verification: Frontend regressions cover header placement, icon-only creation,
  active segmented state, group scoping, agent-cap behavior, and compact-layout
  persistence.

### D-029 — One workspace creation menu owns agents and groups

- Date: 2026-07-15
- Status: accepted
- Decision: The square plus action beside the agent view switcher is the single
  persistent workspace creation entry point. Its menu offers Architect,
  Engineer, and Worker creation in the active group, followed by a separated
  New group action. The duplicate plus button in the application header is
  removed; group creation remains available from the compact group switcher and
  empty-workspace recovery state as well.
- Rationale: Two vertically adjacent plus icons created ambiguity while spending
  header space on the same broad intent. A single labeled menu makes the scope
  explicit after activation and leaves the application header for global
  navigation and settings.
- Scope: Application header markup and the agent-grid creation menu in
  `webview.html`, `static/js/main.js`, `static/js/grid/main.js`, and
  `static/js/commands.js`.
- Constraints: Agent actions remain scoped to the active group. When that group
  is at its agent cap, the menu remains available for New group while presenting
  a disabled agent-limit explanation. Keyboard and compact-switcher group
  creation paths remain unchanged.
- Verification: Frontend regressions cover removal of the duplicate header
  action, menu ordering and separation, New group dispatch, and at-cap menu
  availability.

### D-030 — The utility-rail divider can fit the architect row

- Date: 2026-07-15
- Status: accepted
- Decision: Double-clicking the divider between the agents grid and the utility
  rail expands the grid just enough to fit the visible architect cards on one
  row, capped at four columns. The calculation uses the rendered card width,
  gap, and row padding, and the resolved rail width is persisted like a manual
  drag.
- Rationale: Dragging remains useful for arbitrary layouts, but the common
  intent is to reveal the architect hierarchy without trial-and-error resizing.
  A content-aware shortcut makes that adjustment predictable while preserving
  terminal space when a group contains many architects.
- Scope: The standalone utility-rail resize interaction in
  `static/js/panel_manager.js` and its discoverability label in `webview.html`.
- Constraints: The shortcut only expands; it does not unexpectedly shrink an
  already-wide grid. The utility rail keeps its minimum usable width and the
  outer workspace/terminal divider retains its existing reset behavior. Groups
  with more than four architects continue wrapping after the fourth column,
  and Canvas view or an empty architect row leaves the width unchanged.
- Verification: Frontend regressions cover rendered geometry, the four-column
  cap, width persistence, and the no-shrink behavior.

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

### D-032 — Provider catalogs guide settings without closing the escape hatch

- Date: 2026-07-16
- Status: accepted
- Decision: When an installed provider exposes an account-aware model catalog,
  Torque presents those models and their model-specific reasoning efforts as
  dropdown choices. Every model and reasoning-effort dropdown ends with a
  `Custom…` option that reveals a free-text field, and persisted values missing
  from the current catalog automatically use that editable path.
- Rationale: Detected choices make provider settings faster and less
  error-prone, while catalogs can lag releases, vary by account, or disappear
  when a CLI is unavailable. A visible final escape hatch keeps Torque usable
  without weakening the guided default path.
- Scope: Agent, worker, Engineer, and Architect launch settings; the New Agent
  modal; provider metadata discovery in `torque/provider_catalog.py`; and shared
  model/reasoning controls in `static/js/modals/core.js`.
- Constraints: Discovery is best-effort, cached, and must never block daemon
  startup. Torque stores model and effort values as plain strings and does not
  reject custom values. Codex discovery uses its local account-aware protocol
  with a CLI catalog fallback; providers without catalogs retain the same
  editable controls.
- Verification: Backend regressions cover Codex catalog normalization,
  fallback, and caching. Frontend regressions cover detected ordering,
  model-specific efforts, default labels, `Custom…` as the final option, and
  round-tripping arbitrary model and effort values.

### D-033 — Inheritance controls expose actions, not redundant status prose

- Date: 2026-07-16
- Status: accepted
- Decision: Settings fields that inherit from Group do not render persistent
  captions announcing `Inherited from Group` or `Override active for this agent
  kind`. When a field contains an override, the concise `Use group default`
  action remains available; inherited fields add no secondary status row.
- Rationale: Blank/default values and field placeholders already communicate
  inheritance, while the repeated status captions added visual noise throughout
  Group Settings. The reset action is the only extra control needed when an
  override exists.
- Scope: Shared Group Settings inheritance decoration in
  `static/js/modals/settings-shell.js` and `static/styles/modals.css`.
- Constraints: This is presentation-only. Empty values continue to mean
  inheritance, override values remain unchanged, and resetting a field still
  clears it and marks Settings dirty.
- Verification: Frontend contracts reject both retired captions and the status
  dot while preserving the conditional `Use group default` action.

### D-034 — Settings reset remains inside the explicit save boundary

- Date: 2026-07-16
- Status: accepted
- Decision: `Reset section` restores the active settings section's declared
  control defaults as an unsaved draft. It never submits or closes Settings;
  the operator must use the section's Save action to persist the result. The
  dirty-state caption appears directly beneath that Save action only while
  changes are unsaved, so state and commitment read as one control group without
  adding a persistent clean-state label.
- Rationale: Restoring the persisted snapshot made Reset appear to save
  immediately because it cleared the dirty state and disabled Save. Reset is a
  potentially broad edit, so its result should remain reviewable and reversible
  until the operator explicitly commits it.
- Scope: Shared Group and Torque Settings footer and reset behavior in
  `webview.html`, `static/js/modals/settings-shell.js`, and
  `static/styles/modals.css`.
- Constraints: Reset affects only the active primary section. Client-local
  appearance values may still preview immediately, but are not persisted until
  Save. Cancel and close-discard restore the captured persisted baseline.
- Verification: Frontend regressions cover footer ordering and clean-state
  caption suppression, and confirm Reset changes controls to their declared
  defaults, marks the dialog dirty, reveals the caption, enables Save, and does
  not invoke a persistence path.

### D-035 — Single-purpose group actions are direct controls

- Date: 2026-07-16
- Status: accepted
- Decision: The active group tab exposes Group Settings as a direct gear button
  in both the full tab row and compact group switcher. The former one-item
  overflow/context menu is removed.
- Rationale: A menu adds an interaction step and suggests multiple choices. When
  Settings is the only available action, a labeled direct control communicates
  the result before activation and opens it in one click.
- Scope: Standalone group navigation in `static/js/grid/group-tabs.js`,
  `static/js/commands.js`, and `static/styles/workspace-grid.css`.
- Constraints: The button remains scoped to the active group, stops propagation
  so it does not retrigger tab selection, remains available in compact layouts,
  and has an explicit accessible name and tooltip.
- Verification: Frontend contracts require direct `openGroupSettings` controls
  in both layouts and reject the retired ellipsis, menu semantics, and
  single-item context-menu handler.

### D-036 — Conditional settings expose the governing choice first

- Date: 2026-07-16
- Status: accepted
- Decision: Settings with several dependent behaviors begin with an explicit
  governing choice, then organize the remaining controls into named sections.
  In Workers → Worktree, workspace isolation is a select rather than a checkbox;
  inactive worktree settings remain visible, dimmed, and disabled so operators
  can understand and retain the configured policy. Related boolean combinations
  may be represented by one exhaustive select when every stored combination
  round-trips without loss. Section-local headings, labels, and helper text rely
  on the navigation context instead of repeating the current agent kind.
- Rationale: A single checkbox that hid the entire Worktree configuration made
  the section difficult to discover and understand. Independent checkpoint
  checkboxes obscured the policy they collectively represented, while showing a
  local squash option during PR-only merging implied that it affected GitHub's
  merge path. Governing choices and scoped sections make dependencies explicit
  without discarding advanced configuration.
- Scope: Workers → Worktree structure, checkpoint mapping, merge-mode guidance,
  direct-merge history, post-merge behavior, and shared-path controls in
  `webview.html`, `static/js/modals/worktrees.js`,
  `static/js/modals/group-settings.js`, and `static/styles/modals.css`.
- Constraints: Existing persisted booleans remain the storage contract. The
  checkpoint selector preserves manual, stop-only, progress-only, and
  progress-plus-stop states. Pull-request mode always communicates GitHub
  squash behavior and hides local history controls; Direct and Engineer choice
  expose the history policy used only by direct local merges. Post-merge and
  shared-path settings stay independent of merge mode. The previously exposed
  merge-instructions field remains backend-compatible storage but is omitted
  from the UI because no runtime consumes it.
- Verification: Frontend regressions protect the section hierarchy, all
  selector-to-setting mappings, merge-mode disclosure, inactive-state
  presentation, concise context-aware copy, payload compatibility, and removal
  of the inert field.

### D-037 — General settings share one hierarchy and expose resolved inheritance

- Date: 2026-07-16
- Status: accepted
- Decision: Group, Workers, Engineers, and Architects General settings use the
  same bordered section-card and responsive field-grid system. Group settings
  are divided into Workspace, Environment, and Limits & visibility. Worker
  settings are divided into Launch, Runtime, and Session. Engineer and Architect
  settings use parallel Launch and Runtime sections. Every kind-specific Launch
  section orders Provider, Model, Reasoning effort, then the visually secondary
  Command override. Architect directory and shell controls live in General →
  Runtime rather than System. Section bodies maintain the standard 10px inset
  on every edge so the first form row remains visibly separated from the
  section header and divider.
- Rationale: The former General panes alternated between unstructured field
  stacks, prose dividers, and section cards, so equivalent settings appeared to
  have different semantics. Generic `Group default` labels also required the
  operator to navigate elsewhere to learn the effective value. A shared
  hierarchy makes scanning transferable across agent kinds, while resolved
  `Inherit · value` options make the current behavior legible in place.
- Scope: Group Settings General-pane markup, shared responsive field styles,
  inherited provider/model/reasoning/command/runtime labels, reset
  synchronization, Architect runtime placement, operator documentation, and
  frontend regression coverage.
- Constraints: This is a presentation and form-composition change only. All
  control ids, submitted fields, empty-value inheritance semantics, custom model
  and reasoning escape hatches, and backend persistence contracts remain
  unchanged. Directory, command, environment-file, and environment-variable
  controls span the full row; compact controls may share two columns and stack
  to one column on narrow layouts.
- Verification: Frontend regressions protect section names and ordering, shared
  Launch order, secondary command treatment, Architect runtime relocation,
  responsive field geometry, header-to-form spacing, resolved inheritance
  labels, and unchanged submit payloads.

### D-038 — Every Group Settings pane uses semantic sections

- Date: 2026-07-16
- Status: accepted
- Decision: Every Group Settings sub-pane uses the shared bordered section-card,
  10px body inset, responsive field grid, and concise context-aware labels.
  Group → Agents uses Launch defaults. Sync provider uses Connection,
  Repository & project, Board mapping, Issue behavior, and Assignees. Advanced
  uses Guidance. Worker notifications separates Delivery from Events. Engineer
  Behavior uses Specializations, Orchestration, Communication, and Policy
  overrides; Engineer System uses Permissions, Digest delivery, and Events.
  Architect Behavior uses Orchestration, Continuity, and Instructions;
  Architect System uses Digest delivery and Events.
- Rationale: Flat control stacks obscured relationships and made equivalent
  concepts look unrelated across panes. Repeating words such as `Default`,
  `Engineer`, or `Architect` in every label added length without adding scope.
  Stable section names make the settings hierarchy scannable and transferable
  while leaving detailed explanation to helper text and tooltips.
- Scope: Remaining Group Settings markup and copy, Worker notification
  dependency state, Engineer and Architect digest event presentation, shared
  settings styles, operator documentation, and frontend regression coverage.
- Constraints: Control ids, persistence keys, payload shapes, provider catalog
  behavior, notification presets, and all existing backend semantics remain
  unchanged. Governing choices keep dependent settings visible but dimmed and
  disabled. Required digest events are informational badges rather than disabled
  form controls; only optional events are editable. Custom instructions and
  system-prompt previews remain paired in the same section.
- Verification: Frontend contracts protect every section hierarchy, concise
  labels, responsive geometry, Worker notification disable/restore behavior,
  human-readable event names, required-versus-optional event presentation, and
  unchanged settings submission.

### D-039 — Worker roles live with Worker launch settings

- Date: 2026-07-16
- Status: accepted
- Decision: The default role control lives in Workers → General → Launch and
  applies only to Worker launches. Group → Agents contains only provider,
  model, reasoning-effort, and command defaults that genuinely apply across
  Workers, Engineers, and Architects. Sparse Group panes use layouts suited to
  their content: Shared launch defaults uses a balanced three-column grid, and
  Advanced constrains its single guidance field instead of stretching it across
  a half-empty form grid.
- Rationale: Roles describe dispatch-time Worker behavior. Presenting Role as a
  shared Group agent default suggested that Engineers and Architects used the
  same role taxonomy, and the shared launch resolver could make that suggestion
  real by applying the default role to those kinds. The former sparse layouts
  also made Agents and Advanced appear unfinished or malformed at desktop modal
  widths.
- Scope: Group Settings markup and responsive styles, Worker default-role
  resolution, launch-service role boundaries, operator documentation, and
  regression coverage.
- Constraints: The persisted `default_agent_template` key is retained for
  backward compatibility. Explicit Engineer or Architect launch templates used
  by their dedicated creation flows still work; only the implicit Group default
  is Worker-exclusive.
- Verification: Backend tests protect Worker inheritance and Engineer/Architect
  exclusion. Frontend contracts protect the control location, shared-launch
  copy, balanced sparse-pane layouts, responsive stacking, and unchanged
  settings payload key.

### D-040 — Settings footer stays outside the scrollable workspace

- Date: 2026-07-16
- Status: accepted
- Decision: Structured settings dialogs keep their header, scrollable
  navigation/content workspace, and footer as three sibling grid rows. The
  workspace itself has exactly one full-height row shared by the primary
  navigation and active settings pane.
- Rationale: Nesting the footer inside the workspace creates implicit grid rows.
  Content-heavy panes can hide the mistake, while sparse panes such as Group →
  Agents and Advanced collapse the navigation and content into the top portion
  of the dialog and leave the footer separated by empty space.
- Scope: Group Settings modal structure, workspace grid geometry, and static
  layout regression coverage.
- Constraints: Only the active settings pane scrolls. The primary navigation
  remains fixed beside it, and the footer remains fixed below both.
- Verification: Static HTML parsing asserts that the footer is a direct child
  of the settings dialog, and CSS coverage protects the workspace's single
  `minmax(0, 1fr)` row.

### D-041 — Global operational surfaces and saves are first-class

- Date: 2026-07-17
- Status: accepted
- Decision: Daemon and Relay are separate primary Global Settings sections.
  Global Settings exposes one persistent `Save changes` action that coordinates
  ordinary profile settings and AI settings, even though each domain keeps its
  own backend command. Daemon remains a read-only status surface and therefore
  hides the save action; Relay remains capability-gated and hidden when the
  current runtime does not expose it.
- Rationale: Daemon lifecycle and remote Relay connectivity are distinct
  operational concepts and should not be buried beneath a generic System
  category. Multiple save buttons made the dialog's persistence boundary
  ambiguous and encouraged operators to wonder which edits each action covered.
- Scope: Global Settings navigation, Daemon and Relay visibility, footer
  actions, AI settings coordination, dirty-state tracking, and frontend
  regression coverage.
- Constraints: AI provider secrets remain write-only and never enter global
  settings payloads, snapshots, or logs. A pending embedding-index rebuild is
  confirmed before either write begins. When both domains are dirty, the dialog
  stays open until the AI write succeeds and remains open with its error state
  if that write fails.
- Verification: Frontend contracts protect the primary Daemon/Relay sections,
  capability gating, the single footer action, merged dynamic AI dirty state,
  coordinated profile and AI commands, and deferred modal close.

### D-042 — Durable Inbox separates alerts from notifications

- Date: 2026-07-17
- Status: accepted
- Decision: Torque persists operator-facing alerts and notifications in one
  SQLite-backed Inbox while preserving separate semantics. Alerts use
  open/resolved state; notifications use unread/read state. Both support
  archive/restore, deduplication, occurrence counts, typed navigation or retry
  actions, reconnect-safe WebSocket deltas, and optional desktop delivery.
  Transient toasts are a dismissible delivery layer, not the historical source
  of truth.
- Rationale: A four-second overlay cannot support recovery, auditing, or an
  operator who is away. Treating every error as a notification would create
  noise and erase the distinction between “something failed” and “something
  happened.” A shared storage/delivery substrate with distinct lifecycles keeps
  the system reliable without flattening those meanings.
- Scope: Operator-notice schema and persistence, state snapshots and deltas,
  command handlers, agent/task/system notification producers, the dockable
  Inbox panel and badge, toast behavior, board-sync and desktop-client errors,
  Worker notification settings copy, documentation, and regression coverage.
- Constraints: Field validation and errors already represented by a stable
  inline surface remain local. Inbox actions use an allow-listed typed action
  contract and inert payload data. Provider secrets and arbitrary executable
  content are never stored. A repeated alert reopens and increments its record;
  resolving, dismissing, or archiving does not erase history. This decision
  supersedes D-038 only where D-038 treated Worker event choices as dependent
  on macOS delivery; those choices now govern durable Inbox recording and stay
  editable independently.
- Verification: Persistence tests protect migration, deduplication, lifecycle,
  summary counts, and typed actions. Frontend tests protect Inbox registration,
  badge and delta behavior, filters, action commands, durable error routing,
  toast controls, and workspace-layout integration.

### D-043 — Inbox lives in global chrome, not the panel workspace

- Date: 2026-07-17
- Status: accepted
- Decision: The durable Inbox opens from a notification bell in Torque's
  application chrome. In the browser header, the bell is aligned to the far
  right so global attention state remains visually distinct from workspace
  commands. It uses an anchored overlay with the existing separate Alert and
  Notification views. It is not a dockable, detachable, floatable, pinnable, or
  Go To panel.
- Rationale: Alerts and notifications describe the whole application and must
  remain reachable regardless of the active workspace layout. Treating the
  Inbox as peer content beside Board, Agent, and Health made global state look
  like an optional project tool and consumed panel-navigation space.
- Scope: Header and desktop-status bell controls, unread/open badge, anchored
  Inbox overlay, toast actions, legacy panel-pin and workspace-layout cleanup,
  and frontend regression coverage. This supersedes D-042 only where that
  decision described the Inbox as a dockable panel with a taskbar badge.
- Constraints: The overlay preserves durable history, separate lifecycles,
  typed actions, pagination, archived-item access, keyboard dismissal, and
  click-away dismissal. Browser and desktop modes must both expose a bell.
- Verification: Frontend tests protect the global bell and overlay contract,
  badge behavior, open/close behavior, removal from panel registries and saved
  layouts, toast routing, and lifecycle commands.

### D-044 — Panel actions reflect runtime capabilities

- Date: 2026-07-17
- Status: accepted
- Decision: Docked and floating panel headers expose `Detach to OS window` only
  when Torque is running inside the Tauri desktop shell. Browser standalone
  keeps in-workspace Float, Dock, and Hide actions but does not render a control
  for a native-window operation it cannot perform.
- Rationale: Showing an unavailable action creates dead chrome and suggests that
  browser tabs have native window-management capabilities. Capability-gating the
  control keeps the panel header accurate without changing desktop workflows.
- Scope: Shared docked and floating panel-header action construction.
- Constraints: Capability detection comes from the native API bridge rather
  than viewport, platform, or user-agent heuristics. The underlying detach
  operation remains guarded as a second line of defense.
- Verification: Frontend regressions cover both unavailable browser and
  available Tauri action sets and protect both header construction paths.

### D-045 — Visible direct-message conversations acknowledge routine delivery inline

- Date: 2026-07-20
- Status: accepted
- Decision: When the visible, focused terminal conversation is for the same
  agent as a routine direct-message notification, Torque renders and persists
  the message inline and acknowledges its Inbox notification without a toast
  or unread badge. A different agent or panel, a hidden/unfocused window, and
  all error delivery retain the normal Inbox/toast behavior.
- Rationale: A second attention signal for a message the operator is already
  reading is noise, while broad app-focus suppression would hide genuinely
  unattended conversations.
- Constraints: The canonical terminal selection and focused agent must both
  match the notification agent; a positive document focus signal, document
  visibility, and terminal
  visibility are required. This changes neither durable message history nor
  alert/error lifecycle semantics, and introduces no background or OS
  notification behavior.
- Verification: Frontend regressions cover exact-conversation suppression,
  off-agent and hidden/unfocused retention, error retention, and focus
  transitions that preserve unread state for unattended messages.

### D-046 — Agent-card menus separate focus from destructive lifecycle actions

- Date: 2026-07-21
- Status: accepted
- Decision: Agent cards focus on ordinary click, so their context menus omit a
  redundant Focus action. Identity copy actions stay adjacent, lifecycle
  Dismiss sits immediately before Delete, and principal cards direct keyboard
  Delete/Backspace to a non-destructive explanation of the right-click flow.
  Engineer summaries show only the numeric worker count, while footer actions
  occupy a reserved row above card metadata.
- Rationale: Shorter menus scan faster, principal deletion needs a more
  deliberate path than a navigation key, and status dots plus overlapping
  footer controls added ambiguity without adding actionable information.
- Scope: Agent grid and canvas context menus, agent cards and hierarchy empty
  states, and agent-grid keyboard deletion.
- Constraints: Right-click authorization and confirmation, card click focus,
  Worker/terminal keyboard deletion, lifecycle behavior, worktree behavior,
  and editable/composer key guards remain unchanged.
- Verification: Focused frontend regressions cover menu order and clipboard
  payloads, role-specific worktree availability, empty/count markup and layout,
  footer action clearance, and Delete/Backspace routing by kind and input state.

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
