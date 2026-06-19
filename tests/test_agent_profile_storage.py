import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub

install_aiohttp_stub()

from torque.db import TorqueDB
from torque.state import AgentCell, MatrixState


class AgentProfileStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = TorqueDB(self.root / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = MatrixState(db=self.db)
        self.state.groups["g"] = []
        self.project = self.root / "repo"
        self.project.mkdir()

    def _add_architect(self):
        cell = AgentCell(
            id="arch-1",
            name="Architect",
            group="g",
            cell_type="agent",
            kind="architect",
        )
        self.state.agents[cell.id] = cell
        self.state.groups["g"].append(cell.id)
        self.state._db_save_agent(cell)
        return cell

    def test_trusted_user_assignment_persists_without_changing_effective_snapshot(self):
        cell = self._add_architect()

        status = self.state.assign_agent_profile(
            cell.id,
            "product-manager-draft",
            actor_kind="user",
            actor_label="test-user",
            base_dir=str(self.project),
        )

        self.assertEqual(status["assigned_profile_id"], "product-manager-draft")
        self.assertEqual(status["effective_profile_id"], "full-architect")
        self.assertTrue(status["pending_next_launch"])
        self.assertEqual(cell.effective_agent_profile_id, "")
        loaded = self.db.load_all()["agents"][cell.id]
        self.assertEqual(loaded["agent_profile_id"], "product-manager-draft")
        self.assertEqual(loaded["effective_agent_profile_id"], "")
        audit = self.db.list_agent_profile_audit(agent_id=cell.id)
        self.assertEqual(audit[0]["event"], "assignment_set")

    def test_non_user_assignment_is_rejected(self):
        cell = self._add_architect()

        with self.assertRaises(PermissionError):
            self.state.assign_agent_profile(
                cell.id,
                "product-manager-draft",
                actor_kind="architect",
                base_dir=str(self.project),
            )

    def test_launch_freezes_assigned_snapshot_and_survives_later_assignment_change(self):
        cell = self._add_architect()
        self.state.assign_agent_profile(
            cell.id,
            "product-manager-draft",
            actor_kind="user",
            base_dir=str(self.project),
        )

        snapshot = self.state.apply_effective_agent_profile_for_launch(
            cell,
            base_dir=str(self.project),
        )

        self.assertEqual(snapshot["id"], "product-manager-draft")
        self.assertEqual(cell.effective_agent_profile_id, "product-manager-draft")
        self.assertIn("architect_peer_inbox", "\n".join(snapshot["warnings"]))
        self.state.assign_agent_profile(
            cell.id,
            "full-architect",
            actor_kind="user",
            base_dir=str(self.project),
        )
        self.assertEqual(cell.agent_profile_id, "full-architect")
        self.assertEqual(cell.effective_agent_profile_id, "product-manager-draft")
        status = self.state.agent_profile_status_for_cell(cell, base_dir=str(self.project))
        self.assertTrue(status["pending_next_launch"])
        loaded = self.db.load_all()["agents"][cell.id]
        self.assertEqual(loaded["effective_agent_profile_id"], "product-manager-draft")
        self.assertEqual(
            loaded["effective_agent_profile_snapshot"]["id"],
            "product-manager-draft",
        )

    def test_clearing_assignment_marks_default_full_profile_pending_next_launch(self):
        cell = self._add_architect()
        self.state.assign_agent_profile(
            cell.id,
            "product-manager-draft",
            actor_kind="user",
            base_dir=str(self.project),
        )
        self.state.apply_effective_agent_profile_for_launch(
            cell,
            base_dir=str(self.project),
        )

        status = self.state.assign_agent_profile(
            cell.id,
            "",
            actor_kind="user",
            base_dir=str(self.project),
        )

        self.assertEqual(status["assigned_profile_id"], "")
        self.assertEqual(status["next_launch_profile_id"], "full-architect")
        self.assertEqual(status["effective_profile_id"], "product-manager-draft")
        self.assertTrue(status["pending_next_launch"])
        self.assertEqual(cell.effective_agent_profile_id, "product-manager-draft")

    def test_default_full_profile_snapshot_applies_for_unassigned_launch(self):
        cell = self._add_architect()

        snapshot = self.state.apply_effective_agent_profile_for_launch(
            cell,
            base_dir=str(self.project),
        )

        self.assertEqual(snapshot["id"], "full-architect")
        self.assertEqual(snapshot["status"], "full")
        self.assertEqual(cell.effective_agent_profile_id, "full-architect")


if __name__ == "__main__":
    unittest.main()
