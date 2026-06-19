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
4. Stopped: the session ended, but Torque keeps the record. Agents can be relaunched; legacy terminal records stay readable/deleteable.

## Terminals

Terminals are plain shells managed by Torque for legacy, CLI, and
action-driven compatibility. The operator UI no longer exposes manual terminal
creation; choose an existing agent or legacy terminal card from the grid to
show that live PTY in the workspace.

### Child terminals

Child terminals belong to an agent. Existing child terminals remain
readable, focusable, and deleteable from the grid.

Removing the parent agent removes its child terminals too.

### Standalone terminals

Standalone terminals belong to the group rather than an agent. Existing
standalone terminals remain visible for back-compat, but the UI no longer
offers new standalone terminal buttons or relaunch/reparent controls.

## Custom Creation

Use the agent grid **+ New** dropdown to create workers, engineers, or
architects. That dialog lets you override defaults for:

- Name
- Directory
- Shell
- Boot command
- Provider
- Environment

Group defaults, agent templates, and action-linked creation all layer into this flow.

## Drag and Drop

Torque supports limited reordering from the UI:

- Move groups to change workspace order
- Move agents between groups

Legacy/manual terminals are not draggable, reorderable, or reparentable in the
UI.

## Relaunch and Cleanup

- Relaunch a stopped agent with the relaunch action or `torque agent relaunch`
- Remove an agent when the task is done; child terminals are cleaned up with it
- Use worktree checkpoints before risky changes if the agent has a managed worktree

## Browser and desktop views

The browser and desktop views are the same live UI backed by the same daemon. Both consume the same WebSocket snapshot and delta stream, and both show embedded PTY terminals.

See [Operations](operations.md) for runtime modes.
