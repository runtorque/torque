"""Scoped action editor reads, previews and file lifecycle operations."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from ..actions import parse_yaml

ACTION_AUTHORING_COMMANDS = frozenset({
    "get_action", "render_action", "save_action", "delete_action",
})


def _name(value):
    name = str(value or "").strip()
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", name)
            or any(part in {".", ".."} for part in name.split("/"))):
        raise ValueError("Action name must use letters, numbers, dots, dashes, underscores or namespace folders")
    return name


def _scope(value):
    scope = str(value or "").strip()
    if scope not in {"", "project", "user"}:
        raise ValueError("Action scope must be project or user")
    return scope


def _directories(manager, base_dir, scope):
    user = Path.home() / ".torque" / "actions"
    if scope == "user":
        return [user]
    # Discovery's legacy cwd fallback must not redirect authoring for a new path.
    if base_dir and not Path(base_dir).expanduser().is_dir():
        directories = [user] if user.is_dir() else []
    else:
        directories = [Path(path) for path in manager.find_actions_dirs(base_dir)]
    if scope == "project":
        directories = [path for path in directories if path.resolve() != user.resolve()]
    return directories


def _existing(manager, base_dir, name, scope):
    for directory in _directories(manager, base_dir, scope):
        for suffix in ("", ".yaml", ".yml"):
            path = directory / (name + suffix)
            if path.is_file():
                return path
    return None


def _path_scope(path):
    user = (Path.home() / ".torque" / "actions").resolve()
    return "user" if path.resolve().is_relative_to(user) else "project"


def _definition(value):
    if not isinstance(value, dict):
        raise ValueError("Action definition must be an object")
    if not isinstance(value.get("prompt", ""), str):
        raise ValueError("Action prompt must be text")
    agent = value.get("agent", {})
    if not isinstance(agent, (str, dict)):
        raise ValueError("Action agent must be a role name or an object")
    for key in ("transitions", "terminals"):
        if not isinstance(value.get(key, []), list) or any(not isinstance(item, dict) for item in value.get(key, [])):
            raise ValueError(f"Action {key} must be a list of objects")
    return value


def _write(path, contents):
    """Replace one file only after its complete new contents are written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".action-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def handle_action_authoring_command(data, runtime):
    manager = runtime.action_mgr
    group = data.get("group", "")
    base_dir = await runtime.resolve_base_dir(group)
    cmd = data["cmd"]
    try:
        scope = _scope(data.get("scope"))
        # A draft preview does not require an existing file or a final name.
        name = str(data.get("name", "") or "").strip()
        if cmd == "render_action" and "action" in data:
            action = _definition(data["action"])
        elif cmd in {"get_action", "render_action", "delete_action"}:
            name = _name(name)
            path = _existing(manager, base_dir, name, scope)
            if path is None:
                raise ValueError(f'Action "{name}" not found in {scope or "available"} scope')
            scope = _path_scope(path)
            if cmd != "delete_action":
                action = _definition(parse_yaml(path.read_text(encoding="utf-8")))

        if cmd == "get_action":
            return {"type": "action_detail", "name": name, "group": group,
                    "scope": scope, "path": str(path), "action": action,
                    "vars": manager.get_action_vars(action)}
        if cmd == "render_action":
            variables = data.get("vars", {})
            if not isinstance(variables, dict) or "torque" in variables:
                raise ValueError("Preview variables must be an object without the reserved torque namespace")
            discovered = manager.get_action_vars(action)
            if data.get("variables_only"):
                return {"type": "action_variables", "name": name, "scope": scope,
                        "workspace_group": group, "vars": discovered}
            rendered = manager.render_action(action, variables)
            return {"type": "action_rendered", "name": name, "scope": scope,
                    "workspace_group": group, "vars": discovered,
                    "prompt": rendered.get("prompt", ""),
                    "group": rendered.get("group", ""), "labels": rendered.get("labels", [])}
        if cmd == "save_action":
            name = _name(name)
            scope = scope or "project"
            old_name = _name(data["old_name"]) if data.get("old_name") else ""
            old_scope = _scope(data.get("old_scope"))
            action = _definition(data.get("action", {}))
            if not manager.validate_prompt(action.get("prompt", "")):
                raise ValueError("Action prompt must contain {{ TASK }} or {{ torque.task.title }}")
            # Resolve both identities before creating a destination or removing a source.
            old_path = _existing(manager, base_dir, old_name, old_scope) if old_name else None
            path = _existing(manager, base_dir, name, scope)
            if path is None:
                directories = _directories(manager, base_dir, scope)
                directory = directories[0] if directories else Path(base_dir or os.getcwd()).expanduser() / ".torque" / "actions"
                path = directory / (name + ".yaml")
            contents = runtime.action_to_yaml(name, action)
            _write(path, contents)
            if old_path is not None and old_path.resolve() != path.resolve():
                old_path.unlink()
            return {"type": "actions", "group": group, "scope": scope,
                    "actions": manager.list_actions(base_dir), "saved": name}
        if cmd == "delete_action":
            path.unlink()
            return {"type": "actions", "group": group, "scope": scope,
                    "actions": manager.list_actions(base_dir), "deleted": name}
    except (ValueError, OSError, TypeError) as exc:
        return {"type": "error", "message": str(exc)}
    return None
