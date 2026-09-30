# Torque Design System

Status: living document
Last updated: 2026-08-12

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

1. Reuse or extend the shared semantic tokens: `static/styles/tokens-base.css`
   for the classic UI and `ui/src/design/tokens.css` for the React UI.
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
  height limit. Context menus keep all rows inside that boundary: when their
  content exceeds the usable viewport, the menu itself scrolls vertically.
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
- Toasts acknowledge transient actions. They stack below the global bell at
  the top-right, with the newest item closest to that anchor, can
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

Torque has two frontend implementations during migration. The classic UI has
no build step; its CSS cascade order is explicit in `webview.html`:

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

The React UI lives under `ui/` and mirrors the same design grammar through:

1. `ui/src/design/tokens.css` for semantic values;
2. `ui/src/design/globals.css` for reset, document, focus, motion, selection,
   and scrollbar rules;
3. `ui/src/design/primitives.tsx` and its CSS Module for shared accessible
   controls, menus, dialogs, and state surfaces;
4. feature-local CSS Modules for layout and feature-specific geometry.

React components must not import the classic stylesheets wholesale. A durable
token or component rule must be updated in both implementations while they
coexist, unless the rule applies only to a surface that exists in one client.

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

### D-054 — Secondary surfaces hydrate through typed lazy-resource projection

- Decision: Phase 4 panels request their heavyweight domain slices only after
  the owning product area is opened. Command responses are normalized into
  stable Redux projection keys; live deltas continue to update those same
  keys. Reconnects repeat the active panel's read set once per connection.
- Rationale: Planning, Mission Control, supervisor, catalogs, history, and
  settings are too broad for the compact boot snapshot. Treating command
  responses as one transient “last message” also makes panels race each other.
  Stable resource keys give every panel independent loading/empty/error state
  without expanding the hot snapshot or coupling React to response ordering.
- Scope: React Planning, Control Center, catalog, settings, event, health,
  supervisor, and relay surfaces under `ui/`.
- Consequence: new lazy command response types must be mapped at the protocol
  boundary before feature components consume them.

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
  application chrome. A single app-level control is aligned to the window's
  top-right, outside the column-scoped agent header and bottom status bar, so
  global attention state remains visually distinct from workspace commands. It
  uses an anchored overlay with the existing separate Alert and Notification
  views. It is not a dockable, detachable, floatable, pinnable, or Go To panel.
- Rationale: Alerts and notifications describe the whole application and must
  remain reachable regardless of the active workspace layout. Treating the
  Inbox as peer content beside Board, Agent, and Health made global state look
  like an optional project tool and consumed panel-navigation space.
- Scope: App-level top-right bell control, unread/open badge, anchored Inbox
  overlay, toast actions, legacy panel-pin and workspace-layout cleanup, and
  frontend regression coverage. This supersedes D-042 only where that decision
  described the Inbox as a dockable panel with a taskbar badge.
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

### D-047 — Architect selector order is direct, durable, and motion-coherent

- Date: 2026-07-21
- Status: accepted
- Decision: Architect cards in a Group's labeled selector follow the persisted
  Group member order in both Grid and Canvas. The selector supports same-Group,
  Architect-only drag reordering, including wrapped rows and append space,
  through the existing `move_agent` ordering authority. A lifted source and
  insertion-edge cue explain the pending placement; after authoritative state
  confirms a changed order, displaced Architect cards use a short transform-only
  ease-out from their previous positions.
- Rationale: Creation-time projection hid otherwise durable Group ordering and
  made a successful drag appear ineffective. A single ordering authority keeps
  Grid, keyboard traversal, Canvas, refresh, and restart behavior coherent,
  while explicit insertion and confirmation motion make wrapped reordering
  predictable without a full-grid jump.
- Scope: Architect selector projection and drag interaction, Canvas Architect
  tree ordering, Architect reorder FLIP treatment, and related frontend tests.
- Constraints: Architect drag never reparents, changes Group membership, or
  mutates hierarchy ownership, selection, focus, or expanded-team state. Other
  agent, terminal, and Group drag contracts remain unchanged. Same-position and
  one-card drops do not send mutations. Reduced-motion mode keeps insertion
  cues but removes nonessential lift and reorder travel. Card dimensions,
  four-column fit, and team-band geometry remain unchanged.
- Verification: Frontend regressions protect persisted Grid/navigation and
  Canvas order, exact before/after/append payloads, wrapped target resolution,
  no-op and invalid-drop rejection, transient cleanup, displaced-card FLIP,
  and reduced-motion behavior; state coverage protects save/load ordering.

### D-048 — App-global attention chrome uses the workspace top-right mount

- Date: 2026-08-08
- Status: accepted
- Decision: Persistent app-global attention controls mount in
  `#app-top-right`, the first child of the app-wide `#workspace-shell`. The
  mount is positioned at the workspace's top-right and sits outside the
  column-scoped agent header, terminal workspace, utility rail, panel bar, and
  floating-panel layer.
- Rationale: The agent header deliberately spans only its column (TORQUE:116),
  while the floating-panel layer is runtime-conditional and owned by panel
  lifecycle rendering. The app-wide workspace exists in browser and desktop
  modes and follows in-flow system banners, giving global attention chrome a
  stable window-relative edge without coupling it to either subsystem.
- Scope: The Inbox bell and future persistent app-global attention controls.
- Constraints: The mount must not widen or move the agent header, participate
  in panel placement, or duplicate a control in mode-specific chrome. It is
  hidden with the workspace in detached panel windows and dedicated diff view.
  Anchored overlays remain body-level fixed surfaces with viewport clamping.
- Verification: Frontend regressions protect the single-bell DOM contract,
  mount and mode visibility, badge discovery, accessible expansion state, and
  top-right popover anchoring at narrow and wide viewport widths.

### D-049 — Transient toasts descend from global attention chrome

- Date: 2026-08-09
- Status: accepted
- Decision: Toasts stack below the top-right notification bell, with the newest
  toast nearest the bell and entry motion descending from that anchor.
- Rationale: Bottom-right toasts obstruct the direct-message composer and
  interrupt the operator's primary typing workflow. Aligning transient delivery
  with the global attention control keeps feedback visible without covering the
  composer.
- Scope: Toast placement, visual ordering, and entry motion.
- Constraints: The stack remains click-through while individual toast controls
  remain interactive and keyboard accessible. Toast lifecycle, levels, Inbox
  behavior, and delivery APIs do not change. The header-height token determines
  the vertical offset in both browser and desktop layouts.
- Verification: Frontend regressions protect the top anchor, newest-first
  ordering, descending motion, and pointer-event boundary around the bell.

### D-050 — React uses unstyled accessible primitives, not a themed component suite

- Date: 2026-08-11
- Status: accepted
- Decision: The React client uses React Aria Components for shared interactive
  semantics and focus behavior, then applies Torque's tokens and CSS Modules.
  It does not adopt the visual language of a themed component suite.
- Rationale: Torque needs reliable dialogs, menus, popovers, and keyboard/focus
  behavior, while its dense operator grammar is too specific to inherit a
  consumer-oriented theme without continual overrides.
- Scope: `ui/src/design/`, workspace chrome, Board menus and dialogs, and future
  React feature components.
- Constraints: Prefer native elements for simple controls. Tauri imports stay
  behind the host adapter. Primitive adoption must not introduce Tailwind or a
  CSS-in-JS runtime.
- Verification: React Testing Library covers roles, names, focusable actions,
  and controlled dialog behavior; browser checks cover real focus restoration.

### D-051 — Board drag-and-drop has one mutation path and a keyboard sensor

- Date: 2026-08-11
- Status: accepted
- Decision: The React Board uses dnd-kit pointer and keyboard sensors. Both
  resolve to the existing `board_move_task` or `board_reorder_task` commands;
  the client never mutates authoritative task order optimistically.
- Rationale: One backend-owned mutation path keeps the classic and React clients
  coherent, while the keyboard sensor makes reordering available without a
  pointer and avoids inventing a parallel persistence model.
- Scope: React Board lanes, task cards, empty-lane drop zones, and ordering.
- Constraints: Snapshot/delta confirmation remains authoritative. Routine
  deltas must not clear selection, focus, inline drafts, collapsed hierarchy,
  or viewport position. Reduced-motion rules apply to drag transitions.
- Verification: Unit tests cover ordering/filter/hierarchy projections; browser
  tests cover creation, detail hydration, lane completion, and keyboard access.

### D-052 — React workspace state is split by durability

- Date: 2026-08-11
- Status: accepted
- Decision: Group choice, Board filters, lane sorts, and card density persist
  through existing `ui_*` and `board_set_*` commands. Ephemeral selection,
  focused task, open dialogs, command-palette state, inline drafts, and pipeline
  collapse state live only in the React workspace store or owning component.
- Rationale: Reopening Torque should restore deliberate workspace preferences,
  not stale momentary interaction state. Keeping ephemeral state outside the
  server projection also prevents high-frequency deltas from resetting work in
  progress.
- Scope: React Redux store, Board feature state, group navigation, and command
  palette.
- Constraints: Stable component keys and ownership boundaries must preserve
  local drafts and scroll position during routine task and agent updates.
  Single-task selection remains card-local; bulk-action chrome appears only
  when modifier selection produces a set of at least two tasks.
- Verification: Reducer/component tests cover selection and create flows; live
  delta tests verify full-detail merges without replacing compact records.

### D-053 — One visible terminal surface owns one PTY client

- Date: 2026-08-11
- Status: accepted
- Decision: Each visible React terminal mount owns exactly one imperative xterm
  controller and one `/ws/terminal/{cell_id}` connection. Terminal output writes
  directly to xterm and never enters Redux. Detaching a terminal transfers its
  persisted surface ownership: the main workspace disposes its mount before the
  detached window creates one.
- Rationale: PTY output is too frequent for application-state reconciliation,
  and two visible clients can race last-writer-wins resize and focus commands.
  Explicit ownership preserves scrollback responsiveness while keeping React
  responsible only for terminal chrome and bounded connection status.
- Scope: React agent focus surface, terminal tabs, xterm controller, detached
  Agents/Terminal windows, and the Tauri panel allow-list.
- Constraints: Hidden, zero-size, inactive, and non-owning surfaces never emit
  focus or resize frames. Strict Mode effect replay reuses a short-lived lease
  instead of opening a second socket. Session-id changes create a new controller;
  routine surrounding React renders preserve the existing DOM mount and buffer.
- Verification: Controller tests cover direct output, reconnect, hidden-surface
  suppression, and Strict Mode lease churn. Live browser checks cover agent
  switching, one-xterm cardinality, `0 + 1` main/detached ownership, and clean
  daemon-restart behavior.

### D-054 — Native desktop behavior enters React through a narrow host and menu bridge

- Date: 2026-08-11
- Status: accepted
- Decision: React exposes only named native-menu callbacks, while privileged
  behavior stays behind `DesktopHost` and allow-listed Tauri commands. Window
  labels, detached panel ids, external URLs, and confirmation text are validated
  at the native boundary.
- Rationale: Menu events must reach whichever React surface owns the state,
  without giving product components ambient access to Tauri or a global invoke
  API. The same feature code can then run in a browser and a future Electron
  host.
- Scope: Tauri menus and commands, React application shell, detached windows,
  onboarding, logs, bounds persistence, and supervisor restart.
- Constraints: Tauri imports remain in `ui/src/host/tauri.ts`. Browser hosts
  fail closed for native-only capabilities. Desktop restart targets the terminal
  supervisor in place; it does not terminate the Tauri-owned daemon.
- Verification: Rust tests cover validation and daemon lifecycle; component and
  host tests cover menu callbacks and command arguments; Tauri capabilities
  restrict command access to `main` and `panel-*` windows.

### D-055 — Optional UI products compose validated registries at build time

- Date: 2026-08-11
- Status: accepted
- Decision: Community exports an empty versioned extension registry. A private
  build may compose separately supplied registries through the shared validator;
  invalid ids, core collisions, titles, placements, loaders, or runtime
  capability names fail the build/test path.
- Rationale: Explicit composition preserves tree-shaking and makes absence the
  safe community default. Runtime discovery would expand the native and CSP
  attack surface without a current product need.
- Scope: `ui/src/extensions/`, private product build integration, standalone
  installation, and release checks.
- Constraints: Community modules never import private product paths. Optional
  panels cannot replace core panels, and package output contains no source,
  dependencies, source maps, private directories, or private markers.
- Verification: Registry tests exercise private-build-shaped composition and
  rejection paths; the packaging guard builds and scans the installed community
  artifact in UI and release CI.

### D-056 — Phase 5 quality budgets are executable release inputs

- Date: 2026-08-11
- Status: accepted
- Decision: The React suite protects representative 500-agent/2,000-task state
  hydration plus 1,000 deltas and a 10,000-frame terminal burst with generous
  three-second CI ceilings. Global reduced-motion overrides and xterm screen
  reader mode are explicit test contracts.
- Rationale: Concrete, repeatable workloads detect architectural regressions
  earlier than subjective smoke testing while leaving enough headroom for
  shared CI runners.
- Scope: Protocol projection, terminal controller, global interaction CSS, and
  release preflight.
- Constraints: Terminal output remains outside Redux. Budgets are regression
  alarms rather than end-user latency claims and must be recalibrated with
  recorded evidence if infrastructure changes materially.
- Verification: Vitest runs the workloads and accessibility policy assertions;
  release CI cannot cut a version commit unless the UI suite passes.

### D-057 — React is the default renderer; classic retirement is staged

- Date: 2026-08-11
- Status: accepted
- Decision: The daemon root and primary Tauri launch use React. `/ui-next/`
  remains a compatible React alias and `/legacy/` remains the operational
  classic fallback during burn-in. `TORQUE_UI_DEFAULT=legacy` may change the
  root renderer for one process/profile without entering durable state.
- Rationale: A URL-only cutover is reversible without a database migration and
  lets production evidence accumulate before removing a mature fallback.
  Staging write retirement separately avoids combining the highest-risk rollout
  moment with destructive asset and test removal.
- Scope: daemon routing, browser and Tauri launch behavior, installation,
  diagnostics, operator guidance, and classic fallback ownership.
- Constraints: The Torque maintainers own the fallback for at least 30 days and
  two production releases. Retirement also requires no unresolved P0/P1 React
  regression, no required parity gap, and a successful release-candidate
  rollback drill. Classic writes remain enabled until a separate reviewed
  retirement change. The classic shell exposes an app-wide `New UI` switch to
  `/ui-next/`, so it remains usable even while the root renderer is rolled back.
- Verification: Route/runtime tests protect the default and override; React
  reports bounded redacted client faults to the durable Inbox; browser smoke
  tests cover `/`, `/ui-next/`, `/legacy/`, and profile rollback; release gates
  continue to verify community and Tauri artifacts.

### D-058 — Board drag geometry follows the rendered lane model

- Date: 2026-08-11
- Status: accepted
- Decision: React Board lanes fill their CSS grid tracks, and their sortable
  item order matches the hierarchy-expanded DOM order. Pointer collision favors
  the card directly under the cursor, falls back to the lane body or nearest
  target at seams, and derives server insertion from card midpoints. The grid
  distributes available width evenly; task content wraps and never participates
  in track sizing. Horizontal scrolling begins only when the lane-count minimum
  cannot fit the viewport.
- Rationale: Empty grid-track space created dead drag regions, while a mismatch
  between the sortable item list and rendered hierarchy caused neighboring
  cards to animate out of sequence. Card-center collision also became sticky
  after the first sibling moved, particularly with different card heights.
- Scope: React Board lane layout, pointer and keyboard collision behavior,
  same-lane ordering, and cross-lane placement.
- Constraints: The server remains authoritative; a drag emits one existing
  `board_reorder_task` or `board_move_task` command on drop. Non-manual lane
  sorts do not accept a same-lane manual reorder. During a cross-lane gesture,
  only a transient client-side lane order is updated so the active card joins
  the destination sortable context; the authoritative mutation still occurs
  once, on drop. Positioned cross-lane moves reindex the destination lane. Lane
  borders belong to one lane rather than an ownerless grid gap, the whole lane
  is droppable, and collision filtering never selects the active card itself.
- Verification: Unit tests protect successive downward, upward, keyboard, and
  invalid-target insertion calculations; browser checks cover continuous lane
  drop areas and rendered card movement.

### D-059 — Board lanes progressively mount their task cards

- Date: 2026-08-11
- Status: accepted
- Decision: Each React Board lane mounts its first 20 visible hierarchy rows,
  then reveals additional batches of 20 as a lane-tail sentinel approaches the
  Board's own scroll viewport. Lane headers continue to report the complete filtered task count.
  The lane grid has a viewport-height minimum but grows to the tallest lane so
  separators span the complete scrollable board.
- Rationale: Large completed lanes can contain hundreds of tasks. Mounting all
  of their card components and sortable registrations up front makes routine
  rendering and drag collision work scale with board history rather than the
  operator's current viewport.
- Scope: React Board card rendering, keyboard navigation, sortable registration,
  and full-height lane layout.
- Constraints: The full task snapshot remains client-side and authoritative;
  progressive mounting bounds DOM and drag work but is not server pagination.
  Previously revealed cards remain mounted so scrolling never discards focus,
  selection, or an in-progress drag. Filtering, sorting, grouping, or hierarchy
  collapse starts a fresh render window.
- Verification: Component coverage protects the initial per-lane bound and the
  remaining-task sentinel; UI checks protect lint, types, tests, and production
  build output.

### D-060 — Agent inspection keeps command responses target-scoped

- Date: 2026-08-11
- Status: accepted
- Decision: The React workspace stores auxiliary command responses by response
  type and target identity, while retaining the most recent response only as a
  compatibility fallback. Agent inspection combines runtime events, MCP calls,
  persisted task/message history, Agent Class assignment and audit, and
  principal journals without allowing one asynchronous response to replace an
  unrelated one. Global historical runs live in Control Center and load run
  details on selection.
- Rationale: Classic agent panels routinely issue multiple read commands at
  once. A single last-response slot made correct React parity impossible: a
  worktree preflight could replace its diff, or one agent's history could
  replace another agent's class status. Target-scoped storage also lets panels
  retain their content while another surface refreshes.
- Scope: React projection storage, focused agent inspection, global agent-run
  history, Agent Class assignment/audit, and architect/engineer journal reads.
- Constraints: The server remains authoritative. Agent Class assignment changes
  desired next-launch authority only; the UI must show pending state and require
  an explicit relaunch to change the effective frozen session snapshot. MCP
  arguments and results remain subject to the server's capture/redaction policy.
- Verification: Component tests inject interleaved responses for events, MCP,
  history, class status/audit, and worktree data; backend coverage requires
  class-audit responses to echo their target agent id.

### D-061 — A task dialog is the complete Board work surface

- Date: 2026-08-11
- Status: accepted
- Decision: Opening a React Board task exposes one scrollable work surface for
  identity, group and assignment, action/role variables, dependencies,
  scheduling, verification, external-ticket sync and communication, human ask
  resolution, attachments, structured artifacts, completion evidence, prompt
  preview, dispatch, and pipeline detachment. Compact cards remain concise;
  multi-task metadata and lifecycle operations live in a persistent selection
  bar and an explicit batch-edit dialog.
- Rationale: Splitting task authority across hidden context-menu branches makes
  operational state hard to discover and caused the migration's original
  four-field editor to silently omit meaningful BoardTask fields. A complete
  detail boundary can hydrate compact records once and makes destructive,
  provider, and verification actions legible before execution.
- Scope: React Board task detail, card menus, selection bar, archived-task
  loading, external import/sync, schedules, lane management, filters, and saved
  views.
- Constraints: The daemon remains authoritative and every write uses an
  existing command. Auxiliary previews are ignored until the requesting task
  observes a new response, preventing stale global previews from appearing in
  a newly opened dialog. File uploads remain staged until task save; removal is
  explicit. Archived tasks are lazy-loaded and are not returned to the active
  lane projection.
- Verification: Component tests cover full-field updates, human/external/
  artifact surface availability, multi-select dispatch, archive hydration, and
  external import. UI check and production build remain required, with a live
  daemon smoke covering lane, saved-view, and schedule dialogs.

### D-062 — Planning and operational knowledge keep their native lifecycles

- Date: 2026-08-11
- Status: accepted
- Decision: The React Planning workspace exposes complete, distinct editors
  for Initiatives, Areas, Area notes, Scratchpad notes, Idea Briefs, Architect
  decisions, and pending hires. Catalog authoring likewise preserves separate
  Action, Role, Template, Specialization, Agent Class, and Dynamic Behavior
  contracts. Shared memory and Architect peer chat live in focused agent
  inspection because they are operational context, not Board-task fields.
- Rationale: These records have different authority, approval, archival, and
  linking semantics. Treating them as generic editable cards would erase the
  product safeguards around Idea Brief promotion, Dynamic Behavior review,
  Architect decisions, and durable memory scope.
- Scope: React Planning, Action and catalog editors, Dynamic Behavior approval
  queue, agent-linked Context, and Architect peer messaging.
- Constraints: Every mutation uses an existing daemon command. Behavior changes
  remain proposals until their declared next actor approves them; Idea Brief
  proposal remains product-safe review only; Agent Class and Role authoring do
  not bypass frozen-session authority. Advanced Role, Template, and
  Specialization fields remain JSON-editable so the UI does not truncate future
  schema additions.
- Verification: Component tests cover Initiative linking, scoped memory
  publishing/pinning, Role authoring, and Agent Class authoring. The complete UI
  check and production build are required.

### D-063 — Operator signals retain lifecycle, provenance, and authority

- Date: 2026-08-11
- Status: accepted
- Decision: The React operator surfaces preserve alert and notification
  lifecycle in Inbox, evidence and deep links in Mission Control, raw payloads
  in Activity, maintained-source provenance in Help, and daemon authority in
  supervisor, Relay, and AI controls. Advanced settings retain the complete
  server-returned schema alongside dedicated common controls.
- Rationale: Operational summaries are useful only when the operator can trace
  them to evidence and act without losing durable state. Relay pairing links
  and provider secrets have stricter handling than ordinary preferences, while
  new daemon settings must not disappear merely because the frontend has not
  designed a dedicated field yet.
- Scope: React Inbox, Mission Control, Activity, Help, global/group settings,
  Relay connection and pairing, AI providers and corpus indexing, and PTY
  supervisor controls.
- Constraints: Provider secrets remain write-only, one-time Relay material is
  shown only from the direct command response, supervisor termination honors
  server capability flags, and Help topics come only from the daemon allowlist.
- Verification: Component tests cover notice lifecycle, Mission/health/
  supervisor data, and Help topics. Live smoke covers source-backed Help,
  advanced schemas, and protected supervisor controls.

### D-064 — Board lane geometry owns both drop intent and dividers

- Date: 2026-08-11
- Status: superseded by D-066
- Decision: Pointer drag collision first resolves the horizontal lane and then
  the nearest vertical card in that lane. Lane padding and divider seams no
  longer mean “append to lane.” The single Board grid row grows to the tallest
  lane while retaining a viewport-height minimum, so every divider spans the
  complete scrollable Board.
- Rationale: Global closest-card fallback can cross a lane boundary, while a
  lane-only fallback discards vertical intent and makes cards jump to the
  bottom. Separately sized lane boxes leave dividers stranded at viewport
  height when another lane grows.
- Scope: React Board collision detection and lane grid sizing.
- Constraints: Empty lanes still use the lane itself as their drop target;
  keyboard dragging retains closest-center fallback; progressive card mounting
  remains unchanged.
- Verification: Unit coverage protects lane-seam and vertical-padding
  resolution. The production build and live computed geometry must show equal
  lane heights with a viewport-height minimum.

### D-065 — React workspace chrome persists semantic preferences

- Date: 2026-08-11
- Status: accepted
- Decision: The React workspace has one stable primary product surface with a
  persistent, resizable navigation rail; independent work moves to native
  detached windows. Appearance is applied locally before first paint, while
  keyboard bindings, status-bar visibility, terminal message/composer heights,
  detached-window bounds, and workspace widths use the daemon's durable UI
  settings.
- Rationale: Persisting user intent avoids layout jumps and lost working state
  without coupling the replacement UI to the classic frontend's DOM-specific
  dock and floating-panel model. Native windows also transfer cleanly between
  Tauri today and a possible Electron host later.
- Scope: React application shell, appearance and keybinding preferences,
  status bar, terminal conversation sizing, and native panel detachment.
- Constraints: Classic arbitrary dock/floating coordinates and classic-only
  split ratios do not drive React layout. Host-specific APIs remain isolated in
  the host adapter, and all resize affordances must remain keyboard accessible.
  The Agents terminal and direct-message regions share a visible full-width
  separator; resizing either region persists the conversation height without
  introducing nested outer margins around the terminal workspace. The message
  composer uses the same separator pattern above its text box instead of a
  browser-native corner resize handle, and persists its own height separately.
- Verification: Preference normalization and keybinding tests, UI check and
  production build, plus live smoke for sidebar resizing, status items,
  detached-panel capability gating, and restored terminal heights.

### D-066 — Board lanes are independent working viewports

- Date: 2026-08-12
- Status: accepted
- Decision: The Board owns horizontal overflow only. Every lane fills the Board
  viewport, keeps its header outside the scroll surface, and gives its task body
  an independent vertical scrollbar. Lane-local creation stays at the top of
  that body. The workspace-level create command opens the complete create-task
  dialog. A card's non-interactive shell is its drag activator; selectable text
  and interactive controls do not begin pointer drags.
- Rationale: A shared vertical document made one long historical lane control
  every other lane, hid creation controls below hundreds of tasks, and made
  dividers appear truncated. Whole-card pickup is faster than targeting a small
  handle while preserving ordinary text selection.
- Scope: React Board lane geometry, progressive mounting, task creation, and
  pointer drag activation. This supersedes D-059's shared Board scroll-root
  clause and D-064's tallest-row layout clause.
- Constraints: Progressive mounting observes the owning lane body. Lane headers
  and dividers remain visible for the complete Board viewport. Keyboard dragging
  and the server-authoritative move/reorder command path remain unchanged.
