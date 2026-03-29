# Implementation Plan: Agent Awareness

**Roadmap phase**: 1 — Agent Awareness
**Status**: Implemented
**Goal**: Give Loom real-time visibility into what its managed agents are doing, using a provider-agnostic architecture that works across Claude Code, Codex, Gemini CLI, and future agents.

---

## The Problem

Loom currently knows three things about an agent: whether its terminal session exists, whether the shell prompt has fired (via PromptMonitor), and what the foreground process name is (via jobName). It has no idea what the agent is *thinking*, *doing*, or *producing*. A cell that says "running" tells you nothing about whether the agent is reading files, writing code, stuck in a retry loop, or waiting for input.

## Design Principles

1. **Provider-agnostic core** — Loom's internal event model and UI are decoupled from any specific agent. Agent-specific translation happens in adapters.
2. **Graceful degradation** — Agents with rich hook systems (Claude Code, Gemini CLI) get deep integration. Agents with nothing (OpenCode) still get basic process-level monitoring. The UI adapts to what's available.
3. **Push over poll** — Agents push events to Loom via HTTP hooks or stdout parsing. Loom never polls agent internals.
4. **Non-blocking** — Hooks and adapters must never slow down the agent. All Loom-bound hooks are fire-and-forget (async, short timeouts, no blocking decisions).
5. **Opt-in** — Agent awareness is enabled per group or per agent. Users who just want tab management are unaffected.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  Agent Process                   │
│              (Claude Code, Codex, etc.)          │
└──────────┬──────────────────────┬───────────────┘
           │ HTTP POST /events    │ stdout (JSONL)
           │ (hooks)              │ (parsed by adapter)
           ▼                     ▼
┌─────────────────────────────────────────────────┐
│               Agent Adapter                      │
│         (one per agent type)                     │
│                                                  │
│  Translates agent-specific signals into          │
│  normalized AgentEvent objects                   │
└──────────────────────┬──────────────────────────┘
                       │ AgentEvent
                       ▼
┌─────────────────────────────────────────────────┐
│                 Event Bus                        │
│           (in-process, asyncio)                  │
│                                                  │
│  • Updates AgentCell state                       │
│  • Appends to event log                          │
│  • Triggers notifications                        │
│  • Broadcasts to WebSocket clients               │
└──────────┬──────────────┬───────────────────────┘
           │              │
           ▼              ▼
┌──────────────┐  ┌──────────────────┐
│  Toolbelt UI │  │ macOS Notifs     │
│  (WebSocket) │  │ (osascript)      │
└──────────────┘  └──────────────────┘
```

---

## What Was Built

### Data Model

**AgentEvent** — normalized event produced by all adapters:

```python
@dataclass
class AgentEvent:
    cell_id: str        # Which AgentCell this belongs to
    timestamp: float    # Unix timestamp
    event_type: str     # session_start, tool_start, error, waiting, etc.
    data: dict          # Event-type-specific payload
```

10 event types: `session_start`, `session_end`, `activity_change`, `tool_start`, `tool_end`, `message`, `error`, `waiting`, `progress`, `cost_update`.

**AgentCell extensions** (new fields):

| Field | Type | Persisted | Purpose |
|---|---|---|---|
| `agent_type` | str | Yes | `"claude-code"`, `"codex"`, `"gemini-cli"`, `""` |
| `agent_session_id` | str | Yes | Agent's own session ID (for resume on relaunch) |
| `activity` | str | No | `""`, `"thinking"`, `"tool_call"`, `"waiting"`, `"subagent"` |
| `activity_detail` | str | No | Human-readable: `"Editing server.py"` |
| `last_event_at` | float | No | Timestamp of last hook event |
| `session_tokens_in` | int | No | Cumulative input tokens |
| `session_tokens_out` | int | No | Cumulative output tokens |
| `error_message` | str | No | Last error message |
| `needs_attention` | bool | No | Agent stuck, errored, or needs permission |

**GroupSettings extensions** (in Agents tab):

| Field | Type | Default | Purpose |
|---|---|---|---|
| `agent_boot_command` | str | "" | Override default boot command (e.g. "codex") |
| `agent_session_resume` | bool | True | Resume session on relaunch (`claude --resume`) |
| `agent_idle_timeout` | int | 5 | Minutes before flagging as stuck (0 = disable) |
| `notifications` | bool | False | Master toggle for macOS notifications |
| `notify_on_finish` | bool | True | Notify when agent finishes |
| `notify_on_error` | bool | True | Notify on errors |
| `notify_on_attention` | bool | True | Notify when agent needs attention |

### Adapter Framework

Base class in `loom/adapters/base.py` with methods: `match_process()`, `match_command()`, `get_hook_config()`, `get_env_vars()`, `parse_event()`.

Registry in `loom/adapters/__init__.py` with `detect_agent_type()` (by process name), `detect_by_command()` (by boot command), `get_adapter()` (by name).

**Implemented adapters:**

| Adapter | File | Status | Integration |
|---|---|---|---|
| Claude Code | `claude_code.py` | Full | HTTP hooks (command hook for SessionStart), event parsing, activity inference, session resume |
| Codex | `codex.py` | Stub | Process matching only |
| Gemini CLI | `gemini_cli.py` | Stub | Process matching only |
| Generic | `generic.py` | Full | Fallback, process monitoring only |

### Claude Code Adapter

**Hook installation**: Loom writes hooks to `.claude/settings.local.json` in the agent's working directory using a merge strategy. Loom hooks are identified by their URL (`http://localhost:18932/events`) and can be cleanly added/removed without affecting user hooks. HTTP hooks include `allowedEnvVars: ["LOOM_CELL_ID"]` for header interpolation.

