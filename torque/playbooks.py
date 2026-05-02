"""Draft playbook candidate extraction from Torque history."""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter, defaultdict


_STOPWORDS = {
    "a", "add", "adds", "adding", "an", "and", "change", "changes",
    "create", "feature", "fix",
    "for", "from", "improve", "implement", "in", "into", "of", "on",
    "or", "support", "task", "tasks", "the", "to", "update", "with",
}


def _normalize_task_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"\"[^\"]*\"|'[^']*'", " ", text)
    text = re.sub(r"\b[a-f0-9]{7,40}\b", " ", text)
    text = re.sub(r"\b(?:[a-z0-9_.-]+/)+[a-z0-9_.-]+\b", " ", text)
    text = re.sub(r"\b[a-z0-9_.-]+\.[a-z0-9]{1,8}\b", " ", text)
    text = re.sub(r"#?\d+\b", " ", text)
    text = re.sub(r"[^a-z]+", " ", text)
    return " ".join(tok for tok in text.split() if len(tok) > 1).strip()


def _topic_key(normalized_task: str) -> str:
    tokens = []
    for token in normalized_task.split():
        if token in _STOPWORDS:
            continue
        if token not in tokens:
            tokens.append(token)
        if len(tokens) == 2:
            break
    if not tokens:
        tokens = normalized_task.split()[:2]
    return "-".join(tokens) or "general-work"


def _stable_labels(labels: list[str]) -> list[str]:
    return sorted(label for label in (labels or [])
                  if label and not label.startswith("torque:"))


def _day_from_timestamp(value: str) -> str:
    if isinstance(value, str) and len(value) >= 10:
        return value[:10]
    return ""


def _task_kind(task) -> str:
    if "torque:human" in (task.labels or []):
        return "ask"
    return "action"


def _task_action(task) -> str:
    if _task_kind(task) == "ask":
        return "ask"
    return task.action_name or "manual"


def _has_message(task, action: str) -> bool:
    return any(msg.get("action") == action for msg in (task.messages or []))


def _stage_record(task, attempts: list[dict], agent_history: dict[str, dict]) -> dict:
    latest_attempt = attempts[-1] if attempts else {}
    agent_meta = agent_history.get(latest_attempt.get("agent_id", ""), {})
    kind = _task_kind(task)
    blocked = ("torque:blocked" in (task.labels or [])) or _has_message(task, "blocked")
    errored = ("torque:error" in (task.labels or [])) or _has_message(task, "error")
    ask_open = kind == "ask" and task.lane != "Done"
    completed = (
        latest_attempt.get("outcome") in {"done", "ready"}
        or task.lane == "Done"
        or _has_message(task, "done")
        or _has_message(task, "ready")
    )
    if errored:
        outcome = "error"
    elif blocked:
        outcome = "blocked"
    elif ask_open:
        outcome = "ask"
    elif completed:
        outcome = "done"
    else:
        outcome = latest_attempt.get("outcome") or task.lane.lower()
    return {
        "task_id": task.id,
        "task": task.task,
        "kind": kind,
        "action": _task_action(task),
        "outcome": outcome,
        "agent_template": agent_meta.get("template", "") or task.agent_template,
        "agent_type": agent_meta.get("agent_type", ""),
        "uses_worktree": bool(agent_meta.get("worktree_branch")),
        "retry_count": max(0, len(attempts) - 1),
        "lane": task.lane,
    }


