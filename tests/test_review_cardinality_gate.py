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


class LegacyReviewCardinalityTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        state_mod = importlib.import_module("torque.state")
        self.review_mod = importlib.import_module("torque.server_review")
        self.state = state_mod.MatrixState()
        self.state.groups["g"] = []
        self.AgentCell = state_mod.AgentCell

    def _root(self, gates=None, *, requires_review=False, action="feature/implement"):
        return self.state.board_add_task(
            "Implement feature", "g", id="root", lane="In Progress",
            action_name=action, agent_id="implementer",
            requires_review=requires_review,
            required_review_gates=gates or [],
        )

    def _review(self, root, review_id, reviewer_id, *, lane="In Progress"):
        return self.state.board_add_task(
            "Review feature", "g", id=review_id, lane=lane,
            action_name="feature/review", parent_task_id=root.id,
            pipeline_root_id=root.id, pipeline_depth=1, agent_id=reviewer_id,
            completion_evidence={"review": {
                "verdict": "ship", "agent_id": reviewer_id,
            }},
        )

    @staticmethod
    def _two_gates():
        return [{"id": "review-one"}, {"id": "review-two"}]

    def test_one_of_two_declared_reviews_does_not_cascade_1215_shape(self):
        root = self._root(self._two_gates())
        review = self._review(root, "review-1", "reviewer-1", lane="Done")

        self.state.board_cascade_done(review.id)

        self.assertEqual(root.lane, "In Progress")
        status = self.review_mod._legacy_review_cardinality_status(self.state, root)
        self.assertEqual(status["declared_count"], 2)
        self.assertEqual(status["satisfied_count"], 1)
        self.assertEqual(status["shortfall"], 1)

    def test_two_distinct_ships_cascade_normally(self):
        root = self._root(self._two_gates())
        self._review(root, "review-1", "reviewer-1", lane="Done")
        review = self._review(root, "review-2", "reviewer-2")

        self.state.board_move_task(review.id, "Done")

        self.assertEqual(root.lane, "Done")

    def test_two_ships_from_same_reviewer_do_not_satisfy_two(self):
        root = self._root(self._two_gates())
        self._review(root, "review-1", "reviewer-1", lane="Done")
        review = self._review(root, "review-2", "reviewer-1")

        self.state.board_move_task(review.id, "Done")

        self.assertEqual(root.lane, "In Progress")
        status = self.review_mod._legacy_review_cardinality_status(self.state, root)
        self.assertEqual(status["satisfied_count"], 1)
        self.assertEqual(status["shortfall"], 1)

    def test_declared_cardinality_fails_closed_without_a_durable_reviewer_identity(self):
        root = self._root([{"id": "review-one"}])
        review = self.state.board_add_task(
            "Anonymous review", "g", id="anonymous", lane="Done",
            action_name="feature/review", parent_task_id=root.id,
            pipeline_root_id=root.id, pipeline_depth=1,
            completion_evidence={"review": {"verdict": "ship"}},
        )

        self.state.board_cascade_done(review.id)

        self.assertEqual(root.lane, "In Progress")
        self.assertEqual(
            self.review_mod._legacy_review_cardinality_status(self.state, root)["satisfied_count"],
            0,
        )

    def test_implementer_cannot_satisfy_its_own_declared_review(self):
        root = self._root([{"id": "review-one"}])
        review = self._review(root, "self-review", "implementer", lane="Done")

        self.state.board_cascade_done(review.id)

        self.assertEqual(root.lane, "In Progress")
        self.assertEqual(
            self.review_mod._legacy_review_cardinality_status(self.state, root)["satisfied_count"],
            0,
        )

    def test_direct_close_refusal_names_declared_satisfied_and_shortfall(self):
        root = self._root(self._two_gates())
        self._review(root, "review-1", "reviewer-1", lane="Done")
        worker = self.AgentCell(id="implementer", name="Implementer", group="g",
                                cell_type="agent", kind="worker")

        rejection = self.review_mod._reject_mandatory_review_done_without_ship(
            self.state, None, worker, root,
        )

        self.assertEqual(rejection["type"], "error")
        self.assertIn("declared count=2", rejection["message"])
        self.assertIn("satisfied distinct count=1", rejection["message"])
        self.assertIn("shortfall=1", rejection["message"])

    def test_board_root_close_is_refused_by_the_same_cardinality_gate(self):
        root = self._root(self._two_gates())
        self._review(root, "review-1", "reviewer-1", lane="Done")

        result = self.state.board_move_task(root.id, "Done")

        self.assertEqual(root.lane, "In Progress")
        self.assertFalse(result["eligible"])
        self.assertIn("declared count=2", result["explanations"][0])

    def test_declared_legacy_root_created_done_is_relaned_and_reports_shortfall(self):
        root = self.state.board_add_task(
            "Implement feature", "g", id="created-done", lane="Done",
            action_name="feature/implement", agent_id="implementer",
            required_review_gates=self._two_gates(),
        )

        self.assertNotEqual(root.lane, "Done")
        status = self.review_mod._legacy_review_cardinality_status(self.state, root)
        self.assertEqual(status["declared_count"], 2)
        self.assertEqual(status["satisfied_count"], 0)
        self.assertEqual(status["shortfall"], 2)
        self.assertFalse(status["eligible"])

    def test_empty_legacy_root_created_done_retains_existing_behavior(self):
        root = self.state.board_add_task(
            "Legacy direct Done", "g", id="empty-created-done", lane="Done",
            action_name="feature/implement", agent_id="implementer",
            required_review_gates=[],
        )

        self.assertEqual(root.lane, "Done")

    def test_empty_declaration_retains_legacy_any_ship_cascade(self):
        root = self._root([])
        review = self._review(root, "review-1", "reviewer-1")

        self.state.board_move_task(review.id, "Done")

        self.assertEqual(root.lane, "Done")

    def test_1358_shape_still_keys_on_ship_not_implementer_done(self):
        root = self._root([], requires_review=True)
        review = self._review(root, "review-1", "reviewer-1")

        self.state.board_move_task(review.id, "Done")

        self.assertEqual(root.lane, "Done")


if __name__ == "__main__":
    unittest.main()
