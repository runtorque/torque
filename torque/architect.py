"""Architect system prompt builder.

The architect is a user-created persistent agent that owns product-level
scope for a group: hiring engineers, routing work to them, recording
decisions, and maintaining a private journal so context survives /clear
and daemon restarts. This module assembles the architect's boot prompt
in the same shape as the engineer prompt (``torque/engineer.py``) so the two
surfaces stay structurally comparable.
"""

from __future__ import annotations

from .server_prompts import (
    build_torque_system_prompt,
    build_owner_user_message_guidance,
    build_shared_memory_guidance,
)
from .mcp_canonical import canonicalize_tool_references
from .state import (
    normalize_architect_autonomy_mode,
    normalize_architect_journal_checkpoint_frequency,
)


# ---------------------------------------------------------------------------
# Base system prompt (the architect's "firmware")
# ---------------------------------------------------------------------------

_BASE_SYSTEM_PROMPT = """\
You are the designated architect for the "{group}" group in Torque.

Your role is to own product-level scope: shape what should be built,
route work to the right engineers, hire new engineers when the group is
under-staffed, record decisions, and maintain a persistent journal so
you can recover context after a /clear or daemon restart. You do not
write code yourself and you do not directly dispatch workers — engineers
own implementation and worker orchestration.

## Available tools

You have access to a caller-scoped set of canonical MCP tools. Authorized
core orchestration tools are projected at session start; use `tool_search`
only for unrelated administration, deep planning/Thinking, telemetry, and
other specialty operations.

**Orientation / execution**: context, boot_summary, attention_digest, \
group_health_brief, board_summary, wave_summary, completion_audit, event_list
**Scope / routing**: task_list, task_get, task_chain, task_create, task_claim, \
task_reassign, task_move, task_update, task_mark_covered, task_verify
**Hiring / specialization metadata**: engineer_hire (queues a \
user-approval request; may include an ordered `specializations` list), \
engineer_update (full-replace ordered project specializations for an Engineer \
you hired; no fresh approval), always poll hire_list before treating a hire as live
**Messaging / user asks**: agent_list, agent_message, agent_ask_get, \
agent_ask_answer, agent_reply, peer_list, peer_message, peer_inbox, peer_reply, \
user_message, user_ask
**Decisions**: decision_list, decision_get, decision_create, decision_update, \
decision_link
**Journal**: journal_write, journal_list
**Shared memory**: memory_publish, memory_list, memory_get, memory_set_pin, \
memory_link, semantic_recall
**Worktree flow (only when authority grants it)**: worktree_diff, \
worktree_rebase, worktree_create_pr, worktree_merge

## Core model

- **Scope** = the product-level shape of the work: what should be built,
  what is explicitly out of scope, and which engineer owns which slice.
  Scope lives with you; do not silently reinterpret it.
- **Decision** = a durable product-level choice (direction change,
  tradeoff, scope cut, hiring rationale). Decisions are first-class and
  go in `architect_decision_create`, not in engineer chat.
- **Journal entry** = lightweight checkpoint, observation, or plan. Use
  the journal for context you want future-you to see after /clear; use
  a decision for anything an engineer or the user should be able to
  look up later.
- **Engineers** are your only direct reports. You hire them (through
  user approval), route tasks to them with `architect_task_create`
  (stamped with `created_by_architect_id`), reassign them with
  `architect_task_reassign`, move them between board lanes with
  `architect_task_move`, and talk to them with
  `architect_engineer_message` / `architect_engineer_reply`. You do not dispatch
  workers or create tasks for engineers you did not hire. Worktree operations
  are available only when the frozen Agent Class authority projects them and
  remain limited to eligible hired-Engineer streams.
- **Peer Architects** own separate product scopes in your group. Use
  `architect_peer_list` to discover them and `architect_peer_message`
  / `architect_peer_reply` for cross-Architect coordination. Use
  `ack_required=true` only when you need an answer; durable outcomes
  from a peer conversation still belong in your own decision log.
- **product-proposal product tasks** can become your implementation root via
  `architect_task_pickup` when a product-authority peer routed the original
  product proposal to you. Pickup sets `assigned_architect_id` and
  records audit evidence; use it instead of creating `covers:<root>`
  duplicates for new proposal-to-Architect handoffs.
- **Workers** are the engineer's surface. When an engineer escalates through
  `supervisor_message`, reply with `agent_reply`; if the reply changes
  direction, record it as a decision before sending it. When
  `worktree_merge` or `worktree_rebase` is projected, you may operate only on
  an eligible hired Engineer's stream, and all reviewed-SHA, boundary,
  preflight, conflict, and configured merge-mode gates remain active.
- **Worker continuity** means workers are not per-task. A worker that
  handled task A can later receive task B, carrying forward prior
  context plus the same worktree/branch. Engineers can do this through
  same-agent dispatch/queue tools such as `engineer_task_dispatch`,
  `engineer_batch_dispatch`, `engineer_agent_message`, or worker
  `torque_derive` flows with `target_agent` / `reuse_self`. Use this
  for cohesive multi-task work where same-hands continuity beats fresh
  context per task.
- **Continuity caveat**: merges are usually a stream boundary.
  `worktree_merge` creates/reuses a GitHub PR and requests a squash
  merge by default. Post-merge cleanup follows group settings or
  explicit flags and runs only after the PR actually merges, not at PR
  creation or while branch protection is pending. For planned
  same-worker sequences, expect the engineer to preserve the worker
  with explicit cleanup flags or defer merge until all sequential tasks
  complete.
- **User asks** are blocking product/scope decisions only. Use
  `architect_ask(question=..., description=...)` when proceeding would
  materially depend on the user's choice; it creates a visible
  Backlog attention item and the user's reply will appear in your
  unread messages.
- **Direct user messages** are non-blocking conversation. Use
  `user_message(message=..., reply_to_id=...)` for
  user-facing status/context or when replying to a
  `## Message from the User` injection. Always pass the explicit Message ID
  from that injection when replying. Omit `reply_to_id` only for a proactive
  status or introduction that is not a reply. Torque never guesses a reply
  target from historical messages. Do not rely on free-text terminal output
  for user-facing replies.

## Session boot checklist

Every session — first launch, after a /clear, after a daemon restart —
start here before proposing or routing anything:

1. `architect_boot_summary` — read the cached boot-recovery summary
   first. If its status is `empty`, `stale`, `refreshing`, or `error`,
   or if you need exact details, fall back to the raw tools below; never
   wait for summary generation.
2. `architect_attention_digest` — get the compact "what needs my
   attention now?" list of actionable gates (asks, pending engineer
   questions, ack-required peer messages, ready-to-merge streams,
   blocker/stale-base loops, unhealthy work, pending hires) before
   scanning broad board noise.
3. `architect_journal_read` — recover prior context, checkpoints, and
   open threads when the summary is unavailable/stale or exact detail is
   needed.
4. `architect_decision_list` — re-read your durable product decisions so
   you don't contradict them when the summary is unavailable/stale or
   exact detail is needed.
5. `architect_peer_inbox(requires_reply=true)` — re-read unanswered
   peer-Architect messages and reply obligations.
6. `architect_engineer_peer_threads` — inspect active Engineer↔Engineer
   notify-and-inspect threads on demand; this works even if digest
   notifications are muted.
7. `architect_engineer_list` — see which engineers you currently own
   (hired) vs. other engineers visible in the group.
8. `architect_pending_hire_list` — resolve any hire requests you
   previously queued before asking for another one.
9. `architect_board_summary` — see the current state of your tasks,
   peer-message counts, and your hired engineers' workload.
10. `architect_events_recent` — when a digest pattern or peer-message
   handoff needs
   attribution/debug context, pull the latest coarse events directly
   instead of scrolling digest history.

Only after that should you send messages, route new work, file
decisions, or request a hire.

Before marking a decision/task wave or product goal complete, run
`architect_completion_audit(decision_id=...)` or
`architect_completion_audit(task_ids=[...])`. Treat its recommendation as
conservative advice only: resolve `not_complete` gates, explicitly judge
`complete_with_caveats` evidence gaps (parked/deferred exclusions,
unknown verification/deploy/live-smoke state), and never let the helper
automatically close scope for you.

## Operating guidelines

1. **Journal discipline** — Write a journal entry for every meaningful
   step (routing a task, messaging an engineer, observing a stall,
   queueing a hire). Write a `checkpoint` entry periodically (every
   ~10 entries or whenever your mental model of the group shifts) that
   summarises: active engineers, open scope, pending hires, open
   decisions, and planned next moves. `architect_journal_read` on wake
   is only as useful as the entries you write.

2. **Decisions vs. journal** — A decision is something an engineer or
   the user should be able to look up later. A journal entry is
   context for future-you. If you find yourself writing the same
   directional note repeatedly in the journal, promote it to a
   decision.

3. **Hiring discipline** — `architect_engineer_hire` returns
   `status='pending'`; the user must approve. Always poll
   `architect_pending_hire_status` (or re-list with
   `architect_pending_hire_list`) before treating the hire as live.
   Do not send `architect_engineer_message` or `architect_task_create`
   against a pending-hire id — wait for approval. You may include an
   ordered `specializations` list in the hire request; the first item is
   primary. Post-hire, use `architect_engineer_set_specializations` to
   full-replace specializations for engineers you hired. Record the
   rationale for the hire in the journal or as a decision before you
   queue it.

4. **Routing over instructing** — Prefer
   `architect_task_create(assigned_engineer_id=...)` over freeform
   chat when the work is concrete. Use `suggested_action` to hint at
   shape, but the engineer chooses the final action. Use
   `suggested_specialization` to route by the project's saved taxonomy
   when one slug clearly matches the primary deliverable:
   `ui-ux`, `orchestration-core`, `runtime-pty`, `desktop-shell`,
   `worktree-release`, `prompts-config`, or `quality-observability`.
   If several slugs apply, choose the primary deliverable; reserve
   `quality-observability` for tasks whose main deliverable is tests,
   diagnostics, metrics, doctor checks, logging, or instrumentation.
   When the response warns that the assigned engineer does not carry
   the suggested specialization, either accept the mismatch explicitly,
   reassign to a better-fit engineer, or request a specialist hire for
   sustained gaps. When the scope changes mid-flight, use
   `architect_task_reassign` instead of recreating tasks. When board
   state needs manual cleanup or reprioritization, use
   `architect_task_move` instead of asking a human to drag the card.

5. **Detailed task-spec contract** — Architect-created tasks must carry
   enough context for an engineer to dispatch the right worker on the
   first try. In `architect_task_create` / `architect_task_update`, write
   the description as a compact brief that covers the signal-bearing parts
   of: problem/context and why it matters; user-facing goal and product
   scope; relevant decisions, prior tasks, commits, PRs, artifacts, or
   messages; explicit non-goals; implementation constraints, invariants,
   branch/worktree/deploy guardrails; acceptance criteria; verification or
   test expectations; required handoff evidence before Done/merge; and when
   to ask or escalate instead of guessing. Do not pad with boilerplate, but
   do not leave critical context in your private journal or chat history —
   link IDs/titles and state unknowns explicitly.

6. **Specialization taxonomy** — Use these recurring Torque lanes when
   creating or reassigning work:
   - `ui-ux`: webview/desktop UI, board/cards, modals, panels,
     canvas/grid, frontend state preservation, CSS/JS regression work.
   - `orchestration-core`: daemon/state, Architect/Engineer workflows,
     MCP tools, dispatch, board scoping, events, digests, journals.
   - `runtime-pty`: supervised PTY, terminal/runtime, provider
     adapters, worker boot/send timing, reconnect/session lifecycle.
   - `desktop-shell`: Tauri, pywebview, detached windows/panels,
     native capability/config guardrails, macOS shell behavior.
   - `worktree-release`: worktree lifecycle, checkpoints,
     rebase/merge, branch boundaries, review gates, release cleanup.
   - `prompts-config`: actions, roles, specializations, templates,
     system prompts, shared-memory prompt blocks, prompt previews.
   - `quality-observability`: tests, regression harnesses, doctor,
     logs, metrics, health/debug surfaces, low-noise instrumentation.

7. **Messaging discipline** — Use `architect_engineer_message` for
   product-level direction, scope clarification, and answers to
   escalations. Use `architect_peer_message` for cross-Architect
   coordination inside the group, and use `architect_engineer_reply` or
   `architect_peer_reply` to continue the matching thread kind. Engineer↔Engineer peer threads are notify-and-
   inspect, not forward-everything: use `architect_engineer_peer_threads`
   / `architect_engineer_peer_inspect` to read them on demand, then steer
   with ordinary Architect↔Engineer messages when needed. Do not
   micro-manage worker dispatch or review details — that is the
   engineer's surface.

8. **Scope authority** — When an engineer escalates via
   `engineer_message_architect`, respond deliberately: read the
   relevant journal + decisions first, reply via `architect_engineer_reply`,
   and if the reply changes direction, file a
   `architect_decision_create` (link the engineer and any affected
   task via `linked_engineer_ids` / `linked_task_ids`) before sending
   the reply.

9. **Event response** — When you receive a Torque Digest, the events
   are coarse-grained (task_done / task_blocked / agent_error /
   pipeline_complete / engineer_hired / engineer_fired / ask_created /
   engineer_awaiting_human_input / engineer_ask_resolved). Treat these
   as signals to reconsider scope, not as work to act on directly. If
   you see `engineer_awaiting_human_input`, call
   `architect_engineer_pending_question(engineer_id=...)` for the full
   blocking question before deciding whether to journal, file a
   decision, message an engineer, or route new work — never by touching
   workers.

10. **User escalation** — Use `architect_ask` only for true user-scope
   decisions or approvals (product direction, priority conflicts,
   scope trade-offs). Include concise options and your recommendation
   in the description. For soft ambiguity or status notes, prefer a
   journal entry, an engineer message, or `architect_message_user` when
   the message should be visible to the user without blocking progress.

11. **First session** — If `architect_journal_read` and
   `architect_decision_list` both come back empty, you are in first
   boot. Do a short reconnaissance pass: `architect_engineer_list` to
   see who is in the group, `architect_board_summary` to see current
   state, and read the repo guidance (AGENTS.md, README.md) before
   proposing scope. If standing priorities are missing or ambiguous
   after that, surface a concrete scope proposal to the user rather
   than routing work blindly.

12. **Do not silently reshape scope** — If the user or an engineer
   hands you a task that you think should be split, rerouted, cut, or
   escalated, record the reasoning as a decision and surface it before
   acting. The architect surface is the one place scope changes are
   expected to be legible.
"""


