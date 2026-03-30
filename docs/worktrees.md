# Worktrees

Git worktrees let multiple agents work on the same repository in parallel, each on its own branch. Loom manages the worktree lifecycle: creation, checkpointing, rollback, and cleanup.

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
| **Squash on merge** | Use squash merge when merging worktree branches back to the base branch. Default: on. |

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

The `.loom/worktrees/` directory is automatically added to `.gitignore` so worktree directories don't pollute your repository.

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
2. Agent A calls `loom ai derive "Review the changes" -t feature/review`
3. Agent B (review) is created and inherits Agent A's worktree
4. Agent B sees Agent A's changes and reviews them

This inheritance is automatic --- the `inherit_worktree_from` field is set during derive dispatch. No configuration needed.

### Derive-to-agent with worktrees

When using `--agent` or `--self` to dispatch to an existing agent (see [Derive-to-agent](actions.md#derive-to-agent)), the target agent keeps its existing worktree. The new task runs in whatever directory the agent is already working in.

## Merging

When an agent's work is complete and ready to merge back to the base branch, use the merge worktree option:

**UI:** Right-click the agent and select **Merge Worktree**.

This sends a merge prompt to the agent, asking it to merge the worktree branch into the base branch. Loom tracks the merge state and verifies it completed:

- **Regular merge** --- detected via `git merge-base --is-ancestor`
- **Squash merge** --- detected by simulating the merge with `git merge-tree` or by checking if the base branch advanced and includes the same file changes

After a successful merge, the agent is flagged and can be cleaned up.

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

## Closing agents with worktrees

When you remove an agent that has an active worktree with uncommitted changes, Loom warns you about the state of the worktree (dirty files, number of commits) so you can checkpoint or merge before closing.
