import asyncio
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
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.db_mod = importlib.import_module("torque.db")
        self.db_mod = importlib.reload(self.db_mod)
        self.memory_mod = importlib.import_module("torque.memory")
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
        self.assertEqual(entry["retention_kind"], "ttl")
        self.assertAlmostEqual(
            entry["expires_at"],
            entry["created_at"] + 30 * 24 * 60 * 60,
        )

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

    def test_build_memory_entry_applies_the_same_ttl_to_every_type(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        state.update_group_settings("g", context_default_ttl_days=7)
        for entry_type in self.memory_mod.ENTRY_TYPES:
            entry = self.memory_mod.build_memory_entry(
                state,
                entry_type=entry_type,
                content=f"{entry_type} context.",
                scope_kind="group",
                scope_ref="g",
                retention_kind="durable",
                pinned=True,
            )
            self.assertEqual(entry["retention_kind"], "ttl")
            self.assertAlmostEqual(
                entry["expires_at"],
                entry["created_at"] + 7 * 24 * 60 * 60,
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
        db = self.db_mod.TorqueDB(Path(tmp.name) / "torque.db")
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
        db = self.db_mod.TorqueDB(Path(tmp.name) / "torque.db")
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


class MemoryCommandTests(unittest.TestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.db_mod = importlib.import_module("torque.db")
        self.db_mod = importlib.reload(self.db_mod)
        self.commands_mod = importlib.import_module("torque.commands.memory")
        self.commands_mod = importlib.reload(self.commands_mod)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = self.db_mod.TorqueDB(Path(self.tmp.name) / "torque.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.state = self.state_mod.MatrixState(db=self.db)
        self.state.add_group("g")
        self.author = self.state_mod.AgentCell(
            id="author", name="Author", group="g", directory="/repo",
        )
        self.other = self.state_mod.AgentCell(
            id="other", name="Other", group="g", directory="/repo",
        )
        self.state.agents = {self.author.id: self.author, self.other.id: self.other}

    def _resolve_cell_and_task(self, state, cell_id, _task_id):
        return state.agents.get(cell_id), None

    @staticmethod
    def _resolve_scope_ref(_scope_kind, scope_ref, *, cell, task):
        del cell, task
        return scope_ref

    @staticmethod
    def _resolve_link_ref(_target_kind, target_ref, *, cell, task):
        del cell, task
        return target_ref

    @staticmethod
    def _resolve_task_id(_state, task_id):
        return task_id

    def _command(self, data):
        return asyncio.run(self.commands_mod._handle_memory_command(
            data,
            self.state,
            resolve_cell_and_task=self._resolve_cell_and_task,
            resolve_scope_ref=self._resolve_scope_ref,
            resolve_link_ref=self._resolve_link_ref,
            resolve_task_id=self._resolve_task_id,
        ))

    def test_publish_legacy_retention_argument_uses_group_ttl_and_reports_notice(self):
        self.state.update_group_settings("g", context_default_ttl_days=9)

        result = self._command({
            "cmd": "memory_publish", "cell_id": "author",
            "entry_type": "decision", "content": "Use a bounded TTL.",
            "scope_kind": "group", "scope_ref": "g", "pinned": True,
            "retention_kind": "durable",
        })

        self.assertEqual(result["type"], "memory_entry")
        self.assertIn("deprecated", result["retention_note"])
        entry = result["entry"]
        self.assertEqual(entry["retention_kind"], "ttl")
        self.assertTrue(entry["pinned"])
        self.assertAlmostEqual(
            entry["expires_at"], entry["created_at"] + 9 * 24 * 60 * 60,
        )

    def test_non_author_cannot_update_shared_memory_entry(self):
        created = self._command({
            "cmd": "memory_publish", "cell_id": "author",
            "entry_type": "finding", "content": "Original content.",
            "scope_kind": "group", "scope_ref": "g",
        })["entry"]

        refused = self._command({
            "cmd": "memory_publish", "cell_id": "other", "entry_id": created["id"],
            "content": "Misattributed overwrite.",
        })

        self.assertEqual(refused["type"], "error")
        self.assertIn("original author", refused["message"])
        stored = self.db.load_memory_entry(created["id"])
        self.assertEqual(stored["content"], "Original content.")
        self.assertEqual(stored["source_id"], "author")

    def test_author_update_preserves_existing_expiry_including_legacy_null(self):
        self.db.save_memory_entry({
            "id": "legacy", "project_key": "/repo", "group_name": "g",
            "scope_kind": "group", "scope_ref": "g", "entry_type": "note",
            "title": "Legacy", "content": "Before update.", "pinned": False,
            "task_id": "", "source_kind": "agent", "source_id": "author",
            "source_name": "Author", "retention_kind": "durable", "expires_at": None,
            "created_at": 1.0, "updated_at": 1.0,
        })

        result = self._command({
            "cmd": "memory_publish", "cell_id": "author", "entry_id": "legacy",
            "content": "After update.",
        })

        self.assertEqual(result["type"], "memory_entry")
        self.assertIsNone(self.db._conn.execute(
            "SELECT expires_at FROM memory_entries WHERE id='legacy'"
        ).fetchone()[0])
