"""Codex CLI adapter — template only (process matching, no hook integration yet)."""

from .base import AgentAdapter


class CodexAdapter(AgentAdapter):
    name = "codex"
    display_name = "Codex"

    def match_process(self, process_name: str) -> bool:
        return process_name.lower() == "codex"

    def match_command(self, command: str) -> bool:
        first = command.strip().split()[0] if command.strip() else ""
        return first.lower() == "codex"
