"""Gemini CLI adapter — template only (process matching, no hook integration yet)."""

from .base import AgentAdapter


class GeminiCliAdapter(AgentAdapter):
    name = "gemini-cli"
    display_name = "Gemini CLI"

    def match_process(self, process_name: str) -> bool:
        return process_name.lower() == "gemini"

    def match_command(self, command: str) -> bool:
        first = command.strip().split()[0] if command.strip() else ""
        return first.lower() == "gemini"
