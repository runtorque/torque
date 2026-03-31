"""AgentEvent dataclass and AgentAdapter base class."""

from dataclasses import dataclass, field

EVENT_TYPES = frozenset({
    "session_start",
    "session_end",
    "activity_change",
    "tool_start",
    "tool_end",
    "message",
    "error",
    "waiting",
    "progress",
    "cost_update",
})


@dataclass
class AgentEvent:
    """A normalized event produced by agent adapters."""
    cell_id: str
    timestamp: float
    event_type: str
    data: dict = field(default_factory=dict)


class AgentAdapter:
    """Base class for agent type adapters.

    Each adapter translates agent-specific signals (hooks, JSONL output,
    process monitoring) into normalized AgentEvent objects.
    """

    name: str = ""
    display_name: str = ""
    default_command: str = ""

    def match_process(self, process_name: str) -> bool:
        """Return True if this process name indicates this agent type."""
        return False

    def match_command(self, command: str) -> bool:
        """Return True if this boot command indicates this agent type."""
        return False

    def get_hook_config(self, cell) -> dict | None:
        """Return hook config to write into the agent's environment.

        Returns None if this adapter doesn't use hooks.
        """
        return None

    def get_env_vars(self, cell) -> dict[str, str]:
        """Return extra environment variables to set when spawning."""
        return {}

    def get_resume_command(self, boot_cmd: str, session_id: str) -> str | None:
        """Return the modified boot command for resuming a session.

        Returns None if this adapter doesn't support session resume.
        """
        return None

    def parse_event(self, raw: dict, cell) -> AgentEvent | None:
        """Parse an incoming hook payload into a normalized AgentEvent.

        Return None to skip the event.
        """
        return None
