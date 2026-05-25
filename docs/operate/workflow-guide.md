# Workflow Guide

This guide explains how work moves through Torque from the moment you capture a task to the moment the work is complete. It is the best place to start if you want to understand the day-to-day workflow without reading source code.

For deeper reference, see [Task Board](../tasks/board.md), [Task Lifecycle](../tasks/lifecycle.md), [Actions & Roles](../tasks/actions.md), [Agent Roles](../team/workers.md), [Worktrees](../tasks/worktrees.md), and the [CLI Reference](../reference/cli.md).

## The workflow at a glance

Torque's workflow has seven moving parts:

- **Tasks** are the units of work that appear on the board.
- **Lanes** show where each task is in its lifecycle.
- **Actions** define the prompt an agent receives when a task is dispatched.
- **Agent roles** define how the agent is launched.
- **Dispatch** links a task to an agent and sends the work.
- **Pipelines** let agents hand follow-up work to other agents.
- **Schedules** create or dispatch work automatically at a future time.

If you only remember one rule, remember this: Torque keeps the board as the source of truth. Whether work is created by a human, an agent, or a schedule, it still appears as tasks moving through the same lanes.

## 1. Capture work on the board

Most work starts as a task in **Backlog**. A task should describe one concrete piece of work:

- "Fix the login redirect bug"
- "Review the auth middleware changes"
- "Run the weekly dependency update"

You can create tasks from the board UI or from the CLI:

```bash
torque task create "Fix the login redirect bug" -g backend
torque task create "Review the auth middleware changes" -g backend
```

At creation time, you can also add workflow structure:

- **Action** if the task should use a reusable prompt
- **Variables** if the action needs inputs like `MODULE=auth`
- **Labels** for filtering
- **Dependencies** if this task must wait for another task to finish
- **Scheduled time** if the task should dispatch later

Example:

```bash
torque task create "Review auth middleware" \
  -g backend \
  -t feature/review \
  --depends-on add-auth-middleware
```

For board mechanics and task fields, see [Task Board](../tasks/board.md).

## 2. Decide how the work should run

Before dispatching, decide two separate things:

- **What should the agent do?** Use an [action](../tasks/actions.md).
- **Who should do it, and with what runtime setup?** Use an [agent role](../team/workers.md).

This split matters in daily use:

- An **action** can describe an implementation step, review step, fix step, or research step.
- An **agent role** can choose the provider, model, permissions, icon, worktree behavior, environment variables, companion terminals, and optional worker preamble.

Example action + role pairing:

- `feature/implement` tells the agent how to implement the work.
- `researcher` or `reviewer` tells Torque how to launch that agent.

If a task has no action, Torque can still dispatch it. In that case the task text is sent as raw text instead of a rendered prompt.

For the full action format, examples, and variable system, see [Actions & Roles](../tasks/actions.md). For launch presets, see [Agent Roles](../team/workers.md).

## 3. Plan the board before dispatch

Lanes are the planning surface. The built-in lanes are:

- **Backlog** for unstarted work
- **To Do** for work that is ready and prioritized
- **In Progress** for active work
- **Done** for completed work

Many teams use Torque like this:

1. Capture everything in **Backlog**.
2. Move the next set of tasks into **To Do**.
3. Dispatch tasks when they are ready.
4. Let Torque move active work into **In Progress**.
5. Let agents complete tasks into **Done**.

Two common board controls matter before dispatch:

### Dependencies

Dependencies block dispatch until other tasks are done. Use them when the task is valid, but the timing is not.

Example:

```bash
torque task create "Deploy auth changes" \
  -g backend \
  --depends-on review-auth,run-auth-tests
```

In the UI, dependency-blocked tasks show a lock badge and the dispatch action is disabled until every dependency is in **Done**.

### Scheduling

Use scheduling when the task should not start yet, or when the same work should be created repeatedly.

There are two scheduling modes:

- **Scheduled task**: one existing task waits until a future time, then Torque dispatches that task.
- **Schedule**: a recurring or one-shot rule creates a fresh task when it fires, then dispatches it.

Use a scheduled task when you already know the exact work item:

```bash
torque task create "Kick off release checklist" -g ops --at "tomorrow 09:00"
```

Use a schedule when the work repeats:

```bash
torque schedule create weekly-deps \
  -g backend \
  --cron "0 9 * * 1" \
  --task "Weekly dependency update {date}" \
  -t maintenance/deps
```

