"""Agent launch, role selection, lifecycle authority, and hire orchestration."""

from __future__ import annotations

import shlex
import uuid
from dataclasses import asdict
from types import SimpleNamespace

from . import config as torque_config
from .agent_classes import (
    AGENT_CLASS_SCHEMA_VERSION,
    agent_class_definition_by_id,
    append_agent_class_prompt_block,
    enriched_agent_class_preview,
)
from .config import log
from .server_agent import (
    _new_agent_prompt_sequence,
    _startup_prompt_for_new_agent,
    mcp_entrypoint_for_cell,
    resolve_default_boot_nudge,
    runtime_env_vars_for_cell,
)
from .server_agent_common import _resolve_agent_id, _should_show_guidance_hint
from .server_prompts import build_torque_system_prompt
from .state import ArchitectSettings, EngineerSettings, MatrixState


GUIDANCE_HINT_IDENTITY_LAUNCH = "agent_identity_anchor.launch"


def _resolve_pending_engineer_specializations(
        data: dict, state, group: str, is_engineer: bool) -> list:
    """Resolve the specializations list applied to a new engineer.

    Honors an explicit ``specializations`` field in ``data`` (including an
    explicit empty list, which means "no specs"). When the field is absent,
    falls back to the group-level default
    (``GroupSettings.default_engineer_specializations``).

    Returns ``[]`` for non-engineer agents.
    """
    if not is_engineer:
        return []
    if "specializations" in data:
        return [
            str(item or "").strip()
            for item in (data.get("specializations") or [])
            if str(item or "").strip()
        ]
    gs_default = state.get_group_settings(group)
    return [
        str(item or "").strip()
        for item in (
            getattr(gs_default, "default_engineer_specializations", None) or []
        )
        if str(item or "").strip()
    ]

def _project_specialization_names(specialization_mgr,
                                  base_dir: str = "") -> list[str]:
    """Return architect-routable project specialization slugs only."""
    resolver = getattr(specialization_mgr, "canonical_project_names", None)
    if callable(resolver):
        return [
            str(name or "").strip()
            for name in resolver(base_dir=base_dir)
            if str(name or "").strip()
        ]
    names = []
    seen = set()
    for item in specialization_mgr.list_specializations(
            base_dir=base_dir,
            scope="project",
    ):
        name = str((item or {}).get("name", "") or "").strip()
        if not name or name in seen:
            continue
        names.append(name)
        seen.add(name)
    return names

def _normalize_engineer_specialization_selection(
        raw, valid_names: list | set | tuple | None = None) -> list:
    """Validate, dedupe, and preserve engineer specialization order."""
    if not isinstance(raw, list):
        raise ValueError("specializations must be a list")
    valid_list = None
    valid_set = None
    if valid_names is not None:
        valid_list = []
        valid_seen = set()
        for item in valid_names:
            token = str(item or "").strip()
            if not token or token in valid_seen:
                continue
            valid_list.append(token)
            valid_seen.add(token)
        valid_set = set(valid_list)
    names = []
    seen = set()
    unknown = []
    for item in raw:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        if valid_set is not None and token not in valid_set:
            unknown.append(token)
            continue
        names.append(token)
    if unknown:
        message = (
            "Unknown specialization"
            + ("s" if len(unknown) != 1 else "")
            + ": "
            + ", ".join(unknown)
        )
        if valid_list is not None:
            message += ". Valid specializations: " + ", ".join(valid_list)
        raise ValueError(message)
    return names

def _launch_resolver_for_cell(
        cell, *,
        resolve_agent_launch_config,
        resolve_engineer_launch_config=None,
        resolve_architect_launch_config=None,
        resolve_worker_launch_config=None,
        is_designated_engineer=None):
    """Pick the kind-specific launch resolver for an existing cell."""
    if getattr(cell, "cell_type", "") != "agent":
        return resolve_agent_launch_config
    kind = str(getattr(cell, "kind", "") or "").strip()
    if kind == "architect":
        if resolve_architect_launch_config:
            return resolve_architect_launch_config
        if resolve_engineer_launch_config:
            return resolve_engineer_launch_config
    if kind == "engineer" and resolve_engineer_launch_config:
        return resolve_engineer_launch_config
    if kind == "worker" and resolve_worker_launch_config:
        return resolve_worker_launch_config
    if is_designated_engineer and is_designated_engineer(cell) \
            and resolve_engineer_launch_config:
        return resolve_engineer_launch_config
    return resolve_agent_launch_config

