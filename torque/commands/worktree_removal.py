"""Reviewed operator unlink/removal without weakening filesystem cleanup guards."""


from torque.worktree_manager.removal_review import removal_git_review


def clear_worktree_tracking(cell):
    cell.worktree_path = ""
    cell.worktree_branch = ""
    cell.worktree_base_branch = ""
    cell.worktree_repo_root = ""
    cell.worktree_dirty = False
    cell.worktree_diff = {}
    cell.worktree_changed_files = []
    cell.worktree_checkpoints = 0
    cell.worktree_ahead = 0
    cell.worktree_behind = 0
    cell.worktree_merged = False


def _removal_context(data, runtime):
    state = runtime.state
    aid = str(data.get("id", "") or "")
    cell = state.agents.get(aid)
    result = {"type": "worktree_remove_preview", "id": aid, "ok": False}
    if not cell or state.agent_is_tombstoned(cell) or not cell.worktree_path:
        return {**result, "error": "Removal requires an available agent with a worktree"}
    if data.get("expected_worktree_path", cell.worktree_path) != cell.worktree_path:
        return {**result, "error": "The worktree path changed. Close and review the current worktree."}
    shared = [other for other in state.agents.values()
              if other.id != cell.id and not state.agent_is_tombstoned(other)
              and any(runtime.worktree_path_contains(cell.worktree_path, getattr(other, field, ""))
                      for field in ("worktree_path", "directory", "current_path", "git_root"))]
    shared.sort(key=lambda other: other.id)
    blocked = runtime.worktree_removal_refusal_reason(state, cell)
    if not blocked and cell.session_id:
        blocked = "Stop the attached session before releasing its worktree."
    repo_root = str(getattr(cell, "worktree_repo_root", "") or "")
    if not blocked and not repo_root:
        blocked = "The original repository directory is unavailable. Restore it before releasing this worktree."
    review = {
        "path": cell.worktree_path,
        "branch": str(getattr(cell, "worktree_branch", "") or ""),
        "repo_root": repo_root,
        "session_id": str(cell.session_id or ""),
        "mode": "unlink" if shared else "remove",
        "shared_ids": [other.id for other in shared],
        "base_branch": str(getattr(cell, "worktree_base_branch", "") or ""),
    }
    return {**result, "ok": True, "review": review, "blocked_reason": blocked,
            "shared_with": [{"id": other.id, "name": other.name} for other in shared]}


async def removal_preview(data, runtime):
    context = _removal_context(data, runtime)
    if not context['ok']:
        return context
    cell = runtime.state.agents[context['id']]
    target = context['review']
    evidence = await removal_git_review(target['path'], target['branch'], target['base_branch'])
    current = _removal_context(data, runtime)
    # Git probes yield to session/agent updates. Recheck the target and all users
    # before presenting a review or reaching the next lifecycle effect.
    if runtime.state.agents.get(cell.id) is not cell or current != context:
        return {'type': context['type'], 'id': context['id'], 'ok': False,
                'error': 'The worktree or its users changed while reading. Refresh the removal review.'}
    if evidence.get('error'):
        return {'type': context['type'], 'id': context['id'], 'ok': False, 'error': evidence['error']}
    return {**context, 'review': {**target, **evidence}}


async def remove_reviewed_worktree(data, runtime):
    preview = await removal_preview(data, runtime)
    result = {"type": "worktree_remove", "id": preview["id"], "ok": False,
              "link_cleared": False, "worktree_removed": False}
    if not preview["ok"]:
        return {**result, "error": preview["error"]}
    current = preview["review"]
    result.update(mode=current["mode"], worktree_path=current["path"])
    if not isinstance(data.get("removal_review"), dict) or data["removal_review"] != current:
        return {**result, "error": "The worktree, session, changes or sharing changed. Refresh the removal review before confirming."}
    if preview["blocked_reason"]:
        return {**result, "error": preview["blocked_reason"]}
    if data.get("relaunch"):
        return {**result, "error": "Reviewed worktree release requires a stopped agent and does not relaunch sessions."}
    state = runtime.state
    cell = state.agents[preview["id"]]
    if current["mode"] == "unlink":
        # No awaited work and no Git operation: other users and their files are untouched.
        clear_worktree_tracking(cell)
        cell.directory = current["repo_root"]
        cell.current_path = ""
        cell.current_branch = ""
        cell.git_root = ""
        state._emit_agent(cell)
        state._db_save_agent(cell)
        return {**result, "ok": True, "link_cleared": True,
                "message": "Agent worktree link cleared. Shared worktree and branch retained."}
    removed = await runtime.safe_remove_worktree_result(cell)
    result.update(removed)
    if not removed.get("worktree_removed"):
        return {**result, "ok": False, "error": removed.get("message") or "Worktree removal was refused"}
    if state.agents.get(cell.id) is cell and not state.agent_is_tombstoned(cell):
        cell.directory = current["repo_root"]
        state._emit_agent(cell)
        state._db_save_agent(cell)
    return {**result, "ok": True, "link_cleared": True,
            "message": ("Worktree removed. Branch retained: " + current["branch"]
                        if removed.get("branch_deleted") is False else "Worktree and branch removed.")}
