# Loom

Loom is an iTerm2 Toolbelt plugin that lets you manage AI agents and terminal sessions in a visual grid, directly inside your terminal.

## Why Loom?

If you spend most of your day in the terminal, you know how productive that environment can be. But as AI coding agents become part of the workflow, managing them gets messy fast. Each agent needs its own tab. Each agent might need companion terminals for running tests, watching logs, or checking git status. Multiply that by a few tasks running in parallel and you're drowning in tabs with no easy way to tell them apart.

Loom solves this by giving you a structured way to organize agents and terminals:

- **Groups** collect related work. A group might represent a feature branch, a project, or a bug investigation.
- **Agents** are AI coding sessions (Claude Code, or any CLI tool). Each agent gets its own iTerm2 tab with a boot command, working directory, and environment.
- **Terminals** attach to agents as companion shells. Need a terminal to run tests while your agent codes? Add one as a child of that agent.

When you're done with a task, remove the agent and its terminals are cleaned up with it. No orphaned tabs, no confusion about which terminal belongs to which task.

## How it works

Loom runs as a Python daemon inside iTerm2's scripting infrastructure. It serves a webview UI in the Toolbelt sidebar panel, communicating over a local WebSocket. The webview shows your groups, agents, and terminals in a compact grid. Clicking an agent focuses its iTerm2 tab. Creating an agent opens a new tab with your configured settings. Everything stays in sync.

Loom also supports a standalone mode where the UI runs in a browser window instead of the Toolbelt. This decouples the UI from iTerm2 and is the foundation for future support of other terminal emulators.

## Quick start

See [Getting Started](getting-started.md) to install and run Loom.

## Features

- Visual grid of agents and terminals in the iTerm2 Toolbelt (or standalone browser window)
- Groups with configurable defaults for directory, profile, shell, environment, and more
- Per-agent and per-terminal setting overrides
- Git worktree isolation per agent
- Auto-create companion terminals alongside new agents
- Drag-and-drop reordering of groups, agents, and terminals
- Broadcast commands to all sessions in a group
- Global keyboard shortcuts for navigating between agents from any tab
- Tab color coding for visual organization
- Automatic tab reordering to keep managed sessions grouped
- Per-group window filtering to keep unrelated work out of sight
- `loom` CLI for scripting and automation (`make cli` to install)
- [Actions & templates](actions.md) for reusable prompt templates with Jinja2, variables, and pipeline transitions
- `loom dispatch` to create agents from YAML actions with one command
