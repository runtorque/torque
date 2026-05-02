"""Role discovery, validation, storage, and config resolution."""

from __future__ import annotations

import os

import yaml

from .actions import parse_yaml
from .config import log


class _BlockStr(str):
    """Tagged string subclass so the YAML dumper emits block scalars."""


class _TemplateDumper(yaml.SafeDumper):
    pass


_TemplateDumper.add_representer(
    _BlockStr,
    lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style="|"),
)

_TEMPLATE_KEY_ORDER = [
    "name",
    "display_name",
    "description",
    "provider",
    "command",
    "model",
    "reasoning_effort",
    "permissions",
    "max_turns",
    "system_prompt",
    "initial_prompt",
    "session_resume",
    "idle_timeout",
    "tab_color",
    "icon",
    "worktree",
    "worktree_base_branch",
    "worktree_auto_checkpoint",
    "checkpoint_on_progress",
    "worktree_merge_squash",
    "env_vars",
    "env_file",
    "terminals",
]

_SCALAR_KEYS = {
    "name",
    "display_name",
    "description",
    "provider",
    "command",
    "model",
    "reasoning_effort",
    "permissions",
    "system_prompt",
    "initial_prompt",
    "tab_color",
    "icon",
    "worktree_base_branch",
    "env_file",
}

_INT_KEYS = {"max_turns", "idle_timeout"}
_BOOL_KEYS = {
    "session_resume",
    "worktree",
    "worktree_auto_checkpoint",
    "checkpoint_on_progress",
    "worktree_merge_squash",
}

_KNOWN_KEYS = _SCALAR_KEYS | _INT_KEYS | _BOOL_KEYS | {"env_vars", "terminals"}
_RUNTIME_OVERRIDE_KEYS = {
    "directory",
    "profile",
    "shell",
    "worktree_base_dir",
    "worktree_name",
}

_ROLE_KEY_ORDER = [
    *(_TEMPLATE_KEY_ORDER[:11]),
    "preamble",
    "priorities",
    *(_TEMPLATE_KEY_ORDER[11:]),
]
_ROLE_KNOWN_KEYS = _KNOWN_KEYS | {"preamble", "priorities"}


