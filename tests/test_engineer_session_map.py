import importlib
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class EngineerSessionMapScopingTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.session_map_mod = importlib.import_module("loom.engineer_session_map")
        self.session_map_mod = importlib.reload(self.session_map_mod)

    def _add_cell(self, state, agent_id, name, *, kind="worker",
                  cell_type="agent", owner_engineer_id="",
                  created_by_engineer_id="", hired_by_architect_id="",
                  parent_id="", status="running"):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower().replace(" ", "-"),
            group="loom",
            cell_type=cell_type,
            kind=kind,
            owner_engineer_id=owner_engineer_id,
            created_by_engineer_id=created_by_engineer_id,
            hired_by_architect_id=hired_by_architect_id,
            parent_id=parent_id,
            status=status,
        )
        state.agents[cell.id] = cell
        state.groups.setdefault("loom", []).append(cell.id)
        return cell

    def _make_scoped_state(self):
        state = self.state_mod.MatrixState()
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done", "Archived"]
        state.groups["loom"] = []
        architect = self._add_cell(
            state, "arch-a", "Architect A", kind="architect"
        )
        user = self._add_cell(state, "user", "User", kind="user")
        engineer_a = self._add_cell(
            state,
            "eng-a",
            "Engineer A",
            kind="engineer",
            hired_by_architect_id=architect.id,
        )
        engineer_b = self._add_cell(
            state, "eng-b", "Engineer B", kind="engineer"
        )
        worker_a = self._add_cell(
            state,
            "worker-a",
            "Worker A",
            kind="worker",
            owner_engineer_id=engineer_a.id,
            created_by_engineer_id=engineer_a.id,
        )
        terminal_a = self._add_cell(
            state,
            "terminal-a",
            "Terminal A",
            kind="terminal",
            cell_type="terminal",
            parent_id=worker_a.id,
        )
        worker_b = self._add_cell(
            state,
            "worker-b",
            "Worker B",
            kind="worker",
            owner_engineer_id=engineer_b.id,
            created_by_engineer_id=engineer_b.id,
        )
        state.group_settings["loom"] = self.state_mod.GroupSettings(
            engineer_agent_id=engineer_a.id,
        )
        return state, {
            "architect": architect,
            "user": user,
            "engineer_a": engineer_a,
            "engineer_b": engineer_b,
            "worker_a": worker_a,
            "terminal_a": terminal_a,
            "worker_b": worker_b,
        }

    def test_active_agents_are_scoped_to_engineer_owner(self):
        state, cells = self._make_scoped_state()

        session_map = self.session_map_mod.build_engineer_session_map(
            state,
            "loom",
            engineer_cell=cells["engineer_a"],
            item_limit=20,
        )

        agent_ids = {item["id"] for item in session_map["agents"]["items"]}
        self.assertIn(cells["engineer_a"].id, agent_ids)
        self.assertIn(cells["worker_a"].id, agent_ids)
        self.assertIn(cells["terminal_a"].id, agent_ids)
        self.assertIn(cells["architect"].id, agent_ids)
        self.assertIn(cells["user"].id, agent_ids)
        self.assertNotIn(cells["engineer_b"].id, agent_ids)
        self.assertNotIn(cells["worker_b"].id, agent_ids)

    def test_session_map_falls_back_to_group_engineer_cell(self):
        state, cells = self._make_scoped_state()

        session_map = self.session_map_mod.build_engineer_session_map(
            state,
            "loom",
            item_limit=20,
        )

        agent_ids = {item["id"] for item in session_map["agents"]["items"]}
        self.assertIn(cells["engineer_a"].id, agent_ids)
        self.assertIn(cells["worker_a"].id, agent_ids)
        self.assertNotIn(cells["engineer_b"].id, agent_ids)
        self.assertNotIn(cells["worker_b"].id, agent_ids)

    def test_verification_omits_checkpoints_for_merged_worktree_tasks(self):
        state, cells = self._make_scoped_state()
        BoardTask = self.state_mod.BoardTask
        state.board_tasks["active"] = BoardTask(
            id="active",
            task="Active verification",
            group="loom",
            lane="In Progress",
            verification_mode="restart",
            verification_state="pending",
            worktree_boundary={"status": "open"},
        )
        state.board_tasks["no-boundary"] = BoardTask(
            id="no-boundary",
            task="No boundary verification",
            group="loom",
            lane="In Progress",
            verification_mode="restart",
            verification_state="pending",
        )
        state.board_tasks["merged"] = BoardTask(
            id="merged",
            task="Merged verification",
            group="loom",
            lane="Done",
            verification_mode="restart",
            verification_state="pending",
            worktree_boundary={"status": "merged"},
        )

        session_map = self.session_map_mod.build_engineer_session_map(
            state,
            "loom",
            engineer_cell=cells["engineer_a"],
            item_limit=20,
        )

        verification = session_map["verification"]
        item_ids = {item["id"] for item in verification["items"]}
        self.assertIn("active", item_ids)
        self.assertIn("no-boundary", item_ids)
        self.assertNotIn("merged", item_ids)
        self.assertEqual(2, verification["counts"]["pending"])


if __name__ == "__main__":
    unittest.main()