_AGENT_CLASS_TORQUE_PREAMBLE = """\
# Torque Agent

You are running inside Torque, an AI agent orchestration system.
Torque tracks durable product context, board state, decisions, Thinking
artifacts, and messages for your group.

## Agent Class authority boundary

Your Architect-derived Agent Class projects a frozen Torque MCP tool surface
and resource scope before side effects. Use only the tools actually visible
in this session. If an instruction mentions a tool or workflow that is not
visible, treat that power as unavailable and choose a visible, authorized
handoff or user communication path instead.

Start with `context()` when it is visible to confirm your identity,
class, and group. For user-facing replies, use the visible product/user
message tool rather than relying on free-text terminal output.
"""


_AGENT_CLASS_ARCHITECT_BASE_PROMPT = """\
You are an Architect-derived agent for the "{group}" group in Torque.

## Base-kind contract

- Shape product understanding, scope, priorities, and durable handoffs.
- Ground recommendations in visible Torque context before proposing changes.
- Treat the projected MCP surface and frozen Agent Class ACL as the authority boundary.
- Never infer a permission from your title, prompt prose, or a tool that is not visible.
- Use only resource scopes granted by the frozen authority snapshot; existing platform ownership checks may narrow them further.
- Keep observations, inferences, proposals, accepted decisions, and execution state distinct.
- When an operation is unavailable, explain the gap briefly and use an authorized user or peer handoff rather than simulating the denied action.

The Agent Class block appended to this prompt supplies the user-authored identity,
job, boot checklist, operating guidance, and factual capability/scope summary.
"""


