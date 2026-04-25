# Implementation Plan: Workflow Automation (Phase 4a)

**Roadmap phase**: 4 — Workflow Automation
**Status**: Implemented (Jinja2 actions, task dispatch/create, action-to-task flow, structured task fields)
**Goal**: Make Loom a task runner, not just a session manager. `loom task dispatch` combines ticket creation, agent launch, worktree setup, and task delivery into a single operation. `loom task create` parks tickets for later pickup. Actions make these tasks repeatable and integrate with the board's ticketing system.

---

## The Problem

Creating an agent for a task today requires multiple steps: create the agent, wait for it to boot, then send it a prompt. If the group has worktrees enabled, you also need to wait for the worktree to be created. And if you want specific settings (different command, env vars, a particular directory), you either configure the group settings or pass flags every time.

For recurring workflows — bug fixes, code reviews, dependency updates — you end up repeating the same setup. There's no way to say "spin up my standard bug-fix agent and tell it to fix this issue."

## Design Principles

1. **Actions are data, not code** — An action is a JSON file with pre-filled agent settings. No scripting language, no conditionals, no Turing-completeness. Complex workflows are shell scripts that call `loom dispatch`.
2. **`loom dispatch` is the entry point** — One command that creates an agent from an action and sends it a prompt. It composes existing primitives (`add_agent` + `send_text` + `wait_for_idle`).
3. **Actions layer on top of GroupSettings** — Actions don't replace group settings. They provide per-task overrides. The resolution cascade is: action field → group setting → system default.
4. **No new server commands for v1** — Actions are resolved client-side in the CLI. The server receives the same `add_agent` and `send_text` payloads it already handles. This keeps the server simple and actions a CLI-only concept initially.

---

## Architecture

```
loom dispatch "Fix the login bug" --action bugfix -g Backend
  │
  │  1. Load action from .loom/actions/bugfix.yaml
  │  2. Merge action fields with CLI flags
  │
  ├──► POST /api/cmd  {"cmd": "add_agent", ...}
  │    Server creates agent + worktree + auto-terminals
  │    Returns state with new agent ID
  │
  ├──► Poll until agent has a session_id and status != "stopped"
  │    (agent is booted and ready to receive input)
  │
  ├──► POST /api/cmd  {"cmd": "send_text", id, text: rendered_prompt}
  │    Sends the task prompt to the agent
  │
  └──► (if --wait) Poll until turn completes, print summary
```

Actions stay in the CLI layer. The server is unaware of them — it just receives the resolved fields. This means actions work immediately without a server deploy.

---

## Action Format

Actions live in `.loom/actions/` relative to the git repo root (version-controlled — only `.loom/worktrees/` is gitignored). Each action is a YAML file. Actions support **subdirectory namespaces**: `oneshot/feature.yaml` → action name `oneshot/feature`.

```yaml
name: feature/implement
description: Implement a feature in an isolated worktree

agent:
  name_prefix: impl
  tab_color: "#3fb950"

worktree: true

labels:
  - feature

transitions:
  - action: feature/review
    when: implementation is complete and ready for review

prompt: |
  You are implementing a feature in an isolated worktree branch. Your work will be reviewed by a separate agent when you're done, so focus on getting the implementation right — not on self-review.

  ## Task

  {{ TASK }}

  ## Approach

  - Read the relevant code first to understand the existing patterns and architecture
  - Write clean, minimal code — no speculative abstractions or premature generalization
  - Add or update tests to cover the new behavior
  - Run the test suite to make sure nothing is broken
  - Keep the diff focused — no unrelated changes
```

### Field reference

