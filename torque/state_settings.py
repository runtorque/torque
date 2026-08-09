"""Group, Architect, Engineer, digest, visibility, and attention settings behavior."""

from __future__ import annotations

from dataclasses import dataclass

from .memory import clamp_context_ttl_days

from .state import (
    AgentCell, AgentDigestSettings, ArchitectSettings, BoardTask,
    EngineerSettings, GroupSettings, Optional,
    _ARCHITECT_DIGEST_DEFAULT_ENABLED_EVENTS,
    _ARCHITECT_DIGEST_LEGACY_DEFAULT_ENABLED_EVENTS,
    _DEFAULT_ARCHITECT_HEARTBEAT_INTERVAL,
    _DEFAULT_ARCHITECT_MAX_INTERVAL,
    _DEFAULT_ARCHITECT_PUSH_INTERVAL,
    _DEFAULT_GUIDANCE_HINT_CADENCE,
    _ENGINEER_NOTIFICATION_PRESETS,
    _normalize_board_sync_github_settings,
    _normalize_board_sync_provider,
    _normalize_engineer_hint_snoozes,
    asdict, board_task_is_closed, datetime,
    emit_engineer_ask_resolved_event,
    emit_engineer_awaiting_human_input_event,
    log, normalize_architect_autonomy_mode, normalize_codex_fast_mode,
    normalize_architect_digest_verbosity,
    normalize_architect_enabled_events,
    normalize_architect_journal_checkpoint_frequency,
    normalize_architect_review_gate_thresholds,
    normalize_default_worker_concurrency,
    normalize_engineer_autonomy_mode,
    normalize_engineer_digest_verbosity,
    normalize_engineer_escalation_style,
    normalize_engineer_merge_mode,
    normalize_engineer_same_agent_follow_up_preference,
    normalize_engineer_wave_size_preference,
    normalize_guidance_hint_cadence,
    normalize_worktree_merge_cleanup,
    time, timezone,
)


@dataclass
class AgentSettings:
    """Nullable settings overrides for one Architect or Engineer."""
    agent_id: str = ""
    provider: Optional[str] = None
    boot_command: Optional[str] = None
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    fast_mode: Optional[str] = None
    autonomy_mode: Optional[str] = None
    custom_instructions: Optional[str] = None
    default_worker_concurrency: Optional[int] = None
    wave_size_preference: Optional[str] = None
    same_agent_follow_up_preference: Optional[str] = None
    escalation_style: Optional[str] = None
    engineer_can_override_worker_provider: Optional[bool] = None
    restrict_to_created_agents: Optional[bool] = None