For schedule behavior and board UI details, see [Task Board](../tasks/board.md#scheduled-work). For command syntax, see [CLI Reference](../reference/cli.md#schedule).

## 4. Dispatch the task

Dispatch is the moment a task becomes active. Torque:

1. Creates or selects an agent
2. Applies the group defaults and any agent role
3. Creates a worktree if configured
4. Renders the action prompt if the task has an action
5. Links the task to the agent
6. Moves the task into **In Progress**

You can dispatch from the board UI or with the CLI:

```bash
torque task dispatch "Add dark mode" -t feature/implement -g frontend
```

If the task depends on unfinished tasks, dispatch is blocked. If the task is scheduled, Torque performs the same dispatch automatically when the scheduled time arrives.

For the full dispatch reference, see [Task Board](../tasks/board.md#dispatching).

## 5. Work in progress: updates, blockers, and human input

Once an agent is working on a task, the agent can report back to Torque with MCP tools:

- `torque_progress(message="message")` updates the activity detail
- `torque_blocked(reason="reason")` marks the task as blocked
- `torque_error(message="message")` marks the task as failed and needing attention
- `torque_done(message="summary")` completes the current task
- `torque_ready()` completes the task and releases the agent for future work
- `torque_verify(state="passed", tests_run="...", notes="...")` records deploy/restart/smoke verification status when relevant
- `torque_ask(question="question", description="details")` creates a blocking human-in-the-loop follow-up task in **Backlog** when the agent cannot continue safely without a decision or approval

These updates make the board readable without opening each agent session. A person scanning the board can see which tasks are moving, which are blocked, and which are waiting on a human decision.

When the checkpoint needs to be recorded by Torque itself instead of the active agent, use `torque task verify ...` or `engineer_task_verify(...)` to mark deploy/restart attempted, smoke passed or failed, and any remaining verification notes.

`torque_ask` is not a general status or suggestion channel. If the agent can keep moving, it should keep moving and report context through `torque_progress`, `torque_done`, `torque_blocked`, or derived-task context instead of pausing the task.

For the lane and completion model, see [Task Lifecycle](../tasks/lifecycle.md).

## 6. Hand work off with pipelines

Pipelines are Torque's way of turning one task into a multi-step workflow. They are built from action transitions.

Example:

- `feature/implement` can derive to `feature/review`
- `feature/review` can derive to `feature/fix-review`
- `feature/fix-review` can derive back to `feature/review`

That creates a workflow such as:

1. Implement the feature
2. Review the result
3. Fix issues if needed
4. Review again
5. Mark the work done

From the agent's perspective, this is usually one command:

```text
torque_derive(
  description="Review the auth middleware implementation",
  action="feature/review",
)
```

What Torque does next:

- The current task stays visible on the board
- A new derived task is created beneath it
- The parent task's status badge changes to show the current stage
- The new task is dispatched according to the selected transition

This is how Torque keeps the board forward-moving without reopening old tasks. The original task remains the top-level story; the derived tasks show the detailed handoff chain.

For transition syntax and pipeline examples, see [Actions & Roles](../tasks/actions.md#pipelines). For how completion cascades back up the chain, see [Task Lifecycle](../tasks/lifecycle.md#cascade-completion).

## 7. Complete the work

Work is complete when the active task reaches **Done** and any required follow-up tasks are resolved.

There are two common endings:

- A single task finishes directly with `torque_done(message="summary")`.
- A pipeline finishes when its last derived task is marked done and Torque cascades completion back up the chain.

If you use worktrees, completion is often followed by a git step such as review, merge, checkpoint cleanup, or worktree removal. That git flow is separate from the board flow, which is why the board can remain clean even when code review takes longer.

See [Worktrees](../tasks/worktrees.md) for the isolated-branch workflow.

## End-to-end example

Here is a typical day-to-day flow:

1. Create the main task:

   ```bash
   torque task create "Add auth middleware" -g backend -t feature/implement
   ```

2. Create a dependent follow-up task:

   ```bash
   torque task create "Deploy auth middleware" -g backend --depends-on add-auth-middleware
   ```

3. Dispatch the implementation task from the board UI.

4. The implementation agent finishes and derives a review task:

   ```text
   torque_derive(
     description="Review auth middleware",
     action="feature/review",
   )
   ```

5. The reviewer either:

- calls `torque_done(message="summary")` if the work is good
- calls `torque_derive(description="...", action="feature/fix-review")` if fixes are needed
- calls `torque_ask(question="...", description="...")` if a blocking human decision or approval is required before work can continue

6. Once the implementation chain reaches **Done**, the dependent deployment task can be dispatched.

This is the core Torque workflow: capture work, structure it with actions and templates, dispatch it, let agents report progress and hand off follow-up work, then finish cleanly with the board as the shared source of truth.