| Field | Type | Description |
|---|---|---|
| `name` | string | Action identifier (matches filename without `.yaml`) |
| `description` | string | Human-readable description for `loom action list` |
| `agent.name_prefix` | string | Agent name prefix. Full name: `{prefix}-{task_slug}` |
| `agent.command` | string | Boot command override. Empty = use group default |
| `agent.directory` | string | Working directory override. Empty = use group default |
| `agent.profile` | string | iTerm2 profile override |
| `agent.shell` | string | Shell override |
| `agent.tab_color` | string | Hex color override |
| `agent.env_vars` | dict | Additional env vars (merged on top of group defaults) |
| `worktree` | bool | Create a git worktree for this agent (overrides group setting) |
| `prompt` | string | Unified prompt template (Jinja2). Must contain `{{ TASK }}`. |
| `group` | string | Target group override. Empty = use CLI/UI group. |
| `labels` | list | Labels for orchestration tagging. Optional. |
| `transitions` | list | Valid next steps: `{action: name, when: desc}` or `{ask: true, when: desc}` |
| `max_depth` | int | Max pipeline derivation depth. Optional. |
| `terminals` | list | Child terminals to create alongside the agent |
| `terminals[].name` | string | Terminal name |
| `terminals[].command` | string | Boot command for the terminal |
| `terminals[].directory` | string | Working directory (empty = same as agent) |
| `terminals[].init_script` | string | Init script path |

### Action resolution

Fields cascade: **CLI flag → action field → group setting → system default**.

An empty string in an action field means "fall through to group settings." This lets actions be sparse — a review action might only set `prompt` and `tab_color`, inheriting everything else from the group.

### Variable interpolation

Only the `prompt` field is rendered through Jinja2 with the provided variables. Variables are auto-discovered from the AST — no explicit declaration needed. Default values are extracted from `| default()` filters. All other fields are plain YAML.

The `TASK` variable is always the task description passed to `loom task dispatch`. Custom variables are passed via `-v KEY=VALUE`.

Legacy `${VAR}` syntax is automatically migrated to `{{ VAR }}`. Old-format actions with `task:`+`instructions:`+`context:`+`criteria:` are auto-coalesced into a single `prompt` on load.

---

## CLI Commands

### `loom task dispatch`

Create a ticket, launch an agent, and send the task. The ticket is placed in the "In Progress" lane and linked to the new agent.

```
loom task dispatch <description> [flags]

Arguments:
  description              Task description (sent to agent)

Flags:
  -t, --action NAME        Action name (looks in .loom/actions/)
  -g, --group GROUP        Target group (auto-detected if omitted)
  -n, --name NAME          Agent name override
  -a, --assign PREFIX      Assign to agent name prefix (e.g. 'frontend')
  -c, --command CMD        Boot command override
  -d, --directory DIR      Working directory override
  -v, --var KEY=VALUE      Action variables
  -l, --labels LABELS      Comma-separated labels
  -w, --wait               Wait for the agent to finish
  --no-task                Create agent but don't send the task text
```

**Examples:**

```bash
# Dispatch with action
loom task dispatch "Fix the login bug" -t fix --wait

# With custom test command
loom task dispatch "Fix it" -t fix -v TEST_COMMAND=pytest --wait

# Assign to agent pool
loom task dispatch "Review PR #42" -t review -a review --labels urgent

# Fire and forget
loom task dispatch "Update all dependencies" -t migrate
```

### `loom task create`

Create a ticket in the Backlog lane without launching an agent.

```
loom task create <description> [flags]

Arguments:
  description              Task description

Flags:
  -g, --group GROUP        Target group (auto-detected if omitted)
  -a, --assign PREFIX      Assign to agent name prefix
  -l, --labels LABELS      Comma-separated labels
```

**Examples:**

```bash
# Add to backlog
loom task create "Refactor the auth module"

# With assignment and labels
loom task create "Add rate limiting to API" -a backend -l feature,api
```

### `loom action`

Action management commands.

```
loom action list                    # list available actions
loom action show <name>             # show action contents
loom action create <name>           # create an action interactively
```

`loom action list` scans `.loom/actions/` and shows name + description for each. `loom action show` pretty-prints the JSON. `loom action create` writes a starter action file.

---

## Implementation Steps

### Step 1: Action loader

**File**: `bin/loom` (new functions)

- `find_actions_dir()` — walk up from cwd to find `.loom/actions/`, return path or None
- `load_action(name)` — read and parse `.loom/actions/{name}.yaml`, return dict
- `list_actions()` — scan dir, return list of `{name, description}`
- `render_prompt(action, task, agent_name, agent_id, branch, directory)` — substitute `${TASK}` etc.

