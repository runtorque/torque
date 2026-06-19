# Group Settings

Each group has configurable settings that act as defaults when creating agents and terminals. Open the settings modal by clicking the gear icon (++2699++) on the group header, or right-click the header and select **Settings...**.

Settings are organized into four tabs: **Group**, **Workers**, **Engineer**, and
**Architect**. The Group tab contains sub-tabs for general defaults, worker
defaults, terminals, sync provider configuration, and advanced actions.

## Group tab

These settings apply to the group as a whole and serve as the base defaults for both agents and terminals.

| Setting | Description |
|---------|-------------|
| **Directory** | Default working directory for new sessions. Supports `~` for home directory. |
| **Shell** | Default shell (`zsh`, `bash`, `fish`). Leave as "Default" to use the runtime default shell. |
| **Environment** | Environment variables applied to all sessions. One `KEY=VALUE` per line. |
| **Auto-create terminals** | Number of child terminals to create automatically alongside each new agent (0--10). |
| **Max agents** | Maximum number of agents allowed in this group. Set to 0 for unlimited. When the cap is reached, the "+ New" button shows "Full". |

### Start collapsed on load

When enabled, the group starts collapsed each time the Torque workspace webview loads. You can still expand it manually at any time.

### Pin to active window

When enabled, the group only appears in workspace windows where it has active sessions. If all sessions are closed, the group appears in every window so you can relaunch.

### Sync provider

Use **Group → Sync provider** to configure external board sync for this group.
V1 supports GitHub Issues plus Projects v2. Choose `github`, set
`board_sync_enabled`, then provide `github_repo`,
`github_project_owner`/`github_project_number`, the Project Status field name,
and the lane → Status JSON map. The **Test connection** button runs the same
preflight as `torque board sync test -g GROUP` and surfaces `gh` auth, missing
`project` scope, repo, project, and Status-field failures.

Enabled groups auto-push top-level product task creates and meaningful board
mutations through a debounced background sync. Manual Sync now remains the
force/retry path, and pull preview/apply remains operator-gated. See
[Board sync operator guide](board-sync.md) for setup, auto-sync behavior, PR
closing refs, manual pull preview/apply, limitations, and recovery.

## Group → Worker defaults and Workers tab

Worker-related settings are split between **Group → Worker defaults** (provider,
role, model, reasoning effort) and the **Workers** tab (execution directory,
worktrees, notifications). Leave a field empty to inherit from the group tab.

| Setting | Description |
|---------|-------------|
| **Directory** | Working directory for agents. Overrides the group default. |
| **Shell** | Shell for agents. |
| **Provider** | Preferred agent backend (`claude-code`, `codex`, `gemini-cli`, or empty to auto-detect from the boot command). |
| **Default model** | Optional provider-specific model override for new agents in this group. |
| **Default reasoning effort** | Optional provider-specific reasoning-effort override for new agents in this group. Unsupported providers ignore it. |
| **Boot command** | Command Torque runs when the agent session starts. Leave empty to use the provider default or global default command. |
| **Additional environment** | Extra environment variables for agents, merged with (and can override) the group environment. |
| **Environment file** | Optional shell file sourced before the boot command runs. |
| **Default agent template** | Optional base template applied to new agents in this group before group-specific `agent_*` overrides. |
| **Session resume** | When supported by the provider, relaunch resumes the provider conversation instead of starting from scratch. |
| **Idle timeout** | Minutes Torque waits before it may flag a quiet agent for attention. Set to `0` to disable. |

### Git worktree per worker

When enabled, creating a worker automatically creates a new git worktree branched from the directory's repository. Each worker gets its own branch (`torque/{slug}-{short-id}`) and worktree path, so multiple workers can work on the same repo in parallel without conflicts. The worktree is cleaned up when the worker is removed.

See [Worktrees](../tasks/worktrees.md) for the full guide on checkpoints, rollback, and merge.

!!! note
    The directory must be inside a git repository for this to work. If it's not, the setting is silently ignored.

### Worktree options

When worktrees are enabled, these settings control the execution environment:

