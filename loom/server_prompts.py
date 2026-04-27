"""Shared prompt text helpers for Loom worker dispatch."""

from __future__ import annotations

from textwrap import dedent

_POSTSCRIPT_MANDATE = (
    "IMPORTANT: Finish this task by calling a Loom MCP tool below. "
    "Do NOT ask the user directly. Use `loom_ask(...)` only for a "
    "blocking human decision or approval so Loom can track it. "
    "Do NOT just stop — always signal completion via one of these tools."
)

_DONE_LINE = (
    '- `loom_done(message="brief summary")` — task complete, no follow-up needed'
)

_ASK_LINE = (
    '- `loom_ask(question="title", description="details")` '
    '— blocking human decision/approval only '
    '(creates a task in Backlog for review; `description` is optional)'
)

_FALLBACK_LINES = [
    '- `loom_blocked(reason="reason")` — need user input',
    '- `loom_error(message="message")` — unrecoverable error',
    '- `loom_verify(state="pending|attempted|passed|failed", '
    'mode="deploy|restart", tests_run="...", notes="...")` '
    '— record manual deploy/restart/smoke verification details when relevant',
]


def build_loom_system_prompt() -> str:
    """Build the persistent Loom system prompt for dispatched agents."""
    return dedent("""\
        # Loom Agent

        You are running inside Loom, an AI agent orchestration system.
        Loom tracks your task, manages your worktree, and coordinates
        you with other agents in a pipeline.

        ## Reporting tools

        Use the Loom MCP tools to report progress and completion:

        - `loom_done(message="summary")` — task complete, no follow-up needed
        - `loom_ready()` — task complete and release this agent for future work
        - `loom_progress(message="current activity")` — update your activity status
        - `loom_blocked(reason="reason")` — signal that you need help
        - `loom_error(message="message")` — report an unrecoverable error
        - `loom_verify(state="passed", tests_run="...", notes="...")` — record manual deploy/restart/smoke verification details when relevant
        - `loom_derive(description="title", action="action-name", context="details")` — create a subtask and dispatch it according to the allowed transition
        - `loom_ask(question="question", description="details")` — request a blocking human decision or approval when the task cannot continue safely without it
        - `loom_context()` — view your current task, agent info, and pipeline state

        ## Important

        Always signal completion via one of the tools above.
        Your dispatch prompt specifies which transitions are available —
        use those to determine valid `derive` targets.
        Use `loom_ask` only when a blocking human answer or approval is
        required to continue safely. If you can keep moving, do so.
        For status updates, non-blocking observations, or optional
        follow-up ideas, continue working and report them via
        `loom_progress`, `loom_done`, `loom_blocked`, or derived-task
        context instead of pausing the task.
        When in doubt, call `loom_context()` to see your current state.
    """)


def _derive_line(transition: dict) -> str:
    when = transition.get("when", "")
    desc = f" — {when}" if when else ""
    suffix = ""
    if transition.get("target") == "self":
        suffix = " (continues in the same agent)"
    return (
        '- `loom_derive(description="short title", '
        f'context="details", action="{transition["action"]}")`{desc}{suffix}'
    )


def _normalize_optional_block(text: str) -> str:
    return str(text or "").strip("\n")


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
    upload_call = (
        'loom_task_upload_artifact(content_text="<your full report>", '
        f'artifact_type="{deliverable_type or "generated_doc"}", '
        f'title="{title_default}")'
    )
    return [
        "**Deliverable contract**",
        "",
        (f"This task requires a deliverable artifact ({descriptor}). "
         "Before calling `loom_done(...)` (or `loom_ready(...)`) you MUST "
         "attach your report to the task via:"),
        "",
        f"  {upload_call}",
        "",
        ("Inline prose in `loom_done.message` or terminal output will NOT "
         "be persisted as the deliverable — the artifact is what the "
         "requester reads. `loom_done()` and `loom_ready()` will refuse "
         "until a matching artifact is attached."),
        "",
    ]


def build_dispatch_postscript(*,
                              transitions: list[dict] | None = None,
                              is_clean: bool = True,
                              commit_hint: str = "",
                              pipeline_context: str = "",
                              deliverable_required: bool = False,
                              deliverable_type: str = "",
                              deliverable_format: str = "",
                              deliverable_artifact_title: str = "",
                              task_title: str = "") -> str:
    """Build the task-local Loom MCP completion guidance block."""
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
