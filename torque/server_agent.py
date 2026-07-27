"""Agent launch, prompt bootstrapping, and session helper utilities."""

from __future__ import annotations

import asyncio
import os
import shlex
import time
from typing import Any

from . import config as torque_config
from .adapters import (
    detect_by_command,
    get_adapter,
    get_default_command_for_provider,
)
from .artifacts import (
    artifact_prompt_block,
    legacy_image_prompt_block,
    upstream_artifact_prompt_block,
)
from .agent_classes import append_agent_class_prompt_block
from .behavior_overlay import behavior_overlay_block_marker, split_behavior_overlay_blocks
from .config import log
from .state import normalize_codex_fast_mode
from .identity import prepend_agent_identity_anchor
from .runner_backends import normalize_runner_backend

DEFAULT_MCP_ENTRYPOINT = "torque/mcp.py"
ARCHITECT_MCP_ENTRYPOINT = "torque/mcp_architect.py"
ENGINEER_MCP_ENTRYPOINT = "torque/mcp_engineer.py"


def _copy_worktree_context(target, source) -> bool:
    """Copy an existing worktree context from one cell to another.

    Derived reviewers intentionally inherit the implementer's exact worktree
    path/branch instead of receiving a throwaway branch. The parent
    implementer is considered suspended by the task-handoff graph while the
    review is active; server-side checkpoint guards keep Torque-originated
    writes from that parent out of the shared branch until review finishes.
    """
    if not target or not source or not getattr(source, "worktree_path", ""):
        return False
    target.worktree_path = source.worktree_path
    target.worktree_branch = source.worktree_branch
    target.worktree_repo_root = source.worktree_repo_root
    target.worktree_base_branch = source.worktree_base_branch
    target.worktree_changed_files = list(source.worktree_changed_files or [])
    target.directory = source.worktree_path
    return True


def _append_task_artifacts(prompt: str, attachments, artifacts,
                           upstream_artifacts=None) -> str:
    """Append legacy image references plus structured artifact references."""
    final_prompt = prompt
    final_prompt += legacy_image_prompt_block(attachments, artifacts)
    final_prompt += artifact_prompt_block(artifacts)
    final_prompt += upstream_artifact_prompt_block(upstream_artifacts)
    return final_prompt


def _build_self_dispatch_prompt(shared_context_block: str = "") -> str:
    """Return the minimal follow-up prompt for derive-to-self."""
    prompt = "Proceed with the derived task you just created."
    if shared_context_block:
        prompt += shared_context_block
    return prompt


def _startup_prompt_for_new_agent(*, agent_type: str = "",
                                  persistent_prompt_text: str = "") -> str:
    """Return the first interactive prompt for a newly created agent.

    Always defers to the adapter's ``startup_prompt_from_persistent_prompt``.
    For claude-code this is ``""`` because the persistent prompt is delivered
    via ``--append-system-prompt-file`` (see ``ClaudeCodeAdapter
    .inject_persistent_prompt``) — sending it as the first chat turn would
    duplicate the prompt verbatim into the conversation. Codex still returns
    the text here because its file-based path historically required a first
    interactive turn to seat the instructions.
    """
    if not persistent_prompt_text:
        return ""
    adapter = get_adapter(agent_type) if agent_type else None
    if not adapter:
        return ""
    return adapter.startup_prompt_from_persistent_prompt(
        persistent_prompt_text
    )


def resolve_default_boot_nudge(state, cell) -> str:
    """Return the configured default boot nudge for an architect/engineer.

    Returns ``""`` for any other kind (worker, terminal, unset), which
    suppresses the fallback in ``_new_agent_prompt_sequence``. This is the
    TORQUE:263 fix — long-running architect/engineer agents need a kickoff
    every boot to run their wake protocol, even when their role has no
    ``initial_prompt`` configured. Workers receive their dispatch prompt
    via the task-dispatch path and are not nudged on boot.
    """
    if not cell or not state:
        return ""
    kind = str(getattr(cell, "kind", "") or "").strip()
    if kind not in ("architect", "engineer"):
        return ""
    gs = getattr(state, "global_settings", None)
    if not gs:
        return ""
    if kind == "architect":
        return str(getattr(gs, "architect_default_boot_nudge", "") or "")
    return str(getattr(gs, "engineer_default_boot_nudge", "") or "")


