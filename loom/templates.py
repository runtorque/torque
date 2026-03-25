"""Template loading, variable extraction, and Jinja2 rendering."""

import os
import re

from .config import log

try:
    from jinja2.sandbox import SandboxedEnvironment
    from jinja2 import StrictUndefined
    _HAS_JINJA2 = True
except ImportError:
    _HAS_JINJA2 = False
    log.warning("jinja2 not installed — template rendering will use "
                "basic variable substitution")


# ---------------------------------------------------------------------------
# Minimal YAML parser (same as bin/loom — stdlib only, covers templates)
# ---------------------------------------------------------------------------

def _yaml_parse_value(raw):
    if raw == "" or raw == "~" or raw == "null":
        return None
    if raw in ("true", "True"):
        return True
    if raw in ("false", "False"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        return raw[1:-1]
    return raw


def parse_yaml(text):
    lines = text.split("\n")
    return _yaml_parse_block(lines, 0, 0)[0]


def _yaml_parse_block(lines, idx, base_indent):
    result = {}
    while idx < len(lines):
        line = lines[idx]
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            idx += 1
            continue
        indent = len(line) - len(stripped)
        if indent < base_indent:
            break
        if stripped.startswith("- "):
            lst, idx = _yaml_parse_list(lines, idx, indent)
            return lst, idx
        colon_pos = stripped.find(":")
        if colon_pos == -1:
            idx += 1
            continue
        key = stripped[:colon_pos].strip()
        rest = stripped[colon_pos + 1:].strip()
        if rest and rest[0] not in ('"', "'", "|", ">"):
            comment_pos = rest.find(" #")
            if comment_pos >= 0:
                rest = rest[:comment_pos].strip()
        if rest == "|":
            val, idx = _yaml_parse_block_scalar(lines, idx + 1, indent)
            result[key] = val
        elif rest == "":
            idx += 1
            child, idx = _yaml_parse_block(lines, idx, indent + 1)
            result[key] = child
        else:
            result[key] = _yaml_parse_value(rest)
            idx += 1
    return result, idx


def _yaml_parse_list(lines, idx, base_indent):
    result = []
    while idx < len(lines):
        line = lines[idx]
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            idx += 1
            continue
        indent = len(line) - len(stripped)
        if indent < base_indent:
            break
        if indent > base_indent:
            idx += 1
            continue
        if not stripped.startswith("- "):
            break
        item_str = stripped[2:]
        colon_pos = item_str.find(":")
        if colon_pos > 0:
            item = {}
            key = item_str[:colon_pos].strip()
            rest = item_str[colon_pos + 1:].strip()
            item[key] = _yaml_parse_value(rest) if rest else None
            idx += 1
            while idx < len(lines):
                cline = lines[idx]
                cstripped = cline.lstrip()
                if not cstripped or cstripped.startswith("#"):
                    idx += 1
                    continue
                cindent = len(cline) - len(cstripped)
                if cindent <= base_indent:
                    break
                cp = cstripped.find(":")
                if cp > 0:
                    ck = cstripped[:cp].strip()
                    cv = cstripped[cp + 1:].strip()
                    item[ck] = _yaml_parse_value(cv) if cv else None
                idx += 1
            result.append(item)
        else:
            result.append(_yaml_parse_value(item_str))
            idx += 1
    return result, idx


def _yaml_parse_block_scalar(lines, idx, parent_indent):
    collected = []
    block_indent = None
    while idx < len(lines):
        line = lines[idx]
        if line.strip() == "":
            collected.append("")
            idx += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= parent_indent:
            break
        if block_indent is None:
            block_indent = indent
        collected.append(line[block_indent:] if indent >= block_indent
                         else line.lstrip())
        idx += 1
    while collected and collected[-1] == "":
        collected.pop()
    return "\n".join(collected), idx


# ---------------------------------------------------------------------------
# Syntax migration
# ---------------------------------------------------------------------------

def _migrate_syntax(text):
    """Convert legacy ${VAR} to Jinja2 {{ VAR }} syntax."""
    if not isinstance(text, str):
        return text
    return re.sub(r'\$\{(\w+)\}', r'{{ \1 }}', text)


def _migrate_template(tpl):
    """Recursively migrate all string fields in a template dict."""
    if isinstance(tpl, str):
        return _migrate_syntax(tpl)
    if isinstance(tpl, dict):
        return {k: _migrate_template(v) for k, v in tpl.items()}
    if isinstance(tpl, list):
        return [_migrate_template(item) for item in tpl]
    return tpl


# ---------------------------------------------------------------------------
# TemplateManager
# ---------------------------------------------------------------------------

class TemplateManager:

    def __init__(self):
        if _HAS_JINJA2:
            self._env = SandboxedEnvironment(
                undefined=StrictUndefined,
                keep_trailing_newline=True,
            )
        else:
            self._env = None

    @staticmethod
    def find_templates_dir(base_dir: str = "") -> str | None:
        """Walk up from base_dir (or cwd) to find .loom/templates/."""
        d = os.path.expanduser(base_dir) if base_dir else os.getcwd()
        if not os.path.isdir(d):
            d = os.getcwd()
        for _ in range(20):  # safety limit
            candidate = os.path.join(d, ".loom", "templates")
            if os.path.isdir(candidate):
                return candidate
            parent = os.path.dirname(d)
            if parent == d:
                return None
            d = parent
        return None

    def list_templates(self, base_dir: str = "") -> list[dict]:
        """List all templates with name, description, and vars metadata."""
        tdir = self.find_templates_dir(base_dir)
        if not tdir:
            return []
        result = []
        for fname in sorted(os.listdir(tdir)):
            if not fname.endswith((".yaml", ".yml")):
                continue
            name = fname.rsplit(".", 1)[0]
            path = os.path.join(tdir, fname)
            try:
                with open(path) as f:
                    tpl = parse_yaml(f.read())
                desc = tpl.get("description", "")
                tvars = self.get_template_vars(tpl)
            except Exception:
                desc = "(parse error)"
                tvars = []
            result.append({"name": name, "description": desc, "vars": tvars})
        return result

    def load_template(self, name: str, base_dir: str = "") -> dict | None:
        """Load a template by name. Returns parsed dict or None."""
        tdir = self.find_templates_dir(base_dir)
        if not tdir:
            return None
        for suffix in ("", ".yaml", ".yml"):
            path = os.path.join(tdir, name + suffix)
            if os.path.isfile(path):
                with open(path) as f:
                    return parse_yaml(f.read())
        return None

    @staticmethod
    def get_template_vars(tpl: dict) -> list[dict]:
        """Extract variable metadata from a template's vars section.

        Always includes TASK as the first variable if not explicitly
        declared. Returns list of {name, description, default, required}.
        """
        raw_vars = tpl.get("vars", {})
        if not isinstance(raw_vars, dict):
            raw_vars = {}

        result = []
        has_task = False
        for vname, meta in raw_vars.items():
            if vname == "TASK":
                has_task = True
            if isinstance(meta, dict):
                result.append({
                    "name": vname,
                    "description": meta.get("description", ""),
                    "default": meta.get("default", ""),
                    "required": meta.get("required", False),
                })
            else:
                # Simple value — treat as default
                result.append({
                    "name": vname,
                    "description": "",
                    "default": str(meta) if meta is not None else "",
                    "required": False,
                })

        if not has_task:
            result.insert(0, {
                "name": "TASK",
                "description": "Task description",
                "default": "",
                "required": True,
            })

        return result

    def render_template(self, tpl: dict, variables: dict) -> dict:
        """Render all Jinja2 fields in a template with given variables.

        Returns a flat dict with resolved agent settings:
        {name, command, directory, profile, shell, tab_color, env_vars, prompt}
        """
        tpl = _migrate_template(tpl)
        agent = tpl.get("agent", {})

        result = {
            "name": self._render_str(
                agent.get("name_prefix", tpl.get("name", "agent")),
                variables),
            "command": self._render_str(
                agent.get("command", ""), variables),
            "directory": self._render_str(
                agent.get("directory", ""), variables),
            "profile": agent.get("profile", ""),
            "shell": agent.get("shell", ""),
            "tab_color": agent.get("tab_color", ""),
            "env_vars": {},
            "prompt": self._render_str(
                tpl.get("prompt", "{{ TASK }}"), variables),
            "worktree": tpl.get("worktree", None),
            "terminals": tpl.get("terminals", []),
        }

        # Render env_vars values
        for k, v in (agent.get("env_vars") or {}).items():
            result["env_vars"][k] = self._render_str(
                str(v) if v is not None else "", variables)

        return result

    def _render_str(self, text: str, variables: dict) -> str:
        """Render a single string through Jinja2 or fallback."""
        if not isinstance(text, str) or not text:
            return text or ""
        if self._env:
            try:
                tmpl = self._env.from_string(text)
                return tmpl.render(**variables)
            except Exception as exc:
                log.warning("Jinja2 render failed for %r: %s",
                            text[:50], exc)
                return self._fallback_render(text, variables)
        return self._fallback_render(text, variables)

    @staticmethod
    def _fallback_render(text: str, variables: dict) -> str:
        """Simple {{ VAR }} substitution without Jinja2."""
        for key, value in variables.items():
            text = text.replace('{{ ' + key + ' }}', str(value))
            text = text.replace('{{' + key + '}}', str(value))
        return text