- Verification: Component coverage protects lane-local observation and creation
  placement; the production build and live computed styles verify independent
  overflow, full-height dividers, and the global dialog flow.

### D-067 — Core work surfaces preserve action hierarchy and context

- Date: 2026-08-12
- Status: accepted
- Decision: High-frequency editors keep their primary fields, current state,
  and primary actions visible while secondary contracts use stable tabs with an
  independently scrolling panel. An empty master-detail workspace collapses to
  one action-oriented state until a selectable record exists. The command
  palette is a real fuzzy-search surface with complete keyboard result control.
  Aggregate counts belong in panel headers, local collection counts remain in
  their section headers, and navigation tabs do not duplicate counts. Cards use
  shared content/metadata spacing tokens; truncated user-authored values expose
  their full accessible text and a passive title tooltip.
- Rationale: Operators should not lose task identity or primary actions while
  configuring execution details, and empty inspectors should not imply a
  selection that does not exist. Shared density, count placement, and truncation
  rules make Board, Agents, Planning, and Control read as one product rather
  than adjacent feature implementations.
- Scope: React task details, command palette, Agents empty state, panel and
  collection counts, empty-state guidance, and card metadata across primary
  product areas.
- Constraints: Tabs are navigation rather than dashboards and therefore do not
  carry aggregate badges. Lane and section counts remain local where they aid
  scanning. Native title tooltips supplement rather than replace complete DOM
  text and accessible names. Desktop task dialogs use an exact flex-owned body
  height so only the secondary panel scrolls; compact layouts return the task
  editor body to document scrolling when a fixed primary/secondary split would
  be too narrow.
- Verification: Component coverage protects task tabs and persistent primary
  fields, empty Agents onboarding, fuzzy filtering and keyboard activation,
  header count placement, and long-label titles. The production build plus live
  normal and compact browser smoke verifies layout and overflow behavior.

### D-068 — Board cards state task provenance and execution responsibility

- Date: 2026-08-12
- Status: accepted
- Decision: Every Board card identifies who created the task and who currently
  owns its execution. When a concrete worker differs from the responsible
  Architect or Engineer, the worker appears as a subordinate “via” identity.
  User-created and unassigned work use explicit `You` and `Unassigned` labels.
  Every persisted agent identity is a direct link to that agent's focused
  workspace; fallback identities remain inert text.
  The card itself is the drag affordance, so no redundant grab-handle glyph is
  shown. Drag transforms translate cards without scaling their intrinsic size.
- Rationale: Creation provenance, accountable ownership, and the active worker
  are different facts. Combining them into one anonymous agent badge obscured
  the chain of responsibility, while scaling a lifted card to a neighbor's
  height made variable-density Board ordering feel physically unstable.
- Scope: React Board task normalization, card metadata, detail summary, and
  sortable transforms.
- Constraints: Creator provenance is immutable display data. Responsible
  execution prefers assigned Engineer, then assigned Architect, then a concrete
  agent. Text-selection and interactive-control exclusions still take priority
  over card dragging.
- Verification: Model and component tests protect ownership projection, visible
  role labels, active-worker attribution, handle removal, and translation-only
  transforms. A live pointer drag between unequal-height cards verifies that the
  lifted card retains its width and height.

### D-069 — Agents use an ownership tree and a dedicated context workspace

- Date: 2026-08-22
- Status: superseded by D-070
- Decision: The Agents master surface is a compact, collapsible semantic tree
  whose visual levels follow Architect → Engineer → Worker ownership, with
  attached terminals nested under their parent. Agent-linked durable memory
  fills the right-hand selected-agent pane when `Context` is selected instead
  of `Live`; the ownership tree remains visible and does not move. Context does
  not compete with the terminal and direct-message regions for space.
- Rationale: Repeated cards obscured reporting lines and consumed most of the
  available workspace before operators could scan a team. Context is a reading
  and authoring task with its own list/detail rhythm, while terminal and direct
  messages are a live-operation task; showing all three simultaneously weakens
  each one.
- Scope: React Agents hierarchy, keyboard navigation, selected-agent context
  browsing and authoring, and terminal activation while the workspace is hidden.
- Constraints: The selected agent and persistent ownership tree are shared
  between `Live` and `Context`; the switch controls only the right detail pane.
  Switching views preserves the mounted direct-message draft and layout, but a
  hidden terminal relinquishes its controller. Stale ownership references never
  hide a record: orphaned agents are promoted to roots and visibly marked.
  Tree rows retain lifecycle and inspection actions without expanding into cards.
- Verification: Model tests protect ownership order, collapse projection, and
  orphan visibility. Component tests protect semantic levels, collapse controls,
  context listing/publishing/pinning, and the production type/lint/build checks.

### D-070 — Agent operations stay with Agents; shared context belongs to Control Center

- Date: 2026-08-22
- Status: accepted
- Decision: The Agents ownership tree remains fixed on the left while the right
  detail pane switches between `Live` and `Activity`. `Live` owns terminal and
  direct-message interaction. `Activity` restores the classic role-specific
  operational dossier: Architect Decisions, Journal, Messages, and Events;
  Engineer Journal, Events, Queued, and Completed; and Worker Events, Messages,
  and Worklog. Shared durable Context is a workspace-level Control Center tab.
- Rationale: Journal, decisions, task queues, and message history describe the
  selected agent and need the reporting hierarchy for navigation. Durable
  memory crosses individual agents and scopes to groups, projects, tasks, and
  pipelines, so locating it inside a selected-agent view misrepresented its
  authority and made broader context harder to find.
- Scope: React Agents detail switching and role-specific dossier; Control Center
  Context browsing, filtering, pinning, and authoring; hidden PTY activation.
- Constraints: The ownership tree and selected agent persist across `Live` and
  `Activity`. Switching views preserves mounted terminal and DM state while a
  hidden PTY relinquishes its controller. MCP, history, Agent Class, and
  Architect peer-chat tooling live directly in Activity; there is no parallel
  inspector modal or `Inspect activity` affordance.
  Context supports group-wide browsing plus agent, task, pipeline, project,
  type, search, and pinned filters.
- Verification: Component tests protect Live/Activity draft preservation, each
  role's tab contract, Architect journal/decision projection, Control Center
  context publication/pinning, hierarchy semantics, and production checks.

### D-071 — Activity collections are progressive and collapsed by default

- Date: 2026-08-22
- Status: accepted
- Decision: Agent Activity collections initially request and mount at most 20
  records. Reaching a collection tail requests or mounts the next page. Feed
  records expose a compact summary row and keep their body collapsed until the
  operator expands it. Architect decisions use the same disclosure pattern,
  with active decisions shown by default and an explicit archived view.
- Rationale: Journals, messages, runtime events, MCP calls, and history can grow
  without bound. Fetching and mounting entire collections made the selected
  agent surface slower and made individual records difficult to scan. A compact
  summary preserves chronology while expansion keeps full evidence available.
- Scope: React Agents Activity journals, messages, events, task history, MCP,
  persisted history, Agent Class audit, peer chat, and Architect decisions.
- Constraints: Scroll-tail loading retains a visible keyboard-accessible fallback
  control. Sources with bounded backend reads increase the server limit only at
  the tail; snapshot-only sources progressively mount locally. Decision actions
  operate on the durable Architect lifecycle, archived decisions can be restored,
  and expanded body copy uses the primary text color on dark surfaces while
  timestamps and metadata remain muted.
- Verification: Component tests protect the 20-record boundary, scroll-tail
  expansion, collapsed bodies, archived filtering, and decision commands; the
  normal React lint, typecheck, test, and production build gate remains required.

### D-072 — Activity summaries orient operators without exposing agent controls

- Date: 2026-08-22
- Status: accepted
- Decision: Collapsed journal and message records always expose their first
  content line, while Architect decision headings wrap to show the complete
  title. Peer chat is a read-only thread browser for operators, and digest
  delivery state and controls live in Events for both Architects and Engineers.
- Rationale: A title or event kind alone is insufficient to scan operational
  history, but expanding every record defeats the compact Activity design.
  Operators need to inspect agent-to-agent communication without impersonating
  an agent, and digests are delivery events rather than journal authorship.
- Scope: React Activity disclosure summaries, Architect decisions and peer
  threads, Architect/Engineer digest delivery, and application viewport
  containment.
- Constraints: First-line previews remain single-line and do not replace the
  expandable full body. Peer threads expose their complete progressively mounted
  history but no compose or send affordance. The document root owns no scrolling;
  the shell is fixed to the viewport and each intended panel owns its overflow.
- Verification: Component tests protect collapsed previews, read-only clickable
  peer threads, Architect digest commands and Events placement; browser QA checks
  decision wrapping and that the shell cannot scroll beyond the canvas.

### D-073 — Agent view switching belongs to the shared detail frame

- Date: 2026-08-22
- Status: accepted
- Decision: The `Live` / `Activity` switch is rendered once by the selected
  agent’s shared detail frame, above both view surfaces.
- Rationale: Rendering a separate switch inside each view header changed its
  position as the operator switched modes, making a repeated toggle feel
  unstable and forcing unnecessary pointer travel.
- Scope: React Agents selected-agent detail frame and its Live and Activity
  headers.
- Constraints: View-specific actions such as Settings and Refresh remain in
  their respective headers. The shared switch persists as the same DOM control
  across mode changes and disables Activity for standalone terminals.
- Verification: Component coverage asserts that the same switch element remains
  outside both view panels before and after switching modes.

### D-074 — Detachment is a workspace-level icon action

- Date: 2026-08-22
- Status: accepted
- Decision: Native Agents detachment actions use a compact open-in-window icon
  with an accessible label and tooltip. The Live view does not expose a separate
  terminal-only detach action or a terminal tab row.
- Rationale: The text detach buttons consumed disproportionate header space, and
  the terminal row commonly contained only a redundant `Agent` chip plus a
  second detachment path. Agents and attached terminals are already selectable
  through the ownership hierarchy.
- Scope: React Agents workspace header, selected-agent header, and Live terminal
  surface.
- Constraints: Existing detached-terminal state remains recoverable through its
  focused placeholder, but the React Agents UI cannot initiate new terminal-only
  detachment. Icon-only actions retain explicit accessible names and titles.
- Verification: Tauri-boundary component coverage protects both icon actions and
  the absence of the terminal detach control, terminal selector row, and `Agent`
  tab.

### D-075 — Direct-message direction is visible before reading metadata

- Date: 2026-08-24
- Status: accepted
- Decision: Live direct-message bodies and the composer use compact 11px type.
  Incoming messages use a green-tinted surface with a leading edge accent;
  outgoing messages use a stronger accent-tinted surface with a trailing edge
  accent.
- Rationale: Alignment and an 8px sender label alone made message direction too
  easy to miss, while the inherited application font made short operational
  exchanges consume unnecessary vertical space.
- Scope: React Agents Live direct-message history and composer.
- Constraints: Body text retains the primary text token and 1.45 line height for
  readability. Direction colors derive from semantic tokens so high-contrast and
  accent themes remain authoritative; sender metadata uses the same directional
  family rather than color alone.
- Verification: Component coverage protects explicit inbound/outbound semantics;
  the React production gate protects the CSS Modules and theme-token build.

### D-076 — Parity is verified per behavior, not per panel

- Date: 2026-09-21
- Status: accepted
- Decision: The [parity evidence ledger](docs/plans/react-ui-parity-matrix.md)
  replaces the coarse Phase 4 map. Logs, aggregate peer Chat, pipeline discovery,
  graph navigation, ordering, event resolution, supervisor controls, health
  visualization, and each settings family have independent acceptance gates.
- Rationale: A shared destination or raw JSON does not preserve an operator
  workflow. The previous completion claim hid required functionality.
- Scope: Classic-to-React migration, tests and retirement evidence.
- Constraints: No required row passes from command-string presence alone.
  D-072 keeps operator peer Chat read-only. D-074 keeps new terminal-only detach
  initiation retired while preserving recovery of existing detached windows.
  D-077 records the recommended retirement of canvas coordinates and arbitrary
  docked/floating compositions; fixed workspaces do not reproduce those layouts.
- Verification: Each ledger row states its acceptance scenario and records
  actual test evidence separately. Classic retirement remains gated.

### D-077 — Fixed workspaces and the ownership tree replace spatial layouts

- Date: 2026-09-21
- Status: accepted
- Decision: Adopt the migration handoff's recommended direction: keep the ownership tree as the primary Agents navigation and retire the classic free-position canvas and arbitrary docked/floating panel compositions.
- Rationale: Board, Agents, Planning and Control Center provide stable task-oriented locations. Resizable sidebar and terminal/DM regions plus whole-workspace detachment support focused inspection and separate displays.
- Constraints: This does not preserve per-agent spatial coordinates, arbitrary panel docking, or multiple independently configured copies of a panel. Existing terminal-only detached windows remain recoverable; new initiation stays retired under D-074. Operator peer compose stays retired under D-072.
- Verification: Ledger P-104–P-110 records the loss explicitly; native-window acceptance remains a separate release gate.

### D-078 — Logs are a bounded operational surface

- Date: 2026-09-21
- Status: accepted
- Decision: Control Center Logs reads `/logs` with daemon/supervisor selection, level and regex/literal filters, explicit follow, refresh, and a 2,000-line retention limit. Closing or changing target aborts old requests; rotation resets the cursor.
- Rationale: Events and process logs answer different operator questions.
- Constraints: Reveal-folder is host gated. Pause preserves the reading position; raw logs do not become Redux events.
- Verification: Log component tests, isolated endpoint/browser QA, rendered screenshot inspection.

### D-079 — Aggregate peer Chat remains read-only

- Date: 2026-09-21
- Status: accepted
- Decision: Control Center Chat lists threads across groups, ordered by recent activity. It mounts recent messages progressively and preserves the reading anchor when older retained messages are revealed. Sender/recipient, context and copy remain available.
- Constraints: No operator peer compose. Snapshot retention limits are labelled; progressive mounting must not imply unlimited server history.
- Verification: Component update/selection/disclosure tests and cross-group browser coverage.

### D-080 — Pipeline discovery opens a navigable graph

- Date: 2026-09-21
- Status: accepted
- Decision: A dedicated Pipelines section renders discovered components, directed transitions and ask conditions, handles review cycles, and supports pan, zoom, fit and keyboard node activation into the existing action editor.
- Constraints: Discovery replies are correlated to group and cancellable. Action details/previews use stable keyed response storage; unrelated replies must not clear the editor.
- Verification: Cycle-layout, keyboard and response-isolation tests plus live discovery/editor browser flow.

### D-081 — Organization controls preserve server semantics

- Date: 2026-09-21
- Status: accepted
- Decision: The hierarchy uses persisted group and child ordering. Attached terminals use `reorder_child` and `reparent_terminal`; ordinary moves retain ownership. Groups support arbitrary drag ordering and a keyboard-accessible Move group dialog. Board selected and hidden lanes persist per group without replacing independent lane scroll containers.
- Constraints: A group move does not reassign ownership or silently move owned Engineer/Worker descendants. The UI explains this; attached terminals follow the server's parent/group contract. Terminal organization resyncs the child-order projection.
- Verification: Agent model ordering, App organization/group/lane regressions.

### D-082 — Operational detail and settings require usable controls

- Date: 2026-09-21
- Status: accepted
- Decision: Supervisor exposes sortable session facts, persisted selection/detail/auto-refresh/scroll. Health renders scoped historical charts with accessible sample tables and labels daemon-wide performance separately. Settings hydrate before editing and expose typed controls, descriptions, named maps and lists instead of raw JSON.
- Constraints: Heavy readers abort on unmount. Settings report Saved only after all requested saves acknowledge; partial failure retains drafts and identifies uncertainty. Unchanged inherited relay settings must not become overrides. Schema-specific inheritance/discovery gaps remain in the ledger.
- Verification: Focused supervisor, health, typed-settings, coordinated-save and partial-failure tests.

### D-083 — Detached workspaces and Planning updates preserve local state

- Date: 2026-09-21
- Status: accepted
- Decision: Main-window Agents keeps its draft-bearing frame while a detached workspace owns the PTY; the hidden terminal is inactive. Reattach explicitly restores ownership. Browser workspaces remain available when the shared profile contains native-window records. Planning editors consume current relationship data while keeping local field drafts; compact link replies rehydrate relationship records.
- Constraints: Detached native-window lifecycle still requires real desktop QA. A data refresh must not key-remount an open editor.
- Verification: Tauri adapter browser fixture; live Planning link/edit round trip and draft-preserving component link/unlink regression.

### D-084 — Attention replies and behavior approvals require confirmation

- Date: 2026-09-21
- Status: accepted
- Decision: Board and Activity share a hydrated question/parent context and reply composer. Explicit reply target precedes Architect author and parent worker; unavailable targets retain the question and draft. Enter sends, Shift+Enter adds a line. Drafts clear only after the matching delivery acknowledgement. Behavior approvals use a separate diff review with author, rationale, base, hash and advisory lint; operator actions require a proposal awaiting the user.
- Constraints: Pending actions cannot be submitted twice. Failed or stale decisions retain the note and require a fresh diff. Question choices remain part of the full question text; the server has no separate reply-options field. HTTP acknowledgements and WebSocket state updates remain authoritative.
- Verification: Attention component regressions plus browser review/rejection and retained-answer scenario.

### D-085 — Settings preserve override intent and use discovered choices

- Date: 2026-09-21
- Status: accepted
- Decision: Launch provider/model/reasoning fields offer choices from daemon metadata while retaining custom values. Per-agent forms preserve the opening baseline under updates, distinguish explicit empty text from inheritance, submit sparse edits, and keep the form open on save failure. Use inherited remains inside the save boundary.
- Constraints: Choices do not infer group/default precedence. The server resolves origins and validates values. Per-agent relaunch follows successful saves. Untouched settings are not promoted to overrides by unrelated deltas.
- Verification: Model-specific discovery tests, per-agent failed-save/reset/empty-override regressions, and unchanged-save coverage.

### D-086 — Area workflows use durable domain values and acknowledged writes

- Date: 2026-09-21
- Status: accepted
- Decision: Area lifecycle, note types and Area relations match the backend vocabulary. All four relationship kinds are visible. Notes support create, edit, archive and optional task/decision/initiative/Area targets. Full detail and reconnect hydration update untouched fields and relationships without replacing drafts.
- Constraints: Write failures retain edits. Notes display the latest 50 active records supported by the detail contract. Area-to-Area unlink identifies both target and relation. Compact command replies and full-client deltas must both update the open editor.
- Verification: Component and projection tests, plus isolated live browser lifecycle/link/note round trips.

### D-087 — Native window ownership is separate from saved geometry

- Date: 2026-09-21
- Status: accepted
- Decision: Reattach/close clears a window label while retaining its bounds for reopening. On main-window reconnect, native window inventory clears stale ownership left by an exited app. Captured bounds carry a physical-pixel marker through persistence; logical initial sizes retain their existing meaning.
- Constraints: Browser clients never reconcile native ownership. Late close notifications cannot clear a newer owner. Legacy captures with a monitor identifier are treated as physical pixels. Native captures must not double in size on Retina displays.
- Verification: UI ownership/reopen regressions, backend bounds round trip, Rust pixel-unit and off-screen clamping tests; macOS native QA is recorded separately in the parity ledger.

### D-088 — Settings resets are staged and ordinary saves are sparse

- Date: 2026-09-21
- Status: accepted
- Decision: Global, group, Engineer and Architect resets use defaults supplied by the daemon and remain in the local draft until Save. Individual structured fields reset to the same defaults. Ordinary saves submit only edited fields; an explicit section reset submits that section's editable values. Architect fields have one owner in the form. Runtime identity, pending-question/note state and hint-snooze records are not settings controls.
- Constraints: Global reset covers runtime, shortcuts and status visibility; AI, relay credentials and appearance use their own controls. Empty group launch overrides restore the documented fallback, while concrete defaults remain concrete values. Numeric dropdowns preserve numeric values. Known GitHub options render typed controls even when the stored map is empty; untouched fallback keys remain absent. Reset and incoming data do not remount list editors or discard drafts.
- Verification: Daemon default/schema fixture checks, all 166 editable scope-field default write/reload contracts (including Architect fields also stored under Group), typed-control coverage, sparse-save/reset/failure tests and an isolated browser settings lifecycle.

### D-089 — Persisted heartbeat cadence is authoritative

- Date: 2026-09-21
- Status: accepted
- Decision: Group Engineer and per-agent digest readers preserve an explicit 300-second heartbeat when the maximum interval differs. Single-record, bulk and restart reads use the same persisted value. Missing-column legacy backfill remains the responsibility of the versioned schema migration.
- Rationale: Treating a stored default-looking value as missing silently changed a valid setting after restart.
- Verification: Single/bulk/restart regression, pre-heartbeat migration regression, and a 181-choice dropdown/boolean write/reload audit.

### D-090 — Attention retries respect recorded delivery and reviewed proposals

- Date: 2026-09-21
- Status: accepted
- Decision: Ask resolution is serialized per task in the daemon; closed/resolved asks cannot deliver another answer. Worker/Engineer retries reuse a recorded delivered reply without sending again. A retry whose text or target differs from its recorded reply is rejected explicitly, preserving the operator's draft; corrections use a new message. Operator approve and reject both validate the proposed hash and base when supplied, while approval also requires the proposal's base to remain active.
- Constraints: This is concurrent-request and recorded-delivery protection, not an exactly-once guarantee across a process crash between PTY write and durable acknowledgement. Legacy callers may omit review preconditions. Rejecting a stale proposal withdraws it without changing the active overlay. Successful decisions update the displayed proposal status from the acknowledgement.
- Verification: Backend concurrency, closed Architect, immutable retry, delivered-mirror and decision-precondition regressions; real-daemon browser delivery, reconnect, approve/stale/reject flows.

### D-091 — Attention context refreshes without replacing the composer

- Date: 2026-09-21
- Status: accepted
- Decision: A changed question or parent timestamp rehydrates full attention context, since compact task deltas omit descriptions. Accepted snapshots, reconnect and targeted refresh keep the mounted answer composer, its selection and caret. A behavior review preserves its note on reconnect and requires a fresh diff after a failed decision.
- Constraints: Unrelated agent activity does not trigger question hydration. Delivery and decision errors retain drafts; an authoritative closed task leaves the open-attention list.
- Verification: Compact question/parent component regression and real WebSocket browser checks for answer/review-note draft, focus and caret retention.

### D-092 — Group navigation scrolls within its remaining sidebar space

- Date: 2026-09-21
- Status: accepted
- Decision: The group list fills the space remaining below its header and above the connection footer. It scrolls independently; its last row stays fully reachable by pointer and keyboard regardless of group count.
- Rationale: A list sized to 100% of the parent plus its header placed the final row behind the footer and blocked clicks in larger workspaces.
- Verification: A 25-group browser regression asserts the last row is clickable and remains above the footer; repeated live suites exercise populated profiles.

### D-093 — Terminal scroll intent survives output, fit and reconnect

- Date: 2026-09-22
- Status: accepted
- Decision: Streaming output preserves the operator's scrollback position; Tail explicitly resumes following output. Fit preserves tail pinning or the distance from the tail, and reconnect restores that distance in the replacement snapshot. A reconnected owner always resends its dimensions.
- Constraints: Normal-buffer wheel input uses xterm's public scroll API to avoid a bundled-runtime DOM scroll suppression race. Alternate screens, application mouse reporting and modified wheel events retain xterm handling. Output remains outside React state; hidden surfaces do not send focus or geometry.
- Verification: Controller regressions and a repeated real-xterm/PTY browser scenario. Native macOS detach/resize/close confirms exclusive geometry ownership, one input marker per window and retained main-window DM draft; cross-platform and crash/sleep recovery remain release gates.

### D-094 — Thinking follows the persisted brief contract and keeps failed drafts

- Date: 2026-09-22
- Status: accepted
- Decision: Idea Briefs edit Title, Problem or opportunity, Why it matters, Proposed shape, Smallest useful version, Risks and tradeoffs, and Open questions. Full detail loads before any write; reconnect refreshes untouched fields without replacing local edits. Scratchpad links use a note picker, context text and readable source preview. Hidden source metadata is preserved through sparse writes.
- Constraints: Every Planning creation waits for acknowledgement. Thinking saves and lifecycle writes serialize and retain failed drafts. Park/propose first save local edits; a failed save prevents the lifecycle command. Proposal is product review only and creates no task or assignment. Archived Thinking is discoverable and read only; scratchpad deletion has an inline confirmation. The backend has no unarchive command for these records.
- Verification: Component tests cover failed create/save, full hydration, draft/caret retention, sparse metadata preservation and proposal ordering. Live isolated browser QA exercises all seven brief fields, linking, refinement, proposal, park/return-to-draft, reload, archive and scratchpad edit/archive/delete.

### D-095 — Planning editors use domain statuses and acknowledged sparse writes

- Date: 2026-09-22
- Status: accepted
- Decision: The Initiative roadmap has six durable states: triage, now, next, later, parked and shipped. Decision states are proposed, accepted, revised and rejected; supersession is a separate relationship. Both editors hydrate complete records, retain local edits across incoming records/reconnects, and close only after the required writes succeed. Linked records display persisted IDs with names and support acknowledged add/remove actions.
- Constraints: Initiative archive preserves edited scope before archiving. Decision archive saves its edits atomically in the existing update command; restore changes only archive state. Archived decisions remain in the projection and appear through an explicit filter. Decision creation requires an Architect, title and rationale. Decisions, hire requests and journals are filtered to the selected group; Engineer link choices respect the Architect's visible peers.
- Verification: Component tests cover sparse saves, failed link/update retention, reconnect caret, scoped collections and archived projections. Isolated live QA writes every status, all Initiative scope fields, decision rationale/supersedes, task/Engineer/Decision links and archive/restore. Initiative-to-Board task creation remains a separate open parity row.

### D-096 — Initiative task creation reviews the draft and retains link recovery

