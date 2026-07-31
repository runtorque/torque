"""Detect backend file-size invariant crossings between Git revisions."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import subprocess
from typing import Sequence


DEFAULT_BACKEND_LINE_LIMIT = 2500
HEADROOM_WARNING_THRESHOLD = 10
BACKEND_LINE_LIMITS = {
    "torque/server.py": 6000,
    "torque/state.py": 5000,
    "torque/db_schema.py": 3800,
    "torque/doctor.py": 2600,
    # Architecture-reviewed transport seam: behavior-overlay conditional
    # validation must run after authorization and before write/idempotency
    # setup. Moving it outward would couple JSON-RPC/wake/idempotency response
    # concerns to authorization or change the rejection boundary.
    "torque/mcp.py": 2600,
}

# These facades preserve long-lived import contracts, but direct behavior
# belongs in their domain mixins/services.  WorktreeManager is deliberately a
# structural exception: its sole direct method is construction.
COMPATIBILITY_FACADE_METHOD_LIMITS = {
    ("torque/state.py", "MatrixState"): 103,
    ("torque/db.py", "TorqueDB"): 50,
    ("torque/worktree.py", "WorktreeManager"): 1,
}


class BackendInvariantCheckError(RuntimeError):
    """Raised when the repository cannot supply trustworthy crossing data."""


def backend_file_line_limit(relative_path: str) -> int | None:
    """Return the reviewed line limit for one backend Python path."""
    relative_path = str(relative_path or "").strip().replace("\\", "/")
    if not relative_path.startswith("torque/") or not relative_path.endswith(".py"):
        return None
    return BACKEND_LINE_LIMITS.get(relative_path, DEFAULT_BACKEND_LINE_LIMIT)


def backend_modularity_headroom(repo_root: str | Path) -> list[dict]:
    """Return the current margin for the explicitly reviewed guard budgets."""
    root = Path(repo_root).expanduser().resolve()
    measurements = []
    for (relative_path, class_name), limit in (
        COMPATIBILITY_FACADE_METHOD_LIMITS.items()
    ):
        path = root / relative_path
        tree = ast.parse(path.read_text(), filename=str(path))
        class_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        actual = sum(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for node in class_node.body
        )
        measurements.append({
            "kind": "method",
            "path": relative_path,
            "subject": class_name,
            "actual": actual,
            "limit": limit,
            "headroom": limit - actual,
        })
    for relative_path, limit in BACKEND_LINE_LIMITS.items():
        actual = len((root / relative_path).read_text().splitlines())
        measurements.append({
            "kind": "line",
            "path": relative_path,
            "actual": actual,
            "limit": limit,
            "headroom": limit - actual,
        })
    return measurements


def format_backend_modularity_headroom(measurements: Sequence[dict]) -> str:
    """Format a compact report that keeps green-run budget margins visible."""
    details = []
    for item in measurements:
        subject = f":{item['subject']}" if item["kind"] == "method" else ""
        details.append(
            f"{item['kind']} {item['path']}{subject} "
            f"{item['actual']}/{item['limit']} (headroom {item['headroom']})"
        )
    return "Backend modularity headroom: " + "; ".join(details)


def _git(repo_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip()
        raise BackendInvariantCheckError(
            f"git {' '.join(args)} failed"
            + (f": {detail}" if detail else "")
        )
    return proc


def _revision_file_line_count(
    repo_root: Path,
    revision: str,
    relative_path: str,
) -> int:
    proc = _git(repo_root, "show", f"{revision}:{relative_path}", check=False)
    if proc.returncode == 0:
        return len(proc.stdout.decode(errors="replace").splitlines())
    missing = _git(
        repo_root,
        "cat-file",
        "-e",
        f"{revision}:{relative_path}",
        check=False,
    )
    if missing.returncode != 0:
        return 0
    detail = proc.stderr.decode(errors="replace").strip()
    raise BackendInvariantCheckError(
        f"could not read {relative_path} at {revision}"
        + (f": {detail}" if detail else "")
    )


def check_backend_modularity_crossings(
    repo_root: str | Path,
    base_ref: str,
    candidate_ref: str,
) -> dict:
    """Report backend files whose candidate size newly exceeds its limit."""
    root = Path(repo_root).expanduser().resolve()
    base_ref = str(base_ref or "").strip()
    candidate_ref = str(candidate_ref or "").strip()
    if not root.is_dir() or not base_ref or not candidate_ref:
        raise BackendInvariantCheckError(
            "repo_root, base_ref, and candidate_ref are required"
        )
    for revision in (base_ref, candidate_ref):
        _git(root, "rev-parse", "--verify", f"{revision}^{{commit}}")

    marker = _git(
        root,
        "cat-file",
        "-e",
        f"{base_ref}:tests/test_backend_modularity.py",
        check=False,
    )
    if marker.returncode != 0:
        return {
            "ok": True,
            "applicable": False,
            "phase": "backend_modularity",
            "base_ref": base_ref,
            "candidate_ref": candidate_ref,
            "checked_files": [],
            "crossings": [],
            "headroom": [],
            "warnings": [],
        }

    changed = _git(
        root,
        "diff",
        "--name-only",
        "--diff-filter=ACMRT",
        base_ref,
        candidate_ref,
        "--",
        "torque",
    ).stdout.decode(errors="replace").splitlines()
    checked_files = sorted({
        path.strip().replace("\\", "/")
        for path in changed
        if backend_file_line_limit(path) is not None
    })
    crossings = []
    headroom = []
    warnings = []
    for relative_path in checked_files:
        limit = backend_file_line_limit(relative_path)
        base_lines = _revision_file_line_count(root, base_ref, relative_path)
        candidate_lines = _revision_file_line_count(
            root,
            candidate_ref,
            relative_path,
        )
        measurement = {
            "path": relative_path,
            "limit": limit,
            "base_lines": base_lines,
            "candidate_lines": candidate_lines,
            "base_headroom": limit - base_lines,
            "candidate_headroom": limit - candidate_lines,
        }
        headroom.append(measurement)
        if 0 <= measurement["candidate_headroom"] <= HEADROOM_WARNING_THRESHOLD:
            warnings.append(measurement)
        if base_lines <= limit < candidate_lines:
            crossings.append({
                "path": relative_path,
                "limit": limit,
                "base_lines": base_lines,
                "candidate_lines": candidate_lines,
            })
    return {
        "ok": not crossings,
        "applicable": True,
        "phase": "backend_modularity",
        "base_ref": base_ref,
        "candidate_ref": candidate_ref,
        "checked_files": checked_files,
        "crossings": crossings,
        # Green candidates remain green.  The warning payload makes a nearly
        # exhausted margin visible to the caller before it becomes a crossing.
        "headroom": headroom,
        "warnings": warnings,
    }


def format_backend_modularity_crossings(result: dict) -> str:
    """Format a merge-blocking explanation for detected crossings."""
    crossings = list((result or {}).get("crossings", []) or [])
    details = ", ".join(
        f"{item['path']} ({item['base_lines']} -> {item['candidate_lines']}; "
        f"limit {item['limit']})"
        for item in crossings
    )
    return (
        "Backend modularity preflight blocked files that newly exceed their "
        f"reviewed line limit: {details}. Split by responsibility or obtain "
        "an explicit architecture review and budget before merging."
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check a Git change for backend file-size crossings.",
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--candidate-ref", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = check_backend_modularity_crossings(
            args.repo,
            args.base_ref,
            args.candidate_ref,
        )
    except BackendInvariantCheckError as exc:
        print(json.dumps({"ok": False, "phase": "backend_modularity", "error": str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
