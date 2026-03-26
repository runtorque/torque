"""AgentCell dataclass and MatrixState persistence layer."""

import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional

from aiohttp import web

from .config import DEFAULT_COMMAND, STATE_FILE, log

_DEFAULT_LANES = ["Backlog", "To Do", "In Progress", "Done"]


@dataclass
class BoardTask:
    id: str
    title: str
    description: str = ""
    lane: str = "Backlog"
    position: int = 0
    agent_id: str = ""          # linked agent cell (optional)
    labels: list[str] = field(default_factory=list)
    created_at: str = ""        # ISO 8601
    updated_at: str = ""        # ISO 8601
    # Provider fields (unused in v1, ready for sync)
    provider: str = ""          # "jira", "linear", "github"
    external_id: str = ""       # e.g. "PROJ-123"
    external_url: str = ""      # link back to provider


@dataclass
class AgentCell:
    id: str
    name: str
    group: str
    cell_type: str = "agent"  # "agent" | "terminal"
    session_id: Optional[str] = None
    profile: str = "Default"
    command: str = ""
    directory: str = ""  # working dir on create/relaunch
    tab_color: str = ""  # hex color for iTerm2 tab (e.g. "#f85149")
    icon: str = ""  # custom icon character (from AGENT_ICONS set)
    window_id: str = ""  # iTerm2 window this session lives in
    parent_id: str = ""  # for child terminals: the owning agent's ID
    status: str = "stopped"  # idle | running | error | stopped
    current_process: str = ""  # foreground job name (tracked for terminals)
    current_path: str = ""  # working directory (tracked for terminals)
    current_branch: str = ""  # git branch (empty if not in a repo)
    git_root: str = ""  # git repo root (empty if not in a repo)
    worktree_path: str = ""  # git worktree path (if created via group setting)
    worktree_branch: str = ""  # git worktree branch name
    worktree_repo_root: str = ""  # original repo root (needed for cleanup)
    worktree_base_branch: str = ""  # branch the worktree forked from (e.g. main)
    # Agent awareness (Phase 1)
    agent_type: str = ""  # "claude-code", "codex", "gemini-cli", ""
    agent_session_id: str = ""  # agent's own session ID (e.g. Claude Code session)
    activity: str = ""  # "", "thinking", "tool_call", "writing", "waiting"
    activity_detail: str = ""  # e.g. "Editing server.py", "Running tests"
    last_event_at: float = 0.0  # timestamp of last event received
    session_tokens_in: int = 0  # cumulative input tokens this session
    session_tokens_out: int = 0  # cumulative output tokens this session
    error_message: str = ""  # last error, cleared on next successful event
    needs_attention: bool = False  # agent waiting for input or stuck
    last_summary: str = ""  # last assistant message on Stop (for checkpoint msgs)
    # Worktree status (Phase 2, ephemeral)
    worktree_dirty: bool = False  # has uncommitted changes
    worktree_diff: dict = field(default_factory=dict)  # {files, insertions, deletions}
    worktree_checkpoints: int = 0  # number of checkpoint commits


# Fields that are ephemeral (not meaningful across restarts)
_EPHEMERAL_FIELDS = ("current_process", "current_path",
                     "current_branch", "git_root",
                     "activity", "activity_detail", "last_event_at",
                     "session_tokens_in", "session_tokens_out",
                     "error_message", "needs_attention", "last_summary",
                     "worktree_dirty", "worktree_diff",
                     "worktree_checkpoints")


@dataclass
class GroupSettings:
    """Default settings applied when creating agents/terminals in a group."""
    # Group-level defaults
    default_directory: str = ""
    profile: str = ""
    shell: str = ""
    tab_color: str = ""
    env_vars: dict[str, str] = field(default_factory=dict)
    auto_terminals: int = 0
    max_agents: int = 0
    collapsed_default: bool = False
    filter_by_window: bool = False
    # Agent overrides
    agent_directory: str = ""
    agent_profile: str = ""
    agent_shell: str = ""
    agent_tab_color: str = ""
    agent_env_vars: dict[str, str] = field(default_factory=dict)
    agent_boot_command: str = ""  # override default boot command (e.g. "codex")
    git_worktree: bool = False
    worktree_base_dir: str = ".loom/worktrees"  # directory for worktrees (relative to repo)
    worktree_base_branch: str = ""  # branch to fork from (empty = current HEAD)
    worktree_auto_checkpoint: bool = False  # auto-checkpoint on agent stop
    worktree_merge_squash: bool = True  # squash commits when merging to main
    worktree_merge_instructions: str = ""  # additional instructions appended to merge prompt
    agent_session_resume: bool = True  # resume session on relaunch
    agent_idle_timeout: int = 5  # minutes before flagging agent as stuck (0=disable)
    agent_always_custom_dialog: bool = False
    # Agent notifications
    notifications: bool = False
    notify_on_finish: bool = True
    notify_on_error: bool = True
    notify_on_attention: bool = True
    # Terminal overrides
    terminal_name_prefix: str = ""
    terminal_boot_command: str = ""
    terminal_command_args: str = ""
    terminal_init_script: str = ""
    terminal_directory: str = ""
    terminal_profile: str = ""
    terminal_shell: str = ""
    terminal_tab_color: str = ""
    terminal_env_vars: dict[str, str] = field(default_factory=dict)
    terminal_always_custom_dialog: bool = False


