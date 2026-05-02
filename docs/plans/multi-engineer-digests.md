# Multi-Engineer Event Digest Fan-Out — Implementation Plan

## Motivation

Today's event digest system was built when there was exactly one "engineer" per group. `EventBuffer` in `torque/engineer.py` buffers panel events per group, then periodically injects a formatted digest into a single recipient — `group_settings.engineer_agent_id`. See `_flush` in `torque/engineer.py:865` and the timer loop in `_timer_tick` at line 1295.

The Agent Kinds refactor ([agent-kinds-refactor.md](agent-kinds-refactor.md)) replaced the single-engineer model with **multiple persistent engineers per group** plus **architects**, but the digest delivery path was never fanned out. Concretely, in any group that now has N engineers:

- Only the one engineer whose id matches `group_settings.engineer_agent_id` receives digests.
- The other N-1 engineers are deaf to Torque-managed event signals — they must poll `torque ai context` or the board to discover their workers' state.
- Architects receive no digests at all. There is no code path that targets `kind == "architect"` for digest delivery.
- The single group-keyed buffer (`state.engineer_sent_events[group]`, `state.engineer_buffer_stats[group]`, `state.engineer_settings[group]`) cannot represent per-engineer state, pause toggles, or verbosity preferences.

This plan fans the digest system out along the agent-kinds axis: **every engineer receives a digest scoped to its own workers**, and **architects receive a coarser, opt-in digest** of engineer-level state transitions. The legacy single-engineer path goes away.

## Design principles

1. **One recipient, one scope.** Every digest targets exactly one agent and contains only events that agent is allowed to see. Engineers see their owned workers + their own events; architects see hired-engineers' state transitions. Workers never receive digests (they are event sources, not sinks).
2. **Fan-out is additive, not cross-cutting.** An event may enqueue into multiple recipients' buffers (e.g. a worker finishes → both the owning engineer *and* the hiring architect see it, at their respective verbosity levels). Buffers are per-recipient, not per-group.
3. **Per-recipient settings.** Pause, interval, verbosity, and enabled-event filters move from `EngineerSettings` (keyed by group) to `AgentDigestSettings` (keyed by `agent_id`). Engineers and architects have independent knobs.
4. **Delivery reuses `inject_mcp_message`.** The fix we just shipped (`torque/server.py` `inject_mcp_message` command, `bridge.send_text` + session-resume behavior) is the right transport. No need to maintain a second bespoke injection path in `engineer.py`.
5. **Architect digests are coarse.** Raw worker tool calls would drown the architect. Architect digests contain only engineer-level transitions: `done`, `blocked`, `error`, `ask`, `derive`, pipeline completion, hire status. Worker-level events are summarized into counts, not enumerated.
6. **Migration is automatic.** On first boot after the upgrade, the existing `engineer_agent_id` per group becomes the first engineer digest subscriber; its `EngineerSettings` become that engineer's `AgentDigestSettings`. No user action required.
7. **No ghost engineers.** After migration, the `engineer_agent_id` field is retired and all `engineer_*` state keys are renamed or repurposed. This was flagged as a phase 2 follow-up in [agent-panel.md](agent-panel.md); this plan absorbs that work.

## Data model changes

### New dataclass: `AgentDigestSettings`

Replaces `EngineerSettings`. Keyed by `agent_id` rather than group.

```python
@dataclass
class AgentDigestSettings:
    agent_id: str
    paused: bool = False
    push_interval: int = 60         # seconds between digest pushes
    max_interval: int = 300         # max between quiet-time heartbeats
    heartbeat_interval: int = 300   # 0 = off
    digest_verbosity: str = "balanced"  # compact | balanced | detailed
    enabled_events: set[str] = field(default_factory=set)
    # Architect-only: filter to "engineer-level" transitions
    architect_digest: bool = False  # True if recipient is an architect
```

`ENGINEER_MANDATORY_EVENTS` is renamed `DIGEST_MANDATORY_EVENTS` and remains kind-agnostic.

