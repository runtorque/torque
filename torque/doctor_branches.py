"""Read-only classification of non-ancestral local Torque branches."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path


def _git(directory: str, *args: str) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", directory, *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, "", str(exc)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _json_object(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _task_branch_records(conn: sqlite3.Connection) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT id, worktree_boundary, completion_evidence "
            "FROM board_tasks ORDER BY rowid"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    records = []
    for task_id, raw_boundary, raw_evidence in rows:
        boundary = _json_object(raw_boundary)
        evidence = _json_object(raw_evidence)
        merge = evidence.get("merge") if isinstance(evidence.get("merge"), dict) else {}
        review = evidence.get("review") if isinstance(evidence.get("review"), dict) else {}
        branches = {
            str(boundary.get("branch") or "").strip(),
            str(merge.get("branch") or "").strip(),
        }
        branches.discard("")
        merge_shas = []
        for value in (
            merge.get("sha"),
            (merge.get("pr") or {}).get("merge_commit_sha")
            if isinstance(merge.get("pr"), dict) else "",
        ):
            value = str(value or "").strip()
            if value and value not in merge_shas:
                merge_shas.append(value)
        records.append({
            "task_id": str(task_id or ""),
            "branches": branches,
            "repo_root": os.path.realpath(os.path.expanduser(str(
                boundary.get("repo_root") or ""
            ))) if boundary.get("repo_root") else "",
            "merge_shas": merge_shas,
            "historical_without_merge": bool(
                not merge_shas
                and (
                    isinstance(evidence.get("completion"), dict)
                    or str(review.get("verdict") or "").strip().lower() == "ship"
                )
            ),
        })
    return records


def _configured_repo(
    conn: sqlite3.Connection,
    project_base_dir: str | Path | None,
) -> str:
    configured = project_base_dir or os.environ.get("TORQUE_REPO_ROOT")
    if configured:
        return str(configured)
    try:
        row = conn.execute(
            "SELECT worktree_repo_root, COUNT(*) AS uses, MAX(rowid) AS latest "
            "FROM agents "
            "WHERE worktree_repo_root != '' "
            "GROUP BY worktree_repo_root ORDER BY uses DESC, latest DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    return str(row[0] or "") if row else ""


def _checked_out_branches(repo_root: str) -> set[str]:
    code, output, _ = _git(repo_root, "worktree", "list", "--porcelain")
    if code != 0:
        return set()
    return {
        line.removeprefix("branch refs/heads/").strip()
        for line in output.splitlines()
        if line.startswith("branch refs/heads/")
    }


def collect_squash_branch_cleanup_section(
    conn: sqlite3.Connection,
    project_base_dir: str | Path | None,
) -> dict:
    """Classify without mutating refs, worktrees, tasks, or evidence."""
    section = {
        "available": False,
        "repo_root": "",
        "base_branch": "main",
        "total_local_torque_branches": 0,
        "total_non_ancestral": 0,
        "excluded_worktree_count": 0,
        "counts": {"landed": 0, "unmerged": 0, "unknown": 0},
        "branches": [],
        "errors": [],
        "read_only": True,
    }
    configured_repo = _configured_repo(conn, project_base_dir)
    if not configured_repo:
        section["errors"].append("repository_not_configured")
        return section
    base_dir = os.path.expanduser(str(configured_repo))
    code, common_dir, error = _git(
        base_dir, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    if code != 0 or not common_dir:
        section["errors"].append(error or "not_a_git_repository")
        return section
    repo_root = str(Path(common_dir).resolve().parent)
    canonical_repo_root = os.path.realpath(repo_root)
    section["repo_root"] = repo_root
    code, output, error = _git(
        repo_root,
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/heads/torque/",
    )
    if code != 0:
        section["errors"].append(error or "branch_list_failed")
        return section
    branches = sorted(line.strip() for line in output.splitlines() if line.strip())
    section["available"] = True
    section["total_local_torque_branches"] = len(branches)
    code, output, error = _git(
        repo_root,
        "for-each-ref",
        "--no-merged=main",
        "--format=%(refname:short)",
        "refs/heads/torque/",
    )
    if code != 0:
        section["available"] = False
        section["errors"].append(error or "non_ancestral_branch_list_failed")
        return section
    non_ancestral = sorted(
        line.strip() for line in output.splitlines() if line.strip()
    )
    section["total_non_ancestral"] = len(non_ancestral)
    checked_out = _checked_out_branches(repo_root)
    task_records = _task_branch_records(conn)

    for branch in non_ancestral:
        if branch in checked_out:
            section["excluded_worktree_count"] += 1
            continue

        branch_tree_code, branch_tree, _ = _git(
            repo_root, "rev-parse", f"{branch}^{{tree}}"
        )
        joined = [
            record for record in task_records
            if branch in record["branches"]
            and (
                not record["repo_root"]
                or record["repo_root"] == canonical_repo_root
            )
        ]
        if not joined:
            entry = {"branch": branch, "classification": "unknown"}
        else:
            entry = {
                "branch": branch,
                "classification": "unmerged",
                "task_ids": [record["task_id"] for record in joined],
                "reason": "no_recorded_merge_evidence",
            }
            recorded = [
                (record["task_id"], sha)
                for record in joined
                for sha in record["merge_shas"]
            ]
            if recorded:
                entry["reason"] = "recorded_merge_tree_differs"
                entry["recorded_merge_commits"] = [sha for _, sha in recorded]
                for task_id, merge_sha in recorded:
                    merge_tree_code, merge_tree, _ = _git(
                        repo_root, "rev-parse", f"{merge_sha}^{{tree}}"
                    )
                    if (
                        branch_tree_code == 0
                        and merge_tree_code == 0
                        and branch_tree
                        and branch_tree == merge_tree
                    ):
                        entry = {
                            "branch": branch,
                            "classification": "landed",
                            "task_id": task_id,
                            "merge_commit_sha": merge_sha,
                            "tree_sha": branch_tree,
                        }
                        break
            elif any(record["historical_without_merge"] for record in joined):
                entry["classification"] = "unknown"
                entry["reason"] = "historical_task_missing_merge_evidence"
        section["counts"][entry["classification"]] += 1
        section["branches"].append(entry)
    return section


def collect_nested_branch_cleanup_section(
    conn: sqlite3.Connection,
    project_base_dir: str | Path | None,
) -> dict:
    """Report nested branch populations without applying outer proof rules."""
    section = {
        "available": False,
        "mechanism": "worktree_removed_branch_intentionally_preserved",
        "cleanup_runs": True,
        "production_scope": "report_only_follow_up_required",
        "follow_up_reason": (
            "deletion would change nested recoverability invariants, tests, "
            "and base-branch identity handling"
        ),
        "read_only": True,
        "repositories": [],
        "errors": [],
    }
    configured_repo = _configured_repo(conn, project_base_dir)
    if not configured_repo:
        section["errors"].append("repository_not_configured")
        return section
    code, common_dir, error = _git(
        os.path.expanduser(str(configured_repo)),
        "rev-parse", "--path-format=absolute", "--git-common-dir",
    )
    if code != 0 or not common_dir:
        section["errors"].append(error or "not_a_git_repository")
        return section
    repo_root = str(Path(common_dir).resolve().parent)
    gitmodules = str(Path(repo_root) / ".gitmodules")
    code, output, error = _git(
        repo_root,
        "config", "--file", gitmodules,
        "--get-regexp", r"^submodule\..*\.path$",
    )
    if code not in (0, 1):
        section["errors"].append(error or "submodule_list_failed")
        return section
    section["available"] = True
    for line in output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        path = parts[1].strip()
        nested_root = os.path.realpath(os.path.join(repo_root, path))
        if not nested_root.startswith(os.path.realpath(repo_root) + os.sep):
            section["errors"].append(f"{path}: submodule_path_outside_repo")
            continue
        all_code, all_output, all_error = _git(
            nested_root,
            "for-each-ref", "--format=%(refname:short)", "refs/heads/torque/",
        )
        non_code, non_output, non_error = _git(
            nested_root,
            "for-each-ref", "--no-merged=main",
            "--format=%(refname:short)", "refs/heads/torque/",
        )
        if all_code != 0 or non_code != 0:
            section["errors"].append(
                f"{path}: {all_error or non_error or 'nested_branch_list_failed'}"
            )
            continue
        branches = {
            item.strip() for item in all_output.splitlines() if item.strip()
        }
        non_ancestral = {
            item.strip() for item in non_output.splitlines() if item.strip()
        }
        checked_out = branches & _checked_out_branches(nested_root)
        mirror = {
            branch for branch in branches
            if branch.startswith("torque/submodules/")
        }
        preserved = {
            branch for branch in branches
            if branch.startswith("torque/preserved/")
        }
        section["repositories"].append({
            "path": path,
            "repo_root": nested_root,
            "total": len(branches),
            "ancestral": len(branches - non_ancestral),
            "non_ancestral": len(non_ancestral),
            "checked_out": len(checked_out),
            "checked_out_ancestral": len(checked_out - non_ancestral),
            "checked_out_non_ancestral": len(checked_out & non_ancestral),
            "mirror": len(mirror),
            "mirror_unoccupied": len(mirror - checked_out),
            "preserved": len(preserved),
            "other": len(branches - mirror - preserved),
            "non_ancestral_branches": sorted(non_ancestral),
        })
    return section


def format_squash_branch_cleanup_section(section: dict) -> list[str]:
    counts = section.get("counts", {}) or {}
    lines = [
        "[squash_branch_cleanup]",
        f"  available:                     {str(bool(section.get('available'))).lower()}",
        f"  repo_root:                     {section.get('repo_root', '')}",
        "  total_local_torque_branches:   "
        f"{int(section.get('total_local_torque_branches', 0) or 0)}",
        "  total_non_ancestral:           "
        f"{int(section.get('total_non_ancestral', 0) or 0)}",
        "  excluded_existing_worktree:    "
        f"{int(section.get('excluded_worktree_count', 0) or 0)}",
        f"  landed:                        {int(counts.get('landed', 0) or 0)}",
        f"  unmerged:                      {int(counts.get('unmerged', 0) or 0)}",
        f"  unknown:                       {int(counts.get('unknown', 0) or 0)}",
        "  branches:",
    ]
    entries = list(section.get("branches", []) or [])
    if not entries:
        lines.append("    (none)")
    for entry in entries:
        line = f"    - {entry.get('classification', 'unknown')}: {entry.get('branch', '')}"
        if entry.get("task_id"):
            line += f" task={entry['task_id']} merge={entry.get('merge_commit_sha', '')}"
        elif entry.get("task_ids"):
            line += (
                f" tasks={','.join(entry['task_ids'])}"
                f" reason={entry.get('reason', '')}"
            )
        lines.append(line)
    lines.append("")
    return lines


def format_nested_branch_cleanup_section(section: dict) -> list[str]:
    lines = [
        "[nested_branch_cleanup]",
        f"  available:                     {str(bool(section.get('available'))).lower()}",
        f"  mechanism:                     {section.get('mechanism', '')}",
        f"  cleanup_runs:                  {str(bool(section.get('cleanup_runs'))).lower()}",
        f"  production_scope:              {section.get('production_scope', '')}",
        f"  follow_up_reason:              {section.get('follow_up_reason', '')}",
        "  repositories:",
    ]
    repositories = list(section.get("repositories", []) or [])
    if not repositories:
        lines.append("    (none)")
    for nested in repositories:
        lines.extend([
            f"    - {nested.get('path', '')}: {nested.get('repo_root', '')}",
            "      total/ancestral/non_ancestral: "
            f"{nested.get('total', 0)}/{nested.get('ancestral', 0)}/"
            f"{nested.get('non_ancestral', 0)}",
            "      checked_out: "
            f"{nested.get('checked_out', 0)} "
            f"(ancestral={nested.get('checked_out_ancestral', 0)}, "
            f"non_ancestral={nested.get('checked_out_non_ancestral', 0)})",
            "      mirror/mirror_unoccupied: "
            f"{nested.get('mirror', 0)}/{nested.get('mirror_unoccupied', 0)}",
            f"      preserved/other: {nested.get('preserved', 0)}/{nested.get('other', 0)}",
        ])
    lines.append("")
    return lines