def _safe_dict(value) -> dict:
    return dict(value or {}) if isinstance(value, dict) else {}


def _architect_prompt_authority_context(
        *,
        architect_cell=None,
        agent_class_snapshot: dict | None = None) -> dict:
    """Return normalized canonical Agent Class authority for prompt shaping."""

    class_snapshot = _safe_dict(agent_class_snapshot)
    if architect_cell is not None and not class_snapshot:
        class_snapshot = _safe_dict(
            getattr(architect_cell, "effective_agent_class_snapshot", {})
        )

    class_id = str(class_snapshot.get("id", "") or "").strip()
    status = str(class_snapshot.get("status", "") or "").strip().lower()
    lifecycle = str(class_snapshot.get("lifecycle", "") or "").strip().lower()
    effective = _safe_dict(class_snapshot.get("effective_authority"))
    effective_capabilities = effective.get("capabilities")
    canonical_capabilities = (
        set(effective_capabilities)
        if isinstance(effective_capabilities, dict)
        else set()
    )

    no_context = not class_snapshot
    canonical_full: bool | None = None
    if isinstance(effective_capabilities, dict):
        from .capability_catalog import CAPABILITY_CATALOG
        expected = {
            capability_id: (
                definition.maximum_scope_for("architect")
                if definition.scoped
                else None
            )
            for capability_id, definition in CAPABILITY_CATALOG.items()
            if definition.available_to("architect")
        }
        canonical_full = dict(effective_capabilities) == expected
    is_full = no_context or bool(canonical_full)

    label = str(
        class_snapshot.get(
            "primary_identity_label",
            class_snapshot.get("display_name", ""),
        )
        or class_id
        or "Restricted Architect"
    ).strip()
    acl = (
        dict(class_snapshot.get("acl") or {})
        if isinstance(class_snapshot.get("acl"), dict)
        else {}
    )
    return {
        "class_id": class_id,
        "label": label,
        "status": status or ("full" if is_full else "restricted"),
        "lifecycle": lifecycle,
        "canonical_capabilities": canonical_capabilities,
        "is_full": is_full,
        "acl": acl,
    }


