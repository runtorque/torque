# Implementation Plan: Agent Context Preservation

**Roadmap phase**: 4 — Workflow Automation
**Status**: Planned
**Goal**: Preserve agent context across sequential tasks. When an agent receives a second task — whether from a pipeline derivation, a reviewer sending work back, or a user manually re-dispatching — the action template can detect prior context and adjust the prompt accordingly (abbreviated instructions instead of a full system prompt).

---

## The Problem

Torque can dispatch a task to an agent, and agents can derive follow-up tasks via `torque ai derive`. But every derivation spawns a new agent with a fresh context window. This is wasteful in three scenarios:

1. **Review → fix cycle**: Agent A implements, Agent B reviews and finds issues, then derives a "fix" task. Today this spawns Agent C. But Agent A already has the full codebase in context — the fix task should go back to Agent A.
2. **Sequential dispatch**: A user finishes a task with an agent, then wants to dispatch a related follow-up to the same agent. The agent already understands the codebase, but the action template has no way to know this — it sends the full system prompt again, wasting tokens and context window.
3. **Self-continuation**: An agent finishes one phase and wants to continue with the next phase in the same session, without losing its accumulated context.

In all three cases, the action template needs to know: "is this agent starting fresh, or does it already have context?" And the `derive` command needs a way to target a specific existing agent instead of always creating a new one.

---

## Design Principles

1. **Templates opt in** — The `torque` namespace is always injected into the Jinja2 context, but templates that don't reference it behave identically to today. No existing action breaks.
2. **Context is inferred, not declared** — The system determines `is_clean` from a simple counter on the agent. No explicit "context mode" to configure.
3. **Derive targets are explicit** — `--agent <slug>` and `--self` are opt-in flags on `torque ai derive`. The default behavior (spawn a new agent) is unchanged.
4. **Rich namespace, shallow depth** — The `torque` object exposes agent, worktree, task, and terminal state in a flat, predictable structure. Template authors don't need to understand Torque internals — just dot into what they need.

---

## Architecture

### The `torque` Jinja2 namespace

Every prompt render injects a `torque` dict into the Jinja2 context alongside `TASK` and user-defined action variables. The namespace is reserved — action variables named `torque` are rejected on save.

```python
torque = {
    "agent": {
        "name":      "my-reviewer",        # display name
        "slug":      "my-reviewer",         # unique identifier
        "type":      "claude-code",         # claude-code | codex | gemini-cli | ""
        "group":     "backend",             # owning group
        "directory": "/Users/me/project",   # working directory
    },

    "context": {
        "is_clean":         True,    # no prior tasks dispatched to this agent
        "tasks_dispatched": 0,       # count of tasks sent to this agent
        "previous_tasks": [          # tasks still linked (agent_id set) to this agent
            # {"task": "Review auth refactor", "lane": "Done", "action": "review"},
        ],
    },

    "worktree": {
        "active":       True,                                       # worktree exists
        "path":         "/Users/me/project/.torque/worktrees/...",    # absolute path
        "branch":       "torque/my-reviewer-a1b2c3d4",               # worktree branch
        "base_branch":  "main",                                     # forked from
        "dirty":        True,                                       # uncommitted changes
        "diff":         {"files": 3, "insertions": 42, "deletions": 7},
        "checkpoints":  1,                                          # checkpoint commits
    },

    "task": {
        "id":              "e4f5a6b7",
        "slug":            "fix-auth-issues",
        "depth":           1,           # pipeline depth (0 = root)
        "is_derived":      True,        # has a parent task
        "parent_task_id":  "c2d3e4f5",
        "labels":          ["derived"],
        "group":           "backend",
    },

    "terminals": [                   # child terminals of the target agent
        {
            "name":            "logs",
            "slug":            "my-reviewer:logs",
            "current_path":    "/Users/me/project",
            "current_process": "tail",
            "current_branch":  "torque/my-reviewer-a1b2c3d4",
        },
    ],
}
```

### Derive-to-agent flow

