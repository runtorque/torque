"""Weaver event buffer, digest delivery, and system prompt builder.

The weaver is a per-group orchestrator agent that receives event digests
when idle.  This module manages buffering, delivery timing, and the
system prompt assembly for ``--append-system-prompt-file``.
"""

import asyncio
import logging
import time

from .state import (
    WEAVER_MANDATORY_EVENTS,
    normalize_default_worker_concurrency,
    normalize_weaver_autonomy_mode,
    normalize_weaver_digest_verbosity,
    normalize_weaver_escalation_style,
    normalize_weaver_same_agent_follow_up_preference,
    normalize_weaver_wave_size_preference,
    normalize_worktree_merge_cleanup,
)
from .task_health import HEALTH_SEVERITY

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
weaver_agent_show, weaver_actions_list, weaver_action_show, \
weaver_board_summary
**Write**: weaver_task_create, weaver_task_edit, weaver_task_verify, weaver_task_move, \
weaver_task_dispatch, weaver_batch_dispatch, weaver_task_resolve
**Events**: weaver_events, weaver_notifications, weaver_resume
**Journal**: weaver_journal, weaver_journal_read
**Interaction**: weaver_agent_message, weaver_note, weaver_ask, weaver_agent_close, \
weaver_agent_relaunch
**Worktree**: weaver_merge, weaver_rebase, weaver_create_pr, \
weaver_diff, weaver_worktree_remove, weaver_worktree_checkpoint

## Operating guidelines

1. **Journal discipline** — Write a journal entry for every significant
   decision (dispatch, priority change, resolution).  Write a checkpoint
   entry periodically (every ~10 decisions or when the board state changes
   significantly).  Checkpoints should summarise the board, active agents,
   and your planned next steps.

2. **Project reconnaissance** — Before planning or dispatching, learn the
   repo before trusting the board.  Read `AGENTS.md`, `README.md`, relevant
   docs, and inspect the action catalog with `weaver_actions_list`.  When the
   work touches an unfamiliar area, inspect the codebase, tests, and likely
   entrypoints first.  Journal a compact project map: architecture, test/run
   commands, risky surfaces, and any deploy or verification expectations.

3. **Action discipline** — Actions are contracts, not labels.  Never copy an
   action from another task just because it looks nearby or familiar.  Before
   creating, editing, or dispatching a task, consult `weaver_actions_list`
   and `weaver_action_show` to choose the right action, understand its prompt
   shape, and fill any required variables deliberately.  Prefer the action
   catalog over task imitation.

4. **Transition discipline** — Treat action transitions as part of the task's
   workflow contract.  When a task reaches a natural stopping point, inspect
   the current action's transitions before merging, closing an agent, or
   marking the workflow complete.  Do not close an agent just because the
   current task is done if the next legitimate step should be expressed as a
   transition, re-review, or human sign-off.

5. **Task-writing standards** — Write tasks for execution, not just tracking.
   A good task description should point at likely files, modules, systems, or
   user-visible surfaces; explain the concrete behavior to change; name key
   constraints or risks; and state the expected verification.  Avoid shallow
   descriptions like "fix X" with no code direction.  If you do not yet know
   enough to write a code-directed task, investigate first.

6. **Event response** — When you receive a Loom Digest, process each event:
   - task_completed → decide the next step (dispatch follow-up, close out, etc.)
   - agent_error / agent_blocked → investigate and help or escalate
   - agent_reply → incorporate the information and continue
   - ask_created → review and resolve or escalate to the human
   - task_verification_updated → review pending/failed verification before sending the next wave

7. **Context recovery** — After a /clear or restart, your first actions
   should be: weaver_journal_read → weaver_board_summary → weaver_events.
   Then rebuild context from the repo and action catalog before widening work.
   Use weaver_board_list only when you need the full task inventory.