### SQLite schema

- New table `agent_digest_settings` with primary key `agent_id`, columns mirroring the dataclass. Replaces `engineer_settings` (which is keyed by `group_name`).
- Migration in `TorqueDB.init()`:
  1. Detect old `engineer_settings` table.
  2. For each row, look up `group_settings.engineer_agent_id` for that group. If present, insert into `agent_digest_settings` with `agent_id = <engineer_agent_id>` and `architect_digest = False`.
  3. Leave `engineer_settings` in place for one release for rollback safety; drop it in the release after.
- `group_settings.engineer_agent_id` column is retained in the schema for one release but stops being written. Reads fall through to "find any engineer subscribed to this group's digests" for back-compat during the transition window.

### In-memory state

`MatrixState` replaces `engineer_settings`, `engineer_worklog`, `engineer_sent_events`, `engineer_buffer_stats`, and `engineer_journal` with agent-keyed equivalents:

| Before (group-keyed) | After (agent-keyed) |
|---|---|
| `engineer_settings[group]` | `agent_digest_settings[agent_id]` |
| `engineer_sent_events[group]` | `digest_sent_events[agent_id]` |
| `engineer_buffer_stats[group]` | `digest_buffer_stats[agent_id]` |
| `engineer_worklog[group]` | `engineer_worklog[agent_id]` (engineers only) |
| `engineer_journal[group]` | `agent_journal[agent_id]` (engineers + architects) |

All are populated from per-agent SQLite tables on boot. Delta broadcast op names: `agent_digest_update`, `digest_sent_push`, `digest_buffer_stats`, `engineer_worklog_update`, `agent_journal_update`.

## Event routing

### The recipient-resolver

New pure function `resolve_digest_recipients(state, event) -> list[AgentCell]`:

1. Determine the event's originating cell (`event["cell_id"]` → `AgentCell`).
2. Walk the ownership chain:
   - If the cell is a **worker**: recipient list = `[owner_engineer]` if set + architect-recipients(owner_engineer) if the event is coarse enough.
   - If the cell is an **engineer**: recipient list = `[self]` + `[hiring_architect]` if set and the event is coarse enough.
   - If the cell is an **architect**: recipient list = `[]`. Architects do not feed their own digests.
3. Filter each recipient by their `AgentDigestSettings.paused` and `enabled_events`.
4. For architect recipients, additionally require the event's `kind` to be in the **architect-coarse set**: `task_done`, `task_blocked`, `task_error`, `task_ask`, `task_derive`, `pipeline_complete`, `engineer_hired`, `engineer_fired`.

### Rewriting `on_panel_event`

`on_panel_event` in `torque/engineer.py:708` becomes:

```python
def on_panel_event(self, event: dict):
    recipients = resolve_digest_recipients(self._state, event)
    for recipient in recipients:
        settings = self._state.get_digest_settings(recipient.id)
        if event["kind"] not in DIGEST_MANDATORY_EVENTS \
                and event["kind"] not in settings.enabled_events:
            continue
        buf = self._buffers.setdefault(recipient.id, [])
        if not buf:
            self._buffer_started[recipient.id] = time.time()
        buf.append(event)
        self._emit_buffer_stats(recipient.id)
        if not recipient.activity or recipient.activity == "waiting":
            if not settings.paused:
                self._check_agent_flush(recipient)
```

Buffers and state are now per `agent_id`, not per `group`.

### Rewriting `_flush`

`_flush(group)` → `_flush(agent_id)`. Reads target agent via `state.agents[agent_id]`. Uses `inject_mcp_message` (new) via the daemon's command dispatch rather than calling `bridge.send_text` directly — this gives us the session-resume path for free when an engineer's Claude session has exited.

### Architect digest format

When the recipient is an architect, `_format_digest` branches to a compact format that groups events by engineer:

```
## Architect digest — <timestamp>

### Panelsmith (engineer)
- 3 worker events rolled up (done × 1, progress × 2)
- Worker `4048538e` finished TORQUE:61

### Engineer (engineer)
- Idle

### Pipeline activity
- agent-panel-refactor: stage 1/5 Done
```