def _new_agent_prompt_sequence(launch_cfg: dict, *,
                               startup_prompt: str = "",
                               final_prompt: str = "",
                               cell=None,
                               default_boot_nudge: str = "",
                               task_id: str = "",
                               include_identity_anchor: bool = True,
                               include_final_identity_anchor: bool | None = None,
                               ) -> list[tuple[str, dict]]:
    """Return prompts to send to a brand-new agent in order.

    When ``launch_cfg["initial_prompt"]`` is empty/whitespace and
    ``default_boot_nudge`` is provided, the nudge is sent in its place. The
    caller is responsible for restricting ``default_boot_nudge`` to agent
    kinds that should actually be nudged (architect/engineer); use
    :func:`resolve_default_boot_nudge` to derive it from state + cell.
    """
    startup_len = len(startup_prompt or "")
    final_len = len(final_prompt or "")
    if include_final_identity_anchor is None:
        include_final_identity_anchor = include_identity_anchor
    prompts = []
    if startup_prompt:
        if include_identity_anchor:
            startup_prompt = prepend_agent_identity_anchor(startup_prompt, cell)
        prompts.append((startup_prompt, {}))
    initial_prompt = launch_cfg.get("initial_prompt", "") or ""
    initial_len = len(initial_prompt or "")
    if not initial_prompt.strip() and default_boot_nudge:
        initial_prompt = default_boot_nudge
    if initial_prompt:
        if include_identity_anchor:
            initial_prompt = prepend_agent_identity_anchor(initial_prompt, cell)
        prompts.append((initial_prompt, {}))
    if final_prompt:
        if include_final_identity_anchor:
            final_prompt = prepend_agent_identity_anchor(final_prompt, cell)
        prompts.append((final_prompt, {"background": True}))
    if not prompts:
        context = "dispatch_task" if task_id else "new_agent_prompt_sequence"
        cell_ref = (getattr(cell, "slug", "") or getattr(cell, "name", "")
                    or getattr(cell, "id", "") or "<unknown>")
        log.warning(
            "%s: empty prompt sequence for cell=%s task=%s "
            "(startup=%d, initial=%d, final=%d)",
            context, cell_ref, task_id or "<none>", startup_len, initial_len,
            final_len,
        )
    return prompts


def runtime_env_vars_for_cell(cell, env_vars: dict[str, str] | None = None):
    """Return launch env vars with per-cell identity bindings applied."""
    merged = dict(env_vars or {})
    if getattr(cell, "cell_type", "") == "agent":
        kind = str(getattr(cell, "kind", "") or "").strip()
        if kind == "engineer":
            merged["TORQUE_ENGINEER_ID"] = str(getattr(cell, "id", "") or "")
        elif kind == "architect":
            merged["TORQUE_ARCHITECT_ID"] = str(getattr(cell, "id", "") or "")
    return merged or None


def mcp_env_vars_for_cell(cell) -> dict[str, str] | None:
    """Return env vars safe to persist in provider MCP config files.

    Some provider config files can be shared by same-directory Torque cells;
    Codex now receives Torque-owned per-agent config, while other adapters may
    still use project-local files. These configs must therefore never persist
    per-cell identity bindings such as
    ``TORQUE_CELL_ID`` / ``TORQUE_ARCHITECT_ID`` / ``TORQUE_ENGINEER_ID``:
    whichever Architect/Engineer wrote the file last would otherwise bind
    another same-directory session's stdio MCP proxy to the wrong lane.

    Per-cell identity is supplied by the PTY process environment via
    :func:`runtime_env_vars_for_cell` and ``LocalPTYBridge._session_environment``;
    generated MCP config carries only daemon/profile values that are stable
    across same-directory cells.
    """
    if getattr(cell, "cell_type", "") != "agent":
        return None

    env = {
        "TORQUE_PORT": str(torque_config.WS_PORT),
        "TORQUE_DATA_DIR": str(torque_config.DATA_DIR),
    }
    profile = str(os.environ.get("TORQUE_PROFILE", "") or "").strip()
    if profile:
        env["TORQUE_PROFILE"] = profile
    return env


