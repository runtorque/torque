# Agents & Sessions

This guide explains how Torque turns a saved configuration into a live agent session. It covers agent creation, providers and backends, boot commands, prompts, hooks, MCP integration, worktrees, relaunch and resume behavior, and the differences between adapters such as Claude Code and Codex.

For configuration reference, see [Agent Templates](agent-templates.md), [Group Settings](group-settings.md), and [Worktrees](worktrees.md).

## The three layers Torque manages

When you create an agent, Torque is tracking three related things:

| Layer | What it is | Where it lives |
|------|-------------|----------------|
| **Torque agent** | The saved agent record: name, group, template, worktree, status, dispatch history | Torque state / SQLite |
| **Terminal session** | The live iTerm2 tab or pane Torque created | iTerm2 |
| **Provider session** | The AI tool's own session or conversation state | Claude Code, Codex, or another adapter |

Two IDs matter:

- **`session_id`** is the iTerm2 session ID. Torque uses it to focus tabs, send text, and reconnect after restarts.
- **`agent_session_id`** is the provider's own session ID when the adapter exposes one. Torque uses it for provider-level resume.

Terminals only have the first layer and the iTerm2 session. Provider-aware agents use all three.

## Agent creation flow

Every new agent follows the same high-level flow:

1. **Resolve launch settings**
   Torque merges group defaults, the group's default agent template, any explicit template, and per-agent overrides from the creation dialog or dispatch.

2. **Choose provider and boot command**
   Torque decides which adapter to use and what command to run.

3. **Create a worktree if needed**
   If worktrees are enabled and the directory is inside a git repo, Torque creates a per-agent worktree and points the agent at it.

4. **Create the iTerm2 session**
   Torque opens a new tab, sets the title and color, changes to the working directory, exports environment variables, and sources any configured env file or init script.

5. **Install runtime integration**
   For supported adapters, Torque installs hooks, MCP configuration, provider-specific helper files, and persistent prompt files into the working directory.

6. **Launch the provider**
   Torque runs the final boot command.

7. **Send the first user message**
   If the template defines an `initial_prompt`, or if the agent was created by dispatching a task, Torque sends that text after the provider is ready.

This is why provider, prompt, and worktree settings all need to be understood together: they affect different stages of the same launch flow.

## Providers, backends, and boot commands

Torque has a provider-agnostic adapter layer. A provider tells Torque how to launch and integrate with a backend such as Claude Code or Codex.

### Built-in adapters

| Provider | Default command | Hooks / activity | MCP | Resume | Notes |
|------|------------------|------------------|-----|--------|-------|
| **`claude-code`** | `claude` | Full | Yes | Yes | Richest integration: tool activity, permission prompts, slash skills, persistent system prompt file, model + reasoning-effort flags |
| **`codex`** | `codex` | Partial | Yes | Yes | Command-hook integration, MCP config in `.codex/config.toml`, explicit prompt injection, model + reasoning-effort flags |
| **`gemini-cli`** | `gemini` | No | No | No | Launch + model selection only; no hook or MCP integration yet |
| **generic fallback** | none | No | No | No | Used when Torque cannot identify a provider from the command or process |

### How provider resolution works

Torque resolves the backend in this order:

1. If a **provider** is set explicitly, Torque treats that adapter as the source of truth.
2. If the provider is known, Torque uses that adapter's default command unless you override the boot command.
3. If no provider is set, Torque uses the boot command or global default command and then tries to auto-detect the adapter from the command name.
4. If no adapter matches, Torque falls back to the generic adapter.

Practical consequences:

- Setting `provider: claude-code` with no command runs `claude`.
- Setting `provider: codex` with no command runs `codex`.
- Leaving provider empty but setting command to `claude --model sonnet` lets Torque auto-detect Claude Code from the command.
- Supplying a custom raw command means Torque does less automatic shaping around model flags.

### Model, reasoning effort, permissions, and max turns

These settings are adapter-aware:

