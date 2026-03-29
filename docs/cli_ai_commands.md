# CLI AI Commands — Implementation Plan

## Motivation

Today, the only way Loom knows an agent finished its task is the appended dispatch postscript:

```
When you are done, run `loom task move {slug} Done` to mark the task as complete.
```

This is brittle — it only covers the "done" case and gives the agent no structured way to report richer status like "I'm blocked on user input", "I opened a PR", or "I hit an unrecoverable error". The `loom ai` subcommand group gives agents a first-class reporting interface that updates both the agent cell and the linked board task in a single call.

## Design principles

1. **Zero-config identity** — agents never pass their own ID. The CLI resolves the calling agent from `LOOM_CELL_ID` (env var injected by bridge) or `ITERM_SESSION_ID` (iTerm2 session). Task is auto-resolved from the agent's linked `board_tasks` entry.
2. **Single command, multiple effects** — each `loom ai` subcommand updates the agent cell (activity, needs_attention, error_message), the linked board task (lane, labels, external_url), and triggers a delta broadcast + notification in one shot.
3. **Idempotent** — running the same command twice is safe (e.g. `loom ai done` on an already-Done task is a no-op with a warning).
4. **Composable with existing CLI** — `loom ai` commands are sugar over existing primitives (`board_move_task`, `board_update_task`, agent field updates). Power users can still use the lower-level commands directly.
5. **Agent-type agnostic** — works for Claude Code, Codex, Gemini CLI, or any agent that can run shell commands.

## Command reference

### `loom ai done`

Agent signals task completion.

```
loom ai done [-m MESSAGE] [TASK]
```

**Effects:**
- Moves linked task to the `Done` lane
- Sets agent `activity = ""`, `activity_detail = ""`
- Clears `needs_attention` and `error_message` on agent
- If `-m` provided, stores message as `last_summary` on agent

**Task resolution:** If `TASK` (slug/ID) is omitted, finds the task where `agent_id == cell.id` and `lane` is the group's `dispatch_lane` (default "In Progress"). Dies if ambiguous (multiple in-flight tasks for this agent).

**Example:**
```bash
loom ai done
loom ai done -m "Implemented the feature, all tests pass"
loom ai done fix-login-bug    # explicit task slug
```

---

### `loom ai blocked`

Agent is stuck and needs human input.

```
loom ai blocked REASON [TASK]
```

**Effects:**
- Sets agent `needs_attention = True`
- Sets agent `activity = "waiting"`, `activity_detail = REASON`
- Adds label `blocked` to linked task (if not already present)
- Triggers notification (via existing `notify_on_attention` path)

**Example:**
```bash
loom ai blocked "Need credentials for staging database"
loom ai blocked "Design decision: should we use REST or GraphQL?"
```

---

### `loom ai pr`

Agent opened a pull request.

```
loom ai pr URL [--draft] [TASK]
```

**Effects:**
- Stores URL in `external_url` on linked task
- Adds label `pr:open` (or `pr:draft` if `--draft`) to task, removes stale `pr:*` labels
- Sets agent `activity_detail = "PR opened: {url_short}"`
- Triggers notification

**Example:**
```bash
loom ai pr https://github.com/org/repo/pull/42
loom ai pr https://github.com/org/repo/pull/42 --draft
```

---

### `loom ai merged`

Agent merged or confirmed a PR was merged.

```
loom ai merged [URL] [TASK]
```

**Effects:**
- Moves linked task to `Done` lane
- If URL provided, stores/updates `external_url` on task
- Replaces `pr:open`/`pr:draft` label with `pr:merged`
- Clears agent activity fields
- Triggers notification

**Example:**
```bash
loom ai merged
loom ai merged https://github.com/org/repo/pull/42
```

---

### `loom ai error`

Agent hit an unrecoverable error.

```
loom ai error MESSAGE [TASK]
```

**Effects:**
- Sets agent `error_message = MESSAGE`, `needs_attention = True`
- Adds label `error` to linked task
- Triggers notification (via existing `notify_on_error` path)
- Does **not** move task lane (user decides what to do)

