# Loom

Loom is an iTerm2-first agent orchestration workspace for people who already live in the terminal. It gives you structured groups, managed agent sessions, companion terminals, worktrees, a task board, action-driven dispatch, and a semi-autonomous weaver, all backed by a local Python daemon and a web UI that runs in the Toolbelt or a browser.

## What Loom Covers

- Organize work into groups, agents, and terminals
- Dispatch tasks from reusable actions and templates
- Run isolated git worktrees per agent
- Track work on a built-in task board with pipelines and human-review gates
- Orchestrate work with a per-group weaver agent
- Control Loom from the `loom` CLI

## Start Here

- New to Loom? Start with the [Getting Started guide](docs/getting-started.md).
- Want the full docs map? Open the [documentation home](docs/index.md).
- Need a quick mental model? Read [Concepts](docs/concepts.md).

## Documentation Map

### Set up and learn the basics

- [Getting Started](docs/getting-started.md) — install Loom, run it, and create your first group, agent, and terminal
- [Concepts](docs/concepts.md) — plain-language overview of groups, agents, terminals, broadcasts, and actions
- [Agents & Sessions](docs/agents-and-sessions.md) — understand providers, boot commands, prompts, worktrees, resume, and runtime integration
- [Group Settings](docs/group-settings.md) — configure defaults, overrides, windows, and session behavior
- [Keyboard Shortcuts](docs/keyboard-shortcuts.md) — navigate Loom quickly from the terminal

### Run agent workflows

- [Workflow Guide](docs/workflow-guide.md) — follow the normal path from task creation to dispatch, pipelines, schedules, and completion
- [Task Board](docs/board.md) — manage work in lanes and dispatch tasks to agents
- [Task Lifecycle](docs/task-lifecycle.md) — understand how tasks move from backlog to completion
- [Actions & Templates](docs/actions.md) — define reusable prompts, variables, and pipelines
- [Agent Templates](docs/agent-templates.md) — save reusable agent launch presets
- [Weaver](docs/weaver.md) — use Loom's orchestrator agent for semi-autonomous task management
- [Worktrees](docs/worktrees.md) — isolate agent changes in separate git worktrees

### Reference and project docs

- [CLI Reference](docs/cli.md) — script Loom from the command line
- [Testing](docs/testing.md) — regression matrix, suite structure, and how to run coverage locally
- [Reference Guide](docs/reference-guide.md) — grouped operator reference for commands, shortcuts, settings, logs, and runtime paths
- [Troubleshooting](docs/troubleshooting.md) — symptom-first recovery steps for startup, sessions, worktrees, merge issues, and stale state
- [Operations](docs/operations.md) — runtime modes, deploy/update flow, logs, notifications, and operational guidance
- [Architecture](docs/architecture.md) — system design and component responsibilities
- [Roadmap](docs/roadmap.md) — planned work and direction
- [Docs site](docs/) — browse the full documentation in MkDocs

```bash
git clone https://github.com/aleksanderarruda/iterm2-agent-orchestration.git
cd iterm2-agent-orchestration
make deps
make install
make cli
```

Then in iTerm2:

1. Open **Scripts -> loom**
2. Open **View -> Show Toolbelt**
3. Enable **Loom** from the Toolbelt gear menu

For a wider browser view alongside the Toolbelt:

```bash
make open
```

For standalone-only mode:

```bash
make standalone
make open
```

## Documentation

The docs are organized by job:

- [Getting Started](docs/getting-started.md) for installation and first use
- [Sessions](docs/sessions.md) for groups, agents, terminals, and day-to-day navigation
- [Task Board](docs/board.md) for tasks, dispatch, attachments, dependencies, and agent reporting
- [Actions & Templates](docs/actions.md) for prompt templates, transitions, and pipelines
- [Worktrees](docs/worktrees.md) for isolated branches and checkpoints
- [Sessions](docs/sessions.md) for groups, agents, terminals, broadcast, relaunch, and navigation
- [Schedules](docs/schedules.md) for recurring and one-shot task dispatch
- [Weaver](docs/weaver.md) for orchestrator workflows and tools
- [Operations](docs/operations.md) for runtime modes, logs, deploy/update, and notifications
- [Architecture](docs/architecture.md) for the high-level system design
- [CLI Reference](docs/cli.md) for command-by-command reference
- [Docs Home](docs/index.md) for the full documentation map

## Architecture

Loom has two main parts:

- A Python daemon that manages state, sessions, worktrees, actions, schedules, MCP endpoints, and the HTTP/WebSocket API
- A lightweight HTML/CSS/JS frontend served into the iTerm2 Toolbelt or a browser window

State is persisted in SQLite. Read-only CLI commands can work directly from the database even when the daemon is stopped.

For the more detailed system view, see [docs/architecture.md](docs/architecture.md). Historical design material lives under [docs/plans/](docs/plans/).

## Development Notes

- Runtime entry point: [`loom.py`](loom.py)
- Core package: [`loom/`](loom)
- CLI: [`bin/loom`](bin/loom)
- Docs site config: [`mkdocs.yml`](mkdocs.yml)
- Regression suite entrypoint: `make test`

## Testing

```bash
make test
```

The regression matrix, current suite layering, and remaining gaps live in
[docs/testing.md](docs/testing.md).

## License

MIT
