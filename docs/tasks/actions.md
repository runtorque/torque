# Actions

An action is a reusable prompt template, defined in YAML at `.torque/actions/foo.yaml` (project) or `~/.torque/actions/foo.yaml` (user). Actions standardize what you tell an agent. They make pipelines possible. They turn a prompt you'd otherwise type 50 times into a parameterized template you write once.

This page is the operational guide for actions. The exact schema and two deeper chapters live separately:

- **[Action YAML format](../reference/action-yaml-format.md)** — field-by-field YAML reference, the reserved `torque` namespace, namespacing, transitions, and examples.
- **[Templates](templates.md)** — the Jinja2 layer of the `prompt:` field, plus the `torque` context namespace that lets prompts adapt to the live agent state.
- **[Pipelines](pipelines.md)** — the `transitions:` field, target routing, depth limits, and the LOC review gate.

If you're new to actions, read this page top-to-bottom and follow the links inline.

## A minimal action

```yaml
name: oneshot/fix
description: Diagnose and fix a bug directly on the current branch

agent: bugfixer       # references .torque/roles/bugfixer.yaml

labels:
  - bugfix

prompt: |
  You are diagnosing and fixing a bug. Focus on understanding the
  root cause before writing any code.

  {{ TASK }}

  - Reproduce the issue first
  - Find the root cause
  - Write a failing test, then fix it
  - Run the relevant test suite
```

That's a complete, valid, dispatchable action. The agent gets the rendered prompt with the task description filling in `{{ TASK }}`, plus the dispatch postscript that lists the MCP tools available for reporting back.

## Where actions live

Torque searches two locations. Project-local actions take precedence.

| Scope | Path | Version-controlled? |
|---|---|---|
| **Project** | `.torque/actions/` in your repo root | Yes (recommended) |
| **User** | `~/.torque/actions/` in your home | No |

Subdirectories create namespaces. `.torque/actions/feature/review.yaml` becomes the action `feature/review`. The action editor and the CLI both treat the path as the name.

If a project action and a user action have the same name, the project action wins. The user action is still listed in the UI (marked "shadowed") but never dispatched.

### Installing the starter actions

The repo ships example actions in `actions/`. Copy them in:

```bash
mkdir -p .torque/actions
cp -r actions/* .torque/actions/
```

Inspect them:

```bash
torque action list
torque action show feature/implement
```

## Fields reference

