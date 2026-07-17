"""Agent type adapters — registry and auto-detection."""

import asyncio
import copy
import time

from .base import AgentAdapter, AgentEvent
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter
from .gemini_cli import GeminiCliAdapter
from .generic import GenericAdapter
from ..provider_catalog import discover_codex_models

ADAPTERS: list[AgentAdapter] = [
    ClaudeCodeAdapter(),
    CodexAdapter(),
    GeminiCliAdapter(),
    GenericAdapter(),  # fallback — always matches
]

_adapter_cache: dict[str, AgentAdapter] = {}
_provider_models_cache: dict[str, list[dict]] = {}
_provider_catalog_refreshed_at = 0.0
_provider_catalog_refresh_task: asyncio.Task | None = None
_PROVIDER_CATALOG_TTL_SECONDS = 6 * 60 * 60
_PROVIDER_CATALOG_FAILURE_TTL_SECONDS = 1


def detect_agent_type(process_name: str) -> AgentAdapter:
    """Match a process name to an adapter. Returns GenericAdapter as fallback."""
    for adapter in ADAPTERS:
        if adapter.match_process(process_name):
            return adapter
    return ADAPTERS[-1]


def detect_by_command(command: str) -> AgentAdapter | None:
    """Match a boot command to an adapter. Returns None if only Generic matches."""
    for adapter in ADAPTERS:
        if adapter.match_command(command):
            # Don't return GenericAdapter — it always matches
            if adapter.name != "generic":
                return adapter
            return None
    return None


def get_providers() -> list[dict]:
    """Return known providers (excluding generic fallback)."""
    providers = []
    for adapter in ADAPTERS:
        if not adapter.name or adapter.name == "generic":
            continue
        provider = {
            "name": adapter.name,
            "display_name": adapter.display_name,
            "command": adapter.default_command,
            "reasoning_efforts": adapter.get_reasoning_effort_options(),
        }
        models = _provider_models_cache.get(adapter.name)
        if models:
            provider["models"] = copy.deepcopy(models)
            provider["model_catalog_source"] = "detected"
        providers.append(provider)
    return providers


async def get_providers_async(*, force_refresh: bool = False) -> list[dict]:
    """Return provider metadata after refreshing optional model catalogs."""
    global _provider_catalog_refresh_task
    now = time.monotonic()
    refresh_ttl = (
        _PROVIDER_CATALOG_TTL_SECONDS
        if _provider_models_cache.get("codex")
        else _PROVIDER_CATALOG_FAILURE_TTL_SECONDS
    )
    fresh = (
        _provider_catalog_refreshed_at > 0
        and now - _provider_catalog_refreshed_at < refresh_ttl
    )
    if force_refresh or not fresh:
        if (
            _provider_catalog_refresh_task is None
            or _provider_catalog_refresh_task.done()
        ):
            _provider_catalog_refresh_task = asyncio.create_task(
                _refresh_provider_catalogs()
            )
        await _provider_catalog_refresh_task
    return get_providers()


async def _refresh_provider_catalogs() -> None:
    global _provider_catalog_refreshed_at
    models = await asyncio.to_thread(discover_codex_models)
    if models:
        _provider_models_cache["codex"] = models
    _provider_catalog_refreshed_at = time.monotonic()


def get_default_command_for_provider(name: str) -> str:
    """Look up an adapter by name and return its default boot command."""
    for a in ADAPTERS:
        if a.name == name:
            return a.default_command
    return ""


def get_adapter(agent_type: str) -> AgentAdapter:
    """Look up an adapter by its name (e.g. 'claude-code'). Cached."""
    if not agent_type:
        return ADAPTERS[-1]
    if agent_type not in _adapter_cache:
        for adapter in ADAPTERS:
            if adapter.name == agent_type:
                _adapter_cache[agent_type] = adapter
                break
        else:
            _adapter_cache[agent_type] = ADAPTERS[-1]
    return _adapter_cache[agent_type]


__all__ = [
    "ADAPTERS",
    "AgentAdapter",
    "AgentEvent",
    "detect_agent_type",
    "detect_by_command",
    "get_adapter",
    "get_providers",
    "get_providers_async",
    "get_default_command_for_provider",
]
