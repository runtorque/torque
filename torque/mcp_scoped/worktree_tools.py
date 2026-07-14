"""Worktree merge/PR response formatting helpers."""

from torque.mcp_retry import is_mcp_pr_phase, is_mcp_pr_phase_retryable
from torque.worktree_streams import merge_report_snippet_from_merge_result

def _worktree_result_pr_url(result: dict | None) -> str:
    result = result or {}
    pr = result.get("pr")
    if not isinstance(pr, dict):
        pr = {}
    return str(
        result.get("pr_url")
        or result.get("url")
        or pr.get("url")
        or ""
    ).strip()


def _worktree_result_phase(result: dict | None) -> str:
    result = result or {}
    phase = str(result.get("phase") or "").strip()
    if phase:
        return phase
    pr = result.get("pr")
    if isinstance(pr, dict):
        return str(pr.get("phase") or "").strip()
    return ""


def _worktree_result_error(result: dict | None, fallback: str = "") -> str:
    result = result or {}
    return str(
        result.get("error")
        or result.get("message")
        or fallback
        or "Merge failed"
    ).strip()


def _format_worktree_pr_error(result: dict | None,
                              fallback: str = "Merge failed"
                              ) -> tuple[str, bool | None]:
    """Return an MCP-facing error string plus optional cacheability.

    ``False`` cacheability marks transient PR transport/API phases as
    retryable through the MCP idempotency layer; ``None`` preserves the
    normal cache policy for deterministic errors.
    """
    result = result or {}
    error = _worktree_result_error(result, fallback)
    phase = _worktree_result_phase(result)
    pr_url = _worktree_result_pr_url(result)
    retryable = (
        is_mcp_pr_phase_retryable(phase, error)
        if is_mcp_pr_phase(phase)
        else False
    )
    context = []
    if phase:
        context.append(f"phase={phase}")
    if pr_url:
        context.append(f"pr_url={pr_url}")
    if phase and is_mcp_pr_phase(phase):
        context.append(f"retryable={'true' if retryable else 'false'}")
    if context:
        error = f"{error}\n\nPR context: " + ", ".join(context)
    return error, False if retryable else None


def _worktree_merge_branch_base(result: dict | None, cell, *,
                                branch: str = "",
                                base_branch: str = "") -> tuple[str, str]:
    result = result or {}
    merge_branch = str(
        result.get("branch")
        or branch
        or getattr(cell, "worktree_branch", "")
        or ""
    ).strip()
    merge_base_branch = str(
        result.get("base_branch")
        or base_branch
        or getattr(cell, "worktree_base_branch", "")
        or ""
    ).strip()
    return merge_branch, merge_base_branch


def _worktree_merge_default_message(result: dict | None, cell, *,
                                    branch: str = "",
                                    base_branch: str = "") -> str:
    result = result or {}
    mode = str(result.get("mode") or "direct").strip() or "direct"
    pending = bool(result.get("pending"))
    merge_branch, merge_base_branch = _worktree_merge_branch_base(
        result,
        cell,
        branch=branch,
        base_branch=base_branch,
    )
    if mode == "pull_request":
        if pending:
            return "Pull request is open with auto-merge pending."
        if merge_branch and merge_base_branch:
            return (
                f"Squash-merged {merge_branch} into {merge_base_branch}"
            )
        return "Pull request squash merge completed."
    if merge_branch and merge_base_branch:
        return f"Merged {merge_branch} into {merge_base_branch}"
    return "Worktree merge completed."


def _worktree_merge_success_payload(result: dict | None, cell, *,
                                    branch: str = "",
                                    base_branch: str = "") -> dict:
    result = result or {}
    cleanup = result.get("cleanup", {})
    if not isinstance(cleanup, dict):
        cleanup = {}
    mode = str(result.get("mode") or "direct").strip() or "direct"
    pending = bool(result.get("pending"))
    pr_url = _worktree_result_pr_url(result)
    payload = {
        "type": "ok",
        "message": str(result.get("message") or "").strip(),
        "mode": mode,
        "pr_url": pr_url,
        "pending": pending,
        "sha": str(result.get("sha") or "").strip(),
        "cleanup": cleanup,
    }
    if not payload["message"]:
        payload["message"] = _worktree_merge_default_message(
            result,
            cell,
            branch=branch,
            base_branch=base_branch,
        )
    if "merged" in result:
        payload["merged"] = bool(result.get("merged"))
    elif mode == "pull_request":
        payload["merged"] = bool(payload["sha"]) and not pending
    if "url" in result:
        payload["url"] = str(result.get("url") or "").strip()
    if isinstance(result.get("pr"), dict):
        payload["pr"] = result["pr"]
    if isinstance(result.get("origin_verification"), dict):
        payload["origin_verification"] = result["origin_verification"]
    if isinstance(result.get("nested_submodules"), dict):
        payload["nested_submodules"] = result["nested_submodules"]
    if result.get("auto_force_push"):
        payload["auto_force_push"] = True
    if isinstance(result.get("push"), dict):
        payload["push"] = result["push"]
    if "force_direct" in result:
        payload["force_direct"] = bool(result.get("force_direct"))
    if result.get("warning"):
        payload["warning"] = str(result.get("warning") or "")
    if isinstance(result.get("workflow_breach"), dict):
        payload["workflow_breach"] = result["workflow_breach"]
    if isinstance(result.get("stale_base"), dict):
        payload["stale_base"] = result["stale_base"]
    if result.get("stale_base_warning"):
        payload["stale_base_warning"] = str(
            result.get("stale_base_warning") or ""
        )
    payload["merge_report_snippet"] = merge_report_snippet_from_merge_result(
        result,
        branch=branch,
        base_branch=base_branch,
    )
    # Surface the silent merge cleanup-override (queued follow-ups preserve the
    # agent + worktree even when close/remove flags were requested). The new
    # fields ride along in ``cleanup``; also raise a human-readable WARNING so
    # engineers detect it without deep-inspecting the struct.
    if cleanup.get("cleanup_overridden"):
        count = cleanup.get("queued_followup_count", 0)
        warn = (
            "WARNING: requested cleanup flags (close agent / remove worktree) "
            f"were NOT honored because {count} queued follow-up task(s) remain "
            "on this agent; the agent and its worktree were preserved for that "
            "queued work."
        )
        existing = str(payload.get("warning") or "").strip()
        payload["warning"] = f"{existing}\n{warn}".strip() if existing else warn
        payload["message"] = f"{payload['message']}\n{warn}".strip()
    return payload