async def _relaunch_agent_after_worktree_removal(
        cell, *,
        bridge,
        state,
        resolve_base_dir,
        resolve_agent_launch_config,
        resolve_engineer_launch_config=None,
        resolve_architect_launch_config=None,
        resolve_worker_launch_config=None,
        is_designated_engineer=None,
        apply_persistent_prompt,
        build_cell_persistent_prompt,
        send_agent_prompt=None):
    """Reset an agent session after its worktree is removed.

    This sibling restart path always opens a fresh provider conversation
    (``agent_session_id`` is cleared above), so when ``send_agent_prompt``
    is supplied the role's startup + initial prompts are re-delivered via
    ``_new_agent_prompt_sequence``. Mirrors the ``:259`` fix in
    ``_handle_relaunch_agent_command``: codex agents get their persistent
    prompt seated as the first chat turn, claude-code agents get any role
    ``initial_prompt`` (kickoff text) without duplicating the file-injected
    system prompt.
    """
    if cell.cell_type != "agent":
        return
    if cell.session_id:
        await bridge.close_session(cell.session_id)
    cell.status = "stopped"
    cell.session_id = None
    cell.agent_session_id = ""
    base_dir = cell.worktree_repo_root or cell.directory \
        or await resolve_base_dir(cell.group)
    resolver = _launch_resolver_for_cell(
        cell,
        resolve_agent_launch_config=resolve_agent_launch_config,
        resolve_engineer_launch_config=resolve_engineer_launch_config,
        resolve_architect_launch_config=resolve_architect_launch_config,
        resolve_worker_launch_config=resolve_worker_launch_config,
        is_designated_engineer=is_designated_engineer,
    )
    launch_cfg = resolver(
        cell.group,
        base_dir=base_dir,
        explicit_template=cell.template,
        overrides={},
    )
    if getattr(cell, "runner_backend", ""):
        launch_cfg["runner_backend"] = getattr(cell, "runner_backend", "")
    persistent_prompt_text = build_cell_persistent_prompt(cell, launch_cfg)
    apply_persistent_prompt(cell, launch_cfg, persistent_prompt_text)
    state._emit_agent(cell)
    state._db_save_agent(cell)
    await bridge.create_session(
        cell,
        env_vars=runtime_env_vars_for_cell(cell, launch_cfg.get("env_vars")),
        env_file=launch_cfg.get("env_file", ""),
        shell=launch_cfg.get("shell", ""),
        system_prompt=launch_cfg.get("system_prompt", ""),
        mcp_entrypoint=mcp_entrypoint_for_cell(cell),
    )

    # Fresh-session kickoff: agent_session_id was cleared above to force a
    # fresh provider conversation, so the same kickoff conditions as
    # ``_handle_relaunch_agent_command`` always fire here when
    # ``send_agent_prompt`` is supplied. Without this, any role
    # ``initial_prompt`` is silently dropped on worktree-removal relaunch
    # and codex agents lose their persistent system prompt entirely.
    if (
            send_agent_prompt
            and cell.session_id
            and (not cell.agent_session_id or not cell.session_resume)
    ):
        startup_prompt = _startup_prompt_for_new_agent(
            agent_type=launch_cfg.get("agent_type", ""),
            persistent_prompt_text=persistent_prompt_text,
        )
        for prompt_text, send_kwargs in _new_agent_prompt_sequence(
                launch_cfg, startup_prompt=startup_prompt, cell=cell,
                default_boot_nudge=resolve_default_boot_nudge(state, cell),
                include_identity_anchor=_should_show_guidance_hint(
                    state,
                    cell,
                    GUIDANCE_HINT_IDENTITY_LAUNCH,
                )):
            await send_agent_prompt(cell, prompt_text, **send_kwargs)

def _resolve_engineer_group(state: MatrixState) -> str:
    """Return the reserved engineer group, preferring the designated engineer."""
    for group_name, group_settings in state.group_settings.items():
        engineer_id = str(getattr(group_settings, "engineer_agent_id", "") or "")
        cell = state.get_active_agent(engineer_id)
        if cell and cell.cell_type == "agent" \
                and str(getattr(cell, "kind", "") or "").strip() == "engineer":
            return str(cell.group or group_name or "torque")
    for cell in state.iter_active_agents():
        if cell.cell_type != "agent":
            continue
        if str(getattr(cell, "kind", "") or "").strip() == "engineer":
            return str(cell.group or "torque")
    return "torque"

def _resolve_engineer_cell(state: MatrixState, *, engineer_id: str = "",
                           engineer_slug: str = "",
                           include_tombstoned: bool = False):
    """Resolve an engineer agent by exact id or slug."""
    engineer_id = str(engineer_id or "").strip()
    engineer_slug = str(engineer_slug or "").strip().lower()
    for cell in state.iter_agents(include_tombstoned=include_tombstoned):
        if cell.cell_type != "agent":
            continue
        if str(getattr(cell, "kind", "") or "").strip() != "engineer":
            continue
        if engineer_id and cell.id == engineer_id:
            return cell
        if engineer_slug and str(getattr(cell, "slug", "") or "").strip().lower() \
                == engineer_slug:
            return cell
    return None

def _agent_dismissed_at(cell) -> int:
    try:
        return int(getattr(cell, "dismissed_at", 0) or 0)
    except (TypeError, ValueError):
        return 0

def _relaunch_command_base(command: str, prompt_filename: str) -> str:
    """Return a persisted relaunch command without Torque-managed prompt flags."""
    command = str(command or "").strip()
    prompt_filename = str(prompt_filename or "").strip()
    if not command or not prompt_filename:
        return command
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    cleaned = []
    idx = 0
    while idx < len(parts):
        part = parts[idx]
        if (
                part == "--append-system-prompt-file"
                and idx + 1 < len(parts)
                and prompt_filename in parts[idx + 1]):
            idx += 2
            continue
        cleaned.append(part)
        idx += 1
    if len(cleaned) == len(parts):
        return command
    return shlex.join(cleaned)

def _engineer_dismissed_error(engineer_id: str) -> dict:
    return {
        "type": "error",
        "reason": "engineer_dismissed",
        "message": f"engineer {engineer_id} is dismissed",
        "engineer_id": str(engineer_id or "").strip(),
    }

def _engineer_tombstoned_error(engineer_id: str) -> dict:
    return {
        "type": "error",
        "reason": "engineer_tombstoned",
        "message": f"engineer {engineer_id} is tombstoned",
        "engineer_id": str(engineer_id or "").strip(),
    }

def _architect_dismissed_error(architect_id: str) -> dict:
    return {
        "type": "error",
        "reason": "architect_dismissed",
        "message": f"architect {architect_id} is dismissed",
        "architect_id": str(architect_id or "").strip(),
    }

def _validate_engineer_lifecycle_authority(
        state: MatrixState,
        engineer,
        *,
        architect_id: str = "") -> dict | None:
    """Return an error if an architect-scoped lifecycle command is unauthorized."""
    architect_id = str(architect_id or "").strip()
    if not architect_id:
        return None
    architect = _resolve_architect_cell(state, architect_id=architect_id)
    if not architect:
        return {"type": "error", "message": "Architect not found"}
    hired_by = str(getattr(engineer, "hired_by_architect_id", "") or "").strip()
    if hired_by != architect.id:
        return {"type": "error", "message": "engineer not found in scope"}
    return None

def _validate_architect_lifecycle_authority(
        state: MatrixState,
        architect,
        *,
        caller_kind: str = "") -> dict | None:
    """Return an error if a non-user tries to manage architect lifecycle."""
    del state, architect
    caller_kind = str(caller_kind or "").strip()
    if caller_kind and caller_kind != "user":
        return {
            "type": "error",
            "message": "architect lifecycle is user-only",
        }
    return None

