# Torque

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.1.0-green.svg)](VERSION)
[![Docs](https://img.shields.io/badge/docs-mkdocs-blue.svg)](docs/index.md)

> Torque is a local agent-orchestration workspace for terminal-native
> developers. Manage AI coding agents, run them in isolated git worktrees,
> dispatch work from a task board, and let an embedded engineer coordinate the
> wave from your iTerm2 Toolbelt, a browser, or a native desktop shell.

<!-- TODO(@architect): hero asset path -->

## Why Torque

Coding agents are powerful but messy in practice. Each one wants its own
session, its own branch, its own context window, and its own terminal history.
Spinning up three at once can quickly turn into three terminals, three
worktrees, three half-remembered prompts, and a lot of "where was I?" friction.

Torque puts a thin orchestration layer in front of that workflow. You define
**groups**, drop **agents** and **terminals** into them, dispatch work through
reusable **actions**, and watch tasks move across a built-in board. Every agent
runs in its own isolated git worktree by default, so branch boundaries are
enforced instead of merely hoped for.

One step further: each group can have an **engineer**. The engineer is Torque's
orchestrator agent: it sees the board, dispatches workers, watches digests, and
coordinates the next wave. It is the same idea as a designated build engineer on
a small team, but for a single-user OSS workspace.

What you get:

- Visual group, agent, and terminal grid in iTerm2's Toolbelt sidebar, a
  browser, or a native desktop window.
- Per-agent isolated git worktrees with merge-boundary tracking.
- Reusable action templates with Jinja-rendered prompts and pipeline
  transitions.
- A task board with lanes, dispatch, derived subtasks, and human-review gates.
- An optional embedded engineer agent to coordinate work across an entire group.
- A `torque` CLI for scripting from the command line.

## Quickstart

### iTerm2 Toolbelt mode (recommended)

Requires macOS and iTerm2.

```bash
git clone git@github.com:runtorque/torque.git
cd torque
make deps
make install
make cli
```

Then in iTerm2:

1. Open **Scripts → torque** to launch the daemon.
2. Open **View → Show Toolbelt**.
3. Enable **Torque** from the Toolbelt gear menu.

This is the canonical Torque experience: the daemon runs under iTerm2 and the UI
lives beside your terminal sessions in the Toolbelt.

### Standalone browser mode

From the cloned repo, after `make deps` has been run once:

```bash
make standalone
make open
```

Standalone mode launches the daemon without requiring the Toolbelt UI and opens
Torque in your default browser. It is useful when you want a wider workspace or
are not using the iTerm2 sidebar for the current session.

### Native desktop shell

From the cloned repo, after `make cli` has installed the `torque` command:

```bash
make desktop-deps
torque desktop
```

The desktop shell installs `pywebview` into the iTerm2-managed Python runtime and
starts a native window on its own profile and port. By default it uses the
`desktop` profile and port `18933`, so it does not collide with the Toolbelt
daemon on `18932`.

For more install variants and runtime modes, see
[Getting Started](docs/getting-started.md) and [Operations](docs/operations.md).

## Key concepts

**Groups, agents, and terminals.** A group is a workspace for one project or one
focus area. It contains agents, which are long-running coding sessions in their
own worktrees, and terminals, which are regular shells for ad-hoc commands and
inspection. See [Concepts](docs/concepts.md).

**Actions and tasks.** An action is a reusable prompt template: a Jinja2 file
that takes a task description and renders the dispatch instructions for an
agent. Tasks live on the board; you dispatch them by attaching an action and
sending them to an agent. See [Actions & Templates](docs/actions.md) and
[Task Board](docs/board.md).

**The engineer.** Each group can have an embedded engineer agent that watches the
board, plans the next wave, dispatches workers, monitors digests, and asks for
human input when it hits a decision boundary. The engineer is opt-in; Torque
works fine without one. See [Engineer](docs/engineer.md).

## Documentation

- [Getting Started](docs/getting-started.md) — install and create your first
  group, agent, and terminal.
- [Concepts](docs/concepts.md) — vocabulary and mental model.
- [Workflow Guide](docs/workflow-guide.md) — task, dispatch, and completion path.
- [Actions & Templates](docs/actions.md) — define reusable prompts and pipelines.
- [Engineer](docs/engineer.md) — orchestrator agent workflows.
- [CLI Reference](docs/cli.md) — `torque` command reference.
- [Troubleshooting](docs/troubleshooting.md) — symptom-first recovery.

[Full documentation map →](docs/index.md)

## Contributing

Issues and pull requests are welcome. Until the contributing guide and issue
templates land, include enough context for a maintainer to reproduce the issue
or review the change. For code changes, use [Testing](docs/testing.md) and run
`make test` before sending a PR.

## Status

Torque is single-user, local-first, and currently macOS + iTerm2 with a working
standalone-browser fallback and a beta native desktop shell. Linux and Windows
are follow-up targets: the daemon itself is portable, but the iTerm2 integration
is the strongest dependency today.

The project is on version `1.1.0`. See [Roadmap](docs/roadmap.md) for what's
next.

## License

MIT — see [LICENSE](LICENSE).
