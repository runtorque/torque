#!/usr/bin/env python3
"""Guard that community install artifacts do not include enterprise files."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_ALLOWED_TOP_LEVEL = {
    ".torque_source_repo_root",
    "static",
    "torque",
    "torque.py",
    "torque_desktop.py",
    "webview.html",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _relative_files(root: Path) -> list[Path]:
    return sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())


def assert_no_ee_paths(artifact_dir: Path) -> None:
    leaked = [
        str(path)
        for path in _relative_files(artifact_dir)
        if "ee" in path.parts
    ]
    if leaked:
        sample = "\n".join(f"  - {path}" for path in leaked[:20])
        raise AssertionError(
            "Community install artifact leaked enterprise ee/ files:\n" + sample
        )


def assert_expected_top_level(artifact_dir: Path) -> None:
    unexpected = sorted(
        entry.name
        for entry in artifact_dir.iterdir()
        if entry.name not in DEFAULT_ALLOWED_TOP_LEVEL
    )
    if unexpected:
        raise AssertionError(
            "Community install artifact contains unexpected top-level entries: "
            + ", ".join(unexpected)
        )


def build_standalone_artifact(repo_root: Path, artifact_dir: Path) -> subprocess.CompletedProcess[str]:
    make = shutil.which("make")
    if not make:
        raise RuntimeError("make is required for community packaging guard")
    env = os.environ.copy()
    env.pop("TORQUE_DATA_DIR", None)
    env.pop("TORQUE_PROFILE", None)
    env.pop("TORQUE_STANDALONE", None)
    return subprocess.run(
        [
            make,
            "--no-print-directory",
            "install-standalone",
            f"PRIMARY_APP_DIR={artifact_dir}",
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_repo_root())
    parser.add_argument("--keep-artifact", action="store_true")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    with tempfile.TemporaryDirectory(prefix="torque-community-install-") as tmp:
        artifact_dir = Path(tmp) / "app"
        proc = build_standalone_artifact(repo_root, artifact_dir)
        if proc.returncode != 0:
            sys.stderr.write(proc.stdout)
            sys.stderr.write(proc.stderr)
            return proc.returncode
        try:
            assert_expected_top_level(artifact_dir)
            assert_no_ee_paths(artifact_dir)
        except AssertionError as exc:
            sys.stderr.write(str(exc) + "\n")
            return 1
        if args.keep_artifact:
            kept = repo_root / ".tmp-community-install-artifact"
            if kept.exists():
                shutil.rmtree(kept)
            shutil.copytree(artifact_dir, kept)
            print(f"kept artifact at {kept}")
    print("community install artifact excludes ee/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