def _effective_owner_engineer_id(cell) -> str:
    owner_id = str(getattr(cell, "owner_engineer_id", "") or "").strip()
    if owner_id:
        return owner_id
    return str(getattr(cell, "created_by_engineer_id", "") or "").strip()

def _dismissal_close_cells(state: MatrixState, engineer) -> list:
    """Return the engineer, owned workers, and child terminals to close."""
    roots = []
    seen: set[str] = set()

    def add_root(cell) -> None:
        if not cell or cell.id in seen:
            return
        seen.add(cell.id)
        roots.append(cell)

    add_root(engineer)
    for cell in list(state.iter_active_agents()):
        if cell.cell_type != "agent":
            continue
        if str(getattr(cell, "kind", "") or "").strip() != "worker":
            continue
        if _effective_owner_engineer_id(cell) == engineer.id:
            add_root(cell)

    ordered = []
    ordered_seen: set[str] = set()

    def add_with_children(cell) -> None:
        if not cell or cell.id in ordered_seen:
            return
        ordered_seen.add(cell.id)
        ordered.append(cell)
        for child_id in list(getattr(state, "_children", {}).get(cell.id, [])):
            add_with_children(state.agents.get(child_id))

    for root in roots:
        add_with_children(root)
    return ordered

async def _close_cell_session_preserving_state(
        state: MatrixState,
        cell,
        close_session,
        *,
        errors: list[str] | None = None) -> bool:
    """Close a cell's terminal session while preserving its agent row/history."""
    if not cell:
        return False
    had_session = bool(getattr(cell, "session_id", "") or "")
    if had_session:
        try:
            await close_session(cell.session_id)
        except Exception as exc:
            if errors is not None:
                errors.append(f"Failed to close session for '{cell.name}': {exc}")
            log.exception("Failed to close session for '%s'", cell.name)
    cell.status = "stopped"
    cell.session_id = None
    cell.current_process = ""
    cell.current_path = ""
    cell.current_branch = ""
    cell.git_root = ""
    cell.activity = ""
    cell.activity_detail = ""
    cell.error_message = ""
    cell.needs_attention = False
    state._emit_agent(cell)
    state._db_save_agent(cell)
    return had_session

def _resolve_architect_cell(state: MatrixState, *, architect_id: str = "",
                            architect_slug: str = "",
                            include_tombstoned: bool = False):
    """Resolve an architect agent by exact id or slug."""
    architect_id = str(architect_id or "").strip()
    architect_slug = str(architect_slug or "").strip().lower()
    for cell in state.iter_agents(include_tombstoned=include_tombstoned):
        if cell.cell_type != "agent":
            continue
        if str(getattr(cell, "kind", "") or "").strip() != "architect":
            continue
        if architect_id and cell.id == architect_id:
            return cell
        if architect_slug and str(getattr(cell, "slug", "") or "").strip().lower() \
                == architect_slug:
            return cell
    return None

def _engineer_name_exists(state: MatrixState, name: str, *,
                          exclude_id: str = "") -> bool:
    """Return True when another engineer already has ``name``."""
    return _agent_name_exists_for_kind(
        state,
        name,
        kind="engineer",
        exclude_id=exclude_id,
    )

def _architect_name_exists(state: MatrixState, name: str, *,
                           exclude_id: str = "") -> bool:
    """Return True when another architect already has ``name``."""
    return _agent_name_exists_for_kind(
        state,
        name,
        kind="architect",
        exclude_id=exclude_id,
    )

def _agent_name_exists_for_kind(state: MatrixState, name: str, *,
                                kind: str, exclude_id: str = "") -> bool:
    """Return True when another agent of ``kind`` already has ``name``."""
    normalized = str(name or "").strip().lower()
    if not normalized:
        return False
    excluded = str(exclude_id or "").strip()
    expected_kind = str(kind or "").strip()
    for cell in state.iter_active_agents():
        if cell.cell_type != "agent":
            continue
        if str(getattr(cell, "kind", "") or "").strip() != expected_kind:
            continue
        if excluded and cell.id == excluded:
            continue
        if str(cell.name or "").strip().lower() == normalized:
            return True
    return False

def _behavior_overlay_prompt_block_for_cell(
        state: MatrixState = None,
        cell=None,
        *,
        agent_id: str = "",
        kind: str = "",
        include_role: bool = True,
        include_agent: bool = True,
        worker_dispatch: bool = False) -> str:
    """Fetch a supported agent's Dynamic Behavior prompt block stack."""
    if state is None:
        return ""
    target_id = str(agent_id or getattr(cell, "id", "") or "").strip()
    target_kind = str(kind or getattr(cell, "kind", "") or "").strip()
    if target_kind not in {"architect", "engineer", "worker"} or not target_id:
        return ""
    try:
        target_cell = cell or state.agents.get(target_id)
        if not target_cell:
            return ""
        return state.render_behavior_overlay_stack_for_cell(
            target_cell,
            include_role=include_role,
            include_agent=include_agent and target_kind in {"architect", "engineer"},
            seed_agent=True,
            seed_role=False,
            worker_dispatch=worker_dispatch,
        )
    except Exception:
        log.exception("Failed to render behavior overlay for %s", target_id)
        try:
            from .behavior_overlay import render_behavior_overlay_block
            return render_behavior_overlay_block(agent_id=target_id)
        except Exception:
            return ""

