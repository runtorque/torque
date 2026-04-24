import asyncio
import importlib
import json
import sys
import tempfile
import types
import unittest
from enum import Enum
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


def _install_iterm2_stub():
    iterm2 = types.ModuleType("iterm2")

    class Connection:
        pass

    class Modifier(Enum):
        COMMAND = "command"
        OPTION = "option"
        SHIFT = "shift"
        CONTROL = "control"
        FUNCTION = "function"

    class Keycode(Enum):
        UP_ARROW = "UP_ARROW"
        DOWN_ARROW = "DOWN_ARROW"
        LEFT_ARROW = "LEFT_ARROW"
        RIGHT_ARROW = "RIGHT_ARROW"
        HOME = "HOME"
        END = "END"
        PAGE_UP = "PAGE_UP"
        PAGE_DOWN = "PAGE_DOWN"
        FORWARD_DELETE = "FORWARD_DELETE"
        ANSI_A = "ANSI_A"
        ANSI_B = "ANSI_B"
        ANSI_C = "ANSI_C"
        ANSI_T = "ANSI_T"

    binding = types.ModuleType("iterm2.binding")
    keyboard = types.ModuleType("iterm2.keyboard")
    tool = types.SimpleNamespace(async_register_web_view_tool=None)
    keyboard.Modifier = Modifier
    keyboard.Keycode = Keycode
    iterm2.Connection = Connection
    iterm2.tool = tool
    iterm2.binding = binding
    iterm2.keyboard = keyboard
    sys.modules["iterm2"] = iterm2
    sys.modules["iterm2.binding"] = binding
    sys.modules["iterm2.keyboard"] = keyboard


class ReviewCycleBranchIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub(include_json_helpers=True)
        _install_iterm2_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.server_mod = importlib.import_module("loom.server")
        self.server_mod = importlib.reload(self.server_mod)
        self.worktree_mod = importlib.import_module("loom.worktree")
        self.worktree_mod = importlib.reload(self.worktree_mod)
        self.mcp_engineer_mod = importlib.import_module("loom.mcp_engineer")
        self.mcp_engineer_mod = importlib.reload(self.mcp_engineer_mod)

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.worktree_mgr = self.worktree_mod.WorktreeManager()
        await self._git("init", "-b", "main")
        await self._git("config", "user.name", "Loom Review Cycle Test")
        await self._git("config", "user.email", "loom-review@example.com")
        (self.repo_root / "README.md").write_text("base\n")
        await self._git("add", "README.md")
        await self._git("commit", "-m", "Initial commit")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _git(self, *args, cwd=None):
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(cwd or self.repo_root),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            self.fail(
                f"git {' '.join(args)} failed: {stderr.decode().strip()}"
            )
        return stdout.decode().strip()

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.add_group("g")
        state.update_group_settings("g", dispatch_lane="In Progress")
        return state

    async def _make_worker_with_worktree(self, state, agent_id, name):
        cell = self.state_mod.AgentCell(
            id=agent_id,
            name=name,
            slug=name.lower(),
            group="g",
            cell_type="agent",
            kind="worker",
            status="running",
            session_id=f"{agent_id}-session",
            directory=str(self.repo_root),
            worktree_merge_squash=False,
        )
        state.agents[cell.id] = cell
        state.groups["g"].append(cell.id)
        wt_path = await self.worktree_mgr.create(
            cell,
            str(self.repo_root),
            base_branch="main",
        )
        self.assertTrue(wt_path)
        cell.directory = wt_path
        return cell

    async def test_review_derived_fix_inherits_implementer_branch_and_merges_fix(self):
        state = self._make_state()
        implementer = await self._make_worker_with_worktree(
            state, "impl-1", "Implementer"
        )
        reviewer = await self._make_worker_with_worktree(
            state, "review-1", "Reviewer"
        )

        (Path(implementer.worktree_path) / "feature.txt").write_text(
            "implemented\n"
        )
        await self._git("add", "feature.txt", cwd=implementer.worktree_path)
        await self._git(
            "commit", "-m", "Implement feature", cwd=implementer.worktree_path
        )
        implementer_head = await self._git(
            "rev-parse", "HEAD", cwd=implementer.worktree_path
        )

        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-impl",
            action_name="feature/implement",
            agent_id=implementer.id,
        )
        review = state.board_add_task(
            "Review feature",
            "g",
            lane="In Progress",
            id="task-review",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            agent_id=reviewer.id,
        )

        derive_parent = self.server_mod._ai_derive_parent_task(state, review)
        fix = state.board_add_task(
            "Fix review issues",
            "g",
            lane="Backlog",
            id="task-fix",
            action_name="feature/implement",
            parent_task_id=derive_parent.id,
            pipeline_root_id=root.id,
            pipeline_depth=review.pipeline_depth + 1,
        )

        self.assertEqual(fix.parent_task_id, root.id)
        self.assertNotEqual(fix.parent_task_id, review.id)

        inherited = self.server_mod._resolve_inherited_worktree_source(
            state, fix
        )
        self.assertIs(inherited, implementer)

        fix_worker = self.state_mod.AgentCell(
            id="fix-1",
            name="Fix Worker",
            group="g",
            cell_type="agent",
            kind="worker",
        )
        self.assertTrue(
            self.server_mod._copy_worktree_context(fix_worker, inherited)
        )
        self.assertEqual(fix_worker.worktree_branch, implementer.worktree_branch)
        self.assertEqual(fix_worker.worktree_path, implementer.worktree_path)
        self.assertEqual(
            await self._git("rev-parse", "HEAD", cwd=fix_worker.worktree_path),
            implementer_head,
        )

        (Path(fix_worker.worktree_path) / "fix.txt").write_text("fixed\n")
        await self._git("add", "fix.txt", cwd=fix_worker.worktree_path)
        await self._git(
            "commit", "-m", "Fix review issues", cwd=fix_worker.worktree_path
        )

        divergence = await self.server_mod._review_cycle_sibling_branch_divergence(
            state, implementer, self.worktree_mgr
        )
        self.assertEqual(divergence, [])

        merge_result = await self.worktree_mgr.server_merge(
            implementer,
            "Merge implementation with review fixes",
            squash=False,
        )
        self.assertTrue(merge_result["ok"], merge_result)
        self.assertEqual(
            await self._git("show", "main:feature.txt"),
            "implemented",
        )
        self.assertEqual(await self._git("show", "main:fix.txt"), "fixed")

        (Path(reviewer.worktree_path) / "reviewer-only.txt").write_text(
            "drift\n"
        )
        await self._git("add", "reviewer-only.txt", cwd=reviewer.worktree_path)
        await self._git(
            "commit", "-m", "Reviewer branch drift", cwd=reviewer.worktree_path
        )

        divergence = await self.server_mod._review_cycle_sibling_branch_divergence(
            state, implementer, self.worktree_mgr
        )
        self.assertEqual(len(divergence), 1)
        self.assertEqual(divergence[0]["branch"], reviewer.worktree_branch)
        self.assertEqual(divergence[0]["ahead"], 1)

        blocked = await self.server_mod._sibling_branch_divergence_gate_for_merge(
            state, implementer, self.worktree_mgr, implementer.id, {}
        )
        self.assertIsNotNone(blocked)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["code"], "sibling_branch_divergence")
        self.assertEqual(blocked["siblings"][0]["branch"], reviewer.worktree_branch)
        self.assertIn("Ship-verdict fix", blocked["error"])
        self.assertIn("force=true", blocked["error"])
        forced = await self.server_mod._sibling_branch_divergence_gate_for_merge(
            state,
            implementer,
            self.worktree_mgr,
            implementer.id,
            {"force": True},
        )
        self.assertIsNone(forced)

    def _make_review_fix_handoff_graph(self):
        state = self._make_state()
        implementer = self.state_mod.AgentCell(
            id="impl-1",
            name="Implementer",
            group="g",
            cell_type="agent",
            session_id="impl-session",
            status="running",
            worktree_path="/repo/.loom/worktrees/impl",
            worktree_branch="loom/shared",
            worktree_repo_root="/repo",
        )
        reviewer = self.state_mod.AgentCell(
            id="review-1",
            name="Reviewer",
            group="g",
            cell_type="agent",
            session_id="review-session",
            status="running",
            worktree_path="/repo/.loom/worktrees/impl",
            worktree_branch="loom/shared",
            worktree_repo_root="/repo",
        )
        state.agents = {implementer.id: implementer, reviewer.id: reviewer}
        state.groups["g"].extend([implementer.id, reviewer.id])
        root = state.board_add_task(
            "Implement feature",
            "g",
            lane="In Progress",
            id="task-root",
            action_name="feature/implement",
            agent_id=implementer.id,
        )
        review = state.board_add_task(
            "Review feature",
            "g",
            lane="In Progress",
            id="task-review",
            action_name="feature/review",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=1,
            agent_id=reviewer.id,
        )
        review.status = "Fixing"
        fix = state.board_add_task(
            "Fix review issues",
            "g",
            lane="In Progress",
            id="task-fix",
            action_name="feature/implement",
            parent_task_id=root.id,
            pipeline_root_id=root.id,
            pipeline_depth=2,
            agent_id=implementer.id,
        )
        implementer.current_task_id = fix.id
        reviewer.current_task_id = ""
        return state, implementer, reviewer, root, review, fix

    def test_review_derived_fix_sibling_releases_reviewer_slot_and_checkpoint_block(self):
        (
            state,
            implementer,
            reviewer,
            _root,
            review,
            fix,
        ) = self._make_review_fix_handoff_graph()

        self.assertEqual(fix.parent_task_id, "task-root")
        self.assertNotEqual(fix.parent_task_id, review.id)
        self.assertEqual(
            [task.id for task in state.review_handoff_followups(review.id)],
            [fix.id],
        )
        self.assertFalse(
            state.task_occupies_execution_slot(
                review,
                agent_id=reviewer.id,
            )
        )
        self.assertFalse(state.agent_is_busy(reviewer.id))
        self.assertIsNone(
            self.server_mod._active_shared_worktree_review_for_cell(
                state,
                implementer,
            )
        )
        self.assertEqual(
            self.server_mod._shared_review_checkpoint_block_reason(
                state,
                implementer,
            ),
            "",
        )

    def test_review_derived_fix_sibling_cascades_review_and_root_when_done(self):
        state, _implementer, _reviewer, root, review, fix = (
            self._make_review_fix_handoff_graph()
        )

        state.board_move_task(fix.id, "Done")

        self.assertEqual(state.board_tasks[fix.id].lane, "Done")
        self.assertEqual(state.board_tasks[review.id].lane, "Done")
        self.assertEqual(state.board_tasks[review.id].status, "")
        self.assertEqual(state.board_tasks[root.id].lane, "Done")
        self.assertFalse(state.task_has_unresolved_descendants(root.id))

    def test_review_derived_fix_cascades_after_rereview_path_finishes(self):
        state, _implementer, _reviewer, root, review, fix = (
            self._make_review_fix_handoff_graph()
        )
        rereviewer = self.state_mod.AgentCell(
            id="review-2",
            name="Re-reviewer",
            group="g",
            cell_type="agent",
            session_id="review-2-session",
            status="running",
            worktree_path="/repo/.loom/worktrees/impl",
            worktree_branch="loom/shared",
            worktree_repo_root="/repo",
        )
        state.agents[rereviewer.id] = rereviewer
        state.groups["g"].append(rereviewer.id)
        fix.status = "On Review"
        rereview = state.board_add_task(
            "Re-review fixes",
            "g",
            lane="In Progress",
            id="task-rereview",
            action_name="feature/review",
            parent_task_id=fix.id,
            pipeline_root_id=root.id,
            pipeline_depth=3,
            agent_id=rereviewer.id,
        )

        state.board_move_task(rereview.id, "Done")

        self.assertEqual(state.board_tasks[rereview.id].lane, "Done")
        self.assertEqual(state.board_tasks[fix.id].lane, "Done")
        self.assertEqual(state.board_tasks[review.id].lane, "Done")
        self.assertEqual(state.board_tasks[root.id].lane, "Done")
        self.assertFalse(state.task_has_unresolved_descendants(review.id))
        self.assertFalse(state.task_has_unresolved_descendants(root.id))

    async def test_engineer_merge_passes_force_override_to_worktree_merge(self):
        state = self.state_mod.MatrixState()
        state.groups["loom"] = []
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Engineer",
            slug="engineer",
            group="loom",
            cell_type="agent",
            kind="engineer",
            status="running",
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            slug="worker",
            group="loom",
            cell_type="agent",
            kind="worker",
            owner_engineer_id=engineer.id,
            created_by_engineer_id=engineer.id,
            status="idle",
            worktree_path="/tmp/worker",
            worktree_branch="loom/worker",
            worktree_base_branch="main",
        )
        state.agents[engineer.id] = engineer
        state.agents[worker.id] = worker
        state.groups["loom"].extend([engineer.id, worker.id])
        calls = []

        async def handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                return {
                    "type": "worktree_check_merge",
                    "id": worker.id,
                    "clean": True,
                    "conflicts": [],
                }
            if payload["cmd"] == "worktree_merge":
                self.assertTrue(payload.get("force"))
                return {
                    "type": "worktree_merge",
                    "id": worker.id,
                    "ok": True,
                    "sha": "abc123",
                    "cleanup": {"errors": []},
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_merge",
            {"agent": worker.id, "force": True},
            handle_command,
            state,
            caller_id=engineer.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(json.loads(text)["sha"], "abc123")
        self.assertEqual(
            [call["cmd"] for call in calls],
            ["worktree_check_merge", "worktree_merge"],
        )
        self.assertTrue(calls[-1].get("force"))

    async def test_engineer_merge_passes_auto_move_override_to_worktree_merge(self):
        state = self.state_mod.MatrixState()
        state.groups["loom"] = []
        engineer = self.state_mod.AgentCell(
            id="eng-1",
            name="Engineer",
            slug="engineer",
            group="loom",
            cell_type="agent",
            kind="engineer",
            status="running",
        )
        worker = self.state_mod.AgentCell(
            id="worker-1",
            name="Worker",
            slug="worker",
            group="loom",
            cell_type="agent",
            kind="worker",
            owner_engineer_id=engineer.id,
            created_by_engineer_id=engineer.id,
            status="idle",
            worktree_path="/tmp/worker",
            worktree_branch="loom/worker",
            worktree_base_branch="main",
        )
        state.agents[engineer.id] = engineer
        state.agents[worker.id] = worker
        state.groups["loom"].extend([engineer.id, worker.id])
        calls = []

        async def handle_command(payload):
            calls.append(dict(payload))
            if payload["cmd"] == "worktree_check_merge":
                return {
                    "type": "worktree_check_merge",
                    "id": worker.id,
                    "clean": True,
                    "conflicts": [],
                }
            if payload["cmd"] == "worktree_merge":
                self.assertFalse(payload.get("auto_move_to_done"))
                return {
                    "type": "worktree_merge",
                    "id": worker.id,
                    "ok": True,
                    "sha": "abc123",
                    "cleanup": {"errors": []},
                }
            self.fail(f"Unexpected command: {payload}")

        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            "engineer_merge",
            {"agent": worker.id, "auto_move_to_done": False},
            handle_command,
            state,
            caller_id=engineer.id,
        )

        self.assertFalse(is_error, text)
        self.assertEqual(json.loads(text)["sha"], "abc123")
        self.assertEqual(
            [call["cmd"] for call in calls],
            ["worktree_check_merge", "worktree_merge"],
        )


if __name__ == "__main__":
    unittest.main()
