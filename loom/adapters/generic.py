"""Generic adapter — fallback for unrecognized agents (process monitoring only)."""

from .base import AgentAdapter


class GenericAdapter(AgentAdapter):
    name = "generic"
    display_name = "Generic"

    def match_process(self, process_name: str) -> bool:
        # Always matches — this is the fallback
        return True

    def match_command(self, command: str) -> bool:
        return True
