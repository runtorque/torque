# Agents & Sessions

This guide explains how Loom turns a saved configuration into a live agent session. It covers agent creation, providers and backends, boot commands, prompts, hooks, MCP integration, worktrees, relaunch and resume behavior, and the differences between adapters such as Claude Code and Codex.

For configuration reference, see [Agent Templates](agent-templates.md), [Group Settings](group-settings.md), and [Worktrees](worktrees.md).

## The three layers Loom manages

When you create an agent, Loom is tracking three related things:

| Layer | What it is | Where it lives |
|------|-------------|----------------|
| **Loom agent** | The saved agent record: name, group, template, worktree, status, dispatch history | Loom state / SQLite |
| **Terminal session** | The live iTerm2 tab or pane Loom created | iTerm2 |
| **Provider session** | The AI tool's own session or conversation state | Claude Code, Codex, or another adapter |

Two IDs matter:

- **`session_id`** is the iTerm2 session ID. Loom uses it to focus tabs, send text, and reconnect after restarts.
- **`agent_session_id`** is the provider's own session ID when the adapter exposes one. Loom uses it for provider-level resume.

Terminals only have the first layer and the iTerm2 session. Provider-aware agents use all three.

## Agent creation flow

Every new agent follows the same high-level flow:

1. **Resolve launch settings**
   Loom merges group defaults, the group's default agent template, any explicit template, and per-agent overrides from the creation dialog or dispatch.

2. **Choose provider and boot command**
   Loom decides which adapter to use and what command to run.

3. **Create a worktree if needed**
   If worktrees are enabled and the directory is inside a git repo, Loom creates a per-agent worktree and points the agent at it.

4. **Create the iTerm2 session**
   Loom opens a new tab, sets the title and color, changes to the working directory, exports environment variables, and sources any configured env file or init script.

5. **Install runtime integration**
   For supported adapters, Loom installs hooks, MCP configuration, provider-specific helper files, and persistent prompt files into the working directory.

6. **Launch the provider**
   Loom runs the final boot command.

7. **Send the first user message**
   If the template defines an `initial_prompt`, or if the agent was created by dispatching a task, Loom sends that text after the provider is ready.

This is why provider, prompt, and worktree settings all need to be understood together: they affect different stages of the same launch flow.

## Providers, backends, and boot commands

Loom has a provider-agnostic adapter layer. A provider tells Loom how to launch and integrate with a backend such as Claude Code or Codex.

### Built-in adapters

| Provider | Default command | Hooks / activity | MCP | Resume | Notes |
|------|------------------|------------------|-----|--------|-------|
| **`claude-code`** | `claude` | Full | Yes | Yes | Richest integration: tool activity, permission prompts, slash skills, persistent system prompt file |
| **`codex`** | `codex` | Partial | Yes | Yes | Command-hook integration, MCP config in `.codex/config.toml`, explicit prompt injection |
| **`gemini-cli`** | `gemini` | No | No | No | Launch + model selection only; no hook or MCP integration yet |
| **generic fallback** | none | No | No | No | Used when Loom cannot identify a provider from the command or process |

### How provider resolution works

Loom resolves the backend in this order:

1. If a **provider** is set explicitly, Loom treats that adapter as the source of truth.
2. If the provider is known, Loom uses that adapter's default command unless you override the boot command.
3. If no provider is set, Loom uses the boot command or global default command and then tries to auto-detect the adapter from the command name.
4. If no adapter matches, Loom falls back to the generic adapter.

Practical consequences:

- Setting `provider: claude-code` with no command runs `claude`.
- Setting `provider: codex` with no command runs `codex`.
- Leaving provider empty but setting command to `claude --model sonnet` lets Loom auto-detect Claude Code from the command.
- Supplying a custom raw command means Loom does less automatic shaping around model flags.

### Model, permissions, and max turns

These settings are adapter-aware:

- **`model`** is appended as provider-specific model flags when Loom is using the adapter's default command path.
- **`permissions`** currently affects Claude Code only:
  `skip` becomes `--dangerously-skip-permissions`; any other value becomes `--allowed-tools ...`.
- **`max_turns`** is appended as `--max-turns N` when set.

If you fully override the boot command yourself, prefer to include any provider-specific flags you need directly in that command.

## Prompt model: system, persistent, initial, and task prompts

Loom uses several different prompt layers for different purposes.

### `system_prompt`

This comes from the resolved agent configuration. It is adapter-specific launch context, not a task. Loom uses it while building the provider's persistent session instructions.

### Persistent Loom prompt

For provider-aware agents, Loom builds a persistent prompt that combines:

- the resolved `system_prompt`
- Loom's own agent instructions, centered on the `loom_done`, `loom_derive`, and `loom_ask` MCP tools for task reporting and handoff

This is what gives a Loom-managed agent its long-lived identity inside a session.

Adapter behavior differs:

- **Claude Code** writes a Loom-managed markdown file under `.loom/` and appends it with `--append-system-prompt-file`.
- **Codex** injects the prompt explicitly into the launch command and preserves that path on resume; older Loom-managed `.codex/AGENTS.md` blocks are cleaned up if present.
- **Gemini CLI** and the generic adapter do not currently have persistent prompt integration.

### `initial_prompt`

This is the first normal user message Loom sends after the agent boots. Use it for "what to do first" instructions that should not become part of the provider's persistent identity.

### Task prompts

Dispatched tasks are sent after the session exists. They are separate from the persistent prompt. This is why the same agent can keep its Loom identity while receiving many tasks over time.

See [Actions & Templates](actions.md) for how task prompts are rendered from actions.

## Hooks, MCP, and injected files

Loom installs adapter-specific files into the agent's working directory so it can observe activity and expose Loom tools inside the provider.

### Common runtime behavior

- Loom exports `LOOM_CELL_ID` into every session.
- Provider-aware adapters use that ID to identify the agent in hook payloads and MCP calls.
- Loom excludes its own injected files from git status via `.git/info/exclude`.

### Claude Code

Loom can install:

- `.claude/settings.local.json` for hooks
- `.mcp.json` for the Loom MCP server
- `.claude/skills/loom-*/SKILL.md` for Loom slash-command helpers
- `.claude/instructions.md` for non-persistent system prompt injection when needed
- `.loom/loom-system-prompt-<agent-id>.md` for the persistent Loom prompt

Claude Code hooks send rich activity data such as tool usage, permission prompts, session start, stop, and subagent activity.

### Codex

Loom can install:

- `.codex/hooks.json` for hooks
- `.codex/config.toml` for MCP and the Codex hooks feature
- cleanup for old Loom-managed `.codex/AGENTS.md` prompt blocks

Codex currently reports less runtime detail than Claude Code. Loom primarily sees session start, stop, and tool usage hooks that are useful for activity tracking.

### Gemini CLI and generic adapters

These adapters currently run without hook or MCP integration. Loom can still manage the tab, directory, worktree, and prompt delivery, but it has less visibility into what the provider is doing.

## Session lifecycle

From Loom's point of view, a managed agent goes through these phases:

1. **Created**
   Loom has created the agent record and the iTerm2 tab.

2. **Booting**
   The provider command has been launched and Loom is waiting until the TUI is ready for input.

3. **Active**
   The agent is thinking, using tools, or otherwise working.

4. **Idle**
   The provider is waiting for the next message. Loom detects this via hooks or prompt monitoring.

5. **Stopped**
   The underlying iTerm2 session is gone, but the Loom agent record remains and can be relaunched.

### Readiness detection

Some TUIs are not ready to accept the first prompt immediately after launch. Loom uses adapter-specific input-ready policies:

- **Claude Code** waits for the session-start hook, with a screen-text fallback.
- **Codex** waits for the visible Codex startup UI to stabilize.
- Other adapters fall back to a simpler send path.

This is why Loom can send `initial_prompt` or dispatched task text reliably even when the provider takes a moment to initialize.

### Activity and idle state

Provider-aware adapters report activity through hooks. Loom normalizes those events into status, activity, and last-summary fields for the UI.

At the same time, Loom also uses iTerm2 prompt monitoring. When the shell prompt returns, Loom marks the session idle. For provider-aware agents, hook events provide the richer "what is the agent doing right now?" detail.

## Relaunch, resume, and `/clear`

Loom distinguishes between relaunching an iTerm2 session and resuming a provider conversation.

### Relaunch

Relaunch always creates a fresh iTerm2 tab. Before relaunch, Loom re-resolves the current launch configuration so it picks up changes to:

- templates
- group defaults
- provider and boot command
- worktree settings
- tab color, shell, env vars, and env files

### Resume

If the adapter supports it and `session_resume` is enabled, Loom uses the provider's own resume command when `agent_session_id` is available:

- **Claude Code**: `claude --resume <session-id>`
- **Codex**: `codex resume ... <session-id>`

If no provider session ID is available, or the adapter does not support resume, Loom starts a fresh provider session in the new tab.

### Clearing context

When you clear agent context in Loom:

- supported adapters receive `/clear`
- Loom resets `tasks_dispatched`
- Loom clears the stored `agent_session_id`
- Loom clears the current-task and MCP message history

This means a later relaunch starts from a fresh provider conversation rather than resuming the old one.

## Worktrees and execution environment

Worktrees are part of the agent session model, not a separate feature bolted on afterward.

### What changes when worktrees are enabled

- Loom creates a branch named `loom/<slug>-<short-id>`.
- The agent's working directory is changed to the new worktree path.
- Hooks, MCP files, and persistent prompt files are installed into that worktree's directory.
- The worktree becomes the environment future dispatches and relaunches use.

This is why relaunching a worktree-backed agent usually drops you back into the same isolated branch automatically.

### Worktree inheritance

Pipelines can inherit worktrees so the next agent sees the same branch and files. This is how an implement agent can hand a review or fix task to another agent without copying changes manually.

### Relaunch and missing worktrees

On relaunch, Loom:

- reuses the existing worktree if it is still valid
- clears stale worktree metadata if the path is gone
- recreates a worktree if the config still says one should exist

### Cleanup

When an agent or worktree is removed, Loom also cleans up its runtime integration:

- provider hooks
- MCP config
- Loom-managed persistent prompt files
- the git worktree itself when present

If a worktree is removed but the agent is kept, Loom restores the agent's directory to the repo root.

See [Worktrees](worktrees.md) for checkpoint, rollback, merge, and manual worktree commands.

## Terminals versus agents

A Loom terminal is simpler than a Loom agent:

- no provider adapter
- no hooks or MCP
- no provider session resume
- optional boot command only

Use agents for AI sessions and terminals for companion shells such as tests, logs, or servers.

## Choosing a provider

Use this as a practical rule of thumb:

- Choose **Claude Code** when you want the richest runtime awareness, permission prompts, slash skills, and session resume.
- Choose **Codex** when you want Loom-aware launch, MCP tools, and resume, but can live with less detailed tool telemetry.
- Choose **Gemini CLI** or another custom command when Loom mainly needs to manage tabs, directories, worktrees, and prompt delivery.

The adapter layer lets Loom degrade gracefully: even when a provider has limited integration, it still participates in the same task board, dispatch, and worktree model.
