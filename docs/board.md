# Task Board

The task board is a Kanban-style interface for organizing work. Tasks move through lanes as agents work on them. The board integrates with [actions](actions.md) and [pipelines](actions.md#pipelines) so agents can report progress, derive follow-up tasks, and hand off work automatically.

For the day-to-day narrative from task creation through completion, start with the [Workflow Guide](workflow-guide.md).

## Lanes

Lanes are columns on the board. Tasks move between lanes as their status changes. Loom ships with four default lanes:

| Lane | Purpose |
|------|---------|
| **Backlog** | Tasks waiting to be started. New tasks land here by default. |
| **To Do** | Planned tasks, ready to be dispatched. |
| **In Progress** | Actively assigned to an agent. Tasks move here on dispatch. |
| **Done** | Completed tasks. |

**Backlog** and **In Progress** are reserved --- they can't be renamed or deleted. You can add, rename, reorder, and remove other lanes freely.

### Managing lanes

From the UI, open the board panel and use the lane tab headers. From the CLI:

```bash
loom board lanes                            # list lanes with task counts
loom board add "Deploy to staging" -l "To Do"  # add task to a specific lane
loom board move fix-login -l Done           # move task between lanes
```

## Tasks

A task is a unit of work with a description, optional action, optional agent template, and metadata. Tasks can be linked to agents and organized into pipeline chains.

### Creating tasks

**From the board UI:**

1. Click **+ Add task** at the top of any lane
2. Type the task description and press ++enter++
3. For action-based tasks, click **From action** to open the action picker

**From the CLI:**

```bash
# Simple task
loom task create "Fix the login redirect bug" -g backend

# Task with an action (renders a prompt template when dispatched)
loom task create "Add input validation" -t feature/implement -g backend

# Task with action variables
loom task create "Fix auth tests" -t oneshot/fix -v MODULE=auth -v TEST_CMD=pytest
```

### Task fields

| Field | Description |
|-------|-------------|
| **Task** | The description --- what needs to be done. |
| **Group** | Which group this task belongs to. |
| **Lane** | Current board lane (Backlog, To Do, In Progress, Done, or custom). |
| **Action** | Optional action used to render the dispatch prompt. |
| **Agent template** | Optional template used when dispatch creates a new agent. |
| **Variables** | Values for the action's template variables. |
| **Assignee** | Optional assignee name. |
| **Labels** | Tags for filtering and categorization. |
| **Agent** | The concrete agent working on this task (set on dispatch). |
| **Attachments** | Legacy image attachments included in prompts as file paths. |
| **Artifacts** | Structured artifact metadata such as logs, diffs, reports, snippets, generated docs, and file references. |
| **Dependencies** | Other tasks that must be in **Done** before this task can be dispatched. |
| **Scheduled time** | Optional future time when Loom should auto-dispatch this task. |

### Editing tasks

**From the UI:** Right-click a task card and select **Edit**, or double-click it.

**From the CLI:**

```bash
loom task edit add-validation -t "Add input validation to all API endpoints"
loom task edit add-validation --action feature/implement
loom task edit add-validation -l feature,priority
```

Without inline flags, `loom task edit` opens your `$EDITOR` with the task as YAML.

### Moving tasks

Drag task cards between lanes in the UI, or use the CLI:

```bash
loom task move add-validation -l "In Progress"
loom task move add-validation -l Done
```

## Dependencies

Dependencies let you model "not yet" without losing the task. A task with dependencies can live in **Backlog** or **To Do**, but Loom will not dispatch it until every dependency is in **Done**.

This is useful for cases like:

- deploy only after review is complete
- start QA only after implementation is complete
- run migration only after backup is verified

### Adding dependencies

Use the task editor in the UI, or create/edit the task from the CLI:

```bash
loom task create "Deploy auth middleware" -g backend --depends-on review-auth
loom task edit deploy-auth-middleware --depends-on review-auth,run-auth-tests
```

### How blocked tasks appear

- In the board UI, blocked tasks show a lock badge.
- The context-menu dispatch action is disabled until the dependency chain is clear.
- If a completed dependency is moved back out of **Done**, Loom re-blocks dependent tasks that are not yet finished.

## Dispatching

Dispatching connects a task to an agent. Loom creates (or reuses) an agent, links the task, moves it to the dispatch lane (default: **In Progress**), renders the prompt, and sends it.

### From the board UI

Right-click a task card and select **Dispatch**. Loom creates a new agent in the task's group, using group defaults, the selected agent template (if any), and the action (if any).

### From the CLI

```bash
# Create and dispatch in one step
loom dispatch "Add dark mode" -t feature/implement -g frontend

# Dispatch and wait for completion
loom dispatch "Fix the bug" -t oneshot/fix -g backend -w
```

### What happens during dispatch

1. **Agent creation** --- a new agent is created with the task's slug as its name. Settings come from the group configuration, the task's agent template, or the action's `agent` template reference.
2. **Worktree** --- if the group has `git_worktree` enabled, an isolated worktree is created for the agent.
3. **Task linking** --- the task's `agent_id` is set and its lane changes to the dispatch lane.
4. **Prompt rendering** --- if the task has an action, Loom renders the prompt template with `{{ TASK }}`, action variables, and the [loom context namespace](actions.md#the-loom-context-namespace).
5. **Artifact shaping** --- Loom appends safe artifact references after the rendered task prompt. Images stay on the legacy `## Attached images` path, while structured artifacts are added with type-aware summaries or references.
6. **Prompt delivery** --- the rendered prompt is sent to the agent's terminal. New agents get a 2-second boot delay first.

### Dispatch overlap warnings

Before Loom dispatches work, it now runs deterministic overlap checks against open work in the same group. The first version uses:

- shared files or module areas from live worktree diffs
- same task family or action
- shared worktree lineage
- recent branch history on the same surface
- queued work already linked to the same surface

The board surfaces human-actionable overlap badges on task cards.

- **Overlap notice** remains available to Loom/Weaver internals, but is not shown on board task cards because it is advisory-only.
- **Overlap warning** means Loom found active or queued work on the same surface and asks for confirmation before dispatching.
- **Overlap conflict** means Loom found a strong collision signal, such as the same live branch or direct file overlap, and requires confirmation before dispatching.

Same-agent follow-up queueing is treated as sequential work and is downgraded compared with multi-agent parallel dispatch on the same surface.

### Dispatching to an existing agent

You can dispatch a task to an agent that's already running instead of creating a new one. Select the agent in the dispatch dialog (UI), or use `loom ai derive --agent` / `--self` from within a pipeline.

When dispatching to an existing agent, `loom.context.is_clean` is `False` in the template, so action prompts can send an abbreviated version (see [Actions & Templates](actions.md#the-loom-context-namespace)).

## Scheduled work

Loom supports two ways to start work later:

### Scheduled tasks

A scheduled task is a normal board task with a future `scheduled_at` time. The task stays on the board until that time arrives, then Loom dispatches that same task automatically.

Use this when you already know the exact task you want to run:

```bash
loom task create "Kick off release checklist" -g ops --at "tomorrow 09:00"
```

The task keeps all of its normal metadata:

- action
- action variables
- labels
- dependencies

### Schedules

A schedule is a reusable trigger that creates a fresh task each time it fires and dispatches it immediately. Schedules can be:

- **one-shot** with `--at`
- **recurring** with `--cron`

Example:

```bash
loom schedule create weekly-deps \
  -g backend \
  --cron "0 9 * * 1" \
  --task "Weekly dependency update {date}" \
  -t maintenance/deps
```

Useful placeholders for `--task` are `{date}`, `{time}`, and `{datetime}`.

In the board UI, schedules live in the **Schedules** view. From there you can inspect them, enable or disable them, edit them, or trigger them manually with **Run now**.

See [CLI Reference](cli.md#schedule) for the full command set.

## Agent reporting

Once an agent is working on a task, it can report status back to Loom using `loom ai` commands. These are designed to be called by AI agents (like Claude Code) from within a Loom-managed session.

| Command | Effect |
|---------|--------|
| `loom ai done` | Moves task to **Done**. Agent stays linked. |
| `loom ai ready` | Moves task to **Done** and unlinks the agent (available for new tasks). |
| `loom ai blocked "reason"` | Adds `blocked` label, flags agent as needing attention. |
| `loom ai error "message"` | Adds `error` label, flags agent as needing attention. |
| `loom ai progress "message"` | Updates the activity detail shown in the UI. |
| `loom ai derive "desc" -t action` | Marks task as Done, creates a derived task, and dispatches it. |
| `loom ai ask "question"` | Creates a derived task in Backlog for human review. |

When a task is dispatched with an action that has [transitions](actions.md#pipelines), the available `loom ai` commands are appended to the prompt as a postscript so the agent knows what reporting options it has.

### The `done` vs `ready` distinction

- **`done`** --- task is complete, agent stays linked. Use this when the agent might receive follow-up work (via `--self` or `--agent` dispatch).
- **`ready`** --- task is complete, agent is released. The agent-task link is cleared, signaling that the agent is available for independent work.

## Pipeline tasks

Tasks can form chains through derivation. When an agent calls `loom ai derive`, it creates a child task linked to the parent via `parent_task_id`. The full chain is queryable:

```bash
loom task chain add-dark-mode
```

This shows every task in the pipeline with its depth, lane, and linked agent.

In the board UI, derived tasks show a chain indicator: `depth N from: parent-task`. Right-click a derived task and select **View pipeline** to see the full chain as a thread.

### Pipeline fields

| Field | Description |
|-------|-------------|
| `parent_task_id` | The task this was derived from (empty for root tasks). |
| `pipeline_depth` | 0 for root tasks, incremented per derivation. |
| `pipeline_root_id` | ID of the chain's root task. |

### Label conventions

Loom applies labels automatically during pipeline operations:

| Label | Meaning |
|-------|---------|
| `derived` | Task was created via `loom ai derive`. |
| `human` | Task requires human review (from `loom ai ask`). |
| `blocked` | Agent is blocked and needs input. |
| `error` | Agent encountered an unrecoverable error. |
| `depth-limit` | Pipeline depth limit was reached. |

## Inline task creation

The board supports inline task creation directly in each lane:

1. Click **+ Add task** at the top of a lane
2. An auto-growing textarea appears
3. Type the task description and press ++enter++ to create
4. Press ++shift+enter++ for a newline within the description
5. Press ++escape++ to cancel (draft text is preserved if you blur and return)

Click **From action** to open an action picker overlay. Selecting an action opens the full task creation modal with the action pre-selected and its variable fields ready to fill in.

## Filtering and viewing

```bash
loom task list                          # all tasks grouped by lane
loom task list -l "In Progress"         # filter by lane
loom task list -g backend               # filter by group
loom task list --label review           # filter by label
loom task show add-dark-mode            # full details for one task
```

The board panel in the UI supports the same filtering via lane tabs.
