"""Persistent state hydration and relationship rebuilding."""

from __future__ import annotations

from .state import (
    ARCHIVED_LANE, AgentCell, AgentDigestSettings, AgentMessageLoop,
    AutoDispatchQueueEntry, BoardTask, EngineerSettings, GlobalSettings,
    GroupSettings, Schedule, TASK_DISPATCH_STATE_LIVE,
    TASK_DISPATCH_STATE_QUEUED, _DEFAULT_LANES, _ENGINEER_WORKLOG_LIMIT,
    _EPHEMERAL_FIELDS, _normalize_board_lanes, _normalize_board_sync,
    _normalize_board_sync_github_settings, _normalize_board_sync_provider,
    _normalize_engineer_hint_snoozes, _normalize_messages_thread,
    _normalize_task_dispatch_state, _normalize_verification_fields,
    _normalize_worktree_boundary, log, normalize_architect_digest_verbosity,
    normalize_architect_enabled_events, normalize_artifacts,
    normalize_attachments, normalize_default_worker_concurrency,
    normalize_engineer_autonomy_mode, normalize_engineer_digest_verbosity,
    normalize_engineer_escalation_style, normalize_engineer_merge_mode,
    normalize_engineer_same_agent_follow_up_preference,
    normalize_engineer_wave_size_preference, normalize_group_prefix,
    normalize_guidance_hint_cadence, normalize_worktree_merge_cleanup,
)


class StateLoadingMixin:
    def load(self):
        from .config import DB_FILE, STATE_FILE
        if not self.db:
            return

        # Migration: if DB is empty but state.json exists, import it
        if not self.db.has_data() and STATE_FILE.exists():
            self.db.migrate_from_json(STATE_FILE)

        data = self.db.load_all()

        try:
            fields = set(AgentCell.__dataclass_fields__)
            for aid, raw in data.get("agents", {}).items():
                filtered = {k: v for k, v in raw.items() if k in fields}
                cell = AgentCell(**filtered)
                cell.status = "stopped"
                for f in _EPHEMERAL_FIELDS:
                    setattr(cell, f, type(getattr(cell, f))())
                # Platform extension outcomes are intentionally not persisted
                # with the frozen snapshot. Re-resolve them once during daemon
                # restore, before this stopped cell can serve any MCP call.
                from .agent_classes import (
                    agent_class_definition_by_id,
                    launch_frozen_platform_authority_for_definition,
                )
                class_id = str(getattr(cell, "effective_agent_class_id", "") or "")
                base_dir = self._agent_class_base_dir_for_cell(cell)
                definition = agent_class_definition_by_id(
                    class_id, base_dir=base_dir,
                ) if class_id else None
                cell.effective_agent_class_platform_authority = (
                    launch_frozen_platform_authority_for_definition(definition)
                )
                self.agents[aid] = cell
            self.groups = data.get("groups", {})
            for gname in list(self.groups):
                self.groups[gname] = [
                    aid for aid in self.groups[gname] if aid in self.agents
                ]
            # Restore group settings (backward-compat: missing key → empty)
            gs_fields = set(GroupSettings.__dataclass_fields__)
            for gname, raw in data.get("group_settings", {}).items():
                if gname in self.groups:
                    filtered = {k: v for k, v in raw.items() if k in gs_fields}
                    if "worktree_merge_cleanup" in filtered:
                        filtered["worktree_merge_cleanup"] = (
                            normalize_worktree_merge_cleanup(
                                filtered["worktree_merge_cleanup"])
                        )
                    if "engineer_merge_mode" in filtered:
                        filtered["engineer_merge_mode"] = (
                            normalize_engineer_merge_mode(
                                filtered["engineer_merge_mode"])
                        )
                    if "guidance_hint_cadence" in filtered:
                        filtered["guidance_hint_cadence"] = (
                            normalize_guidance_hint_cadence(
                                filtered["guidance_hint_cadence"])
                        )
                    if "engineer_hint_snoozes" in filtered:
                        filtered["engineer_hint_snoozes"] = (
                            _normalize_engineer_hint_snoozes(
                                filtered["engineer_hint_snoozes"])
                        )
                    if "board_sync_provider" in filtered:
                        filtered["board_sync_provider"] = (
                            _normalize_board_sync_provider(
                                filtered["board_sync_provider"])
                        )
                    if "board_sync_enabled" in filtered:
                        filtered["board_sync_enabled"] = bool(
                            filtered["board_sync_enabled"])
                    if "board_sync_github" in filtered:
                        filtered["board_sync_github"] = (
                            _normalize_board_sync_github_settings(
                                filtered["board_sync_github"])
                        )
                    self._normalize_architect_settings_mapping(
                        filtered,
                        strict=False,
                    )
                    self.group_settings[gname] = GroupSettings(**filtered)
            # Promote orphaned children whose parent was deleted
            for aid, cell in self.agents.items():
                if cell.parent_id and cell.parent_id not in self.agents:
                    log.warning("Orphaned child '%s' (parent %s gone) "
                                "— promoting to standalone", cell.name,
                                cell.parent_id)
                    cell.parent_id = ""
                    if cell.group in self.groups:
                        self.groups[cell.group].append(aid)
            self._rebuild_children()
            # Global settings
            gs_raw = data.get("global_settings")
            if gs_raw:
                gls_fields = set(GlobalSettings.__dataclass_fields__)
                gls_filtered = {k: v for k, v in gs_raw.items()
                                if k in gls_fields}
                self.global_settings = GlobalSettings(**gls_filtered)
                self.metrics_collector.set_enabled(
                    self.global_settings.metrics_enabled
                )
            # Board state — use global default_lanes as fallback
            default = (self.global_settings.default_lanes
                       or list(_DEFAULT_LANES))
            self.board_lanes = _normalize_board_lanes(
                data.get("board_lanes") or default
            )
            bt_fields = set(BoardTask.__dataclass_fields__)
            first_group = next(iter(self.groups), "")
            for tid, raw in data.get("board_tasks", {}).items():
                # Migrate renamed fields: title→task, description→instructions
                if "title" in raw and "task" not in raw:
                    raw["task"] = raw.pop("title")
                if "description" in raw and "instructions" not in raw:
                    raw["instructions"] = raw.pop("description")
                if not raw.get("group"):
                    raw["group"] = first_group
                raw["attachments"] = normalize_attachments(
                    raw.get("attachments", []))
                raw["artifacts"] = normalize_artifacts(
                    raw.get("artifacts", []))
                labels = list(raw.get("labels", []) or [])
                if "torque:archived" in labels and raw.get("lane") != ARCHIVED_LANE:
                    prior_lane = raw.get("lane") or ""
                    raw["lane"] = ARCHIVED_LANE
                    raw["archived_from_lane"] = raw.get("archived_from_lane") \
                        or prior_lane
                    raw["archived_at"] = raw.get("archived_at") \
                        or raw.get("updated_at", "") or raw.get("created_at", "")
                    labels = [label for label in labels
                              if label != "torque:archived"]
                raw["labels"] = labels
                raw["messages_thread"] = _normalize_messages_thread(
                    raw.get("messages_thread", [])
                )
                if "dispatch_state" not in raw or not raw.get("dispatch_state"):
                    raw["dispatch_state"] = (
                        TASK_DISPATCH_STATE_LIVE
                        if str(raw.get("agent_id", "") or "").strip()
                        else TASK_DISPATCH_STATE_QUEUED
                    )
                else:
                    raw["dispatch_state"] = _normalize_task_dispatch_state(
                        raw.get("dispatch_state")
                    )
                _normalize_verification_fields(raw)
                raw["board_sync"] = _normalize_board_sync(
                    raw.get("board_sync", {})
                )
                raw["worktree_boundary"] = _normalize_worktree_boundary(
                    raw.get("worktree_boundary", {})
                )
                resume_after = raw.get("resume_after_boundary_task_id", "")
                if resume_after is None:
                    resume_after = ""
                if not isinstance(resume_after, str):
                    resume_after = str(resume_after)
                raw["resume_after_boundary_task_id"] = resume_after.strip()
                filtered = {k: v for k, v in raw.items() if k in bt_fields}
                self.board_tasks[tid] = BoardTask(**filtered)
            self.task_id_aliases = {
                str(old_id or ""): str(new_id or "")
                for old_id, new_id in (data.get("task_id_aliases", {}) or {}).items()
                if old_id and new_id
            }
            self.task_id_counters = {
                normalize_group_prefix(prefix): max(1, int(next_root or 1))
                for prefix, next_root in (data.get("task_id_counters", {}) or {}).items()
                if prefix
            }
            self.pipeline_task_counters = {
                str(root_id or ""): max(1, int(next_child or 1))
                for root_id, next_child in (
                    data.get("pipeline_task_counters", {}) or {}
                ).items()
                if root_id
            }
            self._rebuild_task_indexes()
            self._clear_engineer_queue_empty_for_active_tasks()
            self.cleanup_stale_boundary_successors(emit=False)
            historical_ghosts = self.cleanup_resolved_engineer_message_followups(
                emit=False
            )
            log.info(
                "Retroactive cleanup: %d historical ghosts expired.",
                historical_ghosts,
            )
            for aid, cell in self.agents.items():
                cell.pending_engineer_message = bool(
                    self.agent_pending_engineer_reply_tasks(aid)
                )
            self.seed_peer_message_caches(emit=False)
            self.seed_direct_message_caches(emit=False)
            self.seed_agent_peer_threads(emit=False)
            reconciled_history = self.history_reconcile_tombstoned_agents()
            if reconciled_history:
                log.info(
                    "Reconciled %d tombstoned agent history rows.",
                    reconciled_history,
                )
            # panel_active: new key; backward compat from board_panel_open
            pa = data.get("panel_active", "")
            if not pa and data.get("board_panel_open"):
                pa = "board"
            self.panel_active = pa
            self.board_panel_height = data.get("board_panel_height", 0)
            self.active_group = str(data.get("active_group", "") or "")
            self.selected_principal_id = str(
                data.get("selected_principal_id", "") or ""
            )
            self.selected_agent_id = str(
                data.get("selected_agent_id", "") or ""
            )
            self.standalone_panel_layout = data.get(
                "standalone_panel_layout", {}
            ) or {}
            self.detached_panels = data.get("detached_panels", {}) or {}
            self.window_bounds = data.get("window_bounds", {}) or {}
            try:
                self.workspace_sidebar_width = int(
                    data.get("workspace_sidebar_width", 0) or 0
                )
            except (TypeError, ValueError):
                self.workspace_sidebar_width = 0
            try:
                self.terminal_direct_messages_height = max(
                    0,
                    int(data.get("terminal_direct_messages_height", 0) or 0),
                )
            except (TypeError, ValueError):
                self.terminal_direct_messages_height = 0
            try:
                self.terminal_compose_height = max(
                    0,
                    int(data.get("terminal_compose_height", 0) or 0),
                )
            except (TypeError, ValueError):
                self.terminal_compose_height = 0
            try:
                self.engineer_panel_split_fraction = float(
                    data.get("engineer_panel_split_fraction", 0.30) or 0.30
                )
            except (TypeError, ValueError):
                self.engineer_panel_split_fraction = 0.30
            try:
                self.context_panel_split_ratio = float(
                    data.get("context_panel_split_ratio", 0.38) or 0.38
                )
            except (TypeError, ValueError):
                self.context_panel_split_ratio = 0.38
            self.supervisor_panel_state = data.get(
                "supervisor_panel_state", {}
            ) or {}
            self.events_dismissed_attention = data.get(
                "events_dismissed_attention", {}
            ) or {}
            self.board_filters_by_group = data.get(
                "board_filters_by_group", {}
            ) or {}
            self.board_selected_lanes_by_group = data.get(
                "board_selected_lanes_by_group", {}
            ) or {}
            self.board_hidden_wide_lanes_by_group = data.get(
                "board_hidden_wide_lanes_by_group", {}
            ) or {}
            self.board_saved_views_by_group = data.get(
                "board_saved_views_by_group", {}
            ) or {}
            self.board_lane_sorts_by_group = data.get(
                "board_lane_sorts_by_group", {}
            ) or {}
            self.board_card_density_by_group = data.get(
                "board_card_density_by_group", {}
            ) or {}
            # Slug migration: generate slugs for resources that lack them.
            # Groups first (terminal slugs may reference group slugs),
            # then agents (terminal slugs reference parent agent slugs),
            # then terminals.
            slug_dirty = False
            self.group_slugs = data.get("group_slugs", {})
            for gname in self.groups:
                if gname not in self.group_slugs:
                    self.group_slugs[gname] = self._unique_group_slug(gname)
                    slug_dirty = True
            # Agents (non-terminal) first
            for aid, cell in self.agents.items():
                if cell.cell_type != "terminal" and not cell.slug:
                    cell.slug = self._unique_agent_slug(cell.name,
                                                       exclude_id=aid)
                    self._db_save_agent(cell)
                    slug_dirty = True
            # Terminals: generate or migrate to parent:name format
            for aid, cell in self.agents.items():
                if cell.cell_type == "terminal" and (
                        not cell.slug or ":" not in cell.slug):
                    cell.slug = self._unique_terminal_slug(
                        cell.name, parent_id=cell.parent_id,
                        group=cell.group, exclude_id=aid)
                    self._db_save_agent(cell)
                    slug_dirty = True
            for tid, task in self.board_tasks.items():
                if not task.slug:
                    task.slug = self._unique_task_slug(task.task,
                                                      exclude_id=tid)
                    self._db_save_task(task)
                    slug_dirty = True
            # Schedules
            sched_fields = set(Schedule.__dataclass_fields__)
            for sid, raw in data.get("schedules", {}).items():
                filtered = {k: v for k, v in raw.items()
                            if k in sched_fields}
                self.schedules[sid] = Schedule(**filtered)
            for sid, sched in self.schedules.items():
                if not sched.slug:
                    sched.slug = self._unique_schedule_slug(
                        sched.name, exclude_id=sid)
                    self._db_save_schedule(sched)
                    slug_dirty = True
            loop_fields = set(AgentMessageLoop.__dataclass_fields__)
            for loop_id, raw in data.get("agent_message_loops", {}).items():
                if not isinstance(raw, dict):
                    continue
                filtered = {k: v for k, v in raw.items()
                            if k in loop_fields}
                if not filtered.get("id"):
                    filtered["id"] = str(loop_id or "").strip()
                if not filtered.get("id") or not filtered.get("agent_id"):
                    continue
                self.agent_message_loops[filtered["id"]] = (
                    AgentMessageLoop(**filtered)
                )
            adq_fields = set(AutoDispatchQueueEntry.__dataclass_fields__)
            for gname, entries in data.get("auto_dispatch_queues", {}).items():
                if gname not in self.groups or not isinstance(entries, list):
                    continue
                restored = []
                for raw in entries:
                    if not isinstance(raw, dict):
                        continue
                    filtered = {
                        k: v for k, v in raw.items() if k in adq_fields
                    }
                    if not filtered.get("task_id"):
                        continue
                    restored.append(AutoDispatchQueueEntry(**filtered))
                if restored:
                    self.auto_dispatch_queues[gname] = restored
            self.cleanup_stale_auto_dispatch_queue()
            if slug_dirty:
                self._db_save_groups()
                log.info("Generated slugs for existing resources")
            # Engineer settings
            if self.db:
                ws_fields = set(EngineerSettings.__dataclass_fields__)
                for gname, raw in self.db.load_all_engineer_settings().items():
                    filtered = {k: v for k, v in raw.items() if k in ws_fields}
                    if "autonomy_mode" in filtered:
                        filtered["autonomy_mode"] = (
                            normalize_engineer_autonomy_mode(
                                filtered["autonomy_mode"])
                        )
                    if "default_worker_concurrency" in filtered:
                        filtered["default_worker_concurrency"] = (
                            normalize_default_worker_concurrency(
                                filtered["default_worker_concurrency"])
                        )
                    if "wave_size_preference" in filtered:
                        filtered["wave_size_preference"] = (
                            normalize_engineer_wave_size_preference(
                                filtered["wave_size_preference"])
                        )
                    if "same_agent_follow_up_preference" in filtered:
                        filtered["same_agent_follow_up_preference"] = (
                            normalize_engineer_same_agent_follow_up_preference(
                                filtered["same_agent_follow_up_preference"])
                        )
                    if "digest_verbosity" in filtered:
                        filtered["digest_verbosity"] = (
                            normalize_engineer_digest_verbosity(
                                filtered["digest_verbosity"])
                        )
                    if "escalation_style" in filtered:
                        filtered["escalation_style"] = (
                            normalize_engineer_escalation_style(
                                filtered["escalation_style"])
                        )
                    self.engineer_settings[gname] = EngineerSettings(**filtered)
                ads_fields = set(AgentDigestSettings.__dataclass_fields__)
                for agent_id, raw in self.db.load_all_agent_digest_settings().items():
                    if agent_id not in self.agents:
                        continue
                    filtered = {
                        k: v for k, v in raw.items() if k in ads_fields
                    }
                    cell = self.agents.get(agent_id)
                    is_architect = (
                        str(getattr(cell, "kind", "") or "").strip()
                        == "architect"
                    )
                    if "digest_verbosity" in filtered:
                        filtered["digest_verbosity"] = (
                            normalize_architect_digest_verbosity(
                                filtered["digest_verbosity"]
                            )
                            if is_architect
                            else normalize_engineer_digest_verbosity(
                                filtered["digest_verbosity"]
                            )
                        )
                    if is_architect and "enabled_events" in filtered:
                        filtered["enabled_events"] = (
                            normalize_architect_enabled_events(
                                filtered["enabled_events"]
                            )
                        )
                    self.agent_digest_settings[agent_id] = (
                        AgentDigestSettings(**filtered)
                    )
                self._backfill_architect_digest_defaults()
                self._backfill_architect_suppress_empty_once()
                for gname in self.groups:
                    entries = self.db.load_engineer_task_log(
                        gname,
                        limit=_ENGINEER_WORKLOG_LIMIT,
                    )
                    if entries:
                        self.engineer_worklog[gname] = entries
            cleaned = self.cleanup_orphaned_attention(emit=False)
            self.recompute_task_health(emit=False, persist=False)
            self.reconcile_task_watches()
            self.reconcile_reminders()
            if cleaned["asks"] or cleaned["engineer_questions"]:
                log.info(
                    "Expired %d orphaned ask(s) and cleared %d stale engineer question(s)",
                    cleaned["asks"],
                    cleaned["engineer_questions"],
                )
        except (TypeError, KeyError):
            log.exception("Failed to load state from SQLite")

    def _rebuild_children(self):
        """Rebuild the parent→children index from parent_id fields."""
        self._children = {}
        for aid, cell in self.agents.items():
            if cell.cell_type == "agent":
                self._children.setdefault(aid, [])
        for aid, cell in self.agents.items():
            if cell.parent_id and cell.parent_id in self._children:
                self._children[cell.parent_id].append(aid)
