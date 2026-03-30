# Group Settings

Each group has configurable settings that act as defaults when creating agents and terminals. Open the settings modal by clicking the gear icon (++2699++) on the group header, or right-click the header and select **Settings...**.

Settings are organized into three tabs: **Group**, **Agents**, and **Terminals**.

## Group tab

These settings apply to the group as a whole and serve as the base defaults for both agents and terminals.

| Setting | Description |
|---------|-------------|
| **Directory** | Default working directory for new sessions. Supports `~` for home directory. |
| **Profile** | Default iTerm2 profile. |
| **Shell** | Default shell (`zsh`, `bash`, `fish`). Leave as "Default" to use the profile's shell. |
| **Tab color** | Default tab color for visual organization. |
| **Environment** | Environment variables applied to all sessions. One `KEY=VALUE` per line. |
| **Auto-create terminals** | Number of child terminals to create automatically alongside each new agent (0--10). |
| **Max agents** | Maximum number of agents allowed in this group. Set to 0 for unlimited. When the cap is reached, the "+ New" button shows "Full". |

### Start collapsed on load

When enabled, the group starts collapsed each time the Toolbelt webview loads. You can still expand it manually at any time.

### Pin to active window

When enabled, the group only appears in iTerm2 windows where it has active sessions. If all sessions are closed, the group appears in every window so you can relaunch.

## Agents tab

These settings override the group defaults when creating agents specifically. Leave a field empty to inherit from the group tab.

| Setting | Description |
|---------|-------------|
| **Directory** | Working directory for agents. Overrides the group default. |
| **Profile** | iTerm2 profile for agents. |
| **Shell** | Shell for agents. |
| **Tab color** | Tab color for agents. Select the arrow (++up++) to inherit from the group, or ++x++ for no color. |
| **Additional environment** | Extra environment variables for agents, merged with (and can override) the group environment. |

### Git worktree per agent

When enabled, creating an agent automatically creates a new git worktree branched from the directory's repository. Each agent gets its own branch (`loom/{id}-{name}`) and worktree path, so multiple agents can work on the same repo in parallel without conflicts. The worktree is cleaned up when the agent is removed.

See [Worktrees](worktrees.md) for the full guide on checkpoints, rollback, and merge.

!!! note
    The directory must be inside a git repository for this to work. If it's not, the setting is silently ignored.

### Always open custom dialog

When enabled, clicking the "+ New" button always opens the full creation dialog instead of instantly creating an agent with defaults.

## Terminals tab

These settings override the group defaults when creating terminals specifically.

| Setting | Description |
|---------|-------------|
| **Name prefix** | Auto-naming prefix for terminals (e.g., "Shell" produces "Shell 1", "Shell 2"). Defaults to "Terminal" if empty. |
| **Boot command** | Command to run when the terminal opens. Unlike agents, terminals don't run a boot command by default. |
| **Arguments** | Arguments appended to the boot command. |
| **Init script** | Path to a shell script sourced after `cd` but before the boot command. Supports `~`. |
| **Directory** | Working directory for terminals. Overrides the group default. |
| **Profile** | iTerm2 profile for terminals. |
| **Shell** | Shell for terminals. |
| **Tab color** | Tab color for terminals. Select the arrow (++up++) to inherit from the group, or ++x++ for no color. |
| **Additional environment** | Extra environment variables for terminals, merged with the group environment. |

### Always open custom dialog

Same as the agent version, but applies to the "+ New terminal" button.

## How defaults are resolved

When creating an agent or terminal, settings are resolved in this order (first non-empty value wins):

1. Value provided in the custom creation dialog
2. Agent-specific or terminal-specific override from group settings
3. Group-level default
4. System default (e.g., "Default" profile, no shell override)

For environment variables, all three levels are merged. Variables from more specific levels override those from less specific ones.

For tab color, the agent/terminal tabs have three states:

- **Inherit** (++up++) --- use whatever the group tab has
- **A specific color** --- override the group
- **None** (++x++) --- explicitly no color, even if the group has one
