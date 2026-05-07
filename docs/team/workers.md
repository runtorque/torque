# Workers

Workers are the agents that do the actual code-writing. They're sharp, focused, and **deliberately ephemeral**.

A Worker is created when work needs doing and closed when the work is merged. It knows about its own task and not much else. You can run a dozen of them in parallel without them tripping over each other's plans because none of them is trying to be context-aware about the others — that's the Engineer's job.

## Why Workers are ephemeral

This is the most important thing to understand about Workers, and it's the thing newcomers second-guess most often.

You will be tempted to keep a Worker around between tasks. Don't. Every long-lived agent you've used has accumulated context drift, conversation pollution, half-remembered decisions, and stale assumptions. You've seen what that looks like. The fix is to throw the Worker away and boot a fresh one for the next task.

Torque makes that easy. The board carries the history, the worktree carries the code, the action carries the prompt. The Worker just has to do the current task. When the task ends, the Worker can be closed without losing anything important.

Workers are also single-purpose by design: one Worker, one task at a time. If you want a Worker to handle a sequential pair of tasks, that's what `target: self` transitions are for — the same agent picks up the follow-up task with its prior context still in the chat. → [Pipelines](../tasks/pipelines.md#target-self--continue-in-the-same-agent)

## How a Worker is launched

When a task is dispatched, Torque does seven things in order:

1. **Resolves launch settings.** Group defaults → assigned role → action overrides → per-agent overrides.
2. **Picks a provider and boot command.** Claude Code, Codex, Gemini CLI, or a generic adapter.
3. **Creates a worktree** if the action or role wants one. The branch is named `torque/<engineer-slug>/<worker-slug>-<shortid>` (or `torque/user/...` for user-spawned workers). → [Worktrees](../tasks/worktrees.md)
4. **Opens an iTerm2 tab** with the right title, tab color, working directory, and environment.
5. **Installs runtime integration.** Hooks, MCP configuration, the persistent system prompt file, any provider-specific glue.
6. **Launches the provider.**
7. **Sends the first message** — the rendered action prompt with the task description, the `torque` context namespace, and the dispatch postscript that lists the MCP tools the Worker can call.

By step 7, the Worker has a worktree, a system prompt, a postscript, and a task description. It starts working.

## Providers and boot commands

Torque ships with three first-class adapters and a generic fallback.

| Provider | Default command | Hooks / activity | MCP | Resume |
|---|---|---|---|---|
| `claude-code` | `claude` | Full | Yes | Yes |
| `codex` | `codex` | Partial | Yes | Yes |
| `gemini-cli` | `gemini` | No | No | No |
| generic | none | No | No | No |

The provider determines what kind of integration the Worker has — whether Torque can install hooks, whether it can read MCP tool calls, whether it can resume the session after restart. **For Workers that need to report through the `torque_*` MCP tools, you want Claude Code or Codex.** Gemini CLI works as a launch target but doesn't have the integration to call back into Torque.

For the full provider resolution rules, model flag handling, permissions handling, and max-turn behavior, see [Sessions](../operate/sessions.md).

## Roles: reusable Worker presets

If you find yourself configuring the same Worker over and over — same provider, same boot flags, same system prompt, same worktree behavior — make a **role** instead.

Roles live in `.torque/roles/foo.yaml` (project) or `~/.torque/roles/foo.yaml` (user). They carry:

- Provider, boot command, model, reasoning effort, permissions, max turns
- System prompt
- Initial prompt
- Worktree behavior
- Environment variables
- Tab color, icon
- Idle timeout
- Optional `preamble` and `priorities` blocks that get prepended to the dispatch prompt

An action can reference a role by name to select **who does the work**, while the action itself defines **what the work prompt says**:

```yaml
name: feature/research
agent: researcher       # references .torque/roles/researcher.yaml

prompt: |
  {{ TASK }}
```

Roles are also where the Architect's hire/dismiss flow plugs in — when an Architect hires an Engineer, it's choosing a role and a boot configuration. → [Architects](architects.md)

## What a Worker actually receives at boot

If you're debugging a Worker's behavior, it helps to know exactly what landed in its context window.

1. **A persistent system prompt** assembled from the role's `system_prompt` plus Torque's own MCP tool instructions. For Claude Code this is appended via `--append-system-prompt-file`; for Codex it's injected into the launch command.
2. **An optional `initial_prompt`** from the role — a "what to do first" message sent before any task.
3. **The rendered action prompt**, which includes the task description, your custom variables, and the `torque` context namespace (agent identity, worktree state, current task, terminals). → [Templates](../tasks/templates.md)
4. **The dispatch postscript** — a short list of which `mcp__torque__*` tools the Worker is allowed to call for *this* action's transitions. If the action has a `feature/review` transition, the postscript lists `mcp__torque__torque_derive`. If it doesn't, it doesn't.

The postscript is dynamic: a fresh agent gets the full reference; an agent with prior context gets a one-liner reminder. This keeps follow-up dispatches from re-injecting the whole system prompt.

## How a Worker reports back

Workers report **exclusively through MCP tools**. The CLI `torque ai *` commands exist for humans and offline scripts only — Workers don't use them.

| Tool | When it's called |
|---|---|
| `torque_progress` | A non-blocking status update with a one-line message. |
| `torque_done` | Task complete, no follow-up needed. |
| `torque_derive` | Hand off to the next pipeline step. Validates against the action's `transitions`. → [Pipelines](../tasks/pipelines.md) |
| `torque_blocked` | Need user input to continue. |
| `torque_error` | Unrecoverable error. |
| `torque_ask` | Blocking human-in-the-loop question. Creates a derived task in Backlog with a `human` label. |
| `torque_ready` | Done + unlink agent + cascade upward. Used for terminal handoffs. |
| `torque_verify` | Record manual deploy/restart/smoke verification details. |
| `torque_reply` | Answer a follow-up question from the Engineer. |
| `torque_name` | Suggest a more descriptive agent name (the Worker often knows what it's working on better than the dispatching action did). |

Each of these maps to a state transition the daemon validates server-side. A Worker can't `torque_derive` to an action that isn't listed in its current action's transitions. → [MCP scoping](mcp-scoping.md)

## When you'd spawn a Worker yourself

Most Workers are spawned by an Engineer (or by `torque task dispatch`). But you can also spawn one directly from the Toolbelt's **+ New** menu when you want a scratch agent — for a one-off question, a quick exploration, or a task you want to drive interactively.

User-spawned Workers:

- Are not owned by any Engineer (`owner_engineer_id` is empty)
- Get worktree branches under `torque/user/...` instead of `torque/<engineer-slug>/...`
- Don't show up in any Engineer's worklog
- Can still be assigned tasks and use the full action / pipeline / MCP machinery

This is the right way to handle work that doesn't belong to an Engineer's group: scratch repos, ad-hoc investigations, side experiments.

## Closing a Worker

Three normal ways a Worker ends:

1. **Auto-close on done.** If the action sets `auto_close_on_done: true` and the pipeline root is fully completed, Torque closes the agent automatically after `torque_done`.
2. **Engineer cleanup.** The Engineer calls `engineer_agent_close` after merging the worktree. Sets the cleanup intent on the dispatching pass.
3. **Manual.** You right-click the cell and remove it. Cascades to child terminals.

Worker closure is reversible up until you also delete the worktree — you can `engineer_agent_relaunch` (or right-click → Relaunch) to bring back a stopped Worker if its branch still has unmerged work.

## Where to next

- [Sessions](../operate/sessions.md) — provider-specific deep dive: Claude Code vs Codex, hooks, model flags.
- [Worktrees](../tasks/worktrees.md) — the branch isolation that makes parallel Workers possible.
- [Engineers](engineers.md) — the layer above Workers, the one that actually orchestrates them.
- [MCP scoping](mcp-scoping.md) — what tools a Worker can and can't call.