| Field | Type | Required | What it does |
|---|---|---|---|
| `name` | string | yes | Unique identifier. Supports namespaces: `feature/review` → `feature/review.yaml`. |
| `description` | string | no | One-liner shown in the UI action picker. |
| `prompt` | string | yes | Jinja2 template. **Must contain `{{ TASK }}`.** → [Templates](templates.md) |
| `agent` | string or object | no | Role name (preferred) or legacy inline agent block. → [Workers](../team/workers.md#roles-reusable-worker-presets) |
| `worktree` | bool | no | Create an isolated git worktree for the agent. → [Worktrees](worktrees.md) |
| `labels` | list | no | Labels applied to the dispatched task. |
| `transitions` | list | no | Valid next actions for pipeline chaining. → [Pipelines](pipelines.md) |
| `auto_close_on_done` | bool | no | Auto-close the agent after `torque_done` when the pipeline root is fully complete. |
| `implementation_depth` | bool | no | Marks the action as code-mutating. Eligible for the LOC review gate. |
| `review_required_above_loc` | int | no | Non-test LOC threshold for auto-deriving `feature/review`. Defaults to 150 when `implementation_depth` is true. |
| `max_depth` | int | no | Override the global `max_pipeline_depth` for this action. |
| `disable_role_preamble` | bool | no | Don't prepend the role's `preamble` / `priorities` blocks to the rendered prompt. |

## The `prompt` field

The `prompt:` field is the only Jinja2-rendered part of an action. Everything else is static YAML.

Three rules:

1. **Must contain `{{ TASK }}`** — validated on save.
2. **Cannot use `torque` as a custom variable name** — it's reserved for the live context namespace.
3. **All other variables are auto-discovered** from the template. You don't declare them anywhere.

For the rendering details (custom variables, the `torque` namespace, conditionals, loops, filters, preview), see [Templates](templates.md).

## The `agent` field

Two forms:

**Preferred — reference a role**:

```yaml
agent: researcher
```

This points to `.torque/roles/researcher.yaml` (or the user-scope equivalent). The role file carries the full launch configuration: provider, model, permissions, system prompt, worktree behavior, environment, child terminals.

**Legacy — inline agent block** (still supported, deprecated):

```yaml
agent:
  name_prefix: impl
  command: claude --model sonnet
  tab_color: "#3fb950"
```

Use roles. The inline form exists for backwards compatibility but is harder to keep consistent across actions.

For the role format and how to write one, see [Workers — Roles](../team/workers.md#roles-reusable-worker-presets).

## The `transitions` field

Transitions declare the legal next steps for an action. They're how pipelines emerge:

```yaml
transitions:
  - action: feature/review
    when: implementation is complete and ready for review
  - ask: true
    when: changes look correct but need human approval
```

Each transition can target a specific agent (`target: self`, `target: parent`, `target: root`) or, by default, dispatch a fresh agent on the calling agent's worktree.

The `when:` field is documentation that the agent reads in its dispatch postscript. Be specific. → [Pipelines](pipelines.md)

## Dispatch flow

Three places to dispatch:

**From the board UI**:

1. Click **+ Add task** in any lane (or open the full task modal).
2. Pick an action from the action picker.
3. Fill in any variables the action requires.
4. Click **Dispatch**.

**From the CLI**:

```bash
# One-liner: create + dispatch
torque task dispatch "Add dark mode" -t feature/implement

# With variables
torque task dispatch "Fix the flaky test" \
  -t oneshot/fix \
  -v MODULE=auth \
  -v TEST_COMMAND="pytest tests/auth"
```

**From an agent (pipeline derivation)**:

```text
torque_derive(
  description="Review the implementation",
  action="feature/review",
)
```

In all three cases, Torque renders the action's `prompt:` against the task description, your variables, and the live `torque` context. → [Templates](templates.md)

## Managing actions

### From the UI

The **Actions** panel (toggle with ++a++ or the panel button) is the visual editor:

- **Action picker** — dropdown grouped by Project and User scope.
- **Prompt editor** — Jinja2 syntax-highlighted textarea with `{{ TASK }}` validation.
- **Variable discovery** — auto-detected variables shown below the editor.
- **Transitions editor** — add / edit / remove transitions, with an action picker for the `action:` field and an auto-growing textarea for `when:`.
- **Save / Duplicate / Delete** — full CRUD with scope picker (Project or User).
- **Pipelines view** — pannable, zoomable canvas showing action nodes connected by transition edges.

Action names allow `/` for subdirectory namespaces. Other special characters are sanitized.

### From the CLI

```bash
torque action list                    # list all actions
torque action show feature/review     # show one action's full YAML
torque action create my-action        # scaffold a new action file
```

### Manual creation

```bash
mkdir -p .torque/actions
cat > .torque/actions/quick-fix.yaml << 'EOF'
name: quick-fix
description: Quick targeted fix
agent: bugfixer

prompt: |
  You are fixing a specific issue. Be surgical — change only what's needed.

  {{ TASK }}

  - Reproduce the issue first
  - Find the root cause
  - Write a failing test, then fix it
  - Run the test suite
EOF
```

Torque picks it up immediately — no daemon restart needed.

## Examples

### A oneshot bug fix

Simple action, no pipeline, runs directly on the current branch:

```yaml
name: oneshot/fix
description: Diagnose and fix a bug directly on the current branch
agent: bugfixer

labels:
  - bugfix

prompt: |
  You are diagnosing and fixing a bug. Focus on understanding the
  root cause before writing any code.

  ## Bug report
  {{ TASK }}

  ## Approach
  1. Reproduce — confirm the failure before changing anything
  2. Find the root cause — don't patch symptoms
  3. Write a failing test, then make it pass
  4. Keep the fix minimal — no drive-by refactors
  5. Run the narrowest relevant tests first; broaden only if risk warrants it
  6. End with: what you verified, what you didn't, what couldn't be verified live
```

### A three-stage feature pipeline

Three actions that compose into the implement → review → fix → re-review loop:

```yaml
# .torque/actions/feature/implement.yaml
name: feature/implement
description: Implement a feature in an isolated worktree
agent: implementer

worktree: true
implementation_depth: true

labels:
  - feature

transitions:
  - action: feature/review
    when: implementation is complete and ready for review

prompt: |
  You are implementing a feature in an isolated worktree branch.

  {{ TASK }}

  - Read relevant code first
  - Write clean, minimal code
  - Add tests for new behavior
  - Run the test suite before finishing
```

```yaml
# .torque/actions/feature/review.yaml
name: feature/review
description: Review a branch for issues
agent: reviewer

auto_close_on_done: true
labels:
  - review

transitions:
  - action: feature/fix-review
    when: issues were found that need to be fixed
    target: parent
  - ask: true
    when: changes look correct but need human sign-off

prompt: |
  {% if torque.context.is_clean %}
  You are a code reviewer. A previous agent implemented changes; your job
  is to evaluate whether the work is correct, safe, and ready to merge.

  ## What was implemented
  {{ TASK }}

  ## Process
  1. Read the full diff
  2. Check correctness and edge cases
  3. Run targeted automated checks; note remaining live verification
  4. Separate blocking issues from follow-up suggestions
  5. End with: **Ship**, **Ship with fixes**, or **Needs rework**
  {% else %}
  Re-review after fixes:

  {{ TASK }}
  {% endif %}
```

```yaml
# .torque/actions/feature/fix-review.yaml
name: feature/fix-review
description: Fix issues found during review
agent: implementer

worktree: true
labels:
  - review-fix

transitions:
  - action: feature/review
    when: all review issues have been addressed
    target: parent

prompt: |
  {% if torque.context.is_clean %}
  You are fixing issues found by a code reviewer on this branch.
  Address every critical and warning issue. Nits are optional.

  {{ TASK }}

  - No new functionality
  - Run the test suite after fixes
  {% else %}
  More review feedback to address:

  {{ TASK }}
  {% endif %}
```

The `target: parent` on the review's fix transition is what sends the fix work back to the original implementer instead of spinning up a fresh fix agent. → [Pipelines — Transition-targeted routing](pipelines.md#transition-targeted-routing)

## Where to next

- [Templates](templates.md) — Jinja2, the `torque` context namespace, conditionals, worked examples.
- [Pipelines](pipelines.md) — transitions, target routing, depth limits, the `ask` gate, LOC gates.
- [Tasks and threads](threads.md) — how dispatched actions become threads on the board.
- [Workers — Roles](../team/workers.md#roles-reusable-worker-presets) — the agent-side configuration the `agent:` field references.
