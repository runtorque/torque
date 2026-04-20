"""Weaver event buffer, digest delivery, and system prompt builder.

The weaver is a per-group orchestrator agent that receives event digests
when idle.  This module manages buffering, delivery timing, and the
system prompt assembly for ``--append-system-prompt-file``.
"""

import asyncio
import logging
import re
import time

from .digest_routing import (
    candidate_digest_recipients,
    recipient_wants_digest_event,
    resolve_digest_recipients,
)
from .state import (
    normalize_default_worker_concurrency,
    normalize_weaver_autonomy_mode,
    normalize_weaver_digest_verbosity,
    normalize_weaver_escalation_style,
    normalize_weaver_same_agent_follow_up_preference,
    normalize_weaver_wave_size_preference,
    normalize_worktree_merge_cleanup,
)
from .task_health import HEALTH_SEVERITY
from .weaver_hints import (
    WEAVER_HINT_RESEND_COOLDOWN_SECS,
    compute_weaver_hints,
)

log = logging.getLogger("loom")

_ASK_DIGEST_PREFERRED_LABELS = {
    "approval needed",
    "ask",
    "decision",
    "decision needed",
    "recommended action",
    "recommended next action",
    "request",
    "summary",
    "tl;dr",
    "tldr",
}
_ASK_DIGEST_SECTION_LABELS = {
    "background",
    "context",
    "details",
    "reason",
}
_ASK_DIGEST_MARKUP_PREFIX_RE = re.compile(
    r"^(?:[-*+]\s+|-\s*\[[ xX]\]\s+|>\s*|#{1,6}\s+|\d+[.)]\s+)"
)
_ASK_DIGEST_LABEL_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z0-9 /;_-]{0,40}?):\s*(?P<value>.+)$"
)

# ---------------------------------------------------------------------------
# Base system prompt (the weaver's "firmware")
# ---------------------------------------------------------------------------

