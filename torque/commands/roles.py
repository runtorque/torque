"""Role and template authoring commands."""

from __future__ import annotations

from ..dispatch_registry import AsyncHandlerRegistry


ROLE_TEMPLATE_COMMAND_NAMES = frozenset({
    "list_roles",
    "list_templates",
    "save_role",
    "save_template",
    "delete_role",
    "delete_template",
})


async def _handle_role_template_command(
    data: dict,
    role_mgr,
    resolve_base_dir,
) -> dict | None:
    cmd = data.get("cmd", "")
    response_type = ""
    item_key = ""
    item_name = ""

    if cmd == "list_roles":
        response_type = "roles"
        item_key = "roles"
    elif cmd == "list_templates":
        response_type = "templates"
        item_key = "templates"
    elif cmd == "save_role":
        response_type = "roles"
        item_key = "roles"
        item_name = "Role"
    elif cmd == "save_template":
        response_type = "templates"
        item_key = "templates"
        item_name = "Template"
    elif cmd == "delete_role":
        response_type = "roles"
        item_key = "roles"
        item_name = "Role"
    elif cmd == "delete_template":
        response_type = "templates"
        item_key = "templates"
        item_name = "Template"
    else:
        return None

    base_dir = await resolve_base_dir(data.get("group", ""))
    group = data.get("group", "")

    if cmd in {"list_roles", "list_templates"}:
        return {
            "type": response_type,
            "group": group,
            item_key: role_mgr.list_roles(base_dir),
        }

    name = data.get("name", "").strip()
    if not name:
        return {"type": "error", "message": f"{item_name} name required"}

    if cmd in {"save_role", "save_template"}:
        scope = data.get("scope", "project") or "project"
        old_name = data.get("old_name", "").strip()
        payload = data.get("data")
        if payload is None:
            payload = data.get("role")
        if payload is None:
            payload = data.get("template", {})
        old_scope = str(data.get("old_scope", "") or "").strip()
        if scope not in {"project", "user"} or old_scope not in {"", "project", "user"}:
            return {"type": "error", "message": "Catalog scope must be project or user"}
        if old_name and not old_scope:
            match = next((item for item in role_mgr.list_roles(base_dir)
                          if item.get("name") == old_name), None)
            old_scope = "user" if match and match.get("global") else "project"
        if not isinstance(payload, dict):
            return {"type": "error", "message": f"{item_name} definition must be an object"}
        try:
            # Keep the original until validation and replacement persistence succeed.
            role_mgr.save_role(name, payload, scope=scope, base_dir=base_dir)
            if old_name and (old_name != name or old_scope != scope):
                role_mgr.delete_template(old_name, scope=old_scope, base_dir=base_dir)
        except (ValueError, OSError) as exc:
            return {"type": "error", "message": str(exc)}
        return {
            "type": response_type,
            "group": group,
            item_key: role_mgr.list_roles(base_dir),
            "saved": name,
        }

    delete_fn = (
        role_mgr.delete_role if cmd == "delete_role" else role_mgr.delete_template
    )
    deleted = delete_fn(name, scope=data.get("scope", ""), base_dir=base_dir)
    if not deleted:
        return {"type": "error", "message": f'{item_name} "{name}" not found'}
    return {
        "type": response_type,
        "group": group,
        item_key: role_mgr.list_roles(base_dir),
        "deleted": name,
    }


_ROLE_TEMPLATE_COMMAND_REGISTRY = AsyncHandlerRegistry()
_ROLE_TEMPLATE_COMMAND_REGISTRY.register_many(
    ROLE_TEMPLATE_COMMAND_NAMES,
    _handle_role_template_command,
    label="roles",
)
