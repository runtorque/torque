"""AgentEvent dataclass and AgentAdapter base class."""

from dataclasses import dataclass, field
import shlex

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


@dataclass(frozen=True)
class InputReadyPolicy:
    """Policy for waiting until an interactive TUI can accept submitted input."""

    enabled: bool = False
    timeout_seconds: float = 0.0
    poll_interval_seconds: float = 0.25
    stable_polls: int = 1


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

    def inject_system_prompt(self, working_dir: str, text: str) -> str:
        """Install provider-specific system-prompt state and return CLI flags.

        File-based adapters can write into ``working_dir`` and return ``""``.
        Flag-based adapters can return the shell-escaped suffix to append to
        the boot command.
        """
        del working_dir
        if not text:
            return ""
        return f" --instructions {shlex.quote(text)}"

    def resolve_model_flags(self, model: str) -> str:
        """Return provider-specific CLI flags for the selected model."""
        if not model:
            return ""
        return f" --model {shlex.quote(model)}"

    def get_resume_command(self, boot_cmd: str, session_id: str) -> str | None:
        """Return the modified boot command for resuming a session.

        Returns None if this adapter doesn't support session resume.
        """
        return None

    def get_submit_key(self) -> str:
        """Return the key sequence that submits text in the adapter's TUI."""
        return "\r"

    def get_multiline_submit_delay(self) -> float:
        """Return the delay before sending submit after a multi-line body."""
        return 0.3

    def get_input_ready_policy(self) -> InputReadyPolicy:
        """Return how Loom should wait for the TUI to accept its first input."""
        return InputReadyPolicy()

    def is_input_ready_screen(self, screen_text: str) -> bool:
        """Return True when the visible screen indicates the TUI is ready."""
        return True

    def parse_event(self, raw: dict, cell) -> AgentEvent | None:
        """Parse an incoming hook payload into a normalized AgentEvent.

        Return None to skip the event.
        """
        return None