- Date: 2026-09-22
- Status: accepted
- Decision: Create Board task opens the shared Board creation form with title, source Initiative, summary, why, scope and done definition taken from the current unsaved Initiative draft. The operator reviews task fields before creation. Creating or cancelling the task does not save or discard the Initiative draft.
- Constraints: Creation waits for acknowledgement before linking. Once a task ID is acknowledged, failed linking retains that ID and offers Retry link; closing the child dialog exposes Resume task link in the Initiative editor. Retrying does not create another task. Group default lane remains selectable. Pending writes prevent closing the child dialog. This does not certify persistence of local drafts across page reloads or ambiguous lost creation responses.
- Verification: Focused component and isolated browser tests exercise cancellation, rejected creation, acknowledged creation with failed linking, close/resume, same-ID retry and unchanged Initiative scope until explicit Save. Broader task-form parity is tracked separately in P-150–P-152.

### D-097 — Review complete creation evidence before the task exists

- Date: 2026-09-22
- Status: accepted
- Decision: Board and Initiative task creation share an acknowledged dialog with cross-group dependency selection, canonical verification modes/states, and draft attachments/artifacts. Verification uses the same fields in create and edit, including deploy/restart attempted. Disclosure sections retain edits without rebuilding the form.
- Constraints: Uploads are scoped to a unique draft ID, remain available after failed creation, and move to the canonical task ID only on successful creation. Closing an uncreated draft waits for upload cleanup; failed cleanup retains the dialog. Removed files are deleted before they can be finalized. External file references retain their original paths. Structured artifact edits must be saved or cancelled before creating the task. Creation and uploads serialize; close and duplicate submission are blocked while a write is pending. Local scheduling values are converted to ISO timestamps.
- Verification: Component tests cover rejected creation, cross-group dependencies, every verification field, artifact drafts, failed removal/cleanup and pending-close guards. Isolated browser QA checks canonical attachment paths, external references, removed-file absence, cancellation cleanup, retained failure drafts, and persisted fields after reopening.

### D-098 — Task detail keeps its save controls inside the viewport

- Date: 2026-09-22
- Status: accepted
- Decision: The task detail grid reserves its footer while allowing both the overview and secondary section to scroll. Narrow layouts keep the same bounded regions instead of expanding the form beyond its containing dialog.
- Verification: The live task workflow reproduced an offscreen Save button at 1280×720 before repair. It now asserts visibility at 1280×720 and 760×600, changes verification, saves and checks persistence. Other viewport combinations still require their own evidence.

### D-099 — Archived Thinking visibility belongs to the selected filter

- Date: 2026-09-22
- Status: accepted
- Decision: Note and brief detail/mutation responses retain archived records in the projection. The Planning archive filter controls visibility; deletion still removes notes. A late HTTP archive acknowledgement must not erase records received through a list or WebSocket update.
- Verification: Deterministic regressions apply an archived list followed by archive acknowledgement and detail read for both notes and briefs. Live Thinking archive/read/delete coverage verifies the resulting operator flow.

### D-100 — Named action variables preserve the catalog and operator values

- Date: 2026-09-22
- Status: accepted
- Decision: Task creation and editing expose the selected action's named variables, excluding reserved TASK/torque. TASK comes from the title. Catalog defaults fill absent values; explicit empty strings, zero, false, unknown keys and structured values remain intact. Advanced JSON is a disclosure for structured/custom values, not the primary editor.
- Constraints: Action changes retain separate drafts for each action for the lifetime of the editor. Catalog refreshes do not overwrite operator edits. Creation resolves the group's default action for fields and preview. Defaults retain the types supplied by the daemon; the current Jinja catalog serializes literal defaults as strings, so the UI does not guess new types.
- Verification: Component coverage exercises defaults, switching, advanced values, refresh focus/caret and submission. Live acceptance checks named creation and editing against a project action.

### D-101 — Preview unsaved task creation without creating work

- Date: 2026-09-22
- Status: accepted
- Decision: Board and Initiative creation can preview the current title, description, effective action, worker role, variables and saved draft evidence. A correlated HTTP read owns its response; failures retain the form and warnings remain visible. Previewing never creates or dispatches a task.
- Constraints: Changed inputs invalidate displayed output, including changes made while a request is pending. Unmount aborts the read. Preview waits for an artifact editor to be saved or cancelled. External ticket fields are available before creation and use the existing backend normalization and group sync policy.
- Verification: Focused tests cover failure, warning, stale responses and unmount; live acceptance verifies actual rendering, evidence and external-link persistence with provider sync disabled.

### D-102 — Task drafts survive refreshes and discard on explicit close

- Date: 2026-09-22
- Status: accepted
- Decision: An open task editor owns its draft until save or explicit close. A later task-detail response may update the projection without remounting the editor, replacing its draft, resetting its selected tab or stealing focus/caret. Creation retains the existing acknowledged cleanup boundary on close.
- Rationale: Classic `taskPersistDraft` retains active modal state, but `modals/core.js::closeModals` explicitly clears it on dismissal. The earlier P-155 reopen-recall requirement misread that boundary. Cancel/close discards; ordinary refresh and failed requests preserve.
- Constraints: Reload/crash persistence is not implied. New task IDs still mount distinct editors; compact records wait for initial detail hydration.
- Verification: Component tests apply late detail/catalog responses to edited fields and assert DOM identity, draft, focus and selection. Live acceptance cancels creation and confirms a fresh blank form on reopen.

### D-103 — Task edits wait for acknowledgement and send only edited fields

- Date: 2026-09-22
- Status: accepted
- Decision: Saving an existing task uses a correlated HTTP acknowledgement. Failures, including the active-worker edit gate, retain the draft. Save, upload, close and competing mutation controls cannot overlap a pending save/upload. Only changed fields are submitted; an untouched assignee cannot accidentally dispatch work, and unrelated refreshed metadata is not overwritten.
- Constraints: External link fields travel as a complete provider/ID/URL tuple because backend normalization consumes them together. Edited verification/sync keys merge with current metadata. Attachment files are removed only after the task edit succeeds; failed cleanup stays retryable without repeating an already acknowledged edit. The editor does not replace newer projections with a mutation's full snapshot. Scheduled values display in local time; unchanged timestamps, including their seconds, are omitted from updates.
- Verification: Focused UI tests cover rejection, close/deduplication, sparse payloads, metadata preservation and partial cleanup. Isolated browser acceptance uses a real active-dispatch gate with a synthetic worker, delays the success acknowledgement, checks unchanged schedule seconds in a non-UTC browser, and verifies actual file retention/deletion across failures.

### D-104 — Existing-task prompt previews render a temporary draft

- Date: 2026-09-22
- Status: accepted
- Decision: Existing-task preview uses the same correlated read and stale-output invalidation as creation. The backend overlays explicitly supplied draft fields onto temporary task/agent copies. Title, cleared description, action variables, role, group, evidence and verification reach the rendered prompt, context and postscript consistently; task identity and ancestry remain available.
- Constraints: Omitted fields retain persisted values for ID-only callers. Explicit empty fields clear the preview instead of falling back to saved data. No preview modifies the task, assigned agent, database or dispatch state. The UI ignores unrelated global preview responses and aborts reads on unmount.
- Verification: Backend tests cover omitted-versus-empty fields, assigned/unassigned drafts, role/context consistency, preserved ancestry and unchanged source objects. Component tests cover unrelated response frames and invalidation; live acceptance renders the changed role preamble and explicitly cleared description while backend reads prove the original task remains unchanged.

### D-105 — Existing-task evidence stays inside the task save boundary

- Date: 2026-09-22
- Status: accepted
- Decision: Creation and existing-task editing share the structured evidence editor. Type, title, summary, path, content, line range and prompt mode remain local until Save task succeeds. An unfinished artifact edit remains mounted across section changes and blocks task Save with an explicit explanation. Evidence and Activity reserve more vertical space for reading/editing, with primary task fields independently scrollable and Save kept visible. Opening the artifact editor focuses its title.
- Constraints: Cancel preserves original evidence and removes only new uploads or cleanup already authorized by an acknowledged save. Failed cleanup retains the editor for retry. Removal never deletes externally owned references. Local evidence edits merge onto the latest known collection so unrelated additions and unchanged metadata survive; this is not server-side conflict detection.
- Verification: Component and isolated browser regressions cover staging, failed writes, retry, concurrent additions, retained composer state and cleanup ordering.

### D-106 — Evidence preview uses actual content and storage

- Date: 2026-09-22
- Status: accepted
- Decision: Evidence opens a nested preview without leaving the task draft. Images display in place; inline draft text takes precedence over lazy file reads. Text remains escaped, with explicit loading/error states and abort on close. Previews expose path, line range and downloads only when a file URL exists.
- Constraints: A filename inferred from an external path is not an uploaded file. External references keep editable paths and no fabricated attachment URL. New uploads use the same image/log/diff/report/document/file classification in create and edit. Large-file performance is not certified by these checks.
- Verification: Component tests cover inline precedence, read failure/abort, image sources and external references. Browser checks exercise nested Escape, actual image decoding, log content and draft-upload cleanup.

### D-107 — Task activity preserves the current reading window

- Date: 2026-09-22
- Status: accepted
- Decision: Board cards expose Task activity and the editor has a dedicated Activity section. Entries show newest sequence first, with action, actor and timestamps normalized from Unix seconds, milliseconds or ISO values. Forty entries mount initially, with explicit older-message paging.
- Constraints: Compact count summaries are not activity rows. An active Activity section refreshes full messages through a correlated task-detail read while retaining the previous rows; hidden sections issue no refresh reads. New arrivals have an explicit reveal control so they do not replace the current reading window. Section switches retain paging and task drafts. This surface is task message history, not agent execution history.
- Verification: Component tests cover ordering, date units, paging, compact refresh failures/retry and inactive sections. Browser acceptance covers compact card hydration, live append and draft continuity.

### D-108 — Numeric settings keep editable drafts and validate before writes

- Date: 2026-09-22
- Status: accepted
- Decision: Primary and structured numeric settings share one input contract. Clearing a field retains a blank draft instead of converting it to zero or restoring the previous value. Zero remains an explicit value where supported. Integer settings reject fractional and unsafe integer values; bounds follow the audited field definition. Save validates the complete form, including embedding-rebuild confirmation, before issuing any mutation.
- Constraints: Invalid fields inside collapsed sections are revealed and focused. Reset updates the mounted input. Arbitrary map keys remain literal strings and do not inherit settings metadata because their names happen to match a setting. Backend normalizers remain authoritative; this UI contract does not certify every runtime side effect.
- Verification: Component tests cover blank/replacement/reset, fractions, bounds, safe integer range, literal map keys and hidden invalid fields. Isolated browser acceptance checks actual numeric persistence and rejection before transport.

### D-109 — Retry only unfinished settings scopes

- Date: 2026-09-22
- Status: accepted
- Decision: Coordinated Settings save records acknowledgement independently for global, group, Engineer, Architect and AI scopes. After a partial failure, the draft stays mounted and retry sends only unfinished scopes or further operator edits. Completed scope resets and relay/secret intents are cleared only after their acknowledgement.
- Constraints: An explicit error response cannot count as success. Embedding rebuild confirmation leaves AI pending. This is retry behavior within the mounted editor; crash recovery and ambiguous lost acknowledgements remain separate gates.
- Verification: Component and real-daemon browser checks reject a group write after global success, then retry without resending global fields. A concurrent external update to the completed global scope survives retry.

### D-110 — Refresh active Settings without replacing operator drafts

- Date: 2026-09-22
- Status: accepted
- Decision: Active Settings rereads global, group and AI sources after reconnect and after a save settles. A complete, matching response set refreshes untouched values and defaults in the mounted form. Three-way reconciliation retains edited fields and nested map removals while adopting remote additions. Pending section resets, secret drafts, relay edits, focus, caret and expanded sections survive refresh.
- Constraints: Reads pause during writes; aborted or late reads cannot replace acknowledged settings. A failed refresh retains the previous form and offers retry. Hidden Settings issues no refresh reads. Arrays are reconciled as whole values. This does not provide server-side conflict detection, persistence after dismissal or crash recovery.
- Verification: Component tests cover reconciliation, refreshed defaults, pending resets, failed reads, hidden tabs and a late read after Save. Isolated browser acceptance reconnects the real WebSocket, checks form identity/caret and persists merged values through the daemon.

### D-111 — Context refresh and writes preserve the active editor

- Date: 2026-09-22
- Status: accepted
- Decision: Active Context uses correlated list requests on entry, Apply and reconnect. Reconnect reuses applied filters; typing does not change the current query. Incoming records refresh untouched editor fields without replacing local changes, focus or caret. Publishing, editing and pinning wait for a matching entry acknowledgement. Existing-entry writes include only changed fields; failures retain the draft and selection for retry. Entry types match the backend: finding, decision, warning, handoff and note. Title/content inputs respect the 200/4000-character limits. Detail shows expiry, and pinning is described as ranking rather than extending retention.
- Constraints: Hidden Context aborts reads. Reads pause while a mutation is pending. A failed list refresh after a successful publish retries only the list, preserving the acknowledged entry. New-entry agent links are captured when editing begins. Group changes reset the workspace to prevent cross-group drafts. This does not provide crash/lost-acknowledgement recovery or server-side conflict detection; task/pipeline attachment controls are addressed separately by D-112.
- Verification: Component coverage checks targets, applied filters, draft reconciliation, failed and mismatched acknowledgements, pin retry and hidden cancellation. Real-daemon browser acceptance checks WebSocket reconnect, sparse persistence, failed reads/writes and a successful publish followed by failed refresh.

### D-112 — Context links are explicit and independent of scope

- Date: 2026-09-22
- Status: accepted
- Decision: New shared Context entries offer optional task, pipeline and agent links with a searchable target picker. Pipeline choices resolve explicit pipeline-root IDs or walk the compact parent chain, deduplicating child tasks. The picker starts with the current group as its search and supports targets across groups with readable names, groups and IDs; duplicate links are omitted. Draft links can be removed and survive reconnect, filter changes and failed publication. Task/agent focus prefills the corresponding optional link while keeping it removable.
- Constraints: Links and scope remain independent. A target absent from the current client inventory stays visible as unavailable and blocks new publication until removed. The backend acknowledgement still determines write success; this does not guarantee target existence across a server-side race. Existing-entry edits leave stored links intact. Saved links show names and IDs and open task/pipeline detail or the selected agent; unavailable records show their IDs with navigation disabled. Agent-tree selection exposes its selected state to assistive technology. Crash recovery and archived-target discovery are separate gates.
- Verification: Component tests cover pipeline roots, target filtering, deduplication, draft retention, missing-target recovery, sparse edits and navigation callbacks. Isolated browser acceptance persists all three link kinds, rejects a publication, reconnects, edits without replacing links, navigates each target and cancels without publishing.

### D-113 — Selected Board tasks hydrate regardless of entry point

- Date: 2026-09-22
- Status: accepted
- Decision: The active Board requests full task detail and its group's action/role catalogs when a task is selected for detail, including selection from Context or other surfaces. Reopen and reconnect refresh that selection; unrelated renders do not repeat the requests. Card clicks set the selection instead of owning a separate hydration path.
- Constraints: During compact reconnect hydration, the Board retains the selected task's last full record so the mounted editor and its draft/caret survive until fresh detail arrives. Task detail still uses the existing WebSocket protocol and projection. Correlated read-failure recovery and archived-target discovery are not certified by this change.
- Verification: App tests open a compact task through shared selection, accept its full detail, reconnect with local edits and reopen. Live Context-link acceptance navigates both a child task and its pipeline root into hydrated editors.

### D-115 — Context pane width is persisted without replacing its editor

- Date: 2026-09-22
- Status: accepted
- Decision: Wide Context workspaces expose a labelled vertical separator. Pointer movement previews width; release saves once through the existing `ui_set_context_panel_split` contract. Left/Right resize by two percentage points; Home/End reach the server's 28–62% bounds. Pointer cancellation restores the starting width. The default remains 38%.
- Constraints: Saves are correlated and serialized; rapid commits retain only the latest queued width. An acknowledgement validates the returned ratio without applying its full snapshot. Failure retains the chosen width and offers retry. Unrelated snapshots cannot overwrite an unsaved width. Successful external width changes are reflected without replacing selection, editor DOM, draft, focus or caret. Unmount cancels pending transport; crash/lost-acknowledgement recovery is not implied.
- Compact equivalent: At 900px of available pane width or less, the separator hides and list/detail stack with independent scrolling. Both remain mounted; the saved wide ratio and editor survive compact/wide transitions. Keeping both panes available replaces Classic's compact Back-to-list navigation. Context uses its available workspace height, keeping toolbar controls outside the pane scroll regions.
- Verification: Component regressions cover keyboard/pointer bounds, cancellation, serial saves, mismatched acknowledgements, retry, external updates, compact transitions and unmount. Browser acceptance checks actual geometry, draft/caret/scroll retention, persistence refusal/retry, reconnect and reload restoration.

### D-116 — Agent Class editing preserves authored authority and waits for acknowledgement

- Date: 2026-09-22
- Status: accepted
- Decision: The catalog shows resolved permissions, scopes, warnings, launch availability and next-launch applicability. Editing uses the authored definition supplied separately from the resolved authority projection; deny rules must never be reconstructed from granted capabilities. Saves preserve prompt sections, metadata, runtime and authored warnings that the form does not expose. Lifecycle choices match the backend's stable/draft vocabulary: stable omits draft metadata, and draft is scratch-only. Changing either control updates both values.
- Interaction: Active catalog reads are correlated, cancellable and refreshed on selection, reconnect and Refresh. Three-way reconciliation adopts untouched fields while retaining local edits, input focus and caret. Validation belongs to the exact current definition and is discarded after edits. Duplicate stages a local, editable project draft. Create/update/archive/delete change selection and dismiss confirmation only after a matching acknowledgement; failures retain the draft or confirmation for retry. A failed post-save list refresh retries only the read.
- Constraints: Existing deny definitions without the authored payload remain read-only until refreshed. Built-ins and archived definitions remain read-only. Reads pause during mutation and hidden Catalog cancels them. This does not certify provider launch effects, server conflict detection or recovery after a lost acknowledgement or process crash.
- Verification: Model, component and backend regressions cover authored deny-rule preservation, unexposed fields, current-draft validation, reconnect and acknowledged lifecycle. The isolated browser scenario verifies the same paths through real YAML persistence and WebSocket reconnect.

### D-117 — Catalog editing uses full scoped definitions and acknowledged lifecycle

- Date: 2026-09-22
- Status: accepted
- Decision: Roles, compatibility Templates and Engineer Specializations load full definitions through scoped detail requests before editing. Project/user entries with the same name have separate selection identities. Listing paths, shadowing flags and discovery metadata are informational and never become authored fields. Templates remain aliases for worker roles, not a separate legacy configuration directory.
- Authoring: Typed fields cover identity, provider/launch settings, behavior prompts and ordered priorities, appearance, worktrees, environment values and child terminals. Blank numeric values inherit; explicit zero remains a value. Optional flags offer inherit/enabled/disabled. Duplicate stages a new editable draft with no write. New definitions and invalid/mismatched acknowledgements retain their draft and selection.
- Refresh: Active list/detail reads are independently cancellable and retryable on selection, reconnect and Refresh. Detail reconciliation adopts untouched fields while retaining local edits, focus/caret, open sections and scroll. Priority, environment and terminal edits are reconciled as whole fields. Hidden Catalog aborts reads; group or project-directory changes create a fresh editor.
- Persistence: Save and custom confirmed Delete wait for a matching group/name acknowledgement. Reads pause while writing; a failed post-save read retries only the read. Rename/scope changes retain the original name and scope until success. Backend replacement persistence precedes source removal, and removal targets only the original scope, preserving shadowed definitions. Older callers without an original scope resolve the original by catalog precedence.
- Limits: Mounted drafts survive failures; explicit selection changes or dismissal do not promise draft recall. This does not certify atomic multi-file recovery after a crash, ambiguous lost acknowledgements, provider launch effects or native lifecycle.
- Verification: Model/component tests, temporary-file backend regressions and isolated live browser tests exercise full-definition preservation, all three catalog kinds, reconnect, duplicate staging, delayed/refused saves and deletion retry.

### D-118 — Actions use scoped authoring, typed transitions and draft previews

- Date: 2026-09-22
- Status: accepted
- Decision: The Actions library keeps project/user identity separate, loads full definitions through correlated reads and refreshes active detail on reconnect. Untouched fields reconcile while edited fields, focus/caret, disclosures and scrolling remain mounted. Failed reads retain accepted data with retry; hidden editors cancel reads.
- Authoring: Inline-agent fields, role choices, execution flags, labels, transition action/target/status/condition, human asks, LOC gates and companion terminals use typed controls. Explicit worktree false differs from inherited absence; optional numeric fields retain blank drafts and explicit zero. Unexposed durable fields, including deliverables and inline environment values, survive ordinary saves. Duplicate stages an editable local draft.
- Preview: Variables are discovered from the current draft prompt with the daemon's parser. Preview renders the full unsaved definition with local example values and safe Torque context, without persistence. Only prompt text is templated. Changed inputs invalidate visible output; late or mismatched responses cannot become a current preview.
- Persistence: Save and custom confirmed Delete wait for matching group/name/scope acknowledgements. Retry after a failed refresh does not repeat an acknowledged mutation. Explicit scopes never fall back to a different scope. Project creation never targets the user directory. Replacement writes complete before source removal; rename/scope changes remove only the resolved original file, preserving shadowed counterparts and existing filename extensions.
- Limits: Single-file replacement is atomic; cross-file rename/move is not crash-atomic and lost acknowledgements are not deduplicated. Explicit navigation discards mounted drafts. Prompt syntax highlighting remains an independent parity gate. Native lifecycle and provider dispatch effects require separate acceptance.
- Verification: Temporary-file backend regressions, model/component tests and isolated real-daemon browser acceptance cover scoped reads, failure retention, duplicate staging, persisted typed fields, unsaved preview and reconnect continuity.

### D-119 — Help is a correlated, source-backed reading workspace

- Date: 2026-09-22
- Status: accepted
- Decision: Help owns independent cancellable reads for topics, the selected document, applied search and the submitted question. Reconnect and Refresh reread those accepted targets without submitting unfinished input. Explicit search mode retains zero-result searches; All topics and Escape clear it. New questions cannot show answers from a different question, and late or mismatched responses cannot replace current content. Failed refreshes keep matching accepted content with targeted retry. Hidden Help cancels its reads.
- Navigation: All eight Classic audience choices are available for the topic list. Section disclosures show source references and line ranges; answer sources and indexed Markdown links open the referenced document or section. Whole-topic navigation is explicit. Selection survives reordered topic refreshes. Deliberate navigation focuses the article; routine refresh preserves input DOM, focus/caret, pane scroll and disclosures.
- Reading: React nodes render headings, nested lists, quotes, fenced/inline code, tables, emphasis and safe links without executing source HTML. Image references remain descriptive text, as in the escape-first Classic renderer. Code and wide tables scroll locally. Examples, source/update/index hashes, index/cache metadata and bounded-excerpt disclosure remain inspectable.
- Layout: The permanent topic/document workspace replaces Classic's additional topic-browser modal. Wide panes scroll independently; compact panes stack with bounded independent scrolling and both remain available. Search and question drafts are independent; the answer is labelled with its submitted question.
- Limits: This is maintained-document lookup, not workspace data or AI retrieval. It does not claim full CommonMark support, native external-link lifecycle acceptance, persistence after dismissal or process-crash recovery.
- Verification: Focused component and real-daemon browser tests cover request cancellation/correlation, explicit empty modes, source/section navigation, freshness/examples, reconnect continuity and wide/compact geometry. The parity ledger records executed runs.

### D-120 — Prompt highlighting preserves native editing

- Date: 2026-09-22
- Status: accepted
- Decision: Actions prompts retain one labelled native textarea. An inert, accessibility-hidden backdrop distinguishes Jinja expressions/statements/comments, quoted strings, filters and parentheses. Tokens preserve every authored character, including incomplete expressions, Unicode and trailing newlines; highlighting does not render templates or rewrite the draft/save payload.
- Interaction: Textarea and backdrop share font, wrapping, padding and scrollbar geometry. Native vertical resizing remains available and scrolling is synchronized. Parent refreshes retain the same input node, focus, caret and viewport. Forced-colors and print modes use plain visible textarea text. Source markup is rendered as text, never HTML.
- Persistence: Existing server newline normalization remains authoritative on reload. The minimal daemon/CLI YAML readers recognize serializer-produced empty lists/maps, so clearing transitions/terminals or using an empty inline agent does not make the saved definition unreadable. Quoted collection text remains text. Serializer-generated quoted scalars stay on one physical line, and both readers decode YAML escapes, preserving multiline prompts with emoji/trailing spaces and multiline non-prompt strings.
- Verification: Token/component tests cover exact text, inert markup, composition event forwarding and input continuity. Isolated browser acceptance covers native insertion/undo, wide/compact wrapping, pointer resize, scroll geometry, reconnect, forced colors, failed saves, unchanged save payloads and preview. Temporary-file backend tests cover empty-collection save/read/preview and offline parser agreement. Full native IME/device and provider-effect acceptance remain separate gates.

### D-121 — Engineer identity edits use validated, acknowledged commands

- Date: 2026-09-22
- Status: accepted
- Decision: Agent Settings rejects trimmed-empty names before any write. Engineer names use `rename_engineer`, preserving its duplicate-name validation, slug, history and session behavior. Presentation edits remain sparse `update_agent` writes without an Engineer name field. A rename must acknowledge the intended ID, name and kind before later settings are submitted.
- Recovery: Failed renames retain the entire draft. Successfully acknowledged name/icon/color edits establish the mounted editor's new baseline, so retrying later failed settings does not replay them or overwrite another operator's subsequent identity changes. Explicitly editing the name back to its original value remains a new rename. Partial success is reported. Request failures focus and reveal the error in the long form; blank-name validation focuses the Name field. Close, Escape, outside dismissal and duplicate submission cannot discard a pending save.
- Limits: This is not an atomic transaction across identity, launch, digest and specialization commands. Broader per-agent settings refresh, numeric validation and non-identity partial-save recovery remain separate acceptance gates. Server conflict detection and process-crash/lost-acknowledgement recovery are not claimed.
- Verification: Component tests cover blank/duplicate refusal, matching acknowledgement, pending dismissal, identity retry and explicit reversal; isolated browser acceptance exercises real duplicate validation, reconnect continuity and retained settings after a partial save. Executed evidence is recorded in the parity ledger.

### D-122 — Done offers an acknowledged stale-task archive batch

- Date: 2026-09-22
- Status: accepted
- Decision: The active Done lane offers one archive action for completed tasks inactive for at least seven days. Eligibility uses the last-update timestamp, falling back to creation only when the update value is absent, and orders oldest first. Missing/invalid timestamps, other groups, non-Done tasks and legacy `torque:archived` records are excluded. The suggestion includes filtered tasks in the current group, matching Classic; the visible scope note states this explicitly.
- Interaction: The suggestion belongs to Done and is absent in Archive or when Done is hidden. A minute timer updates the cutoff while mounted and is removed on unmount. Keyboard and pointer invoke one `board_archive_tasks` request. Pending requests disable repeated submission; refusals remain beside the action and retry recomputes candidates from current state. Success requires the existing success-toast acknowledgement and uses WebSocket state as the task projection. Other lane drafts, selection and reading positions are retained.
- Limits: Eligibility reflects the latest client projection. The existing backend batch is atomic but has no age or Done-only precondition; a concurrent server mutation after selection is not a new guarantee. Crash/lost-acknowledgement recovery remains separate acceptance. No scheduler or automatic archival is added.
- Verification: Model/component tests cover cutoff/order/exclusions, pending/error/retry, expected acknowledgement and idle timer cleanup. Isolated browser QA advances only its clock and exercises real task creation, batch persistence, filtered scope, keyboard activation, failure retention, unrelated Board state and reload/reconnect. Executed evidence appears in the parity ledger.

### D-123 — Per-agent settings reconcile current values and acknowledge each save scope

- Date: 2026-09-22
- Status: accepted
- Decision: Architect/Engineer Settings pins its agent target and reads current server-resolved values on open, reconnect, relevant defaults changes and explicit Refresh. Reads are cancellable and require a matching agent ID and response type. Untouched fields reconcile; explicit drafts remain; staged inheritance follows the latest inherited value. Runtime digest counters do not trigger reads. Hidden dialogs do not read settings.
- Interaction: The dialog indicates unsaved changes, disables unchanged Save and confirms dirty dismissal through Cancel, Close, Escape and outside clicks. Keep editing retains the same form; explicit discard closes it. Initial read failure retains snapshot data and offers retry; writes wait for successful initial hydration. Pending saves prevent dismissal and duplicate submission. Routine refresh does not remount fields or reset focus/caret/scroll.
- Recovery: Saves are sparse, ordered identity → launch → digest → specializations → optional relaunch. Each acknowledged scope advances its baseline. Later failure retains only unfinished intents; external changes to completed fields can reconcile without being replayed on retry. Numeric blank means inheritance, explicit zero remains zero, and non-finite/fractional/negative values are rejected before transport (worker concurrency requires at least one).
- Limits: This extends D-121's identity recovery to the remaining scopes. It is not an atomic multi-command transaction, server conflict detection or crash/lost-acknowledgement recovery. Provider-specific runtime effects and exhaustive settings-field acceptance remain separate gates.
- Verification: Model/component coverage includes cancellation, response validation, reconciliation, dirty dismissal, failed reads, explicit values and scope retries. Isolated browser acceptance and its results are recorded in the parity ledger.

### D-124 — Creation resolves launch defaults and waits for target acknowledgement

- Date: 2026-09-22
- Status: accepted
- Decision: Worker creation reads `render_template` for the current group and selected role/template, including the group default selection. The server returns the group/name with resolved configuration and refuses a missing named role. The form populates provider/command/model/effort, environment, directory/shell and worktree fields from that response; later selection/reconnect/explicit refresh updates only untouched fields. Project definitions take precedence over shadowed global names, matching launch resolution.
- Interaction: Resolving or failed launch reads block Worker creation, retain the form and offer retry. Creation pins its group, keeps fields mounted and disabled while pending, and rejects Close/Cancel/Escape/outside dismissal and repeat submissions during that request. Failures retain values and focus the error. Architect, Engineer and Worker acknowledgements must match the requested name/kind and return an ID; Agent Class launches use their nested agent response; terminals have an explicit `terminal_created` response including ID/parent. The workspace selects the acknowledged target.
- Recovery: Identical mounted creation payloads reuse an idempotency key. After the existing API caches a successful response, retry can retrieve that response without creating another target. Editing the payload starts a distinct attempt. This does not make server operations atomic or recover a daemon crash or a failure after partial side effects but before success caching.
- Hiring: Choosing a hiring Architect changes the action to Request hire, shows ordered specializations and explains Planning approval. A matching pending-hire acknowledgement closes the request; the UI does not invent an Engineer ID before approval.
- Verification: Component tests cover all four kinds, nested Agent Class acknowledgement, hire requests, pending guards, refused/mismatched replies, retry keys, resolved fields, explicit overrides, cancellation and reconnect. Backend tests cover terminal acknowledgement/session failures and group-correlated role resolution. Executed browser/full-suite evidence is recorded in the parity ledger.

### D-125 — Agent creation owns project-scoped class discovery

- Date: 2026-09-22
- Status: accepted
- Decision: The creation dialog reads its Agent Class catalog over a cancellable HTTP request for its pinned group. `agent_class_list` resolves that group's project with the same resolver used by class launch, returns group/base-directory provenance and preserves explicit `base_dir` compatibility. Unscoped legacy requests retain cwd behavior. Catalog projections from other panels cannot replace the creation options.
- Interaction: Open, reconnect and explicit refresh reread the catalog without remounting identity/configuration fields or clearing the selected class. Options show identity, version and project/built-in provenance. A selected class blocks submission while disconnected, refreshing, unavailable, archived/disabled, invalid or incompatible with the chosen kind. Catalog errors that prevent backend class resolution are shown and block explicit class launch. Failures retain data and offer Retry. Selecting Default explicitly removes the class selection and keeps the existing default launch behavior available; catalog failures do not require an explicit class for ordinary creation. Terminal creation and pending hire requests do not request a class catalog.
- Limits: This revalidates discovery; the backend remains authoritative at launch if a file changes after the read. It does not add a frozen catalog transaction or guarantee crash recovery. Authoring and runtime-effect acceptance remain separate from discovery.
- Verification: Real temporary-project backend fixtures cover same-ID project isolation, explicit-path precedence and legacy cwd fallback. Component tests cover response correlation, cancellation, failed reads, selection/caret preservation, availability and registry errors. Browser acceptance and executed results are recorded in the parity ledger.

### D-126 — Composer drafts belong to cells and clear after acknowledgement

- Date: 2026-09-22
- Status: accepted
- Decision: Every normal Live terminal surface has a composer. Standalone terminals send buffered multiline input to their captured session; attached terminals target their parent agent's direct-message thread. Per-cell in-memory drafts retain text, attachments, reply context, selection, scroll, edit undo and sent-message recall across selection and workspace changes. Reload persistence applies to server-recorded sent history, not unsent drafts. Legacy terminal-only windows keep their existing terminal-only contract.
- Sending: Pending submission retains and disables the draft. Only an acknowledgement matching the target/session or direct-message thread/reply clears it. Refusal, transport failure and failed durable delivery retain it. Identical retries reuse the attempted key; changing payload or composing again after success starts a new attempt. A late result updates only its source cell. Standalone sends acknowledge after the terminal adapter returns and never redirect to a replacement session during an awaited status broadcast.
- Layout: Compact Agents stacks the ownership tree and a bounded detail pane inside an outer scroll region, so the composer remains reachable. Terminal cells use three detail rows without reserving an absent worktree-controls row. Reply, attachment and feedback rows retain their content height; the conversation temporarily reserves space for them within terminal bounds, while message history takes the remaining space. Result feedback is revealed without replacing the draft.
- Replies and history: Reply records the source message and agent with a visible removable context. Reparenting cannot silently retarget that reply. History offers server-recorded and newly acknowledged messages, first/last-line arrow recall, and restoration of the original unsent draft. Recall remains separate from edit undo. File upload completion likewise belongs to its source cell.
- Cancellation: Only an acknowledged direct-message turn exposes enabled cancellation, with its captured session, submitted-turn key and independent retry key. Outcomes distinguish queued cancellation, interruption, unsupported providers, replaced sessions, no active turn and interruption failure. Cancellation preserves the unrelated current draft; stale sessions cannot issue cancellation for a new session.
- Limits: Existing API caching and durable direct-message idempotency provide retry behavior; this does not guarantee exactly-once raw PTY input after daemon crash or partial side effects. Durable failed-message replay reports the original failure; it is not automatic redelivery. Rich message history, slash/task completion, loop controls and attachment insertion/preview details have separate parity gates.
- Verification: Component tests cover source-cell isolation, delayed/mismatched acknowledgements, retries, reply context, history, uploads, IME Enter and bounded cancellation. Backend tests cover acknowledgement ordering, refusal, compatibility and session replacement during broadcast. Executed browser and full regression evidence is recorded in the parity ledger.

### D-127 — Direct messages preserve reading position and expose delivery meaning

- Date: 2026-09-22
- Status: accepted
- Decision: Direct messages initially mount the newest 30 retained rows. An accessible Load older action and scrolling to the top reveal more of the daemon's bounded snapshot; the view labels shown/retained counts without claiming an unlimited archive. Latest messages explicitly resumes following. Per-agent reading state retains the visible window, anchor offset, follow intent and selected row through normal updates, reconnect, Live/Activity switching and selected-cell changes. Hidden panes do not replace that state with zero-size geometry.
- Content: Rows identify the actual sender, timestamp, message type, blocking asks, replies and durable delivery state/reason. Buffered persistence is labelled Waiting for delivery; failed/cancelled rows remain visibly distinct. System and reminder cards use a neutral style. Replies show the retained source preview or its ID when unavailable.
- Rendering: Help and direct messages share a React-node Markdown renderer. Raw HTML stays text; external links allow only validated HTTP(S)/mailto destinations. Help additionally resolves its indexed internal source links. Images remain descriptive text. Headings, lists, emphasis, quotes, tables and code fit within the reading pane; code can scroll horizontally without widening the workspace.
- Interaction: Explicit Reply does not compete with text selection or links. Copy message writes the exact original source; Copy code writes the rendered fence contents. Both support keyboard access. A custom message context menu offers copy/reply, arrow-key traversal, Escape and focus restoration. Clipboard failure retains content, selection, draft and reading state and offers visible feedback. Copy feedback occupies its own row so it cannot cover message actions. No native blocking dialogs are used.
- Verification: Focused model/component tests cover progressive windows, reading anchors, hidden panes, target switching, metadata, safe rendering, exact copying, selection retention, context actions and failures. Browser evidence, including actual clipboard and reconnect behavior, is recorded in the parity ledger. Provider effects and native application lifecycle remain separate gates.

### D-128 — Composer suggestions use current server scope and remain editable drafts

- Date: 2026-09-22
- Status: accepted
- Decision: Agent direct-message composers offer slash commands exclusively from the server's `user_dm_commands` catalog, filtered for the actual recipient's provider. Attached terminals use their parent recipient; standalone terminal input has no agent command menu. Labels, usage, help and inserted templates come from the catalog. The backend remains the command grammar authority; arbitrary slash-like prose is sent unchanged.
- Task references: A colon at the start of text or after whitespace opens up to eight matching non-archived tasks from the cell's group, falling back to its parent/current group only when necessary. Search matches IDs and titles. Selection replaces only the trigger through the caret, retains surrounding text and inserts the task ID followed by a space.
- Interaction: Suggestions require a collapsed caret and focused, active composer. Pointer selection preserves input focus; arrows navigate, Enter/Tab selects and Escape dismisses without editing or sending. Shift+Enter retains multiline editing. Selection is one undoable draft edit with an explicit caret. IME composition, pending sends, uploads and hidden panes suppress the popup. Reconnect refreshes suggestions without replacing the draft.
- Layout: A bounded scrollable listbox sits above or below the composer within the viewport, outside clipped terminal/composer containers. Accessible option names include the command/task label and explanatory text; the textarea identifies its active option.
- Verification: Model, component and isolated browser evidence is recorded in the parity ledger. Selecting a suggestion does not execute it. Native IME device acceptance and provider runtime effects remain separate gates.

### D-129 — Message-loop cancellation belongs to the displayed loop

- Date: 2026-09-22
- Status: accepted
- Decision: Agent conversations show their active recurring message, interval and next run, or its deferred-until-idle state. Attached terminals show their parent recipient's loop; standalone terminal input has no loop control. A bounded status region preserves space for history and composition and allows longer messages/feedback to scroll.
- Cancellation: The explicit Cancel loop action sends the existing `/loop cancel` command with the displayed `expected_loop_id` and a stable request key. The backend refuses a mismatched or no-longer-active loop before mutating state. Plain slash-command cancellation keeps its existing active-agent behavior.
- Acknowledgement: The UI confirms the matching agent, loop ID, cancelled status and audit message before reporting success. Pending/error/result state stays with that loop across reconnect and cell changes. A lost-response retry repeats the same request and key; an updated cancelled snapshot does not erase pending/error feedback. A replacement loop receives its own operation key. Unrelated draft text, selection, attachments and reply state remain intact.
- Verification: Component, backend and isolated browser acceptance are recorded in the parity ledger. Existing API idempotency protects acknowledged retries; crash/partial-side-effect recovery remains a separate gate.

### D-130 — Composer images participate in editing and undo history

- Date: 2026-09-22
- Status: accepted; React implementation verified under P-216.
- Decision: Images are atomic inline tokens at text positions, with a separate accessible preview dialog. Inserting an image replaces the selected text/tokens; insertion between adjacent images and removal from either side preserve surrounding content. Outgoing messages expand tokens to their canonical paths at those positions. Text-only composition retains native textarea editing until inline tokens require the rich editor.
- State: Each cell owns its text, image positions, selection and bounded semantic undo/redo snapshots. Preview URLs remain alive while a current draft or undo/redo snapshot references them, and are released after acknowledged send or discarded history. A successful send resets history so undo cannot resurrect a sent draft.
- Uploads: Completion belongs to the originating cell and request. Edits move the pending insertion anchor; edits over an originally selected upload range collapse the replacement so the completion cannot erase newer text. Refusals retain the existing document. Paste, file selection and file drop share this contract.
- Constraints: Routine deltas, selection changes and reconnect must not rewrite an actively edited DOM or interfere with IME composition. Inline editing, keyboard deletion, preview focus, async upload, compact layout and actual send paths require live-browser acceptance; model tests alone do not close P-216.

### D-131 — Composer keyboard actions preserve semantic edit boundaries

- Date: 2026-09-22
- Status: accepted; React implementation verified under P-217–P-219.
- Decision: Both text-only and image-bearing composers group consecutive typing/newlines and deletions into undo transactions. A caret/selection move or edit-kind change starts a new transaction. Paste, completion, image changes, recalled messages, IME composition and explicit clear stay separate. Undo/redo ends the current group; sending resets history only after acknowledgement.
- Escape: Dismiss suggestions first, restore a recalled draft next, cancel reply context next, then clear text/images as an undoable edit. Only a fresh, non-repeating Escape on an empty composer requests cancellation of the acknowledged turn. Pending sends/uploads and IME retain the document.
- Navigation: Home/End targets logical line boundaries; Ctrl/Meta targets document boundaries. Shift retains the anchor, including backward selections; image tokens each occupy one editor position. Alt-modified shortcuts, combined Ctrl+Meta undo chords and composing key events are not intercepted.
- Verification: Native textarea and rich-editor browser input must verify grouping, caret movement, paste, selection, reconnect/cell isolation, clear/restore and actual cancellation. Simulated browser composition does not certify device-level IME.

### D-132 — Restore profile navigation once and save acknowledged local choices

- Date: 2026-09-22
- Status: accepted; browser and persistence acceptance recorded under P-204.
- Decision: The main React window remembers its fixed workspace and Control Center section in a bounded, versioned SQLite preference for the active Torque profile. Browser and native main windows share that profile preference. The last successfully saved choice supplies new windows; existing windows keep their local navigation.
- Migration: Before a React preference exists, map Classic's saved active panel to the equivalent fixed workspace/section. Embedded terminal mode prefers the saved standalone layout's last active panel, then its active side/bottom tabs. Preserve Classic's own keys and layout. Unknown/invalid values fall back to Board and Mission Control.
- Lifecycle: Restore once from the first snapshot. User navigation before hydration wins. Snapshot/delta updates and reconnect never redirect an open window. Offline local navigation queues until a connected snapshot; saves serialize and coalesce to the latest local choice. A write acknowledgement requires SQLite commit and matching preference values. Failures preserve navigation and expose Retry; reconnect retries unfinished local saves. Unacknowledged choices are not promised to survive closing the window.
- Detached windows: The panel query pins their content. They neither restore nor write the main-window preference; their existing geometry/reattachment rules remain separate.
- Verification: Cover validation, profile isolation, full/compact snapshots, offline SQLite reads, delayed/failed durable writes, migration, multiple windows, reconnect, save ordering, retry and actual reload in the isolated browser suite. Native window lifecycle remains a separate acceptance gate.

### D-133 — Report actual React render work without measurement feedback

