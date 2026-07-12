#!/usr/bin/env python3
"""Stamp a release version into every Torque version source, deterministically.

Single source of truth for *which* files carry the release version and *how* each
is rewritten. Used by `.github/workflows/release-macos.yml` (the dispatch-to-release
flow) and runnable locally for validation.

The six sources, and who reads them:
  - VERSION                         runtime: torque daemon (server._read_torque_version)
  - torque/__init__.py __version__  runtime: mcp.SERVER_INFO + db migration major-gate
  - src-tauri/Cargo.toml            build-time: torque-desktop [package] version
  - src-tauri/tauri.conf.json       build-time: Tauri bundle version
  - src-tauri/Cargo.lock            build-time: torque-desktop self-entry (kept in sync)
  - README.md                       operator-facing version badge

Usage:
    scripts/set_release_version.py X.Y.Z [--repo-root DIR] [--check]

Exit codes:
    0  all sources now read the target version (or already did, with --check)
    1  bad version / missing-or-malformed source / --check mismatch
"""
import argparse
import re
import sys
from pathlib import Path

VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def _sub_once(pattern, repl, text, *, label):
    new_text, n = re.subn(pattern, repl, text, count=1)
    if n != 1:
        raise ValueError(f"{label}: expected exactly one match, found {n}")
    return new_text


def _stamp_version_file(path: Path, version: str) -> None:
    path.write_text(f"{version}\n", encoding="utf-8")


def _stamp_init_py(path: Path, version: str) -> str:
    text = path.read_text(encoding="utf-8")
    return _sub_once(
        r'(?m)^__version__\s*=\s*"[^"]*"',
        f'__version__ = "{version}"',
        text,
        label="torque/__init__.py __version__",
    )


def _stamp_cargo_toml(path: Path, version: str) -> str:
    # Rewrite only the [package] version (first `version = "..."` after `[package]`),
    # never a dependency's version.
    text = path.read_text(encoding="utf-8")
    return _sub_once(
        r'(?s)(\[package\][^\[]*?\nversion = ")[^"]*(")',
        lambda m: f"{m.group(1)}{version}{m.group(2)}",
        text,
        label="src-tauri/Cargo.toml [package] version",
    )


def _stamp_tauri_conf(path: Path, version: str) -> str:
    # Top-level "version" key (2-space indent), first occurrence only.
    text = path.read_text(encoding="utf-8")
    return _sub_once(
        r'(?m)^(  "version":\s*)"[^"]*"',
        lambda m: f'{m.group(1)}"{version}"',
        text,
        label="src-tauri/tauri.conf.json top-level version",
    )


def _stamp_cargo_lock(path: Path, version: str) -> str:
    # The torque-desktop self-entry: the `version` line immediately following its
    # `name = "torque-desktop"` line. Never another package's version.
    text = path.read_text(encoding="utf-8")
    return _sub_once(
        r'(name = "torque-desktop"\nversion = ")[^"]*(")',
        lambda m: f"{m.group(1)}{version}{m.group(2)}",
        text,
        label='src-tauri/Cargo.lock torque-desktop self-entry',
    )


def _stamp_readme_badge(path: Path, version: str) -> str:
    text = path.read_text(encoding="utf-8")
    return _sub_once(
        r"(shields\.io/badge/version-)[0-9]+\.[0-9]+\.[0-9]+(-green\.svg)",
        lambda match: f"{match.group(1)}{version}{match.group(2)}",
        text,
        label="README.md version badge",
    )


# (relative path, reader, writer-or-None). VERSION uses the plain file writer.
_TEXT_SOURCES = [
    ("torque/__init__.py", _stamp_init_py),
    ("src-tauri/Cargo.toml", _stamp_cargo_toml),
    ("src-tauri/tauri.conf.json", _stamp_tauri_conf),
    ("src-tauri/Cargo.lock", _stamp_cargo_lock),
    ("README.md", _stamp_readme_badge),
]


def _read_observed_versions(root: Path) -> dict:
    """Best-effort read of the current version in each source (for --check / verify)."""
    observed = {}
    observed["VERSION"] = (root / "VERSION").read_text(encoding="utf-8").strip()
    m = re.search(r'(?m)^__version__\s*=\s*"([^"]*)"', (root / "torque/__init__.py").read_text("utf-8"))
    observed["torque/__init__.py"] = m.group(1) if m else None
    m = re.search(r'(?s)\[package\][^\[]*?\nversion = "([^"]*)"', (root / "src-tauri/Cargo.toml").read_text("utf-8"))
    observed["src-tauri/Cargo.toml"] = m.group(1) if m else None
    m = re.search(r'(?m)^  "version":\s*"([^"]*)"', (root / "src-tauri/tauri.conf.json").read_text("utf-8"))
    observed["src-tauri/tauri.conf.json"] = m.group(1) if m else None
    m = re.search(r'name = "torque-desktop"\nversion = "([^"]*)"', (root / "src-tauri/Cargo.lock").read_text("utf-8"))
    observed["src-tauri/Cargo.lock"] = m.group(1) if m else None
    m = re.search(r"shields\.io/badge/version-([0-9.]+)-green", (root / "README.md").read_text("utf-8"))
    observed["README.md"] = m.group(1) if m else None
    return observed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Stamp the release version into all Torque version sources.")
    parser.add_argument("version", help="Release version, e.g. 2.1.0 (no leading 'v').")
    parser.add_argument("--repo-root", default=None, help="Repo root (default: two levels up from this script).")
    parser.add_argument("--check", action="store_true", help="Verify all sources equal VERSION; do not write.")
    args = parser.parse_args(argv)

    version = args.version.lstrip("v").strip()
    if not VERSION_RE.match(version):
        print(f"error: version must look like X.Y.Z (got {args.version!r})", file=sys.stderr)
        return 1

    root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parents[1]

    for rel in ["VERSION", "torque/__init__.py", "src-tauri/Cargo.toml", "src-tauri/tauri.conf.json", "src-tauri/Cargo.lock", "README.md"]:
        if not (root / rel).is_file():
            print(f"error: missing version source: {rel} (under {root})", file=sys.stderr)
            return 1

    if args.check:
        observed = _read_observed_versions(root)
        bad = {k: v for k, v in observed.items() if v != version}
        for k, v in observed.items():
            print(f"  {k}: {v}{'  <-- MISMATCH' if k in bad else ''}")
        if bad:
            print(f"error: {len(bad)} source(s) do not equal {version}", file=sys.stderr)
            return 1
        print(f"ok: all 6 sources read {version}")
        return 0

    try:
        _stamp_version_file(root / "VERSION", version)
        for rel, stamper in _TEXT_SOURCES:
            path = root / rel
            path.write_text(stamper(path, version), encoding="utf-8")
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    observed = _read_observed_versions(root)
    bad = {k: v for k, v in observed.items() if v != version}
    if bad:
        print(f"error: post-write verification failed for {sorted(bad)} (expected {version})", file=sys.stderr)
        return 1

    print(f"Stamped version {version} into:")
    for rel in observed:
        print(f"  {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
