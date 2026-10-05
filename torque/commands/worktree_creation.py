"""Acknowledged worktree creation and recovery of its optional relaunch."""

from __future__ import annotations

import os

from ..config import log


async def handle_worktree_create(data, runtime):
    state = runtime.state
    manager = runtime.worktree_mgr
    aid = str(data.get("id", "") or "")
    cell = state.agents.get(aid)
    created = False

    def reply(*, ok=False, phase="target", error="", relaunched=False):
        value = {
            "type": "worktree_create", "id": aid, "ok": ok,
            "created": created,
            "worktree_path": str(getattr(cell, "worktree_path", "") or ""),
            "worktree_branch": str(getattr(cell, "worktree_branch", "") or ""),
            "session_id": str(getattr(cell, "session_id", "") or ""),
            "relaunched": relaunched, "phase": phase,
            "resume_available": bool(created and phase == "relaunch" and error),
        }
        if error:
            value["error"] = error
        else:
            value["message"] = "Worktree created and agent relaunched" if relaunched else "Worktree created"
        return value

    def available():
        return state.agents.get(aid) is cell and not getattr(cell, "deleted_at", 0)

    if not cell or not available() or not cell.directory:
        return reply(error="Creation requires an available agent with a directory")
    reviewed_session = str(data.get("expected_session_id", cell.session_id) or "")
    if str(cell.session_id or "") != reviewed_session:
        return reply(phase="session_changed", error="The agent session changed. Review the current session and confirm creation again.")
    resume_path = str(data.get("resume_worktree_path", "") or "")
    if resume_path:
        if not data.get("relaunch") or resume_path != cell.worktree_path:
            return reply(error="The created worktree no longer matches the relaunch request")
        created = True
        repo_root = cell.worktree_repo_root
    elif cell.worktree_path:
        return reply(error="Agent already has a worktree; inspect it instead of creating another")
    else:
        gs = state.get_group_settings(cell.group)
        repo_root = await manager.get_repo_root(cell.directory)
        if not repo_root:
            return reply(phase="create", error="The agent directory is not in a Git repository")
        if not available() or str(cell.session_id or "") != reviewed_session:
            return reply(phase="session_changed", error="The agent changed while its repository was being checked. Review it before creating a worktree.")
        try:
            path = await manager.create(
                cell, repo_root,
                base_dir=cell.worktree_base_dir or ".torque/worktrees",
                base_branch=cell.worktree_base_branch or gs.worktree_base_branch or "",
                symlinks=gs.worktree_symlinks,
                include_gitignored_symlinks=getattr(gs, "worktree_symlink_gitignored_paths", False),
                worktree_submodules=getattr(gs, "worktree_submodules", []),
                state=state,
            )
        except Exception as exc:
            log.exception("Operator worktree creation failed for '%s'", cell.name)
            return reply(phase="create", error=str(exc))
        if not path or path != cell.worktree_path:
            return reply(phase="create", error="Worktree creation did not return a confirmed path")
        created = True
        if not available():
            return reply(phase="target_changed", error="Worktree created, but the agent is no longer available. Inspect the returned worktree path before further action.")
        cell.directory = path
        state._emit_agent(cell)
        state._db_save_agent(cell)

    if not data.get("relaunch"):
        return reply(ok=True, phase="complete")
    if not available() or str(cell.session_id or "") != reviewed_session:
        return reply(phase="relaunch", error="Worktree created, but the agent or session changed before relaunch. Review the current session before retrying.")
    try:
        base_dir = cell.worktree_repo_root or cell.directory or await runtime.resolve_base_dir(cell.group)
        resolver = runtime.launch_resolver_for_cell(
            cell,
            resolve_agent_launch_config=runtime.resolve_agent_launch_config,
            resolve_engineer_launch_config=runtime.resolve_engineer_launch_config,
            resolve_architect_launch_config=runtime.resolve_architect_launch_config,
            resolve_worker_launch_config=runtime.resolve_worker_launch_config,
            is_designated_engineer=runtime.is_designated_engineer,
        )
        config = resolver(cell.group, base_dir=base_dir, explicit_template=cell.template, overrides={})
        if cell.session_id:
            await runtime.bridge.close_session(cell.session_id)
        # Session shutdown can yield to deletion or another lifecycle request.
        if not available() or str(cell.session_id or "") not in {"", reviewed_session}:
            return reply(phase="relaunch", error="Worktree created, but the agent changed while its previous session was closing. Review it before retrying.")
        cell.status = "stopped"
        cell.session_id = None
        cell.agent_session_id = ""
        state._emit_agent(cell)
        state._db_save_agent(cell)
        if cell.agent_type:
            runtime.get_adapter(cell.agent_type).uninstall_persistent_prompt(
                os.path.expanduser(repo_root), runtime.persistent_prompt_filename(cell))
        runtime.apply_persistent_prompt(cell, config, runtime.build_cell_persistent_prompt(cell, config))
        state._emit_agent(cell)
        state._db_save_agent(cell)
        await runtime.bridge.create_session(
            cell,
            env_vars=runtime.runtime_env_vars_for_cell(cell, config.get("env_vars")),
            env_file=config.get("env_file", ""), shell=config.get("shell", ""),
            system_prompt=config.get("system_prompt", ""),
            mcp_entrypoint=runtime.mcp_entrypoint_for_cell(cell),
            target_session_id=data.get("target_session_id", ""),
            target_window_id=data.get("target_window_id", ""),
        )
        if not available() or not cell.session_id or cell.session_id == reviewed_session:
            raise RuntimeError("A new agent session was not confirmed")
    except Exception as exc:
        log.exception("Worktree created but relaunch failed for '%s'", cell.name)
        return reply(phase="relaunch", error="Worktree created, but relaunch failed: " + str(exc))
    return reply(ok=True, phase="complete", relaunched=True)