def mcp_entrypoint_for_cell(cell) -> str:
    """Return the MCP entrypoint path to associate with a cell launch."""
    if getattr(cell, "cell_type", "") != "agent":
        return ""
    kind = str(getattr(cell, "kind", "") or "").strip()
    if kind == "engineer":
        return ENGINEER_MCP_ENTRYPOINT
    if kind == "architect":
        return ARCHITECT_MCP_ENTRYPOINT
    return DEFAULT_MCP_ENTRYPOINT


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
        self._prompt_queue_tails: dict[str, asyncio.Task] = {}
        self._prompt_queue_optimistic_baselines: dict[str, dict] = {}

    def _runtime_terminal_backend(self) -> str:
        return "pty"

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

    @staticmethod
    def validate_provider_command_match(provider: str, command: str) -> None:
        """Reject direct provider/command mismatches before booting a TUI.

        A provider selects Torque's adapter (hooks, MCP config, and the
        input-ready policy).  If a raw command clearly names a different
        provider, the worker can boot under the wrong adapter and never
        receive its first prompt.  Custom wrapper commands are allowed: this
        guard only fires when the command can be positively detected as a
        different known provider.
        """
        provider = str(provider or "").strip()
        command = str(command or "").strip()
        if not provider or not command:
            return
        detected = detect_by_command(command)
        if not detected or detected.name == provider:
            return
        raise ValueError(
            "Provider/command mismatch: provider "
            f"'{provider}' selects the {provider} adapter, but command "
            f"{command!r} looks like provider '{detected.name}'. Pass "
            f"provider='{detected.name}' (or agent_type='{detected.name}' "
            "on dispatch) with this command, or use a command matching "
            f"provider '{provider}'."
        )

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
            cell.name for cell in self.state.iter_active_agents()
            if cell.group == group and cell.cell_type == "agent"
        }
        if base not in existing:
            return base
        index = 2
        while f"{base} {index}" in existing:
            index += 1
        return f"{base} {index}"

    @staticmethod
    def _resolve_fast_mode(*values) -> str:
        """Resolve launch preference from most to least specific scope."""
        for value in values:
            mode = normalize_codex_fast_mode(value, strict=False)
            if mode != "inherit":
                return mode
        return "inherit"

    def resolve_agent_launch_config(self, group: str, *,
                                    base_dir: str = "",
                                    explicit_template: str = "",
                                    overrides: dict[str, Any] | None = None,
                                    apply_group_default_role: bool = True) -> dict:
        """Resolve the effective launch configuration for an agent."""
        gs = self.state.get_group_settings(group)
        resolved = self.template_mgr.resolve_agent_config(
            explicit_template,
            gs,
            overrides or {},
            base_dir=base_dir,
            apply_default_template=apply_group_default_role,
        )

        provider = str(
            resolved.get("provider", "") or gs.agent_provider or ""
        ).strip()
        raw_command = str(
            resolved.get("command", "") or gs.agent_boot_command or ""
        ).strip()
        command, agent_type = self.resolve_provider_command(
            provider, raw_command, self.state.get_default_command()
        )
        self.validate_provider_command_match(provider, command)
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
        elif not tab_color:
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
            or gs.agent_terminal_profile
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

        runner_backend = normalize_runner_backend(
            resolved.get("runner_backend", ""),
            effective_agent_type,
        )
        return {
            "provider": provider,
            "agent_type": effective_agent_type,
            "runner_backend": runner_backend,
            "command": command.strip(),
            "fast_mode": self._resolve_fast_mode(
                resolved.get("fast_mode"), getattr(gs, "agent_fast_mode", "inherit"),
            ),
            # Preserve template/per-launch specificity for kind resolvers.
            "fast_mode_override": normalize_codex_fast_mode(
                resolved.get("fast_mode"), strict=False,
            ),
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
            "worktree_submodules": resolved.get(
                "worktree_submodules", getattr(gs, "worktree_submodules", [])),
            "worktree_symlink_gitignored_paths": resolved.get(
                "worktree_symlink_gitignored_paths",
                getattr(gs, "worktree_symlink_gitignored_paths", False)),
            "terminals": resolved.get("terminals", []),
        }

    def resolve_engineer_launch_config(self, group: str, *,
                                     base_dir: str = "",
                                     explicit_template: str = "",
                                     overrides: dict[str, Any] | None = None) -> dict:
        """Resolve launch config for the designated engineer in a group."""
        merged = dict(overrides or {})
        ws = self.state.get_engineer_settings(group)
        if getattr(ws, "engineer_provider", ""):
            merged["provider"] = ws.engineer_provider
        if getattr(ws, "engineer_boot_command", ""):
            merged["command"] = ws.engineer_boot_command
        if getattr(ws, "engineer_model", ""):
            merged["model"] = ws.engineer_model
        if getattr(ws, "engineer_reasoning_effort", ""):
            merged["reasoning_effort"] = ws.engineer_reasoning_effort
        if getattr(ws, "engineer_directory", ""):
            merged["directory"] = ws.engineer_directory
        if getattr(ws, "engineer_profile", ""):
            merged["profile"] = ws.engineer_profile
        if getattr(ws, "engineer_shell", ""):
            merged["shell"] = ws.engineer_shell
        if getattr(ws, "engineer_tab_color", ""):
            merged["tab_color"] = ws.engineer_tab_color
        resolved = self.resolve_agent_launch_config(
            group,
            base_dir=base_dir,
            explicit_template=explicit_template,
            overrides=merged,
            apply_group_default_role=False,
        )
        resolved["fast_mode"] = self._resolve_fast_mode(
            resolved.get("fast_mode_override"), getattr(ws, "engineer_fast_mode", "inherit"),
            getattr(gs := self.state.get_group_settings(group), "agent_fast_mode", "inherit"),
        )
        resolved["worktree"] = False
        return resolved

    def resolve_worker_launch_config(self, group: str, *,
                                     base_dir: str = "",
                                     explicit_template: str = "",
                                     overrides: dict[str, Any] | None = None) -> dict:
        """Resolve launch config for worker agents in a group."""
        merged = {}
        gs = self.state.get_group_settings(group)
        if getattr(gs, "worker_provider", ""):
            merged["provider"] = gs.worker_provider
        if getattr(gs, "worker_boot_command", ""):
            merged["command"] = gs.worker_boot_command
        if getattr(gs, "worker_model", ""):
            merged["model"] = gs.worker_model
        if getattr(gs, "worker_reasoning_effort", ""):
            merged["reasoning_effort"] = gs.worker_reasoning_effort
        for key, value in (overrides or {}).items():
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
            elif value is None:
                continue
            merged[key] = value
        resolved = self.resolve_agent_launch_config(
            group,
            base_dir=base_dir,
            explicit_template=explicit_template,
            overrides=merged,
            apply_group_default_role=True,
        )
        resolved["fast_mode"] = self._resolve_fast_mode(
            resolved.get("fast_mode_override"), getattr(gs, "worker_fast_mode", "inherit"),
            getattr(gs, "agent_fast_mode", "inherit"),
        )
        return resolved

    def resolve_architect_launch_config(self, group: str, *,
                                      base_dir: str = "",
                                      explicit_template: str = "",
                                      overrides: dict[str, Any] | None = None) -> dict:
        """Resolve launch config for architect agents in a group."""
        merged = dict(overrides or {})
        settings_getter = getattr(self.state, "get_architect_settings", None)
        ws = settings_getter(group) if callable(settings_getter) else None
        if getattr(ws, "architect_provider", ""):
            merged["provider"] = ws.architect_provider
        if getattr(ws, "architect_boot_command", ""):
            merged["command"] = ws.architect_boot_command
        if getattr(ws, "architect_model", ""):
            merged["model"] = ws.architect_model
        if getattr(ws, "architect_reasoning_effort", ""):
            merged["reasoning_effort"] = ws.architect_reasoning_effort
        if getattr(ws, "architect_directory", ""):
            merged["directory"] = ws.architect_directory
        if getattr(ws, "architect_profile", ""):
            merged["profile"] = ws.architect_profile
        if getattr(ws, "architect_shell", ""):
            merged["shell"] = ws.architect_shell
        if getattr(ws, "architect_tab_color", ""):
            merged["tab_color"] = ws.architect_tab_color
        resolved = self.resolve_agent_launch_config(
            group,
            base_dir=base_dir,
            explicit_template=explicit_template,
            overrides=merged,
            apply_group_default_role=False,
        )
        resolved["fast_mode"] = self._resolve_fast_mode(
            resolved.get("fast_mode_override"), getattr(ws, "architect_fast_mode", "inherit"),
            getattr(self.state.get_group_settings(group), "agent_fast_mode", "inherit"),
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
        return f"torque-system-prompt-{cell.id}.md"

    def apply_persistent_prompt(self, cell, launch_cfg: dict,
                                prompt_text: str = "") -> None:
        """Inject adapter-managed persistent prompt flags into launch config."""
        agent_type = launch_cfg.get("agent_type", "") or cell.agent_type
        if not prompt_text or not agent_type:
            return
        kind = str(getattr(cell, "kind", "") or "").strip()
        if kind in {"architect", "engineer"}:
            renderer = getattr(
                self.state,
                "render_behavior_overlay_stack_for_cell",
                None,
            )
            if callable(renderer):
                try:
                    overlay = renderer(
                        cell,
                        include_role=True,
                        include_agent=True,
                        seed_agent=True,
                        seed_role=False,
                    )
                except Exception:
                    log.exception(
                        "Failed to append behavior overlay for cell=%s",
                        getattr(cell, "id", ""),
                    )
                    overlay = ""
                missing_blocks = []
                for block in split_behavior_overlay_blocks(overlay):
                    marker = behavior_overlay_block_marker(block)
                    if marker and marker not in prompt_text:
                        missing_blocks.append(block.rstrip())
                if missing_blocks:
                    prompt_text = (
                        prompt_text.rstrip()
                        + "\n\n"
                        + "\n\n".join(missing_blocks)
                        + "\n"
                    )
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
                                       created_by_engineer_id: str = "",
                                       owner_engineer_id: str = "",
                                       kind: str = "",
                                       persistent: bool = False,
                                       hired_by_architect_id: str = "",
                                       inherited_worktree_from=None,
                                       restore_focus_to_prev_tab: bool = False):
        """Create an agent cell, prepare its worktree, and open the session."""
        runner_backend = normalize_runner_backend(
            launch_cfg.get("runner_backend", ""),
            launch_cfg.get("agent_type", ""),
        )
        launch_cfg["runner_backend"] = runner_backend
        cell = self.state.add_agent(
            name=name,
            group=group,
            terminal_backend=self._runtime_terminal_backend(),
            runner_backend=runner_backend,
            profile=launch_cfg["profile"],
            command=launch_cfg["command"],
            directory=launch_cfg["directory"],
            tab_color=launch_cfg["tab_color"],
            icon=launch_cfg.get("icon", ""),
        )
        if not cell:
            log.warning("add_agent returned None for group=%s name=%s",
                        group, name)
            return None
        cell.session_resume = bool(launch_cfg.get("session_resume", True))
        cell.fast_mode = normalize_codex_fast_mode(launch_cfg.get("fast_mode"), strict=False)
        cell.idle_timeout = int(launch_cfg.get("idle_timeout", 0) or 0)
        cell.kind = str(kind or "").strip()
        cell.persistent = bool(persistent)
        cell.hired_by_architect_id = str(hired_by_architect_id or "").strip()
        cell.worktree_base_dir = (
            launch_cfg.get("worktree_base_dir") or ".torque/worktrees"
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
        cell.created_by_engineer_id = str(created_by_engineer_id or "").strip()
        cell.owner_engineer_id = str(owner_engineer_id or "").strip() or str(
            created_by_engineer_id or ""
        ).strip()
        if launch_cfg.get("agent_type"):
            cell.agent_type = launch_cfg["agent_type"]
        cell.runner_backend = runner_backend
        if launch_cfg.get("agent_class_id"):
            cell.agent_class_id = str(launch_cfg.get("agent_class_id") or "").strip()
            cell.agent_class_version = str(launch_cfg.get("agent_class_version") or "").strip()
            cell.agent_class_assigned_at = time.time()
            cell.agent_class_assigned_by = "trusted-user-launch"
        try:
            self.state.apply_effective_agent_class_for_launch(
                cell,
                base_dir=cell.directory or launch_cfg.get("directory", ""),
            )
        except Exception:
            log.exception(
                "Failed to apply Agent Class launch snapshot for cell=%s",
                getattr(cell, "id", ""),
            )
            raise
        inherited_worktree = _copy_worktree_context(
            cell, inherited_worktree_from
        )
        adopted_worktree = launch_cfg.get("adopted_worktree") or {}
        if adopted_worktree and not inherited_worktree:
            cell.worktree_path = str(adopted_worktree.get("worktree_path", "") or "")
            cell.worktree_branch = str(adopted_worktree.get("branch", "") or "")
            cell.worktree_repo_root = str(adopted_worktree.get("repo_root", "") or "")
            cell.git_root = cell.worktree_repo_root
            cell.worktree_base_branch = str(adopted_worktree.get("base_branch", "") or "")
            if cell.worktree_path:
                cell.directory = cell.worktree_path
                launch_cfg["directory"] = cell.worktree_path
            launch_cfg["worktree"] = False
        if (cell.kind == "architect"
                and not torque_config.ARCHITECT_USES_WORKTREE
                and not inherited_worktree):
            launch_cfg["worktree"] = False
            if cell.directory and self.worktree_mgr:
                repo_root = await self.worktree_mgr.get_repo_root(cell.directory)
                if repo_root:
                    cell.directory = repo_root
                    launch_cfg["directory"] = repo_root
        self.state._emit_agent(cell)
        self.state._db_save_agent(cell)
        self.state.history_record_agent(cell)

        if launch_cfg.get("worktree") and cell.directory and not inherited_worktree:
            repo_root = await self.worktree_mgr.get_repo_root(cell.directory)
            if repo_root:
                worktree_path = await self.worktree_mgr.create(
                    cell,
                    repo_root,
                    base_dir=cell.worktree_base_dir or ".torque/worktrees",
                    base_branch=launch_cfg.get("worktree_base_branch", ""),
                    symlinks=launch_cfg.get("worktree_symlinks", []),
                    include_gitignored_symlinks=launch_cfg.get(
                        "worktree_symlink_gitignored_paths", False),
                    worktree_name=launch_cfg.get("worktree_name", ""),
                    worktree_submodules=launch_cfg.get(
                        "worktree_submodules", []),
                    state=self.state,
                )
                if worktree_path:
                    cell.directory = worktree_path
                    self.state._emit_agent(cell)
                    self.state._db_save_agent(cell)
                    self.state.history_update_agent(
                        cell, worktree_branch=cell.worktree_branch
                    )
                else:
                    log.warning("worktree create failed silently for cell=%s repo=%s",
                                cell.slug or cell.name or cell.id, repo_root)

        persistent_prompt_text = append_agent_class_prompt_block(
            persistent_prompt_text,
            cell,
        )
        self.apply_persistent_prompt(cell, launch_cfg, persistent_prompt_text)
        self.state._emit_agent(cell)
        self.state._db_save_agent(cell)

        try:
            await self.bridge.create_session(
                cell,
                env_vars=runtime_env_vars_for_cell(
                    cell, launch_cfg.get("env_vars")
                ),
                env_file=launch_cfg.get("env_file", ""),
                shell=launch_cfg.get("shell", ""),
                system_prompt=launch_cfg.get("system_prompt", ""),
                sdk_system_prompt=launch_cfg.get("sdk_system_prompt", ""),
                mcp_entrypoint=mcp_entrypoint_for_cell(cell),
                target_session_id=target_session_id,
                target_window_id=target_window_id,
                restore_focus_to_prev_tab=restore_focus_to_prev_tab,
            )
        except Exception as exc:
            message = f"Agent session failed to start: {exc}"
            log.warning(
                "bridge.create_session failed for cell=%s group=%s: %s",
                cell.slug or cell.name or cell.id,
                cell.group,
                exc,
                exc_info=True,
            )
            try:
                cell.error_message = message
                cell.needs_attention = True
                self.state._emit_agent(cell)
                self.state._db_save_agent(cell)
                panel_log = getattr(self.state, "panel_log", None)
                if panel_log and hasattr(panel_log, "append"):
                    pe = panel_log.append(
                        kind="agent_error", cell_id=cell.id,
                        agent_name=cell.name, group=cell.group,
                        message=message,
                    )
                    self.state._emit("event_append", **pe)
            except Exception:
                log.exception(
                    "Failed to emit create_session agent_error for cell=%s",
                    getattr(cell, "id", "<unknown>"),
                )
            raise
        return cell

    async def send_agent_prompt(self, cell, prompt: str, *,
                                delay: float = 0,
                                persist: bool = False,
                                background: bool = False,
                                prime_input_ready: bool = False,
                                settled_submit: bool = False):
        """Send a prompt to a cell session, optionally delayed/queued."""
        payload = prompt if prompt.endswith("\r") else prompt + "\r"
        target_session_id = cell.session_id or ""
        previous = self._prompt_queue_tails.get(target_session_id)
        optimistic_baseline = {}
        if target_session_id:
            optimistic_baseline = (
                self._prompt_queue_optimistic_baselines.get(target_session_id)
                or self.state.snapshot_agent_optimistic_state(cell)
            )
            self._prompt_queue_optimistic_baselines.setdefault(
                target_session_id,
                optimistic_baseline,
            )
        optimistic_at = time.time()
        optimistic_marked = False
        if target_session_id:
            optimistic_marked = self.state.mark_agent_optimistic_running(
                cell,
                optimistic_at,
                emit=True,
                persist=persist,
            )
            if optimistic_marked:
                await self.state.broadcast()
        task_ref = None

        async def _rollback_optimistic_running():
            if not optimistic_marked or not optimistic_baseline:
                return
            if getattr(cell, "status", "") != "running":
                return
            if getattr(cell, "activity", ""):
                return
            if target_session_id \
                    and self._prompt_queue_tails.get(target_session_id) is not task_ref:
                # A newer prompt is queued for the same session; keep the
                # optimistic Running status for that pending work.
                return
            if float(getattr(cell, "last_progress_at", 0) or 0) > optimistic_at:
                # A real activity/progress event arrived after the optimistic
                # mark, so do not roll the cell back underneath it.
                return
            baseline = self._prompt_queue_optimistic_baselines.get(
                target_session_id,
                optimistic_baseline,
            )
            changed = self.state.restore_agent_optimistic_state(
                cell,
                baseline,
                emit=True,
                persist=persist,
            )
            if target_session_id:
                self._prompt_queue_optimistic_baselines.pop(
                    target_session_id, None)
            if changed:
                await self.state.broadcast()

        async def _run():
            try:
                if previous:
                    try:
                        await previous
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass
                if delay:
                    await asyncio.sleep(delay)
                if not target_session_id or cell.session_id != target_session_id:
                    # Silent-drop guard (TORQUE:165 observability). A cell
                    # whose session_id went empty or changed between
                    # enqueue and send used to drop the prompt with no
                    # signal; that is the DOA pattern we paid for in
                    # wave 2. Surface it so post-mortems are 2 min not
                    # 90 min.
                    log.warning(
                        "send_agent_prompt dropped: session mismatch for "
                        "cell=%s (target=%s now=%s prompt=%d chars)",
                        cell.slug or cell.name or cell.id,
                        target_session_id or "<empty>",
                        cell.session_id or "<empty>",
                        len(prompt),
                    )
                    await _rollback_optimistic_running()
                    return
                if prime_input_ready \
                        and hasattr(self.bridge, "prime_input_ready"):
                    self.bridge.prime_input_ready(target_session_id)
                send_started_at = time.time()
                if settled_submit:
                    await self.bridge.send_text(
                        target_session_id,
                        payload,
                        settled_submit=True,
                    )
                else:
                    await self.bridge.send_text(target_session_id, payload)
                completion_landed_during_send = (
                    getattr(cell, "status", "") != "running"
                    and not getattr(cell, "activity", "")
                    and float(getattr(cell, "last_event_at", 0) or 0)
                    >= send_started_at
                )
                if not completion_landed_during_send:
                    self.state.mark_agent_optimistic_running(
                        cell,
                        emit=False,
                        persist=persist,
                    )
                if target_session_id:
                    if self._prompt_queue_tails.get(target_session_id) is task_ref:
                        self._prompt_queue_optimistic_baselines.pop(
                            target_session_id, None)
                    else:
                        self._prompt_queue_optimistic_baselines[
                            target_session_id
                        ] = self.state.snapshot_agent_optimistic_state(cell)
                self.state._emit_agent(cell)
                if persist:
                    self.state._db_save_agent(cell)
                await self.state.broadcast()
            except Exception:
                await _rollback_optimistic_running()
                raise
            finally:
                if target_session_id \
                        and self._prompt_queue_tails.get(target_session_id) is task_ref:
                    self._prompt_queue_tails.pop(target_session_id, None)
                    self._prompt_queue_optimistic_baselines.pop(
                        target_session_id, None)
                if task_ref is not None:
                    self._background_prompt_tasks.discard(task_ref)

        task_ref = asyncio.create_task(_run())
        if target_session_id:
            self._prompt_queue_tails[target_session_id] = task_ref

        if background:
            self._background_prompt_tasks.add(task_ref)
            task_ref.add_done_callback(self._log_background_prompt_failure)
            return task_ref
        else:
            await task_ref

    @staticmethod
    def _log_background_prompt_failure(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.exception("Background prompt delivery failed", exc_info=exc)