def _architect_persistent_prompt_text(group: str = "",
                                      action_system_prompt: str = "",
                                      state: MatrixState = None,
                                      architect_id: str = "",
                                      agent_class_snapshot: dict | None = None
                                      ) -> str:
    """Build the persistent prompt for user-created architect agents."""
    from .architect import (
        build_architect_system_prompt,
        build_architect_torque_preamble,
    )

    group_settings = None
    architect_settings = None
    architect_cell = None
    if state is not None and group:
        try:
            group_settings = state.get_group_settings(group)
            architect_settings = state.get_architect_settings(group)
        except Exception:
            group_settings = None
            architect_settings = None
    if state is not None and architect_id:
        architect_cell = state.agents.get(str(architect_id or "").strip())
        if architect_cell is not None:
            if not agent_class_snapshot:
                agent_class_snapshot = getattr(
                    architect_cell,
                    "effective_agent_class_snapshot",
                    {},
                )

    architect_body = build_architect_system_prompt(
        group or "default",
        architect_settings=architect_settings,
        action_system_prompt=action_system_prompt,
        group_settings=group_settings,
        architect_cell=architect_cell,
        agent_class_snapshot=agent_class_snapshot,
        behavior_overlay_block=_behavior_overlay_prompt_block_for_cell(
            state,
            agent_id=architect_id,
            kind="architect",
        ),
    ).rstrip()

    torque_preamble = build_architect_torque_preamble(
        architect_cell=architect_cell,
        agent_class_snapshot=agent_class_snapshot,
    ).rstrip()
    assembled = torque_preamble + "\n\n" + architect_body + "\n"
    if isinstance(agent_class_snapshot, dict) and agent_class_snapshot.get("id"):
        assembled = append_agent_class_prompt_block(
            assembled,
            SimpleNamespace(
                effective_agent_class_snapshot=agent_class_snapshot,
            ),
        )
    return assembled

def _snapshot_dataclass_like(obj) -> dict:
    if obj is None:
        return {}
    try:
        return asdict(obj)
    except TypeError:
        return {
            key: getattr(obj, key)
            for key in dir(obj)
            if not key.startswith("_")
            and not callable(getattr(obj, key, None))
        }

def _preview_group_settings_for_prompt(
        state: MatrixState, group: str, payload: dict | None = None):
    """Return a group-settings snapshot with unsaved form values overlaid."""
    values = _snapshot_dataclass_like(state.get_group_settings(group))
    for key, value in dict(payload or {}).items():
        if key in values:
            values[key] = value
    return SimpleNamespace(**values)

def _preview_engineer_settings_for_prompt(
        state: MatrixState, group: str, payload: dict | None = None
        ) -> EngineerSettings:
    """Return EngineerSettings with unsaved form values overlaid.

    The preview path intentionally does not mutate MatrixState; it mirrors the
    prompt builder's inputs so the settings modal can ask for a one-off render
    while the user is still editing the form.
    """
    values = _snapshot_dataclass_like(state.get_engineer_settings(group))
    values["group"] = group
    valid = set(EngineerSettings.__dataclass_fields__)
    for key, value in dict(payload or {}).items():
        if key in valid and key != "group":
            values[key] = value
    values["group"] = group
    return EngineerSettings(**{
        key: values.get(key)
        for key in EngineerSettings.__dataclass_fields__
    })

def _preview_architect_settings_for_prompt(
        state: MatrixState, group: str, payload: dict | None = None
        ) -> ArchitectSettings:
    """Return ArchitectSettings with unsaved form values overlaid."""
    incoming = dict(payload or {})
    if (
            "custom_instructions" in incoming
            and "architect_custom_instructions" not in incoming):
        incoming["architect_custom_instructions"] = incoming.pop(
            "custom_instructions"
        )
    values = _snapshot_dataclass_like(state.get_architect_settings(group))
    values["group"] = group
    valid = set(ArchitectSettings.__dataclass_fields__)
    for key, value in incoming.items():
        if key in valid and key != "group":
            values[key] = value
    values["group"] = group
    return ArchitectSettings(**{
        key: values.get(key)
        for key in ArchitectSettings.__dataclass_fields__
    })

def _build_group_system_prompt_preview(
        state: MatrixState, group: str, kind: str, *,
        settings_payload: dict | None = None,
        group_settings_payload: dict | None = None,
        action_system_prompt: str = "",
        specializations_preamble: str = "") -> str:
    """Build the settings-modal system-prompt preview for a group role.

    Mirrors the current boot prompt paths instead of reimplementing prompt
    assembly in JavaScript:

    - Engineer: ``build_engineer_system_prompt(...)`` as used for designated
      engineer launch/relaunch.
    - Architect: Torque's persistent agent preamble plus
      ``build_architect_system_prompt(...)`` as used by
      ``_architect_persistent_prompt_text``.
    """
    normalized_kind = str(kind or "").strip().lower()
    group_name = str(group or "").strip() or "default"
    group_settings = _preview_group_settings_for_prompt(
        state, group_name, group_settings_payload)
    behavior_overlay_block = ""
    if normalized_kind in {"engineer", "architect"} and state is not None:
        try:
            preview_cell = SimpleNamespace(
                id=f"preview-{normalized_kind}",
                kind=normalized_kind,
                group=group_name,
            )
            # Group Settings previews are role-specific previews.  They show
            # the active group/kind role overlay and intentionally omit any
            # per-agent overlay so the modal does not imply a particular
            # Architect/Engineer instance is being previewed.
            behavior_overlay_block = state.render_behavior_overlay_stack_for_cell(
                preview_cell,
                include_role=True,
                include_agent=False,
                seed_agent=False,
                seed_role=False,
            )
        except Exception:
            log.exception(
                "failed to render role behavior overlay for system prompt "
                "preview group=%s kind=%s",
                group_name,
                normalized_kind,
            )

    if normalized_kind == "engineer":
        from .engineer import build_engineer_system_prompt

        engineer_settings = _preview_engineer_settings_for_prompt(
            state, group_name, settings_payload)
        return build_engineer_system_prompt(
            group_name,
            engineer_settings,
            action_system_prompt,
            group_settings=group_settings,
            specializations_preamble=specializations_preamble,
            behavior_overlay_block=behavior_overlay_block,
        ).rstrip() + "\n"

    if normalized_kind == "architect":
        from .architect import build_architect_system_prompt

        architect_settings = _preview_architect_settings_for_prompt(
            state, group_name, settings_payload)
        architect_body = build_architect_system_prompt(
            group_name,
            architect_settings=architect_settings,
            action_system_prompt=action_system_prompt,
            group_settings=group_settings,
            behavior_overlay_block=behavior_overlay_block,
        ).rstrip()
        torque_preamble = build_torque_system_prompt(
            include_shared_memory=False,
        ).rstrip()
        return torque_preamble + "\n\n" + architect_body + "\n"

    raise ValueError("kind must be 'engineer' or 'architect'")

