# Templates and the `torque` context

An action's `prompt:` field is a Jinja2 template. At dispatch time, Torque renders it against three sources of context:

1. **`{{ TASK }}`** — the task description (always present, always required).
2. **Custom variables** — anything you reference in the template that isn't `TASK` or `torque`. Auto-discovered from the source.
3. **The `torque` namespace** — a structured dict of live state about the agent, its worktree, the current task, and any companion terminals. Always available; never has to be passed in.

That third one is the leverage point. A template that uses `{{ TASK }}` alone is just a fancier way of typing the same prompt every time. A template that uses `torque.context.is_clean`, `torque.worktree.dirty`, and `torque.agent.type` is one prompt that adapts to whether the agent is fresh, whether the branch has uncommitted work, and whether you're running Claude Code or Codex.

This page walks through what the namespace contains and how to write templates that actually use it well.

For the broader action format and pipeline transitions, see [Actions](actions.md) and [Pipelines](pipelines.md).

## The `TASK` variable

Every prompt **must** contain `{{ TASK }}`. This is where the task description (what you typed when creating the task) gets inserted at render time.

```yaml
prompt: |
  You are implementing a feature.

  {{ TASK }}

  Keep the diff focused.
```

If you dispatch this with the task "Add dark mode to the settings page", the agent receives:

```text
You are implementing a feature.

Add dark mode to the settings page

Keep the diff focused.
```

Validation happens on save: if `{{ TASK }}` is missing from the prompt, the action editor refuses to save. This is a load-bearing convention — without `{{ TASK }}`, the agent literally has no task to do.

## Custom variables

Any Jinja2 variable that isn't `TASK` or `torque` becomes a custom variable. Torque discovers them automatically from the template — you don't declare them anywhere.

```yaml
prompt: |
  You are fixing a bug in the {{ MODULE }} module.

  {{ TASK }}

  Run the tests with: {{ TEST_COMMAND | default("make test") }}
```

This action exposes three variables:

- `TASK` — required, filled from the task description.
- `MODULE` — required, supplied at dispatch time.
- `TEST_COMMAND` — optional, with a fallback.

The `| default("value")` filter is the standard Jinja2 way to make a variable optional. If it's not provided at dispatch, the fallback fills in.

You supply variables at dispatch:

```bash
torque task dispatch "Fix the flaky auth test" \
  -t oneshot/fix \
  -v MODULE=auth \
  -v TEST_COMMAND="pytest tests/auth"
```

In the task modal, the form auto-grows to show one input field per discovered variable. → [Task board](board.md)

## The `torque` namespace

`torque` is a reserved variable name. You can't use it for your own variables (the action editor rejects it on save). It's injected at render time and contains:

| Path | What it carries |
|---|---|
| `torque.agent` | Agent identity: name, slug, kind, type, group, directory. |
| `torque.context` | Dispatch history: is this a fresh agent? How many tasks dispatched before? |
| `torque.worktree` | Worktree state: active, branch, base branch, dirty, diff stats, checkpoints. |
| `torque.task` | Current task metadata: ID, depth, parent, labels, attachments. |
| `torque.terminals` | List of the agent's child terminals with their live process and path. |

Every field is safe to read even when the surrounding state is missing — empty strings, empty lists, `False` booleans for absent state. You can write `{% if torque.worktree.active %}` without first checking that `torque.worktree` exists.

### `torque.context` — dispatch history

| Field | Type | Description |
|---|---|---|
| `torque.context.is_clean` | bool | `True` if this is the first task dispatched to the agent. |
| `torque.context.tasks_dispatched` | int | Number of prior dispatches (0 on first dispatch). |
| `torque.context.previous_tasks` | list | Tasks still linked to the agent. Each has `task`, `lane`, `action`. |

This is the most-used branch in adaptive templates. The pattern is "long prompt for a fresh agent, short prompt for a continuing one":

```yaml
prompt: |
  {% if torque.context.is_clean %}
  You are a senior engineer. Read code carefully before writing. Keep diffs
  small. Add tests for new behavior. Run the test suite before finishing.

  ## Working directory
  {{ torque.agent.directory }}

  ## Task
  {{ TASK }}

  ## Guidelines
  - One task at a time
  - Commit when each subtask is green
  {% else %}
  ## Next task
  {{ TASK }}
  {% endif %}
```

