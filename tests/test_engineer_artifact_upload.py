import importlib
import json
import tempfile
import types
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class EngineerArtifactUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.reload(importlib.import_module("torque.state"))
        self.server_mod = importlib.reload(importlib.import_module("torque.server"))
        self.server_artifacts = importlib.reload(
            importlib.import_module("torque.server_artifacts")
        )
        self.mcp_engineer_mod = importlib.reload(
            importlib.import_module("torque.mcp_engineer")
        )

    @staticmethod
    def _make_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    def _extract_handle_command(self, state):
        main_code = self.server_mod.main.__code__
        handle_code = next(
            const
            for const in main_code.co_consts
            if isinstance(const, type(main_code))
            and const.co_name == "handle_command"
        )

        class DummyBridge:
            async def list_profiles(self):
                return []

            async def get_launch_context(self):
                return types.SimpleNamespace(
                    current_path="",
                    current_profile="",
                )

            async def update_session(self, *_args, **_kwargs):
                return None

        class DummyWorktreeManager:
            async def diff_summary(self, _cell, *, non_test_only=False):
                return {"files": 0, "insertions": 0, "deletions": 0}

            async def checkpoint(self, _cell, *, message=""):
                return ""

        async def noop_async(*_args, **_kwargs):
            return None

        closure_values = {
            name: None
            for name in handle_code.co_freevars
        }
        closure_values.update({
            "_broadcast_toast": noop_async,
            "_checkpoint_message": lambda _cell: "checkpoint",
            "_checkpoint_on_report": noop_async,
            "_cleanup_after_merge": noop_async,
            "_close_agent_session_only": noop_async,
            "_panel_event": lambda *args, **kwargs: None,
            "_record_task_boundary": noop_async,
            "_resolve_base_dir": noop_async,
            "_runtime_payload": lambda: {},
            "action_mgr": None,
            "bridge": DummyBridge(),
            "db": None,
            "handle_command": None,
            "panel_log": types.SimpleNamespace(
                replace_last=lambda *args, **kwargs: {}
            ),
            "state": state,
            "template_mgr": types.SimpleNamespace(
                resolve_agent_config=lambda *args, **kwargs: {},
                list_templates=lambda *args, **kwargs: [],
            ),
            "worktree_mgr": DummyWorktreeManager(),
        })
        closure = tuple(
            self._make_cell(closure_values[name])
            for name in handle_code.co_freevars
        )
        return types.FunctionType(
            handle_code,
            self.server_mod.__dict__,
            "handle_command",
            None,
            closure,
        )

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.board_lanes = ["Backlog", "To Do", "In Progress", "Done"]
        state.groups["torque"] = []
        return state

    def _add_engineer(self, state, agent_id, name, *, group="torque"):
        if group not in state.groups:
            state.groups[group] = []
        engineer = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower(),
            group=group,
            cell_type="agent",
            kind="engineer",
            status="running",
            persistent=True,
        )
        state.agents[engineer.id] = engineer
        state.groups[group].append(engineer.id)
        return engineer

    def _add_task(self, state, task_id, title, *, group="torque",
                  assigned_engineer_id="", created_by_engineer_id=""):
        if group not in state.groups:
            state.groups[group] = []
        task = self.state_mod.BoardTask(
            id=task_id,
            task=title,
            group=group,
            lane="In Progress",
            assigned_engineer_id=assigned_engineer_id,
            created_by_engineer_id=created_by_engineer_id,
        )
        state.board_tasks[task.id] = task
        return task

    async def test_engineer_upload_happy_path_accepts_task_and_task_id_alias(self):
        state = self._make_state()
        engineer = self._add_engineer(state, "eng-alice", "Alice")
        task = self._add_task(
            state,
            "TORQUE:upload",
            "Attach report",
            assigned_engineer_id=engineer.id,
        )
        handle_command = self._extract_handle_command(state)

        with tempfile.TemporaryDirectory() as tmpdir:
            original_dir = self.server_artifacts.ATTACHMENTS_DIR
            self.server_artifacts.ATTACHMENTS_DIR = Path(tmpdir)
            try:
                text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
                    "engineer_task_upload_artifact",
                    {
                        "task": task.id,
                        "filename": "report.md",
                        "content_text": "# Report\n\nDetails",
                        "artifact_type": "report",
                        "title": "Engineer report",
                    },
                    handle_command,
                    state,
                    caller_id=engineer.id,
                )
                self.assertFalse(is_error, text)
                first = json.loads(text)
                self.assertEqual(first["type"], "task_artifact_uploaded")

                text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
                    "engineer_task_upload_artifact",
                    {
                        "task_id": task.id,
                        "filename": "design.md",
                        "content_text": "# Design\n\nDetails",
                        "artifact_type": "design",
                        "title": "Engineer design",
                    },
                    handle_command,
                    state,
                    caller_id=engineer.id,
                )
                self.assertFalse(is_error, text)
                second = json.loads(text)
                self.assertEqual(second["type"], "task_artifact_uploaded")
            finally:
                self.server_artifacts.ATTACHMENTS_DIR = original_dir

        refreshed = state.board_tasks[task.id]
        self.assertEqual(len(refreshed.artifacts), 2)
        self.assertEqual(
            [artifact["provenance"]["source"] for artifact in refreshed.artifacts],
            ["engineer", "engineer"],
        )
        self.assertEqual(
            [artifact["provenance"]["agent_id"] for artifact in refreshed.artifacts],
            [engineer.id, engineer.id],
        )

    async def test_engineer_upload_rejects_out_of_scope_task_without_artifact(self):
        state = self._make_state()
        alice = self._add_engineer(state, "eng-alice", "Alice")
        bob = self._add_engineer(state, "eng-bob", "Bob")
        task = self._add_task(
            state,
            "TORQUE:bob",
            "Bob's report",
            assigned_engineer_id=bob.id,
        )
        handle_command = self._extract_handle_command(state)

        async def fail_if_called(_payload):
            self.fail("MCP dispatch should reject out-of-scope upload")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_task_upload_artifact",
            {
                "task": task.id,
                "filename": "bad.md",
                "content_text": "should not attach",
            },
            fail_if_called,
            state,
            caller_id=alice.id,
        )
        self.assertTrue(is_error)
        self.assertEqual(text, "task not found in scope")
        self.assertEqual(state.board_tasks[task.id].artifacts, [])

        with tempfile.TemporaryDirectory() as tmpdir:
            original_dir = self.server_artifacts.ATTACHMENTS_DIR
            self.server_artifacts.ATTACHMENTS_DIR = Path(tmpdir)
            try:
                result = await handle_command({
                    "cmd": "task_upload_artifact",
                    "cell_id": alice.id,
                    "task_id": task.id,
                    "filename": "bad.md",
                    "content_text": "should not attach",
                })
            finally:
                self.server_artifacts.ATTACHMENTS_DIR = original_dir

        self.assertEqual(
            result,
            {"type": "error", "message": "task not found in scope"},
        )
        self.assertEqual(state.board_tasks[task.id].artifacts, [])
