# Worktrees

Git worktrees let multiple agents work on the same repository in parallel, each on its own branch. Loom manages the worktree lifecycle: creation, checkpointing, rollback, and cleanup.

For how worktrees fit into agent launch, prompts, MCP, and relaunch behavior, see [Agents & Sessions](agents-and-sessions.md).

## Why worktrees?

Without worktrees, two agents working on the same repo would conflict --- one agent's uncommitted changes would interfere with the other's. Worktrees solve this by giving each agent its own working copy of the repository on a separate branch.

This is especially useful for:

- **Parallel features** --- multiple agents implement different features simultaneously
- **Review pipelines** --- a reviewer agent and a fix agent work on the same branch without stepping on each other
- **Safe experimentation** --- an agent can make changes without touching the main codebase

## Enabling worktrees

Worktrees are enabled per group in [group settings](group-settings.md):

1. Open the group settings (gear icon on the group header)
2. Go to the **Agents** tab
3. Check **Git worktree per agent**

Once enabled, every new agent created in that group gets its own worktree. The agent's working directory is set to the worktree path.

### Worktree settings

| Setting | Description |
|---------|-------------|
| **Git worktree per agent** | Enable worktree creation for new agents. |
| **Base directory** | Where worktrees are stored, relative to the repo root. Default: `.loom/worktrees`. |
| **Base branch** | Branch to fork from. Default: current HEAD. |
| **Auto-checkpoint on stop** | Automatically commit changes when an agent's session ends. |
| **Checkpoint on progress / done** | Throttled automatic checkpoints when the agent reports progress or completion. |
| **Squash on merge** | Use squash merge when merging worktree branches back to the base branch. Default: on. |
| **Symlink paths** | Optional repo-relative paths to mirror into each worktree as symlinks. Useful for shared caches or large generated assets. |

Or from the CLI:

```bash
loom group settings backend -s git_worktree=true
loom group settings backend -s worktree_base_branch=main
loom group settings backend -s worktree_auto_checkpoint=true
```

## How worktrees work

When an agent is created with worktrees enabled:

1. Loom creates a new git branch: `loom/{agent-slug}-{short-id}` (e.g., `loom/impl-add-auth-a1b2c3d`)
2. A worktree is checked out at `{repo-root}/.loom/worktrees/{agent-id}/`
3. The agent's working directory is set to the worktree path
4. The agent's terminal opens in the worktree
5. Loom installs adapter files such as hooks, MCP config, and persistent prompt files into that worktree

The `.loom/worktrees/` directory is automatically added to `.gitignore` so worktree directories don't pollute your repository.

Loom also adds its own injected runtime files to the repo's git exclude list so worktree-backed agent sessions do not pollute `git status`. This includes files such as:

- `.mcp.json`
- `.claude/settings.local.json`
- `.claude/instructions.md`
- `.claude/skills/loom-*/`
- `.codex/config.toml`
- `.codex/hooks.json`

For Codex, Loom may also clean up old Loom-managed sections in `.codex/AGENTS.md` left behind by earlier versions, but the current integration uses `.codex/config.toml` plus prompt files under `.loom/`.

!!! note
    The group's working directory must be inside a git repository for worktrees to work. If it's not, the setting is silently ignored.

## Checkpoints

Checkpoints are snapshot commits on the worktree branch. They let you save progress and roll back if needed.

### Manual checkpoints

Create a checkpoint at any time from the UI or CLI:

**UI:** Right-click an agent and select **Checkpoint Worktree**.

**CLI:**

```bash
loom worktree checkpoint impl-add-auth
```

This stages all changes (`git add -A`) and creates a commit with the message `loom: checkpoint N --- agent-name`.

### Auto-checkpoints

When **Auto-checkpoint on stop** is enabled in group settings, Loom automatically creates a checkpoint whenever an agent's session ends. This catches work in progress if an agent crashes or is stopped unexpectedly.

The auto-checkpoint uses the agent's last summary (if available) as the commit body, giving you context about what the agent was working on.

### Progress checkpoints

When **Checkpoint on progress / done** is enabled, Loom can also create checkpoints when the agent reports progress or completion through `loom_progress(...)`, `loom_done(...)`, or `loom_ready()`. These checkpoints are throttled so progress spam does not create a commit every few seconds.

### Task boundaries on shared branches

When the same agent keeps a shared worktree across sequential tasks, Loom records a task-scoped boundary whenever a worktree-backed task reaches `done` or `ready`.

- If the worktree is dirty, Loom creates a dedicated boundary checkpoint commit.
- If the worktree is already clean, Loom records a marker against the current `HEAD`.
- The completed task stores the boundary metadata, and queued same-agent follow-up tasks point back to that task via `resume_after_boundary_task_id`.

