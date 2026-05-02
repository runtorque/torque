import asyncio
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _install_aiohttp_stub():
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class WebSocketResponse:
        pass

    web.WebSocketResponse = WebSocketResponse
    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


class TorqueSmokeFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _install_aiohttp_stub()
        self.state_mod = importlib.import_module("torque.state")
        self.state_mod = importlib.reload(self.state_mod)
        self.mcp_engineer_mod = importlib.import_module("torque.mcp_engineer")
        self.mcp_engineer_mod = importlib.reload(self.mcp_engineer_mod)
        self.worktree_mod = importlib.import_module("torque.worktree")
        self.worktree_mod = importlib.reload(self.worktree_mod)

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name)
        self.worktree_mgr = self.worktree_mod.WorktreeManager()
        self.state = self.state_mod.MatrixState()
        self.state.add_group("g")

        self.engineer = self.state_mod.AgentCell(
            id="engineer-1",
            name="Engineer",
            slug="engineer",
            group="g",
            cell_type="agent",
            kind="engineer",
            session_id="engineer-session",
            status="running",
        )
        self.state.agents[self.engineer.id] = self.engineer
        self.state.groups["g"].append(self.engineer.id)
        self.state.update_group_settings(
            "g",
            engineer_agent_id=self.engineer.id,
            dispatch_lane="In Progress",
        )

        await self._git("init", "-b", "main")
        await self._git("config", "user.name", "Torque Smoke")
        await self._git("config", "user.email", "torque-smoke@example.com")
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

    async def _run_tool(self, name, args):
        text, is_error = await self.mcp_engineer_mod._dispatch_engineer_tool(
            name,
            args,
            self._handle_command,
            self.state,
            caller_id=self.engineer.id,
        )
        self.assertFalse(is_error, text)
        return json.loads(text)

    async def _handle_command(self, payload):
        cmd = payload["cmd"]
        if cmd == "board_add_task":
            task = self.state.board_add_task(
                payload.get("task", ""),
                payload.get("group", ""),
                lane=payload.get("lane", "") or "Backlog",
                description=payload.get("description", ""),
                labels=payload.get("labels", []),
                depends_on=payload.get("depends_on", []),
                parent_task_id=payload.get("parent_task_id", ""),
                pipeline_root_id=payload.get("pipeline_root_id", ""),
                pipeline_depth=payload.get("pipeline_depth", 0),
                assigned_engineer_id=payload.get("assigned_engineer_id", ""),
                created_by_architect_id=payload.get(
                    "created_by_architect_id", ""
                ),
            )
            if not task:
                return {"type": "error", "message": "Task creation failed"}
            return {"type": "ok", "id": task.id}

        if cmd == "board_update_task":
            task = self.state.board_tasks.get(payload.get("id", ""))
            if not task:
                return {"type": "error", "message": "Task not found"}
            fields = {
                key: value
                for key, value in payload.items()
                if key in self.state_mod.BoardTask.__dataclass_fields__
                and key not in {"id", "slug", "created_at"}
            }
            self.state.board_update_task(task.id, **fields)
            return {"type": "ok", "id": task.id}

        if cmd == "board_move_task":
            task = self.state.board_tasks.get(payload.get("id", ""))
            if not task:
                return {"type": "error", "message": "Task not found"}
            self.state.board_move_task(task.id, payload.get("lane", ""))
            return {"type": "ok", "id": task.id}

        if cmd == "dispatch_task":
            task = self.state.board_tasks.get(payload.get("id", ""))
            if not task:
                return {"type": "error", "message": "Task not found"}
            if task.agent_id:
                return {"type": "error", "message": "Task already assigned"}
            if not self.state.board_deps_met(task):
                return {
                    "type": "error",
                    "message": "Task dependencies are not complete",
                }

            agent = None
            if payload.get("create_agent"):
                name = payload.get("name", "") or self.state.next_cell_name("g", "agent")
                agent = self.state.add_agent(
                    name=name,
                    group="g",
                    profile="Default",
                    command=payload.get("command", "") or "codex",
                    directory=str(self.repo_root),
                )
                if not agent:
                    return {"type": "error", "message": "Agent creation failed"}
                agent.kind = "worker"
                agent.owner_engineer_id = payload.get("owner_engineer_id", "")
                agent.created_by_engineer_id = payload.get(
                    "_created_by_engineer_id", ""
                )
                agent.agent_type = payload.get("agent_type", "") or "codex"
                agent.status = "running"
                wt_path = await self.worktree_mgr.create(
                    agent,
                    str(self.repo_root),
                    base_branch="main",
                )
                if not wt_path:
                    return {"type": "error", "message": "Worktree creation failed"}
                agent.directory = wt_path
            else:
                agent = self.state.agents.get(payload.get("agent_id", ""))
                if not agent:
                    return {"type": "error", "message": "Agent not found"}
                if agent.current_task_id and agent.current_task_id != task.id:
                    self.state.board_update_task(task.id, agent_id=agent.id)
                    self.state.board_move_task(task.id, "To Do")
                    return {
                        "type": "queued",
                        "task_id": task.id,
                        "agent_id": agent.id,
                    }
                agent.status = "running"

            self.state.board_update_task(task.id, agent_id=agent.id)
            self.state.board_move_task(
                task.id,
                self.state.get_group_settings(task.group).dispatch_lane,
            )
            agent.current_task_id = task.id
            agent.tasks_dispatched += 1
            return {"type": "ok", "task_id": task.id, "agent_id": agent.id}

        if cmd == "resolve_ask":
            task = self.state.board_tasks.get(payload.get("id", ""))
            if not task:
                return {"type": "error", "message": "Task not found"}
            parent = self.state.board_tasks.get(task.parent_task_id)
            messages = list(task.messages)
            messages.append(
                {
                    "action": "resolve",
                    "message": payload.get("answer", ""),
                    "agent_name": "human",
                }
            )
            self.state.board_update_task(task.id, messages=messages, status="")
            self.state.board_move_task(task.id, "Done")
            self.state._clear_parent_awaiting_input(parent, exclude_task_id=task.id)
            return {"type": "ok", "resolved": True}

        self.fail(f"Unexpected command: {payload}")

    async def _complete_task(self, agent, task, *, summary):
        readme = Path(agent.worktree_path) / "README.md"
        readme.write_text(summary + "\n")
        sha = await self.worktree_mgr.checkpoint(agent, message=f"Finish {task.task}")
        self.assertTrue(sha)

        progress_messages = list(task.messages)
        progress_messages.append(
            {
                "action": "progress",
                "message": "Implementation in progress",
                "agent_name": agent.name,
            }
        )
        self.state.board_update_task(
            task.id,
            status="Reviewing",
            messages=progress_messages,
        )

        done_messages = list(self.state.board_tasks[task.id].messages)
        done_messages.append(
            {
                "action": "done",
                "message": summary,
                "agent_name": agent.name,
            }
        )
        self.state.board_update_task(task.id, messages=done_messages, status="")
        self.state.board_move_task(task.id, "Done")
        agent.current_task_id = ""
        agent.status = "idle"

    async def test_smoke_task_lifecycle_runs_from_engineer_creation_to_merge_cleanup(self):
        created = await self._run_tool(
            "engineer_task_create",
            {
                "title": "Implement smoke path",
                "description": "Exercise the critical Torque task lifecycle.",
                "labels": ["smoke"],
            },
        )
        task_id = created["id"]

        dispatched = await self._run_tool(
            "engineer_task_dispatch",
            {
                "task": task_id,
                "name": "Smoke Worker",
                "agent_type": "codex",
                "command": "codex",
            },
        )
        agent_id = dispatched["agent_id"]
        agent = self.state.agents[agent_id]
        task = self.state.board_tasks[task_id]

        self.assertTrue(Path(agent.worktree_path).is_dir())
        self.assertEqual(task.lane, "In Progress")
        self.assertEqual(task.agent_id, agent.id)
        self.assertEqual(agent.current_task_id, task.id)

        await self._complete_task(
            agent,
            task,
            summary="smoke flow merged cleanly",
        )

        merge_result = await self.worktree_mgr.server_merge(
            agent,
            "Merge smoke worker change",
        )
        self.assertTrue(merge_result["ok"], merge_result.get("error"))
        self.assertEqual((self.repo_root / "README.md").read_text(), "smoke flow merged cleanly\n")

        wt_path = agent.worktree_path
        removed = await self.worktree_mgr.remove(agent)
        self.assertTrue(removed)
        self.assertFalse(Path(wt_path).exists())
        self.assertEqual(agent.worktree_path, "")
        self.state.remove_agent(agent.id)

        summary = await self._run_tool("engineer_board_summary", {})
        task_after = self.state.board_tasks[task_id]
        self.assertEqual(task_after.lane, "Done")
        self.assertEqual(task_after.agent_id, "")
        self.assertEqual(summary["lanes"]["Done"], 1)
        self.assertEqual(summary["agents"]["active_count"], 0)
        self.assertEqual(summary["asks"]["count"], 0)

    async def test_smoke_engineer_orchestration_blocks_dependencies_until_ready(self):
        blocker = await self._run_tool(
            "engineer_task_create",
            {"title": "Land schema change"},
        )
        dependent = await self._run_tool(
            "engineer_task_create",
            {"title": "Ship API cleanup"},
        )
        self.state.board_update_task(
            dependent["id"],
            depends_on=[blocker["id"]],
        )

        batch = await self._run_tool(
            "engineer_batch_dispatch",
            {
                "tasks": [
                    {"task": blocker["id"]},
                    {"task": dependent["id"]},
                ],
                "max_concurrent": 2,
            },
        )

        self.assertEqual(
            [item["status"] for item in batch["results"]],
            ["dispatched", "failed"],
        )
        self.assertEqual(
            batch["results"][1]["reason"],
            "blocked_by_dependencies",
        )

        blocker_task = self.state.board_tasks[blocker["id"]]
        blocker_agent = self.state.agents[blocker_task.agent_id]
        await self._complete_task(
            blocker_agent,
            blocker_task,
            summary="schema ready for dependents",
        )

        ask = self.state.board_add_task(
            "Need release approval",
            "g",
            labels=["torque:human"],
            parent_task_id=blocker_task.id,
            pipeline_root_id=blocker_task.id,
            pipeline_depth=1,
            agent_id=blocker_agent.id,
        )
        self.assertIsNotNone(ask)

        resolved = await self._run_tool(
            "engineer_task_resolve",
            {
                "task": ask.id,
                "answer": "Approved for rollout",
            },
        )
        self.assertTrue(resolved["resolved"])
        self.assertEqual(self.state.board_tasks[ask.id].lane, "Done")

        dispatched = await self._run_tool(
            "engineer_task_dispatch",
            {
                "task": dependent["id"],
                "agent": blocker_agent.id,
            },
        )
        dependent_task = self.state.board_tasks[dependent["id"]]
        self.assertEqual(dispatched["agent_id"], blocker_agent.id)
        self.assertEqual(dependent_task.lane, "In Progress")
        self.assertEqual(dependent_task.agent_id, blocker_agent.id)
