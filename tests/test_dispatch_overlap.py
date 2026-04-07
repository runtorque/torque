import importlib
import sys
import types
import unittest


def _install_aiohttp_stub():
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class WebSocketResponse:
        pass

    web.WebSocketResponse = WebSocketResponse
    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


class DispatchOverlapTests(unittest.TestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.overlap_mod = importlib.import_module("loom.dispatch_overlap")
        self.overlap_mod = importlib.reload(self.overlap_mod)

    def test_compute_overlap_flags_shared_branch_conflict(self):
        task_a = self.state_mod.BoardTask(
            id="task-a",
            task="Implement auth flow",
            group="g",
            lane="In Progress",
            agent_id="agent-a",
        )
        task_b = self.state_mod.BoardTask(
            id="task-b",
            task="Review auth flow",
            group="g",
            lane="In Progress",
            agent_id="agent-b",
        )
        agent_a = self.state_mod.AgentCell(
            id="agent-a",
            name="Agent A",
            group="g",
            cell_type="agent",
            worktree_branch="loom/shared-auth",
            worktree_repo_root="/repo",
            worktree_changed_files=["src/auth/login.py"],
        )
        agent_b = self.state_mod.AgentCell(
            id="agent-b",
            name="Agent B",
            group="g",
            cell_type="agent",
            worktree_branch="loom/shared-auth",
            worktree_repo_root="/repo",
            worktree_changed_files=["src/auth/login.py"],
        )

        snapshots, groups = self.overlap_mod.compute_dispatch_overlap(
            {"task-a": task_a, "task-b": task_b},
            {"agent-a": agent_a, "agent-b": agent_b},
        )

        self.assertEqual(snapshots["task-a"]["level"], "conflict")
        self.assertEqual(groups["g"]["counts"]["conflict"], 2)

    def test_assess_dispatch_downgrades_same_agent_follow_up(self):
        current = self.state_mod.BoardTask(
            id="task-1",
            task="Billing dashboard implementation",
            group="g",
            lane="In Progress",
            agent_id="agent-1",
        )
        follow_up = self.state_mod.BoardTask(
            id="task-2",
            task="Billing dashboard review",
            group="g",
            lane="Backlog",
        )
        agent = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
            worktree_branch="loom/billing",
            worktree_repo_root="/repo",
            worktree_changed_files=["src/billing/view.py"],
        )

        result = self.overlap_mod.assess_dispatch_overlap(
            {"task-1": current, "task-2": follow_up},
            {"agent-1": agent},
            follow_up,
            target_agent_id="agent-1",
        )

        self.assertEqual(result["level"], "notice")

    def test_assess_dispatch_warns_for_parallel_same_surface_work(self):
        active = self.state_mod.BoardTask(
            id="task-1",
            task="Notifications center implementation",
            group="g",
            lane="In Progress",
            agent_id="agent-1",
        )
        candidate = self.state_mod.BoardTask(
            id="task-2",
            task="Notifications center review",
            group="g",
            lane="Backlog",
        )
        agent = self.state_mod.AgentCell(
            id="agent-1",
            name="Worker",
            group="g",
            cell_type="agent",
        )

        result = self.overlap_mod.assess_dispatch_overlap(
            {"task-1": active, "task-2": candidate},
            {"agent-1": agent},
            candidate,
            create_agent=True,
        )

        self.assertEqual(result["level"], "warning")
