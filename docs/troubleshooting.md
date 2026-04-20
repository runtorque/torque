# Troubleshooting

This page is for operating Loom when something is wrong. Start with the symptom that matches what you see, then follow the smallest recovery step that fixes it.

For command details, see [CLI Reference](cli.md). For the underlying behavior of agents, worktrees, and orchestration, use [Agents & Sessions](agents-and-sessions.md), [Worktrees](worktrees.md), and [Weaver](weaver.md).

## First checks

Before digging into a specific symptom, run these checks:

```bash
loom status
make check
loom logs -f
```

These answer three different questions:

- `loom status` tells you what Loom thinks exists right now.
- `make check` verifies the install and iTerm2 Python environment.
- `loom logs -f` shows daemon-side errors while you reproduce the problem.

## Loom will not start

### Symptoms

- The Scripts menu launch fails immediately.
- The Toolbelt panel stays empty.
- `loom` commands that need the daemon report that they cannot connect.

### Recovery

1. Run `make check`.
2. If iTerm2 Python or dependencies are missing, run `make deps` and `make install`.
3. If port `18932` is already in use, run `make stop`.
4. Start Loom again from the Scripts menu, or run `make run`.
5. Tail `loom logs -f` while starting it again.

### Common causes

- iTerm2 Python API is not enabled.
- Loom was updated in the repo, but the installed copy under iTerm2 Scripts was not refreshed.
- Another process is still listening on Loom's port.

## The Toolbelt panel or standalone UI does not appear

### Symptoms

- Loom is running, but you do not see the panel.
- The browser view does not connect.

### Recovery

1. In iTerm2, open **View > Show Toolbelt** and make sure **Loom** is enabled in the Toolbelt gear menu.
2. If you are using standalone mode, run `make open` after `make standalone` or `make run`.
3. Confirm the port with `LOOM_PORT` if you changed it from the default.
4. Check the daemon log for startup or bind errors.

## The native desktop shell refuses to start or attach

### Symptoms

- `loom desktop` exits immediately with a `pywebview` error.
- `loom desktop --attach` refuses to connect to a running Loom instance.
- The desktop window closes, but you are unsure whether the server should still be running.

### Recovery

1. Run `make check` and confirm `pywebview` is installed.
2. Install `pywebview` into the Python runtime that is launching the desktop shell:
   - default runtime: `make desktop-deps`
   - custom runtime: `YOUR_PYTHON -m pip install pywebview`
3. If you are using `loom desktop --attach`, make sure the target server is:
   - standalone, not the Toolbelt daemon
   - on the exact port you passed
   - using the same profile and data dir as the desktop shell
4. If you only want a native window and do not need to reuse an existing server, run plain `loom desktop` so Loom spawns a desktop-owned child server with safe defaults.

### Notes

- Browser smoke only validates the served UI path. It does not prove native-window behavior.
- Spawn mode shuts down the child server when the desktop window closes.
- Attach mode keeps the external standalone server running after the desktop window closes.

## Buttons do nothing or the UI feels stale

### Symptoms

- Clicking relaunch, remove, or dispatch appears to do nothing.
- The board or grid does not reflect the current terminal state.

### Recovery

1. Run `loom logs -f` and repeat the action.
2. If the daemon is wedged, run `make stop`, then restart Loom.
3. Refresh the browser page if you are in standalone mode.
4. If the problem is limited to one agent session, relaunch that agent instead of restarting everything.

### Why this happens

This usually means the UI is connected to an old daemon instance or the daemon lost contact with iTerm2 state.

## An agent is stopped, missing, or linked to the wrong live tab

### Symptoms

- An agent shows as stopped even though you expect it to be active.
- The agent record exists, but its terminal tab is gone.
- A relaunch created a new tab because Loom could not reuse the prior session.

### Recovery

1. Inspect the agent with `loom status <agent>`.
2. Relaunch the agent with `loom agent relaunch <agent>`.
3. If the agent was using a worktree, confirm the worktree still exists with `loom worktree history <agent>` or by checking the path in the UI.
4. If only the conversation state is bad, clear context in the Loom UI instead of removing the agent.
5. Remove and recreate the agent only after relaunch and context clearing fail.

### Notes

- Relaunch creates a fresh iTerm2 tab.
- Resume only happens when the provider adapter supports it and Loom still has a valid provider session ID.
- A stale tab title or a terminated terminal session can leave a saved agent record behind even though the live tab is gone.

## Provider session did not resume, or the conversation state is wrong

### Symptoms

- Relaunch opens the provider, but the prior conversation is gone.
- The provider resumed, but the agent should really start fresh.
- Dispatches or prompts are being influenced by stale context.

### Recovery

1. Confirm the group or template still has session resume enabled.
2. Relaunch the agent once. If Loom has a provider session ID, it will try the adapter's resume flow automatically.
3. If you want a fresh conversation, use **Clear Context** in Loom before relaunching again.
4. If resume keeps failing, treat it as a fresh session problem and continue with a new conversation.