```
Agent B (reviewer) finishes task T2, wants to send fix back to Agent A
  │
  ├── torque ai derive "Fix the 3 auth issues"   ← creates T3
  │     --action fix                               T3.parent_task_id = T2.id
  │     --agent agent-a                            T3.pipeline_depth = 2
  │
  └── Server resolves --agent agent-a
        │
        ├── dispatch_data = {agent_id: A.id}    ← reuses Agent A
        ├── Renders prompt with torque context:
        │     torque.context.is_clean = False      (A.tasks_dispatched > 0)
        │     torque.context.previous_tasks = [{task: "Implement auth", ...}]
        ├── Sends rendered prompt to A's terminal
        └── Agent A receives abbreviated prompt  ← already has codebase context
```

### Self-derive flow

```
Agent A finishes phase 1 of a multi-phase action
  │
  ├── torque ai derive "Now add tests"           ← creates T2
  │     --action test                              T2.parent_task_id = T1.id
  │     --self                                     equivalent to --agent $TORQUE_CELL_ID
  │
  └── Server dispatches T2 to Agent A
        │
        ├── 2-3s delay (agent still processing derive call)
        ├── Prompt rendered with is_clean=False
        └── New prompt lands in terminal as user input
```

### Dispatch to existing agent (manual)

```
User dispatches task T2 from the board UI, selecting Agent A as target
  │
  ├── dispatch_task with agent_id = A.id        ← reuses Agent A
  ├── Prompt rendered with torque context
  │     torque.context.is_clean = False
  └── Sent to Agent A's terminal immediately
```

---

## Template Examples

### Conditional system prompt

```jinja2
{% if torque.context.is_clean %}
You are a senior engineer working on the {{ torque.worktree.base_branch | default("main") }} branch.
Review the code carefully and provide detailed, actionable feedback.

{{ TASK }}
{% else %}
{{ TASK }}
{% endif %}
```

### Worktree-aware instructions

```jinja2
{% if torque.worktree.active %}
You're working in worktree branch `{{ torque.worktree.branch }}`, forked from `{{ torque.worktree.base_branch }}`.
{% if torque.worktree.dirty %}
Note: there are uncommitted changes ({{ torque.worktree.diff.files }} files, +{{ torque.worktree.diff.insertions }}/-{{ torque.worktree.diff.deletions }}).
{% endif %}
{% endif %}

{{ TASK }}
```

### Pipeline depth awareness

```jinja2
{% if torque.task.is_derived %}
This is a follow-up task at depth {{ torque.task.depth }} in the pipeline.
{% endif %}

{{ TASK }}
```

### Terminal awareness

```jinja2
{% if torque.terminals %}
You have these terminal sessions available:
{% for t in torque.terminals %}
- {{ t.name }}{% if t.current_process %} (running: {{ t.current_process }}){% endif %}
{% endfor %}
{% endif %}

{{ TASK }}
```

### Full multi-phase action

```jinja2
{% if torque.context.is_clean %}
You are an implementation agent. Your job is to implement features in this codebase.

## Environment
- Working directory: {{ torque.agent.directory }}
{% if torque.worktree.active %}
- Branch: `{{ torque.worktree.branch }}` (forked from `{{ torque.worktree.base_branch }}`)
{% endif %}
{% if torque.terminals %}
- Terminals: {{ torque.terminals | map(attribute='name') | join(', ') }}
{% endif %}

## Task
{{ TASK }}

## Guidelines
- Write clean, tested code
- Commit frequently with descriptive messages
- When done, use `torque ai derive` to send to review
{% else %}
Continue working in this session.

{{ TASK }}
{% endif %}
```

---

## Implementation

### 1. Data model — `state.py`

Add one persisted field to `AgentCell`:

```python
@dataclass
class AgentCell:
    ...
    tasks_dispatched: int = 0   # count of tasks sent to this agent (persisted)
```

This counter is incremented in `dispatch_task` **after** the prompt is rendered and sent. On the first dispatch, the counter is `0`, so `is_clean = True`. On the second dispatch, it's `1`, so `is_clean = False`.

The counter is never decremented or reset. Even after `torque ai ready` (which unlinks the task from the agent), the counter persists, correctly reflecting that the agent has prior context.

### 2. Schema migration — `db.py`

In `TorqueDB.init()`:

```python
try:
    cur.execute(
        "ALTER TABLE agents ADD COLUMN tasks_dispatched INTEGER DEFAULT 0")
except Exception:
    pass
```

Update `save_agent` to persist the field. Update `load_all` to read it back (with `0` default for existing rows).

### 3. Torque context builder — `server.py`

