"""Global settings, identity allocation, and agent/group lifecycle state behavior."""

from __future__ import annotations

from .state import (
    AGENT_TOMBSTONE_RETENTION_SECONDS,
    AI_DEFAULT_EMBEDDING_MODEL,
    AgentCell,
    DEFAULT_COMMAND,
    GlobalSettings,
    Optional,
    _safe_float,
    _slugify,
    _unique_slug,
    asdict,
    board_task_is_archived,
    coerce_perceived_empty_threshold,
    coerce_perceived_empty_window_seconds,
    format_derived_task_id,
    format_root_task_id,
    json,
    log,
    normalize_ai_boot_summary_max_refreshes_per_hour,
    normalize_ai_boot_summary_min_interval_seconds,
    normalize_ai_embedding_runtime,
    normalize_ai_generation_provider,
    normalize_ai_index_corpus,
    normalize_ai_text,
    normalize_event_ingest_max_days,
    normalize_event_ingest_max_rows,
    normalize_group_prefix,
    normalize_mcp_call_log_args_capture,
    normalize_mcp_call_log_full_capture_tools,
    normalize_relay_enabled,
    normalize_relay_text,
    normalize_status_bar_visibility,
    normalize_xterm_scrollback,
    os,
    parse_task_id,
    time,
    uuid,
)


class StateLifecycleMixin:
    def get_default_command(self) -> str:
        """Return the effective default boot command.

        Priority: global_settings > env var > 'claude'
        """
        return self.global_settings.default_command or DEFAULT_COMMAND

    def _normalize_global_settings_updates(self, fields: dict) -> dict:
        valid = set(GlobalSettings.__dataclass_fields__)
        updates = {}
        for key, value in fields.items():
            if key in valid:
                if key == "xterm_scrollback":
                    value = normalize_xterm_scrollback(value, strict=True)
                elif key == "event_ingest_max_rows":
                    value = normalize_event_ingest_max_rows(value)
                elif key == "event_ingest_max_days":
                    value = normalize_event_ingest_max_days(value)
                elif key == "metrics_enabled":
                    value = normalize_relay_enabled(value)
                elif key == "mcp_call_log_args_capture":
                    value = normalize_mcp_call_log_args_capture(value)
                elif key == "mcp_call_log_full_capture_tools":
                    value = normalize_mcp_call_log_full_capture_tools(value)
                elif key == "perceived_empty_probe_threshold":
                    value = coerce_perceived_empty_threshold(value)
                elif key == "perceived_empty_window_seconds":
                    value = coerce_perceived_empty_window_seconds(value)
                elif key == "status_bar_visibility":
                    value = normalize_status_bar_visibility(value)
                elif key == "relay_enabled":
                    value = normalize_relay_enabled(value)
                elif key in (
                    "relay_url",
                    "relay_daemon_id",
                    "relay_credential_id",
                    "relay_private_key_path",
                ):
                    value = normalize_relay_text(value)
                elif key == "ai_enabled":
                    value = normalize_relay_enabled(value)
                elif key == "ai_generation_provider":
                    value = normalize_ai_generation_provider(value)
                elif key in (
                    "ai_anthropic_model",
                    "ai_openai_compatible_base_url",
                    "ai_openai_compatible_model",
                ):
                    value = normalize_ai_text(value)
                elif key == "ai_embedding_model":
                    value = (
                        normalize_ai_text(value)
                        or AI_DEFAULT_EMBEDDING_MODEL
                    )
                elif key == "ai_embedding_runtime":
                    value = normalize_ai_embedding_runtime(value)
                elif key == "ai_index_corpus":
                    value = normalize_ai_index_corpus(value)
                elif key == "ai_boot_summary_enabled":
                    value = normalize_relay_enabled(value)
                elif key == "ai_boot_summary_min_interval_seconds":
                    value = normalize_ai_boot_summary_min_interval_seconds(value)
                elif key == "ai_boot_summary_max_refreshes_per_hour":
                    value = normalize_ai_boot_summary_max_refreshes_per_hour(value)
                updates[key] = value
        return updates

    def _apply_global_settings_updates(self, updates: dict) -> None:
        changed_keys = []
        for key, value in updates.items():
            if getattr(self.global_settings, key, None) != value:
                changed_keys.append(key)
            setattr(self.global_settings, key, value)
        if "metrics_enabled" in updates:
            self.metrics_collector.set_enabled(
                self.global_settings.metrics_enabled
            )
        self._emit(
            "global_settings_update",
            **asdict(self.global_settings),
            changed_keys=sorted(changed_keys),
        )

    def update_global_settings(self, **fields):
        """Update global settings fields."""
        updates = self._normalize_global_settings_updates(fields)
        self._apply_global_settings_updates(updates)
        self._db_save_global_settings()

    async def update_global_settings_durable(self, **fields):
        """Update global settings only after the durable write succeeds.

        Most settings writes are UI best-effort and can use the fire-and-forget
        async DB queue. Daemon credential provisioning is different: the relay
        has already accepted a new credential and the private key has been
        committed to disk, so a failed local Settings write must surface a
        recovery handle instead of mutating in-memory state and restarting the
        connector as though the credential were saved.
        """
        updates = self._normalize_global_settings_updates(fields)
        if self.db:
            candidate = GlobalSettings(
                **{**asdict(self.global_settings), **updates}
            )
            save_durable = getattr(self.db, "save_global_settings_durable", None)
            if callable(save_durable):
                await save_durable(candidate)
            else:
                enqueue = getattr(self.db, "_enqueue_async_write", None)
                if callable(enqueue):
                    await enqueue(
                        "global_settings",
                        "save_global_settings",
                        candidate,
                    )
                else:
                    self.db.save_global_settings(candidate)
        self._apply_global_settings_updates(updates)

    def next_cell_name(self, group: str, cell_type: str) -> str:
        """Generate the next auto-name based on group prefix settings."""
        gs = self.get_group_settings(group)
        prefix = gs.terminal_name_prefix if cell_type == "terminal" else ""
        if not prefix:
            prefix = "Agent" if cell_type == "agent" else "Terminal"
        existing = {a.name for a in self.agents.values()
                    if a.group == group}
        i = 1
        while f"{prefix} {i}" in existing:
            i += 1
        return f"{prefix} {i}"

    def _unique_agent_slug(self, name: str, exclude_id: str = "") -> str:
        """Generate a unique slug for an agent."""
        base = _slugify(name)
        existing = {c.slug for c in self.agents.values()
                    if c.id != exclude_id and c.slug}
        return _unique_slug(base, existing)

    def _unique_terminal_slug(self, name: str, parent_id: str = "",
                              group: str = "",
                              exclude_id: str = "") -> str:
        """Generate a unique slug for a terminal: ``parent:name``."""
        if parent_id:
            parent = self.agents.get(parent_id)
            prefix = parent.slug if parent else ""
        else:
            prefix = self.group_slugs.get(group, _slugify(group))
        base = _slugify(name)
        full = f"{prefix}:{base}" if prefix else base
        existing = {c.slug for c in self.agents.values()
                    if c.id != exclude_id and c.slug}
        return _unique_slug(full, existing)

    def _unique_group_slug(self, name: str, exclude_name: str = "") -> str:
        """Generate a unique slug for a group."""
        base = _slugify(name)
        existing = {s for n, s in self.group_slugs.items()
                    if n != exclude_name and s}
        return _unique_slug(base, existing)

    def _unique_task_slug(self, task_text: str, exclude_id: str = "") -> str:
        """Generate a unique slug for a board task."""
        base = _slugify(task_text)
        existing = {t.slug for t in self.board_tasks.values()
                    if t.id != exclude_id and t.slug}
        return _unique_slug(base, existing)

    def normalized_group_prefix(self, group_name: str) -> str:
        return normalize_group_prefix(group_name)

    def group_prefix_conflict(self, group_name: str,
                              exclude_name: str = "") -> str:
        wanted = self.normalized_group_prefix(group_name)
        for existing_name in self.groups:
            if existing_name == exclude_name:
                continue
            if self.normalized_group_prefix(existing_name) == wanted:
                return existing_name
        return ""

    def resolve_task_alias(self, task_id: str) -> str:
        """Return the canonical task ID for ``task_id``.

        Legacy aliases are the compatibility boundary for historical IDs.
        Aliases intentionally take precedence over an exact in-memory task row
        with the same ID: archived literal rows may still exist in SQLite, but
        normal reads and writes should target the live canonical task that the
        alias names.  Follow short alias chains defensively and stop on cycles.
        """
        value = str(task_id or "").strip()
        seen = set()
        while value and value in self.task_id_aliases and value not in seen:
            seen.add(value)
            next_value = str(self.task_id_aliases.get(value, "") or "").strip()
            if not next_value or next_value == value:
                break
            value = next_value
        return value

    def resolve_board_task_id(self, identifier: str, *,
                              allow_prefix: bool = True) -> str:
        """Resolve a board task ID/alias/prefix to a live canonical ID.

        The alias map is authoritative.  If an identifier is an alias whose
        target is missing from in-memory state, return an empty string instead
        of falling back to a literal archived row with the same ID.
        """
        ident = str(identifier or "").strip()
        if not ident:
            return ""

        aliased = self.resolve_task_alias(ident)
        if aliased != ident:
            return aliased if aliased in self.board_tasks else ""

        if ident in self.board_tasks:
            return ident

        if not allow_prefix:
            return ""

        matches: list[str] = []
        seen: set[str] = set()

        # Prefixes can match legacy aliases as well as canonical IDs.  When a
        # literal ID is also an alias key, hide the archived literal row and
        # expose only the alias target.
        for legacy_id in sorted(self.task_id_aliases):
            if not legacy_id.startswith(ident):
                continue
            target_id = self.resolve_task_alias(legacy_id)
            if target_id in self.board_tasks and target_id not in seen:
                matches.append(target_id)
                seen.add(target_id)

        hidden_literal_ids = set(self.task_id_aliases)
        for task_id in sorted(self.board_tasks):
            if task_id in hidden_literal_ids:
                continue
            if task_id.startswith(ident) and task_id not in seen:
                matches.append(task_id)
                seen.add(task_id)

        if len(matches) == 1:
            return matches[0]
        return ""

    def _db_board_task_exists(self, task_id: str) -> bool:
        if not self.db:
            return False
        tid = str(task_id or "").strip()
        if not tid:
            return False
        try:
            exists = getattr(self.db, "board_task_exists", None)
            if callable(exists):
                return bool(exists(tid))
            conn = getattr(self.db, "_conn", None)
            if conn is None:
                return False
            row = conn.execute(
                "SELECT 1 FROM board_tasks WHERE id=? LIMIT 1",
                (tid,),
            ).fetchone()
            return bool(row)
        except Exception:
            log.exception("Failed to check persisted task %s", tid)
            return False

    def ensure_board_task_persisted(self, task_id: str) -> bool:
        """Persist an in-memory task if its canonical row is absent in DB."""
        tid = self.resolve_task_alias(task_id)
        task = self.board_tasks.get(tid)
        if not task or not self.db:
            return False
        if self._db_board_task_exists(tid):
            return False
        self._db_save_task(task)
        return True

    def persist_missing_aliased_tasks(self) -> list[str]:
        """Persist aliased canonical tasks that only exist in memory."""
        persisted: list[str] = []
        for legacy_id in sorted(self.task_id_aliases):
            task_id = self.resolve_task_alias(legacy_id)
            if task_id and task_id in self.board_tasks:
                if self.ensure_board_task_persisted(task_id):
                    persisted.append(task_id)
        return persisted

    def _new_ephemeral_task_id(self) -> str:
        while True:
            tid = uuid.uuid4().hex[:8]
            if tid in self.board_tasks:
                continue
            if tid in self.task_id_aliases:
                continue
            if tid in set(self.task_id_aliases.values()):
                continue
            return tid

    def _alias_or_use_task_id(self, candidate_id: str) -> tuple[str, str] | None:
        """Return ``(task_id, alias_id)`` for a requested/candidate ID.

        If the candidate collides with an archived literal row, keep the
        archived row intact and create a hash-primary-key task addressed by the
        literal ID alias.  Non-archived collisions are rejected so callers do
        not accidentally hide a live task.
        """
        candidate = str(candidate_id or "").strip()
        if not candidate:
            return None
        existing = self.board_tasks.get(candidate)
        if existing:
            if board_task_is_archived(existing):
                return self._new_ephemeral_task_id(), candidate
            return None
        if candidate in self.task_id_aliases:
            target = self.resolve_task_alias(candidate)
            if target not in self.board_tasks:
                return self._new_ephemeral_task_id(), candidate
            return None
        return candidate, ""

    def _allocate_root_task_id(self, group_name: str) -> str:
        prefix = self.normalized_group_prefix(group_name)
        next_root = max(1, int(self.task_id_counters.get(prefix, 1) or 1))
        self.task_id_counters[prefix] = next_root + 1
        self._db_save_task_id_counter(prefix)
        return format_root_task_id(prefix, next_root)

    def _allocate_derived_task_id(self, group_name: str, root_task_id: str) -> str:
        root_id = self.resolve_task_alias(root_task_id)
        parsed = parse_task_id(root_id)
        if not parsed:
            raise ValueError(f"Cannot derive from non-canonical root ID: {root_task_id}")
        prefix = self.normalized_group_prefix(group_name)
        next_child = max(
            1,
            int(self.pipeline_task_counters.get(root_id, 1) or 1),
        )
        self.pipeline_task_counters[root_id] = next_child + 1
        self._db_save_pipeline_task_counter(root_id)
        return format_derived_task_id(prefix, parsed["root_number"], next_child)

    def add_group(self, name: str):
        if name and name not in self.groups \
                and not self.group_prefix_conflict(name):
            self.groups[name] = []
            self.group_slugs[name] = self._unique_group_slug(name)
            self._emit_group(name)
            self._emit("groups_reorder", groups=list(self.groups.keys()))
            self._db_save_groups()

    def remove_group(self, name: str) -> list[AgentCell]:
        removed: list[AgentCell] = []
        if name in self.groups:
            for aid in self.groups[name]:
                cell = self.agents.pop(aid, None)
                if cell:
                    removed.append(cell)
                    self._emit("agent_remove", id=aid,
                               group=cell.group,
                               cell_type=cell.cell_type)
                    # Cascade: remove child terminals
                    for child_id in self._children.pop(aid, []):
                        child = self.agents.pop(child_id, None)
                        if child:
                            removed.append(child)
                            self._emit("agent_remove", id=child_id,
                                       group=child.group,
                                       cell_type=child.cell_type)
            del self.groups[name]
            self.group_slugs.pop(name, None)
            self.group_settings.pop(name, None)
            if name in self.board_filters_by_group:
                del self.board_filters_by_group[name]
                self._emit("ui_update", key="board_filters_by_group",
                           value=self.board_filters_by_group)
                self._db_save_ui(
                    "board_filters_by_group",
                    json.dumps(self.board_filters_by_group),
                )
            if self.active_group == name:
                self.active_group = ""
                self._emit("ui_update", key="active_group",
                           value=self.active_group)
                self._db_save_ui("active_group", self.active_group)
            if name in self.board_selected_lanes_by_group:
                del self.board_selected_lanes_by_group[name]
                self._emit("ui_update", key="board_selected_lanes_by_group",
                           value=self.board_selected_lanes_by_group)
                self._db_save_ui(
                    "board_selected_lanes_by_group",
                    json.dumps(self.board_selected_lanes_by_group),
                )
            if name in self.board_hidden_wide_lanes_by_group:
                del self.board_hidden_wide_lanes_by_group[name]
                self._emit("ui_update",
                           key="board_hidden_wide_lanes_by_group",
                           value=self.board_hidden_wide_lanes_by_group)
                self._db_save_ui(
                    "board_hidden_wide_lanes_by_group",
                    json.dumps(self.board_hidden_wide_lanes_by_group),
                )
            if name in self.board_saved_views_by_group:
                del self.board_saved_views_by_group[name]
                self._emit("ui_update", key="board_saved_views_by_group",
                           value=self.board_saved_views_by_group)
                self._db_save_ui(
                    "board_saved_views_by_group",
                    json.dumps(self.board_saved_views_by_group),
                )
            if name in self.board_lane_sorts_by_group:
                del self.board_lane_sorts_by_group[name]
                self._emit("ui_update", key="board_lane_sorts_by_group",
                           value=self.board_lane_sorts_by_group)
                self._db_save_ui(
                    "board_lane_sorts_by_group",
                    json.dumps(self.board_lane_sorts_by_group),
                )
            if name in self.board_card_density_by_group:
                del self.board_card_density_by_group[name]
                self._emit("ui_update", key="board_card_density_by_group",
                           value=self.board_card_density_by_group)
                self._db_save_ui(
                    "board_card_density_by_group",
                    json.dumps(self.board_card_density_by_group),
                )
            self.auto_dispatch_queues.pop(name, None)
            self._db_delete_auto_dispatch_queue(name)
            self.engineer_settings.pop(name, None)
            self.engineer_worklog.pop(name, None)
            for r in removed:
                if r.cell_type == "agent":
                    self.history_remove_agent(r)
                self.delete_agent_digest_settings(r.id)
            if self.db:
                self.db.delete_engineer_settings(name)
            self._emit("group_remove", name=name)
            self._emit("groups_reorder", groups=list(self.groups.keys()))
            for r in removed:
                self._db_delete_agent(r.id)
            self._db_delete_group(name)
            self._db_save_groups()
        return removed

    def rename_group(self, old: str, new: str):
        if old in self.groups and new and new not in self.groups \
                and not self.group_prefix_conflict(new, exclude_name=old):
            self.groups[new] = self.groups.pop(old)
            self.group_slugs.pop(old, None)
            self.group_slugs[new] = self._unique_group_slug(new)
            if old in self.group_settings:
                self.group_settings[new] = self.group_settings.pop(old)
            if old in self.engineer_worklog:
                self.engineer_worklog[new] = self.engineer_worklog.pop(old)
                for entry in self.engineer_worklog[new]:
                    entry["group"] = new
            if old in self.board_filters_by_group:
                self.board_filters_by_group[new] = \
                    self.board_filters_by_group.pop(old)
                self._emit("ui_update", key="board_filters_by_group",
                           value=self.board_filters_by_group)
                self._db_save_ui(
                    "board_filters_by_group",
                    json.dumps(self.board_filters_by_group),
                )
            if self.active_group == old:
                self.active_group = new
                self._emit("ui_update", key="active_group",
                           value=self.active_group)
                self._db_save_ui("active_group", self.active_group)
            if old in self.board_selected_lanes_by_group:
                self.board_selected_lanes_by_group[new] = \
                    self.board_selected_lanes_by_group.pop(old)
                self._emit("ui_update", key="board_selected_lanes_by_group",
                           value=self.board_selected_lanes_by_group)
                self._db_save_ui(
                    "board_selected_lanes_by_group",
                    json.dumps(self.board_selected_lanes_by_group),
                )
            if old in self.board_hidden_wide_lanes_by_group:
                self.board_hidden_wide_lanes_by_group[new] = \
                    self.board_hidden_wide_lanes_by_group.pop(old)
                self._emit("ui_update",
                           key="board_hidden_wide_lanes_by_group",
                           value=self.board_hidden_wide_lanes_by_group)
                self._db_save_ui(
                    "board_hidden_wide_lanes_by_group",
                    json.dumps(self.board_hidden_wide_lanes_by_group),
                )
            if old in self.board_saved_views_by_group:
                self.board_saved_views_by_group[new] = \
                    self.board_saved_views_by_group.pop(old)
                self._emit("ui_update", key="board_saved_views_by_group",
                           value=self.board_saved_views_by_group)
                self._db_save_ui(
                    "board_saved_views_by_group",
                    json.dumps(self.board_saved_views_by_group),
                )
            if old in self.board_lane_sorts_by_group:
                self.board_lane_sorts_by_group[new] = \
                    self.board_lane_sorts_by_group.pop(old)
                self._emit("ui_update", key="board_lane_sorts_by_group",
                           value=self.board_lane_sorts_by_group)
                self._db_save_ui(
                    "board_lane_sorts_by_group",
                    json.dumps(self.board_lane_sorts_by_group),
                )
            if old in self.board_card_density_by_group:
                self.board_card_density_by_group[new] = \
                    self.board_card_density_by_group.pop(old)
                self._emit("ui_update", key="board_card_density_by_group",
                           value=self.board_card_density_by_group)
                self._db_save_ui(
                    "board_card_density_by_group",
                    json.dumps(self.board_card_density_by_group),
                )
            for aid in self.groups[new]:
                if aid in self.agents:
                    self.agents[aid].group = new
                    self._emit_agent(self.agents[aid])
                    for child_id in self._children.get(aid, []):
                        if child_id in self.agents:
                            self.agents[child_id].group = new
                            self._emit_agent(self.agents[child_id])
            self._emit("group_rename", old_name=old, new_name=new,
                       slug=self.group_slugs.get(new, ""))
            # Persist: rename group, update agent group fields
            self._db_save_groups()
            if new in self.group_settings:
                self._db_save_group_settings(new)
            if self.db:
                self.db.rename_engineer_task_log_group(old, new)
            for aid in self.groups[new]:
                if aid in self.agents:
                    self._db_save_agent(self.agents[aid])
                    for child_id in self._children.get(aid, []):
                        if child_id in self.agents:
                            self._db_save_agent(self.agents[child_id])

    def _add_cell(
        self,
        name: str,
        group: str,
        cell_type: str,
        terminal_backend: str = "",
        runner_backend: str = "",
        profile: str = "Default",
        command: str = "",
        directory: str = "",
        tab_color: str = "",
        icon: str = "",
        parent_id: str = "",
    ) -> Optional[AgentCell]:
        # Child terminals inherit group from parent
        if parent_id:
            parent = self.agents.get(parent_id)
            if not parent or parent.cell_type != "agent":
                return None
            group = parent.group
        elif group not in self.groups:
            return None
        gs = self.get_group_settings(group)
        # Max agents cap
        if cell_type == "agent" and not parent_id:
            if gs.max_agents > 0:
                current = sum(1 for aid in self.groups.get(group, [])
                              if self.agents.get(aid)
                              and self.agents[aid].cell_type == "agent")
                if current >= gs.max_agents:
                    log.warning("Group '%s' at max_agents cap (%d)",
                                group, gs.max_agents)
                    return None
        aid = uuid.uuid4().hex[:8]
        if cell_type == "terminal":
            slug = self._unique_terminal_slug(name, parent_id=parent_id,
                                              group=group)
        else:
            slug = self._unique_agent_slug(name)
        cell = AgentCell(
            id=aid,
            name=name,
            group=group,
            slug=slug,
            cell_type=cell_type,
            terminal_backend=terminal_backend
            or gs.default_terminal_backend
            or "pty",
            runner_backend=runner_backend or "pty",
            profile=profile,
            command=command or (self.get_default_command() if cell_type == "agent" else ""),
            directory=directory,
            tab_color=tab_color,
            icon=icon,
            parent_id=parent_id,
        )
        self.agents[aid] = cell
        if parent_id:
            self._children.setdefault(parent_id, []).append(aid)
        else:
            self.groups[group].append(aid)
        if cell_type == "agent":
            self._children[aid] = []
        self._emit_agent(cell)
        if parent_id:
            self._emit_agent(self.agents[parent_id])  # children changed
        else:
            self._emit_group(group)
        self._db_save_agent(cell)
        self._db_save_groups()
        log.info("Cell created: id=%s type=%s parent=%s tab_color=%r "
                 "directory=%r", aid, cell_type, parent_id or "none",
                 cell.tab_color, cell.directory)
        return cell

    def _agent_class_base_dir_for_cell(self, cell=None, base_dir: str = "") -> str:
        if base_dir:
            return str(base_dir)
        if cell is not None:
            for value in (
                getattr(cell, "worktree_repo_root", ""),
                getattr(cell, "directory", ""),
                getattr(cell, "current_path", ""),
            ):
                if str(value or "").strip():
                    return str(value or "")
        return str(getattr(self, "project_base_dir", "") or os.getcwd())

    def _record_agent_class_audit(self, cell, event: str, *, actor_kind: str = "user",
                                  actor_id: str = "", actor_label: str = "",
                                  previous: dict | None = None,
                                  snapshot: dict | None = None,
                                  message: str = "") -> None:
        if not self.db or not cell:
            return
        saver = getattr(self.db, "save_agent_class_audit", None)
        if not callable(saver):
            return
        try:
            class_snapshot = snapshot or {}
            saver({
                "id": uuid.uuid4().hex,
                "agent_id": cell.id,
                "agent_name": cell.name,
                "event": event,
                "actor_kind": actor_kind or "user",
                "actor_id": actor_id or "",
                "actor_label": actor_label or "",
                "previous_class_id": str((previous or {}).get("class_id", "") or ""),
                "previous_class_version": str((previous or {}).get("class_version", "") or ""),
                "assigned_class_id": getattr(cell, "agent_class_id", "") or "",
                "assigned_class_version": getattr(cell, "agent_class_version", "") or "",
                "effective_class_id": getattr(cell, "effective_agent_class_id", "") or "",
                "effective_class_version": getattr(cell, "effective_agent_class_version", "") or "",
                "snapshot_hash": str((class_snapshot or {}).get("snapshot_hash", "") or ""),
                "snapshot_json": class_snapshot or {},
                "message": message or "",
                "created_at": time.time(),
            })
        except Exception:
            log.exception("Failed to persist agent class audit event=%s agent=%s", event, getattr(cell, "id", ""))

    def assign_agent_class(self, aid: str, class_id: str, *, actor_kind: str = "user",
                           actor_id: str = "", actor_label: str = "",
                           base_dir: str = "") -> dict:
        """Trusted-user assignment of a desired Agent Class.

        Assignment intentionally does not mutate frozen effective class
        authority; ``apply_effective_agent_class_for_launch`` is the launch
        boundary that updates runtime authority.
        """

        cell = self.agents.get(str(aid or "").strip())
        if not cell:
            raise ValueError("Agent not found")
        if cell.cell_type != "agent":
            raise ValueError("Agent Classes can only be assigned to agents")
        if str(actor_kind or "user").strip() != "user":
            raise PermissionError("Agent Class assignment is trusted-user-only")
        class_id = str(class_id or "").strip()
        previous = {
            "class_id": getattr(cell, "agent_class_id", "") or "",
            "class_version": getattr(cell, "agent_class_version", "") or "",
        }
        if not class_id:
            cell.agent_class_id = ""
            cell.agent_class_version = ""
            cell.agent_class_assigned_at = time.time()
            cell.agent_class_assigned_by = actor_label or actor_id or actor_kind or "user"
            self._emit_agent(cell)
            self._db_save_agent(cell)
            self._record_agent_class_audit(
                cell,
                "assignment_cleared",
                actor_kind="user",
                actor_id=actor_id,
                actor_label=actor_label,
                previous=previous,
                message="desired Agent Class assignment cleared; effective launch snapshots unchanged",
            )
            return self.agent_class_status_for_cell(cell, base_dir=base_dir)

        from .agent_classes import agent_class_definition_by_id, enriched_agent_class_preview

        definition = agent_class_definition_by_id(
            class_id,
            base_dir=self._agent_class_base_dir_for_cell(cell, base_dir),
        )
        if not definition:
            raise ValueError(f"Unknown or invalid Agent Class: {class_id}")
        base_kind = str(getattr(cell, "kind", "") or "").strip()
        if base_kind and definition.base_kind != base_kind:
            raise ValueError(
                f"Agent Class {definition.id} is for base_kind={definition.base_kind}, "
                f"but agent kind is {base_kind}"
            )
        cell.agent_class_id = definition.id
        cell.agent_class_version = definition.version
        cell.agent_class_assigned_at = time.time()
        cell.agent_class_assigned_by = actor_label or actor_id or actor_kind or "user"
        self._emit_agent(cell)
        self._db_save_agent(cell)
        self._record_agent_class_audit(
            cell,
            "assignment_set",
            actor_kind="user",
            actor_id=actor_id,
            actor_label=actor_label,
            previous=previous,
            snapshot=enriched_agent_class_preview(
                definition,
                base_dir=self._agent_class_base_dir_for_cell(cell, base_dir),
            ),
            message="desired Agent Class assignment set; applies at next launch/session",
        )
        return self.agent_class_status_for_cell(cell, base_dir=base_dir)

    def apply_effective_agent_class_for_launch(self, cell, *, base_dir: str = "",
                                               actor_kind: str = "system",
                                               actor_id: str = "launch") -> dict:
        """Freeze the desired/default Agent Class effective authority."""

        if not cell or getattr(cell, "cell_type", "") != "agent":
            return {}
        from .agent_classes import (
            agent_class_definition_by_id,
            default_agent_class_id_for_kind,
            freeze_agent_class_snapshot,
        )

        base_kind = str(getattr(cell, "kind", "") or "").strip()
        if not base_kind:
            return {}
        desired_id = str(getattr(cell, "agent_class_id", "") or "").strip()
        class_id = desired_id or default_agent_class_id_for_kind(base_kind)
        if not class_id:
            return {}
        base_dir_resolved = self._agent_class_base_dir_for_cell(cell, base_dir)
        definition = agent_class_definition_by_id(class_id, base_dir=base_dir_resolved)
        if not definition:
            if desired_id:
                raise ValueError(f"Unknown or invalid Agent Class: {class_id}")
            # A broken default registry should fail truthfully instead of
            # silently masking with a broader authority fallback.
            raise ValueError(f"Unknown or invalid default Agent Class: {class_id}")
        if definition.base_kind != base_kind:
            raise ValueError(
                f"Agent Class {definition.id} is for base_kind={definition.base_kind}, "
                f"but agent kind is {base_kind}"
            )
        previous = {
            "class_id": getattr(cell, "effective_agent_class_id", "") or "",
            "class_version": getattr(cell, "effective_agent_class_version", "") or "",
        }
        frozen_at = time.time()
        class_snapshot = freeze_agent_class_snapshot(
            definition,
            assignment_source="assigned" if desired_id else "default_base_kind_class",
            frozen_at=frozen_at,
            base_dir=base_dir_resolved,
        )
        cell.effective_agent_class_id = definition.id
        cell.effective_agent_class_version = definition.version
        cell.effective_agent_class_snapshot = class_snapshot
        cell.effective_agent_class_applied_at = frozen_at
        if desired_id and cell.agent_class_version != definition.version:
            cell.agent_class_version = definition.version
        self._emit_agent(cell)
        self._db_save_agent(cell)
        if (previous["class_id"], previous["class_version"]) != (definition.id, definition.version):
            self._record_agent_class_audit(
                cell,
                "effective_snapshot_applied",
                actor_kind=actor_kind or "system",
                actor_id=actor_id or "launch",
                previous=previous,
                snapshot=class_snapshot,
                message="effective Agent Class authority frozen for launched session",
            )
        return class_snapshot

    def agent_class_status_for_cell(self, cell, *, base_dir: str = "") -> dict:
        from .agent_classes import agent_class_cell_status
        return agent_class_cell_status(
            cell,
            base_dir=self._agent_class_base_dir_for_cell(cell, base_dir),
        )

    def update_agent(self, aid: str, **fields):
        """Update mutable fields on an existing cell."""
        cell = self.agents.get(aid)
        if not cell:
            return
        for key in ("name", "tab_color", "icon"):
            if key in fields:
                setattr(cell, key, fields[key])
        if "engineer_specializations" in fields and cell.kind == "engineer":
            raw_specs = fields.get("engineer_specializations") or []
            specs = []
            seen_specs = set()
            for item in (raw_specs if isinstance(raw_specs, list) else []):
                token = str(item or "").strip()
                if not token or token in seen_specs:
                    continue
                specs.append(token)
                seen_specs.add(token)
            cell.engineer_specializations = specs
        if "name" in fields:
            if cell.cell_type == "terminal":
                cell.slug = self._unique_terminal_slug(
                    cell.name, parent_id=cell.parent_id,
                    group=cell.group, exclude_id=aid)
            else:
                cell.slug = self._unique_agent_slug(cell.name,
                                                    exclude_id=aid)
                # Cascade: update children's slug prefixes
                for child_id in self._children.get(aid, []):
                    child = self.agents.get(child_id)
                    if child:
                        child.slug = self._unique_terminal_slug(
                            child.name, parent_id=aid,
                            group=child.group, exclude_id=child_id)
                        self._emit_agent(child)
                        self._db_save_agent(child)
        self._emit_agent(cell)
        self._db_save_agent(cell)

    def add_agent(self, **kw) -> Optional[AgentCell]:
        return self._add_cell(cell_type="agent", **kw)

    def add_terminal(self, **kw) -> Optional[AgentCell]:
        kw.setdefault("command", "")
        return self._add_cell(cell_type="terminal", **kw)

    def _agent_cascade_cells(self, aid: str) -> list[AgentCell]:
        """Return a root cell plus child terminals in deletion order."""
        cell = self.agents.get(str(aid or "").strip())
        if not cell:
            return []
        ordered: list[AgentCell] = []
        seen: set[str] = set()

        def add(cell_id: str) -> None:
            if not cell_id or cell_id in seen:
                return
            seen.add(cell_id)
            current = self.agents.get(cell_id)
            if not current:
                return
            ordered.append(current)
            for child_id in list(self._children.get(cell_id, [])):
                add(child_id)

        add(cell.id)
        return ordered

    def _prepare_tombstoned_cell(self, cell: AgentCell, now: float) -> None:
        cell.deleted_at = now
        cell.permanent_delete_after = now + AGENT_TOMBSTONE_RETENTION_SECONDS
        cell.status = "stopped"
        cell.session_id = None
        cell.current_task_id = ""
        cell.current_process = ""
        cell.current_path = ""
        cell.current_branch = ""
        cell.git_root = ""
        cell.activity = ""
        cell.activity_detail = ""
        cell.error_message = ""
        cell.needs_attention = False

    def _hard_delete_agent(self, aid: str, *,
                           record_history: bool = True) -> list[AgentCell]:
        removed: list[AgentCell] = []
        cell = self.agents.pop(aid, None)
        if not cell:
            return removed
        removed.append(cell)
        self._emit("agent_remove", id=aid,
                   group=cell.group, cell_type=cell.cell_type)
        # Remove from group list (top-level items only)
        if not cell.parent_id and cell.group in self.groups:
            self.groups[cell.group] = [
                x for x in self.groups[cell.group] if x != aid
            ]
            self._emit_group(cell.group)
        # If this is a child terminal, remove from parent's children list
        if cell.parent_id and cell.parent_id in self._children:
            self._children[cell.parent_id] = [
                x for x in self._children[cell.parent_id] if x != aid
            ]
        # Cascade: remove child terminals
        for child_id in self._children.pop(aid, []):
            child = self.agents.pop(child_id, None)
            if child:
                removed.append(child)
                self._emit("agent_remove", id=child_id,
                           group=child.group,
                           cell_type=child.cell_type)
        # Unlink from board tasks
        removed_ids = {r.id for r in removed}
        for t in self.board_tasks.values():
            if t.agent_id in removed_ids:
                t.agent_id = ""
                self._emit("task_upsert", **asdict(t))
                self._db_save_task(t)
        # Clear engineer designation only when the row is permanently purged.
        gs = self.group_settings.get(cell.group)
        if gs and gs.engineer_agent_id == aid:
            gs.engineer_agent_id = ""
            self._emit("group_settings_update", name=cell.group, **asdict(gs))
            self._db_save_group_settings(cell.group)
        for r in removed:
            if record_history and r.cell_type == "agent":
                self.history_remove_agent(r)
            self.delete_agent_digest_settings(r.id)
            self._db_delete_agent(r.id)
        self.cleanup_orphaned_attention(allow_persisted_agent_fallback=False)
        self._db_save_groups()
        return removed

    def remove_agent(self, aid: str) -> list[AgentCell]:
        """Soft-delete an agent cell for the 7-day restore window.

        Standalone terminals remain immediate hard deletes; soft-delete is for
        agent cells and child terminals that cascade from an agent tombstone.
        """
        cell = self.agents.get(aid)
        if not cell:
            return []
        if cell.cell_type == "terminal":
            return self._hard_delete_agent(aid)

        now = time.time()
        tombstoned = self._agent_cascade_cells(aid)
        tombstoned_ids = {c.id for c in tombstoned}
        for target in tombstoned:
            self._prepare_tombstoned_cell(target, now)
            self._emit_agent(target)
            self._db_save_agent(target)
            if target.cell_type == "agent":
                self.history_remove_agent(target)

        # Existing hard-delete semantics detached tasks from the deleted cell.
        # Keep that irreversible transfer at tombstone time so active routing
        # never targets a hidden/restorable cell.
        for t in self.board_tasks.values():
            if t.agent_id in tombstoned_ids:
                t.agent_id = ""
                self._emit("task_upsert", **asdict(t))
                self._db_save_task(t)

        self.cleanup_orphaned_attention(allow_persisted_agent_fallback=False)
        return tombstoned

    def restore_agent(self, aid: str) -> list[AgentCell]:
        """Restore a tombstoned agent and its tombstoned child terminals."""
        cell = self.agents.get(str(aid or "").strip())
        if not cell:
            return []
        targets = self._agent_cascade_cells(cell.id)
        restored: list[AgentCell] = []
        for target in targets:
            if not self.agent_is_tombstoned(target):
                continue
            target.deleted_at = 0.0
            target.permanent_delete_after = 0.0
            restored.append(target)
            self._emit_agent(target)
            self._db_save_agent(target)
        return restored

    def purge_agent_now(self, aid: str) -> list[AgentCell]:
        """Permanently delete an agent/tombstone immediately."""
        return self._hard_delete_agent(str(aid or "").strip())

    def purge_tombstoned_agents(self, now: float | None = None) -> list[AgentCell]:
        """Permanently delete tombstones whose restore window has expired."""
        ts = _safe_float(now if now is not None else time.time())
        purged: list[AgentCell] = []
        due_ids = [
            cell.id for cell in list(self.agents.values())
            if self.agent_is_tombstoned(cell)
            and _safe_float(getattr(cell, "permanent_delete_after", 0.0)) <= ts
        ]
        for aid in due_ids:
            if aid not in self.agents:
                continue
            purged.extend(self._hard_delete_agent(aid))
        return purged

    def move_agent(self, aid: str, target_group: str, before: str = ""):
        cell = self.agents.get(aid)
        if not cell or target_group not in self.groups:
            return
        old_group = cell.group
        # Detach from parent if this is a child terminal being moved
        if cell.parent_id:
            old_parent = cell.parent_id
            if old_parent in self._children:
                self._children[old_parent] = [
                    x for x in self._children[old_parent] if x != aid
                ]
            cell.parent_id = ""
        # Remove from group list (may not be there if was a child)
        if cell.group in self.groups:
            self.groups[cell.group] = [
                x for x in self.groups[cell.group] if x != aid
            ]
        if before and before in self.groups[target_group]:
            idx = self.groups[target_group].index(before)
            self.groups[target_group].insert(idx, aid)
        else:
            self.groups[target_group].append(aid)
        cell.group = target_group
        self._emit_agent(cell)
        # Move children along
        for child_id in self._children.get(aid, []):
            child = self.agents.get(child_id)
            if child:
                child.group = target_group
                self._emit_agent(child)
        if old_group != target_group:
            self._emit_group(old_group)
        self._emit_group(target_group)
        self._db_save_agent(cell)
        for child_id in self._children.get(aid, []):
            child = self.agents.get(child_id)
            if child:
                self._db_save_agent(child)
        self._db_save_groups()

    def reorder_child(self, aid: str, parent_id: str, before: str = ""):
        """Reorder a child terminal within its parent's children list."""
        if parent_id not in self._children:
            return
        children = self._children[parent_id]
        if aid not in children:
            return
        children.remove(aid)
        if before and before in children:
            idx = children.index(before)
            children.insert(idx, aid)
        else:
            children.append(aid)
        # Children order is derived from _children, emit parent for rebuild
        self._emit_agent(self.agents[parent_id])
        # Children order is in-memory only (_children), group_members tracks it
        self._db_save_groups()

    def reparent_terminal(self, aid: str, new_parent_id: str):
        """Attach a terminal to an agent (or detach if new_parent_id is empty)."""
        cell = self.agents.get(aid)
        if not cell or cell.cell_type != "terminal":
            return
        new_parent = self.agents.get(new_parent_id) if new_parent_id else None
        if new_parent_id and (not new_parent or new_parent.cell_type != "agent"):
            return
        old_parent_id = cell.parent_id
        # Detach from old parent
        if cell.parent_id and cell.parent_id in self._children:
            self._children[cell.parent_id] = [
                x for x in self._children[cell.parent_id] if x != aid
            ]
        # Remove from group list if standalone
        if not cell.parent_id and cell.group in self.groups:
            self.groups[cell.group] = [
                x for x in self.groups[cell.group] if x != aid
            ]
        # Attach to new parent
        if new_parent_id:
            cell.parent_id = new_parent_id
            cell.group = new_parent.group
            self._children.setdefault(new_parent_id, []).append(aid)
        else:
            cell.parent_id = ""
            if cell.group in self.groups:
                self.groups[cell.group].append(aid)
        # Regenerate slug with new parent prefix
        cell.slug = self._unique_terminal_slug(
            cell.name, parent_id=cell.parent_id,
            group=cell.group, exclude_id=aid)
        self._emit_agent(cell)
        if old_parent_id and old_parent_id in self.agents:
            self._emit_agent(self.agents[old_parent_id])
        if new_parent_id:
            self._emit_agent(new_parent)
        self._emit_group(cell.group)
        self._db_save_agent(cell)
        if old_parent_id and old_parent_id in self.agents:
            self._db_save_agent(self.agents[old_parent_id])
        self._db_save_groups()

    def move_group(self, name: str, before: str = ""):
        if name not in self.groups:
            return
        items = [(k, v) for k, v in self.groups.items() if k != name]
        value = self.groups[name]
        idx = next((i for i, (k, _) in enumerate(items) if k == before), -1)
        if idx >= 0:
            items.insert(idx, (name, value))
        else:
            items.append((name, value))
        self.groups = dict(items)
        self._emit("groups_reorder", groups=list(self.groups.keys()))
        self._db_save_groups()

    def cells_with_awareness(self) -> list[AgentCell]:
        """Return cells that have agent awareness active (agent_type set)."""
        return [c for c in self.iter_active_agents() if c.agent_type]
