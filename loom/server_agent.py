"""Agent launch, prompt bootstrapping, and session helper utilities."""

from __future__ import annotations

import asyncio
import os
import shlex
from typing import Any

from .adapters import (
    detect_by_command,
    get_adapter,
    get_default_command_for_provider,
)
from .artifacts import artifact_prompt_block, legacy_image_prompt_block


def _append_task_artifacts(prompt: str, attachments, artifacts) -> str:
    """Append legacy image references plus structured artifact references."""
    final_prompt = prompt
    final_prompt += legacy_image_prompt_block(attachments, artifacts)
    final_prompt += artifact_prompt_block(artifacts)
    return final_prompt


def _build_self_dispatch_prompt(shared_context_block: str = "") -> str:
    """Return the minimal follow-up prompt for derive-to-self."""
    prompt = "Proceed with the derived task you just created."
    if shared_context_block:
        prompt += shared_context_block
    return prompt


def _startup_prompt_for_new_agent(*, agent_type: str = "",
                                  persistent_prompt_text: str = "",
                                  is_weaver: bool = False) -> str:
    """Return the first interactive prompt for a newly created agent."""
    if not persistent_prompt_text:
        return ""
    if is_weaver:
        return persistent_prompt_text
    adapter = get_adapter(agent_type) if agent_type else None
    if not adapter:
        return ""
    return adapter.startup_prompt_from_persistent_prompt(
        persistent_prompt_text
    )


def _new_agent_prompt_sequence(launch_cfg: dict, *,
                               startup_prompt: str = "",
                               final_prompt: str = "") -> list[tuple[str, dict]]:
    """Return prompts to send to a brand-new agent in order."""
    prompts = []
    if startup_prompt:
        prompts.append((startup_prompt, {}))
    initial_prompt = launch_cfg.get("initial_prompt", "")
    if initial_prompt:
        prompts.append((initial_prompt, {}))
    if final_prompt:
        prompts.append((final_prompt, {"background": True}))
    return prompts


