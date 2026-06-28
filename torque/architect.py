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
from .agent_profiles import (
    BASE_KIND_CEILINGS,
    TOOL_CATEGORY_REQUIREMENTS,
)
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

You have access to the architect_* MCP tool surface. Load it at session
start and use these tools instead of freeform instructions to
engineers.

**Read**: architect_attention_digest, architect_completion_audit, \
architect_board_summary, architect_events_recent, architect_deploy_state, \
architect_task_list, architect_task_show, architect_task_chain, \
architect_engineer_list, \
architect_pending_hire_list, architect_pending_hire_status, \
architect_boot_summary, architect_decision_list, architect_journal_read, \
architect_engineer_journal_read, architect_engineer_pending_question, \
architect_peer_list, architect_peer_inbox
**Scope / routing**: architect_task_create, architect_task_reassign, \
architect_task_move, architect_task_update, architect_task_mark_covered
**Hiring / specialization metadata**: architect_engineer_hire (queues a \
user-approval request; may include an ordered `specializations` list), \
architect_engineer_set_specializations (full-replace ordered project \
specializations for an engineer you hired; no fresh approval), always \
poll architect_pending_hire_status before treating a hire as live
**Messaging / user asks**: architect_engineer_message, \
architect_peer_message, architect_reply, architect_message_user, \
architect_ask
**Decisions**: architect_decision_create, architect_decision_update, \
architect_decision_link
**Journal**: architect_journal, architect_journal_read

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
  `architect_engineer_message` / `architect_reply`. You do not dispatch
  workers, touch worktrees, or create tasks for engineers you did not
  hire.
- **Peer Architects** own separate product scopes in your group. Use
  `architect_peer_list` to discover them and `architect_peer_message`
  / `architect_reply` for cross-Architect coordination. Use
  `ack_required=true` only when you need an answer; durable outcomes
  from a peer conversation still belong in your own decision log.
- **Workers and worktrees** are the engineer's surface. When an
  engineer escalates via `engineer_message_architect`, reply with
  `architect_reply`; if the reply changes direction, record it as a
  decision before sending it.
- **Worker continuity** means workers are not per-task. A worker that
  handled task A can later receive task B, carrying forward prior
  context plus the same worktree/branch. Engineers can do this through
  same-agent dispatch/queue tools such as `engineer_task_dispatch`,
  `engineer_batch_dispatch`, `engineer_agent_message`, or worker
  `torque_derive` flows with `target_agent` / `reuse_self`. Use this
  for cohesive multi-task work where same-hands continuity beats fresh
  context per task.
- **Continuity caveat**: merges are usually a stream boundary.
  `engineer_merge` creates/reuses a GitHub PR and requests a squash
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
  `architect_message_user(message=..., reply_to_id=...)` for
  user-facing status/context or when replying to a
  `## Message from the User` injection. Do not rely on free-text
  terminal output for user-facing replies.

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
   coordination inside the group, and use `architect_reply` to continue
   either kind of thread. Engineer↔Engineer peer threads are notify-and-
   inspect, not forward-everything: use `architect_engineer_peer_threads`
   / `architect_engineer_peer_inspect` to read them on demand, then steer
   with ordinary Architect↔Engineer messages when needed. Do not
   micro-manage worker dispatch or review details — that is the
   engineer's surface.

8. **Scope authority** — When an engineer escalates via
   `engineer_message_architect`, respond deliberately: read the
   relevant journal + decisions first, reply via `architect_reply`,
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


_RESTRICTED_TORQUE_PREAMBLE = """\
# Torque Agent

You are running inside Torque, an AI agent orchestration system.
Torque tracks durable product context, board state, decisions, Thinking
artifacts, and messages for your group.

## Restricted Agent Class boundary

Your Architect-derived Agent Class/Profile projects a narrower MCP tool
surface before side effects. Use only the tools actually visible in this
session. If an instruction mentions a tool or workflow that is not visible,
treat that power as unavailable and use a proposal, product-peer message,
or user ask/message instead.

Start with `torque_context()` when it is visible to confirm your identity,
class, and group. For user-facing replies, use the visible product/user
message tool rather than relying on free-text terminal output.
"""


def _safe_dict(value) -> dict:
    return dict(value or {}) if isinstance(value, dict) else {}


