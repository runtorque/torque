"""End-to-end gate test for the action-catalog deliverable rollout.

Walks the whole loom_done gate flow with the three catalog actions that
own a deliverable contract (`oneshot/investigate`, `oneshot/audit`,
`feature/research`). For each one:

1. Build a state with a worker assigned to a board task that carries
   the action's deliverable contract (mirroring what `dispatch_task`
   stamps onto the task at dispatch time).
2. Drive the server's ``handle_command`` with ``cmd=ai_report
   action=done`` — confirm it is refused with ``deliverable_missing``
   and the task stays in ``In Progress``.
3. Drive ``cmd=task_upload_artifact`` with the matching ``artifact_type``
   — confirm the artifact lands on the task.
4. Re-drive ``cmd=ai_report action=done`` — confirm the gate now lets
   the task through and it moves to ``Done``.

This exercises the same code path the live HTTP daemon does (just
without the aiohttp socket layer), giving us deterministic coverage of
the runtime gate firing on the freshly added catalog YAMLs.
"""

import importlib
import types
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = REPO_ROOT / ".loom" / "actions"


class CatalogGateLiveValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.state_mod = importlib.reload(
            importlib.import_module("loom.state")
        )
        self.actions_mod = importlib.reload(
            importlib.import_module("loom.actions")
        )
        self.server_mod = importlib.reload(
            importlib.import_module("loom.server")
        )

    @staticmethod
    def _make_cell(value):
        return (lambda x: lambda: x)(value).__closure__[0]

    def _extract_handle_command(self, state, *, action_mgr):
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
            "action_mgr": action_mgr,
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

    def _build_state_with_contract(self, *, action_name: str,
                                   contract: dict):
        """Mirror ``dispatch_task`` by stamping the action's contract
        onto the board task at creation time."""
        state = self.state_mod.MatrixState()
        state.groups["g"] = []
        cell = self.state_mod.AgentCell(
            id="worker-1",
            name="Catalog worker",
            group="g",
            cell_type="agent",
            kind="worker",
            directory=str(REPO_ROOT),
        )
        state.agents[cell.id] = cell
        task = state.board_add_task(
            f"Catalog deliverable check: {action_name}",
            "g",
            lane="In Progress",
            id="task-deliv",
            action_name=action_name,
            agent_id=cell.id,
            deliverable_required=bool(contract["required"]),
            deliverable_type=contract["type"],
            deliverable_format=contract["format"],
            deliverable_artifact_title=contract["artifact_title"],
        )
        cell.current_task_id = task.id
        return state, cell, task

    async def _walk_gate(self, action_name: str):
        action_mgr = self.actions_mod.ActionManager()
        contract = action_mgr.get_deliverable(
            action_name, base_dir=str(REPO_ROOT))
        # Sanity: the catalog YAML actually exposes a contract — otherwise
        # the gate won't engage and this test is meaningless.
        self.assertTrue(
            contract["required"],
            f"action {action_name!r} missing deliverable.required: "
            "rollout is incomplete",
        )

        state, cell, task = self._build_state_with_contract(
            action_name=action_name, contract=contract)
        handle_command = self._extract_handle_command(
            state, action_mgr=action_mgr)

        # 1. loom_done before uploading must be refused.
        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "done",
            "message": "Pretend report goes here",
        })
        self.assertEqual(
            result.get("type"),
            "deliverable_missing",
            f"{action_name}: expected deliverable_missing, got {result}",
        )
        self.assertEqual(task.lane, "In Progress")
        self.assertFalse(task.artifacts)

        # 2. Upload an artifact matching the contract.
        upload_result = await handle_command({
            "cmd": "task_upload_artifact",
            "cell_id": cell.id,
            "task_id": task.id,
            "content_text": (
                f"# {contract['artifact_title']}\n\n"
                "Findings, recommendations, etc."
            ),
            "artifact_type": contract["type"],
            "title": contract["artifact_title"],
            "mime_type": "text/markdown",
        })
        self.assertEqual(
            upload_result.get("type"),
            "task_artifact_uploaded",
            f"{action_name}: artifact upload failed: {upload_result}",
        )
        refreshed = state.board_tasks.get(task.id)
        self.assertTrue(
            refreshed.artifacts,
            f"{action_name}: artifact missing from task after upload",
        )
        artifact = refreshed.artifacts[0]
        self.assertEqual(
            (artifact.get("type") or artifact.get("artifact_type") or "")
            .lower(),
            contract["type"].lower(),
        )

        # 3. loom_done now must succeed and move the task to Done.
        result = await handle_command({
            "cmd": "ai_report",
            "cell_id": cell.id,
            "action": "done",
            "message": "Report attached.",
        })
        self.assertNotEqual(
            result.get("type") if isinstance(result, dict) else None,
            "deliverable_missing",
            f"{action_name}: gate still rejected after artifact upload: "
            f"{result}",
        )
        self.assertEqual(
            state.board_tasks[task.id].lane,
            "Done",
            f"{action_name}: task did not advance to Done; final={result}",
        )

    async def test_oneshot_investigate_gate_walk(self):
        await self._walk_gate("oneshot/investigate")

    async def test_oneshot_audit_gate_walk(self):
        await self._walk_gate("oneshot/audit")

    async def test_feature_research_gate_walk(self):
        await self._walk_gate("feature/research")


if __name__ == "__main__":
    unittest.main()
