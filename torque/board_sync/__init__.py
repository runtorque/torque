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


class BoardSyncProvider(Protocol):
    """Contract implemented by board-sync adapters."""

    name: str

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


__all__ = [
    "BoardSyncProvider",
    "BoardSyncProviderError",
    "get_provider",
    "normalize_provider_name",
    "register_provider",
]
