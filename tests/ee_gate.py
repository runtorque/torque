"""Shared gate for enterprise-only tests.

Community checkouts may not have the ``ee/`` tree present once it is moved to a
submodule.  EE-dependent tests must therefore opt in explicitly via
``TORQUE_WITH_EE=1`` and verify the required enterprise paths exist before any
imports from ``ee/`` run.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
FLAG_NAME = "TORQUE_WITH_EE"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def torque_with_ee_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether the caller explicitly opted into enterprise tests."""

    source = os.environ if env is None else env
    value = source.get(FLAG_NAME, "")
    return str(value).strip().lower() in _TRUE_VALUES


def missing_ee_paths(
    required_paths: Sequence[str | Path] = (),
    *,
    root: Path = ROOT,
) -> list[str]:
    """Return required EE paths that are absent, as repo-relative strings."""

    seen: set[str] = set()
    missing: list[str] = []
    paths: list[str | Path] = ["ee", *required_paths]
    for raw in paths:
        rel = Path(raw)
        path = rel if rel.is_absolute() else root / rel
        label = str(rel) if not rel.is_absolute() else str(path)
        if not path.exists() and label not in seen:
            seen.add(label)
            missing.append(label)
    return missing


def ee_skip_reason(
    required_paths: Sequence[str | Path] = (),
    *,
    env: Mapping[str, str] | None = None,
    root: Path = ROOT,
) -> str:
    """Explain why an EE-dependent test is skipped, or return ``""``."""

    if not torque_with_ee_enabled(env):
        return (
            "EE tests skipped: set TORQUE_WITH_EE=1 to run enterprise tests "
            "(requires an ee/ checkout)."
        )
    missing = missing_ee_paths(required_paths, root=root)
    if missing:
        return (
            "EE tests skipped: TORQUE_WITH_EE=1 but missing enterprise paths: "
            + ", ".join(missing)
        )
    return ""


def ee_tests_enabled(
    required_paths: Sequence[str | Path] = (),
    *,
    env: Mapping[str, str] | None = None,
    root: Path = ROOT,
) -> bool:
    """Return whether EE tests should run for the required paths."""

    return ee_skip_reason(required_paths, env=env, root=root) == ""


def require_ee_tests_enabled(required_paths: Sequence[str | Path] = ()) -> None:
    """Raise ``SkipTest`` before importing enterprise-only packages/files."""

    reason = ee_skip_reason(required_paths)
    if reason:
        raise unittest.SkipTest(reason)