| Setting | Description |
|---------|-------------|
| **Worktree base directory** | Repo-relative directory where Torque stores worktrees. |
| **Worktree base branch** | Branch to fork from. Leave empty to use the repo's current HEAD. |
| **Auto-checkpoint on stop** | Create a checkpoint commit when the agent session ends. |
| **Checkpoint on progress / done** | Create throttled checkpoints when the agent reports progress or completion. |
| **Squash on merge** | Prefer squash merge for the explicit direct-local fallback. The default `engineer_merge` path creates/reuses a GitHub PR and requests a squash merge regardless of this setting. |
| **Engineer merge mode** | Locks `engineer_merge` for this group. **Pull request** (default) requires the PR workflow and rejects `force_direct=true`; **Direct local** bypasses the PR path for every engineer merge; **Engineer choice** keeps the default PR workflow with `force_direct=true` as an explicit local fallback. Disallowed attempts and lock overrides are recorded as workflow-breach audit events. |
| **Merge instructions** | Extra text Torque appends to merge prompts. |
| **Default post-merge cleanup** | What Torque should do by default after a successful merge when no explicit cleanup choice is provided. The default keeps the worker/worktree warm for same-worker continuity; opt in to auto-sweep to close the worker and delete the merged worktree/branch. For PR merges, cleanup runs after the PR actually merges, not when the PR is created or left pending. |
| **Preserve merge diff by default** | Save the full pre-merge patch as a diff artifact on the latest open branch-boundary task. |
| **Symlink paths** | Repo-relative exact paths or glob patterns that should be mirrored into every worktree as symlinks. Recursive `**` is supported (for example `etl/**/node_modules`). Only existing matches inside the repo root are linked. |

### Provider and resume notes

- If you set **Provider**, Torque treats that adapter as authoritative even if you also override the boot command.
- If Provider is empty, Torque tries to infer the adapter from the boot command or running process name.
- **Default model** and **Default reasoning effort** are only auto-applied when Torque is shaping the provider's normal command path. If you fully override the boot command, include any provider-specific flags yourself.
- Session resume only works for adapters that expose a provider session ID. Claude Code and Codex support it; generic terminals do not.

See [Agents & Sessions](../team/workers.md) for the end-to-end runtime model.

## Manual terminal settings

Manual terminal creation is no longer an operator-facing Group Settings flow.
Existing stored terminal defaults remain in state for legacy, CLI, and
action-driven compatibility, but the web UI does not expose a **Group →
Terminals** pane or a "+ New terminal" button. Select an agent, worker,
engineer, architect, or legacy terminal card from the grid to view its session.

## Engineer tab

The **Engineer** tab owns per-group Engineer configuration.

| Setting | Description |
|---------|-------------|
| **Agent** | Shows the group's designated engineer. Create an engineer from the group’s **+ New** dropdown, then manage it here. |
| **Only show/manage agents launched by this engineer** | Restricts designated-engineer-only tools and views to worker agents originally created by that engineer. Human operators still see the full board. |
| **Provider** | Optional backend override just for the designated engineer. Leave empty to inherit the group default. |
| **Command override** | Optional boot command override for the designated engineer. |
| **Model** | Optional provider-specific model override for the designated engineer. |
| **Reasoning effort** | Optional provider-specific reasoning-effort override for the designated engineer. Unsupported providers ignore it. |
| **Custom Instructions** | Extra instructions appended to the designated-engineer system prompt. |
| **Push / Max / Heartbeat intervals** | Controls digest cadence and idle heartbeat behavior. |
| **Events** | Choose which optional event types appear in Engineer digests. Mandatory event types are always enabled. |

The designated engineer can be restarted from its context menu. The restart flow reuses the same launch dialog, so provider, command, model, reasoning effort, and policy settings stay editable even after that engineer already exists.

## How defaults are resolved

When creating an agent, Torque resolves settings in this order:

1. Group default agent template
2. Group `agent_*` overrides
3. Explicit agent template chosen on the agent, task, or action
4. Values provided in the custom creation dialog
5. Group-level defaults / system defaults where applicable

For terminals, the old resolution still applies: terminal-specific overrides, then group defaults, then system defaults.

For environment variables, all applicable levels are merged. More specific levels override less specific ones.