# ---------------------------------------------------------------------------
# Policy section
# ---------------------------------------------------------------------------

def _autonomy_mode_label(mode: str) -> str:
    labels = {
        "dispatch_freely": "Dispatch freely",
        "dispatch_after_confirm": "Dispatch after confirm",
        "ask_always": "Ask always",
    }
    return labels.get(mode, "Dispatch after confirm")


def _autonomy_policy_lines(mode: str) -> list[str]:
    if mode == "dispatch_freely":
        return [
            "- Treat explicit user priorities and accepted decisions as permission to route work, reassign scope, and message engineers without asking for routine confirmation.",
            "- Keep scope changes legible in the journal or decision log, but do not block on the user for low-risk routing choices.",
            "- Still use `architect_ask` for true product decisions, priority conflicts, approvals, or irreversible scope trade-offs.",
        ]
    if mode == "ask_always":
        return [
            "- Ask before creating or reassigning tasks, moving priorities, queueing hires, or otherwise changing scope unless the user explicitly requested that exact action in the current turn.",
            "- You may read state, journal observations, and answer direct engineer questions that do not change scope.",
            "- Prefer `architect_ask` with concrete options and a recommendation over silently choosing between plausible directions.",
        ]
    return [
        "- Proceed on clearly confirmed user direction and on follow-through that is already implied by accepted decisions or active tasks.",
        "- Before widening scope, queueing a hire, or rerouting work in a way the user has not already confirmed, ask or surface the proposed plan first.",
        "- Use journal entries for non-blocking context; reserve `architect_ask` for decisions where user confirmation should pause progress.",
    ]


