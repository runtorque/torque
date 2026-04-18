"""Agent template discovery, validation, storage, and config resolution."""

import os

import yaml

from .actions import parse_yaml


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


class TemplateManager:
    GLOBAL_TEMPLATES_DIR = os.path.expanduser("~/.loom/agents")

    @staticmethod
    def find_templates_dirs(base_dir: str = "") -> list[str]:
        dirs = []
        d = os.path.expanduser(base_dir) if base_dir else os.getcwd()
        if not os.path.isdir(d):
            d = os.getcwd()
        for _ in range(20):
            candidate = os.path.join(d, ".loom", "agents")
            if os.path.isdir(candidate):
                dirs.append(candidate)
                break
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        g = os.path.expanduser("~/.loom/agents")
        if os.path.isdir(g) and g not in dirs:
            dirs.append(g)
        return dirs

    @staticmethod
    def find_templates_dir(base_dir: str = "") -> str | None:
        dirs = TemplateManager.find_templates_dirs(base_dir)
        return dirs[0] if dirs else None

    def _load_raw(self, name: str, base_dir: str = "",
                  scope: str = "") -> str | None:
        if scope == "user":
            dirs = [os.path.expanduser("~/.loom/agents")]
        elif scope == "project":
            d = self.find_templates_dir(base_dir)
            dirs = [d] if d else []
        else:
            dirs = self.find_templates_dirs(base_dir)
        for tdir in dirs:
            if not tdir:
                continue
            for suffix in ("", ".yaml", ".yml"):
                path = os.path.join(tdir, name + suffix)
                if os.path.isfile(path):
                    with open(path) as f:
                        return f.read()
        return None

    def list_templates(self, base_dir: str = "") -> list[dict]:
        results = []
        seen_names = set()
        for tdir in self.find_templates_dirs(base_dir):
            is_global = (tdir == os.path.expanduser("~/.loom/agents"))
            for dirpath, _dirnames, filenames in os.walk(tdir):
                for fname in sorted(filenames):
                    if not fname.endswith((".yaml", ".yml")):
                        continue
                    rel = os.path.relpath(os.path.join(dirpath, fname), tdir)
                    name = rel.rsplit(".", 1)[0]
                    shadowed = is_global and name in seen_names
                    path = os.path.join(dirpath, fname)
                    try:
                        with open(path) as f:
                            raw = f.read()
                        meta = parse_yaml(raw) or {}
                        meta = _normalize_template_data(meta, name_hint=name)
                    except Exception:
                        meta = {"name": name}
                    results.append({
                        "name": name,
                        "display_name": meta.get("display_name", ""),
                        "description": meta.get("description", ""),
                        "provider": meta.get("provider", ""),
                        "global": is_global,
                        "dir": tdir,
                        "shadowed": shadowed,
                    })
                    if not is_global:
                        seen_names.add(name)
        return sorted(results, key=lambda t: (t["global"], t["name"]))

    def load_template(self, name: str, base_dir: str = "",
                      scope: str = "") -> dict | None:
        raw = self._load_raw(name, base_dir, scope=scope)
        if raw is None:
            return None
        try:
            parsed = parse_yaml(raw) or {}
        except Exception:
            return None
        return _normalize_template_data(parsed, name_hint=name)

    def save_template(self, name: str, data: dict,
                      scope: str = "project", base_dir: str = "") -> str:
        clean = _normalize_template_data(data, name_hint=name)
        clean["name"] = name
        unknown = sorted(set(data or {}) - _KNOWN_KEYS - {"name"})
        if unknown:
            raise ValueError("Unknown template fields: " + ", ".join(unknown))
        if scope == "user":
            tdir = os.path.expanduser("~/.loom/agents")
            os.makedirs(tdir, exist_ok=True)
        else:
            tdir = self.find_templates_dir(base_dir)
            if not tdir:
                d = base_dir or os.getcwd()
                tdir = os.path.join(d, ".loom", "agents")
                os.makedirs(tdir, exist_ok=True)
        path = os.path.join(tdir, name + ".yaml")
        os.makedirs(os.path.dirname(path), exist_ok=True)

        ordered = {}
        for key in _TEMPLATE_KEY_ORDER:
            if key not in clean:
                continue
            value = clean[key]
            if key in ("system_prompt", "initial_prompt") and value:
                ordered[key] = _BlockStr(value.rstrip("\n") + "\n")
            else:
                ordered[key] = value
        with open(path, "w") as f:
            f.write(yaml.dump(
                ordered,
                Dumper=_TemplateDumper,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            ))
        return path

    def delete_template(self, name: str,
                        scope: str = "", base_dir: str = "") -> bool:
        dirs = self.find_templates_dirs(base_dir)
        if scope == "user":
            dirs = [os.path.expanduser("~/.loom/agents")]
        elif scope == "project":
            d = self.find_templates_dir(base_dir)
            dirs = [d] if d else []
        for tdir in dirs:
            if not tdir:
                continue
            for suffix in (".yaml", ".yml"):
                path = os.path.join(tdir, name + suffix)
                if os.path.isfile(path):
                    os.remove(path)
                    return True
        return False

    def resolve_agent_config(self, template_name: str,
                             group_settings,
                             overrides: dict | None,
                             base_dir: str = "") -> dict:
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
            result, _normalize_template_data(
                overrides or {}, allow_runtime_overrides=True)
        )
        if effective_template:
            result["template"] = effective_template
        return result
