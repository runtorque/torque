"""Playbook, action, template, role, and specialization commands."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from ..dispatch_registry import AsyncHandlerRegistry
from .roles import ROLE_TEMPLATE_COMMAND_NAMES, _ROLE_TEMPLATE_COMMAND_REGISTRY


CATALOG_COMMAND_NAMES = frozenset({
    'get_playbook_candidates',
    'extract_playbook_candidates',
    'get_playbooks',
    'get_playbook',
    'generate_playbook_draft',
    'publish_playbook_draft',
    'discard_playbook_draft',
    'list_actions',
    'list_action_catalog',
    'list_specializations',
    'get_specialization',
    'save_specialization',
    'delete_specialization',
    'set_engineer_specializations',
    'get_template',
    'render_template',
    'get_action',
    'render_action',
    'save_action',
    'delete_action',
}) | ROLE_TEMPLATE_COMMAND_NAMES


@dataclass(frozen=True, slots=True)
class CatalogCommandRuntime:
    state: Any
    db: Any
    action_mgr: Any
    template_mgr: Any
    specialization_mgr: Any
    resolve_base_dir: Any
    handle_set_engineer_specializations_command: Any
    action_to_yaml: Any


async def handle_catalog_command(
    data: dict,
    runtime: CatalogCommandRuntime,
) -> dict | None:
    cmd = str(data.get("cmd", "") or "")
    state = runtime.state
    db = runtime.db
    action_mgr = runtime.action_mgr
    template_mgr = runtime.template_mgr
    specialization_mgr = runtime.specialization_mgr
    _resolve_base_dir = runtime.resolve_base_dir
    _handle_set_engineer_specializations_command = (
        runtime.handle_set_engineer_specializations_command
    )
    _action_to_yaml = runtime.action_to_yaml

    if cmd == "get_playbook_candidates":
        limit = min(int(data.get("limit", 50)), 200)
        return {
            "type": "playbook_candidates",
            "group": data.get("group", ""),
            "candidates": state.list_playbook_candidates(
                group=data.get("group", ""), limit=limit),
        }

    if cmd == "extract_playbook_candidates":
        return {
            "type": "playbook_candidates",
            "group": data.get("group", ""),
            "candidates": state.extract_playbook_candidates(
                group=data.get("group", "")),
        }

    if cmd == "get_playbooks":
        limit = min(int(data.get("limit", 50)), 200)
        return {
            "type": "playbooks",
            "group": data.get("group", ""),
            "status": data.get("status", ""),
            "playbooks": state.list_playbooks(
                group=data.get("group", ""),
                status=data.get("status", ""),
                limit=limit,
            ),
        }

    if cmd == "get_playbook":
        playbook_id = data.get("id", "")
        if not playbook_id:
            return {"type": "error", "message": "id required"}
        playbook = state.get_playbook(playbook_id)
        if not playbook:
            return {"type": "error", "message": "Playbook not found"}
        return {"type": "playbook_detail", "playbook": playbook}

    if cmd == "generate_playbook_draft":
        candidate_id = data.get("candidate_id", "")
        if not candidate_id:
            return {"type": "error",
                    "message": "candidate_id required"}
        candidate = db.load_playbook_candidate(candidate_id)
        if not candidate:
            return {"type": "error",
                    "message": "Playbook candidate not found"}
        base_dir = await _resolve_base_dir(
            data.get("group", "") or candidate.get("group", ""))
        from ..playbooks import build_playbook_draft

        draft = build_playbook_draft(
            candidate, action_mgr, template_mgr, base_dir=base_dir)
        existing = state.get_playbook(draft["id"])
        if existing and existing.get("status") == "published":
            draft["created_at"] = existing.get("created_at",
                                                draft["created_at"])
            draft["published_at"] = existing.get("published_at")
            draft["status"] = existing.get("status", "published")
        state.save_playbook(draft)
        return {"type": "playbook_detail", "playbook": draft}

    if cmd == "publish_playbook_draft":
        playbook_id = data.get("id", "")
        if not playbook_id:
            return {"type": "error", "message": "id required"}
        playbook = state.get_playbook(playbook_id)
        if not playbook:
            return {"type": "error", "message": "Playbook not found"}
        if playbook.get("status") != "draft":
            return {"type": "error",
                    "message": "Only draft playbooks can be published"}
        preview = playbook.get("publication_preview", {})
        if not preview.get("ready_to_publish", False):
            return {"type": "error",
                    "message": "Draft is missing required action "
                               "or template references"}
        from ..playbooks import publish_playbook_record

        published = publish_playbook_record(playbook)
        state.save_playbook(published)
        return {"type": "playbook_detail", "playbook": published}

    if cmd == "discard_playbook_draft":
        playbook_id = data.get("id", "")
        if not playbook_id:
            return {"type": "error", "message": "id required"}
        playbook = state.get_playbook(playbook_id)
        if not playbook:
            return {"type": "error", "message": "Playbook not found"}
        if playbook.get("status") != "draft":
            return {"type": "error",
                    "message": "Only draft playbooks can be discarded"}
        from ..playbooks import discard_playbook_record

        discarded = discard_playbook_record(playbook)
        state.save_playbook(discarded)
        return {"type": "playbook_detail", "playbook": discarded}

    # list_action_catalog: dispatch-effective, read-only action metadata.
    # Keep ``list_actions`` below unchanged for the editor, which needs
    # shadowed user entries to remain visible and editable.
    if cmd == "list_action_catalog":
        base_dir = await _resolve_base_dir(data.get("group", ""))
        actions = action_mgr.list_effective_actions(base_dir)
        return {
            "type": "action_catalog",
            "group": data.get("group", ""),
            "actions": actions,
        }

    # list_actions: respond directly
    if cmd == "list_actions":
        base_dir = await _resolve_base_dir(data.get("group", ""))
        actions = action_mgr.list_actions(base_dir)
        return {"type": "actions", "group": data.get("group", ""),
                "actions": actions}

    role_template_dispatch = await _ROLE_TEMPLATE_COMMAND_REGISTRY.dispatch(
        cmd,
        data,
        template_mgr,
        _resolve_base_dir,
    )
    if role_template_dispatch.handled:
        return role_template_dispatch.value

    if cmd == "list_specializations":
        base_dir = await _resolve_base_dir(data.get("group", ""))
        scope = data.get("scope", "") or ""
        items = specialization_mgr.list_specializations(
            base_dir=base_dir, scope=scope)
        return {
            "type": "specializations",
            "group": data.get("group", ""),
            "specializations": items,
        }

    if cmd == "get_specialization":
        base_dir = await _resolve_base_dir(data.get("group", ""))
        scope = data.get("scope", "") or ""
        name = str(data.get("name", "") or "").strip()
        if not name:
            return {"type": "error",
                    "message": "Specialization name required"}
        spec = specialization_mgr.get_specialization(
            name, base_dir=base_dir, scope=scope)
        if not spec:
            return {
                "type": "error",
                "message": f"Specialization \"{name}\" not found",
            }
        return {
            "type": "specialization_detail",
            "name": name,
            "specialization": spec,
        }

    if cmd == "save_specialization":
        base_dir = await _resolve_base_dir(data.get("group", ""))
        scope = data.get("scope", "project") or "project"
        name = str(data.get("name", "") or "").strip()
        if not name:
            return {"type": "error",
                    "message": "Specialization name required"}
        payload = data.get("data")
        if payload is None:
            payload = data.get("specialization", {})
        old_name = str(data.get("old_name", "") or "").strip()
        old_scope = str(data.get("old_scope", "") or "").strip()
        if old_name and (old_name != name or (
                old_scope and old_scope != scope)):
            if old_scope:
                specialization_mgr.delete_specialization(
                    old_name, scope=old_scope, base_dir=base_dir)
            else:
                specialization_mgr.delete_specialization(
                    old_name, base_dir=base_dir)
                specialization_mgr.delete_specialization(
                    old_name, scope="user", base_dir=base_dir)
        try:
            specialization_mgr.save_specialization(
                name, payload or {}, scope=scope, base_dir=base_dir)
        except ValueError as exc:
            return {"type": "error", "message": str(exc)}
        return {
            "type": "specializations",
            "group": data.get("group", ""),
            "specializations": specialization_mgr.list_specializations(
                base_dir=base_dir),
            "saved": name,
        }

    if cmd == "delete_specialization":
        base_dir = await _resolve_base_dir(data.get("group", ""))
        scope = data.get("scope", "") or ""
        name = str(data.get("name", "") or "").strip()
        if not name:
            return {"type": "error",
                    "message": "Specialization name required"}
        deleted = specialization_mgr.delete_specialization(
            name, scope=scope, base_dir=base_dir)
        if not deleted:
            return {
                "type": "error",
                "message": f"Specialization \"{name}\" not found",
            }
        return {
            "type": "specializations",
            "group": data.get("group", ""),
            "specializations": specialization_mgr.list_specializations(
                base_dir=base_dir),
            "deleted": name,
        }

    if cmd == "set_engineer_specializations":
        return await _handle_set_engineer_specializations_command(
            data,
            state,
            resolve_base_dir=_resolve_base_dir,
            specialization_mgr=specialization_mgr,
        )

    if cmd == "get_template":
        base_dir = await _resolve_base_dir(data.get("group", ""))
        scope = data.get("scope", "")
        tpl = template_mgr.load_template(
            data.get("name", ""), base_dir, scope=scope)
        if not tpl:
            return {"type": "error",
                    "message": f"Template \"{data['name']}\" not found"}
        return {"type": "template_detail", "name": data["name"],
                "template": tpl}

    if cmd == "render_template":
        base_dir = await _resolve_base_dir(data.get("group", ""))
        group = data.get("group", "")
        gs = state.get_group_settings(group)
        rendered = template_mgr.resolve_agent_config(
            data.get("name", ""), gs, data.get("overrides", {}),
            base_dir=base_dir)
        return {
            "type": "template_rendered",
            "name": data.get("name", ""),
            "config": rendered,
        }

    # get_action: respond directly
    if cmd == "get_action":
        base_dir = await _resolve_base_dir(data.get("group", ""))
        scope = data.get("scope", "")
        # Scope-aware loading: search only the target directory
        raw = None
        if scope == "user":
            gdir = os.path.expanduser("~/.torque/actions")
            for suffix in ("", ".yaml", ".yml"):
                p = os.path.join(gdir, data["name"] + suffix)
                if os.path.isfile(p):
                    with open(p) as f:
                        raw = f.read()
                    break
        if raw is None:
            raw = action_mgr._load_raw(data["name"], base_dir)
        if not raw:
            return {"type": "error",
                    "message": f"Action \"{data['name']}\" not found"}
        # Editor mode: parse raw YAML without Jinja2 rendering
        from ..actions import parse_yaml
        try:
            act = parse_yaml(raw) or {}
        except Exception:
            act = {}
        avars = action_mgr.get_action_vars(raw)
        return {"type": "action_detail", "name": data["name"],
                "action": act, "vars": avars}

    # render_action: render action prompt without creating an agent
    if cmd == "render_action":
        base_dir = await _resolve_base_dir(data.get("group", ""))
        raw = action_mgr._load_raw(data["name"], base_dir)
        if not raw:
            return {"type": "error",
                    "message": f"Action \"{data['name']}\" not found"}
        variables = data.get("vars", {})
        rendered = action_mgr.render_action(raw, variables)
        return {"type": "action_rendered",
                "name": data["name"],
                "prompt": rendered.get("prompt", ""),
                "group": rendered.get("group", ""),
                "labels": rendered.get("labels", [])}

    # save_action: write action YAML to disk
    if cmd == "save_action":
        name = data.get("name", "").strip()
        if not name:
            return {"type": "error", "message": "Action name required"}
        act_data = data.get("action", {})
        # Validate {{ TASK }} in prompt
        prompt = act_data.get("prompt", "")
        if not action_mgr.validate_prompt(prompt):
            return {"type": "error",
                    "message": "Action prompt must contain {{ TASK }}"}
        # Reject 'torque' as a variable name (reserved namespace)
        avars = action_mgr.get_action_vars(prompt)
        for av in avars:
            if av.get("name") == "torque":
                return {"type": "error",
                        "message": "'torque' is a reserved variable "
                                   "name"}
        scope = data.get("scope", "project")  # "project" or "user"
        base_dir = await _resolve_base_dir(data.get("group", ""))

        if scope == "user":
            tdir = os.path.expanduser("~/.torque/actions")
            os.makedirs(tdir, exist_ok=True)
        else:
            tdir = action_mgr.find_actions_dir(base_dir)
            if not tdir:
                d = base_dir or os.getcwd()
                tdir = os.path.join(d, ".torque", "actions")
                os.makedirs(tdir, exist_ok=True)
        # Rename or scope change: delete old file from any location
        old_name = data.get("old_name", "")
        if old_name:
            for old_dir in action_mgr.find_actions_dirs(base_dir):
                for suffix in (".yaml", ".yml"):
                    old_path = os.path.join(old_dir, old_name + suffix)
                    if os.path.isfile(old_path):
                        os.remove(old_path)
                        break
        path = os.path.join(tdir, name + ".yaml")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        yaml_text = _action_to_yaml(name, act_data)
        with open(path, "w") as f:
            f.write(yaml_text)
        # Return updated list
        actions = action_mgr.list_actions(base_dir)
        return {"type": "actions",
                "group": data.get("group", ""),
                "actions": actions,
                "saved": name}

    # delete_action: remove action file from disk
    if cmd == "delete_action":
        name = data.get("name", "").strip()
        if not name:
            return {"type": "error", "message": "Action name required"}
        base_dir = await _resolve_base_dir(data.get("group", ""))
        deleted = False
        for tdir in action_mgr.find_actions_dirs(base_dir):
            for suffix in (".yaml", ".yml"):
                path = os.path.join(tdir, name + suffix)
                if os.path.isfile(path):
                    os.remove(path)
                    deleted = True
                    break
            if deleted:
                break
        if not deleted:
            return {"type": "error",
                    "message": f"Action \"{name}\" not found"}
        actions = action_mgr.list_actions(base_dir)
        return {"type": "actions",
                "group": data.get("group", ""),
                "actions": actions,
                "deleted": name}


_CATALOG_COMMAND_REGISTRY = AsyncHandlerRegistry()
_CATALOG_COMMAND_REGISTRY.register_many(
    CATALOG_COMMAND_NAMES,
    handle_catalog_command,
    label="catalog",
)
