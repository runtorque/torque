# Reference Guide

This page is the operator-facing reference hub for Torque. Use it when you need the command, shortcut, setting, or file location quickly. For walkthroughs and explanations, follow the deeper guides linked from each section.

## Start with the right page

| If you need... | Go here |
|---|---|
| The normal day-to-day task flow | [Workflow Guide](../operate/workflow-guide.md) |
| Board lanes, dispatch, and task movement | [Task Board](../tasks/board.md) |
| Action files, transitions, and pipelines | [Actions & Templates](../tasks/actions.md); exact schema: [Action YAML format](action-yaml-format.md) |
| Agent launch, provider behavior, prompts, resume, and hooks | [Agents & Sessions](../team/workers.md) |
| Engineer orchestration and MCP tools | [Engineer](../team/engineers.md) |
| Git worktrees, checkpointing, and merge flow | [Worktrees](../tasks/worktrees.md) |
| Symptom-first recovery steps | [Troubleshooting](troubleshooting.md) |

## Command reference by job

The full command surface lives in [CLI Reference](cli.md). This table groups the commands most operators reach for first.

| Job | Common commands |
|---|---|
| Check whether Torque is up | `torque status`, `make check`, `torque logs -f` |
| Install or update Torque | `make deps`, `make install`, `make deploy`, `make stop` |
| Start or open the UI | `make run`, `make standalone`, `make open`, `torque desktop`, `make desktop-attach` |
| Create or inspect agents | `torque agent add`, `torque agent relaunch`, `torque agent remove`, `torque status <agent>` |
| Work with tasks | `torque task create`, `torque task dispatch`, `torque task list`, `torque task show`, `torque task move`, `torque task resolve` |
| Inspect board state | `torque board list`, `torque board lanes` |
| Manage actions and pipelines | `torque action list`, `torque action show`, `torque pipeline list`, `torque pipeline show` |
| Manage schedules | `torque schedule list`, `torque schedule show`, `torque schedule run` |
| Recover a worktree | `torque worktree history`, `torque worktree checkpoint`, `torque worktree remove --relaunch` |

## Keyboard shortcuts

See [Keyboard Shortcuts](../operate/keyboard-shortcuts.md) for the full list. The shortcuts most people use constantly are:

| Shortcut | Action |
|---|---|
| ++n++ | Quick-add an agent to the focused group |
| ++t++ | Add a terminal for the selected agent |
| ++g++ | Create a group |
| ++r++ | Relaunch the focused agent or terminal |
| ++cmd+option+right++ / ++cmd+option+left++ | Move between agents |
| ++cmd+option+down++ / ++cmd+option+up++ | Move between all Torque-managed cells |

## Settings that change behavior

Torque has a few settings surfaces. The details live in the linked pages; this section is the quick map.

| Surface | Use it for |
|---|---|
| [Group Settings](../operate/group-settings.md) | Defaults for directories, boot commands, shells, windows, worktrees, terminal defaults, and Engineer behavior |
| [Agent Templates](../team/workers.md) | Reusable launch presets with provider, model, prompt, worktree, and child terminal defaults |
| [Actions & Templates](../tasks/actions.md) | Prompt rendering, variables, transitions, and pipelines used by dispatch and derive |
| Top-level README environment variable reference | Runtime defaults like `TORQUE_PORT`, `TORQUE_DEFAULT_CMD`, `TORQUE_STANDALONE`, `TORQUE_BIND_ALL`, and desktop-shell overrides such as `TORQUE_DESKTOP_MODE` |

### High-impact settings to remember

- `session_resume` controls whether relaunch tries to continue the provider conversation.
- Agent and Engineer `model` / `reasoning_effort` defaults only auto-apply when Torque is shaping the adapter's normal command path.
- Git worktree settings decide whether agents launch in isolated branches and how those worktrees are checkpointed or merged.
- `worktree_merge_preserve_diff` saves a pre-merge patch artifact on the latest open branch-boundary task instead of making you reconstruct the merge later.
- `restrict_to_created_agents` limits designated-engineer-only tools and views to agents originally launched by that engineer while leaving the human board view unchanged.
- Group directory and shell settings affect where new agents and terminals start.
- Template overrides win for the agent or task launched from that template.

## File locations and logs

These paths matter when Torque is installed into iTerm2's Scripts directory.

| Item | Path |
|---|---|
| Installed Torque project | `~/Library/Application Support/iTerm2/Scripts/torque/torque/` |
| Daemon log | `~/Library/Application Support/iTerm2/Scripts/torque/torque/torque.log` |
| SQLite state database | `~/Library/Application Support/iTerm2/Scripts/torque/torque/torque.db` |
| Auto-launch symlink directory | `~/Library/Application Support/iTerm2/Scripts/AutoLaunch/` |

Use `torque logs` to read the daemon log without remembering the full path.

## Testing, updating, and deployment notes

These are operator notes, not implementation details.

| Task | Command |
|---|---|
| Verify prerequisites | `make check` |
| Install or refresh dependencies in iTerm2's Python | `make deps` |
| Install the optional native desktop-shell dependency | `make desktop-deps` |
| Copy the current repo into the iTerm2 Scripts install | `make install` |
| Replace the installed copy after pulling changes | `make deploy` |
| Stop the daemon listening on port `18932` | `make stop` |
| Run Torque directly | `make run` |
| Open the standalone/browser view | `make standalone`, then `make open` |
| Open the native desktop shell | `torque desktop` or `make desktop` |
| Attach the native shell to an existing standalone server | `torque desktop --attach` or `make desktop-attach` |

!!! note
    `make deploy` updates the installed copy and stops the old daemon, but you still need to relaunch Torque from the Scripts menu or run `make run`.

## User-facing behavior versus implementation notes

Use the docs in this section when you want to operate Torque. Reach for implementation files only when you are changing Torque itself.

- User-facing behavior: commands, UI actions, task flow, recovery steps, and settings.
- Implementation notes: adapter internals, database schema, WebSocket payloads, hook files, and source-level runtime behavior.

If you are diagnosing a problem in a running setup, go to [Troubleshooting](troubleshooting.md) first rather than reading source files.
