import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.services.areas import AreaService
from torque.services.architect_governance import ArchitectGovernanceService
from torque.services.behavior_overlays import BehaviorOverlayService
from torque.services.idea_briefs import IdeaBriefService
from torque.services.journals import JournalService
from torque.services.metrics import MetricsService
from torque.services.initiatives import InitiativeService
from torque.services.thinking import ThinkingService
from torque.state import MatrixState


class BackendServiceCompositionTests(unittest.TestCase):
    def test_matrix_state_composes_initiative_service(self):
        state = MatrixState()

        self.assertIsInstance(state._initiative_service, InitiativeService)
        self.assertIs(state, state._initiative_service._state)

    def test_matrix_state_composes_area_service(self):
        state = MatrixState()

        self.assertIsInstance(state._area_service, AreaService)
        self.assertIs(state, state._area_service._state)

    def test_matrix_state_composes_thinking_service(self):
        state = MatrixState()

        self.assertIsInstance(state._thinking_service, ThinkingService)
        self.assertIs(state, state._thinking_service._state)

    def test_matrix_state_composes_idea_brief_service(self):
        state = MatrixState()

        self.assertIsInstance(state._idea_brief_service, IdeaBriefService)
        self.assertIs(state, state._idea_brief_service._state)

    def test_matrix_state_composes_architect_governance_service(self):
        state = MatrixState()

        self.assertIsInstance(
            state._architect_governance_service,
            ArchitectGovernanceService,
        )
        self.assertIs(state, state._architect_governance_service._state)

    def test_matrix_state_composes_behavior_overlay_service(self):
        state = MatrixState()

        self.assertIsInstance(
            state._behavior_overlay_service,
            BehaviorOverlayService,
        )
        self.assertIs(state, state._behavior_overlay_service._state)

    def test_matrix_state_composes_journal_service(self):
        state = MatrixState()

        self.assertIsInstance(state._journal_service, JournalService)
        self.assertIs(state, state._journal_service._state)

    def test_matrix_state_composes_metrics_service(self):
        state = MatrixState()

        self.assertIsInstance(state._metrics_service, MetricsService)
        self.assertIs(state, state._metrics_service._state)

    def test_matrix_state_keeps_initiative_compatibility_api(self):
        state = MatrixState()

        self.assertEqual([], state.list_initiatives())
        self.assertEqual("", state.resolve_initiative_id("missing"))
        self.assertIsNone(state.load_initiative("missing"))

    def test_matrix_state_domains_are_owned_by_focused_mixins(self):
        expected_modules = {
            "recompute_task_health": "torque.state_board_health",
            "board_add_task": "torque.state_board_mutations",
            "board_get_chain": "torque.state_board_queries",
            "add_group": "torque.state_lifecycle",
            "load": "torque.state_loading",
            "save_direct_message": "torque.state_messages",
            "broadcast": "torque.state_runtime",
            "get_engineer_settings": "torque.state_settings",
            "list_initiatives": "torque.state_services",
        }
        for method_name, module_name in expected_modules.items():
            with self.subTest(method=method_name):
                self.assertEqual(
                    module_name,
                    getattr(MatrixState, method_name).__module__,
                )


if __name__ == "__main__":
    unittest.main()