_BASE_SYSTEM_PROMPT = """\
You are the designated engineer — the orchestrator agent for the "{group}" group in Loom.

Your role is to manage the task board, dispatch work to agents, react to
events, and maintain a persistent decision journal so you can recover
context after a /clear.

## Available tools

You have access to engineer_* MCP tools:

**Read**: engineer_board_list, engineer_task_show, engineer_agents_list, \
engineer_agent_show, engineer_actions_list, engineer_action_show, \
engineer_board_summary, engineer_session_map, engineer_streams_list, \
engineer_stream_show
**Write**: engineer_task_create, engineer_task_edit, engineer_task_verify, engineer_task_move, \
engineer_task_dispatch, engineer_batch_dispatch, engineer_task_resolve
**Events**: engineer_events, engineer_notifications, engineer_resume
**Journal**: engineer_journal, engineer_journal_read
**Interaction**: engineer_agent_message, engineer_note, engineer_ask, engineer_agent_close, \
engineer_agent_relaunch
**Worktree**: engineer_merge, engineer_rebase, engineer_create_pr, \
engineer_diff, engineer_worktree_remove, engineer_worktree_checkpoint

## Core orchestration model

- **Wave** = the set of streams/tasks you intentionally activate in parallel.
- **Stream** = one branch/worktree execution lane that moves through
  implementation, review, blocker fixes, validation, and merge.
- **Product tasks** = deliverables or user-visible asks.
- **Workflow tasks** = review/fix/validation/conflict-resolution steps that
  move a stream safely.
- **Derived tasks** = Loom-created workflow handoffs, often dispatched
  automatically when actions or workflow transitions create the next step.
- **Visibility items** = communication/context you should see without treating
  it as product scope or queueable work.

Use this hierarchy when reasoning:

- waves schedule work across streams
- streams manage continuity inside a branch/worktree
- tasks record work and ownership handoffs inside the stream
- actions are workflow contracts that shape prompt text, transitions, and
  whether follow-up work should stay in the same stream or form a new review
  boundary

Operational rules:

- One stream may contain multiple product tasks plus multiple workflow tasks
  over time.
- Most derived tasks are workflow tasks inside an existing stream, not new
  root/product asks. Treat derivation as workflow structure unless the follow-up
  clearly starts a new branch/worktree slice.
- A stream should have one **foreground mutable owner** at a time; review and
  validation may exist around it without competing for the mutable lane.
- Queue state belongs to the stream. Future product tasks in the same stream
  may appear as `queued`, `paused_by_blocker`, `paused_by_review`,
  `paused_by_validation`, `held`, or `ready_to_resume`.
- Review blockers and merge conflicts preempt future queued product work in the
  same stream until the gate clears.
- Visibility items should inform your reasoning but should not be treated as
  root/product work or as reasons to widen the wave.
- Prefer stream-level reasoning first: identify the stream's state, gate, and
  recommended next action before reacting to individual workflow tasks.
- Use `engineer_streams_list` and `engineer_stream_show` when branch/worktree
  continuity matters; use task views for detailed audit/history.

## Operating guidelines

1. **Journal discipline** — Write a journal entry for every significant
   decision (dispatch, priority change, resolution).  Write a checkpoint
   entry periodically (every ~10 decisions or when the board state changes
   significantly).  Checkpoints should summarise the board, active agents,
   and your planned next steps.

2. **Project reconnaissance** — Before planning or dispatching, learn the
   repo before trusting the board.  Read `AGENTS.md`, `README.md`, relevant
   docs, and inspect the action catalog with `engineer_actions_list`.  When the
   work touches an unfamiliar area, inspect the codebase, tests, and likely
   entrypoints first.  Journal a compact project map: architecture, test/run
   commands, risky surfaces, and any deploy or verification expectations.

3. **Action discipline** — Actions are contracts, not labels.  Never copy an
   action from another task just because it looks nearby or familiar.  Before
   creating, editing, or dispatching a task, consult `engineer_actions_list`
   and `engineer_action_show` to choose the right action, understand its prompt
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
   should be: engineer_journal_read → engineer_session_map → engineer_events.
   Use `engineer_board_summary` when you want the compact snapshot and
   `engineer_board_list` only when you need the full task inventory. Then
   rebuild context from the repo and action catalog before widening work.

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
   `engineer_diff(..., summary_only=true)` to get structured changed-file
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
   a terminal steady state.  Read `engineer_board_summary`, then either
   dispatch the next best wave according to the human's standing priority
   instructions or post a non-blocking `engineer_note` that proposes the
   next wave and names the constraint that prevents automatic dispatch.
   Stay idle only when the backlog is actually exhausted or the board is
   paused on a human checkpoint, approval, or blocking question.

13. **Human interaction** — Use `engineer_note` for non-blocking notes,
   soft questions, status/context, or proposed next-wave plans that
   should stay visible without pausing orchestration.  Use
   `engineer_ask` only when you need a blocking human decision and the
   board should stop widening work until the answer arrives.  If the
   board is idle with backlog remaining and you only need to surface the
   next recommended wave or a soft priority question, use `engineer_note`
   instead of `engineer_ask`.  The human will reply via the panel or
   directly in your terminal.  After receiving their answer, call
   `engineer_resume` to unpause events.

14. **First session** — When starting a new session (no journal history),
   do a short reconnaissance pass before dispatching: read the repo guidance,
   inspect the action catalog, and understand the current board.  If standing
   priorities are missing or ambiguous after that, call `engineer_ask` to get
   direction.  Do not dispatch blindly, but do not skip repo learning either.

15. **Loom mechanics** — Loom can dispatch multiple tasks to the same
   agent. Use `engineer_batch_dispatch` with a shared `agent_group` when
   several ordered tasks should stay on one worker so later tasks queue
   behind earlier ones. Capacity-limited entries are stored in Loom's
   persistent auto-dispatch queue and resume automatically after
   restart. Use `engineer_task_dispatch(agent=...)` to
   target an existing agent directly. Same-agent queued tasks usually
   share one worktree/branch until merge or cleanup, so use this for
   short tightly coupled follow-ups, not long stacks of medium-sized
   tasks. Actions and worker prompts can handle sequential same-agent
   task execution, so it is reasonable to batch a few closely related
   tasks onto one agent when they should land together. Prefer a fresh
   agent when you want a clean review/merge boundary.
"""