On the first dispatch the agent gets the whole system prompt with environment and guidelines. On follow-up dispatches (via `target: self`, or via `--self` from the CLI) the agent already has the system prompt in context — it just gets the new task. This keeps follow-up prompts tight and avoids re-injecting context the agent already has.

### `torque.agent` — agent identity

| Field | Type | Description |
|---|---|---|
| `torque.agent.name` | string | Display name. |
| `torque.agent.slug` | string | URL-friendly identifier. |
| `torque.agent.kind` | string | `worker`, `engineer`, `architect`, or `terminal`. |
| `torque.agent.type` | string | Provider: `claude-code`, `codex`, `gemini-cli`, or empty. |
| `torque.agent.role` | string | Assigned role name, if any. |
| `torque.agent.group` | string | Group name. |
| `torque.agent.directory` | string | Working directory. |
| `torque.agent.owner_engineer` | object | Owner Engineer details (kind / name / slug), for Workers. |
| `torque.agent.hired_by_architect` | object | Hiring Architect details, for Engineers. |

The most useful field is `torque.agent.type`, for adapting prompts to provider idioms:

```yaml
prompt: |
  {{ TASK }}

  {% if torque.agent.type == "claude-code" %}
  Use Claude Code's built-in tools (Read, Edit, Bash, Grep) for file
  edits and searches. Avoid shell-out for things tools handle directly.
  {% elif torque.agent.type == "codex" %}
  Use Codex's `apply_patch` for edits and `shell` for everything else.
  Stay terse — Codex pays per token in some configs.
  {% else %}
  Edit files directly with whatever facilities are available. Use grep
  and find for navigation.
  {% endif %}
```

### `torque.worktree` — git worktree state

| Field | Type | Description |
|---|---|---|
| `torque.worktree.active` | bool | Whether a worktree exists for this agent. |
| `torque.worktree.path` | string | Absolute path. |
| `torque.worktree.branch` | string | Worktree branch. |
| `torque.worktree.base_branch` | string | Branch the worktree was forked from. |
| `torque.worktree.dirty` | bool | Whether there are uncommitted changes. |
| `torque.worktree.diff` | object | `files`, `insertions`, `deletions`. |
| `torque.worktree.checkpoints` | int | Checkpoint commits on the branch. |

Useful for review prompts that want to surface diff stats up front:

```yaml
prompt: |
  {% if torque.worktree.active %}
  You're reviewing branch `{{ torque.worktree.branch }}` (forked from
  `{{ torque.worktree.base_branch }}`).

  {% if torque.worktree.diff.files > 0 %}
  Diff stats: {{ torque.worktree.diff.files }} files,
  +{{ torque.worktree.diff.insertions }}/-{{ torque.worktree.diff.deletions }}.
  {% endif %}

  {% if torque.worktree.dirty %}
  ⚠️ Uncommitted changes present. Surface that in the review verdict.
  {% endif %}
  {% endif %}

  {{ TASK }}
```

### `torque.task` — current task metadata

| Field | Type | Description |
|---|---|---|
| `torque.task.id` | string | `GROUP:<n>` or `GROUP:<root>:<child>` for derived tasks. |
| `torque.task.depth` | int | 0 for root, increments per derivation. |
| `torque.task.is_derived` | bool | `True` for tasks created by `torque_derive`. |
| `torque.task.parent_task_id` | string | Parent task ID, empty for roots. |
| `torque.task.labels` | list | Labels (`["derived", "feature"]`, etc.). |
| `torque.task.group` | string | Task's group. |
| `torque.task.attachments` | list | Image attachments (legacy path). |
| `torque.task.artifacts` | list | Combined artifact set including images, file refs, logs, diffs, reports, snippets. |
| `torque.task.upstream_artifacts` | list | Direct-parent handoff artifacts for derived tasks. Empty for roots. |

The `is_derived` flag is the easiest way to make a prompt aware that it's a follow-up step:

```yaml
prompt: |
  {% if torque.task.is_derived %}
  This is a derived task at depth {{ torque.task.depth }} in a pipeline.
  A previous agent handed off this work to you. The parent task was
  `{{ torque.task.parent_task_id }}`.
  {% endif %}

  {{ TASK }}
```