class MatrixState:
    """In-memory state for all groups and agents, with JSON persistence."""

    def __init__(self):
        self.agents: dict[str, AgentCell] = {}
        self.groups: dict[str, list[str]] = {}
        self.group_settings: dict[str, GroupSettings] = {}
        self.active_session_id: Optional[str] = None
        self.current_window_id: Optional[str] = None
        self._children: dict[str, list[str]] = {}  # agent_id → [child terminal ids]
        self._ws_clients: set[web.WebSocketResponse] = set()
        # Board (Phase 5)
        self.board_lanes: list[str] = list(_DEFAULT_LANES)
        self.board_tasks: dict[str, BoardTask] = {}
        self.board_panel_open: bool = False
        self.board_panel_height: int = 0  # 0 = use CSS default

    # -- Serialization ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "agents": {aid: asdict(a) for aid, a in self.agents.items()},
            "groups": self.groups,
            "group_settings": {
                n: asdict(gs) for n, gs in self.group_settings.items()
            },
            "children": self._children,
            "active_session_id": self.active_session_id,
            "current_window_id": self.current_window_id,
            "board_lanes": self.board_lanes,
            "board_tasks": {
                tid: asdict(t) for tid, t in self.board_tasks.items()
            },
            "board_panel_open": self.board_panel_open,
            "board_panel_height": self.board_panel_height,
        }

    def save(self):
        from .config import STATE_FILE  # re-read in case init_paths was called
        payload = {
            "agents": {aid: asdict(a) for aid, a in self.agents.items()},
            "groups": self.groups,
            "group_settings": {
                n: asdict(gs) for n, gs in self.group_settings.items()
            },
            "board_lanes": self.board_lanes,
            "board_tasks": {
                tid: asdict(t) for tid, t in self.board_tasks.items()
            },
            "board_panel_open": self.board_panel_open,
            "board_panel_height": self.board_panel_height,
        }
        STATE_FILE.write_text(json.dumps(payload, indent=2))

    def load(self):
        from .config import STATE_FILE
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text())
            fields = set(AgentCell.__dataclass_fields__)
            for aid, raw in data.get("agents", {}).items():
                filtered = {k: v for k, v in raw.items() if k in fields}
                cell = AgentCell(**filtered)
                cell.status = "stopped"
                for f in _EPHEMERAL_FIELDS:
                    setattr(cell, f, type(getattr(cell, f))())
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
            # Board state
            self.board_lanes = data.get("board_lanes", list(_DEFAULT_LANES))
            bt_fields = set(BoardTask.__dataclass_fields__)
            for tid, raw in data.get("board_tasks", {}).items():
                filtered = {k: v for k, v in raw.items() if k in bt_fields}
                self.board_tasks[tid] = BoardTask(**filtered)
            self.board_panel_open = data.get("board_panel_open", False)
            self.board_panel_height = data.get("board_panel_height", 0)
        except (json.JSONDecodeError, TypeError, KeyError):
            log.exception("Failed to load state from %s", STATE_FILE)

    def _rebuild_children(self):
        """Rebuild the parent→children index from parent_id fields."""
        self._children = {}
        for aid, cell in self.agents.items():
            if cell.cell_type == "agent":
                self._children.setdefault(aid, [])
        for aid, cell in self.agents.items():
            if cell.parent_id and cell.parent_id in self._children:
                self._children[cell.parent_id].append(aid)

    # -- Group settings -----------------------------------------------------

    def get_group_settings(self, name: str) -> GroupSettings:
        """Return group settings, creating defaults if group has none."""
        return self.group_settings.get(name, GroupSettings())

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
                setattr(gs, key, value)
        self.save()

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

    # -- Mutations ----------------------------------------------------------

    def add_group(self, name: str):
        if name and name not in self.groups:
            self.groups[name] = []
            self.save()

    def remove_group(self, name: str) -> list[AgentCell]:
        removed: list[AgentCell] = []
        if name in self.groups:
            for aid in self.groups[name]:
                cell = self.agents.pop(aid, None)
                if cell:
                    removed.append(cell)
                    # Cascade: remove child terminals
                    for child_id in self._children.pop(aid, []):
                        child = self.agents.pop(child_id, None)
                        if child:
                            removed.append(child)
            del self.groups[name]
            self.group_settings.pop(name, None)
            self.save()
        return removed

    def rename_group(self, old: str, new: str):
        if old in self.groups and new and new not in self.groups:
            self.groups[new] = self.groups.pop(old)
            if old in self.group_settings:
                self.group_settings[new] = self.group_settings.pop(old)
            for aid in self.groups[new]:
                if aid in self.agents:
                    self.agents[aid].group = new
                    for child_id in self._children.get(aid, []):
                        if child_id in self.agents:
                            self.agents[child_id].group = new
            self.save()

    def _add_cell(
        self,
        name: str,
        group: str,
        cell_type: str,
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
        # Max agents cap
        if cell_type == "agent" and not parent_id:
            gs = self.get_group_settings(group)
            if gs.max_agents > 0:
                current = sum(1 for aid in self.groups.get(group, [])
                              if self.agents.get(aid)
                              and self.agents[aid].cell_type == "agent")
                if current >= gs.max_agents:
                    log.warning("Group '%s' at max_agents cap (%d)",
                                group, gs.max_agents)
                    return None
        aid = uuid.uuid4().hex[:8]
        cell = AgentCell(
            id=aid,
            name=name,
            group=group,
            cell_type=cell_type,
            profile=profile,
            command=command or (DEFAULT_COMMAND if cell_type == "agent" else ""),
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
        self.save()
        log.info("Cell created: id=%s type=%s parent=%s tab_color=%r "
                 "directory=%r", aid, cell_type, parent_id or "none",
                 cell.tab_color, cell.directory)
        return cell

    def update_agent(self, aid: str, **fields):
        """Update mutable fields on an existing cell."""
        cell = self.agents.get(aid)
        if not cell:
            return
        for key in ("name", "tab_color", "icon"):
            if key in fields:
                setattr(cell, key, fields[key])
        self.save()

    def add_agent(self, **kw) -> Optional[AgentCell]:
        return self._add_cell(cell_type="agent", **kw)

    def add_terminal(self, **kw) -> Optional[AgentCell]:
        kw.setdefault("command", "")
        return self._add_cell(cell_type="terminal", **kw)

    def remove_agent(self, aid: str) -> list[AgentCell]:
        removed: list[AgentCell] = []
        cell = self.agents.pop(aid, None)
        if not cell:
            return removed
        removed.append(cell)
        # Remove from group list (top-level items only)
        if not cell.parent_id and cell.group in self.groups:
            self.groups[cell.group] = [
                x for x in self.groups[cell.group] if x != aid
            ]
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
        # Unlink from board tasks
        for t in self.board_tasks.values():
            if t.agent_id == aid:
                t.agent_id = ""
        self.save()
        return removed

    def move_agent(self, aid: str, target_group: str, before: str = ""):
        cell = self.agents.get(aid)
        if not cell or target_group not in self.groups:
            return
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
        # Move children along
        for child_id in self._children.get(aid, []):
            child = self.agents.get(child_id)
            if child:
                child.group = target_group
        self.save()

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
        self.save()

    def reparent_terminal(self, aid: str, new_parent_id: str):
        """Attach a terminal to an agent (or detach if new_parent_id is empty)."""
        cell = self.agents.get(aid)
        if not cell or cell.cell_type != "terminal":
            return
        new_parent = self.agents.get(new_parent_id) if new_parent_id else None
        if new_parent_id and (not new_parent or new_parent.cell_type != "agent"):
            return
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
        self.save()

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
        self.save()

    def cells_with_awareness(self) -> list[AgentCell]:
        """Return cells that have agent awareness active (agent_type set)."""
        return [c for c in self.agents.values() if c.agent_type]

    # -- Board (Phase 5) ---------------------------------------------------

    def _board_reindex(self, lane: str):
        """Reindex positions for all tasks in a lane."""
        tasks = sorted(
            [t for t in self.board_tasks.values() if t.lane == lane],
            key=lambda t: t.position,
        )
        for i, t in enumerate(tasks):
            t.position = i

    def board_add_task(self, title: str, lane: str = "",
                       **kwargs) -> Optional[BoardTask]:
        if not title:
            return None
        if not lane:
            lane = self.board_lanes[0] if self.board_lanes else "Backlog"
        if lane not in self.board_lanes:
            return None
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        tid = uuid.uuid4().hex[:8]
        # Position = end of lane
        max_pos = max(
            (t.position for t in self.board_tasks.values() if t.lane == lane),
            default=-1,
        )
        task = BoardTask(
            id=tid,
            title=title,
            lane=lane,
            position=max_pos + 1,
            created_at=now,
            updated_at=now,
            **{k: v for k, v in kwargs.items()
               if k in BoardTask.__dataclass_fields__ and k not in
               ("id", "title", "lane", "position", "created_at", "updated_at")},
        )
        self.board_tasks[tid] = task
        self.save()
        return task

    def board_update_task(self, tid: str, **fields):
        task = self.board_tasks.get(tid)
        if not task:
            return
        valid = set(BoardTask.__dataclass_fields__) - {"id", "created_at"}
        old_lane = task.lane
        for key, value in fields.items():
            if key in valid:
                setattr(task, key, value)
        from datetime import datetime, timezone
        task.updated_at = datetime.now(timezone.utc).isoformat()
        # Reindex if lane changed
        if "lane" in fields and fields["lane"] != old_lane:
            self._board_reindex(old_lane)
            self._board_reindex(task.lane)
        self.save()

    def board_remove_task(self, tid: str):
        task = self.board_tasks.pop(tid, None)
        if task:
            self._board_reindex(task.lane)
            self.save()

    def board_move_task(self, tid: str, lane: str,
                        position: Optional[int] = None):
        task = self.board_tasks.get(tid)
        if not task or lane not in self.board_lanes:
            return
        old_lane = task.lane
        task.lane = lane
        if position is not None:
            task.position = position
        else:
            max_pos = max(
                (t.position for t in self.board_tasks.values()
                 if t.lane == lane and t.id != tid),
                default=-1,
            )
            task.position = max_pos + 1
        from datetime import datetime, timezone
        task.updated_at = datetime.now(timezone.utc).isoformat()
        self._board_reindex(old_lane)
        self._board_reindex(lane)
        self.save()

    def board_reorder_task(self, tid: str, position: int):
        task = self.board_tasks.get(tid)
        if not task:
            return
        lane_tasks = sorted(
            [t for t in self.board_tasks.values()
             if t.lane == task.lane and t.id != tid],
            key=lambda t: t.position,
        )
        lane_tasks.insert(min(position, len(lane_tasks)), task)
        for i, t in enumerate(lane_tasks):
            t.position = i
        self.save()

    def board_add_lane(self, name: str, position: Optional[int] = None):
        if not name or name in self.board_lanes:
            return
        if position is not None:
            self.board_lanes.insert(min(position, len(self.board_lanes)), name)
        else:
            self.board_lanes.append(name)
        self.save()

    def board_rename_lane(self, old_name: str, new_name: str):
        if (old_name not in self.board_lanes or not new_name
                or new_name in self.board_lanes):
            return
        idx = self.board_lanes.index(old_name)
        self.board_lanes[idx] = new_name
        for t in self.board_tasks.values():
            if t.lane == old_name:
                t.lane = new_name
        self.save()

    def board_remove_lane(self, name: str, move_tasks_to: str = ""):
        if name not in self.board_lanes or len(self.board_lanes) <= 1:
            return
        self.board_lanes.remove(name)
        target = move_tasks_to if move_tasks_to in self.board_lanes \
            else self.board_lanes[0]
        for t in self.board_tasks.values():
            if t.lane == name:
                t.lane = target
        self._board_reindex(target)
        self.save()

    def board_reorder_lanes(self, lanes: list[str]):
        if set(lanes) != set(self.board_lanes):
            return
        self.board_lanes = lanes
        self.save()

    def board_unlink_agent(self, agent_id: str):
        """Unlink an agent from all tasks (called when agent is removed)."""
        changed = False
        for t in self.board_tasks.values():
            if t.agent_id == agent_id:
                t.agent_id = ""
                changed = True
        if changed:
            self.save()

    # -- WebSocket broadcast ------------------------------------------------

    async def broadcast(self):
        snapshot = json.dumps({"type": "state", **self.to_dict()})
        dead: set[web.WebSocketResponse] = set()
        for ws in self._ws_clients:
            try:
                await ws.send_str(snapshot)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead
