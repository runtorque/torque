# Torque Design System

Status: living document
Last updated: 2026-07-14

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

### Inputs and selectors

- Inputs, selects, and textareas use `--radius`, `--bg-inset`, and `--border`.
- Every field needs a visible label or an equivalent accessible name.
- Placeholder text provides an example or hint, never the only label.
- Validation and disabled state must remain legible without relying on opacity
  alone for the explanatory text.

### Cards and contained surfaces

- Cards use `--radius` and should be separated primarily by border and surface
  contrast.
- Avoid nesting multiple fully bordered cards when spacing or a subtle divider
  can express the same hierarchy.
- Repeated cards in a list must keep their action placement consistent.

### Panels and toolbars

- Panel headers use compact, aligned controls and preserve the content width for
  the panel's primary information.
- Repeated panel actions belong in the same order: navigation first, then local
  actions, then layout/window controls.
- Resizable panels must preserve operator-selected dimensions and content state.

### Modals, menus, and popovers

- Use Torque's custom overlay, modal, and context-menu patterns rather than
  native blocking dialogs.
- Use `--radius-lg` for the floating container and standard radii for controls
  inside it.
- Opening a surface moves focus into it; closing it restores focus to the control
  that opened it.
- Escape closes dismissible transient surfaces. Destructive confirmation should
  name the affected object.

### Badges, tags, and counts

- Pill geometry is appropriate because these elements describe metadata rather
  than offer navigation.
- Keep badges short and visually subordinate to the primary label.
- Status badges use semantic color tokens and a textual or iconographic cue.

### Empty, loading, and error states

- Empty states explain what is absent and provide one clear next action when one
  exists.
- Loading states preserve layout geometry where practical.
- Errors state what failed and what the operator can do next. Do not replace
  useful content with a generic error if stale content can remain safely visible.

## Responsive and embedded behavior

- Torque must work in standalone browser, desktop, and embedded layouts.
- Prefer container queries for controls whose available panel width matters more
  than the browser viewport.
- Compact variants must preserve the same actions and accessible names.
- A responsive transition must not silently hide the only path to an action.

## Accessibility baseline

- Interactive controls are reachable and operable by keyboard.
- Use semantic roles and native controls before adding ARIA.
- Icon-only controls have explicit `aria-label` text.
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

| Component family | Status | Next concern |
|---|---|---|
| Foundations and tokens | Core scale standardized | Migrate component families and audit remaining literals |
| Group tabs | Standardized | Verify compact switcher parity |
| Panel tabs | Standardized | Verify all panel zones and narrow widths |
| Feature navigation tabs | Core variants standardized | Migrate compatibility aliases to the canonical API |
| Buttons | Core variants standardized | Migrate feature-specific aliases and audit icon buttons |
| Inputs and selectors | Baseline | Audit sizing and validation states |
| Cards | Pending | Reconcile grid, board, context, and agent cards |
| Toolbars and panel headers | Pending | Standardize spacing, ordering, and overflow |
| Menus and popovers | Pending | Standardize geometry and focus restoration |
| Modals | Pending | Standardize headers, actions, widths, and destructive flows |
| Badges, tags, and status | Pending | Separate semantic pills from action controls |
| Empty/loading/error states | Pending | Define reusable patterns and language |

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
