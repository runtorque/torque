# Implementation Plan: CLI & REST API

**Roadmap phase**: 3 — CLI & Remote Control
**Status**: Planned
**Goal**: Make Loom scriptable from the terminal. A `loom` CLI talks to the daemon over HTTP, enabling shell scripts, automation, and other agents to control Loom programmatically.

---

## The Problem

Loom is currently only controllable from the Toolbelt webview. There's no way to:

- Script agent creation from a shell (`for issue in ...; do loom agent new ...`)
- Send a command to an agent from another terminal
- Check agent status from a CI pipeline or cron job
- Compose Loom with other tools (pipes, `xargs`, `jq`, `watch`)
- Let an agent inside Loom spawn sibling agents (self-scaling)

The daemon already has all the command logic — it just only accepts commands over WebSocket from the webview. We need a second door.

## Design Principles

1. **REST for commands, WS for streams** — CLI commands are request-response. Add a `POST /api/cmd` HTTP endpoint that accepts the same `{"cmd": ...}` payloads as the WS handler and returns JSON. No WebSocket needed in the CLI for most operations.
2. **Single file, stdlib only** — The CLI is one Python script (`loom`) using only `urllib`, `json`, `sys`, `argparse`. No pip install, no venv. Runs on macOS system Python 3.
3. **Same command vocabulary** — The REST endpoint dispatches through the same `handle_command` function as the WS handler. One code path, zero drift.
4. **Machine-friendly output** — Default output is human-readable tables. `--json` flag on any command returns raw JSON for piping.
5. **Progressive disclosure** — `loom status` just works. Power features (filters, JSON output, send-text) are there when needed but don't clutter the basics.

---

## Architecture

```
┌────────────────────────────────┐
│        loom CLI (Python)       │
│     single file, stdlib only   │
│                                │
│   argparse → HTTP POST /api/cmd│
│   urllib.request → JSON result │
└──────────────┬─────────────────┘
               │ HTTP (localhost:18932)
               ▼
┌──────────────────────────────────────┐
│           Loom Daemon                │
│                                      │
│  /api/cmd  ──→ handle_command()  ◄── /ws
│  (new)         (existing logic)      (existing)
│                                      │
│  Returns JSON: {ok, data, error}     │
└──────────────────────────────────────┘
```

The WS handler and REST handler share the same `handle_command()` function. The only refactor needed is making `handle_command` return a result dict instead of writing directly to a WebSocket.

---

## Server Changes

### Refactor: Extract command results from `handle_command`

Currently `handle_command(data, ws)` writes responses to `ws` directly and has no return value. We need it to return a result so the REST endpoint can use it.

**Approach**: Introduce a lightweight `CommandResult` that captures what was previously sent directly to the WS client. The function returns this result; the WS handler sends it over WS, the REST handler sends it as HTTP JSON.

```python
# Before (current)
async def handle_command(data: dict, ws: web.WebSocketResponse):
    cmd = data.get("cmd")
    if cmd == "get_config":
        await ws.send_str(json.dumps({"type": "config", ...}))
        return
    ...
    await state.broadcast()

# After (refactored)
async def handle_command(data: dict) -> dict | None:
    cmd = data.get("cmd")
    if cmd == "get_config":
        return {"type": "config", ...}
    ...
    await state.broadcast()
    return None  # broadcast handles the response
```

The WS handler wrapper becomes:

```python
async for msg in ws:
    result = await handle_command(json.loads(msg.data))
    if result:
        await ws.send_str(json.dumps(result))
```

### New endpoint: `POST /api/cmd`

```python
async def handle_api_cmd(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    cmd = data.get("cmd")
    if not cmd:
        return web.json_response({"ok": False, "error": "missing 'cmd'"}, status=400)

    try:
        result = await handle_command(data)
        # For mutation commands, return current state snapshot
        payload = result if result else {"type": "state", **state.to_dict()}
        return web.json_response({"ok": True, "data": payload})
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
```

Route registration:

```python
app_server.router.add_post("/api/cmd", handle_api_cmd)
```

### Commands that need special REST handling

Most commands (add_group, add_agent, remove_agent, etc.) are fire-and-forget mutations — they modify state and broadcast. The REST endpoint returns the updated state after the mutation. A few need attention:

| Command | Current behavior | REST behavior |
|---|---|---|
| `get_config` | Sends config to requesting WS client | Returns config as JSON |
| `get_group_settings` | Sends settings to requesting WS client | Returns settings as JSON |
| `worktree_history` | Sends history to requesting WS client | Returns history as JSON |
| `add_agent` | Creates session, broadcasts state | Returns `{ok, data: {id, name, group}}` — the CLI needs the new agent's ID |
| `add_terminal` | Creates session, broadcasts state | Returns `{ok, data: {id, name, parent_id}}` |
| `restart` | Calls `os.execv()` | Returns `{ok}` before exec — response may be cut off |
| `send_text` | Sends text to iTerm2 session | Returns `{ok}` |

For `add_agent` and `add_terminal`: the handler already has the new cell's ID in scope. We just need to return it.

---

## CLI Design

### Command structure

```
loom <noun> <verb> [args] [flags]

Nouns: status, group, agent, terminal, worktree, broadcast, send, logs
```

### Full command reference

```
loom status                              Show all groups and agents
loom status -g <group>                   Show agents in a specific group
loom status <id|name>                    Show detail for one agent

loom group add <name>                    Create a group
loom group remove <name>                 Remove a group (cascade deletes contents)
loom group rename <old> <new>            Rename a group
loom group settings <name>               Show group settings
loom group settings <name> -s key=val    Update a group setting

loom agent add <name> -g <group>         Create an agent in a group
  [-c command] [-d directory]
  [--profile p] [--color #hex]
loom agent remove <id|name>              Remove agent (cascade deletes children)
loom agent focus <id|name>               Focus/switch to agent's iTerm2 session
loom agent relaunch <id|name>            Restart a stopped agent
loom agent move <id|name> -g <group>     Move agent to different group
loom agent edit <id|name>                Update agent name or color
  [--name n] [--color #hex]

loom terminal add <name> -p <parent>     Create child terminal under agent
  [-c command] [-d directory]
  [--profile p] [--color #hex]
loom terminal remove <id|name>           Remove terminal
loom terminal reparent <id> -p <parent>  Attach terminal to different agent
loom terminal reparent <id> --detach     Detach terminal from parent

loom send <id|name> <text>               Send text to a session

loom worktree create <id|name>           Create worktree for agent
  [--relaunch]
loom worktree remove <id|name>           Remove worktree
  [--relaunch]
loom worktree checkpoint <id|name>       Create checkpoint commit
loom worktree history <id|name>          Show checkpoint history
loom worktree rollback <id|name> <sha>   Rollback to checkpoint

loom logs [-f]                           Tail daemon log (doesn't hit API)

Global flags:
  --json                                 Output raw JSON
  --port PORT                            Override daemon port (default: 18932)
```

### Name resolution

Many commands accept `<id|name>`. The CLI resolves names client-side:

1. If the argument matches a cell ID exactly (8-char hex), use it directly.
2. Otherwise, fetch state from the daemon and find the first cell whose `name` matches (case-insensitive).
3. If multiple cells match, print them and ask the user to use the ID instead.
4. Group names are resolved the same way for group commands.

This resolution happens in a shared `resolve_cell(state, identifier)` helper.

### Output formatting

**`loom status`** (default, human-readable):

```
Backend (3 agents)
  ID        Name          Status    Activity         Branch
  a1b2c3d4  code-agent    running   Editing server   loom/code-agent-a1b2
  e5f6a7b8  test-agent    idle                       loom/test-agent-e5f6
  c9d0e1f2  review-agent  stopped

Frontend (1 agent)
  ID        Name          Status    Activity         Branch
  11223344  ui-agent      running   Running: npm t
```

**`loom status a1b2c3d4`** (single agent detail):

```
Agent: code-agent (a1b2c3d4)
  Group:      Backend
  Status:     running
  Activity:   Editing server.py
  Process:    claude
  Directory:  /Users/me/project
  Branch:     loom/code-agent-a1b2
  Worktree:   /Users/me/project/.loom/worktrees/code-agent-a1b2
  Diff:       +42 -13 (3 files)
  Tokens:     12,340 in / 8,210 out
  Children:
    f3e2d1c0  shell     idle
    a9b8c7d6  test-run  running
```