def _run_record(root, chain: list, task_attempts: dict[str, list[dict]],
                agent_history: dict[str, dict]) -> dict:
    stages = [_stage_record(task, task_attempts.get(task.id, []), agent_history)
              for task in chain]
    workflow_signature = tuple(stage["action"] for stage in stages)
    dispatch_signature = tuple(
        f"{stage['action']}@{stage['agent_template'] or '-'}"
        for stage in stages if stage["kind"] == "action"
    )
    execution_signature = tuple(
        f"{stage['agent_template'] or '-'}#{int(stage['uses_worktree'])}"
        for stage in stages if stage["kind"] == "action"
    )
    action_combination = tuple(sorted({
        stage["action"] for stage in stages if stage["kind"] == "action"
    }))
    total_retries = sum(stage["retry_count"] for stage in stages)
    has_blocked_or_error = any(
        stage["outcome"] in {"blocked", "error"} for stage in stages)
    has_open_ask = any(stage["outcome"] == "ask" for stage in stages)
    terminal = stages[-1] if stages else {}
    terminal_done = bool(stages) and terminal["kind"] != "ask" \
        and terminal["outcome"] == "done"
    success_score = 0.0
    if terminal_done:
        success_score += 0.5
    if not has_blocked_or_error:
        success_score += 0.25
    if not has_open_ask:
        success_score += 0.15
    if total_retries == 0:
        success_score += 0.10
    success_score = round(success_score, 3)
    normalized_task = _normalize_task_text(root.task)
    return {
        "root_task_id": root.id,
        "group": root.group,
        "root_action": root.action_name or "manual",
        "labels": _stable_labels(root.labels),
        "normalized_task": normalized_task,
        "normalized_task_family": _topic_key(normalized_task),
        "support_day": _day_from_timestamp(root.created_at or root.updated_at),
        "workflow_signature": workflow_signature,
        "dispatch_signature": dispatch_signature,
        "execution_signature": execution_signature,
        "action_combination": action_combination,
        "workflow_shape": [
            {"kind": stage["kind"], "action": stage["action"]}
            for stage in stages
        ],
        "dispatch_sequence": [
            {
                "action": stage["action"],
                "agent_template": stage["agent_template"],
                "agent_type": stage["agent_type"],
                "uses_worktree": stage["uses_worktree"],
            }
            for stage in stages if stage["kind"] == "action"
        ],
        "stages": stages,
        "success_score": success_score,
        "successful": terminal_done and not has_blocked_or_error and not has_open_ask,
        "counterexample_reason": (
            "terminal_stage_incomplete" if not terminal_done
            else "blocked_or_error" if has_blocked_or_error
            else "awaiting_human_input" if has_open_ask
            else ""
        ),
    }


def _candidate_name(root_action: str, normalized_task_family: str) -> str:
    base = (root_action or "manual").replace("/", "-")
    suffix = normalized_task_family or "workflow"
    return f"{base}-{suffix}"


def _workflow_transition_plan(workflow: list[dict]) -> list[tuple[str, dict]]:
    plan = []
    for current, nxt in zip(workflow, workflow[1:]):
        if current.get("kind") != "action":
            continue
        if nxt.get("kind") == "ask":
            plan.append((current["action"], {
                "ask": True,
                "when": "Generated from historical playbook draft",
            }))
        elif nxt.get("kind") == "action":
            plan.append((current["action"], {
                "action": nxt["action"],
                "when": "Generated from historical playbook draft",
            }))
    return plan


def _transition_present(existing: list[dict], target: dict) -> bool:
    for item in existing:
        if target.get("ask") and item.get("ask"):
            return True
        if target.get("action") and item.get("action") == target.get("action"):
            return True
    return False


def build_playbook_draft(candidate: dict, action_mgr, template_mgr,
                         base_dir: str = "", now: float | None = None) -> dict:
    """Turn a mined candidate into a reviewable generated draft playbook."""
    now = now or time.time()
    workflow = candidate.get("workflow_shape") or candidate.get("workflow") or []
    entry_action = candidate.get("entry_action") or candidate.get("root_action", "")
    agent_template = candidate.get("agent_template", "")
    entry_action_exists = bool(action_mgr.load_action(entry_action, base_dir)) \
        if entry_action else False
    template_exists = bool(template_mgr.load_template(agent_template, base_dir)) \
        if agent_template else True

    action_updates = []
    for action_name, transition in _workflow_transition_plan(workflow):
        existing = action_mgr.get_transitions(action_name, base_dir)
        action_updates.append({
            "action": action_name,
            "exists": bool(action_mgr.load_action(action_name, base_dir)),
            "transitions_to_add": []
            if _transition_present(existing, transition)
            else [transition],
            "existing_transitions": existing,
        })

    draft_id = f"playbook-{candidate['id']}"
    return {
        "id": draft_id,
        "group": candidate.get("group", ""),
        "source_candidate_id": candidate["id"],
        "status": "draft",
        "generated": True,
        "review_required": True,
        "created_at": now,
        "updated_at": now,
        "published_at": None,
        "discarded_at": None,
        "name": candidate.get("name", draft_id),
        "description": (
            "Generated from Torque history. Review before publication."
        ),
        "match": {
            "root_action": candidate.get("root_action", ""),
            "labels": candidate.get("labels", []),
            "normalized_task_family": candidate.get(
                "normalized_task_family", ""),
        },
        "entry_action": entry_action,
        "agent_template": agent_template,
        "workflow": workflow,
        "constraints": dict(candidate.get("constraints", {})),
        "evidence": {
            **candidate.get("evidence", {}),
            "supporting_runs": candidate.get("supporting_runs", []),
            "counterexamples": candidate.get("counterexamples", []),
        },
        "publication_preview": {
            "generated": True,
            "review_state": "pending",
            "dispatch_recipe": {
                "name": candidate.get("name", draft_id),
                "entry_action": entry_action,
                "agent_template": agent_template,
                "workflow": workflow,
            },
            "entry_action": {
                "name": entry_action,
                "exists": entry_action_exists,
            },
            "template": {
                "name": agent_template,
                "exists": template_exists,
            },
            "pipeline": {
                "ready": all(not update["transitions_to_add"]
                              for update in action_updates),
                "action_updates": action_updates,
            },
            "ready_to_publish": entry_action_exists and template_exists,
        },
    }


