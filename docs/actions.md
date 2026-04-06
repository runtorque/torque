# Actions & Templates

Loom now separates reusable agent configuration from reusable task prompts:

For the day-to-day "how do I use these together?" view, see the [Workflow Guide](workflow-guide.md).

- **Agent templates** define who does the work: provider, model, permissions, system prompt, worktree behavior, environment, and child terminals.
- **Actions** define what work to do: the rendered prompt, labels, and pipeline transitions.

When you dispatch a task, Loom resolves both pieces. The agent template creates the session, then the action prompt is rendered and sent to it.

## Why actions?

Without actions, you'd type the same instructions every time you dispatch a task. Actions let you:

- **Standardize prompts** --- define how your agents approach implementation, review, bug fixes, etc.
- **Parameterize work** --- use variables so one action handles many variations
- **Build pipelines** --- chain actions together so agents can hand off work automatically
- **Adapt to context** --- write prompts that change based on whether the agent is fresh or continuing prior work

## How actions fit into the workflow

In normal use, a task becomes dispatchable when you combine:

- a **task** on the board
- an **action** for the prompt
- optionally an **agent template** for launch settings

Example:

1. Create a task:

   ```bash
   loom task create "Add auth middleware" -g backend -t feature/implement
   ```

2. Dispatch it from the board UI, or create and dispatch in one CLI step:

   ```bash
   loom dispatch "Add auth middleware" -g backend -t feature/implement
   ```

3. Loom launches the agent, renders the action prompt, and sends the result.

4. If the action declares transitions, the agent can hand off the next step with `loom ai derive`.

The same action can be used in manual work, pipeline handoffs, or schedules. A schedule that fires a task with `-t feature/implement` goes through the same prompt-rendering path as a task you dispatched by hand.

## File format

Actions are YAML files with a `.yaml` extension. Here's a minimal action:

```yaml
name: fix
description: Fix a bug

prompt: |
  You are fixing a bug. Focus on understanding the root cause before writing code.

  {{ TASK }}
```

And here's one using most available fields:

```yaml
name: feature/implement
description: Implement a feature in an isolated worktree

agent:
  name_prefix: impl
  tab_color: "#3fb950"

worktree: true

labels:
  - feature

transitions:
  - action: feature/review
    when: implementation is complete and ready for review

prompt: |
  You are implementing a feature in an isolated worktree branch.

  {{ TASK }}

  ## Approach

  - Read the relevant code first
  - Write clean, minimal code
  - Add tests for new behavior
  - Run the test suite before finishing
```