**Example:**
```bash
loom ai error "Tests failing: 3 failures in auth_test.py"
loom ai error "Merge conflict on main that I cannot auto-resolve"
```

---

### `loom ai progress`

Agent reports what it's currently doing. Lightweight status update — no lane changes.

```
loom ai progress MESSAGE [TASK]
```

**Effects:**
- Sets agent `activity_detail = MESSAGE`
- Clears `needs_attention` if previously set (agent is no longer stuck)
- No task lane/label changes

**Example:**
```bash
loom ai progress "Running test suite"
loom ai progress "Refactoring auth module — 3 of 5 files done"
```

---

### `loom ai ready`

Agent signals it's available for another task.

```
loom ai ready [TASK]
```

**Effects:**
- If there's a linked in-progress task, moves it to `Done`
- Clears `agent_id` link from the task
- Clears agent activity, error, and attention fields
- Sets agent `activity_detail = "ready"`
- (Future: could auto-dispatch next Backlog task with matching assignee)

**Example:**
```bash
loom ai ready
```

---

### `loom ai context`

Dumps the agent's current context as structured output. Useful for agents that need to orient themselves (e.g. after resume).

```
loom ai context [--json]
```

**Output:**
- Agent: name, slug, group, status, worktree info
- Task: description, lane, action, labels (if linked)
- Group: name, settings summary

This is a **read-only** command — no state changes. Uses `detect_context()` + SQLite direct reads (works offline).

**Example:**
```bash
loom ai context
loom ai context --json
```

**Sample output:**
```
Agent: fix-auth (fix-auth)
  Group:    Backend
  Status:   running
  Worktree: .loom/worktrees/a1b2c3d4  (loom/fix-auth-a1b2c3d4)
  Base:     main

Task: Fix authentication timeout on staging
  Slug:     fix-authentication-timeout
  Lane:     In Progress
  Action:   bugfix
  Labels:   urgent
```

---

## Identity resolution flow

```
loom ai <subcommand> [args]
        │
        ▼
  ┌─ Read LOOM_CELL_ID env var ──────────────────────┐
  │  (injected by bridge.create_session for all cells)│
  └──────────────────────┬───────────────────────────┘
                         │
                  found? │
              ┌──────────┴──────────┐
              │ yes                  │ no
              ▼                     ▼
     Look up cell in state   Fall back to ITERM_SESSION_ID
     by agent ID             (existing detect_context())
              │                     │
              ▼                     ▼
        cell resolved          cell resolved (or die)
              │
              ▼
  ┌─ Find linked task ──────────────────────────┐
  │  board_tasks where agent_id == cell.id      │
  │  and lane == dispatch_lane (or any active)  │
  └──────────────────────┬──────────────────────┘
                         │
              ┌──────────┴──────────┐
              │ 0 tasks     1 task    2+ tasks
              │                       │
              ▼             ▼         ▼
           (no task     use it    die with
            context)              "ambiguous, specify TASK")
```

For commands where a task is optional (`progress`, `context`), missing task is fine. For commands where a task is required (`done`, `merged`, `pr`), die if no task is linked.

The explicit `TASK` positional arg (slug or ID) is always accepted as an override for disambiguation.

## Server-side changes

### New command: `ai_report`

A single server command handles all `loom ai` subcommands. The CLI translates each subcommand into a structured payload:

```json
{
  "cmd": "ai_report",
  "cell_id": "...",
  "action": "done|blocked|pr|merged|error|progress|ready",
  "message": "...",
  "task_id": "...",
  "url": "...",
  "draft": false
}
```

**In `server.py`**, the `ai_report` handler:

1. Validates `cell_id` exists in state
2. Resolves linked task (from `task_id` or auto-detect)
3. Applies action-specific mutations:
   - Agent cell field updates (via `state._emit_agent()`)
   - Task field updates (via `state.update_task()`)
   - Task lane moves (via `state.move_task()`)
4. Emits deltas + broadcast
5. Triggers notifications where appropriate

