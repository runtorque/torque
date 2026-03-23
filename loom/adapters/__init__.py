"""Agent type adapters — registry and auto-detection."""

from .base import AgentAdapter, AgentEvent
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter
from .gemini_cli import GeminiCliAdapter
from .generic import GenericAdapter

ADAPTERS: list[AgentAdapter] = [
    ClaudeCodeAdapter(),
    CodexAdapter(),
    GeminiCliAdapter(),
    GenericAdapter(),  # fallback — always matches
]

_adapter_cache: dict[str, AgentAdapter] = {}


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
]
