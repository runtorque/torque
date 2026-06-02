"""Lazy optional AI dependency probes.

The helpers in this module must never import heavy optional packages.  Use
``importlib.util.find_spec`` only so base Torque imports stay lightweight even
when AI embeddings are configured.
"""

from __future__ import annotations

import importlib.util
from typing import Callable, Literal


DependencyStatus = Literal["available", "missing"]

AI_DEPS_INSTALL_HINT = "make ai-deps"
AI_DEPENDENCY_PACKAGES = ("sentence-transformers", "sqlite-vec")
_AI_DEPENDENCY_MODULES = (
    ("sentence_transformers", "sentence-transformers"),
    ("sqlite_vec", "sqlite-vec"),
)


def missing_ai_dependency_packages(
    *,
    find_spec: Callable[[str], object | None] = importlib.util.find_spec,
) -> list[str]:
    """Return optional AI package names whose import specs are unavailable.

    ``find_spec`` is injectable for tests and must be a spec lookup only: do not
    replace this with ``importlib.import_module`` or an actual package import.
    """

    missing: list[str] = []
    for module_name, package_name in _AI_DEPENDENCY_MODULES:
        try:
            spec = find_spec(module_name)
        except (AttributeError, ImportError, ValueError):
            spec = None
        if spec is None:
            missing.append(package_name)
    return missing


def embeddings_dependency_status(
    *,
    find_spec: Callable[[str], object | None] = importlib.util.find_spec,
) -> DependencyStatus:
    """Report whether all optional embedding dependencies are discoverable."""

    missing = missing_ai_dependency_packages(find_spec=find_spec)
    return "missing" if missing else "available"
