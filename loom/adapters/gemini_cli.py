"""Gemini CLI adapter — stub only (process matching, no hook integration yet)."""

from .base import AgentAdapter
import shlex


class GeminiCliAdapter(AgentAdapter):
    name = "gemini-cli"
    display_name = "Gemini CLI"
    default_command = "gemini"

    def match_process(self, process_name: str) -> bool:
        return process_name.lower() == "gemini"

    def match_command(self, command: str) -> bool:
        first = command.strip().split()[0] if command.strip() else ""
        return first.lower() == "gemini"

    def inject_system_prompt(self, working_dir: str, text: str) -> str:
        del working_dir, text
        return ""

    def resolve_model_flags(self, model: str) -> str:
        if not model:
            return ""
        return f" --model {shlex.quote(model)}"