8. **Dispatch strategy** — Reuse context, but keep branch boundaries
   clean.  Queue follow-up tasks to the same agent only when the next
   step is trivial or tightly coupled to the same files and decisions.
   When several ready tasks clearly address the same subject, files, or
   decision surface, prefer dispatching them together to the same agent
   up front instead of scattering them across workers.  Loom actions and
   same-agent queues can handle short sequential task runs on one shared
   branch, so group related work intentionally when you expect it to
   review and merge as one coherent slice.
   Prefer short same-agent queues over long sequential backlogs, and
   prefer a clean merge boundary over leaving multiple medium-sized tasks
   stacked on one shared branch.  Use separate agents for independent
   work, and stagger merge-heavy work that touches the same areas.
   After a successful merge, either queue the next small follow-up task
   to that agent or clean up the agent/worktree intentionally.

9. **Diff review** — For large changes, start with
   `weaver_diff(..., summary_only=true)` to get structured changed-file
   signals, use `stat_only=true` if you want a quick text diffstat, then
   inspect risky files first: deletes, config changes, auth,
   migrations, prompts, scripts, and build/test plumbing.

10. **Recovery checklist** — On recovery, check for stale agents with no
   useful progress, non-healthy tasks (blocked, idle-risk, stalled,
   thrashing), orphaned or already-merged worktrees, and unresolved asks
   before dispatching more work.

11. **Wave planning** — Dispatch in short waves.  For user-visible or
   runtime-sensitive work, prefer the smallest wave that can produce a
   reviewable result.  Fill open slots with a mix of one complex task and
   simpler parallel work, then rotate in queued tasks as agents finish
   instead of dispatching everything at once.  After any meaningful UI or
   runtime change, pause before widening the wave and decide whether this
   is the right point for deploy, restart, or smoke verification.  Treat
   pending or failed verification on active work as a pause signal for the
   next related wave until the checkpoint is resolved or a human accepts
   the remaining risk.  If multiple ready tasks touch the same product
   surface, stop widening the wave; let one path merge or verify before
   dispatching more work there.

12. **Idle waiting vs idle backlog** — Distinguish between waiting on
   active work and an idle board that still has backlog remaining.
   When agents are already running or tasks are already in progress and
   there's nothing else worth dispatching yet, wait for Loom digests.
   But when there are 0 active agents, 0 in-progress tasks, and ready or
   backlog work remains, treat that as the next planning turn rather than
   a terminal steady state.  Read `weaver_board_summary`, then either
   dispatch the next best wave according to the human's standing priority
   instructions or post a non-blocking `weaver_note` that proposes the
   next wave and names the constraint that prevents automatic dispatch.
   Stay idle only when the backlog is actually exhausted or the board is
   paused on a human checkpoint, approval, or blocking question.

13. **Human interaction** — Use `weaver_note` for non-blocking notes,
   soft questions, status/context, or proposed next-wave plans that
   should stay visible without pausing orchestration.  Use
   `weaver_ask` only when you need a blocking human decision and the
   board should stop widening work until the answer arrives.  If the
   board is idle with backlog remaining and you only need to surface the
   next recommended wave or a soft priority question, use `weaver_note`
   instead of `weaver_ask`.  The human will reply via the panel or
   directly in your terminal.  After receiving their answer, call
   `weaver_resume` to unpause events.

14. **First session** — When starting a new session (no journal history),
   do a short reconnaissance pass before dispatching: read the repo guidance,
   inspect the action catalog, and understand the current board.  If standing
   priorities are missing or ambiguous after that, call `weaver_ask` to get
   direction.  Do not dispatch blindly, but do not skip repo learning either.

15. **Loom mechanics** — Loom can dispatch multiple tasks to the same
   agent. Use `weaver_batch_dispatch` with a shared `agent_group` when
   several ordered tasks should stay on one worker so later tasks queue
   behind earlier ones. Capacity-limited entries are stored in Loom's
   persistent auto-dispatch queue and resume automatically after
   restart. Use `weaver_task_dispatch(agent=...)` to
   target an existing agent directly. Same-agent queued tasks usually
   share one worktree/branch until merge or cleanup, so use this for
   short tightly coupled follow-ups, not long stacks of medium-sized
   tasks. Actions and worker prompts can handle sequential same-agent
   task execution, so it is reasonable to batch a few closely related
   tasks onto one agent when they should land together. Prefer a fresh
   agent when you want a clean review/merge boundary.
