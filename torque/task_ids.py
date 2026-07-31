"""Helpers for canonical Torque task identifiers."""

from __future__ import annotations

import re
import unicodedata


DRAFT_TASK_PREFIX = "draft-"
_INITIATIVE_ID_RE = re.compile(
    r"^(?P<group_prefix>[A-Z][A-Z0-9_]*)-I:(?P<number>[1-9][0-9]*)$"
)
_AREA_ID_RE = re.compile(
    r"^(?P<group_prefix>[A-Z][A-Z0-9_]*)-A:(?P<number>[1-9][0-9]*)$"
)
_SCRATCHPAD_NOTE_ID_RE = re.compile(
    r"^(?P<group_prefix>[A-Z][A-Z0-9_]*)-S:(?P<number>[1-9][0-9]*)$"
)
_IDEA_BRIEF_ID_RE = re.compile(
    r"^(?P<group_prefix>[A-Z][A-Z0-9_]*)-IB:(?P<number>[1-9][0-9]*)$"
)
_TASK_ID_RE = re.compile(
    r"^(?P<prefix>[A-Z][A-Z0-9_]*):(?P<root>[1-9][0-9]*)(?::(?P<child>[1-9][0-9]*))?$"
)


def normalize_group_prefix(group_name: str) -> str:
    """Return the canonical uppercase task-ID prefix for a group name."""
    normalized = unicodedata.normalize("NFKD", str(group_name or "").strip())
    asciiish = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    value = re.sub(r"[^A-Za-z0-9]+", "_", asciiish.upper())
    value = re.sub(r"_+", "_", value).strip("_")
    if not value:
        value = "GROUP"
    if value[0].isdigit():
        value = f"G_{value}"
    return value


def parse_initiative_id(initiative_id: str) -> dict | None:
    """Parse a canonical Initiative ID, or return None if invalid.

    Initiative IDs deliberately use ``<GROUP>-I:<n>`` (for example
    ``TORQUE-I:1``) instead of a plain ``INIT:<n>`` prefix so they never
    collide with canonical Board task IDs.
    """
    match = _INITIATIVE_ID_RE.match(str(initiative_id or "").strip())
    if not match:
        return None
    return {
        "group_prefix": match.group("group_prefix"),
        "number": int(match.group("number")),
    }


def is_canonical_initiative_id(initiative_id: str) -> bool:
    return parse_initiative_id(initiative_id) is not None


def format_initiative_id(group_prefix: str, number: int) -> str:
    return f"{normalize_group_prefix(group_prefix)}-I:{int(number)}"


def parse_area_id(area_id: str) -> dict | None:
    """Parse a canonical Area ID, or return None if invalid.

    Area IDs deliberately use ``<GROUP>-A:<n>`` (for example
    ``TORQUE-A:1``) so they cannot collide with Board task IDs or
    Initiative IDs.
    """
    match = _AREA_ID_RE.match(str(area_id or "").strip())
    if not match:
        return None
    return {
        "group_prefix": match.group("group_prefix"),
        "number": int(match.group("number")),
    }


def is_canonical_area_id(area_id: str) -> bool:
    return parse_area_id(area_id) is not None


def format_area_id(group_prefix: str, number: int) -> str:
    return f"{normalize_group_prefix(group_prefix)}-A:{int(number)}"


def parse_scratchpad_note_id(note_id: str) -> dict | None:
    """Parse a canonical Thinking Scratchpad note ID.

    Scratchpad IDs use ``<GROUP>-S:<n>`` so they do not collide with Board
    tasks, Initiatives, Areas, or other planning artifacts.
    """
    match = _SCRATCHPAD_NOTE_ID_RE.match(str(note_id or "").strip())
    if not match:
        return None
    return {
        "group_prefix": match.group("group_prefix"),
        "number": int(match.group("number")),
    }


def is_canonical_scratchpad_note_id(note_id: str) -> bool:
    return parse_scratchpad_note_id(note_id) is not None


def format_scratchpad_note_id(group_prefix: str, number: int) -> str:
    return f"{normalize_group_prefix(group_prefix)}-S:{int(number)}"


def parse_idea_brief_id(brief_id: str) -> dict | None:
    """Parse a canonical Idea Brief ID.

    Idea Brief IDs use ``<GROUP>-IB:<n>`` so proposal artifacts remain
    distinct from Board tasks, Planning objects, and Thinking artifacts.
    """
    match = _IDEA_BRIEF_ID_RE.match(str(brief_id or "").strip())
    if not match:
        return None
    return {
        "group_prefix": match.group("group_prefix"),
        "number": int(match.group("number")),
    }


def is_canonical_idea_brief_id(brief_id: str) -> bool:
    return parse_idea_brief_id(brief_id) is not None


def format_idea_brief_id(group_prefix: str, number: int) -> str:
    return f"{normalize_group_prefix(group_prefix)}-IB:{int(number)}"


def parse_task_id(task_id: str) -> dict | None:
    """Parse a canonical task ID, or return None if the format is invalid."""
    match = _TASK_ID_RE.match(str(task_id or "").strip())
    if not match:
        return None
    child = match.group("child")
    return {
        "prefix": match.group("prefix"),
        "root_number": int(match.group("root")),
        "child_number": int(child) if child else None,
        "is_derived": bool(child),
    }


def is_canonical_task_id(task_id: str) -> bool:
    return parse_task_id(task_id) is not None


def is_draft_task_token(task_id: str) -> bool:
    return str(task_id or "").startswith(DRAFT_TASK_PREFIX)


def format_root_task_id(group_prefix: str, task_number: int) -> str:
    return f"{normalize_group_prefix(group_prefix)}:{int(task_number)}"


def format_derived_task_id(group_prefix: str, root_number: int,
                           derived_task_number: int) -> str:
    return (
        f"{normalize_group_prefix(group_prefix)}:{int(root_number)}:"
        f"{int(derived_task_number)}"
    )


def root_task_number(task_id: str) -> int | None:
    parsed = parse_task_id(task_id)
    if not parsed:
        return None
    return parsed["root_number"]
