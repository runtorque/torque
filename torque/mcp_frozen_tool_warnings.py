"""Process-local warning deduplication for frozen MCP tool references."""

from __future__ import annotations

import json
from typing import Any, Iterable


# A daemon restart reloads this module and resets the session-local warning
# memory.  The frozen snapshot hash changes on relaunch, allowing the next
# session to emit one fresh warning for each removed tool.
_WARNED_REFERENCES: set[tuple[str, str, str]] = set()


def warn_removed_frozen_public_tools(
    *,
    cell_id: str,
    snapshot: dict[str, Any],
    missing_tools: Iterable[str],
    logger,
) -> None:
    """Emit each removed frozen tool warning at most once per snapshot."""

    snapshot_identity = str(snapshot.get("snapshot_hash", "") or "").strip()
    if not snapshot_identity:
        # Current launch snapshots always have a hash.  Keep manually-created
        # and legacy snapshots with references stable for this process too.
        snapshot_identity = json.dumps(
            snapshot,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    for tool_name in missing_tools:
        warning_key = (str(cell_id or "").strip(), snapshot_identity, tool_name)
        if warning_key in _WARNED_REFERENCES:
            continue
        logger.warning(
            "Frozen Agent Class %s@%s references removed public tool %s; "
            "skipping it during MCP projection",
            str(snapshot.get("id", "") or "<unknown>"),
            str(snapshot.get("version", "") or "<unknown>"),
            tool_name,
        )
        _WARNED_REFERENCES.add(warning_key)
