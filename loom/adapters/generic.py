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

    def inject_system_prompt(self, working_dir: str, text: str) -> str:
        del working_dir, text
        return ""

    def resolve_model_flags(self, model: str) -> str:
        del model
        return ""
