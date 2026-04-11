# Group Settings

Each group has configurable settings that act as defaults when creating agents and terminals. Open the settings modal by clicking the gear icon (++2699++) on the group header, or right-click the header and select **Settings...**.

Settings are organized into three tabs: **Group**, **Agents**, and **Weaver**.

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
| **Provider** | Preferred agent backend (`claude-code`, `codex`, `gemini-cli`, or empty to auto-detect from the boot command). |
| **Default model** | Optional provider-specific model override for new agents in this group. |
| **Default reasoning effort** | Optional provider-specific reasoning-effort override for new agents in this group. Unsupported providers ignore it. |
| **Boot command** | Command Loom runs when the agent tab opens. Leave empty to use the provider default or global default command. |
| **Tab color** | Tab color for agents. Select the arrow (++up++) to inherit from the group, or ++x++ for no color. |
| **Additional environment** | Extra environment variables for agents, merged with (and can override) the group environment. |
| **Environment file** | Optional shell file sourced before the boot command runs. |
| **Default agent template** | Optional base template applied to new agents in this group before group-specific `agent_*` overrides. |
| **Session resume** | When supported by the provider, relaunch resumes the provider conversation instead of starting from scratch. |
| **Idle timeout** | Minutes Loom waits before it may flag a quiet agent for attention. Set to `0` to disable. |

### Git worktree per agent

When enabled, creating an agent automatically creates a new git worktree branched from the directory's repository. Each agent gets its own branch (`loom/{slug}-{short-id}`) and worktree path, so multiple agents can work on the same repo in parallel without conflicts. The worktree is cleaned up when the agent is removed.

See [Worktrees](worktrees.md) for the full guide on checkpoints, rollback, and merge.

!!! note
    The directory must be inside a git repository for this to work. If it's not, the setting is silently ignored.

### Worktree options

When worktrees are enabled, these settings control the execution environment:

| Setting | Description |
|---------|-------------|
| **Worktree base directory** | Repo-relative directory where Loom stores worktrees. |
| **Worktree base branch** | Branch to fork from. Leave empty to use the repo's current HEAD. |
| **Auto-checkpoint on stop** | Create a checkpoint commit when the agent session ends. |
| **Checkpoint on progress / done** | Create throttled checkpoints when the agent reports progress or completion. |
| **Squash on merge** | Prefer squash merge when merging worktree branches back to the base branch. |
| **Merge instructions** | Extra text Loom appends to merge prompts. |
| **Default post-merge cleanup** | What Loom should do by default after a successful merge when no explicit cleanup choice is provided. |
| **Preserve merge diff by default** | Save the full pre-merge patch as a diff artifact on the latest open branch-boundary task. |
| **Symlink paths** | Repo-relative exact paths or glob patterns that should be mirrored into every worktree as symlinks. Recursive `**` is supported (for example `etl/**/node_modules`). Only existing matches inside the repo root are linked. |

### Provider and resume notes

- If you set **Provider**, Loom treats that adapter as authoritative even if you also override the boot command.
- If Provider is empty, Loom tries to infer the adapter from the boot command or running process name.
- **Default model** and **Default reasoning effort** are only auto-applied when Loom is shaping the provider's normal command path. If you fully override the boot command, include any provider-specific flags yourself.
- Session resume only works for adapters that expose a provider session ID. Claude Code and Codex support it; generic terminals do not.

See [Agents & Sessions](agents-and-sessions.md) for the end-to-end runtime model.

### Always open custom dialog

When enabled, clicking the "+ New" button always opens the full creation dialog instead of instantly creating an agent with defaults.

## Agents → Terminals

These settings override the group defaults when creating terminals specifically. They live inside the **Agents** tab, immediately after **General**.

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

## Weaver tab

The **Weaver** tab owns per-group Weaver configuration.

| Setting | Description |
|---------|-------------|
| **Agent** | Shows the current Weaver agent for the group. Create a Weaver from the group’s **+ New** dropdown, then manage it here. |
| **Only show/manage agents launched by this Weaver** | Restricts Weaver-only tools and views to worker agents originally created by this Weaver. Human operators still see the full board. |
| **Provider** | Optional backend override just for the Weaver. Leave empty to inherit the group default. |
| **Command override** | Optional boot command override for the Weaver. |
| **Model** | Optional provider-specific model override for the designated Weaver. |
| **Reasoning effort** | Optional provider-specific reasoning-effort override for the designated Weaver. Unsupported providers ignore it. |
| **Custom Instructions** | Extra instructions appended to the Weaver system prompt. |
| **Push / Max / Heartbeat intervals** | Controls digest cadence and idle heartbeat behavior. |
| **Events** | Choose which optional event types appear in Weaver digests. Mandatory event types are always enabled. |

The designated Weaver can be restarted from its context menu. The restart flow reuses the same Weaver launch dialog, so provider, command, model, reasoning effort, and policy settings stay editable even after the Weaver already exists.

## How defaults are resolved

When creating an agent, Loom resolves settings in this order:

1. Group default agent template
2. Group `agent_*` overrides
3. Explicit agent template chosen on the agent, task, or action
4. Values provided in the custom creation dialog
5. Group-level defaults / system defaults where applicable

For terminals, the old resolution still applies: terminal-specific overrides, then group defaults, then system defaults.

For environment variables, all applicable levels are merged. More specific levels override less specific ones.

For tab color, the agent/terminal tabs have three states:

- **Inherit** (++up++) --- use whatever the group tab has
- **A specific color** --- override the group
- **None** (++x++) --- explicitly no color, even if the group has one
