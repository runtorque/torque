"""Role discovery, validation, storage, and legacy-template compatibility."""

from __future__ import annotations

import os

import yaml

from .actions import parse_yaml
from .config import log
from .templates import (
    TemplateManager,
    _BlockStr,
    _KNOWN_KEYS,
    _TEMPLATE_KEY_ORDER,
    _TemplateDumper,
    _normalize_template_data,
)

_ROLE_KEY_ORDER = [
    *(_TEMPLATE_KEY_ORDER[:11]),
    "preamble",
    "priorities",
    *(_TEMPLATE_KEY_ORDER[11:]),
]
_ROLE_KNOWN_KEYS = _KNOWN_KEYS | {"preamble", "priorities"}


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


class RoleManager(TemplateManager):
    """Primary role manager with compatibility reads from legacy templates."""

    GLOBAL_ROLES_DIR = os.path.expanduser("~/.loom/roles")

    @staticmethod
    def _find_project_dir(base_dir: str = "", leaf: str = "roles") -> str | None:
        d = os.path.expanduser(base_dir) if base_dir else os.getcwd()
        if not os.path.isdir(d):
            d = os.getcwd()
        for _ in range(20):
            candidate = os.path.join(d, ".loom", leaf)
            if os.path.isdir(candidate):
                return candidate
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        return None

    @staticmethod
    def _default_project_dir(base_dir: str = "", leaf: str = "roles") -> str:
        d = os.path.expanduser(base_dir) if base_dir else os.getcwd()
        return os.path.join(d, ".loom", leaf)

    @classmethod
    def _default_project_role_dir(cls, base_dir: str = "") -> str:
        existing_role_dir = cls._find_project_dir(base_dir, "roles")
        if existing_role_dir:
            return existing_role_dir
        legacy_dir = cls._find_project_dir(base_dir, "agents")
        if legacy_dir:
            return os.path.join(os.path.dirname(legacy_dir), "roles")
        return cls._default_project_dir(base_dir, "roles")

    @classmethod
    def _global_dir(cls, leaf: str = "roles") -> str:
        return os.path.expanduser(os.path.join("~/.loom", leaf))

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

    @staticmethod
    def find_legacy_template_dirs(base_dir: str = "") -> list[str]:
        return TemplateManager.find_templates_dirs(base_dir)

    @classmethod
    def _source_dirs(cls, base_dir: str = "", scope: str = "") -> list[tuple[str, bool, bool]]:
        project_role_dir = cls._find_project_dir(base_dir, "roles")
        global_role_dir = cls._global_dir("roles")
        project_legacy_dir = cls._find_project_dir(base_dir, "agents")
        global_legacy_dir = cls._global_dir("agents")

        dirs: list[tuple[str, bool, bool]] = []
        if scope == "project":
            if project_role_dir:
                dirs.append((project_role_dir, False, False))
            if project_legacy_dir:
                dirs.append((project_legacy_dir, False, True))
            return dirs
        if scope == "user":
            if os.path.isdir(global_role_dir):
                dirs.append((global_role_dir, True, False))
            if os.path.isdir(global_legacy_dir):
                dirs.append((global_legacy_dir, True, True))
            return dirs

        if project_role_dir:
            dirs.append((project_role_dir, False, False))
        if project_legacy_dir:
            dirs.append((project_legacy_dir, False, True))
        if os.path.isdir(global_role_dir):
            dirs.append((global_role_dir, True, False))
        if os.path.isdir(global_legacy_dir):
            dirs.append((global_legacy_dir, True, True))
        return dirs

    @staticmethod
    def _iter_named_yaml_paths(root_dir: str) -> list[tuple[str, str]]:
        entries = []
        for dirpath, _dirnames, filenames in os.walk(root_dir):
            for fname in sorted(filenames):
                if not fname.endswith((".yaml", ".yml")):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fname), root_dir)
                entries.append((rel.rsplit(".", 1)[0], os.path.join(dirpath, fname)))
        return entries

    @staticmethod
    def _shadowed_legacy_keys(entries: list[dict]) -> set[tuple[str, bool]]:
        role_keys = {
            (entry["name"], entry["global"])
            for entry in entries
            if not entry["legacy"]
        }
        legacy_keys = {
            (entry["name"], entry["global"])
            for entry in entries
            if entry["legacy"]
        }
        return role_keys & legacy_keys

    @classmethod
    def _warning_slugs(cls, entries: list[dict]) -> set[str]:
        return {
            name for name, _is_global in cls._shadowed_legacy_keys(entries)
        }

    @staticmethod
    def _log_shadowed_legacy_templates(slugs: set[str]) -> None:
        for slug in sorted(slugs):
            log.warning("role '%s' shadows legacy template", slug)

    def _collect_entries(self, base_dir: str = "", scope: str = "") -> list[dict]:
        entries = []
        for root_dir, is_global, is_legacy in self._source_dirs(base_dir, scope):
            if not root_dir or not os.path.isdir(root_dir):
                continue
            for name, path in self._iter_named_yaml_paths(root_dir):
                entries.append({
                    "name": name,
                    "path": path,
                    "dir": root_dir,
                    "global": is_global,
                    "legacy": is_legacy,
                })
        return entries

    def _load_entry_meta(self, entry: dict) -> dict:
        name = entry["name"]
        try:
            with open(entry["path"]) as f:
                raw = f.read()
            meta = parse_yaml(raw) or {}
            meta = _normalize_role_data(meta, name_hint=name)
        except Exception:
            meta = {"name": name}
        return meta

    def _load_raw(self, name: str, base_dir: str = "",
                  scope: str = "") -> str | None:
        name = str(name or "").strip()
        if not name:
            return None
        entries = self._collect_entries(base_dir, scope=scope)
        shadowed_legacy_keys = self._shadowed_legacy_keys(entries)
        for entry in entries:
            if entry["name"] != name:
                continue
            if not entry["legacy"] and (
                name, entry["global"]
            ) in shadowed_legacy_keys:
                self._log_shadowed_legacy_templates({name})
            with open(entry["path"]) as f:
                return f.read()
        return None

    def list_roles(self, base_dir: str = "") -> list[dict]:
        entries = self._collect_entries(base_dir)
        shadowed_legacy_keys = self._shadowed_legacy_keys(entries)
        warning_slugs = self._warning_slugs(entries)
        if warning_slugs:
            self._log_shadowed_legacy_templates(warning_slugs)

        results = []
        seen_names = set()
        for entry in entries:
            if entry["legacy"] and (
                entry["name"], entry["global"]
            ) in shadowed_legacy_keys:
                continue
            meta = self._load_entry_meta(entry)
            shadowed = entry["global"] and entry["name"] in seen_names
            results.append({
                "name": entry["name"],
                "display_name": meta.get("display_name", ""),
                "description": meta.get("description", ""),
                "provider": meta.get("provider", ""),
                "preamble": meta.get("preamble", ""),
                "priorities": list(meta.get("priorities", [])),
                "global": entry["global"],
                "dir": entry["dir"],
                "shadowed": shadowed,
                "legacy": entry["legacy"],
            })
            if not entry["global"]:
                seen_names.add(entry["name"])
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
        with open(path, "w") as f:
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
        dirs = self.find_roles_dirs(base_dir)
        if scope == "user":
            dirs = [self._global_dir("roles")]
        elif scope == "project":
            role_dir = self.find_project_role_dir(base_dir)
            dirs = [role_dir] if role_dir else []
        for role_dir in dirs:
            if not role_dir:
                continue
            for suffix in (".yaml", ".yml"):
                path = os.path.join(role_dir, name + suffix)
                if os.path.isfile(path):
                    os.remove(path)
                    return True
        return self.delete_template(name, scope=scope, base_dir=base_dir)

    def delete_template(self, name: str,
                        scope: str = "", base_dir: str = "") -> bool:
        for template_dir, _is_global, _is_legacy in self._source_dirs(
            base_dir, scope
        ):
            if not template_dir:
                continue
            for suffix in (".yaml", ".yml"):
                path = os.path.join(template_dir, name + suffix)
                if os.path.isfile(path):
                    os.remove(path)
                    return True
        return False

    @staticmethod
    def render_preamble(role: dict | None) -> str:
        """Return the role prompt block.

        Exact format:
        - empty preamble + empty priorities => ""
        - preamble only => "<preamble>"
        - priorities only => "Priorities:\\n- item\\n- item"
        - both => "<preamble>\\n\\nPriorities:\\n- item\\n- item"
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