**`--json`** flag:

```json
{"ok": true, "data": {"type": "state", "agents": {...}, "groups": {...}}}
```

### Error handling

```
$ loom agent focus nonexistent
Error: No agent matching "nonexistent"

$ loom status
Error: Cannot connect to Loom daemon on localhost:18932
Is it running? Start with: make run (or Scripts menu → loom)
```

---

## Implementation Steps

### Step 1: Refactor `handle_command` to return results

**File**: `loom/server.py`

- Change `handle_command(data, ws)` signature to `handle_command(data)` → returns `dict | None`
- Replace `await ws.send_str(json.dumps({...}))` with `return {...}` for direct-response commands (get_config, get_group_settings, worktree_history)
- For mutation commands that create cells (add_agent, add_terminal), return `{"created": {"id": cell.id, "name": cell.name, ...}}`
- Update the WS handler loop to send the returned result if not None
- Existing behavior is preserved: broadcast still happens at the end of mutations

### Step 2: Add `POST /api/cmd` endpoint

**File**: `loom/server.py`

- Add `handle_api_cmd` async handler
- Register route: `app_server.router.add_post("/api/cmd", handle_api_cmd)`
- Wrap response in `{"ok": true/false, "data": ..., "error": ...}`
- For mutations without a specific return value, include the full state snapshot in `data` so the CLI can display updated status

### Step 3: Write the CLI script

**File**: `bin/loom` (executable, `#!/usr/bin/env python3`)

Structure:

```
bin/loom
├── argparse setup (subparsers for each noun/verb)
├── api_call(cmd, **kwargs) → dict  (urllib POST to /api/cmd)
├── resolve_cell(state, identifier) → cell dict
├── resolve_group(state, identifier) → group name
├── format_status(data) → str  (human-readable table)
├── format_agent_detail(cell) → str
├── main() → parse args, dispatch
```

No classes needed. Flat functions, ~250-300 lines.

### Step 4: Add `loom logs` command

This is the only command that doesn't hit the API — it tails the log file directly.

```python
def cmd_logs(args):
    log_path = os.path.expanduser(
        "~/Library/Application Support/iTerm2/Scripts/loom/loom/loom.log")
    if args.follow:
        os.execvp("tail", ["tail", "-f", log_path])
    else:
        os.execvp("tail", ["tail", "-50", log_path])
```

### Step 5: Makefile target

```makefile
## cli: Install the loom CLI to /usr/local/bin
cli:
	@chmod +x bin/loom
	@ln -sf "$(CURDIR)/bin/loom" /usr/local/bin/loom
	@echo "Installed: loom → $(CURDIR)/bin/loom"
```

Also update `make install` to copy the CLI alongside the daemon files.

### Step 6: `loom task` shortcut (stretch)

A convenience command that combines multiple operations:

```
loom task "Fix the login bug" -g Backend
```

Equivalent to:
1. `loom agent add "Fix the login bug" -g Backend`
2. If group has worktrees enabled, waits for worktree creation
3. `loom send <id> "Fix the login bug"`

This is a CLI-only composite — the server doesn't need a new command.

---

## What We're NOT Building (Yet)

- **WebSocket streaming in the CLI** — `loom watch` or `loom status --follow` would need a WS client. Defer to a later iteration. For now, `watch loom status` achieves the same thing.
- **Remote server mode** — The REST endpoint only binds to `127.0.0.1`. Network exposure, auth, and TLS are Phase 3 follow-ups per the roadmap.
- **MCP server bridge** — Separate project that wraps the same REST API. Roadmap Phase 3 follow-up.
- **Shell completions** — Nice to have, but not in v1. The command set is small enough to learn.
- **Config file** — `--port` flag is sufficient for now. A `~/.loomrc` is premature.

---

## File Changes Summary

| File | Change |
|---|---|
| `loom/server.py` | Refactor `handle_command` return values, add `/api/cmd` route |
| `bin/loom` | New file — CLI script (~300 lines) |
| `Makefile` | Add `cli` target |
| `docs/roadmap.md` | Update Phase 3 status |