**Hooks subscribed**: `SessionStart` (command hook — HTTP not supported), `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `Notification`, `Stop`, `SubagentStart`, `SubagentStop`, `StopFailure` (all HTTP hooks).

**Event mapping**:

| Claude Code Hook | → AgentEvent | Notes |
|---|---|---|
| `SessionStart` | `session_start` | Captures `session_id` for resume support |
| `Stop` | `session_end` | Agent's turn completed |
| `PreToolUse` | `tool_start` | Activity detail from tool name + input |
| `PostToolUse` | `tool_end` (success) | |
| `PostToolUseFailure` | `tool_end` (failure) | |
| `Notification` (`permission_prompt`) | `waiting` | Sets `needs_attention = true` |
| `Notification` (`idle_prompt`) | `session_end` | Agent done, waiting for next prompt |
| `StopFailure` | `error` | Rate limits, auth failures, etc. |
| `SubagentStart` | `activity_change` | Activity = "subagent" |
| `SubagentStop` | `activity_change` | Clears subagent activity |

**Activity inference** (tool name → human-readable detail):
- `Bash` → "Running: {command preview}"
- `Edit` / `Write` → "Editing {filename}"
- `Read` → "Reading {filename}"
- `Grep` / `Glob` → "Searching codebase" / "Searching files"
- `Agent` → "Subagent: {description}"
- `WebFetch` / `WebSearch` → "Fetching web page" / "Searching web"

**Session resume**: When `SessionStart` fires, the adapter captures Claude Code's `session_id` and stores it in `cell.agent_session_id` (persisted to `state.json` immediately). On relaunch, Loom runs `claude --resume <session_id>` instead of plain `claude`, continuing the conversation where it left off. Controlled by the `agent_session_resume` group setting (default: on). A `/clear` in Claude Code generates a new session ID, so resume after clear starts from a blank conversation.

### Event Bus and Throttling

`EventBus` in `loom/events.py`:
- Receives events via `emit()`, updates cell fields, appends to `EventLog`, notifies `NotificationManager`
- Trailing-edge broadcast throttle: at most one WebSocket broadcast per second globally
- Activity-resuming events (`tool_start`, `tool_end`, `activity_change`, `message`) clear `needs_attention` — agents recover automatically from stuck/error state

`EventLog`: Per-cell ring buffer (200 events max), in-memory only.

### HTTP Endpoint

`POST /events` on the existing aiohttp server (port 18932). Correlation: `X-Loom-Cell-Id` header (primary) → `cwd` directory match (fallback). Always returns `200 {}`.

### Auto-Detection

Two detection points:
1. **On create_session**: Boot command is matched against adapters (e.g., `claude` → `claude-code`)
2. **On jobName change**: Process name is matched for terminal cells

`agent_type` is persisted — survives restarts.

### Reconnection

On daemon restart, `reconnect_orphans`:
- Sets status to `"running"` (not `"idle"`) for cells with a boot command
- Re-installs hooks for cells with `agent_type`
- `LOOM_CELL_ID` env var persists in the terminal session, so hook correlation continues working

### Health Monitoring

`health_check` coroutine runs every 30 seconds:
- Flags agents with no events for 5+ minutes as `needs_attention`
- Flags agents with 3+ errors in 5 minutes
- Sends notifications via `NotificationManager`

### macOS Notifications

`NotificationManager` in `loom/notifications.py`:
- 5-second batching window per group
- Combined messages: "2 agents finished, 1 needs attention in group 'Backend'"
- Sent via `osascript` (`display notification`)
- Opt-in per group via `GroupSettings.notifications`

### UI Changes

**Agent cell** (`render.js`):
- Three-state status dot: gray (idle), green pulsing (working), red (disconnected)
- Attention state: status dot becomes larger amber `!` when `needs_attention` is true
- Activity detail line below name: "Editing server.py", "Running: npm test"
- Agent type label: "CC", "CX", "GM" in bottom-left corner
- For awareness agents, `activity` field is the source of truth for working vs idle (not `status`, since PromptMonitor doesn't fire for TUI apps)

**Group header**: Agent count badge only (no running/attention counts — the cells themselves communicate status).

**Group settings modal** (`webview.html`, `modals.js`): Agents tab includes boot command, session resume toggle, idle timeout, and notification settings (4 checkboxes).

Agents without awareness (`agent_type` empty) render exactly as before — fully backward compatible.

---

## File Structure

New files:

```
loom/
  adapters/
    __init__.py          # Registry: detect_agent_type(), detect_by_command(), get_adapter()
    base.py              # AgentAdapter base class, AgentEvent dataclass, EVENT_TYPES
    claude_code.py       # Full: hooks, event parsing, activity inference, install/uninstall
    codex.py             # Stub: process/command matching only
    gemini_cli.py        # Stub: process/command matching only
    generic.py           # Fallback: always matches, process monitoring only
  events.py              # EventBus (throttled broadcast), EventLog (ring buffer), health_check
  notifications.py       # NotificationManager (5s batching, osascript)
