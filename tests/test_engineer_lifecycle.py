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
        self.closed_sessions = []
        self.sent_text = []

    async def create_session(self, cell, **kwargs):
        cell.session_id = kwargs.get("session_id", "session-new")
        self.create_session_calls.append({
            "cell": cell,
            "kwargs": kwargs,
        })

    async def close_session(self, session_id):
        self.closed_sessions.append(session_id)

    async def send_text(self, session_id, text):
        self.sent_text.append((session_id, text))

    def prime_input_ready(self, session_id):
        del session_id


class _FakeWorktreeManager:
    def __init__(self, repo_root="", worktree_path=""):
        self.repo_root = repo_root
        self.worktree_path = worktree_path
        self.create_calls = 0

    async def validate(self, cell):
        del cell
        return True

    async def get_repo_root(self, directory):
        return self.repo_root or directory

    async def create(self, cell, repo_root, **kwargs):
        del kwargs
        self.create_calls += 1
        if self.worktree_path:
            cell.worktree_path = self.worktree_path
            cell.worktree_branch = f"loom/{cell.slug}-abc123"
            cell.worktree_repo_root = repo_root
            return self.worktree_path
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

    def _add_worker_cell(self, state, engineer, name: str = "Worker"):
        worker = state.add_agent(
            name=name,
            group="loom",
            terminal_backend="iterm2",
            profile="Default",
            command="codex",
            directory="/tmp/project",
            tab_color="",
        )
        worker.kind = "worker"
        worker.owner_engineer_id = engineer.id
        worker.created_by_engineer_id = engineer.id
        state._emit_agent(worker)
        return worker

    def test_injected_engineer_to_architect_prompt_respects_ack_required(self):
        status_prompt = self.server_mod._format_injected_mcp_message_prompt(
            message="Status: going quiet.",
            sender_name="Alice",
            sender_kind="engineer",
            recipient_kind="architect",
            message_id="msg-status",
        )
        self.assertIn("## Message from Alice (engineer)", status_prompt)
        self.assertIn("Status: going quiet.", status_prompt)
        self.assertNotIn("Reply with:", status_prompt)
        self.assertNotIn("mcp__loom__architect_reply", status_prompt)

        question_prompt = self.server_mod._format_injected_mcp_message_prompt(
            message="Should I cut scope?",
            sender_name="Alice",
            sender_kind="engineer",
            recipient_kind="architect",
            message_id="msg-question",
            ack_required=True,
        )
        self.assertIn(
            'Reply with: mcp__loom__architect_reply(message_id="msg-question"',
            question_prompt,
        )

        engineer_prompt = self.server_mod._format_injected_mcp_message_prompt(
            message="Please confirm.",
            sender_name="Productmind",
            sender_kind="architect",
            recipient_kind="engineer",
            message_id="msg-architect",
        )
        self.assertIn(
            'Reply with: mcp__loom__engineer_reply(message_id="msg-architect"',
            engineer_prompt,
        )

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

        def fake_resolve_engineer_launch_config(group, *, base_dir="",
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
                resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
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
        # LOOM:263 — empty role initial_prompt + engineer kind synthesizes
        # the configured default boot nudge so the wake protocol fires.
        # The codex startup_prompt is the persistent system prompt, then the
        # default nudge follows.
        self.assertEqual(len(sent_prompts), 2)
        self.assertEqual(sent_prompts[0]["cell_id"], engineer.id)
        self.assertEqual(sent_prompts[1]["cell_id"], engineer.id)
        self.assertIn(
            state.global_settings.engineer_default_boot_nudge,
            sent_prompts[1]["prompt"],
        )

    async def test_add_engineer_rejects_duplicate_name(self):
        state = self._make_state()
        self._add_engineer_cell(state, "eng-alice", "Alice")

        async def fake_resolve_base_dir(group):
            del group
            return "/tmp/project"

        def fake_resolve_engineer_launch_config(*args, **kwargs):
            raise AssertionError("should not resolve launch config")

        async def fake_create_agent_with_config(*args, **kwargs):
            raise AssertionError("should not create duplicate engineer")

        async def fake_send_agent_prompt(*args, **kwargs):
            raise AssertionError("should not send prompts")

        result = await self.server_mod._handle_add_engineer_command(
            {"name": "Alice"},
            state,
            resolve_base_dir=fake_resolve_base_dir,
            resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
            create_agent_with_config=fake_create_agent_with_config,
            send_agent_prompt=fake_send_agent_prompt,
        )

        self.assertEqual(result["type"], "error")
        self.assertEqual(len(state.agents), 1)

    async def test_add_worker_creates_user_owned_detached_worker(self):
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

        def fake_resolve_agent_launch_config(group, *, base_dir="",
                                             explicit_template="",
                                             overrides=None):
            self.assertEqual(group, "loom")
            self.assertEqual(base_dir, temp_dir)
            self.assertEqual(explicit_template, "worker/reviewer")
            self.assertEqual(
                overrides,
                {
                    "command": "codex --worker",
                    "provider": "codex",
                    "template": "worker/reviewer",
                },
            )
            cfg = self._launch_config(temp_dir)
            cfg["initial_prompt"] = "hello worker"
            return cfg

        async def fake_send_agent_prompt(cell, prompt, **kwargs):
            sent_prompts.append({
                "cell_id": cell.id,
                "prompt": prompt,
                "kwargs": kwargs,
            })

        with tempfile.TemporaryDirectory() as temp_dir:
            result = await self.server_mod._handle_add_worker_command(
                {
                    "name": "Detached Worker",
                    "group": "loom",
                    "command": "codex --worker",
                    "provider": "codex",
                    "template": "worker/reviewer",
                },
                state,
                resolve_base_dir=fake_resolve_base_dir,
                resolve_agent_launch_config=fake_resolve_agent_launch_config,
                create_agent_with_config=service.create_agent_with_config,
                send_agent_prompt=fake_send_agent_prompt,
            )

        self.assertEqual(result["name"], "Detached Worker")
        self.assertEqual(result["kind"], "worker")
        worker = state.agents[result["id"]]
        self.assertEqual(worker.kind, "worker")
        self.assertFalse(worker.persistent)
        self.assertEqual(worker.owner_engineer_id, "")
        self.assertEqual(worker.created_by_engineer_id, "")
        self.assertEqual(worker.hired_by_architect_id, "")
        self.assertEqual(worker.template, "worker/reviewer")
        self.assertEqual(len(bridge.create_session_calls), 1)
        call = bridge.create_session_calls[0]["kwargs"]
        self.assertEqual(call["env_vars"]["BASE"], "1")
        self.assertNotIn("LOOM_ENGINEER_ID", call["env_vars"])
        self.assertNotIn("LOOM_ARCHITECT_ID", call["env_vars"])
        self.assertEqual(
            call["mcp_entrypoint"],
            self.server_agent_mod.DEFAULT_MCP_ENTRYPOINT,
        )
        self.assertEqual(len(sent_prompts), 1)
        self.assertEqual(sent_prompts[0]["cell_id"], worker.id)
        self.assertEqual(sent_prompts[0]["prompt"], "hello worker")

    async def test_add_worker_rejects_user_supplied_owner(self):
        state = self._make_state()

        async def fake_resolve_base_dir(group):
            raise AssertionError("should not resolve launch config")

        def fake_resolve_agent_launch_config(*args, **kwargs):
            raise AssertionError("should not resolve launch config")

        async def fake_create_agent_with_config(*args, **kwargs):
            raise AssertionError("should not create worker with user owner")

        async def fake_send_agent_prompt(*args, **kwargs):
            raise AssertionError("should not send prompts")

        result = await self.server_mod._handle_add_worker_command(
            {
                "name": "Bad Worker",
                "group": "loom",
                "owner_engineer_id": "eng-alice",
            },
            state,
            resolve_base_dir=fake_resolve_base_dir,
            resolve_agent_launch_config=fake_resolve_agent_launch_config,
            create_agent_with_config=fake_create_agent_with_config,
            send_agent_prompt=fake_send_agent_prompt,
        )

        self.assertEqual(result["type"], "error")
        self.assertIn("owner_engineer_id", result["message"])
        self.assertEqual(len(state.agents), 0)

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

        def fake_resolve_engineer_launch_config(group, *, base_dir="",
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
                resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
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
        # LOOM:263 — empty role initial_prompt + architect kind synthesizes
        # the configured default boot nudge so the wake protocol fires.
        self.assertEqual(len(sent_prompts), 2)
        self.assertIn(
            state.global_settings.architect_default_boot_nudge,
            sent_prompts[1]["prompt"],
        )

    async def test_architect_persistent_prompt_injects_custom_instructions(self):
        state = self._make_state()
        state.update_architect_settings(
            "loom",
            architect_custom_instructions="Prefer reversible scope cuts.",
        )

        prompt = self.server_mod._architect_persistent_prompt_text(
            group="loom",
            action_system_prompt="Action-specific context.",
            state=state,
        )

        self.assertIn("Action-specific context.", prompt)
        self.assertIn("## Custom Instructions", prompt)
        self.assertIn("Prefer reversible scope cuts.", prompt)

    async def test_add_architect_worktree_default_and_knob(self):
        async def spawn(*, knob=False, launch_worktree=False, group_worktree=False):
            state = self._make_state()
            if group_worktree:
                state.update_group_settings("loom", git_worktree=True)
            repo_root = "/repo"
            subdir = "/repo/packages/app"
            worktree_path = "/repo/.loom/worktrees/architect"
            worktree_mgr = _FakeWorktreeManager(repo_root, worktree_path)
            service = self.server_agent_mod.AgentLaunchService(
                state=state,
                connection=None,
                bridge=_CapturingBridge(),
                worktree_mgr=worktree_mgr,
                template_mgr=None,
            )

            async def fake_resolve_base_dir(group):
                del group
                return repo_root

            async def fake_send_agent_prompt(*args, **kwargs):
                del args, kwargs

            def fake_resolve_engineer_launch_config(*args, **kwargs):
                del args, kwargs
                cfg = self._launch_config(subdir)
                cfg["worktree"] = launch_worktree
                return cfg

            try:
                self.server_mod.loom_config.ARCHITECT_USES_WORKTREE = knob
                result = await self.server_mod._handle_add_architect_command(
                    {"name": "Productmind", "group": "loom"},
                    state,
                    resolve_base_dir=fake_resolve_base_dir,
                    resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
                    create_agent_with_config=service.create_agent_with_config,
                    send_agent_prompt=fake_send_agent_prompt,
                )
                return state.agents[result["id"]], worktree_mgr
            finally:
                self.server_mod.loom_config.ARCHITECT_USES_WORKTREE = False

        architect, worktree_mgr = await spawn(launch_worktree=True)
        self.assertEqual(architect.directory, worktree_mgr.repo_root)
        self.assertEqual(architect.worktree_path, "")
        self.assertEqual(worktree_mgr.create_calls, 0)

        architect, worktree_mgr = await spawn(knob=True, group_worktree=True)
        self.assertEqual(architect.directory, worktree_mgr.worktree_path)
        self.assertEqual(architect.worktree_path, worktree_mgr.worktree_path)
        self.assertEqual(worktree_mgr.create_calls, 1)

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
            self.assertIn(architect.id, state.agents)
            self.assertTrue(state.agent_is_tombstoned(architect))
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
        worker.created_by_engineer_id = engineer.id
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
        self.assertEqual(worker.created_by_engineer_id, "")
        self.assertEqual(state.board_tasks[task.id].assigned_engineer_id, "")
        self.assertIn(engineer.id, state.agents)
        self.assertTrue(state.agent_is_tombstoned(engineer))
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
        self.assertIn(engineer.id, state.agents)
        self.assertTrue(state.agent_is_tombstoned(engineer))

    async def test_restore_and_purge_agent_now_commands(self):
        state = self._make_state()
        engineer = self._add_engineer_cell(state, "eng-alice", "Alice")
        state.remove_agent(engineer.id)
        self.assertTrue(state.agent_is_tombstoned(engineer))

        restored = self.server_mod._handle_restore_agent_command(
            {"id": engineer.id},
            state,
        )
        self.assertEqual(restored, {"type": "ok", "restored": [engineer.id]})
        self.assertFalse(state.agent_is_tombstoned(engineer))

        state.remove_agent(engineer.id)
        cleaned = []

        async def cleanup(removed):
            cleaned.extend(c.id for c in removed)

        purged = await self.server_mod._handle_purge_agent_now_command(
            {"id": engineer.id},
            state,
            cleanup_purged_agents=cleanup,
        )
        self.assertEqual(purged, {"type": "ok", "purged": [engineer.id]})
        self.assertEqual(cleaned, [engineer.id])
        self.assertNotIn(engineer.id, state.agents)

    async def test_remove_agent_direct_terminal_hard_deletes_and_cleans_up(self):
        state = self._make_state()
        terminal = state.add_terminal(
            name="Logs",
            group="loom",
            terminal_backend="iterm2",
            profile="Default",
            command="",
            directory="/tmp/project",
        )
        terminal.session_id = "terminal-session"
        cleaned = []

        async def close_agent_session_only(_cell):
            self.fail("direct terminal removal must not use tombstone close path")

        async def cleanup_purged_agents(removed):
            cleaned.extend(c.id for c in removed)

        result = await self.server_mod._handle_remove_agent_command(
            {"id": terminal.id},
            state,
            close_agent_session_only=close_agent_session_only,
            cleanup_purged_agents=cleanup_purged_agents,
        )

        self.assertEqual(result, {"type": "ok", "removed": [terminal.id]})
        self.assertEqual(cleaned, [terminal.id])
        self.assertNotIn(terminal.id, state.agents)
        self.assertNotIn(terminal.id, state.groups["loom"])

    async def test_dismiss_engineer_closes_engineer_and_owned_workers_preserves_assignments(self):
        state = self._make_state()
        architect = self._add_architect_cell(state, "arch-1", "Productmind")
        other_architect = self._add_architect_cell(state, "arch-2", "Other")
        engineer = self._add_engineer_cell(state, "eng-alice", "Alice")
        engineer.hired_by_architect_id = architect.id
        engineer.session_id = "session-engineer"
        worker = self._add_worker_cell(state, engineer)
        worker.session_id = "session-worker"
        other_engineer = self._add_engineer_cell(state, "eng-bob", "Bob")
        other_worker = self._add_worker_cell(state, other_engineer, "Other Worker")
        other_worker.session_id = "session-other-worker"
        task = state.board_add_task(
            "Keep assignment",
            "loom",
            id="task-keep-assignment",
            assigned_engineer_id=engineer.id,
        )
        bridge = _CapturingBridge()
        panel_events = []

        denied = await self.server_mod._handle_engineer_dismiss_command(
            {"engineer_id": engineer.id, "architect_id": other_architect.id},
            state,
            close_session=bridge.close_session,
        )
        self.assertEqual(denied["type"], "error")
        self.assertEqual(engineer.dismissed_at, 0)

        result = await self.server_mod._handle_engineer_dismiss_command(
            {
                "engineer_id": engineer.id,
                "architect_id": architect.id,
                "reason": "pause",
            },
            state,
            close_session=bridge.close_session,
            panel_event=lambda *args, **kwargs: panel_events.append(args),
        )

        self.assertEqual(result["type"], "ok")
        self.assertGreater(engineer.dismissed_at, 0)
        self.assertIn(engineer.id, state.agents)
        self.assertIn(worker.id, state.agents)
        self.assertEqual(state.board_tasks[task.id].assigned_engineer_id, engineer.id)
        self.assertEqual(engineer.status, "stopped")
        self.assertEqual(worker.status, "stopped")
        self.assertEqual(other_worker.session_id, "session-other-worker")
        self.assertEqual(
            bridge.closed_sessions,
            ["session-engineer", "session-worker"],
        )
        self.assertEqual(panel_events[0][0], "engineer_dismissed")

        second = await self.server_mod._handle_engineer_dismiss_command(
            {"engineer_id": engineer.id, "architect_id": architect.id},
            state,
            close_session=bridge.close_session,
        )
        self.assertTrue(second["already_dismissed"])
        self.assertEqual(second["closed_sessions"], 0)

    async def test_rehire_restores_engineer_replays_messages_and_does_not_reopen_workers(self):
        state = self._make_state()
        architect = self._add_architect_cell(state, "arch-1", "Productmind")
        engineer = self._add_engineer_cell(state, "eng-alice", "Alice")
        engineer.hired_by_architect_id = architect.id
        engineer.status = "stopped"
        engineer.dismissed_at = 123
        engineer.agent_type = "codex"
        identity_anchor = f"You are Alice (engineer, id={engineer.id})."
        engineer.mcp_messages.insert(0, {
            "id": "msg-buffered",
            "thread_id": "msg-buffered",
            "action": "architect_message",
            "message": identity_anchor + "\n\nResume with this context.",
            "timestamp": 1.0,
            "sender_id": architect.id,
            "sender_kind": "architect",
            "peer_id": architect.id,
            "peer_kind": "architect",
            "direction": "received",
            "delivered": False,
            "buffered": True,
        })
        worker = self._add_worker_cell(state, engineer)
        worker.status = "stopped"
        worker.session_id = None
        bridge = _CapturingBridge()
        panel_events = []

        async def fake_resolve_base_dir(group):
            self.assertEqual(group, "loom")
            return temp_dir

        def fake_resolve_engineer_launch_config(group, *, base_dir="",
                                              explicit_template="",
                                              overrides=None):
            self.assertEqual(group, "loom")
            self.assertEqual(overrides, {})
            return self._launch_config(base_dir)

        with tempfile.TemporaryDirectory() as temp_dir:
            engineer.directory = temp_dir
            result = await self.server_mod._handle_engineer_rehire_command(
                {"engineer_id": engineer.id, "architect_id": architect.id},
                state,
                bridge=bridge,
                worktree_mgr=_FakeWorktreeManager(),
                resolve_base_dir=fake_resolve_base_dir,
                resolve_agent_launch_config=lambda *a, **k: {},
                resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
                apply_persistent_prompt=lambda *a, **k: None,
                build_cell_persistent_prompt=lambda *a, **k: "persistent",
                persistent_prompt_filename=lambda cell: f"{cell.id}.md",
                is_designated_engineer=lambda cell: False,
                panel_event=lambda *args, **kwargs: panel_events.append(args),
            )

        self.assertEqual(result["type"], "ok")
        self.assertEqual(engineer.dismissed_at, 0)
        self.assertEqual(engineer.session_id, "session-new")
        self.assertIsNone(worker.session_id)
        self.assertEqual(len(bridge.create_session_calls), 1)
        self.assertEqual(result["replayed_messages"], 1)
        self.assertEqual(len(bridge.sent_text), 1)
        replayed_prompt = bridge.sent_text[0][1]
        self.assertTrue(replayed_prompt.startswith(identity_anchor))
        self.assertIn("## Message from Productmind (architect)", replayed_prompt)
        self.assertIn("Resume with this context.", replayed_prompt)
        self.assertEqual(replayed_prompt.count(identity_anchor), 1)
        self.assertTrue(engineer.mcp_messages[0]["delivered"])
        self.assertEqual(panel_events[0][0], "engineer_rehired")

        second = await self.server_mod._handle_engineer_rehire_command(
            {"engineer_id": engineer.id, "architect_id": architect.id},
            state,
            bridge=bridge,
            worktree_mgr=_FakeWorktreeManager(),
            resolve_base_dir=lambda group: temp_dir,
            resolve_agent_launch_config=lambda *a, **k: {},
            resolve_engineer_launch_config=lambda *a, **k: {},
            apply_persistent_prompt=lambda *a, **k: None,
            build_cell_persistent_prompt=lambda *a, **k: "",
            persistent_prompt_filename=lambda cell: f"{cell.id}.md",
            is_designated_engineer=lambda cell: False,
        )
        self.assertTrue(second["already_hired"])

    async def test_rehire_boot_failure_keeps_dismissed_at_set(self):
        state = self._make_state()
        architect = self._add_architect_cell(state, "arch-1", "Productmind")
        engineer = self._add_engineer_cell(state, "eng-alice", "Alice")
        engineer.hired_by_architect_id = architect.id
        engineer.status = "stopped"
        engineer.dismissed_at = 456
        engineer.agent_type = "codex"

        class _FailingBridge(_CapturingBridge):
            async def create_session(self, cell, **kwargs):
                del cell, kwargs
                raise RuntimeError("provider unavailable")

        async def fake_resolve_base_dir(group):
            del group
            return temp_dir

        with tempfile.TemporaryDirectory() as temp_dir:
            engineer.directory = temp_dir
            result = await self.server_mod._handle_engineer_rehire_command(
                {"engineer_id": engineer.id, "architect_id": architect.id},
                state,
                bridge=_FailingBridge(),
                worktree_mgr=_FakeWorktreeManager(),
                resolve_base_dir=fake_resolve_base_dir,
                resolve_agent_launch_config=lambda *a, **k: {},
                resolve_engineer_launch_config=lambda *a, **k: self._launch_config(temp_dir),
                apply_persistent_prompt=lambda *a, **k: None,
                build_cell_persistent_prompt=lambda *a, **k: "persistent",
                persistent_prompt_filename=lambda cell: f"{cell.id}.md",
                is_designated_engineer=lambda cell: False,
            )

        self.assertEqual(result["type"], "error")
        self.assertEqual(engineer.dismissed_at, 456)
        self.assertEqual(engineer.status, "stopped")
        self.assertIsNone(engineer.session_id)

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
            resolve_engineer_launch_config=lambda *args, **kwargs: {},
            apply_persistent_prompt=lambda *args, **kwargs: None,
            build_cell_persistent_prompt=lambda *args, **kwargs: "",
            persistent_prompt_filename=lambda cell: f"{cell.id}.md",
            is_designated_engineer=lambda cell: False,
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
            raise AssertionError("engineer relaunch must use engineer launch config")

        def fake_resolve_engineer_launch_config(group, *, base_dir="",
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
                resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
                apply_persistent_prompt=fake_apply_persistent_prompt,
                build_cell_persistent_prompt=fake_build_cell_persistent_prompt,
                persistent_prompt_filename=lambda cell: f"{cell.id}.md",
                is_designated_engineer=lambda cell: False,
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

    async def test_relaunch_stopped_architect_uses_engineer_launch_and_architect_mcp_entrypoint(self):
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
                "architect relaunch must use engineer launch config")

        def fake_resolve_engineer_launch_config(group, *, base_dir="",
                                              explicit_template="",
                                              overrides=None):
            self.assertEqual(group, "loom")
            self.assertEqual(overrides, {})
            # Engineer launch config sets worktree=False — architects
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
                resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
                apply_persistent_prompt=lambda *a, **k: None,
                build_cell_persistent_prompt=lambda *a, **k: "",
                persistent_prompt_filename=lambda cell: f"{cell.id}.md",
                is_designated_engineer=lambda cell: False,
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

        def fake_resolve_engineer_launch_config(group, *, base_dir="",
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
                resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
                apply_persistent_prompt=lambda *a, **k: None,
                build_cell_persistent_prompt=lambda *a, **k: "persistent",
                persistent_prompt_filename=lambda cell: f"{cell.id}.md",
                is_designated_engineer=lambda cell: False,
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
        self.assertIn(
            f"You are Alice (engineer, id={engineer.id}).\n\n"
            "Engineer: get started on your queue.",
            prompts,
        )

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
            resolve_engineer_launch_config=lambda *a, **k: {},
            apply_persistent_prompt=lambda *a, **k: None,
            build_cell_persistent_prompt=lambda *a, **k: "",
            persistent_prompt_filename=lambda cell: f"{cell.id}.md",
            is_designated_engineer=lambda cell: False,
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

        def fake_resolve_engineer_launch_config(group, *, base_dir="",
                                              explicit_template="",
                                              overrides=None):
            del group, base_dir, explicit_template, overrides
            # Simulate the bug trigger: group-level engineer settings can't
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
                resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
                apply_persistent_prompt=lambda *a, **k: None,
                build_cell_persistent_prompt=lambda *a, **k: "",
                persistent_prompt_filename=lambda cell: f"{cell.id}.md",
                is_designated_engineer=lambda cell: False,
            )

        self.assertEqual(engineer.agent_type, "codex")
        self.assertEqual(engineer.command, "codex --custom")
        # Directory should also be preserved, not wiped to the launch_cfg
        # value when it's empty — but here launch_cfg provides temp_dir
        # so the assertion is that the engineer's directory matches.
        self.assertEqual(engineer.directory, temp_dir)

    async def test_relaunch_fresh_session_codex_engineer_fires_kickoff_sequence(self):
        """Codex relaunch into a fresh session must re-seat the persistent
        system prompt as the first chat turn — the codex CLI has no
        --append-system-prompt-file equivalent, so skipping the kickoff
        leaves the engineer with no system prompt at all."""
        state = self._make_state()
        engineer = self._add_engineer_cell(state, "eng-alice", "Alice")
        engineer.status = "stopped"
        engineer.agent_type = "codex"
        # Fresh session: no prior provider session id to resume into.
        engineer.agent_session_id = ""
        bridge = _CapturingBridge()
        sent_prompts = []

        async def fake_resolve_base_dir(group):
            del group
            return temp_dir

        def fake_resolve_engineer_launch_config(group, *, base_dir="",
                                              explicit_template="",
                                              overrides=None):
            del group, base_dir, explicit_template, overrides
            cfg = self._launch_config(temp_dir)
            cfg["initial_prompt"] = "Engineer: get started on your queue."
            return cfg

        def fake_build_cell_persistent_prompt(cell, launch_cfg):
            del launch_cfg
            return f"You are the engineer for {cell.group}."

        async def fake_send_agent_prompt(cell, prompt, **kwargs):
            sent_prompts.append((cell.id, prompt, kwargs))

        with tempfile.TemporaryDirectory() as temp_dir:
            engineer.directory = temp_dir
            result = await self.server_mod._handle_relaunch_agent_command(
                {"id": engineer.id},
                state,
                bridge=bridge,
                worktree_mgr=_FakeWorktreeManager(),
                resolve_base_dir=fake_resolve_base_dir,
                resolve_agent_launch_config=lambda *a, **k: {},
                resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
                apply_persistent_prompt=lambda *a, **k: None,
                build_cell_persistent_prompt=fake_build_cell_persistent_prompt,
                persistent_prompt_filename=lambda cell: f"{cell.id}.md",
                is_designated_engineer=lambda cell: False,
                send_agent_prompt=fake_send_agent_prompt,
            )

        self.assertIsNone(result)
        prompts = [p for (_, p, _) in sent_prompts]
        # codex returns the persistent prompt as the first interactive turn.
        self.assertTrue(
            any(f"You are the engineer for {engineer.group}." in p
                for p in prompts),
            f"codex persistent prompt missing from sent prompts: {prompts}",
        )
        # Role-defined initial_prompt fires too.
        self.assertTrue(
            any("Engineer: get started on your queue." in p for p in prompts),
            f"initial_prompt missing from sent prompts: {prompts}",
        )

    async def test_relaunch_fresh_session_claude_code_engineer_fires_initial_prompt_only(self):
        """claude-code relaunch into a fresh session must deliver the role
        initial_prompt (kickoff text) but NOT the persistent prompt as a
        chat turn — claude-code seats the persistent prompt via
        --append-system-prompt-file, so re-sending it would duplicate it
        verbatim into the conversation."""
        state = self._make_state()
        engineer = self._add_engineer_cell(state, "eng-bob", "Bob")
        engineer.status = "stopped"
        engineer.agent_type = "claude-code"
        engineer.agent_session_id = ""
        bridge = _CapturingBridge()
        sent_prompts = []

        async def fake_resolve_base_dir(group):
            del group
            return temp_dir

        def fake_resolve_engineer_launch_config(group, *, base_dir="",
                                              explicit_template="",
                                              overrides=None):
            del group, base_dir, explicit_template, overrides
            cfg = self._launch_config(temp_dir)
            cfg["agent_type"] = "claude-code"
            cfg["initial_prompt"] = "Read engineer_journal_read first."
            return cfg

        def fake_build_cell_persistent_prompt(cell, launch_cfg):
            del cell, launch_cfg
            return "Persistent system prompt body (file-injected)."

        async def fake_send_agent_prompt(cell, prompt, **kwargs):
            sent_prompts.append((cell.id, prompt, kwargs))

        with tempfile.TemporaryDirectory() as temp_dir:
            engineer.directory = temp_dir
            await self.server_mod._handle_relaunch_agent_command(
                {"id": engineer.id},
                state,
                bridge=bridge,
                worktree_mgr=_FakeWorktreeManager(),
                resolve_base_dir=fake_resolve_base_dir,
                resolve_agent_launch_config=lambda *a, **k: {},
                resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
                apply_persistent_prompt=lambda *a, **k: None,
                build_cell_persistent_prompt=fake_build_cell_persistent_prompt,
                persistent_prompt_filename=lambda cell: f"{cell.id}.md",
                is_designated_engineer=lambda cell: False,
                send_agent_prompt=fake_send_agent_prompt,
            )

        prompts = [p for (_, p, _) in sent_prompts]
        # claude-code's persistent prompt must NOT be re-delivered as a
        # chat turn — it ships via --append-system-prompt-file.
        self.assertFalse(
            any("Persistent system prompt body" in p for p in prompts),
            f"claude-code persistent prompt leaked into chat: {prompts}",
        )
        # initial_prompt still fires.
        self.assertTrue(
            any("Read engineer_journal_read first." in p for p in prompts),
            f"claude-code initial_prompt missing from sent prompts: {prompts}",
        )

    async def test_relaunch_resume_viable_skips_kickoff(self):
        """When session_resume is true AND a prior agent_session_id exists,
        the new launch will resume that conversation. The kickoff sequence
        must be skipped to avoid duplicating the system prompt onto the
        resumed turn."""
        state = self._make_state()
        engineer = self._add_engineer_cell(state, "eng-alice", "Alice")
        engineer.status = "stopped"
        engineer.agent_type = "codex"
        # Resume-viable: prior provider session id present, resume enabled.
        engineer.agent_session_id = "prior-session"
        bridge = _CapturingBridge()
        sent_prompts = []

        async def fake_resolve_base_dir(group):
            del group
            return temp_dir

        def fake_resolve_engineer_launch_config(group, *, base_dir="",
                                              explicit_template="",
                                              overrides=None):
            del group, base_dir, explicit_template, overrides
            cfg = self._launch_config(temp_dir)
            cfg["initial_prompt"] = "Should not fire on resume."
            cfg["session_resume"] = True
            return cfg

        async def fake_send_agent_prompt(cell, prompt, **kwargs):
            sent_prompts.append((cell.id, prompt, kwargs))

        with tempfile.TemporaryDirectory() as temp_dir:
            engineer.directory = temp_dir
            await self.server_mod._handle_relaunch_agent_command(
                {"id": engineer.id},
                state,
                bridge=bridge,
                worktree_mgr=_FakeWorktreeManager(),
                resolve_base_dir=fake_resolve_base_dir,
                resolve_agent_launch_config=lambda *a, **k: {},
                resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
                apply_persistent_prompt=lambda *a, **k: None,
                build_cell_persistent_prompt=lambda *a, **k: "system prompt",
                persistent_prompt_filename=lambda cell: f"{cell.id}.md",
                is_designated_engineer=lambda cell: False,
                send_agent_prompt=fake_send_agent_prompt,
            )

        self.assertEqual(
            sent_prompts, [],
            f"resume-viable relaunch must skip kickoff but sent: {sent_prompts}",
        )

    async def test_relaunch_session_resume_disabled_fires_kickoff_even_with_session_id(self):
        """When session_resume is explicitly false, the new launch starts a
        fresh provider conversation regardless of any prior agent_session_id.
        Kickoff must fire."""
        state = self._make_state()
        engineer = self._add_engineer_cell(state, "eng-alice", "Alice")
        engineer.status = "stopped"
        engineer.agent_type = "codex"
        # Prior session id exists, but resume is disabled — fresh conversation.
        engineer.agent_session_id = "stale-but-irrelevant"
        bridge = _CapturingBridge()
        sent_prompts = []

        async def fake_resolve_base_dir(group):
            del group
            return temp_dir

        def fake_resolve_engineer_launch_config(group, *, base_dir="",
                                              explicit_template="",
                                              overrides=None):
            del group, base_dir, explicit_template, overrides
            cfg = self._launch_config(temp_dir)
            cfg["initial_prompt"] = "Re-seat the engineer."
            cfg["session_resume"] = False
            return cfg

        async def fake_send_agent_prompt(cell, prompt, **kwargs):
            sent_prompts.append((cell.id, prompt, kwargs))

        with tempfile.TemporaryDirectory() as temp_dir:
            engineer.directory = temp_dir
            await self.server_mod._handle_relaunch_agent_command(
                {"id": engineer.id},
                state,
                bridge=bridge,
                worktree_mgr=_FakeWorktreeManager(),
                resolve_base_dir=fake_resolve_base_dir,
                resolve_agent_launch_config=lambda *a, **k: {},
                resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
                apply_persistent_prompt=lambda *a, **k: None,
                build_cell_persistent_prompt=lambda *a, **k: "engineer system",
                persistent_prompt_filename=lambda cell: f"{cell.id}.md",
                is_designated_engineer=lambda cell: False,
                send_agent_prompt=fake_send_agent_prompt,
            )

        prompts = [p for (_, p, _) in sent_prompts]
        self.assertTrue(
            any("Re-seat the engineer." in p for p in prompts),
            f"session_resume=False relaunch must fire kickoff: {prompts}",
        )

    async def test_relaunch_fresh_session_architect_fires_kickoff_sequence(self):
        """Architects share the relaunch handler; fresh-session architect
        relaunch must fire the kickoff sequence too."""
        state = self._make_state()
        architect = self._add_architect_cell(state, "arch-1", "Loomer")
        architect.status = "stopped"
        architect.agent_type = "codex"
        architect.agent_session_id = ""
        bridge = _CapturingBridge()
        sent_prompts = []

        async def fake_resolve_base_dir(group):
            del group
            return temp_dir

        def fake_resolve_engineer_launch_config(group, *, base_dir="",
                                              explicit_template="",
                                              overrides=None):
            del group, base_dir, explicit_template, overrides
            cfg = self._launch_config(temp_dir)
            cfg["worktree"] = False
            cfg["initial_prompt"] = "Architect: review the board."
            return cfg

        def fake_build_cell_persistent_prompt(cell, launch_cfg):
            del launch_cfg
            return f"You are the architect for {cell.group}."

        async def fake_send_agent_prompt(cell, prompt, **kwargs):
            sent_prompts.append((cell.id, prompt, kwargs))

        with tempfile.TemporaryDirectory() as temp_dir:
            architect.directory = temp_dir
            await self.server_mod._handle_relaunch_agent_command(
                {"id": architect.id},
                state,
                bridge=bridge,
                worktree_mgr=_FakeWorktreeManager(),
                resolve_base_dir=fake_resolve_base_dir,
                resolve_agent_launch_config=lambda *a, **k: {},
                resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
                apply_persistent_prompt=lambda *a, **k: None,
                build_cell_persistent_prompt=fake_build_cell_persistent_prompt,
                persistent_prompt_filename=lambda cell: f"{cell.id}.md",
                is_designated_engineer=lambda cell: False,
                send_agent_prompt=fake_send_agent_prompt,
            )

        prompts = [p for (_, p, _) in sent_prompts]
        self.assertTrue(
            any(f"You are the architect for {architect.group}." in p
                for p in prompts),
            f"architect persistent prompt missing: {prompts}",
        )
        self.assertTrue(
            any("Architect: review the board." in p for p in prompts),
            f"architect initial_prompt missing: {prompts}",
        )

    async def test_relaunch_terminal_does_not_fire_kickoff_sequence(self):
        """Terminals route through the same handler but are not agents and
        have no role prompts. The kickoff gate must skip them entirely."""
        state = self._make_state()
        state.add_group("loom")
        terminal = state.add_terminal(
            name="Shell",
            group="loom",
            terminal_backend="iterm2",
            profile="Default",
            command="",
            directory="/tmp/project",
            tab_color="",
        )
        terminal.status = "stopped"
        bridge = _CapturingBridge()
        sent_prompts = []

        async def fake_resolve_base_dir(group):
            del group
            return "/tmp/project"

        def fake_resolve_agent_launch_config(group, *, base_dir="",
                                            explicit_template="",
                                            overrides=None):
            del group, base_dir, explicit_template, overrides
            return self._launch_config("/tmp/project")

        async def fake_send_agent_prompt(cell, prompt, **kwargs):
            sent_prompts.append((cell.id, prompt, kwargs))

        await self.server_mod._handle_relaunch_agent_command(
            {"id": terminal.id},
            state,
            bridge=bridge,
            worktree_mgr=_FakeWorktreeManager(),
            resolve_base_dir=fake_resolve_base_dir,
            resolve_agent_launch_config=fake_resolve_agent_launch_config,
            resolve_engineer_launch_config=lambda *a, **k: {},
            apply_persistent_prompt=lambda *a, **k: None,
            build_cell_persistent_prompt=lambda *a, **k: "",
            persistent_prompt_filename=lambda cell: f"{cell.id}.md",
            is_designated_engineer=lambda cell: False,
            send_agent_prompt=fake_send_agent_prompt,
        )

        self.assertEqual(
            sent_prompts, [],
            f"terminal relaunch must not invoke send_agent_prompt: {sent_prompts}",
        )

    async def test_relaunch_after_worktree_removal_fires_kickoff_sequence(self):
        """Sibling restart path: worktree-removal relaunch always opens a
        fresh provider conversation (agent_session_id is cleared inside the
        helper). Without the kickoff threading, codex agents lose their
        persistent system prompt and any role initial_prompt is silently
        dropped — the same shape as the pre-:259 _handle_relaunch_agent_command
        bug, in a sibling code path.
        """
        state = self._make_state()
        engineer = self._add_engineer_cell(state, "eng-alice", "Alice")
        engineer.agent_type = "codex"
        engineer.session_id = "old-session"
        engineer.agent_session_id = "prior-session"
        bridge = _CapturingBridge()
        sent_prompts = []

        async def fake_resolve_base_dir(group):
            del group
            return temp_dir

        def fake_resolve_engineer_launch_config(group, *, base_dir="",
                                              explicit_template="",
                                              overrides=None):
            del group, base_dir, explicit_template, overrides
            cfg = self._launch_config(temp_dir)
            cfg["initial_prompt"] = "Engineer: get back to work."
            return cfg

        def fake_build_cell_persistent_prompt(cell, launch_cfg):
            del launch_cfg
            return f"You are the engineer for {cell.group}."

        async def fake_send_agent_prompt(cell, prompt, **kwargs):
            sent_prompts.append((cell.id, prompt, kwargs))

        with tempfile.TemporaryDirectory() as temp_dir:
            engineer.directory = temp_dir
            await self.server_mod._relaunch_agent_after_worktree_removal(
                engineer,
                bridge=bridge,
                state=state,
                resolve_base_dir=fake_resolve_base_dir,
                resolve_agent_launch_config=lambda *a, **k: {},
                resolve_engineer_launch_config=fake_resolve_engineer_launch_config,
                is_designated_engineer=lambda cell: False,
                apply_persistent_prompt=lambda *a, **k: None,
                build_cell_persistent_prompt=fake_build_cell_persistent_prompt,
                send_agent_prompt=fake_send_agent_prompt,
            )

        # Helper clears agent_session_id and the old session is closed.
        self.assertEqual(engineer.agent_session_id, "")
        self.assertEqual(bridge.closed_sessions, ["old-session"])
        self.assertEqual(len(bridge.create_session_calls), 1)

        prompts = [p for (_, p, _) in sent_prompts]
        # codex returns the persistent prompt as the first interactive turn.
        self.assertTrue(
            any(f"You are the engineer for {engineer.group}." in p
                for p in prompts),
            f"codex persistent prompt missing from sent prompts: {prompts}",
        )
        # Role-defined initial_prompt fires too.
        self.assertTrue(
            any("Engineer: get back to work." in p for p in prompts),
            f"initial_prompt missing from sent prompts: {prompts}",
        )

    async def test_relaunch_after_worktree_removal_skips_kickoff_for_terminals(self):
        """Terminals share the helper but are short-circuited at the top
        (cell_type != "agent" returns early). The kickoff must not fire and
        the bridge must not be touched."""
        state = self._make_state()
        state.add_group("loom")
        terminal = state.add_terminal(
            name="Shell",
            group="loom",
            terminal_backend="iterm2",
            profile="Default",
            command="",
            directory="/tmp/project",
            tab_color="",
        )
        terminal.session_id = "old-terminal-session"
        bridge = _CapturingBridge()
        sent_prompts = []

        async def fake_resolve_base_dir(group):
            del group
            return "/tmp/project"

        async def fake_send_agent_prompt(cell, prompt, **kwargs):
            sent_prompts.append((cell.id, prompt, kwargs))

        await self.server_mod._relaunch_agent_after_worktree_removal(
            terminal,
            bridge=bridge,
            state=state,
            resolve_base_dir=fake_resolve_base_dir,
            resolve_agent_launch_config=lambda *a, **k: {},
            resolve_engineer_launch_config=lambda *a, **k: {},
            is_designated_engineer=lambda cell: False,
            apply_persistent_prompt=lambda *a, **k: None,
            build_cell_persistent_prompt=lambda *a, **k: "",
            send_agent_prompt=fake_send_agent_prompt,
        )

        self.assertEqual(
            sent_prompts, [],
            f"terminal worktree-removal relaunch must not send prompts: {sent_prompts}",
        )
        self.assertEqual(bridge.closed_sessions, [])
        self.assertEqual(bridge.create_session_calls, [])
