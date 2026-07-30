"""Provider abstraction for syncing Torque board tasks with external boards.

V1 ships a GitHub Issues + Projects adapter.  The package shape is used so
provider implementations can live under ``torque.board_sync.<provider>`` while
``torque.board_sync`` remains the public protocol/factory surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover - imported only for type checkers.
    from torque.state import BoardTask, GroupSettings


@dataclass
class BoardSyncProviderError(Exception):
    """Structured provider lookup/call error."""

    provider: str
    phase: str
    error: str

    def to_dict(self) -> dict:
        return {
            "ok": False,
            "provider": self.provider,
            "phase": self.phase,
            "error": self.error,
        }


@dataclass(frozen=True)
class BoardSyncFieldConstraints:
    """Static outbound field limits exposed by a board-sync provider.

    These are deliberately configuration- and reachability-independent: a
    write guard must not perform a provider call merely to learn whether a
    value would be accepted later by the asynchronous sync worker.
    """

    title_max_length: int | None = None


class BoardSyncProvider(Protocol):
    """Contract implemented by board-sync adapters."""

    name: str

    def field_constraints(self) -> BoardSyncFieldConstraints:
        """Return known static limits for values sent to this provider."""

    async def preflight(self, group_settings: "GroupSettings") -> dict:
        """Return provider reachability/configuration metadata."""

    async def push_task(
        self,
        task: "BoardTask",
        group_settings: "GroupSettings",
    ) -> dict:
        """Push local task fields to the provider and return board_sync metadata."""

    async def pull_task(
        self,
        task: "BoardTask",
        group_settings: "GroupSettings",
    ) -> dict:
        """Return a preview diff from provider state; do not mutate local state."""

    async def apply_pull(
        self,
        task: "BoardTask",
        group_settings: "GroupSettings",
        fields: list[str],
    ) -> dict:
        """Return selected inbound values for the state layer to apply."""

    async def list_external_items(self, group_settings: "GroupSettings") -> list[dict]:
        """List provider/project items that could be imported or linked."""

    async def list_projects(self, owner: str | None = None) -> list[dict]:
        """List provider projects available to the current operator."""

    async def append_closing_refs(
        self,
        pr_body: str,
        linked_issues: list[dict],
        group_settings: "GroupSettings | None" = None,
    ) -> str:
        """Append provider-specific PR closing references when appropriate."""


class _StructuredErrorProvider:
    """Protocol-shaped provider that returns structured errors for all calls."""

    def __init__(self, name: str, *, error: str, skipped: bool = False):
        self.name = name
        self._error = error
        self._skipped = skipped

    def _result(self, phase: str) -> dict:
        result = {
            "ok": False,
            "provider": self.name,
            "phase": phase,
            "error": self._error,
        }
        if self._skipped:
            result["skipped"] = True
        return result

    def field_constraints(self) -> BoardSyncFieldConstraints:
        # Unknown/disabled providers intentionally expose no deterministic
        # limit. Their availability must never make a local task unwritable.
        return BoardSyncFieldConstraints()

    async def preflight(self, _group_settings) -> dict:
        return self._result("provider_lookup")

    async def push_task(self, task, _group_settings) -> dict:
        sync = dict(getattr(task, "board_sync", {}) or {})
        sync.update({
            "version": 1,
            "provider": self.name,
            "sync_state": "error",
            "last_error": self._error,
        })
        if self._skipped:
            sync["skipped"] = True
        return sync

    async def pull_task(self, _task, _group_settings) -> dict:
        return self._result("pull_preview")

    async def apply_pull(self, _task, _group_settings, _fields: list[str]) -> dict:
        return self._result("apply_pull")

    async def list_external_items(self, _group_settings) -> list[dict]:
        return [self._result("list_external_items")]

    async def list_projects(self, _owner: str | None = None) -> list[dict]:
        return [self._result("list_projects")]

    async def append_closing_refs(
        self,
        pr_body: str,
        _linked_issues: list[dict],
        _group_settings=None,
    ) -> str:
        return pr_body or ""


_PROVIDER_FACTORIES: dict[str, object] = {}
_BUILTINS_REGISTERED = False


def normalize_provider_name(name: str) -> str:
    return str(name or "none").strip().lower() or "none"


def register_provider(name: str, factory_or_provider) -> None:
    """Register a board-sync provider factory or provider instance."""
    normalized = normalize_provider_name(name)
    if normalized == "none":
        raise ValueError("'none' is reserved for disabled board sync")
    _PROVIDER_FACTORIES[normalized] = factory_or_provider


def _ensure_builtin_providers() -> None:
    global _BUILTINS_REGISTERED
    if _BUILTINS_REGISTERED:
        return
    from .github import GitHubBoardSyncProvider

    register_provider("github", GitHubBoardSyncProvider)
    _BUILTINS_REGISTERED = True


def get_provider(name: str) -> BoardSyncProvider:
    """Return a provider implementation.

    ``none`` is represented as a structured skipped provider so callers that
    accidentally invoke it still get a predictable result. Unknown provider
    names similarly return a protocol-shaped provider whose methods return a
    structured error dictionary.
    """
    normalized = normalize_provider_name(name)
    if normalized == "none":
        return _StructuredErrorProvider(
            "none",
            error="Board sync is disabled for push + manual reconcile.",
            skipped=True,
        )
    _ensure_builtin_providers()
    factory = _PROVIDER_FACTORIES.get(normalized)
    if factory is None:
        return _StructuredErrorProvider(
            normalized,
            error=f"Unknown board sync provider '{normalized}'.",
        )
    if isinstance(factory, type):
        return factory()
    if callable(factory) and not hasattr(factory, "preflight"):
        return factory()
    return factory


def title_validation_error(provider: BoardSyncProvider, title: str) -> str:
    """Return a deterministic title-limit refusal, without provider I/O.

    Providers opt into this guard by exposing a positive static title limit.
    An unknown provider or an adapter without such a limit remains writable.
    """
    constraints_getter = getattr(provider, "field_constraints", None)
    if not callable(constraints_getter):
        # Third-party adapters written before static constraints were added
        # retain the existing permissive behavior until they opt in.
        return ""
    constraints = constraints_getter()
    limit = constraints.title_max_length
    if not isinstance(limit, int) or limit <= 0 or len(title) <= limit:
        return ""
    provider_name = str(getattr(provider, "name", "board sync") or "board sync")
    provider_label = str(
        getattr(provider, "display_name", "") or ""
    ).strip()
    if not provider_label:
        provider_label = provider_name.replace("_", " ").replace("-", " ").title()
    return (
        f"Title is {len(title)} characters; {provider_label} board sync "
        f"supports at most {limit} characters for tracked task titles."
    )


__all__ = [
    "BoardSyncProvider",
    "BoardSyncFieldConstraints",
    "BoardSyncProviderError",
    "get_provider",
    "normalize_provider_name",
    "register_provider",
    "title_validation_error",
]