- **`model`** is appended as provider-specific model flags when Torque is using the adapter's default command path.
- **`reasoning_effort`** is appended as provider-specific reasoning flags when the adapter supports it. Today Claude Code and Codex expose reasoning-effort choices in Torque's create, relaunch, and group-settings flows; unsupported adapters ignore the field.
- **`permissions`** currently affects Claude Code only:
  `skip` becomes `--dangerously-skip-permissions`; any other value becomes `--allowed-tools ...`.
- **`max_turns`** is appended as `--max-turns N` when set.

If you fully override the boot command yourself, prefer to include any provider-specific flags you need directly in that command.

## Prompt model: system, persistent, initial, and task prompts

Torque uses several different prompt layers for different purposes.

### `system_prompt`

This comes from the resolved agent configuration. It is adapter-specific launch context, not a task. Torque uses it while building the provider's persistent session instructions.

### Persistent Torque prompt

For provider-aware agents, Torque builds a persistent prompt that combines:

- the resolved `system_prompt`
- Torque's own agent instructions, centered on the `torque_done`, `torque_derive`, and `torque_ask` MCP tools for task reporting and handoff

This is what gives a Torque-managed agent its long-lived identity inside a session.

Adapter behavior differs:

- **Claude Code** writes a Torque-managed markdown file under `.torque/` and appends it with `--append-system-prompt-file`.
- **Codex** injects the prompt explicitly into the launch command and preserves that path on resume; older Torque-managed `.codex/AGENTS.md` blocks are cleaned up if present.
- **Gemini CLI** and the generic adapter do not currently have persistent prompt integration.

### `initial_prompt`

This is the first normal user message Torque sends after the agent boots. Use it for "what to do first" instructions that should not become part of the provider's persistent identity.

### Task prompts

Dispatched tasks are sent after the session exists. They are separate from the persistent prompt. This is why the same agent can keep its Torque identity while receiving many tasks over time.

See [Actions & Templates](actions.md) for how task prompts are rendered from actions.

## Hooks, MCP, and injected files

Torque installs adapter-specific files into the agent's working directory so it can observe activity and expose Torque tools inside the provider.

### Architect-engineer message acknowledgements

Engineer-to-architect messages carry an optional `ack_required` boolean.
It defaults to `false`: routine progress and status-only updates should not
force the architect to reply. Engineers should set `ack_required: true` only
when they are explicitly asking the architect a question or requesting a
product/scope decision. When `ack_required` is false or omitted, Torque still
delivers the message to the architect session, but suppresses the reply
boilerplate; when true, Torque includes the normal `architect_reply(...)` hint.

### Common runtime behavior

- Torque exports `TORQUE_CELL_ID` into every session.
- Provider-aware adapters use that ID to identify the agent in hook payloads and MCP calls.
- Torque excludes its own injected files from git status via `.git/info/exclude`.

### Claude Code

Torque can install:

- `.claude/settings.local.json` for hooks
- `.mcp.json` for the Torque MCP server
- `.claude/skills/torque-*/SKILL.md` for Torque slash-command helpers
- `.claude/instructions.md` for non-persistent system prompt injection when needed
- `.torque/torque-system-prompt-<agent-id>.md` for the persistent Torque prompt

Claude Code hooks send rich activity data such as tool usage, permission prompts, session start, stop, and subagent activity.

### Codex

Torque can install:

- `.codex/hooks.json` for hooks
- `.codex/config.toml` for MCP and the Codex hooks feature
- cleanup for old Torque-managed `.codex/AGENTS.md` prompt blocks

Codex currently reports less runtime detail than Claude Code. Torque primarily sees session start, stop, and tool usage hooks that are useful for activity tracking.

### Gemini CLI and generic adapters

These adapters currently run without hook or MCP integration. Torque can still manage the tab, directory, worktree, and prompt delivery, but it has less visibility into what the provider is doing.

## Session lifecycle

From Torque's point of view, a managed agent goes through these phases:

1. **Created**
   Torque has created the agent record and the iTerm2 tab.

2. **Booting**
   The provider command has been launched and Torque is waiting until the TUI is ready for input.

3. **Active**
   The agent is thinking, using tools, or otherwise working.

4. **Idle**
   The provider is waiting for the next message. Torque detects this via hooks or prompt monitoring.

5. **Stopped**
   The underlying iTerm2 session is gone, but the Torque agent record remains and can be relaunched.