This keeps the CLI thin (just argument parsing + HTTP call) and the server authoritative on state transitions.

### Alternative: CLI-side composition

Instead of a single `ai_report` command, the CLI could compose multiple existing API calls:

```python
# loom ai done
api_call("board_move_task", id=task_id, lane="Done")
api_call("ai_update_agent", id=cell_id, activity="", needs_attention=False, ...)
```

**Trade-off:** Simpler server (no new command) but two HTTP round-trips and non-atomic updates. A race could leave the task moved but the agent fields stale.

**Recommendation:** Single `ai_report` command for atomicity. It's one new handler that delegates to existing `MatrixState` methods internally.

## Data model changes

### No new fields needed

All required state is already modeled:

| Need | Existing field |
|---|---|
| PR URL | `BoardTask.external_url` |
| PR status | `BoardTask.labels` (convention: `pr:open`, `pr:draft`, `pr:merged`) |
| Agent blocked | `AgentCell.needs_attention` + `activity = "waiting"` |
| Agent error | `AgentCell.error_message` + `needs_attention` |
| Progress text | `AgentCell.activity_detail` |
| Task done | `BoardTask.lane = "Done"` |
| Summary | `AgentCell.last_summary` |

### Label conventions

The `loom ai` commands use structured labels with `:` separators for machine-readable status:

- `pr:open` — PR opened, awaiting review
- `pr:draft` — draft PR
- `pr:merged` — PR merged
- `blocked` — agent waiting for user input
- `error` — agent encountered an error