class AgentLaunchService:
    """Shared runtime for agent-launch configuration and prompt delivery."""

    def __init__(self, *, state, connection, bridge, worktree_mgr,
                 template_mgr):
        self.state = state
        self.connection = connection
        self.bridge = bridge
        self.worktree_mgr = worktree_mgr
        self.template_mgr = template_mgr
        self._background_prompt_tasks: set[asyncio.Task] = set()

    def _runtime_terminal_backend(self) -> str:
        return "pty" if self.bridge.capabilities.supports_embedded_terminal else "iterm2"

    async def resolve_base_dir(self, group: str = "") -> str:
        """Resolve a base directory for action and template discovery."""
        if group:
            gs = self.state.get_group_settings(group)
            directory = gs.agent_directory or gs.default_directory
            if directory:
                return os.path.expanduser(directory)
        try:
            ctx = await self.bridge.get_launch_context()
            if ctx.current_path:
                return ctx.current_path
        except Exception:
            pass
        return ""

    @staticmethod
    def resolve_provider_command(
        provider: str, boot_command: str, default_command: str,
    ) -> tuple[str, str]:
        """Resolve ``(command, agent_type)`` from provider + boot command."""
        if provider:
            adapter_cmd = get_default_command_for_provider(provider)
            if adapter_cmd:
                return (boot_command or adapter_cmd, provider)
        return (boot_command or default_command, "")

    def suggest_template_agent_name(self, group: str, template_name: str,
                                    base_dir: str = "") -> str:
        """Return a unique display name for a template-launched agent."""
        template = self.template_mgr.load_template(template_name, base_dir) or {}
        base = (
            template.get("display_name")
            or template.get("name", "").split("/")[-1].replace("-", " ")
            or "Agent"
        )
        base = " ".join(word.capitalize() for word in base.split()) or "Agent"
        existing = {
            cell.name for cell in self.state.agents.values()
            if cell.group == group and cell.cell_type == "agent"
        }
        if base not in existing:
            return base
        index = 2
        while f"{base} {index}" in existing:
            index += 1
        return f"{base} {index}"

    def resolve_agent_launch_config(self, group: str, *,
                                    base_dir: str = "",
                                    explicit_template: str = "",
                                    overrides: dict[str, Any] | None = None) -> dict:
        """Resolve the effective launch configuration for an agent."""
        gs = self.state.get_group_settings(group)
        resolved = self.template_mgr.resolve_agent_config(
            explicit_template, gs, overrides or {}, base_dir=base_dir
        )

        provider = resolved.get("provider", "") or gs.agent_provider
        raw_command = resolved.get("command", "") or gs.agent_boot_command
        command, agent_type = self.resolve_provider_command(
            provider, raw_command, self.state.get_default_command()
        )
        detected = detect_by_command(command) if command else None
        effective_agent_type = agent_type or provider or (
            detected.name if detected else ""
        )
        adapter = get_adapter(effective_agent_type)
        if not raw_command:
            command += adapter.resolve_model_flags(resolved.get("model", ""))
            command += adapter.resolve_reasoning_effort_flags(
                resolved.get("reasoning_effort", "")
            )
        max_turns = resolved.get("max_turns", 0)
        if max_turns:
            command += f" --max-turns {int(max_turns)}"
        permissions = resolved.get("permissions", "")
        if effective_agent_type == "claude-code" and permissions:
            if permissions == "skip":
                command += " --dangerously-skip-permissions"
            else:
                command += f" --allowed-tools {shlex.quote(permissions)}"

        tab_color = resolved.get("tab_color", "")
        if tab_color == "none":
            tab_color = ""
        if not tab_color:
            agent_tab_color = gs.agent_tab_color
            if agent_tab_color == "none":
                tab_color = ""
            else:
                tab_color = agent_tab_color or gs.tab_color or ""

        directory = (
            resolved.get("directory", "")
            or gs.agent_directory
            or gs.default_directory
            or ""
        )
        profile = (
            resolved.get("profile", "")
            or gs.agent_profile
            or gs.profile
            or "Default"
        )
        shell = (
            resolved.get("shell", "")
            or gs.agent_shell
            or gs.shell
            or ""
        )
        env = {**gs.env_vars, **(resolved.get("env_vars") or {})} or None
        env_file = (
            resolved.get("env_file", "")
            or gs.agent_env_file
            or gs.env_file
        )

        return {
            "provider": provider,
            "agent_type": effective_agent_type,
            "command": command.strip(),
            "profile": profile,
            "directory": directory,
            "shell": shell,
            "tab_color": tab_color,
            "icon": resolved.get("icon", ""),
            "env_vars": env,
            "env_file": env_file,
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
            "worktree_name": resolved.get("worktree_name", ""),
            "worktree_auto_checkpoint": resolved.get(
                "worktree_auto_checkpoint", gs.worktree_auto_checkpoint),
            "checkpoint_on_progress": resolved.get(
                "checkpoint_on_progress", gs.checkpoint_on_progress),
            "worktree_merge_squash": resolved.get(
                "worktree_merge_squash", gs.worktree_merge_squash),
            "worktree_symlinks": resolved.get(
                "worktree_symlinks", gs.worktree_symlinks),
            "terminals": resolved.get("terminals", []),
        }

    def resolve_weaver_launch_config(self, group: str, *,
                                     base_dir: str = "",
                                     explicit_template: str = "",
                                     overrides: dict[str, Any] | None = None) -> dict:
        """Resolve launch config for the designated Weaver in a group."""
        merged = dict(overrides or {})
        ws = self.state.get_weaver_settings(group)
        if getattr(ws, "weaver_provider", ""):
            merged["provider"] = ws.weaver_provider
        if getattr(ws, "weaver_boot_command", ""):
            merged["command"] = ws.weaver_boot_command
        if getattr(ws, "weaver_model", ""):
            merged["model"] = ws.weaver_model
        if getattr(ws, "weaver_reasoning_effort", ""):
            merged["reasoning_effort"] = ws.weaver_reasoning_effort
        resolved = self.resolve_agent_launch_config(
            group,
            base_dir=base_dir,
            explicit_template=explicit_template,
            overrides=merged,
        )
        resolved["worktree"] = False
        return resolved

    async def create_child_terminals(self, group: str, parent_cell,
                                     terminals: list[dict] | None = None,
                                     count: int = 0):
        """Create child terminals using group defaults or explicit specs."""
        gs = self.state.get_group_settings(group)
        created = []
        if terminals:
            for terminal_spec in terminals:
                term_name = terminal_spec.get("name") or self.state.next_cell_name(
                    group, "terminal"
                )
                terminal = self.state.add_terminal(
                    name=term_name,
                    group=group,
                    terminal_backend=self._runtime_terminal_backend(),
                    profile=gs.terminal_profile or gs.profile or "Default",
                    command=terminal_spec.get("command") or "",
                    directory=terminal_spec.get("directory")
                    or parent_cell.directory,
                    tab_color=gs.terminal_tab_color or gs.tab_color or "",
                    parent_id=parent_cell.id,
                )
                if terminal:
                    await self.bridge.create_session(
                        terminal,
                        env_vars={**gs.env_vars, **gs.terminal_env_vars} or None,
                        env_file=gs.terminal_env_file or gs.env_file,
                        init_script=terminal_spec.get("init_script")
                        or gs.terminal_init_script,
                        shell=gs.terminal_shell or gs.shell or "",
                    )
                    created.append(terminal)
            return created

        if count <= 0:
            return created
        profile = gs.terminal_profile or gs.profile or "Default"
        directory = gs.terminal_directory or gs.default_directory or ""
        terminal_tab_color = gs.terminal_tab_color
        tab_color = (
            terminal_tab_color if terminal_tab_color != "none" else ""
        ) or gs.tab_color or ""
        shell = gs.terminal_shell or gs.shell or ""
        env = {**gs.env_vars, **gs.terminal_env_vars} or None
        command = gs.terminal_boot_command or ""
        if gs.terminal_command_args and command:
            command = (command + " " + gs.terminal_command_args).strip()
        for _ in range(count):
            term_name = self.state.next_cell_name(group, "terminal")
            terminal = self.state.add_terminal(
                name=term_name,
                group=group,
                terminal_backend=self._runtime_terminal_backend(),
                profile=profile,
                command=command,
                directory=directory or parent_cell.directory,
                tab_color=tab_color,
                parent_id=parent_cell.id,
            )
            if terminal:
                await self.bridge.create_session(
                    terminal,
                    env_vars=env,
                    env_file=gs.terminal_env_file or gs.env_file,
                    init_script=gs.terminal_init_script,
                    shell=shell,
                )
                created.append(terminal)
        return created

    @staticmethod
    def persistent_prompt_filename(cell) -> str:
        """Return the stable persistent prompt filename for an agent."""
        return f"loom-system-prompt-{cell.id}.md"

    def apply_persistent_prompt(self, cell, launch_cfg: dict,
                                prompt_text: str = "") -> None:
        """Inject adapter-managed persistent prompt flags into launch config."""
        agent_type = launch_cfg.get("agent_type", "") or cell.agent_type
        if not prompt_text or not agent_type:
            return
        adapter = get_adapter(agent_type)
        if not adapter or adapter.name == "generic":
            return
        working_dir = os.path.expanduser(
            cell.directory or launch_cfg.get("directory", "") or os.getcwd()
        )
        prompt_flags = adapter.inject_persistent_prompt(
            working_dir, self.persistent_prompt_filename(cell), prompt_text
        )
        if prompt_flags:
            launch_cfg["command"] = (
                launch_cfg.get("command", "") + prompt_flags
            ).strip()
        launch_cfg["system_prompt"] = ""
        cell.command = launch_cfg.get("command", cell.command)

    async def create_agent_with_config(self, group: str, name: str,
                                       launch_cfg: dict, *,
                                       explicit_template: str = "",
                                       target_session_id: str = "",
                                       target_window_id: str = "",
                                       persistent_prompt_text: str = "",
                                       restore_focus_to_prev_tab: bool = False):
        """Create an agent cell, prepare its worktree, and open the session."""
        cell = self.state.add_agent(
            name=name,
            group=group,
            terminal_backend=self._runtime_terminal_backend(),
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
            launch_cfg.get("worktree_base_dir") or ".loom/worktrees"
        )
        cell.worktree_auto_checkpoint = bool(
            launch_cfg.get("worktree_auto_checkpoint", False)
        )
        cell.checkpoint_on_progress = bool(
            launch_cfg.get("checkpoint_on_progress", False)
        )
        cell.worktree_merge_squash = bool(
            launch_cfg.get("worktree_merge_squash", True)
        )
        cell.template = explicit_template or launch_cfg.get("template", "")
        if launch_cfg.get("agent_type"):
            cell.agent_type = launch_cfg["agent_type"]
        self.state._emit_agent(cell)
        self.state._db_save_agent(cell)
        self.state.history_record_agent(cell)

        if launch_cfg.get("worktree") and cell.directory:
            repo_root = await self.worktree_mgr.get_repo_root(cell.directory)
            if repo_root:
                worktree_path = await self.worktree_mgr.create(
                    cell,
                    repo_root,
                    base_dir=cell.worktree_base_dir or ".loom/worktrees",
                    base_branch=launch_cfg.get("worktree_base_branch", ""),
                    symlinks=launch_cfg.get("worktree_symlinks", []),
                    worktree_name=launch_cfg.get("worktree_name", ""),
                )
                if worktree_path:
                    cell.directory = worktree_path
                    self.state._emit_agent(cell)
                    self.state._db_save_agent(cell)
                    self.state.history_update_agent(
                        cell, worktree_branch=cell.worktree_branch
                    )

        self.apply_persistent_prompt(cell, launch_cfg, persistent_prompt_text)
        self.state._emit_agent(cell)
        self.state._db_save_agent(cell)

        await self.bridge.create_session(
            cell,
            env_vars=launch_cfg.get("env_vars"),
            env_file=launch_cfg.get("env_file", ""),
            shell=launch_cfg.get("shell", ""),
            system_prompt=launch_cfg.get("system_prompt", ""),
            target_session_id=target_session_id,
            target_window_id=target_window_id,
            restore_focus_to_prev_tab=restore_focus_to_prev_tab,
        )
        return cell

    async def send_agent_prompt(self, cell, prompt: str, *,
                                delay: float = 0,
                                persist: bool = False,
                                background: bool = False):
        """Send a prompt to an agent session, optionally delayed."""
        payload = prompt if prompt.endswith("\r") else prompt + "\r"

        async def _run():
            try:
                if delay:
                    await asyncio.sleep(delay)
                if not cell.session_id:
                    return
                await self.bridge.send_text(cell.session_id, payload)
                cell.status = "running"
                self.state._emit_agent(cell)
                if persist:
                    self.state._db_save_agent(cell)
                await self.state.broadcast()
            finally:
                if task_ref is not None:
                    self._background_prompt_tasks.discard(task_ref)

        if background:
            task_ref = asyncio.create_task(_run())
            self._background_prompt_tasks.add(task_ref)
            return task_ref
        else:
            task_ref = None
            await _run()
