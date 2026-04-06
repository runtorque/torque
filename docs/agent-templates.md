# Agent Templates

Agent templates are reusable YAML-defined presets for creating agents and dispatching tasks. They answer the question:

For the runtime model behind these fields, see [Agents & Sessions](agents-and-sessions.md).

> **Who should do this work?**

Actions still answer:

> **What work should be done?**

Together, an agent template plus an action fully describe a dispatch.

## What templates configure

Agent templates can define:

- Provider and command override
- Model
- Permissions
- System prompt
- Initial prompt
- Session resume and idle timeout
- Tab color and icon
- Worktree behavior
- Environment variables
- Child terminals

Templates are static config in v1. Unlike action prompts, template fields are not rendered with Jinja2.

## Field guide

These fields map directly onto Loom's launch pipeline:

| Field | What it controls |
|------|-------------------|
| `provider` | Which adapter/backend Loom should use, such as `claude-code` or `codex` |
| `command` | Explicit boot command override |
| `model` | Provider model selection when Loom is constructing the default command |
| `permissions` | Provider-specific permission flags; currently meaningful for Claude Code |
| `max_turns` | Maximum conversation turns for providers that support `--max-turns` |
| `system_prompt` | Persistent session instructions that Loom folds into the provider's long-lived prompt |
| `initial_prompt` | First normal user message sent after the session boots |
| `session_resume` | Whether relaunch should try to resume the provider conversation |
| `idle_timeout` | Minutes before Loom may flag an idle agent for attention |
| `worktree` and related worktree fields | Whether the agent should run in an isolated git worktree and how Loom manages it |
| `env_vars` / `env_file` | Runtime environment exported before the boot command runs |
| `terminals` | Child terminals to create alongside the agent |

## Provider and boot-command behavior

The most important template fields are `provider` and `command`.

- If you set only `provider`, Loom uses that adapter's default command.
- If you set both `provider` and `command`, Loom uses your command but still treats the session as that provider.
- If you set only `command`, Loom tries to auto-detect the provider from the command name.

Examples:

```yaml
provider: claude-code
```

Runs Claude Code with Loom-managed integration and the default `claude` command.

```yaml
provider: codex
command: codex --sandbox workspace-write
```

Still uses the Codex adapter, but with a custom boot command.

```yaml
command: claude --model sonnet
```

Lets Loom infer the Claude Code adapter from the command itself.

### Model and permission notes

- `model` is only auto-appended when Loom is shaping the provider's default command path.
- `permissions` currently maps to Claude Code flags.
- If you fully own the boot command, include any provider-specific flags directly in that command.

## Where templates live

Loom discovers templates from two locations:

| Scope | Path |
|-------|------|
| Project | `.loom/agents/` |
| User | `~/.loom/agents/` |

Project templates take precedence when a project and user template share the same name. User templates are still shown in the editor as overridden.

Subdirectories create namespaces. For example:

```text
.loom/agents/ops/researcher.yaml
```

becomes the template:

```text
ops/researcher
```

## Example

```yaml
name: researcher
display_name: Researcher
description: High-context research and planning agent

provider: claude-code
model: opus
permissions: skip
system_prompt: |
  Focus on codebase research, synthesis, and implementation planning.
initial_prompt: |
  Start by reading the relevant code before making recommendations.

session_resume: true
idle_timeout: 10

tab_color: "#1f6feb"
icon: "🔎"

worktree: false

env_vars:
  LOOM_ROLE: researcher

terminals:
  - name: logs
    command: tail -f loom.log
```

This template says:

- use the **Claude Code** adapter
- launch it with the **`opus`** model
- install a persistent system prompt for research behavior
- send an initial "read first" message after boot
- resume the provider conversation on relaunch when possible
- do **not** create a worktree automatically
- add one helper terminal for log tailing

## How templates are used

Templates integrate in four places:

### 1. New agent creation

The `+ New` dropdown shows saved templates. Choosing one creates a new agent with that template applied. If the group has `agent_always_custom_dialog` enabled, the custom dialog opens with the template pre-filled instead.

### 2. Task dispatch

Tasks can store an `agent_template`. When dispatch creates a new agent, Loom applies that template before sending the action prompt or raw task text.

### 3. Actions can reference templates

An action can set:

```yaml
agent: researcher
```

This tells Loom to use the `researcher` template whenever that action creates an agent.

Legacy inline action agent mappings still work:

```yaml
agent:
  command: codex
  directory: /repo
```

but template references are the preferred form.

### 4. Group defaults

Each group can set a `default_agent_template`. This acts as the base configuration for new agents in that group.

## Merge order

When Loom resolves agent settings, it applies them in this order:

1. Group default template
2. Group `agent_*` overrides
3. Explicit template selected on the agent, task, or action
4. Per-agent overrides from the custom dialog or dispatch payload

Environment variables are merged across levels. Scalar fields overwrite less specific values.

## Runtime notes

Templates influence more than the boot command:

- `system_prompt` is folded into Loom's persistent agent prompt for supported adapters
- `initial_prompt` is sent only after the provider is ready for input
- `session_resume` controls whether relaunch uses the provider's own resume command
- `worktree` changes the working directory before hooks, MCP config, and prompt files are installed

See [Agents & Sessions](agents-and-sessions.md) for how Loom turns these fields into a live session.

## Related docs

- [Agents & Sessions](agents-and-sessions.md)
- [Actions & Templates](actions.md)
- [Task Board](board.md)
- [Group Settings](group-settings.md)