def _normalize_terminals(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    result = []
    for term in raw:
        if not isinstance(term, dict):
            continue
        name = str(term.get("name", "") or "").strip()
        command = str(term.get("command", "") or "").strip()
        entry = {}
        if name:
            entry["name"] = name
        if command:
            entry["command"] = command
        if entry:
            result.append(entry)
    return result


def _normalize_template_data(data: dict | None, name_hint: str = "",
                             allow_runtime_overrides: bool = False) -> dict:
    data = dict(data or {})
    result = {}

    name = str(data.get("name", "") or name_hint or "").strip()
    if name:
        result["name"] = name

    for key in _SCALAR_KEYS - {"name"}:
        value = data.get(key, "")
        if value is None:
            continue
        value = str(value).strip()
        if value:
            result[key] = value

    for key in _INT_KEYS:
        value = data.get(key, None)
        if value in (None, ""):
            continue
        try:
            result[key] = int(value)
        except (TypeError, ValueError):
            continue

    for key in _BOOL_KEYS:
        if key in data and data.get(key) is not None:
            result[key] = bool(data.get(key))

    env_vars = data.get("env_vars", {})
    if isinstance(env_vars, dict):
        clean_env = {}
        for key, value in env_vars.items():
            if key is None or value is None:
                continue
            k = str(key).strip()
            if not k:
                continue
            clean_env[k] = str(value)
        if clean_env:
            result["env_vars"] = clean_env

    terminals = _normalize_terminals(data.get("terminals"))
    if terminals:
        result["terminals"] = terminals

    if allow_runtime_overrides:
        for key in _RUNTIME_OVERRIDE_KEYS:
            value = data.get(key, "")
            if value is None:
                continue
            value = str(value).strip()
            if value:
                result[key] = value

    return result


def _merge_agent_config(base: dict, more_specific: dict) -> dict:
    merged = dict(base)
    for key, value in (more_specific or {}).items():
        if key == "env_vars":
            env = dict(merged.get("env_vars", {}))
            env.update(value or {})
            if env:
                merged["env_vars"] = env
            elif "env_vars" in merged:
                merged.pop("env_vars", None)
            continue
        if key == "terminals":
            merged["terminals"] = list(value or [])
            continue
        if isinstance(value, str):
            if value:
                merged[key] = value
            continue
        if value is not None:
            merged[key] = value
    return merged


def _normalize_priorities(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    priorities = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            priorities.append(text)
    return priorities


def _normalize_role_data(data: dict | None, name_hint: str = "",
                         allow_runtime_overrides: bool = False) -> dict:
    result = _normalize_template_data(
        data, name_hint=name_hint,
        allow_runtime_overrides=allow_runtime_overrides,
    )
    data = dict(data or {})

    preamble = data.get("preamble", "")
    if preamble is not None:
        preamble = str(preamble).strip()
        if preamble:
            result["preamble"] = preamble

    priorities = _normalize_priorities(data.get("priorities"))
    if priorities:
        result["priorities"] = priorities

    return result


class RoleManager:
    """Primary role manager. Role files live only under ``roles/``."""

    GLOBAL_ROLES_DIR = os.path.expanduser("~/.torque/roles")
    _WARNED_IGNORED_LEGACY_FILES: set[str] = set()

    def __init__(self):
        self._warn_ignored_legacy_templates(scope="user")

    @classmethod
    def _display_path(cls, path: str) -> str:
        raw = str(path or "")
        home = os.path.expanduser("~")
        if raw == home:
            return "~"
        if raw.startswith(home + os.sep):
            return "~/" + raw[len(home) + 1:]
        return raw

    @classmethod
    def _find_project_dir(cls, base_dir: str = "",
                          leaf: str = "roles") -> str | None:
        d = os.path.expanduser(base_dir) if base_dir else os.getcwd()
        if not os.path.isdir(d):
            d = os.getcwd()
        global_dir = os.path.realpath(cls._global_dir(leaf))
        for _ in range(20):
            candidate = os.path.join(d, ".torque", leaf)
            if os.path.isdir(candidate):
                if os.path.realpath(candidate) != global_dir:
                    return candidate
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        return None

    @staticmethod
    def _default_project_dir(base_dir: str = "", leaf: str = "roles") -> str:
        d = os.path.expanduser(base_dir) if base_dir else os.getcwd()
        return os.path.join(d, ".torque", leaf)

    @classmethod
    def _default_project_role_dir(cls, base_dir: str = "") -> str:
        return cls.find_project_role_dir(base_dir) or cls._default_project_dir(
            base_dir, "roles"
        )

    @classmethod
    def _global_dir(cls, leaf: str = "roles") -> str:
        return os.path.expanduser(os.path.join("~/.torque", leaf))

    @classmethod
    def find_roles_dirs(cls, base_dir: str = "") -> list[str]:
        dirs = []
        project_dir = cls._find_project_dir(base_dir, "roles")
        if project_dir:
            dirs.append(project_dir)
        global_dir = cls._global_dir("roles")
        if os.path.isdir(global_dir) and global_dir not in dirs:
            dirs.append(global_dir)
        return dirs

    @classmethod
    def find_role_dir(cls, base_dir: str = "") -> str | None:
        dirs = cls.find_roles_dirs(base_dir)
        return dirs[0] if dirs else None

    @classmethod
    def find_project_role_dir(cls, base_dir: str = "") -> str | None:
        return cls._find_project_dir(base_dir, "roles")

    @classmethod
    def _legacy_scope_pairs(
        cls, base_dir: str = "", scope: str = ""
    ) -> list[tuple[str, str, bool]]:
        pairs: list[tuple[str, str, bool]] = []
        if scope != "user":
            project_legacy_dir = cls._find_project_dir(base_dir, "agents")
            if project_legacy_dir:
                pairs.append(
                    (
                        project_legacy_dir,
                        cls._default_project_dir(base_dir, "roles"),
                        False,
                    )
                )
        if scope != "project":
            global_legacy_dir = cls._global_dir("agents")
            if os.path.isdir(global_legacy_dir):
                pairs.append((global_legacy_dir, cls._global_dir("roles"), True))
        unique: list[tuple[str, str, bool]] = []
        seen_legacy_dirs: set[str] = set()
        for legacy_dir, role_dir, is_global in pairs:
            real_dir = os.path.realpath(legacy_dir)
            if real_dir in seen_legacy_dirs:
                continue
            seen_legacy_dirs.add(real_dir)
            unique.append((legacy_dir, role_dir, is_global))
        return unique

    @staticmethod
    def _iter_named_yaml_paths(root_dir: str) -> list[tuple[str, str]]:
        entries = []
        if not root_dir or not os.path.isdir(root_dir):
            return entries
        for dirpath, _dirnames, filenames in os.walk(root_dir):
            for fname in sorted(filenames):
                if not fname.endswith((".yaml", ".yml")):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fname), root_dir)
                entries.append((rel.rsplit(".", 1)[0], os.path.join(dirpath, fname)))
        return entries

    @classmethod
    def _ignored_legacy_template_entries(
        cls, base_dir: str = "", scope: str = ""
    ) -> list[tuple[str, str]]:
        ignored = []
        for legacy_dir, role_dir, _is_global in cls._legacy_scope_pairs(base_dir, scope):
            role_slugs = {
                name for name, _path in cls._iter_named_yaml_paths(role_dir)
            }
            for name, legacy_path in cls._iter_named_yaml_paths(legacy_dir):
                if name in role_slugs:
                    continue
                ignored.append((legacy_path, role_dir))
        return ignored

    @classmethod
    def _warn_ignored_legacy_templates(cls, base_dir: str = "", scope: str = "") -> None:
        for legacy_path, role_dir in cls._ignored_legacy_template_entries(base_dir, scope):
            real_path = os.path.realpath(legacy_path)
            if real_path in cls._WARNED_IGNORED_LEGACY_FILES:
                continue
            cls._WARNED_IGNORED_LEGACY_FILES.add(real_path)
            role_dir_display = cls._display_path(role_dir)
            if not role_dir_display.endswith("/"):
                role_dir_display += "/"
            log.warning(
                "legacy template file at %s is ignored; move it to %s to use as a role",
                cls._display_path(legacy_path),
                role_dir_display,
            )

    @classmethod
    def _source_dirs(cls, base_dir: str = "", scope: str = "") -> list[tuple[str, bool]]:
        dirs: list[tuple[str, bool]] = []
        if scope == "project":
            project_role_dir = cls.find_project_role_dir(base_dir)
            if project_role_dir:
                dirs.append((project_role_dir, False))
            return dirs
        if scope == "user":
            global_role_dir = cls._global_dir("roles")
            if os.path.isdir(global_role_dir):
                dirs.append((global_role_dir, True))
            return dirs

        project_role_dir = cls.find_project_role_dir(base_dir)
        if project_role_dir:
            dirs.append((project_role_dir, False))
        global_role_dir = cls._global_dir("roles")
        if os.path.isdir(global_role_dir):
            dirs.append((global_role_dir, True))
        return dirs

    def _load_entry_meta(self, entry: dict) -> dict:
        name = entry["name"]
        try:
            with open(entry["path"], encoding="utf-8") as f:
                raw = f.read()
            meta = parse_yaml(raw) or {}
            meta = _normalize_role_data(meta, name_hint=name)
        except Exception:
            meta = {"name": name}
        return meta

    def _load_raw(self, name: str, base_dir: str = "",
                  scope: str = "") -> str | None:
        self._warn_ignored_legacy_templates(base_dir, scope)
        name = str(name or "").strip()
        if not name:
            return None
        for root_dir, _is_global in self._source_dirs(base_dir, scope):
            if not root_dir:
                continue
            for suffix in (".yaml", ".yml"):
                path = os.path.join(root_dir, name + suffix)
                if os.path.isfile(path):
                    with open(path, encoding="utf-8") as f:
                        return f.read()
        return None

    def list_roles(self, base_dir: str = "") -> list[dict]:
        self._warn_ignored_legacy_templates(base_dir)
        results = []
        seen_names = set()
        for root_dir, is_global in self._source_dirs(base_dir):
            if not root_dir or not os.path.isdir(root_dir):
                continue
            for name, path in self._iter_named_yaml_paths(root_dir):
                meta = self._load_entry_meta({"name": name, "path": path})
                shadowed = is_global and name in seen_names
                results.append({
                    "name": name,
                    "display_name": meta.get("display_name", ""),
                    "description": meta.get("description", ""),
                    "provider": meta.get("provider", ""),
                    "preamble": meta.get("preamble", ""),
                    "priorities": list(meta.get("priorities", [])),
                    "global": is_global,
                    "dir": root_dir,
                    "shadowed": shadowed,
                    "legacy": False,
                })
                if not is_global:
                    seen_names.add(name)
        return sorted(results, key=lambda role: (role["global"], role["name"]))

    def list_templates(self, base_dir: str = "") -> list[dict]:
        return self.list_roles(base_dir)

    def load_role(self, name: str, base_dir: str = "",
                  scope: str = "") -> dict | None:
        raw = self._load_raw(name, base_dir, scope=scope)
        if raw is None:
            return None
        try:
            parsed = parse_yaml(raw) or {}
        except Exception:
            return None
        return _normalize_role_data(parsed, name_hint=name)

    def load_template(self, name: str, base_dir: str = "",
                      scope: str = "") -> dict | None:
        return self.load_role(name, base_dir, scope=scope)

    def save_role(self, name: str, data: dict,
                  scope: str = "project", base_dir: str = "") -> str:
        self._warn_ignored_legacy_templates(base_dir, scope)
        clean = _normalize_role_data(data, name_hint=name)
        clean["name"] = name
        unknown = sorted(set(data or {}) - _ROLE_KNOWN_KEYS - {"name"})
        if unknown:
            raise ValueError("Unknown role fields: " + ", ".join(unknown))
        if scope == "user":
            role_dir = self._global_dir("roles")
            os.makedirs(role_dir, exist_ok=True)
        else:
            role_dir = self.find_project_role_dir(base_dir)
            if not role_dir:
                role_dir = self._default_project_role_dir(base_dir)
                os.makedirs(role_dir, exist_ok=True)
        path = os.path.join(role_dir, name + ".yaml")
        os.makedirs(os.path.dirname(path), exist_ok=True)

        ordered = {}
        for key in _ROLE_KEY_ORDER:
            if key not in clean:
                continue
            value = clean[key]
            if key in ("system_prompt", "initial_prompt", "preamble") and value:
                ordered[key] = _BlockStr(value.rstrip("\n") + "\n")
            else:
                ordered[key] = value
        with open(path, "w", encoding="utf-8") as f:
            f.write(yaml.dump(
                ordered,
                Dumper=_TemplateDumper,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            ))
        return path

    def save_template(self, name: str, data: dict,
                      scope: str = "project", base_dir: str = "") -> str:
        return self.save_role(name, data, scope=scope, base_dir=base_dir)

    def delete_role(self, name: str,
                    scope: str = "", base_dir: str = "") -> bool:
        self._warn_ignored_legacy_templates(base_dir, scope)
        return self.delete_template(name, scope=scope, base_dir=base_dir)

    def delete_template(self, name: str,
                        scope: str = "", base_dir: str = "") -> bool:
        self._warn_ignored_legacy_templates(base_dir, scope)
        for role_dir, _is_global in self._source_dirs(base_dir, scope):
            if not role_dir:
                continue
            for suffix in (".yaml", ".yml"):
                path = os.path.join(role_dir, name + suffix)
                if os.path.isfile(path):
                    os.remove(path)
                    return True
        return False

    def resolve_agent_config(self, template_name: str,
                             group_settings,
                             overrides: dict | None,
                             base_dir: str = "") -> dict:
        self._warn_ignored_legacy_templates(base_dir)
        result = {}
        effective_template = ""

        default_template = str(
            getattr(group_settings, "default_agent_template", "") or ""
        ).strip()
        if default_template:
            tpl = self.load_template(default_template, base_dir)
            if tpl:
                result = _merge_agent_config(result, tpl)
                effective_template = default_template

        group_overrides = {
            "directory": getattr(group_settings, "agent_directory", ""),
            "profile": getattr(group_settings, "agent_profile", ""),
            "shell": getattr(group_settings, "agent_shell", ""),
            "tab_color": getattr(group_settings, "agent_tab_color", ""),
            "env_vars": getattr(group_settings, "agent_env_vars", {}),
            "provider": getattr(group_settings, "agent_provider", ""),
            "command": getattr(group_settings, "agent_boot_command", ""),
            "model": getattr(group_settings, "agent_model", ""),
            "reasoning_effort": getattr(
                group_settings, "agent_reasoning_effort", ""
            ),
            "worktree": getattr(group_settings, "git_worktree", False),
            "worktree_base_dir": getattr(
                group_settings, "worktree_base_dir", ""
            ),
            "worktree_base_branch": getattr(
                group_settings, "worktree_base_branch", ""
            ),
            "worktree_auto_checkpoint": getattr(
                group_settings, "worktree_auto_checkpoint", False
            ),
            "checkpoint_on_progress": getattr(
                group_settings, "checkpoint_on_progress", False
            ),
            "worktree_merge_squash": getattr(
                group_settings, "worktree_merge_squash", True
            ),
            "session_resume": getattr(
                group_settings, "agent_session_resume", True
            ),
            "idle_timeout": getattr(
                group_settings, "agent_idle_timeout", 0
            ),
        }
        result = _merge_agent_config(result, group_overrides)

        explicit = str(template_name or "").strip()
        if explicit:
            tpl = self.load_template(explicit, base_dir)
            if tpl:
                result = _merge_agent_config(result, tpl)
                effective_template = explicit

        result = _merge_agent_config(
            result,
            _normalize_role_data(overrides or {}, allow_runtime_overrides=True),
        )
        if effective_template:
            result["template"] = effective_template
        return result

    @staticmethod
    def render_preamble(role: dict | None) -> str:
        """Return the role prompt block.

        Exact format:
        - empty preamble + empty priorities => ""
        - preamble only => "<preamble>"
        - priorities only => "Priorities:\n- item\n- item"
        - both => "<preamble>\n\nPriorities:\n- item\n- item"
        """
        role = dict(role or {})
        preamble = str(role.get("preamble", "") or "").strip()
        priorities = _normalize_priorities(role.get("priorities"))
        parts = []
        if preamble:
            parts.append(preamble)
        if priorities:
            parts.append("Priorities:\n" + "\n".join(
                f"- {item}" for item in priorities
            ))
        return "\n\n".join(parts)
