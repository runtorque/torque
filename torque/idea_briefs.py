"""Shared Idea Brief contract constants and normalization helpers."""

from __future__ import annotations

IDEA_BRIEF_STATUSES = frozenset({"draft", "proposed", "parked", "archived"})
IDEA_BRIEF_ACTIVE_STATUSES = frozenset({"draft", "proposed", "parked"})
IDEA_BRIEF_TEXT_FIELDS = (
    "title",
    "problem_opportunity",
    "why_it_matters",
    "proposed_shape",
    "smallest_useful_version",
    "risks_tradeoffs",
    "open_questions",
)
IDEA_BRIEF_BODY_FIELDS = tuple(field for field in IDEA_BRIEF_TEXT_FIELDS if field != "title")
IDEA_BRIEF_JSON_FIELDS = (
    "thinking_links",
    "source_context",
    "proposal",
    "refinement_log",
)
IDEA_BRIEF_DEFAULT_STATUS = "draft"
IDEA_BRIEF_PROPOSAL_SCOPE = "product_safe_review"
IDEA_BRIEF_CONTRACT_VERSION = 1


def normalize_idea_brief_status(value: str, *, default: str = IDEA_BRIEF_DEFAULT_STATUS) -> str:
    """Return a supported Idea Brief lifecycle status."""

    status = str(value or "").strip().lower()
    if not status:
        status = str(default or IDEA_BRIEF_DEFAULT_STATUS).strip().lower()
    if status not in IDEA_BRIEF_STATUSES:
        raise ValueError(
            "status must be one of: " + ", ".join(sorted(IDEA_BRIEF_STATUSES))
        )
    return status


def idea_brief_is_archived(row: dict | None) -> bool:
    """Return whether a decoded Idea Brief row is archived."""

    if not row:
        return False
    return (
        str(row.get("status", "") or "").strip().lower() == "archived"
        or bool(str(row.get("archived_at", "") or "").strip())
    )


def idea_brief_contract_metadata() -> dict:
    """Small serializable contract marker for API responses and docs."""

    return {
        "contract": "torque.idea_brief.v1",
        "contract_version": IDEA_BRIEF_CONTRACT_VERSION,
        "statuses": sorted(IDEA_BRIEF_STATUSES),
        "active_statuses": sorted(IDEA_BRIEF_ACTIVE_STATUSES),
        "proposal_scope": IDEA_BRIEF_PROPOSAL_SCOPE,
        "proposal_only": True,
    }