def _metadata_from_context(class_snapshot: dict, profile_snapshot: dict) -> dict:
    metadata = _safe_dict(class_snapshot.get("metadata"))
    profile_metadata = _safe_dict(profile_snapshot.get("metadata"))
    for key, value in profile_metadata.items():
        metadata.setdefault(key, value)
    generated_by = _safe_dict(profile_metadata.get("generated_by_agent_class"))
    if generated_by:
        metadata.setdefault("generated_by_agent_class", generated_by)
    return metadata


def _profile_capability_atoms(profile_snapshot: dict) -> set[str]:
    grants = profile_snapshot.get("grants")
    if isinstance(grants, list):
        return {
            str(atom or "").strip()
            for atom in grants
            if str(atom or "").strip()
        }
    atoms: set[str] = set()
    capabilities = profile_snapshot.get("capabilities")
    if isinstance(capabilities, list):
        for item in capabilities:
            if isinstance(item, dict):
                atom = str(item.get("atom", "") or "").strip()
            else:
                atom = str(item or "").strip()
            if atom:
                atoms.add(atom)
    return atoms


def _category_status_map(profile_snapshot: dict, class_snapshot: dict) -> dict[str, str]:
    categories: dict[str, str] = {}
    for source in (profile_snapshot, _safe_dict(class_snapshot.get("internal_policy"))):
        for item in list(source.get("projected_tool_categories", []) or []):
            if not isinstance(item, dict):
                continue
            category = str(item.get("category", "") or "").strip()
            status = str(item.get("status", "") or "").strip()
            if category and status:
                categories[category] = status
    return categories


def _architect_prompt_authority_context(
        *,
        architect_cell=None,
        agent_class_snapshot: dict | None = None,
        agent_profile_snapshot: dict | None = None) -> dict:
    """Return normalized capability/authority facts for prompt shaping.

    Missing context intentionally means "legacy/full Architect" so existing
    callers that do not launch through Agent Classes preserve byte-for-byte
    full Architect guidance.
    """

    class_snapshot = _safe_dict(agent_class_snapshot)
    profile_snapshot = _safe_dict(agent_profile_snapshot)
    if architect_cell is not None:
        if not class_snapshot:
            class_snapshot = _safe_dict(
                getattr(architect_cell, "effective_agent_class_snapshot", {})
            )
        if not profile_snapshot:
            profile_snapshot = _safe_dict(
                getattr(architect_cell, "effective_agent_profile_snapshot", {})
            )
    if not profile_snapshot:
        nested = class_snapshot.get("agent_profile")
        if isinstance(nested, dict):
            profile_snapshot = _safe_dict(nested)

    class_id = str(class_snapshot.get("id", "") or "").strip()
    profile_id = str(profile_snapshot.get("id", "") or "").strip()
    profile_base_kind = str(
        profile_snapshot.get("base_kind", class_snapshot.get("base_kind", ""))
        or ""
    ).strip()
    status = str(
        profile_snapshot.get("status", class_snapshot.get("status", ""))
        or ""
    ).strip().lower()
    lifecycle = str(
        class_snapshot.get("lifecycle", profile_snapshot.get("lifecycle", ""))
        or ""
    ).strip().lower()
    grants = _profile_capability_atoms(profile_snapshot)
    categories = _category_status_map(profile_snapshot, class_snapshot)

    # No effective profile/class context is the historical full Architect path.
    no_context = not class_snapshot and not profile_snapshot
    architect_ceiling = BASE_KIND_CEILINGS.get("architect", frozenset())
    full_by_grants = bool(grants) and grants == set(architect_ceiling)
    full_by_status = status == "full" and lifecycle in {"", "stable"}
    is_full = no_context or (
        profile_base_kind == "architect"
        and (full_by_grants or (full_by_status and not grants))
    )

    metadata = _metadata_from_context(class_snapshot, profile_snapshot)
    generated_by = _safe_dict(metadata.get("generated_by_agent_class"))
    archetype = str(metadata.get("archetype", "") or "").strip()
    source_class_id = str(generated_by.get("id", "") or "").strip()
    label = str(
        class_snapshot.get(
            "primary_identity_label",
            class_snapshot.get("display_name", ""),
        )
        or profile_snapshot.get("display_name", "")
        or class_id
        or "Restricted Architect"
    ).strip()

    is_product_manager = (
        class_id == "product-manager"
        or source_class_id == "product-manager"
        or archetype == "product_manager"
        or profile_id == "product-manager-draft"
        or "product-manager" in profile_id
    )
    is_creative = (
        class_id == "creative-architect"
        or source_class_id == "creative-architect"
        or archetype == "creative_architect"
        or "creative-architect" in profile_id
    )
    is_torque_steward = (
        class_id == "torque-steward"
        or source_class_id == "torque-steward"
        or archetype == "torque_steward"
        or "torque-steward" in profile_id
    )
    if is_creative and label == "Creative":
        # Normal UI shortens the class badge to "Creative"; keep the runtime
        # prompt language stable because this helper is used for authority and
        # behavior instructions, not for normal display surfaces.
        label = "Creative Architect"

    return {
        "class_id": class_id,
        "profile_id": profile_id,
        "label": label,
        "status": status or ("full" if is_full else "restricted"),
        "lifecycle": lifecycle,
        "grants": grants,
        "categories": categories,
        "is_full": is_full,
        "is_product_manager": is_product_manager,
        "is_creative": is_creative,
        "is_torque_steward": is_torque_steward,
    }


