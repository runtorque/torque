# Core concepts

A single-page glossary of every term Torque uses. Each entry links to the page where the concept is fully developed. Keep this open in another tab while you read the rest of the docs.

## Group

A container for agents and terminals. Represents one unit of work — a feature, a project area, a bug fix. Each group can have its own settings, its own Engineer, and its own default worktree behavior. Groups appear as collapsible cards in the Torque workspace (desktop or browser).

→ [Group settings](../operate/group-settings.md)

## Agent

An AI coding session. Every agent has a **kind**:

- **Worker** — the agent that does the actual code-writing work. Ephemeral. Created on dispatch, often closed after merge. → [Workers](../team/workers.md)
- **Engineer** — the per-group orchestrator. Persistent. Coordinates Workers, keeps a journal, dispatches in waves, reviews before merging. → [Engineers](../team/engineers.md)
- **Architect** — the user-level planner. Persistent. Hires Engineers, drafts cross-group plans, maintains a decision log. → [Architects](../team/architects.md)
- **Terminal** — a companion shell session attached to (or standalone alongside) an agent. Not an AI session. → [Sessions](../operate/sessions.md)

## Task

The atomic unit of work tracked on the board. Has a title, an optional action, optional variables, an assignee, labels, a lane, and (when dispatched) a linked agent. Tasks live in SQLite, so they outlive the agent and the daemon both.

→ [The board](../tasks/board.md)

## Action

A reusable prompt template, defined in YAML at `.torque/actions/foo.yaml` or `~/.torque/actions/foo.yaml`. The `prompt` field is rendered through Jinja2 with the task description, your custom variables, and a `torque` context namespace that exposes the live state of the agent, worktree, and pipeline. Actions can declare **transitions** — the actions they're allowed to hand off to.

→ [Actions](../tasks/actions.md)

## Template

The Jinja2 layer of an action's prompt. Lets a single action adapt to context: print one prompt for a fresh agent and a shorter one for a continuing one, choose between Claude and Codex idioms, vary instructions when a worktree is active. Templates render against a structured `torque.*` namespace at dispatch time.

→ [Templates](../tasks/templates.md)

## Pipeline

A multi-step workflow that emerges automatically from the **transitions** declared on actions. There's no separate pipeline object. If `feature/implement` has a transition to `feature/review`, and `feature/review` has a transition to `feature/fix-review`, you have a pipeline. Torque discovers them by walking the action graph.

→ [Pipelines](../tasks/pipelines.md)

## Thread (task derivation)

When an agent finishes a task and the next step is a different task, the agent **derives** that task with `task_derive(...)`. The new task is parented to the original — it inherits the worktree, gets a depth-incremented ID like `LOOM:290:1`, and shows up under its parent on the board as a subordinate card. The full chain of derivations is the **thread**: implement → review → fix → review → done. Threads are how Torque represents progress as history instead of as a status field.

→ [Tasks and threads](../tasks/threads.md)

## Worktree

A git worktree dedicated to one agent. Lets a Worker write code on `torque/<engineer-slug>/<worker-slug>-<shortid>` without touching `main` or any other agent's branch. Torque creates, validates, checkpoints, diffs, and merges them on your behalf.

→ [Worktrees](../tasks/worktrees.md)

## Lane

A column on the task board. Reserved lanes (Backlog, To Do, In Progress, Done) cannot be renamed or deleted. Custom lanes can be added in group settings.

→ [The board](../tasks/board.md)

## Stream

A single branch / worktree execution lane that moves through implementation, review, fix-review, validation, and merge. The Engineer reasons in streams — one stream per worktree — and the UI surfaces stream state directly so you can see queue gates, blockers, and merge readiness without digging through individual tasks.

→ [Streams and waves](../operate/streams-and-waves.md)

## Wave

A set of streams or standalone tasks the Engineer intentionally activates in parallel. Waves are how Engineers dispatch — not all-at-once, not one-at-a-time, but in deliberate batches with a concurrency cap.

→ [Streams and waves](../operate/streams-and-waves.md)

## Digest

A periodic notification the Engineer's terminal receives summarizing buffered events. Idle-gated (only sent when the Engineer is idle), heartbeat-aware (a soft pulse if nothing's happened), and scoped (each Engineer only sees its group's events). Digests are how Torque keeps Engineers situationally aware without making them poll constantly.

→ [Engineers](../team/engineers.md)

## Journal

The Engineer's persistent memory. Decisions, observations, checkpoints, plans. Survives `/clear`, restart, long pauses. The journal belongs to the **group**, not to a specific Engineer agent — recreate the Engineer later and it inherits the journal history.

→ [Engineers](../team/engineers.md)

## Decision log

The Architect's equivalent of the journal. Records cross-group planning decisions, hire/dismiss events, and approvals. Per-Architect.

→ [Architects](../team/architects.md)

## MCP

Model Context Protocol. Torque exposes one canonical tool vocabulary over MCP.
**Each agent's surface is projected from its kind, effective Agent Class
authority, and relationships** — the same operation can route to different
scoped behavior for a Worker, Engineer, or Architect. Server-side enforcement;
not a prompt convention.

→ [MCP scoping](../team/mcp-scoping.md)

## Action transition

A declaration on one action that points to another action: "after `feature/implement`, you may go to `feature/review`." Transitions can also target `self`, `parent`, or `root` to route a derived task back to an existing agent instead of spinning up a new one. The `ask` transition is the human-in-the-loop gate.

→ [Pipelines](../tasks/pipelines.md)

## Role

A reusable agent launch preset, stored in `.torque/roles/foo.yaml` or `~/.torque/roles/foo.yaml`. Carries provider, model, permissions, system prompt, worktree behavior, environment, child terminals, and worker preamble/priorities. Actions can reference roles by name to select **who does the work**, while the action itself defines **what the work prompt says**.

→ [Workers](../team/workers.md)

## Schedule

A cron entry that fires a task into Torque on a recurring interval. Same dispatch path as a manual task — the schedule just chooses the timing.

→ [Schedules](../operate/schedules.md)

## Worktree base branch

The branch a worktree was forked from (typically `main`). Torque tracks this so it can correctly merge, rebase, and detect when the base branch has advanced.

→ [Worktrees](../tasks/worktrees.md)

## `torque ai` / MCP tools / CLI

Three places the same operations live:

- **`torque ai *` CLI** — for humans and offline scripts. `torque ai done`, `torque ai blocked`, `torque ai derive`, etc.
- **MCP tools** — what dispatched workers actually call:
  `mcp__torque__task_complete`, `mcp__torque__task_derive`,
  `mcp__torque__task_progress`, and the rest of their canonical surface.
  Workers report exclusively through MCP, not through the CLI.
- **`torque` CLI** — task creation, dispatch, action management, board operations. The general-purpose CLI surface.

→ [CLI reference](../reference/cli.md), [MCP tools](../reference/mcp-tools.md)
