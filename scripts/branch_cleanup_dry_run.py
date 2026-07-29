#!/usr/bin/env python3
"""Enumerate stranded branches by default; explicitly gated apply can delete refs.

Without ``--apply`` this is a dry-run-only instrument and never calls a
Git ref-mutating command.  ``--apply`` is destructive: it requires an explicit
baseline acknowledgement, recomputes eligibility in-process, and then deletes
only the freshly gated local refs before remote refs.  ``git merge-tree
--write-tree`` may create unreachable loose objects while calculating merged
trees in either mode.

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
import re
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
_VALID_AGENT_BRANCH = re.compile(r"^torque/(?!submodules/)[^/]+/[^/]+$")


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


def is_valid_agent_branch(branch: str) -> bool:
    """Allow only worker streams and the user stream; never submodules."""
    return bool(_VALID_AGENT_BRANCH.fullmatch(branch))


def task_and_agent_exclusions(db_path: Path) -> dict[str, set[str]]:
    """Collect task-boundary and non-idle-agent exclusions from SQLite read-only.

    Boundaries belong to the live gate only while their task is non-terminal.
    A non-terminal task can have an empty boundary, however, so its assigned
    agent's worktree branch is joined in even when that agent is idle.
    """
    reasons: dict[str, set[str]] = defaultdict(set)
    if not db_path.exists():
        raise FileNotFoundError(f"Torque database not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        task_rows = conn.execute(
            "SELECT t.id, t.worktree_boundary, a.worktree_branch "
            "FROM board_tasks AS t "
            "LEFT JOIN agents AS a ON a.id = t.agent_id "
            "WHERE t.lane NOT IN ('Done', 'Archived')"
        )
        for row in task_rows:
            boundary = _json_object(row["worktree_boundary"])
            branch = normalize_ref(str(boundary.get("branch", "")).strip())
            if branch:
                reasons[branch].add(f"non_terminal_task_boundary:{row['id']}")
            assigned_branch = normalize_ref(str(row["worktree_branch"] or "").strip())
            if assigned_branch:
                reasons[assigned_branch].add(f"non_terminal_task_agent:{row['id']}")

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


def merge_tree_is_main(repo: Path, ref: GitRef, main_tree: str) -> tuple[bool, str, str]:
    """Return tree identity, or a non-empty reason when it cannot be proven."""
    result = _run(repo, "merge-tree", "--write-tree", ref.ref, "main", check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().replace("\n", " ")
        return False, f"merge_tree_failed:{detail[:240] or result.returncode}", ""
    tree = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if not tree:
        return False, "merge_tree_failed:no_tree_output", ""
    return (
        tree == main_tree,
        "" if tree == main_tree else f"adds_content:merge_tree={tree}",
        tree,
    )


def enumerate_cleanup(
    repo: Path, db_path: Path, protected: Iterable[str] = DEFAULT_PROTECTED
) -> dict:
    refs_before = ref_snapshot(repo)
    refs = git_refs(repo)
    main_sha = _run(repo, "rev-parse", "main").stdout.strip()
    main_tree = _run(repo, "rev-parse", "main^{tree}").stdout.strip()

    live_reasons = defaultdict(set)
    for branch in live_worktree_branches(repo):
        live_reasons[branch].add("live_worktree")
    for branch, items in task_and_agent_exclusions(db_path).items():
        live_reasons[branch].update(items)
    apply_non_idle_id_exclusions(live_reasons, refs)
    protected_reasons = defaultdict(set)
    protected = tuple(protected)
    for branch in protected:
        protected_reasons[normalize_ref(branch)].add("explicit_protected")
    outside_namespace = [
        {"ref": ref.ref, "branch": ref.branch, "location": ref.location}
        for ref in refs
        if ref.branch != "main" and not is_valid_agent_branch(ref.branch)
    ]
    outside_names = {item["branch"] for item in outside_namespace}
    for ref in refs:
        if ref.branch.startswith("torque/submodules/"):
            outside_names.add(ref.branch)

    live_exclusions = [
        {"branch": branch, "reasons": sorted(items)}
        for branch, items in sorted(live_reasons.items())
        if not branch.startswith("__agent_id__:")
    ]
    protected_status = [
        {
            "branch": branch,
            "present": any(ref.branch == branch for ref in refs),
            "live_reasons": sorted(live_reasons.get(branch, set())),
        }
        for branch in protected
    ]
    eligible: list[dict] = []
    ineligible: list[dict] = []
    for ref in refs:
        if ref.branch == "main":
            continue
        if (
            ref.branch in live_reasons
            or ref.branch in protected_reasons
            or ref.branch in outside_names
        ):
            continue
        identical, reason, merged_tree = merge_tree_is_main(repo, ref, main_tree)
        item = {
            "ref": ref.ref,
            "branch": ref.branch,
            "location": ref.location,
            "tree_evidence": {
                "merge_tree": merged_tree,
                "main_tree": main_tree,
                "identical": identical,
            },
            "gate_evidence": {
                "not_live_excluded": ref.branch not in live_reasons,
                "not_explicitly_protected": ref.branch not in protected_reasons,
                "valid_agent_namespace": ref.branch not in outside_names,
            },
        }
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
        "excluded_for_live_work": live_exclusions,
        "outside_namespace": outside_namespace,
        "protected_status": protected_status,
        "eligible": eligible,
        "ineligible": ineligible,
        "counts": {
            "scanned_local_refs": sum(ref.location == "local" for ref in refs),
            "scanned_origin_refs": sum(ref.location == "origin" for ref in refs),
            "live_excluded_branches": len(live_exclusions),
            "outside_namespace_refs": len(outside_namespace),
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


def write_report(report: dict, output_dir: Path) -> tuple[Path, Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    exclusions = output_dir / "branch-cleanup-live-work-exclusions.json"
    eligible = output_dir / "branch-cleanup-eligible.json"
    outside_namespace = output_dir / "branch-cleanup-outside-namespace.json"
    summary = output_dir / "branch-cleanup-dry-run.json"
    exclusions.write_text(json.dumps(report["excluded_for_live_work"], indent=2) + "\n")
    eligible.write_text(json.dumps(report["eligible"], indent=2) + "\n")
    outside_namespace.write_text(json.dumps(report["outside_namespace"], indent=2) + "\n")
    summary.write_text(json.dumps(report, indent=2) + "\n")
    return exclusions, eligible, outside_namespace, summary


def _apply_command(repo: Path, *args: str) -> dict:
    """Run one mutating command and preserve its attribution evidence."""
    result = _run(repo, *args, check=False)
    return {
        "command": ["git", "-C", str(repo), *args],
        "returncode": result.returncode,
        # This is destructive-operation evidence, not console presentation:
        # preserve both streams losslessly for recovery/audit.
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _phase_result(items: list[dict]) -> dict:
    return {"succeeded": [], "failed": [], "never_attempted": items}


def apply_cleanup(
    repo: Path,
    db_path: Path,
    *,
    expected_eligible_count: int,
    max_count_drift: int,
    measure=enumerate_cleanup,
) -> dict:
    """Apply the recomputed set only after an explicit conservative drift gate.

    No caller-provided artifact or prior report participates in authorization.
    ``measure`` is deliberately invoked in this function immediately before the
    first mutation, then once again after the attempt.  On any local or remote
    failure this stops the rest of that phase and leaves all later refs in
    ``never_attempted`` rather than hiding an ambiguous partial cleanup.
    """
    # This recomputation is the sole authority for mutation.
    pre = measure(repo, db_path)
    actual = len(pre["eligible"])
    drift = abs(actual - expected_eligible_count)
    result = {
        "apply_requested": True,
        "expected_eligible_count": expected_eligible_count,
        "max_count_drift": max_count_drift,
        "pre_mutation": pre,
        "drift": {"actual": actual, "difference": drift, "allowed": drift <= max_count_drift},
        "local": _phase_result([]),
        "remote": _phase_result([]),
        "mutation_started": False,
    }
    if drift > max_count_drift:
        result["refusal"] = "eligible_count_drift_exceeds_threshold"
        result["post_measurement"] = measure(repo, db_path)
        return result

    local = [item for item in pre["eligible"] if item["location"] == "local"]
    remote = [item for item in pre["eligible"] if item["location"] == "origin"]
    result["local"] = _phase_result(local.copy())
    result["remote"] = _phase_result(remote.copy())
    result["mutation_started"] = bool(local or remote)

    # Local phase: -D is allowed only because every item came from this exact
    # pre-mutation recomputation and carries both gate and tree evidence.
    for index, item in enumerate(local):
        evidence = {"ref": item["ref"], "branch": item["branch"], "gate_evidence": item["gate_evidence"], "tree_evidence": item["tree_evidence"]}
        command = _apply_command(repo, "branch", "-D", item["branch"])
        evidence["command"] = command
        result["local"]["never_attempted"] = local[index + 1:]
        if command["returncode"]:
            result["local"]["failed"].append(evidence)
            result["remote"]["never_attempted"] = remote
            result["stop_reason"] = "local_delete_failed"
            result["post_measurement"] = measure(repo, db_path)
            return result
        result["local"]["succeeded"].append(evidence)
    result["local"]["never_attempted"] = []

    # Remote phase comes strictly after every local candidate succeeded.  One
    # push per ref keeps a failed remote attributable; first failure stops.
    for index, item in enumerate(remote):
        evidence = {"ref": item["ref"], "branch": item["branch"], "gate_evidence": item["gate_evidence"], "tree_evidence": item["tree_evidence"]}
        command = _apply_command(repo, "push", "origin", "--delete", item["branch"])
        evidence["command"] = command
        result["remote"]["never_attempted"] = remote[index + 1:]
        if command["returncode"]:
            result["remote"]["failed"].append(evidence)
            result["stop_reason"] = "remote_delete_failed"
            result["post_measurement"] = measure(repo, db_path)
            return result
        result["remote"]["succeeded"].append(evidence)
    result["remote"]["never_attempted"] = []
    result["post_measurement"] = measure(repo, db_path)
    return result


def apply_left_behind(report: dict) -> dict:
    """Make the deliberately retained categories auditable after an apply."""
    return {
        "live_work": report.get("excluded_for_live_work", []),
        "outside_namespace": report.get("outside_namespace", []),
        "protected": report.get("protected_status", []),
        "ineligible": report.get("ineligible", []),
    }


def _phase_counts(phase: dict) -> str:
    return " ".join(
        f"{name}={len(phase.get(name, []))}"
        for name in ("succeeded", "failed", "never_attempted")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--db", type=Path, default=_default_db())
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the separately authorized local-then-remote cleanup (default is dry run)",
    )
    parser.add_argument(
        "--expected-eligible-count",
        type=int,
        help="explicit baseline acknowledgement required with --apply",
    )
    parser.add_argument(
        "--acknowledge-expected-baseline",
        type=int,
        help="must exactly repeat --expected-eligible-count when --apply is used",
    )
    parser.add_argument(
        "--max-count-drift",
        type=int,
        default=27,
        help="absolute eligible-ref drift allowed before apply (default: 27, 15%% of baseline 176)",
    )
    args = parser.parse_args(argv)
    if args.apply and (
        args.expected_eligible_count is None
        or args.acknowledge_expected_baseline != args.expected_eligible_count
    ):
        parser.error(
            "--apply requires --expected-eligible-count N and "
            "--acknowledge-expected-baseline N"
        )
    if args.max_count_drift < 0:
        parser.error("--max-count-drift must be non-negative")
    try:
        repo = args.repo.resolve()
        db = args.db.expanduser()
        if args.apply:
            apply_result = apply_cleanup(
                repo,
                db,
                expected_eligible_count=args.expected_eligible_count,
                max_count_drift=args.max_count_drift,
            )
            report = apply_result["pre_mutation"]
            apply_result["post_measurement_totals"] = apply_result["post_measurement"].get("counts", {})
            apply_result["left_behind"] = apply_left_behind(
                apply_result["post_measurement"]
            )
        else:
            apply_result = None
            report = enumerate_cleanup(repo, db)
    except (FileNotFoundError, subprocess.CalledProcessError, sqlite3.Error) as exc:
        print(f"branch cleanup dry run failed: {exc}", file=sys.stderr)
        return 2
    exclusions, eligible, outside_namespace, summary = write_report(report, args.output_dir)
    if apply_result is not None:
        apply_report = args.output_dir / "branch-cleanup-apply-report.json"
        apply_report.write_text(json.dumps(apply_result, indent=2) + "\n")
        print(f"apply report: {apply_report}")
    print(f"main: {report['main_sha']}")
    print(f"excluded for live work: {len(report['excluded_for_live_work'])} -> {exclusions}")
    print(
        "eligible refs: "
        f"local={report['counts']['eligible_local_refs']} "
        f"origin={report['counts']['eligible_origin_refs']} -> {eligible}"
    )
    print(f"outside valid agent namespace: {len(report['outside_namespace'])} -> {outside_namespace}")
    print(f"full report: {summary}")
    if apply_result is None:
        print(f"refs unchanged: {report['refs_unchanged']}")
    else:
        post_counts = apply_result["post_measurement_totals"]
        print(f"apply mutation started: {apply_result['mutation_started']}")
        print(f"apply local: {_phase_counts(apply_result['local'])}")
        print(f"apply remote: {_phase_counts(apply_result['remote'])}")
        print(
            "apply post eligible refs: "
            f"local={post_counts.get('eligible_local_refs', 0)} "
            f"origin={post_counts.get('eligible_origin_refs', 0)}"
        )
    if not report["refs_unchanged"]:
        return 3
    if apply_result and apply_result.get("refusal"):
        return 4
    if apply_result and (
        apply_result["local"]["failed"] or apply_result["remote"]["failed"]
    ):
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
