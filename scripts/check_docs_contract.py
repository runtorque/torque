#!/usr/bin/env python3
"""Fail when first-party documentation drifts from repository contracts."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_FILES = [
    *sorted(ROOT.glob("*.md")),
    *sorted((ROOT / "docs").rglob("*.md")),
]
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _missing_markdown_links() -> list[str]:
    errors = []
    for path in MARKDOWN_FILES:
        text = path.read_text(encoding="utf-8")
        for match in LINK_RE.finditer(text):
            target = match.group(1).split("#", 1)[0].strip()
            if not target or "://" in target or target.startswith(
                ("mailto:", "app:")
            ):
                continue
            resolved = (path.parent / target).resolve()
            if resolved.exists():
                continue
            line = text.count("\n", 0, match.start()) + 1
            errors.append(
                f"{path.relative_to(ROOT)}:{line}: missing link target {target}"
            )
    return errors


def _version_contract_errors() -> list[str]:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"shields\.io/badge/version-([0-9.]+)-green", readme)
    errors = []
    if not match:
        errors.append("README.md: version badge is missing or malformed")
    elif match.group(1) != version:
        errors.append(
            "README.md: version badge "
            f"{match.group(1)} does not match VERSION {version}"
        )
    issue_template = (
        ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml"
    ).read_text(encoding="utf-8")
    if re.search(r"currently [`'\"]?[0-9]+\.[0-9]+\.[0-9]+", issue_template):
        errors.append("bug_report.yml: do not hard-code the current version")
    return errors


def _architecture_contract_errors() -> list[str]:
    architecture = (
        ROOT / "docs" / "reference" / "architecture.md"
    ).read_text(encoding="utf-8")
    roadmap = (ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
    errors = []
    if "schema with 8 tables" in architecture:
        errors.append("architecture.md: obsolete eight-table schema description")
    if "schema_migrations" not in architecture:
        errors.append("architecture.md: versioned migration ledger is undocumented")
    if "Codex and Gemini CLI have stub adapters" in roadmap:
        errors.append("roadmap.md: Codex adapter maturity is stale")
    return errors


def main() -> int:
    errors = [
        *_missing_markdown_links(),
        *_version_contract_errors(),
        *_architecture_contract_errors(),
    ]
    if errors:
        for error in errors:
            print(f"docs contract error: {error}", file=sys.stderr)
        return 1
    print(
        f"Documentation contracts passed ({len(MARKDOWN_FILES)} Markdown files checked)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
