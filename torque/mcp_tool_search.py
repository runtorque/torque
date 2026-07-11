"""Deferred MCP tool discovery helpers."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Iterable


TOOL_SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Search keywords, or select exact deferred tools with "
                "select:tool_a,tool_b."
            ),
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum schemas to return (default 5).",
            "default": 5,
        },
    },
    "required": ["query"],
}


def make_tool_search_spec(name: str, surface: str) -> dict:
    """Return the eager schema for a deferred-tool search tool."""
    return {
        "name": name,
        "authority": {
            "requirements": [{"capability": "tool.search"}],
        },
        "description": (
            f"Search {surface} MCP tools that are available on demand but "
            "omitted from the eager tools/list response. Use select:name1,name2 "
            "for exact tool names. Returns matching tool schemas in a "
            "<functions>{...}</functions> block."
        ),
        "inputSchema": deepcopy(TOOL_SEARCH_INPUT_SCHEMA),
    }


def is_deferred_tool(tool: dict) -> bool:
    return bool((tool or {}).get("deferred"))


def public_tool_spec(tool: dict) -> dict:
    """Return a copy of a tool schema without Torque-internal metadata."""
    spec = deepcopy(tool or {})
    spec.pop("deferred", None)
    spec.pop("authority", None)
    return spec


def eager_tool_specs(tools: Iterable[dict]) -> list[dict]:
    return [public_tool_spec(tool) for tool in tools if not is_deferred_tool(tool)]


def deferred_tool_specs(tools: Iterable[dict]) -> list[dict]:
    return [public_tool_spec(tool) for tool in tools if is_deferred_tool(tool)]


def _max_results(value) -> int:
    try:
        max_results = int(value)
    except (TypeError, ValueError):
        max_results = 5
    if max_results <= 0:
        return 5
    return min(max_results, 50)


def _tool_search_blob(tool: dict) -> str:
    return json.dumps(public_tool_spec(tool), sort_keys=True, default=str).lower()


def _keyword_terms(query: str) -> list[str]:
    return [term for term in re.split(r"[\s,]+", query.lower().strip()) if term]


def _score_keyword_match(tool: dict, terms: list[str]) -> int:
    if not terms:
        return 0
    name = str(tool.get("name", "") or "").lower()
    description = str(tool.get("description", "") or "").lower()
    blob = _tool_search_blob(tool)
    score = 0
    for term in terms:
        if term not in blob:
            return 0
        if term == name:
            score += 100
        elif term in name:
            score += 20
        elif term in description:
            score += 5
        else:
            score += 1
    return score


def _format_functions_block(matches: list[dict]) -> str:
    return "<functions>" + json.dumps({"tools": matches}, indent=2) + "</functions>"


def tool_search_response(tools: Iterable[dict], args: dict) -> str:
    """Search deferred tools and return a <functions> JSON block."""
    args = args or {}
    query = str(args.get("query", "") or "").strip()
    max_results = _max_results(args.get("max_results", 5))
    deferred = [tool for tool in tools if is_deferred_tool(tool)]

    if query.lower().startswith("select:"):
        requested = [
            name.strip()
            for name in query[len("select:"):].split(",")
            if name.strip()
        ]
        by_name = {str(tool.get("name", "") or ""): tool for tool in deferred}
        matches = [
            public_tool_spec(by_name[name])
            for name in requested
            if name in by_name
        ][:max_results]
        return _format_functions_block(matches)

    terms = _keyword_terms(query)
    if not terms:
        return _format_functions_block([])

    scored = []
    for tool in deferred:
        score = _score_keyword_match(tool, terms)
        if score > 0:
            scored.append((score, str(tool.get("name", "") or ""), tool))
    scored.sort(key=lambda item: (-item[0], item[1]))
    matches = [public_tool_spec(tool) for _score, _name, tool in scored[:max_results]]
    return _format_functions_block(matches)
