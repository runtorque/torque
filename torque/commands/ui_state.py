"""Persisted UI and board-presentation command handlers."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ..dispatch_registry import AsyncHandlerRegistry
from ..state import MatrixState


UI_STATE_COMMAND_NAMES = frozenset({
    "board_add_lane",
    "board_rename_lane",
    "board_remove_lane",
    "board_reorder_lanes",
    "board_set_panel",
    "ui_select_group",
    "ui_select_principal",
    "select_agent",
    "ui_select_agent",
    "ui_set_window_bounds",
    "ui_set_workspace_sidebar_width",
    "ui_set_terminal_direct_messages_height",
    "ui_set_terminal_compose_height",
    "standalone_set_panel_layout",
    "ui_set_detached_panels",
    "ui_set_detached_panel_bounds",
    "first_run_complete",
    "ui_set_engineer_panel_split",
    "ui_set_context_panel_split",
    "ui_set_supervisor_panel_state",
    "events_dismiss",
    "mission_control_dismiss",
    "board_set_filters",
    "board_set_selected_lanes",
    "board_set_hidden_wide_lanes",
    "board_set_saved_views",
    "board_set_lane_sorts",
    "board_set_card_density",
})


def _handle_ui_state_command(data: dict, state: MatrixState):
    cmd = str(data.get("cmd", "") or "").strip()
    result = None
    if cmd == "board_add_lane":
        name = data.get("name", "").strip()
        if not name:
            result = {"type": "error",
                      "message": "Lane name cannot be empty"}
        else:
            state.board_add_lane(name, data.get("position"))

    elif cmd == "board_rename_lane":
        state.board_rename_lane(
            data.get("old_name", ""),
            data.get("new_name", "").strip())

    elif cmd == "board_remove_lane":
        state.board_remove_lane(
            data.get("name", ""),
            data.get("move_tasks_to", ""))

    elif cmd == "board_reorder_lanes":
        state.board_reorder_lanes(data.get("lanes", []))

    elif cmd == "board_set_panel":
        if "active" in data:
            state.panel_active = str(data["active"])
            state._emit("ui_update", key="panel_active",
                        value=state.panel_active)
            state._db_save_ui("panel_active",
                              state.panel_active)
        elif "open" in data:
            # Backward compat
            state.panel_active = "board" if data["open"] \
                else ""
            state._emit("ui_update", key="panel_active",
                        value=state.panel_active)
            state._db_save_ui("panel_active",
                              state.panel_active)
        if "height" in data:
            state.board_panel_height = int(data["height"])
            state._emit("ui_update", key="board_panel_height",
                        value=state.board_panel_height)
            state._db_save_ui("board_panel_height",
                              state.board_panel_height)

    elif cmd == "ui_select_group":
        raw_group = str(data.get("group", "") or "").strip()
        if raw_group and raw_group not in state.groups:
            raw_group = ""
        state.active_group = raw_group
        state._emit(
            "ui_update",
            key="active_group",
            value=state.active_group,
        )
        state._db_save_ui("active_group", state.active_group)

    elif cmd == "ui_select_principal":
        raw_principal = str(data.get("principal_id", "") or "").strip()
        # Empty string is "user" (the default principal).
        # Otherwise must be an existing architect agent.
        if raw_principal:
            target = state.agents.get(raw_principal)
            if not target or (target.kind or "") != "architect":
                raw_principal = ""
        state.selected_principal_id = raw_principal
        state._emit(
            "ui_update",
            key="selected_principal_id",
            value=state.selected_principal_id,
        )
        state._db_save_ui(
            "selected_principal_id",
            state.selected_principal_id,
        )

    elif cmd in {"select_agent", "ui_select_agent"}:
        raw_agent_id = str(data.get("id", "") or "").strip()
        if raw_agent_id:
            target = state.agents.get(raw_agent_id)
            if not target or state.agent_is_tombstoned(target):
                raw_agent_id = ""
            elif target.cell_type == "terminal" and target.parent_id:
                raw_agent_id = target.parent_id
        state.selected_agent_id = raw_agent_id
        state._emit(
            "ui_update",
            key="selected_agent_id",
            value=state.selected_agent_id,
        )
        state._db_save_ui(
            "selected_agent_id",
            state.selected_agent_id,
        )

    elif cmd == "ui_set_window_bounds":
        raw_window = str(data.get("window", "") or "").strip()
        bounds = data.get("bounds", {})
        if not raw_window or not isinstance(bounds, dict):
            result = {
                "type": "error",
                "message": "Invalid window bounds state",
            }
        else:
            normalized = {}
            for key in ("x", "y", "width", "height"):
                value = bounds.get(key)
                if value is None:
                    continue
                try:
                    normalized[key] = float(value)
                except (TypeError, ValueError):
                    continue
            display_id = str(bounds.get("display_id", "") or "").strip()
            if display_id:
                normalized["display_id"] = display_id
            if normalized:
                next_bounds = dict(state.window_bounds or {})
                next_bounds[raw_window] = normalized
                state.window_bounds = next_bounds
            else:
                state.window_bounds = {
                    key: value
                    for key, value in (state.window_bounds or {}).items()
                    if key != raw_window
                }
            state._emit(
                "ui_update",
                key="window_bounds",
                value=state.window_bounds,
            )
            state._db_save_ui(
                "window_bounds",
                json.dumps(state.window_bounds),
            )

    elif cmd == "ui_set_workspace_sidebar_width":
        try:
            width = int(data.get("width", 0) or 0)
        except (TypeError, ValueError):
            width = 0
        state.workspace_sidebar_width = max(0, width)
        state._emit(
            "ui_update",
            key="workspace_sidebar_width",
            value=state.workspace_sidebar_width,
        )
        state._db_save_ui(
            "workspace_sidebar_width",
            state.workspace_sidebar_width,
        )

    elif cmd == "ui_set_terminal_direct_messages_height":
        try:
            height = int(data.get("height", 0) or 0)
        except (TypeError, ValueError):
            height = 0
        state.terminal_direct_messages_height = max(0, height)
        state._emit(
            "ui_update",
            key="terminal_direct_messages_height",
            value=state.terminal_direct_messages_height,
        )
        state._db_save_ui(
            "terminal_direct_messages_height",
            state.terminal_direct_messages_height,
        )

    elif cmd == "ui_set_terminal_compose_height":
        try:
            height = int(data.get("height", 0) or 0)
        except (TypeError, ValueError):
            height = 0
        state.terminal_compose_height = max(0, height)
        state._emit(
            "ui_update",
            key="terminal_compose_height",
            value=state.terminal_compose_height,
        )
        state._db_save_ui(
            "terminal_compose_height",
            state.terminal_compose_height,
        )

    elif cmd == "standalone_set_panel_layout":
        layout = data.get("layout", {})
        if not isinstance(layout, dict):
            result = {
                "type": "error",
                "message": "Invalid standalone panel layout",
            }
        else:
            state.standalone_panel_layout = layout
            state._emit(
                "ui_update",
                key="standalone_panel_layout",
                value=state.standalone_panel_layout,
            )
            state._db_save_ui(
                "standalone_panel_layout",
                json.dumps(state.standalone_panel_layout),
            )

    elif cmd == "ui_set_detached_panels":
        detached_panels = data.get("detached_panels", {})
        if not isinstance(detached_panels, dict):
            result = {
                "type": "error",
                "message": "Invalid detached panel state",
            }
        else:
            normalized = {}
            for panel, raw in detached_panels.items():
                panel = str(panel or "").strip()
                if not panel or not isinstance(raw, dict):
                    continue
                item = dict(raw)
                bounds = item.get("bounds")
                if bounds is not None and not isinstance(bounds, dict):
                    item.pop("bounds", None)
                label = str(item.get("label", "") or "").strip()
                if label:
                    item["label"] = label
                normalized[panel] = item
            state.detached_panels = normalized
            state._emit(
                "ui_update",
                key="detached_panels",
                value=state.detached_panels,
            )
            state._db_save_ui(
                "detached_panels",
                json.dumps(state.detached_panels),
            )

    elif cmd == "ui_set_detached_panel_bounds":
        panel = str(data.get("panel", "") or "").strip()
        label = str(data.get("label", "") or "").strip()
        bounds = data.get("bounds", {})
        if not panel or not isinstance(bounds, dict):
            result = {
                "type": "error",
                "message": "Invalid detached panel bounds state",
            }
        else:
            normalized_bounds = {}
            for key in ("x", "y", "width", "height"):
                value = bounds.get(key)
                if value is None:
                    continue
                try:
                    normalized_bounds[key] = float(value)
                except (TypeError, ValueError):
                    continue
            display_id = str(bounds.get("display_id", "") or "").strip()
            if display_id:
                normalized_bounds["display_id"] = display_id
            next_panels = dict(state.detached_panels or {})
            entry = dict(next_panels.get(panel) or {})
            if label:
                entry["label"] = label
            if normalized_bounds:
                entry["bounds"] = normalized_bounds
            if entry:
                next_panels[panel] = entry
            state.detached_panels = next_panels
            state._emit(
                "ui_update",
                key="detached_panels",
                value=state.detached_panels,
            )
            state._db_save_ui(
                "detached_panels",
                json.dumps(state.detached_panels),
            )

    elif cmd == "first_run_complete":
        sentinel = Path.home() / ".torque" / ".first_run_complete"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text(
            datetime.now(timezone.utc).isoformat(),
            encoding="utf-8",
        )
        result = {
            "type": "ok",
            "first_run_complete": True,
            "sentinel": str(sentinel),
        }

    elif cmd == "ui_set_engineer_panel_split":
        try:
            fraction = float(data.get("fraction", 0.30))
        except (TypeError, ValueError):
            fraction = 0.30
        fraction = max(0.12, min(0.75, fraction))
        state.engineer_panel_split_fraction = fraction
        state._emit(
            "ui_update",
            key="engineer_panel_split_fraction",
            value=state.engineer_panel_split_fraction,
        )
        state._db_save_ui(
            "engineer_panel_split_fraction",
            state.engineer_panel_split_fraction,
        )

    elif cmd == "ui_set_context_panel_split":
        try:
            ratio = float(data.get("ratio", 0.38))
        except (TypeError, ValueError):
            ratio = 0.38
        ratio = max(0.28, min(0.62, ratio))
        state.context_panel_split_ratio = ratio
        state._emit(
            "ui_update",
            key="context_panel_split_ratio",
            value=state.context_panel_split_ratio,
        )
        state._db_save_ui(
            "context_panel_split_ratio",
            state.context_panel_split_ratio,
        )

    elif cmd == "ui_set_supervisor_panel_state":
        raw = data.get("state", {})
        if not isinstance(raw, dict):
            result = {
                "type": "error",
                "message": "Invalid supervisor panel state",
            }
        else:
            sort_key = str(raw.get("sortKey", "") or "")
            if sort_key not in {
                "state", "owner", "session", "pid",
                "command", "bytes", "tty", "path",
            }:
                sort_key = "owner"
            sort_direction = str(
                raw.get("sortDirection", "") or ""
            )
            if sort_direction not in {"asc", "desc"}:
                sort_direction = "asc"
            try:
                scroll_pos = max(
                    0,
                    int(float(raw.get("scrollPos", 0) or 0)),
                )
            except (TypeError, ValueError):
                scroll_pos = 0
            state.supervisor_panel_state = {
                "autoRefresh": bool(raw.get("autoRefresh", True)),
                "sortKey": sort_key,
                "sortDirection": sort_direction,
                "selectedSessionId": str(
                    raw.get("selectedSessionId", "") or ""
                ),
                "expandedSessionId": str(
                    raw.get("expandedSessionId", "") or ""
                ),
                "scrollPos": scroll_pos,
            }
            state._emit(
                "ui_update",
                key="supervisor_panel_state",
                value=state.supervisor_panel_state,
            )
            state._db_save_ui(
                "supervisor_panel_state",
                json.dumps(state.supervisor_panel_state),
            )

    elif cmd == "events_dismiss":
        item_id = str(data.get("id", "") or "").strip()
        if not item_id:
            result = {"type": "error", "message": "Missing event id"}
        else:
            try:
                timestamp = float(data.get("timestamp", 0) or 0)
            except (TypeError, ValueError):
                timestamp = 0.0
            if timestamp <= 0:
                timestamp = time.time()
            state.events_dismissed_attention[item_id] = timestamp
            state._emit(
                "ui_update",
                key="events_dismissed_attention",
                value=state.events_dismissed_attention,
            )
            state._db_save_ui(
                "events_dismissed_attention",
                json.dumps(state.events_dismissed_attention),
            )

    elif cmd == "mission_control_dismiss":
        card_id = str(data.get("id", "") or "").strip()
        if not card_id:
            result = {"type": "error", "message": "Missing Mission Control card id"}
        else:
            try:
                timestamp = float(data.get("timestamp", 0) or 0)
            except (TypeError, ValueError):
                timestamp = 0.0
            if timestamp <= 0:
                timestamp = time.time()
            dismissed_cards = getattr(
                state, "mission_control_dismissed_cards", {},
            )
            if not isinstance(dismissed_cards, dict):
                dismissed_cards = {}
            dismissed_cards[card_id] = timestamp
            state.mission_control_dismissed_cards = dismissed_cards
            state._emit(
                "ui_update",
                key="mission_control_dismissed_cards",
                value=state.mission_control_dismissed_cards,
            )
            state._db_save_ui(
                "mission_control_dismissed_cards",
                json.dumps(state.mission_control_dismissed_cards),
            )

    elif cmd == "board_set_filters":
        raw_filters = data.get("filters_by_group", {})
        if isinstance(raw_filters, dict):
            state.board_filters_by_group = raw_filters
        else:
            state.board_filters_by_group = {}
        state._emit("ui_update", key="board_filters_by_group",
                    value=state.board_filters_by_group)
        state._db_save_ui(
            "board_filters_by_group",
            json.dumps(state.board_filters_by_group),
        )

    elif cmd == "board_set_selected_lanes":
        raw_lanes = data.get("selected_lanes_by_group", {})
        if isinstance(raw_lanes, dict):
            state.board_selected_lanes_by_group = {
                str(group or ""): str(lane or "")
                for group, lane in raw_lanes.items()
                if str(group or "") and str(lane or "")
            }
        else:
            state.board_selected_lanes_by_group = {}
        state._emit("ui_update", key="board_selected_lanes_by_group",
                    value=state.board_selected_lanes_by_group)
        state._db_save_ui(
            "board_selected_lanes_by_group",
            json.dumps(state.board_selected_lanes_by_group),
        )

    elif cmd == "board_set_hidden_wide_lanes":
        raw_lanes = data.get("hidden_wide_lanes_by_group", {})
        normalized = {}
        if isinstance(raw_lanes, dict):
            for group, lanes in raw_lanes.items():
                group = str(group or "")
                if not group or not isinstance(lanes, dict):
                    continue
                lane_state = {
                    str(lane or ""): True
                    for lane, hidden in lanes.items()
                    if str(lane or "") and bool(hidden)
                }
                normalized[group] = lane_state
        state.board_hidden_wide_lanes_by_group = normalized
        state._emit("ui_update",
                    key="board_hidden_wide_lanes_by_group",
                    value=state.board_hidden_wide_lanes_by_group)
        state._db_save_ui(
            "board_hidden_wide_lanes_by_group",
            json.dumps(state.board_hidden_wide_lanes_by_group),
        )

    elif cmd == "board_set_saved_views":
        raw_views = data.get("saved_views_by_group", {})
        if isinstance(raw_views, dict):
            state.board_saved_views_by_group = raw_views
        else:
            state.board_saved_views_by_group = {}
        state._emit("ui_update", key="board_saved_views_by_group",
                    value=state.board_saved_views_by_group)
        state._db_save_ui(
            "board_saved_views_by_group",
            json.dumps(state.board_saved_views_by_group),
        )

    elif cmd == "board_set_lane_sorts":
        raw_sorts = data.get("lane_sorts_by_group", {})
        if isinstance(raw_sorts, dict):
            state.board_lane_sorts_by_group = raw_sorts
        else:
            state.board_lane_sorts_by_group = {}
        state._emit("ui_update", key="board_lane_sorts_by_group",
                    value=state.board_lane_sorts_by_group)
        state._db_save_ui(
            "board_lane_sorts_by_group",
            json.dumps(state.board_lane_sorts_by_group),
        )

    elif cmd == "board_set_card_density":
        raw_density = data.get("card_density_by_group", {})
        if isinstance(raw_density, dict):
            state.board_card_density_by_group = raw_density
        else:
            state.board_card_density_by_group = {}
        state._emit("ui_update", key="board_card_density_by_group",
                    value=state.board_card_density_by_group)
        state._db_save_ui(
            "board_card_density_by_group",
            json.dumps(state.board_card_density_by_group),
        )

    return result


_UI_STATE_COMMAND_REGISTRY = AsyncHandlerRegistry()
_UI_STATE_COMMAND_REGISTRY.register_many(
    UI_STATE_COMMAND_NAMES,
    _handle_ui_state_command,
    label="ui_state",
)
