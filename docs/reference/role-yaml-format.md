# Role YAML format

Role files are reusable launch presets for Torque agents. Actions reference them
with `agent: role-name`, and the Library panel edits the same YAML files.

Use roles for **who** should do the work: provider settings, system/initial
prompts, worktree defaults, environment, child terminals, and the worker
behavior preamble. Keep per-task instructions in action `prompt:` fields — see
[Action YAML format](action-yaml-format.md) for that schema.

## File locations and lookup order

Role files live only in `roles/` directories:

| Scope | Path | Notes |
|---|---|---|
| Project | `.torque/roles/<name>.yaml` | Discovered by walking up from the dispatch directory to the first project `.torque/roles/`. |
| User | `~/.torque/roles/<name>.yaml` | Shared fallback roles for all projects. |

When Torque resolves `agent: <name>` it checks the project role directory first,
then the user role directory. A project role with the same name shadows the user
role.

Nested role names are file paths under the role directory: `review/security.yaml`
is referenced as `agent: review/security`.

!!! warning "Legacy `.torque/agents/` files are ignored"
    Roles no longer load from `.torque/agents/*.yaml` or
    `~/.torque/agents/*.yaml`. Legacy files in those directories are ignored and
    surfaced as warnings in logs and `torque doctor`. Move them to
    `.torque/roles/` or `~/.torque/roles/` to use them as roles.

## Top-level schema

Only `name` is strongly recommended; most fields are optional and inherit from
group settings, action settings, or provider defaults when omitted.

