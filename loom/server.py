"""aiohttp server, WebSocket command handler, and iTerm2 entry point."""

import asyncio
import json
import os
import sys

import aiohttp
from aiohttp import web
import iterm2
import yaml

from .config import WS_PORT, DB_FILE, WEBVIEW_FILE, STANDALONE, BIND_HOST, log
from .db import LoomDB
from dataclasses import asdict
from .state import MatrixState
from .bridge import ITerm2Adapter
from .events import EventLog, EventBus, PanelEventLog, health_check
from .adapters import get_adapter
from .notifications import NotificationManager
from .worktree import WorktreeManager
from .actions import ActionManager
from . import keybindings

# Delay (seconds) before closing an agent after a successful merge.
# TODO: make this a user-facing setting in global/group settings.
CLOSE_AFTER_MERGE_DELAY = 5


async def _worktree_diff_updater(state: MatrixState,
                                 worktree_mgr: WorktreeManager):
    """Periodically update diff stats for cells with active worktrees."""
    while True:
        await asyncio.sleep(60)
        changed = False
        for cell in state.agents.values():
            if not cell.worktree_path:
                continue
            diff = await worktree_mgr.diff_summary(cell)
            dirty = await worktree_mgr.has_uncommitted_changes(cell)
            checkpoints = await worktree_mgr.count_commits(cell)
            if diff != cell.worktree_diff or dirty != cell.worktree_dirty \
                    or checkpoints != cell.worktree_checkpoints:
                cell.worktree_diff = diff
                cell.worktree_dirty = dirty
                cell.worktree_checkpoints = checkpoints
                state._emit_agent(cell)
                changed = True
        if changed:
            await state.broadcast()


class _BlockStr(str):
    """Tagged string subclass so the YAML representer emits block scalar."""

_ACTION_KEY_ORDER = [
    "name", "description", "agent", "group", "worktree",
    "prompt", "labels", "transitions", "terminals",
]

class _ActionDumper(yaml.SafeDumper):
    pass

_ActionDumper.add_representer(
    _BlockStr,
    lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style="|"),
)

def _action_to_yaml(name: str, data: dict) -> str:
    """Convert an action data dict to YAML text."""
    doc = {"name": name}
    if data.get("description"):
        doc["description"] = data["description"]

    agent = data.get("agent", {})
    agent_keys = ("name_prefix", "command", "directory", "profile",
                  "shell", "tab_color")
    agent_block = {k: agent[k] for k in agent_keys if agent.get(k)}
    if agent_block:
        doc["agent"] = agent_block

    if data.get("group"):
        doc["group"] = data["group"]
    if data.get("worktree"):
        doc["worktree"] = True

    prompt = data.get("prompt", "")
    if prompt:
        doc["prompt"] = _BlockStr(prompt.rstrip("\n") + "\n")

    labels = data.get("labels", [])
    if labels:
        doc["labels"] = labels

    transitions = data.get("transitions", [])
    if transitions:
        clean = []
        for tr in transitions:
            if isinstance(tr, dict):
                if tr.get("ask"):
                    entry = {"ask": True}
                    if tr.get("when"):
                        entry["when"] = tr["when"]
                    clean.append(entry)
                elif tr.get("action"):
                    entry = {"action": tr["action"]}
                    if tr.get("when"):
                        entry["when"] = tr["when"]
                    if tr.get("status"):
                        entry["status"] = tr["status"]
                    if tr.get("target"):
                        entry["target"] = tr["target"]
                    clean.append(entry)
        if clean:
            doc["transitions"] = clean

    terminals = data.get("terminals", [])
    if terminals:
        clean = []
        for t in terminals:
            entry = {"name": t.get("name", "shell")}
            if t.get("command"):
                entry["command"] = t["command"]
            clean.append(entry)
        doc["terminals"] = clean

    # Sort keys in the canonical action order
    ordered = {k: doc[k] for k in _ACTION_KEY_ORDER if k in doc}
    return yaml.dump(ordered, Dumper=_ActionDumper,
                     default_flow_style=False, sort_keys=False,
                     allow_unicode=True)


