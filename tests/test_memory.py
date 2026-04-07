import importlib
import unittest

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class MemoryHelpersTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.memory_mod = importlib.import_module("loom.memory")
        self.memory_mod = importlib.reload(self.memory_mod)

    def test_build_memory_entry_defaults_to_task_scope(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            directory="/repo",
            worktree_repo_root="/repo",
        )
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Implement feature",
            group="g",
        )
        state.agents[cell.id] = cell
        state.board_tasks[task.id] = task

        entry = self.memory_mod.build_memory_entry(
            state,
            cell=cell,
            task=task,
            entry_type="decision",
            content="Use explicit memory entries only.",
        )

        self.assertEqual(entry["scope_kind"], "task")
        self.assertEqual(entry["scope_ref"], "task-1")
        self.assertEqual(entry["group_name"], "g")
        self.assertEqual(entry["project_key"], "/repo")
        self.assertEqual(entry["entry_type"], "decision")

    def test_build_memory_entry_defaults_to_group_without_task(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            directory="/repo",
        )
        state.agents[cell.id] = cell

        entry = self.memory_mod.build_memory_entry(
            state,
            cell=cell,
            entry_type="warning",
            content="Avoid context bloat in full state snapshots.",
        )

        self.assertEqual(entry["scope_kind"], "group")
        self.assertEqual(entry["scope_ref"], "g")
        self.assertEqual(entry["group_name"], "g")

    def test_build_memory_entry_rejects_invalid_type(self):
        state = self.state_mod.MatrixState()
        with self.assertRaises(ValueError):
            self.memory_mod.build_memory_entry(
                state,
                entry_type="unknown",
                content="bad",
                scope_kind="group",
                scope_ref="g",
            )

    def test_build_memory_link_defaults_from_current_context(self):
        state = self.state_mod.MatrixState()
        cell = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
        )
        task = self.state_mod.BoardTask(
            id="task-1",
            task="Implement feature",
            group="g",
        )

        link = self.memory_mod.build_memory_link(
            state,
            entry_id="mem-1",
            target_kind="pipeline",
            cell=cell,
            task=task,
        )

        self.assertEqual(link["entry_id"], "mem-1")
        self.assertEqual(link["target_kind"], "pipeline")
        self.assertEqual(link["target_ref"], "task-1")

    def test_build_memory_link_rejects_missing_context(self):
        state = self.state_mod.MatrixState()
        with self.assertRaises(ValueError):
            self.memory_mod.build_memory_link(
                state,
                entry_id="mem-1",
                target_kind="agent",
            )