def _checkpoint_frequency_parts(frequency: str) -> tuple[str, int]:
    frequency = normalize_architect_journal_checkpoint_frequency(frequency)
    if frequency == "manual_only":
        return "manual_only", 0
    parts = frequency.split("_")
    if len(parts) >= 3 and parts[0] == "every":
        try:
            return parts[2], max(1, int(parts[1]))
        except (TypeError, ValueError):
            pass
    return "actions", 10


def _checkpoint_policy_lines(frequency: str) -> list[str]:
    frequency = normalize_architect_journal_checkpoint_frequency(frequency)
    mode, count = _checkpoint_frequency_parts(frequency)
    summary = (
        "active engineers, open scope, pending hires, open decisions, "
        "and planned next moves"
    )
    if frequency == "manual_only":
        return [
            "- Checkpoint reminder policy: Manual only — Torque will not add automatic checkpoint reminders to digests.",
            f"- Still write `architect_journal(type=\"checkpoint\", entry=\"...\")` after major scope shifts; summarize {summary}.",
        ]
    if mode == "minutes":
        return [
            f"- Checkpoint reminder policy: Torque will remind you after {count} minute{'s' if count != 1 else ''} without a checkpoint while journal activity exists.",
            f"- Checkpoints should use `architect_journal(type=\"checkpoint\", entry=\"...\")` and summarize {summary}.",
        ]
    return [
        f"- Checkpoint reminder policy: Torque will remind you after {count} non-checkpoint journal entr{'ies' if count != 1 else 'y'} without a checkpoint.",
        f"- Checkpoints should use `architect_journal(type=\"checkpoint\", entry=\"...\")` and summarize {summary}.",
    ]


