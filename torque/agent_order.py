"""Reconcile saved terminal order with authoritative parent relationships."""

from collections.abc import Mapping


def ordered_terminal_children(agents: Mapping, saved_order=None) -> dict[str, list[str]]:
    """Saved ordering may rank valid children, never create/reparent records."""
    def field(cell, name, default=""):
        if isinstance(cell, Mapping):
            return cell.get(name, default)
        return getattr(cell, name, default)

    children = {aid: [] for aid, cell in agents.items()
                if field(cell, "cell_type", "agent") == "agent"}
    for aid, cell in agents.items():
        parent = field(cell, "parent_id")
        if (field(cell, "cell_type") == "terminal"
                and isinstance(parent, str) and parent in children):
            children[parent].append(aid)
    if not isinstance(saved_order, dict):
        return children
    for parent, actual in children.items():
        preferred = saved_order.get(parent)
        if not isinstance(preferred, list):
            continue
        valid = set(actual)
        ordered = []
        for aid in preferred:
            if isinstance(aid, str) and aid in valid:
                ordered.append(aid)
                valid.remove(aid)
        ordered.extend(aid for aid in actual if aid in valid)
        children[parent] = ordered
    return children