Contrast with the engineer format which enumerates each worker event at the configured verbosity. The architect format caps at ~40 lines regardless of activity volume.

## Delivery

### Reuse `inject_mcp_message`

`EventBuffer._flush` stops calling `bridge.send_text` directly. Instead it builds the digest text and issues a daemon command:

```python
await handle_command({
    "cmd": "inject_mcp_message",
    "agent_id": recipient.id,
    "message": digest_text,
    "sender_name": "Torque",
    "sender_kind": "system",
    "message_id": f"digest-{recipient.id}-{int(time.time())}",
})
```

The `inject_mcp_message` command is kind-aware for the reply-tool hint (already implemented). For digests, the "reply" affordance is meaningless — we extend the formatter to optionally omit the "Reply with: …" footer when `sender_kind == "system"`.

### Session resume on dead engineers

CLAUDE.md already says engineers are persistent but their Claude *processes* can exit. Currently `_flush` bails out when `engineer.session_id` is empty. With the inject pathway, "session ended" → `inject_mcp_message` detects no-session and can either (a) drop the digest (current behavior), or (b) relaunch the engineer before injecting (new).

**Default: relaunch-on-digest is off.** Digests are information, not interruptions. Engineers who've gone quiet have gone quiet deliberately. An architect or user must re-activate them explicitly. Sending an unsolicited digest would boot Claude back into foreground work without the user's intent.

A per-agent setting `wake_on_digest: bool` on `AgentDigestSettings` can flip this per engineer. Default `False`.

## Settings surface (UI)

The Agent Panel ([agent-panel.md](agent-panel.md), Phase 2) already plans for per-agent tabs. The digest settings live there, not in Group Settings:

- **Engineer panel** → new sub-tab "Digest" (or integrate into Journal/Events tab header). Controls: pause, push interval, heartbeat, verbosity, enabled events, wake_on_digest.
- **Architect panel** → same controls, plus the coarse-set toggle (no worker-level events, only engineer transitions).

Group Settings → Engineer tab is removed. The Group Settings panel now delegates digest configuration to the per-agent inspector.

## CLI

New subcommands:

```
torque digest list [--group GROUP]
    Show digest subscription state per agent.

torque digest pause AGENT
torque digest resume AGENT
    Toggle delivery for one agent.

torque digest send AGENT
    Force a flush for one agent (replaces `engineer_buffer.request_manual_flush`).

torque digest settings AGENT [--interval N] [--verbosity V] [--events LIST]
    Inline settings update.
```

The old `torque engineer *` subcommands (if any) are aliased to `torque digest *` for one release, then removed.

## Migration path

Automatic, idempotent, runs once on first daemon boot after upgrade.

**Step 1: schema.** `TorqueDB.init()` creates `agent_digest_settings`. Old `engineer_settings` remains intact.

**Step 2: data.** For every row in `engineer_settings`:
- Look up `group_settings.engineer_agent_id` for that `group_name`.
- If the referenced agent exists and has `kind == "engineer"`, copy the settings into `agent_digest_settings` with `agent_id = <that engineer's id>`.
- If the referenced agent does not exist (stale row), skip with a log warning.

**Step 3: worklog / journal.** For every entry in `engineer_worklog[group]`, look up the group's engineer agent and re-key to `engineer_worklog[agent_id]`. Same for `engineer_journal`. The legacy tables are preserved for one release for rollback.

**Step 4: runtime.** On first boot post-migration, `EventBuffer` uses the new per-agent buffers. Any event the buffer receives is routed via `resolve_digest_recipients`.

**Step 5: subscribe other engineers.** Engineers in the group who are NOT the legacy engineer have no `AgentDigestSettings` row yet. On first event involving their workers, a default-settings row is created and emitted as a `agent_digest_update` delta. This gives you automatic digest coverage for new engineers without a manual migration step.