```

Modified files:

```
loom/state.py            # AgentCell +8 fields, GroupSettings +4 fields, _EPHEMERAL_FIELDS, cells_with_awareness()
loom/server.py           # POST /events route, event bus + notifier wiring, hook cleanup on remove
loom/bridge.py           # Auto-detection, LOOM_CELL_ID injection, hook install, session resume, reconnect fixes
static/js/constants.js   # AGENT_TYPE_LABELS
static/js/render.js      # agentStatusClass() (gray/green/red), activity detail, type label
static/js/modals.js      # Agent settings (boot cmd, resume, idle timeout, notifications) in group settings modal
static/style.css         # Three-state status dot, attention indicator, type label
webview.html             # Agent settings and notification checkboxes in Agents tab of group settings
Makefile                 # Copy adapters/, events.py, notifications.py
```

---

## Decisions

Resolved during implementation:

1. **Hook config scope** — Loom writes to `.claude/settings.local.json` using a merge strategy. Loom hooks are identified by URL (`localhost:18932/events`) and cleanly added/removed without affecting user hooks. `allowedEnvVars` is required for header interpolation.

2. **Multi-window correlation** — `LOOM_CELL_ID` is the mandatory primary key, injected as env var and interpolated into HTTP hook headers. `cwd` matching is a debug fallback.

3. **Adapter scope** — Claude Code is fully implemented. Gemini CLI and Codex are stubs (process matching only). Generic is the fallback.

4. **Event volume / UI throttling** — Events update internal state immediately. WebSocket broadcasts are throttled to at most once per second (trailing-edge global timer).

5. **Notification batching** — 5-second window, grouped by group name. Combined messages for multiple agents.

6. **Endpoint security** — No authentication. Localhost-only. Will be secured in Phase 3 (remote server mode).

7. **Notification delivery** — Uses `osascript` (macOS `display notification`) rather than iTerm2's Python API, which doesn't expose a notification method.

8. **Session resume** — Claude Code's `session_id` is captured from `SessionStart` hooks (command type, not HTTP) and persisted to `state.json`. Relaunch uses `claude --resume <id>`. Controlled by `agent_session_resume` group setting (default: on).

9. **Idle vs. waiting** — `idle_prompt` (agent finished, waiting for next task) maps to `session_end`, not `waiting`. Only `permission_prompt` (agent blocked, needs approval) sets `needs_attention`.

10. **SessionStart hook type** — Claude Code only supports `type: "command"` for `SessionStart` events. HTTP hooks are silently ignored. Uses `curl` to POST to Loom instead.

11. **Status dot simplification** — Three states: gray (idle), green (working), red (disconnected). For awareness agents, `activity` field is the source of truth — `status` field stays "running" while a TUI app is active (PromptMonitor limitation). Awareness agents start as idle on boot; hooks flip to working.

12. **Agent settings in Agents tab** — Boot command, session resume, idle timeout, and notifications are all in the Agents tab of group settings (not the Group tab).

---

## Future Work

- **Session recording** — Persist the EventLog to disk for replay and auditing (Phase 1 item, deferred).
- **Gemini CLI adapter** — Fill in hook config generation and event parsing.
- **Codex adapter** — Investigate `CODEX_HOME` per-agent scoping, implement hook config.
- **Token cost display** — Show `session_tokens_in/out` in the UI (data is collected but not rendered yet).