def _agent_overrides_from_role_settings(kind: str, settings) -> dict:
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind == "engineer":
        mapping = {
            "engineer_provider": "provider",
            "engineer_boot_command": "command",
            "engineer_model": "model",
            "engineer_reasoning_effort": "reasoning_effort",
            "engineer_directory": "directory",
            "engineer_profile": "profile",
            "engineer_shell": "shell",
            "engineer_tab_color": "tab_color",
        }
    else:
        mapping = {
            "architect_provider": "provider",
            "architect_boot_command": "command",
            "architect_model": "model",
            "architect_reasoning_effort": "reasoning_effort",
            "architect_directory": "directory",
            "architect_profile": "profile",
            "architect_shell": "shell",
            "architect_tab_color": "tab_color",
        }
    out = {}
    for source, target in mapping.items():
        value = getattr(settings, source, "")
        if isinstance(value, str):
            value = value.strip()
        if value:
            out[target] = value
    return out

def _requested_agent_class_id(data: dict) -> str:
    return str(
        data.get("agent_class_id", data.get("class_id", "")) or ""
    ).strip()

def _apply_agent_class_launch_selection(
        data: dict,
        launch_cfg: dict,
        *,
        base_kind: str,
        base_dir: str) -> dict:
    """Validate a requested Agent Class and stamp the launch config.

    Absence of a class id is intentionally a no-op so default
    Architect/Engineer/Worker launch behavior remains unchanged.
    """

    class_id = _requested_agent_class_id(data)
    if not class_id:
        return {"ok": True, "agent_class": None}
    definition = agent_class_definition_by_id(class_id, base_dir=base_dir)
    if not definition:
        archived = agent_class_definition_by_id(
            class_id,
            base_dir=base_dir,
            include_archived=True,
        )
        suffix = " (archived/disabled)" if archived else ""
        return {
            "ok": False,
            "error": {
                "type": "error",
                "code": "invalid_agent_class",
                "message": f"Unknown or invalid Agent Class for launch: {class_id}{suffix}",
            },
        }
    if definition.base_kind != base_kind:
        return {
            "ok": False,
            "error": {
                "type": "error",
                "code": "agent_class_base_kind_mismatch",
                "message": (
                    f"Agent Class {definition.id} is for base_kind={definition.base_kind}, "
                    f"but launch kind is {base_kind}"
                ),
            },
        }
    launch_cfg["agent_class_id"] = definition.id
    launch_cfg["agent_class_version"] = definition.version
    return {
        "ok": True,
        "agent_class": enriched_agent_class_preview(
            definition,
            base_dir=base_dir,
        ),
    }

async def _handle_add_engineer_command(
        data: dict,
        state: MatrixState, *,
        resolve_base_dir,
        resolve_engineer_launch_config,
        create_agent_with_config,
        specialization_mgr=None,
        send_agent_prompt) -> dict:
    """Create and launch a persistent engineer agent."""
    name = str(data.get("name", "") or "").strip()
    if not name:
        return {"type": "error", "message": "Engineer name is required"}
    if _engineer_name_exists(state, name):
        return {
            "type": "error",
            "message": f"Engineer '{name}' already exists",
        }

    group = str(data.get("group", "") or "").strip() or _resolve_engineer_group(state)
    if group not in state.groups:
        state.add_group(group)
    base_dir = await resolve_base_dir(group)
    pending_specializations = _resolve_pending_engineer_specializations(
        data, state, group, True)
    overrides = {
        key: str(data.get(key, "") or "").strip()
        for key in ("command", "provider", "directory")
        if str(data.get(key, "") or "").strip()
    }
    launch_cfg = resolve_engineer_launch_config(
        group,
        base_dir=base_dir,
        explicit_template="",
        overrides=overrides,
    )
    class_launch = _apply_agent_class_launch_selection(
        data,
        launch_cfg,
        base_kind="engineer",
        base_dir=base_dir,
    )
    if not class_launch.get("ok"):
        return class_launch["error"]

    from .engineer import build_engineer_system_prompt

    spec_preamble = ""
    if pending_specializations and specialization_mgr is not None:
        try:
            spec_preamble = specialization_mgr.render_engineer_preamble(
                pending_specializations,
                base_dir=base_dir,
            )
        except Exception:
            log.exception(
                "failed to render specializations for new engineer in group=%s",
                group,
            )
    persistent_prompt_text = build_engineer_system_prompt(
        group,
        state.get_engineer_settings(group),
        launch_cfg.get("system_prompt", ""),
        group_settings=state.get_group_settings(group),
        specializations_preamble=spec_preamble,
        owner_is_user=not str(
            data.get("hired_by_architect_id", "") or "").strip(),
        agent_class_snapshot=(
            class_launch.get("agent_class")
            if isinstance(class_launch.get("agent_class"), dict)
            else {}
        ),
    )
    if isinstance(class_launch.get("agent_class"), dict):
        persistent_prompt_text = append_agent_class_prompt_block(
            persistent_prompt_text,
            SimpleNamespace(
                effective_agent_class_snapshot=class_launch["agent_class"],
            ),
        )
    startup_prompt = _startup_prompt_for_new_agent(
        agent_type=launch_cfg.get("agent_type", ""),
        persistent_prompt_text=persistent_prompt_text,
    )

    cell = await create_agent_with_config(
        group,
        name,
        launch_cfg,
        explicit_template="",
        persistent_prompt_text=persistent_prompt_text,
        created_by_engineer_id="",
        owner_engineer_id="",
        kind="engineer",
        persistent=True,
        hired_by_architect_id=str(
            data.get("hired_by_architect_id", "") or ""
        ).strip(),
    )
    if not cell:
        return {"type": "error", "message": "Failed to create engineer"}

    if pending_specializations:
        cell.engineer_specializations = list(pending_specializations)
        state._emit_agent(cell)
        state._db_save_agent(cell)

    if cell.session_id:
        for prompt_text, send_kwargs in _new_agent_prompt_sequence(
                launch_cfg,
                startup_prompt=startup_prompt,
                cell=cell,
                default_boot_nudge=resolve_default_boot_nudge(state, cell),
                include_identity_anchor=_should_show_guidance_hint(
                    state,
                    cell,
                    GUIDANCE_HINT_IDENTITY_LAUNCH,
                )):
            await send_agent_prompt(cell, prompt_text, **send_kwargs)
    return {
        "id": cell.id,
        "slug": cell.slug,
        "name": cell.name,
        "kind": "engineer",
        "agent_class": class_launch.get("agent_class"),
        "agent_class_status": state.agent_class_status_for_cell(
            cell,
            base_dir=base_dir,
        ),
        "specializations": list(
            getattr(cell, "engineer_specializations", []) or []
        ),
    }