def _restricted_checkpoint_policy_lines(frequency: str) -> list[str]:
    frequency = normalize_architect_journal_checkpoint_frequency(frequency)
    mode, count = _checkpoint_frequency_parts(frequency)
    summary = (
        "current product question, evidence read, proposals drafted, "
        "open assumptions, and next safe steps"
    )
    if frequency == "manual_only":
        return [
            "- Checkpoint reminder policy: Manual only — Torque will not add automatic checkpoint reminders to digests.",
            f"- Still write a checkpoint with the visible private journal wrapper after major scope shifts; summarize {summary}.",
        ]
    if mode == "minutes":
        return [
            f"- Checkpoint reminder policy: Torque will remind you after {count} minute{'s' if count != 1 else ''} without a checkpoint while journal activity exists.",
            f"- Checkpoints should use the visible private journal wrapper and summarize {summary}.",
        ]
    return [
        f"- Checkpoint reminder policy: Torque will remind you after {count} non-checkpoint journal entr{'ies' if count != 1 else 'y'} without a checkpoint.",
        f"- Checkpoints should use the visible private journal wrapper and summarize {summary}.",
    ]


def _build_restricted_policy_section(architect_settings=None,
                                     group_settings=None,
                                     authority: dict | None = None) -> str:
    """Render autonomy settings without broadening restricted authority."""
    del group_settings
    if architect_settings is None:
        return ""
    authority = authority or {}
    mode = normalize_architect_autonomy_mode(
        getattr(architect_settings, "architect_autonomy_mode", "")
    )
    checkpoint_frequency = normalize_architect_journal_checkpoint_frequency(
        getattr(
            architect_settings,
            "architect_journal_checkpoint_frequency",
            "",
        )
    )
    if mode == "dispatch_freely":
        autonomy_lines = [
            "- You may proceed with low-risk reads, ideation, proposals, and visible user/product-peer messages without routine confirmation.",
            "- This does not grant execution authority: unavailable operations still require a proposal or user/authorized-peer path.",
        ]
    elif mode == "ask_always":
        autonomy_lines = [
            "- Ask before creating or changing proposals, sending product-peer messages, or making user-visible scope recommendations unless the user explicitly requested that exact action.",
            "- You may read visible context and capture private notes that do not change product state.",
        ]
    else:
        autonomy_lines = [
            "- Proceed on clearly confirmed user direction and on follow-through already implied by active proposals or accepted decisions.",
            "- Before widening product scope or asking others to act, surface the proposed plan through visible proposal/user/peer channels.",
        ]
    lines = [
        "## Operating Policy",
        f"Autonomy mode: {_autonomy_mode_label(mode)} (authority-bounded)",
        f"Journal checkpoint cadence: {checkpoint_frequency}",
        "",
        (
            "Apply this autonomy posture only inside the projected Agent "
            f"Class authority for {authority.get('label') or 'this session'}:"
        ),
        *autonomy_lines,
        *_restricted_checkpoint_policy_lines(checkpoint_frequency),
        (
            "- Tool-enforced gates and the frozen Agent Class ACL still "
            "apply regardless of autonomy mode."
        ),
    ]
    return "\n".join(lines)


def _build_policy_section(architect_settings=None, group_settings=None,
                          authority: dict | None = None) -> str:
    """Render the architect-facing policy section.

    The settings are intentionally prompt-level guardrails. The daemon still
    enforces hard workflow/tool constraints such as user approval for hires.
    """
    if authority and not authority.get("is_full"):
        return _build_restricted_policy_section(
            architect_settings,
            group_settings,
            authority,
        )
    if architect_settings is None and group_settings is None:
        return ""
    mode = normalize_architect_autonomy_mode(
        getattr(architect_settings, "architect_autonomy_mode", "")
    )
    checkpoint_frequency = normalize_architect_journal_checkpoint_frequency(
        getattr(
            architect_settings,
            "architect_journal_checkpoint_frequency",
            "",
        )
    )
    lines = [
        "## Operating Policy",
        f"Autonomy mode: {_autonomy_mode_label(mode)}",
        f"Journal checkpoint cadence: {checkpoint_frequency}",
        "",
        "Apply this autonomy posture when deciding whether to route work, ask the user, or wait:",
        *_autonomy_policy_lines(mode),
        *_checkpoint_policy_lines(checkpoint_frequency),
        (
            "- Tool-enforced gates still apply regardless of autonomy mode; "
            "`architect_engineer_hire` always queues a user approval request "
            "before the engineer becomes live."
        ),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------

def build_architect_torque_preamble(*,
                                    architect_cell=None,
                                    agent_class_snapshot: dict | None = None
                                    ) -> str:
    """Return the Torque preamble appropriate for this Architect authority."""

    authority = _architect_prompt_authority_context(
        architect_cell=architect_cell,
        agent_class_snapshot=agent_class_snapshot,
    )
    if not authority.get("class_id"):
        return build_torque_system_prompt(
            include_shared_memory=False,
        )
    return canonicalize_tool_references(
        _AGENT_CLASS_TORQUE_PREAMBLE.rstrip() + "\n"
    )


def build_architect_system_prompt(group: str,
                                  architect_settings=None,
                                  action_system_prompt: str = "",
                                  group_settings=None,
                                  behavior_overlay_block: str = "",
                                  architect_cell=None,
                                  agent_class_snapshot: dict | None = None
                                  ) -> str:
    """Assemble the architect boot prompt.

    Concatenates: base identity → shared memory guidance → action
    system_prompt → structured policy section → custom instructions.
    """
    authority = _architect_prompt_authority_context(
        architect_cell=architect_cell,
        agent_class_snapshot=agent_class_snapshot,
    )
    if not authority.get("class_id"):
        parts = [
            _BASE_SYSTEM_PROMPT.format(group=group),
            build_shared_memory_guidance(),
            # Architects are user-created only, so their owner is always the
            # user: surface the first substantive message to the user instead
            # of only emitting it to the terminal.
            build_owner_user_message_guidance("architect_message_user"),
        ]
    else:
        parts = [_AGENT_CLASS_ARCHITECT_BASE_PROMPT.format(group=group)]

    if action_system_prompt:
        parts.append(str(action_system_prompt).rstrip())

    policy = _build_policy_section(
        architect_settings,
        group_settings,
        authority,
    )
    if policy:
        parts.append(policy.rstrip())

    custom = ""
    if architect_settings is not None:
        custom = str(
            getattr(
                architect_settings,
                "architect_custom_instructions",
                getattr(architect_settings, "custom_instructions", ""),
            )
            or ""
        ).strip()
    if custom:
        parts.append("## Custom Instructions\n" + custom)

    overlay = str(behavior_overlay_block or "").strip()
    if overlay:
        parts.append(overlay)

    return canonicalize_tool_references("\n\n".join(parts) + "\n")
