import importlib
import sys
import types
import unittest


def _install_aiohttp_stub():
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")
    web.WebSocketResponse = type("WebSocketResponse", (), {})
    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


class FinalizationPolicyTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state = self.state_mod.MatrixState()
        self.state.groups["g"] = []

    def _root(self, mode="merge"):
        return self.state.board_add_task(
            "Root", "g", id="TORQUE:900", finalization_mode=mode,
            required_review_gates=[{"id": "architecture", "role": "architecture", "review_task_id": "TORQUE:900:1"}],
            finalization_boundary={
                "repository": "repo", "base_sha": "base", "head_sha": "head",
                "clean_evidence": {"clean": True}, "diff_evidence": {"files": 1},
            } if mode == "merge" else {
                "artifact_digest": "digest", "artifact_version": "v1",
                "source_identity": "artifact:one", "immutable": True,
            },
        )

    def test_legacy_remains_eligible(self):
        root = self.state.board_add_task("Old", "g", id="TORQUE:899")
        result = self.state.evaluate_task_finalization(root.id)
        self.assertTrue(result["eligible"])
        self.state.board_move_task(root.id, "Done")
        self.assertEqual(root.lane, "Done")

    def test_merge_requires_structured_exact_review_and_merge_parity(self):
        root = self._root()
        review = self.state.board_add_task("Review", "g", id="TORQUE:900:1",
                                           parent_task_id=root.id, pipeline_root_id=root.id)
        first = self.state.evaluate_task_finalization(root.id)
        self.assertIn("review_gate_architecture_not_executed", first["missing_gates"])
        # Prose in the old evidence shape is intentionally not acceptance.
        review.completion_evidence = {"review": {"verdict": "ship"}}
        self.assertIn("review_gate_architecture_not_executed", self.state.evaluate_task_finalization(root.id)["missing_gates"])
        self.state.record_finalization_review(
            review.id, gate_id="architecture", verdict="ship", has_blocking_issues=False,
            required_follow_up_resolved=True, boundary="head")
        self.state.board_move_task(review.id, "Done")
        ready = self.state.evaluate_task_finalization(root.id)
        self.assertEqual(ready["stage"], "ready_to_merge")
        self.assertIn("merge_not_finalized", ready["missing_gates"])
        self.state.board_move_task(root.id, "Done")
        self.assertNotEqual(root.lane, "Done")
        self.assertEqual(root.finalization_audit[-1]["outcome"], "blocked")
        self.state.record_merge_finalization(
            root.id, mode="direct", reference="merge:abc", reviewed_head_sha="head",
            merged_sha="merge", origin_verified=True, reviewed_tree="tree", merged_tree="tree", equal=True)
        self.assertTrue(self.state.evaluate_task_finalization(root.id)["eligible"])
        self.state.board_move_task(root.id, "Done")
        self.assertEqual(root.lane, "Done")

    def test_review_only_requires_exact_immutable_evidence_boundary(self):
        root = self._root("review_only")
        review = self.state.board_add_task("Audit", "g", id="TORQUE:900:1",
                                           parent_task_id=root.id, pipeline_root_id=root.id)
        self.state.record_finalization_review(
            review.id, gate_id="architecture", verdict="ship", has_blocking_issues=False,
            required_follow_up_resolved=True, boundary="other")
        self.assertIn("review_gate_architecture_boundary_mismatch", self.state.evaluate_task_finalization(root.id)["missing_gates"])
        self.state.record_finalization_review(
            review.id, gate_id="architecture", verdict="ship", has_blocking_issues=False,
            required_follow_up_resolved=True, boundary="digest|v1|artifact:one")
        self.state.board_move_task(review.id, "Done")
        self.assertTrue(self.state.evaluate_task_finalization(root.id)["eligible"])

    def test_boundary_advance_invalidates_review(self):
        root = self._root()
        review = self.state.board_add_task("Review", "g", id="TORQUE:900:1",
                                           parent_task_id=root.id, pipeline_root_id=root.id)
        self.state.record_finalization_review(review.id, gate_id="architecture", verdict="ship",
            has_blocking_issues=False, required_follow_up_resolved=True, boundary="head")
        root.worktree_boundary = {"commit_sha": "new-head"}
        result = self.state.evaluate_task_finalization(root.id)
        self.assertIn("boundary_advanced", result["missing_gates"])


if __name__ == "__main__":
    unittest.main()