### What to expect

- Claude Code and Codex can resume when Loom still has the provider session ID.
- After **Clear Context**, Loom intentionally forgets the stored provider session so the next relaunch starts fresh.
- Generic shells and terminals do not have provider-level resume.

## Worktree is missing, dirty, or on the wrong branch

### Symptoms

- A worktree-backed agent relaunches into the repo root instead of its branch.
- The worktree path was deleted or moved.
- `git status` inside the worktree is not what you expected.

### Recovery

1. Check worktree history with `loom worktree history <agent>`.
2. Create a safety checkpoint with `loom worktree checkpoint <agent>` if the worktree still exists.
3. If the worktree metadata is stale, remove it with `loom worktree remove <agent>`.
4. If you want the running agent moved back into the repo or a fresh worktree, use `loom worktree remove <agent> --relaunch` or `loom worktree create <agent> --relaunch`.
5. Confirm the group's worktree settings still match the behavior you want.

### Common causes

- The worktree directory was cleaned up outside Loom.
- The repository root changed.
- The group is no longer configured to launch agents with worktrees.

## Merge is blocked or there are merge conflicts

### Symptoms

- Merge/review flow stops before completion.
- Loom reports that the worktree cannot be merged cleanly.
- The base branch has moved and the worktree no longer merges automatically.

### Recovery

1. Create a checkpoint before changing anything: `loom worktree checkpoint <agent>`.
2. Review the worktree status and history.
3. If the conflict came from designated-engineer review/merge flow, run `engineer_rebase` and retry `engineer_merge`.
4. Merge or rebase manually in the worktree if your team wants a human fix.
5. If you need the agent to continue from the current branch state, relaunch the same agent after the conflict is resolved.
6. If the branch is no longer useful, remove the worktree and restart from a fresh task.

### Notes

- Loom can detect merge problems before cleanup, but it does not make a risky conflict-resolution decision for you.
- Squash merge settings affect how the final merge is expected to complete, not whether the branches conflict.

## Dispatch, schedules, or board movement are not happening

### Symptoms

- A task stays in Backlog and never launches.
- A scheduled task does not appear.
- A task cannot move because something upstream is still unresolved.

### Recovery

1. Run `loom task list` and `loom board lanes` to see where the task actually is.
2. Inspect the task with `loom task show <task>`.
3. Check for unmet dependencies, pending human asks, or a disabled schedule.
4. Use `loom schedule list` and `loom schedule show <schedule>` if the task should have been created automatically.
5. Dispatch the task manually if you want to bypass waiting conditions.

### What usually causes this

- The task is blocked by dependencies.
- A human-in-the-loop ask task is still waiting for an answer.
- The schedule exists but is disabled or timed differently than expected.

## Weaver is not progressing or event digests look wrong

### Symptoms

- The Weaver panel stops sending digests.
- The designated engineer is paused and never resumes.
- The human replied, but the orchestration flow still looks stuck.

### Recovery

1. Open the Engineers panel and check whether event delivery is paused.
2. If the designated engineer asked a human question, make sure the reply was actually sent back to the waiting task.
3. Resume orchestration after the reply if the flow expects that step.
4. Relaunch the designated engineer if the session itself stopped.
5. Use the journal and recent event views in the engineer workflow to recover context before dispatching more work.

See [Weaver](weaver.md) for the full operating model and MCP tool surface.

## Broken or stale agent state recovery

Use this order of operations. It solves most operational issues without deleting useful state.

1. Check status and logs.
2. Relaunch the affected agent or terminal.
3. Clear agent context if the conversation is wrong but the agent record is still valid.
4. Checkpoint the worktree before destructive recovery.
5. Remove and recreate the worktree if the branch state is the problem.
6. Remove and recreate the agent only when the saved agent record itself is no longer worth preserving.
7. Restart the daemon only when the problem is global rather than isolated to one agent.

## Logs and diagnostic data

The most useful operator-level diagnostics are:

- `loom logs` and `loom logs -f`
- `loom status`
- `make check`
- the daemon log at `~/Library/Application Support/iTerm2/Scripts/loom/loom/loom.log`
- the state database at `~/Library/Application Support/iTerm2/Scripts/loom/loom/loom.db`

!!! note
    The log file and database are useful for diagnosis, but most operators should treat them as read-only. Use Loom commands and UI actions for normal recovery instead of editing runtime files directly.

## Testing, update, and deployment notes

When you are operating Loom rather than developing Loom, these are the practical rules:

- Run `make check` before assuming a runtime bug.
- Run `make deploy` after pulling repo changes so the installed iTerm2 copy matches the repo.
- Restart Loom after `make deploy`.
- Use `make stop` only to clear a stuck daemon or port conflict.
- Use `make standalone` and `make open` only when you intentionally want the browser UI.

If you are changing Loom itself, testing and release workflows belong in the project/developer docs rather than in the operator recovery path.