**Rollback plan.** If the new system misbehaves in production, revert the daemon, and the old `engineer_settings` + `group_settings.engineer_agent_id` are still intact. Data written to `agent_digest_settings` during the new daemon's uptime is lost (acceptable — it's settings, not event history).

## Phased delivery

**Phase 1 — foundation (this plan's primary scope).**
1. New `AgentDigestSettings` dataclass + SQLite table + load/save.
2. `resolve_digest_recipients` + rewritten `on_panel_event`.
3. Per-agent buffers; per-agent `_flush`.
4. `inject_mcp_message` integration for delivery.
5. Migration from `engineer_settings` → `agent_digest_settings`.
6. Tests (unit): recipient resolver covers all kind combinations; architect-coarse filter; per-recipient pause.

**Phase 2 — architect digests.**
1. Architect-coarse event set.
2. Architect digest format.
3. Architect-panel Digest tab in UI.

**Phase 3 — settings UI + CLI.**
1. Per-agent Digest sub-tab in Agent Panel.
2. `torque digest *` subcommands.
3. Remove Group Settings → Engineer tab.

**Phase 4 — cleanup.**
1. Drop `engineer_settings` SQLite table.
2. Remove `group_settings.engineer_agent_id` column.
3. Rename `state.engineer_worklog` / `engineer_journal` / friends (already phase 2 of agent-panel plan; this absorbs it).
4. Remove any remaining `engineer_*` MCP command names (already gone per Agent Kinds invariants, double-check).

## Dependencies

- Depends on the **agent-panel refactor** ([agent-panel.md](agent-panel.md)) landing first, so phase 3's UI surface exists. Phases 1 and 2 can ship before the UI; settings can be edited via CLI in the meantime.
- Depends on `inject_mcp_message` (already shipped in the architect-engineer messaging fix).
- Indirectly touches the Engineer MCP tool surface (`mcp_engineer.py`) — a new `engineer_digest_settings` tool replaces the old `engineer_launch_settings` semantics for digest-specific knobs. Backward-compat alias for one release.

## Testing

Manual (no automated tests per CLAUDE.md, though new unit tests for `resolve_digest_recipients` fit cleanly in `tests/`):

1. Group with two engineers + one architect + workers under each engineer. Trigger events on each worker.
   - Each engineer receives digest enumerating only its own workers' events.
   - Architect receives a rolled-up digest with per-engineer counts.
2. Pause engineer A. Trigger events on A's workers. Confirm A's buffer stats update but no digest is injected. Resume → digest flushes.
3. Configure architect `wake_on_digest = False`. Engineer session exits. Trigger an engineer-level event. Confirm architect digest is dropped (not a relaunch).
4. Migration: daemon upgrade from a running system with populated `engineer_settings`. Confirm post-boot: (a) legacy engineer still receives digests, (b) new engineer rows are auto-created on first event, (c) no duplicate delivery.
5. Rollback: revert daemon. Confirm legacy engineer still works.

## Open questions

- **Coalescing deliveries.** If an event fans out to both an engineer and its architect, and both recipients pass their filter, do we inject twice (simple, maybe noisy) or debounce (complex, single source of truth)? Default simple; revisit if noise is real.
- **Architect digest timing.** Should architects get digests on the same cadence as engineers, or on a longer interval (e.g. 5 min) since they consume coarser info? Lean toward longer default (`push_interval=300`) but configurable.
- **Per-worker event gating.** Should workers have any digest-settings of their own (e.g. "notify my owning engineer on every tool call")? Probably not — engineers own the filter.
- **Global digest off switch.** A user might want a maintenance-mode flag that pauses ALL digest delivery across a group or the whole daemon. Worth adding; trivial (`global_settings.digests_paused`). Flag in phase 3 with the CLI.
- **Resume behavior after long pause.** If an engineer is paused for an hour, the buffer accumulates 200+ events. On resume, do we flush the whole backlog, a summary, or only the most recent? Existing behavior is "flush everything." Consider truncating to a max-event digest with a "… N events elided" footer. Defer unless real users hit it.
