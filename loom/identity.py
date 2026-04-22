"""Agent identity helpers used by prompt and message envelopes."""

from __future__ import annotations


IDENTITY_KINDS = {"architect", "engineer", "worker"}


def agent_kind_for_identity(cell) -> str:
    """Return the identity-grounding kind for an agent-like cell."""
    if not cell or getattr(cell, "cell_type", "agent") != "agent":
        return ""
    kind = str(getattr(cell, "kind", "") or "").strip()
    if kind in IDENTITY_KINDS:
        return kind
    # Legacy worker cells may predate the explicit ``kind='worker'`` field.
    if str(getattr(cell, "created_by_weaver_id", "") or "").strip():
        return "worker"
    return ""


def agent_identity_anchor(cell) -> str:
    """Return the in-band identity anchor for a prompt/message recipient."""
    name = str(getattr(cell, "name", "") or "").strip()
    cell_id = str(getattr(cell, "id", "") or "").strip()
    kind = agent_kind_for_identity(cell)
    if not name or not cell_id or not kind:
        return ""
    return f"You are {name} ({kind}, id={cell_id})."


def prepend_agent_identity_anchor(text: str, cell) -> str:
    """Prepend the identity anchor unless it is already present."""
    body = str(text or "").strip("\n")
    anchor = agent_identity_anchor(cell)
    if not anchor:
        return body
    if body == anchor or body.startswith(anchor + "\n"):
        return body
    if not body:
        return anchor
    return f"{anchor}\n\n{body}"
