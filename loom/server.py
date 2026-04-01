"""aiohttp server, WebSocket command handler, and iTerm2 entry point."""

import asyncio
import json
import os
import shlex
import sys
import time
from collections import deque

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
from .adapters import get_adapter, get_providers, get_default_command_for_provider
from .notifications import NotificationManager
from .worktree import WorktreeManager
from .actions import ActionManager, LOOM_CONTEXT_STUB
from .templates import TemplateManager
from . import keybindings
from .mcp import create_mcp_handler

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
    if isinstance(agent, str):
        if agent:
            doc["agent"] = agent
    else:
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
    panel_log = PanelEventLog(
        max_size=state.global_settings.max_event_log, db=db)
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
    template_mgr = TemplateManager()

    # cell ID → (pre-merge base SHA, close_on_merge, clear_context)
    _pending_merges: dict[str, tuple[str, bool, bool]] = {}

    async def _safe_remove_worktree(cell):
        """Remove a worktree only if no other agent shares it."""
        if not cell.worktree_path:
            return
        other_users = [a for a in state.agents.values()
                       if a.id != cell.id
                       and a.worktree_path == cell.worktree_path]
        if other_users:
            log.info("Skipping worktree removal for '%s' — shared with %s",
                     cell.name,
                     ", ".join(a.name for a in other_users))
            cell.worktree_path = ""
            cell.worktree_branch = ""
            cell.worktree_base_branch = ""
            cell.worktree_repo_root = ""
            cell.worktree_checkpoints = 0
        else:
            await worktree_mgr.remove(cell)

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
            pre_sha, close_on_merge, clear_context = \
                _pending_merges.pop(cell.id)
            merged = await worktree_mgr.is_merged(cell)
            if not merged and pre_sha:
                merged = await worktree_mgr.check_base_advanced(
                    cell, pre_sha)
            if merged:
                log.info("Merge verified for '%s': branch %s merged into %s",
                         cell.name, cell.worktree_branch,
                         cell.worktree_base_branch)
                cell.worktree_checkpoints = 0
                state._emit_agent(cell)
                await _broadcast_toast(
                    f'"{cell.name}" merged to {cell.worktree_base_branch}',
                    "success")
                if not close_on_merge and cell.worktree_path:
                    # Rebase onto base branch so the agent continues
                    # from a clean state after a successful merge.
                    valid = await worktree_mgr.validate(cell)
                    if valid:
                        ok = await worktree_mgr.rebase_onto_base(cell)
                        if ok:
                            cell.worktree_checkpoints = \
                                await worktree_mgr.count_commits(cell)
                            cell.worktree_dirty = False
                            cell.worktree_diff = {}
                            state._emit_agent(cell)
                        else:
                            log.warning(
                                "Post-merge rebase failed for '%s'"
                                " — worktree left as-is",
                                cell.name)
                if clear_context and not close_on_merge \
                        and cell.session_id:
                    # Send /clear to reset the agent's conversation
                    await bridge.send_text(
                        cell.session_id, "/clear\r")
                    cell.tasks_dispatched = 0
                    state._emit_agent(cell)
                    state._db_save_agent(cell)
                    log.info("Cleared context for '%s' after merge",
                             cell.name)
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
                            if hasattr(adapter, "uninstall_mcp_config"):
                                adapter.uninstall_mcp_config(
                                    os.path.expanduser(c.directory))
                        event_bus.cleanup_cell(c.id)
                        await _safe_remove_worktree(c)
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
            if cell.worktree_auto_checkpoint:
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

    def _resolve_provider_command(
        provider: str, boot_command: str, default_command: str,
    ) -> tuple[str, str]:
        """Resolve (command, agent_type) from provider + boot_command.

        When *provider* names a known adapter, the agent_type is set
        explicitly and the command defaults to the adapter's default.
        When *provider* is empty, auto-detection in bridge.py takes over.
        """
        if provider:
            adapter_cmd = get_default_command_for_provider(provider)
            if adapter_cmd:  # known provider
                return (boot_command or adapter_cmd, provider)
        # No provider — fall through to boot_command / global default
        return (boot_command or default_command, "")

    def _suggest_template_agent_name(group: str, template_name: str,
                                     base_dir: str = "") -> str:
        tpl = template_mgr.load_template(template_name, base_dir) or {}
        base = (tpl.get("display_name")
                or tpl.get("name", "").split("/")[-1].replace("-", " ")
                or "Agent")
        base = " ".join(w.capitalize() for w in base.split()) or "Agent"
        existing = {
            a.name for a in state.agents.values()
            if a.group == group and a.cell_type == "agent"
        }
        if base not in existing:
            return base
        i = 2
        while f"{base} {i}" in existing:
            i += 1
        return f"{base} {i}"

    def _resolve_agent_launch_config(group: str, *,
                                     base_dir: str = "",
                                     explicit_template: str = "",
                                     overrides: dict | None = None) -> dict:
        gs = state.get_group_settings(group)
        resolved = template_mgr.resolve_agent_config(
            explicit_template, gs, overrides or {}, base_dir=base_dir)

        provider = resolved.get("provider", "") or gs.agent_provider
        raw_command = resolved.get("command", "") or gs.agent_boot_command
        command, agent_type = _resolve_provider_command(
            provider, raw_command, state.get_default_command())
        adapter = get_adapter(agent_type or provider)
        if not raw_command:
            command += adapter.resolve_model_flags(resolved.get("model", ""))
        max_turns = resolved.get("max_turns", 0)
        if max_turns:
            command += f" --max-turns {int(max_turns)}"
        permissions = resolved.get("permissions", "")
        if agent_type == "claude-code" and permissions:
            if permissions == "skip":
                command += " --dangerously-skip-permissions"
            else:
                command += f" --allowed-tools {shlex.quote(permissions)}"

        tab_color = resolved.get("tab_color", "")
        if tab_color == "none":
            tab_color = ""
        if not tab_color:
            _ac = gs.agent_tab_color
            if _ac == "none":
                tab_color = ""
            else:
                tab_color = _ac or gs.tab_color or ""

        directory = (resolved.get("directory", "")
                     or gs.agent_directory or gs.default_directory or "")
        profile = (resolved.get("profile", "")
                   or gs.agent_profile or gs.profile or "Default")
        shell = (resolved.get("shell", "")
                 or gs.agent_shell or gs.shell or "")
        env = {**gs.env_vars, **(resolved.get("env_vars") or {})} or None

        return {
            "provider": provider,
            "agent_type": agent_type,
            "command": command.strip(),
            "profile": profile,
            "directory": directory,
            "shell": shell,
            "tab_color": tab_color,
            "icon": resolved.get("icon", ""),
            "env_vars": env,
            "system_prompt": resolved.get("system_prompt", ""),
            "initial_prompt": resolved.get("initial_prompt", ""),
            "template": resolved.get("template", ""),
            "session_resume": resolved.get(
                "session_resume", gs.agent_session_resume),
            "idle_timeout": resolved.get(
                "idle_timeout", gs.agent_idle_timeout),
            "worktree": resolved.get("worktree", gs.git_worktree),
            "worktree_base_dir": resolved.get(
                "worktree_base_dir", gs.worktree_base_dir),
            "worktree_base_branch": resolved.get(
                "worktree_base_branch", gs.worktree_base_branch),
            "worktree_auto_checkpoint": resolved.get(
                "worktree_auto_checkpoint", gs.worktree_auto_checkpoint),
            "worktree_merge_squash": resolved.get(
                "worktree_merge_squash", gs.worktree_merge_squash),
            "terminals": resolved.get("terminals", []),
        }

    async def _create_child_terminals(group: str, parent_cell,
                                      terminals: list[dict] | None = None,
                                      count: int = 0):
        gs = state.get_group_settings(group)
        created = []
        if terminals:
            for tterm in terminals:
                t_name = tterm.get("name") or state.next_cell_name(
                    group, "terminal")
                t = state.add_terminal(
                    name=t_name,
                    group=group,
                    profile=gs.terminal_profile or gs.profile or "Default",
                    command=tterm.get("command") or "",
                    directory=tterm.get("directory") or parent_cell.directory,
                    tab_color=gs.terminal_tab_color or gs.tab_color or "",
                    parent_id=parent_cell.id,
                )
                if t:
                    await bridge.create_session(
                        t,
                        env_vars={**gs.env_vars, **gs.terminal_env_vars} or None,
                        init_script=tterm.get("init_script")
                        or gs.terminal_init_script,
                        shell=gs.terminal_shell or gs.shell or "",
                    )
                    created.append(t)
            return created

        if count <= 0:
            return created
        t_profile = gs.terminal_profile or gs.profile or "Default"
        t_dir = gs.terminal_directory or gs.default_directory or ""
        _ttc = gs.terminal_tab_color
        t_color = (_ttc if _ttc != "none" else "") or gs.tab_color or ""
        t_shell = gs.terminal_shell or gs.shell or ""
        t_env = {**gs.env_vars, **gs.terminal_env_vars} or None
        t_cmd = gs.terminal_boot_command or ""
        if gs.terminal_command_args and t_cmd:
            t_cmd = (t_cmd + " " + gs.terminal_command_args).strip()
        for _ in range(count):
            t_name = state.next_cell_name(group, "terminal")
            t = state.add_terminal(
                name=t_name,
                group=group,
                profile=t_profile,
                command=t_cmd,
                directory=t_dir or parent_cell.directory,
                tab_color=t_color,
                parent_id=parent_cell.id,
            )
            if t:
                await bridge.create_session(
                    t,
                    env_vars=t_env,
                    init_script=gs.terminal_init_script,
                    shell=t_shell,
                )
                created.append(t)
        return created

    async def _create_agent_with_config(group: str, name: str,
                                        launch_cfg: dict, *,
                                        explicit_template: str = "",
                                        target_session_id: str = "",
                                        target_window_id: str = ""):
        cell = state.add_agent(
            name=name, group=group,
            profile=launch_cfg["profile"],
            command=launch_cfg["command"],
            directory=launch_cfg["directory"],
            tab_color=launch_cfg["tab_color"],
            icon=launch_cfg.get("icon", ""),
        )
        if not cell:
            return None
        cell.session_resume = bool(launch_cfg.get("session_resume", True))
        cell.idle_timeout = int(launch_cfg.get("idle_timeout", 5) or 0)
        cell.worktree_base_dir = (
            launch_cfg.get("worktree_base_dir") or ".loom/worktrees")
        cell.worktree_auto_checkpoint = bool(
            launch_cfg.get("worktree_auto_checkpoint", False))
        cell.worktree_merge_squash = bool(
            launch_cfg.get("worktree_merge_squash", True))
        cell.template = explicit_template or launch_cfg.get("template", "")
        if launch_cfg.get("agent_type"):
            cell.agent_type = launch_cfg["agent_type"]
        state._emit_agent(cell)
        state._db_save_agent(cell)

        if launch_cfg.get("worktree") and cell.directory:
            repo_root = await worktree_mgr.get_repo_root(cell.directory)
            if repo_root:
                wt_path = await worktree_mgr.create(
                    cell, repo_root,
                    base_dir=cell.worktree_base_dir or ".loom/worktrees",
                    base_branch=launch_cfg.get("worktree_base_branch", ""),
                )
                if wt_path:
                    cell.directory = wt_path
                    state._emit_agent(cell)
                    state._db_save_agent(cell)

        await bridge.create_session(
            cell,
            env_vars=launch_cfg.get("env_vars"),
            shell=launch_cfg.get("shell", ""),
            system_prompt=launch_cfg.get("system_prompt", ""),
            target_session_id=target_session_id,
            target_window_id=target_window_id,
        )
        return cell

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

    async def _send_agent_prompt(cell, prompt: str, *,
                                 delay: float = 0,
                                 persist: bool = False,
                                 background: bool = False):
        """Send a prompt to an agent session, optionally delayed."""
        payload = prompt if prompt.endswith("\r") else prompt + "\r"

        async def _run():
            if delay:
                await asyncio.sleep(delay)
            if not cell.session_id:
                return
            await bridge.send_text(cell.session_id, payload)
            cell.status = "running"
            state._emit_agent(cell)
            if persist:
                state._db_save_agent(cell)
            await state.broadcast()

        if background:
            asyncio.create_task(_run())
        else:
            await _run()

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

        def _derive_line(tr):
            when = tr.get("when", "")
            desc = f" — {when}" if when else ""
            suffix = ""
            if tr.get("target") == "self":
                suffix = " (returns task inline — proceed immediately)"
            return (f"- `loom ai derive \"short title\" "
                    f"-d \"details\" "
                    f"-t {tr['action']}`{desc}{suffix}")

        has_transitions = any(
            isinstance(tr, dict) and tr.get("action")
            for tr in transitions)
        has_ask = any(
            isinstance(tr, dict) and tr.get("ask")
            for tr in transitions)

        mandate = ""
        if has_transitions or has_ask:
            mandate = (
                "\n\nIMPORTANT: When you are done, you MUST use one "
                "of the transition commands below. Do NOT ask the "
                "user directly — use `loom ai ask` instead so Loom "
                "can track it. Do NOT just stop — always signal "
                "completion via one of these commands.")

        if not is_clean:
            abbrev = ("\n\n---" + mandate)
            if has_transitions or has_ask:
                abbrev += "\nAvailable transitions:"
                for tr in transitions:
                    if isinstance(tr, dict) and tr.get("action"):
                        abbrev += "\n" + _derive_line(tr)
                if has_ask:
                    abbrev += ("\n- `loom ai ask \"title\" "
                              "-d \"details\"` "
                              "— pause for human input")
                abbrev += ("\n- `loom ai done` "
                           "— task complete, no follow-up")
            else:
                abbrev += ("\nUse `loom ai done` when finished, "
                           "or `loom ai blocked \"reason\"` "
                           "if stuck.")
            return abbrev

        lines = [
            mandate,
            "\nReport your progress with these commands "
            "(or use the equivalent loom_* MCP tools if available):",
            "- `loom ai done` — task complete, no follow-up needed",
        ]

        # Dynamic derive/ask lines from action transitions
        for tr in transitions:
            if isinstance(tr, dict) and tr.get("action"):
                lines.append(_derive_line(tr))
        if has_ask:
            lines.append(
                "- `loom ai ask \"title\" -d \"details\"` "
                "— pause for human input (creates a task in "
                "Backlog for review; -d is optional)")
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
            "title": task.task,
            "slug": task.slug,
            "description": task.description,
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
            resolved_defaults = template_mgr.resolve_agent_config(
                "", gs, {}, base_dir=current_path or await _resolve_base_dir(group))
            return {
                "type": "config",
                "profiles": profile_names,
                "current_path": current_path,
                "current_profile": current_profile,
                "group_cells": group_cells,
                "group_settings": asdict(gs),
                "resolved_agent_defaults": resolved_defaults,
                "providers": get_providers(),
                "templates": template_mgr.list_templates(current_path
                                                          or await _resolve_base_dir(group)),
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
                "resolved_agent_defaults": template_mgr.resolve_agent_config(
                    "", gs, {}, base_dir=await _resolve_base_dir(group)),
                "profiles": pnames,
                "providers": get_providers(),
                "templates": template_mgr.list_templates(
                    await _resolve_base_dir(group)),
            }

        # get_global_settings: respond directly
        if cmd == "get_global_settings":
            return {
                "type": "global_settings",
                "settings": asdict(state.global_settings),
                "keybinding_defaults": keybindings.get_default_bindings(),
            }

        # get_events: paginated event log query
        if cmd == "get_events":
            before_id = int(data.get("before_id", 0))
            limit = min(int(data.get("limit", 50)), 200)
            events = panel_log.get_page(limit=limit, before_id=before_id)
            return {"type": "events_page", "events": events}

        # list_actions: respond directly
        if cmd == "list_actions":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            actions = action_mgr.list_actions(base_dir)
            return {"type": "actions", "group": data.get("group", ""),
                    "actions": actions}

        if cmd == "list_templates":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            return {
                "type": "templates",
                "group": data.get("group", ""),
                "templates": template_mgr.list_templates(base_dir),
            }

        if cmd == "get_template":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            scope = data.get("scope", "")
            tpl = template_mgr.load_template(
                data.get("name", ""), base_dir, scope=scope)
            if not tpl:
                return {"type": "error",
                        "message": f"Template \"{data['name']}\" not found"}
            return {"type": "template_detail", "name": data["name"],
                    "template": tpl}

        if cmd == "save_template":
            name = data.get("name", "").strip()
            if not name:
                return {"type": "error", "message": "Template name required"}
            base_dir = await _resolve_base_dir(data.get("group", ""))
            scope = data.get("scope", "project")
            old_name = data.get("old_name", "").strip()
            if old_name and old_name != name:
                template_mgr.delete_template(old_name, base_dir=base_dir)
                template_mgr.delete_template(old_name, scope="user",
                                             base_dir=base_dir)
            template_mgr.save_template(
                name, data.get("template", {}), scope=scope, base_dir=base_dir)
            return {
                "type": "templates",
                "group": data.get("group", ""),
                "templates": template_mgr.list_templates(base_dir),
                "saved": name,
            }

        if cmd == "delete_template":
            name = data.get("name", "").strip()
            if not name:
                return {"type": "error", "message": "Template name required"}
            base_dir = await _resolve_base_dir(data.get("group", ""))
            deleted = template_mgr.delete_template(
                name, scope=data.get("scope", ""), base_dir=base_dir)
            if not deleted:
                return {"type": "error",
                        "message": f"Template \"{name}\" not found"}
            return {
                "type": "templates",
                "group": data.get("group", ""),
                "templates": template_mgr.list_templates(base_dir),
                "deleted": name,
            }

        if cmd == "render_template":
            base_dir = await _resolve_base_dir(data.get("group", ""))
            group = data.get("group", "")
            gs = state.get_group_settings(group)
            rendered = template_mgr.resolve_agent_config(
                data.get("name", ""), gs, data.get("overrides", {}),
                base_dir=base_dir)
            return {
                "type": "template_rendered",
                "name": data.get("name", ""),
                "config": rendered,
            }

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
                # Propagate max_event_log to panel log
                new_max = state.global_settings.max_event_log
                if panel_log._max_size != new_max:
                    panel_log._max_size = new_max
                    panel_log._events = deque(
                        panel_log._events, maxlen=new_max)
                    if panel_log._db:
                        panel_log._db.trim_panel_events(new_max)

            elif cmd == "suspend_keybindings":
                await keybindings.remove(connection, _displaced[0])

            elif cmd == "resume_keybindings":
                _kb_overrides = (state.global_settings.keybindings
                                 or None)
                _displaced[0] = await keybindings.reinstall(
                    connection, _displaced[0],
                    overrides=_kb_overrides)

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
                        if hasattr(adapter, "uninstall_mcp_config"):
                            adapter.uninstall_mcp_config(
                                os.path.expanduser(c.directory))
                    event_bus.cleanup_cell(c.id)
                    await _safe_remove_worktree(c)

            elif cmd == "rename_group":
                state.rename_group(data["group"], data["new_name"])

            elif cmd == "add_agent":
                group = data["group"]
                base_dir = await _resolve_base_dir(group)
                explicit_template = data.get("template", "").strip()
                launch_cfg = _resolve_agent_launch_config(
                    group,
                    base_dir=base_dir,
                    explicit_template=explicit_template,
                    overrides=data,
                )
                name = (data.get("name", "") or "").strip()
                if not name:
                    if explicit_template:
                        name = _suggest_template_agent_name(
                            group, explicit_template, base_dir)
                    else:
                        name = state.next_cell_name(group, "agent")
                cell = await _create_agent_with_config(
                    group, name, launch_cfg,
                    explicit_template=explicit_template)
                if cell:
                    if launch_cfg.get("terminals"):
                        await _create_child_terminals(
                            group, cell, terminals=launch_cfg["terminals"])
                    else:
                        gs = state.get_group_settings(group)
                        if gs.auto_terminals > 0:
                            await _create_child_terminals(
                                group, cell, count=gs.auto_terminals)
                    if launch_cfg.get("initial_prompt") and cell.session_id:
                        await _send_agent_prompt(
                            cell, launch_cfg["initial_prompt"],
                            background=True)

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

                    # Use rendered values, falling through to group settings
                    name = data.get("name") or rendered["name"]
                    explicit_template = rendered.get("agent_template", "")
                    launch_cfg = _resolve_agent_launch_config(
                        group,
                        base_dir=base_dir,
                        explicit_template=explicit_template,
                        overrides=rendered,
                    )
                    cell = await _create_agent_with_config(
                        group, name, launch_cfg,
                        explicit_template=explicit_template)
                    if cell:
                        await _create_child_terminals(
                            group, cell, terminals=rendered.get("terminals"))

                        # Use the rendered prompt directly
                        prompt = rendered.get("prompt", "")

                        if launch_cfg.get("initial_prompt") and cell.session_id:
                            await _send_agent_prompt(
                                cell, launch_cfg["initial_prompt"])
                        if prompt and cell.session_id:
                            await _send_agent_prompt(
                                cell, prompt, persist=True,
                                background=True)

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
                    # Clean up hooks and MCP config
                    if c.agent_type and c.directory:
                        adapter = get_adapter(c.agent_type)
                        if hasattr(adapter, "uninstall_hooks"):
                            adapter.uninstall_hooks(
                                os.path.expanduser(c.directory))
                        if hasattr(adapter, "uninstall_mcp_config"):
                            adapter.uninstall_mcp_config(
                                os.path.expanduser(c.directory))
                    # Clean up event bus state
                    event_bus.cleanup_cell(c.id)
                    await _safe_remove_worktree(c)

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
                    base_dir = cell.worktree_repo_root or cell.directory \
                        or await _resolve_base_dir(cell.group)
                    launch_cfg = _resolve_agent_launch_config(
                        cell.group,
                        base_dir=base_dir,
                        explicit_template=cell.template,
                        overrides={},
                    )
                    cell.session_resume = bool(
                        launch_cfg.get("session_resume", cell.session_resume))
                    cell.idle_timeout = int(
                        launch_cfg.get("idle_timeout", cell.idle_timeout) or 0)
                    if cell.cell_type == "agent":
                        cell.command = launch_cfg.get("command", cell.command)
                        cell.profile = launch_cfg.get("profile", cell.profile)
                        cell.tab_color = launch_cfg.get(
                            "tab_color", cell.tab_color)
                        cell.icon = launch_cfg.get("icon", cell.icon)
                        cell.agent_type = launch_cfg.get(
                            "agent_type", cell.agent_type)
                        if not cell.worktree_path:
                            cell.directory = launch_cfg.get(
                                "directory", cell.directory)
                    cell.worktree_base_dir = (
                        launch_cfg.get("worktree_base_dir")
                        or cell.worktree_base_dir
                        or ".loom/worktrees")
                    cell.worktree_auto_checkpoint = bool(
                        launch_cfg.get(
                            "worktree_auto_checkpoint",
                            cell.worktree_auto_checkpoint))
                    cell.worktree_merge_squash = bool(
                        launch_cfg.get(
                            "worktree_merge_squash",
                            cell.worktree_merge_squash))
                    state._emit_agent(cell)
                    state._db_save_agent(cell)
                    if cell.cell_type == "terminal":
                        env = {**gs.env_vars, **gs.terminal_env_vars} or None
                        shell = gs.terminal_shell or gs.shell or ""
                        init = gs.terminal_init_script
                    else:
                        env = launch_cfg.get("env_vars")
                        shell = launch_cfg.get("shell", "")
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
                        if not cell.worktree_path and launch_cfg.get("worktree") \
                                and cell.directory:
                            repo_root = await worktree_mgr.get_repo_root(
                                cell.directory)
                            if repo_root:
                                wt_path = await worktree_mgr.create(
                                    cell, repo_root,
                                    base_dir=cell.worktree_base_dir
                                        or ".loom/worktrees",
                                    base_branch=launch_cfg.get(
                                        "worktree_base_branch", "")
                                        or "")
                                if wt_path:
                                    cell.directory = wt_path
                                    state._emit_agent(cell)
                                    state._db_save_agent(cell)
                    await bridge.create_session(
                        cell, env_vars=env,
                        init_script=init, shell=shell,
                        system_prompt=launch_cfg.get("system_prompt", ""))

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
                            base_dir=cell.worktree_base_dir
                                or ".loom/worktrees",
                            base_branch=cell.worktree_base_branch
                                or gs.worktree_base_branch or "")
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
                                base_dir = cell.worktree_repo_root \
                                    or cell.directory \
                                    or await _resolve_base_dir(cell.group)
                                launch_cfg = _resolve_agent_launch_config(
                                    cell.group,
                                    base_dir=base_dir,
                                    explicit_template=cell.template,
                                    overrides={},
                                )
                                await bridge.create_session(
                                    cell,
                                    env_vars=launch_cfg.get("env_vars"),
                                    shell=launch_cfg.get("shell", ""),
                                    system_prompt=launch_cfg.get(
                                        "system_prompt", ""),
                                    target_session_id=data.get(
                                        "target_session_id", ""),
                                    target_window_id=data.get(
                                        "target_window_id", ""))

            elif cmd == "worktree_remove":
                cell = state.agents.get(data["id"])
                if cell and cell.worktree_path:
                    # Restore directory to original repo root
                    repo_root = cell.worktree_repo_root
                    await _safe_remove_worktree(cell)
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
                        base_dir = cell.worktree_repo_root or cell.directory \
                            or await _resolve_base_dir(cell.group)
                        launch_cfg = _resolve_agent_launch_config(
                            cell.group,
                            base_dir=base_dir,
                            explicit_template=cell.template,
                            overrides={},
                        )
                        await bridge.create_session(
                            cell,
                            env_vars=launch_cfg.get("env_vars"),
                            shell=launch_cfg.get("shell", ""),
                            system_prompt=launch_cfg.get("system_prompt", ""))

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

            elif cmd == "worktree_create_pr":
                cell = state.agents.get(data.get("id", ""))
                if not cell or not cell.worktree_path:
                    result = {"type": "worktree_pr",
                              "error": "Agent has no worktree."}
                else:
                    # Build PR title from linked task or agent name
                    title = data.get("title", "")
                    if not title:
                        for t in state.board_tasks.values():
                            if t.agent_id == cell.id:
                                title = t.task
                                break
                    if not title:
                        title = cell.name
                    pr_result = await worktree_mgr.create_pr(
                        cell, title=title)
                    if "error" in pr_result:
                        result = {"type": "worktree_pr",
                                  "error": pr_result["error"]}
                    else:
                        msg = ("PR already exists"
                               if pr_result.get("existing")
                               else "PR created")
                        result = {"type": "worktree_pr",
                                  "url": pr_result["url"],
                                  "message": msg}

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
                        squash = cell.worktree_merge_squash
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
                        clear_flag = bool(data.get("clear_context"))
                        await bridge.send_text(
                            cell.session_id, prompt + "\r")
                        _pending_merges[cell.id] = (
                            pre_sha, close_flag, clear_flag)
                        cell.status = "running"
                        state._emit_agent(cell)
                        # Ephemeral status — no DB write needed

            # -- Board commands (Phase 5) --
            elif cmd == "board_add_task":
                bt = state.board_add_task(
                    task=data.get("task", ""),
                    group=data.get("group", ""),
                    lane=data.get("lane", ""),
                    description=data.get("description", ""),
                    action_name=data.get("action_name", ""),
                    action_vars=data.get("action_vars", {}),
                    agent_template=data.get("agent_template", ""),
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
                        base_dir = await _resolve_base_dir(group)
                        act_meta = action_mgr.load_action(
                            task.action_name, base_dir) \
                            if task.action_name else None
                        action_template = ""
                        if isinstance(act_meta, dict):
                            raw_agent = act_meta.get("agent", "")
                            if isinstance(raw_agent, str):
                                action_template = raw_agent
                        explicit_template = task.agent_template or action_template
                        agent_id = data.get("agent_id", "")
                        if agent_id:
                            # Dispatch to existing agent
                            cell = state.agents.get(agent_id)
                            if not cell:
                                result = {"type": "error",
                                          "message": "Agent not found"}
                            elif cell.current_task_id \
                                    and cell.current_task_id != tid:
                                # Agent is busy — queue the task
                                active = state.board_tasks.get(
                                    cell.current_task_id)
                                if active and active.lane != "Done":
                                    state.board_update_task(
                                        tid, agent_id=cell.id)
                                    state.board_move_task(tid, "To Do")
                                    _panel_event(
                                        "task_queued", cell.id,
                                        cell.name, cell.group,
                                        task.task[:80],
                                        task_id=tid)
                                    result = {
                                        "type": "queued",
                                        "task_id": tid,
                                        "agent_id": cell.id}
                                    cell = None  # skip dispatch below
                        elif data.get("create_agent"):
                            # Create a new agent
                            from loom.state import _slugify
                            slug = _slugify(task.task)
                            agent_name = slug or "agent"
                            launch_cfg = _resolve_agent_launch_config(
                                group,
                                base_dir=base_dir,
                                explicit_template=explicit_template,
                                overrides={},
                            )
                            cell = await _create_agent_with_config(
                                group, agent_name, launch_cfg,
                                explicit_template=explicit_template,
                                target_session_id=data.get(
                                    "target_session_id", ""),
                                target_window_id=data.get(
                                    "target_window_id", ""),
                            )
                            if cell:
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

                                if launch_cfg.get("terminals"):
                                    await _create_child_terminals(
                                        group, cell,
                                        terminals=launch_cfg["terminals"])

                                # Auto-create child terminals (off by default for dispatch)
                                gs = state.get_group_settings(group)
                                if gs.dispatch_auto_terminals \
                                        and gs.auto_terminals > 0:
                                    await _create_child_terminals(
                                        group, cell, count=gs.auto_terminals)

                        if cell:
                            # Link task to agent and move to In Progress
                            dispatch_lane = \
                                state.get_group_settings(group) \
                                    .dispatch_lane or "In Progress"
                            state.board_update_task(
                                tid, agent_id=cell.id,
                                lane=dispatch_lane)
                            cell.current_task_id = tid

                            # Build loom context for template rendering
                            loom_ctx = _build_loom_context(cell, task)

                            # Compose prompt: action-aware
                            prompt = None
                            base_dir = ""
                            if task.action_name \
                                    and not data.get("force_no_action"):
                                base_dir = cell.worktree_repo_root \
                                    or cell.directory \
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
                                final_prompt = prompt
                                final_prompt += _build_postscript(
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
                                        await _send_agent_prompt(
                                            cell, final_prompt,
                                            delay=delay,
                                            background=True)
                                    else:
                                        # Existing agent — send now
                                        await _send_agent_prompt(cell, final_prompt)
                                elif data.get("create_agent") \
                                        and cell.session_id:
                                    if launch_cfg.get("initial_prompt"):
                                        await _send_agent_prompt(
                                            cell, launch_cfg["initial_prompt"])
                                    await _send_agent_prompt(
                                        cell, final_prompt, background=True)

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

                task_desc = data.get("description", "")
                if tid and not task_desc:
                    t = state.board_tasks.get(tid)
                    if t:
                        task_desc = t.description or ""

                if act_name:
                    base_dir = await _resolve_base_dir(act_group)
                    loom_ctx = {
                        **LOOM_CONTEXT_STUB,
                        "task": {
                            **LOOM_CONTEXT_STUB["task"],
                            "title": task_text,
                            "description": task_desc,
                            "group": act_group,
                        },
                    }
                    rendered = action_mgr.render_prompt(
                        act_name,
                        {"TASK": task_text, **avars},
                        base_dir=base_dir,
                        loom_context=loom_ctx)
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
                    # Resolve task: explicit ID → current_task_id →
                    # auto-detect from cell's active tasks.
                    task = None
                    if task_id:
                        task = state.board_tasks.get(task_id)
                    elif cell.current_task_id:
                        task = state.board_tasks.get(
                            cell.current_task_id)
                    if not task and not task_id:
                        linked = [
                            t for t in state.board_tasks.values()
                            if t.agent_id == cell_id
                            and t.lane not in ("Done", "Backlog")]
                        if len(linked) == 1:
                            task = linked[0]

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

                    def _append_mcp(c, act, msg=""):
                        """Append an MCP message to the cell log."""
                        c.mcp_messages.insert(0, {
                            "action": act,
                            "message": msg,
                            "timestamp": time.time(),
                        })
                        if len(c.mcp_messages) > 20:
                            c.mcp_messages[:] = c.mcp_messages[:20]

                    async def _auto_dispatch_next(c):
                        """Pick the next queued task for this agent."""
                        queued = sorted(
                            [t for t in state.board_tasks.values()
                             if t.agent_id == c.id
                             and t.lane == "To Do"],
                            key=lambda t: t.position)
                        if not queued:
                            return
                        nxt = queued[0]
                        _panel_event(
                            "task_auto_dispatched", c.id,
                            c.name, c.group,
                            nxt.task[:80], task_id=nxt.id)
                        await state.broadcast()
                        await handle_command({
                            "cmd": "dispatch_task",
                            "id": nxt.id,
                            "agent_id": c.id})

                    if result and result.get("type") == "error":
                        pass  # auto-resolve failed; skip action

                    elif action == "done":
                        cell.activity = ""
                        cell.activity_detail = ""
                        cell.needs_attention = False
                        cell.error_message = ""
                        if message:
                            cell.last_summary = message
                        cell.current_task_id = ""
                        _append_mcp(cell, "done", message or "Done")
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
                        await _auto_dispatch_next(cell)

                    elif action == "blocked":
                        cell.needs_attention = True
                        cell.activity = "waiting"
                        cell.activity_detail = message
                        _append_mcp(cell, "blocked", message)
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
                        _append_mcp(cell, "error", message)
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
                        _append_mcp(cell, "progress", message)
                        state._emit_agent(cell)
                        # Panel event — replace last progress
                        # for this agent to avoid flooding
                        pe = panel_log.replace_last(
                            "agent_progress", cell.id,
                            agent_name=cell.name,
                            group=cell.group,
                            message=message)
                        state._emit("event_append", **pe)

                    elif action == "ready":
                        cell.activity = ""
                        cell.activity_detail = "ready"
                        cell.needs_attention = False
                        cell.error_message = ""
                        cell.current_task_id = ""
                        _append_mcp(cell, "ready", "Ready")
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
                        await _auto_dispatch_next(cell)

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
                                    derive_desc = data.get(
                                        "description", "")
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
                                        description=derive_desc,
                                    )
                                    if new_task:
                                        _append_mcp(
                                            cell, "derive",
                                            message[:80])
                                        state._emit_agent(cell)
                                        _panel_event(
                                            "task_derived",
                                            cell.id, cell.name,
                                            cell.group,
                                            message[:80],
                                            task_id=new_task.id)
                                        # Determine dispatch target
                                        # Enforce transition's declared
                                        # target — ignore --self/--agent
                                        # if the transition specifies a
                                        # different target
                                        tr_target = ""
                                        if cur_transitions and act_name:
                                            for tr in cur_transitions:
                                                if isinstance(tr, dict) \
                                                        and tr.get(
                                                            "action"
                                                        ) == act_name:
                                                    tr_target = tr.get(
                                                        "target", "")
                                                    break
                                        if tr_target == "self":
                                            reuse_self = True
                                            target_agent = ""
                                        elif tr_target == "parent":
                                            reuse_self = False
                                            pt = state.board_tasks.get(
                                                task.parent_task_id) \
                                                if task.parent_task_id \
                                                else None
                                            if pt and pt.agent_id:
                                                a = state.agents.get(
                                                    pt.agent_id)
                                                if a:
                                                    target_agent = \
                                                        a.slug or a.name
                                        elif tr_target == "root":
                                            reuse_self = False
                                            rid = task.pipeline_root_id \
                                                or task.id
                                            rt = state.board_tasks.get(
                                                rid)
                                            if rt and rt.agent_id:
                                                a = state.agents.get(
                                                    rt.agent_id)
                                                if a:
                                                    target_agent = \
                                                        a.slug or a.name
                                        elif tr_target == "" \
                                                and cur_transitions:
                                            # No target declared — force
                                            # new agent even if agent
                                            # passed --self/--agent
                                            reuse_self = False
                                            target_agent = ""
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
                                        elif target_id == cell.id:
                                            # Self-dispatch: link task
                                            # inline, skip prompt render
                                            # and send_text entirely
                                            dl = state.get_group_settings(
                                                new_task.group
                                            ).dispatch_lane \
                                                or "In Progress"
                                            state.board_update_task(
                                                new_task.id,
                                                agent_id=cell.id,
                                                lane=dl)
                                            cell.current_task_id = \
                                                new_task.id
                                            cell.tasks_dispatched += 1
                                            state._emit_agent(cell)
                                            state._db_save_agent(cell)
                                            _panel_event(
                                                "task_dispatched",
                                                cell.id, cell.name,
                                                cell.group,
                                                new_task.task[:80],
                                                task_id=new_task.id)
                                            await state.broadcast()
                                            result = {
                                                "type": "ok",
                                                "task_id":
                                                    new_task.id,
                                                "agent_id":
                                                    cell.id,
                                                "proceed": True,
                                                "task":
                                                    new_task.task,
                                                "description":
                                                    new_task.description
                                                    or ""}
                                        elif target_id:
                                            # Dispatch to different
                                            # existing agent
                                            tgt = state.agents.get(
                                                target_id)
                                            dispatch_data = {
                                                "cmd": "dispatch_task",
                                                "id": new_task.id,
                                                "agent_id": target_id,
                                            }
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
                                            if cell.session_id:
                                                dispatch_data[
                                                    "target_session_id"
                                                ] = cell.session_id
                                            if cell.window_id:
                                                dispatch_data[
                                                    "target_window_id"
                                                ] = cell.window_id
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
                            _append_mcp(cell, "ask", message)
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
                            ask_desc = data.get(
                                "description", "")
                            new_task = state.board_add_task(
                                task=message,
                                group=grp,
                                lane="Backlog",
                                labels=["human", "derived"],
                                parent_task_id=task.id,
                                pipeline_depth=
                                    task.pipeline_depth + 1,
                                pipeline_root_id=root_id,
                                description=ask_desc,
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
                            _append_mcp(cell, "name", message)
                            state.update_agent(cell.id,
                                               name=message)
                            if cell.session_id:
                                await bridge.update_session(
                                    cell, old_name)
                            _panel_event(
                                "agent_renamed", cell.id,
                                cell.name, cell.group,
                                f"{old_name} \u2192 {cell.name}")
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
                    except Exception:
                        log.exception("Error handling WS command")
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
    app_server.router.add_post("/mcp", create_mcp_handler(handle_command, state))
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
