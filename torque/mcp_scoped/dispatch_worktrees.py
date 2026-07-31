"""Domain dispatcher extracted from :mod:`torque.mcp_tools_shared`."""

from torque.mcp_scoped.dispatch_context import ScopedDispatchContext, UNHANDLED
from torque.mcp_scoped.dispatch_runtime import *  # noqa: F403
from torque.worktree_scope import architect_can_access_user_owned_worker_worktree

async def dispatch_worktrees(ctx: ScopedDispatchContext):
    name = ctx.name
    args = ctx.args
    handle_command = ctx.handle_command
    state = ctx.state
    real_state = ctx.real_state
    tool_prefix = ctx.tool_prefix
    caller_kind = ctx.caller_kind
    caller_id = ctx.caller_id
    idempotency_key = ctx.idempotency_key
    _engineer_cell = ctx.caller_cell
    _engineer_group = ctx.caller_group
    tool_name = normalize_tool_name(name, tool_prefix)

    if tool_name == "merge":
        valid_target, target_error = _validate_exactly_one_worktree_target(args)
        if not valid_target:
            return target_error, True
        driverless = _has_path_target_args(args)
        cell = None
        agent_id = ""
        force_sibling_divergence = bool(args.get("force"))
        force_stale_base = bool(
            args.get("force_stale_base") or args.get("force")
        )
        force_boundary_mismatch = bool(args.get("force_boundary_mismatch"))
        boundary_mismatch_reason = str(
            args.get("boundary_mismatch_reason")
            or args.get("force_boundary_mismatch_reason")
            or ""
        ).strip()

        async def _try_post_success_agent_recovery(agent_ident: str):
            recovered_agent_id = _resolve_agent_including_tombstoned(
                real_state,
                agent_ident,
            )
            if not recovered_agent_id:
                return None
            if not _tombstoned_merge_target_visible_to_caller(
                real_state,
                caller_kind,
                caller_id,
                recovered_agent_id,
            ):
                return None
            recovery_payload = {"cmd": "worktree_merge", "id": recovered_agent_id}
            if "close_agent_on_merge" in args:
                recovery_payload["close_agent_on_merge"] = bool(
                    args.get("close_agent_on_merge")
                )
            if "remove_worktree_on_merge" in args:
                recovery_payload["remove_worktree_on_merge"] = bool(
                    args.get("remove_worktree_on_merge")
                )
            if "auto_move_to_done" in args:
                recovery_payload["auto_move_to_done"] = bool(
                    args.get("auto_move_to_done")
                )
            if force_stale_base:
                recovery_payload["force_stale_base"] = True
            if force_sibling_divergence:
                recovery_payload["force"] = True
            if force_boundary_mismatch:
                recovery_payload["force_boundary_mismatch"] = True
                if boundary_mismatch_reason:
                    recovery_payload["boundary_mismatch_reason"] = (
                        boundary_mismatch_reason
                    )
            if caller_id:
                recovery_payload["actor_agent_id"] = str(caller_id)
            recovered = await handle_command(recovery_payload)
            if recovered and recovered.get("ok"):
                recovered_cell = state.agents.get(recovered_agent_id)
                return json.dumps(
                    _worktree_merge_success_payload(
                        recovered,
                        recovered_cell,
                    )
                ), False
            return None

        if driverless:
            path_payload = _driverless_payload_from_args(
                args,
                caller_id=caller_id,
                group=_engineer_group,
            )
            scoped, scope_reason = _engineer_can_access_worktree_branch(
                real_state,
                caller_id,
                repo_root=path_payload.get("repo_root", ""),
                branch=path_payload.get("branch", ""),
            )
            if not scoped and path_payload.get("repo_root"):
                return scope_reason, True
            agent_id = ""
            merge_branch = path_payload.get("branch", "")
            merge_base_branch = path_payload.get("base_branch", "")
        else:
            agent_ident = args.get("agent", "")
            agent_id, agent_error = _resolve_visible_agent(
                real_state, caller_kind, caller_id, agent_ident
            )
            if not agent_id and caller_kind == "architect":
                candidate_id = _resolve_agent(real_state, agent_ident)
                candidate = real_state.agents.get(candidate_id) if candidate_id else None
                if architect_can_access_user_owned_worker_worktree(
                    real_state, _engineer_cell, candidate
                ):
                    agent_id = candidate_id
                    agent_error = ""
            if not agent_id:
                recovered = await _try_post_success_agent_recovery(agent_ident)
                if recovered:
                    return recovered
                return agent_error, True
            cell = state.agents.get(agent_id)
            if not cell or not cell.worktree_path:
                recovered = await _try_post_success_agent_recovery(agent_ident)
                if recovered:
                    return recovered
                return "Agent has no worktree", True
            merge_branch = str(getattr(cell, "worktree_branch", "") or "").strip()
            merge_base_branch = str(
                getattr(cell, "worktree_base_branch", "") or ""
            ).strip()

        merge_task_id = ""
        if not driverless and str(args.get("task", "") or "").strip():
            merge_task_id = _resolve_task(real_state, args.get("task", "")) or ""
            merge_task = real_state.board_tasks.get(merge_task_id)
            if not merge_task:
                return "Task not found", True
            if str(getattr(merge_task, "agent_id", "") or "") != agent_id:
                return (
                    "Merge task must be assigned to the selected worker; "
                    "branch identity cannot be used as task attribution.",
                    True,
                )

        # First check for conflicts / merge boundary eligibility
        if driverless:
            check_payload = {
                "cmd": "worktree_check_merge",
                **path_payload,
                "allow_stale_base": force_stale_base,
            }
            if force_boundary_mismatch:
                check_payload["allow_boundary_mismatch"] = True
            if force_sibling_divergence:
                check_payload["force"] = True
            result = await handle_command(check_payload)
            error_text = (result or {}).get("error", "")
            blocked = bool(
                result
                and (
                    result.get("type") == "error"
                    or (
                        result.get("error")
                        and not result.get("clean", True)
                        and not result.get("conflicts")
                    )
                )
            )
        else:
            result, error_text, blocked = await _run_worktree_merge_check_with_options(
                handle_command,
                agent_id,
                allow_stale_base=force_stale_base,
                allow_boundary_mismatch=force_boundary_mismatch,
            )
        if blocked:
            return error_text, True
        if result and not result.get("clean", True):
            error_text = str(result.get("error") or "").strip()
            if error_text and not result.get("conflicts"):
                return error_text, True
            conflict_list = _format_worktree_conflicts(
                result.get("conflicts", [])
            )
            if driverless:
                return (
                    f"Merge has conflicts:\n{conflict_list}\n\n"
                    "Resolve/rebase the driverless worktree manually, then retry "
                    "worktree_merge."
                ), True
            return (
                f"Merge has conflicts:\n{conflict_list}\n\n"
                "Run worktree_rebase on "
                f"{cell.slug or cell.id} to replay "
                f"{cell.worktree_branch} onto {cell.worktree_base_branch}, "
                "then retry worktree_merge. "
                "Ask the human only if the rebase still fails."
            ), True

        # Proceed with merge
        payload = (
            {"cmd": "worktree_merge", **path_payload}
            if driverless else {"cmd": "worktree_merge", "id": agent_id}
        )
        if merge_task_id:
            payload["merge_task_id"] = merge_task_id
        msg = args.get("message", "")
        if msg:
            payload["message"] = msg
        pr_title = str(args.get("pr_title") or "").strip()
        if pr_title:
            payload["pr_title"] = pr_title
        pr_body = str(args.get("pr_body") or "").strip()
        if pr_body:
            payload["pr_body"] = pr_body
        if "close_agent_on_merge" in args:
            payload["close_agent_on_merge"] = bool(
                args.get("close_agent_on_merge")
            )
        if "remove_worktree_on_merge" in args:
            payload["remove_worktree_on_merge"] = bool(
                args.get("remove_worktree_on_merge")
            )
        if "auto_move_to_done" in args:
            payload["auto_move_to_done"] = bool(
                args.get("auto_move_to_done")
            )
        if "force_direct" in args:
            payload["force_direct"] = bool(args.get("force_direct"))
        if force_stale_base:
            payload["force_stale_base"] = True
        if force_sibling_divergence:
            payload["force"] = True
        if force_boundary_mismatch:
            payload["force_boundary_mismatch"] = True
            if boundary_mismatch_reason:
                payload["boundary_mismatch_reason"] = boundary_mismatch_reason
        if caller_id:
            payload["actor_agent_id"] = str(caller_id)
        result = await handle_command(payload)
        if result and result.get("ok") is False:
            error, cacheable = _format_worktree_pr_error(result, "Merge failed")
            if "conflict" in error.lower():
                fresh_check = None
                if not driverless:
                    fresh_check, _, _ = await (
                        _run_worktree_merge_check_with_options(
                            handle_command,
                            agent_id,
                            allow_boundary_mismatch=force_boundary_mismatch,
                        )
                    )
                conflict_text = ""
                if fresh_check and not fresh_check.get("clean", True):
                    conflict_text = (
                        "\n\nConflicts:\n"
                        + _format_worktree_conflicts(
                            fresh_check.get("conflicts", [])
                        )
                    )
                if driverless:
                    return (
                        f"Merge failed: {error}{conflict_text}\n\n"
                        "Resolve/rebase the driverless worktree manually, then retry "
                        "worktree_merge."
                    ), True
                return (
                    f"Merge failed: {error}{conflict_text}\n\n"
                    "Run worktree_rebase on "
                    f"{cell.slug or cell.id} and retry worktree_merge. "
                    "Ask the human only if the rebase still fails."
                ), True
            if cacheable is False:
                return error, True, False
            return error, True
        cleanup = result.get("cleanup", {}) if result else {}
        cleanup_errors = cleanup.get("errors", [])
        if cleanup_errors:
            landed_pr = (
                str(result.get("mode") or "").strip() == "pull_request"
                and bool(result.get("merged"))
                and not bool(result.get("pending"))
                and bool(str(result.get("sha") or "").strip())
            )
            if not landed_pr:
                return (
                    _worktree_merge_default_message(
                        result,
                        cell,
                        branch=merge_branch,
                        base_branch=merge_base_branch,
                    )
                    + ", but cleanup failed:\n"
                    + "\n".join(f"  - {err}" for err in cleanup_errors)
                ), True
            result = dict(result)
            cleanup_warning = (
                "Merge landed, but post-merge cleanup reported warnings:\n"
                + "\n".join(f"  - {err}" for err in cleanup_errors)
            )
            existing_warning = str(result.get("warning") or "").strip()
            result["warning"] = (
                f"{existing_warning}\n{cleanup_warning}"
                if existing_warning else cleanup_warning
            )
        return json.dumps(
            _worktree_merge_success_payload(
                result,
                cell,
                branch=merge_branch,
                base_branch=merge_base_branch,
            )
        ), False

    if tool_name == "rebase":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        cell = state.agents.get(agent_id)
        if not cell or not cell.worktree_path:
            return "Agent has no worktree", True

        check, error_text, blocked = (
            await _run_worktree_merge_check_with_options(
                handle_command,
                agent_id,
                allow_dirty=True,
                allow_stale_base=True,
            )
        )
        if blocked:
            return error_text, True

        conflicts_before = check.get("conflicts", [])
        result = await handle_command({
            "cmd": "worktree_rebase",
            "id": agent_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        if result and result.get("ok") is False:
            conflicts = result.get("conflicts", []) or conflicts_before
            if conflicts:
                return (
                    f"{result.get('error', 'Rebase failed')}\n\n"
                    "Conflicts:\n"
                    f"{_format_worktree_conflicts(conflicts)}\n\n"
                    "The rebase was aborted and the worktree should be "
                    "clean. Ask the human if you want a manual conflict-"
                    "resolution plan."
                ), True
            return result.get("error", "Rebase failed"), True

        postcheck, error_text, blocked = await _run_worktree_merge_check(
            handle_command, agent_id
        )
        if blocked:
            return error_text, True

        payload = {
            "type": "ok",
            "message": f"Rebased {cell.worktree_branch} onto "
                       f"{cell.worktree_base_branch}",
            "merge_ready": postcheck.get("clean", False),
            "default_message": postcheck.get("default_message", ""),
            "conflicts_before": conflicts_before,
            "conflicts_after": postcheck.get("conflicts", []),
            "boundary": postcheck.get("boundary"),
            "clean_boundary": postcheck.get("clean_boundary"),
        }
        for key in (
                "post_rebase_head_sha",
                "base_head_sha",
                "review_boundary_updated",
                "review_boundary_task_id",
                "post_rebase_evidence",
                "workflow_breach",
        ):
            if key in result:
                payload[key] = result[key]
        if isinstance(result.get("post_rebase_evidence"), dict):
            payload["post_rebase_evidence_required"] = (
                result["post_rebase_evidence"]
            )
        return json.dumps(payload), False

    if tool_name == "create_pr":
        valid_target, target_error = _validate_exactly_one_worktree_target(args)
        if not valid_target:
            return target_error, True
        driverless = _has_path_target_args(args)
        if driverless:
            payload = {
                "cmd": "worktree_create_pr",
                **_driverless_payload_from_args(
                    args,
                    caller_id=caller_id,
                    group=_engineer_group,
                ),
            }
            cell = None
        else:
            agent_ident = args.get("agent", "")
            agent_id, agent_error = _resolve_visible_agent(
                real_state, caller_kind, caller_id, agent_ident
            )
            if not agent_id:
                return agent_error, True
            cell = state.agents.get(agent_id)
            if not cell or not cell.worktree_path:
                return "Agent has no worktree", True
            payload = {"cmd": "worktree_create_pr", "id": agent_id}
        title = args.get("title", "")
        if title:
            payload["title"] = title
        body = args.get("body", "")
        if body:
            payload["body"] = body
        result = await handle_command(payload)
        if result and result.get("error"):
            error, cacheable = _format_worktree_pr_error(
                result,
                "Failed to create pull request",
            )
            if cacheable is False:
                return error, True, False
            return error, True
        pr_url = _worktree_result_pr_url(result)
        payload = {
            "type": "ok",
            "url": pr_url,
            "pr_url": pr_url,
            "message": (result or {}).get("message", "PR created"),
        }
        if (result or {}).get("pending"):
            payload["pending"] = True
        if (result or {}).get("pending_ee_pr"):
            payload["pending_ee_pr"] = True
        if isinstance((result or {}).get("nested_submodules"), dict):
            payload["nested_submodules"] = result["nested_submodules"]
        if driverless:
            payload["driverless"] = True
        phase = _worktree_result_phase(result)
        if phase:
            payload["phase"] = phase
        if isinstance((result or {}).get("pr"), dict):
            payload["pr"] = result["pr"]
        return json.dumps(payload), False

    if tool_name == "diff":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        cell = state.agents.get(agent_id)
        if not cell or not cell.worktree_path:
            return "Agent has no worktree", True
        if not cell.worktree_base_branch:
            return "Agent has no base branch configured", True

        # Review workers may share their implementer's worktree; diff follows
        # the selected cell's shared branch context, not a reviewer-local fork.
        result = await handle_command({
            "cmd": "worktree_diff",
            "id": agent_id,
            "stat_only": args.get("stat_only", False),
            "summary_only": args.get("summary_only", False),
            "paths": args.get("paths", []),
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        if args.get("summary_only", False):
            warning = str(result.get("stale_base_warning", "") or "").strip()
            if warning:
                return f"{warning}\n\n{json.dumps(result)}", False
            return json.dumps(result), False
        return result.get("diff", "No changes"), False

    if tool_name == "worktree_remove":
        valid_target, target_error = _validate_exactly_one_worktree_target(args)
        if not valid_target:
            return target_error, True
        if _has_path_target_args(args):
            payload = {
                "cmd": "worktree_remove",
                **_driverless_payload_from_args(
                    args,
                    caller_id=caller_id,
                    group=_engineer_group,
                ),
                "delete_branch": bool(args.get("delete_branch", True)),
            }
            cell = None
        else:
            agent_ident = args.get("agent", "")
            agent_id, agent_error = _resolve_visible_agent(
                real_state, caller_kind, caller_id, agent_ident
            )
            if not agent_id:
                return agent_error, True
            cell = state.agents.get(agent_id)
            if not cell or not cell.worktree_path:
                return "Agent has no worktree", True
            payload = {
                "cmd": "worktree_remove",
                "id": agent_id,
            }
        result = await handle_command(payload)
        if result and result.get("type") == "error" \
                and isinstance(result.get("worktree_remove"), dict):
            payload = {
                "type": "error",
                "message": result.get("message", "Unknown error"),
                "worktree_remove": result.get("worktree_remove"),
            }
            return json.dumps(payload), True
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        if result and not result.get("worktree_removed", False):
            payload = {
                "type": "error",
                "message": result.get(
                    "message",
                    "Worktree removal did not remove the git worktree",
                ),
                "worktree_remove": result,
            }
            return json.dumps(payload), True
        if result and result.get("ok") is False:
            payload = {
                "type": "error",
                "message": result.get(
                    "message",
                    "Worktree removed but cleanup was incomplete",
                ),
                "worktree_remove": result,
            }
            return json.dumps(payload), True
        payload = {
            "type": "ok",
            "message": (result or {}).get("message", "Worktree removed"),
        }
        if isinstance(result, dict):
            payload["worktree_remove"] = result
        return json.dumps(payload), False

    if tool_name == "worktree_adopt":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        payload = {
            "cmd": "worktree_adopt",
            "id": agent_id,
            **_driverless_payload_from_args(
                args,
                caller_id=caller_id,
                group=_engineer_group,
            ),
            "relaunch": bool(args.get("relaunch", False)),
        }
        result = await handle_command(payload)
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps(result or {"type": "ok"}), False

    if tool_name == "worktree_advance_boundary":
        valid_target, target_error = _validate_exactly_one_worktree_target(args)
        if not valid_target:
            return target_error, True
        if not str(args.get("verification_note", "") or "").strip():
            return "verification_note is required", True
        if not str(args.get("expected_previous_head", "") or "").strip():
            return "expected_previous_head is required", True
        if _has_path_target_args(args):
            payload = {
                "cmd": "worktree_advance_boundary",
                **_driverless_payload_from_args(
                    args,
                    caller_id=caller_id,
                    group=_engineer_group,
                ),
            }
        else:
            agent_ident = args.get("agent", "")
            agent_id, agent_error = _resolve_visible_agent(
                real_state, caller_kind, caller_id, agent_ident
            )
            if not agent_id:
                return agent_error, True
            payload = {"cmd": "worktree_advance_boundary", "id": agent_id, "group": _engineer_group}
        for key in (
            "expected_previous_head",
            "expected_new_head",
            "verification_note",
            "reason",
        ):
            if key in args:
                payload[key] = args[key]
        payload["actor_agent_id"] = str(caller_id or "").strip()
        result = await handle_command(payload)
        if result and not result.get("ok"):
            return json.dumps(result), True
        return json.dumps(result or {"type": "ok"}), False

    if tool_name == "worktree_checkpoint":
        agent_ident = args.get("agent", "")
        agent_id, agent_error = _resolve_visible_agent(
            real_state, caller_kind, caller_id, agent_ident
        )
        if not agent_id:
            return agent_error, True
        cell = state.agents.get(agent_id)
        if not cell or not cell.worktree_path:
            return "Agent has no worktree", True
        result = await handle_command({
            "cmd": "worktree_checkpoint",
            "id": agent_id,
        })
        if result and result.get("type") == "error":
            return result.get("message", "Unknown error"), True
        return json.dumps({"type": "ok",
                          "message": "Checkpoint created"}), False

    return UNHANDLED