### Step 2: `loom dispatch` command

**File**: `bin/loom` (new subcommand)

Sequence:
1. Load action (if `--action` given)
2. Resolve agent name: `--name` flag → `{action.name_prefix}-{task_slug}` → auto-generated
3. Resolve group: `--group` flag → context detection → die
4. Build `add_agent` payload, merging action fields with CLI flags
5. Call `add_agent` API
6. Poll state until the new agent has `session_id` and `status != "stopped"` (agent booted)
7. Render prompt with variable substitution
8. Call `send_text` API
9. If `--wait`: call `wait_for_idle`

### Step 3: `loom action` commands

**File**: `bin/loom` (new subcommands)

- `loom action list` — scan `.loom/actions/`, print table
- `loom action show <name>` — pretty-print YAML
- `loom action create <name>` — write a starter action to `.loom/actions/{name}.yaml`

### Step 4: Built-in starter actions

**Files**: `actions/` directory in the repo (shipped as examples, not auto-installed)

Five starter actions in subdirectory namespaces:
- `oneshot/feature.yaml` — quick feature, no pipeline
- `oneshot/fix.yaml` — quick bugfix, no pipeline
- `feature/implement.yaml` — implement feature in worktree (→ review)
- `feature/review.yaml` — code review (→ fix-review or ask human)
- `feature/fix-review.yaml` — fix review issues (→ review)

Users copy these into their project's `.loom/actions/` and customize.

### Step 5: Agent boot readiness

After `add_agent`, the CLI needs to wait for the agent to be ready before sending the prompt. The agent isn't ready until the iTerm2 session exists and the boot command has been sent.

**Approach**: Poll state every 500ms until the agent's `session_id` is non-empty and `status` is not `"stopped"`. Cap at 15 seconds. This is fast — session creation takes < 1 second typically.

```python
def wait_for_boot(cell_id, port, timeout=15):
    """Poll until agent has a session and is running."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.5)
        state = get_state(port)
        cell = state.get("agents", {}).get(cell_id)
        if cell and cell.get("session_id") and cell.get("status") != "stopped":
            return cell
    return None
```

A short additional delay (~1s) after boot detection ensures the shell and boot command have had time to initialize before we send the prompt. This matters for Claude Code, which needs a moment to start up.

---

## What We're NOT Building (Yet)

- **Action inheritance** — Actions are flat. No `extends: base-action` chains.
- **Conditional logic in actions** — Jinja2 variables and filters only. No `{% if %}` blocks. Use separate actions for different scenarios.
- **Pipeline composition** — Now supported via `transitions` in actions and `loom ai derive`. Actions can declare valid next steps, forming pipelines (e.g. implement → review ↔ fix-review).
- **Retry / fallback** — Deferred. Retry is `loom task dispatch --wait || loom task dispatch --wait` in a shell script for now.
- **Auto-assignment** — Agents don't yet automatically pick up tickets matching their assignee prefix. Manual assignment or explicit dispatch required.

---

## File Changes Summary

| File | Change |
|---|---|
| `loom/actions.py` | Action loading, Jinja2 rendering, variable discovery, `render_action` returns structured fields, `load_action_raw` for editor, scope-aware `list_actions` with overridden detection |
| `loom/server.py` | `add_agent_from_action` handler, `render_action` command for board integration, `save_action` / `delete_action` CRUD commands (scope-aware) |
| `bin/loom` | `task dispatch`, `task create`, `action` subcommands, action loader |
| `static/js/modals.js` | Task create/edit modal with action picker and prompt preview |
| `static/js/board.js` | Inline "+ Add task" composer for quick task creation |
| `static/js/actions.js` | **New** — Actions panel app (dropdown with Project/User optgroups, structured form editor, Jinja2 syntax highlighting, save/duplicate/delete, scope picker) |
| `actions/oneshot/*.yaml` | Two standalone actions: feature, fix |
| `actions/feature/*.yaml` | Three pipeline actions: implement, review, fix-review |
| `docs/roadmap.md` | Update Phase 4 and Phase 5 status |
