"""Execution-routing scope helpers.

These helpers are intentionally small and side-effect free so both MCP tools
and the server command layer can enforce the same cross-kind task routing rule.
"""

from __future__ import annotations

from collections.abc import Mapping


def _snapshot_base_kind(snapshot: object) -> str:
    if not isinstance(snapshot, Mapping):
        return ""
    base_kind = str(snapshot.get("base_kind", "") or "").strip()
    if base_kind:
        return base_kind
    runtime = snapshot.get("runtime", {})
    if isinstance(runtime, Mapping):
        return str(runtime.get("base_kind", "") or "").strip()
    return ""


def is_architect_execution_target(cell: object | None) -> bool:
    """Return true when a cell/class represents an Architect execution target.

    Engineers may coordinate with Architects through explicit messaging and
    notification tools, but must not route executable task prompts to
    Architect cells or Architect-base Agent Classes.  Prefer the concrete
    runtime ``kind``; the effective class/profile snapshots cover stale or
    partially migrated class metadata.
    """
    if cell is None:
        return False
    if str(getattr(cell, "cell_type", "") or "").strip() != "agent":
        return False
    if str(getattr(cell, "kind", "") or "").strip() == "architect":
        return True
    for attr in (
        "effective_agent_class_snapshot",
        "effective_agent_profile_snapshot",
    ):
        if _snapshot_base_kind(getattr(cell, attr, None)) == "architect":
            return True
    return False


def engineer_architect_task_routing_denied_message(target: object | None) -> str:
    name = str(getattr(target, "name", "") or getattr(target, "slug", "") or "Architect").strip()
    return (
        "Engineer-originated executable task routing to Architect targets/classes "
        f"is denied: {name}. Use architect↔engineer messaging/notifications "
        "or have the Architect originate their own routing instead."
    )


def engineer_architect_close_denied_message(target: object | None) -> str:
    name = str(getattr(target, "name", "") or getattr(target, "slug", "") or "Architect").strip()
    return (
        "Engineer-originated close/remove of Architect targets/classes is "
        f"denied: {name}. Use architect lifecycle controls from the user/"
        "Architect-owned surface instead."
    )