New helper function that assembles the `torque` dict from existing state:

```python
def _build_torque_context(state, cell, task):
    """Build the torque namespace dict for Jinja2 template rendering."""

    # Agent identity
    agent = {
        "name": cell.name,
        "slug": cell.slug,
        "type": cell.agent_type,
        "group": cell.group,
        "directory": cell.directory,
    }

    # Dispatch history
    linked = sorted(
        (t for t in state.board_tasks.values()
         if t.agent_id == cell.id and t.id != task.id),
        key=lambda t: t.created_at,
    )
    context = {
        "is_clean": cell.tasks_dispatched == 0,
        "tasks_dispatched": cell.tasks_dispatched,
        "previous_tasks": [
            {"task": t.task, "lane": t.lane, "action": t.action_name}
            for t in linked
        ],
    }

    # Worktree state
    worktree = {
        "active": bool(cell.worktree_path),
        "path": cell.worktree_path,
        "branch": cell.worktree_branch,
        "base_branch": cell.worktree_base_branch,
        "dirty": cell.worktree_dirty,
        "diff": cell.worktree_diff or {},
        "checkpoints": cell.worktree_checkpoints,
    }

    # Current task metadata
    task_ctx = {
        "id": task.id,
        "slug": task.slug,
        "depth": task.pipeline_depth,
        "is_derived": bool(task.parent_task_id),
        "parent_task_id": task.parent_task_id,
        "labels": list(task.labels),
        "group": task.group,
    }

    # Child terminals of the target agent
    terminals = []
    for cid in state._children.get(cell.id, []):
        ch = state.agents.get(cid)
        if ch:
            terminals.append({
                "name": ch.name,
                "slug": ch.slug,
                "current_path": ch.current_path,
                "current_process": ch.current_process,
                "current_branch": ch.current_branch,
            })

    return {
        "agent": agent,
        "context": context,
        "worktree": worktree,
        "task": task_ctx,
        "terminals": terminals,
    }
```

### 4. Thread torque context through rendering — `actions.py`

`render_prompt` and `render_action` accept an optional `torque_context` parameter:

```python
def render_prompt(self, name, variables, base_dir="", torque_context=None):
    raw = self._load_raw(name, base_dir)
    if raw is None:
        return None
    try:
        act = parse_yaml(raw)
    except Exception:
        act = {}
    prompt_raw = self._coalesce_prompt(act)
    if not prompt_raw:
        return None
    prompt_raw = _migrate_syntax(prompt_raw)

    render_vars = dict(variables)
    if torque_context:
        render_vars["torque"] = torque_context
    return self._render_str(prompt_raw, render_vars)
```

Add validation in `save_action` (or `get_action_vars`) to reject action variables named `torque`:

```python
if "torque" in discovered_vars:
    return {"type": "error",
            "message": "'torque' is a reserved variable name"}
```

The Jinja2 `SandboxedEnvironment` already uses `StrictUndefined`, so referencing `torque.foo` in a template where `torque` isn't injected (e.g., a preview render without dispatch context) would raise an error. To handle this gracefully, switch to `ChainableUndefined` or inject a stub `torque` dict with safe defaults during preview renders:

```python
TORQUE_CONTEXT_STUB = {
    "agent":     {"name": "", "slug": "", "type": "", "group": "", "directory": ""},
    "context":   {"is_clean": True, "tasks_dispatched": 0, "previous_tasks": []},
    "worktree":  {"active": False, "path": "", "branch": "", "base_branch": "",
                  "dirty": False, "diff": {}, "checkpoints": 0},
    "task":      {"id": "", "slug": "", "depth": 0, "is_derived": False,
                  "parent_task_id": "", "labels": [], "group": ""},
    "terminals": [],
}
```

This stub is used when `torque_context` is `None` (preview renders, validation), so templates referencing `torque.*` don't error during preview.

### 5. Dispatch integration — `server.py` `dispatch_task`

In the prompt rendering section of `dispatch_task`, after resolving the target agent (`cell`) and task:

```python
# Build torque context for template rendering
torque_ctx = _build_torque_context(state, cell, task)

# Render prompt with torque context
if task.action_name and not data.get("force_no_action"):
    base_dir = cell.directory or await _resolve_base_dir(group)
    tvars = {"TASK": task.task, **(task.action_vars or {})}
    rendered = action_mgr.render_prompt(
        task.action_name, tvars,
        base_dir=base_dir,
        torque_context=torque_ctx)
    ...
```

