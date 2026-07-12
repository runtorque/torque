"""Small declarative async handler registry used by command/tool facades."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class DispatchResult:
    handled: bool
    value: Any = None


@dataclass(frozen=True)
class _Route:
    label: str
    names: frozenset[str]
    prefix: str
    handler: Callable

    def matches(self, name: str) -> bool:
        return name in self.names or bool(
            self.prefix and name.startswith(self.prefix)
        )


class AsyncHandlerRegistry:
    """Resolve exact-name or prefix routes without a growing ``if`` ladder."""

    def __init__(self) -> None:
        self._exact: dict[str, _Route] = {}
        self._prefixes: list[_Route] = []

    def register_many(
        self,
        names: Iterable[str],
        handler: Callable,
        *,
        label: str = "",
    ) -> None:
        normalized = frozenset(str(name or "").strip() for name in names)
        if not normalized or "" in normalized:
            raise ValueError("Handler routes require non-empty names")
        duplicates = sorted(name for name in normalized if name in self._exact)
        if duplicates:
            raise ValueError(f"Handler route already registered: {duplicates}")
        route = _Route(label or sorted(normalized)[0], normalized, "", handler)
        for name in normalized:
            self._exact[name] = route

    def register_prefix(
        self,
        prefix: str,
        handler: Callable,
        *,
        label: str = "",
    ) -> None:
        normalized = str(prefix or "").strip()
        if not normalized:
            raise ValueError("Handler prefix must be non-empty")
        if any(route.prefix == normalized for route in self._prefixes):
            raise ValueError(f"Handler prefix already registered: {normalized}")
        self._prefixes.append(
            _Route(label or normalized, frozenset(), normalized, handler)
        )
        self._prefixes.sort(key=lambda route: len(route.prefix), reverse=True)

    def route_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._exact))

    def route_prefixes(self) -> tuple[str, ...]:
        return tuple(route.prefix for route in self._prefixes)

    async def dispatch(self, name: str, *args, **kwargs) -> DispatchResult:
        normalized = str(name or "").strip()
        route = self._exact.get(normalized)
        if route is None:
            route = next(
                (candidate for candidate in self._prefixes if candidate.matches(normalized)),
                None,
            )
        if route is None:
            return DispatchResult(False)
        value = route.handler(*args, **kwargs)
        if inspect.isawaitable(value):
            value = await value
        return DispatchResult(True, value)