This lets Loom identify the latest clean mergeable task boundary on a shared branch instead of treating the whole branch history as one undifferentiated unit.

### Viewing checkpoint history

```bash
loom worktree history impl-add-auth
```

This shows all commits on the worktree branch since it forked, with SHA, timestamp, message, and diff stats (insertions/deletions).

### Rolling back

To revert to a previous checkpoint:

```bash
loom worktree history impl-add-auth          # find the checkpoint SHA
loom worktree rollback impl-add-auth abc1234  # reset to that commit
```

This does a hard reset of the worktree branch to the specified commit. Changes after that checkpoint are discarded.

## Worktree status in the UI

The agent cell in the grid shows worktree information:

- **Branch badge** --- the worktree branch name (e.g., `loom/impl-a1b2c3d`)
- **Diff stats** --- files changed, insertions, and deletions relative to the base branch
- **Dirty indicator** --- whether there are uncommitted changes

These stats update periodically (every 60 seconds) and after checkpoints.

## Worktrees in pipelines

When agents hand off work through [pipelines](actions.md#pipelines), worktrees are inherited so the next agent works on the same code:

1. Agent A (implement) works in worktree branch `loom/impl-abc1234`
2. Agent A calls `loom_derive(description="Review the changes", action="feature/review")`
3. Agent B (review) is created and inherits Agent A's worktree
4. Agent B sees Agent A's changes and reviews them

This inheritance is automatic --- the `inherit_worktree_from` field is set during derive dispatch. No configuration needed.

### Transition-targeted reuse with worktrees

When an action transition routes follow-up work to an existing agent (for example `target: self`, `target: parent`, or `target: root`), that agent keeps its existing worktree. The new task runs in whatever directory the target agent is already working in.

## Relaunch and recovery

Worktrees persist independently of a live terminal session. On relaunch, Loom:

- reuses the existing worktree if it is still valid
- clears stale worktree metadata if the path is gone
- recreates a new worktree if the configuration still says the agent should have one

This is why a stopped agent can often be relaunched back into the same isolated branch without manual setup.

Task boundaries are reconstructed from persisted task metadata on restart. If the recorded boundary SHA no longer matches the branch tip, Loom treats that boundary as non-clean and refuses to present it as a merge target until a new clean boundary is recorded.

## Merging

When an agent's work is complete and ready to merge back to the base branch, use the merge worktree option:

**UI:** Right-click the agent and select **Merge Worktree**.

This sends a merge prompt to the agent, asking it to merge the worktree branch into the base branch. Loom tracks the merge state and verifies it completed:

- **Regular merge** --- detected via `git merge-base --is-ancestor`
- **Squash merge** --- detected by simulating the merge with `git merge-tree` or by checking if the base branch advanced and includes the same file changes

After a successful merge, the agent is flagged and can be cleaned up.

### Merge boundaries for sequential waves

On shared same-agent branches, Loom merges only the latest clean task boundary.

- Review and merge views show which completed task currently defines the merge boundary.
- If a queued follow-up has already started, Loom blocks merge and reports that the older boundary is no longer cleanly mergeable.
- If queued follow-up tasks still remain after a successful merge, Loom keeps the agent/worktree alive, resets the branch to the updated base branch, and leaves those queued tasks attached for the next wave.

### Merge settings

The **Squash on merge** group setting (default: on) tells the merge prompt to use `git merge --squash` instead of a regular merge. This keeps the base branch history clean by collapsing the worktree's commits into a single commit.

## Creating and removing worktrees manually

You can manage worktrees independently of agent creation:

### Create a worktree for an existing agent

```bash
loom worktree create impl-add-auth
loom worktree create impl-add-auth --relaunch    # relaunch agent in the worktree
```

### Remove a worktree

```bash
loom worktree remove impl-add-auth
loom worktree remove impl-add-auth --relaunch    # relaunch agent in the original repo
```

Removing a worktree deletes the worktree directory and its branch. The agent's working directory reverts to the original repository root.

!!! note
    When an agent is removed from Loom, its worktree is cleaned up automatically.

If you remove the worktree and relaunch the agent, Loom launches it from the repo root unless worktree settings tell it to create a new one.

## Closing agents with worktrees

When you remove an agent that has an active worktree with uncommitted changes, Loom warns you about the state of the worktree (dirty files, number of commits) so you can checkpoint or merge before closing.

Removing the agent also removes Loom-managed runtime files associated with that session, such as adapter hooks, MCP config, and persistent prompt files in the worktree directory.
