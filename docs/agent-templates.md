# Agent Templates

Agent templates are reusable YAML-defined presets for creating agents and dispatching tasks. They answer the question:

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

## Related docs

- [Actions & Templates](actions.md)
- [Task Board](board.md)
- [Group Settings](group-settings.md)