"""


def _autonomy_mode_label(mode: str) -> str:
    labels = {
        "suggest_only": "Suggest only",
        "dispatch_when_clear": "Dispatch when clear",
        "aggressive_auto_continue": "Aggressive auto-continue",
    }
    return labels.get(mode, "Dispatch when clear")


def _merge_cleanup_label(mode: str) -> str:
    labels = {
        "keep": "Keep agent session and worktree",
        "close": "Close agent session only",
        "remove": "Remove worktree only",
        "close_remove": "Close agent session and remove worktree",
    }
    return labels.get(mode, "Keep agent session and worktree")


def _wave_size_preference_label(mode: str) -> str:
    labels = {
        "small": "Small reviewable waves",
        "balanced": "Balanced waves",
        "large": "Fill available capacity",
    }
    return labels.get(mode, "Small reviewable waves")


def _same_agent_follow_up_preference_label(mode: str) -> str:
    labels = {
        "balanced": "Balanced",
        "prefer_same_agent": "Prefer same agent",
        "prefer_fresh_agent": "Prefer fresh agent",
    }
    return labels.get(mode, "Balanced")


def _digest_verbosity_label(mode: str) -> str:
    labels = {
        "compact": "Compact",
        "balanced": "Balanced",
        "detailed": "Detailed",
    }
    return labels.get(mode, "Balanced")


def _escalation_style_label(mode: str) -> str:
    labels = {
        "ask_early": "Ask early",
        "note_then_ask": "Note first, ask when blocked",
        "keep_moving": "Keep moving unless blocked",
    }
    return labels.get(mode, "Note first, ask when blocked")


def _autonomy_policy_lines(mode: str) -> list[str]:
    if mode == "suggest_only":
        return [
            "- Do not widen the wave automatically just because work exists.",
            "- When backlog remains and the next step looks plausible, prefer `weaver_note` with a proposed wave over dispatching immediately.",
            "- Ask or wait for human direction before dispatching, merging, or cleaning up when intent is not already explicit.",
        ]
    if mode == "aggressive_auto_continue":
        return [
            "- Treat an idle board with actionable backlog as permission to keep moving unless a real approval gate or blocker exists.",
            "- Prefer `weaver_note` over `weaver_ask` for soft ambiguity; reserve blocking asks for true human decisions.",
            "- Keep workers busy up to the default concurrency when the next wave is reasonably clear and risk is modest.",
        ]
    return [
        "- Dispatch automatically when priorities and the next wave are clear from standing instructions and recent board state.",
        "- Use `weaver_note` for soft ambiguity; reserve `weaver_ask` for blocking human decisions or approvals.",
        "- Keep waves reviewable and avoid widening work when verification, review boundaries, or shared-surface risk says to pause.",
    ]


def _wave_size_policy_lines(mode: str) -> list[str]:
    if mode == "large":
        return [
            "- When risk is modest, fill available worker slots instead of waiting for perfectly tiny slices.",
            "- Prefer bundling multiple clearly related ready tasks into the same dispatch wave when review boundaries still look manageable.",
        ]
    if mode == "balanced":
        return [
            "- Prefer medium-sized waves that keep workers busy without widening across the same risky surface too quickly.",
        ]
    return [
        "- Prefer the smallest wave that can still produce a reviewable result.",
        "- Pause sooner before widening work on user-visible, runtime-sensitive, or shared-surface changes.",
    ]


def _same_agent_policy_lines(mode: str) -> list[str]:
    if mode == "prefer_same_agent":
        return [
            "- Bias toward same-agent queued follow-ups when context continuity clearly outweighs the cost of a longer shared branch.",
        ]
    if mode == "prefer_fresh_agent":
        return [
            "- Bias toward fresh agents and cleaner review boundaries unless the follow-up is truly trivial or tightly coupled.",
        ]
    return [
        "- Reuse the same agent for short tightly coupled follow-ups, but prefer a fresh agent when you want a cleaner merge boundary.",
    ]


def _escalation_policy_lines(mode: str) -> list[str]:
    if mode == "ask_early":
        return [
            "- Escalate sooner when priorities, approvals, or product intent are even moderately ambiguous.",
            "- Prefer `weaver_ask` over prolonged autonomous interpretation when a human decision could materially change the plan.",
        ]
    if mode == "keep_moving":
        return [
            "- Keep moving through soft ambiguity when the likely next step is low-risk and reversible.",
            "- Prefer `weaver_note` for visibility and reserve `weaver_ask` for true blockers or approvals.",
        ]
    return [
        "- Prefer `weaver_note` for soft ambiguity and use `weaver_ask` only when the board should genuinely pause for a human decision.",
    ]


def _build_policy_section(weaver_settings=None, group_settings=None) -> str:
    mode = normalize_weaver_autonomy_mode(
        getattr(weaver_settings, "autonomy_mode", "")
    )
    concurrency = normalize_default_worker_concurrency(
        getattr(weaver_settings, "default_worker_concurrency", 2)
    )
    wave_size = normalize_weaver_wave_size_preference(
        getattr(weaver_settings, "wave_size_preference", "small")
    )
    same_agent = normalize_weaver_same_agent_follow_up_preference(
        getattr(weaver_settings, "same_agent_follow_up_preference", "balanced")
    )
    digest_verbosity = normalize_weaver_digest_verbosity(
        getattr(weaver_settings, "digest_verbosity", "balanced")
    )
    escalation_style = normalize_weaver_escalation_style(
        getattr(weaver_settings, "escalation_style", "note_then_ask")
    )
    cleanup_mode = normalize_worktree_merge_cleanup(
        getattr(group_settings, "worktree_merge_cleanup", "keep")
    )
    lines = [
        "── Operating Policy "
        "──────────────────────────",
        f"Autonomy mode: {_autonomy_mode_label(mode)}",
        f"Default worker concurrency: {concurrency}",
        f"Wave size preference: {_wave_size_preference_label(wave_size)}",
        "Same-agent follow-up preference: "
        f"{_same_agent_follow_up_preference_label(same_agent)}",
        f"Digest verbosity: {_digest_verbosity_label(digest_verbosity)}",
        f"Escalation style: {_escalation_style_label(escalation_style)}",
        f"Default post-merge cleanup: {_merge_cleanup_label(cleanup_mode)}",
        "",
        "Apply these policy defaults when the more general guidance above leaves room for judgment:",
        *_autonomy_policy_lines(mode),
        *_wave_size_policy_lines(wave_size),
        *_same_agent_policy_lines(same_agent),
        *_escalation_policy_lines(escalation_style),
        (
            "- When calling `weaver_batch_dispatch` without `max_concurrent`, "
            f"use {concurrency} as the default limit."
        ),
        (
            "- Shape Loom digests as "
            f"{_digest_verbosity_label(digest_verbosity).lower()} by default."
        ),
        (
            "- After a successful merge with no explicit cleanup flags, "
            f"default to: {_merge_cleanup_label(cleanup_mode)}."
        ),
        "────────────────────────────────────────────────",
    ]
    return "\n".join(lines)


def build_weaver_system_prompt(group: str, weaver_settings=None,
                               action_system_prompt: str = "",
                               group_settings=None) -> str:
    """Assemble the full system prompt for a weaver agent.

    Concatenates: base identity → action system_prompt → structured policy
    section → custom instructions.
    """
    parts = [_BASE_SYSTEM_PROMPT.format(group=group)]

    if action_system_prompt:
        parts.append(action_system_prompt.rstrip())

    if weaver_settings or group_settings:
        parts.append(_build_policy_section(weaver_settings, group_settings))

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
    digest to the weaver's terminal.  A periodic timer ensures an idle
    digest still arrives even when nothing critical happens.
    """

    def __init__(self, state, bridge):
        self._state = state
        self._bridge = bridge          # ITerm2Adapter — for send_text()
        self._buffers: dict[str, list[dict]] = {}   # group → buffered events
        self._last_push: dict[str, float] = {}       # group → timestamp
        self._pending_flush: dict[str, bool] = {}    # group → flush task scheduled/running
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

    def _emit_buffer_stats(self, group: str):
        """Queue a buffer-stats delta for the UI."""
        self._state._emit(
            "weaver_buffer_stats",
            group=group,
            **self.get_buffer_stats(group),
        )

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

        kind = event.get("kind", "")
        # Check if this event type should be buffered
        if kind not in WEAVER_MANDATORY_EVENTS:
            if kind not in ws.enabled_events:
                return

        buf = self._buffers.setdefault(group, [])
        buf.append(event)
        self._emit_buffer_stats(group)

        if ws.paused:
            return

        # If the weaver is already idle, schedule a flush now.
        # Without this, buffered events sit until the weaver's next
        # activity change or the heartbeat timer (up to max_interval).
        if not weaver.activity or weaver.activity == "waiting":
            self._schedule_flush(group)

    def on_delivery_resumed(self, group: str):
        """Re-check a group's buffered events after pause/resume changes."""
        weaver = self._state.get_weaver_for_group(group)
        if not weaver:
            return
        self._emit_buffer_stats(group)
        if not weaver.activity or weaver.activity == "waiting":
            self._check_weaver_flush(weaver)

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
                            group, pending_question="",
                            paused=False)

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
        is_overdue = self._is_digest_due(group, ws)

        if has_events or is_overdue:
            self._schedule_flush(group)

    def _schedule_flush(self, group: str):
        """Schedule one flush task per group at a time."""
        if not self._loop or self._pending_flush.get(group):
            return
        self._pending_flush[group] = True
        self._loop.create_task(self._flush(group))

    def _is_digest_due(self, group: str, ws) -> bool:
        """Check if the idle heartbeat interval has elapsed since last push."""
        interval = getattr(ws, "heartbeat_interval", 0)
        if not interval:
            return False
        last = self._last_push.get(group, 0)
        if last == 0:
            return False  # never pushed — wait for first event
        return (time.time() - last) >= interval

    async def _flush(self, group: str):
        """Format and send buffered events as a digest to the weaver."""
        events = None
        try:
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
            text = self._format_digest(group, events, board_summary, weaver)

            self._last_push[group] = time.time()

            try:
                await self._bridge.send_text(weaver.session_id, text + "\n")
                log.info("Weaver digest sent to '%s' (%d events)",
                         weaver.name, len(events))
            except Exception:
                log.exception("Failed to send weaver digest to '%s'",
                              weaver.name)
        finally:
            self._pending_flush.pop(group, None)

            # If new events arrived during the send, or the idle digest is
            # already overdue again, queue the next flush.
            weaver = self._state.get_weaver_for_group(group)
            if weaver:
                is_idle = not weaver.activity or weaver.activity == "waiting"
                ws = self._state.get_weaver_settings(group)
                if is_idle and not ws.paused and (
                        self._buffers.get(group)
                        or self._is_digest_due(group, ws)):
                    self._schedule_flush(group)

            if events:
                self._emit_buffer_stats(group)
                if self._loop:
                    self._loop.create_task(self._state.broadcast())

    def _format_digest(self, group: str, events: list[dict], board_summary: str,
                       weaver=None) -> str:
        verbosity = normalize_weaver_digest_verbosity(
            getattr(
                self._state.get_weaver_settings(group),
                "digest_verbosity",
                "balanced",
            )
        )
        event_limit = 5 if verbosity == "compact" else None
        lines = [f"── Loom Digest ({len(events)} event"
                 f"{'s' if len(events) != 1 else ''}) "
                 f"──────────────────────────"]
        if events:
            visible_events = events[:event_limit] if event_limit else events
            for evt in visible_events:
                kind = evt.get("kind", "")
                agent = self._truncate_digest_text(
                    evt.get("agent_name", ""),
                    limit=24 if verbosity == "compact" else 80,
                )
                msg = self._truncate_digest_text(
                    evt.get("message", ""),
                    limit=72 if verbosity == "compact" else 240,
                )
                if agent and msg:
                    lines.append(f"  {kind}: {agent} — {msg}")
                elif msg:
                    lines.append(f"  {kind}: {msg}")
                else:
                    lines.append(f"  {kind}: {agent}")
            hidden = len(events) - len(visible_events)
            if hidden > 0:
                lines.append(
                    f"  … {hidden} more event{'s' if hidden != 1 else ''}"
                )
        else:
            lines.append("  No new events since last digest.")

        lines.append("")
        lines.append(f"Board: {board_summary}")
        include_active = verbosity == "detailed" or not events
        include_attention = verbosity == "detailed" or not events
        if include_active:
            active_summary = self._active_agents_summary()
            if active_summary:
                lines.append(f"Active: {active_summary}")
        if include_attention:
            attention = self._attention_summary(
                group,
                limit=5 if verbosity == "detailed" else 3,
            )
            if attention:
                lines.append(attention)

        # Context warning
        if weaver:
            ctx_warn = self._context_warning(weaver)
            if ctx_warn:
                lines.append(ctx_warn)

        lines.append("────────────────────────────────────────────────")
        return "\n".join(lines)

    def _active_agents_summary(self) -> str:
        actives = []
        for c in self._state.agents.values():
            if c.cell_type == "agent" and c.activity:
                actives.append(f"{c.slug or c.name} ({c.activity})")
        return " · ".join(actives)

    def _attention_summary(self, group: str, *, limit: int = 3) -> str:
        """Summarize blocked/unhealthy tasks for digests and heartbeats."""
        items = []
        for task in self._state.board_tasks.values():
            if task.group != group or task.lane == "Done":
                continue
            health_state = getattr(task, "health_state", "healthy") or "healthy"
            if health_state == "healthy":
                continue
            items.append((health_state, task.task))
        if not items:
            return ""
        items.sort(
            key=lambda item: (
                -HEALTH_SEVERITY.get(item[0], 0),
                item[1].lower(),
            ),
        )
        preview = [
            f"{state_name}: {title[:40]}"
            for state_name, title in items[:limit]
        ]
        return "Attention: " + " · ".join(preview)

    def _board_summary(self, group: str) -> str:
        """Count tasks per lane for a group, including unhealthy rollups."""
        verbosity = normalize_weaver_digest_verbosity(
            getattr(
                self._state.get_weaver_settings(group),
                "digest_verbosity",
                "balanced",
            )
        )
        counts: dict[str, int] = {}
        unhealthy_counts: dict[str, int] = {}
        unhealthy_items: list[tuple[str, str]] = []
        for t in self._state.board_tasks.values():
            if t.group != group:
                continue
            counts[t.lane] = counts.get(t.lane, 0) + 1
            health_state = getattr(t, "health_state", "healthy") or "healthy"
            if t.lane != "Done" and health_state != "healthy":
                unhealthy_counts[health_state] = (
                    unhealthy_counts.get(health_state, 0) + 1
                )
                unhealthy_items.append((health_state, t.task))
        parts = [f"{count} {lane}" for lane, count in counts.items()]
        if unhealthy_counts:
            ordered = []
            for state_name, count in sorted(
                unhealthy_counts.items(),
                key=lambda item: -HEALTH_SEVERITY.get(item[0], 0),
            ):
                ordered.append(f"{count} {state_name}")
            parts.append("health " + ", ".join(ordered))
            if verbosity != "compact":
                unhealthy_items.sort(
                    key=lambda item: (
                        -HEALTH_SEVERITY.get(item[0], 0),
                        item[1].lower(),
                    ),
                )
                preview = [
                    f"{title[:40]} ({state_name})"
                    for state_name, title in unhealthy_items[:3]
                ]
                parts.append("risk " + ", ".join(preview))
        return " · ".join(parts) if parts else "empty"

    @staticmethod
    def _truncate_digest_text(text: str, *, limit: int) -> str:
        text = str(text or "")
        if limit <= 0 or len(text) <= limit:
            return text
        return text[:max(limit - 1, 0)] + "…"

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
        """Check if any weaver needs a digest and emit buffer stats."""
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

            # Flush if weaver is idle and has buffered events,
            # or if an idle heartbeat digest is overdue.
            is_idle = not weaver.activity or weaver.activity == "waiting"
            has_events = bool(self._buffers.get(group))
            digest_due = self._is_digest_due(group, ws)
            if is_idle and (has_events or digest_due):
                self._schedule_flush(group)

        if stats_changed and self._loop:
            self._loop.create_task(self._state.broadcast())

        self._schedule_timer()
