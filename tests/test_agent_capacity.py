"""Capacity applies to retained agents, excluding recoverable deletions."""
import unittest
from torque.state import MatrixState
from torque.server_review import _fresh_reviewer_seat_available


class AgentCapacityTests(unittest.TestCase):
    def setUp(self):
        self.state = MatrixState()
        self.state.add_group("Capacity")
        self.state.update_group_settings("Capacity", max_agents=1)

    def test_soft_delete_frees_creation_and_reviewer_capacity(self):
        state = self.state
        original = state.add_agent(name="Original", group="Capacity")
        self.assertFalse(_fresh_reviewer_seat_available(state, "Capacity"))
        self.assertIsNone(state.add_agent(name="Blocked", group="Capacity"))
        state.remove_agent(original.id)
        self.assertIn(original.id, state.groups["Capacity"])
        self.assertGreater(original.deleted_at, 0)
        self.assertTrue(_fresh_reviewer_seat_available(state, "Capacity"))
        self.assertIsNotNone(state.add_agent(name="Replacement", group="Capacity"))
        self.assertFalse(_fresh_reviewer_seat_available(state, "Capacity"))

    def test_restore_can_exceed_limit_but_prevents_further_creation(self):
        state = self.state
        original = state.add_agent(name="Original", group="Capacity")
        state.remove_agent(original.id)
        self.assertIsNotNone(state.add_agent(name="Replacement", group="Capacity"))
        self.assertTrue(state.restore_agent(original.id))
        self.assertIsNone(state.add_agent(name="Blocked", group="Capacity"))
        self.assertFalse(_fresh_reviewer_seat_available(state, "Capacity"))

    def test_terminals_other_groups_and_unlimited(self):
        state = self.state
        state.add_group("Other")
        state.add_agent(name="Other", group="Other")
        self.assertIsNotNone(state.add_terminal(name="Standalone", group="Capacity"))
        agent = state.add_agent(name="Retained", group="Capacity")
        self.assertIsNotNone(agent)
        self.assertIsNotNone(state.add_terminal(name="Child", group="Capacity", parent_id=agent.id))
        for status in ("stopped", "dismissed"):
            agent.status = status
            self.assertFalse(_fresh_reviewer_seat_available(state, "Capacity"))
            self.assertIsNone(state.add_agent(name="Blocked", group="Capacity"))
        state.update_group_settings("Capacity", max_agents=0)
        self.assertTrue(_fresh_reviewer_seat_available(state, "Capacity"))
        self.assertIsNotNone(state.add_agent(name="Unlimited", group="Capacity"))
