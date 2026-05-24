# Action YAML format

Actions are YAML files that tell Torque how to render a task prompt and, optionally, how that task can continue through a pipeline.
They live in project scope at `.torque/actions/` or user scope at `~/.torque/actions/`.

Use this page as the field-by-field reference. For the operator walkthrough, see [Actions](../tasks/actions.md), [Templates](../tasks/templates.md), and [Pipelines](../tasks/pipelines.md).

## Lookup, scope, and namespacing

Torque searches for actions in this order:

1. The first project-local `.torque/actions/` found by walking up from the active directory.
2. `~/.torque/actions/`.

Project actions win when a project action and a user action have the same name. User actions with the same name are still listed as shadowed, but the project action is the one that dispatch uses.

The file path under the action directory is the action name:

| File | Action name |
|---|---|
| `.torque/actions/oneshot/fix.yaml` | `oneshot/fix` |
| `.torque/actions/feature/implement.yaml` | `feature/implement` |
| `~/.torque/actions/research.yaml` | `research` |

Set the YAML `name:` to the same value as the path for readability and editor round-tripping. The path is the lookup key.

## Rendering model

Only the `prompt:` field is rendered through Jinja2. All other YAML fields are static metadata.

At render time Torque passes:

- `TASK`: the board task title.
- Any task `action_vars` as top-level Jinja2 variables.
- `torque`: a reserved namespace with live agent, task, worktree, context, and terminal state.

New actions should include `{{ TASK }}` in `prompt:`. The current validator also accepts `{{ torque.task.title }}` for catalog actions that read the task title through the `torque` namespace. Prefer `{{ TASK }}` for portable examples unless you specifically need other `torque.task.*` metadata nearby.

Custom variables are auto-discovered from the prompt; there is no separate variable declaration block. Use Jinja2's `default` filter to make a variable optional:

```yaml
prompt: |
  Work in {{ MODULE }}.

  {{ TASK }}

  Run: {{ TEST_COMMAND | default("make test") }}
```

`torque` is reserved. Do not use it as a custom variable name.

Legacy actions that use `task:`, `instructions:`, `context:`, and `criteria:` are coalesced into `prompt:` on load for compatibility. New actions should use `prompt:` only.

## Top-level schema