class FinalizationPolicyPersistenceTests(unittest.TestCase):
    def test_roundtrip_defaults_legacy_and_preserves_contract(self):
        _install_aiohttp_stub()
        import tempfile
        from pathlib import Path
        from torque.db import TorqueDB
        from torque.state import BoardTask
        with tempfile.TemporaryDirectory() as tmp:
            db = TorqueDB(Path(tmp) / "torque.db")
            try:
                db.init()
                db.save_board_task(BoardTask(id="legacy", task="Legacy", group="g"))
                db.save_board_task(BoardTask(
                    id="new", task="New", group="g", finalization_mode="review_only",
                    required_review_gates=[{"id": "audit", "role": "audit", "review_task_id": "r"}],
                    finalization_boundary={"artifact_digest": "d", "artifact_version": "1", "source_identity": "x", "immutable": True},
                ))
                tasks = db.load_all()["board_tasks"]
                self.assertEqual(tasks["legacy"]["finalization_mode"], "legacy")
                self.assertEqual(tasks["new"]["required_review_gates"][0]["id"], "audit")
                self.assertEqual(tasks["new"]["finalization_boundary"]["artifact_digest"], "d")
            finally:
                db.close()

class FinalizationDoneBypassRegressionTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        from torque.state import MatrixState
        self.state = MatrixState()
        self.state.groups["g"] = []

    def _ineligible_root(self, task_id="TORQUE:910"):
        return self.state.board_add_task(
            "Audit root", "g", id=task_id, finalization_mode="review_only",
            required_review_gates=[{"id": "audit", "role": "audit", "review_task_id": task_id + ":1"}],
            finalization_boundary={"artifact_digest": "digest", "artifact_version": "v1", "source_identity": "artifact", "immutable": True},
        )

    def test_unarchive_to_done_cannot_bypass_finalization(self):
        root = self._ineligible_root()
        self.state.board_archive_task(root.id)
        result = self.state.board_unarchive_task(root.id, lane="Done")
        self.assertEqual(root.lane, "Archived")
        self.assertFalse(result["eligible"])
        self.assertEqual(root.finalization_audit[-1]["caller"], "board_unarchive_task")

    def test_creation_in_done_cannot_bypass_finalization(self):
        root = self.state.board_add_task(
            "Audit root", "g", id="TORQUE:911", lane="Done", finalization_mode="review_only",
            required_review_gates=[{"id": "audit", "role": "audit", "review_task_id": "TORQUE:911:1"}],
            finalization_boundary={"artifact_digest": "digest", "artifact_version": "v1", "source_identity": "artifact", "immutable": True},
        )
        self.assertNotEqual(root.lane, "Done")
        self.assertFalse(self.state.evaluate_task_finalization(root.id)["eligible"])
        self.assertEqual(root.finalization_audit[-1]["caller"], "board_add_task")

    def test_unknown_pipeline_root_cannot_bypass_policy_creation_in_done(self):
        root = self.state.board_add_task(
            "Malformed policy root", "g", id="TORQUE:911:malformed",
            lane="Done", pipeline_root_id="missing-root",
            finalization_mode="review_only",
            required_review_gates=[{
                "id": "audit", "role": "audit",
                "review_task_id": "TORQUE:911:malformed:1",
            }],
            finalization_boundary={
                "artifact_digest": "digest", "artifact_version": "v1",
                "source_identity": "artifact", "immutable": True,
            },
        )
        self.assertNotEqual(root.lane, "Done")
        self.assertFalse(self.state.evaluate_task_finalization(root.id)["eligible"])
        self.assertEqual(root.finalization_audit[-1]["caller"], "board_add_task")

        legacy = self.state.board_add_task(
            "Malformed legacy root", "g", id="TORQUE:911:legacy",
            lane="Done", pipeline_root_id="missing-root",
        )
        self.assertEqual(legacy.lane, "Done")

    def test_unknown_pipeline_root_update_cannot_bypass_subsequent_done_move(self):
        root = self._ineligible_root("TORQUE:915")
        self.state.board_update_task(root.id, pipeline_root_id="missing-root")
        result = self.state.board_move_task(root.id, "Done")
        self.assertNotEqual(root.lane, "Done")
        self.assertFalse(result["eligible"])
        self.assertEqual(root.finalization_audit[-1]["caller"], "board_move_task")

    def test_atomic_candidate_root_update_cannot_bypass_done_admission(self):
        parent = self.state.board_add_task("Parent", "g", id="TORQUE:916")
        child = self.state.board_add_task(
            "Derived child", "g", id="TORQUE:916:1", parent_task_id=parent.id,
            pipeline_root_id=parent.id,
        )
        result = self.state.board_update_task(
            child.id,
            pipeline_root_id="missing-root",
            lane="Done",
            finalization_mode="review_only",
            required_review_gates=[{
                "id": "audit", "role": "audit",
                "review_task_id": "TORQUE:916:2",
            }],
            finalization_boundary={
                "artifact_digest": "digest", "artifact_version": "v1",
                "source_identity": "artifact", "immutable": True,
            },
        )
        self.assertFalse(result["eligible"])
        self.assertNotEqual(child.lane, "Done")
        self.assertEqual(child.finalization_mode, "legacy")
        self.assertEqual(child.pipeline_root_id, parent.id)
        self.assertEqual(child.finalization_audit[-1]["caller"], "board_update_task")

    def test_policy_to_legacy_update_retracts_stale_status_projection(self):
        root = self._ineligible_root("TORQUE:917")
        self.assertTrue(root.finalization_status)
        self.state.board_update_task(
            root.id,
            finalization_mode="legacy",
            required_review_gates=[],
            finalization_boundary={},
        )
        self.assertEqual(root.finalization_mode, "legacy")
        self.assertEqual(root.finalization_status, {})

    def test_state_update_cannot_combine_done_with_new_policy(self):
        root = self.state.board_add_task("Legacy root", "g", id="TORQUE:913")
        result = self.state.board_update_task(
            root.id,
            lane="Done",
            finalization_mode="review_only",
            required_review_gates=[{
                "id": "security", "role": "security",
                "review_task_id": "TORQUE:913:1",
            }],
            finalization_boundary={
                "artifact_digest": "digest", "artifact_version": "v1",
                "source_identity": "artifact", "immutable": True,
            },
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(root.lane, "Backlog")
        self.assertEqual(root.finalization_mode, "legacy")
        self.assertEqual(root.finalization_audit[-1]["caller"], "board_update_task")
        self.assertEqual(root.finalization_audit[-1]["mode"], "review_only")

    def test_state_update_cannot_admit_policy_on_done_legacy_root(self):
        root = self.state.board_add_task("Legacy root", "g", id="TORQUE:914")
        self.state.board_move_task(root.id, "Done")
        result = self.state.board_update_task(
            root.id,
            finalization_mode="review_only",
            required_review_gates=[{
                "id": "security", "role": "security",
                "review_task_id": "TORQUE:914:1",
            }],
            finalization_boundary={
                "artifact_digest": "digest", "artifact_version": "v1",
                "source_identity": "artifact", "immutable": True,
            },
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(root.lane, "Done")
        self.assertEqual(root.finalization_mode, "legacy")

    def test_typed_review_record_refreshes_root_projection(self):
        root = self._ineligible_root("TORQUE:912")
        review = self.state.board_add_task(
            "Audit review", "g", id="TORQUE:912:1", parent_task_id=root.id,
            pipeline_root_id=root.id, action_name="feature/review", agent_id="reviewer",
            dispatch_state="live",
        )
        self.assertEqual(root.finalization_status["label"], "Fixing blockers")
        result = self.state.record_finalization_review(
            review.id, gate_id="audit", verdict="ship", has_blocking_issues=False,
            required_follow_up_resolved=True, boundary="digest|v1|artifact",
        )
        self.assertIn("relevant_descendant_open:TORQUE:912:1", result["missing_gates"])
        self.state.board_move_task(review.id, "Done")
        self.assertEqual(root.finalization_status["label"], "Ready to finalize")

class FinalizationReviewReportFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_verify_report_records_typed_review_evidence_without_prose(self):
        _install_aiohttp_stub()
        from dataclasses import fields
        from torque.commands.ai_reports import AIReportCommandRuntime, handle_ai_report_command
        from torque.state import AgentCell, MatrixState

        state = MatrixState()
        state.groups["g"] = []
        reviewer = AgentCell(id="reviewer", name="Reviewer", group="g", cell_type="agent")
        state.agents[reviewer.id] = reviewer
        state.groups["g"].append(reviewer.id)
        root = state.board_add_task(
            "Root", "g", id="TORQUE:920", finalization_mode="review_only",
            required_review_gates=[{"id": "security", "role": "security", "review_task_id": "TORQUE:920:1"}],
            finalization_boundary={"artifact_digest": "d", "artifact_version": "v", "source_identity": "evidence", "immutable": True},
        )
        review = state.board_add_task(
            "Review", "g", id="TORQUE:920:1", parent_task_id=root.id,
            pipeline_root_id=root.id, action_name="feature/review", agent_id=reviewer.id,
            dispatch_state="live",
        )
        reviewer.current_task_id = review.id

        async def noop_async(*_args, **_kwargs):
            return None
        values = {field.name: None for field in fields(AIReportCommandRuntime)}
        values.update({
            "state": state,
            "capture_auto_resume_targets": lambda *_args, **_kwargs: [],
            "resolve_ai_report_task": lambda *_args, **_kwargs: review,
            "promote_task_for_active_report": lambda *_args, **_kwargs: None,
            "apply_verification_report": lambda task, _payload, _actor, _save, root_task=None: ("typed review evidence", root_task),
            "append_mcp_message": lambda *_args, **_kwargs: None,
            "panel_event": lambda *_args, **_kwargs: None,
            "maybe_auto_resume_targets": noop_async,
        })
        result = await handle_ai_report_command({
            "cell_id": reviewer.id, "action": "verify",
            "finalization_review": {
                "gate_id": "security", "verdict": "ship",
                "has_blocking_issues": False,
                "required_follow_up_resolved": True,
                "boundary": "d|v|evidence",
            },
        }, AIReportCommandRuntime(**values))
        self.assertEqual(result["type"], "finalization_review_recorded")
        self.assertEqual(
            review.completion_evidence["finalization_review"]["verdict"], "ship")
        # The legacy free-form review evidence is not used as finalization evidence.
        self.assertNotIn("review", review.completion_evidence)

class FinalizationLaneRemovalRegressionTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        from torque.state import MatrixState
        self.state = MatrixState()
        self.state.groups["g"] = []
        self.state.board_add_lane("Reviewing")

    def _policy_root(self, task_id, lane="Reviewing"):
        return self.state.board_add_task(
            "Policy root", "g", id=task_id, lane=lane,
            finalization_mode="review_only",
            required_review_gates=[{"id": "audit", "role": "audit", "review_task_id": task_id + ":1"}],
            finalization_boundary={"artifact_digest": "d", "artifact_version": "v", "source_identity": "e", "immutable": True},
        )

    def test_remove_lane_to_done_refuses_ineligible_policy_root_atomically(self):
        root = self._policy_root("TORQUE:930")
        result = self.state.board_remove_lane("Reviewing", move_tasks_to="Done")
        self.assertEqual(root.lane, "Reviewing")
        self.assertIn("Reviewing", self.state.board_lanes)
        self.assertEqual(result["type"], "finalization_blocked")
        self.assertEqual(root.finalization_audit[-1]["caller"], "board_remove_lane")

    def test_remove_lane_to_done_keeps_legacy_and_derived_review_compatibility(self):
        legacy = self.state.board_add_task("Legacy", "g", id="TORQUE:931", lane="Reviewing")
        root = self._policy_root("TORQUE:932", lane="Backlog")
        review = self.state.board_add_task(
            "Derived review", "g", id="TORQUE:932:1", lane="Reviewing",
            parent_task_id=root.id, pipeline_root_id=root.id,
        )
        result = self.state.board_remove_lane("Reviewing", move_tasks_to="Done")
        self.assertEqual(result["type"], "lane_removed")
        self.assertEqual(legacy.lane, "Done")
        self.assertEqual(review.lane, "Done")
        self.assertNotEqual(root.lane, "Done")

class FinalizationProductionAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_board_operation_admits_explicit_policy_and_gates_done(self):
        _install_aiohttp_stub()
        from dataclasses import fields
        from torque.commands.board_operations import BoardOperationRuntime, handle_board_operation_command
        from torque.state import MatrixState

        state = MatrixState()
        state.groups["g"] = []
        async def base_dir(*_args, **_kwargs):
            return ""
        values = {field.name: None for field in fields(BoardOperationRuntime)}
        values.update({
            "state": state,
            "resolve_base_dir": base_dir,
            "resolve_deliverable_for_create": lambda *_args, **_kwargs: {"required": False, "type": "", "format": "", "artifact_title": ""},
            "normalize_external_link": lambda *_args: {"provider": "", "external_id": "", "external_url": ""},
            "is_canonical_task_id": lambda _value: True,
            "is_draft_task_token": lambda _value: False,
            "resolve_task_id": lambda _state, value: value,
            "engineer_tombstoned_error": lambda _value: {"type": "error"},
            "finalize_task_attachments": lambda attachments, artifacts, **_kwargs: (attachments, artifacts),
        })
        runtime = BoardOperationRuntime(**values)
        root_result = await handle_board_operation_command({
            "cmd": "board_add_task", "id": "TORQUE:940", "task": "Policy root", "group": "g",
            "finalization_mode": "review_only",
            "required_review_gates": [{"id": "audit", "role": "audit", "review_task_id": "TORQUE:940:1"}],
            "finalization_boundary": {"artifact_digest": "d", "artifact_version": "v", "source_identity": "e", "immutable": True},
        }, runtime)
        self.assertEqual(root_result["type"], "board_task_added")
        root = state.board_tasks["TORQUE:940"]
        self.assertEqual(root.finalization_mode, "review_only")
        self.assertFalse(state.evaluate_task_finalization(root.id)["eligible"])
        state.board_move_task(root.id, "Done")
        self.assertNotEqual(root.lane, "Done")
        review_result = await handle_board_operation_command({
            "cmd": "board_add_task", "id": "TORQUE:940:1", "task": "Review", "group": "g",
            "parent_task_id": root.id, "pipeline_root_id": root.id,
        }, runtime)
        self.assertEqual(review_result["type"], "board_task_added")
        review = state.board_tasks["TORQUE:940:1"]
        state.record_finalization_review(review.id, gate_id="audit", verdict="ship",
            has_blocking_issues=False, required_follow_up_resolved=True, boundary="d|v|e")
        state.board_move_task(review.id, "Done")
        self.assertEqual(root.lane, "Done")

    async def test_board_operation_rejects_policy_updates_that_would_leave_done_ineligible(self):
        _install_aiohttp_stub()
        from dataclasses import fields
        from torque.commands.board_operations import BoardOperationRuntime, handle_board_operation_command
        from torque.state import MatrixState

        state = MatrixState()
        state.groups["g"] = []

        async def base_dir(*_args, **_kwargs):
            return ""

        values = {field.name: None for field in fields(BoardOperationRuntime)}
        values.update({
            "state": state,
            "resolve_base_dir": base_dir,
            "resolve_deliverable_for_create": lambda *_args, **_kwargs: {"required": False, "type": "", "format": "", "artifact_title": ""},
            "normalize_external_link": lambda *_args: {"provider": "", "external_id": "", "external_url": ""},
            "is_canonical_task_id": lambda _value: True,
            "is_draft_task_token": lambda _value: False,
            "resolve_task_id": lambda _state, value: value,
            "engineer_tombstoned_error": lambda _value: {"type": "error"},
            "finalize_task_attachments": lambda attachments, artifacts, **_kwargs: (attachments, artifacts),
            "capture_auto_resume_targets": lambda *_args, **_kwargs: [],
        })
        runtime = BoardOperationRuntime(**values)
        policy = {
            "finalization_mode": "review_only",
            "required_review_gates": [{
                "id": "security", "role": "security",
                "review_task_id": "TORQUE:941:1",
            }],
            "finalization_boundary": {
                "artifact_digest": "digest", "artifact_version": "v1",
                "source_identity": "artifact", "immutable": True,
            },
        }

        combined = state.board_add_task("Legacy", "g", id="TORQUE:941")
        combined_result = await handle_board_operation_command({
            "cmd": "board_update_task", "id": combined.id, "lane": "Done", **policy,
        }, runtime)
        self.assertEqual(combined_result["type"], "finalization_blocked")
        self.assertEqual(combined.lane, "Backlog")
        self.assertEqual(combined.finalization_mode, "legacy")

        already_done = state.board_add_task("Legacy done", "g", id="TORQUE:942")
        state.board_move_task(already_done.id, "Done")
        already_done_result = await handle_board_operation_command({
            "cmd": "board_update_task", "id": already_done.id,
            **{
                **policy,
                "required_review_gates": [{
                    "id": "security", "role": "security",
                    "review_task_id": "TORQUE:942:1",
                }],
            },
        }, runtime)
        self.assertEqual(already_done_result["type"], "finalization_blocked")
        self.assertEqual(already_done.lane, "Done")
        self.assertEqual(already_done.finalization_mode, "legacy")

        parent = state.board_add_task("Parent", "g", id="TORQUE:943")
        child = state.board_add_task(
            "Child", "g", id="TORQUE:943:1", parent_task_id=parent.id,
            pipeline_root_id=parent.id,
        )
        candidate_result = await handle_board_operation_command({
            "cmd": "board_update_task", "id": child.id,
            "pipeline_root_id": "missing-root", "lane": "Done",
            "finalization_mode": "review_only",
            "required_review_gates": [{
                "id": "security", "role": "security",
                "review_task_id": "TORQUE:943:2",
            }],
            "finalization_boundary": {
                "artifact_digest": "digest", "artifact_version": "v1",
                "source_identity": "artifact", "immutable": True,
            },
        }, runtime)
        self.assertEqual(candidate_result["type"], "finalization_blocked")
        self.assertNotEqual(child.lane, "Done")
        self.assertEqual(child.finalization_mode, "legacy")
