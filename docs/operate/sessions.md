# Sessions

Torque's core workspace model is simple:

- A **group** holds related work.
- An **agent** is an AI session in its own managed PTY.
- A **terminal** is either a child terminal attached to an agent or a standalone terminal inside the group.

If you only need the object model, see [Concepts](../foundations/core-concepts.md). This page is about how those objects behave in practice.

## Groups

Groups are the top-level workspace boundary. A group usually maps to a project, feature stream, bug investigation, or operating area.

A group gives you:

- Default settings for new agents and terminals
- A dedicated grid in the UI
- Per-group board, engineer, and dispatch defaults
- Optional filtering so the group only appears in windows where it has active sessions

Configure group defaults from the gear icon on the group header. See [Group Settings](group-settings.md).

## Agents

An agent is a managed AI coding session. Torque opens a PTY session, runs the configured boot command, streams it into the embedded terminal, and tracks its runtime state.

Agents can have:

- Their own directory and shell
- A provider and boot command override
- Group-level or template-driven environment
- A managed git worktree
- Companion child terminals
- Linked board tasks and `torque ai` reporting state

### Agent lifecycle

1. Created: Torque opens a PTY and launches the boot command.
2. Running: the agent is actively doing work.
3. Idle: the shell prompt has returned.
4. Stopped: the tab closed or the session ended, but Torque keeps the record so it can be relaunched.

## Terminals

Terminals are plain shells managed by Torque.

### Child terminals

Child terminals belong to an agent. They are the normal place for tests, logs, local servers, and ad hoc shell work that supports that agent.

Removing the parent agent removes its child terminals too.

### Standalone terminals

Standalone terminals belong to the group rather than an agent. Use them for durable utility shells that should outlive any one task.

## Custom Creation

Use the dropdown next to **+ New** to open the custom creation dialog. That dialog lets you override defaults for:

- Name
- Directory
- Shell
- Boot command
- Provider
- Environment

Group defaults, agent templates, and action-linked creation all layer into this flow.

## Drag and Drop

Torque supports reordering and reparenting from the UI:

- Move groups to change workspace order
- Move agents between groups
- Reorder terminals within a parent
- Drop a terminal onto another agent to reparent it

## Relaunch and Cleanup

- Relaunch a stopped agent or terminal with the relaunch action or `torque agent relaunch`
- Remove an agent when the task is done; child terminals are cleaned up with it
- Use worktree checkpoints before risky changes if the agent has a managed worktree

## Browser and desktop views

The browser and desktop views are the same live UI backed by the same daemon. Both consume the same WebSocket snapshot and delta stream, and both show embedded PTY terminals.

See [Operations](operations.md) for runtime modes.
