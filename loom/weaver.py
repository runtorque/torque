"""Weaver event buffer, digest delivery, and system prompt builder.

The weaver is a per-group orchestrator agent that receives event digests
when idle.  This module manages buffering, delivery timing, and the
system prompt assembly for ``--append-system-prompt-file``.
"""

import asyncio
import logging
import time

from .state import WEAVER_MANDATORY_EVENTS

log = logging.getLogger("loom")

# ---------------------------------------------------------------------------
# Base system prompt (the weaver's "firmware")
# ---------------------------------------------------------------------------

_BASE_SYSTEM_PROMPT = """\
You are the Weaver — the orchestrator agent for the "{group}" group in Loom.

Your role is to manage the task board, dispatch work to agents, react to
events, and maintain a persistent decision journal so you can recover
context after a /clear.

## Available tools

You have access to weaver_* MCP tools:

**Read**: weaver_board_list, weaver_task_show, weaver_agents_list, \
weaver_agent_show, weaver_actions_list, weaver_action_show
**Write**: weaver_task_create, weaver_task_edit, weaver_task_move, \
weaver_task_dispatch, weaver_task_resolve
**Events**: weaver_events, weaver_notifications, weaver_resume
**Journal**: weaver_journal, weaver_journal_read
**Interaction**: weaver_agent_message, weaver_ask, weaver_agent_close, \
weaver_agent_relaunch
**Worktree**: weaver_merge, weaver_create_pr, weaver_diff, \
weaver_worktree_remove, weaver_worktree_checkpoint

## Operating guidelines

1. **Journal discipline** — Write a journal entry for every significant
   decision (dispatch, priority change, resolution).  Write a checkpoint
   entry periodically (every ~10 decisions or when the board state changes
   significantly).  Checkpoints should summarise the board, active agents,
   and your planned next steps.

2. **Event response** — When you receive a Loom Digest, process each event:
   - task_completed → decide the next step (dispatch follow-up, close out, etc.)
   - agent_error / agent_blocked → investigate and help or escalate
   - agent_reply → incorporate the information and continue
   - ask_created → review and resolve or escalate to the human

3. **Context recovery** — After a /clear or restart, your first actions
   should be: weaver_journal_read → weaver_board_list → weaver_events.
   Reconstruct your plan from the journal, then resume.

4. **Concurrency awareness** — Check how many agents are currently active
   before dispatching new work.  Don't overload the system.

5. **Idle waiting** — When there's nothing to dispatch and you're waiting
   for agents to finish, do nothing.  Loom will push event digests to you
   when something happens.

6. **Human interaction** — Use `weaver_ask` to ask the human questions.
   This pauses event delivery and shows your question in the Weaver panel.
   The human will reply via the panel or directly in your terminal.
   After receiving their answer, call `weaver_resume` to unpause events.

7. **First session** — When starting a new session (no journal history),
   call `weaver_ask` to introduce yourself and ask the human what to
   focus on.  Don't start dispatching tasks without human guidance.
"""


def build_weaver_system_prompt(group: str, weaver_settings=None,
                               action_system_prompt: str = "") -> str:
    """Assemble the full system prompt for a weaver agent.

    Concatenates: base identity → action system_prompt → custom instructions.
    """
    parts = [_BASE_SYSTEM_PROMPT.format(group=group)]

    if action_system_prompt:
        parts.append(action_system_prompt.rstrip())

    if weaver_settings and weaver_settings.custom_instructions:
        ci = weaver_settings.custom_instructions.strip()
        parts.append(
            "── Custom Instructions "
            "────────────────────────\n"
            f"{ci}\n"
            "────────────────────────────────────────────────"
        )

    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Event buffer and digest delivery
# ---------------------------------------------------------------------------