async def _handle_add_architect_command(
        data: dict,
        state: MatrixState, *,
        resolve_base_dir,
        resolve_engineer_launch_config,
        create_agent_with_config,
        send_agent_prompt,
        resolve_architect_launch_config=None) -> dict:
    """Create and launch a persistent architect agent."""
    name = str(data.get("name", "") or "").strip()
    if not name:
        return {"type": "error", "message": "Architect name is required"}
    if _architect_name_exists(state, name):
        return {
            "type": "error",
            "message": f"Architect '{name}' already exists",
        }

    group = str(data.get("group", "") or "").strip() or _resolve_engineer_group(state)
    if group not in state.groups:
        state.add_group(group)
    base_dir = await resolve_base_dir(group)
    overrides = {
        key: str(data.get(key, "") or "").strip()
        for key in ("command", "provider", "directory")
        if str(data.get(key, "") or "").strip()
    }
    launch_resolver = resolve_architect_launch_config or resolve_engineer_launch_config
    launch_cfg = launch_resolver(
        group,
        base_dir=base_dir,
        explicit_template="",
        overrides=overrides,
    )
    class_launch = _apply_agent_class_launch_selection(
        data,
        launch_cfg,
        base_kind="architect",
        base_dir=base_dir,
    )
    if not class_launch.get("ok"):
        return class_launch["error"]
    if torque_config.ARCHITECT_USES_WORKTREE:
        launch_cfg["worktree"] = bool(
            launch_cfg.get("worktree")
            or state.get_group_settings(group).git_worktree
        )
    else:
        launch_cfg["worktree"] = False

    class_prompt_context = (
        class_launch.get("agent_class")
        if isinstance(class_launch.get("agent_class"), dict)
        else {}
    )
    persistent_prompt_text = _architect_persistent_prompt_text(
        group=group,
        action_system_prompt=launch_cfg.get("system_prompt", ""),
        state=state,
        agent_class_snapshot=class_prompt_context,
    )
    startup_prompt = _startup_prompt_for_new_agent(
        agent_type=launch_cfg.get("agent_type", ""),
        persistent_prompt_text=persistent_prompt_text,
    )

    cell = await create_agent_with_config(
        group,
        name,
        launch_cfg,
        explicit_template="",
        persistent_prompt_text=persistent_prompt_text,
        created_by_engineer_id="",
        owner_engineer_id="",
        kind="architect",
        persistent=True,
        hired_by_architect_id="",
    )
    if not cell:
        return {"type": "error", "message": "Failed to create architect"}

    startup_prompt = _startup_prompt_for_new_agent(
        agent_type=launch_cfg.get("agent_type", ""),
        persistent_prompt_text=append_agent_class_prompt_block(
            persistent_prompt_text,
            cell,
        ),
    )

    if cell.session_id:
        for prompt_text, send_kwargs in _new_agent_prompt_sequence(
                launch_cfg,
                startup_prompt=startup_prompt,
                cell=cell,
                default_boot_nudge=resolve_default_boot_nudge(state, cell),
                include_identity_anchor=_should_show_guidance_hint(
                    state,
                    cell,
                    GUIDANCE_HINT_IDENTITY_LAUNCH,
                )):
            await send_agent_prompt(cell, prompt_text, **send_kwargs)
    return {
        "id": cell.id,
        "slug": cell.slug,
        "name": cell.name,
        "kind": "architect",
        "agent_class": class_launch.get("agent_class"),
        "agent_class_status": state.agent_class_status_for_cell(
            cell,
            base_dir=base_dir,
        ),
    }

async def _handle_add_worker_command(
        data: dict,
        state: MatrixState, *,
        resolve_base_dir,
        resolve_agent_launch_config,
        resolve_worker_launch_config=None,
        create_agent_with_config,
        send_agent_prompt) -> dict:
    """Create and launch a user-owned detached worker agent."""
    name = str(data.get("name", "") or "").strip()
    if not name:
        return {"type": "error", "message": "Worker name is required"}

    supplied_owner = str(data.get("owner_engineer_id", "") or "").strip()
    supplied_legacy_owner = str(
        data.get("created_by_engineer_id", "")
        or data.get("_created_by_engineer_id", "")
        or ""
    ).strip()
    if supplied_owner or supplied_legacy_owner:
        return {
            "type": "error",
            "message": (
                "Detached worker creation does not accept "
                "owner_engineer_id"
            ),
        }

    group = str(data.get("group", "") or "").strip() or _resolve_engineer_group(state)
    if group not in state.groups:
        state.add_group(group)
    base_dir = await resolve_base_dir(group)
    explicit_template = str(data.get("template", "") or "").strip()
    overrides = dict(data)
    for key in (
            "cmd",
            "name",
            "group",
            "kind",
            "owner_engineer_id",
            "created_by_engineer_id",
            "_created_by_engineer_id",
            "hired_by_architect_id",
    ):
        overrides.pop(key, None)
    launch_resolver = resolve_worker_launch_config or resolve_agent_launch_config
    launch_cfg = launch_resolver(
        group,
        base_dir=base_dir,
        explicit_template=explicit_template,
        overrides=overrides,
    )
    class_launch = _apply_agent_class_launch_selection(
        data,
        launch_cfg,
        base_kind="worker",
        base_dir=base_dir,
    )
    if not class_launch.get("ok"):
        return class_launch["error"]
    startup_prompt = _startup_prompt_for_new_agent(
        agent_type=launch_cfg.get("agent_type", ""),
        persistent_prompt_text="",
    )

    cell = await create_agent_with_config(
        group,
        name,
        launch_cfg,
        explicit_template=explicit_template,
        persistent_prompt_text="",
        created_by_engineer_id="",
        owner_engineer_id="",
        kind="worker",
        persistent=False,
        hired_by_architect_id="",
    )
    if not cell:
        return {"type": "error", "message": "Failed to create worker"}

    if cell.session_id:
        for prompt_text, send_kwargs in _new_agent_prompt_sequence(
                launch_cfg,
                startup_prompt=startup_prompt):
            await send_agent_prompt(cell, prompt_text, **send_kwargs)
    return {
        "id": cell.id,
        "slug": cell.slug,
        "name": cell.name,
        "kind": "worker",
        "agent_class": class_launch.get("agent_class"),
        "agent_class_status": state.agent_class_status_for_cell(
            cell,
            base_dir=base_dir,
        ),
    }