async def main(connection: iterm2.Connection):
    log.info("Loom starting (port=%d)", WS_PORT)
    db = LoomDB(DB_FILE)
    db.init()
    log.info("SQLite database opened at %s", DB_FILE)
    state = MatrixState(db=db)
    state.load()
    log.info("State loaded: %d agents, %d groups",
             len(state.agents), len(state.groups))

    event_log = EventLog()
    panel_log = PanelEventLog()
    state.panel_log = panel_log
    notifier = NotificationManager(state)
    notifier.start()
    event_bus = EventBus(state, event_log, notifier, panel_log=panel_log)
    event_bus.start()
    asyncio.create_task(health_check(state, event_log, event_bus, notifier))
    log.info("Event bus, health monitor, and notifications started")

    bridge = ITerm2Adapter(connection, state)
    worktree_mgr = WorktreeManager()
    action_mgr = ActionManager()

    # cell ID → (pre-merge base SHA, close_on_merge flag)
    _pending_merges: dict[str, tuple[str, bool]] = {}

    def _checkpoint_message(cell) -> str:
        """Build a checkpoint commit message from the agent's last summary."""
        summary = cell.last_summary.strip()
        n = cell.worktree_checkpoints + 1
        subject = f"loom: checkpoint {n} — {cell.name}"
        if summary:
            return f"{subject}\n\n{summary}"
        return subject

    async def _on_agent_session_end(cell):
        """Handle agent turn completion: merge verification + auto-checkpoint."""
        # Check pending merge result
        if cell.id in _pending_merges:
            pre_sha, close_on_merge = _pending_merges.pop(cell.id)
            merged = await worktree_mgr.is_merged(cell)
            if not merged and pre_sha:
                merged = await worktree_mgr.check_base_advanced(
                    cell, pre_sha)
            if merged:
                log.info("Merge verified for '%s': branch %s merged into %s",
                         cell.name, cell.worktree_branch,
                         cell.worktree_base_branch)
                await _broadcast_toast(
                    f'"{cell.name}" merged to {cell.worktree_base_branch}',
                    "success")
                if close_on_merge:
                    await asyncio.sleep(CLOSE_AFTER_MERGE_DELAY)
                    removed = state.remove_agent(cell.id)
                    for c in removed:
                        if c.session_id:
                            await bridge.close_session(c.session_id)
                        if c.agent_type and c.directory:
                            adapter = get_adapter(c.agent_type)
                            if hasattr(adapter, "uninstall_hooks"):
                                adapter.uninstall_hooks(
                                    os.path.expanduser(c.directory))
                        event_bus.cleanup_cell(c.id)
                        if c.worktree_path:
                            await worktree_mgr.remove(c)
            else:
                log.warning("Merge failed for '%s': branch %s not in %s",
                            cell.name, cell.worktree_branch,
                            cell.worktree_base_branch)
                cell.needs_attention = True
                cell.error_message = (
                    "Merge to main failed — merge manually")
                state._emit_agent(cell)
                state._db_save_agent(cell)
            return  # skip auto-checkpoint on merge turn

        # Auto-checkpoint
        if cell.worktree_path and cell.cell_type == "agent":
            gs = state.get_group_settings(cell.group)
            if gs.worktree_auto_checkpoint:
                msg = _checkpoint_message(cell)
                sha = await worktree_mgr.checkpoint(cell, message=msg)
                if sha:
                    state._db_save_agent(cell)

    async def _broadcast_toast(message, level="info"):
        """Send a toast notification to all WS clients."""
        msg = json.dumps({"type": "toast", "message": message,
                          "level": level})
        dead = set()
        for ws_client in state._ws_clients:
            try:
                await ws_client.send_str(msg)
            except Exception:
                dead.add(ws_client)
        state._ws_clients -= dead

    # Handle agent turn completion (hook-based session_end)
    event_bus.on_session_end = _on_agent_session_end
    # Also checkpoint when the terminal session is actually closed (tab closed)
    bridge.on_session_terminated = _on_agent_session_end

    async def _on_terminal_disconnected(cell):
        """Auto-remove a terminal when its tab is closed (close_on_disconnect)."""
        log.info("Auto-removing terminal '%s' (close_on_disconnect)", cell.name)
        removed = state.remove_agent(cell.id)
        for c in removed:
            event_bus.cleanup_cell(c.id)

    bridge.on_terminal_disconnected = _on_terminal_disconnected
    await bridge.start()
    await bridge.reconnect_orphans()
    asyncio.create_task(_worktree_diff_updater(state, worktree_mgr))

    # Register RPCs and install global key bindings
    _kb_overrides = state.global_settings.keybindings or None
    displaced_bindings = await keybindings.setup(
        connection, state, bridge, overrides=_kb_overrides)
    # Mutable container so nested closures can reassign on keybinding change
    _displaced = [displaced_bindings]

    async def _resolve_base_dir(group: str = "") -> str:
        """Resolve a base directory for action discovery."""
        if group:
            gs = state.get_group_settings(group)
            d = gs.agent_directory or gs.default_directory
            if d:
                return os.path.expanduser(d)
        try:
            app = await iterm2.async_get_app(connection)
            win = app.current_terminal_window
            if win and win.current_tab and win.current_tab.current_session:
                p = await win.current_tab.current_session.async_get_variable(
                    "path")
                if p:
                    return p
        except Exception:
            pass
        return ""

    def _resolve_agent_id(identifier: str) -> str | None:
        """Resolve an agent by slug, name (case-insensitive), ID, or prefix.

        Returns the agent ID or None if not found.  Only matches
        top-level agents (cell_type == 'agent'), not terminals.
        """
        if not identifier:
            return None
        # Exact ID
        if identifier in state.agents:
            c = state.agents[identifier]
            if c.cell_type == "agent":
                return c.id
        # Slug match
        ident_lower = identifier.lower()
        for c in state.agents.values():
            if c.cell_type != "agent":
                continue
            if c.slug and c.slug == identifier:
                return c.id
        # Name match (case-insensitive)
        for c in state.agents.values():
            if c.cell_type != "agent":
                continue
            if c.name.lower() == ident_lower:
                return c.id
        # ID prefix match
        for c in state.agents.values():
            if c.cell_type != "agent":
                continue
            if c.id.startswith(identifier):
                return c.id
        return None

    # -- Postscript builder -------------------------------------------------

    def _build_postscript(task, amgr, base_dir="", is_clean=True):
        """Build the loom-ai instruction block appended to dispatch prompts.

        Only shows commands relevant to the action's transitions.
        ``done``, ``blocked``, and ``error`` are always included.
        ``derive`` only appears when the action declares transitions.
        ``ask`` only appears when an ``ask`` transition exists.

        When ``is_clean`` is False (agent already has context from prior
        tasks), emits an abbreviated version with derive/ask commands
        if the action has transitions.
        """
        # Resolve transitions for this action
        transitions = []
        if task.action_name:
            transitions = amgr.get_transitions(task.action_name,
                                               base_dir)

        def _target_flag(tr):
            """Resolve target field to CLI flags."""
            target = tr.get("target", "")
            if target == "self":
                return " --self"
            if target == "parent" and task.parent_task_id:
                pt = state.board_tasks.get(task.parent_task_id)
                if pt and pt.agent_id:
                    a = state.agents.get(pt.agent_id)
                    if a:
                        slug = a.slug or a.name
                        return f" --agent {slug}"
            if target == "root":
                rid = task.pipeline_root_id or task.id
                rt = state.board_tasks.get(rid)
                if rt and rt.agent_id:
                    a = state.agents.get(rt.agent_id)
                    if a:
                        slug = a.slug or a.name
                        return f" --agent {slug}"
            return ""

        def _derive_line(tr):
            when = tr.get("when", "")
            desc = f" — {when}" if when else ""
            flag = _target_flag(tr)
            return (f"- `loom ai derive \"description\" "
                    f"-t {tr['action']}{flag}`{desc}")

        if not is_clean:
            abbrev = ("\n\n---\nUse `loom ai done` when finished, "
                      "or `loom ai blocked \"reason\"` if stuck.")
            derive_lines = []
            has_ask = False
            for tr in transitions:
                if isinstance(tr, dict):
                    if tr.get("action"):
                        derive_lines.append(_derive_line(tr))
                    if tr.get("ask"):
                        has_ask = True
            if derive_lines or has_ask:
                abbrev += "\nAvailable transitions:"
                abbrev += "\n" + "\n".join(derive_lines)
                if has_ask:
                    abbrev += ("\n- `loom ai ask \"question\"` "
                              "— pause for human input")
            return abbrev

        lines = [
            "\n\nReport your progress with these commands:",
            "- `loom ai done` — task complete, no follow-up needed",
        ]

        # Dynamic derive/ask lines from action transitions
        has_ask = False
        for tr in transitions:
            if isinstance(tr, dict):
                if tr.get("action"):
                    lines.append(_derive_line(tr))
                if tr.get("ask"):
                    has_ask = True
        if has_ask:
            lines.append(
                "- `loom ai ask \"question\"` "
                "— pause for human input (creates a task in "
                "Backlog for review)")
        lines.extend([
            "- `loom ai blocked \"reason\"` — need user input",
            "- `loom ai error \"message\"` — unrecoverable error",
        ])

        # Pipeline context for derived tasks
        if task.parent_task_id:
            max_d = state.global_settings.max_pipeline_depth or "∞"
            parent = state.board_tasks.get(task.parent_task_id)
            root = state.board_tasks.get(task.pipeline_root_id)
            ctx = (f"\n\nThis task is part of a pipeline "
                   f"(depth {task.pipeline_depth}/{max_d}).")
            if parent:
                p_agent = ""
                if parent.agent_id:
                    a = state.agents.get(parent.agent_id)
                    if a:
                        p_agent = f", agent: {a.slug or a.name}"
                p_status = ""
                if parent.status:
                    p_status = f", status: {parent.status}"
                ctx += (f"\nParent task: \"{parent.task[:80]}\" "
                        f"({parent.lane}{p_status}{p_agent})")
            if root and root.id != (parent.id if parent else ""):
                ctx += f"\nRoot task: \"{root.task[:80]}\""
            lines.append(ctx)

        return "\n".join(lines)

    # -- Loom context builder -----------------------------------------------

    def _build_loom_context(cell, task):
        """Build the ``loom`` namespace dict for Jinja2 template rendering.

        Provides agent identity, dispatch history, worktree state, task
        metadata, and child terminal info — all derived from existing
        state at render time.
        """
        # Agent identity
        agent_ctx = {
            "name": cell.name,
            "slug": cell.slug,
            "type": cell.agent_type,
            "group": cell.group,
            "directory": cell.directory,
        }

        # Dispatch history
        linked = sorted(
            (t for t in state.board_tasks.values()
             if t.agent_id == cell.id and t.id != task.id),
            key=lambda t: t.created_at,
        )
        context_ctx = {
            "is_clean": cell.tasks_dispatched == 0,
            "tasks_dispatched": cell.tasks_dispatched,
            "previous_tasks": [
                {"task": t.task, "lane": t.lane, "action": t.action_name}
                for t in linked
            ],
        }

        # Worktree state
        worktree_ctx = {
            "active": bool(cell.worktree_path),
            "path": cell.worktree_path,
            "branch": cell.worktree_branch,
            "base_branch": cell.worktree_base_branch,
            "dirty": cell.worktree_dirty,
            "diff": cell.worktree_diff or {},
            "checkpoints": cell.worktree_checkpoints,
        }

        # Current task metadata
        parent_agent_slug = ""
        if task.parent_task_id:
            pt = state.board_tasks.get(task.parent_task_id)
            if pt and pt.agent_id:
                pa = state.agents.get(pt.agent_id)
                if pa:
                    parent_agent_slug = pa.slug or pa.name
        task_ctx = {
            "id": task.id,
            "slug": task.slug,
            "depth": task.pipeline_depth,
            "is_derived": bool(task.parent_task_id),
            "parent_task_id": task.parent_task_id,
            "parent_agent_slug": parent_agent_slug,
            "labels": list(task.labels),
            "group": task.group,
            "status": task.status,
        }

        # Child terminals of the target agent
        terminals_ctx = []
        for cid in state._children.get(cell.id, []):
            ch = state.agents.get(cid)
            if ch:
                terminals_ctx.append({
                    "name": ch.name,
                    "slug": ch.slug,
                    "current_path": ch.current_path,
                    "current_process": ch.current_process,
                    "current_branch": ch.current_branch,
                })

        return {
            "agent": agent_ctx,
            "context": context_ctx,
            "worktree": worktree_ctx,
            "task": task_ctx,
            "terminals": terminals_ctx,
        }

    # -- Command handler ----------------------------------------------------

    async def handle_command(data: dict) -> dict | None:
        """Handle a command, return a direct-response dict or None.

        Direct-response commands (get_config, get_group_settings,
        worktree_history) return immediately without broadcasting.
        Mutation commands broadcast state to all WS clients and
        optionally return a result dict.
        """
        cmd = data.get("cmd")
        log.info("CMD %s %s", cmd,
                 {k: v for k, v in data.items() if k != "cmd"})

        # get_config: respond directly, no state mutation
        if cmd == "get_config":
            try:
                all_profiles = await iterm2.PartialProfile.async_query(
                    connection)
                profile_names = sorted(
                    set(p.name for p in all_profiles if p.name))
            except Exception:
                log.exception("Failed to query profiles")
                profile_names = ["Default"]

            current_path = ""
            current_profile = "Default"
            try:
                app = await iterm2.async_get_app(connection)
                win = app.current_terminal_window
                if win and win.current_tab \
                        and win.current_tab.current_session:
                    sess = win.current_tab.current_session
                    current_path = (
                        await sess.async_get_variable("path") or "")
                    current_profile = (
                        await sess.async_get_variable("profileName")
                        or "Default")
            except Exception:
                log.exception("Failed to get current session info")

            group = data.get("group", "")
            group_cells = []
            for aid in state.groups.get(group, []):
                c = state.agents.get(aid)
                if c and c.session_id and c.current_path:
                    group_cells.append({
                        "id": c.id, "name": c.name,
                        "current_path": c.current_path,
                    })

            gs = state.get_group_settings(group)
            return {
                "type": "config",
                "profiles": profile_names,
                "current_path": current_path,
                "current_profile": current_profile,
                "group_cells": group_cells,
                "group_settings": asdict(gs),
            }

        # get_group_settings: respond directly, no state mutation
        if cmd == "get_group_settings":
            group = data.get("group", "")
            gs = state.get_group_settings(group)
            try:
                all_profiles = await iterm2.PartialProfile.async_query(
                    connection)
                pnames = sorted(
                    set(p.name for p in all_profiles if p.name))
            except Exception:
                log.exception("Failed to query profiles")
                pnames = ["Default"]
            return {
                "type": "group_settings",
                "group": group,
                "settings": asdict(gs),
                "profiles": pnames,
            }

        # get_global_settings: respond directly
        if cmd == "get_global_settings":
            return {
                "type": "global_settings",
                "settings": asdict(state.global_settings),
                "keybinding_defaults": keybindings.get_default_bindings(),
            }

        # list_actions: respond directly
        if cmd == "list_actions":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            actions = action_mgr.list_actions(base_dir)
            return {"type": "actions", "group": data.get("group", ""),
                    "actions": actions}

        # get_action: respond directly
        if cmd == "get_action":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            scope = data.get("scope", "")
            # Scope-aware loading: search only the target directory
            raw = None
            if scope == "user":
                gdir = os.path.expanduser("~/.loom/actions")
                for suffix in ("", ".yaml", ".yml"):
                    p = os.path.join(gdir, data["name"] + suffix)
                    if os.path.isfile(p):
                        with open(p) as f:
                            raw = f.read()
                        break
            if raw is None:
                raw = action_mgr._load_raw(data["name"], base_dir)
            if not raw:
                return {"type": "error",
                        "message": f"Action \"{data['name']}\" not found"}
            # Editor mode: parse raw YAML without Jinja2 rendering
            from .actions import parse_yaml
            try:
                act = parse_yaml(raw) or {}
            except Exception:
                act = {}
            avars = action_mgr.get_action_vars(raw)
            return {"type": "action_detail", "name": data["name"],
                    "action": act, "vars": avars}

        # render_action: render action prompt without creating an agent
        if cmd == "render_action":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            raw = action_mgr._load_raw(data["name"], base_dir)
            if not raw:
                return {"type": "error",
                        "message": f"Action \"{data['name']}\" not found"}
            variables = data.get("vars", {})
            rendered = action_mgr.render_action(raw, variables)
            return {"type": "action_rendered",
                    "name": data["name"],
                    "prompt": rendered.get("prompt", ""),
                    "group": rendered.get("group", ""),
                    "labels": rendered.get("labels", [])}

        # save_action: write action YAML to disk
        if cmd == "save_action":
            name = data.get("name", "").strip()
            if not name:
                return {"type": "error", "message": "Action name required"}
            act_data = data.get("action", {})
            # Validate {{ TASK }} in prompt
            prompt = act_data.get("prompt", "")
            if not action_mgr.validate_prompt(prompt):
                return {"type": "error",
                        "message": "Action prompt must contain {{ TASK }}"}
            # Reject 'loom' as a variable name (reserved namespace)
            avars = action_mgr.get_action_vars(prompt)
            for av in avars:
                if av.get("name") == "loom":
                    return {"type": "error",
                            "message": "'loom' is a reserved variable "
                                       "name"}
            scope = data.get("scope", "project")  # "project" or "user"
            base_dir = await _resolve_base_dir(data.get("group", ""))

            if scope == "user":
                tdir = os.path.expanduser("~/.loom/actions")
                os.makedirs(tdir, exist_ok=True)
            else:
                tdir = action_mgr.find_actions_dir(base_dir)
                if not tdir:
                    d = base_dir or os.getcwd()
                    tdir = os.path.join(d, ".loom", "actions")
                    os.makedirs(tdir, exist_ok=True)
            # Rename or scope change: delete old file from any location
            old_name = data.get("old_name", "")
            if old_name:
                for old_dir in action_mgr.find_actions_dirs(base_dir):
                    for suffix in (".yaml", ".yml"):
                        old_path = os.path.join(old_dir, old_name + suffix)
                        if os.path.isfile(old_path):
                            os.remove(old_path)
                            break
            path = os.path.join(tdir, name + ".yaml")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            yaml_text = _action_to_yaml(name, act_data)
            with open(path, "w") as f:
                f.write(yaml_text)
            # Return updated list
            actions = action_mgr.list_actions(base_dir)
            return {"type": "actions",
                    "group": data.get("group", ""),
                    "actions": actions,
                    "saved": name}

        # delete_action: remove action file from disk
        if cmd == "delete_action":
            name = data.get("name", "").strip()
            if not name:
                return {"type": "error", "message": "Action name required"}
            base_dir = await _resolve_base_dir(data.get("group", ""))
            deleted = False
            for tdir in action_mgr.find_actions_dirs(base_dir):
                for suffix in (".yaml", ".yml"):
                    path = os.path.join(tdir, name + suffix)
                    if os.path.isfile(path):
                        os.remove(path)
                        deleted = True
                        break
                if deleted:
                    break
            if not deleted:
                return {"type": "error",
                        "message": f"Action \"{name}\" not found"}
            actions = action_mgr.list_actions(base_dir)
            return {"type": "actions",
                    "group": data.get("group", ""),
                    "actions": actions,
                    "deleted": name}

        # -- Mutation commands: broadcast state at the end --
        result = None
        try:
            if cmd == "refresh":
                pass

            elif cmd == "resync":
                # Client detected a sequence gap — send full snapshot
                return {"type": "state", "seq": state._seq,
                        **state.to_dict()}

            elif cmd == "add_group":
                state.add_group(data["group"])

            elif cmd == "update_group_settings":
                settings = data.get("settings", {})
                state.update_group_settings(data["group"], **settings)

            elif cmd == "update_global_settings":
                settings = data.get("settings", {})
                old_kb = state.global_settings.keybindings.copy()
                state.update_global_settings(**settings)
                new_kb = state.global_settings.keybindings
                if new_kb != old_kb:
                    _displaced[0] = await keybindings.reinstall(
                        connection, _displaced[0],
                        overrides=new_kb or None)

            elif cmd == "remove_group":
                removed = state.remove_group(data["group"])
                for c in removed:
                    if c.session_id:
                        await bridge.close_session(c.session_id)
                    if c.agent_type and c.directory:
                        adapter = get_adapter(c.agent_type)
                        if hasattr(adapter, "uninstall_hooks"):
                            adapter.uninstall_hooks(
                                os.path.expanduser(c.directory))
                    event_bus.cleanup_cell(c.id)
                    if c.worktree_path:
                        await worktree_mgr.remove(c)

            elif cmd == "rename_group":
                state.rename_group(data["group"], data["new_name"])

            elif cmd == "add_agent":
                group = data["group"]
                gs = state.get_group_settings(group)
                profile = data.get("profile") or gs.agent_profile or gs.profile or "Default"
                directory = data.get("directory") or gs.agent_directory or gs.default_directory or ""
                _ac = gs.agent_tab_color
                tab_color = data.get("tab_color") or (_ac if _ac != "none" else "") or gs.tab_color or ""
                shell = data.get("shell") or gs.agent_shell or gs.shell or ""
                env = {**gs.env_vars, **gs.agent_env_vars, **(data.get("env_vars") or {})} or None

                command = data.get("command", "") or gs.agent_boot_command
                icon = data.get("icon", "")
                cell = state.add_agent(
                    name=data["name"], group=group,
                    profile=profile,
                    command=command,
                    directory=directory, tab_color=tab_color,
                    icon=icon,
                )
                if cell:
                    # Git worktree — create before session so cwd is correct
                    if gs.git_worktree and cell.directory:
                        repo_root = await worktree_mgr.get_repo_root(
                            cell.directory)
                        if repo_root:
                            wt_path = await worktree_mgr.create(
                                cell, repo_root,
                                base_dir=gs.worktree_base_dir
                                    or ".loom/worktrees",
                                base_branch=gs.worktree_base_branch or "")
                            if wt_path:
                                cell.directory = wt_path
                                state._emit_agent(cell)
                                state._db_save_agent(cell)

                    await bridge.create_session(
                        cell, env_vars=env, shell=shell)

                    # Auto-create child terminals
                    t_profile = gs.terminal_profile or gs.profile or "Default"
                    t_dir = gs.terminal_directory or gs.default_directory or ""
                    _ttc = gs.terminal_tab_color
                    t_color = (_ttc if _ttc != "none" else "") or gs.tab_color or ""
                    t_shell = gs.terminal_shell or gs.shell or ""
                    t_env = {**gs.env_vars, **gs.terminal_env_vars} or None
                    t_cmd = gs.terminal_boot_command or ""
                    if gs.terminal_command_args and t_cmd:
                        t_cmd = (t_cmd + " " + gs.terminal_command_args).strip()
                    for i in range(gs.auto_terminals):
                        t_name = state.next_cell_name(group, "terminal")
                        t = state.add_terminal(
                            name=t_name, group=group,
                            profile=t_profile,
                            command=t_cmd,
                            directory=t_dir or cell.directory,
                            tab_color=t_color,
                            parent_id=cell.id,
                        )
                        if t:
                            await bridge.create_session(
                                t, env_vars=t_env,
                                init_script=gs.terminal_init_script,
                                shell=t_shell)

            elif cmd == "add_agent_from_action":
                group = data["group"]
                act_name = data["action"]
                variables = data.get("vars", {})
                base_dir = await _resolve_base_dir(group)
                raw = action_mgr._load_raw(act_name, base_dir)
                if not raw:
                    result = {"type": "error",
                              "message": f"Action \"{act_name}\" not found"}
                else:
                    rendered = action_mgr.render_action(
                        raw, variables)

                    # Action can override the target group
                    act_group = rendered.get("group", "")
                    if act_group and act_group in state.groups:
                        group = act_group

                    gs = state.get_group_settings(group)

                    # Use rendered values, falling through to group settings
                    name = data.get("name") or rendered["name"]
                    profile = rendered.get("profile") or gs.agent_profile \
                        or gs.profile or "Default"
                    directory = rendered.get("directory") \
                        or gs.agent_directory or gs.default_directory or ""
                    command = rendered.get("command") or gs.agent_boot_command
                    _ac = gs.agent_tab_color
                    tab_color = rendered.get("tab_color") \
                        or (_ac if _ac != "none" else "") or gs.tab_color or ""
                    shell = rendered.get("shell") or gs.agent_shell \
                        or gs.shell or ""
                    env = {**gs.env_vars, **gs.agent_env_vars,
                           **(rendered.get("env_vars") or {})} or None

                    # Worktree: action can override group setting
                    want_worktree = rendered.get("worktree")
                    if want_worktree is None:
                        want_worktree = gs.git_worktree

                    cell = state.add_agent(
                        name=name, group=group, profile=profile,
                        command=command, directory=directory,
                        tab_color=tab_color)
                    if cell:
                        if want_worktree and cell.directory:
                            repo_root = await worktree_mgr.get_repo_root(
                                cell.directory)
                            if repo_root:
                                wt_path = await worktree_mgr.create(
                                    cell, repo_root,
                                    base_dir=gs.worktree_base_dir
                                        or ".loom/worktrees",
                                    base_branch=gs.worktree_base_branch
                                        or "")
                                if wt_path:
                                    cell.directory = wt_path
                                    state._emit_agent(cell)
                                    state._db_save_agent(cell)

                        await bridge.create_session(
                            cell, env_vars=env, shell=shell)

                        # Create action-defined child terminals
                        for tterm in (rendered.get("terminals") or []):
                            t_name = tterm.get("name") \
                                or state.next_cell_name(group, "terminal")
                            t = state.add_terminal(
                                name=t_name, group=group,
                                profile=gs.terminal_profile
                                    or gs.profile or "Default",
                                command=tterm.get("command") or "",
                                directory=tterm.get("directory")
                                    or cell.directory,
                                tab_color=gs.terminal_tab_color
                                    or gs.tab_color or "",
                                parent_id=cell.id)
                            if t:
                                t_shell = gs.terminal_shell \
                                    or gs.shell or ""
                                t_env = {**gs.env_vars,
                                         **gs.terminal_env_vars} or None
                                await bridge.create_session(
                                    t, env_vars=t_env,
                                    init_script=tterm.get("init_script")
                                        or gs.terminal_init_script,
                                    shell=t_shell)

                        # Use the rendered prompt directly
                        prompt = rendered.get("prompt", "")

                        if prompt and cell.session_id:
                            async def _send_after_boot(c, p):
                                await asyncio.sleep(2)
                                if c.session_id:
                                    await bridge.send_text(
                                        c.session_id,
                                        p if p.endswith("\r") else p + "\r")
                                    c.status = "running"
                                    state._emit_agent(c)
                                    state._db_save_agent(c)
                                    await state.broadcast()
                            asyncio.create_task(
                                _send_after_boot(cell, prompt))

            elif cmd == "add_terminal":
                group = data.get("group", "")
                parent_id = data.get("parent_id", "")
                # Resolve group from parent if needed
                resolve_group = group
                if parent_id:
                    p = state.agents.get(parent_id)
                    if p:
                        resolve_group = p.group
                gs = state.get_group_settings(resolve_group or group)
                profile = data.get("profile") or gs.terminal_profile or gs.profile or "Default"
                parent_wt = p.worktree_path if parent_id and p and p.worktree_path else ""
                directory = data.get("directory") or parent_wt or gs.terminal_directory or gs.default_directory or ""
                _tc = gs.terminal_tab_color
                tab_color = data.get("tab_color") or (_tc if _tc != "none" else "") or gs.tab_color or ""
                shell = data.get("shell") or gs.terminal_shell or gs.shell or ""
                env = {**gs.env_vars, **gs.terminal_env_vars, **(data.get("env_vars") or {})} or None
                command = data.get("command") or gs.terminal_boot_command or ""
                cmd_args = data.get("command_args") or gs.terminal_command_args or ""
                if cmd_args and command:
                    command = (command + " " + cmd_args).strip()
                init_script = data.get("init_script") or gs.terminal_init_script or ""

                cell = state.add_terminal(
                    name=data["name"], group=group,
                    profile=profile, command=command,
                    directory=directory, tab_color=tab_color,
                    parent_id=parent_id,
                )
                if cell:
                    await bridge.create_session(
                        cell, env_vars=env,
                        init_script=init_script,
                        shell=shell)

            elif cmd == "remove_agent":
                removed = state.remove_agent(data["id"])
                for c in removed:
                    if c.session_id:
                        await bridge.close_session(c.session_id)
                    # Clean up hooks
                    if c.agent_type and c.directory:
                        adapter = get_adapter(c.agent_type)
                        if hasattr(adapter, "uninstall_hooks"):
                            adapter.uninstall_hooks(
                                os.path.expanduser(c.directory))
                    # Clean up event bus state
                    event_bus.cleanup_cell(c.id)
                    if c.worktree_path:
                        await worktree_mgr.remove(c)

            elif cmd == "update_agent":
                cell = state.agents.get(data["id"])
                if cell:
                    old_name = cell.name
                    new_name = data.get("name", cell.name)
                    new_color = data.get("tab_color", cell.tab_color)
                    new_icon = data.get("icon", cell.icon)
                    state.update_agent(data["id"], name=new_name,
                                       tab_color=new_color,
                                       icon=new_icon)
                    if cell.session_id:
                        await bridge.update_session(cell, old_name)

            elif cmd == "focus_agent":
                cell = state.agents.get(data["id"])
                if cell and cell.session_id:
                    await bridge.focus_session(cell.session_id)

            elif cmd == "send_text":
                cell = state.agents.get(data["id"])
                if cell and cell.session_id:
                    await bridge.send_text(cell.session_id, data["text"])
                    cell.status = "running"
                    state._emit_agent(cell)
                    # Ephemeral status — no DB write needed

            elif cmd == "broadcast_to_group":
                for aid in state.groups.get(data["group"], []):
                    cell = state.agents.get(aid)
                    if cell and cell.session_id:
                        await bridge.send_text(
                            cell.session_id, data["text"])
                        cell.status = "running"
                        state._emit_agent(cell)
                    # Also send to child terminals
                    for child_id in state._children.get(aid, []):
                        child = state.agents.get(child_id)
                        if child and child.session_id:
                            await bridge.send_text(
                                child.session_id, data["text"])
                            child.status = "running"
                            state._emit_agent(child)
                # Ephemeral status — no DB write needed

            elif cmd == "relaunch_agent":
                cell = state.agents.get(data["id"])
                if cell and cell.status == "stopped":
                    gs = state.get_group_settings(cell.group)
                    if cell.cell_type == "terminal":
                        env = {**gs.env_vars, **gs.terminal_env_vars} or None
                        shell = gs.terminal_shell or gs.shell or ""
                        init = gs.terminal_init_script
                    else:
                        env = {**gs.env_vars, **gs.agent_env_vars} or None
                        shell = gs.agent_shell or gs.shell or ""
                        init = ""
                        # Worktree handling on relaunch
                        if cell.worktree_path:
                            if await worktree_mgr.validate(cell):
                                # Reuse existing worktree
                                cell.directory = cell.worktree_path
                                log.info("Reusing worktree for '%s': %s",
                                         cell.name, cell.worktree_path)
                            else:
                                # Worktree gone — clear and recreate if enabled
                                log.warning("Worktree invalid for '%s', "
                                            "clearing", cell.name)
                                cell.worktree_path = ""
                                cell.worktree_branch = ""
                                cell.worktree_repo_root = ""
                                cell.worktree_base_branch = ""
                                state._emit_agent(cell)
                                state._db_save_agent(cell)
                        # Create new worktree if enabled and none exists
                        if not cell.worktree_path and gs.git_worktree \
                                and cell.directory:
                            repo_root = await worktree_mgr.get_repo_root(
                                cell.directory)
                            if repo_root:
                                wt_path = await worktree_mgr.create(
                                    cell, repo_root,
                                    base_dir=gs.worktree_base_dir
                                        or ".loom/worktrees",
                                    base_branch=gs.worktree_base_branch
                                        or "")
                                if wt_path:
                                    cell.directory = wt_path
                                    state._emit_agent(cell)
                                    state._db_save_agent(cell)
                    await bridge.create_session(
                        cell, env_vars=env,
                        init_script=init, shell=shell)

            elif cmd == "move_group":
                state.move_group(data["group"], data.get("before", ""))
                await bridge.reorder_tabs()

            elif cmd == "move_agent":
                state.move_agent(data["id"], data["target_group"],
                                 data.get("before", ""))
                await bridge.reorder_tabs()

            elif cmd == "reparent_terminal":
                state.reparent_terminal(data["id"],
                                        data.get("parent_id", ""))
                await bridge.reorder_tabs()

            elif cmd == "reorder_child":
                state.reorder_child(data["id"], data["parent_id"],
                                    data.get("before", ""))
                await bridge.reorder_tabs()

            elif cmd == "worktree_create":
                cell = state.agents.get(data["id"])
                if cell and not cell.worktree_path and cell.directory:
                    gs = state.get_group_settings(cell.group)
                    repo_root = await worktree_mgr.get_repo_root(
                        cell.directory)
                    if repo_root:
                        wt_path = await worktree_mgr.create(
                            cell, repo_root,
                            base_dir=gs.worktree_base_dir
                                or ".loom/worktrees",
                            base_branch=gs.worktree_base_branch or "")
                        if wt_path:
                            cell.directory = wt_path
                            state._emit_agent(cell)
                            state._db_save_agent(cell)
                            # Relaunch if requested by the UI
                            if data.get("relaunch"):
                                if cell.session_id:
                                    await bridge.close_session(
                                        cell.session_id)
                                cell.status = "stopped"
                                cell.session_id = None
                                # Clear session ID — the old session
                                # may not exist (no prompts sent yet)
                                cell.agent_session_id = ""
                                env = {**gs.env_vars,
                                       **gs.agent_env_vars} or None
                                shell = (gs.agent_shell
                                         or gs.shell or "")
                                await bridge.create_session(
                                    cell, env_vars=env, shell=shell)

            elif cmd == "worktree_remove":
                cell = state.agents.get(data["id"])
                if cell and cell.worktree_path:
                    # Restore directory to original repo root
                    repo_root = cell.worktree_repo_root
                    await worktree_mgr.remove(cell)
                    if repo_root:
                        cell.directory = repo_root
                    state._emit_agent(cell)
                    state._db_save_agent(cell)
                    # Relaunch if requested by the UI
                    if data.get("relaunch") and cell.cell_type == "agent":
                        if cell.session_id:
                            await bridge.close_session(cell.session_id)
                        cell.status = "stopped"
                        cell.session_id = None
                        cell.agent_session_id = ""
                        gs = state.get_group_settings(cell.group)
                        env = {**gs.env_vars,
                               **gs.agent_env_vars} or None
                        shell = gs.agent_shell or gs.shell or ""
                        await bridge.create_session(
                            cell, env_vars=env, shell=shell)

            elif cmd == "worktree_checkpoint":
                cell = state.agents.get(data["id"])
                if cell and cell.worktree_path:
                    msg = _checkpoint_message(cell)
                    await worktree_mgr.checkpoint(cell, message=msg)
                    state._emit_agent(cell)
                    state._db_save_agent(cell)

            elif cmd == "worktree_history":
                cell = state.agents.get(data.get("id", ""))
                commits = []
                if cell and cell.worktree_path:
                    commits = await worktree_mgr.list_checkpoints(cell)
                return {
                    "type": "worktree_history",
                    "id": data.get("id", ""),
                    "branch": cell.worktree_branch if cell else "",
                    "base_branch": cell.worktree_base_branch
                    if cell else "",
                    "commits": commits,
                }

            elif cmd == "worktree_rollback":
                cell = state.agents.get(data.get("id", ""))
                sha = data.get("sha", "")
                if cell and cell.worktree_path and sha:
                    await worktree_mgr.rollback(cell, sha)
                    state._emit_agent(cell)
                    state._db_save_agent(cell)

            elif cmd == "worktree_merge":
                cell = state.agents.get(data.get("id", ""))
                if cell and cell.worktree_path and cell.worktree_branch:
                    if not cell.session_id:
                        result = {
                            "type": "error",
                            "message": "Session not running. Relaunch "
                                       "the agent first, or merge manually.",
                        }
                    else:
                        gs = state.get_group_settings(cell.group)
                        base = cell.worktree_base_branch or "main"
                        branch = cell.worktree_branch
                        repo = cell.worktree_repo_root or ""
                        squash = gs.worktree_merge_squash
                        method = ("Squash merge" if squash
                                  else "Merge")
                        prompt = (
                            f"{method} the current branch "
                            f"`{branch}` into `{base}`. The "
                            f"main repo is at `{repo}`. If there "
                            f"are merge conflicts, resolve them. "
                            f"Do not delete the worktree branch "
                            f"after merging. Write a clear, "
                            f"descriptive commit message that "
                            f"summarizes everything this branch "
                            f"accomplished — review the full diff "
                            f"and commit history to inform it.")
                        extra = gs.worktree_merge_instructions.strip()
                        if extra:
                            prompt += " " + extra
                        # Record base SHA before merge for fallback
                        # verification (squash merges with diverged
                        # base can't be detected from git state alone)
                        pre_sha = ""
                        try:
                            p = await asyncio.create_subprocess_exec(
                                "git", "-C", repo or ".",
                                "rev-parse", base,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.DEVNULL,
                            )
                            out, _ = await p.communicate()
                            if p.returncode == 0:
                                pre_sha = out.decode().strip()
                        except Exception:
                            pass
                        close_flag = bool(data.get("close_on_merge"))
                        await bridge.send_text(
                            cell.session_id, prompt + "\r")
                        _pending_merges[cell.id] = (pre_sha, close_flag)
                        cell.status = "running"
                        state._emit_agent(cell)
                        # Ephemeral status — no DB write needed

            # -- Board commands (Phase 5) --
            elif cmd == "board_add_task":
                bt = state.board_add_task(
                    task=data.get("task", ""),
                    group=data.get("group", ""),
                    lane=data.get("lane", ""),
                    action_name=data.get("action_name", ""),
                    action_vars=data.get("action_vars", {}),
                    assignee=data.get("assignee", ""),
                    agent_id=data.get("agent_id", ""),
                    labels=data.get("labels", []),
                )
                if not bt:
                    result = {"type": "error",
                              "message": "Invalid lane, group, or empty task"}

            elif cmd == "board_update_task":
                tid = data.get("id", "")
                fields = {k: v for k, v in data.items()
                          if k not in ("cmd", "id")}
                state.board_update_task(tid, **fields)

            elif cmd == "board_remove_task":
                state.board_remove_task(data.get("id", ""))

            elif cmd == "board_move_task":
                state.board_move_task(
                    data.get("id", ""),
                    data.get("lane", ""),
                    data.get("position"))

            elif cmd == "board_reorder_task":
                state.board_reorder_task(
                    data.get("id", ""),
                    data.get("position", 0))

            elif cmd == "dispatch_task":
                tid = data.get("id", "")
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                else:
                    group = task.group
                    if not group or group not in state.groups:
                        # Fall back to first group
                        group = next(iter(state.groups), "")
                    if not group:
                        result = {"type": "error",
                                  "message": "No group available"}
                    else:
                        cell = None
                        agent_id = data.get("agent_id", "")
                        if agent_id:
                            # Dispatch to existing agent
                            cell = state.agents.get(agent_id)
                            if not cell:
                                result = {"type": "error",
                                          "message": "Agent not found"}
                        elif data.get("create_agent"):
                            # Create a new agent
                            from loom.state import _slugify
                            slug = _slugify(task.task)
                            agent_name = slug or "agent"
                            gs = state.get_group_settings(group)
                            profile = gs.agent_profile \
                                or gs.profile or "Default"
                            directory = gs.agent_directory \
                                or gs.default_directory or ""
                            _ac = gs.agent_tab_color
                            tab_color = (_ac if _ac != "none" else "") \
                                or gs.tab_color or ""
                            shell = gs.agent_shell or gs.shell or ""
                            env = {**gs.env_vars, **gs.agent_env_vars} \
                                or None
                            command = gs.agent_boot_command

                            cell = state.add_agent(
                                name=agent_name, group=group,
                                profile=profile, command=command,
                                directory=directory,
                                tab_color=tab_color)
                            if cell:
                                # Worktree
                                if gs.git_worktree and cell.directory:
                                    repo_root = \
                                        await worktree_mgr.get_repo_root(
                                            cell.directory)
                                    if repo_root:
                                        wt_path = \
                                            await worktree_mgr.create(
                                                cell, repo_root,
                                                base_dir=gs.worktree_base_dir
                                                    or ".loom/worktrees",
                                                base_branch=
                                                    gs.worktree_base_branch
                                                    or "")
                                        if wt_path:
                                            cell.directory = wt_path
                                            state._emit_agent(cell)
                                            state._db_save_agent(cell)

                                # Worktree inheritance (pipeline)
                                inherit_from = data.get(
                                    "inherit_worktree_from", "")
                                if inherit_from:
                                    src = state.agents.get(inherit_from)
                                    if src and src.worktree_path:
                                        cell.worktree_path = \
                                            src.worktree_path
                                        cell.worktree_branch = \
                                            src.worktree_branch
                                        cell.worktree_repo_root = \
                                            src.worktree_repo_root
                                        cell.worktree_base_branch = \
                                            src.worktree_base_branch
                                        cell.directory = \
                                            src.worktree_path
                                        state._emit_agent(cell)
                                        state._db_save_agent(cell)
                                elif not cell.worktree_path \
                                        and task.parent_task_id:
                                    # HITL dispatch: walk parent
                                    # chain to find worktree
                                    _ptid = task.parent_task_id
                                    while _ptid:
                                        _pt = state.board_tasks.get(
                                            _ptid)
                                        if not _pt:
                                            break
                                        if _pt.agent_id:
                                            _pa = state.agents.get(
                                                _pt.agent_id)
                                            if _pa and \
                                                    _pa.worktree_path:
                                                cell.worktree_path = \
                                                    _pa.worktree_path
                                                cell.worktree_branch =\
                                                    _pa.worktree_branch
                                                cell.worktree_repo_root = \
                                                    _pa.worktree_repo_root
                                                cell.worktree_base_branch = \
                                                    _pa.worktree_base_branch
                                                cell.directory = \
                                                    _pa.worktree_path
                                                state._emit_agent(cell)
                                                state._db_save_agent(
                                                    cell)
                                                break
                                        _ptid = _pt.parent_task_id

                                await bridge.create_session(
                                    cell, env_vars=env, shell=shell)

                                # Auto-create child terminals (off by default for dispatch)
                                if gs.dispatch_auto_terminals:
                                    t_profile = gs.terminal_profile \
                                        or gs.profile or "Default"
                                    t_dir = gs.terminal_directory \
                                        or gs.default_directory or ""
                                    _ttc = gs.terminal_tab_color
                                    t_color = (_ttc if _ttc != "none" \
                                        else "") or gs.tab_color or ""
                                    t_shell = gs.terminal_shell \
                                        or gs.shell or ""
                                    t_env = {**gs.env_vars,
                                             **gs.terminal_env_vars} \
                                        or None
                                    t_cmd = \
                                        gs.terminal_boot_command or ""
                                    if gs.terminal_command_args and t_cmd:
                                        t_cmd = (t_cmd + " "
                                                 + gs.terminal_command_args
                                                 ).strip()
                                    for i in range(gs.auto_terminals):
                                        t_name = state.next_cell_name(
                                            group, "terminal")
                                        t = state.add_terminal(
                                            name=t_name, group=group,
                                            profile=t_profile,
                                            command=t_cmd,
                                            directory=t_dir
                                                or cell.directory,
                                            tab_color=t_color,
                                            parent_id=cell.id)
                                        if t:
                                            await bridge.create_session(
                                                t, env_vars=t_env,
                                                init_script=
                                                    gs.terminal_init_script,
                                                shell=t_shell)

                        if cell:
                            # Link task to agent and move to In Progress
                            dispatch_lane = \
                                state.get_group_settings(group) \
                                    .dispatch_lane or "In Progress"
                            state.board_update_task(
                                tid, agent_id=cell.id,
                                lane=dispatch_lane)

                            # Build loom context for template rendering
                            loom_ctx = _build_loom_context(cell, task)

                            # Compose prompt: action-aware
                            prompt = None
                            base_dir = ""
                            if task.action_name \
                                    and not data.get("force_no_action"):
                                base_dir = cell.directory \
                                    or await _resolve_base_dir(group)
                                tvars = {"TASK": task.task,
                                         **(task.action_vars or {})}
                                rendered = action_mgr.render_prompt(
                                    task.action_name, tvars,
                                    base_dir=base_dir,
                                    loom_context=loom_ctx)
                                if rendered is None:
                                    # Action deleted — warn frontend
                                    result = {
                                        "type":
                                            "dispatch_action_missing",
                                        "task_id": tid,
                                        "action_name":
                                            task.action_name}
                                    prompt = None
                                else:
                                    prompt = rendered
                            elif task.instructions or task.context \
                                    or task.criteria:
                                # Legacy fallback for old tasks
                                parts = []
                                if task.task:
                                    parts.append(task.task)
                                if task.instructions:
                                    parts.append(task.instructions)
                                if task.context:
                                    parts.append(task.context)
                                if task.criteria:
                                    parts.append(task.criteria)
                                prompt = "\n\n".join(parts)
                            else:
                                prompt = task.task

                            if prompt:
                                is_clean = loom_ctx["context"]["is_clean"]
                                prompt += _build_postscript(
                                    task, action_mgr,
                                    base_dir if task.action_name
                                    else "",
                                    is_clean=is_clean)

                                # Track dispatch count
                                cell.tasks_dispatched += 1
                                state._emit_agent(cell)
                                state._db_save_agent(cell)
                                _panel_event(
                                    "task_dispatched", cell.id,
                                    cell.name, cell.group,
                                    task.task[:80],
                                    task_id=task.id)

                                if agent_id and cell.session_id:
                                    delay = 3 if data.get(
                                        "_self_dispatch") else 0
                                    if delay:
                                        # Self-dispatch: delay so
                                        # prompt arrives after current
                                        # agent turn finishes
                                        async def _delayed(c, p, d):
                                            await asyncio.sleep(d)
                                            if c.session_id:
                                                await bridge.send_text(
                                                    c.session_id,
                                                    p if p.endswith("\r")
                                                    else p + "\r")
                                                c.status = "running"
                                                state._emit_agent(c)
                                                await state.broadcast()
                                        asyncio.create_task(
                                            _delayed(cell, prompt,
                                                     delay))
                                    else:
                                        # Existing agent — send now
                                        await bridge.send_text(
                                            cell.session_id,
                                            prompt
                                            if prompt.endswith("\r")
                                            else prompt + "\r")
                                        cell.status = "running"
                                        state._emit_agent(cell)
                                elif data.get("create_agent") \
                                        and cell.session_id:
                                    # New agent — wait for boot
                                    async def _dispatch_send(c, p):
                                        await asyncio.sleep(2)
                                        if c.session_id:
                                            await bridge.send_text(
                                                c.session_id,
                                                p if p.endswith("\r")
                                                else p + "\r")
                                            c.status = "running"
                                            state._emit_agent(c)
                                            await state.broadcast()
                                    asyncio.create_task(
                                        _dispatch_send(cell, prompt))

            elif cmd == "resolve_ask":
                # Resolve an ask task: send answer to parent's agent
                tid = data.get("id", "")
                answer = data.get("answer", "")
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                elif "human" not in (task.labels or []):
                    result = {"type": "error",
                              "message": "Not an ask task"}
                elif not answer.strip():
                    result = {"type": "error",
                              "message": "Answer is required"}
                elif not task.parent_task_id:
                    result = {"type": "error",
                              "message": "Ask task has no parent"}
                else:
                    parent = state.board_tasks.get(
                        task.parent_task_id)
                    if not parent:
                        result = {"type": "error",
                                  "message": "Parent task not found"}
                    elif not parent.agent_id:
                        result = {"type": "error",
                                  "message": "Parent task has no "
                                             "linked agent"}
                    else:
                        agent = state.agents.get(parent.agent_id)
                        if not agent or not agent.session_id:
                            result = {
                                "type": "error",
                                "message": "Parent agent not "
                                           "available"}
                        else:
                            # Send answer to the parent's agent
                            q = task.task
                            if len(q) > 120:
                                q = q[:120] + "…"
                            msg = (f"Answer to your question "
                                   f"\"{q}\":\n{answer}")
                            await bridge.send_text(
                                agent.session_id,
                                msg + "\r")
                            agent.status = "running"
                            state._emit_agent(agent)

                            # Move ask task to Done (no cascade)
                            from datetime import datetime, timezone
                            if task.lane != "Done":
                                state.board_move_task(
                                    task.id, "Done")
                            task.status = ""
                            task.updated_at = datetime.now(
                                timezone.utc).isoformat()
                            state._emit("task_upsert",
                                        **asdict(task))
                            state._db_save_task(task)

                            # Clear parent "Awaiting Input" status
                            parent.status = ""
                            parent.updated_at = datetime.now(
                                timezone.utc).isoformat()
                            state._emit("task_upsert",
                                        **asdict(parent))
                            state._db_save_task(parent)

                            # Clear root task status too
                            root_id = parent.pipeline_root_id \
                                or parent.id
                            if root_id != parent.id:
                                root = state.board_tasks.get(
                                    root_id)
                                if root and root.status:
                                    root.status = ""
                                    root.updated_at = datetime.now(
                                        timezone.utc).isoformat()
                                    state._emit("task_upsert",
                                                **asdict(root))
                                    state._db_save_task(root)

                            _panel_event(
                                "ask_resolved", agent.id,
                                agent.name, agent.group,
                                "Resolved: " + q,
                                task_id=tid)
                            result = {"type": "ok",
                                      "task_id": tid}

            elif cmd == "preview_prompt":
                # Preview rendered prompt for a task or inline params
                tid = data.get("id", "")
                act_name = data.get("action_name", "")
                task_text = data.get("task", "")
                avars = data.get("action_vars", {})
                act_group = data.get("group", "")

                if tid and not task_text:
                    t = state.board_tasks.get(tid)
                    if t:
                        task_text = t.task
                        act_name = act_name or t.action_name
                        avars = avars or t.action_vars or {}
                        act_group = act_group or t.group

                if act_name:
                    base_dir = await _resolve_base_dir(act_group)
                    rendered = action_mgr.render_prompt(
                        act_name,
                        {"TASK": task_text, **avars},
                        base_dir=base_dir)
                    if rendered is None:
                        result = {"type": "prompt_preview",
                                  "prompt": task_text,
                                  "warning": f"Action "
                                             f"\"{act_name}\" not found"}
                    else:
                        result = {"type": "prompt_preview",
                                  "prompt": rendered}
                else:
                    result = {"type": "prompt_preview",
                              "prompt": task_text}

            elif cmd == "board_add_lane":
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

            elif cmd == "ai_report":
                cell_id = data.get("cell_id", "")
                action = data.get("action", "")
                message = data.get("message", "")
                task_id = data.get("task_id", "")

                cell = state.agents.get(cell_id)
                if not cell:
                    result = {"type": "error",
                              "message": f"Cell {cell_id} not found"}
                else:
                    task = state.board_tasks.get(task_id) \
                        if task_id else None

                    def _add_label(t, label):
                        if label not in t.labels:
                            t.labels.append(label)

                    def _save_task(t):
                        from datetime import datetime, timezone
                        t.updated_at = datetime.now(
                            timezone.utc).isoformat()
                        state._emit("task_upsert", **asdict(t))
                        state._db_save_task(t)

                    def _cascade_done(task_id):
                        """Walk up parent chain, completing ancestors
                        whose children are all Done."""
                        t = state.board_tasks.get(task_id)
                        if not t or not t.parent_task_id:
                            return
                        pid = t.parent_task_id
                        while pid:
                            parent = state.board_tasks.get(pid)
                            if not parent:
                                break
                            if parent.lane == "Done":
                                pid = parent.parent_task_id
                                continue
                            children = state.board_get_children(pid)
                            if all(c.lane == "Done" for c in children):
                                parent.status = ""
                                state.board_move_task(pid, "Done")
                                _save_task(parent)
                                pid = parent.parent_task_id
                            else:
                                break

                    if action == "done":
                        cell.activity = ""
                        cell.activity_detail = ""
                        cell.needs_attention = False
                        cell.error_message = ""
                        if message:
                            cell.last_summary = message
                        state._emit_agent(cell)
                        if task and task.lane != "Done":
                            state.board_move_task(task.id, "Done")
                        if task:
                            task.status = ""
                            _save_task(task)
                            _cascade_done(task.id)
                        _panel_event(
                            "task_completed", cell.id,
                            cell.name, cell.group,
                            message or "Task completed")

                    elif action == "blocked":
                        cell.needs_attention = True
                        cell.activity = "waiting"
                        cell.activity_detail = message
                        state._emit_agent(cell)
                        if task:
                            _add_label(task, "blocked")
                            _save_task(task)
                        _panel_event(
                            "agent_blocked", cell.id,
                            cell.name, cell.group, message)

                    elif action == "error":
                        cell.error_message = message
                        cell.needs_attention = True
                        state._emit_agent(cell)
                        if task:
                            _add_label(task, "error")
                            _save_task(task)
                        _panel_event(
                            "agent_error", cell.id,
                            cell.name, cell.group, message)

                    elif action == "progress":
                        cell.activity_detail = message
                        if cell.needs_attention:
                            cell.needs_attention = False
                        state._emit_agent(cell)

                    elif action == "ready":
                        cell.activity = ""
                        cell.activity_detail = "ready"
                        cell.needs_attention = False
                        cell.error_message = ""
                        state._emit_agent(cell)
                        if task:
                            if task.lane != "Done":
                                state.board_move_task(
                                    task.id, "Done")
                            task.agent_id = ""
                            task.status = ""
                            _save_task(task)
                            _cascade_done(task.id)
                        _panel_event(
                            "task_completed", cell.id,
                            cell.name, cell.group,
                            "Ready (task completed)")

                    elif action == "derive":
                        # Derive a new task and dispatch it
                        act_name = data.get("action_name", "")
                        act_vars = data.get("action_vars", {})
                        derive_group = data.get("group", "")
                        reuse_self = data.get("reuse_self", False)
                        target_agent = data.get("target_agent", "")

                        if not task:
                            result = {"type": "error",
                                      "message":
                                          "No linked task to derive from"}
                        elif not message:
                            result = {"type": "error",
                                      "message":
                                          "Derive requires a description"}
                        else:
                            # Validate transition
                            base_dir = await _resolve_base_dir(
                                task.group)
                            cur_transitions = \
                                action_mgr.get_transitions(
                                    task.action_name, base_dir)
                            valid_targets = [
                                t["action"] for t in cur_transitions
                                if isinstance(t, dict)
                                and t.get("action")]
                            if cur_transitions and act_name \
                                    and act_name not in valid_targets:
                                result = {
                                    "type": "error",
                                    "message":
                                        f"Action '{task.action_name}'"
                                        f" cannot transition to "
                                        f"'{act_name}'. Valid: "
                                        f"{', '.join(valid_targets)}"}
                            else:
                                # Check depth limit
                                new_depth = task.pipeline_depth + 1
                                act_meta = \
                                    action_mgr.load_action(
                                        act_name, base_dir) \
                                    if act_name else None
                                max_d = (
                                    (act_meta or {}).get("max_depth")
                                    or state.global_settings
                                        .max_pipeline_depth
                                    or 0)
                                if max_d and new_depth > max_d:
                                    cell.needs_attention = True
                                    state._emit_agent(cell)
                                    if task:
                                        _add_label(task,
                                                   "depth-limit")
                                        _save_task(task)
                                    result = {
                                        "type": "error",
                                        "message":
                                            f"Pipeline depth limit "
                                            f"({max_d}) reached"}
                                else:
                                    # Keep parent in In Progress;
                                    # update its status from transition
                                    cell.activity = ""
                                    cell.activity_detail = ""
                                    cell.needs_attention = False
                                    cell.error_message = ""
                                    state._emit_agent(cell)
                                    # Determine status from transition
                                    derive_status = ""
                                    if cur_transitions and act_name:
                                        for tr in cur_transitions:
                                            if isinstance(tr, dict) \
                                                    and tr.get("action") \
                                                    == act_name:
                                                derive_status = tr.get(
                                                    "status", "")
                                                break
                                    if not derive_status and act_name:
                                        derive_status = act_name
                                    # Update parent task status
                                    task.status = derive_status
                                    _save_task(task)
                                    # Propagate status to root
                                    root_id_s = \
                                        task.pipeline_root_id \
                                        or task.id
                                    if root_id_s != task.id:
                                        root_t = \
                                            state.board_tasks.get(
                                                root_id_s)
                                        if root_t:
                                            root_t.status = \
                                                derive_status
                                            _save_task(root_t)
                                    # Create derived task
                                    grp = derive_group \
                                        or task.group
                                    root_id = \
                                        task.pipeline_root_id \
                                        or task.id
                                    new_task = state.board_add_task(
                                        task=message,
                                        group=grp,
                                        lane="Backlog",
                                        action_name=act_name,
                                        action_vars=act_vars,
                                        labels=["derived"],
                                        parent_task_id=task.id,
                                        pipeline_depth=new_depth,
                                        pipeline_root_id=root_id,
                                    )
                                    if new_task:
                                        _panel_event(
                                            "task_derived",
                                            cell.id, cell.name,
                                            cell.group,
                                            message[:80],
                                            task_id=new_task.id)
                                        # Determine dispatch target
                                        target_id = None
                                        if reuse_self:
                                            target_id = cell.id
                                        elif target_agent:
                                            target_id = \
                                                _resolve_agent_id(
                                                    target_agent)
                                            if not target_id:
                                                result = {
                                                    "type": "error",
                                                    "message":
                                                        "Agent not "
                                                        "found: "
                                                        + target_agent
                                                }

                                        if result and \
                                                result.get("type") \
                                                == "error":
                                            pass  # skip dispatch
                                        elif target_id:
                                            # Dispatch to existing
                                            # agent
                                            tgt = state.agents.get(
                                                target_id)
                                            dispatch_data = {
                                                "cmd": "dispatch_task",
                                                "id": new_task.id,
                                                "agent_id": target_id,
                                            }
                                            if reuse_self:
                                                dispatch_data[
                                                    "_self_dispatch"
                                                ] = True
                                            # Inherit worktree from
                                            # target agent
                                            if tgt and \
                                                    tgt.worktree_path:
                                                dispatch_data[
                                                    "inherit_worktree"
                                                    "_from"
                                                ] = target_id
                                            elif cell.worktree_path:
                                                dispatch_data[
                                                    "inherit_worktree"
                                                    "_from"
                                                ] = cell.id
                                            await state.broadcast()
                                            dr = \
                                                await handle_command(
                                                    dispatch_data)
                                            result = {
                                                "type": "ok",
                                                "task_id":
                                                    new_task.id,
                                                "agent_id":
                                                    target_id}
                                        else:
                                            # Default: new agent
                                            dispatch_data = {
                                                "cmd":
                                                    "dispatch_task",
                                                "id": new_task.id,
                                                "create_agent": True,
                                            }
                                            # Inherit worktree from
                                            # parent agent
                                            if cell.worktree_path:
                                                dispatch_data[
                                                    "inherit_worktree"
                                                    "_from"
                                                ] = cell.id
                                            await state.broadcast()
                                            dr = \
                                                await handle_command(
                                                    dispatch_data)
                                            result = {
                                                "type": "ok",
                                                "task_id":
                                                    new_task.id,
                                                "agent_id":
                                                    new_task.agent_id}
                                    else:
                                        result = {
                                            "type": "error",
                                            "message":
                                                "Failed to create "
                                                "derived task"}

                    elif action == "ask":
                        # Create a derived task in Backlog for human
                        if not task:
                            result = {"type": "error",
                                      "message":
                                          "No linked task to derive from"}
                        elif not message:
                            result = {"type": "error",
                                      "message":
                                          "Ask requires a question"}
                        else:
                            # Keep parent in In Progress with
                            # "Awaiting Input" status
                            cell.activity = ""
                            cell.activity_detail = ""
                            cell.needs_attention = True
                            cell.error_message = ""
                            state._emit_agent(cell)
                            task.status = "Awaiting Input"
                            _save_task(task)
                            # Propagate status to root
                            root_id_s = task.pipeline_root_id \
                                or task.id
                            if root_id_s != task.id:
                                root_t = state.board_tasks.get(
                                    root_id_s)
                                if root_t:
                                    root_t.status = "Awaiting Input"
                                    _save_task(root_t)
                            # Create HITL task in Backlog
                            grp = task.group
                            root_id = task.pipeline_root_id \
                                or task.id
                            new_task = state.board_add_task(
                                task=message,
                                group=grp,
                                lane="Backlog",
                                labels=["human", "derived"],
                                parent_task_id=task.id,
                                pipeline_depth=
                                    task.pipeline_depth + 1,
                                pipeline_root_id=root_id,
                            )
                            if new_task:
                                result = {
                                    "type": "ok",
                                    "task_id": new_task.id}
                                _panel_event(
                                    "ask_created", cell.id,
                                    cell.name, cell.group,
                                    message,
                                    task_id=new_task.id)
                            else:
                                result = {
                                    "type": "error",
                                    "message":
                                        "Failed to create ask task"}

                    elif action == "name":
                        if not message:
                            result = {"type": "error",
                                      "message":
                                          "Name is required"}
                        else:
                            old_name = cell.name
                            state.update_agent(cell.id,
                                               name=message)
                            if cell.session_id:
                                await bridge.update_session(
                                    cell, old_name)
                            result = {"type": "ok",
                                      "slug": cell.slug}

                    else:
                        result = {"type": "error",
                                  "message":
                                      f"Unknown ai action: {action}"}

            elif cmd == "task_chain":
                tid = data.get("task_id", "")
                task = state.board_tasks.get(tid)
                if not task:
                    result = {"type": "error",
                              "message": "Task not found"}
                else:
                    chain = state.board_get_chain(tid)
                    result = {
                        "type": "ok",
                        "root_id": task.pipeline_root_id or task.id,
                        "chain": [
                            {"id": t.id, "task": t.task,
                             "lane": t.lane,
                             "status": t.status,
                             "depth": t.pipeline_depth,
                             "agent_id": t.agent_id,
                             "action_name": t.action_name,
                             "parent_task_id": t.parent_task_id,
                             "labels": t.labels}
                            for t in chain
                        ],
                    }

            elif cmd == "discover_pipelines":
                base_dir = await _resolve_base_dir(
                    data.get("group", ""))
                pipelines = action_mgr.discover_pipelines(base_dir)
                result = {"type": "pipelines", "pipelines": pipelines}

            elif cmd == "restart":
                log.info("Restart requested — cleaning up and re-executing")
                await keybindings.remove(connection, _displaced[0])
                # Persist all agents (status etc.) before restart
                for cell in state.agents.values():
                    state._db_save_agent(cell)
                os.execv(sys.executable, [sys.executable] + sys.argv)

        except Exception as exc:
            log.exception("Command '%s' failed", cmd)
            result = {"type": "error", "message": str(exc)}

        await state.broadcast()
        return result

    # -- Events endpoint (agent hooks) ----------------------------------------

    async def handle_events(request):
        """Receive events from agent hooks (Claude Code HTTP hooks, etc.)."""
        try:
            raw = await request.json()
        except Exception:
            return web.json_response({}, status=400)

        # Correlate: X-Loom-Cell-Id header (primary) → cwd match (fallback)
        cell_id = request.headers.get("X-Loom-Cell-Id", "")
        cell = state.agents.get(cell_id) if cell_id else None

        if not cell:
            # Fallback: match by cwd
            cwd = raw.get("cwd", "")
            if cwd:
                for c in state.agents.values():
                    if c.session_id and c.directory and \
                            os.path.realpath(c.directory) == os.path.realpath(cwd):
                        cell = c
                        break

        if not cell:
            log.debug("Event from unknown cell (id=%s, cwd=%s), discarding",
                      cell_id, raw.get("cwd", ""))
            return web.json_response({})

        # Parse through the adapter
        adapter = get_adapter(cell.agent_type)
        event = adapter.parse_event(raw, cell)
        if event:
            await event_bus.emit(event)

        # Always return 200 with empty JSON — never block the agent
        return web.json_response({})

    # -- Panel event helper -------------------------------------------------

    def _panel_event(kind, cell_id, agent_name, group, message,
                     task_id=""):
        """Append a panel event and queue a delta broadcast."""
        pe = panel_log.append(
            kind=kind, cell_id=cell_id, agent_name=agent_name,
            group=group, message=message, task_id=task_id)
        state._emit("event_append", **pe)

    # -- HTTP / WS routes ---------------------------------------------------

    async def handle_index(_request):
        from .config import WEBVIEW_FILE  # re-read after init_paths
        return web.FileResponse(WEBVIEW_FILE)

    async def handle_ws(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        state._ws_clients.add(ws)
        await ws.send_str(state.snapshot_msg())
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        result = await handle_command(
                            json.loads(msg.data))
                        if result:
                            await ws.send_str(json.dumps(result))
                    except json.JSONDecodeError:
                        log.warning("Received malformed JSON from webview")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
        finally:
            state._ws_clients.discard(ws)
        return ws

    # -- REST API endpoint --------------------------------------------------

    async def handle_api_cmd(request):
        """REST endpoint for CLI and scripting access.

        Accepts the same {"cmd": ..., ...} payloads as the WS handler.
        Returns {"ok": true, "data": ...} on success or
        {"ok": false, "error": "..."} on failure.
        """
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"ok": False, "error": "invalid JSON"}, status=400)

        cmd = data.get("cmd")
        if not cmd:
            return web.json_response(
                {"ok": False, "error": "missing 'cmd'"}, status=400)

        try:
            result = await handle_command(data)
        except Exception as exc:
            log.exception("API command '%s' failed", cmd)
            return web.json_response(
                {"ok": False, "error": str(exc)}, status=500)

        if result and result.get("type") == "error":
            return web.json_response(
                {"ok": False, "error": result.get("message", "")})

        payload = result if result else {"type": "state",
                                         **state.to_dict()}
        return web.json_response({"ok": True, "data": payload})

    # -- Start server -------------------------------------------------------

    app_server = web.Application()
    app_server.router.add_get("/", handle_index)
    app_server.router.add_get("/ws", handle_ws)
    app_server.router.add_post("/events", handle_events)
    app_server.router.add_post("/api/cmd", handle_api_cmd)
    from .config import SCRIPT_DIR
    app_server.router.add_static("/static", SCRIPT_DIR / "static")

    runner = web.AppRunner(app_server)
    await runner.setup()
    site = web.TCPSite(runner, BIND_HOST, WS_PORT, reuse_address=True)
    try:
        await site.start()
    except OSError as exc:
        log.error("Cannot bind port %d: %s — is another instance running?",
                  WS_PORT, exc)
        raise
    log.info("HTTP/WS server listening on %s:%d", BIND_HOST, WS_PORT)

    # -- Register toolbelt (skipped in standalone-only mode) ----------------

    if not STANDALONE:
        await iterm2.tool.async_register_web_view_tool(
            connection,
            display_name="Loom",
            identifier="com.loom.toolbelt",
            reveal_if_already_registered=True,
            url=f"http://127.0.0.1:{WS_PORT}/",
        )
        log.info("Toolbelt webview registered — Loom ready")
    else:
        log.info("Standalone mode — toolbelt registration skipped")
        log.info("Open http://127.0.0.1:%d/ in a browser", WS_PORT)

    await asyncio.Future()