After the prompt is sent (both new-agent and existing-agent paths):

```python
# Track dispatch count
cell.tasks_dispatched += 1
state._db_save_agent(cell)
state._emit_agent(cell)
```

### 6. Conditional postscript — `server.py` `_build_postscript`

Add an `is_clean` parameter to `_build_postscript`. When `False`, emit a one-liner instead of the full command reference:

```python
def _build_postscript(task, action_data, transitions, is_clean=True):
    if not is_clean:
        return "\n\n---\nUse `torque ai done` when finished, or `torque ai blocked \"reason\"` if stuck.\n"

    # ... existing full postscript with all torque ai commands documented ...
```

Pass `is_clean` from the dispatch context:

```python
postscript = _build_postscript(
    task, action_data, transitions,
    is_clean=torque_ctx["context"]["is_clean"])
```

### 7. Derive-to-agent — `server.py` `ai_report`

In the `derive` branch of `ai_report`, handle `target_agent` and `reuse_self` flags:

```python
elif action == "derive":
    # ... existing validation (transitions, depth limit) ...

    # Create derived task (unchanged)
    new_task = state.board_add_task(...)

    # Determine dispatch target
    target_agent_id = None
    if data.get("reuse_self"):
        target_agent_id = cell.id
    elif data.get("target_agent"):
        resolved = _resolve_agent(state, data["target_agent"])
        if not resolved:
            return {"type": "error",
                    "message": f"Agent not found: {data['target_agent']}"}
        target_agent_id = resolved.id

    if target_agent_id:
        dispatch_data = {
            "cmd": "dispatch_task",
            "id": new_task.id,
            "agent_id": target_agent_id,
        }
        # Inherit worktree from target agent (not calling agent)
        target = state.agents.get(target_agent_id)
        if target and target.worktree_path:
            dispatch_data["inherit_worktree_from"] = target_agent_id
    else:
        dispatch_data = {
            "cmd": "dispatch_task",
            "id": new_task.id,
            "create_agent": True,
        }
        if cell.worktree_path:
            dispatch_data["inherit_worktree_from"] = cell.id

    await state.broadcast()
    dr = await handle_command(dispatch_data)
```

The `_resolve_agent` helper reuses the same resolution logic as the CLI's `resolve_cell` — match by ID, slug, name (case-insensitive), or ID prefix.

**Self-dispatch delay**: When `reuse_self` is set, the calling agent is still processing the `torque ai derive` CLI call. The prompt must arrive after the agent's current turn finishes. In `dispatch_task`, when the target is the calling agent:

```python
if agent_id and cell.session_id:
    if data.get("_self_dispatch"):
        # Self-dispatch: delay so prompt arrives after current turn
        async def _delayed_send(c, p):
            await asyncio.sleep(3)
            if c.session_id:
                await bridge.send_text(c.session_id,
                                       p if p.endswith("\r") else p + "\r")
                c.status = "running"
                state._emit_agent(c)
                await state.broadcast()
        asyncio.create_task(_delayed_send(cell, prompt))
    else:
        await bridge.send_text(cell.session_id,
                               prompt if prompt.endswith("\r") else prompt + "\r")
        cell.status = "running"
        state._emit_agent(cell)
```

The `_self_dispatch` flag is set internally when `reuse_self` flows through to `dispatch_task`.

### 8. CLI flags — `bin/torque`

Add `--agent` and `--self` to the `ai derive` subcommand:

```python
p_derive = ai_sub.add_parser("derive",
    help="Derive a new task and dispatch it")
p_derive.add_argument("description")
p_derive.add_argument("-a", "--action", default="")
p_derive.add_argument("-v", "--var", nargs="*", default=[])
p_derive.add_argument("-g", "--group", default="")
p_derive.add_argument("--agent", default="",
    help="Dispatch to an existing agent (by slug, name, or ID)")
p_derive.add_argument("--self", action="store_true", dest="reuse_self",
    help="Dispatch to the calling agent (same session)")
```

Wire to the API call:

```python
def cmd_ai_derive(args):
    cell, task = _resolve_self_and_task(args)
    kwargs = {"cell_id": cell["id"], "action": "derive",
              "message": args.description}
    if task:
        kwargs["task_id"] = task["id"]
    if args.action:
        kwargs["action_name"] = args.action
    if args.var:
        kwargs["action_vars"] = _parse_vars(args.var)
    if args.group:
        kwargs["group"] = args.group
    if args.reuse_self:
        kwargs["reuse_self"] = True
    elif args.agent:
        kwargs["target_agent"] = args.agent
    resp = api_call("ai_report", port=args.port, **kwargs)
```

**Mutual exclusivity**: `--agent` and `--self` are mutually exclusive. Add a validation check:

```python
if args.reuse_self and args.agent:
    die("--self and --agent are mutually exclusive")
```

### 9. Preview rendering — `server.py` `preview_prompt` / `render_action`

The `preview_prompt` and `render_action` commands render prompts outside of a dispatch context (no real agent or task). Use `TORQUE_CONTEXT_STUB` so templates referencing `torque.*` render with safe defaults:

```python
# In preview_prompt handler
rendered = action_mgr.render_prompt(
    action_name, tvars, base_dir=base_dir,
    torque_context=TORQUE_CONTEXT_STUB)
```

This means `torque.context.is_clean` is `True` in previews, which shows the "full prompt" branch — the more informative preview for the user.

---

## File-by-file Change Summary

| File | Change |
|---|---|
| `torque/state.py` | Add `tasks_dispatched: int = 0` field to `AgentCell` |
| `torque/db.py` | `ALTER TABLE` migration for `tasks_dispatched`. Update `save_agent` / `load_all`. |
| `torque/actions.py` | `render_prompt` / `render_action`: accept `torque_context` param, inject as `torque` into Jinja2 vars. Define `TORQUE_CONTEXT_STUB`. |
| `torque/server.py` | New `_build_torque_context()` helper. Wire into `dispatch_task` prompt rendering. Increment `tasks_dispatched` after send. Pass `is_clean` to `_build_postscript`. Handle `target_agent` / `reuse_self` in `ai_report` derive. Add `_self_dispatch` delay path. Use `TORQUE_CONTEXT_STUB` in preview renders. Reject `torque` as action variable name in `save_action`. |
| `bin/torque` | Add `--agent` and `--self` flags to `ai derive`. Mutual exclusivity check. Wire to API params. |

**No changes to**: action YAML format, board/pipeline data model, task chaining, frontend UI, WebSocket protocol, delta ops.

---

## Edge Cases

### `torque ai ready` then re-dispatch

`ready` clears `task.agent_id`, so `previous_tasks` will be empty on the next dispatch. But `tasks_dispatched` (the counter on the agent) is never decremented, so `is_clean` correctly remains `False`. The agent still has conversational context even though the task link is gone.

### Agent restarted between tasks

If the agent's iTerm2 session is relaunched (e.g., via session resume), the Claude Code conversation context is preserved (via `--resume`). The `tasks_dispatched` counter is persisted in SQLite, so `is_clean` remains correct across daemon restarts.

### Preview renders

Templates referencing `torque.*` get `TORQUE_CONTEXT_STUB` during preview, which defaults to `is_clean: True`. This shows the "full prompt" branch in previews, which is the most useful view.

### `--agent` targeting an agent in a different group

Allowed. The derived task is created in the target agent's group (or the calling agent's group if `--group` isn't specified). The task is then dispatched to the target agent via `agent_id`. No group restriction on agent reuse.

### Worktree inheritance with `--agent`

When `--agent` targets an existing agent, the worktree is inherited from the **target** agent (not the calling agent). This is correct: the target agent is already working in a worktree, and the new task should continue there.

When the target agent has no worktree but the calling agent does, no worktree inheritance occurs. The task runs in the target agent's existing directory. (If worktree sharing is needed, the group's worktree settings handle that on agent creation.)

### Self-dispatch timing

The 3-second delay for `--self` dispatches is a heuristic. The calling agent is executing `torque ai derive --self` as a tool call. The CLI returns immediately, the agent's current turn finishes, and then the new prompt arrives. The 3-second delay covers the gap between the CLI response and the agent outputting its final message. If the agent is slow, the prompt may arrive mid-output — but this is the same race condition as the existing 2-second boot delay for new agents, which works in practice.