| Field | Type | Required | Description |
|---|---:|:---:|---|
| `name` | string | recommended | Human-readable action name. Keep it equal to the file path without `.yaml`. |
| `description` | string | no | One-line description shown in action pickers. |
| `prompt` | string | yes | Jinja2-rendered prompt body. Must include `{{ TASK }}` or `{{ torque.task.title }}`. |
| `agent` | string or object | no | Preferred: role/template name. Legacy: inline agent settings. See [Agent settings](#agent-settings). |
| `group` | string | no | Optional group override metadata for action-driven creation flows. Most board tasks use the task's group. |
| `labels` | list of strings | no | Labels associated with tasks created or described by this action. |
| `worktree` | bool | no | Requests an isolated git worktree for the launched worker in action-aware launch paths. Prefer setting this on the referenced role when using `agent: role-name`. |
| `terminals` | list of objects | no | Child terminal specs to create with the worker in action-aware launch paths. Prefer role `terminals:` for reusable launch configuration. |
| `transitions` | list of objects | no | Legal next actions or human-ask exits for this action. See [Transitions](#transitions). |
| `max_depth` | int | no | Per-action override for the global pipeline depth limit. `0` or missing means use the global setting. |
| `auto_close_on_done` | bool | no | After the pipeline root is complete, close agents whose completed chain action has this set. |
| `disable_role_preamble` | bool | no | Do not prepend the worker role's preamble/priorities to this action's rendered prompt. |
| `implementation_depth` | bool | no | Marks the action as code-mutating and eligible for review-gate behavior. |
| `review_required_above_loc` | int | no | Legacy action-level non-test LOC threshold for review gating. Transition `loc_gate` is more specific. |
| `deliverable` | object | no | Optional artifact contract. When `required: true`, closeout is gated on uploading the matching artifact. See [Deliverable contract](#deliverable-contract). |

Example skeleton:

```yaml
name: feature/implement
description: Implement a feature in an isolated worktree
agent: implementer
worktree: true
implementation_depth: true
max_depth: 5

labels:
  - feature

transitions:
  - action: feature/review
    when: implementation is complete and tests pass
    status: "On Review"

prompt: |
  You are implementing a feature.

  {{ TASK }}
```

## Agent settings

Prefer a role/template reference:

```yaml
agent: implementer
```

This points at a worker role in `.torque/roles/` or `~/.torque/roles/`. Roles are the best place for full launch configuration such as provider, model, permissions, system prompt, worktree behavior, environment, and child terminals. For the field-by-field role schema, see [Role YAML format](role-yaml-format.md).

The legacy inline form is still parsed:

```yaml
agent:
  name_prefix: impl
  command: codex
  directory: /path/to/repo
  profile: Default
  shell: /bin/zsh
  tab_color: "#3fb950"
  env_vars:
    FEATURE_FLAG: "1"
```

Inline agent fields:

| Field | Type | Description |
|---|---:|---|
| `name_prefix` | string | Prefix used by legacy action-driven naming paths. |
| `command` | string | Provider boot command override. |
| `directory` | string | Working directory override. |
| `profile` | string | iTerm2 profile name. |
| `shell` | string | Shell override. |
| `tab_color` | string | Tab color, usually a quoted hex string. |
| `env_vars` | object | Environment variables for the launched process in render output. |

## Terminals

`terminals:` is a list of companion terminal specs:

```yaml
terminals:
  - name: server
    command: npm run dev
    directory: /path/to/app
  - name: tests
    command: npm test -- --watch
    init_script: source .venv/bin/activate
```

Supported fields:

| Field | Type | Description |
|---|---:|---|
| `name` | string | Terminal display name. Defaults to a generated shell name when missing. |
| `command` | string | Command to start in the terminal. |
| `directory` | string | Directory for the terminal. Defaults to the parent agent directory. |
| `init_script` | string | Optional startup script sent when the terminal session is created. |

For reusable setups, put terminal definitions on a role and reference it with `agent: role-name`.

## Transitions

Transitions define the derive surface for an action. If an action has transitions, the worker's dispatch postscript lists only those legal `torque_derive(...)` targets. The server validates the same list when the worker calls the tool and rejects targets outside the declared list. If an action has no transitions, derive is not advertised as a completion path; treat it as a terminal stage.

Action transition shape:

```yaml
transitions:
  - action: feature/review
    when: implementation is complete and ready for review
    status: "On Review"
    target: parent
```

| Field | Type | Required | Description |
|---|---:|:---:|---|
| `action` | string | yes | Target action name to create/dispatch. |
| `when` | string | recommended | Plain-English guidance shown to the worker. |
| `status` | string | no | Status badge written to the parent/root task while this transition is active. Defaults to the target action name. |
| `target` | string | no | Dispatch routing: `self`, `parent`, `root`, or omitted for a fresh agent. |
| `required` | bool | no | Requires this transition before `torque_done`/`torque_ready` can close the task. |
| `pre_approved` | bool | no | Marks the derived task as review-preapproved so its closeout gate can be bypassed. |
| `loc_gate` | object | no | Transition-local non-test LOC review policy. |

Human-in-the-loop ask transition:

```yaml
transitions:
  - ask: true
    when: changes look correct but need human sign-off before merging
```

`ask: true` enables `torque_ask(...)` as a completion path instead of deriving another action.

### Transition routing

| `target` | Effect |
|---|---|
| omitted | Create a fresh worker. For pipeline work, the new worker inherits the caller's worktree. |
| `self` | Queue the derived task back into the same worker session. |
| `parent` | Route to the agent that owns the parent task. |
| `root` | Route to the agent that owns the pipeline root task. |

### LOC gate

A transition to `feature/review` can carry a per-transition review gate:

```yaml
transitions:
  - action: feature/review
    when: implementation is complete or LOC gate requires review
    status: "On Review"
    loc_gate:
      ship_direct_max: 50
      review_default_above: 150
      self_review_bypass_allowed: false
```

| Field | Type | Description |
|---|---:|---|
| `ship_direct_max` | int | Non-test LOC at or below which direct completion may ship without review. |
| `review_default_above` | int | Non-test LOC above which review is the default path. |
| `self_review_bypass_allowed` | bool | Whether an explicit self-review bypass may skip the gate. |

## Deliverable contract

The `deliverable` block declares that a task must produce a persisted artifact, not just a code change. When `required: true`, Torque hard-gates `torque_done` / `torque_ready`: the worker must first attach a matching artifact with `torque_task_upload_artifact`, or closeout is rejected.

```yaml
deliverable:
  required: true
  type: plan
  format: markdown
  artifact_title: Implementation Plan
```

| Field | Type | Required | Description |
|---|---:|:---:|---|
| `required` | bool | no | When `true`, the worker must upload a matching artifact before `torque_done`/`torque_ready` can close the task. Defaults to `false` (the block is then a no-op contract). |
| `type` | string | no | Semantic type identifier such as `plan`, `design`, or `report`. The closeout gate matches leniently: when `type` is empty or `other`, **any** attached artifact satisfies the gate; otherwise an uploaded artifact's `artifact_type` must equal this value. |
| `format` | string | no | File-format hint such as `markdown` or `yaml`. Advisory — it is surfaced to the worker but not enforced by the gate. |
| `artifact_title` | string | no | Display title for the uploaded artifact, shown in the task's artifact list. |

### Override semantics

The action's `deliverable` block defines the task's contract. Explicit dispatch-time arguments (from an MCP or HTTP caller that passes its own `deliverable` fields) win over the action block on a per-field basis — `required` is only overridden when explicitly present, and string fields are overridden only when non-empty. An action without a `deliverable` block carries no contract.

### Uploading the matching artifact

The worker satisfies a `type: plan` contract by uploading an artifact whose `artifact_type` matches:

```text
torque_task_upload_artifact(
  content_text="# Implementation Plan\n\n...",
  filename="plan.md",
  artifact_type="plan",
  title="Implementation Plan",
)
```

`content_text` writes inline text (use `local_path` or `content_base64` for files on disk or binary content). Once the matching artifact is attached, `torque_done` / `torque_ready` succeed.

## Reserved `torque` namespace

`torque` is always injected into the Jinja2 render context. Preview renders use stub values for the stable namespace; real dispatch renders use the selected worker and task. Fields marked as real-dispatch only are available in live task dispatch but may not be present in every preview stub.

### `torque.agent`

| Field | Type | Description |
|---|---:|---|
| `torque.agent.id` | string | Agent cell ID. |
| `torque.agent.name` | string | Display name. |
| `torque.agent.slug` | string | URL-friendly agent slug. |
| `torque.agent.type` | string | Provider/adapter type, such as `claude-code`, `codex`, or `gemini-cli`. |
| `torque.agent.group` | string | Agent group. |
| `torque.agent.directory` | string | Agent working directory. |
| `torque.agent.kind` | string | `worker`, `engineer`, `architect`, `terminal`, or empty. |
| `torque.agent.role` | string | Worker role/template slug when present. |
| `torque.agent.owner_engineer` | string | Owning engineer display name for worker contexts. |

### `torque.context`

| Field | Type | Description |
|---|---:|---|
| `torque.context.is_clean` | bool | `true` when this is the worker's first dispatched task. |
| `torque.context.tasks_dispatched` | int | Number of tasks previously dispatched to this worker. |
| `torque.context.previous_tasks` | list | Other tasks linked to this worker, each with `task`, `lane`, and `action`. |

### `torque.worktree`

| Field | Type | Description |
|---|---:|---|
| `torque.worktree.active` | bool | Whether the agent currently has a worktree path. |
| `torque.worktree.path` | string | Worktree filesystem path. |
| `torque.worktree.branch` | string | Worktree branch name. |
| `torque.worktree.base_branch` | string | Base branch used to create the worktree. |
| `torque.worktree.dirty` | bool | Whether there are uncommitted changes. |
| `torque.worktree.diff` | object | Diff summary object, usually including files/insertions/deletions. |
| `torque.worktree.checkpoints` | int | Number of checkpoint commits recorded for the worktree. |

### `torque.task`

| Field | Type | Description |
|---|---:|---|
| `torque.task.id` | string | Board task ID. |
| `torque.task.title` | string | Board task title; same content as `TASK`. |
| `torque.task.slug` | string | URL-friendly task slug. |
| `torque.task.description` | string | Task description/context field. |
| `torque.task.depth` | int | Pipeline depth. Root tasks are `0`. |
| `torque.task.is_derived` | bool | `true` when the task has a parent task. |
| `torque.task.parent_task_id` | string | Parent task ID, if any. |
| `torque.task.parent_agent_id` | string | Parent task agent ID, if resolvable. |
| `torque.task.parent_agent_name` | string | Parent task agent display name. |
| `torque.task.parent_agent_slug` | string | Parent task agent slug. |
| `torque.task.labels` | list | Task labels. |
| `torque.task.group` | string | Task group. |
| `torque.task.status` | string | Task status badge text. |
| `torque.task.verification_mode` | string | Real dispatch: verification mode, such as deploy/restart, when set. |
| `torque.task.verification_state` | string | Real dispatch: verification state. |
| `torque.task.verification_notes` | string | Real dispatch: verification notes. |
| `torque.task.verification_updated_at` | string | Real dispatch: last verification update timestamp. |
| `torque.task.verification_updated_by` | string | Real dispatch: actor that last updated verification. |
| `torque.task.verification_summary` | object | Real dispatch: structured verification summary. |
| `torque.task.worktree_boundary` | object | Worktree boundary metadata for merge/review flows. |
| `torque.task.resume_after_boundary_task_id` | string | Task to resume after a boundary task completes. |
| `torque.task.attachments` | list | Legacy attachment records with `path` and `filename`. |
| `torque.task.artifacts` | list | Normalized artifacts attached to the current task. |
| `torque.task.upstream_artifacts` | list | Direct-parent artifacts made available to a derived task. |

### `torque.terminals`

`torque.terminals` is a list of child terminal snapshots. Each entry has:

| Field | Type | Description |
|---|---:|---|
| `name` | string | Terminal display name. |
| `slug` | string | Terminal slug. |
| `current_path` | string | Terminal current working directory. |
| `current_process` | string | Foreground process name when known. |
| `current_branch` | string | Current git branch when known. |

Example:

```yaml
prompt: |
  {{ TASK }}

  {% if torque.terminals %}
  Companion terminals:
  {% for terminal in torque.terminals %}
  - {{ terminal.name }} at {{ terminal.current_path }}{% if terminal.current_process %} running {{ terminal.current_process }}{% endif %}
  {% endfor %}
  {% endif %}
```

## Examples

### Minimal `oneshot/*` action

```yaml
# .torque/actions/oneshot/fix.yaml
name: oneshot/fix
description: Diagnose and fix a bug directly on the current branch
agent: bugfixer

labels:
  - bugfix

implementation_depth: true

prompt: |
  You are diagnosing and fixing a bug. Be surgical.

  ## Bug report

  {{ TASK }}

  ## Approach

  1. Reproduce the issue.
  2. Find the root cause.
  3. Keep the fix minimal.
  4. Run the narrowest relevant tests.
  5. Report what you verified.
```

### `feature/*` pipeline action with transitions

```yaml
# .torque/actions/feature/implement.yaml
name: feature/implement
description: Implement a feature, then derive review
agent: implementer
worktree: true
implementation_depth: true

labels:
  - feature

transitions:
  - action: feature/review
    when: implementation is complete and tests pass
    status: "On Review"
    required: true
    loc_gate:
      ship_direct_max: 50
      review_default_above: 150
      self_review_bypass_allowed: false

prompt: |
  You are implementing a feature in an isolated worktree.

  {{ TASK }}

  Keep the diff focused, add or update tests, and derive review when ready.
```

```yaml
# .torque/actions/feature/review.yaml
name: feature/review
description: Review an implementation branch
agent: reviewer
auto_close_on_done: true

labels:
  - review

transitions:
  - action: feature/fix-review
    when: blocking issues were found and need fixes
    status: "Fixing Review Issues"
    target: parent
  - ask: true
    when: changes look correct but need human sign-off before merge

prompt: |
  You are reviewing the implementation for correctness, safety, and merge risk.

  {{ TASK }}

  End with Ship, Ship with fixes, or Needs rework.
```

### Action with custom child terminals

```yaml
# .torque/actions/oneshot/dev-server.yaml
name: oneshot/dev-server
description: Work with a dev server and test watcher available
agent:
  name_prefix: dev
  tab_color: "#3fb950"
  directory: /path/to/app

terminals:
  - name: server
    command: npm run dev
    directory: /path/to/app
  - name: tests
    command: npm test -- --watch
    directory: /path/to/app

prompt: |
  You are working with companion terminals for the dev server and tests.

  {{ TASK }}

  Use the server and tests terminals when they are available. If they are not
  running, start the narrowest command needed and report what you verified.
```
