import importlib
import tempfile
import types
import unittest
from pathlib import Path

try:
    from helpers import install_aiohttp_stub
except ModuleNotFoundError:
    from tests.helpers import install_aiohttp_stub


class _CapturingBridge:
    def __init__(self):
        self.capabilities = types.SimpleNamespace(
            supports_embedded_terminal=False,
        )
        self.create_session_calls = []

    async def create_session(self, cell, **kwargs):
        cell.session_id = kwargs.get("session_id", "session-new")
        self.create_session_calls.append({
            "cell": cell,
            "kwargs": kwargs,
        })


class _FakeWorktreeManager:
    async def validate(self, cell):
        del cell
        return True

    async def get_repo_root(self, directory):
        return directory

    async def create(self, cell, repo_root, **kwargs):
        del cell, repo_root, kwargs
        return ""


class EngineerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        install_aiohttp_stub()
        self.server_mod = importlib.import_module("loom.server")
        self.server_mod = importlib.reload(self.server_mod)
        self.server_agent_mod = importlib.import_module("loom.server_agent")
        self.server_agent_mod = importlib.reload(self.server_agent_mod)
        self.state_mod = importlib.import_module("loom.state")
        self.state_mod = importlib.reload(self.state_mod)

    def _make_state(self):
        state = self.state_mod.MatrixState()
        state.add_group("loom")
        return state

    def _launch_config(self, directory: str) -> dict:
        return {
            "profile": "Default",
            "command": "codex",
            "directory": directory,
            "tab_color": "",
            "icon": "",
            "env_vars": {"BASE": "1"},
            "env_file": str(Path(directory) / ".env"),
            "shell": "zsh",
            "system_prompt": "Engineer system prompt",
            "initial_prompt": "",
            "template": "",
            "session_resume": True,
            "idle_timeout": 0,
            "worktree": False,
            "terminals": [],
            "agent_type": "codex",
        }

    def _add_engineer_cell(self, state, engineer_id: str, name: str):
        del engineer_id
        engineer = state.add_agent(
            name=name,
            group="loom",
            terminal_backend="iterm2",
            profile="Default",
            command="codex",
            directory="/tmp/project",
            tab_color="",
        )
        engineer.kind = "engineer"
        engineer.persistent = True
        state._emit_agent(engineer)
        return engineer

    def _add_architect_cell(self, state, architect_id: str, name: str):
        del architect_id
        architect = state.add_agent(
            name=name,
            group="loom",
            terminal_backend="iterm2",
            profile="Default",
            command="codex",
            directory="/tmp/project",
            tab_color="",
        )
        architect.kind = "architect"
        architect.persistent = True
        state._emit_agent(architect)
        return architect

    async def test_add_engineer_creates_persistent_engineer_with_binding_and_engineer_mcp_entrypoint(self):
        state = self._make_state()
        bridge = _CapturingBridge()
        service = self.server_agent_mod.AgentLaunchService(
            state=state,
            connection=None,
            bridge=bridge,
            worktree_mgr=None,
            template_mgr=None,
        )
        sent_prompts = []

        async def fake_resolve_base_dir(group):
            self.assertEqual(group, "loom")
            return temp_dir

        def fake_resolve_weaver_launch_config(group, *, base_dir="",
                                              explicit_template="",
                                              overrides=None):
            self.assertEqual(group, "loom")
            self.assertEqual(base_dir, temp_dir)
            self.assertEqual(explicit_template, "")
            self.assertEqual(
                overrides,
                {"command": "codex --full-auto", "provider": "codex"},
            )
            return self._launch_config(temp_dir)

        async def fake_send_agent_prompt(cell, prompt, **kwargs):
            sent_prompts.append({
                "cell_id": cell.id,
                "prompt": prompt,
                "kwargs": kwargs,
            })

        with tempfile.TemporaryDirectory() as temp_dir:
            result = await self.server_mod._handle_add_engineer_command(
                {
                    "name": "Alice",
                    "command": "codex --full-auto",
                    "provider": "codex",
                },
                state,
                resolve_base_dir=fake_resolve_base_dir,
                resolve_weaver_launch_config=fake_resolve_weaver_launch_config,
                create_agent_with_config=service.create_agent_with_config,
                send_agent_prompt=fake_send_agent_prompt,
            )

        self.assertEqual(result["name"], "Alice")
        self.assertEqual(result["kind"], "engineer")
        engineer = state.agents[result["id"]]
        self.assertEqual(engineer.slug, result["slug"])
        self.assertEqual(engineer.kind, "engineer")
        self.assertTrue(engineer.persistent)
        self.assertTrue(engineer.session_resume)
        self.assertEqual(engineer.owner_engineer_id, "")
        self.assertEqual(engineer.hired_by_architect_id, "")
        self.assertEqual(len(bridge.create_session_calls), 1)
        call = bridge.create_session_calls[0]["kwargs"]
        self.assertEqual(call["env_vars"]["BASE"], "1")
        self.assertEqual(call["env_vars"]["LOOM_ENGINEER_ID"], engineer.id)
        self.assertEqual(
            call["mcp_entrypoint"],
            self.server_agent_mod.ENGINEER_MCP_ENTRYPOINT,
        )
        self.assertEqual(
            self.server_agent_mod.mcp_entrypoint_for_cell(engineer),
            self.server_agent_mod.ENGINEER_MCP_ENTRYPOINT,
        )
        self.assertEqual(len(sent_prompts), 1)
        self.assertEqual(sent_prompts[0]["cell_id"], engineer.id)

    async def test_add_engineer_rejects_duplicate_name(self):
        state = self._make_state()
        self._add_engineer_cell(state, "eng-alice", "Alice")

        async def fake_resolve_base_dir(group):
            del group
            return "/tmp/project"

        def fake_resolve_weaver_launch_config(*args, **kwargs):
            raise AssertionError("should not resolve launch config")

        async def fake_create_agent_with_config(*args, **kwargs):
            raise AssertionError("should not create duplicate engineer")

        async def fake_send_agent_prompt(*args, **kwargs):
            raise AssertionError("should not send prompts")

        result = await self.server_mod._handle_add_engineer_command(
            {"name": "Alice"},
            state,
            resolve_base_dir=fake_resolve_base_dir,
            resolve_weaver_launch_config=fake_resolve_weaver_launch_config,
            create_agent_with_config=fake_create_agent_with_config,
            send_agent_prompt=fake_send_agent_prompt,
        )

        self.assertEqual(result["type"], "error")
        self.assertEqual(len(state.agents), 1)

    async def test_add_architect_creates_persistent_architect_with_binding_and_architect_mcp_entrypoint(self):
        state = self._make_state()
        bridge = _CapturingBridge()
        service = self.server_agent_mod.AgentLaunchService(
            state=state,
            connection=None,
            bridge=bridge,
            worktree_mgr=None,
            template_mgr=None,
        )
        sent_prompts = []

        async def fake_resolve_base_dir(group):
            self.assertEqual(group, "loom")
            return temp_dir

        def fake_resolve_weaver_launch_config(group, *, base_dir="",
                                              explicit_template="",
                                              overrides=None):
            self.assertEqual(group, "loom")
            self.assertEqual(base_dir, temp_dir)
            self.assertEqual(explicit_template, "")
            self.assertEqual(overrides, {"command": "codex --architect"})
            return self._launch_config(temp_dir)

        async def fake_send_agent_prompt(cell, prompt, **kwargs):
            sent_prompts.append({
                "cell_id": cell.id,
                "prompt": prompt,
                "kwargs": kwargs,
            })

        with tempfile.TemporaryDirectory() as temp_dir:
            result = await self.server_mod._handle_add_architect_command(
                {
                    "name": "Productmind",
                    "command": "codex --architect",
                    "group": "loom",
                },
                state,
                resolve_base_dir=fake_resolve_base_dir,
                resolve_weaver_launch_config=fake_resolve_weaver_launch_config,
                create_agent_with_config=service.create_agent_with_config,
                send_agent_prompt=fake_send_agent_prompt,
            )

        self.assertEqual(result["name"], "Productmind")
        self.assertEqual(result["kind"], "architect")
        architect = state.agents[result["id"]]
        self.assertEqual(architect.slug, result["slug"])
        self.assertEqual(architect.kind, "architect")
        self.assertTrue(architect.persistent)
        self.assertEqual(len(bridge.create_session_calls), 1)
        call = bridge.create_session_calls[0]["kwargs"]
        self.assertEqual(call["env_vars"]["BASE"], "1")
        self.assertEqual(call["env_vars"]["LOOM_ARCHITECT_ID"], architect.id)
        self.assertEqual(
            call["mcp_entrypoint"],
            self.server_agent_mod.ARCHITECT_MCP_ENTRYPOINT,
        )
        self.assertEqual(len(sent_prompts), 1)

    async def test_delete_architect_transfers_hired_engineers_and_archives_decisions(self):
        state = self._make_state()
        state.db = self.server_mod.LoomDB(Path(":memory:"))
        state.db.init()
        try:
            architect = self._add_architect_cell(state, "arch-1", "Productmind")
            hired = self._add_engineer_cell(state, "eng-a", "Alice")
            hired.hired_by_architect_id = architect.id
            other = self._add_engineer_cell(state, "eng-b", "Bob")
            saved_decision = state.save_decision({
                "id": "decision-1",
                "architect_id": architect.id,
                "title": "Scope",
                "rationale": "Keep it small",
                "status": "proposed",
            })
            self.assertIsNotNone(saved_decision)
            removed = []

            async def fake_close_agent_session_only(cell):
                removed.append(cell.id)
                return state.remove_agent(cell.id)

            result = await self.server_mod._handle_delete_architect_command(
                {"id": architect.id},
                state,
                close_agent_session_only=fake_close_agent_session_only,
            )

            self.assertEqual(
                result,
                {"transferred_engineers": 1, "archived_decisions": 1},
            )
            self.assertEqual(hired.hired_by_architect_id, "")
            self.assertEqual(other.hired_by_architect_id, "")
            self.assertNotIn(architect.id, state.agents)
            self.assertEqual(removed, [architect.id])
            archived = state.load_decision("decision-1")
            self.assertTrue(archived["archived"])
        finally:
            state.db.close()

    async def test_delete_engineer_transfers_workers_and_tasks(self):
        state = self._make_state()
        engineer = self._add_engineer_cell(state, "eng-alice", "Alice")
        worker = state.add_agent(
            name="Worker",
            group="loom",
            terminal_backend="iterm2",
            profile="Default",
            command="codex",
            directory="/tmp/project",
            tab_color="",
        )
        worker.owner_engineer_id = engineer.id
        worker.created_by_weaver_id = engineer.id
        task = state.board_add_task(
            "Review implementation",
            "loom",
            lane="Backlog",
            id="task-1",
            assigned_engineer_id=engineer.id,
        )
        removed = []

        async def fake_close_agent_session_only(cell):
            removed.append(cell.id)
            return state.remove_agent(cell.id)

        result = await self.server_mod._handle_delete_engineer_command(
            {"id": engineer.id},
            state,
            close_agent_session_only=fake_close_agent_session_only,
        )

        self.assertEqual(result, {"transferred_agents": 1, "transferred_tasks": 1})
        self.assertEqual(worker.owner_engineer_id, "")
        self.assertEqual(worker.created_by_weaver_id, "")
        self.assertEqual(state.board_tasks[task.id].assigned_engineer_id, "")
        self.assertNotIn(engineer.id, state.agents)
        self.assertEqual(removed, [engineer.id])

    async def test_delete_last_engineer_succeeds(self):
        state = self._make_state()
        engineer = self._add_engineer_cell(state, "eng-alice", "Alice")

        async def fake_close_agent_session_only(cell):
            return state.remove_agent(cell.id)

        result = await self.server_mod._handle_delete_engineer_command(
            {"slug": engineer.slug},
            state,
            close_agent_session_only=fake_close_agent_session_only,
        )

        self.assertEqual(result, {"transferred_agents": 0, "transferred_tasks": 0})
        self.assertNotIn(engineer.id, state.agents)

    async def test_rename_engineer_updates_name_slug_and_preserves_kind(self):
        state = self._make_state()
        engineer = self._add_engineer_cell(state, "eng-alice", "Alice")
        engineer.session_id = "session-alice"
        terminal = state.add_terminal(
            name="Shell",
            group="loom",
            terminal_backend="iterm2",
            profile="Default",
            command="",
            directory="/tmp/project",
            tab_color="",
            parent_id=engineer.id,
        )
        updates = []

        async def fake_update_session(cell, old_name):
            updates.append((cell.id, old_name, cell.name))

        result = await self.server_mod._handle_rename_engineer_command(
            {"id": engineer.id, "new_name": "Alice Ops"},
            state,
            update_session=fake_update_session,
        )

        self.assertEqual(result["name"], "Alice Ops")
        self.assertEqual(engineer.name, "Alice Ops")
        self.assertEqual(engineer.kind, "engineer")
        self.assertTrue(engineer.persistent)
        self.assertTrue(terminal.slug.startswith(f"{engineer.slug}:"))
        self.assertEqual(updates, [(engineer.id, "Alice", "Alice Ops")])
        self.assertEqual(
            self.server_agent_mod.mcp_entrypoint_for_cell(engineer),
            self.server_agent_mod.ENGINEER_MCP_ENTRYPOINT,
        )

    async def test_relaunch_deleted_engineer_fails(self):
        state = self._make_state()

        async def fake_resolve_base_dir(group):
            del group
            return "/tmp/project"

        result = await self.server_mod._handle_relaunch_agent_command(
            {"id": "eng-missing"},
            state,
            bridge=_CapturingBridge(),
            worktree_mgr=_FakeWorktreeManager(),
            resolve_base_dir=fake_resolve_base_dir,
            resolve_agent_launch_config=lambda *args, **kwargs: {},
            resolve_weaver_launch_config=lambda *args, **kwargs: {},
            apply_persistent_prompt=lambda *args, **kwargs: None,
            build_cell_persistent_prompt=lambda *args, **kwargs: "",
            persistent_prompt_filename=lambda cell: f"{cell.id}.md",
            is_designated_weaver=lambda cell: False,
        )

        self.assertEqual(result, {"type": "error", "message": "Agent not found"})

    async def test_relaunch_stopped_engineer_preserves_binding_and_engineer_mcp_entrypoint(self):
        state = self._make_state()
        engineer = self._add_engineer_cell(state, "eng-alice", "Alice")
        engineer.status = "stopped"
        engineer.template = "default"
        engineer.agent_type = "codex"
        bridge = _CapturingBridge()
        apply_calls = []

        async def fake_resolve_base_dir(group):
            self.assertEqual(group, "loom")
            return temp_dir

        def fake_resolve_agent_launch_config(*args, **kwargs):
            raise AssertionError("engineer relaunch must use weaver launch config")

        def fake_resolve_weaver_launch_config(group, *, base_dir="",
                                              explicit_template="",
                                              overrides=None):
            self.assertEqual(group, "loom")
            self.assertEqual(base_dir, temp_dir)
            self.assertEqual(explicit_template, "default")
            self.assertEqual(overrides, {})
            return self._launch_config(temp_dir)

        def fake_apply_persistent_prompt(cell, launch_cfg, prompt_text):
            apply_calls.append((cell.id, launch_cfg["command"], prompt_text))

        def fake_build_cell_persistent_prompt(cell, launch_cfg):
            return f"prompt for {cell.id} via {launch_cfg['command']}"

        with tempfile.TemporaryDirectory() as temp_dir:
            engineer.directory = temp_dir
            result = await self.server_mod._handle_relaunch_agent_command(
                {"id": engineer.id},
                state,
                bridge=bridge,
                worktree_mgr=_FakeWorktreeManager(),
                resolve_base_dir=fake_resolve_base_dir,
                resolve_agent_launch_config=fake_resolve_agent_launch_config,
                resolve_weaver_launch_config=fake_resolve_weaver_launch_config,
                apply_persistent_prompt=fake_apply_persistent_prompt,
                build_cell_persistent_prompt=fake_build_cell_persistent_prompt,
                persistent_prompt_filename=lambda cell: f"{cell.id}.md",
                is_designated_weaver=lambda cell: False,
            )

        self.assertIsNone(result)
        self.assertEqual(engineer.kind, "engineer")
        self.assertTrue(engineer.persistent)
        self.assertEqual(len(apply_calls), 1)
        self.assertEqual(len(bridge.create_session_calls), 1)
        call = bridge.create_session_calls[0]["kwargs"]
        self.assertEqual(call["env_vars"]["BASE"], "1")
        self.assertEqual(call["env_vars"]["LOOM_ENGINEER_ID"], engineer.id)
        self.assertEqual(
            call["mcp_entrypoint"],
            self.server_agent_mod.ENGINEER_MCP_ENTRYPOINT,
        )

    async def test_relaunch_stopped_architect_uses_weaver_launch_and_architect_mcp_entrypoint(self):
        state = self._make_state()
        architect = self._add_architect_cell(state, "arch-1", "Loomer")
        architect.status = "stopped"
        architect.agent_type = "codex"
        bridge = _CapturingBridge()

        async def fake_resolve_base_dir(group):
            self.assertEqual(group, "loom")
            return temp_dir

        def fake_resolve_agent_launch_config(*args, **kwargs):
            raise AssertionError(
                "architect relaunch must use weaver launch config")

        def fake_resolve_weaver_launch_config(group, *, base_dir="",
                                              explicit_template="",
                                              overrides=None):
            self.assertEqual(group, "loom")
            self.assertEqual(overrides, {})
            # Weaver launch config sets worktree=False — architects
            # must NOT spawn a worktree on relaunch.
            cfg = self._launch_config(temp_dir)
            cfg["worktree"] = False
            return cfg

        with tempfile.TemporaryDirectory() as temp_dir:
            architect.directory = temp_dir
            result = await self.server_mod._handle_relaunch_agent_command(
                {"id": architect.id},
                state,
                bridge=bridge,
                worktree_mgr=_FakeWorktreeManager(),
                resolve_base_dir=fake_resolve_base_dir,
                resolve_agent_launch_config=fake_resolve_agent_launch_config,
                resolve_weaver_launch_config=fake_resolve_weaver_launch_config,
                apply_persistent_prompt=lambda *a, **k: None,
                build_cell_persistent_prompt=lambda *a, **k: "",
                persistent_prompt_filename=lambda cell: f"{cell.id}.md",
                is_designated_weaver=lambda cell: False,
            )

        self.assertIsNone(result)
        self.assertEqual(architect.kind, "architect")
        self.assertEqual(len(bridge.create_session_calls), 1)
        call = bridge.create_session_calls[0]["kwargs"]
        self.assertEqual(call["env_vars"]["LOOM_ARCHITECT_ID"], architect.id)
        self.assertEqual(
            call["mcp_entrypoint"],
            self.server_agent_mod.ARCHITECT_MCP_ENTRYPOINT,
        )
        # Architect's persisted agent_type must survive re-resolution even
        # when no per-agent overrides make it into launch_cfg.
        self.assertEqual(architect.agent_type, "codex")

    async def test_restart_agent_closes_session_and_resends_startup_sequence(self):
        state = self._make_state()
        engineer = self._add_engineer_cell(state, "eng-alice", "Alice")
        engineer.status = "idle"
        engineer.agent_type = "codex"
        engineer.agent_session_id = "prev-session"
        engineer.tasks_dispatched = 5
        engineer.current_task_id = "LOOM:99"
        engineer.session_id = "active-session"
        closed = []
        sent_prompts = []

        class _Bridge(_CapturingBridge):
            async def close_session(self, session_id):
                closed.append(session_id)

        bridge = _Bridge()

        async def fake_resolve_base_dir(group):
            del group
            return temp_dir

        def fake_resolve_weaver_launch_config(group, *, base_dir="",
                                              explicit_template="",
                                              overrides=None):
            del group, base_dir, explicit_template, overrides
            cfg = self._launch_config(temp_dir)
            cfg["initial_prompt"] = "Engineer: get started on your queue."
            return cfg

        async def fake_send_agent_prompt(cell, prompt, **kwargs):
            sent_prompts.append((cell.id, prompt, kwargs))

        with tempfile.TemporaryDirectory() as temp_dir:
            engineer.directory = temp_dir
            result = await self.server_mod._handle_restart_agent_command(
                {"id": engineer.id},
                state,
                bridge=bridge,
                worktree_mgr=_FakeWorktreeManager(),
                resolve_base_dir=fake_resolve_base_dir,
                resolve_agent_launch_config=lambda *a, **k: {},
                resolve_weaver_launch_config=fake_resolve_weaver_launch_config,
                apply_persistent_prompt=lambda *a, **k: None,
                build_cell_persistent_prompt=lambda *a, **k: "persistent",
                persistent_prompt_filename=lambda cell: f"{cell.id}.md",
                is_designated_weaver=lambda cell: False,
                send_agent_prompt=fake_send_agent_prompt,
            )

        self.assertIsNone(result)
        self.assertEqual(closed, ["active-session"])
        # Session-resume state must be cleared so the new session starts fresh.
        self.assertEqual(engineer.agent_session_id, "")
        self.assertEqual(engineer.tasks_dispatched, 0)
        self.assertEqual(engineer.current_task_id, "")
        # A fresh session was opened with the architect/engineer MCP entrypoint.
        self.assertEqual(len(bridge.create_session_calls), 1)
        call = bridge.create_session_calls[0]["kwargs"]
        self.assertEqual(
            call["mcp_entrypoint"],
            self.server_agent_mod.ENGINEER_MCP_ENTRYPOINT,
        )
        # initial_prompt from launch_cfg is re-delivered.
        prompts = [p for (_, p, _) in sent_prompts]
        self.assertIn("Engineer: get started on your queue.", prompts)

    async def test_restart_agent_rejects_terminals(self):
        state = self._make_state()
        state.add_group("loom")
        terminal = state.add_terminal(
            name="Term",
            group="loom",
            terminal_backend="iterm2",
            profile="Default",
            command="",
            directory="/tmp",
            tab_color="",
        )

        async def fake_resolve_base_dir(group):
            del group
            return "/tmp"

        async def noop(*a, **k):
            pass

        result = await self.server_mod._handle_restart_agent_command(
            {"id": terminal.id},
            state,
            bridge=_CapturingBridge(),
            worktree_mgr=_FakeWorktreeManager(),
            resolve_base_dir=fake_resolve_base_dir,
            resolve_agent_launch_config=lambda *a, **k: {},
            resolve_weaver_launch_config=lambda *a, **k: {},
            apply_persistent_prompt=lambda *a, **k: None,
            build_cell_persistent_prompt=lambda *a, **k: "",
            persistent_prompt_filename=lambda cell: f"{cell.id}.md",
            is_designated_weaver=lambda cell: False,
            send_agent_prompt=noop,
        )

        self.assertEqual(result.get("type"), "error")

    async def test_relaunch_preserves_cell_agent_type_when_launch_cfg_empty(self):
        state = self._make_state()
        engineer = self._add_engineer_cell(state, "eng-alice", "Alice")
        engineer.status = "stopped"
        engineer.agent_type = "codex"
        engineer.command = "codex --custom"
        bridge = _CapturingBridge()

        async def fake_resolve_base_dir(group):
            del group
            return temp_dir

        def fake_resolve_weaver_launch_config(group, *, base_dir="",
                                              explicit_template="",
                                              overrides=None):
            del group, base_dir, explicit_template, overrides
            # Simulate the bug trigger: group-level weaver settings can't
            # describe this engineer's specific provider, so the resolver
            # returns an empty agent_type/command.  These must NOT clobber
            # the cell's persisted values.
            return {
                "profile": "Default",
                "command": "",
                "directory": temp_dir,
                "tab_color": "",
                "icon": "",
                "env_vars": {},
                "env_file": "",
                "shell": "",
                "system_prompt": "",
                "initial_prompt": "",
                "template": "",
                "session_resume": True,
                "idle_timeout": 0,
                "worktree": False,
                "terminals": [],
                "agent_type": "",
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            engineer.directory = temp_dir
            await self.server_mod._handle_relaunch_agent_command(
                {"id": engineer.id},
                state,
                bridge=bridge,
                worktree_mgr=_FakeWorktreeManager(),
                resolve_base_dir=fake_resolve_base_dir,
                resolve_agent_launch_config=lambda *a, **k: {},
                resolve_weaver_launch_config=fake_resolve_weaver_launch_config,
                apply_persistent_prompt=lambda *a, **k: None,
                build_cell_persistent_prompt=lambda *a, **k: "",
                persistent_prompt_filename=lambda cell: f"{cell.id}.md",
                is_designated_weaver=lambda cell: False,
            )

        self.assertEqual(engineer.agent_type, "codex")
        self.assertEqual(engineer.command, "codex --custom")
        # Directory should also be preserved, not wiped to the launch_cfg
        # value when it's empty — but here launch_cfg provides temp_dir
        # so the assertion is that the engineer's directory matches.
        self.assertEqual(engineer.directory, temp_dir)