### Readiness detection

Some TUIs are not ready to accept the first prompt immediately after launch. Torque uses adapter-specific input-ready policies:

- **Claude Code** waits for the session-start hook, with a screen-text fallback.
- **Codex** waits for the visible Codex startup UI to stabilize.
- Other adapters fall back to a simpler send path.

This is why Torque can send `initial_prompt` or dispatched task text reliably even when the provider takes a moment to initialize.

### Activity and idle state

Provider-aware adapters report activity through hooks. Torque normalizes those events into status, activity, and last-summary fields for the UI.

At the same time, Torque also uses iTerm2 prompt monitoring. When the shell prompt returns, Torque marks the session idle. For provider-aware agents, hook events provide the richer "what is the agent doing right now?" detail.

## Relaunch, resume, and `/clear`

Torque distinguishes between relaunching an iTerm2 session and resuming a provider conversation.

### Relaunch

Relaunch always creates a fresh iTerm2 tab. Before relaunch, Torque re-resolves the current launch configuration so it picks up changes to:

- templates
- group defaults
- provider and boot command
- worktree settings
- tab color, shell, env vars, and env files

### Resume

If the adapter supports it and `session_resume` is enabled, Torque uses the provider's own resume command when `agent_session_id` is available:

- **Claude Code**: `claude --resume <session-id>`
- **Codex**: `codex resume ... <session-id>`

If no provider session ID is available, or the adapter does not support resume, Torque starts a fresh provider session in the new tab.

### Clearing context

When you clear agent context in Torque:

- supported adapters receive `/clear`
- Torque resets `tasks_dispatched`
- Torque clears the stored `agent_session_id`
- Torque clears the current-task and MCP message history

This means a later relaunch starts from a fresh provider conversation rather than resuming the old one.

## Worktrees and execution environment

Worktrees are part of the agent session model, not a separate feature bolted on afterward.

### What changes when worktrees are enabled

- Torque creates a branch named `torque/<slug>-<short-id>`.
- The agent's working directory is changed to the new worktree path.
- Hooks, MCP files, and persistent prompt files are installed into that worktree's directory.
- The worktree becomes the environment future dispatches and relaunches use.

This is why relaunching a worktree-backed agent usually drops you back into the same isolated branch automatically.

Architect sessions are the exception: new architects run from the repository's main checkout by default and do not create a per-agent worktree, because architects are intended to shape scope rather than edit code. Existing architect cells that already have worktrees continue to use them; the forward-looking code knob `torque.config.ARCHITECT_USES_WORKTREE` can be set to `True` to restore per-architect worktree creation for new architect sessions.

### Worktree inheritance

Pipelines can inherit worktrees so the next agent sees the same branch and files. This is how an implement agent can hand a review or fix task to another agent without copying changes manually.

### Relaunch and missing worktrees

On relaunch, Torque:

- reuses the existing worktree if it is still valid
- clears stale worktree metadata if the path is gone
- recreates a worktree if the config still says one should exist

### Cleanup

When an agent or worktree is removed, Torque also cleans up its runtime integration:

- provider hooks
- MCP config
- Torque-managed persistent prompt files
- the git worktree itself when present

If a worktree is removed but the agent is kept, Torque restores the agent's directory to the repo root.

See [Worktrees](worktrees.md) for checkpoint, rollback, merge, and manual worktree commands.

## Terminals versus agents

A Torque terminal is simpler than a Torque agent:

- no provider adapter
- no hooks or MCP
- no provider session resume
- optional boot command only

Use agents for AI sessions and terminals for companion shells such as tests, logs, or servers.

## Choosing a provider

Use this as a practical rule of thumb:

- Choose **Claude Code** when you want the richest runtime awareness, permission prompts, slash skills, and session resume.
- Choose **Codex** when you want Torque-aware launch, MCP tools, and resume, but can live with less detailed tool telemetry.
- Choose **Gemini CLI** or another custom command when Torque mainly needs to manage tabs, directories, worktrees, and prompt delivery.

The adapter layer lets Torque degrade gracefully: even when a provider has limited integration, it still participates in the same task board, dispatch, and worktree model.