class StateSettingsMixin:
    def agent_settings_snapshot(self) -> dict:
        """Project raw and resolved per-agent settings into state snapshots."""
        return {
            "agent_settings": {
                agent_id: asdict(settings)
                for agent_id, settings in self.agent_settings.items()
            },
            "resolved_agent_settings": {
                agent_id: self.resolve_agent_settings(agent_id)
                for agent_id, cell in self.agents.items()
                if cell.kind in {"architect", "engineer"}
            },
        }

    def get_group_settings(self, name: str) -> GroupSettings:
        """Return group settings, creating defaults if group has none."""
        return self.group_settings.get(name, GroupSettings())

    def should_show_guidance_hint(self, hint_type: str, cell) -> bool:
        """Return whether a recurring soft guidance hint should be shown.

        Counters are ephemeral and scoped to a specific hint, agent, and
        provider session. ``guidance_hint_cadence=0`` preserves legacy
        every-time behavior; positive values show on the first occurrence and
        then every Nth occurrence.
        """
        hint_type = str(hint_type or "").strip()
        if not hint_type or not cell:
            return True
        agent_id = str(getattr(cell, "id", "") or "").strip()
        session_id = str(getattr(cell, "session_id", "") or "").strip()
        if not agent_id or not session_id:
            return True

        group = str(getattr(cell, "group", "") or "").strip()
        cadence = normalize_guidance_hint_cadence(
            getattr(
                self.get_group_settings(group),
                "guidance_hint_cadence",
                _DEFAULT_GUIDANCE_HINT_CADENCE,
            )
        )
        key = f"{hint_type}:{agent_id}:{session_id}"
        count = int(self.guidance_hint_counters.get(key, 0) or 0) + 1
        self.guidance_hint_counters[key] = count
        if cadence == 0:
            return True
        return count == 1 or count % cadence == 0

    def _normalize_architect_settings_mapping(
            self, fields: dict, *, strict: bool) -> dict:
        if "architect_autonomy_mode" in fields:
            fields["architect_autonomy_mode"] = normalize_architect_autonomy_mode(
                fields["architect_autonomy_mode"],
                strict=strict,
            )
        if "architect_digest_verbosity" in fields:
            fields["architect_digest_verbosity"] = (
                normalize_architect_digest_verbosity(
                    fields["architect_digest_verbosity"],
                    strict=strict,
                )
            )
        if "architect_journal_checkpoint_frequency" in fields:
            fields["architect_journal_checkpoint_frequency"] = (
                normalize_architect_journal_checkpoint_frequency(
                    fields["architect_journal_checkpoint_frequency"],
                    strict=strict,
                )
            )
        if "architect_review_gate_thresholds" in fields:
            fields["architect_review_gate_thresholds"] = (
                normalize_architect_review_gate_thresholds(
                    fields["architect_review_gate_thresholds"],
                    strict=strict,
                )
            )
        for bool_key in ("architect_suppress_empty_digests",):
            if bool_key in fields:
                raw = fields[bool_key]
                if isinstance(raw, str):
                    fields[bool_key] = (
                        raw.strip().lower() in {"1", "true", "yes", "on"}
                    )
                else:
                    fields[bool_key] = bool(raw)
        for int_key, default_val, min_val in (
                ("architect_push_interval",
                    _DEFAULT_ARCHITECT_PUSH_INTERVAL, 0),
                ("architect_max_interval",
                    _DEFAULT_ARCHITECT_MAX_INTERVAL, 0),
                ("architect_heartbeat_interval",
                    _DEFAULT_ARCHITECT_HEARTBEAT_INTERVAL, 0)):
            if int_key in fields:
                raw = fields[int_key]
                try:
                    parsed = int(raw)
                except (TypeError, ValueError):
                    parsed = default_val
                if parsed < min_val:
                    parsed = min_val
                fields[int_key] = parsed
        if "architect_enabled_events" in fields:
            fields["architect_enabled_events"] = (
                normalize_architect_enabled_events(
                    fields["architect_enabled_events"]
                )
            )
        if "architect_fast_mode" in fields:
            fields["architect_fast_mode"] = normalize_codex_fast_mode(
                fields["architect_fast_mode"], strict=strict
            )
        for key in (
                "architect_boot_command", "architect_provider",
                "architect_model", "architect_reasoning_effort",
                "architect_fast_mode",
                "architect_directory", "architect_profile",
                "architect_shell", "architect_tab_color",
                "architect_custom_instructions"):
            if key in fields:
                fields[key] = str(fields[key] or "").strip()
        return fields

    def get_architect_settings(self, group: str) -> ArchitectSettings:
        """Return architect settings for a group, backed by group_settings."""
        group = str(group or "").strip()
        gs = self.get_group_settings(group)
        values = {}
        for key in ArchitectSettings.__dataclass_fields__:
            if key == "group":
                continue
            if hasattr(gs, key):
                value = getattr(gs, key)
                if key == "architect_review_gate_thresholds":
                    value = normalize_architect_review_gate_thresholds(value)
                values[key] = value
        self._normalize_architect_settings_mapping(values, strict=False)
        return ArchitectSettings(group=group, **values)

    def _architect_cells_for_group(self, group: str) -> list[AgentCell]:
        group = str(group or "").strip()
        return [
            cell for cell in self.iter_active_agents()
            if cell.cell_type == "agent"
            and str(getattr(cell, "kind", "") or "").strip() == "architect"
            and str(getattr(cell, "group", "") or "").strip() == group
        ]

    def _sync_architect_digest_settings(self, group: str,
                                        fields: dict) -> None:
        digest_field_map = {
            "architect_push_interval": "push_interval",
            "architect_max_interval": "max_interval",
            "architect_heartbeat_interval": "heartbeat_interval",
            "architect_digest_verbosity": "digest_verbosity",
            "architect_suppress_empty_digests": "suppress_empty",
            "architect_enabled_events": "enabled_events",
        }
        digest_updates = {
            digest_field_map[key]: fields[key]
            for key in digest_field_map
            if key in fields
        }
        if not digest_updates:
            return
        for architect in self._architect_cells_for_group(group):
            self.update_agent_digest_settings(architect.id, **digest_updates)

    def update_architect_settings(self, group: str, **fields) -> dict:
        """Update architect settings for a group.

        These settings are persisted in ``group_settings`` so read-only CLI
        paths and group snapshots see one source of truth.
        """
        group = str(group or "").strip()
        if group not in self.groups:
            return {}
        gs = self.group_settings.get(group)
        if gs is None:
            gs = GroupSettings()
            self.group_settings[group] = gs
        fields = dict(fields or {})
        if "custom_instructions" in fields and "architect_custom_instructions" not in fields:
            fields["architect_custom_instructions"] = fields.pop(
                "custom_instructions"
            )
        valid = set(ArchitectSettings.__dataclass_fields__) - {"group"}
        candidate = {
            key: value for key, value in fields.items()
            if key in valid
        }
        self._normalize_architect_settings_mapping(candidate, strict=True)
        applied = {}
        for key, value in candidate.items():
            setattr(gs, key, value)
            applied[key] = value
        if not applied:
            return {}
        payload = asdict(self.get_architect_settings(group))
        payload.pop("group", None)
        self._emit("architect_settings_update", group=group, **payload)
        self._emit("group_settings_update", name=group, **asdict(gs))
        self._db_save_group_settings(group)
        self._sync_architect_digest_settings(group, applied)
        return applied

    def update_group_settings(self, name: str, **fields):
        """Update group settings. Creates GroupSettings entry if needed."""
        if name not in self.groups:
            return
        gs = self.group_settings.get(name)
        if gs is None:
            gs = GroupSettings()
            self.group_settings[name] = gs
        valid = set(GroupSettings.__dataclass_fields__)
        for key, value in fields.items():
            if key in valid:
                if key == "worktree_merge_cleanup":
                    value = normalize_worktree_merge_cleanup(value)
                elif key == "engineer_merge_mode":
                    value = normalize_engineer_merge_mode(value)
                elif key == "guidance_hint_cadence":
                    value = normalize_guidance_hint_cadence(value)
                elif key == "context_default_ttl_days":
                    value = clamp_context_ttl_days(value)
                elif key == "engineer_hint_snoozes":
                    value = _normalize_engineer_hint_snoozes(value)
                elif key == "board_sync_provider":
                    value = _normalize_board_sync_provider(value)
                elif key == "board_sync_enabled":
                    value = bool(value)
                elif key == "board_sync_github":
                    value = _normalize_board_sync_github_settings(value)
                elif key in {
                        "agent_fast_mode", "worker_fast_mode"}:
                    value = normalize_codex_fast_mode(value, strict=True)
                elif key in {
                        "agent_model", "agent_reasoning_effort", "agent_fast_mode",
                        "worker_provider", "worker_boot_command",
                        "worker_model", "worker_reasoning_effort", "worker_fast_mode"}:
                    value = str(value or "").strip()
                elif key in (
                        set(ArchitectSettings.__dataclass_fields__) - {"group"}):
                    normalized = self._normalize_architect_settings_mapping(
                        {key: value},
                        strict=True,
                    )
                    value = normalized[key]
                setattr(gs, key, value)
        self._emit("group_settings_update", name=name, **asdict(gs))
        if any(
                key in (set(ArchitectSettings.__dataclass_fields__) - {"group"})
                for key in fields):
            payload = asdict(self.get_architect_settings(name))
            payload.pop("group", None)
            self._emit("architect_settings_update", group=name, **payload)
        self._db_save_group_settings(name)
        self._sync_architect_digest_settings(name, fields)

    def get_engineer_for_group(self, group: str) -> Optional[AgentCell]:
        """Return the engineer agent for a group, or None."""
        gs = self.group_settings.get(group)
        if not gs or not gs.engineer_agent_id:
            return None
        return self.get_active_agent(gs.engineer_agent_id)

    def get_engineer_settings(self, group: str) -> EngineerSettings:
        """Return engineer settings for a group, creating defaults if needed."""
        return self.engineer_settings.get(group, EngineerSettings(group=group))

    def get_agent_settings(self, agent_id: str) -> AgentSettings:
        """Return only explicitly stored nullable overrides for an agent."""
        agent_id = str(agent_id or "").strip()
        return self.agent_settings.get(agent_id, AgentSettings(agent_id=agent_id))

    @staticmethod
    def _agent_setting_inherits(key: str, value) -> bool:
        """Return whether an override value represents field-level inherit."""
        if value is None:
            return True
        if not isinstance(value, str):
            return False
        value = value.strip()
        return not value or (key == "fast_mode" and value.lower() == "inherit")

    def update_agent_settings(self, agent_id: str, **fields) -> AgentSettings:
        """Set/clear per-agent overrides; ``None`` always means inherit."""
        agent_id = str(agent_id or "").strip()
        cell = self.agents.get(agent_id)
        if not cell or cell.kind not in {"architect", "engineer"}:
            raise ValueError("per-agent settings require an Architect or Engineer id")
        current = self.agent_settings.get(agent_id, AgentSettings(agent_id=agent_id))
        valid = set(AgentSettings.__dataclass_fields__) - {"agent_id"}
        for key, value in fields.items():
            if key not in valid:
                continue
            if self._agent_setting_inherits(key, value):
                value = None
            if value is not None:
                if key in {"engineer_can_override_worker_provider", "restrict_to_created_agents"}:
                    value = bool(value)
                elif key == "default_worker_concurrency":
                    value = normalize_default_worker_concurrency(value)
                elif key == "autonomy_mode":
                    value = (
                        normalize_architect_autonomy_mode(value, strict=True)
                        if cell.kind == "architect"
                        else normalize_engineer_autonomy_mode(value)
                    )
                elif key == "wave_size_preference":
                    value = normalize_engineer_wave_size_preference(value)
                elif key == "same_agent_follow_up_preference":
                    value = normalize_engineer_same_agent_follow_up_preference(value)
                elif key == "escalation_style":
                    value = normalize_engineer_escalation_style(value)
                elif key == "fast_mode":
                    value = normalize_codex_fast_mode(value, strict=True)
                else:
                    value = str(value).strip()
            setattr(current, key, value)
        self.agent_settings[agent_id] = current
        payload = asdict(current)
        self._emit(
            "agent_settings_update",
            group=cell.group,
            **payload,
            resolved=self.resolve_agent_settings(agent_id),
        )
        if self.db:
            self.db.save_agent_settings(agent_id, payload)
        return current

    def delete_agent_settings(self, agent_id: str) -> None:
        agent_id = str(agent_id or "").strip()
        self.agent_settings.pop(agent_id, None)
        if self.db:
            self.db.delete_agent_settings(agent_id)

    def resolve_agent_settings(self, agent_id: str) -> dict:
        """Return effective values with an explicit per-field origin layer."""
        agent_id = str(agent_id or "").strip()
        cell = self.agents.get(agent_id)
        if not cell or cell.kind not in {"architect", "engineer"}:
            raise ValueError("resolved settings require an Architect or Engineer id")
        overrides = self.get_agent_settings(agent_id)
        group_exists = cell.group in self.engineer_settings or cell.group in self.group_settings
        if cell.kind == "architect":
            group_settings = self.get_architect_settings(cell.group)
            mapping = {
                "provider": "architect_provider", "boot_command": "architect_boot_command",
                "model": "architect_model", "reasoning_effort": "architect_reasoning_effort",
                "fast_mode": "architect_fast_mode", "autonomy_mode": "architect_autonomy_mode",
                "custom_instructions": "architect_custom_instructions",
            }
        else:
            group_settings = self.get_engineer_settings(cell.group)
            mapping = {
                "provider": "engineer_provider", "boot_command": "engineer_boot_command",
                "model": "engineer_model", "reasoning_effort": "engineer_reasoning_effort",
                "fast_mode": "engineer_fast_mode", "autonomy_mode": "autonomy_mode",
                "custom_instructions": "custom_instructions",
                "default_worker_concurrency": "default_worker_concurrency",
                "wave_size_preference": "wave_size_preference",
                "same_agent_follow_up_preference": "same_agent_follow_up_preference",
                "escalation_style": "escalation_style",
                "engineer_can_override_worker_provider": "engineer_can_override_worker_provider",
                "restrict_to_created_agents": "restrict_to_created_agents",
            }
        resolved = {}
        generic = self.get_group_settings(cell.group)
        generic_mapping = {
            "provider": "agent_provider", "boot_command": "agent_boot_command",
            "model": "agent_model", "reasoning_effort": "agent_reasoning_effort",
            "fast_mode": "agent_fast_mode",
        }
        for name in set(AgentSettings.__dataclass_fields__) - {"agent_id"}:
            value = getattr(overrides, name)
            if not self._agent_setting_inherits(name, value):
                origin = "per-agent"
            else:
                attr = mapping.get(name)
                value = getattr(group_settings, attr, None) if attr else None
                if value in {None, "", "inherit"} and name in generic_mapping:
                    value = getattr(generic, generic_mapping[name], None)
                origin = (
                    "group"
                    if group_exists and value not in {None, "", "inherit"}
                    else "default"
                )
            resolved[name] = {"value": value, "origin": origin}
        digest = self.get_agent_digest_settings(agent_id)
        override_fields = set(
            getattr(self.agent_digest_settings.get(agent_id), "override_fields", []) or []
        )
        for name in ("paused", "push_interval", "max_interval", "heartbeat_interval",
                     "digest_verbosity", "enabled_events"):
            resolved[name] = {
                "value": getattr(digest, name),
                "origin": "per-agent" if name in override_fields else (
                    "group" if group_exists else "default"
                ),
            }
        resolved["agent_class_id"] = {
            "value": cell.agent_class_id,
            "origin": "per-agent" if cell.agent_class_id else "default",
        }
        resolved["engineer_specializations"] = {
            "value": list(cell.engineer_specializations),
            "origin": "per-agent" if cell.engineer_specializations else "default",
        }
        return resolved

    def _default_agent_digest_settings(
        self,
        agent_id: str,
        cell=None,
    ) -> AgentDigestSettings:
        """Return kind-aware default digest settings for one recipient."""
        agent_id = str(agent_id or "").strip()
        if cell is None:
            cell = self.agents.get(agent_id)
        is_architect = bool(
            cell and str(getattr(cell, "kind", "") or "").strip() == "architect"
        )
        kwargs = {}
        if is_architect:
            arch = self.get_architect_settings(
                getattr(cell, "group", "") or ""
            )
            enabled = normalize_architect_enabled_events(
                arch.architect_enabled_events
            )
            kwargs["enabled_events"] = enabled
            kwargs["push_interval"] = int(
                arch.architect_push_interval
                if arch.architect_push_interval is not None
                else _DEFAULT_ARCHITECT_PUSH_INTERVAL
            )
            kwargs["max_interval"] = int(
                arch.architect_max_interval
                if arch.architect_max_interval is not None
                else _DEFAULT_ARCHITECT_MAX_INTERVAL
            )
            kwargs["heartbeat_interval"] = int(
                arch.architect_heartbeat_interval
                if arch.architect_heartbeat_interval is not None
                else _DEFAULT_ARCHITECT_HEARTBEAT_INTERVAL
            )
            kwargs["digest_verbosity"] = normalize_architect_digest_verbosity(
                arch.architect_digest_verbosity
            )
            kwargs["suppress_empty"] = bool(
                arch.architect_suppress_empty_digests
            )
            return AgentDigestSettings(
                agent_id=agent_id,
                architect_digest=True,
                wake_on_digest=False,
                **kwargs,
            )
        return AgentDigestSettings(
            agent_id=agent_id,
            push_interval=60,
            architect_digest=False,
            wake_on_digest=False,
            **kwargs,
        )

    def _legacy_agent_digest_settings(self, agent_id: str) -> AgentDigestSettings:
        agent_id = str(agent_id or "").strip()
        cell = self.agents.get(agent_id)
        if not cell:
            return AgentDigestSettings(agent_id=agent_id)
        if str(getattr(cell, "kind", "") or "").strip() == "architect":
            return self._default_agent_digest_settings(agent_id, cell)
        if str(getattr(cell, "kind", "") or "").strip() != "engineer":
            return self._default_agent_digest_settings(agent_id, cell)
        ws = self.get_engineer_settings(cell.group)
        push_interval = getattr(ws, "push_interval", 60)
        if push_interval is None:
            push_interval = 60
        max_interval = getattr(ws, "max_interval", 300)
        if max_interval is None:
            max_interval = 300
        heartbeat_interval = getattr(ws, "heartbeat_interval", 300)
        if heartbeat_interval is None:
            heartbeat_interval = 300
        return AgentDigestSettings(
            agent_id=agent_id,
            paused=bool(getattr(ws, "paused", False)),
            push_interval=int(push_interval),
            max_interval=int(max_interval),
            heartbeat_interval=int(heartbeat_interval),
            digest_verbosity=normalize_engineer_digest_verbosity(
                getattr(ws, "digest_verbosity", "balanced")
            ),
            enabled_events=list(getattr(ws, "enabled_events", []) or []),
            architect_digest=(cell.kind == "architect"),
            wake_on_digest=False,
        )

    def get_agent_digest_settings(self, agent_id: str) -> AgentDigestSettings:
        """Return digest settings for one engineer/architect recipient."""
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return AgentDigestSettings()
        base = self._legacy_agent_digest_settings(agent_id)
        settings = self.agent_digest_settings.get(agent_id)
        if settings is None:
            return base
        resolved = AgentDigestSettings(**asdict(base))
        for key in set(settings.override_fields or []):
            if key in {"paused", "push_interval", "max_interval", "heartbeat_interval",
                       "digest_verbosity", "enabled_events"}:
                setattr(resolved, key, getattr(settings, key))
        resolved.architect_digest = settings.architect_digest
        resolved.wake_on_digest = settings.wake_on_digest
        resolved.suppress_empty = settings.suppress_empty
        resolved.override_fields = list(settings.override_fields or [])
        return resolved

    def _backfill_architect_digest_defaults(self) -> None:
        """One-time: quiet architect rows/groups with the old broad default."""
        marker_key = "architect_digest_quiet_default_backfilled"
        group_marker_key = "architect_digest_group_quiet_default_backfilled"
        if not self.db:
            return
        try:
            already = self.db.load_ui_state_value(marker_key)
            group_already = self.db.load_ui_state_value(group_marker_key)
        except Exception:
            log.exception("Failed to read backfill marker %s", marker_key)
            return
        if already and group_already:
            return
        legacy_defaults = set(normalize_architect_enabled_events(
            _ARCHITECT_DIGEST_LEGACY_DEFAULT_ENABLED_EVENTS
        ))
        # Existing installations may have rows written before the Engineer peer
        # coarse events existed. Treat that previous all-events set as the same
        # legacy broad default so :796's digest-quiet backfill still quiets it.
        legacy_defaults_without_engineer_peer = legacy_defaults - {
            "engineer_peer_thread_opened",
            "engineer_peer_thread_active",
        }
        engineer_defaults = set(
            _ENGINEER_NOTIFICATION_PRESETS["normal"]["enabled_events"]
        )

        def _is_legacy_broad_default(
                enabled_events, *, include_engineer_defaults: bool = False) -> bool:
            enabled_set = set(normalize_architect_enabled_events(enabled_events))
            if (
                    enabled_set == legacy_defaults
                    or enabled_set == legacy_defaults_without_engineer_peer):
                return True
            return bool(
                include_engineer_defaults and enabled_set == engineer_defaults
            )

        if not already:
            changed = []
            for agent_id, settings in self.agent_digest_settings.items():
                cell = self.agents.get(agent_id)
                if str(getattr(cell, "kind", "") or "").strip() != "architect":
                    continue
                enabled = normalize_architect_enabled_events(
                    getattr(settings, "enabled_events", []) or []
                )
                if not bool(getattr(settings, "architect_digest", False)):
                    settings.architect_digest = True
                if _is_legacy_broad_default(
                        enabled,
                        include_engineer_defaults=True,
                ):
                    settings.enabled_events = list(
                        _ARCHITECT_DIGEST_DEFAULT_ENABLED_EVENTS
                    )
                else:
                    settings.enabled_events = enabled
                changed.append((agent_id, settings))
            for agent_id, settings in changed:
                self.db.save_agent_digest_settings(agent_id, asdict(settings))
            self.db.defer_write(
                "ui_state", "save_ui_state", marker_key, "1",
            )

        if not group_already:
            changed_groups = []
            for group_name, settings in self.group_settings.items():
                enabled = normalize_architect_enabled_events(
                    getattr(settings, "architect_enabled_events", []) or []
                )
                if _is_legacy_broad_default(enabled):
                    enabled = list(_ARCHITECT_DIGEST_DEFAULT_ENABLED_EVENTS)
                current = list(
                    getattr(settings, "architect_enabled_events", []) or []
                )
                if current == enabled:
                    continue
                settings.architect_enabled_events = enabled
                changed_groups.append((group_name, settings))
            for group_name, settings in changed_groups:
                self.db.save_group_settings(group_name, settings)
            self.db.defer_write(
                "ui_state", "save_ui_state", group_marker_key, "1",
            )

    def _backfill_architect_suppress_empty_once(self) -> None:
        """One-time: flip ``suppress_empty=True`` on pre-existing architect rows.

        The ``suppress_empty`` column was added with default 0 to keep the
        migration trivial. Without this backfill, architects whose digest
        settings row predates the new column keep emitting empty heartbeat
        digests — which is exactly the user complaint that motivated the
        new flag. We run this exactly once (gated by a ``ui_state``
        marker) so a user who later explicitly sets the flag back to
        False is not overwritten on subsequent boots.
        """
        marker_key = "architect_digest_suppress_empty_backfilled"
        if not self.db:
            return
        try:
            already = self.db.load_ui_state_value(marker_key)
        except Exception:
            log.exception("Failed to read backfill marker %s", marker_key)
            return
        if already:
            return
        changed = []
        for agent_id, settings in self.agent_digest_settings.items():
            cell = self.agents.get(agent_id)
            if str(getattr(cell, "kind", "") or "").strip() != "architect":
                continue
            if bool(getattr(settings, "suppress_empty", False)):
                continue
            settings.suppress_empty = True
            changed.append((agent_id, settings))
        for agent_id, settings in changed:
            self.db.save_agent_digest_settings(agent_id, asdict(settings))
        self.db.defer_write(
            "ui_state", "save_ui_state", marker_key, "1",
        )

    def update_agent_digest_settings(self, agent_id: str, **fields):
        """Update digest settings for one engineer/architect recipient."""
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return
        settings = self.agent_digest_settings.get(agent_id)
        if settings is None:
            settings = AgentDigestSettings(
                **asdict(self.get_agent_digest_settings(agent_id))
            )
            settings.agent_id = agent_id
            self.agent_digest_settings[agent_id] = settings
        valid = set(AgentDigestSettings.__dataclass_fields__) - {"override_fields"}
        delivery_fields = {"paused", "push_interval", "max_interval", "heartbeat_interval",
                           "digest_verbosity", "enabled_events"}
        for key, value in fields.items():
            if key not in valid:
                continue
            if key in delivery_fields and value is None:
                settings.override_fields = [
                    name for name in settings.override_fields if name != key
                ]
                continue
            cell = self.agents.get(agent_id)
            is_architect = (
                str(getattr(cell, "kind", "") or "").strip() == "architect"
            )
            if key == "digest_verbosity":
                value = (
                    normalize_architect_digest_verbosity(value)
                    if is_architect
                    else normalize_engineer_digest_verbosity(value)
                )
            elif key == "enabled_events" and is_architect:
                value = normalize_architect_enabled_events(value)
            elif key == "enabled_events":
                value = [
                    str(item).strip() for item in (value or [])
                    if str(item).strip()
                ]
            elif key in {"paused", "architect_digest", "wake_on_digest",
                         "suppress_empty"}:
                value = bool(value)
            setattr(settings, key, value)
            if key in delivery_fields and key not in settings.override_fields:
                settings.override_fields.append(key)
        payload = asdict(settings)
        self._emit(
            "agent_digest_update",
            group=getattr(self.agents.get(agent_id), "group", "") or "",
            resolved=self.resolve_agent_settings(agent_id),
            **payload,
        )
        if self.db:
            self.db.save_agent_digest_settings(agent_id, payload)

    def ensure_agent_digest_settings(self, agent_id: str) -> AgentDigestSettings | None:
        """Persist a default digest-settings row when one does not exist yet."""
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return None
        if agent_id in self.agent_digest_settings:
            return self.agent_digest_settings[agent_id]
        self.update_agent_digest_settings(agent_id)
        return self.agent_digest_settings.get(agent_id)

    def _normalize_engineer_settings_value(self, key: str, value):
        if key == "autonomy_mode":
            return normalize_engineer_autonomy_mode(value)
        if key == "default_worker_concurrency":
            return normalize_default_worker_concurrency(value)
        if key == "wave_size_preference":
            return normalize_engineer_wave_size_preference(value)
        if key == "same_agent_follow_up_preference":
            return normalize_engineer_same_agent_follow_up_preference(value)
        if key == "digest_verbosity":
            return normalize_engineer_digest_verbosity(value)
        if key == "escalation_style":
            return normalize_engineer_escalation_style(value)
        if key in {
                "restrict_to_created_agents",
                "engineer_can_override_worker_provider"}:
            return bool(value)
        if key in {"pending_question_set_at", "pending_note_set_at"}:
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0
        if key in {"pending_question_actor_id", "pending_note_actor_id"}:
            return str(value or "").strip()
        if key == "engineer_fast_mode":
            return normalize_codex_fast_mode(value, strict=True)
        if key in {
                "engineer_model", "engineer_reasoning_effort",
                "engineer_fast_mode",
                "engineer_directory", "engineer_profile",
                "engineer_shell", "engineer_tab_color"}:
            return str(value or "").strip()
        return value

    def _apply_engineer_settings_fields(
            self, group: str, fields: dict) -> tuple[EngineerSettings, dict]:
        fields = dict(fields or {})
        pending_question_actor_id = str(
            fields.pop("_pending_question_actor_id", "") or ""
        ).strip()
        pending_note_actor_id = str(
            fields.pop("_pending_note_actor_id", "") or ""
        ).strip()
        ws = self.engineer_settings.get(group)
        if ws is None:
            ws = EngineerSettings(group=group)
            self.engineer_settings[group] = ws
        previous_pending_question = str(
            getattr(ws, "pending_question", "") or ""
        )
        previous_pending_actor_id = str(
            getattr(ws, "pending_question_actor_id", "") or ""
        ).strip()
        previous_pending_note = str(
            getattr(ws, "pending_note", "") or ""
        )
        previous_pending_note_actor_id = str(
            getattr(ws, "pending_note_actor_id", "") or ""
        ).strip()
        valid = set(EngineerSettings.__dataclass_fields__)
        applied = {}
        for key, value in fields.items():
            if key in valid:
                value = self._normalize_engineer_settings_value(key, value)
                setattr(ws, key, value)
                applied[key] = value
        if (
                "pending_question" in applied
                and "pending_question_set_at" not in applied):
            current_pending_question = str(
                getattr(ws, "pending_question", "") or ""
            )
            if current_pending_question:
                pending_question_actor_changed = bool(
                    pending_question_actor_id
                    and pending_question_actor_id != previous_pending_actor_id
                )
                pending_question_is_new = (
                    current_pending_question != previous_pending_question
                    or pending_question_actor_changed
                )
                if pending_question_is_new:
                    ws.pending_question_set_at = time.time()
                    applied["pending_question_set_at"] = ws.pending_question_set_at
                    if (
                            pending_question_actor_id
                            or "pending_question_actor_id" not in applied):
                        ws.pending_question_actor_id = pending_question_actor_id
                        applied["pending_question_actor_id"] = (
                            pending_question_actor_id
                        )
                if pending_question_actor_id:
                    ws.pending_question_actor_id = pending_question_actor_id
                    applied["pending_question_actor_id"] = pending_question_actor_id
            elif previous_pending_question:
                ws.pending_question_set_at = 0.0
                ws.pending_question_actor_id = ""
                applied["pending_question_set_at"] = 0.0
                applied["pending_question_actor_id"] = ""
        if (
                "pending_note" in applied
                and "pending_note_set_at" not in applied):
            current_pending_note = str(getattr(ws, "pending_note", "") or "")
            if current_pending_note:
                pending_note_actor_changed = bool(
                    pending_note_actor_id
                    and pending_note_actor_id != previous_pending_note_actor_id
                )
                pending_note_is_new = (
                    current_pending_note != previous_pending_note
                    or pending_note_actor_changed
                )
                if pending_note_is_new:
                    ws.pending_note_set_at = time.time()
                    applied["pending_note_set_at"] = ws.pending_note_set_at
                    if (
                            pending_note_actor_id
                            or "pending_note_actor_id" not in applied):
                        ws.pending_note_actor_id = pending_note_actor_id
                        applied["pending_note_actor_id"] = pending_note_actor_id
                if pending_note_actor_id:
                    ws.pending_note_actor_id = pending_note_actor_id
                    applied["pending_note_actor_id"] = pending_note_actor_id
            elif previous_pending_note:
                ws.pending_note_set_at = 0.0
                ws.pending_note_actor_id = ""
                applied["pending_note_set_at"] = 0.0
                applied["pending_note_actor_id"] = ""
        d = asdict(ws)
        d.pop("group", None)
        self._emit("engineer_settings_update", group=group, **d)
        if "pending_question" in applied:
            current_pending_question = str(
                getattr(ws, "pending_question", "") or ""
            )
            current_pending_actor_id = str(
                getattr(ws, "pending_question_actor_id", "") or ""
            ).strip()
            if current_pending_question and (
                    current_pending_question != previous_pending_question
                    or (
                        current_pending_actor_id
                        and current_pending_actor_id != previous_pending_actor_id
                    )):
                emit_engineer_awaiting_human_input_event(
                    self,
                    group=group,
                    question=current_pending_question,
                    engineer_id=current_pending_actor_id,
                )
            elif previous_pending_question and not current_pending_question:
                emit_engineer_ask_resolved_event(
                    self,
                    group=group,
                    question=previous_pending_question,
                    engineer_id=(
                        pending_question_actor_id
                        or previous_pending_actor_id
                    ),
                )
        return ws, applied

    def _sync_legacy_engineer_digest_settings(self, group: str,
                                            fields: dict) -> None:
        legacy_engineer = self.get_engineer_for_group(group)
        if legacy_engineer and legacy_engineer.id in self.agent_digest_settings:
            digest_fields = {
                key: fields[key]
                for key in (
                    "paused",
                    "push_interval",
                    "max_interval",
                    "heartbeat_interval",
                    "digest_verbosity",
                    "enabled_events",
                )
                if key in fields
            }
            if digest_fields:
                self.update_agent_digest_settings(legacy_engineer.id, **digest_fields)

    def update_engineer_settings(self, group: str, **fields):
        """Update engineer settings for a group."""
        ws, applied = self._apply_engineer_settings_fields(group, fields)
        if self.db:
            self.db.save_engineer_settings(group, asdict(ws))
        self._sync_legacy_engineer_digest_settings(group, applied)

    async def update_engineer_settings_async(self, group: str, **fields) -> bool:
        """Update and await persistence for engineer settings for a group."""
        ws, applied = self._apply_engineer_settings_fields(group, fields)
        if self.db:
            await self.db.save_engineer_settings_async(group, asdict(ws))
        self._sync_legacy_engineer_digest_settings(group, applied)
        return True

    def engineer_restricts_to_created_agents(self, group: str) -> bool:
        """Return whether the group's Engineer is restricted to owned agents."""
        return bool(
            self.get_engineer_settings(group).restrict_to_created_agents
        )

    def agent_is_visible_to_engineer(self, engineer_id: str, agent_id: str) -> bool:
        """Return whether ``agent_id`` is visible/controllable to ``engineer_id``.

        Visibility is always limited to cells in the same group. Engineer scope
        is strict across the agent-kind hierarchy: an engineer sees itself,
        architects/user-level principals for scope-up coordination, and only
        workers/terminals owned by that engineer.
        """
        engineer = self.agents.get(str(engineer_id or "").strip())
        agent = self.agents.get(str(agent_id or "").strip())
        if self.agent_is_tombstoned(engineer) or self.agent_is_tombstoned(agent):
            return False
        if not engineer or engineer.cell_type != "agent":
            return False
        if str(getattr(engineer, "kind", "") or "").strip() != "engineer":
            return False
        if not agent or agent.cell_type not in {"agent", "terminal"}:
            return False
        if not engineer.group or agent.group != engineer.group:
            return False

        engineer_id = str(getattr(engineer, "id", "") or "").strip()
        if not engineer_id:
            return False
        if str(getattr(agent, "id", "") or "").strip() == engineer_id:
            return True

        kind = str(getattr(agent, "kind", "") or "").strip()
        if kind in {"architect", "user", "human"}:
            return True
        if kind == "engineer":
            return False

        owner_id = str(getattr(agent, "owner_engineer_id", "") or "").strip()
        created_by_id = str(
            getattr(agent, "created_by_engineer_id", "") or ""
        ).strip()
        # An explicit owner is authoritative.  ``created_by_engineer_id`` is
        # retained as provenance and is only a legacy ownership fallback when
        # old rows have no explicit owner.  Otherwise transferring ownership
        # would leave the former creator able to inspect the worker.
        if owner_id == engineer_id or (not owner_id and created_by_id == engineer_id):
            return True

        if agent.cell_type == "terminal":
            parent_id = str(getattr(agent, "parent_id", "") or "").strip()
            if parent_id == engineer_id:
                return True
            parent = self.agents.get(parent_id)
            if (
                    parent
                    and str(getattr(parent, "group", "") or "").strip()
                    == engineer.group
            ):
                parent_owner_id = str(
                    getattr(parent, "owner_engineer_id", "") or ""
                ).strip()
                parent_created_by_id = str(
                    getattr(parent, "created_by_engineer_id", "") or ""
                ).strip()
                if (
                        str(getattr(parent, "id", "") or "").strip()
                        == engineer_id
                        or parent_owner_id == engineer_id
                        or (
                            not parent_owner_id
                            and parent_created_by_id == engineer_id
                        )
                ):
                    return True

        return False

    def engineer_can_access_task(
            self,
            engineer_id: str,
            task,
            *,
            allow_created: bool = True,
            allow_unassigned: bool = False) -> bool:
        """Return whether an Engineer may act on ``task``.

        Task access is group-bound and owned by explicit task assignment. Some
        mutating surfaces also allow the Engineer that created a task to keep
        managing it while it is unassigned or after reassignment.
        """
        engineer_id = str(engineer_id or "").strip()
        engineer = self.agents.get(engineer_id)
        if self.agent_is_tombstoned(engineer):
            return False
        if not engineer or getattr(engineer, "cell_type", "") != "agent":
            return False
        if str(getattr(engineer, "kind", "") or "").strip() != "engineer":
            return False

        if isinstance(task, str):
            task = self.board_tasks.get(str(task or "").strip())
        if not task:
            return False
        engineer_group = str(getattr(engineer, "group", "") or "").strip()
        task_group = str(getattr(task, "group", "") or "").strip()
        if not engineer_group or task_group != engineer_group:
            return False

        assigned_engineer_id = str(
            getattr(task, "assigned_engineer_id", "") or ""
        ).strip()
        if assigned_engineer_id == engineer_id:
            return True
        if allow_created:
            created_by_engineer_id = str(
                getattr(task, "created_by_engineer_id", "") or ""
            ).strip()
            if created_by_engineer_id == engineer_id:
                return True
        if allow_unassigned and not assigned_engineer_id:
            return True
        return False

    def _save_engineer_settings(self, group: str, emit: bool = True):
        ws = self.engineer_settings.get(group)
        if not ws:
            return
        d = asdict(ws)
        d.pop("group", None)
        if emit:
            self._emit("engineer_settings_update", group=group, **d)
        if self.db:
            self.db.save_engineer_settings(group, asdict(ws))

    def delete_agent_digest_settings(self, agent_id: str):
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return
        self.agent_digest_settings.pop(agent_id, None)
        if self.db:
            self.db.delete_agent_digest_settings(agent_id)

    def _open_human_asks_for_parent(self, parent_task_id: str,
                                    exclude_task_id: str = "") -> list[BoardTask]:
        asks: list[BoardTask] = []
        if not parent_task_id:
            return asks
        for task in self.board_tasks.values():
            if task.id == exclude_task_id:
                continue
            if task.parent_task_id != parent_task_id:
                continue
            if board_task_is_closed(task):
                continue
            if "torque:human" not in (task.labels or []):
                continue
            asks.append(task)
        return asks

    def _clear_parent_awaiting_input(self, parent: Optional[BoardTask],
                                     exclude_task_id: str = "",
                                     emit: bool = True):
        if not parent:
            return
        if self._open_human_asks_for_parent(parent.id, exclude_task_id):
            return

        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        if parent.status:
            parent.status = ""
            parent.updated_at = now_iso
            if emit:
                self.emit_task_upsert(parent)
            self._db_save_task(parent)

        root_id = parent.pipeline_root_id or parent.id
        if root_id == parent.id:
            return
        root = self.board_tasks.get(root_id)
        if root and root.status:
            root.status = ""
            root.updated_at = now_iso
            if emit:
                self.emit_task_upsert(root)
            self._db_save_task(root)

    def _expire_orphaned_ask(self, task: BoardTask, reason: str,
                             emit: bool = True) -> bool:
        if "torque:human" not in (task.labels or []) or board_task_is_closed(task):
            return False

        from datetime import datetime, timezone
        if task.lane != "Done" and self._is_finalization_root(task):
            allowed, _result = self._finalization_done_allowed(
                task, caller="expire_orphaned_ask"
            )
            if not allowed:
                return False
        parent = self.board_tasks.get(task.parent_task_id)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        changed = False

        if task.agent_id:
            task.agent_id = ""
            changed = True
        if task.status:
            task.status = ""
            changed = True
        if task.lane != "Done":
            task.lane = "Done"
            max_pos = max(
                (t.position for t in self.board_tasks.values()
                 if t.lane == "Done" and t.id != task.id),
                default=-1,
            )
            task.position = max_pos + 1
            for label in ("torque:blocked", "torque:error"):
                if label in task.labels:
                    task.labels.remove(label)
            changed = True
        if reason and not any(
                m.get("action") == "system"
                and m.get("message") == reason
                for m in (task.messages or [])):
            task.messages.append({
                "timestamp": now.timestamp(),
                "action": "system",
                "message": reason,
                "agent_name": "Torque",
            })
            changed = True

        if changed:
            task.updated_at = now_iso
            self._refresh_finalization_root_projection(task)
            if emit:
                self.emit_task_upsert(task)
            self._db_save_task(task)

        self._clear_parent_awaiting_input(
            parent, exclude_task_id=task.id, emit=emit)
        return changed

    def _agent_persisted_in_db(self, agent_id: str) -> bool:
        agent_id = str(agent_id or "").strip()
        if not agent_id or not self.db:
            return False
        try:
            return bool(self.db.agent_exists(agent_id))
        except Exception:
            log.exception("Failed to check persisted agent %s", agent_id)
            return False

    def _attention_source_agent_available(
            self, agent_id: str, live_agents: set[str], *,
            allow_persisted_agent_fallback: bool) -> bool:
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return False
        if agent_id in live_agents:
            return True
        return (
            allow_persisted_agent_fallback
            and self._agent_persisted_in_db(agent_id)
        )

    def cleanup_orphaned_attention(
            self, emit: bool = True, *,
            allow_persisted_agent_fallback: bool = True) -> dict[str, int]:
        """Expire asks and pending engineer questions whose source agent is gone."""
        cleaned = {"asks": 0, "engineer_questions": 0}
        live_agents = {
            aid for aid, cell in self.agents.items()
            if not self.agent_is_tombstoned(cell)
        }

        for group, ws in self.engineer_settings.items():
            gs = self.group_settings.get(group)
            engineer_id = gs.engineer_agent_id if gs else ""
            question_source_id = (
                str(getattr(ws, "pending_question_actor_id", "") or "").strip()
                or engineer_id
            )
            question_source_available = self._attention_source_agent_available(
                question_source_id,
                live_agents,
                allow_persisted_agent_fallback=allow_persisted_agent_fallback,
            )
            engineer_available = self._attention_source_agent_available(
                engineer_id,
                live_agents,
                allow_persisted_agent_fallback=allow_persisted_agent_fallback,
            )
            # Actor-scoped asks are durable: a pending human question should
            # survive the asking agent's live session ending (or a temporary
            # tombstone) so the eventual answer can be queued for its next
            # session.  Legacy rows without an actor still use the old
            # orphan cleanup behavior.
            if (
                    ws.pending_question
                    and not question_source_available
                    and not str(
                        getattr(ws, "pending_question_actor_id", "") or ""
                    ).strip()):
                stale_question = ws.pending_question
                stale_actor_id = (
                    str(getattr(ws, "pending_question_actor_id", "") or "").strip()
                    or engineer_id
                )
                log.warning(
                    "Clearing stale engineer pending question for group=%s "
                    "source_agent_id=%r in_memory=%s persisted=%s "
                    "pending_question_len=%d",
                    group,
                    question_source_id,
                    bool(question_source_id and question_source_id in live_agents),
                    bool(
                        question_source_id
                        and self._agent_persisted_in_db(question_source_id)
                    ),
                    len(ws.pending_question or ""),
                )
                ws.pending_question = ""
                ws.pending_question_set_at = 0.0
                ws.pending_question_actor_id = ""
                ws.paused = False
                ws.pending_note = ""
                ws.pending_note_kind = ""
                self._save_engineer_settings(group, emit=emit)
                if emit:
                    emit_engineer_ask_resolved_event(
                        self,
                        group=group,
                        question=stale_question,
                        engineer_id=stale_actor_id,
                    )
                cleaned["engineer_questions"] += 1
            if ws.pending_note and not engineer_available:
                ws.pending_note = ""
                ws.pending_note_kind = ""
                self._save_engineer_settings(group, emit=emit)

        reason = "Ask expired because the source agent is no longer available."
        for task in list(self.board_tasks.values()):
            if "torque:human" not in (task.labels or []) or board_task_is_closed(task):
                continue
            parent = self.board_tasks.get(task.parent_task_id)
            reply_agent_id = str(
                getattr(task, "reply_agent_id", "") or ""
            ).strip()
            parent_agent_id = parent.agent_id if parent else ""
            parent_agent_available = self._attention_source_agent_available(
                reply_agent_id or parent_agent_id,
                live_agents,
                allow_persisted_agent_fallback=allow_persisted_agent_fallback,
            )
            # New ask tasks stamp reply_agent_id with the logical asking
            # agent.  That decouples the ask from the parent task's current
            # live session/assignment, so do not expire it merely because the
            # source agent is currently unavailable.
            if not parent or (not reply_agent_id and not parent_agent_available):
                if self._expire_orphaned_ask(task, reason, emit=emit):
                    cleaned["asks"] += 1

        return cleaned

    def journal_append(self, group: str, entry_type: str,
                       entry: str, author_cell_id: str = "",
                       timestamp: float | None = None,
                       source_key: str = "") -> dict:
        """Append an entry to the engineer journal. Returns the entry dict."""
        import time
        try:
            ts = float(timestamp) if timestamp is not None else time.time()
        except (TypeError, ValueError):
            ts = time.time()
        entry_id = 0
        inserted = True
        author_cell_id = str(author_cell_id or "").strip()
        source_key = str(source_key or "").strip()
        if self.db:
            entry_id, inserted = self.db.save_journal_entry(
                group,
                ts,
                entry_type,
                entry,
                author_cell_id=author_cell_id,
                source_key=source_key,
                return_inserted=True,
            )
        evt = {"id": entry_id, "group": group, "timestamp": ts,
               "type": entry_type, "entry": entry,
               "author_cell_id": author_cell_id}
        if not inserted:
            evt["duplicate"] = True
            return evt
        self._emit("journal_append", **evt)
        return evt

    def journal_read(self, group: str, limit: int = 20,
                     entry_type: str = "",
                     author_cell_id: str = "") -> list[dict]:
        """Read recent journal entries for a group."""
        if self.db:
            return self.db.load_journal_entries(
                group,
                limit,
                entry_type,
                author_cell_id=str(author_cell_id or "").strip(),
            )
        return []