def _has_capability(authority: dict, atom: str) -> bool:
    return atom in set(authority.get("grants") or set())


def _has_capabilities(authority: dict, *atoms: str) -> bool:
    grants = set(authority.get("grants") or set())
    return set(atoms).issubset(grants)


def _allows_category(authority: dict, category: str) -> bool:
    if authority.get("is_full"):
        return True
    categories = dict(authority.get("categories") or {})
    status = categories.get(category)
    if status:
        return status == "allowed"
    required = TOOL_CATEGORY_REQUIREMENTS.get(category)
    if not required:
        return False
    grants = set(authority.get("grants") or set())
    return set(required).issubset(grants)


def _restricted_identity_sentence(authority: dict) -> str:
    label = authority.get("label") or "Restricted Architect"
    if authority.get("is_creative"):
        return (
            f"You are the {label} for the \"{{group}}\" group in Torque: "
            "an ideation and product-discovery partner, not an execution "
            "authority or a source of accepted plans."
        )
    if authority.get("is_product_manager"):
        return (
            f"You are the {label} for the \"{{group}}\" group in Torque: "
            "a PM-safe planning and intake agent with proposal-only product "
            "authority."
        )
    if authority.get("is_torque_steward"):
        return (
            f"You are the {label} for the \"{{group}}\" group in Torque: "
            "a conservative operational steward that represents the user's "
            "wishes by observing, explaining, and recommending safe next "
            "steps without autonomous mutation."
        )
    return (
        f"You are the {label} for the \"{{group}}\" group in Torque: an "
        "Architect-derived agent with a restricted, projected tool surface."
    )


def _restricted_tool_lines(authority: dict) -> list[str]:
    lines = [
        "- Use only MCP tools visible in this session; profile projection is the source of truth.",
    ]
    if authority.get("is_torque_steward"):
        lines.append(
            "- Context: use `torque_context()` when visible, then projected board/task, event, MCP telemetry, Area, Initiative, and Decision reads for operational health checks."
        )
        if _has_capabilities(
                authority,
                "observe.board_summary",
                "observe.task_detail",
                "observe.events",
                "observe.mcp_calls",
                "planning.area_read",
                "planning.initiative_read",
                "decision.list",
        ):
            lines.append(
                "- Operating brief: when `architect_steward_operating_brief` is visible, use it as the deterministic read-only starting point for onboarding, anomaly reports, and responsible-actor suggestions."
            )
    else:
        lines.append(
            "- Context: use `torque_context()` when visible, then product/context reads that are projected for your class."
        )
    if _allows_category(authority, "planning_reads"):
        if authority.get("is_torque_steward"):
            lines.append(
                "- Operational reads: use visible board/task, Area, Initiative, Decision, recent-event, and telemetry read tools only to summarize health, stuck work, missed handoffs, and cleanup candidates."
            )
        else:
            lines.append(
                "- Product reads: use visible board/task, Area, Initiative, and Decision read tools; Product/Creative classes should prefer `architect_product_*` reads when present."
            )
    if _allows_category(authority, "thinking_reads") or _allows_category(authority, "thinking_writes"):
        lines.append(
            "- Thinking workspace: use `architect_thinking_scratchpad_*` and `architect_thinking_mind_map_*`; create/update only caller-owned Thinking artifacts."
        )
    if _allows_category(authority, "idea_briefs"):
        lines.append(
            "- Idea Briefs: use `architect_product_idea_brief_*` to draft/refine/park structured proposal artifacts linked to Thinking references; proposing a brief never dispatches or assigns work."
        )
    if _allows_category(authority, "pm_queued_tasks"):
        lines.append(
            "- Task proposals: use `architect_product_task_propose` for queued, unassigned, non-dispatched product task proposals."
        )
    if _allows_category(authority, "pm_decisions"):
        lines.append(
            "- Decision proposals: use `architect_product_decision_create`, `architect_product_decision_update`, and `architect_product_decision_link` for proposed decisions only."
        )
    if _allows_category(authority, "peer_architect_comm"):
        lines.append(
            "- Product-peer discussion: use visible peer tools; Product/Creative classes should use `architect_product_peer_*` wrappers and keep acknowledgement requests anchored to product context."
        )
    if _has_capability(authority, "comm.user_message") or _has_capability(authority, "comm.user_ask"):
        lines.append(
            "- User communication: use visible product/user ask or message tools for blocking decisions and non-blocking status."
        )
    if _has_capability(authority, "journal.private"):
        lines.append(
            "- Recovery notes: use the visible private journal wrapper for observations, plans, and checkpoints."
        )
    if _allows_category(authority, "execution_task_control"):
        lines.append(
            "- Executable task control: if explicitly visible, use it only inside this class's policy and do not assume Worker dispatch or merge authority."
        )
    if _allows_category(authority, "engineer_roster"):
        lines.append(
            "- Engineer roster/hiring: if explicitly visible, follow user-approval gates before treating a staffing change as live."
        )
    return lines


def _restricted_unavailable_line(authority: dict) -> str:
    unavailable: list[str] = []
    if not _allows_category(authority, "engineer_roster"):
        unavailable.append("Engineer hiring/roster management")
    if not _allows_category(authority, "engineer_worker_comm"):
        unavailable.append("direct Engineer/Worker messaging or control")
    if not _allows_category(authority, "execution_task_control"):
        unavailable.append("executable task create/reassign/move/dispatch")
    if not _allows_category(authority, "worker_dispatch"):
        unavailable.append("Worker dispatch")
    if not _allows_category(authority, "worktree_merge"):
        unavailable.append("worktree merge/release")
    if not _allows_category(authority, "deploy_admin"):
        unavailable.append("deploy/restart/admin")
    if not _allows_category(authority, "profile_admin"):
        unavailable.append("settings/profile administration")
    if not _has_capabilities(
            authority,
            "decision.accept",
            "decision.create",
            "decision.update",
    ):
        unavailable.append("accepted-decision authority")
    if not unavailable:
        return ""
    return (
        "- Unavailable powers in this session: "
        + "; ".join(unavailable)
        + ". Do not present these as workflows you can perform; capture the need as a proposal, user ask, or product-peer discussion instead."
    )


def _restricted_boot_lines(authority: dict) -> list[str]:
    if authority.get("is_torque_steward"):
        return [
            "1. Confirm your class, group, lifecycle/status, and visible read-only tools with `torque_context()` when available.",
            "2. Read projected board/task and recent-event context before making any recommendation.",
            "3. For onboarding or operating-state requests, prefer the structured Steward operating brief helper when visible; otherwise summarize operational health from projected reads: stuck/stale work, missed handoffs, unclear ownership, overdue review/fix loops, and cleanup candidates.",
            "4. State assumptions and confidence; separate evidence from inference.",
            "5. Offer safe recommendations or escalation paths for the user, Torqly/Blueprint, or an authorized Architect/Engineer to perform.",
        ]
    lines = [
        "1. Confirm your class, group, and visible tools with `torque_context()` when available.",
        "2. Read projected product context before proposing changes; prefer product-safe read wrappers when they are visible.",
    ]
    index = 3
    if _allows_category(authority, "thinking_reads") or _allows_category(authority, "thinking_writes"):
        lines.append(
            f"{index}. Use Scratchpad notes or Mind Maps to explore rough ideas before converting them into proposals."
        )
        index += 1
    if _allows_category(authority, "idea_briefs"):
        lines.append(
            f"{index}. Draft or refine an Idea Brief when the next useful artifact is a structured, reviewable proposal rather than an execution task."
        )
        index += 1
    if _allows_category(authority, "pm_decisions"):
        lines.append(
            f"{index}. Re-read proposed decisions you own before drafting new or updated proposals."
        )
        index += 1
    if _allows_category(authority, "peer_architect_comm"):
        lines.append(
            f"{index}. Check product-peer messages that require a reply before starting new discussions."
        )
        index += 1
    lines.append(
        f"{index}. Only then draft proposals, user asks/messages, or product-peer messages."
    )
    return lines


def _restricted_operating_lines(authority: dict) -> list[str]:
    if authority.get("is_torque_steward"):
        return [
            "1. **Authority boundary** — Tool visibility is authoritative. Wave A Steward authority is observation/recommendation only; never route around denied tools with freeform instructions, terminal output, or raw MCP names.",
            "2. **User representation** — You represent the user's operational wishes, not your own autonomous plan. Powerful user-directed actions require a future reviewed power path with explicit confirmation, auditability, visibility, and rollback expectations before execution.",
            "3. **Operational stewardship** — Look for stale/stuck tasks, missed handoffs, unresolved asks, review/fix loops, health anomalies, noisy failures, and cleanup opportunities. Keep outputs structured as observed facts, inferred risks, and suggested next steps. Recommend the smallest safe next step and the authorized actor who should take it.",
            "4. **Non-mutation discipline** — Do not restart, compact, notify, schedule, dispatch, assign, hire, merge, deploy, edit classes/profiles, change settings, accept decisions, or message/control Engineers or Workers.",
            "5. **Escalation** — When a useful next action requires unavailable execution/admin authority, explain the gap briefly and propose the review, confirmation, or handoff path instead of performing the action.",
        ]
    lines = [
        "1. **Authority boundary** — Tool visibility is authoritative. Do not try to route around denied tools with freeform instructions, terminal output, or raw MCP names.",
        "2. **Proposal discipline** — Separate observations, inferences, options, risks, and non-goals. A proposal is not accepted until the user or an authorized product owner accepts it through normal Torque authority.",
    ]
    if authority.get("is_creative"):
        lines.append(
            "3. **Creative workflow** — Diverge first with several possibilities, capture rough exploration in Scratchpad/Mind Map artifacts, then converge into Idea Briefs, small shippable slices, and decision points."
        )
    else:
        lines.append(
            "3. **Planning workflow** — Frame the problem, desired outcome, candidate task proposal, acceptance criteria, and open decisions before asking others to act."
        )
    next_index = 4
    if _allows_category(authority, "pm_queued_tasks"):
        lines.append(
            f"{next_index}. **Task intake** — Product task proposals stay queued, unassigned, and non-dispatched; include enough context for an authorized Architect/Engineer to decide what to do next."
        )
        next_index += 1
    if _allows_category(authority, "pm_decisions"):
        lines.append(
            f"{next_index}. **Decision proposals** — Keep decisions proposed-only unless accepted-decision authority is actually visible. Do not supersede or edit other owners' decisions as accepted truth."
        )
        next_index += 1
    if _allows_category(authority, "peer_architect_comm"):
        lines.append(
            f"{next_index}. **Peer discussion** — Product-peer messages are for alignment, critique, and handoff context. Anchor them to product context and record durable outcomes as proposals."
        )
        next_index += 1
    lines.append(
        f"{next_index}. **Escalation** — When useful next action requires unavailable execution/admin authority, explain the gap briefly and propose the authorized path instead of teaching the denied workflow."
    )
    return lines


