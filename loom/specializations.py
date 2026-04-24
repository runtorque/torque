"""Engineer specialization discovery, validation, storage.

Parallel to ``loom/roles.py`` but operates at the engineer level:
- Role (worker-level): shapes a worker's action scope and preamble.
- Specialization (engineer-level): shapes an engineer's boot preamble
  and how they route/triage tasks. Engineers can carry multiple
  specializations (ordered, primary first).

Specialization YAML files live at ``~/.loom/specializations/*.yaml`` and
project ``.loom/specializations/*.yaml``. Read precedence: project
overrides user. YAML shape is minimal: ``name``, ``priorities``, and
``preamble``.
"""

from __future__ import annotations

import os

import yaml

from .actions import parse_yaml
from .config import log


class _BlockStr(str):
    """Tagged string subclass so the YAML dumper emits block scalars."""


class _SpecializationDumper(yaml.SafeDumper):
    pass


_SpecializationDumper.add_representer(
    _BlockStr,
    lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style="|"),
)

_SPECIALIZATION_KEY_ORDER = ["name", "description", "preamble", "priorities"]
_SCALAR_KEYS = {"name", "description"}
_KNOWN_KEYS = _SCALAR_KEYS | {"preamble", "priorities"}


def _normalize_priorities(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _normalize_specialization_data(data: dict | None,
                                   name_hint: str = "") -> dict:
    data = dict(data or {})
    result: dict = {}

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

    preamble = data.get("preamble", "")
    if preamble is not None:
        preamble = str(preamble).strip()
        if preamble:
            result["preamble"] = preamble

    priorities = _normalize_priorities(data.get("priorities"))
    if priorities:
        result["priorities"] = priorities

    return result


class SpecializationManager:
    """Primary specialization manager.

    Specialization files live under ``specializations/`` at the project or
    user scope. Mirrors ``RoleManager`` in behavior and API, minus legacy
    migration warnings (specializations are a new-only feature).
    """

    GLOBAL_DIR = os.path.expanduser("~/.loom/specializations")

    @classmethod
    def _global_dir(cls) -> str:
        return os.path.expanduser("~/.loom/specializations")

    @classmethod
    def _find_project_dir(cls, base_dir: str = "") -> str | None:
        d = os.path.expanduser(base_dir) if base_dir else os.getcwd()
        if not os.path.isdir(d):
            d = os.getcwd()
        global_dir = os.path.realpath(cls._global_dir())
        for _ in range(20):
            candidate = os.path.join(d, ".loom", "specializations")
            if os.path.isdir(candidate):
                if os.path.realpath(candidate) != global_dir:
                    return candidate
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        return None

    @classmethod
    def _default_project_dir(cls, base_dir: str = "") -> str:
        d = os.path.expanduser(base_dir) if base_dir else os.getcwd()
        return os.path.join(d, ".loom", "specializations")

    @classmethod
    def find_specialization_dirs(cls, base_dir: str = "") -> list[str]:
        dirs = []
        project_dir = cls._find_project_dir(base_dir)
        if project_dir:
            dirs.append(project_dir)
        global_dir = cls._global_dir()
        if os.path.isdir(global_dir) and global_dir not in dirs:
            dirs.append(global_dir)
        return dirs

    @classmethod
    def _source_dirs(cls, base_dir: str = "",
                     scope: str = "") -> list[tuple[str, bool]]:
        dirs: list[tuple[str, bool]] = []
        if scope == "project":
            project_dir = cls._find_project_dir(base_dir)
            if project_dir:
                dirs.append((project_dir, False))
            return dirs
        if scope == "user":
            global_dir = cls._global_dir()
            if os.path.isdir(global_dir):
                dirs.append((global_dir, True))
            return dirs

        project_dir = cls._find_project_dir(base_dir)
        if project_dir:
            dirs.append((project_dir, False))
        global_dir = cls._global_dir()
        if os.path.isdir(global_dir):
            dirs.append((global_dir, True))
        return dirs

    @staticmethod
    def _iter_named_yaml_paths(root_dir: str) -> list[tuple[str, str]]:
        entries = []
        if not root_dir or not os.path.isdir(root_dir):
            return entries
        for dirpath, _dirnames, filenames in os.walk(root_dir):
            for fname in sorted(filenames):
                if not fname.endswith((".yaml", ".yml")):
                    continue
                rel = os.path.relpath(
                    os.path.join(dirpath, fname), root_dir)
                entries.append(
                    (rel.rsplit(".", 1)[0], os.path.join(dirpath, fname)))
        return entries

    def _load_entry_meta(self, entry: dict) -> dict:
        name = entry["name"]
        try:
            with open(entry["path"], encoding="utf-8") as f:
                raw = f.read()
            meta = parse_yaml(raw) or {}
            meta = _normalize_specialization_data(meta, name_hint=name)
        except Exception as exc:
            log.warning(
                "malformed specialization at %s: %s", entry["path"], exc)
            meta = {"name": name}
        return meta

    def _load_raw(self, name: str, base_dir: str = "",
                  scope: str = "") -> str | None:
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

    def list_specializations(self, base_dir: str = "",
                             scope: str = "") -> list[dict]:
        results = []
        seen_names = set()
        for root_dir, is_global in self._source_dirs(base_dir, scope):
            if not root_dir or not os.path.isdir(root_dir):
                continue
            for name, path in self._iter_named_yaml_paths(root_dir):
                meta = self._load_entry_meta({"name": name, "path": path})
                shadowed = is_global and name in seen_names
                results.append({
                    "name": name,
                    "description": meta.get("description", ""),
                    "preamble": meta.get("preamble", ""),
                    "priorities": list(meta.get("priorities", [])),
                    "global": is_global,
                    "dir": root_dir,
                    "shadowed": shadowed,
                })
                if not is_global:
                    seen_names.add(name)
        return sorted(results, key=lambda item: (item["global"], item["name"]))

    def get_specialization(self, name: str, base_dir: str = "",
                           scope: str = "") -> dict | None:
        raw = self._load_raw(name, base_dir, scope=scope)
        if raw is None:
            return None
        try:
            parsed = parse_yaml(raw) or {}
        except Exception as exc:
            log.warning("malformed specialization %r: %s", name, exc)
            return None
        return _normalize_specialization_data(parsed, name_hint=name)

    def save_specialization(self, name: str, data: dict,
                            scope: str = "project",
                            base_dir: str = "") -> str:
        clean = _normalize_specialization_data(data, name_hint=name)
        clean["name"] = name
        unknown = sorted(set(data or {}) - _KNOWN_KEYS - {"name"})
        if unknown:
            raise ValueError(
                "Unknown specialization fields: " + ", ".join(unknown))
        if scope == "user":
            target_dir = self._global_dir()
            os.makedirs(target_dir, exist_ok=True)
        else:
            target_dir = self._find_project_dir(base_dir)
            if not target_dir:
                target_dir = self._default_project_dir(base_dir)
                os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, name + ".yaml")
        os.makedirs(os.path.dirname(path), exist_ok=True)

        ordered: dict = {}
        for key in _SPECIALIZATION_KEY_ORDER:
            if key not in clean:
                continue
            value = clean[key]
            if key == "preamble" and value:
                ordered[key] = _BlockStr(str(value).rstrip("\n") + "\n")
            else:
                ordered[key] = value

        with open(path, "w", encoding="utf-8") as f:
            f.write(yaml.dump(
                ordered,
                Dumper=_SpecializationDumper,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            ))
        return path

    def delete_specialization(self, name: str,
                              scope: str = "",
                              base_dir: str = "") -> bool:
        for target_dir, _is_global in self._source_dirs(base_dir, scope):
            if not target_dir:
                continue
            for suffix in (".yaml", ".yml"):
                path = os.path.join(target_dir, name + suffix)
                if os.path.isfile(path):
                    os.remove(path)
                    return True
        return False

    @staticmethod
    def render_preamble(spec: dict | None) -> str:
        """Return a single specialization's preamble + priorities block.

        Format matches ``RoleManager.render_preamble``:
        - empty preamble + empty priorities => ""
        - preamble only => "<preamble>"
        - priorities only => "Priorities:\\n- item\\n- item"
        - both => "<preamble>\\n\\nPriorities:\\n- item\\n- item"
        """
        spec = dict(spec or {})
        preamble = str(spec.get("preamble", "") or "").strip()
        priorities = _normalize_priorities(spec.get("priorities"))
        parts = []
        if preamble:
            parts.append(preamble)
        if priorities:
            parts.append("Priorities:\n" + "\n".join(
                f"- {item}" for item in priorities
            ))
        return "\n\n".join(parts)

    def render_engineer_preamble(self, specialization_names: list[str],
                                 base_dir: str = "") -> str:
        """Render a combined preamble for an ordered list of specializations.

        The first entry is the primary specialization. Output shape:

            Specializations: <primary> (primary), <secondary>, ...

            <primary preamble + priorities>

            <secondary preamble + priorities>

            ...

        Missing specializations are silently skipped (with a warning log).
        Returns an empty string when no specializations resolve.
        """
        names = [str(n or "").strip() for n in (specialization_names or [])]
        names = [n for n in names if n]
        if not names:
            return ""

        resolved: list[tuple[str, dict]] = []
        for name in names:
            spec = self.get_specialization(name, base_dir=base_dir)
            if not spec:
                log.warning(
                    "engineer specialization %r not found; skipping", name)
                continue
            resolved.append((name, spec))

        if not resolved:
            return ""

        header_parts = []
        for idx, (name, _spec) in enumerate(resolved):
            header_parts.append(
                f"{name} (primary)" if idx == 0 else name)
        header = "Specializations: " + ", ".join(header_parts)

        blocks = [header]
        for _name, spec in resolved:
            body = self.render_preamble(spec)
            if body:
                blocks.append(body)
        return "\n\n".join(blocks)
