# Reference Guide

This page is the operator-facing reference hub for Loom. Use it when you need the command, shortcut, setting, or file location quickly. For walkthroughs and explanations, follow the deeper guides linked from each section.

## Start with the right page

| If you need... | Go here |
|---|---|
| The normal day-to-day task flow | [Workflow Guide](workflow-guide.md) |
| Board lanes, dispatch, and task movement | [Task Board](board.md) |
| Action files, transitions, and pipelines | [Actions & Templates](actions.md) |
| Agent launch, provider behavior, prompts, resume, and hooks | [Agents & Sessions](agents-and-sessions.md) |
| Weaver orchestration and MCP tools | [Weaver](weaver.md) |
| Git worktrees, checkpointing, and merge flow | [Worktrees](worktrees.md) |
| Symptom-first recovery steps | [Troubleshooting](troubleshooting.md) |

## Command reference by job

The full command surface lives in [CLI Reference](cli.md). This table groups the commands most operators reach for first.

| Job | Common commands |
|---|---|
| Check whether Loom is up | `loom status`, `make check`, `loom logs -f` |
| Install or update Loom | `make deps`, `make install`, `make deploy`, `make stop` |
| Start or open the UI | `make run`, `make standalone`, `make open`, `loom desktop`, `make desktop-attach` |
| Create or inspect agents | `loom agent add`, `loom agent relaunch`, `loom agent remove`, `loom status <agent>` |
| Work with tasks | `loom task create`, `loom task dispatch`, `loom task list`, `loom task show`, `loom task move`, `loom task resolve` |
| Inspect board state | `loom board list`, `loom board lanes` |
| Manage actions and pipelines | `loom action list`, `loom action show`, `loom pipeline list`, `loom pipeline show` |
| Manage schedules | `loom schedule list`, `loom schedule show`, `loom schedule run` |
| Recover a worktree | `loom worktree history`, `loom worktree checkpoint`, `loom worktree remove --relaunch` |

## Keyboard shortcuts

See [Keyboard Shortcuts](keyboard-shortcuts.md) for the full list. The shortcuts most people use constantly are:

| Shortcut | Action |
|---|---|
| ++n++ | Quick-add an agent to the focused group |
| ++t++ | Add a terminal for the selected agent |
| ++g++ | Create a group |
| ++b++ | Toggle the broadcast bar |
| ++r++ | Relaunch the focused agent or terminal |
| ++cmd+option+right++ / ++cmd+option+left++ | Move between agents |
| ++cmd+option+down++ / ++cmd+option+up++ | Move between all Loom-managed cells |
| ++cmd+shift+b++ | Toggle broadcast for the current group from any tab |

## Settings that change behavior

Loom has a few settings surfaces. The details live in the linked pages; this section is the quick map.

| Surface | Use it for |
|---|---|
| [Group Settings](group-settings.md) | Defaults for directories, boot commands, shells, windows, worktrees, terminal defaults, and Weaver behavior |
| [Agent Templates](agent-templates.md) | Reusable launch presets with provider, model, prompt, worktree, and child terminal defaults |
| [Actions & Templates](actions.md) | Prompt rendering, variables, transitions, and pipelines used by dispatch and derive |
| Top-level README environment variable reference | Runtime defaults like `LOOM_PORT`, `LOOM_DEFAULT_CMD`, `LOOM_STANDALONE`, `LOOM_BIND_ALL`, and desktop-shell overrides such as `LOOM_DESKTOP_MODE` |

### High-impact settings to remember

- `session_resume` controls whether relaunch tries to continue the provider conversation.
- Agent and Weaver `model` / `reasoning_effort` defaults only auto-apply when Loom is shaping the adapter's normal command path.
- Git worktree settings decide whether agents launch in isolated branches and how those worktrees are checkpointed or merged.
- `worktree_merge_preserve_diff` saves a pre-merge patch artifact on the latest open branch-boundary task instead of making you reconstruct the merge later.
- `restrict_to_created_agents` limits Weaver-only tools and views to agents originally launched by that Weaver while leaving the human board view unchanged.
- Group directory and shell settings affect where new agents and terminals start.
- Template overrides win for the agent or task launched from that template.

## File locations and logs

These paths matter when Loom is installed into iTerm2's Scripts directory.

| Item | Path |
|---|---|
| Installed Loom project | `~/Library/Application Support/iTerm2/Scripts/loom/loom/` |
| Daemon log | `~/Library/Application Support/iTerm2/Scripts/loom/loom/loom.log` |
| SQLite state database | `~/Library/Application Support/iTerm2/Scripts/loom/loom/loom.db` |
| Auto-launch symlink directory | `~/Library/Application Support/iTerm2/Scripts/AutoLaunch/` |

Use `loom logs` to read the daemon log without remembering the full path.

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
| Run Loom directly | `make run` |
| Open the standalone/browser view | `make standalone`, then `make open` |
| Open the native desktop shell | `loom desktop` or `make desktop` |
| Attach the native shell to an existing standalone server | `loom desktop --attach` or `make desktop-attach` |

!!! note
    `make deploy` updates the installed copy and stops the old daemon, but you still need to relaunch Loom from the Scripts menu or run `make run`.

## User-facing behavior versus implementation notes

Use the docs in this section when you want to operate Loom. Reach for implementation files only when you are changing Loom itself.

- User-facing behavior: commands, UI actions, task flow, recovery steps, and settings.
- Implementation notes: adapter internals, database schema, WebSocket payloads, hook files, and source-level runtime behavior.

If you are diagnosing a problem in a running setup, go to [Troubleshooting](troubleshooting.md) first rather than reading source files.