_ENGINEER_ARCHITECT_ESCALATION_SECTION = """\
## Architect escalation

When a non-trivial product or scope decision arises, call
`engineer_message_architect(architect_id=<hiring-architect-id>, message=...)`
BEFORE committing to a direction. The hiring architect owns product-level
scope. Do not silently reinterpret the task.

This only applies when an architect is in your chain. If you are user-owned
and no architect is attached, proceed as normal.
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
            "- When backlog remains and the next step looks plausible, prefer `engineer_note` with a proposed wave over dispatching immediately.",
            "- Ask or wait for human direction before dispatching, merging, or cleaning up when intent is not already explicit.",
        ]
    if mode == "aggressive_auto_continue":
        return [
            "- Treat an idle board with actionable backlog as permission to keep moving unless a real approval gate or blocker exists.",
            "- Prefer `engineer_note` over `engineer_ask` for soft ambiguity; reserve blocking asks for true human decisions.",
            "- Keep workers busy up to the default concurrency when the next wave is reasonably clear and risk is modest.",
        ]
    return [
        "- Dispatch automatically when priorities and the next wave are clear from standing instructions and recent board state.",
        "- Use `engineer_note` for soft ambiguity; reserve `engineer_ask` for blocking human decisions or approvals.",
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
            "- Prefer `engineer_ask` over prolonged autonomous interpretation when a human decision could materially change the plan.",
        ]
    if mode == "keep_moving":
        return [
            "- Keep moving through soft ambiguity when the likely next step is low-risk and reversible.",
            "- Prefer `engineer_note` for visibility and reserve `engineer_ask` for true blockers or approvals.",
        ]
    return [
        "- Prefer `engineer_note` for soft ambiguity and use `engineer_ask` only when the board should genuinely pause for a human decision.",
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
    restrict_to_created_agents = bool(
        getattr(weaver_settings, "restrict_to_created_agents", False)
    )
    lines = [
        "## Operating Policy",
        f"Autonomy mode: {_autonomy_mode_label(mode)}",
        f"Default worker concurrency: {concurrency}",
        f"Wave size preference: {_wave_size_preference_label(wave_size)}",
        "Same-agent follow-up preference: "
        f"{_same_agent_follow_up_preference_label(same_agent)}",
        f"Digest verbosity: {_digest_verbosity_label(digest_verbosity)}",
        f"Escalation style: {_escalation_style_label(escalation_style)}",
        f"Default post-merge cleanup: {_merge_cleanup_label(cleanup_mode)}",
        "Owned-agent restriction: "
        + ("Enabled" if restrict_to_created_agents else "Disabled"),
        "",
        "Apply these policy defaults when the more general guidance above leaves room for judgment:",
        *_autonomy_policy_lines(mode),
        *_wave_size_policy_lines(wave_size),
        *_same_agent_policy_lines(same_agent),
        *_escalation_policy_lines(escalation_style),
        (
            "- When calling `engineer_batch_dispatch` without `max_concurrent`, "
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
    ]
    if restrict_to_created_agents:
        lines.extend([
            "- You can only inspect or control worker agents that you originally created.",
            "- Legacy or human-created agents may still exist on the board, but they are intentionally hidden from your agent-targeted tools.",
        ])
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
            "## Custom Instructions\n"
            f"{ci}"
        )

    return "\n\n".join(parts) + "\n"


def build_engineer_system_prompt(group: str, weaver_settings=None,
                                 action_system_prompt: str = "",
                                 group_settings=None) -> str:
    """Assemble the engineer boot prompt with architect escalation guidance."""
    prompt = build_weaver_system_prompt(
        group,
        weaver_settings,
        action_system_prompt,
        group_settings=group_settings,
    ).rstrip()
    return prompt + "\n\n" + _ENGINEER_ARCHITECT_ESCALATION_SECTION + "\n"




# ---------------------------------------------------------------------------
# Event buffer and digest delivery
# ---------------------------------------------------------------------------

class WeaverEventBuffer:
    """Per-recipient event buffering with idle-gated digest delivery.

    Events are buffered per engineer/architect recipient. When the target
    goes idle and events are pending, the buffer flushes a formatted digest
    to that recipient's terminal. A periodic timer ensures an idle digest
    still arrives even when nothing critical happens.
    """

    def __init__(self, state, bridge, *, inject_message=None):
        self._state = state
        self._bridge = bridge          # adapter fallback for send_text()
        self._inject_message = inject_message
        self._buffers: dict[str, list[dict]] = {}   # agent_id → buffered events
        self._buffer_started: dict[str, float] = {}  # agent_id → oldest buffered event timestamp
        self._sent_events: dict[str, list[dict]] = {}  # agent_id → recently delivered events
        self._last_push: dict[str, float] = {}       # agent_id → timestamp
        self._due_checks: dict[str, asyncio.TimerHandle] = {}  # agent_id → next regular-digest deadline callback
        self._due_check_deadlines: dict[str, float] = {}  # agent_id → wall-clock deadline for due check
        self._pending_flush: dict[str, bool] = {}    # agent_id → flush task scheduled/running
        self._manual_flush_requested: dict[str, bool] = {}  # agent_id → operator requested immediate flush
        self._was_idle_with_question: set[str] = set()  # groups where weaver went idle with pending_question
        self._hint_delivery: dict[str, dict[str, float]] = {}  # group → fingerprint → last sent at
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
        for handle in self._due_checks.values():
            handle.cancel()
        self._due_checks.clear()
        self._due_check_deadlines.clear()

    @staticmethod
    def _event_snapshot(event: dict, *, delivered_at: float | None = None) -> dict:
        snapshot = {
            "id": event.get("id", 0),
            "timestamp": event.get("timestamp", 0),
            "kind": event.get("kind", ""),
            "cell_id": event.get("cell_id", ""),
            "agent_name": event.get("agent_name", ""),
            "group": event.get("group", ""),
            "message": event.get("message", ""),
            "task_id": event.get("task_id", ""),
        }
        if delivered_at is not None:
            snapshot["delivered_at"] = delivered_at
        elif "delivered_at" in event:
            snapshot["delivered_at"] = event.get("delivered_at", 0)
        return snapshot

    def _resolve_recipient_id(self, recipient_or_group: str) -> str:
        recipient_or_group = str(recipient_or_group or "").strip()
        if recipient_or_group in self._state.agents:
            return recipient_or_group
        legacy_weaver = self._state.get_weaver_for_group(recipient_or_group)
        if legacy_weaver:
            return legacy_weaver.id
        return recipient_or_group

    def _digest_recipient(self, agent_id: str):
        target = self._state.agents.get(str(agent_id or "").strip())
        if not target or target.cell_type != "agent":
            return None
        if str(getattr(target, "kind", "") or "").strip() not in {
                "engineer", "architect"}:
            return None
        return target

    def get_sent_events(self, recipient_or_group: str) -> list[dict]:
        agent_id = self._resolve_recipient_id(recipient_or_group)
        return [dict(evt) for evt in self._sent_events.get(agent_id, [])]

    def export_state(self) -> dict:
        agent_ids = {
            cell.id
            for cell in self._state.agents.values()
            if self._digest_recipient(cell.id)
        }
        agent_ids.update(self._buffers.keys())
        agent_ids.update(self._sent_events.keys())

        return {
            "digest_buffer_stats": {
                agent_id: self.get_buffer_stats(agent_id)
                for agent_id in sorted(agent_ids)
            },
            "digest_sent_events": {
                agent_id: self.get_sent_events(agent_id)
                for agent_id in sorted(agent_ids)
                if self._sent_events.get(agent_id)
            },
        }

    def get_buffer_stats(self, recipient_or_group: str) -> dict:
        """Return buffer stats for the UI: event count + seconds until next push."""
        agent_id = self._resolve_recipient_id(recipient_or_group)
        target = self._state.agents.get(agent_id)
        buf = self._buffers.get(agent_id, [])
        settings = self._state.get_agent_digest_settings(agent_id)
        last = self._last_push.get(agent_id, 0)
        now = time.time()
        deadline = None
        manual_flush_requested = bool(
            self._manual_flush_requested.get(agent_id)
        )

        if buf:
            if manual_flush_requested:
                next_in = 0
                deadline = now
            else:
                deadline = self._buffered_digest_deadline(agent_id, settings)
                next_in = self._seconds_until_buffered_digest(
                    agent_id, settings, now=now)
        elif not last:
            next_in = settings.push_interval
            deadline = now + settings.push_interval
        else:
            elapsed = now - last
            next_in = max(0, settings.push_interval - elapsed)
            deadline = last + settings.push_interval

        return {
            "agent_id": agent_id,
            "group": getattr(target, "group", "") or "",
            "buffered_events": len(buf),
            "next_push_in": int(next_in),
            "next_push_at": int(deadline or 0),
            "queued_events": [
                self._event_snapshot(evt) for evt in buf
            ],
            "manual_flush_requested": manual_flush_requested,
        }

    def _buffered_digest_deadline(self, agent_id: str, settings) -> float | None:
        """Return the next eligible wall-clock time for a buffered digest."""
        if not self._buffers.get(agent_id):
            return None
        first = self._buffer_started.get(agent_id)
        if first is None:
            return None

        last = self._last_push.get(agent_id, 0)
        push_deadline = (last + settings.push_interval) if last else (
            first + settings.push_interval
        )
        max_deadline = first + settings.max_interval
        return min(push_deadline, max_deadline)

    def _seconds_until_buffered_digest(self, agent_id: str, settings, *,
                                       now: float | None = None) -> float:
        """Return seconds until buffered events become eligible to flush."""
        deadline = self._buffered_digest_deadline(agent_id, settings)
        if deadline is None:
            return 0
        current = time.time() if now is None else now
        return max(0, deadline - current)

    def _is_buffered_digest_due(self, agent_id: str, settings, *,
                                now: float | None = None) -> bool:
        """Check whether buffered events are eligible for a regular digest."""
        deadline = self._buffered_digest_deadline(agent_id, settings)
        if deadline is None:
            return False
        current = time.time() if now is None else now
        return current >= deadline

    def _cancel_due_check(self, agent_id: str):
        handle = self._due_checks.pop(agent_id, None)
        if handle:
            handle.cancel()
        self._due_check_deadlines.pop(agent_id, None)

    def _ensure_due_check(self, agent_id: str, settings):
        """Schedule a precise wake-up for the next buffered-digest deadline."""
        if not self._loop or self._loop.is_closed():
            return

        deadline = self._buffered_digest_deadline(agent_id, settings)
        if deadline is None:
            self._cancel_due_check(agent_id)
            return

        existing = self._due_checks.get(agent_id)
        existing_deadline = self._due_check_deadlines.get(agent_id)
        if existing and not existing.cancelled() and existing_deadline is not None:
            if abs(existing_deadline - deadline) < 0.5:
                return
            existing.cancel()

        delay = max(0, deadline - time.time())
        self._due_checks[agent_id] = self._loop.call_later(
            delay, self._on_due_check, agent_id)
        self._due_check_deadlines[agent_id] = deadline

    def _on_due_check(self, agent_id: str):
        self._due_checks.pop(agent_id, None)
        self._due_check_deadlines.pop(agent_id, None)
        target = self._digest_recipient(agent_id)
        if not target:
            return
        if not target.activity or target.activity == "waiting":
            self._check_weaver_flush(target)

    def _emit_buffer_stats(self, agent_id: str):
        """Queue a buffer-stats delta for the UI."""
        payload = self.get_buffer_stats(agent_id)
        self._state._emit(
            "digest_buffer_stats",
            **payload,
        )

    def _emit_sent_events(self, agent_id: str):
        target = self._state.agents.get(agent_id)
        self._state._emit(
            "digest_sent_push",
            agent_id=agent_id,
            group=getattr(target, "group", "") or "",
            events=self.get_sent_events(agent_id),
        )

    # -- Public hooks ---------------------------------------------------------

    def on_panel_event(self, event: dict):
        """Called when a panel event is emitted. Buffer for matching recipients."""
        if not event.get("group", ""):
            return
        candidate_ids = candidate_digest_recipients(self._state, event)
        active_recipient_ids = set(resolve_digest_recipients(self._state, event))
        for recipient_id in candidate_ids:
            if recipient_id not in self._state.agent_digest_settings:
                self._state.ensure_agent_digest_settings(recipient_id)
        for recipient_id in candidate_ids:
            if recipient_id not in active_recipient_ids and not (
                    recipient_id in self._state.agent_digest_settings
                    and self._state.get_agent_digest_settings(recipient_id).paused
                    and recipient_wants_digest_event(
                        self._state,
                        recipient_id,
                        event,
                        ignore_pause=True,
                    )
            ):
                continue
            target = self._digest_recipient(recipient_id)
            if not target or not target.session_id or target.status != "running":
                continue
            buf = self._buffers.setdefault(recipient_id, [])
            if not buf:
                self._buffer_started[recipient_id] = time.time()
            buf.append(dict(event))
            self._emit_buffer_stats(recipient_id)

            settings = self._state.get_agent_digest_settings(recipient_id)
            if settings.paused:
                continue
            if not target.activity or target.activity == "waiting":
                self._check_weaver_flush(target)

    def on_delivery_resumed(self, recipient_or_group: str):
        """Re-check buffered events after pause/resume changes."""
        agent_id = self._resolve_recipient_id(recipient_or_group)
        target = self._digest_recipient(agent_id)
        if not target:
            return
        self._emit_buffer_stats(agent_id)
        if not target.activity or target.activity == "waiting":
            if self._buffers.get(agent_id):
                self._cancel_due_check(agent_id)
                self._schedule_flush(agent_id)
            else:
                self._check_weaver_flush(target)

    def on_delivery_paused(self, recipient_or_group: str):
        agent_id = self._resolve_recipient_id(recipient_or_group)
        self._manual_flush_requested.pop(agent_id, None)
        self._cancel_due_check(agent_id)
        self._emit_buffer_stats(agent_id)

    def on_agent_activity_change(self, cell):
        """Called when an agent's activity changes. Flush if a recipient goes idle."""
        legacy_weaver = self._state.get_weaver_for_group(cell.group)
        is_legacy_weaver = bool(legacy_weaver and legacy_weaver.id == cell.id)

        if is_legacy_weaver:
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

        if (
                self._digest_recipient(cell.id)
                and (not cell.activity or cell.activity == "waiting")
        ):
            self._check_weaver_flush(cell)

    def request_manual_flush(self, recipient_or_group: str) -> tuple[bool, str]:
        agent_id = self._resolve_recipient_id(recipient_or_group)
        if not agent_id:
            return False, "Recipient is required"

        target = self._digest_recipient(agent_id)
        if not target or not target.session_id:
            return False, "Digest recipient is not running"

        if not self._loop or self._loop.is_closed():
            return False, "Digest delivery is unavailable right now"

        settings = self._state.get_agent_digest_settings(agent_id)
        if settings.paused:
            return (
                False,
                "Digest delivery is paused. Resume it before sending queued events.",
            )

        if not self._buffers.get(agent_id):
            return False, "No queued events to send"

        self._manual_flush_requested[agent_id] = True
        self._emit_buffer_stats(agent_id)

        if not target.activity or target.activity == "waiting":
            self._check_weaver_flush(target)
        return True, ""

    # -- Internal -------------------------------------------------------------

    def _check_weaver_flush(self, cell):
        """If *cell* is an eligible digest recipient with buffered events, flush."""
        if not self._digest_recipient(getattr(cell, "id", "")):
            return

        agent_id = cell.id
        group = cell.group
        settings = self._state.get_agent_digest_settings(agent_id)
        if settings.paused:
            return

        has_events = bool(self._buffers.get(agent_id))
        if has_events:
            if (self._manual_flush_requested.get(agent_id)
                    or self._is_buffered_digest_due(agent_id, settings)):
                self._cancel_due_check(agent_id)
                self._schedule_flush(agent_id)
            else:
                self._ensure_due_check(agent_id, settings)
            return

        self._cancel_due_check(agent_id)
        if self._due_hints(group, weaver=cell):
            self._schedule_flush(agent_id)
            return
        is_overdue = self._is_heartbeat_due(agent_id, settings)
        if is_overdue:
            self._schedule_flush(agent_id)

    def _schedule_flush(self, agent_id: str):
        """Schedule one flush task per recipient at a time."""
        if not self._loop or self._pending_flush.get(agent_id):
            return
        self._cancel_due_check(agent_id)
        self._pending_flush[agent_id] = True
        self._loop.create_task(self._flush(agent_id))

    def _is_heartbeat_due(self, agent_id: str, settings) -> bool:
        """Check if the idle heartbeat interval has elapsed since last push."""
        interval = getattr(settings, "heartbeat_interval", 0)
        if not interval:
            return False
        last = self._last_push.get(agent_id, 0)
        if last == 0:
            return False  # never pushed — wait for first event
        return (time.time() - last) >= interval

    async def _flush(self, agent_id: str):
        """Format and send buffered events as a digest to one recipient."""
        events = None
        buffer_started_at = None
        try:
            target = self._digest_recipient(agent_id)
            if not target or not target.session_id:
                return

            settings = self._state.get_agent_digest_settings(agent_id)
            if settings.paused:
                return

            # Check recipient is actually idle
            if target.activity and target.activity != "waiting":
                return

            self._manual_flush_requested.pop(agent_id, None)
            events = self._buffers.pop(agent_id, [])
            buffer_started_at = self._buffer_started.pop(agent_id, None)
            due_hints = self._due_hints(target.group, weaver=target)
            board_summary = self._board_summary(target.group, recipient_id=agent_id)
            text = self._format_digest(
                target.group,
                events,
                board_summary,
                target,
                hints=due_hints,
            )

            try:
                if self._inject_message:
                    await self._inject_message(
                        target,
                        text,
                        sender_name="Loom",
                        sender_kind="system",
                        action="digest",
                    )
                else:
                    if hasattr(self._bridge, "prime_input_ready"):
                        self._bridge.prime_input_ready(target.session_id)
                    await self._bridge.send_text(target.session_id, text + "\n")
                delivered_at = time.time()
                self._last_push[agent_id] = delivered_at
                log.info("Digest sent to '%s' (%d events)",
                         target.name, len(events))
                if events:
                    sent = self._sent_events.setdefault(agent_id, [])
                    sent.extend(
                        self._event_snapshot(evt, delivered_at=delivered_at)
                        for evt in events
                    )
                    if len(sent) > 200:
                        self._sent_events[agent_id] = sent[-200:]
                    self._emit_sent_events(agent_id)
                if due_hints:
                    self._record_sent_hints(
                        target.group,
                        due_hints,
                        sent_at=delivered_at,
                    )
            except Exception:
                log.exception("Failed to send digest to '%s'",
                              target.name)
                if events:
                    current = self._buffers.get(agent_id, [])
                    self._buffers[agent_id] = events + current
                    if buffer_started_at is not None:
                        restored = self._buffer_started.get(agent_id)
                        self._buffer_started[agent_id] = (
                            buffer_started_at
                            if restored is None
                            else min(restored, buffer_started_at)
                        )
        finally:
            self._pending_flush.pop(agent_id, None)

            # If new events arrived during the send, or the idle digest is
            # already overdue again, queue the next flush.
            target = self._digest_recipient(agent_id)
            if target:
                is_idle = not target.activity or target.activity == "waiting"
                settings = self._state.get_agent_digest_settings(agent_id)
                if is_idle and not settings.paused:
                    self._check_weaver_flush(target)

            if events:
                self._emit_buffer_stats(agent_id)
                if self._loop:
                    self._loop.create_task(self._state.broadcast())

    def _format_digest(self, group: str, events: list[dict], board_summary: str,
                       weaver=None, hints: list[dict] | None = None) -> str:
        recipient_id = str(getattr(weaver, "id", "") or "").strip()
        verbosity = normalize_weaver_digest_verbosity(
            getattr(
                self._state.get_agent_digest_settings(recipient_id)
                if recipient_id else self._state.get_weaver_settings(group),
                "digest_verbosity",
                "balanced",
            )
        )
        event_limit = 5 if verbosity == "compact" else None
        lines = [f"## Loom Digest ({len(events)} event"
                 f"{'s' if len(events) != 1 else ''})"]
        if events:
            visible_events = events[:event_limit] if event_limit else events
            for evt in visible_events:
                lines.append(
                    self._format_digest_event_line(
                        evt,
                        verbosity=verbosity,
                    )
                )
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
        if hints:
            hint_limit = 1 if verbosity == "compact" else (
                3 if verbosity == "detailed" else 2
            )
            lines.append(
                "Hints: "
                + " · ".join(
                    self._truncate_digest_text(
                        str((hint or {}).get("message", "") or ""),
                        limit=72 if verbosity == "compact" else 180,
                    )
                    for hint in hints[:hint_limit]
                    if str((hint or {}).get("message", "") or "").strip()
                )
            )

        # Context warning
        if weaver:
            ctx_warn = self._context_warning(weaver)
            if ctx_warn:
                lines.append(ctx_warn)

        lines.append("---")
        return "\n".join(lines)

    def _format_digest_event_line(self, evt: dict, *, verbosity: str) -> str:
        kind = evt.get("kind", "")
        agent = self._truncate_digest_text(
            self._normalize_digest_text(evt.get("agent_name", "")),
            limit=24 if verbosity == "compact" else 80,
        )
        msg = self._format_digest_event_message(evt, verbosity=verbosity)
        if agent and msg:
            return f"  {kind}: {agent} — {msg}"
        if msg:
            return f"  {kind}: {msg}"
        return f"  {kind}: {agent}"

    def _format_digest_event_message(self, evt: dict, *, verbosity: str) -> str:
        kind = evt.get("kind")
        if kind == "task_artifact_uploaded":
            limit = 96 if verbosity == "compact" else (
                320 if verbosity == "detailed" else 260
            )
        else:
            limit = 72 if verbosity == "compact" else 240
        message = self._normalize_digest_text(evt.get("message", ""))
        if kind != "ask_created":
            return self._truncate_digest_text(message, limit=limit)

        task_id = str(evt.get("task_id", "") or "").strip()
        ask_task = self._state.board_tasks.get(task_id) if task_id else None
        ask_context = self._ask_digest_summary(
            getattr(ask_task, "description", "") or ""
        )
        if not ask_context:
            return self._truncate_digest_text(message, limit=limit)

        if verbosity == "compact":
            title_limit = 30
            summary_limit = 38
        elif verbosity == "detailed":
            title_limit = 80
            summary_limit = 150
        else:
            title_limit = 56
            summary_limit = 110

        title = self._truncate_digest_text(message, limit=title_limit)
        summary = self._normalize_digest_text(ask_context)

        if summary.casefold().startswith(message.casefold()):
            summary = summary[len(message):].lstrip(" —-:;,")
        elif message.casefold().startswith(summary.casefold()):
            summary = ""

        if not summary:
            return self._truncate_digest_text(message, limit=limit)

        summary = self._truncate_digest_text(summary, limit=summary_limit)
        return f"{title} — {summary}"

    @classmethod
    def _ask_digest_summary(cls, description: str) -> str:
        text = str(description or "").replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip():
            return ""

        preferred = ""
        fallback = ""
        for raw_line in text.split("\n"):
            line = cls._normalize_digest_text(raw_line)
            if not line:
                continue
            line = cls._strip_ask_digest_markup(line)
            if not line:
                continue

            label_match = _ASK_DIGEST_LABEL_RE.match(line)
            if label_match:
                label = cls._normalize_digest_text(
                    label_match.group("label")
                ).casefold()
                value = cls._normalize_digest_text(
                    label_match.group("value")
                )
                if not value:
                    continue
                if label in _ASK_DIGEST_PREFERRED_LABELS:
                    preferred = value
                    break
                if label in _ASK_DIGEST_SECTION_LABELS:
                    if not fallback:
                        fallback = value
                    continue
                if not fallback:
                    fallback = value
                continue

            if line.endswith(":") and len(line.split()) <= 4:
                continue
            if not fallback:
                fallback = line

        candidate = preferred or fallback or cls._normalize_digest_text(text)
        return cls._first_sentence(candidate)

    @staticmethod
    def _strip_ask_digest_markup(text: str) -> str:
        cleaned = str(text or "").strip()
        while True:
            updated = _ASK_DIGEST_MARKUP_PREFIX_RE.sub("", cleaned, count=1)
            if updated == cleaned:
                break
            cleaned = updated.strip()
        return cleaned.strip("`")

    @staticmethod
    def _normalize_digest_text(text: str) -> str:
        return " ".join(str(text or "").split())

    @classmethod
    def _first_sentence(cls, text: str) -> str:
        normalized = cls._normalize_digest_text(text)
        if not normalized:
            return ""
        match = re.search(r"(?<=[.!?])\s+", normalized)
        if match and match.start() <= 160:
            return normalized[:match.start()].strip()
        return normalized

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

    def _board_summary(self, group: str, *, recipient_id: str = "") -> str:
        """Count tasks per lane for a group, including unhealthy rollups."""
        verbosity = normalize_weaver_digest_verbosity(
            getattr(
                self._state.get_agent_digest_settings(recipient_id)
                if recipient_id else self._state.get_weaver_settings(group),
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

    def _current_hints(self, group: str, *, weaver=None) -> list[dict]:
        weaver_id = ""
        if weaver and getattr(weaver, "id", ""):
            weaver_id = weaver.id
        else:
            live_weaver = self._state.get_weaver_for_group(group)
            if live_weaver:
                weaver_id = live_weaver.id
        return compute_weaver_hints(
            self._state,
            group,
            weaver_id=weaver_id,
        )

    def _due_hints(self, group: str, *, weaver=None,
                   now: float | None = None) -> list[dict]:
        current = self._current_hints(group, weaver=weaver)
        current_fingerprints = {
            str(hint.get("fingerprint", "") or "").strip()
            for hint in current
            if str(hint.get("fingerprint", "") or "").strip()
        }
        delivery = self._hint_delivery.setdefault(group, {})
        stale_fingerprints = [
            fingerprint for fingerprint in list(delivery)
            if fingerprint not in current_fingerprints
        ]
        for fingerprint in stale_fingerprints:
            delivery.pop(fingerprint, None)

        sent_now = time.time() if now is None else now
        due = []
        for hint in current:
            fingerprint = str(hint.get("fingerprint", "") or "").strip()
            if not fingerprint:
                continue
            last_sent_at = delivery.get(fingerprint, 0.0)
            if last_sent_at and (
                    sent_now - last_sent_at) < WEAVER_HINT_RESEND_COOLDOWN_SECS:
                continue
            due.append(hint)
        return due

    def _record_sent_hints(self, group: str, hints: list[dict], *,
                           sent_at: float) -> None:
        if not hints:
            return
        delivery = self._hint_delivery.setdefault(group, {})
        for hint in hints:
            fingerprint = str(hint.get("fingerprint", "") or "").strip()
            if fingerprint:
                delivery[fingerprint] = sent_at

    # -- Timer ----------------------------------------------------------------

    def _schedule_timer(self):
        """Schedule a periodic check every 10 seconds."""
        if self._loop and not self._loop.is_closed():
            self._timer_handle = self._loop.call_later(
                10.0, self._timer_tick)

    def _timer_tick(self):
        """Check if any engineer/architect recipient needs a digest."""
        self._timer_handle = None
        stats_changed = False

        for recipient in self._state.agents.values():
            if recipient.cell_type != "agent":
                continue
            if str(getattr(recipient, "kind", "") or "").strip() not in {
                    "engineer", "architect"}:
                continue
            if recipient.status != "running" or not recipient.session_id:
                continue

            # Emit buffer stats for the UI
            stats = self.get_buffer_stats(recipient.id)
            self._state._emit("digest_buffer_stats", **stats)
            stats_changed = True

            settings = self._state.get_agent_digest_settings(recipient.id)
            if settings.paused:
                continue

            is_idle = not recipient.activity or recipient.activity == "waiting"
            if is_idle:
                self._check_weaver_flush(recipient)

        if stats_changed and self._loop:
            self._loop.create_task(self._state.broadcast())

        self._schedule_timer()
