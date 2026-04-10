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
    hook_event: bool = False
    screen_fallback: bool = False
    timeout_seconds: float = 0.0
    poll_interval_seconds: float = 0.25
    stable_polls: int = 1
    post_ready_delay: float = 0.0


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

    def inject_persistent_prompt(self, working_dir: str,
                                 filename: str, text: str) -> str:
        """Install a persistent prompt that survives /clear and resume.

        Like ``inject_system_prompt`` but uses a delivery mechanism that
        persists across session resets (file-based for both Claude Code and
        Codex).  Returns CLI flags to append, or ``""`` for file-only.
        """
        return self.inject_system_prompt(working_dir, text)

    def uninstall_persistent_prompt(self, working_dir: str,
                                    filename: str = "") -> None:
        """Remove persistent prompts installed by inject_persistent_prompt."""
        pass

    def startup_prompt_from_persistent_prompt(self, text: str) -> str:
        """Return a startup message that preserves old prompt semantics.

        Most adapters deliver persistent prompts out-of-band (for example via
        provider-specific instructions files), so they should return ``""``.
        Adapters that historically exposed the persistent prompt as the first
        conversation turn can override this to keep launch behavior stable.
        """
        del text
        return ""

    def resolve_model_flags(self, model: str) -> str:
        """Return provider-specific CLI flags for the selected model."""
        if not model:
            return ""
        return f" --model {shlex.quote(model)}"

    def resolve_reasoning_effort_flags(self, reasoning_effort: str) -> str:
        """Return provider-specific CLI flags for reasoning effort."""
        del reasoning_effort
        return ""

    def get_reasoning_effort_options(self) -> list[str]:
        """Return supported reasoning-effort options for UI affordances."""
        return []

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

    def get_input_chunks(self, body: str) -> list[str]:
        """Return the ordered terminal chunks used to enter a prompt body.

        The default path sends the whole body as one chunk. Adapters can
        override this when their TUI handles pasted multi-line input poorly
        and needs explicit newline shortcut sequences instead.
        """
        return [body] if body else []

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