async def _handle_agent_class_launch_command(
        data: dict,
        state: MatrixState, *,
        resolve_base_dir,
        resolve_agent_launch_config,
        resolve_engineer_launch_config,
        resolve_architect_launch_config=None,
        resolve_worker_launch_config=None,
        create_agent_with_config,
        specialization_mgr=None,
        send_agent_prompt) -> dict:
    """Create a new Architect/Engineer/Worker from a saved Agent Class."""

    class_id = _requested_agent_class_id(data)
    if not class_id:
        return {"type": "error", "message": "Agent Class id is required"}
    group = str(data.get("group", "") or "").strip() or _resolve_engineer_group(state)
    if group not in state.groups:
        state.add_group(group)
    base_dir = str(data.get("base_dir", "") or "").strip() or await resolve_base_dir(group)
    definition = agent_class_definition_by_id(class_id, base_dir=base_dir)
    if not definition:
        archived = agent_class_definition_by_id(
            class_id,
            base_dir=base_dir,
            include_archived=True,
        )
        suffix = " (archived/disabled)" if archived else ""
        return {
            "type": "error",
            "code": "invalid_agent_class",
            "message": f"Unknown or invalid Agent Class for launch: {class_id}{suffix}",
        }
    requested_kind = str(data.get("kind", "") or "").strip()
    if requested_kind and requested_kind != definition.base_kind:
        return {
            "type": "error",
            "code": "agent_class_base_kind_mismatch",
            "message": (
                f"Agent Class {definition.id} is for base_kind={definition.base_kind}, "
                f"but requested kind is {requested_kind}"
            ),
        }
    payload = dict(data)
    payload["group"] = group
    payload["agent_class_id"] = definition.id
    payload.pop("class_id", None)
    if definition.base_kind == "architect":
        created = await _handle_add_architect_command(
            payload,
            state,
            resolve_base_dir=resolve_base_dir,
            resolve_engineer_launch_config=resolve_engineer_launch_config,
            resolve_architect_launch_config=resolve_architect_launch_config,
            create_agent_with_config=create_agent_with_config,
            send_agent_prompt=send_agent_prompt,
        )
    elif definition.base_kind == "engineer":
        created = await _handle_add_engineer_command(
            payload,
            state,
            resolve_base_dir=resolve_base_dir,
            resolve_engineer_launch_config=resolve_engineer_launch_config,
            create_agent_with_config=create_agent_with_config,
            specialization_mgr=specialization_mgr,
            send_agent_prompt=send_agent_prompt,
        )
    elif definition.base_kind == "worker":
        created = await _handle_add_worker_command(
            payload,
            state,
            resolve_base_dir=resolve_base_dir,
            resolve_agent_launch_config=resolve_agent_launch_config,
            resolve_worker_launch_config=resolve_worker_launch_config,
            create_agent_with_config=create_agent_with_config,
            send_agent_prompt=send_agent_prompt,
        )
    else:
        return {
            "type": "error",
            "code": "invalid_base_kind",
            "message": f"Unsupported Agent Class base_kind: {definition.base_kind}",
        }
    if isinstance(created, dict) and created.get("type") == "error":
        return created
    return {
        "type": "agent_class_launch",
        "schema_version": AGENT_CLASS_SCHEMA_VERSION,
        "agent": created,
        "agent_class": enriched_agent_class_preview(
            definition,
            base_dir=base_dir,
        ),
        "base_kind": definition.base_kind,
        "storage": {
            "mutates_running_sessions": False,
            "launch_boundary": "new_agent",
        },
    }

async def _handle_architect_engineer_hire_command(
        data: dict,
        state: MatrixState, *,
        resolve_base_dir=None,
        specialization_mgr=None) -> dict:
    """Queue a user-approved pending hire for an architect."""
    architect = _resolve_architect_cell(
        state,
        architect_id=data.get("architect_id", ""),
    )
    if not architect:
        return {"type": "error", "message": "Architect not found"}
    if _agent_dismissed_at(architect):
        return _architect_dismissed_error(architect.id)
    if not architect.group:
        return {"type": "error", "message": "Architect is not assigned to a group"}

    name = str(data.get("name", "") or "").strip()
    if not name:
        return {"type": "error", "message": "Engineer name is required"}

    requested_specializations = []
    if "specializations" in data:
        try:
            if not callable(resolve_base_dir) or specialization_mgr is None:
                raise ValueError("specialization validation is unavailable")
            base_dir = await resolve_base_dir(architect.group)
            requested_specializations = (
                _normalize_engineer_specialization_selection(
                    data.get("specializations"),
                    valid_names=_project_specialization_names(
                        specialization_mgr,
                        base_dir,
                    ),
                )
            )
        except ValueError as exc:
            return {"type": "error", "message": str(exc)}

    pending_hire = await state.save_pending_hire_async({
        "id": "hire-" + uuid.uuid4().hex[:12],
        "architect_id": architect.id,
        "requested_name": name,
        "requested_command": str(data.get("command", "") or "").strip(),
        "requested_provider": str(data.get("provider", "") or "").strip(),
        "requested_directory": str(data.get("directory", "") or "").strip(),
        "requested_specializations": requested_specializations,
        "status": "pending",
        "resolution_note": "",
        "created_engineer_id": "",
    })
    if not pending_hire:
        return {"type": "error", "message": "Failed to create pending hire"}
    return {
        "hire_id": pending_hire["id"],
        "status": pending_hire["status"],
        "requested_specializations": list(
            pending_hire.get("requested_specializations", []) or []
        ),
    }

