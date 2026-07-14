"""Small agent identity and guidance helpers shared by server domains."""

from __future__ import annotations

from .config import log
from .state import MatrixState


def _resolve_agent_id(state, identifier: str) -> str:
    """Resolve an agent by exact ID, slug, name, or ID prefix."""
    ident = str(identifier or "").strip()
    if not ident:
        return ""
    if ident in state.agents:
        cell = state.agents[ident]
        if cell.cell_type == "agent" and not state.agent_is_tombstoned(cell):
            return cell.id
    ident_lower = ident.lower()
    for cell in state.iter_active_agents():
        if cell.cell_type != "agent":
            continue
        if cell.slug == ident_lower:
            return cell.id
    for cell in state.iter_active_agents():
        if cell.cell_type != "agent":
            continue
        if cell.name.lower() == ident_lower:
            return cell.id
    for cell in state.iter_active_agents():
        if cell.cell_type != "agent":
            continue
        if cell.id.startswith(ident):
            return cell.id
    return ""

def _should_show_guidance_hint(state: MatrixState | None,
                               cell,
                               hint_type: str) -> bool:
    """Delegate recurring soft-hint cadence to state when available."""
    checker = getattr(state, "should_show_guidance_hint", None)
    if not callable(checker):
        return True
    try:
        return bool(checker(hint_type, cell))
    except Exception:
        log.exception(
            "Failed to evaluate guidance hint cadence for hint=%s cell=%s",
            hint_type,
            getattr(cell, "id", ""),
        )
        return True
