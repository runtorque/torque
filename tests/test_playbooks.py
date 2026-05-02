import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.actions import ActionManager
from torque.db import TorqueDB
from torque.playbooks import (
    build_playbook_draft,
    discard_playbook_record,
    publish_playbook_record,
)
from torque.state import BoardTask, MatrixState
from torque.roles import RoleManager


class PlaybookCandidateExtractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(db=self.db)
        self.state.groups["g"] = []

    def _add_task(self, task_id, title, action_name, created_at, *,
                  lane="Done", parent_task_id="", pipeline_root_id="",
                  pipeline_depth=0, messages=None, labels=None):
        task = BoardTask(
            id=task_id,
            task=title,
            group="g",
            action_name=action_name,
            lane=lane,
            labels=labels or ["feature"],
            created_at=created_at,
            updated_at=created_at,
            parent_task_id=parent_task_id,
            pipeline_root_id=pipeline_root_id,
            pipeline_depth=pipeline_depth,
            messages=messages or [{"action": "done", "message": "Complete"}],
        )
        self.state.board_tasks[task.id] = task
        return task

    def _record_dispatch(self, agent_id, task_id, task_title, started_at, *,
                         outcome="done", template="default",
                         worktree_branch="torque/test", agent_type="codex"):
        self.db.save_agent_history({
            "id": agent_id,
            "name": agent_id,
            "group": "g",
            "agent_type": agent_type,
            "template": template,
            "created_at": started_at,
            "worktree_branch": worktree_branch,
            "status": "removed",
        })
        self.db.save_agent_task({
            "agent_id": agent_id,
            "task_id": task_id,
            "task_title": task_title,
            "started_at": started_at,
            "completed_at": started_at + 1,
            "outcome": outcome,
        })

    def _add_successful_run(self, run_no, title, created_at):
        root_id = f"root-{run_no}"
        review_id = f"review-{run_no}"
        root = self._add_task(
            root_id,
            title,
            "feature/implement",
            created_at,
        )
        review = self._add_task(
            review_id,
            f"Review {title}",
            "feature/review",
            created_at,
            parent_task_id=root_id,
            pipeline_root_id=root_id,
            pipeline_depth=1,
        )
        self._record_dispatch(f"impl-{run_no}", root.id, root.task, float(run_no * 10))
        self._record_dispatch(
            f"reviewer-{run_no}", review.id, review.task, float(run_no * 10 + 1))

    def _add_blocked_run(self, run_no, title, created_at):
        root_id = f"root-{run_no}"
        review_id = f"review-{run_no}"
        root = self._add_task(
            root_id,
            title,
            "feature/implement",
            created_at,
        )
        review = self._add_task(
            review_id,
            f"Review {title}",
            "feature/review",
            created_at,
            lane="In Progress",
            parent_task_id=root_id,
            pipeline_root_id=root_id,
            pipeline_depth=1,
            messages=[{"action": "blocked", "message": "Waiting on fix"}],
            labels=["feature", "torque:blocked"],
        )
        self._record_dispatch(f"impl-{run_no}", root.id, root.task, float(run_no * 10))
        self._record_dispatch(
            f"reviewer-{run_no}",
            review.id,
            review.task,
            float(run_no * 10 + 1),
            outcome="blocked",
        )

    def test_extract_playbook_candidates_from_repeated_successful_pipeline_history(self):
        self._add_successful_run(1, "Add billing dashboard export", "2026-04-01T10:00:00+00:00")
        self._add_successful_run(2, "Improve billing dashboard filters", "2026-04-02T10:00:00+00:00")
        self._add_successful_run(3, "Change billing dashboard layout", "2026-04-03T10:00:00+00:00")
        self._add_blocked_run(4, "Fix billing dashboard permissions", "2026-04-04T10:00:00+00:00")

        candidates = self.state.extract_playbook_candidates(group="g")

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["status"], "draft")
        self.assertEqual(
            candidate["workflow_shape"],
            [
                {"kind": "action", "action": "feature/implement"},
                {"kind": "action", "action": "feature/review"},
            ],
        )
        self.assertEqual(candidate["dispatch_sequence"][0]["agent_template"], "default")
        self.assertEqual(
            candidate["action_combination"],
            ["feature/implement", "feature/review"],
        )
        self.assertEqual(candidate["evidence"]["successful_runs"], 3)
        self.assertEqual(candidate["evidence"]["total_runs"], 4)
        self.assertGreaterEqual(candidate["evidence"]["quality_score"], 0.8)
        self.assertEqual(candidate["counterexamples"][0]["root_task_id"], "root-4")

        persisted = self.state.list_playbook_candidates(group="g")
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["id"], candidate["id"])

    def test_extract_playbook_candidates_skips_inconsistent_workflows(self):
        self._add_successful_run(1, "Add billing dashboard export", "2026-04-01T10:00:00+00:00")
        self._add_successful_run(2, "Improve billing dashboard filters", "2026-04-02T10:00:00+00:00")

        root = self._add_task(
            "root-3",
            "Change billing dashboard layout",
            "feature/implement",
            "2026-04-03T10:00:00+00:00",
        )
        qa = self._add_task(
            "qa-3",
            "QA billing dashboard layout",
            "feature/test",
            "2026-04-03T10:00:00+00:00",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
        )
        self._record_dispatch("impl-3", root.id, root.task, 30.0)
        self._record_dispatch("qa-3-agent", qa.id, qa.task, 31.0)

        candidates = self.state.extract_playbook_candidates(group="g")

        self.assertEqual(candidates, [])
        self.assertEqual(self.state.list_playbook_candidates(group="g"), [])


class PlaybookDraftPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / ".torque" / "actions" / "feature").mkdir(parents=True)
        (self.repo / ".torque" / "actions" / "feature" / "implement.yaml").write_text(
            "name: feature/implement\nprompt: |\n  {{ TASK }}\n"
        )
        (self.repo / ".torque" / "actions" / "feature" / "review.yaml").write_text(
            "name: feature/review\nprompt: |\n  {{ TASK }}\n"
        )
        RoleManager().save_template(
            "default",
            {
                "display_name": "Default",
                "provider": "codex",
                "system_prompt": "You are the default agent.\n",
            },
            base_dir=str(self.repo),
        )
        self.db = TorqueDB(self.repo / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(db=self.db)
        self.state.groups["g"] = []
        self.action_mgr = ActionManager()
        self.template_mgr = RoleManager()

    def _candidate(self):
        return {
            "id": "cand-1",
            "group": "g",
            "name": "feature-implement-billing-dashboard",
            "root_action": "feature/implement",
            "entry_action": "feature/implement",
            "agent_template": "default",
            "labels": ["feature"],
            "normalized_task_family": "billing-dashboard",
            "workflow": [
                {"kind": "action", "action": "feature/implement"},
                {"kind": "action", "action": "feature/review"},
            ],
            "workflow_shape": [
                {"kind": "action", "action": "feature/implement"},
                {"kind": "action", "action": "feature/review"},
            ],
            "constraints": {"worktree": True},
            "evidence": {"quality_score": 0.92, "successful_runs": 3},
            "supporting_runs": [{"root_task_id": "root-1"}],
            "counterexamples": [{"root_task_id": "root-4", "reason": "blocked"}],
        }

    def test_build_playbook_draft_marks_generated_reviewable_publication_preview(self):
        draft = build_playbook_draft(
            self._candidate(),
            self.action_mgr,
            self.template_mgr,
            base_dir=str(self.repo),
            now=100.0,
        )

        self.assertEqual(draft["status"], "draft")
        self.assertTrue(draft["generated"])
        self.assertTrue(draft["review_required"])
        self.assertEqual(draft["publication_preview"]["review_state"], "pending")
        self.assertTrue(draft["publication_preview"]["entry_action"]["exists"])
        self.assertTrue(draft["publication_preview"]["template"]["exists"])
        self.assertFalse(draft["publication_preview"]["pipeline"]["ready"])
        self.assertEqual(
            draft["publication_preview"]["pipeline"]["action_updates"][0]["transitions_to_add"],
            [{"action": "feature/review", "when": "Generated from historical playbook draft"}],
        )

    def test_publish_and_discard_playbook_are_explicit_state_transitions(self):
        draft = build_playbook_draft(
            self._candidate(),
            self.action_mgr,
            self.template_mgr,
            base_dir=str(self.repo),
            now=100.0,
        )
        self.state.save_playbook(draft)

        published = publish_playbook_record(draft, now=110.0)
        self.state.save_playbook(published)

        self.assertEqual(
            self.state.list_playbooks(group="g", status="published")[0]["status"],
            "published",
        )

        discarded = discard_playbook_record(
            {**draft, "id": "playbook-cand-2", "name": "other-playbook"},
            now=120.0,
        )
        self.state.save_playbook(discarded)

        self.assertEqual(self.state.list_playbooks(group="g", status="draft"), [])
        self.assertEqual(
            self.state.list_playbooks(group="g", status="discarded")[0]["status"],
            "discarded",
        )