### Fields reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| **name** | string | yes | Unique identifier. Supports namespaces: `feature/review` maps to `feature/review.yaml`. |
| **description** | string | no | One-line description shown in the UI action picker. |
| **prompt** | string | yes | The Jinja2 template rendered and sent to the agent. Must contain `{{ TASK }}`. |
| **agent** | string or object | no | Preferred: agent template name to use when the action spawns a new agent. Legacy inline agent mapping is still supported but deprecated. |
| **agent.name_prefix** | string | no | Legacy inline field. Prefix for the agent name (deprecated). |
| **agent.command** | string | no | Legacy inline field. Boot command override (deprecated). |
| **agent.directory** | string | no | Legacy inline field. Working directory override (deprecated). |
| **agent.profile** | string | no | Legacy inline field. iTerm2 profile override (deprecated). |
| **agent.shell** | string | no | Legacy inline field. Shell override (deprecated). |
| **agent.tab_color** | string | no | Legacy inline field. Hex tab color (deprecated). |
| **agent.env_vars** | object | no | Legacy inline field. Environment variables to set (deprecated). |
| **worktree** | boolean | no | If `true`, create an isolated git worktree for the agent. |
| **labels** | list | no | Labels applied to the task on the board. |
| **transitions** | list | no | Valid next actions for pipeline chaining (see [Pipelines](#pipelines)). |
| **max_depth** | integer | no | Override the global `max_pipeline_depth` for this action. |

## Where actions live

Loom searches two locations for action files. Project-local actions take precedence over global ones.

| Scope | Path | Version-controlled? |
|-------|------|---------------------|
| **Project** | `.loom/actions/` in your repo root | Yes (recommended) |
| **User** | `~/.loom/actions/` in your home directory | No |

Subdirectories create namespaces. The file `.loom/actions/feature/review.yaml` becomes the action `feature/review`.

If a project and user action have the same name, the project action wins. The user action is still visible in the UI (marked as "shadowed") but won't be used for dispatch.

### Installing the starter actions

The repo ships with example actions in `actions/`. Copy them to get started:

```bash
# Project-local (recommended — commit them with your repo)
cp -r actions/* .loom/actions/

# Or global (shared across all projects)
cp -r actions/* ~/.loom/actions/
```

## The prompt field

The `prompt` field is the heart of an action. It's a [Jinja2](https://jinja.palletsprojects.com/) template that gets rendered with variables before being sent to the agent.

### The `TASK` variable

Every prompt **must** contain `{{ TASK }}`. This is where the task description (what the user typed) gets inserted.

```yaml
prompt: |
  You are implementing a feature.

  {{ TASK }}

  Keep the diff focused.
```

If you dispatch this action with the task "Add dark mode to the settings page", the agent receives:

```
You are implementing a feature.

Add dark mode to the settings page

Keep the diff focused.
```

### Custom variables

You can add any variable to your prompt. Loom discovers them automatically from the Jinja2 template --- no declaration needed.

```yaml
prompt: |
  You are fixing a bug in the {{ MODULE }} module.

  {{ TASK }}

  Run the tests with: {{ TEST_COMMAND | default("make test") }}
```

This action exposes three variables:

- `TASK` --- always required, filled from the task description
- `MODULE` --- required, the user provides this when creating the task
- `TEST_COMMAND` --- optional, defaults to `make test` if not provided

The `| default("value")` filter makes a variable optional by providing a fallback value.

### Jinja2 features

The prompt field supports the full Jinja2 template language inside a sandboxed environment. You can use:

**Conditionals:**

```yaml
prompt: |
  {{ TASK }}

  {% if LANGUAGE == "python" %}
  Use pytest for testing. Follow PEP 8.
  {% elif LANGUAGE == "typescript" %}
  Use vitest for testing. Follow the project's ESLint config.
  {% else %}
  Run the project's existing test suite.
  {% endif %}
```

**Loops:**

```yaml
prompt: |
  {{ TASK }}

  Focus on these files:
  {% for file in FILES.split(",") %}
  - {{ file.strip() }}
  {% endfor %}
```

**Filters:**

```yaml
prompt: |
  Module: {{ MODULE | upper }}
  {{ TASK | replace("TODO", "DONE") }}
```

!!! note
    Only the `prompt` field is rendered through Jinja2. Agent templates are static YAML in v1; they do not render Jinja2 expressions.

## Agent templates

Agent templates live alongside actions but in a separate directory:

| Scope | Path |
|-------|------|
| **Project** | `.loom/agents/` |
| **User** | `~/.loom/agents/` |

They support the fields documented in the UI editor: provider, command override, model, permissions, system prompt, initial prompt, session resume, idle timeout, tab color, icon, worktree settings, environment variables, and child terminals.

Actions can reference templates directly:

```yaml
name: feature/research
agent: researcher

prompt: |
  {{ TASK }}
```

Legacy inline agent blocks still work:

```yaml
name: oneshot/fix
agent:
  command: codex
  directory: /repo

prompt: |
  {{ TASK }}
```

## The `loom` context namespace

When Loom dispatches a task, it injects a `loom` variable into the template context alongside `TASK` and your custom variables. This variable contains information about the agent, its history, the worktree, the current task, and available terminals.

This is particularly useful for writing prompts that adapt to whether the agent is starting fresh or continuing prior work.

### `loom.context` --- dispatch history

| Field | Type | Description |
|-------|------|-------------|
| `loom.context.is_clean` | bool | `True` if this is the first task dispatched to this agent. `False` if the agent has processed tasks before. |
| `loom.context.tasks_dispatched` | int | Number of tasks previously dispatched to this agent (0 on first dispatch). |
| `loom.context.previous_tasks` | list | Tasks still linked to this agent. Each entry has `task`, `lane`, and `action` fields. |

**Example --- conditional system prompt:**

```yaml
prompt: |
  {% if loom.context.is_clean %}
  You are a senior engineer. Review all code carefully and provide
  detailed, actionable feedback. Flag real problems, not style preferences.

  {{ TASK }}
  {% else %}
  {{ TASK }}
  {% endif %}
```

When the agent receives its first task, it gets the full system prompt with instructions. When a follow-up task is dispatched to the same agent (via `--self` or the UI), the agent already has the system prompt in context, so only the new task description is sent.

### `loom.agent` --- agent identity

| Field | Type | Description |
|-------|------|-------------|
| `loom.agent.name` | string | Agent display name (e.g., `impl-add-auth`). |
| `loom.agent.slug` | string | URL-friendly identifier (e.g., `impl-add-auth`). |
| `loom.agent.type` | string | Agent type: `claude-code`, `codex`, `gemini-cli`, or empty. |
| `loom.agent.group` | string | Group the agent belongs to. |
| `loom.agent.directory` | string | Working directory. |

**Example --- agent-type-aware instructions:**

```yaml
prompt: |
  {{ TASK }}

  {% if loom.agent.type == "claude-code" %}
  Use Claude Code's built-in tools for file editing and searches.
  {% else %}
  Edit files directly. Use grep and find for navigation.
  {% endif %}
```

### `loom.worktree` --- git worktree state

| Field | Type | Description |
|-------|------|-------------|
| `loom.worktree.active` | bool | Whether a worktree exists for this agent. |
| `loom.worktree.path` | string | Absolute path to the worktree directory. |
| `loom.worktree.branch` | string | Worktree branch name (e.g., `loom/impl-a1b2c3d4`). |
| `loom.worktree.base_branch` | string | Branch the worktree was forked from (e.g., `main`). |
| `loom.worktree.dirty` | bool | Whether there are uncommitted changes. |
| `loom.worktree.diff` | object | Change stats: `files`, `insertions`, `deletions`. |
| `loom.worktree.checkpoints` | int | Number of checkpoint commits on the worktree branch. |

**Example --- worktree-aware review prompt:**

```yaml
prompt: |
  {% if loom.worktree.active %}
  You're reviewing changes on branch `{{ loom.worktree.branch }}`,
  forked from `{{ loom.worktree.base_branch }}`.
  {% if loom.worktree.dirty %}
  Warning: there are uncommitted changes
  ({{ loom.worktree.diff.files }} files,
  +{{ loom.worktree.diff.insertions }}/-{{ loom.worktree.diff.deletions }}).
  {% endif %}
  {% endif %}

  {{ TASK }}
```

### `loom.task` --- current task metadata

| Field | Type | Description |
|-------|------|-------------|
| `loom.task.id` | string | Task ID. |
| `loom.task.slug` | string | Task slug. |
| `loom.task.depth` | int | Pipeline depth (0 for root tasks, increments per derivation). |
| `loom.task.is_derived` | bool | Whether this task was created by `loom ai derive`. |
| `loom.task.parent_task_id` | string | ID of the parent task (empty for root tasks). |
| `loom.task.labels` | list | Task labels (e.g., `["derived", "feature"]`). |
| `loom.task.group` | string | Task's group name. |

**Example --- pipeline-aware instructions:**

```yaml
prompt: |
  {% if loom.task.is_derived %}
  This is a follow-up task at depth {{ loom.task.depth }} in the pipeline.
  A previous agent handed off this work to you.
  {% endif %}

  {{ TASK }}
```

### `loom.terminals` --- child terminal sessions

A list of the agent's child terminals. Each entry has `name`, `slug`, `current_path`, `current_process`, and `current_branch`. Empty for newly created agents.

**Example --- terminal awareness:**

```yaml
prompt: |
  {{ TASK }}

  {% if loom.terminals %}
  You have these companion terminals available:
  {% for t in loom.terminals %}
  - **{{ t.name }}**{% if t.current_process %} (running: {{ t.current_process }}){% endif %}
  {% endfor %}
  {% endif %}
```

### Preview behavior

When you preview a prompt in the UI (before dispatching), there's no real agent or task yet. Loom uses safe defaults: `loom.context.is_clean` is `True`, all strings are empty, all lists are empty. This means previews always show the "full prompt" branch of your conditionals, which is the most informative view.

## Dispatching tasks

There are three ways to dispatch a task with an action:

### From the board UI

1. Create a task on the board (or click **+ Add task** in a lane)
2. Select an action from the action picker
3. Fill in any variables the action requires
4. Click **Dispatch**

Loom creates a new agent, renders the prompt, and sends it after the agent boots.

### From the CLI

```bash
# One-liner: create + dispatch
loom dispatch "Add dark mode" -t feature/implement

# With variables
loom dispatch "Fix the flaky test" -t oneshot/fix -v MODULE=auth -v TEST_COMMAND="pytest tests/auth"
```

### From an agent (pipeline derivation)

An agent that's finished its task can derive a new task and dispatch it:

```bash
# Derive to a new agent (default)
loom ai derive "Review the implementation" -t feature/review

# Derive to a specific existing agent
loom ai derive "Fix the 3 issues found" -t feature/fix-review --agent impl-add-auth

# Derive to yourself (same session, preserves context)
loom ai derive "Now add tests for the validation" -t feature/implement --self
```

See [Derive-to-agent](#derive-to-agent) for details.

## Pipelines

Pipelines are multi-step workflows where agents hand off work to each other. They're not declared as a separate object --- they emerge from actions' `transitions` fields.

### Declaring transitions

Each action can list the actions it's allowed to transition to:

```yaml
# feature/implement.yaml
transitions:
  - action: feature/review
    when: implementation is complete and ready for review
```

```yaml
# feature/review.yaml
transitions:
  - action: feature/fix-review
    when: issues were found that need to be fixed
  - ask: true
    when: changes look correct but need human sign-off before merging
```

```yaml
# feature/fix-review.yaml
transitions:
  - action: feature/review
    when: all review issues have been addressed
```

This creates a pipeline: **implement → review → (fix → review →)* done**.

The `when` field is documentation --- it's included in the agent's postscript so it knows when each transition is appropriate.

### How agents use transitions

When Loom dispatches a task that has transitions, it appends a postscript to the prompt telling the agent which `loom ai` commands are available:

```
Report your progress with these commands:
- `loom ai done` — task complete, no follow-up needed
- `loom ai derive "description" -t feature/review` — implementation is complete and ready for review
- `loom ai blocked "reason"` — need user input
- `loom ai error "message"` — unrecoverable error
```

The agent reads these instructions and calls the appropriate command when it's done. The server validates that the transition is allowed before dispatching.

### The `ask` transition

The `ask` transition is a human-in-the-loop gate. When an agent calls `loom ai ask "question"`, the derived task lands in the **Backlog** lane with a `human` label instead of being dispatched automatically. A human reviews the question, optionally edits the task, and dispatches it manually.

```yaml
transitions:
  - ask: true
    when: changes look correct but need human approval
```

### Depth limits

Pipelines have a depth limit to prevent runaway chains. The default is 10 (configurable in global settings as `max_pipeline_depth`). Individual actions can override this with the `max_depth` field:

```yaml
max_depth: 5
```

When the limit is reached, the agent gets an error and the task is flagged with `needs_attention`.

### Viewing pipelines

```bash
# List all pipelines discovered from action transitions
loom pipeline list

# Show a specific pipeline's structure
loom pipeline show feature/implement

# Show the full task chain for a specific task
loom task chain <task-slug>
```

In the board UI, derived tasks show a chain indicator (`↳ depth N · from: parent-task`). Right-click a task and select **View pipeline** to see the full chain.

## Derive-to-agent

By default, `loom ai derive` creates a new agent for each derived task. But sometimes you want the next task to run in an existing agent that already has context.

### `--self` --- continue in the same agent

When an agent derives a task to itself, the new prompt arrives in the same terminal session after a short delay. The agent keeps its full conversation context.

```bash
loom ai derive "Now add tests for the validation" -t feature/implement --self
```

This is useful for multi-phase actions where the same agent should handle sequential steps:

```yaml
# An action that handles both implementation and testing
prompt: |
  {% if loom.context.is_clean %}
  You are implementing a feature. Write clean code and commit when done.
  After implementation, you'll receive a follow-up task to add tests.

  {{ TASK }}
  {% else %}
  Continue working in this session.

  {{ TASK }}
  {% endif %}
```

### `--agent` --- dispatch to a specific agent

When Agent B (e.g., a reviewer) finds issues, it can send the fix task back to Agent A (the original implementer) which still has the codebase in context:

```bash
loom ai derive "Fix the 3 auth issues I found" -t feature/fix-review --agent impl-add-auth
```

The identifier can be a slug, name, ID, or ID prefix. The new prompt is rendered with `loom.context.is_clean = False` since the target agent has processed tasks before.

### Worktree inheritance

When deriving to a specific agent, the worktree is inherited from the **target** agent. The new task runs in the same worktree the agent is already working in.

When deriving to a new agent (the default), the worktree is inherited from the **calling** agent, so the new agent sees the same code.

## Writing effective prompts

### Structure your prompts

Good prompts follow a consistent structure:

```yaml
prompt: |
  # Role and context
  You are a code reviewer. You're thorough but fair.

  # The task
  {{ TASK }}

  # Approach (step-by-step instructions)
  1. Read the full diff
  2. Check correctness
  3. Run the test suite
  4. Report findings

  # Constraints
  - Flag real problems, not style preferences
  - Keep suggestions actionable
```

### Use conditionals for context-aware prompts

The most powerful pattern is using `loom.context.is_clean` to write prompts that work both as initial dispatches and as follow-ups:

```yaml
prompt: |
  {% if loom.context.is_clean %}
  You are a senior engineer working on the {{ loom.worktree.base_branch | default("main") }} branch.
  Your job is to implement features cleanly and ship them with tests.

  ## Environment
  - Working directory: {{ loom.agent.directory }}
  {% if loom.worktree.active %}
  - Branch: `{{ loom.worktree.branch }}` (forked from `{{ loom.worktree.base_branch }}`)
  {% endif %}
  {% if loom.terminals %}
  - Terminals: {{ loom.terminals | map(attribute="name") | join(", ") }}
  {% endif %}

  ## Task
  {{ TASK }}

  ## Guidelines
  - Read relevant code before writing
  - Write tests for new behavior
  - Keep diffs focused
  {% else %}
  {{ TASK }}
  {% endif %}
```

On the first dispatch, the agent gets the full system prompt with environment details and guidelines. On follow-ups, it just gets the new task --- the agent already knows the guidelines and has the codebase in context.

### Keep prompts focused

Each action should do one thing well. Don't try to combine implementation and review in one action --- split them into separate actions connected by transitions.

```yaml
# Good: focused actions
# feature/implement.yaml → feature/review.yaml → feature/fix-review.yaml

# Avoid: monolithic prompts that try to do everything
```

### Use variables for reusable actions

If you find yourself copying actions that differ only in a few details, use variables instead:

```yaml
prompt: |
  You are working on the {{ COMPONENT | default("frontend") }} of the application.
  The test command is: {{ TEST_CMD | default("npm test") }}

  {{ TASK }}
```

### Test with preview

Before dispatching, use the preview button in the task modal to see the fully rendered prompt. This shows you exactly what the agent will receive, with all variables substituted and conditionals resolved.

From the CLI:

```bash
loom task create "Add dark mode" -a feature/implement
# Then open the task in the UI and click Preview
```

## Managing actions

### From the UI

The **Actions** panel (toggle with ++a++ or the panel button) provides a visual editor:

- **Action picker** --- dropdown with all available actions, grouped by Project and User scope
- **Prompt editor** --- syntax-highlighted textarea with Jinja2 support
- **Variable discovery** --- auto-detected variables shown below the editor
- **Transitions editor** --- add/edit/remove transitions with an action picker dropdown
- **Save/Duplicate/Delete** --- full CRUD with scope picker (Project or User)
- **Pipelines view** --- visual canvas showing action nodes connected by transition edges

### From the CLI

```bash
loom action list                    # list all actions
loom action show feature/review     # show action details
loom action create my-action        # create a new action
```

### Creating actions manually

Create a `.yaml` file in `.loom/actions/` (project) or `~/.loom/actions/` (global):

```bash
mkdir -p .loom/actions
cat > .loom/actions/quick-fix.yaml << 'EOF'
name: quick-fix
description: Quick targeted fix

agent:
  name_prefix: fix
  tab_color: "#f85149"

prompt: |
  You are fixing a specific issue. Be surgical — change only what's needed.

  {{ TASK }}

  - Reproduce the issue first
  - Find the root cause
  - Write a failing test, then fix it
  - Run the test suite
EOF
```

Loom picks it up immediately --- no restart needed.

## Examples

### Oneshot bug fix

A simple action for quick, targeted fixes with no pipeline:

```yaml
name: oneshot/fix
description: Diagnose and fix a bug directly on the current branch

agent:
  name_prefix: fix
  tab_color: "#f85149"

labels:
  - bugfix

prompt: |
  You are diagnosing and fixing a bug. Focus on understanding the
  root cause before writing any code.

  ## Bug report

  {{ TASK }}

  ## Approach

  1. Reproduce first — confirm the failure before changing anything
  2. Find the root cause — don't just patch symptoms
  3. Write a failing test that captures the bug, then make it pass
  4. Keep the fix minimal — no drive-by refactors
  5. Run the full test suite for regressions
```

### Implement → review → fix pipeline

Three actions that form a review cycle:

**`.loom/actions/feature/implement.yaml`:**

```yaml
name: feature/implement
description: Implement a feature in an isolated worktree

agent:
  name_prefix: impl
  tab_color: "#3fb950"

worktree: true
labels:
  - feature

transitions:
  - action: feature/review
    when: implementation is complete and ready for review

prompt: |
  You are implementing a feature in an isolated worktree branch.
  Your work will be reviewed by a separate agent when you're done.

  {{ TASK }}

  - Read the relevant code first
  - Write clean, minimal code
  - Add tests for new behavior
  - Run the test suite before finishing
```

**`.loom/actions/feature/review.yaml`:**

```yaml
name: feature/review
description: Review a branch for issues

agent:
  name_prefix: review
  tab_color: "#a371f7"

labels:
  - review

transitions:
  - action: feature/fix-review
    when: issues were found that need to be fixed
  - ask: true
    when: changes look correct but need human sign-off

prompt: |
  {% if loom.context.is_clean %}
  You are a code reviewer. A previous agent implemented changes and
  your job is to evaluate whether the work is correct, safe, and
  ready to merge.

  ## What was implemented

  {{ TASK }}

  ## Review process

  1. Read the full diff
  2. Check correctness and edge cases
  3. Run the test suite
  4. Flag issues with file, severity, problem, and fix

  ## Verdict

  End with: **Ship**, **Ship with fixes**, or **Needs rework**.
  {% else %}
  Re-review after fixes:

  {{ TASK }}
  {% endif %}
```

**`.loom/actions/feature/fix-review.yaml`:**

```yaml
name: feature/fix-review
description: Fix issues found during code review

agent:
  name_prefix: fix
  tab_color: "#f85149"

worktree: true
labels:
  - review-fix

transitions:
  - action: feature/review
    when: all review issues have been addressed

prompt: |
  {% if loom.context.is_clean %}
  You are fixing issues found by a code reviewer on this worktree
  branch. Address every critical and warning issue. Nits are optional.

  {{ TASK }}

  - Do not introduce new functionality
  - Run the test suite after fixes
  {% else %}
  More review feedback to address:

  {{ TASK }}
  {% endif %}
```

### Context-preserving multi-phase action

An action designed to be dispatched multiple times to the same agent with `--self`:

```yaml
name: iterative
description: Iterative task that adapts to agent context

agent:
  name_prefix: iter
  tab_color: "#58a6ff"

prompt: |
  {% if loom.context.is_clean %}
  You are working on a multi-step task. You'll receive the first
  step now and follow-up steps as new messages.

  ## Environment
  {% if loom.worktree.active %}
  - Branch: `{{ loom.worktree.branch }}`
  - Base: `{{ loom.worktree.base_branch }}`
  {% endif %}
  - Directory: {{ loom.agent.directory }}

  ## Current step
  {{ TASK }}

  ## Guidelines
  - Commit after each step
  - Run tests before finishing each step
  - Keep changes focused to the current step
  {% else %}
  ## Next step
  {{ TASK }}
  {% endif %}
```

Usage:

```bash
# First dispatch — full instructions
loom dispatch "Add the User model with email and name fields" -t iterative

# Agent finishes, then derives to itself
loom ai derive "Add the API endpoints for CRUD operations" -t iterative --self

# Agent finishes again, derives once more
loom ai derive "Add input validation and error handling" -t iterative --self
```

Each follow-up only sends the new step. The agent retains the full context of what it built in previous steps.