### `torque.terminals` — companion terminal sessions

A list of the agent's child terminals. Each entry has `name`, `slug`, `current_path`, `current_process`, `current_branch`. Empty for newly created agents.

```yaml
prompt: |
  {{ TASK }}

  {% if torque.terminals %}
  You have these companion terminals available:
  {% for t in torque.terminals %}
  - **{{ t.name }}** at `{{ t.current_path }}`{% if t.current_process %} (running {{ t.current_process }}){% endif %}
  {% endfor %}
  Use them for tests, logs, or shell commands.
  {% endif %}
```

## Jinja2 features to remember

The full Jinja2 sandboxed environment is available. The features you'll reach for most:

**Conditionals:**

```yaml
{% if LANGUAGE == "python" %}
Use pytest for testing. Follow PEP 8.
{% elif LANGUAGE == "typescript" %}
Use vitest. Follow the project's ESLint config.
{% else %}
Run the project's existing test suite.
{% endif %}
```

**Loops:**

```yaml
Focus on these files:
{% for file in FILES.split(",") %}
- {{ file.strip() }}
{% endfor %}
```

**Filters:**

```yaml
Module: {{ MODULE | upper }}
Path: {{ PATH | replace("/", " > ") }}
Default: {{ TIMEOUT | default(30) }}
```

Only the `prompt` field is rendered through Jinja2. Action metadata (name, description, agent, transitions, labels) is static YAML — no rendering happens there.

## Preview before dispatch

Before you trust a template in production, render it. The task creation/edit modal has a **Preview prompt** button that runs the full render path with stub values for the `torque` namespace:

- `torque.context.is_clean = True` (so you see the "fresh agent" branch)
- All strings empty
- All lists empty
- All booleans `False`

This means previews always show the **most informative branch** of your conditionals. The agent will see less if it's on a follow-up dispatch — and that's deliberate, you want to verify the long-form prompt first.

From the CLI, you can also dump the rendered prompt:

```bash
torque task dispatch "Some task" -t feature/implement --dry-run
```

## A complete worked example

A research action that adapts to provider, worktree, and dispatch history:

```yaml
name: research
description: Research and report — adapts to fresh vs continuing dispatches
agent: researcher

labels:
  - research

transitions:
  - action: research
    when: more sub-questions to investigate
    target: self
  - ask: true
    when: needs human direction on what to investigate next

prompt: |
  {% if torque.context.is_clean %}
  You are a research agent. Your job is to investigate questions
  thoroughly and report findings concisely.

  ## Environment
  - Working directory: {{ torque.agent.directory }}
  {% if torque.worktree.active %}
  - Branch: `{{ torque.worktree.branch }}` (from `{{ torque.worktree.base_branch }}`)
  {% endif %}
  {% if torque.terminals %}
  - Companion terminals: {{ torque.terminals | map(attribute="name") | join(", ") }}
  {% endif %}

  ## How to research
  {% if torque.agent.type == "claude-code" %}
  Use Read, Grep, and Bash freely. Cite file paths and line numbers in
  your findings. Avoid Web tools unless explicitly necessary.
  {% else %}
  Read the codebase directly. Cite file paths and line numbers.
  {% endif %}

  ## Question
  {{ TASK }}

  Report findings as a short summary plus a list of citations. If you
  uncover sub-questions worth investigating, derive a follow-up
  research task with `torque_derive(action="research")`.
  {% else %}
  ## Next research question
  {{ TASK }}
  {% endif %}
```

The same template renders three different ways:

- **Fresh dispatch with worktree** — full system prompt + environment + Claude-Code-specific instructions.
- **Continuing dispatch** (via `target: self` derivation) — just the next question, the agent already has the rest in context.
- **Fresh dispatch on Codex** — full system prompt without the Claude-Code-specific tool guidance.

One template, three behaviors. That's the leverage of the `torque` namespace.

## Where to next

- [Actions](actions.md) — the YAML format around the prompt.
- [Pipelines](pipelines.md) — how `transitions` and `target` work, including the `ask` gate.
- [Tasks and threads](threads.md) — how the rendered prompts compose into multi-step work.
