# Concepts

## Groups

A group is a container for agents and terminals. It represents a unit of work --- a feature, a project, a bug fix, or any context you want to keep together.

Groups appear as collapsible cards in the Toolbelt panel. You can drag them to reorder, right-click for a context menu, or click the gear icon to configure [group settings](group-settings.md).

Groups can be configured to only appear in windows where they have active sessions (see [Pin to active window](group-settings.md#pin-to-active-window)), keeping unrelated work out of sight.

## Agents

An agent is an AI coding session. When you create an agent, Torque opens a new iTerm2 tab and runs a boot command (defaults to `claude`). Agents appear as cells in the group grid.

Each agent can have its own:

- Working directory
- iTerm2 profile
- Shell
- Tab color
- Environment variables
- [Git worktree](worktrees.md) (for isolated parallel work on the same repo)

Agents are the primary unit of work. Select an agent to see its child terminals in a drawer below the grid.

### Agent lifecycle

1. **Create** --- a new tab opens, the boot command runs, status shows as `running`
2. **Idle** --- the shell prompt returns (detected via iTerm2's PromptMonitor), status shows as `idle`
3. **Running** --- a command is executing
4. **Stopped** --- the session was closed or terminated. The agent stays in the grid and can be relaunched.

## Terminals

Terminals are companion shell sessions. They come in two flavors:

### Child terminals

Created from an agent's terminal drawer, child terminals are owned by their parent agent. They share the agent's group and are removed when the agent is removed. Use them for tasks that support the agent's work: running tests, tailing logs, checking git status.

### Standalone terminals

Created from the group's "Terminals" section (visible when a group has standalone terminals), these are not attached to any agent. They persist independently.

### Terminal features

Terminals track live session information:

- **Current process** --- shown as a badge (e.g., `ZSH`, `NODE`, `VIM`)
- **Working directory** --- displayed below the terminal name
- **Git branch** --- shown alongside the repo name when inside a git repository

## Drag and drop

Most things can be reordered by dragging:

- **Groups** --- drag the header to reorder groups
- **Agents** --- drag between groups or reorder within a group
- **Terminals** --- drag to reorder within a parent, or drop on a different agent to reparent

## Actions

Actions are reusable prompt templates defined in YAML files. When you dispatch a task with an action, Torque renders the prompt template with the task description and sends it to the agent. Actions can declare transitions to create multi-step pipelines where agents hand off work to each other.

See [Actions & Templates](actions.md) for the full guide.

## Streams and waves

When Torque's Engineer is coordinating larger pieces of work, it reasons at two levels:

- a **stream** is one branch/worktree execution lane that moves through implementation, review, blocker fixes, validation, and merge
- a **wave** is the set of streams and/or standalone tasks the designated engineer intentionally activates in parallel

That means the designated engineer can plan work across streams while each stream manages its own queue, review blockers, and validation gates.

See [Streams & Waves](streams-and-waves.md) for the detailed model.
