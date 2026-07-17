# Group Settings

Each group has configurable settings that act as defaults when creating agents
and terminals. Open the modal with the gear button on the group tab or group
header.

Settings are organized into four tabs: **Group**, **Workers**, **Engineers**,
and **Architects**. The Group tab contains sub-tabs for workspace settings,
all-agent launch defaults, sync provider configuration, and advanced actions.

## Group tab

**Group → General** organizes workspace-wide settings into **Workspace**,
**Environment**, and **Limits & visibility**.

| Setting | Description |
|---------|-------------|
| **Directory** | Default working directory for new sessions. Supports `~` for home directory. |
| **Shell** | Default shell (`zsh`, `bash`, `fish`). Leave as **System default** to use the runtime shell. |
| **Env file** | Optional shell file sourced when a session starts. |
| **Variables** | Environment variables applied to all sessions. One `KEY=VALUE` per line. |
| **Agent limit** | Maximum number of top-level agents allowed in this group. Set to `0` for unlimited. |

### Start collapsed on load

When enabled, the group starts collapsed each time the Torque workspace webview loads. You can still expand it manually at any time.

### Pin to active window

When enabled, the group only appears in workspace windows where it has active sessions. If all sessions are closed, the group appears in every window so you can relaunch.

### Sync provider

Use **Group → Sync provider** to configure external board sync for this group.
The pane is organized into **Connection**, **Repository & project**, **Board
mapping**, **Issue behavior**, and **Assignees**. V1 supports GitHub Issues plus
Projects v2. Select GitHub, configure the repository and project, test the
connection, then enable synchronization. The preflight is the same one used by
`torque board sync test -g GROUP` and surfaces `gh` auth, missing `project`
scope, repository, project, and Status-field failures.

Enabled groups auto-push top-level product task creates and meaningful board
mutations through a debounced background sync. Manual Sync now remains the
force/retry path, and pull preview/apply remains operator-gated. See
[Board sync operator guide](board-sync.md) for setup, auto-sync behavior, PR
closing refs, manual pull preview/apply, limitations, and recovery.

## Launch and runtime defaults

**Group → Agents** defines provider, model, reasoning effort, and command
defaults shared by Workers, Engineers, and Architects in one **Shared launch
defaults** section. Each kind's **General** pane may override those launch
values.

**Workers → General → Launch** owns the **Default role**. Worker roles are not
shared with Engineers or Architects. It provides the base Worker role; an
explicit task or action role can override it.

The four General panes use the same hierarchy:

- **Group:** Workspace, Environment, and Limits & visibility.
- **Workers:** Launch, Runtime, and Session.
- **Engineers:** Launch and Runtime.
- **Architects:** Launch and Runtime.

Inherited select options and placeholders show the resolved value, for example
`Inherit · Codex` or `Inherit · GPT-5`. Choosing or entering another value
creates the existing per-kind override; no storage format changes.

| Setting | Description |
|---------|-------------|
| **Provider** | Preferred agent backend (`claude-code`, `codex`, `gemini-cli`, or the inherited Group → Agents value). |
| **Default role** | Optional base Worker role; explicit task and action roles take precedence. |
| **Model** | Optional provider-specific model override. Detected choices remain editable through the final **Custom…** option. |
| **Reasoning effort** | Optional provider-specific reasoning-effort override. Unsupported providers ignore it. |
| **Command override** | Advanced escape hatch for replacing the inherited provider command. |
| **Directory** | Working directory override. Workers inherit Group → General; Engineers and Architects inherit the shared agent runtime. |
| **Shell** | Shell override with the resolved inherited shell shown in the default option. |
| **Env file** | Optional shell file sourced before the Worker command runs. |
| **Additional variables** | Extra Worker environment variables, merged with and able to override the group environment. |
| **Session resume** | When supported by the provider, relaunch resumes the provider conversation instead of starting from scratch. |
| **Idle timeout** | Minutes Torque waits before it may flag a quiet Worker for attention. Set to `0` to disable. |

The Architect directory and shell controls live in **Architects → General →
Runtime**, parallel to the Engineer controls. Architect digest delivery remains
under **Architects → System**.

The underlying settings retain their existing meanings:

| Stored setting | UI location |
|----------------|-------------|
| Group directory, shell, environment | Group → General |
| All-agent provider, model, effort, command | Group → Agents |
| Default Worker role, Worker launch, and shared agent runtime overrides | Workers → General |
| Engineer launch and runtime overrides | Engineers → General |
| Architect launch and runtime overrides | Architects → General |

### Workspace isolation

Under **Workers → Worktree**, choose a **Workspace mode**:

- **Shared group checkout** — uses the group's normal working directory.
- **Isolated worktree** — gives each Worker a separate branch and
  checkout, allowing parallel work without sharing an index or uncommitted
  files.

The remaining worktree policy stays visible when the shared checkout is
selected, but it is dimmed and inactive. Torque retains those values so they are
ready if isolated worktrees are enabled again.

See [Worktrees](../tasks/worktrees.md) for the full guide on checkpoints, rollback, and merge.

!!! note
    The directory must be inside a git repository for this to work. If it's not, the setting is silently ignored.

### Worktree options

When isolated worktrees are selected, settings are grouped by lifecycle:

| Setting | Description |
|---------|-------------|
| **Worktree base directory** | Repo-relative directory where Torque stores worktrees. |
| **Worktree base branch** | Branch to fork from. Leave empty to use the repo's current HEAD. |
| **Automatic checkpoints** | Choose manual only, checkpoint when the Worker stops, checkpoint on throttled progress updates, or both progress and stop. |
| **Merge mode** | Locks `engineer_merge` for this group. **Pull request** (default) requires the PR workflow and rejects `force_direct=true`; **Direct local** bypasses the PR path for every Engineer merge; **Engineer choice** keeps PR as the default with `force_direct=true` as an explicit local fallback. |
| **Direct-merge history** | For Direct local and Engineer choice, preserve Worker commits or squash them into one local commit. Pull request mode hides this option because GitHub squash is always requested. |
| **Default post-merge cleanup** | What Torque should do by default after a successful merge when no explicit cleanup choice is provided. The default keeps the worker/worktree warm for same-worker continuity; opt in to auto-sweep to close the worker and delete the merged worktree/branch. For PR merges, cleanup runs after the PR actually merges, not when the PR is created or left pending. |
| **Preserve merge diff** | Save the full pre-merge patch as a diff artifact on the latest open branch-boundary task. |
| **Symlink all gitignored paths** | Mirror ignored paths into each worktree. This can expose `.env` files, credentials, caches, and dependencies; Workers modify the original files through the symlinks. |
| **Explicit symlinks** | Repo-relative exact paths or glob patterns that should be mirrored into every worktree as symlinks. Recursive `**` is supported (for example `etl/**/node_modules`). Only existing matches inside the repo root are linked. |

### Provider and resume notes

- If you set **Provider**, Torque treats that adapter as authoritative even if you also override the boot command.
- If Provider is empty, Torque tries to infer the adapter from the boot command or running process name.
- **Model** and **Reasoning effort** are only auto-applied when Torque is shaping the provider's normal command path. If you fully override the command, include any provider-specific flags yourself.
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

- **General** contains Launch and Runtime overrides.
- **Behavior** contains Specializations, Orchestration, Communication, and
  Policy overrides.
- **System** contains Permissions, Digest delivery, and Events.

Digest events that Torque always includes are presented as informational badges.
Only optional events use checkboxes. The Behavior notification preset updates
the detailed System controls; manual System changes switch the preset to
**Custom**.

## Architect tab

The **Architect** tab follows the same structure:

- **General** contains Launch and Runtime overrides.
- **Behavior** contains Orchestration, Continuity, and Instructions.
- **System** contains Digest delivery and Events.

Architect journal checkpoint cadence lives under **Behavior → Continuity**.
Mandatory digest events are informational; optional events remain configurable.

## Worker notifications

**Workers → Notifications** separates the governing macOS **Delivery** choice
from the **Events** that trigger it. When delivery is off, event choices remain
visible but inactive so their configuration is still understandable and is
retained for the next time delivery is enabled.

## How defaults are resolved

When creating an agent, Torque resolves settings in this order:

1. Group default agent template
2. Group `agent_*` overrides
3. Explicit agent template chosen on the agent, task, or action
4. Values provided in the custom creation dialog
5. Group-level defaults / system defaults where applicable

For terminals, the old resolution still applies: terminal-specific overrides, then group defaults, then system defaults.

For environment variables, all applicable levels are merged. More specific levels override less specific ones.