def publish_playbook_record(playbook: dict, now: float | None = None) -> dict:
    """Mark a generated draft as explicitly published."""
    now = now or time.time()
    updated = dict(playbook)
    updated["status"] = "published"
    updated["updated_at"] = now
    updated["published_at"] = now
    updated["discarded_at"] = None
    updated.setdefault("publication_preview", {})
    updated["publication_preview"]["review_state"] = "published"
    return updated


def discard_playbook_record(playbook: dict, now: float | None = None) -> dict:
    """Mark a generated draft as explicitly discarded."""
    now = now or time.time()
    updated = dict(playbook)
    updated["status"] = "discarded"
    updated["updated_at"] = now
    updated["discarded_at"] = now
    updated.setdefault("publication_preview", {})
    updated["publication_preview"]["review_state"] = "discarded"
    return updated


def extract_playbook_candidates(state, group: str = "") -> list[dict]:
    """Mine draft playbook candidates from completed historical runs."""
    if not state.db:
        return []

    agent_history = {
        record["id"]: record
        for record in state.db.load_all_agent_history_records()
    }
    task_attempts: dict[str, list[dict]] = defaultdict(list)
    for record in state.db.load_all_agent_tasks():
        if record.get("task_id"):
            task_attempts[record["task_id"]].append(record)

    runs = []
    for task in state.board_tasks.values():
        if task.parent_task_id:
            continue
        if group and task.group != group:
            continue
        chain = state.board_get_chain(task.id)
        if len(chain) < 2:
            continue
        runs.append(_run_record(task, chain, task_attempts, agent_history))

    families: dict[str, list[dict]] = defaultdict(list)
    for run in runs:
        family_key = "|".join([
            run["group"],
            run["root_action"],
            ",".join(run["labels"]),
            run["normalized_task_family"],
        ])
        run["family_key"] = family_key
        families[family_key].append(run)

    candidates = []
    now = time.time()
    for family_key, family_runs in families.items():
        successful = [run for run in family_runs if run["successful"]]
        if len(successful) < 3:
            continue

        workflow_counts = Counter(run["workflow_signature"] for run in successful)
        dominant_workflow, dominant_workflow_count = workflow_counts.most_common(1)[0]
        dominant_workflow_share = dominant_workflow_count / len(successful)
        if dominant_workflow_share < 0.80:
            continue

        dominant_workflow_runs = [
            run for run in successful
            if run["workflow_signature"] == dominant_workflow
        ]
        dispatch_counts = Counter(
            run["dispatch_signature"] for run in dominant_workflow_runs)
        dominant_dispatch, dominant_dispatch_count = dispatch_counts.most_common(1)[0]
        dominant_dispatch_share = dominant_dispatch_count / len(dominant_workflow_runs)
        if dominant_dispatch_share < 0.67:
            continue

        execution_counts = Counter(
            run["execution_signature"] for run in dominant_workflow_runs)
        dominant_execution, dominant_execution_count = execution_counts.most_common(1)[0]
        dominant_execution_share = dominant_execution_count / len(dominant_workflow_runs)
        if dominant_execution_share < 0.67:
            continue

        unique_days = {
            run["support_day"] for run in dominant_workflow_runs if run["support_day"]
        }
        unique_titles = {
            run["normalized_task"] for run in dominant_workflow_runs if run["normalized_task"]
        }
        if len(unique_days) < 2 or len(unique_titles) < 2:
            continue

        alternative_runs = [
            run for run in family_runs
            if run["workflow_signature"] != dominant_workflow
        ]
        alt_success_rate = (
            sum(1 for run in alternative_runs if run["successful"]) / len(alternative_runs)
            if alternative_runs else 0.0
        )
        success_rate = len(successful) / len(family_runs)
        advantage = round(success_rate - alt_success_rate, 3)
        if alternative_runs and advantage < 0.15:
            continue

        exemplar = dominant_workflow_runs[0]
        matched_dispatch_runs = [
            run for run in dominant_workflow_runs
            if run["dispatch_signature"] == dominant_dispatch
        ]
        matched_execution_runs = [
            run for run in dominant_workflow_runs
            if run["execution_signature"] == dominant_execution
        ]
        worktree_share = (
            sum(1 for run in matched_execution_runs
                if any(step.get("uses_worktree")
                       for step in run["dispatch_sequence"]))
            / len(matched_execution_runs)
            if matched_execution_runs else 0.0
        )
        quality_score = round(
            (success_rate * 0.40)
            + (dominant_workflow_share * 0.25)
            + (dominant_dispatch_share * 0.15)
            + (dominant_execution_share * 0.10)
            + (min(1.0, advantage + 0.5) * 0.10),
            3,
        )

        candidate_id = hashlib.sha1(
            f"{family_key}|{dominant_workflow}|{dominant_dispatch}|{dominant_execution}".encode()
        ).hexdigest()[:12]
        candidate = {
            "id": candidate_id,
            "group": exemplar["group"],
            "family_key": family_key,
            "status": "draft",
            "created_at": now,
            "updated_at": now,
            "name": _candidate_name(
                exemplar["root_action"], exemplar["normalized_task_family"]),
            "root_action": exemplar["root_action"],
            "labels": exemplar["labels"],
            "normalized_task_family": exemplar["normalized_task_family"],
            "entry_action": exemplar["root_action"],
            "agent_template": (
                matched_execution_runs[0]["dispatch_sequence"][0]["agent_template"]
                if matched_execution_runs and matched_execution_runs[0]["dispatch_sequence"]
                else ""
            ),
            "workflow": exemplar["workflow_shape"],
            "workflow_shape": exemplar["workflow_shape"],
            "dispatch_sequence": matched_dispatch_runs[0]["dispatch_sequence"],
            "action_combination": list(exemplar["action_combination"]),
            "constraints": {
                "worktree": worktree_share >= 0.50,
                "review_required": any(
                    step["action"] == "ask" for step in exemplar["workflow_shape"]),
            },
            "evidence": {
                "successful_runs": len(successful),
                "total_runs": len(family_runs),
                "support_days": len(unique_days),
                "distinct_task_titles": len(unique_titles),
                "dominant_workflow_share": round(dominant_workflow_share, 3),
                "dominant_dispatch_share": round(dominant_dispatch_share, 3),
                "dominant_execution_share": round(dominant_execution_share, 3),
                "success_rate": round(success_rate, 3),
                "advantage": advantage,
                "quality_score": quality_score,
                "average_success_score": round(
                    sum(run["success_score"] for run in successful) / len(successful), 3),
            },
            "supporting_runs": [
                {
                    "root_task_id": run["root_task_id"],
                    "normalized_task": run["normalized_task"],
                    "support_day": run["support_day"],
                    "success_score": run["success_score"],
                }
                for run in dominant_workflow_runs
            ],
            "counterexamples": [
                {
                    "root_task_id": run["root_task_id"],
                    "normalized_task": run["normalized_task"],
                    "reason": run["counterexample_reason"] or "alternative_workflow",
                    "workflow_signature": list(run["workflow_signature"]),
                }
                for run in family_runs
                if not run["successful"] or run["workflow_signature"] != dominant_workflow
            ][:5],
        }
        candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            item["evidence"].get("quality_score", 0),
            item["evidence"].get("successful_runs", 0),
        ),
        reverse=True,
    )
    return candidates
