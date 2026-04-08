import importlib
import tempfile
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class MemoryHelpersTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.db_mod = importlib.import_module("loom.db")
        self.db_mod = importlib.reload(self.db_mod)
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

    def test_select_relevant_prompt_entries_prefers_pins_then_nearest_scope(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = self.db_mod.LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)

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
            pipeline_root_id="root-1",
        )

        db.save_memory_entry(
            {
                "id": "mem-group-pin",
                "project_key": "/repo",
                "group_name": "g",
                "scope_kind": "group",
                "scope_ref": "g",
                "entry_type": "note",
                "title": "Pinned group note",
                "content": "Always run narrow tests first.",
                "pinned": True,
                "task_id": "",
                "source_kind": "agent",
                "source_id": "agent-1",
                "source_name": "Worker",
                "created_at": 10.0,
                "updated_at": 10.0,
            }
        )
        db.save_memory_entry(
            {
                "id": "mem-task",
                "project_key": "/repo",
                "group_name": "g",
                "scope_kind": "task",
                "scope_ref": "task-1",
                "entry_type": "decision",
                "title": "Task-local choice",
                "content": "Reuse the existing worktree.",
                "pinned": False,
                "task_id": "task-1",
                "source_kind": "agent",
                "source_id": "agent-1",
                "source_name": "Worker",
                "created_at": 20.0,
                "updated_at": 20.0,
            }
        )
        db.save_memory_entry(
            {
                "id": "mem-project",
                "project_key": "/repo",
                "group_name": "g",
                "scope_kind": "project",
                "scope_ref": "/repo",
                "entry_type": "finding",
                "title": "Project note",
                "content": "Avoid mutating unrelated tests.",
                "pinned": False,
                "task_id": "",
                "source_kind": "agent",
                "source_id": "agent-2",
                "source_name": "Reviewer",
                "created_at": 30.0,
                "updated_at": 30.0,
            }
        )
        db.save_memory_entry(
            {
                "id": "mem-link",
                "project_key": "/repo",
                "group_name": "g",
                "scope_kind": "project",
                "scope_ref": "/repo",
                "entry_type": "handoff",
                "title": "Linked handoff",
                "content": "The reviewer expects a summary of touched files.",
                "pinned": False,
                "task_id": "",
                "source_kind": "agent",
                "source_id": "agent-2",
                "source_name": "Reviewer",
                "created_at": 40.0,
                "updated_at": 40.0,
            }
        )
        db.save_memory_link(
            {
                "entry_id": "mem-link",
                "target_kind": "task",
                "target_ref": "task-1",
                "created_at": 41.0,
            }
        )

        selected = self.memory_mod.select_relevant_prompt_entries(
            db,
            cell=cell,
            task=task,
            max_entries=4,
        )

        self.assertEqual(
            [item["entry"]["id"] for item in selected],
            ["mem-group-pin", "mem-task", "mem-link", "mem-project"],
        )
        self.assertEqual(selected[0]["match_reasons"], ["scope_group"])
        self.assertEqual(selected[2]["match_reasons"], ["link_task", "scope_project"])

    def test_build_prompt_memory_block_is_bounded_and_shaped(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = self.db_mod.LoomDB(Path(tmp.name) / "loom.db")
        db.init()
        self.addCleanup(db.close)

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

        for idx in range(3):
            db.save_memory_entry(
                {
                    "id": f"mem-{idx}",
                    "project_key": "/repo",
                    "group_name": "g",
                    "scope_kind": "task",
                    "scope_ref": "task-1",
                    "entry_type": "decision",
                    "title": f"Decision {idx}",
                    "content": "x" * 300,
                    "pinned": idx == 0,
                    "task_id": "task-1",
                    "source_kind": "agent",
                    "source_id": "agent-1",
                    "source_name": "Worker",
                    "created_at": 10.0 + idx,
                    "updated_at": 10.0 + idx,
                }
            )

        block = self.memory_mod.build_prompt_memory_block(
            db,
            cell=cell,
            task=task,
            max_entries=2,
            max_chars=220,
        )

        self.assertIn("Relevant shared context", block)
        self.assertIn("Decision 0", block)
        self.assertIn("pinned", block)
        self.assertLessEqual(len(block), 220)
        self.assertEqual(block.count("\n- "), 1)
