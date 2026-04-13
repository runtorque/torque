"""Action loading, variable extraction, and Jinja2 rendering.

Actions are YAML files where only the ``prompt`` field is rendered
through Jinja2. All other fields are plain YAML. Variables are
auto-discovered from the prompt's Jinja2 AST — no explicit declaration
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
    log.warning("jinja2 not installed — action rendering will use "
                "basic variable substitution")


# ---------------------------------------------------------------------------
# Minimal YAML parser (same as bin/loom — stdlib only, covers actions)
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
            # Only treat as top-level list if we haven't parsed any
            # keys yet — otherwise this is a bug (list at same indent
            # as parent keys, which PyYAML produces for list values).
            if not result:
                lst, idx = _yaml_parse_list(lines, idx, indent)
                return lst, idx
            else:
                # Shouldn't happen in well-formed YAML at this level;
                # skip to avoid losing the dict we've built.
                idx += 1
                continue
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
            # Empty value: peek at next non-blank line to decide
            # if it's a nested block or a list at the same indent
            idx += 1
            peek_idx = idx
            while peek_idx < len(lines):
                peek = lines[peek_idx].lstrip()
                if peek and not peek.startswith("#"):
                    break
                peek_idx += 1
            if peek_idx < len(lines) and lines[peek_idx].lstrip().startswith("- "):
                peek_indent = len(lines[peek_idx]) - len(lines[peek_idx].lstrip())
                lst, idx = _yaml_parse_list(lines, peek_idx, peek_indent)
                result[key] = lst
            else:
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
# ActionManager
# ---------------------------------------------------------------------------

def _migrate_syntax(text):
    """Convert legacy ${VAR} to Jinja2 {{ VAR }} syntax."""
    return re.sub(r'\$\{(\w+)\}', r'{{ \1 }}', text)


# Stub loom context for preview renders (no real agent/task).
# Templates referencing loom.* get safe defaults instead of StrictUndefined errors.
LOOM_CONTEXT_STUB = {
    "agent":     {"name": "", "slug": "", "type": "", "group": "", "directory": ""},
    "context":   {"is_clean": True, "tasks_dispatched": 0, "previous_tasks": []},
    "worktree":  {"active": False, "path": "", "branch": "", "base_branch": "",
                  "dirty": False, "diff": {}, "checkpoints": 0},
    "task":      {"id": "", "title": "", "slug": "", "description": "",
                  "depth": 0, "is_derived": False,
                  "parent_task_id": "", "parent_agent_id": "",
                  "parent_agent_name": "", "parent_agent_slug": "",
                  "labels": [], "group": "", "status": "",
                  "worktree_boundary": {},
                  "resume_after_boundary_task_id": "",
                  "attachments": [], "artifacts": [],
                  "upstream_artifacts": []},
    "terminals": [],
}


class ActionManager:

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

    GLOBAL_ACTIONS_DIR = os.path.expanduser("~/.loom/actions")

    @staticmethod
    def _coalesce_prompt(act: dict) -> str:
        """Build a unified prompt from old-format or new-format fields.

        New format: ``prompt`` key.
        Old format: ``task`` + ``instructions`` + ``context`` + ``criteria``.
        """
        prompt = act.get("prompt", "")
        if prompt:
            return prompt
        parts = []
        for key in ("task", "instructions", "context", "criteria"):
            val = act.get(key, "")
            if val:
                parts.append(val.rstrip())
        return "\n\n".join(parts)

    @staticmethod
    def validate_prompt(prompt: str) -> bool:
        """Check that the prompt contains ``{{ TASK }}`` or
        ``{{ loom.task.title }}``."""
        return bool(
            re.search(r'\{\{\s*TASK\s*(\|[^}]*)?\}\}', prompt)
            or re.search(r'\{\{\s*loom\.task\.title\s*(\|[^}]*)?\}\}',
                         prompt))

    def render_prompt(self, name: str, variables: dict,
                      base_dir: str = "",
                      loom_context: dict | None = None) -> str | None:
        """Render only the action's prompt field with variables.

        Returns the rendered prompt string, or None if the action is
        not found.  Used by dispatch and preview.

        ``loom_context`` is injected as the ``loom`` namespace so
        templates can branch on agent state (e.g. ``loom.context.is_clean``).
        """
        raw = self._load_raw(name, base_dir)
        if raw is None:
            return None

        # Parse raw YAML (no Jinja2) to preserve {{ VAR }} placeholders
        try:
            act = parse_yaml(raw)
        except Exception:
            act = {}
        prompt_raw = self._coalesce_prompt(act)
        if not prompt_raw:
            return None

        prompt_raw = _migrate_syntax(prompt_raw)
        render_vars = dict(variables)
        render_vars["loom"] = loom_context or LOOM_CONTEXT_STUB
        return self._render_str(prompt_raw, render_vars)

    @staticmethod
    def find_actions_dirs(base_dir: str = "") -> list[str]:
        """Return action directories in priority order.

        Searches up from base_dir (or cwd) for a project-local
        .loom/actions/, then falls back to ~/.loom/actions/.
        Project actions take precedence over global ones.
        """
        dirs = []
        # Project-local: walk up from base_dir
        d = os.path.expanduser(base_dir) if base_dir else os.getcwd()
        if not os.path.isdir(d):
            d = os.getcwd()
        for _ in range(20):
            candidate = os.path.join(d, ".loom", "actions")
            if os.path.isdir(candidate):
                dirs.append(candidate)
                break
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        # Global: ~/.loom/actions/ (expand at call time for safety)
        g = os.path.expanduser("~/.loom/actions")
        if os.path.isdir(g) and g not in dirs:
            dirs.append(g)
        return dirs

    @staticmethod
    def find_actions_dir(base_dir: str = "") -> str | None:
        """Return the highest-priority actions directory, or None."""
        dirs = ActionManager.find_actions_dirs(base_dir)
        return dirs[0] if dirs else None

    def _load_raw(self, name: str, base_dir: str = "") -> str | None:
        """Load an action file as raw text. Searches all action dirs.

        Supports namespaced names like ``oneshot/feature`` which map to
        ``actions/oneshot/feature.yaml``.
        """
        for tdir in self.find_actions_dirs(base_dir):
            for suffix in ("", ".yaml", ".yml"):
                path = os.path.join(tdir, name + suffix)
                if os.path.isfile(path):
                    with open(path) as f:
                        return f.read()
        return None

    def list_actions(self, base_dir: str = "") -> list[dict]:
        """List all actions with name, description, vars, and scope.

        Returns all actions from all directories. When names collide
        across scopes, both are included — project actions are marked
        as the active one (used for dispatch), user actions with the
        same name are marked as ``shadowed``.

        Supports subdirectory namespaces: ``oneshot/feature.yaml`` is
        listed as action name ``oneshot/feature``.
        """
        results = []
        seen_names = set()  # track project-local names for shadowing
        for tdir in self.find_actions_dirs(base_dir):
            is_global = (tdir == os.path.expanduser("~/.loom/actions"))
            for dirpath, _dirnames, filenames in os.walk(tdir):
                for fname in sorted(filenames):
                    if not fname.endswith((".yaml", ".yml")):
                        continue
                    rel = os.path.relpath(
                        os.path.join(dirpath, fname), tdir)
                    name = rel.rsplit(".", 1)[0]
                    path = os.path.join(dirpath, fname)
                    shadowed = is_global and name in seen_names
                    try:
                        with open(path) as f:
                            raw = f.read()
                        try:
                            meta = parse_yaml(raw) or {}
                        except Exception:
                            meta = {}
                        desc = meta.get("description", "") if meta else ""
                        avars = self.get_action_vars(raw)
                    except Exception:
                        desc = "(parse error)"
                        avars = []
                    results.append({"name": name, "description": desc,
                                    "vars": avars, "global": is_global,
                                    "dir": tdir, "shadowed": shadowed})
                    if not is_global:
                        seen_names.add(name)
        return sorted(results, key=lambda t: (t["global"], t["name"]))

    def load_action(self, name: str, base_dir: str = "") -> dict | None:
        """Load action metadata (parsed as plain YAML). Returns dict or None."""
        raw = self._load_raw(name, base_dir)
        if raw is None:
            return None
        try:
            return parse_yaml(raw) or {}
        except Exception:
            return None

    def load_action_raw(self, name: str, base_dir: str = "") -> dict | None:
        """Load action as raw YAML (no Jinja2 rendering).

        Preserves ``{{ VAR }}`` placeholders in field values.
        Used by the action editor.
        """
        raw = self._load_raw(name, base_dir)
        if raw is None:
            return None
        try:
            return parse_yaml(raw)
        except Exception:
            return None

    def get_action_vars(self, raw_or_act) -> list[dict]:
        """Auto-discover variables from the action's prompt field.

        Parses only the prompt field as a Jinja2 template to find
        referenced variables. Extracts default values from
        ``| default()`` filters. TASK is always listed first and
        marked as required.

        Accepts either raw text (str) or a parsed dict (for backward
        compat — will just return TASK in that case).
        """
        if isinstance(raw_or_act, dict):
            # Legacy call with parsed dict — extract prompt only
            prompt = self._coalesce_prompt(raw_or_act)
            raw = _migrate_syntax(prompt) if prompt else ""
        else:
            # Extract prompt from raw YAML, then scan only that
            try:
                parsed = parse_yaml(raw_or_act)
            except Exception:
                parsed = {}
            if isinstance(parsed, dict):
                prompt = self._coalesce_prompt(parsed) if parsed else ""
            else:
                # Raw text is not valid YAML structure (or is a list);
                # treat the entire input as the prompt template
                prompt = raw_or_act
            raw = _migrate_syntax(prompt) if prompt else ""

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
        # Filter out 'loom' — it's a reserved namespace injected at render time
        discovered.discard("loom")
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

    def render_action(self, act_or_name, variables: dict,
                      base_dir: str = "",
                      loom_context: dict | None = None) -> dict:
        """Parse the action YAML, render only the prompt through Jinja2.

        Returns a flat dict with resolved agent settings:
        {name, command, directory, profile, shell, tab_color, env_vars,
         prompt, group, labels, worktree, terminals}
        """
        if isinstance(act_or_name, str) and "\n" not in act_or_name:
            # It's an action name, load raw
            raw = self._load_raw(act_or_name, base_dir)
            if not raw:
                return {}
            try:
                act = parse_yaml(raw)
            except Exception:
                return {}
        elif isinstance(act_or_name, str):
            raw = act_or_name
            try:
                act = parse_yaml(raw)
            except Exception:
                return {}
        else:
            # Legacy dict
            act = act_or_name

        if not isinstance(act, dict):
            return {}

        raw_agent = act.get("agent", {}) if isinstance(act, dict) else {}
        agent = raw_agent if isinstance(raw_agent, dict) else {}
        agent_template = raw_agent if isinstance(raw_agent, str) else ""

        # Coalesce prompt from new or old format, then render through Jinja2
        prompt_raw = self._coalesce_prompt(act)
        if prompt_raw:
            prompt_raw = _migrate_syntax(prompt_raw)
            render_vars = dict(variables)
            render_vars["loom"] = loom_context or LOOM_CONTEXT_STUB
            prompt = self._render_str(prompt_raw, render_vars)
        else:
            prompt = ""

        return {
            "name": agent.get("name_prefix", act.get("name", "agent")),
            "command": agent.get("command", ""),
            "directory": agent.get("directory", ""),
            "profile": agent.get("profile", ""),
            "shell": agent.get("shell", ""),
            "tab_color": agent.get("tab_color", ""),
            "env_vars": agent.get("env_vars", {}),
            "agent_template": agent_template,
            "prompt": prompt,
            "group": act.get("group", ""),
            "labels": act.get("labels", []),
            "worktree": act.get("worktree", None),
            "auto_close_on_done": bool(act.get("auto_close_on_done", False)),
            "terminals": act.get("terminals", []),
            "transitions": act.get("transitions", []),
            "max_depth": act.get("max_depth", None),
        }

    def get_auto_close_on_done(self, action_name: str,
                               base_dir: str = "") -> bool:
        """Return whether an action opts into auto-close on task done."""
        act = self.load_action(action_name, base_dir)
        if not isinstance(act, dict):
            return False
        return bool(act.get("auto_close_on_done", False))

    def get_transitions(self, action_name: str,
                         base_dir: str = "") -> list[dict]:
        """Return the transitions list for an action.

        Each entry is {action: str, when: str} or {ask: True, when: str}.
        Returns [] if action has no transitions or is not found.
        """
        act = self.load_action(action_name, base_dir)
        if not act:
            return []
        transitions = act.get("transitions") or []
        result = []
        for tr in transitions:
            if isinstance(tr, dict):
                action_target = tr.get("action", "")
                if action_target:
                    entry = {"action": action_target,
                             "when": tr.get("when", "")}
                    if tr.get("status"):
                        entry["status"] = tr["status"]
                    # target: self | parent | root | new (default)
                    target = tr.get("target", "")
                    # Backward compat: self: true → target: self
                    if not target and tr.get("self"):
                        target = "self"
                    if target:
                        entry["target"] = target
                    result.append(entry)
                elif tr.get("ask"):
                    result.append({"ask": True,
                                   "when": tr.get("when", "")})
            else:
                result.append(tr)
        return result

    def discover_pipelines(self, base_dir: str = "") -> list[dict]:
        """Scan all actions and discover pipelines from transitions.

        Returns a list of pipeline dicts:
        [{name, actions: [str], edges: [{from, to, when}],
          asks: [{from, when}]}]
        """
        actions = self.list_actions(base_dir)
        # Build adjacency: load transitions for each action
        graph = {}  # name → [{to, when}]
        asks = {}   # name → [{when}]
        all_names = set()
        for a in actions:
            if a.get("shadowed"):
                continue
            name = a["name"]
            all_names.add(name)
            act = self.load_action(name, base_dir)
            transitions = (act.get("transitions") or []) if act else []
            edges = []
            act_asks = []
            for tr in transitions:
                if isinstance(tr, dict):
                    target = tr.get("action")
                    if target:
                        edges.append({"to": target,
                                      "when": tr.get("when", "")})
                    elif tr.get("ask"):
                        act_asks.append({"when": tr.get("when", "")})
            graph[name] = edges
            if act_asks:
                asks[name] = act_asks

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

            # Collect ask transitions for actions in this component
            comp_asks = []
            for n in comp:
                for a in asks.get(n, []):
                    comp_asks.append({"from": n, "when": a["when"]})

            pipelines.append({
                "name": name,
                "actions": sorted(comp),
                "edges": edges,
                "asks": comp_asks,
            })

        return sorted(pipelines, key=lambda p: p["name"])

    # -- Internal helpers ---------------------------------------------------

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
