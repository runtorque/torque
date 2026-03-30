"""AgentCell dataclass and MatrixState persistence layer."""

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional

from aiohttp import web

from .config import DEFAULT_COMMAND, log
from .db import LoomDB

_RESERVED_LANES = {"Backlog", "To Do", "In Progress", "Done"}
_DEFAULT_LANES = ["Backlog", "To Do", "In Progress", "Done"]


@dataclass
class BoardTask:
    id: str
    task: str                   # actionable task description (required)
    slug: str = ""              # auto-generated from task text
    group: str = ""             # owning group (must be an existing group)
    action_name: str = ""       # action used for prompt rendering (optional)
    action_vars: dict = field(default_factory=dict)  # action variable values
    instructions: str = ""      # DEPRECATED — kept for backward compat
    context: str = ""           # DEPRECATED — kept for backward compat
    criteria: str = ""          # DEPRECATED — kept for backward compat
    lane: str = "Backlog"
    position: int = 0
    assignee: str = ""          # agent name prefix for pool assignment (optional)
    agent_id: str = ""          # concrete agent working on this (optional)
    labels: list[str] = field(default_factory=list)
    created_at: str = ""        # ISO 8601
    updated_at: str = ""        # ISO 8601
    # Provider fields (unused in v1, ready for sync)
    provider: str = ""          # "jira", "linear", "github"
    external_id: str = ""       # e.g. "PROJ-123"
    external_url: str = ""      # link back to provider
    # Pipeline fields (Phase 4b)
    parent_task_id: str = ""    # task this was derived from (empty for root tasks)
    pipeline_depth: int = 0     # 0 for root, auto-incremented from parent
    pipeline_root_id: str = ""  # ID of the chain's root task (self.id for roots)
    status: str = ""            # pipeline status (e.g. "On Review", "Fixing")


@dataclass
class AgentCell:
    id: str
    name: str
    group: str
    slug: str = ""              # auto-generated from name
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
    # Context preservation (dispatch history)
    tasks_dispatched: int = 0  # number of tasks sent to this agent (persisted)
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


def _slugify(name: str, max_len: int = 40) -> str:
    """Convert a name to a URL-friendly slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0]
    return slug or "unnamed"


def _unique_slug(base: str, existing: set) -> str:
    """Ensure slug is unique by appending a numeric suffix."""
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


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
    terminal_close_on_disconnect: bool = False  # remove terminal from Loom when tab closed
    # Board / Dispatch
    dispatch_lane: str = "In Progress"  # lane for dispatched tasks
    dispatch_auto_terminals: bool = False  # create child terminals on dispatch


@dataclass
class GlobalSettings:
    """App-wide settings for the Loom instance."""
    # General > Server
    default_command: str = ""        # empty = use config.DEFAULT_COMMAND (env var fallback)
    filter_by_window: bool = True    # global default for window filtering
    focus_new_tabs: bool = True      # switch focus to newly created tabs
    # General > Board
    default_lanes: list[str] = field(default_factory=lambda: list(_DEFAULT_LANES))
    # Keybindings — action name → {modifiers, keycode, character} overrides
    keybindings: dict[str, dict] = field(default_factory=dict)
    # Pipeline
    max_pipeline_depth: int = 10  # 0 = unlimited


class MatrixState:
    """In-memory state for all groups and agents, with JSON persistence."""

    def __init__(self, db: Optional[LoomDB] = None):
        self.db: Optional[LoomDB] = db
        self.agents: dict[str, AgentCell] = {}
        self.groups: dict[str, list[str]] = {}
        self.group_slugs: dict[str, str] = {}  # group_name → slug
        self.group_settings: dict[str, GroupSettings] = {}
        self.active_session_id: Optional[str] = None
        self.current_window_id: Optional[str] = None
        self._children: dict[str, list[str]] = {}  # agent_id → [child terminal ids]
        self._ws_clients: set[web.WebSocketResponse] = set()
        # Global settings
        self.global_settings: GlobalSettings = GlobalSettings()
        # Board (Phase 5)
        self.board_lanes: list[str] = list(_DEFAULT_LANES)
        self.board_tasks: dict[str, BoardTask] = {}
        self.panel_active: str = ""  # '' | 'board' | 'actions' | 'events'
        self.board_panel_height: int = 0  # 0 = use CSS default
        self.panel_log = None  # PanelEventLog, set from server.py
        # Delta broadcast accumulator
        self._delta_ops: list[dict] = []
        self._seq: int = 0

    # -- Delta emission -----------------------------------------------------

    def _emit(self, op: str, **kwargs):
        """Accumulate a delta operation for the next broadcast."""
        self._delta_ops.append({"op": op, **kwargs})

    def _emit_agent(self, cell: AgentCell):
        """Emit an agent_upsert delta with the full agent dict."""
        self._emit("agent_upsert", **asdict(cell))

    def _emit_group(self, name: str):
        """Emit a group_update delta with the current member list."""
        self._emit("group_update", name=name,
                   slug=self.group_slugs.get(name, ""),
                   agents=list(self.groups.get(name, [])))

    # -- Serialization ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "agents": {aid: asdict(a) for aid, a in self.agents.items()},
            "groups": self.groups,
            "group_slugs": dict(self.group_slugs),
            "group_settings": {
                n: asdict(gs) for n, gs in self.group_settings.items()
            },
            "global_settings": asdict(self.global_settings),
            "children": self._children,
            "active_session_id": self.active_session_id,
            "current_window_id": self.current_window_id,
            "board_lanes": self.board_lanes,
            "board_tasks": {
                tid: asdict(t) for tid, t in self.board_tasks.items()
            },
            "panel_active": self.panel_active,
            "board_panel_height": self.board_panel_height,
            "panel_events": self.panel_log.get_recent(100) if self.panel_log else [],
        }

    # -- Targeted persistence helpers ----------------------------------------

    def _db_save_agent(self, cell: AgentCell):
        """Persist a single agent to SQLite."""
        if self.db:
            try:
                self.db.save_agent(cell)
            except Exception:
                log.exception("Failed to save agent %s", cell.id)

    def _db_delete_agent(self, agent_id: str):
        if self.db:
            try:
                self.db.delete_agent(agent_id)
            except Exception:
                log.exception("Failed to delete agent %s", agent_id)

    def _db_save_groups(self):
        """Persist all groups and their memberships."""
        if self.db:
            try:
                self.db.save_groups(self.groups, self.group_slugs)
                for gname, members in self.groups.items():
                    self.db.save_group_members(gname, members)
            except Exception:
                log.exception("Failed to save groups")

    def _db_save_group_settings(self, name: str):
        if self.db and name in self.group_settings:
            try:
                self.db.save_group_settings(name, self.group_settings[name])
            except Exception:
                log.exception("Failed to save group settings for %s", name)

    def _db_delete_group(self, name: str):
        if self.db:
            try:
                self.db.delete_group(name)
            except Exception:
                log.exception("Failed to delete group %s", name)

    def _db_save_task(self, task: BoardTask):
        if self.db:
            try:
                self.db.save_board_task(task)
            except Exception:
                log.exception("Failed to save task %s", task.id)

    def _db_delete_task(self, task_id: str):
        if self.db:
            try:
                self.db.delete_board_task(task_id)
            except Exception:
                log.exception("Failed to delete task %s", task_id)

    def _db_save_lanes(self):
        if self.db:
            try:
                self.db.save_board_lanes(self.board_lanes)
            except Exception:
                log.exception("Failed to save board lanes")

    def _db_save_ui(self, key: str, value):
        if self.db:
            try:
                self.db.save_ui_state(key, value)
            except Exception:
                log.exception("Failed to save UI state %s", key)

    def _db_save_global_settings(self):
        if self.db:
            try:
                self.db.save_global_settings(self.global_settings)
            except Exception:
                log.exception("Failed to save global settings")

    def load(self):
        from .config import DB_FILE, STATE_FILE
        if not self.db:
            return

        # Migration: if DB is empty but state.json exists, import it
        if not self.db.has_data() and STATE_FILE.exists():
            self.db.migrate_from_json(STATE_FILE)

        data = self.db.load_all()
        if not data.get("agents") and not data.get("groups"):
            return  # empty DB, nothing to load

        try:
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
            # Global settings
            gs_raw = data.get("global_settings")
            if gs_raw:
                gls_fields = set(GlobalSettings.__dataclass_fields__)
                gls_filtered = {k: v for k, v in gs_raw.items()
                                if k in gls_fields}
                self.global_settings = GlobalSettings(**gls_filtered)
            # Board state — use global default_lanes as fallback
            default = (self.global_settings.default_lanes
                       or list(_DEFAULT_LANES))
            self.board_lanes = data.get("board_lanes") or default
            # Ensure reserved lanes exist
            for rl in _RESERVED_LANES:
                if rl not in self.board_lanes:
                    self.board_lanes.insert(0, rl)
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
                filtered = {k: v for k, v in raw.items() if k in bt_fields}
                self.board_tasks[tid] = BoardTask(**filtered)
            # panel_active: new key; backward compat from board_panel_open
            pa = data.get("panel_active", "")
            if not pa and data.get("board_panel_open"):
                pa = "board"
            self.panel_active = pa
            self.board_panel_height = data.get("board_panel_height", 0)
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
            if slug_dirty:
                self._db_save_groups()
                log.info("Generated slugs for existing resources")
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
        self._emit("group_settings_update", name=name, **asdict(gs))
        self._db_save_group_settings(name)

    # -- Global settings ----------------------------------------------------

    def get_default_command(self) -> str:
        """Return the effective default boot command.

        Priority: global_settings > env var > 'claude'
        """
        return self.global_settings.default_command or DEFAULT_COMMAND

    def update_global_settings(self, **fields):
        """Update global settings fields."""
        valid = set(GlobalSettings.__dataclass_fields__)
        for key, value in fields.items():
            if key in valid:
                setattr(self.global_settings, key, value)
        self._emit("global_settings_update",
                    **asdict(self.global_settings))
        self._db_save_global_settings()

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

    # -- Slug helpers -------------------------------------------------------

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

    # -- Mutations ----------------------------------------------------------

    def add_group(self, name: str):
        if name and name not in self.groups:
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
                    self._emit("agent_remove", id=aid)
                    # Cascade: remove child terminals
                    for child_id in self._children.pop(aid, []):
                        child = self.agents.pop(child_id, None)
                        if child:
                            removed.append(child)
                            self._emit("agent_remove", id=child_id)
            del self.groups[name]
            self.group_slugs.pop(name, None)
            self.group_settings.pop(name, None)
            self._emit("group_remove", name=name)
            self._emit("groups_reorder", groups=list(self.groups.keys()))
            for r in removed:
                self._db_delete_agent(r.id)
            self._db_delete_group(name)
            self._db_save_groups()
        return removed

    def rename_group(self, old: str, new: str):
        if old in self.groups and new and new not in self.groups:
            self.groups[new] = self.groups.pop(old)
            self.group_slugs.pop(old, None)
            self.group_slugs[new] = self._unique_group_slug(new)
            if old in self.group_settings:
                self.group_settings[new] = self.group_settings.pop(old)
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

    def update_agent(self, aid: str, **fields):
        """Update mutable fields on an existing cell."""
        cell = self.agents.get(aid)
        if not cell:
            return
        for key in ("name", "tab_color", "icon"):
            if key in fields:
                setattr(cell, key, fields[key])
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

    def remove_agent(self, aid: str) -> list[AgentCell]:
        removed: list[AgentCell] = []
        cell = self.agents.pop(aid, None)
        if not cell:
            return removed
        removed.append(cell)
        self._emit("agent_remove", id=aid)
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
                self._emit("agent_remove", id=child_id)
        # Unlink from board tasks
        for t in self.board_tasks.values():
            if t.agent_id == aid:
                t.agent_id = ""
                self._emit("task_upsert", **asdict(t))
                self._db_save_task(t)
        for r in removed:
            self._db_delete_agent(r.id)
        self._db_save_groups()
        return removed

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

    def board_add_task(self, task: str, group: str, lane: str = "",
                       **kwargs) -> Optional[BoardTask]:
        if not task:
            return None
        if not group or group not in self.groups:
            return None
        if not lane:
            lane = self.board_lanes[0] if self.board_lanes else "Backlog"
        if lane not in self.board_lanes:
            return None
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        tid = uuid.uuid4().hex[:8]
        task_slug = self._unique_task_slug(task)
        # Position = end of lane
        max_pos = max(
            (t.position for t in self.board_tasks.values() if t.lane == lane),
            default=-1,
        )
        bt = BoardTask(
            id=tid,
            task=task,
            slug=task_slug,
            group=group,
            lane=lane,
            position=max_pos + 1,
            created_at=now,
            updated_at=now,
            **{k: v for k, v in kwargs.items()
               if k in BoardTask.__dataclass_fields__ and k not in
               ("id", "task", "slug", "group", "lane", "position",
                "created_at", "updated_at")},
        )
        self.board_tasks[tid] = bt
        self._emit("task_upsert", **asdict(bt))
        self._db_save_task(bt)
        return bt

    def board_update_task(self, tid: str, **fields):
        task = self.board_tasks.get(tid)
        if not task:
            return
        valid = set(BoardTask.__dataclass_fields__) - {"id", "slug", "created_at"}
        old_lane = task.lane
        for key, value in fields.items():
            if key in valid:
                setattr(task, key, value)
        if "task" in fields:
            task.slug = self._unique_task_slug(task.task, exclude_id=tid)
        from datetime import datetime, timezone
        task.updated_at = datetime.now(timezone.utc).isoformat()
        # Reindex if lane changed
        if "lane" in fields and fields["lane"] != old_lane:
            self._board_reindex(old_lane)
            self._board_reindex(task.lane)
        self._emit("task_upsert", **asdict(task))
        self._db_save_task(task)

    def board_remove_task(self, tid: str):
        task = self.board_tasks.pop(tid, None)
        if task:
            self._board_reindex(task.lane)
            self._emit("task_remove", id=tid)
            self._db_delete_task(tid)

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
        self._emit("task_upsert", **asdict(task))
        self._db_save_task(task)

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
            self._emit("task_upsert", **asdict(t))
        for t in lane_tasks:
            self._db_save_task(t)

    def board_add_lane(self, name: str, position: Optional[int] = None):
        if not name or name in self.board_lanes:
            return
        if position is not None:
            self.board_lanes.insert(min(position, len(self.board_lanes)), name)
        else:
            self.board_lanes.append(name)
        self._emit("lanes_update", lanes=list(self.board_lanes))
        self._db_save_lanes()

    def board_rename_lane(self, old_name: str, new_name: str):
        if (old_name in _RESERVED_LANES or old_name not in self.board_lanes
                or not new_name or new_name in self.board_lanes):
            return
        idx = self.board_lanes.index(old_name)
        self.board_lanes[idx] = new_name
        for t in self.board_tasks.values():
            if t.lane == old_name:
                t.lane = new_name
                self._emit("task_upsert", **asdict(t))
        self._emit("lanes_update", lanes=list(self.board_lanes))
        self._db_save_lanes()
        for t in self.board_tasks.values():
            if t.lane == new_name:
                self._db_save_task(t)

    def board_remove_lane(self, name: str, move_tasks_to: str = ""):
        if (name in _RESERVED_LANES or name not in self.board_lanes
                or len(self.board_lanes) <= 1):
            return
        self.board_lanes.remove(name)
        target = move_tasks_to if move_tasks_to in self.board_lanes \
            else self.board_lanes[0]
        for t in self.board_tasks.values():
            if t.lane == name:
                t.lane = target
                self._emit("task_upsert", **asdict(t))
        self._board_reindex(target)
        self._emit("lanes_update", lanes=list(self.board_lanes))
        self._db_save_lanes()
        for t in self.board_tasks.values():
            if t.lane == target:
                self._db_save_task(t)

    def board_reorder_lanes(self, lanes: list[str]):
        if set(lanes) != set(self.board_lanes):
            return
        self.board_lanes = lanes
        self._emit("lanes_update", lanes=list(self.board_lanes))
        self._db_save_lanes()

    def board_get_children(self, task_id: str) -> list[BoardTask]:
        """Return direct children of a task (derived tasks)."""
        return [t for t in self.board_tasks.values()
                if t.parent_task_id == task_id]

    def board_get_chain(self, task_id: str) -> list[BoardTask]:
        """Return all tasks in the same pipeline chain, ordered by depth."""
        task = self.board_tasks.get(task_id)
        if not task:
            return []
        root_id = task.pipeline_root_id or task.id
        chain = [t for t in self.board_tasks.values()
                 if (t.pipeline_root_id == root_id) or (t.id == root_id)]
        chain.sort(key=lambda t: (t.pipeline_depth, t.created_at))
        return chain

    def board_unlink_agent(self, agent_id: str):
        """Unlink an agent from all tasks (called when agent is removed)."""
        changed = False
        for t in self.board_tasks.values():
            if t.agent_id == agent_id:
                t.agent_id = ""
                self._emit("task_upsert", **asdict(t))
                self._db_save_task(t)
                changed = True

    # -- WebSocket broadcast ------------------------------------------------

    def snapshot_msg(self) -> str:
        """Generate a full state snapshot message (for initial connect / resync)."""
        return json.dumps({
            "type": "state", "seq": self._seq, **self.to_dict()})

    async def broadcast(self):
        """Send accumulated deltas to all WS clients.

        If there are no delta ops (e.g. broadcast after a focus change
        where the _emit was called), this is a no-op — the delta was
        already queued by _emit().
        """
        if not self._delta_ops:
            return
        self._seq += 1
        msg = json.dumps({
            "type": "delta", "seq": self._seq,
            "ops": self._delta_ops,
        })
        self._delta_ops = []
        dead: set[web.WebSocketResponse] = set()
        for ws in self._ws_clients:
            try:
                await ws.send_str(msg)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead
