"""AgentCell dataclass and MatrixState persistence layer."""

import json
import uuid
from dataclasses import asdict, dataclass
from typing import Optional

from aiohttp import web

from .config import DEFAULT_COMMAND, STATE_FILE, log


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
    status: str = "stopped"  # idle | running | error | stopped
    current_process: str = ""  # foreground job name (tracked for terminals)
    current_path: str = ""  # working directory (tracked for terminals)
    current_branch: str = ""  # git branch (empty if not in a repo)
    git_root: str = ""  # git repo root (empty if not in a repo)


# Fields that are ephemeral (not meaningful across restarts)
_EPHEMERAL_FIELDS = ("current_process", "current_path",
                     "current_branch", "git_root")


class MatrixState:
    """In-memory state for all groups and agents, with JSON persistence."""

    def __init__(self):
        self.agents: dict[str, AgentCell] = {}
        self.groups: dict[str, list[str]] = {}
        self.active_session_id: Optional[str] = None
        self._ws_clients: set[web.WebSocketResponse] = set()

    # -- Serialization ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "agents": {aid: asdict(a) for aid, a in self.agents.items()},
            "groups": self.groups,
            "active_session_id": self.active_session_id,
        }

    def save(self):
        from .config import STATE_FILE  # re-read in case init_paths was called
        payload = {
            "agents": {aid: asdict(a) for aid, a in self.agents.items()},
            "groups": self.groups,
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
        except (json.JSONDecodeError, TypeError, KeyError):
            log.exception("Failed to load state from %s", STATE_FILE)

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
            del self.groups[name]
            self.save()
        return removed

    def rename_group(self, old: str, new: str):
        if old in self.groups and new and new not in self.groups:
            self.groups[new] = self.groups.pop(old)
            for aid in self.groups[new]:
                if aid in self.agents:
                    self.agents[aid].group = new
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
    ) -> Optional[AgentCell]:
        if group not in self.groups:
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
        )
        self.agents[aid] = cell
        self.groups[group].append(aid)
        self.save()
        log.info("Cell created: id=%s type=%s tab_color=%r directory=%r",
                 aid, cell_type, cell.tab_color, cell.directory)
        return cell

    def add_agent(self, **kw) -> Optional[AgentCell]:
        return self._add_cell(cell_type="agent", **kw)

    def add_terminal(self, **kw) -> Optional[AgentCell]:
        return self._add_cell(cell_type="terminal", command="", **kw)

    def remove_agent(self, aid: str) -> Optional[AgentCell]:
        cell = self.agents.pop(aid, None)
        if cell and cell.group in self.groups:
            self.groups[cell.group] = [
                x for x in self.groups[cell.group] if x != aid
            ]
            self.save()
        return cell

    def move_agent(self, aid: str, target_group: str):
        cell = self.agents.get(aid)
        if not cell or target_group not in self.groups:
            return
        if cell.group in self.groups:
            self.groups[cell.group] = [
                x for x in self.groups[cell.group] if x != aid
            ]
        self.groups[target_group].append(aid)
        cell.group = target_group
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
