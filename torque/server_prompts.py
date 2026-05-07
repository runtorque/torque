"""Shared prompt text helpers for Torque worker dispatch."""

from __future__ import annotations

from textwrap import dedent

_POSTSCRIPT_MANDATE = (
    "IMPORTANT: Finish this task by calling a Torque MCP tool below. "
    "Do NOT ask the user directly. Use `torque_ask(...)` only for a "
    "blocking human decision or approval so Torque can track it. "
    "Do NOT just stop — always signal completion via one of these tools."
)

_DONE_LINE = (
    '- `torque_done(message="brief summary")` — task complete, no follow-up needed'
)

_ASK_LINE = (
    '- `torque_ask(question="title", description="details")` '
    '— blocking human decision/approval only '
    '(creates a task in Backlog for review; `description` is optional)'
)

_FALLBACK_LINES = [
    '- `torque_blocked(reason="reason")` — need user input',
    '- `torque_error(message="message")` — unrecoverable error',
    '- `torque_verify(state="pending|attempted|passed|failed", '
    'mode="deploy|restart", tests_run="...", notes="...")` '
    '— record manual deploy/restart/smoke verification details when relevant',
]


def build_torque_system_prompt() -> str:
    """Build the persistent Torque system prompt for dispatched agents."""
    return dedent("""\
        # Torque Agent

        You are running inside Torque, an AI agent orchestration system.
        Torque tracks your task, manages your worktree, and coordinates
        you with other agents in a pipeline.

        ## Reporting tools

        Use the Torque MCP tools to report progress and completion:

        - `torque_done(message="summary")` — task complete, no follow-up needed
        - `torque_ready()` — task complete and release this agent for future work
        - `torque_progress(message="current activity")` — update your activity status
        - `torque_blocked(reason="reason")` — signal that you need help
        - `torque_error(message="message")` — report an unrecoverable error
        - `torque_verify(state="passed", tests_run="...", notes="...")` — record manual deploy/restart/smoke verification details when relevant
        - `torque_derive(description="title", action="action-name", context="details")` — create a subtask and dispatch it according to the allowed transition
        - `torque_ask(question="question", description="details")` — request a blocking human decision or approval when the task cannot continue safely without it
        - `torque_context()` — view your current task, agent info, and pipeline state

        ## Important

        Always signal completion via one of the tools above.
        Your dispatch prompt specifies which transitions are available —
        use those to determine valid `derive` targets.
        Use `torque_ask` only when a blocking human answer or approval is
        required to continue safely. If you can keep moving, do so.
        For status updates, non-blocking observations, or optional
        follow-up ideas, continue working and report them via
        `torque_progress`, `torque_done`, `torque_blocked`, or derived-task
        context instead of pausing the task.
        When in doubt, call `torque_context()` to see your current state.
    """)


def _derive_line(transition: dict) -> str:
    when = transition.get("when", "")
    desc = f" — {when}" if when else ""
    suffix = ""
    if transition.get("target") == "self":
        suffix = " (continues in the same agent)"
    return (
        '- `torque_derive(description="short title", '
        f'context="details", action="{transition["action"]}")`{desc}{suffix}'
    )


def _normalize_optional_block(text: str) -> str:
    return str(text or "").strip("\n")


def deliverable_word(deliverable_type: str) -> str:
    """Return the user-facing noun for a deliverable contract type.

    Empty or ``"other"`` collapse to the generic ``"deliverable"``; any
    other value (canonical like ``"report"`` / ``"plan"`` or free-form
    like ``"diagnostic_log"``) is returned verbatim after stripping
    whitespace. The result feeds natural-language copy in the dispatch
    postscript and the gate error message so the worker sees wording
    that matches the contract instead of a hardcoded "report".
    """
    raw = str(deliverable_type or "").strip()
    if not raw or raw.lower() == "other":
        return "deliverable"
    return raw


def _build_deliverable_block(*,
                             deliverable_required: bool,
                             deliverable_type: str,
                             deliverable_format: str,
                             deliverable_artifact_title: str,
                             task_title: str = "") -> list[str]:
    """Render the Deliverable contract block as a list of lines.

    Returns ``[]`` when no deliverable is required so callers can splice
    the result into their normal lines list without conditionals.
    """
    if not deliverable_required:
        return []
    type_label = (deliverable_type or "any artifact").strip() or "any artifact"
    format_label = (deliverable_format or "").strip()
    title_default = (
        deliverable_artifact_title.strip()
        or task_title.strip()
        or "deliverable"
    )
    descriptor = type_label
    if format_label:
        descriptor = f"{type_label}, {format_label}"
    word = deliverable_word(deliverable_type)
    upload_call = (
        f'torque_task_upload_artifact(content_text="<your full {word}>", '
        f'artifact_type="{deliverable_type or "generated_doc"}", '
        f'title="{title_default}")'
    )
    return [
        "**Deliverable contract**",
        "",
        (f"This task requires a deliverable artifact ({descriptor}). "
         "Before calling `torque_done(...)` (or `torque_ready(...)`) you MUST "
         f"attach your {word} to the task via:"),
        "",
        f"  {upload_call}",
        "",
        ("Inline prose in `torque_done.message` or terminal output will NOT "
         "be persisted as the deliverable — the artifact is what the "
         "requester reads. `torque_done()` and `torque_ready()` will refuse "
         "until a matching artifact is attached."),
        "",
    ]


def build_engineer_deliverable_awareness(task) -> str:
    """Render the engineer-facing deliverable awareness block for a task.

    Mirrors the worker dispatch postscript's deliverable contract block
    but engineer-flavored: surfaces the engineer-side
    ``engineer_task_upload_artifact`` tool (with a lazy-load hint, since
    that tool is deferred per ``ENGINEER_DEFERRED_TOOL_NAMES``) and notes
    that workers see their own contract block via ``torque_task_upload_artifact``.

    Returns ``""`` when the task carries no deliverable contract so callers
    can splice the result into a response without conditionals.
    """
    if not task or not bool(getattr(task, "deliverable_required", False)):
        return ""
    deliverable_type = str(getattr(task, "deliverable_type", "") or "")
    deliverable_format = str(getattr(task, "deliverable_format", "") or "")
    title_default = (
        str(getattr(task, "deliverable_artifact_title", "") or "").strip()
        or str(getattr(task, "task", "") or "").strip()
        or "deliverable"
    )
    task_id = str(getattr(task, "id", "") or "").strip()
    type_label = deliverable_type.strip() or "any artifact"
    descriptor = type_label
    if deliverable_format.strip():
        descriptor = f"{type_label}, {deliverable_format.strip()}"
    word = deliverable_word(deliverable_type)
    upload_call = (
        f'engineer_task_upload_artifact(task_id="{task_id}", '
        f'content_text="<your full {word}>", '
        f'artifact_type="{deliverable_type or "generated_doc"}", '
        f'title="{title_default}")'
    )
    lines = [
        "**Deliverable contract on this task**",
        "",
        f"Type: {type_label}"
        + (f" ({deliverable_format.strip()})" if deliverable_format.strip() else ""),
        f"Title: {title_default}",
        "",
        f"This task requires a deliverable artifact ({descriptor}). "
        "To attach the deliverable yourself:",
        "",
        f"  {upload_call}",
        "",
        "If the tool isn't visible, run "
        '`engineer_tool_search("select:engineer_task_upload_artifact")` '
        "to load the schema.",
        "",
        "When dispatching a worker, the worker's postscript will contain "
        "its own contract block — they will see the same contract via "
        "their own `torque_task_upload_artifact` tool.",
        "",
        "The `torque_done` / `engineer_task_resolve` gate refuses task "
        "closure until a matching artifact attaches.",
    ]
    return "\n".join(lines)


def _build_review_required_block(*,
                                  requires_review: bool,
                                  pre_approved_by: str = "",
                                  transitions: list[dict] | None = None,
                                  ) -> list[str]:
    """Render the mandatory-review awareness block (TORQUE:256).

    Returns ``[]`` when no review contract applies so callers can splice
    the result without conditionals.
    """
    if not requires_review:
        return []
    pre_approved_by = str(pre_approved_by or "").strip()
    if pre_approved_by:
        return [
            "**Review pre-approved**",
            "",
            (f"This task is pre-approved by review `{pre_approved_by}`. "
             "You may call `torque_done(...)` directly when complete; no "
             "review derivation is required for this task. The reviewer "
             "determined this fix is small enough to ship without "
             "re-review."),
            "",
        ]
    # Find the required transition's target action (first wins) so the
    # awareness block names the action by name.
    required_target = ""
    for tr in (transitions or []):
        if isinstance(tr, dict) and tr.get("required") and tr.get("action"):
            required_target = str(tr.get("action") or "").strip()
            break
    target_label = required_target or "feature/review"
    return [
        "**Review required**",
        "",
        (f"This task must derive the `{target_label}` transition before "
         "`torque_done` will succeed. Do NOT call `torque_done(...)` "
         "directly. The MCP gate will refuse with a structured "
         "`review_required` error and the task will stay In Progress. "
         "After you derive the review, the reviewer's Ship verdict "
         "cascades the parent to Done — you do not need to call "
         "`torque_done()` again on this parent task. If you believe a "
         "review is unnecessary, use `torque_ask(...)` to request a human "
         "decision; do NOT self-skip."),
        "",
    ]


def compute_commit_hint(*,
                        has_worktree_branch: bool,
                        is_implementation: bool,
                        auto_checkpoint: bool,
                        checkpoint_on_progress: bool) -> str:
    """Build the commit-discipline hint for the dispatch postscript.

    Emits the "commit your changes" instruction when the worker is doing
    committable implementation work and is not relying on Torque's
    automatic checkpoint mechanism. The hint covers two paths:

    - **Isolated worktree** (``has_worktree_branch`` truthy): the worker
      has its own branch + working dir; a plain commit instruction is
      enough because there is no shared-tree contention.
    - **Shared working tree** (``has_worktree_branch`` falsy +
      ``is_implementation`` truthy): typical of ``oneshot/*`` actions,
      where multiple workers may operate on the same tree concurrently.
      The hint adds a scope-your-``git add`` warning to prevent sweeping
      sibling workers' uncommitted WIP into the commit.

    Returns ``""`` when no commit instruction applies (review tasks
    without committable work, auto-checkpointed cells, etc.).
    """
    if not (has_worktree_branch or is_implementation):
        return ""
    if auto_checkpoint or checkpoint_on_progress:
        return ""
    hint = (
        "Before reporting done, commit all your changes with a "
        "descriptive commit message."
    )
    if not has_worktree_branch:
        hint += (
            " You are not in an isolated worktree, so scope `git add` "
            "to the specific files you modified — do not use "
            "`git add -A`. Other workers may have uncommitted changes "
            "in the same tree."
        )
    return hint


def build_dispatch_postscript(*,
                              transitions: list[dict] | None = None,
                              is_clean: bool = True,
                              commit_hint: str = "",
                              pipeline_context: str = "",
                              deliverable_required: bool = False,
                              deliverable_type: str = "",
                              deliverable_format: str = "",
                              deliverable_artifact_title: str = "",
                              task_title: str = "",
                              requires_review: bool = False,
                              pre_approved_by: str = "") -> str:
    """Build the task-local Torque MCP completion guidance block."""
    transitions = transitions or []
    has_transitions = any(
        isinstance(tr, dict) and tr.get("action")
        for tr in transitions
    )
    has_ask = any(
        isinstance(tr, dict) and tr.get("ask")
        for tr in transitions
    )

    lines: list[str] = []
    if has_transitions or has_ask:
        lines.append(_POSTSCRIPT_MANDATE)
        lines.append("")

    deliverable_block = _build_deliverable_block(
        deliverable_required=deliverable_required,
        deliverable_type=deliverable_type,
        deliverable_format=deliverable_format,
        deliverable_artifact_title=deliverable_artifact_title,
        task_title=task_title,
    )
    if deliverable_block:
        lines.extend(deliverable_block)

    review_block = _build_review_required_block(
        requires_review=requires_review,
        pre_approved_by=pre_approved_by,
        transitions=transitions,
    )
    if review_block:
        lines.extend(review_block)

    lines.append("Available completion paths for this task:")
    for tr in transitions:
        if isinstance(tr, dict) and tr.get("action"):
            lines.append(_derive_line(tr))
    if has_ask:
        lines.append(_ASK_LINE)
    lines.append(_DONE_LINE)
    lines.extend([
        "",
        "Other reporting tools when relevant:",
        *_FALLBACK_LINES,
    ])

    normalized_context = _normalize_optional_block(pipeline_context)
    if normalized_context:
        lines.extend(["", normalized_context])

    normalized_commit_hint = _normalize_optional_block(commit_hint)
    if normalized_commit_hint:
        lines.extend(["", normalized_commit_hint])

    prefix = "\n\n" if is_clean else "\n\n---\n"
    return prefix + "\n".join(lines)
