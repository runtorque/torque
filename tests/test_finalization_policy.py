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