def _build_restricted_system_prompt(authority: dict, group: str) -> str:
    unavailable = _restricted_unavailable_line(authority)
    tool_lines = _restricted_tool_lines(authority)
    if unavailable:
        tool_lines.append(unavailable)
    if authority.get("is_torque_steward"):
        job_line = (
            "Your job is to observe Torque operational state, explain what is "
            "happening, identify risks or missed handoffs, and recommend safe "
            "next steps within the authority that your Agent Class/Profile "
            "actually grants."
        )
        core_model = [
            "- **Operational context** is evidence you can read: task state, ownership, health, recent events, MCP telemetry, decisions, Areas, Initiatives, and visible handoff artifacts.",
            "- **Recommendation** is a non-binding next-step candidate. Name the authorized actor, confirmation needs, risk, and rollback/audit expectations when the recommendation would require power you do not have.",
            "- **User-delegated power** is future reviewed product surface, not implicit Wave A authority. Even explicit user requests for powerful actions must be debated, confirmed, audited, and routed through approved implementation before execution.",
            "- **Non-mutation** is the default: observations, summaries, and recommendations are allowed; state changes are denied unless a later reviewed class/tool surface grants them.",
        ]
    else:
        job_line = (
            "Your job is to shape product understanding, options, and safe "
            "next-step proposals within the authority that your Agent "
            "Class/Profile actually grants."
        )
        core_model = [
            "- **Product context** is evidence you can read: tasks, Areas, Initiatives, Decisions, recent context, Thinking artifacts, and user/peer messages that your projected tools expose.",
            "- **Proposal** is a non-binding task, decision, or direction candidate. Mark assumptions and acceptance criteria clearly; never imply it has already been approved.",
            "- **Thinking artifacts** are for exploration and synthesis. Use them to keep rough reasoning visible without converting every idea into a task or decision.",
            "- **User asks/messages** are the safe path for decisions or status that need the user's attention. Keep asks concrete and bounded.",
            "- **Product peers** are for discussion and alignment with same-group Architect/product profiles when that communication surface is visible.",
        ]
    lines = [
        _restricted_identity_sentence(authority).format(group=group),
        "",
        job_line,
        "",
        "## Available tools and authority",
        "",
        *tool_lines,
        "",
        "## Core model",
        "",
        *core_model,
        "",
        "## Session boot checklist",
        "",
        *_restricted_boot_lines(authority),
        "",
        "## Operating guidelines",
        "",
        *_restricted_operating_lines(authority),
    ]
    return "\n".join(lines).strip()


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
            "- Tool-enforced gates and Agent Class/Profile restrictions still "
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
                                    agent_class_snapshot: dict | None = None,
                                    agent_profile_snapshot: dict | None = None
                                    ) -> str:
    """Return the Torque preamble appropriate for this Architect authority."""

    authority = _architect_prompt_authority_context(
        architect_cell=architect_cell,
        agent_class_snapshot=agent_class_snapshot,
        agent_profile_snapshot=agent_profile_snapshot,
    )
    if authority.get("is_full"):
        return build_torque_system_prompt(
            include_shared_memory=False,
        )
    return _RESTRICTED_TORQUE_PREAMBLE.rstrip() + "\n"


def build_architect_system_prompt(group: str,
                                  architect_settings=None,
                                  action_system_prompt: str = "",
                                  group_settings=None,
                                  behavior_overlay_block: str = "",
                                  architect_cell=None,
                                  agent_class_snapshot: dict | None = None,
                                  agent_profile_snapshot: dict | None = None
                                  ) -> str:
    """Assemble the architect boot prompt.

    Concatenates: base identity → shared memory guidance → action
    system_prompt → structured policy section → custom instructions.
    """
    authority = _architect_prompt_authority_context(
        architect_cell=architect_cell,
        agent_class_snapshot=agent_class_snapshot,
        agent_profile_snapshot=agent_profile_snapshot,
    )
    if authority.get("is_full"):
        parts = [
            _BASE_SYSTEM_PROMPT.format(group=group),
            build_shared_memory_guidance(),
            # Architects are user-created only, so their owner is always the
            # user: surface the first substantive message to the user instead
            # of only emitting it to the terminal.
            build_owner_user_message_guidance("architect_message_user"),
        ]
    else:
        parts = [_build_restricted_system_prompt(authority, group)]
        if _has_capabilities(
                authority,
                "memory.read",
                "memory.publish",
                "memory.admin",
        ):
            parts.append(build_shared_memory_guidance())
        if _has_capability(authority, "comm.user_message"):
            parts.append(
                build_owner_user_message_guidance(
                    "architect_product_message_user"
                )
            )

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

    return "\n\n".join(parts) + "\n"