async def _handle_pending_hire_approve_command(
        data: dict,
        state: MatrixState, *,
        resolve_base_dir,
        resolve_engineer_launch_config,
        create_agent_with_config,
        specialization_mgr=None,
        send_agent_prompt) -> dict:
    """Approve a pending architect hire and create the engineer."""
    pending_hire = state.load_pending_hire(data.get("id", ""))
    if not pending_hire:
        return {"type": "error", "message": "Pending hire not found"}

    if pending_hire["status"] == "approved":
        engineer = state.agents.get(pending_hire.get("created_engineer_id", ""))
        return {
            "engineer_id": str(pending_hire.get("created_engineer_id", "") or ""),
            "slug": str(getattr(engineer, "slug", "") or ""),
        }
    if pending_hire["status"] == "rejected":
        return {"type": "error", "message": "Pending hire has already been rejected"}

    architect = _resolve_architect_cell(
        state,
        architect_id=pending_hire.get("architect_id", ""),
    )
    if not architect:
        return {"type": "error", "message": "Architect not found for pending hire"}
    if _agent_dismissed_at(architect):
        return _architect_dismissed_error(architect.id)
    if not architect.group:
        return {"type": "error", "message": "Architect is not assigned to a group"}

    created = await _handle_add_engineer_command(
        {
            "name": pending_hire.get("requested_name", ""),
            "command": pending_hire.get("requested_command", ""),
            "provider": pending_hire.get("requested_provider", ""),
            "directory": pending_hire.get("requested_directory", ""),
            "specializations": list(
                pending_hire.get("requested_specializations", []) or []
            ),
            "group": architect.group,
            "hired_by_architect_id": architect.id,
        },
        state,
        resolve_base_dir=resolve_base_dir,
        resolve_engineer_launch_config=resolve_engineer_launch_config,
        create_agent_with_config=create_agent_with_config,
        specialization_mgr=specialization_mgr,
        send_agent_prompt=send_agent_prompt,
    )
    if created.get("type") == "error":
        return created

    saved = await state.save_pending_hire_async({
        "id": pending_hire["id"],
        "status": "approved",
        "resolution_note": str(data.get("note", "") or "").strip(),
        "created_engineer_id": created["id"],
    })
    if not saved:
        return {"type": "error", "message": "Failed to resolve pending hire"}
    return {
        "engineer_id": created["id"],
        "slug": created["slug"],
        "specializations": list(created.get("specializations", []) or []),
    }

async def _handle_pending_hire_reject_command(
        data: dict,
        state: MatrixState) -> dict:
    """Reject a pending architect hire request."""
    pending_hire = state.load_pending_hire(data.get("id", ""))
    if not pending_hire:
        return {"type": "error", "message": "Pending hire not found"}
    if pending_hire["status"] == "approved":
        return {"type": "error", "message": "Pending hire has already been approved"}
    if pending_hire["status"] == "rejected":
        return {"ok": True}

    saved = await state.save_pending_hire_async({
        "id": pending_hire["id"],
        "status": "rejected",
        "resolution_note": str(data.get("note", "") or "").strip(),
        "created_engineer_id": "",
    })
    if not saved:
        return {"type": "error", "message": "Failed to resolve pending hire"}
    return {"ok": True}

async def _handle_set_engineer_specializations_command(
        data: dict,
        state: MatrixState, *,
        resolve_base_dir,
        specialization_mgr,
        architect_id: str = "") -> dict:
    """Full-replace an engineer's ordered specialization list."""
    engineer_ident = str(data.get("engineer_id", "") or "").strip()
    if not engineer_ident:
        return {
            "type": "error",
            "message": "engineer_id is required",
        }
    agent_id = _resolve_agent_id(state, engineer_ident)
    cell = state.agents.get(agent_id) if agent_id else None
    scoped_architect_id = str(
        architect_id or data.get("architect_id", "") or ""
    ).strip()
    if not cell or cell.kind != "engineer":
        message = (
            "engineer not found in scope"
            if scoped_architect_id
            else f"Engineer \"{engineer_ident}\" not found"
        )
        return {"type": "error", "message": message}
    if scoped_architect_id:
        architect = _resolve_architect_cell(
            state,
            architect_id=scoped_architect_id,
        )
        if not architect:
            return {"type": "error", "message": "Architect not found"}
        hired_by = str(
            getattr(cell, "hired_by_architect_id", "") or ""
        ).strip()
        if hired_by != architect.id:
            return {"type": "error", "message": "engineer not found in scope"}
        if _agent_dismissed_at(architect):
            return _architect_dismissed_error(architect.id)
    if state.agent_is_tombstoned(cell):
        return {"type": "error", "message": "engineer is tombstoned"}
    try:
        base_dir = await resolve_base_dir(cell.group)
        names = _normalize_engineer_specialization_selection(
            data.get("specializations", []),
            valid_names=_project_specialization_names(
                specialization_mgr,
                base_dir,
            ),
        )
    except ValueError as exc:
        return {
            "type": "error",
            "message": str(exc),
        }
    if list(getattr(cell, "engineer_specializations", []) or []) != names:
        cell.engineer_specializations = list(names)
        state._emit_agent(cell)
        state._db_save_agent(cell)
    return {
        "type": "engineer_specializations",
        "engineer_id": cell.id,
        "specializations": list(names),
        "primary_specialization": names[0] if names else "",
    }

def _handle_pending_hire_list_command(data: dict, state: MatrixState) -> dict:
    """Return pending-hire rows for the UI or architect-scoped polling."""
    status_filter = str(data.get("status_filter", "") or "").strip()
    architect_id = str(data.get("architect_id", "") or "").strip()
    return {
        "pending_hires": state.load_pending_hires(
            status_filter=status_filter,
            architect_id=architect_id,
        )
    }
