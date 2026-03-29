"""Template loading, variable extraction, and Jinja2 rendering.

Templates are YAML files rendered through Jinja2 as a whole. Variables
are auto-discovered from the Jinja2 AST — no explicit declaration
needed. Default values are extracted from ``| default()`` filters.
"""

import os
import re

from .config import log

try:
    from jinja2.sandbox import SandboxedEnvironment
    from jinja2 import Undefined, StrictUndefined, nodes
    from jinja2 import meta as jinja2_meta
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
# TemplateManager
# ---------------------------------------------------------------------------

def _migrate_syntax(text):
    """Convert legacy ${VAR} to Jinja2 {{ VAR }} syntax."""
    return re.sub(r'\$\{(\w+)\}', r'{{ \1 }}', text)


class TemplateManager:

    def __init__(self):
        if _HAS_JINJA2:
            self._env = SandboxedEnvironment(
                undefined=StrictUndefined,
                keep_trailing_newline=True,
            )
            # Lenient env for metadata parsing (unresolved vars → empty)
            self._lenient_env = SandboxedEnvironment(
                undefined=Undefined,
                keep_trailing_newline=True,
            )
        else:
            self._env = None
            self._lenient_env = None

    GLOBAL_TEMPLATES_DIR = os.path.expanduser("~/.loom/templates")

    @staticmethod
    def _coalesce_prompt(tpl: dict) -> str:
        """Build a unified prompt from old-format or new-format fields.

        New format: ``prompt`` key.
        Old format: ``task`` + ``instructions`` + ``context`` + ``criteria``.
        """
        prompt = tpl.get("prompt", "")
        if prompt:
            return prompt
        parts = []
        for key in ("task", "instructions", "context", "criteria"):
            val = tpl.get(key, "")
            if val:
                parts.append(val.rstrip())
        return "\n\n".join(parts)

    @staticmethod
    def validate_prompt(prompt: str) -> bool:
        """Check that the prompt contains ``{{ TASK }}``."""
        return bool(re.search(r'\{\{\s*TASK\s*(\|[^}]*)?\}\}', prompt))

    def render_prompt(self, name: str, variables: dict,
                      base_dir: str = "") -> str | None:
        """Render only the template's prompt field with variables.

        Returns the rendered prompt string, or None if the template is
        not found.  Used by dispatch and preview.
        """
        raw = self._load_raw(name, base_dir)
        if raw is None:
            return None
        raw = _migrate_syntax(raw)

        # Parse raw YAML (no Jinja2) to preserve {{ VAR }} placeholders
        try:
            tpl = parse_yaml(raw)
        except Exception:
            tpl = self._parse_lenient(raw) or {}
        prompt_raw = self._coalesce_prompt(tpl)
        if not prompt_raw:
            return None

        return self._render_str(prompt_raw, variables)

    @staticmethod
    def find_templates_dirs(base_dir: str = "") -> list[str]:
        """Return template directories in priority order.

        Searches up from base_dir (or cwd) for a project-local
        .loom/templates/, then falls back to ~/.loom/templates/.
        Project templates take precedence over global ones.
        """
        dirs = []
        # Project-local: walk up from base_dir
        d = os.path.expanduser(base_dir) if base_dir else os.getcwd()
        if not os.path.isdir(d):
            d = os.getcwd()
        for _ in range(20):
            candidate = os.path.join(d, ".loom", "templates")
            if os.path.isdir(candidate):
                dirs.append(candidate)
                break
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        # Global: ~/.loom/templates/ (expand at call time for safety)
        g = os.path.expanduser("~/.loom/templates")
        if os.path.isdir(g) and g not in dirs:
            dirs.append(g)
        return dirs

    @staticmethod
    def find_templates_dir(base_dir: str = "") -> str | None:
        """Return the highest-priority templates directory, or None."""
        dirs = TemplateManager.find_templates_dirs(base_dir)
        return dirs[0] if dirs else None

    def _load_raw(self, name: str, base_dir: str = "") -> str | None:
        """Load a template file as raw text. Searches all template dirs."""
        for tdir in self.find_templates_dirs(base_dir):
            for suffix in ("", ".yaml", ".yml"):
                path = os.path.join(tdir, name + suffix)
                if os.path.isfile(path):
                    with open(path) as f:
                        return f.read()
        return None

    def list_templates(self, base_dir: str = "") -> list[dict]:
        """List all templates with name, description, vars, and scope.

        Returns all templates from all directories. When names collide
        across scopes, both are included — project templates are marked
        as the active one (used for dispatch), user templates with the
        same name are marked as ``shadowed``.
        """
        results = []
        seen_names = set()  # track project-local names for shadowing
        for tdir in self.find_templates_dirs(base_dir):
            for fname in sorted(os.listdir(tdir)):
                if not fname.endswith((".yaml", ".yml")):
                    continue
                name = fname.rsplit(".", 1)[0]
                path = os.path.join(tdir, fname)
                is_global = (tdir == os.path.expanduser("~/.loom/templates"))
                shadowed = is_global and name in seen_names
                try:
                    with open(path) as f:
                        raw = f.read()
                    raw = _migrate_syntax(raw)
                    meta = self._parse_lenient(raw)
                    desc = meta.get("description", "") if meta else ""
                    tvars = self.get_template_vars(raw)
                except Exception:
                    desc = "(parse error)"
                    tvars = []
                results.append({"name": name, "description": desc,
                                "vars": tvars, "global": is_global,
                                "dir": tdir, "shadowed": shadowed})
                if not is_global:
                    seen_names.add(name)
        return sorted(results, key=lambda t: (t["global"], t["name"]))

    def load_template(self, name: str, base_dir: str = "") -> dict | None:
        """Load template metadata (parsed leniently). Returns dict or None."""
        raw = self._load_raw(name, base_dir)
        if raw is None:
            return None
        raw = _migrate_syntax(raw)
        return self._parse_lenient(raw) or {}

    def load_template_raw(self, name: str, base_dir: str = "") -> dict | None:
        """Load template as raw YAML (no Jinja2 rendering).

        Preserves ``{{ VAR }}`` placeholders in field values.
        Used by the template editor.
        """
        raw = self._load_raw(name, base_dir)
        if raw is None:
            return None
        try:
            return parse_yaml(raw)
        except Exception:
            return None

    def get_template_vars(self, raw_or_tpl) -> list[dict]:
        """Auto-discover variables from the raw template text.

        Parses the entire file as a Jinja2 template to find referenced
        variables. Extracts default values from ``| default()`` filters.
        TASK is always listed first and marked as required.

        Accepts either raw text (str) or a parsed dict (for backward
        compat — will just return TASK in that case).
        """
        if isinstance(raw_or_tpl, dict):
            # Legacy call with parsed dict — collect strings and scan
            raw = self._dict_to_scannable(raw_or_tpl)
        else:
            raw = _migrate_syntax(raw_or_tpl)

        # Discover variables and defaults from Jinja2 AST
        discovered = set()
        defaults = {}

        if self._env and _HAS_JINJA2:
            try:
                ast = self._env.parse(raw)
                discovered = jinja2_meta.find_undeclared_variables(ast)
                defaults = self._extract_defaults(ast)
            except Exception as exc:
                log.debug("Jinja2 AST parse failed: %s", exc)
        else:
            discovered = set(re.findall(r'\{\{\s*(\w+)\s*', raw))

        # Build result: TASK first, then others alphabetically
        result = []
        ordered = sorted(discovered - {"TASK"})
        if "TASK" in discovered:
            ordered = ["TASK"] + ordered

        for vname in ordered:
            result.append({
                "name": vname,
                "default": defaults.get(vname, ""),
                "required": vname == "TASK",
            })

        return result

    def render_template(self, tpl_or_name, variables: dict,
                        base_dir: str = "") -> dict:
        """Render the entire template file through Jinja2, then parse YAML.

        Returns a flat dict with resolved agent settings:
        {name, command, directory, profile, shell, tab_color, env_vars,
         prompt, group, labels, worktree, terminals}
        """
        if isinstance(tpl_or_name, str) and "\n" not in tpl_or_name:
            # It's a template name, load raw
            raw = self._load_raw(tpl_or_name, base_dir)
            if not raw:
                return {}
        elif isinstance(tpl_or_name, str):
            raw = tpl_or_name
        else:
            # Legacy dict — render field by field
            return self._render_dict_fields(tpl_or_name, variables)

        raw = _migrate_syntax(raw)

        # Render entire file through Jinja2
        if self._env and _HAS_JINJA2:
            try:
                tmpl = self._env.from_string(raw)
                rendered_text = tmpl.render(**variables)
            except Exception as exc:
                log.warning("Jinja2 render failed: %s", exc)
                rendered_text = self._fallback_render(raw, variables)
        else:
            rendered_text = self._fallback_render(raw, variables)

        # Parse the rendered YAML
        try:
            tpl = parse_yaml(rendered_text)
        except Exception as exc:
            log.warning("YAML parse failed after render: %s", exc)
            return {}

        agent = tpl.get("agent", {}) if isinstance(tpl, dict) else {}

        # Coalesce prompt from new or old format
        prompt = self._coalesce_prompt(tpl)

        return {
            "name": agent.get("name_prefix", tpl.get("name", "agent")),
            "command": agent.get("command", ""),
            "directory": agent.get("directory", ""),
            "profile": agent.get("profile", ""),
            "shell": agent.get("shell", ""),
            "tab_color": agent.get("tab_color", ""),
            "env_vars": agent.get("env_vars", {}),
            "prompt": prompt,
            "group": tpl.get("group", ""),
            "labels": tpl.get("labels", []),
            "worktree": tpl.get("worktree", None),
            "terminals": tpl.get("terminals", []),
            "transitions": tpl.get("transitions", []),
            "max_depth": tpl.get("max_depth", None),
        }

    def get_transitions(self, template_name: str,
                         base_dir: str = "") -> list[dict]:
        """Return the transitions list for a template.

        Each entry is {template: str, when: str} or {ask: True, when: str}.
        Returns [] if template has no transitions or is not found.
        """
        tpl = self.load_template(template_name, base_dir)
        if not tpl:
            return []
        return tpl.get("transitions") or []

    def discover_pipelines(self, base_dir: str = "") -> list[dict]:
        """Scan all templates and discover pipelines from transitions.

        Returns a list of pipeline dicts:
        [{name, templates: [str], edges: [{from, to, when}]}]
        """
        templates = self.list_templates(base_dir)
        # Build adjacency: load transitions for each template
        graph = {}  # name → [{to, when}]
        all_names = set()
        for t in templates:
            if t.get("shadowed"):
                continue
            name = t["name"]
            all_names.add(name)
            tpl = self.load_template(name, base_dir)
            transitions = (tpl.get("transitions") or []) if tpl else []
            edges = []
            for tr in transitions:
                if isinstance(tr, dict) and tr.get("template"):
                    edges.append({"to": tr["template"],
                                  "when": tr.get("when", "")})
            graph[name] = edges

        # Build undirected connected components
        adj = {n: set() for n in all_names}
        for src, edges in graph.items():
            for e in edges:
                target = e["to"]
                if target in all_names:
                    adj[src].add(target)
                    adj.setdefault(target, set()).add(src)

        visited = set()
        components = []
        for node in all_names:
            if node in visited:
                continue
            # BFS to find connected component
            component = set()
            queue = [node]
            while queue:
                n = queue.pop(0)
                if n in visited:
                    continue
                visited.add(n)
                component.add(n)
                for neighbour in adj.get(n, []):
                    if neighbour not in visited:
                        queue.append(neighbour)
            # Only include if there are actual edges (not standalone)
            has_edges = any(graph.get(n) for n in component)
            if has_edges:
                components.append(component)

        # Build pipeline dicts
        pipelines = []
        for comp in components:
            # Find entry points (nodes with no incoming edges within component)
            incoming = set()
            for n in comp:
                for e in graph.get(n, []):
                    if e["to"] in comp:
                        incoming.add(e["to"])
            entry_points = comp - incoming
            name = sorted(entry_points)[0] if entry_points else sorted(comp)[0]

            edges = []
            for n in comp:
                for e in graph.get(n, []):
                    if e["to"] in comp:
                        edges.append({"from": n, "to": e["to"],
                                      "when": e["when"]})

            pipelines.append({
                "name": name,
                "templates": sorted(comp),
                "edges": edges,
            })

        return sorted(pipelines, key=lambda p: p["name"])

    # -- Internal helpers ---------------------------------------------------

    def _parse_lenient(self, raw: str) -> dict | None:
        """Render with lenient undefined (unresolved vars → empty), parse."""
        if self._lenient_env and _HAS_JINJA2:
            try:
                rendered = self._lenient_env.from_string(raw).render()
                return parse_yaml(rendered)
            except Exception:
                pass
        # Fallback: try parsing raw text directly
        try:
            return parse_yaml(raw)
        except Exception:
            return None

    @staticmethod
    def _extract_defaults(ast) -> dict[str, str]:
        """Walk Jinja2 AST to find ``| default(val)`` filter values."""
        if not _HAS_JINJA2:
            return {}
        defaults = {}
        for node in ast.find_all(nodes.Filter):
            if node.name in ("default", "d") and node.args:
                if isinstance(node.node, nodes.Name):
                    arg = node.args[0]
                    if isinstance(arg, nodes.Const) and arg.value is not None:
                        defaults[node.node.name] = str(arg.value)
        return defaults

    @staticmethod
    def _dict_to_scannable(tpl: dict) -> str:
        """Convert a parsed template dict back to scannable text."""
        parts = []
        def _walk(obj):
            if isinstance(obj, str):
                parts.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    _walk(item)
        _walk(tpl)
        return "\n".join(parts)

    def _render_dict_fields(self, tpl: dict, variables: dict) -> dict:
        """Legacy: render individual fields of a parsed template dict."""
        agent = tpl.get("agent", {})
        # Coalesce prompt from new or old format
        prompt_raw = self._coalesce_prompt(tpl) or "{{ TASK }}"
        return {
            "name": self._render_str(
                agent.get("name_prefix", tpl.get("name", "agent")),
                variables),
            "command": self._render_str(agent.get("command", ""), variables),
            "directory": self._render_str(
                agent.get("directory", ""), variables),
            "profile": agent.get("profile", ""),
            "shell": agent.get("shell", ""),
            "tab_color": agent.get("tab_color", ""),
            "env_vars": {k: self._render_str(str(v or ""), variables)
                         for k, v in (agent.get("env_vars") or {}).items()},
            "prompt": self._render_str(prompt_raw, variables),
            "group": self._render_str(tpl.get("group", ""), variables),
            "labels": tpl.get("labels", []),
            "worktree": tpl.get("worktree", None),
            "terminals": tpl.get("terminals", []),
            "transitions": tpl.get("transitions", []),
            "max_depth": tpl.get("max_depth", None),
        }

    def _render_str(self, text: str, variables: dict) -> str:
        if not isinstance(text, str) or not text:
            return text or ""
        text = _migrate_syntax(text)
        if self._env:
            try:
                return self._env.from_string(text).render(**variables)
            except Exception:
                return self._fallback_render(text, variables)
        return self._fallback_render(text, variables)

    @staticmethod
    def _fallback_render(text: str, variables: dict) -> str:
        for key, value in variables.items():
            text = text.replace('{{ ' + key + ' }}', str(value))
            text = text.replace('{{' + key + '}}', str(value))
        return text