These are additive (don't remove user-set labels) but exclusive within prefix (e.g. setting `pr:merged` removes `pr:open`).

## Webview changes

### Task card enhancements

- **PR badge**: If task has `external_url` and a `pr:*` label, show a small link icon on the card. Clicking opens the URL in the default browser (via `window.open()`).
- **Blocked indicator**: If task has `blocked` label, show a yellow warning icon on the card.
- **Error indicator**: If task has `error` label, show a red icon on the card.

These are small CSS/render additions — no new components.

### Agent cell enhancements

The existing status dot and activity detail already cover agent-side rendering. No changes needed — `needs_attention` already shows a yellow indicator.

## Dispatch prompt update

Replace the current hardcoded postscript:

```python
# Current
prompt += f"\n\nWhen you are done, run `loom task move {task_ref} Done` to mark the task as complete."
```

With a richer instruction block:

```python
prompt += f"""

When reporting progress, use these commands:
- `loom ai done` — when the task is complete
- `loom ai pr URL` — when you open a pull request
- `loom ai merged` — when a PR is merged
- `loom ai blocked "reason"` — when you need user input
- `loom ai error "message"` — when you hit an unrecoverable error
- `loom ai progress "message"` — to report what you're working on
"""
```

This could also be:
- A configurable template variable (`{{ LOOM_INSTRUCTIONS }}` auto-injected)
- A group setting toggle (`dispatch_include_ai_instructions: true`)
- Appended only when the agent type supports CLI (has shell access)

**Recommendation:** Keep it as a hardcoded postscript initially (simple), make it configurable in a follow-up.

## CLI implementation

### File changes

**`bin/loom`** — new subcommand group:

```python
# -- ai (agent reporting) ---------------------------------------------------

def _resolve_self(port):
    """Resolve the calling agent's cell and linked task.
    Uses LOOM_CELL_ID (preferred) or ITERM_SESSION_ID fallback.
    Returns (cell_dict, task_dict_or_None).
    """
    cell_id = os.environ.get("LOOM_CELL_ID", "")
    state_data = get_state(port)

    if cell_id:
        cell = state_data.get("agents", {}).get(cell_id)
        if not cell:
            die(f"LOOM_CELL_ID={cell_id} not found in state")
    else:
        cell, _parent, _group = detect_context(state_data)
        if not cell:
            die("Cannot identify agent — not in a Loom-managed session\n"
                "  (LOOM_CELL_ID not set and ITERM_SESSION_ID not matched)")
        cell_id = cell["id"]

    # Find linked task(s)
    tasks = state_data.get("board_tasks", {})
    linked = [t for t in tasks.values() if t.get("agent_id") == cell_id]
    # Prefer in-progress tasks
    active = [t for t in linked if t.get("lane") not in ("Done", "Backlog")]
    task = active[0] if len(active) == 1 else None

    return cell, task, active


def _resolve_self_and_task(args, require_task=True):
    """Resolve agent + task, with optional explicit TASK override."""
    cell, task, active = _resolve_self(args.port)
    explicit = getattr(args, "task_ref", None)

    if explicit:
        st = get_state(args.port)
        task = resolve_task(st, explicit)
    elif require_task and not task:
        if len(active) > 1:
            slugs = ", ".join(t.get("slug", t["id"][:6]) for t in active)
            die(f"Multiple active tasks for this agent: {slugs}\n"
                "  Specify the task: loom ai done TASK_SLUG")
        die("No active task linked to this agent")

    return cell, task


def cmd_ai_done(args):
    cell, task = _resolve_self_and_task(args)
    kwargs = {
        "cell_id": cell["id"],
        "action": "done",
    }
    if task:
        kwargs["task_id"] = task["id"]
    if args.message:
        kwargs["message"] = args.message
    resp = api_call("ai_report", port=args.port, **kwargs)
    check_ok(resp)
    task_label = task.get("slug", task["id"][:6]) if task else ""
    print(f"Done{' — ' + task_label if task_label else ''}")


def cmd_ai_blocked(args):
    cell, task = _resolve_self_and_task(args, require_task=False)
    kwargs = {
        "cell_id": cell["id"],
        "action": "blocked",
        "message": args.reason,
    }
    if task:
        kwargs["task_id"] = task["id"]
    resp = api_call("ai_report", port=args.port, **kwargs)
    check_ok(resp)
    print(f"Blocked: {args.reason}")


def cmd_ai_pr(args):
    cell, task = _resolve_self_and_task(args)
    kwargs = {
        "cell_id": cell["id"],
        "action": "pr",
        "url": args.url,
    }
    if task:
        kwargs["task_id"] = task["id"]
    if args.draft:
        kwargs["draft"] = True
    resp = api_call("ai_report", port=args.port, **kwargs)
    check_ok(resp)
    print(f"PR recorded: {args.url}")


def cmd_ai_merged(args):
    cell, task = _resolve_self_and_task(args)
    kwargs = {
        "cell_id": cell["id"],
        "action": "merged",
    }
    if task:
        kwargs["task_id"] = task["id"]
    if args.url:
        kwargs["url"] = args.url
    resp = api_call("ai_report", port=args.port, **kwargs)
    check_ok(resp)
    print("Merged")


def cmd_ai_error(args):
    cell, task = _resolve_self_and_task(args, require_task=False)
    kwargs = {
        "cell_id": cell["id"],
        "action": "error",
        "message": args.message,
    }
    if task:
        kwargs["task_id"] = task["id"]
    resp = api_call("ai_report", port=args.port, **kwargs)
    check_ok(resp)
    print(f"Error reported: {args.message}")


def cmd_ai_progress(args):
    cell, task = _resolve_self_and_task(args, require_task=False)
    kwargs = {
        "cell_id": cell["id"],
        "action": "progress",
        "message": args.message,
    }
    if task:
        kwargs["task_id"] = task["id"]
    resp = api_call("ai_report", port=args.port, **kwargs)
    check_ok(resp)
    print(f"Progress: {args.message}")


def cmd_ai_ready(args):
    cell, task = _resolve_self_and_task(args, require_task=False)
    kwargs = {
        "cell_id": cell["id"],
        "action": "ready",
    }
    if task:
        kwargs["task_id"] = task["id"]
    resp = api_call("ai_report", port=args.port, **kwargs)
    check_ok(resp)
    print("Ready for next task")


def cmd_ai_context(args):
    cell_id = os.environ.get("LOOM_CELL_ID", "")
    # Use local state (works offline)
    st = get_state_local(args.port)

    if cell_id:
        cell = st.get("agents", {}).get(cell_id)
    else:
        cell, _, _ = detect_context(st)

    if not cell:
        die("Cannot identify agent — not in a Loom-managed session")

    if args.json:
        # Include linked task
        tasks = st.get("board_tasks", {})
        linked = [t for t in tasks.values()
                  if t.get("agent_id") == cell["id"]]
        print(json.dumps({"agent": cell, "tasks": linked}, indent=2))
        return

    # Human-readable summary
    ...
```

### Argparse registration

```python
# -- ai (agent reporting) --------------------------------------------------
ai = sub.add_parser("ai", help="Agent reporting (used by AI agents)")
ai.set_defaults(func=None, _sub_parser=ai)
ai_sub = ai.add_subparsers(dest="ai_cmd")

p = ai_sub.add_parser("done", help="Mark task as done")
p.add_argument("-m", "--message", help="Completion summary")
p.add_argument("task_ref", nargs="?", help="Task slug/ID (auto-detected)")
p.set_defaults(func=cmd_ai_done)

p = ai_sub.add_parser("blocked", help="Signal blocked on user input")
p.add_argument("reason", help="Why the agent is blocked")
p.add_argument("task_ref", nargs="?", help="Task slug/ID (auto-detected)")
p.set_defaults(func=cmd_ai_blocked)

p = ai_sub.add_parser("pr", help="Report a pull request opened")
p.add_argument("url", help="Pull request URL")
p.add_argument("--draft", action="store_true", help="Mark as draft PR")
p.add_argument("task_ref", nargs="?", help="Task slug/ID (auto-detected)")
p.set_defaults(func=cmd_ai_pr)

p = ai_sub.add_parser("merged", help="Report PR merged")
p.add_argument("url", nargs="?", help="Pull request URL")
p.add_argument("task_ref", nargs="?", dest="task_ref",
               help="Task slug/ID (auto-detected)")
p.set_defaults(func=cmd_ai_merged)

p = ai_sub.add_parser("error", help="Report an unrecoverable error")
p.add_argument("message", help="Error description")
p.add_argument("task_ref", nargs="?", help="Task slug/ID (auto-detected)")
p.set_defaults(func=cmd_ai_error)

p = ai_sub.add_parser("progress", help="Report current progress")
p.add_argument("message", help="Progress description")
p.add_argument("task_ref", nargs="?", help="Task slug/ID (auto-detected)")
p.set_defaults(func=cmd_ai_progress)

p = ai_sub.add_parser("ready", help="Signal ready for next task")
p.add_argument("task_ref", nargs="?", help="Task slug/ID (auto-detected)")
p.set_defaults(func=cmd_ai_ready)

p = ai_sub.add_parser("context", help="Show current agent context")
p.set_defaults(func=cmd_ai_context)
```

## Server-side handler

In `server.py`, inside the command dispatch block:

```python
elif cmd == "ai_report":
    cell_id = data.get("cell_id", "")
    action = data.get("action", "")
    message = data.get("message", "")
    task_id = data.get("task_id", "")
    url = data.get("url", "")
    is_draft = data.get("draft", False)

    cell = state.get_cell(cell_id)
    if not cell:
        result = {"ok": False, "error": f"Cell {cell_id} not found"}
        break

    task = state.board_tasks.get(task_id) if task_id else None

    if action == "done":
        cell.activity = ""
        cell.activity_detail = ""
        cell.needs_attention = False
        cell.error_message = ""
        if message:
            cell.last_summary = message
        state._emit_agent(cell)

        if task:
            state.move_task(task.id, "Done")

    elif action == "blocked":
        cell.needs_attention = True
        cell.activity = "waiting"
        cell.activity_detail = message
        state._emit_agent(cell)

        if task:
            _add_label(task, "blocked")
            state._emit_task(task)
            state._db_save_task(task)

    elif action == "pr":
        cell.activity_detail = f"PR opened"
        state._emit_agent(cell)

        if task:
            task.external_url = url
            _replace_prefix_label(task, "pr:", "pr:draft" if is_draft else "pr:open")
            state._emit_task(task)
            state._db_save_task(task)

    elif action == "merged":
        cell.activity = ""
        cell.activity_detail = ""
        cell.needs_attention = False
        cell.error_message = ""
        state._emit_agent(cell)

        if task:
            if url:
                task.external_url = url
            _replace_prefix_label(task, "pr:", "pr:merged")
            state.move_task(task.id, "Done")

    elif action == "error":
        cell.error_message = message
        cell.needs_attention = True
        state._emit_agent(cell)

        if task:
            _add_label(task, "error")
            state._emit_task(task)
            state._db_save_task(task)

    elif action == "progress":
        cell.activity_detail = message
        if cell.needs_attention:
            cell.needs_attention = False
        state._emit_agent(cell)

    elif action == "ready":
        cell.activity = ""
        cell.activity_detail = "ready"
        cell.needs_attention = False
        cell.error_message = ""
        state._emit_agent(cell)

        if task:
            state.move_task(task.id, "Done")
            task.agent_id = ""
            state._emit_task(task)
            state._db_save_task(task)

    result = {"ok": True}
    await state.broadcast()
```

Helper functions for label manipulation:

```python
def _add_label(task, label):
    if label not in task.labels:
        task.labels.append(label)

def _remove_label(task, label):
    task.labels = [l for l in task.labels if l != label]

def _replace_prefix_label(task, prefix, new_label):
    task.labels = [l for l in task.labels if not l.startswith(prefix)]
    task.labels.append(new_label)
```

## Implementation order

### Phase 1 — Core commands (MVP)

1. **`_resolve_self` + `_resolve_self_and_task`** in `bin/loom` — identity resolution
2. **`ai_report` handler** in `server.py` — single handler for all actions
3. **CLI subcommands**: `done`, `blocked`, `error`, `progress`, `context`
4. **Update dispatch postscript** — replace `loom task move` with `loom ai` instructions
5. **Label helpers** in `server.py`

### Phase 2 — PR tracking

6. **CLI subcommands**: `pr`, `merged`
7. **Task card PR badge** in `board.js` — clickable link icon for `external_url`
8. **Label-based indicators** in `board.js` — visual badges for `pr:*`, `blocked`, `error` labels

### Phase 3 — Ready + auto-dispatch

9. **CLI subcommand**: `ready`
10. **Auto-dispatch**: when an agent reports `ready`, check Backlog for tasks with matching `assignee` prefix and auto-dispatch the next one. This is the foundation for continuous agent pools.

### Phase 4 — Polish

11. **Configurable dispatch instructions** — group setting to control what `loom ai` instructions are appended to the dispatch prompt
12. **`loom ai context` enrichment** — include recent event log, worktree diff summary, sibling agent status

## Files touched

```
bin/loom                  # ~200 lines: ai subcommand group, _resolve_self, 8 handlers
loom/server.py            # ~80 lines: ai_report handler, label helpers
loom/state.py             # 0 lines (no new fields)
static/js/board.js        # ~30 lines: PR badge, label indicators on cards
static/style.css          # ~15 lines: badge styles
CLAUDE.md                 # Document new commands
```

## Compatibility notes

- **Existing `loom task move X Done`** still works — agents using the old postscript won't break.
- **`external_url`** field already exists on `BoardTask` but is unused — repurposing it for PR URLs requires no schema migration.
- **Labels** are free-form strings — the `pr:*` / `blocked` / `error` conventions are just conventions, not enforced at the schema level.
- **`LOOM_CELL_ID`** is already injected into every Loom-managed session — no bridge changes needed.

## Decisions

1. **`loom ai done` auto-trigger worktree merge** — will be a group setting (not in MVP, added when the setting exists).
2. **Daemon required** — yes, all `loom ai` commands (except `context`) require the daemon. No offline queueing.
3. **Notification customization** — not implementing per-action toggles now. Existing `notify_on_finish` / `notify_on_error` / `notify_on_attention` settings cover the cases.
4. **`loom ai ready` auto-dispatch** — no. When pipelines are introduced, the user will decide what happens on ready.
