#!/usr/bin/env python3
"""Safely enumerate stranded, squash-merged branches without deleting them.

This is deliberately a *dry-run-only* instrument.  It never calls a Git
ref-mutating command.  ``git merge-tree --write-tree`` is its sole write-like
operation: Git may create unreachable loose objects while calculating the
merged tree, but no local or remote ref is changed.

The safety order is intentional: live-stream exclusions are collected before
tree identity is tested.  A newly dispatched worker can have an unchanged
branch whose merge tree equals ``main``; that fact must not make it eligible.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


TERMINAL_LANES = frozenset({"Done", "Archived"})
ATTESTED_PROTECTED = (
    "torque/panelsmith/action-catalog-reader-9835e94",
    "torque/courier/fix-cascade-review-gate-1e083db",
    "torque/courier/fix-legacy-worker-ask-resolution-3bcace0",
)
# These streams are intentionally explicit even though the live-worktree gate
# should catch them.  They are the two Courier DOA worktree branches named in
# the cleanup decision, so their protection remains visible in the report.
COURIER_DOA_PROTECTED = (
    "torque/courier/diagnose-forge-specializations-aa66cd7",
    "torque/courier/fix-review-verdict-recording-cc39425",
)
DEFAULT_PROTECTED = ATTESTED_PROTECTED + COURIER_DOA_PROTECTED


@dataclass(frozen=True)
class GitRef:
    ref: str
    branch: str
    location: str  # local or origin


def _run(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True, check=check
    )


def normalize_ref(ref: str) -> str:
    """Map heads/origin refs to the common bare branch name."""
    for prefix in ("refs/heads/", "refs/remotes/origin/", "origin/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def git_refs(repo: Path) -> list[GitRef]:
    output = _run(
        repo, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin"
    ).stdout
    refs: list[GitRef] = []
    for ref in output.splitlines():
        if ref == "refs/remotes/origin/HEAD":
            continue
        if ref.startswith("refs/heads/"):
            refs.append(GitRef(ref, normalize_ref(ref), "local"))
        elif ref.startswith("refs/remotes/origin/"):
            refs.append(GitRef(ref, normalize_ref(ref), "origin"))
    return refs


def ref_snapshot(repo: Path) -> dict[str, str]:
    return {
        ref.ref: _run(repo, "rev-parse", ref.ref).stdout.strip()
        for ref in git_refs(repo)
    }


def live_worktree_branches(repo: Path) -> set[str]:
    """Read named branches from porcelain output; detached worktrees have none."""
    output = _run(repo, "worktree", "list", "--porcelain").stdout
    return {
        normalize_ref(line.split(" ", 1)[1])
        for line in output.splitlines()
        if line.startswith("branch refs/heads/")
    }


def _default_db() -> Path:
    data_dir = os.environ.get("TORQUE_DATA_DIR", "").strip()
    if data_dir:
        return Path(os.path.expanduser(data_dir)) / "torque.db"
    profile = os.environ.get("TORQUE_PROFILE", "default").strip() or "default"
    return Path.home() / ".torque" / "profiles" / profile / "torque.db"


def _json_object(value: object) -> dict:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def task_and_agent_exclusions(db_path: Path) -> dict[str, set[str]]:
    """Collect task-boundary and non-idle-agent exclusions from SQLite read-only.

    A boundary branch is protected even if its task later reached a terminal
    lane: a non-empty boundary is an independent requested safety gate.  For
    non-terminal tasks the reason is recorded separately.  A task's assigned
    agent contributes its current worktree branch through the agent query.
    """
    reasons: dict[str, set[str]] = defaultdict(set)
    if not db_path.exists():
        raise FileNotFoundError(f"Torque database not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        for row in conn.execute("SELECT id, lane, worktree_boundary FROM board_tasks"):
            boundary = _json_object(row["worktree_boundary"])
            if not boundary:
                continue
            branch = normalize_ref(str(boundary.get("branch", "")).strip())
            if not branch:
                continue
            reasons[branch].add(f"task_boundary:{row['id']}")
            if str(row["lane"] or "") not in TERMINAL_LANES:
                reasons[branch].add(f"non_terminal_task:{row['id']}")

        # Status is the live daemon's persisted activity state.  Do not limit
        # this to workers: a non-idle engineer/architect ID in a branch is
        # equally a live stream signal.
        rows = conn.execute(
            "SELECT id, worktree_branch FROM agents "
            "WHERE COALESCE(deleted_at, 0) = 0 AND status != 'idle'"
        )
        for row in rows:
            agent_id = str(row["id"] or "").strip()
            branch = normalize_ref(str(row["worktree_branch"] or "").strip())
            if branch:
                reasons[branch].add(f"non_idle_agent_branch:{agent_id}")
            # The ID substring gate is applied to actual refs by
            # ``apply_non_idle_id_exclusions`` after this read-only query.
            if agent_id:
                reasons[f"__agent_id__:{agent_id}"].add("non_idle_agent_id")
    finally:
        conn.close()
    return reasons


def apply_non_idle_id_exclusions(
    reasons: dict[str, set[str]], refs: Iterable[GitRef]
) -> None:
    agent_ids = [key.removeprefix("__agent_id__:") for key in reasons if key.startswith("__agent_id__:")]
    for key in [key for key in reasons if key.startswith("__agent_id__:")]:
        del reasons[key]
    for ref in refs:
        for agent_id in agent_ids:
            if agent_id and agent_id in ref.branch:
                reasons[ref.branch].add(f"non_idle_agent_id:{agent_id}")


def merge_tree_is_main(repo: Path, ref: GitRef, main_tree: str) -> tuple[bool, str]:
    """Return tree identity, or a non-empty reason when it cannot be proven."""
    result = _run(repo, "merge-tree", "--write-tree", ref.ref, "main", check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().replace("\n", " ")
        return False, f"merge_tree_failed:{detail[:240] or result.returncode}"
    tree = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if not tree:
        return False, "merge_tree_failed:no_tree_output"
    return tree == main_tree, "" if tree == main_tree else f"adds_content:merge_tree={tree}"


def enumerate_cleanup(
    repo: Path, db_path: Path, protected: Iterable[str] = DEFAULT_PROTECTED
) -> dict:
    refs_before = ref_snapshot(repo)
    refs = git_refs(repo)
    main_sha = _run(repo, "rev-parse", "main").stdout.strip()
    main_tree = _run(repo, "rev-parse", "main^{tree}").stdout.strip()

    reasons = defaultdict(set)
    for branch in live_worktree_branches(repo):
        reasons[branch].add("live_worktree")
    for branch, items in task_and_agent_exclusions(db_path).items():
        reasons[branch].update(items)
    apply_non_idle_id_exclusions(reasons, refs)
    protected = tuple(protected)
    for branch in protected:
        reasons[normalize_ref(branch)].add("explicit_protected")
    for ref in refs:
        if ref.branch.startswith("torque/submodules/"):
            reasons[ref.branch].add("submodule_branch_not_in_scope")

    exclusions = [
        {"branch": branch, "reasons": sorted(items)}
        for branch, items in sorted(reasons.items())
        if not branch.startswith("__agent_id__:")
    ]
    eligible: list[dict] = []
    ineligible: list[dict] = []
    for ref in refs:
        if ref.branch == "main":
            continue
        if ref.branch in reasons:
            continue
        identical, reason = merge_tree_is_main(repo, ref, main_tree)
        item = {"ref": ref.ref, "branch": ref.branch, "location": ref.location}
        if identical:
            eligible.append(item)
        else:
            item["reason"] = reason
            ineligible.append(item)

    refs_after = ref_snapshot(repo)
    eligible.sort(key=lambda item: (item["location"], item["branch"]))
    ineligible.sort(key=lambda item: (item["location"], item["branch"]))
    return {
        "dry_run_only": True,
        "measurement_note": "merge-tree --write-tree may create unreachable loose objects; no refs are changed.",
        "main_sha": main_sha,
        "main_tree": main_tree,
        "commands": {
            "live_worktrees": "git worktree list --porcelain",
            "tree_identity": "git merge-tree --write-tree <ref> main; compare output to main^{tree}",
        },
        "excluded_for_live_work": exclusions,
        "eligible": eligible,
        "ineligible": ineligible,
        "counts": {
            "scanned_local_refs": sum(ref.location == "local" for ref in refs),
            "scanned_origin_refs": sum(ref.location == "origin" for ref in refs),
            "excluded_branches": len(exclusions),
            "eligible_local_refs": sum(item["location"] == "local" for item in eligible),
            "eligible_origin_refs": sum(item["location"] == "origin" for item in eligible),
            "ineligible_refs": len(ineligible),
        },
        "protected_refs": list(protected),
        "refs_unchanged": refs_before == refs_after,
        "ref_changes": {
            "before": refs_before,
            "after": refs_after,
        },
    }


def write_report(report: dict, output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    exclusions = output_dir / "branch-cleanup-live-work-exclusions.json"
    eligible = output_dir / "branch-cleanup-eligible.json"
    summary = output_dir / "branch-cleanup-dry-run.json"
    exclusions.write_text(json.dumps(report["excluded_for_live_work"], indent=2) + "\n")
    eligible.write_text(json.dumps(report["eligible"], indent=2) + "\n")
    summary.write_text(json.dumps(report, indent=2) + "\n")
    return exclusions, eligible, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--db", type=Path, default=_default_db())
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = enumerate_cleanup(args.repo.resolve(), args.db.expanduser())
    except (FileNotFoundError, subprocess.CalledProcessError, sqlite3.Error) as exc:
        print(f"branch cleanup dry run failed: {exc}", file=sys.stderr)
        return 2
    exclusions, eligible, summary = write_report(report, args.output_dir)
    print(f"main: {report['main_sha']}")
    print(f"excluded for live work: {len(report['excluded_for_live_work'])} -> {exclusions}")
    print(
        "eligible refs: "
        f"local={report['counts']['eligible_local_refs']} "
        f"origin={report['counts']['eligible_origin_refs']} -> {eligible}"
    )
    print(f"full report: {summary}")
    print(f"refs unchanged: {report['refs_unchanged']}")
    return 0 if report["refs_unchanged"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