| Field | Type | Purpose |
|---|---:|---|
| `name` | string | Role slug. Usually matches the file path without `.yaml`. |
| `display_name` | string | Friendly name shown in the UI. |
| `description` | string | One-line summary shown in role pickers. |
| `provider` | string | Agent provider/adapter, such as `claude-code`, `codex`, `gemini-cli`, or generic. |
| `command` | string | Command override. Leave blank to use the provider default. |
| `model` | string | Provider model setting passed through the adapter. |
| `reasoning_effort` | string | Provider reasoning-effort setting when supported. |
| `permissions` | string | Provider permission flag/preset when supported. |
| `max_turns` | int | Provider max-turn limit when supported. |
| `system_prompt` | string | Persistent provider system prompt content for this role. |
| `initial_prompt` | string | Optional kickoff message sent when the agent session starts/relaunches. |
| `preamble` | string | Worker behavior text prepended to each worker dispatch prompt. See [Prompt insertion](#prompt-insertion). |
| `priorities` | list of strings | Ordered reminders rendered below `preamble` as bullet points. |
| `session_resume` | bool | Whether supported providers should resume prior session state on relaunch. |
| `idle_timeout` | int | Idle timeout in minutes; `0` means no role-specific timeout. |
| `tab_color` | string | Legacy color metadata retained for compatibility; ignored by the PTY runtime. |
| `icon` | string | UI icon/emoji for the agent. |
| `worktree` | bool | Request a git worktree for agents launched with this role. |
| `worktree_base_branch` | string | Base branch override for role worktrees. |
| `worktree_auto_checkpoint` | bool | Auto-checkpoint worktree state on stop when enabled. |
| `checkpoint_on_progress` | bool | Auto-checkpoint on `task_progress` when enabled. |
| `worktree_merge_squash` | bool | Prefer squash merge for this role's worktrees. |
| `env_vars` | mapping | Environment variables injected into the agent process. |
| `env_file` | string | Environment file path to load for the agent process. |
| `terminals` | list of objects | Child terminals to create with the agent. Each entry supports `name` and `command`. |

Unknown fields are not part of the role contract. The UI/save path rejects
unknown fields; manually edited unknown fields are ignored at runtime and are
dropped if the role is later saved through Torque.

## `preamble` and `priorities`

`preamble` and `priorities` are role-only behavior fields. They do not replace
an action prompt and do not need `{{ TASK }}`.

```yaml
preamble: |
  You are a focused reviewer. Prefer small, verifiable findings over broad
  rewrites.
priorities:
  - Read the relevant code before editing.
  - Keep the diff focused.
  - Report exactly what you verified.
```

Normalization rules:

- `preamble` is stripped of leading/trailing blank lines before dispatch.
- `priorities` must be a YAML list; blank entries are dropped.
- When Torque saves a role, `preamble`, `system_prompt`, and `initial_prompt`
  are emitted as block scalars for readability.
- Role preambles are literal text. Put task-aware Jinja2 logic in action
  `prompt:` fields instead.

### Prompt insertion

For worker dispatch, Torque assembles the message in this order:

1. The identity anchor, for example `You are UI Worker (worker, id=abc12345).`
2. The role preamble block:
   - `preamble` text, if present.
   - a blank line plus `Priorities:` bullets, if `priorities` are present.
3. The rendered action `prompt:` body.
4. The Torque dispatch postscript that lists the MCP tools allowed for this
   action and its transitions.

Empty blocks are omitted and included blocks are separated by one blank line.
Actions can opt out with `disable_role_preamble: true`.

The preamble block is prepended only for workers. Engineers, Architects, and
terminals do not receive worker role preambles, even if they use a role for
launch configuration.

## Minimal role

```yaml
name: general-worker
display_name: General worker
description: General-purpose worker for focused implementation and docs tasks.
preamble: |
  You are a focused Torque worker. Read the existing code or docs first,
  keep changes small, and avoid unrelated cleanup.
priorities:
  - Preserve existing behavior unless the task asks for a change.
  - Prefer targeted verification before broad test runs.
  - Report what changed and what was verified.
```

## Examples from the Torque taxonomy

The Torque project taxonomy keeps worker roles narrow and mostly behavior-only:
provider, model, command, permissions, and worktree defaults stay controlled by
group settings, actions, or explicit dispatch overrides unless a role truly
needs to own them.

### UI-focused worker

```yaml
name: ui-worker
display_name: UI worker
description: Frontend/UI worker for Torque's live operator console.
preamble: |
  You are Torque's UI/front-end specialist. Treat the webview as a live,
  stateful operator console where routine WebSocket rerenders must not disturb
  the operator's current interaction.
priorities:
  - Preserve scroll position, focus, caret, inline drafts, hover/selection state, and viewport anchors across rerenders.
  - Keep DOM subtrees stable for interactive panels; avoid full rerenders unless the structure must change.
  - Add or update Node frontend regression coverage for state-preservation fixes.
  - Smoke-check user-visible flows when practical and report any live validation that still needs a human.
```

### Server-focused worker

```yaml
name: orchestration-worker
display_name: Orchestration worker
description: Backend orchestration worker for daemon, board, Architect/Engineer, MCP, events, and digests.
preamble: |
  You are Torque's orchestration-core specialist across the Python daemon,
  board state, Architect/Engineer flows, MCP tools, events, digests, and
  journals. Treat these surfaces as coupled unless the code proves otherwise.
priorities:
  - Preserve SQLite as the source of truth and keep WebSocket snapshot/delta expectations intact.
  - Keep MCP scoping, authorization, creator, assignee, owner, and handoff fields explicit.
  - Treat dispatch, derivation, deliverable gates, idempotency, journals, and digests as coupled workflows.
  - Cover server, state, and MCP changes with targeted unit tests before broadening verification.
```

### Generic launch preset

Use a generic role when you want reusable launch settings plus light behavior
without tying the worker to a specific product area.

```yaml
name: codex-worktree-worker
display_name: Codex worktree worker
description: Generic Codex worker with an isolated worktree and a helper terminal.
provider: codex
worktree: true
worktree_auto_checkpoint: true
checkpoint_on_progress: true
env_vars:
  TORQUE_EXAMPLE_MODE: "1"
terminals:
  - name: tests
    command: make test
preamble: |
  You are a general implementation worker. Keep the change scoped to the task
  and prefer direct, verifiable edits over speculative refactors.
priorities:
  - Read the relevant code first.
  - Run the narrowest useful checks.
  - Leave unrelated issues for follow-up tasks.
```