- Date: 2026-09-22
- Status: accepted; model, production-browser and daemon-ingestion acceptance recorded under P-220/P-221.
- Decision: A single root Profiler records committed React render work for the visible workspace subtree, including independently updating children. Production builds use React DOM's profiling renderer; development retains its normal renderer. `actualDuration` measures React render work, not animation-frame wait time, browser paint, or imperative xterm rendering. React's [Profiler contract](https://react.dev/reference/react/Profiler) defines the measurement; installed React DOM source and production-browser acceptance verify the build behavior.
- Aggregation: Match Classic's five-second, newest-240-sample window, nearest-rank p95 and two-second report cadence. Report zero after previously observed work expires. Disconnected windows stop reporting; reconnect restarts the cadence. Collectors are local to each window and remain outside React/Redux state.
- Transport: Use the existing HTTP command endpoint so telemetry acknowledgements do not create UI renders. Allow one outstanding report, abort stalled requests after ten seconds, discard failures, and cancel requests/timers on unmount. Reporting must never interrupt operator work.
- Cost: Profiling adds CPU and bundle overhead. Keep one root observer and a bounded collector; verify production child updates and idle behavior instead of extrapolating from development callbacks. This is render-work telemetry, not a new long-task or paint observer.
- Verification: Controlled rate/p95/window/cap tests, child-only component updates, failure/stall/disconnect/reconnect/cleanup tests, and production Chrome reports with an otherwise quiet WebSocket fixture and real daemon ingestion.

### D-134 — Health shows live measurements and refreshes its reading workspace

- Date: 2026-09-22
- Status: accepted; component and production browser acceptance recorded under P-223–P-225.
- Decision: Health presents current daemon-wide performance measurements separately from persisted history. Render every Classic live metric and collection status from the existing metrics ticks. Preserve zero values, distinguish unknown/disabled/offline data, and show when frontend windows are not reporting. Only the five history series provided by the daemon are graphed; frontend history is not retained.
- Refresh: While visible and synchronized, read workflow health and history together, refresh sixty seconds after each attempt, and refresh after reconnect or an explicit request. Abort obsolete requests and time out stalled reads. Reject mismatched scopes/windows, retain accepted data on failure, and preserve focused controls and expanded sample disclosures across refreshes. Hidden surfaces issue no reads.
- Continuity: Health scope and history window remain local to each window across Control Center tab changes. They do not change the durable workspace navigation preference. Scope changes never display the previous scope's data; performance history remains daemon-wide while workflow health follows the chosen group scope.
- Verification: Component coverage exercises tick fields, unknown/disabled/offline states, polling, cancellation, failures, timeouts, reconnect, hidden cleanup and filter restoration. Production browser acceptance compares displayed frontend values with actual daemon ticks and waits through the real minute timer.

### D-135 — Detach the selected Control Center section

- Date: 2026-09-22
- Status: accepted; component, Rust and browser host-boundary acceptance recorded under P-222.
- Decision: Whole-workspace Control Center detachment carries the current section through the typed host request and native URL. Native code accepts only the known Control sections, rejects section parameters on unrelated workspaces, and preserves the frontend route/query/fragment. React validates query input independently and initializes the selected section before mounting any data-reading panel; absent or unknown sections use Mission Control.
- Ownership: Section handoff happens once at window creation. Subsequent local navigation survives reconnects and shared preference updates; detached windows do not write the main-window workspace preference. Reload restores the section in that window's launch URL. Detaching an already open workspace focuses it without resetting its current section. Reattachment exposes the main window's existing section.
- Scope: This repairs the retained whole-workspace detach contract. It does not change the retirement of arbitrary docking or permit multiple independently owned windows for the same workspace.
- Verification: URL/adapter/bootstrap and current-section component tests, plus production browser host-boundary scenarios for Context, Logs and Help. Actual native lifecycle acceptance remains a separate gate.

### D-136 — Reconnect refresh is independent of operational polling preferences

- Date: 2026-09-22
- Status: accepted; component and production browser acceptance recorded under P-226/P-227.
- Decision: Visible Supervisor and Logs perform a fresh HTTP read when the WebSocket reconnect epoch changes, including when Auto-refresh sessions or Follow is off. The pause setting controls periodic polling and automatic log scrolling; reconnect must not silently re-enable either. HTTP log/session access remains independent of WebSocket availability.
- Continuity: Retain accepted content, session sort/disclosure, log target/search/level/cursor and focused controls. Abort superseded requests before beginning the new read, ignore late responses, retain normal retry behavior, and stop all reads after unmount. A paused log tail appends new entries at its retained cursor without scrolling to the bottom.
- Verification: Reproduction tests fail before the repair for both paused panels; component tests cover pause/focus/scroll/disclosure/cursor and superseded responses, with production browser acceptance against real daemon HTTP data and real WebSocket reconnects.

### D-137 — Pipeline discovery refresh preserves the graph workspace

- Date: 2026-09-22
- Status: accepted; component and production browser acceptance recorded under P-228.
- Decision: The active Pipeline explorer keeps its last accepted group-specific discovery independently of compact snapshot caches. Reconnect, sequence resync and explicit discovery refresh the data after synchronization without removing the current graph. Errors remain actionable above the accepted graph; obsolete, foreign-scope or malformed payloads cannot replace it.
- Continuity: Keep graph DOM, zoom, pan, selected pipeline and focused/selected action while that pipeline remains available. If a pipeline is removed, choose an available fallback instead of retaining an invisible stale selection. Group changes hide the prior graph, and hidden explorers abort reads. The shared auxiliary projection remains updated for compatible consumers.
- Scope: This changes discovery lifecycle, not graph layout or action editing. Older daemon responses without a group field are correlated by their cancelable HTTP request.
- Verification: Component tests reproduce compact-snapshot removal and cover deferred/failed/stale reads, synchronization, selection and hidden cleanup; production browser acceptance uses actual saved action pipelines and reconnects.

### D-138 — Architect review thresholds use fixed validated fields

- Date: 2026-09-22
- Status: accepted; component, production browser, SQLite persistence and backend policy checks passed for P-229.
- Decision: The Architect review-gate object has two non-negative whole-number thresholds and a boolean self-review bypass setting. Present those named controls with units and validate safe integers before saving. Blank, negative, fractional and unsafe values remain editable drafts with focused validation; saving cannot silently truncate or clamp them.
- Schema: This object has a fixed server schema, so it has no arbitrary add/remove-key controls. Its enclosing reset uses daemon defaults. User maps such as environment variables continue to accept literal keys/string values even when a key matches a threshold name.
- Verification: Component boundary/reset/type tests, real daemon refusal/retry/reload of exact numeric/boolean values, and existing review-policy backend tests. Full acceptance of other settings remains separate.

### D-139 — Structured settings fit their available width

- Date: 2026-09-22
- Status: accepted; production browser geometry, draft/reset checks and screenshot review passed for P-230.
- Decision: Named settings objects occupy a full form row, with their children using the shared responsive grid. Nested grids can shrink below the preferred field width. Reset and remove labels wrap within their own cells instead of overlapping adjacent controls; full accessible names remain available.
- Scope: Structured settings only; window resizing retains the current mounted inputs and drafts. No values or save semantics change.
- Verification: Browser geometry, screenshot inspection and reset/draft checks at 1280, 960 and 760 pixel widths.

### D-140 — GitHub configuration helpers use unsaved settings

- Date: 2026-09-22
- Status: accepted; component, provider/manager and production fixture acceptance recorded under P-231–P-234.
- Decision: Group settings provide accessible project discovery, connection preflight, current-repository detection and lane/status suggestions alongside the explicit GitHub fields. Helpers query the unsaved draft; only Save changes persists it. Selecting a project fills its owner/number/id and resolves its Status options. Server suggestions fill an empty lane map and never replace an existing mapping.
- Lifecycle: Correlate reads by group and configuration, cancel on edits, provider/group changes, save or unmount, bound requests to 30 seconds, and clear completed request intent. Refusals and partial/empty discovery remain visible and retryable; resetting a field must not replay a prior request. No automatic reads occur in hidden panels.
- Repository context: Existing Classic/backend preflight resolves an unspecified repository from the daemon working directory. The helper states this behavior and clears the manual repository only in its temporary detection request. It stages the returned repository after success; no backend directory semantics are changed here.
- Verification: Component lifecycle and mapping tests, existing provider/manager tests, and a production-browser scenario through the real daemon with a deterministic read-only gh executable. This fixture does not certify external GitHub credentials, network or permission behavior.

### D-141 — Engineer specializations use one ordered project picker

- Date: 2026-09-22
- Status: accepted; component, backend and production-browser evidence recorded under P-235–P-238.
- Decision: Per-agent settings, direct creation/hire and group defaults use the same project-aware specialization controls: add, remove, move up/down, and a primary marker on the first entry. Keep manual slug editing as an optional disclosure. Existing selections unavailable in a refreshed project catalog remain visible until the operator changes them.
- Lifecycle: Catalog discovery starts on request/focus and refreshes after reconnect while mounted. Scope validation, cancellation, failure/retry and retained input nodes protect selected order, manual drafts and keyboard focus. Changes continue through each surface's acknowledged save/create flow.
- Defaults: A new Engineer follows group specialization defaults until the operator edits its list. An explicitly empty selection means no specializations and is never replaced by later defaults.
- Authority: The operator settings command remains `set_engineer_specializations`. Its server handler also serves the Classic Architect-scoped alias; no MCP scope or ownership rule changes.
- Verification: Component order/focus/draft/error tests, default-inheritance reproduction, production creation/edit/group round trips, and existing backend catalog/creation/Architect scope tests. Other lifecycle and native gates remain separate.

### D-142 — Engineer notification presets stage explicit settings

- Date: 2026-09-22
- Status: accepted; acceptance evidence tracked under P-239–P-241.
- Decision: Group Engineer defaults, direct Engineer creation and per-agent settings share Quiet, Normal and Noisy notification bundles. Each bundle stages the Classic verbosity, push/max delivery intervals, heartbeat and enabled-event list together. Only the existing explicit save/create flow persists the values; relaunch follows acknowledged settings saves.
- Drafts: Derive the selected preset from all five current values. Event order and duplicates do not affect matching. Manual changes, unknown events and explicitly empty event lists remain intact and show Custom when unmatched. Selecting Custom does not mutate fields; selecting a named preset replaces only its five fields. Refusal and reconnect retain pending edits.
- Inheritance: Untouched creation fields follow group defaults. An edited empty event list is an explicit empty override, not a request to inherit. Per-agent origin labels and Use inherited actions remain authoritative.
- Verification: Compare all three bundles to the checked-in Classic constant, test atomic application and manual edits, and exercise real group save/reload, creation, per-agent refusal/retry and relaunch against an isolated daemon. Runtime timer delivery and native lifecycle acceptance remain separate gates.

### D-143 — Worktree diff disclosure bounds mounted lines

- Date: 2026-09-22
- Status: accepted; P-242 tracks executable acceptance.
- Decision: Worktree Changes uses the Classic automatic disclosure thresholds: collapse when there are more than 12 files, more than 1,500 total lines, or a single file has more than 800 lines. In an automatically collapsed diff, preview the first file with at most 400 lines when available. File headers remain keyboard-operable; collapsed files mount no line content.
- Reading: Expanded files initially mount at most 400 lines across hunks. Show more adds the next 400 (or remaining) lines without replacing preceding content. Collapse all and Expand all preserve already requested line budgets. Label how many files are collapsed and how many lines remain; binary and unavailable line diffs have explicit states.
- Continuity: Key disclosure and line budgets by file path for the inspector lifetime. Refresh/reordering retains existing file nodes; switching Changes/History hides their existing surfaces instead of recreating them. Each agent's inspector starts a new disclosure workspace. Reconnect/read-cancellation acceptance remains a separate lifecycle gate.
- Verification: Classic threshold boundaries, mixed large/small/binary files, multi-hunk truncation, complete progressive expansion, path-keyed refresh and inspector tab retention have focused tests. Production acceptance uses an actual temporary Git worktree.

### D-144 — Worktree inspector reads survive resync and failure

- Date: 2026-09-22
- Status: accepted; P-243 tracks executable acceptance.
- Decision: The open inspector owns its accepted Changes, merge-preflight and History results. Read each through a correlated HTTP request, validate the response type/agent and required shape, and retain the previous successful result independently if a later read fails. Compact WebSocket cache replacement must not remove accepted content.
- Lifecycle: Read only while the inspector is active and the connection has a synchronized snapshot. Refresh on reconnect/resync and explicit Refresh/Retry. Cancel obsolete requests on target/path/branch changes, hide, disconnect and unmount; bound each read to 30 seconds. Hidden or closed inspectors perform no reads. Errors identify the failed resource and keep retry available.
- Continuity: Keep file disclosure, requested line budgets, Changes/History selection and merge drafts while refreshing; preserve existing file nodes and reading scroll through refused reads. Temporarily hiding the inspector retains its disclosure model outside the modal portal. Agent removal or worktree relocation is resolved from current agent state, not the captured opener record.
- Preflight: PR/merge controls require a successful current preflight; a retained older clean result does not enable actions during disconnection, refresh or failure. Existing server mutation and merge-authority rules remain unchanged.
- Verification: Reproduced cache-loss regression, independent read failures, mismatched/incomplete replies, stale cancellation, hidden/unmounted inactivity, bounded timeout/retry and production-browser reconnect with real checkpoint changes. Mutation acknowledgement and native lifecycle remain separate gates.

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

### D-041 — Per-agent settings show server-resolved origin explicitly

- Date: 2026-08-09
- Status: accepted
- Decision: Architect and Engineer per-agent Settings render every editable
  value with the origin returned by the server (`per-agent`, `group`, or
  `default`). An active override exposes a `Use inherited` action; inherited
  values remain visible and labelled rather than relying on blank-value
  inference.
- Rationale: At the per-agent layer, an intentional override and an inherited
  blank can be operationally different. The server is the only authority for
  precedence and origin, so the client must display that result and submit only
  sparse user changes. This is a deliberate exception to D-033's quieter
  presentation for group-kind defaults.
- Scope: The shared Architect/Engineer per-agent Settings dialog.
- Constraints: Opening and saving an unchanged dialog emits no mutation; reset
  remains inside the explicit save boundary. The client must not reconstruct
  group/default precedence.
- Verification: Frontend regressions cover unchanged save, override origin, and
  returning a field to inheritance.

### D-114 — History refreshes without replacing the reading workspace

History uses cancellable correlated reads for its current status list and selected run. Reconnect and explicit Refresh reload both independently; selecting a run loads only its detail. Failed reads retain the last accepted data with a specific retry control. A mismatched run ID never replaces the selected record, and hidden History cancels all reads. Search, selection, input focus/caret, independent pane scroll and open message disclosures remain on their existing DOM nodes during refresh. Group changes start a fresh workspace. Wide layouts constrain both panes to the available height; compact layouts stack bounded panes.

History renders the persisted contract: role/template, input/output token counts, task title/outcome/start time, and message timestamps/content. Long messages use a keyboard-operable disclosure. Task and message links open known Board tasks through shared selection, changing group when necessary; unavailable targets retain their text with disabled navigation. Focus live agent selects its Agents workspace as well as sending the existing focus command. The server's bounded history/message window remains unchanged; absent archived task discovery and native lifecycle acceptance remain separate gates.


### D-145 — Worktree inspector mutations require matching acknowledgements

- Date: 2026-09-22
- Scope: Inspector checkpoint, rollback, rebase, PR, merge and removal operations.
- Decision: Send explicit keyed HTTP writes, hold the operation's draft and confirmation while pending, and accept only a response matching its operation and agent. Rollback additionally matches the selected checkpoint; removal requires verified removal. A confirmed refusal retains the editor and permits a fresh attempt. A lost, timed-out, incomplete or mismatched acknowledgement retains the original payload/key and exposes explicit retry; reconnect does not replay writes automatically.
- Read recovery: Suspend inspector refreshes during an unresolved write, retain accepted content and disclosure, and refresh Changes/preflight/History after a confirmed result. A structurally valid blocked preflight is accepted data: its error, stale-base warning and conflict details must remain available with the Rebase recovery control. Only actual read/transport/shape failures use the retained-data retry state.
- Results: Stale cached progress never disables a new operation. PR links, pending merge state and cleanup warnings belong to the acknowledged result. If a merge closes the agent before its acknowledgement, retain the submitted target long enough to show the result; it cannot start new writes after disappearing. Dialog dismissal is guarded while an operation remains unresolved.
- Backend: Explicit checkpoint distinguishes no changes from failed staging/commit/verification, while automatic checkpoint callers retain best-effort behavior. Rollback acknowledges the manager's actual outcome. A scoped coordinator joins simultaneous retries only for the six keyed inspector commands and complements the existing completed-response cache. It does not provide atomic execution across daemon crash, persist an unsent client retry across window destruction, or change unrelated API command execution.
- Verification: Component, real-HTTP, real-Git browser and backend evidence is recorded in the parity ledger. The separate Focus-panel worktree toolbar remains an explicit follow-up; this decision does not certify external GitHub or native lifecycle acceptance.


### D-146 — Worktree toolbar operations retain their target and outcome

- Date: 2026-09-23
- Scope: Focused-agent worktree creation, checkpoint and preflight controls.
- Decision: Active-session creation requires an explicit warning that restarting replaces the current conversation. Cancel sends no write. Creation for a stopped agent proceeds without starting a session. A workspace-owned controller retains the submitted agent across focus changes and temporary hiding; pending or uncertain operations cannot be dismissed or duplicated.
- Acknowledgement: Creation confirms the returned worktree path and, when requested, a distinct new session. Bind creation to the reviewed session ID and refuse a changed session before side effects. Partial creation exposes the existing path and offers a separate relaunch retry against that path, without creating another worktree. Lost acknowledgements retain the original payload/key; reconnect never automatically replays a write. The scoped worktree coordinator now includes creation as its seventh command.
- Checkpoint and preflight: Toolbar checkpoint uses the same acknowledged lifecycle as the inspector, including created, clean no-op, refusal and explicit retry. Preflight opens the retained inspector and its correlated Changes/preflight/History reads so readiness is visible for the requested agent.
- Boundaries: Session or target changes during awaited work are checked before the next lifecycle effect; deleting an agent during creation cannot resurrect it through persistence. These checks do not make Git and PTY operations atomic across daemon crash or preserve unsent retries across window destruction.
- Verification: Focused component/backend regressions and isolated real-Git/real-PTY browser acceptance are recorded in the parity ledger. External GitHub effects and native lifecycle remain separate gates.


### D-147 — Review PR publication and inherit merge cleanup intent

- Date: 2026-09-23
- Scope: Worktree inspector PR creation and merge options.
- PR review: Before any Create PR write, show the reviewed source branch and target base and state that the branch is pushed to origin first. Cancel sends no write. A changed agent, path, branch or base requires a new review. Pending/refused/unknown outcomes retain that review; known refusals allow a fresh explicit attempt, while unknown outcomes retry the exact acknowledged-operation payload/key. A confirmed result returns to the inspector with its message/link.
- Confirmation workspace: PR review and rollback share the inspector's single dialog, preserving the mounted diff and merge controls while temporarily hiding them. This avoids inaccessible overlapping dialogs when the workspace hides and reopens. Keyboard focus moves into the confirmation and returns to its opener after cancellation or completion.
- Merge defaults: Read cleanup mode and preserved-diff defaults from the target agent's group. `keep`, `close`, `remove`, `close_remove` and `auto_sweep` use the same close/remove mapping as Classic. Untouched options follow refreshed group defaults; explicit checkbox edits remain through refresh/reconnect. Once submitted, the reviewed values remain fixed through failure and retry. Closing and reopening starts a fresh review from current defaults.
- Timing: Explain that cleanup runs after the merge completes, not when a pull request is merely created. The backend remains authoritative for actual cleanup, preserved-diff eligibility and warnings.
- Verification: P-248/P-249 record component, Classic-contract and isolated production-browser evidence. External GitHub execution and native lifecycle remain separate acceptance gates.


### D-148 — Worktree release reviews shared use and destructive consequences

- Date: 2026-09-23
- Scope: React worktree inspector removal; the existing unreviewed cleanup callers retain their contracts.
- Review: A correlated read identifies other agents using the path, the current branch/base commits, uncommitted and ignored files, and attached-session or queued-work guards. Read errors disable confirmation. Git evidence is read again on submission; changes to the reviewed target, sharing, session, HEAD, status, diff or untracked-file metadata require a fresh review. These checks are not a filesystem lock or an atomic transaction with Git/PTY operations.
- Shared use: Clear only the selected stopped agent's link and restore its repository directory. Keep the shared files, branch and other agents' metadata and sessions. Shared users include agents whose directory/current path/git root is inside the worktree, even without an explicit worktree link.
- Physical removal: Explicitly warn that uncommitted and ignored files will be discarded. Existing active/fresh/queued-work guards remain in force. Branch deletion uses the existing safe Git cleanup; a retained branch is a visible partial cleanup result, not a reason to replay physical removal. Reviewed release does not restart a session.
- Recovery: Cancel performs no mutation. Pending and unknown outcomes retain the reviewed context; explicit retry uses the same payload/key. Verified link-only and physical-removal acknowledgements have distinct contracts. Keep the acknowledged result visible until dismissal, including retained-branch details. Long paths and branch names wrap within desktop and narrow dialogs.
- Verification: Component and backend regressions, real-Git evidence checks and isolated browser acceptance are recorded under P-250. Native lifecycle, external integrations, crash atomicity and window-destruction retry persistence remain separate concerns.


### D-149 — Settings owns its draft until navigation is resolved

- Date: 2026-09-23
- Scope: Settings exits through Control Center sections, workspaces, group selection, command palette, native menu callbacks, same-window Classic link and detach initiation.
- Decision: Route a complete requested navigation action through one Settings guard. With unsaved edits, offer Keep editing or Discard changes; cancellation performs neither settings writes nor the requested navigation. Keep the form mounted and restore the last editor's focus/caret. Discard resets unsaved fields and secret-entry buffers before applying the requested action once. Clean and reverted drafts need no confirmation.
- Pending save: Do not dismiss or abort the Settings owner while a save is unresolved. Retain its requested destination and expose the save's eventual state. Failure permits returning to the retained draft or explicit discard; successful completion permits explicit continuation. Never navigate automatically merely because a save finished. Already committed scopes remain applied when remaining edits are discarded.
- Scope continuity: If the daemon changes the active group while Settings has edited or saving state, retain the original group form and identify it visibly. Switching to the new group is explicit. Save continues to target the retained group. An external native ownership change likewise does not erase the local form.
- Boundaries: This guard does not persist drafts or raw secret inputs, promise recovery after hard browser refresh/window destruction, or intercept operating-system process termination. Existing appearance controls retain their immediate local-save behavior. Native menu/detach integration has host-boundary tests; actual Tauri lifecycle remains a separate acceptance gate.
- Verification: P-253 records focused app coverage, isolated production-browser navigation/reconnect/save-failure acceptance and screenshot inspection. Broad settings lifecycle and remaining enum/validation/search work remain open.


### D-150 — Capture choices and retention limits explain their storage effects

- Date: 2026-09-23
- Scope: Global event-ingest and MCP call-log controls.
- Decision: Offer Off, Metadata and Full as explicit choices. Explain that capture applies to future events and does not rewrite historical records. Metadata keeps argument keys and size summaries; matching allowlist tool names, globs or regexes retain full values. Off drops arguments and results regardless of the allowlist.
- Retention: Require at least one row and a nonnegative integer day count. Explain that zero days disables age expiry while the row cap still applies, and lowering retention can remove older records. Invalid drafts stay visible and receive focus before any save writes occur.
- Verification: P-251/P-252 record component, time-controlled retention and isolated production-browser acceptance. This change uses the existing daemon capture/retention semantics.


### D-151 — Settings search reveals the existing editor

- Date: 2026-09-23
- Scope: Workspace Settings controls across global/group, Engineer/Architect defaults, AI, relay, appearance and keyboard shortcuts.
- Decision: Search mounted control labels, associated descriptions and section/disclosure scopes, including closed disclosures. Show the scope beside each result so repeated names are distinguishable. Match all query words without case sensitivity. Initially show 20 results with an explicit Show more action; announce counts and empty results.
- Navigation: Activating a result clears the search, opens its ancestor disclosures and focuses/scrolls the existing control. Enter activates the first available match without submitting Settings; Arrow Down enters results and Escape or Clear settings search clears the query. Search does not save, reset, remount or filter away form fields. Pending-save disabled controls remain unavailable in results.
- Refresh and privacy: Refresh results when mounted controls, labels or descriptions change, while retaining the query and its selection. Never index input values, provider keys, textarea drafts or option content. The search index stays local to the mounted Settings form and sends no requests.
- Verification: P-254 records focused model/component/app checks and isolated browser acceptance, including retained drafts, reconnect, scope reveal and narrow-screen results. Broad settings lifecycle and native acceptance remain separate gates.


### D-152 — Settings numeric editors use the supported domain boundaries

- Date: 2026-09-23
- Scope: Guidance hint cadence, Shared Context default lifetime, and perceived-empty probe threshold/window.
- Decision: Require integer cadence 0–100, Context lifetime 1–60 days, probe threshold 2–25 and window 10–3600 seconds. Describe the units and cadence-zero behavior beside the controls. These match Classic's cadence control and the daemon's existing normalization boundaries.
- Validation: Preserve invalid editing buffers, reveal/focus hidden invalid controls and prevent the coordinated save from writing any scope. Accept and persist both endpoints without silently clamping the operator's entered value.
- Verification: P-255 records focused component, persistence/reload and isolated production-browser evidence. The existing daemon normalization/runtime semantics are unchanged.


### D-153 — Shortcut capture, navigation and dispatch share effective bindings

- Date: 2026-09-23
- Scope: Settings keyboard editors, the command/group/panel navigators, Board/create/composer actions and their visible hints.
- Conflict review: Capturing a shortcut edits the Settings draft only. Show the current owner before replacing a mutable binding; Cancel retains both assignments and restores editor focus. Explicit reassignment swaps the previous binding into the other action only when the resulting assignments are unambiguous. Reset uses the same review. If assignments change during review, require a fresh confirmation. Fixed navigation conflicts cannot be reassigned.
- Input ownership: Tab traverses controls and Escape cancels review. Capture does not activate global shortcuts; composing/repeated key events are ignored. Preserve ordinary text editing, menus, dialogs and local item-navigation keys. Command-palette platform aliases retain every additional modifier.
- Navigation: Cmd/Ctrl+G opens group-only search; Cmd/Ctrl+P opens panel-only search over the fixed workspaces and Control Center destinations. Cmd/Ctrl+K opens all commands. Existing fuzzy selection, arrow/Enter/Escape navigation and Settings draft protection apply. Docking, pinning and panel reordering remain covered by the documented fixed-workspace redesign.
- Dispatch: Use the effective saved bindings for actual actions and hints. Both B and Classic K open Board by default; the legacy panel.toggle override remains supported as an alternate Board entry point. Focus composer reveals the selected agent's Live view before focusing its editor. A saved create-task override replaces N even while a Board card owns focus.
- Verification: Focused model/component tests and isolated production-browser acceptance are recorded under P-095/P-101/P-102. Native window routing and cross-platform host lifecycle remain separate acceptance gates.


### D-154 — Launch inheritance is a preview, not an implicit override

- Date: 2026-09-23
- Scope: Shared Agent and Worker/Engineer/Architect launch settings.
- Decision: Resolve model and reasoning suggestions from the explicit role provider, the current shared Agent provider draft, then the runtime default command matched against discovered provider metadata. Use the effective model's reasoning choices and advertised default when available. Keep unknown/custom providers and models editable; do not invent choices for missing metadata.
- Preview: Empty provider, model, reasoning, command, directory, shell and environment-file fields show the inherited shared draft or known runtime/provider default. Shared edits update these previews before Save. Role-specific explicit overrides stay intact. Show provider/system default, current directory or none when no concrete inherited value is available.
- Persistence and stability: Keep the input value empty while it inherits. Preview computation must not add settings writes, convert suggestions into overrides or replace a focused editor. Reset/clear resumes inheritance. Current coordinated Settings save and reconnect reconciliation continue to own drafts, focus and sparse writes.
- Verification: P-256 records focused component/model and isolated production-browser evidence. This projection does not launch providers or change the daemon's launch-resolution rules.


### D-155 — Relay editing preserves resolved configuration and explicit overrides

- Date: 2026-09-23
- Scope: Relay settings in Control Center.
- Decision: Show the daemon's effective enabled state and source provenance. Keep inherited text values in placeholders, with empty editable overrides; show settings-sourced values in their editors. Prefer live resolved configuration from snapshots, settings reads and low-frequency deltas. If the resolved contract is absent, retain raw settings compatibility.
- Editing: Only touched fields enter the coordinated save payload. Clearing text resumes file/environment inheritance. Incoming updates refresh untouched fields, preserve dirty drafts, and defer replacement of a focused clean editor until blur. Save acknowledgement must retain the most recent focused edit rather than restore its pre-edit value.
- Enabled fallback: Explain when the environment enables Relay. Saving Disabled clears the settings override; the environment flag can still keep Relay enabled, and the post-save form must reflect that effective state.
- Privacy and boundaries: Read only the five public configuration fields. Display private-key paths or empty inheritance placeholders; never read or display inline PEM fields. Configuration verification does not imply external Relay connectivity or credential provisioning.
- Verification: P-257 records model/app tests, backend resolution-contract checks, and isolated production-browser acceptance with the connector module deliberately unavailable.


### D-156 — Default role selection uses the scoped catalog

- Date: 2026-09-23
- Scope: Group default_agent_template, the compatibility field for the default worker role.
- Decision: Offer None plus Project and User optgroups using discovered display names. Filter shadowed entries and prefer a project role when names collide, including older catalogs without explicit shadowed flags. Persist only the role name through the normal group settings save.
- Continuity: A selected name absent from the current catalog remains selected and visibly identified as unavailable. Refresh, disappearance or changed labels do not clear it, write settings, replace the editor or move focus. Reappearing entries regain their discovered label. None and Reset explicitly clear the override while preserving other launch settings.
- Verification: P-258 records component coverage and isolated production-browser catalog disappearance/reappearance, sparse save/reload, clear and reset acceptance. No role is launched by this selector.


### D-157 — Digest event selection separates mandatory and optional delivery

- Date: 2026-09-23
- Scope: Group Engineer and Architect digest event settings.
- Decision: Show role-specific, named optional-event checkboxes and an Always included list matching the daemon's mandatory event floor. Mandatory events have no off control. Clearing optional selections persists an explicit empty list while mandatory delivery remains active. The Engineer preset selector continues to update these choices; manual changes can produce Custom.
- Extensibility: Preserve saved unknown names as additional choices. Retain unchecked additional choices in the mounted editor so operators can undo a draft change. An explicit event-name field supports extension/future event names; Enter adds to the local draft without submitting Settings. Prevent duplicate or mandatory entries.
- Stability and save: Incoming reconciliation, presets and reset update checkbox values without replacing focused controls. Selecting events only edits the settings draft. Save uses existing sparse role-scoped commands, preserves unrelated values and retains changes after refusal/reconnect.
- Verification: P-259/P-260 record focused component tests, direct comparisons with Classic choices and backend mandatory constants, and isolated production-browser acceptance. Configuring a digest filter does not certify timer-driven or external-provider delivery.


### D-158 — Status-bar settings preview the unsaved visibility draft

- Date: 2026-09-23
- Scope: Global status-bar visibility in React Settings.
- Decision: Display a compact wrapping rail of sample indicators above the visibility choices. Label the values as samples; mirror the React footer order and keep segments passive. Every visibility edit immediately updates the preview. An explicit empty choice shows “No optional items selected”.
- State: The preview derives only from the current global draft and shares its reset, refresh, reconnect and save lifecycle. It performs no reads or writes. The live workspace footer continues using acknowledged saved settings; preview interaction never navigates or triggers operational actions.
- Verification: P-261 records component and isolated browser coverage for all indicators, unsaved changes, empty selection, reset, save/refusal/retry, reload and reconnect retention.


### D-159 — Architect journal frequency offers named choices and custom entry

- Date: 2026-09-23
- Scope: Group Architect journal checkpoint frequency.
- Decision: Offer Classic's action-count, elapsed-minute and manual-only choices with readable labels. Saved custom frequencies remain selected and editable through an explicit Custom frequency choice. Custom input accepts the existing positive-integer action/minute syntax and manual_only; blank or malformed drafts block Settings Save using native form validity.
- Continuity: Choosing a frequency edits only the Architect settings draft. Custom typing retains its input and focus even when it matches a preset. External field reset restores the default preset. Existing sparse save, refusal/retry and reconnect handling remain shared with other settings.
- Verification: P-262 records focused picker/reset/validation tests, exact Classic/backend catalog comparison and isolated browser save/reload/reconnect acceptance. Selecting a frequency does not certify timer-driven reminders or provider execution.


### D-160 — Agent Activity reads belong to the visible tab

- Date: 2026-09-23
- Scope: Selected-agent Activity resource loading, refresh and reconnect.
- Decision: Read Events, role Journal, Decisions, MCP, History, Agent Class and Architect peer resources only while their owning Activity tab is visible. Tabs backed by the synchronized task/message snapshot perform no auxiliary read. Engineer Journal requests only its journal/session/settings dependencies; Engineer Events includes group pause settings. Refresh repeats only the current plan at the retained page limits.
- Lifecycle: Use owned abortable HTTP reads with response-type/target validation, a 30-second timeout and visible retry. Changing agent, tab, applied filters or visibility cancels obsolete reads. Wait for a synchronized connection and refresh after reconnect/resync. Retain accepted target-scoped content so refresh, compact snapshots and failures do not remount expanded rows or remove focus. Do not use another agent's latest response aliases.
- MCP drafts: Typing tool/outcome/range edits does not fetch or reset the displayed result window. Apply commits the query and resets its remote limit; refresh/reconnect/page extension use the applied query while unfinished edits remain in their controls.
- Layout: Read status and retry occupy a dedicated auto-sized row above the bounded scrolling content, so the content cannot overlap the retry control.
- Verification: P-263 records component/integration and isolated browser acceptance. Planning section reads are a separate P-264 repair; this change does not close the broad P-112 gate.


### D-161 — Planning reads follow the visible section and editor

- Date: 2026-09-25
- Scope: Planning section lists, relationship choices, reconnect and read failures.
- Decision: Initiatives reads its list; Areas reads its list with links/notes; Thinking reads notes and briefs; Decisions reads its current archive view; Hires & journals reads pending hires and group journals. Schedules uses the synchronized snapshot. Only an open Initiative editor adds decisions; an open Area editor adds initiatives and decisions for its relationship/note selectors. The header reports the visible section's counts instead of implying unseen collections have loaded.
- Lifecycle: Correlated HTTP reads validate response type and explicit group, abort on plan change/unmount, time out after 30 seconds and retry in place. Reconnect/resync waits for a synchronized snapshot and refreshes only the current plan. Retain accepted collections until their own replacement arrives, then apply ordinary live updates again; pending/failed reads preserve selected editors, drafts, focus and caret. Changing workspace group mounts a fresh scope.
- Feedback: Loading/error/retry is visible inside an open editor or creation dialog, so modal focus containment never makes the recovery action inaccessible. Otherwise it appears above the section content. Retry and Refresh only repeat the current plan; archive toggles only affect their relevant section.
- Verification: P-264 records component/integration and isolated production-browser acceptance. Broad P-112 and other parity gates remain independently open.


### D-162 — Area browsing restores the Classic retained window and filters

- Date: 2026-09-25
- Scope: Planning / Areas list, discovery and relationship targets.
- Decision: Request the same bounded 500 active Areas as Classic. Use the entire accepted collection for relationship/note choices; search and filters affect only the browsing list. Exclude archived and other-group records and sort by lifecycle order, case-insensitive type, then title/id, with Classic's planned fallback for unknown lifecycles.
- Controls: Search matches id, slug, title, type, lifecycle, summary, user purpose and system purpose case-insensitively. Lifecycle offers all seven values and All. Type choices come from active Areas in the current group; a selected type that disappears remains explicit until changed or cleared. Combine the filters locally, show matching/total counts, distinguish no matches from no Areas and provide Clear filters.
- Continuity: Filters survive section changes, refresh, reconnect and incoming records within the mounted group. Filtering sends no reads or writes. Retain the search input, focus and caret during incoming data; changing group resets the scoped workspace. Controls wrap in narrow layouts.
- Verification: P-265–P-269 cover the window, search, lifecycle/type filters and ordering independently, with focused and real-daemon browser acceptance.


### D-163 — Activity owns its project Agent Class catalog

- Date: 2026-09-25
- Scope: Selected-agent Activity / Agent Class discovery and choice availability.
- Decision: Resolve the catalog from worktree repo root, agent directory, then current path; if no path exists, resolve the selected group. Scope list/status/assignment commands consistently. Activity owns the correlated catalog response and never borrows the shared Control Center/creation catalog. Reject explicit directory mismatches and obsolete responses; a changed project invalidates the prior catalog immediately.
- Continuity: Retain the accepted same-project catalog and desired draft through reconnect, compact snapshots and failed refresh. A missing desired ID remains an explicit unavailable option. Block assignment while reads are pending/failed or the current catalog has not been verified.
- Availability: Reuse the creation flow's Classic-equivalent base-kind, archived/disabled, launchable and invalid checks. Unavailable catalog entries remain visible but disabled. Explain a retained unavailable selection; changing to Default is explicit. Assignment still applies only at the next launch boundary.
- Verification: P-270/P-271 track project isolation and unavailable-choice gating with focused and isolated browser acceptance. Assignment acknowledgement/relaunch behavior remains separately auditable under P-054.


### D-164 — Agent Class assignment follows acknowledged state

- Date: 2026-09-25
- Scope: Selected-agent Activity / Agent Class status, selection and assignment writes.
- Decision: Status reads and assignment replies share one latest accepted status per agent. An earlier assignment never overrides a later status read. Retain that accepted status during compact reconnect snapshots. An untouched selector follows the saved assignment; an edited selector remains a draft while the desired/effective facts update independently.
- Freshness: Visible class details refresh after an assignment or effective-launch field changes, after a completed local save attempt and after reconnect. Unrelated agent activity does not refresh class details. Pause and cancel class reads while a local assignment is pending, then refresh status, catalog and audit from the current scope.
- Saves: Use a correlated HTTP request and an immediate in-flight guard retained in app-session state per agent; switching agent or Live/Activity does not drop the pending operation, its reviewed draft or its eventual acknowledgement. Disable the selector, Save and Relaunch while assignment is pending. Only a matching response type, agent ID and assigned class ID can acknowledge success. Refusal preserves the reviewed selection and reports its reason inline. A transport failure, timeout or mismatched acknowledgement reports an unknown outcome; never replay a write automatically. Refresh the displayed status so the operator can review it before another explicit Save.
- Verification: P-272/P-273 separately track fresh desired/effective status and acknowledged assignment lifecycle. Relaunch remains an explicit operation for stopped agents only. Running agents get guidance that their current class stays frozen until the next launch; saving never stops or restarts them. P-274 tracks this launch-state distinction.


### D-165 — Agent Class previews explain selection separately from the running class

- Date: 2026-09-25
- Scope: Activity / Agent Class selected/default preview, warnings and current assignment metadata.
- Decision: Replace raw JSON with readable identity/version, purpose, base kind, lifecycle/status, scratch-only state, access and apply timing. Default selection previews the actual default class for the agent kind. A missing selection never borrows an unrelated class. Selection and disclosure changes are read-only; accepted preview content, selection, focus and expanded grants survive refresh/reconnect/refusal.
- Authority: Show resolved grants from `effective_authority.capabilities` (or the canonical compact ACL) as Allowed actions, with their scopes in an expandable list. Show authored deny rules from `authoring_definition.acl`; compact ACL rules are resolved grants and must never be relabeled as denials. An unavailable authored deny list is explicitly unavailable. Allow mode explains default denial; deny mode shows its explicit denials or their absence.
- Warnings: Keep Current class warnings associated with the frozen effective class and Selected class warnings associated with the reviewed choice. Normalize duplicates within each scope. Match Classic's deliberate exclusion of generic connector-governance notices (`_agentPanelClassConnectorCaveat` returns an empty string); retain actionable connector warnings. This corrects the earlier P-276 inventory wording, which treated the intentionally omitted caveat as a gap.
- Metadata: Current status includes base-kind metadata, lifecycle/status, assignment actor/time and effective freeze time. Accepted status takes precedence over stale raw agent fields, including explicit zero timestamps. Render valid timestamps with machine-readable dates and wrap long values in narrow layouts.
- Verification: P-275–P-277 track preview, warning and metadata acceptance independently. No mutation/backend contract changes are required.


### D-166 — Settings prompt previews belong to the reviewed draft

- Date: 2026-09-25
- Scope: Settings Engineer/Architect system-prompt preview and copying.
- Decision: Keep the inline preview design, with an owned HTTP read using the unsaved selected-role settings and group overrides. Accept only a response matching its request ID, group and role. Cancel obsolete reads on replacement, draft change or unmount; changing and reverting a draft does not revive a cancelled preview.
- Outcomes: Show loading, empty, failure and stale states explicitly. Bound waiting to 30 seconds, provide an explicit retry, and never save settings or automatically replay a request to obtain a preview. Unrelated role edits leave the accepted preview intact.
- Copy: Enable copying only for the accepted current result. Preserve selectable text when clipboard access fails, show success/failure inline, and prevent a late clipboard completion from unlocking or labeling a newer copy. Saving settings disables preview/copy actions.
- Verification: P-278/P-279 track response ownership and clipboard behavior. Focused regressions cover mismatches, cancellation, timeout, empty/refused results and overlapping clipboard completions; isolated browser acceptance covers both real backend role renderers and persisted-settings isolation.


### D-167 — Relay credential pairing is an acknowledged Settings operation

- Date: 2026-09-25
- Scope: Settings / Relay one-time daemon pairing token, replacement and results.
- Decision: Provide a transient password input for the administrator-supplied token. Gate pairing on the saved effective Relay URL and daemon ID, including inherited sources; unsaved Relay edits do not change the pairing target. Pairing is available before the connector is enabled. Do not store the token in Settings, shared projection state or browser storage.
- Replacement: An existing effective credential requires inline confirmation naming the credential and explaining the possible connection disruption. Cancel performs no mutation. Changed effective configuration or token edits invalidate that confirmation.
- Ownership: Use one owned HTTP operation with an immediate in-flight guard and a 30-second timeout. While it is pending, disable Settings edits/saves and retain the Settings workspace through the existing navigation guard. Pause Settings refresh reads until it settles, then reconcile refreshed values without losing unrelated drafts. Reconnect never replays pairing.
- Outcomes: Only a valid credential acknowledgement clears the token and updates effective Relay configuration. Present credential ID, private-key file path, owner and provenance on success. Token rejection retains the input. A Relay-accepted credential whose local Settings save failed displays both recovery values and explicit save-or-revoke guidance. Transport, timeout or malformed acknowledgement reports an unknown outcome for operator review; never retry automatically. Unmount cancels observation and clears the transient token with its owner.
- Verification: P-280/P-281 track configuration/token entry and acknowledged replacement/recovery. Component tests exercise gates, confirmation, failures, late replies and token continuity; isolated browser acceptance exercises Settings protection/reconnect and deterministic response fixtures without minting external credentials.


### D-168 — Relay device links have a transient display owner

- Date: 2026-09-25
- Scope: Settings / Relay device-link confirmation, generation and display.
- Decision: Gate generation on saved effective Relay enablement and URL. Opening the section never mints a credential. Use inline confirm/cancel for each generation, a synchronous in-flight guard and an owned HTTP acknowledgement with a 30-second bound. Report refusal and unknown outcomes explicitly, with manual retry through a new confirmation. Cancel obsolete pending reads when the effective target changes or Settings closes; reconnect never replays the mutation.
- Lifetime: Keep the successful code, establish URL and expiry only in the mounted device-link view. Never dispatch the response to shared projection or connection caches. Ignore old cached results. Dismissal, closing Settings, or requesting a replacement removes the previous display; cancellation of that replacement does not restore it. Routine configuration/reconnect refreshes preserve the accepted display, its DOM and focus, while independently updating generation availability. Show the source Relay with the accepted link.
- QR: Lazily load the existing pinned local `qrcode-generator` asset through the same first-party static route used by xterm. Generate numeric SVG modules locally with a four-module white quiet zone and black foreground. No credential-bearing URL goes to an external image/QR service. An encoder failure leaves selectable URL/code and an independent QR retry that does not mint another credential.
- Presentation: Show keyboard-selectable URL/code, QR, relative expiry and a machine-readable absolute timestamp. Update relative expiry while the display is mounted; expiry does not trigger another request. Provide explicit dismissal and explain the display-once behavior.
- Verification: P-282/P-283 track confirmation/outcomes and display lifetime/details. Component/model/App regressions cover gates, malformed replies, timeout/close/target cancellation, retained display, no shared-state secret cache, expiry and local numeric encoding. Browser acceptance verifies the actual vendor-generated QR, zero external requests, reconnect continuity and dismiss/close behavior using controlled mint replies.


### D-169 — Relay diagnostics share live status and retain explicit probe results

- Date: 2026-09-25
- Scope: Settings / Relay connection test and diagnostics; workspace status bar.
- Probe: Keep one explicit, correlated HTTP connection test in app-session state, matching Classic's retained result across Settings close/reopen. Navigation and reconnect neither discard the pending read nor issue another probe. A synchronous guard blocks duplicates; a 15-second timeout aborts observation and enables explicit retry. Only a matching typed result with a nonempty status is accepted, and late/superseded acknowledgements cannot replace the current operation. Show status, message and multiline detail; preserve unknown result text with warning severity.
- Diagnostics: Settings shows the live projected connection status, host, daemon ID, retry attempts, state-since time, last connection and last error. Dates remain readable and valid dates carry machine-readable timestamps. Missing payload hides the connection section; missing individual values are explicit. Routine deltas preserve Settings drafts, focus/caret, probe results and transient pairing/device-link views.
- Severity: Use the same mapping in detailed status and the passive indicator: connected is success, disabled is muted, error is danger, and connecting/disconnected/unknown are warning. At five or more retries, connecting/disconnected escalates to danger while retaining the true reported status.
- Indicator: Show a distinct compact Relay label beside Daemon only when daemon-status visibility is enabled and Relay is configured. Honor explicit configured/enabled flags, preserve unknown states and expose status/host/retry/timestamp/error detail through accessible text and a tooltip. The focusable indicator is informational, not an action, and remains visible in narrow layouts. The draft status-bar sample includes both Daemon and Relay.
- Verification: P-284–P-286 track probe lifecycle/details, live diagnostics and indicator gating/escalation. Focused tests cover malformed replies, timeout/late results, close/reopen continuity, statuses and visibility. Isolated browser acceptance exercises a real disabled backend probe plus controlled TLS/refusal/success replies and live-delta/reconnect continuity without contacting an external Relay.


### D-170 — AI keys and rebuild consent belong to the current Settings draft

- Date: 2026-09-25
- Scope: Settings / AI provider secret intent, save outcomes and embedding rebuild consent.
- Keys: Use write-only password drafts with explicit configured/new/clear status. Clearing a key removes its replacement draft; typing a replacement cancels the pending clear. Keeping the saved key cancels clearing without resurrecting discarded text. Blank means unchanged. Keep drafts and consent only in the mounted Settings owner, never shared response caches or browser storage.
- Acknowledgement: Clear key drafts and clear intents only after a typed AI settings acknowledgement. Malformed/refused outcomes retain drafts for explicit retry. Redact exact submitted keys and labeled credentials from displayed save errors. Preserve already acknowledged non-AI scopes so a later AI failure or confirmation does not replay their writes.
- Consent: When the saved index has entries and the AI draft changes model/corpus or has a rebuild warning, present inline confirm/cancel before any coordinated Settings write. Backend-requested consent belongs to the exact current AI draft and secret intent; changing either invalidates it. Cancel retains edits and sends nothing further. Only explicit confirmation sends the confirmation flag. Routine reconnects retain unchanged consent, fields and focus; successful acknowledgement or leaving Settings clears consent.
- Distinction: Classic's manual index-start action itself has no confirmation modal. Its job/dependency gates and outcomes remain separate from consent for rebuild-causing Settings changes.
- Verification: P-287/P-288 cover secret-intent precedence, redacted failure, typed acknowledgement, client/backend confirmation, cancellation and partial-save ownership. Isolated browser acceptance uses disabled AI for real dummy-key persistence and controlled embedding-change outcomes to avoid model downloads/provider execution.


### D-171 — AI diagnostics follow the live projection; index-start requests have an owner

- Date: 2026-09-25
- Scope: Settings / embedding dependencies, vector index, boot summaries, metering and index start.
- Diagnostics: Render the Classic dependency/runtime/package/install details and first-download guidance; index state, all six counts, active/desired models and dimensions, last-built/error, rebuild reason and current job; summary state/counts/refresh/error and four 24-hour metering counts plus last-call time. Use readable status labels, semantic severity, machine-readable timestamps and explicit missing values. Unknown states remain visible. Suppress stale summary errors when the current summary is ready.
- Projection: Full AI settings updates unwrap into the canonical AI settings object. Index and summary deltas merge only their live sections, including partial counts/rebuild metadata and companion embedding/metering fields, preserving unrelated settings. Explicit empty/null values clear obsolete errors/jobs. Live diagnostics update in the mounted form without replacing editor DOM, drafts or focus.
- Index start: Match Classic's incremental/rebuild selection and missing-dependency, building and queued/running-job gates. Starting is explicit and requires no extra confirmation beyond the button. A single app-session request prevents duplicates and survives closing/reopening Settings; reconnect never replays it. Bound observation to 30 seconds, report refusal or unknown outcome with live-status guidance, and permit only explicit retry when the live gate allows it.
- Ordering: A typed job acknowledgement confirms that the start was accepted. Adopt its diagnostic snapshot only if neither AI state nor the snapshot generation changed while pending; a late queued acknowledgement must not overwrite a newer running/completed job. Keep accepted-request feedback distinct from live job status.
- Verification: P-289–P-291 require projection/model/component coverage and isolated browser acceptance of nested/flat/full deltas, live diagnostic detail, draft/focus continuity, job gates, late acknowledgement, failure/retry and a real disabled-AI job without inference or downloads.


### D-172 — Settings appearance is a preview until Save; dirty state follows the draft

- Date: 2026-09-25
- Scope: Settings appearance and consistent unsaved-state feedback; supersedes the immediate-local-save exception in D-149 and restores D-034's lifecycle.
- Draft ownership: Settings owns appearance alongside its other drafts. Contrast, accent, density, scale, terminal font and motion controls preview document appearance without writing browser storage. Reset stages the default appearance. Accent buttons never submit the form. Appearance-only changes receive the same navigation protection and discard confirmation as runtime fields.
- Commit/discard: Commit appearance locally after all coordinated daemon settings writes succeed. Refusal or rebuild confirmation retains the preview and leaves persisted appearance unchanged. Storage failure is explicit, retains the draft and permits manual retry without replaying acknowledged daemon writes. Leaving/unmounting Settings restores the persisted appearance; successful saves become the new baseline.
- Dirty state: The unsaved caption, Save availability and navigation protection use the same actual pending-change predicate. Returning fields/appearance to the baseline clears ordinary edits. Staged section resets, explicit Relay overrides and AI clear intents remain pending until acknowledged or discarded. Submitting a clean form sends nothing.
- Verification: P-292/P-293 require component and isolated browser evidence for edit/revert, reset, explicit intents, preview/cancel/discard, acknowledged persistence, reconnect and failed-save retention. Existing terminal instances applying the previewed font are separately audited.


### D-173 — Terminal preferences update the existing xterm session

- Date: 2026-09-25
- Scope: embedded terminals, local appearance and global scrollback; P-294/P-295.
- Appearance: Initialize xterm from the owning document's effective terminal font, background, text and accent tokens, including selection color. Observe appearance style/contrast changes so preview, discard and saved changes update existing instances. Apply changed options only; color-only changes require no fit. Font changes use the existing fit path that preserves reading/tail intent and restricts geometry messages to the visible PTY owner.
- Scrollback: Match Classic/daemon normalization: finite numeric values are floored, 100–100,000 lines are accepted, invalid/out-of-range values use 2,000. Initialize from saved global settings, update existing instances on settings deltas and resync, and keep user drafts separate. Changing preferences must not remount xterm, recreate its socket, reset output or focus the terminal.
- Lifetime: Disconnect appearance observers with the controller. Hidden/inactive controllers can accept options but cannot claim PTY focus/resize ownership. Shrinking scrollback intentionally drops history beyond the new limit; surviving history retains xterm's viewport behavior.
- Verification: Model/controller tests, store-to-mounted-terminal checks and real-xterm isolated browser acceptance cover initialization, updates, normalization, reading/tail continuity, cleanup and reconnect. Native multi-window/cross-platform acceptance remains separate.


### D-174 — Mission Control owns its group summary and preserves inspection state

- Date: 2026-09-25
- Scope: Mission Control summary reads, cards/disclosures/details and dismissal; P-296–P-299.
- Reads: Use a bounded correlated HTTP read for the visible group, validating type/group/cards and aborting obsolete requests on scope changes or unmount. Retain the accepted current-group summary through refresh errors and reconnect, distinguish loading/disconnected/error from empty sections, and offer manual retry. Shared latest-response caches cannot replace the owned summary. No hidden panel requests run.
- Inspection: Keep search, section disclosure and selected card ID in the app session. Group changes clear summary/selection; refreshed objects preserve the selected ID while it exists. Render reported totals and truncation separately from filter results, and distinguish empty results from unmatched filtering. Disclosure and card inspection are keyboard-accessible controls.
- Context: Cards expose reference, severity/kind, owner, gate, reason, recommended next action, evidence/caveats, timestamps and source descriptors. The selected detail and source freshness/errors use readable fields, not raw JSON. Existing task/agent navigation remains available; source descriptors stay read-only.
- Dismissal: A single owned request per card reports pending/refusal/unknown outcomes and permits explicit retry. Accepted dismissal and persisted dismissal deltas remove cards, adjust cached totals once and clear selected detail. Refresh/reload cannot resurrect persisted dismissed cards. No reconnect replays mutations; observation ends on leaving the owner. Restore focus to the section toggle when removing the focused card without taking focus from another control.
- Verification: Focused model/component/App tests and isolated browser acceptance must cover wrong/late replies, refresh/reconnect/hidden behavior, filter/caret/disclosure/selection, readable metadata, actual dismissal persistence, external deltas, refusal/retry, desktop and narrow layouts. Supervisor/health remain independent owned surfaces; no live supervisor restart/termination is required for this repair.


### D-175 — Settings requests have bounded observation and validated outcomes

- Date: 2026-09-25
- Scope: initial/reconnect Settings snapshots and coordinated Save; P-300–P-302.
- Reads: Observe each settings read for at most 15 seconds. Timeout provides explicit retry and preserves the mounted draft, focus and caret. Initial loading becomes an actionable error. Validate the expected response type, settings object and group before publishing a coordinated snapshot. Abort obsolete requests on navigation, scope changes or save start; late responses cannot replace current state.
- Saves: Observe each sequential scope write for at most 30 seconds. A timeout means the outcome is unknown, not that the daemon rolled back. Release the saving/navigation guard, retain unfinished drafts and refresh current values without replaying mutations. Earlier acknowledged scopes remain acknowledged; explicit retry sends only remaining changes. The adapter settling after cancellation must not change success/error, clear secrets or advance the sequence.
- Acknowledgements: Accept the daemon's existing state/ok results for ordinary settings mutations and matching typed settings responses when supplied; reject unrelated frames and wrong-group typed responses before clearing drafts or advancing later writes. AI continues to require its typed settings acknowledgement and current-draft rebuild consent. Appearance commits only after all coordinated writes are acknowledged.
- Lifetime: Bound each owned request independently of transport cancellation. Clean up deadlines and parent-abort listeners on every outcome. Leaving a settings owner ends observation; it does not claim to cancel an already received daemon mutation.
- Verification: Component/request tests and isolated browser acceptance cover initial and reconnect timeouts, retained drafts/focus/caret, malformed/unrelated outcomes, partial acknowledgement, navigation recovery, late responses, manual retry and no reconnect replay. Broad field validation and runtime-effect acceptance remain separate.


### D-176 — Agent Settings bounds requests to one dialog target

- Date: 2026-09-25
- Scope: per-agent Settings initial/refresh reads and sequential saves; P-303/P-304.
- Owner: Key the editor to the selected agent ID so target replacement creates a fresh draft owner. Retain one AbortController for a save sequence; unmount/target replacement aborts its observation. Check ownership before and after every awaited write, before later commands and before closing. Late replies cannot update or close a different dialog or dispatch remaining digest/specialization/relaunch steps.
- Reads: Reuse the Settings 15-second observation deadline. An initial timeout retains identity drafts but prevents saving until hydration succeeds; a refresh timeout retains hydrated edits and provides explicit retry. Scope validation and existing three-way draft/inheritance reconciliation remain authoritative. Cleanup aborts obsolete transport observation and deadlines.
- Saves: Reuse the 30-second per-scope deadline. On timeout, report an unknown outcome and any earlier acknowledgements, release disabled controls, retain unfinished fields and relaunch intent, and refresh current values. Explicit retry sends only pending scopes; reconnect and late acknowledgements never replay mutations. Acknowledged fields can adopt subsequent external changes.
- Verification: Component tests must cover initial timeout/retry, partial-save timeout, unmount, target replacement, late responses and no stale follow-up/relaunch. Isolated browser acceptance must demonstrate retained focus/caret, actual partial-save persistence, external updates to acknowledged fields, reconnect without replay and reload.


### D-177 — Behavior drafts and proposals belong to a validated scope

- Date: 2026-09-25
- Scope: Dynamic Behavior editor reads, drafts, local preview and set-text proposal submission; P-305–P-308.
- Scope: Select a group role (Architect/Engineer/Worker) or a supported principal agent (Architect/Engineer). Fetch current text, versions and proposals with correlated bounded requests for that exact group/kind/target. Validate text/version responses, filter proposals by canonical scope and reject obsolete replies. Only the visible editor reads; reconnect and explicit refresh retain accepted content while requiring a fresh base before submission. Projected active-version changes invalidate the accepted base.
- Discovery: Preserve a separate explicit group approval-list read before a target is selected. Filter by group, retain existing review actions, and refresh an already-open list on reconnect; never use the aggregate list as an editor text source.
- Drafts: Keep text and rationale independently per group/kind/target in the app session. Seed only clean drafts from accepted text; preserve explicit empty edits and dirty drafts across refresh, target switches and panel closure. Normal updates retain input DOM, caret, focus and local preview disclosure.
- Preview: Render a local compact unified diff between accepted scoped text and the unsaved draft, including removal of all text and a no-change state. Preview sends no mutation. Historical and proposal review continue through their separate review dialogs.
- Submit: Use the accepted version ID as expected_base_version_id and send the exact text/rationale as a user-authored set-text proposal. Guard duplicate pending submissions, validate returned proposal identity/scope/base, show refusal/unknown outcomes and preserve drafts. Reuse an idempotency key only for explicit retries of the same scope/base/text/rationale. Reconnect never replays a mutation; leaving the owner ends observation. Acknowledged submission is distinct from approval and does not apply the overlay.
- Verification: Component and isolated browser evidence must cover scope changes, wrong/late replies, explicit clearing, retained drafts and focus/caret, hidden/reconnect behavior, preview without writes, stale-base protection, refusal/retry, idempotency and persisted proposal contents. Full timeline/provenance and rollback remain separately audited requirements.


### D-178 — Behavior history exposes provenance and requests reviewed rollback

- Date: 2026-09-25
- Scope: Behavior Overlay version timeline and historical comparison; P-309/P-310.
- History: Identify the accepted active version and render version numbers, complete IDs, author/approver identities, creation time, byte count, full SHA-256, parent/source proposal and rationale as labeled fields. Expand provenance on demand with stable version keys; missing values remain explicit. Long identifiers wrap at narrow widths.
- Comparison: Inspect a historical version against the accepted current base in a scoped modal. Bound and validate both version identities and scopes, refresh on reconnect/base changes, retain the previous diff and rationale while loading or on failure, and disable rollback until the current comparison succeeds. Active-version inspection cannot request rollback.
- Rollback: After reviewing the comparison, explicitly confirm a user-authored rollback proposal with target_version_id and expected_base_version_id. The existing daemon determines approval authority; this control never applies the overlay. Preserve rationale and same-signature idempotency tokens across close/reopen, distinguish unknown/refused/submitted outcomes, guard duplicate pending requests and never replay on reconnect. Closing ends observation without claiming to cancel a received daemon write.
- Verification: Component tests and isolated browser acceptance cover metadata, scoped diffs, stale bases, refusal/retry, timeouts, late replies, close/reopen, reconnect continuity and persisted rollback proposals requiring separate approval.


### D-179 — Proposal review bounds observation and states overlay application scope

- Date: 2026-09-25
- Scope: Behavior proposal review and overlay application guidance; P-311–P-313.
- Review: Key each dialog owner to its proposal. Read the exact proposal diff with a 15-second deadline, validate the response type/identity/base/hash, and retain the inspected comparison, note, focus and caret during refresh or failure. Disable decisions until a current connected read succeeds. Reconnect refreshes only visible, idle review; it never replays a decision.
- Decision: Approve/reject carries the reviewed proposal hash and base. Retain one mutation controller, bound its observation to 30 seconds, and end observation on leaving/replacing the dialog. Validate the acknowledged proposal, scope, base, hash and terminal status before reporting success. On refusal or unknown outcome, unlock controls and retain the note/comparison; require a fresh explicit review before retrying. Closing is always available and does not claim to cancel a received daemon mutation.
- Guidance: Explain that role overlays apply group-wide, require user review and take effect on the next worker dispatch or principal launch/relaunch; an agent overlay applies only to that agent at its next launch/relaunch. Proposal cards expose change type, approval route, text bytes, full hash and advisory warning count before opening the detailed diff. Missing metadata is explicit.
- Verification: Reproduce timeout, replaced-owner and malformed-acknowledgement defects before fixing. Component and isolated browser acceptance cover retained review state, stale decisions, bounded recovery, late replies, no replay, actual persisted approval/rejection and scope guidance.


### D-180 — Catalog authoring bounds reads, previews and mutation observation

- Date: 2026-09-26
- Scope: Role/template/specialization libraries, Actions and Agent Classes; P-314–P-320.
- Reads: Reuse the shared 15-second observation deadline for listings, selected definitions, role choices, variable discovery, prompt rendering and class validation. Preserve accepted content and edited fields while surfacing an actionable timeout. Explicit retry and active reconnect can refresh the current owner; obsolete, hidden or cancelled results cannot replace it.
- Writes: Observe catalog/action save/delete and class create/update/archive/delete for at most 30 seconds. Unknown outcomes release busy controls, retain drafts and confirmation, and allow ordinary read refresh to establish current data. Retry is always an explicit operator action; neither timeout nor reconnect replays a mutation. A late transport settlement cannot publish success, navigate selection or clear a newer draft.
- Selection: Bind the initially displayed Agent Class to its stable ID. Catalog reorder/insertion on refresh cannot replace its editor or draft. Explicit selection and acknowledged mutations remain the selection-changing paths.
- Draft reconciliation: Preserve the existing three-way merge of untouched server fields and local edits, stable input DOM/caret/disclosure, and acknowledged original name/scope through rename or scope changes. Newly discovered persisted data is distinct from a mutation acknowledgement.
- Verification: Reproduce stalled requests before repair. Focused tests cover all three catalog kinds, action preview, class validation, mutation types, late replies and unmount; isolated production browser scenarios verify real deadlines, draft continuity, explicit retry and persisted results.


### D-181 — Shared specialization discovery has bounded recovery

- Date: 2026-09-26
- Scope: D-141's shared picker in Engineer creation/hiring, per-agent settings and group defaults; P-321.
- Discovery: Keep on-demand loading and group-scoped reconnect refresh. Bound each read to 15 seconds independently of transport cancellation. A stalled discovery exposes an actionable error and re-enables Refresh specializations.
- Continuity: Preserve accepted options, exact ordered selections including unavailable slugs, manual text, input DOM, focus and caret through timeout. Do not infer an empty catalog from a failed read. Late responses cannot replace an accepted retry, and unmount/disable/group change ends observation.
- Verification: Reproduce stalled initial/reconnect reads, then verify explicit retry, stale-result rejection and hidden cancellation. Exercise the shared control in production browser creation, per-agent settings and group defaults, with persisted ordering through reload.


### D-182 — Operational panels bound visible reads and Context mutations

- Date: 2026-09-26
- Scope: Pipeline discovery, History list/detail, Help topics/document/search/query, Context list/publish/pin/unpin and Supervisor polling; P-322–P-327.
- Read recovery: Observe each read for at most 15 seconds independently of transport cancellation. A timeout retains the last accepted graph, article, run, entries or sessions and exposes retry. Preserve selection, disclosure, view position, focus/caret and unsubmitted drafts. Independently owned requests settle independently; late results cannot replace current content.
- Polling: A Supervisor timeout is a failed observation, not an empty session list. Retain accepted rows and resume the configured polling cadence after the failure; pausing, replacing or hiding the view cancels pending observation and future polls.
- Context writes: Bound mutation observation to 30 seconds. An unacknowledged outcome releases busy controls and retains the editor/intent; ordinary scoped refresh may reveal persisted changes. Never replay a mutation on timeout or reconnect, and never let a late response close the editor or erase newer edits. Explicit publication of a new entry after an unknown outcome requires reviewing refreshed entries first; the UI does not claim backend deduplication.
- Verification: Reproduce stalls before repair, then test initial load, accepted-state retention, independent retries, delayed settlements, polling recovery and cancellation. Isolated production browser acceptance must exercise actual deadlines and persisted Context outcomes alongside existing panel lifecycle regressions.


### D-183 — Context pane-width persistence recovers without draining a failed queue

- Date: 2026-09-26
- Scope: D-115 Context split persistence; P-328.
- Bound observation: Observe each pane-width write for at most 30 seconds and cancel observation when hidden. A missing acknowledgement means an unknown outcome, not proof that the daemon did not persist it. Release the saving indicator while retaining the latest visible ratio and editor state.
- Queue: Continue coalescing rapid commits after successful acknowledgements. After refusal or timeout, stop and clear the queued writes while retaining the latest local ratio as dirty. Later snapshots cannot overwrite that intent. Retry pane width or another explicit resize submits the current intent; reconnect and late replies do not replay it or clear the error.
- Verification: Reproduce stalled saving and queue continuation after refusal. Component/browser acceptance covers current ratio and draft/caret retention, actual persisted-but-withheld acknowledgement, explicit retry, ordinary successful coalescing, compact transitions, reload and hidden cancellation.


### D-184 — Compatibility-only automatic terminal counts are not editable Settings

- Date: 2026-09-26
- Scope: Group Settings auto_terminals; P-329.
- Contract: Classic no longer exposes this field and agent launch explicitly ignores it as an implicit companion-terminal fallback. Keep the persisted field for profile compatibility, but exclude it from React's editable settings, search controls, changed-value payloads and section resets. Do not silently reset or migrate its stored value.
- Explicit companions: Role/template/action launch definitions retain their explicit terminals configuration and supported creation behavior. Removing an inert group control does not retire companion terminals.
- Verification: Reproduce the exposed field and reset contamination, then verify mounted Settings and helper behavior, sparse save and whole-group reset through the daemon, reload persistence, and the existing backend explicit-companion/no-fallback guard tests.


### D-185 — Settings text normalization happens at the save boundary

- Date: 2026-09-26
- Scope: Classic `_collectGlobalSettingsPayload` / `submitGroupSettings` text contracts; P-330.
- Contract: Preserve exact local editing buffers. Normalize only explicitly submitted, known fields using Classic's outer-whitespace rules for commands, model/reasoning overrides, paths and task defaults. Keep internal spaces. Empty worktree base directory, dispatch lane and journal frequency use Classic's documented defaults; other empty text retains inheritance semantics. Trim individual task-label and full-capture-tool list entries. Normalize named GitHub text fields without promoting absent defaults.
- Boundaries: Do not generically trim every string or recurse through arbitrary maps. Instructions, environment values, map keys/values and unknown fields remain exact in client payloads. Existing backend normalizers retain their authority, including Architect instruction normalization.
- Lifecycle: Refusal preserves the exact authored draft. Sparse writes and partial acknowledgements remain scoped; retry does not replay a previously acknowledged scope. Successful rereads/reload display authoritative stored values.
- Verification: Mounted regression reproduces the untrimmed path payload. Model tests cover all four scopes, explicit empty defaults, unchanged inputs and protected text/maps; production acceptance exercises four-scope partial failure, payloads, persistence and reload.


### D-186 — Ownership-tree terminal activation is explicit and scoped

- Date: 2026-09-26
- Scope: Agent/terminal ownership rows, Live/Activity, focus_on_click and the visible xterm owner; P-331/P-332/P-333.
- Activation: Double-clicking a row or pressing Enter selects its agent, opens Live, sends the canonical focus_agent command and places keyboard input into that row's visible terminal. The same action works when the row was already selected. Embedded controls such as collapse/menu buttons do not trigger double-click activation.
- Selection: Ordinary selection retains Activity unless the existing Focus on click preference is enabled (standalone terminals continue to open Live). Keyboard focus and arrow traversal alone select no PTY. React applies live preference changes; no restart is required. This retains the ownership tree's inspection behavior while making the advertised preference effective.
- Ownership: Local terminal focus is a one-shot request tied to the selected cell and session. Consume it after visible layout; cancel scheduled work on hide/unmount. Reconnect, routine data updates and later Live/Activity toggles do not replay consumed focus intent or steal focus from a draft. The terminal controller's visible/active ownership guard remains authoritative.
- Verification: Reproduce missing double-click activation and unused preference. Component coverage checks Enter/Activity, live preference changes, traversal, one-shot focus and cancellation. Browser acceptance uses independent real PTY receivers to verify exact input target and retained composer state across activation/reconnect.

- PTY handoff: Each cell/session receives a distinct terminal mount. Switching or restarting detaches the previous DOM surface immediately, even while its Strict Mode lease waits for cleanup, so that controller fails the visible-owner guard and cannot share the new terminal container. Ordinary same-session rerenders retain the mount and scrollback. Old-session activation intent cannot focus a replacement session.


### D-187 — Planning editors bound and own request observation

- Date: 2026-09-26
- Status: accepted
- Scope: Scratchpad, Idea Brief, Initiative, Decision and Area details; Planning create/edit/link/lifecycle operations; Initiative task-option discovery and its separate task-link acknowledgement.
- Decision: Editor detail and task-option reads have a 15-second observation deadline; writes have a 30-second deadline. A timeout releases controls with a retry path and retains the current record, local drafts and reading state. Closing or replacing an editor cancels its observation; late responses cannot apply state, clear drafts, close another dialog or continue a dependent lifecycle sequence.
- Constraints: A write is accepted only when its response matches the existing daemon's command-specific type and available record, owner, target and relation identity. Unknown outcomes are shown explicitly and are never replayed on reconnect. An acknowledged edit is removed from the pending edit set before a dependent archive, so retry cannot overwrite intervening external changes. A created Board task keeps its known ID until its link is confirmed; link retry reuses that ID even when the original link already persisted. Cancellation stops client observation and does not claim to reverse server work.
- Verification: P-334–P-337 track component regressions and real-deadline browser acceptance, including retained input identity/focus/caret, malformed responses, unmount cancellation, partial archive recovery and exactly one Board task across link recovery. The top-level Planning collection timer remains independent.

### D-188 — Board activity and prompt-preview read recovery

- Date: 2026-09-26
- Status: accepted
- Scope: Compact task activity hydration and task-authoring prompt preview.
- Decision: Both reads stop waiting after 15 seconds, expose an explicit retry and cancel observation on unmount. Hidden activity cancels its read; a changed task gets a fresh history owner. Activity validates the response type and task identity before showing rows. Unloaded history has a loading state instead of claiming that no activity exists.
- Retention: A refresh failure retains loaded messages, the current reading window and the surrounding task draft. New messages remain behind the existing Show new messages action. Preview results remain tied to the exact draft inputs that produced them; late or cancelled replies cannot replace a successful retry. Reads never save task changes.
- Verification: Component regressions and isolated production-browser checks exercise real deadlines, wrong task identity, late replies, retry, hidden/unmounted cancellation and retained DOM/focus/caret/reading position. Board creation, edits, upload cleanup and bulk archive require their separate mutation recovery acceptance.

### D-189 — Dialog titles remain accessible through content updates

- Date: 2026-09-26
- Status: accepted
- Scope: Shared ModalDialog, including Board activity/task editing.
- Decision: The dialog explicitly uses its visible title as its accessible label. A child draft update must not make the dialog unnamed when the component library's generated heading association changes. Keep the visible heading, focus containment and mounted draft unchanged; title changes update the accessible name too.
- Evidence: The Board activity browser scenario initially found the named dialog, then lost that accessible name immediately after filling Description while the heading, dialog and focused draft remained visible. The trace and snapshot are preserved in `/private/tmp/board-read-browser-initial-evidence-20260926`. Acceptance retains the named-dialog locator through real timeout/retry and draft edits rather than weakening it to an unnamed selector.

### D-190 — Board entry and archive observation have explicit recovery

- Date: 2026-09-26
- Status: accepted
- Scope: Initial and reconnect full-task hydration; inactive completed-task batch archive.
- Decision: Full task details use a correlated 15-second HTTP read, validating type, task ID, group and complete description before projection. Error/timeout exposes Retry task details; an already mounted task editor retains its draft and DOM while reconnect refresh fails. Closing or changing the target cancels observation and rejects late replies. Catalog discovery remains a separate request lifecycle.
- Archive: Each explicit batch snapshots the currently eligible task IDs and owns a 30-second observation deadline. Unknown outcomes release controls with review guidance and never imply rollback. Explicit retry recalculates eligibility from current Board state; reconnect does not replay the write. Switching groups or unmounting cancels observation and suppresses obsolete success callbacks. The existing success-toast acknowledgement remains the daemon contract.
- Verification: Reproduce initial-loading hangs, stale target acceptance and unbounded/unowned archive observation. Component and production-browser tests exercise actual deadlines, malformed detail identity, target/group replacement, reconnect with retained input identity/focus/caret, current-eligibility retry, ignored late replies and actual task persistence.
- Layout: A retained task editor shares the available dialog height with its refresh-error banner. Save/Cancel remain visible at ordinary and compact viewport sizes while the detail sections scroll; recovery feedback must not push the fixed task footer outside the dialog.

### D-191 — Task edits and evidence cleanup retain operation ownership

- Date: 2026-09-26
- Status: accepted
- Scope: Existing-task save, file upload, removal and Cancel cleanup.
- Decision: Each serialized operation owns a cancellation controller. Every command, upload response body and local evidence preparation has a 30-second observation deadline. Verify ownership after each await before projecting evidence, starting dependent cleanup, clearing drafts or closing the editor. Target replacement/unmount aborts observation and cannot affect the replacement task.
- Acknowledgements: Existing edit/removal commands return a full state snapshot; accept only a finite sequence with the addressed task present under its ID. Keep live deltas authoritative instead of applying the entire write snapshot. Upload metadata must contain nonempty filenames and paths owned by the requested task. Invalid responses retain drafts and identify the outcome as unknown.
- Recovery: Retain accepted uploads after a later upload fails. Preserve the acknowledged edit baseline before attachment cleanup so retry only repeats unacknowledged cleanup, including after external edits or reconnect. Never automatically replay writes or claim cancellation rolls back server changes. Only known, uncommitted file names are removed on Cancel; an upload whose server outcome is unknown is not proof that no file was stored.
- Discard intent: Once Cancel starts deleting new uploads, lock task fields and Save until explicit Cancel retry acknowledges cleanup. A lost cleanup response may follow deletion; never allow the retained display to recreate a reference to a missing file.
- Unknown save outcomes: Close preserves uploads whenever an edit may have committed without an acknowledgement. Reverting the draft to its old baseline does not prove rollback. Even after a later successful retry, preserve uploaded filenames that an older uncertain write could still reference; this can leave unreferenced files, but prevents deleting committed evidence.
- Verification: Reproduce timeout, wrong-target acknowledgement and obsolete-owner failures. Component and production-browser coverage checks real save/upload/cleanup deadlines, bounded response bodies, partial-save retry, retained evidence, ignored late results, target replacement, no reconnect replay and persistent task/file outcomes.


### D-192 — Recover creation with the exact submitted intent

- Task creation observes writes, upload bodies, evidence preparation, draft-file removal and draft-directory cleanup with explicit 30-second deadlines. The mounted dialog owns each sequence; unmount/group replacement aborts observation and late results cannot start linking, update evidence or close another surface.
- Before sending creation, freeze its reviewed payload with a dedicated idempotency key distinct from the draft upload token. Unknown transport/server outcomes and malformed acknowledgements keep that exact payload and key. Retry creation recovers the same operation; it never silently submits changed fields. Reconnect does not replay creation.
- While creation is uncertain, retain fields/uploads and require explicit recovery before discarding them. An explicit `creation_refused` acknowledgement from the pre-creation validation path allows correcting the draft under a new key; key conflicts and server failures retain uncertainty. Accept only the existing creation response types, a nonempty task ID and the submitted title. Retain an acknowledged ID through subsequent link failures.
- HTTP Board creation now shares pending operations by key and payload fingerprint, extending the existing worktree coordinator. Matching concurrent callers join the same operation; a changed payload is rejected, and a disconnected waiter cannot cancel the write. Existing SQLite/critical command receipts handle completed replay. This is not a blanket exactly-once guarantee for arbitrary commands, file effects or daemon crashes.
- Draft-file removal validates the correlated full-state acknowledgement, since draft tokens have no Board record. Cleanup and upload errors retain the reviewed evidence until acknowledged retry; unknown uploads are not represented as server rollback.
- Acceptance requires component deadlines/unmount/refusal/acknowledgement regressions, route-level concurrent-request and SQLite-cache tests, production creation/upload/cleanup recovery and the full backend regression suite.
- Creation dialogs use the fitted modal body with an independently scrolling field/preview region. Recovery errors and Create/Retry/Cancel actions stay visible through expanded evidence, uncertain outcomes and compact-window resizing; retain the same draft nodes and reading position.
- Once the user starts discarding staged uploads, keep that discard intent through an unknown cleanup outcome. Retain the displayed draft but disable creation and evidence edits; only explicit cleanup retry may finish closing, since the files may already have been removed.

- Pending single-file removal also blocks creation and evidence edits until explicit Retry removal acknowledges it. Cancel may discard the entire draft. The retained row is an unresolved operation, not proof that the file still exists.


### D-193 — Schedule authoring and lifecycle parity

- Date: 2026-09-26
- Scope: Board schedule listing, authoring, execution and removal; P-043/P-346–P-350 and the schedule portion of P-345.
- The list spans all groups, sorts by name, and has an explicit group filter. Show the slug/group/enabled state, task template, action/role, trigger/timezone, next run and run count. Format trigger and next-run timestamps in the displayed schedule timezone (UTC by default), with an explicit timezone suffix; invalid legacy timezone values fall back to the original ISO instant. Keep keyed cards mounted through routine updates.
- Author recurring and one-time triggers with explicit modes and six Classic cron presets. Keep both local mode drafts, but send an empty inactive trigger. One-time inputs use the browser local timezone and preserve the original ISO instant, including seconds, when unchanged. The timezone field supplies cron scheduling.
- Group reassignment requests that group's action and role catalogs. Named variables exclude TASK/torque, support defaults and retain per-action drafts; Advanced variables remains available for structured values. Represent unavailable saved selections explicitly.
- Visible schedules own bounded 15-second catalog/list reads; reconnect refresh retains accepted choices, editor nodes and drafts. Hidden schedules issue no reads. Invalid, late, cancelled or wrong-group catalog replies cannot replace accepted options. Each failure has an explicit retry.
- Schedule mutations have a retained owner and 30-second observation deadline, validated command-specific acknowledgements, and no reconnect replay. Keep failed edits. Creation freezes its reviewed payload/key on an unknown outcome; explicit retry shares the same pending/completed HTTP operation. Until acknowledged/refused, keep its editor mounted and prevent closing or switching schedules. This does not certify process-crash exactly-once behavior.
- Removal uses a custom confirmation with captured schedule name/group/ID. Live list updates cannot retarget the confirmation; cancellation sends no write.
- Run now uses injected dispatch/event callbacks and records the resulting task, timestamp and run count. Validate with an isolated generic worker, not a commercial provider invocation.

### D-194 — Group-owned Board authoring options

- Date: 2026-09-26
- Scope: P-345; task creation, existing-task Execution, batch editing and Initiative-to-Board creation, sharing the schedule catalog contract from D-193.
- Each visible authoring surface owns its action/role reads and accepted options. Never derive these choices from the most recent global WebSocket catalog. Existing-task options follow the editable target group; batch editing discovers actions on its first open and does not request unused roles.
- Bound each catalog read at 15 seconds. Reconnect and explicit retry refresh the current group while preserving accepted options, variable drafts, the editor node, focus and caret. Report loading and errors next to the authoring controls with an explicit Retry task options action.
- Validate response type, declared group, container and entry identities before replacing accepted options. Late, cancelled, malformed and unrelated responses cannot replace them. Abort reads when the dialog closes, the target group changes or the Execution tab becomes hidden; hidden authoring surfaces issue no reconnect reads.
- Keep selected action/role values explicit as unavailable when absent from the current catalog. A refresh or target-group change must not silently substitute a default or erase the authored variable payload.
- This changes option discovery only. Existing acknowledged creation/edit/link ownership and unknown-outcome evidence retention remain governed by D-191/D-192.

### D-195 — Artifact preview classification and recovery

- Date: 2026-09-26
- Scope: P-351/P-352; Board creation/edit evidence previews and the P-112 recovery audit. D-106's inline-draft precedence and escaped text remain authoritative.
- Recognize Classic image/text extensions from filename, path or title and normalize MIME/type casing. Read legacy nested storage content/path. Distinguish inline-only storage and external file references from actual task uploads; uploaded absolute filesystem paths resolve through the attachment endpoint rather than being used as browser URLs. Preserve explicitly supplied safe download URLs on external or inline references.
- Bound file response/body and image loading observation at 15 seconds. Show an explicit error and retry after failure or timeout. Ignore late results after expiry, target replacement or closure. A visible file preview refreshes after reconnect; hidden previews issue no reads.
- Keep accepted text mounted and readable through reconnect and failed refresh. Retain text selection/focus and the enclosing task draft. An empty successful file is explicitly empty; do not substitute summary/path metadata for its content. Different preview identities cannot inherit each other's content.
- Keep refresh/retry controls mounted through loading and success. Their labels may change, but their DOM/focus identity must survive so Escape still dismisses the nested preview and leaves its parent editor open. Image retries replace only the image request, not the focused control.
- Preserve image decoding, source-task downloads, path copying, line metadata and nested Escape behavior. This checkpoint does not certify large-file performance, external-host availability or native lifecycle acceptance.

### D-196 — Attention question and delivery recovery

- Date: 2026-09-26
- Scope: P-353/P-354; Attention ask responses within the broad P-112 recovery audit. Classic target selection and acknowledged answer clearing remain required.
- Visible open questions own question and parent-detail reads with 15-second observation limits. Validate task identity and full question lifecycle state before projecting a response. Closing, removing or replacing the question cancels its reads and delivery observation; hidden closed questions issue no reconnect reads.
- Keep accepted question/parent prose readable through compact snapshots and failed refreshes. Preserve the answer editor, draft, focus and caret through ordinary refresh/reconnect; retain parent context only for its matching parent identity.
- Bound answer-delivery observation at 30 seconds and validate command/request correlation, any returned task/recipient IDs, and delivery state. Late or cancelled results cannot clear a draft or affect another question. An ordinary explicit refusal retains an editable draft.
- An unknown delivery outcome freezes the exact submitted command and reply target. A read started after uncertainty must complete successfully before explicit retry; a read already in flight is insufficient. Reconnect may perform that read but never resends the answer. Retry reuses the reviewed payload; a changed reply target blocks retry and explains that the original delivery needs inspection.
- The backend serializes resolution by task and rejects already closed questions. The request ID correlates responses; it is not a durable delivery idempotency receipt. Concurrent/replayed live delivery is tested, but process-crash exactly-once delivery is not certified.

### D-197 — Creation discovery observation limits

- Date: 2026-09-26
- Scope: P-355; existing owned Agent Class discovery and worker launch-default resolution. Role/template option ownership and launch-write recovery are separate open requirements P-356/P-357.
- Bound each class/default read at 15 seconds independently of transport cancellation. Expose the existing explicit retry on timeout; reject late results from expired, replaced or closed owners. Resolve launch defaults only while connected and visible.
- Preserve selected classes, accepted class options, explicit launch-field overrides and the editor's focus/caret through refresh and retry. A selected class cannot launch until revalidated; an unavailable explicit selection cannot silently become a default launch.
- Reconnect refreshes accepted discovery and defaults without replaying creation. Closing cancels unfinished observations and stops hidden reconnect reads. Completed transports need no artificial abort; regression tests assert cancellation on still-pending requests.
- This read repair does not change launch mutation semantics or make overlapping creation retries safe. Completed HTTP receipts are distinct from coordination of pending writes; P-357 must establish both before enabling timeout retries.

### D-198 — Coordinate pending keyed launches

- Date: 2026-09-26
- Scope: backend portion of P-357; explicitly keyed HTTP add_worker/add_engineer/add_architect/add_terminal/create_agent_from_class/architect_engineer_hire commands.
- A matching retry must join the same pending command operation before its completed receipt exists. Cancelling one HTTP waiter does not cancel that shared operation. Reject a changed payload under the same key while pending as well as after completion; return a distinct response object to each caller.
- Reuse existing command hashing, pending-write coordination and persisted successful receipts. This preserves the original launch result, including a pending hire acknowledgement, rather than inventing a second target during an overlap.
- This checkpoint does not add UI timeout retries, claim crash-safe exactly-once execution, or resolve errors after partial target creation. P-357 stays open until bounded owned UI observation, frozen reviewed payloads, known refusal versus uncertain outcome handling and live recovery acceptance are complete.

### D-199 — Group-owned creation roles

- Date: 2026-09-26
- Scope: P-356; Worker role/template choices in AgentCreateDialog. Class discovery and resolved launch fields retain D-197's ownership and observation limits.
- Request list_roles for the dialog's captured creation group through an owned HTTP read. list_templates is a compatibility alias of the same role-manager list; do not combine competing global projections or duplicate alias requests. Project roles precede shadowed user roles, with one option per identity.
- Only visible Worker controls request these options. Pause discovery while creating, cancel on closure/kind change, and issue no hidden reconnect reads. SpecializationPicker owns its own discovery; the dialog must not send unused background specialization requests.
- Bound observation at 15 seconds. Validate response type, exact group, collection and entry names; retain accepted options, selected role, explicit launch fields and editor focus/caret through failures and reconnect. Late/foreign/malformed responses cannot replace accepted choices. Provide explicit refresh/retry beside the picker.
- Preserve a removed selection as an unavailable option and block its launch until it is available and revalidated or the user explicitly chooses another role/default. Do not show a new verification warning while a reviewed launch is pending merely because discovery is paused.
- These rules govern option discovery only. P-357 remains open for owned launch observation and uncertain/post-creation outcomes.


### D-200 — Recover uncertain and incomplete creation

- Date: 2026-09-26
- Scope: P-357; the six keyed React creation commands, AgentCreateDialog, and workspace navigation while a reviewed launch is unresolved.
- Observe delivery for at most 30 seconds independently of transport cancellation. Freeze the reviewed command, kind, idempotency key and fields until a matching outcome or an explicit verified refusal arrives. Reconnect never replays creation. Only an explicit retry resends the identical command; late, cancelled or mismatched acknowledgements cannot close the dialog or select a target.
- Distinguish verified refusal from uncertainty. Release the draft only when the daemon proves no target was allocated or verifies its removal from both memory and persistence. HTTP conflicts, transport failures, verification failures and server errors keep the reviewed request. A corrected draft after a verified refusal receives a new key.
- Capture exact target allocation within the owning asynchronous launch, before state or hire persistence mutation. Do not infer ownership from names or global snapshot differences. A failed launch with a surviving target returns a correlated creation_incomplete result containing its exact ID. Retain completed operations in memory through receipt-save or outcome-verification failures so retry cannot rerun their handler. Existing durable receipts cover later successful replay.
- An incomplete agent requires deliberate Inspect created target; a saved hire displays its exact identity and can be retained for review in Planning without claiming an Engineer exists. Neither case automatically reports successful launch. Locked Create and Cancel controls must also look disabled.
- Use the existing workspace navigation protection with creation-specific text. Command-navigator panel/group changes cannot discard the unresolved request. Return to creation keeps recovery active; closing a recovered owner clears queued navigation protection. Existing Settings confirmation behavior remains unchanged.
- These guarantees apply to the live daemon and owned UI session. Process-local unsettled outcomes are not crash-safe transactions, and forced page/process reload does not persist the dialog draft. Independent crash/restart/native recovery acceptance remains required before Classic retirement.


### D-201 — Settle GitHub check deadlines independently

- Date: 2026-09-26
- Scope: P-358; draft-only GitHub project discovery, connection checks and repository detection in Settings.
- Keep the existing 30-second observation limit, but settle the UI and release the check controls at that deadline independently of whether fetch honors cancellation. Retain accepted project choices, selected identity and all unsaved settings.
- An expired, replaced, hidden or wrong-group response cannot apply repository/project/lane suggestions or replace the accepted project list. In particular, completion of an old request must not clear a newer request's pending state. Retry remains an explicit check of the current draft; no timeout writes settings or replays on reconnect.
- Preserve editor focus/caret and existing non-empty lane mappings. Save remains the separate acknowledged persistence boundary. Controlled read-only gh fixtures verify this UI contract without claiming live external GitHub acceptance.


### D-202 — Retain bounded loop-cancellation observation

- Date: 2026-09-26
- Scope: P-359; AgentMessageLoop cancellation delivery and retained composer state.
- The shared composer store owns a started cancellation for its captured agent and exact loop ID. Changing selection or unmounting that panel must not lose its pending/result state or affect the unrelated draft. Bound observation at 30 seconds independently of transport cancellation, settle timeout into an unknown-outcome error, and leave explicit retry available even if the projection already shows the loop stopped.
- Retry the identical guarded command and idempotency key. Reconnect and returning to the agent do not replay the write. Expired replies cannot settle a newer retry. Continue requiring the matching cancelled-loop acknowledgement and audit identity before reporting this request as confirmed.
- Keep the existing displayed-loop backend guard: a replacement loop is never cancelled by an old request. This UI deadline does not establish crash-safe receipt recovery or change ordinary message, upload or turn-cancellation delivery semantics.


### D-203 — Coordinate terminal effects and acknowledge actual delivery

- Date: 2026-09-26
- Scope: P-360/P-362; keyed HTTP terminal submission and cancellation of a submitted user DM turn.
- Matching overlapping requests share one shielded backend operation. Losing a caller must not cancel its delivery. A completed handler acknowledgement survives receipt-save failure in memory until exact retry persists it; changed payloads cannot take over that retained result. Keep existing durable replay after receipt persistence and distinct execution for distinct message keys.
- Treat an explicit False from the terminal adapter as a verified unavailable-session failure. Restore optimistic running state under the existing freshness guards, record no sent history or success receipt, and let the composer retain its draft through the existing failure path.
- These changes do not certify handler-error or process-crash recovery. In particular, an error after input or interrupt delivery is not evidence that nothing happened. P-361 retains the requirement for frozen submitted intent, bounded UI observation and truthful recovery; do not add automatic retries or claim an uncertain interrupt left a turn unchanged.

### D-204 — Recover uncertain composer delivery with its original intent

- Date: 2026-09-26
- Scope: P-361; terminal input, direct-message submission and cancellation of an acknowledged user turn.
- The shared composer store owns each submitted command across selection changes and unmounts. Bound submission and cancellation observation at 30 seconds independently of transport abort. An uncertain submission freezes its exact message, attachment paths, reply, target, session and idempotency key; explicit Retry delivery recovers that request. Returning to a cell or reconnecting never resends it. Expired responses cannot settle a newer attempt or clear another draft.
- Only an error explicitly confirming no attempted delivery, correlated to the exact command and key, releases the draft for editing. Contradictory refusal/uncertainty flags remain uncertain. Review delivery explains that the message may already have been sent; Keep recovering preserves the frozen intent, while I checked; keep draft deliberately releases it without claiming rollback. Sending the retained draft afterward starts a new request and may duplicate earlier delivery.
- Retain the captured cancellation session/source turn/key through its deadline. Apply its result only to that turn. An older attached-composer delivery receipt must not replace a newer parent submission's cancellable turn. Successful recovery still clears only the original composer draft.
- Keyed HTTP delivery errors after possible effects retain an uncertain receipt, including across receipt-save failures. Exact retry recovers the outcome without repeating the handler; persisted receipts survive route reconstruction. Verified no-input preflight failures remain retryable. A partial interrupt reports an unknown outcome instead of claiming the turn was left unchanged.
- This contract does not establish exactly-once delivery across a daemon crash before receipt persistence, forced-page-reload draft retention, upload recovery or commercial-provider interruption. Those remain separate acceptance work.


### D-205 — Publish direct state changes before acknowledging the command

- Date: 2026-09-26
- Scope: P-054/P-080–P-083/P-112; Planning mutations, Agent Class assignment/clear, Engineer specialization selection and persisted Relay credentials through the direct command path.
- A successful direct state mutation must publish its queued deltas before returning its acknowledgement. Other open clients and clean editor fields must receive the change without waiting for unrelated agent activity or periodic runtime updates. Preserve dirty fields through the existing editor ownership rules.
- Keep explicit state-mutation manifests beside their domain commands. Read-only list/show/status commands must not flush unrelated pending changes. File-only catalog authoring does not gain a synthetic state change. Credential storage failures must not emit optimistic settings; class assignment changes desired authority without changing the effective launch snapshot. This restores the existing WebSocket update contract; it does not add durable delta replay or certify process-crash recovery.


### D-206 — Bound composer uploads without releasing their source ownership

- Date: 2026-09-26
- Scope: P-363/P-112; inline image selection, paste and drop in the message composer.
- Bound each upload at 30 seconds across both request and response-body reading, independently of transport abort. The source draft owns that deadline across navigation and unmounting. Expiry releases attachment/send controls while preserving text, reply, selection, accepted images, native composition and undo history; expose an inline instruction to attach again explicitly.
- Validate complete upload acknowledgements before accepting tokens or creating preview URLs. An expired acknowledgement cannot add an image, replace a newer upload's anchor or unlock its controls. Reconnect and returning to a composer do not resend uploads. Existing successful source-cell and IME-deferred insertion behavior remains intact.
- Uploading stores files; it does not submit a message. This deadline does not prove an unacknowledged file was never saved, provide an idempotent upload receipt, or delete unacknowledged files. Only an explicit Send submits the currently accepted image paths. Raw terminal drops have a separate owner/failure contract and remain under audit.


### D-207 — Recover terminal image drops within the original active pane

- Date: 2026-09-26
- Scope: P-364, raw image drops into xterm.
- Upload images independently and keep successful paths in drop order, shell-quoted. A failed file must not discard another successful image; an empty result must send no terminal input. Reject malformed and control-character paths.
- Bound each request and body observation to 30 seconds independently of abort support. Show pending feedback, then identify failed filenames and instruct the user to drop only failed images again. Preserve the usable terminal and Tail control; keep long feedback scrollable at compact sizes. Do not retry automatically or imply an expired upload saved no file.
- Bind results to the original active controller lease, not merely a reusable controller instance or session ID. Leaving, replacing or unmounting the pane cancels its observation. Old completions must never paste or steal focus after return. Restore terminal focus only for the original active pane.


### D-208 — Bound workspace saves without reverting newer navigation

- Date: 2026-09-26
- Scope: P-365; main-window active panel and Control tab persistence.
- Retain visible navigation during a request/body stall. At 30 seconds release save observation independently of transport abort, expose the existing Retry workspace save action, and permit the newest queued navigation to save. Do not replay on unrelated state updates or let an expired acknowledgement settle a newer request.
- Bind each window lifetime to a random writer ID and each local navigation intent to a positive increasing revision. Exact retries reuse both. Persist the highest accepted revision and its preference atomically with the preference; reject older revisions or changed payloads reusing a revision. An exact replay acknowledges its original result without overriding a later save from another window.
- Serialize durable saves and publication through a daemon-owned task that survives caller cancellation. Keep unversioned Classic/API callers compatible. Fresh windows restore the current saved preference; active windows retain their local navigation when another window saves. Detached windows continue to own their own navigation.
- Durable receipts survive daemon restart and full snapshots. They retain one small record per window lifetime, not one per click or retry; do not expire them while arbitrarily delayed requests could still arrive. Window IDs and revision metadata are transport/persistence details, not product controls.


### D-209 — Retain ownership-tree disclosure during workspace navigation

- Date: 2026-09-26
- Scope: P-366; P-019 ownership-tree interaction.
- Explicit agent-branch collapse belongs to the current window's workspace state, so leaving/reopening Agents and reconnect snapshots retain it along with selection and composer drafts. Keep the state local to the window lifetime, as with Classic's in-memory group disclosure; this does not introduce persisted reload defaults.
- Expand all affects only agents in the displayed group. Preserve another group's disclosure choices, including while its branch is offscreen. Pointer toggles and Left/Right keyboard expansion share the same state.
- This decision covers explicit operator disclosure. It does not redefine the separate Classic group-level `collapsed_default` preference as a per-agent default; that field's redesigned equivalent remains under the Settings audit.

### D-210 — Hand terminal focus over only when input is ready

- Date: 2026-09-26
- Scope: P-332 terminal activation and P-113 focus continuity.
- An explicit ownership-row activation transfers keyboard focus only after the visible terminal socket opens. Until then, retain the focus intent against its original DOM focus target. Typing immediately after the handoff must reach the selected PTY.
- Cancel that pending intent when the operator focuses another control, the connection closes, the page becomes hidden, or the controller is disposed. A late open must still satisfy visible-pane/session ownership. Reconnect alone does not revive a consumed intent.
- Returning to a visible page preserves a focused composer or another editor. Refresh terminal geometry and restore terminal ownership only when focus already belongs to that terminal surface.
- This retains focus intent, not raw input: do not queue commands for replay across connection failures or agent/session changes.

### D-211 — Bound log reads while retaining the accepted tail

- Date: 2026-09-26
- Scope: P-006 log recovery and the Logs portion of P-112.
- Bound the entire log refresh, including response-body decoding, to 15 seconds independently of transport cancellation. On timeout, keep the last accepted lines, cursor, target, filters and reading state; expose the existing Refresh logs recovery action. Initial loading becomes an explicit unavailable state instead of lasting indefinitely.
- Follow retains its existing two-second retry cadence after an error. A paused view remains paused and retries through Refresh logs or an active reconnect refresh.
- An expired or cancelled response cannot append lines, advance the cursor, clear a newer error or settle a newer read. Switching targets, leaving the surface and reconnecting cancel the old read's transport and timeout. Successful reads clear the timeout and cancellation subscription.


### D-212 — Restore native windows within the current usable desktop

- Date: 2026-09-26
- Scope: P-108/P-123/P-124 main and detached window bounds.
- Main and detached windows share monitor selection and geometry recovery. Preserve placement on a connected secondary display, including negative origins. Prefer the display with the largest overlap; monitor names are fallback hints because they are not unique. Recenter on the named display or primary display when saved coordinates no longer intersect the desktop.
- Use each display's physical work area and scale. Preserve captured physical pixels, legacy display-marked bounds and the main window’s historical interpretation of unmarked captures as physical; explicit logical bounds and omitted default sizes are converted once for the selected display.
- Keep the entire restored window reachable within the selected work area, including its native decorations. Honor logical minimum sizes where they fit; reduce the native minimum on a smaller screen so it cannot defeat recovery. Move before applying the final physical size.
- This restoration contract does not certify monitor hot-plug while a window is open, operating-system sleep recovery, or other platforms. Those native acceptance checks remain separate.


### D-213 — Separate inactive profile fields from Classic layout preferences

- Date: 2026-09-26
- Scope: P-087/P-088 Settings field contracts; extends D-184 compatibility preservation.
- Current standalone/desktop behavior does not read `focus_new_tabs`, `terminal_always_custom_dialog` or `worktree_merge_instructions`. Keep their persisted values for old profiles, but exclude them from React controls, search, changed-value writes and section resets. Do not promise focus, dialog or merge behavior through controls with no effect, and do not reset or migrate stored values while editing another setting.
- `filter_by_window` (global/group) and `collapsed_default` (group) still control the Classic grid. Keep their typed controls editable, with accessible descriptions that explicitly identify their Classic-only scope. Place field guidance on its own line so it is not read as part of a Reset button. The React fixed workspace selects one group; its ownership-tree disclosure follows explicit window-local choices under D-077/D-209. A Classic group default must not silently become an agent-branch default.
- This is a documented compatibility disposition for inactive fields, not removal of active launch, terminal, worktree or Classic layout workflows. Metadata and daemon schema remain available for profile compatibility. Boundary, provider and other runtime-effect acceptance remains independent.


### D-214 — Preserve hire rejection notes and readable Planning journals

- Date: 2026-09-26
- Scope: P-084, P-367 and P-368.
- Reject with note opens a custom dialog naming the requested Engineer and requesting Architect. The optional multiline note survives unrelated updates and reconnects. Submission trims only its outer whitespace and targets the captured hire ID. Pending submission prevents repetition and dismissal; an error retains the submitted note for an explicit identical retry. A 30-second observation deadline covers request and body decoding. An expired, cancelled or unmounted response cannot settle a later attempt; reconnect never replays the rejection.
- Keep the review dialog mounted independently of the pending-hire card because an authoritative resolution delta can remove that card before the HTTP acknowledgement arrives. Close only on the daemon's accepted rejection contract, then refresh the displayed collection. Cancel after an unknown outcome ends the review; it cannot undo a server-side resolution.
- Planning journals render the actual `entry`, `type`, `timestamp` and author identity, rather than generic title/summary fields. Use a first-line summary with author/type/time and an expandable complete multiline body. Stable entry keys preserve disclosure through refresh and reconnect. Journal cards are read-only disclosures, not buttons with no action.


### D-215 — Count workspace members independently of recoverable deletion records

- Date: 2026-09-30
- Scope: Control Center header and Mission Control agent/terminal totals; P-078/P-369.
- Count non-deleted records in the selected group, including stopped agents and terminals that remain in the workspace. Recoverable records with `deleted_at` are excluded from current totals; restoration includes them again. Apply the same distinction after live deltas and snapshots.
- Keep recoverable records in the projection and historical Context author lookup. A summary count must not purge history, remove Restore targets, or equate stopped processes with deleted workspace members.


### D-216 — Match embedding-runtime choices to the daemon contract

- Date: 2026-09-30
- Scope: AI settings, P-087/P-370.
- The embedding-runtime control offers Sentence Transformers, the daemon's currently supported runtime. Remove the unsupported FastEmbed choice, which caused valid-looking drafts to fail on save. Add another choice only when the daemon accepts and implements it.
- Keep provider/model, corpus and boot-summary settings independently editable with AI disabled. Preserve authored drafts on refusal or reconnect; display saved normalized values after reload. Enabling AI remains a persisted master switch, and unavailable local dependencies/models remain a visible recoverable runtime error.


### D-217 — Preserve literal launch environment values

- Date: 2026-09-30
- Scope: Agent/terminal creation environment editors, P-371; extends D-185's literal environment-value contract.
- Trim variable names, but preserve everything after the first equals sign, including leading/trailing spaces, additional equals signs and explicit empty values. Ignore blank lines, comments and lines without a variable name and equals sign.
- Worker role/group hydration must round-trip the displayed environment values without silently changing them on Create. The same value rules apply to explicit Engineer, Architect and terminal environment edits. This deliberately improves on Classic's trimming parser to preserve the exact maps already supported by Settings and the launch backend.
- Group/agent environment-file fallback and variable precedence remain daemon-owned; clearing an agent default restores the group fallback for future launches.


D-217 clarification (2026-09-30): arbitrary map values, including environment variables and GitHub mappings, are literal data. An empty map-value input must not display “Inherit / default”; preserve inheritance hints only on actual schema fields with that behavior. Clearing a literal value and removing its entry remain distinct operations.


### D-218 — Distinguish Inbox recording from desktop notification delivery

- Date: 2026-09-30
- Scope: Group notification settings under P-088.
- Label the `notifications` switch “Desktop notifications” and explain that it adds batched macOS delivery. Turning it off retains Inbox history and does not disable Inbox recording.
- The finish, error and attention switches independently control which events produce Inbox entries. Keep these controls editable when desktop delivery is disabled; errors appear as Inbox alerts. Their descriptions explain that desktop delivery follows the separate switch.
- Preserve normal sparse saves, refused drafts, reconnect focus and daemon-backed values. Do not change the existing backend delivery policy.


### D-219 — Select merge task attribution explicitly

- Date: 2026-09-30
- Scope: Worktree inspector merge controls, P-372; extends P-249.
- Offer a Merge task selector for tasks assigned to the target worker and unassigned tasks with its matching open worktree boundary, including already-loaded archived records. Show task ID, title and lane. Leave automatic attribution available for the existing exactly-one-active-task backend path; never guess from the newest boundary when multiple tasks exist.
- Send an explicitly selected task as `merge_task_id`. Preserve selection through ordinary deltas, reconnects and refused operations. Scope the selection to the worker, worktree path and branch so another target cannot inherit it. If the selected task is no longer available/assigned, retain its identity visibly and require a new choice before merging.
- Freeze the selection during a pending or uncertain operation; exact-key retries retain the submitted attribution. For a released assignment, the server requires an explicit selection plus matching group, recording worker, repository, branch, base branch, open status and a recorded commit. Reassigned tasks remain blocked. Include recording-worker and commit provenance in compact boundary snapshots so the selector can apply the same eligibility rules. Existing automatic attribution and all later merge gates remain unchanged.
- This deliberately completes a workflow that Classic also cannot express: its merge command omits explicit attribution, and completion releases the live assignment, so reported work can otherwise produce a server refusal with no selectable recovery. Do not reopen completed tasks merely to satisfy automatic attribution.


### D-220 — Restore terminal name suggestions without replacing drafts

- Date: 2026-09-30
- Scope: New Terminal and group `terminal_name_prefix`, P-373.
- A saved nonempty terminal prefix suggests the lowest unused positive suffix, matching Classic's workspace-wide name collision check. The setting belongs to the creation group; collisions include other groups. With no prefix, keep the explicit empty name field. Other agent kinds do not inherit terminal names.
- Suggestions may hydrate with incoming group defaults until the operator edits the name. Manual text and explicit clearing remain authoritative through deltas, reconnects and kind changes. A cleared required name keeps creation disabled.
- Freeze the exact submitted identity before creation so a newly visible record or changed prefix cannot alter the displayed name during pending delivery or retry. Existing creation receipt and duplicate-prevention rules remain authoritative.


### D-221 — Distinguish retained launch metadata from embedded terminal effects

- Date: 2026-09-30
- Scope: P-088/P-089 terminal backend, profile and tab-color defaults.
- Keep the stored profile/color fields editable and preserve their existing inheritance into launch records. Describe them as launch metadata: the current embedded terminal adapter does not apply terminal-emulator profiles, and embedded terminals/React do not apply tab colors. Do not promise an iTerm appearance change from Engineer defaults.
- Label the stored default backend as a compatibility value: current operator launches explicitly use the embedded PTY backend. Editing this stored field does not switch terminal runtimes.
- This is clarification of existing runtime capabilities, not retirement of a supported visual workflow. Classic's manual terminal defaults pane is already absent, while action/role metadata authoring remains available. `TerminalCapabilities` and `LocalPtyAdapter.capabilities` define the current support; launch resolution/persistence alone must not be counted as a visual-effect test.


### D-222 — Preview worker-specific launch defaults using launch precedence

- Date: 2026-09-30
- Scope: New Worker, P-374; group worker launch defaults in P-088.
- Request a worker-specific raw role preview. Share worker provider/command/model/reasoning override precedence with the executable launch resolver: worker defaults override role/shared values, and explicit launch edits override worker defaults. Preserve the existing distinct fast-mode order: explicit/role, worker, shared.
- Keep preview commands raw; model/reasoning flags belong to backend launch finalization. Show the values that will be submitted, retain explicit edits on refresh/reconnect and freeze reviewed payloads for exact creation retries. Clearing stored worker defaults restores role/shared inheritance.
- Keep generic role previews backward compatible for other consumers. This repairs a React request that previously promoted generic preview values into explicit overrides, bypassing worker-specific defaults.


### D-223 — Honor principal overrides and expose supported creation controls

- Date: 2026-09-30
- Scope: New Engineer/Architect shell, icon and environment overrides; P-375/P-376 and P-371. Creation control applicability is P-377.
- Forward explicit shell, icon and literal environment maps through the same principal launch resolver as provider, command and directory. Do not silently discard a field shown in creation. Preserve environment whitespace, empty values and equals signs; merge with inherited environment using existing launch rules. An untouched shell inherits kind/group defaults, so label its empty choice “Inherit defaults.”
- Show separate command-argument and initialization-script controls only for terminals, matching Classic's terminal-only startup contract. Agent operators can include arguments in Boot command. Hidden terminal drafts must not be sent after switching to an agent kind. Show the icon control only for agents: terminal creation does not support an icon override. These controls were previously visible without consumers; no supported Classic workflow is retired.
- Render a saved custom icon beside the ownership-row name while retaining its independent status dot. Treat the icon as decorative for accessibility; updates and clearing preserve row identity and selection.
- Preserve sparse untouched principal defaults and per-agent settings precedence. Verify actual shell, environment and directory from a local process; resolved model/reasoning/fast-mode values do not by themselves prove provider inference or service-tier behavior when a custom command is used.


### D-224 — Inherit the group lane for general task creation

- Date: 2026-09-30
- Scope: general Board task creation and P-378; Group Settings P-088.
- Start the general New task dialog at Group default. Send an empty lane until the operator chooses a lane, so the existing server resolves the saved group default and its normal fallback. Classic's general task modal follows this contract.
- Keep explicit lane selection stable across snapshots, reconnect and refused creation. Inline creation within a Board lane continues to submit that lane explicitly. Initiative task creation continues to inherit the group default.
- Keep label/action inheritance and explicit overrides under the existing backend contract. This repairs the general dialog's unintended first-lane override; no backend mutation or default-resolution semantics change.


### D-225 — Offer launch-time worktree overrides only for Workers

- Date: 2026-09-30
- Scope: New Worker/Engineer/Architect and P-379.
- Show and submit launch-time worktree controls only for Workers. Engineer creation forces a shared directory; Architect creation follows the daemon's internal worktree policy. Their handlers do not honor these creation-form overrides, and Classic principal settings dialogs do not offer them.
- Keep a Worker's edited worktree name/base/checkpoint options when switching kinds and back, but do not submit those hidden values for a principal. Do not promise a worktree override in the general creation description.
- Preserve the explicit post-launch Worktree Create flow for both principal kinds, including review of conversation loss, cancellation without mutation, and confirmed creation/relaunch. Removing ignored launch inputs does not retire that supported workflow or change backend policy.


### D-226 — Apply agent capacity to retained agents and preserve creation drafts

- Date: 2026-09-30
- Scope: Group Maximum agents, creation menu/dialog and fresh reviewer allocation; P-380.
- Count non-deleted Workers, Engineers and Architects in the group, including stopped and dismissed agents. Terminals and recoverable deletions consume no seats. Zero means unlimited. Creation and fresh reviewer allocation share the backend count.
- At capacity, explain the count and limit beside disabled new-agent menu choices; keep Terminal available. A creation dialog opened by another entry point or before capacity changes blocks new agent submission while retaining all fields, focus and caret. Explain how to free a seat or change Maximum agents.
- A pending hire is an approval proposal and remains available; actual allocation still observes the backend limit. Restoring a deleted agent remains permitted even above the limit, while further new creation is blocked.
- An uncertain creation retains its exact recovery request. Its own saved target may consume the last slot, so capacity feedback must not prevent receipt recovery or suggest making a second creation.