class WeaverEventBuffer:
    """Per-group event buffering with idle-gated digest delivery.

    Events are buffered per group.  When the weaver goes idle (activity
    becomes empty) and events are pending, the buffer flushes a formatted
    digest to the weaver's terminal.  A periodic timer ensures heartbeat
    delivery even when nothing critical happens.
    """

    def __init__(self, state, bridge):
        self._state = state
        self._bridge = bridge          # ITerm2Adapter — for send_text()
        self._buffers: dict[str, list[dict]] = {}   # group → buffered events
        self._last_push: dict[str, float] = {}       # group → timestamp
        self._pending_flush: dict[str, bool] = {}    # group → True if due
        self._was_idle_with_question: set[str] = set()  # groups where weaver went idle with pending_question
        self._timer_handle: asyncio.TimerHandle | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self):
        """Capture the event loop and start the periodic check timer."""
        self._loop = asyncio.get_running_loop()
        self._schedule_timer()

    def stop(self):
        if self._timer_handle:
            self._timer_handle.cancel()
            self._timer_handle = None

    def get_buffer_stats(self, group: str) -> dict:
        """Return buffer stats for the UI: event count + seconds until next push."""
        buf = self._buffers.get(group, [])
        ws = self._state.get_weaver_settings(group)
        last = self._last_push.get(group, 0)
        now = time.time()

        if not last:
            next_in = ws.push_interval
        else:
            elapsed = now - last
            next_in = max(0, ws.push_interval - elapsed)

        # If the weaver is idle and events are buffered, next push is imminent
        weaver = self._state.get_weaver_for_group(group)
        if weaver and (not weaver.activity or weaver.activity == "waiting"):
            if buf:
                next_in = 0

        return {
            "buffered_events": len(buf),
            "next_push_in": int(next_in),
        }

    # -- Public hooks ---------------------------------------------------------

    def on_panel_event(self, event: dict):
        """Called when a panel event is emitted.  Buffer for matching weavers."""
        group = event.get("group", "")
        if not group:
            return

        # Is there a weaver for this group?
        weaver = self._state.get_weaver_for_group(group)
        if not weaver:
            return

        ws = self._state.get_weaver_settings(group)
        if ws.paused:
            return

        kind = event.get("kind", "")
        # Check if this event type should be buffered
        if kind not in WEAVER_MANDATORY_EVENTS:
            if kind not in ws.enabled_events:
                return

        buf = self._buffers.setdefault(group, [])
        buf.append(event)

    def on_agent_activity_change(self, cell):
        """Called when an agent's activity changes.  Flush if weaver goes idle."""
        gs = self._state.group_settings.get(cell.group)
        is_weaver = gs and gs.weaver_agent_id == cell.id

        if is_weaver:
            group = cell.group
            # Track when the weaver goes idle while a question is pending.
            # Only auto-clear pending_question when the weaver becomes
            # active AFTER having been idle — this prevents clearing the
            # question during the same tool-call turn that set it.
            if not cell.activity or cell.activity == "waiting":
                ws = self._state.get_weaver_settings(group)
                if ws.pending_question:
                    self._was_idle_with_question.add(group)
            elif cell.activity and cell.activity not in ("", "waiting"):
                if group in self._was_idle_with_question:
                    self._was_idle_with_question.discard(group)
                    ws = self._state.get_weaver_settings(group)
                    if ws.pending_question:
                        self._state.update_weaver_settings(
                            group, pending_question="")

        if not cell.activity or cell.activity == "waiting":
            # Agent went idle — check if it's a weaver with pending events
            self._check_weaver_flush(cell)

    # -- Internal -------------------------------------------------------------

    def _check_weaver_flush(self, cell):
        """If *cell* is a weaver with buffered events (or overdue), flush."""
        gs = self._state.group_settings.get(cell.group)
        if not gs or gs.weaver_agent_id != cell.id:
            return  # not a weaver

        group = cell.group
        ws = self._state.get_weaver_settings(group)
        if ws.paused:
            return

        has_events = bool(self._buffers.get(group))
        is_overdue = self._is_heartbeat_due(group, ws)

        if has_events or is_overdue:
            self._pending_flush[group] = True
            if self._loop:
                self._loop.create_task(self._flush(group))

    def _is_heartbeat_due(self, group: str, ws) -> bool:
        """Check if max_interval has elapsed since last push."""
        last = self._last_push.get(group, 0)
        if last == 0:
            return False  # never pushed — wait for first event
        return (time.time() - last) >= ws.max_interval

    async def _flush(self, group: str):
        """Format and send buffered events as a digest to the weaver."""
        self._pending_flush.pop(group, None)

        weaver = self._state.get_weaver_for_group(group)
        if not weaver or not weaver.session_id:
            return

        ws = self._state.get_weaver_settings(group)
        if ws.paused:
            return

        # Check weaver is actually idle
        if weaver.activity and weaver.activity != "waiting":
            return

        events = self._buffers.pop(group, [])
        board_summary = self._board_summary(group)

        if events:
            text = self._format_digest(events, board_summary, weaver)
        else:
            text = self._format_heartbeat(board_summary)

        self._last_push[group] = time.time()

        try:
            await self._bridge.send_text(weaver.session_id, text + "\n")
            log.info("Weaver digest sent to '%s' (%d events)",
                     weaver.name, len(events))
        except Exception:
            log.exception("Failed to send weaver digest to '%s'", weaver.name)

    def _format_digest(self, events: list[dict], board_summary: str,
                       weaver=None) -> str:
        lines = [f"── Loom Digest ({len(events)} event"
                 f"{'s' if len(events) != 1 else ''}) "
                 f"──────────────────────────"]
        for evt in events:
            kind = evt.get("kind", "")
            agent = evt.get("agent_name", "")
            msg = evt.get("message", "")
            if agent and msg:
                lines.append(f"  {kind}: {agent} — {msg}")
            elif msg:
                lines.append(f"  {kind}: {msg}")
            else:
                lines.append(f"  {kind}: {agent}")

        lines.append("")
        lines.append(f"Board: {board_summary}")

        # Context warning
        if weaver:
            ctx_warn = self._context_warning(weaver)
            if ctx_warn:
                lines.append(ctx_warn)

        lines.append("────────────────────────────────────────────────")
        return "\n".join(lines)

    def _format_heartbeat(self, board_summary: str) -> str:
        lines = [
            "── Loom Heartbeat ─────────────────────────────",
            "No new events since last digest.",
            "",
            f"Board: {board_summary}",
        ]

        # Include active agents
        actives = []
        for c in self._state.agents.values():
            if c.cell_type == "agent" and c.activity:
                actives.append(f"{c.slug or c.name} ({c.activity})")
        if actives:
            lines.append(f"Active: {' · '.join(actives)}")

        lines.append("────────────────────────────────────────────────")
        return "\n".join(lines)

    def _board_summary(self, group: str) -> str:
        """Count tasks per lane for a group."""
        counts: dict[str, int] = {}
        for t in self._state.board_tasks.values():
            if t.group == group:
                counts[t.lane] = counts.get(t.lane, 0) + 1
        parts = [f"{count} {lane}" for lane, count in counts.items()]
        return " · ".join(parts) if parts else "empty"

    def _context_warning(self, weaver) -> str:
        """Return a warning string if weaver context is getting large."""
        total = weaver.session_tokens_in + weaver.session_tokens_out
        # Rough threshold: 800K tokens (~80% of 1M context)
        if total > 800_000:
            pct = min(99, int(total / 10_000))  # rough percentage of 1M
            return (f"!! Context usage: ~{pct}% (~{total // 1000}K tokens). "
                    f"Consider writing a checkpoint.")
        return ""

    # -- Timer ----------------------------------------------------------------

    def _schedule_timer(self):
        """Schedule a periodic check every 10 seconds."""
        if self._loop and not self._loop.is_closed():
            self._timer_handle = self._loop.call_later(
                10.0, self._timer_tick)

    def _timer_tick(self):
        """Check if any weaver needs a heartbeat and emit buffer stats."""
        self._timer_handle = None
        now = time.time()
        stats_changed = False

        for group, gs_obj in self._state.group_settings.items():
            if not gs_obj.weaver_agent_id:
                continue
            weaver = self._state.agents.get(gs_obj.weaver_agent_id)
            if not weaver or weaver.status != "running":
                continue

            # Emit buffer stats for the UI
            stats = self.get_buffer_stats(group)
            self._state._emit("weaver_buffer_stats",
                              group=group, **stats)
            stats_changed = True

            ws = self._state.get_weaver_settings(group)
            if ws.paused:
                continue

            # Check if heartbeat is overdue
            last = self._last_push.get(group, 0)
            if last and (now - last) >= ws.max_interval:
                # Only flush if weaver is idle
                if not weaver.activity or weaver.activity == "waiting":
                    if self._loop:
                        self._loop.create_task(self._flush(group))

        if stats_changed and self._loop:
            self._loop.create_task(self._state.broadcast())

        self._schedule_timer()
