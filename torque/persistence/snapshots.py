"""Bulk state snapshot persistence and startup loading."""

import json
import time
from dataclasses import asdict

from torque.db_board import (
    _BOARD_TASK_COLUMNS,
    decode_auto_dispatch_queue_rows,
    decode_board_task_row,
)
from torque import profiling
from torque.persistence.common import (
    GROUP_SETTINGS_BOOL_FIELDS as _GS_BOOL_FIELDS,
    GROUP_SETTINGS_JSON_FIELDS as _GS_JSON_FIELDS,
    group_settings_field_names as _group_settings_field_names,
    json_loads_default as _json_loads_default,
    snapshot_db_payload as _snapshot_db_payload,
)


class SnapshotPersistenceMixin:
    """Save and restore complete state snapshots at migration/startup boundaries."""

    def save_task_and_agents(self, task, agents) -> None:
        """Atomically persist one task and its linked worker snapshots."""
        agent_rows = list(agents or [])

        def _operation():
            try:
                self._conn.execute("BEGIN")
                for cell in agent_rows:
                    self._insert_agent_row(self._conn, cell)
                self._insert_board_task_row(self._conn, task)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        with profiling.timer("sqlite_write_ms"), \
                profiling.timer("sqlite_write_save_task_and_agents_ms"):
            self._run_sqlite_write_with_lock_retry(
                _operation,
                surface="task_and_agents",
            )

    async def save_task_and_agents_async(self, task, agents) -> None:
        """Queue and await the task/worker ownership transaction."""
        return await self._enqueue_async_write(
            "task_and_agents",
            "save_task_and_agents",
            _snapshot_db_payload(task),
            _snapshot_db_payload(list(agents or [])),
        )

    def save_agents(self, cells) -> None:
        """Upsert multiple agent snapshots in one SQLite transaction."""
        snapshots = list(cells or [])
        if not snapshots:
            return
        with profiling.timer("sqlite_write_ms"), \
                profiling.timer("sqlite_write_save_agents_ms"):
            try:
                for cell in snapshots:
                    self._insert_agent_row(self._conn, cell)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def backfill_agent_history(self):
        """Create history records for existing agents that lack them."""
        import time
        if (
            self._column_exists("agents", "role")
            and self._column_exists("agents", "template")
        ):
            template_expr = (
                "CASE WHEN TRIM(COALESCE(role, '')) != '' "
                "THEN role ELSE template END"
            )
        elif self._column_exists("agents", "role"):
            template_expr = "role"
        elif self._column_exists("agents", "template"):
            template_expr = "template"
        else:
            template_expr = "''"
        rows = self._conn.execute(
            f"SELECT id, name, slug, group_name, agent_type, {template_expr}, "
            "worktree_branch, tasks_dispatched FROM agents "
            "WHERE cell_type='agent' AND id NOT IN "
            "(SELECT id FROM agent_history)").fetchall()
        for r in rows:
            self._conn.execute("""
                INSERT OR IGNORE INTO agent_history
                    (id, name, slug, "group", agent_type, template,
                     created_at, worktree_branch, total_tasks, status)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (r[0], r[1], r[2], r[3], r[4], r[5],
                  time.time(), r[6], r[7], "active"))
        self._conn.commit()

    # -- Bulk save (transitional) -------------------------------------------

    def save_all(self, state_dict: dict):
        """Bulk-write entire state to DB (used by migrate_from_json)."""
        c = self._conn.cursor()
        try:
            # Agents
            c.execute("DELETE FROM agents")
            for aid, a in state_dict.get("agents", {}).items():
                item = dict(a) if isinstance(a, dict) else asdict(a)
                item.setdefault("id", aid)
                self._insert_agent_row(c, item)

            # Groups + members
            c.execute("DELETE FROM groups")
            c.execute("DELETE FROM group_members")
            group_slugs = state_dict.get("group_slugs", {})
            for pos, (gname, members) in enumerate(
                    state_dict.get("groups", {}).items()):
                c.execute(
                    "INSERT INTO groups (name, slug, position) VALUES (?,?,?)",
                    (gname, group_slugs.get(gname, ""), pos))
                for mpos, aid in enumerate(members):
                    c.execute(
                        "INSERT INTO group_members "
                        "(group_name, agent_id, position) VALUES (?,?,?)",
                        (gname, aid, mpos))

            # Group settings
            c.execute("DELETE FROM group_settings")
            gs_fields = _group_settings_field_names()
            for gname, gs in state_dict.get("group_settings", {}).items():
                d = dict(gs) if isinstance(gs, dict) else asdict(gs)
                if gs_fields is not None:
                    d = {k: v for k, v in d.items() if k in gs_fields}
                for k in _GS_JSON_FIELDS:
                    if k in d:
                        d[k] = json.dumps(d[k])
                for k in _GS_BOOL_FIELDS:
                    if k in d:
                        d[k] = int(d[k])
                cols = ["group_name"] + list(d.keys())
                placeholders = ",".join(["?"] * len(cols))
                col_str = ",".join(cols)
                c.execute(
                    f"INSERT INTO group_settings ({col_str}) "
                    f"VALUES ({placeholders})",
                    [gname] + list(d.values()))

            # Board lanes
            c.execute("DELETE FROM board_lanes")
            for pos, lane in enumerate(
                    state_dict.get("board_lanes", [])):
                c.execute(
                    "INSERT INTO board_lanes (name, position) VALUES (?,?)",
                    (lane, pos))

            # Board tasks
            c.execute("DELETE FROM board_tasks")
            for tid, t in state_dict.get("board_tasks", {}).items():
                item = dict(t) if isinstance(t, dict) else asdict(t)
                item.setdefault("id", tid)
                self._insert_board_task_row(c, item)

            # Auto-dispatch queues
            c.execute("DELETE FROM auto_dispatch_queue")
            for gname, entries in state_dict.get(
                    "auto_dispatch_queues", {}).items():
                for pos, entry in enumerate(entries):
                    item = dict(entry)
                    c.execute("""
                        INSERT INTO auto_dispatch_queue
                            (group_name, position, task_id, agent_group,
                             max_concurrent, target_agent_id,
                             engineer_owner_id, provider, enqueued_at)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, (
                        gname,
                        pos,
                        item.get("task_id", ""),
                        item.get("agent_group", ""),
                        int(item.get("max_concurrent", 1) or 1),
                        item.get("target_agent_id", ""),
                        item.get("engineer_owner_id", ""),
                        item.get("provider", ""),
                        item.get("enqueued_at", ""),
                    ))

            # UI state
            c.execute("DELETE FROM ui_state")
            for key in (
                "panel_active",
                "board_panel_height",
                "active_group",
                "selected_principal_id",
                "selected_agent_id",
                "standalone_panel_layout",
                "detached_panels",
                "window_bounds",
                "workspace_sidebar_width",
                "engineer_panel_split_fraction",
                "context_panel_split_ratio",
                "supervisor_panel_state",
            ):
                val = state_dict.get(key)
                if val is not None:
                    if key in {
                        "standalone_panel_layout",
                        "detached_panels",
                        "window_bounds",
                        "supervisor_panel_state",
                    }:
                        val = json.dumps(val)
                    c.execute(
                        "INSERT INTO ui_state (key, value) VALUES (?,?)",
                        (key, str(val)))
            if state_dict.get("events_dismissed_attention") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "events_dismissed_attention",
                        json.dumps(state_dict.get("events_dismissed_attention")
                                   or {}),
                    ),
                )
            if state_dict.get("mission_control_dismissed_cards") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "mission_control_dismissed_cards",
                        json.dumps(state_dict.get("mission_control_dismissed_cards")
                                   or {}),
                    ),
                )
            if state_dict.get("board_filters_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_filters_by_group",
                        json.dumps(state_dict.get("board_filters_by_group")
                                   or {}),
                    ),
                )
            if state_dict.get("board_selected_lanes_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_selected_lanes_by_group",
                        json.dumps(state_dict.get(
                            "board_selected_lanes_by_group") or {}),
                    ),
                )
            if state_dict.get("board_hidden_wide_lanes_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_hidden_wide_lanes_by_group",
                        json.dumps(state_dict.get(
                            "board_hidden_wide_lanes_by_group") or {}),
                    ),
                )
            if state_dict.get("board_saved_views_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_saved_views_by_group",
                        json.dumps(state_dict.get("board_saved_views_by_group")
                                   or {}),
                    ),
                )
            if state_dict.get("board_lane_sorts_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_lane_sorts_by_group",
                        json.dumps(state_dict.get("board_lane_sorts_by_group")
                                   or {}),
                    ),
                )
            if state_dict.get("board_card_density_by_group") is not None:
                c.execute(
                    "INSERT INTO ui_state (key, value) VALUES (?,?)",
                    (
                        "board_card_density_by_group",
                        json.dumps(state_dict.get("board_card_density_by_group")
                                   or {}),
                    ),
                )

            self._conn.commit()
            self._reseed_task_id_counters()
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # -- Read methods (daemon startup + CLI) --------------------------------

    def load_all(self, *, dehydrate_archived_artifacts: bool = False) -> dict:
        """Load full state from SQLite. Returns dict matching state.json
        structure for easy consumption by MatrixState.load()."""
        c = self._conn.cursor()

        # Agents
        agents = {}
        for row in c.execute(
                "SELECT * FROM agents").fetchall():
            cols = [d[0] for d in c.description]
            d = dict(zip(cols, row))
            # Map group_name back to 'group' for AgentCell
            d["group"] = d.pop("group_name")
            d.setdefault("template", str(d.get("role", "") or ""))
            d.setdefault(
                "created_by_engineer_id",
                str(d.get("owner_engineer_id", "") or ""),
            )
            d["worktree_auto_checkpoint"] = bool(
                d.get("worktree_auto_checkpoint", 0))
            d["checkpoint_on_progress"] = bool(
                d.get("checkpoint_on_progress", 0))
            d["worktree_merge_squash"] = bool(
                d.get("worktree_merge_squash", 1))
            d["persistent"] = bool(d.get("persistent", 0))
            d["session_resume"] = bool(d.get("session_resume", 1))
            d["queue_empty_emitted"] = bool(
                d.get("queue_empty_emitted", 1)
            )
            d["last_progress_at"] = float(d.get("last_progress_at", 0) or 0)
            d["last_heartbeat_at"] = float(d.get("last_heartbeat_at", 0) or 0)
            d["last_activity_at"] = max(
                float(d.get("last_activity_at", 0) or 0),
                d["last_progress_at"],
                d["last_heartbeat_at"],
            )
            d["deleted_at"] = float(d.get("deleted_at", 0) or 0)
            d["permanent_delete_after"] = float(
                d.get("permanent_delete_after", 0) or 0
            )
            d["agent_class_assigned_at"] = float(
                d.get("agent_class_assigned_at", 0) or 0
            )
            d["effective_agent_class_applied_at"] = float(
                d.get("effective_agent_class_applied_at", 0) or 0
            )
            d["effective_agent_class_snapshot"] = _json_loads_default(
                d.get("effective_agent_class_snapshot", "{}"),
                {},
            )
            raw_specs = d.get("engineer_specializations", "")
            if isinstance(raw_specs, str):
                try:
                    decoded = json.loads(raw_specs or "[]")
                except (json.JSONDecodeError, TypeError):
                    decoded = []
            else:
                decoded = raw_specs or []
            if not isinstance(decoded, list):
                decoded = []
            d["engineer_specializations"] = [
                str(item or "").strip()
                for item in decoded
                if str(item or "").strip()
            ]
            agents[d["id"]] = d

        # Groups (ordered)
        groups = {}
        group_slugs = {}
        for row in c.execute(
                "SELECT name, slug FROM groups ORDER BY position"):
            groups[row[0]] = []
            if row[1]:
                group_slugs[row[0]] = row[1]

        # Group members
        for row in c.execute(
                "SELECT group_name, agent_id FROM group_members "
                "ORDER BY group_name, position"):
            gname, aid = row
            if gname in groups:
                groups[gname].append(aid)

        # Group settings
        group_settings = {}
        rows = c.execute("SELECT * FROM group_settings").fetchall()
        if rows:
            cols = [d[0] for d in c.description]
            gs_fields = _group_settings_field_names()
            for row in rows:
                d = dict(zip(cols, row))
                gname = d.pop("group_name")
                if gs_fields is not None:
                    d = {k: v for k, v in d.items() if k in gs_fields}
                # Decode JSON fields
                for k in _GS_JSON_FIELDS:
                    if k in d and isinstance(d[k], str):
                        try:
                            d[k] = json.loads(d[k])
                        except (json.JSONDecodeError, TypeError):
                            d[k] = [] if k in {"board_default_labels",
                                                "worktree_symlinks",
                                                "worktree_submodules",
                                                "default_engineer_specializations",
                                                "architect_enabled_events"} else {}
                    if k == "board_sync_github" and k in d \
                            and not isinstance(d[k], dict):
                        d[k] = {}
                # Decode booleans
                for k in _GS_BOOL_FIELDS:
                    if k in d:
                        d[k] = bool(d[k])
                group_settings[gname] = d

        # Board lanes
        board_lanes = [row[0] for row in c.execute(
            "SELECT name FROM board_lanes ORDER BY position")]

        # Board tasks
        board_tasks = {}
        if dehydrate_archived_artifacts:
            # Do this projection in SQLite, before Python decodes the task
            # row. json_remove handles both legacy body copies; json_each and
            # json_group_array preserve the artifact list and its metadata.
            # The default full read remains available to offline/CLI callers.
            projected_columns = []
            for column in _BOARD_TASK_COLUMNS:
                if column == "artifacts":
                    projected_columns.append("""
                        CASE WHEN lane = 'Archived'
                              OR instr(labels, '"torque:archived"') > 0
                        THEN COALESCE(
                            (SELECT json_group_array(json_remove(
                                value, '$.content', '$.storage.content'
                            )) FROM json_each(CASE
                                WHEN json_valid(board_tasks.artifacts)
                                THEN board_tasks.artifacts ELSE '[]' END)),
                            '[]'
                        ) ELSE artifacts END AS artifacts
                    """)
                else:
                    projected_columns.append(column)
            rows = c.execute(
                "SELECT " + ", ".join(projected_columns) + " FROM board_tasks"
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM board_tasks").fetchall()
        if rows:
            cols = [d[0] for d in c.description]
            for row in rows:
                d = decode_board_task_row(row, cols)
                d.setdefault(
                    "engineer_owner_id",
                    str(d.get("assigned_engineer_id", "") or ""),
                )
                board_tasks[d["id"]] = d

        # UI state
        ui = {}
        for row in c.execute("SELECT key, value FROM ui_state"):
            ui[row[0]] = row[1]
        try:
            board_filters_by_group = json.loads(
                ui.get("board_filters_by_group", "{}") or "{}"
            )
            if not isinstance(board_filters_by_group, dict):
                board_filters_by_group = {}
        except Exception:
            board_filters_by_group = {}
        try:
            board_selected_lanes_by_group = json.loads(
                ui.get("board_selected_lanes_by_group", "{}") or "{}"
            )
            if not isinstance(board_selected_lanes_by_group, dict):
                board_selected_lanes_by_group = {}
        except Exception:
            board_selected_lanes_by_group = {}
        try:
            board_hidden_wide_lanes_by_group = json.loads(
                ui.get("board_hidden_wide_lanes_by_group", "{}") or "{}"
            )
            if not isinstance(board_hidden_wide_lanes_by_group, dict):
                board_hidden_wide_lanes_by_group = {}
        except Exception:
            board_hidden_wide_lanes_by_group = {}
        try:
            board_saved_views_by_group = json.loads(
                ui.get("board_saved_views_by_group", "{}") or "{}"
            )
            if not isinstance(board_saved_views_by_group, dict):
                board_saved_views_by_group = {}
        except Exception:
            board_saved_views_by_group = {}
        try:
            board_lane_sorts_by_group = json.loads(
                ui.get("board_lane_sorts_by_group", "{}") or "{}"
            )
            if not isinstance(board_lane_sorts_by_group, dict):
                board_lane_sorts_by_group = {}
        except Exception:
            board_lane_sorts_by_group = {}
        try:
            board_card_density_by_group = json.loads(
                ui.get("board_card_density_by_group", "{}") or "{}"
            )
            if not isinstance(board_card_density_by_group, dict):
                board_card_density_by_group = {}
        except Exception:
            board_card_density_by_group = {}
        try:
            detached_panels = json.loads(
                ui.get("detached_panels", "{}") or "{}"
            )
            if not isinstance(detached_panels, dict):
                detached_panels = {}
        except Exception:
            detached_panels = {}
        try:
            window_bounds = json.loads(
                ui.get("window_bounds", "{}") or "{}"
            )
            if not isinstance(window_bounds, dict):
                window_bounds = {}
        except Exception:
            window_bounds = {}
        try:
            workspace_sidebar_width = int(
                ui.get("workspace_sidebar_width", "0") or "0"
            )
        except (TypeError, ValueError):
            workspace_sidebar_width = 0
        try:
            terminal_direct_messages_height = max(
                0,
                int(ui.get("terminal_direct_messages_height", "0") or "0"),
            )
        except (TypeError, ValueError):
            terminal_direct_messages_height = 0
        try:
            terminal_compose_height = max(
                0,
                int(ui.get("terminal_compose_height", "0") or "0"),
            )
        except (TypeError, ValueError):
            terminal_compose_height = 0
        try:
            context_panel_split_ratio = float(
                ui.get("context_panel_split_ratio", "0.38") or "0.38"
            )
        except (TypeError, ValueError):
            context_panel_split_ratio = 0.38
        try:
            engineer_panel_split_fraction = float(
                ui.get("engineer_panel_split_fraction", "0.30") or "0.30"
            )
        except (TypeError, ValueError):
            engineer_panel_split_fraction = 0.30
        try:
            supervisor_panel_state = json.loads(
                ui.get("supervisor_panel_state", "{}") or "{}"
            )
            if not isinstance(supervisor_panel_state, dict):
                supervisor_panel_state = {}
        except Exception:
            supervisor_panel_state = {}

        # Global settings
        global_settings = {}
        for row in c.execute("SELECT key, value FROM global_settings"):
            try:
                global_settings[row[0]] = json.loads(row[1])
            except (json.JSONDecodeError, TypeError):
                global_settings[row[0]] = row[1]

        # Schedules
        schedules = {}
        try:
            rows = c.execute("SELECT * FROM schedules").fetchall()
            if rows:
                cols = [d[0] for d in c.description]
                for row in rows:
                    d = dict(zip(cols, row))
                    d["group"] = d.pop("group_name", "")
                    d["enabled"] = bool(d.get("enabled", 1))
                    try:
                        d["labels"] = json.loads(d.get("labels", "[]"))
                    except (json.JSONDecodeError, TypeError):
                        d["labels"] = []
                    try:
                        d["action_vars"] = json.loads(
                            d.get("action_vars", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        d["action_vars"] = {}
                    schedules[d["id"]] = d
        except Exception:
            pass  # table may not exist yet on first load

        try:
            agent_message_loops = self.load_agent_message_loops()
        except Exception:
            agent_message_loops = {}

        auto_dispatch_queues = {}
        try:
            rows = c.execute(
                "SELECT group_name, position, task_id, agent_group, "
                "max_concurrent, target_agent_id, engineer_owner_id, "
                "provider, enqueued_at "
                "FROM auto_dispatch_queue ORDER BY group_name, position"
            ).fetchall()
            auto_dispatch_queues = decode_auto_dispatch_queue_rows(rows)
        except Exception:
            auto_dispatch_queues = {}

        task_id_aliases = self.load_task_id_aliases()
        task_id_counters = self.load_task_id_counters()
        pipeline_task_counters = self.load_pipeline_task_counters()

        return {
            "agents": agents,
            "groups": groups,
            "group_slugs": group_slugs,
            "group_settings": group_settings,
            "board_lanes": board_lanes,
            "board_tasks": board_tasks,
            "schedules": schedules,
            "agent_message_loops": agent_message_loops,
            "auto_dispatch_queues": auto_dispatch_queues,
            "panel_active": ui.get("panel_active", "")
                or ("board" if ui.get("board_panel_open", "False") == "True"
                    else ""),
            "board_panel_height": int(ui.get("board_panel_height", "0")),
            "active_group": str(ui.get("active_group", "") or ""),
            "selected_principal_id": str(
                ui.get("selected_principal_id", "") or ""
            ),
            "selected_agent_id": str(
                ui.get("selected_agent_id", "") or ""
            ),
            "standalone_panel_layout": (
                json.loads(ui.get("standalone_panel_layout", "{}"))
                if ui.get("standalone_panel_layout") else {}
            ),
            "detached_panels": detached_panels,
            "window_bounds": window_bounds,
            "workspace_sidebar_width": workspace_sidebar_width,
            "terminal_direct_messages_height": (
                terminal_direct_messages_height
            ),
            "terminal_compose_height": terminal_compose_height,
            "engineer_panel_split_fraction": engineer_panel_split_fraction,
            "context_panel_split_ratio": context_panel_split_ratio,
            "supervisor_panel_state": supervisor_panel_state,
            "events_dismissed_attention": (
                json.loads(ui.get("events_dismissed_attention", "{}"))
                if ui.get("events_dismissed_attention") else {}
            ),
            "mission_control_dismissed_cards": (
                json.loads(ui.get("mission_control_dismissed_cards", "{}"))
                if ui.get("mission_control_dismissed_cards") else {}
            ),
            "board_filters_by_group": board_filters_by_group,
            "board_selected_lanes_by_group": board_selected_lanes_by_group,
            "board_hidden_wide_lanes_by_group": board_hidden_wide_lanes_by_group,
            "board_saved_views_by_group": board_saved_views_by_group,
            "board_lane_sorts_by_group": board_lane_sorts_by_group,
            "board_card_density_by_group": board_card_density_by_group,
            "global_settings": global_settings,
            "task_id_aliases": task_id_aliases,
            "task_id_counters": task_id_counters,
            "pipeline_task_counters": pipeline_task_counters,
        }

    def has_data(self) -> bool:
        """Check if the DB has any persisted state."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM agents").fetchone()
        groups = self._conn.execute(
            "SELECT COUNT(*) FROM groups").fetchone()
        return (row and row[0] > 0) or (groups and groups[0] > 0)
